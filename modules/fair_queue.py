"""Bounded per-group action workflows feeding the process-wide RPC layer.

Ported from the previous Foxora codebase (``src/foxbot/fair_queue.py``) so the
preserved group-request queue capability keeps working on top of the real B2
bot code:

* every group's queued requests are processed strictly per design: each job
  is executed by its own group worker and stays an independent logical
  operation — jobs are never merged into fewer operations,
* pending work is bounded globally and per group (plus a cap on how many
  distinct groups can hold pending work), so memory/CPU stay flat under
  load,
* repeated failure logging is rate-limited per group (one warning per 30s)
  and the failure map itself is bounded, so logging cannot flood CPU or
  grow unbounded,
* group failures are isolated: a failed action in one group never stops the
  other groups' workers.

``modules.moderation_queue.ModerationQueue`` remains the live per-chat
moderation path inherited from B2; this fair scheduler is the preserved
Foxora component for multi-group background request workflows.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

Action = Callable[[], Awaitable[Any]]


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


@dataclass(slots=True)
class _ActionJob:
    group_id: str
    operation: Action
    future: asyncio.Future[Any]


class FairModerationQueue:
    """Bounded per-group action workflows feeding the process-wide RPC queue."""

    def __init__(
        self,
        *,
        max_pending_total: int = 256,
        max_pending_per_group: int = 16,
        max_groups: int = 128,
    ) -> None:
        if min(max_pending_total, max_pending_per_group, max_groups) < 1:
            raise ValueError("moderation queue bounds must be positive")
        self.max_pending_total = max_pending_total
        self.max_pending_per_group = max_pending_per_group
        self.max_groups = max_groups
        self._queues: OrderedDict[str, deque[_ActionJob]] = OrderedDict()
        self._workers: dict[str, asyncio.Task[None]] = {}
        self._group_pending: dict[str, int] = {}
        self._total_pending = 0
        self._closing = False
        self._failure_log: OrderedDict[str, float] = OrderedDict()
        self._logger = logging.getLogger(__name__)

    @classmethod
    def from_env(cls) -> "FairModerationQueue":
        """Build with the preserved Foxora runtime bounds (same env family as
        :class:`modules.rpc_queue.SerialRpcQueue`)."""
        return cls(
            max_pending_total=_env_int(
                "RPC_MAX_PENDING_TOTAL", 256, minimum=1, maximum=4096
            ),
            max_pending_per_group=_env_int(
                "FAIR_MAX_PENDING_PER_GROUP", 16, minimum=1, maximum=256
            ),
            max_groups=_env_int("FAIR_MAX_GROUPS", 128, minimum=1, maximum=4096),
        )

    @property
    def pending_count(self) -> int:
        return self._total_pending

    async def submit(self, group_id: str | int, operation: Action) -> tuple[bool, Any]:
        group = str(group_id)
        if self._closing or not group:
            return False, None
        queue = self._queues.get(group)
        if queue is None:
            if len(self._queues) >= self.max_groups:
                self._log_failure(group, "group_capacity")
                return False, None
            queue = deque()
            self._queues[group] = queue
        if (
            self._total_pending >= self.max_pending_total
            or self._group_pending.get(group, 0) >= self.max_pending_per_group
        ):
            self._log_failure(group, "queue_capacity")
            if not queue and group not in self._workers:
                self._queues.pop(group, None)
            return False, None
        future = asyncio.get_running_loop().create_future()
        future.add_done_callback(self._consume_exception)
        queue.append(_ActionJob(group, operation, future))
        self._group_pending[group] = self._group_pending.get(group, 0) + 1
        self._total_pending += 1
        self._ensure_worker(group)
        return True, await asyncio.shield(future)

    async def close(self, *, drain: bool = True) -> None:
        self._closing = True
        tasks = tuple(self._workers.values())
        if not drain:
            for task in tasks:
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if not drain:
            for group_id, queue in tuple(self._queues.items()):
                for job in tuple(queue):
                    self._finish(job, None)
                    self._decrement_pending(group_id)
                queue.clear()
        self._queues.clear()

    def _ensure_worker(self, group_id: str) -> None:
        task = self._workers.get(group_id)
        if task is None or task.done():
            self._workers[group_id] = asyncio.create_task(
                self._run_group(group_id), name=f"foxora:moderation-queue:{group_id}"
            )

    async def _run_group(self, group_id: str) -> None:
        try:
            while True:
                queue = self._queues.get(group_id)
                if not queue:
                    return
                job = queue.popleft()
                try:
                    result = await job.operation()
                except asyncio.CancelledError:
                    self._finish(job, None)
                    raise
                except Exception as exc:  # noqa: BLE001 - a failed group action does not stop other groups
                    self._log_failure(group_id, type(exc).__name__)
                    if not job.future.done():
                        job.future.set_exception(exc)
                else:
                    self._finish(job, result)
                finally:
                    self._decrement_pending(group_id)
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

    def _finish(self, job: _ActionJob, result: Any) -> None:
        if not job.future.done():
            job.future.set_result(result)

    def _decrement_pending(self, group_id: str) -> None:
        self._total_pending = max(0, self._total_pending - 1)
        group_pending = self._group_pending.get(group_id, 0) - 1
        if group_pending > 0:
            self._group_pending[group_id] = group_pending
        else:
            self._group_pending.pop(group_id, None)

    @staticmethod
    def _consume_exception(future: asyncio.Future[Any]) -> None:
        if not future.cancelled():
            future.exception()

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
            "queued automatic moderation action did not complete group=%s reason=%s",
            group_id,
            code,
        )
