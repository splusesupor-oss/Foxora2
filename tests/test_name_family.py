from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import patch

from foxbot.filters import WordFilter
from foxbot.game_commands import PERSIAN_LETTERS
from foxbot.name_family import (
    NAME_FAMILY_CATEGORIES,
    NameFamilyService,
    _category_bank,
    is_static_answer,
    normalize_answer,
    parse_submission,
    playable_name_family_letters,
)
from foxbot.storage import BotStore


def test_name_family_submission_parser_accepts_ordered_and_labeled_forms() -> None:
    ordered = parse_submission("آراد\nاحمدی\nتهران\nسیب\nکتاب\nگربه\nگوگوش")
    assert ordered is not None
    assert tuple(ordered) == NAME_FAMILY_CATEGORIES

    labeled = parse_submission(
        "خواننده: گوگوش\nحیوان: گربه\nوسیله: کتاب\nمیوه: سیب\n"
        "شهر: تهران\nفامیل: احمدی\nنام: آراد"
    )
    assert labeled == ordered
    assert parse_submission("نام: آراد\nنام: علی") is None
    assert parse_submission("فقط یک پاسخ") is None


def test_name_family_static_lexicon_uses_global_filter_without_echoing_matches() -> None:
    word_filter = WordFilter(WordFilter.read_file("config/banned_words.txt"))
    payloads = [value for values in _category_bank().values() for value in values]
    assert sum(word_filter.match_global(value) is not None for value in payloads) == 0

    city = next(value for value in _category_bank()["شهر"] if value)
    assert is_static_answer(
        category="شهر",
        letter=city[0],
        answer=city,
        word_filter=word_filter,
    )
    assert not is_static_answer(
        category="میوه",
        letter=city[0],
        answer=city,
        word_filter=word_filter,
    )


def test_name_family_round_bank_covers_reference_letters_with_distinct_answers() -> None:
    word_filter = WordFilter(WordFilter.read_file("config/banned_words.txt"))
    assert len(PERSIAN_LETTERS) == 32
    assert len(set(PERSIAN_LETTERS)) == 32
    letters = playable_name_family_letters(PERSIAN_LETTERS, word_filter)
    reference_safe_letters = tuple("ابپتجدرزشعفگلمنهی")
    assert set(reference_safe_letters).issubset(letters)

    banks = _category_bank()
    reference_counts = (50, 38, 37, 28, 37, 27, 25)
    local_counts = tuple(len(banks[category]) for category in NAME_FAMILY_CATEGORIES)
    assert all(
        local_count >= reference_count
        for local_count, reference_count in zip(local_counts, reference_counts, strict=True)
    ), "the local name-family lexicon must not be smaller than the inspected snapshot"

    for letter in letters:
        choices = [
            {
                normalize_answer(answer)
                for answer in banks[category]
                if normalize_answer(answer).startswith(normalize_answer(letter))
                and word_filter.match_global(answer) is None
            }
            for category in NAME_FAMILY_CATEGORIES
        ]
        assert all(choices), "each selected letter must cover every category safely"
        choices.sort(key=len)

        def has_distinct_answers(
            index: int,
            used: frozenset[str],
            candidates: list[set[str]],
        ) -> bool:
            if index == len(candidates):
                return True
            return any(
                answer not in used
                and has_distinct_answers(index + 1, used | {answer}, candidates)
                for answer in candidates[index]
            )

        assert has_distinct_answers(0, frozenset(), choices), (
            "each selected letter must admit seven distinct category answers"
        )


def test_external_name_family_answers_learn_only_after_distinct_thresholds() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        external_calls: list[tuple[str, str]] = []

        def confirm(category: str, answer: str) -> bool:
            external_calls.append((category, answer))
            return category == "شهر" and answer == "Atlas"

        service = NameFamilyService(WordFilter(()), external_validator=confirm)
        observations = (
            ("g1", "u1"),
            ("g1", "u2"),
            ("g2", "u1"),
            ("g2", "u3"),
            ("g2", "u4"),
        )
        for group_id, user_id in observations:
            assert await service.is_valid(
                store,
                category="شهر",
                letter="A",
                answer="Atlas",
                group_id=group_id,
                user_id=user_id,
            )
        assert store.name_family_learning_counts(
            category="شهر", letter="a", normalized_answer="atlas"
        ) == (5, 4, 2)
        assert len(external_calls) == 1  # shared positive cache, not repeated provider traffic

        assert await service.is_valid(
            store,
            category="شهر",
            letter="A",
            answer="Atlas",
            group_id="g3",
            user_id="u5",
        )
        assert len(external_calls) == 1
        store.close()

    asyncio.run(scenario())


def test_name_family_external_validation_fails_closed_and_filter_precedes_lookup() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        calls = 0

        def fail(_category: str, _answer: str) -> bool:
            nonlocal calls
            calls += 1
            raise RuntimeError("provider unavailable")

        service = NameFamilyService(WordFilter(("blocked",)), external_validator=fail)
        assert not await service.is_valid(
            store,
            category="شهر",
            letter="b",
            answer="blocked",
            group_id="g",
            user_id="u",
        )
        assert calls == 0
        assert not await service.is_valid(
            store,
            category="شهر",
            letter="a",
            answer="Atlas",
            group_id="g",
            user_id="u",
        )
        assert calls == 1
        assert store.name_family_learning_counts(
            category="شهر", letter="a", normalized_answer="atlas"
        ) == (0, 0, 0)
        store.close()

    asyncio.run(scenario())


def test_public_lookup_uses_exact_labels_and_category_hints_with_fallback() -> None:
    service = NameFamilyService(WordFilter(()))
    with patch.object(
        service,
        "_request_json",
        side_effect=[{"search": [{"label": "Atlas", "description": "city"}]}],
    ) as request_json:
        assert service._lookup_category("شهر", "Atlas")
        assert request_json.call_count == 1

    with patch.object(
        service,
        "_request_json",
        side_effect=[
            None,
            {"query": {"search": [{"title": "Atlas", "snippet": "A city in the region."}]}},
        ],
    ) as request_json:
        assert service._lookup_category("شهر", "Atlas")
        assert request_json.call_count == 2

    with patch.object(
        service,
        "_request_json",
        side_effect=[
            {"search": [{"label": "Atlas", "description": "singer"}]},
            {"query": {"search": [{"title": "Atlas", "snippet": "A popular musician."}]}},
        ],
    ) as request_json:
        assert not service._lookup_category("شهر", "Atlas")
        assert request_json.call_count == 2


def test_name_family_external_lookups_are_serialized() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        guard = threading.Lock()
        active = 0
        peak = 0
        calls = 0

        def confirm(_category: str, _answer: str) -> bool:
            nonlocal active, peak, calls
            with guard:
                active += 1
                calls += 1
                peak = max(peak, active)
            time.sleep(0.01)
            with guard:
                active -= 1
            return True

        service = NameFamilyService(WordFilter(()), external_validator=confirm)
        answers = ("Atlas", "Athens", "Albania")
        results = await asyncio.gather(
            *(
                service.is_valid(
                    store,
                    category="شهر",
                    letter="A",
                    answer=answer,
                    group_id="g",
                    user_id=f"u{index}",
                )
                for index, answer in enumerate(answers)
            )
        )
        assert results == [True, True, True]
        assert calls == 3
        assert peak == 1
        store.close()

    asyncio.run(scenario())
