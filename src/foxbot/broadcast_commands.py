from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import uuid4

from foxbot.group_expiry import is_expired as group_is_expired
from foxbot.storage import BotStore

BROADCAST_TRIGGERS = frozenset({"اطلاع رسانی", "اطلاعرسانی", "اعلان"})
BROADCAST_CONFIRM_WORDS = frozenset({"تأیید", "تایید", "✅ تایید"})
BROADCAST_CANCEL_WORDS = frozenset({"لغو", "❌ لغو"})
_MAX_BROADCAST_GROUPS = 5000
_MAX_BROADCAST_TEXT_UTF16 = 3000
_SESSION_TTL_SECONDS = 600
_STATE_GROUP_ID = "__global__"
_STATE_PREFIX = "global_broadcast:"
_PREVIEW_PREFIX = "📣 پیش‌نمایش اطلاع‌رسانی سراسری\n\n"
_PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def _persian_digits(value: int) -> str:
    return str(int(value)).translate(_PERSIAN_DIGITS)

_ENTITY_FIELDS: dict[str, tuple[str, ...]] = {
    "MessageEntityCustomEmoji": ("document_id",),
    "MessageEntityMentionName": ("user_id",),
    "MessageEntityPre": ("language",),
    "MessageEntityTextUrl": ("url",),
}
_SIMPLE_ENTITY_TYPES = frozenset(
    {
        "MessageEntityBankCard",
        "MessageEntityBlockquote",
        "MessageEntityBold",
        "MessageEntityBotCommand",
        "MessageEntityCashtag",
        "MessageEntityCode",
        "MessageEntityEmail",
        "MessageEntityHashtag",
        "MessageEntityItalic",
        "MessageEntityMention",
        "MessageEntityPhone",
        "MessageEntitySpoiler",
        "MessageEntityStrike",
        "MessageEntityUnderline",
        "MessageEntityUnknown",
        "MessageEntityUrl",
    }
)
_SUPPORTED_ENTITY_TYPES = _SIMPLE_ENTITY_TYPES | frozenset(_ENTITY_FIELDS)


class BroadcastService:
    """Guarded, local-registry-only global group announcement workflow."""

    def __init__(
        self,
        store: BotStore,
        platform: Any,
        global_owner_check: Callable[[str | int], bool],
        *,
        max_groups: int = _MAX_BROADCAST_GROUPS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not 1 <= max_groups <= _MAX_BROADCAST_GROUPS:
            raise ValueError("broadcast group limit must be between 1 and 5000")
        self.store = store
        self.platform = platform
        self.global_owner_check = global_owner_check
        self.max_groups = max_groups
        self.clock = clock
        self._active_tokens: set[str] = set()
        self._lock = asyncio.Lock()

    @staticmethod
    def normalize_command(value: Any) -> str:
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

    @staticmethod
    def _state_key(owner_id: str | int) -> str:
        return f"{_STATE_PREFIX}{owner_id!s}"

    @staticmethod
    def _utf16_length(value: str) -> int:
        return len(value.encode("utf-16-le")) // 2

    @staticmethod
    def _message_entities(event: Any) -> Any:
        entities = getattr(event, "entities", None)
        if entities is not None:
            return entities
        message = getattr(event, "message", None)
        return getattr(message, "entities", None)

    @classmethod
    def _serialize_entities(cls, event: Any, text: str) -> list[dict[str, Any]]:
        raw_entities = cls._message_entities(event)
        if raw_entities is None:
            return []
        if not isinstance(raw_entities, (list, tuple)) or len(raw_entities) > 256:
            raise ValueError("formatting entities are not supported")
        text_length = cls._utf16_length(text)
        descriptors: list[dict[str, Any]] = []
        for entity in raw_entities:
            entity_type = type(entity).__name__
            if entity_type not in _SUPPORTED_ENTITY_TYPES:
                raise ValueError("formatting entities are not supported")
            try:
                offset = int(entity.offset)
                length = int(entity.length)
            except (AttributeError, TypeError, ValueError, OverflowError) as exc:
                raise ValueError("formatting entities are invalid") from exc
            if offset < 0 or length < 1 or offset + length > text_length:
                raise ValueError("formatting entities are outside the message")
            descriptor: dict[str, Any] = {
                "type": entity_type,
                "offset": offset,
                "length": length,
            }
            for field in _ENTITY_FIELDS.get(entity_type, ()):
                value = getattr(entity, field, None)
                if field in {"user_id", "document_id"}:
                    try:
                        value = int(value)
                    except (TypeError, ValueError, OverflowError) as exc:
                        raise ValueError("formatting entity reference is invalid") from exc
                    if value <= 0:
                        raise ValueError("formatting entity reference is invalid")
                elif field == "language":
                    value = str(value or "")
                    if len(value) > 64:
                        raise ValueError("formatting entity language is invalid")
                else:
                    value = str(value or "")
                    if not value or len(value) > 2048:
                        raise ValueError("formatting entity URL is invalid")
                descriptor[field] = value
            descriptors.append(descriptor)
        return descriptors

    @classmethod
    def _build_entities(cls, descriptors: list[dict[str, Any]]) -> list[Any] | None:
        if not descriptors:
            return None
        try:
            from splusthon.tl import types
        except ImportError as exc:
            raise RuntimeError("SPlusthon message entities are unavailable") from exc
        entities: list[Any] = []
        for descriptor in descriptors:
            entity_type = descriptor.get("type")
            if entity_type not in _SUPPORTED_ENTITY_TYPES:
                raise ValueError("stored formatting entity is unsupported")
            entity_class = getattr(types, str(entity_type), None)
            if entity_class is None:
                raise ValueError("stored formatting entity is unavailable")
            kwargs = {
                "offset": int(descriptor["offset"]),
                "length": int(descriptor["length"]),
            }
            for field in _ENTITY_FIELDS.get(str(entity_type), ()):
                kwargs[field] = descriptor[field]
            entities.append(entity_class(**kwargs))
        return entities

    def _active_group(self, group_id: str | int, registry: Any | None = None) -> bool:
        try:
            current = (
                self.store.get_group_state(group_id, "group_registry", {})
                if registry is None
                else registry
            )
            if not isinstance(current, dict) or current.get("active") is not True:
                return False
            expiry = self.store.get_group_state(group_id, "expiry", {})
            if not isinstance(expiry, dict):
                return False
            if expiry:
                expiry_text = str(expiry.get("expires_at", ""))
                if not expiry_text:
                    return False
                datetime.fromisoformat(expiry_text)
            return not group_is_expired(self.store, group_id)
        except Exception:  # noqa: BLE001 - a broken registry or expiry record fails closed
            return False

    def _scan_active_groups(self) -> tuple[tuple[str, ...], bool]:
        records, complete = self.store.list_group_states_bounded(
            "group_registry", limit=self.max_groups
        )
        if not complete or any(
            not group_id or len(group_id) > 128 for group_id, _ in records
        ):
            return (), False
        active = tuple(
            group_id
            for group_id, registry in records
            if group_id != _STATE_GROUP_ID
            and self._active_group(group_id, registry)
        )
        return active, True

    async def _reply(
        self,
        reply: Callable[..., Any],
        event: Any,
        text: str,
        *,
        formatting_entities: list[Any] | None = None,
    ) -> Any:
        if formatting_entities:
            result = reply(event, text, formatting_entities=formatting_entities)
        else:
            result = reply(event, text)
        return await result if inspect.isawaitable(result) else result

    def _clear_state(self, owner_id: str | int) -> None:
        self.store.delete_group_state(_STATE_GROUP_ID, self._state_key(owner_id))

    def _read_state(self, owner_id: str | int) -> dict[str, Any] | None:
        value = self.store.get_group_state(
            _STATE_GROUP_ID, self._state_key(owner_id), None
        )
        if value is None:
            return None
        if not isinstance(value, dict):
            raise TypeError("broadcast workflow state is malformed")
        return value

    def _state_is_expired(self, state: dict[str, Any]) -> bool:
        try:
            created_at = float(state.get("created_at"))
        except (TypeError, ValueError, OverflowError):
            return True
        return self.clock() - created_at > _SESSION_TTL_SECONDS

    async def handle(
        self,
        event: Any,
        *,
        group_id: str | int,
        actor_id: str | int,
        text: str,
        reply: Callable[..., Any],
    ) -> bool:
        async with self._lock:
            return await self._handle_locked(
                event,
                group_id=group_id,
                actor_id=actor_id,
                text=text,
                reply=reply,
            )

    async def _handle_locked(
        self,
        event: Any,
        *,
        group_id: str | int,
        actor_id: str | int,
        text: str,
        reply: Callable[..., Any],
    ) -> bool:
        try:
            is_owner = bool(self.global_owner_check(actor_id))
        except Exception:  # noqa: BLE001 - owner verification fails closed
            return False
        if not is_owner:
            return False

        normalized = self.normalize_command(text)
        try:
            state = self._read_state(actor_id)
        except Exception:  # noqa: BLE001 - corrupt workflow state cannot authorize a send
            self._clear_state(actor_id)
            await self._reply(
                reply,
                event,
                "❌ وضعیت محلی اطلاع‌رسانی معتبر نبود؛ پاک‌سازی شد و هیچ پیامی ارسال نشد.",
            )
            return True
        if state is not None and state.get("step") == "sending":
            if (
                state.get("owner_id") != str(actor_id)
                or state.get("origin_group_id") != str(group_id)
            ):
                return False
            token = str(state.get("token", ""))
            if token in self._active_tokens:
                await self._reply(
                    reply,
                    event,
                    "📣 ارسال سراسری در حال انجام است؛ ارسال دیگری تا پایان این کار آغاز نمی‌شود.",
                )
                return True
            if normalized in BROADCAST_CANCEL_WORDS:
                self._clear_state(actor_id)
                await self._reply(
                    reply,
                    event,
                    "⚠️ وضعیت ارسال قبلی نامشخص بود. وضعیت محلی پاک شد؛ هیچ ارسال تازه‌ای آغاز نشد.",
                )
                return True
            await self._reply(
                reply,
                event,
                "⚠️ وضعیت ارسال قبلی نامشخص است. برای پاک‌کردن وضعیت محلی «لغو» را بفرستید؛ ارسال خودکار تکرار نمی‌شود.",
            )
            return True

        if normalized in BROADCAST_TRIGGERS:
            if not self._active_group(group_id):
                await self._reply(
                    reply,
                    event,
                    "❌ شروع اطلاع‌رسانی از گروه فعالِ ثبت‌شده ممکن است.",
                )
                return True
            state = {
                "step": "message",
                "owner_id": str(actor_id),
                "origin_group_id": str(group_id),
                "created_at": self.clock(),
                "token": uuid4().hex,
            }
            self.store.set_group_state(
                _STATE_GROUP_ID, self._state_key(actor_id), state
            )
            try:
                await self._reply(
                    reply,
                    event,
                    "📣 متن اطلاع‌رسانی را در همین گروه بفرستید؛ برای لغو «لغو» را بفرستید.",
                )
            except Exception:
                self._clear_state(actor_id)
                raise
            return True

        if state is None:
            return False
        if (
            state.get("owner_id") != str(actor_id)
            or state.get("origin_group_id") != str(group_id)
        ):
            return False
        if self._state_is_expired(state):
            self._clear_state(actor_id)
            await self._reply(
                reply,
                event,
                "⌛ نشست اطلاع‌رسانی منقضی شد؛ برای شروع دوباره فرمان را بفرستید.",
            )
            return True

        step = state.get("step")
        if step == "message":
            if normalized in BROADCAST_CANCEL_WORDS:
                self._clear_state(actor_id)
                await self._reply(reply, event, "اطلاع‌رسانی لغو شد؛ هیچ پیامی ارسال نشد.")
                return True
            if normalized in BROADCAST_CONFIRM_WORDS:
                await self._reply(
                    reply,
                    event,
                    "ابتدا متن اطلاع‌رسانی را بفرستید؛ برای لغو «لغو» را ارسال کنید.",
                )
                return True
            message_text = str(getattr(event, "raw_text", None) or text or "")
            if not message_text.strip():
                await self._reply(reply, event, "متن اطلاع‌رسانی خالی است؛ متن را بفرستید.")
                return True
            if self._utf16_length(message_text) > _MAX_BROADCAST_TEXT_UTF16:
                await self._reply(
                    reply,
                    event,
                    "متن بیش از حد مجاز است؛ لطفاً آن را به حداکثر ۳۰۰۰ واحد UTF-16 کوتاه کنید.",
                )
                return True
            try:
                entities = self._serialize_entities(event, message_text)
            except ValueError:
                await self._reply(
                    reply,
                    event,
                    "قالب‌بندی پیام پشتیبانی یا معتبر نیست؛ متن را بدون قالب‌بندی ویژه دوباره بفرستید.",
                )
                return True
            try:
                groups, complete = self._scan_active_groups()
            except Exception:  # noqa: BLE001 - an incomplete registry scan must fail closed
                self._clear_state(actor_id)
                await self._reply(
                    reply,
                    event,
                    "❌ اسکن کامل فهرست محلی گروه‌ها ممکن نشد؛ هیچ پیامی ارسال نشد.",
                )
                return True
            if not complete:
                self._clear_state(actor_id)
                await self._reply(
                    reply,
                    event,
                    "❌ فهرست گروه‌های فعال از سقف اسکن محلی فراتر رفت؛ برای جلوگیری از ارسال ناقص هیچ پیامی ارسال نشد.",
                )
                return True
            if not groups or str(group_id) not in groups:
                self._clear_state(actor_id)
                await self._reply(
                    reply,
                    event,
                    "❌ گروه فعلی یا گروه فعالی برای ارسال در ثبت محلی موجود نیست؛ هیچ پیامی ارسال نشد.",
                )
                return True
            state.update(
                {
                    "step": "confirm",
                    "message": message_text,
                    "entities": entities,
                    "target_groups": list(groups),
                }
            )
            self.store.set_group_state(
                _STATE_GROUP_ID, self._state_key(actor_id), state
            )
            footer = (
                f"\n\n📋 گروه‌های فعالِ اسکن‌شده: {_persian_digits(len(groups))}"
                "\nبرای ارسال دقیقاً «تأیید» را بفرستید؛ برای لغو «لغو» را بفرستید."
            )
            preview = f"{_PREVIEW_PREFIX}{message_text}{footer}"
            shifted_entities = [
                {
                    **entity,
                    "offset": int(entity["offset"])
                    + self._utf16_length(_PREVIEW_PREFIX),
                }
                for entity in entities
            ]
            try:
                preview_entities = self._build_entities(shifted_entities)
                await self._reply(
                    reply,
                    event,
                    preview,
                    formatting_entities=preview_entities,
                )
            except Exception:
                self._clear_state(actor_id)
                raise
            return True

        if step != "confirm":
            self._clear_state(actor_id)
            return False
        if normalized in BROADCAST_CANCEL_WORDS:
            self._clear_state(actor_id)
            await self._reply(reply, event, "اطلاع‌رسانی لغو شد؛ هیچ پیامی ارسال نشد.")
            return True
        if normalized not in BROADCAST_CONFIRM_WORDS:
            await self._reply(
                reply,
                event,
                "برای اجرای پیش‌نمایش «تأیید» یا برای توقف «لغو» را بفرستید.",
            )
            return True

        targets = state.get("target_groups")
        message_text = state.get("message")
        entities = state.get("entities", [])
        if (
            not isinstance(targets, list)
            or not targets
            or len(targets) > self.max_groups
            or any(
                not isinstance(target, str) or not target or len(target) > 128
                for target in targets
            )
            or not isinstance(message_text, str)
            or not message_text
            or not isinstance(entities, list)
        ):
            self._clear_state(actor_id)
            await self._reply(
                reply,
                event,
                "❌ دادهٔ پیش‌نمایش معتبر نیست؛ هیچ پیامی ارسال نشد. دوباره شروع کنید.",
            )
            return True
        try:
            current_groups, complete = self._scan_active_groups()
        except Exception:  # noqa: BLE001 - confirmation requires a complete local scan
            self._clear_state(actor_id)
            await self._reply(
                reply,
                event,
                "❌ اعتبارسنجی دوبارهٔ گروه‌های محلی کامل نشد؛ هیچ پیامی ارسال نشد.",
            )
            return True
        if not complete or tuple(targets) != current_groups:
            self._clear_state(actor_id)
            await self._reply(
                reply,
                event,
                "⚠️ فهرست گروه‌های فعال از زمان پیش‌نمایش تغییر کرده است؛ هیچ پیامی ارسال نشد. برای بازبینی دوباره شروع کنید.",
            )
            return True
        if not callable(getattr(self.platform, "send_group_message", None)):
            await self._reply(
                reply,
                event,
                "❌ ارسال گروهی در این اتصال در دسترس نیست؛ هیچ پیامی ارسال نشد.",
            )
            return True
        try:
            send_entities = self._build_entities(entities)
        except (KeyError, TypeError, ValueError, RuntimeError):
            self._clear_state(actor_id)
            await self._reply(
                reply,
                event,
                "❌ قالب‌بندی ذخیره‌شده معتبر نیست؛ هیچ پیامی ارسال نشد.",
            )
            return True

        token = str(state.get("token", ""))
        state.update(
            {
                "step": "sending",
                "started_at": self.clock(),
                "confirmed_sends": 0,
                "failed_sends": 0,
                "skipped_groups": 0,
                "last_index": 0,
            }
        )
        self.store.set_group_state(_STATE_GROUP_ID, self._state_key(actor_id), state)
        try:
            await self._reply(
                reply,
                event,
                "✅ تأیید دریافت شد؛ ارسال ترتیبی گروه‌به‌گروه آغاز می‌شود.",
            )
        except Exception:
            state["step"] = "confirm"
            self.store.set_group_state(
                _STATE_GROUP_ID, self._state_key(actor_id), state
            )
            raise

        self._active_tokens.add(token)
        confirmed = 0
        failed = 0
        skipped = 0
        last_processed = 0
        sender = self.platform.send_group_message
        try:
            for index, target_group in enumerate(targets, start=1):
                last_processed = index
                if not self._active_group(target_group):
                    skipped += 1
                else:
                    try:
                        result = await sender(
                            target_group,
                            message_text,
                            formatting_entities=send_entities,
                        )
                        if result is None or result is False or (
                            isinstance(result, (int, str)) and not result
                        ):
                            failed += 1
                        else:
                            confirmed += 1
                    except Exception:  # noqa: BLE001 - continue and report aggregate outcome
                        failed += 1
                if index % 25 == 0:
                    state.update(
                        {
                            "confirmed_sends": confirmed,
                            "failed_sends": failed,
                            "skipped_groups": skipped,
                            "last_index": index,
                        }
                    )
                    self.store.set_group_state(
                        _STATE_GROUP_ID, self._state_key(actor_id), state
                    )
        except asyncio.CancelledError:
            state.update(
                {
                    "step": "sending",
                    "confirmed_sends": confirmed,
                    "failed_sends": failed,
                    "skipped_groups": skipped,
                    "last_index": last_processed,
                }
            )
            self.store.set_group_state(_STATE_GROUP_ID, self._state_key(actor_id), state)
            raise
        finally:
            self._active_tokens.discard(token)

        self._clear_state(actor_id)
        await self._reply(
            reply,
            event,
            "📣 ارسال پایان یافت.\n"
            f"✅ تأییدشده توسط پلتفرم: {_persian_digits(confirmed)}\n"
            f"⚠️ ناموفق یا بدون تأیید: {_persian_digits(failed)}\n"
            f"⏭️ گروه‌های غیرفعال‌شده هنگام ارسال: {_persian_digits(skipped)}",
        )
        return True
