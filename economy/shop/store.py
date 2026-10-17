"""🛒 زیرساخت فروشگاه.

فعلاً هیچ آیتمی از پیش تعریف نشده؛ فقط زیرساخت آماده است تا بعداً
آیتم‌ها اضافه شوند. آیتم‌ها در ``config/economy_shop.json`` نگهداری
می‌شوند و کاملاً از فایل موجودی جدا هستند.

خرید اتمیک است: کسر سکه، ثبت خرید و ثبت تاریخچه همه در یک تراکنش انجام
می‌شوند، پس هرگز پول کم شود ولی آیتم ثبت نشود.
"""
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from modules.runtime_paths import runtime_config_file

from economy import settings, storage
from economy.coins import accounts
from economy.transactions import ledger

ITEMS_FILE = runtime_config_file("economy_shop.json")

_cache = None
_cache_mtime = None


class ShopError(Exception):
    """خطای قابل‌انتظار فروشگاه."""


def _mtime():
    try:
        return ITEMS_FILE.stat().st_mtime_ns
    except OSError:
        return None


def _load():
    global _cache, _cache_mtime
    mtime = _mtime()
    if _cache is not None and mtime == _cache_mtime:
        return _cache
    if mtime is None:
        _cache = {}
    else:
        try:
            raw = json.loads(ITEMS_FILE.read_text(encoding="utf-8"))
            _cache = raw if isinstance(raw, dict) else {}
        except (OSError, ValueError):
            _cache = {}
    _cache_mtime = mtime
    return _cache


def _save(data):
    global _cache, _cache_mtime
    ITEMS_FILE.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_path = tempfile.mkstemp(dir=str(ITEMS_FILE.parent),
                                         suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, ITEMS_FILE)
    except BaseException:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise
    _cache = data
    _cache_mtime = _mtime()


# ---------------------------------------------------------------------------
# مدیریت آیتم‌ها
# ---------------------------------------------------------------------------
def add_item(item_id, title, price, coin_type=settings.BRONZE, *,
             description=None, stock=None, payload=None):
    """آیتم جدید ثبت یا آیتم موجود را به‌روزرسانی می‌کند."""
    if coin_type not in settings.COIN_TYPES:
        raise ShopError(f"نوع سکه نامعتبر است: {coin_type!r}")
    if isinstance(price, bool) or not isinstance(price, int) or price <= 0:
        raise ShopError("قیمت باید عدد صحیح مثبت باشد.")
    if not str(item_id).strip():
        raise ShopError("شناسهٔ آیتم نمی‌تواند خالی باشد.")

    data = dict(_load())
    item = {
        "id": str(item_id),
        "title": str(title),
        "price": int(price),
        "coin_type": coin_type,
    }
    if description:
        item["description"] = str(description)
    if stock is not None:
        if isinstance(stock, bool) or not isinstance(stock, int) or stock < 0:
            raise ShopError("موجودی انبار باید عدد صحیح غیرمنفی باشد.")
        item["stock"] = int(stock)
    if payload is not None:
        item["payload"] = payload
    data[str(item_id)] = item
    _save(data)
    return dict(item)


def remove_item(item_id):
    data = dict(_load())
    if str(item_id) not in data:
        return False
    del data[str(item_id)]
    _save(data)
    return True


def clear_items():
    _save({})


def get_item(item_id):
    item = _load().get(str(item_id))
    return dict(item) if item else None


def list_items():
    """همهٔ آیتم‌ها، مرتب بر اساس قیمت."""
    return sorted((dict(item) for item in _load().values()),
                  key=lambda item: (item.get("coin_type", ""),
                                    item.get("price", 0)))


# ---------------------------------------------------------------------------
# خرید
# ---------------------------------------------------------------------------
def can_afford(chat_id, user_id, item_id):
    item = get_item(item_id)
    if not item:
        return False
    balance = accounts.get_balance(chat_id, user_id)
    return balance.get(item["coin_type"], 0) >= item["price"]


def buy(chat_id, user_id, item_id, *, reference=None):
    """خرید یک آیتم — اتمیک.

    خروجی ``(item, balance)``. اگر موجودی کافی نباشد یا آیتم تمام شده
    باشد ``ShopError`` می‌دهد.
    """
    item = get_item(item_id)
    if not item:
        raise ShopError("چنین آیتمی در فروشگاه وجود ندارد.")

    stock = item.get("stock")
    if stock is not None and stock <= 0:
        raise ShopError("موجودی این آیتم تمام شده است.")

    key = accounts.user_key(chat_id, user_id)
    coin_type = item["coin_type"]
    price = int(item["price"])

    with storage.transaction() as data:
        if ledger.is_duplicate(data, key, reference):
            user = accounts._user(data, key)
            return dict(item), accounts._snapshot_balance(user)

        user = accounts._user(data, key)
        current = int(user.get(coin_type, 0))
        if current < price:
            raise ShopError(
                f"موجودی {settings.COIN_LABELS[coin_type]} کافی نیست: "
                f"{current} < {price}"
            )
        user[coin_type] = current - price
        total = accounts._refresh_total(data, user)

        owned = user.setdefault("purchases", [])
        owned.append({
            "item_id": item["id"],
            "title": item["title"],
            "price": price,
            "coin_type": coin_type,
            "at": datetime.now(timezone.utc).isoformat(),
        })
        # تاریخچهٔ نمایشی خرید محدود است؛ مالکیت آیتم‌های پروفایل در
        # profile نگه‌داری می‌شود و با این هرس از بین نمی‌رود.
        if len(owned) > 500:
            del owned[:-500]
        ledger.record(
            data, key, ledger.KIND_PURCHASE, {coin_type: -price},
            reference=reference, note=item["title"],
            balance_after=accounts._snapshot_balance(user),
            total_value=total,
        )
        balance = accounts._snapshot_balance(user)

    # کم کردن انبار بعد از موفقیت پرداخت.
    if stock is not None:
        stored = dict(_load())
        record = stored.get(item["id"])
        if record and record.get("stock") is not None:
            record["stock"] = max(0, int(record["stock"]) - 1)
            _save(stored)

    return dict(item), balance


def purchases(chat_id, user_id):
    """فهرست خریدهای یک کاربر."""
    fields = storage.user_fields(
        accounts.user_key(chat_id, user_id), ("purchases",)
    ) or {}
    return [dict(entry) for entry in (fields.get("purchases") or [])]
