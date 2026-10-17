"""Tests for Step 1: Long-term speed stability, worker lifecycles, and per-group pause isolation."""
import asyncio
import time
from unittest.mock import MagicMock

from modules.message_delete_queue import MessageDeleteQueue
from modules.group_dispatch import GroupDispatcher


def test_message_delete_queue_per_chat_pause_isolation():
    """Verify that a delete failure in Chat 100 pauses only Chat 100 and does NOT pause Chat 200."""
    async def scenario():
        client = MagicMock()
        logger = MagicMock()

        # Chat 100 fails with an Exception (simulating rate limit / permission failure)
        # Chat 200 succeeds
        async def mock_delete_messages(chat_id, ids):
            if str(chat_id) in {"100", "-100100"}:
                raise RuntimeError("RPC error simulated for chat 100")
            return True

        client.delete_messages = mock_delete_messages

        queue = MessageDeleteQueue(client, logger, micro_buffer_seconds=0.0)

        # 1. Enqueue delete in Chat 100 (which will fail and trigger pause on chat 100)
        fut1 = queue.enqueue(100, [1, 2], priority=1)
        deleted1, remaining1 = await fut1
        assert deleted1 == 0
        assert remaining1 == [1, 2]

        # Verify chat 100 is paused
        assert 100 in [int(k) for k in queue._automatic_pause_until.keys()]
        assert queue._automatic_pause_until.get("100", 0) > time.monotonic()

        # 2. Subsequent automatic delete in Chat 100 is short-circuited by pause
        fut1_retry = queue.enqueue(100, [3], priority=1)
        d_paused, r_paused = await fut1_retry
        assert d_paused == 0
        assert r_paused == [3]

        # 3. CRITICAL TEST: Chat 200 is NOT affected by Chat 100's pause!
        fut2 = queue.enqueue(200, [10, 20], priority=1)
        deleted2, remaining2 = await fut2
        assert deleted2 == 2
        assert remaining2 == []
        assert "200" not in queue._automatic_pause_until

        await queue.close()

    asyncio.run(scenario())


def test_message_delete_queue_cleanup_expired():
    """Verify cleanup_expired removes old cooldown timestamps."""
    client = MagicMock()
    logger = MagicMock()
    queue = MessageDeleteQueue(client, logger)

    now = time.monotonic()
    queue._automatic_pause_until["100"] = now - 10.0  # expired
    queue._automatic_pause_until["200"] = now + 50.0  # active

    queue.cleanup_expired(now)

    assert "100" not in queue._automatic_pause_until
    assert "200" in queue._automatic_pause_until


def test_message_delete_queue_worker_respawns_on_late_items():
    """Verify that if items arrive right before or during worker exit, worker respawns and processes them."""
    async def scenario():
        client = MagicMock()
        logger = MagicMock()

        processed_ids = []

        async def mock_delete_messages(chat_id, ids):
            processed_ids.extend(ids)
            return True

        client.delete_messages = mock_delete_messages
        queue = MessageDeleteQueue(client, logger, micro_buffer_seconds=0.0)

        # Enqueue first batch
        fut1 = queue.enqueue(100, [1, 2], priority=1)
        await fut1

        assert processed_ids == [1, 2]

        # Enqueue a second batch after first completed
        fut2 = queue.enqueue(100, [3, 4], priority=1)
        await fut2

        assert processed_ids == [1, 2, 3, 4]
        await queue.close()

    asyncio.run(scenario())


def test_group_dispatcher_worker_respawns_on_late_items():
    """Verify GroupDispatcher worker respawns cleanly if new items arrive when previous worker is in finally block."""
    async def scenario():
        logger = MagicMock()
        dispatcher = GroupDispatcher(logger=logger, max_pending_normal=20, normal_concurrency=1)

        executed = []

        def make_job(val):
            async def job():
                executed.append(val)
            return job

        # Submit first job
        dispatcher.submit(1, make_job("A"))
        await asyncio.sleep(0.05)
        assert "A" in executed

        # Submit second job after first worker finishes
        dispatcher.submit(1, make_job("B"))
        await asyncio.sleep(0.05)
        assert "B" in executed

        await dispatcher.close()

    asyncio.run(scenario())
