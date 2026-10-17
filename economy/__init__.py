"""💰 سیستم اقتصاد — ماژول کاملاً مستقل.

این بسته هیچ وابستگی‌ای به بازی‌ها یا قابلیت‌های فعلی ربات ندارد و هیچ
فایل موجود را تغییر نمی‌دهد. بازی‌ها *فقط* از توابع همین فایل استفاده
می‌کنند و هرگز نباید مستقیماً فایل دیتابیس را باز کنند.

    from economy import add_bronze, get_balance

    add_bronze(user_id, 5, reference="riddle:42")

ساختار:
    economy/settings.py        ارزش سکه‌ها و نرخ تبدیل (قابل تنظیم)
    economy/storage.py         تراکنش اتمیک + نوشتن اتمیک روی دیسک
    economy/coins/             موجودی، تبدیل، انتقال
    economy/shop/              فروشگاه
    economy/ranking/           رتبه‌بندی بر پایهٔ ارزش کل
    economy/transactions/      تاریخچه
"""
from economy import ranking as _ranking
from economy import settings, shop, storage
from economy import catalog
from economy import directory
from economy import game_progress
from economy import profiles
from economy import rewards
from economy import upgrade_migration
from economy.coins import accounts as _accounts
from economy import activity
from economy.activity import (
    daily_ranking,
    record_message,
    settle_previous_days,
)
from economy.coins.accounts import (
    BRONZE,
    COIN_TYPES,
    GOLD,
    SILVER,
    EconomyError,
    add,
    add_bronze,
    add_gold,
    add_silver,
    calculate_total_value,
    convert_bronze,
    convert_silver,
    chat_aliases,
    chat_key,
    get_balance,
    get_profile,
    normalize_user_id,
    resolve_user_key,
    split_key,
    user_key,
    recalculate,
    set_name,
    recalculate_all,
    remove,
    remove_bronze,
    remove_gold,
    remove_silver,
    transfer,
)
from economy.daily import claim_daily, daily_status
from economy.ranking.board import get_rank, leaderboard, ranked_users
from economy.transactions.ledger import history as transaction_history

__all__ = [
    # انواع سکه
    "BRONZE", "SILVER", "GOLD", "COIN_TYPES", "EconomyError",
    # افزودن و کسر
    "add", "add_bronze", "add_silver", "add_gold",
    "remove", "remove_bronze", "remove_silver", "remove_gold",
    # تبدیل و انتقال
    "convert_bronze", "convert_silver", "transfer",
    # ارزش و موجودی
    "get_balance", "get_profile", "set_name", "chat_key", "chat_aliases",
    "normalize_user_id", "resolve_user_key", "split_key", "user_key",
    "calculate_total_value", "recalculate", "recalculate_all",
    # فعالیت روزانه
    "record_message", "settle_previous_days", "daily_ranking", "activity",
    # رتبه‌بندی
    "get_rank", "leaderboard", "ranked_users",
    # جایزهٔ روزانه
    "claim_daily", "daily_status",
    # تاریخچه و فروشگاه
    "transaction_history", "shop",
    # پروفایل و فهرست آیتم‌ها
    "profiles", "catalog",
    # جدول جایزهٔ بازی‌ها
    "rewards", "award_game",
    # دفترچهٔ یوزرنیم
    "directory",
    # پیشرفت دائمی بازی‌ها
    "game_progress",
    # جبران تبدیل‌های قدیمی
    "upgrade_migration",
    # زیرساخت
    "settings", "storage", "flush",
]


# ---------------------------------------------------------------------------
# API مخصوص بازی‌ها
# ---------------------------------------------------------------------------
def flush():
    """نوشتن تغییرات معوق اقتصاد روی دیسک (از حلقهٔ دوره‌ای)."""
    return storage.flush()


def award(chat_id, user_id, amount, coin_type=BRONZE, *, reference=None,
          note=None, name=None, win=True):
    """تنها راه پرداخت جایزه از سمت بازی‌ها.

    ``reference`` یکتا بدهید (مثل ``"vampire:chat:session"``) تا یک جایزه
    هرگز دو بار پرداخت نشود.
    """
    return _accounts.add(
        chat_id, user_id, coin_type, amount,
        kind="reward", reference=reference, note=note, name=name, win=win,
    )


def award_game(chat_id, user_id, game, *, reference=None, name=None,
               amount=None, win=True):
    """جایزهٔ یک بازی را با *نوع سکهٔ درست* پرداخت می‌کند.

    نوع سکه از ``economy.rewards`` می‌آید، نه از فراخوان؛ پس هیچ بازی‌ای
    نمی‌تواند با نوع اشتباه سکه ثبت شود. ``amount`` فقط برای بازی‌هایی
    مثل جعبهٔ شانسی است که مقدارشان متغیر است.
    """
    coin_type = rewards.coin_for(game)
    value = rewards.amount_for(game) if amount is None else int(amount)
    if value <= 0:
        return get_balance(chat_id, user_id)
    return _accounts.add(
        chat_id, user_id, coin_type, value,
        kind="reward", reference=reference, note=rewards.label_for(game),
        name=name, win=win,
    )


def spend(chat_id, user_id, amount, coin_type=BRONZE, *, reference=None,
          note=None):
    """کسر سکه برای بازی‌ها؛ در صورت کمبود موجودی خطا می‌دهد."""
    return _accounts.remove(
        chat_id, user_id, coin_type, amount,
        kind="spend", reference=reference, note=note,
    )


def reset_all():
    """پاک‌سازی کامل اقتصاد — فقط برای تست."""
    storage.reset_all()
    settings.reset_cache()
    shop.store._cache = None
    shop.store._cache_mtime = None
