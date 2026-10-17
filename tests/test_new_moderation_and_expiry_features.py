"""تست‌های جامع قابلیت‌های جدید:
۱. تنظیم انقضا از ۱ روز تا ۴۰ روز و غیرفعال‌سازی آنی
۲. لیست سکوت و پاک لیست سکوت
۳. لیست اخراج و پاک لیست اخراج
۴. سکوت نام [عبارت] و اخراج نام [عبارت]
۵. مجوزها (مدیر ثبت‌شده، مالک ثبت‌شده، مالک سراسری)
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from datetime import datetime, timedelta, timezone

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest
from modules import group_expiry as ge
from modules import manual_mute_storage as mms
from modules import banned_storage as bs
from modules.persian_names import match_persian_name, normalize_persian_name_text
from modules.owner_check import is_global_owner
from modules.group_storage import activate_group, deactivate_group, is_active, set_group_owner
from modules.admin_storage import add_admin, remove_admin
from handlers.message_handler import handle_new_message, _has_group_management_permission


# ===========================================================================
# تست ۱: تنظیم انقضا از ۱ روز تا ۴۰ روز
# ===========================================================================
def test_expiry_1_to_40_days():
    # روزهای مختلف از ۱ تا ۴۰ با اعداد فارسی و انگلیسی
    for d in (1, 2, 5, 7, 10, 15, 20, 25, 30, 35, 40):
        cmd_p = f"{str(d).translate(ge._PERSIAN_DIGITS)} روز"
        cmd_e = f"{d} روز"

        assert ge.match_command(cmd_p) == cmd_p
        assert ge.duration_days(cmd_p) == d

        assert ge.match_command(cmd_e) is not None
        assert ge.duration_days(cmd_e) == d

    # دستورهای متنی
    assert ge.match_command("یک هفته") == "یک هفته"
    assert ge.duration_days("یک هفته") == 7
    assert ge.match_command("دو هفته") == "دو هفته"
    assert ge.duration_days("دو هفته") == 14
    assert ge.match_command("یک ماه") == "یک ماه"
    assert ge.duration_days("یک ماه") == 29

    # دستورهای خارج از بازه
    assert ge.match_command("۰ روز") is None
    assert ge.match_command("۴۱ روز") is None
    assert ge.match_command("۵۰ روز") is None
    assert ge.match_command("سلام") is None


# ===========================================================================
# تست ۲: تطبیق نام فارسی
# ===========================================================================
def test_persian_name_matching_accuracy():
    # موارد مثبت
    assert match_persian_name("علی", "علی", "رضایی") is True
    assert match_persian_name("علی", "سید علی", "حسینی") is True
    assert match_persian_name("علی", "علیرضا", None) is True
    assert match_persian_name("علی", "علی‌رضا", None) is True
    assert match_persian_name("علی رضا", "سید علیرضا", "محمدی") is True
    assert match_persian_name("محمد", "مُحَمَّد", "علوی") is True
    assert match_persian_name("فاطمه", "فاطمة", "کریمی") is True
    assert match_persian_name("امیر", "🌟 امیرحسین 🌟", None) is True

    # موارد منفی (نباید تطبیق بخورند)
    assert match_persian_name("علی", "جعلی", None) is False
    assert match_persian_name("علی", "تعلیق", None) is False
    assert match_persian_name("علی", "معلی", None) is False
    assert match_persian_name("مهدی", "مهدیه", None) is False


# ===========================================================================
# تست ۳: ماژول manual_mute_storage
# ===========================================================================
def test_manual_mute_storage_crud(tmp_path, monkeypatch):
    test_file = tmp_path / "manual_mutes.json"
    monkeypatch.setattr(mms, "FILE", test_file)
    monkeypatch.setattr(mms, "_cache", None)
    monkeypatch.setattr(mms, "_cache_mtime", None)

    chat_id = 998877

    # ابتدا خالی است
    assert mms.get_muted_users(chat_id) == []
    assert not mms.is_manual_muted(chat_id, 101)

    # افزودن کاربر ۱
    mms.add_manual_mute(chat_id, 101, username="user1", display_name="کاربر یک")
    assert mms.is_manual_muted(chat_id, 101)
    users = mms.get_muted_users(chat_id)
    assert len(users) == 1
    assert users[0]["user_id"] == "101"
    assert users[0]["username"] == "user1"
    assert users[0]["display_name"] == "کاربر یک"

    # افزودن کاربر ۲
    mms.add_manual_mute(chat_id, 102, display_name="کاربر دو")
    assert len(mms.get_muted_users(chat_id)) == 2

    # حذف کاربر ۱
    assert mms.remove_manual_mute(chat_id, 101) is True
    assert not mms.is_manual_muted(chat_id, 101)
    assert len(mms.get_muted_users(chat_id)) == 1

    # پاک‌سازی کامل گروه
    assert mms.clear_manual_mutes(chat_id) == 1
    assert mms.get_muted_users(chat_id) == []


# ===========================================================================
# تست ۴: ماژول banned_storage get_banned_list و clear_group_banned
# ===========================================================================
def test_banned_storage_list_and_clear(tmp_path, monkeypatch):
    test_file = tmp_path / "banned_users.json"
    monkeypatch.setattr(bs, "FILE", test_file)
    monkeypatch.setattr(bs, "_USE_SQLITE", False)
    monkeypatch.setattr(bs, "_cache", None)
    monkeypatch.setattr(bs, "_cache_mtime", None)

    chat_id = 112233

    # ابتدا خالی است
    assert bs.get_banned_list(chat_id) == []

    # افزودن کاربر اخراجی
    bs.add_banned(chat_id, 201, username="bad_user", display_name="بد کاربر", reason="تخلف", source="manual")
    banned = bs.get_banned_list(chat_id)
    assert len(banned) == 1
    assert banned[0]["user_id"] == "201"
    assert banned[0]["username"] == "bad_user"
    assert banned[0]["reason"] == "تخلف"

    # پاک‌سازی لیست اخراج
    assert bs.clear_group_banned(chat_id) == 1
    assert bs.get_banned_list(chat_id) == []


# ===========================================================================
# تست ۵: بررسی مجوزها و رفتارهای هندلر دستورات
# ===========================================================================
class FakeUser:
    def __init__(self, uid, fn="", ln="", username=None):
        self.id = uid
        self.first_name = fn
        self.last_name = ln
        self.username = username


class FakeMessage:
    def __init__(self, text, user_id=1, mid=100):
        self.id = mid
        self.message = text
        self.reply_to_msg_id = None
        self.entities = None
        self.file = None
        self.photo = None


class FakeChat:
    def __init__(self, cid, title="گروه تست"):
        self.id = cid
        self.title = title


class FakeEvent:
    def __init__(self, chat_id, sender, text):
        self.chat_id = chat_id
        self.sender = sender
        self.message = FakeMessage(text, user_id=sender.id)
        self.reply_to = None
        self.is_private = False
        self.replies = []
        self._chat = FakeChat(chat_id)

    async def get_sender(self):
        return self.sender

    async def get_chat(self):
        return self._chat

    async def reply(self, text, **kwargs):
        self.replies.append(text)

    async def respond(self, text, **kwargs):
        self.replies.append(text)


class FakeAdminActions:
    def __init__(self):
        self.muted = []
        self.unmuted = []
        self.banned = []
        self.unbanned = []
        self.bot_account_id = 999999

    async def mute_user(self, chat_id, user_id, *args, **kwargs):
        self.muted.append((chat_id, user_id))
        return True

    async def unmute_user(self, chat_id, user_id, *args, **kwargs):
        self.unmuted.append((chat_id, user_id))
        return True

    async def ban_user(self, chat_id, user_id, *args, **kwargs):
        self.banned.append((chat_id, user_id))
        return True

    async def unban_user(self, chat_id, user_id, *args, **kwargs):
        self.unbanned.append((chat_id, user_id))
        return True


class FakeClient:
    def __init__(self, participants=None):
        self._participants = participants or []
        self.edited_permissions = []

    async def get_participants(self, chat, *args, **kwargs):
        return list(self._participants)

    async def get_me(self):
        return FakeUser(999999, fn="ربات", username="bot")

    async def edit_permissions(self, chat_id, member, **kwargs):
        self.edited_permissions.append((chat_id, member.id))
        return True


class FakeLogger:
    def log_info(self, *args, **kwargs): pass
    def log_error(self, *args, **kwargs): pass
    def log_warning(self, *args, **kwargs): pass


class FakeModerationQueue:
    def enqueue(self, chat_id, action, operation=None, on_success=None, on_failure=None, **kwargs):
        if operation:
            res = operation()
            if asyncio.iscoroutine(res):
                asyncio.create_task(res)
        if on_success:
            asyncio.create_task(on_success(True))


class FakeBot:
    def __init__(self, participants=None):
        self.client = FakeClient(participants)
        self.admin_actions = FakeAdminActions()
        self.logger = FakeLogger()
        self.moderation_queue = FakeModerationQueue()
        self.punished_users = set()
        self.spam_burst_messages = {}
        self.spammer_messages = {}
        self.spam_burst_users = set()
        self.rejoin_spam_state = {}
        self.user_messages = {}
        self.repeat_messages = {}
        self.bot_account_id = 999999
        self.detector = type("Detector", (), {
            "is_spam": lambda *a, **k: (False, None),
            "check_message": lambda *a, **k: (False, None),
        })()
        self.tracker = type("Tracker", (), {
            "muted_users": {},
            "banned_users": {},
            "reset_count": lambda *a, **k: None,
            "get_count": lambda *a, **k: 0,
            "increment": lambda *a, **k: 1,
            "decrement": lambda *a, **k: 0,
            "should_punish": lambda *a, **k: False,
        })()
        self.config_manager = type("Config", (), {
            "get": lambda self, k, d=None: d,
            "banned_words": [],
        })()
        self.group_timer_tasks = {}
        self.banned_words_local = set()

    def is_spam_locked(self, *a, **k): return False
    def clear_spam_lock(self, *a, **k): pass
    def clear_released_user_state(self, *a, **k): pass


def test_permission_denial(tmp_path, monkeypatch):
    async def _runner():
        # کاربری که مدیر یا مالک نیست باید رد شود
        chat_id = 778899
        normal_user = FakeUser(1001, "کاربر", "عادی")
        bot = FakeBot()

        event = FakeEvent(chat_id, normal_user, "لیست سکوت")
        await handle_new_message(bot, event)
        assert any("فقط" in r for r in event.replies)

        event2 = FakeEvent(chat_id, normal_user, "پاک لیست سکوت")
        await handle_new_message(bot, event2)
        assert any("فقط" in r for r in event2.replies)

        event3 = FakeEvent(chat_id, normal_user, "لیست اخراج")
        await handle_new_message(bot, event3)
        assert any("فقط" in r for r in event3.replies)

        event4 = FakeEvent(chat_id, normal_user, "سکوت نام علی")
        await handle_new_message(bot, event4)
        assert any("فقط" in r for r in event4.replies)

    asyncio.run(_runner())


def test_mute_and_kick_by_name(tmp_path, monkeypatch):
    async def _runner():
        # راه‌اندازی فایل‌های موقت
        mms_file = tmp_path / "manual_mutes.json"
        monkeypatch.setattr(mms, "FILE", mms_file)
        monkeypatch.setattr(mms, "_cache", None)
        monkeypatch.setattr(mms, "_cache_mtime", None)

        bans_file = tmp_path / "banned_users.json"
        monkeypatch.setattr(bs, "FILE", bans_file)
        monkeypatch.setattr(bs, "_USE_SQLITE", False)
        monkeypatch.setattr(bs, "_cache", None)
        monkeypatch.setattr(bs, "_cache_mtime", None)

        chat_id = 554433
        owner = FakeUser(999111, "مالک", username="owner_user")
        # ثبت مالک گروه
        set_group_owner(chat_id, owner.id)
        activate_group(chat_id, "گروه تست")

        # اعضای گروه شامل:
        # ۱. علی رضایی (باید هدف قرار گیرد)
        # ۲. علیرضا حسینی (باید هدف قرار گیرد)
        # ۳. حسن جعلی (نباید هدف قرار گیرد)
        # ۴. مالک (نباید هدف قرار گیرد)
        # ۵. ادمین (نباید هدف قرار گیرد)
        admin_user = FakeUser(888111, "علی", "مدیر", username="admin_ali")
        add_admin(chat_id, admin_user.id, admin_user.username)

        target1 = FakeUser(3001, "علی", "رضایی")
        target2 = FakeUser(3002, "علیرضا", "حسینی")
        other = FakeUser(3003, "حسن", "جعلی")

        participants = [owner, admin_user, target1, target2, other]
        bot = FakeBot(participants)

        # ۱. اجرای سکوت نام علی
        event_mute = FakeEvent(chat_id, owner, "سکوت نام علی")
        await handle_new_message(bot, event_mute)

        # نتیجه سکوت: تعداد ۲ نفر باید سکوت شده باشند
        assert any("تعداد 2 کاربر با نام «علی» سکوت شدند." in r for r in event_mute.replies)
        # بررسی اینکه هدف‌ها در manual_mute_storage هستند
        muted_users = mms.get_muted_users(chat_id)
        muted_ids = {u["user_id"] for u in muted_users}
        assert "3001" in muted_ids
        assert "3002" in muted_ids
        # ادمین و مالک نباید باشند
        assert str(admin_user.id) not in muted_ids
        assert str(owner.id) not in muted_ids
        assert "3003" not in muted_ids

        # ۲. اجرای «لیست سکوت»
        event_list_mute = FakeEvent(chat_id, owner, "لیست سکوت")
        await handle_new_message(bot, event_list_mute)
        assert any("لیست کاربر سکوت شده توسط روباه" in r for r in event_list_mute.replies)

        # ۳. اجرای «پاک لیست سکوت»
        event_clear_mute = FakeEvent(chat_id, owner, "پاک لیست سکوت")
        await handle_new_message(bot, event_clear_mute)
        assert any("پاکسازی انجام شد تعداد = (" in r and "۲ نفر ) ✓" in r for r in event_clear_mute.replies)
        assert mms.get_muted_users(chat_id) == []

        # ۴. اجرای اخراج نام علی
        event_kick = FakeEvent(chat_id, owner, "اخراج نام علی")
        await handle_new_message(bot, event_kick)
        assert any("تعداد 2 کاربر با نام «علی» اخراج شدند." in r for r in event_kick.replies)

        # ۵. اجرای «لیست اخراج»
        event_list_kick = FakeEvent(chat_id, owner, "لیست اخراج")
        await handle_new_message(bot, event_list_kick)
        assert any("لیست کاربر اخراج شده توسط روباه" in r for r in event_list_kick.replies)

        # ۶. اجرای «پاک لیست اخراج»
        event_clear_kick = FakeEvent(chat_id, owner, "پاک لیست اخراج")
        await handle_new_message(bot, event_clear_kick)
        assert any("پاکسازی انجام شد تعداد = (" in r and "۲ نفر ) ✓" in r for r in event_clear_kick.replies)
        assert bs.get_banned_list(chat_id) == []

    asyncio.run(_runner())
