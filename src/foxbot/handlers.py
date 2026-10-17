from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

from foxbot.domain import MatchMode, Member, ModerationKind, OperationReport
from foxbot.management import MemberManagementService
from foxbot.wordlists import GroupWordService


@dataclass(frozen=True, slots=True)
class CommandContext:
    """Trusted event scope supplied by the SPlusthon event adapter.

    group_id must come from the incoming event's chat, never from message text.
    actor_id must come from the authenticated sender attached to that event.
    """

    group_id: str
    actor_id: str


class ManagementHandlers:
    """Thin event-facing facade for the group-scoped management services.

    Legacy command routing is kept at the SPlusthon event bridge so it can
    preserve the source bot's phrases while this facade delegates authorization
    and scope checks to the underlying services.
    """

    def __init__(
        self,
        service: MemberManagementService,
        word_lists: GroupWordService | None = None,
    ) -> None:
        self.service = service
        self.word_lists = word_lists

    def _word_lists(self) -> GroupWordService:
        if self.word_lists is None:
            raise RuntimeError("group word lists are not configured")
        return self.word_lists

    async def iter_active_mutes(self, context: CommandContext) -> AsyncIterator[Member]:
        async for member in self.service.iter_active_mutes(
            context.group_id, context.actor_id
        ):
            yield member

    async def search_members(
        self,
        context: CommandContext,
        query: str,
        *,
        include_bio: bool = False,
        match_mode: MatchMode = MatchMode.CONTAINS,
    ) -> list[Member]:
        return await self.service.search_members(
            context.group_id,
            context.actor_id,
            query,
            include_bio=include_bio,
            match_mode=match_mode,
        )

    async def prepare_clear_mutes(self, context: CommandContext) -> dict[str, object]:
        return await self.service.prepare_clear_active_mutes(
            context.group_id, context.actor_id
        )

    async def confirm_clear_mutes(
        self, context: CommandContext, job_id: str
    ) -> OperationReport:
        return await self.service.confirm_clear_active_mutes(
            job_id=job_id,
            group_id=context.group_id,
            actor_id=context.actor_id,
        )

    async def prepare_name_action(
        self,
        context: CommandContext,
        *,
        operation: ModerationKind,
        query: str,
        match_mode: MatchMode = MatchMode.CONTAINS,
        include_bio: bool = False,
    ) -> dict[str, object]:
        return await self.service.prepare_name_action(
            group_id=context.group_id,
            actor_id=context.actor_id,
            operation=operation,
            query=query,
            match_mode=match_mode,
            include_bio=include_bio,
        )

    async def confirm_name_action(
        self, context: CommandContext, job_id: str
    ) -> OperationReport:
        return await self.service.confirm_name_action(
            job_id=job_id,
            group_id=context.group_id,
            actor_id=context.actor_id,
        )

    async def cancel_bulk_action(self, context: CommandContext, job_id: str) -> bool:
        return await self.service.cancel_bulk_action(
            job_id=job_id,
            group_id=context.group_id,
            actor_id=context.actor_id,
        )

    async def list_kick_history(
        self,
        context: CommandContext,
        *,
        limit: int = 100,
        offset: int = 0,
        include_unverified: bool = False,
    ) -> list[dict[str, object]]:
        return await self.service.list_kick_history(
            context.group_id,
            context.actor_id,
            limit=limit,
            offset=offset,
            include_unverified=include_unverified,
        )

    async def clear_internal_ban_list(self, context: CommandContext) -> int:
        return await self.service.clear_bot_ban_list(
            context.group_id, context.actor_id
        )

    async def list_group_words(self, context: CommandContext) -> tuple[str, ...]:
        return await self._word_lists().list_terms(
            context.group_id, context.actor_id
        )

    async def add_group_word(
        self, context: CommandContext, term: str
    ) -> tuple[str, ...]:
        return await self._word_lists().add_term(
            context.group_id, context.actor_id, term
        )

    async def remove_group_word(
        self, context: CommandContext, term: str
    ) -> tuple[str, ...]:
        return await self._word_lists().remove_term(
            context.group_id, context.actor_id, term
        )

    async def replace_group_words(
        self, context: CommandContext, terms: list[str]
    ) -> tuple[str, ...]:
        return await self._word_lists().replace_terms(
            context.group_id, context.actor_id, terms
        )
