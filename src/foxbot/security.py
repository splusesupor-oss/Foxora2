from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path

from foxbot.domain import ActorKind
from foxbot.storage import BotStore


class OwnerConfigError(RuntimeError):
    pass


class OwnerRegistry:
    """Read the single canonical owner file on each authorization check.

    Missing, unreadable or malformed files fail closed. No owner ID is embedded
    in source code or recovered from a stale runtime copy.
    """

    def __init__(self, canonical_path: str | Path = "config/owner.json") -> None:
        self.path = Path(canonical_path)

    def get_owner_id(self) -> str:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise OwnerConfigError("canonical owner file is missing") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise OwnerConfigError("canonical owner file is unreadable or invalid") from exc
        value = data.get("user_id") if isinstance(data, dict) else None
        if value is None or str(value).strip() == "":
            raise OwnerConfigError("canonical owner ID is not configured")
        return str(value).strip()

    def is_global_owner(self, user_id: str | int) -> bool:
        try:
            return self.get_owner_id() == str(user_id)
        except OwnerConfigError:
            return False


NativeAdminCheck = Callable[[str, str], Awaitable[bool]]


class AccessController:
    """Separate global owner, registered group owner and native group admin."""

    def __init__(
        self,
        owner_registry: OwnerRegistry,
        store: BotStore,
        native_admin_check: NativeAdminCheck,
    ) -> None:
        self.owner_registry = owner_registry
        self.store = store
        self.native_admin_check = native_admin_check

    async def role_for(self, group_id: str | int, user_id: str | int) -> ActorKind | None:
        gid, uid = str(group_id), str(user_id)
        if self.owner_registry.is_global_owner(uid):
            return ActorKind.GLOBAL_OWNER
        if self.store.get_group_owner(gid) == uid:
            return ActorKind.GROUP_OWNER
        if self.store.has_group_command_member(gid, "registered_admin", uid):
            return ActorKind.GROUP_ADMIN
        # API lookup is authoritative for current group-admin status; any API
        # error is expected to be translated to False by the adapter.
        if await self.native_admin_check(gid, uid):
            return ActorKind.GROUP_ADMIN
        return None

    async def can_manage(self, group_id: str | int, user_id: str | int) -> bool:
        return await self.role_for(group_id, user_id) is not None

    def explicit_role_for(
        self, group_id: str | int, user_id: str | int
    ) -> ActorKind | None:
        """Return only application-registered roles, excluding native admins.

        Most source management routes authorize the global owner, the
        registered owner of this group, or an admin explicitly registered for
        this group. Platform-native admin status is a separate capability and
        is used only by routes that explicitly opt into it.
        """
        gid, uid = str(group_id), str(user_id)
        if self.owner_registry.is_global_owner(uid):
            return ActorKind.GLOBAL_OWNER
        if self.store.get_group_owner(gid) == uid:
            return ActorKind.GROUP_OWNER
        if self.store.has_group_command_member(gid, "registered_admin", uid):
            return ActorKind.GROUP_ADMIN
        return None

    def can_manage_explicit_group_roles(
        self, group_id: str | int, user_id: str | int
    ) -> bool:
        return self.explicit_role_for(group_id, user_id) is not None
