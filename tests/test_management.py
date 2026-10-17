from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from foxbot.domain import (
    MatchMode,
    Member,
    MemberPage,
    ModerationKind,
    ModerationResult,
)
from foxbot.management import MemberManagementService
from foxbot.platform import IncompleteMemberScan
from foxbot.security import AccessController, OwnerRegistry
from foxbot.storage import BotStore


class FakePlatform:
    def __init__(self, members: list[Member] | None = None) -> None:
        self.members = {(m.group_id, m.user_id): m for m in (members or [])}
        self.mute_calls: list[tuple[str, str, int | None]] = []
        self.unmute_calls: list[tuple[str, str, dict | None, object]] = []
        self.kick_calls: list[tuple[str, str]] = []
        self.bad_cursor = False
        self.mute_pages_error = False

    async def list_members_page(self, group_id: str, offset: int, limit: int, *, include_bio: bool = False) -> MemberPage:
        if self.bad_cursor:
            page = [m for (gid, _), m in self.members.items() if gid == group_id]
            return MemberPage(tuple(page[:1]), offset, total_count=len(page), raw_count=1)
        all_members = [m for (gid, _), m in sorted(self.members.items()) if gid == group_id]
        selected = all_members[offset : offset + limit]
        next_offset = offset + len(selected) if offset + len(selected) < len(all_members) else None
        return MemberPage(tuple(selected), next_offset, len(all_members), len(selected))

    async def list_active_mutes_page(self, group_id: str, offset: int, limit: int) -> MemberPage:
        if self.mute_pages_error:
            raise RuntimeError("simulated member enumeration failure")
        all_members = [
            m for (gid, _), m in sorted(self.members.items())
            if gid == group_id and m.is_member and m.is_muted
        ]
        selected = all_members[offset : offset + limit]
        next_offset = offset + len(selected) if offset + len(selected) < len(all_members) else None
        return MemberPage(tuple(selected), next_offset, len(all_members), len(selected))

    async def get_member(self, group_id: str, user_id: str, *, include_bio: bool = False) -> Member | None:
        return self.members.get((group_id, user_id))

    async def mute_member(self, group_id: str, user_id: str, *, duration_seconds: int | None) -> ModerationResult:
        self.mute_calls.append((group_id, user_id, duration_seconds))
        current = self.members[(group_id, user_id)]
        self.members[(group_id, user_id)] = replace(current, is_muted=True, mute_status_known=True)
        return ModerationResult(True, verified=True)

    async def unmute_member(
        self,
        group_id: str,
        user_id: str,
        *,
        restore_rights: dict[str, bool] | None = None,
        restore_until: datetime | int | str | None = None,
    ) -> ModerationResult:
        self.unmute_calls.append((group_id, user_id, restore_rights, restore_until))
        current = self.members[(group_id, user_id)]
        self.members[(group_id, user_id)] = replace(current, is_muted=False, mute_status_known=True)
        return ModerationResult(True, verified=True)

    async def kick_member(self, group_id: str, user_id: str) -> ModerationResult:
        self.kick_calls.append((group_id, user_id))
        current = self.members[(group_id, user_id)]
        self.members[(group_id, user_id)] = replace(current, is_member=False, is_banned=False)
        return ModerationResult(True, "", detail="not_banned", verified=True)

    async def is_native_admin(self, group_id: str, user_id: str) -> bool:
        return user_id in {"admin", "admin2"} and group_id in {"g", "g2"}


def member(
    user_id: str,
    *,
    group_id: str = "g",
    name: str = "ordinary user",
    bio: str | None = None,
    bio_available: bool = False,
    muted: bool = False,
    admin: bool = False,
    admin_known: bool = True,
    member_status: bool = True,
    membership_known: bool = True,
) -> Member:
    return Member(
        group_id=group_id,
        user_id=user_id,
        display_name=name,
        bio=bio,
        bio_available=bio_available,
        is_member=member_status,
        membership_status_known=membership_known,
        is_admin=admin,
        admin_status_known=admin_known,
        is_bot=False,
        is_muted=muted,
        mute_status_known=True,
        is_banned=False,
        ban_status_known=True,
        restriction_rights=None,
    )


def build_service(tmp_path, platform: FakePlatform, *, duration: int | None = 3600, ttl: timedelta = timedelta(minutes=5)):
    owner_file = tmp_path / "owner.json"
    owner_file.write_text('{"user_id":"owner"}', encoding="utf-8")
    store = BotStore(":memory:")
    store.add_group_command_member(
        "g", "registered_admin", "admin", display_name="Registered Admin"
    )
    access = AccessController(OwnerRegistry(owner_file), store, platform.is_native_admin)
    service = MemberManagementService(
        platform,
        store,
        access,
        mute_duration_provider=(lambda _gid: duration) if duration is not None else None,
        page_size=2,
        plan_ttl=ttl,
    )
    return service, store


def test_name_action_is_staged_then_confirmed_and_rechecked(tmp_path) -> None:
    async def scenario() -> None:
        platform = FakePlatform([
            member("u1", name="rose user"),
            member("admin-user", name="rose admin", admin=True),
            member("u2", name="other"),
        ])
        service, store = build_service(tmp_path, platform)
        plan = await service.prepare_name_action(
            group_id="g",
            actor_id="admin",
            operation=ModerationKind.MUTE,
            query="rose",
            match_mode=MatchMode.CONTAINS,
        )
        assert plan["status"] == "ready"
        assert plan["scan_complete"] == 1
        assert plan["matched"] == 1
        assert platform.mute_calls == []
        with pytest.raises(PermissionError):
            await service.confirm_name_action(
                job_id=plan["job_id"], group_id="g", actor_id="admin2"
            )
        report = await service.confirm_name_action(
            job_id=plan["job_id"], group_id="g", actor_id="admin"
        )
        assert report.succeeded == 1
        assert report.failed == 0
        assert platform.mute_calls == [("g", "u1", 3600)]
        state = store.get_mute_state("g", "u1")
        assert state["active"] == 1
        assert state["actor_kind"] == "group_admin"
        assert state["source"] == "bot"
        store.close()

    asyncio.run(scenario())


def test_native_admin_is_not_authorized_for_application_bulk_routes(tmp_path) -> None:
    async def scenario() -> None:
        platform = FakePlatform([member("u1", name="rose")])
        service, store = build_service(tmp_path, platform)

        # This actor is a live platform admin in the fake but has not been
        # registered in this group's application admin list.
        with pytest.raises(PermissionError):
            await service.prepare_name_action(
                group_id="g",
                actor_id="admin2",
                operation=ModerationKind.KICK,
                query="rose",
            )
        assert platform.kick_calls == []
        assert store._db.execute("SELECT COUNT(*) FROM bulk_jobs").fetchone()[0] == 0
        store.close()

    asyncio.run(scenario())


def test_incomplete_or_unsafe_scan_cannot_be_confirmed(tmp_path) -> None:
    async def scenario() -> None:
        platform = FakePlatform([
            member("u1", name="rose", admin_known=False),
            member("u2", name="rose"),
        ])
        service, store = build_service(tmp_path, platform)
        plan = await service.prepare_name_action(
            group_id="g", actor_id="admin", operation=ModerationKind.KICK, query="rose"
        )
        assert plan["status"] == "incomplete"
        assert plan["scan_complete"] == 0
        with pytest.raises(ValueError):
            await service.confirm_name_action(job_id=plan["job_id"], group_id="g", actor_id="admin")
        assert platform.kick_calls == []

        platform2 = FakePlatform([member("u1", name="rose")])
        platform2.bad_cursor = True
        service2, store2 = build_service(tmp_path / "second", platform2)
        plan2 = await service2.prepare_name_action(
            group_id="g", actor_id="admin", operation=ModerationKind.KICK, query="rose"
        )
        assert plan2["status"] == "incomplete"
        assert platform2.kick_calls == []
        store.close()
        store2.close()

    tmp_path.joinpath("second").mkdir()
    asyncio.run(scenario())


def test_bio_search_fails_closed_when_api_does_not_supply_all_bios(tmp_path) -> None:
    async def scenario() -> None:
        platform = FakePlatform([member("u1", name="rose", bio="rose bio", bio_available=False)])
        service, store = build_service(tmp_path, platform)
        plan = await service.prepare_name_action(
            group_id="g",
            actor_id="admin",
            operation=ModerationKind.MUTE,
            query="rose",
            include_bio=True,
        )
        assert plan["status"] == "incomplete"
        assert plan["error_code"] == "bio_not_available_for_every_member"
        assert platform.mute_calls == []
        store.close()

    asyncio.run(scenario())


def test_member_search_requires_complete_api_scope_and_can_search_bio(tmp_path) -> None:
    async def scenario() -> None:
        platform = FakePlatform([
            member("u1", name="Rose", bio="garden", bio_available=True),
            member("u2", name="Other", bio="Rose", bio_available=True),
            member("u3", name="Unknown bio", bio_available=False),
        ])
        service, store = build_service(tmp_path, platform)
        by_name = await service.search_members("g", "admin", "rose")
        assert [item.user_id for item in by_name] == ["u1"]
        with pytest.raises(IncompleteMemberScan, match="bio_not_available_for_every_member"):
            await service.search_members("g", "admin", "rose", include_bio=True)
        assert platform.mute_calls == []
        store.close()

    asyncio.run(scenario())


def test_unmute_all_requires_confirmation_and_restores_known_bot_rights(tmp_path) -> None:
    async def scenario() -> None:
        platform = FakePlatform([member("u1", muted=True), member("u2", muted=True)])
        service, store = build_service(tmp_path, platform)
        now = datetime.now(UTC)
        store.record_mute(
            group_id="g",
            user_id="u1",
            actor_id="admin",
            actor_kind="group_admin",
            source="bot",
            previous_rights={"send_messages": False, "send_media": True},
            previous_until=now + timedelta(days=3),
            succeeded=True,
        )
        plan = await service.prepare_clear_active_mutes("g", "admin")
        assert plan["status"] == "ready"
        assert plan["matched"] == 2
        assert platform.unmute_calls == []
        report = await service.confirm_clear_active_mutes(
            job_id=plan["job_id"], group_id="g", actor_id="admin"
        )
        assert report.succeeded == 2
        assert len(platform.unmute_calls) == 2
        call = next(item for item in platform.unmute_calls if item[1] == "u1")
        assert call[2] == {"send_messages": False, "send_media": True}
        assert call[3] is not None
        assert store.get_mute_state("g", "u1")["active"] == 0
        store.close()

    asyncio.run(scenario())


def test_unmute_plan_is_incomplete_if_restricted_member_enumeration_fails(tmp_path) -> None:
    async def scenario() -> None:
        platform = FakePlatform([member("u1", muted=True)])
        platform.mute_pages_error = True
        service, store = build_service(tmp_path, platform)
        plan = await service.prepare_clear_active_mutes("g", "admin")
        assert plan["status"] == "incomplete"
        assert plan["scan_complete"] == 0
        with pytest.raises(ValueError):
            await service.confirm_clear_active_mutes(
                job_id=plan["job_id"], group_id="g", actor_id="admin"
            )
        assert platform.unmute_calls == []
        store.close()

    asyncio.run(scenario())


def test_kick_success_is_logged_only_after_verified_removal(tmp_path) -> None:
    async def scenario() -> None:
        platform = FakePlatform([member("u1", name="rose user")])
        service, store = build_service(tmp_path, platform)
        plan = await service.prepare_name_action(
            group_id="g", actor_id="admin", operation=ModerationKind.KICK, query="rose"
        )
        report = await service.confirm_name_action(
            job_id=plan["job_id"], group_id="g", actor_id="admin"
        )
        assert report.succeeded == 1
        history = await service.list_kick_history("g", "admin")
        assert len(history) == 1
        assert history[0]["user_id"] == "u1"
        assert history[0]["actor_kind"] == "group_admin"
        assert history[0]["membership_status"] == "not_member"
        store.close()

    asyncio.run(scenario())


def test_clearing_internal_ban_list_never_calls_platform_moderation(tmp_path) -> None:
    async def scenario() -> None:
        platform = FakePlatform()
        service, store = build_service(tmp_path, platform)
        store.add_bot_ban(group_id="g", user_id="u1", display_name="User")
        with pytest.raises(PermissionError):
            await service.clear_bot_ban_list("g", "admin")
        store.set_group_owner("g", "admin")
        count = await service.clear_bot_ban_list("g", "admin")
        assert count == 1
        assert store.list_bot_bans("g") == []
        assert len(store.list_archived_bot_bans("g")) == 1
        assert platform.kick_calls == []
        assert platform.unmute_calls == []
        store.close()

    asyncio.run(scenario())


def test_expired_plan_is_rejected_without_action(tmp_path) -> None:
    async def scenario() -> None:
        platform = FakePlatform([member("u1", name="rose user")])
        service, store = build_service(tmp_path, platform, ttl=timedelta(milliseconds=5))
        plan = await service.prepare_name_action(
            group_id="g", actor_id="admin", operation=ModerationKind.KICK, query="rose"
        )
        await asyncio.sleep(0.02)
        with pytest.raises(ValueError, match="expired"):
            await service.confirm_name_action(
                job_id=plan["job_id"], group_id="g", actor_id="admin"
            )
        assert platform.kick_calls == []
        store.close()

    asyncio.run(scenario())


def test_confirmation_lock_prevents_duplicate_execution(tmp_path) -> None:
    async def scenario() -> None:
        platform = FakePlatform([member("u1", name="rose user")])
        service, store = build_service(tmp_path, platform)
        plan = await service.prepare_name_action(
            group_id="g", actor_id="admin", operation=ModerationKind.KICK, query="rose"
        )
        outcomes = await asyncio.gather(
            service.confirm_name_action(job_id=plan["job_id"], group_id="g", actor_id="admin"),
            service.confirm_name_action(job_id=plan["job_id"], group_id="g", actor_id="admin"),
            return_exceptions=True,
        )
        assert sum(not isinstance(item, Exception) for item in outcomes) == 1
        assert sum(isinstance(item, ValueError) for item in outcomes) == 1
        assert platform.kick_calls == [("g", "u1")]
        store.close()

    asyncio.run(scenario())
