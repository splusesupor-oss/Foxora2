import json
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from modules.runtime_paths import runtime_log_file

from modules.group_id import normalize_group_id

FILE = runtime_log_file("user_map.json", migrate=True)

# ⚡️ کش mtime + نوشتن غیرمسدودکننده — قبلاً «هر تخلف» فایل را کامل
# می‌خواند و با indent همگام روی حلقهٔ رویداد می‌نوشت؛ در موج اسپم همین
# نوشتن‌های مکرر روی حافظهٔ کند گوشی، ربات را تکه‌تکه بلاک می‌کرد.
_WRITER = ThreadPoolExecutor(max_workers=1, thread_name_prefix="user-map-save")
_cache = None
_cache_mtime = None
# تا وقتی نوشتنی در صف نخ نویسنده است، کشِ حافظه مرجع است؛ وگرنه
# load ممکن بود دیسکِ عقب‌مانده را جدیدتر ببیند و کش تازه را پاک کند.
_pending_writes = 0


def _file_mtime():
    try:
        return FILE.stat().st_mtime_ns
    except OSError:
        return None


def load_map():
    global _cache, _cache_mtime
    if _cache is not None and _pending_writes > 0:
        return _cache
    mtime = _file_mtime()
    if _cache is not None and mtime == _cache_mtime:
        return _cache
    if mtime is None:
        _cache = {}
    else:
        try:
            data = json.loads(FILE.read_text(encoding="utf-8"))
            _cache = data if isinstance(data, dict) else {}
        except Exception:
            _cache = {}
    _cache_mtime = mtime
    return _cache


def _write_payload(payload):
    """نوشتن اتمیک (temp + replace)؛ فقط داخل نخ نویسنده."""
    global _cache_mtime, _pending_writes
    temp_path = None
    try:
        FILE.parent.mkdir(parents=True, exist_ok=True)
        handle, temp_path = tempfile.mkstemp(
            dir=str(FILE.parent), suffix=".tmp")
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(payload)
        os.replace(temp_path, FILE)
        temp_path = None
        _cache_mtime = _file_mtime()
    except OSError:
        if temp_path is not None:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
    finally:
        _pending_writes = max(0, _pending_writes - 1)


def save_map(data):
    global _cache, _pending_writes
    _cache = data
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    _pending_writes += 1
    try:
        _WRITER.submit(_write_payload, payload)
    except Exception:
        _write_payload(payload)


def save_user(group_id, username, user_id):
    if not username:
        return

    data = load_map()

    gid = normalize_group_id(group_id)
    uname = username.replace("@", "").lower()

    if gid not in data:
        data[gid] = {}

    # اگر همان مقدار قبلی است، هیچ نوشتنی لازم نیست (مسیر داغ تخلف).
    if data[gid].get(uname) == str(user_id):
        return

    data[gid][uname] = str(user_id)

    save_map(data)


def find_user(username):
    username = username.replace("@", "").lower()

    data = load_map()

    for gid, users in data.items():
        if username in users:
            return int(gid), int(users[username])

    return None, None
