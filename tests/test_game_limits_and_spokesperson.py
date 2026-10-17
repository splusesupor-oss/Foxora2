import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.simple_replies import SIMPLE_REPLIES, INSULTS, INSULT_REPLY
from handlers.fox_games_router import (
    active_game_count,
    handle as handle_fox_games,
    reset_all as reset_fox_games,
    LIMIT_EXCEEDED_MESSAGE,
    MAX_ACTIVE_GAMES_PER_CHAT,
)
from modules.fox_games import (
    laugh_or_lose,
    survival,
    maemma,
)


class MockEvent:
    def __init__(self, text, chat_id=1001, user_id=2001, is_private=False):
        self.raw_text = text
        self.message = SimpleNamespace(message=text, id=1, entities=None)
        self.chat_id = chat_id
        self.sender_id = user_id
        self.is_private = is_private
        self.replies = []

    async def reply(self, msg, *args, **kwargs):
        self.replies.append(msg)
        return SimpleNamespace(id=len(self.replies))


def test_spokesperson_dictionary_and_new_replies():
    """Verify new exact replies in simple_replies dictionary."""
    assert SIMPLE_REPLIES.get("عشقم") == "جونم نانا 🐥"
    assert SIMPLE_REPLIES.get("رل بزنیم") == "من یک ربات هستم فاقد احساسات🏆"
    assert SIMPLE_REPLIES.get("زندگیم") == "جونم 🍫✨"
    assert SIMPLE_REPLIES.get("دوست دارم") == "ولی من دوست ندارم چون عاشقتم🥲"


def test_spokesperson_whole_phrase_not_substring():
    """Verify that sentences containing spokesperson words do NOT match substring-wise."""
    # Substring / embedded phrases must NOT be in SIMPLE_REPLIES
    assert "ربات دیروز بود" not in SIMPLE_REPLIES
    assert "من دوست دارم امروز برم" not in SIMPLE_REPLIES
    assert "سلام بر شما" not in SIMPLE_REPLIES
    assert "امروز عشقم اومد" not in SIMPLE_REPLIES

    # Exact phrases must match
    assert "عشقم" in SIMPLE_REPLIES
    assert "دوست دارم" in SIMPLE_REPLIES
    assert "زندگیم" in SIMPLE_REPLIES
    assert "رل بزنیم" in SIMPLE_REPLIES


def test_fox_games_max_two_concurrent_limit_per_group():
    """Verify max 2 active games per chat and proper release upon game completion."""
    async def scenario():
        reset_fox_games()
        chat_a = 1001
        chat_b = 2002
        bot = SimpleNamespace(client=MagicMock(), logger=MagicMock())
        sender = SimpleNamespace(id=2001, first_name="Ali", username="ali")

        # 1. Start Game 1 in Chat A: Laugh or Lose
        event1 = MockEvent("بخند یا بباز", chat_id=chat_a)
        handled1 = await handle_fox_games(bot, event1, chat_a, 2001, sender, "بخند یا بباز")
        assert handled1 is True
        assert laugh_or_lose.is_active(chat_a) is True
        assert active_game_count(chat_a) == 1

        # 2. Start Game 2 in Chat A: Survival
        event2 = MockEvent("بقا", chat_id=chat_a)
        handled2 = await handle_fox_games(bot, event2, chat_a, 2001, sender, "بقا")
        assert handled2 is True
        assert survival.is_active(chat_a) is True
        assert active_game_count(chat_a) == 2

        # 3. Attempt to start Game 3 in Chat A: Maemma -> must be blocked
        event3 = MockEvent("معما", chat_id=chat_a)
        handled3 = await handle_fox_games(bot, event3, chat_a, 2001, sender, "معما")
        assert handled3 is True
        assert maemma.is_active(chat_a) is False
        assert any(LIMIT_EXCEEDED_MESSAGE in r for r in event3.replies)
        assert active_game_count(chat_a) == 2

        # 4. Group Isolation: Chat B should still be able to start games
        event_b = MockEvent("معما", chat_id=chat_b)
        handled_b = await handle_fox_games(bot, event_b, chat_b, 3001, sender, "معما")
        assert handled_b is True
        assert maemma.is_active(chat_b) is True
        assert active_game_count(chat_b) == 1

        # 5. Finish Game 1 in Chat A (Laugh or Lose)
        laugh_or_lose.reset_all(chat_a)
        assert laugh_or_lose.is_active(chat_a) is False
        assert active_game_count(chat_a) == 1

        # 6. Now Chat A can start Game 3 (Maemma)
        event3_retry = MockEvent("معما", chat_id=chat_a)
        handled3_retry = await handle_fox_games(bot, event3_retry, chat_a, 2001, sender, "معما")
        assert handled3_retry is True
        assert maemma.is_active(chat_a) is True
        assert active_game_count(chat_a) == 2

        # Cleanup
        reset_fox_games()

    asyncio.run(scenario())
