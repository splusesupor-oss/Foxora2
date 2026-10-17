"""Persistent last pinned message per Soroush Plus group."""
import json
from pathlib import Path

from modules.runtime_paths import runtime_config_file
from modules.atomic_write import write_json
from modules.group_id import normalize_group_id

FILE = runtime_config_file("pinned_messages.json")


def _load():
    try:
        return json.loads(FILE.read_text(encoding="utf-8")) if FILE.exists() else {}
    except Exception:
        return {}


def save(chat_id, message_id):
    data = _load()
    data[normalize_group_id(chat_id)] = int(message_id)
    write_json(FILE, data, indent=2)


def get(chat_id):
    return _load().get(normalize_group_id(chat_id))
