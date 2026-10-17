"""Comprehensive tests for:
1. Banned words whole-word filter (no false positive substrings)
2. Auto-notice deletion timer lifecycle and diagnostic logs

Run with:
    python3 tests/test_banned_words_and_auto_notice_timer.py
"""
import asyncio
import time
import re
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.group_words_storage import (
    find_matching_filter_word,
    normalize_filter_text,
    add_word,
    remove_word,
    get_words,
)
from modules.spam_detector import SpamDetector
from modules.notice_cleanup import NoticeCleanup, capture_sent
from modules.message_delete_queue import MessageDeleteQueue
from modules.admin_actions import AdminActions, public_warning_reason_line
from modules.cache_manager import PermissionCircuitBreaker


class FakeLogger:
    def __init__(self):
        self.logs = []

    def log_info(self, msg):
        self.logs.append(("INFO", msg))

    def log_error(self, msg):
        self.logs.append(("ERROR", msg))

    def log_action(self, *args, **kwargs):
        self.logs.append(("ACTION", repr((args, kwargs))))

    def has_log(self, substring):
        return any(substring in msg for _lvl, msg in self.logs)

    def find_logs(self, prefix):
        return [msg for _lvl, msg in self.logs if prefix in msg]


class FakeClient:
    def __init__(self):
        self.deleted_messages = []
        self.sent_messages = []
        self._msg_counter = 1000

    async def delete_messages(self, chat_id, message_ids):
        self.deleted_messages.append((chat_id, list(message_ids)))
        return len(message_ids)

    async def send_message(self, chat_id, text, **kwargs):
        self._msg_counter += 1
        msg = FakeSentMessage(self._msg_counter, chat_id, text)
        self.sent_messages.append(msg)
        return msg

    async def get_me(self):
        class Me:
            id = 9999
        return Me()

    async def get_entity(self, user_id):
        class User:
            id = user_id
            username = "test_user"
            first_name = "Test"
            last_name = "User"
        return User()

    async def get_input_entity(self, peer):
        return peer

    async def edit_permissions(self, *args, **kwargs):
        return True


class FakeSentMessage:
    def __init__(self, msg_id, chat_id, text):
        self.id = msg_id
        self.chat_id = chat_id
        self.text = text


class FakeConfig:
    def __init__(self, banned_words=None):
        self.banned_words = set(banned_words or [])
        self._banned_words_version = 1
        self._dict = {"send_warning": True, "action_on_threshold": "ban"}

    def get(self, key, default=None):
        return self._dict.get(key, default)

    def reload_if_needed(self):
        pass


def test_banned_words_whole_word_matching():
    print("\n--- TEST 1: Banned Words / Filter Whole-Word Matching ---")
    
    # 1. Test group_words_storage.find_matching_filter_word
    words = ["پی"]

    # Must match:
    assert find_matching_filter_word("پی", words) == "پی", "Failed: 'پی'"
    assert find_matching_filter_word("پی بیا", words) == "پی", "Failed: 'پی بیا'"
    assert find_matching_filter_word("سلام پی", words) == "پی", "Failed: 'سلام پی'"
    assert find_matching_filter_word("پی!", words) == "پی", "Failed: 'پی!'"
    assert find_matching_filter_word("(پی)", words) == "پی", "Failed: '(پی)'"
    assert find_matching_filter_word("پی، خوبی؟", words) == "پی", "Failed: 'پی، خوبی؟'"
    assert find_matching_filter_word("  پی  ", words) == "پی", "Failed: '  پی  '"
    assert find_matching_filter_word("هر کس میخواد بیاد پی سریع", words) == "پی"

    # Must NOT match (substrings of other words):
    assert find_matching_filter_word("پیر شدیم", words) is None, "False positive: 'پیر شدیم'"
    assert find_matching_filter_word("پیشش بودم", words) is None, "False positive: 'پیشش بودم'"
    assert find_matching_filter_word("پیام", words) is None, "False positive: 'پیام'"
    assert find_matching_filter_word("پیام داد", words) is None, "False positive: 'پیام داد'"
    assert find_matching_filter_word("پیرمرد", words) is None, "False positive: 'پیرمرد'"
    assert find_matching_filter_word("پیچ", words) is None, "False positive: 'پیچ'"
    assert find_matching_filter_word("پیش", words) is None, "False positive: 'پیش'"
    assert find_matching_filter_word("ناپیدا", words) is None, "False positive: 'ناپیدا'"

    # 2. Test English & punctuation
    assert find_matching_filter_word("please BUY now", ["buy"]) == "buy"
    assert find_matching_filter_word("buyer paid", ["buy"]) is None
    assert find_matching_filter_word("گفت «پی» و رفت", ["پی"]) == "پی"

    # 3. Test SpamDetector global banned words
    det = SpamDetector(FakeConfig(banned_words=["پی"]))
    is_spam, reason = det.check_banned_words("پی بیا")
    assert is_spam is True and "پی" in reason, f"SpamDetector failed on 'پی بیا': {reason}"

    is_spam, _ = det.check_banned_words("پیر شدیم")
    assert is_spam is False, "SpamDetector false positive on 'پیر شدیم'"

    is_spam, _ = det.check_banned_words("پیام دادند")
    assert is_spam is False, "SpamDetector false positive on 'پیام دادند'"

    print("  PASS: All whole-word filter tests passed successfully without false substring matches!")


def test_auto_notice_lifecycle_and_diagnostic_logs():
    print("\n--- TEST 2: Auto-Notice Deletion Timer & Diagnostic Logs ---")

    async def scenario():
        logger = FakeLogger()
        client = FakeClient()
        cb = PermissionCircuitBreaker(logger=logger)
        delete_queue = MessageDeleteQueue(client, logger, batch_size=10, micro_buffer_seconds=0.01)
        
        # Initialize NoticeCleanup with a short test TTL (0.15s)
        test_path = "/tmp/test_notice_cleanup.json"
        cleanup = NoticeCleanup(
            persist_path=test_path,
            logger=logger,
            ttl_seconds=0.15,
            delete_queue=delete_queue,
            max_retries=3,
        )
        cleanup.client = client
        cleanup.start()

        chat_id = -1001234567890
        
        # 1. Create and send auto notice
        sent_msg = await client.send_message(chat_id, "⚠️ اخطار: پیام شما حذف شد")
        assert sent_msg.id > 0
        
        # 2. Schedule auto notice
        now_t = time.time()
        scheduled = cleanup.schedule(chat_id, sent_msg, ttl=0.15, now=now_t)
        assert scheduled is True

        # Check logs for AUTO_NOTICE_CREATED and AUTO_NOTICE_TIMER_STARTED
        created_logs = logger.find_logs("AUTO_NOTICE_CREATED")
        timer_logs = logger.find_logs("AUTO_NOTICE_TIMER_STARTED")
        
        assert len(created_logs) > 0, "Missing log: AUTO_NOTICE_CREATED"
        assert len(timer_logs) > 0, "Missing log: AUTO_NOTICE_TIMER_STARTED"
        assert f"message_id={sent_msg.id}" in created_logs[0]
        assert f"chat_id={chat_id}" in created_logs[0]
        print("  PASS: AUTO_NOTICE_CREATED and AUTO_NOTICE_TIMER_STARTED logged correctly.")

        # 3. Wait for TTL to expire (0.15s TTL + slight buffer for delete worker)
        await asyncio.sleep(0.3)

        # 4. Check logs for AUTO_NOTICE_DELETE_TRIGGERED and AUTO_NOTICE_DELETE_RESULT
        triggered_logs = logger.find_logs("AUTO_NOTICE_DELETE_TRIGGERED")
        result_logs = logger.find_logs("AUTO_NOTICE_DELETE_RESULT")

        assert len(triggered_logs) > 0, "Missing log: AUTO_NOTICE_DELETE_TRIGGERED"
        assert len(result_logs) > 0, "Missing log: AUTO_NOTICE_DELETE_RESULT"
        assert f"message_ids=[{sent_msg.id}]" in triggered_logs[0]
        assert "deleted=1" in result_logs[0]
        print("  PASS: AUTO_NOTICE_DELETE_TRIGGERED and AUTO_NOTICE_DELETE_RESULT logged correctly.")

        # 5. Verify message was actually deleted in client
        deleted_ids = [mid for _, ids in client.deleted_messages for mid in ids]
        assert sent_msg.id in deleted_ids, f"Message {sent_msg.id} not found in client deleted messages: {deleted_ids}"
        print(f"  PASS: Message {sent_msg.id} successfully deleted from chat {chat_id} via MessageDeleteQueue!")

        cleanup.stop()

    asyncio.run(scenario())


def test_admin_actions_send_warning_notice_integration():
    print("\n--- TEST 3: AdminActions send_warning Auto-Notice Integration ---")

    async def scenario():
        logger = FakeLogger()
        client = FakeClient()
        config = FakeConfig()
        admin = AdminActions(client, logger, config)
        
        test_path = "/tmp/test_notice_cleanup_admin.json"
        cleanup = NoticeCleanup(
            persist_path=test_path,
            logger=logger,
            ttl_seconds=0.1,
            max_retries=3,
        )
        cleanup.client = client
        cleanup.start()
        admin.notice_cleanup = cleanup

        chat_id = -1009876543210
        
        # Call send_warning
        await admin.send_warning(
            chat_id=chat_id,
            username="spammer",
            reason="فیلتر گروه (پی)",
            count=1,
            threshold=3,
        )

        assert len(client.sent_messages) == 1
        sent_warning = client.sent_messages[0]

        await admin.send_warning(
            chat_id=chat_id - 1,
            username="spammer2",
            reason="banned_word کلمه ممنوعه (پی)",
            count=1,
            threshold=3,
            user=type("U", (), {"id": 4242, "first_name": "Ali", "last_name": "", "username": "spammer2"})(),
        )
        banned_notice = client.sent_messages[-1].text
        assert "banned_word" not in banned_notice
        assert "bannde_word" not in banned_notice
        assert "دلیل کلمه ممنوعه : (پی)" in banned_notice

        # Wait for notice cleanup TTL
        await asyncio.sleep(0.25)

        # Verify deletion triggered and executed
        triggered_logs = logger.find_logs("AUTO_NOTICE_DELETE_TRIGGERED")
        result_logs = logger.find_logs("AUTO_NOTICE_DELETE_RESULT")
        
        assert len(triggered_logs) > 0, "Admin warning did not trigger AUTO_NOTICE_DELETE_TRIGGERED"
        assert len(result_logs) > 0, "Admin warning did not trigger AUTO_NOTICE_DELETE_RESULT"
        print("  PASS: AdminActions send_warning auto-notice successfully scheduled, tracked, and deleted!")

        cleanup.stop()

    asyncio.run(scenario())


def test_warning_hides_internal_banned_word_tag():
    print("\n--- TEST 4: Warning notice hides banned_word tag ---")
    line = public_warning_reason_line("banned_word کلمه ممنوعه (پی)")
    assert line == "دلیل کلمه ممنوعه : (پی)", line
    assert "banned_word" not in line
    line = public_warning_reason_line("spam_banned_word banned_word کلمه ممنوعه (کیر)")
    assert line == "دلیل کلمه ممنوعه : (کیر)", line
    assert "banned_word" not in line
    line = public_warning_reason_line("فیلتر گروه (تبلیغ)")
    assert line == "دلیل فیلتر گروه : (تبلیغ)", line
    print("  PASS: Internal banned_word tag is stripped from the public notice.")


def main():
    print("================ RUNNING FIX VALIDATION TESTS ================")
    test_banned_words_whole_word_matching()
    test_auto_notice_lifecycle_and_diagnostic_logs()
    test_admin_actions_send_warning_notice_integration()
    test_warning_hides_internal_banned_word_tag()
    print("\n================ ALL TESTS PASSED SUCCESSFULLY ================")


if __name__ == "__main__":
    main()
