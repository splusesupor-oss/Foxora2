"""Independent, compact question bank for the Survival game.

The source game uses five difficulty bands and free-text answers. These locally
written questions follow that structure without importing the source bank.
"""
from __future__ import annotations

from typing import TypeAlias

Question: TypeAlias = tuple[str, str, tuple[str, ...]]
QuestionWithId: TypeAlias = tuple[str, str, str, tuple[str, ...]]

LEVELS: tuple[tuple[Question, ...], ...] = (
    (
        ("در رمزگذاری ASCII، کد ده‌دهی حرف بزرگ A چند است؟", "65", ("۶۵", "٦٥")),
        ("یک شبانه‌روز چند ثانیه دارد؟", "86400", ("۸۶۴۰۰", "٨٦٤٠٠")),
        ("یک هکتار چند متر مربع مساحت دارد؟", "10000", ("۱۰۰۰۰", "١٠٠٠٠")),
        ("در صفحهٔ شطرنج استاندارد چند خانهٔ تیره وجود دارد؟", "32", ("۳۲", "٣٢")),
        ("در سال میلادی عادی، ماه فوریه چند روز دارد؟", "28", ("۲۸", "٢٨")),
        ("یک کیلوگرم چند گرم است؟", "1000", ("۱۰۰۰", "١٠٠٠")),
        ("یک‌چهارم ساعت چند دقیقه است؟", "15", ("۱۵", "١٥")),
        ("هر دقیقه چند ثانیه دارد؟", "60", ("۶۰", "٦٠")),
        ("یک شبانه‌روز چند ساعت است؟", "24", ("۲۴", "٢٤")),
        ("وسیلهٔ سنجش فشار جو چه نام دارد؟", "بارومتر", ("فشارسنج هوا",)),
    ),
    (
        ("در یک مکعب روبیک ۳×۳×۳ چند مکعب گوشه‌ای وجود دارد؟", "8", ("۸", "٨")),
        ("در یک تاس سالم چند وجه دارای عدد زوج است؟", "3", ("۳", "٣")),
        ("عدد دودویی ۱۰۱۰۱ در مبنای ده چند است؟", "21", ("۲۱", "٢١")),
        ("زاویهٔ مرکزی هر قطاع در یک ده‌ضلعی منتظم چند درجه است؟", "36", ("۳۶", "٣٦")),
        ("اگر قطر دایره ۱۸ سانتی‌متر باشد، شعاع آن چند سانتی‌متر است؟", "9", ("۹", "٩")),
        ("حجم مکعب‌مستطیلی با ضلع‌های ۲، ۳ و ۵ چند واحد مکعب است؟", "30", ("۳۰", "٣٠")),
        ("در دستهٔ ورق استاندارد چند خال اصلی وجود دارد؟", "4", ("۴", "٤")),
        ("در شبکه‌ای از خانه‌های واحد با ۵ سطر و ۵ ستون چند خانه هست؟", "25", ("۲۵", "٢٥")),
        ("در ۲٫۴ متر چند میلی‌متر وجود دارد؟", "2400", ("۲۴۰۰", "٢٤٠٠")),
        ("در نظریهٔ اطلاعات، سنجهٔ میانگینِ نامعلومی چه نام دارد؟", "آنتروپی", ("entropy",)),
    ),
    (
        ("اگر f(x)=3x−4 باشد، f(10) چند است؟", "26", ("۲۶", "٢٦")),
        ("با دو تاس شش‌وجهی چند زوج مرتب از نتیجه‌ها ممکن است؟", "36", ("۳۶", "٣٦")),
        ("مساحت مثلثی با قاعدهٔ ۱۸ و ارتفاع ۱۰ چند واحد مربع است؟", "90", ("۹۰", "٩٠")),
        ("یک هشت‌ضلعی چند قطر دارد؟", "20", ("۲۰", "٢٠")),
        ("حاصل ۱۷ به توان ۲ چند است؟", "289", ("۲۸۹", "٢٨٩")),
        ("سرعت متوسط طی مسیر ۱۵۶ کیلومتری در ۳ ساعت چند کیلومتر بر ساعت است؟", "52", ("۵۲", "٥٢")),
        ("با افزایش ۵ درصدیِ ۶۰۰ واحد، مقدار تازه چند می‌شود؟", "630", ("۶۳۰", "٦٣٠")),
        ("با ۵ بیت چند الگوی دودویی متفاوت می‌توان ساخت؟", "32", ("۳۲", "٣٢")),
        ("در آمار، فاصلهٔ چارک سوم و اول چه نام دارد؟", "دامنهٔ میان‌چارکی", ("فاصلهٔ بین چارکی", "IQR")),
        ("روش فشرده‌سازی بی‌اتلاف با کدهای متغیر چه نام دارد؟", "کدگذاری هافمن", ("هافمن", "Huffman coding")),
    ),
    (
        ("دترمینان ماتریس [[3,2],[5,4]] چند است؟", "2", ("۲", "٢")),
        ("اگر log₂(x)=7 باشد، x چند است؟", "128", ("۱۲۸", "١٢٨")),
        ("یک گراف کامل با ۸ رأس چند یال دارد؟", "28", ("۲۸", "٢٨")),
        ("مساحت کل مکعبی با ضلع ۵ چند واحد مربع است؟", "150", ("۱۵۰", "١٥٠")),
        ("میانگین هندسیِ ۴ و ۱۶ چند است؟", "8", ("۸", "٨")),
        ("موجی با بسامد ۴ هرتز در ۸ ثانیه چند چرخهٔ کامل انجام می‌دهد؟", "32", ("۳۲", "٣٢")),
        ("دترمینان ماتریس قطری با درایه‌های ۲، ۳ و ۷ روی قطر چند است؟", "42", ("۴۲", "٤٢")),
        ("زاویهٔ خارجی هر ضلعِ یک ۱۸ضلعی منتظم چند درجه است؟", "20", ("۲۰", "٢٠", "بیست", "20 درجه")),
        ("اگر f(x)=x³−2x باشد، f'(3) چند است؟", "25", ("۲۵", "٢٥", "بیست و پنج")),
        ("سطح یک‌روی ساخته‌شده با نیم‌پیچش چه نام دارد؟", "نوار موبیوس", ("Möbius strip",)),
    ),
    (
        ("مشتق e^x در x=0 چند است؟", "1", ("۱", "١")),
        ("باقی‌ماندهٔ ۲ به توان ۲۰ هنگام تقسیم بر ۷ چند است؟", "4", ("۴", "٤")),
        ("چند جایگشتِ مرتب از ۷ شیء با انتخاب ۳ شیء می‌توان ساخت؟", "210", ("۲۱۰", "٢١٠")),
        ("تعداد انتخاب‌های سه‌تایی از ۱۰ عضو چند است؟", "120", ("۱۲۰", "١٢٠")),
        ("مشتق x⁴ در x=2 چند است؟", "32", ("۳۲", "٣٢")),
        ("مجموع مربع‌های اعداد ۱ تا ۱۰ چند است؟", "385", ("۳۸۵", "٣٨٥")),
        ("یک ده‌ضلعی چند قطر دارد؟", "35", ("۳۵", "٣٥")),
        ("دترمینان [[4,7],[2,6]] چند است؟", "10", ("۱۰", "١٠")),
        ("اگر f(x)=2x²+3x باشد، f'(4) چند است؟", "19", ("۱۹", "١٩")),
        ("در کامپایلر، مرحلهٔ تبدیل نویسه‌ها به توکن چه نام دارد؟", "تحلیل واژگانی", ("تحلیل لغوی", "lexical analysis")),
    ),
)


_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def _fa_number(value: int) -> str:
    return str(value).translate(_FA_DIGITS)


def _additional_questions(level: int) -> tuple[Question, ...]:
    if level == 1:
        return tuple(
            (
                f"اگر در هر یک از {_fa_number(left)} جعبه، {_fa_number(right)} مهره بگذاریم، چند مهره داریم؟",
                str(left * right),
                (_fa_number(left * right),),
            )
            for left in range(11, 17)
            for right in range(11, 16)
        )
    if level == 2:
        percentages = (12, 15, 18, 20, 25)
        bases = (200, 400, 500, 600, 800, 1000)
        return tuple(
            (
                f"{_fa_number(percent)} درصدِ {_fa_number(base)} چند می‌شود؟",
                str(percent * base // 100),
                (_fa_number(percent * base // 100),),
            )
            for percent in percentages
            for base in bases
        )
    if level == 3:
        roots = [root for root in range(33, 64) if root != 45]
        return tuple(
            (
                f"کدام عددِ مثبت را در خودش ضرب کنیم تا {_fa_number(root * root)} شود؟",
                str(root),
                (_fa_number(root),),
            )
            for root in roots
        )
    if level == 4:
        return tuple(
            (
                f"مجموع عددهای ۱ تا {_fa_number(last)} چند می‌شود؟",
                str(last * (last + 1) // 2),
                (_fa_number(last * (last + 1) // 2),),
            )
            for last in range(50, 80)
        )
    if level == 5:
        return tuple(
            (
                f"حاصل ۲ به توان {_fa_number(exponent)} چند است؟",
                str(2**exponent),
                (_fa_number(2**exponent),),
            )
            for exponent in range(21, 51)
        )
    return ()


_EXTRA_QUESTIONS: tuple[tuple[Question, ...], ...] = (
    (
        ("یک دوجین چند عدد است؟", "12", ("۱۲", "دوازده")),
        ("دو ساعت و نیم چند دقیقه است؟", "150", ("۱۵۰", "یکصد و پنجاه")),
        ("مکعب چند رأس دارد؟", "8", ("۸", "هشت")),
    ),
    (
        ("محیط مستطیلی با طول ۱۲ و عرض ۷ چند واحد است؟", "38", ("۳۸", "سی و هشت")),
        ("از ۲۵۰ مهره، سه گروهِ پنجاه‌تایی چند مهره می‌شود؟", "150", ("۱۵۰", "یکصد و پنجاه")),
    ),
    (),
    (),
    (),
)

_ARABIC_DIGIT_TRANSLATION = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")


def _with_arabic_digit_aliases(level: tuple[Question, ...]) -> tuple[Question, ...]:
    questions: list[Question] = []
    for prompt, answer, aliases in level:
        if answer.isascii() and answer.isdigit():
            arabic_digits = answer.translate(_ARABIC_DIGIT_TRANSLATION)
            if arabic_digits not in aliases:
                aliases = (*aliases, arabic_digits)
        questions.append((prompt, answer, aliases))
    return tuple(questions)


LEVELS = tuple(
    _with_arabic_digit_aliases(
        base + _additional_questions(index) + _EXTRA_QUESTIONS[index - 1]
    )
    for index, base in enumerate(LEVELS, 1)
)


# Positions whose prompt text was revised use versioned IDs so persisted
# no-repeat history does not mistake new content for an already-seen question.
_REVISED_ID_LAST_POSITION = {1: 40, 2: 10, 3: 40, 4: 10, 5: 10}


def level_pool(level: int) -> tuple[QuestionWithId, ...]:
    """Return one difficulty band with stable IDs, clamped to levels one through five."""
    bounded = min(max(int(level), 1), len(LEVELS))
    last_revised = _REVISED_ID_LAST_POSITION[bounded]
    return tuple(
        (
            f"survival-v2-l{bounded}-q{index}"
            if index <= last_revised
            else f"survival-l{bounded}-q{index}",
            prompt,
            answer,
            aliases,
        )
        for index, (prompt, answer, aliases) in enumerate(LEVELS[bounded - 1], 1)
    )


def all_questions() -> tuple[QuestionWithId, ...]:
    return tuple(
        question
        for level in range(1, len(LEVELS) + 1)
        for question in level_pool(level)
    )
