"""🔁 گردش معماهای حدس ایموجی: بدون لو رفتن جواب، بدون بن‌بست.

دو مشکل واقعی که این تست‌ها می‌گیرند:

  ۱) کاربر تازه‌وارد می‌توانست همان معمایی را بگیرد که چند لحظه پیش در
     همان گروه جواب داده شده بود، پس بقیه جواب را می‌دانستند.
  ۲) وقتی کاربری همهٔ ۲۰۰ مرحله را تمام می‌کرد، ``start`` برای همیشه
     ``None`` می‌داد و بازی برای او بسته می‌ماند.

    python tests/test_emoji_rotation.py
"""
import asyncio
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

import economy.shop.store as store
import economy.storage as storage
import modules.emoji_guess as eg
import modules.group_storage as group_storage
from economy import game_progress
from test_economy_routing import build_handler, Event

PASSED = FAILED = 0
CHAT = -1009999888877
CHAT_B = -100515151515


def check(label, cond, detail=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  PASS  {label}")
    else:
        FAILED += 1
        print(f"  FAIL  {label} {detail}")


def fresh():
    temp = Path(tempfile.mkdtemp())
    storage.use_file(temp / "economy.json")
    store.ITEMS_FILE = temp / "shop.json"
    store._cache = None
    store._cache_mtime = None
    eg._ACTIVE.clear()
    group_storage.activate_group(CHAT, "گروه تست")
    return temp


def play(chat_id, user_id, rounds):
    answers = []
    for _ in range(rounds):
        puzzle = eg.start(chat_id, user_id)
        if puzzle is None:
            break
        eg.answer(chat_id, user_id, "کاربر", puzzle["answer"])
        answers.append(puzzle["answer"])
    return answers


# ===========================================================================
# ۵) معمای تازه‌مصرف‌شده به کاربر بعدی داده نمی‌شود
# ===========================================================================
def test_new_user_avoids_recent_puzzles():
    print("\n### 🔒 کاربر جدید معمای تازه را نمی‌گیرد")
    collisions = 0
    for trial in range(30):
        fresh()
        used = play(CHAT, 1000 + trial, 15)
        puzzle = eg.start(CHAT, 2000 + trial)
        if puzzle and puzzle["answer"] in used:
            collisions += 1
    check("در هیچ‌کدام از ۳۰ آزمون تکرار رخ نداد", collisions == 0,
          f"-> {collisions}/30")


def test_recent_list_is_recorded():
    print("\n### 🔒 فهرست «تازه‌مصرف‌شده» گروه ثبت می‌شود")
    fresh()
    used = play(CHAT, 30, 6)
    recent = game_progress.recent(CHAT, eg.GAME)
    check("همهٔ معماهای استفاده‌شده ثبت شدند",
          set(used) <= set(recent), f"-> {set(used) - set(recent)}")
    check("ترتیب حفظ شده (آخری در انتهاست)",
          recent[-1] == used[-1], f"-> {recent[-1]} != {used[-1]}")


def test_recent_window_is_bounded():
    """فهرست نباید بی‌نهایت رشد کند وگرنه انتخاب ممکن تمام می‌شود."""
    print("\n### 🔒 پنجرهٔ «تازه» محدود است")
    fresh()
    play(CHAT, 31, 60)
    recent = game_progress.recent(CHAT, eg.GAME)
    check("طول فهرست از پنجره بیشتر نمی‌شود",
          len(recent) <= eg.RECENT_WINDOW,
          f"-> {len(recent)} > {eg.RECENT_WINDOW}")
    check("پنجره کوچک‌تر از کل بانک است",
          eg.RECENT_WINDOW < len(eg.PUZZLES))


def test_recent_is_per_group():
    print("\n### 🔒 فهرست «تازه» هر گروه جداست")
    fresh()
    used = play(CHAT, 32, 8)
    check("گروه دیگر فهرست خالی دارد",
          game_progress.recent(CHAT_B, eg.GAME) == [],
          f"-> {game_progress.recent(CHAT_B, eg.GAME)}")
    other = eg.start(CHAT_B, 33)
    check("گروه دیگر آزادانه انتخاب می‌کند", other is not None)
    check("گروه اول دست‌نخورده ماند",
          set(used) <= set(game_progress.recent(CHAT, eg.GAME)))


def test_recent_survives_restart():
    print("\n### 🔒 فهرست «تازه» پس از ری‌استارت می‌ماند")
    fresh()
    used = play(CHAT, 34, 10)
    eg._ACTIVE.clear()
    storage._cache = None
    storage._cache_mtime = None
    recent = game_progress.recent(CHAT, eg.GAME)
    check("پس از ری‌استارت هنوز ثبت است", set(used) <= set(recent))
    puzzle = eg.start(CHAT, 35)
    check("کاربر جدید پس از ری‌استارت هم معمای تازه نمی‌گیرد",
          puzzle["answer"] not in used, f"-> {puzzle['answer']}")


def test_game_never_deadlocks():
    """کنار گذاشتن «تازه‌ها» هرگز نباید بازی را قفل کند."""
    print("\n### 🔒 بازی هرگز قفل نمی‌شود")
    fresh()
    blocked = 0
    for index in range(120):
        puzzle = eg.start(CHAT, 40 + index)
        if puzzle is None:
            blocked += 1
        else:
            eg.finish(CHAT, puzzle["token"])
    check("هیچ کاربری بدون معما نماند", blocked == 0, f"-> {blocked}")


# ===========================================================================
# ۷) چرخهٔ جدید پس از مصرف همهٔ معماها
# ===========================================================================
def test_new_cycle_after_exhaustion():
    print("\n### 🔁 پس از مصرف همه، دور تازه ساخته می‌شود")
    fresh()
    first = play(CHAT, 50, len(eg.PUZZLES))
    check("دور اول کامل شد", len(first) == len(eg.PUZZLES),
          f"-> {len(first)}")
    check("شمارندهٔ دور هنوز صفر است",
          game_progress.cycle(CHAT, 50, eg.GAME) == 0)

    puzzle = eg.start(CHAT, 50)
    check("start دیگر None نمی‌دهد", puzzle is not None)
    check("دور تازه از مرحلهٔ ۱ شروع می‌شود",
          puzzle and puzzle["stage"] == 1, f"-> {puzzle}")
    check("شمارندهٔ دور یکی زیاد شد",
          game_progress.cycle(CHAT, 50, eg.GAME) == 1)


def test_second_cycle_covers_everything_again():
    print("\n### 🔁 دور دوم هم کل بانک را می‌دهد")
    fresh()
    play(CHAT, 51, len(eg.PUZZLES))
    second = play(CHAT, 51, len(eg.PUZZLES))
    check("دور دوم هم کامل شد", len(second) == len(eg.PUZZLES),
          f"-> {len(second)}")
    check("دور دوم بدون تکرار است",
          len(set(second)) == len(eg.PUZZLES),
          f"-> {len(set(second))}")
    check("شمارندهٔ دور روی ۱ است",
          game_progress.cycle(CHAT, 51, eg.GAME) == 1)


def test_cycle_is_per_user_and_group():
    print("\n### 🔁 دور هر کاربر و گروه جداست")
    fresh()
    play(CHAT, 52, len(eg.PUZZLES))
    eg.start(CHAT, 52)
    check("کاربر اول وارد دور ۲ شد",
          game_progress.cycle(CHAT, 52, eg.GAME) == 1)
    check("کاربر دیگر هنوز در دور ۱ است",
          game_progress.cycle(CHAT, 53, eg.GAME) == 0)
    check("همان کاربر در گروه دیگر در دور ۱ است",
          game_progress.cycle(CHAT_B, 52, eg.GAME) == 0)


def test_cycle_survives_restart():
    print("\n### 🔁 دور پس از ری‌استارت می‌ماند")
    fresh()
    play(CHAT, 54, len(eg.PUZZLES))
    eg.start(CHAT, 54)
    eg._ACTIVE.clear()
    storage._cache = None
    storage._cache_mtime = None
    check("شمارندهٔ دور باقی ماند",
          game_progress.cycle(CHAT, 54, eg.GAME) == 1)
    check("پیشرفت دور تازه هم ذخیره شده",
          eg.seen_count(CHAT, 54) == 1,
          f"-> {eg.seen_count(CHAT, 54)}")


# ===========================================================================
# محافظ ضدسوءاستفاده باید دست‌نخورده بماند
# ===========================================================================
def test_owner_can_finish_own_last_round():
    print("\n### 🛡 صاحب دور، مرحلهٔ آخر خودش را می‌بندد")
    fresh()
    played = play(CHAT, 60, len(eg.PUZZLES))
    check("همهٔ مرحله‌ها واقعاً پاسخ داده شدند",
          len(played) == len(eg.PUZZLES), f"-> {len(played)}")
    check("بازی روی مرحلهٔ آخر قفل نماند", not eg.is_active(CHAT))


def test_exhausted_user_cannot_steal_others_round():
    print("\n### 🛡 کاربر تمام‌شده از دور دیگران امتیاز نمی‌گیرد")
    fresh()
    play(CHAT, 61, len(eg.PUZZLES))
    check("کاربر ۶۱ تمام‌شده است", eg.is_exhausted(CHAT, 61))

    owner = eg.start(CHAT, 62)
    check("دور متعلق به کاربر ۶۲ است", owner["user_id"] == 62)
    check("کاربر تمام‌شده پاسخ نمی‌گیرد",
          eg.answer(CHAT, 61, "U", owner["answer"]) is None)
    check("بازی برای صاحبش فعال می‌ماند", eg.is_active(CHAT))
    check("صاحب دور پاسخ خودش را می‌گیرد",
          eg.answer(CHAT, 62, "U", owner["answer"]) == owner["answer"])


def test_one_puzzle_per_user_at_a_time():
    """سشن‌ها per-user هستند: هر کاربر یک معمای باز، نه هر گروه."""
    print("\n### 🛡 هر کاربر فقط یک معمای باز دارد")
    fresh()
    first = eg.start(CHAT, 70)
    again = eg.start(CHAT, 70)
    other = eg.start(CHAT, 71)
    check("معمای اول ساخته شد", first is not None)
    check("همان کاربر معمای دوم نمی‌گیرد", again is None)
    check("کاربر دیگر آزادانه شروع می‌کند", other is not None)
    check("دو معما متفاوت‌اند",
          other["answer"] != first["answer"], f"-> {other['answer']}")
    check("هر دو سشن هم‌زمان بازند",
          eg.is_active(CHAT, 70) and eg.is_active(CHAT, 71))

    eg.finish(CHAT, first["token"], 70)
    check("بستن سشن اول روی دومی اثر ندارد", eg.is_active(CHAT, 71))
    third = eg.start(CHAT, 70)
    check("پس از پایان، همان کاربر دوباره شروع می‌کند",
          third is not None)


# ===========================================================================
# مسیر واقعی هندلر
# ===========================================================================
def test_second_user_does_not_see_known_answer():
    print("\n### 🔌 کاربر دوم جواب لو رفته را نمی‌گیرد")
    fresh()

    async def scenario():
        bot, handler = await build_handler()
        used = []
        for _ in range(12):
            await handler(Event("حدس ایموجی", 80))
            state = eg.active_state(CHAT, 80)
            if not state:
                break
            used.append(state["answer"])
            await handler(Event(state["answer"], 80))

        event = Event("حدس ایموجی", 81)
        await handler(event)
        return bot, used, eg.active_state(CHAT, 81), event

    bot, used, state, event = asyncio.run(scenario())
    check("کاربر اول ۱۲ مرحله بازی کرد", len(used) == 12, f"-> {len(used)}")
    check("کاربر دوم معما گرفت", state is not None)
    check("معمای کاربر دوم قبلاً جواب داده نشده",
          state and state["answer"] not in used,
          f"-> {state['answer'] if state else None}")
    check("هیچ خطایی نیست", not bot.logger.errors,
          f"-> {[e[:100] for e in bot.logger.errors][:1]}")


def test_exhausted_user_gets_new_cycle_through_handler():
    print("\n### 🔌 دور تازه از مسیر واقعی ربات")
    fresh()
    play(CHAT, 82, len(eg.PUZZLES))

    async def scenario():
        bot, handler = await build_handler()
        event = Event("حدس ایموجی", 82)
        await handler(event)
        return bot, event

    bot, event = asyncio.run(scenario())
    check("پیام «تمام شد» داده نمی‌شود",
          not event.said("تمام مراحل"), f"-> {event.replies}")
    check("معمای تازه ارسال شد", event.said("حدس ایموجی"),
          f"-> {event.replies}")
    check("از مرحلهٔ ۱ دور تازه شروع می‌شود",
          event.said("مرحله 𝟭"), f"-> {event.replies}")
    check("هیچ خطایی نیست", not bot.logger.errors)


def test_progress_still_persists():
    """اطمینان از اینکه تغییرات این تست‌ها ماندگاری را نشکسته‌اند."""
    print("\n### 🔌 ماندگاری پیشرفت دست‌نخورده است")
    fresh()
    play(CHAT, 83, 7)
    eg._ACTIVE.clear()
    storage._cache = None
    storage._cache_mtime = None
    check("پیشرفت پس از ری‌استارت", eg.seen_count(CHAT, 83) == 7,
          f"-> {eg.seen_count(CHAT, 83)}")
    puzzle = eg.start(CHAT, 83)
    check("از مرحلهٔ ۸ ادامه می‌دهد", puzzle["stage"] == 8,
          f"-> {puzzle['stage']}")


def test_bank_size_unchanged():
    print("\n### 📦 بانک دست‌نخورده و بدون تکرار است")
    check("بانک دست‌کم ۴۰۰ مرحله دارد", len(eg.PUZZLES) >= 400,
          f"-> {len(eg.PUZZLES)}")
    answers = [answer for _, answer, _ in eg.PUZZLES]
    check("هیچ پاسخ تکراری نیست", len(set(answers)) == len(answers))


# ===========================================================================
def main():
    test_new_user_avoids_recent_puzzles()
    test_recent_list_is_recorded()
    test_recent_window_is_bounded()
    test_recent_is_per_group()
    test_recent_survives_restart()
    test_game_never_deadlocks()
    test_new_cycle_after_exhaustion()
    test_second_cycle_covers_everything_again()
    test_cycle_is_per_user_and_group()
    test_cycle_survives_restart()
    test_owner_can_finish_own_last_round()
    test_exhausted_user_cannot_steal_others_round()
    test_one_puzzle_per_user_at_a_time()
    test_second_user_does_not_see_known_answer()
    test_exhausted_user_gets_new_cycle_through_handler()
    test_progress_still_persists()
    test_bank_size_unchanged()
    print(f"\npassed={PASSED} failed={FAILED}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
