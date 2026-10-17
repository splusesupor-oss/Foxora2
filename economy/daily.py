"""🎁 جایزهٔ روزانه.

مقدار جایزه و فاصلهٔ زمانی از تنظیمات خوانده می‌شود. دریافت اتمیک است و
تا پایان دورهٔ انتظار دوباره پرداخت نمی‌شود.
"""
from datetime import datetime, timezone

from economy import settings, storage
from economy.coins import accounts
from economy.transactions import ledger


def _now():
    return datetime.now(timezone.utc)


def _parse(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def daily_status(chat_id, user_id, now=None):
    """``(available, seconds_left)`` برای جایزهٔ روزانه."""
    moment = now or _now()
    user = storage.user_fields(
        accounts.user_key(chat_id, user_id), ("daily_claimed_at",)
    ) or {}
    last = _parse(user.get("daily_claimed_at"))
    cooldown = int(settings.get("DailyRewardCooldownSeconds"))
    if last is None:
        return True, 0
    elapsed = (moment - last).total_seconds()
    if elapsed >= cooldown:
        return True, 0
    return False, int(cooldown - elapsed)


def claim_daily(chat_id, user_id, *, now=None, reference=None):
    """جایزهٔ روزانه را پرداخت می‌کند.

    خروجی ``(granted, balance, seconds_left)``. اگر هنوز نوبت نرسیده
    باشد ``granted=False`` است و موجودی تغییر نمی‌کند.
    """
    moment = now or _now()
    key = accounts.user_key(chat_id, user_id)
    config = settings.load()
    cooldown = int(config["DailyRewardCooldownSeconds"])
    payout = {
        accounts.BRONZE: int(config["DailyRewardBronze"]),
        accounts.SILVER: int(config["DailyRewardSilver"]),
        accounts.GOLD: int(config["DailyRewardGold"]),
    }

    with storage.transaction() as data:
        user = accounts._user(data, key)
        last = _parse(user.get("daily_claimed_at"))
        if last is not None:
            elapsed = (moment - last).total_seconds()
            if elapsed < cooldown:
                return (False, accounts._snapshot_balance(user),
                        int(cooldown - elapsed))

        changes = {}
        for coin, amount in payout.items():
            if amount > 0:
                user[coin] = int(user.get(coin, 0)) + amount
                changes[coin] = amount
        user["daily_claimed_at"] = moment.astimezone(timezone.utc).isoformat()
        total = accounts._refresh_total(data, user)
        ledger.record(
            data, key, ledger.KIND_DAILY, changes,
            reference=reference, note="جایزه روزانه",
            balance_after=accounts._snapshot_balance(user),
            total_value=total,
        )
        return True, accounts._snapshot_balance(user), cooldown
