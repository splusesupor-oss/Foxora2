from __future__ import annotations

from datetime import datetime
from typing import Protocol

from foxbot.domain import Member, MemberPage, ModerationResult


class PlatformCapabilityUnavailable(RuntimeError):
    pass


class IncompleteMemberScan(RuntimeError):
    pass


class SoroushPlatform(Protocol):
    async def list_members_page(
        self, group_id: str, offset: int, limit: int, *, include_bio: bool = False
    ) -> MemberPage: ...

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
