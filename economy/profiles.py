"""👤 پروفایل کاربر — اطلاعات شخصی و آیتم‌های خریداری‌شده.

هر کاربر در هر گروه یک پروفایل جدا دارد؛ دقیقاً همان کلیدی که کیف پول
استفاده می‌کند (``"<chat_id>:<user_id>"``). داده‌ها داخل رکورد همان
کاربر در ``config/economy.json`` زیر کلید ``"profile"`` می‌نشینند، پس
خرید و کسر سکه در *یک* تراکنش اتمیک انجام می‌شود و هرگز پول کم نمی‌شود
بدون اینکه آیتم ثبت شود.

    profiles.register(chat_id, user_id, name="علی", city="شیراز", age=20)
    profiles.buy(chat_id, user_id, "badge_fox")
    profiles.get(chat_id, user_id)
"""
from datetime import datetime, timezone

from economy import catalog, name_filter, settings, storage
from economy.coins import accounts
from economy.transactions import ledger

MAX_NAME = 32
MAX_CITY = 32
MAX_NICKNAME = 32
MIN_AGE = 5
MAX_AGE = 120


class ProfileError(Exception):
    """خطای قابل‌انتظار پروفایل (ورودی نامعتبر، موجودی کم و…)."""


_DIGIT_MAP = {ord(p): str(i) for i, p in enumerate("𝟬𝟭𝟮𝟯𝟰𝟱𝟲𝟳𝟴𝟵")}
_DIGIT_MAP.update({ord(a): str(i) for i, a in enumerate("٠١٢٣٤٥٦٧٨٩")})


def _now():
    return datetime.now(timezone.utc).isoformat()


def _clean(text):
    value = str(text or "")
    for source, target in (("\u200c", " "), ("\u200f", ""), ("\u200e", ""),
                           ("\r", " "), ("\n", " "), ("\t", " ")):
        value = value.replace(source, target)
    return " ".join(value.split())


def _blank():
    return {
        "registered": False,
        "name": None,
        "city": None,
        "age": None,
        "nickname": None,
        "badges": [],
        "stars": 0,
        "titles": [],
        "created_at": None,
        "updated_at": None,
    }


def _record(data, key):
    """رکورد پروفایل داخل یک تراکنش باز؛ در صورت نبود ساخته می‌شود."""
    user = accounts._user(data, key)
    profile = user.get("profile")
    if not isinstance(profile, dict):
        profile = _blank()
        user["profile"] = profile
    for field, default in _blank().items():
        profile.setdefault(field, default)
    if not isinstance(profile.get("badges"), list):
        profile["badges"] = []
    if not isinstance(profile.get("titles"), list):
        profile["titles"] = []
    try:
        profile["stars"] = int(profile.get("stars", 0) or 0)
    except (TypeError, ValueError):
        profile["stars"] = 0
    return profile


# ---------------------------------------------------------------------------
# اعتبارسنجی ورودی‌های ثبت‌نام
# ---------------------------------------------------------------------------
def _reject_bad_words(value):
    """نام/لقب نامناسب را رد می‌کند.

    فیلتر داخل خودِ بستهٔ economy است تا استقلال بسته حفظ شود؛ این بسته
    عمداً هیچ چیزی از ``modules/`` import نمی‌کند.
    """
    ok, message = name_filter.check(value)
    if not ok:
        raise ProfileError(message)


def validate_name(text):
    value = _clean(text)
    if not value:
        raise ProfileError("اسم نمی‌تواند خالی باشد.")
    if len(value) > MAX_NAME:
        raise ProfileError(f"اسم نباید بیشتر از {MAX_NAME} نویسه باشد.")
    _reject_bad_words(value)
    return value


def validate_city(text):
    value = _clean(text)
    if not value:
        raise ProfileError("شهر نمی‌تواند خالی باشد.")
    if len(value) > MAX_CITY:
        raise ProfileError(f"شهر نباید بیشتر از {MAX_CITY} نویسه باشد.")
    return value


def validate_age(text):
    value = _clean(text).translate(_DIGIT_MAP)
    if not value.isdigit():
        raise ProfileError("سن باید فقط عدد باشد.")
    age = int(value)
    if age < MIN_AGE or age > MAX_AGE:
        raise ProfileError(f"سن باید بین {MIN_AGE} و {MAX_AGE} باشد.")
    return age


def validate_nickname(text, *, owned_titles=()):
    """``None`` یعنی کاربر لقب نمی‌خواهد.

    لقب‌هایی که در فروشگاه فروخته می‌شوند قفل‌اند: تا وقتی کاربر آن آیتم
    را نخریده باشد نمی‌تواند همان متن را دستی به‌عنوان لقب ثبت کند،
    وگرنه خرید بی‌معنا می‌شد.
    """
    value = _clean(text)
    if not value or value.translate(_DIGIT_MAP) == "0":
        return None
    if value in {"ندارم", "رد", "بیخیال", "بی خیال", "خالی", "-"}:
        return None
    if len(value) > MAX_NICKNAME:
        raise ProfileError(f"لقب نباید بیشتر از {MAX_NICKNAME} نویسه باشد.")
    _reject_bad_words(value)

    locked = catalog.title_item_for(value)
    if locked is not None and locked["id"] not in set(owned_titles):
        price = str(catalog.TITLE_PRICE).translate(
            {ord(str(i)): p for i, p in enumerate("𝟬𝟭𝟮𝟯𝟰𝟱𝟲𝟳𝟴𝟵")})
        raise ProfileError(
            f"لقب «{locked['title']}» یکی از آیتم‌های فروشگاه است.\n"
            f"برای استفاده از آن باید ابتدا آن را بخرید ({price} برنز)."
        )
    return value


# ---------------------------------------------------------------------------
# خواندن
# ---------------------------------------------------------------------------
def get(chat_id, user_id):
    """کپی فقط-خواندنی از پروفایل؛ همیشه یک dict کامل برمی‌گرداند."""
    fields = storage.user_fields(
        accounts.user_key(chat_id, user_id), ("profile",)
    ) or {}
    profile = _blank()
    stored = fields.get("profile")
    if isinstance(stored, dict):
        for field in profile:
            if field in stored:
                profile[field] = stored[field]
    if not isinstance(profile.get("badges"), list):
        profile["badges"] = []
    if not isinstance(profile.get("titles"), list):
        profile["titles"] = []
    try:
        profile["stars"] = int(profile.get("stars", 0) or 0)
    except (TypeError, ValueError):
        profile["stars"] = 0
    profile["badges"] = [str(item) for item in profile["badges"]]
    profile["titles"] = [str(item) for item in profile["titles"]]
    return profile


def is_registered(chat_id, user_id):
    return bool(get(chat_id, user_id)["registered"])


def owned_badges(chat_id, user_id):
    """نشان‌های خریداری‌شده به ترتیب خرید (اولین خرید، اول فهرست)."""
    owned = []
    for item_id in get(chat_id, user_id)["badges"]:
        item = catalog.get_badge(item_id)
        if item:
            owned.append(item)
    return owned


def first_badge(chat_id, user_id):
    """اولین نشان خریداری‌شده، یا ``None``."""
    owned = owned_badges(chat_id, user_id)
    return owned[0] if owned else None


def stars(chat_id, user_id):
    return int(get(chat_id, user_id)["stars"])


def owns(chat_id, user_id, item_id):
    profile = get(chat_id, user_id)
    item = catalog.get(item_id)
    if item is None:
        return False
    if item["kind"] == catalog.KIND_BADGE:
        return item["id"] in profile["badges"]
    if item["kind"] == catalog.KIND_TITLE:
        return item["id"] in profile["titles"]
    if item["kind"] == catalog.KIND_STAR:
        return profile["stars"] >= int(item["level"])
    return False


# ---------------------------------------------------------------------------
# نوشتن
# ---------------------------------------------------------------------------
def register(chat_id, user_id, *, name, city, age, nickname=None):
    """ثبت اطلاعات اولیه. ورودی‌ها اعتبارسنجی می‌شوند."""
    clean_name = validate_name(name)
    clean_city = validate_city(city)
    clean_age = validate_age(age)
    clean_nick = validate_nickname(
        nickname, owned_titles=get(chat_id, user_id)["titles"])

    key = accounts.user_key(chat_id, user_id)
    with storage.transaction() as data:
        profile = _record(data, key)
        profile["registered"] = True
        profile["name"] = clean_name
        profile["city"] = clean_city
        profile["age"] = clean_age
        profile["nickname"] = clean_nick
        profile["created_at"] = profile.get("created_at") or _now()
        profile["updated_at"] = _now()
        return dict(profile)


def update(chat_id, user_id, **fields):
    """ویرایش تکی فیلدها؛ فقط فیلدهای شناخته‌شده پذیرفته می‌شوند."""
    owned = get(chat_id, user_id)["titles"]
    validators = {
        "name": validate_name,
        "city": validate_city,
        "age": validate_age,
        "nickname": lambda value: validate_nickname(value,
                                                    owned_titles=owned),
    }
    cleaned = {}
    for field, value in fields.items():
        if field not in validators:
            raise ProfileError(f"فیلد ناشناخته: {field}")
        cleaned[field] = validators[field](value)

    key = accounts.user_key(chat_id, user_id)
    with storage.transaction() as data:
        profile = _record(data, key)
        profile.update(cleaned)
        profile["updated_at"] = _now()
        return dict(profile)


def buy(chat_id, user_id, item_id, *, reference=None):
    """خرید یک آیتم پروفایل — اتمیک.

    خروجی ``(item, balance, profile)``. اگر آیتم ناشناخته باشد، قبلاً
    خریداری شده باشد یا موجودی کم باشد ``ProfileError`` می‌دهد.
    """
    item = catalog.get(item_id)
    if item is None:
        raise ProfileError("چنین آیتمی وجود ندارد.")

    key = accounts.user_key(chat_id, user_id)
    coin_type = item["coin_type"]
    price = int(item["price"])

    with storage.transaction() as data:
        if ledger.is_duplicate(data, key, reference):
            user = accounts._user(data, key)
            return (dict(item), accounts._snapshot_balance(user),
                    dict(_record(data, key)))

        profile = _record(data, key)

        if item["kind"] == catalog.KIND_BADGE:
            if item["id"] in profile["badges"]:
                raise ProfileError("این نشان را قبلاً خریده‌اید.")
        elif item["kind"] == catalog.KIND_TITLE:
            if item["id"] in profile["titles"]:
                raise ProfileError("این لقب را قبلاً خریده‌اید.")
        elif item["kind"] == catalog.KIND_STAR:
            if profile["stars"] >= int(item["level"]):
                raise ProfileError("این سطح را قبلاً دارید.")

        user = accounts._user(data, key)
        current = int(user.get(coin_type, 0))
        if current < price:
            raise ProfileError(
                f"موجودی {settings.COIN_LABELS[coin_type]} کافی نیست: "
                f"{current} < {price}"
            )
        user[coin_type] = current - price
        total = accounts._refresh_total(data, user)

        # اعمال اثر آیتم — بلافاصله در پروفایل دیده می‌شود.
        if item["kind"] == catalog.KIND_BADGE:
            profile["badges"].append(item["id"])
        elif item["kind"] == catalog.KIND_TITLE:
            profile["titles"].append(item["id"])
            # لقب خریداری‌شده جای لقب فعلی می‌نشیند.
            profile["nickname"] = item["title"]
        elif item["kind"] == catalog.KIND_STAR:
            profile["stars"] = int(item["level"])
        profile["updated_at"] = _now()

        ledger.record(
            data, key, ledger.KIND_PURCHASE, {coin_type: -price},
            reference=reference, note=item["label"],
            balance_after=accounts._snapshot_balance(user),
            total_value=total,
        )
        return (dict(item), accounts._snapshot_balance(user), dict(profile))


def delete(chat_id, user_id):
    """حذف پروفایل: اطلاعات شخصی پاک می‌شود.

    آیتم‌های خریداری‌شده (نشان، سطح، لقب) *نگه داشته* می‌شوند چون با
    سکهٔ واقعی خریده شده‌اند و پاک کردنشان یعنی سوزاندن پول کاربر.
    کیف پول هم دست‌نخورده می‌ماند.
    """
    key = accounts.user_key(chat_id, user_id)
    with storage.transaction() as data:
        profile = _record(data, key)
        profile["registered"] = False
        profile["name"] = None
        profile["city"] = None
        profile["age"] = None
        profile["nickname"] = None
        profile["updated_at"] = _now()
        return dict(profile)


def reset(chat_id, user_id):
    """پاک کردن کامل پروفایل همراه آیتم‌ها — فقط برای تست و پشتیبانی."""
    key = accounts.user_key(chat_id, user_id)
    with storage.transaction() as data:
        user = accounts._user(data, key)
        user["profile"] = _blank()
        return dict(user["profile"])
