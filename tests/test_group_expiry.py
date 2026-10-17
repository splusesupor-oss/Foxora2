from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from foxbot.group_expiry import (
    DURATION_DAYS,
    build_confirmation,
    due_expiries,
    is_expired,
    mark_notified,
    match_expiry_command,
    remaining_status,
    same_expiry_generation,
    set_expiry,
)
from foxbot.rpc_queue import SerialRpcQueue
from foxbot.runtime import _process_scheduled_actions
from foxbot.storage import BotStore


@pytest.mark.parametrize(
    ("command", "days"),
    (("۵ روز", 5), ("یک هفته", 7), ("دو هفته", 14), ("یک ماه", 29)),
)
def test_exact_duration_commands_persist_and_expire_at_their_deadline(
    command: str, days: int
) -> None:
    store = BotStore(":memory:")
    now = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)

    record = set_expiry(store, "g", command, title="Test group", now=now)
    assert record is not None
    assert record["days"] == days
    assert record["title"] == "Test group"
    assert datetime.fromisoformat(record["expires_at"]) == now + timedelta(days=days)
    assert not is_expired(store, "g", now=now + timedelta(days=days) - timedelta(seconds=1))
    assert is_expired(store, "g", now=now + timedelta(days=days))
    assert due_expiries(store, now=now + timedelta(days=days)) == [("g", record)]
    assert remaining_status(store, "g", now=now)[0] == "active"

    mark_notified(store, "g", record)
    assert due_expiries(store, now=now + timedelta(days=days)) == []
    store.close()


def test_expiry_commands_are_exact_but_normalize_persian_text() -> None:
    assert set(DURATION_DAYS.values()) == {5, 7, 14, 29}
    assert match_expiry_command("  يک\u200c هفته  ") == "یک هفته"
    assert match_expiry_command("دو  هفته") == "دو هفته"
    assert match_expiry_command("یک هفته دیگر") is None
    assert match_expiry_command("ثبت یک ماه") is None
    assert match_expiry_command("𝟱 روز") is None


def test_confirmation_uses_tehran_jalali_dates_and_utf16_entity_spans() -> None:
    activated = datetime(2026, 3, 21, 7, 0, tzinfo=UTC)
    expires = activated + timedelta(days=7)
    text, spans = build_confirmation(activated, expires)

    assert "𝟭𝟰𝟬𝟱/𝟬𝟭/𝟬𝟭" in text
    assert "فروردین" in text
    assert "ساعت 𝟭𝟬:𝟯𝟬" in text
    encoded = text.encode("utf-16-le")
    for kind, offset, length in spans:
        selected = encoded[offset * 2 : (offset + length) * 2].decode("utf-16-le")
        assert "فروردین" in selected
        assert kind in {"bold", "blockquote"}


def test_failed_expiry_notice_uses_bounded_retry_backoff() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        expired_at = datetime.now(UTC) - timedelta(seconds=1)
        store.set_group_state("g", "group_registry", {"active": True})
        store.set_group_state(
            "g",
            "expiry",
            {
                "generation": "retry-case",
                "command": "۵ روز",
                "days": 5,
                "activated_at": (expired_at - timedelta(days=5)).isoformat(),
                "expires_at": expired_at.isoformat(),
                "notified": False,
            },
        )

        class FailingClient:
            async def send_message(self, _group_id, _text, **_kwargs):
                raise TimeoutError("temporary")

        queue = SerialRpcQueue(min_interval_seconds=0)
        runtime = SimpleNamespace(store=store, group_admin=None, rpc_queue=queue)
        await _process_scheduled_actions(runtime, FailingClient())

        record = store.get_group_state("g", "expiry")
        retry_at = datetime.fromisoformat(record["next_notice_at"])
        assert record["notice_attempts"] == 1
        assert record["notified"] is False
        assert store.get_group_state("g", "group_registry")["active"] is False
        assert 29 <= (retry_at - datetime.now(UTC)).total_seconds() <= 31
        assert due_expiries(store, now=retry_at - timedelta(seconds=1)) == []
        assert len(due_expiries(store, now=retry_at)) == 1

        await queue.close()
        store.close()

    asyncio.run(scenario())


def test_watcher_skips_stale_notice_when_owner_renews_before_rpc_dispatch() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        old_record = set_expiry(
            store,
            "g",
            "۵ روز",
            now=datetime.now(UTC) - timedelta(days=6),
        )
        assert old_record is not None
        old_record["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        store.set_group_state("g", "expiry", old_record)
        store.set_group_state("g", "group_registry", {"active": True})

        class RenewBeforeSendQueue:
            async def submit(self, _group_id, _operation_name, operation, **_kwargs):
                renewed = set_expiry(store, "g", "۵ روز")
                assert renewed is not None
                store.set_group_state("g", "group_registry", {"active": True})
                return await operation()

        class NoticeClient:
            def __init__(self) -> None:
                self.notices = []

            async def send_message(self, group_id, text, **kwargs):
                self.notices.append((group_id, text, kwargs))
                return SimpleNamespace(id=len(self.notices))

        runtime = SimpleNamespace(store=store, group_admin=None, rpc_queue=RenewBeforeSendQueue())
        client = NoticeClient()
        await _process_scheduled_actions(runtime, client)

        current = store.get_group_state("g", "expiry")
        assert current["generation"] != old_record["generation"]
        assert not same_expiry_generation(current, old_record)
        assert current["notified"] is False
        assert store.get_group_state("g", "group_registry")["active"] is True
        assert client.notices == []
        store.close()

    asyncio.run(scenario())


def test_scheduler_deactivates_expired_group_and_sends_one_notice() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        store.set_group_state("g", "group_registry", {"active": True, "title": "Test group"})
        store.set_group_state(
            "g",
            "expiry",
            {
                "command": "۵ روز",
                "days": 5,
                "activated_at": (datetime.now(UTC) - timedelta(days=6)).isoformat(),
                "expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
                "title": "Test group",
                "notified": False,
            },
        )

        class NoticeClient:
            def __init__(self) -> None:
                self.notices = []

            async def send_message(self, group_id, text, **kwargs):
                self.notices.append((group_id, text, kwargs))
                return SimpleNamespace(id=len(self.notices))

        queue = SerialRpcQueue(min_interval_seconds=0)
        runtime = SimpleNamespace(store=store, group_admin=None, rpc_queue=queue)
        client = NoticeClient()

        await _process_scheduled_actions(runtime, client)
        assert store.get_group_state("g", "group_registry")["active"] is False
        expiry = store.get_group_state("g", "expiry")
        assert expiry["notified"] is True
        assert expiry["deactivated_at"]
        assert len(client.notices) == 1
        group_id, text, kwargs = client.notices[0]
        assert group_id == "g"
        assert text == "⛔ مدت زمان فعال بودن گروه به پایان رسید و گروه به‌صورت خودکار غیرفعال شد"
        assert len(kwargs["formatting_entities"]) == 1

        await _process_scheduled_actions(runtime, client)
        assert len(client.notices) == 1
        await queue.close()
        store.close()

    asyncio.run(scenario())
