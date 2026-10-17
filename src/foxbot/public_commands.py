from __future__ import annotations

import asyncio
import html
import json
import re
import threading
import time
import unicodedata
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from urllib.error import URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from foxbot.economy_commands import handle_economy_command
from foxbot.factual_search import FactualSearchError, factual_intent, search_factual
from foxbot.filters import WordFilter
from foxbot.font_converter import make_fonts
from foxbot.fun_content import (
    BIOGRAPHIES,
    DARES,
    FACTS,
    JOKES,
    TRUTHS,
    personal_reply,
    pick_new,
)
from foxbot.game_commands import handle_game_command
from foxbot.photo_download import (
    OwnerCheck,
    PhotoSender,
    PhotoUrlSender,
    handle_photo_download_command,
)
from foxbot.storage import BotStore

ReplyCallback = Callable[..., Awaitable[Any]]
StatusProvider = Callable[[Any], Awaitable[str]]
PrivateMessageSender = Callable[[str | int, str | int, int, str], Awaitable[Any]]
ReplyMessageLoader = Callable[[str | int, str | int], Awaitable[Any | None]]
_GLOBAL_STATE = "__global__"
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_MATH_DIGITS = "𝟬𝟭𝟮𝟯𝟰𝟱𝟲𝟳𝟴𝟵"
_TRANSLATION_SLOTS = threading.BoundedSemaphore(2)
_SEARCH_SLOTS = threading.BoundedSemaphore(2)
_TRANSLATION_ERROR = "❌ ارتباط با سرویس ترجمه برقرار نشد، دوباره تلاش کنید."
_HELP_COMMANDS = frozenset({"راهنما", "/help", "!help", "help"})
_GAME_LIST_COMMANDS = frozenset({
    "لیست بازی", "لیست بازی ها", "لیست بازی‌ها", "بازی ها", "بازی‌ها",
})
_USER_HELP = (
    "👤 دستورات کاربران\n\n"
    "راهنما و فهرست‌ها: راهنما | help | /help | !help | لیست کاربران | لیست ادمینی | لیست بازی | لیست بازی ها | لیست بازی‌ها | بازی ها | بازی‌ها\n"
    "ثبت و نمایش اصل: ثبت اصل | اصلم\n"
    "آمار و امتیاز: آمارم | امتیاز من | رتبه ها | راهنمای امتیاز | سطح گروه\n"
    "حافظهٔ نام: ثبت اسم <نام> | ثبت <نام> | حافظه من | حذف اسم\n"
    "اطلاعات: بیوگرافی | دانستنی | جک | شخصیت <نام>\n"
    "آزمایش دکمهٔ شیشه‌ای: تست دکمه\n"
    "یادآوری: یاد آوری\n"
    "ترجمهٔ انگلیسی: ترجمه\n"
    "جستجوی وب: جستجو <عبارت> | پرسش اطلاعاتی با ریپلای به پیام روباه (در صورت مجاز بودن)\n"
    "فونت: فونت <متن>\n"
    "اقتصاد: موجودی/کیف پول/انتقال سکه (تبدیل، انتقال، تاریخچه و جایزه روزانه) | فروشگاه\n"
    "پروفایل: ثبت پرفایل/ثبت پروفایل | پرفایلم/پروفایلم | حذف پرفایل/حذف پروفایل\n"
    "دانلود عکس: دانلود عکس\n"
    "سایت بازی: سایت بازی | سایت | لینک بازی | /game | /site"
)
_ADMIN_HELP = (
    "👮 فرمان‌های مدیریت گروه\n\n"
    "فیلتر واژه: /فیلتر <واژه> | /رفع <واژه> | /فیلترها | !addword/!addban <واژه> | !remword/!removeword <واژه> | !افزودن/!حذف <واژه>\n"
    "فیلتر سختگیرانه: فعال کلمات ممنوعه | لغو کلمات ممنوعه\n"
    "راهنما: لیست ادمینی | !راهنما | /help و !help (راهنمای عمومی)\n"
    "آمار و شمارنده: !stats/!آمار | !reset/!ریست [شناسه کاربر]\n"
    "قفل گروه: !lock/!قفل | !unlock/!باز | !timelock/!قفل_ساعتی <دقیقه>\n"
    "نام و تصویر گروه: !title/!اسم <نام> | !photo/!عکس <مسیر تصویر محلی>\n"
    "مدیریت پیام و عضو: لیست سکوت | رفع سکوت همه | جستجو عضو/بیو <عبارت> | سکوت نام/بیو <عبارت> | اخراج نام/بیو <عبارت> | (ریپلای) سکوت | رفع سکوت | اخراج | آزاد [@username] | ریست اخراجی ها\n"
    "پاک‌کردن: پاک (ریپلای به پیام) | پاک <تعداد>\n"
    "عملیات پس از اسکن و با تایید جداگانه: تایید عملیات <شناسه> | تایید رفع سکوت <شناسه> | لغو عملیات <شناسه> | تایید پاک <شناسه> | لغو پاک <شناسه>\n"
    "ثبت و نمایش قوانین: ثبت قوانین | قوانین | حذف قوانین\n"
    "ادمین و VIP: لیست ادمین | ثبت ادمین (ریپلای/@username) | لغو ادمین | برکناری ادمین | vip | لغو vip | لیست vip\n"
    "اخطار و سابقه: (ریپلای) اخطار | صفر | حذف اخطار | حذف اخطارها | تغییر اخطار | تغییر مجازات | سابقه ها | لاگ مدیریتی\n"
    "آمار و زمان‌بندی: آمار گروه | آمار گپ | ریست آمار | پاکسازی خودکار | مهلت گروه\n"
    "بازی‌های گروه: سرگرمی فعال | سرگرمی خاموش\n"
    "کپی‌بورد و حافظه: کپی بورد | کپی | حذف حافظه\n"
    "جستجوی منبع‌دار: هوش مصنوعی فعال | هوش مصنوعی خاموش | مجاز [@username] | غیرمجاز/غیر مجاز [@username] | لیست هوش مصنوعی\n"
    "فیلتر نام: فهرست/لیست فیلتر نام/اسم | فیلتر نام/اسم <واژه> | لغو فیلتر نام/اسم <واژه>\n"
    "ویژهٔ مالک سراسری: فعال | غیر فعال | فعال سازی | ثبت گروه | ثبت مالک (ریپلای) | لغو مالک/برکناری مالک | لیست انقضا | وضعیت ربات | پینگ ربات | اطلاع رسانی/اطلاعرسانی/اعلان (پیش‌نمایش و تأیید)"
)
_GAME_LIST = (
    "🎮 فهرست بازی‌های روباه\n\n"
    "🧩 چیستان | معما | حدس ایموجی | حدس پرچم\n"
    "✍️ تصحیح کلمات | چهار گزینه‌ای | جای خالی | ساخت جمله | حدس جمله\n"
    "📝 اسم فامیل | کی بیشتر بلده | دروغ یا حقیقت\n"
    "🎭 جرعت | جرات | جرئت | حقیقت | حقیقت بگو | جک\n"
    "😂 بخند یا بباز | 🏕 بقا | 🎁 جعبه شانسی | 🧛 خون‌آشام\n"
    "💣 مین‌یاب | 🎯 بهترین جواب | ⚔️ نبرد | 🕵️ کارگاه\n"
    "برای پیوستن به نبرد بنویسید: شرکت\n"
    "برای ریست پیشرفت حدس ایموجی: شروع دوباره حدس ایموجی | ریست حدس ایموجی\n\n"
    "برای بازی آنلاین گروه: سایت بازی"
)


def _translate_sync(text: str) -> tuple[str | None, str | None]:
    query = text.strip()
    if not query:
        return None, "❌ متن انگلیسی ارسال نشده است."
    if len(query) > 2000:
        return None, "❌ متن برای ترجمه بیش از حد طولانی است."
    url = "https://api.mymemory.translated.net/get?" + urlencode(
        {"q": query, "langpair": "en|fa"}
    )
    with _TRANSLATION_SLOTS:
        for attempt in range(2):
            request = Request(url, headers={"User-Agent": "SoroushPlusBot/1.0"})
            try:
                with urlopen(request, timeout=15) as response:
                    if response.status != 200:
                        return None, _TRANSLATION_ERROR
                    payload = response.read(1_000_001)
                if len(payload) > 1_000_000:
                    return None, _TRANSLATION_ERROR
                data = json.loads(payload.decode("utf-8"))
                translated = data.get("responseData", {}).get("translatedText")
                if not isinstance(translated, str) or not translated.strip():
                    return None, _TRANSLATION_ERROR
                return html.unescape(translated.strip()), None
            except (URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError):
                if attempt == 0:
                    time.sleep(0.5)
    return None, _TRANSLATION_ERROR


_SEARCH_UNAVAILABLE = "❌ ارتباط با سرور جستجو برقرار نشد، چند لحظه بعد دوباره تلاش کنید."
_SEARCH_NO_RESULTS = "🔍 نتیجه‌ای پیدا نشد."
_SEARCH_BLOCKED_WORDS = (
    "porn", "porno", "xxx", "sex", "s e x", "سکس", "سکسی", "پورن",
    "فیلم پورن", "فیلم سوپر", "سوپر", "gay", "گی", "lez", "les", "لز",
    "تریسام", "threesome", "adult", "nude", "naked", "برهنه", "18+", "18",
    "erotic", "شهوت", "شهوانی",
)
_MODERATION_SEARCH_PREFIXES = ("جستجو عضو ", "جستجو بیو ")
_SEARCH_RESULT_PATTERN = re.compile(
    r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_SEARCH_TAG_PATTERN = re.compile(r"<.*?>", re.DOTALL)


def _search_web_search_locked(query: str) -> str:
    """Small DuckDuckGo HTML client; called only in a worker thread."""
    query = str(query or "").strip()
    if not query:
        return _SEARCH_NO_RESULTS
    url = "https://html.duckduckgo.com/html/?" + urlencode({"q": query})
    payload: bytes | None = None
    for attempt in range(3):
        request = Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; FoxSoroushBot/1.0)",
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        try:
            with urlopen(request, timeout=20) as response:
                if response.status == 429 or response.status >= 500:
                    if attempt < 2:
                        time.sleep(1 if attempt == 0 else 2)
                        continue
                    return _SEARCH_UNAVAILABLE
                if response.status != 200:
                    return _SEARCH_NO_RESULTS
                payload = response.read(2_000_001)
            if len(payload) > 2_000_000:
                return _SEARCH_UNAVAILABLE
            break
        except (URLError, TimeoutError, OSError):
            if attempt == 2:
                return _SEARCH_UNAVAILABLE
            time.sleep(1 if attempt == 0 else 2)
    if not payload:
        return _SEARCH_UNAVAILABLE
    page = payload.decode("utf-8", errors="replace")
    if "<html" not in page.lower():
        return _SEARCH_NO_RESULTS
    results: list[tuple[str, str]] = []
    for link, raw_title in _SEARCH_RESULT_PATTERN.findall(page):
        title = html.unescape(_SEARCH_TAG_PATTERN.sub("", raw_title)).strip()
        if "uddg=" in link:
            try:
                link = parse_qs(urlparse(link).query).get("uddg", [link])[0]
            except (TypeError, ValueError):
                pass
        link = html.unescape(link).strip()
        if title and link:
            results.append((title, link))
    if not results:
        return _SEARCH_NO_RESULTS
    return "🔎 نتایج جستجو:\n\n" + "\n\n".join(
        f"{index}- {title}\n🔗 {link}"
        for index, (title, link) in enumerate(results[:5], 1)
    )


def _search_web_sync(query: str) -> str:
    with _SEARCH_SLOTS:
        return _search_web_search_locked(query)


def _search_cooldown(store: BotStore, actor_id: str | int) -> tuple[bool, int]:
    now = time.time()
    state = store.get_group_state(_GLOBAL_STATE, "search_cooldown", {})
    cooldowns = state if isinstance(state, dict) else {}
    recent: dict[str, float] = {}
    for user, stamp in cooldowns.items():
        try:
            value = float(stamp)
        except (TypeError, ValueError):
            continue
        if 0 <= now - value < 60:
            recent[str(user)] = value
    user_key = str(actor_id)
    last = recent.get(user_key, 0.0)
    if now - last < 60:
        store.set_group_state(_GLOBAL_STATE, "search_cooldown", recent)
        return False, int(60 - (now - last))
    recent[user_key] = now
    store.set_group_state(_GLOBAL_STATE, "search_cooldown", recent)
    return True, 0


def _reply_message_id(event: Any) -> int | None:
    message = getattr(event, "message", None)
    candidates = (
        getattr(getattr(event, "reply_to", None), "reply_to_msg_id", None),
        getattr(event, "reply_to_msg_id", None),
        getattr(message, "reply_to_msg_id", None),
        getattr(getattr(message, "reply_to", None), "reply_to_msg_id", None),
    )
    for candidate in candidates:
        try:
            parsed = int(candidate)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return None


def _search_factual_sync(query: str) -> str:
    # Share the two-slot cap with ordinary web searches so one route cannot
    # multiply outbound web traffic or exhaust worker threads.
    with _SEARCH_SLOTS:
        return search_factual(query)


async def _handle_factual_reply_search(
    event: Any,
    store: BotStore,
    *,
    group_id: str | int,
    actor_id: str | int,
    text: str,
    reply: ReplyCallback | None,
    bot_user_id: str | int | None,
    reply_message_loader: ReplyMessageLoader | None,
) -> bool:
    if bot_user_id is None or reply_message_loader is None:
        return False
    value = _normalize(text)
    if not value or value[0] in "!./":
        return False
    if not bool(store.get_group_state(group_id, "google_ai_enabled", False)):
        return False
    if not store.has_group_command_member(group_id, "ai_allowed", actor_id):
        return False
    is_request, topic = factual_intent(text)
    if not is_request:
        return False
    reply_id = _reply_message_id(event)
    if reply_id is None:
        return False

    replied = None
    try:
        replied = await reply_message_loader(group_id, reply_id)
    except Exception:  # noqa: BLE001 - reply lookup failures must fail closed
        return False
    if replied is None:
        return False
    replied_id = getattr(replied, "id", None)
    if replied_id is not None and str(replied_id) != str(reply_id):
        return False
    replied_group = getattr(replied, "chat_id", None)
    if replied_group is not None and str(replied_group) != str(group_id):
        return False
    sender = getattr(replied, "sender", None)
    reply_sender_id = getattr(replied, "sender_id", None)
    if reply_sender_id is None:
        reply_sender_id = getattr(sender, "id", None)
    if reply_sender_id is None or str(reply_sender_id) != str(bot_user_id):
        return False

    normalized_request = value.lower()
    if any(term in normalized_request for term in _SEARCH_BLOCKED_WORDS):
        await _send(event, reply, "🚫 جستجو این مطلب غیرمجاز است.")
        return True
    if len(topic) > 350:
        await _send(event, reply, "❌ عبارت جستجو بیش از حد طولانی است.")
        return True
    try:
        answer = await asyncio.to_thread(_search_factual_sync, topic)
    except FactualSearchError as error:
        if error.reason == "unavailable":
            await _send(event, reply, _SEARCH_UNAVAILABLE)
        else:
            await _send(event, reply, "ℹ️ اطلاعات کافی و قابل‌اعتمادی برای پاسخ پیدا نشد.")
        return True
    except Exception:  # noqa: BLE001 - never expose provider details to the group
        await _send(event, reply, _SEARCH_UNAVAILABLE)
        return True
    await _send(event, reply, answer)
    return True


def _normalize(value: str) -> str:
    return " ".join(
        str(value or "")
        .replace("\u200c", " ")
        .replace("\u200f", "")
        .replace("\u200e", "")
        .replace("ي", "ی")
        .replace("ك", "ک")
        .split()
    )


def _math_digits(value: Any) -> str:
    return "".join(_MATH_DIGITS[int(char)] if char.isdigit() else char for char in str(value))


def _u16(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _text_entity(name: str, offset: int, length: int) -> Any:
    from splusthon.tl import types

    cls = getattr(types, name, None)
    return cls(offset=offset, length=length) if cls else None


def _bold_entity(offset: int, length: int) -> Any:
    return _text_entity("MessageEntityBold", offset, length)


def _blockquote_entity(offset: int, length: int) -> Any:
    return _text_entity("MessageEntityBlockquote", offset, length)


def _display_name(event: Any, actor_id: str | int) -> str:
    sender = getattr(event, "sender", None)
    username = str(getattr(sender, "username", None) or "").strip().lstrip("@")
    if username:
        return f"@{username}"
    name = " ".join(
        str(part).strip()
        for part in (getattr(sender, "first_name", None), getattr(sender, "last_name", None))
        if part and str(part).strip()
    )
    return name or str(actor_id)


async def _send(
    event: Any,
    reply: ReplyCallback | None,
    text: str,
    *,
    formatting_entities: list[Any] | None = None,
    buttons: Any | None = None,
) -> Any:
    if reply is not None:
        kwargs: dict[str, Any] = {"formatting_entities": formatting_entities}
        if buttons is not None:
            kwargs["buttons"] = buttons
        return await reply(event, text, **kwargs)
    sender = getattr(event, "reply", None) or getattr(event, "respond", None)
    if not callable(sender):
        raise TypeError("event has no supported reply method")
    kwargs = {"formatting_entities": formatting_entities} if formatting_entities else {}
    if buttons is not None:
        kwargs["buttons"] = buttons
    result = sender(text, **kwargs)
    return await result if hasattr(result, "__await__") else result


def _jalali_today(today: date | None = None) -> str:
    current = today or date.today()
    gy, gm, gd = current.year, current.month, current.day
    g_days = (0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334)
    gy2 = gy + 1 if gm > 2 else gy
    days = (
        355666 + 365 * gy + (gy2 + 3) // 4 - (gy2 + 99) // 100
        + (gy2 + 399) // 400 + gd + g_days[gm - 1]
    )
    jy = -1595 + 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm = 1 + days // 31
        jd = 1 + days % 31
    else:
        jm = 7 + (days - 186) // 30
        jd = 1 + (days - 186) % 30
    return f"{jy:04d}/{jm:02d}/{jd:02d}"


def _validated_name(text: str) -> str | None:
    value = _normalize(text)
    if not value or len(value) > 20:
        return None
    if any(not (char.isalpha() or char in " -_") for char in value):
        return None
    if len(value.replace(" ", "")) < 2 or len(value.split()) > 2:
        return None
    if any(unicodedata.category(char).startswith("C") for char in value):
        return None
    return value


async def _handle_memory(
    event: Any,
    store: BotStore,
    *,
    group_id: str | int,
    actor_id: str | int,
    value: str,
    reply: ReplyCallback | None,
) -> bool:
    if value == "حافظه من":
        name = store.get_group_state(group_id, f"memory:{actor_id}")
        await _send(event, reply, f"🧠 حافظه شما: {name}" if name else "🧠 هنوز اسمی ثبت نکردی.")
        return True
    if value == "حذف اسم":
        removed = store.delete_group_state(group_id, f"memory:{actor_id}")
        await _send(event, reply, "✅ حافظه شما حذف شد." if removed else "🧠 حافظه‌ای برای حذف ندارید.")
        return True

    prefix = None
    for candidate in ("ثبت اسم ", "ثبت "):
        if value.startswith(candidate):
            prefix = candidate
            break
    if prefix is None:
        return False
    name_text = value[len(prefix):].strip()
    if not name_text:
        return False
    reserved = {
        "ادمین", "مالک", "گروه", "قوانین", "اصل", "پروفایل", "پرفایل",
        "کلمه", "کلمات", "اخطار", "vip",
    }
    if prefix == "ثبت " and name_text.split()[0] in reserved:
        return False
    name = _validated_name(name_text)
    if name is None:
        await _send(event, reply, "❌ نام معتبر نیست؛ نام باید حداکثر ۲۰ نویسه و شامل یک یا دو واژه باشد.")
        return True
    store.set_group_state(group_id, f"memory:{actor_id}", name)
    await _send(event, reply, f"✅ حافظه شما ثبت شد: {name}")
    return True


async def _handle_original(
    event: Any,
    store: BotStore,
    *,
    actor_id: str | int,
    value: str,
    text: str,
    reply: ReplyCallback | None,
) -> bool:
    key = f"original_pending:{actor_id}"
    if value == "ثبت اصل":
        store.set_group_state(
            _GLOBAL_STATE,
            key,
            {"started_at": time.time()},
        )
        await _send(event, reply, "لقب یا اصل خودتو بنویس")
        return True

    pending = store.get_group_state(_GLOBAL_STATE, key)
    if isinstance(pending, dict):
        try:
            started_at = float(pending.get("started_at", 0))
        except (TypeError, ValueError):
            started_at = 0
        if 0 < started_at and time.time() - started_at <= 600:
            original = _normalize(text)
            if original:
                store.set_group_state(_GLOBAL_STATE, f"user_original:{actor_id}", original[:200])
                store.delete_group_state(_GLOBAL_STATE, key)
                await _send(event, reply, "✅ اصل شما ثبت شد")
                return True
        else:
            store.delete_group_state(_GLOBAL_STATE, key)

    if value == "اصلم":
        original = store.get_group_state(_GLOBAL_STATE, f"user_original:{actor_id}")
        if original:
            entity = _bold_entity(0, _u16(str(original)))
            await _send(event, reply, str(original), formatting_entities=[entity] if entity else None)
        else:
            await _send(event, reply, "هنوز اصلی ثبت نکردی. برای ثبت بنویس: ثبت اصل")
        return True
    return False


async def _handle_reminder(
    event: Any,
    store: BotStore,
    *,
    group_id: str | int,
    actor_id: str | int,
    value: str,
    text: str,
    reply: ReplyCallback | None,
) -> bool:
    pending_key = f"reminder_pending:{actor_id}"
    if value == "یاد آوری":
        store.set_group_state(
            group_id,
            pending_key,
            {"started_at": time.time(), "name": _display_name(event, actor_id)},
        )
        await _send(event, reply, "📝 متن یادآوری و زمان موردنظر را ارسال کنید.")
        return True

    pending = store.get_group_state(group_id, pending_key)
    if not isinstance(pending, dict):
        return False
    try:
        started_at = float(pending.get("started_at", 0))
    except (TypeError, ValueError):
        started_at = 0
    if started_at <= 0 or time.time() - started_at > 600:
        store.delete_group_state(group_id, pending_key)
        return False

    normalized = _normalize(text).translate(_DIGITS)
    match = re.match(r"^(\d+)\s*(دقیقه|ساعت|روز)\s+(.+)$", normalized, re.DOTALL)
    if not match:
        await _send(event, reply, "❌ زمان را مانند «۳۰ دقیقه آب بخور» ارسال کنید.")
        return True
    amount = int(match.group(1))
    unit = match.group(2)
    reminder_text = match.group(3).strip()
    if amount <= 0 or amount > 365 * 24 * 60 or not reminder_text or len(reminder_text) > 2000:
        await _send(event, reply, "❌ زمان یا متن یادآوری معتبر نیست؛ دوباره ارسال کنید.")
        return True
    if store.count_group_states_prefix("reminder:") >= 2000:
        await _send(event, reply, "⚠️ ظرفیت یادآوری‌ها تکمیل است؛ بعداً دوباره تلاش کنید.")
        return True
    seconds = amount * {"دقیقه": 60, "ساعت": 3600, "روز": 86400}[unit]
    due_at = datetime.now(UTC) + timedelta(seconds=seconds)
    reminder_id = uuid.uuid4().hex
    reminder = {
        "id": reminder_id,
        "user_id": str(actor_id),
        "name": str(pending.get("name") or _display_name(event, actor_id)),
        "text": reminder_text,
        "due_at": due_at.isoformat(timespec="seconds"),
        "time_label": f"{amount} {unit} دیگر",
        "attempts": 0,
    }
    store.set_group_state(group_id, f"reminder:{reminder_id}", reminder)
    store.delete_group_state(group_id, pending_key)
    await _send(
        event,
        reply,
        "✅ یادآوری ثبت شد.\n\n"
        f"⏰ زمان: {reminder['time_label']}\n"
        f"📝 متن: {reminder_text}",
    )
    return True


async def _handle_translation(
    event: Any,
    store: BotStore,
    *,
    group_id: str | int,
    actor_id: str | int,
    value: str,
    text: str,
    reply: ReplyCallback | None,
) -> bool:
    pending_key = f"translation_pending:{actor_id}"
    if value == "ترجمه":
        store.set_group_state(group_id, pending_key, {"started_at": time.time()})
        await _send(event, reply, "🌐 متن انگلیسی خود را ارسال کنید.")
        return True
    pending = store.get_group_state(group_id, pending_key)
    if not isinstance(pending, dict):
        return False
    try:
        started_at = float(pending.get("started_at", 0))
    except (TypeError, ValueError):
        started_at = 0
    if started_at <= 0 or time.time() - started_at > 600:
        store.delete_group_state(group_id, pending_key)
        return False
    store.delete_group_state(group_id, pending_key)
    translated, error = await asyncio.to_thread(_translate_sync, text)
    if error or translated is None:
        await _send(event, reply, error or _TRANSLATION_ERROR)
        return True
    translation_text = (
        "🌐 ترجمه\n\n"
        f"🇬🇧 متن:\n\n{text}\n\n"
        f"🇮🇷 ترجمه:\n\n{translated}"
    )
    entities = []
    for label in ("🌐 ترجمه", "🇬🇧 متن:", "🇮🇷 ترجمه:"):
        offset = _u16(translation_text[:translation_text.index(label)])
        entity = _bold_entity(offset, _u16(label))
        if entity:
            entities.append(entity)
    translated_offset = _u16(translation_text[:translation_text.rfind(translated)])
    bold = _bold_entity(translated_offset, _u16(translated))
    quote = _blockquote_entity(translated_offset, _u16(translated))
    entities.extend(entity for entity in (bold, quote) if entity)
    await _send(event, reply, translation_text, formatting_entities=entities)
    return True


async def _handle_user_stats(
    event: Any,
    store: BotStore,
    *,
    group_id: str | int,
    actor_id: str | int,
    value: str,
    reply: ReplyCallback | None,
) -> bool:
    if value == "آمارم":
        activity = store.get_group_user_activity(group_id, actor_id)
        user = store.get_group_user_stats(group_id, actor_id)
        messages = int(user.get("messages", 0))
        violations = store.get_warning_count(group_id, actor_id)
        first, last = float(activity.get("first", 0)), float(activity.get("last", 0))
        active_hours = max(0.0, (last - first) / 3600) if first and last else 0.0
        score = min(10, max(1, messages // 10 + int(activity.get("gifs", 0)) + int(activity.get("videos", 0))))
        sender_name = " ".join(
            str(part).strip()
            for part in (getattr(getattr(event, "sender", None), "first_name", None), getattr(getattr(event, "sender", None), "last_name", None))
            if part and str(part).strip()
        ) or str(actor_id)
        score_line = f"⎋ [ امتیاز کاربر: {_math_digits(score)} از {_math_digits(10)} ]"
        text = (
            f"📆 تاریخ : 『 {_jalali_today()} 』\n\n"
            f"                 ⟣ {sender_name} ⟢\n\n"
            "     ═───────◇───────═\n\n"
            f"● تعداد پیام [ {_math_digits(messages)} ]\n\n"
            f"● تعداد گیف [ {_math_digits(activity.get('gifs', 0))} ]\n\n"
            f"● تعداد تخلف [ {_math_digits(violations)} ]\n\n"
            f"● تعداد فیلم‌ها [ {_math_digits(activity.get('videos', 0))} ]\n\n"
            f"● ساعاتی که داخل گروه فعالیت کرد [ {_math_digits(f'{active_hours:.1f}')} ]\n\n"
            f"{score_line}"
        )
        start = _u16(text[:text.index(score_line)])
        entity = _bold_entity(start, _u16(score_line))
        await _send(event, reply, text, formatting_entities=[entity] if entity else None)
        return True

    if value == "سطح گروه":
        stats = store.get_group_stats(group_id)
        messages = max(0, int(stats.get("messages", 0)))
        level = min(20, messages // 1000)
        remaining = max(0, (level + 1) * 1000 - messages) if level < 20 else 0
        done = messages - level * 1000 if level < 20 else 1000
        filled = max(0, min(10, round(done / 100)))
        users = stats.get("users", [])
        top = users[0] if users and int(users[0].get("messages", 0)) > 0 else None
        top_name = "کاربر ناشناس"
        if top:
            username = str(top.get("username") or "").strip().lstrip("@")
            top_name = f"@{username}" if username and not username.isdigit() else str(top.get("display_name") or "کاربر ناشناس")
        lines = [
            "🏆 سطح گروه\n\n",
            f"📊 سطح فعلی: {_math_digits(level)} از {_math_digits(20)}\n",
            f"💬 مجموع پیام‌ها: {_math_digits(messages)}\n",
            f"🧮 هر {_math_digits(1000)} پیام = یک سطح\n\n",
            f"👑 فعال‌ترین عضو گروه:\n{top_name} — {_math_digits(top.get('messages', 0) if top else 0)} پیام\n\n",
            f"{'▰' * filled}{'▱' * (10 - filled)}\n",
        ]
        lines.append(
            "🌟 این گروه به بالاترین سطح رسیده است!"
            if level >= 20 else f"📈 تا سطح {_math_digits(level + 1)} فقط {_math_digits(remaining)} پیام مانده."
        )
        await _send(event, reply, "".join(lines))
        return True

    if value == "راهنمای امتیاز":
        guide = (
            "🏅 راهنمای امتیاز\n\n"
            "🧩 حدس چیستان:\n+3 سکه\n\n"
            "😀 حدس ایموجی:\n+4 سکه\n\n"
            "🌍 حدس پرچم:\n+3 سکه\n\n"
            "📝 اسم فامیل (70 امتیاز یا بیشتر):\n+6 سکه\n\n"
            "✍️ تصحیح کلمات:\n+1 سکه\n\n"
            "❓ چهار گزینه‌ای:\n+3 سکه\n\n"
            "😂 بخند یا بباز:\n+1 سکه (اولین نفری که ایموجی خنده ارسال کند)\n\n"
            "🏕 بقا:\n+8 سکه (برنده نهایی)\n\n"
            "🧛 خون‌آشام:\n+7 سکه (حدس صحیح خون‌آشام)\n\n"
            "🧩 معما:\nپاسخ صحیح: ۳ سکه برنز\n\n"
            "حدس کلمه: ۴ سکه برنز\n\n"
            "🎯 بهترین جواب:\nبا ارسال بهترین و کاربردی‌ترین پاسخ به سوال، برنده انتخاب می‌شود.\n🪙 جایزه: ۲ سکه برنز\n\n"
            "⚔️ نبرد:\nبرنده: ۲ سکه برنز (اگر مساوی شود هر دو ۲ سکه برنز)\n\n"
            "📈 پایان هر روز:\n\n🥇 رتبه اول:\n+12 سکه\n\n🥈 رتبه دوم:\n+8 سکه\n\n🥉 رتبه سوم:\n+5 سکه"
        )
        await _send(event, reply, guide)
        return True

    if value == "امتیاز من":
        profile = store.get_economy_profile(group_id, actor_id)
        rank = store.group_coin_rank(group_id, actor_id)
        activity = store.get_group_user_activity(group_id, actor_id)
        first = float(activity.get("first", 0))
        membership_days = max(0, int((time.time() - first) // 86400)) if first else 0
        name = _display_name(event, actor_id)
        text = (
            "🏅 پروفایل امتیاز\n\n"
            f"ᯓ نام کاربری: {name}\n\n"
            f"🥉 برنز: ← {_math_digits(profile['bronze'])}\n\n"
            f"🥈 نقره: ← {_math_digits(profile['silver'])}\n\n"
            f"🥇 طلا: ← {_math_digits(profile['gold'])}\n\n"
            f"💎 ارزش کل: ← {_math_digits(profile['total_coin_value'])}\n\n"
            f"🏆 رتبه: ← {_math_digits(rank) if rank else 'ندارد'}\n\n"
            f"🎮 برد در بازی‌ها: ← {_math_digits(profile['wins'])}\n\n"
            f"📅 مدت عضویت: 「 {_math_digits(membership_days)} 」"
        )
        await _send(event, reply, text)
        return True

    if value in {"رتبه ها", "رتبه‌ها"}:
        ranking = store.group_coin_leaderboard(group_id, 5)
        rank_digits = ("1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟")
        entries = []
        for index, row in enumerate(ranking, 1):
            number = rank_digits[index - 1] if index <= len(rank_digits) else f"{_math_digits(index)}."
            display = str(row.get("display_name") or "").strip() or "کاربر ناشناس"
            entries.append(
                f"{number} {display} — 💎 {_math_digits(row['total_coin_value'])}\n"
                f"🥉 {_math_digits(row['bronze'])} | 🥈 {_math_digits(row['silver'])} | 🥇 {_math_digits(row['gold'])}"
            )
        text = "🏆 برترین کاربران" + ("\n\n" + "\n\n".join(entries) if entries else "\n\nندارد")
        await _send(event, reply, text)
        return True
    return False


def _choose_content(store: BotStore, group_id: str | int, category: str, items: tuple[str, ...]) -> str:
    seen = store.get_group_state(group_id, f"content_seen:{category}", [])
    _, text, next_seen = pick_new(items, seen if isinstance(seen, list) else [])
    store.set_group_state(group_id, f"content_seen:{category}", next_seen)
    return text


_NAME_MEANINGS = {
    "علی": ("عربی", "والا و بلندمرتبه"),
    "محمد": ("عربی", "ستوده‌شده"),
    "رضا": ("عربی", "خشنودی و رضایت"),
    "مریم": ("ریشهٔ کهن سامی", "معناهای آن در منابع مختلف یکسان نیست"),
    "سارا": ("عبری", "بانوی والا؛ در منابع برای آن برداشت‌های دیگری نیز آمده است"),
    "زهرا": ("عربی", "درخشان و نورانی"),
    "فاطمه": ("عربی", "در کاربرد فرهنگی با احترام و پاکی پیوند دارد"),
    "امیر": ("عربی", "فرمانده یا سرپرست"),
    "حسین": ("عربی", "نیکو و زیبا"),
    "کوروش": ("ایرانی باستان", "ریشه و معنای دقیق آن محل بحث زبان‌شناسان است"),
    "نیما": ("فارسی", "نامی نو و شناخته‌شده در فرهنگ معاصر فارسی"),
    "بهرام": ("ایرانی باستان", "نامی کهن با پیوندهای اسطوره‌ای و تاریخی"),
}


def _name_analysis(value: str) -> str | None:
    name = _normalize(value)
    if not name or len(name) > 40 or any(
        not (char.isalpha() or char in " -_") for char in name
    ):
        return None
    first = name.split()[0].lower()
    origin_meaning = _NAME_MEANINGS.get(first)
    if origin_meaning is None:
        return (
            f"🧩 تحلیل کلی نام «{name}»\n\n"
            "برای این نام اطلاعات ریشه‌شناختیِ کافی در دادهٔ محلی موجود نیست؛ "
            "برای جلوگیری از ساختن معنای نادرست، معنی قطعی اعلام نمی‌کنم.\n\n"
            "⚠️ نام به‌تنهایی شخصیت واقعی افراد را مشخص نمی‌کند؛ شخصیت به تجربه، "
            "محیط و انتخاب‌های فرد وابسته است."
        )
    origin, meaning = origin_meaning
    return (
        f"🧩 تحلیل کلی نام «{name}»\n\n"
        f"📖 ریشهٔ زبانی: {origin}\n"
        f"✨ معنی یا برداشت رایج: {meaning}\n\n"
        "⚠️ این فقط اطلاعات کلی دربارهٔ ریشه و کاربرد نام است، نه تحلیل شخصیت. "
        "شخصیت واقعی را نمی‌توان فقط از روی نام تشخیص داد."
    )


async def handle_group_level_before_late_workflow(
    event: Any,
    store: BotStore,
    *,
    group_id: str | int,
    actor_id: str | int,
    text: str,
    reply: ReplyCallback | None = None,
) -> bool:
    """Mirror the source's group-level route before late pending admin steps."""
    value = _normalize(text)
    if value != "سطح گروه":
        return False
    return await _handle_user_stats(
        event,
        store,
        group_id=group_id,
        actor_id=actor_id,
        value=value,
        reply=reply,
    )


async def _handle_game_site(
    event: Any,
    store: BotStore,
    *,
    group_id: str | int,
    actor_id: str | int,
    text: str,
    reply: ReplyCallback | None = None,
) -> bool:
    value = _normalize(text)
    if value in {"سایت بازی", "سایت", "لینک بازی", "/game", "/site"}:
        registry = store.get_group_state(group_id, "group_registry", {})
        registry = registry if isinstance(registry, dict) else {}
        if not registry.get("active"):
            await _send(
                event,
                reply,
                "❌ روباه در این گروه فعال نیست.\n"
                "برای استفاده از سایت بازی، ابتدا روباه باید با دستور «فعال» در گروه فعال‌سازی شود.",
            )
            return True
        profile = store.get_economy_profile(group_id, actor_id)
        current_bronze = int(profile.get("bronze", 0) or 0)
        if current_bronze < 20:
            await _send(
                event,
                reply,
                "❌ موجودی کافی نیست\n"
                f"موجودی فعلی شما: {_math_digits(current_bronze)} برنز\n"
                "برای دریافت سایت حداقل ۲۰ برنز نیاز دارید",
            )
            return True
        message = getattr(event, "message", None)
        message_id = getattr(message, "id", None) or getattr(event, "id", None)
        spend_reference = (
            f"game-site:{group_id}:{actor_id}:{message_id}"
            if message_id is not None
            else None
        )
        profile, outcome = store.spend_group_coins(
            group_id,
            actor_id,
            20,
            "bronze",
            reference=spend_reference,
            display_name=_display_name(event, actor_id),
            note="ورود به سایت بازی روباه",
        )
        if outcome == "insufficient":
            current_bronze = int((profile or {}).get("bronze", 0) or 0)
            await _send(
                event,
                reply,
                "❌ موجودی کافی نیست\n"
                f"موجودی فعلی شما: {_math_digits(current_bronze)} برنز\n"
                "برای دریافت سایت حداقل ۲۰ برنز نیاز دارید",
            )
            return True
        title = str(
            getattr(getattr(event, "chat", None), "title", None)
            or registry.get("title")
            or group_id
        )
        await _send(
            event,
            reply,
            "🖇لینک سایت بازی روباه\n"
            "https://fox-game.aifox-chat.workers.dev/\n\n"
            f"🔘 گروه فعال شده {title}\n\n"
            "                          ─━━━━━━⊱✿⊰━━━━━━─\n"
            "اطلاعات و رتبه‌بندی شما در سایت ذخیره می‌شود.\n"
            "                           ─━━━━━━⊱✿⊰━━━━━━─",
        )
        return True
    return False


async def handle_pre_game_public_command(
    event: Any,
    store: BotStore,
    *,
    group_id: str | int,
    actor_id: str | int,
    text: str,
    reply: ReplyCallback | None = None,
) -> bool:
    """Route source-ordered user workflows and web search before an active game."""
    value = _normalize(text)
    if not value:
        return False
    if await _handle_original(
        event, store, actor_id=actor_id, value=value, text=text, reply=reply
    ):
        return True
    if value == "بیوگرافی":
        await _send(event, reply, _choose_content(store, group_id, "biography", BIOGRAPHIES))
        return True
    if await _handle_reminder(
        event,
        store,
        group_id=group_id,
        actor_id=actor_id,
        value=value,
        text=text,
        reply=reply,
    ):
        return True
    if await _handle_translation(
        event,
        store,
        group_id=group_id,
        actor_id=actor_id,
        value=value,
        text=text,
        reply=reply,
    ):
        return True
    if value.startswith("جستجو ") and not value.startswith(_MODERATION_SEARCH_PREFIXES):
        query = value[len("جستجو "):].strip()
        if any(word.lower() in query.lower() for word in _SEARCH_BLOCKED_WORDS):
            await _send(event, reply, "🚫 جستجو این مطلب غیرمجاز است.")
            return True
        if len(query) > 400:
            await _send(event, reply, "❌ عبارت جستجو بیش از حد طولانی است.")
            return True
        allowed, wait = _search_cooldown(store, actor_id)
        if not allowed:
            await _send(event, reply, f"⏳ لطفاً {_math_digits(wait)} ثانیه صبر کنید")
            return True
        result = await asyncio.to_thread(_search_web_sync, query)
        await _send(event, reply, result)
        return True
    return await _handle_game_site(
        event, store, group_id=group_id, actor_id=actor_id, text=text, reply=reply
    )


async def handle_pre_workflow_public_command(
    event: Any,
    store: BotStore,
    *,
    group_id: str | int,
    actor_id: str | int,
    text: str,
    reply: ReplyCallback | None = None,
    photo_sender: PhotoSender | None = None,
    photo_url_sender: PhotoUrlSender | None = None,
    global_owner_check: OwnerCheck | None = None,
    bot_user_id: str | int | None = None,
    reply_message_loader: ReplyMessageLoader | None = None,
    word_filter: WordFilter | None = None,
    skip_economy: bool = False,
) -> bool:
    """Handle routes that the active dispatcher places before pending workflows."""
    value = _normalize(text)
    if not value:
        return False
    if await handle_photo_download_command(
        event,
        store,
        group_id=group_id,
        actor_id=actor_id,
        text=text,
        reply=reply,
        photo_sender=photo_sender,
        url_sender=photo_url_sender,
        owner_check=global_owner_check,
        blocked_terms=_SEARCH_BLOCKED_WORDS,
    ):
        return True
    if not value.startswith(_MODERATION_SEARCH_PREFIXES) and await _handle_factual_reply_search(
        event,
        store,
        group_id=group_id,
        actor_id=actor_id,
        text=text,
        reply=reply,
        bot_user_id=bot_user_id,
        reply_message_loader=reply_message_loader,
    ):
        return True
    if not skip_economy and await handle_economy_command(
        event,
        store,
        group_id=group_id,
        actor_id=actor_id,
        text=text,
        reply=reply,
        word_filter=word_filter,
    ):
        return True
    if await _handle_memory(
        event, store, group_id=group_id, actor_id=actor_id, value=value, reply=reply
    ):
        return True
    if value.startswith("شخصیت "):
        report = _name_analysis(value[len("شخصیت "):])
        await _send(event, reply, report or "❌ نام معتبر وارد کنید.")
        return True
    if value == "دانستنی":
        fact = _choose_content(store, group_id, "fact", FACTS)
        await _send(event, reply, f"🧠 دانستنی:\n\n{fact}")
        return True
    saved_name = store.get_group_state(group_id, f"memory:{actor_id}")
    if isinstance(saved_name, str) and saved_name:
        response = personal_reply(value, saved_name)
        if response:
            await _send(event, reply, response)
            return True
    return False


async def handle_public_command(
    event: Any,
    store: BotStore,
    *,
    group_id: str | int,
    actor_id: str | int,
    text: str,
    reply: ReplyCallback | None = None,
    photo_sender: PhotoSender | None = None,
    photo_url_sender: PhotoUrlSender | None = None,
    global_owner_check: OwnerCheck | None = None,
    status_provider: StatusProvider | None = None,
    private_message_sender: PrivateMessageSender | None = None,
    bot_user_id: str | int | None = None,
    reply_message_loader: ReplyMessageLoader | None = None,
    word_filter: WordFilter | None = None,
    name_family_service: Any | None = None,
    skip_economy: bool = False,
    route_phase: Literal["all", "post_workflow"] = "all",
) -> bool:
    """Handle the local, group-scoped public commands that need no external API."""
    if route_phase not in {"all", "post_workflow"}:
        raise ValueError("invalid public route phase")
    value = _normalize(text)
    if not value:
        return False
    if route_phase == "all" and await handle_pre_workflow_public_command(
        event,
        store,
        group_id=group_id,
        actor_id=actor_id,
        text=text,
        reply=reply,
        photo_sender=photo_sender,
        photo_url_sender=photo_url_sender,
        global_owner_check=global_owner_check,
        bot_user_id=bot_user_id,
        reply_message_loader=reply_message_loader,
        word_filter=word_filter,
        skip_economy=skip_economy,
    ):
        return True
    if route_phase == "all" and await handle_pre_game_public_command(
        event, store, group_id=group_id, actor_id=actor_id, text=text, reply=reply
    ):
        return True
    if await handle_game_command(
        event, store, group_id=group_id, actor_id=actor_id, text=text, reply=reply,
        private_message_sender=private_message_sender, word_filter=word_filter,
        name_family_service=name_family_service,
    ):
        return True
    if value in {"وضعیت ربات", "پینگ ربات"}:
        if global_owner_check is None or not global_owner_check(actor_id):
            return True
        if status_provider is None:
            await _send(event, reply, "⚠️ گزارش سلامت در این runtime در دسترس نیست.")
            return True
        try:
            await _send(event, reply, await status_provider(event))
        except Exception:  # noqa: BLE001 - status failures must not expose tracebacks
            await _send(event, reply, "❌ دریافت گزارش سلامت انجام نشد؛ بعداً دوباره تلاش کنید.")
        return True
    if value in _HELP_COMMANDS:
        await _send(
            event,
            reply,
            "🦊 راهنمای روباه\n\n"
            "برای دستورات عمومی بنویسید: لیست کاربران\n"
            "برای فرمان‌های مدیریتی بنویسید: لیست ادمینی\n"
            "برای فهرست بازی‌ها بنویسید: لیست بازی",
        )
        return True
    if value == "لیست کاربران":
        await _send(event, reply, _USER_HELP)
        return True
    if value == "لیست ادمینی":
        await _send(event, reply, _ADMIN_HELP)
        return True
    if value in _GAME_LIST_COMMANDS:
        await _send(event, reply, _GAME_LIST)
        return True
    if value == "تست دکمه":
        try:
            from splusthon import Button
        except ImportError:
            await _send(event, reply, "⚠️ دکمهٔ شیشه‌ای در این نصب SPlusthon در دسترس نیست.")
            return True
        await _send(
            event,
            reply,
            "تست دکمه شیشه‌ای",
            buttons=[[Button.inline("سلام", data=b"test_salam")]],
        )
        return True
    if value in {"جرعت", "جرات", "جرئت"}:
        dare = _choose_content(store, group_id, "dare", DARES)
        await _send(event, reply, f"🎯 جرعت:\n{dare}")
        return True
    if value in {"حقیقت", "حقیقت بگو"}:
        truth = _choose_content(store, group_id, "truth", TRUTHS)
        await _send(event, reply, f"🧠 حقیقت:\n{truth}")
        return True
    if value in {"ربات", "روباه"}:
        await _send(
            event,
            reply,
            "🦊 سلام، من روباه هستم 🤖\n\n"
            "برای آشنایی با امکانات و خدمات بیشتر، کلمه «راهنما» را ارسال کنید.",
        )
        return True
    if value == "جک":
        await _send(event, reply, _choose_content(store, group_id, "joke", JOKES))
        return True
    if await _handle_user_stats(
        event, store, group_id=group_id, actor_id=actor_id, value=value, reply=reply
    ):
        return True
    if value.startswith("فونت "):
        font_text = value[len("فونت "):].strip()
        if font_text:
            if len(font_text) > 250:
                await _send(event, reply, "❌ متن برای تبدیل فونت بیش از حد طولانی است.")
            else:
                variants = "\n\n".join(make_fonts(font_text))
                await _send(event, reply, "✨ فونت‌های ساخته شده:\n\n" + variants)
        return True
    return False
