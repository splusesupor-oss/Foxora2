"""زیرساخت مشترک بازی‌های Fox AI.

این فایل فقط «ابزار» می‌دهد؛ هیچ state سراسری‌ای نگه نمی‌دارد. هر بازی یک
نمونهٔ ``SessionStore`` مخصوص خودش می‌سازد، پس حافظهٔ بازی‌ها از هم و از
بازی‌های قدیمی ربات کاملاً جداست.
"""
import asyncio
import time
from itertools import count
from modules.user_display import format_user


class SessionStore:
    """نگهدارندهٔ session یک بازی، به تفکیک چت.

    قفل هر چت تضمین می‌کند دو دستور هم‌زمان نتوانند دو session بسازند
    (جلوگیری از Race Condition).
    """

    def __init__(self, name):
        self.name = name
        self._sessions = {}
        self._locks = {}
        self._tasks = {}
        self._sequence = count(1)
        self._finished = set()

    # ---------------- قفل ----------------
    def lock(self, chat_id):
        lock = self._locks.get(chat_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[chat_id] = lock
        return lock

    # ---------------- چرخهٔ حیات ----------------
    def is_active(self, chat_id):
        return chat_id in self._sessions

    def get(self, chat_id):
        return self._sessions.get(chat_id)

    def new_id(self):
        return next(self._sequence)

    def create(self, chat_id, data):
        """session تازه می‌سازد؛ None اگر بازی همین حالا فعال باشد."""
        if chat_id in self._sessions:
            return None
        session = dict(data)
        session.setdefault("session_id", self.new_id())
        session.setdefault("chat_id", chat_id)
        session.setdefault("created_at", time.monotonic())
        self._sessions[chat_id] = session
        return session

    def close(self, chat_id, session_id=None):
        """session را می‌بندد. تنها یک بار برای هر session موفق می‌شود."""
        session = self._sessions.get(chat_id)
        if not session:
            return None
        if session_id is not None and session["session_id"] != session_id:
            return None
        if session["session_id"] in self._finished:
            return None
        self._sessions.pop(chat_id, None)
        self._finished.add(session["session_id"])
        if len(self._finished) > 500:
            self._finished.clear()
            self._finished.add(session["session_id"])
        return session

    def is_finished(self, session_id):
        return session_id in self._finished

    # ---------------- تایمر ----------------
    def cancel_task(self, chat_id):
        task = self._tasks.pop(chat_id, None)
        if task is not None and not task.done():
            task.cancel()
        return task

    def schedule(self, chat_id, coro_factory):
        """یک task مخصوص این بازی می‌سازد و مالکیتش را نگه می‌دارد."""
        self.cancel_task(chat_id)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return None
        task = loop.create_task(coro_factory())
        self._tasks[chat_id] = task

        def _cleanup(done):
            if self._tasks.get(chat_id) is done:
                self._tasks.pop(chat_id, None)

        task.add_done_callback(_cleanup)
        return task

    def task_for(self, chat_id):
        return self._tasks.get(chat_id)

    # ---------------- تست/ری‌استارت ----------------
    def reset(self, chat_id=None):
        if chat_id is None:
            for task in list(self._tasks.values()):
                if not task.done():
                    task.cancel()
            self._tasks.clear()
            self._sessions.clear()
            self._finished.clear()
            self._locks.clear()
            return
        self.cancel_task(chat_id)
        self._sessions.pop(chat_id, None)
        self._locks.pop(chat_id, None)


def display_name(user):
    """نام بازیکن با سیاست مشترک username-first."""
    return format_user(user)


def username_tag(user):
    username = getattr(user, "username", None)
    return f"@{str(username).lstrip('@')}" if username else ""


PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
_DIGIT_MAP = {ord(p): str(i) for i, p in enumerate(PERSIAN_DIGITS)}
_DIGIT_MAP.update({ord(a): str(i) for i, a in enumerate("٠١٢٣٤٥٦٧٨٩")})


def to_english_digits(text):
    return str(text or "").translate(_DIGIT_MAP)


def to_persian_digits(value):
    return "".join(PERSIAN_DIGITS[int(ch)] if ch.isdigit() else ch
                   for ch in str(value))


def parse_int(text):
    """عدد فارسی یا انگلیسی را می‌خواند؛ None اگر عدد نباشد."""
    cleaned = to_english_digits(text).strip()
    if not cleaned or not cleaned.lstrip("-").isdigit():
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None


def normalize_text(value):
    """یکسان‌سازی متن فارسی برای مقایسهٔ پاسخ."""
    text = str(value or "").strip().lower()
    for source, target in (
        ("\u200c", " "), ("\u200f", ""), ("\u200e", ""), ("\ufeff", ""),
        ("ي", "ی"), ("ك", "ک"), ("أ", "ا"), ("إ", "ا"), ("آ", "ا"),
        ("ة", "ه"), ("ؤ", "و"),
    ):
        text = text.replace(source, target)
    return " ".join(text.split())


def log(logger, message):
    if logger is not None:
        try:
            logger.log_info(message)
        except Exception:
            pass


def log_error(logger, message):
    if logger is not None:
        try:
            logger.log_error(message)
        except Exception:
            pass
