"""No-network regressions for scheduled cleanup peer/snapshot handling."""
import asyncio
from types import SimpleNamespace

from modules import admin_tools


class _Peer:
    def __init__(self, channel_id):
        self.channel_id = channel_id


class _Actions:
    def __init__(self):
        self.lock_calls = 0
        self.unlock_calls = 0

    @staticmethod
    def _peer_id_candidates(chat_id):
        return (-1000009429374, chat_id)

    async def lock_group(self, _chat_id):
        self.lock_calls += 1

    async def unlock_group(self, _chat_id):
        self.unlock_calls += 1


def test_snapshot_prefers_normalized_cached_input_peer():
    async def scenario():
        peer = _Peer(9429374)

        class Client:
            def __init__(self):
                self.targets = []

            async def iter_messages(self, target, limit):
                self.targets.append(target)
                if target is not peer:
                    raise AssertionError("short numeric id must not run first")
                for message_id in (9, 8, 8, 7):
                    yield SimpleNamespace(id=message_id)

        client = Client()
        bot = SimpleNamespace(
            client=client,
            reply_input_peer_cache={-1000009429374: peer},
            group_actions=_Actions(),
        )
        logs = []
        ids = await admin_tools._snapshot_cleanup_message_ids(
            bot, 9429374, 700, logs.append
        )
        return ids, client.targets, logs

    ids, targets, logs = asyncio.run(scenario())
    assert ids == [9, 8, 7]
    assert len(targets) == 1 and isinstance(targets[0], _Peer)
    assert any("target_type=_Peer" in line for line in logs)


def test_snapshot_failure_is_not_misreported_as_empty_success():
    async def scenario():
        class Client:
            async def iter_messages(self, _target, limit):
                if False:
                    yield None
                raise LookupError("cold entity cache")

        bot = SimpleNamespace(
            client=Client(), reply_input_peer_cache={}, group_actions=_Actions()
        )
        logs = []
        ids = await admin_tools._snapshot_cleanup_message_ids(
            bot, 9429374, 700, logs.append
        )
        return ids, logs

    ids, logs = asyncio.run(scenario())
    assert ids is None
    assert any("SNAPSHOT FAILED" in line for line in logs)


def test_execute_cleanup_aborts_before_lock_when_snapshot_failed():
    async def scenario():
        class Client:
            async def iter_messages(self, _target, limit):
                if False:
                    yield None
                raise LookupError("not resolved")

        actions = _Actions()
        bot = SimpleNamespace(
            client=Client(),
            reply_input_peer_cache={},
            group_actions=actions,
            message_delete_queue=None,
        )

        class Logger:
            def __init__(self):
                self.lines = []

            def log_info(self, line):
                self.lines.append(line)

        logger = Logger()
        result = await admin_tools.execute_cleanup(
            bot, 9429374001, 700, logger=logger
        )
        return result, actions, logger.lines

    result, actions, lines = asyncio.run(scenario())
    assert result is False
    assert actions.lock_calls == 0
    assert actions.unlock_calls == 0
    assert any("ABORT" in line and "snapshot_failed" in line for line in lines)
