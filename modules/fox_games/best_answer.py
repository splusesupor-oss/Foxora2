"""🎯 بهترین جواب — بازی گروهی.

یک سوال فکری در گروه مطرح می‌شود و اعضا پاسخ‌های خود را می‌فرستند. بعد
از ۴۰ ثانیه، «بهترین» پاسخ بر اساس معیار مشخص انتخاب و ۴ سکه برنز به
برنده داده می‌شود.

ساختار:
- هر گروه فقط یک دور فعال دارد (``SessionStore`` با کلید chat_id).
- پاسخ‌ها فقط به همان دور و همان گروه تعلق دارند.
- هر کاربر یک پاسخ ثبت می‌کند (اولین پاسخش ثبت می‌شود).
- قضاوت: امتیاز هر پاسخ = (تعداد کلیدواژه‌های مفهوم درست حاضر) و سپس
  شباهت توکن. برنده بالاترین امتیاز، و در تساوی اولین پاسخ.
- بدون پاسخ → پایان بدون خطا، بدون برنده.

جایزه: ۴ سکه برنز از ``economy.rewards``. reference یکتا و ماندگار.
"""
import asyncio
import random
import time

from economy import game_progress as _gp
from modules.fox_games.answer_analysis import (
    analyze as _analyze_answer,
    pick_best as _pick_best,
)
from modules.fox_games.best_answer_questions import BEST_ANSWER_QUESTIONS
from modules.fox_games.session_core import (
    SessionStore,
    log,
    log_error,
    normalize_text,
    to_persian_digits,
)

GAME = "best_answer"
COMMAND = "بهترین جواب"
ANSWER_SECONDS = 40
REWARD = 2
RECENT_WINDOW = 20

_STORE = SessionStore(GAME)
_RANDOM = random.SystemRandom()


def is_active(chat_id):
    return _STORE.is_active(chat_id)


def _norm(value):
    return normalize_text(value).replace(" ", "")


def _score(answer_text, keywords):
    """امتیاز سازگار با تحلیل جدید (برای تست/لاگ).

    برندهٔ واقعی با ``_pick_best`` تعیین می‌شود؛ این فقط یک نمای ساده از
    کیفیت می‌دهد تا رفتار قدیمی در لاگ‌ها قابل مقایسه بماند.
    """
    from modules.fox_games import answer_analysis as _aa
    result = _aa.analyze("", keywords, answer_text)
    if not result["valid"]:
        return 0, _norm(answer_text)
    return result["score"], _norm(answer_text)


def start(chat_id, logger=None):
    """دور تازه شروع می‌کند؛ None اگر بازی فعال است یا بانک خالی است."""
    if _STORE.is_active(chat_id):
        return None

    recent = set(_gp.recent(chat_id, GAME))
    remaining = [q for q in BEST_ANSWER_QUESTIONS if q[0] not in recent]
    if not remaining:
        remaining = list(BEST_ANSWER_QUESTIONS)
    question, keywords, sample = _RANDOM.choice(remaining)
    _gp.mark_recent(chat_id, GAME, question, RECENT_WINDOW)

    session = _STORE.create(chat_id, {
        "question": question,
        "keywords": tuple(keywords),
        "sample": sample,
        "question_msg_id": None,   # شناسهٔ پیامِ سوال؛ برای ریپلای
        "answers": {},          # user_id -> {"name","text","ts"}
        "order": [],
        "winner": None,
        "answered": 0,
    })
    if session is None:
        return None
    log(logger, f"BEST_ANSWER START chat_id={chat_id} "
                f"session_id={session['session_id']} q={question[:40]!r}")
    return dict(session)


def set_question_msg_id(chat_id, msg_id):
    """شناسهٔ پیامِ سوال را در سشن ثبت می‌کند (برای بررسی ریپلای)."""
    session = _STORE.get(chat_id)
    if session is None:
        return False
    session["question_msg_id"] = msg_id
    return True


def submit(chat_id, user_id, name, text, reply_to_msg_id=None, logger=None):
    """پاسخ را فقط اگر «ریپلای مستقیم به سوال» باشد ثبت می‌کند.

    هر کاربر فقط اولین پاسخش ثبت می‌شود. پیامِ بدون ریپلای یا ریپلایِ به
    پیامِ دیگری به‌عنوان پاسخ ثبت نمی‌شود.

    خروجی: "ok" اگر ثبت شد، "already" اگر قبلاً پاسخ داده بود،
    "not_reply" اگر ریپلایِ درست نبود، None اگر دوری فعال نیست.
    """
    session = _STORE.get(chat_id)
    if not session or session.get("finished"):
        return None
    # فقط ریپلایِ مستقیم به پیامِ سوال به‌عنوان پاسخ پذیرفته می‌شود.
    if session.get("question_msg_id") is None \
            or reply_to_msg_id != session.get("question_msg_id"):
        return "not_reply"
    if user_id in session["answers"]:
        return "already"  # قبلاً پاسخ داده، دست نخورده
    session["answers"][user_id] = {
        "name": name,
        "text": text,
        "ts": time.monotonic(),
    }
    session["order"].append(user_id)
    session["answered"] += 1
    log(logger, f"BEST_ANSWER SUBMIT chat_id={chat_id} user_id={user_id}")
    return "ok"


def judge(chat_id, session_id, logger=None):
    """برنده را با معیار مشخص تعیین می‌کند و دور را می‌بندد.

    خروجی دیکشنری برنده (شامل session_id برای reference) یا None.
    پرداخت توسط روتر (از راه `_coins`) انجام می‌شود.
    """
    session = _STORE.get(chat_id)
    if not session or session["session_id"] != session_id:
        return None
    if session.get("finished"):
        return session.get("winner")

    session["finished"] = True
    answers = session["answers"]

    winner = None
    if answers:
        # پاسخ‌ها به ترتیب ثبت؛ تحلیل و انتخابِ بهترین پاسخِ معتبر
        ordered = [
            {"user_id": uid, "name": a["name"], "text": a["text"],
             "ts": a["ts"]}
            for uid, a in answers.items()
        ]
        picked = _pick_best(session["question"], session["keywords"], ordered)
        if picked is not None:
            picked["session_id"] = session_id
            winner = picked

    closed = _STORE.close(chat_id, session_id)
    if closed is None:
        return winner
    # اگر judge از درونِ خودِ تایمر اجرا شده باشد، تسکِ جاری را cancel
    # نکن تا پاسخِ پایان (on_finish) قطع نشود.
    task = _STORE.task_for(chat_id)
    current = asyncio.current_task()
    if task is not None and task is not current and not task.done():
        task.cancel()
    if task is not None and task is current:
        _STORE._tasks.pop(chat_id, None)

    if winner:
        log(logger, f"BEST_ANSWER WINNER chat_id={chat_id} "
                    f"user_id={winner['user_id']}")
    else:
        log(logger, f"BEST_ANSWER NO_WINNER chat_id={chat_id} "
                    f"answers={len(answers)}")
    return winner


async def run_timeout(chat_id, session_id, on_finish, logger=None,
                      timeout=None):
    limit = ANSWER_SECONDS if timeout is None else timeout
    try:
        await asyncio.sleep(limit)
    except asyncio.CancelledError:
        raise
    finally:
        winner = judge(chat_id, session_id, logger)
        try:
            await on_finish(winner)
        except Exception:
            pass


def schedule(chat_id, session_id, on_finish, logger=None, timeout=None):
    return _STORE.schedule(chat_id, lambda: run_timeout(
        chat_id, session_id, on_finish, logger, timeout))


def reset_all(chat_id=None):
    _STORE.reset(chat_id)
