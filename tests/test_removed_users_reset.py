"""No-network regressions for bounded reset of system-created removals."""
import asyncio
import sys
import types as pytypes
from types import SimpleNamespace

from modules.removed_users_reset import reset_system_removed_users


class Logger:
    def __init__(self):
        self.infos = []
        self.errors = []

    def log_info(self, message):
        self.infos.append(message)

    def log_error(self, message):
        self.errors.append(message)


class GetParticipantsRequest:
    def __init__(self, channel, filter, offset, limit, hash):
        self.channel = channel
        self.filter = filter
        self.offset = offset
        self.limit = limit
        self.hash = hash


class ChannelParticipantsKicked:
    def __init__(self, query):
        self.query = query


def _install_fake_splusthon(resolved_chat, input_chat):
    previous = {
        name: sys.modules.get(name)
        for name in ("splusthon", "splusthon.tl")
    }
    root = pytypes.ModuleType("splusthon")
    root.types = SimpleNamespace(
        ChannelParticipantsKicked=ChannelParticipantsKicked
    )
    root.utils = SimpleNamespace(
        get_input_peer=lambda value: input_chat if value is resolved_chat else None
    )
    tl = pytypes.ModuleType("splusthon.tl")
    tl.functions = SimpleNamespace(
        channels=SimpleNamespace(GetParticipantsRequest=GetParticipantsRequest)
    )
    root.tl = tl
    sys.modules["splusthon"] = root
    sys.modules["splusthon.tl"] = tl
    return previous


def _restore_modules(previous):
    for name, module in previous.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


def test_reset_uses_one_kicked_snapshot_and_no_get_entity_calls():
    async def scenario():
        resolved_chat = object()
        input_chat = SimpleNamespace(channel_id=77, access_hash=1)
        previous = _install_fake_splusthon(resolved_chat, input_chat)

        class Participant:
            def __init__(self, user_id):
                self.peer = SimpleNamespace(user_id=user_id)

        kicked_user = SimpleNamespace(id=1, access_hash=9)

        class Client:
            def __init__(self):
                self.list_calls = 0
                self.edits = []
                self.entity_calls = 0

            async def __call__(self, request):
                self.list_calls += 1
                return SimpleNamespace(
                    participants=[Participant(1)], users=[kicked_user]
                )

            async def get_entity(self, _value):
                self.entity_calls += 1
                raise AssertionError("per-record get_entity must not run")

            async def edit_permissions(self, chat, user, until_date=None):
                self.edits.append((chat, user, until_date))
                return True

        entries = [
            {"user_id": "1", "source": "system"},
            {"user_id": "2", "source": "system"},
            {"user_id": "3", "source": "manual"},
        ]
        client = Client()
        logger = Logger()
        try:
            released, remaining = await reset_system_removed_users(
                client, 77, entries, logger, resolved_chat=resolved_chat
            )
        finally:
            _restore_modules(previous)
        return released, remaining, client, logger

    released, remaining, client, logger = asyncio.run(scenario())
    assert released == 1
    assert remaining == [{"user_id": "3", "source": "manual"}]
    assert client.list_calls == 1
    assert client.entity_calls == 0
    assert len(client.edits) == 1
    assert logger.errors == []


def test_snapshot_failure_keeps_every_storage_entry():
    async def scenario():
        resolved_chat = object()
        input_chat = SimpleNamespace(channel_id=88, access_hash=2)
        previous = _install_fake_splusthon(resolved_chat, input_chat)

        class Client:
            async def __call__(self, request):
                raise RuntimeError("NOT_SUPPORTED")

        entries = [
            {"user_id": "10", "source": "system"},
            {"user_id": "11", "source": "manual"},
        ]
        logger = Logger()
        try:
            result = await reset_system_removed_users(
                Client(), 88, entries, logger, resolved_chat=resolved_chat
            )
        finally:
            _restore_modules(previous)
        return result, entries, logger

    (released, remaining), entries, logger = asyncio.run(scenario())
    assert released == 0
    assert remaining == entries
    assert len(logger.errors) == 1
    assert "فهرست اخراجی" in logger.errors[0]
