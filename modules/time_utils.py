"""منبعِ مرکزیِ زمانِ پروژه — منطقهٔ زمانیِ واقعیِ ربات (سروش/ایران).

همهٔ بخش‌ها (admin_tools، group_expiry، main و …) زمان و timezone را فقط
از همین ماژول می‌گیرند تا یک منبعِ واحد داشته باشیم و هیچ timezone یا ساعتِ
دیگری به‌صورت hardcode در جای دیگری از پروژه تکرار نشود.
"""
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    try:
        TEHRAN = ZoneInfo("Asia/Tehran")
    except ZoneInfoNotFoundError:
        TEHRAN = timezone(timedelta(hours=3, minutes=30))
except ImportError:  # pragma: no cover
    TEHRAN = timezone(timedelta(hours=3, minutes=30))


def now_local():
    """زمانِ فعلیِ واقعیِ ربات در منطقهٔ زمانیِ تهران (Asia/Tehran)."""
    return datetime.now(TEHRAN)
