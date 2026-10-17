"""تشخیصِ مستقیمِ رباتِ دیگر در گروه و غیرفعال‌سازیِ روباه.

قانونِ اصلی:
  - «ربات بودن» از فیلدِ مستقیمِ API (``User.bot``) تعیین می‌شود؛ هیچ حدسی بر
    اساسِ سرعتِ پیام، نامِ کاربری یا بیوگرافی زده نمی‌شود. بنابراین کاربرِ عادی
    هرگز به‌اشتباه ربات تشخیص داده نمی‌شود.
  - در پیامِ گروه، آبجکتِ sender اغلب یک entityِ خلاصه است که ``bot`` آن
    ``None`` است (پرنشده). مسیر داغ ``get_entity`` نمی‌زند تا سندر مشترک
    قفل نشود؛ entity ناقص انسان فرض می‌شود. فقط با ``allow_rpc=True``
    entity کامل خوانده می‌شود. فقط اگر ``bot is True`` باشد ربات اعلام می‌شود.
  - حسابِ خودِ روباه هرگز هدفِ این ماژول قرار نمی‌گیرد.

وضعیتِ هر گروهِ غیرفعال‌شده در ``config/bot_disabled_groups.json`` ذخیره
می‌شود تا بعد از هر پیام روباه دوباره فعال نشود و پیامِ اطلاع‌رسانی فقط یک
بار ارسال شود.
"""
import json
from datetime import datetime
from pathlib import Path

from modules.runtime_paths import CONFIG_DIR
from modules.atomic_write import write_json

_BASE = CONFIG_DIR
_FILE = _BASE / "bot_disabled_groups.json"

# دستورِ مجاز برای فعال‌سازیِ دوباره (مالک/ادمین).
REENABLE_COMMAND = "فعال کردن روباه"

# کشِ درون‌حافظه‌ایِ وضعیتِ قطعیِ نوعِ حسابِ هر user_id (برای پرهیز از
# fetchِ تکراری روی هر پیام).
_KNOWN_BOT_IDS = set()    # قطعاً ربات
_KNOWN_HUMAN_IDS = set()  # قطعاً کاربرِ عادی
_KNOWN_MAX = 4000
# Disabled-group map.  is_disabled() used to parse the JSON file on every
# group message; keep it in memory and only reread when the file changes.
_DISABLED = None
_DISABLED_MTIME = None


def _load():
    try:
        if _FILE.exists():
            data = json.loads(_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        pass
    return {}


def _disabled_map():
    """Return the disabled-groups dict without reading disk on every call."""
    global _DISABLED, _DISABLED_MTIME
    try:
        mtime = _FILE.stat().st_mtime if _FILE.exists() else -1
    except OSError:
        mtime = -1
    if _DISABLED is not None and _DISABLED_MTIME == mtime:
        return _DISABLED
    _DISABLED = _load() if mtime != -1 else {}
    _DISABLED_MTIME = mtime
    return _DISABLED


def _save(data):
    global _DISABLED, _DISABLED_MTIME
    try:
        _BASE.mkdir(parents=True, exist_ok=True)
        write_json(_FILE, data, indent=2)
        _DISABLED = data if isinstance(data, dict) else {}
        try:
            _DISABLED_MTIME = _FILE.stat().st_mtime
        except OSError:
            _DISABLED_MTIME = None
    except OSError:
        pass


def is_bot_sender(user):
    """آیا فرستنده یک حسابِ رباتِ واقعی است (فقط با فیلدِ مستقیمِ API)؟

    بر اساسِ ``User.bot``:
      - ``bot is True`` → ربات.
      - ``bot is False`` → کاربرِ عادی (قطعی).
      - ``bot is None`` (entityِ ناقص) → نامشخص؛ با ``resolve_is_bot`` باید
        entityِ کامل گرفته شود. این تابعِ ساده فقط برای مقدارِ قطعیِ True است.
    """
    if user is None:
        return False
    return getattr(user, "bot", None) is True


async def resolve_is_bot(client, user, user_id, *, allow_rpc=False):
    """تشخیص ربات بودن بدون RPC روی مسیر داغ.

    ترتیب:
      1. کش قطعیِ قبلی.
      2. فیلد ``user.bot`` اگر پر باشد.
      3. entity ناقص (``bot is None``): روی مسیر پیام ``get_entity``
         صدا نمی‌شود — آن RPC سندر مشترک را چند ثانیه قفل می‌کرد.
         اگر ``allow_rpc=True`` باشد (مسیر غیر داغ/تست) entity کامل
         گرفته می‌شود.

    خروجی: bool.
    """
    if user_id in _KNOWN_BOT_IDS:
        return True
    if user_id in _KNOWN_HUMAN_IDS:
        return False
    if user is not None and getattr(user, "bot", None) is not None:
        _remember(user_id, user.bot)
        return bool(user.bot)
    if not allow_rpc or client is None:
        return False
    try:
        full = await client.get_entity(user if user is not None else user_id)
    except Exception:
        return False
    is_bot = getattr(full, "bot", None) is True
    _remember(user_id, is_bot)
    return is_bot


def _remember(user_id, is_bot):
    if user_id is None:
        return
    target = _KNOWN_BOT_IDS if is_bot else _KNOWN_HUMAN_IDS
    target.add(user_id)
    if len(target) > _KNOWN_MAX:
        target.pop()


def display(user):
    """نامِ نمایشیِ ربات: @username در اولویت، بعد نامِ نمایشی."""
    if user is None:
        return "ربات"
    username = getattr(user, "username", None)
    if username:
        return "@" + str(username).lstrip("@")
    first = getattr(user, "first_name", None) or ""
    last = getattr(user, "last_name", None) or ""
    name = " ".join(part for part in (first, last) if part).strip()
    return name or "ربات"


def disable_for_bot(chat_id, bot_user):
    """گروه را ثبت و بلافاصله قابل‌خواندن بودنِ وضعیت را تأیید می‌کند.

    مقدار بازگشتی فقط وقتی ``True`` است که ذخیره‌سازی موفق شده و همان شناسه
    گروه از فایل دوباره خوانده شود؛ در غیر این صورت caller نباید گروه را
    خاموش‌شده اعلام یا پیام موفقیت ارسال کند.
    """
    data = _load()
    key = str(chat_id)
    data[key] = {
        "bot_id": getattr(bot_user, "id", None),
        "bot_name": display(bot_user),
        "disabled_at": datetime.now().isoformat(timespec="seconds"),
    }
    try:
        _save(data)
        return key in _load()
    except Exception:
        return False


def is_disabled(chat_id):
    """آیا این گروه به دلیلِ رباتِ دیگر غیرفعال شده است؟"""
    return str(chat_id) in _disabled_map()


def reenable(chat_id):
    """فعال‌سازیِ دوبارهٔ روباه در این گروه (حذفِ وضعیتِ غیرفعال)."""
    data = _load()
    if str(chat_id) in data:
        del data[str(chat_id)]
        _save(data)
        return True
    return False


def disabled_bot_name(chat_id):
    return _disabled_map().get(str(chat_id), {}).get("bot_name", "ربات")


def sender_dump(user):
    """نمایشِ کاملِ دادهٔ sender برای لاگِ تشخیصی (هرآنچه API داده است)."""
    if user is None:
        return repr(None)
    try:
        to_dict = getattr(user, "to_dict", None)
        if callable(to_dict):
            return json.dumps(to_dict(), ensure_ascii=False, default=str)
    except Exception:
        pass
    try:
        return json.dumps(vars(user), ensure_ascii=False, default=str)
    except Exception:
        return repr(user)

