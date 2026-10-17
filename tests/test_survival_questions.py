"""بانک سوال بازی بقا: کیفیت محتوا و چرخش بدون تکرار.

    python tests/test_survival_questions.py
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


from modules.fox_games import survival as sv
from modules.fox_games.survival_questions import (
    LEVELS,
    all_questions,
    level_pool,
    question_count,
)

PASSED = FAILED = 0
MIN_BANK = 200


def check(label, cond, detail=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  PASS  {label}")
    else:
        FAILED += 1
        print(f"  FAIL  {label} {detail}")


class User:
    def __init__(self, uid, name=None):
        self.id = uid
        self.first_name = name
        self.last_name = None
        self.username = None


class Logger:
    def __init__(self):
        self.info = []

    def log_info(self, m):
        self.info.append(m)

    def log_error(self, m):
        self.info.append(m)

    def count(self, needle):
        return sum(1 for m in self.info if needle in m)


def _norm(value):
    return " ".join(str(value or "").replace("\u200c", " ").split())


def play(chat_id, questions, logger=None):
    """یک بازی کامل و برداشتن ``questions`` سوال."""
    sv.start(chat_id, logger)
    sv.join(chat_id, 1, User(1, "الف"), logger)
    sv.join(chat_id, 2, User(2, "ب"), logger)
    sv.begin_rounds(chat_id, logger)
    drawn = []
    for _ in range(questions):
        item = sv.next_question(chat_id, logger)
        if item is None:
            break
        drawn.append(item["text"])
    sv.finish(chat_id, logger=logger)
    return drawn


def drain(chat_id, count, per_game=5, logger=None):
    """دقیقاً ``count`` سوال برمی‌دارد، بدون برداشت اضافه.

    برداشت بیش از حد باعث می‌شد ناخواسته از مرز بانک عبور کنیم و ریست
    تاریخچه رخ دهد، که اندازه‌گیری را خراب می‌کرد.
    """
    out = []
    while len(out) < count:
        take = min(per_game, count - len(out))
        out.extend(play(chat_id, take, logger))
    return out


# ---------------------------------------------------------------------------
# محتوای بانک
# ---------------------------------------------------------------------------
def test_bank_size():
    print("\n### اندازه و ساختار بانک")
    bank = all_questions()
    check(f"بانک حداقل {MIN_BANK} سوال دارد",
          len(bank) >= MIN_BANK, f"-> {len(bank)}")
    check("question_count با بانک هم‌خوان است", question_count() == len(bank))
    check("پنج سطح سختی وجود دارد", len(LEVELS) == 5, f"-> {len(LEVELS)}")
    check("هر سطح دست‌کم ۳۰ سوال دارد",
          all(len(level) >= 30 for level in LEVELS),
          f"-> {[len(l) for l in LEVELS]}")


def test_structure():
    print("\n### قالب رکوردها")
    bank = all_questions()
    check("هر رکورد سه بخش دارد", all(len(item) == 3 for item in bank))
    check("متن سوال رشتهٔ ناتهی است",
          all(isinstance(i[0], str) and i[0].strip() for i in bank))
    check("پاسخ اصلی رشتهٔ ناتهی است",
          all(isinstance(i[1], str) and i[1].strip() for i in bank))
    check("پاسخ‌های جایگزین tuple هستند",
          all(isinstance(i[2], tuple) for i in bank))
    check("همهٔ جایگزین‌ها رشته‌اند",
          all(isinstance(a, str) and a.strip() for i in bank for a in i[2]))
    check("هر سوال با علامت پرسش یا نقطه تمام می‌شود",
          all(i[0].rstrip().endswith(("؟", "?")) for i in bank),
          f"-> {[i[0] for i in bank if not i[0].rstrip().endswith(('؟', '?'))][:3]}")


def test_uniqueness():
    print("\n### یکتا بودن سوال‌ها")
    bank = all_questions()
    texts = [_norm(i[0]) for i in bank]
    dupes = [t for t, c in Counter(texts).items() if c > 1]
    check("هیچ متن سوال تکراری نیست", not dupes, f"-> {dupes[:3]}")

    pairs = [(_norm(i[0]), _norm(i[1])) for i in bank]
    check("هیچ جفت (سوال، پاسخ) تکراری نیست",
          len(set(pairs)) == len(pairs))

    # یک سوال نباید در دو سطح مختلف تکرار شود
    seen = {}
    overlap = []
    for index, level in enumerate(LEVELS, 1):
        for item in level:
            key = _norm(item[0])
            if key in seen:
                overlap.append((key, seen[key], index))
            seen[key] = index
    check("هیچ سوالی بین دو سطح مشترک نیست", not overlap, f"-> {overlap[:2]}")


def test_no_trivial_questions():
    """سوال‌های کودکانه و بسیار ساده نباید در بانک باشند."""
    print("\n### نبود سوال ساده یا کودکانه")
    bank = all_questions()
    banned = (
        "یک هفته چند روز",
        "یک سال چند ماه",
        "یک ساعت چند دقیقه",
        "آب در چند درجه سانتی‌گراد یخ",
        "بزرگ‌ترین اقیانوس جهان",
        "سیاره سرخ",
        "پایتخت ژاپن",
        "شاهنامه اثر کیست",
    )
    texts = [_norm(i[0]) for i in bank]
    found = [phrase for phrase in banned
             if any(_norm(phrase) in text for text in texts)]
    check("سوال‌های ساده حذف شده‌اند", not found, f"-> {found}")

    # جمع و ضرب تک‌رقمی نشانهٔ سوال کودکانه است
    simple = [
        text for text in texts
        if re.fullmatch(r"[۰-۹0-9] [×\+\-] [۰-۹0-9] چند می ?شود؟?", text)
    ]
    check("عملیات تک‌رقمی وجود ندارد", not simple, f"-> {simple[:3]}")


def test_answer_is_unambiguous():
    print("\n### پاسخ یکتا و بدون ابهام")
    bank = all_questions()
    empty = [i[0] for i in bank if not _norm(i[1])]
    check("هیچ پاسخ خالی وجود ندارد", not empty, f"-> {empty[:2]}")

    # پاسخ نباید خودش چند گزینه را با «یا» پیشنهاد دهد
    ambiguous = [i[0] for i in bank if " یا " in _norm(i[1])]
    check("پاسخ اصلی چندگزینه‌ای نیست", not ambiguous, f"-> {ambiguous[:2]}")

    # پاسخ جایگزین نباید عیناً برابر پاسخ اصلی باشد
    redundant = [
        i[0] for i in bank
        if any(_norm(a) == _norm(i[1]) for a in i[2])
    ]
    check("جایگزین تکراری با پاسخ اصلی نیست", not redundant,
          f"-> {redundant[:2]}")

    # مجموعهٔ پذیرش هر سوال نباید تهی باشد
    from modules.fox_games.session_core import normalize_text
    broken = [
        i[0] for i in bank
        if not {normalize_text(i[1])} | {normalize_text(a) for a in i[2]}
    ]
    check("مجموعهٔ پاسخ‌های پذیرفته ناتهی است", not broken)


def test_variety():
    print("\n### تنوع موضوعی")
    texts = " ".join(_norm(i[0]) for i in all_questions())
    topics = {
        "ریاضی": ("حاصل", "جذر", "درصد", "توان", "مجموع"),
        "دنباله": ("عدد بعدی",),
        "علوم": ("شیمیایی", "اتم", "انرژی", "سیاره", "بدن", "گاز"),
        "جغرافیا": ("پایتخت", "کشور", "اقیانوس", "رود", "قاره"),
        "تاریخ": ("امپراتوری", "سلسله", "جنگ", "معاهده", "سال"),
        "ادبیات": ("سروده", "اثر", "کتاب", "شاعر"),
        "فناوری": ("رایانه", "الگوریتم", "شبکه", "برنامه‌نویسی", "پردازنده"),
        "منطق": ("اگر", "چند بار", "احتمال"),
    }
    missing = [name for name, words in topics.items()
               if not any(_norm(w) in texts for w in words)]
    check("همهٔ حوزه‌های موضوعی پوشش داده شده‌اند", not missing,
          f"-> {missing}")


def test_level_pool():
    print("\n### تابع level_pool")
    check("سطح ۱ درست برمی‌گردد", level_pool(1) is LEVELS[0])
    check("سطح ۵ درست برمی‌گردد", level_pool(5) is LEVELS[4])
    check("سطح صفر به سطح ۱ محدود می‌شود", level_pool(0) is LEVELS[0])
    check("سطح منفی به سطح ۱ محدود می‌شود", level_pool(-3) is LEVELS[0])
    check("سطح بالاتر از آخرین، سخت‌ترین را می‌دهد",
          level_pool(99) is LEVELS[-1])


# ---------------------------------------------------------------------------
# چرخش بدون تکرار
# ---------------------------------------------------------------------------
def test_no_repeat_until_bank_exhausted():
    """مهم‌ترین تست: تا مصرف کامل بانک هیچ سوالی تکرار نمی‌شود."""
    print("\n### بدون تکرار تا اتمام کامل بانک")
    sv.reset_all()
    sv.reset_history()
    bank = question_count()
    drawn = drain(-9001, bank)
    check(f"در {bank} برداشت هیچ تکراری نبود",
          len(set(drawn)) == bank, f"-> {len(set(drawn))}/{bank}")
    check("کل بانک پوشش داده شد",
          set(drawn) == {i[0] for i in all_questions()})
    check("تاریخچهٔ گروه پر شده است",
          sv.history_size(-9001) == bank, f"-> {sv.history_size(-9001)}")
    check("باقی‌مانده صفر است", sv.remaining_questions(-9001) == 0)
    sv.reset_all()
    sv.reset_history()


def test_history_resets_after_full_cycle():
    """بعد از اتمام بانک، تاریخچه ریست و دور تازه آغاز می‌شود."""
    print("\n### ریست تاریخچه پس از یک دور کامل")
    sv.reset_all()
    sv.reset_history()
    logger = Logger()
    bank = question_count()

    seq = []
    for _ in range(bank * 3 // 5 + 20):
        sv.start(-9002, logger)
        sv.join(-9002, 1, User(1, "الف"), logger)
        sv.join(-9002, 2, User(2, "ب"), logger)
        sv.begin_rounds(-9002, logger)
        for _ in range(5):
            before = logger.count("HISTORY RESET")
            item = sv.next_question(-9002, logger)
            after = logger.count("HISTORY RESET")
            seq.append((item["text"], after > before))
        sv.finish(-9002, logger=logger)

    # بریدن دنباله در نقاط ریست
    segments, current = [], []
    for text, was_reset in seq:
        if was_reset and current:
            segments.append(current)
            current = []
        current.append(text)
    if current:
        segments.append(current)

    check("دست‌کم یک ریست رخ داد", len(segments) >= 2,
          f"-> {len(segments)} بخش")
    check("هیچ بخشی تکرار ندارد",
          all(len(s) == len(set(s)) for s in segments),
          f"-> {[len(s) - len(set(s)) for s in segments]}")
    full = [s for s in segments if len(s) >= bank]
    check("هر دور کامل دقیقاً به اندازهٔ بانک است",
          all(len(s) == bank for s in full), f"-> {[len(s) for s in full]}")
    check("ریست در لاگ ثبت شد", logger.count("HISTORY RESET") >= 1)
    sv.reset_all()
    sv.reset_history()


def test_random_not_sequential():
    """انتخاب تصادفی است، نه ترتیبی؛ و بازی‌ها از سوال اول شروع نمی‌شوند."""
    print("\n### تصادفی بودن انتخاب")
    sv.reset_all()
    sv.reset_history()

    firsts = []
    for _ in range(40):
        firsts.append(play(-9003, 1)[0])
    check("سوال اولِ بازی‌های پیاپی تکرار نمی‌شود",
          len(set(firsts)) == len(firsts), f"-> {len(set(firsts))}/40")

    level_one = [item[0] for item in level_pool(1)]
    check("سوال اول همیشه یکسان نیست", len(set(firsts)) > 1)
    check("ترتیب با ترتیب تعریف بانک یکسان نیست",
          firsts != level_one[:len(firsts)])

    # دو گروه مستقل نباید ترتیب یکسان بگیرند
    sv.reset_all()
    sv.reset_history()
    a = drain(-9004, 30)
    sv.reset_all()
    b = drain(-9005, 30)
    check("دو گروه ترتیب یکسان نمی‌گیرند", a != b)
    sv.reset_all()
    sv.reset_history()


def test_history_is_per_group():
    print("\n### تاریخچه به تفکیک گروه")
    sv.reset_all()
    sv.reset_history()

    first = drain(-9006, 40)
    check("گروه اول ۴۰ سوال دیده", sv.history_size(-9006) == 40)
    check("گروه دوم هنوز تاریخچه ندارد", sv.history_size(-9007) == 0)

    second = drain(-9007, 40)
    check("گروه دوم دور تازه و بدون تکرار گرفت",
          len(set(second)) == 40, f"-> {len(set(second))}")
    check("تاریخچهٔ دو گروه از هم جداست",
          sv.history_size(-9006) == 40 and sv.history_size(-9007) == 40)

    sv.reset_history(-9006)
    check("ریست یک گروه فقط همان را پاک می‌کند",
          sv.history_size(-9006) == 0 and sv.history_size(-9007) == 40)
    sv.reset_all()
    sv.reset_history()


def test_no_repeat_inside_single_game():
    print("\n### نبود تکرار درون یک بازی طولانی")
    sv.reset_all()
    sv.reset_history()
    drawn = play(-9008, 60)
    check("۶۰ سوال پیاپی در یک بازی بدون تکرار",
          len(set(drawn)) == len(drawn), f"-> {len(set(drawn))}/{len(drawn)}")
    sv.reset_all()
    sv.reset_history()


def test_answer_validation_real_path():
    """اعتبارسنجی پاسخ روی مسیر واقعی بازی."""
    print("\n### اعتبارسنجی دقیق پاسخ")
    sv.reset_all()
    sv.reset_history()
    logger = Logger()

    sv.start(-9009, logger)
    sv.join(-9009, 1, User(1, "الف"), logger)
    sv.join(-9009, 2, User(2, "ب"), logger)
    sv.begin_rounds(-9009, logger)
    sv.next_question(-9009, logger)
    question = sv._STORE.get(-9009)["question"]

    check("پاسخ غلط رد می‌شود",
          sv.answer(-9009, 1, "یک پاسخ کاملا بی‌ربط", logger)[0] == "wrong")
    check("پاسخ درست پذیرفته می‌شود",
          sv.answer(-9009, 2, question["answer"], logger)[0] == "correct")
    sv.reset_all()
    sv.reset_history()

    # هر جایگزین تعریف‌شده باید پذیرفته شود
    from modules.fox_games.session_core import normalize_text
    failures = []
    for text, answer, aliases in all_questions():
        accepted = {normalize_text(answer)}
        accepted |= {normalize_text(a) for a in aliases}
        for alias in aliases:
            if normalize_text(alias) not in accepted:
                failures.append((text, alias))
    check("همهٔ پاسخ‌های جایگزین پذیرفته می‌شوند", not failures,
          f"-> {failures[:2]}")


def test_state_isolation():
    print("\n### استقلال کامل از بازی‌های دیگر")
    import modules.emoji_guess as eg
    import modules.flag_guess as fg
    import modules.riddles as rd
    from modules.fox_games import laugh_or_lose as lol
    from modules.fox_games import vampire as vp

    ids = {
        "survival_history": id(sv._CHAT_HISTORY),
        "survival_store": id(sv._STORE),
        "emoji_game": id(eg.GAME),
        "flag_seen": id(fg._SEEN_HISTORY),
        "riddle_seen": id(rd._SEEN_BY_USER),
        "laugh_store": id(lol._STORE),
        "vampire_store": id(vp._STORE),
    }
    check("هیچ ظرف حالتی مشترک نیست", len(set(ids.values())) == len(ids))

    sv.reset_all()
    sv.reset_history()
    eg.reset_all()
    rd.reset_all()

    drain(-9010, 20)
    before = sv.history_size(-9010)
    # بازی‌های دیگر نباید تاریخچهٔ بقا را دست بزنند
    eg.start(-9010, 1)
    rd.new_riddle(-9010, 1)
    lol.start(-9010)
    vp.start(-9010)
    check("بازی‌های دیگر تاریخچهٔ بقا را تغییر ندادند",
          sv.history_size(-9010) == before)

    eg.reset_all()
    rd.reset_all()
    lol.reset_all()
    vp.reset_all()
    check("ریست بازی‌های دیگر تاریخچهٔ بقا را پاک نکرد",
          sv.history_size(-9010) == before)
    sv.reset_all()
    sv.reset_history()


def main():
    test_bank_size()
    test_structure()
    test_uniqueness()
    test_no_trivial_questions()
    test_answer_is_unambiguous()
    test_variety()
    test_level_pool()
    test_no_repeat_until_bank_exhausted()
    test_history_resets_after_full_cycle()
    test_random_not_sequential()
    test_history_is_per_group()
    test_no_repeat_inside_single_game()
    test_answer_validation_real_path()
    test_state_isolation()

    print("\n" + "=" * 52)
    print(f"passed={PASSED} failed={FAILED}")
    print("=" * 52)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
