"""Prove diagnostic PERFORMANCE SNAPSHOT logs fire without mutating queues."""
from __future__ import annotations

import asyncio
import ast
import sys
import time
import types
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if "splusthon" not in sys.modules:
    splusthon = types.ModuleType("splusthon")
    class PeerUser:
        def __init__(self, user_id):
            self.user_id = user_id
    class InputPeerUser:
        def __init__(self, user_id, access_hash):
            self.user_id, self.access_hash = user_id, access_hash
    splusthon.types = types.SimpleNamespace(PeerUser=PeerUser, InputPeerUser=InputPeerUser)
    sys.modules["splusthon"] = splusthon

from modules.runtime_snapshot import (
    ACCUMULATION_KEYS,
    METRIC_KEYS,
    RuntimeSnapshotMonitor,
    collect,
    collect_sync,
    detect_accumulation,
    detect_issues,
    format_accumulation_report,
    format_snapshot,
    prune_unbounded_maps,
    vs_baseline_lines,
)


class Logger:
    def __init__(self):
        self.infos = []
        self.errors = []

    def log_info(self, message):
        self.infos.append(str(message))

    def log_error(self, message):
        self.errors.append(str(message))


class FakeQueue:
    def __init__(self, size=0):
        self._size = int(size)

    def qsize(self):
        return self._size


class FakeWaiter:
    def __init__(self, wait_ms=0, done=False, bucket="send"):
        self.enqueued_at = time.perf_counter() - (float(wait_ms) / 1000.0)
        self.future = SimpleNamespace(done=lambda: bool(done))
        self.bucket = bucket


class FakeTask:
    def __init__(self, done=False):
        self._done = bool(done)

    def done(self):
        return self._done


def make_bot(**overrides):
    bot = SimpleNamespace(
        started_at=time.time(),
        moderation_queue=SimpleNamespace(_pending_keys=set(), _queues={}),
        message_delete_queue=SimpleNamespace(_queues={}, _pending_ids=set()),
        group_dispatcher=SimpleNamespace(_normal_pending={}, _queues={}),
        rpc_governor=SimpleNamespace(_queues={0: {}, 1: {}, 2: {}, 3: {}}),
        outgoing_sender=SimpleNamespace(_normal_pending=lambda: 0, _queues={}),
        notice_cleanup=SimpleNamespace(_items={}, _workers={}),
        reply_input_peer_cache={},
        client=SimpleNamespace(_sender=None),
    )
    for key, value in overrides.items():
        setattr(bot, key, value)
    return bot


def check(condition, message):
    if not condition:
        raise AssertionError(message)
    print("  PASS", message)


def test_snapshot_contains_requested_metrics():
    print("test_snapshot_contains_requested_metrics")
    bot = make_bot()
    snapshot = collect_sync(bot)
    missing = [key for key in METRIC_KEYS if key not in snapshot]
    check(not missing, f"all requested metrics present {missing}")
    check(snapshot["memory_mb"] >= 0, f"memory_mb={snapshot['memory_mb']}")
    text = format_snapshot(snapshot)
    check(text.startswith("PERFORMANCE SNAPSHOT"), "title is PERFORMANCE SNAPSHOT")
    for key in METRIC_KEYS:
        check(f"{key}=" in text, f"log line {key}")


def test_reads_existing_queue_sizes_only():
    print("test_reads_existing_queue_sizes_only")
    pending_keys = {("g1", 1, "mute"), ("g1", 2, "ban")}
    delete_ids = {11, 22, 33}
    bot = make_bot(
        moderation_queue=SimpleNamespace(
            _pending_keys=pending_keys,
            _queues={"g1": FakeQueue(2)},
        ),
        message_delete_queue=SimpleNamespace(
            _queues={"g1": FakeQueue(4), "g2": FakeQueue(1)},
            _pending_ids=delete_ids,
        ),
        group_dispatcher=SimpleNamespace(
            _normal_pending={"g1": 7, "g2": 1},
            _queues={("g1", "normal"): FakeQueue(3), ("g1", "admin"): FakeQueue(9)},
        ),
        outgoing_sender=SimpleNamespace(_normal_pending=lambda: 6, _queues={}),
        notice_cleanup=SimpleNamespace(_items={"g1": [{}, {}], "g2": [{}]}, _workers={}),
        reply_input_peer_cache={"u1": object(), "u2": object()},
        client=SimpleNamespace(_sender=SimpleNamespace(_pending_state={i: object() for i in range(6)})),
        rpc_governor=SimpleNamespace(_queues={
            2: {"g1": [FakeWaiter(wait_ms=80), FakeWaiter(wait_ms=10, done=True)]},
        }),
    )
    snapshot = collect_sync(bot)
    check(snapshot["moderation_queue_pending"] == 2, f"moderation={snapshot['moderation_queue_pending']}")
    check(snapshot["delete_queue_pending"] == 5, f"delete={snapshot['delete_queue_pending']}")
    check(snapshot["normal_queue_pending"] == 8, f"normal={snapshot['normal_queue_pending']}")
    check(snapshot["sender_pending"] == 6, f"sender={snapshot['sender_pending']}")
    check(snapshot["active_auto_notice_timers"] == 3, f"notices={snapshot['active_auto_notice_timers']}")
    check(snapshot["peer_cache_size"] == 2, f"peer={snapshot['peer_cache_size']}")
    check(snapshot["rpc_governor_wait_ms"] >= 70, f"governor wait={snapshot['rpc_governor_wait_ms']}")
    check(pending_keys == {("g1", 1, "mute"), ("g1", 2, "ban")}, "moderation set unchanged")
    check(delete_ids == {11, 22, 33}, "delete ids unchanged")


def test_growth_and_backlog_detectors():
    print("test_growth_and_backlog_detectors")
    previous = {key: 0 for key in METRIC_KEYS}
    current = {key: 0 for key in METRIC_KEYS}
    current.update({
        "event_loop_lag_ms": 120.0,
        "pending_tasks": 80,
        "active_tasks": 180,
        "moderation_queue_pending": 12,
        "delete_queue_pending": 9,
        "normal_queue_pending": 20,
        "rpc_pending": 14,
        "rpc_governor_wait_ms": 90.0,
        "sender_pending": 11,
        "active_auto_notice_timers": 18,
        "username_directory_cache_size": 40,
        "economy_cache_size": 55,
        "peer_cache_size": 30,
        "memory_mb": 40.0,
    })
    previous["memory_mb"] = 10.0
    lines = detect_issues(current, previous)
    joined = "\n".join(lines)
    check("EVENT LOOP LAG DETECTED" in joined, "lag detector")
    check("TASK GROWTH DETECTED" in joined, "task detector")
    check("QUEUE BACKLOG DETECTED" in joined, "queue detector")
    check("RPC BACKLOG DETECTED" in joined, "rpc detector")
    check("AUTO NOTICE TIMER GROWTH DETECTED" in joined, "notice detector")
    check("CACHE GROWTH DETECTED" in joined, "cache detector")
    check("GROWING STATE DETECTED metric=memory_mb" in joined, "growing memory")
    check("GROWING STATE DETECTED metric=normal_queue_pending" in joined, "growing normal queue")


def test_loop_logs_baseline_and_second_snapshot():
    print("test_loop_logs_baseline_and_second_snapshot")

    async def scenario():
        logger = Logger()
        bot = make_bot()
        monitor = RuntimeSnapshotMonitor(
            bot, logger, interval_seconds=0.05, lag_probe_seconds=0.0
        )
        monitor.start()
        await asyncio.sleep(0.16)
        await monitor.stop()
        snapshots = [msg for msg in logger.infos if msg.startswith("PERFORMANCE SNAPSHOT")]
        check(len(snapshots) >= 2, f"periodic snapshots logged count={len(snapshots)}")
        check(any("PERFORMANCE BASELINE RECORDED" in msg for msg in logger.infos), "baseline recorded")
        first = snapshots[0]
        for key in METRIC_KEYS:
            check(f"{key}=" in first, f"first snapshot has {key}")
        check(monitor.baseline is not None, "baseline kept")
        check(monitor.previous is not None, "previous kept")

    asyncio.run(scenario())


def test_burst_then_backlog_appears_in_logs():
    print("test_burst_then_backlog_appears_in_logs")

    async def scenario():
        logger = Logger()
        bot = make_bot()
        monitor = RuntimeSnapshotMonitor(
            bot, logger, interval_seconds=0.04, lag_probe_seconds=0.0
        )
        await monitor.emit()
        bot.moderation_queue._pending_keys.update({
            ("g1", i, "mute") for i in range(8)
        })
        bot.message_delete_queue._queues["g1"] = FakeQueue(12)
        bot.group_dispatcher._normal_pending["g1"] = 15
        bot.outgoing_sender = SimpleNamespace(_normal_pending=lambda: 11, _queues={})
        bot.client = SimpleNamespace(
            _sender=SimpleNamespace(_pending_state={i: object() for i in range(11)})
        )
        bot.notice_cleanup._items["g1"] = [{} for _ in range(9)]
        bot.reply_input_peer_cache.update({f"u{i}": i for i in range(20)})
        await monitor.emit()
        errors = "\n".join(logger.errors)
        infos = "\n".join(logger.infos)
        check("PERFORMANCE SNAPSHOT" in infos, "burst snapshot logged")
        check("QUEUE BACKLOG DETECTED" in errors, "burst queue backlog")
        check("RPC BACKLOG DETECTED" in errors, "burst sender backlog")
        check("AUTO NOTICE TIMER GROWTH DETECTED" in errors, "burst notice growth")
        check("GROWING STATE DETECTED" in errors, "burst growing state")

    asyncio.run(scenario())


def test_milestones_compare_to_baseline():
    print("test_milestones_compare_to_baseline")

    async def scenario():
        logger = Logger()
        bot = make_bot(started_at=time.time() - (30 * 60) - 5)
        monitor = RuntimeSnapshotMonitor(
            bot, logger, interval_seconds=0.05, lag_probe_seconds=0.0
        )
        await monitor.emit()
        infos = "\n".join(logger.infos)
        check("PERFORMANCE MILESTONE elapsed=1800s" in infos, "30 minute milestone")
        check("PERFORMANCE VS BASELINE" in infos, "vs baseline logged")
        check("UNUSUAL GROWTH SINCE START" in infos, "unusual growth summary")

        bot.started_at = time.time() - (60 * 60) - 5
        await monitor.emit()
        infos = "\n".join(logger.infos)
        check("PERFORMANCE MILESTONE elapsed=3600s" in infos, "60 minute milestone")

        bot.started_at = time.time() - (90 * 60) - 5
        await monitor.emit()
        infos = "\n".join(logger.infos)
        check("PERFORMANCE MILESTONE elapsed=5400s" in infos, "90 minute milestone")

    asyncio.run(scenario())


def test_event_loop_lag_is_measured():
    print("test_event_loop_lag_is_measured")

    async def scenario():
        bot = make_bot()
        snapshot = await collect(bot, lag_probe_seconds=0.01)
        check(snapshot["event_loop_lag_ms"] >= 0, f"lag={snapshot['event_loop_lag_ms']}")
        check(snapshot["active_tasks"] >= 1, f"active_tasks={snapshot['active_tasks']}")

    asyncio.run(scenario())


def test_username_directory_unboundlocal_still_fixed():
    print("test_username_directory_unboundlocal_still_fixed")
    source = (ROOT / "handlers" / "message_handler.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    handler = None
    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "handle_new_message":
            handler = node
            break
    check(handler is not None, "handle_new_message found")
    inner_economy_imports = []
    for node in ast.walk(handler):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "economy":
                    inner_economy_imports.append(node.lineno)
        if isinstance(node, ast.ImportFrom) and node.module == "economy":
            inner_economy_imports.append(node.lineno)
    check(not inner_economy_imports, f"no local economy import {inner_economy_imports}")
    from handlers import message_handler
    check("economy" not in message_handler.handle_new_message.__code__.co_varnames,
          "economy is not a local variable")


def test_vs_baseline_flags_queue_growth():
    print("test_vs_baseline_flags_queue_growth")
    baseline = {key: 0 for key in METRIC_KEYS}
    current = dict(baseline)
    current["normal_queue_pending"] = 22
    current["memory_mb"] = 50.0
    lines = vs_baseline_lines(current, baseline)
    joined = "\n".join(lines)
    check("normal_queue_pending baseline=0 current=22" in joined, "delta line")
    check("UNUSUAL GROWTH SINCE START metrics=" in joined, "summary present")
    check("normal_queue_pending" in joined.split("metrics=")[-1], "queue flagged")


def test_accumulation_report_fields_and_governor_buckets():
    print("test_accumulation_report_fields_and_governor_buckets")
    bot = make_bot(
        moderation_queue=SimpleNamespace(
            _pending_keys={("g1", 1, "mute")},
            _queues={"g1": FakeQueue(2)},
            _workers={"g1": [FakeTask(done=True), FakeTask(done=False)]},
        ),
        message_delete_queue=SimpleNamespace(
            _queues={"g1": FakeQueue(3)},
            _pending_ids={1, 2, 3},
            _workers={"g1": FakeTask(done=True)},
        ),
        group_dispatcher=SimpleNamespace(
            _normal_pending={"g1": 4},
            _queues={("g1", "normal"): FakeQueue(4), ("g1", "admin"): FakeQueue(1)},
            _workers={("g1", "normal"): [FakeTask(done=True)]},
        ),
        outgoing_sender=SimpleNamespace(
            _normal_pending=lambda: 2,
            _queues={("g1", "normal"): FakeQueue(2)},
            _workers={("g1", "normal"): [FakeTask(done=False)]},
        ),
        rpc_governor=SimpleNamespace(_queues={
            1: {"g1": [FakeWaiter(wait_ms=40, bucket="delete")]},
            2: {"g1": [FakeWaiter(wait_ms=80, bucket="send"), FakeWaiter(wait_ms=10, done=True, bucket="send")]},
            3: {"g1": [FakeWaiter(wait_ms=20, bucket="heavy")]},
        }),
        bot_sent_messages=[1, 2, 3],
        reply_input_peer_cache={"u1": object(), "u2": object()},
        performance_monitor=SimpleNamespace(_batch={"a": {}, "b": {}}),
        punished_users={"x", "y"},
        flood_messages={"g1": [1, 2]},
    )
    snapshot = collect_sync(bot)
    missing = [key for key in ACCUMULATION_KEYS if key not in snapshot]
    check(not missing, f"accumulation keys present {missing}")
    check(snapshot["governor_waiting"] == 3, f"waiting={snapshot['governor_waiting']}")
    check(snapshot["governor_waiting_by_bucket"]["delete"] == 1, "delete bucket")
    check(snapshot["governor_waiting_by_bucket"]["send"] == 1, "send bucket")
    check(snapshot["governor_waiting_by_bucket"]["heavy"] == 1, "heavy bucket")
    check(snapshot["leftover_done_workers"] >= 3, f"leftover={snapshot['leftover_done_workers']}")
    check(snapshot["dispatcher_jobs"] == 5, f"dispatcher_jobs={snapshot['dispatcher_jobs']}")
    check(snapshot["sender_jobs"] == 2, f"sender_jobs={snapshot['sender_jobs']}")
    check(snapshot["delete_jobs"] == 3, f"delete_jobs={snapshot['delete_jobs']}")
    check(snapshot["bot_sent_messages_size"] == 3, "bot_sent_messages counted")
    check(snapshot["performance_batch_size"] == 2, "performance batch counted")
    check(snapshot["long_map_total"] >= 7, f"long_map_total={snapshot['long_map_total']}")
    text = format_accumulation_report(snapshot)
    check(text.startswith("ACCUMULATION REPORT"), "accumulation title")
    check("governor_waiting_by_bucket=" in text, "bucket line")
    check("pending_tasks=" in text, "pending_tasks line")
    check("memory_mb=" in text, "memory line")


def test_accumulation_growth_and_periodic_log():
    print("test_accumulation_growth_and_periodic_log")
    previous = {key: 0 for key in ACCUMULATION_KEYS}
    current = {key: 0 for key in ACCUMULATION_KEYS}
    current.update({
        "pending_tasks": 40,
        "rpc_pending": 6,
        "sender_pending": 5,
        "governor_waiting": 4,
        "memory_mb": 20.0,
        "username_directory_cache_size": 30,
        "leftover_done_workers": 2,
        "long_map_total": 80,
        "governor_waiting_by_bucket": {"send": 3, "delete": 1},
    })
    previous["memory_mb"] = 10.0
    lines = detect_accumulation(current, previous)
    joined = "\n".join(lines)
    check("ACCUMULATION GROWTH metric=pending_tasks" in joined, "task growth")
    check("ACCUMULATION GROWTH metric=memory_mb" in joined, "memory growth")
    check("WORKER LEFTOVER DETECTED" in joined, "leftover workers")
    check("GOVERNOR WAITING PER BUCKET" in joined, "waiting per bucket")

    async def scenario():
        logger = Logger()
        bot = make_bot()
        monitor = RuntimeSnapshotMonitor(
            bot, logger, interval_seconds=0.04, lag_probe_seconds=0.0,
            accumulation_interval_seconds=0.05,
        )
        monitor.start()
        await asyncio.sleep(0.16)
        await monitor.stop()
        reports = [msg for msg in logger.infos if msg.startswith("ACCUMULATION REPORT")]
        check(len(reports) >= 2, f"periodic accumulation count={len(reports)}")
        first = reports[0]
        for key in ("pending_tasks", "rpc_pending", "sender_pending", "governor_waiting", "memory_mb"):
            check(f"{key}=" in first, f"accumulation has {key}")

    asyncio.run(scenario())


def test_prune_unbounded_maps_caps_lists_and_caches():
    print("test_prune_unbounded_maps_caps_lists_and_caches")
    bot = make_bot(
        bot_sent_messages=list(range(2500)),
        reply_input_peer_cache={f"u{i}": i for i in range(700)},
        performance_monitor=SimpleNamespace(_batch={f"k{i}": {"count": 1, "max_ms": 200} for i in range(600)}),
    )
    trimmed = prune_unbounded_maps(bot)
    check(len(bot.bot_sent_messages) <= 2000, f"sent={len(bot.bot_sent_messages)}")
    check(len(bot.reply_input_peer_cache) <= 500, f"peer={len(bot.reply_input_peer_cache)}")
    check(len(bot.performance_monitor._batch) <= 500, f"batch={len(bot.performance_monitor._batch)}")
    check(trimmed.get("bot_sent_messages", 0) > 0, "sent trimmed")
    check(trimmed.get("reply_input_peer_cache", 0) > 0, "peer trimmed")
    check(trimmed.get("performance_batch", 0) > 0, "batch trimmed")


def main():
    test_snapshot_contains_requested_metrics()
    test_reads_existing_queue_sizes_only()
    test_growth_and_backlog_detectors()
    test_loop_logs_baseline_and_second_snapshot()
    test_burst_then_backlog_appears_in_logs()
    test_milestones_compare_to_baseline()
    test_event_loop_lag_is_measured()
    test_username_directory_unboundlocal_still_fixed()
    test_vs_baseline_flags_queue_growth()
    test_accumulation_report_fields_and_governor_buckets()
    test_accumulation_growth_and_periodic_log()
    test_prune_unbounded_maps_caps_lists_and_caches()
    print("ALL RUNTIME SNAPSHOT TESTS PASSED")


if __name__ == "__main__":
    main()
