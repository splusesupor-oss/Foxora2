"""سابقهٔ سریع پیام‌ها برای پاکسازی موج اسپم، مستقل از punishment."""
from collections import defaultdict, deque
import re
import time
from modules.group_id import normalize_group_id

_MAX = 500
_WINDOW = 120
# Retention is longer than every detection window; it only bounds stale cache.
_RETENTION = 30 * 60
_HISTORY = defaultdict(lambda: deque(maxlen=_MAX))


def _key(chat_id, user_id):
    return (normalize_group_id(chat_id), str(user_id))


def _norm(text):
    return " ".join(re.sub(r"\s+", " ", str(text or "").lower()).split())


def _prune_key(key, now=None, retention=_RETENTION):
    now = time.time() if now is None else now
    rows = _HISTORY.get(key)
    if not rows:
        return
    fresh = [row for row in rows if now - row.get("timestamp", now) <= retention]
    if fresh:
        _HISTORY[key] = deque(fresh, maxlen=_MAX)
    else:
        _HISTORY.pop(key, None)


def add_message(chat_id, user_id, message_id, text, timestamp=None):
    if message_id is None:
        return False
    key = _key(chat_id, user_id)
    _prune_key(key)
    rows = _HISTORY[key]
    for row in rows:
        if row.get("message_id") == message_id:
            return True
    rows.append({
        "chat_id": chat_id,
        "user_id": user_id,
        "message_id": message_id,
        "timestamp": time.time() if timestamp is None else timestamp,
        "text": _norm(text),
    })
    return True


def get_user_recent_messages(chat_id, user_id, limit=None):
    key = _key(chat_id, user_id)
    _prune_key(key)
    rows = list(_HISTORY.get(key, ()))
    return rows[-limit:] if limit else rows


def find_spam_messages(chat_id, user_id, text=None, window=_WINDOW):
    now = time.time()
    target = _norm(text) if text is not None else None
    rows = get_user_recent_messages(chat_id, user_id)
    return [row for row in rows if now - row["timestamp"] <= window
            and (target is None or row["text"] == target)]


def spam_snapshot(chat_id, user_id, current_message_id=None):
    """Return one authoritative recent ID snapshot for every spam branch."""
    ids = [
        row["message_id"] for row in get_user_recent_messages(chat_id, user_id)
        if isinstance(row.get("message_id"), int) and row["message_id"] > 0
    ]
    if current_message_id and current_message_id not in ids:
        ids.append(current_message_id)
    return list(dict.fromkeys(ids))


def cleanup_expired(now=None, retention=_RETENTION):
    """Drop inactive user histories without touching recent detection data."""
    now = time.time() if now is None else now
    for key in list(_HISTORY):
        _prune_key(key, now=now, retention=retention)


def remove_message_ids(chat_id, user_id, message_ids):
    """Forget selected IDs without clearing newer history for this sender."""
    key = _key(chat_id, user_id)
    rows = _HISTORY.get(key)
    if not rows:
        return 0
    remove = {
        message_id for message_id in (message_ids or ())
        if isinstance(message_id, int) and message_id > 0
    }
    if not remove:
        return 0
    kept = [row for row in rows if row.get("message_id") not in remove]
    removed = len(rows) - len(kept)
    if kept:
        _HISTORY[key] = deque(kept, maxlen=_MAX)
    else:
        _HISTORY.pop(key, None)
    return removed


def clear_user_history(chat_id, user_id):
    _HISTORY.pop(_key(chat_id, user_id), None)


def reset_all():
    _HISTORY.clear()
