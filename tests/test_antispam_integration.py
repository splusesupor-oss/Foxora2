from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from foxbot.domain import Member, ModerationResult
from foxbot.filters import WordFilter
from foxbot.group_admin import GroupAdminService
from foxbot.legacy_admins import LegacyAdminRegistry
from foxbot.management import MemberManagementService
from foxbot.message_pipeline import MessagePipeline
from foxbot.security import AccessController, OwnerRegistry
from foxbot.soroush_events import SPlusthonEventBridge
from foxbot.storage import BotStore


class ModerationPlatform:
    def __init__(
        self, *, mute_ok: bool = True, ban_ok: bool = True, delete_ok: bool = True
    ) -> None:
        self.member = Member(
            group_id="g",
            user_id="u",
            display_name="Test User",
            username="testuser",
            is_member=True,
            membership_status_known=True,
            is_admin=False,
            admin_status_known=True,
            is_bot=False,
            is_muted=False,
            mute_status_known=True,
            is_banned=False,
            ban_status_known=True,
            restriction_rights={"send_messages": False, "send_media": True},
        )
        self.mute_ok = mute_ok
        self.ban_ok = ban_ok
        self.delete_ok = delete_ok
        self.deleted: list[tuple[str, str]] = []
        self.actions: list[tuple[str, int | None]] = []

    async def is_native_admin(self, _group_id: str, _user_id: str) -> bool:
        return self.member.is_admin

    async def get_member(self, group_id: str, user_id: str, **_kwargs) -> Member | None:
        if str(group_id) != self.member.group_id or str(user_id) != self.member.user_id:
            return None
        return self.member

    async def delete_message(self, group_id: str, message_id: str) -> bool:
        self.deleted.append((str(group_id), str(message_id)))
        return self.delete_ok

    async def mute_member(
        self, group_id: str, user_id: str, *, duration_seconds: int | None
    ) -> ModerationResult:
        self.actions.append(("mute", duration_seconds))
        if self.mute_ok:
            self.member = replace(self.member, is_muted=True)
            return ModerationResult(True, verified=True)
        return ModerationResult(False, "mute_not_verified", verified=False)

    async def ban_member(self, group_id: str, user_id: str) -> ModerationResult:
        self.actions.append(("ban", None))
        if self.ban_ok:
            self.member = replace(self.member, is_banned=True)
            return ModerationResult(True, verified=True)
        return ModerationResult(False, "ban_not_verified", verified=False)


class Event:
    is_group = True
    out = False
    chat_id = "g"
    sender_id = "u"

    def __init__(
        self,
        message_id: int,
        text: str = "",
        *,
        media_kind: str | None = None,
        fwd_from: object | None = None,
        grouped_id: int | None = None,
        forwards: int | None = None,
    ) -> None:
        self.id = message_id
        self.raw_text = text
        self.sender = SimpleNamespace(
            id="u", first_name="Test", last_name="User", username="testuser"
        )
        document = (
            SimpleNamespace(mime_type="image/gif", attributes=[])
            if media_kind == "gif"
            else None
        )
        self.message = SimpleNamespace(
            id=message_id,
            media=SimpleNamespace(document=document) if document else None,
            document=document,
            fwd_from=fwd_from,
            grouped_id=grouped_id,
            forwards=forwards,
        )
        self.replies: list[str] = []

    async def reply(self, text: str) -> None:
        self.replies.append(text)


def _bridge(
    tmp_path,
    platform: ModerationPlatform,
    *,
    terms: tuple[str, ...] = (),
    legacy_admin_registry: LegacyAdminRegistry | None = None,
    check_phone_numbers_enabled: bool = True,
    check_usernames_enabled: bool = True,
    check_forwarded_enabled: bool = False,
    send_spam_warnings_enabled: bool = True,
    action_duration_seconds: int = 3600,
    warning_message_template: str | None = None,
    check_ad_names_enabled: bool = True,
):
    owner_file = tmp_path / "owner.json"
    owner_file.write_text('{"user_id":"owner"}', encoding="utf-8")
    store = BotStore(":memory:")
    store.set_group_state("g", "group_registry", {"active": True})
    access = AccessController(OwnerRegistry(owner_file), store, platform.is_native_admin)
    management = MemberManagementService(platform, store, access, bot_user_id="bot")
    group_admin = GroupAdminService(store, access, management)
    pipeline = MessagePipeline(WordFilter(terms), platform.delete_message)
    bridge = SPlusthonEventBridge(
        pipeline,
        management,
        group_admin=group_admin,
        platform=platform,
        legacy_admin_registry=legacy_admin_registry,
        check_phone_numbers_enabled=check_phone_numbers_enabled,
        check_usernames_enabled=check_usernames_enabled,
        check_forwarded_enabled=check_forwarded_enabled,
        check_ad_names_enabled=check_ad_names_enabled,
        send_spam_warnings_enabled=send_spam_warnings_enabled,
        action_duration_seconds=action_duration_seconds,
        warning_message_template=warning_message_template,
    )
    return store, management, group_admin, bridge


def test_custom_warning_template_keeps_platform_delete_status_accurate(tmp_path) -> None:
    async def scenario() -> None:
        platform = ModerationPlatform(delete_ok=False)
        store, _management, _group_admin, bridge = _bridge(
            tmp_path,
            platform,
            terms=("blocked",),
            warning_message_template="{username}: {reason} ({count}/{threshold})",
        )
        event = Event(1, "blocked")
        result = await bridge.on_new_message(event)
        assert result.delete_succeeded is False
        assert result.warning_count == 1
        assert len(event.replies) == 1
        assert "پیام حذف شد." not in event.replies[0]
        assert "تأیید نشد" in event.replies[0]
        assert event.replies[0].startswith("Test User:")
        await bridge.close(drain=True)
        store.close()

    asyncio.run(scenario())


def test_custom_warning_output_is_bounded_and_keeps_delete_status(tmp_path) -> None:
    async def scenario() -> None:
        platform = ModerationPlatform(delete_ok=False)
        store, _management, _group_admin, bridge = _bridge(
            tmp_path,
            platform,
            terms=("blocked",),
            warning_message_template="{username}" * 100,
        )
        event = Event(1, "blocked")
        event.sender.first_name = "😀" * 300
        result = await bridge.on_new_message(event)
        assert result.delete_succeeded is False
        warning = event.replies[-1]
        assert len(warning.encode("utf-16-le")) // 2 <= 3500
        assert warning.endswith("تخلف ثبت شد، اما حذف پیام از سروش تأیید نشد.")
        await bridge.close(drain=True)
        store.close()

    asyncio.run(scenario())


def test_warning_notice_toggle_keeps_warning_record_and_deletion(tmp_path) -> None:
    async def scenario() -> None:
        platform = ModerationPlatform()
        store, _management, _group_admin, bridge = _bridge(
            tmp_path,
            platform,
            terms=("blocked",),
            send_spam_warnings_enabled=False,
        )
        event = Event(1, "blocked")
        result = await bridge.on_new_message(event)
        assert result.delete_succeeded is True
        assert result.warning_count == 1
        assert store.get_warning_count("g", "u") == 1
        assert event.replies == []
        await bridge.close(drain=True)
        assert platform.deleted == [("g", "1")]
        store.close()

    asyncio.run(scenario())


def test_word_filter_records_automatic_warning_and_acts_only_after_verified_threshold(tmp_path) -> None:
    async def scenario() -> None:
        platform = ModerationPlatform()
        store, _management, _group_admin, bridge = _bridge(
            tmp_path,
            platform,
            terms=("blocked",),
            action_duration_seconds=7200,
        )
        store.set_group_state("g", "warning_threshold", 2)
        store.set_group_state("g", "punishment_mode", "mute")
        first = Event(1, "blocked")
        second = Event(2, "blocked")

        first_result = await bridge.on_new_message(first)
        second_result = await bridge.on_new_message(second)
        assert first_result.delete_succeeded is True
        assert first_result.warning_count == 1
        assert second_result.delete_succeeded is True
        assert second_result.warning_count == 2
        assert store.get_warning_count("g", "u") == 2

        await bridge.close(drain=True)
        assert platform.actions == [("mute", 7200)]
        assert platform.member.is_muted is True
        assert store.get_warning_count("g", "u") == 0
        assert store.get_group_stats("g")["deleted"] == 2
        assert store.mute_event_count("g", "u") == 1
        assert store.list_mute_events("g")
        history = store.list_group_history_events(
            "g", since=datetime.now(UTC) - timedelta(days=1)
        )
        warnings = [item for item in history if item["event_type"] == "warning"]
        assert len(warnings) == 2
        assert all(item["actor_id"] is None for item in warnings)
        assert all(item["actor_display"] == "سامانه" for item in warnings)
        assert len(first.replies) + len(second.replies) == 1
        store.close()

    asyncio.run(scenario())


def test_failed_delete_warning_does_not_claim_the_message_was_removed(tmp_path) -> None:
    async def scenario() -> None:
        platform = ModerationPlatform(delete_ok=False)
        store, _management, _group_admin, bridge = _bridge(
            tmp_path, platform, terms=("blocked",)
        )
        event = Event(1, "blocked")
        result = await bridge.on_new_message(event)
        assert result.delete_authorized is True
        assert result.delete_succeeded is False
        assert result.warning_count == 1
        await bridge.close(drain=True)

        assert len(platform.deleted) == 3
        assert len(event.replies) == 1
        assert "حذف پیام از سروش تأیید نشد" in event.replies[0]
        assert "پیام حذف شد" not in event.replies[0]
        assert store.get_warning_count("g", "u") == 1
        store.close()

    asyncio.run(scenario())


def test_unverified_threshold_action_keeps_warning_count_and_never_claims_success(tmp_path) -> None:
    async def scenario() -> None:
        platform = ModerationPlatform(mute_ok=False)
        store, _management, _group_admin, bridge = _bridge(
            tmp_path, platform, terms=("blocked",)
        )
        store.set_group_state("g", "warning_threshold", 1)
        store.set_group_state("g", "punishment_mode", "mute")
        event = Event(1, "blocked")
        await bridge.on_new_message(event)
        await bridge.close(drain=True)

        assert platform.member.is_muted is False
        assert store.get_warning_count("g", "u") == 1
        assert store.list_mute_events("g") == []
        assert not any("سکوت شد" in reply for reply in event.replies)
        store.close()

    asyncio.run(scenario())


def test_photo_only_message_is_not_misclassified_as_gif_spam(tmp_path) -> None:
    async def scenario() -> None:
        platform = ModerationPlatform()
        store, _management, _group_admin, bridge = _bridge(tmp_path, platform)
        event = Event(1)
        event.message.document = SimpleNamespace(mime_type="image/png", attributes=[])
        event.message.media = SimpleNamespace(document=event.message.document)

        def gif_marker() -> bool:
            return True

        event.message.gif = gif_marker
        result = await bridge.on_new_message(event)
        assert result.processed is False
        assert result.spam_decision is None
        await bridge.close(drain=True)
        assert platform.deleted == []
        assert platform.actions == []
        store.close()

    asyncio.run(scenario())


def test_gif_burst_deletes_all_ids_and_verifies_one_hour_mute(tmp_path) -> None:
    async def scenario() -> None:
        platform = ModerationPlatform()
        store, _management, _group_admin, bridge = _bridge(tmp_path, platform)
        results = []
        for message_id in range(1, 7):
            results.append(await bridge.on_new_message(Event(message_id, media_kind="gif")))
        assert all(not result.processed for result in results[:5])
        assert results[5].spam_decision.kind == "gif_burst"
        await bridge.close(drain=True)

        assert [message_id for _group, message_id in platform.deleted] == [
            str(value) for value in range(2, 7)
        ]
        assert platform.actions == [("mute", 3600)]
        assert platform.member.is_muted is True
        assert store.get_group_user_activity("g", "u")["gifs"] == 6
        assert store.get_group_stats("g")["deleted"] == 5
        assert store.mute_event_count("g", "u") == 1
        store.close()

    asyncio.run(scenario())


def test_failed_gif_mute_keeps_deleting_future_gifs_without_success_notice(tmp_path) -> None:
    async def scenario() -> None:
        platform = ModerationPlatform(mute_ok=False)
        store, _management, _group_admin, bridge = _bridge(tmp_path, platform)
        events = []
        for message_id in range(1, 7):
            event = Event(message_id, media_kind="gif")
            events.append(event)
            result = await bridge.on_new_message(event)
        assert result.spam_decision.action_required is True

        future = Event(7, media_kind="gif")
        events.append(future)
        followup = await bridge.on_new_message(future)
        assert followup.spam_decision is not None
        assert followup.spam_decision.action_required is False
        await bridge.close(drain=True)

        assert platform.actions == [("mute", 3600)]
        assert platform.member.is_muted is False
        assert [message_id for _group, message_id in platform.deleted] == [
            str(value) for value in range(2, 8)
        ]
        assert not any("سکوت شد" in reply for event in events for reply in event.replies)
        store.close()

    asyncio.run(scenario())


def test_non_gif_message_breaks_consecutive_gif_threshold(tmp_path) -> None:
    async def scenario() -> None:
        platform = ModerationPlatform()
        store, _management, _group_admin, bridge = _bridge(tmp_path, platform)
        for message_id in range(1, 6):
            result = await bridge.on_new_message(Event(message_id, media_kind="gif"))
            assert result.spam_decision is None
        ordinary = await bridge.on_new_message(Event(6, "پیام معمولی بین گیف‌ها"))
        assert ordinary.spam_decision is None

        results = []
        for message_id in range(7, 13):
            results.append(
                await bridge.on_new_message(Event(message_id, media_kind="gif"))
            )
        assert all(result.spam_decision is None for result in results[:5])
        assert results[-1].spam_decision.kind == "gif_burst"
        await bridge.close(drain=True)

        assert [message_id for _group, message_id in platform.deleted] == [
            str(value) for value in range(8, 13)
        ]
        assert platform.actions == [("mute", 3600)]
        assert store.get_group_stats("g")["deleted"] == 5
        store.close()

    asyncio.run(scenario())


def test_forward_detection_defaults_off_and_requires_explicit_configuration(tmp_path) -> None:
    async def scenario() -> None:
        platform = ModerationPlatform()
        store, _management, _group_admin, bridge = _bridge(tmp_path, platform)
        result = await bridge.on_new_message(
            Event(1, "ordinary forwarded text", fwd_from=object())
        )
        assert result.spam_decision is None
        await bridge.close(drain=True)
        assert platform.deleted == []
        assert platform.actions == []
        store.close()

    asyncio.run(scenario())


def test_forward_header_album_cleanup_and_third_forward_mute(tmp_path) -> None:
    async def scenario() -> None:
        platform = ModerationPlatform()
        store, _management, _group_admin, bridge = _bridge(
            tmp_path, platform, check_forwarded_enabled=True
        )

        view_only = await bridge.on_new_message(Event(1, forwards=500))
        assert view_only.processed is False
        assert platform.deleted == []

        assert (await bridge.on_new_message(Event(2, grouped_id=77))).processed is False
        first_forward = await bridge.on_new_message(
            Event(3, fwd_from=object(), grouped_id=77)
        )
        assert first_forward.spam_decision.kind == "forward"
        second_album_item = await bridge.on_new_message(Event(4, grouped_id=77))
        assert second_album_item.spam_decision.kind == "forward"
        third_forward = await bridge.on_new_message(Event(5, fwd_from=object()))
        assert third_forward.spam_decision.action_required is True
        await bridge.close(drain=True)

        deleted_ids = [message_id for _group, message_id in platform.deleted]
        assert sorted(deleted_ids) == ["2", "3", "4", "5"]
        assert platform.actions == [("mute", 24 * 60 * 60)]
        assert platform.member.is_muted is True
        assert store.get_group_stats("g")["deleted"] == 4
        assert store.mute_event_count("g", "u") == 1
        store.close()

    asyncio.run(scenario())


def test_forward_header_compatibility_fields_are_detected_but_view_count_is_not(tmp_path) -> None:
    async def scenario() -> None:
        platform = ModerationPlatform()
        store, _management, _group_admin, bridge = _bridge(
            tmp_path, platform, check_forwarded_enabled=True
        )
        zero_date = Event(1, forwards=900)
        zero_date.message.forward_date = 0
        assert (await bridge.on_new_message(zero_date)).spam_decision is None

        compatibility = Event(2, forwards=900)
        compatibility.message.forward_from_chat = 12345
        result = await bridge.on_new_message(compatibility)
        assert result.spam_decision.kind == "forward"
        await bridge.close(drain=True)
        assert platform.deleted == [("g", "2")]
        assert platform.actions == []
        store.close()

    asyncio.run(scenario())


def test_forward_management_commands_bypass_but_public_help_does_not(tmp_path) -> None:
    async def scenario() -> None:
        platform = ModerationPlatform()
        store, _management, _group_admin, bridge = _bridge(
            tmp_path, platform, check_forwarded_enabled=True
        )
        management = Event(1, "قفل", fwd_from=object())
        management_result = await bridge.on_new_message(management)
        assert management_result.spam_decision is None

        prefixed_admin = Event(2, "!addword example", fwd_from=object())
        prefixed_admin_result = await bridge.on_new_message(prefixed_admin)
        assert prefixed_admin_result.spam_decision is None

        public_help = Event(3, "راهنما", fwd_from=object())
        public_result = await bridge.on_new_message(public_help)
        assert public_result.spam_decision is not None
        assert public_result.spam_decision.kind == "forward"
        prefixed_help = Event(4, "!help", fwd_from=object())
        prefixed_result = await bridge.on_new_message(prefixed_help)
        assert prefixed_result.spam_decision is not None
        assert prefixed_result.spam_decision.kind == "forward"
        await bridge.close(drain=True)

        assert platform.deleted == [("g", "3"), ("g", "4")]
        assert platform.actions == []
        store.close()

    asyncio.run(scenario())


def test_filter_hit_with_long_repetitive_payload_verifies_direct_ban(tmp_path) -> None:
    async def scenario() -> None:
        platform = ModerationPlatform()
        store, _management, _group_admin, bridge = _bridge(
            tmp_path, platform, terms=("blocked",)
        )
        unique_words = (
            "blocked alpha bravo charlie delta echo foxtrot golf hotel india "
            "juliet kilo lima mike november oscar papa quebec romeo"
        )
        event = Event(1, (unique_words + " ") * 3)
        result = await bridge.on_new_message(event)
        assert result.spam_decision is not None
        assert result.spam_decision.kind == "long_repeat"
        assert result.warning_count is None
        assert result.delete_succeeded is True
        await bridge.close(drain=True)

        assert platform.actions == [("ban", None)]
        assert platform.member.is_banned is True
        assert store.get_warning_count("g", "u") == 0
        assert any("از گروه اخراج شد" in reply for reply in event.replies)
        assert store.list_bot_bans("g")[0]["source"] == "automatic_spam"
        store.close()

    asyncio.run(scenario())


def test_repeated_message_creates_one_ban_incident_without_warning_count(tmp_path) -> None:
    async def scenario() -> None:
        platform = ModerationPlatform()
        store, _management, _group_admin, bridge = _bridge(tmp_path, platform)
        results = []
        for message_id in range(1, 6):
            results.append(
                await bridge.on_new_message(
                    Event(message_id, "این پیام تکراری را برای آزمایش می‌فرستم")
                )
            )
        assert results[2].spam_decision.kind == "repeat"
        assert results[3].spam_decision.kind == "incident"
        assert results[4].spam_decision.kind == "incident"
        await bridge.close(drain=True)

        assert platform.actions == [("ban", None)]
        assert platform.member.is_banned is True
        assert [message_id for _group, message_id in platform.deleted] == [
            str(value) for value in range(1, 6)
        ]
        assert store.get_warning_count("g", "u") == 0
        assert store.list_bot_bans("g")[0]["source"] == "automatic_spam"
        store.close()

    asyncio.run(scenario())


def test_unverified_spam_ban_never_sends_a_ban_success_notice(tmp_path) -> None:
    async def scenario() -> None:
        platform = ModerationPlatform(ban_ok=False)
        store, _management, _group_admin, bridge = _bridge(tmp_path, platform)
        events = []
        for message_id in range(1, 5):
            event = Event(
                message_id, "این پیام تکراری را برای آزمایش می‌فرستم"
            )
            events.append(event)
            await bridge.on_new_message(event)
        await bridge.close(drain=True)

        assert platform.actions == [("ban", None)]
        assert platform.member.is_banned is False
        assert not any("از گروه اخراج شد" in reply for event in events for reply in event.replies)
        store.close()

    asyncio.run(scenario())


def test_registered_admin_messages_do_not_seed_spam_state_for_later_member(tmp_path) -> None:
    async def scenario() -> None:
        platform = ModerationPlatform()
        store, _management, _group_admin, bridge = _bridge(tmp_path, platform)
        store.add_group_command_member("g", "registered_admin", "u")
        for message_id in range(1, 5):
            result = await bridge.on_new_message(
                Event(message_id, "این پیام تکراری را برای آزمایش می‌فرستم")
            )
            assert result.spam_decision is None
        assert platform.actions == []

        store.remove_group_command_member("g", "registered_admin", "u")
        for message_id in range(5, 7):
            result = await bridge.on_new_message(
                Event(message_id, "این پیام تکراری را برای آزمایش می‌فرستم")
            )
            assert result.spam_decision is None
        repeated = await bridge.on_new_message(
            Event(7, "این پیام تکراری را برای آزمایش می‌فرستم")
        )
        assert repeated.spam_decision.kind == "repeat"
        followup = await bridge.on_new_message(
            Event(8, "این پیام تکراری را برای آزمایش می‌فرستم")
        )
        assert followup.spam_decision.kind == "incident"
        await bridge.close(drain=True)
        assert platform.actions == [("ban", None)]
        store.close()

    asyncio.run(scenario())


def test_native_admin_messages_do_not_seed_spam_state_for_later_member(tmp_path) -> None:
    async def scenario() -> None:
        platform = ModerationPlatform()
        platform.member = replace(platform.member, is_admin=True)
        store, _management, _group_admin, bridge = _bridge(tmp_path, platform)

        for message_id in range(1, 5):
            result = await bridge.on_new_message(
                Event(message_id, "این پیام تکراری را برای آزمایش می‌فرستم")
            )
            assert result.spam_decision is None
        assert bridge.spam_guard.tracked_sender_count == 0
        assert platform.actions == []

        platform.member = replace(platform.member, is_admin=False)
        bridge._native_admin_cache.clear()
        for message_id in range(5, 7):
            result = await bridge.on_new_message(
                Event(message_id, "این پیام تکراری را برای آزمایش می‌فرستم")
            )
            assert result.spam_decision is None
        final = await bridge.on_new_message(
            Event(7, "این پیام تکراری را برای آزمایش می‌فرستم")
        )
        assert final.spam_decision.kind == "repeat"
        followup = await bridge.on_new_message(
            Event(8, "این پیام تکراری را برای آزمایش می‌فرستم")
        )
        assert followup.spam_decision.kind == "incident"
        await bridge.close(drain=True)
        assert platform.actions == [("ban", None)]
        store.close()

    asyncio.run(scenario())


def test_unknown_native_admin_state_fails_closed_for_spam_detection(tmp_path) -> None:
    async def scenario() -> None:
        platform = ModerationPlatform()
        platform.member = replace(platform.member, admin_status_known=False)
        store, _management, _group_admin, bridge = _bridge(tmp_path, platform)
        for message_id in range(1, 6):
            result = await bridge.on_new_message(
                Event(message_id, "این پیام تکراری را برای آزمایش می‌فرستم")
            )
            assert result.spam_decision is None
        assert bridge.spam_guard.tracked_sender_count == 0
        await bridge.close(drain=True)
        assert platform.actions == []
        store.close()

    asyncio.run(scenario())


def test_legacy_admin_stays_protected_if_local_promotion_fails(tmp_path) -> None:
    async def scenario() -> None:
        platform = ModerationPlatform()
        admins_file = tmp_path / "admins.json"
        admins_file.write_text('{"g":["@testuser"]}', encoding="utf-8")
        store, _management, _group_admin, bridge = _bridge(
            tmp_path,
            platform,
            legacy_admin_registry=LegacyAdminRegistry(admins_file),
        )

        def fail_promotion(*_args, **_kwargs):
            raise OSError("test promotion failure")

        store.add_group_command_member = fail_promotion
        for message_id in range(1, 6):
            result = await bridge.on_new_message(
                Event(message_id, "این پیام تکراری را برای آزمایش می‌فرستم")
            )
            assert result.spam_decision is None
        assert bridge.spam_guard.tracked_sender_count == 0
        assert platform.actions == []
        await bridge.close(drain=True)
        store.close()

    asyncio.run(scenario())


def test_public_username_records_a_warning_after_verified_deletion(tmp_path) -> None:
    async def scenario() -> None:
        platform = ModerationPlatform()
        store, _management, _group_admin, bridge = _bridge(tmp_path, platform)
        event = Event(1, "برای هماهنگی به @public_user پیام بده")
        result = await bridge.on_new_message(event)
        assert result.spam_decision.kind == "public_username"
        assert platform.deleted == [("g", "1")]
        assert store.get_warning_count("g", "u") == 1
        assert store.get_group_stats("g")["deleted"] == 1
        assert len(event.replies) == 1
        assert "پیام حذف شد" in event.replies[0]
        assert "اخطار: 1/5" in event.replies[0]
        await bridge.close(drain=True)
        assert platform.actions == []
        store.close()

    asyncio.run(scenario())


def test_site_policy_link_records_warning_after_verified_deletion(tmp_path) -> None:
    async def scenario() -> None:
        platform = ModerationPlatform()
        store, _management, _group_admin, bridge = _bridge(tmp_path, platform)
        event = Event(1, "اطلاعات بیشتر https://newsite.example/path")
        result = await bridge.on_new_message(event)

        assert result.spam_decision is not None
        assert result.spam_decision.kind == "site_link"
        assert platform.deleted == [("g", "1")]
        assert store.get_warning_count("g", "u") == 1
        assert store.get_group_stats("g")["deleted"] == 1
        assert len(event.replies) == 1
        assert "پیام حذف شد" in event.replies[0]
        assert "اخطار: 1/5" in event.replies[0]

        await bridge.close(drain=True)
        assert platform.actions == []
        store.close()

    asyncio.run(scenario())


def test_mixed_two_text_run_bans_after_three_normalized_copies(tmp_path) -> None:
    async def scenario() -> None:
        platform = ModerationPlatform()
        store, _management, _group_admin, bridge = _bridge(tmp_path, platform)
        texts = (
            "این پیام معمولی را برای آزمایش می‌فرستم",
            "یک گفتگوی کوتاه و عادی در گروه",
        )
        results = []
        events = []
        for message_id in range(1, 6):
            event = Event(message_id, texts[(message_id - 1) % 2])
            events.append(event)
            results.append(await bridge.on_new_message(event))
        assert results[4].spam_decision.kind == "repeat"
        assert results[4].spam_decision.message_ids == ("1", "3", "5")
        await bridge.close(drain=True)

        assert [message_id for _group, message_id in platform.deleted] == [
            "1", "3", "5"
        ]
        assert platform.actions == [("ban", None)]
        assert platform.member.is_banned is True
        assert "از گروه اخراج شد" in events[-1].replies[0]
        assert store.get_group_stats("g")["deleted"] == 3
        store.close()

    asyncio.run(scenario())


def test_sender_name_uses_only_approved_global_terms_and_verifies_action(tmp_path) -> None:
    async def scenario() -> None:
        platform = ModerationPlatform()
        store, _management, _group_admin, bridge = _bridge(
            tmp_path, platform, terms=("blocked",)
        )
        event = Event(1, "سلام")
        event.sender.first_name = "blocked"

        result = await bridge.on_new_message(event)
        assert result.processed is True
        assert result.spam_decision is not None
        assert result.spam_decision.kind == "sender_name"
        assert platform.deleted == []
        await bridge.close(drain=True)

        assert platform.actions == [("ban", None)]
        assert platform.member.is_banned is True
        assert len(event.replies) == 1
        assert "از گروه اخراج شد" in event.replies[0]
        assert store.get_group_stats("g")["kicked"] == 1
        store.close()

    asyncio.run(scenario())


def test_sender_name_mute_notice_reports_nonstandard_duration(tmp_path) -> None:
    async def scenario() -> None:
        platform = ModerationPlatform()
        store, _management, _group_admin, bridge = _bridge(
            tmp_path, platform, terms=("blocked",), action_duration_seconds=7200
        )
        store.set_group_state("g", "punishment_mode", "mute")
        event = Event(1, "سلام")
        event.sender.first_name = "blocked"

        result = await bridge.on_new_message(event)
        assert result.spam_decision is not None
        await bridge.close(drain=True)

        assert platform.actions == [("mute", 7200)]
        assert len(event.replies) == 1
        assert "به مدت ۲ ساعت سکوت شد" in event.replies[0]
        assert "سکوت دائم شد" not in event.replies[0]
        store.close()

    asyncio.run(scenario())


def test_sender_name_action_is_suppressed_for_commands_and_native_admins(tmp_path) -> None:
    async def scenario() -> None:
        platform = ModerationPlatform()
        store, _management, _group_admin, bridge = _bridge(
            tmp_path, platform, terms=("blocked",)
        )
        admin_event = Event(1, "سلام")
        admin_event.sender.first_name = "blocked"
        platform.member = replace(platform.member, is_admin=True)
        admin_result = await bridge.on_new_message(admin_event)
        assert admin_result.spam_decision is None

        platform.member = replace(platform.member, is_admin=False)
        command_event = Event(2, "!unhandled")
        command_event.sender.first_name = "blocked"
        command_result = await bridge.on_new_message(command_event)
        assert command_result.spam_decision is None
        await bridge.close(drain=True)

        assert platform.actions == []
        store.close()

    asyncio.run(scenario())


def test_sender_name_failed_action_has_no_success_notice_and_is_debounced(tmp_path) -> None:
    async def scenario() -> None:
        platform = ModerationPlatform(ban_ok=False)
        store, _management, _group_admin, bridge = _bridge(
            tmp_path, platform, terms=("blocked",)
        )
        first = Event(1, "سلام")
        first.sender.username = "blocked_user"
        second = Event(2, "روز بخیر")
        second.sender.username = "blocked_user"

        first_result = await bridge.on_new_message(first)
        second_result = await bridge.on_new_message(second)
        assert first_result.spam_decision is not None
        assert second_result.processed is True
        await bridge.close(drain=True)

        assert platform.actions == [("ban", None)]
        assert platform.member.is_banned is False
        assert first.replies == []
        assert second.replies == []
        assert store.get_group_stats("g")["kicked"] == 0
        store.close()

    asyncio.run(scenario())


def test_sender_name_check_can_be_disabled_without_changing_global_list(tmp_path) -> None:
    async def scenario() -> None:
        platform = ModerationPlatform()
        store, _management, _group_admin, bridge = _bridge(
            tmp_path,
            platform,
            terms=("blocked",),
            check_ad_names_enabled=False,
        )
        event = Event(1, "سلام")
        event.sender.first_name = "blocked"

        result = await bridge.on_new_message(event)
        assert result.spam_decision is None
        await bridge.close(drain=True)

        assert platform.actions == []
        assert bridge.pipeline.word_filter.global_terms == ("blocked",)
        store.close()

    asyncio.run(scenario())
