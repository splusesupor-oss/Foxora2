from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from foxbot.domain import Member, MemberPage, ModerationResult


class PlatformCapabilityUnavailable(RuntimeError):
    pass


class IncompleteMemberScan(RuntimeError):
    pass


class GroupInfoError(RuntimeError):
    """Base class for a failed or incomplete live Soroush group lookup."""


class GroupInfoAccessDenied(GroupInfoError):
    """SoroushPlus refused group metadata due to membership/permissions."""


class GroupNotFound(GroupInfoError):
    """The group ID is invalid or the group cannot be resolved."""


class GroupInfoNetworkError(GroupInfoError):
    """The live group lookup failed because of a transport problem."""


class IncompleteGroupInfo(GroupInfoError):
    """The service returned a partial/unverifiable owner or admin response."""


@dataclass(frozen=True, slots=True)
class GroupRoleMember:
    user_id: str
    display_name: str
    username: str | None = None


@dataclass(frozen=True, slots=True)
class GroupAdministration:
    owner: GroupRoleMember | None
    admins: tuple[GroupRoleMember, ...]


class SoroushPlatform(Protocol):
    async def list_members_page(
        self, group_id: str, offset: int, limit: int, *, include_bio: bool = False
    ) -> MemberPage: ...

    async def get_group_administration(self, group_id: str | int) -> GroupAdministration: ...

    async def list_active_mutes_page(self, group_id: str, offset: int, limit: int) -> MemberPage: ...

    async def get_member(
        self, group_id: str, user_id: str, *, include_bio: bool = False
    ) -> Member | None: ...

    async def mute_member(
        self, group_id: str, user_id: str, *, duration_seconds: int | None
    ) -> ModerationResult: ...

    async def unmute_member(
        self,
        group_id: str,
        user_id: str,
        *,
        restore_rights: dict[str, bool] | None = None,
        restore_until: datetime | int | str | None = None,
    ) -> ModerationResult: ...

    async def kick_member(self, group_id: str, user_id: str) -> ModerationResult: ...

    async def ban_member(self, group_id: str, user_id: str) -> ModerationResult: ...

    async def change_group_photo(self, group_id: str | int, file_path: str) -> bool: ...

    async def delete_message(self, group_id: str, message_id: str | int) -> bool: ...

    async def is_native_admin(self, group_id: str, user_id: str) -> bool: ...
