"""😂 بخند یا بباز — اولین کسی که بعد از شمارش معکوس بخندد برنده است.

state، تایمر و session کاملاً مخصوص همین بازی است.
"""
import asyncio

from modules.fox_games.session_core import SessionStore, display_name, log, log_error

GAME_NAME = "laugh_or_lose"
COMMAND = "بخند یا بباز"
COUNTDOWN_SECONDS = 3
ROUND_TIMEOUT = 60
# مقدار جایزه از جدول واحد اقتصاد خوانده می‌شود تا در یک جا تعریف شود
# و با مقدار پرداختی و متن پیام هم‌خوان بماند.
try:
    from economy import rewards as _rewards
    WINNER_COINS = _rewards.amount_for("laugh_or_lose")
except Exception:  # اگر اقتصاد در دسترس نبود، بازی نباید بخوابد.
    WINNER_COINS = 3

LAUGH_EMOJIS = frozenset({"😂", "🤣", "😆", "😹", "😄", "😁"})

_STORE = SessionStore(GAME_NAME)

ALREADY_RUNNING = "😂 بازی بخند یا بباز همین حالا در جریان است."
NO_WINNER = "😐 کسی نخندید! بازی بدون برنده تمام شد."


def is_active(chat_id):
    return _STORE.is_active(chat_id)


def is_accepting(chat_id):
    """آیا بازی در مرحلهٔ پذیرش خنده است (پس از شمارش معکوس)."""
    session = _STORE.get(chat_id)
    return bool(session and session.get("phase") == "open")


def contains_laugh(text):
    """آیا متن حاوی یکی از ایموجی‌های مجاز خنده است."""
    if not text:
        return False
    return any(emoji in str(text) for emoji in LAUGH_EMOJIS)


def start(chat_id, logger=None):
    """session تازه می‌سازد؛ None اگر بازی فعال باشد."""
    session = _STORE.create(chat_id, {
        "phase": "countdown",
        "winner": None,
    })
    if session is None:
        log(logger, f"FOX LAUGH START BLOCKED chat_id={chat_id} reason=already_active")
        return None
    log(logger, f"FOX LAUGH START chat_id={chat_id} session_id={session['session_id']}")
    return dict(session)


def open_round(chat_id, session_id, logger=None):
    """پس از شمارش معکوس، دریافت خنده را باز می‌کند."""
    session = _STORE.get(chat_id)
    if not session or session["session_id"] != session_id:
        return False
    session["phase"] = "open"
    log(logger, f"FOX LAUGH OPEN chat_id={chat_id} session_id={session_id}")
    return True


def claim_win(chat_id, user_id, user, logger=None):
    """اولین خندهٔ معتبر را ثبت می‌کند.

    خروجی: dict برنده، یا None اگر بازی باز نیست یا قبلاً برنده دارد.
    عملیات همگام است، پس دو پیام هم‌زمان نمی‌توانند هر دو برنده شوند.
    """
    session = _STORE.get(chat_id)
    if not session or session.get("phase") != "open":
        return None
    if session.get("winner") is not None:
        return None

    name = display_name(user)
    session["winner"] = {"user_id": user_id, "name": name}
    session["phase"] = "closed"
    closed = _STORE.close(chat_id, session["session_id"])
    _STORE.cancel_task(chat_id)
    if closed is None:
        return None
    log(logger,
        f"FOX LAUGH WINNER chat_id={chat_id} session_id={session['session_id']} "
        f"user_id={user_id} name={name}")
    return {"user_id": user_id, "name": name,
            "session_id": session["session_id"], "coins": WINNER_COINS}


def abandon(chat_id, session_id=None, logger=None):
    """پایان بدون برنده."""
    session = _STORE.close(chat_id, session_id)
    _STORE.cancel_task(chat_id)
    if session:
        log(logger, f"FOX LAUGH TIMEOUT chat_id={chat_id} "
                    f"session_id={session['session_id']}")
    return bool(session)


async def run_round(chat_id, session_id, on_countdown, on_open, on_timeout,
                    logger=None, countdown=None, timeout=None):
    """چرخهٔ کامل بازی: شمارش معکوس، باز کردن، سپس timeout."""
    steps = COUNTDOWN_SECONDS if countdown is None else countdown
    limit = ROUND_TIMEOUT if timeout is None else timeout
    try:
        for remaining in range(steps, 0, -1):
            await on_countdown(remaining)
            await asyncio.sleep(1 if countdown is None else 0)
        if not open_round(chat_id, session_id, logger):
            return
        await on_open()
        await asyncio.sleep(limit)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        log_error(logger, f"FOX LAUGH ROUND FAILED chat_id={chat_id} error={error!r}")
    finally:
        session = _STORE.get(chat_id)
        if session and session["session_id"] == session_id:
            abandon(chat_id, session_id, logger)
            try:
                await on_timeout()
            except Exception as error:
                log_error(logger,
                          f"FOX LAUGH TIMEOUT MSG FAILED chat_id={chat_id} error={error!r}")


def schedule(chat_id, session_id, on_countdown, on_open, on_timeout,
             logger=None, countdown=None, timeout=None):
    return _STORE.schedule(chat_id, lambda: run_round(
        chat_id, session_id, on_countdown, on_open, on_timeout,
        logger, countdown, timeout,
    ))


def reset_all(chat_id=None):
    _STORE.reset(chat_id)
