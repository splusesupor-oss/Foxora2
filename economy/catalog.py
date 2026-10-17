"""📦 فهرست ثابت آیتم‌های قابل خرید پروفایل.

سه دستهٔ آیتم اینجا تعریف می‌شوند و *فقط* از همین‌جا خوانده می‌شوند:

    🛡 نشان‌ها      → کنار عنوان و در بخش نشان‌های پروفایل دیده می‌شوند
    ⭐ خرید سطح     → تعداد ستارهٔ پرشدهٔ پروفایل
    🏷 خرید لقب     → عنوان بالای پروفایل

این فایل عمداً هیچ وابستگی‌ای به بیرون از بستهٔ economy ندارد؛ فقط
داده و توابع خالص. ترتیب فهرست‌ها همان ترتیبی است که به کاربر نمایش
داده می‌شود، پس شمارهٔ هر آیتم پایدار می‌ماند.
"""
from economy import settings

BRONZE = settings.BRONZE
SILVER = settings.SILVER
GOLD = settings.GOLD

KIND_BADGE = "badge"
KIND_STAR = "star"
KIND_TITLE = "title"

MAX_STARS = 7

# --- 🛡 نشان‌ها -------------------------------------------------------------
# (شناسه، ایموجی، نام، قیمت، نوع سکه)
_BADGES = (
    ("badge_fox",    "🦊", "نشان روباه",    100, SILVER),
    ("badge_lion",   "🦁", "نشان شیر",      120, SILVER),
    ("badge_heart",  "🫀", "نشان قلب",      300, BRONZE),
    ("badge_king",   "👑", "نشان پادشاه",   300, SILVER),
    ("badge_bolt",   "⚡", "نشان صاعقه",    150, SILVER),
    ("badge_skull",  "💀", "نشان اسکلت",    200, SILVER),
    ("badge_wolf",   "🐺", "نشان گرگ",      180, SILVER),
    ("badge_dragon", "🐉", "نشان اژدها",    500, SILVER),
    ("badge_legend", "☠️", "نشان افسانه",   700, SILVER),
    ("badge_galaxy", "🌌", "نشان کهکشانی",  900, SILVER),
)

# --- ⭐ خرید سطح ------------------------------------------------------------
# (سطح، نام، قیمت)
_STARS = (
    (1, "یک ستاره",   200),
    (2, "دو ستاره",   400),
    (3, "سه ستاره",   800),
    (4, "چهار ستاره", 1000),
    (5, "پنج ستاره",  1200),
    (6, "شش ستاره",   1400),
    (7, "هفت ستاره",  1800),
)

# --- 🏷 خرید لقب اختصاصی ----------------------------------------------------
TITLE_PRICE = 200
TITLE_COIN = BRONZE

# (شناسه، ایموجی، متن لقب، نام قابل تایپ برای خرید)
_TITLES = (
    ("title_fox_king",  "👑", "𝙁𝙤𝙭 𝙆𝙞𝙣𝙜",   "fox king"),
    ("title_dark_lord", "⚡", "𝘿𝙖𝙧𝙠 𝙇𝙤𝙧𝙙",  "dark lord"),
    ("title_royal",     "💎", "𝙍𝙤𝙮𝙖𝙡",      "royal"),
    ("title_fox_boy",   "🦊", "𝙁𝙤𝙭 𝘽𝙤𝙮",    "fox boy"),
    ("title_killer",    "☠️", "𝙆𝙞𝙡𝙡𝙚𝙧",     "killer"),
    ("title_overlord",  "👑", "𝙊𝙫𝙚𝙧𝙇𝙤𝙧𝙙",   "overlord"),
    ("title_warrior",   "⚔️", "𝙒𝙖𝙧𝙧𝙞𝙤𝙧",    "warrior"),
    ("title_moon",      "🌙", "𝙈𝙤𝙤𝙣",       "moon"),
    ("title_devil",     "😈", "𝘿𝙚𝙫𝙞𝙡",      "devil"),
    ("title_reaper",    "💀", "𝙍𝙚𝙖𝙥𝙚𝙧",     "reaper"),
    ("title_phantom",   "🎭", "𝙋𝙝𝙖𝙣𝙩𝙤𝙢",    "phantom"),
    ("title_lone_wolf", "🐺", "𝙇𝙤𝙣𝙚 𝙒𝙤𝙡𝙛",  "lone wolf"),
    ("title_dragon",    "🐉", "𝘿𝙧𝙖𝙜𝙤𝙣",     "dragon"),
    ("title_emperor",   "⚜️", "𝙀𝙢𝙥𝙚𝙧𝙤𝙧",    "emperor"),
    ("title_star_boy",  "🌠", "𝙎𝙩𝙖𝙧𝘽𝙤𝙮",    "starboy"),
)


def _badge_items():
    items = []
    for item_id, emoji, name, price, coin in _BADGES:
        items.append({
            "id": item_id,
            "kind": KIND_BADGE,
            "emoji": emoji,
            "name": name,
            "label": f"{emoji} {name}",
            "price": int(price),
            "coin_type": coin,
            "aliases": (name, name.replace("نشان ", "").strip(), emoji),
        })
    return items


def _star_items():
    items = []
    for level, name, price in _STARS:
        stars = "⭐" * level
        items.append({
            "id": f"star_{level}",
            "kind": KIND_STAR,
            "emoji": stars,
            "name": name,
            "label": f"{stars} {name}",
            "level": int(level),
            "price": int(price),
            "coin_type": SILVER,
            "aliases": (name, f"سطح {level}", f"ستاره {level}", stars),
        })
    return items


def _title_items():
    items = []
    for item_id, emoji, text, typed in _TITLES:
        items.append({
            "id": item_id,
            "kind": KIND_TITLE,
            "emoji": emoji,
            "name": text,
            "label": f"{emoji} {text}",
            "title": text,
            "typed": typed,
            "price": TITLE_PRICE,
            "coin_type": TITLE_COIN,
            "aliases": (text, typed, typed.replace(" ", "")),
        })
    return items


def badges():
    """فهرست نشان‌ها، به ترتیب نمایش."""
    return _badge_items()


def stars():
    """فهرست سطح‌های ستاره، از یک تا هفت."""
    return _star_items()


def titles():
    """فهرست لقب‌های اختصاصی."""
    return _title_items()


def all_items():
    """همهٔ آیتم‌ها پشت سر هم؛ شمارهٔ هر آیتم = جایگاه آن + ۱."""
    return badges() + stars() + titles()


def get(item_id):
    """آیتم را با شناسهٔ دقیق برمی‌گرداند."""
    for item in all_items():
        if item["id"] == str(item_id):
            return item
    return None


def get_badge(item_id):
    for item in badges():
        if item["id"] == str(item_id):
            return item
    return None


_DIGIT_MAP = {ord(p): str(i) for i, p in enumerate("۰۱۲۳۴۵۶۷۸۹")}
_DIGIT_MAP.update({ord(a): str(i) for i, a in enumerate("٠١٢٣٤٥٦٧٨٩")})


def _normalize(text):
    value = str(text or "")
    value = value.translate(_DIGIT_MAP)
    for source, target in (("\u200c", " "), ("\u200f", ""), ("\u200e", ""),
                           ("\ufe0f", ""), ("ي", "ی"), ("ك", "ک")):
        value = value.replace(source, target)
    return " ".join(value.split()).lower()


def resolve(text):
    """ورودی کاربر را به آیتم تبدیل می‌کند.

    هم شمارهٔ فهرست (۱ تا ۳۲) پذیرفته می‌شود، هم شناسهٔ انگلیسی، هم نام
    فارسی/انگلیسی آیتم. اگر چیزی پیدا نشود ``None``.
    """
    cleaned = _normalize(text)
    if not cleaned:
        return None

    items = all_items()

    if cleaned.isdigit():
        index = int(cleaned)
        if 1 <= index <= len(items):
            return items[index - 1]
        return None

    for item in items:
        if _normalize(item["id"]) == cleaned:
            return item
    for item in items:
        for alias in item.get("aliases", ()):
            if _normalize(alias) == cleaned:
                return item
    return None


def title_item_for(text):
    """اگر متن دقیقاً یکی از لقب‌های فروشگاه باشد، همان آیتم را می‌دهد.

    برای قفل کردن لقب‌های فروشگاه لازم است: کاربر نباید بتواند لقبی را
    که فروشی است مستقیم در پروفایل بنویسد و از خرید فرار کند.
    """
    cleaned = _normalize(text)
    if not cleaned:
        return None
    for item in titles():
        if _normalize(item["title"]) == cleaned:
            return item
        if _normalize(item["typed"]) == cleaned:
            return item
        # بدون ایموجی و بدون فاصله هم نباید راه فرار باشد.
        if _normalize(item["typed"]).replace(" ", "") == cleaned.replace(" ", ""):
            return item
    return None


def shortfall(item, balance):
    """چند سکه کم دارد تا بتواند این آیتم را بخرد."""
    have = int(balance.get(item["coin_type"], 0))
    return max(0, int(item["price"]) - have)


def number_of(item_id):
    """شمارهٔ نمایشی یک آیتم در فهرست (از ۱)."""
    for index, item in enumerate(all_items(), 1):
        if item["id"] == str(item_id):
            return index
    return None
