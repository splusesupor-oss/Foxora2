"""🔍 تحلیل و رتبه‌بندی پاسخ‌های بازی «بهترین جواب».

این ماژول جایگزین امتیازدهیِ صرفاً «تعداد کلیدواژه» می‌شود. پاسخ هر
کاربر با چند سیگنال مستقل تحلیل می‌شود و فقط پاسخ‌های «معتبر» در رقابت
شرکت می‌کنند:

  ۱) ضد-آشغال/اسپم/بی‌معنی:
     پاسخ‌های خالی، خیلی کوتاه، «نمی‌دانم»، تکراری، فقط ایموجی/رقم، یا
     خارج از موضوع (بدون هیچ همپوشانی با سوال) رد می‌شوند.

  ۲) ارتباط با سوال (relevance):
     چه مقدار واژگانِ محتواییِ سوال در پاسخ دیده می‌شود. پاسخِ بی‌ربط
     همپوشانی تقریباً صفر دارد.

  ۳) دقت / قابل‌استفاده‌بودن (accuracy):
     چه تعداد از مفهوم‌های کلیدی (keywords) به‌درستی در پاسخ حضور دارند.

  ۴) کامل‌بودن / کیفیت (completeness):
     طول پاسخِ معنادار (پس از حذف stopword) و وجود ساختارِ جمله. پاسخِ
     فقط-کلیدواژه (keyword stuffing) و پاسخِ خیلی کوتاه امتیاز کمی می‌گیرند
     تا انتخاب برنده بر اساسِ «طول» یا «لیست کردن کلمات» نباشد.

امتیاز نهایی ترکیب وزنیِ همین سیگنال‌هاست؛ برنده بالاترین امتیاز و در
تساوی اولین پاسخ است. این سیستم کاملاً قطعی و بدون وابستگی به سرویس
خارجی است (آفلاین اجرا می‌شود).
"""
import re
import unicodedata

from modules.fox_games.session_core import normalize_text

# ---------------------------------------------------------------------------
# واژگان توقف فارسی (برای شمردن «واژهٔ معنادار»)
# ---------------------------------------------------------------------------
_STOPWORDS = frozenset({
    "و", "با", "به", "از", "که", "در", "برای", "این", "آن", "یک", "را",
    "هم", "یا", "می", "شد", "می‌شود", "می‌کند", "می‌شوند", "است", "نیست",
    "کرد", "کردن", "باید", "اگر", "اما", "ولی", "بلکه", "خود", "خودش",
    "خیلی", "بیشتر", "فقط", "مثل", "همان", "حالا", "چون", "چرا", "چه",
    "چگونه", "چند", "کی", "کجا", "زیرا", "اینکه", "بر", "تا", "ازآن",
    "دراین", "بله", "آره", "خب", "نه", "باز", "بود", "دارد", "دارند",
    "شود", "شوند", "کند", "کنند", "کرده", "گفته", "یعنی", "همین", "دیگر",
})

# الگوهای آشغال
_DONT_KNOW = ("نمی‌دانم", "نمیدانم", "نمدونم", "نمیدونم", "نمی دونم",
              "نمی‌دونم", "نمی‌دانستم", "بی‌خیال", "بیخیال", "ببخشید",
              "ببخشین", "شوخی کردم", "شوخیه", "حوصله ندارم", "نمی‌خوام",
              "نمیدونستم", "میدونم", "نمیدانستم")
_SPAM_MARKERS = ("لینک", "http", "www", "سایت", "تبلیغ", "خرید", "فروش",
                 "سکه بده", "لطفا سکه", "بازی کن", "جواب نده")


def _norm(text):
    return normalize_text(text)


def _content_tokens(text):
    """واژه‌های محتوایی (غیر-stopword، غیر رقم/نشانه) به‌صورت نرمال‌شده."""
    norm = _norm(text)
    # حذف نشانه‌های سجاوندی و رقم
    norm = re.sub(r"[؟?!.!،,؛:«»\"'()\-–]+", " ", norm)
    tokens = []
    for tok in norm.split():
        tok = tok.strip()
        if not tok:
            continue
        if tok in _STOPWORDS:
            continue
        if tok.isdigit():
            continue
        if len(tok) < 2:
            continue
        # حذف تکرار یک حرف («ههههه»)
        if len(set(tok)) == 1:
            continue
        tokens.append(tok)
    return tokens


def _has_persian_letters(text):
    return any("\u0600" <= ch <= "\u06FF" for ch in text)


def garbage_reason(text):
    """اگر پاسخ آشغال/اسپم/بی‌معنی باشد دلیل آن را برمی‌گرداند؛ وگرنه None."""
    raw = (text or "").strip()
    norm = _norm(raw).replace(" ", "")

    if not raw:
        return "empty"
    if len(raw) > 600:
        return "too_long"

    # فقط ایموجی / رقم / نشانه
    if not _has_persian_letters(raw):
        return "no_text"

    # خیلی کوتاه: کمتر از ۲ واژهٔ محتوایی
    if len(_content_tokens(raw)) < 2:
        return "too_short"

    # «نمی‌دانم» و امثال آن
    n = _norm(raw)
    for phrase in _DONT_KNOW:
        if phrase in n or _norm(phrase) in n:
            return "dont_know"

    # تکراری بودن حروف («ههههه»، «ششش»)
    if len(raw) >= 4 and len(set(n)) <= 2:
        return "repetitive"

    # اسپم/تبلیغ
    for marker in _SPAM_MARKERS:
        if _norm(marker) in n:
            return "spam"

    return None


def _token_overlap(set_a, set_b):
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / float(len(set_a))


def _keyword_matches(answer_norm, keywords):
    """کدام کلیدواژه‌ها در پاسخ حضور دارند (تطبیق زیررشتهٔ نرمال‌شده)."""
    matched = []
    for kw in keywords:
        kw_norm = _norm(kw).replace(" ", "")
        if not kw_norm:
            continue
        ans = answer_norm.replace(" ", "")
        if kw_norm in ans:
            matched.append(kw)
    return matched


def _completeness(content_tokens, matched_count):
    """کیفیت بر اساس طولِ معنادار و تنوع.

    - اگر فقط کلماتِ کلیدواژه باشند (keyword stuffing) و هیچ واژهٔ
      توضیحیِ دیگری نباشد، کامل‌بودن کم می‌شود.
    - پاسخِ ۳+ واژهٔ توضیحی کامل‌تر است.
    """
    meaningful = len(content_tokens)
    if meaningful <= 1:
        return 0.0
    # تعداد واژه‌های «توضیحی» (کل واژه‌های محتوایی منهای کلیدواژه‌ها)
    explanatory = max(0, meaningful - matched_count)
    # پایه: هرچه واژهٔ معنادار بیشتر، بهتر (تا سقف)
    base = min(1.0, meaningful / 8.0)
    # اگر توضیحی (غیر از لیست کردن کلمات) باشد، امتیاز کامل‌بودن بالا می‌رود
    if explanatory >= 2:
        base = max(base, 0.6)
    elif explanatory == 0:
        # فقط کلیدواژه بدون هیچ توضیح → keyword stuffing
        base = min(base, 0.25)
    return base


def _relevance(answer_content, question_content):
    """ارتباط: چه مقدار از واژگان محتوایی سوال در پاسخ آمده است."""
    if not question_content:
        return 0.0
    return _token_overlap(question_content, answer_content)


def analyze(question, keywords, answer):
    """تحلیل کامل یک پاسخ.

    خروجی دیکشنری:
      - valid: bool — آیا پاسخ در رقابت می‌ماند؟
      - reason: str|None — چرا نامعتبر است
      - relevance, accuracy, completeness: float در بازه ۰ تا ۱
      - quality: float ترکیبی
      - score: float برای رتبه‌بندی
      - matched: list کلیدواژه‌های حاضر
    """
    reason = garbage_reason(answer)
    if reason is not None:
        return {
            "valid": False, "reason": reason,
            "relevance": 0.0, "accuracy": 0.0, "completeness": 0.0,
            "quality": 0.0, "score": 0.0, "matched": [],
        }

    answer_norm = _norm(answer)
    answer_tokens = set(_content_tokens(answer))
    question_tokens = set(_content_tokens(question))

    matched = _keyword_matches(answer_norm, keywords)
    accuracy = len(matched) / float(len(keywords)) if keywords else 0.0

    rel = _relevance(answer_tokens, question_tokens)

    # ارتباط لازم است: پاسخِ بی‌ربط (بدون هیچ واژهٔ مشترک با سوال) در رقابت
    # نمی‌ماند، مگر اینکه دست‌کم یک کلیدواژهٔ درست داشته باشد.
    if rel <= 0.0 and accuracy <= 0.0:
        return {
            "valid": False, "reason": "off_topic",
            "relevance": rel, "accuracy": accuracy, "completeness": 0.0,
            "quality": 0.0, "score": 0.0, "matched": matched,
        }

    comp = _completeness(answer_tokens, len(matched))

    # کیفیت ترکیبی: ارتباط و دقت مهم‌ترین، کامل‌بودن پشتیبان
    quality = 0.35 * rel + 0.40 * accuracy + 0.25 * comp

    # امتیاز رتبه‌بندی: کیفیت + پاداشِ کوچکِ کامل‌بودن (نه فقط طول)
    score = quality + 0.15 * comp

    return {
        "valid": True, "reason": None,
        "relevance": round(rel, 4),
        "accuracy": round(accuracy, 4),
        "completeness": round(comp, 4),
        "quality": round(quality, 4),
        "score": round(score, 4),
        "matched": matched,
    }


def pick_best(question, keywords, answers_in_order):
    """بهترین پاسخِ معتبر را برمی‌گرداند.

    ``answers_in_order``: لیستی از ``{"user_id","name","text","ts"}`` به
    ترتیبِ ثبت. خروجی دیکشنری برنده (شامل تحلیل) یا None اگر هیچ پاسخِ
    معتبری نباشد.
    """
    best = None
    for a in answers_in_order:
        result = analyze(question, keywords, a["text"])
        if not result["valid"]:
            continue
        # در تساوی، اولین پاسخ (زمان ثبت کمتر) برنده است
        candidate = (result["score"], -a["ts"], a["user_id"], a, result)
        if best is None or candidate[0] > best[0]:
            best = candidate
        elif best is not None and candidate[0] == best[0]:
            # امتیاز برابر → اولین پاسخ
            pass
    if best is None:
        return None
    _score, _neg_ts, uid, a, result = best
    return {
        "user_id": uid,
        "name": a["name"],
        "text": a["text"],
        "score": result["score"],
        "quality": result["quality"],
        "relevance": result["relevance"],
        "accuracy": result["accuracy"],
        "completeness": result["completeness"],
        "analysis": result,
    }
