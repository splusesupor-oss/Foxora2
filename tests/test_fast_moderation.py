"""Fast moderation command must enqueue before reply/native-admin RPC work.

    python3 tests/test_fast_moderation.py
"""
import asyncio
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from handlers import message_handler as mh


class Logger:
    def __init__(self):
        self.infos = []
        self.errors = []

    def log_info(self, message):
        self.infos.append(message)

    def log_error(self, message):
        self.errors.append(message)


class User:
    def __init__(self, user_id, username=None):
        self.id = user_id
        self.username = username
        self.first_name = username or str(user_id)
        self.last_name = None


class ReplyMessage:
    def __init__(self, user):
        self.user = user

    async def get_sender(self):
        await asyncio.sleep(0.05)
        return self.user


class Client:
    def __init__(self, target):
        self.target = target
        self.get_messages_calls = 0

    async def get_messages(self, chat_id, ids=None):
        self.get_messages_calls += 1
        await asyncio.sleep(0.05)
        return ReplyMessage(self.target)


class AdminActions:
    def __init__(self):
        self.calls = []

    async def mute_user(self, chat_id, user_id, **kwargs):
        self.calls.append((chat_id, user_id, kwargs))
        await asyncio.sleep(0.05)
        return True


class CapturingModerationQueue:
    def __init__(self):
        self.job = None

    def enqueue(self, chat_id, action, operation, **kwargs):
        self.job = {
            "chat_id": chat_id,
            "action": action,
            "operation": operation,
            **kwargs,
        }
        return True


class Event:
    def __init__(self, sender):
        self.chat_id = -100123
        self.is_private = False
        self.sender = sender
        self.sender_id = sender.id
        self.message = type("Message", (), {"id": 900})()
        self.reply_to = type("Reply", (), {"reply_to_msg_id": 321})()
        self.replies = []

    async def get_sender(self):
        return self.sender

    async def reply(self, text, **kwargs):
        self.replies.append(text)
        return type("Sent", (), {"id": len(self.replies)})()


class Bot:
    def __init__(self, sender, target):
        self.logger = Logger()
        self.client = Client(target)
        self.admin_actions = AdminActions()
        self.moderation_queue = CapturingModerationQueue()
        self.reply_input_peer_cache = {}


class FastModerationTests(unittest.TestCase):
    def test_mute_enqueues_without_waiting_for_target_or_native_rpc(self):
        async def scenario():
            sender = User(101, "admin")
            target = User(202, "target")
            event = Event(sender)
            bot = Bot(sender, target)

            original_permission = mh._has_group_management_permission
            original_native = mh._is_native_group_admin
            try:
                # Sender is registered; target is not.
                mh._has_group_management_permission = (
                    lambda _bot, _chat, user_id, _username:
                    int(user_id) == sender.id
                )

                async def native_admin(*_args, **_kwargs):
                    await asyncio.sleep(0.05)
                    return False

                mh._is_native_group_admin = native_admin
                started = time.perf_counter()
                handled = await mh.handle_fast_moderation_command(
                    bot, event, "سکوت", sender
                )
                handler_ms = (time.perf_counter() - started) * 1000

                self.assertTrue(handled)
                self.assertLess(handler_ms, 25.0)
                await asyncio.sleep(0)
                self.assertEqual(event.replies, [])
                self.assertEqual(bot.client.get_messages_calls, 0)
                self.assertIsNotNone(bot.moderation_queue.job)
                self.assertEqual(
                    bot.moderation_queue.job["user_id"], "reply:321"
                )

                result = await bot.moderation_queue.job["operation"]()
                self.assertTrue(result["muted"])
                self.assertEqual(result["target_user"].id, target.id)
                self.assertEqual(bot.client.get_messages_calls, 1)
                self.assertEqual(len(bot.admin_actions.calls), 1)
            finally:
                mh._has_group_management_permission = original_permission
                mh._is_native_group_admin = original_native

        asyncio.run(scenario())

    def test_core_priority_lane_uses_fast_moderation_route(self):
        source = (ROOT / "core" / "bot_working_split_ok.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("is_fast_moderation_command(text)", source)
        self.assertIn("await handle_fast_moderation_command(", source)
        self.assertTrue(mh.is_fast_moderation_command("سکوت"))
        self.assertTrue(mh.is_fast_moderation_command("قفل"))
        self.assertTrue(mh.is_fast_moderation_command("باز"))
        self.assertFalse(mh.is_fast_moderation_command("سلام"))

    def test_unregistered_sender_falls_back_to_compatibility_path(self):
        async def scenario():
            sender = User(303, "native_only")
            event = Event(sender)
            bot = Bot(sender, User(404))
            original_permission = mh._has_group_management_permission
            try:
                mh._has_group_management_permission = (
                    lambda *_args, **_kwargs: False
                )
                handled = await mh.handle_fast_moderation_command(
                    bot, event, "سکوت", sender
                )
                self.assertFalse(handled)
                self.assertIsNone(bot.moderation_queue.job)
            finally:
                mh._has_group_management_permission = original_permission

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main(verbosity=2)
