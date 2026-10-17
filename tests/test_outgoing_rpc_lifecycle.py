"""Outgoing RPC phases + sender_pending lifecycle. No governor/queue rewrite.

    python tests/test_outgoing_rpc_lifecycle.py
"""
from __future__ import annotations

import asyncio
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import connection_guard as cg
from modules.outgoing_profiler import (
    add_rpc_phase,
    begin_rpc_phases,
    end_rpc_phases,
    instrument_client,
    pending_rpc_snapshot,
)
from modules.runtime_snapshot import RuntimeSnapshotMonitor, collect_sync, detect_issues


PASSED = FAILED = 0


def check(label, cond, detail=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  PASS  {label} {detail}")
    else:
        FAILED += 1
        print(f"  FAIL  {label} {detail}")


class Logger:
    def __init__(self):
        self.infos = []
        self.errors = []

    def log_info(self, message):
        self.infos.append(str(message))

    def log_error(self, message):
        self.errors.append(str(message))


class SendMessageRequest:
    def __init__(self, peer=1):
        self.peer = peer


class DeleteMessagesRequest:
    def __init__(self, peer=1):
        self.peer = peer


class PingRequest:
    pass


class RequestState:
    def __init__(self, request, future, msg_id):
        self.request = request
        self.future = future
        self.msg_id = msg_id
        self.container_id = None


class TrackingSender:
    def __init__(self):
        self._pending_state = {}
        self._n = 0

    def put(self, request, *, done=False, age=0.0):
        self._n += 1
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        if done:
            future.set_result("ok")
        state = RequestState(request, future, self._n)
        self._pending_state[self._n] = state
        table = cg._seen_at(self)
        table[self._n] = time.monotonic() - float(age)
        return state


class TrackingClient:
    def __init__(self, rpc_ms=80, hang=False, leave_zombie=False):
        self._sender = TrackingSender()
        self.rpc_ms = rpc_ms
        self.hang = hang
        self.leave_zombie = leave_zombie
        self.calls = []

    async def _call(self, sender, request, ordered=False, flood_sleep_threshold=None):
        self.calls.append(type(request).__name__)
        state = sender.put(request)
        if self.hang:
            await asyncio.sleep(30)
            return "late"
        await asyncio.sleep(self.rpc_ms / 1000.0)
        state.future.set_result("ok")
        if not self.leave_zombie:
            sender._pending_state.pop(state.msg_id, None)
        return "ok"

    async def send_message(self, entity, text):
        return await self._call(self._sender, SendMessageRequest(entity))

    async def delete_messages(self, entity, ids):
        return await self._call(self._sender, DeleteMessagesRequest(entity))

    async def edit_permissions(self, *a, **k):
        return "ok"

    async def kick_participant(self, *a, **k):
        return "ok"


def _lines(logger, prefix):
    return [line for line in logger.infos + logger.errors if line.startswith(prefix)]


def test_phases_logged_on_slow_and_critical():
    print("\n### فازهای RPC جدا لاگ می‌شوند")

    async def scenario():
        logger = Logger()
        client = TrackingClient(rpc_ms=80)
        instrument_client(client, logger)
        cg.install_rpc_timeout(client, timeout=5.0, logger=logger)
        token, phases = begin_rpc_phases("send_message")
        add_rpc_phase("queue_wait_ms", 40)
        add_rpc_phase("governor_wait_ms", 25)
        add_rpc_phase("sender_wait_ms", 15)
        await client.send_message(-1, "hi")
        end_rpc_phases(token)
        logger2 = Logger()
        client2 = TrackingClient(rpc_ms=550)
        instrument_client(client2, logger2)
        await client2.send_message(-1, "slow")
        logger3 = Logger()
        client3 = TrackingClient(rpc_ms=2050)
        instrument_client(client3, logger3)
        await client3.send_message(-1, "critical")
        return logger, logger2, logger3, phases

    logger, slow, critical, phases = asyncio.run(scenario())
    slow_lines = _lines(slow, "OUTGOING RPC SLOW")
    crit_lines = _lines(critical, "OUTGOING RPC CRITICAL")
    print("    slow:", slow_lines[:1])
    print("    critical:", crit_lines[:1])
    check("SLOW بالای 500ms لاگ شد", bool(slow_lines))
    check("CRITICAL بالای 2000ms لاگ شد", bool(crit_lines))
    if slow_lines:
        text = slow_lines[0]
        for field in (
            "queue_wait_ms=",
            "governor_wait_ms=",
            "sender_wait_ms=",
            "rpc_await_ms=",
            "total_ms=",
            "sender_pending=",
        ):
            check(f"SLOW فیلد {field}", field in text)
    if crit_lines:
        text = crit_lines[0]
        check("CRITICAL rpc_await حدود 2s است", "rpc_await_ms=" in text)
        check("CRITICAL total_ms دارد", "total_ms=" in text)


def test_completed_and_timeout_leave_no_pending():
    print("\n### بعد از success / timeout / cancel جدول خالی است")

    async def scenario():
        logger = Logger()
        client = TrackingClient(rpc_ms=20)
        instrument_client(client, logger)
        cg.install_rpc_timeout(client, timeout=2.0, logger=logger)
        await client.send_message(1, "ok")
        after_ok = len(client._sender._pending_state)

        hung = TrackingClient(hang=True)
        instrument_client(hung, logger)
        cg.install_rpc_timeout(hung, timeout=0.05, logger=logger)
        timed_out = False
        try:
            await hung.send_message(1, "hang")
        except cg.RpcTimeout:
            timed_out = True
        after_timeout = len(hung._sender._pending_state)

        cancelled_client = TrackingClient(hang=True)
        instrument_client(cancelled_client, logger)
        cg.install_rpc_timeout(cancelled_client, timeout=5.0, logger=logger)
        task = asyncio.create_task(cancelled_client.send_message(1, "x"))
        await asyncio.sleep(0.02)
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, cg.RpcTimeout):
            pass
        after_cancel = len(cancelled_client._sender._pending_state)
        return after_ok, timed_out, after_timeout, after_cancel

    after_ok, timed_out, after_timeout, after_cancel = asyncio.run(scenario())
    check("success چیزی جا نگذاشت", after_ok == 0, f"-> {after_ok}")
    check("timeout رخ داد", timed_out)
    check("timeout جدول را خالی کرد", after_timeout == 0, f"-> {after_timeout}")
    check("cancel جدول را خالی کرد", after_cancel == 0, f"-> {after_cancel}")


def test_splusthon_zombie_is_reclaimed():
    print("\n### اگر SPlusthon ردیف تمام‌شده را جا بگذارد، finally پاکش می‌کند")

    async def scenario():
        logger = Logger()
        client = TrackingClient(rpc_ms=10, leave_zombie=True)
        instrument_client(client, logger)
        cg.install_rpc_timeout(client, timeout=2.0, logger=logger)
        await client.send_message(1, "zombie")
        return len(client._sender._pending_state)

    left = asyncio.run(scenario())
    check("زامبی بعد از بازگشت _call نماند", left == 0, f"-> {left}")


def test_inspect_and_stale_keepalive_vs_live_rpc():
    print("\n### keepalive کهنه حذف می‌شود؛ Send زنده می‌ماند")

    async def scenario():
        logger = Logger()
        sender = TrackingSender()
        live = sender.put(SendMessageRequest(7), age=8.0)
        sender.put(PingRequest(), age=45.0)
        sender.put(PingRequest(), done=True, age=2.0)
        cg.note_pending(sender)
        rows = cg.inspect_pending(sender)
        dropped = cg.reclaim_dead_pending(sender, logger=logger)
        return live, rows, dropped, sender, logger

    live, rows, dropped, sender, logger = asyncio.run(scenario())
    types_before = sorted(row["request_type"] for row in rows)
    left_types = [
        type(state.request).__name__ for state in sender._pending_state.values()
    ]
    stale_logs = _lines(logger, "STALE SENDER PENDING")
    print("    before=", types_before, "dropped=", dropped, "left=", left_types)
    print("    stale logs:", stale_logs)
    check("inspect نوع‌ها را می‌بیند", "PingRequest" in types_before and "SendMessageRequest" in types_before)
    check("حداقل یک ping کهنه/تمام‌شده حذف شد", dropped >= 2, f"-> {dropped}")
    check("Send زنده ماند", left_types == ["SendMessageRequest"], f"-> {left_types}")
    check("STALE SENDER PENDING لاگ شد", bool(stale_logs))
    if stale_logs:
        check("لاگ age_ms دارد", "age_ms=" in stale_logs[0])
        check("لاگ request_type دارد", "request_type=" in stale_logs[0])
        check("لاگ operation دارد", "operation=" in stale_logs[0])
    check("future زنده لغو نشد", not live.future.done())


def test_wave_of_groups_does_not_accumulate():
    print("\n### موج ۴۰ گروه بعد از اتمام، sender_pending را انباشته نمی‌کند")

    async def scenario():
        logger = Logger()
        client = TrackingClient(rpc_ms=15, leave_zombie=True)
        instrument_client(client, logger)
        cg.install_rpc_timeout(client, timeout=2.0, logger=logger)
        await asyncio.gather(*[
            client.send_message(-1000 - i, f"msg-{i}")
            for i in range(40)
        ])
        leftover_pings = 0
        for _ in range(12):
            client._sender.put(PingRequest(), done=True, age=40.0)
            leftover_pings += 1
        before = len(client._sender._pending_state)
        cg.note_pending(client._sender)
        dropped = cg.reclaim_dead_pending(client._sender, logger=logger)
        after = len(client._sender._pending_state)
        snap = pending_rpc_snapshot(client._sender)
        return before, dropped, after, leftover_pings, snap, logger

    before, dropped, after, leftover_pings, snap, logger = asyncio.run(scenario())
    print(
        f"    before={before} dropped={dropped} after={after} "
        f"pings={leftover_pings} snap={snap['sender_pending']}"
    )
    check("موج ۴۰ RPC زامبی باقی نگذاشت", before == leftover_pings, f"-> {before}")
    check("reclaim پینگ‌های کهنه را برداشت", after == 0, f"-> {after}")
    check("snapshot sender_pending=0", snap["sender_pending"] == 0)


def test_startup_30_60_90_snapshots_and_growth():
    print("\n### snapshotهای startup / 30 / 60-90 و رشد تدریجی sender_pending")

    async def scenario():
        logger = Logger()
        bot = types.SimpleNamespace(
            started_at=time.time(),
            client=types.SimpleNamespace(_sender=TrackingSender()),
            moderation_queue=types.SimpleNamespace(_pending_keys=set(), _queues={}),
            message_delete_queue=types.SimpleNamespace(_queues={}, _pending_ids=set()),
            group_dispatcher=types.SimpleNamespace(_normal_pending={}, _queues={}),
            rpc_governor=None,
            outgoing_sender=None,
            notice_cleanup=types.SimpleNamespace(_items={}, _workers={}),
            reply_input_peer_cache={},
        )
        monitor = RuntimeSnapshotMonitor(
            bot, logger, interval_seconds=0.04, lag_probe_seconds=0.0
        )
        await monitor.emit(title="PERFORMANCE SNAPSHOT reason=startup")
        sender = bot.client._sender
        for mark, count in ((10, 10), (20, 20), (40, 40), (60, 59)):
            sender._pending_state.clear()
            for _ in range(count):
                sender.put(PingRequest(), age=5.0)
            bot.started_at = time.time() - (
                0 if mark == 10 else 30 * 60 if mark == 20 else 60 * 60 if mark == 40 else 90 * 60
            )
            await monitor.emit()
        infos = "\n".join(logger.infos)
        errors = "\n".join(logger.errors)
        return logger, infos, errors, collect_sync(bot)

    logger, infos, errors, last = asyncio.run(scenario())
    print("    last sender_pending=", last.get("sender_pending"))
    check("startup snapshot هست", "reason=startup" in infos)
    check("milestone 30 دقیقه", "PERFORMANCE MILESTONE elapsed=1800s" in infos)
    check("milestone 60 دقیقه", "PERFORMANCE MILESTONE elapsed=3600s" in infos)
    check("milestone 90 دقیقه", "PERFORMANCE MILESTONE elapsed=5400s" in infos)
    check("رشد 10 دیده شد", "SENDER PENDING GROWTH" in errors and "crossed=10" in errors)
    check("رشد 20 دیده شد", "crossed=20" in errors)
    check("رشد 40 دیده شد", "crossed=40" in errors)
    check("snapshot sender_pending را نگه داشت", last["sender_pending"] == 59, f"-> {last['sender_pending']}")
    check("event_loop_lag در snapshot است", "event_loop_lag_ms" in last)
    check("pending_tasks در snapshot است", "pending_tasks" in last)
    check("memory_mb در snapshot است", "memory_mb" in last)


def test_directory_does_not_raise_or_spam():
    print("\n### دفترچهٔ یوزرنیم خطا پرتاب نمی‌کند و economy محلی نیست")
    import inspect
    import tempfile
    import economy.storage as storage
    from economy import directory
    import handlers.message_handler as handler

    storage.use_file(Path(tempfile.mkdtemp()) / "economy.json")
    check("یوزرنیم معتبر ثبت می‌شود", directory.remember(-9, 44, "hosein") == "44")
    check("تکراری خطا نمی‌دهد", directory.remember(-9, 44, "hosein") == "44")
    check("یوزرنیم خالی None است", directory.remember(-9, 44, None) is None)
    check("user خالی None است", directory.remember(-9, None, "x") is None)

    original = storage.transaction

    class Boom:
        def __enter__(self):
            raise RuntimeError("economy storage down")

        def __exit__(self, *a):
            return False

    storage.transaction = lambda *a, **k: Boom()
    try:
        raised = None
        try:
            result = directory.remember(-9, 55, "mina")
        except Exception as error:
            raised = error
            result = "raised"
        check("خرابی storage پرتاب نشد", raised is None, f"-> {raised!r}")
        check("خرابی storage None برگرداند", result is None, f"-> {result}")
    finally:
        storage.transaction = original

    names = handler.handle_new_message.__code__.co_varnames
    source = inspect.getsource(handler.handle_new_message)
    check("economy متغیر محلی هندلر نیست", "economy" not in names)
    check("import economy داخل هندلر نیست", "import economy" not in source)
    check("از username_directory ماژول‌سطح استفاده می‌شود", "username_directory.remember" in source)



def test_superseded_keepalive_is_dropped_live_rpc_stays():
    print("\n### پینگ قدیمی با پینگ تازه جایگزین می‌شود؛ Send زنده می‌ماند")

    async def scenario():
        logger = Logger()
        sender = TrackingSender()
        live = sender.put(SendMessageRequest(3), age=4.0)
        newest = None
        for age in (22.0, 18.0, 12.0, 3.0):
            newest = sender.put(PingRequest(), age=age)
        dropped = cg.reclaim_superseded_keepalive(sender, keep_newest=1, logger=logger)
        left = [
            type(state.request).__name__ for state in sender._pending_state.values()
        ]
        return live, newest, dropped, left, logger

    live, newest, dropped, left, logger = asyncio.run(scenario())
    check("سه پینگ قدیمی حذف شد", dropped == 3, f"-> {dropped}")
    check("Send زنده ماند", "SendMessageRequest" in left, f"-> {left}")
    check("فقط یک Ping زنده ماند", left.count("PingRequest") == 1, f"-> {left}")
    check("future پینگ تازه لغو نشد", not newest.future.done())
    check("future سند لغو نشد", not live.future.done())
    superseded = [line for line in logger.infos if line.startswith("KEEPALIVE SUPERSEDED")]
    check("KEEPALIVE SUPERSEDED لاگ شد", bool(superseded))
    if superseded:
        check("kept تعداد واقعی باقی‌مانده است", "kept=1" in superseded[0], f"-> {superseded[0]}")
        check("kept=0 دیگر پارامتر keep_newest نیست", "kept=0" not in superseded[0])


def test_reconnect_drops_keepalive_not_live_send():
    print("\n### reconnect فقط keepalive را پاک می‌کند")

    async def scenario():
        sender = TrackingSender()
        live = sender.put(DeleteMessagesRequest(9), age=2.0)
        sender.put(PingRequest(), age=8.0)
        sender.put(PingRequest(), age=16.0)
        dropped = cg.drop_keepalive_pending(sender)
        left = [
            type(state.request).__name__ for state in sender._pending_state.values()
        ]
        return live, dropped, left

    live, dropped, left = asyncio.run(scenario())
    check("هر دو ping حذف شد", dropped == 2, f"-> {dropped}")
    check("Delete زنده ماند", left == ["DeleteMessagesRequest"], f"-> {left}")
    check("future دیلیت لغو نشد", not live.future.done())


def test_new_keepalive_ping_clears_old_pending():
    print("\n### ping factory keeps at most one live Ping; Send stays")

    async def scenario():
        logger = Logger()
        client = TrackingClient(rpc_ms=5)
        client._sender._keepalive_ping = lambda rnd_id: rnd_id
        client._sender._handle_pong = lambda message: None
        client._sender._start_reconnect = lambda error: None
        client._sender._user_connected = True
        client._sender._reconnecting = False
        client._sender._ping = None
        instrument_client(client, logger)
        sender = client._sender
        sender.put(SendMessageRequest(1), age=1.0)
        sender.put(PingRequest(), age=9.0)
        sender.put(PingRequest(), age=19.0)
        sender._keepalive_ping(77)
        left = [
            type(state.request).__name__ for state in sender._pending_state.values()
        ]
        return left, logger

    left, logger = asyncio.run(scenario())
    ping_left = left.count("PingRequest")
    check("at most one unanswered Ping", ping_left <= 1, f"-> {left}")
    check("Send stayed", "SendMessageRequest" in left, f"-> {left}")
    check(
        "only Send and at most one Ping",
        set(left) <= {"SendMessageRequest", "PingRequest"},
        f"-> {left}",
    )
    texts = "\n".join(logger.infos)
    check("kept=0 is not on the ping path", "kept=0" not in texts)


def test_detect_sender_growth_sequence():
    print("\n### دتکتور 0→10→20→40→60")
    previous = {"sender_pending": 0, "rpc_pending": 0, "event_loop_lag_ms": 0}
    current = {"sender_pending": 22, "rpc_pending": 0, "event_loop_lag_ms": 1.9}
    lines = "\n".join(detect_issues(current, previous))
    check("crossed 10", "crossed=10" in lines)
    check("crossed 20", "crossed=20" in lines)
    check("40 هنوز نه", "crossed=40" not in lines)


def main():
    test_phases_logged_on_slow_and_critical()
    test_completed_and_timeout_leave_no_pending()
    test_splusthon_zombie_is_reclaimed()
    test_inspect_and_stale_keepalive_vs_live_rpc()
    test_superseded_keepalive_is_dropped_live_rpc_stays()
    test_reconnect_drops_keepalive_not_live_send()
    test_new_keepalive_ping_clears_old_pending()
    test_wave_of_groups_does_not_accumulate()
    test_startup_30_60_90_snapshots_and_growth()
    test_directory_does_not_raise_or_spam()
    test_detect_sender_growth_sequence()
    print(f"\npassed={PASSED} failed={FAILED}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
