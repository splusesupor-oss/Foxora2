from __future__ import annotations

import asyncio

from modules.fair_delete_queue import FairDeleteQueue


def test_delete_queue_capacity_counts_active_and_queued_jobs_per_group() -> None:
    async def scenario() -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        deleted: list[tuple[str, str]] = []

        async def delete(group_id: str, message_id: str) -> bool:
            deleted.append((group_id, message_id))
            if message_id == "active":
                started.set()
                await release.wait()
            return True

        queue = FairDeleteQueue(
            delete,
            max_pending_total=2,
            max_pending_per_group=1,
            max_groups=2,
        )
        assert queue.enqueue("g1", ["active"]) == 1
        await started.wait()
        assert queue.pending_count == 1
        assert queue.enqueue("g1", ["overflow"]) == 0
        assert queue.enqueue("g2", ["other"]) == 1
        assert queue.enqueue("g3", ["too-many-groups"]) == 0
        release.set()
        await queue.drain()
        assert set(deleted) == {("g1", "active"), ("g2", "other")}
        assert queue.pending_count == 0
        await queue.close()

    asyncio.run(scenario())
