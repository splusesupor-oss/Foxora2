"""رگرسیون محدودسازی stateها: سقف دفترچهٔ username + هماهنگ‌سازی punished_users.

۱. ``username_directory`` (economy/directory.py):
   * دفترچهٔ هر گروه سقف ``USERNAME_BOOK_MAX`` (۵۰) entry دارد.
   * هنگام عبور از سقف، قدیمی‌ترین entryها (اولین ثبت) حذف می‌شوند.
   * محدودسازی روی دادهٔ پایدار (SQLite/JSON) هم اعمال می‌شود، نه فقط RAM.
   * username حذف‌شده با اولین پیام بعدی همان کاربر دوباره ثبت می‌شود.
   * lookup/انتقال با @username برای نام‌های نگه‌داشته‌شده دست‌نخورده است.

۲. ``punished_users`` (core.cleanup_punished_users_state):
   * بدون TTL کورکورانه: entry کاربری که هنوز در ذخیره‌گاه بن است می‌ماند.
   * entry کاربر آزادشده (دیگر در ذخیره‌گاه نیست) حذف می‌شود.
   * گروه‌های سکوت (مجازات=سکوت دائمی): entryها نگهداری می‌شوند (منبع
     حقیقت سمت سرور است) — حذف نمی‌شوند.
   * mapهای tracker (خالی در کد زنده) به‌عنوان محافظت احتیاطی عمل می‌کنند.
   * سقف ایمنی هیچ‌گاه مجازات فعال را حذف نمی‌کند.

    python -m pytest tests/test_state_bounds.py
"""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import bot_working_split_ok as core_module
from economy import directory
from economy import storage
from modules import banned_storage
from modules import punishment_mode

CHAT = -10022770888
CHAT2 = -100555444333
G1 = "9429374"
G2 = "23153594"


# ---------------------------------------------------------------------------
# fixtures: ایزولاسیون کامل — هیچ فایل واقعی پروژه دست نمی‌خورد
# ---------------------------------------------------------------------------
@pytest.fixture
def econ_sqlite(tmp_path):
    """backend SQLite روی فایل موقت + بازیابی وضعیت قبلی storage."""
    orig = dict(DATA_FILE=storage.DATA_FILE, DB_FILE=storage.DB_FILE,
                backend=storage._BACKEND, cache=storage._cache,
                cache_mtime=storage._cache_mtime, dirty=storage._dirty,
                dirty_rows=storage._dirty_rows, conn=storage._conn)
    storage.use_sqlite(tmp_path / "bot.sqlite3")
    yield storage
    storage._close_connection()
    storage.DATA_FILE = orig["DATA_FILE"]
    storage.DB_FILE = orig["DB_FILE"]
    storage._BACKEND = orig["backend"]
    storage._cache = orig["cache"]
    storage._cache_mtime = orig["cache_mtime"]
    storage._dirty = orig["dirty"]
    storage._dirty_rows = orig["dirty_rows"]
    storage._conn = orig["conn"]


@pytest.fixture
def econ_json(tmp_path):
    """backend JSON روی فایل موقت + بازیابی وضعیت قبلی storage."""
    orig = dict(DATA_FILE=storage.DATA_FILE, DB_FILE=storage.DB_FILE,
                backend=storage._BACKEND, cache=storage._cache,
                cache_mtime=storage._cache_mtime, dirty=storage._dirty,
                dirty_rows=storage._dirty_rows, conn=storage._conn)
    storage.use_file(tmp_path / "economy.json")
    yield storage
    storage._close_connection()
    storage.DATA_FILE = orig["DATA_FILE"]
    storage.DB_FILE = orig["DB_FILE"]
    storage._BACKEND = orig["backend"]
    storage._cache = orig["cache"]
    storage._cache_mtime = orig["cache_mtime"]
    storage._dirty = orig["dirty"]
    storage._dirty_rows = orig["dirty_rows"]
    storage._conn = orig["conn"]


@pytest.fixture
def isolated_banned(tmp_path, monkeypatch):
    monkeypatch.setattr(banned_storage, "FILE", tmp_path / "banned_users.json")
    monkeypatch.setattr(banned_storage, "_cache", None)
    monkeypatch.setattr(banned_storage, "_cache_mtime", None)
    return banned_storage


@pytest.fixture
def pm_file(tmp_path, monkeypatch):
    """فایل punishment_mode ایزوله برای تنظیم حالت گروه‌ها در تست."""
    monkeypatch.setattr(punishment_mode, "_FILE", tmp_path / "punishment_mode.json")
    monkeypatch.setattr(punishment_mode, "_cache", None)
    monkeypatch.setattr(punishment_mode, "_cache_mtime", None)
    return tmp_path / "punishment_mode.json"


def _set_mode(pm_path, group, mode):
    import json
    pm_path.write_text(json.dumps({group: mode}), encoding="utf-8")
    punishment_mode._cache = None
    punishment_mode._cache_mtime = None


def _make_bot():
    return SimpleNamespace(
        punished_users=set(),
        tracker=SimpleNamespace(banned_users={}, muted_users={}),
        logger=MagicMock(),
        PUNISHED_USERS_MAX=core_module.SoroushAntiSpamBot.PUNISHED_USERS_MAX,
    )


def _reconcile(bot):
    # فراخوانی روش کلاس روی self مجازی — بدون ساخت ربات کامل.
    return core_module.SoroushAntiSpamBot.cleanup_punished_users_state(bot)


# ===========================================================================
# ۱) سقف دفترچهٔ username — backend SQLite
# ===========================================================================
def test_username_book_capped_in_sqlite(econ_sqlite):
    st = econ_sqlite
    assert st._BACKEND == "sqlite"
    for i in range(directory.USERNAME_BOOK_MAX + 3):
        directory.remember(CHAT, 900000 + i, f"user{i:04d}")
    book = directory.entries(CHAT)
    assert len(book) == directory.USERNAME_BOOK_MAX
    # قدیمی‌ترین‌ها (اولین ثبت) ریزیده شده‌اند
    for i in range(3):
        assert f"user{i:04d}" not in book
    # جدیدترین‌ها حضور دارند و نگاشت درست است
    assert book.get("user0502") == str(900502)
    assert book.get("user0501") == str(900501)
    # پایدارسازی + بررسی رکورد خود SQLite
    st.flush()


def test_cap_persists_across_reload_in_sqlite(econ_sqlite):
    """بعد از flush و «ری‌استارت» (حذف آینهٔ RAM) دادهٔ پایدار ≤ سقف است."""
    st = econ_sqlite
    for i in range(directory.USERNAME_BOOK_MAX + 5):
        directory.remember(CHAT, 920000 + i, f"px{i:04d}")
    st.flush()
    # شبیه‌سازی پروسهٔ تازه: آینهٔ RAM خالی، خواندن از SQLite
    st._cache = None
    book = directory.entries(CHAT)
    assert len(book) == directory.USERNAME_BOOK_MAX
    assert "px0000" not in book
    assert book.get("px0504") == str(920504)
    assert directory.lookup(CHAT, "px0504") == str(920504)


def test_evicted_username_reregisters_on_next_message(econ_sqlite):
    for i in range(directory.USERNAME_BOOK_MAX):
        directory.remember(CHAT, 910000 + i, f"rot{i:04d}")
    # یک username جدید → قدیمی‌ترین (rot0000) evict می‌شود
    directory.remember(CHAT, 999999, "newcomer")
    assert directory.lookup(CHAT, "rot0000") is None
    assert directory.lookup(CHAT, "newcomer") == "999999"
    # کاربر پیام بعدی می‌دهد → دوباره ثبت می‌شود (همان رفتار remember)
    directory.remember(CHAT, 910000, "rot0000")
    assert directory.lookup(CHAT, "rot0000") == "910000"
    assert len(directory.entries(CHAT)) <= directory.USERNAME_BOOK_MAX


def test_username_change_updates_entry_not_duplicate(econ_sqlite):
    directory.remember(CHAT, 700001, "sharedname")
    # کاربر دیگری همان username را می‌گیرد → entry به‌روز، نه تکراری
    directory.remember(CHAT, 700002, "sharedname")
    book = directory.entries(CHAT)
    assert book.get("sharedname") == "700002"
    assert len(book) == 1


def test_cap_is_per_group(econ_sqlite):
    for i in range(directory.USERNAME_BOOK_MAX + 2):
        directory.remember(CHAT, 930000 + i, f"ga{i:04d}")
    directory.remember(CHAT2, 939999, "only_one")
    assert len(directory.entries(CHAT)) == directory.USERNAME_BOOK_MAX
    assert directory.entries(CHAT2) == {"only_one": "939999"}
    assert directory.lookup(CHAT2, "only_one") == "939999"


def test_same_user_reregister_is_write_free(econ_json):
    """مسیر داغ: همان کاربر/username → هیچ تغییر (و نوشتن) انجام نمی‌شود."""
    st = econ_json
    st.flush()
    st._dirty = False
    directory.remember(CHAT, 750001, "stable_name")
    st.flush()
    st._dirty = False
    # ثبت تکراری باید بدون لمس داده باشد
    before = directory.entries(CHAT)
    directory.remember(CHAT, 750001, "stable_name")
    after = directory.entries(CHAT)
    assert before == after == {"stable_name": "750001"}
    assert st.is_dirty() is False, "ثبت تکراری نباید dirty row بسازد"


# ===========================================================================
# ۲) punished_users — هماهنگ‌سازی با منابع حقیقت
# ===========================================================================
def test_reconcile_keeps_active_ban(isolated_banned, pm_file):
    _set_mode(pm_file, G1, "ban")
    bot = _make_bot()
    isolated_banned.add_banned(G1, 700001, username="spammer1")
    bot.punished_users.add(f"{G1}:700001")   # بن فعال
    bot.punished_users.add(f"{G2}:700002")   # آزادشده: در ذخیره‌گاه نیست
    removed = _reconcile(bot)
    assert f"{G1}:700001" in bot.punished_users, "بن فعال نباید حذف شود"
    assert f"{G2}:700002" not in bot.punished_users, "entry آزادشده حذف شود"
    assert removed == 1


def test_reconcile_keeps_mute_mode_entries(isolated_banned, pm_file):
    """گروه سکوت: مجازات=سکوت دائمی سمت سرور؛ entry حذف نمی‌شود."""
    _set_mode(pm_file, G1, "mute")
    bot = _make_bot()
    # هیچ بنی در ذخیره‌گاه نیست؛ باز هم باید نگه داشته شود
    bot.punished_users.add(f"{G1}:700003")
    _reconcile(bot)
    assert f"{G1}:700003" in bot.punished_users


def test_reconcile_respects_tracker_maps(isolated_banned, pm_file):
    _set_mode(pm_file, G1, "ban")
    bot = _make_bot()
    bot.tracker.muted_users[f"{G1}:730001"] = True
    bot.punished_users.add(f"{G1}:730001")
    _reconcile(bot)
    assert f"{G1}:730001" in bot.punished_users, "mapهای tracker محافظت کنند"


def test_reconcile_keeps_malformed_key(isolated_banned, pm_file):
    bot = _make_bot()
    bot.punished_users.add("no-colon-key")
    _reconcile(bot)
    assert "no-colon-key" in bot.punished_users, "فرمت ناشناخته دست‌نخورده بماند"


def test_reconcile_survives_banned_storage_failure(pm_file):
    """اگر خواندن ذخیره‌گاه بن شکست بخورد، هیچ entry‌ای حذف نشود."""
    bot = _make_bot()
    bot.punished_users.add(f"{G1}:740001")
    real_load = core_module.load_banned

    def boom():
        raise RuntimeError("storage down")

    core_module.load_banned = boom
    try:
        _reconcile(bot)
    finally:
        core_module.load_banned = real_load
    assert f"{G1}:740001" in bot.punished_users, "پیش روی شک حذف نکن"


def test_safety_cap_never_drops_active_punishment(isolated_banned, pm_file):
    _set_mode(pm_file, G1, "ban")
    bot = _make_bot()
    bot.PUNISHED_USERS_MAX = 10
    # ۲۰ بن فعال — سقف نباید هیچ‌کدام را حذف کند
    for i in range(20):
        isolated_banned.add_banned(G1, 710000 + i)
        bot.punished_users.add(f"{G1}:{710000 + i}")
    _reconcile(bot)
    assert len(bot.punished_users) == 20, "مجازات فعال هرگز با سقف حذف نمی‌شود"
    # entryهای غیرفعال با سقف (و هماهنگ‌سازی) حذف می‌شوند
    for i in range(5):
        bot.punished_users.add(f"{G2}:{720000 + i}")
    _reconcile(bot)
    assert not any(k.startswith(f"{G2}:") for k in bot.punished_users)
    assert len(bot.punished_users) == 20


def test_no_blind_ttl_release_path_still_works(isolated_banned, pm_file):
    """سناریوی کامل: بن → آزادسازی (حذف از ذخیره‌گاه) → حذف entry در sweep."""
    _set_mode(pm_file, G1, "ban")
    bot = _make_bot()
    isolated_banned.add_banned(G1, 750010, username="late_spammer")
    bot.punished_users.add(f"{G1}:750010")
    assert _reconcile(bot) == 0
    assert f"{G1}:750010" in bot.punished_users
    # آزادسازی: همان کاری که مسیرهای unban/رفع بن انجام می‌دهند
    from modules.banned_storage import remove_banned
    remove_banned(G1, 750010)
    assert _reconcile(bot) == 1
    assert f"{G1}:750010" not in bot.punished_users
