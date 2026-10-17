"""🎁 جعبه شانسی — ۹ جعبه، ۴ پوچ، ۵ جایزه، با سهمیهٔ واقعی ۲۴ ساعته.

سهمیه روی دیسک ذخیره می‌شود تا ری‌استارت ربات آن را صفر نکند.
"""
import json
import random
import time
from pathlib import Path

from modules.runtime_paths import runtime_config_file
from modules.atomic_write import write_json

from modules.fox_games.session_core import (
    SessionStore,
    log,
    parse_int,
    to_persian_digits,
)

GAME_NAME = "lucky_box"
COMMAND = "جعبه شانسی"

BOX_COUNT = 9
EMPTY_BOXES = 4
PRIZE_BOXES = BOX_COUNT - EMPTY_BOXES      # ۵ جعبه جایزه
MIN_PRIZE = 1
MAX_PRIZE = 15
DAILY_LIMIT = 2
QUOTA_WINDOW = 24 * 60 * 60
PICK_TIMEOUT = 60

STATE_FILE = runtime_config_file("fox_lucky_box.json")

_STORE = SessionStore(GAME_NAME)
_RANDOM = random.SystemRandom()
_QUOTA = None

BOARD = (
    "┌───┬───┬───┐\n"
    "│ 1 │ 2 │ 3 │\n"
    "├───┼───┼───┤\n"
    "│ 4 │ 5 │ 6 │\n"
    "├───┼───┼───┤\n"
    "│ 7 │ 8 │ 9 │\n"
    "└───┴───┴───┘"
)

ALREADY_RUNNING = "🎁 جعبه شانسی همین حالا در جریان است."


# --------------------------------------------------------------------------
# سهمیهٔ روزانه (ماندگار روی دیسک)
# --------------------------------------------------------------------------
def _load_quota():
    global _QUOTA
    if _QUOTA is not None:
        return _QUOTA
    try:
        _QUOTA = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _QUOTA = {}
    return _QUOTA


def _save_quota():
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        write_json(STATE_FILE, _QUOTA or {})
    except OSError:
        pass


def _recent_plays(user_id):
    """زمان بازی‌های ۲۴ ساعت اخیر این کاربر (wall clock)."""
    quota = _load_quota()
    now = time.time()
    stamps = [t for t in quota.get(str(user_id), []) if now - t < QUOTA_WINDOW]
    quota[str(user_id)] = stamps
    return stamps


def remaining_plays(user_id):
    return max(0, DAILY_LIMIT - len(_recent_plays(user_id)))


def seconds_until_next(user_id):
    """ثانیهٔ باقی‌مانده تا آزاد شدن یک نوبت. صفر یعنی سهمیه دارد."""
    stamps = _recent_plays(user_id)
    if len(stamps) < DAILY_LIMIT:
        return 0
    oldest = min(stamps[-DAILY_LIMIT:])
    return max(0, int(QUOTA_WINDOW - (time.time() - oldest)))


def format_wait(seconds):
    hours, remainder = divmod(int(seconds), 3600)
    minutes = remainder // 60
    if hours and minutes:
        return f"{to_persian_digits(hours)} ساعت و {to_persian_digits(minutes)} دقیقه دیگر"
    if hours:
        return f"{to_persian_digits(hours)} ساعت دیگر"
    if minutes:
        return f"{to_persian_digits(minutes)} دقیقه دیگر"
    return "کمتر از یک دقیقه دیگر"


def quota_message(user_id):
    return (
        "⏳ سهمیه امروز شما تمام شده است.\n\n"
        "بازی بعدی:\n"
        f"{format_wait(seconds_until_next(user_id))}"
    )


def _consume_quota(user_id):
    _recent_plays(user_id)
    _load_quota().setdefault(str(user_id), []).append(time.time())
    _save_quota()


# --------------------------------------------------------------------------
# چیدمان جعبه‌ها
# --------------------------------------------------------------------------
def build_boxes():
    """هر بار از نو: ۴ پوچ و ۵ جایزهٔ تصادفی ۱ تا ۱۵ سکه."""
    prizes = [0] * EMPTY_BOXES + [
        _RANDOM.randint(MIN_PRIZE, MAX_PRIZE) for _ in range(PRIZE_BOXES)
    ]
    _RANDOM.shuffle(prizes)
    return {index + 1: prize for index, prize in enumerate(prizes)}


def is_active(chat_id):
    return _STORE.is_active(chat_id)


def start(chat_id, user_id, logger=None):
    """بازی را شروع می‌کند.

    خروجی ``(session, error)`` — error یکی از ``"active"`` یا ``"quota"``.
    """
    if _STORE.is_active(chat_id):
        return None, "active"
    if remaining_plays(user_id) <= 0:
        log(logger, f"FOX BOX QUOTA BLOCK chat_id={chat_id} user_id={user_id}")
        return None, "quota"

    boxes = build_boxes()
    session = _STORE.create(chat_id, {
        "user_id": user_id,
        "boxes": boxes,
        "opened": False,
    })
    if session is None:
        return None, "active"
    _consume_quota(user_id)
    log(logger,
        f"FOX BOX START chat_id={chat_id} user_id={user_id} "
        f"session_id={session['session_id']} remaining={remaining_plays(user_id)}")
    return dict(session), None


def pick(chat_id, user_id, text, logger=None):
    """انتخاب یک جعبه. فقط صاحب بازی و فقط یک بار.

    خروجی ``(result, error)``؛ error در ``{"not_owner","bad_number","done"}``.
    """
    session = _STORE.get(chat_id)
    if not session:
        return None, None
    if session["user_id"] != user_id:
        return None, "not_owner"
    if session.get("opened"):
        return None, "done"

    number = parse_int(text)
    if number is None or not 1 <= number <= BOX_COUNT:
        return None, "bad_number"

    session["opened"] = True
    prize = session["boxes"][number]
    closed = _STORE.close(chat_id, session["session_id"])
    _STORE.cancel_task(chat_id)
    if closed is None:
        return None, "done"

    log(logger,
        f"FOX BOX PICK chat_id={chat_id} user_id={user_id} box={number} "
        f"prize={prize} session_id={session['session_id']}")
    return {
        "box": number,
        "prize": prize,
        "boxes": dict(session["boxes"]),
        "session_id": session["session_id"],
    }, None


def abandon(chat_id, session_id=None, logger=None):
    session = _STORE.close(chat_id, session_id)
    _STORE.cancel_task(chat_id)
    if session:
        log(logger, f"FOX BOX TIMEOUT chat_id={chat_id} "
                    f"session_id={session['session_id']}")
    return session


async def run_timeout(chat_id, session_id, on_timeout, logger=None, timeout=None):
    import asyncio

    limit = PICK_TIMEOUT if timeout is None else timeout
    try:
        await asyncio.sleep(limit)
    except asyncio.CancelledError:
        raise
    finally:
        session = _STORE.get(chat_id)
        if session and session["session_id"] == session_id:
            abandon(chat_id, session_id, logger)
            try:
                await on_timeout()
            except Exception:
                pass


def schedule(chat_id, session_id, on_timeout, logger=None, timeout=None):
    return _STORE.schedule(chat_id, lambda: run_timeout(
        chat_id, session_id, on_timeout, logger, timeout))


def reset_all(chat_id=None, clear_quota=False):
    _STORE.reset(chat_id)
    if clear_quota:
        global _QUOTA
        _QUOTA = {}
        _save_quota()
