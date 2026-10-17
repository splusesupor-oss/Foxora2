from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import string
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from foxbot.filters import WordFilter
from foxbot.game_commands import (
    due_game_notices_and_activity,
    refresh_timed_game_deadline_after_delivery,
)
from foxbot.group_admin import GroupAdminService
from foxbot.group_expiry import (
    build_expired_notice,
    due_expiries,
    same_expiry_generation,
)
from foxbot.group_expiry import (
    mark_notified as mark_expiry_notified,
)
from foxbot.handlers import ManagementHandlers
from foxbot.legacy_admins import LegacyAdminRegistry
from foxbot.management import MemberManagementService
from foxbot.message_actions import FairDeleteQueue
from foxbot.message_pipeline import MessagePipeline
from foxbot.name_family import NameFamilyService
from foxbot.rpc_queue import SerialRpcQueue
from foxbot.security import AccessController, OwnerRegistry
from foxbot.soroush_adapter import SPlusthonAdapter
from foxbot.soroush_events import SPlusthonEventBridge
from foxbot.storage import BotStore
from foxbot.wordlists import GroupWordService


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().casefold()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value")


def _env_positive_int(
    name: str, default: int, *, maximum: int | None = None
) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if value < 1 or (maximum is not None and value > maximum):
        bound = f" between 1 and {maximum}" if maximum is not None else ""
        raise ValueError(f"{name} must be a positive integer{bound}")
    return value


_SAFE_CONFIG_BOOL_KEYS = {
    "check_links",
    "delete_spam",
    "check_banned_words",
    "check_phone_numbers",
    "check_usernames",
    "check_forwarded",
    "send_warning",
    "enable_hot_reload_banned_words",
}


def _owner_silver_group_ids_from_env() -> tuple[str, ...]:
    raw = os.getenv("FOX_OWNER_SILVER_GROUP_IDS", "").strip()
    if not raw:
        return ()
    entries = raw.split(",")
    if len(entries) > 64:
        raise ValueError("FOX_OWNER_SILVER_GROUP_IDS may contain at most 64 IDs")
    normalized: set[str] = set()
    for entry in entries:
        value = entry.strip()
        digits = value.removeprefix("-")
        if (
            not digits
            or len(digits) > 20
            or not digits.isascii()
            or not digits.isdigit()
        ):
            raise ValueError("FOX_OWNER_SILVER_GROUP_IDS must be comma-separated numeric IDs")
        parsed = int(value)
        if parsed == 0:
            raise ValueError("FOX_OWNER_SILVER_GROUP_IDS cannot contain zero")
        normalized.add(str(parsed))
    return tuple(sorted(normalized))


def _load_safe_moderation_config() -> dict[str, Any]:
    """Load an allowlist of non-secret settings, ignoring lists and credentials."""
    raw_path = os.getenv("FOX_CONFIG_FILE", "config/config.json").strip()
    if not raw_path:
        return {}
    path = Path(raw_path)
    try:
        with path.open("rb") as stream:
            raw_payload = stream.read(1_000_001)
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise ValueError("FOX_CONFIG_FILE must be a readable UTF-8 JSON object") from exc
    if len(raw_payload) > 1_000_000:
        raise ValueError("FOX_CONFIG_FILE must not exceed 1 MB")
    try:
        payload = json.loads(raw_payload.decode("utf-8"))
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ValueError("FOX_CONFIG_FILE must be a readable UTF-8 JSON object") from exc
    if not isinstance(payload, dict):
        raise TypeError("FOX_CONFIG_FILE must contain a JSON object")

    safe: dict[str, Any] = {}
    for key in _SAFE_CONFIG_BOOL_KEYS:
        if key not in payload:
            continue
        if type(payload[key]) is not bool:
            raise ValueError(f"FOX_CONFIG_FILE field {key} must be boolean")
        safe[key] = payload[key]
    if "spam_threshold" in payload:
        value = payload["spam_threshold"]
        if type(value) is not int or not 1 <= value <= 12:
            raise ValueError("FOX_CONFIG_FILE spam_threshold must be between 1 and 12")
        safe["spam_threshold"] = value
    if "action_duration_seconds" in payload:
        value = payload["action_duration_seconds"]
        if type(value) is not int or not 1 <= value <= 40 * 24 * 60 * 60:
            raise ValueError("FOX_CONFIG_FILE action_duration_seconds must be 1 second to 40 days")
        safe["action_duration_seconds"] = value
    if "action_on_threshold" in payload:
        value = payload["action_on_threshold"]
        if not isinstance(value, str) or value.strip().casefold() not in {"ban", "mute"}:
            raise ValueError("FOX_CONFIG_FILE action_on_threshold must be ban or mute")
        safe["action_on_threshold"] = value.strip().casefold()
    return safe


def _warning_template_from_env() -> str | None:
    template = os.getenv("FOX_WARNING_MESSAGE")
    if template is None or not template.strip():
        return None
    if len(template) > 2000:
        raise ValueError("FOX_WARNING_MESSAGE must not exceed 2000 characters")
    allowed = {"username", "reason", "count", "threshold"}
    try:
        for _literal, field_name, format_spec, conversion in string.Formatter().parse(
            template
        ):
            if field_name is None:
                continue
            if field_name not in allowed or format_spec or conversion:
                raise ValueError(
                    "FOX_WARNING_MESSAGE may use only plain username, reason, count, threshold fields"
                )
    except ValueError as exc:
        if "FOX_WARNING_MESSAGE" in str(exc):
            raise
        raise ValueError("FOX_WARNING_MESSAGE has invalid format syntax") from exc
    return template


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    session_path: Path
    session_string: str | None
    api_id: int | None
    api_hash: str | None
    database_path: Path
    owner_path: Path
    global_words_path: Path
    legacy_admins_path: Path = Path("config/admins.json")
    owner_silver_group_ids: tuple[str, ...] = ()
    rpc_max_pending_total: int = 256
    rpc_max_pending_per_group: int = 32
    rpc_min_interval_seconds: float = 0.25
    mute_duration_seconds: int | None = None
    spam_threshold: int = 5
    action_on_threshold: str = "ban"
    action_duration_seconds: int = 3600
    page_size: int = 100
    check_links: bool = True
    delete_spam: bool = True
    check_banned_words: bool = True
    check_ad_names: bool = True
    check_phone_numbers: bool = True
    check_usernames: bool = True
    check_forwarded: bool = False
    send_spam_warnings: bool = True
    hot_reload_banned_words: bool = True
    warning_message_template: str | None = None
    name_family_min_observations: int = 5
    name_family_min_unique_users: int = 3
    name_family_min_unique_chats: int = 2

    @classmethod
    def from_env(cls) -> RuntimeSettings:
        data_dir = Path(os.getenv("FOX_BOT_DATA_DIR", "./data"))
        session_path = Path(os.getenv("SOROUSH_SESSION_PATH", str(data_dir / "soroush")))
        session_string = os.getenv("SOROUSH_SESSION_STRING") or None
        api_id_text = os.getenv("SOROUSH_API_ID", "").strip()
        api_hash = os.getenv("SOROUSH_API_HASH", "").strip() or None
        if bool(api_id_text) != bool(api_hash):
            raise ValueError("Set both SOROUSH_API_ID and SOROUSH_API_HASH, or neither")
        try:
            api_id = int(api_id_text) if api_id_text else None
        except ValueError as exc:
            raise ValueError("SOROUSH_API_ID must be a positive integer") from exc
        if api_id is not None and api_id <= 0:
            raise ValueError("SOROUSH_API_ID must be a positive integer")
        moderation_config = _load_safe_moderation_config()
        duration_text = os.getenv("FOX_MUTE_DURATION_SECONDS", "").strip()
        duration = int(duration_text) if duration_text else None
        if duration is not None and not 0 < duration <= 40 * 24 * 60 * 60:
            raise ValueError("FOX_MUTE_DURATION_SECONDS must be within 1 second and 40 days")
        spam_threshold = _env_positive_int(
            "FOX_SPAM_THRESHOLD", moderation_config.get("spam_threshold", 5)
        )
        if spam_threshold > 12:
            raise ValueError("FOX_SPAM_THRESHOLD must be between 1 and 12")
        action_on_threshold = os.getenv(
            "FOX_ACTION_ON_THRESHOLD", moderation_config.get("action_on_threshold", "ban")
        ).strip().casefold()
        if action_on_threshold not in {"ban", "mute"}:
            raise ValueError("FOX_ACTION_ON_THRESHOLD must be ban or mute")
        action_duration = _env_positive_int(
            "FOX_ACTION_DURATION_SECONDS", moderation_config.get("action_duration_seconds", 3600)
        )
        if action_duration > 40 * 24 * 60 * 60:
            raise ValueError("FOX_ACTION_DURATION_SECONDS must not exceed 40 days")
        rpc_max_pending_total = _env_positive_int(
            "RPC_MAX_PENDING_TOTAL", 256, maximum=4096
        )
        rpc_max_pending_per_group = _env_positive_int(
            "RPC_MAX_PENDING_PER_GROUP", 32, maximum=256
        )
        try:
            rpc_min_interval = float(os.getenv("RPC_MIN_INTERVAL_SECONDS", "0.25"))
        except ValueError as exc:
            raise ValueError("RPC_MIN_INTERVAL_SECONDS must be a finite number from 0 to 60") from exc
        if not math.isfinite(rpc_min_interval) or not 0 <= rpc_min_interval <= 60:
            raise ValueError("RPC_MIN_INTERVAL_SECONDS must be a finite number from 0 to 60")
        page_size = _env_positive_int("FOX_MEMBER_PAGE_SIZE", 100, maximum=1000)
        return cls(
            session_path=session_path,
            session_string=session_string,
            api_id=api_id,
            api_hash=api_hash,
            database_path=Path(os.getenv("FOX_BOT_DB", str(data_dir / "foxbot.sqlite3"))),
            owner_path=Path(os.getenv("FOX_OWNER_FILE", "config/owner.json")),
            global_words_path=Path(
                os.getenv("FOX_GLOBAL_WORDS_FILE", "config/banned_words.txt")
            ),
            legacy_admins_path=Path(
                os.getenv("FOX_LEGACY_ADMINS_FILE", "config/admins.json")
            ),
            owner_silver_group_ids=_owner_silver_group_ids_from_env(),
            rpc_max_pending_total=rpc_max_pending_total,
            rpc_max_pending_per_group=rpc_max_pending_per_group,
            rpc_min_interval_seconds=rpc_min_interval,
            mute_duration_seconds=duration,
            spam_threshold=spam_threshold,
            action_on_threshold=action_on_threshold,
            action_duration_seconds=action_duration,
            page_size=page_size,
            check_links=_env_bool(
                "FOX_CHECK_LINKS", moderation_config.get("check_links", True)
            ),
            delete_spam=_env_bool(
                "FOX_DELETE_SPAM", moderation_config.get("delete_spam", True)
            ),
            check_banned_words=_env_bool(
                "FOX_CHECK_BANNED_WORDS", moderation_config.get("check_banned_words", True)
            ),
            # Sender-name checks reuse only the user-approved global matcher;
            # the source-only ad-term corpus is intentionally not imported.
            check_ad_names=_env_bool("FOX_CHECK_AD_NAMES", True),
            check_phone_numbers=_env_bool(
                "FOX_CHECK_PHONE_NUMBERS", moderation_config.get("check_phone_numbers", True)
            ),
            check_usernames=_env_bool(
                "FOX_CHECK_USERNAMES", moderation_config.get("check_usernames", True)
            ),
            check_forwarded=_env_bool(
                "FOX_CHECK_FORWARDED", moderation_config.get("check_forwarded", False)
            ),
            send_spam_warnings=_env_bool(
                "FOX_SEND_SPAM_WARNINGS", moderation_config.get("send_warning", True)
            ),
            hot_reload_banned_words=_env_bool(
                "FOX_HOT_RELOAD_BANNED_WORDS",
                moderation_config.get("enable_hot_reload_banned_words", True),
            ),
            warning_message_template=_warning_template_from_env(),
            name_family_min_observations=_env_positive_int(
                "FOX_NAME_FAMILY_MIN_OBSERVATIONS", 5
            ),
            name_family_min_unique_users=_env_positive_int(
                "FOX_NAME_FAMILY_MIN_UNIQUE_USERS", 3
            ),
            name_family_min_unique_chats=_env_positive_int(
                "FOX_NAME_FAMILY_MIN_UNIQUE_CHATS", 2
            ),
        )


class GlobalWordListReloader:
    """Reload only the configured, user-approved word file when it changes."""

    def __init__(
        self,
        path: Path,
        word_filter: WordFilter,
        *,
        on_reload: Callable[[], None] | None = None,
    ) -> None:
        self.path = Path(path)
        self.word_filter = word_filter
        self.on_reload = on_reload
        self._last_warning_at = 0.0
        self._signature = self._file_signature()

    def _file_signature(self) -> tuple[int, int] | None:
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            return None
        except OSError as exc:
            self._warn_failure(exc)
            return None
        return stat.st_mtime_ns, stat.st_size

    def _warn_failure(self, error: Exception) -> None:
        now = time.monotonic()
        if now - self._last_warning_at < 60.0:
            return
        self._last_warning_at = now
        logging.getLogger(__name__).warning(
            "approved word list reload failed error=%s", type(error).__name__
        )

    def reload_if_changed(self) -> bool:
        signature = self._file_signature()
        if signature is None:
            self._signature = None
            return False
        if signature == self._signature:
            return False
        try:
            terms = WordFilter.read_file(self.path)
        except (OSError, UnicodeError, ValueError) as exc:
            self._warn_failure(exc)
            return False
        if self._file_signature() != signature:
            return False
        try:
            self.word_filter.replace_global_terms(terms)
        except (TypeError, ValueError) as exc:
            self._warn_failure(exc)
            return False
        self._signature = signature
        if self.on_reload is not None:
            try:
                self.on_reload()
            except Exception as exc:  # noqa: BLE001 - retain working matcher on callback failure
                self._warn_failure(exc)
        return True


@dataclass(slots=True)
class BotRuntime:
    store: BotStore
    rpc_queue: SerialRpcQueue
    platform: SPlusthonAdapter
    access: AccessController
    management: MemberManagementService
    group_admin: GroupAdminService
    word_filter: WordFilter
    wordlist_reloader: GlobalWordListReloader | None
    word_lists: GroupWordService
    message_pipeline: MessagePipeline
    delete_queue: FairDeleteQueue
    event_bridge: SPlusthonEventBridge
    handlers: ManagementHandlers


def create_soroush_client(settings: RuntimeSettings) -> Any:
    """Build a SPlusthon client without starting/login prompts."""
    try:
        from splusthon import SoroushClient
        from splusthon.sessions import StringSession
    except ImportError as exc:
        raise RuntimeError("Install the declared SPlusthon dependency first") from exc
    if not settings.session_string:
        settings.session_path.parent.mkdir(parents=True, exist_ok=True)
    session: Any = (
        StringSession(settings.session_string)
        if settings.session_string
        else str(settings.session_path)
    )
    if settings.api_id is None and settings.api_hash is None:
        # SPlusthon 1.1.x supplies its own library defaults; no invented credentials.
        return SoroushClient(session)
    return SoroushClient(session, settings.api_id, settings.api_hash)


def build_runtime(client: Any, settings: RuntimeSettings) -> BotRuntime:
    owner_registry = OwnerRegistry(settings.owner_path)
    store = BotStore(
        settings.database_path,
        global_owner_check=owner_registry.is_global_owner,
        owner_silver_group_ids=settings.owner_silver_group_ids,
    )
    queue = SerialRpcQueue(
        max_pending_total=settings.rpc_max_pending_total,
        max_pending_per_group=settings.rpc_max_pending_per_group,
        min_interval_seconds=settings.rpc_min_interval_seconds,
    )
    platform = SPlusthonAdapter(client, queue)
    access = AccessController(
        owner_registry, store, platform.is_native_admin
    )
    duration_provider = (
        (lambda _group_id: settings.mute_duration_seconds)
        if settings.mute_duration_seconds is not None
        else None
    )
    management = MemberManagementService(
        platform,
        store,
        access,
        mute_duration_provider=duration_provider,
        page_size=settings.page_size,
    )
    global_terms = WordFilter.read_file(settings.global_words_path)
    word_filter = WordFilter(global_terms)
    name_family_service = NameFamilyService(
        word_filter,
        min_observations=settings.name_family_min_observations,
        min_unique_users=settings.name_family_min_unique_users,
        min_unique_chats=settings.name_family_min_unique_chats,
    )
    wordlist_reloader = (
        GlobalWordListReloader(
            settings.global_words_path,
            word_filter,
            on_reload=name_family_service.refresh_known_answers,
        )
        if settings.hot_reload_banned_words
        else None
    )
    word_lists = GroupWordService(store, access, word_filter)
    word_lists.reload_all()
    group_admin = GroupAdminService(
        store,
        access,
        management,
        default_warning_threshold=settings.spam_threshold,
        default_punishment_mode=settings.action_on_threshold,
    )
    legacy_admin_registry = LegacyAdminRegistry(settings.legacy_admins_path)
    delete_queue = FairDeleteQueue(platform.delete_message)
    message_pipeline = MessagePipeline(
        word_filter,
        delete_queue.delete_one,
        check_banned_words=settings.check_banned_words,
    )
    event_bridge = SPlusthonEventBridge(
        message_pipeline,
        management,
        word_lists,
        rpc_queue=queue,
        group_admin=group_admin,
        platform=platform,
        global_owner_check=access.owner_registry.is_global_owner,
        legacy_admin_registry=legacy_admin_registry,
        delete_queue=delete_queue,
        site_link_policy_enabled=settings.check_links,
        delete_spam_enabled=settings.delete_spam,
        check_ad_names_enabled=settings.check_ad_names,
        check_phone_numbers_enabled=settings.check_phone_numbers,
        check_usernames_enabled=settings.check_usernames,
        check_forwarded_enabled=settings.check_forwarded,
        send_spam_warnings_enabled=settings.send_spam_warnings,
        action_duration_seconds=settings.action_duration_seconds,
        warning_message_template=settings.warning_message_template,
        name_family_service=name_family_service,
    )
    handlers = ManagementHandlers(management, word_lists)
    return BotRuntime(
        store=store,
        rpc_queue=queue,
        platform=platform,
        access=access,
        management=management,
        group_admin=group_admin,
        word_filter=word_filter,
        wordlist_reloader=wordlist_reloader,
        word_lists=word_lists,
        message_pipeline=message_pipeline,
        delete_queue=delete_queue,
        event_bridge=event_bridge,
        handlers=handlers,
    )


async def _send_scheduler_notice(
    runtime: BotRuntime,
    client: Any,
    group_id: str,
    text: str,
    *,
    formatting_entities: list[Any] | None = None,
    before_send: Callable[[], bool] | None = None,
) -> bool:
    async def send() -> Any:
        if before_send is not None and not before_send():
            return False
        kwargs = (
            {"formatting_entities": formatting_entities}
            if formatting_entities is not None
            else {}
        )
        result = client.send_message(group_id, text, **kwargs)
        return await result if hasattr(result, "__await__") else result

    try:
        result = await runtime.rpc_queue.submit(
            group_id,
            "scheduler.notice",
            send,
            priority=1,
            retry_safe=False,
            max_attempts=1,
        )
        return result is not None and result is not False
    except Exception as exc:  # noqa: BLE001 - scheduler reports only bounded error classes
        logging.getLogger(__name__).warning(
            "scheduled notice failed group=%s error=%s", group_id, type(exc).__name__
        )
        return False


def _expiry_formatting_entities(spans: list[tuple[str, int, int]]) -> list[Any] | None:
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


async def _process_scheduled_actions(runtime: BotRuntime, client: Any) -> float | None:
    reloader = getattr(runtime, "wordlist_reloader", None)
    if reloader is not None and reloader.reload_if_changed():
        logging.getLogger(__name__).info(
            "approved global word list reloaded entries=%d",
            len(runtime.word_filter.global_terms),
        )
    now = datetime.now(UTC)
    for group_id, expiry in due_expiries(runtime.store, now=now):
        registry = runtime.store.get_group_state(group_id, "group_registry", {})
        registry = dict(registry) if isinstance(registry, dict) else {}
        registry["active"] = False
        if expiry.get("title"):
            registry["title"] = str(expiry["title"])
        runtime.store.set_group_state(group_id, "group_registry", registry)

        expiry.setdefault("deactivated_at", now.isoformat(timespec="seconds"))
        runtime.store.set_group_state(group_id, "expiry", expiry)
        text, spans = build_expired_notice()
        expected_expiry = dict(expiry)

        def is_current(
            expected_group_id: str = group_id,
            expected_record: dict[str, Any] = expected_expiry,
        ) -> bool:
            return same_expiry_generation(
                runtime.store.get_group_state(expected_group_id, "expiry"),
                expected_record,
            )
        sent = await _send_scheduler_notice(
            runtime,
            client,
            group_id,
            text,
            formatting_entities=_expiry_formatting_entities(spans),
            before_send=is_current,
        )
        current_expiry = runtime.store.get_group_state(group_id, "expiry")
        if not same_expiry_generation(current_expiry, expiry):
            continue
        if sent:
            mark_expiry_notified(runtime.store, group_id, current_expiry)
        else:
            current_expiry = dict(current_expiry)
            try:
                attempts = max(0, int(current_expiry.get("notice_attempts", 0))) + 1
            except (TypeError, ValueError):
                attempts = 1
            current_expiry["notice_attempts"] = attempts
            delay = min(900, 30 * (2 ** min(attempts - 1, 5)))
            current_expiry["next_notice_at"] = (
                now + timedelta(seconds=delay)
            ).isoformat(timespec="seconds")
            runtime.store.set_group_state(group_id, "expiry", current_expiry)

    for group_id, state in runtime.store.list_group_states("auto_cleanup"):
        if not isinstance(state, dict):
            continue
        if state.get("status") == "awaiting_confirmation":
            job_id = state.get("job_id")
            plan = runtime.store.get_group_state(group_id, f"delete_plan:{job_id}") if job_id else None
            try:
                expires_at = datetime.fromisoformat(str(plan["expires_at"])) if isinstance(plan, dict) else now
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=UTC)
            except (KeyError, TypeError, ValueError):
                expires_at = now
            if expires_at.astimezone(UTC) <= now:
                state["status"] = "expired"
                runtime.store.set_group_state(group_id, "auto_cleanup", state)
                await _send_scheduler_notice(
                    runtime, client, group_id,
                    "⌛ مهلت تأیید پاکسازی خودکار پایان یافت؛ هیچ پیام دیگری حذف نشد.",
                )
            continue
        if state.get("status") != "scheduled":
            continue
        try:
            due_at = datetime.fromisoformat(str(state["scheduled_at"]))
            if due_at.tzinfo is None:
                due_at = due_at.replace(tzinfo=UTC)
        except (KeyError, TypeError, ValueError):
            state["status"] = "failed"
            runtime.store.set_group_state(group_id, "auto_cleanup", state)
            continue
        if due_at.astimezone(UTC) > now:
            continue
        actor_id = state.get("actor_id")
        count = state.get("count")
        try:
            if actor_id is None or not isinstance(count, int):
                raise ValueError("scheduled cleanup configuration is invalid")
            plan = await runtime.group_admin.prepare_delete_recent(group_id, actor_id, count)
            if not plan.get("scan_complete"):
                state["status"] = "failed"
                state["error_code"] = str(plan.get("error_code") or "incomplete_scan")
                runtime.store.set_group_state(group_id, "auto_cleanup", state)
                await _send_scheduler_notice(
                    runtime,
                    client,
                    group_id,
                    "❌ پاکسازی خودکار اجرا نشد؛ اسکن پیام‌ها کامل نبود و هیچ پیامی حذف نشد.",
                )
                continue
            state["status"] = "awaiting_confirmation"
            state["job_id"] = plan["job_id"]
            state["staged_count"] = plan["count"]
            state["staged_at"] = now.isoformat(timespec="seconds")
            runtime.store.set_group_state(group_id, "auto_cleanup", state)
            await _send_scheduler_notice(
                runtime,
                client,
                group_id,
                "🧹 پاکسازی خودکار اسکن شد؛ هنوز هیچ پیامی حذف نشده است.\n"
                f"تعداد پیام‌های آماده: {plan['count']}\n"
                f"تأیید: تایید پاک {plan['job_id']}\n"
                f"لغو: لغو پاک {plan['job_id']}",
            )
        except Exception as exc:  # noqa: BLE001 - errors make this one-shot schedule fail closed
            state["status"] = "failed"
            state["error_code"] = type(exc).__name__
            runtime.store.set_group_state(group_id, "auto_cleanup", state)
            await _send_scheduler_notice(
                runtime,
                client,
                group_id,
                "❌ پاکسازی خودکار آماده نشد؛ هیچ پیامی حذف نشده است.",
            )
            logging.getLogger(__name__).warning(
                "scheduled cleanup staging failed group=%s error=%s",
                group_id,
                type(exc).__name__,
            )

    for group_id, state in runtime.store.list_group_states("timed_unlock"):
        if not isinstance(state, dict) or state.get("status", "scheduled") != "scheduled":
            continue
        try:
            unlock_at = datetime.fromisoformat(str(state["unlock_at"]))
            if unlock_at.tzinfo is None:
                unlock_at = unlock_at.replace(tzinfo=UTC)
        except (KeyError, TypeError, ValueError):
            runtime.store.delete_group_state(group_id, "timed_unlock")
            continue
        if unlock_at.astimezone(UTC) > now:
            continue
        actor_id = state.get("actor_id")
        try:
            if actor_id is None:
                raise ValueError("timed unlock actor is missing")
            await runtime.group_admin.set_group_locked(group_id, actor_id, False)
            runtime.store.delete_group_state(group_id, "timed_unlock")
            await _send_scheduler_notice(runtime, client, group_id, "🔓 قفل ساعتی پایان یافت و گروه باز شد")
        except Exception as exc:  # noqa: BLE001 - do not silently claim an unlock
            state["status"] = "failed"
            state["error_code"] = type(exc).__name__
            runtime.store.set_group_state(group_id, "timed_unlock", state)
            await _send_scheduler_notice(runtime, client, group_id, "❌ باز کردن خودکار گروه انجام نشد؛ دسترسی و وضعیت پلتفرم را بررسی کنید.")
            logging.getLogger(__name__).warning(
                "timed unlock failed group=%s error=%s", group_id, type(exc).__name__
            )

    for group_id, state_key, reminder in runtime.store.list_group_states_prefix("reminder:"):
        if not isinstance(reminder, dict):
            runtime.store.delete_group_state(group_id, state_key)
            continue
        try:
            due_at = datetime.fromisoformat(str(reminder["due_at"]))
            if due_at.tzinfo is None:
                due_at = due_at.replace(tzinfo=UTC)
            retry_at = datetime.fromisoformat(str(reminder.get("next_attempt_at", "1970-01-01T00:00:00+00:00")))
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=UTC)
        except (KeyError, TypeError, ValueError):
            runtime.store.delete_group_state(group_id, state_key)
            continue
        if due_at.astimezone(UTC) > now or retry_at.astimezone(UTC) > now:
            continue
        reminder_name = reminder.get("name") or "کاربر"
        reminder_text = reminder.get("text") or ""
        notice = f"⏰ یادآوری برای {reminder_name}:\n\n📝 {reminder_text}"
        if await _send_scheduler_notice(runtime, client, group_id, notice):
            runtime.store.delete_group_state(group_id, state_key)
            continue
        attempts = int(reminder.get("attempts", 0)) + 1
        if attempts >= 3:
            runtime.store.delete_group_state(group_id, state_key)
            logging.getLogger(__name__).warning(
                "reminder delivery abandoned group=%s", group_id
            )
            continue
        reminder["attempts"] = attempts
        reminder["next_attempt_at"] = (now + timedelta(seconds=30)).isoformat(timespec="seconds")
        runtime.store.set_group_state(group_id, state_key, reminder)

    game_notices, active_game_deadline, next_game_deadline = due_game_notices_and_activity(
        runtime.store, now.timestamp()
    )
    for group_id, state_key, notice in game_notices:
        current_notice = notice
        game_notice_failed = False
        while True:
            delivery_state = runtime.store.get_group_state(group_id, state_key)
            if (
                not isinstance(delivery_state, dict)
                or str(delivery_state.get("notice_text") or "") != current_notice
            ):
                break
            delivery_kind = str(delivery_state.get("kind") or "")
            delivery_data = delivery_state.get("data", {})
            delivery_data = delivery_data if isinstance(delivery_data, dict) else {}
            delivery_phase = str(
                delivery_state.get("phase") or delivery_data.get("phase") or ""
            )
            delivery_stage_key = "round" if delivery_kind == "survival" else "index"
            try:
                delivery_stage = int(delivery_data.get(delivery_stage_key, 0) or 0)
            except (TypeError, ValueError):
                delivery_stage = -1
            delivery_token = str(delivery_state.get("token") or "")
            delivery_prompt = str(delivery_state.get("prompt") or "")
            if not await _send_scheduler_notice(runtime, client, group_id, current_notice):
                game_notice_failed = True
                break
            state = runtime.store.get_group_state(group_id, state_key)
            if not isinstance(state, dict):
                break
            # A game transition may need several platform sends (for example,
            # elimination, then the next question). Keep each send distinct,
            # sequential, and retryable if any one fails.
            if str(state.get("notice_text") or "") != current_notice:
                break
            queue = state.get("notice_queue")
            queue = queue if isinstance(queue, list) else []
            if queue:
                next_notice = str(queue[0] or "")
                state["notice_queue"] = queue[1:]
                if next_notice:
                    state["notice_text"] = next_notice
                    runtime.store.set_group_state(group_id, state_key, state)
                    current_notice = next_notice
                    continue
            state.pop("notice_queue", None)
            if state.get("status") == "timed_out":
                runtime.store.delete_group_state(group_id, state_key)
            elif state.get("notice_pending"):
                state["notice_pending"] = False
                state.pop("notice_text", None)
                runtime.store.set_group_state(group_id, state_key, state)
                if delivery_kind in {"survival", "battle"} and delivery_stage >= 0:
                    refresh_timed_game_deadline_after_delivery(
                        runtime.store, group_id, state_key,
                        token=delivery_token,
                        phase=delivery_phase,
                        stage=delivery_stage,
                        expected_prompt=delivery_prompt,
                        now=time.time(),
                    )
                    refreshed_state = runtime.store.get_group_state(
                        group_id, state_key
                    )
                    if isinstance(refreshed_state, dict):
                        state = refreshed_state
                try:
                    expires_at = float(state.get("expires_at", 0))
                except (TypeError, ValueError):
                    expires_at = 0
                if state.get("status", "pending") == "pending" and expires_at > time.time():
                    active_game_deadline = True
                    if next_game_deadline is None or expires_at < next_game_deadline:
                        next_game_deadline = expires_at
            break
        if game_notice_failed:
            retry_at = time.time() + _ACTIVE_GAME_SCHEDULER_INTERVAL_SECONDS
            active_game_deadline = True
            if next_game_deadline is None or retry_at < next_game_deadline:
                next_game_deadline = retry_at
    if next_game_deadline is not None:
        return min(
            _ACTIVE_GAME_SCHEDULER_INTERVAL_SECONDS,
            max(0.05, next_game_deadline - time.time()),
        )
    if active_game_deadline:
        return float(_ACTIVE_GAME_SCHEDULER_INTERVAL_SECONDS)
    return None


_SCHEDULER_INTERVAL_SECONDS = 15
_ACTIVE_GAME_SCHEDULER_INTERVAL_SECONDS = 5


async def _scheduler_loop(runtime: BotRuntime, client: Any) -> None:
    logger = logging.getLogger(__name__)
    while True:
        next_game_check = None
        try:
            next_game_check = await _process_scheduled_actions(runtime, client)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - continue scheduler after isolated state/API errors
            logger.warning("scheduler iteration failed error=%s", type(exc).__name__)
        interval = (
            _SCHEDULER_INTERVAL_SECONDS
            if next_game_check is None
            else min(
                _ACTIVE_GAME_SCHEDULER_INTERVAL_SECONDS,
                max(0.05, float(next_game_check)),
            )
        )
        await asyncio.sleep(interval)


async def run_bot(
    settings: RuntimeSettings | None = None,
    *,
    client: Any | None = None,
) -> None:
    """Run moderation, group-filter and routed group-admin services.

    No login credential is requested by this function; an uninitialized session
    fails closed. Platform-dependent admin commands still fail closed when the
    installed SPlusthon build cannot verify their result.
    """
    logging.basicConfig(level=logging.WARNING)
    settings = settings or RuntimeSettings.from_env()
    client = client or create_soroush_client(settings)
    runtime = build_runtime(client, settings)
    connected = False
    scheduler_task: asyncio.Task[None] | None = None
    try:
        await client.connect()
        connected = True
        authorized = await runtime.rpc_queue.submit(
            "startup", "auth.check", client.is_user_authorized, retry_safe=True, max_attempts=2
        )
        if not authorized:
            raise RuntimeError(
                "SPlusthon session is not authorized; initialize the local session before starting the bot"
            )
        me = await runtime.rpc_queue.submit(
            "startup", "auth.get_me", client.get_me, retry_safe=True, max_attempts=2
        )
        runtime.management.bot_user_id = str(me.id)
        runtime.event_bridge.register(client)
        scheduler_task = asyncio.create_task(
            _scheduler_loop(runtime, client), name="foxbot:scheduler"
        )
        await client.run_until_disconnected()
    finally:
        if scheduler_task is not None:
            scheduler_task.cancel()
            await asyncio.gather(scheduler_task, return_exceptions=True)
        if connected:
            try:
                await runtime.event_bridge.close(drain=True)
            finally:
                try:
                    await runtime.rpc_queue.close(drain=True)
                finally:
                    await client.disconnect()
        else:
            await runtime.event_bridge.close(drain=False)
            await runtime.rpc_queue.close(drain=False)
        runtime.store.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="fox-soroush-bot",
        description="Run the Fox moderation services with an authorized SPlusthon session.",
    )
    parser.parse_args()
    asyncio.run(run_bot())
