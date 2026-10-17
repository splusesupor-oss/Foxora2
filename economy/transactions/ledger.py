"""🧾 دفتر تراکنش‌ها.

هر تغییر موجودی اینجا با زمان دقیق ثبت می‌شود. ثبت همیشه *داخل* همان
تراکنش اتمیکِ تغییر موجودی انجام می‌گیرد، پس هرگز موجودی عوض شود ولی
تراکنش ثبت نشود (یا برعکس).

جلوگیری از ثبت دوباره: اگر ``reference`` داده شود، همان مرجع برای همان
کاربر فقط یک بار پذیرفته می‌شود. بازی‌ها با این مکانیزم می‌توانند مطمئن
شوند یک جایزه دو بار پرداخت نمی‌شود.
"""
from datetime import datetime, timezone

from economy import storage

KIND_REWARD = "reward"          # جایزهٔ بازی
KIND_RECEIVE = "receive"        # دریافت عمومی
KIND_SPEND = "spend"            # خرج کردن
KIND_CONVERT = "convert"        # تبدیل سکه
KIND_TRANSFER_IN = "transfer_in"
KIND_TRANSFER_OUT = "transfer_out"
KIND_DAILY = "daily"            # جایزهٔ روزانه
KIND_PURCHASE = "purchase"      # خرید از فروشگاه

KINDS = frozenset({
    KIND_REWARD, KIND_RECEIVE, KIND_SPEND, KIND_CONVERT,
    KIND_TRANSFER_IN, KIND_TRANSFER_OUT, KIND_DAILY, KIND_PURCHASE,
})

MAX_HISTORY = 500


def _now():
    return datetime.now(timezone.utc).isoformat()


def is_duplicate(data, user_key, reference):
    """آیا این مرجع قبلاً برای همین کاربر ثبت شده است."""
    if not reference:
        return False
    user = data.get("users", {}).get(user_key)
    if not user:
        return False
    return str(reference) in set(user.get("references", []))


def record(data, user_key, kind, changes, *, reference=None, note=None,
           counterparty=None, balance_after=None, total_value=None):
    """یک ردیف تاریخچه اضافه می‌کند.

    ``data`` باید همان dict داخل ``storage.transaction()`` باشد.
    """
    if kind not in KINDS:
        raise ValueError(f"نوع تراکنش نامعتبر است: {kind!r}")

    user = data.setdefault("users", {}).setdefault(user_key, {})
    entries = user.setdefault("transactions", [])
    entry = {
        "id": storage.next_sequence(data),
        "kind": kind,
        "at": _now(),
        "changes": {k: int(v) for k, v in dict(changes or {}).items() if v},
    }
    if reference:
        entry["reference"] = str(reference)
        user.setdefault("references", []).append(str(reference))
        # فهرست مراجع نباید بی‌نهایت رشد کند.
        if len(user["references"]) > MAX_HISTORY * 2:
            del user["references"][:-MAX_HISTORY]
    if note:
        entry["note"] = str(note)
    if counterparty is not None:
        entry["counterparty"] = str(counterparty)
    if balance_after is not None:
        entry["balance_after"] = dict(balance_after)
    if total_value is not None:
        entry["total_value"] = int(total_value)

    entries.append(entry)
    if len(entries) > MAX_HISTORY:
        del entries[:-MAX_HISTORY]
    return entry


def history(chat_id, user_id, limit=20, kind=None):
    """تاریخچهٔ یک کاربر در همین گروه، تازه‌ترین اول."""
    from economy.coins.accounts import user_key

    fields = storage.user_fields(
        user_key(chat_id, user_id), ("transactions",)
    ) or {}
    entries = fields.get("transactions") or []
    if kind is not None:
        entries = [e for e in entries if e.get("kind") == kind]
    ordered = sorted(entries, key=lambda e: e.get("id", 0), reverse=True)
    return ordered[:limit] if limit else ordered
