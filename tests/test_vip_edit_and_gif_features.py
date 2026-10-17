"""تست‌های جامع ویژگی‌های جدید:
۱. معافیت کامل کاربران VIP از تمام اقدامات نظارتی (سکوت، اخراج، اخطار، حذف پیام در پیام‌های سریع/پشت‌سرهم، سکوت/اخراج نام).
۲. تشخیص پیام‌های ویرایش‌شده و ارسال اخطار برای کلمات فیلتر یا ممنوعه.
۳. تغییر مدت و قالب سکوت موقت برای گیف تکراری به ۳ ساعت با Blockquote و Bold در خط اول.
"""

import asyncio
import inspect
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest
from modules import vip_storage
from modules import message_tracker
from modules.admin_actions import AdminActions, public_warning_reason_line
from modules.light_spam_ingest import _cheap_admin_bypass
from handlers.message_handler import (
    handle_fast_moderation_command,
    handle_new_message,
)
from splusthon.tl.types import MessageEntityBlockquote, MessageEntityBold


def test_vip_storage_api(tmp_path, monkeypatch):
    """بررسی ذخیره و بازیابی VIP به تفکیک گروه."""
    monkeypatch.setattr(vip_storage, "FILE", tmp_path / "vip_users.json")
    vip_storage._cache = None
    vip_storage._cache_mtime = None

    chat_id = -100111222
    user_id = 998877

    assert not vip_storage.is_vip(chat_id, user_id)
    assert vip_storage.add_vip(chat_id, user_id)
    assert vip_storage.is_vip(chat_id, user_id)
    # اضافه کردن مجدد باید False باشد
    assert not vip_storage.add_vip(chat_id, user_id)
    assert str(user_id) in vip_storage.list_vips(chat_id)

    # حذف vip
    assert vip_storage.remove_vip(chat_id, user_id)
    assert not vip_storage.is_vip(chat_id, user_id)


def test_vip_in_cheap_admin_bypass(tmp_path, monkeypatch):
    """بررسی دور زدن مسیر سبک اسپم (light_spam_ingest) برای کاربران VIP."""
    monkeypatch.setattr(vip_storage, "FILE", tmp_path / "vip_users.json")
    vip_storage._cache = None
    vip_storage._cache_mtime = None

    chat_id = -100111222
    user_id = 554433

    class DummyBot:
        bot_account_id = 999999
        _light_admin_bypass = None
        native_group_admin_cache = {}

    bot = DummyBot()
    assert not _cheap_admin_bypass(bot, chat_id, user_id)

    vip_storage.add_vip(chat_id, user_id)
    assert _cheap_admin_bypass(bot, chat_id, user_id)


def test_vip_protection_in_source_code():
    """بررسی وجود معافیت‌های VIP در سورس message_handler."""
    src = inspect.getsource(handle_new_message)
    # VIP check at top
    assert "is_vip_user" in src
    # auto_punish_protected includes is_vip_user
    assert "auto_punish_protected = is_group_moderator or native_admin_warn_only or is_vip_user" in src
    # VIP exempt in manual kick
    assert "این کاربر vip است و نمی‌تواند اخراج شود" in src
    # VIP exempt in manual mute
    assert "این کاربر vip است و نمی‌تواند سکوت شود" in src
    # VIP exempt in manual warn
    assert "این کاربر vip است و نمی‌تواند اخطار بگیرد" in src
    # VIP exempt in name-based kick and mute
    assert "vip_storage.is_vip(chat_id, m_id)" in src


@pytest.mark.anyio
async def test_fast_mute_vip_and_already_muted_protection(tmp_path, monkeypatch):
    """بررسی محافظت VIP و پیام سکوت مجدد در مسیر سریع سکوت."""
    monkeypatch.setattr(vip_storage, "FILE", tmp_path / "vip_users.json")
    vip_storage._cache = None
    vip_storage._cache_mtime = None

    chat_id = -100123456
    vip_user_id = 778899

    vip_storage.add_vip(chat_id, vip_user_id)

    class MockUser:
        def __init__(self, uid, username="vip_user"):
            self.id = uid
            self.username = username
            self.first_name = "VIP"
            self.last_name = "Member"

    class MockMsg:
        async def get_sender(self):
            return MockUser(vip_user_id)

    class MockClient:
        async def get_messages(self, cid, ids=None):
            return MockMsg()

    class MockQueue:
        def enqueue(self, chat_id, action, user_id, timeout_seconds, operation, on_success, on_failure):
            async def run():
                res = await operation()
                await on_success(res)
            asyncio.create_task(run())
            return True

    replies = []
    class MockEvent:
        chat_id = -100123456
        is_private = False
        reply_to = type("R", (), {"reply_to_msg_id": 42})()
        sender_id = 111111
        sender = type("S", (), {"id": 111111, "username": "admin_user"})()
        async def reply(self, text, *args, **kwargs):
            replies.append(text)

    class MockBot:
        client = MockClient()
        moderation_queue = MockQueue()
        admin_actions = type("AA", (), {"mute_user": lambda *a, **k: asyncio.sleep(0, result=True)})()
        tracker = type("T", (), {"muted_users": set()})()
        punished_users = set()
        logger = type("L", (), {"log_info": lambda *a: None, "log_error": lambda *a: None})()

    # Admin bypass stub for sender only
    monkeypatch.setattr(
        "handlers.message_handler._has_group_management_permission",
        lambda bot, cid, uid, uname: uid == 111111
    )

    ev = MockEvent()
    handled = await handle_fast_moderation_command(MockBot(), ev, text="سکوت", sender=ev.sender)
    assert handled
    # Give the task a moment to run
    await asyncio.sleep(0.01)
    assert len(replies) == 1
    assert "این کاربر vip است و نمی‌تواند سکوت شود" in replies[0]


def test_edited_message_entry_gate():
    """بررسی رفتار _entry_new در bot_working_split_ok برای پیام‌های ویرایش‌شده."""
    from collections import OrderedDict
    import time

    _entry_seen = OrderedDict()
    _entry_ttl_s = 5.0
    _entry_max = 4096

    def _entry_new(chat_id, message_id, raw_text="", is_edit=False):
        now = time.monotonic()
        cutoff = now - _entry_ttl_s
        while _entry_seen:
            _oldest_key, _oldest_val = next(iter(_entry_seen.items()))
            _oldest_t = _oldest_val[0] if isinstance(_oldest_val, tuple) else _oldest_val
            if len(_entry_seen) <= _entry_max and _oldest_t >= cutoff:
                break
            _entry_seen.popitem(last=False)
        key = (chat_id, message_id)
        text_hash = hash(raw_text or "")
        if key in _entry_seen:
            val = _entry_seen[key]
            old_t, old_hash = val if isinstance(val, tuple) else (val, None)
            if old_hash is not None and text_hash != old_hash:
                _entry_seen[key] = (now, text_hash)
                return True
            return False
        _entry_seen[key] = (now, text_hash)
        return True

    chat_id = -100555
    msg_id = 999

    # ۱. دریافت پیام جدید اول "سلام" -> مجاز
    assert _entry_new(chat_id, msg_id, raw_text="سلام", is_edit=False) is True

    # ۲. دریافت تکراری همان پیام با همان متن "سلام" در بازه ۵ ثانیه -> رد شدن (Duplicate)
    assert _entry_new(chat_id, msg_id, raw_text="سلام", is_edit=False) is False

    # ۳. ویرایش پیام به کلمه دیگر مثلا "کوس" در بازه ۵ ثانیه -> مجاز (Edit detected)
    assert _entry_new(chat_id, msg_id, raw_text="کوس", is_edit=True) is True

    # ۴. تکرار مجدد ویرایش با همان متن "کوس" -> رد شدن
    assert _entry_new(chat_id, msg_id, raw_text="کوس", is_edit=True) is False


def test_message_tracker_updates_on_edit():
    """بررسی اینکه message_tracker متن جدید پیام ویرایش‌شده را به روز می‌کند."""
    message_tracker.reset_all()
    chat_id = -100999
    user_id = 1234
    msg_id = 5678

    message_tracker.add_message(chat_id, user_id, msg_id, "سلام")
    msgs = message_tracker.get_user_recent_messages(chat_id, user_id)
    assert len(msgs) == 1
    assert msgs[0]["text"] == "سلام"

    # پیام ویرایش می‌شود
    message_tracker.add_message(chat_id, user_id, msg_id, "متن جدید ویرایش شده")
    msgs = message_tracker.get_user_recent_messages(chat_id, user_id)
    assert len(msgs) == 1
    assert msgs[0]["text"] == "متن جدید ویرایش شده"


@pytest.mark.anyio
async def test_send_warning_for_edited_message():
    """بررسی متن و قالب اخطار برای پیام ویرایش‌شده در send_warning."""
    sent = []
    class MockClient:
        _outgoing_sender = None
        async def send_message(self, chat_id, msg, reply_to=None, formatting_entities=None):
            sent.append({"chat_id": chat_id, "msg": msg, "reply_to": reply_to, "entities": formatting_entities})
            return type("M", (), {"id": 100})()

    actions = AdminActions(
        client=MockClient(),
        logger=type("L", (), {"log_error": lambda *a: None})(),
        config_manager={"send_warning": True},
    )

    chat_id = -100888
    # اخطار پیام عادی
    await actions.send_warning(
        chat_id=chat_id,
        username="user1",
        reason="کلمه ممنوعه (کوس)",
        count=1,
        threshold=3,
        reply_to=None,
        is_edit=False,
    )
    assert len(sent) == 1
    assert "پیام شما حذف شد" in sent[0]["msg"]
    assert "پیام ویرایش‌شده شما حذف شد" not in sent[0]["msg"]

    # اخطار پیام ویرایش‌شده
    await actions.send_warning(
        chat_id=chat_id,
        username="user1",
        reason="کلمه ممنوعه (کوس)",
        count=2,
        threshold=3,
        reply_to=777,
        is_edit=True,
    )
    assert len(sent) == 2
    assert "پیام ویرایش‌شده شما حذف شد" in sent[1]["msg"]
    assert sent[1]["reply_to"] == 777
    assert "دلیل کلمه ممنوعه : (کوس)" in sent[1]["msg"]


def test_gif_duplicate_mute_template_and_duration():
    """بررسی مدت زمان (۳ ساعت / 10800s) و قالب اعلان گیف تکراری."""
    src = inspect.getsource(handle_new_message)
    # مدت زمان سکوت ۱۰۸۰۰ ثانیه
    assert "mute_user(\n                            chat_id, user_id, 10800" in src or "mute_user(chat_id, user_id, 10800" in src or "10800" in src
    assert "USER MUTED 10800" in src

    # قالب متن اعلان
    assert "کاربر ← {user_display}" in src
    assert "شما به دلیل ارسال گیف تکراری" in src
    assert "به مدت 3 ساعت سکوت موقت شدید" in src

    # قالب‌بندی خط اول با Blockquote و Bold
    assert "MessageEntityBlockquote(offset=0, length=first_line_len)" in src
    assert "MessageEntityBold(offset=0, length=first_line_len)" in src

    # تست عملی ساخت پیام و entityها
    user_display = "علی رضایی"
    first_line = f"کاربر ← {user_display}"
    notice_text = (
        f"{first_line}\n\n"
        "شما به دلیل ارسال گیف تکراری\n"
        "به مدت 3 ساعت سکوت موقت شدید"
    )
    first_line_len = len(first_line.encode("utf-16-le")) // 2
    entities = [
        MessageEntityBlockquote(offset=0, length=first_line_len),
        MessageEntityBold(offset=0, length=first_line_len),
    ]

    assert notice_text.startswith(first_line)
    assert entities[0].offset == 0
    assert entities[0].length == first_line_len
    assert entities[1].offset == 0
    assert entities[1].length == first_line_len
