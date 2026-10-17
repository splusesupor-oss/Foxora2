from __future__ import annotations

import asyncio

import pytest

from foxbot.filters import (
    MAX_GROUP_FILTER_TEXT_LENGTH,
    MAX_GROUP_WORD_TERM_LENGTH,
    MAX_GROUP_WORD_TERMS,
    WordFilter,
)
from foxbot.security import AccessController, OwnerRegistry
from foxbot.storage import BotStore
from foxbot.word_commands import handle_group_word_command
from foxbot.wordlists import GroupWordService


class Event:
    def __init__(self, text: str) -> None:
        self.raw_text = text
        self.sender_id = "owner"
        self.replies: list[str] = []

    async def reply(self, text: str) -> None:
        self.replies.append(text)


def _service(tmp_path):
    owner_path = tmp_path / "owner.json"
    owner_path.write_text('{"user_id":"global-owner"}', encoding="utf-8")
    store = BotStore(":memory:")
    store.set_group_owner("g", "owner")

    async def native_admin(_group_id: str, _user_id: str) -> bool:
        return False

    word_filter = WordFilter()
    service = GroupWordService(
        store,
        AccessController(OwnerRegistry(owner_path), store, native_admin),
        word_filter,
    )
    return store, word_filter, service


def test_group_filter_terms_are_bounded_and_precompiled(monkeypatch) -> None:
    matcher = WordFilter()
    with pytest.raises(ValueError, match="term limit"):
        matcher.set_group_terms("g", (f"term-{index}" for index in range(MAX_GROUP_WORD_TERMS + 1)))
    with pytest.raises(ValueError, match="length limit"):
        matcher.set_group_terms("g", ("x" * (MAX_GROUP_WORD_TERM_LENGTH + 1),))

    matcher.set_group_terms("g", ("manual phrase",))

    def unexpected_compile(*_args, **_kwargs):
        raise AssertionError("group patterns should be compiled when terms are loaded")

    monkeypatch.setattr("foxbot.filters.re.compile", unexpected_compile)
    hit = matcher.match("g", "a manual phrase appears", include_global=False)
    assert hit is not None and hit.scope == "group" and hit.original_term == "manual phrase"


def test_group_filter_text_scan_is_bounded_to_a_safe_prefix() -> None:
    matcher = WordFilter()
    matcher.set_group_terms("g", ("marker",))
    text = "x" * MAX_GROUP_FILTER_TEXT_LENGTH + " marker"
    assert matcher.match("g", text, include_global=False) is None
    assert matcher.match("g", "marker " + text, include_global=False) is not None


def test_group_filter_commands_report_safe_limits_without_saving_input(tmp_path) -> None:
    async def scenario() -> None:
        store, word_filter, service = _service(tmp_path)
        event = Event("/فیلتر " + "x" * (MAX_GROUP_WORD_TERM_LENGTH + 1))
        assert await handle_group_word_command(
            event, service, group_id="g", actor_id="owner", text=event.raw_text
        )
        assert event.replies == [
            "❌ فهرست فیلتر این گروه به یکی از محدودیت‌های امن خود رسیده است."
        ]
        assert store.list_group_words("g") == []
        assert word_filter.group_terms("g") == ()

        huge_event = Event("/فیلتر " + "x" * 100_000)
        assert await handle_group_word_command(
            huge_event, service, group_id="g", actor_id="owner", text=huge_event.raw_text
        )
        assert huge_event.replies == event.replies
        assert store.list_group_words("g") == []

        oversized = [f"term-{index}" for index in range(MAX_GROUP_WORD_TERMS + 1)]
        with pytest.raises(ValueError, match="term limit"):
            await service.replace_terms("g", "owner", oversized)
        assert store.list_group_words("g") == []
        store.close()

    asyncio.run(scenario())


def test_group_filter_list_is_paginated_with_sequential_replies(tmp_path) -> None:
    async def scenario() -> None:
        store, _word_filter, service = _service(tmp_path)
        terms = tuple(f"term-{index:03d}-" + "x" * 70 for index in range(100))
        await service.replace_terms("g", "owner", terms)
        event = Event("/فیلترها")
        assert await handle_group_word_command(
            event, service, group_id="g", actor_id="owner", text=event.raw_text
        )
        assert len(event.replies) > 1
        assert all(
            sum(2 if ord(char) > 0xFFFF else 1 for char in reply) <= 3500
            for reply in event.replies
        )
        assert event.replies[0].startswith("📋 کلمات فیلتر شده:")
        assert all(reply.startswith("📋 ادامه فهرست فیلترها:") for reply in event.replies[1:])
        joined = "\n".join(event.replies)
        assert all(term in joined for term in terms)
        store.close()

    asyncio.run(scenario())
