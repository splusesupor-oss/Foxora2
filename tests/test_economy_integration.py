"""🔗 اتصال اقتصاد به ربات — تست واقعی مسیر کامل.

اثبات می‌کند:
  • جایزهٔ هر بازی در اقتصاد جدید ثبت می‌شود.
  • سیستم سکهٔ قدیمی کاملاً حذف شده است.
  • هیچ بازی‌ای مستقیماً به دیتابیس اقتصاد دست نمی‌زند.
  • دو بخش «موجودی» و «فروشگاه» کار می‌کنند.
  • «رتبه ها» فقط بر پایهٔ total_coin_value است.

    python tests/test_economy_integration.py
"""
import asyncio
import ast
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import economy
import economy.shop.store as store
import economy.storage as storage
import handlers.economy_handler as eco_handler
import handlers.fox_games_router as router
from economy.ui import balance_menu, shop_menu

PASSED = FAILED = 0
CHAT = -100999001


def check(label, cond, detail=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  PASS  {label}")
    else:
        FAILED += 1
        print(f"  FAIL  {label} {detail}")


class User:
    def __init__(self, uid, name=None, username=None):
        self.id = uid
        self.first_name = name or f"U{uid}"
        self.last_name = None
        self.username = username


class Message:
    def __init__(self, mid=1):
        self.id = mid


class Event:
    def __init__(self, reply_target=None):
        self.out = []
        self.message = Message()
        self.reply_to = reply_target is not None
        self._reply_target = reply_target

    async def reply(self, text, **kwargs):
        self.out.append(text)
        return None

    def said(self, needle):
        return any(needle in m for m in self.out)

    async def get_reply_message(self):
        if self._reply_target is None:
            return None
        return _ReplyMessage(self._reply_target)


class _ReplyMessage:
    def __init__(self, user):
        self._user = user

    async def get_sender(self):
        return self._user


class Logger:
    def __init__(self):
        self.info, self.errors = [], []

    def log_info(self, m):
        self.info.append(m)

    def log_error(self, m):
        self.errors.append(m)

    def has(self, needle):
        return any(needle in m for m in self.info + self.errors)


class Client:
    def __init__(self):
        self.sent = []

    async def send_message(self, target, text, **kwargs):
        self.sent.append((target, text))
        return True


class Bot:
    def __init__(self):
        self.client = Client()
        self.logger = Logger()


def fresh():
    temp = Path(tempfile.mkdtemp())
    storage.use_file(temp / "economy.json")
    store.ITEMS_FILE = temp / "shop.json"
    store._cache = None
    store._cache_mtime = None
    eco_handler.reset_all()
    router.reset_all()
    return temp


async def send_eco(bot, event, user_id, text, name=None):
    return await eco_handler.handle(
        bot, event, CHAT, user_id, User(user_id, name), text, bot.logger)


# ===========================================================================
# حذف کامل سیستم قدیمی
# ===========================================================================
def test_legacy_removed():
    print("\n### 🗑️ حذف کامل سیستم سکهٔ قدیمی")
    for name in ("modules/coins.py", "modules/game_points.py"):
        check(f"فایل {name} حذف شده", not (ROOT / name).exists())

    import importlib
    for name in ("modules.coins", "modules.game_points"):
        try:
            importlib.import_module(name)
            check(f"ماژول {name} دیگر قابل import نیست", False)
        except ModuleNotFoundError:
            check(f"ماژول {name} دیگر قابل import نیست", True)

    live = []
    for folder in ("handlers", "core", "modules"):
        for path in (ROOT / folder).rglob("*.py"):
            if "__pycache__" in str(path):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if "modules.coins" in text or "modules.game_points" in text:
                live.append(path.name)
    check("هیچ ارجاع زنده‌ای به سیستم قدیمی نمانده", not live, f"-> {live}")


def test_no_direct_db_access():
    print("\n### 🔒 هیچ بازی‌ای مستقیماً به دیتابیس دست نمی‌زند")
    offenders = []
    for folder in ("handlers", "core", "modules"):
        for path in (ROOT / folder).rglob("*.py"):
            if "__pycache__" in str(path):
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8",
                                                errors="ignore"))
            except SyntaxError:
                # فایل‌های بکاپ قدیمی و شکسته به این بررسی ربطی ندارند.
                continue
            for node in ast.walk(tree):
                module = None
                if isinstance(node, ast.ImportFrom) and node.module:
                    module = node.module
                elif isinstance(node, ast.Import):
                    module = node.names[0].name
                if not module or not module.startswith("economy"):
                    continue
                # فقط API عمومی و لایهٔ UI مجازند.
                allowed = (
                    module == "economy"
                    or module.startswith("economy.ui")
                )
                if not allowed:
                    offenders.append(f"{path.name}:{module}")
    check("فقط از API عمومی اقتصاد استفاده می‌شود", not offenders,
          f"-> {offenders}")

    games = ("emoji_guess.py", "flag_guess.py", "riddles.py",
             "fill_blank.py", "name_family.py", "multiple_choice.py",
             "word_correction.py")
    leaked = []
    for name in games:
        path = ROOT / "modules" / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if "economy.storage" in text or "storage.transaction" in text:
            leaked.append(name)
    check("هیچ بازی‌ای لایهٔ storage را import نمی‌کند", not leaked,
          f"-> {leaked}")


# ===========================================================================
# جوایز بازی‌ها
# ===========================================================================
def test_emoji_reward_lands_in_economy():
    print("\n### 🎮 حدس ایموجی → اقتصاد")
    fresh()
    import modules.emoji_guess as eg
    eg.reset_all()

    puzzle = eg.start(CHAT, 11)
    before = economy.get_balance(CHAT, 11)[economy.BRONZE]
    eg.answer(CHAT, 11, "علی", puzzle["answer"])
    after = economy.get_balance(CHAT, 11)

    check("جایزه در اقتصاد ثبت شد",
          after[economy.BRONZE] == before + eg.REWARD_BRONZE,
          f"-> {after[economy.BRONZE]}")
    check("ارزش کل بازمحاسبه شد",
          after["total_coin_value"] == after[economy.BRONZE])
    check("تراکنش به عنوان جایزه ثبت شد",
          economy.transaction_history(CHAT, 11)[0]["kind"] == "reward")
    check("نام کاربر ذخیره شد",
          economy.get_profile(CHAT, 11)["name"] == "علی")
    eg.reset_all()


def test_fox_game_rewards():
    print("\n### 🎮 بازی‌های Fox → اقتصاد")
    fresh()
    from modules.fox_games import laugh_or_lose as lol

    async def scenario():
        bot, event = Bot(), Event()
        await router.handle(bot, event, CHAT, 1, User(1, "علی"),
                            "بخند یا بباز", bot.logger)
        lol.open_round(CHAT, lol._STORE.get(CHAT)["session_id"], bot.logger)
        await router.handle(bot, event, CHAT, 21, User(21, "حسین"),
                            "😂", bot.logger)
        return bot, event

    bot, event = asyncio.run(scenario())
    balance = economy.get_balance(CHAT, 21)
    check("برندهٔ بخند یا بباز سکه گرفت",
          balance[economy.BRONZE] == lol.WINNER_COINS,
          f"-> {balance[economy.BRONZE]}")
    check("پرداخت لاگ شد", bot.logger.has("FOX REWARD PAID"))
    check("در تاریخچه ثبت شد",
          economy.transaction_history(CHAT, 21)[0]["kind"] == "reward")
    router.reset_all()


def test_vampire_reward():
    print("\n### 🎮 خون‌آشام → اقتصاد")
    fresh()
    from modules.fox_games import vampire as vp
    vp.reset_all()
    logger = Logger()

    vp.start(CHAT, logger)
    for uid in range(31, 35):
        vp.join(CHAT, uid, User(uid, f"P{uid}"), logger)
    chosen = vp.choose_vampire(CHAT, logger)
    vp.open_guessing(CHAT, logger=logger)

    players = vp._STORE.get(CHAT)["players"]
    vampire_uid = chosen["player"]["user_id"]
    winner = next(p["user_id"] for p in players if p["user_id"] != vampire_uid)

    async def scenario():
        bot, event = Bot(), Event()
        await router.handle(bot, event, CHAT, winner, User(winner),
                            str(chosen["number"]), bot.logger)
        return bot

    bot = asyncio.run(scenario())
    # خون‌آشام بازی «سخت» است: نقره می‌دهد نه برنز.
    check("برندهٔ خون‌آشام ۷ سکه نقره گرفت",
          economy.get_balance(CHAT, winner)[economy.SILVER] == vp.WINNER_COINS,
          f"-> {economy.get_balance(CHAT, winner)[economy.SILVER]}")
    router.reset_all()


def test_reward_is_idempotent_across_games():
    print("\n### 🛡️ جایزه دو بار پرداخت نمی‌شود")
    fresh()
    for _ in range(5):
        economy.award(CHAT, 41, 3, reference="riddle:-100:41:7")
    check("پنج تلاش با مرجع یکسان یک بار پرداخت شد",
          economy.get_balance(CHAT, 41)[economy.BRONZE] == 3)
    check("فقط یک ردیف تاریخچه",
          len(economy.transaction_history(CHAT, 41)) == 1)


# ===========================================================================
# بخش موجودی
# ===========================================================================
def test_balance_menu_opens():
    print("\n### 💰 بخش موجودی")
    fresh()
    economy.add_bronze(CHAT, 1, 152)
    economy.add_silver(CHAT, 1, 34)
    economy.add_gold(CHAT, 1, 8)

    async def scenario():
        bot, event = Bot(), Event()
        consumed = await send_eco(bot, event, 1, "موجودی", "علی")
        return consumed, event

    consumed, event = asyncio.run(scenario())
    check("دستور «موجودی» مصرف شد", consumed is True)
    check("منو نمایش داده شد", event.said("کیف پول شما"))
    check("برنز نمایش داده شد", event.said("۱۵۲"))
    check("نقره نمایش داده شد", event.said("۳۴"))
    check("طلا نمایش داده شد", event.said("۸"))
    check("ارزش کل نمایش داده شد", event.said("ارزش کل"))
    check("همهٔ گزینه‌ها در یک منو هستند",
          all(event.said(x) for x in
              ("تبدیل برنز به نقره", "تبدیل نقره به طلا", "انتقال برنز",
               "انتقال نقره", "انتقال طلا", "تاریخچه", "جایزه روزانه")))
    check("session باز شد", balance_menu.is_open(CHAT, 1))
    eco_handler.reset_all()


def test_menu_conversion():
    print("\n### 💰 تبدیل از داخل منو")
    fresh()
    economy.add_bronze(CHAT, 1, 250)

    async def scenario():
        bot = Bot()
        await send_eco(bot, Event(), 1, "موجودی")
        one = Event()
        await send_eco(bot, one, 1, "1")
        return one

    event = asyncio.run(scenario())
    check("تبدیل برنز انجام شد", event.said("تبدیل شد"))
    balance = economy.get_balance(CHAT, 1)
    check("۱۰۰ برنز کسر شد", balance[economy.BRONZE] == 150)
    check("۱۲ نقره اضافه شد", balance[economy.SILVER] == 12)

    fresh()
    economy.add_silver(CHAT, 1, 100)

    async def scenario2():
        bot = Bot()
        await send_eco(bot, Event(), 1, "موجودی")
        two = Event()
        await send_eco(bot, two, 1, "۲")     # رقم فارسی
        return two

    event = asyncio.run(scenario2())
    check("رقم فارسی پذیرفته شد", event.said("تبدیل شد"))
    check("۷۰ نقره کسر شد", economy.get_balance(CHAT, 1)[economy.SILVER] == 30)
    check("۱۰ طلا اضافه شد", economy.get_balance(CHAT, 1)[economy.GOLD] == 10)

    # موجودی ناکافی
    async def scenario3():
        bot = Bot()
        await send_eco(bot, Event(), 1, "موجودی")
        fail = Event()
        await send_eco(bot, fail, 1, "2")
        return fail

    event = asyncio.run(scenario3())
    check("تبدیل بدون موجودی رد می‌شود", event.said("کافی نیست"))
    eco_handler.reset_all()


def test_menu_transfer():
    """انتقال حالا دو گامی است: یوزرنیم، سپس مقدار. ریپلای حذف شده."""
    print("\n### 💰 انتقال از داخل منو (با یوزرنیم)")
    fresh()
    economy.add_bronze(CHAT, 1, 100)
    economy.directory.remember(CHAT, 2, "@hosein")
    storage.flush()

    async def scenario():
        bot = Bot()
        await send_eco(bot, Event(), 1, "موجودی")
        prompt = Event()
        await send_eco(bot, prompt, 1, "3")
        target = Event()
        await send_eco(bot, target, 1, "@hosein")
        amount = Event()
        await send_eco(bot, amount, 1, "40")
        return prompt, target, amount

    prompt, target, amount = asyncio.run(scenario())
    check("راهنمای انتقال نمایش داده شد", prompt.said("انتقال برنز"))
    check("یوزرنیم خواسته می‌شود", prompt.said("یوزرنیم کاربر مقصد"))
    check("ریپلای دیگر خواسته نمی‌شود", not prompt.said("ریپلای"))
    check("پس از یوزرنیم، مقدار خواسته می‌شود", target.said("مقدار برنز"))
    check("انتقال انجام شد", amount.said("منتقل شد"))
    check("از فرستنده کسر شد",
          economy.get_balance(CHAT, 1)[economy.BRONZE] == 60)
    check("به گیرنده رسید",
          economy.get_balance(CHAT, 2)[economy.BRONZE] == 40)
    check("نام گیرنده ذخیره شد",
          economy.get_profile(CHAT, 2)["name"] == "@hosein")
    check("session بسته شد", not balance_menu.is_open(CHAT, 1))

    # آیدی عددی به‌جای یوزرنیم
    async def scenario2():
        bot = Bot()
        await send_eco(bot, Event(), 1, "موجودی")
        await send_eco(bot, Event(), 1, "3")
        numeric = Event()
        await send_eco(bot, numeric, 1, "2")
        return numeric

    event = asyncio.run(scenario2())
    check("آیدی عددی رد می‌شود", event.said("آیدی عددی"))

    # بیش از موجودی
    async def scenario3():
        bot = Bot()
        await send_eco(bot, Event(), 1, "موجودی")
        await send_eco(bot, Event(), 1, "3")
        await send_eco(bot, Event(), 1, "@hosein")
        too_much = Event()
        await send_eco(bot, too_much, 1, "99999")
        return too_much

    event = asyncio.run(scenario3())
    check("انتقال بیش از موجودی رد می‌شود", event.said("کافی نیست"))
    check("موجودی دست‌نخورده ماند",
          economy.get_balance(CHAT, 1)[economy.BRONZE] == 60)
    eco_handler.reset_all()


def test_menu_history_and_daily():
    print("\n### 💰 تاریخچه و جایزه روزانه از منو")
    fresh()
    economy.add_bronze(CHAT, 1, 10)

    async def scenario():
        bot = Bot()
        await send_eco(bot, Event(), 1, "موجودی")
        history = Event()
        await send_eco(bot, history, 1, "6")
        daily = Event()
        await send_eco(bot, daily, 1, "7")
        again = Event()
        await send_eco(bot, again, 1, "7")
        return history, daily, again

    history, daily, again = asyncio.run(scenario())
    check("تاریخچه نمایش داده شد", history.said("تاریخچه تراکنش"))
    check("جایزه روزانه پرداخت شد", daily.said("جایزه روزانه دریافت شد"))
    check("دریافت دوباره رد شد", again.said("قبلاً دریافت"))
    check("موجودی افزایش یافت",
          economy.get_balance(CHAT, 1)[economy.BRONZE] == 35)
    eco_handler.reset_all()


def test_no_separate_commands():
    """هیچ‌کدام از قابلیت‌ها دستور جداگانه ندارند."""
    print("\n### 💰 نبود دستور جداگانه")
    fresh()

    async def scenario(text):
        bot, event = Bot(), Event()
        consumed = await send_eco(bot, event, 1, text)
        return consumed, event

    for text in ("تبدیل برنز", "تبدیل نقره", "انتقال", "تاریخچه",
                 "جایزه روزانه", "انتقال برنز", "خرید"):
        consumed, event = asyncio.run(scenario(text))
        check(f"«{text}» دستور مستقل نیست", consumed is False,
              f"-> {event.out}")

    for text in ("موجودی", "فروشگاه"):
        consumed, _ = asyncio.run(scenario(text))
        check(f"«{text}» دستور معتبر است", consumed is True)
        eco_handler.reset_all()


# ===========================================================================
# بخش فروشگاه
# ===========================================================================
def test_shop_section():
    print("\n### 🛒 بخش فروشگاه")
    fresh()

    async def scenario():
        bot = Bot()
        menu = Event()
        await send_eco(bot, menu, 1, "فروشگاه")
        buy = Event()
        await send_eco(bot, buy, 1, "1")
        return menu, buy

    menu, buy = asyncio.run(scenario())
    check("منوی فروشگاه باز شد", menu.said("🛒 فروشگاه"))
    check("گزینهٔ ترکیبی موجود است", menu.said("لیست آیتم ها و خرید"))
    # فهرست هنگام ورود می‌آید و هرگز خالی نیست.
    check("فهرست ثابت آیتم‌ها نمایش داده می‌شود", menu.said("نشان روباه"))
    check("پیام «هنوز آیتمی» دیگر نمی‌آید", not menu.said("هنوز آیتمی"))
    check("راهنمای انتخاب آیتم می‌آید", buy.said("شمارهٔ آیتم"))
    check("موجودی و ارزش کل در فروشگاه دیده می‌شود",
          menu.said("ارزش کل"))
    eco_handler.reset_all()


def test_shop_buy_flow():
    print("\n### 🛒 خرید از فروشگاه")
    fresh()
    economy.shop.add_item("badge", "نشان طلایی", 50, "bronze", stock=1)
    economy.add_bronze(CHAT, 1, 120)

    async def scenario():
        bot = Bot()
        listing = Event()
        await send_eco(bot, listing, 1, "فروشگاه")
        prompt = Event()
        await send_eco(bot, prompt, 1, "1")
        await send_eco(bot, Event(), 1, "badge")
        buy = Event()
        await send_eco(bot, buy, 1, "تایید")
        return listing, prompt, buy

    listing, prompt, buy = asyncio.run(scenario())
    check("آیتم در فهرست دیده می‌شود", listing.said("نشان طلایی"))
    check("راهنمای خرید نمایش داده شد", prompt.said("برای لغو"))
    check("خرید انجام شد", buy.said("خریداری شد"))
    check("سکه کسر شد", economy.get_balance(CHAT, 1)[economy.BRONZE] == 70)
    check("خرید در تاریخچه ثبت شد",
          economy.transaction_history(CHAT, 1)[0]["kind"] == "purchase")
    check("انبار کم شد", economy.shop.get_item("badge")["stock"] == 0)
    eco_handler.reset_all()


def test_shop_is_extensible():
    """افزودن آیتم نباید نیاز به تغییر کد UI داشته باشد."""
    print("\n### 🛒 توسعه‌پذیری فروشگاه")
    fresh()
    for index in range(5):
        economy.shop.add_item(f"item{index}", f"آیتم {index}",
                              (index + 1) * 10, "bronze")
    text, _ = shop_menu.render_items()
    check("همهٔ آیتم‌های جدید بدون تغییر کد نمایش داده می‌شوند",
          all(f"آیتم {i}" in text for i in range(5)))

    economy.shop.add_item("gold_item", "آیتم طلایی", 2, "gold")
    text, _ = shop_menu.render_items()
    check("آیتم با سکهٔ متفاوت هم پشتیبانی می‌شود",
          "آیتم طلایی" in text and "طلا" in text)


# ===========================================================================
# رتبه‌بندی
# ===========================================================================
def test_ranking_uses_total_value():
    print("\n### 🏆 رتبه‌بندی فقط بر پایهٔ ارزش کل")
    fresh()
    economy.add_bronze(CHAT, 1, 1000)      # ارزش ۱۰۰۰، سکهٔ زیاد
    economy.add_gold(CHAT, 2, 20)          # ارزش ۲۰۰۰، سکهٔ کم

    ranking = economy.leaderboard(CHAT, 5)
    check("کاربر با ارزش بیشتر اول است", ranking[0]["user_id"] == "2")
    check("تعداد سکه ملاک نیست", ranking[1]["user_id"] == "1")
    check("get_rank هم‌خوان است",
          economy.get_rank(CHAT, 2) == 1 and economy.get_rank(CHAT, 1) == 2)


def test_ranking_tie_break_integration():
    print("\n### 🏆 تساوی: زودتر رسیده بالاتر")
    fresh()
    economy.add_bronze(CHAT, 1, 100)
    economy.add_bronze(CHAT, 2, 100)
    ranking = economy.leaderboard(CHAT, 5)
    check("ارزش‌ها برابرند",
          ranking[0]["total_coin_value"] == ranking[1]["total_coin_value"])
    check("نفر اول زودتر رسیده", ranking[0]["user_id"] == "1")


# ===========================================================================
# جدا بودن و پایداری
# ===========================================================================
def test_sections_are_independent():
    print("\n### 🔒 استقلال دو بخش از هم")
    fresh()

    async def scenario():
        bot = Bot()
        await send_eco(bot, Event(), 1, "موجودی")
        opened_balance = balance_menu.is_open(CHAT, 1)
        await send_eco(bot, Event(), 1, "فروشگاه")
        return opened_balance, balance_menu.is_open(CHAT, 1), \
            shop_menu.is_open(CHAT, 1)

    opened, still_balance, shop_open = asyncio.run(scenario())
    check("بخش موجودی باز شد", opened)
    check("باز کردن فروشگاه، موجودی را بست", not still_balance)
    check("بخش فروشگاه باز است", shop_open)
    eco_handler.reset_all()


def test_menu_does_not_swallow_game_messages():
    print("\n### 🔒 منو پیام بازی‌ها را نمی‌بلعد")
    fresh()

    async def scenario():
        bot = Bot()
        await send_eco(bot, Event(), 1, "موجودی")
        # متنی که گزینهٔ منو نیست باید عبور کند
        passthrough = Event()
        consumed = await send_eco(bot, passthrough, 1, "چیستان")
        return consumed, passthrough

    consumed, event = asyncio.run(scenario())
    check("متن نامرتبط مصرف نمی‌شود", consumed is False)
    check("پاسخی تولید نشد", event.out == [])
    check("منو همچنان باز است", balance_menu.is_open(CHAT, 1))
    eco_handler.reset_all()


def test_two_users_independent_sessions():
    print("\n### 🔒 گفتگوی هر کاربر جداست")
    fresh()
    economy.add_bronze(CHAT, 1, 200)
    economy.add_bronze(CHAT, 2, 200)

    async def scenario():
        bot = Bot()
        await send_eco(bot, Event(), 1, "موجودی")
        await send_eco(bot, Event(), 2, "موجودی")
        first = Event()
        await send_eco(bot, first, 1, "1")
        return first

    first = asyncio.run(scenario())
    check("کاربر اول تبدیل کرد", first.said("تبدیل شد"))
    check("موجودی کاربر اول تغییر کرد",
          economy.get_balance(CHAT, 1)[economy.BRONZE] == 100)
    check("موجودی کاربر دوم دست‌نخورده است",
          economy.get_balance(CHAT, 2)[economy.BRONZE] == 200)
    check("session کاربر دوم هنوز باز است", balance_menu.is_open(CHAT, 2))
    eco_handler.reset_all()


def test_persistence_after_restart():
    print("\n### 💾 ماندگاری پس از ری‌استارت")
    fresh()
    economy.award(CHAT, 1, 50, name="علی")
    expected = economy.get_balance(CHAT, 1)

    storage._cache = None
    storage._cache_mtime = None
    check("موجودی پس از ری‌استارت باقی است",
          economy.get_balance(CHAT, 1) == expected)
    check("تاریخچه باقی است", len(economy.transaction_history(CHAT, 1)) == 1)
    check("رتبه‌بندی پس از ری‌استارت کار می‌کند",
          economy.get_rank(CHAT, 1) == 1)


def main():
    test_legacy_removed()
    test_no_direct_db_access()
    test_emoji_reward_lands_in_economy()
    test_fox_game_rewards()
    test_vampire_reward()
    test_reward_is_idempotent_across_games()
    test_balance_menu_opens()
    test_menu_conversion()
    test_menu_transfer()
    test_menu_history_and_daily()
    test_no_separate_commands()
    test_shop_section()
    test_shop_buy_flow()
    test_shop_is_extensible()
    test_ranking_uses_total_value()
    test_ranking_tie_break_integration()
    test_sections_are_independent()
    test_menu_does_not_swallow_game_messages()
    test_two_users_independent_sessions()
    test_persistence_after_restart()

    print("\n" + "=" * 52)
    print(f"passed={PASSED} failed={FAILED}")
    print("=" * 52)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
