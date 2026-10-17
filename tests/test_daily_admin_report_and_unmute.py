"""تست‌های جامع:
۱. رفع سکوت با یوزرنیم (رفع سکوت @username)
۲. فرمت نقل‌قول و ضخیم برای «لیست سکوت» و «لیست اخراج»
۳. فرمت پاکسازی «پاک لیست سکوت» و «پاک لیست اخراج»
۴. گزارش فعالیت کمترین ادمین در ساعت ۱۱ شب (۲۳:۰۰)
"""
import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from modules import daily_admin_report
from modules import manual_mute_storage as mms
from modules import banned_storage as bs
from modules.group_storage import activate_group, set_group_owner
from modules.admin_storage import add_admin
from economy import activity, storage
from handlers.message_handler import handle_new_message


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
        self.reply_entities = []
        self._chat = FakeChat(chat_id)

    async def get_sender(self):
        return self.sender

    async def get_chat(self):
        return self._chat

    async def reply(self, text, **kwargs):
        self.replies.append(text)
        if "formatting_entities" in kwargs:
            self.reply_entities.append(kwargs["formatting_entities"])

    async def respond(self, text, **kwargs):
        self.replies.append(text)


class FakeAdminActions:
    def __init__(self):
        self.unmuted = []
        self.banned = []
        self.unbanned = []
        self.bot_account_id = 999999

    async def unmute_user(self, chat_id, user_id, *args, **kwargs):
        self.unmuted.append((chat_id, user_id))
        return True

    async def unban_user(self, chat_id, user_id, *args, **kwargs):
        self.unbanned.append((chat_id, user_id))
        return True


class FakeClient:
    def __init__(self, participants=None, entities=None):
        self._participants = participants or []
        self._entities = entities or {}
        self.sent_messages = []

    async def get_participants(self, chat, *args, **kwargs):
        return list(self._participants)

    async def iter_participants(self, chat, *args, **kwargs):
        for p in self._participants:
            yield p

    async def get_me(self):
        return FakeUser(999999, fn="ربات", username="bot")

    async def get_entity(self, target):
        if isinstance(target, str):
            clean = target.lstrip("@").lower()
            for p in self._participants:
                if getattr(p, "username", None) and p.username.lower() == clean:
                    return p
            if clean in self._entities:
                return self._entities[clean]
        elif isinstance(target, int):
            for p in self._participants:
                if p.id == target:
                    return p
            if target in self._entities:
                return self._entities[target]
        raise ValueError(f"Entity not found: {target}")

    async def send_message(self, chat_id, text, formatting_entities=None, **kwargs):
        self.sent_messages.append({
            "chat_id": chat_id,
            "text": text,
            "formatting_entities": formatting_entities,
        })
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
    def __init__(self, participants=None, entities=None):
        self.client = FakeClient(participants, entities)
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


def test_unmute_by_username(tmp_path, monkeypatch):
    """تست رفع سکوت با نام کاربری: رفع سکوت @username."""
    async def _runner():
        mms_file = tmp_path / "manual_mutes.json"
        monkeypatch.setattr(mms, "FILE", mms_file)
        monkeypatch.setattr(mms, "_cache", None)
        monkeypatch.setattr(mms, "_cache_mtime", None)

        chat_id = 771122
        owner = FakeUser(999001, "مالک", username="owner_boss")
        set_group_owner(chat_id, owner.id)
        activate_group(chat_id, "گروه تست رفع سکوت")

        target = FakeUser(445566, "کاربر", "سکوت‌شده", username="muted_guy")
        participants = [owner, target]
        bot = FakeBot(participants)

        # ثبت کاربر در لیست سکوت دستی
        mms.add_manual_mute(chat_id, target.id, username="muted_guy", display_name="کاربر سکوت‌شده")
        assert len(mms.get_muted_users(chat_id)) == 1

        # اجرای دستور: رفع سکوت @muted_guy
        event = FakeEvent(chat_id, owner, "رفع سکوت @muted_guy")
        await handle_new_message(bot, event)
        await asyncio.sleep(0.05)

        # بررسی نتایج
        assert any("سکوت کاربر برداشته شد" in r for r in event.replies)
        # خروج از manual_mute_storage
        assert len(mms.get_muted_users(chat_id)) == 0

    asyncio.run(_runner())


def test_list_and_clear_formatting(tmp_path, monkeypatch):
    """تست فرمت لیست سکوت و لیست اخراج به همراه موجودیت‌های نقل قول و ضخیم."""
    async def _runner():
        mms_file = tmp_path / "manual_mutes.json"
        monkeypatch.setattr(mms, "FILE", mms_file)
        monkeypatch.setattr(mms, "_cache", None)
        monkeypatch.setattr(mms, "_cache_mtime", None)

        bans_file = tmp_path / "banned_users.json"
        monkeypatch.setattr(bs, "FILE", bans_file)
        monkeypatch.setattr(bs, "_USE_SQLITE", False)
        monkeypatch.setattr(bs, "_cache", None)
        monkeypatch.setattr(bs, "_cache_mtime", None)

        chat_id = 334455
        owner = FakeUser(999002, "مالک", username="boss_owner")
        set_group_owner(chat_id, owner.id)
        activate_group(chat_id, "گروه تست فرمت")

        bot = FakeBot([owner])

        # افزودن ۳ کاربر به سکوت
        mms.add_manual_mute(chat_id, 101, username="user_one", display_name="اول")
        mms.add_manual_mute(chat_id, 102, username=None, display_name="دوم")
        mms.add_manual_mute(chat_id, 103, username="user_three", display_name=None)

        event_list_mute = FakeEvent(chat_id, owner, "لیست سکوت")
        await handle_new_message(bot, event_list_mute)

        reply_text = event_list_mute.replies[0]
        # بررسی تیتر و موجودیت‌ها
        assert "لیست کاربر سکوت شده توسط روباه" in reply_text
        assert "۱-  @user_one" in reply_text
        assert "۲-  دوم" in reply_text
        assert "۳-  @user_three" in reply_text
        assert len(event_list_mute.reply_entities) > 0
        entities = event_list_mute.reply_entities[0]
        # باید هم Bold و هم Blockquote داشته باشد
        from splusthon.tl.types import MessageEntityBold, MessageEntityBlockquote
        assert any(isinstance(e, MessageEntityBold) for e in entities)
        assert any(isinstance(e, MessageEntityBlockquote) for e in entities)

        # تست پاک لیست سکوت
        event_clear_mute = FakeEvent(chat_id, owner, "پاک لیست سکوت")
        await handle_new_message(bot, event_clear_mute)
        assert any("پاکسازی انجام شد تعداد = ( ۳ نفر ) ✓" in r for r in event_clear_mute.replies)
        assert mms.get_muted_users(chat_id) == []

        # افزودن ۲ کاربر به اخراج
        bs.add_banned(chat_id, 201, username="banned_one", display_name="اخراجی اول")
        bs.add_banned(chat_id, 202, username=None, display_name="اخراجی دوم")

        event_list_kick = FakeEvent(chat_id, owner, "لیست اخراج")
        await handle_new_message(bot, event_list_kick)

        reply_kick_text = event_list_kick.replies[0]
        assert "لیست کاربر اخراج شده توسط روباه" in reply_kick_text
        assert "۱-  @banned_one" in reply_kick_text
        assert "۲-  اخراجی دوم" in reply_kick_text
        entities_kick = event_list_kick.reply_entities[0]
        assert any(isinstance(e, MessageEntityBold) for e in entities_kick)
        assert any(isinstance(e, MessageEntityBlockquote) for e in entities_kick)

        # تست پاک لیست اخراج
        event_clear_kick = FakeEvent(chat_id, owner, "پاک لیست اخراج")
        await handle_new_message(bot, event_clear_kick)
        assert any("پاکسازی انجام شد تعداد = ( ۲ نفر ) ✓" in r for r in event_clear_kick.replies)
        assert bs.get_banned_list(chat_id) == []

    asyncio.run(_runner())


def test_daily_admin_report_selection_and_format(tmp_path, monkeypatch):
    """تست منطق گزارش کم‌فعالیت‌ترین ادمین:
    - ادمین‌های با صفر پیام نادیده گرفته می‌شوند.
    - از بین ادمین‌های با پیام (> 0)، کمترین انتخاب می‌شود (مثال ۱۰۰ و ۸۰ -> ۸۰).
    - فرمت نقل‌قول و پیام کامل بررسی می‌شود.
    """
    async def _runner():
        state_file = tmp_path / "daily_admin_report_state.json"
        monkeypatch.setattr(daily_admin_report, "FILE", state_file)
        monkeypatch.setattr(daily_admin_report, "_cache", None)
        monkeypatch.setattr(daily_admin_report, "_cache_mtime", None)

        chat_id = 998877
        owner = FakeUser(1001, "رضا", "مدیر", username="reza_admin")
        set_group_owner(chat_id, owner.id)
        activate_group(chat_id, "گروه تست ادمین‌ها")

        # ثبت ادمین دوم
        admin2 = FakeUser(1002, "محسن", "حسینی", username="mohsen_ad")
        add_admin(chat_id, admin2.id, admin2.username)

        # ثبت ادمین سوم که هیچ پیامی نداده (۰ پیام)
        admin_inactive = FakeUser(1003, "کامبیز", "ساکت", username="kambiz_silent")
        add_admin(chat_id, admin_inactive.id, admin_inactive.username)

        participants = [owner, admin2, admin_inactive]
        bot = FakeBot(participants)

        # ثبت پیام‌ها در اقتصاد/فعالیت:
        # ۱. ادمین اول ۱۰۰ پیام داد
        for _ in range(100):
            activity.record_message(chat_id, owner.id, name="رضا مدیر")
        # ۲. ادمین دوم ۸۰ پیام داد
        for _ in range(80):
            activity.record_message(chat_id, admin2.id, name="محسن حسینی")
        # ۳. کاربر عادی که ادمین نیست ۵ پیام داد (نباید انتخاب شود)
        for _ in range(5):
            activity.record_message(chat_id, 99999, name="کاربر عادی")

        # بررسی تابع get_least_active_admin_for_group
        report = await daily_admin_report.get_least_active_admin_for_group(bot, chat_id)
        assert report is not None
        # ادمین انتخاب‌شده باید محسن با ۸۰ پیام باشد
        assert report["admin"]["user_id"] == 1002
        assert report["count"] == 80
        assert "@mohsen_ad" in report["user_label"]
        assert "محسن" in report["user_label"]

        # بررسی تیتر در نقل قول
        header_expected = "ادمین جان : ﴿ محسن حسینی @mohsen_ad ﴾"
        assert header_expected in report["full_text"]
        # بررسی متن بدنه
        assert "امروز فقط « ۸۰ » ارسال کردی دسخوشش فکر نمیکنی یکم کم حرف زدی ! سعی کن از فردا بیشتر فعال باشی تا یه گروه شلوغ داشته باشیم دور هم" in report["full_text"]

        # بررسی ارسال و وضعیت پایدار
        sent = await daily_admin_report.send_daily_admin_report_for_group(bot, chat_id, force=False)
        assert sent is True
        assert len(bot.client.sent_messages) == 1
        msg = bot.client.sent_messages[0]
        assert msg["chat_id"] == chat_id
        assert header_expected in msg["text"]

        # تلاش مجدد بدون force نباید ارسال کند (چون امروز ارسال شده)
        sent2 = await daily_admin_report.send_daily_admin_report_for_group(bot, chat_id, force=False)
        assert sent2 is False
        assert len(bot.client.sent_messages) == 1

    asyncio.run(_runner())


def test_daily_admin_report_all_zero_messages(tmp_path, monkeypatch):
    """اگر همه ادمین‌ها صفر پیام داده باشند، هیچ گزارشی نباید ارسال شود."""
    async def _runner():
        state_file = tmp_path / "daily_admin_report_state.json"
        monkeypatch.setattr(daily_admin_report, "FILE", state_file)
        monkeypatch.setattr(daily_admin_report, "_cache", None)
        monkeypatch.setattr(daily_admin_report, "_cache_mtime", None)

        chat_id = 998866
        owner = FakeUser(2001, "مالک", username="owner_zero")
        set_group_owner(chat_id, owner.id)
        activate_group(chat_id, "گروه تست صفر پیام")

        admin = FakeUser(2002, "ادمین", username="admin_zero")
        add_admin(chat_id, admin.id, admin.username)

        bot = FakeBot([owner, admin])

        # هیچ پیامی ثبت نشده
        report = await daily_admin_report.get_least_active_admin_for_group(bot, chat_id)
        assert report is None

        sent = await daily_admin_report.send_daily_admin_report_for_group(bot, chat_id)
        assert sent is False
        assert len(bot.client.sent_messages) == 0

    asyncio.run(_runner())
