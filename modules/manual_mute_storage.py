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


def add_manual_mute(group_id, user_id):
    """ثبت سکوت دستی (بدون حذف بقیه)."""
    g = str(normalize_group_id(group_id))
    data = _load()
    ids = data.get(g)
    if not isinstance(ids, list):
        ids = []
    uid = str(user_id)
    if uid in ids:
        return
    ids.append(uid)
    data[g] = ids
    _save(data)


def remove_manual_mute(group_id, user_id):
    """حذف سکوت دستی؛ False اگر ثبت نشده باشد."""
    g = str(normalize_group_id(group_id))
    data = _load()
    ids = data.get(g)
    if not isinstance(ids, list):
        return False
    uid = str(user_id)
    if uid not in ids:
        return False
    ids.remove(uid)
    data[g] = ids
    _save(data)
    return True


def is_manual_muted(group_id, user_id):
    try:
        data = _load()
        ids = data.get(str(normalize_group_id(group_id)))
        return isinstance(ids, list) and str(user_id) in ids
    except Exception:
        return False
