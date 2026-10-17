from __future__ import annotations

import asyncio
from pathlib import Path

from foxbot.filters import WordFilter
from foxbot.security import AccessController, OwnerRegistry
from foxbot.storage import BotStore
from foxbot.word_commands import handle_group_word_command
from foxbot.wordlists import GroupWordService


class Event:
    def __init__(self, text: str, *, actor_id: str = "owner") -> None:
        self.raw_text = text
        self.sender_id = actor_id
        self.replies: list[str] = []

    async def reply(self, text: str) -> None:
        self.replies.append(text)


class Harness:
    def __init__(self, tmp_path: Path) -> None:
        self.store = BotStore(":memory:")
        owner_path = tmp_path / "owner.json"
        owner_path.write_text('{"user_id":"global-owner"}', encoding="utf-8")
        self.store.set_group_owner("g", "owner")

        async def is_native_admin(_group_id: str, user_id: str) -> bool:
            return user_id == "native-admin"

        access = AccessController(OwnerRegistry(owner_path), self.store, is_native_admin)
        self.word_filter = WordFilter(global_terms=("global-only",))
        self.service = GroupWordService(self.store, access, self.word_filter)

    async def dispatch(self, event: Event) -> bool:
        return await handle_group_word_command(
            event,
            self.service,
            group_id="g",
            actor_id=event.sender_id,
            text=event.raw_text,
        )


def test_slash_group_filter_add_remove_and_list_commands(tmp_path) -> None:
    async def scenario() -> None:
        app = Harness(tmp_path)
        add = Event("/فیلتر bad phrase")
        assert await app.dispatch(add)
        assert add.replies[-1] == "☑️ کلمه «bad phrase» فیلتر شد"

        listing = Event("/فیلترها")
        assert await app.dispatch(listing)
        assert listing.replies[-1] == "📋 کلمات فیلتر شده:\n\nbad phrase"

        remove = Event("/رفع bad phrase")
        assert await app.dispatch(remove)
        assert remove.replies[-1] == "✅ کلمه «bad phrase» از فیلتر این گروه حذف شد"
        assert await app.service.list_terms("g", "owner") == ()
        app.store.close()

    asyncio.run(scenario())


def test_legacy_filter_aliases_add_and_remove_without_touching_global_words(tmp_path) -> None:
    async def scenario() -> None:
        app = Harness(tmp_path)
        for command, term in (
            ("!addword alpha", "alpha"),
            (".addban beta", "beta"),
            ("/افزودن gamma", "gamma"),
        ):
            event = Event(command)
            assert await app.dispatch(event)
            assert event.replies[-1] == f"✅ کلمه '{term}' اضافه شد."

        assert await app.service.list_terms("g", "owner") == ("alpha", "beta", "gamma")
        assert app.word_filter.match("g", "global-only").scope == "global"
        assert app.word_filter.match("other", "alpha") is None

        for command, term in (
            ("!remword alpha", "alpha"),
            (".removeword beta", "beta"),
            ("/حذف gamma", "gamma"),
        ):
            event = Event(command)
            assert await app.dispatch(event)
            assert event.replies[-1] == f"✅ کلمه '{term}' حذف شد."
        assert await app.service.list_terms("g", "owner") == ()
        app.store.close()

    asyncio.run(scenario())


def test_banned_word_strict_toggle_is_separate_from_manual_group_filter(tmp_path) -> None:
    async def scenario() -> None:
        app = Harness(tmp_path)
        add = Event("/فیلتر manual")
        assert await app.dispatch(add)
        strict_off = Event("لغو کلمات ممنوعه")
        assert await app.dispatch(strict_off)
        assert "خاموش شد" in strict_off.replies[-1]
        assert app.word_filter.match("g", "global-only") is None
        assert app.word_filter.match("g", "manual").scope == "group"

        strict_on = Event("فعال کلمات ممنوعه")
        assert await app.dispatch(strict_on)
        assert "فعال شد" in strict_on.replies[-1]
        assert app.word_filter.match("g", "global-only").scope == "global"
        app.store.close()

    asyncio.run(scenario())


def test_group_filter_commands_reject_non_admin_and_wrong_group_changes(tmp_path) -> None:
    async def scenario() -> None:
        app = Harness(tmp_path)
        for actor_id in ("member", "native-admin"):
            denied = Event("!addword forbidden", actor_id=actor_id)
            assert await app.dispatch(denied)
            assert denied.replies[-1] == "❌ فقط مالک یا ادمین گروه می‌تواند این دستور را اجرا کند"
        assert await app.service.list_terms("g", "owner") == ()
        app.store.close()

    asyncio.run(scenario())
