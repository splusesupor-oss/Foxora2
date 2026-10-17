from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from foxbot import soroush_events
from foxbot.anti_spam import SpamDecision
from foxbot.domain import Member, MemberPage
from foxbot.filters import WordFilter
from foxbot.legacy_admins import LegacyAdminRegistry
from foxbot.management import MemberManagementService
from foxbot.message_pipeline import MessagePipeline
from foxbot.rpc_queue import SerialRpcQueue
from foxbot.security import AccessController, OwnerRegistry
from foxbot.soroush_adapter import SPlusthonAdapter
from foxbot.soroush_events import SPlusthonEventBridge
from foxbot.storage import BotStore
from foxbot.wordlists import GroupWordService


def test_public_level_and_reserved_group_command_skip_spam_classification() -> None:
    assert soroush_events._is_likely_command("سطح گروه")
    assert soroush_events._is_likely_command("حذف گروه")


def test_forward_management_gate_recognizes_prefixed_admin_aliases() -> None:
    for command in (
        "!addword example", "!remword example", "!stats", "!reset",
        "!lock", "!unlock", "!timelock 5", "!photo image.png",
        "!title Group", "!راهنما", "/فیلترها", "/فیلتر example", "/رفع example",
    ):
        assert soroush_events._is_forward_management_command(command)
    for command in ("راهنما", "!help", "/help", "ordinary forwarded text"):
        assert not soroush_events._is_forward_management_command(command)


def test_formatted_reply_retries_plain_text_after_platform_rejection() -> None:
    async def scenario() -> None:
        queue = SerialRpcQueue(min_interval_seconds=0)

        async def delete_message(_group_id: str, _message_id: str) -> bool:
            return True

        bridge = SPlusthonEventBridge(
            MessagePipeline(WordFilter(), delete_message),
            None,
            rpc_queue=queue,
        )

        class RejectFormatting:
            chat_id = "group"

            def __init__(self) -> None:
                self.calls = []

            async def reply(self, text, **kwargs):
                self.calls.append((text, kwargs))
                if "formatting_entities" in kwargs:
                    raise TypeError("formatting entities unsupported")
                return SimpleNamespace(id=1)

        event = RejectFormatting()
        result = await bridge._reply(
            event, "wallet", formatting_entities=[object()]
        )
        assert result.id == 1
        assert len(event.calls) == 2
        assert "formatting_entities" in event.calls[0][1]
        assert event.calls[1] == ("wallet", {})
        await queue.close()

    asyncio.run(scenario())


def test_delete_spam_setting_controls_history_but_keeps_current_trigger() -> None:
    decision = SpamDecision(
        kind="campaign",
        reason="test",
        message_ids=("old-1", "old-2", "current"),
        policy="ban",
    )
    assert soroush_events._spam_target_ids(
        decision, "current", delete_history_enabled=True
    ) == ("old-1", "old-2", "current")
    assert soroush_events._spam_target_ids(
        decision, "current", delete_history_enabled=False
    ) == ("current",)


class FakeClient:
    def __init__(self):
        self.handlers = []
        self.deleted = []

    def add_event_handler(self, callback, builder):
        self.handlers.append((callback, builder))

    async def delete_messages(self, group_id, message_ids, *, revoke=True):
        self.deleted.append((group_id, tuple(message_ids), revoke))
        return [SimpleNamespace(pts=1, pts_count=1)]

    async def get_me(self):
        self.health_calls = getattr(self, "health_calls", 0) + 1
        return SimpleNamespace(id=1)


class FakeEvents:
    class NewMessage:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class MessageEdited:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class ChatAction:
        def __init__(self, **kwargs):
            self.kwargs = kwargs


class FakePlatform:
    async def is_native_admin(self, group_id: str, user_id: str) -> bool:
        return False

    async def list_members_page(self, *args, **kwargs):
        raise NotImplementedError

    async def list_active_mutes_page(self, *args, **kwargs):
        raise NotImplementedError

    async def get_member(self, *args, **kwargs):
        raise NotImplementedError

    async def mute_member(self, *args, **kwargs):
        raise NotImplementedError

    async def unmute_member(self, *args, **kwargs):
        raise NotImplementedError

    async def kick_member(self, *args, **kwargs):
        raise NotImplementedError

    async def delete_message(self, group_id, message_id):
        return False


def test_soroush_event_bridge_registers_filters_and_kick_history(tmp_path) -> None:
    async def scenario() -> None:
        owner_file = tmp_path / "owner.json"
        owner_file.write_text('{"user_id":"owner"}', encoding="utf-8")
        store = BotStore(":memory:")
        platform = FakePlatform()
        access = AccessController(OwnerRegistry(owner_file), store, platform.is_native_admin)
        management = MemberManagementService(platform, store, access, bot_user_id="bot")
        client = FakeClient()
        queue = SerialRpcQueue()
        adapter = SPlusthonAdapter(client, queue)
        word_filter = WordFilter(("blocked",))
        pipeline = MessagePipeline(word_filter, adapter.delete_message)
        bridge = SPlusthonEventBridge(pipeline, management)
        bridge.register(client, events_module=FakeEvents)
        assert len(client.handlers) == 3
        assert client.handlers[0][1].kwargs == {"incoming": True}
        assert client.handlers[1][1].kwargs == {"incoming": True}

        event = SimpleNamespace(
            is_group=True,
            out=False,
            chat_id=-1001,
            id=12,
            sender_id=44,
            raw_text="blocked message",
        )
        result = await bridge.on_new_message(event)
        assert result.violation is not None
        assert result.delete_succeeded is True
        assert client.deleted == [("-1001", (12,), True)]
        assert (await bridge.on_new_message(event)).processed is False

        edited = SimpleNamespace(
            is_group=True,
            out=False,
            chat_id=-1001,
            id=13,
            sender_id=44,
            raw_text="blocked after edit",
        )
        assert (await bridge.on_message_edited(edited)).delete_succeeded is True

        kick = SimpleNamespace(
            user_kicked=True,
            is_group=True,
            chat_id=-1001,
            user_id=55,
            kicked_by=SimpleNamespace(id=66),
            action_message=SimpleNamespace(id=77),
            original_update=None,
        )
        assert await bridge.on_chat_action(kick) is True
        assert await bridge.on_chat_action(kick) is False
        events = store.list_kick_events(-1001)
        assert len(events) == 1
        assert events[0]["user_id"] == "55"
        assert events[0]["result"] == "success"
        assert events[0]["source"] == "platform_event"

        voluntary_leave = SimpleNamespace(
            user_kicked=False, is_group=True, chat_id=55, user_id=55
        )
        assert await bridge.on_chat_action(voluntary_leave) is False
        await queue.close()
        store.close()

    asyncio.run(scenario())


def test_health_report_uses_serial_rpc_queue_for_platform_probe() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        platform = FakePlatform()
        queue = SerialRpcQueue()
        client = FakeClient()
        adapter = SPlusthonAdapter(client, queue)
        bridge = SPlusthonEventBridge(
            MessagePipeline(WordFilter(()), platform.delete_message),
            None,
            rpc_queue=queue,
            platform=adapter,
        )

        report = await bridge._health_report(SimpleNamespace(chat_id="g"))
        assert "🩺 وضعیت ربات" in report
        assert "🌐 آزمون RPC سروش:" in report
        assert client.health_calls == 1
        assert queue.metrics["completed"] == 1
        await queue.close()
        store.close()

    asyncio.run(scenario())


def test_media_only_messages_contribute_to_group_and_user_activity(tmp_path) -> None:
    async def scenario() -> None:
        owner_file = tmp_path / "owner.json"
        owner_file.write_text('{"user_id":"owner"}', encoding="utf-8")
        store = BotStore(":memory:")
        platform = FakePlatform()
        access = AccessController(OwnerRegistry(owner_file), store, platform.is_native_admin)
        management = MemberManagementService(platform, store, access)
        from foxbot.group_admin import GroupAdminService

        group_admin = GroupAdminService(store, access, management)
        store.set_group_state("g", "group_registry", {"active": True})
        bridge = SPlusthonEventBridge(
            MessagePipeline(WordFilter(()), platform.delete_message),
            management,
            group_admin=group_admin,
        )
        gif = SimpleNamespace(
            is_group=True,
            out=False,
            chat_id="g",
            id=20,
            sender_id="user",
            raw_text="",
            message=SimpleNamespace(
                document=SimpleNamespace(mime_type="image/gif", attributes=[]),
                media=None,
            ),
            sender=SimpleNamespace(first_name="Gif", last_name="User", username="gifuser"),
        )
        video = SimpleNamespace(
            is_group=True,
            out=False,
            chat_id="g",
            id=21,
            sender_id="user",
            raw_text="video caption",
            message=SimpleNamespace(
                document=SimpleNamespace(mime_type="video/mp4", attributes=[]),
                media=None,
            ),
            sender=SimpleNamespace(first_name="Gif", last_name="User", username="gifuser"),
        )
        assert (await bridge.on_new_message(gif)).processed is False
        assert (await bridge.on_new_message(video)).processed is True
        assert store.get_group_stats("g")["messages"] == 2
        assert store.get_group_user_stats("g", "user")["messages"] == 2
        activity = store.get_group_user_activity("g", "user")
        assert activity["gifs"] == 1 and activity["videos"] == 1
        store.close()

    asyncio.run(scenario())


def test_main_help_aliases_precede_admin_prefixed_help_alias(tmp_path) -> None:
    async def scenario() -> None:
        owner_file = tmp_path / "owner.json"
        owner_file.write_text('{"user_id":"owner"}', encoding="utf-8")
        store = BotStore(":memory:")
        platform = FakePlatform()
        access = AccessController(OwnerRegistry(owner_file), store, platform.is_native_admin)
        management = MemberManagementService(platform, store, access)
        from foxbot.group_admin import GroupAdminService

        group_admin = GroupAdminService(store, access, management)
        store.set_group_state("g", "group_registry", {"active": True})
        bridge = SPlusthonEventBridge(
            MessagePipeline(WordFilter(()), platform.delete_message),
            management,
            group_admin=group_admin,
        )

        class Event:
            is_group = True
            out = False
            chat_id = "g"
            sender_id = "member"

            def __init__(self, message_id: int, raw_text: str) -> None:
                self.id = message_id
                self.raw_text = raw_text
                self.replies: list[str] = []

            async def reply(self, text: str) -> None:
                self.replies.append(text)

        for message_id, alias in enumerate(("!help", "/help"), start=1):
            event = Event(message_id, alias)
            result = await bridge.on_new_message(event)
            assert result.processed is False
            assert event.replies[-1].startswith("🦊 راهنمای روباه")
            assert "دستورات مدیر" not in event.replies[-1]

        start = Event(3, "ثبت قوانین")
        start.sender_id = "owner"
        start.sender = SimpleNamespace(id="owner", first_name="Owner", username="owner")
        assert (await bridge.on_new_message(start)).processed is False
        assert await group_admin.pending_workflow("g", "owner") is not None

        pending_help = Event(4, "!help")
        pending_help.sender_id = "owner"
        pending_help.sender = SimpleNamespace(id="owner", first_name="Owner", username="owner")
        assert (await bridge.on_new_message(pending_help)).processed is False
        assert group_admin.get_law("g") == "!help"
        assert await group_admin.pending_workflow("g", "owner") is None
        assert pending_help.replies[-1] == "📜 قوانین گروه ثبت شد ✅"

        store.close()

    asyncio.run(scenario())


def test_inactive_group_ignores_normal_commands_until_owner_activates_it(tmp_path) -> None:
    async def scenario() -> None:
        owner_file = tmp_path / "owner.json"
        owner_file.write_text('{"user_id":"owner"}', encoding="utf-8")
        store = BotStore(":memory:")
        platform = FakePlatform()
        access = AccessController(OwnerRegistry(owner_file), store, platform.is_native_admin)
        management = MemberManagementService(platform, store, access)
        from foxbot.group_admin import GroupAdminService

        group_admin = GroupAdminService(store, access, management)
        bridge = SPlusthonEventBridge(
            MessagePipeline(WordFilter(("blocked",)), platform.delete_message),
            management,
            group_admin=group_admin,
        )

        class Event:
            is_group = True
            out = False
            chat_id = "g"
            chat = SimpleNamespace(title="Test group")

            def __init__(self, message_id: int, sender_id: str, raw_text: str) -> None:
                self.id = message_id
                self.sender_id = sender_id
                self.raw_text = raw_text
                self.replies: list[str] = []

            async def reply(self, text: str) -> None:
                self.replies.append(text)

        ignored = Event(1, "member", "!help")
        assert (await bridge.on_new_message(ignored)).processed is False
        assert ignored.replies == []
        assert store.get_group_stats("g")["messages"] == 0

        activate = Event(2, "owner", "فعال")
        assert (await bridge.on_new_message(activate)).processed is False
        assert store.get_group_state("g", "group_registry")["active"] is True
        assert "روباه در گروه «Test group» فعال سازی شد" in activate.replies[-1]

        help_event = Event(3, "member", "!help")
        assert (await bridge.on_new_message(help_event)).processed is False
        assert help_event.replies[-1].startswith("🦊 راهنمای روباه")
        store.close()

    asyncio.run(scenario())


def test_event_bridge_passes_runtime_word_filter_to_public_route(monkeypatch, tmp_path) -> None:
    async def scenario() -> None:
        owner_file = tmp_path / "owner.json"
        owner_file.write_text('{"user_id":"owner"}', encoding="utf-8")
        store = BotStore(":memory:")
        platform = FakePlatform()
        access = AccessController(OwnerRegistry(owner_file), store, platform.is_native_admin)
        management = MemberManagementService(platform, store, access)
        from foxbot.group_admin import GroupAdminService

        group_admin = GroupAdminService(store, access, management)
        store.set_group_state("g", "group_registry", {"active": True})
        word_filter = WordFilter(("runtime-only",))
        bridge = SPlusthonEventBridge(
            MessagePipeline(word_filter, platform.delete_message),
            management,
            group_admin=group_admin,
        )
        seen = []

        async def public_route(*_args, **kwargs):
            seen.append(kwargs.get("word_filter"))
            return True

        monkeypatch.setattr(soroush_events, "handle_public_command", public_route)
        event = SimpleNamespace(
            is_group=True,
            out=False,
            chat_id="g",
            id=90,
            sender_id="member",
            raw_text="سلام",
            sender=SimpleNamespace(id="member", first_name="Test", username="test"),
            chat=SimpleNamespace(title="Test group"),
            replies=[],
        )
        result = await bridge.on_new_message(event)
        assert result.processed is False
        assert len(seen) == 1 and seen[0] is word_filter
        store.close()

    asyncio.run(scenario())


def test_moderation_search_prefixes_are_not_captured_by_web_search(monkeypatch, tmp_path) -> None:
    async def scenario() -> None:
        owner_file = tmp_path / "owner.json"
        owner_file.write_text('{"user_id":"owner"}', encoding="utf-8")
        store = BotStore(":memory:")
        member = Member(
            group_id="g",
            user_id="target",
            display_name="Rose",
            bio="bio-marker",
            bio_available=True,
            membership_status_known=True,
        )

        class SearchPlatform(FakePlatform):
            async def list_members_page(
                self, _group_id: str, offset: int, _limit: int, *, include_bio: bool = False
            ) -> MemberPage:
                assert include_bio in {False, True}
                if offset == 0:
                    return MemberPage((member,), None, total_count=1, raw_count=1)
                return MemberPage((), None, total_count=1, raw_count=0)

        platform = SearchPlatform()
        store.set_group_owner("g", "owner")
        store.set_group_state("g", "group_registry", {"active": True})
        access = AccessController(OwnerRegistry(owner_file), store, platform.is_native_admin)
        management = MemberManagementService(platform, store, access)
        from foxbot import public_commands
        from foxbot.group_admin import GroupAdminService

        group_admin = GroupAdminService(store, access, management)
        bridge = SPlusthonEventBridge(
            MessagePipeline(WordFilter(()), platform.delete_message),
            management,
            group_admin=group_admin,
        )

        def unexpected_web_search(_query: str) -> str:
            raise AssertionError("moderation search was routed to public web search")

        monkeypatch.setattr(public_commands, "_search_web_sync", unexpected_web_search)

        def unexpected_factual_search(*_args, **_kwargs) -> bool:
            raise AssertionError("moderation search was routed to factual reply search")

        monkeypatch.setattr(
            public_commands, "_handle_factual_reply_search", unexpected_factual_search
        )

        class Event:
            is_group = True
            out = False
            chat_id = "g"
            sender_id = "owner"
            sender = SimpleNamespace(id="owner", first_name="Owner", username="owner")

            def __init__(self, message_id: int, raw_text: str) -> None:
                self.id = message_id
                self.raw_text = raw_text
                self.replies: list[str] = []

            async def reply(self, text: str) -> None:
                self.replies.append(text)

        for message_id, query in enumerate(
            ("جستجو عضو Rose", "جستجو بیو bio-marker"), start=91
        ):
            event = Event(message_id, query)
            result = await bridge.on_new_message(event)
            assert result.processed is False
            assert "Rose — target" in event.replies[-1]

        store.close()

    asyncio.run(scenario())


def test_pre_workflow_public_routes_precede_pending_group_admin_workflow(tmp_path) -> None:
    async def scenario() -> None:
        owner_file = tmp_path / "owner.json"
        owner_file.write_text('{"user_id":"global-owner"}', encoding="utf-8")
        store = BotStore(":memory:")
        store.set_group_owner("g", "owner")
        store.set_group_state("g", "group_registry", {"active": True})
        platform = FakePlatform()
        access = AccessController(OwnerRegistry(owner_file), store, platform.is_native_admin)
        management = MemberManagementService(platform, store, access)
        from foxbot.group_admin import GroupAdminService

        group_admin = GroupAdminService(store, access, management)
        bridge = SPlusthonEventBridge(
            MessagePipeline(WordFilter(()), platform.delete_message),
            management,
            group_admin=group_admin,
        )

        class Event:
            is_group = True
            out = False
            chat_id = "g"
            sender_id = "owner"
            sender = SimpleNamespace(id="owner", first_name="Owner", username="owner")

            def __init__(self, message_id: int, raw_text: str) -> None:
                self.id = message_id
                self.raw_text = raw_text
                self.replies: list[str] = []

            async def reply(self, text: str, **_kwargs) -> None:
                self.replies.append(text)

        start = Event(101, "ثبت قوانین")
        assert (await bridge.on_new_message(start)).processed is False
        assert await group_admin.pending_workflow("g", "owner") is not None

        fact = Event(102, "دانستنی")
        assert (await bridge.on_new_message(fact)).processed is False
        assert "🧠 دانستنی:" in fact.replies[-1]
        assert await group_admin.pending_workflow("g", "owner") is not None

        store.set_group_state("g", "memory:owner", "Alice")
        memory = Event(103, "حافظه من")
        assert (await bridge.on_new_message(memory)).processed is False
        assert "Alice" in memory.replies[-1]
        assert await group_admin.pending_workflow("g", "owner") is not None

        wallet = Event(104, "موجودی")
        assert (await bridge.on_new_message(wallet)).processed is False
        assert "کیف پول شما" in wallet.replies[-1]
        assert await group_admin.pending_workflow("g", "owner") is not None
        store.close()

    asyncio.run(scenario())


def test_earlier_admin_and_level_routes_precede_late_workflows(tmp_path) -> None:
    async def scenario() -> None:
        owner_file = tmp_path / "owner.json"
        owner_file.write_text('{"user_id":"global-owner"}', encoding="utf-8")
        store = BotStore(":memory:")
        store.set_group_owner("g", "owner")
        store.set_group_state("g", "group_registry", {"active": True})
        platform = FakePlatform()
        access = AccessController(OwnerRegistry(owner_file), store, platform.is_native_admin)
        management = MemberManagementService(platform, store, access)
        from foxbot.group_admin import GroupAdminService

        group_admin = GroupAdminService(store, access, management)
        bridge = SPlusthonEventBridge(
            MessagePipeline(WordFilter(()), platform.delete_message),
            management,
            group_admin=group_admin,
        )

        class Event:
            is_group = True
            out = False
            chat_id = "g"
            sender_id = "owner"
            sender = SimpleNamespace(id="owner", first_name="Owner", username="owner")

            def __init__(self, message_id: int, raw_text: str) -> None:
                self.id = message_id
                self.raw_text = raw_text
                self.replies: list[str] = []

            async def reply(self, text: str, **_kwargs) -> None:
                self.replies.append(text)

        start = Event(201, "تغییر اخطار")
        assert (await bridge.on_new_message(start)).processed is False
        assert (await group_admin.pending_workflow("g", "owner"))["kind"] == "warning_threshold"

        history = Event(202, "سابقه ها")
        assert (await bridge.on_new_message(history)).processed is False
        assert history.replies[-1].startswith("📭 در ۲۴ ساعت گذشته")
        assert (await group_admin.pending_workflow("g", "owner"))["kind"] == "warning_threshold"

        level = Event(203, "سطح گروه")
        assert (await bridge.on_new_message(level)).processed is False
        assert "سطح گروه" in level.replies[-1]
        assert (await group_admin.pending_workflow("g", "owner"))["kind"] == "warning_threshold"

        cleanup = Event(204, "پاکسازی خودکار")
        assert (await bridge.on_new_message(cleanup)).processed is False
        assert (await group_admin.pending_workflow("g", "owner"))["kind"] == "auto_cleanup"
        assert await group_admin.pending_workflow("g", "owner", kind="warning_threshold") is not None
        assert await group_admin.pending_workflow("g", "owner", kind="auto_cleanup") is not None
        store.close()

    asyncio.run(scenario())


def test_game_router_precedes_remaining_direct_admin_routes(monkeypatch, tmp_path) -> None:
    async def scenario() -> None:
        owner_file = tmp_path / "owner.json"
        owner_file.write_text('{"user_id":"owner"}', encoding="utf-8")
        store = BotStore(":memory:")
        store.set_group_state("g", "group_registry", {"active": True})
        platform = FakePlatform()
        access = AccessController(OwnerRegistry(owner_file), store, platform.is_native_admin)
        management = MemberManagementService(platform, store, access)
        from foxbot import public_commands
        from foxbot.group_admin import GroupAdminService

        group_admin = GroupAdminService(store, access, management)
        bridge = SPlusthonEventBridge(
            MessagePipeline(WordFilter(()), platform.delete_message),
            management,
            group_admin=group_admin,
        )
        game_routes: list[str] = []

        async def game_route(event, _store, **kwargs) -> bool:
            if kwargs.get("text") != "!lock":
                return False
            game_routes.append("!lock")
            await kwargs["reply"](event, "game router received the answer")
            return True

        monkeypatch.setattr(public_commands, "handle_game_command", game_route)

        class Event:
            is_group = True
            out = False
            chat_id = "g"
            sender_id = "owner"
            sender = SimpleNamespace(id="owner", first_name="Owner", username="owner")

            def __init__(self) -> None:
                self.id = 301
                self.raw_text = "!lock"
                self.replies: list[str] = []

            async def reply(self, text: str, **_kwargs) -> None:
                self.replies.append(text)

        event = Event()
        result = await bridge.on_new_message(event)
        assert result.processed is False
        assert game_routes == ["!lock"]
        assert event.replies == ["game router received the answer"]
        store.close()

    asyncio.run(scenario())


def test_pre_game_public_routes_precede_active_game_router(monkeypatch, tmp_path) -> None:
    async def scenario() -> None:
        owner_file = tmp_path / "owner.json"
        owner_file.write_text('{"user_id":"owner"}', encoding="utf-8")
        store = BotStore(":memory:")
        store.set_group_state("g", "group_registry", {"active": True})
        platform = FakePlatform()
        access = AccessController(OwnerRegistry(owner_file), store, platform.is_native_admin)
        management = MemberManagementService(platform, store, access)
        from foxbot import public_commands
        from foxbot.group_admin import GroupAdminService

        group_admin = GroupAdminService(store, access, management)
        bridge = SPlusthonEventBridge(
            MessagePipeline(WordFilter(()), platform.delete_message),
            management,
            group_admin=group_admin,
        )
        game_routes: list[str] = []

        def fake_search(_query: str) -> str:
            return "web route won"

        async def game_route(_event, _store, **kwargs) -> bool:
            game_routes.append(str(kwargs.get("text", "")))
            return False

        monkeypatch.setattr(public_commands, "_search_web_sync", fake_search)
        monkeypatch.setattr(public_commands, "handle_game_command", game_route)

        class Event:
            is_group = True
            out = False
            chat_id = "g"
            sender_id = "owner"
            sender = SimpleNamespace(id="owner", first_name="Owner", username="owner")

            def __init__(self, message_id: int, raw_text: str) -> None:
                self.id = message_id
                self.raw_text = raw_text
                self.replies: list[str] = []

            async def reply(self, text: str, **_kwargs) -> None:
                self.replies.append(text)

        search = Event(401, "جستجو cats")
        assert (await bridge.on_new_message(search)).processed is False
        assert search.replies == ["web route won"]
        assert "جستجو cats" not in game_routes

        site = Event(402, "سایت")
        assert (await bridge.on_new_message(site)).processed is False
        assert "موجودی کافی نیست" in site.replies[-1]
        assert "سایت" not in game_routes

        start = Event(403, "ثبت اصل")
        assert (await bridge.on_new_message(start)).processed is False
        assert start.replies[-1] == "لقب یا اصل خودتو بنویس"
        capture = Event(404, "!lock")
        assert (await bridge.on_new_message(capture)).processed is False
        assert capture.replies[-1] == "✅ اصل شما ثبت شد"
        assert "!lock" not in game_routes
        store.close()

    asyncio.run(scenario())


def test_legacy_group_filter_commands_are_scoped_and_skip_spam_pipeline(tmp_path) -> None:
    async def scenario() -> None:
        owner_file = tmp_path / "owner.json"
        owner_file.write_text('{"user_id":"owner"}', encoding="utf-8")
        store = BotStore(":memory:")
        store.set_group_owner("-1001", "owner")
        platform = FakePlatform()

        async def get_member_status(_group_id: str, user_id: str):
            if user_id == "unknown-admin":
                return SimpleNamespace(admin_status_known=False, is_admin=False)
            return SimpleNamespace(
                admin_status_known=True,
                is_admin=user_id == "native-admin",
            )

        platform.get_member = get_member_status
        access = AccessController(OwnerRegistry(owner_file), store, platform.is_native_admin)
        management = MemberManagementService(platform, store, access)
        from foxbot.group_admin import GroupAdminService

        group_admin = GroupAdminService(store, access, management)
        store.add_group_command_member("-1001", "registered_admin", "registered")
        store.add_group_command_member("-1001", "vip", "vip")
        store.set_group_state("-1001", "group_registry", {"active": True})
        queue = SerialRpcQueue()
        client = FakeClient()
        adapter = SPlusthonAdapter(client, queue)
        word_filter = WordFilter(("blocked", "global-only"))
        pipeline = MessagePipeline(word_filter, adapter.delete_message)
        words = GroupWordService(store, access, word_filter)
        bridge = SPlusthonEventBridge(
            pipeline,
            management,
            words,
            rpc_queue=queue,
            group_admin=group_admin,
        )
        replies: list[str] = []

        class Event:
            is_group = True
            out = False
            chat_id = -1001

            def __init__(self, message_id: int, sender_id: str, raw_text: str) -> None:
                self.id = message_id
                self.sender_id = sender_id
                self.raw_text = raw_text

            async def reply(self, text: str) -> None:
                replies.append(text)

        command = Event(1, "owner", "/فیلتر blocked")
        result = await bridge.on_new_message(command)
        assert not result.processed
        assert client.deleted == []
        assert replies == ["☑️ کلمه «blocked» فیلتر شد"]
        assert await words.list_terms("-1001", "owner") == ("blocked",)

        normal_message = Event(2, "member", "blocked")
        result = await bridge.on_new_message(normal_message)
        assert result.delete_succeeded is True
        assert client.deleted == [("-1001", (2,), True)]

        registered_admin_message = Event(11, "registered", "blocked")
        result = await bridge.on_new_message(registered_admin_message)
        assert result.violation is not None and result.delete_succeeded is None
        vip_message = Event(12, "vip", "blocked")
        result = await bridge.on_new_message(vip_message)
        assert result.violation is not None and result.delete_succeeded is None
        native_admin_message = Event(13, "native-admin", "blocked")
        result = await bridge.on_new_message(native_admin_message)
        assert result.violation is not None and result.delete_succeeded is None
        unknown_admin_message = Event(14, "unknown-admin", "blocked")
        result = await bridge.on_new_message(unknown_admin_message)
        assert result.violation is not None and result.delete_succeeded is None
        assert client.deleted == [("-1001", (2,), True)]

        list_command = Event(3, "owner", "/فیلترها")
        await bridge.on_new_message(list_command)
        assert replies[-1] == "📋 کلمات فیلتر شده:\n\nblocked"

        remove_command = Event(4, "owner", "/رفع blocked")
        await bridge.on_new_message(remove_command)
        assert replies[-1] == "✅ کلمه «blocked» از فیلتر این گروه حذف شد"
        assert await words.list_terms("-1001", "owner") == ()

        add_manual = Event(5, "owner", "/فیلتر group-only")
        await bridge.on_new_message(add_manual)
        toggle_off = Event(6, "owner", "لغو کلمات ممنوعه")
        await bridge.on_new_message(toggle_off)
        assert replies[-1] == (
            "✅ حالت سختگیرانه (کلمات ممنوعه) برای این گروه خاموش شد.\n"
            "فیلتر دستی همچنان فعال است."
        )

        manual_message = Event(7, "member", "group-only")
        assert (await bridge.on_new_message(manual_message)).delete_succeeded is True
        global_message = Event(8, "member", "global-only")
        assert (await bridge.on_new_message(global_message)).violation is None
        assert client.deleted == [
            ("-1001", (2,), True),
            ("-1001", (7,), True),
        ]

        toggle_on = Event(9, "owner", "فعال کلمات ممنوعه")
        await bridge.on_new_message(toggle_on)
        assert replies[-1] == "✅ حالت سختگیرانه (کلمات ممنوعه) برای این گروه فعال شد."
        global_message_again = Event(10, "member", "global-only")
        assert (await bridge.on_new_message(global_message_again)).delete_succeeded is True
        await queue.close()
        store.close()

    asyncio.run(scenario())


def test_expired_group_is_silent_for_members_and_global_owner_renewal_reactivates_it(tmp_path) -> None:
    async def scenario() -> None:
        owner_file = tmp_path / "owner.json"
        owner_file.write_text('{"user_id":"owner"}', encoding="utf-8")
        store = BotStore(":memory:")
        platform = FakePlatform()
        access = AccessController(OwnerRegistry(owner_file), store, platform.is_native_admin)
        management = MemberManagementService(platform, store, access)
        from foxbot.group_admin import GroupAdminService

        group_admin = GroupAdminService(store, access, management)
        expired_at = datetime.now(UTC) - timedelta(seconds=1)
        store.set_group_state("g", "group_registry", {"active": False, "title": "Test group"})
        store.set_group_state(
            "g",
            "expiry",
            {
                "command": "۵ روز",
                "days": 5,
                "activated_at": (expired_at - timedelta(days=5)).isoformat(),
                "expires_at": expired_at.isoformat(),
                "title": "Test group",
                "notified": True,
            },
        )
        bridge = SPlusthonEventBridge(
            MessagePipeline(WordFilter(()), platform.delete_message),
            management,
            group_admin=group_admin,
        )

        class Event:
            is_group = True
            out = False
            chat_id = "g"
            chat = SimpleNamespace(title="Test group")

            def __init__(self, message_id: int, sender_id: str, raw_text: str) -> None:
                self.id = message_id
                self.sender_id = sender_id
                self.raw_text = raw_text
                self.replies: list[tuple[str, dict]] = []

            async def reply(self, text: str, **kwargs) -> None:
                self.replies.append((text, kwargs))

        blocked_member = Event(1, "member", "!help")
        assert (await bridge.on_new_message(blocked_member)).processed is False
        assert blocked_member.replies == []
        assert store.get_group_stats("g")["messages"] == 0

        renewal = Event(2, "owner", "۵ روز")
        assert (await bridge.on_new_message(renewal)).processed is False
        assert len(renewal.replies) == 1
        response, kwargs = renewal.replies[0]
        assert response.startswith("✅ تاریخ انقضای گروه تنظیم شد.")
        assert "Test group" not in response
        assert kwargs["formatting_entities"]
        assert store.get_group_state("g", "group_registry")["active"] is True
        new_expiry = store.get_group_state("g", "expiry")
        assert new_expiry["notified"] is False
        assert datetime.fromisoformat(new_expiry["expires_at"]) > datetime.now(UTC)

        member_cannot_change_expiry = Event(3, "member", "دو هفته")
        assert (await bridge.on_new_message(member_cannot_change_expiry)).processed is False
        assert member_cannot_change_expiry.replies == []
        assert store.get_group_state("g", "expiry") == new_expiry

        help_event = Event(4, "member", "!help")
        assert (await bridge.on_new_message(help_event)).processed is False
        assert help_event.replies[-1][0].startswith("🦊 راهنمای روباه")
        store.close()

    asyncio.run(scenario())


def test_history_dispatch_reports_warnings_not_only_kick_events(tmp_path) -> None:
    async def scenario() -> None:
        owner_file = tmp_path / "owner.json"
        owner_file.write_text('{"user_id":"owner"}', encoding="utf-8")
        store = BotStore(":memory:")
        store.set_group_owner("g", "owner")
        store.set_group_state("g", "group_registry", {"active": True})
        store.record_group_command_event(
            group_id="g",
            event_type="warning",
            user_id="private-user-id",
            display_name="Rose",
            actor_id="owner",
            detail="test warning",
        )
        platform = FakePlatform()
        access = AccessController(OwnerRegistry(owner_file), store, platform.is_native_admin)
        management = MemberManagementService(platform, store, access)
        from foxbot.group_admin import GroupAdminService

        group_admin = GroupAdminService(store, access, management)
        bridge = SPlusthonEventBridge(
            MessagePipeline(WordFilter(()), platform.delete_message),
            management,
            group_admin=group_admin,
        )

        class Event:
            is_group = True
            out = False
            chat_id = "g"
            sender_id = "owner"
            id = 1
            raw_text = "سابقه ها"

            def __init__(self) -> None:
                self.replies: list[tuple[str, dict]] = []

            async def reply(self, text: str, **kwargs) -> None:
                self.replies.append((text, kwargs))

        event = Event()
        assert (await bridge.on_new_message(event)).processed is False
        assert "Rose" in event.replies[-1][0]
        assert "⚠️ اخطار گرفته" in event.replies[-1][0]
        assert "private-user-id" not in event.replies[-1][0]
        assert "سابقهٔ اخراج" not in event.replies[-1][0]
        store.close()

    asyncio.run(scenario())


def test_legacy_username_admin_is_promoted_to_group_scoped_user_id(tmp_path) -> None:
    async def scenario() -> None:
        owner_file = tmp_path / "owner.json"
        owner_file.write_text('{"user_id":"owner"}', encoding="utf-8")
        admins_file = tmp_path / "admins.json"
        admins_file.write_text(
            '{"-1001":["@LegacyAdmin"]}', encoding="utf-8"
        )
        store = BotStore(":memory:")
        store.set_group_state("-1001", "group_registry", {"active": True})
        store.set_group_state("-2002", "group_registry", {"active": True})
        platform = FakePlatform()
        access = AccessController(OwnerRegistry(owner_file), store, platform.is_native_admin)
        management = MemberManagementService(platform, store, access)
        from foxbot.group_admin import GroupAdminService

        group_admin = GroupAdminService(store, access, management)
        word_filter = WordFilter(())
        word_lists = GroupWordService(store, access, word_filter)
        legacy_admins = LegacyAdminRegistry(admins_file)
        bridge = SPlusthonEventBridge(
            MessagePipeline(word_filter, platform.delete_message),
            management,
            word_lists,
            group_admin=group_admin,
            legacy_admin_registry=legacy_admins,
        )

        class Event:
            is_group = True
            out = False

            def __init__(self, group_id: str, message_id: int, username: str) -> None:
                self.chat_id = group_id
                self.id = message_id
                self.sender_id = "legacy-user-id"
                self.raw_text = "/فیلتر blocked"
                self.sender = SimpleNamespace(
                    id=self.sender_id, username=username, first_name="Legacy", last_name="Admin"
                )
                self.replies: list[str] = []

            async def reply(self, text: str) -> None:
                self.replies.append(text)

        in_group = Event("-1001", 1, "LegacyAdmin")
        assert (await bridge.on_new_message(in_group)).processed is False
        assert in_group.replies == ["☑️ کلمه «blocked» فیلتر شد"]
        assert store.has_group_command_member("-1001", "registered_admin", "legacy-user-id")
        assert legacy_admins.is_promoted("-1001", "legacy-user-id")
        assert await word_lists.list_terms("-1001", "legacy-user-id") == ("blocked",)

        other_group = Event("-2002", 2, "LegacyAdmin")
        assert (await bridge.on_new_message(other_group)).processed is False
        assert "فقط مالک یا ادمین ثبت‌شده" in other_group.replies[-1]
        assert store.list_group_words("-2002") == []
        store.close()

    asyncio.run(scenario())
