"""Offline tests for the owner-only «ارسال عکس» diagnostic command."""
import asyncio
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from handlers import owner_photo_test as feature
from modules import message_dedup
from modules.manage_features.peers import group_peer

OWNER = 12345


class FakeLogger:
    def __init__(self):
        self.infos = []
        self.errors = []

    def log_info(self, message):
        self.infos.append(message)

    def log_error(self, message):
        self.errors.append(message)


class FakeClient:
    def __init__(self):
        self.photo_calls = []
        self.text_calls = []
        self.error = None
        self.result = SimpleNamespace(id=77, media=SimpleNamespace())
        self.delay = 0

    async def send_file(self, *args, **kwargs):
        self.photo_calls.append((args, kwargs))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.result

    async def send_message(self, *args, **kwargs):
        self.text_calls.append((args, kwargs))
        return SimpleNamespace(id=78)


class FakeEvent:
    def __init__(self, text="ارسال عکس", sender_id=OWNER, chat_id=501,
                 private=True, peer=None, message_id=20):
        self.message = SimpleNamespace(
            message=text, caption=None, id=message_id,
            _input_chat=peer, edit_date=None,
        )
        self.sender_id = sender_id
        self.sender = SimpleNamespace(id=sender_id) if sender_id else None
        self.chat_id = chat_id
        self.is_private = private
        self.is_edit = False
        self.replies = []
        self.reply_error = None
        self.input_chat_calls = 0
        self.get_input_chat_result = None

    async def get_input_chat(self):
        self.input_chat_calls += 1
        return self.get_input_chat_result

    async def reply(self, text):
        self.replies.append(text)
        if self.reply_error:
            raise self.reply_error
        return SimpleNamespace(id=79)


class OwnerPhotoTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        message_dedup.reset()
        owner_patch = patch.object(feature, "is_global_owner",
                                   side_effect=lambda uid: uid == OWNER)
        owner_patch.start()
        self.addCleanup(owner_patch.stop)
        self.addCleanup(message_dedup.reset)
        self.bot = SimpleNamespace(client=FakeClient(), logger=FakeLogger(),
                                   reply_input_peer_cache={})

    def test_command_is_exact_and_asset_is_real_jpeg(self):
        self.assertTrue(feature.is_command("   ارسال   عکس  "))
        self.assertFalse(feature.is_command("دانلود عکس"))
        self.assertFalse(feature.is_command("ارسال عکس الان"))
        self.assertTrue(feature.PHOTO_PATH.is_file())
        self.assertEqual(feature.PHOTO_PATH.read_bytes()[:3], b"\xff\xd8\xff")
        self.assertLess(feature.PHOTO_PATH.stat().st_size, 1_000_000)

    async def test_owner_sends_one_local_jpg_to_resolved_peer(self):
        peer = object()
        event = FakeEvent(peer=peer)
        self.assertTrue(await feature.handle(self.bot, event))
        self.assertEqual(len(self.bot.client.photo_calls), 1)
        (target, filepath), kwargs = self.bot.client.photo_calls[0]
        self.assertIs(target, peer)
        self.assertEqual(Path(filepath), feature.PHOTO_PATH)
        self.assertIs(kwargs["force_document"], False)
        self.assertFalse(event.replies)
        self.assertTrue(any("PHOTO TEST SEND RETURNED" in text
                            for text in self.bot.logger.infos))

    async def test_failed_upload_shows_exact_exception_to_owner(self):
        self.bot.client.error = RuntimeError("FILE_REQUEST_RECEIVED_ON_CONNECTION_SERVER")
        event = FakeEvent(peer=object(), message_id=21)
        self.assertTrue(await feature.handle(self.bot, event))
        self.assertEqual(len(self.bot.client.photo_calls), 1)
        self.assertEqual(len(event.replies), 1)
        self.assertIn("RuntimeError: FILE_REQUEST_RECEIVED_ON_CONNECTION_SERVER",
                      event.replies[0])
        self.assertTrue(any("PHOTO TEST SEND FAILED" in text
                            for text in self.bot.logger.errors))

    async def test_non_owner_sees_no_photo_and_no_internal_error(self):
        event = FakeEvent(sender_id=22222, message_id=22)
        self.assertTrue(await feature.handle(self.bot, event))
        self.assertFalse(self.bot.client.photo_calls)
        self.assertFalse(event.replies)
        self.assertFalse(self.bot.client.text_calls)

    async def test_unrelated_message_is_not_consumed(self):
        self.assertFalse(await feature.handle(self.bot, FakeEvent(text="سلام")))
        self.assertFalse(self.bot.client.photo_calls)

    async def test_edit_does_not_send_again(self):
        event = FakeEvent(message_id=23)
        event.message.edit_date = object()
        self.assertTrue(await feature.handle(self.bot, event))
        self.assertFalse(self.bot.client.photo_calls)

    async def test_replayed_message_sends_only_once(self):
        event = FakeEvent(peer=object(), message_id=24)
        self.assertTrue(await feature.handle(self.bot, event))
        self.assertTrue(await feature.handle(self.bot, event))
        self.assertEqual(len(self.bot.client.photo_calls), 1)

    async def test_positive_group_id_uses_safe_group_peer(self):
        event = FakeEvent(chat_id=7788, private=False, message_id=25)
        self.assertTrue(await feature.handle(self.bot, event))
        target = self.bot.client.photo_calls[0][0][0]
        self.assertEqual(target, group_peer(self.bot, 7788))
        self.assertLess(target, 0)

    async def test_cached_group_peer_is_preferred(self):
        peer = object()
        self.bot.reply_input_peer_cache[7788] = peer
        event = FakeEvent(chat_id=7788, private=False, message_id=26)
        self.assertTrue(await feature.handle(self.bot, event))
        self.assertIs(self.bot.client.photo_calls[0][0][0], peer)
        self.assertEqual(event.input_chat_calls, 0)

    async def test_misclassified_private_chat_still_targets_owner(self):
        peer_user = type("PeerUser", (), {})()
        event = FakeEvent(chat_id=OWNER, private=False, message_id=31)
        event._chat_peer = peer_user
        self.assertTrue(feature.is_private_chat(event))
        self.assertTrue(await feature.handle(self.bot, event))
        self.assertIs(self.bot.client.photo_calls[0][0][0], event.sender)

    def test_private_peer_on_message_is_also_recognized(self):
        event = FakeEvent(private=False)
        event.message.peer_id = type("PeerUser", (), {})()
        self.assertTrue(feature.is_private_chat(event))

    async def test_missing_asset_is_reported_without_upload(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(feature, "PHOTO_PATH",
                              Path(temporary) / "missing.jpg"):
                event = FakeEvent(message_id=27)
                self.assertTrue(await feature.handle(self.bot, event))
        self.assertFalse(self.bot.client.photo_calls)
        self.assertIn("FileNotFoundError", event.replies[0])

    async def test_timeout_is_reported(self):
        self.bot.client.delay = 0.05
        with patch.object(feature, "SEND_TIMEOUT", 0.01):
            event = FakeEvent(peer=object(), message_id=28)
            self.assertTrue(await feature.handle(self.bot, event))
        self.assertIn("TimeoutError", event.replies[0])

    async def test_reply_failure_uses_same_peer_for_text_fallback(self):
        peer = object()
        self.bot.client.error = RuntimeError("UPLOAD_FAILED")
        event = FakeEvent(peer=peer, message_id=29)
        event.reply_error = RuntimeError("REPLY_FAILED")
        self.assertTrue(await feature.handle(self.bot, event))
        self.assertEqual(len(self.bot.client.text_calls), 1)
        (target, text), _kwargs = self.bot.client.text_calls[0]
        self.assertIs(target, peer)
        self.assertIn("RuntimeError: UPLOAD_FAILED", text)

    async def test_none_response_is_uncertain_not_claimed_success(self):
        self.bot.client.result = None
        event = FakeEvent(peer=object(), message_id=30)
        self.assertTrue(await feature.handle(self.bot, event))
        self.assertEqual(len(self.bot.client.photo_calls), 1)
        self.assertIn("تأییدیهٔ ارسال عکس", event.replies[0])

    def test_core_routes_before_private_and_group_handlers_after_gate(self):
        core = (Path(__file__).resolve().parents[1]
                / "core" / "bot_working_split_ok.py").read_text()
        new_msg = core[core.index("async def new_message_handler(event):"):]
        first = new_msg.index("await handle_owner_photo_test(self, event)")
        gate = new_msg.index("_group_subscription_gate(event)")
        second = new_msg.rindex("await handle_owner_photo_test(self, event)")
        self.assertLess(first, gate)  # only for mislabelled private PeerUser
        self.assertLess(gate, second)  # ordinary group/private route
        self.assertLess(new_msg.index("native_admin_warning_lock.drop_if_active"),
                        second)
        self.assertLess(second,
                        new_msg.index("await try_handle_private_start(self, event)"))


if __name__ == "__main__":
    unittest.main()
