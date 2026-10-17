"""Persistent administrator-managed rules for each group."""
import json
import time
from pathlib import Path

from modules.runtime_paths import runtime_config_file
from modules.atomic_write import write_json

from modules.group_id import normalize_group_id

FILE = runtime_config_file("group_rules.json")
_WAITING = {}
_WAITING_TTL = 10 * 60
_WAITING_MAX = 2000
MAX_RULES_LENGTH = 4000


def _key(chat_id):
    return normalize_group_id(chat_id)


def _load():
    try:
        return json.loads(FILE.read_text(encoding="utf-8")) if FILE.exists() else {}
    except (OSError, ValueError):
        return {}


def _save(data):
    FILE.parent.mkdir(parents=True, exist_ok=True)
    write_json(FILE, data, indent=2)


def _prune_waiting(now=None):
    now = time.monotonic() if now is None else now
    for key, created in list(_WAITING.items()):
        if now - float(created) > _WAITING_TTL:
            _WAITING.pop(key, None)
    while len(_WAITING) > _WAITING_MAX:
        _WAITING.pop(next(iter(_WAITING)), None)


def begin(chat_id, user_id):
    _prune_waiting()
    _WAITING[(_key(chat_id), str(user_id))] = time.monotonic()


def waiting(chat_id, user_id):
    _prune_waiting()
    return (_key(chat_id), str(user_id)) in _WAITING


def cancel(chat_id, user_id):
    _WAITING.pop((_key(chat_id), str(user_id)), None)


def save(chat_id, user_id, text):
    rules = "\n".join(line.strip() for line in str(text or "").splitlines() if line.strip())
    if not rules or len(rules) > MAX_RULES_LENGTH:
        return False
    data = _load()
    data[_key(chat_id)] = {"rules": rules, "updated_by": str(user_id)}
    _save(data)
    cancel(chat_id, user_id)
    return True


def get(chat_id):
    return _load().get(_key(chat_id), {}).get("rules")


def remove(chat_id):
    data = _load()
    if _key(chat_id) not in data:
        return False
    del data[_key(chat_id)]
    _save(data)
    return True


def format_rules(chat_id):
    rules = get(chat_id)
    if not rules:
        return None
    lines = rules.splitlines()
    numbered = "\n".join(f"{index}- {line}" for index, line in enumerate(lines, 1))
    return (
        "📜 قوانین گروه:\n\n"
        f"{numbered}\n\n"
        "کاربرانی که قوانین گروه را رعایت نکنند طبق قوانین گروه اخطار دریافت خواهند کرد."
    )
