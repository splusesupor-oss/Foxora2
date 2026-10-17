from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from splusthon.tl import types

from foxbot.broadcast_commands import BROADCAST_TRIGGERS, BroadcastService
from foxbot.filters import WordFilter
from foxbot.group_admin import GroupAdminService
from foxbot.management import MemberManagementService
from foxbot.message_pipeline import MessagePipeline
from foxbot.security import AccessController, OwnerRegistry
from foxbot.soroush_events import SPlusthonEventBridge
from foxbot.storage import BotStore


class FakeEvent:
    def __init__(
        self,
        group_id: str,
        actor_id: str,
        text: str,
        *,
        entities: list[Any] | None = None,
    ) -> None:
        self.chat_id = group_id
        self.sender_id = actor_id
        self.raw_text = text
        self.message = SimpleNamespace(entities=entities or [])
        self.replies: list[tuple[str, dict[str, Any]]] = []

    async def reply(self, text: str, **kwargs: Any) -> SimpleNamespace:
        self.replies.append((text, kwargs))
        return SimpleNamespace(id=len(self.replies))


class FakePlatform:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, list[Any] | None]] = []
        self.after_send = None
        self.response: Any = SimpleNamespace(id=1)

    async def send_group_message(
        self,
        group_id: str,
        text: str,
        *,
        formatting_entities: list[Any] | None = None,
    ) -> Any:
        self.sent.append((str(group_id), text, formatting_entities))
        if self.after_send is not None:
            self.after_send(str(group_id))
        return self.response


async def _reply(
    event: FakeEvent,
    text: str,
    *,
    formatting_entities: list[Any] | None = None,
) -> Any:
    kwargs = {"formatting_entities": formatting_entities} if formatting_entities else {}
    return await event.reply(text, **kwargs)


def _active_groups(store: BotStore, *group_ids: str) -> None:
    for group_id in group_ids:
        store.set_group_state(group_id, "group_registry", {"active": True})


async def _handle(
    service: BroadcastService, event: FakeEvent, *, actor_id: str = "owner"
) -> bool:
    return await service.handle(
        event,
        group_id=event.chat_id,
        actor_id=actor_id,
        text=event.raw_text,
        reply=_reply,
    )


def test_global_broadcast_requires_preview_and_explicit_confirm_then_sends_in_order() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        _active_groups(store, "g-a", "g-b")
        store.set_group_state("g-inactive", "group_registry", {"active": False})
        platform = FakePlatform()
        service = BroadcastService(store, platform, lambda user_id: user_id == "owner")

        trigger = FakeEvent("g-a", "owner", "اطلاع رسانی")
        assert await _handle(service, trigger)
        assert not platform.sent
        assert "متن اطلاع‌رسانی" in trigger.replies[-1][0]

        body = FakeEvent(
            "g-a",
            "owner",
            "Hello 🦊",
            entities=[types.MessageEntityBold(offset=0, length=5)],
        )
        assert await _handle(service, body)
        assert not platform.sent
        preview, preview_kwargs = body.replies[-1]
        assert "Hello 🦊" in preview
        assert "اسکن‌شده: ۲" in preview
        preview_entities = preview_kwargs["formatting_entities"]
        assert len(preview_entities) == 1
        assert preview_entities[0].offset > 0
        state = store.get_group_state("__global__", "global_broadcast:owner")
        assert state["step"] == "confirm"
        assert state["target_groups"] == ["g-a", "g-b"]

        confirm = FakeEvent("g-a", "owner", "تأیید")
        assert await _handle(service, confirm)
        assert [item[0] for item in platform.sent] == ["g-a", "g-b"]
        assert all(item[1] == "Hello 🦊" for item in platform.sent)
        assert all(item[2] and item[2][0].__class__.__name__ == "MessageEntityBold" for item in platform.sent)
        assert store.get_group_state("__global__", "global_broadcast:owner") is None
        assert "تأییدشده توسط پلتفرم: ۲" in confirm.replies[-1][0]
        store.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("trigger", sorted(BROADCAST_TRIGGERS))
def test_all_legacy_global_broadcast_triggers_enter_the_guarded_workflow(trigger: str) -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        _active_groups(store, "g")
        service = BroadcastService(store, FakePlatform(), lambda user_id: user_id == "owner")
        event = FakeEvent("g", "owner", trigger)
        assert await _handle(service, event)
        assert store.get_group_state("__global__", "global_broadcast:owner")["step"] == "message"
        store.close()

    asyncio.run(scenario())


def test_broadcast_is_unavailable_to_non_global_owners() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        _active_groups(store, "g")
        platform = FakePlatform()
        service = BroadcastService(store, platform, lambda user_id: user_id == "owner")
        event = FakeEvent("g", "member", "اعلان")
        assert not await _handle(service, event, actor_id="member")
        assert not event.replies
        assert not platform.sent
        assert store.get_group_state("__global__", "global_broadcast:member") is None
        store.close()

    asyncio.run(scenario())


def test_changed_local_group_registry_invalidates_confirmation_without_sending() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        _active_groups(store, "g-a", "g-b")
        platform = FakePlatform()
        service = BroadcastService(store, platform, lambda user_id: user_id == "owner")
        await _handle(service, FakeEvent("g-a", "owner", "اعلان"))
        await _handle(service, FakeEvent("g-a", "owner", "An announcement"))
        store.set_group_state("g-b", "group_registry", {"active": False})
        confirm = FakeEvent("g-a", "owner", "تایید")
        assert await _handle(service, confirm)
        assert not platform.sent
        assert "تغییر کرده است" in confirm.replies[-1][0]
        assert store.get_group_state("__global__", "global_broadcast:owner") is None
        store.close()

    asyncio.run(scenario())


def test_every_target_is_revalidated_immediately_before_its_send() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        _active_groups(store, "g-a", "g-b")
        platform = FakePlatform()
        platform.after_send = lambda group_id: store.set_group_state(
            "g-b", "group_registry", {"active": False}
        )
        service = BroadcastService(store, platform, lambda user_id: user_id == "owner")
        await _handle(service, FakeEvent("g-a", "owner", "اطلاعرسانی"))
        await _handle(service, FakeEvent("g-a", "owner", "Sequential message"))
        confirm = FakeEvent("g-a", "owner", "✅ تایید")
        await _handle(service, confirm)
        assert [item[0] for item in platform.sent] == ["g-a"]
        assert "غیرفعال‌شده هنگام ارسال: ۱" in confirm.replies[-1][0]
        store.close()

    asyncio.run(scenario())


def test_incomplete_bounded_scan_clears_session_and_sends_nothing() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        _active_groups(store, "g-a", "g-b")
        platform = FakePlatform()
        service = BroadcastService(
            store,
            platform,
            lambda user_id: user_id == "owner",
            max_groups=1,
        )
        await _handle(service, FakeEvent("g-a", "owner", "اعلان"))
        body = FakeEvent("g-a", "owner", "Message")
        await _handle(service, body)
        assert "سقف اسکن محلی" in body.replies[-1][0]
        assert not platform.sent
        assert store.get_group_state("__global__", "global_broadcast:owner") is None
        store.close()

    asyncio.run(scenario())


def test_broadcast_can_be_cancelled_before_confirmation_without_sending() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        _active_groups(store, "g")
        platform = FakePlatform()
        service = BroadcastService(store, platform, lambda user_id: user_id == "owner")
        await _handle(service, FakeEvent("g", "owner", "اعلان"))
        await _handle(service, FakeEvent("g", "owner", "Announcement"))
        cancel = FakeEvent("g", "owner", "❌ لغو")
        assert await _handle(service, cancel)
        assert "هیچ پیامی ارسال نشد" in cancel.replies[-1][0]
        assert not platform.sent
        assert store.get_group_state("__global__", "global_broadcast:owner") is None
        store.close()

    asyncio.run(scenario())


def test_event_bridge_routes_global_broadcast_trigger_before_message_filters(tmp_path) -> None:
    async def scenario() -> None:
        owner_file = tmp_path / "owner.json"
        owner_file.write_text('{"user_id":"owner"}', encoding="utf-8")
        store = BotStore(":memory:")
        _active_groups(store, "g")
        platform = FakePlatform()

        async def is_native_admin(_group_id: str, _user_id: str) -> bool:
            return False

        async def delete_message(_group_id: str, _message_id: str) -> bool:
            return True

        access = AccessController(
            OwnerRegistry(owner_file), store, is_native_admin
        )
        management = MemberManagementService(
            platform, store, access, bot_user_id="bot"
        )
        group_admin = GroupAdminService(store, access, management)
        bridge = SPlusthonEventBridge(
            MessagePipeline(WordFilter(("اعلان",)), delete_message),
            management,
            group_admin=group_admin,
            platform=platform,
        )

        class BridgeEvent(FakeEvent):
            is_group = True
            out = False

            def __init__(self, message_id: int, text: str) -> None:
                super().__init__("g", "owner", text)
                self.id = message_id
                self.sender = SimpleNamespace(
                    id="owner", first_name="Global", last_name="Owner", username=None
                )

        trigger = BridgeEvent(1, "اعلان")
        await bridge.on_new_message(trigger)
        assert trigger.replies
        assert "متن اطلاع‌رسانی" in trigger.replies[-1][0]
        assert not platform.sent

        help_event = BridgeEvent(2, "راهنما")
        await bridge.on_new_message(help_event)
        assert help_event.replies
        assert store.get_group_state("__global__", "global_broadcast:owner")["step"] == "message"
        assert not platform.sent

        body = BridgeEvent(3, "A safe announcement")
        await bridge.on_new_message(body)
        assert not platform.sent
        assert "پیش‌نمایش" in body.replies[-1][0]

        confirmation = BridgeEvent(4, "تأیید")
        await bridge.on_new_message(confirmation)
        assert [sent[0] for sent in platform.sent] == ["g"]
        assert "تأییدشده توسط پلتفرم: ۱" in confirmation.replies[-1][0]
        await bridge.close(drain=True)
        store.close()

    asyncio.run(scenario())


def test_uncertain_broadcast_state_can_only_be_cleared_from_its_origin_group() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        _active_groups(store, "g-a", "g-b")
        store.set_group_state(
            "__global__",
            "global_broadcast:owner",
            {
                "step": "sending",
                "owner_id": "owner",
                "origin_group_id": "g-a",
                "token": "interrupted-token",
                "created_at": 1.0,
            },
        )
        service = BroadcastService(store, FakePlatform(), lambda user_id: user_id == "owner")
        wrong_group = FakeEvent("g-b", "owner", "لغو")
        assert not await _handle(service, wrong_group)
        assert store.get_group_state("__global__", "global_broadcast:owner") is not None

        origin_group = FakeEvent("g-a", "owner", "لغو")
        assert await _handle(service, origin_group)
        assert "وضعیت محلی پاک شد" in origin_group.replies[-1][0]
        assert store.get_group_state("__global__", "global_broadcast:owner") is None
        store.close()

    asyncio.run(scenario())


def test_missing_platform_ack_is_reported_as_unconfirmed_not_success() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        _active_groups(store, "g")
        platform = FakePlatform()
        platform.response = None
        service = BroadcastService(store, platform, lambda user_id: user_id == "owner")
        await _handle(service, FakeEvent("g", "owner", "اعلان"))
        await _handle(service, FakeEvent("g", "owner", "Announcement"))
        confirmation = FakeEvent("g", "owner", "تأیید")
        await _handle(service, confirmation)
        assert len(platform.sent) == 1
        assert "تأییدشده توسط پلتفرم: ۰" in confirmation.replies[-1][0]
        assert "ناموفق یا بدون تأیید: ۱" in confirmation.replies[-1][0]
        store.close()

    asyncio.run(scenario())


def test_preview_session_expires_without_sending() -> None:
    async def scenario() -> None:
        now = [1000.0]
        store = BotStore(":memory:")
        _active_groups(store, "g")
        platform = FakePlatform()
        service = BroadcastService(
            store,
            platform,
            lambda user_id: user_id == "owner",
            clock=lambda: now[0],
        )
        await _handle(service, FakeEvent("g", "owner", "اعلان"))
        now[0] += 601
        expired = FakeEvent("g", "owner", "Too late")
        assert await _handle(service, expired)
        assert "نشست اطلاع‌رسانی منقضی شد" in expired.replies[-1][0]
        assert not platform.sent
        assert store.get_group_state("__global__", "global_broadcast:owner") is None
        store.close()

    asyncio.run(scenario())
