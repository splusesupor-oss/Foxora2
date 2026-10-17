from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from persiantools.jdatetime import JalaliDateTime

from foxbot.storage import BotStore

TEHRAN = ZoneInfo("Asia/Tehran")
DURATION_DAYS = {
    "۵ روز": 5,
    "یک هفته": 7,
    "دو هفته": 14,
    "یک ماه": 29,
}
DURATION_COMMANDS = frozenset(DURATION_DAYS)
MONTH_NAMES = (
    "فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
    "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند",
)
_MATH_DIGITS = str.maketrans("0123456789", "𝟬𝟭𝟮𝟯𝟰𝟱𝟲𝟳𝟴𝟵")
_NORMALIZE = str.maketrans({"ي": "ی", "ك": "ک", "\u200c": " ", "\u200e": "", "\u200f": "", "\ufeff": ""})

SET_HEADER = "✅ تاریخ انقضای گروه تنظیم شد."
ACTIVATED_LABEL = "📅 تاریخ فعال‌سازی:"
EXPIRES_LABEL = "⏳ تاریخ انقضا:"
EXPIRED_MESSAGE = "⛔ مدت زمان فعال بودن گروه به پایان رسید و گروه به‌صورت خودکار غیرفعال شد"


def normalize_expiry_command(text: str | None) -> str:
    return " ".join(str(text or "").translate(_NORMALIZE).split())


def match_expiry_command(text: str | None) -> str | None:
    value = normalize_expiry_command(text)
    return value if value in DURATION_DAYS else None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_datetime(value: Any) -> datetime | None:
    try:
        return _as_utc(datetime.fromisoformat(str(value)))
    except (TypeError, ValueError):
        return None


def get_expiry(store: BotStore, group_id: str | int) -> dict[str, Any] | None:
    record = store.get_group_state(group_id, "expiry")
    return dict(record) if isinstance(record, dict) and record else None


def set_expiry(
    store: BotStore,
    group_id: str | int,
    command: str,
    *,
    title: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    normalized = match_expiry_command(command)
    if normalized is None:
        return None
    started = _as_utc(now or datetime.now(UTC))
    ends = started + timedelta(days=DURATION_DAYS[normalized])
    previous = get_expiry(store, group_id) or {}
    record = {
        "generation": uuid4().hex,
        "command": normalized,
        "days": DURATION_DAYS[normalized],
        "activated_at": started.isoformat(),
        "expires_at": ends.isoformat(),
        "notified": False,
        "title": str(title or previous.get("title") or "").strip(),
    }
    store.set_group_state(group_id, "expiry", record)
    return record


def is_expired(
    store: BotStore, group_id: str | int, *, now: datetime | None = None
) -> bool:
    record = get_expiry(store, group_id)
    if record is None:
        return False
    end = _parse_datetime(record.get("expires_at"))
    if end is None:
        return False
    return end <= _as_utc(now or datetime.now(UTC))


def due_expiries(
    store: BotStore, *, now: datetime | None = None
) -> list[tuple[str, dict[str, Any]]]:
    moment = _as_utc(now or datetime.now(UTC))
    due: list[tuple[str, dict[str, Any]]] = []
    for group_id, value in store.list_group_states("expiry"):
        if not isinstance(value, dict) or value.get("notified"):
            continue
        end = _parse_datetime(value.get("expires_at"))
        if end is None or end > moment:
            continue
        retry_at = _parse_datetime(value.get("next_notice_at"))
        if retry_at is not None and retry_at > moment:
            continue
        due.append((str(group_id), dict(value)))
    return due


def remaining_status(
    store: BotStore,
    group_id: str | int,
    *,
    now: datetime | None = None,
) -> tuple[str | None, str | None]:
    record = get_expiry(store, group_id)
    if record is None:
        return None, None
    end = _parse_datetime(record.get("expires_at"))
    if end is None:
        return None, None
    seconds = int((end - _as_utc(now or datetime.now(UTC))).total_seconds())
    if seconds <= 0:
        return "expired", "منقضی شده"
    days, rest = divmod(seconds, 86_400)
    hours, rest = divmod(rest, 3_600)
    minutes = rest // 60
    parts: list[str] = []
    if days:
        parts.append(f"{days} روز")
    if hours or days:
        parts.append(f"{hours} ساعت")
    if not days and not hours:
        parts.append(f"{minutes} دقیقه")
    text = " و ".join(parts).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))
    return "active", text


def _u16(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _format_jalali_datetime(moment: datetime) -> str:
    local = _as_utc(moment).astimezone(TEHRAN)
    jalali = JalaliDateTime(local, locale="fa")
    date_part = (
        f"{jalali.year:04d}/{jalali.month:02d}/{jalali.day:02d} "
        f"({jalali.day} {MONTH_NAMES[jalali.month - 1]} {jalali.year})"
    )
    return f"{date_part} - ساعت {local.hour:02d}:{local.minute:02d}".translate(
        _MATH_DIGITS
    )


def build_confirmation(
    activated_at: datetime, expires_at: datetime
) -> tuple[str, list[tuple[str, int, int]]]:
    activated = _format_jalali_datetime(activated_at)
    expires = _format_jalali_datetime(expires_at)
    text = (
        f"{SET_HEADER}\n\n"
        f"{ACTIVATED_LABEL}\n{activated}\n\n"
        f"{EXPIRES_LABEL}\n{expires}"
    )
    spans: list[tuple[str, int, int]] = []
    for value in (activated, expires):
        offset = _u16(text[: text.index(value)])
        length = _u16(value)
        spans.extend((("blockquote", offset, length), ("bold", offset, length)))
    return text, spans


def build_expired_notice() -> tuple[str, list[tuple[str, int, int]]]:
    return EXPIRED_MESSAGE, [("bold", 0, _u16(EXPIRED_MESSAGE))]


def same_expiry_generation(
    current: dict[str, Any] | None, expected: dict[str, Any]
) -> bool:
    if not isinstance(current, dict):
        return False
    current_generation = current.get("generation")
    expected_generation = expected.get("generation")
    if current_generation is not None or expected_generation is not None:
        return current_generation is not None and current_generation == expected_generation
    return all(
        current.get(key) == expected.get(key)
        for key in ("command", "activated_at", "expires_at")
    )


def mark_notified(
    store: BotStore, group_id: str | int, record: dict[str, Any]
) -> bool:
    current = get_expiry(store, group_id)
    if not same_expiry_generation(current, record):
        return False
    updated = dict(current)
    updated["notified"] = True
    updated.pop("notice_attempts", None)
    updated.pop("next_notice_at", None)
    store.set_group_state(group_id, "expiry", updated)
    return True
