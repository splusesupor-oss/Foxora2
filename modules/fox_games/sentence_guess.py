"""بازی مستقل «حدس جمله / ساخت جمله» با بانک و state جداگانه.

این همان بازیِ قبلی است — بازیِ دومی ساخته نشده. فقط سه چیز اضافه شده:

1. دستورِ دومِ «ساخت جمله» کنارِ «حدس جمله» (هر دو همین بازی).
2. نشستِ **به‌تفکیکِ کاربر**: کلیدِ state از ``chat_id`` به
   ``(chat_id, user_id)`` تغییر کرده، پس هر کاربر سوالِ خودش را دارد و
   سوالِ یک نفر به دستِ دیگری نمی‌افتد.
3. **عدمِ تکرار برای هر کاربر** با ``economy.game_progress`` (همان
   سازوکاری که «معما» استفاده می‌کند): جمله‌ای که کاربر قبلاً درست جواب
   داده دوباره به او داده نمی‌شود و وقتی بانک تمام شد، دورِ تازه شروع
   می‌شود.

امضایِ توابع سازگار با قبل مانده است (``user_id`` اختیاری) تا هیچ
فراخوانیِ قدیمی نشکند.
"""
import json
import random
import time
from pathlib import Path

from modules.runtime_paths import runtime_config_file
from modules.atomic_write import write_json

from .sentence_guess_puzzles import PUZZLES

GAME = "sentence_guess"
COMMAND = "حدس جمله"
# دستورِ خواسته‌شده در کنارِ دستورِ قدیمی؛ هر دو همین بازی را اجرا می‌کنند.
ALT_COMMAND = "ساخت جمله"
COMMANDS = (COMMAND, ALT_COMMAND)

TIMEOUT_SECONDS = 30
# جایزه: ۳ سکهٔ برنز (مطابقِ «sentence_guess» در economy/rewards.py).
REWARD = 3

# چند جملهٔ اخیرِ گروه کنار گذاشته می‌شود تا کاربرِ بعدی جمله‌ای را نگیرد
# که همین حالا جلوی چشمِ همه جواب داده شد.
RECENT_WINDOW = 15

FILE = runtime_config_file("sentence_guess_state.json")
_ACTIVE = {}
_RANDOM = random.SystemRandom()


def _load():
    try:
        data = json.loads(FILE.read_text(encoding="utf-8")) if FILE.exists() else {}
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _save(data):
    FILE.parent.mkdir(parents=True, exist_ok=True)
    write_json(FILE, data, indent=2)


def _key(chat_id, user_id=None):
    """کلیدِ نشست. بدونِ ``user_id`` کلیدِ قدیمیِ چت‌محور ساخته می‌شود."""
    if user_id is None:
        return str(chat_id)
    return f"{chat_id}:{user_id}"


def _chat_keys(chat_id, data=None):
    """همهٔ کلیدهایِ متعلق به این چت (چه چت‌محورِ قدیمی، چه کاربرمحور)."""
    prefix = f"{chat_id}:"
    plain = str(chat_id)
    source = data if data is not None else _ACTIVE
    return [k for k in source if k == plain or k.startswith(prefix)]


def _expired(state):
    return time.time() - float(state.get("started_at", 0)) >= TIMEOUT_SECONDS


def _recover(chat_id, user_id=None):
    key = _key(chat_id, user_id)
    state = _ACTIVE.get(key)
    if state is None and user_id is not None:
        # سازگاری با نشست‌هایی که پیش از این تغییر، چت‌محور ذخیره شده‌اند.
        legacy = _ACTIVE.get(str(chat_id))
        if legacy is not None:
            state = legacy
            key = str(chat_id)
    if state is not None:
        if _expired(state):
            _clear_key(key)
            return None
        return state

    stored = _load()
    state = stored.get(key)
    if not isinstance(state, dict) and user_id is not None:
        state = stored.get(str(chat_id))
        if isinstance(state, dict):
            key = str(chat_id)
    if not isinstance(state, dict):
        return None
    if _expired(state):
        _clear_key(key)
        return None
    _ACTIVE[key] = state
    return state


def _clear_key(key):
    _ACTIVE.pop(key, None)
    data = _load()
    if key in data:
        data.pop(key, None)
        _save(data)


def _clear(chat_id, user_id=None):
    if user_id is None:
        # پاک‌کردنِ کلِ چت (رفتارِ قبلی).
        data = _load()
        changed = False
        for key in set(_chat_keys(chat_id)) | set(_chat_keys(chat_id, data)):
            _ACTIVE.pop(key, None)
            if key in data:
                data.pop(key, None)
                changed = True
        if changed:
            _save(data)
        return
    _clear_key(_key(chat_id, user_id))
    # نشستِ قدیمیِ چت‌محور هم اگر مانده بود پاک شود.
    if str(chat_id) in _ACTIVE or str(chat_id) in _load():
        _clear_key(str(chat_id))


def is_active(chat_id, user_id=None):
    """با ``user_id`` یعنی «آیا همین کاربر جمله دارد»؛ بدون آن یعنی گروه."""
    if user_id is not None:
        return _recover(chat_id, user_id) is not None
    for key in set(_chat_keys(chat_id)) | set(_chat_keys(chat_id, _load())):
        state = _ACTIVE.get(key) or _load().get(key)
        if isinstance(state, dict) and not _expired(state):
            return True
        _clear_key(key)
    return False


def _pick(chat_id, user_id, used):
    """جمله‌ای که این کاربر ندیده و به‌تازگی در گروه استفاده نشده."""
    remaining = [p for p in PUZZLES if p[1] not in used]
    if not remaining:
        remaining = list(PUZZLES)
    recent = set()
    if user_id is not None:
        try:
            from economy import game_progress as _gp
            recent = set(_gp.recent(chat_id, GAME))
        except Exception:
            recent = set()
    preferred = [p for p in remaining if p[1] not in recent]
    pool = preferred or remaining
    return _RANDOM.choice(pool)


def start(chat_id, user_id=None, mode="guess"):
    """یک جملهٔ تازه برای این کاربر شروع می‌کند.

    اگر همین کاربر نشستِ باز داشته باشد ``None`` برمی‌گردد.
    ``mode`` یکی از ``"guess"`` (حدس جمله) یا ``"build"`` (ساخت جمله) است.
    در حالت ``"build"`` کلماتِ پاسخ به‌هم‌ریخته نمایش داده می‌شوند.
    """
    if _recover(chat_id, user_id) is not None:
        return None

    used = set()
    number = 1
    if user_id is not None:
        try:
            from economy import game_progress as _gp
            seen = _gp.seen(chat_id, user_id, GAME)
            if len(seen) >= len(PUZZLES):
                _gp.start_new_cycle(chat_id, user_id, GAME)
                seen = _gp.seen(chat_id, user_id, GAME)
            used = set(seen)
            number = len(seen) + 1
        except Exception:
            used = set()
            number = 1

    # در حالت ساخت جمله از بانک جداگانه (جمله‌های واقعی حداقل ۴ کلمه) استفاده می‌شود
    if mode == "build":
        try:
            from .sentence_build_puzzles import PUZZLES as BUILD_PUZZLES
        except Exception:
            BUILD_PUZZLES = []
        build_pool = BUILD_PUZZLES if BUILD_PUZZLES else []
        if build_pool:
            remaining = [p for p in build_pool if p[1] not in used]
            if not remaining:
                remaining = list(build_pool)
            preferred = remaining
            pool = preferred if preferred else remaining
            question, answer_value = _RANDOM.choice(pool)
            # در ساخت جمله، پیشرفت کاربر بر اساس بانک ساخت جمله سنجیده می‌شود
            if user_id is not None:
                try:
                    from economy import game_progress as _gp
                    # اگر تعداد دیده‌شده‌ها از بانک ساخت جمله بیشتر یا مساوی بود، دور تازه
                    seen_for_build = set()
                    if len(used) >= len(build_pool):
                        _gp.start_new_cycle(chat_id, user_id, GAME)
                        seen_for_build = set()
                    else:
                        seen_for_build = set(used)
                    used = seen_for_build
                    number = len(seen_for_build) + 1
                except Exception:
                    pass
        else:
            build_pool = []
            # اگر بانک جداگانه نبود، از بانک اصلی فقط چندکلمه‌ای‌ها انتخاب شود
            multi_word_pool = [p for p in PUZZLES if len(str(p[1]).split()) >= 2]
            effective_pool = multi_word_pool if multi_word_pool else PUZZLES
            remaining = [p for p in effective_pool if p[1] not in used]
            if not remaining:
                remaining = list(effective_pool)
            preferred = remaining
            pool = preferred if preferred else remaining
            question, answer_value = _RANDOM.choice(pool)
    else:
        question, answer_value = _pick(chat_id, user_id, used)
    display_question = question
    if mode == "build":
        words = str(answer_value).split()
        _RANDOM.shuffle(words)
        display_question = " / ".join(words)
    question = display_question
    if user_id is not None:
        try:
            from economy import game_progress as _gp
            _gp.mark_recent(chat_id, GAME, answer_value, RECENT_WINDOW)
        except Exception:
            pass

    # تعیین طول بانک بر اساس حالت
    total_puzzles = len(build_pool) if mode == "build" and build_pool else len(PUZZLES)
    try:
        index = PUZZLES.index((question, answer_value))
    except ValueError:
        index = -1
    state = {"index": index, "question": question, "answer": answer_value,
             "number": number, "total": total_puzzles,
             "user_id": user_id, "chat_id": chat_id,
             # A timer must only finish the exact round that created it.
             # ``time_ns`` plus random bits stays unique even for rapid starts.
             "token": f"{time.time_ns()}:{_RANDOM.getrandbits(32)}",
             "started_at": time.time()}
    key = _key(chat_id, user_id)
    _ACTIVE[key] = state
    data = _load()
    data[key] = state
    _save(data)
    return dict(state)


def current(chat_id, user_id=None):
    state = _recover(chat_id, user_id)
    return dict(state) if state else None


def has_active(chat_id, user_id=None):
    """Check a live session without letting wall-clock expiry erase its answer.

    The router calls this immediately before ``answer``. Timeout ownership is
    deliberately left to the token-bound timer, preventing a correct answer
    queued at the deadline from clearing state before either outcome is sent.
    """
    key = _key(chat_id, user_id)
    state = _ACTIVE.get(key)
    if state is None:
        state = _load().get(key)
        if isinstance(state, dict):
            _ACTIVE[key] = state
    if state is None and user_id is not None:
        # One release may still contain a legacy chat-wide round.
        key = str(chat_id)
        state = _ACTIVE.get(key) or _load().get(key)
        if isinstance(state, dict):
            _ACTIVE[key] = state
    return isinstance(state, dict)


def _norm(value):
    return " ".join(str(value or "").lower().replace("‌", " ").split())


def answer(chat_id, text, user_id=None):
    """پاسخِ کاربر؛ فقط روی نشستِ خودِ او اثر می‌گذارد.

    در صورتِ درستی، پاسخ برایِ همان کاربر «دیده‌شده» ثبت می‌شود تا دوباره
    به او داده نشود.
    """
    key = _key(chat_id, user_id)
    state = _ACTIVE.get(key)
    if state is None:
        state = _load().get(key)
        if isinstance(state, dict):
            _ACTIVE[key] = state
    if state is None and user_id is not None:
        key = str(chat_id)
        state = _ACTIVE.get(key) or _load().get(key)
        if isinstance(state, dict):
            _ACTIVE[key] = state
    if not isinstance(state, dict):
        return None
    if _norm(text) != _norm(state["answer"]):
        return None
    result = dict(state)
    if user_id is not None:
        try:
            from economy import game_progress as _gp
            _gp.mark_seen(chat_id, user_id, GAME, state["answer"])
        except Exception:
            pass
    _clear(chat_id, user_id)
    return result


def timeout(chat_id, user_id=None, token=None):
    """Finish only the round identified by ``token`` when one is supplied."""
    key = _key(chat_id, user_id)
    # مستقیماً از حافظه یا فایل بدون بررسی انقضا می‌خوانیم تا پاسخ حفظ شود
    state = _ACTIVE.get(key)
    if state is None:
        data = _load()
        state = data.get(key)
    if not isinstance(state, dict):
        # اگر کلید قدیمی چت‌محور باشد
        if user_id is not None:
            data = _load()
            state = data.get(str(chat_id))
        else:
            state = _ACTIVE.get(str(chat_id)) or _load().get(str(chat_id))
    if not isinstance(state, dict):
        return None
    if token is not None and state.get("token") != token:
        return None
    result = dict(state)
    _clear(chat_id, user_id)
    return result


def reset_all(chat_id=None, user_id=None):
    if chat_id is None:
        _ACTIVE.clear()
        if FILE.exists():
            FILE.unlink()
    else:
        _clear(chat_id, user_id)
