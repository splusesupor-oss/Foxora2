from __future__ import annotations

import asyncio
import inspect
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


class RpcQueueFull(RuntimeError):
    pass


class RpcQueueClosed(RuntimeError):
    pass


RpcCall = Callable[[], Any | Awaitable[Any]]


@dataclass(slots=True)
class _Job:
    group_id: str
    operation: str
    call: RpcCall
    future: asyncio.Future[Any]
    priority: int
    sequence: int
    idempotency_key: str | None
    retry_safe: bool
    max_attempts: int


class SerialRpcQueue:
    """Bounded, fair, single-flight outbound RPC scheduler.

    One worker awaits each operation to finish before starting the next. Jobs
    are bounded globally and per group; each ready group gets a turn before a
    group with a long backlog receives another. This serializes calls made
    through this scheduler, not calls made directly on the underlying client.

    Safe retries are opt-in and should be used only for read-only/idempotent
    calls. Moderation actions should normally keep ``retry_safe=False`` and be
    verified before any explicit retry.
    """

    def __init__(
        self,
        *,
        max_pending_total: int = 256,
        max_pending_per_group: int = 32,
        min_interval_seconds: float = 0.0,
    ) -> None:
        if max_pending_total < 1 or max_pending_per_group < 1:
            raise ValueError("queue capacities must be positive")
        if min_interval_seconds < 0:
            raise ValueError("min_interval_seconds cannot be negative")
        self.max_pending_total = max_pending_total
        self.max_pending_per_group = max_pending_per_group
        self.min_interval_seconds = min_interval_seconds
        self._condition = asyncio.Condition()
        self._queues: dict[str, list[_Job]] = {}
        self._round_robin: deque[str] = deque()
        self._outstanding_per_group: dict[str, int] = {}
        self._outstanding = 0
        self._queued = 0
        self._sequence = 0
        self._dedupe: dict[tuple[str, str], asyncio.Future[Any]] = {}
        self._worker: asyncio.Task[None] | None = None
        self._closing = False
        self._cooldown_until = 0.0
        self._next_start_at = 0.0
        self._completed = 0
        self._failed = 0

    @property
    def pending_count(self) -> int:
        return self._queued

    @property
    def outstanding_count(self) -> int:
        return self._outstanding

    @property
    def metrics(self) -> dict[str, int]:
        return {
            "queued": self._queued,
            "outstanding": self._outstanding,
            "completed": self._completed,
            "failed": self._failed,
        }

    def _ensure_worker(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self._run(), name="foxbot-serial-rpc")

    async def submit(
        self,
        group_id: str | int,
        operation: str,
        call: RpcCall,
        *,
        priority: int = 10,
        idempotency_key: str | None = None,
        retry_safe: bool = False,
        max_attempts: int = 1,
    ) -> Any:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        self._ensure_worker()
        gid = str(group_id)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any]

        async with self._condition:
            if self._closing:
                raise RpcQueueClosed("RPC queue is closing")
            dedupe_key = (gid, idempotency_key) if idempotency_key else None
            existing = self._dedupe.get(dedupe_key) if dedupe_key else None
            if existing is not None and not existing.done():
                future = existing
            else:
                group_count = self._outstanding_per_group.get(gid, 0)
                if self._outstanding >= self.max_pending_total:
                    raise RpcQueueFull("global RPC backlog is full")
                if group_count >= self.max_pending_per_group:
                    raise RpcQueueFull(f"RPC backlog for group {gid} is full")
                future = loop.create_future()
                self._sequence += 1
                job = _Job(
                    group_id=gid,
                    operation=operation,
                    call=call,
                    future=future,
                    priority=int(priority),
                    sequence=self._sequence,
                    idempotency_key=idempotency_key,
                    retry_safe=retry_safe,
                    max_attempts=max_attempts,
                )
                bucket = self._queues.get(gid)
                if bucket is None:
                    bucket = self._queues[gid] = []
                    self._round_robin.append(gid)
                bucket.append(job)
                self._queued += 1
                self._outstanding += 1
                self._outstanding_per_group[gid] = group_count + 1
                if dedupe_key:
                    self._dedupe[dedupe_key] = future
                self._condition.notify()

        # Shielding means cancelling a command handler cannot cancel a queued
        # moderation RPC midway; the job remains observable and serialized.
        return await asyncio.shield(future)

    async def _next_job(self) -> _Job | None:
        async with self._condition:
            while not self._round_robin:
                if self._closing and self._outstanding == 0:
                    return None
                await self._condition.wait()
            gid = self._round_robin.popleft()
            bucket = self._queues[gid]
            index = min(range(len(bucket)), key=lambda i: (bucket[i].priority, bucket[i].sequence))
            job = bucket.pop(index)
            self._queued -= 1
            if bucket:
                self._round_robin.append(gid)
            else:
                self._queues.pop(gid, None)
            return job

    @staticmethod
    def _flood_wait_seconds(error: BaseException) -> float | None:
        value = getattr(error, "seconds", None)
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
        name = type(error).__name__.lower()
        if "flood" not in name and "wait" not in name:
            return None
        match = re.search(r"(\d+(?:\.\d+)?)", str(error))
        return float(match.group(1)) if match else None

    async def _wait_until_allowed(self) -> None:
        now = time.monotonic()
        wait_for = max(self._cooldown_until, self._next_start_at) - now
        if wait_for > 0:
            await asyncio.sleep(wait_for)
        self._next_start_at = time.monotonic() + self.min_interval_seconds

    async def _execute(self, job: _Job) -> None:
        attempt = 0
        while True:
            await self._wait_until_allowed()
            try:
                result = job.call()
                if inspect.isawaitable(result):
                    result = await result
                if not job.future.done():
                    job.future.set_result(result)
                self._completed += 1
                return
            except BaseException as error:
                if isinstance(error, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
                    if not job.future.done():
                        job.future.set_exception(error)
                    raise
                flood_wait = self._flood_wait_seconds(error)
                if flood_wait is not None:
                    self._cooldown_until = max(
                        self._cooldown_until,
                        time.monotonic() + flood_wait,
                    )
                retryable_transport_error = isinstance(error, (TimeoutError, ConnectionError))
                can_retry = (
                    job.retry_safe
                    and attempt + 1 < job.max_attempts
                    and (flood_wait is not None or retryable_transport_error)
                )
                if can_retry:
                    attempt += 1
                    if flood_wait is not None:
                        await asyncio.sleep(flood_wait)
                    continue
                if not job.future.done():
                    job.future.set_exception(error)
                self._failed += 1
                return

    async def _finish(self, job: _Job) -> None:
        async with self._condition:
            self._outstanding -= 1
            count = self._outstanding_per_group.get(job.group_id, 0) - 1
            if count > 0:
                self._outstanding_per_group[job.group_id] = count
            else:
                self._outstanding_per_group.pop(job.group_id, None)
            if job.idempotency_key:
                key = (job.group_id, job.idempotency_key)
                if self._dedupe.get(key) is job.future:
                    self._dedupe.pop(key, None)
            self._condition.notify_all()

    async def _run(self) -> None:
        while True:
            job = await self._next_job()
            if job is None:
                return
            try:
                if not job.future.cancelled():
                    await self._execute(job)
            finally:
                await self._finish(job)

    async def close(self, *, drain: bool = True) -> None:
        async with self._condition:
            self._closing = True
            if not drain:
                queued_jobs = [job for bucket in self._queues.values() for job in bucket]
                self._queues.clear()
                self._round_robin.clear()
                self._queued = 0
                for job in queued_jobs:
                    if not job.future.done():
                        job.future.set_exception(RpcQueueClosed("RPC queue closed before execution"))
                    self._outstanding -= 1
                    remaining = self._outstanding_per_group.get(job.group_id, 0) - 1
                    if remaining > 0:
                        self._outstanding_per_group[job.group_id] = remaining
                    else:
                        self._outstanding_per_group.pop(job.group_id, None)
                    if job.idempotency_key:
                        self._dedupe.pop((job.group_id, job.idempotency_key), None)
            self._condition.notify_all()
        if self._worker is not None:
            await self._worker
