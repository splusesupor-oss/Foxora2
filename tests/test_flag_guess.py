"""بررسی تصادفی‌بودن و عدم تکرار در بازی «حدس پرچم».

    python tests/test_flag_guess.py
"""
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import modules.flag_guess as fg

PASSED = FAILED = 0


def check(label, cond, detail=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  PASS  {label}")
    else:
        FAILED += 1
        print(f"  FAIL  {label} {detail}")


def play(chat_id, rounds, user_id=None):
    """rounds دور کامل بازی و برگرداندن ترتیب پاسخ‌ها."""
    if user_id is None:
        user_id = chat_id
    out = []
    for _ in range(rounds):
        game = fg.start(chat_id, user_id)
        assert game is not None, "start returned None while flags remained"
        out.append(game["answer"])
        fg.finish(chat_id, game["token"])
    return out


def test_catalogue():
    print("\n### فهرست پرچم‌ها")
    total = len(fg.COUNTRIES)
    check(f"دست‌کم ۱۰۰ کشور موجود است ({total})", total >= 100, f"-> {total}")
    names = [c[1] for c in fg.COUNTRIES]
    flags = [c[0] for c in fg.COUNTRIES]
    check("هیچ نام تکراری نیست",
          len(names) == len(set(names)),
          f"-> {[n for n, c in Counter(names).items() if c > 1]}")
    check("هیچ ایموجی پرچم تکراری نیست",
          len(flags) == len(set(flags)),
          f"-> {[f for f, c in Counter(flags).items() if c > 1]}")
    check("همهٔ ورودی‌ها ساختار سه‌تایی دارند",
          all(len(c) == 3 and c[0] and c[1] for c in fg.COUNTRIES))
    check("همهٔ پرچم‌ها ایموجی منطقه‌ای هستند",
          all(len(c[0]) == 2 and all(0x1F1E6 <= ord(ch) <= 0x1F1FF for ch in c[0])
              for c in fg.COUNTRIES))
    # پوشش قاره‌ها: کشورهای معروف و کمتر شناخته‌شده هر دو
    for country in ("ایران", "آمریکا", "برزیل", "ژاپن"):
        check(f"کشور معروف موجود است: {country}", country in names)
    for country in ("لتونی", "نامیبیا", "برونئی", "پاراگوئه"):
        check(f"کشور کمتر شناخته‌شده موجود است: {country}", country in names)


def test_no_immediate_repeat():
    print("\n### پرچم قبلی بلافاصله تکرار نمی‌شود")
    fg.reset_history()
    seq = play(-100001, 40)
    adjacent = [a for a, b in zip(seq, seq[1:]) if a == b]
    check("هیچ پرچمی دو بار پشت سر هم نیامد", not adjacent, f"-> {adjacent[:3]}")


def test_no_repeat_within_cycle():
    print("\n### در یک دور کامل هیچ تکراری رخ نمی‌دهد")
    fg.reset_history()
    total = len(fg.COUNTRIES)
    seq = play(-100002, total)
    dupes = [n for n, c in Counter(seq).items() if c > 1]
    check(f"در {total} دور هیچ پرچمی تکرار نشد", not dupes, f"-> {dupes[:5]}")
    check("همهٔ پرچم‌ها دقیقاً یک بار استفاده شدند",
          len(set(seq)) == total, f"-> {len(set(seq))}/{total}")


def test_first_ten_are_distinct():
    print("\n### حداقل ۱۰ پرچم متفاوت در هر چرخه")
    fg.reset_history()
    seq = play(-100003, 10)
    check("۱۰ دور اول همگی متفاوت‌اند", len(set(seq)) == 10, f"-> {len(set(seq))}")


def test_exhaustion_locks_the_user():
    """پس از دیدن همهٔ پرچم‌ها، بازی برای همان کاربر بسته می‌شود."""
    print("\n### اتمام پرچم‌ها و قفل شدن بازی")
    fg.reset_history()
    total = len(fg.COUNTRIES)
    chat, user = -100700, 7001

    check("در ابتدا کاربر محدود نیست", not fg.is_exhausted(user))
    check(f"در ابتدا {total} پرچم باقی است",
          fg.remaining_count(user) == total, f"-> {fg.remaining_count(user)}")

    seq = play(chat, total, user)
    check(f"همهٔ {total} پرچم یکتا نمایش داده شد",
          len(set(seq)) == total, f"-> {len(set(seq))}")
    check("کاربر اکنون محدود است", fg.is_exhausted(user))
    check("هیچ پرچمی باقی نمانده", fg.remaining_count(user) == 0,
          f"-> {fg.remaining_count(user)}")

    check("start دیگر پرچم نمی‌دهد", fg.start(chat, user) is None)
    check("هیچ بازی فعالی ساخته نشد", not fg.is_active(chat))
    for _ in range(5):
        check("تلاش‌های بعدی هم رد می‌شوند", fg.start(chat, user) is None)
        break


def test_exhausted_user_earns_nothing():
    """کاربر محدودشده نباید بتواند امتیاز بگیرد."""
    print("\n### کاربر محدودشده امتیاز نمی‌گیرد")
    fg.reset_history()
    chat, locked, fresh = -100701, 7002, 7003
    play(chat, len(fg.COUNTRIES), locked)
    check("کاربر محدود شد", fg.is_exhausted(locked))

    # بازی‌ای که کاربر دیگری شروع کرده
    game = fg.start(chat, fresh)
    check("کاربر آزاد توانست بازی بگیرد", game is not None)
    check("پاسخ درستِ کاربر محدودشده پذیرفته نمی‌شود",
          fg.answer(chat, game["answer"], locked) is None)
    check("بازی هنوز فعال است", fg.is_active(chat))
    check("پاسخ درستِ کاربر آزاد پذیرفته می‌شود",
          fg.answer(chat, game["answer"], fresh) == game["answer"])


def test_lock_is_per_user_only():
    """محدودیت فقط برای همان کاربر است."""
    print("\n### محدودیت فقط برای همان کاربر")
    fg.reset_history()
    chat, locked, other = -100702, 7004, 7005
    play(chat, len(fg.COUNTRIES), locked)
    check("کاربر اول محدود شد", fg.is_exhausted(locked))
    check("کاربر دوم محدود نیست", not fg.is_exhausted(other))
    game = fg.start(chat, other)
    check("کاربر دوم در همان چت پرچم می‌گیرد", game is not None)
    check("کاربر دوم تاریخچهٔ خودش را دارد", fg.seen_count(other) == 1,
          f"-> {fg.seen_count(other)}")
    check("تاریخچهٔ کاربر اول دست‌نخورده است",
          fg.seen_count(locked) == len(fg.COUNTRIES))
    fg.finish(chat, game["token"])


def test_exhausted_message_text():
    print("\n### متن پیام پایان بازی")
    msg = fg.EXHAUSTED_MESSAGE
    check("پیام تکمیل بازی است",
          "✅ شما این بازی را تکمیل کردید." in msg, f"{msg}")
    check("راهنمای استفاده از بازی‌های دیگر",
          "لطفاً از بازی‌های دیگر استفاده کنید" in msg, f"{msg}")


def test_multiple_chats_independent():
    print("\n### چند کاربر/گروه همزمان")
    fg.reset_history()
    chats = [-100100 - i for i in range(5)]
    # هر چت ۱۰ دور بازی می‌کند؛ تاریخچه‌ها نباید روی هم اثر بگذارند.
    results = {c: play(c, 10) for c in chats}
    for chat, seq in results.items():
        check(f"چت {chat}: هر ۱۰ پرچم متفاوت است",
              len(set(seq)) == 10, f"-> {len(set(seq))}")
    firsts = [seq[0] for seq in results.values()]
    check("شروع بازی برای چت‌های مختلف یکسان نیست",
          len(set(firsts)) >= 3, f"-> {firsts}")
    check("هر کاربر تاریخچهٔ مستقل دارد",
          all(fg.seen_count(c) == 10 for c in chats),
          f"-> {[fg.seen_count(c) for c in chats]}")


def test_concurrent_games_isolated():
    print("\n### بازی همزمان در چند چت بدون تداخل")
    fg.reset_history()
    a, b = -100301, -100302
    game_a = fg.start(a)
    game_b = fg.start(b)
    check("هر دو بازی فعال‌اند", fg.is_active(a) and fg.is_active(b))
    check("پاسخ درست چت a فقط بازی a را می‌بندد",
          fg.answer(a, game_a["answer"]) == game_a["answer"])
    check("بازی چت b هنوز فعال است", fg.is_active(b))
    check("بازی چت a بسته شد", not fg.is_active(a))
    fg.finish(b, game_b["token"])


def test_randomness_across_restarts():
    print("\n### تصادفی‌بودن پس از ری‌استارت (تاریخچه خالی)")
    firsts = []
    for _ in range(30):
        fg.reset_history()
        game = fg.start(-100400)
        firsts.append(game["answer"])
        fg.finish(-100400, game["token"])
    unique = len(set(firsts))
    check(f"شروع بازی در ۳۰ ری‌استارت متنوع است ({unique} کشور یکتا)",
          unique >= 20, f"-> {unique}")
    top = Counter(firsts).most_common(1)[0]
    check("هیچ کشوری بر شروع بازی غالب نیست", top[1] <= 4, f"-> {top}")


def test_answer_matching():
    print("\n### پذیرش پاسخ")
    fg.reset_history()
    game = fg.start(-100500)
    check("پاسخ غلط رد می‌شود", fg.answer(-100500, "کشور نامعتبر") is None)
    check("پاسخ درست پذیرفته می‌شود",
          fg.answer(-100500, game["answer"]) == game["answer"])
    # نام مستعار انگلیسی
    fg.reset_history()
    target = next(c for c in fg.COUNTRIES if c[1] == "ایران")
    fg._ACTIVE[-100501] = {"flag": target[0], "answer": target[1],
                           "aliases": target[2], "token": 0}
    check("نام مستعار انگلیسی پذیرفته می‌شود",
          fg.answer(-100501, "Iran") == "ایران")
    # نویسه‌های عربی
    fg._ACTIVE[-100502] = {"flag": target[0], "answer": target[1],
                           "aliases": target[2], "token": 0}
    check("نویسهٔ عربی ي نرمال می‌شود", fg.answer(-100502, "ايران") == "ایران")


def test_double_start_guard():
    print("\n### شروع دوباره در حین بازی فعال")
    fg.reset_history()
    first = fg.start(-100600)
    second = fg.start(-100600)
    check("بازی دوم شروع نمی‌شود", second is None, f"-> {second}")
    check("بازی اول دست‌نخورده است",
          fg.is_active(-100600) and fg._ACTIVE[-100600]["answer"] == first["answer"])
    fg.finish(-100600, first["token"])


def test_progress_persists_across_restart():
    """تاریخچهٔ کاربر باید بعد از «ری‌استارت» (بارگذاری دوباره از فایل) حفظ شود."""
    import importlib
    print("\n### ماندگاری تاریخچه بعد از ری‌استارت")
    # پاک‌سازی و یک کاربر که چند پرچم دیده
    fg.reset_history()
    chat, user = -100800, 8001
    g1 = fg.start(chat, user)
    assert g1 is not None
    first = g1["answer"]
    fg.finish(chat, g1["token"])
    seen_before = fg.seen_count(user)
    check("کاربر حداقل یک پرچم دیده", seen_before >= 1, f"{seen_before}")

    # شبیه‌سازی ری‌استارت: state حافظه پاک و از لایهٔ ماندگار برمی‌گردد.
    if fg._PROGRESS_STORE.sqlite:
        # backend مسیر خصوصی (مانند استقرار واقعی Termux/Foxora): تاریخچه در
        # runtime_kv دیتابیس SQLite است؛ flush دسته‌ای لازم است.
        fg._PROGRESS_STORE.flush()
        from modules import runtime_db
        row = runtime_db.kv_get("flag_guess_progress", fg._user_key(user), None)
        check("تاریخچهٔ کاربر در دیتابیس ثبت شده", bool(row), f"row={row!r}")
        fg._SEEN_HISTORY.clear()
        check("بعد از بارگذاری، تاریخچه برگشت", fg.seen_count(user) == seen_before,
              f"{fg.seen_count(user)} vs {seen_before}")
    else:
        file = fg._PROGRESS_FILE
        check("فایل پیشرفت ساخته شد", file.exists(), str(file))
        data = __import__("json").loads(file.read_text(encoding="utf-8"))
        check("تاریخچهٔ کاربر در فایل ثبت شده", str(user) in data, f"{list(data)[:3]}")
        fg._SEEN_HISTORY.clear()
        fg._load_progress()
        check("بعد از بارگذاری، تاریخچه برگشت", fg.seen_count(user) == seen_before,
              f"{fg.seen_count(user)} vs {seen_before}")
    check("کاربر پرچمِ دیده‌شده را دوباره نمی‌گیرد",
          fg.start(chat, user)["answer"] != first)
    fg.reset_history()




def main():
    test_catalogue()
    test_no_immediate_repeat()
    test_no_repeat_within_cycle()
    test_first_ten_are_distinct()
    test_exhaustion_locks_the_user()
    test_exhausted_user_earns_nothing()
    test_lock_is_per_user_only()
    test_exhausted_message_text()
    test_progress_persists_across_restart()
    test_multiple_chats_independent()
    test_concurrent_games_isolated()
    test_randomness_across_restarts()
    test_answer_matching()
    test_double_start_guard()

    print(f"\n{'=' * 52}")
    print(f"passed={PASSED} failed={FAILED}")
    print("=" * 52)
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())


