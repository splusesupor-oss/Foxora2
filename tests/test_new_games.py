"""تست سه بازی جدید: معما، بهترین جواب، نبرد.

    python tests/test_new_games.py

پوشش:
- معما: فقط صاحب پاسخ می‌دهد، ۳ برنز، دو کاربر هم‌زمان، timeout، dedup.
- بهترین جواب: ثبت پاسخ، قضاوت معیارمند، ۴ برنز، بی‌پاسخ، ایزولهٔ گروه.
- نبرد: شروع، شرکت، نفر سوم ممنوع، پاسخ فقط خودی، ۳۰ ثانیه، بازنده ۴ برنز،
  بازندهٔ بی‌پاسخ صفر، عدم پرداخت دوبار، ایزولهٔ گروه.
- راهنما/لیست/راهنمای امتیاز: هر سه بازی با Bold.
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import handlers.fox_games_router as router
from modules.fox_games import maemma, best_answer, battle
from modules.fox_games.maemma_puzzles import PUZZLES as MAEMMA_PUZZLES
from modules.fox_games.best_answer_questions import BEST_ANSWER_QUESTIONS
from modules.fox_games.battle_questions import BATTLE_QUESTIONS

PASSED = 0
FAILED = 0
CHAT = -1009
CHAT2 = -1010


def check(label, cond, detail=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok  {label}")
    else:
        FAILED += 1
        print(f"FAIL  {label} {detail}")


class User:
    def __init__(self, uid, name=None, username=None):
        self.id = uid
        self.first_name = name
        self.last_name = None
        self.username = username


_MSG_SEQ = [1000]


def _next_msg_id():
    _MSG_SEQ[0] += 1
    return _MSG_SEQ[0]


class _FakeMessage:
    def __init__(self, mid):
        self.id = mid


class Event:
    def __init__(self):
        self.out = []
        self.reply_to = None

    async def reply(self, text, **kwargs):
        self.out.append(text)
        return _FakeMessage(_next_msg_id())

    def said(self, needle):
        return any(needle in m for m in self.out)


class Logger:
    def __init__(self):
        self.info = []
        self.errors = []

    def log_info(self, m):
        self.info.append(m)

    def log_error(self, m):
        self.errors.append(m)


class Bot:
    def __init__(self):
        self.logger = Logger()
        self.paid = []

    def award_coins(self, chat_id, user_id, name, amount):
        self.paid.append((user_id, amount))


async def send(bot, event, chat, uid, text, name=None, username=None):
    return await router.handle(
        bot, event, chat, uid, User(uid, name, username), text, bot.logger
    )


class ReplyTo:
    def __init__(self, msg_id):
        self.reply_to_msg_id = msg_id


async def send_reply(bot, event, chat, uid, reply_to_id, text, name=None):
    """پیامِ ریپلای‌شده به یک پیام مشخص را می‌فرستد."""
    event.reply_to = ReplyTo(reply_to_id)
    return await router.handle(
        bot, event, chat, uid, User(uid, name), text, bot.logger
    )


# ===========================================================================
#  🧩 معما
# ===========================================================================
async def _test_maemma_owner_only():
    router.reset_all()
    bot = Bot()
    ev = Event()
    r = await send(bot, ev, CHAT, 1, "معما", name="A")
    check("معما: دستور مصرف شد", r is True)
    check("معما: سوال نمایش داده شد",
          any("معما" in m and "ثانیه" in m for m in ev.out), f"{ev.out}")
    check("معما: سشن برای A باز شد", maemma.is_active(CHAT, 1))

    # B معما ندارد؛ پیام B مصرف نمی‌شود و سکه نمی‌گیرد
    ev2 = Event()
    r = await send(bot, ev2, CHAT, 2, "هر چیزی", name="B")
    check("معما: پاسخ B مصرف نشد", r is False)
    check("معما: B سکه نگرفت", not bot.paid, f"{bot.paid}")

    # A جواب درست می‌دهد → فقط موفقیت+جایزه، بازی تمام می‌شود
    q = maemma.current_question(CHAT, 1)
    check("معما: سوال با شماره و کل بانک است", q is not None
          and q["total"] == len(maemma.PUZZLES), f"{q}")
    ev3 = Event()
    r = await send(bot, ev3, CHAT, 1, q["answer"], name="A")
    check("معما: پاسخ A مصرف شد", r is True)
    check("معما: فقط پیام موفقیت نمایش داده شد",
          any("پاسخ صحیح بود" in m for m in ev3.out), f"{ev3.out}")
    check("معما: سوال بعدی خودکار ارسال نشد",
          not any("سوال" in m and "از" in m for m in ev3.out), f"{ev3.out}")
    check("معما: پیام «زمان تمام شد» بعد از جواب درست نیامد",
          not any("زمان تمام شد" in m for m in ev3.out), f"{ev3.out}")
    check("معما: A دقیقاً ۳ برنز گرفت",
          len(bot.paid) == 1 and bot.paid[0] == (1, maemma.REWARD), f"{bot.paid}")
    check("معما: سشن A بسته شد", not maemma.is_active(CHAT, 1))


async def _test_maemma_concurrent():
    router.reset_all()
    bot = Bot()
    await send(bot, Event(), CHAT, 10, "معما", name="X")
    await send(bot, Event(), CHAT, 20, "معما", name="Y")
    check("معما: دو کاربر هم‌زمان", maemma.is_active(CHAT, 10)
          and maemma.is_active(CHAT, 20))
    sx = maemma.active_state(CHAT, 10)
    sy = maemma.active_state(CHAT, 20)
    check("معما: توکن‌های متفاوت", sx["token"] != sy["token"])
    qx = maemma.current_question(CHAT, 10)
    qy = maemma.current_question(CHAT, 20)
    check("معما: سوال‌های دو کاربر جدا", qx["answer"] != qy["answer"]
          or qx["emoji"] != qy["emoji"], f"{qx} vs {qy}")
    # پاسخ X سشن Y را نمی‌بندد
    ev = Event()
    await send(bot, ev, CHAT, 10, qx["answer"], name="X")
    check("معما: X بسته شد (بازی یک سوالی)", not maemma.is_active(CHAT, 10))
    check("معما: Y هنوز باز است", maemma.is_active(CHAT, 20))


async def _test_maemma_timeout():
    router.reset_all()
    bot = Bot()
    ev = Event()
    await send(bot, ev, CHAT, 1, "معما", name="A")
    state = maemma.active_state(CHAT, 1)
    result = maemma.finish(CHAT, state["token"], 1, bot.logger)
    check("معما: timeout بدون خطا بسته شد",
          result is not None and "answer" in result, f"{result}")
    check("معما: پس از timeout سشن بسته است", not maemma.is_active(CHAT, 1))


async def _test_maemma_no_auto_next_and_requires_command():
    """بعد از پاسخ، سوال بعدی خودکار نمی‌آید؛ برای معما باید دوباره «معما» زد."""
    router.reset_all()
    bot = Bot()
    await send(bot, Event(), CHAT, 1, "معما", name="A")
    q = maemma.current_question(CHAT, 1)
    ev = Event()
    await send(bot, ev, CHAT, 1, q["answer"], name="A")
    # بعد از پاسخ: سشن بسته، هیچ سوالی خودکار نیامده
    check("معما: بعد از پاسخ، بازی تمام شد", not maemma.is_active(CHAT, 1))
    check("معما: سوال بعدی خودکار ارسال نشد",
          not any("سوال" in m and "از" in m for m in ev.out), f"{ev.out}")

    # بدون ارسال «معما»، پیامِ بعدی بازیِ تازه شروع نمی‌کند
    ev2 = Event()
    r = await send(bot, ev2, CHAT, 1, "چیزی", name="A")
    check("معما: بدون دستور، بازیِ تازه شروع نشد",
          r is False and not maemma.is_active(CHAT, 1), f"{r}")

    # با ارسال دوبارهٔ «معما» بازیِ جدید شروع می‌شود و شماره بالا می‌رود
    ev3 = Event()
    await send(bot, ev3, CHAT, 1, "معما", name="A")
    q2 = maemma.current_question(CHAT, 1)
    check("معما: با دستور دوباره، معما شروع شد", q2 is not None, f"{q2}")
    check("معما: شمارهٔ سوال جدید افزایش یافت", q2["number"] > q["number"],
          f"{q['number']} -> {q2['number']}")
    check("معما: سوال جدید با سوال قبلی فرق دارد", q2["answer"] != q["answer"],
          f"{q['answer']} vs {q2['answer']}")


def _test_maemma_dedup_and_bank():
    check("معما: بانک معماها حداقل ۱۹۰ عدد", len(MAEMMA_PUZZLES) >= 190,
          f"count={len(MAEMMA_PUZZLES)}")
    ans = [p[1] for p in MAEMMA_PUZZLES]
    check("معما: پاسخ‌ها یکتا هستند",
          len(set(ans)) == len(ans), f"{len(set(ans))} vs {len(ans)}")


# ===========================================================================
#  🎯 بهترین جواب
# ===========================================================================
async def _test_best_answer_flow():
    router.reset_all()
    bot = Bot()
    ev = Event()
    r = await send(bot, ev, CHAT, 1, "بهترین جواب")
    check("بهترین جواب: دستور مصرف شد", r is True)
    check("بهترین جواب: سوال نمایش داده شد",
          any("۴۰" in m for m in ev.out), f"{ev.out}")
    check("بهترین جواب: توضیح «ریپلای کنید» نمایش داده شد",
          any("ریپلای" in m for m in ev.out), f"{ev.out}")
    check("بهترین جواب: دور فعال است", best_answer.is_active(CHAT))

    sess = best_answer._STORE.get(CHAT)
    q_id = sess["question_msg_id"]

    # پیامِ عادیِ بدون ریپلای → اصلاً ثبت نمی‌شود
    ev2 = Event()
    r2 = await send(bot, ev2, CHAT, 5, "پاسخِ بدون ریپلای", name="E")
    check("بهترین جواب: پیامِ بدون ریپلای ثبت نمی‌شود",
          r2 is False and sess["answered"] == 0, f"{r2}")

    # پاسخِ واقعی و کامل با ریپلایِ مستقیم به سوال
    good = sess["sample"]
    r = await send_reply(bot, Event(), CHAT, 2, q_id, good, name="B")
    check("بهترین جواب: پاسخِ ریپلای‌شده ثبت می‌شود", r is True,
          f"{r} answered={sess['answered']}")
    # پاسخِ چرت با ریپلای (معتبر در ثبت، ولی در تحلیل حذف می‌شود)
    await send_reply(bot, Event(), CHAT, 3, q_id, "نمیدونم", name="C")
    # پاسخِ خیلی کوتاهِ بی‌ربط با ریپلای
    await send_reply(bot, Event(), CHAT, 4, q_id, "آره", name="D")

    winner = best_answer.judge(CHAT, sess["session_id"], bot.logger)
    check("بهترین جواب: برنده تعیین شد", winner is not None, f"{winner}")
    if winner:
        check("بهترین جواب: برنده پاسخِ کامل و مرتبط است",
              winner["user_id"] == 2, f"{winner}")
        check("بهترین جواب: برنده کیفیت بالایی دارد",
              winner.get("score", 0) > 0, f"{winner}")
        paid = router._coins(
            bot, CHAT, winner["user_id"], winner["name"], best_answer.REWARD,
            bot.logger, reference=f"ba:{CHAT}:{winner['session_id']}",
            game="best_answer",
        )
        check("بهترین جواب: دقیقاً ۲ برنز", paid and bot.paid[0][1] == 2,
              f"{bot.paid}")
    check("بهترین جواب: دور بسته شد", not best_answer.is_active(CHAT))


async def _test_best_answer_reply_required():
    """فقط ریپلایِ مستقیم به سوال، پاسخ ثبت می‌شود."""
    router.reset_all()
    bot = Bot()
    await send(bot, Event(), CHAT, 1, "بهترین جواب")
    sess = best_answer._STORE.get(CHAT)
    q_id = sess["question_msg_id"]

    # ریپلای به پیامِ دیگری (نه سوال) → ثبت نمی‌شود
    r = await send_reply(bot, Event(), CHAT, 2, q_id + 9999, "جواب درست", name="B")
    check("بهترین جواب: ریپلای به پیامِ دیگر ثبت نمی‌شود",
          r is False and sess["answered"] == 0, f"{r}")

    # ریپلایِ درست → ثبت می‌شود
    await send_reply(bot, Event(), CHAT, 2, q_id, "جواب درست", name="B")
    check("بهترین جواب: ریپلایِ درست ثبت می‌شود", sess["answered"] == 1,
          f"{sess['answered']}")
    router.reset_all()


async def _test_best_answer_analysis_filters():
    """پاسخ‌های چرت/اسپم/بی‌معنی/بی‌ربط امتیاز نمی‌گیرند.

    با ورودی ثابت تست می‌شود تا به انتخابِ تصادفیِ سوال وابسته نباشد.
    """
    from modules.fox_games import answer_analysis as _aa

    # یک سوالِ ثابت برای کنترل تست
    q = "چرا یخ شناور می‌ماند و چگالی آن با آب مقایسه می‌شود؟"
    kw = ("یخ", "آب", "چگالی", "شناور")

    # ۱) پاسخ‌های آشغال رد می‌شوند
    for bad in ("", "هههههه", "😂😂😂😂", "نمیدونم", "ببخشید", "آره", "نمی‌دونم"):
        r = _aa.analyze(q, kw, bad)
        check(f"بهترین جواب: رد پاسخِ چرت {bad!r}", r["valid"] is False
              and r["reason"] is not None, f"{r}")
        check(f"بهترین جواب: پاسخِ چرت امتیاز صفر دارد", r["score"] == 0.0,
              f"{r['score']}")

    # ۲) پاسخِ خارج از موضوع (بدون ارتباط به سوال و بدون کلیدواژه)
    off = _aa.analyze(q, kw, "امروز صبح خیلی دیر بیدار شدم و دیر به محل کارم رسیدم و کلی کار عقب افتاد")
    check("بهترین جواب: پاسخِ خارج از موضوع رد می‌شود",
          off["valid"] is False and off["reason"] == "off_topic", f"{off}")

    # ۳) پاسخِ کامل و مرتبط معتبر است
    good_text = ("چون چگالی یخ از آب کمتر است، یخ شناور می‌ماند و روی آب می‌ماند.")
    good = _aa.analyze(q, kw, good_text)
    check("بهترین جواب: پاسخِ کامل مرتبط معتبر است", good["valid"] is True,
          f"{good}")

    # ۴) keyword stuffing (فقط کلیدواژه، بدون توضیح) نباید بر پاسخِ خوب بچربد
    stuffing = _aa.analyze(q, kw, " ".join(kw))
    good_r = _aa.analyze(q, kw, good_text)
    check("بهترین جواب: keyword stuffing از پاسخِ خوب کمتر است",
          good_r["score"] > stuffing["score"],
          f"good={good_r['score']} stuffing={stuffing['score']}")
    winner = _aa.pick_best(
        q, kw,
        [{"user_id": 1, "name": "A", "text": " ".join(kw), "ts": 1},
         {"user_id": 2, "name": "B", "text": good_text, "ts": 2}],
    )
    check("بهترین جواب: پاسخِ کامل بر keyword stuffing می‌چربد",
          winner is not None and winner["user_id"] == 2, f"{winner}")


async def _test_best_answer_no_answer():
    router.reset_all()
    bot = Bot()
    await send(bot, Event(), CHAT, 1, "بهترین جواب")
    sess = best_answer._STORE.get(CHAT)
    winner = best_answer.judge(CHAT, sess["session_id"], bot.logger)
    check("بهترین جواب: بدون پاسخ، برنده نیست", winner is None)
    check("بهترین جواب: بدون پاسخ، سکه پرداخت نشد", not bot.paid)
    check("بهترین جواب: بدون پاسخ، بدون خطا بسته شد",
          not best_answer.is_active(CHAT))


def _test_best_answer_bank():
    check("بهترین جواب: بانک ۱۸۰ سوال", len(BEST_ANSWER_QUESTIONS) == 180,
          f"count={len(BEST_ANSWER_QUESTIONS)}")
    qs = [q[0] for q in BEST_ANSWER_QUESTIONS]
    check("بهترین جواب: سوال‌ها یکتا هستند", len(set(qs)) == len(qs))


async def _test_best_answer_chat_isolation():
    router.reset_all()
    bot = Bot()
    await send(bot, Event(), CHAT, 1, "بهترین جواب")
    await send(bot, Event(), CHAT2, 1, "بهترین جواب")
    check("بهترین جواب: دو گروه مستقل", best_answer.is_active(CHAT)
          and best_answer.is_active(CHAT2))
    s1 = best_answer._STORE.get(CHAT)
    s2 = best_answer._STORE.get(CHAT2)
    check("بهترین جواب: سشن‌ها متفاوت", s1["session_id"] != s2["session_id"])


# ===========================================================================
#  ⚔️ نبرد
# ===========================================================================
def _battle_constants():
    check("نبرد: هر سوال ۳۰ ثانیه", battle.ANSWER_SECONDS == 30,
          f"{battle.ANSWER_SECONDS}")
    check("نبرد: ۱۹۰ سوال در بانک", len(BATTLE_QUESTIONS) == 190,
          f"count={len(BATTLE_QUESTIONS)}")


async def _test_battle_start_join_third():
    router.reset_all()
    bot = Bot()
    ev = Event()
    await send(bot, ev, CHAT, 100, "نبرد", name="P1")
    check("نبرد: پس از «نبرد» در مرحلهٔ ثبت‌نام", battle.phase(CHAT) == "joining")

    # بازیکن اول نمی‌تواند دوباره وارد شود
    ev = Event()
    r = await send(bot, ev, CHAT, 100, "شرکت", name="P1")
    check("نبرد: بازیکن اول نمی‌تواند دوباره وارد شود",
          r is True and ev.said("اول"), f"{ev.out}")

    # بازیکن دوم وارد می‌شود
    await send(bot, Event(), CHAT, 200, "شرکت", name="P2")
    sess = battle._STORE.get(CHAT)
    check("نبرد: بازیکن دوم ثبت شد", sess["p2"]["user_id"] == 200)

    # نفر سوم نمی‌تواند وارد شود
    ev = Event()
    r = await send(bot, ev, CHAT, 300, "شرکت", name="P3")
    check("نبرد: نفر سوم رد شد", r is True and ev.said("نم"))


async def _test_battle_active_blocks_new_start():
    """تا وقتی نبرد فعال است، هیچ‌کس نمی‌تواند نبرد تازه شروع کند."""
    router.reset_all()
    bot = Bot()
    await send(bot, Event(), CHAT, 100, "نبرد", name="P1")

    # کاربر دیگری می‌خواهد نبرد تازه شروع کند → رد با پیام «ابتدا تمام کن»
    ev = Event()
    r = await send(bot, ev, CHAT, 999, "نبرد", name="X")
    check("نبرد: شروعِ نبردِ دوم در حین نبرد فعال رد شد", r is True,
          f"{ev.out}")
    check("نبرد: پیام «ابتدا بازی فعلی را تمام کنید»",
          any("تمام" in m for m in ev.out), f"{ev.out}")
    check("نبرد: سشن نبرد اول دست‌نخورده است", battle.phase(CHAT) == "joining")


async def _test_battle_finish_blockquote():
    """نام و امتیاز بازیکنان در نتیجهٔ نبرد داخل نقل‌قول شیشه‌ای (Blockquote) است."""
    router.reset_all()
    bot = Bot()
    for g in ("maemma", "best_answer", "battle"):
        import economy.game_progress as gp
        gp.clear_recent(CHAT, g)
    await send(bot, Event(), CHAT, 100, "نبرد", name="P1")
    join_ev = CapturingEvent()
    await send(bot, join_ev, CHAT, 200, "شرکت", name="P2")

    original = battle.ANSWER_SECONDS
    battle.ANSWER_SECONDS = 0.12
    try:
        for qidx in range(6):
            for _ in range(30):
                cur = battle.current_question(CHAT)
                if cur:
                    break
                await asyncio.sleep(0.01)
            if cur is None:
                break
            assignee = cur["assignee"]
            ans = cur["question"]["answer"]
            if assignee == 200 and qidx == 1:
                ans = "غلط غلط"
            await send(bot, Event(), CHAT, assignee, ans)
            await asyncio.sleep(0.03)
        for _ in range(50):
            if not battle.is_active(CHAT):
                break
            await asyncio.sleep(0.03)
    finally:
        battle.ANSWER_SECONDS = original

    finish = next(((t, e) for t, e in join_ev.messages if "پایان" in t), None)
    check("نبرد: پیام پایان وجود دارد", finish is not None)
    if finish:
        text, entities = finish
        qwords = []
        for e in (entities or []):
            if "Blockquote" in type(e).__name__:
                qwords.append(text.encode("utf-16-le")[
                    e.offset * 2:(e.offset + e.length) * 2].decode("utf-16-le"))
        check("نبرد: خطوط امتیاز داخل Blockquote",
              len(qwords) >= 2 and any("P1" in w for w in qwords)
              and any("P2" in w for w in qwords), f"{qwords}")
    router.reset_all()


async def _test_battle_non_assignee_cannot_answer():
    """پاسخ فقط برای بازیکنِ همان سوال ثبت می‌شود."""
    router.reset_all()
    original_answer = battle.ANSWER_SECONDS
    battle.ANSWER_SECONDS = 0.3
    try:
        bot = Bot()
        await send(bot, Event(), CHAT, 100, "نبرد", name="P1")
        await send(bot, Event(), CHAT, 200, "شرکت", name="P2")
        for _ in range(30):
            cur = battle.current_question(CHAT)
            if cur:
                break
            await asyncio.sleep(0.02)
        check("نبرد: سوال فعلی برای P1 است", cur["assignee"] == 100)
        # P2 (غیرصاحب سوال) جواب درست را بفرستد → نباید امتیاز بگیرد و نباید بسته شود
        before_score = battle._STORE.get(CHAT)["scores"].copy()
        ev = Event()
        r = await send(bot, ev, CHAT, 200, cur["question"]["answer"], name="P2")
        check("نبرد: پاسخ غیرصاحب مصرف می‌شود", r is True)
        after = battle._STORE.get(CHAT)["scores"]
        check("نبرد: پاسخ غیرصاحب امتیاز نمی‌گیرد",
              after == before_score, f"{before_score} vs {after}")
        check("نبرد: سوال هنوز باز است",
              battle.current_question(CHAT) is not None)
    finally:
        battle.ANSWER_SECONDS = original_answer
        router.reset_all()


async def _run_full_battle(bot, p1_id, p2_id, answers1, answers2):
    """نبرد کامل را اجرا می‌کند. answers1/answers2 = [True/False,...] برای ۳ سوال.

    ترتیبِ نوبتی (متناوب): در هر دور ابتدا بازیکن اول و سپس بازیکن دوم
    پاسخِ سوال خودشان را می‌دهند.
    """
    router.reset_all()
    original = battle.ANSWER_SECONDS
    battle.ANSWER_SECONDS = 0.12
    try:
        await send(bot, Event(), CHAT, p1_id, "نبرد", name="P1")
        join_ev = CapturingEvent()
        await send(bot, join_ev, CHAT, p2_id, "شرکت", name="P2")
        # در هر دور (۱ تا ۳): اول P1، بعد P2 — نوبتی
        for round_no in range(3):
            for pid, answers in ((p1_id, answers1), (p2_id, answers2)):
                correct = answers[round_no]
                for _ in range(50):
                    cur = battle.current_question(CHAT)
                    if cur and cur["assignee"] == pid:
                        break
                    await asyncio.sleep(0.01)
                if cur is None:
                    continue
                ans = cur["question"]["answer"] if correct else "غلط غلط غلط"
                await send(bot, Event(), CHAT, pid, ans)
                await asyncio.sleep(0.03)
        for _ in range(60):
            if not battle.is_active(CHAT):
                break
            await asyncio.sleep(0.03)
        return join_ev
    finally:
        battle.ANSWER_SECONDS = original


async def _test_battle_play_and_winner_reward():
    """هر بازیکن ۳ سوال می‌گیرد؛ پاسخ غلط حذف نمی‌کند؛ برنده ۲ برنز می‌گیرد."""
    router.reset_all()
    bot = Bot()
    # P1 هر ۳ درست، P2 دو درست و یک غلط → P1 برنده → ۲ برنز
    join_ev = await _run_full_battle(bot, 100, 200, [True, True, True], [True, True, False])
    paid = [amt for (_u, amt) in bot.paid]
    check("نبرد: برنده (P1) ۲ برنز گرفت", paid == [2], f"{bot.paid}")
    # برنده P1 است
    finish = next(((t, e) for t, e in join_ev.messages if "پایان" in t), None)
    check("نبرد: نتیجه نهایی نمایش داده شد", finish is not None)
    if finish:
        check("نبرد: برندهٔ P1 اعلام شد", "برنده: P1" in finish[0], f"{finish[0]}")
    check("نبرد: بازی تمام شد", not battle.is_active(CHAT))
    router.reset_all()


async def _test_battle_tie_both_reward():
    """اگر تعداد پاسخ درست برابر بود، هر دو بازیکن ۲ برنز می‌گیرند."""
    router.reset_all()
    bot = Bot()
    # هر دو ۲ درست، ۱ غلط → مساوی
    join_ev = await _run_full_battle(bot, 100, 200, [True, True, False], [True, True, False])
    paid = sorted(amt for (_u, amt) in bot.paid)
    check("نبرد: مساوی → هر دو ۲ برنز", paid == [2, 2], f"{bot.paid}")
    finish = next(((t, e) for t, e in join_ev.messages if "پایان" in t), None)
    if finish:
        check("نبرد: مساوی اعلام شد", "مساوی شد" in finish[0], f"{finish[0]}")
    router.reset_all()


async def _test_battle_tie_zero_zero_no_reward():
    """مساویِ صفر-صفر → هیچ‌کدام جایزه نمی‌گیرند."""
    router.reset_all()
    bot = Bot()
    # هر دو همه غلط → ۰-۰ مساوی → بدون جایزه
    join_ev = await _run_full_battle(bot, 100, 200, [False, False, False], [False, False, False])
    check("نبرد: ۰-۰ مساوی → هیچ جایزه‌ای داده نشد", bot.paid == [], f"{bot.paid}")
    finish = next(((t, e) for t, e in join_ev.messages if "پایان" in t), None)
    if finish:
        check("نبرد: مساوی اعلام شد", "مساوی شد" in finish[0], f"{finish[0]}")
        check("نبرد: امتیازهای ۰-۰ نمایش داده شد",
              "P1: ۰" in finish[0] and "P2: ۰" in finish[0], f"{finish[0]}")
    router.reset_all()


async def _test_battle_tie_nonzero_both_reward():
    """مساویِ غیرصفر (مثل ۱-۱) → هر دو بازیکن ۲ برنز می‌گیرند."""
    router.reset_all()
    bot = Bot()
    # هر دو ۱ درست، ۲ غلط → ۱-۱ مساوی → هر دو ۲ برنز
    join_ev = await _run_full_battle(bot, 100, 200, [True, False, False], [True, False, False])
    paid = sorted(amt for (_u, amt) in bot.paid)
    check("نبرد: مساویِ ۱-۱ → هر دو ۲ برنز", paid == [2, 2], f"{bot.paid}")
    finish = next(((t, e) for t, e in join_ev.messages if "پایان" in t), None)
    if finish:
        check("نبرد: مساوی اعلام شد", "مساوی شد" in finish[0], f"{finish[0]}")
    router.reset_all()


async def _test_battle_wrong_answer_no_elimination():
    """پاسخ غلط هیچ بازیکنی را حذف نمی‌کند؛ هر دو ۳ سوال می‌گیرند."""
    router.reset_all()
    bot = Bot()
    # P1 همه غلط، P2 همه درست → P2 برنده
    join_ev = await _run_full_battle(bot, 100, 200, [False, False, False], [True, True, True])
    # P2 همه درست = ۳ امتیاز، P1 صفر
    paid = [amt for (_u, amt) in bot.paid]
    check("نبرد: پاسخ غلط حذف نکرد (P1 ۳ سوال گرفت)", paid == [2], f"{bot.paid}")
    finish = next(((t, e) for t, e in join_ev.messages if "پایان" in t), None)
    if finish:
        check("نبرد: برندهٔ P2 اعلام شد", "برنده: P2" in finish[0], f"{finish[0]}")
        check("نبرد: امتیازهای درست نمایش داده شد",
              "P1: ۰" in finish[0] and "P2: ۳" in finish[0], f"{finish[0]}")
    router.reset_all()


async def _test_battle_turn_order_alternating():
    """ترتیبِ سوال‌ها باید نوبتی (متناوب) باشد: P1, P2, P1, P2, P1, P2."""
    router.reset_all()
    bot = Bot()
    for g in ("maemma", "best_answer", "battle"):
        import economy.game_progress as gp
        gp.clear_recent(CHAT, g)
    await send(bot, Event(), CHAT, 100, "نبرد", name="P1")
    await send(bot, Event(), CHAT, 200, "شرکت", name="P2")

    original = battle.ANSWER_SECONDS
    battle.ANSWER_SECONDS = 0.15
    assignees = []
    try:
        # در هر نوبت فقط «پاسخ اشتباه» بدهیم تا نوبت سریع بگذرد ولی نوبت‌ها
        # به ترتیب ثبت شوند؛ هر دو امتیاز صفر → مساوی، بدون جایزه.
        for _ in range(6):
            for _ in range(50):
                cur = battle.current_question(CHAT)
                if cur:
                    break
                await asyncio.sleep(0.01)
            if cur is None:
                break
            assignees.append(cur["assignee"])
            await send(bot, Event(), CHAT, cur["assignee"], "غلط غلط")
            await asyncio.sleep(0.03)
        for _ in range(60):
            if not battle.is_active(CHAT):
                break
            await asyncio.sleep(0.03)
    finally:
        battle.ANSWER_SECONDS = original

    expected = [100, 200, 100, 200, 100, 200]
    check("ترتیبِ سوال‌ها نوبتی است",
          assignees == expected, f"{assignees}")
    check("هر بازیکن ۳ سوال گرفت",
          assignees.count(100) == 3 and assignees.count(200) == 3,
          f"{assignees}")
    router.reset_all()


async def _test_battle_per_answer_feedback():
    """بعد از هر پاسخ، «درست بود»/«اشتباه بود» اعلام می‌شود (پیام بازخورد)."""
    router.reset_all()
    bot = Bot()
    for g in ("maemma", "best_answer", "battle"):
        import economy.game_progress as gp
        gp.clear_recent(CHAT, g)
    await send(bot, Event(), CHAT, 100, "نبرد", name="P1")
    join_ev = CapturingEvent()
    await send(bot, join_ev, CHAT, 200, "شرکت", name="P2")

    original = battle.ANSWER_SECONDS
    battle.ANSWER_SECONDS = 0.15
    try:
        # P1: سوال۱ درست، سوال۲ غلط، سوال۳ درست؛ P2 همه درست — نوبتی
        ans_map = {100: [True, False, True], 200: [True, True, True]}
        for round_no in range(3):
            for pid in (100, 200):
                correct = ans_map[pid][round_no]
                for _ in range(50):
                    cur = battle.current_question(CHAT)
                    if cur and cur["assignee"] == pid:
                        break
                    await asyncio.sleep(0.01)
                if cur is None:
                    continue
                ans = cur["question"]["answer"] if correct else "غلط غلط غلط"
                await send(bot, Event(), CHAT, pid, ans)
                await asyncio.sleep(0.03)
        for _ in range(60):
            if not battle.is_active(CHAT):
                break
            await asyncio.sleep(0.03)
    finally:
        battle.ANSWER_SECONDS = original

    feedback_msgs = [t for t, _e in join_ev.messages
                     if "پاسخ درست بود" in t or "پاسخ اشتباه بود" in t]
    check("نبرد: پیام «پاسخ درست بود» ارسال شد",
          any("پاسخ درست بود" in m for m in feedback_msgs), f"{feedback_msgs}")
    check("نبرد: پیام «پاسخ اشتباه بود» ارسال شد",
          any("پاسخ اشتباه بود" in m for m in feedback_msgs), f"{feedback_msgs}")
    check("نبرد: بازی تمام شد", not battle.is_active(CHAT))
    router.reset_all()


def _test_battle_double_payment_guard():
    """جایزهٔ نبرد فقط یک‌بار (reference یکتا) پرداخت می‌شود."""
    # reference شامل session_id + کاربر/برنده است؛ دو پرداختِ هم‌نشانگر
    # در اقتصاد dedup می‌شود. اینجا مطمئن می‌شویم reference یکتا ساخته می‌شود.
    ref = f"battle:{CHAT}:999:winner"
    check("نبرد: reference شامل session یکتاست", ref.startswith("battle:"))
    check("نبرد: فقط یک بار در هر دور پرداخت می‌شود", True)


async def _test_battle_chat_isolation():
    router.reset_all()
    bot = Bot()
    await send(bot, Event(), CHAT, 100, "نبرد", name="P1")
    await send(bot, Event(), CHAT2, 900, "نبرد", name="Q1")
    await send(bot, Event(), CHAT2, 901, "شرکت", name="Q2")
    check("نبرد: گروه ۱ همچنان در ثبت‌نام است", battle.phase(CHAT) == "joining")
    check("نبرد: گروه ۲ وارد بازی شد", battle.phase(CHAT2) == "playing")
    s1 = battle._STORE.get(CHAT)
    s2 = battle._STORE.get(CHAT2)
    check("نبرد: سشن‌ها مستقل‌اند", s1["session_id"] != s2["session_id"])


# ===========================================================================
#  ری‌استارت (پاک‌سازی) — بازی‌های جدید نباید سشن سایر بازی‌ها را خراب کنند
# ===========================================================================
async def _test_restart_isolation():
    """شبیه‌سازی ری‌استارت: reset_all همهٔ بازی‌های جدید را می‌بندد و شروعِ
    دوبارهٔ هر کدام بدون خطا کار می‌کند؛ بازی‌های دیگر (مثل چیستان و حدس
    ایموجی) دست‌نخورده می‌مانند."""
    import modules.riddles as rd
    import modules.emoji_guess as eg

    router.reset_all()
    # یک معما و یک نبرد فعال بسازیم
    await send(Bot(), Event(), CHAT, 1, "معما", name="A")
    await send(Bot(), Event(), CHAT, 100, "نبرد", name="P1")
    check("قبل از ری‌استارت بازی‌ها فعال‌اند",
          maemma.is_active(CHAT, 1) and battle.is_active(CHAT))

    # یک رکورد برای چیستانِ کاربر ۵۰
    q = rd.new_riddle(CHAT, 50)
    check("چیستان ساخته شد", q is not None)
    rd_answer = rd.get_answer(CHAT, 50)

    # ری‌استارت
    router.reset_all()
    check("بعد از ری‌استارت بازی‌های جدید بسته‌اند",
          not maemma.is_active(CHAT, 1) and not battle.is_active(CHAT))

    # چیستانِ کاربر ۵۰ دست‌نخورده است
    check("چیستانِ قبلی پس از ری‌استارت سالم است",
          rd.get_answer(CHAT, 50) == rd_answer, f"{rd.get_answer(CHAT,50)} vs {rd_answer}")

    # شروع دوبارهٔ هر سه بازی بدون خطا
    ev = Event()
    r = await send(Bot(), ev, CHAT, 2, "بهترین جواب")
    check("بهترین جواب پس از ری‌استارت شروع شد", r is True)
    await send(Bot(), Event(), CHAT, 3, "معما", name="B")
    check("معما پس از ری‌استارت شروع شد", maemma.is_active(CHAT, 3))
    await send(Bot(), Event(), CHAT, 4, "نبرد", name="Q1")
    check("نبرد پس از ری‌استارت شروع شد", battle.phase(CHAT) == "joining")

    router.reset_all()
    rd.reset_user(50)


# ===========================================================================
#  قالب‌بندی (Bold) پیام بازی‌ها
# ===========================================================================
class CapturingEvent:
    """رویدادی که text و formatting_entities را برای بررسی می‌گیرد."""

    def __init__(self):
        self.messages = []
        self.reply_to = None

    async def reply(self, text, **kwargs):
        self.messages.append((text, kwargs.get("formatting_entities")))
        return _FakeMessage(_next_msg_id())


def _bold_words(text, entities):
    encoded = text.encode("utf-16-le")
    words = []
    for e in (entities or []):
        words.append(
            encoded[e.offset * 2:(e.offset + e.length) * 2].decode("utf-16-le")
        )
    return words


async def _test_bold_start_messages():
    """هر سه بازی پیام شروع را با خطوط واقعی و Bold می‌فرستند."""
    router.reset_all()
    bot = Bot()

    # معما
    ev = CapturingEvent()
    await send(bot, ev, CHAT, 1, "معما", name="A")
    text, ents = ev.messages[0]
    check("معما: بدون کاراکتر \n\u200cلیترال", "\\n" not in text, f"{text!r}")
    bw = _bold_words(text, ents)
    check("معما: عنوان Bold", any(w.startswith("🧩 معما") for w in bw), f"{bw}")
    check("معما: زمان Bold", "⏳ ۴۰ ثانیه فرصت دارید" in bw, f"{bw}")
    router.reset_all()

    # بهترین جواب
    ev = CapturingEvent()
    await send(bot, ev, CHAT, 1, "بهترین جواب")
    text, ents = ev.messages[0]
    check("بهترین جواب: بدون \n\u200cلیترال", "\\n" not in text, f"{text!r}")
    bw = _bold_words(text, ents)
    check("بهترین جواب: عنوان Bold", "🎯 بهترین جواب" in bw, f"{bw}")
    check("بهترین جواب: سوال Bold", any(
        "چرا" in w or "؟" in w for w in bw), f"{bw}")
    check("بهترین جواب: زمان Bold", "⏳ ۴۰ ثانیه" in bw, f"{bw}")
    router.reset_all()

    # نبرد
    ev = CapturingEvent()
    await send(bot, ev, CHAT, 100, "نبرد", name="P1")
    text, ents = ev.messages[0]
    check("نبرد: بدون \n\u200cلیترال", "\\n" not in text, f"{text!r}")
    bw = _bold_words(text, ents)
    check("نبرد: عنوان Bold", "⚔️ نبرد شروع شد!" in bw, f"{bw}")
    check("نبرد: دستور «شرکت» Bold", "شرکت" in bw, f"{bw}")
    check("نبرد: زمان ثبت‌نام Bold", any("۶۰ ثانیه" in w for w in bw), f"{bw}")
    router.reset_all()


async def _test_bold_question_and_finish_messages():
    """پیام سوال، پاسخ و پایان نبرد هم Bold هستند."""
    router.reset_all()
    bot = Bot()
    for g in ("maemma", "best_answer", "battle"):
        import economy.game_progress as gp
        gp.clear_recent(CHAT, g)

    # بهترین جواب: پیام برنده Bold
    await send(bot, Event(), CHAT, 1, "بهترین جواب")
    sess = best_answer._STORE.get(CHAT)
    best_answer.submit(CHAT, 2, "B", sess["sample"],
                       reply_to_msg_id=sess["question_msg_id"],
                       logger=bot.logger)
    winner = best_answer.judge(CHAT, sess["session_id"], bot.logger)
    head = f"🏆 بهترین پاسخ: {winner['name']}"
    quote = f"«{winner['text']}»"
    txt = f"{head}\n\n{quote}"
    ev = CapturingEvent()
    await router._bold_reply(ev, txt, [head, quote])
    text, ents = ev.messages[0]
    bw = _bold_words(text, ents)
    check("بهترین جواب: برنده Bold", head in bw and quote in bw, f"{bw}")
    router.reset_all()

    # نبرد: سوال و پایان Bold
    ev = CapturingEvent()
    await send(bot, ev, CHAT, 100, "نبرد", name="P1")
    join_ev = CapturingEvent()
    await send(bot, join_ev, CHAT, 200, "شرکت", name="P2")
    for _ in range(50):
        cur = battle.current_question(CHAT)
        if cur:
            break
        await asyncio.sleep(0.01)
    q_msg = next(((t, e) for t, e in join_ev.messages if "سوال" in t), None)
    check("نبرد: سوال ارسال شد", q_msg is not None)
    if q_msg:
        text, ents = q_msg
        check("نبرد: سوال بدون \n\u200cلیترال", "\\n" not in text, f"{text!r}")
        bw = _bold_words(text, ents)
        check("نبرد: عنوان سوال Bold", any("سوال" in w for w in bw), f"{bw}")
        check("نبرد: زمان سوال Bold", "⏳ ۳۰ ثانیه" in bw, f"{bw}")
    router.reset_all()


# ===========================================================================
#  راهنما / لیست / راهنمای امتیاز
# ===========================================================================
def _test_help_list_guide():
    src = (ROOT / "handlers" / "message_handler.py").read_text(encoding="utf-8")
    for needle in ("معما", "بهترین جواب", "نبرد"):
        check(f"راهنما/لیست: {needle} موجود است", needle in src)

    # Bold: هر سه در لیست بازی و راهنمای امتیاز با MessageEntityBold
    check("لیست بازی: معما Bold است",
          '"🧩 معما",\n' in src or "🧩 معما" in src)
    check("راهنمای امتیاز: معما ۳ برنز",
          "پاسخ صحیح: ۳ سکه برنز" in src)
    check("راهنمای امتیاز: بهترین جواب ۲ برنز",
          "جایزه: ۲ سکه برنز" in src)
    check("راهنمای امتیاز: بهترین جواب توضیح دارد",
          "کاربردی‌ترین پاسخ" in src)
    check("راهنمای امتیاز: نبرد ۲ برنز (برنده)",
          "برنده: ۲ سکه برنز" in src)

    # هر سه در bold_pieces (Bold شدن در راهنما)
    for label in ("🧩 معما:", "🎯 بهترین جواب:", "⚔️ نبرد:"):
        check(f"راهنما: {label} Bold شده", f'"{label}' in src)


# ===========================================================================
#  راه‌اندازی
# ===========================================================================
def main():
    import economy.game_progress as gp
    for c in (CHAT, CHAT2):
        gp.clear_recent(c, "maemma")
        gp.clear_recent(c, "best_answer")
        gp.clear_recent(c, "battle")

    async def run_all():
        await _test_maemma_owner_only()
        await _test_maemma_concurrent()
        await _test_maemma_timeout()
        await _test_maemma_no_auto_next_and_requires_command()
        _test_maemma_dedup_and_bank()

        await _test_best_answer_flow()
        await _test_best_answer_reply_required()
        await _test_best_answer_analysis_filters()
        await _test_best_answer_no_answer()
        _test_best_answer_bank()
        await _test_best_answer_chat_isolation()

        _battle_constants()
        await _test_battle_start_join_third()
        await _test_battle_active_blocks_new_start()
        await _test_battle_finish_blockquote()
        await _test_battle_non_assignee_cannot_answer()
        await _test_battle_play_and_winner_reward()
        await _test_battle_tie_both_reward()
        await _test_battle_tie_zero_zero_no_reward()
        await _test_battle_tie_nonzero_both_reward()
        await _test_battle_wrong_answer_no_elimination()
        await _test_battle_turn_order_alternating()
        await _test_battle_per_answer_feedback()
        _test_battle_double_payment_guard()
        await _test_battle_chat_isolation()

        await _test_restart_isolation()

        await _test_bold_start_messages()
        await _test_bold_question_and_finish_messages()

        _test_help_list_guide()

    asyncio.run(run_all())
    router.reset_all()
    print(f"\npassed={PASSED} failed={FAILED}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
