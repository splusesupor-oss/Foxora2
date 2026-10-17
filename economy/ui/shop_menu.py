"""🛒 بخش «فروشگاه» — مستقل از بخش موجودی.

فعلاً فقط زیرساخت: فهرست آیتم‌ها و خرید. افزودن آیتم جدید هیچ تغییری
در این فایل لازم ندارد؛ کافی است با ``economy.shop.add_item`` ثبت شود و
خودکار در فهرست و خرید ظاهر می‌شود.
"""
import time

import economy
from economy import catalog, profiles
from economy.ui import profile_menu
from economy.ui.formatting import fa, quote_spans, spans_for, u16

COMMAND = "فروشگاه"
SESSION_TIMEOUT = 180

# «لیست آیتم‌ها» گزینهٔ جدا ندارد؛ فهرست هنگام ورود نمایش داده می‌شود.
MENU_BUY = "1"
MENU_CLOSE = "0"

# راهنما و گزینه‌ها یک بلوک واحدند تا هر دو با هم Bold و داخل نقل قول
# شیشه‌ای بروند. نام گزینه دقیقاً همان چیزی است که کاربر خواسته.
MENU_BLOCK = (
    "برای انتخاب، شماره گزینه را بفرستید:\n\n"
    "۱) 🛍 لیست آیتم ها و خرید\n"
    "۰) بستن"
)

STEP_MENU = "menu"
STEP_BUY = "buy"
STEP_CONFIRM = "confirm"

_COIN_NAMES = {
    economy.BRONZE: "برنز",
    economy.SILVER: "نقره",
    economy.GOLD: "طلا",
}

_SESSIONS = {}


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
    for key, item in list(_SESSIONS.items()):
        if now - item["at"] > SESSION_TIMEOUT:
            del _SESSIONS[key]


def is_open(chat_id, user_id):
    _prune()
    return _key(chat_id, user_id) in _SESSIONS


def open_session(chat_id, user_id, step=STEP_MENU, **extra):
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
def render_menu(chat_id, user_id):
    balance = economy.get_balance(chat_id, user_id)
    header = "🛒 فروشگاه"
    # شمارش باید فهرست ثابت نشان/سطح/لقب را هم در بر بگیرد، وگرنه
    # کاربر «۰ آیتم» می‌بیند در حالی که ۳۲ آیتم خریدنی هست.
    count = len(catalog.all_items()) + len(economy.shop.list_items())
    text = (
        f"{header}\n\n"
        f"موجودی شما:\n"
        f"🥉 {fa(balance[economy.BRONZE])} | "
        f"🥈 {fa(balance[economy.SILVER])} | "
        f"🥇 {fa(balance[economy.GOLD])}\n"
        f"💎 ارزش کل: {fa(balance['total_coin_value'])}\n\n"
        f"آیتم‌های موجود: {fa(count)}\n\n"
        f"{MENU_BLOCK}"
    )
    spans = spans_for(text, [header, "💎 ارزش کل:"])
    # راهنما و گزینه‌ها: Bold داخل نقل قول شیشه‌ای.
    spans += quote_spans(text, MENU_BLOCK)
    return text, spans


def render_entry(chat_id, user_id):
    """ورود به فروشگاه: منو + فهرست آیتم‌ها با هم.

    کاربر دیگر لازم نیست اول «لیست آیتم‌ها» را انتخاب کند.
    """
    menu, menu_spans = render_menu(chat_id, user_id)
    items, _ = render_items(chat_id, user_id)
    combined = f"{menu}\n\n{items}"
    # منو پیشوند متن نهایی است، پس offsetهای آن (شامل نقل قول شیشه‌ای)
    # بدون تغییر معتبر می‌مانند. بازساختن آن‌ها باعث می‌شد نقل قول گم شود.
    spans = list(menu_spans)
    # بخش فهرست کاملاً Bold می‌ماند.
    spans.append(("bold", u16(f"{menu}\n\n"), u16(items)))
    return combined, spans


def render_items(chat_id=None, user_id=None):
    """فهرست آیتم‌ها.

    نشان‌ها، سطح‌ها و لقب‌ها *همان* فهرست بخش پروفایل‌اند؛ یک منبع
    حقیقت واحد. پیش‌تر این تابع فقط ``economy.shop`` را می‌خواند که
    هیچ‌وقت پر نمی‌شد، پس کاربر همیشه «هنوز آیتمی اضافه نشده» می‌دید.
    """
    text, spans = profile_menu.render_items(chat_id, user_id)

    items = economy.shop.list_items()
    if not items:
        return text, spans

    # آیتم‌های پویا (اگر کسی با add_item ثبت کرده باشد) پس از فهرست ثابت.
    header = "🛒 آیتم‌های ویژه"
    lines = [header, ""]
    for item in items:
        stock = item.get("stock")
        stock_text = ""
        if stock is not None:
            stock_text = (f"\n📦 موجودی: {fa(stock)}" if stock > 0
                          else "\n📦 ناموجود")
        description = f"\n{item['description']}" if item.get("description") \
            else ""
        lines.append(
            f"🔖 {item['title']}\n"
            f"🆔 {item['id']}\n"
            f"💵 {fa(item['price'])} {_COIN_NAMES.get(item['coin_type'], '')}"
            f"{description}{stock_text}"
        )
    extra = "\n\n".join(lines)
    combined = f"{text}\n\n{extra}"
    # کل متن Bold می‌ماند، دقیقاً مثل بخش پروفایل.
    return combined, [("bold", 0, u16(combined))]


def buy_prompt(chat_id=None, user_id=None):
    """راهنمای انتخاب آیتم. فهرست همان بالا نمایش داده شده است."""
    text = (
        "🛍 خرید آیتم\n\n"
        "شمارهٔ آیتم موردنظر را بفرستید (۱ تا "
        f"{fa(len(catalog.all_items()))}).\n"
        "برای لغو، ۰ بفرستید."
    )
    return text, spans_for(text, ["🛍 خرید آیتم"])


def _legacy_buy_prompt():
    items = economy.shop.list_items()
    if not items:
        return (
            "🛒 خرید\n\n"
            "هنوز آیتمی برای خرید وجود ندارد."
        )
    ids = "\n".join(f"• {item['id']} — {item['title']}" for item in items)
    return (
        "🛒 خرید\n\n"
        "شناسهٔ آیتم را بفرستید:\n\n"
        f"{ids}\n\n"
        "برای لغو، ۰ بفرستید."
    )


def select_item(chat_id, user_id, text):
    """انتخاب آیتم: اول فهرست ثابت، بعد آیتم‌های پویای فروشگاه."""
    if catalog.resolve(text) is not None:
        return profile_menu.select_item(chat_id, user_id, text)

    # آیتم پویا (ثبت‌شده با add_item) — با شناسه انتخاب می‌شود.
    item = economy.shop.get_item(normalize(text))
    if item is None:
        return profile_menu.select_item(chat_id, user_id, text)

    stock = item.get("stock")
    if stock is not None and stock <= 0:
        return None, "❌ موجودی این آیتم تمام شده است."

    balance = economy.get_balance(chat_id, user_id)
    have = int(balance.get(item["coin_type"], 0))
    missing = max(0, int(item["price"]) - have)
    if missing:
        coin = _COIN_NAMES.get(item["coin_type"], "")
        return None, (
            "موجودی سکه کافی نیست.\n"
            f"برای خرید این آیتم به {fa(missing)} سکه {coin} دیگر "
            "نیاز دارید."
        )
    text_out = (
        f"🛍 {item['title']}\n"
        f"💵 قیمت: {fa(item['price'])} "
        f"{_COIN_NAMES.get(item['coin_type'], '')}\n\n"
        "آیا از خرید این آیتم مطمئن هستید؟\n\n"
        "✅ تایید\n"
        "❌ لغو"
    )
    return item, (text_out,
                  spans_for(text_out,
                            ["آیا از خرید این آیتم مطمئن هستید؟",
                             "✅ تایید", "❌ لغو"]))


def is_confirm(text):
    return profile_menu.is_confirm(text)


def is_decline(text):
    return profile_menu.is_decline(text)


def do_buy(chat_id, user_id, item_id, *, reference=None):
    # آیتم‌های ثابت (نشان/سطح/لقب) از مسیر پروفایل خریداری می‌شوند تا
    # اثرشان فوراً روی کارت بنشیند.
    if catalog.resolve(item_id) is not None:
        return profile_menu.do_buy(chat_id, user_id, item_id,
                                   reference=reference)
    try:
        item, balance = economy.shop.buy(chat_id, user_id, item_id,
                                         reference=reference)
    except economy.shop.ShopError as error:
        return False, f"❌ {error}"
    return True, (
        f"✅ «{item['title']}» خریداری شد.\n\n"
        f"💵 پرداخت: {fa(item['price'])} "
        f"{_COIN_NAMES.get(item['coin_type'], '')}\n\n"
        f"موجودی شما:\n"
        f"🥉 {fa(balance[economy.BRONZE])} | "
        f"🥈 {fa(balance[economy.SILVER])} | "
        f"🥇 {fa(balance[economy.GOLD])}\n"
        f"💎 ارزش کل: {fa(balance['total_coin_value'])}"
    )
