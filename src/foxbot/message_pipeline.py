from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from foxbot.filters import FilterHit, WordFilter
from foxbot.revision_gate import MessageRevisionGate


@dataclass(frozen=True, slots=True)
class IncomingMessage:
    group_id: str
    message_id: str
    user_id: str
    text: str
    edited: bool = False
    username: str | None = None


@dataclass(frozen=True, slots=True)
class PipelineResult:
    processed: bool
    violation: FilterHit | None = None
    delete_succeeded: bool | None = None
    delete_authorized: bool | None = None
    error_code: str = ""
    spam_decision: object | None = None
    warning_count: int | None = None


DeleteMessage = Callable[[str, str], Awaitable[bool]]
DeleteGuard = Callable[[IncomingMessage, FilterHit], Awaitable[bool]]


class MessagePipeline:
    """Run new and genuinely edited text through the same active filters."""

    def __init__(
        self,
        word_filter: WordFilter,
        delete_message: DeleteMessage,
        *,
        cache_size: int = 100_000,
        check_banned_words: bool = True,
    ):
        self._filter = word_filter
        self._delete_message = delete_message
        self._revisions = MessageRevisionGate(max_entries=cache_size)
        self.check_banned_words = bool(check_banned_words)

    @property
    def delete_message_callback(self) -> DeleteMessage:
        return self._delete_message

    @property
    def word_filter(self) -> WordFilter:
        return self._filter

    def set_delete_message(self, delete_message: DeleteMessage) -> None:
        """Replace the transport callback with a bounded action-queue wrapper."""
        self._delete_message = delete_message

    async def process(
        self,
        message: IncomingMessage,
        *,
        delete_guard: DeleteGuard | None = None,
    ) -> PipelineResult:
        if not self._revisions.should_process(message.group_id, message.message_id, message.text):
            return PipelineResult(processed=False)
        hit = self._filter.match(
            message.group_id,
            message.text,
            include_global=self.check_banned_words,
        )
        if hit is None:
            return PipelineResult(processed=True)
        if delete_guard is not None:
            try:
                may_delete = await delete_guard(message, hit)
            except Exception:  # noqa: BLE001 - uncertain admin status fails closed
                # An uncertain moderator/admin status must never trigger a
                # destructive automatic action.
                may_delete = False
            if not may_delete:
                return PipelineResult(
                    processed=True,
                    violation=hit,
                    delete_authorized=False,
                )
        # The exact edited message is deleted; an edit is not skipped merely
        # because it arrived within a previous event-deduplication window.
        try:
            deleted = bool(
                await self._delete_message(message.group_id, message.message_id)
            )
        except Exception as exc:  # noqa: BLE001 - keep the message retryable without losing moderation state
            self._revisions.forget(message.group_id, message.message_id)
            return PipelineResult(
                processed=True,
                violation=hit,
                delete_succeeded=False,
                delete_authorized=True,
                error_code=type(exc).__name__,
            )
        if not deleted:
            # Keep failed deletion retryable on a duplicate/update event.
            self._revisions.forget(message.group_id, message.message_id)
        return PipelineResult(
            processed=True,
            violation=hit,
            delete_succeeded=deleted,
            delete_authorized=True,
        )
