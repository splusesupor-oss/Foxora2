"""Bounded per-group deletion queue (preserved Foxora capability).

Ported from the previous Foxora codebase (``src/foxbot/message_actions.py``,
class ``FairDeleteQueue``) so delete request queueing keeps the preserved
Foxora semantics on top of the real B2 bot code:

* every queued delete stays an independent logical operation for its group;
  only the *same* (group, message) pair is deduplicated while still pending —
  different deletions are never merged into one,
* pending work is bounded globally (``max_pending_total``), per group
  (``max_pending_per_group``) and in the number of distinct groups
  (``max_groups``), so pending state cannot grow without limit,
* per-group workers process deletions in order with bounded retries,
* repeated failure logging is rate-limited per group (one log per 30s) and
  the failure map is bounded, so logging costs stay flat.

``modules.delete_queue``/``modules.message_delete_queue`` remain the live B2
delete paths; this scheduler is the preserved Foxora component and is
covered by ``tests/test_delete_queue_capacity.py``.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any

DeleteMessage = Callable[[str, str], Awaitable[bool]]
DeleteCallback = Callable[[str, str], Any | Awaitable[Any]]


@dataclass(slots=True)
class _DeleteJob:
    group_id: str
    message_id: str
    future: asyncio.Future[bool]
    max_attempts: int
    attempts: int = 0
    on_success: DeleteCallback | None = None


class FairDeleteQueue:
    """Bounded per-group deletion workers; the platform RPC queue remains authoritative."""

    def __init__(
        self,
        delete_message: DeleteMessage,
        *,
        max_pending_total: int = 5000,
        max_pending_per_group: int = 500,
        max_groups: int = 512,
    ) -> None:
        if min(max_pending_total, max_pending_per_group, max_groups) < 1:
            raise ValueError("delete queue bounds must be positive")
        self._delete_message = delete_message
        self.max_pending_total = max_pending_total
        self.max_pending_per_group = max_pending_per_group
        self.max_groups = max_groups
        self._queues: OrderedDict[str, deque[_DeleteJob]] = OrderedDict()
        self._workers: dict[str, asyncio.Task[None]] = {}
        self._pending: dict[tuple[str, str], _DeleteJob] = {}
        self._group_pending: dict[str, int] = {}
        self._total_pending = 0
        self._closing = False
        self._failure_log: OrderedDict[str, float] = OrderedDict()
        self._logger = logging.getLogger(__name__)

    @property
    def pending_count(self) -> int:
        return self._total_pending

    async def delete_one(self, group_id: str, message_id: str) -> bool:
        future = self._enqueue_one(
            str(group_id), str(message_id), max_attempts=3, on_success=None
        )
        if future is None:
            return False
        return await asyncio.shield(future)

    def enqueue(
        self,
        group_id: str | int,
        message_ids: Iterable[str | int],
        *,
        max_attempts: int = 3,
        on_success: DeleteCallback | None = None,
    ) -> int:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        group = str(group_id)
        accepted = 0
        for message_id in message_ids:
            mid = str(message_id)
            if not mid or mid == "0":
                continue
            future = self._enqueue_one(
                group, mid, max_attempts=max_attempts, on_success=on_success
            )
            if future is not None:
                accepted += 1
        return accepted

    async def drain(self) -> None:
        """Wait until the currently queued deletions finish, without closing the queue."""
        while self._workers:
            await asyncio.gather(*tuple(self._workers.values()), return_exceptions=True)

    async def close(self, *, drain: bool = True) -> None:
        self._closing = True
        tasks = tuple(self._workers.values())
        if not drain:
            for task in tasks:
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if not drain:
            for job in tuple(self._pending.values()):
                self._finish(job, False)
        self._queues.clear()

    def _enqueue_one(
        self,
        group_id: str,
        message_id: str,
        *,
        max_attempts: int,
        on_success: DeleteCallback | None,
    ) -> asyncio.Future[bool] | None:
        if self._closing or not group_id or not message_id:
            return None
        key = (group_id, message_id)
        existing = self._pending.get(key)
        if existing is not None:
            if existing.on_success is None and on_success is not None:
                existing.on_success = on_success
            return existing.future
        group_queue = self._queues.get(group_id)
        if group_queue is None:
            if len(self._queues) >= self.max_groups:
                self._log_failure(group_id, "group_capacity")
                return None
            group_queue = deque()
            self._queues[group_id] = group_queue
        if (
            self._total_pending >= self.max_pending_total
            or self._group_pending.get(group_id, 0) >= self.max_pending_per_group
        ):
            self._log_failure(group_id, "queue_capacity")
            if not group_queue and group_id not in self._workers:
                self._queues.pop(group_id, None)
            return None
        future = asyncio.get_running_loop().create_future()
        job = _DeleteJob(
            group_id=group_id,
            message_id=message_id,
            future=future,
            max_attempts=max_attempts,
            on_success=on_success,
        )
        group_queue.append(job)
        self._pending[key] = job
        self._group_pending[group_id] = self._group_pending.get(group_id, 0) + 1
        self._total_pending += 1
        self._ensure_worker(group_id)
        return future

    def _ensure_worker(self, group_id: str) -> None:
        task = self._workers.get(group_id)
        if task is None or task.done():
            self._workers[group_id] = asyncio.create_task(
                self._run_group(group_id), name=f"foxbot:delete:{group_id}"
            )

    async def _run_group(self, group_id: str) -> None:
        try:
            while True:
                queue = self._queues.get(group_id)
                if not queue:
                    return
                job = queue.popleft()
                try:
                    deleted = bool(
                        await self._delete_message(group_id, job.message_id)
                    )
                except asyncio.CancelledError:
                    self._finish(job, False)
                    raise
                except Exception as exc:  # noqa: BLE001 - keep other groups draining
                    deleted = False
                    self._log_failure(group_id, type(exc).__name__)
                if not deleted and job.attempts + 1 < job.max_attempts and not self._closing:
                    job.attempts += 1
                    await asyncio.sleep(min(0.05 * (2 ** (job.attempts - 1)), 0.5))
                    queue = self._queues.get(group_id)
                    if queue is not None:
                        queue.appendleft(job)
                    continue
                if not deleted:
                    self._log_failure(group_id, "delete_not_confirmed")
                if deleted and job.on_success is not None:
                    try:
                        callback_result = job.on_success(group_id, job.message_id)
                        if inspect.isawaitable(callback_result):
                            await callback_result
                    except Exception as exc:  # noqa: BLE001 - a metric callback cannot stop deletion
                        self._log_failure(group_id, f"callback_{type(exc).__name__}")
                self._finish(job, deleted)
        finally:
            current = asyncio.current_task()
            if self._workers.get(group_id) is current:
                self._workers.pop(group_id, None)
            queue = self._queues.get(group_id)
            if queue:
                if not self._closing:
                    self._ensure_worker(group_id)
            else:
                self._queues.pop(group_id, None)

    def _finish(self, job: _DeleteJob, succeeded: bool) -> None:
        key = (job.group_id, job.message_id)
        if self._pending.pop(key, None) is not None:
            self._total_pending = max(0, self._total_pending - 1)
            group_pending = self._group_pending.get(job.group_id, 0) - 1
            if group_pending > 0:
                self._group_pending[job.group_id] = group_pending
            else:
                self._group_pending.pop(job.group_id, None)
        if not job.future.done():
            job.future.set_result(bool(succeeded))

    def _log_failure(self, group_id: str, code: str) -> None:
        now = time.monotonic()
        previous = self._failure_log.get(group_id)
        if previous is not None and now - previous < 30.0:
            return
        self._failure_log[group_id] = now
        self._failure_log.move_to_end(group_id)
        while len(self._failure_log) > 512:
            self._failure_log.popitem(last=False)
        self._logger.warning(
            "queued message deletion did not complete group=%s reason=%s",
            group_id,
            code,
        )
