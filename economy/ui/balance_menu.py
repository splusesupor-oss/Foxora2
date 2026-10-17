"""💰 بخش «موجودی» — یک منوی واحد شامل تمام قابلیت‌های اقتصاد.

هیچ‌کدام از این قابلیت‌ها دستور جداگانه ندارند؛ همه فقط از داخل همین
بخش در دسترس‌اند. کاربر ابتدا «موجودی» می‌فرستد و سپس شمارهٔ گزینه را.

این ماژول *فقط* از API عمومی اقتصاد استفاده می‌کند و هرگز فایل دیتابیس
را باز نمی‌کند.
"""
import time

import economy
from economy import directory, settings
from economy.ui.formatting import fa, format_duration, spans_for

COMMAND = "موجودی"

# مدت زندهٔ منو؛ پس از آن کاربر باید دوباره «موجودی» بفرستد.
SESSION_TIMEOUT = 180

MENU_BRONZE_TO_SILVER = "1"
MENU_SILVER_TO_GOLD = "2"
MENU_SEND_BRONZE = "3"
MENU_SEND_SILVER = "4"
MENU_SEND_GOLD = "5"
MENU_HISTORY = "6"
MENU_DAILY = "7"
MENU_CLOSE = "0"

_TRANSFER_COINS = {
    MENU_SEND_BRONZE: economy.BRONZE,
    MENU_SEND_SILVER: economy.SILVER,
    MENU_SEND_GOLD: economy.GOLD,
}

_COIN_NAMES = {
    economy.BRONZE: "برنز",
    economy.SILVER: "نقره",
    economy.GOLD: "طلا",
}

# session های باز: (chat_id, user_id) -> {"step": ..., "at": ...}
_SESSIONS = {}

_DIGIT_MAP = {ord(p): str(i) for i, p in enumerate("۰۱۲۳۴۵۶۷۸۹")}
_DIGIT_MAP.update({ord(a): str(i) for i, a in enumerate("٠١٢٣٤٥٦٧٨٩")})


def _english(text):
    return str(text or "").translate(_DIGIT_MAP).strip()


def normalize(text):
    value = str(text or "")
    for source, target in (("\u200c", " "), ("\u200f", ""), ("\u200e", ""),
                           ("ي", "ی"), ("ك", "ک")):
        value = value.replace(source, target)
    return " ".join(value.split())


def is_command(text):
    return normalize(text) == COMMAND


def _key(chat_id, user_id):
    return (str(chat_id), str(user_id))


def _prune():
    now = time.monotonic()
    for key, session in list(_SESSIONS.items()):
        if now - session["at"] > SESSION_TIMEOUT:
            del _SESSIONS[key]


def is_open(chat_id, user_id):
    _prune()
    return _key(chat_id, user_id) in _SESSIONS


def open_session(chat_id, user_id, step="menu", **extra):
    _prune()
    session = {"step": step, "at": time.monotonic()}
    session.update(extra)
    _SESSIONS[_key(chat_id, user_id)] = session
    return session


def close_session(chat_id, user_id):
    return _SESSIONS.pop(_key(chat_id, user_id), None) is not None


def session(chat_id, user_id):
    _prune()
    return _SESSIONS.get(_key(chat_id, user_id))


def reset_all():
    _SESSIONS.clear()


# ---------------------------------------------------------------------------
# متن‌ها
# ---------------------------------------------------------------------------
def render_menu(chat_id, user_id, *, balance=None, rank=None):
    """منوی اصلی به‌همراه موجودی زنده."""
    if balance is None:
        balance = economy.get_balance(chat_id, user_id)
    if rank is None:
        rank = economy.get_rank(chat_id, user_id)
    config = settings.load()

    header = "💰 کیف پول شما"
    text = (
        f"{header}\n\n"
        f"🥉 برنز: {fa(balance[economy.BRONZE])}\n"
        f"🥈 نقره: {fa(balance[economy.SILVER])}\n"
        f"🥇 طلا: {fa(balance[economy.GOLD])}\n\n"
        f"💎 ارزش کل: {fa(balance['total_coin_value'])}\n"
        f"🏆 رتبه: {fa(rank) if rank else 'ندارد'}\n\n"
        "برای انتخاب، شمارهٔ گزینه را بفرستید:\n\n"
        f"۱) تبدیل برنز به نقره "
        f"({fa(int(config['BronzeToSilverCost']))} ➜ "
        f"{fa(int(config['BronzeToSilverGain']))})\n"
        f"۲) تبدیل نقره به طلا "
        f"({fa(int(config['SilverToGoldCost']))} ➜ "
        f"{fa(int(config['SilverToGoldGain']))})\n"
        "۳) انتقال برنز\n"
        "۴) انتقال نقره\n"
        "۵) انتقال طلا\n"
        "۶) تاریخچه تراکنش‌ها\n"
        "۷) جایزه روزانه\n"
        "۰) بستن"
    )
    spans = spans_for(text, [header])
    spans += spans_for(text, ["🥉 برنز:", "🥈 نقره:", "🥇 طلا:",
                              "💎 ارزش کل:", "🏆 رتبه:"])
    return text, spans


def render_balance_only(chat_id, user_id):
    balance = economy.get_balance(chat_id, user_id)
    text = (
        "💰 موجودی\n\n"
        f"🥉 برنز: {fa(balance[economy.BRONZE])}\n"
        f"🥈 نقره: {fa(balance[economy.SILVER])}\n"
        f"🥇 طلا: {fa(balance[economy.GOLD])}\n\n"
        f"💎 ارزش کل: {fa(balance['total_coin_value'])}"
    )
    return text, spans_for(text, ["💰 موجودی", "💎 ارزش کل:"])


def render_history(chat_id, user_id, limit=10):
    entries = economy.transaction_history(chat_id, user_id, limit=limit)
    header = "🧾 تاریخچه تراکنش‌ها"
    if not entries:
        text = f"{header}\n\nهنوز تراکنشی ثبت نشده است."
        return text, spans_for(text, [header])

    labels = {
        "reward": "🎁 جایزه",
        "receive": "➕ دریافت",
        "spend": "➖ خرج",
        "convert": "🔄 تبدیل",
        "transfer_in": "📥 دریافت انتقال",
        "transfer_out": "📤 ارسال انتقال",
        "daily": "📅 جایزه روزانه",
        "purchase": "🛒 خرید",
    }
    lines = [header, ""]
    for entry in entries:
        changes = " ، ".join(
            f"{_COIN_NAMES.get(coin, coin)} {'+' if amount > 0 else ''}"
            f"{fa(amount)}"
            for coin, amount in entry.get("changes", {}).items()
        ) or "بدون تغییر"
        when = str(entry.get("at", ""))[:16].replace("T", " ")
        label = labels.get(entry.get("kind"), entry.get("kind", ""))
        note = f" — {entry['note']}" if entry.get("note") else ""
        lines.append(f"{label}: {changes}{note}\n🕐 {when}")
    text = "\n\n".join(lines)
    return text, spans_for(text, [header])


# ---------------------------------------------------------------------------
# اقدام‌ها
# ---------------------------------------------------------------------------
def do_convert_bronze(chat_id, user_id):
    try:
        balance = economy.convert_bronze(chat_id, user_id)
    except economy.EconomyError as error:
        return False, f"❌ {error}"
    config = settings.load()
    return True, (
        f"✅ {fa(int(config['BronzeToSilverCost']))} برنز به "
        f"{fa(int(config['BronzeToSilverGain']))} نقره تبدیل شد.\n\n"
        f"🥉 برنز: {fa(balance[economy.BRONZE])}\n"
        f"🥈 نقره: {fa(balance[economy.SILVER])}\n"
        f"💎 ارزش کل: {fa(balance['total_coin_value'])}"
    )


def do_convert_silver(chat_id, user_id):
    try:
        balance = economy.convert_silver(chat_id, user_id)
    except economy.EconomyError as error:
        return False, f"❌ {error}"
    config = settings.load()
    return True, (
        f"✅ {fa(int(config['SilverToGoldCost']))} نقره به "
        f"{fa(int(config['SilverToGoldGain']))} طلا تبدیل شد.\n\n"
        f"🥈 نقره: {fa(balance[economy.SILVER])}\n"
        f"🥇 طلا: {fa(balance[economy.GOLD])}\n"
        f"💎 ارزش کل: {fa(balance['total_coin_value'])}"
    )


def do_daily(chat_id, user_id):
    granted, balance, wait = economy.claim_daily(chat_id, user_id)
    if not granted:
        return False, (
            "⏳ جایزه روزانه را قبلاً دریافت کرده‌اید.\n\n"
            f"نوبت بعدی: {format_duration(wait)} دیگر"
        )
    config = settings.load()
    parts = []
    for coin, key in ((economy.BRONZE, "DailyRewardBronze"),
                      (economy.SILVER, "DailyRewardSilver"),
                      (economy.GOLD, "DailyRewardGold")):
        amount = int(config[key])
        if amount > 0:
            parts.append(f"{_COIN_NAMES[coin]} +{fa(amount)}")
    return True, (
        "🎁 جایزه روزانه دریافت شد!\n\n"
        + ("، ".join(parts) if parts else "بدون جایزه")
        + f"\n\n💎 ارزش کل: {fa(balance['total_coin_value'])}"
    )


def parse_transfer_amount(text):
    """مقدار انتقال را می‌خواند؛ ``None`` اگر عدد معتبر نباشد."""
    cleaned = _english(text)
    if not cleaned.isdigit():
        return None
    value = int(cleaned)
    return value if value > 0 else None


def do_transfer(chat_id, sender_id, receiver_id, coin_type, amount, *, reference=None):
    try:
        result = economy.transfer(
            chat_id, sender_id, receiver_id, coin_type, amount,
            reference=reference)
    except economy.EconomyError as error:
        return False, f"❌ {error}"
    sender = result["sender"]
    return True, (
        f"✅ {fa(amount)} {_COIN_NAMES[coin_type]} منتقل شد.\n\n"
        f"موجودی شما:\n"
        f"🥉 {fa(sender[economy.BRONZE])} | "
        f"🥈 {fa(sender[economy.SILVER])} | "
        f"🥇 {fa(sender[economy.GOLD])}\n"
        f"💎 ارزش کل: {fa(sender['total_coin_value'])}"
    )


def transfer_prompt(chat_id, coin_type, user_id):
    """گام ۱: یوزرنیم مقصد. ریپلای دیگر پذیرفته نمی‌شود."""
    balance = economy.get_balance(chat_id, user_id)
    return (
        f"📤 انتقال {_COIN_NAMES[coin_type]}\n\n"
        f"موجودی شما: {fa(balance[coin_type])}\n\n"
        "یوزرنیم کاربر مقصد را ارسال کنید:\n"
        "مثال:\n"
        "@username\n\n"
        "برای لغو، ۰ بفرستید."
    )


def transfer_amount_prompt(chat_id, coin_type, user_id, target_username):
    """گام ۲: مقدار سکه."""
    balance = economy.get_balance(chat_id, user_id)
    return (
        f"📤 انتقال {_COIN_NAMES[coin_type]} به @{target_username}\n\n"
        f"موجودی شما: {fa(balance[coin_type])}\n\n"
        f"مقدار {_COIN_NAMES[coin_type]} برای انتقال را ارسال کنید:\n\n"
        "مثال:\n"
        "10\n\n"
        "برای لغو، ۰ بفرستید."
    )


def resolve_target(chat_id, text, sender_id):
    """یوزرنیم مقصد را به شناسه تبدیل می‌کند.

    خروجی ``(user_id, username, error)``. آیدی عددی عمداً رد می‌شود؛
    فقط یوزرنیم معتبر پذیرفته است.
    """
    raw = str(text or "").strip()
    cleaned = _english(raw).strip()
    if cleaned.isdigit():
        return None, None, (
            "❌ آیدی عددی پذیرفته نمی‌شود.\n"
            "فقط یوزرنیم کاربر مقصد را بفرستید. مثال:\n@username"
        )
    if not directory.is_valid(raw):
        return None, None, (
            "❌ یوزرنیم معتبر نیست.\n"
            "یوزرنیم باید با حرف انگلیسی شروع شود. مثال:\n@username"
        )

    username = directory.normalize(raw)
    target_id = directory.lookup(chat_id, username)
    if target_id is None:
        return None, None, (
            f"❌ کاربری با یوزرنیم @{username} در این گروه پیدا نشد.\n"
            "کاربر مقصد باید حداقل یک پیام در این گروه فرستاده باشد."
        )
    if str(target_id) == str(sender_id):
        return None, None, "❌ انتقال به خودتان ممکن نیست."
    return target_id, username, None


def coin_for_option(option):
    return _TRANSFER_COINS.get(option)
