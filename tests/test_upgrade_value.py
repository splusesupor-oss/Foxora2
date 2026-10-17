"""⬆️ تبدیل سکه یک «ارتقا» است و باید ارزش کل را بالا ببرد.

پیش‌تر «۱۰۰ برنز ➜ ۱۰ نقره» ارزش‌خنثی بود (۱۰۰ = ۱۰۰) و ارزش کل تکان
نمی‌خورد. حالا نرخ «۱۰۰ برنز ➜ ۱۲ نقره» است، پس هر تبدیل سود دارد.

همچنین کاربرانی که *پیش از* این تغییر تبدیل کرده بودند یک بار جبران
می‌شوند تا کسی با مقدار قدیمی نماند.

    python tests/test_upgrade_value.py
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
from economy import settings, upgrade_migration
from economy.transactions import ledger
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
    settings.SETTINGS_FILE = temp / "economy_settings.json"
    settings.reset_cache()
    eco_handler.reset_all()
    group_storage.activate_group(CHAT, "گروه تست")
    return temp


def value_of(balance):
    values = settings.coin_values()
    return (balance[economy.BRONZE] * values[economy.BRONZE]
            + balance[economy.SILVER] * values[economy.SILVER]
            + balance[economy.GOLD] * values[economy.GOLD])


def seed_legacy_conversion(chat_id, user_id, bronze_spent, silver_given,
                           leftover_bronze):
    """کاربری که با نرخ قدیمی تبدیل کرده است."""
    key = accounts.user_key(chat_id, user_id)
    with storage.transaction() as data:
        user = accounts._user(data, key)
        user[economy.BRONZE] = leftover_bronze
        user[economy.SILVER] = silver_given
        ledger.record(
            data, key, ledger.KIND_CONVERT,
            {economy.BRONZE: -bronze_spent, economy.SILVER: silver_given},
            note="نرخ قدیمی",
            balance_after=accounts._snapshot_balance(user),
        )
        accounts._refresh_total(data, user)
    return key


# ===========================================================================
# نرخ‌های ارتقا
# ===========================================================================
def test_conversion_rates_are_profitable():
    print("\n### ⬆️ نرخ تبدیل سودده است")
    fresh()
    config = settings.load()
    values = settings.coin_values()

    bronze_delta = (config["BronzeToSilverGain"] * values[economy.SILVER]
                    - config["BronzeToSilverCost"] * values[economy.BRONZE])
    silver_delta = (config["SilverToGoldGain"] * values[economy.GOLD]
                    - config["SilverToGoldCost"] * values[economy.SILVER])

    check("برنز ➜ نقره سود دارد", bronze_delta > 0, f"-> {bronze_delta}")
    check("سود برنز ➜ نقره = ۲۰", bronze_delta == 20, f"-> {bronze_delta}")
    check("نقره ➜ طلا سود دارد", silver_delta > 0, f"-> {silver_delta}")
    check("سود نقره ➜ طلا = ۳۰۰", silver_delta == 300, f"-> {silver_delta}")
    check("نرخ برنز ➜ نقره ۱۲ است",
          config["BronzeToSilverGain"] == 12,
          f"-> {config['BronzeToSilverGain']}")


def test_bronze_conversion_raises_total():
    print("\n### ⬆️ تبدیل برنز ارزش کل را بالا می‌برد")
    fresh()
    economy.add_bronze(CHAT, 1, 173)
    economy.add_silver(CHAT, 1, 2)
    before = economy.get_balance(CHAT, 1)
    check("قبل: ۱۹۳", before["total_coin_value"] == 193,
          f"-> {before['total_coin_value']}")

    returned = economy.convert_bronze(CHAT, 1)
    after = economy.get_balance(CHAT, 1)
    check("۱۰۰ برنز خرج شد", after[economy.BRONZE] == 73)
    check("۱۲ نقره گرفت", after[economy.SILVER] == 14,
          f"-> {after[economy.SILVER]}")
    check("ارزش کل بالا رفت",
          after["total_coin_value"] > before["total_coin_value"],
          f"{before['total_coin_value']} -> {after['total_coin_value']}")
    check("دقیقاً ۲۰ اضافه شد",
          after["total_coin_value"] - before["total_coin_value"] == 20,
          f"-> {after['total_coin_value']}")
    check("مقدار برگشتی با فرمول هم‌خوان است",
          returned["total_coin_value"] == value_of(returned))


def test_silver_conversion_raises_total():
    print("\n### ⬆️ تبدیل نقره ارزش کل را بالا می‌برد")
    fresh()
    economy.add_silver(CHAT, 2, 100)
    before = economy.get_balance(CHAT, 2)["total_coin_value"]
    economy.convert_silver(CHAT, 2)
    after = economy.get_balance(CHAT, 2)
    check("قبل: ۱۰۰۰", before == 1000)
    check("بعد: ۱۳۰۰", after["total_coin_value"] == 1300,
          f"-> {after['total_coin_value']}")
    check("۳۰۰ اضافه شد", after["total_coin_value"] - before == 300)
    check("با فرمول هم‌خوان است",
          after["total_coin_value"] == value_of(after))


def test_repeated_conversions_keep_gaining():
    print("\n### ⬆️ هر تبدیل باز هم سود می‌دهد")
    fresh()
    economy.add_bronze(CHAT, 3, 500)
    totals = [economy.get_balance(CHAT, 3)["total_coin_value"]]
    for _ in range(5):
        economy.convert_bronze(CHAT, 3)
        totals.append(economy.get_balance(CHAT, 3)["total_coin_value"])
    check("ارزش کل پیوسته بالا می‌رود",
          all(b > a for a, b in zip(totals, totals[1:])), f"-> {totals}")
    check("مجموع سود = ۵ × ۲۰", totals[-1] - totals[0] == 100,
          f"-> {totals}")


# ===========================================================================
# بقیهٔ عملیات‌ها هم ارزش را درست نگه می‌دارند
# ===========================================================================
def test_rewards_and_daily_raise_total():
    print("\n### ⬆️ جایزه و پاداش روزانه")
    fresh()
    start = economy.get_balance(CHAT, 4)["total_coin_value"]
    economy.award_game(CHAT, 4, "flag", reference="f1")
    after_game = economy.get_balance(CHAT, 4)
    check("جایزهٔ بازی ارزش را بالا برد",
          after_game["total_coin_value"] > start)
    check("با فرمول هم‌خوان است",
          after_game["total_coin_value"] == value_of(after_game))

    granted, balance, wait = economy.claim_daily(CHAT, 4)
    after_daily = economy.get_balance(CHAT, 4)
    check("پاداش روزانه داده شد", granted)
    check("پاداش روزانه ارزش را بالا برد",
          after_daily["total_coin_value"]
          > after_game["total_coin_value"])
    check("با فرمول هم‌خوان است",
          after_daily["total_coin_value"] == value_of(after_daily))


def test_transfer_conserves_value():
    """انتقال جابه‌جایی است نه ارتقا؛ نباید ارزش بسازد."""
    print("\n### ⬆️ انتقال ارزش نمی‌سازد")
    fresh()
    economy.add_bronze(CHAT, 5, 300)
    total_before = (economy.get_balance(CHAT, 5)["total_coin_value"]
                    + economy.get_balance(CHAT, 6)["total_coin_value"])
    economy.transfer(CHAT, 5, 6, economy.BRONZE, 120)
    sender = economy.get_balance(CHAT, 5)
    receiver = economy.get_balance(CHAT, 6)
    check("مجموع ارزش دو طرف حفظ شد",
          sender["total_coin_value"] + receiver["total_coin_value"]
          == total_before,
          f"-> {sender['total_coin_value']}+"
          f"{receiver['total_coin_value']} != {total_before}")
    check("گیرنده ارزش گرفت", receiver["total_coin_value"] == 120)
    check("هر دو با فرمول هم‌خوان‌اند",
          sender["total_coin_value"] == value_of(sender)
          and receiver["total_coin_value"] == value_of(receiver))


def test_purchase_lowers_total():
    print("\n### ⬆️ خرید ارزش را کم می‌کند")
    fresh()
    economy.add_silver(CHAT, 7, 500)
    before = economy.get_balance(CHAT, 7)["total_coin_value"]
    economy.profiles.buy(CHAT, 7, "badge_fox")
    after = economy.get_balance(CHAT, 7)
    check("ارزش کل کم شد", after["total_coin_value"] < before)
    check("با فرمول هم‌خوان است",
          after["total_coin_value"] == value_of(after))


# ===========================================================================
# جبران کاربران قدیمی
# ===========================================================================
def test_legacy_user_is_compensated():
    print("\n### 🔁 کاربر قدیمی جبران می‌شود")
    fresh()
    seed_legacy_conversion(CHAT, 10, bronze_spent=100, silver_given=10,
                           leftover_bronze=200)
    before = economy.get_balance(CHAT, 10)
    check("قبل: ۱۰ نقره", before[economy.SILVER] == 10)
    check("قبل: ارزش ۳۰۰", before["total_coin_value"] == 300)

    pending = upgrade_migration.preview()
    check("گزارش بدهی نشان می‌دهد", bool(pending), f"-> {pending}")

    paid = upgrade_migration.run()
    after = economy.get_balance(CHAT, 10)
    check("جبران انجام شد", bool(paid), f"-> {paid}")
    check("نقره به ۱۲ رسید", after[economy.SILVER] == 12,
          f"-> {after[economy.SILVER]}")
    check("ارزش کل ۲۰ بالا رفت", after["total_coin_value"] == 320,
          f"-> {after['total_coin_value']}")
    check("با فرمول هم‌خوان است",
          after["total_coin_value"] == value_of(after))


def test_legacy_multi_conversion_compensated():
    print("\n### 🔁 چند تبدیل قدیمی با هم")
    fresh()
    seed_legacy_conversion(CHAT, 11, bronze_spent=300, silver_given=30,
                           leftover_bronze=0)
    upgrade_migration.run()
    after = economy.get_balance(CHAT, 11)
    check("۳ تبدیل ⇒ ۳۶ نقره", after[economy.SILVER] == 36,
          f"-> {after[economy.SILVER]}")
    check("ارزش کل ۶۰ بالا رفت", after["total_coin_value"] == 360,
          f"-> {after['total_coin_value']}")


def test_migration_is_idempotent():
    print("\n### 🔁 اجرای دوباره چیزی اضافه نمی‌کند")
    fresh()
    seed_legacy_conversion(CHAT, 12, bronze_spent=100, silver_given=10,
                           leftover_bronze=50)
    upgrade_migration.run()
    first = economy.get_balance(CHAT, 12)
    for _ in range(3):
        again = upgrade_migration.run()
        check("اجرای دوباره چیزی پرداخت نمی‌کند", not again, f"-> {again}")
    check("موجودی دست‌نخورده ماند",
          economy.get_balance(CHAT, 12) == first)


def test_new_rate_user_not_double_paid():
    print("\n### 🔁 کاربر با نرخ جدید دوباره پول نمی‌گیرد")
    fresh()
    economy.add_bronze(CHAT, 13, 200)
    economy.convert_bronze(CHAT, 13)
    before = economy.get_balance(CHAT, 13)
    check("با نرخ جدید ۱۲ نقره گرفت", before[economy.SILVER] == 12)

    paid = upgrade_migration.run()
    after = economy.get_balance(CHAT, 13)
    check("جبرانی پرداخت نشد", not paid, f"-> {paid}")
    check("موجودی تغییر نکرد", before == after)


def test_untouched_user_unaffected():
    print("\n### 🔁 کاربری که تبدیل نکرده دست‌نخورده می‌ماند")
    fresh()
    economy.add_bronze(CHAT, 14, 77)
    before = economy.get_balance(CHAT, 14)
    upgrade_migration.run()
    check("موجودی تغییر نکرد", economy.get_balance(CHAT, 14) == before)
    check("گزارش خالی است", not upgrade_migration.preview())


def test_migration_records_history():
    print("\n### 🔁 جبران در تاریخچه ثبت می‌شود")
    fresh()
    seed_legacy_conversion(CHAT, 15, bronze_spent=100, silver_given=10,
                           leftover_bronze=0)
    upgrade_migration.run()
    history = economy.transaction_history(CHAT, 15)
    notes = [entry.get("note") for entry in history]
    check("رکورد جبران ثبت شد",
          "جبران تبدیل با نرخ قدیمی" in notes, f"-> {notes}")


def test_gold_conversion_legacy_compensated():
    print("\n### 🔁 تبدیل قدیمی نقره ➜ طلا")
    fresh()
    key = accounts.user_key(CHAT, 16)
    with storage.transaction() as data:
        user = accounts._user(data, key)
        user[economy.SILVER] = 0
        user[economy.GOLD] = 8          # با نرخ فرضی کمتر
        ledger.record(
            data, key, ledger.KIND_CONVERT,
            {economy.SILVER: -70, economy.GOLD: 8},
            note="نرخ قدیمی",
            balance_after=accounts._snapshot_balance(user))
        accounts._refresh_total(data, user)

    upgrade_migration.run()
    after = economy.get_balance(CHAT, 16)
    check("طلا به ۱۰ رسید", after[economy.GOLD] == 10,
          f"-> {after[economy.GOLD]}")
    check("با فرمول هم‌خوان است",
          after["total_coin_value"] == value_of(after))


def test_migration_never_takes_coins_away():
    print("\n### 🔁 هرگز سکه پس گرفته نمی‌شود")
    fresh()
    # کاربری که سخاوتمندانه بیشتر گرفته بود.
    key = accounts.user_key(CHAT, 17)
    with storage.transaction() as data:
        user = accounts._user(data, key)
        user[economy.SILVER] = 50
        ledger.record(
            data, key, ledger.KIND_CONVERT,
            {economy.BRONZE: -100, economy.SILVER: 50},
            note="سخاوتمندانه",
            balance_after=accounts._snapshot_balance(user))
        accounts._refresh_total(data, user)

    before = economy.get_balance(CHAT, 17)
    upgrade_migration.run()
    after = economy.get_balance(CHAT, 17)
    check("سکه‌ای کم نشد",
          after[economy.SILVER] >= before[economy.SILVER],
          f"{before[economy.SILVER]} -> {after[economy.SILVER]}")
    check("موجودی دست‌نخورده ماند", after == before)


# ===========================================================================
# تنظیمات کهنه روی دستگاه
# ===========================================================================
def test_old_settings_file_is_upgraded():
    """فایل تنظیمات gitignore است؛ نباید دستگاه با نرخ کهنه بماند."""
    print("\n### ⚙️ فایل تنظیمات قدیمی به‌روز می‌شود")
    import json
    temp = fresh()
    stale = dict(settings.DEFAULTS)
    stale["BronzeToSilverGain"] = 10          # نرخ کهنه
    stale.pop("SettingsVersion", None)        # نسخه ندارد
    settings.SETTINGS_FILE.write_text(
        json.dumps(stale, ensure_ascii=False), encoding="utf-8")
    settings.reset_cache()

    config = settings.load()
    check("نرخ کهنه به ۱۲ ارتقا یافت",
          config["BronzeToSilverGain"] == 12,
          f"-> {config['BronzeToSilverGain']}")
    check("نسخه ثبت شد", config["SettingsVersion"] >= 2)


def test_admin_overrides_are_preserved():
    print("\n### ⚙️ تنظیم دستی مدیر حفظ می‌شود")
    import json
    fresh()
    custom = dict(settings.DEFAULTS)
    custom["DailyRewardBronze"] = 99
    settings.SETTINGS_FILE.write_text(
        json.dumps(custom, ensure_ascii=False), encoding="utf-8")
    settings.reset_cache()
    check("مقدار دلخواه مدیر دست‌نخورده ماند",
          settings.load()["DailyRewardBronze"] == 99)


# ===========================================================================
# مسیر واقعی
# ===========================================================================
def test_migration_runs_on_startup():
    print("\n### 🔌 جبران هنگام راه‌اندازی خودکار انجام می‌شود")
    fresh()
    seed_legacy_conversion(CHAT, 20, bronze_spent=100, silver_given=10,
                           leftover_bronze=100)
    before = economy.get_balance(CHAT, 20)

    async def scenario():
        bot, handler = await build_handler()
        event = Event("موجودی", 20)
        await handler(event)
        return bot, event

    bot, event = asyncio.run(scenario())
    after = economy.get_balance(CHAT, 20)
    check("بدون دخالت دستی جبران شد",
          after[economy.SILVER] > before[economy.SILVER],
          f"{before[economy.SILVER]} -> {after[economy.SILVER]}")
    check("جبران لاگ شد", bot.logger.has("UPGRADE MIGRATION"))
    check("منوی موجودی عدد تازه را نشان می‌دهد",
          event.said("🥈 نقره: ۱۲"), f"-> {event.replies}")
    check("هیچ خطایی نیست", not bot.logger.errors,
          f"-> {[e[:100] for e in bot.logger.errors][:1]}")
    eco_handler.reset_all()


def test_conversion_through_handler_gains():
    print("\n### 🔌 تبدیل از منو سود می‌دهد")
    fresh()

    async def scenario():
        bot, handler = await build_handler()
        economy.add_bronze(CHAT, 21, 173)
        economy.add_silver(CHAT, 21, 2)
        before = Event("موجودی", 21)
        await handler(before)
        convert = Event("1", 21)
        await handler(convert)
        after = Event("موجودی", 21)
        await handler(after)
        return before, convert, after

    before, convert, after = asyncio.run(scenario())
    check("قبل ۱۹۳ بود", before.said("💎 ارزش کل: ۱۹۳"),
          f"-> {before.replies}")
    check("منو نرخ ۱۲ را نشان می‌دهد",
          before.said("تبدیل برنز به نقره (۱۰۰ ➜ ۱۲)"),
          f"-> {before.replies}")
    check("پیام تبدیل ۱۲ نقره می‌گوید", convert.said("۱۲ نقره"),
          f"-> {convert.replies}")
    check("بعد ۲۱۳ شد", after.said("💎 ارزش کل: ۲۱۳"),
          f"-> {after.replies}")
    eco_handler.reset_all()


def test_everyone_consistent_after_migration():
    print("\n### 🔁 هیچ‌کس با مقدار قدیمی نمی‌ماند")
    fresh()
    seed_legacy_conversion(CHAT, 30, 100, 10, 0)
    seed_legacy_conversion(CHAT, 31, 200, 20, 5)
    economy.add_bronze(CHAT, 32, 40)
    economy.add_bronze(CHAT, 33, 200)
    economy.convert_bronze(CHAT, 33)

    upgrade_migration.run()
    for user_id in (30, 31, 32, 33):
        balance = economy.get_balance(CHAT, user_id)
        check(f"کاربر {user_id} ارزش درست دارد",
              balance["total_coin_value"] == value_of(balance),
              f"-> {balance}")
        profile = economy.get_profile(CHAT, user_id)
        check(f"پروفایل کاربر {user_id} هم‌خوان است",
              profile["total_coin_value"] == balance["total_coin_value"])
    check("گزارش بدهی خالی شد", not upgrade_migration.preview())


# ===========================================================================
def main():
    test_conversion_rates_are_profitable()
    test_bronze_conversion_raises_total()
    test_silver_conversion_raises_total()
    test_repeated_conversions_keep_gaining()
    test_rewards_and_daily_raise_total()
    test_transfer_conserves_value()
    test_purchase_lowers_total()
    test_legacy_user_is_compensated()
    test_legacy_multi_conversion_compensated()
    test_migration_is_idempotent()
    test_new_rate_user_not_double_paid()
    test_untouched_user_unaffected()
    test_migration_records_history()
    test_gold_conversion_legacy_compensated()
    test_migration_never_takes_coins_away()
    test_old_settings_file_is_upgraded()
    test_admin_overrides_are_preserved()
    test_migration_runs_on_startup()
    test_conversion_through_handler_gains()
    test_everyone_consistent_after_migration()
    print(f"\npassed={PASSED} failed={FAILED}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
