"""Focused regressions for rapid text floods and outgoing priority pressure."""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import patch

from modules import big_spam, message_tracker
from modules.admin_actions import AdminActions
from modules.light_spam_ingest import ingest_event, incident_key
from modules.outgoing_sender import (
    OutgoingSender,
    _CHAT_GATES,
    _wrap_call_with_gate,
)


def _rows(*texts):
    stamp = time.time()
    return [
        {"message_id": index, "text": text, "timestamp": stamp}
        for index, text in enumerate(texts, 1)
    ]


def test_promotional_gibberish_and_varied_floods_are_early_detected():
    bio = "بیو چک 🥹🫦"
    hit, reason, ids = big_spam.detect_big_spam(bio, _rows(bio, bio))
    assert hit and reason == "repeated_promotional_messages" and ids == {1, 2}

    gibberish = ("خمخخخمخخخخخخ", "خمخخخمخخخخخخ", "خخخخخ")
    hit, reason, ids = big_spam.detect_big_spam(gibberish[-1], _rows(*gibberish))
    assert hit and reason == "repeated_gibberish_messages" and ids == {1, 2, 3}

    varied = tuple(f"متن چرخشی {index % 4}" for index in range(10))
    hit, reason, ids = big_spam.detect_big_spam(varied[-1], _rows(*varied))
    assert hit and reason == "rapid_message_flood" and ids == set(range(1, 11))

    unique = tuple(f"پاسخ متفاوت شماره {index}" for index in range(10))
    assert not big_spam.detect_big_spam(unique[-1], _rows(*unique))[0]


def test_normal_message_and_three_ordinary_copies_are_not_auto_banned():
    text = "فیلم دیشب عالی بود"
    assert not big_spam.detect_big_spam(text, _rows(text))[0]
    assert not big_spam.detect_big_spam(text, _rows(text, text, text))[0]
    assert big_spam.detect_big_spam(text, _rows(text, text, text, text))[0]


def test_detector_returns_only_wave_ids_not_unrelated_recent_messages():
    packed = " ".join(["بیوچک"] * 6)
    hit, reason, ids = big_spam.detect_big_spam(
        packed, _rows("سلام", "حالت خوبه", packed)
    )
    assert hit and reason == "repeated_promotional_phrase" and ids == {3}

    repeated = "متن تکراری موج"
    hit, reason, ids = big_spam.detect_big_spam(
        repeated,
        _rows(repeated, "گفتگوی عادی", repeated, "پاسخ متفاوت", repeated, repeated),
    )
    assert hit and reason == "repeated_identical_messages"
    assert ids == {1, 3, 5, 6}

    gibberish = "خخخخخخخ"
    hit, reason, ids = big_spam.detect_big_spam(
        gibberish, _rows(gibberish, "سلام دوست من", gibberish, gibberish)
    )
    assert hit and reason == "repeated_gibberish_messages"
    assert ids == {1, 3, 4}


def test_active_game_answers_bypass_generic_flood_but_not_explicit_promotions():
    answer = "گزینه سه"
    rows = _rows(answer, answer, answer, answer)
    assert big_spam.detect_big_spam(answer, rows)[0]
    assert not big_spam.detect_big_spam(answer, rows, allow_generic=False)[0]

    promo = "بیو چک 🥹🫦"
    hit, reason, _ids = big_spam.detect_big_spam(
        promo, _rows(promo, promo), allow_generic=False,
    )
    assert hit and reason == "repeated_promotional_messages"


def test_light_ingest_honors_active_game_guard_for_generic_answers():
    message_tracker.reset_all()
    started = []
    bot = SimpleNamespace(
        _big_spam_incidents={},
        bot_account_id=999,
        native_group_admin_cache={},
        _light_admin_bypass=lambda _chat, _user: False,
        _light_game_answer_active=lambda _chat, _user: True,
        _queue_big_spam_ban=lambda *_args: started.append(_args),
    )

    def event(mid, text):
        return SimpleNamespace(
            chat_id=-44,
            sender_id=8,
            sender=None,
            is_private=False,
            message=SimpleNamespace(id=mid, message=text, caption=None),
        )

    results = [ingest_event(bot, event(mid, "گزینه سه")) for mid in range(1, 5)]
    assert not any(result.detected for result in results)
    assert started == []

    promo = "بیو چک 🥹🫦"
    ingest_event(bot, event(5, promo))
    result = ingest_event(bot, event(6, promo))
    assert result.detected and result.skip_heavy
    assert len(started) == 1
    assert started[0][4] == {5, 6}
    message_tracker.reset_all()


def test_light_ingest_reads_from_id_and_canonicalizes_full_channel_id():
    message_tracker.reset_all()
    full_chat = -1000021055171
    short_chat = 21055171
    user_id = 69443195
    started = []
    bot = SimpleNamespace(
        _big_spam_incidents={},
        punished_users=set(),
        bot_account_id=999,
        native_group_admin_cache={},
        _light_admin_bypass=lambda _chat, _user: False,
    )

    def start(_event, chat_id, member_id, _sender, ids, reason):
        key = incident_key(chat_id, member_id)
        incident = bot._big_spam_incidents.setdefault(key, {"ids": set()})
        incident["ids"].update(ids)
        started.append((key, reason))
        return True

    bot._queue_big_spam_ban = start

    def event(mid):
        return SimpleNamespace(
            chat_id=full_chat,
            sender_id=None,
            sender=None,
            is_private=False,
            message=SimpleNamespace(
                id=mid,
                message="بیو چک 🥹🫦",
                caption=None,
                sender_id=None,
                from_id=SimpleNamespace(user_id=user_id),
            ),
        )

    first = ingest_event(bot, event(1))
    second = ingest_event(bot, event(2))
    assert not first.detected
    assert second.detected and second.skip_heavy
    assert started == [((str(short_chat), str(user_id)), "repeated_promotional_messages")]
    assert bot._big_spam_incidents[(str(short_chat), str(user_id))]["ids"] == {1, 2}
    message_tracker.reset_all()


def test_active_spam_lock_deletes_on_light_path_without_heavy_handler():
    message_tracker.reset_all()
    calls = []

    class Queue:
        def enqueue(self, chat_id, ids, *, priority=1):
            calls.append((chat_id, list(ids), priority))
            return True

    bot = SimpleNamespace(
        _big_spam_incidents={},
        bot_account_id=999,
        native_group_admin_cache={},
        _light_admin_bypass=lambda _chat, _user: False,
        is_spam_locked=lambda key: key == ("21055171", "77"),
        message_delete_queue=Queue(),
    )
    event = SimpleNamespace(
        chat_id=-1000021055171,
        sender_id=77,
        sender=None,
        is_private=False,
        message=SimpleNamespace(id=500, message="پیام بعدی موج", caption=None),
    )
    result = ingest_event(bot, event)
    assert result.skip_heavy and result.reason == "active_spam_lock"
    assert calls == [(-1000021055171, [500], 1)]
    message_tracker.reset_all()


def test_successfully_deleted_ids_are_removed_without_losing_new_rows():
    message_tracker.reset_all()
    message_tracker.add_message(-1, 7, 10, "spam")
    message_tracker.add_message(-1, 7, 11, "new")
    assert message_tracker.remove_message_ids(-1, 7, {10}) == 1
    assert message_tracker.spam_snapshot(-1, 7) == [11]
    message_tracker.reset_all()


def test_priority_zero_send_bypasses_normal_send_gate():
    class SendMessageRequest:
        def __init__(self, peer, label):
            self.peer = SimpleNamespace(channel_id=peer)
            self.label = label

    class Client:
        def __init__(self):
            self._sender = object()
            self.normal_started = asyncio.Event()
            self.release_normal = asyncio.Event()
            self.urgent_started = asyncio.Event()

        async def _call(self, sender, request, ordered=False, flood_sleep_threshold=None):
            if request.label == "normal":
                self.normal_started.set()
                await self.release_normal.wait()
            else:
                self.urgent_started.set()
            return request.label

        async def send_message(self, entity, text):
            return await self._call(self._sender, SendMessageRequest(entity, text))

    class Logger:
        def log_info(self, _message):
            pass

        def log_error(self, message):
            raise AssertionError(message)

    async def scenario():
        _CHAT_GATES.clear()
        client = Client()
        logger = Logger()
        assert _wrap_call_with_gate(client, logger)
        sender = OutgoingSender(client, logger)
        sender.enqueue_send(42, "normal", priority=1)
        await client.normal_started.wait()
        sender.enqueue_send(42, "urgent", priority=0)
        await asyncio.wait_for(client.urgent_started.wait(), timeout=0.2)
        client.release_normal.set()
        await asyncio.wait_for(
            asyncio.gather(*(queue.join() for queue in list(sender._queues.values()))),
            timeout=1,
        )
        await sender.close()

    asyncio.run(scenario())


def test_permanent_ban_is_one_permission_rpc_and_failure_is_not_success():
    class Logger:
        def __init__(self):
            self.errors = []
            self.actions = []

        def log_error(self, message):
            self.errors.append(message)

        def log_action(self, *args):
            self.actions.append(args)

    class Client:
        def __init__(self, fail=False):
            self.fail = fail
            self.edits = 0
            self.kicks = 0

        async def get_entity(self, user_id):
            return SimpleNamespace(id=user_id, username=None, first_name="x", last_name=None)

        async def get_me(self):
            return SimpleNamespace(id=999)

        async def kick_participant(self, *_args, **_kwargs):
            self.kicks += 1
            raise AssertionError("kick_participant must not be used for a permanent ban")

        async def edit_permissions(self, *_args, **_kwargs):
            self.edits += 1
            if self.fail:
                raise RuntimeError("permission failed")
            return True

    async def run(fail):
        client = Client(fail=fail)
        logger = Logger()
        action = AdminActions(client, logger, {})
        with patch("modules.banned_storage.add_banned", lambda *_args, **_kwargs: None):
            result = await action._ban_user_rpc(-10, 7)
        return result, client, logger

    success, client, logger = asyncio.run(run(False))
    assert success is True and client.edits == 1 and client.kicks == 0
    assert logger.actions

    success, client, logger = asyncio.run(run(True))
    assert success is False and client.edits == 1 and client.kicks == 0
    assert logger.errors
