"""📤 انتقال سکه فقط با یوزرنیم + خرید آیتم از فروشگاه.

پوشش:
    • انتقال با ریپلای کاملاً حذف شده
    • گام ۱ یوزرنیم، گام ۲ مقدار
    • آیدی عددی رد می‌شود
    • یوزرنیم بی‌اعتبار رد می‌شود
    • هر سه سکه (برنز، نقره، طلا)
    • دفترچهٔ یوزرنیم per-group
    • خرید آیتم ۱ تا ۳۲ با تایید/لغو و کمبود موجودی

    python tests/test_transfer_username.py
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
import modules.group_storage as group_storage
import test_economy_routing as routing
from economy import catalog, directory, profiles
from economy.ui import balance_menu, profile_menu, shop_menu
from test_economy_routing import build_handler

PASSED = FAILED = 0
CHAT = -1009999888877
CHAT_B = -100321321321


def check(label, cond, detail=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  PASS  {label}")
    else:
        FAILED += 1
        print(f"  FAIL  {label} {detail}")


class Event(routing.Event):
    """رویداد با یوزرنیم قابل تنظیم."""

    def __init__(self, text, user_id, username=None, chat_id=CHAT):
        super().__init__(text, user_id, chat_id=chat_id)
        self._user = routing.User(user_id, f"U{user_id}", username=username)


def fresh():
    temp = Path(tempfile.mkdtemp())
    storage.use_file(temp / "economy.json")
    store.ITEMS_FILE = temp / "shop.json"
    store._cache = None
    store._cache_mtime = None
    eco_handler.reset_all()
    group_storage.activate_group(CHAT, "گروه تست")
    return temp


# ===========================================================================
# ۱) دفترچهٔ یوزرنیم
# ===========================================================================
def test_username_validation():
    print("\n### 📇 اعتبارسنجی یوزرنیم")
    for good in ("@ali", "ali", "A_li9", "user_name", "@Mina_dh1"):
        check(f"«{good}» معتبر است", directory.is_valid(good))
    for bad in ("12345", "@123", "ab", "سلام", "9abc", "", None,
                "a" * 33, "@bad!name", "with space"):
        check(f"«{bad}» رد می‌شود", not directory.is_valid(bad))
    check("آیدی عددی هرگز معتبر نیست", not directory.is_valid("987654321"))


def test_username_normalize():
    print("\n### 📇 یکسان‌سازی یوزرنیم")
    check("@ حذف می‌شود", directory.normalize("@Ali") == "ali")
    check("حروف بزرگ کوچک می‌شوند", directory.normalize("ALI") == "ali")
    check("فاصله حذف می‌شود", directory.normalize("  @ali  ") == "ali")
    check("خالی None می‌دهد", directory.normalize("   ") is None)


def test_directory_is_per_group():
    print("\n### 📇 دفترچه per-group است")
    fresh()
    directory.remember(CHAT, 1, "@ali")
    directory.remember(CHAT_B, 2, "@ali")
    storage.flush()
    check("گروه A کاربر خودش را می‌دهد", directory.lookup(CHAT, "ali") == "1")
    check("گروه B کاربر خودش را می‌دهد",
          directory.lookup(CHAT_B, "ali") == "2")
    check("یوزرنیم ناموجود None می‌دهد",
          directory.lookup(CHAT, "nobody") is None)
    check("username_of برعکس کار می‌کند",
          directory.username_of(CHAT, 1) == "ali")


def test_directory_updates_username():
    print("\n### 📇 تغییر یوزرنیم ثبت می‌شود")
    fresh()
    directory.remember(CHAT, 5, "@old")
    directory.remember(CHAT, 5, "@new")
    storage.flush()
    check("یوزرنیم جدید ثبت شد", directory.lookup(CHAT, "new") == "5")
    check("هر دو به یک کاربر اشاره می‌کنند",
          directory.lookup(CHAT, "old") == "5")


def test_directory_ignores_missing_username():
    print("\n### 📇 کاربر بدون یوزرنیم ثبت نمی‌شود")
    fresh()
    check("None چیزی ثبت نمی‌کند",
          directory.remember(CHAT, 6, None) is None)
    check("رشتهٔ خالی چیزی ثبت نمی‌کند",
          directory.remember(CHAT, 6, "  ") is None)
    check("دفترچه خالی ماند", directory.entries(CHAT) == {})


# ===========================================================================
# ۲) حذف کامل روش ریپلای
# ===========================================================================
def test_reply_transfer_removed():
    print("\n### 🚫 انتقال با ریپلای حذف شده")
    handler_src = (ROOT / "handlers" / "economy_handler.py").read_text(
        encoding="utf-8")
    menu_src = (ROOT / "economy" / "ui" / "balance_menu.py").read_text(
        encoding="utf-8")
    check("تابع _reply_to_user_id حذف شده",
          "_reply_to_user_id" not in handler_src)
    check("get_reply_message دیگر صدا زده نمی‌شود",
          "get_reply_message" not in handler_src)
    check("پیام «ریپلای کنید» در راهنما نیست",
          "ریپلای کنید" not in menu_src)

    prompt = balance_menu.transfer_prompt(CHAT, economy.BRONZE, 1)
    check("راهنما یوزرنیم می‌خواهد", "یوزرنیم کاربر مقصد" in prompt)
    check("راهنما مثال @username دارد", "@username" in prompt)
    check("راهنما حرفی از ریپلای نمی‌زند", "ریپلای" not in prompt)
    check("راهنما گزینهٔ لغو دارد", "برای لغو، ۰ بفرستید" in prompt)


def test_transfer_prompt_shows_real_balance():
    print("\n### 📤 راهنما موجودی واقعی را نشان می‌دهد")
    fresh()
    economy.add_bronze(CHAT, 7, 45)
    prompt = balance_menu.transfer_prompt(CHAT, economy.BRONZE, 7)
    check("موجودی واقعی نمایش داده می‌شود", "موجودی شما: ۴۵" in prompt,
          f"-> {prompt}")
    check("عنوان نوع سکه را دارد", "📤 انتقال برنز" in prompt)


def test_amount_prompt_format():
    print("\n### 📤 راهنمای مقدار")
    fresh()
    economy.add_silver(CHAT, 8, 12)
    prompt = balance_menu.transfer_amount_prompt(
        CHAT, economy.SILVER, 8, "mina")
    check("یوزرنیم مقصد نمایش داده می‌شود", "@mina" in prompt)
    check("مقدار خواسته می‌شود", "مقدار نقره برای انتقال" in prompt)
    check("مثال عددی دارد", "10" in prompt)
    check("موجودی واقعی را نشان می‌دهد", "موجودی شما: ۱۲" in prompt)
    check("گزینهٔ لغو دارد", "برای لغو، ۰ بفرستید" in prompt)


# ===========================================================================
# ۳) resolve_target
# ===========================================================================
def test_resolve_rejects_numeric_id():
    print("\n### 🚫 آیدی عددی پذیرفته نمی‌شود")
    fresh()
    directory.remember(CHAT, 999, "@target")
    storage.flush()
    for numeric in ("999", "۹۹۹", "123456789"):
        target, username, error = balance_menu.resolve_target(
            CHAT, numeric, 1)
        check(f"«{numeric}» رد شد", target is None and bool(error),
              f"-> target={target!r}")
        check(f"پیام «{numeric}» آیدی عددی را توضیح می‌دهد",
              bool(error) and "آیدی عددی" in error, f"-> {error}")


def test_resolve_rejects_invalid_username():
    print("\n### 🚫 یوزرنیم بی‌اعتبار")
    fresh()
    for bad in ("@bad!name", "سلام", "ab", "9abc"):
        target, username, error = balance_menu.resolve_target(CHAT, bad, 1)
        check(f"«{bad}» رد شد", target is None and bool(error))
        check(f"پیام «{bad}» راهنمای درست می‌دهد", "@username" in error)


def test_resolve_rejects_unknown_user():
    print("\n### 🚫 کاربر ناشناخته")
    fresh()
    target, username, error = balance_menu.resolve_target(CHAT, "@ghost", 1)
    check("کاربر ناموجود رد می‌شود", target is None)
    check("پیام توضیح می‌دهد", "پیدا نشد" in error, f"-> {error}")
    check("راه حل را می‌گوید", "پیام" in error)


def test_resolve_rejects_self():
    print("\n### 🚫 انتقال به خود")
    fresh()
    directory.remember(CHAT, 50, "@myself")
    storage.flush()
    target, username, error = balance_menu.resolve_target(CHAT, "@myself", 50)
    check("انتقال به خود رد می‌شود", target is None)
    check("پیام مناسب می‌دهد", "خودتان" in error, f"-> {error}")


def test_resolve_accepts_valid_username():
    print("\n### ✅ یوزرنیم درست پذیرفته می‌شود")
    fresh()
    directory.remember(CHAT, 60, "@Mina_dh1")
    storage.flush()
    for spelling in ("@Mina_dh1", "mina_dh1", "@MINA_DH1", "  @Mina_dh1 "):
        target, username, error = balance_menu.resolve_target(
            CHAT, spelling, 1)
        check(f"«{spelling}» پذیرفته شد",
              target == "60" and error is None, f"-> {error}")
        check(f"یوزرنیم «{spelling}» یکسان‌سازی شد", username == "mina_dh1")


def test_resolve_is_group_scoped():
    print("\n### 🚫 مقصد باید در همین گروه باشد")
    fresh()
    directory.remember(CHAT_B, 70, "@elsewhere")
    storage.flush()
    target, username, error = balance_menu.resolve_target(
        CHAT, "@elsewhere", 1)
    check("کاربر گروه دیگر پیدا نمی‌شود", target is None)
    check("در گروه خودش پیدا می‌شود",
          balance_menu.resolve_target(CHAT_B, "@elsewhere", 1)[0] == "70")


# ===========================================================================
# ۴) انتقال کامل از مسیر هندلر — هر سه سکه
# ===========================================================================
def _transfer_scenario(coin_option, coin_type, amount, sender=100,
                       receiver=200):
    async def scenario():
        bot, handler = await build_handler()
        await handler(Event("سلام", receiver, "mina"))
        storage.flush()
        if coin_type == economy.BRONZE:
            economy.add_bronze(CHAT, sender, 500)
        elif coin_type == economy.SILVER:
            economy.add_silver(CHAT, sender, 500)
        else:
            economy.add_gold(CHAT, sender, 500)

        steps = {}
        await handler(Event("موجودی", sender, "ali"))
        step1 = Event(coin_option, sender, "ali")
        await handler(step1)
        steps["prompt"] = step1
        step2 = Event("@mina", sender, "ali")
        await handler(step2)
        steps["target"] = step2
        step3 = Event(str(amount), sender, "ali")
        await handler(step3)
        steps["done"] = step3
        return bot, steps

    return asyncio.run(scenario())


def test_transfer_bronze():
    print("\n### 💰 انتقال برنز با یوزرنیم")
    fresh()
    bot, steps = _transfer_scenario("3", economy.BRONZE, 30)
    check("راهنمای یوزرنیم آمد", steps["prompt"].said("یوزرنیم کاربر مقصد"))
    check("ریپلای خواسته نشد", not steps["prompt"].said("ریپلای"))
    check("راهنمای مقدار آمد", steps["target"].said("مقدار برنز"))
    check("یوزرنیم مقصد تایید شد", steps["target"].said("@mina"))
    check("انتقال انجام شد", steps["done"].said("منتقل شد"))
    check("از فرستنده کم شد",
          economy.get_balance(CHAT, 100)[economy.BRONZE] == 470)
    check("به گیرنده اضافه شد",
          economy.get_balance(CHAT, 200)[economy.BRONZE] == 30)
    check("انتقال لاگ شد", bot.logger.has("ECONOMY TRANSFER"))
    check("هیچ خطایی نیست", not bot.logger.errors,
          f"-> {[e[:100] for e in bot.logger.errors][:1]}")
    eco_handler.reset_all()


def test_transfer_silver():
    print("\n### 💰 انتقال نقره با یوزرنیم")
    fresh()
    bot, steps = _transfer_scenario("4", economy.SILVER, 25)
    check("راهنمای مقدار نقره آمد", steps["target"].said("مقدار نقره"))
    check("انتقال انجام شد", steps["done"].said("منتقل شد"))
    check("از فرستنده کم شد",
          economy.get_balance(CHAT, 100)[economy.SILVER] == 475)
    check("به گیرنده اضافه شد",
          economy.get_balance(CHAT, 200)[economy.SILVER] == 25)
    eco_handler.reset_all()


def test_transfer_gold():
    print("\n### 💰 انتقال طلا با یوزرنیم")
    fresh()
    bot, steps = _transfer_scenario("5", economy.GOLD, 7)
    check("راهنمای مقدار طلا آمد", steps["target"].said("مقدار طلا"))
    check("انتقال انجام شد", steps["done"].said("منتقل شد"))
    check("از فرستنده کم شد",
          economy.get_balance(CHAT, 100)[economy.GOLD] == 493)
    check("به گیرنده اضافه شد",
          economy.get_balance(CHAT, 200)[economy.GOLD] == 7)
    eco_handler.reset_all()


def test_numeric_id_never_transfers():
    """آیدی عددی نباید هرگز به انتقال واقعی منجر شود."""
    print("\n### 🚫 آیدی عددی هرگز سکه منتقل نمی‌کند")
    fresh()

    async def scenario():
        bot, handler = await build_handler()
        await handler(Event("سلام", 200, "mina"))
        storage.flush()
        economy.add_bronze(CHAT, 100, 100)
        await handler(Event("موجودی", 100, "ali"))
        await handler(Event("3", 100, "ali"))
        attempt = Event("200", 100, "ali")
        await handler(attempt)
        # اگر آیدی عددی پذیرفته شده بود، این عدد مقدار انتقال می‌شد.
        follow = Event("50", 100, "ali")
        await handler(follow)
        return attempt, follow

    attempt, follow = asyncio.run(scenario())
    check("آیدی عددی رد شد", attempt.said("آیدی عددی"))
    check("گیرنده هیچ سکه‌ای نگرفت",
          economy.get_balance(CHAT, 200)[economy.BRONZE] == 0,
          f"-> {economy.get_balance(CHAT, 200)[economy.BRONZE]}")
    check("فرستنده هیچ سکه‌ای از دست نداد",
          economy.get_balance(CHAT, 100)[economy.BRONZE] == 100,
          f"-> {economy.get_balance(CHAT, 100)[economy.BRONZE]}")
    check("عدد بعدی هم به‌عنوان مقصد رد می‌شود",
          follow.said("آیدی عددی") or follow.said("یوزرنیم"))
    eco_handler.reset_all()


def test_transfer_rejects_numeric_through_handler():
    print("\n### 🔌 آیدی عددی از مسیر هندلر رد می‌شود")
    fresh()

    async def scenario():
        bot, handler = await build_handler()
        await handler(Event("سلام", 200, "mina"))
        storage.flush()
        economy.add_bronze(CHAT, 100, 100)
        await handler(Event("موجودی", 100, "ali"))
        await handler(Event("3", 100, "ali"))
        numeric = Event("200", 100, "ali")
        await handler(numeric)
        still = balance_menu.session(CHAT, 100)
        ok = Event("@mina", 100, "ali")
        await handler(ok)
        return numeric, still, ok

    numeric, still, ok = asyncio.run(scenario())
    check("آیدی عددی رد شد", numeric.said("آیدی عددی"))
    check("هیچ سکه‌ای منتقل نشد",
          economy.get_balance(CHAT, 200)[economy.BRONZE] == 0)
    check("منو در همان گام می‌ماند",
          still is not None and still["step"] == "transfer")
    check("بعدش یوزرنیم درست کار می‌کند", ok.said("مقدار برنز"))
    eco_handler.reset_all()


def test_transfer_invalid_amount():
    print("\n### 🔌 مقدار نامعتبر")
    fresh()

    async def scenario():
        bot, handler = await build_handler()
        await handler(Event("سلام", 200, "mina"))
        storage.flush()
        economy.add_bronze(CHAT, 100, 100)
        await handler(Event("موجودی", 100, "ali"))
        await handler(Event("3", 100, "ali"))
        await handler(Event("@mina", 100, "ali"))
        bad = Event("خیلی زیاد", 100, "ali")
        await handler(bad)
        good = Event("10", 100, "ali")
        await handler(good)
        return bad, good

    bad, good = asyncio.run(scenario())
    check("مقدار غیرعددی رد می‌شود", bad.said("عدد مثبت"))
    check("پس از خطا همان گام ادامه دارد", good.said("منتقل شد"))
    check("مقدار درست منتقل شد",
          economy.get_balance(CHAT, 200)[economy.BRONZE] == 10)
    eco_handler.reset_all()


def test_transfer_cancel():
    print("\n### 🔌 لغو انتقال")
    fresh()

    async def scenario():
        bot, handler = await build_handler()
        await handler(Event("سلام", 200, "mina"))
        storage.flush()
        economy.add_bronze(CHAT, 100, 100)
        await handler(Event("موجودی", 100, "ali"))
        await handler(Event("3", 100, "ali"))
        cancel1 = Event("0", 100, "ali")
        await handler(cancel1)

        await handler(Event("موجودی", 100, "ali"))
        await handler(Event("3", 100, "ali"))
        await handler(Event("@mina", 100, "ali"))
        cancel2 = Event("0", 100, "ali")
        await handler(cancel2)
        return cancel1, cancel2

    cancel1, cancel2 = asyncio.run(scenario())
    check("لغو در گام یوزرنیم", cancel1.said("لغو شد"))
    check("لغو در گام مقدار", cancel2.said("لغو شد"))
    check("هیچ سکه‌ای جابه‌جا نشد",
          economy.get_balance(CHAT, 100)[economy.BRONZE] == 100
          and economy.get_balance(CHAT, 200)[economy.BRONZE] == 0)
    eco_handler.reset_all()


def test_transfer_insufficient_balance():
    print("\n### 🔌 موجودی ناکافی")
    fresh()

    async def scenario():
        bot, handler = await build_handler()
        await handler(Event("سلام", 200, "mina"))
        storage.flush()
        economy.add_bronze(CHAT, 100, 5)
        await handler(Event("موجودی", 100, "ali"))
        await handler(Event("3", 100, "ali"))
        await handler(Event("@mina", 100, "ali"))
        too_much = Event("500", 100, "ali")
        await handler(too_much)
        return too_much

    too_much = asyncio.run(scenario())
    check("کمبود موجودی اعلام می‌شود", too_much.said("کافی نیست"))
    check("سکه‌ای منتقل نشد",
          economy.get_balance(CHAT, 100)[economy.BRONZE] == 5
          and economy.get_balance(CHAT, 200)[economy.BRONZE] == 0)
    eco_handler.reset_all()


def test_directory_registers_from_any_message():
    """کاربری که فقط دستور می‌فرستد هم باید ثبت شود."""
    print("\n### 📇 ثبت یوزرنیم از هر پیامی")
    fresh()

    async def scenario():
        bot, handler = await build_handler()
        await handler(Event("موجودی", 300, "commander"))
        storage.flush()
        return directory.lookup(CHAT, "commander")

    found = asyncio.run(scenario())
    check("کاربر فقط با فرستادن دستور ثبت شد", found == "300", f"-> {found}")
    eco_handler.reset_all()


# ===========================================================================
# ۵) خرید آیتم از فروشگاه
# ===========================================================================
def test_shop_item_numbers_1_to_32():
    print("\n### 🛍 شماره‌های ۱ تا ۳۲")
    items = catalog.all_items()
    check("۳۲ آیتم هست", len(items) == 32)
    check("۱ تا ۱۰ نشان‌اند",
          all(items[i]["kind"] == catalog.KIND_BADGE for i in range(10)))
    check("۱۱ تا ۱۷ سطح‌اند",
          all(items[i]["kind"] == catalog.KIND_STAR for i in range(10, 17)))
    check("۱۸ تا ۳۲ لقب‌اند",
          all(items[i]["kind"] == catalog.KIND_TITLE for i in range(17, 32)))
    for number in range(1, 33):
        check(f"شمارهٔ {number} به آیتم می‌رسد",
              catalog.resolve(str(number)) is not None)


def test_shop_item_names_unchanged():
    print("\n### 🛍 نام و قیمت آیتم‌ها دست‌نخورده")
    text, _ = shop_menu.render_items()
    for expected in ("۱) 🦊 نشان روباه — ۱۰۰ نقره",
                     "۲) 🦁 نشان شیر — ۱۲۰ نقره",
                     "۳) 🫀 نشان قلب — ۳۰۰ برنز"):
        check(f"«{expected}» بدون تغییر", expected in text)
    check("دستهٔ نشان‌ها هست", "🛡 نشان‌ها" in text)
    check("دستهٔ سطح هست", "⭐ خرید سطح" in text)
    check("دستهٔ لقب هست", "🏷 خرید لقب اختصاصی" in text)
    check("قیمت لقب‌ها اعلام شده", "قیمت همه لقب‌ها: ۲۰۰ برنز" in text)


def _shop_buy(user_id, number, confirm="تایید", bronze=0, silver=0):
    async def scenario():
        bot, handler = await build_handler()
        if bronze:
            economy.add_bronze(CHAT, user_id, bronze)
        if silver:
            economy.add_silver(CHAT, user_id, silver)
        await handler(Event("فروشگاه", user_id, "buyer"))
        enter = Event("1", user_id, "buyer")
        await handler(enter)
        pick = Event(str(number), user_id, "buyer")
        await handler(pick)
        done = None
        if confirm is not None:
            done = Event(confirm, user_id, "buyer")
            await handler(done)
        return bot, enter, pick, done

    return asyncio.run(scenario())


def test_shop_buy_badge():
    print("\n### 🛍 خرید نشان (شمارهٔ ۳)")
    fresh()
    bot, enter, pick, done = _shop_buy(400, 3, bronze=500)
    check("راهنمای انتخاب آمد", enter.said("شمارهٔ آیتم"))
    check("محدودهٔ ۱ تا ۳۲ اعلام شد", enter.said("۳۲"))
    check("تایید خواسته شد", pick.said("مطمئن هستید"))
    check("نام آیتم درست است", pick.said("نشان قلب"))
    check("قیمت درست است", pick.said("۳۰۰"))
    check("خرید انجام شد", done.said("خریداری شد"))
    check("سکه کسر شد",
          economy.get_balance(CHAT, 400)[economy.BRONZE] == 200)
    check("آیتم به پروفایل اضافه شد",
          "badge_heart" in profiles.get(CHAT, 400)["badges"])
    eco_handler.reset_all()


def test_shop_buy_star():
    print("\n### 🛍 خرید سطح (شمارهٔ ۱۳)")
    fresh()
    bot, enter, pick, done = _shop_buy(401, 13, silver=3000)
    check("تایید خواسته شد", pick.said("مطمئن هستید"))
    check("خرید انجام شد", done.said("خریداری شد"))
    check("سطح روی ۳ نشست", profiles.stars(CHAT, 401) == 3)
    check("نقره کسر شد",
          economy.get_balance(CHAT, 401)[economy.SILVER] == 2200)
    eco_handler.reset_all()


def test_shop_buy_title():
    print("\n### 🛍 خرید لقب (شمارهٔ ۱۸)")
    fresh()
    bot, enter, pick, done = _shop_buy(402, 18, bronze=500)
    check("تایید خواسته شد", pick.said("مطمئن هستید"))
    check("خرید انجام شد", done.said("خریداری شد"))
    check("لقب روی پروفایل نشست",
          profiles.get(CHAT, 402)["nickname"] == "𝙁𝙤𝙭 𝙆𝙞𝙣𝙜")
    check("برنز کسر شد",
          economy.get_balance(CHAT, 402)[economy.BRONZE] == 300)
    eco_handler.reset_all()


def test_shop_buy_cancel():
    print("\n### 🛍 لغو خرید")
    fresh()
    bot, enter, pick, done = _shop_buy(403, 3, confirm="لغو", bronze=500)
    check("لغو اعلام شد", done.said("لغو شد"))
    check("سکه‌ای کسر نشد",
          economy.get_balance(CHAT, 403)[economy.BRONZE] == 500)
    check("آیتمی اضافه نشد", profiles.get(CHAT, 403)["badges"] == [])
    eco_handler.reset_all()


def test_shop_insufficient_shows_shortfall():
    print("\n### 🛍 کمبود موجودی با مقدار دقیق")
    fresh()
    bot, enter, pick, done = _shop_buy(404, 3, confirm=None, bronze=45)
    check("کمبود اعلام شد", pick.said("موجودی سکه کافی نیست"))
    check("مقدار کمبود درست است", pick.said("۲۵۵"), f"-> {pick.replies}")
    check("نوع سکه اعلام شد", pick.said("برنز"))
    check("سکه‌ای کسر نشد",
          economy.get_balance(CHAT, 404)[economy.BRONZE] == 45)
    eco_handler.reset_all()


def test_shop_all_32_selectable():
    print("\n### 🛍 هر ۳۲ شماره قابل انتخاب است")
    fresh()
    economy.add_bronze(CHAT, 405, 100000)
    economy.add_silver(CHAT, 405, 100000)
    for number in range(1, 33):
        item, message = shop_menu.select_item(CHAT, 405, str(number))
        check(f"شمارهٔ {number} انتخاب می‌شود", item is not None,
              f"-> {message if isinstance(message, str) else ''}")


def test_shop_rejects_out_of_range():
    print("\n### 🛍 شمارهٔ خارج از محدوده")
    fresh()
    economy.add_bronze(CHAT, 406, 1000)
    for number in ("33", "99", "0۰0"):
        item, message = shop_menu.select_item(CHAT, 406, number)
        check(f"«{number}» رد می‌شود", item is None)


# ===========================================================================
def main():
    test_username_validation()
    test_username_normalize()
    test_directory_is_per_group()
    test_directory_updates_username()
    test_directory_ignores_missing_username()
    test_reply_transfer_removed()
    test_transfer_prompt_shows_real_balance()
    test_amount_prompt_format()
    test_resolve_rejects_numeric_id()
    test_resolve_rejects_invalid_username()
    test_resolve_rejects_unknown_user()
    test_resolve_rejects_self()
    test_resolve_accepts_valid_username()
    test_resolve_is_group_scoped()
    test_transfer_bronze()
    test_transfer_silver()
    test_transfer_gold()
    test_numeric_id_never_transfers()
    test_transfer_rejects_numeric_through_handler()
    test_transfer_invalid_amount()
    test_transfer_cancel()
    test_transfer_insufficient_balance()
    test_directory_registers_from_any_message()
    test_shop_item_numbers_1_to_32()
    test_shop_item_names_unchanged()
    test_shop_buy_badge()
    test_shop_buy_star()
    test_shop_buy_title()
    test_shop_buy_cancel()
    test_shop_insufficient_shows_shortfall()
    test_shop_all_32_selectable()
    test_shop_rejects_out_of_range()
    print(f"\npassed={PASSED} failed={FAILED}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
