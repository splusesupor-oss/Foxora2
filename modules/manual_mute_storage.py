"""ذخیرهٔ سکوت‌های دستی به تفکیک گروه (ID-based، الگوی vip_storage).

سکوت دستی (دستور «سکوت») با مجازات خودکار ربات فرق دارد:
- رفعش فقط با «رفع سکوت» است، نه «آزاد».
- این ماژول نشان می‌دهد کدام سکوت‌ها دستی بوده‌اند تا «آزاد»
  روی آن‌ها عمل نکند.
"""
import json

from modules.runtime_paths import runtime_config_file
from modules.atomic_write import write_json
from modules.group_id import normalize_group_id

FILE = runtime_config_file("manual_mutes.json")

_cache = None
_cache_mtime = None


def _load():
    global _cache, _cache_mtime
    try:
        mtime = FILE.stat().st_mtime_ns
    except OSError:
        mtime = None
    if _cache is not None and mtime == _cache_mtime:
        return _cache
    if mtime is None:
        _cache = {}
    else:
        try:
            _cache = json.loads(FILE.read_text(encoding="utf-8"))
        except Exception:
            _cache = {}
    _cache_mtime = mtime
    return _cache


def _save(data):
    global _cache, _cache_mtime
    write_json(FILE, data, indent=2)
    _cache = data
    _cache_mtime = FILE.stat().st_mtime_ns


def add_manual_mute(group_id, user_id, username=None, display_name=None):
    """ثبت سکوت دستی (بدون حذف بقیه)."""
    g = str(normalize_group_id(group_id))
    data = _load()
    items = data.get(g)
    if not isinstance(items, list):
        items = []
    uid = str(user_id)
    # بررسی اینکه قبلاً ثبت شده یا خیر
    for idx, item in enumerate(items):
        item_uid = item.get("user_id") if isinstance(item, dict) else str(item)
        if str(item_uid) == uid:
            if isinstance(item, dict):
                if username:
                    item["username"] = username
                if display_name:
                    item["display_name"] = display_name
                items[idx] = item
                data[g] = items
                _save(data)
            return
    record = {
        "user_id": uid,
        "username": username or None,
        "display_name": display_name or None,
    }
    items.append(record)
    data[g] = items
    _save(data)


def remove_manual_mute(group_id, user_id):
    """حذف سکوت دستی؛ False اگر ثبت نشده باشد."""
    g = str(normalize_group_id(group_id))
    data = _load()
    items = data.get(g)
    if not isinstance(items, list):
        return False
    uid = str(user_id)
    found = None
    for item in items:
        item_uid = item.get("user_id") if isinstance(item, dict) else str(item)
        if str(item_uid) == uid:
            found = item
            break
    if found is None:
        return False
    items.remove(found)
    data[g] = items
    _save(data)
    return True


def is_manual_muted(group_id, user_id):
    try:
        data = _load()
        items = data.get(str(normalize_group_id(group_id)))
        if not isinstance(items, list):
            return False
        uid = str(user_id)
        for item in items:
            item_uid = item.get("user_id") if isinstance(item, dict) else str(item)
            if str(item_uid) == uid:
                return True
        return False
    except Exception:
        return False


def get_muted_users(group_id):
    """لیست افراد در حال حاضر در سکوت برای این گروه."""
    try:
        data = _load()
        items = data.get(str(normalize_group_id(group_id)))
        if not isinstance(items, list):
            return []
        result = []
        for item in items:
            if isinstance(item, dict):
                result.append(dict(item))
            else:
                result.append({
                    "user_id": str(item),
                    "username": None,
                    "display_name": None,
                })
        return result
    except Exception:
        return []


def clear_manual_mutes(group_id):
    """پاک کردن تمام افراد از لیست سکوت این گروه."""
    g = str(normalize_group_id(group_id))
    data = _load()
    if g not in data:
        return 0
    count = len(data[g]) if isinstance(data[g], list) else 0
    data.pop(g, None)
    _save(data)
    return count
