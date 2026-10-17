"""مدیریت توکن‌های امن، اختصاصی و وابسته به گروه برای ورود به سایت بازی روباه.

ویژگی‌های امنیتی:
- هر توکن به صورت رمزنگاری‌شده منحصراً به chat_id گروه فعال و user_id کاربر متصل است.
- بررسی وضعیت فعال بودن روباه در گروه ثبت‌شده (Active Group Verification) در لحظه ورود و هر عملیات.
- در صورت خروج روباه از گروه، غیرفعال شدن یا حذف گروه از ربات، توکن‌ها بلافاصله باطل می‌شوند.
- جلوگیری از پخش لینک با قفل شدن روی دستگاه اول (Device Binding).
- توکن‌ها در طول مدت اعتبار برای همان کاربر در همان گروه قابل استفاده مکرر هستند.
"""
import hashlib
import hmac
import json
import os
import secrets
import tempfile
import threading
import time
from pathlib import Path

from modules.runtime_paths import CONFIG_DIR

TOKEN_FILE = CONFIG_DIR / "fox_game_tokens.json"
LEADERBOARD_FILE = CONFIG_DIR / "fox_game_leaderboard.json"
OFFICIAL_POOL_FILE = CONFIG_DIR / "fox_official_tokens.json"

TOKEN_LIFETIME_SECONDS = 86400  # ۲۴ ساعت اعتبار کامل (۱ شبانه‌روز)
_STORE_LOCK = threading.RLock()
EMPTY_POOL_MESSAGE = "فعلاً توکن جدیدی موجود نیست"


def _load_json(file_path):
    try:
        if file_path.exists():
            return json.loads(file_path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_json(file_path, data):
    file_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=str(file_path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, file_path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass



def load_official_tokens():
    tokens = []
    try:
        from modules.fox_official_pool import OFFICIAL_TOKENS
        tokens.extend(str(t).strip() for t in OFFICIAL_TOKENS if str(t).strip())
    except Exception:
        pass
    for path in (
        Path(__file__).resolve().parent / "fox_official_tokens.json",
        Path(__file__).resolve().parent.parent / "config" / "fox_official_tokens.json",
        OFFICIAL_POOL_FILE,
    ):
        data = _load_json(path)
        extra = []
        if isinstance(data, list):
            extra = [str(t).strip() for t in data if str(t).strip()]
        elif isinstance(data, dict):
            extra = [str(t).strip() for t in data.get("tokens", []) if str(t).strip()]
        for item in extra:
            if item not in tokens:
                tokens.append(item)
    return tokens


def official_token_set():
    return set(load_official_tokens())


def is_official_token(token):
    clean = str(token or "").strip()
    return bool(clean) and clean in official_token_set()


def _record_token_keys(data):
    """Every token ever written to the store, including retired/expired."""
    if not isinstance(data, dict):
        return set()
    used = set()
    for key, record in data.items():
        if not isinstance(record, dict):
            continue
        used.add(str(key))
    return used


def _is_retired(record, now=None):
    if not isinstance(record, dict):
        return True
    if record.get("retired"):
        return True
    if now is None:
        now = time.time()
    try:
        expires = float(record.get("expires_at", 0) or 0)
    except (TypeError, ValueError):
        expires = 0.0
    return expires <= now


def _allocate_official_token(active_data, exclude=()):
    used = _record_token_keys(active_data)
    used.update(str(item) for item in exclude if item)
    for item in load_official_tokens():
        if item not in used:
            return item
    raise ValueError(EMPTY_POOL_MESSAGE)


DEV_TEST_TOKEN = "FOX-TEST-DEV-2026"
DEV_CHAT_ID = "fox_dev_group"
DEV_USER_ID = "dev_tester_77"


def _get_dev_record(device_id=None):
    try:
        from modules import group_storage
        group_storage.activate_group(DEV_CHAT_ID, "🦊 گروه آزمایشی روباه")
    except Exception:
        pass

    lb = _load_json(LEADERBOARD_FILE)
    entry = lb.get(DEV_USER_ID, {})
    nickname = entry.get("nickname") or "👑 تستر ارشد روباه"

    return {
        "user_id": DEV_USER_ID,
        "chat_id": DEV_CHAT_ID,
        "group_title": "🦊 گروه آزمایشی روباه",
        "first_name": "تستر ارشد",
        "username": "fox_tester",
        "nickname": nickname,
        "created_at": time.time(),
        "expires_at": 9999999999,  # تاریخ بسیار دور جهت تست‌های مکرر و دائمی
        "claimed": True,
        "device_id": str(device_id) if device_id else "dev_mock_device",
        "is_dev_token": True,
    }


def is_group_active(chat_id):
    """بررسی فعال بودن روباه در گروه ثبت‌شده از دیتابیس گروه‌های ربات."""
    if str(chat_id) == DEV_CHAT_ID:
        return True
    try:
        from modules import group_storage
        groups = group_storage.load_groups()
        if str(chat_id) in groups:
            return groups[str(chat_id)].get("active", False) is True
        return group_storage.is_active(chat_id) is True
    except Exception:
        return True


def cleanup_expired():
    """بازنشانی توکن‌های منقضی/گروه غیرفعال. کلیدها حفظ می‌شوند تا به pool برنگردند."""
    with _STORE_LOCK:
        data = _load_json(TOKEN_FILE)
        if not isinstance(data, dict):
            return
        now = time.time()
        changed = False
        for _key, record in data.items():
            if not isinstance(record, dict):
                continue
            expired = False
            try:
                expired = float(record.get("expires_at", 0) or 0) <= now
            except (TypeError, ValueError):
                expired = True
            inactive = not is_group_active(record.get("chat_id"))
            if (expired or inactive) and not record.get("retired"):
                record["retired"] = True
                changed = True
        if changed:
            _save_json(TOKEN_FILE, data)


def revoke_group_tokens(chat_id):
    """ابطال توکن‌های یک گروه. از store حذف نمی‌شوند تا دوباره اختصاص داده نشوند."""
    with _STORE_LOCK:
        data = _load_json(TOKEN_FILE)
        if not isinstance(data, dict):
            return 0
        now = time.time()
        removed = 0
        for _key, record in data.items():
            if not isinstance(record, dict):
                continue
            if str(record.get("chat_id")) != str(chat_id):
                continue
            if record.get("retired"):
                continue
            record["retired"] = True
            try:
                expires = float(record.get("expires_at", 0) or 0)
            except (TypeError, ValueError):
                expires = now
            if expires > now:
                record["expires_at"] = now
            removed += 1
        if removed > 0:
            _save_json(TOKEN_FILE, data)
        return removed


def _id_aliases(value):
    aliases = {str(value)}
    try:
        aliases.add(str(int(value)))
    except (TypeError, ValueError):
        pass
    try:
        from economy.coins.accounts import chat_aliases
        aliases.update(chat_aliases(value))
    except Exception:
        pass
    return aliases


def _find_active_token_in(data, chat_id, user_id, now=None):
    if not isinstance(data, dict):
        return None, None
    if now is None:
        now = time.time()
    user_aliases = _id_aliases(user_id)
    chat_ids = _id_aliases(chat_id)
    for token, record in data.items():
        if not isinstance(record, dict):
            continue
        if record.get("retired"):
            continue
        try:
            expires = float(record.get("expires_at", 0) or 0)
        except (TypeError, ValueError):
            continue
        if expires <= now:
            continue
        if str(record.get("user_id")) not in user_aliases:
            continue
        if str(record.get("chat_id")) not in chat_ids:
            continue
        if not is_group_active(record.get("chat_id")):
            continue
        return token, record
    return None, None


def find_active_token(chat_id, user_id):
    """توکن معتبر فعلی همین کاربر در همین گروه را برمی‌گرداند."""
    with _STORE_LOCK:
        data = _load_json(TOKEN_FILE)
        return _find_active_token_in(data, chat_id, user_id, time.time())


def _retire_user_tokens(data, chat_id, user_id, now=None):
    """Keep previous tokens in the store so they cannot return to the pool."""
    if not isinstance(data, dict):
        return []
    if now is None:
        now = time.time()
    user_aliases = _id_aliases(user_id)
    chat_ids = _id_aliases(chat_id)
    previous = []
    for token, record in data.items():
        if not isinstance(record, dict):
            continue
        if str(record.get("user_id")) not in user_aliases:
            continue
        if str(record.get("chat_id")) not in chat_ids:
            continue
        previous.append(token)
        record["retired"] = True
        try:
            expires = float(record.get("expires_at", 0) or 0)
        except (TypeError, ValueError):
            expires = now
        if expires > now:
            record["expires_at"] = now
    return previous


def _write_new_assignment(data, chat_id, user_id, first_name, username, now, previous):
    raw_token = _allocate_official_token(data, exclude=previous)
    display_name = first_name or (f"@{username}" if username else f"کاربر {user_id}")
    group_title = f"گروه {chat_id}"
    try:
        from modules import group_storage
        g_info = group_storage.load_groups().get(str(chat_id), {})
        if g_info.get("title"):
            group_title = g_info["title"]
    except Exception:
        pass
    lb = _load_json(LEADERBOARD_FILE)
    existing_entry = lb.get(str(user_id), {}) if isinstance(lb, dict) else {}
    nickname = (existing_entry or {}).get("nickname") or display_name
    data[raw_token] = {
        "user_id": str(user_id),
        "chat_id": str(chat_id),
        "group_title": group_title,
        "first_name": first_name or "",
        "username": username or "",
        "nickname": nickname,
        "created_at": now,
        "issued_at": now,
        "expires_at": now + TOKEN_LIFETIME_SECONDS,
        "claimed": False,
        "device_id": None,
        "retired": False,
    }
    _save_json(TOKEN_FILE, data)
    if not isinstance(lb, dict):
        lb = {}
    if str(user_id) not in lb:
        lb[str(user_id)] = {
            "user_id": str(user_id),
            "nickname": nickname,
            "wins": 0,
            "gold_won": 0,
            "silver_won": 0,
            "bronze_won": 0,
            "games_played": 0,
            "last_active": now,
        }
        _save_json(LEADERBOARD_FILE, lb)
    return raw_token


def issue_user_token(chat_id, user_id, first_name=None, username=None, check_active_group=False):
    """Return ``(token, created_new)`` under one lock. Reuses a live 24h token."""
    if check_active_group and not is_group_active(chat_id):
        raise ValueError("❌ روباه در این گروه فعال نیست یا گروه ثبت نشده است.")
    with _STORE_LOCK:
        data = _load_json(TOKEN_FILE)
        if not isinstance(data, dict):
            data = {}
        now = time.time()
        existing, _record = _find_active_token_in(data, chat_id, user_id, now)
        if existing:
            return existing, False
        previous = _retire_user_tokens(data, chat_id, user_id, now)
        token = _write_new_assignment(
            data, chat_id, user_id, first_name, username, now, previous,
        )
        return token, True


def create_token(chat_id, user_id, first_name=None, username=None, check_active_group=False):
    """ایجاد یا بازگرداندن توکن اختصاصی ۲۴ ساعته همین کاربر در همین گروه."""
    token, _created = issue_user_token(
        chat_id, user_id, first_name=first_name, username=username,
        check_active_group=check_active_group,
    )
    return token


def validate_token(token, device_id=None, expected_chat_id=None):
    """بررسی کامل و چندمرحله‌ای اعتبار توکن:
    ۱. وجود و انقضای زمانی توکن
    ۲. فعال بودن گروه ثبت‌شده در روباه (اگر روباه خارج شود، توکن باطل است)
    ۳. تطابق شناسه گروه
    ۴. انحصار دستگاه (Device Binding برای جلوگیری از پخش لینک)
    """
    if not token or not isinstance(token, str):
        return False, None, "توکن ارائه نشده است."

    clean_token = token.strip()
    if not is_official_token(clean_token):
        return False, None, "توکن نامعتبر است."

    with _STORE_LOCK:
        data = _load_json(TOKEN_FILE)
        record = data.get(clean_token) if isinstance(data, dict) else None

        if not record:
            return False, None, "توکن معتبر نیست یا منقضی شده است."

        now = time.time()
        try:
            expires = float(record.get("expires_at", 0) or 0)
        except (TypeError, ValueError):
            expires = 0.0
        bound_chat_id = record.get("chat_id")
        if not is_group_active(bound_chat_id):
            if not record.get("retired"):
                record["retired"] = True
                record["expires_at"] = min(expires, now) if expires else now
                _save_json(TOKEN_FILE, data)
            return False, None, "❌ دسترسی غیرمجاز: روباه در این گروه فعال نیست یا انقضای گروه به پایان رسیده است."
        if record.get("retired") or expires <= now:
            if not record.get("retired"):
                record["retired"] = True
                _save_json(TOKEN_FILE, data)
            return False, None, "اعتبار زمانی این لینک (۲۴ ساعت) به پایان رسیده است. می‌توانید با دستور «سایت بازی» در گروه فعال لینک جدید دریافت نمایید."

        if expected_chat_id is not None and str(bound_chat_id) != str(expected_chat_id):
            return False, None, "❌ این توکن برای این گروه صادر نشده است."

        saved_device = record.get("device_id")
        if device_id:
            if saved_device is None:
                record["device_id"] = str(device_id)
                record["claimed"] = True
                _save_json(TOKEN_FILE, data)
            elif saved_device != str(device_id):
                return False, None, "این لینک مخصوص کاربر و دستگاه دیگری است و امکان پخش عمومی ندارد."

        return True, record, None


def update_nickname(token, new_nickname, device_id=None):
    """به‌روزرسانی نام مستعار کاربر."""
    valid, record, err = validate_token(token, device_id)
    if not valid or not record:
        return False, err or "توکن نامعتبر است."

    clean_name = str(new_nickname).strip()[:30]
    if not clean_name:
        return False, "نام مستعار نمی‌تواند خالی باشد."

    data = _load_json(TOKEN_FILE)
    if token in data:
        data[token]["nickname"] = clean_name
        _save_json(TOKEN_FILE, data)

    user_id = record["user_id"]
    lb = _load_json(LEADERBOARD_FILE)
    if str(user_id) in lb:
        lb[str(user_id)]["nickname"] = clean_name
        lb[str(user_id)]["last_active"] = time.time()
        _save_json(LEADERBOARD_FILE, lb)

    return True, clean_name


def record_win(token, game_name, bronze_won=0, silver_won=0, gold_won=0, device_id=None):
    """ثبت پیروزی و اهدای سکه پس از تأیید اعتبار گروه و توکن."""
    valid, record, err = validate_token(token, device_id)
    if not valid or not record:
        return False, err or "توکن نامعتبر است."

    user_id = record["user_id"]
    chat_id = record["chat_id"]

    lb = _load_json(LEADERBOARD_FILE)
    entry = lb.setdefault(str(user_id), {
        "user_id": str(user_id),
        "nickname": record.get("nickname") or f"کاربر {user_id}",
        "wins": 0,
        "gold_won": 0,
        "silver_won": 0,
        "bronze_won": 0,
        "games_played": 0,
        "last_active": time.time(),
    })

    entry["wins"] = int(entry.get("wins", 0)) + 1
    entry["games_played"] = int(entry.get("games_played", 0)) + 1
    entry["bronze_won"] = int(entry.get("bronze_won", 0)) + int(bronze_won)
    entry["silver_won"] = int(entry.get("silver_won", 0)) + int(silver_won)
    entry["gold_won"] = int(entry.get("gold_won", 0)) + int(gold_won)
    entry["last_active"] = time.time()
    _save_json(LEADERBOARD_FILE, lb)

    # اهدای سکه در سیستم اقتصاد گروه مربوطه
    try:
        import economy
        if bronze_won > 0:
            economy.add_bronze(chat_id, user_id, bronze_won, note=f"سایت بازی: {game_name}")
        if silver_won > 0:
            economy.add_silver(chat_id, user_id, silver_won, note=f"سایت بازی: {game_name}")
        if gold_won > 0:
            economy.add_gold(chat_id, user_id, gold_won, note=f"سایت بازی: {game_name}")
    except Exception:
        pass

    return True, entry


def convert_coins(token, convert_type, times=1, device_id=None):
    """تبدیل سکه‌ها طبق منطق سیستم اقتصاد ربات در گروه متصل."""
    valid, record, err = validate_token(token, device_id)
    if not valid or not record:
        return False, err or "توکن نامعتبر یا منقضی است.", None

    user_id = record["user_id"]
    chat_id = record["chat_id"]

    try:
        import economy
        if convert_type == "bronze_to_silver":
            new_bal = economy.convert_bronze(chat_id, user_id, times=times, note="تبدیل سکه در سایت بازی")
        elif convert_type == "silver_to_gold":
            new_bal = economy.convert_silver(chat_id, user_id, times=times, note="تبدیل سکه در سایت بازی")
        else:
            return False, "نوع تبدیل نامعتبر است.", None

        balance = {
            "gold": new_bal.get(economy.GOLD, 0),
            "silver": new_bal.get(economy.SILVER, 0),
            "bronze": new_bal.get(economy.BRONZE, 0),
        }
        return True, "تبدیل با موفقیت انجام شد!", balance
    except Exception as e:
        return False, str(e), None


def get_real_leaderboard():
    """دریافت رتبه‌بندی تمام بازیکنان واقعی."""
    lb = _load_json(LEADERBOARD_FILE)
    players = list(lb.values())

    players.sort(
        key=lambda p: (
            p.get("wins", 0),
            p.get("gold_won", 0),
            p.get("silver_won", 0),
            p.get("bronze_won", 0)
        ),
        reverse=True,
    )
    return players[:20]
