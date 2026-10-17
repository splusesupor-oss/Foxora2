"""💾 پیشرفت حدس ایموجی دائمی است و بازی ۲۰۰ مرحله دارد.

باگ اصلی: پیشرفت در یک dict داخل حافظه بود، پس با هر ری‌استارت پاک
می‌شد و کاربر دوباره از مرحلهٔ ۱ شروع می‌کرد.

سناریوی خواسته‌شده:
    ۱) چند مرحله رد شود
    ۲) ربات خاموش شود
    ۳) ربات روشن شود
    ۴) «حدس ایموجی» دوباره اجرا شود
    ۵) باید از مرحلهٔ بعدی ادامه دهد، نه ۱

    python tests/test_emoji_progress.py
"""
import asyncio
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

import economy
import economy.shop.store as store
import economy.storage as storage
import modules.emoji_guess as eg
import modules.group_storage as group_storage
from economy import game_progress
from test_economy_routing import build_handler, Event

PASSED = FAILED = 0
CHAT = -1009999888877
CHAT_B = -100424242424


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


def restart():
    """ری‌استارت واقعی: حافظه پاک، فایل دیتابیس دست‌نخورده."""
    eg._ACTIVE.clear()
    storage._cache = None
    storage._cache_mtime = None


def play(chat_id, user_id, rounds):
    """چند مرحله را کامل بازی می‌کند."""
    answers = []
    for _ in range(rounds):
        puzzle = eg.start(chat_id, user_id)
        if puzzle is None:
            break
        eg.answer(chat_id, user_id, "کاربر", puzzle["answer"])
        answers.append(puzzle["answer"])
    return answers


# ===========================================================================
# محتوا: ۲۰۰ مرحله
# ===========================================================================
def test_total_stages():
    print("\n### 🎮 تعداد مراحل")
    check("حداقل ۲۰۰ مرحله دارد", len(eg.PUZZLES) >= 200,
          f"-> {len(eg.PUZZLES)}")
    check("بانک دست‌کم ۴۰۰ مرحله دارد", len(eg.PUZZLES) >= 400,
          f"-> {len(eg.PUZZLES)}")
    check("total_stages هم‌خوان است", eg.total_stages() == len(eg.PUZZLES))


def test_no_duplicates():
    print("\n### 🎮 هیچ مرحلهٔ تکراری نیست")
    answers = [answer for _, answer, _ in eg.PUZZLES]
    emojis = [emoji for emoji, _, _ in eg.PUZZLES]
    duplicate_answers = [a for a, n in Counter(answers).items() if n > 1]
    duplicate_emojis = [e for e, n in Counter(emojis).items() if n > 1]
    check("پاسخ تکراری نیست", not duplicate_answers,
          f"-> {duplicate_answers}")
    check("ترکیب ایموجی تکراری نیست", not duplicate_emojis,
          f"-> {duplicate_emojis}")


def test_new_stages_come_after_120():
    """ترتیب حفظ شود: مراحل جدید بعد از ۱۲۰ بیایند."""
    print("\n### 🎮 مراحل جدید بعد از مرحلهٔ ۱۲۰")
    original = [answer for _, answer, _ in eg.PUZZLES[:120]]
    check("۱۲۰ مرحلهٔ اول دست‌نخورده‌اند",
          original[0] == "پیتزا" and original[-1] == "تفکر",
          f"-> {original[0]} … {original[-1]}")
    added = [answer for _, answer, _ in eg.PUZZLES[120:]]
    check("مرحله‌های جدید بعد از آن‌ها هستند", len(added) >= 80,
          f"-> {len(added)}")
    check("مرحلهٔ ۱۲۱ از دستهٔ جدید است", added[0] == "شیر گاو",
          f"-> {added[0]}")
    check("هیچ مرحلهٔ جدیدی در ۱۲۰ تای اول نیست",
          not (set(added) & set(original)))


def test_stage_content_is_valid():
    print("\n### 🎮 ساختار مراحل سالم است")
    for index, item in enumerate(eg.PUZZLES, 1):
        check_ok = (isinstance(item, tuple) and len(item) == 3
                    and item[0].strip() and item[1].strip()
                    and isinstance(item[2], tuple))
        if not check_ok:
            check(f"مرحلهٔ {index} ساختار درستی دارد", False, f"-> {item}")
            return
    check("همهٔ ۲۰۰ مرحله ساختار درستی دارند", True)
    check("همهٔ پاسخ‌ها فارسی و بدون فاصلهٔ اضافه‌اند",
          all(answer == answer.strip() for _, answer, _ in eg.PUZZLES))
    check("هر مرحله حداقل یک ایموجی دارد",
          all(len(emoji) >= 1 for emoji, _, _ in eg.PUZZLES))


def test_tiers_progress_in_difficulty():
    print("\n### 🎮 سطح‌بندی سختی")
    check("دست‌کم شش دسته وجود دارد", len(eg.TIERS) >= 6,
          f"-> {len(eg.TIERS)}")
    check("نام هر دسته تعریف شده",
          len(eg.TIER_NAMES) == len(eg.TIERS))
    check("مجموع دسته‌ها = کل مراحل",
          sum(len(tier) for tier in eg.TIERS) == len(eg.PUZZLES))
    check("دستهٔ آسان اول است", eg.TIER_NAMES[0] == "آسان")
    check("آخرین دسته از سطح سخت است",
          eg.TIER_NAMES[-1].startswith("سخت"), f"-> {eg.TIER_NAMES[-1]}")


# ===========================================================================
# ماندگاری پیشرفت
# ===========================================================================
def test_progress_survives_restart():
    print("\n### 💾 پیشرفت پس از ری‌استارت می‌ماند")
    fresh()
    play(CHAT, 100, 5)
    check("۵ مرحله بازی شد", eg.seen_count(CHAT, 100) == 5)

    restart()
    check("پس از ری‌استارت پیشرفت باقی است",
          eg.seen_count(CHAT, 100) == 5, f"-> {eg.seen_count(CHAT, 100)}")
    puzzle = eg.start(CHAT, 100)
    check("از مرحلهٔ ۶ ادامه می‌دهد", puzzle["stage"] == 6,
          f"-> {puzzle['stage']}")


def test_stage_57_continues_at_58():
    """سناریوی دقیق خواسته‌شده."""
    print("\n### 💾 مرحلهٔ ۵۷ ➜ پس از ری‌استارت ۵۸")
    fresh()
    play(CHAT, 57, 57)
    check("۵۷ مرحله رد شد", eg.seen_count(CHAT, 57) == 57,
          f"-> {eg.seen_count(CHAT, 57)}")

    restart()
    puzzle = eg.start(CHAT, 57)
    check("از مرحلهٔ ۵۸ ادامه می‌دهد", puzzle["stage"] == 58,
          f"-> {puzzle['stage']}")
    check("از مرحلهٔ ۱ شروع نمی‌کند", puzzle["stage"] != 1)


def test_progress_survives_many_restarts():
    print("\n### 💾 چند ری‌استارت پیاپی")
    fresh()
    stages = []
    for _ in range(5):
        play(CHAT, 101, 3)
        restart()
        stages.append(eg.seen_count(CHAT, 101))
    check("پیشرفت پیوسته بالا می‌رود", stages == [3, 6, 9, 12, 15],
          f"-> {stages}")


def test_progress_written_to_disk():
    print("\n### 💾 پیشرفت روی دیسک نوشته می‌شود")
    temp = fresh()
    play(CHAT, 102, 4)
    storage.flush()
    raw = json.loads((temp / "economy.json").read_text(encoding="utf-8"))
    check("کلید game_progress در فایل هست", "game_progress" in raw)
    chat_key = economy.chat_key(CHAT)
    stored = raw["game_progress"][chat_key]["102"][eg.GAME]
    check("۴ مرحله در فایل ثبت شده", len(stored) == 4, f"-> {stored}")


def test_no_repeat_after_restart():
    print("\n### 💾 مرحلهٔ تکراری پس از ری‌استارت داده نمی‌شود")
    fresh()
    seen = set(play(CHAT, 103, 20))
    restart()
    later = set(play(CHAT, 103, 20))
    check("هیچ مرحله‌ای تکرار نشد", not (seen & later),
          f"-> {seen & later}")
    check("مجموع ۴۰ مرحلهٔ متمایز", len(seen | later) == 40)


def test_interrupted_stage_not_repeated():
    """اگر ربات وسط مرحله خاموش شود، همان معما تکرار نشود."""
    print("\n### 💾 مرحلهٔ نیمه‌تمام تکرار نمی‌شود")
    fresh()
    puzzle = eg.start(CHAT, 104)
    started = puzzle["answer"]
    restart()                       # بدون پاسخ دادن
    check("مرحلهٔ شروع‌شده ثبت شده", eg.seen_count(CHAT, 104) == 1)
    following = eg.start(CHAT, 104)
    check("همان معما دوباره داده نمی‌شود",
          following["answer"] != started,
          f"-> {following['answer']}")


# ===========================================================================
# جداسازی گروه‌ها
# ===========================================================================
def test_progress_is_per_group():
    print("\n### 🏘️ پیشرفت هر گروه جداست")
    fresh()
    play(CHAT, 200, 7)
    check("گروه A هفت مرحله دارد", eg.seen_count(CHAT, 200) == 7)
    check("گروه B صفر مرحله دارد", eg.seen_count(CHAT_B, 200) == 0,
          f"-> {eg.seen_count(CHAT_B, 200)}")

    play(CHAT_B, 200, 3)
    check("گروه B مستقل پیش می‌رود", eg.seen_count(CHAT_B, 200) == 3)
    check("گروه A دست‌نخورده ماند", eg.seen_count(CHAT, 200) == 7)

    restart()
    check("پس از ری‌استارت گروه A", eg.seen_count(CHAT, 200) == 7)
    check("پس از ری‌استارت گروه B", eg.seen_count(CHAT_B, 200) == 3)


def test_progress_is_per_user():
    print("\n### 🏘️ پیشرفت هر کاربر جداست")
    fresh()
    play(CHAT, 201, 6)
    check("کاربر اول ۶ مرحله", eg.seen_count(CHAT, 201) == 6)
    check("کاربر دوم صفر", eg.seen_count(CHAT, 202) == 0)
    play(CHAT, 202, 2)
    check("کاربر دوم مستقل", eg.seen_count(CHAT, 202) == 2)
    check("کاربر اول دست‌نخورده", eg.seen_count(CHAT, 201) == 6)


# ===========================================================================
# ریست فقط با دستور
# ===========================================================================
def test_only_reset_command_clears():
    print("\n### 🔄 فقط دستور ریست پیشرفت را پاک می‌کند")
    fresh()
    play(CHAT, 300, 9)
    check("۹ مرحله ثبت شد", eg.seen_count(CHAT, 300) == 9)

    restart()
    check("ری‌استارت پاک نمی‌کند", eg.seen_count(CHAT, 300) == 9)

    eg.reset_user(CHAT, 300)
    check("دستور ریست پاک می‌کند", eg.seen_count(CHAT, 300) == 0)
    puzzle = eg.start(CHAT, 300)
    check("پس از ریست از مرحلهٔ ۱ شروع می‌شود", puzzle["stage"] == 1,
          f"-> {puzzle['stage']}")


def test_reset_is_scoped():
    print("\n### 🔄 ریست فقط همان کاربر و گروه را پاک می‌کند")
    fresh()
    play(CHAT, 301, 5)
    play(CHAT, 302, 5)
    play(CHAT_B, 301, 5)

    eg.reset_user(CHAT, 301)
    check("کاربر هدف پاک شد", eg.seen_count(CHAT, 301) == 0)
    check("کاربر دیگر دست‌نخورده", eg.seen_count(CHAT, 302) == 5)
    check("گروه دیگر دست‌نخورده", eg.seen_count(CHAT_B, 301) == 5)


# ===========================================================================
# اتمام و ادامه
# ===========================================================================
def test_exhaustion_needs_all_200():
    print("\n### 🏁 اتمام پس از کل بانک")
    fresh()
    total = len(eg.PUZZLES)
    play(CHAT, 400, total - 1)
    check("پیش از آخرین مرحله تمام نشده",
          not eg.is_exhausted(CHAT, 400),
          f"-> {eg.seen_count(CHAT, 400)}")
    check("یک مرحله باقی است", eg.remaining_count(CHAT, 400) == 1)

    play(CHAT, 400, 1)
    check("پس از کل بانک دور تمام است", eg.is_exhausted(CHAT, 400))
    check("باقی‌مانده صفر", eg.remaining_count(CHAT, 400) == 0)
    check("current_tier در پایان دور None است",
          eg.current_tier(CHAT, 400) is None)

    restart()
    check("پایان دور پس از ری‌استارت هم می‌ماند",
          eg.is_exhausted(CHAT, 400))

    # بازی قفل نمی‌شود: شروع بعدی یک دور تازه می‌سازد.
    again = eg.start(CHAT, 400)
    check("start دور تازه می‌دهد", again is not None)
    check("دور تازه از مرحلهٔ ۱ شروع می‌شود",
          again and again["stage"] == 1, f"-> {again}")


def test_all_200_reachable():
    print("\n### 🏁 همهٔ مرحله‌های بانک قابل دسترسی‌اند")
    fresh()
    total = len(eg.PUZZLES)
    answers = play(CHAT, 401, total)
    check("همهٔ مرحله‌های متمایز بازی شد", len(set(answers)) == total,
          f"-> {len(set(answers))}")
    expected = {answer for _, answer, _ in eg.PUZZLES}
    check("همهٔ مراحل تعریف‌شده دیده شدند", set(answers) == expected)


# ===========================================================================
# مسیر واقعی هندلر
# ===========================================================================
def test_full_scenario_through_handler():
    """سناریوی کامل: بازی، خاموش، روشن، ادامه."""
    print("\n### 🔌 سناریوی کامل از مسیر واقعی ربات")
    fresh()

    async def scenario():
        bot, handler = await build_handler()
        # ۱) چند مرحله رد می‌کنیم
        for _ in range(4):
            await handler(Event("حدس ایموجی", 500))
            state = eg.active_state(CHAT, 500)
            if state:
                await handler(Event(state["answer"], 500))
        before = eg.seen_count(CHAT, 500)

        # ۲و۳) خاموش و روشن
        restart()
        bot2, handler2 = await build_handler()

        # ۴) دوباره اجرا
        event = Event("حدس ایموجی", 500)
        await handler2(event)
        return before, event, eg.active_state(CHAT, 500)

    before, event, state = asyncio.run(scenario())
    check("۴ مرحله بازی شد", before == 4, f"-> {before}")
    check("پس از ری‌استارت ادامه داد",
          state is not None and state["stage"] == 5,
          f"-> {state['stage'] if state else None}")
    check("پیام مرحلهٔ ۵ را نشان می‌دهد", event.said("مرحله 𝟱"),
          f"-> {event.replies}")
    from handlers.message_handler import EMOJI_GUESS_TOTAL
    digits = str(EMOJI_GUESS_TOTAL).translate(
        str.maketrans("0123456789", "𝟬𝟭𝟮𝟯𝟰𝟱𝟲𝟳𝟴𝟵"))
    check("تعداد کل بانک را می‌گوید", event.said(digits),
          f"-> {event.replies}")


def test_reset_command_through_handler():
    print("\n### 🔌 دستور ریست از مسیر واقعی")
    fresh()

    async def scenario():
        bot, handler = await build_handler()
        for _ in range(3):
            await handler(Event("حدس ایموجی", 501))
            state = eg.active_state(CHAT, 501)
            if state:
                await handler(Event(state["answer"], 501))
        before = eg.seen_count(CHAT, 501)

        reset = Event("شروع دوباره حدس ایموجی", 501)
        await handler(reset)
        after = eg.seen_count(CHAT, 501)

        fresh_start = Event("حدس ایموجی", 501)
        await handler(fresh_start)
        return bot, before, reset, after, fresh_start

    bot, before, reset, after, fresh_start = asyncio.run(scenario())
    check("۳ مرحله بازی شد", before == 3, f"-> {before}")
    check("دستور ریست پاسخ می‌دهد", bool(reset.replies),
          "*** هیچ پاسخی نیامد ***")
    check("پیام ریست مناسب است", reset.said("پاک شد"),
          f"-> {reset.replies}")
    check("پیشرفت صفر شد", after == 0, f"-> {after}")
    check("دوباره از مرحلهٔ ۱ شروع می‌شود",
          fresh_start.said("مرحله 𝟭"), f"-> {fresh_start.replies}")
    check("هیچ خطایی نیست", not bot.logger.errors,
          f"-> {[e[:100] for e in bot.logger.errors][:1]}")


def test_reward_still_paid():
    print("\n### 🔌 جایزه همچنان پرداخت می‌شود")
    fresh()

    async def scenario():
        bot, handler = await build_handler()
        await handler(Event("حدس ایموجی", 502))
        state = eg.active_state(CHAT, 502)
        before = economy.get_balance(CHAT, 502)[economy.BRONZE]
        event = Event(state["answer"], 502)
        await handler(event)
        after = economy.get_balance(CHAT, 502)[economy.BRONZE]
        return before, after, event

    before, after, event = asyncio.run(scenario())
    check("۴ برنز پرداخت شد", after - before == 4, f"{before} -> {after}")
    check("پیام موفقیت آمد", event.said("پاسخ صحیح"))


def test_progress_store_is_generic():
    print("\n### 💾 دفتر پیشرفت برای بازی‌های دیگر هم آماده است")
    fresh()
    game_progress.mark_seen(CHAT, 600, "other_game", "x1")
    game_progress.mark_seen(CHAT, 600, "other_game", "x2")
    check("بازی دیگر جدا ثبت می‌شود",
          game_progress.seen_count(CHAT, 600, "other_game") == 2)
    check("روی حدس ایموجی اثر ندارد",
          game_progress.seen_count(CHAT, 600, eg.GAME) == 0)
    check("ثبت تکراری دوباره شمرده نمی‌شود",
          game_progress.mark_seen(CHAT, 600, "other_game", "x1") == 2)


# ===========================================================================
def main():
    test_total_stages()
    test_no_duplicates()
    test_new_stages_come_after_120()
    test_stage_content_is_valid()
    test_tiers_progress_in_difficulty()
    test_progress_survives_restart()
    test_stage_57_continues_at_58()
    test_progress_survives_many_restarts()
    test_progress_written_to_disk()
    test_no_repeat_after_restart()
    test_interrupted_stage_not_repeated()
    test_progress_is_per_group()
    test_progress_is_per_user()
    test_only_reset_command_clears()
    test_reset_is_scoped()
    test_exhaustion_needs_all_200()
    test_all_200_reachable()
    test_full_scenario_through_handler()
    test_reset_command_through_handler()
    test_reward_still_paid()
    test_progress_store_is_generic()
    print(f"\npassed={PASSED} failed={FAILED}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
