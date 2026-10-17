from __future__ import annotations

import asyncio
from types import SimpleNamespace

from foxbot import fun_content, game_commands, survival_questions
from foxbot.battle_questions import BATTLE_QUESTIONS
from foxbot.filters import WordFilter
from foxbot.fun_content import DARES, JOKES, TRUTHS
from foxbot.public_commands import handle_public_command
from foxbot.storage import BotStore
from foxbot.who_knows_bank import WHO_KNOWS_BANK


class Event:
    def __init__(self, text: str, actor_id: str = "user") -> None:
        self.raw_text = text
        self.sender_id = actor_id
        self.sender = SimpleNamespace(id=actor_id, first_name="Test", last_name="Player", username="testplayer")
        self.replies: list[str] = []


def test_who_knows_category_banks_are_unique_and_playable() -> None:
    assert len(game_commands.WHO_KNOWS_CATEGORIES) == 7
    for category, words in game_commands.WHO_KNOWS_BANK.items():
        normalized = [game_commands._normalize_answer(word) for word in words]
        assert len(words) >= 40, category
        assert all(len(word) >= 2 for word in normalized), category
        assert len(normalized) == len(set(normalized)), category
    assert game_commands.WHO_KNOWS_ROUNDS
    assert all(answers for _letter, _category, answers in game_commands.WHO_KNOWS_ROUNDS)


def test_sentence_build_scramble_has_a_bounded_reorder_fallback(monkeypatch) -> None:
    class SameOrder:
        def __init__(self) -> None:
            self.calls = 0

        def sample(self, population, count):
            self.calls += 1
            return list(population)

    randomizer = SameOrder()
    monkeypatch.setattr(game_commands.secrets, "SystemRandom", lambda: randomizer)
    assert game_commands._scramble_sentence_words("alpha beta gamma") == [
        "beta",
        "alpha",
        "gamma",
    ]
    assert randomizer.calls == 8
    assert game_commands._scramble_sentence_words("same same") == ["same", "same"]
    assert game_commands._scramble_sentence_words("single") == ["single"]


def test_routed_fun_and_game_payloads_pass_global_word_filter() -> None:
    word_filter = WordFilter(WordFilter.read_file("config/banned_words.txt"))

    def values(item):
        if isinstance(item, str):
            yield item
        elif isinstance(item, dict):
            for nested in item.values():
                yield from values(nested)
        elif isinstance(item, (tuple, list, set, frozenset)):
            for nested in item:
                yield from values(nested)

    expected_bank_sizes = (
        (fun_content.BIOGRAPHIES, 90, "biographies"),
        (fun_content.FACTS, 100, "facts"),
        (fun_content.DARES, 100, "dares"),
        (fun_content.TRUTHS, 70, "truths"),
        (fun_content.JOKES, 80, "jokes"),
        (game_commands.RIDDLES, 210, "riddles"),
        (game_commands.MAEMMA_PUZZLES, 802, "independent maemma bank"),
        (game_commands.EMOJI_PUZZLES, 851, "emoji puzzles"),
        (game_commands.FILL_BLANKS, 216, "fill blanks"),
        (game_commands.FLAGS, 387, "flags"),
        (game_commands.CORRECTIONS, 240, "corrections"),
        (game_commands.QUIZZES, 1125, "quizzes"),
        (game_commands.QUIZ_METADATA, 450, "quiz topic and difficulty labels"),
        (game_commands.TRUTH_LIE, 317, "truth or lie"),
        (game_commands.WHO_KNOWS_CATEGORIES, 7, "who-knows categories"),
        (game_commands.SENTENCES, 320, "sentences"),
        (game_commands.SENTENCE_GUESS_PUZZLES, 640, "sentence-guess puzzles"),
        (game_commands.BEST_QUESTIONS, 1080, "best-answer questions"),
        (game_commands.KARAGAH_OBJECTS, 50, "Karagah objects"),
        (WHO_KNOWS_BANK, 446, "who-knows accepted words"),
        (survival_questions.LEVELS, 825, "survival questions"),
        (BATTLE_QUESTIONS, 653, "battle questions and aliases"),
    )
    for bank, expected_size, label in expected_bank_sizes:
        assert sum(1 for _ in values(bank)) == expected_size, f"{label} scope changed"

    assert len(game_commands.RIDDLES) == 100
    assert len(game_commands.MAEMMA_PUZZLES) == 222
    maemma_questions = {
        game_commands._normalize_answer(prompt)
        for prompt, _answers in game_commands.MAEMMA_PUZZLES
    }
    riddle_questions = {
        game_commands._normalize_answer(prompt)
        for prompt, _answers in game_commands.RIDDLES
    }
    assert len(maemma_questions) == 222
    assert maemma_questions.isdisjoint(riddle_questions)
    assert len(game_commands.EMOJI_PUZZLES) == 400
    assert len(game_commands.QUIZZES) == 225
    assert len(game_commands.QUIZ_METADATA) == 225
    assert len({category for category, _level in game_commands.QUIZ_METADATA}) == 32
    assert len({level for _category, level in game_commands.QUIZ_METADATA}) == 2
    assert len(game_commands.TRUTH_LIE) == 317
    assert len(game_commands.SENTENCES) == 320
    assert len(game_commands.SENTENCE_GUESS_PUZZLES) == 320
    assert len(BATTLE_QUESTIONS) == 190
    assert len(survival_questions.all_questions()) == 205

    maemma_answer_slots = sum(
        len({game_commands._normalize_answer(answer) for answer in answers})
        for _prompt, answers in game_commands.MAEMMA_PUZZLES
    )
    flag_answer_slots = sum(
        len({game_commands._normalize_answer(answer) for answer in answers})
        for _flag, answers in game_commands.FLAGS
    )
    battle_answer_slots = sum(
        len({game_commands._normalize_answer(answer) for answer in (answer, *aliases)})
        for _prompt, answer, aliases in BATTLE_QUESTIONS
    )
    assert maemma_answer_slots == 580
    assert flag_answer_slots == 262
    assert battle_answer_slots == 462

    payloads = []
    for name in ("BIOGRAPHIES", "FACTS", "DARES", "TRUTHS", "JOKES"):
        payloads.extend(values(getattr(fun_content, name)))
    for name in (
        "RIDDLES", "EMOJI_PUZZLES", "FILL_BLANKS", "FLAGS", "CORRECTIONS",
        "QUIZZES", "QUIZ_METADATA", "TRUTH_LIE", "WHO_KNOWS_CATEGORIES", "SENTENCES",
        "SENTENCE_GUESS_PUZZLES", "BEST_QUESTIONS", "KARAGAH_OBJECTS",
    ):
        payloads.extend(values(getattr(game_commands, name)))
    payloads.extend(values(WHO_KNOWS_BANK))
    payloads.extend(values(game_commands.MAEMMA_PUZZLES))
    payloads.extend(values(survival_questions.LEVELS))
    payloads.extend(
        text
        for prompt, answer, aliases in BATTLE_QUESTIONS
        for text in (prompt, answer, *aliases)
    )
    assert len(payloads) == 9059, f"audited bank scope changed: {len(payloads)} strings"
    payload_match_count = sum(
        word_filter.match_global(text) is not None for text in payloads
    )
    assert payload_match_count == 0, f"bank payload matches: {payload_match_count}"

    round_payload_match_count = sum(
        word_filter.match_global(text) is not None
        for _letter, category, answers in game_commands.WHO_KNOWS_ROUNDS
        for text in (category, *answers)
    )
    assert round_payload_match_count == 0, (
        f"derived who-knows payload matches: {round_payload_match_count}"
    )


def test_clean_room_answer_alias_extensions_are_playable() -> None:
    maemma_answers = game_commands.MAEMMA_PUZZLES[100][1]
    assert len(maemma_answers) == 4
    assert all(game_commands._accepted(alias, maemma_answers) for alias in maemma_answers)

    flag_answers = next(answers for flag, answers in game_commands.FLAGS if flag == "🇩🇪")
    assert game_commands._accepted("جرمنی", flag_answers)

    battle_answer, battle_aliases = next(
        (answer, aliases)
        for _prompt, answer, aliases in BATTLE_QUESTIONS
        if answer.isdigit()
        and any(any("٠" <= char <= "٩" for char in alias) for alias in aliases)
    )
    arabic_indic = next(
        alias for alias in battle_aliases
        if any("٠" <= char <= "٩" for char in alias)
    )
    assert game_commands._accepted(
        arabic_indic, (battle_answer, *battle_aliases)
    )


def test_every_survival_and_battle_bank_variant_is_accepted_locally() -> None:
    for _question_id, prompt, answer, aliases in survival_questions.all_questions():
        variants = (answer, *aliases)
        assert prompt.strip() and all(variant.strip() for variant in variants)
        assert all(game_commands._accepted(variant, variants) for variant in variants)

    for prompt, answer, aliases in BATTLE_QUESTIONS:
        variants = (answer, *aliases)
        assert prompt.strip() and all(variant.strip() for variant in variants)
        assert all(
            game_commands._battle_answer_matches(variant, list(variants))
            for variant in variants
        )


def test_survival_and_battle_accept_reference_arabic_letter_normalization() -> None:
    equivalences = (
        ("الف", "ألف"),
        ("اسلام", "إسلام"),
        ("ادم", "آدم"),
        ("مدرسه", "مدرسة"),
        ("مومن", "مؤمن"),
    )
    for accepted_answer, spelling_variant in equivalences:
        assert game_commands._accepted(spelling_variant, [accepted_answer])
        assert game_commands._battle_answer_matches(
            spelling_variant, [accepted_answer]
        )


def test_timed_game_deadline_refresh_rechecks_session_phase_and_stage() -> None:
    store = BotStore(":memory:")
    key = game_commands._key("battle")
    state = {
        "status": "pending",
        "kind": "battle",
        "token": "current-session",
        "phase": "playing",
        "data": {"phase": "playing", "index": 2},
        "prompt": "Question 2",
        "expires_at": 111.0,
    }
    store.set_group_state("g", key, state)

    assert not game_commands.refresh_timed_game_deadline_after_delivery(
        store, "g", key, token="stale-session", phase="playing", stage=2, now=200,
    )
    assert not game_commands.refresh_timed_game_deadline_after_delivery(
        store, "g", key, token="current-session", phase="playing", stage=1, now=200,
    )
    unchanged = store.get_group_state("g", key)
    assert unchanged["expires_at"] == 111.0
    assert not game_commands.refresh_timed_game_deadline_after_delivery(
        store, "g", key, token="current-session", phase="playing", stage=2,
        expected_prompt="Stale question", now=200,
    )
    assert store.get_group_state("g", key)["expires_at"] == 111.0

    assert game_commands.refresh_timed_game_deadline_after_delivery(
        store, "g", key, token="current-session", phase="playing", stage=2,
        expected_prompt="Question 2", now=200,
    )
    refreshed = store.get_group_state("g", key)
    assert refreshed["expires_at"] == 200 + game_commands._BATTLE_ANSWER_SECONDS
    store.close()


def test_who_knows_selection_skips_global_filtered_round_letters() -> None:
    word_filter = WordFilter(WordFilter.read_file("config/banned_words.txt"))
    has_filtered_letter = any(
        bool(word_filter.match_global(letter))
        for letter, _category, _answers in game_commands.WHO_KNOWS_ROUNDS
    )
    assert has_filtered_letter

    async def scenario() -> None:
        store = BotStore(":memory:")
        start = Event("کی بیشتر بلده")
        assert await dispatch(start, store, word_filter=word_filter)
        state = store.get_group_state("g", game_commands._key("who_knows"))
        letter = state["data"]["letter"]
        assert not bool(word_filter.match_global(letter))
        store.close()

    asyncio.run(scenario())


def test_name_family_selection_skips_global_filtered_round_letters() -> None:
    word_filter = WordFilter(WordFilter.read_file("config/banned_words.txt"))
    has_filtered_letter = any(
        bool(word_filter.match_global(letter))
        for letter in game_commands.PERSIAN_LETTERS
    )
    assert has_filtered_letter

    async def scenario() -> None:
        store = BotStore(":memory:")
        start = Event("اسم فامیل")
        assert await dispatch(start, store, word_filter=word_filter)
        state = store.get_group_state("g", game_commands._key("name_family"))
        letter = state["data"]["letter"]
        assert not bool(word_filter.match_global(letter))
        assert letter in game_commands.playable_name_family_letters(
            game_commands.PERSIAN_LETTERS, word_filter
        )
        store.close()

    asyncio.run(scenario())


def test_name_family_requires_valid_unique_category_answers() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")

        async def reply(event, text, *, formatting_entities=None, prefer_respond=False):
            event.replies.append(text)
            return SimpleNamespace(id=len(event.replies))

        start = Event("اسم فامیل", actor_id="host")
        assert await game_commands.handle_game_command(
            start,
            store,
            group_id="g",
            actor_id="host",
            text=start.raw_text,
            reply=reply,
            word_filter=WordFilter(()),
        )
        key = game_commands._key("name_family")
        state = store.get_group_state("g", key)
        state["data"]["letter"] = "a"
        store.set_group_state("g", key, state)

        class Validator:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str]] = []

            async def is_valid(self, _store, *, category, letter, answer, **_kwargs):
                self.calls.append((category, answer))
                return letter == "a" and answer in {"a1", "a2", "a3", "a4", "a5"}

        validator = Validator()
        first = Event("a1\na2\na3\na4\na5\na6\na7", actor_id="first")
        assert await game_commands.handle_game_command(
            first,
            store,
            group_id="g",
            actor_id="first",
            text=first.raw_text,
            reply=reply,
            name_family_service=validator,
        )
        state = store.get_group_state("g", key)
        assert state["data"]["submissions"]["first"]["score"] == 50
        assert len(state["data"]["submissions"]["first"]["answers"]) == 5

        second = Event("a1\na2\na3\na4\na5\na6\na7", actor_id="second")
        assert await game_commands.handle_game_command(
            second,
            store,
            group_id="g",
            actor_id="second",
            text=second.raw_text,
            reply=reply,
            name_family_service=validator,
        )
        state = store.get_group_state("g", key)
        assert "second" not in state["data"]["submissions"]
        assert len(state["data"]["submissions"]) == 1
        assert len(validator.calls) == 9
        store.close()

    asyncio.run(scenario())


def test_name_family_fails_closed_without_filter_or_validator() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")

        async def reply(event, text, *, formatting_entities=None, prefer_respond=False):
            event.replies.append(text)
            return SimpleNamespace(id=len(event.replies))

        start = Event("اسم فامیل", actor_id="host")
        assert await game_commands.handle_game_command(
            start,
            store,
            group_id="g",
            actor_id="host",
            text=start.raw_text,
            reply=reply,
        )
        assert store.get_group_state("g", game_commands._key("name_family")) is None
        assert len(start.replies) == 1
        store.close()

    asyncio.run(scenario())


def test_name_family_static_fallback_receives_global_filter() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        word_filter = WordFilter(WordFilter.read_file("config/banned_words.txt"))

        async def reply(event, text, *, formatting_entities=None, prefer_respond=False):
            event.replies.append(text)
            return SimpleNamespace(id=len(event.replies))

        start = Event("اسم فامیل", actor_id="host")
        assert await game_commands.handle_game_command(
            start,
            store,
            group_id="g",
            actor_id="host",
            text=start.raw_text,
            reply=reply,
            word_filter=word_filter,
        )
        key = game_commands._key("name_family")
        state = store.get_group_state("g", key)
        state["data"]["letter"] = "پ"
        store.set_group_state("g", key, state)

        submission = Event(
            "پارسا\nپاکزاد\nپلدختر\nپرتقال\nپرچم\nپلنگ\nپرواز همای",
            actor_id="player",
        )
        assert await game_commands.handle_game_command(
            submission,
            store,
            group_id="g",
            actor_id="player",
            text=submission.raw_text,
            reply=reply,
            word_filter=word_filter,
        )
        state = store.get_group_state("g", key)
        assert state["data"]["submissions"]["player"]["score"] == 70
        store.close()

    asyncio.run(scenario())


def test_expanded_game_banks_have_expected_size_and_valid_entries() -> None:
    assert len(game_commands.RIDDLES) == 100
    assert len(game_commands.MAEMMA_PUZZLES) == 222
    assert len(game_commands.EMOJI_PUZZLES) == 400
    assert len(game_commands.FILL_BLANKS) == 90
    assert len(game_commands.FLAGS) == 125
    assert len(game_commands.CORRECTIONS) == 120
    assert len(game_commands.QUIZZES) == 225
    assert len(game_commands.TRUTH_LIE) == 317
    assert len(game_commands.SENTENCES) == 320
    assert len(game_commands.SENTENCE_GUESS_PUZZLES) == 320
    assert len(game_commands.BEST_QUESTIONS) == 180
    assert len(game_commands.KARAGAH_OBJECTS) == 50
    assert (len(DARES), len(TRUTHS), len(JOKES)) == (100, 70, 80)

    def assert_unique(values: list[str]) -> None:
        normalized = [game_commands._normalize_answer(value) for value in values]
        assert all(normalized)
        assert len(normalized) == len(set(normalized))

    for bank in (
        game_commands.RIDDLES,
        game_commands.EMOJI_PUZZLES,
        game_commands.FILL_BLANKS,
        game_commands.FLAGS,
    ):
        assert_unique([row[0] for row in bank])
        assert_unique([row[1][0] for row in bank])
        assert all(row[1] and all(answer for answer in row[1]) for row in bank)
    assert all(prompt.count("___") == 1 for prompt, _answers in game_commands.FILL_BLANKS)

    assert_unique([wrong for wrong, _correct in game_commands.CORRECTIONS])
    for wrong, correct in game_commands.CORRECTIONS:
        assert game_commands._correction_matches(correct, wrong, correct)
        assert not game_commands._correction_matches(wrong, wrong, correct)

    assert_unique([question for question, _options, _correct in game_commands.QUIZZES])
    for _question, options, correct in game_commands.QUIZZES:
        assert len(options) == 4
        assert_unique(list(options))
        assert 1 <= correct <= 4
    assert {correct for _question, _options, correct in game_commands.QUIZZES} == {1, 2, 3, 4}

    assert_unique([statement for statement, _truth in game_commands.TRUTH_LIE])
    assert {truth for _statement, truth in game_commands.TRUTH_LIE} == {True, False}
    assert_unique(list(game_commands.SENTENCES))
    assert_unique([clue for clue, _sentence in game_commands.SENTENCE_GUESS_PUZZLES])
    assert {
        game_commands._normalize_answer(sentence)
        for _clue, sentence in game_commands.SENTENCE_GUESS_PUZZLES
    }.isdisjoint(
        game_commands._normalize_answer(sentence)
        for sentence in game_commands.SENTENCES
    )
    assert_unique([question for question, _keywords in game_commands.BEST_QUESTIONS])
    assert all(len(keywords) >= 3 for _question, keywords in game_commands.BEST_QUESTIONS)
    assert_unique(list(game_commands.KARAGAH_OBJECTS))
    for bank in (DARES, TRUTHS, JOKES):
        assert_unique(list(bank))


def test_correction_answers_preserve_space_vs_half_space() -> None:
    wrong = "می تواند"
    correct = "می\u200cتواند"
    assert game_commands._correction_matches(correct, wrong, correct)
    assert not game_commands._correction_matches(wrong, wrong, correct)
    assert game_commands._correction_matches("تأثیر", "تاثیر", "تأثیر")
    assert not game_commands._correction_matches("تاثیر", "تاثیر", "تأثیر")


def test_spacing_only_correction_rejects_wrong_form_and_awards_correct_form(monkeypatch) -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        monkeypatch.setattr(
            game_commands, "_choose",
            lambda *_args, **_kwargs: (0, ("می تواند", "می\u200cتواند"), []),
        )
        start = Event("تصحیح کلمات")
        assert await dispatch(start, store)
        state_key = game_commands._key("correction")
        state = store.get_group_state("g", state_key)
        assert state["data"]["wrong"] == "می تواند"

        wrong_answer = Event("می تواند")
        assert not await dispatch(wrong_answer, store)
        assert store.get_group_state("g", state_key) is not None
        assert store.get_economy_profile("g", "user")["bronze"] == 0

        correct_answer = Event("می\u200cتواند")
        assert await dispatch(correct_answer, store)
        assert store.get_group_state("g", state_key) is None
        assert store.get_economy_profile("g", "user")["bronze"] == 3
        store.close()

    asyncio.run(scenario())


def test_game_reward_contract_matches_audited_values() -> None:
    assert game_commands._GAME_REWARDS == {
        "riddle": (3, "bronze"), "emoji": (4, "bronze"),
        "flag": (3, "bronze"), "name_family": (6, "bronze"),
        "correction": (3, "bronze"), "quiz": (3, "bronze"),
        "fill_blank": (2, "bronze"), "laugh_or_lose": (3, "bronze"),
        "lucky_box": (0, "bronze"), "maemma": (3, "bronze"),
        "best_answer": (2, "bronze"), "battle": (2, "bronze"),
        "sentence_guess": (3, "bronze"), "minesweeper": (5, "silver"),
        "who_knows": (2, "bronze"), "truth_lie": (2, "bronze"),
        "karagah_thief": (12, "bronze"), "survival": (8, "silver"),
        "survival_step": (1, "silver"), "vampire": (7, "silver"),
        "karagah": (12, "silver"),
    }


async def dispatch(
    event: Event,
    store: BotStore,
    group_id: str = "g",
    private_message_sender=None,
    word_filter: WordFilter | None = None,
) -> bool:
    async def reply(_event, text, *, formatting_entities=None, prefer_respond=False):
        event.replies.append(text)
        return SimpleNamespace(id=len(event.replies))

    return await handle_public_command(
        event,
        store,
        group_id=group_id,
        actor_id=event.sender_id,
        text=event.raw_text,
        reply=reply,
        private_message_sender=private_message_sender,
        word_filter=word_filter,
    )


def test_user_riddle_and_emoji_games_award_idempotent_group_coins() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        riddle = Event("چیستان")
        assert await dispatch(riddle, store)
        state_key = game_commands._key("riddle", "user")
        state = store.get_group_state("g", state_key)
        assert "چیستان:" in riddle.replies[-1]
        answer = Event(state["answers"][0])
        assert await dispatch(answer, store)
        assert store.get_economy_profile("g", "user")["bronze"] == 3
        assert store.get_group_state("g", state_key) is None

        emoji = Event("حدس ایموجی")
        assert await dispatch(emoji, store)
        emoji_state = store.get_group_state("g", game_commands._key("emoji", "user"))
        answer_emoji = Event(emoji_state["answers"][0])
        assert await dispatch(answer_emoji, store)
        assert store.get_economy_profile("g", "user")["bronze"] == 7
        store.close()

    asyncio.run(scenario())


def test_duplicate_game_award_does_not_claim_a_second_coin_payment() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        start = Event("چیستان")
        assert await dispatch(start, store)
        state = store.get_group_state("g", game_commands._key("riddle", "user"))
        reference = f"game:g:riddle:{state['token']}:user"
        store.award_group_coins(
            "g", "user", 3, "bronze", reference=reference,
            note="game:riddle",
        )

        duplicate_answer = Event(state["answers"][0])
        assert await dispatch(duplicate_answer, store)
        assert "قبلاً به حساب شما اضافه شده است" in duplicate_answer.replies[-1]
        assert "+۳ سکه برنز" not in duplicate_answer.replies[-1]
        assert store.get_economy_profile("g", "user")["bronze"] == 3
        store.close()

    asyncio.run(scenario())


def test_who_knows_accepts_only_category_words_and_rotates_letters() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        start = Event("کی بیشتر بلده")
        assert await dispatch(start, store)
        key = game_commands._key("who_knows")
        state = store.get_group_state("g", key)
        letter = state["data"]["letter"]
        accepted = state["data"]["accepted_answers"]
        assert accepted
        assert all(game_commands._normalize_who_knows(word).startswith(letter) for word in accepted)

        invalid = Event(f"{letter}زززز")
        assert not await dispatch(invalid, store)
        assert store.get_group_state("g", key) is not None
        assert store.get_economy_profile("g", "user")["bronze"] == 0

        correct = Event(accepted[0])
        assert await dispatch(correct, store)
        assert store.get_economy_profile("g", "user")["bronze"] == 2
        assert store.get_group_state("g", key) is None

        next_start = Event("کی بیشتر بلده")
        assert await dispatch(next_start, store)
        next_state = store.get_group_state("g", key)
        assert next_state["data"]["letter"] != letter
        store.close()

    asyncio.run(scenario())


def test_group_quiz_correct_and_wrong_answers_finish_round() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        start = Event("چهار گزینه‌ای")
        assert await dispatch(start, store)
        state_key = game_commands._key("quiz")
        state = store.get_group_state("g", state_key)
        assert state is not None
        assert "موضوع:" in start.replies[-1]
        assert "سطح:" in start.replies[-1]
        answer = Event(str(state["data"]["correct"]))
        assert await dispatch(answer, store)
        assert store.get_economy_profile("g", "user")["bronze"] == 3
        assert store.get_group_state("g", state_key) is None

        start_again = Event("چهار گزینه ای")
        assert await dispatch(start_again, store)
        wrong_state = store.get_group_state("g", state_key)
        wrong = (int(wrong_state["data"]["correct"]) % 4) + 1
        wrong_answer = Event(str(wrong))
        assert await dispatch(wrong_answer, store)
        assert "پاسخ درست گزینه" in wrong_answer.replies[-1]
        assert store.get_group_state("g", state_key) is None
        assert store.get_economy_profile("g", "user")["bronze"] == 3
        store.close()

    asyncio.run(scenario())


def test_unconfirmed_direct_prompt_does_not_restart_its_countdown() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        observed_deadline: list[float] = []
        event = Event("نبرد", actor_id="host")

        async def unconfirmed_reply(_event, _text, *, formatting_entities=None):
            state = store.get_group_state("g", game_commands._key("battle"))
            observed_deadline.append(float(state["expires_at"]))

        assert await handle_public_command(
            event, store, group_id="g", actor_id="host", text="نبرد",
            reply=unconfirmed_reply,
        )
        state = store.get_group_state("g", game_commands._key("battle"))
        assert len(observed_deadline) == 1
        assert state["expires_at"] == observed_deadline[0]
        store.close()

    asyncio.run(scenario())


def test_direct_survival_and_battle_deadlines_start_after_prompt_delivery() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        delivered_at: list[float] = []

        async def reply(event, text, *, formatting_entities=None, prefer_respond=False):
            await asyncio.sleep(0.005)
            event.replies.append(text)
            delivered_at.append(game_commands.time.time())
            return SimpleNamespace(id=len(delivered_at))

        async def send(event: Event, group_id: str) -> list[float]:
            before = len(delivered_at)
            handled = await handle_public_command(
                event, store, group_id=group_id, actor_id=event.sender_id,
                text=event.raw_text, reply=reply,
            )
            assert handled
            return delivered_at[before:]

        battle_key = game_commands._key("battle")
        start = Event("نبرد", actor_id="host")
        start_delivery = await send(start, "battle-group")
        state = store.get_group_state("battle-group", battle_key)
        assert state["expires_at"] >= (
            start_delivery[-1] + game_commands._BATTLE_JOIN_SECONDS - 0.1
        )

        join = Event("شرکت", actor_id="opponent")
        join_delivery = await send(join, "battle-group")
        state = store.get_group_state("battle-group", battle_key)
        assert len(join_delivery) == 2
        assert state["data"]["index"] == 0
        assert state["expires_at"] >= (
            join_delivery[-1] + game_commands._BATTLE_ANSWER_SECONDS - 0.1
        )

        first_question = state["data"]["questions"][0]
        answer = Event(first_question["accepted_answers"][0], actor_id="host")
        answer_delivery = await send(answer, "battle-group")
        state = store.get_group_state("battle-group", battle_key)
        assert len(answer_delivery) == 2
        assert state["data"]["index"] == 1
        assert state["expires_at"] >= (
            answer_delivery[-1] + game_commands._BATTLE_ANSWER_SECONDS - 0.1
        )

        survival_key = game_commands._key("survival")
        survival_start = Event("بقا", actor_id="host")
        survival_start_delivery = await send(survival_start, "survival-group")
        state = store.get_group_state("survival-group", survival_key)
        assert state["expires_at"] >= (
            survival_start_delivery[-1] + game_commands._SURVIVAL_JOIN_SECONDS - 0.1
        )
        for index in range(1, 5):
            join_event = Event("شرکت", actor_id=f"player-{index}")
            deliveries = await send(join_event, "survival-group")
        state = store.get_group_state("survival-group", survival_key)
        assert len(deliveries) == 3
        assert state["data"]["round"] == 1
        assert state["expires_at"] >= (
            deliveries[-1] + game_commands._SURVIVAL_ANSWER_SECONDS - 0.1
        )
        store.close()

    asyncio.run(scenario())


def test_battle_runs_six_assigned_free_text_questions_and_pays_winner() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        bank = game_commands.all_battle_questions()
        assert len(bank) == 190
        assert len({question[0] for question in bank}) == len(bank)
        assert len({question[1] for question in bank}) == len(bank)
        assert all(all(isinstance(alias, str) and alias for alias in question[3]) for question in bank)
        start = Event("نبرد", actor_id="p1")
        assert await dispatch(start, store)
        key = game_commands._key("battle")
        state = store.get_group_state("g", key)
        assert state["data"]["phase"] == "joining"
        assert state["expires_at"] - game_commands.time.time() <= 60

        host_join = Event("شرکت", actor_id="p1")
        assert await dispatch(host_join, store)
        assert "بازیکن اول" in host_join.replies[-1]
        assert store.get_group_state("g", key)["data"]["opponent"] is None

        join = Event("شرکت", actor_id="p2")
        assert await dispatch(join, store)
        state = store.get_group_state("g", key)
        assert state["phase"] == "playing"
        assert len(state["data"]["questions"]) == 6
        assert "بازیکن ۱ — سؤال ۱" in state["prompt"]

        expected_correct = {0, 1, 2}
        for index in range(6):
            state = store.get_group_state("g", key)
            assignee = state["data"]["players"][index % 2]["user_id"]
            if index == 0:
                out_of_turn = Event("پاسخ زودهنگام", actor_id="p2")
                assert await dispatch(out_of_turn, store)
                assert store.get_group_state("g", key)["data"]["index"] == 0
            question = state["data"]["questions"][index]
            answer = (
                question["accepted_answers"][0]
                if index in expected_correct else "پاسخ نامعتبر"
            )
            response = Event(str(answer), actor_id=assignee)
            assert await dispatch(response, store)
            if index < 5:
                assert store.get_group_state("g", key)["data"]["index"] == index + 1
            else:
                assert store.get_group_state("g", key) is None
                assert "🏆 برنده: @testplayer" in response.replies[-1]

        assert store.get_economy_profile("g", "p1")["bronze"] == 2
        assert store.get_economy_profile("g", "p2")["bronze"] == 0
        store.close()

    asyncio.run(scenario())


def test_battle_timeout_advances_questions_and_zero_score_draw_has_no_reward() -> None:
    store = BotStore(":memory:")

    async def scenario() -> None:
        start = Event("نبرد", actor_id="p1")
        assert await dispatch(start, store)
        join = Event("شرکت", actor_id="p2")
        assert await dispatch(join, store)
        key = game_commands._key("battle")
        now = 2_000_000_000.0

        for index in range(game_commands._BATTLE_QUESTIONS_TOTAL):
            state = store.get_group_state("g", key)
            state["expires_at"] = now - 1
            store.set_group_state("g", key, state)
            notices = game_commands.due_game_notices(store, now=now)
            assert len(notices) == 1
            if index < game_commands._BATTLE_QUESTIONS_TOTAL - 1:
                assert "زمان سؤال تمام شد" in notices[0][2]
                updated = store.get_group_state("g", key)
                assert updated["data"]["index"] == index + 1
                assert updated["notice_pending"] is True
                assert game_commands.due_game_notices(store, now=now + 1) == notices
                updated["notice_pending"] = False
                updated.pop("notice_text", None)
                store.set_group_state("g", key, updated)
            else:
                assert notices[0][2] == "⏰ زمان سؤال تمام شد!"
                final_state = store.get_group_state("g", key)
                assert final_state["status"] == "timed_out"
                assert "🤝 مساوی شد" in final_state["notice_queue"][0]

        assert store.get_economy_profile("g", "p1")["bronze"] == 0
        assert store.get_economy_profile("g", "p2")["bronze"] == 0

    asyncio.run(scenario())
    store.close()


def test_battle_join_timeout_cancels_and_scored_draw_rewards_both_players() -> None:
    store = BotStore(":memory:")

    async def scenario() -> None:
        start = Event("نبرد", actor_id="host")
        assert await dispatch(start, store, group_id="waiting")
        key = game_commands._key("battle")
        state = store.get_group_state("waiting", key)
        now = game_commands.time.time() + 61
        state["expires_at"] = now - 1
        store.set_group_state("waiting", key, state)
        notices = game_commands.due_game_notices(store, now=now)
        assert len(notices) == 1
        assert "بازیکن دوم نیامد" in notices[0][2]
        assert store.get_group_state("waiting", key)["status"] == "timed_out"

        state = {"token": "tie-token", "status": "pending"}
        data = {
            "players": [
                {"user_id": "p1", "name": "Player One"},
                {"user_id": "p2", "name": "Player Two"},
            ],
            "scores": {"p1": 2, "p2": 2},
        }
        result = game_commands._battle_finish_notice(store, "draw", state, data)
        assert "🤝 مساوی شد" in result
        assert store.get_economy_profile("draw", "p1")["bronze"] == 2
        assert store.get_economy_profile("draw", "p2")["bronze"] == 2

    asyncio.run(scenario())
    store.close()


def test_group_game_capacity_is_two_and_duplicate_start_does_not_advance_bank() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        first = Event("چهار گزینه ای")
        assert await dispatch(first, store)
        seen_key = game_commands._seen_key("quiz", "user")
        first_seen = store.get_group_state("g", seen_key)

        duplicate = Event("چهار گزینه‌ای")
        assert await dispatch(duplicate, store)
        assert store.get_group_state("g", seen_key) == first_seen
        assert "همین حالا در گروه در جریان است" in duplicate.replies[-1]

        second = Event("حدس پرچم")
        assert await dispatch(second, store)
        assert store.get_group_state("g", game_commands._key("flag")) is not None

        third = Event("دروغ یا حقیقت")
        assert await dispatch(third, store)
        assert "دو بازی فعال است" in third.replies[-1]
        assert store.get_group_state("g", game_commands._key("truth_lie")) is None
        store.close()

    asyncio.run(scenario())


def test_truth_lie_accepts_reference_aliases_keycaps_and_group_no_repeat(monkeypatch) -> None:
    for answer in ("1", "۱", "1️⃣", "حقیقت", "حقیقته", "درست", "درسته", "راست", "راسته"):
        assert game_commands._parse_truth_lie_guess(answer) is True
    for answer in ("2", "۲", "2️⃣", "دروغ", "دروغه", "غلط", "غلطه", "نادرست"):
        assert game_commands._parse_truth_lie_guess(answer) is False
    assert game_commands._parse_truth_lie_guess("شاید") is None

    def deterministic_pick(items, used=None):
        seen = {int(value) for value in used or () if str(value).isdigit()}
        remaining = [index for index in range(len(items)) if index not in seen]
        if not remaining:
            seen.clear()
            remaining = list(range(len(items)))
        index = remaining[0]
        seen.add(index)
        return index, items[index], sorted(seen)

    async def scenario() -> None:
        store = BotStore(":memory:")
        monkeypatch.setattr(game_commands, "pick_new", deterministic_pick)
        first_start = Event("دروغ یا حقیقت", actor_id="p1")
        assert await dispatch(first_start, store)
        first_state = store.get_group_state("g", game_commands._key("truth_lie"))
        first_statement, first_truth = game_commands.TRUTH_LIE[0]
        assert first_statement in first_state["prompt"]
        assert first_state["data"]["truth"] is first_truth
        first_answer = Event("حقیقته" if first_truth else "دروغه", actor_id="p1")
        assert await dispatch(first_answer, store)
        assert store.get_economy_profile("g", "p1")["bronze"] == 2

        second_start = Event("دروغ یا حقیقت", actor_id="p2")
        assert await dispatch(second_start, store)
        second_state = store.get_group_state("g", game_commands._key("truth_lie"))
        assert second_state["data"]["truth"] is game_commands.TRUTH_LIE[1][1]
        assert game_commands.TRUTH_LIE[1][0] in second_state["prompt"]
        seen = store.get_group_state("g", game_commands._seen_key("truth_lie", "__group__"))
        assert seen == [0, 1]
        store.close()

    asyncio.run(scenario())


def test_game_guard_blocks_new_games_without_creating_state() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        store.set_group_state("g", "entertainment_enabled", False)
        event = Event("حدس پرچم")
        assert await dispatch(event, store)
        assert "غیر فعال" in event.replies[-1]
        assert store.list_group_states_prefix("game_session:") == []
        store.close()

    asyncio.run(scenario())


def test_best_answer_counts_repeated_content_and_keeps_first_equal_submission() -> None:
    answer = "گیاهان نور نور"
    score = game_commands._best_answer_score("گیاهان نور", ["نور"], answer)
    assert score == 0.99
    for phrase in ("نمی‌دانم", "نمی‌دونم", "نمی دانم", "نمیدونم"):
        assert game_commands._best_answer_score(
            "گیاهان نور", ["نور"], f"{phrase} گیاهان نور",
        ) is None

    repeated_data = {
        "question": "گیاهان نور",
        "keywords": ["نور"],
        "submissions": {
            "z-first": {"text": answer, "at": 10},
            "a-second": {"text": answer, "at": 10},
        },
    }
    winner = game_commands._best_answer_winner(repeated_data, current=20)
    assert winner is not None and winner[0] == "z-first"


def test_best_answer_rejects_solicitation_and_nonanswer_intents() -> None:
    question = "برای کاهش مصرف آب در خانه چه کارهایی می‌توان انجام داد؟"
    keywords = ["شیر", "نشتی", "آبیاری"]
    for answer in (
        "برای کاهش مصرف آب لطفاً چند سکه به من بدهید.",
        "این بازی را اجرا کنید تا پاسخ به دست بیاید.",
        "پاسخ ندهید و به مرحلهٔ بعد بروید.",
        "از خرید و فروش محصولات، درآمد بیشتری کسب کنید.",
    ):
        assert game_commands._best_answer_score(question, keywords, answer) is None

    helpful_answer = (
        "هنگام مسواک‌زدن شیر را ببندید و نشتی لوله‌ها را تعمیر کنید؛ "
        "آبیاری قطره‌ای نیز آب کمتری مصرف می‌کند."
    )
    assert game_commands._best_answer_score(question, keywords, helpful_answer) is not None


def test_best_answer_requires_reply_to_question_and_scores_only_one_submission() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        assert len(game_commands.BEST_QUESTIONS) == 180
        assert len({question[0] for question in game_commands.BEST_QUESTIONS}) == 180
        start = Event("بهترین جواب")
        assert await dispatch(start, store)
        state_key = game_commands._key("best_answer")
        state = store.get_group_state("g", state_key)
        question_id = state["data"]["question_message_id"]
        assert question_id == 1

        unthreaded = Event("برای صرفه‌جویی باید راه‌حل مناسب انتخاب کنیم.")
        assert not await dispatch(unthreaded, store)
        assert store.get_group_state("g", state_key)["data"]["submissions"] == {}

        keywords = state["data"]["keywords"]
        answer_value = f"{keywords[0]} و {keywords[1]} کمک می‌کنند تا {state['data']['question']}"
        answer_text = Event(answer_value)
        answer_text.reply_to = SimpleNamespace(reply_to_msg_id=question_id)
        assert await dispatch(answer_text, store)
        current = store.get_group_state("g", state_key)
        assert list(current["data"]["submissions"]) == ["user"]

        duplicate = Event("این پاسخ دوم را نباید جایگزین کند.")
        duplicate.reply_to = SimpleNamespace(reply_to_msg_id=question_id)
        assert await dispatch(duplicate, store)
        current = store.get_group_state("g", state_key)
        assert current["data"]["submissions"]["user"]["text"] == game_commands.normalize(answer_text.raw_text)

        current["expires_at"] = 1
        store.set_group_state("g", state_key, current)
        notices = game_commands.due_game_notices(store, now=100)
        assert len(notices) == 1
        assert "🏆 بهترین پاسخ" in notices[0][2]
        assert store.get_economy_profile("g", "user")["bronze"] == 2
        store.close()

    asyncio.run(scenario())


def test_lucky_box_matches_group_owner_board_prizes_and_rolling_quota() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        start = Event("جعبه شانسی")
        assert await dispatch(start, store)
        state_key = game_commands._key("lucky_box")
        state = store.get_group_state("g", state_key)
        assert "┌───┬───┬───┐" in start.replies[-1]
        prizes = list(state["data"]["boxes"].values())
        assert prizes.count(0) == 4
        assert sum(prize > 0 for prize in prizes) == 5
        assert all(1 <= prize <= 15 for prize in prizes if prize > 0)

        other_player = Event("1", actor_id="other")
        assert not await dispatch(other_player, store)
        assert store.get_group_state("g", state_key) is not None

        first_pick = Event("1")
        assert await dispatch(first_pick, store)
        assert store.get_group_state("g", state_key) is None

        second_start = Event("جعبه شانسی")
        assert await dispatch(second_start, store)
        assert store.get_group_state("g", state_key) is not None
        second_pick = Event("2")
        assert await dispatch(second_pick, store)
        assert store.get_group_state("g", state_key) is None

        blocked = Event("جعبه شانسی")
        assert await dispatch(blocked, store)
        assert "سهمیه امروز شما تمام شده است" in blocked.replies[-1]
        store.close()

    asyncio.run(scenario())


def test_game_timeout_is_staged_once_and_can_be_retried() -> None:
    store = BotStore(":memory:")
    state = {
        "status": "pending", "kind": "riddle", "token": "old-round",
        "answers": ["گودال"], "expires_at": 10,
    }
    key = game_commands._key("riddle", "user")
    store.set_group_state("g", key, state)
    first = game_commands.due_game_notices(store, now=11)
    assert len(first) == 1 and "گودال" in first[0][2]
    second = game_commands.due_game_notices(store, now=12)
    assert second == first
    store.delete_group_state("g", key)
    assert game_commands.due_game_notices(store, now=13) == []
    store.close()


def test_game_notice_batch_reports_live_deadlines_for_adaptive_polling() -> None:
    store = BotStore(":memory:")
    key = "game_session:g:riddle"
    store.set_group_state(
        "g", key,
        {"status": "pending", "kind": "riddle", "expires_at": 25},
    )

    notices, active, next_deadline = game_commands.due_game_notices_and_activity(
        store, now=20
    )
    assert notices == []
    assert active is True
    assert next_deadline == 25

    store.set_group_state(
        "g", key,
        {
            "status": "pending", "kind": "riddle", "expires_at": 25,
            "notice_pending": True, "notice_text": "اعلان آمادهٔ ارسال",
        },
    )
    notices, active, next_deadline = game_commands.due_game_notices_and_activity(
        store, now=20
    )
    assert notices == [("g", key, "اعلان آمادهٔ ارسال")]
    assert active is True
    assert next_deadline == 25
    store.close()


def test_sentence_games_have_independent_reference_sized_prompt_union() -> None:
    build_prompts = {
        game_commands._normalize_answer(sentence)
        for sentence in game_commands.SENTENCES
    }
    guess_answers = {
        game_commands._normalize_answer(answer)
        for _clue, answer in game_commands.SENTENCE_GUESS_PUZZLES
    }

    assert len(build_prompts) == 320
    assert len(guess_answers) == 320
    assert build_prompts.isdisjoint(guess_answers)
    # The fixed reference snapshot has 295 unique build prompts and 315
    # guess prompts, with no overlap: 610 unique sentence prompts in total.
    assert len(build_prompts | guess_answers) >= 610


def test_survival_and_battle_unique_prompt_union_is_not_smaller_than_reference() -> None:
    survival_prompts = {
        game_commands._normalize_answer(question)
        for _question_id, question, _answer, _aliases in survival_questions.all_questions()
    }
    battle_prompts = {
        game_commands._normalize_answer(row[1])
        for row in game_commands.all_battle_questions()
    }

    assert len(survival_prompts) == 205
    assert len(battle_prompts) == 190
    # The fixed reference snapshot has one normalized overlap across 203
    # survival and 190 battle prompts, yielding 392 unique prompts.
    assert len(survival_prompts | battle_prompts) >= 392


def test_quiz_battle_and_best_answer_have_reference_sized_unique_prompt_union() -> None:
    quiz_prompts = {
        game_commands._normalize_answer(question)
        for question, _options, _correct in game_commands.QUIZZES
    }
    battle_prompts = {
        game_commands._normalize_answer(row[1])
        for row in game_commands.all_battle_questions()
    }
    best_answer_prompts = {
        game_commands._normalize_answer(question)
        for question, _keywords in game_commands.BEST_QUESTIONS
    }

    assert len(quiz_prompts) == 225
    assert len(battle_prompts) == 190
    assert len(best_answer_prompts) == 180
    assert quiz_prompts.isdisjoint(battle_prompts)
    assert best_answer_prompts.isdisjoint(quiz_prompts | battle_prompts)
    assert len(quiz_prompts | battle_prompts) == 415
    # The fixed reference snapshot has 224 quiz, 190 battle, and 160
    # best-answer prompts, with one quiz/battle overlap (573 unique overall).
    assert len(quiz_prompts | battle_prompts | best_answer_prompts) >= 573


def test_laugh_or_lose_uses_countdown_then_60_second_open_round(monkeypatch) -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        monkeypatch.setattr(game_commands, "_LAUGH_COUNTDOWN_SECONDS", 1)
        monkeypatch.setattr(game_commands, "_LAUGH_COUNTDOWN_INTERVAL_SECONDS", 0.0)

        start = Event("بخند یا بباز")
        assert await dispatch(start, store)
        key = game_commands._key("laugh_or_lose")
        state = store.get_group_state("g", key)
        assert state["data"]["phase"] == "countdown"
        assert "آماده باش" in start.replies[-1]

        early = Event("😂", actor_id="early")
        assert not await dispatch(early, store)
        assert store.get_group_state("g", key) is not None

        await game_commands._LAUGH_TASKS["g"]
        state = store.get_group_state("g", key)
        assert state["data"]["phase"] == "open"
        assert state["expires_at"] - game_commands.time.time() <= 60
        assert any("۱..." in message for message in start.replies)
        assert any("حالا بخند" in message for message in start.replies)

        winner = Event("😂", actor_id="winner")
        assert await dispatch(winner, store)
        assert store.get_economy_profile("g", "winner")["bronze"] == 3
        assert store.get_group_state("g", key) is None
        assert "🏆 برنده" in winner.replies[-1]
        store.close()

    asyncio.run(scenario())


def test_minesweeper_has_one_safe_cell_daily_tehran_quota_and_mine_penalty(monkeypatch) -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        monkeypatch.setattr(game_commands.secrets, "randbelow", lambda _limit: 4)
        store.award_group_coins("g", "user", 20, "bronze", reference="mine-funds")

        start = Event("مین یاب")
        assert await dispatch(start, store)
        state_key = game_commands._key("minesweeper", "user")
        state = store.get_group_state("g", state_key)
        assert state["data"]["safe_cell"] == 5
        assert state["expires_at"] - game_commands.time.time() <= 60
        assert "۲ از ۲" in start.replies[-1]
        assert "1️⃣2️⃣3️⃣" in start.replies[-1]

        mine = Event("۴")
        assert await dispatch(mine, store)
        assert "مین در خانهٔ ۴" in mine.replies[-1]
        assert "۱۰ سکه برنز از شما کم شد" in mine.replies[-1]
        assert store.get_economy_profile("g", "user")["bronze"] == 10
        assert store.get_group_state("g", state_key) is None
        quota_key = "game_daily_quota:minesweeper:user"
        quota = store.get_group_state("__global__", quota_key)
        assert quota["count"] == 1
        assert quota["events"]

        second_start = Event("مین یاب")
        assert await dispatch(second_start, store)
        second_state = store.get_group_state("g", state_key)
        second_state["expires_at"] = 1
        store.set_group_state("g", state_key, second_state)
        notices = game_commands.due_game_notices(store, now=2)
        assert len(notices) == 1
        assert "خانهٔ امن در ۵ بود" in notices[0][2]
        assert "۰ از ۲" in notices[0][2]
        assert store.get_group_state("__global__", quota_key)["count"] == 2

        blocked = Event("مین یاب")
        assert await dispatch(blocked, store)
        assert "شانس‌های امروز شما تمام شد" in blocked.replies[-1]
        store.close()

    asyncio.run(scenario())


def test_minesweeper_safe_pick_awards_five_silver_and_daily_quota_is_atomic(monkeypatch) -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        monkeypatch.setattr(game_commands.secrets, "randbelow", lambda _limit: 4)
        start = Event("مین یاب")
        assert await dispatch(start, store)
        win = Event("5")
        assert await dispatch(win, store)
        profile = store.get_economy_profile("g", "user")
        assert profile["silver"] == 5
        assert "+۵ سکه نقره" in win.replies[-1]

        day = "2026-10-16"
        first, remaining = store.consume_daily_game_chance(
            "test-game", "user", day, 2, reference="round-1"
        )
        assert first and remaining == 1
        duplicate, same_remaining = store.consume_daily_game_chance(
            "test-game", "user", day, 2, reference="round-1"
        )
        assert duplicate and same_remaining == 1
        _second, remaining = store.consume_daily_game_chance(
            "test-game", "user", day, 2, reference="round-2"
        )
        assert remaining == 0
        _next_day, remaining = store.consume_daily_game_chance(
            "test-game", "user", "2026-10-17", 2, reference="round-3"
        )
        assert remaining == 1
        store.close()

    asyncio.run(scenario())


def test_revised_question_ids_do_not_reuse_stale_no_repeat_history() -> None:
    for level, revised_count in ((1, 40), (2, 10), (3, 40), (4, 10), (5, 10)):
        ids = [row[0] for row in survival_questions.level_pool(level)]
        assert all(question_id.startswith(f"survival-v2-l{level}-") for question_id in ids[:revised_count])
        assert all(question_id.startswith(f"survival-l{level}-") for question_id in ids[revised_count:])

    battle_ids = [row[0] for row in game_commands.all_battle_questions()]
    assert all(question_id.startswith("battle-v2-") for question_id in battle_ids[:51])
    assert all(question_id.startswith("battle-q") for question_id in battle_ids[51:])


def test_survival_bank_has_five_levels_and_group_persistent_no_repeat() -> None:
    store = BotStore(":memory:")
    bank = game_commands.all_survival_questions()
    assert len(game_commands.SURVIVAL_LEVELS) == 5
    assert [len(level) for level in game_commands.SURVIVAL_LEVELS] == [43, 42, 40, 40, 40]
    assert len(bank) == 205
    assert [
        sum(len(aliases) for _prompt, _answer, aliases in level)
        for level in survival_questions.LEVELS
    ] == [88, 85, 80, 82, 80]
    assert len({question[0] for question in bank}) == len(bank)
    digit_translation = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")
    numeric_questions = 0
    for question_id, prompt, answer, accepted in bank:
        assert prompt and answer and accepted and question_id
        assert all(isinstance(alias, str) and alias for alias in accepted)
        if answer.isascii() and answer.isdigit():
            numeric_questions += 1
            arabic_digit_alias = answer.translate(digit_translation)
            assert arabic_digit_alias in accepted
            assert game_commands._accepted(arabic_digit_alias, (answer, *accepted))
    assert numeric_questions == 199

    first_state: dict = {}
    first_data = {"round": 0}
    first_prompt = game_commands._survival_start_round(
        store, "g", first_state, first_data, current=0
    )
    first_id = first_data["question_id"]
    assert "سطح ۱ از ۵" in first_prompt
    assert "پاسخ را به‌صورت متن بفرستید" in first_prompt
    assert "options" not in first_data

    next_game_state: dict = {}
    next_game_data = {"round": 0}
    game_commands._survival_start_round(
        store, "g", next_game_state, next_game_data, current=0
    )
    assert next_game_data["question_id"] != first_id
    store.close()


def test_survival_aborts_below_minimum_and_starts_when_the_fourth_player_joins() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        start_short = Event("بقا", actor_id="host")
        assert await dispatch(start_short, store, group_id="short")
        join_short = Event("شرکت", actor_id="only-player")
        assert await dispatch(join_short, store, group_id="short")
        short_key = game_commands._key("survival")
        state = store.get_group_state("short", short_key)
        state["expires_at"] = game_commands.time.time() - 1
        store.set_group_state("short", short_key, state)
        aborted = game_commands.due_game_notices(store)
        assert len(aborted) == 1
        assert "تعداد شرکت‌کننده کافی نبود" in aborted[0][2]

        start_full = Event("بقا", actor_id="host")
        assert await dispatch(start_full, store, group_id="full")
        fourth_join = None
        for number in range(1, 5):
            join = Event("شرکت", actor_id=f"player-{number}")
            assert await dispatch(join, store, group_id="full")
            if number == 4:
                fourth_join = join
        assert fourth_join is not None
        assert len(fourth_join.replies) == 3
        assert "ثبت شد" in fourth_join.replies[0]
        assert "🏕 بازی شروع شد" in fourth_join.replies[1]
        assert "مرحلهٔ ۱ از ۲۰" in fourth_join.replies[2]
        full_key = game_commands._key("survival")
        state = store.get_group_state("full", full_key)
        assert state["phase"] == "playing"
        assert len(state["data"]["players"]) == 4
        assert "مرحلهٔ ۱ از ۲۰" in state["prompt"]
        store.close()

    asyncio.run(scenario())


def test_sentence_guess_and_build_are_distinct_and_progress_only_on_correct_answer() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        guess = Event("حدس جمله", actor_id="guesser")
        assert await dispatch(guess, store)
        guess_key = game_commands._key("sentence_guess", "guesser")
        guess_state = store.get_group_state("g", guess_key)
        assert guess_state["data"]["mode"] == "guess"
        assert "حدس جمله" in guess.replies[-1]
        assert " / " not in guess.replies[-1]
        guess_progress = game_commands._seen_key("sentence_guess_guess", "guesser")
        assert store.get_group_state("g", guess_progress, []) == []

        wrong = Event("پاسخ نامرتبط", actor_id="guesser")
        assert not await dispatch(wrong, store)
        assert store.get_group_state("g", guess_key) is not None
        assert store.get_group_state("g", guess_progress, []) == []

        correct = Event(guess_state["answers"][0], actor_id="guesser")
        assert await dispatch(correct, store)
        assert store.get_group_state("g", guess_key) is None
        assert guess_state["data"]["puzzle_index"] in store.get_group_state("g", guess_progress)
        assert store.get_economy_profile("g", "guesser")["bronze"] == 3

        second_guess = Event("حدس جمله", actor_id="guesser")
        assert await dispatch(second_guess, store)
        second_state = store.get_group_state("g", guess_key)
        assert second_state["data"]["puzzle_index"] != guess_state["data"]["puzzle_index"]

        builder = Event("ساخت جمله", actor_id="builder")
        assert await dispatch(builder, store)
        build_key = game_commands._key("sentence_guess", "builder")
        build_state = store.get_group_state("g", build_key)
        assert build_state["data"]["mode"] == "build"
        assert "ساخت جمله" in builder.replies[-1]
        assert " / " in builder.replies[-1]
        build_progress = game_commands._seen_key("sentence_guess_build", "builder")
        assert store.get_group_state("g", build_progress, []) == []

        build_answer = Event(build_state["answers"][0], actor_id="builder")
        assert await dispatch(build_answer, store)
        assert build_state["data"]["puzzle_index"] in store.get_group_state("g", build_progress)
        assert store.get_economy_profile("g", "builder")["bronze"] == 3
        store.close()

    asyncio.run(scenario())


def test_sentence_deadline_starts_after_delivery_and_recent_answers_are_group_scoped(monkeypatch) -> None:
    clock = [100.0]
    monkeypatch.setattr(game_commands, "time", SimpleNamespace(time=lambda: clock[0]))

    async def scenario() -> None:
        store = BotStore(":memory:")
        first = Event("حدس جمله", actor_id="first")
        delivery_completed: list[float] = []

        async def delayed_reply(event, text, *, formatting_entities=None, prefer_respond=False):
            event.replies.append(text)
            pending = store.get_group_state("g", game_commands._key("sentence_guess", "first"))
            assert pending["deadline_pending"] is True
            clock[0] = 110.0
            delivery_completed.append(clock[0])
            return SimpleNamespace(id=1)

        assert await game_commands.handle_game_command(
            first, store, group_id="g", actor_id="first", text=first.raw_text,
            reply=delayed_reply,
        )
        first_state = store.get_group_state("g", game_commands._key("sentence_guess", "first"))
        assert not first_state.get("deadline_pending")
        assert first_state["expires_at"] == delivery_completed[0] + 30
        first_answer = game_commands._normalize_answer(first_state["answers"][0])
        recent = store.get_group_state(
            game_commands._GLOBAL_STATE, "sentence_guess_recent:g", [],
        )
        assert first_answer in recent

        second = Event("حدس جمله", actor_id="second")
        assert await dispatch(second, store)
        second_state = store.get_group_state("g", game_commands._key("sentence_guess", "second"))
        assert game_commands._normalize_answer(second_state["answers"][0]) != first_answer
        store.close()

    asyncio.run(scenario())


def test_late_sentence_answer_does_not_erase_staged_timeout_notice() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        key = game_commands._key("sentence_guess", "user")
        state = {
            "status": "pending", "kind": "sentence_guess", "token": "late-round",
            "answers": ["پاسخ درست"], "expires_at": 1,
            "data": {
                "mode": "guess", "progress_kind": "sentence_guess_guess",
                "puzzle_index": 0,
            },
        }
        store.set_group_state("g", key, state)

        late = Event("پاسخ درست", actor_id="user")
        assert await dispatch(late, store)
        assert store.get_group_state("g", key) is not None
        assert store.get_group_state(
            "g", game_commands._seen_key("sentence_guess_guess", "user"), [],
        ) == []

        notices = game_commands.due_game_notices(store, now=2)
        assert len(notices) == 1
        assert "پاسخ درست" in notices[0][2]
        assert store.get_group_state("g", key)["status"] == "timed_out"
        store.close()

    asyncio.run(scenario())


def test_unconfirmed_sentence_prompt_is_cleared_and_stale_pending_state_expires() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        event = Event("حدس جمله", actor_id="user")

        async def unconfirmed_reply(_event, _text, *, formatting_entities=None, prefer_respond=False):
            return False

        assert await game_commands.handle_game_command(
            event, store, group_id="g", actor_id="user", text=event.raw_text,
            reply=unconfirmed_reply,
        )
        key = game_commands._key("sentence_guess", "user")
        assert store.get_group_state("g", key) is None
        assert store.get_group_state(
            game_commands._GLOBAL_STATE, "sentence_guess_recent:g", [],
        ) == []

        stale_key = game_commands._key("sentence_guess", "stale")
        store.set_group_state("g", stale_key, {
            "status": "pending", "kind": "sentence_guess", "token": "stale",
            "deadline_pending": True, "deadline_pending_since": 10,
            "expires_at": 130, "data": {"mode": "guess", "puzzle_index": 0},
        })
        assert game_commands.due_game_notices(store, now=129) == []
        assert store.get_group_state("g", stale_key) is not None
        assert game_commands.due_game_notices(store, now=130) == []
        assert store.get_group_state("g", stale_key) is None
        store.close()

    asyncio.run(scenario())


def test_survival_registers_two_to_four_players_and_awards_per_round_and_winner() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        start = Event("بقا", actor_id="host")
        assert await dispatch(start, store)
        key = game_commands._key("survival")
        state = store.get_group_state("g", key)
        assert state["phase"] == "joining"
        assert state["expires_at"] - game_commands.time.time() <= 60
        assert "حداقل ۲ و حداکثر ۴ نفر" in start.replies[-1]

        for user_id in ("p1", "p2"):
            joined = Event("شرکت", actor_id=user_id)
            assert await dispatch(joined, store)
            assert "ثبت شد" in joined.replies[-1]
        state = store.get_group_state("g", key)
        assert len(state["data"]["players"]) == 2
        assert state["phase"] == "joining"

        now = game_commands.time.time()
        state["expires_at"] = now - 1
        store.set_group_state("g", key, state)
        notices = game_commands.due_game_notices(store, now=now)
        assert len(notices) == 1
        assert "🏕 بازی شروع شد" in notices[0][2]
        state = store.get_group_state("g", key)
        assert state["phase"] == "playing"
        assert state["notice_pending"] is True
        assert len(state["notice_queue"]) == 1
        assert "مرحلهٔ ۱ از ۲۰" in state["notice_queue"][0]

        # A failed first send can be retried without re-starting the round.
        assert game_commands.due_game_notices(store, now=now + 1) == notices
        state["notice_pending"] = False
        state.pop("notice_text", None)
        state.pop("notice_queue", None)
        store.set_group_state("g", key, state)

        correct = str(state["data"]["accepted_answers"][0])
        correct_answer = Event(correct, actor_id="p1")
        assert await dispatch(correct_answer, store)
        wrong_answer = Event("پاسخ نادرست", actor_id="p2")
        assert await dispatch(wrong_answer, store)
        assert store.get_economy_profile("g", "p1")["silver"] == 1
        state = store.get_group_state("g", key)
        assert state["data"]["players"]["p2"]["alive"] is False

        now = game_commands.time.time()
        state["expires_at"] = now - 1
        store.set_group_state("g", key, state)
        final_notice = game_commands.due_game_notices(store, now=now)
        assert len(final_notice) == 1
        assert "برندهٔ بقا" in final_notice[0][2]
        assert store.get_economy_profile("g", "p1")["silver"] == 9
        assert game_commands.due_game_notices(store, now=now + 1) == final_notice
        assert store.get_economy_profile("g", "p1")["silver"] == 9
        store.close()

    asyncio.run(scenario())


def test_vampire_assigns_one_role_only_by_private_message_and_pays_correct_guess() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        private_messages = []

        async def send_private(group_id, user_id, access_hash, text):
            private_messages.append((group_id, user_id, access_hash, text))
            return SimpleNamespace(id=700)

        start = Event("خون\u200cآشام", actor_id="starter")
        assert await dispatch(start, store, private_message_sender=send_private)
        task = game_commands._VAMPIRE_TASKS["g"]
        key = game_commands._key("vampire")
        joining = store.get_group_state("g", key)
        assert joining["data"]["phase"] == "joining"
        assert "access_hash" not in str(joining)
        assert "شرکت" in start.replies[-1]

        join_events = []
        for index in range(5):
            player = Event("شرکت", actor_id=f"p{index}")
            player.sender.access_hash = 9000 + index
            assert await dispatch(player, store, private_message_sender=send_private)
            join_events.append(player)
        await task

        state = store.get_group_state("g", key)
        assert state["phase"] == "guessing"
        assert len(private_messages) == 1
        dm_group, dm_user, dm_hash, dm_text = private_messages[0]
        assert dm_group == "g"
        assert dm_user == state["data"]["vampire_id"]
        assert dm_hash in range(9000, 9005)
        assert dm_text == game_commands._VAMPIRE_ROLE_MESSAGE
        assert all(
            game_commands._VAMPIRE_ROLE_MESSAGE not in text
            for event in [start, *join_events]
            for text in event.replies
        )
        assert any("Test Player" in text for text in start.replies)

        vampire_index = state["data"]["vampire_index"]
        vampire_id = state["data"]["vampire_id"]
        winner = next(
            player for player in state["data"]["players"]
            if player["user_id"] != vampire_id
        )
        guess = Event(str(vampire_index + 1), actor_id=winner["user_id"])
        assert await dispatch(guess, store, private_message_sender=send_private)
        assert "سکه نقره" in guess.replies[-1]
        assert store.get_economy_profile("g", winner["user_id"])["silver"] == 7
        assert store.get_group_state("g", key) is None
        store.close()

    asyncio.run(scenario())


def test_vampire_aborts_without_exposing_a_role_when_dm_fails() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        private_messages = []

        async def failing_private_message(group_id, user_id, access_hash, text):
            private_messages.append((group_id, user_id, access_hash, text))
            raise PermissionError("private chat unavailable")

        start = Event("خون آشام", actor_id="starter")
        assert await dispatch(start, store, private_message_sender=failing_private_message)
        task = game_commands._VAMPIRE_TASKS["g"]
        join_events = []
        for index in range(5):
            player = Event("شرکت", actor_id=f"q{index}")
            player.sender.access_hash = 2000 + index
            assert await dispatch(player, store, private_message_sender=failing_private_message)
            join_events.append(player)
        await task

        state = store.get_group_state("g", game_commands._key("vampire"))
        assert state["status"] == "timed_out"
        assert state["notice_text"] == game_commands._VAMPIRE_FAILED_MESSAGE
        assert len(private_messages) == 5
        assert len({message[1] for message in private_messages}) == 5
        notices = game_commands.due_game_notices(store, now=10**12)
        assert len(notices) == 1
        assert notices[0][2] == game_commands._VAMPIRE_FAILED_MESSAGE
        assert all(
            "شما خون‌آشام هستید" not in text
            for event in [start, *join_events]
            for text in event.replies
        )
        store.close()

    asyncio.run(scenario())


def test_vampire_does_not_start_below_minimum_player_count(monkeypatch) -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        private_messages = []

        async def send_private(group_id, user_id, access_hash, text):
            private_messages.append((group_id, user_id, access_hash, text))
            return SimpleNamespace(id=801)

        start = Event("خون آشام", actor_id="starter")
        assert await dispatch(start, store, private_message_sender=send_private)
        task = game_commands._VAMPIRE_TASKS["g"]
        for index in range(3):
            player = Event("شرکت", actor_id=f"few{index}")
            player.sender.access_hash = 3000 + index
            assert await dispatch(player, store, private_message_sender=send_private)
        await asyncio.sleep(0.01)
        await task
        state = store.get_group_state("g", game_commands._key("vampire"))
        assert state["status"] == "timed_out"
        assert private_messages == []
        notices = game_commands.due_game_notices(store, now=10**12)
        assert len(notices) == 1
        assert "تعداد شرکت‌کننده کافی نبود" in notices[0][2]
        store.close()

    monkeypatch.setattr(game_commands, "_VAMPIRE_JOIN_SECONDS", 0.001)
    asyncio.run(scenario())


def test_vampire_reassigns_role_after_permanent_dm_failure() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        calls = []

        async def send_private(group_id, user_id, access_hash, text):
            calls.append((group_id, user_id, access_hash, text))
            if len(calls) == 1:
                raise PermissionError("private chat is not available")
            return SimpleNamespace(id=950)

        start = Event("خون آشام", actor_id="starter")
        assert await dispatch(start, store, private_message_sender=send_private)
        task = game_commands._VAMPIRE_TASKS["g"]
        for index in range(5):
            player = Event("شرکت", actor_id=f"retry{index}")
            player.sender.access_hash = 5000 + index
            assert await dispatch(player, store, private_message_sender=send_private)
        await task

        state = store.get_group_state("g", game_commands._key("vampire"))
        assert state["phase"] == "guessing"
        assert len(calls) == 2
        assert calls[0][1] != calls[1][1]
        assert state["data"]["vampire_id"] == calls[1][1]
        assert all(call[3] == game_commands._VAMPIRE_ROLE_MESSAGE for call in calls)
        store.close()

    asyncio.run(scenario())


def test_vampire_retries_transient_dm_errors_on_same_player(monkeypatch) -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        calls = []

        async def send_private(group_id, user_id, access_hash, text):
            calls.append((group_id, user_id, access_hash, text))
            if len(calls) < 3:
                raise TimeoutError("RPC timed out")
            return SimpleNamespace(id=951)

        start = Event("خون آشام", actor_id="starter")
        assert await dispatch(start, store, private_message_sender=send_private)
        task = game_commands._VAMPIRE_TASKS["g"]
        for index in range(5):
            player = Event("شرکت", actor_id=f"transient{index}")
            player.sender.access_hash = 6000 + index
            assert await dispatch(player, store, private_message_sender=send_private)
        await task

        state = store.get_group_state("g", game_commands._key("vampire"))
        assert state["phase"] == "guessing"
        assert len(calls) == game_commands._ROLE_DM_RETRIES
        assert len({call[1] for call in calls}) == 1
        assert state["data"]["vampire_id"] == calls[0][1]
        store.close()

    monkeypatch.setattr(game_commands, "_ROLE_DM_RETRY_DELAY_SECONDS", 0)
    asyncio.run(scenario())


def test_karagah_private_role_two_stage_guess_and_winner_reward() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        private_messages = []

        async def send_private(group_id, user_id, access_hash, text):
            private_messages.append((group_id, user_id, access_hash, text))
            if len(private_messages) == 1:
                raise PermissionError("private chat is not available")
            return SimpleNamespace(id=990)

        start = Event("کارگاه", actor_id="starter")
        assert await dispatch(start, store, private_message_sender=send_private)
        task = game_commands._KARAGAH_TASKS["g"]
        key = game_commands._key("karagah")
        joined = []
        for index in range(5):
            player = Event("شرکت", actor_id=f"detective{index}")
            player.sender.access_hash = 8000 + index
            assert await dispatch(player, store, private_message_sender=send_private)
            joined.append(player)
        await task

        state = store.get_group_state("g", key)
        data = state["data"]
        assert state["phase"] == "thief_guess"
        assert len(data["players"]) == 5
        assert len(data["options"]) == 5
        assert len(set(data["options"])) == 5
        assert "access_hash" not in str(state)
        assert len(private_messages) == 2
        assert private_messages[0][1] != private_messages[1][1]
        assert private_messages[1][1] == data["thief_id"]
        assert data["stolen_object"] in private_messages[1][3]
        assert all(
            data["stolen_object"] not in message
            and "شما دزد هستید" not in message
            for event in [start, *joined]
            for message in event.replies
        )

        finder = next(
            player for player in data["players"]
            if player["user_id"] != data["thief_id"]
        )
        thief_guess = Event(str(data["thief_index"] + 1), actor_id=finder["user_id"])
        assert await dispatch(thief_guess, store, private_message_sender=send_private)
        state = store.get_group_state("g", key)
        assert state["phase"] == "object_guess"
        assert "شیء دزدیده‌شده" not in thief_guess.replies[-1]
        assert all(option in thief_guess.replies[-1] for option in state["data"]["options"])

        correct_option = state["data"]["options"].index(state["data"]["stolen_object"]) + 1
        object_guess = Event(str(correct_option), actor_id=finder["user_id"])
        assert await dispatch(object_guess, store, private_message_sender=send_private)
        assert "پرونده حل شد" in object_guess.replies[-1]
        assert "\\n" not in object_guess.replies[-1]
        assert "\n\n" in object_guess.replies[-1]
        assert store.get_economy_profile("g", finder["user_id"])["silver"] == 12
        assert store.get_group_state("g", key) is None
        store.close()

    asyncio.run(scenario())


def test_karagah_wrong_object_awards_thief_and_timeout_notice_is_idempotent() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")

        async def send_private(group_id, user_id, access_hash, text):
            return SimpleNamespace(id=991)

        start = Event("کارگاه", actor_id="starter")
        assert await dispatch(start, store, private_message_sender=send_private)
        task = game_commands._KARAGAH_TASKS["g"]
        for index in range(5):
            player = Event("شرکت", actor_id=f"case{index}")
            player.sender.access_hash = 10000 + index
            assert await dispatch(player, store, private_message_sender=send_private)
        await task

        key = game_commands._key("karagah")
        state = store.get_group_state("g", key)
        data = state["data"]
        finder = next(
            player for player in data["players"]
            if player["user_id"] != data["thief_id"]
        )
        thief_guess = Event(str(data["thief_index"] + 1), actor_id=finder["user_id"])
        assert await dispatch(thief_guess, store)
        state = store.get_group_state("g", key)
        data = state["data"]
        wrong_option = next(
            index for index, option in enumerate(data["options"], 1)
            if option != data["stolen_object"]
        )
        object_guess = Event(str(wrong_option), actor_id=finder["user_id"])
        assert await dispatch(object_guess, store)
        assert "دزد برنده شد" in object_guess.replies[-1]
        thief_id = data["thief_id"]
        assert store.get_economy_profile("g", thief_id)["bronze"] == 12
        assert store.get_group_state("g", key) is None
        store.close()

    asyncio.run(scenario())


def test_karagah_timeout_awards_thief_after_no_correct_thief_guess() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")

        async def send_private(group_id, user_id, access_hash, text):
            return SimpleNamespace(id=992)

        start = Event("کارگاه", actor_id="starter")
        assert await dispatch(start, store, private_message_sender=send_private)
        task = game_commands._KARAGAH_TASKS["g"]
        for index in range(5):
            player = Event("شرکت", actor_id=f"timeout{index}")
            player.sender.access_hash = 11000 + index
            assert await dispatch(player, store, private_message_sender=send_private)
        await task

        key = game_commands._key("karagah")
        state = store.get_group_state("g", key)
        thief_id = state["data"]["thief_id"]
        state["expires_at"] = 1
        store.award_group_coins(
            "g", thief_id, game_commands._GAME_REWARDS["karagah_thief"][0],
            "bronze", reference=f"karagah:g:{state['token']}:thief",
            note="game:karagah_thief",
        )
        store.set_group_state("g", key, state)
        late_message = Event("2", actor_id="timeout0")
        assert not await dispatch(late_message, store)
        assert store.get_group_state("g", key) is not None
        notices = game_commands.due_game_notices(store, now=10**12)
        assert len(notices) == 1
        assert "دزد برنده شد" in notices[0][2]
        assert "قبلاً ثبت شده بود" in notices[0][2]
        assert "+۱۲" not in notices[0][2]
        assert "\\n" not in notices[0][2]
        assert "\n\n" in notices[0][2]
        assert store.get_economy_profile("g", thief_id)["bronze"] == 12
        assert game_commands.due_game_notices(store, now=10**12 + 1) == notices
        assert store.get_economy_profile("g", thief_id)["bronze"] == 12
        store.close()

    asyncio.run(scenario())


def test_karagah_cancels_when_fewer_than_four_players_join(monkeypatch) -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")
        private_messages = []

        async def send_private(group_id, user_id, access_hash, text):
            private_messages.append((group_id, user_id, access_hash, text))
            return SimpleNamespace(id=993)

        start = Event("کارگاه", actor_id="starter")
        assert await dispatch(start, store, private_message_sender=send_private)
        task = game_commands._KARAGAH_TASKS["g"]
        for index in range(3):
            player = Event("شرکت", actor_id=f"under{index}")
            player.sender.access_hash = 12000 + index
            assert await dispatch(player, store, private_message_sender=send_private)
        await asyncio.sleep(0.01)
        await task
        state = store.get_group_state("g", game_commands._key("karagah"))
        assert state["status"] == "timed_out"
        assert state["notice_text"] == game_commands._KARAGAH_NOT_ENOUGH_MESSAGE
        assert private_messages == []
        assert game_commands.due_game_notices(store, now=10**12)[0][2] == state["notice_text"]
        store.close()

    monkeypatch.setattr(game_commands, "_KARAGAH_JOIN_SECONDS", 0.001)
    asyncio.run(scenario())


def test_secret_role_peer_data_uses_cached_input_sender_and_rejects_min_user_hash() -> None:
    cached_peer_event = SimpleNamespace(
        sender=None,
        input_sender=SimpleNamespace(user_id=77, access_hash=123456),
    )
    player, access_hash = game_commands._secret_player_info(cached_peer_event, 77)
    assert player == {"user_id": "77", "name": "کاربر", "tag": ""}
    assert access_hash == 123456

    min_user_event = SimpleNamespace(
        sender=SimpleNamespace(
            id=88, first_name="Partial", last_name="User", username=None,
            access_hash=654321, min=True,
        ),
        input_sender=None,
    )
    player, access_hash = game_commands._secret_player_info(min_user_event, 88)
    assert player["name"] == "Partial User"
    assert access_hash is None


def test_karagah_object_guess_timeout_awards_thief_not_finder() -> None:
    async def scenario() -> None:
        store = BotStore(":memory:")

        async def send_private(group_id, user_id, access_hash, text):
            return SimpleNamespace(id=994)

        start = Event("کارگاه", actor_id="starter")
        assert await dispatch(start, store, private_message_sender=send_private)
        task = game_commands._KARAGAH_TASKS["g"]
        for index in range(5):
            player = Event("شرکت", actor_id=f"object-timeout{index}")
            player.sender.access_hash = 13000 + index
            assert await dispatch(player, store, private_message_sender=send_private)
        await task

        key = game_commands._key("karagah")
        state = store.get_group_state("g", key)
        data = state["data"]
        finder = next(
            player for player in data["players"]
            if player["user_id"] != data["thief_id"]
        )
        thief_guess = Event(str(data["thief_index"] + 1), actor_id=finder["user_id"])
        assert await dispatch(thief_guess, store)
        state = store.get_group_state("g", key)
        state["expires_at"] = 1
        store.set_group_state("g", key, state)

        notices = game_commands.due_game_notices(store, now=10**12)
        assert len(notices) == 1
        assert "زمان تمام شد" in notices[0][2]
        assert store.get_economy_profile("g", data["thief_id"])["bronze"] == 12
        assert store.get_economy_profile("g", finder["user_id"])["silver"] == 0
        store.close()

    asyncio.run(scenario())
