"""GLOBAL_FORBIDDEN_WORDS must stay on when a group filter is turned off.

    python tests/test_global_vs_group_word_isolation.py
"""
import inspect
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.spam_detector import SpamDetector
from modules import group_banned_words_control as group_switch
from modules.group_words_storage import find_matching_filter_word

PASSED = FAILED = 0


def check(label, cond, detail=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  PASS  {label}")
    else:
        FAILED += 1
        print(f"  FAIL  {label} {detail}")


class _FakeConfig:
    def __init__(self, words):
        self.banned_words = set(words)
        self._banned_words_version = 1

    def get(self, key, default=None):
        if key == "check_banned_words":
            return True
        return default

    def reload_if_needed(self):
        pass


def test_global_ignores_group_switch():
    print("\n### لیست سراسری به سوئیچ گروه وابسته نیست")
    source = inspect.getsource(SpamDetector.check_banned_words)
    module_src = inspect.getsource(SpamDetector)
    check("check_banned_words دیگر is_enabled ندارد", "is_enabled" not in source)
    check("spam_detector سوئیچ گروه را import نمی‌کند",
          "group_banned_words_control" not in module_src)

    det = SpamDetector(_FakeConfig(["بیو", "سکس"]))
    disabled_chat = 9429374  # stored as false in group_banned_words.json
    hit, reason = det.check_banned_words("بیو چک کن", disabled_chat)
    check("گروه خاموش‌شده هم بیو سراسری را می‌گیرد",
          hit and "بیو" in (reason or ""))
    is_spam, spam_reason = det.is_spam("سکس", disabled_chat)
    check("is_spam هم با گروه خاموش‌شده کار می‌کند",
          is_spam and "سکس" in spam_reason)
    unknown_chat = 999999999
    hit, _reason = det.check_banned_words("بیو", unknown_chat)
    check("گروه تازه بدون هیچ تنظیم اولیه هم سراسری را دارد", hit is True)


def test_group_switch_only_affects_custom_filter():
    print("\n### سوئیچ گروه فقط فیلتر سفارشی را عوض می‌کند")
    original_file = group_switch.FILE
    original_cache = group_switch._cache
    original_mtime = group_switch._cache_mtime
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "group_banned_words.json"
            path.write_text("{}", encoding="utf-8")
            group_switch.FILE = str(path)
            group_switch._cache = None
            group_switch._cache_mtime = None

            new_chat = 888001
            check("گروه جدید پیش‌فرض روشن است",
                  group_switch.is_enabled(new_chat) is True)
            group_switch.disable(new_chat)
            check("disable فقط همان گروه را خاموش می‌کند",
                  group_switch.is_enabled(new_chat) is False)
            saved = json.loads(path.read_text(encoding="utf-8"))
            check("فایل سوئیچ فقط وضعیت گروه را نگه می‌دارد",
                  saved.get(str(new_chat)) is False)

            custom_words = ["رل پی"]
            check("فیلتر سفارشی هنوز مستقل match می‌کند",
                  find_matching_filter_word("بیا رل پی", custom_words) == "رل پی")

            det = SpamDetector(_FakeConfig(["بیو"]))
            hit, _reason = det.check_banned_words("بیو", new_chat)
            check("خاموش کردن فیلتر گروه، سراسری را خاموش نمی‌کند", hit is True)
    finally:
        group_switch.FILE = original_file
        group_switch._cache = original_cache
        group_switch._cache_mtime = original_mtime


def test_handler_gates_only_group_filter():
    print("\n### هندلر فقط فیلتر گروه را پشت سوئیچ می‌گذارد")
    handler_src = (ROOT / "handlers" / "message_handler.py").read_text(
        encoding="utf-8"
    )
    check("هندلر سوئیچ را برای فیلتر گروه می‌خواند",
          "group_custom_filter_enabled" in handler_src)
    check("فیلتر گروه پشت if سوئیچ است",
          "if group_custom_filter_enabled(chat_id):" in handler_src)
    check("is_spam سراسری همچنان با chat_id صدا زده می‌شود",
          "bot.detector.is_spam(message_text, chat_id)" in handler_src)


def main():
    test_global_ignores_group_switch()
    test_group_switch_only_affects_custom_filter()
    test_handler_gates_only_group_filter()
    print(f"\npassed={PASSED} failed={FAILED}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
