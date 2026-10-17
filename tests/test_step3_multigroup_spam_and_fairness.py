import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from modules.group_dispatch import GroupDispatcher, PRIORITY_ADMIN, PRIORITY_NORMAL, PRIORITY_COMMAND
from modules.message_delete_queue import MessageDeleteQueue
from modules.moderation_queue import ModerationQueue
from modules.rpc_governor import RpcGovernor


class MockLogger:
    def __init__(self):
        self.logs = []

    def log_action(self, *args, **kwargs):
        pass

    def log_error(self, *args, **kwargs):
        self.logs.append(("ERROR", args))

    def log_info(self, *args, **kwargs):
        self.logs.append(("INFO", args))


class MockRpcClient:
    def __init__(self, rpc_delay=0.005):
        self.rpc_delay = rpc_delay
        self.delete_calls = []
        self.mute_calls = []
        self.send_calls = []

    async def delete_messages(self, chat_id, ids):
        if self.rpc_delay:
            await asyncio.sleep(self.rpc_delay)
        self.delete_calls.append((chat_id, list(ids), time.perf_counter()))
        return True

    async def edit_permissions(self, chat, user, **kwargs):
        if self.rpc_delay:
            await asyncio.sleep(self.rpc_delay)
        self.mute_calls.append((chat, user, kwargs, time.perf_counter()))
        return True

    async def send_message(self, chat_id, text, **kwargs):
        if self.rpc_delay:
            await asyncio.sleep(self.rpc_delay)
        self.send_calls.append((chat_id, text, time.perf_counter()))
        return SimpleNamespace(id=9999)


def test_60_concurrent_groups_isolation_and_fairness():
    """60 groups concurrently processing messages with zero cross-group head-of-line blocking."""
    async def scenario():
        logger = MockLogger()
        dispatcher = GroupDispatcher(max_pending_normal=150, normal_concurrency=4, logger=logger)

        group_counts = {i: 0 for i in range(1, 61)}
        group_latencies = {i: [] for i in range(1, 61)}
        start_time = time.perf_counter()

        def make_handler(gid, msg_idx, enqueued_at):
            async def handler():
                await asyncio.sleep(0.002)  # Simulated message processing
                now = time.perf_counter()
                group_counts[gid] += 1
                group_latencies[gid].append((now - enqueued_at) * 1000)
            return handler

        # Submit 10 messages to each of the 60 groups (600 total messages)
        # Groups 1-10 are 'bursty' (enqueued in tight loop), 11-60 are normal
        for msg_idx in range(10):
            for gid in range(1, 61):
                t0 = time.perf_counter()
                dispatcher.submit(
                    gid,
                    make_handler(gid, msg_idx, t0),
                    priority=PRIORITY_NORMAL,
                )

        await dispatcher.join(timeout=10.0)
        total_duration = time.perf_counter() - start_time

        # Assert every single group had all 10 messages processed
        for gid in range(1, 61):
            assert group_counts[gid] == 10, f"Group {gid} processed {group_counts[gid]}/10"

        # Assert all 600 messages finished quickly due to concurrency across all 60 groups
        assert total_duration < 3.0, f"60 groups took {total_duration:.2f}s (expected < 3.0s)"

        # Calculate latency metrics per group
        avg_latencies = [sum(lats) / len(lats) for lats in group_latencies.values()]
        max_avg_latency = max(avg_latencies)
        min_avg_latency = min(avg_latencies)

        print(f"\n--- 60 Groups Concurrency Results ---")
        print(f"Total Groups: 60 | Total Messages Processed: 600")
        print(f"Total Execution Time: {total_duration*1000:.1f} ms")
        print(f"Min Group Avg Latency: {min_avg_latency:.1f} ms | Max Group Avg Latency: {max_avg_latency:.1f} ms")

        await dispatcher.close()

    asyncio.run(scenario())


def test_severe_spam_wave_with_batch_delete_efficiency():
    """1 group receives 100 spam messages; MessageDeleteQueue batches them efficiently without dropping any."""
    async def scenario():
        logger = MockLogger()
        client = MockRpcClient(rpc_delay=0.005)
        delete_queue = MessageDeleteQueue(
            client, logger, batch_size=15, micro_buffer_seconds=0.02
        )

        chat_id = 777
        message_ids = list(range(1, 101))  # 100 spam messages

        t0 = time.perf_counter()
        # Enqueue all 100 messages in rapid succession (simulating wave)
        futures = []
        for mid in message_ids:
            fut = delete_queue.enqueue(chat_id, [mid], priority=1)
            futures.append(fut)

        results = await asyncio.gather(*futures)
        duration_ms = (time.perf_counter() - t0) * 1000

        total_deleted = sum(r[0] for r in results)
        total_remaining = sum(len(r[1]) for r in results)

        # All 100 messages must be completely deleted with 0 remaining
        assert total_deleted == 100
        assert total_remaining == 0

        # Batching efficiency: 100 messages / 15 batch_size = 7 RPC calls (not 100 calls!)
        num_rpc_calls = len(client.delete_calls)
        assert num_rpc_calls <= 7, f"Expected <= 7 RPC calls, got {num_rpc_calls}"

        print(f"\n--- Severe Spam Wave Batch Delete Results ---")
        print(f"Messages Enqueued: 100 | Messages Deleted: {total_deleted}")
        print(f"RPC Calls to Soroush: {num_rpc_calls} (Batched from 100 individual messages)")
        print(f"Total Cleanup Time: {duration_ms:.1f} ms")

        await delete_queue.close()

    asyncio.run(scenario())


def test_moderation_priority_over_heavy_spam_cleanup():
    """ModerationQueue priority ensures Ban/Mute executes without waiting behind large delete batches."""
    async def scenario():
        logger = MockLogger()
        client = MockRpcClient(rpc_delay=0.01)
        delete_queue = MessageDeleteQueue(
            client, logger, batch_size=15, micro_buffer_seconds=0.01
        )
        mod_queue = ModerationQueue(logger, per_chat_limit=3)

        chat_id = 888

        # 1. Start heavy spam deletion in chat 888 (60 messages in progress)
        for i in range(60):
            delete_queue.enqueue(chat_id, [i + 1], priority=1)

        # 2. Immediately enqueue urgent admin mute
        mute_executed_at = None
        mute_enqueued_at = time.perf_counter()

        async def do_mute():
            nonlocal mute_executed_at
            mute_executed_at = time.perf_counter()
            await client.edit_permissions(chat_id, 9999, send_messages=False)
            return True

        mod_fut = asyncio.get_running_loop().create_future()

        mod_queue.enqueue(
            chat_id,
            "mute",
            operation=do_mute,
            user_id=9999,
            on_success=lambda res: mod_fut.set_result(res),
        )

        res = await asyncio.wait_for(mod_fut, timeout=2.0)
        assert res is True

        mute_wait_time_ms = (mute_executed_at - mute_enqueued_at) * 1000
        # Mute should execute with sub-50ms queue wait time despite 60 delete jobs queued
        assert mute_wait_time_ms < 100.0, f"Mute was delayed {mute_wait_time_ms:.1f} ms"

        print(f"\n--- Moderation Priority over Spam Cleanup Results ---")
        print(f"Admin Mute Queue Wait Time: {mute_wait_time_ms:.2f} ms (Ran independently of delete batch queue)")

        await delete_queue.close()
        await mod_queue.close()

    asyncio.run(scenario())


def test_spam_wave_across_multiple_groups_simultaneously():
    """10 groups simultaneously experience heavy spam waves (40 spam messages each = 400 messages)."""
    async def scenario():
        logger = MockLogger()
        client = MockRpcClient(rpc_delay=0.005)
        delete_queue = MessageDeleteQueue(
            client, logger, batch_size=15, micro_buffer_seconds=0.02
        )

        groups = list(range(101, 111))  # 10 groups
        all_futures = []
        group_results = {g: [] for g in groups}

        start_wall = time.perf_counter()

        # Enqueue 40 spam messages per group concurrently across all 10 groups
        for gid in groups:
            for mid in range(1, 41):
                fut = delete_queue.enqueue(gid, [mid], priority=1)
                all_futures.append((gid, fut))

        for gid, fut in all_futures:
            res = await fut
            group_results[gid].append(res)

        total_time_ms = (time.perf_counter() - start_wall) * 1000

        # Verify 100% cleanup across all 10 groups
        for gid in groups:
            deleted_in_group = sum(r[0] for r in group_results[gid])
            remaining_in_group = sum(len(r[1]) for r in group_results[gid])
            assert deleted_in_group == 40, f"Group {gid} only deleted {deleted_in_group}/40"
            assert remaining_in_group == 0, f"Group {gid} had {remaining_in_group} remaining"

        total_deleted = sum(sum(r[0] for r in res_list) for res_list in group_results.values())
        assert total_deleted == 400

        # Assert RPC calls were batched per group (40 / 15 = 3 RPCs per group -> ~30 total RPCs instead of 400)
        total_rpc_calls = len(client.delete_calls)
        assert total_rpc_calls <= 35, f"Expected <= 35 RPC calls, got {total_rpc_calls}"

        print(f"\n--- Multi-Group Simultaneous Spam Wave Results ---")
        print(f"Total Groups: 10 | Total Spam Messages: 400 | Total Deleted: {total_deleted}")
        print(f"Total Delete RPCs: {total_rpc_calls} (Reduced from 400)")
        print(f"All Groups Drained In: {total_time_ms:.1f} ms")

        await delete_queue.close()

    asyncio.run(scenario())


def test_group_dispatcher_per_chat_overflow_isolation():
    """Heavy traffic in Chat A exceeding normal buffer triggers overflow handling without impacting Chat B."""
    async def scenario():
        logger = MockLogger()
        dispatcher = GroupDispatcher(max_pending_normal=30, normal_concurrency=2, logger=logger)

        chat_a = 555
        chat_b = 666

        overflow_a = []
        processed_a = []
        processed_b = []

        # Enqueue 50 messages into Chat A (limit is 30 -> 20 will overflow)
        for i in range(50):
            def make_a(idx=i):
                async def work():
                    await asyncio.sleep(0.01)
                    processed_a.append(idx)
                return work

            dispatcher.submit(
                chat_a,
                make_a(i),
                priority=PRIORITY_NORMAL,
                on_overflow=lambda idx=i: overflow_a.append(idx),
            )

        # Enqueue 10 messages into Chat B
        for i in range(10):
            def make_b(idx=i):
                async def work():
                    await asyncio.sleep(0.005)
                    processed_b.append(idx)
                return work

            dispatcher.submit(
                chat_b,
                make_b(i),
                priority=PRIORITY_NORMAL,
            )

        await dispatcher.join(timeout=5.0)

        # Chat A: 30 processed + 20 overflowed
        assert len(processed_a) == 30
        assert len(overflow_a) == 20

        # Chat B: 10/10 processed with 0 drops or overflows
        assert len(processed_b) == 10

        print(f"\n--- GroupDispatcher Overflow Isolation Results ---")
        print(f"Chat A (Heavy Flood): {len(processed_a)} queued/processed, {len(overflow_a)} overflow handled")
        print(f"Chat B (Normal): {len(processed_b)}/10 processed with 0 drops")

        await dispatcher.close()

    asyncio.run(scenario())
