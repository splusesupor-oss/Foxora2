import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import handlers.message_handler as handler


class _Logger:
    def log_info(self, _message):
        pass

    def log_error(self, message):
        raise AssertionError(message)


def test_big_incident_drains_ids_captured_during_cleanup(monkeypatch):
    bot = SimpleNamespace(logger=_Logger(), _big_spam_incidents={})
    chat_id, user_id = -90001, 73
    incident = handler._big_spam_incident(bot, (chat_id, user_id), {101})
    batches = []

    async def fake_cleanup(_bot, _chat_id, _user_id, ids):
        batches.append(set(ids))
        if len(batches) == 1:
            # Simulate an in-flight incoming message while the first batch is
            # being deleted. It must be picked up before the incident ends.
            handler._capture_big_spam_message(bot, chat_id, user_id, 102)
        return len(ids), []

    sent = []

    async def fake_notice(_bot, _event, _chat_id, _user_id, count, _notice_id):
        sent.append(count)
        return True

    monkeypatch.setattr(handler, "cleanup_spam_messages", fake_cleanup)
    monkeypatch.setattr(handler, "_send_spam_ban_cleanup_notification", fake_notice)

    async def run():
        await handler._drain_big_spam_incident(
            bot, SimpleNamespace(sender=None), chat_id, user_id, incident
        )
        pending = [
            task for task in asyncio.all_tasks()
            if task is not asyncio.current_task()
        ]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    asyncio.run(run())

    assert batches == [{101}, {102}]
    assert sent == [2]
    assert handler._spam_runtime_key(chat_id, user_id) not in bot._big_spam_incidents


def test_big_incident_retries_remaining_ids(monkeypatch):
    bot = SimpleNamespace(logger=_Logger(), _big_spam_incidents={})
    chat_id, user_id = -90002, 74
    incident = handler._big_spam_incident(bot, (chat_id, user_id), {201})
    calls = []

    async def fake_cleanup(_bot, _chat_id, _user_id, ids):
        calls.append(set(ids))
        return (0, list(ids)) if len(calls) == 1 else (len(ids), [])

    async def instant_sleep(_seconds):
        return None

    async def fake_notice(*_args):
        return True

    monkeypatch.setattr(handler, "cleanup_spam_messages", fake_cleanup)
    monkeypatch.setattr(handler, "_send_spam_ban_cleanup_notification", fake_notice)
    monkeypatch.setattr(handler._asyncio, "sleep", instant_sleep)

    asyncio.run(handler._drain_big_spam_incident(
        bot, SimpleNamespace(sender=None), chat_id, user_id, incident
    ))

    assert calls == [{201}, {201}]
    assert handler._spam_runtime_key(chat_id, user_id) not in bot._big_spam_incidents


def test_big_incident_uses_resolved_event_peer_for_delete_queue(monkeypatch):
    peer = object()
    calls = []

    class Queue:
        async def enqueue(self, chat_id, ids, *, priority=1, rpc_peer=None):
            calls.append((chat_id, list(ids), rpc_peer))
            return len(ids), []

    bot = SimpleNamespace(
        logger=_Logger(), _big_spam_incidents={}, message_delete_queue=Queue(),
    )
    chat_id, user_id = -90004, 76
    incident = handler._big_spam_incident(bot, (chat_id, user_id), {401})
    incident["rpc_peer"] = peer

    async def fake_notice(*_args):
        return True

    monkeypatch.setattr(handler, "_send_spam_ban_cleanup_notification", fake_notice)
    asyncio.run(handler._drain_big_spam_incident(
        bot, SimpleNamespace(sender=None), chat_id, user_id, incident
    ))

    assert calls == [(chat_id, [401], peer)]
    assert handler._spam_runtime_key(chat_id, user_id) not in bot._big_spam_incidents


def test_pending_ban_deadline_extends_while_moderation_job_is_active(monkeypatch):
    class Logger:
        def __init__(self):
            self.errors = []

        def log_info(self, _message):
            pass

        def log_error(self, message):
            self.errors.append(message)

    logger = Logger()
    chat_id, user_id = -90005, 77
    key = handler._spam_runtime_key(chat_id, user_id)
    moderation = SimpleNamespace(_pending_keys={(key[0], user_id, "ban")})
    bot = SimpleNamespace(
        logger=logger,
        _big_spam_incidents={},
        moderation_queue=moderation,
        punished_users={handler._punishment_key(chat_id, user_id)},
        clear_spam_lock=lambda _key: None,
    )
    incident = handler._big_spam_incident(bot, key)

    async def run():
        now = asyncio.get_running_loop().time()
        incident["ban_state"] = "pending"
        incident["ban_deadline"] = now - 1
        incident["ban_absolute_deadline"] = now + 100

        async def progress(seconds):
            if seconds == 0.05:
                moderation._pending_keys.clear()
                incident["ban_state"] = "confirmed"

        monkeypatch.setattr(handler._asyncio, "sleep", progress)
        await handler._drain_big_spam_incident(
            bot, SimpleNamespace(sender=None), chat_id, user_id, incident
        )

    async def fake_notice(*_args):
        return True

    monkeypatch.setattr(handler, "_send_spam_ban_cleanup_notification", fake_notice)
    asyncio.run(run())

    assert not any("BAN CALLBACK TIMEOUT" in row for row in logger.errors)
    assert key not in bot._big_spam_incidents


def test_big_incident_bounds_permanently_unresolved_delete_retries(monkeypatch):
    class Logger:
        def __init__(self):
            self.errors = []

        def log_info(self, _message):
            pass

        def log_error(self, message):
            self.errors.append(message)

    logger = Logger()
    bot = SimpleNamespace(logger=logger, _big_spam_incidents={})
    chat_id, user_id = -90003, 75
    incident = handler._big_spam_incident(bot, (chat_id, user_id), {301, 302})
    calls = []

    async def always_unresolved(_bot, _chat_id, _user_id, ids):
        calls.append(set(ids))
        return 0, list(ids)

    async def instant_sleep(_seconds):
        return None

    async def fake_notice(*_args):
        return True

    monkeypatch.setattr(handler, "cleanup_spam_messages", always_unresolved)
    monkeypatch.setattr(handler, "_send_spam_ban_cleanup_notification", fake_notice)
    monkeypatch.setattr(handler._asyncio, "sleep", instant_sleep)

    asyncio.run(handler._drain_big_spam_incident(
        bot, SimpleNamespace(sender=None), chat_id, user_id, incident
    ))

    # Initial queue attempt plus three bounded incident retry rounds.
    assert calls == [{301, 302}] * 4
    assert any("BIG SPAM DELETE UNRESOLVED" in row for row in logger.errors)
    assert handler._spam_runtime_key(chat_id, user_id) not in bot._big_spam_incidents
