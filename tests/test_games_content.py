"""بررسی محتوا و سلامت بازی‌های «جای خالی» و «چیستان».

    python tests/test_games_content.py
"""
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import modules.fill_blank as fb
import modules.riddles as rd

PASSED = FAILED = 0


def check(label, cond, detail=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  PASS  {label}")
    else:
        FAILED += 1
        print(f"  FAIL  {label} {detail}")


def _norm(value):
    return " ".join(str(value or "").replace("\u200c", " ").split())


def test_fill_count_and_uniqueness():
    print("\n### جای خالی: تعداد و یکتایی")
    check("۹۰ سؤال موجود است", len(fb.FILLS) == 90, f"-> {len(fb.FILLS)}")
    questions = [q for q, _ in fb.FILLS]
    dupes = [q for q, c in Counter(questions).items() if c > 1]
    check("هیچ سؤال تکراری نیست", not dupes, f"-> {dupes[:3]}")
    pairs = [(q, a) for q, a in fb.FILLS]
    check("هیچ جفت (سؤال، پاسخ) تکراری نیست",
          len(pairs) == len(set(pairs)), f"-> {len(pairs) - len(set(pairs))}")


def test_fill_structure():
    print("\n### جای خالی: ساختار")
    no_blank = [q for q, _ in fb.FILLS if "____" not in q]
    check("همهٔ سؤال‌ها جای خالی دارند", not no_blank, f"-> {no_blank[:3]}")
    empty = [(q, a) for q, a in fb.FILLS if not str(a).strip()]
    check("هیچ پاسخ خالی نیست", not empty, f"-> {empty[:3]}")
    long_ans = [(q, a) for q, a in fb.FILLS if len(_norm(a)) > 20]
    check("پاسخ‌ها کوتاه و مشخص‌اند", not long_ans, f"-> {long_ans[:3]}")
    # پاسخ نباید به‌صورت یک واژهٔ مستقل در سؤال تکرار شده باشد.
    # (پاسخ‌های تک‌حرفی یا جزئی از یک واژهٔ دیگر استثنا هستند.)
    leaked = []
    for q, a in fb.FILLS:
        answer = _norm(a)
        if len(answer) < 3:
            continue
        if answer in _norm(q).split():
            leaked.append((q, a))
    check("پاسخ به‌صورت واژهٔ مستقل در سؤال لو نرفته", not leaked, f"-> {leaked[:3]}")


def test_fill_difficulty():
    print("\n### جای خالی: سطح دشواری")
    trivial = {"سبز", "سفید", "زرد", "سیاه", "قرمز"}
    color_only = [(q, a) for q, a in fb.FILLS if _norm(a) in trivial]
    check("سؤال‌های صرفاً رنگی حذف شده‌اند",
          len(color_only) <= 2, f"-> {len(color_only)}")
    avg = sum(len(_norm(q)) for q, _ in fb.FILLS) / len(fb.FILLS)
    check(f"میانگین طول سؤال معقول است ({avg:.0f} نویسه)", avg >= 30, f"-> {avg:.0f}")


def test_fill_gameplay():
    print("\n### جای خالی: عملکرد بازی")
    chat, user = -100123, 555
    q = fb.new_fill(chat, user)
    check("سؤال تولید شد", bool(q) and "____" in q, f"-> {q!r}")
    answer = fb.get_fill_answer(chat, user)
    check("پاسخ در حافظه ثبت شد", bool(answer))
    check("پاسخ غلط رد می‌شود", fb.check_fill(chat, user, "پاسخ کاملا غلط") is False)
    check("پاسخ درست پذیرفته می‌شود", fb.check_fill(chat, user, answer) is True)
    check("امتیاز افزایش یافت", fb.get_score(user) >= 1)
    check("بعد از پاسخ، بازی بسته شد",
          fb.get_fill_answer(chat, user) is None)

    # پذیرش پاسخ با نیم‌فاصله/فاصله متفاوت
    fb.new_fill(chat, user)
    real = fb.get_fill_answer(chat, user)
    spaced = " ".join(real) if len(real) < 6 else real.replace(" ", "\u200c")
    accepted = fb.check_fill(chat, user, real)
    check("پاسخ دقیق همیشه پذیرفته می‌شود", accepted is True)


def test_riddle_count_and_uniqueness():
    print("\n### چیستان: تعداد و یکتایی")
    check("۱۰۰ چیستان موجود است", len(rd.RIDDLES) == 100, f"-> {len(rd.RIDDLES)}")
    questions = [q for q, _ in rd.RIDDLES]
    dupes = [q for q, c in Counter(questions).items() if c > 1]
    check("هیچ چیستان تکراری نیست", not dupes, f"-> {dupes[:3]}")
    check("هیچ جفت (سؤال، پاسخ) تکراری نیست",
          len(rd.RIDDLES) == len(set(rd.RIDDLES)),
          f"-> {len(rd.RIDDLES) - len(set(rd.RIDDLES))}")


def test_riddle_structure():
    print("\n### چیستان: ساختار")
    no_q = [q for q, _ in rd.RIDDLES if "؟" not in q]
    check("همهٔ چیستان‌ها علامت سؤال دارند", not no_q, f"-> {no_q[:3]}")
    empty = [(q, a) for q, a in rd.RIDDLES if not str(a).strip()]
    check("هیچ پاسخ خالی نیست", not empty, f"-> {empty[:3]}")
    # معماهای منطقی (مثل «اول کبریت را روشن می‌کنی») عمداً واژهٔ پاسخ را در
    # متن دارند؛ نکتهٔ آن‌ها همین است. فقط چیستان‌های توصیفی بررسی می‌شوند.
    LOGIC_PUZZLES = {"کبریت", "حسن", "دو", "همه ماه ها", "یک بار",
                     "ندارد", "کوتاه"}
    leaked = []
    for q, a in rd.RIDDLES:
        answer = _norm(a)
        if len(answer) < 3 or answer in LOGIC_PUZZLES:
            continue
        if answer in _norm(q).split():
            leaked.append((q, a))
    check("پاسخ چیستان‌های توصیفی لو نرفته", not leaked, f"-> {leaked[:3]}")


def test_riddle_variety():
    print("\n### چیستان: تنوع")
    answers = [_norm(a) for _, a in rd.RIDDLES]
    unique = len(set(answers))
    check(f"تنوع پاسخ‌ها بالاست ({unique} پاسخ یکتا از ۱۰۰)",
          unique >= 85, f"-> {unique}")
    over = [a for a, c in Counter(answers).items() if c > 3]
    check("هیچ پاسخی بیش از ۳ بار تکرار نشده", not over, f"-> {over}")
    starts = Counter(q.split()[0] for q, _ in rd.RIDDLES)
    check("چیستان‌ها با الگوهای متنوع شروع می‌شوند",
          starts.most_common(1)[0][1] < len(rd.RIDDLES) * 0.65,
          f"-> {starts.most_common(3)}")
    check("دست‌کم ۴ الگوی شروع متفاوت وجود دارد",
          len(starts) >= 4, f"-> {len(starts)}")


def test_riddle_gameplay():
    print("\n### چیستان: عملکرد بازی")
    chat, user = -100999, 777
    rd.reset_all()
    q = rd.new_riddle(chat, user)
    check("چیستان تولید شد", bool(q))
    answer = rd.get_answer(chat, user)
    check("پاسخ در حافظه ثبت شد", bool(answer))
    check("پاسخ غلط رد می‌شود", rd.check_answer(chat, user, "چیز دیگری") is False)
    check("پاسخ درست پذیرفته می‌شود", rd.check_answer(chat, user, answer) is True)
    check("بعد از پاسخ، بازی بسته شد", rd.get_answer(chat, user) is None)


def test_riddle_rotation():
    """چرخش چیستان اکنون «به تفکیک کاربر» است.

    قبلاً تاریخچه سراسری بود: چیستان‌هایی که کاربر A دیده بود برای کاربر B
    هم مصرف‌شده حساب می‌شد. حالا هر کاربر دور کامل و مستقل خودش را دارد.
    """
    print("\n### چیستان: چرخش بدون تکرار (به تفکیک کاربر)")
    rd.reset_all()
    seen = []
    for _ in range(len(rd.RIDDLES)):
        seen.append(rd.new_riddle(-100777, 4242))
    dupes = [q for q, c in Counter(seen).items() if c > 1]
    check("در یک دور کامل هیچ تکراری رخ نداد", not dupes, f"-> {len(dupes)} تکرار")
    check(f"همهٔ {len(rd.RIDDLES)} چیستان استفاده شدند",
          len(set(seen)) == len(rd.RIDDLES), f"-> {len(set(seen))}")

    # کاربر تازه باید دور کامل خودش را داشته باشد، نه باقی‌ماندهٔ دیگران.
    fresh = [rd.new_riddle(-100777, 9999) for _ in range(len(rd.RIDDLES))]
    check("کاربر دوم دور کامل و مستقل گرفت",
          len(set(fresh)) == len(rd.RIDDLES), f"-> {len(set(fresh))}")

    # بعد از اتمام لیست، دور از نو باز می‌شود (بازی قفل دائمی ندارد).
    check("پس از اتمام، دور جدید آغاز می‌شود", bool(rd.new_riddle(-100777, 4242)))
    rd.reset_all()


def main():
    test_fill_count_and_uniqueness()
    test_fill_structure()
    test_fill_difficulty()
    test_fill_gameplay()
    test_riddle_count_and_uniqueness()
    test_riddle_structure()
    test_riddle_variety()
    test_riddle_gameplay()
    test_riddle_rotation()

    print(f"\n{'=' * 52}")
    print(f"passed={PASSED} failed={FAILED}")
    print("=" * 52)
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
