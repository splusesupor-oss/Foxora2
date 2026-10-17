"""🛡️ فیلتر نام و لقب — تشخیص واژهٔ نامناسب در فارسی و انگلیسی.

این ماژول *فقط* برای نام، لقب و پروفایل استفاده می‌شود و به سیستم اصلی
ضداسپم ربات دست نمی‌زند.

چرا یک لیست ساده کافی نیست: کاربر می‌تواند با فاصله، نقطه، تکرار حرف،
ارقام شبیه‌حرف (leetspeak) یا نویسه‌های نامرئی فیلتر را دور بزند. پس
پیش از مقایسه، متن به یک «شکل فشرده» تبدیل می‌شود:

    ک.ی.ر      → کیر
    k i r      → kir
    f-u-c-k    → fuck
    sh1t       → shit
    کییییر     → کیر

دو نوع پاسخ دارید:
    ``BANNED``     واژهٔ رکیک → پیام «غیرمجاز است»
    ``RESTRICTED`` واژهٔ محدودشده (مثل «پهلوی») → پیام مخصوص خودش
"""
import re
import unicodedata

BANNED = "banned"
RESTRICTED = "restricted"

MESSAGE_BANNED = (
    "این نام یا لقب غیرمجاز است و امکان استفاده از آن وجود ندارد."
)
MESSAGE_RESTRICTED = (
    "شما دیگر نمی‌توانید از این بخش استفاده کنید. این مورد غیرمجاز است."
)

# ---------------------------------------------------------------------------
# واژه‌های محدودشده: ثبت نمی‌شوند و پیام مخصوص خودشان را دارند.
# ---------------------------------------------------------------------------
_RESTRICTED_FA = ("پهلوی",)
_RESTRICTED_EN = ("pahlavi", "pahlevi")

# ---------------------------------------------------------------------------
# واژه‌های رکیک.
#
# «بلند» یعنی واژه‌ای که آن‌قدر مشخص است که اگر داخل واژهٔ دیگری هم بیاید
# باز هم نامناسب است. «کوتاه» یعنی واژه‌ای که ممکن است تکه‌ای از یک نام
# واقعی باشد (مثل «کس» در «کسری»)، پس فقط وقتی رد می‌شود که خودش یک
# واژهٔ مستقل باشد.
# ---------------------------------------------------------------------------
_BANNED_LONG_FA = (
    "کیرم", "کیری", "کیرخور", "کسکش", "کصکش", "کسخل", "کصخل",
    "کسمشنگ", "جنده", "جندهه", "قحبه", "فاحشه", "بیناموس", "بیشرف",
    "حرومزاده", "حرامزاده", "مادرجنده", "مادرقحبه", "خارکسه",
    "خارکصه", "پدرسگ", "پدرصلواتی", "کونی", "کونده", "گاییدم",
    "گایید", "بگا", "لاشی", "عوضی", "اشغال", "کثافت", "آشغال",
    "گوساله", "بیشعور", "بیعقل", "خفهشو", "خفه", "دیوث", "دیوس",
    "جاکش", "قرمساق", "بیپدر", "بیمادر", "نکبت", "کصلیس", "کسلیس",
    "ممهخور", "سیکتیر", "گوهخور", "گوهنخور", "زرنزن", "زربزن",
    "پفیوز", "چاقال", "چاکال", "الاغ", "خرمغز", "بیغیرت",
    "هرزه", "روسپی", "لجن", "کونکش", "ننهجنده",
)
_BANNED_SHORT_FA = (
    "کیر", "کس", "کص", "کون", "گوه", "ریدم", "رید", "شاش",
    "جق", "جقی", "ممه", "سیک", "زنا", "فحش", "احمق", "لعنتی",
    "خایه", "بیخایه",
)

_BANNED_LONG_EN = (
    "fuck", "fucker", "fucking", "motherfucker", "bitch", "bastard",
    "asshole", "dickhead", "whore", "slut", "cunt", "pussy",
    "nigger", "nigga", "faggot", "retard", "wanker", "bollocks",
    "shithead", "jackass", "dumbass", "douchebag", "prick",
    "cocksucker", "twat", "bugger", "arsehole", "shit",
)
# فینگلیش: فحش فارسی که با حروف لاتین نوشته می‌شود.
_BANNED_LONG_FINGLISH = (
    "koskesh", "kooskesh", "kosskesh", "jende", "jendeh",
    "haromzade", "haramzade", "madarjende", "kooni", "kuni",
    "pedarsag", "gaeidam", "gayidam", "bishoor", "bishour",
    "dayoos", "dayous", "jakesh", "kharkose", "kharkoseh",
    "goozo", "koskhol", "kosskhol", "kirkhor", "bisharaf",
    "binamoos", "ghahbe", "lashi", "avazi", "kasafat",
)
_BANNED_SHORT_FINGLISH = (
    "kir", "koon", "kun", "kos", "koss", "gooh", "goh",
    "ridam", "shash", "jagh", "sik",
)

_BANNED_SHORT_EN = (
    "ass", "dick", "cock", "damn", "crap", "piss", "slut", "hoe",
    "wtf", "stfu", "milf",
)

# نویسه‌های نامرئی و کششی که فقط برای دور زدن فیلتر به کار می‌روند.
_INVISIBLE = dict.fromkeys(
    [0x200B, 0x200C, 0x200D, 0x200E, 0x200F, 0x2060, 0xFEFF, 0x0640]
)

# یکسان‌سازی نویسه‌های عربی/فارسی.
_ARABIC_MAP = str.maketrans({
    "ي": "ی", "ك": "ک", "ة": "ه", "ۀ": "ه",
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ؤ": "و", "ئ": "ی",
    "ﻻ": "لا",
})

# ارقام فارسی/عربی → لاتین.
_DIGIT_MAP = {ord(p): str(i) for i, p in enumerate("۰۱۲۳۴۵۶۷۸۹")}
_DIGIT_MAP.update({ord(a): str(i) for i, a in enumerate("٠١٢٣٤٥٦٧٨٩")})

# leetspeak: نویسه‌هایی که جای حرف می‌نشینند.
_LEET_MAP = str.maketrans({
    "0": "o", "1": "i", "3": "e", "5": "s", "7": "t",
    "8": "b", "9": "g", "@": "a", "$": "s", "!": "i", "|": "i",
})
# «4» گاهی جای «a» می‌نشیند و گاهی جای «u» (مثل f4ck). هر دو خوانش
# بررسی می‌شوند تا هیچ‌کدام از قلم نیفتد.
_AMBIGUOUS = {"4": ("a", "u")}

# نقطه‌گذاری عربی/فارسی داخل بازهٔ یونیکد فارسی است، پس باید صریح کنار
# گذاشته شود وگرنه «ک،ی،ر» جداکننده شمرده نمی‌شود.
_ARABIC_PUNCT = "\u060C\u061B\u061F\u066A\u066B\u066C\u06D4\u00AB\u00BB"
# هر چیزی که حرف یا رقم نیست، جداکننده شمرده می‌شود.
_SEPARATORS = re.compile(
    r"[^0-9A-Za-z\u0600-\u06FF]+|[" + _ARABIC_PUNCT + r"]+"
)
_REPEATS = re.compile(r"(.)\1{1,}")


def _strip_marks(value):
    """اعراب و علامت‌های ترکیبی را حذف می‌کند."""
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in decomposed
                   if not unicodedata.combining(ch))


def normalize(value):
    """متن را به شکل قابل‌مقایسه در می‌آورد (با حفظ فاصله‌ها)."""
    text = str(value or "")
    text = text.translate(_INVISIBLE)
    text = text.translate(_DIGIT_MAP)
    text = _strip_marks(text)
    text = text.translate(_ARABIC_MAP)
    text = text.lower()
    return " ".join(text.split())


def _squash(value, ambiguous=None):
    """همهٔ جداکننده‌ها و تکرارها را حذف می‌کند.

    ``"ک.ی.ر"`` و ``"k i r"`` و ``"کییییر"`` همه به یک شکل می‌رسند، پس
    ترفندهای فاصله‌گذاری کار نمی‌کنند. جایگزینی leet *پیش از* حذف
    جداکننده انجام می‌شود، وگرنه «@sshole» نویسهٔ «@» را از دست می‌داد.
    """
    text = normalize(value)
    if ambiguous:
        for source, target in ambiguous.items():
            text = text.replace(source, target)
    text = text.translate(_LEET_MAP)
    text = _SEPARATORS.sub("", text)
    return _REPEATS.sub(r"\1", text)


def _squash_variants(value):
    """همهٔ خوانش‌های ممکن از متن (به‌خاطر نویسه‌های چندمعنا)."""
    variants = {_squash(value)}
    for source, options in _AMBIGUOUS.items():
        if source in normalize(value):
            for option in options:
                variants.add(_squash(value, {source: option}))
    return {variant for variant in variants if variant}


def _tokens(value):
    """واژه‌های متن، هرکدام جداگانه فشرده‌شده."""
    normalized = normalize(value).translate(_LEET_MAP)
    raw = [part for part in _SEPARATORS.split(normalized) if part]
    result = []
    for token in raw:
        squashed = _REPEATS.sub(r"\1", token)
        if squashed:
            result.append(squashed)
    return result


def _prepare(words):
    return tuple(sorted({_squash(word) for word in words if _squash(word)},
                        key=len, reverse=True))


_NAMES_DB = None

_RESTRICTED_SET = _prepare(_RESTRICTED_FA + _RESTRICTED_EN)
_LONG_SET = _prepare(_BANNED_LONG_FA + _BANNED_LONG_EN
                     + _BANNED_LONG_FINGLISH)
_SHORT_SET = _prepare(_BANNED_SHORT_FA + _BANNED_SHORT_EN
                      + _BANNED_SHORT_FINGLISH)


# نام‌های واقعی فینگلیش که تکه‌ای از یک واژهٔ کوتاه را در خود دارند.
# بدون این، «Kosar» (کوثر) قربانی «kos» می‌شد.
_SAFE_NAMES = frozenset({
    "kosar", "kousar", "koosar", "kosra", "kosari", "kasra", "kasri",
    "kiana", "kian", "kianoosh", "kianush", "koorosh", "kourosh",
    "kurosh", "koroush", "maksim", "maxim", "koohyar", "kouhyar",
    "kohyar", "sikandar", "sikander", "koohsar", "kuhsar",
    "kosha", "koosha", "kousha", "kooshan", "kooshyar",
    "gohar", "gouhar", "goharshad", "shashi",
})


def _is_safe_name(token):
    return token in _SAFE_NAMES


def _is_known_name(value):
    """آیا این متن یک نام واقعی ثبت‌شده است.

    نام واقعی هرگز نباید قربانی تطبیق زیررشته‌ای شود: «کسری» و «مکسیم»
    واژهٔ «کس» را در خود دارند ولی نام‌اند.

    دیتابیس نام‌ها در ``modules/persian_names.py`` است، ولی بستهٔ economy
    عمداً از ``modules/`` چیزی import نمی‌کند. پس با ``importlib`` و در
    زمان اجرا خوانده می‌شود؛ اگر نبود، فهرست ``_SAFE_NAMES`` کار را
    پیش می‌برد.
    """
    global _NAMES_DB
    if _NAMES_DB is None:
        try:
            import importlib
            _NAMES_DB = importlib.import_module("modules.persian_names")
        except Exception:
            _NAMES_DB = False
    if not _NAMES_DB:
        return False
    try:
        return _NAMES_DB.is_known_name(value)
    except Exception:
        return False


def classify(text):
    """نوع مشکل متن را برمی‌گرداند.

    خروجی: ``None`` (سالم)، ``RESTRICTED`` یا ``BANNED``.
    """
    if not str(text or "").strip():
        return None

    variants = _squash_variants(text)
    tokens = _tokens(text)
    if not variants:
        return None
    squashed = _squash(text)

    # ۱) واژه‌های محدودشده — اولویت بالاتر، پیام مخصوص خودشان.
    for term in _RESTRICTED_SET:
        if term and any(term in variant for variant in variants):
            return RESTRICTED

    # ۲) واژه‌های رکیکِ بلند: هر جای متن باشند رد می‌شوند.
    for term in _LONG_SET:
        if term and any(term in variant for variant in variants):
            return BANNED

    # ۳) واژه‌های کوتاه: فقط وقتی خودشان یک واژهٔ مستقل باشند.
    #    این‌طور «کسری» یا «مکسیم» قربانی نمی‌شوند.
    for token in tokens:
        if _is_safe_name(token):
            continue
        if token in _SHORT_SET:
            return BANNED

    # ۴) کل متنِ فشرده اگر دقیقاً یک واژهٔ کوتاه باشد (مثل «ک ی ر»).
    if any(variant in _SHORT_SET for variant in variants):
        return BANNED

    # ۵) واژهٔ کوتاه چسبیده به متن، فقط اگر نام واقعی نباشد.
    if not _is_known_name(text) and not _is_known_name(squashed):
        for token in tokens:
            if _is_known_name(token) or _is_safe_name(token):
                continue
            for term in _SHORT_SET:
                if term and len(term) >= 3 and term in token:
                    return BANNED
    return None


def is_allowed(text):
    """``True`` یعنی این نام/لقب قابل استفاده است."""
    return classify(text) is None


def message_for(kind):
    """پیام مناسب برای نوع مشکل."""
    if kind == RESTRICTED:
        return MESSAGE_RESTRICTED
    if kind == BANNED:
        return MESSAGE_BANNED
    return None


def check(text):
    """``(ok, message)`` — راحت‌ترین راه استفاده."""
    kind = classify(text)
    if kind is None:
        return True, None
    return False, message_for(kind)
