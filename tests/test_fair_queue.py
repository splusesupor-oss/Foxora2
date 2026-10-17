from __future__ import annotations

import asyncio

from foxbot.fair_queue import FairModerationQueue


def test_moderation_workflows_are_fifo_per_group_and_bounded() -> None:
    async def scenario() -> None:
        queue = FairModerationQueue(
            max_pending_total=4,
            max_pending_per_group=2,
            max_groups=2,
        )
        started = asyncio.Event()
        release = asyncio.Event()
        order: list[str] = []
        active_by_group: dict[str, int] = {}
        maximum_by_group: dict[str, int] = {}

        async def operation(group_id: str, name: str, *, wait: bool = False) -> str:
            active_by_group[group_id] = active_by_group.get(group_id, 0) + 1
            maximum_by_group[group_id] = max(
                maximum_by_group.get(group_id, 0), active_by_group[group_id]
            )
            order.append(name)
            if wait:
                started.set()
                await release.wait()
            active_by_group[group_id] -= 1
            return name

        first = asyncio.create_task(
            queue.submit("g1", lambda: operation("g1", "g1-first", wait=True))
        )
        await started.wait()
        second = asyncio.create_task(
            queue.submit("g1", lambda: operation("g1", "g1-second"))
        )
        third = asyncio.create_task(
            queue.submit("g2", lambda: operation("g2", "g2-first"))
        )
        await asyncio.sleep(0)

        accepted, result = await queue.submit(
            "g1", lambda: operation("g1", "must-not-run")
        )
        assert accepted is False and result is None
        assert await third == (True, "g2-first")
        release.set()
        assert await first == (True, "g1-first")
        assert await second == (True, "g1-second")
        assert order.index("g1-first") < order.index("g1-second")
        assert maximum_by_group["g1"] == 1
        assert queue.pending_count == 0
        await queue.close()

    asyncio.run(scenario())


def test_moderation_queue_drain_and_abort_leave_no_pending_jobs() -> None:
    async def scenario() -> None:
        queue = FairModerationQueue(max_pending_total=4, max_pending_per_group=4)
        started = asyncio.Event()
        release = asyncio.Event()

        async def action() -> str:
            started.set()
            await release.wait()
            return "done"

        first = asyncio.create_task(queue.submit("g", action))
        await started.wait()
        second = asyncio.create_task(queue.submit("g", action))
        await asyncio.sleep(0)
        assert queue.pending_count == 2
        close_task = asyncio.create_task(queue.close(drain=True))
        await asyncio.sleep(0)
        assert not close_task.done()
        release.set()
        assert await first == (True, "done")
        assert await second == (True, "done")
        await close_task
        assert queue.pending_count == 0

        cancelled_queue = FairModerationQueue()
        started.clear()
        abort_release = asyncio.Event()

        async def abortable_action() -> str:
            started.set()
            await abort_release.wait()
            return "done"

        blocked = asyncio.create_task(cancelled_queue.submit("g", abortable_action))
        await started.wait()
        queued = asyncio.create_task(cancelled_queue.submit("g", abortable_action))
        await asyncio.sleep(0)
        await cancelled_queue.close(drain=False)
        assert await blocked == (True, None)
        assert await queued == (True, None)
        assert cancelled_queue.pending_count == 0

    asyncio.run(scenario())
