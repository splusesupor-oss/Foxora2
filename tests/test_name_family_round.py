"""بازی اسم فامیل: ثبت پاسخ، تایمر و نمایش نتایج.

    python tests/test_name_family_round.py
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import modules.name_family as nf

PASSED = FAILED = 0
CHAT = -100555


def check(label, cond, detail=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  PASS  {label}")
    else:
        FAILED += 1
        print(f"  FAIL  {label} {detail}")


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


def answers_for(letter):
    return "\n".join(nf.ROUND_EXAMPLES[letter])


def fresh_round():
    nf.reset_all()
    game = nf.start(CHAT)
    return game, answers_for(game["letter"])


# --------------------------------------------------------------------------
def test_answer_formats_recorded():
    """پاسخ‌ها با هر قالب واقعی صفحه‌کلید ثبت می‌شوند."""
    print("\n### ثبت پاسخ در قالب‌های واقعی")
    cases = {
        "ساده": lambda t: t,
        "خط جدید انتهایی": lambda t: t + "\n",
        "فاصله و خط انتهایی": lambda t: t + "   \n",
        "خط خالی وسط": lambda t: t.replace("\n", "\n\n", 1),
        "CRLF": lambda t: t.replace("\n", "\r\n"),
        "خط خالی ابتدایی": lambda t: "\n" + t,
        "فاصله دور هر خط": lambda t: "\n".join(f"  {x}  " for x in t.split("\n")),
    }
    for label, mutate in cases.items():
        _game, text = fresh_round()
        points = nf.submit(CHAT, 1, "U", mutate(text))
        check(f"{label}: ثبت شد", points is not None and points > 0, f"-> {points}")


def test_invalid_shapes_rejected():
    print("\n### ورودی نامعتبر همچنان رد می‌شود")
    _game, text = fresh_round()
    check("کمتر از ۷ پاسخ رد می‌شود",
          nf.submit(CHAT, 2, "U", "\n".join(text.split("\n")[:5])) is None)
    _game, text = fresh_round()
    check("بیشتر از ۷ پاسخ رد می‌شود",
          nf.submit(CHAT, 3, "U", text + "\nاضافه") is None)
    _game, text = fresh_round()
    check("جداکنندهٔ قدیمی رد می‌شود",
          nf.submit(CHAT, 4, "U", text.replace("\n", " | ", 1)) is None)
    nf.reset_all()
    check("بدون دور فعال ثبت نمی‌شود", nf.submit(CHAT, 5, "U", "a\nb") is None)


def test_one_submission_per_user():
    print("\n### هر کاربر فقط یک بار ثبت می‌کند")
    _game, text = fresh_round()
    first = nf.submit(CHAT, 10, "U", text)
    second = nf.submit(CHAT, 10, "U", text)
    check("ثبت اول موفق", first is not None)
    check("ثبت دوم رد می‌شود", second is None, f"-> {second}")


def test_results_always_delivered():
    print("\n### نتایج همیشه ارسال می‌شوند")

    async def scenario():
        game, text = fresh_round()
        nf.submit(CHAT, 20, "P1", text)
        got = []

        async def on_results(ranking):
            got.append(ranking)

        nf.schedule_round(CHAT, game["round_id"], on_results, seconds=0.1)
        await asyncio.sleep(0.35)
        return got

    got = asyncio.run(scenario())
    check("نتایج ارسال شد", len(got) == 1, f"-> {len(got)}")
    check("رتبه‌بندی شامل بازیکن است", got and len(got[0]) == 1)
    check("دور آزاد شد", not nf.is_active(CHAT))


def test_cancelled_timer_still_delivers():
    """لغو تایمر نباید نتایج را از بین ببرد یا دور را قفل کند."""
    print("\n### لغو تایمر: نتایج باز هم ارسال می‌شوند")

    async def scenario():
        game, text = fresh_round()
        nf.submit(CHAT, 30, "P", text)
        got = []
        logger = Logger()

        async def on_results(ranking):
            got.append(ranking)

        task = nf.schedule_round(
            CHAT, game["round_id"], on_results, logger=logger, seconds=0.5
        )
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0.1)
        return got, logger

    got, logger = asyncio.run(scenario())
    check("نتایج با وجود لغو ارسال شد", len(got) == 1, f"-> {len(got)}")
    check("دور قفل نشد", not nf.is_active(CHAT))
    check("لغو لاگ شد", logger.has("TIMER CANCELLED"))


def test_results_exception_releases_round():
    print("\n### خطا در ارسال نتایج، بازی را قفل نمی‌کند")

    async def scenario():
        game, text = fresh_round()
        nf.submit(CHAT, 40, "P", text)
        logger = Logger()

        async def boom(_ranking):
            raise RuntimeError("reply failed")

        nf.schedule_round(CHAT, game["round_id"], boom, logger=logger, seconds=0.1)
        await asyncio.sleep(0.35)
        return logger

    logger = asyncio.run(scenario())
    check("دور آزاد شد", not nf.is_active(CHAT))
    check("خطا لاگ شد", logger.has("RESULTS FAILED"))
    check("بازی بعدی شروع می‌شود", nf.start(CHAT) is not None)


def test_many_users_all_recorded():
    print("\n### چند کاربر هم‌زمان")

    async def scenario(count):
        game, text = fresh_round()
        for uid in range(1, count + 1):
            nf.submit(CHAT, uid, f"U{uid}", text)
        got = []

        async def on_results(ranking):
            got.append(ranking)

        nf.schedule_round(CHAT, game["round_id"], on_results, seconds=0.1)
        await asyncio.sleep(0.3)
        return got

    for count in (5, 20, 50):
        got = asyncio.run(scenario(count))
        ranked = len(got[0]) if got else 0
        check(f"{count} کاربر: همه ثبت و رتبه‌بندی شدند",
              ranked == count, f"-> {ranked}")


def test_sequential_rounds():
    print("\n### چند بازی پشت سر هم")

    async def scenario(rounds):
        nf.reset_all()
        done = []
        for index in range(rounds):
            game = nf.start(CHAT)
            if game is None:
                break
            nf.submit(CHAT, 200 + index, f"P{index}", answers_for(game["letter"]))

            async def on_results(ranking, i=index):
                done.append((i, len(ranking)))

            nf.schedule_round(CHAT, game["round_id"], on_results, seconds=0.05)
            await asyncio.sleep(0.15)
        return done

    done = asyncio.run(scenario(6))
    check("هر ۶ دور کامل شد", len(done) == 6, f"-> {done}")
    check("هر دور دقیقاً یک بازیکن داشت", all(n == 1 for _i, n in done), f"-> {done}")


def test_finish_is_idempotent():
    print("\n### finish دو بار نتیجه تولید نمی‌کند")
    game, text = fresh_round()
    nf.submit(CHAT, 60, "P", text)
    first = nf.finish(CHAT, game["round_id"])
    second = nf.finish(CHAT, game["round_id"])
    check("بار اول رتبه‌بندی برمی‌گردد", len(first) == 1)
    check("بار دوم خالی است", second == [], f"-> {second}")


def test_stale_timer_cannot_close_new_round():
    print("\n### تایمر قدیمی دور جدید را نمی‌بندد")
    game_a, text = fresh_round()
    nf.submit(CHAT, 70, "P", text)
    nf.finish(CHAT, game_a["round_id"])
    game_b = nf.start(CHAT)
    check("دور جدید شروع شد", game_b is not None)
    stale = nf.finish(CHAT, game_a["round_id"])
    check("round_id قدیمی رد می‌شود", stale == [], f"-> {stale}")
    check("دور جدید هنوز فعال است", nf.is_active(CHAT))
    nf.reset_all()


def test_cancel_round_helper():
    print("\n### cancel_round دور را تمیز می‌بندد")
    game, _text = fresh_round()
    check("دور فعال است", nf.is_active(CHAT))
    check("cancel_round موفق بود", nf.cancel_round(CHAT) is True)
    check("دور بسته شد", not nf.is_active(CHAT))
    check("نتیجه‌ای تولید نشد", nf.finish(CHAT, game["round_id"]) == [])
    check("بازی بعدی شروع می‌شود", nf.start(CHAT) is not None)
    nf.reset_all()


def test_state_isolated_from_other_games():
    print("\n### استقلال کامل از سایر بازی‌ها")
    import modules.flag_guess as fg
    import modules.riddles as rd
    import modules.fill_blank as fb

    nf.reset_all()
    fg.reset_history()
    rd.used_riddles.clear()

    game, text = fresh_round()
    nf.submit(CHAT, 80, "P", text)
    fg.start(CHAT, 80)
    rd.new_riddle(CHAT, 80)
    fb.new_fill(CHAT, 80)

    check("دور اسم فامیل هنوز فعال است", nf.is_active(CHAT))
    check("پاسخ ثبت‌شده باقی است", len(nf._ACTIVE[CHAT]["answers"]) == 1)

    own = {id(nf._ACTIVE), id(nf._ROUND_TASKS), id(nf._FINISHED_ROUNDS)}
    other = {id(fg._ACTIVE), id(fg._SEEN_HISTORY), id(rd.active_riddles),
             id(rd.used_riddles), id(fb.active_fill)}
    check("هیچ ساختار داده‌ای مشترک نیست", not (own & other))

    # پایان بازی‌های دیگر نباید اسم فامیل را خراب کند
    fg.finish(CHAT)
    rd.check_answer(CHAT, 80, "پاسخ غلط")
    check("پس از پایان بازی‌های دیگر، دور سالم است", nf.is_active(CHAT))
    ranking = nf.finish(CHAT, game["round_id"])
    check("رتبه‌بندی سالم است", len(ranking) == 1, f"-> {ranking}")
    nf.reset_all()


def test_logging_covers_lifecycle():
    print("\n### لاگ کامل چرخهٔ بازی")

    async def scenario():
        game, text = fresh_round()
        logger = Logger()
        nf.submit(CHAT, 90, "P", text, logger=logger)

        async def on_results(_r):
            pass

        nf.schedule_round(CHAT, game["round_id"], on_results,
                          logger=logger, seconds=0.05)
        await asyncio.sleep(0.25)
        return logger

    logger = asyncio.run(scenario())
    for needle in ("NAME FAMILY TRACE SUBMIT_PARSED", "NAME FAMILY VALIDATION",
                   "NAME FAMILY TIMER END", "NAME FAMILY RESULTS START",
                   "NAME FAMILY RESULTS SENT"):
        check(f"لاگ موجود: {needle}", logger.has(needle))


def main():
    test_answer_formats_recorded()
    test_invalid_shapes_rejected()
    test_one_submission_per_user()
    test_results_always_delivered()
    test_cancelled_timer_still_delivers()
    test_results_exception_releases_round()
    test_many_users_all_recorded()
    test_sequential_rounds()
    test_finish_is_idempotent()
    test_stale_timer_cannot_close_new_round()
    test_cancel_round_helper()
    test_state_isolated_from_other_games()
    test_logging_covers_lifecycle()

    print(f"\n{'=' * 52}")
    print(f"passed={PASSED} failed={FAILED}")
    print("=" * 52)
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
