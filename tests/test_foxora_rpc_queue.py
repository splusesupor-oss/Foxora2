from __future__ import annotations

import asyncio

import pytest

from modules.rpc_queue import RpcQueueFull, SerialRpcQueue


def test_queue_executes_only_one_rpc_at_a_time_and_drains() -> None:
    async def scenario() -> None:
        queue = SerialRpcQueue(max_pending_total=20, max_pending_per_group=10)
        active = 0
        maximum = 0

        async def call(value: int) -> int:
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.005)
            active -= 1
            return value

        tasks = [
            asyncio.create_task(queue.submit(str(index % 3), "test", lambda i=index: call(i)))
            for index in range(12)
        ]
        assert await asyncio.gather(*tasks) == list(range(12))
        assert maximum == 1
        assert queue.metrics["completed"] == 12
        await queue.close()

    asyncio.run(scenario())


def test_queue_is_bounded_globally_and_per_group() -> None:
    async def scenario() -> None:
        queue = SerialRpcQueue(max_pending_total=2, max_pending_per_group=1)
        started = asyncio.Event()
        release = asyncio.Event()

        async def blocked() -> str:
            started.set()
            await release.wait()
            return "done"

        first = asyncio.create_task(queue.submit("g1", "blocked", blocked))
        await started.wait()
        second = asyncio.create_task(queue.submit("g2", "queued", lambda: "second"))
        await asyncio.sleep(0)
        with pytest.raises(RpcQueueFull):
            await queue.submit("g1", "same-group-overflow", lambda: "never")
        with pytest.raises(RpcQueueFull):
            await queue.submit("g3", "global-overflow", lambda: "never")
        release.set()
        assert await first == "done"
        assert await second == "second"
        await queue.close()

    asyncio.run(scenario())


def test_deduplication_shares_only_an_in_flight_job() -> None:
    async def scenario() -> None:
        queue = SerialRpcQueue()
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def read() -> int:
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return 42

        first = asyncio.create_task(
            queue.submit("g", "read", read, idempotency_key="same")
        )
        await started.wait()
        second = asyncio.create_task(
            queue.submit("g", "duplicate", read, idempotency_key="same")
        )
        await asyncio.sleep(0)
        release.set()
        assert await asyncio.gather(first, second) == [42, 42]
        assert calls == 1
        assert await queue.submit("g", "read-again", read, idempotency_key="same") == 42
        assert calls == 2
        await queue.close()

    asyncio.run(scenario())


def test_retry_is_opt_in_and_restricted_to_safe_reads() -> None:
    class FloodWaitError(Exception):
        seconds = 0.001

    async def scenario() -> None:
        queue = SerialRpcQueue()
        attempts = 0

        async def safe_read() -> str:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise FloodWaitError("wait")
            return "ok"

        assert await queue.submit("g", "read", safe_read, retry_safe=True, max_attempts=2) == "ok"
        assert attempts == 2
        attempts = 0
        with pytest.raises(FloodWaitError):
            await queue.submit("g", "moderation", safe_read, retry_safe=False, max_attempts=2)
        assert attempts == 1
        await queue.close()

    asyncio.run(scenario())
