from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from typing import Any

from foxbot.admin_commands import handle_group_admin_command
from foxbot.anti_spam import SpamDecision, SpamGuard, is_long_repetitive_payload
from foxbot.broadcast_commands import BroadcastService
from foxbot.fair_queue import FairModerationQueue
from foxbot.filters import FilterHit
from foxbot.game_commands import (
    EMOJI_RESET_COMMANDS,
    GAME_COMMANDS,
    has_active_answer_session,
)
from foxbot.group_admin import GroupAdminService
from foxbot.group_expiry import (
    build_confirmation as build_expiry_confirmation,
)
from foxbot.group_expiry import (
    is_expired as group_is_expired,
)
from foxbot.group_expiry import (
    match_expiry_command,
)
from foxbot.group_expiry import (
    set_expiry as set_group_expiry,
)
from foxbot.legacy_admins import LegacyAdminRegistry
from foxbot.management import MemberManagementService
from foxbot.message_actions import FairDeleteQueue
from foxbot.message_pipeline import IncomingMessage, MessagePipeline, PipelineResult
from foxbot.moderation_commands import handle_moderation_command
from foxbot.name_family import NameFamilyService
from foxbot.public_commands import (
    handle_group_level_before_late_workflow,
    handle_pre_game_public_command,
    handle_pre_workflow_public_command,
    handle_public_command,
)
from foxbot.rpc_queue import SerialRpcQueue
from foxbot.word_commands import handle_group_word_command
from foxbot.wordlists import GroupWordService

_OWNER_LIFECYCLE_COMMANDS = frozenset(
    {"فعال", "غیر فعال", "فعال سازی", "ثبت گروه", "ثبت مالک"}
)
_PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def _format_mute_duration(seconds: int) -> str:
    remaining = max(1, int(seconds))
    parts: list[str] = []
    for unit_seconds, label in ((86400, "روز"), (3600, "ساعت"), (60, "دقیقه")):
        amount, remaining = divmod(remaining, unit_seconds)
        if amount:
            parts.append(f"{amount} {label}".translate(_PERSIAN_DIGITS))
    if remaining:
        parts.append(f"{remaining} ثانیه".translate(_PERSIAN_DIGITS))
    return " و ".join(parts)


def _spam_target_ids(
    decision: SpamDecision,
    current_message_id: str,
    *,
    delete_history_enabled: bool,
) -> tuple[str, ...]:
    message_ids = tuple(dict.fromkeys((*decision.message_ids, str(current_message_id))))
    if decision.policy != "warning" and not delete_history_enabled:
        return (str(current_message_id),)
    return message_ids


def _normalize_command_text(value: Any) -> str:
    return " ".join(
        str(value or "")[:8000]
        .replace("\u200c", " ")
        .replace("\u200f", "")
        .replace("\u200e", "")
        .replace("\ufeff", "")
        .replace("ي", "ی")
        .replace("ك", "ک")
        .split()
    )


def _utf16_code_units(value: str) -> int:
    return sum(2 if ord(char) > 0xFFFF else 1 for char in value)


def _truncate_utf16(value: str, max_units: int) -> str:
    rendered: list[str] = []
    used = 0
    for char in str(value):
        width = 2 if ord(char) > 0xFFFF else 1
        if used + width > max_units:
            break
        rendered.append(char)
        used += width
    return "".join(rendered)


def _warning_with_status(body: str, status: str) -> str:
    suffix = f"\n{status}"
    available = max(0, 3500 - _utf16_code_units(suffix))
    return f"{_truncate_utf16(body, available)}{suffix}"


_COMMAND_EXACT = frozenset(
    {
        "help", "راهنما", "آمار", "stats", "آمار گروه", "آمار گپ", "آمارم",
        "فروشگاه", "موجودی", "کیف پول", "انتقال سکه", "ربات", "روباه",
        "پینگ ربات", "وضعیت ربات", "لیست کاربران", "لیست ادمین",
        "لیست ادمینی", "لیست سکوت", "لیست بازی", "لیست بازی ها",
        "لیست بازی‌ها", "بازی ها", "بازی‌ها", "فیلترها", "/فیلترها",
        "راهنمای امتیاز", "امتیاز من", "رتبه ها", "رتبه‌ها", "بیوگرافی",
        "ثبت اصل", "اصلم", "یاد آوری", "ترجمه", "حافظه من", "حذف اسم",
        "ثبت پرفایل", "ثبت پروفایل", "پرفایلم", "پروفایلم", "حذف پرفایل",
        "حذف پروفایل", "سایت بازی", "سایت", "لینک بازی", "/game", "/site",
        "تست دکمه", "دانستنی", "جک", "قفل", "باز", "سکوت", "رفع سکوت",
        "اخراج", "آزاد", "بن", "پاک", "اخطار", "صفر", "حذف اخطار",
        "حذف اخطارها", "ثبت قوانین", "قوانین", "حذف قوانین", "حذف حافظه",
        "ثبت ادمین", "لغو ادمین", "برکناری ادمین", "vip", "لغو vip",
        "لیست vip", "سنجاق", "پیام سنجاق", "لاگ مدیریتی", "سابقه ها",
        "سابقه‌ها", "ریست آمار", "مهلت گروه", "سرگرمی فعال", "سرگرمی خاموش",
        "پاکسازی خودکار", "تغییر اخطار", "تغییر مجازات", "هوش مصنوعی فعال",
        "هوش مصنوعی خاموش", "لیست هوش مصنوعی", "مجاز", "غیرمجاز", "غیر مجاز",
        "لیست انقضا", "سطح گروه", "حذف گروه", "فعال", "غیر فعال", "فعال سازی",
        "ثبت گروه", "ثبت مالک",
        "لغو مالک", "برکناری مالک", "فعال کلمات ممنوعه", "لغو کلمات ممنوعه",
        "ریست اخراجی ها", "رفع سکوت همه", "کپی بورد", "کپی",
        "فهرست فیلتر نام", "لیست فیلتر نام", "فهرست فیلتر اسم", "لیست فیلتر اسم",
    }
) | GAME_COMMANDS | EMOJI_RESET_COMMANDS
_COMMAND_PREFIXES = (
    "ثبت ", "ثبت اسم ", "شخصیت ", "فونت ", "جستجو ", "ترجمه ",
    "یاد آوری ", "فیلتر ", "رفع ", "اخطار ", "تغییر اخطار ",
    "تغییر مجازات ", "سکوت ", "اخراج ", "آزاد ", "آزاد@", "پاک ",
    "تایید ", "تأیید ", "لغو ", "برکناری ادمین ", "ثبت ادمین ",
    "لغو ادمین ", "مجاز @", "غیرمجاز @", "غیر مجاز @", "فیلتر نام ",
    "فیلتر اسم ", "لغو فیلتر نام ", "لغو فیلتر اسم ", "!", "/", ".",
)
_LATE_PENDING_WORKFLOWS = frozenset(
    {"auto_cleanup", "warning_threshold", "punishment_mode"}
)
_EARLY_ADMIN_ROUTES_BY_WORKFLOW = {
    "auto_cleanup": frozenset(
        {"صفر", "حذف اخطار", "حذف اخطارها", "لاگ مدیریتی", "سابقه ها", "پاکسازی خودکار"}
    ),
    "warning_threshold": frozenset(
        {
            "صفر", "حذف اخطار", "حذف اخطارها", "لاگ مدیریتی", "سابقه ها",
            "پاکسازی خودکار", "تغییر اخطار",
        }
    ),
    "punishment_mode": frozenset(
        {
            "صفر", "حذف اخطار", "حذف اخطارها", "لاگ مدیریتی", "سابقه ها",
            "پاکسازی خودکار", "تغییر اخطار", "تغییر مجازات",
        }
    ),
}


def _is_likely_command(normalized_text: str) -> bool:
    value = _normalize_command_text(normalized_text).casefold()
    return value in _COMMAND_EXACT or value.startswith(_COMMAND_PREFIXES)


# Mirror the source dispatcher’s admin-priority vocabulary for this narrow
# forward exemption. It intentionally excludes public commands such as
# «راهنما» and prefixed aliases such as !help after their prefix is stripped.
_FORWARD_MANAGEMENT_EXACT = frozenset(
    {
        "بن", "اخراج", "قفل", "باز", "پاک", "سکوت", "رفع سکوت", "آزاد",
        "اخطار", "ثبت ادمین", "لغو ادمین", "برکناری ادمین", "فعال",
        "غیر فعال", "فعال سازی", "ثبت گروه", "حذف گروه", "ثبت مالک",
        "لغو مالک", "برکناری مالک", "لیست انقضا", "مهلت گروه", "صفر",
        "هوش مصنوعی فعال", "هوش مصنوعی خاموش", "مجاز", "غیرمجاز",
        "غیر مجاز", "لیست هوش مصنوعی", "پاکسازی خودکار", "لاگ مدیریتی",
        "ثبت قوانین", "حذف قوانین", "ریست آمار", "ریست اخراجی ها",
        "حذف اخطار", "حذف اخطارها", "اطلاع رسانی", "تایید", "✅ تایید",
        "لغو", "❌ لغو", "لیست ادمین", "لیست ادمینی", "سنجاق",
        "فعال کلمات ممنوعه", "لغو کلمات ممنوعه", "سابقه ها", "سابقه‌ها",
        "تغییر اخطار", "تغییر مجازات", "وضعیت ربات", "پینگ ربات",
        "کپی بورد", "کپی",
    }
)
_FORWARD_MANAGEMENT_PREFIXES = (
    "پاک ", "بن ", "اخراج ", "سکوت ", "ثبت ادمین", "لغو ادمین",
    "برکناری ادمین", "مجاز ", "غیر مجاز", "غیرمجاز",
)
_FORWARD_PREFIXED_MANAGEMENT_EXACT = frozenset(
    {
        "!addword", "!addban", "!افزودن", "!remword", "!removeword",
        "!حذف", "!stats", "!آمار", "!reset", "!ریست", "!lock",
        "!قفل", "!unlock", "!باز", "!timelock", "!قفل_ساعتی",
        "!photo", "!عکس", "!title", "!اسم", "!راهنما", "/فیلترها",
    }
)
_FORWARD_PREFIXED_MANAGEMENT_PREFIXES = (
    "!addword ", "!addban ", "!افزودن ", "!remword ", "!removeword ",
    "!حذف ", "!reset ", "!ریست ", "!timelock ", "!قفل_ساعتی ",
    "!photo ", "!عکس ", "!title ", "!اسم ", "/فیلتر ", "/رفع ",
)


def _is_forward_management_command(normalized_text: str) -> bool:
    value = _normalize_command_text(normalized_text).casefold()
    if value in _FORWARD_PREFIXED_MANAGEMENT_EXACT or value.startswith(
        _FORWARD_PREFIXED_MANAGEMENT_PREFIXES
    ):
        return True
    if value[:1] in {".", "/", "!"}:
        value = value.lstrip("./!").strip()
        if not value:
            return False
    return value in _FORWARD_MANAGEMENT_EXACT or value.startswith(
        _FORWARD_MANAGEMENT_PREFIXES
    )


def _forward_metadata(event: Any) -> bool:
    message = getattr(event, "message", None) or event
    header_names = (
        "fwd_from",
        "forward_header",
        "forwarded_header",
        "forward",
        "forward_from",
        "forward_chat",
        "forward_from_chat",
        "forward_sender_name",
        "fwd_from_id",
        "forward_from_id",
        "forward_date",
    )
    flag_names = ("is_forward", "is_forwarded", "forwarded")
    for obj in (message, event):
        for name in header_names:
            try:
                value = getattr(obj, name, None)
            except (AttributeError, RuntimeError, TypeError):
                continue
            if value is None or value is False or callable(value):
                continue
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value == 0:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            if isinstance(value, bytes) and not value:
                continue
            return True
        for name in flag_names:
            try:
                value = getattr(obj, name, None)
            except (AttributeError, RuntimeError, TypeError):
                continue
            if value is True:
                return True
    # A view/forward counter is not provenance metadata.
    return False


def _grouped_id(event: Any) -> str | int | None:
    message = getattr(event, "message", None) or event
    value = getattr(message, "grouped_id", None)
    if value is None:
        value = getattr(event, "grouped_id", None)
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    rendered = str(value).strip()
    return value if rendered and rendered != "0" else None


def _expiry_entities(spans: list[tuple[str, int, int]]) -> list[Any] | None:
    try:
        from splusthon.tl import types
    except ImportError:
        return None
    names = {"bold": "MessageEntityBold", "blockquote": "MessageEntityBlockquote"}
    entities: list[Any] = []
    for kind, offset, length in spans:
        cls = getattr(types, names.get(kind, ""), None)
        if cls is None:
            return None
        entities.append(cls(offset=offset, length=length))
    return entities


def _read_process_rss_mb() -> float | None:
    try:
        with open("/proc/self/status", encoding="utf-8") as status_file:
            for line in status_file:
                if line.startswith("VmRSS:"):
                    return float(line.split()[1]) / 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


class SPlusthonEventBridge:
    """Bind filtering, legacy group-word commands and kick events to updates."""

    def __init__(
        self,
        pipeline: MessagePipeline,
        management: MemberManagementService,
        word_lists: GroupWordService | None = None,
        rpc_queue: SerialRpcQueue | None = None,
        group_admin: GroupAdminService | None = None,
        platform: Any | None = None,
        global_owner_check: Callable[[str | int], bool] | None = None,
        legacy_admin_registry: LegacyAdminRegistry | None = None,
        spam_guard: SpamGuard | None = None,
        delete_queue: FairDeleteQueue | None = None,
        site_link_policy_enabled: bool | Callable[[], bool] = True,
        delete_spam_enabled: bool = True,
        check_ad_names_enabled: bool = True,
        check_phone_numbers_enabled: bool = True,
        check_usernames_enabled: bool = True,
        check_forwarded_enabled: bool = False,
        send_spam_warnings_enabled: bool = True,
        action_duration_seconds: int = 3600,
        warning_message_template: str | None = None,
        name_family_service: NameFamilyService | None = None,
    ) -> None:
        self.pipeline = pipeline
        self.management = management
        self.word_lists = word_lists
        self.rpc_queue = rpc_queue
        self.group_admin = group_admin
        self.platform = platform
        self.global_owner_check = global_owner_check
        self.legacy_admin_registry = legacy_admin_registry
        self.name_family_service = name_family_service
        self.delete_spam_enabled = bool(delete_spam_enabled)
        self.check_ad_names_enabled = bool(check_ad_names_enabled)
        self.send_spam_warnings_enabled = bool(send_spam_warnings_enabled)
        self.check_forwarded_enabled = bool(check_forwarded_enabled)
        self.action_duration_seconds = int(action_duration_seconds)
        if not 0 < self.action_duration_seconds <= 40 * 24 * 60 * 60:
            raise ValueError("automatic action duration must be within 1 second and 40 days")
        self.warning_message_template = warning_message_template
        self.delete_queue = delete_queue or FairDeleteQueue(
            self.pipeline.delete_message_callback
        )
        self.pipeline.set_delete_message(self.delete_queue.delete_one)
        self.spam_guard = spam_guard or SpamGuard(
            generic_exemption=self._game_answer_exemption,
            site_link_policy_enabled=site_link_policy_enabled,
            check_phone_numbers=check_phone_numbers_enabled,
            check_usernames=check_usernames_enabled,
            check_forwarded=check_forwarded_enabled,
        )
        self.moderation_queue = FairModerationQueue()
        self._started_at = time.monotonic()
        self._native_admin_cache: OrderedDict[
            tuple[str, str], tuple[float, bool | None]
        ] = OrderedDict()
        self._native_admin_lookup_locks: OrderedDict[
            tuple[str, str], asyncio.Lock
        ] = OrderedDict()
        self._legacy_admin_error_log: OrderedDict[str, float] = OrderedDict()
        self._formatting_fallback_log: OrderedDict[str, float] = OrderedDict()
        self._spam_event_log: OrderedDict[str, float] = OrderedDict()
        self._last_broadcast_error_log = -60.0
        self._warning_notice_users: OrderedDict[tuple[str, str], float] = OrderedDict()
        self._warning_notice_groups: OrderedDict[str, float] = OrderedDict()
        self._ad_name_action_cooldowns: OrderedDict[tuple[str, str], float] = OrderedDict()
        self._moderation_tasks: dict[tuple[str, str], asyncio.Task[Any]] = {}
        self._moderation_kinds: dict[tuple[str, str], str] = {}
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._closed = False
        self.broadcast_service = (
            BroadcastService(
                self.group_admin.store,
                self.platform,
                self.group_admin.access.owner_registry.is_global_owner,
            )
            if self.group_admin is not None and self.platform is not None
            else None
        )

    def _warn_legacy_admin_failure(self, group_id: str | int, error: Exception) -> None:
        key = str(group_id)
        now = time.monotonic()
        previous = self._legacy_admin_error_log.get(key)
        if previous is not None and now - previous < 60.0:
            return
        self._legacy_admin_error_log[key] = now
        self._legacy_admin_error_log.move_to_end(key)
        while len(self._legacy_admin_error_log) > 1024:
            self._legacy_admin_error_log.popitem(last=False)
        logging.getLogger(__name__).warning(
            "legacy admin promotion failed group=%s error=%s",
            key,
            type(error).__name__,
        )

    def _warn_formatting_fallback(
        self, group_id: str | int | None, error: Exception
    ) -> None:
        key = str(group_id or "unknown")
        now = time.monotonic()
        previous = self._formatting_fallback_log.get(key)
        if previous is not None and now - previous < 60.0:
            return
        self._formatting_fallback_log[key] = now
        self._formatting_fallback_log.move_to_end(key)
        while len(self._formatting_fallback_log) > 1024:
            self._formatting_fallback_log.popitem(last=False)
        logging.getLogger(__name__).warning(
            "formatted reply failed; retrying plain group=%s error=%s",
            key,
            type(error).__name__,
        )

    def _warn_broadcast_failure(self, error: Exception) -> None:
        now = time.monotonic()
        if now - self._last_broadcast_error_log < 60.0:
            return
        self._last_broadcast_error_log = now
        logging.getLogger(__name__).warning(
            "global broadcast workflow failed error=%s", type(error).__name__
        )

    def _game_answer_exemption(self, group_id: str, user_id: str) -> bool:
        if self.group_admin is None:
            return False
        return has_active_answer_session(
            self.group_admin.store, group_id, user_id
        )

    def _spawn(self, awaitable: Any, *, name: str) -> asyncio.Task[Any]:
        task = asyncio.create_task(awaitable, name=name)
        self._background_tasks.add(task)

        def finished(done: asyncio.Task[Any]) -> None:
            self._background_tasks.discard(done)
            if done.cancelled():
                return
            try:
                error = done.exception()
            except asyncio.CancelledError:
                return
            if error is not None:
                logging.getLogger(__name__).warning(
                    "background moderation task failed error=%s",
                    type(error).__name__,
                )

        task.add_done_callback(finished)
        return task

    def _log_spam(self, group_id: str | int, kind: str, outcome: str = "detected") -> None:
        key = str(group_id)
        now = time.monotonic()
        previous = self._spam_event_log.get(key)
        if previous is not None and now - previous < 30.0:
            return
        self._spam_event_log[key] = now
        self._spam_event_log.move_to_end(key)
        while len(self._spam_event_log) > 1024:
            self._spam_event_log.popitem(last=False)
        logging.getLogger(__name__).info(
            "anti-spam group=%s kind=%s outcome=%s", key, kind, outcome
        )

    def _record_spam_deletion(self, group_id: str, _message_id: str) -> None:
        if self.group_admin is not None:
            self.group_admin.store.increment_group_stat(group_id, "deleted")

    def _warning_notice_allowed(self, group_id: str, user_id: str) -> bool:
        now = time.monotonic()
        user_key = (str(group_id), str(user_id))
        if now - self._warning_notice_users.get(user_key, -60.0) < 20.0:
            return False
        if now - self._warning_notice_groups.get(str(group_id), -3.0) < 3.0:
            return False
        self._warning_notice_users[user_key] = now
        self._warning_notice_users.move_to_end(user_key)
        self._warning_notice_groups[str(group_id)] = now
        self._warning_notice_groups.move_to_end(str(group_id))
        while len(self._warning_notice_users) > 4096:
            self._warning_notice_users.popitem(last=False)
        while len(self._warning_notice_groups) > 1024:
            self._warning_notice_groups.popitem(last=False)
        return True

    async def _record_automatic_warning(
        self,
        event: Any,
        message: IncomingMessage,
        *,
        target_name: str,
        reason: str,
        deletion_confirmed: bool,
    ) -> int | None:
        if self.group_admin is None:
            return None
        count, threshold = self.group_admin.add_automatic_warning(
            message.group_id,
            message.user_id,
            target_name=target_name,
            reason=reason,
            message_id=message.message_id,
        )
        if (
            self.send_spam_warnings_enabled
            and self._warning_notice_allowed(message.group_id, message.user_id)
        ):
            deletion_text = (
                "پیام حذف شد."
                if deletion_confirmed
                else "تخلف ثبت شد، اما حذف پیام از سروش تأیید نشد."
            )
            display_name = _truncate_utf16(
                target_name or message.user_id, 128
            )
            safe_reason = _truncate_utf16(reason, 256)
            default_text = (
                f"⚠️ {display_name}\n"
                f"{deletion_text} دلیل: {safe_reason}\n"
                f"اخطار: {count}/{threshold}"
            )
            warning_text = default_text
            if self.warning_message_template:
                try:
                    warning_body = self.warning_message_template.format_map(
                        {
                            "username": display_name,
                            "reason": safe_reason,
                            "count": count,
                            "threshold": threshold,
                        }
                    )
                except (KeyError, ValueError, IndexError):
                    pass
                else:
                    warning_text = _warning_with_status(warning_body, deletion_text)
            warning_text = _truncate_utf16(warning_text, 3500)
            await self._send_moderation_notice(
                event, warning_text, message.group_id
            )
        if count >= threshold:
            mode = self.group_admin.punishment_mode(message.group_id)
            self._schedule_automatic_action(
                event,
                message,
                kind="warning_threshold",
                reason="رسیدن به سقف اخطار گروه",
                mode=mode,
                duration_seconds=(
                    self.action_duration_seconds if mode == "mute" else None
                ),
                clear_warnings=True,
                notify=False,
            )
        return count

    async def _send_moderation_notice(
        self, event: Any, text: str, group_id: str
    ) -> None:
        try:
            await self._reply(event, text)
        except Exception as exc:  # noqa: BLE001 - notification failure never changes action status
            self._log_spam(group_id, "notice", type(exc).__name__)

    def _schedule_automatic_action(
        self,
        event: Any,
        message: IncomingMessage,
        *,
        kind: str,
        reason: str,
        mode: str,
        duration_seconds: int | None,
        clear_warnings: bool = False,
        notify: bool = False,
    ) -> bool:
        key = (message.group_id, message.user_id)
        current = self._moderation_tasks.get(key)
        if current is not None and current.done():
            self._moderation_tasks.pop(key, None)
            self._moderation_kinds.pop(key, None)
        previous = current if current is not None and not current.done() else None
        if previous is not None and self._moderation_kinds.get(key) == kind:
            return False
        if (previous is None and len(self._moderation_tasks) >= 256) or self._closed:
            self.spam_guard.resolve_action(
                message.group_id, message.user_id, kind, success=False
            )
            self._log_spam(message.group_id, kind, "moderation_queue_full")
            return False

        async def run_in_user_order() -> None:
            if previous is not None:
                try:
                    await asyncio.shield(previous)
                except asyncio.CancelledError:
                    if self._closed:
                        raise
                except Exception as exc:  # noqa: BLE001 - prior action failure does not block a newer signal
                    self._log_spam(
                        message.group_id,
                        kind,
                        f"previous_action_{type(exc).__name__}",
                    )
            try:
                accepted, _result = await self.moderation_queue.submit(
                    message.group_id,
                    lambda: self._run_automatic_action(
                        event,
                        message,
                        kind=kind,
                        reason=reason,
                        mode=mode,
                        duration_seconds=duration_seconds,
                        clear_warnings=clear_warnings,
                        notify=notify,
                    ),
                )
                if not accepted:
                    self.spam_guard.resolve_action(
                        message.group_id, message.user_id, kind, success=False
                    )
                    self._log_spam(message.group_id, kind, "moderation_queue_full")
            except asyncio.CancelledError:
                self.spam_guard.resolve_action(
                    message.group_id, message.user_id, kind, success=False
                )
                raise
            except Exception as exc:  # noqa: BLE001 - queue errors must not look like successful actions
                self.spam_guard.resolve_action(
                    message.group_id, message.user_id, kind, success=False
                )
                self._log_spam(message.group_id, kind, type(exc).__name__)

        task = self._spawn(
            run_in_user_order(),
            name=f"foxbot:moderation:{message.group_id}:{message.user_id}",
        )
        self._moderation_tasks[key] = task
        self._moderation_kinds[key] = kind

        def remove_pending(done: asyncio.Task[Any]) -> None:
            if self._moderation_tasks.get(key) is done:
                self._moderation_tasks.pop(key, None)
                self._moderation_kinds.pop(key, None)

        task.add_done_callback(remove_pending)
        return True

    async def _run_automatic_action(
        self,
        event: Any,
        message: IncomingMessage,
        *,
        kind: str,
        reason: str,
        mode: str,
        duration_seconds: int | None,
        clear_warnings: bool,
        notify: bool,
    ) -> None:
        try:
            result = await self.management.automatic_action(
                group_id=message.group_id,
                user_id=message.user_id,
                mode=mode,
                reason=reason,
                duration_seconds=duration_seconds,
            )
            succeeded = bool(result.success and result.verified)
            self.spam_guard.resolve_action(
                message.group_id, message.user_id, kind, success=succeeded
            )
            if not succeeded:
                self._log_spam(
                    message.group_id,
                    kind,
                    result.code or "action_not_verified",
                )
                return
            if clear_warnings and self.group_admin is not None:
                self.group_admin.store.reset_warning_count(
                    message.group_id, message.user_id
                )
            if (
                notify
                and self.send_spam_warnings_enabled
                and self._warning_notice_allowed(message.group_id, message.user_id)
            ):
                if mode == "ban":
                    action_text = "از گروه اخراج شد"
                elif mode == "mute" and duration_seconds is None:
                    action_text = "سکوت دائم شد"
                elif mode == "mute" and duration_seconds == 3600:
                    action_text = "یک ساعت سکوت شد"
                elif mode == "mute" and duration_seconds == 24 * 60 * 60:
                    action_text = "۲۴ ساعت سکوت شد"
                elif mode == "mute":
                    action_text = (
                        f"به مدت {_format_mute_duration(duration_seconds or 1)} سکوت شد"
                    )
                else:
                    action_text = "محدود شد"
                target_name = self._display_name(getattr(event, "sender", None))
                notice = (
                    f"🔹کاربر ← {target_name or message.user_id}\n\n"
                    f"به دلیل {reason} {action_text}"
                )
                await self._send_moderation_notice(event, notice, message.group_id)
            self._log_spam(message.group_id, kind, "action_verified")
        except asyncio.CancelledError:
            self.spam_guard.resolve_action(
                message.group_id, message.user_id, kind, success=False
            )
            raise
        except Exception as exc:  # noqa: BLE001 - an uncertain action is never reported as successful
            self.spam_guard.resolve_action(
                message.group_id, message.user_id, kind, success=False
            )
            self._log_spam(message.group_id, kind, type(exc).__name__)

    @staticmethod
    def _display_name(sender: Any) -> str:
        if sender is None:
            return ""
        name = " ".join(
            value
            for part in (
                getattr(sender, "first_name", None),
                getattr(sender, "last_name", None),
            )
            if part and (value := str(part)[:128].strip())
        ).strip()
        username = str(getattr(sender, "username", None) or "")[:64].strip().lstrip("@")
        return _truncate_utf16(name or (f"@{username}" if username else ""), 128)

    def _sender_name_has_approved_term(self, sender: Any) -> bool:
        """Check bounded sender fields against only the user-approved global list."""
        if sender is None:
            return False
        for attribute, limit in (
            ("first_name", 128),
            ("last_name", 128),
            ("username", 64),
        ):
            try:
                value = getattr(sender, attribute, None)
                if value is None:
                    continue
                candidate = (
                    value if isinstance(value, str) else str(value)
                )[:limit].strip()
            except (AttributeError, TypeError, ValueError, RuntimeError):
                continue
            candidates = [candidate]
            if attribute == "username" and "_" in candidate:
                candidates.append(candidate.replace("_", " "))
            if any(
                item
                and self.pipeline.word_filter.match_global(item) is not None
                for item in candidates
            ):
                return True
        return False

    def _reserve_ad_name_action(self, group_id: str, user_id: str) -> bool:
        key = (str(group_id), str(user_id))
        now = time.monotonic()
        expires_at = self._ad_name_action_cooldowns.get(key)
        if expires_at is not None and expires_at > now:
            self._ad_name_action_cooldowns.move_to_end(key)
            return False
        self._ad_name_action_cooldowns.pop(key, None)
        self._ad_name_action_cooldowns[key] = now + 300.0
        while len(self._ad_name_action_cooldowns) > 4096:
            self._ad_name_action_cooldowns.popitem(last=False)
        return True

    def _release_ad_name_action(self, group_id: str, user_id: str) -> None:
        self._ad_name_action_cooldowns.pop((str(group_id), str(user_id)), None)

    async def _handle_spam_decision(
        self, event: Any, message: IncomingMessage, decision: SpamDecision
    ) -> bool:
        allowed = await self._filter_delete_allowed(
            message,
            FilterHit(scope="anti_spam", original_term=decision.reason),
        )
        if not allowed:
            self.spam_guard.clear_user(message.group_id, message.user_id)
            self._log_spam(message.group_id, decision.kind, "protected_or_unknown_actor")
            return False

        message_ids = _spam_target_ids(
            decision,
            message.message_id,
            delete_history_enabled=self.delete_spam_enabled,
        )
        if decision.policy == "warning":
            delete_confirmed = False
            if message_ids:
                delete_confirmed = await self.delete_queue.delete_one(
                    message.group_id, message_ids[0]
                )
                if delete_confirmed:
                    self._record_spam_deletion(message.group_id, message_ids[0])
                else:
                    self._log_spam(message.group_id, decision.kind, "delete_not_confirmed")
            if self.group_admin is not None:
                await self._record_automatic_warning(
                    event,
                    message,
                    target_name=self._display_name(getattr(event, "sender", None)),
                    reason=decision.reason,
                    deletion_confirmed=delete_confirmed,
                )
            return True

        def deletion_succeeded(group_id: str, message_id: str) -> None:
            self._record_spam_deletion(group_id, message_id)

        accepted = self.delete_queue.enqueue(
            message.group_id,
            message_ids,
            max_attempts=3,
            on_success=deletion_succeeded,
        )
        if accepted < len(message_ids):
            self._log_spam(message.group_id, decision.kind, "delete_backlog_limited")
        else:
            self._log_spam(message.group_id, decision.kind)

        if decision.action_required:
            mode = (
                decision.action
                if decision.policy in {"ban", "fixed_mute"}
                else self.group_admin.punishment_mode(message.group_id)
                if self.group_admin is not None
                else "ban"
            )
            self._schedule_automatic_action(
                event,
                message,
                kind=decision.kind,
                reason=decision.reason,
                mode=mode or "ban",
                duration_seconds=decision.duration_seconds,
                notify=decision.notify,
            )
        return True

    async def close(self, *, drain: bool = True) -> None:
        self._closed = True
        if not drain:
            for task in tuple(self._background_tasks):
                task.cancel()
        while self._background_tasks:
            tasks = tuple(self._background_tasks)
            if not drain:
                for task in tasks:
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.moderation_queue.close(drain=drain)
        await self.delete_queue.close(drain=drain)
        while self._background_tasks:
            tasks = tuple(self._background_tasks)
            if not drain:
                for task in tasks:
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _reply(
        self,
        event: Any,
        text: str,
        *,
        formatting_entities: list[Any] | None = None,
        buttons: Any | None = None,
        prefer_respond: bool = False,
    ) -> Any:
        sender = (
            getattr(event, "respond", None) if prefer_respond else None
        ) or getattr(event, "reply", None) or getattr(event, "respond", None)
        if not callable(sender):
            raise TypeError("SPlusthon event has no supported reply method")

        async def send(*, include_entities: bool) -> Any:
            kwargs = (
                {"formatting_entities": formatting_entities}
                if include_entities and formatting_entities
                else {}
            )
            if buttons is not None:
                kwargs["buttons"] = buttons
            result = sender(text, **kwargs)
            return await result if inspect.isawaitable(result) else result

        group_id = getattr(event, "chat_id", None)

        async def submit(*, include_entities: bool) -> Any:
            async def operation() -> Any:
                return await send(include_entities=include_entities)

            if self.rpc_queue is None or group_id is None:
                return await operation()
            return await self.rpc_queue.submit(
                str(group_id),
                "event.reply",
                operation,
                priority=1,
                retry_safe=False,
                max_attempts=1,
            )

        try:
            return await submit(include_entities=True)
        except Exception as exc:
            if not formatting_entities:
                raise
            self._warn_formatting_fallback(group_id, exc)
            return await submit(include_entities=False)

    async def _health_report(self, event: Any) -> str:
        loop_started = time.perf_counter()
        await asyncio.sleep(0.05)
        loop_lag_ms = max(0.0, (time.perf_counter() - loop_started - 0.05) * 1000)

        rss_mb = await asyncio.to_thread(_read_process_rss_mb)

        client = getattr(self.platform, "client", None)
        get_me = getattr(client, "get_me", None)
        rpc_ms: float | None = None
        if callable(get_me) and self.rpc_queue is not None:
            async def ping() -> Any:
                result = get_me()
                if inspect.isawaitable(result):
                    return await asyncio.wait_for(result, timeout=10)
                return result

            ping_started = time.perf_counter()
            try:
                await asyncio.wait_for(
                    self.rpc_queue.submit(
                        str(getattr(event, "chat_id", None) or "health"),
                        "owner.health_ping",
                        ping,
                        priority=5,
                        retry_safe=False,
                        max_attempts=1,
                    ),
                    timeout=11,
                )
                rpc_ms = (time.perf_counter() - ping_started) * 1000
            except Exception:  # noqa: BLE001 - only a compact failure status is shown
                rpc_ms = None

        metrics = self.rpc_queue.metrics if self.rpc_queue is not None else {}
        delete_pending = self.delete_queue.pending_count
        moderation_pending = self.moderation_queue.pending_count
        uptime_seconds = max(0, int(time.monotonic() - self._started_at))
        hours, remainder = divmod(uptime_seconds, 3600)
        minutes = remainder // 60
        verdict: list[str] = []
        if loop_lag_ms >= 100:
            verdict.append("⚠️ تأخیر حلقه بالاست؛ فشار پردازشی داخلی را بررسی کنید.")
        if rss_mb is not None and rss_mb >= 500:
            verdict.append("⚠️ مصرف حافظهٔ فرایند بالاست.")
        if int(metrics.get("queued", 0)) >= 20:
            verdict.append("⚠️ صف RPC تحت فشار است.")
        if delete_pending >= 100 or moderation_pending >= 50:
            verdict.append("⚠️ صف اقدام‌های خودکار تحت فشار است.")
        if rpc_ms is not None and rpc_ms >= 1000 and loop_lag_ms < 100:
            verdict.append("🌐 RPC کند است؛ شبکه/سرویس پلتفرم یا صف را بررسی کنید.")
        if rpc_ms is None:
            verdict.append("⚠️ آزمون RPC ناموفق یا خارج از مهلت بود.")
        if not verdict:
            verdict.append("✅ شاخص‌های محلی و آزمون RPC در محدودهٔ عادی‌اند.")

        memory = f"{rss_mb:.0f} MB" if rss_mb is not None else "نامشخص"
        rpc = f"{rpc_ms:.0f} ms" if rpc_ms is not None else "خطا/مهلت"
        return (
            "🩺 وضعیت ربات\n\n"
            f"⏳ مدت اجرا: {hours} ساعت و {minutes} دقیقه\n"
            f"🧠 حافظهٔ فرایند (RSS): {memory}\n"
            f"🌀 تأخیر حلقهٔ رویداد: {loop_lag_ms:.0f} ms\n"
            f"🌐 آزمون RPC سروش: {rpc}\n"
            f"📡 صف خروجی: {metrics.get('queued', 0)} منتظر | "
            f"{metrics.get('outstanding', 0)} در حال انجام | "
            f"{metrics.get('failed', 0)} خطای تجمعی\n"
            f"🧰 صف اقدام خودکار: {delete_pending} حذف | "
            f"{moderation_pending} عملیات مدیریتی منتظر\n\n"
            + "\n".join(verdict)
        )

    @staticmethod
    def _media_marker(message: Any, name: str) -> bool:
        try:
            value = getattr(message, name, None)
        except (AttributeError, RuntimeError, TypeError):
            return False
        return not callable(value) and bool(value)

    @staticmethod
    def _media_kind(event: Any) -> str | None:
        message = getattr(event, "message", None) or event
        media = getattr(message, "media", None)
        document = getattr(message, "document", None) or getattr(media, "document", None)
        mime_type = str(getattr(document, "mime_type", None) or "").lower()
        attributes = getattr(document, "attributes", None) or ()
        attribute_names = tuple(type(attribute).__name__.lower() for attribute in attributes)
        if (
            SPlusthonEventBridge._media_marker(message, "gif")
            or SPlusthonEventBridge._media_marker(message, "animation")
            or mime_type == "image/gif"
            or any("animated" in name or "gif" in name for name in attribute_names)
        ):
            return "gif"
        if (
            SPlusthonEventBridge._media_marker(message, "video")
            or mime_type.startswith("video/")
            or any("video" in name for name in attribute_names)
        ):
            return "video"
        return None

    async def _handle_expiry_command(
        self,
        event: Any,
        group_id: str | int,
        command: str,
        *,
        is_global_owner: bool,
    ) -> bool:
        if self.group_admin is None:
            return False
        if not is_global_owner:
            return True

        store = self.group_admin.store
        was_expired = group_is_expired(store, group_id)
        registry = store.get_group_state(group_id, "group_registry", {})
        registry = dict(registry) if isinstance(registry, dict) else {}
        previous_expiry = store.get_group_state(group_id, "expiry", {})
        previous_expiry = previous_expiry if isinstance(previous_expiry, dict) else {}
        chat = getattr(event, "chat", None) or getattr(event, "_bot_cached_chat", None)
        title = str(
            getattr(chat, "title", None)
            or previous_expiry.get("title")
            or registry.get("title")
            or ""
        ).strip()
        try:
            record = set_group_expiry(
                store, group_id, command, title=title or None
            )
            if record is None:
                return True
            # The source's expiry notice promises this command reactivates an
            # expired group. Its handler updated the deadline but omitted the
            # corresponding active-state transition; update both records in
            # the command path so renewal actually restores service.
            if was_expired:
                registry["active"] = True
                if title:
                    registry["title"] = title
                store.set_group_state(group_id, "group_registry", registry)
            activated_at = datetime.fromisoformat(str(record["activated_at"]))
            expires_at = datetime.fromisoformat(str(record["expires_at"]))
            response, spans = build_expiry_confirmation(activated_at, expires_at)
            await self._reply(
                event,
                response,
                formatting_entities=_expiry_entities(spans),
            )
        except Exception as exc:  # noqa: BLE001 - expiry changes must fail visibly
            logging.getLogger(__name__).warning(
                "group expiry update failed group=%s error=%s",
                str(group_id),
                type(exc).__name__,
            )
            await self._reply(
                event,
                "❌ تنظیم تاریخ انقضا انجام نشد؛ دوباره تلاش کنید.",
            )
        return True

    async def _native_admin_status(
        self, message: IncomingMessage
    ) -> bool | None:
        key = (message.group_id, message.user_id)
        now = time.monotonic()
        cached = self._native_admin_cache.get(key)
        if cached is not None:
            expires_at, is_admin = cached
            if expires_at > now:
                self._native_admin_cache.move_to_end(key)
                return is_admin
            self._native_admin_cache.pop(key, None)

        lock = self._native_admin_lookup_locks.get(key)
        if lock is None:
            if len(self._native_admin_lookup_locks) >= 4096:
                for old_key, old_lock in tuple(self._native_admin_lookup_locks.items()):
                    if not old_lock.locked():
                        self._native_admin_lookup_locks.pop(old_key, None)
                        break
                if len(self._native_admin_lookup_locks) >= 4096:
                    return None
            lock = asyncio.Lock()
            self._native_admin_lookup_locks[key] = lock
        self._native_admin_lookup_locks.move_to_end(key)

        async with lock:
            now = time.monotonic()
            cached = self._native_admin_cache.get(key)
            if cached is not None and cached[0] > now:
                self._native_admin_cache.move_to_end(key)
                return cached[1]

            platform = self.platform or self.management.platform
            getter = getattr(platform, "get_member", None)
            status: bool | None = None
            if callable(getter):
                try:
                    member = await getter(message.group_id, message.user_id)
                except Exception:  # noqa: BLE001 - unknown status must not cause deletion
                    member = None
                if member is not None and bool(
                    getattr(member, "admin_status_known", False)
                ):
                    status = bool(getattr(member, "is_admin", False))

            ttl = 5.0 if status is not None else 1.0
            self._native_admin_cache[key] = (time.monotonic() + ttl, status)
            self._native_admin_cache.move_to_end(key)
            while len(self._native_admin_cache) > 4096:
                self._native_admin_cache.popitem(last=False)
            return status

    async def _spam_detection_allowed(self, message: IncomingMessage) -> bool:
        """Avoid seeding incidents with protected or not-yet-verified senders."""
        if self.group_admin is None:
            return True
        access = self.group_admin.access
        if access.can_manage_explicit_group_roles(message.group_id, message.user_id):
            return False
        if self.group_admin.is_vip(message.group_id, message.user_id):
            return False
        if (
            self.legacy_admin_registry is not None
            and self.legacy_admin_registry.is_registered_admin(
                message.group_id, message.user_id, message.username
            )
        ):
            return False
        # Only a confirmed non-admin enters the destructive detector. Unknown
        # platform role state is fail-closed and cannot seed state for later use.
        return await self._native_admin_status(message) is False

    async def _filter_delete_allowed(
        self, message: IncomingMessage, _hit: FilterHit
    ) -> bool:
        """Protect trusted actors before applying a destructive word-filter hit."""
        if self.group_admin is None:
            return True
        access = self.group_admin.access
        if access.can_manage_explicit_group_roles(message.group_id, message.user_id):
            return False
        if self.group_admin.is_vip(message.group_id, message.user_id):
            return False
        if (
            self.legacy_admin_registry is not None
            and self.legacy_admin_registry.is_registered_admin(
                message.group_id, message.user_id, message.username
            )
        ):
            return False
        return await self._native_admin_status(message) is False

    @staticmethod
    def _stable_update_key(event: Any, group_id: str, user_id: str, actor_id: str | None) -> str:
        message = getattr(event, "action_message", None)
        message_id = getattr(message, "id", None)
        if message_id is not None:
            identity = f"message:{message_id}"
        else:
            update = getattr(event, "original_update", None)
            unique = (
                getattr(update, "qts", None)
                or getattr(update, "pts", None)
                or getattr(update, "version", None)
            )
            if unique is not None:
                identity = f"update:{type(update).__name__}:{unique}"
            else:
                data = "|".join(
                    (
                        type(update).__name__ if update is not None else "unknown",
                        str(getattr(update, "date", "")),
                        str(getattr(update, "channel_id", "")),
                        str(getattr(update, "chat_id", "")),
                        str(user_id),
                        str(actor_id or ""),
                        repr(getattr(update, "prev_participant", None)),
                        repr(getattr(update, "new_participant", None)),
                    )
                )
                identity = "hash:" + hashlib.sha256(data.encode("utf-8")).hexdigest()[:24]
        return f"splus:{group_id}:{identity}:{user_id}:{actor_id or 'unknown'}"

    async def _process_message(self, event: Any, *, edited: bool) -> PipelineResult:
        if not bool(getattr(event, "is_group", False)) or bool(getattr(event, "out", False)):
            return PipelineResult(processed=False)
        group_id = getattr(event, "chat_id", None)
        message_id = getattr(event, "id", None)
        if message_id is None:
            message_id = getattr(getattr(event, "message", None), "id", None)
        sender_id = getattr(event, "sender_id", None)
        text = str(getattr(event, "raw_text", None) or "")
        if group_id is None or message_id is None or sender_id is None:
            return PipelineResult(processed=False)
        sender = getattr(event, "sender", None)
        if (
            self.group_admin is not None
            and self.legacy_admin_registry is not None
            and not self.legacy_admin_registry.is_promoted(group_id, sender_id)
        ):
            try:
                if self.legacy_admin_registry.is_registered_admin(
                    group_id, sender_id, getattr(sender, "username", None)
                ):
                    display_name = " ".join(
                        part
                        for part in (
                            getattr(sender, "first_name", None),
                            getattr(sender, "last_name", None),
                        )
                        if part
                    ).strip()
                    self.group_admin.store.add_group_command_member(
                        group_id,
                        "registered_admin",
                        sender_id,
                        display_name=display_name,
                        username=getattr(sender, "username", None),
                    )
                    self.legacy_admin_registry.mark_promoted(group_id, sender_id)
            except Exception as exc:  # noqa: BLE001 - migration lookup must fail closed
                self._warn_legacy_admin_failure(group_id, exc)
        normalized_text = _normalize_command_text(text)
        expiry_command = match_expiry_command(normalized_text) if not edited else None
        if self.group_admin is not None:
            store = self.group_admin.store
            group_registry = store.get_group_state(group_id, "group_registry", {})
            active = isinstance(group_registry, dict) and bool(
                group_registry.get("active", False)
            )
            expired = group_is_expired(store, group_id)
            is_global_owner = False
            if expired or expiry_command is not None:
                is_global_owner = self.group_admin.access.owner_registry.is_global_owner(
                    sender_id
                )
            if expired and not is_global_owner:
                return PipelineResult(processed=False)
            allowed_while_inactive = (
                normalized_text in _OWNER_LIFECYCLE_COMMANDS
                or (expiry_command is not None and is_global_owner)
            )
            if not active and not allowed_while_inactive:
                return PipelineResult(processed=False)
            if expiry_command is not None and not edited:
                await self._handle_expiry_command(
                    event,
                    group_id,
                    expiry_command,
                    is_global_owner=is_global_owner,
                )
                return PipelineResult(processed=False)
        media_kind = self._media_kind(event)
        if not edited and self.group_admin is not None:
            display_name = self._display_name(sender)
            await self.group_admin.record_message(
                group_id,
                sender_id,
                display_name=display_name,
                username=getattr(sender, "username", None),
            )
            if media_kind is not None:
                self.group_admin.store.record_group_media_activity(
                    group_id, sender_id, media_kind
                )
        incoming = IncomingMessage(
            group_id=str(group_id),
            message_id=str(message_id),
            user_id=str(sender_id),
            text=str(text),
            edited=edited,
            username=getattr(sender, "username", None),
        )
        if not edited:
            if not await self._spam_detection_allowed(incoming):
                self.spam_guard.clear_user(group_id, sender_id)
                decision = None
            else:
                is_command = _is_likely_command(normalized_text)
                if (
                    self.check_ad_names_enabled
                    and self.group_admin is not None
                    and not is_command
                    and self._sender_name_has_approved_term(sender)
                ):
                    if not self._reserve_ad_name_action(str(group_id), str(sender_id)):
                        return PipelineResult(processed=True)
                    mode = self.group_admin.punishment_mode(group_id)
                    duration = self.action_duration_seconds if mode == "mute" else None
                    decision = SpamDecision(
                        kind="sender_name",
                        reason="نام نمایشی با فهرست واژه‌های تأییدشده تطبیق دارد",
                        message_ids=(str(message_id),),
                        policy="fixed_mute" if mode == "mute" else "ban",
                        action=mode,
                        duration_seconds=duration,
                        action_required=True,
                        notify=True,
                    )
                    accepted = self._schedule_automatic_action(
                        event,
                        incoming,
                        kind=decision.kind,
                        reason=decision.reason,
                        mode=mode,
                        duration_seconds=duration,
                        notify=True,
                    )
                    if not accepted:
                        self._release_ad_name_action(str(group_id), str(sender_id))
                    else:
                        self._log_spam(str(group_id), "sender_name", "action_queued")
                    return PipelineResult(processed=True, spam_decision=decision)
                if media_kind != "gif":
                    self.spam_guard.reset_gif_history(group_id, sender_id)
                management_command = _is_forward_management_command(normalized_text)
                decision = self.spam_guard.inspect(
                    group_id,
                    sender_id,
                    message_id,
                    text,
                    media_kind=media_kind,
                    forwarded=(
                        self.check_forwarded_enabled
                        and _forward_metadata(event)
                        and not management_command
                    ),
                    grouped_id=(
                        _grouped_id(event)
                        if self.check_forwarded_enabled and not management_command
                        else None
                    ),
                    is_command=is_command,
                )
            if decision is not None and await self._handle_spam_decision(
                event, incoming, decision
            ):
                return PipelineResult(
                    processed=True,
                    spam_decision=decision,
                )
        if not text.strip():
            return PipelineResult(processed=False)
        if not edited:
            if self.group_admin is not None:
                # Match source order: pre-workflow routes, pending workflows, then
                # public/game and group-word routes before remaining direct admins.
                handled = await handle_pre_workflow_public_command(
                    event,
                    self.group_admin.store,
                    group_id=group_id,
                    actor_id=sender_id,
                    text=str(text),
                    reply=self._reply,
                    photo_sender=getattr(self.platform, "send_photo", None),
                    photo_url_sender=getattr(self.platform, "send_photo_url", None),
                    global_owner_check=self.global_owner_check,
                    bot_user_id=getattr(self.management, "bot_user_id", None),
                    reply_message_loader=self.group_admin.get_message,
                    word_filter=self.pipeline.word_filter,
                )
                if handled:
                    return PipelineResult(processed=False)
                pending_workflow = await self.group_admin.pending_workflow(
                    group_id, sender_id
                )
                pending_kind = (
                    pending_workflow.get("kind")
                    if isinstance(pending_workflow, dict)
                    else None
                )
                if pending_kind in _LATE_PENDING_WORKFLOWS:
                    handled = await handle_group_level_before_late_workflow(
                        event,
                        self.group_admin.store,
                        group_id=group_id,
                        actor_id=sender_id,
                        text=str(text),
                        reply=self._reply,
                    )
                    if handled:
                        return PipelineResult(processed=False)
                    early_admin_routes = _EARLY_ADMIN_ROUTES_BY_WORKFLOW.get(
                        str(pending_kind), frozenset()
                    )
                    if normalized_text in early_admin_routes:
                        handled = await handle_group_admin_command(
                            event,
                            self.group_admin,
                            group_id=group_id,
                            actor_id=sender_id,
                            text=str(text),
                            reply=self._reply,
                            skip_pending=True,
                        )
                        if handled:
                            return PipelineResult(processed=False)
                handled = await handle_group_admin_command(
                    event,
                    self.group_admin,
                    group_id=group_id,
                    actor_id=sender_id,
                    text=str(text),
                    reply=self._reply,
                    pending_only=True,
                )
                if handled:
                    return PipelineResult(processed=False)
                handled = await handle_pre_game_public_command(
                    event,
                    self.group_admin.store,
                    group_id=group_id,
                    actor_id=sender_id,
                    text=str(text),
                    reply=self._reply,
                )
                if handled:
                    return PipelineResult(processed=False)
                handled = await handle_public_command(
                    event,
                    self.group_admin.store,
                    group_id=group_id,
                    actor_id=sender_id,
                    text=text,
                    reply=self._reply,
                    photo_sender=getattr(self.platform, "send_photo", None),
                    photo_url_sender=getattr(self.platform, "send_photo_url", None),
                    global_owner_check=self.global_owner_check,
                    status_provider=self._health_report,
                    private_message_sender=getattr(
                        self.platform, "send_private_message", None
                    ),
                    bot_user_id=getattr(self.management, "bot_user_id", None),
                    reply_message_loader=(
                        self.group_admin.get_message if self.group_admin is not None else None
                    ),
                    word_filter=self.pipeline.word_filter,
                    name_family_service=self.name_family_service,
                    skip_economy=True,
                    route_phase="post_workflow",
                )
                if handled:
                    return PipelineResult(processed=False)
            if self.word_lists is not None:
                handled = await handle_group_word_command(
                    event,
                    self.word_lists,
                    group_id=group_id,
                    actor_id=sender_id,
                    text=str(text),
                    reply_callback=self._reply,
                )
                if handled:
                    return PipelineResult(processed=False)
            if self.group_admin is not None:
                handled = await handle_group_admin_command(
                    event,
                    self.group_admin,
                    group_id=group_id,
                    actor_id=sender_id,
                    text=str(text),
                    reply=self._reply,
                    skip_pending=True,
                )
                if handled:
                    return PipelineResult(processed=False)
            handled = await handle_moderation_command(
                event,
                self.management,
                group_id=group_id,
                actor_id=sender_id,
                text=str(text),
                reply=self._reply,
            )
            if handled:
                return PipelineResult(processed=False)
        if not edited and self.broadcast_service is not None:
            try:
                broadcast_handled = await self.broadcast_service.handle(
                    event,
                    group_id=group_id,
                    actor_id=sender_id,
                    text=str(text),
                    reply=self._reply,
                )
            except Exception as exc:  # noqa: BLE001 - broadcast failures must not fall through
                self._warn_broadcast_failure(exc)
                return PipelineResult(processed=False)
            if broadcast_handled:
                return PipelineResult(processed=False)
        result = await self.pipeline.process(
            incoming,
            delete_guard=(
                self._filter_delete_allowed if self.group_admin is not None else None
            ),
        )
        if (
            not edited
            and self.group_admin is not None
            and result.violation is not None
            and result.delete_authorized is not False
            and is_long_repetitive_payload(text)
        ):
            severe_decision = SpamDecision(
                kind="long_repeat",
                reason="اسپم تکراری شدید",
                message_ids=(str(message_id),),
                policy="ban",
                action="ban",
                action_required=True,
                notify=True,
            )
            self._schedule_automatic_action(
                event,
                incoming,
                kind=severe_decision.kind,
                reason=severe_decision.reason,
                mode="ban",
                duration_seconds=None,
                notify=True,
            )
            result = replace(result, spam_decision=severe_decision)
        elif (
            self.group_admin is not None
            and result.violation is not None
            and result.delete_authorized is not False
        ):
            filter_reason = (
                "فیلتر واژهٔ گروه" if result.violation.scope == "group"
                else "فهرست سراسری کلمات ممنوعه"
            )
            warning_count = await self._record_automatic_warning(
                event,
                incoming,
                target_name=self._display_name(sender),
                reason=filter_reason,
                deletion_confirmed=result.delete_succeeded is True,
            )
            if warning_count is not None:
                result = replace(result, warning_count=warning_count)
        if not edited and self.group_admin is not None and result.delete_succeeded:
            await self.group_admin.increment_group_stat(str(group_id), "deleted")
        return result

    async def on_new_message(self, event: Any) -> PipelineResult:
        return await self._process_message(event, edited=False)

    async def on_message_edited(self, event: Any) -> PipelineResult:
        return await self._process_message(event, edited=True)

    async def on_chat_action(self, event: Any) -> bool:
        """Persist only platform-confirmed kicks, not voluntary member departures."""
        if not bool(getattr(event, "user_kicked", False)):
            return False
        if not bool(getattr(event, "is_group", False)):
            return False
        group_id = getattr(event, "chat_id", None)
        user_id = getattr(event, "user_id", None)
        if group_id is None or user_id is None:
            return False
        kicker = getattr(event, "kicked_by", None)
        if hasattr(kicker, "id"):
            actor_id = kicker.id
        elif hasattr(kicker, "user_id"):
            actor_id = kicker.user_id
        elif isinstance(kicker, (str, int)):
            actor_id = kicker
        else:
            actor_id = None
        event_key = self._stable_update_key(
            event, str(group_id), str(user_id), None if actor_id is None else str(actor_id)
        )
        affected_user = getattr(event, "user", None)
        display_name = self._display_name(affected_user)
        return await self.management.record_observed_kick(
            group_id=group_id,
            user_id=user_id,
            event_key=event_key,
            actor_id=actor_id,
            display_name=display_name,
        )

    def register(self, client: Any, *, events_module: Any | None = None) -> None:
        """Register the supported incoming-message and group-action updates."""
        if events_module is None:
            try:
                from splusthon import events as events_module
            except ImportError as exc:
                raise RuntimeError("SPlusthon events are unavailable") from exc
        client.add_event_handler(
            self.on_new_message, events_module.NewMessage(incoming=True)
        )
        client.add_event_handler(
            self.on_message_edited, events_module.MessageEdited(incoming=True)
        )
        client.add_event_handler(
            self.on_chat_action, events_module.ChatAction()
        )
