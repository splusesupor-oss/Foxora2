"""💾 پیشرفت دائمی بازی‌ها، به تفکیک گروه و کاربر.

پیش‌تر پیشرفت «حدس ایموجی» فقط در یک dict داخل حافظه بود، پس با هر
ری‌استارت، کرش یا آپدیت از بین می‌رفت و کاربر دوباره از مرحلهٔ ۱ شروع
می‌کرد.

اینجا پیشرفت داخل همان فایل اقتصاد (``config/economy.json``) نگه داشته
می‌شود، زیر کلید ``game_progress``:

    game_progress
      └── "<chat_key>"
            └── "<user_id>"
                  └── "<game>"
                        └── ["پاسخ۱", "پاسخ۲", ...]

هر گروه دفتر جدا دارد، دقیقاً مثل کیف پول؛ پس پیشرفت یک کاربر در یک
گروه روی گروه دیگر اثر نمی‌گذارد.

نوشتن با ``defer=True`` انجام می‌شود تا مسیر داغ کند نشود؛ حلقهٔ دوره‌ای
ربات آن را روی دیسک می‌نشاند. برای اطمینان، ``mark_seen`` بلافاصله هم
flush می‌کند چون از دست رفتن یک مرحله برای کاربر آزاردهنده است.
"""
import asyncio
import time

from economy import storage
from economy.coins import accounts

ROOT = "game_progress"
RECENT = "game_recent"
CYCLE_KEY = "__cycle__"


def _chat(chat_id):
    return accounts.chat_key(chat_id)


def _bucket(data, chat_id, user_id, game, *, create=False):
    root = data.setdefault(ROOT, {}) if create else data.get(ROOT, {})
    chat = root.setdefault(_chat(chat_id), {}) if create \
        else root.get(_chat(chat_id), {})
    user = chat.setdefault(str(user_id), {}) if create \
        else chat.get(str(user_id), {})
    if create:
        return user.setdefault(game, [])
    return user.get(game, [])


def seen(chat_id, user_id, game):
    """مجموعهٔ مرحله‌هایی که این کاربر در این گروه دیده است."""
    # فقط همین سطل خوانده می‌شود، نه کل فایل. ``snapshot()`` کل داده را
    # deepcopy می‌کرد و هزینه‌اش با رشد فایل خطی بالا می‌رفت.
    bucket = storage.read_path(ROOT, _chat(chat_id), str(user_id), game,
                               default=[])
    return {str(item) for item in bucket}


def seen_count(chat_id, user_id, game):
    return len(seen(chat_id, user_id, game))


def has_seen(chat_id, user_id, game, item):
    return str(item) in seen(chat_id, user_id, game)


def mark_seen(chat_id, user_id, game, item):
    """یک مرحله را «دیده‌شده» ثبت می‌کند.

    خروجی: تعداد کل مرحله‌های دیده‌شده پس از ثبت.

    ⚠️ اینجا عمداً ``storage.flush()`` صدا زده نمی‌شود.

    قبلاً بعد از هر ثبت، کل ``economy.json`` به صورت همگام روی دیسک
    نوشته می‌شد. اندازه‌گیری واقعی نشان داد این کار حلقهٔ رویداد را
    بلاک می‌کند و هزینه‌اش با بزرگ شدن فایل خطی رشد می‌کند
    (۲۷KB → ۳ms، ۱۳۶KB → ۱۴ms برای هر سوال)؛ یعنی ربات هرچه بیشتر کار
    می‌کرد کندتر می‌شد و با ری‌استارت دوباره سریع می‌شد.

    داده از دست نمی‌رود: تراکنش با ``defer=True`` کش را به‌روز و
    «کثیف» علامت می‌زند و حلقهٔ دوره‌ای در ``core`` هر ۱۵ ثانیه با
    ``asyncio.to_thread(flush_economy)`` آن را *خارج از* حلقهٔ رویداد
    روی دیسک می‌نشاند. برای نقاط حساس، ``flush_now()`` در دسترس است.

    برای اینکه یک کرشِ ناگهانی پیشرفت را نبلعد، نوشتن با فاصلهٔ
    ``WRITE_DEBOUNCE_SECONDS`` انجام می‌شود: نوشتنِ پشت‌سرهم حذف
    می‌شود ولی داده بیش از این مدت روی دیسک ننشسته نمی‌ماند.
    """
    value = str(item)
    with storage.transaction(defer=True) as data:
        bucket = _bucket(data, chat_id, user_id, game, create=True)
        if value not in bucket:
            bucket.append(value)
        total = len(bucket)
    _maybe_flush()
    return total


# فاصلهٔ کمینهٔ نوشتن روی دیسک از مسیر داغ.
#
# صفر یعنی همان رفتار قدیمی: نوشتن همگام بعد از *هر* ثبت، که با رشد
# فایل حلقهٔ رویداد را ثانیه‌ها بلاک می‌کرد. عددی بزرگ یعنی ریسک از
# دست رفتن پیشرفت هنگام کرش. یک ثانیه هر دو را می‌پوشاند: در یک
# رگبار پیام فقط یک بار نوشته می‌شود، و بیشترین چیزی که با کرش از
# دست می‌رود کار همان یک ثانیه است.
WRITE_DEBOUNCE_SECONDS = 1.0

_last_write = 0.0


def _maybe_flush(force=False):
    """پیشرفت را ماندگار می‌کند، بدون بلاک کردن حلقهٔ رویداد.

    اگر حلقهٔ رویدادی در حال اجراست، نوشتن به یک thread سپرده می‌شود
    (``json.dump`` کار I/O است و GIL را در زمان نوشتن آزاد می‌کند)، پس
    پردازش پیام‌ها متوقف نمی‌شود. بیرون از حلقهٔ رویداد — مثل تست‌ها و
    اسکریپت‌ها — همان‌جا و همگام نوشته می‌شود تا رفتار قابل پیش‌بینی
    بماند.

    خارج از حلقهٔ رویداد هیچ debounce ای اعمال نمی‌شود: آنجا هزینه‌ای
    برای پاسخ‌گویی ربات ندارد و در عوض تضمین می‌کند پیشرفت بلافاصله
    ماندگار شود (کرش نباید مرحلهٔ شروع‌شده را ببلعد). debounce فقط
    داخل حلقهٔ رویداد معنا دارد، جایی که هدفش نریختن رگبار نوشتن روی
    یک thread است.
    """
    global _last_write

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is None:
        _last_write = time.monotonic()
        try:
            return storage.flush()
        except Exception:
            return False

    now = time.monotonic()
    if not force and (now - _last_write) < WRITE_DEBOUNCE_SECONDS:
        return False
    _last_write = now

    # داخل حلقهٔ رویداد: نوشتن در پس‌زمینه، بدون انتظار.
    task = loop.create_task(asyncio.to_thread(_safe_flush))
    _BACKGROUND_WRITES.add(task)
    task.add_done_callback(_BACKGROUND_WRITES.discard)
    return True


# ارجاع نگه داشته می‌شود تا تسک پیش از پایان جمع‌آوری نشود.
_BACKGROUND_WRITES = set()


def _safe_flush():
    try:
        return storage.flush()
    except Exception:
        return False


def flush_now():
    """نوشتن فوری روی دیسک، بدون توجه به فاصلهٔ debounce."""
    return _maybe_flush(force=True)


def reset(chat_id, user_id, game):
    """پیشرفت این کاربر در این بازی و همین گروه را پاک می‌کند."""
    with storage.transaction() as data:
        root = data.get(ROOT, {})
        chat = root.get(_chat(chat_id), {})
        user = chat.get(str(user_id), {})
        had = bool(user.get(game))
        user.pop(game, None)
        if not user:
            chat.pop(str(user_id), None)
        if not chat:
            root.pop(_chat(chat_id), None)
        return had


def reset_game_everywhere(game):
    """پیشرفت یک بازی را برای همهٔ کاربران پاک می‌کند — فقط برای تست.

    فهرست «تازه‌مصرف‌شدهٔ» گروه‌ها هم پاک می‌شود؛ در غیر این صورت یک
    پنجرهٔ کهنه باقی می‌ماند و انتخاب معما را بی‌دلیل محدود می‌کند.
    """
    with storage.transaction() as data:
        root = data.get(ROOT, {})
        for chat in list(root.values()):
            for user in list(chat.values()):
                user.pop(game, None)
                user.pop(f"{game}{CYCLE_KEY}", None)
        for chat in list(data.get(RECENT, {}).values()):
            chat.pop(game, None)


# ---------------------------------------------------------------------------
# مرحله‌های «تازه استفاده‌شده» در کل گروه
#
# بدون این، کاربر تازه‌وارد ممکن بود همان معمایی را بگیرد که چند لحظه
# پیش کاربر دیگری در همان گروه جواب داده بود، پس بقیه جواب را می‌دانستند.
# ---------------------------------------------------------------------------
def recent(chat_id, game):
    bucket = storage.read_path(RECENT, _chat(chat_id), game, default=[])
    return [str(item) for item in bucket]


def mark_recent(chat_id, game, item, limit):
    """مرحله را در فهرست «تازه استفاده‌شدهٔ گروه» ثبت می‌کند.

    فهرست کوتاه نگه داشته می‌شود (``limit``) تا با مصرف شدن بانک، بازی
    قفل نشود.
    """
    value = str(item)
    with storage.transaction(defer=True) as data:
        chat = data.setdefault(RECENT, {}).setdefault(_chat(chat_id), {})
        bucket = chat.setdefault(game, [])
        if value in bucket:
            bucket.remove(value)
        bucket.append(value)
        if limit > 0 and len(bucket) > limit:
            del bucket[:-limit]
        size = len(bucket)
    _maybe_flush()
    return size


def clear_recent(chat_id, game):
    with storage.transaction() as data:
        chat = data.get(RECENT, {}).get(_chat(chat_id), {})
        chat.pop(game, None)


# ---------------------------------------------------------------------------
# دور (cycle): وقتی کاربر همهٔ مرحله‌ها را دید، دور تازه شروع می‌شود
# ---------------------------------------------------------------------------
def cycle(chat_id, user_id, game):
    value = storage.read_path(ROOT, _chat(chat_id), str(user_id),
                              f"{game}{CYCLE_KEY}", default=0)
    return int(value or 0)


def start_new_cycle(chat_id, user_id, game):
    """تاریخچهٔ کاربر را خالی و شمارندهٔ دور را یکی زیاد می‌کند."""
    with storage.transaction() as data:
        root = data.setdefault(ROOT, {})
        chat = root.setdefault(_chat(chat_id), {})
        user = chat.setdefault(str(user_id), {})
        user[game] = []
        current = int(user.get(f"{game}{CYCLE_KEY}", 0) or 0) + 1
        user[f"{game}{CYCLE_KEY}"] = current
    try:
        storage.flush()
    except Exception:
        pass
    return current


def all_users(chat_id, game):
    """کاربران این گروه که در این بازی پیشرفتی دارند."""
    chat = storage.read_path(ROOT, _chat(chat_id), default={})
    return {user_id: len(games.get(game, []))
            for user_id, games in chat.items() if games.get(game)}
