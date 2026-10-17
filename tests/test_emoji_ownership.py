"""🔐 هر کاربر فقط به معمای خودش پاسخ می‌دهد.

باگ واقعی: سشن حدس ایموجی با کلید ``chat_id`` نگه داشته می‌شد، پس یک
سشن برای کل گروه بود. نتیجه:

  • هر عضوی می‌توانست پاسخ معمای شخص دیگر را بفرستد و سکه بگیرد.
  • با پاسخ آن شخص، معمای صاحب اصلی هم بسته می‌شد.
  • تا وقتی یک نفر بازی می‌کرد، بقیه اصلاً نمی‌توانستند شروع کنند.

حالا کلید ``(chat_id, user_id)`` است، دقیقاً مثل چیستان و جای خالی.

    python tests/test_emoji_ownership.py
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

# ⚠️ پیش از import هر بازی: وگرنه نوشتن‌ها روی config واقعی می‌نشیند.
import tempfile as _tempfile
import economy.storage as _storage
_storage.use_file(Path(_tempfile.mkdtemp()) / "economy.json")

import economy
import economy.shop.store as store
import economy.storage as storage
import modules.emoji_guess as eg
import modules.group_storage as group_storage
from test_economy_routing import build_handler, Event

PASSED = FAILED = 0
CHAT = -1009999888877
CHAT_B = -100626262626


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


# ===========================================================================
# مالکیت معما
# ===========================================================================
def test_outsider_cannot_answer():
    print("\n### 🔐 پاسخ کاربر دیگر پذیرفته نمی‌شود")
    fresh()
    puzzle = eg.start(CHAT, 1)
    before = economy.get_balance(CHAT, 2)[economy.BRONZE]

    result = eg.answer(CHAT, 2, "مزاحم", puzzle["answer"])
    after = economy.get_balance(CHAT, 2)[economy.BRONZE]

    check("پاسخ رد می‌شود", result is None, f"-> {result}")
    check("هیچ سکه‌ای داده نمی‌شود", after == before, f"{before} -> {after}")
    check("معمای صاحبش باز می‌ماند", eg.is_active(CHAT, 1))
    check("مزاحم سشنی ندارد", not eg.is_active(CHAT, 2))


def test_owner_can_answer():
    print("\n### 🔐 صاحب معما پاسخ می‌گیرد")
    fresh()
    puzzle = eg.start(CHAT, 3)
    result = eg.answer(CHAT, 3, "صاحب", puzzle["answer"])
    check("پاسخ پذیرفته می‌شود", result == puzzle["answer"], f"-> {result}")
    check("سکه پرداخت شد",
          economy.get_balance(CHAT, 3)[economy.BRONZE] == 4,
          f"-> {economy.get_balance(CHAT, 3)[economy.BRONZE]}")
    check("سشن بسته شد", not eg.is_active(CHAT, 3))


def test_outsider_attempt_does_not_consume_puzzle():
    """تلاش ناموفق دیگران نباید معما را بسوزاند."""
    print("\n### 🔐 تلاش مزاحم معما را نمی‌سوزاند")
    fresh()
    puzzle = eg.start(CHAT, 4)
    for intruder in (5, 6, 7):
        eg.answer(CHAT, intruder, "م", puzzle["answer"])
    check("معما هنوز همان است",
          eg.active_state(CHAT, 4)["answer"] == puzzle["answer"])
    check("صاحبش هنوز می‌تواند پاسخ دهد",
          eg.answer(CHAT, 4, "صاحب", puzzle["answer"]) == puzzle["answer"])
    for intruder in (5, 6, 7):
        check(f"مزاحم {intruder} سکه نگرفت",
              economy.get_balance(CHAT, intruder)[economy.BRONZE] == 0)


def test_wrong_answer_keeps_session():
    print("\n### 🔐 پاسخ غلط سشن را نمی‌بندد")
    fresh()
    puzzle = eg.start(CHAT, 8)
    check("پاسخ غلط رد می‌شود",
          eg.answer(CHAT, 8, "u", "یک پاسخ کاملاً غلط") is None)
    check("سشن باز مانده", eg.is_active(CHAT, 8))
    check("پاسخ درست بعدش کار می‌کند",
          eg.answer(CHAT, 8, "u", puzzle["answer"]) == puzzle["answer"])


# ===========================================================================
# بازی هم‌زمان
# ===========================================================================
def test_simultaneous_players():
    print("\n### 👥 چند کاربر هم‌زمان بازی می‌کنند")
    fresh()
    players = {}
    for user_id in (10, 11, 12, 13, 14):
        players[user_id] = eg.start(CHAT, user_id)
    check("همه معما گرفتند", all(players.values()),
          f"-> {[k for k, v in players.items() if not v]}")
    check("هر کدام سشن جدا دارند",
          all(eg.is_active(CHAT, user_id) for user_id in players))
    check("فهرست بازیکنان فعال درست است",
          set(eg.active_players(CHAT)) == {str(u) for u in players},
          f"-> {eg.active_players(CHAT)}")


def test_one_answer_does_not_affect_others():
    print("\n### 👥 پاسخ یکی روی بقیه اثر ندارد")
    fresh()
    a = eg.start(CHAT, 20)
    b = eg.start(CHAT, 21)
    c = eg.start(CHAT, 22)

    eg.answer(CHAT, 21, "u", b["answer"])
    check("سشن پاسخ‌دهنده بسته شد", not eg.is_active(CHAT, 21))
    check("سشن کاربر ۲۰ باز ماند", eg.is_active(CHAT, 20))
    check("سشن کاربر ۲۲ باز ماند", eg.is_active(CHAT, 22))
    check("معمای کاربر ۲۰ عوض نشد",
          eg.active_state(CHAT, 20)["answer"] == a["answer"])
    check("معمای کاربر ۲۲ عوض نشد",
          eg.active_state(CHAT, 22)["answer"] == c["answer"])
    check("فقط پاسخ‌دهنده سکه گرفت",
          economy.get_balance(CHAT, 21)[economy.BRONZE] == 4
          and economy.get_balance(CHAT, 20)[economy.BRONZE] == 0
          and economy.get_balance(CHAT, 22)[economy.BRONZE] == 0)


def test_restart_blocked_only_for_owner():
    print("\n### 👥 شروع دوباره فقط برای خود کاربر بسته است")
    fresh()
    first = eg.start(CHAT, 30)
    again = eg.start(CHAT, 30)
    other = eg.start(CHAT, 31)
    check("کاربر نمی‌تواند معمای باز خودش را بازنویسی کند", again is None)
    check("معمای اولش دست‌نخورده است",
          eg.active_state(CHAT, 30)["answer"] == first["answer"])
    check("کاربر دیگر آزادانه شروع می‌کند", other is not None)


def test_timer_closes_only_own_session():
    print("\n### ⏰ تایمر فقط سشن خودش را می‌بندد")
    fresh()
    a = eg.start(CHAT, 40)
    b = eg.start(CHAT, 41)

    closed = eg.finish(CHAT, a["token"], 40)
    check("سشن کاربر ۴۰ بسته شد",
          closed == a["answer"] and not eg.is_active(CHAT, 40))
    check("سشن کاربر ۴۱ دست‌نخورده", eg.is_active(CHAT, 41))
    check("معمای ۴۱ عوض نشد",
          eg.active_state(CHAT, 41)["answer"] == b["answer"])


def test_stale_timer_does_not_close_new_round():
    print("\n### ⏰ تایمر کهنه دور تازه را نمی‌بندد")
    fresh()
    first = eg.start(CHAT, 50)
    eg.answer(CHAT, 50, "u", first["answer"])
    second = eg.start(CHAT, 50)

    check("توکن دور تازه متفاوت است", second["token"] != first["token"])
    check("تایمر دور قبلی بی‌اثر است",
          eg.finish(CHAT, first["token"], 50) is None)
    check("دور تازه هنوز باز است", eg.is_active(CHAT, 50))


def test_sessions_are_per_group():
    print("\n### 🏘️ سشن‌ها بین گروه‌ها جدا هستند")
    fresh()
    a = eg.start(CHAT, 60)
    b = eg.start(CHAT_B, 60)
    check("همان کاربر در دو گروه دو سشن دارد",
          a is not None and b is not None)
    check("معماها مستقل‌اند",
          eg.is_active(CHAT, 60) and eg.is_active(CHAT_B, 60))

    eg.answer(CHAT, 60, "u", a["answer"])
    check("پاسخ در گروه A سشن گروه B را نمی‌بندد",
          eg.is_active(CHAT_B, 60))
    check("پاسخ گروه A فقط در گروه A سکه داد",
          economy.get_balance(CHAT, 60)[economy.BRONZE] == 4
          and economy.get_balance(CHAT_B, 60)[economy.BRONZE] == 0)


def test_is_active_signatures():
    print("\n### 🔎 is_active با و بدون user_id")
    fresh()
    check("در ابتدا کسی فعال نیست", not eg.is_active(CHAT))
    eg.start(CHAT, 70)
    check("با user_id درست است", eg.is_active(CHAT, 70))
    check("برای کاربر دیگر نادرست است", not eg.is_active(CHAT, 71))
    check("بدون user_id یعنی «کسی در گروه»", eg.is_active(CHAT))
    check("گروه دیگر فعال نیست", not eg.is_active(CHAT_B))


def test_reset_user_only_clears_own_session():
    print("\n### 🔄 ریست فقط سشن خودش را پاک می‌کند")
    fresh()
    eg.start(CHAT, 80)
    other = eg.start(CHAT, 81)
    eg.reset_user(CHAT, 80)
    check("سشن کاربر ۸۰ پاک شد", not eg.is_active(CHAT, 80))
    check("سشن کاربر ۸۱ باقی ماند", eg.is_active(CHAT, 81))
    check("معمای ۸۱ عوض نشد",
          eg.active_state(CHAT, 81)["answer"] == other["answer"])


# ===========================================================================
# مسیر واقعی هندلر
# ===========================================================================
def test_handler_blocks_answer_theft():
    print("\n### 🔌 سرقت پاسخ از مسیر واقعی ربات")
    fresh()

    async def scenario():
        bot, handler = await build_handler()
        await handler(Event("حدس ایموجی", 100))
        owner_state = eg.active_state(CHAT, 100)
        thief = Event(owner_state["answer"], 200)
        await handler(thief)
        return bot, owner_state, thief

    bot, owner_state, thief = asyncio.run(scenario())
    check("ربات به مزاحم پاسخ موفقیت نمی‌دهد",
          not thief.said("پاسخ صحیح"), f"-> {thief.replies}")
    check("مزاحم سکه نگرفت",
          economy.get_balance(CHAT, 200)[economy.BRONZE] == 0)
    check("معمای صاحبش باز ماند", eg.is_active(CHAT, 100))
    check("معمای صاحبش عوض نشد",
          eg.active_state(CHAT, 100)["answer"] == owner_state["answer"])
    check("هیچ خطایی رخ نداد", not bot.logger.errors,
          f"-> {[e[:100] for e in bot.logger.errors][:1]}")


def test_handler_two_players_at_once():
    print("\n### 🔌 دو بازیکن هم‌زمان از مسیر واقعی")
    fresh()

    async def scenario():
        bot, handler = await build_handler()
        first = Event("حدس ایموجی", 101)
        await handler(first)
        second = Event("حدس ایموجی", 102)
        await handler(second)
        state_a = eg.active_state(CHAT, 101)
        state_b = eg.active_state(CHAT, 102)

        # اگر نفر دوم معما نگرفته باشد، تست باید شکست بخورد نه کرش کند.
        reply_a = Event(state_a["answer"] if state_a else "-", 101)
        await handler(reply_a)
        reply_b = Event(state_b["answer"] if state_b else "-", 102)
        await handler(reply_b)
        return bot, second, state_a, state_b, reply_a, reply_b

    bot, second, state_a, state_b, reply_a, reply_b = asyncio.run(scenario())
    check("نفر دوم پیام «بازی در جریان است» نمی‌گیرد",
          not second.said("بازی دیگر"), f"-> {second.replies}")
    check("نفر دوم معما گرفت", state_b is not None)
    check("دو معما متفاوت‌اند",
          bool(state_a) and bool(state_b)
          and state_a["answer"] != state_b["answer"])
    check("هر دو پاسخ درست گرفتند",
          reply_a.said("پاسخ صحیح") and reply_b.said("پاسخ صحیح"))
    check("هر دو سکه گرفتند",
          economy.get_balance(CHAT, 101)[economy.BRONZE] == 4
          and economy.get_balance(CHAT, 102)[economy.BRONZE] == 4)
    check("هیچ خطایی رخ نداد", not bot.logger.errors)


def test_handler_rejects_double_start():
    print("\n### 🔌 شروع دوباره توسط همان کاربر")
    fresh()

    async def scenario():
        bot, handler = await build_handler()
        await handler(Event("حدس ایموجی", 103))
        first = eg.active_state(CHAT, 103)
        again = Event("حدس ایموجی", 103)
        await handler(again)
        return first, again, eg.active_state(CHAT, 103)

    first, again, current = asyncio.run(scenario())
    check("پیام مناسب داده می‌شود", again.said("معمای باز"),
          f"-> {again.replies}")
    check("معمای اولش دست‌نخورده ماند",
          current["answer"] == first["answer"])


def test_emoji_does_not_lock_other_games():
    """حدس ایموجی دیگر کل گروه را قفل نمی‌کند.

    از مسیر واقعی هندلر بررسی می‌شود: اگر ایموجی داخل گیت «بازی در
    جریان» برگردد، نفر دوم پیام «صبر کنید» می‌گیرد و اصلاً معما
    نمی‌گیرد.
    """
    print("\n### 🔌 حدس ایموجی گروه را قفل نمی‌کند")
    fresh()
    import handlers.message_handler as mh

    async def scenario():
        bot, handler = await build_handler()
        await handler(Event("حدس ایموجی", 104))
        busy = mh._chat_game_busy(CHAT)
        second = Event("حدس ایموجی", 105)
        await handler(second)
        return busy, second, eg.active_state(CHAT, 105)

    busy, second, state = asyncio.run(scenario())
    check("گیت «بازی در جریان» فعال نمی‌شود", not busy)
    check("نفر دوم پیام صبر نمی‌گیرد",
          not second.said("بازی دیگر"), f"-> {second.replies}")
    check("نفر دوم واقعاً معما می‌گیرد", state is not None)


def test_reward_reference_is_per_user():
    print("\n### 🪙 مرجع جایزه برای هر کاربر یکتاست")
    fresh()
    a = eg.start(CHAT, 110)
    b = eg.start(CHAT, 111)
    eg.answer(CHAT, 110, "u", a["answer"])
    eg.answer(CHAT, 111, "u", b["answer"])
    check("هر دو دقیقاً یک بار سکه گرفتند",
          economy.get_balance(CHAT, 110)[economy.BRONZE] == 4
          and economy.get_balance(CHAT, 111)[economy.BRONZE] == 4)
    history_a = economy.transaction_history(CHAT, 110)
    history_b = economy.transaction_history(CHAT, 111)
    check("تاریخچهٔ هرکدام یک رکورد دارد",
          len(history_a) == 1 and len(history_b) == 1,
          f"-> {len(history_a)}, {len(history_b)}")


# ===========================================================================
def main():
    test_outsider_cannot_answer()
    test_owner_can_answer()
    test_outsider_attempt_does_not_consume_puzzle()
    test_wrong_answer_keeps_session()
    test_simultaneous_players()
    test_one_answer_does_not_affect_others()
    test_restart_blocked_only_for_owner()
    test_timer_closes_only_own_session()
    test_stale_timer_does_not_close_new_round()
    test_sessions_are_per_group()
    test_is_active_signatures()
    test_reset_user_only_clears_own_session()
    test_handler_blocks_answer_theft()
    test_handler_two_players_at_once()
    test_handler_rejects_double_start()
    test_emoji_does_not_lock_other_games()
    test_reward_reference_is_per_user()
    print(f"\npassed={PASSED} failed={FAILED}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
