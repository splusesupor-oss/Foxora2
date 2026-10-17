"""⚔️ نبرد — بازی دو نفرهٔ سوال‌وجواب.

- «نبرد» → بازیکن اول (فقط همان کاربر).
- «شرکت» → دومین نفر وارد همان نبرد می‌شود؛ نفر سوم و خودِ بازیکن اول
  نمی‌توانند دوباره وارد شوند.
- هر بازیکن در مجموع ۳ سوال می‌گیرد. بازیکن اول هر ۳ سوالش را پشت‌سرهم
  می‌پرسد (بعد از هر پاسخ، نتیجه همان پاسخ اعلام می‌شود و سوال بعدی همان
  بازیکن نمایش داده می‌شود)، سپس بازیکن دوم هر ۳ سوالش را.
- پاسخِ درست → «✅ پاسخ درست بود!» + ۱ امتیاز. پاسخِ غلط → «❌ پاسخ اشتباه
  بود!» بدون امتیاز. پاسخِ غلط هرگز بازیکن را حذف نمی‌کند؛ بازیکن تا پایان
  ۳ سوال خودش ادامه می‌دهد چه درست چه غلط.
- پس از پایان هر دو بازیکن، نتیجهٔ نهایی اعلام می‌شود: تعداد پاسخ‌های درست
  هر بازیکن و برنده بر اساس تعداد پاسخ‌های درست. اگر مساوی → «🤝 مساوی شد.»
- جایزه: برنده ۲ سکه برنز؛ اگر مساوی شود هر دو بازیکن ۲ سکه برنز.
- اگر مهلت ثبت‌نام تمام شود و بازیکن دوم نیاید، نبرد بدون خطا بسته می‌شود.

ساختار با ``SessionStore`` (کلید chat_id)؛ هر گروه مستقل است.
"""
import asyncio
import random
import time

from economy import game_progress as _gp
from modules.fox_games.battle_questions import BATTLE_QUESTIONS
from modules.fox_games.session_core import (
    SessionStore,
    log,
    log_error,
    normalize_text,
    to_persian_digits,
)

GAME = "battle"
COMMAND = "نبرد"
JOIN_WORD = "شرکت"
QUESTIONS_PER_PLAYER = 3
TOTAL_QUESTIONS = QUESTIONS_PER_PLAYER * 2  # ۶ سوال (۳ برای هر بازیکن)
ANSWER_SECONDS = 30
JOIN_SECONDS = 60
REWARD = 2
RECENT_WINDOW = 30

_STORE = SessionStore(GAME)
_RANDOM = random.SystemRandom()

ALREADY_RUNNING = (
    "⚔️ یک نبرد همین حالا در این گروه در جریان است.\n"
    "لطفاً ابتدا بازی فعلی را تمام کنید و سپس دوباره «نبرد» بفرستید."
)
NOT_ENOUGH = "⏰ مهلت ثبت‌نام تمام شد؛ بازیکن دوم نیامد. نبرد لغو شد."


def is_active(chat_id):
    return _STORE.is_active(chat_id)


def phase(chat_id):
    session = _STORE.get(chat_id)
    if not session:
        return "none"
    return session.get("phase", "joining")


def players(chat_id):
    session = _STORE.get(chat_id)
    if not session:
        return []
    return [session.get("p1"), session.get("p2")]


def _norm(value):
    return normalize_text(value).replace(" ", "")


def _check_answer(question, text):
    expected = _norm(question["answer"])
    aliases = {_norm(a) for a in question.get("aliases", ()) if _norm(a)}
    answer = _norm(text)
    if not expected:
        return False
    if answer == expected:
        return True
    if answer and (expected in answer or answer in expected):
        return True
    for alias in aliases:
        if alias and (alias in answer or answer in alias):
            return True
    return False


def _pick_questions(chat_id):
    recent = set(_gp.recent(chat_id, GAME))
    remaining = [q for q in BATTLE_QUESTIONS if q[0] not in recent]
    if not remaining:
        remaining = list(BATTLE_QUESTIONS)
    _RANDOM.shuffle(remaining)
    chosen = remaining[:TOTAL_QUESTIONS]
    for q in chosen:
        _gp.mark_recent(chat_id, GAME, q[0], RECENT_WINDOW)
    return [
        {"question": q[0], "answer": q[1], "aliases": tuple(q[2])}
        for q in chosen
    ]


def start(chat_id, user_id, name, logger=None):
    """بازیکن اول را ثبت می‌کند. None اگر نبرد فعال است."""
    if _STORE.is_active(chat_id):
        return None
    session = _STORE.create(chat_id, {
        "p1": {"user_id": user_id, "name": name},
        "p2": None,
        "phase": "joining",
        "finished": False,
        "current": None,
        "scores": {},
        "answered_count": {},
        "questions": [],
        "index": 0,
    })
    if session is None:
        return None
    log(logger, f"BATTLE START chat_id={chat_id} p1={user_id} "
                f"session_id={session['session_id']}")
    return dict(session)


def join(chat_id, user_id, name, logger=None):
    """بازیکن دوم را ثبت و بازی را آمادهٔ شروع می‌کند.

    خروجی (result, players)؛ result در {"joined","duplicate","not_open","full"}.
    """
    session = _STORE.get(chat_id)
    if not session:
        return "not_open", None
    if session.get("finished") or session["phase"] != "joining":
        return "full", None
    if session["p1"]["user_id"] == user_id:
        return "duplicate", None
    if session.get("p2") is not None:
        return "full", None
    session["p2"] = {"user_id": user_id, "name": name}
    return "joined", [session["p1"], session["p2"]]


def begin(chat_id, logger=None):
    """بعد از پیوستن بازیکن دوم: سوال‌ها را می‌چیند و وارد مرحلهٔ بازی می‌کند."""
    session = _STORE.get(chat_id)
    if not session or session.get("p2") is None:
        return None
    session["questions"] = _pick_questions(chat_id)
    session["phase"] = "playing"
    session["index"] = 0
    session["scores"] = {
        session["p1"]["user_id"]: 0, session["p2"]["user_id"]: 0,
    }
    session["answered_count"] = {
        session["p1"]["user_id"]: 0, session["p2"]["user_id"]: 0,
    }
    log(logger, f"BATTLE BEGIN chat_id={chat_id} "
                f"session_id={session['session_id']}")
    return dict(session)


def current_question(chat_id):
    session = _STORE.get(chat_id)
    if not session:
        return None
    return session.get("current")


def answer(chat_id, user_id, text, logger=None):
    """پاسخ سوالِ فعلی؛ فقط بازیکنِ همان سوال.

    خروجی (result, info)؛ result در {"no_game","no_question","not_assignee",
    "already","correct","wrong"}.
    """
    session = _STORE.get(chat_id)
    if not session or session.get("finished"):
        return "no_game", None
    if session["phase"] != "playing":
        return "no_game", None
    cur = session.get("current")
    if not cur:
        return "no_question", None
    if cur["assignee"] != user_id:
        return "not_assignee", None
    if cur.get("answered"):
        return "already", None

    correct = _check_answer(cur["question"], text)
    cur["answered"] = True
    session["answered_count"][user_id] = session["answered_count"].get(user_id, 0) + 1
    result = "correct" if correct else "wrong"
    if correct:
        session["scores"][user_id] = session["scores"].get(user_id, 0) + 1
        log(logger, f"BATTLE CORRECT chat_id={chat_id} user_id={user_id}")
    else:
        log(logger, f"BATTLE WRONG chat_id={chat_id} user_id={user_id}")
    fut = cur.get("future")
    if fut is not None and not fut.done():
        fut.set_result(result)
    return result, None


def abort_joining(chat_id, session_id, on_abort, logger=None):
    """مهلت ثبت‌نام تمام شد و بازیکن دوم نیامد."""
    session = _STORE.get(chat_id)
    if not session or session["session_id"] != session_id:
        return
    if session["phase"] != "joining":
        return
    _STORE.close(chat_id, session_id)
    _STORE.cancel_task(chat_id)
    log(logger, f"BATTLE ABORT chat_id={chat_id}")
    try:
        asyncio.get_running_loop().create_task(on_abort())
    except Exception:
        pass


async def run_game(chat_id, on_question, on_answer, on_finish, logger=None):
    """حلقهٔ اصلی نبرد. بعد از تشکیل دو بازیکن اجرا می‌شود.

    ترتیب: نوبتی (متناوب) بین دو بازیکن. برای هر «دور» (۱ تا ۳) ابتدا
    بازیکن اول یک سوال می‌گیرد و بعد بازیکن دوم یک سوال — تا هر دو نفر به
    سوال سوم برسند:

        سوال ۱ → بازیکن اول
        سوال ۱ → بازیکن دوم
        سوال ۲ → بازیکن اول
        سوال ۲ → بازیکن دوم
        سوال ۳ → بازیکن اول
        سوال ۳ → بازیکن دوم

    بعد از پایان هر ۶ سوال، پاسخ‌های هر دو بررسی و نتیجهٔ نهایی اعلام می‌شود.
    """
    session = _STORE.get(chat_id)
    if not session:
        return
    p1, p2 = session["p1"]["user_id"], session["p2"]["user_id"]
    qs = session["questions"]  # ۶ سوالِ مجزا (۳ سوال برای هر بازیکن)

    for round_no in range(1, QUESTIONS_PER_PLAYER + 1):
        # در هر دور: ابتدا بازیکن اول، سپس بازیکن دوم
        for player_num, player_id in ((1, p1), (2, p2)):
            # سوالِ این بازیکن در این دور — هر بازیکن در هر دور یک سوالِ
            # مجزا می‌گیرد؛ سوال برای همان بازیکن تکرار نمی‌شود.
            q = qs[(round_no - 1) * 2 + (player_num - 1)]
            fut = asyncio.get_running_loop().create_future()
            session["current"] = {
                "assignee": player_id,
                "question": q,
                "future": fut,
            }
            try:
                await on_question(player_num, round_no, q, player_id)
            except Exception:
                pass

            result = "timeout"
            try:
                await asyncio.wait_for(fut, timeout=ANSWER_SECONDS)
            except asyncio.TimeoutError:
                log(logger, f"BATTLE TIMEOUT chat_id={chat_id} "
                            f"player={player_id} round={round_no}")
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            else:
                result = fut.result() if fut.done() else "timeout"

            session["current"] = None

            # بعد از هر پاسخ، نتیجهٔ همان پاسخ اعلام می‌شود
            try:
                await on_answer(result, player_id, player_num, round_no)
            except Exception:
                pass


    # پایان بازی
    s1 = session["scores"].get(p1, 0)
    s2 = session["scores"].get(p2, 0)
    if s1 > s2:
        winner = p1
        tie = False
    elif s2 > s1:
        winner = p2
        tie = False
    else:
        winner = None
        tie = True

    session["finished"] = True
    session_id = session["session_id"]
    _STORE.close(chat_id, session_id)
    # تسکِ جاری (خودِ run_game) را cancel نکن تا on_finish قطع نشود.
    task = _STORE.task_for(chat_id)
    current = asyncio.current_task()
    if task is not None and task is not current and not task.done():
        task.cancel()
    if task is not None and task is current:
        _STORE._tasks.pop(chat_id, None)
    result = {
        "p1": session["p1"], "p2": session["p2"],
        "score1": s1, "score2": s2,
        "winner": winner,
        "tie": tie,
        "answered_count": dict(session["answered_count"]),
        "session_id": session_id,
    }
    try:
        await on_finish(result)
    except Exception:
        pass


def schedule_game(chat_id, on_question, on_answer, on_finish, logger=None):
    return _STORE.schedule(chat_id, lambda: run_game(
        chat_id, on_question, on_answer, on_finish, logger))


def schedule_join_timeout(chat_id, session_id, on_abort, logger=None):
    async def _wait():
        try:
            await asyncio.sleep(JOIN_SECONDS)
        except asyncio.CancelledError:
            raise
        finally:
            abort_joining(chat_id, session_id, on_abort, logger)
    return _STORE.schedule(chat_id, _wait)


def reset_all(chat_id=None):
    _STORE.reset(chat_id)
