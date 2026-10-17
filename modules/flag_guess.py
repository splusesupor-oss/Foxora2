"""Thirty-second country flag guessing game using Unicode flag images.

انتخاب پرچم کاملاً تصادفی است و برای هر چت یک «تاریخچه» نگه داشته می‌شود، پس
پرچمی که قبلاً دیده شده تا پایان یک دور کامل دوباره نمی‌آید. وقتی همهٔ پرچم‌ها
مصرف شدند تاریخچه صفر می‌شود و دور تازه آغاز می‌گردد — با این تضمین که پرچم
اولِ دور جدید با آخرین پرچم دور قبل یکی نباشد.
"""
import json
import random
from itertools import count
from pathlib import Path

from modules.runtime_paths import runtime_config_file
from modules.game_progress_storage import SeenProgressStore
from modules.atomic_write import write_json

COUNTRIES = (
    # --- خاورمیانه و آسیای غربی ---
    ("🇮🇷", "ایران", ("iran",)),
    ("🇹🇷", "ترکیه", ("turkey", "türkiye")),
    ("🇸🇦", "عربستان سعودی", ("saudi arabia", "عربستان")),
    ("🇦🇪", "امارات", ("uae", "امارات متحده عربی")),
    ("🇮🇶", "عراق", ("iraq",)),
    ("🇶🇦", "قطر", ("qatar",)),
    ("🇰🇼", "کویت", ("kuwait",)),
    ("🇴🇲", "عمان", ("oman",)),
    ("🇧🇭", "بحرین", ("bahrain",)),
    ("🇯🇴", "اردن", ("jordan",)),
    ("🇱🇧", "لبنان", ("lebanon",)),
    ("🇸🇾", "سوریه", ("syria",)),
    ("🇾🇪", "یمن", ("yemen",)),
    ("🇦🇫", "افغانستان", ("afghanistan",)),
    ("🇦🇿", "آذربایجان", ("azerbaijan",)),
    ("🇦🇲", "ارمنستان", ("armenia",)),
    ("🇬🇪", "گرجستان", ("georgia",)),
    # --- آسیای مرکزی و جنوبی ---
    ("🇵🇰", "پاکستان", ("pakistan",)),
    ("🇮🇳", "هند", ("india",)),
    ("🇧🇩", "بنگلادش", ("bangladesh",)),
    ("🇱🇰", "سریلانکا", ("sri lanka",)),
    ("🇳🇵", "نپال", ("nepal",)),
    ("🇹🇲", "ترکمنستان", ("turkmenistan",)),
    ("🇺🇿", "ازبکستان", ("uzbekistan",)),
    ("🇰🇿", "قزاقستان", ("kazakhstan",)),
    ("🇰🇬", "قرقیزستان", ("kyrgyzstan",)),
    ("🇹🇯", "تاجیکستان", ("tajikistan",)),
    ("🇲🇳", "مغولستان", ("mongolia",)),
    # --- شرق و جنوب شرق آسیا ---
    ("🇯🇵", "ژاپن", ("japan",)),
    ("🇨🇳", "چین", ("china",)),
    ("🇰🇷", "کره جنوبی", ("south korea", "کره‌جنوبی")),
    ("🇰🇵", "کره شمالی", ("north korea", "کره‌شمالی")),
    ("🇹🇭", "تایلند", ("thailand",)),
    ("🇻🇳", "ویتنام", ("vietnam",)),
    ("🇮🇩", "اندونزی", ("indonesia",)),
    ("🇲🇾", "مالزی", ("malaysia",)),
    ("🇵🇭", "فیلیپین", ("philippines",)),
    ("🇸🇬", "سنگاپور", ("singapore",)),
    ("🇲🇲", "میانمار", ("myanmar", "برمه")),
    ("🇰🇭", "کامبوج", ("cambodia",)),
    ("🇱🇦", "لائوس", ("laos",)),
    ("🇧🇳", "برونئی", ("brunei",)),
    # --- اروپای غربی و شمالی ---
    ("🇫🇷", "فرانسه", ("france",)),
    ("🇩🇪", "آلمان", ("germany",)),
    ("🇮🇹", "ایتالیا", ("italy",)),
    ("🇪🇸", "اسپانیا", ("spain",)),
    ("🇵🇹", "پرتغال", ("portugal",)),
    ("🇬🇧", "انگلستان", ("united kingdom", "بریتانیا", "uk")),
    ("🇮🇪", "ایرلند", ("ireland",)),
    ("🇳🇱", "هلند", ("netherlands",)),
    ("🇧🇪", "بلژیک", ("belgium",)),
    ("🇱🇺", "لوکزامبورگ", ("luxembourg",)),
    ("🇨🇭", "سوئیس", ("switzerland",)),
    ("🇦🇹", "اتریش", ("austria",)),
    ("🇳🇴", "نروژ", ("norway",)),
    ("🇸🇪", "سوئد", ("sweden",)),
    ("🇫🇮", "فنلاند", ("finland",)),
    ("🇩🇰", "دانمارک", ("denmark",)),
    ("🇮🇸", "ایسلند", ("iceland",)),
    # --- اروپای شرقی و جنوبی ---
    ("🇷🇺", "روسیه", ("russia",)),
    ("🇺🇦", "اوکراین", ("ukraine",)),
    ("🇵🇱", "لهستان", ("poland",)),
    ("🇨🇿", "چک", ("czechia", "جمهوری چک")),
    ("🇸🇰", "اسلواکی", ("slovakia",)),
    ("🇭🇺", "مجارستان", ("hungary",)),
    ("🇷🇴", "رومانی", ("romania",)),
    ("🇧🇬", "بلغارستان", ("bulgaria",)),
    ("🇷🇸", "صربستان", ("serbia",)),
    ("🇭🇷", "کرواسی", ("croatia",)),
    ("🇬🇷", "یونان", ("greece",)),
    ("🇦🇱", "آلبانی", ("albania",)),
    ("🇧🇦", "بوسنی", ("bosnia", "بوسنی و هرزگوین")),
    ("🇧🇾", "بلاروس", ("belarus",)),
    ("🇱🇹", "لیتوانی", ("lithuania",)),
    ("🇱🇻", "لتونی", ("latvia",)),
    ("🇪🇪", "استونی", ("estonia",)),
    ("🇲🇹", "مالت", ("malta",)),
    ("🇨🇾", "قبرس", ("cyprus",)),
    # --- آفریقا ---
    ("🇪🇬", "مصر", ("egypt",)),
    ("🇲🇦", "مراکش", ("morocco",)),
    ("🇩🇿", "الجزایر", ("algeria",)),
    ("🇹🇳", "تونس", ("tunisia",)),
    ("🇱🇾", "لیبی", ("libya",)),
    ("🇸🇩", "سودان", ("sudan",)),
    ("🇪🇹", "اتیوپی", ("ethiopia",)),
    ("🇰🇪", "کنیا", ("kenya",)),
    ("🇹🇿", "تانزانیا", ("tanzania",)),
    ("🇺🇬", "اوگاندا", ("uganda",)),
    ("🇳🇬", "نیجریه", ("nigeria",)),
    ("🇬🇭", "غنا", ("ghana",)),
    ("🇸🇳", "سنگال", ("senegal",)),
    ("🇨🇮", "ساحل عاج", ("ivory coast",)),
    ("🇨🇲", "کامرون", ("cameroon",)),
    ("🇿🇦", "آفریقای جنوبی", ("south africa",)),
    ("🇿🇼", "زیمبابوه", ("zimbabwe",)),
    ("🇳🇦", "نامیبیا", ("namibia",)),
    ("🇲🇿", "موزامبیک", ("mozambique",)),
    ("🇦🇴", "آنگولا", ("angola",)),
    ("🇲🇬", "ماداگاسکار", ("madagascar",)),
    ("🇲🇱", "مالی", ("mali",)),
    ("🇸🇴", "سومالی", ("somalia",)),
    # --- آمریکای شمالی و مرکزی ---
    ("🇺🇸", "آمریکا", ("usa", "united states", "ایالات متحده")),
    ("🇨🇦", "کانادا", ("canada",)),
    ("🇲🇽", "مکزیک", ("mexico",)),
    ("🇨🇺", "کوبا", ("cuba",)),
    ("🇯🇲", "جامائیکا", ("jamaica",)),
    ("🇵🇦", "پاناما", ("panama",)),
    ("🇨🇷", "کاستاریکا", ("costa rica",)),
    ("🇬🇹", "گواتمالا", ("guatemala",)),
    ("🇭🇳", "هندوراس", ("honduras",)),
    ("🇩🇴", "دومینیکن", ("dominican republic",)),
    # --- آمریکای جنوبی ---
    ("🇧🇷", "برزیل", ("brazil",)),
    ("🇦🇷", "آرژانتین", ("argentina",)),
    ("🇨🇱", "شیلی", ("chile",)),
    ("🇨🇴", "کلمبیا", ("colombia",)),
    ("🇵🇪", "پرو", ("peru",)),
    ("🇻🇪", "ونزوئلا", ("venezuela",)),
    ("🇺🇾", "اروگوئه", ("uruguay",)),
    ("🇵🇾", "پاراگوئه", ("paraguay",)),
    ("🇧🇴", "بولیوی", ("bolivia",)),
    ("🇪🇨", "اکوادور", ("ecuador",)),
    # --- اقیانوسیه ---
    ("🇦🇺", "استرالیا", ("australia",)),
    ("🇳🇿", "نیوزیلند", ("new zealand",)),
    ("🇫🇯", "فیجی", ("fiji",)),
    ("🇵🇬", "پاپوآ گینه نو", ("papua new guinea",)),
)

_ACTIVE = {}
_LAST_COUNTRY = {}
# تاریخچهٔ پرچم‌های دیده‌شده، به تفکیک «کاربر» (نه چت). هر کاربر تا زمانی که
# همهٔ پرچم‌ها را ندیده هیچ تکراری دریافت نمی‌کند و پس از دیدن همه، بازی برای
# او برای همیشه بسته می‌شود تا امتیاز تکراری نگیرد.
_SEEN_HISTORY = {}
_FALLBACK_TOKENS = count(1)

# فایلِ ماندگارِ تاریخچهٔ هر کاربر تا بعد از ری‌استارتِ ربات هم حفظ شود.
_PROGRESS_FILE = runtime_config_file("flag_guess_progress.json")
_PROGRESS_STORE = SeenProgressStore("flag_guess_progress", _PROGRESS_FILE, _SEEN_HISTORY)


def _load_progress():
    """تاریخچهٔ دیده‌شده را از فایل می‌خواند (بعد از ری‌استارت حفظ می‌شود)."""
    if _PROGRESS_STORE.sqlite:
        return
    try:
        if _PROGRESS_FILE.exists():
            data = json.loads(_PROGRESS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for k, v in data.items():
                    _SEEN_HISTORY.setdefault(k, set(v or ()))
    except (OSError, ValueError):
        pass


def _save_progress(user_key=None, values=None):
    """Persist JSON or mark one lazy SQLite row for batched persistence."""
    if _PROGRESS_STORE.sqlite:
        if user_key is not None:
            if values is None:
                _PROGRESS_STORE.mark(user_key)
            else:
                _PROGRESS_STORE.replace(user_key, values)
        return
    try:
        data = {k: sorted(v) for k, v in _SEEN_HISTORY.items()}
        write_json(_PROGRESS_FILE, data)
    except OSError:
        pass


_load_progress()


def _next_token():
    """توکن یکتا که با ری‌استارت ربات تکرار نمی‌شود.

    شمارندهٔ حافظه‌ای با هر ری‌استارت از ۱ شروع می‌شد و مرجع جایزه را
    تکراری می‌کرد، پس دفتر تراکنش پرداخت را رد می‌کرد و کاربر سکه‌ای
    نمی‌گرفت. حالا از شمارندهٔ ماندگار اقتصاد استفاده می‌شود.
    """
    try:
        from economy import rewards as _rewards
        return _rewards.round_id()
    except Exception:
        # اگر اقتصاد در دسترس نبود، بازی نباید بخوابد.
        return next(_FALLBACK_TOKENS)


_RANDOM = random.SystemRandom()

EXHAUSTED_MESSAGE = (
    "✅ شما این بازی را تکمیل کردید. لطفاً از بازی‌های دیگر استفاده کنید."
)


def _norm(value):
    return " ".join(str(value or "").strip().lower().replace("‌", " ").replace("ي", "ی").replace("ك", "ک").split())


def _user_key(user_id):
    return str(user_id)


def _seen(user_id):
    key = _user_key(user_id)
    if _PROGRESS_STORE.sqlite:
        return _PROGRESS_STORE.get(key)
    return _SEEN_HISTORY.setdefault(key, set())


def _chat_key(chat_id):
    return str(chat_id)


def is_exhausted(user_id):
    """آیا این کاربر همهٔ پرچم‌ها را دیده و بازی برایش بسته است."""
    seen = _seen(user_id)
    return bool(seen) and len(seen) >= len(COUNTRIES)


def remaining_count(user_id):
    """تعداد پرچم‌های باقی‌مانده برای این کاربر."""
    seen = _seen(user_id)
    return max(len(COUNTRIES) - len(seen), 0)


def seen_count(user_id):
    """تعداد پرچم‌هایی که این کاربر تا کنون دیده است."""
    return len(_seen(user_id))


def _pick_country(chat_id, user_id):
    """کشوری تصادفی که این کاربر ندیده است؛ None اگر همه را دیده باشد."""
    ukey = _user_key(user_id)
    seen = _seen(user_id)

    remaining = [c for c in COUNTRIES if c[1] not in seen]
    if not remaining:
        # همهٔ پرچم‌ها برای این کاربر مصرف شده؛ بازی بسته می‌ماند.
        return None

    # پرچم قبلیِ همین چت بلافاصله تکرار نمی‌شود.
    last = _LAST_COUNTRY.get(_chat_key(chat_id))
    if last is not None and len(remaining) > 1:
        remaining = [c for c in remaining if c[1] != last] or remaining

    country = _RANDOM.choice(remaining)
    seen.add(country[1])
    _save_progress(ukey, seen)
    return country


def is_active(chat_id):
    return chat_id in _ACTIVE


def get_active(chat_id):
    """جلسهٔ فعال این چت، یا ``None``.

    برای خواندن token *پیش از* بستن جلسه توسط ``answer()`` لازم است.
    """
    state = _ACTIVE.get(chat_id)
    return dict(state) if state else None


def start(chat_id, user_id=None):
    """بازی تازه شروع می‌کند.

    ``None`` یعنی بازی شروع نشد: یا بازی دیگری در همین چت فعال است، یا این
    کاربر همهٔ پرچم‌ها را دیده. برای تفکیک این دو از ``is_exhausted`` استفاده
    کنید.
    """
    if chat_id in _ACTIVE:
        return None
    if user_id is None:
        user_id = chat_id
    picked = _pick_country(chat_id, user_id)
    if picked is None:
        return None
    flag, answer, aliases = picked
    state = {
        "flag": flag,
        "answer": answer,
        "aliases": aliases,
        "token": _next_token(),
        "user_id": user_id,
    }
    _ACTIVE[chat_id] = state
    _LAST_COUNTRY[_chat_key(chat_id)] = answer
    return dict(state)


def answer(chat_id, text, user_id=None):
    """پاسخ را بررسی می‌کند.

    اگر ``user_id`` داده شود، فقط همان کاربری که بازی را شروع کرده می‌تواند
    امتیاز بگیرد؛ این جلوی کسب امتیاز توسط کاربرِ محدودشده را می‌گیرد.
    """
    state = _ACTIVE.get(chat_id)
    if not state:
        return None
    if user_id is not None and is_exhausted(user_id):
        return None
    accepted = {_norm(state["answer"])} | {_norm(alias) for alias in state["aliases"]}
    if _norm(text) not in accepted:
        return None
    _ACTIVE.pop(chat_id, None)
    if user_id is not None:
        # پاسخ‌دهنده هم این پرچم را «دیده» ثبت می‌شود، نه فقط شروع‌کننده.
        # در غیر این صورت یک نفر می‌توانست با شروع دادن دستور توسط دیگران
        # بی‌نهایت بار همان پرچم‌ها را جواب دهد و سکه بگیرد.
        key = _user_key(user_id)
        seen = _seen(user_id)
        seen.add(state["answer"])
        _save_progress(key, seen)
    return state["answer"]


def finish(chat_id, token=None):
    state = _ACTIVE.get(chat_id)
    if not state or (token is not None and state["token"] != token):
        return None
    _ACTIVE.pop(chat_id, None)
    return state["answer"]


def reset_history(user_id=None):
    """تاریخچهٔ یک کاربر (یا همهٔ کاربران) را پاک می‌کند."""
    if user_id is None:
        if _PROGRESS_STORE.sqlite:
            _PROGRESS_STORE.clear()
        else:
            _SEEN_HISTORY.clear()
            _save_progress()
        _LAST_COUNTRY.clear()
        _ACTIVE.clear()
        return
    key = _user_key(user_id)
    if _PROGRESS_STORE.sqlite:
        _PROGRESS_STORE.delete(key)
    else:
        _SEEN_HISTORY.pop(key, None)
        _save_progress()
