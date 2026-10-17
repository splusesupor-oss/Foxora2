"""📈 رتبه‌بندی روزانهٔ پیام‌ها.

جایگزین ``record_message`` و ``settle_previous_days`` سیستم قدیمی سکه.
تعداد پیام هر کاربر در هر روز شمرده می‌شود و در پایان روز به سه نفر اول
جایزهٔ برنز داده می‌شود.

مثل بقیهٔ اقتصاد، همه چیز داخل ``storage.transaction()`` اتفاق می‌افتد و
جایزه با ``reference`` یکتا ثبت می‌گردد تا یک روز دو بار تسویه نشود.
"""
import json
from datetime import datetime, timedelta, timezone

from economy import settings, storage
from economy.coins import accounts
from economy.transactions import ledger

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    try:
        TEHRAN = ZoneInfo("Asia/Tehran")
    except ZoneInfoNotFoundError:
        TEHRAN = timezone(timedelta(hours=3, minutes=30))
except ImportError:  # pragma: no cover
    TEHRAN = timezone(timedelta(hours=3, minutes=30))

# جایزهٔ سه نفر اول هر روز، بر حسب برنز.
DAILY_RANK_REWARDS = (12, 8, 5)


def _today(now=None):
    moment = now or datetime.now(timezone.utc)
    return moment.astimezone(TEHRAN).date().isoformat()


def record_message(chat_id, user_id, name=None, *, now=None):
    """یک پیام برای شمارش روزانه ثبت می‌کند.

    این تابع در «هر پیام گروه» صدا زده می‌شود، پس مسیر داغ است.
    ``defer=True`` یعنی فقط حافظه به‌روز شود و نوشتن روی دیسک به حلقهٔ
    دوره‌ای سپرده شود — دقیقاً همان کاری که سیستم قدیمی با ``_DIRTY``
    می‌کرد. بدون این، هر پیام یک deepcopy و یک نوشتن کامل دیتابیس
    داشت که با بزرگ شدن دیتابیس به ده‌ها میلی‌ثانیه می‌رسید.
    """
    key = str(user_id)
    day = _today(now)
    with storage.transaction(defer=True) as data:
        daily = data.setdefault("daily_messages", {})
        group = daily.setdefault(day, {}).setdefault(str(chat_id), {})
        entry = group.setdefault(key, {"messages": 0})
        entry["messages"] = int(entry.get("messages", 0)) + 1
        if name:
            entry["name"] = str(name)
        return entry["messages"]


def message_count(chat_id, user_id, *, now=None):
    entry = storage.read_path(
        "daily_messages", _today(now), str(chat_id), str(user_id), default={}
    )
    return int(entry.get("messages", 0))


def daily_ranking(chat_id, limit=3, *, now=None):
    """رتبه‌بندی پیام‌های امروز در یک گروه."""
    users = storage.read_path(
        "daily_messages", _today(now), str(chat_id), default={}
    )
    rows = [
        {"user_id": key, "messages": int(value.get("messages", 0)),
         "name": value.get("name")}
        for key, value in users.items()
    ]
    rows.sort(key=lambda row: -row["messages"])
    return rows[:limit] if limit else rows


def settle_previous_days(*, now=None):
    """روزهای گذشتهٔ تسویه‌نشده را جایزه می‌دهد.

    خروجی فهرستی از ``(chat_id, user_id, bronze)``.
    """
    today = _today(now)
    awards = []
    with storage.transaction() as data:
        daily = data.setdefault("daily_messages", {})
        paid = set(data.setdefault("paid_days", []))
        for day in sorted(daily):
            if day >= today or day in paid:
                continue
            for chat_id, users in daily[day].items():
                ranking = sorted(
                    users.items(),
                    key=lambda item: -int(item[1].get("messages", 0)),
                )[:len(DAILY_RANK_REWARDS)]
                for index, (key, entry) in enumerate(ranking):
                    amount = DAILY_RANK_REWARDS[index]
                    reference = f"daily_rank:{day}:{chat_id}:{key}"
                    wallet = accounts.user_key(chat_id, key)
                    if ledger.is_duplicate(data, wallet, reference):
                        continue
                    user = accounts._user(data, accounts.user_key(chat_id, key))
                    user[accounts.BRONZE] = (
                        int(user.get(accounts.BRONZE, 0)) + amount
                    )
                    total = accounts._refresh_total(data, user)
                    ledger.record(
                        data, wallet, ledger.KIND_REWARD,
                        {accounts.BRONZE: amount},
                        reference=reference,
                        note=f"رتبه {index + 1} پیام‌های روز {day}",
                        balance_after=accounts._snapshot_balance(user),
                        total_value=total,
                    )
                    awards.append((chat_id, key, amount))
            paid.add(day)
        data["paid_days"] = sorted(paid)
        # روزهای خیلی قدیمی پاک می‌شوند تا فایل بی‌نهایت رشد نکند.
        cutoff = (
            (now or datetime.now(timezone.utc)).astimezone(TEHRAN).date()
            - timedelta(days=14)
        ).isoformat()
        stale_days = {d: daily[d] for d in daily if d < cutoff}
        if stale_days:
            # 🗄️ قبل از حذف، به آرشیو سرد منتقل می‌شوند تا تاریخچه از
            # بین نرود؛ خطای آرشیو هرگز تسویه را نمی‌شکند.
            _archive_days(stale_days)
        for day in stale_days:
            del daily[day]
        data["paid_days"] = [d for d in data["paid_days"] if d >= cutoff]
    return awards


def _archive_days(stale_days):
    """روزهای قدیمی را خارج از مسیر داغ آرشیو می‌کند.

    در Backend جدید، آرشیو داخل جدول SQLite می‌رود و دیگر یک JSON
    تجمعی و رو‌به‌رشد بازنویسی نمی‌شود. حالت JSON قدیمی فقط برای
    سازگاری اضطراری حفظ شده است.
    """
    try:
        if storage.backend_name() == "sqlite":
            storage.archive_daily_days(stale_days)
            return
        archive_file = (
            storage.DATA_FILE.parent / "archive" / "coins_daily_archive.json"
        )
        archive_file.parent.mkdir(parents=True, exist_ok=True)
        if archive_file.exists():
            try:
                archive = json.loads(
                    archive_file.read_text(encoding="utf-8"))
            except Exception:
                archive = {}
        else:
            archive = {}
        for day, groups in stale_days.items():
            archive.setdefault(day, {}).update(groups)
        archive_file.write_text(
            json.dumps(archive, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
    except Exception:
        pass
