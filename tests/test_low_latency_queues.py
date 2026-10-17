"""No-network regressions for bounded low-latency group queues."""
import asyncio
import time
from types import SimpleNamespace

from modules.admin_actions import AdminActions
from modules.moderation_queue import ModerationQueue
from modules.outgoing_sender import OutgoingSender


class Logger:
    def __init__(self):
        self.infos = []
        self.errors = []
        self.actions = []

    def log_info(self, message):
        self.infos.append(message)

    def log_error(self, message):
        self.errors.append(message)

    def log_action(self, *args):
        self.actions.append(args)


async def _wait_until(predicate, timeout=0.5):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("condition did not become true")
        await asyncio.sleep(0.001)


def test_three_independent_users_start_without_per_chat_serial_wait():
    async def scenario():
        logger = Logger()
        queue = ModerationQueue(logger, per_chat_limit=3)
        release = asyncio.Event()
        started = []
        active = 0
        max_active = 0

        def operation(user_id):
            async def run():
                nonlocal active, max_active
                started.append((user_id, time.perf_counter()))
                active += 1
                max_active = max(max_active, active)
                try:
                    await release.wait()
                    return True
                finally:
                    active -= 1
            return run

        before = time.perf_counter()
        for user_id in (1, 2, 3):
            assert queue.enqueue(
                -1000021055171, "mute", operation(user_id),
                user_id=user_id,
            )
        await _wait_until(lambda: len(started) == 3, timeout=0.15)
        start_delays = [(stamp - before) * 1000 for _uid, stamp in started]
        release.set()
        await _wait_until(lambda: queue._completed == 3)
        await queue.close()
        return start_delays, max_active, logger

    delays, max_active, logger = asyncio.run(scenario())
    assert max(delays) < 150, delays
    assert max_active == 3
    assert not logger.errors


def test_same_user_actions_remain_serialized():
    async def scenario():
        queue = ModerationQueue(Logger(), per_chat_limit=3)
        first_started = asyncio.Event()
        first_release = asyncio.Event()
        second_started = asyncio.Event()
        order = []

        async def first():
            order.append("first_start")
            first_started.set()
            await first_release.wait()
            order.append("first_end")
            return True

        async def second():
            order.append("second_start")
            second_started.set()
            return True

        queue.enqueue(21055171, "mute", first, user_id=99)
        queue.enqueue(-1000021055171, "unmute", second, user_id=99)
        await asyncio.wait_for(first_started.wait(), timeout=0.1)
        await asyncio.sleep(0.03)
        assert not second_started.is_set()
        first_release.set()
        await asyncio.wait_for(second_started.wait(), timeout=0.1)
        await _wait_until(lambda: queue._completed == 2)
        await queue.close()
        return order

    order = asyncio.run(scenario())
    assert order == ["first_start", "first_end", "second_start"]


def test_moderation_per_chat_cap_is_strict():
    async def scenario():
        queue = ModerationQueue(Logger(), per_chat_limit=3)
        release = asyncio.Event()
        started = []
        active = 0
        max_active = 0

        def operation(user_id):
            async def run():
                nonlocal active, max_active
                started.append(user_id)
                active += 1
                max_active = max(max_active, active)
                try:
                    await release.wait()
                    return True
                finally:
                    active -= 1
            return run

        for user_id in range(4):
            queue.enqueue(77, "mute", operation(user_id), user_id=user_id)
        await _wait_until(lambda: len(started) == 3)
        await asyncio.sleep(0.03)
        assert len(started) == 3
        release.set()
        await _wait_until(lambda: queue._completed == 4)
        await queue.close()
        return max_active, started

    max_active, started = asyncio.run(scenario())
    assert max_active == 3
    assert sorted(started) == [0, 1, 2, 3]



def test_manual_mute_jumps_a_queued_automatic_ban():
    async def scenario():
        queue = ModerationQueue(Logger(), per_chat_limit=1)
        first_started, release = asyncio.Event(), asyncio.Event()
        order = []

        async def first_ban():
            order.append("first_ban")
            first_started.set()
            await release.wait()
            return True

        async def automatic_ban():
            order.append("automatic_ban")
            return True

        async def manual_mute():
            order.append("manual_mute")
            return True

        queue.enqueue(1, "ban", first_ban, user_id=1)
        await asyncio.wait_for(first_started.wait(), timeout=0.1)
        queue.enqueue(1, "ban", automatic_ban, user_id=2)
        queue.enqueue(1, "mute", manual_mute, user_id=3)
        release.set()
        await _wait_until(lambda: queue._completed == 3)
        await queue.close()
        return order

    assert asyncio.run(scenario()) == ["first_ban", "manual_mute", "automatic_ban"]

def test_two_normal_sends_use_existing_slots_and_notices_stay_independent():
    async def scenario():
        logger = Logger()
        sender = OutgoingSender(
            client=None, logger=logger, normal_concurrency=2,
        )
        release = asyncio.Event()
        normal_started = []
        notice_started = asyncio.Event()

        def normal(index):
            async def run():
                normal_started.append(index)
                await release.wait()
                return index
            return run

        async def notice():
            notice_started.set()
            return "notice"

        before = time.perf_counter()
        sender.enqueue(55, normal(1), priority=1)
        sender.enqueue(55, normal(2), priority=1)
        sender.enqueue(55, normal(3), priority=1)
        sender.enqueue(55, notice, priority=0)
        await _wait_until(
            lambda: len(normal_started) == 2 and notice_started.is_set(),
            timeout=0.15,
        )
        initial_delay = (time.perf_counter() - before) * 1000
        assert normal_started == [1, 2]
        release.set()
        await _wait_until(lambda: sender.stats["sent"] == 4)
        await sender.close()
        return initial_delay, normal_started, logger

    delay, order, logger = asyncio.run(scenario())
    assert delay < 150, delay
    assert order == [1, 2, 3]
    assert not logger.errors


def test_ban_reuses_known_identity_and_peers_without_get_me_rpc():
    async def scenario():
        logger = Logger()
        user = SimpleNamespace(
            id=77, access_hash=700, username="fast",
            first_name="Fast", last_name="User",
        )
        user_peer = SimpleNamespace(user_id=77, access_hash=700)
        chat_peer = SimpleNamespace(channel_id=21055171, access_hash=900)

        class Client:
            def __init__(self):
                self.input_calls = []
                self.edits = []
                self.get_me_calls = 0

            async def get_me(self):
                self.get_me_calls += 1
                raise AssertionError("known bot identity must skip get_me")

            async def get_input_entity(self, value):
                self.input_calls.append(value)
                if value is user:
                    return user_peer
                if value is chat_peer:
                    return chat_peer
                raise AssertionError(f"unexpected entity resolution: {value!r}")

            async def edit_permissions(self, chat, target, **rights):
                self.edits.append((chat, target, rights))
                return True

        client = Client()
        actions = AdminActions(
            client, logger, SimpleNamespace(get=lambda *_a, **_k: None),
            peer_cache={-1000021055171: chat_peer},
            bot_account_id=999,
        )

        from modules import banned_storage, punishment_mode
        original_add = banned_storage.add_banned
        original_mode = punishment_mode.is_mute
        banned_storage.add_banned = lambda *_a, **_k: True
        punishment_mode.is_mute = lambda _chat_id: False
        try:
            result = await actions.ban_user(
                21055171, 77, reason="test", user=user,
            )
        finally:
            banned_storage.add_banned = original_add
            punishment_mode.is_mute = original_mode
        return result, client, logger

    result, client, logger = asyncio.run(scenario())
    assert result is True
    assert client.get_me_calls == 0
    assert len(client.input_calls) == 2
    assert getattr(client.input_calls[0], "id", None) == 77
    assert getattr(client.input_calls[1], "channel_id", None) == 21055171
    assert len(client.edits) == 1
    assert client.edits[0][0] is client.input_calls[1]
    assert getattr(client.edits[0][1], "user_id", None) == 77
    assert not logger.errors
