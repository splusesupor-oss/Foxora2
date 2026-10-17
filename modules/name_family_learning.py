"""Automatic confidence-based learning memory for unknown Name & Family answers."""
import json
import time
from pathlib import Path

from modules.runtime_paths import runtime_config_file
from modules.atomic_write import write_json

FILE = runtime_config_file("name_family_learning.json")


def _load():
    try:
        return json.loads(FILE.read_text(encoding="utf-8")) if FILE.exists() else {}
    except Exception:
        return {}


def _save(data):
    FILE.parent.mkdir(parents=True, exist_ok=True)
    write_json(FILE, data, indent=2)


def record(
    category,
    letter,
    raw_answer,
    normalized_answer,
    chat_id,
    user_id,
    *,
    min_observations=5,
    min_unique_users=3,
    min_unique_chats=2,
):
    """Record unknown answers and promote only after independent observation thresholds."""
    data = _load()
    key = f"{category}|{letter}|{normalized_answer}"
    now = time.time()
    item = data.get(key)
    if item is None:
        item = {
            "category": category,
            "letter": letter,
            "raw_answer": raw_answer,
            "normalized_answer": normalized_answer,
            "count": 0,
            "first_seen_at": now,
            "last_seen_at": now,
            "chat_ids": [],
            "user_ids": [],
            "status": "learning",
        }
        data[key] = item
    item["count"] = int(item.get("count", 0)) + 1
    item["last_seen_at"] = now
    for field, value in (("chat_ids", str(chat_id)), ("user_ids", str(user_id))):
        values = item.setdefault(field, [])
        if value not in values:
            values.append(value)
    if (
        item.get("status") == "learning"
        and item["count"] >= max(1, int(min_observations))
        and len(item.get("user_ids", [])) >= max(1, int(min_unique_users))
        and len(item.get("chat_ids", [])) >= max(1, int(min_unique_chats))
    ):
        item["status"] = "learned"
        item["learned_at"] = now
    _save(data)
    return item


def learned_words():
    """Return runtime-approved words grouped by category, without human approval."""
    result = {}
    for item in _load().values():
        if item.get("status") != "learned":
            continue
        category = item.get("category")
        answer = item.get("normalized_answer")
        if category and answer:
            result.setdefault(category, set()).add(answer)
    return result
