"""حدس ایموجی (۱۲۰ مرحله) و چهار گزینه‌ای (۱۶۰ سوال) — بازسازی کامل.

    python tests/test_emoji_and_quiz.py
"""
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ⚠️ پیش از import هر بازی اجرا می‌شود: خودِ economy هنگام import مسیر
# config/economy.json واقعی را می‌بندد و نوشتن‌های بعدی همان‌جا می‌نشیند.
import tempfile as _tempfile
import economy.storage as _storage
_storage.use_file(Path(_tempfile.mkdtemp()) / "economy.json")


import modules.emoji_guess as eg

import modules.multiple_choice as mc

PASSED = FAILED = 0
CHAT = -770001


def check(label, cond, detail=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  PASS  {label}")
    else:
        FAILED += 1
        print(f"  FAIL  {label} {detail}")


def emoji_count(value):
    """شمارش ایموجی‌ها با نادیده گرفتن VS16، ZWJ و کد رنگ پوست."""
    return sum(
        1 for ch in value
        if ch not in ("\ufe0f", "\u200d")
        and not "\U0001F3FB" <= ch <= "\U0001F3FF"
    )


def drain_emoji(user_id, chat_id=CHAT):
    """همهٔ مرحله‌های یک «دور» کامل.

    ⚠️ پس از مصرف شدن بانک، ``start`` دور تازه‌ای می‌سازد و دیگر هرگز
    ``None`` نمی‌دهد. پس حلقه با شمارهٔ مرحله متوقف می‌شود، نه با None،
    وگرنه بی‌نهایت ادامه پیدا می‌کرد.
    """
    out = []
    for _ in range(len(eg.PUZZLES)):
        puzzle = eg.start(chat_id, user_id)
        if puzzle is None:
            break
        out.append(puzzle)
        eg.finish(chat_id, puzzle["token"])
    return out


# ===========================================================================
# 😀 حدس ایموجی
# ===========================================================================
def test_emoji_bank():
    total = len(eg.PUZZLES)
    print(f"\n### 😀 بانک {total} مرحله‌ای")
    check("دست‌کم ۴۰۰ مرحله دارد", total >= 400, f"-> {total}")
    check("total_stages با بانک هم‌خوان است", eg.total_stages() == total)
    check("دست‌کم شش سطح سختی دارد", len(eg.TIERS) >= 6)
    check("مجموع سطوح برابر کل بانک است",
          sum(len(t) for t in eg.TIERS) == total,
          f"-> {[len(t) for t in eg.TIERS]}")
    check("نام هر سطح تعریف شده",
          len(eg.TIER_NAMES) == len(eg.TIERS))


def test_emoji_structure():
    print("\n### 😀 ساختار و تعداد ایموجی")
    check("هر رکورد سه بخش دارد", all(len(i) == 3 for i in eg.PUZZLES))
    counts = [emoji_count(i[0]) for i in eg.PUZZLES]
    check("هر معما بین ۲ تا ۴ ایموجی دارد",
          all(2 <= n <= 4 for n in counts),
          f"-> {[(i[0], n) for i, n in zip(eg.PUZZLES, counts) if not 2 <= n <= 4][:3]}")
    check("هیچ معمای تک‌ایموجی وجود ندارد", min(counts) >= 2)
    check("هیچ معمای بیش از ۴ ایموجی نیست", max(counts) <= 4)
    check("توزیع ایموجی متنوع است (نه همه ۲تایی)",
          len(set(counts)) > 1, f"-> {Counter(counts)}")


def test_emoji_answers_are_persian():
    print("\n### 😀 پاسخ‌های فارسی و دقیق")
    pattern = re.compile(r"^[آ-یءئؤةا\s\u200c]+$")
    bad = [i[1] for i in eg.PUZZLES if not pattern.match(i[1])]
    check("همهٔ پاسخ‌ها فارسی‌اند", not bad, f"-> {bad[:3]}")
    check("هیچ پاسخ خالی نیست", all(i[1].strip() for i in eg.PUZZLES))

    answers = [i[1] for i in eg.PUZZLES]
    check("هیچ پاسخ تکراری نیست", len(answers) == len(set(answers)),
          f"-> {[a for a, c in Counter(answers).items() if c > 1]}")
    emojis = [i[0] for i in eg.PUZZLES]
    check("هیچ ترکیب ایموجی تکراری نیست", len(emojis) == len(set(emojis)))

    redundant = [
        (i[1], a) for i in eg.PUZZLES for a in i[2]
        if eg._norm(a) == eg._norm(i[1])
    ]
    check("جایگزین تکراری با پاسخ اصلی نیست", not redundant, f"-> {redundant[:3]}")


def test_emoji_exhausted_message():
    print("\n### 😀 پیام پایان مراحل")
    check("متن دقیقاً مطابق خواسته است",
          eg.EXHAUSTED_MESSAGE == (
              "✅ تمام مراحل حدس ایموجی را انجام داده‌اید. "
              "به‌زودی مراحل جدید اضافه می‌شود."
          ), f"-> {eg.EXHAUSTED_MESSAGE}")


def test_emoji_no_repeat_120():
    print("\n### 😀 هیچ تکراری تا پایان بانک")
    eg.reset_all()
    drawn = drain_emoji(4001)
    answers = [p["answer"] for p in drawn]
    total = len(eg.PUZZLES)
    check("کل بانک داده شد", len(drawn) == total, f"-> {len(drawn)}")
    check("هیچ مرحله‌ای تکرار نشد", len(set(answers)) == total,
          f"-> {len(set(answers))}")
    check("کل بانک پوشش داده شد",
          set(answers) == {i[1] for i in eg.PUZZLES})
    check("کاربر پس از کل بانک exhausted است", eg.is_exhausted(CHAT, 4001))
    check("باقی‌مانده صفر است", eg.remaining_count(CHAT, 4001) == 0)
    check("current_tier پس از اتمام None است", eg.current_tier(CHAT, 4001) is None)
    # پس از اتمام، بازی قفل نمی‌شود: دور تازه‌ای ساخته می‌شود.
    again = eg.start(CHAT, 4001)
    check("دور تازه ساخته می‌شود", again is not None)
    check("دور تازه از مرحلهٔ ۱ شروع می‌شود",
          again and again["stage"] == 1, f"-> {again}")
    eg.finish(CHAT)
    eg.reset_all()


def test_emoji_difficulty_progression():
    print("\n### 😀 سختی تدریجی: آسان → متوسط → سخت")
    eg.reset_all()
    drawn = drain_emoji(4002)
    tiers = [p["tier"] for p in drawn]

    order = []
    for tier in tiers:
        if not order or order[-1] != tier:
            order.append(tier)
    check("ترتیب سطوح از آسان به سخت است",
          order == list(eg.TIER_NAMES), f"-> {order}")
    check("تعداد هر سطح با بانک هم‌خوان است",
          Counter(tiers) == {name: len(tier)
                             for name, tier in zip(eg.TIER_NAMES, eg.TIERS)},
          f"-> {dict(Counter(tiers))}")
    check("۴۰ مرحلهٔ اول همه آسان‌اند", set(tiers[:40]) == {"آسان"})
    check("مرحله‌های آخر از سطح پیشرفتهٔ سخت‌اند",
          set(tiers[-len(eg.TIERS[-1]):]) == {eg.TIER_NAMES[-1]})

    stages = [p["stage"] for p in drawn]
    check("شمارهٔ مرحله صعودی است",
          stages == list(range(1, len(eg.PUZZLES) + 1)))
    eg.reset_all()


def test_emoji_random_order():
    print("\n### 😀 ترتیب تصادفی و مستقل برای هر کاربر")
    eg.reset_all()
    a = [p["answer"] for p in drain_emoji(4003)]
    eg.reset_all()
    b = [p["answer"] for p in drain_emoji(4004)]
    check("ترتیب دو کاربر یکسان نیست", a != b)
    total = len(eg.PUZZLES)
    check("هر دو کامل و بدون تکرارند",
          len(set(a)) == total and len(set(b)) == total)

    eg.reset_all()
    firsts = set()
    for uid in range(4100, 4140):
        puzzle = eg.start(CHAT, uid)
        firsts.add(puzzle["answer"])
        eg.finish(CHAT, puzzle["token"])
    check("مرحلهٔ اول کاربران مختلف متنوع است",
          len(firsts) > 5, f"-> {len(firsts)}")
    check("مرحلهٔ اول همیشه از سطح آسان است",
          firsts <= {i[1] for i in eg.EASY})
    eg.reset_all()


def test_emoji_per_user_history():
    print("\n### 😀 تاریخچه به تفکیک کاربر")
    eg.reset_all()
    for _ in range(30):
        puzzle = eg.start(CHAT, 4005)
        eg.finish(CHAT, puzzle["token"])
    check("کاربر اول ۳۰ مرحله دیده", eg.seen_count(CHAT, 4005) == 30)
    check("کاربر دوم هنوز تاریخچه ندارد", eg.seen_count(CHAT, 4006) == 0)

    fresh = [p["answer"] for p in drain_emoji(4006)]
    check("کاربر دوم دور کامل و مستقل گرفت",
          len(set(fresh)) == len(eg.PUZZLES))
    check("تاریخچهٔ کاربر اول دست‌نخورده ماند", eg.seen_count(CHAT, 4005) == 30)

    eg.reset_user(CHAT, 4005)
    check("ریست یک کاربر فقط همان را پاک می‌کند",
          eg.seen_count(CHAT, 4005) == 0
          and eg.seen_count(CHAT, 4006) == len(eg.PUZZLES))
    eg.reset_all()


def test_emoji_answer_validation():
    print("\n### 😀 اعتبارسنجی پاسخ")
    eg.reset_all()
    puzzle = eg.start(CHAT, 4007)

    check("پاسخ غلط رد می‌شود",
          eg.answer(CHAT, 4007, "U", "یک جواب کاملا اشتباه") is None)
    check("بازی پس از پاسخ غلط فعال می‌ماند", eg.is_active(CHAT))
    check("متن خالی رد می‌شود", eg.answer(CHAT, 4007, "U", "") is None)

    other = next(i[1] for i in eg.PUZZLES if i[1] != puzzle["answer"])
    check("پاسخ مرحلهٔ دیگر پذیرفته نمی‌شود",
          eg.answer(CHAT, 4007, "U", other) is None)

    check("پاسخ درست پذیرفته می‌شود",
          eg.answer(CHAT, 4007, "U", puzzle["answer"]) == puzzle["answer"])
    check("بازی پس از پاسخ درست بسته شد", not eg.is_active(CHAT))
    check("پاسخ دوباره امتیاز نمی‌دهد",
          eg.answer(CHAT, 4007, "U", puzzle["answer"]) is None)

    # شکل‌های املایی همان جواب
    eg.reset_all()
    target = next(i for i in eg.PUZZLES if i[1] == "لاک پشت")
    eg._ACTIVE[eg._session_key(CHAT, 4008)] = {
        "emoji": target[0], "answer": target[1],
        "aliases": target[2], "token": 0, "user_id": 4008}
    check("نگارش بدون فاصله پذیرفته می‌شود",
          eg.answer(CHAT, 4008, "U", "لاکپشت") == "لاک پشت")

    eg.reset_all()
    target = next(i for i in eg.PUZZLES if i[1] == "کامپیوتر")
    eg._ACTIVE[eg._session_key(CHAT, 4009)] = {
        "emoji": target[0], "answer": target[1],
        "aliases": target[2], "token": 0, "user_id": 4009}
    check("نام مستعار تعریف‌شده پذیرفته می‌شود",
          eg.answer(CHAT, 4009, "U", "رایانه") == "کامپیوتر")
    eg.reset_all()


def test_emoji_timer_and_token():
    print("\n### 😀 تایمر و گارد توکن")
    check("مهلت پاسخ ۴۰ ثانیه است", eg.ANSWER_SECONDS == 40)
    eg.reset_all()

    first = eg.start(CHAT, 4010)
    stale = first["token"]
    check("همان کاربر دور دومی نمی‌گیرد",
          eg.start(CHAT, 4010) is None)
    check("کاربر دیگر هم‌زمان می‌تواند شروع کند",
          eg.start(CHAT, 4011) is not None)
    check("دور اول دست‌نخورده ماند",
          eg.active_state(CHAT, 4010)["answer"] == first["answer"])

    check("finish با توکن درست کار می‌کند",
          eg.finish(CHAT, stale, 4010) == first["answer"])
    second = eg.start(CHAT, 4010)
    check("تایمر دور قبلی دور جدید را نمی‌بندد",
          eg.finish(CHAT, stale) is None)
    check("دور جدید هنوز فعال است", eg.is_active(CHAT))
    check("finish با توکن درست دور جدید را می‌بندد",
          eg.finish(CHAT, second["token"]) == second["answer"])
    eg.reset_all()


def test_emoji_coin_award():
    print("\n### 😀 افزودن سکه فقط یک بار")
    eg.reset_all()

    calls = []
    original = eg._economy_award_game

    def spy(chat_id, user_id, game, **kwargs):
        calls.append((user_id, game, kwargs.get("reference")))
        return {"bronze": 0, "total_coin_value": 0}

    eg._economy_award_game = spy
    try:
        puzzle = eg.start(CHAT, 4012)
        eg.answer(CHAT, 4012, "U", "غلط")
        check("پاسخ غلط امتیاز نمی‌دهد", calls == [])
        eg.answer(CHAT, 4012, "U", puzzle["answer"])
        check("پاسخ درست یک بار امتیاز می‌دهد", len(calls) == 1, f"-> {calls}")
        eg.answer(CHAT, 4012, "U", puzzle["answer"])
        check("پاسخ تکراری امتیاز دوباره نمی‌دهد", len(calls) == 1)
        check("جایزه از جدول «حدس ایموجی» می‌آید",
              calls and calls[0][1] == "emoji", f"-> {calls}")
    finally:
        eg._economy_award_game = original
    eg.reset_all()


def test_emoji_anti_farm():
    print("\n### 😀 جلوگیری از سوءاستفاده")
    eg.reset_all()
    drain_emoji(4013)
    check("کاربر تمام‌شده exhausted است", eg.is_exhausted(CHAT, 4013))

    puzzle = eg.start(CHAT, 4014)
    check("کاربر تمام‌شده از دور دیگری امتیاز نمی‌گیرد",
          eg.answer(CHAT, 4013, "U", puzzle["answer"]) is None)
    check("بازی برای صاحب دور فعال می‌ماند", eg.is_active(CHAT, 4014))
    check("صاحب دور پاسخ خود را می‌گیرد",
          eg.answer(CHAT, 4014, "U", puzzle["answer"]) == puzzle["answer"])

    # سشن‌ها per-user هستند: پاسخ دادن به معمای دیگران ممکن نیست.
    eg.reset_all()
    solved = []
    for i in range(10):
        item = eg.start(CHAT, 4200 + i)
        solved.append(eg.answer(CHAT, 4999, "farmer", item["answer"]))
    check("هیچ پاسخی از دور دیگران پذیرفته نشد", not any(solved),
          f"-> {solved}")
    check("فارمر هیچ تاریخچه‌ای نگرفت",
          eg.seen_count(CHAT, 4999) == 0,
          f"-> {eg.seen_count(CHAT, 4999)}")

    # هر کس معمای خودش را جواب دهد، در تاریخچهٔ خودش ثبت می‌شود.
    own = [eg.answer(CHAT, 4200 + i, "u",
                     eg.active_state(CHAT, 4200 + i)["answer"])
           for i in range(10)]
    check("هر کس معمای خودش را گرفت", all(own), f"-> {own}")
    check("هر کدام یک مرحله در تاریخچه دارند",
          all(eg.seen_count(CHAT, 4200 + i) == 1 for i in range(10)))
    eg.reset_all()


# ===========================================================================
# ❓ چهار گزینه‌ای
# ===========================================================================
def test_quiz_bank():
    print("\n### ❓ بانک سوال چهار گزینه‌ای")
    total = len(mc.QUESTIONS)
    check("بانک حداقل ۱۶۰ سوال دارد", total >= 160, f"-> {total}")
    check("total_questions هم‌خوان است", mc.total_questions() == total)

    texts = [q["question"] for q in mc.QUESTIONS]
    check("هیچ سوال تکراری نیست", len(texts) == len(set(texts)),
          f"-> {[t for t, c in Counter(texts).items() if c > 1][:3]}")

    categories = Counter(q["category"] for q in mc.QUESTIONS)
    check("دست‌کم ۱۰ دستهٔ موضوعی دارد", len(categories) >= 10,
          f"-> {len(categories)}")
    # ⚠️ فهرست موضوع‌ها روی مقدار ثابت قفل نمی‌شود.
    #
    # نسخهٔ قبلی «قرآن/ایران/عمومی» را الزامی می‌کرد؛ آن دسته‌ها با
    # بازطراحی بانک حذف شدند و تست بی‌دلیل می‌شکست. حالا از منبع حقیقت
    # (خود بانک) خوانده می‌شود تا با هر تغییر بعدی هم درست بماند.
    # پوشش کاملِ موضوع‌های خواسته‌شده در tests/test_quiz_bank.py است.
    check("هر سوال دقیقاً یک موضوع ناتهی دارد",
          all(str(q.get("category", "")).strip() for q in mc.QUESTIONS))
    check("هیچ موضوعی بر بانک غالب نیست",
          max(categories.values()) <= total * 0.2,
          f"-> {categories.most_common(2)}")


def test_quiz_options_valid():
    print("\n### ❓ اعتبار گزینه‌ها")
    bank = mc.QUESTIONS
    check("هر سوال دقیقاً ۴ گزینه دارد",
          all(len(q["options"]) == 4 for q in bank),
          f"-> {[q['question'] for q in bank if len(q['options']) != 4][:2]}")
    check("گزینه‌های هر سوال یکتا هستند",
          all(len(set(q["options"])) == 4 for q in bank),
          f"-> {[q['question'] for q in bank if len(set(q['options'])) != 4][:2]}")
    check("شمارهٔ پاسخ بین ۱ تا ۴ است",
          all(1 <= q["answer"] <= 4 for q in bank),
          f"-> {[q['question'] for q in bank if not 1 <= q['answer'] <= 4][:2]}")
    check("هیچ گزینهٔ خالی نیست",
          all(str(o).strip() for q in bank for o in q["options"]))
    check("هر سوال دسته‌بندی دارد",
          all(str(q.get("category", "")).strip() for q in bank))

    # گزینهٔ درست نباید همیشه در یک جایگاه باشد
    positions = Counter(q["answer"] for q in bank)
    check("جایگاه پاسخ درست در گزینه‌ها پخش شده است",
          len(positions) == 4, f"-> {dict(positions)}")


def test_quiz_no_repeat_per_user():
    print("\n### ❓ بدون تکرار تا پایان بانک برای هر کاربر")
    mc.reset_all()
    total = len(mc.QUESTIONS)
    drawn = []
    # ⚠️ حلقهٔ کران‌دار، نه ``while True``.
    #
    # بازی دیگر پس از دیدن همهٔ سوال‌ها برای همیشه بسته نمی‌شود؛
    # ``start_question`` دور تازه می‌سازد و هرگز ``None`` نمی‌دهد.
    # حلقهٔ بی‌کران قدیمی با این رفتار برای همیشه می‌چرخید.
    for _ in range(total):
        item = mc.start_question(CHAT, 5001)
        drawn.append(item["question"])
        mc.clear_question(CHAT, item["token"])
    check(f"همهٔ {total} سوال داده شد", len(drawn) == total, f"-> {len(drawn)}")
    check("هیچ سوالی تکرار نشد", len(set(drawn)) == total,
          f"-> {len(set(drawn))}")
    check("کل بانک پوشش داده شد",
          set(drawn) == {q["question"] for q in mc.QUESTIONS})
    check("کاربر پس از اتمام دور، exhausted است",
          mc.is_exhausted(CHAT, 5001))
    check("پس از اتمام، دور تازه شروع می‌شود و بازی قفل نمی‌شود",
          mc.start_question(CHAT, 5001) is not None)
    check("پیام اتمام تعریف شده است", bool(mc.EXHAUSTED_MESSAGE))
    mc.reset_all()


def test_quiz_history_per_user():
    print("\n### ❓ تاریخچه به تفکیک کاربر، نه گروه")
    mc.reset_all()
    first = []
    for _ in range(60):
        item = mc.start_question(CHAT, 5002)
        first.append(item["question"])
        mc.clear_question(CHAT, item["token"])
    check("کاربر اول ۶۰ سوال بدون تکرار گرفت", len(set(first)) == 60)
    # تاریخچه حالا per-group است، پس امضا ``(chat_id, user_id)`` شده.
    check("کاربر دوم تاریخچه ندارد", mc.seen_count(CHAT, 5003) == 0)

    second = []
    for _ in range(60):
        item = mc.start_question(CHAT, 5003)
        second.append(item["question"])
        mc.clear_question(CHAT, item["token"])
    check("کاربر دوم در همان گروه دور تازه گرفت", len(set(second)) == 60)
    check("تاریخچهٔ دو کاربر جداست",
          mc.seen_count(CHAT, 5002) == 60 and mc.seen_count(CHAT, 5003) == 60)

    mc.reset_user(CHAT, 5002)
    check("ریست یک کاربر فقط همان را پاک می‌کند",
          mc.seen_count(CHAT, 5002) == 0 and mc.seen_count(CHAT, 5003) == 60)
    mc.reset_all()


def test_quiz_order_differs():
    print("\n### ❓ ترتیب نمایش برای هر کاربر متفاوت است")
    mc.reset_all()
    a, b = [], []
    for _ in range(40):
        item = mc.start_question(CHAT, 5004)
        a.append(item["question"])
        mc.clear_question(CHAT, item["token"])
    for _ in range(40):
        item = mc.start_question(CHAT, 5005)
        b.append(item["question"])
        mc.clear_question(CHAT, item["token"])
    check("ترتیب دو کاربر یکسان نیست", a != b)
    check("هر دو بدون تکرارند", len(set(a)) == 40 and len(set(b)) == 40)
    mc.reset_all()


def test_quiz_answer_validation():
    print("\n### ❓ بررسی پاسخ درست و غلط")
    mc.reset_all()
    item = mc.start_question(CHAT, 5006)
    correct = item["answer"]
    wrong = 1 if correct != 1 else 2

    check("متن غیرعددی سوال را نمی‌بندد",
          mc.answer_question(CHAT, "سلام", 5006) is None)
    check("سوال هنوز فعال است", mc.get_active_question(CHAT) is not None)
    check("عدد خارج از بازه رد می‌شود",
          mc.answer_question(CHAT, "5", 5006) is None)
    check("عدد صفر رد می‌شود", mc.answer_question(CHAT, "0", 5006) is None)

    result = mc.answer_question(CHAT, str(wrong), 5006)
    check("پاسخ اشتباه امتیاز نمی‌گیرد", result == (False, correct),
          f"-> {result}")
    check("بعد از پاسخ، سوال بسته شد", mc.get_active_question(CHAT) is None)
    check("پاسخ دوباره اثری ندارد",
          mc.answer_question(CHAT, str(correct), 5006) is None)

    item = mc.start_question(CHAT, 5006)
    ok, option = mc.answer_question(CHAT, str(item["answer"]), 5006)
    check("گزینهٔ درست تشخیص داده می‌شود", ok is True)
    check("شمارهٔ گزینهٔ درست برگردانده می‌شود", option == item["answer"])

    # ارقام فارسی
    item = mc.start_question(CHAT, 5006)
    persian = "۰۱۲۳۴"[item["answer"]]
    ok, _ = mc.answer_question(CHAT, persian, 5006)
    check("رقم فارسی پذیرفته می‌شود", ok is True)
    mc.reset_all()


def test_quiz_every_question_answerable():
    """هر سوال بانک باید با شمارهٔ پاسخش درست ارزیابی شود."""
    print("\n### ❓ صحت پاسخ همهٔ سوال‌های بانک")
    mc.reset_all()
    failures = []
    for index, question in enumerate(mc.QUESTIONS):
        mc._active_questions[CHAT] = {
            "token": -1,
            "answer": question["answer"],
            "options": list(question["options"]),
            "question": question["question"],
            "category": question["category"],
            "user_id": 5007,
        }
        ok, option = mc.answer_question(CHAT, str(question["answer"]))
        if not ok or option != question["answer"]:
            failures.append(question["question"])
    check("همهٔ سوال‌ها با گزینهٔ درست پاسخ صحیح می‌دهند",
          not failures, f"-> {failures[:3]}")

    wrong_failures = []
    for question in mc.QUESTIONS:
        wrong = 1 if question["answer"] != 1 else 2
        mc._active_questions[CHAT] = {
            "token": -1,
            "answer": question["answer"],
            "options": list(question["options"]),
            "question": question["question"],
            "category": question["category"],
            "user_id": 5007,
        }
        ok, _ = mc.answer_question(CHAT, str(wrong))
        if ok:
            wrong_failures.append(question["question"])
    check("هیچ گزینهٔ غلطی درست شمرده نمی‌شود", not wrong_failures,
          f"-> {wrong_failures[:3]}")
    mc.reset_all()


def test_quiz_timer_token():
    print("\n### ❓ تایمر و گارد توکن")
    check("مهلت پاسخ ۳۰ ثانیه است", mc.ANSWER_SECONDS == 30)
    mc.reset_all()

    item = mc.start_question(CHAT, 5008)
    stale = item["token"]
    check("clear با توکن درست کار می‌کند",
          mc.clear_question(CHAT, stale) is True)

    fresh = mc.start_question(CHAT, 5008)
    check("تایمر قدیمی سوال جدید را نمی‌بندد",
          mc.clear_question(CHAT, stale) is False)
    check("سوال جدید فعال ماند", mc.get_active_question(CHAT) is not None)
    check("clear با توکن درست سوال جدید را می‌بندد",
          mc.clear_question(CHAT, fresh["token"]) is True)
    mc.reset_all()


def test_state_isolation():
    print("\n### 🔒 استقلال دو بازی از هم و از بقیه")
    import modules.fill_blank as fb
    import modules.flag_guess as fg
    import modules.riddles as rd

    ids = {
        "emoji_active": id(eg._ACTIVE),
        "emoji_active": id(eg._ACTIVE),
        "quiz_active": id(mc._active_questions),
        "quiz_seen": id(mc._SEEN_BY_USER),
        "flag_seen": id(fg._SEEN_HISTORY),
        "riddle_seen": id(rd._SEEN_BY_USER),
        "fill_seen": id(fb._SEEN_BY_USER),
    }
    check("هیچ ظرف حالتی مشترک نیست", len(set(ids.values())) == len(ids))

    eg.reset_all()
    mc.reset_all()
    puzzle = eg.start(CHAT, 6001)
    quiz = mc.start_question(CHAT, 6001)
    check("هر دو بازی هم‌زمان در یک گروه فعال‌اند",
          eg.is_active(CHAT) and mc.get_active_question(CHAT) is not None)

    mc.answer_question(CHAT, str(quiz["answer"]), 6001)
    check("بستن چهار گزینه‌ای، حدس ایموجی را نبست", eg.is_active(CHAT))
    eg.answer(CHAT, 6001, "U", puzzle["answer"])
    check("تاریخچهٔ دو بازی مستقل است",
          eg.seen_count(CHAT, 6001) == 1 and mc.seen_count(CHAT, 6001) == 1)

    eg.reset_all()
    check("ریست ایموجی تاریخچهٔ چهار گزینه‌ای را پاک نکرد",
          mc.seen_count(CHAT, 6001) == 1)
    mc.reset_all()


def test_repeated_runs():
    print("\n### 🔁 اجرای پیاپی و چند کاربر هم‌زمان")
    eg.reset_all()
    mc.reset_all()

    for i in range(50):
        puzzle = eg.start(CHAT, 7001)
        eg.answer(CHAT, 7001, "U", puzzle["answer"])
    check("۵۰ اجرای پیاپی ایموجی بدون تکرار", eg.seen_count(CHAT, 7001) == 50)
    check("state بعد از هر بار پاک است", not eg.is_active(CHAT))

    for i in range(50):
        item = mc.start_question(CHAT, 7001)
        mc.answer_question(CHAT, str(item["answer"]), 7001)
    check("۵۰ اجرای پیاپی چهار گزینه‌ای بدون تکرار",
          mc.seen_count(CHAT, 7001) == 50)
    check("سوال بعد از هر بار بسته است",
          mc.get_active_question(CHAT) is None)

    # چند کاربر در گروه‌های جدا
    eg.reset_all()
    for uid in range(7100, 7120):
        puzzle = eg.start(-uid, uid)
        eg.answer(-uid, uid, "U", puzzle["answer"])
    check("۲۰ کاربر هم‌زمان بدون تداخل بازی کردند",
          all(eg.seen_count(-uid, uid) == 1 for uid in range(7100, 7120)))
    eg.reset_all()
    mc.reset_all()


def main():
    test_emoji_bank()
    test_emoji_structure()
    test_emoji_answers_are_persian()
    test_emoji_exhausted_message()
    test_emoji_no_repeat_120()
    test_emoji_difficulty_progression()
    test_emoji_random_order()
    test_emoji_per_user_history()
    test_emoji_answer_validation()
    test_emoji_timer_and_token()
    test_emoji_coin_award()
    test_emoji_anti_farm()

    test_quiz_bank()
    test_quiz_options_valid()
    test_quiz_no_repeat_per_user()
    test_quiz_history_per_user()
    test_quiz_order_differs()
    test_quiz_answer_validation()
    test_quiz_every_question_answerable()
    test_quiz_timer_token()

    test_state_isolation()
    test_repeated_runs()

    print("\n" + "=" * 52)
    print(f"passed={PASSED} failed={FAILED}")
    print("=" * 52)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
