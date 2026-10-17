from __future__ import annotations

import asyncio

from foxbot.domain import Member, ModerationKind, OperationReport
from foxbot.moderation_commands import handle_moderation_command


class FakeManagement:
    def __init__(self) -> None:
        self.confirmed_unmute: list[str] = []
        self.prepared_action: dict[str, object] | None = None
        self.confirmed_action: list[str] = []
        self.cancelled: list[str] = []
        self.search_args: tuple[object, ...] | None = None

    async def prepare_clear_active_mutes(self, group_id, actor_id):
        return {
            "job_id": "unmute-job",
            "status": "ready",
            "scan_complete": 1,
            "matched": 2,
        }

    async def confirm_clear_active_mutes(self, *, job_id, group_id, actor_id):
        self.confirmed_unmute.append(job_id)
        return OperationReport(group_id=str(group_id), operation="unmute_all", succeeded=1, failed=1)

    async def confirm_name_action(self, *, job_id, group_id, actor_id):
        self.confirmed_action.append(job_id)
        return OperationReport(group_id=str(group_id), operation="mute", succeeded=1)

    async def cancel_bulk_action(self, *, job_id, group_id, actor_id):
        self.cancelled.append(job_id)
        return True

    async def prepare_name_action(self, **kwargs):
        self.prepared_action = kwargs
        return {
            "job_id": "name-job",
            "status": "ready",
            "scan_complete": 1,
            "matched": 1,
        }

    async def search_members(self, group_id, actor_id, query, *, include_bio, match_mode):
        self.search_args = (group_id, actor_id, query, include_bio, match_mode)
        return [Member(group_id=str(group_id), user_id="u1", display_name="Rose")]


def test_bulk_unmute_command_requires_an_explicit_second_confirmation() -> None:
    async def scenario() -> None:
        service = FakeManagement()
        replies: list[str] = []

        async def reply(_event, text):
            replies.append(text)

        event = object()
        handled = await handle_moderation_command(
            event, service, group_id="g", actor_id="admin", text="رفع سکوت همه", reply=reply
        )
        assert handled is True
        assert service.confirmed_unmute == []
        assert "هیچ تغییری هنوز انجام نشده" in replies[-1]
        assert "تایید رفع سکوت unmute-job" in replies[-1]

        await handle_moderation_command(
            event,
            service,
            group_id="g",
            actor_id="admin",
            text="تایید رفع سکوت unmute-job",
            reply=reply,
        )
        assert service.confirmed_unmute == ["unmute-job"]
        assert "✅ موفق: 1" in replies[-1]
        assert "❌ ناموفق: 1" in replies[-1]

    asyncio.run(scenario())


def test_name_bio_commands_prepare_then_confirm_and_search_bio_is_read_only() -> None:
    async def scenario() -> None:
        service = FakeManagement()
        replies: list[str] = []

        async def reply(_event, text):
            replies.append(text)

        event = object()
        await handle_moderation_command(
            event,
            service,
            group_id="g",
            actor_id="admin",
            text="اخراج بیو Rose",
            reply=reply,
        )
        assert service.prepared_action["operation"] is ModerationKind.KICK
        assert service.prepared_action["include_bio"] is True
        assert service.prepared_action["query"] == "Rose"
        assert "اقدامی انجام نشده" in replies[-1]

        await handle_moderation_command(
            event,
            service,
            group_id="g",
            actor_id="admin",
            text="تایید عملیات name-job",
            reply=reply,
        )
        assert service.confirmed_action == ["name-job"]

        await handle_moderation_command(
            event,
            service,
            group_id="g",
            actor_id="admin",
            text="جستجو بیو Rose",
            reply=reply,
        )
        assert service.search_args[3] is True
        assert "Rose — u1" in replies[-1]

    asyncio.run(scenario())
