from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class ActorKind(str, Enum):
    BOT = "bot"
    GROUP_ADMIN = "group_admin"
    GROUP_OWNER = "group_owner"
    GLOBAL_OWNER = "global_owner"
    UNKNOWN = "unknown"


class ModerationKind(str, Enum):
    MUTE = "mute"
    KICK = "kick"


class MatchMode(str, Enum):
    EXACT = "exact"
    CONTAINS = "contains"


@dataclass(frozen=True, slots=True)
class Member:
    group_id: str
    user_id: str
    display_name: str = ""
    username: str | None = None
    bio: str | None = None
    bio_available: bool = False
    is_member: bool = True
    membership_status_known: bool = False
    is_admin: bool = False
    admin_status_known: bool = False
    is_bot: bool = False
    protected: bool = False
    is_muted: bool = False
    mute_status_known: bool = False
    is_banned: bool = False
    ban_status_known: bool = False
    restriction_rights: dict[str, bool] | None = None
    restriction_until: datetime | int | None = None


@dataclass(frozen=True, slots=True)
class ModerationResult:
    success: bool
    code: str = ""
    detail: str = ""
    verified: bool = False


@dataclass(frozen=True, slots=True)
class MemberPage:
    members: tuple[Member, ...]
    next_offset: int | None
    total_count: int | None = None
    raw_count: int | None = None


@dataclass(frozen=True, slots=True)
class OperationFailure:
    user_id: str
    display_name: str
    code: str
    detail: str = ""


@dataclass(slots=True)
class OperationReport:
    group_id: str
    operation: str
    examined: int = 0
    matched: int = 0
    succeeded: int = 0
    already_done: int = 0
    skipped: int = 0
    failed: int = 0
    complete: bool = True
    failures: list[OperationFailure] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_failure(self, user_id: str, display_name: str, code: str, detail: str = "") -> None:
        self.failed += 1
        self.failures.append(
            OperationFailure(
                user_id=str(user_id),
                display_name=display_name or "",
                code=code,
                detail=detail,
            )
        )
