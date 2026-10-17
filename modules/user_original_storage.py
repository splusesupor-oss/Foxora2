"""ذخیره‌سازی مستقل اصل/لقب شخصی کاربران."""
import json
import time
from pathlib import Path

from modules.runtime_paths import runtime_config_file
from modules.atomic_write import write_json


FILE = runtime_config_file("user_originals.json")
_pending_users = {}
_PENDING_TTL = 10 * 60
_PENDING_MAX = 2000


def _prune_pending(now=None):
    now = time.monotonic() if now is None else now
    for user_id, created in list(_pending_users.items()):
        if now - float(created) > _PENDING_TTL:
            _pending_users.pop(user_id, None)
    while len(_pending_users) > _PENDING_MAX:
        _pending_users.pop(next(iter(_pending_users)), None)


def load_originals():
    if not FILE.exists():
        return {}
    try:
        return json.loads(FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_originals(data):
    write_json(FILE, data, indent=2)


def begin_registration(user_id):
    _prune_pending()
    _pending_users[str(user_id)] = time.monotonic()


def is_waiting_for_original(user_id):
    _prune_pending()
    return str(user_id) in _pending_users


def save_original(user_id, original):
    data = load_originals()
    data[str(user_id)] = original.strip()
    save_originals(data)
    _pending_users.pop(str(user_id), None)


def get_original(user_id):
    return load_originals().get(str(user_id))
