"""دفترچهٔ یوزرنیم نباید به‌خاطر scope محلی economy بترکد.

علت UnboundLocalError:
    داخل handle_new_message یک ``import economy`` دیرهنگام (شاخهٔ سایت بازی)
    نام economy را برای *کل* تابع محلی می‌کرد. بلوک USERNAME DIRECTORY
    خیلی زودتر اجرا می‌شود، پس economy هنوز مقدار محلی نداشت.

    python tests/test_username_directory_scope.py
"""
import asyncio
import inspect
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import economy
import economy.storage as storage
import handlers.message_handler as message_handler
import modules.group_storage as group_storage
from economy import directory

PASSED = FAILED = 0
CHAT = -10022770888


def check(label, cond, detail=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  PASS  {label}")
    else:
        FAILED += 1
        print(f"  FAIL  {label} {detail}")


def fresh():
    temp = Path(tempfile.mkdtemp())
    storage.use_file(temp / "economy.json")
    group_storage.activate_group(CHAT, "گروه تست")
    return temp


def test_economy_is_not_a_local_of_handle_new_message():
    print("\n### 🔎 economy نباید متغیر محلی handle_new_message باشد")
    names = message_handler.handle_new_message.__code__.co_varnames
    check(
        "economy در co_varnames نیست",
        "economy" not in names,
        f"-> {names}",
    )
    source = inspect.getsource(message_handler.handle_new_message)
    check(
        "داخل تابع import economy وجود ندارد",
        "import economy" not in source,
    )
    check(
        "ماژول economy در سطح فایل import شده",
        getattr(message_handler, "economy", None) is economy,
    )


def test_unboundlocal_reproduced_by_late_import():
    """همان الگوی باگ: استفادهٔ زودهنگام + import دیرهنگام."""
    print("\n### 🧨 الگوی UnboundLocalError با import دیرهنگام")

    def broken(chat_id, user_id, username):
        economy.directory.remember(chat_id, user_id, username)
        import economy  # noqa: F401 — همان اشتباه قبلی هندلر

    fresh()
    raised = None
    try:
        broken(CHAT, 11, "ali")
    except UnboundLocalError as error:
        raised = error
    check("الگوی قدیمی UnboundLocalError می‌دهد", raised is not None)
    check(
        "پیام خطا مربوط به economy است",
        raised is not None and "economy" in str(raised),
        f"-> {raised!r}",
    )


def test_handler_directory_block_with_several_users():
    print("\n### 📇 بلوک USERNAME DIRECTORY با چند کاربر")
    fresh()
    users = (
        (6401, "hosein"),
        (6402, "mina"),
        (9403, "FoxKing"),
        (2277, "@Ali_Reza"),
        (3001, None),
    )
    errors = []
    for user_id, username in users:
        try:
            economy.directory.remember(CHAT, user_id, username)
        except Exception as error:
            errors.append((user_id, username, repr(error)))
    check("هیچ UnboundLocalError ای رخ نداد", not errors, f"-> {errors}")
    check("hosein ثبت شد", directory.lookup(CHAT, "hosein") == "6401")
    check("mina ثبت شد", directory.lookup(CHAT, "mina") == "6402")
    check("FoxKing نرمال شد", directory.lookup(CHAT, "foxking") == "9403")
    check("Ali_Reza نرمال شد", directory.lookup(CHAT, "@ali_reza") == "2277")
    check("بدون یوزرنیم چیزی خراب نکرد", directory.lookup(CHAT, "none") is None)


class Logger:
    def __init__(self):
        self.info, self.errors = [], []

    def log_info(self, message):
        self.info.append(str(message))

    def log_error(self, message):
        self.errors.append(str(message))

    def log_deleted_message(self, **kwargs):
        return None

    def has(self, needle):
        return any(needle in m for m in self.info + self.errors)


class FakeClient:
    def __init__(self):
        self.sent = []

    async def get_entity(self, user_id):
        return types.SimpleNamespace(id=user_id, username=None, first_name="x")

    async def send_message(self, *args, **kwargs):
        self.sent.append((args, kwargs))
        return types.SimpleNamespace(id=1)

    def get_permissions(self, *args, **kwargs):
        raise RuntimeError("native admin should not run for this test")


class FakeBot:
    def __init__(self):
        self.client = FakeClient()
        self.logger = Logger()
        self.config_manager = types.SimpleNamespace(get=lambda *a, **k: None)
        self.tracker = types.SimpleNamespace(
            get_count=lambda *a, **k: 0,
            increment=lambda *a, **k: 0,
            reset_count=lambda *a, **k: None,
            should_punish=lambda *a, **k: False,
            is_banned=lambda *a, **k: False,
            is_muted=lambda *a, **k: False,
            banned_users={},
            muted_users={},
        )
        self.detector = types.SimpleNamespace(
            is_spam=lambda *a, **k: (False, None),
            has_public_username=lambda *a, **k: False,
        )
        self.bot_account_id = 555
        self.punished_users = set()
        self.spam_burst_messages = {}
        self.spammer_messages = {}
        self.spam_burst_users = set()
        self.rejoin_spam_state = {}
        self.group_timer_tasks = {}
        self.reply_input_peer_cache = {}
        self.forward_spam_counts = {}
        self.moderation_queue = types.SimpleNamespace(enqueue=lambda *a, **k: True)
        self.outgoing_sender = None
        self.admin_actions = types.SimpleNamespace()
        self.group_actions = types.SimpleNamespace()
        self.message_delete_queue = None
        self.rpc_governor = None
        self.group_dispatcher = None

    def is_spam_locked(self, key):
        return False

    def set_spam_lock(self, key):
        return None

    def clear_spam_lock(self, key):
        return None

    def touch_temporary_state(self, *args, **kwargs):
        return None

    def acquire_delete_notice_lock(self, chat_id):
        return False


class Message:
    def __init__(self, text, mid=2001):
        self.message = text
        self.id = mid
        self.entities = None
        self.file = None
        self.caption = None
        self.grouped_id = None


class User:
    def __init__(self, uid, username, name="علی"):
        self.id = uid
        self.first_name = name
        self.last_name = None
        self.username = username
        self.about = None
        self.bot = False


class Event:
    def __init__(self, text, user_id, username, chat_id=CHAT):
        self.message = Message(text)
        self.chat_id = chat_id
        self.is_private = False
        self.out = False
        self.replies = []
        self._user = User(user_id, username)
        self.sender = self._user
        self.sender_id = user_id
        self.chat = types.SimpleNamespace(id=chat_id, title="گروه تست")
        self.reply_to = None

    async def get_chat(self):
        return self.chat

    async def get_sender(self):
        return self._user

    async def reply(self, text, **kwargs):
        self.replies.append(text)
        return None

    async def respond(self, text, **kwargs):
        self.replies.append(text)
        return None


def test_handle_new_message_directory_does_not_log_failure():
    print("\n### 🔌 مسیر واقعی handle_new_message")
    fresh()

    async def scenario():
        bot = FakeBot()
        seen = []
        for uid, username, text in (
            (6401, "hosein", "سلام"),
            (6402, "mina", "موجودی"),
            (9403, "FoxKing", "سایت بازی"),
            (2277, "ali_reza", "لیست بازی"),
            (3001, None, "سلام دوباره"),
        ):
            event = Event(text, uid, username)
            await message_handler.handle_new_message(bot, event)
            seen.append((uid, username, event))
        return bot, seen

    bot, seen = asyncio.run(scenario())
    failed = [m for m in bot.logger.errors if "USERNAME DIRECTORY FAILED" in m]
    unbound = [m for m in bot.logger.errors if "UnboundLocalError" in m and "economy" in m]
    check("لاگ USERNAME DIRECTORY FAILED نیامد", not failed, f"-> {failed[:2]}")
    check("UnboundLocalError مربوط به economy در لاگ نیست", not unbound, f"-> {unbound[:2]}")
    check("hosein از مسیر هندلر ثبت شد", directory.lookup(CHAT, "hosein") == "6401")
    check("mina از مسیر هندلر ثبت شد", directory.lookup(CHAT, "mina") == "6402")
    check("FoxKing از مسیر هندلر ثبت شد", directory.lookup(CHAT, "foxking") == "9403")
    check("ali_reza از مسیر هندلر ثبت شد", directory.lookup(CHAT, "ali_reza") == "2277")


def main():
    test_economy_is_not_a_local_of_handle_new_message()
    test_unboundlocal_reproduced_by_late_import()
    test_handler_directory_block_with_several_users()
    test_handle_new_message_directory_does_not_log_failure()
    print(f"\npassed={PASSED} failed={FAILED}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
