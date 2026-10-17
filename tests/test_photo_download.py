from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from foxbot.photo_download import (
    ASK_QUERY,
    CONFIRM_TEXT,
    INSUFFICIENT,
    _image_format,
    _safe_public_url,
    handle_photo_download_command,
    wait_for_background_tasks,
)
from foxbot.public_commands import handle_public_command
from foxbot.rpc_queue import SerialRpcQueue
from foxbot.soroush_adapter import SPlusthonAdapter
from foxbot.storage import BotStore


class FakeEvent:
    def __init__(self) -> None:
        self.replies: list[str] = []

    async def reply(self, text: str, **_kwargs: Any) -> Any:
        self.replies.append(text)
        return SimpleNamespace(id=len(self.replies))


async def _reply(event: FakeEvent, text: str, *, formatting_entities=None) -> Any:
    return await event.reply(text, formatting_entities=formatting_entities)


def _fund(store: BotStore, *, group_id: str = "group", user_id: str = "user", amount: int = 40) -> None:
    store.award_group_coins(group_id, user_id, amount, "bronze", reference=f"fund:{user_id}")


def _seed_image_search(monkeypatch) -> None:
    from foxbot import photo_download

    async def search(_query: str):
        return [
            ("https://images.example.test/cat-1.jpg", "cat one", []),
            ("https://images.example.test/cat-2.jpg", "cat two", []),
        ]

    monkeypatch.setattr(photo_download, "_search_image_candidates", search)
    monkeypatch.setattr(photo_download, "_download_image", lambda _url: b"\xff\xd8\xff" + b"x" * 128)


def test_photo_download_is_routed_and_debits_only_after_sequential_send(monkeypatch) -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        _fund(store, amount=40)
        event = FakeEvent()
        sent: list[str] = []
        active = 0
        max_active = 0

        async def send_photo(group_id: str, content: bytes, filename: str) -> bool:
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0)
            assert group_id == "group"
            assert content.startswith(b"\xff\xd8\xff")
            sent.append(filename)
            active -= 1
            return True

        _seed_image_search(monkeypatch)
        assert await handle_public_command(
            event, store, group_id="group", actor_id="user", text="دانلود عکس",
            reply=_reply, photo_sender=send_photo,
        )
        assert event.replies[-1] == ASK_QUERY
        assert await handle_public_command(
            event, store, group_id="group", actor_id="user", text="گربه",
            reply=_reply, photo_sender=send_photo,
        )
        assert CONFIRM_TEXT in event.replies[-1]
        assert store.get_economy_profile("group", "user")["bronze"] == 40

        assert await handle_public_command(
            event, store, group_id="group", actor_id="user", text="بله",
            reply=_reply, photo_sender=send_photo,
        )
        # Confirmation itself does not debit; the task must receive successful
        # platform-send results before the atomic debit is written.
        assert store.get_economy_profile("group", "user")["bronze"] == 40
        await wait_for_background_tasks()
        assert sent == ["photo_1.jpg", "photo_2.jpg"]
        assert max_active == 1
        assert store.get_economy_profile("group", "user")["bronze"] == 20
        assert "۲۰ سکه برنز کسر شد" in event.replies[-1]
        assert store.get_group_state("group", "photo_download:user") is None
        store.close()

    asyncio.run(scenario())


def test_photo_download_never_debits_when_all_platform_delivery_paths_fail(monkeypatch) -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        _fund(store, amount=10)
        event = FakeEvent()
        attempted_links = 0

        async def no_photo(_group_id: str, _content: bytes, _filename: str) -> bool:
            return False

        async def no_external_photo(_group_id: str, _url: str) -> bool:
            return False

        async def reply_with_failed_link(event: FakeEvent, text: str, *, formatting_entities=None) -> Any:
            nonlocal attempted_links
            if text.startswith("🖼️ تصویر (لینک‌های مستقیم)"):
                attempted_links += 1
                raise RuntimeError("platform send failed")
            return await event.reply(text)

        _seed_image_search(monkeypatch)
        assert await handle_photo_download_command(
            event, store, group_id="group", actor_id="user", text="دانلود عکس",
            reply=reply_with_failed_link, photo_sender=no_photo, url_sender=no_external_photo,
        )
        assert await handle_photo_download_command(
            event, store, group_id="group", actor_id="user", text="گربه",
            reply=reply_with_failed_link, photo_sender=no_photo, url_sender=no_external_photo,
        )
        assert await handle_photo_download_command(
            event, store, group_id="group", actor_id="user", text="بله",
            reply=reply_with_failed_link, photo_sender=no_photo, url_sender=no_external_photo,
        )
        await wait_for_background_tasks()
        assert attempted_links == 1
        assert store.get_economy_profile("group", "user")["bronze"] == 10
        assert "هیچ سکه‌ای کم نشد" in event.replies[-1]
        store.close()

    asyncio.run(scenario())


def test_photo_download_owner_bypass_and_insufficient_balance(monkeypatch) -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        event = FakeEvent()
        owner_sends: list[str] = []

        async def send_photo(_group_id: str, _content: bytes, filename: str) -> bool:
            owner_sends.append(filename)
            return True

        _seed_image_search(monkeypatch)
        for text in ("دانلود عکس", "روباه", "بله"):
            assert await handle_photo_download_command(
                event, store, group_id="group", actor_id="owner", text=text,
                reply=_reply, photo_sender=send_photo, owner_check=lambda uid: uid == "owner",
            )
        await wait_for_background_tasks()
        assert len(owner_sends) == 2
        assert store.get_economy_profile("group", "owner")["bronze"] == 0
        assert "۰ سکه برنز کسر شد" in event.replies[-1]

        no_funds = FakeEvent()
        assert await handle_photo_download_command(
            no_funds, store, group_id="group", actor_id="poor", text="دانلود عکس", reply=_reply
        )
        assert await handle_photo_download_command(
            no_funds, store, group_id="group", actor_id="poor", text="روباه", reply=_reply
        )
        assert INSUFFICIENT in no_funds.replies[-1]
        assert store.get_group_state("group", "photo_download:poor") is None
        store.close()

    asyncio.run(scenario())


def test_photo_query_blocking_and_url_image_validation() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        _fund(store)
        event = FakeEvent()
        assert await handle_photo_download_command(
            event, store, group_id="group", actor_id="user", text="دانلود عکس",
            reply=_reply, blocked_terms=("restricted phrase",),
        )
        assert await handle_photo_download_command(
            event, store, group_id="group", actor_id="user", text="restricted phrase",
            reply=_reply, blocked_terms=("restricted phrase",),
        )
        assert "ممنوع" in event.replies[-1]
        assert store.get_group_state("group", "photo_download:user") is None
        store.close()

    asyncio.run(scenario())
    assert _safe_public_url("https://upload.wikimedia.org/example/image.jpg")
    assert not _safe_public_url("http://upload.wikimedia.org/example/image.jpg")
    assert not _safe_public_url("https://127.0.0.1/photo.jpg")
    assert _image_format(b"\x89PNG\r\n\x1a\n" + b"x" * 10) == "png"
    assert _image_format(b"<html>not an image</html>") is None


def test_photo_adapter_queues_each_uploaded_photo_operation() -> None:
    class Client:
        def __init__(self) -> None:
            self.uploads: list[tuple[str, bytes, str]] = []

        async def send_file(self, group_id: str, stream, **kwargs):
            self.uploads.append((group_id, stream.read(), stream.name))
            return SimpleNamespace(id=len(self.uploads))

    async def scenario() -> None:
        client = Client()
        queue = SerialRpcQueue(min_interval_seconds=0)
        adapter = SPlusthonAdapter(client, queue)
        data = b"\xff\xd8\xff" + b"image-data" * 20
        assert await adapter.send_photo("group", data, "single.jpg")
        assert len(client.uploads) == 1
        assert client.uploads[0] == ("group", data, "single.jpg")
        await queue.close()

    asyncio.run(scenario())
