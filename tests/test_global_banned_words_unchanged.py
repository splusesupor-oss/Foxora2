"""Prove SpamDetector.check_banned_words is untouched and still works.

    python tests/test_global_banned_words_unchanged.py
"""
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.spam_detector import SpamDetector

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


def detector(words):
    return SpamDetector(_FakeConfig(words))


def test_source_still_word_boundary():
    print("\n### منبع check_banned_words مستقل از فیلتر گروه است")
    source = inspect.getsource(SpamDetector.check_banned_words)
    refresh = inspect.getsource(SpamDetector._refresh_banned_word_patterns)
    fold = inspect.getsource(SpamDetector._fold_banned_letters)
    fuzzy = inspect.getsource(SpamDetector._banned_word_fuzzy_body)
    check("از group_words_storage استفاده نمی‌کند", "group_words" not in source)
    check("از find_matching_filter_word استفاده نمی‌کند",
          "find_matching_filter_word" not in source + refresh)
    check("مرز کلمه با lookbehind حروف است",
          r"(?<![" in refresh or "_BANNED_LETTER_CLASS" in refresh)
    check("نرمال‌سازی ي/ك باقی است", '"ي"' in fold and '"ک"' in fold)
    check("فاصلهٔ مبهم بین حروف را می‌پذیرد", "_BANNED_SEP_OPT" in fuzzy)


def test_bio_still_detected():
    print("\n### «بیو» سراسری مثل قبل کار می‌کند")
    det = detector(["بیو"])
    hit, reason = det.check_banned_words("بیو")
    check("بیو مستقل", hit and "بیو" in (reason or ""))
    hit, reason = det.check_banned_words("این بیو منه")
    check("بیو داخل جمله", hit and "بیو" in (reason or ""))
    hit, _reason = det.check_banned_words("بیوگرافی")
    check("بیوگرافی مثل قبل match نمی‌شود", hit is False)
    is_spam, spam_reason = det.is_spam("بیو")
    check("is_spam هم بیو را می‌گیرد", is_spam and "کلمه ممنوعه" in spam_reason)


def test_pi_global_still_detected():
    print("\n### «پی» سراسری مثل قبل کار می‌کند")
    det = detector(["پی"])
    phrase = "هر کس میخواد مدیر گپ بشه بیاد پی فقط دختر"
    hit, reason = det.check_banned_words(phrase)
    check("جمله تبلیغاتی با پی مستقل", hit and "پی" in (reason or ""))
    hit, reason = det.check_banned_words("پی پر از فیلم")
    check("پی پر از فیلم", hit and "پی" in (reason or ""))
    hit, _reason = det.check_banned_words("پیام داد")
    check("پیام مثل قبل match نمی‌شود", hit is False)
    hit, _reason = det.check_banned_words("پیشش بودم")
    check("پیشش مثل قبل match نمی‌شود", hit is False)


def test_independent_of_group_filter():
    print("\n### سیستم سراسری به فیلتر گروه وابسته نیست")
    from modules import group_words_storage as storage
    det = detector(["بیو"])
    check("توابع جدا هستند",
          storage.find_matching_filter_word is not det.check_banned_words)
    check("بدون لیست گروه هم بیو سراسری کار می‌کند",
          det.check_banned_words("بیو")[0] is True)
    check("فیلتر گروه خالی روی سراسری اثر ندارد",
          storage.find_matching_filter_word("بیو", []) is None
          and det.check_banned_words("بیو")[0] is True)


def main():
    test_source_still_word_boundary()
    test_bio_still_detected()
    test_pi_global_still_detected()
    test_independent_of_group_filter()
    print(f"\npassed={PASSED} failed={FAILED}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
