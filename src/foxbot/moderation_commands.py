from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from foxbot.domain import MatchMode, ModerationKind
from foxbot.management import MemberManagementService
from foxbot.platform import IncompleteMemberScan

Reply = Callable[[Any, str], Awaitable[None]]


def _report_text(report: Any) -> str:
    lines = [
        f"✅ موفق: {report.succeeded}",
        f"↪️ قبلاً انجام شده: {report.already_done}",
        f"⏭ ردشده: {report.skipped}",
        f"❌ ناموفق: {report.failed}",
    ]
    if report.failures:
        details = [
            f"• {failure.display_name or failure.user_id}: {failure.code}"
            for failure in report.failures[:10]
        ]
        lines.extend(("", *details))
        if len(report.failures) > 10:
            lines.append(f"و {len(report.failures) - 10} مورد دیگر")
    return "📊 گزارش عملیات\n" + "\n".join(lines)


async def handle_moderation_command(
    event: Any,
    service: MemberManagementService,
    *,
    group_id: str | int,
    actor_id: str | int,
    text: str,
    reply: Reply,
) -> bool:
    """Route the existing moderation commands and the added bulk workflows.

    Existing command spelling `ریست اخراجی ها` is retained. The broader
    ``سابقه ها`` report belongs to the admin/history route, which includes
    warnings, mutes and kicks rather than only kick-event rows.
    New bulk actions are staged and require a separate, actor/group-scoped
    confirmation before the service can call the platform.
    """
    value = " ".join((text or "").split())
    if value in {"لیست سکوت", "ریست اخراجی ها", "رفع سکوت همه"} or value.startswith(("تایید رفع سکوت ", "تأیید رفع سکوت ", "تایید عملیات ", "تأیید عملیات ", "لغو عملیات ", "جستجو عضو ", "جستجو بیو ", "سکوت نام ", "سکوت بیو ", "اخراج نام ", "اخراج بیو ")):
        command = value
    else:
        return False

    try:
        if command == "لیست سکوت":
            members = [member async for member in service.iter_active_mutes(group_id, actor_id)]
            if not members:
                await reply(event, "🔇 هیچ کاربری در حال حاضر سکوت فعال ندارد.")
                return True
            rows = [
                f"• {member.display_name or member.user_id} — {member.user_id}"
                for member in members[:50]
            ]
            if len(members) > 50:
                rows.append(f"و {len(members) - 50} کاربر دیگر")
            await reply(event, "🔇 کاربران دارای سکوت فعال:\n" + "\n".join(rows))
            return True

        if command == "ریست اخراجی ها":
            removed = await service.clear_bot_ban_list(group_id, actor_id)
            await reply(event, f"🔗[ {removed} کاربران اخراج شده ] از لیست خارج شد")
            return True

        if command == "رفع سکوت همه":
            plan = await service.prepare_clear_active_mutes(group_id, actor_id)
            if plan.get("status") != "ready" or not plan.get("scan_complete"):
                await reply(event, "❌ فهرست کامل دریافت نشد؛ هیچ کاربری رفع سکوت نشد.")
                return True
            job_id = str(plan["job_id"])
            count = int(plan.get("matched", 0))
            if count == 0:
                await reply(event, "🔇 کاربری برای رفع سکوت پیدا نشد.")
                return True
            await reply(
                event,
                f"⚠️ {count} کاربر برای رفع سکوت آماده‌اند. هیچ تغییری هنوز انجام نشده.\n"
                f"برای تأیید بنویسید: تایید رفع سکوت {job_id}\n"
                f"برای لغو بنویسید: لغو عملیات {job_id}",
            )
            return True

        if command.startswith(("تایید رفع سکوت ", "تأیید رفع سکوت ")):
            prefix = "تایید رفع سکوت " if command.startswith("تایید رفع سکوت ") else "تأیید رفع سکوت "
            job_id = command[len(prefix):].strip()
            report = await service.confirm_clear_active_mutes(
                job_id=job_id, group_id=group_id, actor_id=actor_id
            )
            await reply(event, _report_text(report))
            return True

        if command.startswith(("تایید عملیات ", "تأیید عملیات ")):
            job_id = command.split(maxsplit=2)[-1]
            report = await service.confirm_name_action(
                job_id=job_id, group_id=group_id, actor_id=actor_id
            )
            await reply(event, _report_text(report))
            return True

        if command.startswith("لغو عملیات "):
            job_id = command.split(maxsplit=2)[-1]
            cancelled = await service.cancel_bulk_action(
                job_id=job_id, group_id=group_id, actor_id=actor_id
            )
            await reply(
                event,
                "✅ عملیات لغو شد؛ هیچ تغییری روی اعضا اعمال نشد."
                if cancelled else "⚠️ عملیات پیدا نشد، قبلاً اجرا شده یا قابل لغو نیست.",
            )
            return True

        if command.startswith(("جستجو عضو ", "جستجو بیو ")):
            include_bio = command.startswith("جستجو بیو ")
            query = command.split(maxsplit=2)[-1].strip()
            matches = await service.search_members(
                group_id,
                actor_id,
                query,
                include_bio=include_bio,
                match_mode=MatchMode.CONTAINS,
            )
            label = "نام و بیو" if include_bio else "نام"
            if not matches:
                await reply(event, f"🔎 عضوی با این عبارت در {label} پیدا نشد.")
                return True
            rows = [
                f"• {member.display_name or member.user_id} — {member.user_id}"
                for member in matches[:50]
            ]
            if len(matches) > 50:
                rows.append(f"و {len(matches) - 50} عضو دیگر")
            await reply(event, f"🔎 نتیجهٔ جستجو در {label}:\n" + "\n".join(rows))
            return True

        operation = ModerationKind.MUTE if command.startswith("سکوت ") else ModerationKind.KICK
        include_bio = " بیو " in f" {command} "
        query = command.split(maxsplit=2)[-1].strip()
        plan = await service.prepare_name_action(
            group_id=group_id,
            actor_id=actor_id,
            operation=operation,
            query=query,
            match_mode=MatchMode.CONTAINS,
            include_bio=include_bio,
        )
        if plan.get("status") != "ready" or not plan.get("scan_complete"):
            await reply(event, "❌ اسکن کامل اعضا انجام نشد؛ هیچ اقدامی اجرا نمی‌شود.")
            return True
        job_id = str(plan["job_id"])
        count = int(plan.get("matched", 0))
        if count == 0:
            await reply(event, "🔎 عضوی با این معیار پیدا نشد.")
            return True
        action_name = "سکوت" if operation is ModerationKind.MUTE else "اخراج"
        await reply(
            event,
            f"⚠️ {count} عضو برای «{action_name}» پیدا شدند. هنوز اقدامی انجام نشده.\n"
            f"برای تأیید بنویسید: تایید عملیات {job_id}\n"
            f"برای لغو بنویسید: لغو عملیات {job_id}",
        )
        return True
    except PermissionError:
        if command == "ریست اخراجی ها":
            await reply(event, "❌ فقط مالک ثبت‌شده اجازه استفاده از این دستور را دارد")
        else:
            await reply(event, "❌ فقط مالک یا ادمین همین گروه اجازهٔ این دستور را دارد.")
        return True
    except (IncompleteMemberScan, ValueError, TypeError, RuntimeError) as exc:
        reason = str(exc) or type(exc).__name__
        await reply(event, f"❌ عملیات انجام نشد: {reason}")
        return True
