from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from foxbot.filters import MAX_GROUP_WORD_TERM_LENGTH
from foxbot.wordlists import GroupWordService

_UNAUTHORIZED = "❌ فقط مالک یا ادمین ثبت‌شده اجازه استفاده از فیلترها را دارد"
_GROUP_WORD_PAGE_LIMIT = 3500


def _utf16_code_units(value: str) -> int:
    return sum(2 if ord(char) > 0xFFFF else 1 for char in value)


def _format_group_word_pages(terms: tuple[str, ...]) -> tuple[str, ...]:
    first_header = "📋 کلمات فیلتر شده:\n\n"
    next_header = "📋 ادامه فهرست فیلترها:\n\n"
    pages: list[str] = []
    current = first_header
    for term in terms:
        line = f"{term}\n"
        if (
            _utf16_code_units(current) + _utf16_code_units(line) > _GROUP_WORD_PAGE_LIMIT
            and current != first_header
        ):
            pages.append(current.rstrip())
            current = next_header
        current += line
    if current != first_header:
        pages.append(current.rstrip())
    return tuple(pages)


async def _reply(event: Any, text: str) -> None:
    reply = getattr(event, "reply", None) or getattr(event, "respond", None)
    if not callable(reply):
        raise TypeError("SPlusthon event has no supported reply method")
    result = reply(text)
    if hasattr(result, "__await__"):
        await result


async def handle_group_word_command(
    event: Any,
    word_lists: GroupWordService,
    *,
    group_id: str | int,
    actor_id: str | int,
    text: str,
    reply_callback: Callable[[Any, str], Awaitable[None]] | None = None,
) -> bool:
    """Dispatch the original group's per-group filter commands.

    Command phrases and user-facing responses follow the source bot's
    ``group_words_commands`` interface. The new implementation keeps all edits
    scoped to the current group.
    """
    raw_text = str(text or "")
    input_too_long = len(raw_text) > MAX_GROUP_WORD_TERM_LENGTH + 64
    value = raw_text[:MAX_GROUP_WORD_TERM_LENGTH + 64].strip()
    action: str | None = None
    term = ""
    legacy_alias = False
    if value == "لغو کلمات ممنوعه":
        action = "strict_off"
    elif value == "فعال کلمات ممنوعه":
        action = "strict_on"
    elif value.startswith("/فیلتر "):
        action, term = "add", value[len("/فیلتر "):].strip()
    elif value.startswith("/رفع "):
        action, term = "remove", value[len("/رفع "):].strip()
    elif value == "/فیلترها":
        action = "list"
    elif value[:1] in {"!", "/", "."}:
        command_parts = value[1:].strip().split(maxsplit=1)
        if not command_parts:
            return False
        command = command_parts[0].lower()
        term = command_parts[1].strip() if len(command_parts) > 1 else ""
        if command in {"addword", "addban", "افزودن"}:
            action, legacy_alias = "add", True
        elif command in {"remword", "removeword", "حذف"}:
            action, legacy_alias = "remove", True
        else:
            return False
    else:
        return False

    send = reply_callback or _reply
    if input_too_long and action in {"add", "remove"}:
        await send(event, "❌ فهرست فیلتر این گروه به یکی از محدودیت‌های امن خود رسیده است.")
        return True
    try:
        if action in {"strict_off", "strict_on"}:
            enabled = action == "strict_on"
            await word_lists.set_strict_words_enabled(group_id, actor_id, enabled)
            if enabled:
                await send(
                    event,
                    "✅ حالت سختگیرانه (کلمات ممنوعه) برای این گروه فعال شد.",
                )
            else:
                await send(
                    event,
                    "✅ حالت سختگیرانه (کلمات ممنوعه) برای این گروه خاموش شد.\n"
                    "فیلتر دستی همچنان فعال است.",
                )
            return True

        if action == "list":
            terms = await word_lists.list_terms(group_id, actor_id)
            if terms:
                for page in _format_group_word_pages(terms):
                    await send(event, page)
            else:
                await send(event, "📋 هیچ کلمه‌ای فیلتر نشده")
            return True

        if not term:
            if not legacy_alias:
                # Match the original slash handler: bare /فیلتر or /رفع is
                # ignored, while the prefixed English aliases show their usage.
                return False
            command_name = "!addword" if action == "add" else "!remword"
            await send(event, f"❌ استفاده: {command_name} کلمه")
            return True

        if action == "add":
            _terms, added = await word_lists.add_term_with_result(
                group_id, actor_id, term
            )
            if legacy_alias:
                reply = (
                    f"✅ کلمه '{term}' اضافه شد."
                    if added else "⚠️ این کلمه قبلا وجود دارد."
                )
            else:
                reply = (
                    f"☑️ کلمه «{term}» فیلتر شد"
                    if added else "⚠️ این کلمه قبلا برای این گروه ثبت شده"
                )
            await send(event, reply)
            return True

        _terms, removed = await word_lists.remove_term_with_result(
            group_id, actor_id, term
        )
        if legacy_alias:
            reply = (
                f"✅ کلمه '{term}' حذف شد."
                if removed else "⚠️ کلمه پیدا نشد."
            )
        else:
            reply = (
                f"✅ کلمه «{term}» از فیلتر این گروه حذف شد"
                if removed else "⚠️ این کلمه در فیلترها نیست"
            )
        await send(event, reply)
        return True
    except PermissionError:
        if legacy_alias or action in {"strict_off", "strict_on"}:
            await send(event, "❌ فقط مالک یا ادمین گروه می‌تواند این دستور را اجرا کند")
        else:
            await send(event, _UNAUTHORIZED)
        return True
    except ValueError:
        await send(event, "❌ فهرست فیلتر این گروه به یکی از محدودیت‌های امن خود رسیده است.")
        return True
