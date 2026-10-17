"""چکاپ کامل همهٔ بازی‌ها — تست دائمی رگرسیون.

هر باگی که در چکاپ سراسری پیدا و رفع شد اینجا یک تست دارد، تا دوباره
برنگردد. مسیرهای واقعی ماژول‌ها اجرا می‌شوند (بدون mock کردن منطق بازی).

    python tests/test_games_audit.py
"""
import asyncio
import pathlib
import sys
import tempfile
import time
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ⚠️ پیش از import هر بازی اجرا می‌شود: خودِ economy هنگام import مسیر
# config/economy.json واقعی را می‌بندد و نوشتن‌های بعدی همان‌جا می‌نشیند.
import economy.storage as _storage
_storage.use_file(pathlib.Path(tempfile.mkdtemp()) / "economy.json")


import economy as coins
import economy.storage as _economy_storage
import modules.emoji_guess as eg
import modules.fill_blank as fb
import modules.flag_guess as fg
import modules.multiple_choice as mc
import modules.name_family as nf
import modules.riddles as rd
import modules.word_correction as wc
from modules.fox_games import laugh_or_lose as lol
from modules.fox_games import lucky_box as lb
from modules.fox_games import survival as sv
from modules.fox_games import vampire as vp

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


class Logger:
    def __init__(self):
        self.info, self.errors = [], []

    def log_info(self, m):
        self.info.append(m)

    def log_error(self, m):
        self.errors.append(m)

    def has(self, needle):
        return any(needle in m for m in self.info + self.errors)


class User:
    def __init__(self, uid, name=None):
        self.id = uid
        self.first_name = name
        self.last_name = None
        self.username = None


# ---------------------------------------------------------------------------
# 🧩 چیستان
# ---------------------------------------------------------------------------
def test_riddle():
    print("\n### 🧩 چیستان")
    rd.reset_all()
    CHAT = -5001

    # سوال تکراری برای یک کاربر تا پایان دور کامل
    seen = [rd.new_riddle(CHAT, 1) for _ in range(len(rd.RIDDLES))]
    check("هیچ چیستان تکراری در یک دور کامل", len(set(seen)) == len(rd.RIDDLES),
          f"-> {len(set(seen))}/{len(rd.RIDDLES)}")

    # تاریخچهٔ هر کاربر مستقل است
    rd.reset_all()
    [rd.new_riddle(CHAT, 1) for _ in range(len(rd.RIDDLES))]
    fresh = [rd.new_riddle(CHAT, 2) for _ in range(len(rd.RIDDLES))]
    check("کاربر دوم دور کامل مستقل دارد", len(set(fresh)) == len(rd.RIDDLES),
          f"-> {len(set(fresh))}")

    # پاسخ درست/غلط
    rd.reset_all()
    rd.new_riddle(CHAT, 3)
    answer = rd.get_answer(CHAT, 3)
    check("پاسخ غلط رد می‌شود", rd.check_answer(CHAT, 3, "یک چیز بی‌ربط") is False)
    check("پاسخ درست پذیرفته می‌شود", rd.check_answer(CHAT, 3, answer) is True)
    check("بعد از پاسخ درست، بازی بسته شد", rd.get_answer(CHAT, 3) is None)
    check("سکه دوباره داده نمی‌شود",
          rd.check_answer(CHAT, 3, answer) is False)

    # نرمال‌سازی: نیم‌فاصله و «ي/ك» عربی
    rd.reset_all()
    rd.new_riddle(CHAT, 4)
    real = rd.get_answer(CHAT, 4)
    variant = real.replace("ی", "ي").replace("ک", "ك")
    check("شکل عربی حروف هم پذیرفته می‌شود",
          rd.check_answer(CHAT, 4, variant) is True)

    # چند کاربر هم‌زمان در یک گروه
    rd.reset_all()
    rd.new_riddle(CHAT, 10)
    rd.new_riddle(CHAT, 11)
    a10, a11 = rd.get_answer(CHAT, 10), rd.get_answer(CHAT, 11)
    check("دو کاربر هم‌زمان state جدا دارند", a10 is not None and a11 is not None)
    check("پاسخ کاربر ۱۰ روی کاربر ۱۱ اثر ندارد",
          rd.check_answer(CHAT, 10, a10) is True and rd.get_answer(CHAT, 11) == a11)

    # پاسخ کاربر دیگر امتیاز نمی‌گیرد
    rd.reset_all()
    rd.new_riddle(CHAT, 20)
    stolen = rd.get_answer(CHAT, 20)
    check("کاربر بدون چیستان فعال امتیاز نمی‌گیرد",
          rd.check_answer(CHAT, 21, stolen) is False)

    # انقضای زمان
    rd.reset_all()
    rd.new_riddle(CHAT, 30)
    expired = rd.get_answer(CHAT, 30)
    rd.active_riddles[(CHAT, 30)]["time"] = time.time() - rd.RIDDLE_TIMEOUT - 1
    check("بعد از پایان زمان، پاسخ درست هم رد می‌شود",
          rd.check_answer(CHAT, 30, expired) is False)
    check("state منقضی پاک شد", rd.get_answer(CHAT, 30) is None)

    # توکن: تایمر دور قبلی نباید دور جدید را ببندد
    rd.reset_all()
    rd.new_riddle(CHAT, 40)
    stale = rd.get_token(CHAT, 40)
    rd.check_answer(CHAT, 40, rd.get_answer(CHAT, 40))
    rd.new_riddle(CHAT, 40)
    check("تایمر قدیمی دور جدید را نمی‌بندد",
          rd.clear(CHAT, 40, stale) is None)
    check("دور جدید هنوز فعال است", rd.get_answer(CHAT, 40) is not None)
    rd.reset_all()


# ---------------------------------------------------------------------------
# 📝 جای خالی
# ---------------------------------------------------------------------------
def test_fill_blank():
    print("\n### 📝 جای خالی")
    fb.reset_all()
    CHAT = -5002

    seen = [fb.new_fill(CHAT, 1) for _ in range(len(fb.FILLS))]
    check("هیچ سوال تکراری در یک دور کامل", len(set(seen)) == len(fb.FILLS),
          f"-> {len(set(seen))}/{len(fb.FILLS)}")

    fb.reset_all()
    [fb.new_fill(CHAT, 1) for _ in range(len(fb.FILLS))]
    fresh = [fb.new_fill(CHAT, 2) for _ in range(len(fb.FILLS))]
    check("تاریخچهٔ هر کاربر جداست", len(set(fresh)) == len(fb.FILLS))

    fb.reset_all()
    fb.new_fill(CHAT, 3)
    ans = fb.get_fill_answer(CHAT, 3)
    check("سوال‌ها درست لود شدند", bool(ans))
    check("پاسخ غلط رد می‌شود", fb.check_fill(CHAT, 3, "کاملا اشتباه") is False)
    check("پاسخ درست پذیرفته می‌شود", fb.check_fill(CHAT, 3, ans) is True)
    check("امتیاز دوباره داده نمی‌شود", fb.check_fill(CHAT, 3, ans) is False)
    check("امتیاز فقط یک بار ثبت شد", fb.get_score(3, CHAT) == 1)

    # امتیاز بین گروه‌ها قاطی نمی‌شود
    fb.reset_all()
    fb.new_fill(-1, 7)
    fb.check_fill(-1, 7, fb.get_fill_answer(-1, 7))
    fb.new_fill(-2, 7)
    fb.check_fill(-2, 7, fb.get_fill_answer(-2, 7))
    check("امتیاز گروه اول جدا شمرده می‌شود", fb.get_score(7, -1) == 1)
    check("امتیاز گروه دوم جدا شمرده می‌شود", fb.get_score(7, -2) == 1)
    check("مجموع کل کاربر درست است", fb.get_score(7) == 2)

    # انقضا
    fb.reset_all()
    fb.new_fill(CHAT, 8)
    expired = fb.get_fill_answer(CHAT, 8)
    fb.active_fill[(CHAT, 8)]["time"] = time.time() - fb.TIMEOUT - 1
    check("بعد از پایان زمان پاسخ رد می‌شود",
          fb.check_fill(CHAT, 8, expired) is False)

    # توکن
    fb.reset_all()
    fb.new_fill(CHAT, 9)
    stale = fb.get_token(CHAT, 9)
    fb.check_fill(CHAT, 9, fb.get_fill_answer(CHAT, 9))
    fb.new_fill(CHAT, 9)
    check("تایمر قدیمی پاسخ دور جدید را لو نمی‌دهد",
          fb.clear(CHAT, 9, stale) is None)
    fb.reset_all()


# ---------------------------------------------------------------------------
# 😀 حدس ایموجی
# ---------------------------------------------------------------------------
def test_emoji_guess():
    print("\n### 😀 حدس ایموجی")
    eg.reset_all()
    CHAT = -5003

    # پس از مصرف بانک، start دور تازه می‌سازد و دیگر None نمی‌دهد؛
    # پس حلقه به اندازهٔ یک دور کامل محدود می‌شود.
    seen = []
    for _ in range(len(eg.PUZZLES)):
        p = eg.start(CHAT, 1)
        if p is None:
            break
        seen.append(p["answer"])
        eg.finish(CHAT, p["token"])
    check("هیچ ایموجی تکراری برای یک کاربر",
          len(set(seen)) == len(seen) == len(eg.PUZZLES), f"-> {len(seen)}")
    check("بعد از اتمام، بازی برای کاربر بسته است", eg.is_exhausted(CHAT, 1))
    check("پیام اتمام درست است",
          eg.EXHAUSTED_MESSAGE == (
              "✅ تمام مراحل حدس ایموجی را انجام داده‌اید. "
              "به‌زودی مراحل جدید اضافه می‌شود."
          ), f"-> {eg.EXHAUSTED_MESSAGE}")

    # کاربر تمام‌شده نمی‌تواند از دور دیگران سکه بگیرد
    eg.reset_all()
    for _ in range(len(eg.PUZZLES)):
        drained = eg.start(CHAT, 1)
        if drained is None:
            break
        eg.finish(CHAT, drained["token"])
    p = eg.start(CHAT, 2)
    check("کاربر تمام‌شده از دور دیگری امتیاز نمی‌گیرد",
          eg.answer(CHAT, 1, "x", p["answer"]) is None)
    check("بازی هنوز برای کاربر دوم فعال است", eg.is_active(CHAT))
    check("کاربر دوم پاسخ درست را می‌گیرد",
          eg.answer(CHAT, 2, "u2", p["answer"]) == p["answer"])

    # ضد فارم: سشن‌ها per-user هستند، پس یک نفر نمی‌تواند با پاسخ دادن
    # به معمای دیگران سکه جمع کند. پیش‌تر همین کار ممکن بود.
    eg.reset_all()
    farmed = []
    for i in range(5):
        p = eg.start(CHAT, 100 + i)
        farmed.append(eg.answer(CHAT, 999, "farmer", p["answer"]))
    check("هیچ پاسخی از دور دیگران پذیرفته نشد",
          not any(farmed), f"-> {farmed}")
    check("فارمر هیچ پیشرفتی نگرفت",
          eg.seen_count(CHAT, 999) == 0,
          f"-> {eg.seen_count(CHAT, 999)}")
    check("معمای صاحبانش دست‌نخورده ماند",
          all(eg.is_active(CHAT, 100 + i) for i in range(5)))

    # ولی هرکس معمای خودش را جواب دهد، در تاریخچهٔ خودش می‌نشیند.
    own = [eg.answer(CHAT, 100 + i, "u",
                     eg.active_state(CHAT, 100 + i)["answer"])
           for i in range(5)]
    check("هر کس معمای خودش را گرفت", all(own), f"-> {own}")
    check("هر کدام یک مرحله در تاریخچه دارند",
          all(eg.seen_count(CHAT, 100 + i) == 1 for i in range(5)))

    # وقتی خودِ کاربر بازی را شروع کند هرگز تکراری نمی‌گیرد.
    eg.reset_all()
    own = []
    for _ in range(25):
        p = eg.start(CHAT, 999)
        own.append(eg.answer(CHAT, 999, "farmer", p["answer"]))
    check("شروع‌کننده در ۲۵ اجرا هیچ معمای تکراری نگرفت",
          len(set(own)) == 25, f"-> {len(set(own))}")

    # پاسخ غلط
    eg.reset_all()
    p = eg.start(CHAT, 3)
    check("پاسخ غلط رد می‌شود", eg.answer(CHAT, 3, "n", "جواب غلط") is None)
    check("بازی بعد از پاسخ غلط فعال می‌ماند", eg.is_active(CHAT))
    check("پاسخ درست پذیرفته می‌شود",
          eg.answer(CHAT, 3, "n", p["answer"]) == p["answer"])
    check("پاسخ دوباره امتیاز نمی‌دهد",
          eg.answer(CHAT, 3, "n", p["answer"]) is None)

    # اجرای دوباره تا وقتی دور فعال است
    # سشن‌ها per-user هستند: فقط خودِ کاربر نمی‌تواند دور بازش را
    # بازنویسی کند؛ کاربر دیگر آزاد است هم‌زمان بازی کند.
    eg.reset_all()
    first = eg.start(CHAT, 4)
    check("همان کاربر دور فعالش را بازنویسی نمی‌کند",
          eg.start(CHAT, 4) is None)
    check("کاربر دیگر می‌تواند هم‌زمان شروع کند",
          eg.start(CHAT, 5) is not None)
    check("دور اول دست‌نخورده باقی ماند",
          eg.active_state(CHAT, 4)["answer"] == first["answer"])
    eg.reset_all()


# ---------------------------------------------------------------------------
# 🌍 حدس پرچم
# ---------------------------------------------------------------------------
def test_flag_guess():
    print("\n### 🌍 حدس پرچم")
    fg.reset_history()
    CHAT = -5004

    seen = []
    while True:
        s = fg.start(CHAT, 1)
        if s is None:
            break
        seen.append(s["answer"])
        fg.finish(CHAT, s["token"])
    check("هیچ پرچم تکراری برای یک کاربر",
          len(set(seen)) == len(seen) == len(fg.COUNTRIES), f"-> {len(seen)}")
    check("تمام شدن لیست درست مدیریت می‌شود", fg.is_exhausted(1))
    check("پیام اتمام تعریف شده است", bool(fg.EXHAUSTED_MESSAGE))

    # پرچم پشت سر هم تکرار نمی‌شود
    fg.reset_history()
    pairs = []
    for i in range(20):
        s = fg.start(CHAT, 50)
        pairs.append(s["answer"])
        fg.finish(CHAT, s["token"])
    check("هیچ پرچمی بلافاصله پشت سر هم نیامد",
          all(a != b for a, b in zip(pairs, pairs[1:])))

    # انتخاب تصادفی است (نه ترتیبی)
    fg.reset_history()
    firsts = set()
    for uid in range(200, 240):
        s = fg.start(CHAT, uid)
        firsts.add(s["answer"])
        fg.finish(CHAT, s["token"])
    check("انتخاب پرچم تصادفی است", len(firsts) > 5, f"-> {len(firsts)}")

    # پاسخ‌دهنده در تاریخچه ثبت می‌شود.
    # هر شروع‌کننده تاریخچهٔ مستقل دارد، پس دو نفر می‌توانند تصادفاً یک
    # پرچم بگیرند؛ ملاک درست این است که هر پرچمِ پاسخ‌داده‌شده در تاریخچهٔ
    # پاسخ‌دهنده بنشیند، نه اینکه ۵ برداشت لزوماً ۵ پرچم متمایز باشد.
    fg.reset_history()
    answered = []
    for i in range(5):
        s = fg.start(CHAT, 300 + i)
        answered.append(fg.answer(CHAT, s["answer"], 777))
    check("هر پاسخ درست ثبت شد", all(answered), f"-> {answered}")
    check("پاسخ‌دهنده در تاریخچهٔ خودش ثبت می‌شود",
          fg.seen_count(777) == len(set(answered)),
          f"-> {fg.seen_count(777)} vs {len(set(answered))}")
    check("همهٔ پرچم‌های پاسخ‌داده‌شده در تاریخچه هستند",
          set(answered) <= fg._SEEN_HISTORY[str(777)])

    # و وقتی خودِ پاسخ‌دهنده بازی را شروع کند، هرگز تکراری نمی‌گیرد.
    fg.reset_history()
    own = []
    for _ in range(25):
        s = fg.start(CHAT, 777)
        own.append(fg.answer(CHAT, s["answer"], 777))
    check("شروع‌کننده در ۲۵ اجرا هیچ پرچم تکراری نگرفت",
          len(set(own)) == 25, f"-> {len(set(own))}")

    # کاربر تمام‌شده امتیاز نمی‌گیرد
    fg.reset_history()
    while fg.start(CHAT, 9) is not None:
        fg.finish(CHAT, fg._ACTIVE[CHAT]["token"]) if CHAT in fg._ACTIVE else None
    s = fg.start(CHAT, 10)
    check("کاربر تمام‌شده از دور دیگری سکه نمی‌گیرد",
          fg.answer(CHAT, s["answer"], 9) is None)
    check("پاسخ غلط رد می‌شود", fg.answer(CHAT, "کشور نامعلوم", 10) is None)
    check("پاسخ درست پذیرفته می‌شود",
          fg.answer(CHAT, s["answer"], 10) == s["answer"])
    fg.reset_history()


# ---------------------------------------------------------------------------
# ✍️ تصحیح کلمات
# ---------------------------------------------------------------------------
def test_word_correction():
    print("\n### ✍️ تصحیح کلمات")
    CHAT = -5005
    wc._active.clear()
    wc._remaining.clear()

    game = wc.start(CHAT)
    check("بازی شروع شد", bool(game["wrong"]) and bool(game["correct"]))
    check("پیام نامرتبط بازی را مصرف نمی‌کند",
          wc.answer(CHAT, "چیستان") is None)
    check("پیام نامرتبط دیگر هم عبور می‌کند",
          wc.answer(CHAT, "سلام بچه‌ها") is None)
    check("تلاش روی همان کلمه، غلط اعلام می‌شود",
          wc.answer(CHAT, game["wrong"]) is False)
    check("بازی بعد از پاسخ غلط فعال می‌ماند", wc.get(CHAT) is not None)
    check("پاسخ درست پذیرفته می‌شود", wc.answer(CHAT, game["correct"]) is True)
    check("بعد از برد بازی بسته شد", wc.get(CHAT) is None)

    # نیم‌فاصله فقط وقتی نادیده گرفته می‌شود که تفاوت غلط و درست در آن
    # نباشد. برای جفت‌هایی مثل «هم اکنون» → «هم‌اکنون» خودِ فاصله‌گذاری
    # همان چیزی است که باید تصحیح شود، پس تایپ با فاصله «درست» نیست.
    wc._active.clear()
    wc._remaining.clear()
    spacing_pair = next(
        (w, c) for w, c in wc.WORDS if wc._letters(w) == wc._letters(c)
    )
    other_pair = next(
        (w, c) for w, c in wc.WORDS if wc._letters(w) != wc._letters(c)
    )

    wc._active[CHAT] = {"wrong": spacing_pair[0], "correct": spacing_pair[1],
                        "token": 1}
    check("در جفت فاصله‌ای، شکل غلط پذیرفته نمی‌شود",
          wc.answer(CHAT, spacing_pair[0]) is False,
          f"-> {spacing_pair}")
    check("در جفت فاصله‌ای، فقط شکل درست پذیرفته می‌شود",
          wc.answer(CHAT, spacing_pair[1]) is True, f"-> {spacing_pair}")

    wc._active[CHAT] = {"wrong": other_pair[0], "correct": other_pair[1],
                        "token": 2}
    loose = other_pair[1].replace("\u200c", " ")
    check("در بقیهٔ جفت‌ها نیم‌فاصله مانع پذیرش نیست",
          wc.answer(CHAT, loose) is True, f"-> {other_pair}")

    # کلمات تکراری تا پایان بانک
    wc._active.clear()
    wc._remaining.clear()
    words = []
    for _ in range(len(wc.WORDS)):
        words.append(wc.start(CHAT)["wrong"])
        wc._active.pop(CHAT, None)
    check("هیچ کلمهٔ تکراری تا پایان بانک", len(set(words)) == len(wc.WORDS))
    wc._active.clear()
    wc._remaining.clear()


# ---------------------------------------------------------------------------
# ❓ چهار گزینه‌ای
# ---------------------------------------------------------------------------
def test_multiple_choice():
    print("\n### ❓ چهار گزینه‌ای")
    CHAT = -5006
    mc.reset_all()

    quiz = mc.start_question(CHAT, 1)
    check("سوال چهار گزینه دارد", len(quiz["options"]) == 4)
    check("پاسخ درست بین ۱ تا ۴ است", 1 <= quiz["answer"] <= 4)
    wrong = str(1 if quiz["answer"] != 1 else 2)
    ok, correct = mc.answer_question(CHAT, wrong)
    check("پاسخ اشتباه امتیاز نمی‌گیرد", ok is False)
    check("گزینهٔ درست اعلام می‌شود", correct == quiz["answer"])
    check("بعد از پاسخ، سوال بسته شد", mc.get_active_question(CHAT) is None)

    quiz = mc.start_question(CHAT, 1)
    ok, _ = mc.answer_question(CHAT, str(quiz["answer"]))
    check("گزینهٔ درست تشخیص داده می‌شود", ok is True)
    check("پاسخ دوباره اثری ندارد",
          mc.answer_question(CHAT, str(quiz["answer"])) is None)

    # متن غیرعددی سوال را مصرف نمی‌کند
    quiz = mc.start_question(CHAT, 1)
    check("متن غیرعددی سوال را نمی‌بندد",
          mc.answer_question(CHAT, "سلام") is None)
    check("سوال هنوز فعال است", mc.get_active_question(CHAT) is not None)

    # توکن تایمر
    stale = quiz["token"]
    mc.clear_question(CHAT, stale)
    new_quiz = mc.start_question(CHAT)
    check("تایمر قدیمی سوال جدید را نمی‌بندد",
          mc.clear_question(CHAT, stale) is False)
    check("سوال جدید فعال ماند", mc.get_active_question(CHAT) is not None)

    # عدم تکرار سوال
    mc.reset_all()
    qs = []
    for _ in range(len(mc.QUESTIONS)):
        qs.append(mc.start_question(CHAT, 1)["question"])
        mc._active_questions.pop(CHAT, None)
    check("هیچ سوال تکراری تا پایان بانک", len(set(qs)) == len(mc.QUESTIONS))

    # دو گروه مستقل (کاربران تازه، چون تاریخچهٔ کاربر ۱ بالا مصرف شد)
    mc.reset_all()
    a = mc.start_question(-1, 71)
    b = mc.start_question(-2, 72)
    mc.answer_question(-1, str(a["answer"]))
    check("گروه دوم از پاسخ گروه اول متاثر نمی‌شود",
          mc.get_active_question(-2) is not None)

    # تاریخچه به تفکیک کاربر است، نه گروه
    mc.reset_all()
    first = []
    for _ in range(60):
        item = mc.start_question(-5, 81)
        first.append(item["question"])
        mc.clear_question(-5, item["token"])
    check("کاربر اول ۶۰ سوال بدون تکرار گرفت", len(set(first)) == 60)
    second = []
    for _ in range(60):
        item = mc.start_question(-5, 82)
        second.append(item["question"])
        mc.clear_question(-5, item["token"])
    check("کاربر دوم در همان گروه دور تازه گرفت", len(set(second)) == 60)
    check("تاریخچهٔ دو کاربر جداست",
          mc.seen_count(-5, 81) == 60 and mc.seen_count(-5, 82) == 60)

    # ارقام فارسی هم پذیرفته می‌شوند
    mc.reset_all()
    item = mc.start_question(-6, 83)
    persian = "۰۱۲۳۴"[item["answer"]]
    ok, _ = mc.answer_question(-6, persian, 83)
    check("پاسخ با رقم فارسی پذیرفته می‌شود", ok is True)

    # اتمام کامل بانک
    mc.reset_all()
    drained = 0
    # حلقهٔ کران‌دار: بازی دیگر پس از اتمام بانک ``None`` نمی‌دهد،
    # بلکه دور تازه می‌سازد. ``while True`` قدیمی بی‌پایان می‌شد.
    for _ in range(len(mc.QUESTIONS)):
        item = mc.start_question(-7, 84)
        if item is None:
            break
        drained += 1
        mc.clear_question(-7, item["token"])
    check("کل بانک برای یک کاربر مصرف شد",
          drained == len(mc.QUESTIONS), f"-> {drained}")
    check("کاربر پس از اتمام دور، exhausted است", mc.is_exhausted(-7, 84))
    check("پیام اتمام تعریف شده است", bool(mc.EXHAUSTED_MESSAGE))
    mc.reset_all()


# ---------------------------------------------------------------------------
# 📝 اسم فامیل
# ---------------------------------------------------------------------------
def test_name_family():
    print("\n### 📝 اسم فامیل")
    nf.reset_all()
    CHAT = -5007

    game = nf.start(CHAT)
    check("دور شروع شد و حرف دارد", bool(game["letter"]))
    check("مهلت ۹۰ ثانیه است", game["seconds"] == 90 == nf.ROUND_SECONDS)
    check("اجرای دوباره دور فعال را خراب نمی‌کند", nf.start(CHAT) is None)

    answers = "\n".join(["الف"] * 7)
    first = nf.submit(CHAT, 1, "کاربر۱", answers)
    check("پاسخ کاربر ثبت شد", first is not None)
    check("ثبت دوم همان کاربر رد می‌شود",
          nf.submit(CHAT, 1, "کاربر۱", answers) is None)
    check("متن با تعداد خط اشتباه رد می‌شود",
          nf.submit(CHAT, 2, "کاربر۲", "فقط یک خط") is None)

    ranking = nf.finish(CHAT, game["round_id"])
    check("نتایج پس از پایان برگردانده شد", len(ranking) == 1)
    check("finish دوباره چیزی برنمی‌گرداند",
          nf.finish(CHAT, game["round_id"]) == [])
    check("state بعد از پایان پاک شد", not nf.is_active(CHAT))

    # هر دور جداست
    second = nf.start(CHAT)
    check("دور جدید شناسهٔ تازه دارد", second["round_id"] != game["round_id"])
    check("دور جدید پاسخ‌های دور قبل را ندارد", second["answers"] == {})
    check("همان کاربر در دور جدید دوباره می‌تواند ثبت کند",
          nf.submit(CHAT, 1, "کاربر۱", answers) is not None)

    # لغو
    check("لغو دور موفق بود", nf.cancel_round(CHAT) is True)
    check("بعد از لغو، بازی فعال نیست", not nf.is_active(CHAT))
    check("finish بعد از لغو نتیجه نمی‌دهد", nf.finish(CHAT) == [])

    # جدا بودن گروه‌ها
    nf.reset_all()
    g1, g2 = nf.start(-11), nf.start(-12)
    nf.submit(-11, 1, "a", answers)
    check("دو گروه هم‌زمان مستقل‌اند",
          g1["round_id"] != g2["round_id"] and nf.is_active(-12))
    check("پاسخ گروه اول در گروه دوم نیست",
          len(nf.finish(-12, g2["round_id"])) == 0)
    nf.reset_all()


def test_name_family_timer_real():
    print("\n### 📝 اسم فامیل: تایمر واقعی و جدا بودن از بقیه")
    nf.reset_all()
    CHAT = -5008
    logger = Logger()

    async def scenario():
        game = nf.start(CHAT)
        got = []

        async def on_results(ranking):
            got.append(ranking)

        nf.schedule_round(CHAT, game["round_id"], on_results,
                          logger=logger, seconds=0.2)
        nf.submit(CHAT, 1, "علی", "\n".join(["الف"] * 7))
        # بازی دیگری در همین فاصله اجرا شود؛ نباید تایمر اسم فامیل را بکشد.
        eg.reset_all()
        eg.start(CHAT, 2)
        rd.new_riddle(CHAT, 3)
        await asyncio.sleep(0.5)
        return got

    got = asyncio.run(scenario())
    check("نتایج بعد از پایان تایمر اعلام شد", len(got) == 1, f"-> {got}")
    check("رتبه‌بندی شامل شرکت‌کننده است",
          got and len(got[0]) == 1 and got[0][0]["name"] == "علی")
    check("state بعد از تایمر پاک شد", not nf.is_active(CHAT))
    check("بازی‌های دیگر تایمر اسم فامیل را نکشتند",
          not logger.has("RESULTS FAILED"))
    nf.reset_all()
    eg.reset_all()
    rd.reset_all()


# ---------------------------------------------------------------------------
# 😂 بخند یا بباز
# ---------------------------------------------------------------------------
def test_laugh_or_lose():
    print("\n### 😂 بخند یا بباز")
    lol.reset_all()
    CHAT = -5009
    logger = Logger()

    session = lol.start(CHAT, logger)
    check("بازی شروع شد", session is not None)
    check("اجرای دوباره مسدود است", lol.start(CHAT, logger) is None)
    check("قبل از باز شدن، خنده پذیرفته نمی‌شود",
          lol.claim_win(CHAT, 1, User(1, "علی"), logger) is None)

    lol.open_round(CHAT, session["session_id"], logger)
    check("مرحلهٔ پذیرش باز شد", lol.is_accepting(CHAT))
    check("ایموجی خنده تشخیص داده می‌شود", lol.contains_laugh("😂"))
    check("متن بدون ایموجی خنده رد می‌شود", not lol.contains_laugh("سلام"))

    win = lol.claim_win(CHAT, 1, User(1, "علی"), logger)
    check("اولین نفر برنده شد", win and win["user_id"] == 1)
    check("جایزهٔ برنده ۳ سکه است", win["coins"] == lol.WINNER_COINS == 3)
    check("نفر دوم جایزه نمی‌گیرد",
          lol.claim_win(CHAT, 2, User(2, "حسین"), logger) is None)
    check("بعد از برنده، بازی بسته شد", not lol.is_active(CHAT))
    lol.reset_all()


def test_laugh_concurrent():
    print("\n### 😂 هجوم هم‌زمان چند کاربر")
    lol.reset_all()
    CHAT = -5010
    logger = Logger()

    async def scenario():
        session = lol.start(CHAT, logger)
        lol.open_round(CHAT, session["session_id"], logger)

        async def attempt(uid):
            await asyncio.sleep(0)
            return lol.claim_win(CHAT, uid, User(uid, f"u{uid}"), logger)

        return await asyncio.gather(*(attempt(i) for i in range(1, 26)))

    results = asyncio.run(scenario())
    winners = [r for r in results if r is not None]
    check("از ۲۵ کاربر هم‌زمان دقیقاً یک برنده", len(winners) == 1,
          f"-> {len(winners)}")
    check("بازی بعد از برنده بسته است", not lol.is_active(CHAT))
    lol.reset_all()


# ---------------------------------------------------------------------------
# 🏕 بقا
# ---------------------------------------------------------------------------
def test_survival():
    print("\n### 🏕 بقا")
    sv.reset_all()
    CHAT = -5011
    logger = Logger()

    sv.start(CHAT, logger)
    check("ثبت‌نام اول موفق",
          sv.join(CHAT, 1, User(1, "علی"), logger)[0] == "joined")
    check("ثبت‌نام تکراری رد می‌شود",
          sv.join(CHAT, 1, User(1, "علی"), logger)[0] == "duplicate")
    for uid in (2, 3, 4):
        sv.join(CHAT, uid, User(uid, f"u{uid}"), logger)
    check("ظرفیت رعایت می‌شود",
          sv.join(CHAT, 5, User(5, "u5"), logger)[0] == "full")

    sv.begin_rounds(CHAT, logger)
    q1 = sv.next_question(CHAT, logger)
    check("مرحله از ۱ شروع می‌شود", q1["level"] == 1)
    state = sv._STORE.get(CHAT)
    correct = state["question"]["answer"]

    check("پاسخ درست ثبت می‌شود",
          sv.answer(CHAT, 1, correct, logger)[0] == "correct")
    check("پاسخ دوم همان کاربر رد می‌شود",
          sv.answer(CHAT, 1, correct, logger)[0] == "already")
    check("پاسخ غلط باعث حذف می‌شود",
          sv.answer(CHAT, 2, "جواب کاملا غلط", logger)[0] == "wrong")
    check("غیر بازیکن پذیرفته نمی‌شود",
          sv.answer(CHAT, 99, correct, logger)[0] == "not_player")
    check("سکهٔ مرحله ثبت شد",
          sv._STORE.get(CHAT)["players"]["1"]["round_coins"] == sv.CORRECT_COINS)

    removed = sv.eliminate_silent(CHAT, logger)
    check("ساکت‌ها حذف شدند", {p["user_id"] for p in removed} == {3, 4},
          f"-> {[p['user_id'] for p in removed]}")
    check("فقط پاسخ‌دهندهٔ درست زنده ماند",
          [p["user_id"] for p in sv.alive_players(CHAT)] == [1])

    q2 = sv.next_question(CHAT, logger)
    check("مرحله بعد از اول شروع نمی‌شود", q2["level"] == 2)
    check("سوال مرحله دوم تکراری نیست", q2["text"] != q1["text"])

    champion = sv.finish(CHAT, logger=logger)
    check("برنده درست تعیین شد", champion and champion["user_id"] == 1)
    check("سکهٔ مراحل برنده حفظ شد", champion["round_coins"] == sv.CORRECT_COINS)
    check("بعد از پایان، بازی پاک شد", not sv.is_active(CHAT))
    check("finish دوباره نتیجه نمی‌دهد", sv.finish(CHAT, logger=logger) is None)
    sv.reset_all()


def test_survival_no_restart_questions():
    print("\n### 🏕 بقا: سوال‌ها از اول شروع نمی‌شوند")
    sv.reset_all()
    CHAT = -5012
    logger = Logger()
    firsts = []
    for _ in range(6):
        sv.start(CHAT, logger)
        sv.join(CHAT, 1, User(1, "علی"), logger)
        sv.join(CHAT, 2, User(2, "حسین"), logger)
        sv.begin_rounds(CHAT, logger)
        firsts.append(sv.next_question(CHAT, logger)["text"])
        sv.finish(CHAT, logger=logger)
    check("سوال اول بازی‌های پشت‌سرهم تکرار نمی‌شود",
          len(set(firsts)) == len(firsts), f"-> {len(set(firsts))}/{len(firsts)}")
    sv.reset_all()


# ---------------------------------------------------------------------------
# 🎁 جعبه شانسی
# ---------------------------------------------------------------------------
def test_lucky_box():
    print("\n### 🎁 جعبه شانسی")
    tmp = pathlib.Path(tempfile.mkdtemp()) / "box.json"
    original_file, original_quota = lb.STATE_FILE, lb._QUOTA
    lb.STATE_FILE = tmp
    lb._QUOTA = {}
    lb.reset_all()
    CHAT = -5013
    logger = Logger()

    boxes = lb.build_boxes()
    check("۹ جعبه تولید شد", len(boxes) == lb.BOX_COUNT == 9)
    check("۴ جعبه پوچ است",
          sum(1 for v in boxes.values() if v == 0) == lb.EMPTY_BOXES == 4)
    prizes = [v for v in boxes.values() if v > 0]
    check("۵ جعبه جایزه دارد", len(prizes) == lb.PRIZE_BOXES == 5)
    check("جایزه‌ها بین ۱ تا ۱۵ هستند",
          all(lb.MIN_PRIZE <= v <= lb.MAX_PRIZE for v in prizes))

    session, err = lb.start(CHAT, 1, logger)
    check("بازی اول شروع شد", session is not None and err is None)
    check("بازی هم‌زمان دوم مسدود است", lb.start(CHAT, 2, logger)[1] == "active")
    check("کاربر دیگر نمی‌تواند جعبه باز کند",
          lb.pick(CHAT, 2, "3", logger)[1] == "not_owner")
    check("عدد نامعتبر رد می‌شود", lb.pick(CHAT, 1, "20", logger)[1] == "bad_number")
    check("متن غیرعددی رد می‌شود", lb.pick(CHAT, 1, "سلام", logger)[1] == "bad_number")

    result, err = lb.pick(CHAT, 1, "۵", logger)
    check("ارقام فارسی پذیرفته می‌شوند", result is not None and result["box"] == 5)
    check("بعد از انتخاب، بازی بسته شد", not lb.is_active(CHAT))

    check("سهمیهٔ باقی‌مانده ۱ است", lb.remaining_plays(1) == 1)
    lb.start(CHAT, 1, logger)
    lb.pick(CHAT, 1, "1", logger)
    check("سهمیهٔ روزانه تمام شد", lb.remaining_plays(1) == 0)
    check("بازی سوم با خطای سهمیه رد می‌شود",
          lb.start(CHAT, 1, logger)[1] == "quota")
    check("زمان انتظار محاسبه می‌شود", lb.seconds_until_next(1) > 0)
    check("پیام انتظار نمایش داده می‌شود",
          "سهمیه امروز شما تمام شده" in lb.quota_message(1))
    check("کاربر دیگر سهمیهٔ مستقل دارد", lb.remaining_plays(2) == lb.DAILY_LIMIT)

    lb.STATE_FILE, lb._QUOTA = original_file, original_quota
    lb.reset_all()


# ---------------------------------------------------------------------------
# 🧛 خون‌آشام
# ---------------------------------------------------------------------------
def test_vampire():
    print("\n### 🧛 خون‌آشام")
    vp.reset_all()
    CHAT = -5014
    logger = Logger()

    vp.start(CHAT, logger)
    check("اجرای دوباره تا پایان بازی مسدود است", vp.start(CHAT, logger) is None)
    check("ثبت‌نام موفق", vp.join(CHAT, 1, User(1, "علی"), logger)[0] == "joined")
    check("ثبت‌نام تکراری رد می‌شود",
          vp.join(CHAT, 1, User(1, "علی"), logger)[0] == "duplicate")
    check("با نفرات کم، خون‌آشام انتخاب نمی‌شود",
          vp.choose_vampire(CHAT, logger) is None)

    for uid in (2, 3, 4):
        vp.join(CHAT, uid, User(uid, f"u{uid}"), logger)
    check("ظرفیت پنجم هم پذیرفته می‌شود",
          vp.join(CHAT, 5, User(5, "u5"), logger)[0] == "joined")
    check("نفر ششم رد می‌شود",
          vp.join(CHAT, 6, User(6, "u6"), logger)[0] == "full")

    chosen = vp.choose_vampire(CHAT, logger)
    check("خون‌آشام انتخاب شد", chosen is not None)
    check("پیش از ارسال پیوی مرحله assigning است",
          vp.phase(CHAT) == "assigning", f"-> {vp.phase(CHAT)}")
    check("پیش از ارسال پیوی حدس پذیرفته نمی‌شود",
          vp.guess(CHAT, 1, "1", logger)[0] == "closed")
    check("باز شدن مرحلهٔ حدس موفق بود",
          vp.open_guessing(CHAT, logger=logger) is True)
    check("مرحله به حدس تغییر کرد", vp.phase(CHAT) == "guessing")
    vampire_uid = chosen["player"]["user_id"]
    number = chosen["number"]

    check("خون‌آشام نمی‌تواند حدس بزند",
          vp.guess(CHAT, vampire_uid, str(number), logger)[0] == "is_vampire")
    check("غیر بازیکن نمی‌تواند حدس بزند",
          vp.guess(CHAT, 99, str(number), logger)[0] == "not_player")

    players = vp._STORE.get(CHAT)["players"]
    guesser = next(p["user_id"] for p in players if p["user_id"] != vampire_uid)
    own = next(i for i, p in enumerate(players, 1) if p["user_id"] == guesser)
    check("کسی نمی‌تواند خودش را انتخاب کند",
          vp.guess(CHAT, guesser, str(own), logger)[0] == "self_guess")
    check("انتخاب خود، نوبت را مصرف نمی‌کند",
          guesser not in vp._STORE.get(CHAT)["guessed"])

    wrong = next(i for i in range(1, len(players) + 1) if i not in {number, own})
    check("حدس غلط ثبت می‌شود",
          vp.guess(CHAT, guesser, str(wrong), logger)[0] == "wrong")
    check("حدس دوم همان کاربر رد می‌شود",
          vp.guess(CHAT, guesser, str(number), logger)[0] == "already")

    winner = next(p["user_id"] for p in players
                  if p["user_id"] not in {vampire_uid, guesser})
    state, info = vp.guess(CHAT, winner, str(number), logger)
    check("حدس درست جایزه می‌دهد",
          state == "correct" and info["coins"] == vp.WINNER_COINS == 7)
    check("بعد از حدس درست، بازی بسته شد", not vp.is_active(CHAT))
    vp.reset_all()


def test_vampire_random_and_reveal():
    print("\n### 🧛 تصادفی بودن و افشای پایانی")
    vp.reset_all()
    CHAT = -5015
    logger = Logger()

    picks = Counter()
    for _ in range(60):
        vp.start(CHAT, logger)
        for uid in range(1, 5):
            vp.join(CHAT, uid, User(uid, f"u{uid}"), logger)
        picks[vp.choose_vampire(CHAT, logger)["player"]["user_id"]] += 1
        vp.open_guessing(CHAT, logger=logger)
        vp.abandon(CHAT, logger=logger)
    check("انتخاب خون‌آشام تصادفی است و همه شانس دارند",
          len(picks) == 4, f"-> {dict(picks)}")

    vp.start(CHAT, logger)
    for uid in range(1, 5):
        vp.join(CHAT, uid, User(uid, f"u{uid}"), logger)
    chosen = vp.choose_vampire(CHAT, logger)
    vp.open_guessing(CHAT, logger=logger)
    revealed = vp.reveal(CHAT, logger=logger)
    check("در پایان هویت خون‌آشام برگردانده می‌شود",
          revealed and revealed["user_id"] == chosen["player"]["user_id"])
    check("متن افشا شامل نام است", chosen["player"]["name"] in vp.format_reveal(revealed))
    check("بعد از افشا بازی بسته است", not vp.is_active(CHAT))
    vp.reset_all()


def test_vampire_timer_real():
    print("\n### 🧛 تایمر ۵۰ ثانیه‌ای")
    check("مهلت حدس ۵۰ ثانیه است", vp.GUESS_SECONDS == 50)
    vp.reset_all()
    CHAT = -5016
    logger = Logger()

    async def scenario():
        session = vp.start(CHAT, logger)
        for uid in range(1, 5):
            vp.join(CHAT, uid, User(uid, f"u{uid}"), logger)
        revealed = []

        async def noop(*_):
            return None

        async def on_timeout(v):
            revealed.append(v)

        vp.schedule(CHAT, session["session_id"], {
            "on_abort": noop, "on_roles": noop, "on_timeout": on_timeout,
        }, logger=logger, join_seconds=0.05, guess_seconds=0.2)
        await asyncio.sleep(0.6)
        return revealed

    revealed = asyncio.run(scenario())
    check("بعد از پایان زمان، خون‌آشام افشا شد", len(revealed) == 1)
    check("state بعد از تایمر پاک شد", not vp.is_active(CHAT))
    vp.reset_all()


# ---------------------------------------------------------------------------
# تداخل بین بازی‌ها و سکه‌ها
# ---------------------------------------------------------------------------
def test_no_shared_state():
    print("\n### 🔒 نبود state مشترک بین بازی‌ها")
    containers = {
        "emoji._ACTIVE": id(eg._ACTIVE),
        "emoji.GAME": id(eg.GAME),
        "flag._ACTIVE": id(fg._ACTIVE),
        "flag._SEEN": id(fg._SEEN_HISTORY),
        "riddle.active": id(rd.active_riddles),
        "riddle._SEEN": id(rd._SEEN_BY_USER),
        "fill.active": id(fb.active_fill),
        "fill._SEEN": id(fb._SEEN_BY_USER),
        "namefamily._ACTIVE": id(nf._ACTIVE),
        "namefamily._TASKS": id(nf._ROUND_TASKS),
        "mc.active": id(mc._active_questions),
        "wc.active": id(wc._active),
    }
    check("هیچ دو بازی یک ظرف state مشترک ندارند",
          len(set(containers.values())) == len(containers))

    stores = {
        "laugh": id(lol._STORE), "survival": id(sv._STORE),
        "lucky_box": id(lb._STORE), "vampire": id(vp._STORE),
    }
    check("هر بازی Fox یک SessionStore مستقل دارد",
          len(set(stores.values())) == len(stores))

    # اجرای هم‌زمان همهٔ بازی‌ها در یک گروه
    CHAT = -5017
    eg.reset_all(); fg.reset_history(); rd.reset_all(); fb.reset_all()
    nf.reset_all(); lol.reset_all(); sv.reset_all(); vp.reset_all()
    mc._active_questions.clear(); wc._active.clear()

    nf.start(CHAT)
    eg.start(CHAT, 1)
    fg.start(CHAT, 1)
    rd.new_riddle(CHAT, 1)
    fb.new_fill(CHAT, 1)
    mc.start_question(CHAT)
    wc.start(CHAT)
    lol.start(CHAT)
    sv.start(CHAT)
    vp.start(CHAT)
    check("همهٔ بازی‌ها هم‌زمان در یک گروه زنده‌اند",
          all([nf.is_active(CHAT), eg.is_active(CHAT), fg.is_active(CHAT),
               rd.get_answer(CHAT, 1) is not None,
               fb.get_fill_answer(CHAT, 1) is not None,
               mc.get_active_question(CHAT) is not None,
               wc.get(CHAT) is not None, lol.is_active(CHAT),
               sv.is_active(CHAT), vp.is_active(CHAT)]))

    # بستن یکی نباید بقیه را ببندد
    nf.cancel_round(CHAT)
    lol.reset_all(CHAT)
    check("لغو اسم فامیل بقیه را نبست",
          eg.is_active(CHAT) and fg.is_active(CHAT) and vp.is_active(CHAT)
          and sv.is_active(CHAT) and mc.get_active_question(CHAT) is not None)
    check("بستن «بخند یا بباز» بقیه را نبست",
          sv.is_active(CHAT) and vp.is_active(CHAT))

    eg.reset_all(); fg.reset_history(); rd.reset_all(); fb.reset_all()
    nf.reset_all(); lol.reset_all(); sv.reset_all(); vp.reset_all()
    mc._active_questions.clear(); wc._active.clear()


def test_coin_isolation():
    print("\n### 🪙 ذخیرهٔ سکه‌ها")
    original = _economy_storage.DATA_FILE
    _economy_storage.use_file(pathlib.Path(tempfile.mkdtemp()) / "economy.json")

    try:
        # در اقتصاد جدید کیف پول «به تفکیک کاربر» است، نه گروه.
        coins.award(CHAT, 1, 4, name="علی")
        check("سکه ثبت شد", coins.get_balance(CHAT, 1)[coins.BRONZE] == 4)
        coins.award(CHAT, 1, 3, name="علی")
        check("سکه‌ها جمع می‌شوند", coins.get_balance(CHAT, 1)[coins.BRONZE] == 7)
        check("کاربر دیگر متاثر نمی‌شود",
              coins.get_balance(CHAT, 2)[coins.BRONZE] == 0)
        check("ارزش کل بازمحاسبه شد",
              coins.get_balance(CHAT, 1)["total_coin_value"] == 7)
        check("موجودی روی دیسک ماندگار است",
              _economy_storage.DATA_FILE.exists())

        # جایزه با مرجع تکراری دو بار پرداخت نمی‌شود.
        coins.award(CHAT, 3, 5, reference="dup:1")
        coins.award(CHAT, 3, 5, reference="dup:1")
        check("مرجع تکراری فقط یک بار پرداخت می‌شود",
              coins.get_balance(CHAT, 3)[coins.BRONZE] == 5)
    finally:
        _economy_storage.use_file(original)


def test_repeat_runs():
    print("\n### 🔁 چند اجرای پشت سر هم")
    CHAT = -5018
    eg.reset_all(); fg.reset_history(); rd.reset_all(); fb.reset_all()

    for i in range(25):
        p = eg.start(CHAT, 1)
        eg.answer(CHAT, 1, "u", p["answer"])
    check("۲۵ اجرای پیاپی حدس ایموجی بدون تکرار", eg.seen_count(CHAT, 1) == 25)
    check("بعد از هر بار، state پاک است", not eg.is_active(CHAT))

    for i in range(25):
        s = fg.start(CHAT, 1)
        fg.answer(CHAT, s["answer"], 1)
    check("۲۵ اجرای پیاپی حدس پرچم بدون تکرار", fg.seen_count(1) == 25)
    check("بعد از هر بار، state پرچم پاک است", not fg.is_active(CHAT))

    for i in range(30):
        rd.new_riddle(CHAT, 1)
        rd.check_answer(CHAT, 1, rd.get_answer(CHAT, 1))
    check("۳۰ اجرای پیاپی چیستان بدون تکرار", rd.seen_count(1) == 30)

    for i in range(30):
        fb.new_fill(CHAT, 1)
        fb.check_fill(CHAT, 1, fb.get_fill_answer(CHAT, 1))
    check("۳۰ اجرای پیاپی جای خالی بدون تکرار", fb.seen_count(1) == 30)
    check("امتیاز جای خالی دقیقاً ۳۰ بار ثبت شد", fb.get_score(1, CHAT) == 30)

    eg.reset_all(); fg.reset_history(); rd.reset_all(); fb.reset_all()


def test_concurrent_users():
    print("\n### 👥 چند کاربر هم‌زمان")
    CHAT = -5019
    rd.reset_all(); fb.reset_all()

    for uid in range(1, 21):
        rd.new_riddle(CHAT, uid)
        fb.new_fill(CHAT, uid)
    check("۲۰ کاربر هم‌زمان چیستان مستقل دارند",
          all(rd.get_answer(CHAT, uid) for uid in range(1, 21)))
    check("۲۰ کاربر هم‌زمان جای خالی مستقل دارند",
          all(fb.get_fill_answer(CHAT, uid) for uid in range(1, 21)))

    for uid in range(1, 21):
        check_ok = rd.check_answer(CHAT, uid, rd.get_answer(CHAT, uid))
        if not check_ok:
            break
    check("همهٔ ۲۰ کاربر پاسخ درست خود را گرفتند", check_ok)
    check("پاسخ‌ها با هم قاطی نشدند",
          all(rd.get_answer(CHAT, uid) is None for uid in range(1, 21)))
    rd.reset_all(); fb.reset_all()


def main():
    test_riddle()
    test_fill_blank()
    test_emoji_guess()
    test_flag_guess()
    test_word_correction()
    test_multiple_choice()
    test_name_family()
    test_name_family_timer_real()
    test_laugh_or_lose()
    test_laugh_concurrent()
    test_survival()
    test_survival_no_restart_questions()
    test_lucky_box()
    test_vampire()
    test_vampire_random_and_reveal()
    test_vampire_timer_real()
    test_no_shared_state()
    test_coin_isolation()
    test_repeat_runs()
    test_concurrent_users()

    print("\n" + "=" * 52)
    print(f"passed={PASSED} failed={FAILED}")
    print("=" * 52)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
