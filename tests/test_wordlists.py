from __future__ import annotations

import asyncio

from foxbot.filters import (
    WordFilter,
)
from foxbot.security import AccessController, OwnerRegistry
from foxbot.storage import BotStore
from foxbot.wordlists import GroupWordService


def test_group_word_lists_persist_and_stay_scoped(tmp_path) -> None:
    async def scenario() -> None:
        owner_path = tmp_path / "owner.json"
        owner_path.write_text('{"user_id":"owner"}', encoding="utf-8")
        store = BotStore(":memory:")
        store.set_group_owner("g1", "group-owner")

        async def is_admin(group_id: str, user_id: str) -> bool:
            return group_id == "g2" and user_id == "native-admin"

        store.add_group_command_member(
            "g2", "registered_admin", "admin", display_name="Registered Admin"
        )
        access = AccessController(OwnerRegistry(owner_path), store, is_admin)
        word_filter = WordFilter(global_terms=("global-term",))
        service = GroupWordService(store, access, word_filter)
        service.reload_all()

        terms = await service.add_term("g1", "group-owner", "Group Term")
        assert terms == ("Group Term",)
        assert word_filter.match("g1", "a group term appears").scope == "group"
        assert word_filter.match("g1", "global-term").scope == "global"
        assert word_filter.match("g2", "group term") is None
        assert word_filter.match("g2", "global term") is None
        assert word_filter.match("g2", "global-term").scope == "global"

        await service.set_strict_words_enabled("g1", "group-owner", False)
        assert word_filter.match("g1", "global-term") is None
        assert word_filter.match("g1", "group term").scope == "group"
        assert word_filter.match("g2", "global-term").scope == "global"

        assert await service.add_term("g2", "admin", "Other Group") == ("Other Group",)
        assert await service.list_terms("g2", "admin") == ("Other Group",)
        assert await service.replace_terms("g1", "group-owner", ["Exact", "", "Exact", "Variant"]) == (
            "Exact", "Variant"
        )
        assert await service.remove_term("g1", "group-owner", "Exact") == ("Variant",)

        new_filter = WordFilter(global_terms=("global-term",))
        GroupWordService(store, access, new_filter).reload_all()
        assert new_filter.match("g1", "variant").scope == "group"
        assert new_filter.match("g1", "global-term") is None
        assert new_filter.match("g2", "other group").scope == "group"
        assert new_filter.match("g2", "global-term").scope == "global"
        store.close()

    asyncio.run(scenario())


def test_group_word_edits_require_current_group_authorization(tmp_path) -> None:
    async def scenario() -> None:
        owner_path = tmp_path / "owner.json"
        owner_path.write_text('{"user_id":"owner"}', encoding="utf-8")
        store = BotStore(":memory:")

        async def native_admin(_group_id: str, user_id: str) -> bool:
            return user_id == "native-admin"

        service = GroupWordService(
            store,
            AccessController(OwnerRegistry(owner_path), store, native_admin),
            WordFilter(),
        )
        for operation in (
            service.add_term("g", "unauthorized", "word"),
            service.add_term("g", "native-admin", "native-word"),
            service.list_terms("g", "native-admin"),
            service.set_strict_words_enabled("g", "native-admin", False),
        ):
            try:
                await operation
            except PermissionError:
                pass
            else:
                raise AssertionError("unauthorized word-filter access was accepted")
        assert store.list_group_words("g") == []
        assert store.strict_words_enabled("g") is True
        store.close()

    asyncio.run(scenario())
