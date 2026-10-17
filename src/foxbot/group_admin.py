from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

from foxbot.domain import ActorKind
from foxbot.management import MemberManagementService
from foxbot.security import AccessController
from foxbot.storage import (
    MAX_GROUP_HISTORY_RECORDS_PER_USER,
    MAX_GROUP_HISTORY_USERS,
    BotStore,
)

MAX_HISTORY_USERS = MAX_GROUP_HISTORY_USERS
MAX_HISTORY_RECORDS_PER_USER = MAX_GROUP_HISTORY_RECORDS_PER_USER


class GroupAdminService:
    """Persistent group-admin state and authorization for legacy admin commands."""

    def __init__(
        self,
        store: BotStore,
        access: AccessController,
        management: MemberManagementService,
        *,
        default_warning_threshold: int = 5,
        default_punishment_mode: str = "ban",
    ) -> None:
        if not 1 <= int(default_warning_threshold) <= 12:
            raise ValueError("default warning threshold must be between 1 and 12")
        if default_punishment_mode not in {"ban", "mute"}:
            raise ValueError("default punishment mode must be ban or mute")
        self.store = store
        self.access = access
        self.management = management
        self.default_warning_threshold = int(default_warning_threshold)
        self.default_punishment_mode = default_punishment_mode

    async def role(self, group_id: str | int, actor_id: str | int) -> ActorKind | None:
        return await self.access.role_for(group_id, actor_id)

    async def require_admin(self, group_id: str | int, actor_id: str | int) -> ActorKind:
        role = self.access.explicit_role_for(group_id, actor_id)
        if role is None:
            raise PermissionError(
                "only the global owner, this group's owner, or a registered group admin may use this command"
            )
        return role

    async def require_owner(self, group_id: str | int, actor_id: str | int) -> ActorKind:
        role = await self.role(group_id, actor_id)
        if role not in {ActorKind.GLOBAL_OWNER, ActorKind.GROUP_OWNER}:
            raise PermissionError("only the global owner or this group's owner may manage admins")
        return role

    async def require_zero_permission(
        self, group_id: str | int, actor_id: str | int
    ) -> ActorKind:
        role = await self.role(group_id, actor_id)
        if role not in {ActorKind.GLOBAL_OWNER, ActorKind.GROUP_OWNER}:
            raise PermissionError("only the global owner or this group's owner may reset violations")
        return role

    async def add_admin(
        self,
        group_id: str | int,
        actor_id: str | int,
        target_id: str | int,
        *,
        display_name: str = "",
        username: str | None = None,
    ) -> bool:
        await self.require_owner(group_id, actor_id)
        if str(target_id) == str(actor_id):
            return False
        return self.store.add_group_command_member(
            group_id,
            "registered_admin",
            target_id,
            display_name=display_name,
            username=username,
        )

    async def remove_admin(
        self, group_id: str | int, actor_id: str | int, target_id: str | int
    ) -> bool:
        await self.require_owner(group_id, actor_id)
        if str(target_id) == self.store.get_group_owner(group_id):
            return False
        if self.access.owner_registry.is_global_owner(target_id):
            return False
        return self.store.remove_group_command_member(
            group_id, "registered_admin", target_id
        )

    async def registered_admins(self, group_id: str | int) -> list[dict[str, Any]]:
        return self.store.list_group_command_members(
            group_id, "registered_admin", limit=100
        )

    async def list_admins(self, group_id: str | int, actor_id: str | int) -> tuple[list[dict[str, Any]], list[Any]]:
        await self.require_admin(group_id, actor_id)
        registered = await self.registered_admins(group_id)
        members = await self.management.list_members(group_id, actor_id)
        native = [member for member in members if member.is_admin]
        if any(not member.admin_status_known for member in members):
            from foxbot.platform import IncompleteMemberScan
            raise IncompleteMemberScan("admin status is unknown for one or more members")
        return registered, native

    async def add_vip(
        self,
        group_id: str | int,
        actor_id: str | int,
        target_id: str | int,
        *,
        display_name: str = "",
        username: str | None = None,
    ) -> bool:
        await self.require_admin(group_id, actor_id)
        target = await self.management.platform.get_member(str(group_id), str(target_id))
        if target is None:
            return False
        if target.group_id != str(group_id) or target.user_id != str(target_id):
            from foxbot.platform import IncompleteMemberScan
            raise IncompleteMemberScan("VIP target lookup returned a different member")
        if not target.membership_status_known or not target.admin_status_known:
            from foxbot.platform import IncompleteMemberScan
            raise IncompleteMemberScan("VIP target membership/admin status is unknown")
        if not target.is_member or target.is_admin:
            return False
        return self.store.add_group_command_member(
            group_id, "vip", target_id,
            display_name=display_name, username=username,
        )

    async def remove_vip(
        self, group_id: str | int, actor_id: str | int, target_id: str | int
    ) -> bool:
        await self.require_admin(group_id, actor_id)
        return self.store.remove_group_command_member(group_id, "vip", target_id)

    async def list_vips(
        self, group_id: str | int, actor_id: str | int
    ) -> list[dict[str, Any]]:
        await self.require_admin(group_id, actor_id)
        return self.store.list_group_command_members(group_id, "vip", limit=20)

    def is_vip(self, group_id: str | int, user_id: str | int) -> bool:
        return self.store.has_group_command_member(group_id, "vip", user_id)

    async def set_law(self, group_id: str | int, actor_id: str | int, law: str) -> None:
        await self.require_admin(group_id, actor_id)
        value = law.strip()
        if not value or len(value) > 4000:
            raise ValueError("law text must contain 1 to 4000 characters")
        self.store.set_group_state(group_id, "law_text", value)

    async def remove_law(self, group_id: str | int, actor_id: str | int) -> bool:
        await self.require_admin(group_id, actor_id)
        return self.store.delete_group_state(group_id, "law_text")

    def get_law(self, group_id: str | int) -> str | None:
        value = self.store.get_group_state(group_id, "law_text")
        return value if isinstance(value, str) else None

    async def delete_memory(
        self, group_id: str | int, actor_id: str | int, target_id: str | int
    ) -> bool:
        await self.require_admin(group_id, actor_id)
        return self.store.delete_group_state(group_id, f"memory:{target_id}")

    async def set_memory(
        self, group_id: str | int, actor_id: str | int, name: str
    ) -> None:
        await self.require_admin(group_id, actor_id)
        self.store.set_group_state(group_id, f"memory:{actor_id}", name.strip())

    async def add_name_filter(
        self, group_id: str | int, actor_id: str | int, term: str
    ) -> bool:
        await self.require_admin(group_id, actor_id)
        value = self._clean_name_term(term)
        terms = self.list_name_filters(group_id)
        if value in terms:
            return False
        terms.append(value)
        self.store.set_group_state(group_id, "name_filters", terms)
        return True

    async def remove_name_filter(
        self, group_id: str | int, actor_id: str | int, term: str
    ) -> bool:
        await self.require_admin(group_id, actor_id)
        value = self._clean_name_term(term)
        terms = self.list_name_filters(group_id)
        if value not in terms:
            return False
        terms.remove(value)
        self.store.set_group_state(group_id, "name_filters", terms)
        return True

    def list_name_filters(self, group_id: str | int) -> list[str]:
        value = self.store.get_group_state(group_id, "name_filters", [])
        return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []

    @staticmethod
    def _clean_name_term(term: str) -> str:
        value = " ".join(str(term or "").split())
        if not value or len(value) > 80:
            raise ValueError("name-filter term must contain 1 to 80 characters")
        return value

    async def set_ai_enabled(
        self, group_id: str | int, actor_id: str | int, enabled: bool
    ) -> None:
        await self.require_admin(group_id, actor_id)
        self.store.set_group_state(group_id, "google_ai_enabled", bool(enabled))

    def ai_enabled(self, group_id: str | int) -> bool:
        return bool(self.store.get_group_state(group_id, "google_ai_enabled", False))

    async def allow_ai_user(
        self,
        group_id: str | int,
        actor_id: str | int,
        target_id: str | int,
        *,
        display_name: str = "",
        username: str | None = None,
    ) -> bool:
        await self.require_admin(group_id, actor_id)
        return self.store.add_group_command_member(
            group_id, "ai_allowed", target_id,
            display_name=display_name, username=username,
        )

    async def disallow_ai_user(
        self, group_id: str | int, actor_id: str | int, target_id: str | int
    ) -> bool:
        await self.require_admin(group_id, actor_id)
        return self.store.remove_group_command_member(group_id, "ai_allowed", target_id)

    async def allowed_ai_users(
        self, group_id: str | int, actor_id: str | int
    ) -> list[dict[str, Any]]:
        await self.require_admin(group_id, actor_id)
        return self.store.list_group_command_members(group_id, "ai_allowed", limit=100)

    async def set_warning_threshold(
        self, group_id: str | int, actor_id: str | int, threshold: int
    ) -> int:
        await self.require_owner(group_id, actor_id)
        if not 1 <= int(threshold) <= 12:
            raise ValueError("threshold must be between 1 and 12")
        self.store.set_group_state(group_id, "warning_threshold", int(threshold))
        return int(threshold)

    def warning_threshold(self, group_id: str | int) -> int:
        value = self.store.get_group_state(
            group_id, "warning_threshold", self.default_warning_threshold
        )
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return self.default_warning_threshold
        return (
            parsed
            if 1 <= parsed <= 12
            else self.default_warning_threshold
        )

    async def set_punishment_mode(
        self, group_id: str | int, actor_id: str | int, mode: str
    ) -> str:
        await self.require_owner(group_id, actor_id)
        if mode not in {"ban", "mute"}:
            raise ValueError("punishment mode must be ban or mute")
        self.store.set_group_state(group_id, "punishment_mode", mode)
        return mode

    def punishment_mode(self, group_id: str | int) -> str:
        value = self.store.get_group_state(
            group_id, "punishment_mode", self.default_punishment_mode
        )
        return value if value in {"ban", "mute"} else self.default_punishment_mode

    async def add_warning(
        self,
        group_id: str | int,
        actor_id: str | int,
        target_id: str | int,
        *,
        target_name: str,
        actor_name: str,
        reason: str = "اخطار دستی",
    ) -> tuple[int, int]:
        await self.require_admin(group_id, actor_id)
        count = self.store.add_warning(group_id, target_id)
        threshold = self.warning_threshold(group_id)
        self.store.record_group_command_event(
            group_id=group_id,
            event_type="warning",
            user_id=target_id,
            display_name=target_name,
            actor_id=actor_id,
            actor_display=actor_name,
            detail=reason,
        )
        return count, threshold

    def add_automatic_warning(
        self,
        group_id: str | int,
        target_id: str | int,
        *,
        target_name: str,
        reason: str,
        message_id: str | int | None = None,
    ) -> tuple[int, int]:
        """Record a detector-verified event without pretending an admin sent it."""
        count = self.store.add_automatic_warning(
            group_id,
            target_id,
            display_name=target_name,
            reason=reason,
            message_id=message_id,
        )
        return count, self.warning_threshold(group_id)

    def warning_count(self, group_id: str | int, user_id: str | int) -> int:
        return self.store.get_warning_count(group_id, user_id)

    async def warning_counts(
        self, group_id: str | int, actor_id: str | int
    ) -> list[dict[str, Any]]:
        await self.require_admin(group_id, actor_id)
        return self.store.list_warning_counts(group_id)

    async def reset_group_warning_counts(
        self, group_id: str | int, actor_id: str | int
    ) -> int:
        await self.require_admin(group_id, actor_id)
        return self.store.reset_group_warnings(group_id)

    async def reset_one_warning_count(
        self, group_id: str | int, actor_id: str | int, target_id: str | int
    ) -> int:
        await self.require_admin(group_id, actor_id)
        return self.store.reset_warning_count(group_id, target_id)

    async def remove_warning_count(
        self,
        group_id: str | int,
        actor_id: str | int,
        target_id: str | int,
        *,
        target_name: str,
        actor_name: str,
    ) -> int:
        await self.require_admin(group_id, actor_id)
        old = self.store.reset_warning_count(group_id, target_id)
        self.store.record_group_command_event(
            group_id=group_id,
            event_type="warning_removed",
            user_id=target_id,
            display_name=target_name,
            actor_id=actor_id,
            actor_display=actor_name,
            detail=f"{old} → 0",
        )
        return old

    async def reset_violations(
        self,
        group_id: str | int,
        actor_id: str | int,
        target_id: str | int,
        *,
        target_name: str,
        actor_name: str,
        action: str = "reset_violations",
    ) -> int:
        await self.require_zero_permission(group_id, actor_id)
        old = self.store.reset_warning_count(group_id, target_id)
        self.store.record_group_command_event(
            group_id=group_id,
            event_type=action,
            user_id=target_id,
            display_name=target_name,
            actor_id=actor_id,
            actor_display=actor_name,
            detail=f"{old} → 0",
        )
        return old

    async def record_admin_log(
        self,
        group_id: str | int,
        actor_id: str | int,
        *,
        actor_name: str,
        message_id: str | int,
        message_excerpt: str = "",
        target_id: str | int | None = None,
        target_name: str = "",
        detail: str = "حذف پیام",
    ) -> None:
        await self.require_admin(group_id, actor_id)
        self.store.record_group_command_event(
            group_id=group_id,
            event_type="message_deleted",
            user_id=target_id,
            display_name=target_name,
            actor_id=actor_id,
            actor_display=actor_name,
            detail=detail,
            message_id=message_id,
            message_excerpt=message_excerpt,
        )

    async def record_admin_action(
        self,
        group_id: str | int,
        actor_id: str | int,
        *,
        actor_name: str,
        action: str,
        target_id: str | int | None = None,
        target_name: str = "",
        note: str = "",
    ) -> None:
        self.store.record_group_command_event(
            group_id=group_id,
            event_type="admin_action",
            user_id=target_id,
            display_name=target_name,
            actor_id=actor_id,
            actor_display=actor_name,
            detail=f"{action} — {note}" if note else action,
        )

    async def admin_log(
        self, group_id: str | int, actor_id: str | int
    ) -> list[dict[str, Any]]:
        await self.require_admin(group_id, actor_id)
        return self.store.list_group_command_events(
            group_id, event_types=("message_deleted", "admin_action"), limit=200
        )

    async def history_24h(
        self, group_id: str | int, actor_id: str | int
    ) -> list[dict[str, Any]]:
        await self.require_admin(group_id, actor_id)
        since = datetime.now(UTC) - timedelta(hours=24)
        return self.store.list_group_history_events(
            group_id,
            since=since,
            max_users=MAX_HISTORY_USERS,
            records_per_user=MAX_HISTORY_RECORDS_PER_USER,
        )

    async def reset_group_stats(
        self, group_id: str | int, actor_id: str | int
    ) -> None:
        await self.require_admin(group_id, actor_id)
        self.store.reset_group_stats(group_id)

    async def group_stats(self, group_id: str | int) -> dict[str, Any]:
        return self.store.get_group_stats(group_id)

    async def increment_group_stat(
        self, group_id: str | int, field: str, amount: int = 1
    ) -> None:
        self.store.increment_group_stat(group_id, field, amount)

    async def record_message(
        self,
        group_id: str | int,
        user_id: str | int,
        *,
        display_name: str = "",
        username: str | None = None,
    ) -> None:
        self.store.record_group_message(
            group_id,
            user_id,
            display_name=display_name,
            username=username,
        )

    async def update_expiry_title(
        self, group_id: str | int, title: str
    ) -> None:
        current = self.store.get_group_state(group_id, "expiry", {})
        if isinstance(current, dict) and current:
            current["title"] = title
            self.store.set_group_state(group_id, "expiry", current)

    def group_expiry(self, group_id: str | int) -> dict[str, Any] | None:
        value = self.store.get_group_state(group_id, "expiry")
        return value if isinstance(value, dict) else None

    async def set_group_locked(
        self, group_id: str | int, actor_id: str | int, locked: bool
    ) -> bool:
        await self.require_admin(group_id, actor_id)
        setter = getattr(self.management.platform, "set_group_locked", None)
        if not callable(setter):
            from foxbot.platform import PlatformCapabilityUnavailable
            raise PlatformCapabilityUnavailable("group lock/unlock is unavailable")
        key = "group_lock_snapshot"
        if locked:
            previous = self.store.get_group_state(group_id, key)
            snapshot = await setter(group_id, True)
            if previous is None:
                self.store.set_group_state(group_id, key, snapshot)
        else:
            previous = self.store.get_group_state(group_id, key)
            await setter(group_id, False, restore_rights=previous)
            self.store.delete_group_state(group_id, key)
        return True

    async def change_group_title(
        self, group_id: str | int, actor_id: str | int, title: str
    ) -> bool:
        await self.require_admin(group_id, actor_id)
        change = getattr(self.management.platform, "change_group_title", None)
        if not callable(change):
            from foxbot.platform import PlatformCapabilityUnavailable
            raise PlatformCapabilityUnavailable("group title editing is unavailable")
        return await change(group_id, title)

    async def change_group_photo(
        self, group_id: str | int, actor_id: str | int, file_path: str
    ) -> bool:
        await self.require_admin(group_id, actor_id)
        change = getattr(self.management.platform, "change_group_photo", None)
        if not callable(change):
            from foxbot.platform import PlatformCapabilityUnavailable
            raise PlatformCapabilityUnavailable("group photo editing is unavailable")
        return await change(group_id, file_path)

    async def pin_message(
        self, group_id: str | int, actor_id: str | int, message_id: str | int
    ) -> bool:
        await self.require_admin(group_id, actor_id)
        pin = getattr(self.management.platform, "pin_group_message", None)
        if not callable(pin):
            from foxbot.platform import PlatformCapabilityUnavailable
            raise PlatformCapabilityUnavailable("message pinning is unavailable")
        ok = await pin(group_id, message_id)
        if ok:
            self.store.set_group_state(group_id, "pinned_message_id", int(message_id))
        return ok

    async def pinned_message_id(self, group_id: str | int) -> int | None:
        get_pinned = getattr(self.management.platform, "get_pinned_message_id", None)
        if not callable(get_pinned):
            from foxbot.platform import PlatformCapabilityUnavailable
            raise PlatformCapabilityUnavailable("pinned-message lookup is unavailable")
        pinned = await get_pinned(group_id)
        if pinned is None:
            self.store.delete_group_state(group_id, "pinned_message_id")
            return None
        self.store.set_group_state(group_id, "pinned_message_id", pinned)
        return pinned

    async def resolve_replied_user(self, group_id: str | int, event: Any) -> Any | None:
        resolver = getattr(self.management.platform, "resolve_replied_user", None)
        if not callable(resolver):
            from foxbot.platform import PlatformCapabilityUnavailable
            raise PlatformCapabilityUnavailable("reply target resolution is unavailable")
        return await resolver(group_id, event)

    async def resolve_username(self, group_id: str | int, username: str) -> Any | None:
        resolver = getattr(self.management.platform, "resolve_username", None)
        if not callable(resolver):
            from foxbot.platform import PlatformCapabilityUnavailable
            raise PlatformCapabilityUnavailable("username resolution is unavailable")
        return await resolver(group_id, username)

    async def get_message(self, group_id: str | int, message_id: str | int) -> Any | None:
        getter = getattr(self.management.platform, "get_message", None)
        if not callable(getter):
            from foxbot.platform import PlatformCapabilityUnavailable
            raise PlatformCapabilityUnavailable("message lookup is unavailable")
        return await getter(group_id, message_id)

    async def delete_single_message(
        self,
        group_id: str | int,
        actor_id: str | int,
        message_id: str | int,
        *,
        actor_name: str,
        actor_username: str | None = None,
    ) -> bool:
        await self.require_admin(group_id, actor_id)
        message = await self.get_message(group_id, message_id)
        if message is None or int(getattr(message, "id", 0)) != int(message_id):
            return False
        message_group = getattr(message, "chat_id", None)
        if message_group is not None and str(message_group) != str(group_id):
            return False
        excerpt = str(getattr(message, "raw_text", None) or getattr(message, "message", None) or "")[:240]
        sender = getattr(message, "sender", None)
        target_id = getattr(message, "sender_id", None) or getattr(sender, "id", None)
        target_name = " ".join(
            part for part in (
                getattr(sender, "first_name", None),
                getattr(sender, "last_name", None),
            ) if part
        ).strip()
        deleted = await self.management.platform.delete_message(str(group_id), int(message_id))
        if deleted:
            self.store.increment_group_stat(group_id, "deleted")
            self.store.record_group_command_event(
                group_id=group_id,
                event_type="message_deleted",
                user_id=target_id,
                display_name=target_name,
                actor_id=actor_id,
                actor_display=actor_name or actor_username or "کاربر ناشناس",
                detail="حذف پیام",
                message_id=message_id,
                message_excerpt=excerpt,
            )
        return bool(deleted)

    async def prepare_delete_recent(
        self,
        group_id: str | int,
        actor_id: str | int,
        count: int,
    ) -> dict[str, Any]:
        await self.require_admin(group_id, actor_id)
        if not 1 <= count <= 700:
            raise ValueError("requested delete count must be between 1 and 700")
        scanner = getattr(self.management.platform, "get_recent_messages", None)
        if not callable(scanner):
            from foxbot.platform import PlatformCapabilityUnavailable
            raise PlatformCapabilityUnavailable("recent message scan is unavailable")
        messages = await scanner(group_id, count)
        message_ids = [int(message.id) for message in messages if getattr(message, "id", None) is not None]
        if not message_ids:
            raise ValueError("no deletable messages were returned")
        job_id = uuid.uuid4().hex[:12]
        expires_at = (datetime.now(UTC) + timedelta(minutes=2)).isoformat()
        self.store.set_group_state(
            group_id,
            f"delete_plan:{job_id}",
            {
                "job_id": job_id,
                "group_id": str(group_id),
                "actor_id": str(actor_id),
                "message_ids": message_ids,
                "count": len(message_ids),
                "expires_at": expires_at,
            },
        )
        return {"job_id": job_id, "count": len(message_ids), "scan_complete": True}

    async def cancel_delete_recent(
        self, group_id: str | int, actor_id: str | int, job_id: str
    ) -> bool:
        await self.require_admin(group_id, actor_id)
        key = f"delete_plan:{job_id}"
        plan = self.store.get_group_state(group_id, key)
        if not isinstance(plan, dict) or plan.get("actor_id") != str(actor_id):
            return False
        return self.store.delete_group_state(group_id, key)

    async def confirm_delete_recent(
        self,
        group_id: str | int,
        actor_id: str | int,
        job_id: str,
        *,
        actor_name: str,
    ) -> tuple[int, int]:
        await self.require_admin(group_id, actor_id)
        key = f"delete_plan:{job_id}"
        plan = self.store.get_group_state(group_id, key)
        if not isinstance(plan, dict) or plan.get("group_id") != str(group_id) or plan.get("actor_id") != str(actor_id):
            raise PermissionError("delete plan does not belong to this actor and group")
        try:
            expires_at = datetime.fromisoformat(str(plan["expires_at"]))
        except (KeyError, TypeError, ValueError) as exc:
            self.store.delete_group_state(group_id, key)
            raise ValueError("delete plan is invalid") from exc
        if expires_at <= datetime.now(UTC):
            self.store.delete_group_state(group_id, key)
            raise ValueError("delete plan expired")
        ids = [int(value) for value in plan.get("message_ids", [])]
        if not ids or len(ids) != int(plan.get("count", 0)):
            raise ValueError("delete plan is incomplete")
        delete = self.management.platform.delete_message
        getter = getattr(self.management.platform, "get_message", None)
        if not callable(getter):
            from foxbot.platform import PlatformCapabilityUnavailable
            raise PlatformCapabilityUnavailable("message revalidation is unavailable")
        deleted = 0
        failed = 0
        # Re-fetch each staged message in the same group, then submit each
        # delete separately and in order through the serial RPC queue.
        for message_id in ids:
            try:
                message = await getter(group_id, message_id)
                if message is None or int(getattr(message, "id", 0)) != message_id:
                    failed += 1
                    continue
                message_group = getattr(message, "chat_id", None)
                if message_group is not None and str(message_group) != str(group_id):
                    failed += 1
                    continue
                excerpt = str(getattr(message, "raw_text", None) or getattr(message, "message", None) or "")[:240]
                sender = getattr(message, "sender", None)
                target_id = getattr(message, "sender_id", None) or getattr(sender, "id", None)
                target_name = " ".join(
                    part for part in (
                        getattr(sender, "first_name", None),
                        getattr(sender, "last_name", None),
                    ) if part
                ).strip()
                if await delete(str(group_id), message_id):
                    deleted += 1
                    self.store.increment_group_stat(group_id, "deleted")
                    self.store.record_group_command_event(
                        group_id=group_id,
                        event_type="message_deleted",
                        user_id=target_id,
                        display_name=target_name,
                        actor_id=actor_id,
                        actor_display=actor_name,
                        detail="پاکسازی دسته‌ای",
                        message_id=message_id,
                        message_excerpt=excerpt,
                    )
                else:
                    failed += 1
            except Exception:  # noqa: BLE001 - one failed message does not abort the ordered batch
                failed += 1
        self.store.delete_group_state(group_id, key)
        return deleted, failed

    async def set_group_member_count(
        self, group_id: str | int, actor_id: str | int
    ) -> int | None:
        await self.require_admin(group_id, actor_id)
        getter = getattr(self.management.platform, "get_group_member_count", None)
        if not callable(getter):
            from foxbot.platform import PlatformCapabilityUnavailable
            raise PlatformCapabilityUnavailable("group member count is unavailable")
        count = await getter(group_id)
        if count is not None:
            self.store.set_group_member_count(group_id, count)
        return count

    async def set_entertainment(
        self, group_id: str | int, actor_id: str | int, enabled: bool
    ) -> None:
        await self.require_admin(group_id, actor_id)
        self.store.set_group_state(group_id, "entertainment_enabled", bool(enabled))

    def entertainment_enabled(self, group_id: str | int) -> bool:
        return bool(self.store.get_group_state(group_id, "entertainment_enabled", True))

    async def set_clipboard_pending_guide(
        self, group_id: str | int, actor_id: str | int, message_id: str | int
    ) -> None:
        await self.require_admin(group_id, actor_id)
        self.store.set_group_state(
            group_id,
            "clipboard_pending_guide",
            {"actor_id": str(actor_id), "message_id": str(message_id)},
        )

    def clipboard_pending_guide(self, group_id: str | int) -> dict[str, Any] | None:
        value = self.store.get_group_state(group_id, "clipboard_pending_guide")
        return value if isinstance(value, dict) else None

    async def save_clipboard(
        self,
        group_id: str | int,
        actor_id: str | int,
        message_id: str | int,
        text: str,
        entities: list[dict[str, Any]],
    ) -> None:
        guide = self.clipboard_pending_guide(group_id)
        await self.require_admin(group_id, actor_id)
        if not guide or guide.get("message_id") != str(message_id):
            raise PermissionError("clipboard reply does not match the pending guide")
        if not text.strip() or len(text.encode("utf-16-le")) // 2 > 4096:
            raise ValueError("clipboard text must contain 1 to 4096 UTF-16 units")
        self.store.set_group_state(
            group_id, "clipboard_content", {"text": text, "entities": entities}
        )
        self.store.delete_group_state(group_id, "clipboard_pending_guide")

    def clipboard(self, group_id: str | int) -> dict[str, Any] | None:
        value = self.store.get_group_state(group_id, "clipboard_content")
        return value if isinstance(value, dict) else None

    _WORKFLOW_PRIORITY: ClassVar[dict[str, int]] = {
        "law": 0,
        "auto_cleanup": 1,
        "warning_threshold": 2,
        "punishment_mode": 3,
    }

    def _load_pending_workflows(
        self, group_id: str | int
    ) -> dict[str, dict[str, dict[str, Any]]]:
        raw = self.store.get_group_state(group_id, "pending_workflows", {})
        workflows: dict[str, dict[str, dict[str, Any]]] = {}
        if isinstance(raw, dict):
            for actor_id, by_kind in raw.items():
                if not isinstance(by_kind, dict):
                    continue
                actor = str(actor_id)
                normalized: dict[str, dict[str, Any]] = {}
                for kind, workflow in by_kind.items():
                    if isinstance(workflow, dict):
                        normalized[str(kind)] = dict(workflow)
                if normalized:
                    workflows[actor] = normalized

        legacy = self.store.get_group_state(group_id, "pending_workflow")
        if legacy is not None:
            if isinstance(legacy, dict):
                actor = str(legacy.get("actor_id") or "")
                kind = str(legacy.get("kind") or "")
                if actor and kind:
                    workflows.setdefault(actor, {}).setdefault(kind, dict(legacy))
            self.store.delete_group_state(group_id, "pending_workflow")
            self._save_pending_workflows(group_id, workflows)
        return workflows

    def _save_pending_workflows(
        self, group_id: str | int, workflows: dict[str, dict[str, dict[str, Any]]]
    ) -> None:
        compact = {
            actor: by_kind
            for actor, by_kind in workflows.items()
            if by_kind
        }
        if compact:
            self.store.set_group_state(group_id, "pending_workflows", compact)
        else:
            self.store.delete_group_state(group_id, "pending_workflows")

    async def pending_workflow(
        self,
        group_id: str | int,
        actor_id: str | int | None = None,
        *,
        kind: str | None = None,
    ) -> dict[str, Any] | None:
        workflows = self._load_pending_workflows(group_id)
        actors = [str(actor_id)] if actor_id is not None else list(workflows)
        now = datetime.now(UTC)
        candidates: list[tuple[int, str, dict[str, Any]]] = []
        changed = False
        for actor in actors:
            by_kind = workflows.get(actor)
            if not isinstance(by_kind, dict):
                continue
            for workflow_kind, workflow in tuple(by_kind.items()):
                if (
                    workflow.get("actor_id") != actor
                    or workflow.get("kind") != workflow_kind
                ):
                    by_kind.pop(workflow_kind, None)
                    changed = True
                    continue
                expires_at = workflow.get("expires_at")
                if expires_at:
                    try:
                        expires = datetime.fromisoformat(str(expires_at))
                        if expires.tzinfo is None:
                            expires = expires.replace(tzinfo=UTC)
                    except ValueError:
                        by_kind.pop(workflow_kind, None)
                        changed = True
                        continue
                    if expires <= now:
                        by_kind.pop(workflow_kind, None)
                        changed = True
                        continue
                if kind is None or workflow_kind == kind:
                    candidates.append(
                        (self._WORKFLOW_PRIORITY.get(workflow_kind, 100), actor, workflow)
                    )
        if changed:
            self._save_pending_workflows(group_id, workflows)
        if not candidates:
            return None
        return min(candidates, key=lambda item: (item[0], item[1]))[2]

    async def start_workflow(
        self,
        group_id: str | int,
        actor_id: str | int,
        kind: str,
        values: dict[str, Any] | None = None,
        *,
        ttl: timedelta = timedelta(minutes=2),
    ) -> None:
        await self.require_admin(group_id, actor_id)
        workflows = self._load_pending_workflows(group_id)
        actor = str(actor_id)
        workflows.setdefault(actor, {})[kind] = {
            "kind": kind,
            "actor_id": actor,
            "values": values or {},
            "expires_at": (datetime.now(UTC) + ttl).isoformat(),
        }
        self._save_pending_workflows(group_id, workflows)

    async def update_workflow(
        self, group_id: str | int, actor_id: str | int, workflow: dict[str, Any]
    ) -> None:
        actor = str(actor_id)
        kind = str(workflow.get("kind") or "")
        if workflow.get("actor_id") != actor or not kind:
            raise PermissionError("workflow belongs to another actor")
        workflows = self._load_pending_workflows(group_id)
        workflows.setdefault(actor, {})[kind] = dict(workflow)
        self._save_pending_workflows(group_id, workflows)

    async def cancel_workflow(
        self,
        group_id: str | int,
        actor_id: str | int | None = None,
        *,
        kind: str | None = None,
    ) -> None:
        workflows = self._load_pending_workflows(group_id)
        if actor_id is None:
            if kind is None:
                workflows.clear()
            else:
                for by_kind in workflows.values():
                    by_kind.pop(kind, None)
        else:
            actor = str(actor_id)
            by_kind = workflows.get(actor)
            if by_kind is not None:
                if kind is None:
                    workflows.pop(actor, None)
                else:
                    by_kind.pop(kind, None)
        self._save_pending_workflows(group_id, workflows)
