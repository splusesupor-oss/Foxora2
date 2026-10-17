from __future__ import annotations

import asyncio
import inspect
import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta

from foxbot.domain import (
    ActorKind,
    MatchMode,
    Member,
    MemberPage,
    ModerationKind,
    ModerationResult,
    OperationReport,
)
from foxbot.matching import name_matches
from foxbot.platform import (
    IncompleteMemberScan,
    PlatformCapabilityUnavailable,
    SoroushPlatform,
)
from foxbot.security import AccessController
from foxbot.storage import BotStore

DurationProvider = Callable[[str], int | None | Awaitable[int | None]]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _plan_view(job: dict[str, object] | None) -> dict[str, object]:
    if not job:
        return {}
    keys = (
        "job_id", "group_id", "actor_id", "operation", "query", "match_mode",
        "include_bio", "status", "created_at", "expires_at", "examined", "matched",
        "scan_complete", "error_code",
    )
    return {key: job[key] for key in keys if key in job}


async def _iter_pages(
    fetch_page: Callable[[str, int, int], Awaitable[MemberPage]],
    group_id: str,
    page_size: int,
) -> AsyncIterator[MemberPage]:
    offset = 0
    visited_offsets: set[int] = set()
    while True:
        if offset in visited_offsets:
            raise IncompleteMemberScan("pagination cursor repeated")
        visited_offsets.add(offset)
        page = await fetch_page(group_id, offset, page_size)
        yield page
        raw_count = page.raw_count if page.raw_count is not None else len(page.members)
        if raw_count < 0 or (page.total_count is not None and page.total_count < 0):
            raise IncompleteMemberScan("API returned an invalid pagination count")
        if page.next_offset is None:
            if page.total_count is None:
                raise IncompleteMemberScan("API did not provide a verifiable terminal count")
            if offset + raw_count != page.total_count:
                raise IncompleteMemberScan("API page count disagrees with its reported total")
            return
        if page.next_offset <= offset:
            raise IncompleteMemberScan("pagination cursor did not advance")
        if raw_count == 0 or page.next_offset != offset + raw_count:
            raise IncompleteMemberScan("pagination cursor skipped or overlapped records")
        offset = page.next_offset


class MemberManagementService:
    """Group-scoped moderation with durable, explicitly confirmed bulk plans."""

    def __init__(
        self,
        platform: SoroushPlatform,
        store: BotStore,
        access: AccessController,
        *,
        mute_duration_provider: DurationProvider | None = None,
        bot_user_id: str | int | None = None,
        page_size: int = 100,
        plan_ttl: timedelta = timedelta(minutes=5),
    ) -> None:
        if page_size < 1:
            raise ValueError("page_size must be positive")
        if plan_ttl.total_seconds() <= 0:
            raise ValueError("plan_ttl must be positive")
        self.platform = platform
        self.store = store
        self.access = access
        self.mute_duration_provider = mute_duration_provider
        self.bot_user_id = None if bot_user_id is None else str(bot_user_id)
        self.page_size = page_size
        self.plan_ttl = plan_ttl
        # Prevent two confirm callbacks in this process from executing the same
        # persisted pending targets concurrently. The RPC queue independently
        # serializes every platform request.
        self._bulk_confirmation_lock = asyncio.Lock()

    async def _require_access(self, group_id: str, actor_id: str) -> str:
        role = self.access.explicit_role_for(group_id, actor_id)
        if role is None:
            raise PermissionError(
                "actor is not authorized by the current group's registered roles"
            )
        return role.value

    async def iter_active_mutes(
        self, group_id: str | int, actor_id: str | int
    ) -> AsyncIterator[Member]:
        """Stream platform-confirmed active mute restrictions for one group."""
        gid, actor = str(group_id), str(actor_id)
        await self._require_access(gid, actor)
        async for page in _iter_pages(
            self.platform.list_active_mutes_page, gid, self.page_size
        ):
            for member in page.members:
                if member.group_id != gid:
                    raise IncompleteMemberScan("API returned a member from another group")
                if not member.mute_status_known or not member.membership_status_known:
                    raise IncompleteMemberScan("API did not provide authoritative membership/mute state")
                if member.is_member and member.is_muted:
                    yield member

    async def search_members(
        self,
        group_id: str | int,
        actor_id: str | int,
        query: str,
        *,
        include_bio: bool = False,
        match_mode: MatchMode = MatchMode.CONTAINS,
    ) -> list[Member]:
        """Search a complete, authorized member scan without taking action."""
        if not query or not query.strip():
            raise ValueError("search criterion cannot be empty")
        if not isinstance(match_mode, MatchMode):
            raise TypeError("unsupported match mode")
        gid = str(group_id)
        await self._require_access(gid, str(actor_id))
        matches: list[Member] = []
        async for page in _iter_pages(
            lambda group, offset, limit: self.platform.list_members_page(
                group, offset, limit, include_bio=include_bio
            ),
            gid,
            self.page_size,
        ):
            for member in page.members:
                if member.group_id != gid:
                    raise IncompleteMemberScan("API returned a member from another group")
                if not member.membership_status_known:
                    raise IncompleteMemberScan("membership status is unknown")
                if include_bio and not member.bio_available:
                    raise IncompleteMemberScan("bio_not_available_for_every_member")
                if member.is_member and name_matches(
                    member, query, match_mode, include_bio=include_bio
                ):
                    matches.append(member)
        return matches

    async def list_members(
        self, group_id: str | int, actor_id: str | int
    ) -> list[Member]:
        """Return a complete, authorized group scan for admin/stat reports."""
        gid = str(group_id)
        await self._require_access(gid, str(actor_id))
        members: list[Member] = []
        async for page in _iter_pages(
            self.platform.list_members_page, gid, self.page_size
        ):
            for member in page.members:
                if member.group_id != gid or not member.membership_status_known:
                    raise IncompleteMemberScan("member enumeration returned incomplete or cross-group data")
                members.append(member)
        return members

    async def moderate_single_member(
        self,
        *,
        group_id: str | int,
        actor_id: str | int,
        user_id: str | int,
        operation: ModerationKind,
    ) -> OperationReport:
        """Freshly validate and moderate one reply-target in the current group."""
        if not isinstance(operation, ModerationKind):
            raise TypeError("unsupported moderation operation")
        gid, actor, uid = str(group_id), str(actor_id), str(user_id)
        actor_role = await self._require_access(gid, actor)
        current = await self.platform.get_member(gid, uid)
        report = OperationReport(group_id=gid, operation=operation.value, examined=1, matched=1)
        if current is None:
            report.skipped = 1
            return report
        if current.group_id != gid or current.user_id != uid:
            raise IncompleteMemberScan("member lookup returned a different user or group")
        if not current.membership_status_known or not current.admin_status_known:
            raise IncompleteMemberScan("membership/admin status is unknown")
        if not current.is_member or current.is_admin or current.is_bot or current.protected:
            report.skipped = 1
            return report
        if not current.ban_status_known or current.is_banned:
            report.skipped = 1
            return report
        if operation is ModerationKind.MUTE:
            if not current.mute_status_known:
                raise IncompleteMemberScan("mute status is unknown")
            if current.is_muted:
                report.already_done = 1
                return report
            duration = await self._mute_duration(gid)
            outcome = await self.platform.mute_member(gid, uid, duration_seconds=duration)
            ok = outcome.success and outcome.verified
            self.store.record_mute(
                group_id=gid,
                user_id=uid,
                display_name=current.display_name,
                reason="manual mute command",
                actor_id=actor,
                actor_kind=actor_role,
                source="bot",
                expires_at=(_utc_now() + timedelta(seconds=duration)) if duration is not None else None,
                previous_rights=current.restriction_rights,
                previous_until=current.restriction_until,
                succeeded=ok,
                error_code="" if ok else (outcome.code or "mute_unverified"),
            )
            if ok:
                report.succeeded = 1
                self.store.increment_group_stat(gid, "muted")
            else:
                report.add_failure(uid, current.display_name, outcome.code or "mute_unverified", outcome.detail)
            return report

        outcome = await self.platform.kick_member(gid, uid)
        ok = outcome.success and outcome.verified
        self.store.record_kick_event(
            event_key=f"bot:manual:{uuid.uuid4()}:{uid}",
            group_id=gid,
            user_id=uid,
            display_name=current.display_name,
            reason="manual kick command",
            actor_id=actor,
            actor_kind=actor_role,
            source="bot",
            result="success" if ok else "failed",
            membership_status="not_member" if ok else "unknown",
            ban_status="not_banned" if ok else "unknown",
        )
        if ok:
            self.store.add_bot_ban(
                group_id=gid,
                user_id=uid,
                display_name=current.display_name,
                username=current.username,
                reason="manual kick command",
                source="manual",
            )
            self.store.increment_group_stat(gid, "kicked")
            report.succeeded = 1
        else:
            report.add_failure(uid, current.display_name, outcome.code or "kick_unverified", outcome.detail)
        return report

    async def ban_single_member(
        self,
        *,
        group_id: str | int,
        actor_id: str | int,
        user_id: str | int,
        reason: str = "warning threshold reached",
    ) -> OperationReport:
        """Apply a permanent group ban after fresh, fail-closed validation."""
        gid, actor, uid = str(group_id), str(actor_id), str(user_id)
        await self._require_access(gid, actor)
        current = await self.platform.get_member(gid, uid)
        report = OperationReport(group_id=gid, operation="ban", examined=1, matched=1)
        if current is None:
            report.skipped = 1
            return report
        if current.group_id != gid or current.user_id != uid:
            raise IncompleteMemberScan("member lookup returned a different user or group")
        if not current.membership_status_known or not current.admin_status_known or not current.ban_status_known:
            raise IncompleteMemberScan("membership, admin or ban status is unknown")
        if current.is_banned:
            report.already_done = 1
            return report
        if not current.is_member or current.is_admin or current.is_bot or current.protected:
            report.skipped = 1
            return report
        ban = getattr(self.platform, "ban_member", None)
        if not callable(ban):
            raise PlatformCapabilityUnavailable("permanent group ban is unavailable")
        outcome = await ban(gid, uid)
        if outcome.success and outcome.verified:
            self.store.add_bot_ban(
                group_id=gid,
                user_id=uid,
                display_name=current.display_name,
                username=current.username,
                reason=reason,
                source="warning_threshold",
            )
            self.store.increment_group_stat(gid, "kicked")
            report.succeeded = 1
        else:
            report.add_failure(uid, current.display_name, outcome.code or "ban_unverified", outcome.detail)
        return report

    async def automatic_action(
        self,
        *,
        group_id: str | int,
        user_id: str | int,
        mode: str,
        reason: str,
        duration_seconds: int | None = None,
    ) -> ModerationResult:
        """Apply a detector-triggered action after fresh, group-scoped protection checks."""
        gid, uid = str(group_id), str(user_id)
        if mode not in {"ban", "mute"}:
            return ModerationResult(False, "invalid_action_mode", verified=True)
        if duration_seconds is not None and not 0 < int(duration_seconds) <= 40 * 24 * 60 * 60:
            return ModerationResult(False, "invalid_duration", verified=True)
        if await self.access.role_for(gid, uid) is not None:
            return ModerationResult(False, "protected_bot_admin", verified=True)
        if self.store.has_group_command_member(gid, "vip", uid):
            return ModerationResult(False, "protected_vip", verified=True)

        current = await self.platform.get_member(gid, uid)
        if current is None:
            return ModerationResult(False, "not_member", verified=True)
        if current.group_id != gid or current.user_id != uid:
            raise IncompleteMemberScan("automatic moderation lookup returned a different user or group")
        if not current.membership_status_known or not current.admin_status_known:
            return ModerationResult(False, "member_or_admin_state_unknown", verified=False)
        if not current.is_member or current.is_admin or current.is_bot or current.protected:
            return ModerationResult(False, "protected_or_absent_member", verified=True)
        if not current.ban_status_known:
            return ModerationResult(False, "ban_state_unknown", verified=False)
        if current.is_banned:
            return ModerationResult(False, "already_banned", verified=True)

        bot_actor = self.bot_user_id
        actor_kind = ActorKind.BOT.value if bot_actor is not None else ActorKind.UNKNOWN.value
        safe_reason = str(reason or "automatic spam detection")[:120]
        if mode == "ban":
            ban = getattr(self.platform, "ban_member", None)
            if not callable(ban):
                raise PlatformCapabilityUnavailable("permanent group ban is unavailable")
            outcome = await ban(gid, uid)
            succeeded = bool(outcome.success and outcome.verified)
            if succeeded:
                self.store.add_bot_ban(
                    group_id=gid,
                    user_id=uid,
                    display_name=current.display_name,
                    username=current.username,
                    reason=safe_reason,
                    source="automatic_spam",
                )
                self.store.increment_group_stat(gid, "kicked")
            self.store.record_group_command_event(
                group_id=gid,
                event_type="automatic_ban" if succeeded else "automatic_ban_failed",
                user_id=uid,
                display_name=current.display_name,
                actor_id=bot_actor,
                actor_display="ربات",
                detail=safe_reason if succeeded else (outcome.code or "ban_not_verified"),
            )
            return ModerationResult(
                succeeded,
                outcome.code or ("" if succeeded else "ban_not_verified"),
                outcome.detail,
                verified=bool(outcome.verified),
            )

        if not current.mute_status_known:
            return ModerationResult(False, "mute_state_unknown", verified=False)
        if current.is_muted:
            return ModerationResult(False, "already_muted", verified=True)
        outcome = await self.platform.mute_member(
            gid, uid, duration_seconds=duration_seconds
        )
        succeeded = bool(outcome.success and outcome.verified)
        expires_at = (
            _utc_now() + timedelta(seconds=int(duration_seconds))
            if succeeded and duration_seconds is not None
            else None
        )
        self.store.record_mute(
            group_id=gid,
            user_id=uid,
            display_name=current.display_name,
            username=current.username,
            reason=safe_reason,
            actor_id=bot_actor,
            actor_kind=actor_kind,
            source="automatic_spam",
            expires_at=expires_at,
            previous_rights=current.restriction_rights,
            previous_until=current.restriction_until,
            succeeded=succeeded,
            error_code="" if succeeded else (outcome.code or "mute_not_verified"),
        )
        if succeeded:
            self.store.increment_group_stat(gid, "muted")
        return ModerationResult(
            succeeded,
            outcome.code or ("" if succeeded else "mute_not_verified"),
            outcome.detail,
            verified=bool(outcome.verified),
        )

    async def unmute_single_member(
        self, *, group_id: str | int, actor_id: str | int, user_id: str | int
    ) -> OperationReport:
        gid, actor, uid = str(group_id), str(actor_id), str(user_id)
        actor_role = await self._require_access(gid, actor)
        current = await self.platform.get_member(gid, uid)
        report = OperationReport(group_id=gid, operation="unmute", examined=1, matched=1)
        if current is None:
            self.store.mark_mute_inactive(gid, uid)
            report.skipped = 1
            return report
        if current.group_id != gid or current.user_id != uid:
            raise IncompleteMemberScan("member lookup returned a different user or group")
        if not current.membership_status_known or not current.admin_status_known or not current.ban_status_known or not current.mute_status_known:
            raise IncompleteMemberScan("membership, admin, ban or mute status is unknown")
        if not current.is_member or current.is_admin or current.is_bot or current.protected or current.is_banned:
            report.skipped = 1
            return report
        if not current.is_muted:
            self.store.mark_mute_inactive(gid, uid)
            report.already_done = 1
            return report
        restore_rights, restore_until = self._local_restore(gid, uid)
        outcome = await self.platform.unmute_member(
            gid, uid, restore_rights=restore_rights, restore_until=restore_until
        )
        ok = outcome.success and outcome.verified
        self.store.record_unmute(
            group_id=gid,
            user_id=uid,
            display_name=current.display_name,
            reason="manual unmute command",
            actor_id=actor,
            actor_kind=actor_role,
            source="bot",
            succeeded=ok,
            error_code="" if ok else (outcome.code or "unmute_unverified"),
        )
        if ok:
            report.succeeded = 1
        else:
            report.add_failure(uid, current.display_name, outcome.code or "unmute_unverified", outcome.detail)
        return report

    async def release_single_member(
        self, *, group_id: str | int, actor_id: str | int, user_id: str | int
    ) -> OperationReport:
        gid, actor, uid = str(group_id), str(actor_id), str(user_id)
        await self._require_access(gid, actor)
        unban = getattr(self.platform, "unban_member", None)
        if not callable(unban):
            raise PlatformCapabilityUnavailable("unban is not supported by the installed SPlusthon adapter")
        outcome = await unban(gid, uid)
        report = OperationReport(group_id=gid, operation="unban", examined=1, matched=1)
        if outcome.success and outcome.verified:
            self.store.mark_mute_inactive(gid, uid)
            self.store.remove_bot_ban(gid, uid)
            self.store.reset_warning_count(gid, uid)
            report.succeeded = 1
        else:
            report.add_failure(uid, "", outcome.code or "unban_unverified", outcome.detail)
        return report

    async def prepare_clear_active_mutes(
        self, group_id: str | int, actor_id: str | int
    ) -> dict[str, object]:
        """Build a validated unmute plan. No moderation request is made here."""
        gid, actor = str(group_id), str(actor_id)
        await self._require_access(gid, actor)
        job_id = str(uuid.uuid4())
        created = _utc_now()
        self.store.create_bulk_job(
            job_id=job_id,
            group_id=gid,
            actor_id=actor,
            operation="unmute_all",
            query="",
            match_mode=MatchMode.EXACT.value,
            include_bio=False,
            created_at=created,
            expires_at=created + self.plan_ttl,
        )
        try:
            async for page in _iter_pages(
                self.platform.list_active_mutes_page, gid, self.page_size
            ):
                targets: list[tuple[str, str]] = []
                for member in page.members:
                    if member.group_id != gid:
                        raise IncompleteMemberScan("API returned a member from another group")
                    if not member.membership_status_known or not member.mute_status_known:
                        raise IncompleteMemberScan("membership or mute status is unknown")
                    if not member.admin_status_known:
                        raise IncompleteMemberScan("member admin status is unknown")
                    if member.is_member and member.is_muted and not member.is_admin:
                        if not member.user_id:
                            raise IncompleteMemberScan("muted member has no stable user ID")
                        targets.append((member.user_id, member.display_name))
                self.store.add_bulk_targets_batch(job_id, targets)
                self.store.update_bulk_scan(job_id, examined_delta=len(page.members))
        except Exception as exc:  # noqa: BLE001 - any scan/API failure invalidates the staged plan
            self.store.update_bulk_scan(
                job_id,
                status="incomplete",
                scan_complete=False,
                error_code=type(exc).__name__,
            )
            return _plan_view(self.store.get_bulk_job(job_id))

        self.store.update_bulk_scan(job_id, status="ready", scan_complete=True)
        return _plan_view(self.store.get_bulk_job(job_id))

    async def confirm_clear_active_mutes(
        self, *, job_id: str, group_id: str | int, actor_id: str | int
    ) -> OperationReport:
        """Execute a previously confirmed complete unmute plan, one member at a time."""
        async with self._bulk_confirmation_lock:
            gid, actor = str(group_id), str(actor_id)
            actor_role = await self._require_access(gid, actor)
            job = self.store.get_bulk_job(job_id)
            if not job or job["group_id"] != gid or job["actor_id"] != actor:
                raise PermissionError("plan does not belong to this actor and group")
            if job["operation"] != "unmute_all":
                raise ValueError("plan is not an unmute-all plan")
            self._check_ready_plan(job)
            self.store.finish_bulk_job(job_id, "running")
            report = OperationReport(
                group_id=gid,
                operation="unmute_all",
                examined=int(job["examined"]),
                matched=int(job["matched"]),
            )
            while True:
                targets = self.store.list_pending_bulk_targets(job_id, limit=self.page_size)
                if not targets:
                    break
                for target in targets:
                    uid, old_name = target["user_id"], target["display_name"]
                    attempted = False
                    try:
                        current = await self.platform.get_member(gid, uid)
                        if current is None:
                            self.store.mark_mute_inactive(gid, uid)
                            self.store.update_bulk_target(job_id, uid, "skipped", "no_longer_member")
                            report.skipped += 1
                            continue
                        if current.group_id != gid:
                            raise IncompleteMemberScan("member lookup returned another group")
                        if not (
                            current.membership_status_known
                            and current.admin_status_known
                            and current.mute_status_known
                            and current.ban_status_known
                        ):
                            raise IncompleteMemberScan("current membership/admin/mute status is unknown")
                        if not current.is_member:
                            self.store.mark_mute_inactive(gid, uid)
                            self.store.update_bulk_target(job_id, uid, "skipped", "no_longer_member")
                            report.skipped += 1
                            continue
                        if current.is_admin:
                            self.store.update_bulk_target(job_id, uid, "skipped", "current_admin")
                            report.skipped += 1
                            continue
                        if current.is_banned:
                            self.store.update_bulk_target(job_id, uid, "skipped", "currently_banned")
                            report.skipped += 1
                            continue
                        if not current.is_muted:
                            self.store.mark_mute_inactive(gid, uid)
                            self.store.update_bulk_target(job_id, uid, "already_done", "already_unmuted")
                            report.already_done += 1
                            continue

                        restore_rights, restore_until = self._local_restore(gid, uid)
                        attempted = True
                        outcome = await self.platform.unmute_member(
                            gid,
                            uid,
                            restore_rights=restore_rights,
                            restore_until=restore_until,
                        )
                        ok = outcome.success and outcome.verified
                        self.store.record_unmute(
                            group_id=gid,
                            user_id=uid,
                            display_name=current.display_name or old_name,
                            reason="bulk unmute",
                            actor_id=actor,
                            actor_kind=actor_role,
                            source="bot",
                            succeeded=ok,
                            error_code="" if ok else (outcome.code or "unmute_unverified"),
                        )
                        if ok:
                            report.succeeded += 1
                            self.store.update_bulk_target(job_id, uid, "succeeded")
                        else:
                            code = outcome.code or "unmute_unverified"
                            report.add_failure(uid, current.display_name or old_name, code, outcome.detail)
                            self.store.update_bulk_target(job_id, uid, "failed", code)
                    except Exception as exc:  # noqa: BLE001 - include API failures in the per-member report
                        code = type(exc).__name__
                        if attempted:
                            self.store.record_unmute(
                                group_id=gid,
                                user_id=uid,
                                display_name=old_name,
                                reason="bulk unmute",
                                actor_id=actor,
                                actor_kind=actor_role,
                                source="bot",
                                succeeded=False,
                                error_code=code,
                            )
                        report.add_failure(uid, old_name, code, str(exc))
                        self.store.update_bulk_target(job_id, uid, "failed", code)
            self.store.finish_bulk_job(job_id, "completed")
            report.metadata["job_id"] = job_id
            return report

    async def prepare_name_action(
        self,
        *,
        group_id: str | int,
        actor_id: str | int,
        operation: ModerationKind,
        query: str,
        match_mode: MatchMode = MatchMode.CONTAINS,
        include_bio: bool = False,
    ) -> dict[str, object]:
        """Scan and persist a name/bio action plan; never punishes during scanning."""
        if not isinstance(operation, ModerationKind):
            raise TypeError("unsupported moderation operation")
        if not isinstance(match_mode, MatchMode):
            raise TypeError("unsupported match mode")
        if not query or not query.strip():
            raise ValueError("search criterion cannot be empty")
        gid, actor = str(group_id), str(actor_id)
        await self._require_access(gid, actor)
        job_id = str(uuid.uuid4())
        created = _utc_now()
        self.store.create_bulk_job(
            job_id=job_id,
            group_id=gid,
            actor_id=actor,
            operation=operation.value,
            query=query,
            match_mode=match_mode.value,
            include_bio=include_bio,
            created_at=created,
            expires_at=created + self.plan_ttl,
        )
        incomplete_reason = ""
        try:
            async for page in _iter_pages(
                lambda g, offset, limit: self.platform.list_members_page(
                    g, offset, limit, include_bio=include_bio
                ),
                gid,
                self.page_size,
            ):
                candidates: list[tuple[str, str]] = []
                for member in page.members:
                    if member.group_id != gid:
                        raise IncompleteMemberScan("API returned a member from another group")
                    if not member.membership_status_known or not member.admin_status_known:
                        incomplete_reason = incomplete_reason or "membership_or_admin_status_unknown"
                    if include_bio and not member.bio_available:
                        incomplete_reason = incomplete_reason or "bio_not_available_for_every_member"
                    if not member.is_member or member.is_bot or member.is_admin or member.protected:
                        continue
                    if name_matches(member, query, match_mode, include_bio=include_bio):
                        if not member.user_id:
                            raise IncompleteMemberScan("matched member has no stable user ID")
                        candidates.append((member.user_id, member.display_name))
                self.store.add_bulk_targets_batch(job_id, candidates)
                self.store.update_bulk_scan(job_id, examined_delta=len(page.members))
            if incomplete_reason:
                raise IncompleteMemberScan(incomplete_reason)
        except Exception as exc:  # noqa: BLE001 - any scan/API failure invalidates the staged plan
            code = incomplete_reason or type(exc).__name__
            self.store.update_bulk_scan(
                job_id,
                status="incomplete",
                scan_complete=False,
                error_code=code,
            )
            return _plan_view(self.store.get_bulk_job(job_id))

        self.store.update_bulk_scan(job_id, status="ready", scan_complete=True)
        return _plan_view(self.store.get_bulk_job(job_id))

    async def cancel_bulk_action(
        self, *, job_id: str, group_id: str | int, actor_id: str | int
    ) -> bool:
        gid, actor = str(group_id), str(actor_id)
        await self._require_access(gid, actor)
        job = self.store.get_bulk_job(job_id)
        if not job or job["group_id"] != gid or job["actor_id"] != actor:
            return False
        if job["status"] != "ready":
            return False
        self.store.finish_bulk_job(job_id, "cancelled")
        return True

    async def cancel_name_action(
        self, *, job_id: str, group_id: str | int, actor_id: str | int
    ) -> bool:
        """Compatibility alias; all staged bulk jobs share the same cancel path."""
        return await self.cancel_bulk_action(
            job_id=job_id, group_id=group_id, actor_id=actor_id
        )

    async def confirm_name_action(
        self, *, job_id: str, group_id: str | int, actor_id: str | int
    ) -> OperationReport:
        async with self._bulk_confirmation_lock:
            gid, actor = str(group_id), str(actor_id)
            actor_role = await self._require_access(gid, actor)
            job = self.store.get_bulk_job(job_id)
            if not job or job["group_id"] != gid or job["actor_id"] != actor:
                raise PermissionError("plan does not belong to this actor and group")
            if job["operation"] not in {ModerationKind.MUTE.value, ModerationKind.KICK.value}:
                raise ValueError("plan is not a name-based mute/kick plan")
            self._check_ready_plan(job)
            operation = ModerationKind(job["operation"])
            mode = MatchMode(job["match_mode"])
            include_bio = bool(job["include_bio"])
            query = str(job["query"])
            mute_duration = await self._mute_duration(gid) if operation is ModerationKind.MUTE else None
            report = OperationReport(
                group_id=gid,
                operation=operation.value,
                examined=int(job["examined"]),
                matched=int(job["matched"]),
            )
            self.store.finish_bulk_job(job_id, "running")
            while True:
                targets = self.store.list_pending_bulk_targets(job_id, limit=self.page_size)
                if not targets:
                    break
                for target in targets:
                    uid, old_name = target["user_id"], target["display_name"]
                    attempted = False
                    try:
                        current = await self.platform.get_member(
                            gid, uid, include_bio=include_bio
                        )
                        if current is None:
                            self.store.update_bulk_target(job_id, uid, "skipped", "no_longer_member")
                            report.skipped += 1
                            continue
                        if current.group_id != gid:
                            raise IncompleteMemberScan("member lookup returned another group")
                        if not current.membership_status_known or not current.admin_status_known:
                            raise IncompleteMemberScan("membership/admin status is unknown")
                        if not current.is_member or current.is_admin or current.is_bot or current.protected:
                            self.store.update_bulk_target(job_id, uid, "skipped", "protected_or_not_member")
                            report.skipped += 1
                            continue
                        if not current.ban_status_known:
                            raise IncompleteMemberScan("current ban status is unknown")
                        if current.is_banned:
                            self.store.update_bulk_target(job_id, uid, "skipped", "currently_banned")
                            report.skipped += 1
                            continue
                        if include_bio and not current.bio_available:
                            raise IncompleteMemberScan("member biography is no longer available")
                        if not name_matches(current, query, mode, include_bio=include_bio):
                            self.store.update_bulk_target(job_id, uid, "skipped", "criterion_no_longer_matches")
                            report.skipped += 1
                            continue

                        if operation is ModerationKind.MUTE:
                            if not current.mute_status_known:
                                raise IncompleteMemberScan("current mute state is unknown")
                            if current.is_muted:
                                self.store.update_bulk_target(job_id, uid, "already_done", "already_muted")
                                report.already_done += 1
                                continue
                            attempted = True
                            outcome = await self.platform.mute_member(
                                gid, uid, duration_seconds=mute_duration
                            )
                            ok = outcome.success and outcome.verified
                            self.store.record_mute(
                                group_id=gid,
                                user_id=uid,
                                display_name=current.display_name or old_name,
                                reason=f"name match: {query}",
                                actor_id=actor,
                                actor_kind=actor_role,
                                source="bot",
                                expires_at=(
                                    _utc_now() + timedelta(seconds=mute_duration)
                                    if mute_duration is not None else None
                                ),
                                previous_rights=current.restriction_rights,
                                previous_until=current.restriction_until,
                                succeeded=ok,
                                error_code="" if ok else (outcome.code or "mute_unverified"),
                            )
                        else:
                            attempted = True
                            outcome = await self.platform.kick_member(gid, uid)
                            ok = outcome.success and outcome.verified
                            if ok:
                                self.store.record_kick_event(
                                    event_key=f"bot:{job_id}:{uid}",
                                    group_id=gid,
                                    user_id=uid,
                                    display_name=current.display_name or old_name,
                                    reason=f"name match: {query}",
                                    actor_id=actor,
                                    actor_kind=actor_role,
                                    source="bot",
                                    result="success",
                                    membership_status="not_member",
                                    ban_status="not_banned",
                                )

                        if ok:
                            report.succeeded += 1
                            self.store.increment_group_stat(
                                gid, "muted" if operation is ModerationKind.MUTE else "kicked"
                            )
                            self.store.update_bulk_target(job_id, uid, "succeeded")
                        else:
                            code = outcome.code or f"{operation.value}_unverified"
                            report.add_failure(
                                uid, current.display_name or old_name, code, outcome.detail
                            )
                            self.store.update_bulk_target(job_id, uid, "failed", code)
                    except Exception as exc:  # noqa: BLE001 - capture uncertain API outcomes for this target
                        code = type(exc).__name__
                        if attempted and operation is ModerationKind.KICK:
                            self.store.record_kick_event(
                                event_key=f"bot:{job_id}:{uid}",
                                group_id=gid,
                                user_id=uid,
                                display_name=old_name,
                                reason=f"name match: {query}",
                                actor_id=actor,
                                actor_kind=actor_role,
                                source="bot",
                                result="unknown",
                                membership_status="unknown",
                                ban_status="unknown",
                            )
                        elif attempted and operation is ModerationKind.MUTE:
                            self.store.record_mute(
                                group_id=gid,
                                user_id=uid,
                                display_name=old_name,
                                reason=f"name match: {query}",
                                actor_id=actor,
                                actor_kind=actor_role,
                                source="bot",
                                expires_at=(
                                    _utc_now() + timedelta(seconds=mute_duration)
                                    if mute_duration is not None else None
                                ),
                                succeeded=False,
                                error_code=code,
                            )
                        report.add_failure(uid, old_name, code, str(exc))
                        self.store.update_bulk_target(job_id, uid, "failed", code)
            self.store.finish_bulk_job(job_id, "completed")
            report.metadata["job_id"] = job_id
            return report

    def _local_restore(
        self, group_id: str, user_id: str
    ) -> tuple[dict[str, bool] | None, str | int | None]:
        state = self.store.get_mute_state(group_id, user_id)
        if not state or not state["active"] or state["source"] != "bot":
            return None, None
        raw = state.get("previous_rights_json")
        if raw is None:
            rights: dict[str, bool] = {}
        else:
            decoded = json.loads(raw)
            if not isinstance(decoded, dict) or any(
                not isinstance(key, str) or not isinstance(value, bool)
                for key, value in decoded.items()
            ):
                raise ValueError("stored pre-mute rights are invalid")
            rights = decoded
        return rights, state.get("previous_until")

    def _check_ready_plan(self, job: dict[str, object]) -> None:
        if not bool(job["scan_complete"]) or job["status"] not in {"ready", "running"}:
            raise ValueError("plan is incomplete or not ready for confirmation")
        if _parse_datetime(str(job["expires_at"])) <= _utc_now():
            self.store.finish_bulk_job(str(job["job_id"]), "expired")
            raise ValueError("plan expired")

    async def _mute_duration(self, group_id: str) -> int | None:
        if self.mute_duration_provider is None:
            raise ValueError("existing mute/punishment duration is not configured")
        value = self.mute_duration_provider(group_id)
        if inspect.isawaitable(value):
            value = await value
        if value is not None and int(value) <= 0:
            raise ValueError("mute duration must be positive or None")
        return None if value is None else int(value)

    async def record_observed_kick(
        self,
        *,
        group_id: str | int,
        user_id: str | int,
        event_key: str,
        actor_id: str | int | None = None,
        display_name: str = "",
    ) -> bool:
        """Record a platform-emitted kick event, deduplicated by its stable key."""
        gid, uid = str(group_id), str(user_id)
        if not gid or not uid or not event_key:
            return False
        actor = None if actor_id is None else str(actor_id)
        actor_kind = ActorKind.UNKNOWN.value
        if actor is not None:
            if actor == self.bot_user_id:
                actor_kind = ActorKind.BOT.value
            else:
                role = await self.access.role_for(gid, actor)
                actor_kind = role.value if role is not None else ActorKind.UNKNOWN.value
        return self.store.record_kick_event(
            event_key=event_key,
            group_id=gid,
            user_id=uid,
            display_name=display_name,
            reason="platform kick event",
            actor_id=actor,
            actor_kind=actor_kind,
            source="platform_event",
            result="success",
            membership_status="not_member",
            ban_status="unknown",
        )

    async def clear_bot_ban_list(
        self, group_id: str | int, actor_id: str | int
    ) -> int:
        """Archive and clear the local list only; this never calls platform unban."""
        role = await self.access.role_for(str(group_id), str(actor_id))
        if role not in {ActorKind.GLOBAL_OWNER, ActorKind.GROUP_OWNER}:
            raise PermissionError("only the group owner or global owner may clear the local ban list")
        return self.store.clear_bot_ban_list(group_id)

    async def list_kick_history(
        self,
        group_id: str | int,
        actor_id: str | int,
        *,
        limit: int = 100,
        offset: int = 0,
        include_unverified: bool = False,
    ) -> list[dict[str, object]]:
        await self._require_access(str(group_id), str(actor_id))
        return self.store.list_kick_events(
            group_id,
            limit=limit,
            offset=offset,
            result=None if include_unverified else "success",
        )
