"""🏆 جایزهٔ بازی‌ها، اتصال به اقتصاد و رتبه‌بندی per-group.

پوشش:
    • بازی عادی → برنز، بازی سخت → نقره
    • مقدار سکهٔ هر بازی همان مقدار قبلی
    • هیچ بازی‌ای بدون جایزه یا با نوع اشتباه ثبت نشود
    • حدس پرچم: جایزه واقعاً پرداخت شود (باگ UnboundLocalError)
    • همهٔ بازی‌ها به یک اقتصاد مشترک وصل‌اند
    • رتبه‌بندی و بردها کاملاً per-group

    python tests/test_game_rewards.py
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

import economy
import economy.shop.store as store
import economy.storage as storage
import handlers.economy_handler as eco_handler
import handlers.fox_games_router as fox_router
import modules.flag_guess as flag_guess
import modules.group_storage as group_storage
from economy import rewards
from test_economy_routing import build_handler, Event

PASSED = FAILED = 0
CHAT = -1009999888877
CHAT_B = -100555444333
CHAT_C = -100777666555


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
    eco_handler.reset_all()
    flag_guess.reset_history()
    flag_guess._ACTIVE.clear()
    for chat in (CHAT, CHAT_B, CHAT_C):
        group_storage.activate_group(chat, "گروه تست")
    return temp


# ===========================================================================
# ۱) جدول جایزه: عادی برنز، سخت نقره
# ===========================================================================
def test_reward_table_coin_types():
    print("\n### 🏆 نوع سکهٔ هر بازی")
    normal = ("riddle", "emoji", "flag", "name_family", "correction",
              "quiz", "fill_blank", "laugh_or_lose", "lucky_box")
    hard = ("survival", "survival_step", "vampire")

    for game in normal:
        check(f"«{rewards.label_for(game)}» برنز می‌دهد",
              rewards.coin_for(game) == economy.BRONZE,
              f"-> {rewards.coin_for(game)}")
    for game in hard:
        check(f"«{rewards.label_for(game)}» نقره می‌دهد",
              rewards.coin_for(game) == economy.SILVER,
              f"-> {rewards.coin_for(game)}")
    check("بازی سخت با is_hard تشخیص داده می‌شود",
          all(rewards.is_hard(g) for g in hard))
    check("بازی عادی سخت شمرده نمی‌شود",
          not any(rewards.is_hard(g) for g in normal))


def test_reward_amounts_unchanged():
    """مقدار سکه‌ها نباید عوض شده باشد؛ فقط نوع سکه."""
    print("\n### 🏆 مقدار سکه‌ها دست‌نخورده")
    expected = {
        "riddle": 3, "emoji": 4, "flag": 3, "name_family": 6,
        "correction": 3, "quiz": 3, "laugh_or_lose": 3,
        "survival": 8, "survival_step": 1, "vampire": 7,
    }
    for game, amount in expected.items():
        check(f"«{rewards.label_for(game)}» = {amount}",
              rewards.amount_for(game) == amount,
              f"-> {rewards.amount_for(game)}")


def test_no_game_without_reward():
    print("\n### 🏆 هیچ بازی‌ای بدون جایزه نیست")
    for game in rewards.games():
        check(f"«{game}» نوع سکه دارد",
              rewards.coin_for(game) in (economy.BRONZE, economy.SILVER))
        check(f"«{game}» نام فارسی دارد", bool(rewards.label_for(game)))
    payable = [g for g in rewards.games() if g != "lucky_box"]
    check("همهٔ بازی‌ها مقدار مثبت دارند",
          all(rewards.amount_for(g) > 0 for g in payable))
    check("جعبه شانسی مقدار متغیر دارد",
          rewards.amount_for("lucky_box") == 0)


def test_unknown_game_is_rejected():
    print("\n### 🏆 بازی ثبت‌نشده رد می‌شود")
    try:
        rewards.coin_for("no_such_game")
        check("بازی ناشناخته خطا می‌دهد", False)
    except rewards.UnknownGame as error:
        check("بازی ناشناخته خطا می‌دهد", "ثبت نشده" in str(error))
    try:
        economy.award_game(CHAT, 1, "no_such_game")
        check("award_game هم بازی ناشناخته را رد می‌کند", False)
    except rewards.UnknownGame:
        check("award_game هم بازی ناشناخته را رد می‌کند", True)


def test_award_game_pays_right_coin():
    print("\n### 🏆 award_game نوع سکهٔ درست را می‌پردازد")
    fresh()
    economy.award_game(CHAT, 1, "riddle", reference="r1")
    balance = economy.get_balance(CHAT, 1)
    check("بازی عادی فقط برنز داد",
          balance[economy.BRONZE] == 3 and balance[economy.SILVER] == 0)

    economy.award_game(CHAT, 1, "survival", reference="s1")
    balance = economy.get_balance(CHAT, 1)
    check("بازی سخت فقط نقره داد",
          balance[economy.BRONZE] == 3 and balance[economy.SILVER] == 8)
    check("ارزش کل درست محاسبه شد",
          balance["total_coin_value"] == 3 + 8 * 10)
    check("برد ثبت شد", economy.get_profile(CHAT, 1)["wins"] == 2)
    check("تاریخچه نام بازی را دارد",
          economy.transaction_history(CHAT, 1)[0]["note"] == "بقا")


def test_award_game_is_idempotent():
    print("\n### 🏆 مرجع تکراری دوبار پرداخت نمی‌کند")
    fresh()
    economy.award_game(CHAT, 2, "vampire", reference="dup")
    first = economy.get_balance(CHAT, 2)[economy.SILVER]
    economy.award_game(CHAT, 2, "vampire", reference="dup")
    check("جایزه فقط یک بار پرداخت شد",
          economy.get_balance(CHAT, 2)[economy.SILVER] == first)


def test_lucky_box_variable_amount():
    print("\n### 🎁 جعبه شانسی مقدار متغیر")
    fresh()
    economy.award_game(CHAT, 3, "lucky_box", amount=5, reference="lb1")
    check("مقدار داده‌شده پرداخت شد",
          economy.get_balance(CHAT, 3)[economy.BRONZE] == 5)
    economy.award_game(CHAT, 3, "lucky_box", amount=0, reference="lb2")
    check("جایزهٔ صفر چیزی اضافه نمی‌کند",
          economy.get_balance(CHAT, 3)[economy.BRONZE] == 5)


# ===========================================================================
# ۲) حدس پرچم
# ===========================================================================
def test_flag_guess_pays_reward():
    """باگ واقعی: flag_game فقط در شاخهٔ شروع تعریف می‌شد، پس شاخهٔ پاسخ
    UnboundLocalError می‌داد و کاربر «+۳ سکه» می‌دید ولی چیزی نمی‌گرفت."""
    print("\n### 🌍 حدس پرچم واقعاً جایزه می‌دهد")
    fresh()

    async def scenario():
        bot, handler = await build_handler()
        await handler(Event("حدس پرچم", 500))
        state = flag_guess.get_active(CHAT)
        answer = state["answer"] if state else None
        before = economy.get_balance(CHAT, 500)[economy.BRONZE]
        event = Event(answer, 500)
        await handler(event)
        after = economy.get_balance(CHAT, 500)[economy.BRONZE]
        return bot, answer, before, after, event

    bot, answer, before, after, event = asyncio.run(scenario())
    check("بازی شروع شد و پاسخ دارد", bool(answer))
    check("هیچ استثنایی رخ نداد", not bot.logger.errors,
          f"-> {[e[:120] for e in bot.logger.errors][:1]}")
    check("سکه واقعاً اضافه شد", after - before == 3, f"{before} -> {after}")
    check("پیام موفقیت آمد", event.said("پاسخ صحیح"))
    check("نوع سکه برنز اعلام شد", event.said("برنز"))
    check("برد ثبت شد", economy.get_profile(CHAT, 500)["wins"] == 1)


def test_flag_guess_shows_in_profile_and_balance():
    print("\n### 🌍 جایزهٔ پرچم در پروفایل و موجودی دیده می‌شود")
    fresh()
    from economy import profiles
    from economy.ui import balance_menu, profile_menu

    async def scenario():
        bot, handler = await build_handler()
        await handler(Event("حدس پرچم", 501))
        answer = flag_guess.get_active(CHAT)["answer"]
        await handler(Event(answer, 501))
        return bot

    asyncio.run(scenario())
    profiles.register(CHAT, 501, name="آرمین", city="تهران", age=25)
    card, _ = profile_menu.render_card(CHAT, 501, None)
    check("برنز در کارت پروفایل دیده می‌شود", "🥉 برنز: ۳" in card)
    check("برد در کارت پروفایل دیده می‌شود", "🎮 برد: ۱" in card)
    menu, _ = balance_menu.render_menu(CHAT, 501)
    check("برنز در موجودی دیده می‌شود", "🥉 برنز: ۳" in menu)
    check("رتبه در موجودی دیده می‌شود", "🏆 رتبه: ۱" in menu)


def test_flag_reward_not_paid_twice():
    print("\n### 🌍 پرچم دوبار سکه نمی‌دهد")
    fresh()

    async def scenario():
        bot, handler = await build_handler()
        await handler(Event("حدس پرچم", 502))
        answer = flag_guess.get_active(CHAT)["answer"]
        await handler(Event(answer, 502))
        first = economy.get_balance(CHAT, 502)[economy.BRONZE]
        # پاسخ دوباره، بدون بازی فعال
        await handler(Event(answer, 502))
        return first, economy.get_balance(CHAT, 502)[economy.BRONZE]

    first, second = asyncio.run(scenario())
    check("پرداخت دوم انجام نشد", first == second, f"{first} -> {second}")


def test_flag_guess_uses_live_token():
    """token باید پیش از بسته شدن جلسه خوانده شود."""
    print("\n### 🌍 token از جلسهٔ زنده خوانده می‌شود")
    fresh()
    flag_guess.start(CHAT, 503)
    state = flag_guess.get_active(CHAT)
    check("get_active جلسه را برمی‌گرداند", state is not None)
    check("token در جلسه هست", "token" in state)
    flag_guess.answer(CHAT, state["answer"], 503)
    check("بعد از پاسخ جلسه بسته می‌شود",
          flag_guess.get_active(CHAT) is None)


# ===========================================================================
# ۳) اتصال همهٔ بازی‌ها به اقتصاد
# ===========================================================================
def test_all_games_share_one_economy():
    print("\n### 🔗 همهٔ بازی‌ها به یک اقتصاد وصل‌اند")
    fresh()
    for index, game in enumerate(("riddle", "emoji", "flag", "correction",
                                  "quiz", "fill_blank")):
        economy.award_game(CHAT, 600, game, reference=f"{game}:{index}")
    expected = sum(rewards.amount_for(g) for g in
                   ("riddle", "emoji", "flag", "correction", "quiz",
                    "fill_blank"))
    balance = economy.get_balance(CHAT, 600)
    check("همهٔ برنزها در یک کیف پول جمع شدند",
          balance[economy.BRONZE] == expected, f"-> {balance}")

    for index, game in enumerate(("survival", "vampire")):
        economy.award_game(CHAT, 600, game, reference=f"h{game}:{index}")
    balance = economy.get_balance(CHAT, 600)
    check("نقره‌ها هم در همان کیف پول‌اند",
          balance[economy.SILVER] == 8 + 7)
    check("ارزش کل هر دو سکه را می‌شمارد",
          balance["total_coin_value"] == expected + 15 * 10)
    check("همهٔ بردها شمرده شدند",
          economy.get_profile(CHAT, 600)["wins"] == 8)


def test_fox_router_uses_reward_table():
    print("\n### 🔗 روتر Fox از جدول جایزه استفاده می‌کند")
    fresh()

    class Bot:
        pass

    ok = fox_router._coins(Bot(), CHAT, 601, "علی", 8, None,
                           reference="sv1", game="survival")
    check("پرداخت بقا موفق بود", ok)
    check("بقا نقره داد",
          economy.get_balance(CHAT, 601)[economy.SILVER] == 8)
    check("بقا برنز نداد",
          economy.get_balance(CHAT, 601)[economy.BRONZE] == 0)

    ok = fox_router._coins(Bot(), CHAT, 601, "علی", 1, None,
                           reference="lg1", game="laugh_or_lose")
    check("بخند یا بباز برنز داد",
          economy.get_balance(CHAT, 601)[economy.BRONZE] == 1)
    check("نام سکه برای بازی سخت «نقره» است",
          fox_router.coin_word("vampire") == "نقره")
    check("نام سکه برای بازی عادی «برنز» است",
          fox_router.coin_word("laugh_or_lose") == "برنز")


def test_reward_visible_in_profile():
    print("\n### 🔗 جایزه در پروفایل دیده می‌شود")
    fresh()
    from economy import profiles
    from economy.ui import profile_menu
    profiles.register(CHAT, 602, name="سارا", city="کرج", age=24)
    economy.award_game(CHAT, 602, "vampire", reference="v9")
    economy.award_game(CHAT, 602, "riddle", reference="r9")
    card, _ = profile_menu.render_card(CHAT, 602, None)
    check("نقره در کارت هست", "🥈 نقره: ۷" in card, f"-> {card[:200]}")
    check("برنز در کارت هست", "🥉 برنز: ۳" in card)
    check("تعداد برد در کارت هست", "🎮 برد: ۲" in card)


# ===========================================================================
# ۴) رتبه‌بندی per-group
# ===========================================================================
def test_ranking_is_per_group():
    print("\n### 🏆 رتبه‌بندی هر گروه جداست")
    fresh()
    # گروه A: کاربر ۱ قوی‌تر
    economy.award_game(CHAT, 1, "survival", reference="a1")     # 80
    economy.award_game(CHAT, 2, "riddle", reference="a2")       # 3
    # گروه B: برعکس
    economy.award_game(CHAT_B, 2, "vampire", reference="b2")    # 70
    economy.award_game(CHAT_B, 1, "correction", reference="b1")  # 1

    check("در گروه A کاربر ۱ اول است",
          economy.get_rank(CHAT, 1) == 1 and economy.get_rank(CHAT, 2) == 2)
    check("در گروه B کاربر ۲ اول است",
          economy.get_rank(CHAT_B, 2) == 1
          and economy.get_rank(CHAT_B, 1) == 2)
    check("رتبهٔ یک گروه روی گروه دیگر اثر ندارد",
          economy.get_rank(CHAT, 1) != economy.get_rank(CHAT_B, 1))


def test_balances_are_per_group():
    print("\n### 🏆 موجودی هر گروه جداست")
    fresh()
    economy.award_game(CHAT, 10, "riddle", reference="g1")
    economy.award_game(CHAT_B, 10, "survival", reference="g2")
    check("گروه A فقط برنز خودش را دارد",
          economy.get_balance(CHAT, 10)[economy.BRONZE] == 3
          and economy.get_balance(CHAT, 10)[economy.SILVER] == 0)
    check("گروه B فقط نقرهٔ خودش را دارد",
          economy.get_balance(CHAT_B, 10)[economy.SILVER] == 8
          and economy.get_balance(CHAT_B, 10)[economy.BRONZE] == 0)
    check("گروه C خالی است",
          economy.get_balance(CHAT_C, 10)["total_coin_value"] == 0)


def test_wins_are_per_group():
    print("\n### 🏆 بردها هم per-group هستند")
    fresh()
    for index in range(3):
        economy.award_game(CHAT, 11, "riddle", reference=f"w{index}")
    economy.award_game(CHAT_B, 11, "riddle", reference="wb")
    check("بردهای گروه A شمرده شدند",
          economy.get_profile(CHAT, 11)["wins"] == 3)
    check("بردهای گروه B جدا شمرده شدند",
          economy.get_profile(CHAT_B, 11)["wins"] == 1)
    check("گروه C برد ندارد",
          economy.get_profile(CHAT_C, 11)["wins"] == 0)


def test_leaderboard_isolated_between_groups():
    print("\n### 🏆 جدول رتبه بین گروه‌ها نشت نمی‌کند")
    fresh()
    for user_id, amount in ((21, 5), (22, 3), (23, 1)):
        for step in range(amount):
            economy.award_game(CHAT, user_id, "correction",
                               reference=f"A{user_id}:{step}")
    for user_id, amount in ((24, 4), (25, 2)):
        for step in range(amount):
            economy.award_game(CHAT_B, user_id, "correction",
                               reference=f"B{user_id}:{step}")

    board_a = economy.leaderboard(CHAT, 10)
    board_b = economy.leaderboard(CHAT_B, 10)
    ids_a = [row["user_id"] for row in board_a]
    ids_b = [row["user_id"] for row in board_b]
    check("جدول گروه A فقط کاربران خودش را دارد",
          ids_a == ["21", "22", "23"], f"-> {ids_a}")
    check("جدول گروه B فقط کاربران خودش را دارد",
          ids_b == ["24", "25"], f"-> {ids_b}")
    check("هیچ کاربر گروه B در جدول A نیست",
          not set(ids_a) & set(ids_b))
    check("جدول گروه C خالی است", economy.leaderboard(CHAT_C, 10) == [])


def test_same_user_different_rank_per_group():
    print("\n### 🏆 یک کاربر در هر گروه رتبهٔ خودش را دارد")
    fresh()
    # کاربر ۹۹ در گروه A ضعیف، در گروه B قوی، در گروه C تنها.
    economy.award_game(CHAT, 99, "correction", reference="cA")
    economy.award_game(CHAT, 98, "survival", reference="sA")
    economy.award_game(CHAT_B, 99, "survival", reference="sB")
    economy.award_game(CHAT_B, 98, "correction", reference="cB")
    economy.award_game(CHAT_C, 99, "riddle", reference="rC")

    check("در گروه A رتبهٔ دوم است", economy.get_rank(CHAT, 99) == 2)
    check("در گروه B رتبهٔ اول است", economy.get_rank(CHAT_B, 99) == 1)
    check("در گروه C رتبهٔ اول است", economy.get_rank(CHAT_C, 99) == 1)
    correction_award = rewards.amount_for("correction")
    check("موجودی هر گروه مستقل است",
          economy.get_balance(CHAT, 99)[economy.BRONZE] == correction_award
          and economy.get_balance(CHAT_B, 99)[economy.SILVER] == 8
          and economy.get_balance(CHAT_C, 99)[economy.BRONZE] == 3)


def test_many_users_many_groups():
    print("\n### 🏆 چند کاربر در چند گروه")
    fresh()
    plan = {
        CHAT:   {1: 6, 2: 4, 3: 2},
        CHAT_B: {1: 1, 2: 9, 4: 5},
        CHAT_C: {3: 7, 4: 3},
    }
    for chat, users in plan.items():
        for user_id, times in users.items():
            for step in range(times):
                economy.award_game(chat, user_id, "correction",
                                   reference=f"{chat}:{user_id}:{step}")

    for chat, users in plan.items():
        expected = sorted(users, key=lambda u: -users[u])
        actual = [int(r["user_id"]) for r in economy.leaderboard(chat, 10)]
        check(f"ترتیب رتبه در گروه {chat} درست است",
              actual == expected, f"-> {actual} != {expected}")
        award = rewards.amount_for("correction")
        for user_id, times in users.items():
            expected_bronze = times * award
            check(f"موجودی کاربر {user_id} در گروه {chat} = "
                  f"{expected_bronze}",
                  economy.get_balance(chat, user_id)[economy.BRONZE]
                  == expected_bronze,
                  f"-> {economy.get_balance(chat, user_id)[economy.BRONZE]}")


# ===========================================================================
# ۵) مسیر واقعی هندلر
# ===========================================================================
def test_games_through_real_handler():
    print("\n### 🔌 بازی‌ها از مسیر واقعی هندلر")
    fresh()

    async def scenario():
        bot, handler = await build_handler()
        results = {}

        await handler(Event("حدس پرچم", 700))
        answer = flag_guess.get_active(CHAT)["answer"]
        event = Event(answer, 700)
        await handler(event)
        results["flag"] = (event, economy.get_balance(CHAT, 700))
        return bot, results

    bot, results = asyncio.run(scenario())
    event, balance = results["flag"]
    check("پرچم از هندلر واقعی جواب داد", bool(event.replies))
    check("پرچم برنز داد", balance[economy.BRONZE] == 3)
    check("هیچ خطایی در لاگ نیست", not bot.logger.errors,
          f"-> {[e[:100] for e in bot.logger.errors][:1]}")


def test_reward_message_names_the_coin():
    print("\n### 🔌 پیام جایزه نوع سکه را می‌گوید")
    fresh()

    async def scenario():
        bot, handler = await build_handler()
        await handler(Event("حدس پرچم", 701))
        answer = flag_guess.get_active(CHAT)["answer"]
        event = Event(answer, 701)
        await handler(event)
        return event

    event = asyncio.run(scenario())
    check("کلمهٔ «برنز» در پیام هست", event.said("برنز"))
    check("مقدار سکه در پیام هست", event.said("سکه"))
    check("ارزش کل اعلام می‌شود", event.said("ارزش کل"))


# ===========================================================================
def main():
    test_reward_table_coin_types()
    test_reward_amounts_unchanged()
    test_no_game_without_reward()
    test_unknown_game_is_rejected()
    test_award_game_pays_right_coin()
    test_award_game_is_idempotent()
    test_lucky_box_variable_amount()
    test_flag_guess_pays_reward()
    test_flag_guess_shows_in_profile_and_balance()
    test_flag_reward_not_paid_twice()
    test_flag_guess_uses_live_token()
    test_all_games_share_one_economy()
    test_fox_router_uses_reward_table()
    test_reward_visible_in_profile()
    test_ranking_is_per_group()
    test_balances_are_per_group()
    test_wins_are_per_group()
    test_leaderboard_isolated_between_groups()
    test_same_user_different_rank_per_group()
    test_many_users_many_groups()
    test_games_through_real_handler()
    test_reward_message_names_the_coin()
    print(f"\npassed={PASSED} failed={FAILED}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
