from __future__ import annotations

import asyncio

import pytest

from foxbot.domain import MatchMode, Member
from foxbot.expiry import InvalidExpiry, parse_expiry_days
from foxbot.filters import WordFilter
from foxbot.matching import name_matches, normalize_persian
from foxbot.message_pipeline import IncomingMessage, MessagePipeline
from foxbot.messages import MessageIntent, MissingMessageTemplate, TemplateRenderer
from foxbot.revision_gate import MessageRevisionGate


def test_persian_normalization_and_name_bio_matching() -> None:
    assert normalize_persian("علي‌رضا كاظمي") == "علی رضا کاظمی"
    member = Member(
        group_id="g",
        user_id="1",
        display_name="علی رضا",
        bio="آموزش پایتون",
        bio_available=True,
    )
    assert name_matches(member, "علي", MatchMode.CONTAINS)
    assert name_matches(member, "علی رضا", MatchMode.EXACT)
    assert name_matches(member, "پایتون", MatchMode.CONTAINS, include_bio=True)
    assert not name_matches(member, "پایتون", MatchMode.CONTAINS, include_bio=False)


def test_expiry_parses_persian_and_arabic_digits_in_range() -> None:
    assert parse_expiry_days("۴۰ روز") == 40
    assert parse_expiry_days("٠١ day") == 1
    with pytest.raises(InvalidExpiry):
        parse_expiry_days("0")
    with pytest.raises(InvalidExpiry):
        parse_expiry_days("41 days")
    with pytest.raises(InvalidExpiry):
        parse_expiry_days("1.5")


def test_word_filter_keeps_global_and_group_scopes_separate() -> None:
    words = WordFilter(global_terms=("جهانی", ""))
    words.set_group_terms("g1", ("گروهی",))
    assert words.match("g1", "این متن گروهی است").scope == "group"
    assert words.match("g2", "این متن جهانی است").scope == "global"
    assert words.match("g2", "جهانیی") is not None
    assert words.match("g2", "جهانیب") is None
    assert words.match("g2", "گروهی") is None
    assert words.global_terms == ("جهانی",)


def test_global_only_match_ignores_group_terms_and_group_toggles() -> None:
    words = WordFilter(global_terms=("global-only",))
    words.set_group_terms("g", ("group-only",))
    words.set_global_enabled("g", False)

    assert words.match("g", "global-only") is None
    assert words.match("g", "group-only").scope == "group"
    assert words.match_global("global-only").scope == "global"
    assert words.match_global("group-only") is None


def test_global_word_filter_matches_source_obfuscations_without_adding_terms() -> None:
    terms = ("bad word", "test", "سلام")
    words = WordFilter(global_terms=terms)

    punctuated_phrase = words.match("g", "b.a_d---w.o.r.d")
    assert punctuated_phrase is not None
    assert punctuated_phrase.scope == "global"
    assert punctuated_phrase.original_term == "bad word"
    assert words.match("g", "teeeeest").original_term == "test"
    assert words.match("g", "صلام").original_term == "سلام"
    assert words.match("g", "badword") is None
    assert words.global_terms == terms


def test_global_word_filter_bounds_repetitive_obfuscation_matching() -> None:
    words = WordFilter(global_terms=("aaaaab",))
    assert words.match("g", "a" * 8000) is None


def test_group_word_filter_uses_whole_tokens_and_source_text_normalization() -> None:
    words = WordFilter()
    words.set_group_terms("g", ("ممنوعه", "bad-word", "tag_name", "ک"))

    assert words.match("g", "غیرممنوعه") is None
    assert words.match("g", "این ممنوعه،است").original_term == "ممنوعه"
    assert words.match("g", "bad/word").original_term == "bad-word"
    assert words.match("g", "BAD-WORD").original_term == "bad-word"
    assert words.match("g", "tag name").original_term == "tag_name"
    assert words.match("g", "مـمَنوعه").original_term == "ممنوعه"
    assert words.match("g", "کلمه") is None
    assert words.match("g", "ک").original_term == "ک"


def test_global_word_filter_can_be_disabled_per_group_without_disabling_manual_terms() -> None:
    words = WordFilter(global_terms=("global term",))
    words.set_group_terms("g1", ("group term",))
    words.set_global_enabled("g1", False)
    assert words.match("g1", "global term") is None
    assert words.match("g1", "group term").scope == "group"
    assert words.match("g2", "global term").scope == "global"


def test_short_banned_terms_match_whole_tokens_only() -> None:
    words = WordFilter(global_terms=("ک", "پی", "چک", "کس", "جق"))
    assert words.match("g", "این کلمه عادی است") is None
    assert words.match("g", "پیام عادی") is None
    assert words.match("g", "چکیده") is None
    assert words.match("g", "ک") is not None
    assert words.match("g", "برای پی بیا") is not None
    assert words.match("g", "بیو چک") is not None
    assert words.match("g", "کسش") is None
    assert words.match("g", "این جق است") is not None


def test_revision_gate_admits_real_edits_and_bounds_cache() -> None:
    gate = MessageRevisionGate(max_entries=2)
    assert gate.should_process("g", "1", "first")
    assert not gate.should_process("g", "1", "first")
    assert gate.should_process("g", "1", "edited")
    assert gate.should_process("g", "2", "two")
    assert gate.should_process("g", "3", "three")
    # Entry 1 was evicted; the bounded cache admits it again.
    assert gate.should_process("g", "1", "edited")

    large_payload = "😀x" * 10_000
    assert gate.should_process("g", "large", large_payload)
    assert not gate.should_process("g", "large", large_payload)
    assert gate.should_process("g", "large", large_payload[:-1] + "y")
    assert gate.should_process("g", "surrogate", "\ud800")


def test_pipeline_retries_failed_delete_and_dedupes_after_success() -> None:
    async def scenario() -> None:
        attempts = 0

        async def delete(_group_id: str, _message_id: str) -> bool:
            nonlocal attempts
            attempts += 1
            return attempts > 1

        pipeline = MessagePipeline(WordFilter(global_terms=("ممنوع",)), delete)
        message = IncomingMessage("g", "m1", "u1", "این ممنوع است")
        first = await pipeline.process(message)
        second = await pipeline.process(message)
        third = await pipeline.process(message)
        assert first.processed and first.delete_succeeded is False
        assert second.processed and second.delete_succeeded is True
        assert not third.processed
        assert attempts == 2

    asyncio.run(scenario())


def test_pipeline_global_word_toggle_preserves_manual_group_terms() -> None:
    async def scenario() -> None:
        deleted: list[str] = []

        async def delete(_group_id: str, message_id: str) -> bool:
            deleted.append(message_id)
            return True

        word_filter = WordFilter(("global-marker",))
        word_filter.set_group_terms("g", ("local-marker",))
        pipeline = MessagePipeline(
            word_filter,
            delete,
            check_banned_words=False,
        )
        global_result = await pipeline.process(
            IncomingMessage("g", "1", "u", "global-marker")
        )
        local_result = await pipeline.process(
            IncomingMessage("g", "2", "u", "local-marker")
        )
        assert global_result.violation is None
        assert local_result.violation is not None
        assert local_result.violation.scope == "group"
        assert deleted == ["2"]

    asyncio.run(scenario())


def test_pipeline_does_not_delete_nonmatching_or_duplicate_message() -> None:
    async def scenario() -> None:
        deleted: list[tuple[str, str]] = []

        async def delete(group_id: str, message_id: str) -> bool:
            deleted.append((group_id, message_id))
            return True

        pipeline = MessagePipeline(WordFilter(("blocked",)), delete)
        assert (await pipeline.process(IncomingMessage("g", "1", "u", "hello"))).processed
        assert not (await pipeline.process(IncomingMessage("g", "1", "u", "hello"))).processed
        assert deleted == []

    asyncio.run(scenario())


def test_template_renderer_requires_owner_provided_copy() -> None:
    renderer = TemplateRenderer()
    with pytest.raises(MissingMessageTemplate):
        renderer.render(MessageIntent("admin.success", {"count": 1}))
    renderer = TemplateRenderer({"admin.success": "Done: {count}"})
    assert renderer.render(MessageIntent("admin.success", {"count": 3})) == "Done: 3"
    with pytest.raises(ValueError):
        renderer.render(MessageIntent("admin.success", {}))
