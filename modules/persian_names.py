"""دیتابیس جامع نام‌های فارسی برای اعتبارسنجی «ثبت اسم» و «شخصیت».

پیش از این هر دو مسیر روی یک لیست ۷۷ تایی دست‌نویس تکیه می‌کردند و هر نام
واقعیِ خارج از آن لیست «نامعتبر» اعلام می‌شد. اینجا نام‌ها از یک دیتاست
ترکیبی (۲۴٬۶۶۳ نام دخترانه و پسرانه) خوانده می‌شوند تا حدس‌زدن حذف شود.

منابع دادهٔ ``data/persian_names.json``:

* https://github.com/nabidam/persian-names  (۸۸۱۶ نام، پاکیزه)
* https://github.com/nikahd99/iranian-Names-Database-By-Gender (۲۰ هزار، فیلترشده)

دیتاست یک بار و به‌صورت تنبل (lazy) بارگذاری و در حافظه cache می‌شود.
"""
import json
import re
import unicodedata
from pathlib import Path

_DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "persian_names.json"

_NAMES = None
_META = None

# نویسه‌های عربی/کنترلی که باید به شکل فارسی استاندارد تبدیل شوند.
_CHAR_MAP = {
    "\u200c": " ",   # نیم‌فاصله
    "\u200b": "",
    "\u200e": "",
    "\u200f": "",
    "\ufeff": "",
    "\u064a": "\u06cc",  # ي عربی
    "\u0649": "\u06cc",  # ى
    "\u0643": "\u06a9",  # ك عربی
    "\u0629": "\u0647",  # ة
    "\u0623": "\u0627",  # أ
    "\u0625": "\u0627",  # إ
    "\u0622": "\u0627",  # آ  (فقط برای کلید جستجو)
}

# اعراب و تشدید در کلید جستجو نادیده گرفته می‌شوند.
_DIACRITICS = re.compile(r"[\u064b-\u0652\u0670]")

_PERSIAN_TEXT = re.compile(r"^[آ-یءأإؤئۀة\s]+$")
_LATIN_TEXT = re.compile(r"^[A-Za-z][A-Za-z\s'-]*$")


def normalize(value):
    """کلید یکسان‌شدهٔ نام برای جستجو در دیتابیس."""
    text = str(value or "").strip()
    if not text:
        return ""
    for source, target in _CHAR_MAP.items():
        text = text.replace(source, target)
    text = _DIACRITICS.sub("", text)
    text = unicodedata.normalize("NFC", text)
    return " ".join(text.split()).lower()


def _load():
    global _NAMES, _META
    if _NAMES is not None:
        return _NAMES
    data = {}
    meta = {}
    try:
        raw = json.loads(_DATA_FILE.read_text(encoding="utf-8"))
        meta = raw.get("_meta", {})
        for name, gender in (raw.get("names") or {}).items():
            key = normalize(name)
            if key:
                data[key] = gender
    except (OSError, ValueError):
        # نبودِ فایل نباید ربات را از کار بیندازد؛ فقط دیتابیس خالی می‌ماند.
        data = {}
    _NAMES = data
    _META = meta
    return _NAMES


def count():
    return len(_load())


def meta():
    _load()
    return dict(_META or {})


def is_known_name(value):
    """آیا این نام در دیتابیس وجود دارد (تک‌واژه یا چندواژه)."""
    key = normalize(value)
    if not key:
        return False
    return key in _load()


def gender_of(value):
    """'M' / 'F' / 'B' یا None اگر نام ناشناس باشد."""
    return _load().get(normalize(value))


def lookup(value):
    """نام را می‌یابد و (نام_نرمال، جنسیت) برمی‌گرداند، یا (None, None)."""
    key = normalize(value)
    if not key:
        return None, None
    gender = _load().get(key)
    if gender is not None:
        return key, gender
    return None, None


def contains_known_token(value):
    """آیا حداقل یکی از واژه‌های ورودی یک نام شناخته‌شده است.

    برای ورودی‌هایی مثل «علی رضایی» که واژهٔ دوم نام خانوادگی است.
    """
    key = normalize(value)
    if not key:
        return False
    database = _load()
    if key in database:
        return True
    return any(token in database for token in key.split() if token)


def first_known_token(value):
    """اولین واژه‌ای که نام شناخته‌شده است را برمی‌گرداند."""
    key = normalize(value)
    if not key:
        return None
    database = _load()
    if key in database:
        return key
    for token in key.split():
        if token in database:
            return token
    return None


def is_persian_text(value):
    return bool(_PERSIAN_TEXT.fullmatch(str(value or "").strip()))


def is_latin_text(value):
    return bool(_LATIN_TEXT.fullmatch(str(value or "").strip()))
