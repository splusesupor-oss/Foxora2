"""A scheduled reply must not keep the same-lane worker busy.

    python tests/test_worker_not_blocked_by_reply.py
"""
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.group_dispatch import PRIORITY_NORMAL, GroupDispatcher
from modules.performance import MessagePerformance


def _schedule_reply(bot, event, *args, **kwargs):
    async def run():
        try:
            await event.reply(*args, **kwargs)
        except Exception as error:
            logger = getattr(bot, "logger", None)
            if logger is not None:
                logger.log_error(f"SCHEDULED REPLY FAILED error={error!r}")
    return asyncio.create_task(run())

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
        self.infos.append(message)

    def log_error(self, message):
        self.errors.append(message)


class FakeEvent:
    def __init__(self, delay_s):
        self.delay_s = delay_s
        self.replies = []

    async def reply(self, text, **kwargs):
        await asyncio.sleep(self.delay_s)
        self.replies.append(text)
        return True


def test_scheduled_reply_does_not_block_next_job():
    print("\n### reply سه‌ثانیه‌ای worker را نگه نمی‌دارد")

    async def scenario():
        logger = Logger()
        dispatcher = GroupDispatcher(logger=logger)
        event = FakeEvent(0.30)
        started = []

        async def first():
            _schedule_reply(type("B", (), {"logger": logger})(), event, "سلام")
            started.append(("first_done", time.perf_counter()))

        async def second():
            started.append(("second_start", time.perf_counter()))

        t0 = time.perf_counter()
        dispatcher.submit(-1, first, priority=PRIORITY_NORMAL, kind="normal")
        await asyncio.sleep(0)
        dispatcher.submit(-1, second, priority=PRIORITY_NORMAL, kind="normal")
        await dispatcher.join(timeout=2)
        await asyncio.sleep(0.35)
        return started, event.replies, (time.perf_counter() - t0) * 1000

    marks, replies, wall = asyncio.run(scenario())
    names = [row[0] for row in marks]
    gap = None
    if len(marks) >= 2:
        gap = (marks[1][1] - marks[0][1]) * 1000
    print(f"    order={names} gap_ms={gap} replies={replies} wall={wall:.1f}")
    check("هر دو شغل تمام شدند", names == ["first_done", "second_start"], f"-> {names}")
    check("شغل دوم بلافاصله شروع شد (نه بعد از 300ms reply)",
          gap is not None and gap < 50, f"-> {gap}")
    check("reply در پس‌زمینه ارسال شد", replies == ["سلام"], f"-> {replies}")


def test_profiler_skip_to_isolates_admin_check():
    print("\n### ADMIN_CHECK دیگر مسیر routing را قورت نمی‌دهد")
    profiler = MessagePerformance()
    time.sleep(0.05)
    profiler.mark("COMMAND_MATCH")
    time.sleep(0.08)
    profiler.skip_to()
    time.sleep(0.01)
    profiler.mark("ADMIN_CHECK")
    check("COMMAND_MATCH حدود 50ms است نه 130ms",
          30 <= profiler.values["COMMAND_MATCH"] <= 80,
          f"-> {profiler.values['COMMAND_MATCH']:.1f}")
    check("ADMIN_CHECK فقط بازهٔ کوتاه بعد از skip است",
          5 <= profiler.values["ADMIN_CHECK"] <= 40,
          f"-> {profiler.values['ADMIN_CHECK']:.1f}")


def test_group_memory_cache_avoids_reread(tmp_path=None):
    print("\n### کش حافظه گروه دیسک را برای هر پیام نمی‌خواند")
    import modules.group_memory as memory
    original_file = memory.FILE
    original_cache = memory._cache
    original_mtime = memory._cache_mtime
    try:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "group_memory.json"
            path.write_text('{"1":{"2":{"name":"علی"}}}', encoding="utf-8")
            memory.FILE = path
            memory._cache = None
            memory._cache_mtime = None
            reads = {"n": 0}
            real_read = Path.read_text

            def counting_read(self, *args, **kwargs):
                if self == path:
                    reads["n"] += 1
                return real_read(self, *args, **kwargs)

            Path.read_text = counting_read
            try:
                first = memory.get_name(1, 2)
                second = memory.get_name(1, 2)
                third = memory.get_name(1, 2)
            finally:
                Path.read_text = real_read
            check("نام درست خوانده شد", first == second == third == "علی")
            check("فقط یک بار از دیسک خواند", reads["n"] == 1, f"-> {reads['n']}")
    finally:
        memory.FILE = original_file
        memory._cache = original_cache
        memory._cache_mtime = original_mtime


def test_handler_wires_the_fix():
    print("\n### هندلر reply ساده را دیگر await نمی‌کند")
    src = (ROOT / "handlers" / "message_handler.py").read_text(encoding="utf-8")
    check("هلپر _schedule_reply تعریف شده", "def _schedule_reply(" in src)
    check("سلام/پاسخ ثابت با schedule ارسال می‌شود",
          "_schedule_reply(bot, event, simple_reply)" in src)
    check("ADMIN_CHECK با skip_to جدا شده",
          "profiler.skip_to()" in src and "profiler.mark(\"ADMIN_CHECK\")" in src)
    check("FILTER با skip_to از بازی‌ها جدا شده",
          'profiler.mark("FILTER")' in src)
    check("fox games فقط وقتی فعال/دستور است صدا می‌شود",
          "clean_text in FOX_GAME_COMMANDS or fox_game_active(chat_id)" in src)


def main():
    test_scheduled_reply_does_not_block_next_job()
    test_profiler_skip_to_isolates_admin_check()
    test_group_memory_cache_avoids_reread()
    test_handler_wires_the_fix()
    print(f"\npassed={PASSED} failed={FAILED}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
