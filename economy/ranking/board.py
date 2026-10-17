"""🏆 جدول رتبه‌بندی.

مرتب‌سازی *فقط* بر اساس ``total_coin_value`` است، نه تعداد سکه. اگر دو
کاربر ارزش برابر داشته باشند، کسی که زودتر به آن مقدار رسیده رتبهٔ
بالاتری می‌گیرد؛ این با ``value_reached_seq`` سنجیده می‌شود که یک
شمارندهٔ یکنواخت صعودی است و در لحظهٔ تغییر ارزش ثبت می‌گردد.
"""
from economy import storage
from economy.coins import accounts


def _rows(chat_id):
    """فقط کیف پول‌های همین گروه."""
    wanted = accounts.chat_key(chat_id)
    fields = (
        accounts.BRONZE, accounts.SILVER, accounts.GOLD,
        "total_coin_value", "name", "wins", "value_reached_seq",
        "value_reached_at",
    )
    rows = []
    for key, user in storage.user_records(fields):
        group, user_id = accounts.split_key(key)
        if group != wanted:
            continue
        is_owner = accounts.is_owner_silver_group(chat_id, user_id)
        effective_user = {**user, accounts.SILVER: accounts.OWNER_SILVER} if is_owner else user
        value = accounts.compute_total_value(effective_user)
        rows.append({
            "user_id": user_id,
            "total_coin_value": value,
            "bronze": int(user.get(accounts.BRONZE, 0)),
            "silver": accounts.OWNER_SILVER if is_owner else int(user.get(accounts.SILVER, 0)),
            "gold": int(user.get(accounts.GOLD, 0)),
            "name": user.get("name"),
            "wins": int(user.get("wins", 0)),
            "reached_seq": int(user.get("value_reached_seq", 0)),
            "reached_at": user.get("value_reached_at"),
        })
    return rows


def ranked_users(chat_id, limit=None, include_zero=False):
    """کاربران «همین گروه»، مرتب‌شده از ارزشمندترین.

    ``include_zero=False`` کاربران با ارزش صفر را کنار می‌گذارد.
    """
    rows = _rows(chat_id)
    if not include_zero:
        rows = [row for row in rows if row["total_coin_value"] > 0]
    # ارزش بیشتر بالاتر؛ در تساوی، مهر زمانی کوچک‌تر (زودتر) بالاتر.
    rows.sort(key=lambda row: (-row["total_coin_value"], row["reached_seq"]))
    for index, row in enumerate(rows, 1):
        row["rank"] = index
    return rows[:limit] if limit else rows


def leaderboard(chat_id, limit=10, include_zero=False):
    return ranked_users(chat_id, limit=limit, include_zero=include_zero)


def get_rank(chat_id, user_id, include_zero=False):
    """رتبهٔ یک کاربر در همین گروه، یا ``None``."""
    target = str(user_id)
    for row in ranked_users(chat_id, include_zero=include_zero):
        if row["user_id"] == target:
            return row["rank"]
    return None
