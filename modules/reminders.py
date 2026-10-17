"""Persistent user reminders for Soroush Plus chats."""
import json
import re
import time
from pathlib import Path

from modules.runtime_paths import runtime_config_file
from modules.atomic_write import write_json

FILE = runtime_config_file("reminders.json")
_WAITING = {}
_WAITING_TTL = 10 * 60
_WAITING_MAX = 2000
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")


def _load():
    try:
        return json.loads(FILE.read_text(encoding="utf-8")) if FILE.exists() else []
    except Exception:
        return []


def _save(items):
    write_json(FILE, items, indent=2)


def _prune_waiting(now=None):
    now = time.monotonic() if now is None else now
    for key, state in list(_WAITING.items()):
        if now - float(state.get("_ts", 0)) > _WAITING_TTL:
            _WAITING.pop(key, None)
    while len(_WAITING) > _WAITING_MAX:
        _WAITING.pop(next(iter(_WAITING)), None)


def begin(chat_id, user_id, display_name):
    _prune_waiting()
    _WAITING[(str(chat_id), str(user_id))] = {
        "name": display_name, "_ts": time.monotonic(),
    }


def waiting(chat_id, user_id):
    _prune_waiting()
    return (str(chat_id), str(user_id)) in _WAITING


def capture(chat_id, user_id, text):
    _prune_waiting()
    state = _WAITING.get((str(chat_id), str(user_id)))
    if not state:
        return None
    normalized = text.translate(_DIGITS).strip()
    match = re.match(r"^(\d+)\s*(دقیقه|ساعت|روز)\s+(.+)$", normalized, re.DOTALL)
    if not match:
        return False
    amount = int(match.group(1))
    unit = match.group(2)
    reminder_text = match.group(3).strip()
    if amount <= 0 or not reminder_text:
        return False
    seconds = amount * {"دقیقه": 60, "ساعت": 3600, "روز": 86400}[unit]
    item = {
        "id": f"{chat_id}:{user_id}:{time.time_ns()}",
        "chat_id": str(chat_id),
        "user_id": str(user_id),
        "name": state.get("name") or "Unknown User",
        "text": reminder_text,
        "due_at": time.time() + seconds,
        "time_label": f"{amount} {unit} دیگر",
    }
    items = _load()
    items.append(item)
    _save(items)
    _WAITING.pop((str(chat_id), str(user_id)), None)
    return item


def due():
    now = time.time()
    return [item for item in _load() if item.get("due_at", 0) <= now]


def mark_sent(reminder_id):
    items = _load()
    remaining = [item for item in items if item.get("id") != reminder_id]
    if len(remaining) != len(items):
        _save(remaining)
        return True
    return False
