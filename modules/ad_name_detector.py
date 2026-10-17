"""تشخیص مستقل نام‌های تبلیغاتی؛ جدا از فیلتر متن گروه."""
import re

from modules.user_display import format_user

_TERMS = (
    r"بیو\s*چک", r"چک\s*بیو", r"بیوگرافی\s*چک", r"بیومو\s*(?:چک|ببینید|ببین)",
    r"بیو.*(?:فیلم|لینک|چک|ببین)",
    r"فیلم",
    r"حال\s*پی",
    r"تمام\s*سانسور",
    r"حال\s*می(?:د|ذ)م",
    r"فیلم\s*پی",
    r"🔞",
    r"پی\s*وی",
    r"پیوی",
    r"\bpv\b",
    r"خاله",
    r"صیغه",
    r"رایگان",
    r"سکس",
    r"سکسی",
    r"پورن",
    r"نود",
    r"فیلتر\s*شکن",
    r"فیلترشکن",
    r"\bvpn\b",
    r"شارژ\s*رایگان",
    r"کانال",
    r"پکیج",
    r"ارز\s*دیجیتال",
    r"تتر",
    r"پهلوی",
    r"شاهزاده",
    r"شاه\s*زاده",
    r"پرچم\s*آمریکا",
    r"دلباخته\s*پهلوی",
    r"رضا\s*شاه",
    r"رضاشاه",
    r"محمدرضا\s*شاه",
    r"جان\s*فدای\s*میهن",
    r"جانفدای\s*میهن",
    r"فرزند\s*ایران",
)
_PATTERNS = tuple(re.compile(p, re.IGNORECASE) for p in _TERMS)


def _norm(value):
    if not value:
        return ""
    value = str(value).lower().replace("ي", "ی").replace("ك", "ک").replace("_", " ")
    # حذف «کشیده» (ـ tatweel) و علائم حرکات
    value = re.sub(r"[\u0640\u064b-\u065f]", "", value)
    # تبدیل نیم‌فاصله، نشانه‌های جهت، فاصله‌های خاص، ایموجی‌ها، علائم نگارشی و نمادها به فاصله
    value = re.sub(r"[\u200c\u200d\u200f\u200e\ufeff\u00a0\-_.,/\\;:!؟،؛|()\[\]{}<>+=*&^%$#@~\"\'`«»…]+", " ", value)
    return " ".join(value.split())


def _collapse(value):
    """جمع کردن حروف تکراری: «بیوچکک» و «بییییو چک» → «بیوچک» و «بیو چک»."""
    return re.sub(r"(.)\1+", r"\1", value)


def display_name(user):
    return format_user(user)


def reason(user):
    username = _norm(getattr(user, "username", None))
    first = getattr(user, "first_name", None) or ""
    last = getattr(user, "last_name", None) or ""
    name = _norm(f"{first} {last}".strip())
    for value in (username, name):
        if not value:
            continue
        # هم متن عادی و هم نسخهٔ بدون حروف تکراری بررسی می‌شود تا
        # نوشتار کشیده (بیوچکک، بیــو چک، بییییو چک) هم گرفته شود.
        for candidate in (value, _collapse(value)):
            for pattern in _PATTERNS:
                if pattern.search(candidate):
                    return pattern.pattern
    return None
