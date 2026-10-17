"""Owner commands must run on a fast path, not the heavy group pipeline."""
import asyncio
import sys
import types
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if "splusthon" not in sys.modules:
    fake = types.ModuleType("splusthon")
    fake.Button = object
    fake.types = types.ModuleType("splusthon.types")
    tl = types.ModuleType("splusthon.tl")
    tl_types = types.ModuleType("splusthon.tl.types")

    class _Ent:
        def __init__(self, offset=0, length=0, **_kwargs):
            self.offset = offset
            self.length = length

    tl_types.MessageEntityBold = _Ent
    tl_types.MessageEntityBlockquote = _Ent
    tl.types = tl_types
    tl.functions = types.ModuleType("splusthon.tl.functions")
    fake.tl = tl
    sys.modules["splusthon"] = fake
    sys.modules["splusthon.tl"] = tl
    sys.modules["splusthon.tl.types"] = tl_types
    sys.modules["splusthon.tl.functions"] = tl.functions
    sys.modules["splusthon.types"] = fake.types

import handlers.message_handler as handler
from modules.group_dispatch import PRIORITY_ADMIN, classify_priority
from modules.owner_check import get_owner
import modules.group_storage as group_storage

PASSED = FAILED = 0
CHAT = -88001122


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
        self.infos = []
        self.errors = []

    def log_info(self, message):
        self.infos.append(message)

    def log_error(self, message):
        self.errors.append(message)


class Config:
    def get(self, key, default=None):
        return default


class Tracker:
    def reset_count(self, *a, **k):
        return None

    def get_count(self, *a, **k):
        return 0


class User:
    def __init__(self, uid, name="مالک", username="osine1"):
        self.id = uid
        self.first_name = name
        self.last_name = None
        self.username = username


class Message:
    def __init__(self, text, mid=9):
        self.message = text
        self.id = mid
        self.file = None
        self.caption = None


class ReplyMessage:
    def __init__(self, sender):
        self._sender = sender

    async def get_sender(self):
        return self._sender


class ReplyTo:
    def __init__(self, msg_id):
        self.reply_to_msg_id = msg_id


class Event:
    def __init__(self, text, user, chat_id=CHAT, title="گروه تست",
                 reply_sender=None):
        self.message = Message(text)
        self.chat_id = chat_id
        self.sender_id = user.id
        self.sender = user
        self.chat = SimpleNamespace(id=chat_id, title=title)
        self.is_private = False
        self.replies = []
        self.responds = []
        self.get_chat_calls = 0
        self.get_sender_calls = 0
        self.reply_to = ReplyTo(self.message.id) if reply_sender else None
        self._reply_sender = reply_sender

    async def get_chat(self):
        self.get_chat_calls += 1
        return self.chat

    async def get_sender(self):
        self.get_sender_calls += 1
        return self.sender

    async def reply(self, text, **_kw):
        self.replies.append(text)
        return SimpleNamespace(id=1)

    async def respond(self, text, **_kw):
        self.responds.append(text)
        return SimpleNamespace(id=2)


class Client:
    def __init__(self, reply_sender=None):
        self.reply_sender = reply_sender
        self.get_messages_calls = []
        self.get_permissions_calls = []
        self.get_me_calls = 0
        self.get_entity_calls = []

    async def get_messages(self, chat_id, ids=None, limit=None):
        self.get_messages_calls.append((chat_id, ids, limit))
        if ids is not None and self.reply_sender is not None:
            return ReplyMessage(self.reply_sender)
        return None

    async def get_permissions(self, chat_id, user):
        self.get_permissions_calls.append((chat_id, user))
        return SimpleNamespace(is_admin=False)

    async def get_me(self):
        self.get_me_calls += 1
        return SimpleNamespace(id=1, username="aifox")

    async def get_entity(self, value):
        self.get_entity_calls.append(value)
        return SimpleNamespace(id=value)

    async def get_input_entity(self, value):
        return SimpleNamespace(id=value)

    async def __call__(self, *_a, **_k):
        return SimpleNamespace(users=[], participants=[], full_chat=None)


def _owner():
    return User(get_owner()["user_id"])


def _bot(client=None):
    return SimpleNamespace(
        logger=Logger(),
        config_manager=Config(),
        tracker=Tracker(),
        client=client or Client(),
        group_timer_tasks={},
        bot_account_id=555,
        punished_users=set(),
        reply_input_peer_cache={},
        native_group_admin_cache={},
    )


def _isolate_groups(tmp_path):
    original = group_storage.FILE
    group_storage.FILE = tmp_path
    group_storage._cache = None
    group_storage._cache_mtime = None
    return original


def _restore_groups(original):
    group_storage.FILE = original
    group_storage._cache = None
    group_storage._cache_mtime = None


def test_fast_owner_commands_are_detected():
    print("\n### تشخیص دستورهای مسیر سریع")
    expected = {"فعال", "فعال سازی", "غیر فعال", "ثبت گروه", "ثبت مالک"}
    check("FAST_OWNER_COMMANDS defined",
          hasattr(handler, "FAST_OWNER_COMMANDS"))
    check("is_fast_owner_command defined",
          hasattr(handler, "is_fast_owner_command"))
    check("set matches expected commands",
          set(handler.FAST_OWNER_COMMANDS) == expected,
          f"-> {handler.FAST_OWNER_COMMANDS}")
    source = (ROOT / "core" / "bot_working_split_ok.py").read_text(encoding="utf-8")
    check("bot imports is_fast_owner_command",
          "is_fast_owner_command" in source and "handle_fast_owner_command" in source)
    check("bot uses is_fast_owner_command in priority path",
          "if is_fast_owner_command(text):" in source)
    check("priority path has fallback on detection failure",
          "FAST OWNER COMMAND FALLBACK" in source)


def test_process_priority_command_has_no_nameerror():
    print("\n### process_priority_command بدون NameError اجرا می‌شود")
    from modules.broadcast_state import normalize_command_text

    async def run_priority_check(event_text, is_private=False):
        text = normalize_command_text(event_text)
        if is_private:
            return "private"
        try:
            if handler.is_fast_owner_command(text):
                return "fast"
        except Exception:
            return "fallback"
        return "other"

    for command in ("فعال", "فعال سازی", "غیر فعال", "ثبت گروه", "ثبت مالک"):
        result = asyncio.run(run_priority_check(command))
        check(f"{command!r} routed to fast path", result == "fast", f"-> {result}")
    result = asyncio.run(run_priority_check("ثبت گروه", is_private=True))
    check("private skips fast path", result == "private")
    result = asyncio.run(run_priority_check("قفل"))
    check("قفل is not a FAST_OWNER command", result == "other")


def test_lane_is_admin_not_normal():
    print("\n### این دستورات روی lane ادمین می‌مانند")
    for text in ("فعال", "ثبت گروه", "ثبت مالک"):
        priority, kind = classify_priority(text)
        check(f"{text!r} admin lane",
              priority == PRIORITY_ADMIN and kind == "admin",
              f"-> {priority} {kind}")
    check("فعال in FAST_OWNER_COMMANDS",
          "فعال" in handler.FAST_OWNER_COMMANDS)
    check("ثبت گروه in FAST_OWNER_COMMANDS",
          "ثبت گروه" in handler.FAST_OWNER_COMMANDS)
    check("ثبت مالک in FAST_OWNER_COMMANDS",
          "ثبت مالک" in handler.FAST_OWNER_COMMANDS)


def test_register_group_is_direct():
    print("\n### ثبت گروه مستقیم ذخیره و پاسخ می‌دهد")
    tmp = ROOT / "config" / "_test_fast_owner_groups.json"
    original = _isolate_groups(tmp)
    try:
        if tmp.exists():
            tmp.unlink()
        bot = _bot()
        ev = Event("ثبت گروه", _owner(), title="گروه فوری")
        handled = asyncio.run(handler.handle_fast_owner_command(bot, ev, "ثبت گروه"))
        check("handled", handled is True)
        check("saved active", group_storage.is_active(CHAT) is True)
        check("title stored",
              group_storage.load_groups().get(str(CHAT), {}).get("title")
              == "گروه فوری")
        check("reply sent", any("ثبت شد" in item for item in ev.replies),
              f"-> {ev.replies}")
        check("no get_chat when title cached", ev.get_chat_calls == 0,
              f"-> {ev.get_chat_calls}")
        check("no get_permissions", bot.client.get_permissions_calls == [])
        check("no get_me", bot.client.get_me_calls == 0)
        infos = bot.logger.infos
        check("COMMAND RECEIVED",
              any(item.startswith("COMMAND RECEIVED") and "ثبت گروه" in item
                  for item in infos), f"-> {infos}")
        check("COMMAND HANDLER START",
              any(item.startswith("COMMAND HANDLER START") for item in infos))
        check("COMMAND SAVED",
              any(item.startswith("COMMAND SAVED") for item in infos))
        check("COMMAND RESPONSE SENT",
              any(item.startswith("COMMAND RESPONSE SENT") for item in infos))
    finally:
        _restore_groups(original)
        if tmp.exists():
            tmp.unlink()


def test_register_owner_is_direct():
    print("\n### ثبت مالک مستقیم ذخیره می‌شود")
    tmp = ROOT / "config" / "_test_fast_owner_groups.json"
    original = _isolate_groups(tmp)
    try:
        if tmp.exists():
            tmp.unlink()
        target = User(4242, name="هدف", username="target")
        bot = _bot(Client(reply_sender=target))
        ev = Event("ثبت مالک", _owner(), reply_sender=target)
        handled = asyncio.run(handler.handle_fast_owner_command(bot, ev, "ثبت مالک"))
        check("handled", handled is True)
        check("owner saved", group_storage.get_group_owner(CHAT) == 4242,
              f"-> {group_storage.get_group_owner(CHAT)}")
        check("reply mentions target",
              any("ثبت شد" in item for item in ev.replies), f"-> {ev.replies}")
        check("one get_messages", len(bot.client.get_messages_calls) == 1,
              f"-> {bot.client.get_messages_calls}")
        check("no get_permissions", bot.client.get_permissions_calls == [])
        check("COMMAND SAVED logged",
              any(item.startswith("COMMAND SAVED") for item in bot.logger.infos))
    finally:
        _restore_groups(original)
        if tmp.exists():
            tmp.unlink()


def test_non_owner_keeps_same_replies():
    print("\n### کاربر عادی همان پیام خطای قبلی را می‌گیرد")
    tmp = ROOT / "config" / "_test_fast_owner_groups.json"
    original = _isolate_groups(tmp)
    try:
        if tmp.exists():
            tmp.unlink()
        bot = _bot()
        stranger = User(999001)
        ev_group = Event("ثبت گروه", stranger)
        asyncio.run(handler.handle_fast_owner_command(bot, ev_group, "ثبت گروه"))
        check("ثبت گروه denied",
              any("فقط مالک ربات اجازه ثبت گروه دارد" in item
                  for item in ev_group.replies), f"-> {ev_group.replies}")
        check("group not saved", group_storage.is_active(CHAT) is False)
        ev_owner = Event("ثبت مالک", stranger, reply_sender=User(1))
        asyncio.run(handler.handle_fast_owner_command(bot, ev_owner, "ثبت مالک"))
        check("ثبت مالک denied",
              any("فقط مالک اصلی ربات اجازه ثبت مالک گروه را دارد" in item
                  for item in ev_owner.replies), f"-> {ev_owner.replies}")
    finally:
        _restore_groups(original)
        if tmp.exists():
            tmp.unlink()


def test_handle_new_message_skips_heavy_path():
    print("\n### handle_new_message قبل از get_chat/native admin قطع می‌شود")
    tmp = ROOT / "config" / "_test_fast_owner_groups.json"
    original = _isolate_groups(tmp)
    try:
        if tmp.exists():
            tmp.unlink()
        bot = _bot()
        ev = Event("ثبت گروه", _owner(), title="گروه زود")
        asyncio.run(handler.handle_new_message(bot, ev))
        check("registered via early path", group_storage.is_active(CHAT) is True)
        check("no get_chat", ev.get_chat_calls == 0, f"-> {ev.get_chat_calls}")
        check("no get_sender", ev.get_sender_calls == 0,
              f"-> {ev.get_sender_calls}")
        check("no get_permissions", bot.client.get_permissions_calls == [])
        check("no get_me", bot.client.get_me_calls == 0)
        check("reply sent", any("ثبت شد" in item for item in ev.replies),
              f"-> {ev.replies}")
    finally:
        _restore_groups(original)
        if tmp.exists():
            tmp.unlink()


def test_private_is_not_consumed():
    print("\n### پیوی وارد مسیر سریع گروهی نمی‌شود")
    bot = _bot()
    ev = Event("ثبت گروه", _owner())
    ev.is_private = True
    handled = asyncio.run(handler.handle_fast_owner_command(bot, ev, "ثبت گروه"))
    check("private not handled", handled is False)
    check("no private reply", ev.replies == [], f"-> {ev.replies}")


def test_regular_messages_skip_fast_path():
    print("\n### پیام‌های عادی وارد مسیر سریع نمی‌شوند")
    ordinary = (
        "ربات",
        "سلام ربات",
        "سخنگو",
        "بازی‌ها",
        "بازی ها",
        "سلام",
        "خوبی",
        "راهنما",
        "قفل",
        "بن",
        "سلام خوبی",
    )
    for text in ordinary:
        check(
            f"{text!r} is not a fast owner command",
            handler.is_fast_owner_command(text) is False,
        )
    bot = _bot()
    stranger = User(999002, name="کاربر", username="user")
    for text in ("ربات", "سلام ربات", "سخنگو", "بازی‌ها", "سلام"):
        ev = Event(text, stranger)
        handled = asyncio.run(handler.handle_fast_owner_command(bot, ev, text))
        check(f"{text!r} not consumed by fast handler", handled is False)
        check(f"{text!r} no fast reply", ev.replies == [], f"-> {ev.replies}")
        check(
            f"{text!r} no COMMAND RECEIVED",
            not any(item.startswith("COMMAND RECEIVED") for item in bot.logger.infos),
        )
        bot.logger.infos.clear()


def test_fast_path_failure_falls_back():
    print("\n### شکست تشخیص fast command مسیر عادی را قطع نمی‌کند")
    reached_normal = []

    async def fake_fast(*_a, **_k):
        raise RuntimeError("fast detection exploded")

    async def run_like_priority(text):
        try:
            if handler.is_fast_owner_command(text):
                if await fake_fast():
                    return "fast"
        except Exception:
            pass
        reached_normal.append(text)
        return "normal"

    for text in ("ربات", "سلام ربات", "سخنگو", "بازی‌ها", "سلام گروه"):
        result = asyncio.run(run_like_priority(text))
        check(f"{text!r} stays on normal path", result == "normal", f"-> {result}")
    result = asyncio.run(run_like_priority("ثبت گروه"))
    check("ثبت گروه still attempts fast then falls back", result == "normal",
          f"-> {result}")
    check("normal handler still reached after failure",
          "ثبت گروه" in reached_normal and "ربات" in reached_normal,
          f"-> {reached_normal}")
    check("empty text is not fast", handler.is_fast_owner_command("") is False)
    check("None is not fast", handler.is_fast_owner_command(None) is False)
    check("prefix فعال سازی گروه is not exact",
          handler.is_fast_owner_command("فعال سازی گروه") is False)
    check("فعال with extra words is not exact",
          handler.is_fast_owner_command("فعال باش") is False)

    original_normalize = handler.normalize_command

    def broken_normalize(_text):
        raise NameError("FAST_OWNER_COMMANDS is not defined")

    handler.normalize_command = broken_normalize
    try:
        check("broken detector does not raise",
              handler.is_fast_owner_command("فعال") is False)
        check("broken detector keeps other messages off fast path",
              handler.is_fast_owner_command("ربات") is False)
    finally:
        handler.normalize_command = original_normalize


def test_handle_new_message_regular_text_not_consumed():
    print("\n### handle_new_message پیام عادی را به مسیر سریع نمی‌دهد")
    bot = _bot()
    ev = Event("ربات", User(777, name="کاربر", username="member"))
    original = handler.handle_fast_owner_command
    called = []

    async def spy(bot_obj, event, text=None):
        called.append(text)
        return await original(bot_obj, event, text)

    handler.handle_fast_owner_command = spy
    try:
        asyncio.run(handler.handle_new_message(bot, ev))
    except Exception:
        pass
    finally:
        handler.handle_fast_owner_command = original
    check("regular message did not crash before routing", True)
    check("fast handler not invoked for ربات", called == [], f"-> {called}")
    check("no owner command log",
          not any(item.startswith("COMMAND RECEIVED") for item in bot.logger.infos))


if __name__ == "__main__":
    test_fast_owner_commands_are_detected()
    test_process_priority_command_has_no_nameerror()
    test_lane_is_admin_not_normal()
    test_register_group_is_direct()
    test_register_owner_is_direct()
    test_non_owner_keeps_same_replies()
    test_handle_new_message_skips_heavy_path()
    test_private_is_not_consumed()
    test_regular_messages_skip_fast_path()
    test_fast_path_failure_falls_back()
    test_handle_new_message_regular_text_not_consumed()
    print(f"\n{PASSED} passed, {FAILED} failed")
    raise SystemExit(1 if FAILED else 0)
