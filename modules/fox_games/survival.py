"""🏕 بقا — ثبت‌نام ۴ نفره، سوال‌های مرحله‌ای، آخرین بازمانده برنده است."""
import asyncio
import random

from modules.fox_games.session_core import (
    SessionStore,
    display_name,
    log,
    log_error,
    normalize_text,
)
from modules.fox_games.survival_questions import (
    LEVELS,
    all_questions,
    level_pool,
)

GAME_NAME = "survival"
COMMAND = "بقا"
JOIN_WORD = "شرکت"

MAX_PLAYERS = 4
# حداقل تعداد بازیکن برای شروع. با ۱ نفر بازی معنا ندارد: تک‌بازیکن بدون
# رقیب برنده می‌شد و ۸ سکه می‌گرفت. قابل تنظیم بین ۲ تا ۴ با set_min_players.
MIN_PLAYERS = 2
MIN_PLAYERS_FLOOR = 2
JOIN_SECONDS = 60
ANSWER_SECONDS = 30
CORRECT_COINS = 1          # سکهٔ هر پاسخ صحیح در هر مرحله
WINNER_COINS = 8           # جایزهٔ برندهٔ نهایی، علاوه بر سکه‌های مراحل
# سقف مرحله‌ها: بازی حتی اگر همه ساکت بمانند هم باید پایان بپذیرد.
MAX_ROUNDS = 20

_STORE = SessionStore(GAME_NAME)
_RANDOM = random.SystemRandom()

# تاریخچهٔ سوال‌های استفاده‌شده به تفکیک گروه. روی «کل بانک» نگه داشته
# می‌شود و بین بازی‌های پشت‌سرهم باقی می‌ماند، پس هیچ سوالی تا مصرف شدن
# کامل بانک تکرار نمی‌شود. این ساختار فقط متعلق به بازی بقاست.
_CHAT_HISTORY = {}

ALREADY_RUNNING = "🏕 بازی بقا همین حالا در جریان است."
NOT_ENOUGH = "🏕 تعداد شرکت‌کننده کافی نبود؛ بازی لغو شد."
NO_WINNER = "🏕 همه حذف شدند؛ بازی بدون برنده تمام شد."


def set_min_players(value):
    """حداقل تعداد بازیکن را بین ۲ تا ``MAX_PLAYERS`` تنظیم می‌کند."""
    global MIN_PLAYERS
    try:
        number = int(value)
    except (TypeError, ValueError):
        return MIN_PLAYERS
    MIN_PLAYERS = max(MIN_PLAYERS_FLOOR, min(number, MAX_PLAYERS))
    return MIN_PLAYERS


def waiting_message(chat_id):
    """پیام انتظار تا وقتی حداقل تعداد تکمیل نشده است."""
    joined = player_count(chat_id)
    return (
        f"⏳ در انتظار بازیکنان...\n\n"
        f"ثبت‌نام شده: {joined} از حداقل {MIN_PLAYERS} نفر\n"
        f"برای شرکت بنویسید: {JOIN_WORD}"
    )


def has_minimum(chat_id):
    """آیا حداقل تعداد لازم برای شروع ثبت‌نام کرده‌اند."""
    return player_count(chat_id) >= MIN_PLAYERS


def is_active(chat_id):
    return _STORE.is_active(chat_id)


def phase(chat_id):
    session = _STORE.get(chat_id)
    return session.get("phase") if session else None


def start(chat_id, logger=None):
    session = _STORE.create(chat_id, {
        "phase": "joining",
        "players": {},
        "order": [],
        "level": 0,
        "question": None,
        "answered": set(),
        "used_questions": set(),
    })
    if session is None:
        log(logger, f"FOX SURVIVAL START BLOCKED chat_id={chat_id} reason=already_active")
        return None
    log(logger, f"FOX SURVIVAL START chat_id={chat_id} session_id={session['session_id']}")
    return dict(session)


def join(chat_id, user_id, user, logger=None):
    """ثبت‌نام یک بازیکن.

    خروجی ``(state, players)`` — state در
    ``{"joined","duplicate","full","closed"}``.
    """
    session = _STORE.get(chat_id)
    if not session or session.get("phase") != "joining":
        return "closed", []
    key = str(user_id)
    if key in session["players"]:
        return "duplicate", list(session["order"])
    if len(session["players"]) >= MAX_PLAYERS:
        return "full", list(session["order"])

    name = display_name(user)
    session["players"][key] = {
        "user_id": user_id, "name": name, "alive": True, "round_coins": 0,
    }
    session["order"].append(name)
    log(logger,
        f"FOX SURVIVAL JOIN chat_id={chat_id} user_id={user_id} name={name} "
        f"count={len(session['players'])}")
    return "joined", list(session["order"])


def player_count(chat_id):
    session = _STORE.get(chat_id)
    return len(session["players"]) if session else 0


def is_full(chat_id):
    return player_count(chat_id) >= MAX_PLAYERS


def alive_players(chat_id):
    session = _STORE.get(chat_id)
    if not session:
        return []
    return [p for p in session["players"].values() if p["alive"]]


def begin_rounds(chat_id, logger=None):
    """پایان ثبت‌نام و آغاز مراحل.

    ``False`` یعنی حداقل تعداد بازیکن تکمیل نشده و بازی نباید شروع شود.
    پس از موفقیت، phase به ``playing`` می‌رود و ``join`` هیچ بازیکن جدیدی
    را نمی‌پذیرد.
    """
    session = _STORE.get(chat_id)
    if not session or session.get("phase") != "joining":
        return False
    if len(session["players"]) < MIN_PLAYERS:
        log(logger, f"FOX SURVIVAL ABORT chat_id={chat_id} reason=not_enough_players "
                    f"count={len(session['players'])} required={MIN_PLAYERS}")
        return False
    session["phase"] = "playing"
    log(logger, f"FOX SURVIVAL BEGIN chat_id={chat_id} "
                f"players={len(session['players'])} required={MIN_PLAYERS}")
    return True


def history_size(chat_id):
    """تعداد سوال‌هایی که این گروه تا کنون دیده است."""
    return len(_CHAT_HISTORY.get(str(chat_id), ()))


def remaining_questions(chat_id):
    """تعداد سوال‌های دیده‌نشدهٔ باقی‌مانده برای این گروه."""
    return max(len(all_questions()) - history_size(chat_id), 0)


def reset_history(chat_id=None):
    """تاریخچهٔ سوال یک گروه (یا همهٔ گروه‌ها) را پاک می‌کند."""
    if chat_id is None:
        _CHAT_HISTORY.clear()
        return
    _CHAT_HISTORY.pop(str(chat_id), None)


def next_question(chat_id, logger=None):
    """سوال مرحلهٔ بعد: تصادفی، ترجیحاً سخت‌تر، و بدون تکرار.

    تاریخچه روی «کل بانک» نگه داشته می‌شود، نه هر سطح جداگانه. تا وقتی
    حتی یک سوال دیده‌نشده در بانک باقی باشد هیچ سوالی تکرار نمی‌شود:

      ۱. سوال دیده‌نشده از سطح فعلی (حفظ منحنی سختی).
      ۲. اگر سطح فعلی تمام شده بود، سوال دیده‌نشده از نزدیک‌ترین سطح دیگر
         — از سطح‌های سخت‌تر شروع می‌شود تا بازی آسان‌تر نشود.
      ۳. فقط وقتی کل بانک مصرف شد، تاریخچه ریست می‌شود و دور تازه با
         انتخاب تصادفی آغاز می‌گردد.
    """
    session = _STORE.get(chat_id)
    if not session or session.get("phase") != "playing":
        return None
    session["level"] += 1
    chat_seen = _CHAT_HISTORY.setdefault(str(chat_id), set())
    bank = all_questions()

    def unseen(items):
        return [
            item for item in items
            if item[0] not in session["used_questions"]
            and item[0] not in chat_seen
        ]

    # ۱) سطح فعلی
    pool = unseen(level_pool(session["level"]))

    # ۲) سطح‌های دیگر، از سخت‌تر به آسان‌تر
    if not pool:
        current = min(max(session["level"], 1), len(LEVELS))
        order = list(range(current + 1, len(LEVELS) + 1))
        order += list(range(current - 1, 0, -1))
        for level in order:
            pool = unseen(level_pool(level))
            if pool:
                log(logger, f"FOX SURVIVAL LEVEL FALLBACK chat_id={chat_id} "
                            f"from={current} to={level}")
                break

    # ۳) کل بانک مصرف شده: ریست کامل تاریخچه
    if not pool:
        log(logger, f"FOX SURVIVAL HISTORY RESET chat_id={chat_id} "
                    f"bank={len(bank)} reason=exhausted")
        chat_seen.clear()
        session["used_questions"].clear()
        pool = unseen(level_pool(session["level"])) or unseen(bank) or list(bank)

    question, answer, aliases = _RANDOM.choice(pool)
    session["used_questions"].add(question)
    chat_seen.add(question)
    session["question"] = {
        "text": question,
        "answer": answer,
        "aliases": tuple(aliases),
    }
    session["answered"] = set()
    log(logger,
        f"FOX SURVIVAL QUESTION chat_id={chat_id} level={session['level']} "
        f"alive={len(alive_players(chat_id))}")
    return {
        "level": session["level"],
        "text": question,
        "alive": [p["name"] for p in alive_players(chat_id)],
    }


def _accepted(question):
    values = {normalize_text(question["answer"])}
    values |= {normalize_text(alias) for alias in question["aliases"]}
    return {value for value in values if value}


def answer(chat_id, user_id, text, logger=None):
    """پاسخ یک بازیکن.

    خروجی ``(state, info)`` — state در
    ``{"correct","wrong","not_player","already","no_question"}``.
    """
    session = _STORE.get(chat_id)
    if not session or session.get("phase") != "playing" or not session["question"]:
        return "no_question", None
    key = str(user_id)
    player = session["players"].get(key)
    if not player or not player["alive"]:
        return "not_player", None
    if key in session["answered"]:
        return "already", None

    session["answered"].add(key)
    if normalize_text(text) in _accepted(session["question"]):
        # سکهٔ مرحله فقط یک بار برای هر مرحله و فقط به پاسخ صحیح تعلق می‌گیرد.
        player["round_coins"] = player.get("round_coins", 0) + CORRECT_COINS
        log(logger, f"FOX SURVIVAL CORRECT chat_id={chat_id} user_id={user_id} "
                    f"level={session['level']} coins={CORRECT_COINS} "
                    f"total_round_coins={player['round_coins']}")
        return "correct", player

    player["alive"] = False
    log(logger, f"FOX SURVIVAL ELIMINATED chat_id={chat_id} user_id={user_id} "
                f"reason=wrong_answer level={session['level']}")
    return "wrong", player


def eliminate_silent(chat_id, logger=None):
    """هر بازیکنی که در مهلت پاسخ نداد حذف می‌شود.

    اگر حذف هم‌زمان باعث شود هیچ بازمانده‌ای نماند، آخرین گروه دوباره زنده
    می‌شوند تا بازی بدون برنده تمام نشود و مرحلهٔ بعد تعیین‌کننده باشد.
    """
    session = _STORE.get(chat_id)
    if not session or session.get("phase") != "playing":
        return []
    before = [p for p in session["players"].values() if p["alive"]]
    removed = []
    for key, player in session["players"].items():
        if player["alive"] and key not in session["answered"]:
            player["alive"] = False
            removed.append(player)
            log(logger, f"FOX SURVIVAL ELIMINATED chat_id={chat_id} "
                        f"user_id={player['user_id']} reason=timeout "
                        f"level={session['level']}")

    if removed and not any(p["alive"] for p in session["players"].values()):
        # همه هم‌زمان ساکت ماندند. اگر بیش از یک نفر بودند همه برمی‌گردند تا
        # مرحلهٔ بعد تصمیم بگیرد. اگر فقط یک نفر مانده بود، او *زنده نمی‌شود*:
        # کسی که هیچ پاسخی نداده نباید برندهٔ ۸ سکه شود؛ بازی بدون برنده
        # تمام می‌شود.
        if len(before) == 1:
            log(logger, f"FOX SURVIVAL NO WINNER chat_id={chat_id} "
                        f"reason=last_player_silent "
                        f"user_id={before[0]['user_id']} level={session['level']}")
        else:
            for player in before:
                player["alive"] = True
            removed = []
            log(logger, f"FOX SURVIVAL TIE RESTORED chat_id={chat_id} "
                        f"players={len(before)} level={session['level']}")
    return removed


def winner(chat_id):
    """برنده وقتی دقیقاً یک بازمانده باقی مانده باشد."""
    alive = alive_players(chat_id)
    return alive[0] if len(alive) == 1 else None


def finish(chat_id, session_id=None, logger=None):
    """پایان بازی و برگرداندن برنده (یا None)."""
    session = _STORE.get(chat_id)
    if not session:
        return None
    alive = [p for p in session["players"].values() if p["alive"]]
    closed = _STORE.close(chat_id, session_id or session["session_id"])
    _STORE.cancel_task(chat_id)
    if closed is None:
        return None
    champion = alive[0] if len(alive) == 1 else None
    log(logger,
        f"FOX SURVIVAL END chat_id={chat_id} session_id={closed['session_id']} "
        f"winner={champion['name'] if champion else None}")
    return champion


def abandon(chat_id, session_id=None, logger=None):
    session = _STORE.close(chat_id, session_id)
    _STORE.cancel_task(chat_id)
    if session:
        log(logger, f"FOX SURVIVAL ABANDON chat_id={chat_id} "
                    f"session_id={session['session_id']}")
    return bool(session)


async def run_game(chat_id, session_id, callbacks, logger=None,
                   join_seconds=None, answer_seconds=None):
    """چرخهٔ کامل بازی. نتایج همیشه اعلام می‌شوند، حتی با لغو یا خطا."""
    join_wait = JOIN_SECONDS if join_seconds is None else join_seconds
    answer_wait = ANSWER_SECONDS if answer_seconds is None else answer_seconds
    finished_cleanly = False
    try:
        # --- مرحلهٔ ثبت‌نام ---
        # تمام مهلت ثبت‌نام سپری می‌شود مگر ظرفیت پر شود. با اولین «شرکت»
        # بازی شروع نمی‌شود؛ باید دست‌کم MIN_PLAYERS نفر جمع شوند.
        waited = 0.0
        step = 0.05 if join_wait <= 2 else 0.5
        while waited < join_wait and not is_full(chat_id):
            await asyncio.sleep(step)
            waited += step
        session = _STORE.get(chat_id)
        if not session or session["session_id"] != session_id:
            return
        if not begin_rounds(chat_id, logger):
            # مهلت تمام شد و تعداد کافی نبود: همهٔ state پاک می‌شود.
            abandon(chat_id, session_id, logger)
            await callbacks["on_abort"]()
            finished_cleanly = True
            return

        await callbacks["on_begin"]([p["name"] for p in alive_players(chat_id)])

        # --- مراحل سوال ---
        while True:
            session = _STORE.get(chat_id)
            if not session or session["session_id"] != session_id:
                return
            question = next_question(chat_id, logger)
            if question is None:
                return
            await callbacks["on_question"](question)
            await asyncio.sleep(answer_wait)

            session = _STORE.get(chat_id)
            if not session or session["session_id"] != session_id:
                return
            removed = eliminate_silent(chat_id, logger)
            if removed:
                await callbacks["on_eliminated"]([p["name"] for p in removed])

            alive = alive_players(chat_id)
            current = _STORE.get(chat_id)
            reached_cap = current is not None and current["level"] >= MAX_ROUNDS
            if len(alive) <= 1 or reached_cap:
                if reached_cap and len(alive) > 1:
                    log(logger, f"FOX SURVIVAL ROUND CAP chat_id={chat_id} "
                                f"alive={len(alive)}")
                champion = finish(chat_id, session_id, logger)
                await callbacks["on_finish"](champion)
                finished_cleanly = True
                return
    except asyncio.CancelledError:
        raise
    except Exception as error:
        log_error(logger, f"FOX SURVIVAL LOOP FAILED chat_id={chat_id} error={error!r}")
    finally:
        if not finished_cleanly:
            session = _STORE.get(chat_id)
            if session and session["session_id"] == session_id:
                champion = finish(chat_id, session_id, logger)
                try:
                    await callbacks["on_finish"](champion)
                except Exception as error:
                    log_error(logger, f"FOX SURVIVAL FINAL MSG FAILED "
                                      f"chat_id={chat_id} error={error!r}")


def schedule(chat_id, session_id, callbacks, logger=None,
             join_seconds=None, answer_seconds=None):
    return _STORE.schedule(chat_id, lambda: run_game(
        chat_id, session_id, callbacks, logger, join_seconds, answer_seconds))


def reset_all(chat_id=None):
    _STORE.reset(chat_id)
