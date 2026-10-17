"""Regression guards for answer/timer races in independent game modules."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import multiple_choice as quiz
from modules.fox_games import sentence_guess


def test_sentence_stale_timer_cannot_finish_new_round():
    chat_id, user_id = -99001, 501
    key = sentence_guess._key(chat_id, user_id)
    sentence_guess._ACTIVE.clear()
    old = {
        "token": "old-round", "answer": "پاسخ قدیمی", "started_at": 0,
        "chat_id": chat_id, "user_id": user_id,
    }
    new = {
        "token": "new-round", "answer": "پاسخ جدید", "started_at": 0,
        "chat_id": chat_id, "user_id": user_id,
    }
    sentence_guess._ACTIVE[key] = old
    # The old timeout fires after a newer state has replaced its round.
    sentence_guess._ACTIVE[key] = new
    assert sentence_guess.timeout(chat_id, user_id, "old-round") is None
    assert sentence_guess.has_active(chat_id, user_id)
    assert sentence_guess.answer(chat_id, "پاسخ جدید", user_id)["token"] == "new-round"
    assert not sentence_guess.has_active(chat_id, user_id)


def test_sentence_correct_answer_wins_over_expiry_read_path():
    chat_id, user_id = -99002, 502
    key = sentence_guess._key(chat_id, user_id)
    sentence_guess._ACTIVE.clear()
    sentence_guess._ACTIVE[key] = {
        "token": "deadline-round", "answer": "جواب درست", "started_at": 0,
        "chat_id": chat_id, "user_id": user_id,
    }
    # Router-level active check must not erase the answer before the answer
    # handler gets its atomic chance to finish the round.
    assert sentence_guess.has_active(chat_id, user_id)
    assert sentence_guess.answer(chat_id, "جواب درست", user_id)["answer"] == "جواب درست"
    assert sentence_guess.timeout(chat_id, user_id, "deadline-round") is None


def test_quiz_stale_timer_cannot_clear_new_question_and_correct_answer_is_once():
    chat_id = -99003
    quiz._active_questions.clear()
    quiz._active_questions[chat_id] = {
        "token": "new-question", "answer": 2, "index": 1,
    }
    # A timer for an older round must not clear the newer active question.
    assert not quiz.clear_question(chat_id, "old-question")
    assert quiz.get_active_question(chat_id)["token"] == "new-question"
    assert quiz.answer_question(chat_id, "۲", user_id=503) == (True, 2)
    # A repeated delivery of the same option cannot issue another reward.
    assert quiz.answer_question(chat_id, "2", user_id=503) is None
