from __future__ import annotations

import asyncio
import secrets
import time
import unicodedata
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from foxbot import game_bank_expansions as _game_bank_expansions
from foxbot import game_bank_extra as _game_bank_extra
from foxbot.battle_questions import all_questions as all_battle_questions
from foxbot.filters import WordFilter
from foxbot.fun_content import DARES, JOKES, TRUTHS, pick_new
from foxbot.maemma_base import MAEMMA_BASE_PUZZLES
from foxbot.name_family import (
    NAME_FAMILY_CATEGORIES,
    playable_name_family_letters,
)
from foxbot.name_family import (
    is_static_answer as is_static_name_family_answer,
)
from foxbot.name_family import (
    normalize_answer as normalize_name_family_answer,
)
from foxbot.name_family import (
    parse_submission as parse_name_family_submission,
)
from foxbot.quiz_extra_topics import QUIZ_TOPIC_EXPANSIONS
from foxbot.quiz_independent_extra import QUIZ_CLEANROOM_EXTRA
from foxbot.quiz_metadata import metadata_for_question
from foxbot.sentence_build_extra import SENTENCE_BUILD_EXTRA
from foxbot.storage import BotStore
from foxbot.survival_questions import LEVELS as SURVIVAL_LEVELS
from foxbot.survival_questions import all_questions as all_survival_questions
from foxbot.survival_questions import level_pool as survival_level_pool
from foxbot.who_knows_bank import WHO_KNOWS_BANK, playable_rounds
from foxbot.who_knows_bank import normalized as _normalize_who_knows

ReplyCallback = Callable[..., Awaitable[Any]]
PrivateMessageCallback = Callable[[str | int, str | int, int, str], Awaitable[Any]]

# Active source game-router aliases, retaining significant Unicode spellings.
FOX_GAME_COMMANDS = frozenset({
    "بخند یا بباز", "بقا", "جعبه شانسی", "خون آشام", "خون\u200cآشام", "معما",
    "حدس جمله", "ساخت جمله", "مین یاب", "بهترین جواب", "نبرد",
    "کارگاه", "شرکت",
})
EMOJI_RESET_COMMANDS = frozenset({"شروع دوباره حدس ایموجی", "ریست حدس ایموجی"})
GAME_COMMANDS = frozenset({
    *FOX_GAME_COMMANDS,
    "اسم فامیل", "حدس ایموجی", "حدس پرچم", "تصحیح کلمات", "چهار گزینه ای",
    "کی بیشتر بلده", "دروغ یا حقیقت", "جای خالی", "چیستان",
    "جرعت", "جرات", "جرئت", "حقیقت", "حقیقت بگو", "جک",
})
_GROUP_GAME_KINDS = {
    "بخند یا بباز": "laugh_or_lose", "بقا": "survival",
    "جعبه شانسی": "lucky_box", "خون آشام": "vampire",
    "بهترین جواب": "best_answer", "نبرد": "battle", "کارگاه": "karagah",
    "حدس پرچم": "flag", "تصحیح کلمات": "correction",
    "چهار گزینه ای": "quiz", "کی بیشتر بلده": "who_knows",
    "دروغ یا حقیقت": "truth_lie", "اسم فامیل": "name_family",
    "معما": "maemma", "حدس جمله": "sentence_guess",
    "ساخت جمله": "sentence_guess", "مین یاب": "minesweeper",
}

# Local economy contract, independently maintained against the audited source
# reward table. Variable lucky-box prizes pass their selected amount explicitly.
_GAME_REWARDS = {
    "riddle": (3, "bronze"), "emoji": (4, "bronze"),
    "flag": (3, "bronze"), "name_family": (6, "bronze"),
    "correction": (3, "bronze"), "quiz": (3, "bronze"),
    "fill_blank": (2, "bronze"), "laugh_or_lose": (3, "bronze"),
    "lucky_box": (0, "bronze"), "maemma": (3, "bronze"),
    "best_answer": (2, "bronze"), "battle": (2, "bronze"),
    "sentence_guess": (3, "bronze"), "minesweeper": (5, "silver"),
    "who_knows": (2, "bronze"), "truth_lie": (2, "bronze"),
    "karagah_thief": (12, "bronze"), "survival": (8, "silver"),
    "survival_step": (1, "silver"), "vampire": (7, "silver"),
    "karagah": (12, "silver"),
}

RIDDLES = (
    ("آن چیست که هرچه از آن برداری بزرگ‌تر می‌شود؟", ("گودال", "چاله")),
    ("دندان دارد ولی چیزی نمی‌خورد؛ چیست؟", ("شانه",)),
    ("پا ندارد ولی همیشه راه می‌رود؛ چیست؟", ("ساعت",)),
    ("همیشه بالا می‌رود و پایین نمی‌آید؛ چیست؟", ("سن", "عمر")),
    ("خانه دارد اما در و پنجره ندارد؛ چیست؟", ("تخم مرغ", "تخممرغ")),
    ("کلید دارد اما هیچ دری را باز نمی‌کند؛ چیست؟", ("پیانو",)),
    ("هرچه خشک‌تر می‌کند، خودش خیس‌تر می‌شود؛ چیست؟", ("حوله",)),
    ("بدون زبان حرف می‌زند و بدون گوش شنیده می‌شود؛ چیست؟", ("پژواک", "اکو")),
)
EMOJI_PUZZLES = (
    ('🌧️☀️✨', ('رنگین کمان', 'رنگین‌کمان')),
    ("🐝🍯", ("عسل", "زنبور عسل")),
    ("🌙⭐🌌", ("شب", "آسمان شب")),
    ("🧊🐻‍❄️", ("خرس قطبی",)),
    ("📚🦉", ("دانش", "جغد کتابخوان")),
    ("🌱💧☀️", ("رشد گیاه", "گیاه")),
    ("🐢🥷🐢🥷", ("لاکپشت های نینجا", "لاک پشت های نینجا", "لاکپشت نینجا")),
    ("🚂💨", ("قطار", "قطار بخار")),
)
FILL_BLANKS = (
    ("آب در دمای صفر درجهٔ سانتی‌گراد معمولاً به ___ تبدیل می‌شود.", ("یخ",)),
    ("پایتخت ایران شهر ___ است.", ("تهران",)),
    ("رنگ حاصل از ترکیب آبی و زرد، ___ است.", ("سبز",)),
    ("یک هفته ___ روز دارد.", ("هفت", "7", "۷")),
    ("خورشید از سمت ___ طلوع می‌کند.", ("شرق",)),
    ("بزرگ‌ترین سیارهٔ منظومهٔ شمسی ___ است.", ("مشتری",)),
    ("حیوانی که به آن کشتی صحرا می‌گویند، ___ است.", ("شتر",)),
    ("فصل بعد از بهار، ___ است.", ("تابستان",)),
)
FLAGS = (
    ("🇩🇪", ("آلمان", "germany")), ("🇯🇵", ("ژاپن", "japan")),
    ("🇧🇷", ("برزیل", "brazil")), ("🇨🇦", ("کانادا", "canada")),
    ("🇪🇬", ("مصر", "egypt")), ("🇫🇷", ("فرانسه", "france")),
    ("🇮🇹", ("ایتالیا", "italy")), ("🇮🇳", ("هند", "india")),
)
CORRECTIONS = (
    ("خاهر", "خواهر"), ("مسعول", "مسئول"), ("گزارشات", "گزارش‌ها"),
    ("زغال اخته", "زغال‌اخته"), ("تاثیر", "تأثیر"), ("مساله", "مسئله"),
    ("قظعا", "قطعاً"), ("میباشد", "می‌باشد"),
)
QUIZZES = (
    ("در تقسیم یاخته‌ای که دو سلول دخترِ مشابه پدید می‌آورد، نام فرایند چیست؟", ("میوز", "میتوز", "تخمیر", "انتقال فعال"), 2),
    ("در نت‌نویسی موسیقی، نت‌ها معمولاً روی کدام ساختار نوشته می‌شوند؟", ("خط زمان", "حامل پنج‌خطی", "نقشهٔ میزان", "جدول تناوبی"), 2),
    ("کدام جانور پستاندار است؟", ("قورباغه", "کبوتر", "دلفین", "مار"), 3),
    ("کدام اقیانوس بزرگ‌ترین است؟", ("هند", "منجمد شمالی", "اطلس", "آرام"), 4),
    ("آب از چند اتم هیدروژن و یک اتم اکسیژن ساخته شده است؟", ("یک", "دو", "سه", "چهار"), 2),
)
TRUTH_LIE = (
    ("خفاش‌ها پستاندار هستند.", True),
    ("زحل تنها سیاره‌ای است که حلقه دارد.", False),
    ("هشت‌پا سه قلب دارد.", True),
    ("نور از صدا کندتر حرکت می‌کند.", False),
    ("گیاهان در فتوسنتز از نور استفاده می‌کنند.", True),
)
WHO_KNOWS_CATEGORIES = tuple(WHO_KNOWS_BANK)
WHO_KNOWS_ROUNDS = playable_rounds()
PERSIAN_LETTERS = tuple("ابپتثجچحخدذرزژسشصضطظعغفقکگلمنوهی")
SENTENCE_GUESS_PUZZLES = (
    ("🌧️ + 🪟 + 😌", "باران آرام روی پنجره می‌بارید"),
    ("🌅 + 🐦 + 🎵", "صبح زود پرنده‌ها آواز خواندند"),
    ("🦊 + 🌳 + 💤", "روباه زیر درخت استراحت می‌کرد"),
    ("🤝 + 💛 + 🆘", "دوست خوب همیشه کمک می‌کند"),
    ("📚 + 🌟 + 🙂", "امروز یک کتاب جالب خواندم"),
)
BEST_QUESTIONS = (
    ("برای صرفه‌جویی در آب خانه چه کارهای روزانه‌ای مؤثر است؟", ("شیر", "نشتی", "دوش", "کوتاه", "آبیاری")),
    ("چطور می‌توان استفاده از پلاستیک یک‌بارمصرف را کمتر کرد؟", ("کیسه", "بطری", "چندبار", "جایگزین", "بازیافت")),
    ("چرا خواب کافی به یادگیری و حافظه کمک می‌کند؟", ("خواب", "تمرکز", "حافظه", "یادگیری", "استراحت")),
    ("هنگام زلزله در فضای بسته چه اقدام‌هایی ایمن‌تر است؟", ("میز", "پنجره", "پناه", "سر", "خروج")),
    ("پیش از بازنشر یک خبر آنلاین چه چیزهایی را باید بررسی کرد؟", ("منبع", "تاریخ", "نویسنده", "تأیید", "خبر")),
    ("چطور می‌توان ورزش را به عادتی پایدار تبدیل کرد؟", ("برنامه", "آرام", "منظم", "هدف", "تمرین")),
    ("چه راه‌هایی مصرف انرژی خانه را کاهش می‌دهد؟", ("چراغ", "عایق", "دما", "خاموش", "برق")),
    ("زنبورها چه نقشی در تولید خوراک و کشاورزی دارند؟", ("گرده", "گل", "گیاه", "محصول", "باروری")),
    ("یک گروه چگونه می‌تواند تصمیمی منصفانه‌تر بگیرد؟", ("گوش", "شواهد", "معیار", "گزینه", "نظر")),
    ("برای جلوگیری از گم‌شدن پرونده‌های مهم دیجیتال چه باید کرد؟", ("نسخه", "پشتیبان", "ذخیره", "رمز", "ابری")),
    ("در گفت‌وگو با کسی که مخالف ماست چطور محترمانه رفتار کنیم؟", ("گوش", "پرسش", "احترام", "دلیل", "توضیح")),
    ("کدام تغییرهای شهری می‌تواند آلودگی هوا را کمتر کند؟", ("حمل", "عمومی", "دوچرخه", "پیاده", "سوخت")),
    ("چرا حفظ تنوع زیستی برای انسان و طبیعت مهم است؟", ("گونه", "زیستگاه", "زنجیره", "اکوسیستم", "تعادل")),
    ("چه چیزی یک آزمایش علمی را قابل تکرار می‌کند؟", ("روش", "متغیر", "اندازه", "ثبت", "کنترل")),
    ("چطور می‌توان زمان مطالعه را بهتر مدیریت کرد؟", ("هدف", "وقفه", "برنامه", "تمرکز", "تقسیم")),
    ("برای امن‌تر کردن گذرواژه‌های حساب‌های آنلاین چه روشی بهتر است؟", ("طولانی", "منحصر", "مدیر", "دومرحله", "رمز")),
)
KARAGAH_OBJECTS = (
    "دستبند نقره", "فلش مموری", "دوربین شکاری", "دفترچه یادداشت", "ساعت جیبی",
    "نقشهٔ قدیمی", "ساز کوچک", "مهرهٔ شطرنج", "قطب‌نما", "مجسمهٔ برنزی",
    "کلید گاوصندوق", "عینک مطالعه", "جام سفالی", "قلم خوشنویسی", "آلبوم عکس",
    "سنگ عقیق", "چراغ‌قوه", "رادیوی جیبی", "مدال ورزشی", "جعبهٔ موسیقی",
)


# Expand local game banks without importing source-code data or game logic.
RIDDLES += (
    _game_bank_expansions.RIDDLE_EXTRA + _game_bank_expansions.RIDDLE_EXTRA_2
)
EMOJI_PUZZLES += (
    _game_bank_expansions.EMOJI_EXTRA + _game_bank_expansions.EMOJI_EXTRA_2
)
FILL_BLANKS += (
    _game_bank_expansions.FILL_EXTRA + _game_bank_expansions.FILL_EXTRA_2
)
FLAGS += _game_bank_expansions.FLAGS_EXTRA
_FLAG_ANSWER_ADDONS = {
    "🇩🇪": ("جرمنی",),
    "🇯🇵": ("نیپون",),
    "🇺🇸": ("آمریکا",),
    "🇬🇧": ("انگلستان",),
    "🇷🇺": ("فدراسیون روسیه",),
    "🇬🇷": ("هلاس",),
    "🇰🇷": ("جمهوری کرهٔ جنوبی",),
    "🇰🇵": ("جمهوری دموکراتیک خلق کره",),
    "🇨🇳": ("جمهوری خلق چین",),
    "🇮🇳": ("بهارات",),
    "🇨🇿": ("Czech Republic",),
    "🇳🇱": ("پادشاهی هلند",),
}
FLAGS = tuple(
    (flag, answers + _FLAG_ANSWER_ADDONS.get(flag, ()))
    for flag, answers in FLAGS
)
CORRECTIONS += (
    _game_bank_expansions.CORRECTION_EXTRA
    + _game_bank_expansions.CORRECTION_EXTRA_2
)
QUIZZES += _game_bank_expansions.QUIZ_EXTRA
QUIZZES += QUIZ_CLEANROOM_EXTRA
TRUTH_LIE += (
    _game_bank_expansions.TRUTH_LIE_EXTRA + _game_bank_expansions.TRUTH_LIE_EXTRA_2
)
SENTENCES = SENTENCE_BUILD_EXTRA
SENTENCE_GUESS_PUZZLES += (
    _game_bank_expansions.SENTENCE_GUESS_EXTRA
    + _game_bank_expansions.SENTENCE_GUESS_EXTRA_2
)
BEST_QUESTIONS += (
    _game_bank_expansions.BEST_QUESTIONS_EXTRA
    + _game_bank_expansions.BEST_QUESTIONS_EXTRA_2
    + _game_bank_expansions.BEST_QUESTIONS_EXTRA_3
)
KARAGAH_OBJECTS += _game_bank_expansions.KARAGAH_OBJECTS_EXTRA
EMOJI_PUZZLES += _game_bank_extra.EMOJI_EXTRA_PARITY
QUIZZES += _game_bank_extra.QUIZ_EXTRA_PARITY
QUIZZES += QUIZ_TOPIC_EXPANSIONS
TRUTH_LIE += _game_bank_extra.TRUTH_LIE_EXTRA_PARITY
SENTENCE_GUESS_PUZZLES += _game_bank_extra.SENTENCE_GUESS_EXTRA_PARITY
QUIZ_METADATA = tuple(
    metadata_for_question(question)
    for question, _options, _correct in QUIZZES
)
MAEMMA_PUZZLES = MAEMMA_BASE_PUZZLES + _game_bank_extra.MAEMMA_EXTRA_PARITY

_GLOBAL_STATE = "__global__"
_BLOCKED_NOTICE = (
    "بازی های روباه عموم غیر فعال می‌باشند ؛ برای فعال سازی باید ادمین یا "
    "مالک با دستور سرگرمی فعال بازی هارو فعال کند تا بتوانید از بازی ها استفاده کنید ☑️"
)
_BUSY_MESSAGE = "در حال حاضر دو بازی فعال است، بعد از پایان آن‌ها دوباره تلاش کنید"
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_TEHRAN = ZoneInfo("Asia/Tehran")
_MINESWEEPER_DAILY_CHANCES = 2
_MINESWEEPER_REWARD = _GAME_REWARDS["minesweeper"][0]
_MINESWEEPER_PENALTY = 10
_MINESWEEPER_PICK_SECONDS = 60
_MINESWEEPER_CELLS = ("1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣")
_SURVIVAL_MIN_PLAYERS = 2
_SURVIVAL_MAX_PLAYERS = 4
_SURVIVAL_JOIN_SECONDS = 60
_SURVIVAL_ANSWER_SECONDS = 30
_SURVIVAL_MAX_ROUNDS = 20
_SURVIVAL_STEP_REWARD = _GAME_REWARDS["survival_step"][0]
_SURVIVAL_WINNER_REWARD = _GAME_REWARDS["survival"][0]
_SENTENCE_GUESS_ANSWER_SECONDS = 30
_SENTENCE_GUESS_RECENT_WINDOW = 15
_SENTENCE_GUESS_PENDING_DELIVERY_SECONDS = 120
_BATTLE_QUESTIONS_PER_PLAYER = 3
_BATTLE_QUESTIONS_TOTAL = _BATTLE_QUESTIONS_PER_PLAYER * 2
_BATTLE_JOIN_SECONDS = 60
_BATTLE_ANSWER_SECONDS = 30
_BATTLE_REWARD = _GAME_REWARDS["battle"][0]
_VAMPIRE_MIN_PLAYERS = 4
_VAMPIRE_MAX_PLAYERS = 5
_VAMPIRE_JOIN_SECONDS = 60
_VAMPIRE_GUESS_SECONDS = 50
_VAMPIRE_REWARD = _GAME_REWARDS["vampire"][0]
_ROLE_DM_RETRIES = 3
_ROLE_DM_RETRY_DELAY_SECONDS = 1.0
_VAMPIRE_ROLE_MESSAGE = "🧛 شما خون‌آشام هستید.\n\nتا پایان بازی چیزی نگویید."
_KARAGAH_MIN_PLAYERS = 4
_KARAGAH_MAX_PLAYERS = 5
_KARAGAH_JOIN_SECONDS = 60
_KARAGAH_THIEF_GUESS_SECONDS = 40
_KARAGAH_OBJECT_GUESS_SECONDS = 30
_KARAGAH_WINNER_REWARD = _GAME_REWARDS["karagah"][0]
_KARAGAH_THIEF_REWARD = _GAME_REWARDS["karagah_thief"][0]
_KARAGAH_OBJECT_OPTION_COUNT = 5
_KARAGAH_ROLE_MESSAGE = (
    "🕵️ شما دزد هستید!\n\n"
    "🎒 شیء دزدیده‌شده: {object}\n\n"
    "تا پایان بازی چیزی نگویید."
)
_KARAGAH_FAILED_MESSAGE = (
    "🕵️ این پرونده تشکیل نشد.\n\n"
    "چند لحظه دیگر دوباره «کارگاه» را بفرستید."
)
_KARAGAH_NOT_ENOUGH_MESSAGE = "🕵️ تعداد شرکت‌کننده کافی نبود؛ پرونده بسته شد."
_VAMPIRE_CHOSEN_MESSAGE = (
    "🩸 پیام خصوصی خون‌آشام برای یکی از بازیکنان ارسال شد. "
    "حالا حدس بزنید خون‌آشام کیست!"
)
_VAMPIRE_FAILED_MESSAGE = (
    "🧛 این دور انجام نشد.\n\n"
    "چند لحظه دیگر دوباره «خون آشام» را بفرستید."
)
_VAMPIRE_TASKS: dict[str, asyncio.Task[Any]] = {}
_VAMPIRE_JOIN_EVENTS: dict[str, asyncio.Event] = {}
_KARAGAH_TASKS: dict[str, asyncio.Task[Any]] = {}
_KARAGAH_JOIN_EVENTS: dict[str, asyncio.Event] = {}
_SECRET_ROLE_ACCESS_HASHES: dict[tuple[str, str, str], int] = {}
_LAUGH_COUNTDOWN_SECONDS = 3
_LAUGH_COUNTDOWN_INTERVAL_SECONDS = 1.0
_LAUGH_ROUND_SECONDS = 60
_LAUGH_EMOJIS = ("😂", "🤣", "😆", "😹", "😄", "😁")
_LAUGH_TASKS: dict[str, asyncio.Task[Any]] = {}


def _tehran_now() -> datetime:
    return datetime.now(_TEHRAN)


def _tehran_day_key(now: datetime | None = None) -> str:
    return (now or _tehran_now()).astimezone(_TEHRAN).strftime("%Y-%m-%d")


def _minesweeper_remaining(store: BotStore, actor_id: str | int) -> int:
    saved = store.get_group_state(
        _GLOBAL_STATE, f"game_daily_quota:minesweeper:{actor_id}", {}
    )
    if not isinstance(saved, dict) or saved.get("date") != _tehran_day_key():
        return _MINESWEEPER_DAILY_CHANCES
    try:
        used = max(0, int(saved.get("count", 0)))
    except (TypeError, ValueError, OverflowError):
        used = 0
    return max(0, _MINESWEEPER_DAILY_CHANCES - used)


def _minesweeper_consume_chance(
    store: BotStore, actor_id: str | int, *, reference: str,
) -> int:
    _consumed, remaining = store.consume_daily_game_chance(
        "minesweeper", actor_id, _tehran_day_key(),
        _MINESWEEPER_DAILY_CHANCES, reference=reference,
    )
    return remaining


def _format_minesweeper_wait(seconds: int) -> str:
    hours, remainder = divmod(max(0, int(seconds)), 3600)
    minutes = remainder // 60
    if hours and minutes:
        return f"{_fa(hours)} ساعت و {_fa(minutes)} دقیقه دیگر"
    if hours:
        return f"{_fa(hours)} ساعت دیگر"
    if minutes:
        return f"{_fa(minutes)} دقیقه دیگر"
    return "کمتر از یک دقیقه دیگر"


def _minesweeper_quota_message(now: datetime | None = None) -> str:
    local_now = (now or _tehran_now()).astimezone(_TEHRAN)
    midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    wait = int(max(0.0, (midnight - local_now).total_seconds()))
    return (
        "⏳ شانس‌های امروز شما تمام شد.\n\n"
        f"هر روز {_fa(_MINESWEEPER_DAILY_CHANCES)} شانس دارید.\n"
        "شانس تازه ساعت ۰۰:۰۰ به وقت ایران:\n"
        f"{_format_minesweeper_wait(wait)}"
    )


def _minesweeper_board(revealed: int | None = None, *, won: bool = False) -> str:
    cells = list(_MINESWEEPER_CELLS)
    if revealed is not None and 1 <= int(revealed) <= 9:
        cells[int(revealed) - 1] = "✅" if won else "💣"
    return "\n".join("".join(cells[row:row + 3]) for row in (0, 3, 6))


def _survival_start_round(
    store: BotStore,
    group_id: str | int,
    state: dict[str, Any],
    data: dict[str, Any],
    *,
    current: float,
) -> str:
    round_number = int(data.get("round", 0) or 0) + 1
    level = min(round_number, len(SURVIVAL_LEVELS))
    history_key = f"survival_question_history:{group_id}"
    saved_history = store.get_group_state(_GLOBAL_STATE, history_key, {})
    if isinstance(saved_history, dict) and isinstance(saved_history.get("seen"), list):
        seen = {str(item) for item in saved_history["seen"]}
    else:
        seen = set()

    current_pool = [
        item for item in survival_level_pool(level) if item[0] not in seen
    ]
    pool = current_pool
    if not pool:
        fallback_levels = list(range(level + 1, len(SURVIVAL_LEVELS) + 1))
        fallback_levels.extend(range(level - 1, 0, -1))
        for fallback_level in fallback_levels:
            pool = [
                item for item in survival_level_pool(fallback_level)
                if item[0] not in seen
            ]
            if pool:
                level = fallback_level
                break
    if not pool:
        seen.clear()
        pool = list(all_survival_questions())
        level = min(round_number, len(SURVIVAL_LEVELS))

    question_id, question, answer, aliases = secrets.choice(pool)
    seen.add(question_id)
    store.set_group_state(
        _GLOBAL_STATE, history_key, {"seen": sorted(seen)},
    )
    accepted_answers = [answer, *aliases]
    used = data.get("used_questions", [])
    used = {str(item) for item in used} if isinstance(used, list) else set()
    used.add(question_id)
    data.update({
        "phase": "playing",
        "round": round_number,
        "level": level,
        "question_id": question_id,
        "question": question,
        "accepted_answers": accepted_answers,
        "answered": [],
        "used_questions": sorted(used),
    })
    state["phase"] = "playing"
    state["data"] = data
    state["answers"] = accepted_answers
    state["expires_at"] = current + _SURVIVAL_ANSWER_SECONDS
    prompt = (
        f"🏕 مرحلهٔ {_fa(round_number)} از {_fa(_SURVIVAL_MAX_ROUNDS)}"
        f" — سطح {_fa(level)} از {_fa(len(SURVIVAL_LEVELS))}"
        f"\n\n{question}\n\n"
        "پاسخ را به‌صورت متن بفرستید.\n"
        f"⏳ {_fa(_SURVIVAL_ANSWER_SECONDS)} ثانیه"
    )
    state["prompt"] = prompt
    return prompt


def _battle_pick_questions(store: BotStore, group_id: str | int) -> list[dict[str, Any]]:
    history_key = f"battle_question_history:{group_id}"
    saved = store.get_group_state(_GLOBAL_STATE, history_key, {})
    seen = {
        str(item) for item in saved.get("seen", [])
    } if isinstance(saved, dict) and isinstance(saved.get("seen"), list) else set()
    bank = list(all_battle_questions())
    available = [question for question in bank if question[0] not in seen]
    if len(available) < _BATTLE_QUESTIONS_TOTAL:
        seen.clear()
        available = bank
    selected = secrets.SystemRandom().sample(available, _BATTLE_QUESTIONS_TOTAL)
    seen.update(question[0] for question in selected)
    store.set_group_state(_GLOBAL_STATE, history_key, {"seen": sorted(seen)})
    return [
        {"id": question_id, "question": prompt, "accepted_answers": [answer, *aliases]}
        for question_id, prompt, answer, aliases in selected
    ]


def _battle_set_question(
    state: dict[str, Any], data: dict[str, Any], *, current: float,
) -> str | None:
    questions = data.get("questions", [])
    players = data.get("players", [])
    if not isinstance(questions, list) or not isinstance(players, list) or len(players) != 2:
        return None
    index = int(data.get("index", 0) or 0)
    if not 0 <= index < len(questions):
        return None
    question = questions[index]
    if not isinstance(question, dict):
        return None
    assignee = players[index % 2]
    if not isinstance(assignee, dict):
        return None
    data["current_player"] = str(assignee.get("user_id") or "")
    state["phase"] = "playing"
    state["data"] = data
    state["answers"] = list(question.get("accepted_answers", []))
    number = index // 2 + 1
    player_number = index % 2 + 1
    prompt = (
        f"⚔️ بازیکن {_fa(player_number)} — سؤال {_fa(number)}"
        f"\n\n{question.get('question', '')}"
        f"\n\n⏳ {_fa(_BATTLE_ANSWER_SECONDS)} ثانیه"
        f"\nبرای: {assignee.get('name', 'بازیکن')}"
    )
    state["prompt"] = prompt
    state["expires_at"] = current + _BATTLE_ANSWER_SECONDS
    return prompt


def refresh_timed_game_deadline_after_delivery(
    store: BotStore,
    group_id: str | int,
    state_key: str,
    *,
    token: str,
    phase: str,
    stage: int,
    expected_prompt: str | None = None,
    now: float | None = None,
) -> bool:
    """Start a survival/battle window after its prompt has been sent.

    The reference begins each countdown after the platform send completes.
    State transitions are staged before the send locally, so refresh only the
    same session and stage after the final ordered output succeeds.
    """
    state = store.get_group_state(group_id, state_key)
    if not isinstance(state, dict):
        return False
    if str(state.get("token") or "") != str(token):
        return False
    if state.get("status", "pending") != "pending":
        return False
    data = state.get("data", {})
    if not isinstance(data, dict):
        return False
    kind = str(state.get("kind") or "")
    current_phase = str(state.get("phase") or data.get("phase") or "")
    if phase not in {"joining", "playing"} or current_phase != phase:
        return False
    if kind == "survival":
        try:
            current_stage = int(data.get("round", 0) or 0)
        except (TypeError, ValueError):
            return False
        seconds = _SURVIVAL_JOIN_SECONDS if phase == "joining" else _SURVIVAL_ANSWER_SECONDS
    elif kind == "battle":
        try:
            current_stage = int(data.get("index", 0) or 0)
        except (TypeError, ValueError):
            return False
        seconds = _BATTLE_JOIN_SECONDS if phase == "joining" else _BATTLE_ANSWER_SECONDS
    else:
        return False
    if current_stage != int(stage):
        return False
    if expected_prompt is not None and str(state.get("prompt") or "") != expected_prompt:
        return False
    if phase == "playing" and not expected_prompt:
        return False
    state["expires_at"] = (time.time() if now is None else float(now)) + seconds
    store.set_group_state(group_id, state_key, state)
    return True


def refresh_sentence_deadline_after_delivery(
    store: BotStore,
    group_id: str | int,
    state_key: str,
    *,
    token: str,
    mode: str,
    puzzle_index: int,
    expected_prompt: str,
    now: float | None = None,
) -> bool:
    """Start a sentence-game answer window only after confirmed delivery."""
    state = store.get_group_state(group_id, state_key)
    if not isinstance(state, dict):
        return False
    if str(state.get("token") or "") != str(token):
        return False
    if state.get("status", "pending") != "pending":
        return False
    if state.get("kind") != "sentence_guess" or not state.get("deadline_pending"):
        return False
    if str(state.get("prompt") or "") != expected_prompt:
        return False
    data = state.get("data", {})
    if not isinstance(data, dict) or str(data.get("mode") or "") != mode:
        return False
    try:
        current_index = int(data.get("puzzle_index", -1))
    except (TypeError, ValueError):
        return False
    if current_index != int(puzzle_index):
        return False
    state["expires_at"] = (
        time.time() if now is None else float(now)
    ) + _SENTENCE_GUESS_ANSWER_SECONDS
    state.pop("deadline_pending", None)
    state.pop("deadline_pending_since", None)
    store.set_group_state(group_id, state_key, state)
    return True


def _battle_finish_notice(
    store: BotStore,
    group_id: str | int,
    state: dict[str, Any],
    data: dict[str, Any],
) -> str:
    players = data.get("players", [])
    scores = data.get("scores", {})
    if not isinstance(players, list) or len(players) != 2 or not isinstance(scores, dict):
        state["status"] = "timed_out"
        return "⚔️ نبرد پایان یافت؛ نتیجهٔ بازیکنان در دسترس نیست."
    score_values = [int(scores.get(str(player.get("user_id")), 0) or 0) for player in players]
    lines = [
        "🏁 پایان نبرد!",
        f"{players[0].get('name', 'بازیکن')}: {_fa(score_values[0])}",
        f"{players[1].get('name', 'بازیکن')}: {_fa(score_values[1])}",
    ]
    rewards: list[str] = []
    token = str(state.get("token") or "")
    if score_values[0] == score_values[1]:
        outcome = "🤝 مساوی شد."
        if score_values[0] > 0:
            for player in players:
                user_id = str(player.get("user_id") or "")
                try:
                    _profile, award_outcome = store.award_group_coins(
                        group_id, user_id, _BATTLE_REWARD,
                        reference=f"battle:{group_id}:{token}:tie:{user_id}",
                        display_name=str(player.get("name") or ""),
                        note="game:battle", return_outcome=True,
                    )
                    rewards.append(
                        f"🪙 {player.get('name', 'بازیکن')} +"
                        f"{_fa(_BATTLE_REWARD)} سکه برنز"
                        if award_outcome == "awarded"
                        else f"🪙 جایزهٔ {player.get('name', 'بازیکن')} قبلاً ثبت شده بود"
                    )
                except ValueError:
                    continue
    else:
        winner_index = 0 if score_values[0] > score_values[1] else 1
        winner = players[winner_index]
        outcome = f"🏆 برنده: {winner.get('name', 'بازیکن')}"
        user_id = str(winner.get("user_id") or "")
        try:
            _profile, award_outcome = store.award_group_coins(
                group_id, user_id, _BATTLE_REWARD,
                reference=f"battle:{group_id}:{token}:winner:{user_id}",
                display_name=str(winner.get("name") or ""),
                note="game:battle", return_outcome=True,
            )
            rewards.append(
                f"🪙 {winner.get('name', 'بازیکن')} +"
                f"{_fa(_BATTLE_REWARD)} سکه برنز"
                if award_outcome == "awarded"
                else f"🪙 جایزهٔ {winner.get('name', 'بازیکن')} قبلاً ثبت شده بود"
            )
        except ValueError:
            pass
    state["status"] = "timed_out"
    return "\n".join([*lines, "", outcome, *rewards])


def _battle_answer_matches(text: str, accepted_answers: list[Any]) -> bool:
    normalized = _normalize_answer(text)
    if not normalized:
        return False
    for item in accepted_answers:
        candidate = _normalize_answer(str(item))
        if candidate and (candidate == normalized or candidate in normalized or normalized in candidate):
            return True
    return False


def _private_role_peer_key(
    group_id: str | int, kind: str, user_id: str | int,
) -> tuple[str, str, str]:
    return str(group_id), str(kind), str(user_id)


def _clear_private_role_peers(group_id: str | int, kind: str) -> None:
    group_key = str(group_id)
    for key in [key for key in _SECRET_ROLE_ACCESS_HASHES if key[:2] == (group_key, kind)]:
        _SECRET_ROLE_ACCESS_HASHES.pop(key, None)


def _secret_player_info(event: Any, actor_id: str | int) -> tuple[dict[str, Any], int | None]:
    sender = getattr(event, "sender", None)
    username = str(getattr(sender, "username", None) or "").strip().lstrip("@")
    name = " ".join(
        str(part).strip() for part in (
            getattr(sender, "first_name", None), getattr(sender, "last_name", None)
        ) if part and str(part).strip()
    )
    if not name:
        name = f"@{username}" if username else "کاربر"
    access_hash = getattr(sender, "access_hash", None)
    try:
        access_hash = int(access_hash) if access_hash is not None else None
    except (TypeError, ValueError, OverflowError):
        access_hash = None
    sender_id = getattr(sender, "id", None)
    if sender_id is not None and str(sender_id) != str(actor_id):
        access_hash = None
    if bool(getattr(sender, "min", False)):
        access_hash = None
    if access_hash is None:
        input_sender = getattr(event, "input_sender", None)
        if str(getattr(input_sender, "user_id", "")) == str(actor_id):
            try:
                access_hash = int(input_sender.access_hash)
            except (AttributeError, TypeError, ValueError, OverflowError):
                access_hash = None
    player = {"user_id": str(actor_id), "name": name, "tag": username}
    return player, access_hash


def _vampire_reveal_text(data: dict[str, Any]) -> str:
    players = data.get("players", [])
    index = data.get("vampire_index")
    if not isinstance(players, list) or not isinstance(index, int) or not 0 <= index < len(players):
        return "⏰ زمان تمام شد؛ دور خون‌آشام پایان یافت."
    vampire = players[index]
    if not isinstance(vampire, dict):
        return "⏰ زمان تمام شد؛ دور خون‌آشام پایان یافت."
    name = str(vampire.get("name") or "کاربر")
    tag = str(vampire.get("tag") or "")
    suffix = f" ({tag})" if tag and tag != name else ""
    return f"⏰ زمان تمام شد.\n\n🧛 خون‌آشام:\n\n{name}{suffix}"


def _dm_error_is_transient(error: BaseException | None) -> bool:
    if error is None:
        return False
    error_name = type(error).__name__.lower()
    error_text = str(error).lower()
    transient_names = (
        "timeout", "timedouterror", "rpc error", "rpcerror", "flood", "servererror",
        "network", "connectionerror", "ioerror", "oserror", "typeerror",
        "queuefull", "rpcqueuefull",
    )
    transient_keywords = (
        "timeout", "timed out", "flood", "network", "connection",
        "cannot connect", "connection reset", "timedout",
    )
    return any(word in error_name for word in transient_names) or any(
        word in error_text for word in transient_keywords
    )


async def _deliver_private_role(
    private_message_sender: PrivateMessageCallback,
    *,
    group_id: str | int,
    user_id: str | int,
    access_hash: int,
    message: str,
) -> tuple[bool, BaseException | None]:
    last_error: BaseException | None = None
    for attempt in range(_ROLE_DM_RETRIES):
        try:
            result = await private_message_sender(
                group_id, str(user_id), int(access_hash), message,
            )
            if result is None or result is False:
                raise RuntimeError("private role delivery was not confirmed")
            return True, None
        except Exception as error:  # noqa: BLE001 - bounded retry; otherwise reassign privately
            last_error = error
            if not _dm_error_is_transient(error):
                break
            if attempt + 1 < _ROLE_DM_RETRIES and _ROLE_DM_RETRY_DELAY_SECONDS > 0:
                await asyncio.sleep(_ROLE_DM_RETRY_DELAY_SECONDS)
    return False, last_error


async def _run_vampire_assignment(
    event: Any,
    store: BotStore,
    *,
    group_id: str | int,
    token: str,
    join_event: asyncio.Event,
    reply: ReplyCallback | None,
    private_message_sender: PrivateMessageCallback | None,
    join_seconds: float | None = None,
) -> None:
    group_key = str(group_id)
    state_key = _key("vampire")
    wait_seconds = _VAMPIRE_JOIN_SECONDS if join_seconds is None else join_seconds
    try:
        if wait_seconds > 0:
            try:
                await asyncio.wait_for(join_event.wait(), timeout=wait_seconds)
            except TimeoutError:
                pass
        state = store.get_group_state(group_id, state_key)
        if not isinstance(state, dict) or state.get("token") != token:
            return
        if state.get("status", "pending") != "pending":
            return
        if str(state.get("phase") or "joining") != "joining":
            return
        data = state.get("data", {}) if isinstance(state.get("data"), dict) else {}
        players = data.get("players", [])
        players = players if isinstance(players, list) else []
        if len(players) < _VAMPIRE_MIN_PLAYERS:
            state["status"] = "timed_out"
            state["notice_text"] = "🧛 تعداد شرکت‌کننده کافی نبود؛ بازی لغو شد."
            store.set_group_state(group_id, state_key, state)
            return
        eligible = [
            index for index, player in enumerate(players)
            if isinstance(player, dict)
            and _private_role_peer_key(group_id, "vampire", player.get("user_id", ""))
            in _SECRET_ROLE_ACCESS_HASHES
        ]
        if not eligible or private_message_sender is None:
            state["status"] = "timed_out"
            state["notice_text"] = _VAMPIRE_FAILED_MESSAGE
            store.set_group_state(group_id, state_key, state)
            return

        last_key = f"vampire_last_user:{group_id}"
        last_vampire = store.get_group_state(_GLOBAL_STATE, last_key)
        remaining = set(eligible)
        vampire_index: int | None = None
        while remaining:
            options = sorted(remaining)
            preferred = [
                index for index in options
                if str(players[index].get("user_id")) != str(last_vampire)
            ]
            candidate_index = secrets.choice(preferred or options)
            candidate = players[candidate_index]
            state["phase"] = "assigning"
            data["phase"] = "assigning"
            state["data"] = data
            store.set_group_state(group_id, state_key, state)
            delivered, _error = await _deliver_private_role(
                private_message_sender,
                group_id=group_id,
                user_id=candidate["user_id"],
                access_hash=_SECRET_ROLE_ACCESS_HASHES[
                    _private_role_peer_key(group_id, "vampire", candidate["user_id"])
                ],
                message=_VAMPIRE_ROLE_MESSAGE,
            )
            if delivered:
                vampire_index = candidate_index
                break
            remaining.remove(candidate_index)

        if vampire_index is None:
            state["status"] = "timed_out"
            state["notice_text"] = _VAMPIRE_FAILED_MESSAGE
            store.set_group_state(group_id, state_key, state)
            return

        vampire = players[vampire_index]
        data["phase"] = "guessing"
        data["vampire_id"] = str(vampire["user_id"])
        data["vampire_index"] = vampire_index
        data["guessed"] = []
        state["phase"] = "guessing"
        state["data"] = data
        state["expires_at"] = time.time() + _VAMPIRE_GUESS_SECONDS
        roster = "\n".join(
            f"{_fa(index)}. {player.get('name', 'کاربر')}"
            for index, player in enumerate(players, 1)
            if isinstance(player, dict)
        )
        state["prompt"] = roster
        store.set_group_state(group_id, state_key, state)
        await _send(event, reply, _VAMPIRE_CHOSEN_MESSAGE)
        await _send(event, reply, roster)
    except Exception:  # noqa: BLE001 - stage a bounded public fallback, no traceback per game
        failed = store.get_group_state(group_id, state_key)
        if (
            isinstance(failed, dict)
            and failed.get("token") == token
            and failed.get("status", "pending") == "pending"
        ):
            failed_data = failed.get("data", {})
            failed_data = failed_data if isinstance(failed_data, dict) else {}
            if str(failed.get("phase") or failed_data.get("phase")) == "guessing":
                notice = _vampire_reveal_text(failed_data)
                vampire_id = str(failed_data.get("vampire_id") or "")
                if vampire_id:
                    store.set_group_state(
                        _GLOBAL_STATE, f"vampire_last_user:{group_id}", vampire_id
                    )
            else:
                notice = _VAMPIRE_FAILED_MESSAGE
            failed["status"] = "timed_out"
            failed["notice_text"] = notice[:2500]
            store.set_group_state(group_id, state_key, failed)
    finally:
        _clear_private_role_peers(group_id, "vampire")
        current_task = asyncio.current_task()
        if _VAMPIRE_TASKS.get(group_key) is current_task:
            _VAMPIRE_TASKS.pop(group_key, None)
        if _VAMPIRE_JOIN_EVENTS.get(group_key) is join_event:
            _VAMPIRE_JOIN_EVENTS.pop(group_key, None)


def _karagah_object_options() -> tuple[str, list[str]]:
    stolen = secrets.choice(KARAGAH_OBJECTS)
    rng = secrets.SystemRandom()
    options = rng.sample(
        [item for item in KARAGAH_OBJECTS if item != stolen],
        _KARAGAH_OBJECT_OPTION_COUNT - 1,
    )
    options.append(stolen)
    rng.shuffle(options)
    return stolen, options


def _karagah_stage_resolution(
    store: BotStore,
    group_id: str | int,
    state_key: str,
    state: dict[str, Any],
    data: dict[str, Any],
    *,
    result: str,
    reason: str,
    finder_id: str | None = None,
) -> str:
    data["phase"] = "settling"
    data["resolution"] = {
        "result": result,
        "reason": reason,
        "finder_id": finder_id,
    }
    state["phase"] = "settling"
    state["data"] = data
    state["expires_at"] = time.time()
    store.set_group_state(group_id, state_key, state)
    return _karagah_resolution_notice(store, group_id, state_key, state, data)


def _karagah_resolution_notice(
    store: BotStore,
    group_id: str | int,
    state_key: str,
    state: dict[str, Any],
    data: dict[str, Any],
) -> str:
    players = data.get("players", [])
    players = players if isinstance(players, list) else []
    try:
        thief_index = int(data.get("thief_index"))
    except (TypeError, ValueError, OverflowError):
        thief_index = -1
    thief = (
        players[thief_index]
        if 0 <= thief_index < len(players) and isinstance(players[thief_index], dict)
        else None
    )
    stolen = str(data.get("stolen_object") or "—")
    resolution = data.get("resolution", {})
    resolution = resolution if isinstance(resolution, dict) else {}
    result = str(resolution.get("result") or "")
    reason = str(resolution.get("reason") or "")
    token = str(state.get("token") or "")
    reward_line = ""

    if thief is None:
        notice = "🕵️ پرونده بدون نتیجه بسته شد."
    else:
        thief_id = str(thief.get("user_id") or "")
        store.set_group_state(
            _GLOBAL_STATE, f"karagah_last_thief:{group_id}", thief_id
        )
        if result == "solved":
            finder_id = str(resolution.get("finder_id") or "")
            finder = next(
                (player for player in players
                 if isinstance(player, dict) and str(player.get("user_id")) == finder_id),
                None,
            )
            if finder is None:
                notice = "🕵️ پرونده بدون نتیجه بسته شد."
            else:
                try:
                    _profile, award_outcome = store.award_group_coins(
                        group_id, finder_id, _KARAGAH_WINNER_REWARD, "silver",
                        reference=f"karagah:{group_id}:{token}:winner",
                        display_name=str(finder.get("name") or ""),
                        note="game:karagah", return_outcome=True,
                    )
                    reward_line = (
                        f"\n🥈 +{_fa(_KARAGAH_WINNER_REWARD)} سکه نقره"
                        if award_outcome == "awarded"
                        else "\n🪙 جایزهٔ این دور قبلاً ثبت شده بود"
                    )
                except ValueError:
                    reward_line = ""
                notice = (
                    "🏆 پرونده حل شد!\n\n"
                    f"✅ شیء دزدیده‌شده: {stolen}\n"
                    f"🎉 برنده: {finder.get('name', 'کاربر')}{reward_line}"
                )
        elif result == "thief_wins":
            try:
                _profile, award_outcome = store.award_group_coins(
                    group_id, thief_id, _KARAGAH_THIEF_REWARD, "bronze",
                    reference=f"karagah:{group_id}:{token}:thief",
                    display_name=str(thief.get("name") or ""),
                    note="game:karagah_thief", return_outcome=True,
                )
                reward_line = (
                    f"\n🥉 +{_fa(_KARAGAH_THIEF_REWARD)} "
                    "سکه برنز برای دزد"
                    if award_outcome == "awarded"
                    else "\n🪙 جایزهٔ این دور قبلاً ثبت شده بود"
                )
            except ValueError:
                reward_line = ""
            header = (
                "❌ حدس شیء اشتباه بود!\n\n"
                if reason == "object_wrong"
                else "⏰ زمان تمام شد!\n\n"
            )
            notice = (
                f"{header}😈 دزد برنده شد!\n\n"
                f"🕵️ دزد: {thief.get('name', 'کاربر')}\n"
                f"🎒 شیء دزدیده‌شده: {stolen}{reward_line}"
            )
        else:
            notice = "🕵️ پرونده بدون نتیجه بسته شد."

    state["status"] = "timed_out"
    state["phase"] = "finished"
    state["notice_text"] = notice[:2500]
    store.set_group_state(group_id, state_key, state)
    return notice[:2500]


async def _run_karagah_assignment(
    event: Any,
    store: BotStore,
    *,
    group_id: str | int,
    token: str,
    join_event: asyncio.Event,
    reply: ReplyCallback | None,
    private_message_sender: PrivateMessageCallback | None,
    join_seconds: float | None = None,
) -> None:
    group_key = str(group_id)
    state_key = _key("karagah")
    wait_seconds = _KARAGAH_JOIN_SECONDS if join_seconds is None else join_seconds
    try:
        if wait_seconds > 0:
            try:
                await asyncio.wait_for(join_event.wait(), timeout=wait_seconds)
            except TimeoutError:
                pass
        state = store.get_group_state(group_id, state_key)
        if not isinstance(state, dict) or state.get("token") != token:
            return
        if state.get("status", "pending") != "pending":
            return
        if str(state.get("phase") or "joining") != "joining":
            return
        data = state.get("data", {}) if isinstance(state.get("data"), dict) else {}
        players = data.get("players", [])
        players = players if isinstance(players, list) else []
        if len(players) < _KARAGAH_MIN_PLAYERS:
            state["status"] = "timed_out"
            state["notice_text"] = _KARAGAH_NOT_ENOUGH_MESSAGE
            store.set_group_state(group_id, state_key, state)
            return

        stolen, object_options = _karagah_object_options()
        data.update({
            "phase": "assigning",
            "stolen_object": stolen,
            "options": object_options,
        })
        state["phase"] = "assigning"
        state["data"] = data
        store.set_group_state(group_id, state_key, state)

        eligible = [
            index for index, player in enumerate(players)
            if isinstance(player, dict)
            and _private_role_peer_key(group_id, "karagah", player.get("user_id", ""))
            in _SECRET_ROLE_ACCESS_HASHES
        ]
        if not eligible or private_message_sender is None:
            state["status"] = "timed_out"
            state["notice_text"] = _KARAGAH_FAILED_MESSAGE
            store.set_group_state(group_id, state_key, state)
            return

        last_thief = store.get_group_state(
            _GLOBAL_STATE, f"karagah_last_thief:{group_id}"
        )
        remaining = set(eligible)
        thief_index: int | None = None
        while remaining:
            options_left = sorted(remaining)
            preferred = [
                index for index in options_left
                if str(players[index].get("user_id")) != str(last_thief)
            ]
            candidate_index = secrets.choice(preferred or options_left)
            candidate = players[candidate_index]
            state["phase"] = "assigning"
            data["phase"] = "assigning"
            state["data"] = data
            store.set_group_state(group_id, state_key, state)
            access_hash = _SECRET_ROLE_ACCESS_HASHES[
                _private_role_peer_key(group_id, "karagah", candidate["user_id"])
            ]
            role_message = _KARAGAH_ROLE_MESSAGE.format(object=stolen)
            delivered, _error = await _deliver_private_role(
                private_message_sender,
                group_id=group_id,
                user_id=candidate["user_id"],
                access_hash=access_hash,
                message=role_message,
            )
            if delivered:
                thief_index = candidate_index
                break
            remaining.remove(candidate_index)

        if thief_index is None:
            state["status"] = "timed_out"
            state["notice_text"] = _KARAGAH_FAILED_MESSAGE
            store.set_group_state(group_id, state_key, state)
            return

        data.update({
            "phase": "thief_guess",
            "thief_id": str(players[thief_index]["user_id"]),
            "thief_index": thief_index,
            "finder_id": None,
            "guessed": [],
        })
        state["phase"] = "thief_guess"
        state["data"] = data
        state["expires_at"] = time.time() + _KARAGAH_THIEF_GUESS_SECONDS
        roster = "\n".join(
            f"{index}- {player.get('name', 'کاربر')}"
            for index, player in enumerate(players, 1)
            if isinstance(player, dict)
        )
        state["prompt"] = roster
        store.set_group_state(group_id, state_key, state)
        notice = (
            "🕵️ پروندهٔ جدید ساخته شد\n\n"
            f"شرکت‌کنندگان:\n\n{roster}\n\n"
            "دزد انتخاب شد و پیام نقش فقط به پیوی او ارسال شد 🤫\n\n"
            "شروع پرونده؟\n"
            "🕵️ دزد را با شماره حدس بزنید\n"
            f"⏳ {_fa(_KARAGAH_THIEF_GUESS_SECONDS)} ثانیه فرصت دارید"
        )
        await _send(event, reply, notice)
    except Exception:  # noqa: BLE001 - preserve a generic fallback without logging payloads
        failed = store.get_group_state(group_id, state_key)
        if (
            isinstance(failed, dict)
            and failed.get("token") == token
            and failed.get("status", "pending") == "pending"
        ):
            failed["status"] = "timed_out"
            failed["notice_text"] = _KARAGAH_FAILED_MESSAGE
            store.set_group_state(group_id, state_key, failed)
    finally:
        _clear_private_role_peers(group_id, "karagah")
        current_task = asyncio.current_task()
        if _KARAGAH_TASKS.get(group_key) is current_task:
            _KARAGAH_TASKS.pop(group_key, None)
        if _KARAGAH_JOIN_EVENTS.get(group_key) is join_event:
            _KARAGAH_JOIN_EVENTS.pop(group_key, None)


def normalize(value: str) -> str:
    text = str(value or "")
    for source, target in (("\u200c", " "), ("\u200f", ""), ("\u200e", ""), ("\ufeff", ""), ("\u00a0", " "), ("ي", "ی"), ("ك", "ک")):
        text = text.replace(source, target)
    return " ".join(text.split())


def _parse_truth_lie_guess(value: str) -> bool | None:
    text = str(value or "").strip().translate(_DIGITS)
    text = text.replace("\ufe0f", "").replace("\u20e3", "")
    normalized = normalize(text).replace("ي", "ی")
    if normalized in {"1", "حقیقت", "حقیقته", "درست", "درسته", "راست", "راسته"}:
        return True
    if normalized in {"2", "دروغ", "دروغه", "غلط", "غلطه", "نادرست"}:
        return False
    return None


def _key(kind: str, user_id: str | int | None = None) -> str:
    return f"game_session:{'group' if user_id is None else 'user'}:{kind}" + (f":{user_id}" if user_id is not None else "")


def _seen_key(kind: str, user_id: str | int) -> str:
    return f"game_seen:{kind}:{user_id}"


def _choose(store: BotStore, group_id: str | int, kind: str, user_id: str | int, bank: tuple[Any, ...]) -> tuple[int, Any, list[int]]:
    previous = store.get_group_state(group_id, _seen_key(kind, user_id), [])
    index, item, seen = pick_new(bank, previous if isinstance(previous, list) else [])
    store.set_group_state(group_id, _seen_key(kind, user_id), seen)
    return index, item, seen


def _choose_unanswered(
    store: BotStore,
    group_id: str | int,
    progress_kind: str,
    user_id: str | int,
    bank: tuple[Any, ...],
    *,
    exclude_answers: set[str] | None = None,
) -> tuple[int, Any, int]:
    key = _seen_key(progress_kind, user_id)
    previous = store.get_group_state(group_id, key, [])
    saved = previous if isinstance(previous, list) else []
    seen = sorted({
        int(value) for value in saved
        if str(value).isdigit() and 0 <= int(value) < len(bank)
    })
    if len(seen) >= len(bank):
        seen = []
        store.set_group_state(group_id, key, seen)
    available = [index for index in range(len(bank)) if index not in set(seen)]
    excluded = {
        normalized for answer in (exclude_answers or set())
        if (normalized := _normalize_answer(answer))
    }
    preferred = [
        index for index in available
        if _normalize_answer(str(bank[index][1])) not in excluded
    ]
    index = secrets.choice(preferred or available)
    return index, bank[index], len(seen) + 1


def _sentence_recent_answers(store: BotStore, group_id: str | int) -> set[str]:
    saved = store.get_group_state(
        _GLOBAL_STATE, f"sentence_guess_recent:{group_id}", [],
    )
    if not isinstance(saved, list):
        return set()
    return {
        normalized for answer in saved
        if (normalized := _normalize_answer(str(answer)))
    }


def _mark_sentence_recent(
    store: BotStore, group_id: str | int, answer: str,
) -> None:
    normalized = _normalize_answer(answer)
    if not normalized:
        return
    key = f"sentence_guess_recent:{group_id}"
    saved = store.get_group_state(_GLOBAL_STATE, key, [])
    saved_values = saved if isinstance(saved, list) else []
    recent = [
        value for item in saved_values
        if (value := _normalize_answer(str(item)))
    ]
    recent = [value for value in recent if value != normalized]
    recent.append(normalized)
    store.set_group_state(
        _GLOBAL_STATE, key, recent[-_SENTENCE_GUESS_RECENT_WINDOW:],
    )


def _scramble_sentence_words(sentence: str) -> list[str]:
    words = sentence.split()
    if len(set(words)) < 2:
        return words
    randomizer = secrets.SystemRandom()
    for _ in range(8):
        scrambled = randomizer.sample(words, len(words))
        if scrambled != words:
            return scrambled
    scrambled = words[:]
    different = next(index for index in range(1, len(words)) if words[index] != words[0])
    scrambled[0], scrambled[different] = scrambled[different], scrambled[0]
    return scrambled


def _global_owner_name(event: Any, actor_id: str | int) -> str:
    sender = getattr(event, "sender", None)
    username = str(getattr(sender, "username", None) or "").strip().lstrip("@")
    if username:
        return f"@{username}"
    name = " ".join(str(part).strip() for part in (
        getattr(sender, "first_name", None), getattr(sender, "last_name", None)
    ) if part and str(part).strip())
    return name or str(actor_id)


def _fa(value: Any) -> str:
    return str(value).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


def _session(store: BotStore, group_id: str | int, kind: str, user_id: str | int | None = None) -> dict[str, Any] | None:
    state = store.get_group_state(group_id, _key(kind, user_id))
    return state if isinstance(state, dict) and state.get("status", "pending") == "pending" else None


def _start_state(
    store: BotStore,
    group_id: str | int,
    kind: str,
    *,
    user_id: str | int | None,
    answer: str | None,
    prompt: str,
    seconds: int,
    data: dict[str, Any] | None = None,
    answers: tuple[str, ...] = (),
    deadline_pending: bool = False,
) -> dict[str, Any]:
    current = time.time()
    state = {
        "status": "pending", "kind": kind, "token": uuid.uuid4().hex,
        "answer": answer, "answers": list(answers), "prompt": prompt,
        "expires_at": current + seconds, "data": data or {},
    }
    if deadline_pending:
        state["expires_at"] = current + _SENTENCE_GUESS_PENDING_DELIVERY_SECONDS
        state["deadline_pending"] = True
        state["deadline_pending_since"] = current
    store.set_group_state(group_id, _key(kind, user_id), state)
    return state


def _group_busy(store: BotStore, group_id: str | int) -> bool:
    active_kinds: set[str] = set()
    for gid, _key_value, state in store.list_group_states_prefix("game_session:", limit=5000):
        if gid != str(group_id) or not isinstance(state, dict):
            continue
        if state.get("status", "pending") != "pending":
            continue
        kind = str(state.get("kind") or "")
        if kind:
            active_kinds.add(kind)
            if len(active_kinds) >= 2:
                return True
    return False


def _normalize_answer(value: str) -> str:
    arabic_letter_map = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ة": "ه", "ؤ": "و"})
    punctuation = str.maketrans("", "", " ،,.;:!?؟!ـ\"'«»")
    value = normalize(value).lower().translate(arabic_letter_map).translate(punctuation)
    return "".join(char for char in value if not char.isspace())


def _accepted(value: str, answers: tuple[str, ...] | list[str]) -> bool:
    normalized = _normalize_answer(value)
    return any(normalized == _normalize_answer(answer) for answer in answers)


def _correction_spacing_key(value: str) -> str:
    text = str(value or "").strip()
    for source, target in (
        ("\u200f", ""), ("\u200e", ""), ("\ufeff", ""),
        ("ي", "ی"), ("ك", "ک"),
    ):
        text = text.replace(source, target)
    text = " ".join(text.split())
    return text.replace("\u200c", "\x00")


def _correction_matches(text: str, wrong: str, correct: str) -> bool:
    wrong_letters = _normalize_answer(wrong)
    correct_letters = _normalize_answer(correct)
    if wrong_letters == correct_letters:
        return _correction_spacing_key(text) == _correction_spacing_key(correct)
    return _normalize_answer(text) == correct_letters


def _next_who_knows_round(
    store: BotStore,
    group_id: str | int,
    word_filter: WordFilter | None = None,
) -> tuple[str, str, tuple[str, ...]] | None:
    history_key = f"who_knows_letters:{group_id}"
    saved = store.get_group_state(_GLOBAL_STATE, history_key, {})
    seen = (
        {str(letter) for letter in saved.get("seen", [])}
        if isinstance(saved, dict) and isinstance(saved.get("seen"), list)
        else set()
    )
    rounds = WHO_KNOWS_ROUNDS
    if word_filter is not None:
        rounds = tuple(
            entry for entry in rounds
            if word_filter.match_global(entry[0]) is None
        )
    letters = {letter for letter, _category, _answers in rounds}
    remaining = sorted(letters - seen)
    if not remaining:
        store.delete_group_state(_GLOBAL_STATE, history_key)
        return None
    letter = secrets.choice(remaining)
    options = [entry for entry in rounds if entry[0] == letter]
    return secrets.choice(options)


def _remember_who_knows_letter(
    store: BotStore, group_id: str | int, letter: str,
) -> None:
    history_key = f"who_knows_letters:{group_id}"
    saved = store.get_group_state(_GLOBAL_STATE, history_key, {})
    seen = (
        {str(item) for item in saved.get("seen", [])}
        if isinstance(saved, dict) and isinstance(saved.get("seen"), list)
        else set()
    )
    seen.add(str(letter))
    store.set_group_state(_GLOBAL_STATE, history_key, {"seen": sorted(seen)})


async def _send(event: Any, reply: ReplyCallback | None, text: str) -> Any:
    if reply is not None:
        return await reply(event, text, formatting_entities=None)
    method = getattr(event, "reply", None) or getattr(event, "respond", None)
    if not callable(method):
        raise TypeError("event has no supported reply method")
    result = method(text)
    return await result if hasattr(result, "__await__") else result


def _send_succeeded(result: Any) -> bool:
    return not (
        result is None
        or result is False
        or isinstance(result, (int, str, list, tuple, set, dict)) and not result
    )


def _award(
    store: BotStore,
    group_id: str | int,
    actor_id: str | int,
    kind: str,
    token: str,
    event: Any,
    *,
    amount: int,
    coin_type: str = "bronze",
) -> bool:
    _profile, outcome = store.award_group_coins(
        group_id,
        actor_id,
        amount,
        coin_type,
        reference=f"game:{group_id}:{kind}:{token}:{actor_id}",
        display_name=_global_owner_name(event, actor_id),
        note=f"game:{kind}",
        return_outcome=True,
    )
    return outcome == "awarded"


def _reward_line(amount: int, coin_type: str = "bronze") -> str:
    label = {"bronze": "برنز", "silver": "نقره", "gold": "طلا"}[coin_type]
    return f"\n\n🪙 +{_fa(amount)} سکه {label}"


def _game_command(value: str) -> bool:
    return value in GAME_COMMANDS or value in EMOJI_RESET_COMMANDS


def _is_enabled(store: BotStore, group_id: str | int) -> bool:
    return bool(store.get_group_state(group_id, "entertainment_enabled", True))


async def _finish_correct(
    event: Any,
    store: BotStore,
    *,
    group_id: str | int,
    actor_id: str | int,
    kind: str,
    state_key: str,
    state: dict[str, Any],
    amount: int,
    coin_type: str = "bronze",
    reply: ReplyCallback | None,
    message: str = "🎉 پاسخ درست بود!",
) -> bool:
    awarded = _award(
        store, group_id, actor_id, kind, str(state["token"]), event,
        amount=amount, coin_type=coin_type,
    )
    store.delete_group_state(group_id, state_key)
    if awarded:
        response = message + _reward_line(amount, coin_type)
    else:
        response = (
            "🎉 پاسخ صحیح بود!\n\n"
            "🪙 جایزهٔ این دور قبلاً به حساب شما اضافه شده است."
        )
    await _send(event, reply, response)
    return True


def _user_active_kind(store: BotStore, group_id: str | int, actor_id: str | int) -> tuple[str, str, dict[str, Any]] | None:
    for kind in ("riddle", "maemma", "emoji", "fill_blank", "sentence_guess", "lucky_box", "minesweeper"):
        key = _key(kind, actor_id)
        state = store.get_group_state(group_id, key)
        if isinstance(state, dict) and state.get("status", "pending") == "pending":
            return kind, key, state
    return None


def has_active_answer_session(
    store: BotStore, group_id: str | int, user_id: str | int
) -> bool:
    """Return whether a live local game may reasonably accept this user's answer."""
    now = time.time()
    user_session = _user_active_kind(store, group_id, user_id)
    if user_session is not None and float(user_session[2].get("expires_at", 0)) > now:
        return True
    for kind in set(_GROUP_GAME_KINDS.values()) - {"sentence_guess", "minesweeper"}:
        state = _session(store, group_id, kind)
        if state is None or float(state.get("expires_at", 0)) <= now:
            continue
        data = state.get("data", {})
        if kind == "lucky_box" and (
            not isinstance(data, dict) or str(data.get("user_id", "")) != str(user_id)
        ):
            continue
        # Group answer games accept any member unless their own handler
        # applies a participant gate. This exemption is used only for the
        # generic burst detector; explicit promotional waves still apply.
        return True
    return False


async def _answer_user_game(
    event: Any,
    store: BotStore,
    *,
    group_id: str | int,
    actor_id: str | int,
    text: str,
    reply: ReplyCallback | None,
) -> bool:
    active = _user_active_kind(store, group_id, actor_id)
    if active is None:
        return False
    kind, state_key, state = active
    if float(state.get("expires_at", 0)) <= time.time():
        # Keep durable timeout state for games whose public result is staged
        # by the scheduler; otherwise a late answer could erase the notice.
        if kind in {"minesweeper", "sentence_guess"}:
            return kind == "sentence_guess"
        store.delete_group_state(group_id, state_key)
        return False
    data = state.get("data", {}) if isinstance(state.get("data"), dict) else {}
    if kind in {"riddle", "emoji", "fill_blank", "sentence_guess"}:
        accepted = state.get("answers", [])
        if not _accepted(text, accepted if isinstance(accepted, list) else []):
            return False
        if kind == "sentence_guess":
            progress_kind = str(data.get("progress_kind") or "")
            try:
                index = int(data.get("puzzle_index", -1))
            except (TypeError, ValueError):
                index = -1
            bank_size = len(SENTENCE_GUESS_PUZZLES if data.get("mode") == "guess" else SENTENCES)
            if progress_kind and 0 <= index < bank_size:
                progress_key = _seen_key(progress_kind, actor_id)
                previous = store.get_group_state(group_id, progress_key, [])
                seen = previous if isinstance(previous, list) else []
                if index not in {int(value) for value in seen if str(value).isdigit()}:
                    seen.append(index)
                    store.set_group_state(group_id, progress_key, seen)
        amount, coin = _GAME_REWARDS[kind]
        return await _finish_correct(
            event, store, group_id=group_id, actor_id=actor_id, kind=kind,
            state_key=state_key, state=state, amount=amount, coin_type=coin,
            reply=reply, message="🎉 پاسخ درست بود!",
        )
    if kind == "lucky_box":
        numeric = normalize(text).translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
        if not numeric.isdigit() or not 1 <= int(numeric) <= 9:
            return False
        store.delete_group_state(group_id, state_key)
        reward = int(data.get("reward", 0))
        coin = str(data.get("coin_type", "bronze"))
        if reward:
            awarded = _award(
                store, group_id, actor_id, kind, str(state["token"]), event,
                amount=reward, coin_type=coin,
            )
            if awarded:
                response = f"🎁 جعبهٔ {_fa(numeric)} را باز کردی!" + _reward_line(reward, coin)
            else:
                response = (
                    f"🎁 جعبهٔ {_fa(numeric)} را باز کردی!\n\n"
                    "🪙 جایزهٔ این دور قبلاً به حساب شما اضافه شده است."
                )
            await _send(event, reply, response)
        else:
            await _send(event, reply, f"🎁 جعبهٔ {_fa(numeric)} پوچ بود؛ دفعهٔ بعد شانس بیشتری داری.")
        return True
    if kind == "minesweeper":
        numeric = normalize(text).translate(_DIGITS)
        if not numeric.isdigit() or not 1 <= int(numeric) <= 9:
            return False
        safe_cell = int(data.get("safe_cell", 0))
        if not 1 <= safe_cell <= 9:
            store.delete_group_state(group_id, state_key)
            return False
        selected = int(numeric)
        quota_reference = f"{group_id}:{state['token']}:{actor_id}"
        remaining = _minesweeper_consume_chance(
            store, actor_id, reference=quota_reference,
        )
        store.delete_group_state(group_id, state_key)
        remaining_line = (
            f"🎟 شانس باقی‌ماندهٔ امروز: {_fa(remaining)} از "
            f"{_fa(_MINESWEEPER_DAILY_CHANCES)}"
        )
        if selected == safe_cell:
            awarded = _award(
                store, group_id, actor_id, kind, str(state["token"]), event,
                amount=_MINESWEEPER_REWARD, coin_type="silver",
            )
            reward_line = (
                _reward_line(_MINESWEEPER_REWARD, "silver")
                if awarded else "\n\n🪙 جایزهٔ این دور قبلاً به حساب شما اضافه شده است."
            )
            await _send(
                event, reply,
                f"✅ {_global_owner_name(event, actor_id)} جان سالم به در برد!\n\n"
                f"{_minesweeper_board(selected, won=True)}\n\n"
                f"خانهٔ {_fa(selected)} امن بود."
                f"{reward_line}\n{remaining_line}",
            )
        else:
            _profile, penalty = store.spend_group_coins(
                group_id, actor_id, _MINESWEEPER_PENALTY, "bronze",
                reference=(
                    f"minesweeper:penalty:{group_id}:"
                    f"{state['token']}:{actor_id}"
                ),
                display_name=_global_owner_name(event, actor_id),
                note="مین یاب",
            )
            penalty_line = (
                f"🪙 {_fa(_MINESWEEPER_PENALTY)} سکه برنز از شما کم شد"
                if penalty == "spent"
                else "🪙 جریمهٔ این دور قبلاً کسر شده بود."
                if penalty == "duplicate"
                else "موجودی کافی نبود؛ جریمه کسر نشد."
            )
            await _send(
                event, reply,
                "🧨 حدس اشتباه بود، مین منفجر شد!\n\n"
                f"{_minesweeper_board(selected)}\n\n"
                f"💣 مین در خانهٔ {_fa(selected)} بود.\n{penalty_line}\n"
                f"{remaining_line}",
            )
        return True
    return False


async def _run_laugh_countdown(
    event: Any,
    store: BotStore,
    *,
    group_id: str | int,
    token: str,
    reply: ReplyCallback | None,
    countdown_seconds: int | None = None,
    interval_seconds: float | None = None,
) -> None:
    group_key = str(group_id)
    state_key = _key("laugh_or_lose")
    steps = _LAUGH_COUNTDOWN_SECONDS if countdown_seconds is None else countdown_seconds
    interval = (
        _LAUGH_COUNTDOWN_INTERVAL_SECONDS
        if interval_seconds is None else interval_seconds
    )
    try:
        for remaining in range(steps, 0, -1):
            state = store.get_group_state(group_id, state_key)
            if not isinstance(state, dict) or state.get("token") != token:
                return
            data = state.get("data", {}) if isinstance(state.get("data"), dict) else {}
            if data.get("phase") != "countdown":
                return
            await _send(event, reply, f"{_fa(remaining)}...")
            if interval > 0:
                await asyncio.sleep(interval)
        state = store.get_group_state(group_id, state_key)
        if not isinstance(state, dict) or state.get("token") != token:
            return
        data = state.get("data", {}) if isinstance(state.get("data"), dict) else {}
        if data.get("phase") != "countdown":
            return
        data["phase"] = "open"
        state["data"] = data
        state["expires_at"] = time.time() + _LAUGH_ROUND_SECONDS
        store.set_group_state(group_id, state_key, state)
        await _send(
            event, reply,
            "😂 حالا بخند!\n\n"
            "اولین نفری که یکی از این ایموجی‌ها را بفرستد برنده است:\n"
            "😂 🤣 😆 😹 😄 😁",
        )
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - durable timeout remains the fallback
        # A failed countdown send leaves the durable timeout as the fallback.
        return
    finally:
        current = asyncio.current_task()
        if _LAUGH_TASKS.get(group_key) is current:
            _LAUGH_TASKS.pop(group_key, None)


async def _answer_group_game(
    event: Any,
    store: BotStore,
    *,
    group_id: str | int,
    actor_id: str | int,
    text: str,
    raw_text: str,
    reply: ReplyCallback | None,
    private_message_sender: PrivateMessageCallback | None,
    word_filter: WordFilter | None,
    name_family_service: Any | None,
) -> bool:
    for kind in ("laugh_or_lose", "survival", "lucky_box", "vampire", "best_answer", "battle", "karagah", "flag", "correction", "quiz", "who_knows", "truth_lie", "name_family"):
        state_key = _key(kind)
        state = store.get_group_state(group_id, state_key)
        if not isinstance(state, dict) or state.get("status", "pending") != "pending":
            continue
        if float(state.get("expires_at", 0)) <= time.time():
            if kind not in {"minesweeper", "survival", "battle", "vampire", "karagah"}:
                store.delete_group_state(group_id, state_key)
            continue
        data = state.get("data", {}) if isinstance(state.get("data"), dict) else {}
        if kind == "flag":
            if _accepted(text, state.get("answers", [])):
                return await _finish_correct(event, store, group_id=group_id, actor_id=actor_id, kind=kind, state_key=state_key, state=state, amount=_GAME_REWARDS[kind][0], reply=reply)
            continue
        if kind == "correction":
            correct = state.get("answers", [""])
            correct = str(correct[0]) if correct else ""
            wrong = str(data.get("wrong", ""))
            if _correction_matches(raw_text, wrong, correct):
                return await _finish_correct(event, store, group_id=group_id, actor_id=actor_id, kind=kind, state_key=state_key, state=state, amount=_GAME_REWARDS[kind][0], reply=reply)
            continue
        if kind == "quiz":
            numeric = normalize(text).translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
            options = list(state.get("answers", []))
            correct = int(data.get("correct", 0))
            selected = int(numeric) if numeric.isdigit() and 1 <= int(numeric) <= len(options) else None
            if selected is None:
                selected = next((index for index, option in enumerate(options, 1) if _accepted(text, [str(option)])), None)
            if selected is None:
                continue
            if selected == correct:
                return await _finish_correct(event, store, group_id=group_id, actor_id=actor_id, kind=kind, state_key=state_key, state=state, amount=_GAME_REWARDS[kind][0], reply=reply)
            store.delete_group_state(group_id, state_key)
            correct_answer = options[correct - 1] if 1 <= correct <= len(options) else "نامشخص"
            await _send(event, reply, f"❌ پاسخ درست گزینهٔ {_fa(correct)} بود: {correct_answer}")
            return True
        if kind == "who_knows":
            letter = _normalize_who_knows(str(data.get("letter", "")))
            candidate = _normalize_who_knows(text)
            accepted = data.get("accepted_answers", [])
            accepted = accepted if isinstance(accepted, list) else []
            accepted_normalized = {
                _normalize_who_knows(str(answer)) for answer in accepted
            }
            if candidate.startswith(letter) and candidate in accepted_normalized:
                return await _finish_correct(
                    event, store, group_id=group_id, actor_id=actor_id,
                    kind=kind, state_key=state_key, state=state,
                    amount=_GAME_REWARDS[kind][0], reply=reply,
                    message="🧠 جواب قابل قبول بود!",
                )
            continue
        if kind == "lucky_box":
            if str(data.get("user_id", "")) != str(actor_id):
                continue
            numeric = normalize(text).translate(_DIGITS)
            if not numeric.isdigit() or not 1 <= int(numeric) <= 9:
                continue
            boxes = data.get("boxes", {})
            prize = int(boxes.get(str(int(numeric)), 0)) if isinstance(boxes, dict) else 0
            store.delete_group_state(group_id, state_key)
            if prize > 0:
                awarded = _award(
                    store, group_id, actor_id, kind, str(state["token"]), event,
                    amount=prize,
                )
                if awarded:
                    response = (
                        f"🎁 جعبهٔ {_fa(numeric)} باز شد!\n\n"
                        f"🎉 جایزه: {_fa(prize)} سکه"
                        + _reward_line(prize)
                    )
                else:
                    response = (
                        f"🎁 جعبهٔ {_fa(numeric)} باز شد!\n\n"
                        "🪙 جایزهٔ این دور قبلاً به حساب شما اضافه شده است."
                    )
                await _send(event, reply, response)
            else:
                await _send(event, reply, f"🎁 جعبهٔ {_fa(numeric)} باز شد!\n\n😔 پوچ بود.")
            return True
        if kind == "truth_lie":
            guess = _parse_truth_lie_guess(raw_text)
            said_truth = guess is True
            said_lie = guess is False
            if not (said_truth or said_lie):
                continue
            actual = bool(data.get("truth"))
            store.delete_group_state(group_id, state_key)
            if said_truth == actual:
                awarded = _award(
                    store, group_id, actor_id, kind, str(state["token"]), event,
                    amount=_GAME_REWARDS[kind][0],
                )
                response = f"✅ درست گفتی! پاسخ: {'حقیقت' if actual else 'دروغ'}"
                if awarded:
                    response += _reward_line(_GAME_REWARDS[kind][0])
                else:
                    response += "\n\n🪙 جایزهٔ این دور قبلاً به حساب شما اضافه شده است."
                await _send(event, reply, response)
            else:
                await _send(event, reply, f"❌ اشتباه بود. پاسخ درست: {'حقیقت' if actual else 'دروغ'}")
            return True
        if kind == "laugh_or_lose":
            if data.get("phase") != "open":
                continue
            if any(mark in text for mark in _LAUGH_EMOJIS):
                winner = _global_owner_name(event, actor_id)
                return await _finish_correct(
                    event, store, group_id=group_id, actor_id=actor_id,
                    kind=kind, state_key=state_key, state=state,
                    amount=_GAME_REWARDS[kind][0], reply=reply,
                    message=f"🏆 برنده: {winner}",
                )
            continue
        if kind == "name_family":
            if normalize(text) in {"اسم فامیل", "شروع", "شروع مجدد"}:
                continue
            category_answers = parse_name_family_submission(str(raw_text or ""))
            if category_answers is None:
                continue
            submissions = dict(data.get("submissions") or {})
            if str(actor_id) in submissions:
                await _send(event, reply, "ℹ️ پاسخ شما قبلاً ثبت شده است.")
                return True
            letter = str(data.get("letter", ""))
            seen_answers = {
                normalize_name_family_answer(str(answer))
                for submission in submissions.values()
                if isinstance(submission, dict)
                for answer in submission.get("answers", [])
                if isinstance(answer, str)
            }
            valid_answers: list[str] = []
            for category in NAME_FAMILY_CATEGORIES:
                answer = str(category_answers.get(category, "")).strip()
                normalized_answer = normalize_name_family_answer(answer)
                if not answer or not normalized_answer or normalized_answer in seen_answers:
                    continue
                if name_family_service is not None:
                    valid = await name_family_service.is_valid(
                        store,
                        category=category,
                        letter=letter,
                        answer=answer,
                        group_id=group_id,
                        user_id=actor_id,
                    )
                elif word_filter is not None:
                    valid = is_static_name_family_answer(
                        category=category,
                        letter=letter,
                        answer=answer,
                        word_filter=word_filter,
                    )
                else:
                    valid = False
                if valid:
                    valid_answers.append(normalized_answer)
                    seen_answers.add(normalized_answer)
            if len(valid_answers) < 5:
                await _send(
                    event,
                    reply,
                    f"❌ دست‌کم ۵ پاسخ معتبر و یکتا با حرف «{letter}» لازم است؛ دوباره ۷ خط بفرستید.",
                )
                return True
            score = len(valid_answers) * 10
            submissions[str(actor_id)] = {
                "name": _global_owner_name(event, actor_id),
                "score": score,
                "answers": valid_answers,
            }
            data["submissions"] = submissions
            state["data"] = data
            store.set_group_state(group_id, state_key, state)
            await _send(event, reply, f"✅ پاسخ ثبت شد؛ {_fa(score)} امتیاز فعلی.")
            return True
        if kind == "best_answer":
            if len(normalize(text)) < 4:
                continue
            reply_to = getattr(event, "reply_to", None)
            reply_to_id = getattr(reply_to, "reply_to_msg_id", None)
            question_message_id = data.get("question_message_id")
            if question_message_id is None or str(reply_to_id) != str(question_message_id):
                return False
            submissions = dict(data.get("submissions") or {})
            if str(actor_id) in submissions:
                return True
            submissions[str(actor_id)] = {
                "name": _global_owner_name(event, actor_id),
                "text": normalize(text)[:600],
                "at": time.time(),
            }
            data["submissions"] = submissions
            state["data"] = data
            store.set_group_state(group_id, state_key, state)
            await _send(event, reply, "✅ پاسخ شما ثبت شد؛ نتیجه پس از پایان زمان اعلام می‌شود.")
            return True
        if kind == "battle":
            phase = str(state.get("phase") or data.get("phase") or "joining")
            actor_key = str(actor_id)
            players = data.get("players", [])
            players = players if isinstance(players, list) else []
            player_ids = {
                str(player.get("user_id"))
                for player in players if isinstance(player, dict)
            }
            if phase == "joining":
                if normalize(text) != "شرکت":
                    continue
                host = str(data.get("host") or "")
                if actor_key == host:
                    await _send(event, reply, "⚠️ شما بازیکن اول هستید؛ نمی‌توانید دوباره وارد شوید.")
                    return True
                if data.get("opponent") is not None:
                    await _send(event, reply, "⚠️ نبرد همین حالا پر است.")
                    return True
                opponent = {
                    "user_id": actor_key,
                    "name": _global_owner_name(event, actor_id),
                }
                host_player = next(
                    (player for player in players if str(player.get("user_id")) == host),
                    {"user_id": host, "name": str(data.get("host_name") or "بازیکن اول")},
                )
                data["opponent"] = actor_key
                data["players"] = [host_player, opponent]
                data["scores"] = {host: 0, actor_key: 0}
                data["questions"] = _battle_pick_questions(store, group_id)
                data["index"] = 0
                data["phase"] = "playing"
                prompt = _battle_set_question(state, data, current=time.time())
                store.set_group_state(group_id, state_key, state)
                await _send(event, reply, "⚔️ بازیکن دوم پیوست! نبرد شروع می‌شود...")
                if prompt:
                    delivered = await _send(event, reply, prompt)
                    if _send_succeeded(delivered):
                        refresh_timed_game_deadline_after_delivery(
                            store, group_id, state_key,
                            token=str(state.get("token") or ""),
                            phase="playing", stage=0, expected_prompt=prompt,
                        )
                return True
            if phase != "playing" or actor_key not in player_ids:
                if phase == "playing" and normalize(text) == "شرکت":
                    await _send(event, reply, "⚠️ نبرد همین حالا در جریان است؛ نمی‌توانید وارد شوید.")
                    return True
                continue
            index = int(data.get("index", 0) or 0)
            questions = data.get("questions", [])
            if not isinstance(questions, list) or not 0 <= index < len(questions):
                continue
            assigned = players[index % 2]
            if not isinstance(assigned, dict):
                continue
            if str(assigned.get("user_id")) != actor_key:
                return True
            question = questions[index]
            if not isinstance(question, dict):
                continue
            accepted_answers = question.get("accepted_answers", [])
            accepted_answers = accepted_answers if isinstance(accepted_answers, list) else []
            correct = _battle_answer_matches(text, accepted_answers)
            if correct:
                scores = data.get("scores", {})
                scores = scores if isinstance(scores, dict) else {}
                scores[actor_key] = int(scores.get(actor_key, 0) or 0) + 1
                data["scores"] = scores
                response = "✅ پاسخ درست بود!"
            else:
                response = "❌ پاسخ اشتباه بود!"
            data["index"] = index + 1
            if int(data["index"]) >= _BATTLE_QUESTIONS_TOTAL:
                result = _battle_finish_notice(store, group_id, state, data)
                state["data"] = data
                state["notice_text"] = result[:2500]
                store.set_group_state(group_id, state_key, state)
                await _send(event, reply, response)
                await _send(event, reply, result)
                store.delete_group_state(group_id, state_key)
                return True
            prompt = _battle_set_question(state, data, current=time.time())
            store.set_group_state(group_id, state_key, state)
            await _send(event, reply, response)
            if prompt:
                delivered = await _send(event, reply, prompt)
                if _send_succeeded(delivered):
                    refresh_timed_game_deadline_after_delivery(
                        store, group_id, state_key,
                        token=str(state.get("token") or ""),
                        phase="playing", stage=int(data["index"]),
                        expected_prompt=prompt,
                    )
            return True
        if kind == "karagah":
            phase = str(state.get("phase") or data.get("phase") or "joining")
            players = data.get("players", [])
            players = players if isinstance(players, list) else []
            actor_key = str(actor_id)
            player_ids = {
                str(player.get("user_id"))
                for player in players if isinstance(player, dict)
            }
            if phase == "joining":
                if normalize(text) != "شرکت":
                    continue
                if actor_key in player_ids:
                    await _send(event, reply, "⚠️ شما قبلاً ثبت‌نام کرده‌اید.")
                    return True
                if len(players) >= _KARAGAH_MAX_PLAYERS:
                    await _send(event, reply, "⚠️ ظرفیت تکمیل است.")
                    return True
                player, access_hash = _secret_player_info(event, actor_id)
                players.append(player)
                data["players"] = players
                state["data"] = data
                store.set_group_state(group_id, state_key, state)
                if access_hash is not None:
                    _SECRET_ROLE_ACCESS_HASHES[
                        _private_role_peer_key(group_id, "karagah", actor_id)
                    ] = access_hash
                await _send(
                    event, reply,
                    f"✅ ثبت شد ({_fa(len(players))}/{_fa(_KARAGAH_MAX_PLAYERS)})",
                )
                if len(players) >= _KARAGAH_MAX_PLAYERS:
                    join_event = _KARAGAH_JOIN_EVENTS.get(str(group_id))
                    if join_event is not None:
                        join_event.set()
                return True
            if phase == "thief_guess":
                if actor_key not in player_ids:
                    continue
                thief_id = str(data.get("thief_id") or "")
                if actor_key == thief_id:
                    return True
                guessed = data.get("guessed", [])
                guessed_set = (
                    {str(item) for item in guessed}
                    if isinstance(guessed, list) else set()
                )
                if actor_key in guessed_set:
                    return True
                numeric = normalize(text).translate(_DIGITS)
                if not numeric.isdigit():
                    continue
                number = int(numeric)
                if not 1 <= number <= len(players):
                    continue
                guesser = next(
                    (player for player in players
                     if isinstance(player, dict)
                     and str(player.get("user_id")) == actor_key),
                    None,
                )
                target = players[number - 1]
                if not isinstance(guesser, dict) or not isinstance(target, dict):
                    continue
                if str(target.get("user_id")) == actor_key:
                    await _send(event, reply, "⚠️ نمی‌توانید خودتان را انتخاب کنید.")
                    return True
                guessed_set.add(actor_key)
                data["guessed"] = sorted(guessed_set)
                if str(target.get("user_id")) != thief_id:
                    state["data"] = data
                    store.set_group_state(group_id, state_key, state)
                    await _send(
                        event, reply,
                        f"❌ اشتباه بود، {guesser.get('name', 'کاربر')}!",
                    )
                    return True

                data["phase"] = "object_guess"
                data["finder_id"] = actor_key
                data["object_guessed"] = False
                state["phase"] = "object_guess"
                state["data"] = data
                state["expires_at"] = time.time() + _KARAGAH_OBJECT_GUESS_SECONDS
                options = data.get("options", [])
                options = options if isinstance(options, list) else []
                option_lines = "\n".join(
                    f"{index}) {option}"
                    for index, option in enumerate(options, 1)
                )
                state["prompt"] = option_lines
                store.set_group_state(group_id, state_key, state)
                notice = (
                    f"🎯 آفرین {guesser.get('name', 'کاربر')}! دزد را پیدا کردی!"
                    f"\n\n🕵️ دزد: {target.get('name', 'کاربر')}"
                    "\n\nحالا حدس بزن چه چیزی دزدیده شده؟"
                    f"\n\n{option_lines}"
                    "\n\nشماره گزینه را بفرست"
                    f"\n⏳ {_fa(_KARAGAH_OBJECT_GUESS_SECONDS)} ثانیه فرصت داری"
                )
                await _send(event, reply, notice)
                return True
            if phase == "object_guess":
                if actor_key != str(data.get("finder_id") or ""):
                    continue
                options = data.get("options", [])
                options = options if isinstance(options, list) else []
                numeric = normalize(text).translate(_DIGITS)
                chosen = None
                if numeric.isdigit() and 1 <= int(numeric) <= len(options):
                    chosen = str(options[int(numeric) - 1])
                else:
                    normalized = _normalize_answer(text)
                    chosen = next(
                        (str(option) for option in options
                         if _normalize_answer(str(option)) == normalized),
                        None,
                    )
                if chosen is None:
                    continue
                is_correct = _normalize_answer(chosen) == _normalize_answer(
                    str(data.get("stolen_object") or "")
                )
                notice = _karagah_stage_resolution(
                    store, group_id, state_key, state, data,
                    result="solved" if is_correct else "thief_wins",
                    reason="solved" if is_correct else "object_wrong",
                    finder_id=actor_key,
                )
                await _send(event, reply, notice)
                store.delete_group_state(group_id, state_key)
                return True
            continue
        if kind == "survival":
            players = data.get("players", {})
            if not isinstance(players, dict):
                players = {}
            order = data.get("order", [])
            if not isinstance(order, list):
                order = []
            phase = str(state.get("phase") or data.get("phase") or "joining")
            if phase == "joining":
                if normalize(text) != "شرکت":
                    continue
                actor_key = str(actor_id)
                if actor_key in players:
                    await _send(event, reply, "⚠️ شما قبلاً ثبت‌نام کرده‌اید.")
                    return True
                if len(players) >= _SURVIVAL_MAX_PLAYERS:
                    await _send(event, reply, "⚠️ ظرفیت تکمیل است.")
                    return True
                players[actor_key] = {
                    "user_id": actor_key,
                    "name": _global_owner_name(event, actor_id),
                    "alive": True,
                    "round_coins": 0,
                }
                order.append(actor_key)
                data["players"] = players
                data["order"] = order
                state["data"] = data
                if len(players) >= _SURVIVAL_MAX_PLAYERS:
                    roster = "\n".join(
                        f"{_fa(index)}. {players[user_id]['name']}"
                        for index, user_id in enumerate(order, 1)
                    )
                    prompt = _survival_start_round(
                        store, group_id, state, data, current=time.time()
                    )
                    store.set_group_state(group_id, state_key, state)
                    # Preserve the reference's three ordered, separate outputs:
                    # join receipt, roster, then the first question.
                    await _send(
                        event, reply,
                        f"✅ ثبت شد ({_fa(len(players))}/"
                        f"{_fa(_SURVIVAL_MAX_PLAYERS)})",
                    )
                    await _send(event, reply, f"🏕 بازی شروع شد!\n\n{roster}")
                    delivered = await _send(event, reply, prompt)
                    if _send_succeeded(delivered):
                        refresh_timed_game_deadline_after_delivery(
                            store, group_id, state_key,
                            token=str(state.get("token") or ""),
                            phase="playing", stage=int(data.get("round", 0) or 0),
                            expected_prompt=prompt,
                        )
                    return True
                confirmation = (
                    f"✅ ثبت شد ({_fa(len(players))}/"
                    f"{_fa(_SURVIVAL_MAX_PLAYERS)})"
                )
                if len(players) < _SURVIVAL_MIN_PLAYERS:
                    confirmation += (
                        "\n\n⏳ در انتظار بازیکنان...\n\n"
                        f"ثبت‌نام شده: {_fa(len(players))} از حداقل "
                        f"{_fa(_SURVIVAL_MIN_PLAYERS)} نفر\n"
                        "برای شرکت بنویسید: شرکت"
                    )
                store.set_group_state(group_id, state_key, state)
                await _send(event, reply, confirmation)
                return True
            if phase != "playing":
                continue
            actor_key = str(actor_id)
            player = players.get(actor_key)
            if not isinstance(player, dict) or not player.get("alive"):
                continue
            answered = data.get("answered", [])
            if not isinstance(answered, list):
                answered = []
            if actor_key in answered:
                return True
            accepted_answers = data.get("accepted_answers", [])
            accepted_answers = (
                accepted_answers if isinstance(accepted_answers, list) else []
            )
            is_correct = _accepted(text, [str(item) for item in accepted_answers])
            answered.append(actor_key)
            data["answered"] = answered
            round_number = int(data.get("round", 1) or 1)
            if is_correct:
                _profile, award_outcome = store.award_group_coins(
                    group_id, actor_key, _SURVIVAL_STEP_REWARD, "silver",
                    reference=(
                        f"survival_round:{group_id}:{state['token']}:"
                        f"{round_number}:{actor_key}"
                    ),
                    display_name=str(player.get("name") or ""),
                    note="game:survival_step", return_outcome=True,
                )
                player["round_coins"] = int(player.get("round_coins", 0) or 0) + _SURVIVAL_STEP_REWARD
                response = "✅ درست بود!"
                response += (
                    _reward_line(_SURVIVAL_STEP_REWARD, "silver")
                    if award_outcome == "awarded"
                    else "\n\n🪙 جایزهٔ این مرحله قبلاً به حساب شما اضافه شده است."
                )
            else:
                player["alive"] = False
                response = "❌ پاسخ اشتباه — حذف شدید."
            data["players"] = players
            state["data"] = data
            store.set_group_state(group_id, state_key, state)
            await _send(event, reply, response)
            return True
        if kind == "vampire":
            phase = str(state.get("phase") or data.get("phase") or "joining")
            players = data.get("players", [])
            players = players if isinstance(players, list) else []
            actor_key = str(actor_id)
            player_ids = {
                str(player.get("user_id"))
                for player in players if isinstance(player, dict)
            }
            if phase == "joining":
                if normalize(text) != "شرکت":
                    continue
                if actor_key in player_ids:
                    await _send(event, reply, "⚠️ شما قبلاً ثبت‌نام کرده‌اید.")
                    return True
                if len(players) >= _VAMPIRE_MAX_PLAYERS:
                    await _send(event, reply, "⚠️ ظرفیت تکمیل است.")
                    return True
                player, access_hash = _secret_player_info(event, actor_id)
                players.append(player)
                data["players"] = players
                state["data"] = data
                store.set_group_state(group_id, state_key, state)
                if access_hash is not None:
                    _SECRET_ROLE_ACCESS_HASHES[
                        _private_role_peer_key(group_id, "vampire", actor_id)
                    ] = access_hash
                await _send(
                    event, reply,
                    f"✅ ثبت شد ({_fa(len(players))}/{_fa(_VAMPIRE_MAX_PLAYERS)})",
                )
                if len(players) >= _VAMPIRE_MAX_PLAYERS:
                    join_event = _VAMPIRE_JOIN_EVENTS.get(str(group_id))
                    if join_event is not None:
                        join_event.set()
                return True
            if phase != "guessing" or actor_key not in player_ids:
                continue
            vampire_id = str(data.get("vampire_id") or "")
            if actor_key == vampire_id:
                return True
            guessed = data.get("guessed", [])
            guessed_set = {str(item) for item in guessed} if isinstance(guessed, list) else set()
            if actor_key in guessed_set:
                return True
            numeric = normalize(text).translate(_DIGITS)
            if not numeric.isdigit():
                continue
            number = int(numeric)
            if not 1 <= number <= len(players):
                continue
            guesser = next(
                (player for player in players
                 if isinstance(player, dict) and str(player.get("user_id")) == actor_key),
                None,
            )
            target = players[number - 1]
            if not isinstance(guesser, dict) or not isinstance(target, dict):
                continue
            if str(target.get("user_id")) == actor_key:
                await _send(event, reply, "⚠️ نمی‌توانید خودتان را انتخاب کنید.")
                return True
            guessed_set.add(actor_key)
            data["guessed"] = sorted(guessed_set)
            if str(target.get("user_id")) != vampire_id:
                state["data"] = data
                store.set_group_state(group_id, state_key, state)
                await _send(
                    event, reply,
                    f"❌ اشتباه بود، {guesser.get('name', 'کاربر')}!",
                )
                return True

            last_key = f"vampire_last_user:{group_id}"
            store.set_group_state(_GLOBAL_STATE, last_key, vampire_id)
            _profile, award_outcome = store.award_group_coins(
                group_id, actor_key, _VAMPIRE_REWARD, "silver",
                reference=f"vampire:{group_id}:{state['token']}:winner",
                display_name=str(guesser.get("name") or ""),
                note="game:vampire", return_outcome=True,
            )
            tag = str(target.get("tag") or "")
            suffix = f" ({tag})" if tag and tag != str(target.get("name") or "") else ""
            reward_line = (
                f"\n\n🪙 +{_fa(_VAMPIRE_REWARD)} سکه نقره"
                if award_outcome == "awarded"
                else "\n\n🪙 جایزهٔ این دور قبلاً به حساب شما اضافه شده است."
            )
            notice = (
                f"🎯 درست حدس زدی، {guesser.get('name', 'کاربر')}!"
                f"\n\n🧛 خون‌آشام: {target.get('name', 'کاربر')}{suffix}"
                f"{reward_line}"
            )
            store.delete_group_state(group_id, state_key)
            await _send(event, reply, notice)
            return True
    return False


def _lucky_box_recent_plays(store: BotStore, actor_id: str | int, now: float | None = None) -> list[float]:
    current = time.time() if now is None else float(now)
    key = f"lucky_box_plays:{actor_id}"
    saved = store.get_group_state(_GLOBAL_STATE, key, [])
    stamps: list[float] = []
    if isinstance(saved, list):
        for stamp in saved:
            try:
                value = float(stamp)
            except (TypeError, ValueError):
                continue
            if 0 <= current - value < 24 * 60 * 60:
                stamps.append(value)
    stamps.sort()
    store.set_group_state(_GLOBAL_STATE, key, stamps)
    return stamps


def _lucky_box_wait_message(stamps: list[float], now: float) -> str:
    seconds = max(0, int(24 * 60 * 60 - (now - stamps[-2])))
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours and minutes:
        wait = f"{_fa(hours)} ساعت و {_fa(minutes)} دقیقه دیگر"
    elif hours:
        wait = f"{_fa(hours)} ساعت دیگر"
    elif minutes:
        wait = f"{_fa(minutes)} دقیقه دیگر"
    else:
        wait = "کمتر از یک دقیقه دیگر"
    return f"⏳ سهمیه امروز شما تمام شده است.\n\nبازی بعدی:\n{wait}"


def _make_group_game(
    store: BotStore, group_id: str | int, kind: str, prompt: str, answer: str | None,
    answers: tuple[str, ...] | list[str] = (), seconds: int = 45, data: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    if _session(store, group_id, kind) is not None:
        return None, "⚠️ همین بازی در این گروه فعال است."
    if _group_busy(store, group_id):
        return None, _BUSY_MESSAGE
    state = _start_state(
        store, group_id, kind, user_id=None, answer=answer, prompt=prompt,
        seconds=seconds, data=data, answers=answers,
    )
    return state, None


async def handle_game_command(
    event: Any,
    store: BotStore,
    *,
    group_id: str | int,
    actor_id: str | int,
    text: str,
    reply: ReplyCallback | None = None,
    private_message_sender: PrivateMessageCallback | None = None,
    word_filter: WordFilter | None = None,
    name_family_service: Any | None = None,
) -> bool:
    value = normalize(text)
    if not value:
        return False
    if value in EMOJI_RESET_COMMANDS:
        store.delete_group_state(group_id, _key("emoji", actor_id))
        store.delete_group_state(group_id, _seen_key("emoji", actor_id))
        await _send(event, reply, "🔄 پیشرفت حدس ایموجی شما در این گروه پاک شد.\n\nبا «حدس ایموجی» از مرحلهٔ ۱ شروع کنید.")
        return True

    if await _answer_user_game(event, store, group_id=group_id, actor_id=actor_id, text=value, reply=reply):
        return True
    if await _answer_group_game(
        event, store, group_id=group_id, actor_id=actor_id, text=value,
        raw_text=text, reply=reply,
        private_message_sender=private_message_sender,
        word_filter=word_filter,
        name_family_service=name_family_service,
    ):
        return True

    if not _game_command(value):
        return False
    if not _is_enabled(store, group_id):
        await _send(event, reply, _BLOCKED_NOTICE)
        return True

    group_kind = _GROUP_GAME_KINDS.get(value)
    if group_kind:
        pending_final = store.get_group_state(group_id, _key(group_kind))
        if (
            isinstance(pending_final, dict)
            and pending_final.get("status") == "timed_out"
            and pending_final.get("notice_text")
        ):
            await _send(
                event, reply,
                "⌛ پایان دور قبلی هنوز اعلام نشده است؛ کمی بعد دوباره تلاش کنید.",
            )
            return True
    if group_kind and _session(store, group_id, group_kind) is not None:
        await _send(event, reply, "⚠️ یک دور از این بازی همین حالا در گروه در جریان است.")
        return True
    if group_kind and _group_busy(store, group_id):
        await _send(event, reply, _BUSY_MESSAGE)
        return True

    if value in {"جرعت", "جرات", "جرئت"}:
        _index, dare, _used = _choose(store, group_id, "dare", actor_id, DARES)
        await _send(event, reply, f"🎯 جرعت:\n{dare}")
        return True
    if value in {"حقیقت", "حقیقت بگو"}:
        _index, truth, _used = _choose(store, group_id, "truth", actor_id, TRUTHS)
        await _send(event, reply, f"🧠 حقیقت:\n{truth}")
        return True
    if value == "جک":
        _index, joke, _used = _choose(store, group_id, "joke", actor_id, JOKES)
        await _send(event, reply, joke)
        return True

    if value == "چیستان" or value == "معما":
        kind = "riddle" if value == "چیستان" else "maemma"
        if _session(store, group_id, kind, actor_id):
            await _send(event, reply, "⏳ شما یک معمای باز دارید؛ اول همان را پاسخ دهید.")
            return True
        bank = RIDDLES if kind == "riddle" else MAEMMA_PUZZLES
        _index, (question, answers), _used = _choose(store, group_id, kind, actor_id, bank)
        state = _start_state(store, group_id, kind, user_id=actor_id, answer=None, prompt=question, seconds=50, answers=answers)
        await _send(event, reply, f"🧩 {'چیستان' if kind == 'riddle' else 'معما'}:\n\n{question}\n\n⏳ {_fa(50)} ثانیه فرصت دارید")
        return True
    if value == "حدس ایموجی":
        if _session(store, group_id, "emoji", actor_id):
            await _send(event, reply, "⏳ شما یک معمای باز دارید؛ اول همان را پاسخ دهید.")
            return True
        index, (emoji, answers), _used = _choose(store, group_id, "emoji", actor_id, EMOJI_PUZZLES)
        state = _start_state(store, group_id, "emoji", user_id=actor_id, answer=None, prompt=emoji, seconds=30, answers=answers, data={"stage": index + 1, "total": len(EMOJI_PUZZLES)})
        await _send(event, reply, f"🎮 حدس ایموجی\n\nمرحلهٔ {_fa(index + 1)} از {_fa(len(EMOJI_PUZZLES))}\n\n{emoji}\n\n⏳ {_fa(30)} ثانیه فرصت دارید")
        return True
    if value == "جای خالی":
        if _session(store, group_id, "fill_blank", actor_id):
            await _send(event, reply, "⏳ شما یک جای خالی باز دارید؛ اول همان را پاسخ دهید.")
            return True
        _index, (question, answers), _used = _choose(store, group_id, "fill_blank", actor_id, FILL_BLANKS)
        _start_state(store, group_id, "fill_blank", user_id=actor_id, answer=None, prompt=question, seconds=60, answers=answers)
        await _send(event, reply, f"📝 جای خالی:\n\n{question}\n\n⏳ {_fa(60)} ثانیه فرصت دارید")
        return True
    if value in {"حدس جمله", "ساخت جمله"}:
        if _session(store, group_id, "sentence_guess", actor_id):
            await _send(event, reply, "⏳ شما یک جملهٔ باز دارید؛ اول همان را پاسخ دهید.")
            return True
        mode = "guess" if value == "حدس جمله" else "build"
        progress_kind = f"sentence_guess_{mode}"
        recent_answers = _sentence_recent_answers(store, group_id)
        if mode == "guess":
            index, (clue, answer), number = _choose_unanswered(
                store, group_id, progress_kind, actor_id, SENTENCE_GUESS_PUZZLES,
                exclude_answers=recent_answers,
            )
            prompt = (
                f"🧩 حدس جمله — سوال {_fa(number)} از "
                f"{_fa(len(SENTENCE_GUESS_PUZZLES))}:\n\n"
                f"{clue}\n\n⏳ {_fa(_SENTENCE_GUESS_ANSWER_SECONDS)} ثانیه فرصت دارید"
            )
            answer_value = answer
            answers = (answer,)
        else:
            index, sentence, number = _choose_unanswered(
                store, group_id, progress_kind, actor_id, SENTENCES,
                exclude_answers=recent_answers,
            )
            scrambled = _scramble_sentence_words(sentence)
            prompt = (
                f"✏️ ساخت جمله — سوال {_fa(number)} از {_fa(len(SENTENCES))}:\n\n"
                f"کلمه‌ها را مرتب کنید و جملهٔ درست را بسازید:\n\n"
                f"{' / '.join(scrambled)}\n\n"
                f"⏳ {_fa(_SENTENCE_GUESS_ANSWER_SECONDS)} ثانیه فرصت دارید"
            )
            answer_value = sentence
            answers = (sentence,)
        state = _start_state(
            store, group_id, "sentence_guess", user_id=actor_id,
            answer=None, prompt=prompt, seconds=_SENTENCE_GUESS_ANSWER_SECONDS,
            answers=answers, deadline_pending=True,
            data={
                "mode": mode, "progress_kind": progress_kind,
                "puzzle_index": index, "number": number,
            },
        )
        state_key = _key("sentence_guess", actor_id)
        try:
            delivered = await _send(event, reply, prompt)
        except Exception:  # noqa: BLE001 - fail closed when prompt delivery is unconfirmed
            delivered = None
        if _send_succeeded(delivered):
            if refresh_sentence_deadline_after_delivery(
                store, group_id, state_key,
                token=str(state.get("token") or ""), mode=mode,
                puzzle_index=index, expected_prompt=prompt,
            ):
                _mark_sentence_recent(store, group_id, answer_value)
        else:
            current_state = store.get_group_state(group_id, state_key)
            if (
                isinstance(current_state, dict)
                and current_state.get("token") == state.get("token")
            ):
                store.delete_group_state(group_id, state_key)
        return True
    if value == "مین یاب":
        if _session(store, group_id, "minesweeper", actor_id):
            await _send(event, reply, "💣 شما یک مین‌یاب باز دارید؛ اول همان را تمام کنید.")
            return True
        remaining = _minesweeper_remaining(store, actor_id)
        if remaining <= 0:
            await _send(event, reply, _minesweeper_quota_message())
            return True
        safe_cell = secrets.randbelow(9) + 1
        _start_state(
            store, group_id, "minesweeper", user_id=actor_id,
            answer=None, prompt="مین یاب", seconds=_MINESWEEPER_PICK_SECONDS,
            data={"safe_cell": safe_cell, "user_id": str(actor_id)},
        )
        title = "💣 مین یاب"
        hint = "یک خانه انتخاب کنید (۱ تا ۹)"
        remaining_line = (
            f"🎟 شانس باقی‌ماندهٔ امروز: {_fa(remaining)} از "
            f"{_fa(_MINESWEEPER_DAILY_CHANCES)}"
        )
        await _send(
            event, reply,
            f"{title}\n\n{_minesweeper_board()}\n\n"
            f"{hint}\n{remaining_line}\n"
            f"⏳ {_fa(_MINESWEEPER_PICK_SECONDS)} ثانیه",
        )
        return True
    if value == "جعبه شانسی":
        if _session(store, group_id, "lucky_box") is not None:
            await _send(event, reply, "🎁 جعبه شانسی همین حالا در جریان است.")
            return True
        if _group_busy(store, group_id):
            await _send(event, reply, _BUSY_MESSAGE)
            return True
        now = time.time()
        stamps = _lucky_box_recent_plays(store, actor_id, now)
        if len(stamps) >= 2:
            await _send(event, reply, _lucky_box_wait_message(stamps, now))
            return True
        prizes = [0, 0, 0, 0, *[secrets.randbelow(15) + 1 for _ in range(5)]]
        secrets.SystemRandom().shuffle(prizes)
        boxes = {str(index + 1): prize for index, prize in enumerate(prizes)}
        state = _start_state(
            store, group_id, "lucky_box", user_id=None, answer=None,
            prompt="جعبه شانسی", seconds=60,
            data={"user_id": str(actor_id), "boxes": boxes},
        )
        stamps.append(now)
        store.set_group_state(_GLOBAL_STATE, f"lucky_box_plays:{actor_id}", stamps)
        board = "┌───┬───┬───┐\n│ 1 │ 2 │ 3 │\n├───┼───┼───┤\n│ 4 │ 5 │ 6 │\n├───┼───┼───┤\n│ 7 │ 8 │ 9 │\n└───┴───┴───┘"
        await _send(event, reply, f"🎁 جعبه شانسی\n\n{board}\n\nشماره یک جعبه را بفرستید (۱ تا ۹)")
        return state is not None
    if value == "حدس پرچم":
        if _group_busy(store, group_id):
            await _send(event, reply, _BUSY_MESSAGE)
            return True
        _index, (flag, answers), _used = _choose(store, group_id, "flag", actor_id, FLAGS)
        state, error = _make_group_game(store, group_id, "flag", f"🌍 حدس پرچم\n\n{flag}\n\nاین پرچم متعلق به کدام کشور است؟\n\n⏳ ۳۰ ثانیه", None, answers, 30)
        await _send(event, reply, error or state["prompt"])
        return True
    if value == "تصحیح کلمات":
        if _group_busy(store, group_id):
            await _send(event, reply, _BUSY_MESSAGE)
            return True
        _index, (wrong, correct), _used = _choose(
            store, group_id, "correction", "__group__", CORRECTIONS,
        )
        state, error = _make_group_game(
            store, group_id, "correction",
            f"✍️ شکل درست این واژه را بنویس:\n\n{wrong}\n\n⏳ ۳۰ ثانیه",
            None, (correct,), 30, {"wrong": wrong},
        )
        await _send(event, reply, error or state["prompt"])
        return True
    if value == "چهار گزینه ای":
        if _group_busy(store, group_id):
            await _send(event, reply, _BUSY_MESSAGE)
            return True
        _index, (question, options, correct), _used = _choose(store, group_id, "quiz", actor_id, QUIZZES)
        category, level = QUIZ_METADATA[_index]
        prompt = (
            f"🎯 سوال چهار گزینه‌ای:\n"
            f"📚 موضوع: {category} | سطح: {level}\n\n"
            f"{question}\n\n"
            + "\n".join(f"{i}. {option}" for i, option in enumerate(options, 1))
            + "\n\n⏳ ۳۰ ثانیه"
        )
        state, error = _make_group_game(store, group_id, "quiz", prompt, None, (), 30, {"correct": correct})
        if state:
            state["answers"] = list(options)
            store.set_group_state(group_id, _key("quiz"), state)
        await _send(event, reply, error or prompt)
        return True
    if value == "کی بیشتر بلده":
        if _group_busy(store, group_id):
            await _send(event, reply, _BUSY_MESSAGE)
            return True
        selected_round = _next_who_knows_round(store, group_id, word_filter)
        if selected_round is None:
            await _send(
                event, reply,
                "🧠 همهٔ حروف این دور استفاده شدند. برای شروع دور تازه، دوباره بازی را اجرا کنید.",
            )
            return True
        letter, category, accepted_answers = selected_round
        prompt = f"🧠 کی بیشتر بلده؟\n\nبا حرف «{letter}» یک {category} بگو.\n\n⏳ ۲۵ ثانیه"
        state, error = _make_group_game(
            store, group_id, "who_knows", prompt, None, (), 25,
            {
                "letter": letter, "category": category,
                "accepted_answers": list(accepted_answers),
            },
        )
        if state is not None and error is None:
            _remember_who_knows_letter(store, group_id, letter)
        await _send(event, reply, error or prompt)
        return True
    if value == "دروغ یا حقیقت":
        if _group_busy(store, group_id):
            await _send(event, reply, _BUSY_MESSAGE)
            return True
        _index, (statement, truth), _used = _choose(
            store, group_id, "truth_lie", "__group__", TRUTH_LIE,
        )
        prompt = f"🧠 دروغ یا حقیقت؟\n\nسوال:\n«{statement}»\n\n۱) حقیقت\n۲) دروغ\n\n⏳ ۲۵ ثانیه"
        state, error = _make_group_game(store, group_id, "truth_lie", prompt, None, (), 25, {"truth": truth})
        await _send(event, reply, error or prompt)
        return True
    if value == "بخند یا بباز":
        if _group_busy(store, group_id):
            await _send(event, reply, _BUSY_MESSAGE)
            return True
        prompt = "😂 بخند یا بباز\n\nآماده باش..."
        state, error = _make_group_game(
            store, group_id, "laugh_or_lose", prompt, None, (),
            _LAUGH_COUNTDOWN_SECONDS + _LAUGH_ROUND_SECONDS,
            {"phase": "countdown"},
        )
        if state is None:
            await _send(event, reply, error or _BUSY_MESSAGE)
            return True
        await _send(event, reply, prompt)
        task = asyncio.create_task(
            _run_laugh_countdown(
                event, store, group_id=group_id, token=str(state["token"]),
                reply=reply,
            )
        )
        _LAUGH_TASKS[str(group_id)] = task
        return True
    if value == "اسم فامیل":
        if _group_busy(store, group_id):
            await _send(event, reply, _BUSY_MESSAGE)
            return True
        active_filter = (
            word_filter
            if word_filter is not None
            else getattr(name_family_service, "word_filter", None)
        )
        if active_filter is None:
            await _send(event, reply, "⚠️ سرویس امن بررسی پاسخ‌های این بازی در دسترس نیست.")
            return True
        letters = playable_name_family_letters(PERSIAN_LETTERS, active_filter)
        if not letters:
            await _send(event, reply, "❌ برای این بازی حرف امنی با پاسخ معتبر برای همهٔ دسته‌ها پیدا نشد.")
            return True
        letter = secrets.choice(letters)
        prompt = f"🎮 اسم فامیل\n\nحرف: {letter}\n\n⏳ زمان: ۹۰ ثانیه\n\n👤 نام\n👤 فامیل\n🌍 شهر\n🍇 میوه\n📦 وسیله\n🐶 حیوان\n🎵 خواننده\n\n۷ پاسخ را هر کدام در یک خط بفرستید."
        state, error = _make_group_game(store, group_id, "name_family", prompt, None, (), 90, {"letter": letter, "submissions": {}})
        await _send(event, reply, error or prompt)
        return True
    if value == "بهترین جواب":
        if _group_busy(store, group_id):
            await _send(event, reply, _BUSY_MESSAGE)
            return True
        question, keywords = secrets.choice(BEST_QUESTIONS)
        prompt = f"🎯 بهترین جواب\n\n{question}\n\nپاسخ خود را بفرستید؛ نتیجه پس از ۴۰ ثانیه اعلام می‌شود."
        state, error = _make_group_game(store, group_id, "best_answer", prompt, None, (), 40, {"question": question, "keywords": list(keywords), "submissions": {}})
        sent = await _send(event, reply, error or prompt)
        if state is not None and error is None:
            message_id = sent if isinstance(sent, int) and not isinstance(sent, bool) else getattr(sent, "id", None)
            if message_id is None:
                message_id = getattr(getattr(sent, "message", None), "id", None)
            data = dict(state.get("data") or {})
            data["question_message_id"] = message_id
            state["data"] = data
            store.set_group_state(group_id, _key("best_answer"), state)
        return True
    if value == "نبرد":
        prompt = (
            "⚔️ نبرد شروع شد!\n\n"
            "شما بازیکن اول هستید.\n"
            "برای پیوستن بازیکن دوم بنویسید:\n"
            "شرکت\n\n"
            f"⏳ مهلت ثبت‌نام: {_fa(_BATTLE_JOIN_SECONDS)} ثانیه"
        )
        host = {
            "user_id": str(actor_id),
            "name": _global_owner_name(event, actor_id),
        }
        state, error = _make_group_game(
            store, group_id, "battle", prompt, None, (), _BATTLE_JOIN_SECONDS,
            {
                "phase": "joining", "host": str(actor_id),
                "host_name": host["name"], "opponent": None,
                "players": [host], "scores": {}, "questions": [], "index": 0,
            },
        )
        delivered = await _send(event, reply, error or prompt)
        if state is not None and error is None and _send_succeeded(delivered):
            refresh_timed_game_deadline_after_delivery(
                store, group_id, _key("battle"),
                token=str(state.get("token") or ""),
                phase="joining", stage=0, expected_prompt=prompt,
            )
        return True
    if value == "شرکت":
        return False
    if value == "خون آشام":
        prompt = (
            "🧛 بازی خون‌آشام\n\n"
            "برای شرکت بنویسید: شرکت\n\n"
            f"حداقل {_fa(_VAMPIRE_MIN_PLAYERS)} و حداکثر "
            f"{_fa(_VAMPIRE_MAX_PLAYERS)} نفر\n"
            f"⏳ مهلت ثبت‌نام: {_fa(_VAMPIRE_JOIN_SECONDS)} ثانیه"
        )
        state, error = _make_group_game(
            store, group_id, "vampire", prompt, None, (), _VAMPIRE_JOIN_SECONDS,
            {"phase": "joining", "players": [], "vampire_id": None, "guessed": []},
        )
        if state is None:
            await _send(event, reply, error or _BUSY_MESSAGE)
            return True
        _clear_private_role_peers(group_id, "vampire")
        await _send(event, reply, prompt)
        join_event = asyncio.Event()
        group_key = str(group_id)
        _VAMPIRE_JOIN_EVENTS[group_key] = join_event
        task = asyncio.create_task(
            _run_vampire_assignment(
                event, store, group_id=group_id, token=str(state["token"]),
                join_event=join_event, reply=reply,
                private_message_sender=private_message_sender,
            )
        )
        _VAMPIRE_TASKS[group_key] = task
        return True
    if value == "کارگاه":
        prompt = (
            "🕵️ پروندهٔ تازه ساخته شد\n\n"
            "منتظر بازیکنان...\n\n"
            f"تعداد مورد نیاز: {_fa(_KARAGAH_MIN_PLAYERS)} تا "
            f"{_fa(_KARAGAH_MAX_PLAYERS)} نفر\n\n"
            "برای شرکت بنویسید: شرکت\n"
            f"⏳ مهلت ثبت‌نام: {_fa(_KARAGAH_JOIN_SECONDS)} ثانیه"
        )
        state, error = _make_group_game(
            store, group_id, "karagah", prompt, None, (),
            _KARAGAH_JOIN_SECONDS,
            {
                "phase": "joining", "players": [], "thief_id": None,
                "thief_index": None, "stolen_object": None,
                "options": [], "finder_id": None, "guessed": [],
            },
        )
        if state is None:
            await _send(event, reply, error or _BUSY_MESSAGE)
            return True
        _clear_private_role_peers(group_id, "karagah")
        await _send(event, reply, prompt)
        join_event = asyncio.Event()
        group_key = str(group_id)
        _KARAGAH_JOIN_EVENTS[group_key] = join_event
        task = asyncio.create_task(
            _run_karagah_assignment(
                event, store, group_id=group_id, token=str(state["token"]),
                join_event=join_event, reply=reply,
                private_message_sender=private_message_sender,
            )
        )
        _KARAGAH_TASKS[group_key] = task
        return True
    if value == "بقا":
        if _group_busy(store, group_id):
            await _send(event, reply, _BUSY_MESSAGE)
            return True
        prompt = (
            "🏕 بازی بقا\n\n"
            "برای شرکت بنویسید: شرکت\n\n"
            f"حداقل {_fa(_SURVIVAL_MIN_PLAYERS)} و حداکثر "
            f"{_fa(_SURVIVAL_MAX_PLAYERS)} نفر\n"
            f"⏳ مهلت ثبت‌نام: {_fa(_SURVIVAL_JOIN_SECONDS)} ثانیه"
        )
        state = _start_state(
            store, group_id, "survival", user_id=None, answer=None,
            prompt=prompt, seconds=_SURVIVAL_JOIN_SECONDS,
            data={
                "phase": "joining", "players": {}, "order": [],
                "round": 0, "answered": [], "used_questions": [],
            },
        )
        state["phase"] = "joining"
        store.set_group_state(group_id, _key("survival"), state)
        delivered = await _send(event, reply, prompt)
        if _send_succeeded(delivered):
            refresh_timed_game_deadline_after_delivery(
                store, group_id, _key("survival"),
                token=str(state.get("token") or ""),
                phase="joining", stage=0, expected_prompt=prompt,
            )
        return True
    return False


def _best_answer_has_spam_intent(normalized: str) -> bool:
    """Filter link, solicitation, sales, and non-answer directives by intent."""
    if any(
        marker in normalized
        for marker in ("http", "www", "تبلیغ", "سکه بده", "لینک")
    ):
        return True
    tokens = {
        "".join(
            character
            for character in word
            if not unicodedata.category(character).startswith(("P", "M"))
        )
        for word in normalized.split()
    }
    if any(
        token.startswith(("سایت", "خرید", "فروش"))
        for token in tokens
    ):
        return True
    reward_terms = {"سکه", "پول", "جایزه", "پاداش", "اعتبار"}
    request_terms = {
        "لطفا", "خواهش", "بده", "بدید", "بدهید", "بفرست", "بفرستید",
        "میخوام", "میخواهم", "میخوایم", "میدین", "میده",
    }
    if tokens & reward_terms and tokens & request_terms:
        return True
    if "بازی" in tokens and tokens & {"کن", "کنید", "بکن", "بکنید", "شروع"}:
        return True
    return bool(
        tokens & {"جواب", "پاسخ"}
        and any(
            token.startswith(("ند", "نمی")) or token in {"نه", "نزن", "نکن"}
            for token in tokens
        )
    )


def _best_answer_score(question: str, keywords: list[Any], answer: str) -> float | None:
    """Score a submitted answer locally; reject empty, irrelevant, and junk text."""
    raw = str(answer or "").strip()
    if not raw or len(raw) > 600 or not any("\u0600" <= char <= "\u06ff" for char in raw):
        return None
    normalized = normalize(raw).lower()
    dont_know_phrases = (
        "نمی‌دانم", "نمی‌دونم", "نمیدانم", "نمیدونم", "نمدونم", "نمی دانم", "نمی دونم",
    )
    if any(normalize(phrase).lower() in normalized for phrase in dont_know_phrases):
        return None
    if _best_answer_has_spam_intent(normalized):
        return None
    if len(raw) >= 4 and len(set(normalized)) <= 2:
        return None

    stop_words = {"و", "با", "به", "از", "که", "در", "برای", "این", "آن", "یک", "را", "هم", "یا", "است", "نیست", "چرا", "چه", "چطور", "می", "شود"}

    def content_tokens(value: str) -> list[str]:
        result: list[str] = []
        for word in normalize(value).lower().split():
            cleaned = "".join(char for char in word if not unicodedata.category(char).startswith("P"))
            if len(cleaned) >= 2 and not cleaned.isdigit() and cleaned not in stop_words and len(set(cleaned)) > 1:
                result.append(cleaned)
        return result

    answer_content_tokens = content_tokens(raw)
    answer_tokens = set(answer_content_tokens)
    question_tokens = set(content_tokens(question))
    if len(answer_content_tokens) < 2:
        return None
    compact = "".join(char for char in normalized if not unicodedata.category(char).startswith("P") and not char.isspace())
    matched = 0
    for keyword in keywords:
        key = "".join(
            char for char in normalize(str(keyword)).lower()
            if not unicodedata.category(char).startswith("P") and not char.isspace()
        )
        if key and key in compact:
            matched += 1
    relevance = len(answer_tokens & question_tokens) / max(1, len(question_tokens))
    accuracy = matched / max(1, len(keywords))
    if relevance <= 0 and accuracy <= 0:
        return None
    explanatory = max(0, len(answer_content_tokens) - matched)
    completeness = min(1.0, len(answer_content_tokens) / 8.0)
    if explanatory >= 2:
        completeness = max(completeness, 0.6)
    elif explanatory == 0:
        completeness = min(completeness, 0.25)
    return round(0.35 * relevance + 0.40 * accuracy + 0.40 * completeness, 4)


def _best_answer_winner(data: dict[str, Any], current: float) -> tuple[str, dict[str, Any]] | None:
    question = str(data.get("question") or "")
    keywords = list(data.get("keywords") or [])
    submissions = data.get("submissions", {})
    if not isinstance(submissions, dict):
        return None
    ranked = []
    for user_id, submission in submissions.items():
        if not isinstance(submission, dict):
            continue
        answer = str(submission.get("text") or "")
        score = _best_answer_score(question, keywords, answer)
        if score is None:
            continue
        try:
            submitted_at = float(submission.get("at", current))
        except (TypeError, ValueError):
            submitted_at = current
        ranked.append((-score, submitted_at, str(user_id), submission))
    if not ranked:
        return None
    _score, _submitted_at, user_id, submission = min(
        ranked, key=lambda item: (item[0], item[1])
    )
    return user_id, submission


def _survival_finish_notice(
    store: BotStore, group_id: str, state: dict[str, Any], players: dict[str, Any],
) -> str:
    alive = [player for player in players.values() if isinstance(player, dict) and player.get("alive")]
    if len(alive) != 1:
        return "🏕 بازی بقا بدون برنده تمام شد."
    winner = alive[0]
    user_id = str(winner.get("user_id") or "")
    name = str(winner.get("name") or "کاربر")
    rounds = max(0, int(winner.get("round_coins", 0) or 0))
    amount = _SURVIVAL_WINNER_REWARD
    _profile, award_outcome = store.award_group_coins(
        group_id, user_id, amount, "silver",
        reference=f"survival_win:{group_id}:{state.get('token')}",
        display_name=name, note="game:survival", return_outcome=True,
    )
    total = rounds + amount
    winner_reward = (
        f"+{_fa(amount)}"
        if award_outcome == "awarded"
        else "قبلاً ثبت شده"
    )
    return (
        f"🏆 برندهٔ بقا: {name}\n\n"
        f"🪙 سکهٔ مراحل: +{_fa(rounds)}\n"
        f"🪙 جایزهٔ برنده: {winner_reward}\n"
        f"مجموع این بازی: {_fa(total)} سکه نقره"
    )


def _due_game_notice_batch(
    store: BotStore, now: float | None = None,
) -> tuple[list[tuple[str, str, str]], bool, float | None]:
    """Stage due game timeouts and report the nearest future deadline."""
    current = time.time() if now is None else float(now)
    notices: list[tuple[str, str, str]] = []
    active_deadline = False
    next_deadline: float | None = None
    for group_id, state_key, state in store.list_group_states_prefix("game_session:", limit=5000):
        if not isinstance(state, dict):
            store.delete_group_state(group_id, state_key)
            continue
        if state.get("status") == "timed_out":
            notice = str(state.get("notice_text") or "")
            if notice:
                notices.append((group_id, state_key, notice))
            continue
        if state.get("deadline_pending"):
            try:
                delivery_started = float(state.get("deadline_pending_since", 0))
            except (TypeError, ValueError):
                delivery_started = 0
            if (
                delivery_started <= 0
                or delivery_started + _SENTENCE_GUESS_PENDING_DELIVERY_SECONDS <= current
            ):
                store.delete_group_state(group_id, state_key)
            continue
        if state.get("notice_pending"):
            if state.get("status", "pending") == "pending":
                try:
                    pending_due = float(state.get("expires_at", 0))
                except (TypeError, ValueError):
                    pending_due = 0
                if pending_due > 0:
                    active_deadline = True
                    if pending_due > current and (
                        next_deadline is None or pending_due < next_deadline
                    ):
                        next_deadline = pending_due
            notice = str(state.get("notice_text") or "")
            if notice:
                notices.append((group_id, state_key, notice))
            continue
        if state.get("status", "pending") != "pending":
            continue
        try:
            due = float(state.get("expires_at", 0))
        except (TypeError, ValueError):
            due = 0
        if due <= 0:
            continue
        active_deadline = True
        if due > current:
            if next_deadline is None or due < next_deadline:
                next_deadline = due
            continue
        kind = str(state.get("kind") or "")
        if kind == "laugh_or_lose":
            notice = "😐 کسی نخندید! بازی بدون برنده تمام شد."
        elif kind == "name_family":
            data = state.get("data", {}) if isinstance(state.get("data"), dict) else {}
            submissions = data.get("submissions", {}) if isinstance(data.get("submissions"), dict) else {}
            ranked = sorted(submissions.items(), key=lambda item: (-int(item[1].get("score", 0)), str(item[1].get("name", ""))))
            if not ranked:
                notice = "🏆 نتایج\n\nشرکت‌کننده‌ای پاسخ معتبر ثبت نکرد."
            else:
                lines = ["🏆 نتایج"]
                medals = ("🥇", "🥈", "🥉")
                for index, (user_id, result) in enumerate(ranked, 1):
                    points = int(result.get("score", 0))
                    reward = ""
                    if points >= 70:
                        try:
                            _profile, award_outcome = store.award_group_coins(
                                group_id, user_id, _GAME_REWARDS["name_family"][0],
                                reference=f"game:{state['token']}:name_family:{user_id}",
                                display_name=str(result.get("name", "")),
                                note="game:name_family", return_outcome=True,
                            )
                            reward = (
                                f" — 🪙 +{_fa(_GAME_REWARDS['name_family'][0])} سکه برنز"
                                if award_outcome == "awarded"
                                else " — 🪙 جایزه قبلاً ثبت شده بود"
                            )
                        except ValueError:
                            reward = ""
                    medal = medals[index - 1] if index <= len(medals) else "•"
                    lines.append(f"{medal} {result.get('name', 'کاربر')} — {_fa(points)} امتیاز{reward}")
                notice = "\n".join(lines)
        elif kind == "best_answer":
            data = state.get("data", {}) if isinstance(state.get("data"), dict) else {}
            winner_result = _best_answer_winner(data, current)
            if winner_result is None:
                notice = "🎯 هیچ پاسخ درستی ثبت نشد."
            else:
                winner_id, winner = winner_result
                _profile, award_outcome = store.award_group_coins(
                    group_id, winner_id, _GAME_REWARDS["best_answer"][0],
                    reference=f"game:{state['token']}:best_answer:{winner_id}",
                    display_name=str(winner.get("name", "")),
                    note="game:best_answer", return_outcome=True,
                )
                reward_line = (
                    f"🪙 +{_fa(_GAME_REWARDS['best_answer'][0])} سکه برنز"
                    if award_outcome == "awarded"
                    else "🪙 جایزه قبلاً ثبت شده بود"
                )
                notice = (
                    f"🏆 بهترین پاسخ: {winner.get('name', 'کاربر')}\n\n"
                    f"«{winner.get('text', '')}»\n\n{reward_line}"
                )
        elif kind == "battle":
            data = state.get("data", {}) if isinstance(state.get("data"), dict) else {}
            phase = str(state.get("phase") or data.get("phase") or "joining")
            if phase == "joining":
                notice = "⏰ مهلت ثبت‌نام تمام شد؛ بازیکن دوم نیامد. نبرد لغو شد."
                state["status"] = "timed_out"
            elif phase == "playing":
                try:
                    data["index"] = int(data.get("index", 0) or 0) + 1
                except (TypeError, ValueError):
                    data["index"] = _BATTLE_QUESTIONS_TOTAL
                # The reference reports the timeout and the next/final prompt
                # as distinct sequential messages, never as a merged notice.
                notice = "⏰ زمان سؤال تمام شد!"
                if int(data["index"]) >= _BATTLE_QUESTIONS_TOTAL:
                    result = _battle_finish_notice(store, group_id, state, data)
                    state["notice_queue"] = [result[:2500]]
                else:
                    prompt = _battle_set_question(state, data, current=current)
                    if prompt:
                        state["notice_queue"] = [prompt[:2500]]
                    state["notice_pending"] = True
            else:
                notice = "⚔️ نبرد بدون نتیجه پایان یافت."
                state["status"] = "timed_out"
            state["data"] = data
            state["notice_text"] = notice[:2500]
            store.set_group_state(group_id, state_key, state)
            notices.append((group_id, state_key, notice[:2500]))
            continue
        elif kind == "vampire":
            data = state.get("data", {}) if isinstance(state.get("data"), dict) else {}
            phase = str(state.get("phase") or data.get("phase") or "joining")
            task = _VAMPIRE_TASKS.get(str(group_id))
            if phase in {"joining", "assigning"} and task is not None and not task.done():
                continue
            if phase == "joining":
                players = data.get("players", [])
                players = players if isinstance(players, list) else []
                notice = (
                    "🧛 تعداد شرکت‌کننده کافی نبود؛ بازی لغو شد."
                    if len(players) < _VAMPIRE_MIN_PLAYERS
                    else _VAMPIRE_FAILED_MESSAGE
                )
            elif phase == "guessing":
                notice = _vampire_reveal_text(data)
                vampire_id = str(data.get("vampire_id") or "")
                if vampire_id:
                    store.set_group_state(
                        _GLOBAL_STATE, f"vampire_last_user:{group_id}", vampire_id
                    )
            else:
                notice = _VAMPIRE_FAILED_MESSAGE
            state["status"] = "timed_out"
            state["notice_text"] = notice[:2500]
            store.set_group_state(group_id, state_key, state)
            notices.append((group_id, state_key, notice[:2500]))
            continue
        elif kind == "karagah":
            data = state.get("data", {}) if isinstance(state.get("data"), dict) else {}
            phase = str(state.get("phase") or data.get("phase") or "joining")
            task = _KARAGAH_TASKS.get(str(group_id))
            if phase in {"joining", "assigning"} and task is not None and not task.done():
                continue
            if phase == "joining":
                players = data.get("players", [])
                players = players if isinstance(players, list) else []
                notice = (
                    _KARAGAH_NOT_ENOUGH_MESSAGE
                    if len(players) < _KARAGAH_MIN_PLAYERS
                    else _KARAGAH_FAILED_MESSAGE
                )
                state["status"] = "timed_out"
                state["notice_text"] = notice
                store.set_group_state(group_id, state_key, state)
            elif phase == "assigning":
                notice = _KARAGAH_FAILED_MESSAGE
                state["status"] = "timed_out"
                state["notice_text"] = notice
                store.set_group_state(group_id, state_key, state)
            elif phase in {"thief_guess", "object_guess"}:
                reason = "thief_timeout" if phase == "thief_guess" else "object_timeout"
                notice = _karagah_stage_resolution(
                    store, group_id, state_key, state, data,
                    result="thief_wins", reason=reason,
                )
            elif phase == "settling":
                notice = _karagah_resolution_notice(
                    store, group_id, state_key, state, data,
                )
            else:
                notice = "🕵️ پرونده بدون نتیجه بسته شد."
                state["status"] = "timed_out"
                state["notice_text"] = notice
                store.set_group_state(group_id, state_key, state)
            notices.append((group_id, state_key, notice[:2500]))
            continue
        elif kind == "survival":
            data = state.get("data", {}) if isinstance(state.get("data"), dict) else {}
            players = data.get("players", {})
            players = players if isinstance(players, dict) else {}
            phase = str(state.get("phase") or data.get("phase") or "joining")
            if phase == "joining":
                if len(players) < _SURVIVAL_MIN_PLAYERS:
                    notice = "🏕 تعداد شرکت‌کننده کافی نبود؛ بازی لغو شد."
                    state["status"] = "timed_out"
                else:
                    order = data.get("order", [])
                    order = order if isinstance(order, list) else list(players)
                    roster = "\n".join(
                        f"{_fa(index)}. {players[user_id].get('name', 'کاربر')}"
                        for index, user_id in enumerate(order, 1)
                        if isinstance(players.get(user_id), dict)
                    )
                    question_prompt = _survival_start_round(
                        store, group_id, state, data, current=current
                    )
                    # Keep the reference's roster and question as two ordered
                    # messages. The runtime drains this bounded queue serially.
                    notice = f"🏕 بازی شروع شد!\n\n{roster}"
                    state["notice_queue"] = [question_prompt[:2500]]
                    state["notice_pending"] = True
            elif phase == "playing":
                alive_before = [
                    user_id for user_id, player in players.items()
                    if isinstance(player, dict) and player.get("alive")
                ]
                answered = data.get("answered", [])
                answered = set(answered) if isinstance(answered, list) else set()
                removed: list[str] = []
                for user_id in alive_before:
                    if user_id not in answered:
                        players[user_id]["alive"] = False
                        removed.append(str(players[user_id].get("name") or "کاربر"))
                alive = [
                    user_id for user_id, player in players.items()
                    if isinstance(player, dict) and player.get("alive")
                ]
                if removed and not alive and len(alive_before) > 1:
                    for user_id in alive_before:
                        players[user_id]["alive"] = True
                    alive = list(alive_before)
                    removed = []
                round_number = int(data.get("round", 0) or 0)
                removed_notice = (
                    "❌ حذف شدند:\n" + "\n".join(f"• {name}" for name in removed)
                    if removed else None
                )
                if len(alive) <= 1 or round_number >= _SURVIVAL_MAX_ROUNDS:
                    finish_notice = _survival_finish_notice(
                        store, group_id, state, players
                    )
                    notice = removed_notice or finish_notice
                    if removed_notice:
                        state["notice_queue"] = [finish_notice[:2500]]
                    state["status"] = "timed_out"
                else:
                    question_prompt = _survival_start_round(
                        store, group_id, state, data, current=current
                    )
                    notice = removed_notice or question_prompt
                    if removed_notice:
                        state["notice_queue"] = [question_prompt[:2500]]
                    state["notice_pending"] = True
            else:
                notice = "🏕 بازی بقا بدون برنده تمام شد."
                state["status"] = "timed_out"
            data["players"] = players
            state["data"] = data
            state["notice_text"] = notice[:2500]
            store.set_group_state(group_id, state_key, state)
            notices.append((group_id, state_key, notice[:2500]))
            continue
        elif kind == "quiz":
            options = list(state.get("answers", []))
            correct = int((state.get("data") or {}).get("correct", 0))
            if 1 <= correct <= len(options):
                notice = f"⏰ زمان تمام شد!\n\n✅ پاسخ درست: گزینهٔ {_fa(correct)} — {options[correct - 1]}"
            else:
                notice = "⏰ زمان تمام شد!"
        elif kind == "minesweeper":
            data = state.get("data", {}) if isinstance(state.get("data"), dict) else {}
            user_id = str(data.get("user_id") or state_key.rsplit(":", 1)[-1])
            safe_cell = int(data.get("safe_cell", 0) or 0)
            remaining = _minesweeper_consume_chance(
                store, user_id,
                reference=f"{group_id}:{state.get('token')}:{user_id}",
            )
            remaining_line = (
                f"🎟 شانس باقی‌ماندهٔ امروز: {_fa(remaining)} از "
                f"{_fa(_MINESWEEPER_DAILY_CHANCES)}"
            )
            if 1 <= safe_cell <= 9:
                notice = (
                    "⏰ زمان تمام شد!\n\n"
                    f"{_minesweeper_board(safe_cell, won=True)}\n\n"
                    f"✅ خانهٔ امن در {_fa(safe_cell)} بود.\n{remaining_line}"
                )
            else:
                notice = "⏰ زمان انتخاب خانه تمام شد.\n" + remaining_line
        elif kind == "lucky_box":
            notice = "⏰ زمان انتخاب جعبه تمام شد."
        elif kind == "truth_lie":
            truth = bool((state.get("data") or {}).get("truth"))
            notice = "⏰ زمان تمام شد!\n\nپاسخ درست: " + ("حقیقت" if truth else "دروغ")
        else:
            answer = str(state.get("answers", [""])[0]) if state.get("answers") else ""
            notice = "⏰ زمان تمام شد!"
            if answer:
                notice += f"\n\n✅ پاسخ درست: {answer}"
        state["status"] = "timed_out"
        state["notice_text"] = notice[:2500]
        store.set_group_state(group_id, state_key, state)
        notices.append((group_id, state_key, notice[:2500]))
    return notices, active_deadline, next_deadline


def due_game_notices(store: BotStore, now: float | None = None) -> list[tuple[str, str, str]]:
    """Stage due game timeouts once; return (group, state_key, notice)."""
    return _due_game_notice_batch(store, now)[0]


def due_game_notices_and_activity(
    store: BotStore, now: float | None = None,
) -> tuple[list[tuple[str, str, str]], bool, float | None]:
    """Return staged notices, active-deadline status, and the next deadline."""
    return _due_game_notice_batch(store, now)
