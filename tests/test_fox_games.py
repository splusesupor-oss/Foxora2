"""چهار بازی جدید Fox AI: استقلال، Race Condition، Timeout و جوایز.

    python tests/test_fox_games.py
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import handlers.fox_games_router as router
from modules.fox_games import laugh_or_lose as ll
from modules.fox_games import lucky_box as lb
from modules.fox_games import survival as sv
from modules.fox_games import vampire as vp
from modules.fox_games.survival_questions import LEVELS, all_questions

PASSED = FAILED = 0
CHAT = -100900


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
        self.first_name = name
        self.last_name = None
        self.username = username


class Event:
    def __init__(self):
        self.out = []

    async def reply(self, text, **kwargs):
        self.out.append(text)
        return None

    def said(self, needle):
        return any(needle in message for message in self.out)


class Client:
    def __init__(self):
        self.dm = []

    async def send_message(self, target, text, **kwargs):
        self.dm.append((target, text))


class Logger:
    def __init__(self):
        self.info = []
        self.errors = []

    def log_info(self, m):
        self.info.append(m)

    def log_error(self, m):
        self.errors.append(m)

    def has(self, needle):
        return any(needle in m for m in self.info + self.errors)


class Bot:
    def __init__(self):
        self.client = Client()
        self.logger = Logger()
        self.paid = []

    def award_coins(self, chat_id, user_id, name, amount):
        self.paid.append((user_id, amount))


async def send(bot, event, uid, text, name=None, username=None):
    return await router.handle(
        bot, event, CHAT, uid, User(uid, name, username), text, bot.logger
    )


async def noop(*args, **kwargs):
    pass


# ==========================================================================
# 😂 بخند یا بباز
# ==========================================================================
def test_laugh():
    print("\n### 😂 بخند یا بباز")

    async def scenario():
        router.reset_all()
        bot, event = Bot(), Event()
        await send(bot, event, 1, "بخند یا بباز")
        started = ll.is_active(CHAT)

        again = Event()
        await send(bot, again, 2, "بخند یا بباز")
        blocked = again.said("همین حالا در جریان")

        session_id = ll._STORE.get(CHAT)["session_id"]
        early = await send(bot, event, 5, "😂", "Early")
        ll.open_round(CHAT, session_id)

        first = await send(bot, event, 10, "😂 خیلی خنده‌دار بود", "Ali")
        second = await send(bot, event, 11, "🤣", "Reza")
        return bot, event, started, blocked, early, first, second

    bot, event, started, blocked, early, first, second = asyncio.run(scenario())
    check("بازی شروع شد", started)
    check("اجرای دوباره مسدود شد", blocked)
    check("خنده پیش از پایان شمارش پذیرفته نمی‌شود", not early)
    check("اولین خنده برنده است", first)
    check("نفر دوم نادیده گرفته می‌شود", not second)
    check("برنده ۳ سکه گرفت", bot.paid == [(10, 3)], f"-> {bot.paid}")
    check("بازی بلافاصله بسته شد", not ll.is_active(CHAT))
    check("نام برنده اعلام شد", event.said("برنده"))


def test_laugh_emoji_set():
    print("\n### 😂 ایموجی‌های مجاز")
    for emoji in ("😂", "🤣", "😆", "😹", "😄", "😁"):
        check(f"{emoji} پذیرفته می‌شود", ll.contains_laugh(emoji))
    for emoji in ("😀", "🙂", "❤️", "سلام"):
        check(f"{emoji} پذیرفته نمی‌شود", not ll.contains_laugh(emoji))


def test_laugh_timeout():
    print("\n### 😂 پایان بدون برنده")

    async def scenario():
        router.reset_all()
        bot, event = Bot(), Event()
        session = ll.start(CHAT, bot.logger)
        fired = []

        async def on_timeout():
            fired.append(True)

        ll.schedule(CHAT, session["session_id"], noop, noop, on_timeout,
                    logger=bot.logger, countdown=0, timeout=0.1)
        await asyncio.sleep(0.4)
        return fired

    fired = asyncio.run(scenario())
    check("پیام پایان ارسال شد", fired == [True])
    check("session آزاد شد", not ll.is_active(CHAT))


# ==========================================================================
# 🏕 بقا
# ==========================================================================
def test_survival_registration():
    print("\n### 🏕 ثبت‌نام بقا")

    async def scenario():
        router.reset_all()
        bot, event = Bot(), Event()
        await send(bot, event, 1, "بقا")
        again = Event()
        await send(bot, again, 2, "بقا")
        blocked = again.said("همین حالا در جریان")
        # تایمر واقعی ثبت‌نام را می‌بندد؛ برای این تست آن را متوقف می‌کنیم تا
        # فقط منطق ثبت‌نام سنجیده شود.
        sv._STORE.cancel_task(CHAT)
        for uid in (21, 22, 23, 24):
            await send(bot, event, uid, "شرکت", f"P{uid}")
        dup = Event()
        await router.handle(bot, dup, CHAT, 21, User(21, "P21"), "شرکت", bot.logger)
        overflow = Event()
        await router.handle(bot, overflow, CHAT, 25, User(25, "P25"),
                            "شرکت", bot.logger)
        return blocked, dup, overflow

    blocked, dup, overflow = asyncio.run(scenario())
    check("اجرای دوباره مسدود شد", blocked)
    check("۴ نفر ثبت شدند", sv.player_count(CHAT) == 4, f"-> {sv.player_count(CHAT)}")
    check("ظرفیت تکمیل است", sv.is_full(CHAT))
    check("ثبت‌نام تکراری رد شد", dup.said("قبلاً ثبت‌نام"))
    check("نفر پنجم رد شد", overflow.said("ظرفیت تکمیل"))
    router.reset_all()


def test_survival_full_game():
    print("\n### 🏕 بازی کامل بقا")

    async def scenario():
        router.reset_all()
        bot = Bot()
        sv.start(CHAT, bot.logger)
        sid = sv._STORE.get(CHAT)["session_id"]
        for uid in (1, 2, 3, 4):
            sv.join(CHAT, uid, User(uid, f"P{uid}"), bot.logger)
        finished = []

        async def on_finish(champion):
            finished.append(champion)

        sv.schedule(CHAT, sid, {
            "on_abort": noop, "on_begin": noop, "on_question": noop,
            "on_eliminated": noop, "on_finish": on_finish,
        }, logger=bot.logger, join_seconds=0.05, answer_seconds=0.2)

        for _ in range(20):
            await asyncio.sleep(0.12)
            session = sv._STORE.get(CHAT)
            if not session or not session.get("question"):
                break
            alive = [p for p in session["players"].values() if p["alive"]]
            if len(alive) <= 1:
                break
            sv.answer(CHAT, alive[0]["user_id"], session["question"]["answer"])
            await asyncio.sleep(0.22)
        await asyncio.sleep(0.8)
        return bot, finished

    bot, finished = asyncio.run(scenario())
    check("بازی پایان یافت", len(finished) == 1, f"-> {len(finished)}")
    check("برنده مشخص شد", finished and finished[0] is not None,
          f"-> {finished}")
    check("session بسته شد", not sv.is_active(CHAT))
    check("حذف بازیکن لاگ شد", bot.logger.has("FOX SURVIVAL ELIMINATED"))
    router.reset_all()


def test_survival_wrong_answer_eliminates():
    print("\n### 🏕 پاسخ اشتباه = حذف")
    router.reset_all()
    logger = Logger()
    sv.start(CHAT, logger)
    for uid in (1, 2, 3, 4):
        sv.join(CHAT, uid, User(uid, f"P{uid}"), logger)
    sv.begin_rounds(CHAT, logger)
    sv.next_question(CHAT, logger)
    session = sv._STORE.get(CHAT)
    correct = session["question"]["answer"]

    state, _ = sv.answer(CHAT, 1, correct)
    check("پاسخ درست پذیرفته شد", state == "correct")
    state, _ = sv.answer(CHAT, 2, "پاسخ کاملا اشتباه")
    check("پاسخ اشتباه حذف می‌کند", state == "wrong")
    state, _ = sv.answer(CHAT, 1, correct)
    check("پاسخ دوم همان کاربر رد می‌شود", state == "already")
    state, _ = sv.answer(CHAT, 99, correct)
    check("غیر بازیکن پذیرفته نمی‌شود", state == "not_player")
    removed = sv.eliminate_silent(CHAT, logger)
    check("ساکت‌ها حذف شدند", len(removed) == 2, f"-> {len(removed)}")
    check("فقط یک بازمانده", len(sv.alive_players(CHAT)) == 1)
    router.reset_all()


def test_survival_questions():
    print("\n### 🏕 بانک سوال")
    questions = all_questions()
    check(f"بانک بزرگ است ({len(questions)} سوال)", len(questions) >= 50)
    texts = [q[0] for q in questions]
    check("هیچ سوال تکراری نیست", len(texts) == len(set(texts)))
    check("چند سطح سختی دارد", len(LEVELS) >= 4, f"-> {len(LEVELS)}")
    check("همهٔ سوال‌ها پاسخ دارند", all(q[1] for q in questions))

    router.reset_all()
    logger = Logger()
    sv.start(CHAT, logger)
    for uid in (1, 2):
        sv.join(CHAT, uid, User(uid, f"P{uid}"), logger)
    sv.begin_rounds(CHAT, logger)
    seen = []
    for _ in range(8):
        question = sv.next_question(CHAT, logger)
        if question is None:
            break
        seen.append(question["text"])
    check("سوال‌ها در یک بازی تکرار نمی‌شوند",
          len(seen) == len(set(seen)), f"-> {len(seen)} vs {len(set(seen))}")
    check("سطح هر مرحله بالا می‌رود",
          sv._STORE.get(CHAT)["level"] == len(seen))
    router.reset_all()


# ==========================================================================
# 🎁 جعبه شانسی
# ==========================================================================
def test_lucky_box_layout():
    print("\n### 🎁 چیدمان جعبه‌ها")
    for _ in range(30):
        boxes = lb.build_boxes()
        empties = sum(1 for value in boxes.values() if value == 0)
        prizes = [v for v in boxes.values() if v > 0]
        if len(boxes) != 9 or empties != 4 or len(prizes) != 5:
            check("چیدمان درست است", False, f"-> {boxes}")
            return
        if not all(1 <= p <= 15 for p in prizes):
            check("جوایز بین ۱ تا ۱۵", False, f"-> {prizes}")
            return
    check("همیشه ۹ جعبه", True)
    check("همیشه ۴ پوچ و ۵ جایزه", True)
    check("جوایز بین ۱ تا ۱۵ سکه", True)
    layouts = {tuple(sorted(lb.build_boxes().items())) for _ in range(20)}
    check("هیچ الگوی ثابتی نیست", len(layouts) > 1, f"-> {len(layouts)}")


def test_lucky_box_play_and_quota():
    print("\n### 🎁 بازی و سهمیهٔ روزانه")

    async def scenario():
        router.reset_all()
        lb.reset_all(clear_quota=True)
        bot = Bot()
        user = 30

        first = Event()
        await send(bot, first, user, "جعبه شانسی")
        board = first.said("┌───┬───┬───┐")
        other = await send(bot, first, 99, "5")
        await send(bot, first, user, "5")
        opened = first.said("باز شد")

        second = Event()
        await send(bot, second, user, "جعبه شانسی")
        await send(bot, second, user, "3")

        third = Event()
        await send(bot, third, user, "جعبه شانسی")
        return bot, board, other, opened, third

    bot, board, other, opened, third = asyncio.run(scenario())
    check("جدول نمایش داده شد", board)
    check("فقط صاحب بازی می‌تواند انتخاب کند", not other)
    check("جعبه باز شد", opened)
    check("بار سوم مسدود شد", third.said("سهمیه امروز شما تمام شده است"))
    check("زمان باقی‌مانده واقعی نمایش داده شد", third.said("دیگر"))
    check("سهمیه صفر شد", lb.remaining_plays(30) == 0)
    check("زمان انتظار مثبت است", lb.seconds_until_next(30) > 0)
    lb.reset_all(clear_quota=True)


def test_lucky_box_wait_format():
    print("\n### 🎁 قالب زمان انتظار")
    check("ساعت و دقیقه", "ساعت" in lb.format_wait(8 * 3600 + 23 * 60)
          and "دقیقه" in lb.format_wait(8 * 3600 + 23 * 60))
    check("فقط دقیقه", lb.format_wait(600).endswith("دقیقه دیگر"))
    check("کمتر از یک دقیقه", "کمتر از یک دقیقه" in lb.format_wait(30))


# ==========================================================================
# 🧛 خون‌آشام
# ==========================================================================
def test_vampire_full_game():
    print("\n### 🧛 بازی کامل خون‌آشام")

    async def scenario():
        router.reset_all()
        bot, event = Bot(), Event()
        await send(bot, event, 1, "خون آشام")
        again = Event()
        await send(bot, again, 2, "خون آشام")
        blocked = again.said("همین حالا در جریان")

        names = {41: "علی", 42: "حسین", 43: "محمد", 44: "میلاد"}
        for uid, name in names.items():
            await send(bot, event, uid, "شرکت", name)
        session_id = vp._STORE.get(CHAT)["session_id"]

        async def on_roles(chosen):
            await event.reply(
                "🧛 شرکت‌کنندگان:\n\n"
                + vp.roster_lines(chosen["players"])
                + f"\n\n{vp.CHOSEN_MESSAGE}"
            )
            await bot.client.send_message(chosen["player"]["user_id"],
                                          vp.ROLE_MESSAGE)
            # قرارداد تازهٔ ``on_roles``: ``(chosen, mode)``.
            return chosen, "dm"

        vp.schedule(CHAT, session_id, {
            "on_abort": noop, "on_roles": on_roles, "on_timeout": noop,
        }, logger=bot.logger, join_seconds=0.05, guess_seconds=5)
        await asyncio.sleep(0.3)

        session = vp._STORE.get(CHAT)
        index = session["vampire"]
        vampire_uid = session["players"][index]["user_id"]
        number = index + 1

        self_state, _ = vp.guess(CHAT, vampire_uid, str(number), bot.logger)
        self_guess = self_state == "is_vampire"
        others = [p["user_id"] for p in session["players"]
                  if p["user_id"] != vampire_uid]
        # عدد اشتباه باید نه خون‌آشام باشد و نه خودِ حدس‌زننده: انتخاب خود
        # شخص اکنون رد می‌شود و نوبتش را مصرف نمی‌کند.
        own_number = next(i for i, p in enumerate(session["players"], 1)
                          if p["user_id"] == others[0])
        wrong_number = next(i for i in range(1, len(session["players"]) + 1)
                            if i not in {number, own_number})
        await send(bot, event, others[0], str(wrong_number))
        second_guess = await send(bot, event, others[0], str(number))
        await send(bot, event, others[1], str(number))
        return bot, event, blocked, self_guess, second_guess, vampire_uid, others

    (bot, event, blocked, self_guess, second_guess,
     vampire_uid, others) = asyncio.run(scenario())
    check("اجرای دوباره مسدود شد", blocked)
    check("فقط خون‌آشام پیام خصوصی گرفت",
          len(bot.client.dm) == 1 and bot.client.dm[0][0] == vampire_uid,
          f"-> {bot.client.dm}")
    check("متن نقش درست است", bot.client.dm[0][1] == vp.ROLE_MESSAGE)
    check("فهرست شماره‌دار با ارقام لاتین نمایش داده شد", event.said("1."))
    check("پیام اعلام ارسال پیوی نمایش داده شد",
          event.said("🩸 پیام خصوصی خون‌آشام برای یکی از بازیکنان ارسال شد"))
    check("اعلام انتخاب خون‌آشام", event.said(vp.CHOSEN_MESSAGE))
    check("خون‌آشام نمی‌تواند حدس بزند", self_guess)
    check("حدس دوم همان نفر نادیده گرفته شد", second_guess)
    check("حدس درست بازی را تمام کرد", not vp.is_active(CHAT))
    check("برنده ۷ سکه گرفت", bot.paid == [(others[1], vp.WINNER_COINS)],
          f"-> {bot.paid}")
    check("نام خون‌آشام اعلام شد", event.said("🧛 خون‌آشام:"))
    router.reset_all()


def test_vampire_timeout_reveals():
    print("\n### 🧛 پایان زمان و افشای خون‌آشام")

    async def scenario():
        router.reset_all()
        bot = Bot()
        vp.start(CHAT, bot.logger)
        session_id = vp._STORE.get(CHAT)["session_id"]
        for uid, name in ((51, "علی"), (52, "حسین"), (53, "محمد"), (54, "رضا")):
            vp.join(CHAT, uid, User(uid, name, f"u{uid}"), bot.logger)
        revealed = []

        async def on_timeout(vampire):
            revealed.append(vampire)

        vp.schedule(CHAT, session_id, {
            "on_abort": noop, "on_roles": noop, "on_timeout": on_timeout,
        }, logger=bot.logger, join_seconds=0.05, guess_seconds=0.15)
        await asyncio.sleep(0.6)
        return revealed

    revealed = asyncio.run(scenario())
    check("خون‌آشام افشا شد", len(revealed) == 1, f"-> {len(revealed)}")
    check("session بسته شد", not vp.is_active(CHAT))
    if revealed:
        text = vp.format_reveal(revealed[0])
        check("متن پایان درست است", "⏰ زمان تمام شد." in text
              and "🧛 خون‌آشام:" in text)
        check("یوزرنیم نمایش داده شد", "(@u" in text, f"-> {text}")
    router.reset_all()


def test_vampire_minimum_players():
    print("\n### 🧛 حداقل بازیکن")

    async def scenario():
        router.reset_all()
        bot = Bot()
        vp.start(CHAT, bot.logger)
        session_id = vp._STORE.get(CHAT)["session_id"]
        for uid in (61, 62):
            vp.join(CHAT, uid, User(uid, f"P{uid}"), bot.logger)
        aborted = []

        async def on_abort():
            aborted.append(True)

        vp.schedule(CHAT, session_id, {
            "on_abort": on_abort, "on_roles": noop, "on_timeout": noop,
        }, logger=bot.logger, join_seconds=0.05, guess_seconds=0.1)
        await asyncio.sleep(0.4)
        return aborted

    aborted = asyncio.run(scenario())
    check("با کمتر از ۴ نفر لغو شد", aborted == [True])
    check("session آزاد شد", not vp.is_active(CHAT))
    router.reset_all()


def test_vampire_display_names():
    print("\n### 🧛 نام نمایشی")
    from modules.fox_games.session_core import display_name
    check("Display Name اولویت دارد",
          display_name(User(1, "علی", "ali_x")) == "علی")
    check("در نبود نام، یوزرنیم", display_name(User(2, None, "ali_x")) == "@ali_x")
    check("در نبود هر دو، جایگزین مناسب",
          display_name(User(3, None, None)) == "بازیکن 3")


# ==========================================================================
# استقلال و همزیستی
# ==========================================================================
def test_isolation_between_fox_games():
    print("\n### استقلال چهار بازی از هم")

    async def scenario():
        router.reset_all()
        lb.reset_all(clear_quota=True)
        bot, event = Bot(), Event()
        await send(bot, event, 1, "بخند یا بباز")
        await send(bot, event, 2, "بقا")
        await send(bot, event, 3, "خون آشام")
        await send(bot, event, 4, "جعبه شانسی")
        return (ll.is_active(CHAT), sv.is_active(CHAT),
                vp.is_active(CHAT), lb.is_active(CHAT))

    laugh, survive, vamp, box = asyncio.run(scenario())
    check("هر چهار بازی هم‌زمان مستقل فعال‌اند",
          laugh and survive and vamp and box,
          f"-> {laugh} {survive} {vamp} {box}")

    stores = [id(ll._STORE), id(sv._STORE), id(lb._STORE), id(vp._STORE)]
    check("هر بازی SessionStore جدا دارد", len(set(stores)) == 4)
    router.reset_all()
    lb.reset_all(clear_quota=True)


def test_isolation_from_legacy_games():
    print("\n### استقلال از بازی‌های قدیمی")
    import modules.flag_guess as fg
    import modules.name_family as nf
    import modules.riddles as rd

    async def scenario():
        router.reset_all()
        fg.reset_history()
        nf.reset_all()
        rd.used_riddles.clear()
        bot, event = Bot(), Event()

        nf.start(CHAT)
        fg.start(CHAT, 1)
        rd.new_riddle(CHAT, 1)
        await send(bot, event, 1, "بخند یا بباز")
        await send(bot, event, 2, "بقا")

        legacy_ok = nf.is_active(CHAT) and fg.is_active(CHAT)
        fox_ok = ll.is_active(CHAT) and sv.is_active(CHAT)
        return legacy_ok, fox_ok

    legacy_ok, fox_ok = asyncio.run(scenario())
    check("بازی‌های قدیمی دست‌نخورده ماندند", legacy_ok)
    check("بازی‌های جدید هم‌زمان فعال شدند", fox_ok)

    fox_state = {id(ll._STORE._sessions), id(sv._STORE._sessions),
                 id(lb._STORE._sessions), id(vp._STORE._sessions)}
    legacy_state = {id(nf._ACTIVE), id(fg._ACTIVE), id(rd.active_riddles)}
    check("هیچ ساختار داده‌ای مشترک نیست", not (fox_state & legacy_state))
    router.reset_all()
    nf.reset_all()
    fg.reset_history()


def test_router_ignores_unrelated_text():
    print("\n### روتر پیام‌های نامرتبط را مصرف نمی‌کند")

    async def scenario():
        router.reset_all()
        bot, event = Bot(), Event()
        results = []
        for text in ("سلام", "اسم فامیل", "حدس پرچم", "چیستان", "راهنما"):
            results.append(await send(bot, event, 1, text))
        return results

    results = asyncio.run(scenario())
    check("هیچ پیام نامرتبطی مصرف نشد", not any(results), f"-> {results}")


def test_sequential_sessions():
    print("\n### چند session پشت سر هم")

    async def scenario():
        router.reset_all()
        bot = Bot()
        wins = 0
        for index in range(4):
            event = Event()
            await send(bot, event, 1, "بخند یا بباز")
            session = ll._STORE.get(CHAT)
            if session is None:
                break
            ll.open_round(CHAT, session["session_id"])
            if await send(bot, event, 10 + index, "😂", f"P{index}"):
                wins += 1
        return wins, bot

    wins, bot = asyncio.run(scenario())
    check("هر ۴ بازی پشت سر هم کار کرد", wins == 4, f"-> {wins}")
    check("۴ جایزه پرداخت شد", len(bot.paid) == 4, f"-> {bot.paid}")
    router.reset_all()


def test_survival_per_round_coins():
    """سکهٔ هر مرحله باید بلافاصله و فقط به پاسخ صحیح پرداخت شود."""
    print("\n### 🏕 سکهٔ مرحله‌ای بقا (مثال درخواست)")
    import tempfile
    import pathlib
    import economy as coins
    import economy.storage as _st

    original = _st.DATA_FILE
    _st.use_file(pathlib.Path(tempfile.mkdtemp()) / "economy.json")

    class RealBot:
        def __init__(self):
            self.client = Client()
            self.logger = Logger()

    names = {1: "علی", 2: "حسین", 3: "رضا", 4: "میلاد"}

    try:
        async def scenario():
            router.reset_all()
            sv._CHAT_HISTORY.clear()
            bot, event = RealBot(), Event()
            sv.start(CHAT, bot.logger)
            for uid, name in names.items():
                sv.join(CHAT, uid, User(uid, name), bot.logger)
            sv._STORE.cancel_task(CHAT)
            sv.begin_rounds(CHAT, bot.logger)

            # مرحله اول: علی و حسین درست، رضا غلط، میلاد ساکت
            sv.next_question(CHAT, bot.logger)
            answer = sv._STORE.get(CHAT)["question"]["answer"]
            for uid in (1, 2):
                await router.handle(bot, event, CHAT, uid,
                                    User(uid, names[uid]), answer, bot.logger)
            await router.handle(bot, event, CHAT, 3, User(3, names[3]),
                                "پاسخ کاملا غلط", bot.logger)
            sv.eliminate_silent(CHAT, bot.logger)
            # سکهٔ مراحل بقا نقره است.
            round1 = {uid: coins.get_balance(CHAT, uid)[coins.SILVER]
                      for uid in names}

            # مرحله دوم: علی درست، حسین غلط
            sv.next_question(CHAT, bot.logger)
            answer = sv._STORE.get(CHAT)["question"]["answer"]
            await router.handle(bot, event, CHAT, 1, User(1, names[1]),
                                answer, bot.logger)
            await router.handle(bot, event, CHAT, 2, User(2, names[2]),
                                "غلط", bot.logger)
            sv.eliminate_silent(CHAT, bot.logger)

            champion = sv.finish(CHAT, None, bot.logger)
            router._coins(bot, CHAT, champion["user_id"], champion["name"],
                          sv.WINNER_COINS, bot.logger, game="survival")
            return round1, champion

        round1, champion = asyncio.run(scenario())

        # بقا بازی «سخت» است: سکهٔ آن نقره است، نه برنز.
        check("علی پس از مرحله ۱: ۱ سکه نقره", round1[1] == 1,
              f"-> {round1[1]}")
        check("حسین پس از مرحله ۱: ۱ سکه نقره", round1[2] == 1,
              f"-> {round1[2]}")
        check("رضا (پاسخ غلط) سکه نگرفت", round1[3] == 0, f"-> {round1[3]}")
        check("میلاد (ساکت) سکه نگرفت", round1[4] == 0, f"-> {round1[4]}")

        check("برنده علی است", champion["name"] == "علی", f"-> {champion['name']}")
        check("سکهٔ مراحل برنده = ۲", champion["round_coins"] == 2,
              f"-> {champion['round_coins']}")
        check("مجموع علی = ۱۰ سکه نقره",
              coins.get_balance(CHAT, 1)[coins.SILVER] == 10,
              f"-> {coins.get_balance(CHAT, 1)[coins.SILVER]}")
        check("بقا برنز نمی‌دهد",
              coins.get_balance(CHAT, 1)[coins.BRONZE] == 0,
              f"-> {coins.get_balance(CHAT, 1)[coins.BRONZE]}")
        check("حسین سکهٔ مرحله‌اش را پس از حذف نگه داشت",
              coins.get_balance(CHAT, 2)[coins.SILVER] == 1,
              f"-> {coins.get_balance(CHAT, 2)[coins.SILVER]}")
        check("مقدار سکهٔ هر مرحله = ۱", sv.CORRECT_COINS == 1)
    finally:
        _st.use_file(original)
        router.reset_all()
        sv._CHAT_HISTORY.clear()


def test_survival_question_variety():
    """سؤال اول هر بازی یکسان نباشد و بانک پس از اتمام دوباره باز شود."""
    print("\n### 🏕 تنوع سؤال بقا")
    from modules.fox_games.survival_questions import LEVELS

    sv.reset_all()
    sv._CHAT_HISTORY.clear()
    firsts = []
    for _ in range(5):
        sv.start(CHAT)
        for uid in (1, 2):
            sv.join(CHAT, uid, User(uid, f"P{uid}"))
        sv.begin_rounds(CHAT)
        firsts.append(sv.next_question(CHAT)["text"])
        sv.finish(CHAT)
    check("سؤال اول در ۵ بازی پیاپی تکرار نشد",
          len(set(firsts)) == 5, f"-> {len(set(firsts))}/5")

    # سختی مرحله‌به‌مرحله
    sv.reset_all()
    sv._CHAT_HISTORY.clear()
    sv.start(CHAT)
    for uid in (1, 2):
        sv.join(CHAT, uid, User(uid, f"P{uid}"))
    sv.begin_rounds(CHAT)
    correct_tier = True
    for _ in range(6):
        question = sv.next_question(CHAT)
        tier = min(question["level"], len(LEVELS)) - 1
        if question["text"] not in [q[0] for q in LEVELS[tier]]:
            correct_tier = False
    check("هر مرحله از سطح سخت‌تر انتخاب می‌شود", correct_tier)

    # اتمام یک سطح: تاریخچه روی «کل بانک» است، نه هر سطح جداگانه. وقتی
    # سطح یک تمام شود انتخاب به سطح‌های دیگر می‌رود و هیچ سوالی تکرار
    # نمی‌شود — نه اینکه سطح یک از نو باز شود.
    sv.reset_all()
    sv._CHAT_HISTORY.clear()
    tier_size = len(LEVELS[0])
    draws = tier_size + 3
    seen = []
    for _ in range(draws):
        sv.start(CHAT)
        for uid in (1, 2):
            sv.join(CHAT, uid, User(uid, f"P{uid}"))
        sv.begin_rounds(CHAT)
        seen.append(sv.next_question(CHAT)["text"])
        sv.finish(CHAT)
    check("پس از اتمام یک سطح، بازی بدون خطا ادامه یافت",
          len(seen) == draws)
    check("حتی پس از اتمام سطح یک هیچ سؤالی تکرار نشد",
          len(set(seen)) == draws, f"-> {len(set(seen))}/{draws}")
    check("سؤال‌ها از بیش از یک سطح آمده‌اند",
          not set(seen).issubset({q[0] for q in LEVELS[0]}))
    sv.reset_all()
    sv._CHAT_HISTORY.clear()


def test_survival_solo_does_not_start_end_to_end():
    """باگ گزارش‌شده: با اولین «شرکت» بازی فوراً شروع می‌شد.

    مسیر کامل روتر با تایمر واقعی اجرا می‌شود: یک نفر ثبت‌نام می‌کند،
    مهلت تمام می‌شود، و بازی باید لغو شود — نه شروع.
    """
    print("\n### 🏕 یک نفر: بازی شروع نمی‌شود (مسیر کامل)")

    async def scenario():
        router.reset_all()
        bot, event = Bot(), Event()
        sv.reset_all()
        session = sv.start(CHAT, bot.logger)
        began, finished, aborted = [], [], []

        async def on_begin(names):
            began.append(list(names))

        async def on_finish(champion):
            finished.append(champion)

        async def on_abort():
            aborted.append(True)

        sv.schedule(CHAT, session["session_id"], {
            "on_abort": on_abort, "on_begin": on_begin, "on_question": noop,
            "on_eliminated": noop, "on_finish": on_finish,
        }, logger=bot.logger, join_seconds=0.3, answer_seconds=0.1)

        await asyncio.sleep(0.05)
        joined = sv.join(CHAT, 1, User(1, "تنها"), bot.logger)[0]
        waiting = sv.waiting_message(CHAT)
        await asyncio.sleep(1.2)
        return bot, joined, waiting, began, finished, aborted

    bot, joined, waiting, began, finished, aborted = asyncio.run(scenario())
    check("ثبت‌نام نفر اول موفق بود", joined == "joined")
    check("پیام انتظار نمایش داده شد", "در انتظار بازیکنان" in waiting)
    check("بازی با یک نفر شروع نشد", began == [], f"-> {began}")
    check("هیچ برنده‌ای اعلام نشد", finished == [], f"-> {finished}")
    check("ثبت‌نام لغو شد", aborted == [True])
    check("هیچ سکه‌ای پرداخت نشد", bot.paid == [], f"-> {bot.paid}")
    check("همهٔ state پاک شد", not sv.is_active(CHAT))
    check("لغو به دلیل کمبود بازیکن لاگ شد",
          bot.logger.has("reason=not_enough_players"))
    router.reset_all()


def test_survival_two_players_start_and_pay():
    """با ۲ نفر بازی شروع می‌شود و فقط بازماندهٔ واقعی ۸ سکه می‌گیرد."""
    print("\n### 🏕 دو نفر: شروع، برنده و پرداخت ۸ سکه")

    async def scenario():
        router.reset_all()
        bot, event = Bot(), Event()
        sv.reset_all()
        session = sv.start(CHAT, bot.logger)
        began, finished = [], []

        async def on_begin(names):
            began.append(list(names))

        async def on_finish(champion):
            finished.append(champion)
            if champion is not None:
                bot.award_coins(CHAT, champion["user_id"], champion["name"],
                                sv.WINNER_COINS)

        sv.schedule(CHAT, session["session_id"], {
            "on_abort": noop, "on_begin": on_begin, "on_question": noop,
            "on_eliminated": noop, "on_finish": on_finish,
        }, logger=bot.logger, join_seconds=0.2, answer_seconds=0.3)

        await asyncio.sleep(0.05)
        sv.join(CHAT, 1, User(1, "بازمانده"), bot.logger)
        sv.join(CHAT, 2, User(2, "حذفی"), bot.logger)

        # بازیکن ۱ همیشه درست پاسخ می‌دهد، بازیکن ۲ همیشه غلط.
        for _ in range(10):
            await asyncio.sleep(0.12)
            state = sv._STORE.get(CHAT)
            if not state or not state.get("question"):
                continue
            correct = state["question"]["answer"]
            sv.answer(CHAT, 1, correct, bot.logger)
            sv.answer(CHAT, 2, "پاسخ کاملا غلط", bot.logger)
            if len(sv.alive_players(CHAT)) <= 1:
                break
        await asyncio.sleep(1.0)
        return bot, began, finished

    bot, began, finished = asyncio.run(scenario())
    check("بازی با ۲ نفر شروع شد", len(began) == 1 and len(began[0]) == 2,
          f"-> {began}")
    check("بازی پایان یافت", len(finished) == 1, f"-> {finished}")
    check("برنده همان بازماندهٔ واقعی است",
          finished and finished[0] is not None
          and finished[0]["user_id"] == 1, f"-> {finished}")
    check("دقیقاً یک پرداخت انجام شد", len(bot.paid) == 1, f"-> {bot.paid}")
    check("برنده ۸ سکه گرفت", bot.paid == [(1, sv.WINNER_COINS)],
          f"-> {bot.paid}")
    check("session بسته شد", not sv.is_active(CHAT))
    router.reset_all()


def test_survival_restart_blocked_and_join_once():
    """اجرای دوبارهٔ «بقا» مسدود است و هر کاربر فقط یک بار «شرکت» دارد."""
    print("\n### 🏕 اجرای دوباره و ثبت‌نام یکتا (مسیر روتر)")

    async def scenario():
        router.reset_all()
        bot, event = Bot(), Event()
        await send(bot, event, 1, "بقا")
        started = sv.is_active(CHAT)

        again = Event()
        await send(bot, again, 2, "بقا")
        blocked = again.said("همین حالا در جریان")

        sv._STORE.cancel_task(CHAT)

        first = Event()
        await router.handle(bot, first, CHAT, 11, User(11, "الف"),
                            "شرکت", bot.logger)
        second = Event()
        await router.handle(bot, second, CHAT, 11, User(11, "الف"),
                            "شرکت", bot.logger)
        return started, blocked, first, second

    started, blocked, first, second = asyncio.run(scenario())
    check("بازی شروع شد", started)
    check("اجرای دوباره مسدود و پیام داده شد", blocked)
    check("ثبت‌نام اول تأیید شد", first.said("ثبت شد"))
    check("پیام انتظار همراه ثبت‌نام آمد", first.said("در انتظار بازیکنان"))
    check("ثبت‌نام تکراری رد شد", second.said("قبلاً ثبت‌نام"))
    check("فقط یک بار شمرده شد", sv.player_count(CHAT) == 1,
          f"-> {sv.player_count(CHAT)}")
    router.reset_all()


def test_survival_state_isolation():
    """state بقا کاملاً مستقل از بقیهٔ بازی‌هاست."""
    print("\n### 🏕 جدا بودن کامل state بقا")
    router.reset_all()
    sv.reset_all()

    stores = {"survival": id(sv._STORE), "laugh": id(ll._STORE),
              "lucky_box": id(lb._STORE), "vampire": id(vp._STORE)}
    check("SessionStore بقا مستقل است", len(set(stores.values())) == 4)
    check("تاریخچهٔ سوال بقا مخصوص خودش است",
          id(sv._CHAT_HISTORY) not in {id(ll._STORE), id(vp._STORE)})

    sv.start(CHAT)
    sv.join(CHAT, 1, User(1, "الف"))
    sv.join(CHAT, 2, User(2, "ب"))
    ll.start(CHAT)
    vp.start(CHAT)
    check("بقا و بقیه هم‌زمان زنده‌اند",
          sv.is_active(CHAT) and ll.is_active(CHAT) and vp.is_active(CHAT))

    ll.reset_all(CHAT)
    vp.reset_all(CHAT)
    check("بستن بازی‌های دیگر بقا را نبست", sv.is_active(CHAT))
    check("بازیکنان بقا دست‌نخورده ماندند", sv.player_count(CHAT) == 2)

    sv.reset_all(CHAT)
    check("پاک کردن بقا کامل انجام شد", not sv.is_active(CHAT))
    router.reset_all()


def test_survival_minimum_players():
    """بازی با یک نفر شروع نمی‌شود؛ حداقل ۲ نفر لازم است."""
    print("\n### 🏕 حداقل تعداد بازیکن (۲ تا ۴ نفر)")
    check("حداقل بازیکن ۲ است", sv.MIN_PLAYERS == 2, f"-> {sv.MIN_PLAYERS}")
    sv.reset_all()
    sv.start(CHAT)
    sv.join(CHAT, 1, User(1, "تنها"))
    check("با یک نفر بازی آغاز نمی‌شود", sv.begin_rounds(CHAT) is False)
    check("با یک نفر حداقل تکمیل نیست", sv.has_minimum(CHAT) is False)
    check("پیام انتظار نمایش داده می‌شود",
          "در انتظار بازیکنان" in sv.waiting_message(CHAT))
    sv.join(CHAT, 2, User(2, "دومی"))
    check("با دو نفر حداقل تکمیل است", sv.has_minimum(CHAT) is True)
    check("با دو نفر بازی آغاز می‌شود", sv.begin_rounds(CHAT) is True)
    sv.reset_all()

    # حداقل قابل تنظیم بین ۲ تا ۴ است و از ۲ پایین‌تر نمی‌رود.
    original = sv.MIN_PLAYERS
    try:
        check("تنظیم حداقل روی ۳", sv.set_min_players(3) == 3)
        check("تنظیم حداقل روی ۴", sv.set_min_players(4) == 4)
        check("مقدار ۱ به کف ۲ محدود می‌شود", sv.set_min_players(1) == 2)
        check("مقدار بزرگ‌تر از ظرفیت محدود می‌شود",
              sv.set_min_players(99) == sv.MAX_PLAYERS)
        check("مقدار نامعتبر تغییری نمی‌دهد",
              sv.set_min_players("abc") == sv.MAX_PLAYERS)

        sv.set_min_players(3)
        sv.reset_all()
        sv.start(CHAT)
        sv.join(CHAT, 1, User(1, "الف"))
        sv.join(CHAT, 2, User(2, "ب"))
        check("با حداقل ۳، دو نفر کافی نیست", sv.begin_rounds(CHAT) is False)
        sv.join(CHAT, 3, User(3, "ج"))
        check("با حداقل ۳، سه نفر کافی است", sv.begin_rounds(CHAT) is True)
    finally:
        sv.set_min_players(original)
        sv.reset_all()


def test_survival_no_new_players_after_start():
    """پس از شروع بازی هیچ کاربر جدیدی نمی‌تواند وارد شود."""
    print("\n### 🏕 ورود ممنوع پس از شروع")
    sv.reset_all()
    sv.start(CHAT)
    sv.join(CHAT, 1, User(1, "الف"))
    sv.join(CHAT, 2, User(2, "ب"))
    sv.begin_rounds(CHAT)
    state, _ = sv.join(CHAT, 3, User(3, "دیرآمده"))
    check("ورود بعد از شروع رد می‌شود", state == "closed", f"-> {state}")
    check("تعداد بازیکنان تغییر نکرد", sv.player_count(CHAT) == 2)
    check("کاربر دیرآمده بازیکن نیست",
          sv.answer(CHAT, 3, "هرچیزی")[0] == "no_question"
          or sv.answer(CHAT, 3, "هرچیزی")[0] == "not_player")
    sv.reset_all()


def test_survival_all_eliminated_no_coins():
    """اگر همه حذف شدند هیچ سکه‌ای داده نمی‌شود."""
    print("\n### 🏕 حذف همه = بدون برنده و بدون سکه")
    sv.reset_all()
    logger = Logger()
    sv.start(CHAT, logger)
    sv.join(CHAT, 1, User(1, "الف"), logger)
    sv.join(CHAT, 2, User(2, "ب"), logger)
    sv.begin_rounds(CHAT, logger)
    sv.next_question(CHAT, logger)

    # هر دو پاسخ غلط می‌دهند: هیچ‌کس زنده نمی‌ماند.
    sv.answer(CHAT, 1, "پاسخ کاملا غلط", logger)
    sv.answer(CHAT, 2, "پاسخ کاملا غلط", logger)
    check("هیچ بازیکن زنده‌ای نماند", len(sv.alive_players(CHAT)) == 0)
    check("برنده‌ای وجود ندارد", sv.winner(CHAT) is None)
    champion = sv.finish(CHAT, logger=logger)
    check("finish هیچ برنده‌ای برنمی‌گرداند", champion is None, f"-> {champion}")
    sv.reset_all()

    # تک‌بازماندهٔ ساکت هم نباید برنده شود.
    sv.start(CHAT, logger)
    sv.join(CHAT, 1, User(1, "الف"), logger)
    sv.join(CHAT, 2, User(2, "ب"), logger)
    sv.begin_rounds(CHAT, logger)
    sv.next_question(CHAT, logger)
    sv.answer(CHAT, 1, "پاسخ کاملا غلط", logger)   # حذف می‌شود
    sv.eliminate_silent(CHAT, logger)              # نفر دوم ساکت بود
    check("بازماندهٔ ساکت زنده نمی‌ماند", len(sv.alive_players(CHAT)) == 0)
    check("بازی ساکت برنده ندارد", sv.finish(CHAT, logger=logger) is None)
    check("نبود برنده لاگ شد", logger.has("FOX SURVIVAL NO WINNER"))
    sv.reset_all()


def test_vampire_cannot_self_guess():
    """خون‌آشام نه می‌تواند خودش را حدس بزند، نه فرصت حدس مصرف می‌کند."""
    print("\n### 🧛 خون‌آشام نمی‌تواند خودش را حدس بزند")
    router.reset_all()
    logger = Logger()
    vp.start(CHAT, logger)
    for uid, name in ((1, "علی"), (2, "حسین"), (3, "رضا"), (4, "محمد")):
        vp.join(CHAT, uid, User(uid, name), logger)
    chosen = vp.choose_vampire(CHAT, logger)
    # مرحلهٔ حدس فقط بعد از ارسال موفق پیوی باز می‌شود.
    vp.open_guessing(CHAT, logger=logger)
    vampire_uid = chosen["player"]["user_id"]
    number = chosen["number"]

    state, _ = vp.guess(CHAT, vampire_uid, str(number), logger)
    check("حدس خودِ خون‌آشام رد می‌شود", state == "is_vampire", f"-> {state}")
    check("بازی همچنان فعال است", vp.is_active(CHAT))
    check("فرصت حدس او مصرف نشد",
          vampire_uid not in vp._STORE.get(CHAT)["guessed"])

    other = str(1 if number != 1 else 2)
    state, _ = vp.guess(CHAT, vampire_uid, other, logger)
    check("حدس خون‌آشام روی نفر دیگر هم رد می‌شود", state == "is_vampire",
          f"-> {state}")
    check("رد شدن لاگ شد", logger.has("reason=is_vampire"))

    # بقیه یک بار حدس دارند
    # هیچ‌کس نمی‌تواند خودش را انتخاب کند و این تلاش نوبتش را نمی‌سوزاند.
    players = vp._STORE.get(CHAT)["players"]
    guesser = next(p["user_id"] for p in players
                   if p["user_id"] != vampire_uid)
    own_number = next(i for i, p in enumerate(players, 1)
                      if p["user_id"] == guesser)
    self_state, _ = vp.guess(CHAT, guesser, str(own_number), logger)
    check("بازیکن عادی نمی‌تواند خودش را انتخاب کند",
          self_state == "self_guess", f"-> {self_state}")
    check("این تلاش نوبت او را مصرف نکرد",
          guesser not in vp._STORE.get(CHAT)["guessed"])
    check("رد شدن انتخاب خود لاگ شد", logger.has("reason=self_guess"))

    wrong = str(next(i for i in range(1, len(players) + 1)
                     if i not in {number, own_number}))
    first, _ = vp.guess(CHAT, guesser, wrong, logger)
    second, _ = vp.guess(CHAT, guesser, str(number), logger)
    check("حدس اول بازیکن پذیرفته شد", first in {"correct", "wrong"},
          f"-> {first}")
    check("حدس دوم همان بازیکن رد شد", second == "already", f"-> {second}")
    router.reset_all()


def test_real_coin_payout():
    """جایزه باید روی موجودی واقعی بنشیند، حتی وقتی bot متد award_coins ندارد.

    گارد رگرسیون: قبلاً روتر به ``getattr(bot, "award_coins")`` تکیه می‌کرد و
    چون شیء واقعی ربات چنین متدی ندارد، هیچ سکه‌ای پرداخت نمی‌شد.
    """
    print("\n### پرداخت واقعی سکه (بدون award_coins روی bot)")
    import tempfile
    import pathlib
    import economy as coins
    import economy.storage as _st

    original_file = _st.DATA_FILE
    _st.use_file(pathlib.Path(tempfile.mkdtemp()) / "economy.json")

    class RealBot:
        """مثل شیء واقعی ربات: هیچ متد award_coins ندارد."""

        def __init__(self):
            self.client = Client()
            self.logger = Logger()

    try:
        check("شیء ربات واقعی متد award_coins ندارد",
              not hasattr(RealBot(), "award_coins"))

        async def scenario():
            router.reset_all()
            bot, event = RealBot(), Event()
            await send(bot, event, 1, "بخند یا بباز")
            ll.open_round(CHAT, ll._STORE.get(CHAT)["session_id"], bot.logger)
            await send(bot, event, 10, "😂", "Ali")

            vp.start(CHAT, bot.logger)
            for uid, name in ((41, "a"), (42, "b"), (43, "c"), (44, "d")):
                vp.join(CHAT, uid, User(uid, name), bot.logger)
            chosen = vp.choose_vampire(CHAT, bot.logger)
            vp.open_guessing(CHAT, logger=bot.logger)
            guesser = next(p["user_id"] for p in chosen["players"]
                           if p["user_id"] != chosen["player"]["user_id"])
            await send(bot, event, guesser, str(chosen["number"]))
            return bot, guesser

        bot, guesser = asyncio.run(scenario())
        check("برندهٔ بخند یا بباز ۳ سکه گرفت",
              coins.get_balance(CHAT, 10)[coins.BRONZE] == 3,
              f"-> {coins.get_balance(CHAT, 10)[coins.BRONZE]}")
        check("برندهٔ خون‌آشام ۷ سکه نقره گرفت",
              coins.get_balance(CHAT, guesser)[coins.SILVER] == 7,
              f"-> {coins.get_balance(CHAT, guesser)[coins.SILVER]}")
        check("پرداخت لاگ شد", bot.logger.has("FOX REWARD PAID"))

        # بقا
        router.reset_all()
        logger = Logger()
        sv.start(CHAT, logger)
        for uid in (61, 62, 63, 64):
            sv.join(CHAT, uid, User(uid, f"P{uid}"), logger)
        sv._STORE.cancel_task(CHAT)
        sv.begin_rounds(CHAT, logger)
        sv.next_question(CHAT, logger)
        session = sv._STORE.get(CHAT)
        sv.answer(CHAT, 61, session["question"]["answer"], logger)
        sv.eliminate_silent(CHAT, logger)
        champion = sv.finish(CHAT, None, logger)
        router._coins(RealBot(), CHAT, champion["user_id"],
                      champion["name"], sv.WINNER_COINS, logger,
                      game="survival")
        check("برندهٔ بقا ۸ سکه نقره گرفت",
              coins.get_balance(CHAT, champion["user_id"])[coins.SILVER] == 8,
              f"-> {coins.get_balance(CHAT, champion['user_id'])[coins.SILVER]}")
    finally:
        _st.use_file(original_file)
        router.reset_all()


def test_reward_values():
    print("\n### مقادیر جایزه مطابق راهنما")
    check("بخند یا بباز = ۳ سکه", ll.WINNER_COINS == 3, f"-> {ll.WINNER_COINS}")
    check("بقا = ۸ سکه", sv.WINNER_COINS == 8, f"-> {sv.WINNER_COINS}")
    check("خون‌آشام = ۷ سکه", vp.WINNER_COINS == 7, f"-> {vp.WINNER_COINS}")
    check("زمان حدس خون‌آشام = ۵۰ ثانیه", vp.GUESS_SECONDS == 50,
          f"-> {vp.GUESS_SECONDS}")


def test_logging():
    print("\n### لاگ کامل")

    async def scenario():
        router.reset_all()
        bot, event = Bot(), Event()
        await send(bot, event, 1, "بخند یا بباز")
        ll.open_round(CHAT, ll._STORE.get(CHAT)["session_id"], bot.logger)
        await send(bot, event, 10, "😂", "Ali")
        await send(bot, event, 1, "بقا")
        await send(bot, event, 21, "شرکت", "P21")
        return bot

    bot = asyncio.run(scenario())
    for needle in ("FOX LAUGH START", "FOX LAUGH OPEN", "FOX LAUGH WINNER",
                   "FOX SURVIVAL START", "FOX SURVIVAL JOIN"):
        check(f"لاگ موجود: {needle}", bot.logger.has(needle))
    router.reset_all()


def main():
    test_laugh()
    test_laugh_emoji_set()
    test_laugh_timeout()
    test_survival_registration()
    test_survival_full_game()
    test_survival_wrong_answer_eliminates()
    test_survival_questions()
    test_lucky_box_layout()
    test_lucky_box_play_and_quota()
    test_lucky_box_wait_format()
    test_vampire_full_game()
    test_vampire_timeout_reveals()
    test_vampire_minimum_players()
    test_vampire_display_names()
    test_isolation_between_fox_games()
    test_isolation_from_legacy_games()
    test_router_ignores_unrelated_text()
    test_sequential_sessions()
    test_survival_per_round_coins()
    test_survival_question_variety()
    test_survival_solo_does_not_start_end_to_end()
    test_survival_two_players_start_and_pay()
    test_survival_restart_blocked_and_join_once()
    test_survival_state_isolation()
    test_survival_minimum_players()
    test_survival_no_new_players_after_start()
    test_survival_all_eliminated_no_coins()
    test_vampire_cannot_self_guess()
    test_real_coin_payout()
    test_reward_values()
    test_logging()

    print(f"\n{'=' * 52}")
    print(f"passed={PASSED} failed={FAILED}")
    print("=" * 52)
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
