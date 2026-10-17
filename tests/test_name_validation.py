"""اعتبارسنجی نام روی دیتابیس جامع، نه لیست دست‌نویس.

هر دو مسیر «ثبت اسم» و «شخصیت» با صدها نام واقعی دخترانه و پسرانه آزموده
می‌شوند تا مطمئن شویم دیگر نام واقعی رد نمی‌شود.

    python tests/test_name_validation.py
"""
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import persian_names
from modules.group_memory import extract_name
from modules.name_insights import report

PASSED = FAILED = 0


def check(label, cond, detail=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  PASS  {label}")
    else:
        FAILED += 1
        print(f"  FAIL  {label} {detail}")


# نام‌های واقعی که پیش از این رد می‌شدند (خارج از لیست ۷۷تایی قدیمی).
GIRL_NAMES = [
    "آیناز", "پرنیان", "ثمین", "یگانه", "سوگند", "کیانا", "هلیا", "رومینا",
    "دلارام", "نیایش", "آناهیتا", "شکیبا", "غزل", "الناز", "مبینا", "ستایش",
    "پریسا", "مینا", "سحر", "بهار", "یلدا", "آتنا", "درسا", "ویدا", "شیدا",
    "نازیلا", "فرناز", "گلناز", "مهرناز", "شقایق", "بنفشه", "یاسمین", "نیکو",
    "آرزو", "افسانه", "الهام", "الهه", "پگاه", "تارا", "ترمه", "حدیث",
    "خاطره", "راضیه", "رعنا", "زیبا", "سمانه", "سوده", "شهرزاد", "صبا",
    "طاهره", "عسل", "فرزانه", "کوثر", "لادن", "مرجان", "منیره", "مهتاب",
    "نسترن", "نوشین", "هدیه", "ویولت", "کتایون", "پوران", "زرین", "سیما",
    "شهناز", "فرشته", "لاله", "محدثه", "مژگان", "نازنین", "نگین", "هما",
]

BOY_NAMES = [
    "بردیا", "آرتین", "رادین", "ماهان", "کوروش", "اردشیر", "بابک", "بهزاد",
    "پژمان", "تیرداد", "جمشید", "خشایار", "داریوش", "رستم", "زانیار", "سهراب",
    "شاهرخ", "فرامرز", "قباد", "کامبیز", "کاوه", "کیومرث", "منوچهر", "نریمان",
    "هوشنگ", "یاشار", "آرش", "ابوالفضل", "احسان", "ارسلان", "اسفندیار",
    "افشین", "امین", "بهمن", "پدرام", "پرویز", "پیمان", "توحید", "جواد",
    "حامد", "حسن", "خسرو", "رامتین", "روزبه", "سالار", "سیاوش", "شایان",
    "شهاب", "صادق", "طاها", "عرفان", "فرزین", "فربد", "قاسم", "کسری",
    "کیوان", "مازیار", "متین", "مسعود", "مهران", "میثم", "نیما", "وحید",
    "هادی", "هومن", "یاسین", "یحیی", "بنیامین", "امیرمحمد",
]

COMPOUND = ["محمد رضا", "امیر حسین", "علی رضا", "سید علی", "محمد مهدی"]

INVALID = ["کیرم", "جنده", "حرومزاده", "", "  ", "x", "۱۲۳۴", "!!!", "؟؟"]


def test_database_loaded():
    print("\n### دیتابیس نام‌ها")
    n = persian_names.count()
    check(f"دیتابیس بارگذاری شد ({n} نام)", n > 20000, f"-> {n}")
    m = persian_names.meta()
    check("متادیتای منابع موجود است", bool(m.get("sources")), f"-> {m}")


def test_girl_names():
    print(f"\n### {len(GIRL_NAMES)} نام دخترانه")
    bad = []
    for n in GIRL_NAMES:
        name, err = extract_name(n)
        if err or name != n:
            bad.append((n, err))
    check(f"همهٔ {len(GIRL_NAMES)} نام دخترانه پذیرفته شدند", not bad,
          f"-> رد شده: {bad[:8]}")


def test_boy_names():
    print(f"\n### {len(BOY_NAMES)} نام پسرانه")
    bad = []
    for n in BOY_NAMES:
        name, err = extract_name(n)
        if err or name != n:
            bad.append((n, err))
    check(f"همهٔ {len(BOY_NAMES)} نام پسرانه پذیرفته شدند", not bad,
          f"-> رد شده: {bad[:8]}")


def test_random_sample_from_database():
    """نمونهٔ تصادفی بزرگ از خودِ دیتابیس باید پذیرفته شود."""
    print("\n### نمونهٔ تصادفی ۵۰۰ نام از دیتابیس")
    db = persian_names._load()
    single = [n for n in db if " " not in n and 2 <= len(n) <= 18]
    sample = random.Random(1234).sample(single, min(500, len(single)))
    bad = []
    for n in sample:
        _name, err = extract_name(n)
        if err:
            bad.append((n, err))
    rate = 100 * (len(sample) - len(bad)) / len(sample)
    check(f"نرخ پذیرش ≥ ۹۹٪ (واقعی: {rate:.1f}%)", rate >= 99.0,
          f"-> رد شده: {bad[:10]}")


def test_compound_names():
    print("\n### نام‌های مرکب")
    for n in COMPOUND:
        name, err = extract_name(n)
        check(f"{n!r} پذیرفته شد", err is None, f"-> {err}")


def test_invalid_rejected():
    print("\n### ورودی نامعتبر باید رد شود")
    for n in INVALID:
        name, err = extract_name(n)
        check(f"{n!r} رد شد", err is not None, f"-> {name!r}")


def test_sentence_extraction():
    print("\n### استخراج نام از جمله")
    cases = [("اسم من علی هست", "علی"), ("من زهرا هستم", "زهرا")]
    for text, expected in cases:
        name, err = extract_name(text)
        check(f"{text!r} -> {expected!r}", name == expected, f"-> {name!r} err={err}")


def test_gender_lookup():
    print("\n### تشخیص جنسیت")
    for n, g in (("زهرا", "F"), ("علی", "M"), ("مریم", "F"), ("حسین", "M")):
        check(f"{n} -> {g}", persian_names.gender_of(n) == g,
              f"-> {persian_names.gender_of(n)}")


def test_personality_uses_database():
    print("\n### شخصیت‌شناسی از دیتابیس استفاده می‌کند")
    for n in ["آیناز", "بردیا", "سوگند", "رادین", "پرنیان"]:
        r = report(n)
        check(f"{n}: گزارش تولید شد", bool(r))
        if r:
            check(f"{n}: «نامشخص» اعلام نشد",
                  "ریشه نام: نامشخص" not in r,
                  "-> هنوز ناشناس گزارش می‌شود")


def test_personality_curated_still_wins():
    print("\n### رکوردهای تفصیلی دست‌نویس اولویت دارند")
    r = report("علی")
    check("«علی» ریشهٔ عربی تفصیلی دارد", r and "عربی" in r)


def test_personality_unknown_is_honest():
    print("\n### نام ناموجود صادقانه ناشناس اعلام می‌شود")
    r = report("ژکلمنپ")
    check("گزارش تولید شد", bool(r))
    check("به‌عنوان نامشخص علامت خورد", r and "نامشخص" in r)


def test_normalization():
    print("\n### یکسان‌سازی نویسه‌ها")
    check("ي عربی", persian_names.is_known_name("علي"))
    check("ك عربی", persian_names.is_known_name("كوروش"))
    check("فاصلهٔ اضافه", persian_names.is_known_name("  زهرا  "))


def main():
    test_database_loaded()
    test_girl_names()
    test_boy_names()
    test_random_sample_from_database()
    test_compound_names()
    test_invalid_rejected()
    test_sentence_extraction()
    test_gender_lookup()
    test_personality_uses_database()
    test_personality_curated_still_wins()
    test_personality_unknown_is_honest()
    test_normalization()

    print(f"\n{'=' * 52}")
    print(f"passed={PASSED} failed={FAILED}")
    print("=" * 52)
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
