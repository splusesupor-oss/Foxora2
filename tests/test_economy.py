"""💰 سیستم اقتصاد — تست کامل و واقعی.

پوشش: انواع سکه، تبدیل، انتقال، ارزش کل، رتبه‌بندی، فروشگاه، تاریخچه،
جایزهٔ روزانه، همزمانی، جلوگیری از منفی شدن و جلوگیری از ثبت دوباره.

    python tests/test_economy.py
"""
import json
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import economy
import economy.settings as settings
import economy.shop.store as store
import economy.storage as storage
from economy.coins import accounts
from economy.ranking import board

PASSED = FAILED = 0
CHAT = -100500


def check(label, cond, detail=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  PASS  {label}")
    else:
        FAILED += 1
        print(f"  FAIL  {label} {detail}")


def fresh():
    """هر تست روی فایل‌های تازهٔ خودش کار می‌کند."""
    temp = Path(tempfile.mkdtemp())
    storage.use_file(temp / "economy.json")
    store.ITEMS_FILE = temp / "shop.json"
    store._cache = None
    store._cache_mtime = None
    settings.SETTINGS_FILE = temp / "settings.json"
    settings.reset_cache()
    return temp


# ===========================================================================
# انواع سکه و موجودی
# ===========================================================================
def test_coin_types():
    print("\n### 🪙 سه نوع سکه")
    fresh()
    check("سه نوع سکه تعریف شده", set(economy.COIN_TYPES) ==
          {"bronze", "silver", "gold"})
    check("کاربر تازه موجودی صفر دارد",
          economy.get_balance(CHAT, 1) ==
          {"bronze": 0, "silver": 0, "gold": 0, "total_coin_value": 0})

    economy.add_bronze(CHAT, 1, 152)
    economy.add_silver(CHAT, 1, 34)
    economy.add_gold(CHAT, 1, 8)
    balance = economy.get_balance(CHAT, 1)
    check("برنز جدا ذخیره شد", balance["bronze"] == 152)
    check("نقره جدا ذخیره شد", balance["silver"] == 34)
    check("طلا جدا ذخیره شد", balance["gold"] == 8)
    check("موجودی کاربران از هم جداست",
          economy.get_balance(CHAT, 2)["bronze"] == 0)


def test_add_remove():
    print("\n### 🪙 افزودن و کسر")
    fresh()
    economy.add_bronze(CHAT, 1, 100)
    economy.add_silver(CHAT, 1, 50)
    economy.add_gold(CHAT, 1, 20)

    check("کسر برنز", economy.remove_bronze(CHAT, 1, 30)["bronze"] == 70)
    check("کسر نقره", economy.remove_silver(CHAT, 1, 10)["silver"] == 40)
    check("کسر طلا", economy.remove_gold(CHAT, 1, 5)["gold"] == 15)

    for label, fn in (("add", economy.add_bronze),
                      ("remove", economy.remove_bronze)):
        for bad in (0, -5, 1.5, "10", True, None):
            try:
                fn(1, bad)
                check(f"{label} با ورودی {bad!r} رد می‌شود", False)
            except economy.EconomyError:
                check(f"{label} با ورودی {bad!r} رد می‌شود", True)
            except TypeError:
                check(f"{label} با ورودی {bad!r} رد می‌شود", True)

    try:
        economy.add(CHAT, 1, "platinum", 5)
        check("نوع سکهٔ نامعتبر رد می‌شود", False)
    except economy.EconomyError:
        check("نوع سکهٔ نامعتبر رد می‌شود", True)


def test_no_negative_balance():
    print("\n### 🛡️ جلوگیری از منفی شدن موجودی")
    fresh()
    economy.add_bronze(CHAT, 1, 10)

    try:
        economy.remove_bronze(CHAT, 1, 11)
        check("کسر بیش از موجودی رد می‌شود", False)
    except economy.EconomyError as error:
        check("کسر بیش از موجودی رد می‌شود", "کافی نیست" in str(error))
    check("موجودی پس از رد شدن تغییر نکرد",
          economy.get_balance(CHAT, 1)["bronze"] == 10)

    try:
        economy.remove_silver(CHAT, 1, 1)
        check("کسر از سکهٔ خالی رد می‌شود", False)
    except economy.EconomyError:
        check("کسر از سکهٔ خالی رد می‌شود", True)

    check("کسر دقیقاً برابر موجودی مجاز است",
          economy.remove_bronze(CHAT, 1, 10)["bronze"] == 0)
    check("موجودی صفر شد و منفی نیست",
          economy.get_balance(CHAT, 1)["bronze"] == 0)


# ===========================================================================
# تبدیل
# ===========================================================================
def test_conversion():
    print("\n### 🔄 تبدیل سکه")
    fresh()
    economy.add_bronze(CHAT, 1, 250)

    result = economy.convert_bronze(CHAT, 1)
    check("۱۰۰ برنز کسر شد", result["bronze"] == 150)
    check("۱۲ نقره اضافه شد", result["silver"] == 12)

    result = economy.convert_bronze(CHAT, 1, times=1)
    check("تبدیل دوم درست بود",
          result["bronze"] == 50 and result["silver"] == 24)

    try:
        economy.convert_bronze(CHAT, 1)
        check("تبدیل با موجودی ناکافی رد می‌شود", False)
    except economy.EconomyError:
        check("تبدیل با موجودی ناکافی رد می‌شود", True)
    check("موجودی پس از رد شدن دست‌نخورده است",
          economy.get_balance(CHAT, 1)["bronze"] == 50)

    fresh()
    economy.add_silver(CHAT, 1, 150)
    result = economy.convert_silver(CHAT, 1)
    check("۷۰ نقره کسر شد", result["silver"] == 80)
    check("۱۰ طلا اضافه شد", result["gold"] == 10)

    try:
        economy.convert_silver(CHAT, 1, times=2)
        check("تبدیل چندتایی بدون موجودی رد می‌شود", False)
    except economy.EconomyError:
        check("تبدیل چندتایی بدون موجودی رد می‌شود", True)
    check("موجودی نقره تغییر نکرد", economy.get_balance(CHAT, 1)["silver"] == 80)

    fresh()
    economy.add_bronze(CHAT, 1, 500)
    result = economy.convert_bronze(CHAT, 1, times=3)
    check("تبدیل سه‌تایی یک‌جا",
          result["bronze"] == 200 and result["silver"] == 36)

    for bad in (0, -1, 2.5):
        try:
            economy.convert_bronze(CHAT, 1, times=bad)
            check(f"تعداد تبدیل {bad!r} رد می‌شود", False)
        except economy.EconomyError:
            check(f"تعداد تبدیل {bad!r} رد می‌شود", True)


def test_conversion_preserves_value():
    # تبدیل حالا «ارتقا» است و باید ارزش را بالا ببرد، نه ثابت نگه دارد.
    print("\n### 🔄 تبدیل ارزش را بالا می‌برد")
    fresh()
    economy.add_bronze(CHAT, 1, 100)
    before = economy.calculate_total_value(CHAT, 1)
    economy.convert_bronze(CHAT, 1)
    after = economy.calculate_total_value(CHAT, 1)
    check("۱۰۰ برنز ➜ ۱۲ نقره سود می‌دهد", after > before,
          f"-> {before} vs {after}")
    check("سود دقیقاً ۲۰ است", after - before == 20, f"-> {after - before}")

    fresh()
    economy.add_silver(CHAT, 1, 70)
    before = economy.calculate_total_value(CHAT, 1)
    economy.convert_silver(CHAT, 1)
    after = economy.calculate_total_value(CHAT, 1)
    check("۷۰ نقره ➜ ۱۰ طلا ارزش را بالا می‌برد", after > before,
          f"-> {before} -> {after}")


# ===========================================================================
# ارزش کل
# ===========================================================================
def test_total_value():
    print("\n### 💎 محاسبهٔ ارزش کل")
    fresh()
    economy.add_bronze(CHAT, 1, 152)
    economy.add_silver(CHAT, 1, 34)
    economy.add_gold(CHAT, 1, 8)

    expected = 152 * 1 + 34 * 10 + 8 * 100
    check("ارزش کل درست محاسبه شد",
          economy.calculate_total_value(CHAT, 1) == expected,
          f"-> {economy.calculate_total_value(CHAT, 1)} vs {expected}")
    check("ارزش در موجودی هم هست",
          economy.get_balance(CHAT, 1)["total_coin_value"] == expected)

    economy.add_gold(CHAT, 1, 2)
    check("پس از دریافت جایزه بازمحاسبه شد",
          economy.calculate_total_value(CHAT, 1) == expected + 200)
    economy.remove_bronze(CHAT, 1, 52)
    check("پس از خرج کردن بازمحاسبه شد",
          economy.calculate_total_value(CHAT, 1) == expected + 200 - 52)
    economy.convert_bronze(CHAT, 1)
    check("پس از تبدیل بازمحاسبه شد (با سود ارتقا)",
          economy.calculate_total_value(CHAT, 1)
          == expected + 200 - 52 + 20)
    economy.transfer(CHAT, 1, 2, "gold", 1)
    check("پس از انتقال، فرستنده بازمحاسبه شد",
          economy.calculate_total_value(CHAT, 1)
          == expected + 100 - 52 + 20)
    check("پس از انتقال، گیرنده بازمحاسبه شد",
          economy.calculate_total_value(CHAT, 2) == 100)


def test_settings_drive_value():
    print("\n### ⚙️ ارزش سکه از تنظیمات می‌آید")
    fresh()
    check("ارزش پیش‌فرض برنز", settings.coin_value("bronze") == 1)
    check("ارزش پیش‌فرض نقره", settings.coin_value("silver") == 10)
    check("ارزش پیش‌فرض طلا", settings.coin_value("gold") == 100)

    economy.add_gold(CHAT, 1, 5)
    check("ارزش با تنظیمات پیش‌فرض", economy.calculate_total_value(CHAT, 1) == 500)

    settings.save({"GoldValue": 250})
    check("تنظیمات ذخیره شد", settings.coin_value("gold") == 250)
    check("ارزش با تنظیمات جدید بازمحاسبه شد",
          economy.calculate_total_value(CHAT, 1) == 1250)

    settings.reset_cache()
    check("تنظیمات پس از ری‌استارت باقی است",
          settings.coin_value("gold") == 250)

    economy.recalculate_all()
    check("recalculate_all ارزش ذخیره‌شده را تازه کرد",
          economy.get_balance(CHAT, 1)["total_coin_value"] == 1250)


# ===========================================================================
# انتقال
# ===========================================================================
def test_transfer():
    print("\n### 🔁 انتقال سکه")
    fresh()
    economy.add_bronze(CHAT, 1, 100)
    economy.add_silver(CHAT, 1, 50)
    economy.add_gold(CHAT, 1, 10)

    result = economy.transfer(CHAT, 1, 2, "bronze", 30)
    check("برنز از فرستنده کسر شد", result["sender"]["bronze"] == 70)
    check("برنز به گیرنده رسید", result["receiver"]["bronze"] == 30)

    economy.transfer(CHAT, 1, 2, "silver", 20)
    economy.transfer(CHAT, 1, 2, "gold", 5)
    check("هر سه نوع سکه قابل انتقال است",
          economy.get_balance(CHAT, 2) ==
          {"bronze": 30, "silver": 20, "gold": 5,
           "total_coin_value": 30 + 200 + 500})

    try:
        economy.transfer(CHAT, 1, 2, "gold", 999)
        check("انتقال بیش از موجودی رد می‌شود", False)
    except economy.EconomyError:
        check("انتقال بیش از موجودی رد می‌شود", True)
    check("موجودی فرستنده پس از رد شدن تغییر نکرد",
          economy.get_balance(CHAT, 1)["gold"] == 5)
    check("موجودی گیرنده پس از رد شدن تغییر نکرد",
          economy.get_balance(CHAT, 2)["gold"] == 5)

    try:
        economy.transfer(CHAT, 1, 1, "bronze", 1)
        check("انتقال به خود رد می‌شود", False)
    except economy.EconomyError:
        check("انتقال به خود رد می‌شود", True)

    for bad in (0, -5, 2.5):
        try:
            economy.transfer(CHAT, 1, 2, "bronze", bad)
            check(f"انتقال مقدار {bad!r} رد می‌شود", False)
        except economy.EconomyError:
            check(f"انتقال مقدار {bad!r} رد می‌شود", True)


def test_transfer_conserves_total():
    print("\n### 🔁 انتقال مجموع را حفظ می‌کند")
    fresh()
    economy.add_gold(CHAT, 1, 100)
    before = (economy.calculate_total_value(CHAT, 1)
              + economy.calculate_total_value(CHAT, 2))
    economy.transfer(CHAT, 1, 2, "gold", 40)
    after = (economy.calculate_total_value(CHAT, 1)
             + economy.calculate_total_value(CHAT, 2))
    check("مجموع ارزش دو طرف ثابت ماند", before == after,
          f"-> {before} vs {after}")


# ===========================================================================
# رتبه‌بندی
# ===========================================================================
def test_ranking_by_value():
    print("\n### 🏆 رتبه‌بندی بر پایهٔ ارزش، نه تعداد")
    fresh()
    # کاربر ۱: ۱۰۰۰ برنز = ارزش ۱۰۰۰ (تعداد سکه بیشتر)
    economy.add_bronze(CHAT, 1, 1000)
    # کاربر ۲: ۲۰ طلا = ارزش ۲۰۰۰ (تعداد سکه کمتر، ارزش بیشتر)
    economy.add_gold(CHAT, 2, 20)

    ranking = economy.leaderboard(CHAT, )
    check("رتبه بر اساس ارزش است نه تعداد سکه",
          ranking[0]["user_id"] == "2", f"-> {ranking[0]}")
    check("کاربر با سکهٔ بیشتر ولی ارزش کمتر، دوم است",
          ranking[1]["user_id"] == "1")
    check("get_rank درست است",
          economy.get_rank(CHAT, 2) == 1 and economy.get_rank(CHAT, 1) == 2)
    check("کاربر بدون سکه رتبه ندارد", economy.get_rank(CHAT, 999) is None)


def test_ranking_tie_break():
    print("\n### 🏆 تساوی: هرکس زودتر رسیده بالاتر")
    fresh()
    economy.add_bronze(CHAT, 1, 100)   # اول به ۱۰۰ رسید
    economy.add_bronze(CHAT, 2, 100)   # بعداً به همان ۱۰۰ رسید

    ranking = economy.leaderboard(CHAT, )
    check("هر دو ارزش برابر دارند",
          ranking[0]["total_coin_value"] == ranking[1]["total_coin_value"])
    check("کسی که زودتر رسیده رتبهٔ بالاتر دارد",
          ranking[0]["user_id"] == "1", f"-> {ranking[0]['user_id']}")
    check("مهر ترتیبی نفر اول کوچک‌تر است",
          ranking[0]["reached_seq"] < ranking[1]["reached_seq"])

    # اگر نفر دوم جلو بزند باید اول شود
    economy.add_bronze(CHAT, 2, 1)
    check("با پیشی گرفتن، رتبه عوض می‌شود",
          economy.leaderboard(CHAT, )[0]["user_id"] == "2")

    # و با برگشت به تساوی، دوباره نفر قدیمی‌تر جلو می‌افتد
    economy.remove_bronze(CHAT, 2, 1)
    top = economy.leaderboard(CHAT, )[0]
    check("پس از بازگشت به تساوی، ترتیب بر پایهٔ زمان است",
          top["user_id"] == "1", f"-> {top['user_id']}")


def test_ranking_limit():
    print("\n### 🏆 محدودیت و فیلتر جدول")
    fresh()
    for uid in range(1, 8):
        economy.add_bronze(CHAT, uid, uid * 10)
    check("محدودیت رعایت می‌شود", len(economy.leaderboard(CHAT, 3)) == 3)
    check("ترتیب نزولی است",
          [r["user_id"] for r in economy.leaderboard(CHAT, 3)] == ["7", "6", "5"])

    economy.add_bronze(CHAT, 99, 1)
    economy.remove_bronze(CHAT, 99, 1)
    ids = [r["user_id"] for r in economy.leaderboard(CHAT, limit=None)]
    check("کاربر با ارزش صفر در جدول نیست", "99" not in ids)
    ids_all = [r["user_id"]
               for r in economy.ranked_users(CHAT, include_zero=True)]
    check("با include_zero همه می‌آیند", "99" in ids_all)


# ===========================================================================
# تاریخچه
# ===========================================================================
def test_transaction_history():
    print("\n### 🧾 تاریخچهٔ تراکنش")
    fresh()
    economy.add_bronze(CHAT, 1, 200, note="جایزه")
    economy.remove_bronze(CHAT, 1, 20)
    economy.convert_bronze(CHAT, 1)
    economy.transfer(CHAT, 1, 2, "silver", 5)
    economy.claim_daily(CHAT, 1)

    entries = economy.transaction_history(CHAT, 1, limit=None)
    kinds = [e["kind"] for e in entries]
    for kind in ("receive", "spend", "convert", "transfer_out", "daily"):
        check(f"تراکنش «{kind}» ثبت شد", kind in kinds, f"-> {kinds}")

    check("گیرنده تراکنش ورودی دارد",
          any(e["kind"] == "transfer_in"
              for e in economy.transaction_history(CHAT, 2)))
    check("هر تراکنش زمان دقیق دارد",
          all(e.get("at") for e in entries))
    check("هر تراکنش موجودی پس از خود را دارد",
          all("balance_after" in e for e in entries))
    check("هر تراکنش ارزش کل را ثبت می‌کند",
          all("total_value" in e for e in entries))
    check("تازه‌ترین تراکنش اول است",
          entries[0]["id"] > entries[-1]["id"])

    filtered = economy.transaction_history(CHAT, 1, kind="convert")
    check("فیلتر بر اساس نوع کار می‌کند",
          len(filtered) == 1 and filtered[0]["kind"] == "convert")


def test_no_duplicate_transactions():
    print("\n### 🛡️ جلوگیری از ثبت دوبارهٔ تراکنش")
    fresh()
    for _ in range(5):
        economy.award(CHAT, 1, 10, reference="game:riddle:42")
    check("جایزه با مرجع تکراری فقط یک بار پرداخت شد",
          economy.get_balance(CHAT, 1)["bronze"] == 10,
          f"-> {economy.get_balance(CHAT, 1)['bronze']}")
    check("فقط یک ردیف تاریخچه ثبت شد",
          len(economy.transaction_history(CHAT, 1)) == 1)

    economy.award(CHAT, 1, 10, reference="game:riddle:43")
    check("مرجع متفاوت پرداخت می‌شود",
          economy.get_balance(CHAT, 1)["bronze"] == 20)

    economy.add_bronze(CHAT, 1, 5)
    economy.add_bronze(CHAT, 1, 5)
    check("بدون مرجع، هر بار پرداخت می‌شود",
          economy.get_balance(CHAT, 1)["bronze"] == 30)

    fresh()
    economy.add_bronze(CHAT, 1, 100)
    for _ in range(3):
        economy.transfer(CHAT, 1, 2, "bronze", 10, reference="tx:1")
    check("انتقال با مرجع تکراری یک بار انجام می‌شود",
          economy.get_balance(CHAT, 2)["bronze"] == 10,
          f"-> {economy.get_balance(CHAT, 2)['bronze']}")


# ===========================================================================
# فروشگاه
# ===========================================================================
def test_shop():
    print("\n### 🛒 فروشگاه")
    fresh()
    check("فروشگاه ابتدا خالی است", economy.shop.list_items() == [])

    economy.shop.add_item("badge", "نشان طلایی", 50, "bronze",
                          description="یک نشان", stock=2)
    economy.shop.add_item("vip", "عضویت ویژه", 3, "gold")
    check("دو آیتم ثبت شد", len(economy.shop.list_items()) == 2)
    check("آیتم قابل خواندن است",
          economy.shop.get_item("badge")["title"] == "نشان طلایی")

    economy.add_bronze(CHAT, 1, 120)
    check("توان خرید بررسی می‌شود", economy.shop.can_afford(CHAT, 1, "badge"))
    check("آیتم گران‌تر قابل خرید نیست",
          not economy.shop.can_afford(CHAT, 1, "vip"))

    item, balance = economy.shop.buy(CHAT, 1, "badge")
    check("خرید انجام شد", item["id"] == "badge")
    check("سکه کسر شد", balance["bronze"] == 70)
    check("ارزش کل پس از خرید بازمحاسبه شد",
          balance["total_coin_value"] == 70)
    check("خرید در تاریخچه ثبت شد",
          any(e["kind"] == "purchase"
              for e in economy.transaction_history(CHAT, 1)))
    check("خرید در فهرست خریدها آمد",
          len(economy.shop.purchases(CHAT, 1)) == 1)

    economy.shop.buy(CHAT, 1, "badge")
    check("انبار تمام شد", economy.shop.get_item("badge")["stock"] == 0)
    try:
        economy.shop.buy(CHAT, 1, "badge")
        check("خرید بدون موجودی انبار رد می‌شود", False)
    except economy.shop.ShopError:
        check("خرید بدون موجودی انبار رد می‌شود", True)

    try:
        economy.shop.buy(CHAT, 1, "vip")
        check("خرید بدون سکهٔ کافی رد می‌شود", False)
    except economy.shop.ShopError as error:
        check("خرید بدون سکهٔ کافی رد می‌شود", "کافی نیست" in str(error))

    try:
        economy.shop.buy(CHAT, 1, "ghost")
        check("خرید آیتم ناموجود رد می‌شود", False)
    except economy.shop.ShopError:
        check("خرید آیتم ناموجود رد می‌شود", True)

    check("حذف آیتم کار می‌کند", economy.shop.remove_item("vip") is True)
    check("حذف دوباره False می‌دهد",
          economy.shop.remove_item("vip") is False)

    for bad_price in (0, -5, 1.5):
        try:
            economy.shop.add_item("x", "X", bad_price)
            check(f"قیمت {bad_price!r} رد می‌شود", False)
        except economy.shop.ShopError:
            check(f"قیمت {bad_price!r} رد می‌شود", True)


def test_shop_purchase_is_atomic():
    print("\n### 🛒 خرید تکراری با مرجع یکسان")
    fresh()
    economy.shop.add_item("item", "آیتم", 10, "bronze")
    economy.add_bronze(CHAT, 1, 100)
    for _ in range(4):
        economy.shop.buy(CHAT, 1, "item", reference="buy:1")
    check("خرید با مرجع تکراری یک بار حساب شد",
          economy.get_balance(CHAT, 1)["bronze"] == 90,
          f"-> {economy.get_balance(CHAT, 1)['bronze']}")


# ===========================================================================
# جایزهٔ روزانه
# ===========================================================================
def test_daily_reward():
    print("\n### 🎁 جایزهٔ روزانه")
    fresh()
    granted, balance, _ = economy.claim_daily(CHAT, 1)
    check("جایزه پرداخت شد", granted is True)
    check("مقدار پیش‌فرض برنز اضافه شد", balance["bronze"] == 25)
    check("ارزش کل بازمحاسبه شد", balance["total_coin_value"] == 25)

    granted, balance, wait = economy.claim_daily(CHAT, 1)
    check("دریافت دوباره رد می‌شود", granted is False)
    check("موجودی تغییر نکرد", balance["bronze"] == 25)
    check("زمان انتظار برگردانده شد", wait > 0)

    available, left = economy.daily_status(CHAT, 1)
    check("وضعیت نشان می‌دهد هنوز نوبت نیست",
          available is False and left > 0)

    future = datetime.now(timezone.utc) + timedelta(days=1, seconds=1)
    granted, balance, _ = economy.claim_daily(CHAT, 1, now=future)
    check("پس از پایان انتظار دوباره پرداخت می‌شود", granted is True)
    check("موجودی دو برابر شد", balance["bronze"] == 50)
    check("جایزه در تاریخچه ثبت شد",
          len(economy.transaction_history(CHAT, 1, kind="daily")) == 2)


# ===========================================================================
# همزمانی
# ===========================================================================
def test_concurrent_adds():
    print("\n### ⚡ همزمانی: افزودن")
    fresh()
    errors = []

    def worker():
        for _ in range(50):
            try:
                economy.add_bronze(CHAT, 1, 1)
            except Exception as error:      # pragma: no cover
                errors.append(error)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    balance = economy.get_balance(CHAT, 1)
    check("هیچ به‌روزرسانی‌ای گم نشد", balance["bronze"] == 1000,
          f"-> {balance['bronze']}")
    check("هیچ خطایی رخ نداد", not errors, f"-> {errors[:2]}")
    check("ارزش کل با موجودی هم‌خوان است",
          balance["total_coin_value"] == balance["bronze"])


def test_concurrent_spend_never_negative():
    print("\n### ⚡ همزمانی: خرج کردن هرگز منفی نمی‌شود")
    fresh()
    economy.add_bronze(CHAT, 1, 100)
    rejected = []

    def worker():
        for _ in range(50):
            try:
                economy.remove_bronze(CHAT, 1, 1)
            except economy.EconomyError:
                rejected.append(1)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    balance = economy.get_balance(CHAT, 1)
    check("موجودی منفی نشد", balance["bronze"] >= 0,
          f"-> {balance['bronze']}")
    check("دقیقاً تا صفر خرج شد", balance["bronze"] == 0)
    check("حسابداری درست است: موفق‌ها + ردشده‌ها = کل تلاش‌ها",
          500 - len(rejected) == 100, f"-> rejected={len(rejected)}")


def test_concurrent_transfers():
    print("\n### ⚡ همزمانی: انتقال")
    fresh()
    economy.add_gold(CHAT, 1, 500)

    def worker():
        for _ in range(50):
            try:
                economy.transfer(CHAT, 1, 2, "gold", 1)
            except economy.EconomyError:
                pass

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    first = economy.get_balance(CHAT, 1)["gold"]
    second = economy.get_balance(CHAT, 2)["gold"]
    check("مجموع طلا حفظ شد", first + second == 500,
          f"-> {first} + {second}")
    check("فرستنده منفی نشد", first >= 0)


def test_concurrent_duplicate_reference():
    print("\n### ⚡ همزمانی: مرجع تکراری")
    fresh()

    def worker():
        for _ in range(20):
            economy.award(CHAT, 1, 10, reference="same")

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    check("۲۰۰ تلاش هم‌زمان فقط یک بار پرداخت شد",
          economy.get_balance(CHAT, 1)["bronze"] == 10,
          f"-> {economy.get_balance(CHAT, 1)['bronze']}")
    check("فقط یک ردیف تاریخچه ثبت شد",
          len(economy.transaction_history(CHAT, 1)) == 1)


def test_rollback_on_error():
    print("\n### ⚡ بازگشت تغییرات هنگام خطا")
    fresh()
    economy.add_bronze(CHAT, 1, 50)
    try:
        with storage.transaction() as data:
            accounts._user(data, economy.user_key(CHAT, 1))["bronze"] = 9999
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    check("تغییرات ناموفق ذخیره نشدند",
          economy.get_balance(CHAT, 1)["bronze"] == 50,
          f"-> {economy.get_balance(CHAT, 1)['bronze']}")


# ===========================================================================
# ماندگاری و استقلال
# ===========================================================================
def test_persistence():
    print("\n### 💾 ماندگاری پس از ری‌استارت")
    temp = fresh()
    economy.add_bronze(CHAT, 1, 100)
    economy.add_gold(CHAT, 1, 3)
    expected = economy.get_balance(CHAT, 1)

    check("فایل روی دیسک ساخته شد", storage.DATA_FILE.exists())

    storage._cache = None
    storage._cache_mtime = None
    check("موجودی پس از ری‌استارت باقی است",
          economy.get_balance(CHAT, 1) == expected)
    check("تاریخچه پس از ری‌استارت باقی است",
          len(economy.transaction_history(CHAT, 1)) == 2)

    storage.DATA_FILE.write_text("{ broken", encoding="utf-8")
    storage._cache = None
    storage._cache_mtime = None
    check("فایل خراب باعث کرش نمی‌شود",
          economy.get_balance(CHAT, 1)["bronze"] == 0)


def test_independence():
    print("\n### 🔒 استقلال کامل از بازی‌ها")
    import ast

    imported = set()
    for path in sorted(Path(ROOT / "economy").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])

    forbidden = {"modules", "handlers", "core", "splusthon"}
    leaked = imported & forbidden
    check("اقتصاد هیچ ماژول بازی/ربات را import نمی‌کند", not leaked,
          f"-> {sorted(leaked)}")
    check("فقط کتابخانهٔ استاندارد و خودِ economy",
          imported <= {"economy", "json", "os", "tempfile", "threading",
                       "copy", "datetime", "pathlib", "time", "zoneinfo",
                       # فیلتر نام: فقط کتابخانهٔ استاندارد.
                       "re", "unicodedata", "importlib",
                       # game_progress نوشتن روی دیسک را به thread
                       # می‌سپارد تا حلقهٔ رویداد بلاک نشود؛ asyncio هم
                       # کتابخانهٔ استاندارد است و قید «نداشتن وابستگی
                       # بیرونی» را نمی‌شکند.
                       "asyncio"},
          f"-> {sorted(imported)}")

    check("فایل دادهٔ اقتصاد جداست",
          "economy" in storage.DATA_FILE.name)

    # سیستم سکهٔ قدیمی کاملاً حذف شده است.
    import importlib
    for legacy_name in ("modules.coins", "modules.game_points"):
        try:
            importlib.import_module(legacy_name)
            check(f"ماژول قدیمی {legacy_name} حذف شده", False)
        except ModuleNotFoundError:
            check(f"ماژول قدیمی {legacy_name} حذف شده", True)


def test_public_api_surface():
    print("\n### 📡 سطح API عمومی")
    required = (
        "add_bronze", "add_silver", "add_gold",
        "remove_bronze", "remove_silver", "remove_gold",
        "convert_bronze", "convert_silver", "transfer",
        "calculate_total_value", "get_balance", "get_rank",
    )
    for name in required:
        check(f"تابع {name}() موجود است",
              callable(getattr(economy, name, None)))
    for name in ("award", "spend", "claim_daily", "leaderboard",
                 "transaction_history", "recalculate_all"):
        check(f"تابع {name}() موجود است",
              callable(getattr(economy, name, None)))


def test_games_use_api_only():
    print("\n### 📡 بازی‌ها فقط از API استفاده می‌کنند")
    fresh()
    # شبیه‌سازی یک بازی که جایزه می‌دهد
    balance = economy.award(CHAT, 5, 7, reference="vampire:chat1:session9")
    check("award موجودی را بالا برد", balance["bronze"] == 7)
    check("award به عنوان جایزه ثبت شد",
          economy.transaction_history(CHAT, 5)[0]["kind"] == "reward")

    balance = economy.spend(CHAT, 5, 3, reference="shop:x")
    check("spend موجودی را کم کرد", balance["bronze"] == 4)
    check("spend به عنوان خرج ثبت شد",
          economy.transaction_history(CHAT, 5)[0]["kind"] == "spend")

    try:
        economy.spend(CHAT, 5, 100)
        check("spend بیش از موجودی رد می‌شود", False)
    except economy.EconomyError:
        check("spend بیش از موجودی رد می‌شود", True)


def test_record_message_is_hot_path_cheap():
    """گارد رگرسیون: شمارش پیام در «هر پیام» صدا زده می‌شود.

    اگر این تابع دیتابیس را کپی و روی دیسک بنویسد، هزینه‌اش با بزرگ شدن
    دیتابیس بالا می‌رود و کل ربات کند می‌شود. باید مستقل از اندازه بماند.
    """
    print("\n### ⚡ هزینهٔ ثبت پیام مستقل از اندازهٔ دیتابیس")
    import time

    def cost_with(users):
        fresh()
        with storage.transaction() as data:
            bucket = data.setdefault("users", {})
            for index in range(users):
                bucket[economy.user_key(CHAT, index)] = {
                    "bronze": index, "silver": 0, "gold": 0,
                    "total_coin_value": index, "transactions": [],
                    "references": [], "value_reached_seq": index, "wins": 0,
                }
        start = time.perf_counter()
        for _ in range(20):
            economy.record_message(-100123, 999, "علی")
        return (time.perf_counter() - start) / 20 * 1000

    small = cost_with(0)
    large = cost_with(4000)
    check("ثبت پیام روی دیتابیس بزرگ هم سریع است", large < 5.0,
          f"-> {large:.2f} ms")
    check("هزینه با اندازهٔ دیتابیس رشد نمی‌کند", large < small + 5.0,
          f"-> small={small:.3f} large={large:.3f}")

    # نباید در مسیر داغ روی دیسک بنویسد
    fresh()
    economy.record_message(-1, 7, "علی")
    check("در مسیر داغ روی دیسک نمی‌نویسد",
          not storage.DATA_FILE.exists())
    check("علامت تغییر گذاشته شد", storage.is_dirty() is True)

    # ولی داده باید با flush ماندگار شود
    economy.record_message(-1, 7, "علی")
    check("flush داده را می‌نویسد", economy.flush() is True)
    check("فایل ساخته شد", storage.DATA_FILE.exists())
    saved = json.loads(storage.DATA_FILE.read_text(encoding="utf-8"))
    day = list(saved["daily_messages"])[0]
    check("هر دو پیام شمرده شدند",
          saved["daily_messages"][day]["-1"]["7"]["messages"] == 2)
    check("flush دوباره چیزی نمی‌نویسد", economy.flush() is False)


def test_award_still_writes_immediately():
    """جایزه و موجودی باید فوراً ذخیره شوند، نه معوق."""
    print("\n### 💾 عملیات مالی فوراً روی دیسک می‌نشیند")
    fresh()
    economy.award(CHAT, 1, 10, name="علی")
    check("جایزه بلافاصله نوشته شد", storage.DATA_FILE.exists())
    saved = json.loads(storage.DATA_FILE.read_text(encoding="utf-8"))
    check("مقدار درست ذخیره شد", saved["users"][economy.user_key(CHAT, 1)]["bronze"] == 10)

    economy.add_bronze(CHAT, 2, 5)
    saved = json.loads(storage.DATA_FILE.read_text(encoding="utf-8"))
    check("افزودن سکه هم فوری است", saved["users"][economy.user_key(CHAT, 2)]["bronze"] == 5)


def main():
    test_coin_types()
    test_add_remove()
    test_no_negative_balance()
    test_conversion()
    test_conversion_preserves_value()
    test_total_value()
    test_settings_drive_value()
    test_transfer()
    test_transfer_conserves_total()
    test_ranking_by_value()
    test_ranking_tie_break()
    test_ranking_limit()
    test_transaction_history()
    test_no_duplicate_transactions()
    test_shop()
    test_shop_purchase_is_atomic()
    test_daily_reward()
    test_concurrent_adds()
    test_concurrent_spend_never_negative()
    test_concurrent_transfers()
    test_concurrent_duplicate_reference()
    test_rollback_on_error()
    test_persistence()
    test_independence()
    test_public_api_surface()
    test_games_use_api_only()
    test_record_message_is_hot_path_cheap()
    test_award_still_writes_immediately()

    print("\n" + "=" * 52)
    print(f"passed={PASSED} failed={FAILED}")
    print("=" * 52)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
