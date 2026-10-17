import json
import os

from modules.runtime_paths import runtime_log_file

from modules.group_id import normalize_group_id
from datetime import datetime

FILE = str(runtime_log_file("group_stats.json", migrate=True))
# 🛟 نسخهٔ پشتیبانِ «آخرین وضعیت سالم». اگر فایل اصلی خراب شود
# (kill وسط نوشتن، تداخل git stash/pop و...) از این بازیابی می‌شود
# تا شمارش پیام‌ها — و در نتیجه «سطح گروه» — هرگز صفر نشود.
BACKUP_FILE = FILE + ".bak"

_stats_cache = None
_stats_cache_mtime = None
_stats_dirty = False

def _file_mtime():
    try:
        return os.stat(FILE).st_mtime_ns
    except OSError:
        return None


def _read_json(path):
    """خواندن امن؛ None اگر فایل نبود یا JSON خراب بود."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def load_stats():
    global _stats_cache, _stats_cache_mtime, _stats_dirty
    mtime = _file_mtime()
    if _stats_cache is not None and mtime == _stats_cache_mtime:
        return _stats_cache

    data = _read_json(FILE)
    if data is None:
        # ⚠️ فایل اصلی گم/خراب است. قبلاً اینجا بی‌صدا {} برمی‌گشت و
        # flush بعدی همان {} را روی دیسک می‌نوشت — یعنی کل شمارش
        # پیام‌ها و سطح گروه‌ها یک‌شبه صفر می‌شد. حالا از آخرین نسخهٔ
        # سالم بازیابی می‌کنیم و dirty می‌شود تا فایل اصلی ترمیم شود.
        backup = _read_json(BACKUP_FILE)
        if backup is not None:
            _stats_cache = backup
            _stats_dirty = True
        else:
            _stats_cache = {}
    else:
        _stats_cache = data

    _stats_cache_mtime = mtime
    return _stats_cache


def save_stats(data):
    """به‌روزرسانی حافظه؛ flush دوره‌ای از نوشتن برای هر پیام جلوگیری می‌کند."""
    global _stats_cache, _stats_dirty
    _stats_cache = data
    _stats_dirty = True


def flush():
    global _stats_cache_mtime, _stats_dirty
    if not _stats_dirty:
        return False
    os.makedirs(os.path.dirname(FILE) or ".", exist_ok=True)
    payload = json.dumps(_stats_cache or {}, ensure_ascii=False,
                         separators=(",", ":"))
    # ✍️ نوشتن اتمیک (temp + replace): اگر پروسه وسط نوشتن kill شود
    # (pkill ری‌استارت روزانه، OOM، خاموشی)، فایل نیمه‌کاره و خراب
    # باقی نمی‌ماند — قبلاً همین باعث صفر شدن سطح گروه‌ها می‌شد.
    temp_path = FILE + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        f.write(payload)
    os.replace(temp_path, FILE)
    # نسخهٔ پشتیبان همیشه از همین دادهٔ سالمِ در حافظه نوشته می‌شود
    # (نه با جابه‌جایی فایل قبلی که ممکن است خراب باشد).
    try:
        backup_temp = BACKUP_FILE + ".tmp"
        with open(backup_temp, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(backup_temp, BACKUP_FILE)
    except OSError:
        pass
    _stats_cache_mtime = _file_mtime()
    _stats_dirty = False
    return True


def ensure_group(data, chat_id):
    chat_id = normalize_group_id(chat_id)

    if chat_id not in data:
        data[chat_id] = {
            "messages": 0,
            "deleted": 0,
            "kicked": 0,
            "muted": 0,
            "users": {}
        }

    return data[chat_id]


def ensure_user(group, user_id, username=""):
    user_id = str(user_id)

    if user_id not in group["users"]:
        group["users"][user_id] = {
            "username": username or "unknown",
            "messages": 0,
            "deleted": 0
        }

    elif username:
        group["users"][user_id]["username"] = username

    return group["users"][user_id]


def add_message(chat_id, user_id, username=""):
    data = load_stats()

    group = ensure_group(data, chat_id)
    user = ensure_user(group, user_id, username)

    group["messages"] += 1
    user["messages"] += 1

    save_stats(data)


def add_deleted(chat_id, user_id, username=""):
    add_deleted_count(chat_id, user_id, username)


def add_deleted_count(chat_id, user_id, username="", count=1):
    data = load_stats()

    group = ensure_group(data, chat_id)
    user = ensure_user(group, user_id, username)
    count = max(0, int(count))

    group["deleted"] += count
    user["deleted"] += count

    save_stats(data)


def add_kick(chat_id):
    data = load_stats()

    group = ensure_group(data, chat_id)
    group["kicked"] += 1

    save_stats(data)


def add_mute(chat_id):
    data = load_stats()

    group = ensure_group(data, chat_id)
    group["muted"] += 1

    save_stats(data)


def get_stats(chat_id):
    data = load_stats()
    return data.get(normalize_group_id(chat_id), {
        "messages": 0,
        "deleted": 0,
        "kicked": 0,
        "muted": 0,
        "members": 0,
        "users": {}
    })


def top_users(chat_id, limit=10):
    group = get_stats(chat_id)

    users = group.get("users", {})

    result = sorted(
        users.items(),
        key=lambda x: x[1].get("messages", 0),
        reverse=True
    )

    return result[:limit]


def make_report(chat_id, member_count=0):
    group = get_stats(chat_id)

    text = (
        "📊 **آمار گروه**\n\n"
        "💬 **کل پیام‌ها:** "
        f"{group['messages']}\n\n"

        "🗑 **پیام حذف شده:** "
        f"{group['deleted']}\n\n"

        "🚪 **اخراج شده:** "
        f"{group['kicked']}\n\n"

        "🔇 **سکوت شده:** "
        f"{group['muted']}\n\n"

        "👥 **تعداد اعضا:** "
        f"{member_count}\n\n"

        "🏆 **کاربران فعال:**\n\n"
    )

    for i, (_, user) in enumerate(top_users(chat_id), 1):
        name = user.get("username", "unknown")
        count = user.get("messages", 0)
        text += f"{i}️⃣ @{name} - {count} پیام\n\n"

    return text

