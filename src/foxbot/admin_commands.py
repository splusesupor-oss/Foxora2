from __future__ import annotations

import re
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from foxbot.domain import ModerationKind
from foxbot.group_admin import GroupAdminService
from foxbot.platform import (
    GroupInfoAccessDenied,
    GroupInfoError,
    GroupInfoNetworkError,
    GroupNotFound,
    IncompleteGroupInfo,
    IncompleteMemberScan,
    PlatformCapabilityUnavailable,
)

_TEHRAN = ZoneInfo("Asia/Tehran")
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_USERNAME = re.compile(r"^[A-Za-z0-9_]{4,32}$")

ReplyCallback = Callable[..., Awaitable[Any]]


async def _send(
    event: Any,
    reply: ReplyCallback | None,
    text: str,
    *,
    formatting_entities: list[Any] | None = None,
    prefer_respond: bool = False,
) -> Any:
    if reply is not None:
        return await reply(
            event,
            text,
            formatting_entities=formatting_entities,
            prefer_respond=prefer_respond,
        )
    sender = (
        getattr(event, "respond", None) if prefer_respond else None
    ) or getattr(event, "reply", None) or getattr(event, "respond", None)
    if not callable(sender):
        raise TypeError("event has no supported reply method")
    kwargs = {"formatting_entities": formatting_entities} if formatting_entities is not None else {}
    result = sender(text, **kwargs)
    return await result if hasattr(result, "__await__") else result


def _entity(name: str, **values: Any) -> Any:
    from splusthon.tl import types

    cls = getattr(types, name, None)
    if cls is None:
        raise PlatformCapabilityUnavailable(f"SPlusthon entity {name} is unavailable")
    return cls(**values)


def _u16(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _entity_to_json(entity: Any) -> dict[str, Any] | None:
    if isinstance(entity, dict):
        data = dict(entity)
    else:
        serializer = getattr(entity, "to_dict", None)
        if not callable(serializer):
            return None
        try:
            data = serializer()
        except Exception:  # noqa: BLE001 - unsupported entity should not break text saving
            return None
    if not isinstance(data, dict) or not isinstance(data.get("_"), str):
        return None
    cleaned: dict[str, Any] = {"_": data["_"]}
    for key, value in data.items():
        if key == "_":
            continue
        if value is None or isinstance(value, (str, int, float, bool)) or isinstance(value, list) and all(
            item is None or isinstance(item, (str, int, float, bool)) for item in value
        ):
            cleaned[key] = value
    return cleaned


def _restore_entities(values: Any) -> list[Any]:
    from splusthon.tl import types

    if not isinstance(values, list):
        return []
    result: list[Any] = []
    for spec in values:
        if not isinstance(spec, dict):
            continue
        name = spec.get("_")
        cls = getattr(types, name, None) if isinstance(name, str) else None
        if cls is None:
            continue
        try:
            result.append(cls(**{key: value for key, value in spec.items() if key != "_"}))
        except (TypeError, ValueError):
            continue
    return result


def _normalize(text: str) -> str:
    value = str(text or "")
    value = value.replace("\u200c", " ").replace("\u200f", "").replace("\u200e", "")
    value = value.replace("ي", "ی").replace("ك", "ک")
    return " ".join(value.split())


def _reply_message_id(event: Any) -> int | None:
    candidates = (
        getattr(getattr(event, "reply_to", None), "reply_to_msg_id", None),
        getattr(event, "reply_to_msg_id", None),
        getattr(getattr(event, "message", None), "reply_to_msg_id", None),
        getattr(
            getattr(getattr(event, "message", None), "reply_to", None),
            "reply_to_msg_id",
            None,
        ),
    )
    for value in candidates:
        if value is not None:
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                continue
            if parsed > 0:
                return parsed
    return None


def _sender(event: Any) -> Any | None:
    return getattr(event, "sender", None) or getattr(event, "_bot_cached_sender", None)


def _user_id(user: Any) -> str | None:
    value = getattr(user, "id", None)
    return str(value) if value is not None and str(value).strip() else None


def _display(user: Any, fallback: str = "کاربر ناشناس") -> str:
    username = str(getattr(user, "username", None) or "").strip().lstrip("@")
    if username:
        return f"@{username}"
    name = " ".join(
        str(part).strip()
        for part in (
            getattr(user, "first_name", None),
            getattr(user, "last_name", None),
        )
        if part and str(part).strip()
    )
    return name or fallback


def _record_display(record: dict[str, Any]) -> str:
    username = str(record.get("username") or "").strip().lstrip("@")
    if username:
        return f"@{username}"
    return str(record.get("display_name") or "کاربر ناشناس").strip()


def _group_role_display(user: Any) -> str:
    name = str(getattr(user, "display_name", None) or _display(user)).strip()
    user_id = str(getattr(user, "user_id", "")).strip()
    username = str(getattr(user, "username", None) or "").strip().lstrip("@")
    identity = f"@{username}" if username else name
    return f"{identity} (شناسه: {user_id})" if user_id else identity


def _group_info_error_text(exc: Exception) -> str:
    if isinstance(exc, GroupInfoAccessDenied):
        return "دسترسی سروش‌پلاس کافی نیست؛ عضویت حساب ربات در گروه و سطح دسترسی آن را بررسی کنید."
    if isinstance(exc, GroupNotFound):
        return "شناسهٔ گروه نامعتبر است یا گروه در حساب قابل دسترسی نیست."
    if isinstance(exc, GroupInfoNetworkError):
        return "به‌دلیل خطای اتصال، اطلاعات زندهٔ گروه دریافت نشد؛ دوباره تلاش کنید."
    if isinstance(exc, IncompleteGroupInfo):
        return "سروش‌پلاس پاسخ ناقص دربارهٔ مالک/ادمین‌ها برگرداند؛ نتیجهٔ ناقص نمایش داده نمی‌شود."
    if isinstance(exc, PlatformCapabilityUnavailable):
        return "نسخهٔ نصب‌شدهٔ SPlusthon قابلیت موردنیاز برای دریافت مالک/ادمین‌های گروه را ندارد."
    if isinstance(exc, GroupInfoError):
        return "سروش‌پلاس نتوانست اطلاعات گروه را ارائه کند."
    return f"دریافت اطلاعات گروه ناموفق بود ({type(exc).__name__})."


def _fa_math(value: int) -> str:
    digits = "𝟬𝟭𝟮𝟯𝟰𝟱𝟲𝟳𝟴𝟵"
    return "".join(digits[int(char)] if char.isdigit() else char for char in str(value))


def _prefixed_command(value: str) -> tuple[str, list[str]] | None:
    if not value or value[0] not in "!./":
        return None
    parts = value[1:].strip().split()
    if not parts:
        return None
    return parts[0].lower(), parts[1:]


def _reported_failure(report: Any) -> str:
    if getattr(report, "failed", 0):
        return "❌ عملیات روی کاربر انجام نشد"
    if getattr(report, "skipped", 0):
        return "⚠️ کاربر عضو گروه نیست یا برای این عملیات محافظت شده است"
    return "⚠️ تغییری لازم نبود"


def _format_stats(stats: dict[str, Any]) -> str:
    text = (
        "📊 **آمار گروه**\n\n"
        f"💬 **کل پیام‌ها:** {int(stats.get('messages', 0))}\n\n"
        f"🗑 **پیام حذف شده:** {int(stats.get('deleted', 0))}\n\n"
        f"🚪 **اخراج شده:** {int(stats.get('kicked', 0))}\n\n"
        f"🔇 **سکوت شده:** {int(stats.get('muted', 0))}\n\n"
        f"👥 **تعداد اعضا:** {int(stats.get('members', 0))}\n\n"
        "🏆 **کاربران فعال:**\n\n"
    )
    for index, user in enumerate(stats.get("users", []), 1):
        username = str(user.get("username") or "").strip().lstrip("@")
        display = f"@{username}" if username else str(user.get("display_name") or "کاربر ناشناس")
        text += f"{index}️⃣ {display} - {int(user.get('messages', 0))} پیام\n\n"
    return text


def _format_admin_list(
    registered: list[dict[str, Any]], native: list[Any]
) -> tuple[str, list[Any]]:
    registered_text = "\n".join(
        f"★ : {_record_display(item)} (شناسه: {item.get('user_id', 'نامشخص')})"
        for item in registered
    ) if registered else "ندارد"
    native_displays = sorted({_group_role_display(item) for item in native})
    group_text = "\n".join(f"☆ : {name}" for name in native_displays) if native_displays else "ندارد"
    first_title = "✍ ادمین‌های ثبت‌شده ربات"
    second_title = "✍ ادمین‌های گروه"
    text = f"{first_title}\n{registered_text}\n\n{second_title}\n{group_text}"
    first_start = _u16(first_title + "\n")
    second_start = _u16(text[:text.index(second_title) + len(second_title) + 1])
    entities = [
        _entity("MessageEntityBold", offset=0, length=_u16(first_title)),
        _entity("MessageEntityBlockquote", offset=first_start, length=_u16(registered_text)),
        _entity("MessageEntityBold", offset=_u16(text[:text.index(second_title)]), length=_u16(second_title)),
        _entity("MessageEntityBlockquote", offset=second_start, length=_u16(group_text)),
    ]
    return text, entities


def _format_admin_log(rows: list[dict[str, Any]]) -> tuple[str, list[Any]]:
    if not rows:
        return "📭 هنوز اقدامی در این گروه ثبت نشده است.", []
    # Source presentation is chronological, with one quote block per actor.
    grouped: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for row in reversed(rows):
        actor_id = str(row.get("actor_id") or row.get("actor_display") or "unknown")
        entry = grouped.setdefault(
            actor_id,
            {"actor": row.get("actor_display") or "کاربر ناشناس", "actions": []},
        )
        target = str(row.get("display_name") or "").strip()
        detail = str(row.get("detail") or "").strip()
        action = detail or "اقدام مدیریتی"
        if target:
            action = f"{action} {target}"
        excerpt = str(row.get("message_excerpt") or "").strip()
        if row.get("event_type") == "message_deleted" and excerpt:
            action = f"{action}: {excerpt[:160]}"
        entry["actions"].append(action)
    lines = ["🧾 لاگ مدیریتی گروه:\n"]
    entities: list[Any] = []
    for index, entry in enumerate(grouped.values(), 1):
        lines.append(f"{index}.\n")
        actor_line = f"👤 {entry['actor']}\n"
        start = _u16("".join(lines))
        lines.append(actor_line)
        entities.append(_entity("MessageEntityBlockquote", offset=start, length=_u16(actor_line)))
        for action in entry["actions"]:
            lines.append(f"→ {action}\n")
        lines.append("\n")
    footer = "⏳ این لاگ‌ها هر ۲۴ ساعت به‌صورت خودکار ریست می‌شوند."
    start = _u16("".join(lines))
    lines.append(footer)
    entities.append(_entity("MessageEntityBold", offset=start, length=_u16(footer)))
    return "".join(lines), entities


def _format_history(rows: list[dict[str, Any]]) -> tuple[str, list[Any]]:
    if not rows:
        return "📭 در ۲۴ ساعت گذشته سابقه‌ای برای این گروه ثبت نشده است.", []
    grouped: OrderedDict[str, dict[str, list[str]]] = OrderedDict()
    displays: dict[str, str] = {}
    for row in rows:
        uid = str(row.get("user_id") or row.get("display_name") or "unknown")
        displays.setdefault(uid, str(row.get("display_name") or "کاربر ناشناس"))
        data = grouped.setdefault(uid, {"kick": [], "mute": [], "warning": []})
        kind = row.get("event_type")
        label = "kick" if kind == "kick" else "mute" if kind == "mute" else "warning" if kind == "warning" else None
        if label:
            data[label].append(str(row.get("detail") or "").strip())
    lines = ["🗂 سابقهٔ کاربران این گروه:\n\n"]
    for uid, events in grouped.items():
        header = f"「 {displays[uid]} 」\n"
        lines.append(header)
        for key, label in (("kick", "🚫 اخراج شده"), ("mute", "🔇 سکوت شده"), ("warning", "⚠️ اخطار گرفته")):
            reasons = events[key]
            if not reasons:
                continue
            lines.append(f"{label}{f' ({_fa_math(len(reasons))} بار)' if len(reasons) > 1 else ''}\n")
            for reason in reasons:
                lines.append(f"📝 دلیل: {reason or 'ثبت نشده'}\n")
        lines.append("\n")
    text = "".join(lines)
    footer = "⏳ سابقه‌ها هر ۲۴ ساعت به‌صورت خودکار ریست می‌شوند."
    lines.append(footer)
    text = "".join(lines)
    entities = [
        _entity("MessageEntityBlockquote", offset=0, length=_u16(text)),
        _entity("MessageEntityBold", offset=0, length=_u16("🗂 سابقهٔ کاربران این گروه:")),
        _entity("MessageEntityBold", offset=_u16(text[:text.index(footer)]), length=_u16(footer)),
    ]
    return text, entities


def _format_vip_notice(display: str, *, removed: bool = False) -> tuple[str, list[Any]]:
    if removed:
        text = (
            "\u200f☰ \U0001D5E9\U0001D5DC\U0001D5E3 , #plus\n"
            f"🏔کاربر  「 {display} 」\n\n"
            "\u200f❌ vip لغو شد"
        )
        return text, []
    text = (
        "\u200f☰ \U0001D5E9\U0001D5DC\U0001D5E3 , #plus\n"
        f"🏔کاربر  「 {display} 」\n\n"
        "» لغو vip\n"
        "» لیست vip"
    )
    entities = []
    for command in ("لغو vip", "لیست vip"):
        start = text.index("» " + command) + 2
        entities.append(_entity("MessageEntityBold", offset=_u16(text[:start]), length=_u16(command)))
    return text, entities


def _parse_time(value: str, *, now: datetime) -> str | None:
    raw = _normalize(value).translate(_DIGITS)
    raw = re.sub(r"\s*ساعت\s*", " ", raw).strip()
    day_part = None
    for word, label in (("صبح", "morning"), ("ظهر", "noon"), ("عصر", "afternoon"), ("شب", "night")):
        if word in raw:
            day_part = label
            raw = raw.replace(word, " ").strip()
            break
    match = re.search(r"(\d{1,2})\s*:\s*(\d{1,2})", raw)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
    else:
        numbers = re.findall(r"\d{1,2}", raw)
        hour, minute = (int(numbers[0]), 0) if numbers else (None, 0)
    if hour is None or not 0 <= minute <= 59:
        return None
    if day_part:
        if not 1 <= hour <= 12 and (hour != 0 or day_part != "night"):
            return None
        if day_part == "morning":
            hour = 7 if hour == 0 else hour
        elif day_part == "noon":
            hour = 12 if hour == 0 or hour == 12 else hour + 12
        elif day_part == "afternoon":
            hour = 17 if hour == 0 else (hour if hour >= 12 else hour + 12)
        else:
            hour = 21 if hour == 0 else (0 if hour == 12 else hour + 12)
        return f"{hour % 24:02d}:{minute:02d}"
    if hour == 0 or 13 <= hour <= 23 or hour == 12:
        return f"{hour:02d}:{minute:02d}"
    if not 1 <= hour <= 11:
        return None
    candidates = [now.replace(hour=hour, minute=minute, second=0, microsecond=0), now.replace(hour=hour + 12, minute=minute, second=0, microsecond=0)]
    future = [candidate for candidate in candidates if candidate >= now]
    choice = min(future) if future else candidates[0]
    return f"{choice.hour:02d}:{choice.minute:02d}"


def _parse_cleanup_count(value: str) -> int | None:
    normalized = _normalize(value).translate(_DIGITS)
    try:
        count = int(normalized)
    except ValueError:
        return None
    return count if 1 <= count <= 700 else None


async def _resolve_user(
    service: GroupAdminService,
    event: Any,
    group_id: str | int,
    *,
    username: str | None = None,
) -> Any | None:
    reply_id = _reply_message_id(event)
    if reply_id is not None:
        return await service.resolve_replied_user(group_id, event)
    if username is None:
        return None
    value = username.strip().lstrip("@")
    if not _USERNAME.fullmatch(value):
        return None
    return await service.resolve_username(group_id, value)


async def _handle_pending_workflow(
    event: Any,
    service: GroupAdminService,
    *,
    group_id: str | int,
    actor_id: str | int,
    text: str,
    reply: ReplyCallback | None,
) -> bool:
    workflow = await service.pending_workflow(group_id, actor_id)
    if workflow is None:
        return False
    kind = workflow.get("kind")
    value = _normalize(text)

    if kind == "law":
        try:
            await service.set_law(group_id, actor_id, text)
        except ValueError:
            await _send(event, reply, "❌ متن قوانین معتبر نیست یا خیلی طولانی است.")
            return True
        await service.cancel_workflow(group_id, actor_id=actor_id, kind=str(kind))
        await _send(event, reply, "📜 قوانین گروه ثبت شد ✅")
        await service.record_admin_action(
            group_id, actor_id, actor_name=_display(_sender(event)), action="ثبت قوانین"
        )
        return True

    if kind == "warning_threshold":
        numeric = value.translate(_DIGITS)
        if not numeric.isdigit():
            await service.cancel_workflow(group_id, actor_id=actor_id, kind=str(kind))
            return False
        chosen = int(numeric)
        if not 1 <= chosen <= 12:
            await _send(event, reply, "❌ فقط عددی بین ۱ تا ۱۲ مجاز است. دوباره بنویسید مثلا « 7 »")
            return True
        await service.set_warning_threshold(group_id, actor_id, chosen)
        await service.cancel_workflow(group_id, actor_id=actor_id, kind=str(kind))
        await _send(event, reply, f"🔒 تعداد اخطار تغییر کرد « {chosen} »")
        await service.record_admin_action(
            group_id, actor_id, actor_name=_display(_sender(event)), action="تغییر سقف اخطار", note=str(chosen)
        )
        return True

    if kind == "punishment_mode":
        confirms = {"تایید", "تأیید", "✅ تایید", "✅ تأیید", "🔸تایید", "🔸تأیید"}
        cancels = {"لغو", "❌ لغو", "🔸لغو"}
        if value in confirms:
            current = service.punishment_mode(group_id)
            new_mode = "mute" if current == "ban" else "ban"
            await service.set_punishment_mode(group_id, actor_id, new_mode)
            await service.cancel_workflow(group_id, actor_id=actor_id, kind=str(kind))
            label = "سکوت" if new_mode == "mute" else "اخراج"
            await _send(
                event,
                reply,
                f"📌 مجازات تغییر کرد « {label} »\nبرای تغییر دوباره مجازات همان دستور رو ارسال کنید",
            )
            await service.record_admin_action(
                group_id, actor_id, actor_name=_display(_sender(event)), action="تغییر مجازات", note=label
            )
            return True
        if value in cancels:
            await service.cancel_workflow(group_id, actor_id=actor_id, kind=str(kind))
            await _send(event, reply, "❌ تغییر مجازات لغو شد")
            return True
        await service.cancel_workflow(group_id, actor_id=actor_id, kind=str(kind))
        return False

    if kind == "auto_cleanup":
        state = workflow.get("values")
        state = state if isinstance(state, dict) else {}
        step = state.get("step")
        if step == "day":
            compact = value.replace(" ", "")
            if compact in {"امروز", "امرز", "اموز"}:
                state["day"] = "today"
            elif compact in {"فردا", "فرداه", "فر دا"}:
                state["day"] = "tomorrow"
            else:
                await _send(event, reply, "❌ لطفاً یکی از گزینه‌های «امروز» یا «فردا» را ارسال کنید.")
                return True
            state["step"] = "time"
            workflow["values"] = state
            await service.update_workflow(group_id, actor_id, workflow)
            await _send(event, reply, "🕐 ساعت انجام پاکسازی را وارد کنید:\nمثال: «13:32»")
            return True
        if step == "time":
            now = datetime.now(_TEHRAN)
            time_value = _parse_time(value, now=now)
            if time_value is None:
                await _send(
                    event,
                    reply,
                    "❌ ساعت نامعتبر است؛ مانند «15:12»، «۷ صبح»، «۷ شب» یا «ساعت ۷» ارسال کنید.",
                )
                return True
            hour, minute = (int(part) for part in time_value.split(":"))
            scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if state.get("day") == "tomorrow" or scheduled <= now:
                scheduled += timedelta(days=1)
            state["time"] = time_value
            state["scheduled_at"] = scheduled.astimezone(UTC).isoformat(timespec="seconds")
            state["step"] = "count"
            workflow["values"] = state
            await service.update_workflow(group_id, actor_id, workflow)
            await _send(event, reply, "🗑️ چه تعداد پیام حذف شود؟\nعددی بین ۱ تا ۷۰۰ ارسال کنید (مثلاً «100»).")
            return True
        if step == "count":
            count = _parse_cleanup_count(value)
            if count is None:
                await _send(event, reply, "❌ تعداد نامعتبر است؛ عددی بین ۱ تا ۷۰۰ ارسال کنید.")
                return True
            scheduled_at = state.get("scheduled_at")
            if not isinstance(scheduled_at, str):
                await service.cancel_workflow(group_id, actor_id=actor_id, kind=str(kind))
                await _send(event, reply, "❌ خطا در ذخیرهٔ تنظیم؛ دوباره تلاش کنید.")
                return True
            try:
                scheduled = datetime.fromisoformat(scheduled_at)
                if scheduled.tzinfo is None:
                    scheduled = scheduled.replace(tzinfo=UTC)
            except ValueError:
                await service.cancel_workflow(group_id, actor_id=actor_id, kind=str(kind))
                await _send(event, reply, "❌ خطا در ذخیرهٔ تنظیم؛ دوباره تلاش کنید.")
                return True
            service.store.set_group_state(
                group_id,
                "auto_cleanup",
                {
                    "actor_id": str(actor_id),
                    "count": count,
                    "scheduled_at": scheduled_at,
                    "status": "scheduled",
                },
            )
            await service.cancel_workflow(group_id, actor_id=actor_id, kind=str(kind))
            await _send(
                event,
                reply,
                "✅ پاکسازی خودکار تنظیم شد.\n\n"
                f"زمان: {scheduled.astimezone(_TEHRAN).strftime('%Y-%m-%d %H:%M')} تهران\n"
                f"تعداد پیام: {count}\n"
                "در زمان اجرا، پس از اسکن کامل، برای حذف نهایی تأیید جداگانه لازم است.",
            )
            await service.record_admin_action(
                group_id,
                actor_id,
                actor_name=_display(_sender(event)),
                action="تنظیم پاکسازی خودکار",
                note=f"{count} پیام؛ {scheduled_at}",
            )
            return True
    return False


async def handle_group_admin_command(
    event: Any,
    service: GroupAdminService,
    *,
    group_id: str | int,
    actor_id: str | int,
    text: str,
    reply: ReplyCallback | None = None,
    pending_only: bool = False,
    skip_pending: bool = False,
) -> bool:
    """Route group-admin commands; phase flags preserve source interleaving around pending workflows."""
    if group_id is None or actor_id is None:
        return False
    value = _normalize(text)
    if not value:
        return False

    try:
        if not skip_pending:
            if await _handle_pending_workflow(
                event,
                service,
                group_id=group_id,
                actor_id=actor_id,
                text=text,
                reply=reply,
            ):
                return True
            if pending_only:
                return False

        global_owner = service.access.owner_registry.is_global_owner(actor_id)
        if value in {"سرگرمی خاموش", "سرگرمی فعال"}:
            if bool(getattr(event, "is_private", False)):
                await _send(event, reply, "❌ این دستور فقط داخل گروه کار می‌کند.")
                return True
            group_owner = service.store.get_group_owner(group_id)
            permitted = (
                global_owner
                or (group_owner is not None and str(group_owner) == str(actor_id))
                or service.store.has_group_command_member(group_id, "registered_admin", actor_id)
            )
            if not permitted:
                await _send(event, reply, "❌ فقط مالک یا ادمین‌های گروه اجازه تغییر وضعیت سرگرمی را دارند")
                return True
            enabled = value == "سرگرمی فعال"
            service.store.set_group_state(group_id, "entertainment_enabled", enabled)
            notice = (
                "🍬 : بازی های روباه فعال شد میتوانید با دستور لیست بازی از بازی ها استفاده کنید"
                if enabled
                else "🎮 بازی های روباه غیر فعال شد"
            )
            await _send(event, reply, notice)
            return True
        if value in {"فعال", "غیر فعال", "فعال سازی", "ثبت گروه"}:
            if value in {"فعال", "غیر فعال"} and not global_owner:
                return True
            if value in {"فعال سازی", "ثبت گروه"} and not global_owner:
                message = (
                    "❌ فقط مالک ربات اجازه ثبت گروه دارد"
                    if value == "ثبت گروه"
                    else "❌ فقط مالک ربات اجازه این دستور را دارد"
                )
                await _send(event, reply, message)
                return True
            group_state = service.store.get_group_state(group_id, "group_registry", {})
            group_state = group_state if isinstance(group_state, dict) else {}
            active = bool(group_state.get("active", False))
            title = str(
                getattr(getattr(event, "chat", None), "title", None)
                or group_state.get("title")
                or group_id
            )
            if value == "فعال" and active:
                return True
            if value == "غیر فعال":
                group_state.update({"active": False, "title": title})
                service.store.set_group_state(group_id, "group_registry", group_state)
                await _send(event, reply, f"🦊 روباه در گروه «{title}» غیر فعال شد ❌")
                return True
            group_state.update({
                "active": True,
                "title": title,
                "registered_at": group_state.get("registered_at") or datetime.now(UTC).isoformat(timespec="seconds"),
            })
            service.store.set_group_state(group_id, "group_registry", group_state)
            owner_text = "دادهٔ مالک در پاسخ سروش‌پلاس موجود نبود."
            admins_text = "اطلاعات دریافت نشد."
            try:
                live_roles = await service.group_administration(group_id, actor_id)
                if live_roles.owner is not None:
                    owner_text = _group_role_display(live_roles.owner)
                names = [_group_role_display(admin) for admin in live_roles.admins]
                admins_text = "\n".join(
                    f"{index}. {name}" for index, name in enumerate(names, 1)
                ) if names else "در پاسخ کامل سرویس، ادمینی گزارش نشد."
            except Exception as exc:  # noqa: BLE001 - show a categorized live API failure, never stale/fabricated roles
                detail = _group_info_error_text(exc)
                owner_text = detail
                admins_text = detail
            activation_text = (
                f"🦊 روباه در گروه «{title}» فعال سازی شد ✅\n\n"
                f"👑 مالک گروه:\n{owner_text}\n\n"
                f"👮 ادمین های گروه:\n{admins_text}\n\n"
                "برای آشنایی بیشتر کلمه راهنما را ارسال کنید."
            )
            await _send(event, reply, activation_text)
            return True

        if value == "ثبت مالک":
            if not global_owner:
                await _send(event, reply, "❌ فقط مالک اصلی ربات اجازه ثبت مالک گروه را دارد")
                return True
            target = await _resolve_user(service, event, group_id)
            if target is None or _user_id(target) is None:
                await _send(event, reply, "❌ برای ثبت مالک باید روی پیام کاربر ریپلای کنید")
                return True
            service.store.set_group_owner(group_id, _user_id(target))
            await _send(event, reply, f"**مالـک ثبت شد** 「 {_display(target)}」‌𓆤")
            return True
        if value in {"لغو مالک", "برکناری مالک"}:
            if not global_owner:
                await _send(event, reply, "❌ فقط مالک اصلی ربات اجازه لغو مالک گروه را دارد")
                return True
            removed_owner = service.store.remove_group_owner(group_id)
            await _send(
                event,
                reply,
                "✅ مالک گروه لغو شد" if removed_owner else "❌ برای این گروه مالک ثبت‌شده‌ای وجود ندارد",
            )
            return True
        if value == "لیست انقضا":
            if not global_owner:
                return True
            rows = service.store.list_group_states("expiry")
            now = datetime.now(UTC)
            output = ["📋 لیست انقضای گروه‌ها"]
            listed = 0
            for registered_group, record in rows:
                if not isinstance(record, dict) or not record.get("expires_at"):
                    continue
                listed += 1
                title = str(record.get("title") or registered_group)
                try:
                    expires = datetime.fromisoformat(str(record["expires_at"]))
                    if expires.tzinfo is None:
                        expires = expires.replace(tzinfo=UTC)
                    seconds = int((expires.astimezone(UTC) - now).total_seconds())
                except (TypeError, ValueError):
                    remaining = "تاریخ انقضا نامعتبر است"
                else:
                    if seconds <= 0:
                        remaining = "منقضی شده"
                    else:
                        days, rest = divmod(seconds, 86400)
                        hours, rest = divmod(rest, 3600)
                        minutes = rest // 60
                        parts = []
                        if days:
                            parts.append(f"{_fa_math(days)} روز")
                        if hours or days:
                            parts.append(f"{_fa_math(hours)} ساعت")
                        if not days and not hours:
                            parts.append(f"{_fa_math(minutes)} دقیقه")
                        remaining = " و ".join(parts) + " باقی مانده"
                output.append(f"\n\n{listed}. {title}\n⏳ {remaining}")
            if listed == 0:
                output.append("\n\nℹ️ هیچ تاریخ انقضایی ثبت نشده است.")
            await _send(event, reply, "".join(output))
            return True

        prefixed = _prefixed_command(value)
        command = prefixed[0] if prefixed else ""
        args = prefixed[1] if prefixed else []
        aliases = {
            "stats": "legacy_stats",
            "آمار": "legacy_stats",
            "reset": "legacy_reset",
            "ریست": "legacy_reset",
            "lock": "lock",
            "قفل": "lock",
            "unlock": "unlock",
            "باز": "unlock",
            "title": "title",
            "اسم": "title",
            "help": "help",
            "راهنما": "help",
            "timelock": "timelock",
            "قفل_ساعتی": "timelock",
            "photo": "photo",
            "عکس": "photo",
        }
        alias = aliases.get(command)

        # Clipboard commands are deliberately checked before reply-to-save,
        # matching the source handler's early route.
        if value in {"کپی بورد", "کپی"}:
            if value == "کپی بورد":
                guide = (
                    "🔖راهنما و توضیحات کپی بورد\n\n"
                    "برای ذخیره یک متن، روی همین پیام پاسخ دهید و متن موردنظر را ارسال کنید.\n\n"
                    "سپس با دستور «کپی» متن ذخیره‌شده را دوباره بفرستید. متن ساده و قالب‌دار پشتیبانی می‌شود.\n\n"
                    "📌 روی همین پیام ریپلای کنید و متن یا پیوند موردنظر را بفرستید"
                )
                title = "🔖راهنما و توضیحات کپی بورد"
                description = guide.split("\n\n", 2)[1]
                entities = [
                    _entity("MessageEntityBlockquote", offset=0, length=_u16(title)),
                    _entity("MessageEntityBold", offset=0, length=_u16(title)),
                    _entity("MessageEntityBold", offset=_u16(title + "\n\n"), length=_u16(description)),
                ]
                sent = await _send(event, reply, guide, formatting_entities=entities)
                sent_id = getattr(sent, "id", None)
                if sent_id is not None:
                    await service.set_clipboard_pending_guide(group_id, actor_id, sent_id)
                else:
                    await _send(event, reply, "❌ پیام راهنمای کپی بورد ثبت نشد؛ دوباره تلاش کنید.")
                return True
            clipboard = service.clipboard(group_id)
            if clipboard is None:
                await _send(event, reply, "❌ هنوز متنی برای کپی ذخیره نشده است.")
                return True
            await _send(
                event,
                reply,
                str(clipboard.get("text") or ""),
                formatting_entities=_restore_entities(clipboard.get("entities")),
                prefer_respond=True,
            )
            return True

        # Save a reply only when it targets the exact pending guide message.
        pending_guide = service.clipboard_pending_guide(group_id)
        if pending_guide and _reply_message_id(event) is not None:
            reply_id = _reply_message_id(event)
            if str(reply_id) == str(pending_guide.get("message_id")):
                role = await service.role(group_id, actor_id)
                if role is not None:
                    message = getattr(event, "message", None)
                    entities = [
                        spec for spec in (
                            _entity_to_json(item)
                            for item in (getattr(message, "entities", None) or [])
                        ) if spec is not None
                    ]
                    await service.save_clipboard(
                        group_id,
                        actor_id,
                        reply_id,
                        str(getattr(event, "raw_text", None) or text),
                        entities,
                    )
                    await _send(event, reply, "✅ متن کپی بورد با موفقیت ذخیره شد.")
                    return True

        # Legacy aliases from handlers/admin_handler.py.
        if alias == "legacy_stats":
            counts = await service.warning_counts(group_id, actor_id)
            await _send(event, reply, f"📊 تعداد کاربران متخلف: {len(counts)}")
            return True
        if alias == "legacy_reset":
            if args and args[0].translate(_DIGITS).isdigit():
                await service.reset_one_warning_count(group_id, actor_id, args[0].translate(_DIGITS))
                await _send(event, reply, "✅ شمارنده کاربر صفر شد.")
            else:
                await service.reset_group_warning_counts(group_id, actor_id)
                await _send(event, reply, "✅ شمارنده گروه صفر شد.")
            return True
        if alias == "help":
            await service.require_admin(group_id, actor_id)
            help_text = (
                "🤖 راهنمای کوتاه مدیریت گروه:\n"
                "!addword/!addban | !remword/!removeword\n"
                "!stats/!آمار | !reset/!ریست | !lock/!قفل | !unlock/!باز\n"
                "!timelock/!قفل_ساعتی <دقیقه> | !title/!اسم <نام> | !photo/!عکس <مسیر>\n\n"
                "برای فهرست کامل فرمان‌های مدیریتیِ route‌شده، «لیست ادمینی» را بفرستید.\n"
                "نکته: /help و !help راهنمای عمومی هستند؛ !راهنما همین راهنمای مدیر را نشان می‌دهد."
            )
            await _send(event, reply, help_text)
            return True
        if alias in {"lock", "unlock"}:
            locked = alias == "lock"
            await service.set_group_locked(group_id, actor_id, locked)
            action = "قفل کردن گروه" if locked else "باز کردن گروه"
            await service.record_admin_action(
                group_id, actor_id, actor_name=_display(_sender(event)), action=action
            )
            await _send(event, reply, "🔒 گروه قفل شد" if locked else "🔓 گروه باز شد")
            return True
        if alias == "timelock":
            await service.require_admin(group_id, actor_id)
            if not args or not args[0].translate(_DIGITS).isdigit():
                await _send(event, reply, "❌ استفاده: !قفل_ساعتی دقیقه")
                return True
            minutes = int(args[0].translate(_DIGITS))
            if not 1 <= minutes <= 10080:
                await _send(event, reply, "❌ مدت قفل باید بین ۱ تا ۱۰۰۸۰ دقیقه باشد.")
                return True
            snapshot = await service.set_group_locked(group_id, actor_id, True)
            unlock_at = (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat(timespec="seconds")
            service.store.set_group_state(
                group_id,
                "timed_unlock",
                {"actor_id": str(actor_id), "unlock_at": unlock_at, "rights": snapshot},
            )
            await service.record_admin_action(
                group_id, actor_id, actor_name=_display(_sender(event)), action="قفل ساعتی", note=f"{minutes} دقیقه"
            )
            await _send(event, reply, "⏰ قفل ساعتی فعال شد")
            return True
        if alias == "photo":
            if not args:
                await service.require_admin(group_id, actor_id)
                await _send(event, reply, "❌ مسیر عکس را بده")
                return True
            await service.change_group_photo(group_id, actor_id, " ".join(args))
            await service.record_admin_action(
                group_id, actor_id, actor_name=_display(_sender(event)), action="تغییر عکس گروه"
            )
            await _send(event, reply, "🖼️ عکس گروه تغییر کرد ✅")
            return True
        if alias == "title":
            await service.require_admin(group_id, actor_id)
            title = " ".join(args).strip()
            if not title:
                await _send(event, reply, "❌ استفاده: !اسم نام جدید گروه")
                return True
            await service.change_group_title(group_id, actor_id, title)
            await service.update_expiry_title(group_id, title)
            await service.record_admin_action(
                group_id, actor_id, actor_name=_display(_sender(event)), action="تغییر نام گروه", note=title
            )
            await _send(event, reply, "✅ اسم گروه تغییر کرد")
            return True

        # Group rules. The next message from the initiating admin is the law text.
        if value == "ثبت قوانین":
            await service.start_workflow(group_id, actor_id, "law")
            await _send(event, reply, "📜 لطفاً قوانین گروه را ارسال کنید.")
            return True
        if value == "حذف قوانین":
            removed = await service.remove_law(group_id, actor_id)
            if removed:
                await _send(event, reply, "✅ قوانین گروه حذف شد.")
            else:
                await _send(event, reply, "📜 قانونی برای حذف ثبت نشده است.")
            await service.record_admin_action(
                group_id, actor_id, actor_name=_display(_sender(event)), action="حذف قوانین"
            )
            return True
        if value == "قوانین":
            law = service.get_law(group_id)
            await _send(event, reply, law or "📜 هنوز قانونی برای این گروه ثبت نشده است.")
            return True

        # Group ownership/admin registry.
        if value == "لیست ادمین":
            registered, native = await service.list_admins(group_id, actor_id)
            text_out, entities = _format_admin_list(registered, native)
            await _send(event, reply, text_out, formatting_entities=entities)
            return True
        if value in {"ثبت ادمین", "برکناری ادمین", "لغو ادمین"} or value.startswith(("ثبت ادمین ", "برکناری ادمین ", "لغو ادمین ")):
            base = next((item for item in ("ثبت ادمین", "برکناری ادمین", "لغو ادمین") if value == item or value.startswith(item + " ")), "")
            username_arg = value[len(base):].strip() if base else ""
            target = await _resolve_user(service, event, group_id, username=username_arg or None)
            if target is None or _user_id(target) is None:
                await _send(event, reply, "❌ کاربر پیدا نشد")
                return True
            target_id = _user_id(target)
            name = _display(target)
            if base == "ثبت ادمین":
                added = await service.add_admin(
                    group_id, actor_id, target_id,
                    display_name=name, username=getattr(target, "username", None),
                )
                await _send(event, reply, "✅ ادمین گروه ثبت شد." if added else "⚠️ این کاربر از قبل ادمین ثبت‌شده است.")
                action = "ثبت ادمین"
            else:
                removed = await service.remove_admin(group_id, actor_id, target_id)
                await _send(event, reply, "✅ ادمین گروه برکنار شد." if removed else "⚠️ این کاربر ادمین ثبت‌شده نیست یا قابل برکناری نیست.")
                action = "برکناری ادمین"
            await service.record_admin_action(
                group_id, actor_id, actor_name=_display(_sender(event)), action=action,
                target_id=target_id, target_name=name,
            )
            return True

        # VIP list is group-scoped, capped at the source limit of twenty.
        if value in {"vip", "لغو vip", "لیست vip"}:
            if value == "لیست vip":
                vips = await service.list_vips(group_id, actor_id)
                lines = [
                    "\u200f➛ 「 "
                    + _record_display(item)
                    + " 」"
                    for item in vips
                ] or ["\u200f➛ 「」"]
                title = "\u200f☴ \U0001D5E9\U0001D5DC\U0001D5E3 \U0001D5DF\U0001D5DC\U0001D5E6\U0001D5E7 #plus"
                await _send(event, reply, title + "\n\n" + "\n\n".join(lines))
                return True
            target = await _resolve_user(service, event, group_id)
            if target is None or _user_id(target) is None:
                await _send(event, reply, "❌ باید روی پیام کاربر ریپلای کنید")
                return True
            uid = _user_id(target)
            name = _display(target)
            if value == "vip":
                if service.is_vip(group_id, uid):
                    await _send(event, reply, "⚠️ این کاربر قبلا vip ثبت شده است")
                    return True
                added = await service.add_vip(
                    group_id, actor_id, uid,
                    display_name=name, username=getattr(target, "username", None),
                )
                if not added:
                    await _send(event, reply, "⚠️ این کاربر ادمین است و vip نشد")
                    return True
                notice, entities = _format_vip_notice(name)
                await _send(event, reply, notice, formatting_entities=entities)
                await service.record_admin_action(
                    group_id, actor_id, actor_name=_display(_sender(event)), action="ثبت vip", target_id=uid, target_name=name
                )
            else:
                removed = await service.remove_vip(group_id, actor_id, uid)
                if not removed:
                    await _send(event, reply, "⚠️ این کاربر vip نیست")
                    return True
                notice, entities = _format_vip_notice(name, removed=True)
                await _send(event, reply, notice, formatting_entities=entities or None)
                await service.record_admin_action(
                    group_id, actor_id, actor_name=_display(_sender(event)), action="لغو vip", target_id=uid, target_name=name
                )
            return True

        # Group status and warning policy.
        if value in {"قفل", "باز"}:
            locked = value == "قفل"
            await service.set_group_locked(group_id, actor_id, locked)
            action = "قفل کردن گروه" if locked else "باز کردن گروه"
            await service.record_admin_action(group_id, actor_id, actor_name=_display(_sender(event)), action=action)
            await _send(event, reply, "🔒 گروه قفل شد" if locked else "🔓 گروه باز شد")
            return True
        if value.startswith("!اسم "):
            # Usually caught by the prefixed alias above; retained for exact command spelling.
            title = value[len("!اسم "):].strip()
            if not title:
                await _send(event, reply, "❌ استفاده: !اسم نام جدید گروه")
                return True
            await service.change_group_title(group_id, actor_id, title)
            await service.update_expiry_title(group_id, title)
            await service.record_admin_action(group_id, actor_id, actor_name=_display(_sender(event)), action="تغییر نام گروه", note=title)
            await _send(event, reply, "✅ اسم گروه تغییر کرد")
            return True
        if value == "مهلت گروه":
            role = await service.role(group_id, actor_id)
            if role is None:
                return True
            expiry = service.group_expiry(group_id) or {}
            title = str(getattr(getattr(event, "chat", None), "title", "") or expiry.get("title") or "بدون نام")
            current_title = str(getattr(getattr(event, "chat", None), "title", "") or "").strip()
            if current_title:
                await service.update_expiry_title(group_id, current_title)
            raw_expiry = expiry.get("expires_at")
            try:
                end = datetime.fromisoformat(str(raw_expiry))
                if end.tzinfo is None:
                    end = end.replace(tzinfo=UTC)
                seconds = int((end.astimezone(UTC) - datetime.now(UTC)).total_seconds())
            except (TypeError, ValueError):
                await _send(event, reply, "ℹ️ برای این گروه تاریخ انقضایی ثبت نشده است.")
                return True
            if seconds <= 0:
                remaining = "منقضی شده"
            else:
                days, rest = divmod(seconds, 86400)
                hours, rest = divmod(rest, 3600)
                minutes = rest // 60
                parts = []
                if days:
                    parts.append(f"{days} روز")
                if hours or days:
                    parts.append(f"{hours} ساعت")
                if not days and not hours:
                    parts.append(f"{minutes} دقیقه")
                remaining = " و ".join(parts).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))
            msg = f"↻- گروه : {title}\nمهلت باقی مانده : {remaining}\nبرای تمدید اشتراک : 𝄞 @aifox_bot"
            entities = [
                _entity("MessageEntityBold", offset=_u16(msg[:msg.index("گروه")]), length=_u16("گروه")),
                _entity("MessageEntityBold", offset=_u16(msg[:msg.index("مهلت باقی مانده")]), length=_u16("مهلت باقی مانده")),
            ]
            await _send(event, reply, msg, formatting_entities=entities)
            return True
        if value in {"آمار گروه", "آمار گپ"}:
            getter = getattr(service.management.platform, "get_group_member_count", None)
            if callable(getter):
                with suppress(Exception):
                    count = await getter(group_id)
                    if count is not None:
                        service.store.set_group_member_count(group_id, count)
            stats = await service.group_stats(group_id)
            await _send(event, reply, _format_stats(stats))
            return True
        if value == "ریست آمار":
            await service.reset_group_stats(group_id, actor_id)
            await _send(event, reply, "✅ آمار گروه ریست شد\n👥 تعداد اعضا حفظ شد")
            await service.record_admin_action(group_id, actor_id, actor_name=_display(_sender(event)), action="ریست آمار")
            return True
        if value == "سرگرمی خاموش":
            await service.set_entertainment(group_id, actor_id, False)
            await _send(event, reply, "🎮 بازی های روباه غیر فعال شد")
            await service.record_admin_action(group_id, actor_id, actor_name=_display(_sender(event)), action="خاموش کردن سرگرمی")
            return True
        if value == "سرگرمی فعال":
            await service.set_entertainment(group_id, actor_id, True)
            await _send(event, reply, "🍬 : بازی های روباه فعال شد میتوانید با دستور لیست بازی از بازی ها استفاده کنید")
            await service.record_admin_action(group_id, actor_id, actor_name=_display(_sender(event)), action="فعال کردن سرگرمی")
            return True

        if value == "پاکسازی خودکار":
            await service.start_workflow(
                group_id, actor_id, "auto_cleanup", {"step": "day"}
            )
            await _send(
                event,
                reply,
                "🧹 پاکسازی خودکار\n\n"
                "پاکسازی چه زمانی انجام شود؟\n"
                "گزینه‌ها:\n"
                "- امروز\n"
                "- فردا",
            )
            return True

        if value == "تغییر اخطار":
            await service.start_workflow(group_id, actor_id, "warning_threshold")
            prompt = (
                f"⚙ تغییر اخطار\n\nتعداد اخطار فعلی {service.warning_threshold(group_id)}\n\n\n\n"
                "از ۱ تا ۱۲ میتوانید یک تعداد برای اخطار تایین کنید مثلا « 7 »"
            )
            await _send(event, reply, prompt)
            return True
        if value == "تغییر مجازات":
            await service.start_workflow(group_id, actor_id, "punishment_mode")
            if service.punishment_mode(group_id) == "mute":
                body = (
                    "در حال حاضر مجازات به صورت سکوت انجام می‌شود آیا میخواهید مجازات به بن تبدیل شود و از این پس "
                    "به جای سکوت کاربر ؛ مستقیم اخراج شود؟"
                )
            else:
                body = (
                    "در حال حاضر مجازات به صورت بن انجام می‌شود آیا میخواهید مجازات به سکوت دائمی تبدیل شود و از این پس "
                    "به جای اخراج کاربر؛ مستقیم سکوت دائم شود؟"
                )
            header = "⚠️ تغییر مجازات"
            prompt = f"{header}\n\n{body}\n\n\n\n🔸تایید\n\n🔸لغو"
            entities = [
                _entity("MessageEntityBlockquote", offset=0, length=_u16(header)),
                _entity("MessageEntityBold", offset=_u16("⚠️ "), length=_u16("تغییر مجازات")),
            ]
            await _send(event, reply, prompt, formatting_entities=entities)
            return True

        # A numeric manual warning requires a reply to a real group member.
        if value == "اخطار":
            target = await _resolve_user(service, event, group_id)
            if target is None or _user_id(target) is None:
                await _send(event, reply, "❌ باید روی پیام کاربر ریپلای کنید")
                return True
            uid = _user_id(target)
            actor_name = _display(_sender(event))
            display = _display(target)
            count, threshold = await service.add_warning(
                group_id, actor_id, uid, target_name=display, actor_name=actor_name,
            )
            header = f"⚠️ کاربر {display}"
            response = f"{header}\nیک اخطار دستی دریافت کرد\n\nتعداد اخطار: {_fa_math(count)}/{_fa_math(threshold)}"
            try:
                await _send(event, reply, response, formatting_entities=[_entity("MessageEntityBlockquote", offset=0, length=_u16(header))])
            except Exception:  # noqa: BLE001 - format fallback must not undo the warning
                await _send(event, reply, response)
            await service.record_admin_action(
                group_id, actor_id, actor_name=actor_name, action="اخطار", target_id=uid,
                target_name=display, note=f"تعداد {count}",
            )
            if count >= threshold:
                mode = service.punishment_mode(group_id)
                try:
                    if mode == "mute":
                        report = await service.management.moderate_single_member(
                            group_id=group_id, actor_id=actor_id, user_id=uid, operation=ModerationKind.MUTE,
                        )
                    else:
                        report = await service.management.ban_single_member(
                            group_id=group_id, actor_id=actor_id, user_id=uid,
                        )
                    if report.succeeded:
                        service.store.reset_warning_count(group_id, uid)
                        result_text = (
                            f"🚫 کاربر 「{display}」\nبه دلیل تخلفات "
                            + ("سکوت شد." if mode == "mute" else "از گروه اخراج شد.")
                        )
                        await _send(event, reply, result_text)
                        await service.record_admin_action(
                            group_id, actor_id, actor_name=actor_name,
                            action="مجازات پس از رسیدن به سقف اخطار", target_id=uid, target_name=display,
                        )
                    elif report.failed:
                        await _send(event, reply, "⚠️ سقف اخطار رسید، اما مجازات در پلتفرم تأیید نشد.")
                    elif mode == "mute":
                        await _send(event, reply, "⚠️ سقف اخطار رسید، اما مدت سکوت در تنظیمات ربات تعیین نشده است یا هدف قابل سکوت نیست.")
                except ValueError:
                    await _send(event, reply, "⚠️ سقف اخطار رسید، اما مدت سکوت در تنظیمات ربات تعیین نشده است.")
            return True

        if value in {"صفر", "حذف اخطار", "حذف اخطارها"}:
            target = await _resolve_user(service, event, group_id)
            if target is None or _user_id(target) is None:
                await _send(event, reply, "❌ باید روی پیام کاربر ریپلای کنید")
                return True
            uid = _user_id(target)
            display = _display(target)
            if value == "صفر":
                try:
                    old = await service.reset_violations(
                        group_id, actor_id, uid, target_name=display,
                        actor_name=_display(_sender(event)),
                    )
                except PermissionError:
                    await _send(event, reply, "❌ فقط مالک اصلی ربات یا مالک گروه اجازه صفر کردن تخلفات را دارد")
                    return True
                await _send(event, reply, "✅ تخلفات کاربر صفر شد.")
                await service.record_admin_action(
                    group_id, actor_id, actor_name=_display(_sender(event)), action="صفر کردن تخلفات",
                    target_id=uid, target_name=display, note=f"{old} → 0",
                )
            else:
                before = service.warning_count(group_id, uid)
                await service.remove_warning_count(
                    group_id, actor_id, uid, target_name=display,
                    actor_name=_display(_sender(event)),
                )
                if value == "حذف اخطارها":
                    await _send(event, reply, "✅ همهٔ اخطارهای کاربر حذف شد.\nتعداد اخطار: 0")
                else:
                    await _send(event, reply, "✅ تمام اخطارهای کاربر حذف شد.")
                await service.record_admin_action(
                    group_id, actor_id, actor_name=_display(_sender(event)), action=value,
                    target_id=uid, target_name=display, note=f"{before} → 0",
                )
            return True

        if value == "حذف حافظه":
            target = await _resolve_user(service, event, group_id)
            if target is None or _user_id(target) is None:
                await _send(event, reply, "❌ روی پیام کاربر ریپلای کنید.")
                return True
            uid = _user_id(target)
            display = _display(target)
            removed = await service.delete_memory(group_id, actor_id, uid)
            if removed:
                await _send(event, reply, "✅ حافظه کاربر حذف شد.")
                await service.record_admin_action(
                    group_id, actor_id, actor_name=_display(_sender(event)),
                    action="حذف حافظه کاربر", target_id=uid, target_name=display,
                )
            else:
                await _send(event, reply, "🧠 حافظه‌ای برای حذف ندارید.")
            return True

        if value == "لاگ مدیریتی":
            rows = await service.admin_log(group_id, actor_id)
            log_text, entities = _format_admin_log(rows)
            await _send(event, reply, log_text, formatting_entities=entities or None)
            return True
        if value in {"سابقه ها", "سابقه‌ها"}:
            rows = await service.history_24h(group_id, actor_id)
            history_text, entities = _format_history(rows)
            await _send(event, reply, history_text, formatting_entities=entities or None)
            return True

        # Single-member moderation and release.
        if value in {"سکوت", "رفع سکوت", "اخراج"}:
            target = await _resolve_user(service, event, group_id)
            if target is None or _user_id(target) is None:
                await _send(event, reply, "❌ باید روی پیام کاربر ریپلای کنید")
                return True
            uid = _user_id(target)
            display = _display(target)
            if value == "سکوت":
                report = await service.management.moderate_single_member(
                    group_id=group_id, actor_id=actor_id, user_id=uid, operation=ModerationKind.MUTE,
                )
                if report.succeeded:
                    await _send(event, reply, f"「 {display} 」**سکوت دائم شد** 🔊")
                    await service.record_admin_action(group_id, actor_id, actor_name=_display(_sender(event)), action="سکوت کاربر", target_id=uid, target_name=display)
                elif report.already_done:
                    await _send(event, reply, "⚠️ این کاربر از قبل سکوت است")
                else:
                    await _send(event, reply, "❌ انجام سکوت ناموفق بود")
                return True
            if value == "رفع سکوت":
                report = await service.management.unmute_single_member(group_id=group_id, actor_id=actor_id, user_id=uid)
                if report.succeeded:
                    await _send(event, reply, "🔊 سکوت کاربر برداشته شد")
                    await service.record_admin_action(group_id, actor_id, actor_name=_display(_sender(event)), action="رفع سکوت کاربر", target_id=uid, target_name=display)
                elif report.already_done:
                    await _send(event, reply, "⚠️ این کاربر در سکوت نبود")
                else:
                    await _send(event, reply, "❌ رفع سکوت انجام نشد")
                return True
            report = await service.management.moderate_single_member(
                group_id=group_id, actor_id=actor_id, user_id=uid, operation=ModerationKind.KICK,
            )
            if report.succeeded:
                await _send(event, reply, "⟳ کاربر اخراج شد")
                await service.record_admin_action(group_id, actor_id, actor_name=_display(_sender(event)), action="اخراج کاربر", target_id=uid, target_name=display)
            else:
                await _send(event, reply, "❌ اخراج کاربر انجام نشد" if report.failed else _reported_failure(report))
            return True

        if value == "آزاد" or value.startswith(("آزاد ", "آزاد@")):
            username_arg = value[4:].strip() if value != "آزاد" else ""
            if username_arg:
                username_arg = username_arg.split()[0].lstrip("@")
                if username_arg.isdigit():
                    await _send(event, reply, "❌ لطفاً از نام کاربری استفاده کنید، آیدی عددی پشتیبانی نمی‌شود")
                    return True
                if not _USERNAME.fullmatch(username_arg):
                    await _send(event, reply, f"❌ نام کاربری نامعتبر است: @{username_arg}")
                    return True
            target = await _resolve_user(service, event, group_id, username=username_arg or None)
            if target is None or _user_id(target) is None:
                await _send(event, reply, "❌ باید روی پیام کاربر ریپلای کنید یا به صورت «آزاد @username» بنویسید")
                return True
            uid = _user_id(target)
            report = await service.management.release_single_member(
                group_id=group_id, actor_id=actor_id, user_id=uid,
            )
            if report.succeeded:
                display = _display(target)
                await _send(event, reply, f"♻️ کاربر آزاد شد ᯓ⁪ [ {display} ]")
                await service.record_admin_action(group_id, actor_id, actor_name=_display(_sender(event)), action="آزاد کردن کاربر", target_id=uid, target_name=display)
            else:
                await _send(event, reply, "❌ آزاد کردن کاربر انجام نشد")
            return True

        # Pin commands: only `سنجاق` requires an admin; viewing is read-only.
        if value == "سنجاق":
            reply_id = _reply_message_id(event)
            if reply_id is None:
                await _send(event, reply, "❌ باید روی پیام موردنظر ریپلای کنید")
                return True
            pinned = await service.pin_message(group_id, actor_id, reply_id)
            if pinned:
                await _send(event, reply, "📌 پیام با موفقیت سنجاق شد.")
                await service.record_admin_action(group_id, actor_id, actor_name=_display(_sender(event)), action="سنجاق پیام", note=str(reply_id))
            else:
                await _send(event, reply, "❌ سنجاق پیام انجام نشد")
            return True
        if value == "پیام سنجاق":
            pinned_id = await service.pinned_message_id(group_id)
            if not pinned_id:
                await _send(event, reply, "📌 پیامی سنجاق نشده است.")
                return True
            try:
                pinned = await service.get_message(group_id, pinned_id)
            except Exception:  # noqa: BLE001 - read failure uses the source fallback
                pinned = None
            body = getattr(pinned, "raw_text", None) or getattr(pinned, "message", None)
            await _send(event, reply, str(body) if body else "📌 پیام سنجاق‌شده قابل نمایش نیست.")
            return True

        # Single and bulk message deletion. Bulk jobs are always scanned first,
        # require an explicit actor-bound confirmation, and re-fetch each item.
        if value == "پاک":
            reply_id = _reply_message_id(event)
            if reply_id is None:
                await _send(event, reply, "❌ باید روی پیام ریپلای کنید")
                return True
            deleted = await service.delete_single_message(
                group_id, actor_id, reply_id,
                actor_name=_display(_sender(event)),
                actor_username=getattr(_sender(event), "username", None),
            )
            await _send(event, reply, "✅ با موفقیت پاک شد" if deleted else "❌ پاک کردن پیام انجام نشد")
            return True
        delete_match = re.fullmatch(r"پاک\s+([0-9۰-۹٠-٩]+)", value)
        if delete_match:
            count = int(delete_match.group(1).translate(_DIGITS))
            plan = await service.prepare_delete_recent(group_id, actor_id, count)
            if not plan.get("scan_complete"):
                await _send(event, reply, "❌ اسکن پیام‌ها کامل نشد؛ هیچ پیامی حذف نشده است.")
                return True
            await _send(
                event,
                reply,
                f"⚠️ اسکن کامل شد: {plan['count']} پیام برای حذف آماده است.\n"
                f"تأیید: تایید پاک {plan['job_id']}\nلغو: لغو پاک {plan['job_id']}",
            )
            return True
        confirm_delete = re.fullmatch(r"تایید پاک\s+([A-Fa-f0-9]{12})", value)
        if confirm_delete:
            job_id = confirm_delete.group(1)
            deleted, failed = await service.confirm_delete_recent(
                group_id, actor_id, job_id, actor_name=_display(_sender(event)),
            )
            cleanup = service.store.get_group_state(group_id, "auto_cleanup")
            if isinstance(cleanup, dict) and cleanup.get("job_id") == job_id:
                cleanup["status"] = "completed" if failed == 0 else "completed_with_failures"
                service.store.set_group_state(group_id, "auto_cleanup", cleanup)
            await _send(event, reply, f"{deleted} پیام پاک شد 💣" if failed == 0 else f"{deleted} پیام پاک شد؛ {failed} پیام حذف نشد.")
            return True
        cancel_delete = re.fullmatch(r"لغو پاک\s+([A-Fa-f0-9]{12})", value)
        if cancel_delete:
            job_id = cancel_delete.group(1)
            cancelled = await service.cancel_delete_recent(group_id, actor_id, job_id)
            cleanup = service.store.get_group_state(group_id, "auto_cleanup")
            if cancelled and isinstance(cleanup, dict) and cleanup.get("job_id") == job_id:
                cleanup["status"] = "cancelled"
                service.store.set_group_state(group_id, "auto_cleanup", cleanup)
            await _send(event, reply, "✅ پاکسازی لغو شد." if cancelled else "⚠️ درخواست پیدا نشد یا منقضی شده است.")
            return True

        # AI/search access is stored per group; direct @username and reply both work.
        if value in {"هوش مصنوعی فعال", "هوش مصنوعی خاموش"}:
            enabled = value == "هوش مصنوعی فعال"
            await service.set_ai_enabled(group_id, actor_id, enabled)
            await _send(event, reply, "✅ هوش مصنوعی گوگل این گروه فعال شد." if enabled else "✅ هوش مصنوعی گوگل این گروه خاموش شد.")
            await service.record_admin_action(group_id, actor_id, actor_name=_display(_sender(event)), action=value)
            return True
        if value == "لیست هوش مصنوعی":
            users = await service.allowed_ai_users(group_id, actor_id)
            if not users:
                await _send(event, reply, "📋 هنوز کاربری برای هوش مصنوعی مجاز نشده است.")
                return True
            lines = ["✅ کاربران مجاز جستجوی گوگل:\n"]
            for index, item in enumerate(users, 1):
                lines.append(f"{index}- {_record_display(item)}")
            await _send(event, reply, "\n".join(lines))
            return True
        grant = value == "مجاز" or value.startswith("مجاز @")
        revoke = value in {"غیرمجاز", "غیر مجاز"} or value.startswith(("غیرمجاز @", "غیر مجاز @"))
        if grant or revoke:
            username_arg = None
            for prefix in ("مجاز ", "غیرمجاز ", "غیر مجاز "):
                if value.startswith(prefix) and "@" in value[len(prefix):]:
                    username_arg = value[len(prefix):].strip().split()[0]
                    break
            target = await _resolve_user(service, event, group_id, username=username_arg)
            if target is None or _user_id(target) is None:
                await _send(event, reply, "❌ باید روی پیام کاربر ریپلای کنید یا @username وارد کنید")
                return True
            uid = _user_id(target)
            display = _display(target)
            if grant:
                added = await service.allow_ai_user(
                    group_id, actor_id, uid, display_name=display,
                    username=getattr(target, "username", None),
                )
                if added:
                    await _send(event, reply, f"✅ کاربر : {display}\n\nبه لیست کاربران مجاز هوش مصنوعی گوگل اضافه شد.")
                    await service.record_admin_action(group_id, actor_id, actor_name=_display(_sender(event)), action="مجاز کردن هوش مصنوعی", target_id=uid, target_name=display)
                else:
                    await _send(event, reply, "⚠️ این کاربر از قبل در فهرست مجاز است.")
            else:
                removed = await service.disallow_ai_user(group_id, actor_id, uid)
                await _send(event, reply, "✅ دسترسی هوش مصنوعی گوگل کاربر حذف شد." if removed else "⚠️ این کاربر در لیست کاربران مجاز هوش مصنوعی گوگل نیست.")
                if removed:
                    await service.record_admin_action(group_id, actor_id, actor_name=_display(_sender(event)), action="حذف مجوز هوش مصنوعی", target_id=uid, target_name=display)
            return True

        # Name-filter list management. Terms remain group-scoped and are never
        # merged with the user's global banned-word file.
        name_filter_commands = (
            ("فهرست فیلتر نام", "list"), ("لیست فیلتر نام", "list"),
            ("فهرست فیلتر اسم", "list"), ("لیست فیلتر اسم", "list"),
        )
        list_name_command = next((cmd for cmd, _ in name_filter_commands if value == cmd), None)
        if list_name_command:
            terms = await service.require_admin(group_id, actor_id)
            del terms
            rows = service.list_name_filters(group_id)
            await _send(event, reply, "📋 هیچ نامی در فیلتر نیست" if not rows else "📋 فیلتر نام گروه:\n\n" + "\n".join(rows))
            return True
        add_prefix = next((prefix for prefix in ("فیلتر نام ", "فیلتر اسم ") if value.startswith(prefix)), None)
        remove_prefix = next((prefix for prefix in ("لغو فیلتر نام ", "لغو فیلتر اسم ") if value.startswith(prefix)), None)
        if add_prefix:
            term = value[len(add_prefix):].strip()
            added = await service.add_name_filter(group_id, actor_id, term)
            await _send(event, reply, f"✅ نام «{term}» به فیلتر اضافه شد." if added else "⚠️ این نام از قبل در فیلتر ثبت شده است.")
            return True
        if remove_prefix:
            term = value[len(remove_prefix):].strip()
            removed = await service.remove_name_filter(group_id, actor_id, term)
            await _send(event, reply, f"✅ نام «{term}» از فیلتر حذف شد." if removed else "⚠️ این نام در فیلتر نیست.")
            return True

        # No service command matched: allow the public-message pipeline to run.
        return False
    except PermissionError:
        if value == "لیست ادمین":
            message = "❌ فقط مالک یا ادمین ثبت‌شده اجازه استفاده دارد"
        elif value in {"صفر", "حذف اخطار", "حذف اخطارها"}:
            if value == "حذف اخطار":
                message = "❌ فقط مالک یا ادمین اجازه حذف اخطار را دارند"
            elif value == "حذف اخطارها":
                message = "❌ فقط مالک یا ادمین اجازه حذف اخطارها را دارند"
            else:
                message = "❌ فقط مالک اصلی ربات یا مالک گروه اجازه صفر کردن تخلفات را دارد"
        elif value in {"سابقه ها", "سابقه‌ها"}:
            message = "⛔️ این دستور فقط برای مالک و ادمین‌های گروه است."
        elif value in {"مهلت گروه"}:
            return True
        elif value in {"ثبت ادمین", "لغو ادمین", "برکناری ادمین"} or value.startswith(("ثبت ادمین ", "لغو ادمین ", "برکناری ادمین ")):
            message = "❌ فقط مالک گروه اجازه مدیریت ادمین‌ها را دارد"
        elif value in {"تغییر اخطار", "تغییر مجازات"}:
            message = "❌ فقط مالک اصلی ربات یا مالک همین گروه اجازه تغییر را دارد"
        else:
            message = "❌ فقط مالک یا ادمین‌های گروه اجازه استفاده از این دستور را دارند"
        await _send(event, reply, message)
        return True
    except (ValueError, IncompleteMemberScan) as exc:
        message = str(exc) if isinstance(exc, ValueError) else "❌ بررسی گروه کامل نشد؛ هیچ عملیات انبوهی اجرا نشد."
        await _send(event, reply, f"❌ {message}" if not message.startswith("❌") else message)
        return True
    except PlatformCapabilityUnavailable:
        await _send(event, reply, "❌ این قابلیت در نسخهٔ نصب‌شدهٔ SPlusthon در دسترس نیست.")
        return True
    except Exception:  # noqa: BLE001 - commands return concise failure; no per-message traceback logging
        await _send(event, reply, "❌ عملیات انجام نشد؛ وضعیت پلتفرم تأیید نشد.")
        return True
