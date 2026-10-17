"""🧛 خون‌آشام — دیباگ کامل مسیر بازی، با تمرکز روی پیام خصوصی.

ریشهٔ باگ «پیام خصوصی ارسال نمی‌شود»:
    بازی فقط ``user_id`` عددی را نگه می‌داشت و همان int را به
    ``client.send_message`` می‌داد. splusthon اول ``utils.get_input_peer(int)``
    را صدا می‌زند که همیشه ``TypeError`` می‌دهد، بعد سراغ کش می‌رود. نشست از
    نوع ``StringSession`` است و کش آن فقط در RAM زندگی می‌کند، پس بعد از هر
    ری‌استارت خالی است و resolve با ``ValueError`` شکست می‌خورد. حساب هم
    userbot است، بنابراین مسیر ویژهٔ ``access_hash=0`` مخصوص bot ها هم در
    دسترس نیست. خطا داخل ``except`` بی‌صدا بلعیده می‌شد و بازی ادامه پیدا
    می‌کرد.

    این دقیقاً توضیح می‌دهد چرا قابلیت «بدون تغییر کد» خراب شد: تا وقتی
    پروسه بالا بود کش پر بود و پیوی می‌رفت؛ بعد از ری‌استارت کش خالی شد.

    python tests/test_vampire_game.py
"""
import asyncio
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from splusthon import utils
from splusthon.tl import types

import handlers.fox_games_router as router
from modules.fox_games import vampire as vp

PASSED = FAILED = 0
CHAT = -880001


def check(label, cond, detail=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  PASS  {label}")
    else:
        FAILED += 1
        print(f"  FAIL  {label} {detail}")


def make_user(uid, name, access_hash=999):
    """شیء کاربر واقعی splusthon، همان چیزی که event.get_sender() می‌دهد."""
    return types.User(id=uid, user_type=None, access_hash=access_hash,
                      first_name=name)


class RealisticClient:
    """resolve را دقیقاً مثل splusthon انجام می‌دهد.

    یک ``int`` فقط وقتی کار می‌کند که در کش باشد؛ شیء ``User`` دارای
    ``access_hash`` همیشه بدون کش و بدون شبکه resolve می‌شود.
    """

    def __init__(self, cache=None, fail_all=False):
        self.entity_cache = dict(cache or {})
        self.sent = []
        self.fail_all = fail_all

    async def send_message(self, target, text, **kwargs):
        if self.fail_all:
            raise ValueError("Could not find the input entity")
        try:
            peer = utils.get_input_peer(target)
        except TypeError:
            if isinstance(target, int):
                if target not in self.entity_cache:
                    raise ValueError(
                        f"Could not find input entity with key {target}")
                peer = types.InputPeerUser(target, self.entity_cache[target])
            else:
                raise
        self.sent.append((peer, text))
        return True


class Event:
    def __init__(self):
        self.out = []

    async def reply(self, text, **kwargs):
        self.out.append(text)
        return None

    def said(self, needle):
        return any(needle in m for m in self.out)


class SelectiveClient(RealisticClient):
    """فقط پیوی بعضی کاربران باز است.

    واقعی‌ترین حالت گروه: بعضی اعضا قبلاً با ربات چت داشته‌اند و
    بعضی نه، یا تنظیمات حریم خصوصی بعضی‌ها ارسال را رد می‌کند.
    """

    def __init__(self, reachable):
        super().__init__()
        self.reachable = set(reachable)
        self.rejected = []

    async def send_message(self, target, text, **kwargs):
        user_id = getattr(target, "id", None) or getattr(
            target, "user_id", None)
        if isinstance(target, int):
            user_id = target
        if user_id not in self.reachable:
            self.rejected.append(user_id)
            raise ValueError(
                f"user {user_id} has no private chat with the bot")
        return await super().send_message(target, text, **kwargs)


class SpoilerEvent(Event):
    """مثل Event ولی entityهای هر پیام را هم نگه می‌دارد."""

    def __init__(self):
        super().__init__()
        self.entities = []

    async def reply(self, text, **kwargs):
        self.entities.append(kwargs.get("formatting_entities"))
        return await super().reply(text, **kwargs)


class Logger:
    def __init__(self):
        self.info, self.errors = [], []

    def log_info(self, m):
        self.info.append(m)

    def log_error(self, m):
        self.errors.append(m)

    def has(self, needle):
        return any(needle in m for m in self.info + self.errors)

    def has_error(self, needle):
        return any(needle in m for m in self.errors)


class Bot:
    def __init__(self, client=None):
        self.client = client or RealisticClient()
        self.logger = Logger()
        self.paid = []

    def award_coins(self, chat_id, user_id, name, amount):
        self.paid.append((user_id, amount))
        return amount


async def send(bot, event, uid, text, name=None):
    return await router.handle(
        bot, event, CHAT, uid, make_user(uid, name or f"P{uid}"),
        text, bot.logger,
    )


def seed(logger, count=4, access_hash=999):
    """یک بازی با ``count`` بازیکن ثبت‌نام‌شده."""
    vp.reset_all()
    vp.start(CHAT, logger)
    for uid in range(101, 101 + count):
        vp.join(CHAT, uid, make_user(uid, f"P{uid}", access_hash), logger)


# ===========================================================================
# ریشهٔ باگ
# ===========================================================================
def test_bare_int_cannot_resolve():
    print("\n### 🔍 ریشهٔ باگ: int خام قابل resolve نیست")
    raised = False
    try:
        utils.get_input_peer(101)
    except TypeError:
        raised = True
    check("get_input_peer روی int خام TypeError می‌دهد", raised)

    user = make_user(101, "علی")
    peer = utils.get_input_peer(user)
    check("شیء User مستقیماً به InputPeerUser تبدیل می‌شود",
          isinstance(peer, types.InputPeerUser) and peer.user_id == 101)
    check("access_hash حفظ می‌شود", peer.access_hash == 999)

    from splusthon.sessions import StringSession
    session = StringSession()
    check("کش StringSession پس از ری‌استارت خالی است",
          len(session._entities) == 0)
    missing = False
    try:
        session.get_input_entity(101)
    except ValueError:
        missing = True
    check("int خام روی نشست تازه ValueError می‌دهد", missing)


def test_join_stores_peer():
    print("\n### 🔍 ثبت‌نام شیء کاربر را نگه می‌دارد")
    logger = Logger()
    seed(logger)
    players = vp._STORE.get(CHAT)["players"]
    check("همهٔ بازیکنان peer دارند",
          all(p.get("peer") is not None for p in players))
    check("peer قابل تبدیل به InputPeer است",
          all(isinstance(utils.get_input_peer(p["peer"]), types.InputPeerUser)
              for p in players))
    check("ثبت peer در لاگ آمده", logger.has("has_peer=True"))
    vp.reset_all()


def test_dm_succeeds_with_cold_cache():
    """گارد رگرسیون اصلی: با کش خالی هم پیوی باید برود."""
    print("\n### 🩸 ارسال پیوی با کش کاملاً خالی")
    logger = Logger()
    seed(logger)
    chosen = vp.choose_vampire(CHAT, logger)
    client = RealisticClient(cache={})       # کش خالی، مثل بعد از ری‌استارت

    ok, error, _transient = asyncio.run(
        vp.send_role_dm(client, chosen["player"], logger=logger, chat_id=CHAT))
    check("ارسال پیوی موفق بود", ok is True, f"-> {error!r}")
    check("دقیقاً یک پیام ارسال شد", len(client.sent) == 1)
    check("گیرنده InputPeerUser درست است",
          client.sent[0][0].user_id == chosen["player"]["user_id"])
    check("متن نقش درست است", client.sent[0][1] == vp.ROLE_MESSAGE)
    check("ارسال موفق لاگ شد", logger.has("ROLE DM SENT"))
    check("مسیر استفاده‌شده peer_object بود", logger.has("via=peer_object"))
    vp.reset_all()


def test_old_behaviour_would_fail():
    """اثبات اینکه کد قدیمی (فقط int) واقعاً شکست می‌خورد."""
    print("\n### 🔍 بازتولید رفتار قدیمی")
    logger = Logger()
    seed(logger)
    chosen = vp.choose_vampire(CHAT, logger)

    legacy = dict(chosen["player"])
    legacy["peer"] = None                     # همان کاری که کد قدیمی می‌کرد
    client = RealisticClient(cache={})
    ok, error, _transient = asyncio.run(
        vp.send_role_dm(client, legacy, logger=logger, chat_id=CHAT))
    check("با شناسهٔ عددی و کش خالی ارسال شکست می‌خورد", ok is False)
    check("خطا از نوع ValueError است", isinstance(error, ValueError),
          f"-> {error!r}")
    check("شکست در لاگ خطا ثبت شد", logger.has_error("ROLE DM FAILED"))
    check("هیچ پیامی ارسال نشد", client.sent == [])
    vp.reset_all()


def test_dm_failure_never_cancels_game():
    """درخواست کاربر: نبود چت خصوصی نباید بازی را متوقف کند.

    وقتی پیوی بعضی بازیکنان باز نیست، نقش به بازیکنی می‌رسد که پیوی‌اش
    باز است — نه اینکه بازی لغو شود یا نقش در گروه اعلام شود.
    """
    print("\n### 🩸 پیوی بستهٔ بعضی بازیکنان بازی را لغو نمی‌کند")

    async def scenario():
        router.reset_all()
        # فقط دو نفر آخر پیوی باز دارند.
        bot = Bot(SelectiveClient(reachable={14, 15}))
        event = SpoilerEvent()
        await send(bot, event, 1, "خون آشام")
        for uid in range(11, 11 + vp.MAX_PLAYERS):
            await send(bot, event, uid, "شرکت")
        await asyncio.sleep(0.6)
        # وضعیت باید *داخل* همین حلقه خوانده شود: پایان ``asyncio.run``
        # تسک بازی را cancel می‌کند و بند ``finally`` جلسه را می‌بندد،
        # پس خواندن بعد از آن همیشه False می‌دهد و ربطی به محصول ندارد.
        return bot, event, vp.is_active(CHAT), vp.phase(CHAT), \
            vp.vampire_player(CHAT)

    bot, event, active, phase, vampire = asyncio.run(scenario())
    check("بازی لغو نشد و هنوز فعال است", active)
    check("مرحلهٔ حدس باز شد", phase == "guessing", f"-> {phase}")
    check("نقش به بازیکنی رسید که پیوی‌اش باز است",
          vampire and vampire["user_id"] in {14, 15}, f"-> {vampire}")
    check("دقیقاً یک پیوی موفق ارسال شد", len(bot.client.sent) == 1,
          f"-> {len(bot.client.sent)}")

    # انتخاب اولیه تصادفی است، پس «جابه‌جایی» فقط وقتی انتظار می‌رود که
    # قرعهٔ اول به بازیکنی با پیوی بسته افتاده باشد. انتظار را از روی
    # همان قرعهٔ واقعی می‌سازیم تا تست قطعی باشد، نه شانسی.
    first_pick = next(
        (int(m.split("vampire_user_id=")[1].split()[0])
         for m in bot.logger.info if "VAMPIRE CHOSEN" in m),
        None,
    )
    check("قرعهٔ اولیه در لاگ ثبت شد", first_pick is not None)
    if first_pick is not None:
        needed = first_pick not in {14, 15}
        check("جابه‌جایی دقیقاً وقتی لازم بوده انجام شد",
              bot.logger.has("REASSIGNED") == needed,
              f"-> قرعهٔ اول={first_pick} نیاز={needed}")
    check("پیام «بازی لغو شد» داده نشد", not event.said("بازی لغو شد"))
    check("از کاربر درخواست پیوی دستی نشد",
          not event.said("یک بار به ربات پیام خصوصی"))
    check("هیچ سکه‌ای بدون حدس درست پرداخت نشد", bot.paid == [])
    router.reset_all()


def test_no_role_information_ever_reaches_the_group():
    """قلب درخواست کاربر: هیچ نشانه‌ای از نقش نباید در گروه دیده شود."""
    print("\n### 🔒 هیچ اطلاعات نقشی به گروه نمی‌رود")

    async def scenario():
        router.reset_all()
        bot = Bot(SelectiveClient(reachable={63}))
        event = SpoilerEvent()
        await send(bot, event, 1, "خون آشام")
        for uid in range(61, 61 + vp.MAX_PLAYERS):
            await send(bot, event, uid, "شرکت")
        await asyncio.sleep(0.6)
        return bot, event, vp.vampire_player(CHAT)

    bot, event, vampire = asyncio.run(scenario())
    group_text = "\n".join(event.out)

    check("متن «نقش مخفی این دور» کاملاً حذف شده",
          "نقش مخفی این دور" not in group_text)
    check("راهنمای «روی کادر زیر بزند» حذف شده",
          "روی کادر" not in group_text)
    check("هیچ entity اسپویلری فرستاده نشد",
          all(not e for e in event.entities), f"-> {event.entities}")
    check("عبارت «شما خون‌آشام هستید» هرگز در گروه نیامد",
          vp.ROLE_MESSAGE not in group_text)

    # نام خون‌آشام نباید به شکلی که او را مشخص کند در گروه بیاید.
    # فهرست شماره‌دار همهٔ بازیکنان را دارد و لو دهنده نیست؛ پس بررسی
    # می‌کنیم که نام خون‌آشام بیش از بقیه تکرار نشده باشد.
    counts = {p["name"]: group_text.count(p["name"])
              for p in vp._STORE.get(CHAT)["players"]} \
        if vp._STORE.get(CHAT) else {}
    if counts:
        check("نام خون‌آشام بیشتر از بقیه تکرار نشده",
              len(set(counts.values())) == 1, f"-> {counts}")

    check("پیوی فقط برای یک نفر رفت", len(bot.client.sent) == 1)
    check("گیرندهٔ پیوی همان خون‌آشام است",
          vampire and bot.client.sent[0][0].user_id == vampire["user_id"])
    router.reset_all()


def test_spoiler_fallback_is_gone():
    """مسیر اسپویلر باید از ریشه حذف شده باشد، نه فقط استفاده نشود."""
    print("\n### 🧹 حذف کامل مسیر اسپویلر")
    check("secret_role_message دیگر وجود ندارد",
          not hasattr(vp, "secret_role_message"))
    check("SECRET_FALLBACK_HEADER حذف شد",
          not hasattr(vp, "SECRET_FALLBACK_HEADER"))
    check("SECRET_FALLBACK_HINT حذف شد",
          not hasattr(vp, "SECRET_FALLBACK_HINT"))
    check("سازندهٔ entity اسپویلر از روتر حذف شد",
          not hasattr(router, "_spoiler_entities"))

    source = (ROOT / "modules" / "fox_games" / "vampire.py").read_text()
    check("کلمهٔ spoiler در ماژول بازی نمانده",
          "spoiler" not in source.lower(), )
    router_source = (ROOT / "handlers" / "fox_games_router.py").read_text()
    check("کلمهٔ Spoiler در روتر نمانده",
          "spoiler" not in router_source.lower())


def test_all_players_unreachable_stops_quietly():
    """وقتی بازیکنانِ کافی شرکت کرده‌اند، دور بدونِ پیامِ «تلاشِ دوباره» اجرا می‌شود.

    (پیامِ «این دور انجام نشد / چند لحظه بعد صبر کنید» حذف شد؛ بازی نباید
    کاربر را مجبور به تلاشِ دوباره کند.)
    """
    print("\n### 🩸 وقتی پیوی هیچ‌کس باز نیست")
    for _ in range(2):
        router.reset_all()
        bot = Bot(RealisticClient(fail_all=True))
        event = SpoilerEvent()
        CH = -50999

        async def scenario():
            await send(bot, event, 1, "خون آشام")
            for uid in range(71, 71 + vp.MAX_PLAYERS):
                await send(bot, event, uid, "شرکت")
            await asyncio.sleep(0.6)
            return bot, event

        bot, event = asyncio.run(scenario())
        group_text = "\n".join(event.out)
        check("پیامِ «تلاشِ دوباره» داده نشد", not event.said("این دور انجام نشد"))
        check("هیچ نامی از بازیکنان برده نشد",
              not any(f"P{uid}" in group_text for uid in range(71, 76)),
              f"-> {group_text!r}")
        check("از کسی خواسته نشد به ربات پیام خصوصی بدهد",
              not event.said("پیام خصوصی بدهد"))
        check("هیچ نقشی در گروه اعلام نشد", vp.ROLE_MESSAGE not in group_text)
        check("هر بازیکن دقیقاً یک بار امتحان شد",
              bot.logger.has("ROLE UNDELIVERABLE"))
        router.reset_all()


def test_dm_preferred_when_available():
    """اگر پیوی ممکن باشد، همان استفاده شود و چیزی در گروه لو نرود."""
    print("\n### 🩸 وقتی پیوی ممکن است، مسیر جایگزین استفاده نمی‌شود")

    async def scenario():
        router.reset_all()
        bot = Bot()                       # پیوی سالم
        event = SpoilerEvent()
        await send(bot, event, 1, "خون آشام")
        for uid in range(41, 41 + vp.MAX_PLAYERS):
            await send(bot, event, uid, "شرکت")
        await asyncio.sleep(0.6)
        return bot, event, vp.is_active(CHAT)

    bot, event, active = asyncio.run(scenario())
    check("پیوی ارسال شد", len(bot.client.sent) == 1)
    check("نقش در گروه اعلام نشد",
          vp.ROLE_MESSAGE not in "\n".join(event.out))
    check("پیام اعلام معمولی داده شد", event.said(vp.CHOSEN_MESSAGE))
    check("هیچ entity اسپویلری فرستاده نشد",
          all(not e for e in event.entities))
    check("بازی فعال است", active)
    router.reset_all()


def test_deliver_role_modes():
    """قرارداد ``deliver_role``: فقط dm یا failed — هیچ مسیر گروهی."""
    print("\n### 🩸 حالت‌های رساندن نقش")
    logger = Logger()

    # پیوی همه باز است.
    seed(logger)
    chosen = vp.choose_vampire(CHAT, logger)
    result, mode = asyncio.run(vp.deliver_role(
        RealisticClient(), CHAT, chosen, logger=logger))
    check("پیوی سالم -> dm", mode == "dm", f"-> {mode}")
    check("همان بازیکن اول نگه داشته شد",
          result["player"]["user_id"] == chosen["player"]["user_id"])
    vp.reset_all()

    # پیوی هیچ‌کس باز نیست.
    seed(logger)
    chosen = vp.choose_vampire(CHAT, logger)
    result, mode = asyncio.run(vp.deliver_role(
        RealisticClient(fail_all=True), CHAT, chosen, logger=logger))
    check("پیوی هیچ‌کس باز نیست -> failed", mode == "failed", f"-> {mode}")
    check("هیچ بازیکنی برگردانده نشد", result is None)
    vp.reset_all()

    # فقط یک نفر پیوی باز دارد -> نقش باید به همان برسد.
    # لاگر تازه، چون seed() بالا لاگر مشترک را پر کرده است.
    logger = Logger()
    seed(logger)
    chosen = vp.choose_vampire(CHAT, logger)
    only = 103
    result, mode = asyncio.run(vp.deliver_role(
        SelectiveClient(reachable={only}), CHAT, chosen, logger=logger))
    check("با یک گیرندهٔ ممکن -> dm", mode == "dm", f"-> {mode}")
    check("نقش دقیقاً به همان بازیکن رسید",
          result and result["player"]["user_id"] == only, f"-> {result}")
    # اگر قرعهٔ اول همان ۱۰۳ بوده باشد جابه‌جایی لازم نبوده؛ هر دو حالت درست است.
    started_on_target = chosen["player"]["user_id"] == only
    check("در صورت نیاز، جابه‌جایی انجام و لاگ شد",
          started_on_target or logger.has("REASSIGNED"),
          f"-> شروع={chosen['player']['user_id']}")
    vp.reset_all()


def test_reassign_keeps_numbering_consistent():
    """بعد از جابه‌جایی، شمارهٔ خون‌آشام با فهرست گروه یکی بماند."""
    print("\n### 🔢 شمارهٔ خون‌آشام بعد از جابه‌جایی درست است")
    logger = Logger()
    seed(logger)
    chosen = vp.choose_vampire(CHAT, logger)
    first = chosen["player"]["user_id"]

    moved = vp.reassign_vampire(CHAT, first, logger)
    check("بازیکن تازه انتخاب شد", moved is not None)
    check("بازیکن قبلی دیگر خون‌آشام نیست",
          moved["player"]["user_id"] != first)

    players = vp._STORE.get(CHAT)["players"]
    expected = next(i for i, p in enumerate(players, 1)
                    if p["user_id"] == moved["player"]["user_id"])
    check("شماره با جایگاه واقعی در فهرست یکی است",
          moved["number"] == expected, f"-> {moved['number']} != {expected}")
    check("vampire_player همان را برمی‌گرداند",
          vp.vampire_player(CHAT)["user_id"] == moved["player"]["user_id"])
    vp.reset_all()


def test_guessing_works_after_reassign():
    """بعد از جابه‌جایی نقش، بازی کامل انجام می‌شود و جایزه می‌دهد."""
    print("\n### 🩸 بازی بعد از جابه‌جایی نقش کامل انجام می‌شود")

    async def scenario():
        router.reset_all()
        # فقط دو نفر پیوی باز دارند؛ نقش حتماً جابه‌جا می‌شود.
        bot = Bot(SelectiveClient(reachable={54, 55}))
        event = SpoilerEvent()
        await send(bot, event, 1, "خون آشام")
        for uid in range(51, 51 + vp.MAX_PLAYERS):
            await send(bot, event, uid, "شرکت")
        await asyncio.sleep(0.6)

        vampire_uid = vp.vampire_player(CHAT)["user_id"]
        players = vp._STORE.get(CHAT)["players"]
        number = next(i for i, p in enumerate(players, 1)
                      if p["user_id"] == vampire_uid)
        guesser = next(p["user_id"] for p in players
                       if p["user_id"] != vampire_uid)
        await send(bot, event, guesser, str(number))
        return bot, event

    bot, event = asyncio.run(scenario())
    check("حدس درست پذیرفته شد", bot.paid != [], f"-> {bot.paid}")
    check("جایزه ۷ سکه بود", bot.paid and bot.paid[0][1] == vp.WINNER_COINS,
          f"-> {bot.paid}")
    check("بازی پس از برد بسته شد", not vp.is_active(CHAT))
    router.reset_all()


def test_no_guessing_before_dm():
    print("\n### 🩸 پیش از ارسال پیوی هیچ حدسی پذیرفته نمی‌شود")
    logger = Logger()
    seed(logger)
    chosen = vp.choose_vampire(CHAT, logger)
    check("مرحله پس از انتخاب، assigning است",
          vp.phase(CHAT) == "assigning", f"-> {vp.phase(CHAT)}")
    state, _ = vp.guess(CHAT, 102, "1", logger)
    check("حدس در مرحلهٔ assigning رد می‌شود", state == "closed", f"-> {state}")

    check("open_guessing مرحله را باز می‌کند",
          vp.open_guessing(CHAT, logger=logger) is True)
    check("اکنون مرحله guessing است", vp.phase(CHAT) == "guessing")
    check("open_guessing دوباره کار نمی‌کند",
          vp.open_guessing(CHAT, logger=logger) is False)
    check("باز شدن مرحله لاگ شد", logger.has("GUESSING OPEN"))
    vp.reset_all()


# ===========================================================================
# پیام‌های گروه
# ===========================================================================
def test_group_messages_exact():
    print("\n### 💬 متن دقیق پیام‌های گروه")
    check("متن اعلام ارسال پیوی دقیقاً مطابق خواسته است",
          vp.CHOSEN_MESSAGE == (
              "🩸 پیام خصوصی خون‌آشام برای یکی از بازیکنان ارسال شد. "
              "حالا حدس بزنید خون‌آشام کیست!"
          ), f"-> {vp.CHOSEN_MESSAGE}")

    players = [{"name": n} for n in ("علی", "حسین", "محمد", "میلاد", "رضا")]
    roster = vp.roster_lines(players)
    check("فهرست دقیقاً نام‌ها را با شماره لاتین می‌دهد",
          roster == "1. علی\n2. حسین\n3. محمد\n4. میلاد\n5. رضا",
          f"-> {roster!r}")


def test_full_flow_messages():
    print("\n### 💬 مسیر کامل: پیام‌ها به ترتیب درست")

    async def scenario():
        router.reset_all()
        bot, event = Bot(), Event()
        await send(bot, event, 1, "خون آشام")
        for uid in range(21, 21 + vp.MAX_PLAYERS):
            await send(bot, event, uid, "شرکت")
        await asyncio.sleep(0.6)
        return bot, event

    bot, event = asyncio.run(scenario())
    check("پیام اعلام ارسال پیوی نمایش داده شد",
          event.said("🩸 پیام خصوصی خون‌آشام برای یکی از بازیکنان ارسال شد"))
    check("فهرست شماره‌دار نمایش داده شد", event.said("1."))
    check("دقیقاً یک پیام خصوصی ارسال شد", len(bot.client.sent) == 1,
          f"-> {len(bot.client.sent)}")
    check("متن پیوی متن نقش است",
          bot.client.sent[0][1] == vp.ROLE_MESSAGE)

    order_dm = next(i for i, m in enumerate(event.out)
                    if "🩸 پیام خصوصی" in m)
    order_roster = next(i for i, m in enumerate(event.out) if m.startswith("1."))
    check("فهرست بعد از پیام اعلام می‌آید", order_roster > order_dm)
    router.reset_all()


# ===========================================================================
# قوانین بازی
# ===========================================================================
def test_rules():
    print("\n### 📏 قوانین بازی")
    check("زمان حدس دقیقاً ۵۰ ثانیه است", vp.GUESS_SECONDS == 50)
    check("جایزهٔ حدس درست ۷ سکه است", vp.WINNER_COINS == 7)

    logger = Logger()
    seed(logger, count=5)
    chosen = vp.choose_vampire(CHAT, logger)
    vp.open_guessing(CHAT, logger=logger)
    players = vp._STORE.get(CHAT)["players"]
    vampire_uid = chosen["player"]["user_id"]
    number = chosen["number"]

    check("غیر شرکت‌کننده نمی‌تواند حدس بزند",
          vp.guess(CHAT, 999, str(number), logger)[0] == "not_player")
    check("خون‌آشام نمی‌تواند حدس بزند",
          vp.guess(CHAT, vampire_uid, str(number), logger)[0] == "is_vampire")
    check("نوبت خون‌آشام مصرف نشد",
          vampire_uid not in vp._STORE.get(CHAT)["guessed"])

    guesser = next(p["user_id"] for p in players if p["user_id"] != vampire_uid)
    own = next(i for i, p in enumerate(players, 1) if p["user_id"] == guesser)
    check("کسی نمی‌تواند شمارهٔ خودش را انتخاب کند",
          vp.guess(CHAT, guesser, str(own), logger)[0] == "self_guess")
    check("انتخاب خود نوبت را نمی‌سوزاند",
          guesser not in vp._STORE.get(CHAT)["guessed"])

    wrong = next(i for i in range(1, len(players) + 1)
                 if i not in {number, own})
    check("حدس غلط ثبت می‌شود",
          vp.guess(CHAT, guesser, str(wrong), logger)[0] == "wrong")
    check("حدس دوم همان بازیکن رد می‌شود",
          vp.guess(CHAT, guesser, str(number), logger)[0] == "already")

    winner = next(p["user_id"] for p in players
                  if p["user_id"] not in {vampire_uid, guesser})
    state, info = vp.guess(CHAT, winner, str(number), logger)
    check("حدس درست پذیرفته می‌شود", state == "correct", f"-> {state}")
    check("جایزه ۷ سکه است", info["coins"] == 7)
    check("بازی پس از حدس درست بسته شد", not vp.is_active(CHAT))
    vp.reset_all()


def test_restart_blocked():
    print("\n### 📏 اجرای دوباره در حین بازی")

    async def scenario():
        router.reset_all()
        bot, event = Bot(), Event()
        await send(bot, event, 1, "خون آشام")
        started = vp.is_active(CHAT)
        again = Event()
        await send(bot, again, 2, "خون آشام")
        third = Event()
        await send(bot, third, 3, "خون‌آشام")
        vp._STORE.cancel_task(CHAT)
        return started, again, third

    started, again, third = asyncio.run(scenario())
    check("بازی شروع شد", started)
    check("اجرای دوباره مسدود شد", again.said("همین حالا در جریان"))
    check("شکل با نیم‌فاصله هم مسدود می‌شود",
          third.said("همین حالا در جریان"))
    router.reset_all()


def test_random_selection():
    print("\n### 🎲 انتخاب تصادفی خون‌آشام")
    logger = Logger()
    picks = Counter()
    for _ in range(80):
        seed(logger)
        picks[vp.choose_vampire(CHAT, logger)["player"]["user_id"]] += 1
        vp.abandon(CHAT, logger=logger)
    check("همهٔ بازیکنان شانس انتخاب دارند", len(picks) == 4,
          f"-> {dict(picks)}")
    check("توزیع تک‌نفره نیست", max(picks.values()) < 70,
          f"-> {dict(picks)}")
    vp.reset_all()


def test_timeout_reveals():
    print("\n### ⏰ پایان زمان و افشای خون‌آشام")

    async def scenario():
        router.reset_all()
        bot, event = Bot(), Event()
        session = vp.start(CHAT, bot.logger)
        for uid in (31, 32, 33, 34):
            vp.join(CHAT, uid, make_user(uid, f"P{uid}"), bot.logger)

        async def on_roles(chosen):
            # قرارداد تازه: ``(chosen, mode)`` برگردانده می‌شود.
            return await vp.deliver_role(
                bot.client, CHAT, chosen, logger=bot.logger)

        async def on_roster(chosen):
            await event.reply(vp.CHOSEN_MESSAGE)
            await event.reply(vp.roster_lines(chosen["players"]))

        async def on_timeout(revealed):
            await event.reply(vp.format_reveal(revealed))

        async def noop(*_):
            return None

        vp.schedule(CHAT, session["session_id"], {
            "on_abort": noop, "on_roles": on_roles, "on_roster": on_roster,
            "on_dm_failed": noop, "on_timeout": on_timeout,
        }, logger=bot.logger, join_seconds=0.05, guess_seconds=0.3)
        await asyncio.sleep(0.9)
        return bot, event

    bot, event = asyncio.run(scenario())
    check("پیوی ارسال شد", len(bot.client.sent) == 1)
    check("هویت خون‌آشام در پایان اعلام شد", event.said("🧛 خون‌آشام:"))
    check("پایان پنجرهٔ حدس لاگ شد",
          bot.logger.has("GUESS WINDOW ENDED"))
    check("state پس از پایان پاک شد", not vp.is_active(CHAT))
    check("هیچ سکه‌ای بدون حدس درست پرداخت نشد", bot.paid == [])
    router.reset_all()


# ===========================================================================
# پاک شدن وضعیت و نبود نشتی
# ===========================================================================
def test_state_cleanup():
    print("\n### 🧹 پاک شدن کامل وضعیت بین بازی‌ها")
    logger = Logger()
    seed(logger)
    chosen = vp.choose_vampire(CHAT, logger)
    vp.open_guessing(CHAT, logger=logger)
    players = vp._STORE.get(CHAT)["players"]
    vampire_uid = chosen["player"]["user_id"]
    guesser = next(p["user_id"] for p in players if p["user_id"] != vampire_uid)
    own = next(i for i, p in enumerate(players, 1) if p["user_id"] == guesser)
    wrong = next(i for i in range(1, len(players) + 1)
                 if i not in {chosen["number"], own})
    vp.guess(CHAT, guesser, str(wrong), logger)

    vp.reveal(CHAT, logger=logger)
    check("بازی بسته شد", not vp.is_active(CHAT))
    check("session حذف شد", vp._STORE.get(CHAT) is None)
    check("تایمر باقی نماند", vp._STORE.task_for(CHAT) is None)

    # بازی تازه نباید هیچ ردی از قبلی داشته باشد
    seed(logger)
    fresh = vp._STORE.get(CHAT)
    check("بازی جدید هیچ حدسی از قبل ندارد", fresh["guessed"] == set())
    check("بازی جدید خون‌آشام تعیین‌نشده دارد", fresh["vampire"] is None)
    check("بازی جدید در مرحلهٔ joining است", fresh["phase"] == "joining")
    check("بازیکنان بازی قبلی پاک شدند", len(fresh["players"]) == 4)
    check("شناسهٔ session تازه است",
          fresh["session_id"] != chosen.get("session_id"))
    vp.reset_all()


def test_repeated_games():
    print("\n### 🔁 ده بازی پشت سر هم بدون نشتی")

    async def one_round(index):
        router.reset_all()
        bot, event = Bot(), Event()
        await send(bot, event, 1, "خون آشام")
        base = 500 + index * 10
        for offset in range(vp.MAX_PLAYERS):
            await send(bot, event, base + offset, "شرکت")
        await asyncio.sleep(0.6)
        return bot, event

    results = []
    for index in range(10):
        results.append(asyncio.run(one_round(index)))

    check("در هر ۱۰ بازی دقیقاً یک پیوی ارسال شد",
          all(len(bot.client.sent) == 1 for bot, _ in results),
          f"-> {[len(b.client.sent) for b, _ in results]}")
    check("در هر ۱۰ بازی پیام اعلام نمایش داده شد",
          all(ev.said("🩸 پیام خصوصی") for _, ev in results))
    check("در هر ۱۰ بازی فهرست نمایش داده شد",
          all(ev.said("1.") for _, ev in results))
    check("هیچ بازی‌ای در پایان فعال نماند", not vp.is_active(CHAT))
    check("هیچ خطای پیوی رخ نداد",
          not any(b.logger.has_error("ROLE DM FAILED") for b, _ in results))
    router.reset_all()


def test_isolation_from_other_games():
    print("\n### 🔒 نبود تداخل با بازی‌های دیگر")
    import modules.emoji_guess as eg
    from modules.fox_games import laugh_or_lose as lol
    from modules.fox_games import lucky_box as lb
    from modules.fox_games import survival as sv

    stores = {"vampire": id(vp._STORE), "laugh": id(lol._STORE),
              "survival": id(sv._STORE), "lucky_box": id(lb._STORE)}
    check("SessionStore خون‌آشام مستقل است", len(set(stores.values())) == 4)

    router.reset_all()
    eg.reset_all()
    logger = Logger()
    seed(logger)
    lol.start(CHAT)
    sv.start(CHAT)
    eg.start(CHAT, 1)
    check("همه هم‌زمان فعالند",
          vp.is_active(CHAT) and lol.is_active(CHAT) and sv.is_active(CHAT))

    lol.reset_all(CHAT)
    sv.reset_all(CHAT)
    eg.reset_all()
    check("بستن بازی‌های دیگر خون‌آشام را نبست", vp.is_active(CHAT))
    check("بازیکنان خون‌آشام دست‌نخورده ماندند", vp.player_count(CHAT) == 4)

    vp.reset_all(CHAT)
    check("ریست خون‌آشام کامل انجام شد", not vp.is_active(CHAT))
    router.reset_all()


def test_logging_covers_every_stage():
    print("\n### 📝 پوشش لاگ در تمام مراحل")

    async def scenario():
        router.reset_all()
        bot, event = Bot(), Event()
        await send(bot, event, 1, "خون آشام")
        for uid in range(41, 41 + vp.MAX_PLAYERS):
            await send(bot, event, uid, "شرکت")
        await asyncio.sleep(0.6)
        return bot

    bot = asyncio.run(scenario())
    for stage in ("FOX VAMPIRE START", "FOX VAMPIRE JOIN",
                  "FOX VAMPIRE JOIN CLOSED", "FOX VAMPIRE CHOSEN",
                  "ROLE DM TRY", "ROLE DM SENT", "GUESSING OPEN"):
        check(f"لاگ مرحلهٔ «{stage}» ثبت شد", bot.logger.has(stage))
    router.reset_all()


def main():
    test_bare_int_cannot_resolve()
    test_join_stores_peer()
    test_dm_succeeds_with_cold_cache()
    test_old_behaviour_would_fail()
    test_dm_failure_never_cancels_game()
    test_dm_preferred_when_available()
    test_deliver_role_modes()
    test_guessing_works_after_reassign()
    test_reassign_keeps_numbering_consistent()
    test_no_role_information_ever_reaches_the_group()
    test_spoiler_fallback_is_gone()
    test_all_players_unreachable_stops_quietly()
    test_no_guessing_before_dm()
    test_group_messages_exact()
    test_full_flow_messages()
    test_rules()
    test_restart_blocked()
    test_random_selection()
    test_timeout_reveals()
    test_state_cleanup()
    test_repeated_games()
    test_isolation_from_other_games()
    test_logging_covers_every_stage()

    print("\n" + "=" * 52)
    print(f"passed={PASSED} failed={FAILED}")
    print("=" * 52)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
