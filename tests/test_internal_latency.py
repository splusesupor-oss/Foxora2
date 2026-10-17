"""Benchmark internal overhead around an 88ms fake RPC.

    python tests/test_internal_latency.py

Does not touch Soroush. Measures only Python queue / yield / worker delay.
"""
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.group_dispatch import (
    PRIORITY_ADMIN,
    PRIORITY_COMMAND,
    PRIORITY_NORMAL,
    GroupDispatcher,
)
from modules.message_delete_queue import MessageDeleteQueue
from modules.moderation_queue import ModerationQueue

PASSED = FAILED = 0
RPC_MS = 88


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
        self.infos.append(message)

    def log_error(self, message):
        self.errors.append(message)


async def fake_rpc(ms=RPC_MS):
    await asyncio.sleep(ms / 1000.0)
    return True


def parse_wait(infos, kind=None):
    waits, yields, handlers = [], [], []
    for line in infos:
        if line.startswith("QUEUE WAIT TIME"):
            if kind and f"kind={kind}" not in line and f"lane={kind}" not in line:
                continue
            for part in line.split():
                if part.startswith("queue_wait_ms="):
                    waits.append(float(part.split("=", 1)[1]))
                if part.startswith("yield_ms="):
                    yields.append(float(part.split("=", 1)[1]))
        if line.startswith("HANDLER TIME"):
            if kind and f"kind={kind}" not in line and f"lane={kind}" not in line:
                continue
            for part in line.split():
                if part.startswith("handler_ms="):
                    handlers.append(float(part.split("=", 1)[1]))
    return waits, yields, handlers


def test_idle_admin_overhead():
    print("\n### BEFORE: سکوت تنها روی گروه خالی (RPC مصنوعی 88ms)")

    async def scenario():
        logger = Logger()
        dispatcher = GroupDispatcher(logger=logger)
        marks = {}

        async def mute():
            marks["start"] = time.perf_counter()
            await fake_rpc(RPC_MS)
            marks["after_rpc"] = time.perf_counter()

        t0 = time.perf_counter()
        dispatcher.submit(-1, mute, priority=PRIORITY_ADMIN, kind="admin")
        await dispatcher.join(timeout=2)
        total_ms = (time.perf_counter() - t0) * 1000
        after_rpc_gap = (marks["after_rpc"] - marks["start"]) * 1000
        return total_ms, after_rpc_gap, logger.infos

    total_ms, rpc_span, infos = asyncio.run(scenario())
    waits, yields, handlers = parse_wait(infos)
    print(f"    total_ms={total_ms:.1f}  rpc_span={rpc_span:.1f}  "
          f"queue_wait={waits} yield={yields} handler={handlers}")
    check("total نزدیک RPC است (سربار صف < 50ms)",
          80 <= total_ms <= 160, f"-> {total_ms:.1f}ms")
    check("بعد از RPC فاصله اضافه نیست",
          80 <= rpc_span <= 120, f"-> {rpc_span:.1f}ms")


def test_command_does_not_yield_behind_admin():
    print("\n### AFTER: command پشت admin همان گروه yield نمی‌کند")

    async def scenario():
        logger = Logger()
        dispatcher = GroupDispatcher(logger=logger)
        hold = asyncio.Event()
        command_started = []

        async def admin():
            await hold.wait()

        async def command():
            command_started.append(time.perf_counter())

        t0 = time.perf_counter()
        dispatcher.submit(-2, admin, priority=PRIORITY_ADMIN, kind="admin")
        await asyncio.sleep(0)
        dispatcher.submit(-2, command, priority=PRIORITY_COMMAND, kind="command")
        await asyncio.sleep(0.05)
        started = bool(command_started)
        delay = None if not command_started else (command_started[0] - t0) * 1000
        hold.set()
        await dispatcher.join(timeout=2)
        return started, delay, logger.infos

    started, delay, infos = asyncio.run(scenario())
    waits, yields, _ = parse_wait(infos, "command")
    print(f"    command_started_during_admin={started}  "
          f"start_delay_ms={delay} yield={yields} queue_wait={waits}")
    check("command همزمان با admin شروع شد", started and delay is not None and delay < 50,
          f"-> {delay}")
    check("yield_ms صفر است یا لاگ نشد",
          not yields or max(yields) < 5, f"-> {yields}")


def test_admin_does_not_yield_behind_command():
    print("\n### BEFORE: admin پشت command همان گروه (نباید yield کند)")

    async def scenario():
        logger = Logger()
        dispatcher = GroupDispatcher(logger=logger)
        hold = asyncio.Event()
        admin_started = []

        async def command():
            await hold.wait()

        async def admin():
            admin_started.append(time.perf_counter())
            await fake_rpc(RPC_MS)

        dispatcher.submit(-3, command, priority=PRIORITY_COMMAND, kind="command")
        await asyncio.sleep(0)
        t0 = time.perf_counter()
        dispatcher.submit(-3, admin, priority=PRIORITY_ADMIN, kind="admin")
        await asyncio.sleep(0.05)
        started_while_command_held = bool(admin_started)
        delay = None if not admin_started else (admin_started[0] - t0) * 1000
        hold.set()
        await dispatcher.join(timeout=2)
        return started_while_command_held, delay, logger.infos

    started, delay, infos = asyncio.run(scenario())
    print(f"    admin_started_during_command={started} delay_ms={delay}")
    check("سکوت/admin همان لحظه شروع شد", started and delay is not None and delay < 50,
          f"-> {delay}")


def test_group_b_not_queued_behind_a():
    print("\n### BEFORE: RPC 88ms گروه B پشت شغل 400ms گروه A نمی‌ماند")

    async def scenario():
        dispatcher = GroupDispatcher(logger=Logger())
        marks = {}

        async def slow_a():
            await asyncio.sleep(0.40)

        async def fast_b():
            marks["b"] = time.perf_counter()
            await fake_rpc(RPC_MS)

        t0 = time.perf_counter()
        dispatcher.submit(-10, slow_a, priority=PRIORITY_NORMAL, kind="normal")
        await asyncio.sleep(0)
        dispatcher.submit(-20, fast_b, priority=PRIORITY_ADMIN, kind="admin")
        await dispatcher.join(timeout=2)
        b_total = (marks["b"] - t0) * 1000 if "b" in marks else None
        wall = (time.perf_counter() - t0) * 1000
        return b_total, wall

    b_start, wall = asyncio.run(scenario())
    print(f"    B_start_delay_ms={b_start}  wall={wall:.1f}")
    check("شروع B نزدیک صفر است نه 400ms",
          b_start is not None and b_start < 50, f"-> {b_start}")


def test_same_lane_serial():
    print("\n### BEFORE: دو admin همان گروه روی یک worker سریال‌اند")

    async def scenario():
        dispatcher = GroupDispatcher(logger=Logger())
        order = []

        async def first():
            order.append(("1s", time.perf_counter()))
            await asyncio.sleep(0.12)
            order.append(("1e", time.perf_counter()))

        async def second():
            order.append(("2s", time.perf_counter()))
            await fake_rpc(RPC_MS)
            order.append(("2e", time.perf_counter()))

        dispatcher.submit(-4, first, priority=PRIORITY_ADMIN, kind="admin")
        await asyncio.sleep(0)
        dispatcher.submit(-4, second, priority=PRIORITY_ADMIN, kind="admin")
        await dispatcher.join(timeout=2)
        gap = (order[2][1] - order[1][1]) * 1000
        return [row[0] for row in order], gap

    names, gap = asyncio.run(scenario())
    print(f"    order={names}  gap_after_first_ms={gap:.1f}")
    check("ترتیب 1s,1e,2s,2e", names == ["1s", "1e", "2s", "2e"], f"-> {names}")
    check("دومی بلافاصله بعد از اولی شروع شد (سربار < 30ms)",
          0 <= gap < 30, f"-> {gap:.1f}ms")


def test_delete_no_inter_batch_sleep():
    print("\n### AFTER: صف حذف بعد از RPC موفق sleep زمانی ندارد")

    class Client:
        def __init__(self):
            self.calls = []

        async def delete_messages(self, chat_id, ids):
            self.calls.append(time.perf_counter())
            await fake_rpc(50)

    async def scenario():
        client = Client()
        queue = MessageDeleteQueue(
            client, Logger(), batch_size=2, inter_batch_delay=0,
        )
        t0 = time.perf_counter()
        fut = queue.enqueue(-5, [1, 2, 3, 4], priority=1)
        await asyncio.wait_for(asyncio.wrap_future(fut), timeout=2)
        return (time.perf_counter() - t0) * 1000, client.calls

    wall, calls = asyncio.run(scenario())
    extra = None
    if len(calls) >= 2:
        extra = (calls[1] - calls[0]) * 1000 - 50
    print(f"    wall={wall:.1f}  inter_batch_extra_ms={extra}")
    check("دو batch فقط حدود 2×50ms طول کشید",
          90 <= wall <= 160, f"-> {wall:.1f}")
    check("بین batch خواب ده‌ها ms نیست",
          extra is not None and extra < 20, f"-> {extra}")


def test_moderation_callback_does_not_hold_worker():
    print("\n### BEFORE: callback moderation worker را نگه نمی‌دارد")

    async def scenario():
        queue = ModerationQueue(Logger())
        order = []
        hold = asyncio.Event()

        async def mute():
            order.append("mute_rpc")
            await fake_rpc(30)
            return True

        async def notice(_result):
            order.append("notice_start")
            await hold.wait()
            order.append("notice_end")

        async def second():
            order.append("second")
            return True

        queue.enqueue(-6, "mute", mute, user_id=1, on_success=notice)
        await asyncio.sleep(0.08)
        queue.enqueue(-6, "ban", second, user_id=2)
        await asyncio.sleep(0.08)
        second_during_notice = "second" in order and "notice_end" not in order
        hold.set()
        await asyncio.sleep(0.05)
        return second_during_notice, order

    ok, order = asyncio.run(scenario())
    print(f"    order={order}")
    check("ban بعدی پشت notice نماند", ok, f"-> {order}")


def main():
    test_idle_admin_overhead()
    test_command_does_not_yield_behind_admin()
    test_admin_does_not_yield_behind_command()
    test_group_b_not_queued_behind_a()
    test_same_lane_serial()
    test_delete_no_inter_batch_sleep()
    test_moderation_callback_does_not_hold_worker()
    print(f"\nafter passed={PASSED} failed={FAILED}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
