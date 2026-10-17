"""حدس ایموجی: تاریخچهٔ هر کاربر و جلوگیری از معمای تکراری.

    python tests/test_emoji_guess.py
"""
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ⚠️ پیش از import هر بازی اجرا می‌شود: خودِ economy هنگام import مسیر
# config/economy.json واقعی را می‌بندد و نوشتن‌های بعدی همان‌جا می‌نشیند.
import tempfile as _tempfile
import economy.storage as _storage
_storage.use_file(Path(_tempfile.mkdtemp()) / "economy.json")


import modules.emoji_guess as eg


PASSED = FAILED = 0
CHAT = -100321


def check(label, cond, detail=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  PASS  {label}")
    else:
        FAILED += 1
        print(f"  FAIL  {label} {detail}")


def play(chat_id, user_id, rounds):
    """rounds بار بازی و برگرداندن پاسخ‌های دیده‌شده."""
    seen = []
    for _ in range(rounds):
        puzzle = eg.start(chat_id, user_id)
        if puzzle is None:
            break
        seen.append(puzzle["answer"])
        eg.finish(chat_id, puzzle["token"])
    return seen


def test_catalogue():
    print("\n### بانک معماها")
    total = len(eg.PUZZLES)
    check(f"بانک دست‌کم ۴۰۰ مرحله دارد ({total})", total >= 400, f"-> {total}")
    answers = [item[1] for item in eg.PUZZLES]
    check("هیچ پاسخ تکراری در بانک نیست",
          len(answers) == len(set(answers)),
          f"-> {[a for a, c in Counter(answers).items() if c > 1]}")
    emojis = [item[0] for item in eg.PUZZLES]
    check("هیچ ترکیب ایموجی تکراری نیست", len(emojis) == len(set(emojis)))
    check("همهٔ ورودی‌ها ایموجی و پاسخ دارند",
          all(item[0] and item[1] for item in eg.PUZZLES))
    check("هر رکورد سه بخش دارد", all(len(item) == 3 for item in eg.PUZZLES))
    check("دست‌کم شش سطح سختی وجود دارد", len(eg.TIERS) >= 6)
    check("مجموع سطوح برابر کل بانک است",
          sum(len(tier) for tier in eg.TIERS) == total,
          f"-> {[len(t) for t in eg.TIERS]}")

    def emoji_count(value):
        return sum(
            1 for ch in value
            if ch not in ("\ufe0f", "\u200d")
            and not "\U0001F3FB" <= ch <= "\U0001F3FF"
        )

    counts = [emoji_count(item[0]) for item in eg.PUZZLES]
    check("هر معما بین ۲ تا ۴ ایموجی دارد",
          all(2 <= n <= 4 for n in counts),
          f"-> {[e for e, n in zip(emojis, counts) if not 2 <= n <= 4][:3]}")
    check("هیچ معمای تک‌ایموجی نیست", min(counts) >= 2)

    check("پیام اتمام تعریف شده است", bool(eg.EXHAUSTED_MESSAGE))
    check("متن پیام اتمام دقیقاً مطابق خواسته است",
          eg.EXHAUSTED_MESSAGE == (
              "✅ تمام مراحل حدس ایموجی را انجام داده‌اید. "
              "به‌زودی مراحل جدید اضافه می‌شود."
          ),
          f"-> {eg.EXHAUSTED_MESSAGE}")


def test_no_repeat_for_one_user():
    """هستهٔ باگ: یک کاربر هرگز نباید معمای تکراری بگیرد."""
    print("\n### هیچ معمای تکراری برای یک کاربر")
    eg.reset_all()
    total = len(eg.PUZZLES)
    seen = play(CHAT, 500, total)
    duplicates = [a for a, c in Counter(seen).items() if c > 1]
    check(f"{total} بازی پیاپی: هیچ تکراری", not duplicates,
          f"-> {duplicates[:5]}")
    check("همهٔ معماها دقیقاً یک بار آمدند",
          len(set(seen)) == total, f"-> {len(set(seen))}/{total}")


def test_hundreds_of_attempts():
    """صدها اجرا: داخل هر دور تکراری نباشد و بازی قفل نشود.

    پس از مصرف شدن بانک، دور تازه‌ای ساخته می‌شود؛ پس تکرار *بین* دورها
    طبیعی است، ولی *داخل* یک دور هرگز نباید رخ دهد.
    """
    print("\n### صدها اجرای پشت‌سرهم")
    eg.reset_all()
    total = len(eg.PUZZLES)
    first = play(CHAT, 501, total)
    duplicates = [a for a, c in Counter(first).items() if c > 1]
    check("دور اول بدون هیچ تکراری", not duplicates, f"-> {duplicates[:5]}")
    check(f"دور اول دقیقاً {total} مرحله دارد",
          len(first) == total, f"-> {len(first)}")

    more = play(CHAT, 501, 1000)
    check("پس از اتمام بازی قفل نمی‌شود", len(more) > 0, f"-> {len(more)}")
    check("دورهای بعدی هم معما می‌دهند",
          all(answer for answer in more))
    eg.finish(CHAT)


def test_exhaustion_counters():
    print("\n### شمارنده‌های تاریخچه")
    eg.reset_all()
    total = len(eg.PUZZLES)
    check("در ابتدا چیزی دیده نشده", eg.seen_count(CHAT, 600) == 0)
    check(f"در ابتدا {total} معما باقی است",
          eg.remaining_count(CHAT, 600) == total, f"-> {eg.remaining_count(CHAT, 600)}")
    check("در ابتدا exhausted نیست", not eg.is_exhausted(CHAT, 600))

    play(CHAT, 600, 5)
    check("پس از ۵ بازی، ۵ معما دیده شده",
          eg.seen_count(CHAT, 600) == 5, f"-> {eg.seen_count(CHAT, 600)}")
    check("باقی‌مانده درست است",
          eg.remaining_count(CHAT, 600) == total - 5, f"-> {eg.remaining_count(CHAT, 600)}")

    # دقیقاً تا انتهای همین دور بازی می‌کنیم (نه بیشتر)، وگرنه دور تازه
    # شروع می‌شود و شمارنده دوباره از صفر بالا می‌رود.
    play(CHAT, 600, total - 5)
    check("پس از اتمام، باقی‌مانده صفر است",
          eg.remaining_count(CHAT, 600) == 0, f"-> {eg.remaining_count(CHAT, 600)}")
    check("اکنون exhausted است", eg.is_exhausted(CHAT, 600))


def test_per_user_isolation():
    print("\n### تاریخچهٔ هر کاربر جداست")
    eg.reset_all()
    total = len(eg.PUZZLES)
    play(CHAT, 700, total)
    check("کاربر اول exhausted شد", eg.is_exhausted(CHAT, 700))
    check("کاربر دوم exhausted نیست", not eg.is_exhausted(CHAT, 701))

    puzzle = eg.start(CHAT, 701)
    check("کاربر دوم معما می‌گیرد", puzzle is not None)
    check("کاربر دوم تاریخچهٔ خودش را دارد",
          eg.seen_count(CHAT, 701) == 1, f"-> {eg.seen_count(CHAT, 701)}")
    check("تاریخچهٔ کاربر اول دست‌نخورده است",
          eg.seen_count(CHAT, 700) == total, f"-> {eg.seen_count(CHAT, 700)}")
    if puzzle:
        eg.finish(CHAT, puzzle["token"])

    # کاربر سوم هم مستقل است
    third = play(CHAT, 702, 10)
    check("کاربر سوم ۱۰ معمای یکتا گرفت",
          len(set(third)) == 10, f"-> {len(set(third))}")


def test_many_users_parallel():
    print("\n### چند کاربر با تاریخچه‌های مستقل")
    eg.reset_all()
    users = [800 + i for i in range(6)]
    results = {uid: play(CHAT, uid, 15) for uid in users}
    for uid, seen in results.items():
        duplicates = [a for a, c in Counter(seen).items() if c > 1]
        check(f"کاربر {uid}: ۱۵ معمای یکتا",
              len(set(seen)) == 15 and not duplicates,
              f"-> {len(set(seen))} dup={duplicates[:3]}")
    check("هر کاربر شمارندهٔ مستقل دارد",
          all(eg.seen_count(CHAT, uid) == 15 for uid in users),
          f"-> {[eg.seen_count(CHAT, u) for u in users]}")


def test_reset_user():
    print("\n### پاک‌سازی تاریخچهٔ یک کاربر")
    eg.reset_all()
    total = len(eg.PUZZLES)
    play(CHAT, 900, total)
    check("کاربر exhausted شد", eg.is_exhausted(CHAT, 900))
    eg.reset_user(CHAT, 900)
    check("پس از reset دوباره می‌تواند بازی کند", not eg.is_exhausted(CHAT, 900))
    check("شمارنده صفر شد", eg.seen_count(CHAT, 900) == 0)
    puzzle = eg.start(CHAT, 900)
    check("معمای تازه دریافت شد", puzzle is not None)
    if puzzle:
        eg.finish(CHAT, puzzle["token"])


def test_answer_and_coins():
    print("\n### پاسخ درست و سیستم امتیاز")
    eg.reset_all()
    puzzle = eg.start(CHAT, 950)
    check("بازی شروع شد", puzzle is not None)
    check("پاسخ غلط رد می‌شود",
          eg.answer(CHAT, 950, "U", "پاسخ کاملا غلط") is None)
    check("بازی هنوز فعال است", eg.is_active(CHAT))
    result = eg.answer(CHAT, 950, "U", puzzle["answer"])
    check("پاسخ درست پذیرفته شد", result == puzzle["answer"], f"-> {result}")
    check("بازی بسته شد", not eg.is_active(CHAT))

    # نام مستعار انگلیسی همچنان کار می‌کند
    eg.reset_all()
    target = next(p for p in eg.PUZZLES if p[1] == "مرد عنکبوتی")
    eg._ACTIVE[eg._session_key(CHAT, 951)] = {
        "emoji": target[0], "answer": target[1],
        "aliases": target[2], "token": 0, "user_id": 951}
    check("نام مستعار فارسی پذیرفته می‌شود",
          eg.answer(CHAT, 951, "U", "اسپایدرمن") == "مرد عنکبوتی")


def test_double_start_guard():
    print("\n### شروع دوباره در حین بازی فعال")
    eg.reset_all()
    first = eg.start(CHAT, 960)
    second = eg.start(CHAT, 960)
    check("بازی دوم شروع نمی‌شود", second is None, f"-> {second}")
    check("بازی اول دست‌نخورده است",
          eg.is_active(CHAT, 960)
          and eg.active_state(CHAT, 960)["answer"] == first["answer"])
    check("تاریخچه فقط یک بار افزایش یافت",
          eg.seen_count(CHAT, 960) == 1, f"-> {eg.seen_count(CHAT, 960)}")
    eg.finish(CHAT, first["token"])


def test_isolation_from_other_games():
    print("\n### استقلال از سایر بازی‌ها")
    import modules.flag_guess as fg
    import modules.riddles as rd

    eg.reset_all()
    fg.reset_history()
    rd.used_riddles.clear()

    puzzle = eg.start(CHAT, 970)
    fg.start(CHAT, 970)
    rd.new_riddle(CHAT, 970)

    check("بازی ایموجی هنوز فعال است", eg.is_active(CHAT))
    check("پاسخ ایموجی دست‌نخورده است",
          eg.active_state(CHAT, 970)["answer"] == puzzle["answer"])
    own = {id(eg._ACTIVE)}
    other = {id(fg._ACTIVE), id(fg._SEEN_HISTORY),
             id(rd.active_riddles), id(rd.used_riddles)}
    check("هیچ ساختار داده‌ای مشترک نیست", not (own & other))

    fg.finish(CHAT)
    check("پس از پایان بازی دیگر، ایموجی سالم است", eg.is_active(CHAT))
    eg.finish(CHAT, puzzle["token"])
    eg.reset_all()
    fg.reset_history()


def main():
    test_catalogue()
    test_no_repeat_for_one_user()
    test_hundreds_of_attempts()
    test_exhaustion_counters()
    test_per_user_isolation()
    test_many_users_parallel()
    test_reset_user()
    test_answer_and_coins()
    test_double_start_guard()
    test_isolation_from_other_games()

    print(f"\n{'=' * 52}")
    print(f"passed={PASSED} failed={FAILED}")
    print("=" * 52)
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
