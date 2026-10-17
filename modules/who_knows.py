# -*- coding: utf-8 -*-
"""🧠 بازی «کی بیشتر بلده؟»

هر مرحله فقط یک سؤال دارد: یک «حرف» تصادفی + یک «دسته» تصادفی
(حیوان، شهر، میوه، شیء، شخصیت، غذا، کشور). اولین پاسخ درست هر مرحله
🥉 ۲ سکه برنز می‌گیرد و مرحلهٔ بعدی خودکار شروع می‌شود. زمان هر مرحله
۲۵ ثانیه است؛ اگر تمام شود مرحله بسته می‌شود. وقتی همهٔ حروف مصرف
شدند بازی تمام است.

state با کلید chat_id نگه داشته می‌شود (مثل اسم فامیل). پاسخ هر کاربر
فقط برای خودش حساب می‌شود؛ جواب درست به نام همان فرستنده ثبت می‌شود.
اعتبارسنجی پاسخ آفلاین است: بانک داخلی + دیتابیس/آموخته‌های اسم فامیل
برای دسته‌های مشترک. هیچ وابستگی به وب ندارد.
"""
import random
import time

GAME = "who_knows"
COMMAND = "کی بیشتر بلده"
ANSWER_SECONDS = 25
REWARD = 2

END_MESSAGE = (
    "🧠 بازی کی بیشتر بلده تمام شد!\n\n"
    "تمام حروف استفاده شدند.\n"
    "برای بازی‌های دیگر از لیست بازی استفاده کنید."
)

_RANDOM = random.SystemRandom()
_ACTIVE = {}      # chat_id -> {token, letter, category, deadline}
_REMAINING = {}   # chat_id -> [حروف باقی‌مانده]

_AR_MAP = {"ي": "ی", "ك": "ک", "أ": "ا", "إ": "ا", "آ": "ا", "ؤ": "و", "ة": "ه", "ٔ": ""}


def _normalize(value):
    text = str(value or "").strip().lower()
    for src, dst in _AR_MAP.items():
        text = text.replace(src, dst)
    text = text.replace("‌", " ")
    return " ".join(text.split())


# ---------------------------------------------------------------------------
# بانک داخلی کلمات — کلید: دسته، مقدار: کلمات معتبر.
# ---------------------------------------------------------------------------
CATEGORY_BANK = {
    "حیوان": [
        "اسب", "اردک", "الاغ", "بز", "ببر", "بوفالو", "پلنگ", "پروانه", "پنگوئن",
        "تمساح", "جغد", "خرگوش", "خرس", "خروس", "دلفین", "روباه", "زنبور", "زرافه",
        "سگ", "سنجاب", "شیر", "شتر", "طوطی", "عقاب", "فیل", "قناری", "کلاغ",
        "کبوتر", "گرگ", "گاو", "گوسفند", "لاک پشت", "مار", "موش", "میمون", "نهنگ",
        "همستر", "هدهد", "یوزپلنگ", "کوسه", "قورباغه", "عنکبوت", "غاز", "چیتا",
        "صدف", "ماهی", "آهو", "اختاپوس",
    ],
    "شهر": [
        "اصفهان", "اهواز", "اراک", "ارومیه", "اردبیل", "بوشهر", "بجنورد", "بم",
        "پاریس", "پکن", "تهران", "تبریز", "توکیو", "جهرم", "چابهار", "خرم اباد",
        "دزفول", "دبی", "رشت", "زاهدان", "زنجان", "ساری", "سمنان", "سنندج",
        "شیراز", "شهرکرد", "طبس", "قم", "قزوین", "کرمان", "کرج", "کاشان",
        "گرگان", "لندن", "مشهد", "مادرید", "نیشابور", "نیویورک", "همدان",
        "هرات", "یزد", "یاسوج", "ونیز", "وین", "غزه", "عسلویه", "فردوس",
    ],
    "میوه": [
        "انار", "انگور", "اناناس", "البالو", "انجیر", "به", "پرتقال", "پسته",
        "توت", "توت فرنگی", "خربزه", "خرما", "زردالو", "زغال اخته", "سیب",
        "شلیل", "شاه توت", "طالبی", "الو", "گیلاس", "گلابی", "لیمو", "موز",
        "نارنگی", "نارگیل", "هندوانه", "هلو", "کیوی", "گریپ فروت", "ازگیل",
        "بلوبری", "تمشک", "چاقاله بادام", "لیچی", "پاپایا",
    ],
    "شیء": [
        "اتو", "اینه", "بشقاب", "بالش", "پتو", "پنکه", "پالت", "تلویزیون",
        "تلفن", "جارو", "چاقو", "چراغ", "چتر", "خودکار", "دفتر", "دوربین",
        "رادیو", "زنجیر", "ساعت", "سماور", "شانه", "صندلی", "عینک", "غربال",
        "فرش", "قاشق", "قوری", "کتاب", "کیف", "گلدان", "لیوان", "مداد",
        "میز", "نردبان", "هاون", "یخچال", "کامپیوتر", "موبایل", "تبلت",
        "چمدان", "پرده", "ظرف", "ذره بین", "سشوار",
    ],
    "شخصیت": [
        "اردشیر", "ابوعلی سینا", "بابک خرمدین", "پروین اعتصامی", "پاستور",
        "تختی", "حافظ", "خیام", "داروین", "رودکی", "زکریای رازی", "سعدی",
        "سهراب سپهری", "شجریان", "شکسپیر", "صادق هدایت", "عطار", "فردوسی",
        "قیصر امین پور", "کوروش", "گاندی", "مولوی", "مصدق", "نیوتن",
        "نیما یوشیج", "هوشنگ ابتهاج", "یعقوب لیث", "انیشتین", "ادیسون",
        "تسلا", "مسی", "رونالدو", "علی دایی", "مریم میرزاخانی",
    ],
    "غذا": [
        "ابگوشت", "اش رشته", "املت", "باقالی پلو", "بریانی", "پیتزا",
        "ته چین", "جوجه کباب", "چلوکباب", "حلیم", "خورش قیمه", "خورش کرفس",
        "دلمه", "دیزی", "زرشک پلو", "ساندویچ", "سوپ", "شله زرد", "شویدپلو",
        "عدس پلو", "غذای دریایی", "فسنجان", "قورمه سبزی", "قیمه", "کوکو سبزی",
        "کباب کوبیده", "کتلت", "گوجه املت", "لازانیا", "لوبیا پلو",
        "ماکارونی", "میرزاقاسمی", "نرگسی", "همبرگر", "هویج پلو", "یتیمچه",
        "سوشی", "پاستا", "فلافل", "طاس کباب", "ژیگو", "خوراک زبان",
    ],
    "کشور": [
        "ایران", "امریکا", "المان", "امارات", "اسپانیا", "ایتالیا", "اتریش",
        "برزیل", "بلژیک", "پرتغال", "پاکستان", "ترکیه", "تایلند", "ژاپن",
        "چین", "دانمارک", "روسیه", "رومانی", "سوئد", "سوریه", "شیلی",
        "عراق", "عمان", "غنا", "فرانسه", "فنلاند", "قطر", "قزاقستان",
        "کانادا", "کره جنوبی", "گرجستان", "لبنان", "لهستان", "مصر",
        "مکزیک", "مالزی", "نروژ", "هند", "هلند", "یونان", "یمن", "ونزوئلا",
        "اندونزی", "صربستان", "تونس", "مراکش",
    ],
}

# برچسب نمایشی هر دسته در متن سؤال.
CATEGORY_LABEL = {
    "حیوان": "حیوان", "شهر": "شهر", "میوه": "میوه", "شیء": "شیء",
    "شخصیت": "شخصیت", "غذا": "غذا", "کشور": "کشور",
}

# دسته‌های مشترک با اسم فامیل برای اعتبارسنجی بیشتر (دیتابیس + آموخته‌ها).
_NAME_FAMILY_CATEGORY = {"حیوان": "حیوان", "شهر": "شهر", "میوه": "میوه", "شیء": "وسیله"}

# پوشش حروف: برای هر دسته، حرف اولِ نرمال‌شدهٔ کلمات بانک.
_NORMALIZED_BANK = {
    category: {_normalize(word) for word in words}
    for category, words in CATEGORY_BANK.items()
}
_LETTER_CATEGORIES = {}
for _category, _words in _NORMALIZED_BANK.items():
    for _word in _words:
        if _word:
            _LETTER_CATEGORIES.setdefault(_word[0], set()).add(_category)

PLAYABLE_LETTERS = tuple(sorted(_LETTER_CATEGORIES))


def is_active(chat_id):
    return chat_id in _ACTIVE


def current(chat_id):
    state = _ACTIVE.get(chat_id)
    return dict(state) if state else None


def letters_finished(chat_id):
    remaining = _REMAINING.get(chat_id)
    return remaining is not None and not remaining


def start(chat_id):
    """مرحلهٔ جدید. خروجی dict مرحله، «finished» یا None اگر فعال است."""
    if chat_id in _ACTIVE:
        return None
    remaining = _REMAINING.get(chat_id)
    if remaining is None:
        remaining = list(PLAYABLE_LETTERS)
        _RANDOM.shuffle(remaining)
        _REMAINING[chat_id] = remaining
    if not remaining:
        # همهٔ حروف مصرف شده؛ برای دور بعدی دوباره پر می‌شود.
        _REMAINING.pop(chat_id, None)
        return "finished"
    letter = remaining.pop()
    category = _RANDOM.choice(sorted(_LETTER_CATEGORIES[letter]))
    state = {
        "token": f"{int(time.time() * 1000)}",
        "letter": letter,
        "category": category,
        "deadline": time.monotonic() + ANSWER_SECONDS,
    }
    _ACTIVE[chat_id] = state
    return dict(state)


def _name_family_valid(category, letter, answer):
    """اعتبارسنجی تکمیلی با دیتابیس/آموخته‌های اسم فامیل (آفلاین)."""
    mapped = _NAME_FAMILY_CATEGORY.get(category)
    if not mapped:
        return False
    try:
        from modules import name_family as nf
        if nf._validate_answer(mapped, letter, answer):
            return True
        learned = nf.LEARNED_NORMALIZED.get(mapped) or set()
        normalized = nf._normalize(answer)
        return bool(normalized and normalized in learned
                    and normalized.startswith(nf._normalize(letter)))
    except Exception:
        return False


def check_word(category, letter, answer):
    """آیا پاسخ، واقعی و متناسب با حرف/دسته است؟"""
    normalized = _normalize(answer)
    if not normalized or len(normalized) < 2:
        return False
    if not normalized.startswith(_normalize(letter)):
        return False
    if normalized in _NORMALIZED_BANK.get(category, set()):
        return True
    return _name_family_valid(category, letter, answer)


def answer(chat_id, user_id, text):
    """بررسی پاسخ. خروجی dict مرحله در پاسخ درست، وگرنه None.

    فقط پیام خود فرستنده برای خودش حساب می‌شود؛ جایزه به همان user_id
    پرداخت می‌شود که پیام را فرستاده است.
    """
    state = _ACTIVE.get(chat_id)
    if not state:
        return None
    # ⚠️ چک مهلت عمداً حذف شد: تا وقتی تایمر مرحله را نبسته، پاسخِ
    # رسیده پذیرفته می‌شود (در گروه شلوغ پیام ممکن است چند ثانیه در
    # صف مانده باشد؛ جوابِ به‌موقعِ کاربر نباید بی‌صدا رد شود).
    # پاسخ باید کوتاه باشد (یک مورد)، نه جملهٔ بلند یا چندخطی.
    raw = str(text or "").strip()
    if not raw or "\n" in raw or len(raw) > 40:
        return None
    if not check_word(state["category"], state["letter"], raw):
        return None
    _ACTIVE.pop(chat_id, None)
    result = dict(state)
    result["user_id"] = user_id
    result["answer"] = raw
    return result


def timeout(chat_id, token):
    """بستن مرحله پس از پایان زمان؛ فقط اگر همان مرحله هنوز باز باشد."""
    state = _ACTIVE.get(chat_id)
    if not state or state["token"] != token:
        return None
    _ACTIVE.pop(chat_id, None)
    return dict(state)


def cancel(chat_id):
    _ACTIVE.pop(chat_id, None)


def reset_all(chat_id=None):
    if chat_id is None:
        _ACTIVE.clear()
        _REMAINING.clear()
    else:
        _ACTIVE.pop(chat_id, None)
        _REMAINING.pop(chat_id, None)
