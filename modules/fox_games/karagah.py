# -*- coding: utf-8 -*-
"""🕵️ بازی «کارگاه» — پیدا کردن دزد و شیء دزدیده‌شده.

جریان بازی (همان سیستم اثبات‌شدهٔ خون‌آشام، با دو مرحله):
    ۱) ثبت‌نام ۵ نفر با «شرکت»
    ۲) یک نفر مخفیانه دزد می‌شود؛ نقش و «شیء دزدیده‌شده» فقط به پیوی او می‌رود
    ۳) گروه ۴۰ ثانیه فرصت دارد دزد را با شماره پیدا کند
    ۴) پیداکنندهٔ دزد باید از بین گزینه‌ها شیء را حدس بزند (۳۰ ثانیه)
       - درست → 🥈 ۱۲ سکه نقره برای برنده
       - غلط یا پایان زمان → دزد برنده؛ 🥉 ۱۲ سکه برنز

state هر گروه کاملاً مستقل است (SessionStore).
"""
import asyncio
import random
import time

from modules.fox_games.session_core import (
    SessionStore, display_name, log, log_error, parse_int,
)

GAME_NAME = "karagah"
COMMAND = "کارگاه"
JOIN_WORD = "شرکت"

# حداقل ۴ و حداکثر ۵ بازیکن؛ اگر ظرفیت ۵ نفر پر شود بازی زودتر شروع
# می‌شود، وگرنه در پایان مهلت ثبت‌نام با ۴ نفر هم تشکیل می‌شود.
MIN_PLAYERS = 4
MAX_PLAYERS = 5
# سازگاری با متن‌های قدیمی
PLAYERS_NEEDED = MAX_PLAYERS
JOIN_SECONDS = 60
THIEF_GUESS_SECONDS = 40
OBJECT_GUESS_SECONDS = 30
WINNER_SILVER = 12
THIEF_BRONZE = 12
OBJECT_OPTIONS = 5

DM_TIMEOUT = 12          # مهلت هر تلاشِ send (ثانیه) — همان مقدار خون‌آشام
DM_RETRIES = 3           # حداکثر تلاش روی همان بازیکن برای خطای موقت

_STORE = SessionStore(GAME_NAME)
_RANDOM = random.SystemRandom()
_LAST_THIEF_BY_CHAT = {}

OBJECTS = (
    "گوشی موبایل", "ساعت طلا", "کیف پول", "گردنبند الماس", "انگشتر عقیق",
    "تابلوی نقاشی", "لپ تاپ", "دوربین عکاسی", "عینک آفتابی", "کلید خانه",
    "مدال طلا", "گلدان عتیقه", "سکه قدیمی", "قالیچه ابریشمی", "هدفون",
    "شمعدان نقره", "کتاب خطی", "جعبه جواهر", "ساعت دیواری", "قلم طلایی",
)

ALREADY_RUNNING = "🕵️ یک پرونده همین حالا در جریان است."
NOT_ENOUGH = "🕵️ تعداد شرکت‌کننده کافی نبود؛ پرونده بسته شد."
DM_FAILED_MESSAGE = (
    "🕵️ این پرونده تشکیل نشد.\n\n"
    "چند لحظه دیگر دوباره «کارگاه» را بفرستید."
)
ROLE_MESSAGE = (
    "🕵️ شما دزد هستید!\n\n"
    "🎒 شیء دزدیده‌شده: {obj}\n\n"
    "تا پایان بازی چیزی نگویید."
)


def is_active(chat_id):
    return _STORE.is_active(chat_id)


def phase(chat_id):
    session = _STORE.get(chat_id)
    return session.get("phase") if session else None


def start(chat_id, logger=None):
    session = _STORE.create(chat_id, {
        "phase": "joining",
        "players": [],
        "ids": set(),
        "thief": None,
        "object": None,
        "options": [],
        "finder": None,
        "guessed": set(),
        "object_deadline": None,
    })
    if session is None:
        log(logger, f"FOX KARAGAH START BLOCKED chat_id={chat_id} reason=already_active")
        return None
    log(logger, f"FOX KARAGAH START chat_id={chat_id} session_id={session['session_id']}")
    return dict(session)


def join(chat_id, user_id, user, logger=None):
    session = _STORE.get(chat_id)
    if not session or session.get("phase") != "joining":
        return "closed", []
    if user_id in session["ids"]:
        return "duplicate", list(session["players"])
    if len(session["players"]) >= MAX_PLAYERS:
        return "full", list(session["players"])
    player = {
        "user_id": user_id,
        "name": display_name(user),
        # مثل خون‌آشام: شیء کامل کاربر برای ارسال پیوی نگه داشته می‌شود.
        "peer": user,
    }
    session["ids"].add(user_id)
    session["players"].append(player)
    log(logger, f"FOX KARAGAH JOIN chat_id={chat_id} user_id={user_id} "
                f"name={player['name']} count={len(session['players'])}")
    return "joined", list(session["players"])


def player_count(chat_id):
    session = _STORE.get(chat_id)
    return len(session["players"]) if session else 0


def is_full(chat_id):
    return player_count(chat_id) >= MAX_PLAYERS


def roster_lines(players):
    """فقط نام نمایشی کاربران؛ بدون یوزرنیم."""
    return "\n".join(
        f"{index}- {player['name']}"
        for index, player in enumerate(players, 1)
    )


def choose_thief(chat_id, exclude_ids=(), logger=None):
    """انتخاب مخفیانهٔ دزد؛ دزدِ دورِ قبل پشت‌سرهم انتخاب نمی‌شود."""
    session = _STORE.get(chat_id)
    if not session or session.get("phase") not in {"joining", "assigning"}:
        return None
    players = session["players"]
    if len(players) < MIN_PLAYERS:
        return None
    last_thief = _LAST_THIEF_BY_CHAT.get(chat_id)
    candidates = [
        i for i, p in enumerate(players)
        if p["user_id"] not in exclude_ids and p["user_id"] != last_thief
    ]
    if not candidates:
        candidates = [
            i for i, p in enumerate(players)
            if p["user_id"] not in exclude_ids
        ]
    if not candidates:
        return None
    index = _RANDOM.choice(candidates)
    session["thief"] = index
    session["phase"] = "assigning"
    if not session.get("object"):
        session["object"] = _RANDOM.choice(OBJECTS)
        decoys = [o for o in OBJECTS if o != session["object"]]
        _RANDOM.shuffle(decoys)
        options = decoys[:OBJECT_OPTIONS - 1] + [session["object"]]
        _RANDOM.shuffle(options)
        session["options"] = options
    thief = players[index]
    log(logger, f"FOX KARAGAH THIEF CHOSEN chat_id={chat_id} "
                f"session_id={session['session_id']} thief_user_id={thief['user_id']} "
                f"object={session['object']!r}")
    return {"number": index + 1, "player": dict(thief),
            "object": session["object"], "players": list(players)}


def open_guessing(chat_id, session_id=None, logger=None):
    session = _STORE.get(chat_id)
    if not session or session.get("phase") != "assigning":
        return False
    if session_id is not None and session["session_id"] != session_id:
        return False
    session["phase"] = "thief_guess"
    log(logger, f"FOX KARAGAH GUESSING OPEN chat_id={chat_id} "
                f"seconds={THIEF_GUESS_SECONDS}")
    return True


def guess_thief(chat_id, user_id, text, logger=None):
    """حدس دزد با شماره. خروجی (state, info)."""
    session = _STORE.get(chat_id)
    if not session or session.get("phase") != "thief_guess":
        return "closed", None
    if user_id not in session["ids"]:
        return "not_player", None
    thief_index = session["thief"]
    if session["players"][thief_index]["user_id"] == user_id:
        return "is_thief", None
    if user_id in session["guessed"]:
        return "already", None
    number = parse_int(text)
    if number is None or not 1 <= number <= len(session["players"]):
        return "bad_number", None
    if session["players"][number - 1]["user_id"] == user_id:
        return "self_guess", None

    session["guessed"].add(user_id)
    guesser = next(p for p in session["players"] if p["user_id"] == user_id)
    if number - 1 == thief_index:
        session["phase"] = "object_guess"
        session["finder"] = guesser["user_id"]
        session["object_deadline"] = time.monotonic() + OBJECT_GUESS_SECONDS
        log(logger, f"FOX KARAGAH THIEF FOUND chat_id={chat_id} "
                    f"finder={user_id} thief={session['players'][thief_index]['user_id']}")
        return "found", {
            "guesser": dict(guesser),
            "thief": dict(session["players"][thief_index]),
            "options": list(session["options"]),
        }
    return "wrong", {"guesser": dict(guesser), "number": number}


def guess_object(chat_id, user_id, text, logger=None):
    """حدس شیء توسط پیداکنندهٔ دزد؛ فقط یک فرصت."""
    session = _STORE.get(chat_id)
    if not session or session.get("phase") != "object_guess":
        return "closed", None
    if user_id != session.get("finder"):
        return "not_finder", None
    options = session.get("options") or []
    number = parse_int(text)
    chosen = None
    if number is not None and 1 <= number <= len(options):
        chosen = options[number - 1]
    else:
        normalized = " ".join(str(text or "").split())
        if normalized in options:
            chosen = normalized
    if chosen is None:
        return "bad_option", None

    thief = dict(session["players"][session["thief"]])
    finder = next(p for p in session["players"] if p["user_id"] == user_id)
    stolen = session["object"]
    _LAST_THIEF_BY_CHAT[chat_id] = thief["user_id"]
    closed = _STORE.close(chat_id, session["session_id"])
    _STORE.cancel_task(chat_id)
    if closed is None:
        return "closed", None
    info = {"finder": dict(finder), "thief": thief, "object": stolen}
    if chosen == stolen:
        log(logger, f"FOX KARAGAH SOLVED chat_id={chat_id} winner={user_id}")
        return "solved", info
    log(logger, f"FOX KARAGAH OBJECT WRONG chat_id={chat_id} chosen={chosen!r}")
    return "object_wrong", info


def thief_win_close(chat_id, session_id=None, logger=None, reason="timeout"):
    """پایان به نفع دزد (پایان زمان هر مرحله)."""
    session = _STORE.get(chat_id)
    if not session:
        return None
    if session_id is not None and session["session_id"] != session_id:
        return None
    thief = None
    if session.get("thief") is not None:
        thief = dict(session["players"][session["thief"]])
        _LAST_THIEF_BY_CHAT[chat_id] = thief["user_id"]
    stolen = session.get("object")
    closed = _STORE.close(chat_id, session["session_id"])
    _STORE.cancel_task(chat_id)
    if closed is None:
        return None
    log(logger, f"FOX KARAGAH THIEF WIN chat_id={chat_id} reason={reason}")
    return {"thief": thief, "object": stolen, "reason": reason}


def abandon(chat_id, session_id=None, logger=None):
    session = _STORE.close(chat_id, session_id)
    _STORE.cancel_task(chat_id)
    return bool(session)


async def deliver_role(client, chat_id, chosen, logger=None):
    """نقش دزد را **فقط** از راه پیام خصوصی می‌رساند.

    کپی مستقل از مکانیزم اثبات‌شدهٔ بازی خون‌آشام (بدون اشتراک کد):
      ۱. ارسال اول با شیء کاربر (peer با access_hash واقعی) و بعد با
         شناسهٔ عددی به‌عنوان آخرین راه.
      ۲. خطای موقت (timeout/شبکه/RPC) با چند تلاشِ timeout دار مهار
         می‌شود؛ خطای دائمی (حریم خصوصی/بلاک) → نقش به بازیکن دیگری
         منتقل می‌شود.
      ۳. هیچ نقشی هرگز داخل گروه اعلام نمی‌شود.

    خروجی ``(chosen, mode)`` با mode برابر ``"dm"`` یا ``"failed"``.
    """
    attempted = 0
    session = _STORE.get(chat_id)
    max_attempts = max(
        len(session["players"]) if session else 1, 1
    ) * DM_RETRIES + 2
    while chosen is not None and attempted < max_attempts:
        attempted += 1
        ok, error, transient = await send_role_dm(
            client, chosen["player"], chosen["object"],
            logger=logger, chat_id=chat_id)
        if ok:
            log(logger, f"FOX KARAGAH ROLE DELIVERED chat_id={chat_id} "
                        f"attempts={attempted}")
            return chosen, "dm"
        log(logger, f"FOX KARAGAH ROLE UNREACHABLE chat_id={chat_id} "
                    f"user_id={chosen['player'].get('user_id')} "
                    f"reason={error!r} transient={transient} "
                    f"-> trying another player")
        chosen = reassign_thief(
            chat_id, chosen["player"].get("user_id"), logger)

    log_error(logger, f"FOX KARAGAH ROLE UNDELIVERABLE chat_id={chat_id} "
                      f"attempts={attempted}")
    return None, "failed"


def _is_transient_dm_error(error):
    """آیا خطای ارسالِ نقش «موقت» است یا «دائمی»؟ (کپی مستقل از خون‌آشام)"""
    if error is None:
        return False
    name = error.__class__.__name__
    text = str(error).lower()
    transient_names = (
        "timeout", "timedouterror", "rpc error", "flood", "servererror",
        "network", "connectionerror", "ioerror", "oserror", "typeerror",
    )
    transient_keywords = (
        "timeout", "timed out", "flood", "network", "connection",
        "cannot connect", "connection reset", "timedout",
    )
    if any(k in name.lower() for k in transient_names):
        return True
    return any(k in text for k in transient_keywords)


async def send_role_dm(client, player, stolen_object, logger=None, chat_id=None):
    """پیام نقش دزد را به پیوی می‌فرستد. ``(ok, error, transient)``.

    ترتیب تلاش (همان روش خون‌آشام):
      ۱. شیء کاربر که هنگام «شرکت» ذخیره شده — دارای access_hash و بدون
         نیاز به کش یا شبکه.
      ۲. شناسهٔ عددی، فقط به عنوان آخرین راه.
    هر تلاش مهلت دارد تا یک RPC گیرکرده، بازی را برای همیشه قفل نکند.
    """
    message = ROLE_MESSAGE.format(obj=stolen_object)
    user_id = player.get("user_id")
    targets = []
    peer = player.get("peer")
    if peer is not None:
        targets.append(("peer_object", peer))
    if user_id is not None:
        targets.append(("user_id", user_id))

    if not targets:
        log_error(logger, f"FOX KARAGAH ROLE DM FAILED chat_id={chat_id} "
                          f"user_id={user_id} reason=no_target")
        return False, "no_target", False

    last_error = None
    for attempt in range(DM_RETRIES):
        last_error = None
        for label, target in targets:
            try:
                log(logger, f"FOX KARAGAH ROLE DM TRY chat_id={chat_id} "
                            f"user_id={user_id} via={label} attempt={attempt + 1}")
                await asyncio.wait_for(
                    client.send_message(target, message),
                    timeout=DM_TIMEOUT,
                )
                log(logger, f"FOX KARAGAH ROLE DM SENT chat_id={chat_id} "
                            f"user_id={user_id} via={label}")
                return True, None, False
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError:
                last_error = TimeoutError(
                    f"send_message timed out after {DM_TIMEOUT}s"
                )
                log_error(logger, f"FOX KARAGAH ROLE DM TIMEOUT "
                                  f"chat_id={chat_id} user_id={user_id} "
                                  f"via={label} attempt={attempt + 1}")
            except Exception as error:
                last_error = error
                log_error(logger, f"FOX KARAGAH ROLE DM ATTEMPT FAILED "
                                  f"chat_id={chat_id} user_id={user_id} "
                                  f"via={label} error={error!r} "
                                  f"attempt={attempt + 1}")

        transient = _is_transient_dm_error(last_error)
        if not transient:
            break
        if attempt < DM_RETRIES - 1:
            await asyncio.sleep(1)

    transient = _is_transient_dm_error(last_error)
    log_error(logger, f"FOX KARAGAH ROLE DM FAILED chat_id={chat_id} "
                      f"user_id={user_id} error={last_error!r} "
                      f"transient={transient}")
    return False, last_error, transient


def reassign_thief(chat_id, user_id, logger=None):
    """دزد را به بازیکن دیگری منتقل می‌کند (پیوی بسته/بلاک).

    همان منطق ``reassign_vampire``: بازیکنِ دردسترس بعدی به‌صورت
    تصادفی انتخاب می‌شود؛ دزدِ دورِ قبل در صورت امکان کنار گذاشته
    می‌شود. شیء دزدیده‌شده همان قبلی می‌ماند. خروجی ساختار
    ``choose_thief`` یا ``None``.
    """
    session = _STORE.get(chat_id)
    if not session or session.get("phase") != "assigning":
        return None

    session.setdefault("unreachable", set()).add(user_id)
    remaining = [
        index
        for index, player in enumerate(session["players"])
        if player["user_id"] not in session["unreachable"]
    ]
    if not remaining:
        log_error(logger, f"FOX KARAGAH NO REACHABLE PLAYER chat_id={chat_id}")
        return None

    last_thief = _LAST_THIEF_BY_CHAT.get(chat_id)
    preferred = [i for i in remaining
                 if session["players"][i]["user_id"] != last_thief]
    pool = preferred or remaining
    index = _RANDOM.choice(pool)
    session["thief"] = index
    thief = session["players"][index]
    log(logger, f"FOX KARAGAH REASSIGNED chat_id={chat_id} "
                f"from_user_id={user_id} to_user_id={thief['user_id']} "
                f"number={index + 1}")
    return {
        "number": index + 1,
        "player": dict(thief),
        "object": session["object"],
        "players": list(session["players"]),
    }


async def run_game(chat_id, session_id, callbacks, logger=None,
                   join_seconds=None, thief_seconds=None):
    """چرخهٔ کامل پرونده؛ پایان بازی همیشه تضمین شده است."""
    join_wait = JOIN_SECONDS if join_seconds is None else join_seconds
    thief_wait = THIEF_GUESS_SECONDS if thief_seconds is None else thief_seconds
    finished = False
    try:
        waited = 0.0
        step = 0.05 if join_wait <= 2 else 0.5
        while waited < join_wait and not is_full(chat_id):
            await asyncio.sleep(step)
            waited += step

        session = _STORE.get(chat_id)
        if not session or session["session_id"] != session_id:
            return
        if len(session["players"]) < MIN_PLAYERS:
            abandon(chat_id, session_id, logger)
            await callbacks["on_abort"]()
            finished = True
            return

        chosen = choose_thief(chat_id, logger=logger)
        if chosen is None:
            abandon(chat_id, session_id, logger)
            await callbacks["on_abort"]()
            finished = True
            return

        chosen, delivery = await callbacks["on_roles"](chosen)
        if chosen is None:
            log_error(logger, f"FOX KARAGAH ABORT chat_id={chat_id} reason=dm_failed_all")
            abandon(chat_id, session_id, logger)
            dm_failed = callbacks.get("on_dm_failed")
            if dm_failed is not None:
                await dm_failed()
            else:
                await callbacks["on_abort"]()
            finished = True
            return

        if not open_guessing(chat_id, session_id, logger):
            return
        await callbacks["on_roster"](chosen)

        # مرحلهٔ حدس دزد: ۴۰ ثانیه.
        await asyncio.sleep(thief_wait)
        session = _STORE.get(chat_id)
        if not session or session["session_id"] != session_id:
            return
        if session.get("phase") == "thief_guess":
            result = thief_win_close(chat_id, session_id, logger, reason="thief_timeout")
            if result:
                await callbacks["on_thief_win"](result)
            finished = True
            return

        # مرحلهٔ حدس شیء: تا سررسید صبر می‌کنیم (پاسخ درست session را می‌بندد).
        while True:
            session = _STORE.get(chat_id)
            if not session or session["session_id"] != session_id:
                return
            deadline = session.get("object_deadline")
            if deadline is None or time.monotonic() >= deadline:
                break
            await asyncio.sleep(0.5)
        session = _STORE.get(chat_id)
        if session and session["session_id"] == session_id \
                and session.get("phase") == "object_guess":
            result = thief_win_close(chat_id, session_id, logger, reason="object_timeout")
            if result:
                await callbacks["on_thief_win"](result)
        finished = True
    except asyncio.CancelledError:
        raise
    except Exception as error:
        log_error(logger, f"FOX KARAGAH LOOP FAILED chat_id={chat_id} error={error!r}")
        if not finished:
            abandon(chat_id, session_id, logger)


def schedule(chat_id, session_id, callbacks, logger=None,
             join_seconds=None, thief_seconds=None):
    return _STORE.schedule(chat_id, lambda: run_game(
        chat_id, session_id, callbacks, logger, join_seconds, thief_seconds))


def reset_all(chat_id=None):
    _STORE.reset(chat_id)
    if chat_id is None:
        _LAST_THIEF_BY_CHAT.clear()
    else:
        _LAST_THIEF_BY_CHAT.pop(chat_id, None)
