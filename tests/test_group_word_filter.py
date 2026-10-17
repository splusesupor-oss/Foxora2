"""Word-level matching for per-group filtered words only.

    python tests/test_group_word_filter.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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


def matched(text, words):
    return find_matching_filter_word(text, words)


def test_pi_standalone_only():
    print("\n### «پی» فقط به‌صورت کلمه مستقل")
    words = ["پی"]
    for text in (
        "پی",
        "پی بیا",
        "سلام پی",
        "پی پر از فیلم",
        "پی!",
        "(پی)",
        "پی، خوبی؟",
        "  پی  ",
        "هر کس میخواد مدیر گپ بشه بیاد پی فقط دختر",
    ):
        check(f"MATCH {text!r}", matched(text, words) == "پی",
              f"-> {matched(text, words)!r}")
    for text in (
        "پیشش بودم",
        "پیام",
        "پیام داد",
        "پیرمرد",
        "پیچ",
        "پیش",
        "ناپیدا",
    ):
        check(f"NO MATCH {text!r}", matched(text, words) is None,
              f"-> {matched(text, words)!r}")


def test_punctuation_and_latin():
    print("\n### علائم و لاتین")
    check("BUY مستقل", matched("please BUY now", ["buy"]) == "buy")
    check("buyer نه", matched("buyer paid", ["buy"]) is None)
    check("نقل‌قول", matched("گفت «پی» و رفت", ["پی"]) == "پی")


def test_multiword_phrase():
    print("\n### عبارت چندکلمه‌ای")
    words = ["فلان کلمه"]
    check("عبارت مستقل", matched("سلام فلان کلمه بگو", words) == "فلان کلمه")
    check("پسوند چسبیده بدون نیم‌فاصله نه",
          matched("سلام فلان کلمهای", words) is None)
    check("بدون فاصله نه", matched("سلام فلانکلمه بگو", words) is None)
    check("نیم‌فاصله مثل فاصله است",
          matched("سلام فلان کلمه‌ای", words) == "فلان کلمه")


def test_normalization_preserved():
    print("\n### نرمال‌سازی ی/ك و فاصله")
    check("ي عربی", matched("اين پي است", ["پی"]) == "پی")
    check("ك عربی نرمال می‌شود", matched("پيك", ["پیک"]) == "پیک")
    check("پی داخل پیک نه", matched("سلام پیک", ["پی"]) is None)
    check("نیم‌فاصله جداکننده است", matched("پی‌وی", ["پی"]) == "پی")


def test_real_words_still_match():
    print("\n### کلمات واقعی فیلتر همچنان match می‌شوند")
    check("تلگرام مستقل", matched("برو تلگرام ببین", ["تلگرام"]) == "تلگرام")
    check("بیو مستقل", matched("بیو چک کن", ["بیو"]) == "بیو")
    check("بیوگرافی با فیلتر گروه بیو نه", matched("بیوگرافی من", ["بیو"]) is None)


def main():
    test_pi_standalone_only()
    test_punctuation_and_latin()
    test_multiword_phrase()
    test_normalization_preserved()
    test_real_words_still_match()
    print(f"\npassed={PASSED} failed={FAILED}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
