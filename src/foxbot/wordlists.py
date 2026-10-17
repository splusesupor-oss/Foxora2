from __future__ import annotations

from collections.abc import Iterable

from foxbot.filters import (
    MAX_GROUP_WORD_LIST_BYTES,
    MAX_GROUP_WORD_TERM_LENGTH,
    MAX_GROUP_WORD_TERMS,
    WordFilter,
)
from foxbot.security import AccessController
from foxbot.storage import BotStore


class GroupWordService:
    """Persist and authorize per-group word-list edits without variant synthesis."""

    def __init__(
        self,
        store: BotStore,
        access: AccessController,
        word_filter: WordFilter,
    ) -> None:
        self.store = store
        self.access = access
        self.word_filter = word_filter

    def reload_all(self) -> None:
        self.word_filter.clear_group_terms()
        for group_id in self.store.list_group_word_groups():
            self.word_filter.set_group_terms(
                group_id, self.store.list_group_words(group_id)
            )
        for group_id in self.store.disabled_strict_word_groups():
            self.word_filter.set_global_enabled(group_id, False)

    async def set_strict_words_enabled(
        self, group_id: str | int, actor_id: str | int, enabled: bool
    ) -> None:
        gid, actor = str(group_id), str(actor_id)
        if not self.access.can_manage_explicit_group_roles(gid, actor):
            raise PermissionError("actor is not authorized to manage this group")
        self.store.set_strict_words_enabled(gid, enabled)
        self.word_filter.set_global_enabled(gid, enabled)

    def _reload_group(self, group_id: str) -> tuple[str, ...]:
        terms = tuple(self.store.list_group_words(group_id))
        self.word_filter.set_group_terms(group_id, terms)
        return terms

    async def list_terms(self, group_id: str | int, actor_id: str | int) -> tuple[str, ...]:
        gid, actor = str(group_id), str(actor_id)
        if not self.access.can_manage_explicit_group_roles(gid, actor):
            raise PermissionError("actor is not authorized to manage this group")
        return tuple(self.store.list_group_words(gid))

    async def add_term(
        self, group_id: str | int, actor_id: str | int, term: str
    ) -> tuple[str, ...]:
        terms, _added = await self.add_term_with_result(group_id, actor_id, term)
        return terms

    async def add_term_with_result(
        self, group_id: str | int, actor_id: str | int, term: str
    ) -> tuple[tuple[str, ...], bool]:
        gid, actor = str(group_id), str(actor_id)
        if not self.access.can_manage_explicit_group_roles(gid, actor):
            raise PermissionError("actor is not authorized to manage this group")
        if not isinstance(term, str):
            raise TypeError("group word must be a string")
        if len(term) > MAX_GROUP_WORD_TERM_LENGTH:
            raise ValueError("group word term exceeds the configured length limit")
        cleaned = term.strip()
        if not cleaned:
            raise ValueError("group word cannot be empty")
        added = self.store.add_group_word(gid, cleaned, actor)
        return self._reload_group(gid), added

    async def remove_term(
        self, group_id: str | int, actor_id: str | int, term: str
    ) -> tuple[str, ...]:
        terms, _removed = await self.remove_term_with_result(group_id, actor_id, term)
        return terms

    async def remove_term_with_result(
        self, group_id: str | int, actor_id: str | int, term: str
    ) -> tuple[tuple[str, ...], bool]:
        gid, actor = str(group_id), str(actor_id)
        if not self.access.can_manage_explicit_group_roles(gid, actor):
            raise PermissionError("actor is not authorized to manage this group")
        if not isinstance(term, str):
            raise TypeError("group word must be a string")
        if len(term) > MAX_GROUP_WORD_TERM_LENGTH:
            raise ValueError("group word term exceeds the configured length limit")
        cleaned = term.strip()
        if not cleaned:
            raise ValueError("group word cannot be empty")
        removed = self.store.remove_group_word(gid, cleaned)
        return self._reload_group(gid), removed

    async def replace_terms(
        self,
        group_id: str | int,
        actor_id: str | int,
        terms: Iterable[str],
    ) -> tuple[str, ...]:
        if isinstance(terms, str):
            raise TypeError("group word terms must be an iterable of strings, not one string")
        gid, actor = str(group_id), str(actor_id)
        if not self.access.can_manage_explicit_group_roles(gid, actor):
            raise PermissionError("actor is not authorized to manage this group")
        cleaned: list[str] = []
        seen: set[str] = set()
        total_bytes = 0
        input_count = 0
        for term in terms:
            input_count += 1
            if input_count > MAX_GROUP_WORD_TERMS:
                raise ValueError("group word input exceeds the configured term limit")
            if not isinstance(term, str):
                raise TypeError("group word terms must be strings")
            if len(term) > MAX_GROUP_WORD_TERM_LENGTH:
                raise ValueError("group word term exceeds the configured length limit")
            value = term.strip()
            if not value or value in seen:
                continue
            total_bytes += len(value.encode("utf-8"))
            if total_bytes > MAX_GROUP_WORD_LIST_BYTES:
                raise ValueError("group word list exceeds the configured size limit")
            cleaned.append(value)
            seen.add(value)
        self.store.replace_group_words(gid, cleaned, actor)
        return self._reload_group(gid)
