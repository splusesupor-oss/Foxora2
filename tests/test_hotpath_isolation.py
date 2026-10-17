"""Scheduling isolation: admin/delete/moderation must not freeze other work."""
import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if "splusthon" not in sys.modules:
    import types
    fake = types.ModuleType("splusthon")
    fake.Button = object
    fake.types = types.ModuleType("splusthon.types")
    tl = types.ModuleType("splusthon.tl")
    tl_types = types.ModuleType("splusthon.tl.types")

    class _Ent:
        def __init__(self, offset=0, length=0, **_kwargs):
            self.offset = offset
            self.length = length

    tl_types.MessageEntityBold = _Ent
    tl_types.MessageEntityBlockquote = _Ent
    tl.types = tl_types
    tl.functions = types.ModuleType("splusthon.tl.functions")
    fake.tl = tl
    sys.modules["splusthon"] = fake
    sys.modules["splusthon.tl"] = tl
    sys.modules["splusthon.tl.types"] = tl_types
    sys.modules["splusthon.tl.functions"] = tl.functions
    sys.modules["splusthon.types"] = fake.types

from modules.group_dispatch import (
    PRIORITY_ADMIN,
    PRIORITY_NORMAL,
    GroupDispatcher,
    classify_priority,
)
from modules.message_delete_queue import MessageDeleteQueue
from modules.moderation_queue import ModerationQueue
import modules.bot_detector as bot_detector
from handlers.message_handler import (
    _is_management_command,
    _INTERNAL_EXACT_COMMANDS,
)

PASSED = FAILED = 0


def check(label, cond, detail=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  PASS  {label}")
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


def test_delete_queue_uses_small_batches():
    print("\n### صف حذف بچ کوچک دارد و sender را قفل نمی‌کند")
    queue = MessageDeleteQueue(SimpleNamespace(), Logger())
    check("default batch_size is 15", queue.batch_size == 15, f"-> {queue.batch_size}")
    check("inter_batch_delay is 0", queue.inter_batch_delay == 0,
          f"-> {queue.inter_batch_delay}")


def test_delete_queue_logs_wait_time():
    print("\n### صف حذف QUEUE WAIT TIME را ثبت می‌کند")

    class Client:
        async def delete_messages(self, chat_id, ids):
            await asyncio.sleep(0.08)
            return None

    async def scenario():
        logger = Logger()
        queue = MessageDeleteQueue(
            Client(), logger, batch_size=10, inter_batch_delay=0,
        )
        first = queue.enqueue(-1, [1], priority=1)
        await asyncio.sleep(0.02)
        second = queue.enqueue(-1, [2], priority=1)
        await asyncio.wait_for(asyncio.wrap_future(second), timeout=1)
        await asyncio.wait_for(asyncio.wrap_future(first), timeout=1)
        return logger.infos

    infos = asyncio.run(scenario())
    check(
        "QUEUE WAIT TIME logged for delayed delete",
        any(item.startswith("QUEUE WAIT TIME") and "lane=delete" in item
            for item in infos),
        f"-> {infos}",
    )


def test_moderation_failure_does_not_block_next_job():
    print("\n### callback شکست moderation worker را نگه نمی‌دارد")

    async def scenario():
        logger = Logger()
        queue = ModerationQueue(logger)
        order = []
        hold = asyncio.Event()

        async def failing():
            raise RuntimeError("ban failed")

        async def slow_failure(_error):
            order.append("fail_start")
            await hold.wait()
            order.append("fail_end")

        async def second():
            order.append("second")
            return True

        queue.enqueue(-9, "ban", failing, user_id=1, on_failure=slow_failure)
        await asyncio.sleep(0.05)
        queue.enqueue(-9, "mute", second, user_id=2)
        await asyncio.sleep(0.1)
        second_ran_while_callback_held = "second" in order and "fail_end" not in order
        hold.set()
        await asyncio.sleep(0.05)
        return second_ran_while_callback_held, order

    ran, order = asyncio.run(scenario())
    check("شغل بعدی پشت callback شکست نماند", ran, f"-> {order}")


def test_bot_detector_does_not_reread_file():
    print("\n### is_disabled فایل را روی هر پیام نمی‌خواند")
    path = ROOT / "config" / "_test_bot_disabled_hotpath.json"
    original = bot_detector._FILE
    bot_detector._FILE = path
    bot_detector._DISABLED = None
    bot_detector._DISABLED_MTIME = None
    try:
        if path.exists():
            path.unlink()
        check("missing file is not disabled", bot_detector.is_disabled(-1) is False)
        bot_detector.disable_for_bot(-77, SimpleNamespace(id=1, username="b"))
        check("save updates cache", bot_detector.is_disabled(-77) is True)
        cached = bot_detector._DISABLED
        bot_detector.is_disabled(-77)
        bot_detector.is_disabled(-1)
        check("repeated is_disabled keeps same cache object",
              bot_detector._DISABLED is cached)
        check("other id stays false", bot_detector.is_disabled(-1) is False)
    finally:
        bot_detector._FILE = original
        bot_detector._DISABLED = None
        bot_detector._DISABLED_MTIME = None
        if path.exists():
            path.unlink()


def test_known_commands_skip_heavy_inspect():
    print("\n### دستور مدیریتی inspect سنگین ندارد")
    for text in ("بن", "قفل", "پاک", "سکوت", "فعال", "ثبت گروه"):
        check(f"{text!r} is management", _is_management_command(text) is True)
    for text in ("ربات", "بازی‌ها", "سلام", "سخنگو"):
        check(f"{text!r} is not management", _is_management_command(text) is False)
    check("ربات is an internal command", "ربات" in _INTERNAL_EXACT_COMMANDS)
    check("بازی‌ها is command lane not management",
          classify_priority("بازی‌ها")[1] == "command")
    check("ربات is command lane", classify_priority("ربات")[1] == "command")


def test_admin_lane_still_classifies():
    print("\n### lane ادمین برای بن/قفل حفظ شده")
    for text in ("بن", "قفل", "پاک 10"):
        priority, kind = classify_priority(text)
        check(f"{text!r} admin", priority == PRIORITY_ADMIN and kind == "admin",
              f"-> {priority} {kind}")


if __name__ == "__main__":
    test_delete_queue_uses_small_batches()
    test_delete_queue_logs_wait_time()
    test_moderation_failure_does_not_block_next_job()
    test_bot_detector_does_not_reread_file()
    test_known_commands_skip_heavy_inspect()
    test_admin_lane_still_classifies()
    print(f"\n{PASSED} passed, {FAILED} failed")
    raise SystemExit(1 if FAILED else 0)
