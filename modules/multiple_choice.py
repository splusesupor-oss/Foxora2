"""🎯 بازی چهار گزینه‌ای — انتخاب تصادفی با تاریخچهٔ ماندگار.

بانک سوال در ``modules/quiz_questions.py`` است (فقط داده). این فایل فقط
منطق دارد.

──────────────────────────────────────────────────────────────────────
چطور از تکرار جلوگیری می‌شود
──────────────────────────────────────────────────────────────────────
دو لایهٔ مستقل:

۱. **تاریخچهٔ هر کاربر** — تا وقتی حتی یک سوال دیده‌نشده باقی باشد، آن
   کاربر هرگز سوال تکراری نمی‌گیرد. این تاریخچه per-group است (دقیقاً
   مثل «حدس ایموجی») چون کیف پول و پیشرفت هم per-group هستند.

۲. **تاریخچهٔ اخیر گروه** — سوال‌هایی که همین تازگی در این گروه پرسیده
   شده‌اند از انتخاب کنار گذاشته می‌شوند، تا دو کاربر پشت سر هم یک سوال
   نگیرند و جواب لو نرود. این یک *ترجیح* است نه قید سخت: اگر همهٔ
   سوال‌های باقی‌ماندهٔ کاربر در پنجرهٔ اخیر گروه باشند، بازی قفل
   نمی‌شود و از میان همان‌ها انتخاب می‌شود (بند ۱۰ درخواست: سوال‌هایی
   که دیرتر دیده شده‌اند زودتر برمی‌گردند).

هر دو لایه در ``config/economy.json`` ذخیره می‌شوند، پس با خاموش و روشن
شدن ربات از بین نمی‌روند.

──────────────────────────────────────────────────────────────────────
کارایی
──────────────────────────────────────────────────────────────────────
``seen`` به صورت ``set`` خوانده می‌شود و ``recent`` به ``dict`` تبدیل
می‌شود که جایگاه هر سوال را نگه می‌دارد؛ پس انتخاب یک سوال از میان N
سوال O(N) است با عملیات O(1) روی هر عضو — نه جست‌وجوی تودرتو. اندیس
عددی سوال ذخیره می‌شود، نه متن کامل، تا فایل کوچک بماند.
"""
import random
from itertools import count

from economy import game_progress as _progress

from modules.quiz_questions import QUESTIONS

GAME = "multiple_choice"

# چند سوال آخرِ هر گروه کنار گذاشته می‌شوند تا پشت سر هم تکرار نشوند.
# با بانک ۲۲۴ سوالی، ۳۰ تا حدود ۱۳٪ است: به‌اندازهٔ کافی بزرگ که تکرار
# نزدیک حس نشود و به‌اندازهٔ کافی کوچک که انتخاب را خفه نکند.
RECENT_WINDOW = 30

_PERSIAN_DIGITS = {ord(p): str(i) for i, p in enumerate("۰۱۲۳۴۵۶۷۸۹")}
_PERSIAN_DIGITS.update({ord(a): str(i) for i, a in enumerate("٠١٢٣٤٥٦٧٨٩")})


def _english_digits(text):
    """ارقام فارسی/عربی را به انگلیسی تبدیل می‌کند تا «۲» هم پذیرفته شود."""
    return str(text or "").translate(_PERSIAN_DIGITS)


_FALLBACK_TOKENS = count(1)


def _next_token():
    """توکن یکتا که با ری‌استارت ربات تکرار نمی‌شود.

    شمارندهٔ حافظه‌ای با هر ری‌استارت از ۱ شروع می‌شد و مرجع جایزه را
    تکراری می‌کرد، پس دفتر تراکنش پرداخت را رد می‌کرد و کاربر سکه‌ای
    نمی‌گرفت. حالا از شمارندهٔ ماندگار اقتصاد استفاده می‌شود.
    """
    try:
        from economy import rewards as _rewards
        return _rewards.round_id()
    except Exception:
        # اگر اقتصاد در دسترس نبود، بازی نباید بخوابد.
        return next(_FALLBACK_TOKENS)


_RANDOM = random.SystemRandom()

ANSWER_SECONDS = 30
EXHAUSTED_MESSAGE = (
    "✅ شما این بازی را تکمیل کردید. لطفاً از بازی‌های دیگر استفاده کنید."
)

# سوال فعال هر چت. کلید ``chat_id`` است چون در گروه هر لحظه یک سوال
# روی تابلو است و هر کسی می‌تواند جواب بدهد.
_active_questions = {}

# سازگاری با کد قدیمی که این نام را import می‌کرد.
_SEEN_BY_USER = {}
_remaining_question_indexes = {}


def total_questions():
    return len(QUESTIONS)


# ---------------------------------------------------------------------------
# تاریخچه
# ---------------------------------------------------------------------------
def _seen(chat_id, user_id):
    """اندیس سوال‌هایی که این کاربر در این گروه دیده است."""
    result = set()
    for item in _progress.seen(chat_id, user_id, GAME):
        try:
            result.add(int(item))
        except (TypeError, ValueError):
            continue
    return result


def _pair(chat_id, user_id):
    """پشتیبانی از فراخوانی تک‌آرگومانی قدیمی ``f(user_id)``.

    امضای تاریخی این توابع فقط ``user_id`` می‌گرفت. تاریخچه حالا
    per-group است، پس امضای درست ``(chat_id, user_id)`` است؛ ولی
    فراخوان تک‌آرگومانی نباید خطا بدهد.
    """
    return (chat_id, chat_id) if user_id is None else (chat_id, user_id)


def seen_count(chat_id, user_id=None):
    """تعداد سوال‌هایی که این کاربر در این گروه دیده است."""
    return len(_seen(*_pair(chat_id, user_id)))


def remaining_count(chat_id, user_id=None):
    """تعداد سوال‌های باقی‌ماندهٔ دور فعلی برای این کاربر."""
    return max(len(QUESTIONS) - seen_count(chat_id, user_id), 0)


def is_exhausted(chat_id, user_id=None):
    """آیا این کاربر همهٔ سوال‌های دور فعلی را دیده است.

    ⚠️ این یعنی «دور فعلی تمام شد»، نه «بازی برای همیشه بسته است»؛
    ``start_question`` خودش دور تازه می‌سازد.
    """
    return remaining_count(chat_id, user_id) == 0


def cycle(chat_id, user_id):
    """شمارهٔ دور فعلی این کاربر در این گروه."""
    return _progress.cycle(chat_id, user_id, GAME)


def reset_user(chat_id, user_id=None):
    """تاریخچهٔ یک کاربر را پاک می‌کند."""
    return _progress.reset(*_pair(chat_id, user_id), GAME)


def reset_all():
    """پاک‌سازی کامل — فقط برای تست.

    ⚠️ هنگام ری‌استارت ربات صدا زده **نمی‌شود**؛ صدا زدنش یعنی پاک شدن
    تاریخچهٔ همهٔ کاربران.
    """
    _active_questions.clear()
    _SEEN_BY_USER.clear()
    _remaining_question_indexes.clear()
    _progress.reset_game_everywhere(GAME)


# ---------------------------------------------------------------------------
# انتخاب سوال
# ---------------------------------------------------------------------------
def _pick(seen, recent):
    """یک اندیس تصادفی از سوال‌های دیده‌نشده برمی‌گرداند.

    ``recent`` فهرست مرتبِ سوال‌های اخیرِ گروه است (قدیمی‌ترین اول).
    اولویت با سوال‌هایی است که اصلاً در پنجرهٔ اخیر گروه نیستند. اگر
    همهٔ گزینه‌های ممکن در آن پنجره باشند، آن‌که از همه دیرتر دیده شده
    انتخاب می‌شود تا بازی هرگز قفل نشود.
    """
    remaining = [index for index in range(len(QUESTIONS)) if index not in seen]
    if not remaining:
        return None

    # جایگاه در پنجرهٔ اخیر: هرچه کوچک‌تر، قدیمی‌تر.
    position = {}
    for order, item in enumerate(recent):
        try:
            position[int(item)] = order
        except (TypeError, ValueError):
            continue

    fresh = [index for index in remaining if index not in position]
    if fresh:
        return _RANDOM.choice(fresh)

    # همه در پنجره‌اند: قدیمی‌ترین‌ها دوباره وارد چرخه می‌شوند.
    oldest = min(position[index] for index in remaining)
    stale = [index for index in remaining if position[index] == oldest]
    return _RANDOM.choice(stale)


def start_question(chat_id, user_id=None):
    """سوال تازه‌ای که این کاربر ندیده است.

    انتخاب تصادفی است، پس دو کاربر در یک گروه معمولاً سوال‌های متفاوتی
    می‌گیرند؛ تاریخچهٔ اخیر گروه این جدایی را تقویت می‌کند.
    """
    if user_id is None:
        user_id = chat_id

    seen = _seen(chat_id, user_id)
    if len(seen) >= len(QUESTIONS):
        # کاربر همه را دیده: دور تازه، تا بازی برای همیشه بسته نماند.
        _progress.start_new_cycle(chat_id, user_id, GAME)
        seen = _seen(chat_id, user_id)

    index = _pick(seen, _progress.recent(chat_id, GAME))
    if index is None:
        return None

    question = QUESTIONS[index]
    # همین‌جا ثبت دائمی می‌شود تا اگر ربات وسط سوال خاموش شد، همان سوال
    # دوباره به این کاربر داده نشود.
    _progress.mark_seen(chat_id, user_id, GAME, index)
    _progress.mark_recent(chat_id, GAME, index, RECENT_WINDOW)

    data = {
        "token": _next_token(),
        "index": index,
        "answer": question["answer"],
        "options": list(question["options"]),
        "question": question["question"],
        "category": question["category"],
        "level": question.get("level", "متوسط"),
        "user_id": user_id,
    }
    _active_questions[chat_id] = data
    return dict(data)


def answer_question(chat_id, text, user_id=None):
    """پاسخ ۱ تا ۴ را بررسی می‌کند.

    خروجی ``(is_correct, correct_option)`` یا ``None`` وقتی متن اصلاً یک
    گزینهٔ معتبر نیست (تا پیام‌های نامرتبط بازی را نبندند).

    پاسخ‌دهنده هم این سوال را «دیده» ثبت می‌شود تا هرگز دوباره نگیرد —
    حتی اگر شروع‌کنندهٔ سوال کس دیگری بوده باشد.
    """
    data = _active_questions.get(chat_id)
    if not data:
        return None
    answer_text = _english_digits(str(text).strip())
    if answer_text not in {"1", "2", "3", "4"}:
        return None

    selected = int(answer_text)
    correct = selected == data["answer"]
    correct_option = data["answer"]
    _active_questions.pop(chat_id, None)
    if user_id is not None and data.get("index") is not None:
        _progress.mark_seen(chat_id, user_id, GAME, data["index"])
    return correct, correct_option


def get_active_question(chat_id):
    data = _active_questions.get(chat_id)
    return dict(data) if data else None


def clear_question(chat_id, token=None):
    """فقط در صورت تطابق توکن، سوال فعال را پاک می‌کند."""
    data = _active_questions.get(chat_id)
    if not data or (token is not None and data["token"] != token):
        return False

    _active_questions.pop(chat_id, None)
    return True
