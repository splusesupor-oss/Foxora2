"""💎 ارزش کل همیشه از روی موجودی واقعی محاسبه می‌شود.

قانون: ارزش کل هرگز یک مقدار ذخیره‌شده نیست. هر بار که موجودی نمایش
داده می‌شود، ارزش کل از روی برنز/نقره/طلای فعلی و ارزش‌های تنظیمات
دوباره ساخته می‌شود — نه از تاریخچهٔ تراکنش، نه از یک فیلد کش‌شده.

باگ واقعی: ``_snapshot_balance`` فیلد ذخیره‌شدهٔ ``total_coin_value`` را
برمی‌گرداند. در مسیرهایی که بدون بازمحاسبه زود برمی‌گردند (مثل «مرجع
تکراری») یا وقتی ارزش سکه‌ها در تنظیمات عوض می‌شد، عددِ کهنه نمایش
داده می‌شد.

    python tests/test_total_value.py
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
import economy.coins.accounts as accounts
import economy.shop.store as store
import economy.storage as storage
import handlers.economy_handler as eco_handler
import modules.group_storage as group_storage
from economy import profiles, settings
from economy.ui import balance_menu
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
    eco_handler.reset_all()
    # تنظیمات هم باید موقت باشد، وگرنه save() فایل واقعی config را
    # می‌نویسد و تست‌های بعدی را با نرخ دستکاری‌شده آلوده می‌کند.
    settings.SETTINGS_FILE = temp / "economy_settings.json"
    settings.reset_cache()
    settings.save({"BronzeValue": 1, "SilverValue": 10, "GoldValue": 100})
    group_storage.activate_group(CHAT, "گروه تست")
    return temp


def expected(balance):
    """ارزش کل درست، فقط از روی موجودی فعلی."""
    values = settings.coin_values()
    return (balance[economy.BRONZE] * values[economy.BRONZE]
            + balance[economy.SILVER] * values[economy.SILVER]
            + balance[economy.GOLD] * values[economy.GOLD])


def agrees(balance):
    return balance["total_coin_value"] == expected(balance)


# ===========================================================================
# فرمول پایه
# ===========================================================================
def test_formula_is_coins_only():
    print("\n### 💎 فرمول فقط بر پایهٔ موجودی است")
    fresh()
    economy.add_bronze(CHAT, 1, 173)
    economy.add_silver(CHAT, 1, 2)
    balance = economy.get_balance(CHAT, 1)
    check("۱۷۳ برنز + ۲ نقره = ۱۹۳",
          balance["total_coin_value"] == 193,
          f"-> {balance['total_coin_value']}")

    economy.add_gold(CHAT, 1, 3)
    balance = economy.get_balance(CHAT, 1)
    check("افزودن ۳ طلا ۳۰۰ اضافه می‌کند",
          balance["total_coin_value"] == 493,
          f"-> {balance['total_coin_value']}")
    check("با فرمول هم‌خوان است", agrees(balance))


def test_history_does_not_affect_total():
    """ارزش کل نباید از تاریخچهٔ تراکنش ساخته شود."""
    print("\n### 💎 تاریخچه در ارزش کل نقشی ندارد")
    fresh()
    for index in range(20):
        economy.add_bronze(CHAT, 2, 5, reference=f"r{index}")
    balance = economy.get_balance(CHAT, 2)
    check("۲۰ تراکنش ۵تایی = ۱۰۰ برنز",
          balance[economy.BRONZE] == 100)
    check("ارزش کل = ۱۰۰، نه جمع تاریخچه",
          balance["total_coin_value"] == 100,
          f"-> {balance['total_coin_value']}")
    check("تعداد تراکنش‌ها روی ارزش اثر ندارد",
          len(economy.transaction_history(CHAT, 2)) == 20
          and balance["total_coin_value"] == 100)


def test_total_is_not_stored_value():
    """قلب باگ: فیلد ذخیره‌شده نباید ملاک نمایش باشد."""
    print("\n### 💎 مقدار ذخیره‌شده ملاک نیست")
    fresh()
    economy.add_bronze(CHAT, 3, 100)
    economy.add_bronze(CHAT, 3, 10, reference="dupe")

    # فیلد ذخیره‌شده را عمداً کهنه می‌کنیم و سکه‌ها را عوض می‌کنیم.
    key = accounts.user_key(CHAT, 3)
    with storage.transaction() as data:
        user = accounts._user(data, key)
        user[economy.SILVER] = 50          # +۵۰۰ ارزش
        # total_coin_value عمداً دست‌نخورده می‌ماند

    stored = storage.snapshot()["users"][key]["total_coin_value"]
    truth = economy.get_balance(CHAT, 3)
    check("فیلد ذخیره‌شده کهنه است", stored != truth["total_coin_value"],
          f"stored={stored} truth={truth['total_coin_value']}")
    check("get_balance مقدار درست می‌دهد", agrees(truth))

    # مسیر «مرجع تکراری» که بدون بازمحاسبه زود برمی‌گردد.
    duplicate = economy.add_bronze(CHAT, 3, 10, reference="dupe")
    check("مسیر مرجع تکراری هم مقدار درست می‌دهد", agrees(duplicate),
          f"-> {duplicate}")
    check("با get_balance یکی است",
          duplicate["total_coin_value"] == truth["total_coin_value"],
          f"{duplicate['total_coin_value']} != {truth['total_coin_value']}")


def test_settings_change_reflects_immediately():
    print("\n### 💎 تغییر ارزش سکه فوراً اثر می‌کند")
    fresh()
    economy.add_silver(CHAT, 4, 10)
    check("با نرخ ۱۰: ارزش = ۱۰۰",
          economy.get_balance(CHAT, 4)["total_coin_value"] == 100)
    settings.save({"SilverValue": 25})
    check("با نرخ ۲۵: ارزش = ۲۵۰",
          economy.get_balance(CHAT, 4)["total_coin_value"] == 250,
          f"-> {economy.get_balance(CHAT, 4)['total_coin_value']}")
    returned = economy.add_bronze(CHAT, 4, 1)
    check("مقدار برگشتی عملیات هم به‌روز است", agrees(returned),
          f"-> {returned}")
    settings.save({"SilverValue": 10})


def test_empty_wallet():
    print("\n### 💎 کیف پول خالی")
    fresh()
    balance = economy.get_balance(CHAT, 99)
    check("ارزش کل صفر است", balance["total_coin_value"] == 0)
    check("با فرمول هم‌خوان است", agrees(balance))


# ===========================================================================
# ۱) تبدیل برنز → نقره
# ===========================================================================
def test_convert_bronze_to_silver():
    print("\n### ۱) تبدیل برنز به نقره")
    fresh()
    economy.add_bronze(CHAT, 10, 173)
    economy.add_silver(CHAT, 10, 2)
    before = economy.get_balance(CHAT, 10)
    check("قبل: ۱۹۳", before["total_coin_value"] == 193)

    returned = economy.convert_bronze(CHAT, 10)
    after = economy.get_balance(CHAT, 10)
    # تبدیل حالا یک «ارتقا» است: ۱۰۰ برنز ➜ ۱۲ نقره.
    check("موجودی درست شد: ۷۳ برنز، ۱۴ نقره",
          after[economy.BRONZE] == 73 and after[economy.SILVER] == 14,
          f"-> {after}")
    check("مقدار برگشتی با فرمول هم‌خوان است", agrees(returned))
    check("get_balance با فرمول هم‌خوان است", agrees(after))
    check("تبدیل سود می‌دهد: ۱۹۳ ➜ ۲۱۳",
          after["total_coin_value"] == 213,
          f"-> {after['total_coin_value']}")


def test_convert_bronze_changes_total_when_rates_differ():
    """اگر نرخ تبدیل هم‌ارزش نباشد، ارزش کل باید واقعاً عوض شود."""
    print("\n### ۱) تبدیل برنز وقتی نرخ هم‌ارزش نیست")
    fresh()
    settings.save({"BronzeToSilverCost": 100, "BronzeToSilverGain": 20})
    economy.add_bronze(CHAT, 11, 200)
    before = economy.get_balance(CHAT, 11)["total_coin_value"]
    economy.convert_bronze(CHAT, 11)
    after = economy.get_balance(CHAT, 11)
    check("ارزش کل واقعاً بالا رفت", after["total_coin_value"] > before,
          f"{before} -> {after['total_coin_value']}")
    check("با فرمول هم‌خوان است", agrees(after))
    settings.save({"BronzeToSilverCost": 100, "BronzeToSilverGain": 10})


# ===========================================================================
# ۲) تبدیل نقره → طلا
# ===========================================================================
def test_convert_silver_to_gold():
    print("\n### ۲) تبدیل نقره به طلا")
    fresh()
    economy.add_silver(CHAT, 12, 100)
    before = economy.get_balance(CHAT, 12)
    check("قبل: ۱۰۰۰", before["total_coin_value"] == 1000)

    returned = economy.convert_silver(CHAT, 12)
    after = economy.get_balance(CHAT, 12)
    check("موجودی: ۳۰ نقره، ۱۰ طلا",
          after[economy.SILVER] == 30 and after[economy.GOLD] == 10,
          f"-> {after}")
    check("ارزش کل به ۱۳۰۰ رسید",
          after["total_coin_value"] == 1300,
          f"-> {after['total_coin_value']}")
    check("مقدار برگشتی با فرمول هم‌خوان است", agrees(returned))
    check("get_balance با فرمول هم‌خوان است", agrees(after))


# ===========================================================================
# ۳) خرید آیتم
# ===========================================================================
def test_purchase_updates_total():
    print("\n### ۳) خرید آیتم")
    fresh()
    economy.add_silver(CHAT, 13, 500)
    before = economy.get_balance(CHAT, 13)
    check("قبل: ۵۰۰۰", before["total_coin_value"] == 5000)

    item, balance, profile = profiles.buy(CHAT, 13, "badge_fox")
    after = economy.get_balance(CHAT, 13)
    check("۱۰۰ نقره کسر شد", after[economy.SILVER] == 400)
    check("ارزش کل به ۴۰۰۰ رسید", after["total_coin_value"] == 4000,
          f"-> {after['total_coin_value']}")
    check("مقدار برگشتی خرید با فرمول هم‌خوان است", agrees(balance))
    check("get_balance با فرمول هم‌خوان است", agrees(after))


def test_shop_purchase_updates_total():
    print("\n### ۳) خرید از فروشگاه پویا")
    fresh()
    economy.shop.add_item("gem", "نگین", 50, "bronze")
    economy.add_bronze(CHAT, 14, 200)
    item, balance = economy.shop.buy(CHAT, 14, "gem")
    after = economy.get_balance(CHAT, 14)
    check("سکه کسر شد", after[economy.BRONZE] == 150)
    check("ارزش کل درست است", after["total_coin_value"] == 150)
    check("مقدار برگشتی با فرمول هم‌خوان است", agrees(balance))


# ===========================================================================
# ۴) انتقال سکه
# ===========================================================================
def test_transfer_updates_both_sides():
    print("\n### ۴) انتقال سکه")
    fresh()
    economy.add_bronze(CHAT, 15, 300)
    economy.add_silver(CHAT, 16, 5)

    result = economy.transfer(CHAT, 15, 16, economy.BRONZE, 120)
    sender = economy.get_balance(CHAT, 15)
    receiver = economy.get_balance(CHAT, 16)

    check("فرستنده: ۱۸۰ برنز", sender[economy.BRONZE] == 180)
    check("فرستنده ارزش کل: ۱۸۰", sender["total_coin_value"] == 180)
    check("گیرنده: ۱۲۰ برنز + ۵ نقره",
          receiver[economy.BRONZE] == 120 and receiver[economy.SILVER] == 5)
    check("گیرنده ارزش کل: ۱۷۰", receiver["total_coin_value"] == 170,
          f"-> {receiver['total_coin_value']}")
    check("مقدار برگشتی فرستنده هم‌خوان است", agrees(result["sender"]))
    check("مقدار برگشتی گیرنده هم‌خوان است", agrees(result["receiver"]))
    check("جمع ارزش دو طرف حفظ شد",
          sender["total_coin_value"] + receiver["total_coin_value"]
          == 300 + 50)


# ===========================================================================
# ۵) جایزه بازی و کسر
# ===========================================================================
def test_game_reward_updates_total():
    print("\n### ۵) جایزهٔ بازی")
    fresh()
    bronze = economy.award_game(CHAT, 17, "flag", reference="f1")
    check("بازی عادی: برنز", bronze[economy.BRONZE] == 3)
    check("ارزش کل = ۳", bronze["total_coin_value"] == 3)
    check("با فرمول هم‌خوان است", agrees(bronze))

    silver = economy.award_game(CHAT, 17, "survival", reference="s1")
    check("بازی سخت: نقره", silver[economy.SILVER] == 8)
    check("ارزش کل = ۳ + ۸۰ = ۸۳",
          silver["total_coin_value"] == 83,
          f"-> {silver['total_coin_value']}")
    check("با فرمول هم‌خوان است", agrees(silver))


def test_spend_updates_total():
    print("\n### ۵) کسر سکه")
    fresh()
    economy.add_bronze(CHAT, 18, 100)
    remaining = economy.spend(CHAT, 18, 30)
    check("۳۰ کسر شد", remaining[economy.BRONZE] == 70)
    check("ارزش کل = ۷۰", remaining["total_coin_value"] == 70)
    check("با فرمول هم‌خوان است", agrees(remaining))
    check("get_balance هم‌خوان است",
          agrees(economy.get_balance(CHAT, 18)))


def test_daily_reward_updates_total():
    print("\n### ۵) جایزهٔ روزانه")
    fresh()
    granted, balance, wait = economy.claim_daily(CHAT, 19)
    check("جایزه داده شد", granted)
    check("با فرمول هم‌خوان است", agrees(balance), f"-> {balance}")


# ===========================================================================
# هر عملیات، بدون استثنا
# ===========================================================================
def test_every_operation_agrees():
    print("\n### 💎 همهٔ عملیات‌ها با فرمول هم‌خوان‌اند")
    fresh()
    economy.add_bronze(CHAT, 20, 1000)
    economy.add_silver(CHAT, 20, 200)
    economy.add_gold(CHAT, 20, 5)

    operations = [
        ("add_bronze", lambda: economy.add_bronze(CHAT, 20, 7)),
        ("add_silver", lambda: economy.add_silver(CHAT, 20, 3)),
        ("add_gold", lambda: economy.add_gold(CHAT, 20, 1)),
        ("remove_bronze", lambda: economy.remove_bronze(CHAT, 20, 5)),
        ("remove_silver", lambda: economy.remove_silver(CHAT, 20, 2)),
        ("convert_bronze", lambda: economy.convert_bronze(CHAT, 20)),
        ("convert_silver", lambda: economy.convert_silver(CHAT, 20)),
        ("award_game", lambda: economy.award_game(CHAT, 20, "riddle",
                                                  reference="op1")),
        ("spend", lambda: economy.spend(CHAT, 20, 4)),
    ]
    for name, operation in operations:
        returned = operation()
        live = economy.get_balance(CHAT, 20)
        check(f"«{name}» مقدار برگشتی درست است", agrees(returned),
              f"-> {returned}")
        check(f"«{name}» با get_balance یکی است",
              returned["total_coin_value"] == live["total_coin_value"],
              f"{returned['total_coin_value']} != "
              f"{live['total_coin_value']}")


def test_profile_and_ranking_agree():
    print("\n### 💎 پروفایل و رتبه‌بندی هم همان عدد را می‌دهند")
    fresh()
    economy.add_bronze(CHAT, 21, 173)
    economy.add_silver(CHAT, 21, 2)
    economy.convert_bronze(CHAT, 21)

    balance = economy.get_balance(CHAT, 21)
    profile = economy.get_profile(CHAT, 21)
    board = economy.leaderboard(CHAT, 5)
    row = next(r for r in board if r["user_id"] == "21")

    check("پروفایل با موجودی یکی است",
          profile["total_coin_value"] == balance["total_coin_value"],
          f"{profile['total_coin_value']} != "
          f"{balance['total_coin_value']}")
    check("رتبه‌بندی با موجودی یکی است",
          row["total_coin_value"] == balance["total_coin_value"],
          f"{row['total_coin_value']} != {balance['total_coin_value']}")
    check("همه با فرمول هم‌خوان‌اند", agrees(balance))


# ===========================================================================
# از مسیر واقعی هندلر
# ===========================================================================
def test_conversion_through_handler():
    print("\n### 🔌 تبدیل از مسیر واقعی هندلر")
    fresh()

    async def scenario():
        bot, handler = await build_handler()
        economy.add_bronze(CHAT, 30, 173)
        economy.add_silver(CHAT, 30, 2)
        before = Event("موجودی", 30)
        await handler(before)
        await handler(Event("1", 30))          # برنز ➜ نقره
        after = Event("موجودی", 30)
        await handler(after)
        return bot, before, after

    bot, before, after = asyncio.run(scenario())
    check("قبل: ۱۹۳ نمایش داده شد", before.said("💎 ارزش کل: ۱۹۳"),
          f"-> {before.replies}")
    check("بعد: موجودی ۷۳ و ۱۴ شد",
          after.said("🥉 برنز: ۷۳") and after.said("🥈 نقره: ۱۴"))
    check("بعد: ارزش کل بالا رفت",
          after.said("💎 ارزش کل: ۲۱۳"), f"-> {after.replies}")
    check("عدد نمایش‌داده‌شده با دیتابیس یکی است",
          agrees(economy.get_balance(CHAT, 30)))
    check("هیچ خطایی نیست", not bot.logger.errors)
    eco_handler.reset_all()


def test_silver_to_gold_through_handler():
    print("\n### 🔌 تبدیل نقره به طلا از هندلر")
    fresh()

    async def scenario():
        bot, handler = await build_handler()
        economy.add_silver(CHAT, 31, 100)
        await handler(Event("موجودی", 31))
        convert = Event("2", 31)
        await handler(convert)
        after = Event("موجودی", 31)
        await handler(after)
        return convert, after

    convert, after = asyncio.run(scenario())
    check("پیام تبدیل ارزش تازه را می‌گوید",
          convert.said("۱,۳۰۰"), f"-> {convert.replies}")
    check("منوی موجودی هم ۱٬۳۰۰ نشان می‌دهد",
          after.said("💎 ارزش کل: ۱,۳۰۰"), f"-> {after.replies}")
    check("با دیتابیس هم‌خوان است",
          agrees(economy.get_balance(CHAT, 31)))
    eco_handler.reset_all()


def test_all_menus_show_same_total():
    print("\n### 🔌 همهٔ منوها یک عدد نشان می‌دهند")
    fresh()
    economy.add_bronze(CHAT, 32, 173)
    economy.add_silver(CHAT, 32, 2)
    economy.convert_bronze(CHAT, 32)
    truth = economy.get_balance(CHAT, 32)["total_coin_value"]

    from economy.ui import profile_menu, shop_menu
    from economy.ui.formatting import fa
    wanted = f"💎 ارزش کل: {fa(truth)}"

    menu, _ = balance_menu.render_menu(CHAT, 32)
    check("منوی موجودی", wanted in menu, f"-> want {wanted}")
    only, _ = balance_menu.render_balance_only(CHAT, 32)
    check("نمایش موجودی تنها", wanted in only)
    shop, _ = shop_menu.render_menu(CHAT, 32)
    check("منوی فروشگاه", wanted in shop)

    profiles.register(CHAT, 32, name="علی", city="شیراز", age=20)
    card, _ = profile_menu.render_card(CHAT, 32, None)
    from economy.ui.formatting import fa_plain
    check("کارت پروفایل موجودی درست دارد",
          f"🥉 برنز: {fa_plain(73)}" in card
          and f"🥈 نقره: {fa_plain(14)}" in card, f"-> {card[:200]}")


# ===========================================================================
def main():
    test_formula_is_coins_only()
    test_history_does_not_affect_total()
    test_total_is_not_stored_value()
    test_settings_change_reflects_immediately()
    test_empty_wallet()
    test_convert_bronze_to_silver()
    test_convert_bronze_changes_total_when_rates_differ()
    test_convert_silver_to_gold()
    test_purchase_updates_total()
    test_shop_purchase_updates_total()
    test_transfer_updates_both_sides()
    test_game_reward_updates_total()
    test_spend_updates_total()
    test_daily_reward_updates_total()
    test_every_operation_agrees()
    test_profile_and_ranking_agree()
    test_conversion_through_handler()
    test_silver_to_gold_through_handler()
    test_all_menus_show_same_total()
    print(f"\npassed={PASSED} failed={FAILED}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
