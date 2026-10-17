"""تست عملکردی دستورهای مدیریتی جدید (لاگ، پاکسازی خودکار، حذف اخطار)."""
import asyncio
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import handlers.message_handler as mh
import modules.admin_tools as at

PASSED = FAILED = 0
CHAT = -777000888


def check(label, cond, detail=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  PASS  {label}")
    else:
        FAILED += 1
        print(f"  FAIL  {label} {detail}")


class Logger:
    def __init__(self):
        self.info, self.errors = [], []

    def log_info(self, m): self.info.append(m)
    def log_error(self, m): self.errors.append(m)


class ConfigManager:
    def get(self, key, default=None): return default


class Tracker:
    def __init__(self):
        self.data = {}

    def _k(self, chat, user): return (chat, user)

    def get_count(self, chat, user): return self.data.get(self._k(chat, user), 0)

    def increment(self, chat, user):
        k = self._k(chat, user)
        self.data[k] = self.data.get(k, 0) + 1
        return self.data[k]

    def decrement(self, chat, user):
        k = self._k(chat, user)
        n = max(self.data.get(k, 0) - 1, 0)
        if n == 0:
            self.data.pop(k, None)
        else:
            self.data[k] = n
        return n

    def reset_count(self, chat, user):
        self.data.pop(self._k(chat, user), None)

    def should_punish(self, chat, user): return False


class Detector:
    def is_spam(self, *a, **k): return False, None
    def check_message(self, *a, **k): return False, None


class User:
    def __init__(self, uid, name="علی", username=None):
        self.id = uid
        self.first_name = name
        self.last_name = None
        self.username = username


class Message:
    _n = 1000

    def __init__(self, text):
        Message._n += 1
        self.message = text
        self.id = Message._n
        self.file = None


class ReplyMessage:
    def __init__(self, sender_user):
        self._sender = sender_user

    async def get_sender(self):
        return self._sender


class ReplyTo:
    def __init__(self, msg_id):
        self.reply_to_msg_id = msg_id


class Event:
    def __init__(self, text, user_id, chat_id=CHAT, reply_sender=None):
        self.message = Message(text)
        self.chat_id = chat_id
        self.is_private = False
        self.replies = []
        self._user = User(user_id, username="owner")
        self.reply_to = ReplyTo(self.message.id) if reply_sender is not None else None
        self._reply_sender = reply_sender

    async def get_chat(self): return types.SimpleNamespace(id=self.chat_id)
    async def get_sender(self): return self._user
    async def reply(self, text, **kw): self.replies.append(text)
    async def respond(self, text, **kw): self.replies.append(text)

    def said(self, needle):
        return any(needle in m for m in self.replies)


class Client:
    def __init__(self, reply_sender=None):
        self.reply_sender = reply_sender
        self.sent = []
        self.get_messages_calls = []

    async def get_messages(self, chat_id, ids=None, limit=None):
        self.get_messages_calls.append((chat_id, ids, limit))
        if ids is not None and self.reply_sender is not None:
            return ReplyMessage(self.reply_sender)
        return []

    async def delete_messages(self, *a, **k): return None
    async def send_message(self, target, text, **kw):
        self.sent.append((target, text)); return True


def build_bot(reply_sender=None):
    bot = types.SimpleNamespace()
    bot.logger = Logger()
    bot.config_manager = ConfigManager()
    bot.tracker = Tracker()
    bot.detector = Detector()
    bot.client = Client(reply_sender)
    bot.group_timer_tasks = {}
    bot.bot_account_id = 555
    bot.punished_users = set()
    bot.spam_burst_messages = {}
    bot.spammer_messages = {}
    bot.spam_burst_users = set()
    bot.moderation_queue = types.SimpleNamespace(enqueue=lambda *a, **k: True)
    bot.admin_actions = types.SimpleNamespace()
    bot.group_actions = types.SimpleNamespace(
        lock_group=lambda *a, **k: None,
        unlock_group=lambda *a, **k: None)
    bot.cleanup_tasks = {}
    return bot


def _owner_id():
    from modules.owner_check import get_owner
    return get_owner()["user_id"]


async def drive(bot, event):
    await mh.handle_new_message(bot, event)


def test_admin_log_command():
    at._ADMIN_LOG_FILE.write_text("{}", encoding="utf-8")
    at.log_action(CHAT, {"username": "admin1"}, "اخطار", target={"username": "user1"})
    bot = build_bot()
    ev = Event("لاگ مدیریتی", _owner_id())
    asyncio.run(drive(bot, ev))
    check("لاگ مدیریتی پاسخ داد", ev.said("لاگ مدیریتی گروه"), f"{ev.replies}")
    check("اقدام در لاگ هست", any("اخطار" in r for r in ev.replies))
    check("شناسهٔ عددی در پاسخ نیست",
          not any("-777000888" in r or "777000888" in r for r in ev.replies))
    at.clear_log(CHAT)


def test_admin_log_denied_for_regular_user():
    at._ADMIN_LOG_FILE.write_text("{}", encoding="utf-8")
    bot = build_bot()
    # کاربرِ عادی (نه مالک، نه ادمین)
    ev = Event("لاگ مدیریتی", 99999999)
    asyncio.run(drive(bot, ev))
    check("کاربر عادی رد شد", ev.said("فقط مالک یا ادمین"), f"{ev.replies}")


def test_auto_cleanup_flow():
    at._PENDING_CLEANUP.pop(CHAT, None)
    at._CLEANUP_FILE.write_text("{}", encoding="utf-8")
    bot = build_bot()
    owner = _owner_id()

    ev1 = Event("پاکسازی خودکار", owner)
    asyncio.run(drive(bot, ev1))
    check("روز پرسیده شد", ev1.said("امروز") and ev1.said("فردا"),
          f"{ev1.replies}")

    ev2 = Event("فردا", owner)
    asyncio.run(drive(bot, ev2))
    check("ساعت پرسیده شد", ev2.said("ساعت انجام پاکسازی"), f"{ev2.replies}")

    ev3 = Event("15:12", owner)
    asyncio.run(drive(bot, ev3))
    check("تعداد پرسیده شد", ev3.said("چه تعداد پیام"), f"{ev3.replies}")

    ev4 = Event("800", owner)
    asyncio.run(drive(bot, ev4))
    check("تنظیم تأیید شد", ev4.said("پاکسازی خودکار تنظیم شد"), f"{ev4.replies}")

    rec = at.get_cleanup(CHAT)
    check("تنظیم ذخیره شد",
          rec and rec["day"] == "tomorrow" and rec["time"] == "15:12"
          and rec["count"] == 800, f"{rec}")
    check("scheduled_at ذخیره شد", rec and bool(rec.get("scheduled_at")))
    check("set_at ذخیره شد", rec and bool(rec.get("set_at")))
    check("نمایشِ خوانا شامل زمان پاکسازی است",
          "زمان پاکسازی" in at.format_cleanup(CHAT))
    at.clear_cleanup(CHAT)


def test_auto_cleanup_past_time_rolls_to_tomorrow():
    at._PENDING_CLEANUP.pop(CHAT, None)
    bot = build_bot()
    owner = _owner_id()

    ev1 = Event("پاکسازی خودکار", owner)
    asyncio.run(drive(bot, ev1))
    ev2 = Event("امروز", owner)
    asyncio.run(drive(bot, ev2))
    # یک ساعت قطعاً گذشته (00:01)
    ev3 = Event("00:01", owner)
    asyncio.run(drive(bot, ev3))
    # ادامه می‌یابد (ساعتِ گذشته اعلام می‌شود ولی جریان متوقف نمی‌شود)
    check("بعد از ساعتِ گذشته، تعداد پرسیده شد", ev3.said("چه تعداد پیام"),
          f"{ev3.replies}")
    at._PENDING_CLEANUP.pop(CHAT, None)


def test_cleanup_day_time_of_day():
    check("شب", at.time_of_day(2) == "شب")
    check("صبح", at.time_of_day(9) == "صبح")
    check("ظهر", at.time_of_day(14) == "ظهر")
    check("عصر", at.time_of_day(17) == "عصر")
    check("شب دیرهنگام", at.time_of_day(22) == "شب")
    check("امروز", at.valid_day("امروز") == "today")
    check("فردا", at.valid_day("فردا") == "tomorrow")
    check("نامعتبر", at.valid_day("هرگز") is None)


def test_remove_warning_reply():
    at._ADMIN_LOG_FILE.write_text("{}", encoding="utf-8")
    bot = build_bot(reply_sender=User(4242, name="کاربر هدف"))
    # سه اخطار بده؛ «حذف اخطار» باید همهٔ سوابقِ همان کاربر را یکجا حذف کند
    bot.tracker.increment(CHAT, 4242)
    bot.tracker.increment(CHAT, 4242)
    bot.tracker.increment(CHAT, 4242)
    check("سه اخطار موجود", bot.tracker.get_count(CHAT, 4242) == 3)
    # کاربر دیگری در همان گروه و همان کاربر در گروه دیگر (برای اطمینان از ایزوله‌بودن)
    bot.tracker.increment(CHAT, 9999)
    bot.tracker.increment(-424242, 4242)
    ev = Event("حذف اخطار", _owner_id(), reply_sender=User(4242, name="کاربر هدف"))
    asyncio.run(drive(bot, ev))
    check("حذف اخطار پاسخ داد",
          ev.said("تمام اخطارهای کاربر حذف شد"), f"{ev.replies}")
    check("تخلفات کاربر به صفر رسید", bot.tracker.get_count(CHAT, 4242) == 0)
    check("کاربرِ دیگر دست‌نخورده", bot.tracker.get_count(CHAT, 9999) == 1)
    check("گروهِ دیگر دست‌نخورده", bot.tracker.get_count(-424242, 4242) == 1)
    at.clear_log(CHAT)


def test_remove_warning_denied_for_regular_user():
    bot = build_bot(reply_sender=User(4242, name="کاربر هدف"))
    ev = Event("حذف اخطار", 99999999, reply_sender=User(4242))
    asyncio.run(drive(bot, ev))
    check("کاربر عادی برای حذف اخطار رد شد",
          ev.said("فقط مالک یا ادمین"), f"{ev.replies}")


def test_remove_all_warnings_resets_to_zero():
    at._ADMIN_LOG_FILE.write_text("{}", encoding="utf-8")
    bot = build_bot(reply_sender=User(5151, name="کاربر هدف"))
    # سه اخطار بده، بعد «حذف اخطارها» همه را صفر کند
    bot.tracker.increment(CHAT, 5151)
    bot.tracker.increment(CHAT, 5151)
    bot.tracker.increment(CHAT, 5151)
    check("سه اخطار موجود", bot.tracker.get_count(CHAT, 5151) == 3)
    ev = Event("حذف اخطارها", _owner_id(), reply_sender=User(5151, name="کاربر هدف"))
    asyncio.run(drive(bot, ev))
    check("حذف اخطارها پاسخ داد",
          ev.said("همهٔ اخطارهای کاربر حذف شد"), f"{ev.replies}")
    check("همهٔ اخطارها صفر شد", bot.tracker.get_count(CHAT, 5151) == 0)
    at.clear_log(CHAT)


def test_zero_only_owner_and_group_owner():
    at._ADMIN_LOG_FILE.write_text("{}", encoding="utf-8")
    bot = build_bot(reply_sender=User(7777, name="هدف"))
    bot.tracker.increment(CHAT, 7777)
    # کاربرِ عادی → رد
    ev_regular = Event("صفر", 99999999, reply_sender=User(7777))
    asyncio.run(drive(bot, ev_regular))
    check("کاربر عادی برای «صفر» رد شد",
          ev_regular.said("فقط مالک اصلی ربات یا مالک گروه"), f"{ev_regular.replies}")
    # مالک اصلی → مجاز
    bot.tracker.increment(CHAT, 7777)
    ev_owner = Event("صفر", _owner_id(), reply_sender=User(7777))
    asyncio.run(drive(bot, ev_owner))
    check("مالک اصلی «صفر» را اجرا کرد",
          ev_owner.said("تخلفات کاربر صفر شد"), f"{ev_owner.replies}")
    check("تخلفات صفر شد", bot.tracker.get_count(CHAT, 7777) == 0)
    at.clear_log(CHAT)


def main():
    test_admin_log_command()
    test_admin_log_denied_for_regular_user()
    test_auto_cleanup_flow()
    test_auto_cleanup_past_time_rolls_to_tomorrow()
    test_cleanup_day_time_of_day()
    test_remove_warning_reply()
    test_remove_warning_denied_for_regular_user()
    test_remove_all_warnings_resets_to_zero()
    test_zero_only_owner_and_group_owner()
    print(f"\npassed={PASSED} failed={FAILED}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
