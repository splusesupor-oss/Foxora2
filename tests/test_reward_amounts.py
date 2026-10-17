"""🪙 جایزهٔ «بخند یا بباز» و «تصحیح کلمات» = ۳ سکه برنز.

هر دو بازی پیش‌تر ۱ سکه می‌دادند. حالا ۳ سکه می‌دهند و پرداخت از راه
سیستم اقتصاد انجام می‌شود، پس در موجودی، رتبه‌بندی و پروفایل هم دیده
می‌شود.

مهم‌ترین نکته: جایزه باید *دقیقاً یک بار* ثبت شود، حتی اگر پاسخ دوباره
فرستاده شود یا همان مرجع دوباره پرداخت شود.

    python tests/test_reward_amounts.py
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
import handlers.fox_games_router as router
import modules.fox_games.laugh_or_lose as lol
import modules.group_storage as group_storage
import modules.word_correction as wc
from economy import profiles, rewards
from economy.ui import balance_menu, profile_menu
from test_economy_routing import build_handler, Event

PASSED = FAILED = 0
CHAT = -1009999888877


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
    wc._active.clear()
    lol._STORE._sessions.clear() if hasattr(lol._STORE, "_sessions") else None
    group_storage.activate_group(CHAT, "گروه تست")
    return temp


class Bot:
    pass


class Member:
    def __init__(self, user_id):
        self.id = user_id
        self.first_name = "علی"
        self.last_name = None
        self.username = None


# ===========================================================================
# جدول جایزه
# ===========================================================================
def test_reward_table_values():
    print("\n### 🪙 مقدار جایزه در جدول")
    check("«تصحیح کلمات» ۳ سکه است",
          rewards.amount_for("correction") == 3,
          f"-> {rewards.amount_for('correction')}")
    check("«بخند یا بباز» ۳ سکه است",
          rewards.amount_for("laugh_or_lose") == 3,
          f"-> {rewards.amount_for('laugh_or_lose')}")
    check("هر دو برنز می‌دهند",
          rewards.coin_for("correction") == economy.BRONZE
          and rewards.coin_for("laugh_or_lose") == economy.BRONZE)
    check("هیچ‌کدام بازی «سخت» نیستند",
          not rewards.is_hard("correction")
          and not rewards.is_hard("laugh_or_lose"))


def test_laugh_module_reads_the_table():
    """مقدار داخل ماژول نباید از جدول جدا بیفتد."""
    print("\n### 🪙 ماژول بخند یا بباز از جدول می‌خواند")
    check("WINNER_COINS با جدول یکی است",
          lol.WINNER_COINS == rewards.amount_for("laugh_or_lose"),
          f"-> {lol.WINNER_COINS}")
    check("WINNER_COINS برابر ۳ است", lol.WINNER_COINS == 3,
          f"-> {lol.WINNER_COINS}")


def test_other_games_unchanged():
    """فقط این دو بازی عوض شده‌اند."""
    print("\n### 🪙 بقیهٔ بازی‌ها دست‌نخورده‌اند")
    expected = {
        "riddle": 3, "emoji": 4, "flag": 3, "name_family": 6,
        "quiz": 3, "fill_blank": 2,
        "survival": 8, "survival_step": 1, "vampire": 7,
    }
    for game, amount in expected.items():
        check(f"«{rewards.label_for(game)}» = {amount}",
              rewards.amount_for(game) == amount,
              f"-> {rewards.amount_for(game)}")


# ===========================================================================
# تصحیح کلمات
# ===========================================================================
def test_correction_pays_three():
    print("\n### ✍️ تصحیح کلمات ۳ سکه می‌دهد")
    fresh()

    async def scenario():
        bot, handler = await build_handler()
        before = economy.get_balance(CHAT, 10)[economy.BRONZE]
        await handler(Event("تصحیح کلمات", 10))
        correct = wc.get(CHAT)["correct"]
        event = Event(correct, 10)
        await handler(event)
        after = economy.get_balance(CHAT, 10)[economy.BRONZE]
        return bot, before, after, event

    bot, before, after, event = asyncio.run(scenario())
    check("قبل صفر بود", before == 0)
    check("دقیقاً ۳ سکه اضافه شد", after - before == 3,
          f"{before} -> {after}")
    check("پیام موفقیت آمد", event.said("پاسخ صحیح"))
    check("پیام عدد ۳ را می‌گوید", event.said("𝟯"), f"-> {event.replies}")
    check("پیام «برنز» را می‌گوید", event.said("برنز"))
    check("هیچ خطایی نیست", not bot.logger.errors,
          f"-> {[e[:100] for e in bot.logger.errors][:1]}")


def test_correction_pays_only_once():
    print("\n### ✍️ تصحیح کلمات فقط یک بار پرداخت می‌کند")
    fresh()

    async def scenario():
        bot, handler = await build_handler()
        await handler(Event("تصحیح کلمات", 11))
        correct = wc.get(CHAT)["correct"]
        await handler(Event(correct, 11))
        once = economy.get_balance(CHAT, 11)[economy.BRONZE]
        # همان پاسخ را دوباره می‌فرستیم
        for _ in range(3):
            await handler(Event(correct, 11))
        twice = economy.get_balance(CHAT, 11)[economy.BRONZE]
        return once, twice

    once, twice = asyncio.run(scenario())
    check("بار اول ۳ سکه", once == 3, f"-> {once}")
    check("ارسال دوباره سکه اضافه نمی‌کند", twice == 3, f"-> {twice}")
    check("فقط یک تراکنش ثبت شد",
          len(economy.transaction_history(CHAT, 11)) == 1,
          f"-> {len(economy.transaction_history(CHAT, 11))}")


def test_correction_wrong_answer_pays_nothing():
    print("\n### ✍️ پاسخ غلط سکه نمی‌دهد")
    fresh()

    async def scenario():
        bot, handler = await build_handler()
        await handler(Event("تصحیح کلمات", 12))
        await handler(Event("یک پاسخ کاملاً غلط", 12))
        return economy.get_balance(CHAT, 12)[economy.BRONZE]

    balance = asyncio.run(scenario())
    check("هیچ سکه‌ای داده نشد", balance == 0, f"-> {balance}")


# ===========================================================================
# بخند یا بباز
# ===========================================================================
def test_laugh_pays_three():
    print("\n### 😂 بخند یا بباز ۳ سکه می‌دهد")
    fresh()
    lol.start(CHAT, None)
    lol._STORE.get(CHAT)["phase"] = "open"
    win = lol.claim_win(CHAT, 20, Member(20))

    check("برنده ثبت شد", win is not None)
    check("مقدار جایزه در payload برابر ۳ است", win["coins"] == 3,
          f"-> {win['coins']}")

    before = economy.get_balance(CHAT, 20)[economy.BRONZE]
    paid = router._coins(Bot(), CHAT, 20, win["name"], win["coins"], None,
                         reference=f"laugh:{CHAT}:{win['session_id']}",
                         game="laugh_or_lose")
    after = economy.get_balance(CHAT, 20)[economy.BRONZE]
    check("پرداخت موفق بود", paid)
    check("دقیقاً ۳ سکه اضافه شد", after - before == 3,
          f"{before} -> {after}")
    check("برد ثبت شد", economy.get_profile(CHAT, 20)["wins"] == 1)


def test_laugh_pays_only_once():
    print("\n### 😂 بخند یا بباز فقط یک بار پرداخت می‌کند")
    fresh()
    lol.start(CHAT, None)
    lol._STORE.get(CHAT)["phase"] = "open"
    win = lol.claim_win(CHAT, 21, Member(21))
    reference = f"laugh:{CHAT}:{win['session_id']}"

    for _ in range(4):
        router._coins(Bot(), CHAT, 21, win["name"], win["coins"], None,
                      reference=reference, game="laugh_or_lose")
    balance = economy.get_balance(CHAT, 21)[economy.BRONZE]
    check("با وجود ۴ پرداخت، فقط ۳ سکه", balance == 3, f"-> {balance}")
    check("فقط یک تراکنش ثبت شد",
          len(economy.transaction_history(CHAT, 21)) == 1,
          f"-> {len(economy.transaction_history(CHAT, 21))}")
    check("برد فقط یک بار شمرده شد",
          economy.get_profile(CHAT, 21)["wins"] == 1)


def test_laugh_second_claim_rejected():
    """فقط اولین نفر برنده است."""
    print("\n### 😂 نفر دوم برنده نمی‌شود")
    fresh()
    lol.start(CHAT, None)
    lol._STORE.get(CHAT)["phase"] = "open"
    first = lol.claim_win(CHAT, 22, Member(22))
    second = lol.claim_win(CHAT, 23, Member(23))
    check("نفر اول برنده شد", first is not None)
    check("نفر دوم رد شد", second is None, f"-> {second}")


# ===========================================================================
# اتصال به اقتصاد
# ===========================================================================
def test_rewards_visible_everywhere():
    print("\n### 💰 جایزه در موجودی، رتبه و پروفایل دیده می‌شود")
    fresh()
    economy.award_game(CHAT, 30, "correction", reference="c1")
    economy.award_game(CHAT, 30, "laugh_or_lose", reference="l1")

    balance = economy.get_balance(CHAT, 30)
    check("مجموع ۳ + ۳ = ۶", balance[economy.BRONZE] == 6,
          f"-> {balance[economy.BRONZE]}")
    check("ارزش کل درست است", balance["total_coin_value"] == 6)

    menu, _ = balance_menu.render_menu(CHAT, 30)
    check("موجودی ۶ برنز نشان می‌دهد", "🥉 برنز: ۶" in menu)
    check("موجودی رتبه نشان می‌دهد", "🏆 رتبه: ۱" in menu)

    profiles.register(CHAT, 30, name="علی", city="شیراز", age=20)
    card, _ = profile_menu.render_card(CHAT, 30, None)
    check("پروفایل ۶ برنز نشان می‌دهد", "🥉 برنز: ۶" in card)
    check("پروفایل ۲ برد نشان می‌دهد", "🎮 برد: ۲" in card)

    board = economy.leaderboard(CHAT, 5)
    check("در رتبه‌بندی هست",
          any(row["user_id"] == "30" and row["total_coin_value"] == 6
              for row in board), f"-> {board}")


def test_rewards_are_per_group():
    print("\n### 💰 جایزه per-group است")
    fresh()
    other = -100515151
    economy.award_game(CHAT, 31, "correction", reference="c2")
    check("گروه بازی‌شده ۳ سکه دارد",
          economy.get_balance(CHAT, 31)[economy.BRONZE] == 3)
    check("گروه دیگر صفر است",
          economy.get_balance(other, 31)[economy.BRONZE] == 0)


def test_award_game_uses_table_amount():
    """اگر amount داده نشود، مقدار از جدول می‌آید."""
    print("\n### 💰 مقدار پیش‌فرض از جدول می‌آید")
    fresh()
    economy.award_game(CHAT, 32, "correction", reference="x1")
    check("تصحیح کلمات ۳ داد",
          economy.get_balance(CHAT, 32)[economy.BRONZE] == 3)
    economy.award_game(CHAT, 33, "laugh_or_lose", reference="x2")
    check("بخند یا بباز ۳ داد",
          economy.get_balance(CHAT, 33)[economy.BRONZE] == 3)


def test_history_notes_the_game():
    print("\n### 💰 نام بازی در تاریخچه ثبت می‌شود")
    fresh()
    economy.award_game(CHAT, 34, "correction", reference="h1")
    economy.award_game(CHAT, 34, "laugh_or_lose", reference="h2")
    notes = [entry.get("note")
             for entry in economy.transaction_history(CHAT, 34)]
    check("«تصحیح کلمات» در تاریخچه هست", "تصحیح کلمات" in notes,
          f"-> {notes}")
    check("«بخند یا بباز» در تاریخچه هست", "بخند یا بباز" in notes,
          f"-> {notes}")


# ===========================================================================
def main():
    test_reward_table_values()
    test_laugh_module_reads_the_table()
    test_other_games_unchanged()
    test_correction_pays_three()
    test_correction_pays_only_once()
    test_correction_wrong_answer_pays_nothing()
    test_laugh_pays_three()
    test_laugh_pays_only_once()
    test_laugh_second_claim_rejected()
    test_rewards_visible_everywhere()
    test_rewards_are_per_group()
    test_award_game_uses_table_amount()
    test_history_notes_the_game()
    print(f"\npassed={PASSED} failed={FAILED}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
