"""Single persistent per-group access store for Google Search users."""
import json
import logging
from pathlib import Path

from modules.runtime_paths import runtime_config_file
from modules.atomic_write import write_json

from modules.group_id import normalize_group_id
from modules.user_display import format_user
from modules.time_utils import now_local

FILE = runtime_config_file("search_access.json")
DAILY_LIMIT = 27
_CACHE = None


def _key(chat_id):
    return normalize_group_id(chat_id)


def _normalize(data):
    """Migrate direct legacy group keys to the single ``groups`` root."""
    changed = False
    if "groups" not in data or not isinstance(data.get("groups"), dict):
        legacy_groups = {key: value for key, value in data.items() if isinstance(value, dict)}
        data.clear()
        data["groups"] = legacy_groups
        changed = True
    groups = data["groups"]
    for group in groups.values():
        if not isinstance(group, dict):
            continue
        if "allowed" in group and "allowed_users" not in group:
            group["allowed_users"] = group.pop("allowed")
            changed = True
        group.setdefault("enabled", False)
        group.setdefault("allowed_users", {})
        group.setdefault("usage", {})
    return changed


def _load():
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    try:
        data = json.loads(FILE.read_text(encoding="utf-8")) if FILE.exists() else {}
        data = data if isinstance(data, dict) else {}
        if _normalize(data):
            _save(data)
            return _CACHE if _CACHE is not None else data
        _CACHE = data
        return data
    except (OSError, ValueError):
        _CACHE = {}
        return _CACHE


def _save(data):
    global _CACHE
    _CACHE = data
    write_json(FILE, data, indent=2)


def _group(data, chat_id, create=False):
    _normalize(data)
    groups = data["groups"]
    key = _key(chat_id)
    if create:
        return groups.setdefault(key, {"enabled": False, "allowed_users": {}, "usage": {}})
    group = groups.get(key)
    return group if isinstance(group, dict) else None


def _log(kind, chat_id, user_id=None, **fields):
    payload = " ".join(f"{key}={value!r}" for key, value in fields.items())
    logging.getLogger("SoroushAntiSpam").info(
        f"SEARCH ACCESS {kind} chat_id={chat_id} canonical_chat_id={_key(chat_id)} "
        f"target_user_id={user_id if user_id is not None else 'none'} "
        f"storage_path={FILE} {payload}"
    )


def access_state(chat_id, user_id):
    group = _group(_load(), chat_id)
    enabled = bool(group and group.get("enabled"))
    users = group.get("allowed_users", {}) if group else {}
    allowed = bool(enabled and str(user_id) in users)
    return enabled, allowed


def set_enabled(chat_id, enabled):
    data = _load()
    group = _group(data, chat_id, create=True)
    group["enabled"] = bool(enabled)
    _save(data)
    _log("SAVE", chat_id, enabled=group["enabled"],
         allowed_users=sorted(str(value) for value in group["allowed_users"]))
    return group["enabled"]


def allow(chat_id, user):
    user_id = getattr(user, "id", None)
    if user_id is None:
        return False
    data = _load()
    group = _group(data, chat_id, create=True)
    users = group["allowed_users"]
    before = sorted(str(value) for value in users)
    users[str(user_id)] = {
        "username": getattr(user, "username", None),
        "display": format_user(user),
    }
    _save(data)
    persisted = _group(_load(), chat_id) or {}
    persisted_users = persisted.get("allowed_users", {})
    verified = str(user_id) in persisted_users
    _log("SAVE DEBUG", chat_id, user_id,
         username=getattr(user, "username", None), before=before,
         after=sorted(str(value) for value in persisted_users),
         saved_data={"enabled": bool(persisted.get("enabled")),
                     "allowed_users": sorted(str(value) for value in persisted_users)},
         verified=verified)
    _log("SAVE", chat_id, user_id,
         allowed_users=sorted(str(value) for value in persisted_users))
    return verified


def disallow(chat_id, user_id):
    data = _load()
    group = _group(data, chat_id)
    users = group.get("allowed_users", {}) if group else {}
    if str(user_id) not in users:
        return False
    del users[str(user_id)]
    _save(data)
    _log("SAVE", chat_id, user_id,
         allowed_users=sorted(str(value) for value in users))
    return True


def _today():
    return now_local().date().isoformat()


def _usage_bucket(group, day=None):
    usage = group.setdefault("usage", {})
    day = day or _today()
    for key in list(usage):
        if key != day:
            usage.pop(key, None)
    return usage.setdefault(day, {})


def reserve_request(chat_id, user_id):
    data = _load()
    group = _group(data, chat_id)
    users = group.get("allowed_users", {}) if group else {}
    if not group or not group.get("enabled") or str(user_id) not in users:
        return False, 0, False
    bucket = _usage_bucket(group)
    entry = bucket.setdefault(str(user_id), {"count": 0, "notified": False})
    count = int(entry.get("count", 0))
    if count >= DAILY_LIMIT:
        send_notice = not bool(entry.get("notified"))
        entry["notified"] = True
        _save(data)
        return False, count, send_notice
    new_count = count + 1
    entry["count"] = new_count
    send_notice = new_count >= DAILY_LIMIT and not bool(entry.get("notified"))
    if send_notice:
        entry["notified"] = True
    _save(data)
    return True, new_count, send_notice


def allowed_users(chat_id):
    group = _group(_load(), chat_id)
    if not group:
        return []
    bucket = _usage_bucket(group)
    rows = []
    for user_id, profile in group.get("allowed_users", {}).items():
        profile = profile if isinstance(profile, dict) else {}
        usage = bucket.get(str(user_id), {})
        rows.append({
            "user_id": str(user_id),
            "display": profile.get("display") or "کاربر ناشناس",
            "username": profile.get("username"),
            "count": int(usage.get("count", 0)),
        })
    return rows


def reset_for_tests():
    global _CACHE
    _CACHE = None
    _save({})
    _CACHE = {}
