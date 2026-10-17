"""Offline tests for silent, aggregated slow-process monitoring."""
import asyncio
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if "splusthon" not in sys.modules:
    splusthon = types.ModuleType("splusthon")
    class PeerUser:
        def __init__(self, user_id): self.user_id = user_id
    class InputPeerUser:
        def __init__(self, user_id, access_hash):
            self.user_id, self.access_hash = user_id, access_hash
    splusthon.types = types.SimpleNamespace(PeerUser=PeerUser, InputPeerUser=InputPeerUser)
    sys.modules["splusthon"] = splusthon

from modules import owner_private
from modules.performance_monitor import SlowProcessMonitor, owner_worthy_event


class Logger:
    def __init__(self): self.infos, self.errors = [], []
    def log_info(self, message): self.infos.append(message)
    def log_error(self, message): self.errors.append(message)


class Peer:
    def __init__(self, user_id): self.user_id = user_id


class Client:
    def __init__(self, owner_id):
        self.owner_id, self.sent, self._sender = owner_id, [], None
    async def get_me(self, input_peer=False): return Peer(self.owner_id)
    async def send_message(self, target, text):
        self.sent.append((target, text))


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.owner_id = 987654321
        self.original = owner_private.get_owner
        owner_private.get_owner = lambda: {"user_id": self.owner_id, "username": None}

    def tearDown(self): owner_private.get_owner = self.original

    def test_routine_event_is_silent_and_only_aggregated(self):
        async def scenario():
            logger, client = Logger(), Client(self.owner_id)
            monitor = SlowProcessMonitor(client, logger, batch_interval_seconds=3600)
            self.assertFalse(monitor.record(total_ms=400, chat_id=1, message_id=2, handler="normal"))
            self.assertEqual(logger.infos, [])
            self.assertEqual(client.sent, [])
            sent = await monitor.flush_batch()
            await monitor.close()
            return client, sent, monitor
        client, sent, monitor = asyncio.run(scenario())
        self.assertFalse(sent)
        self.assertEqual(client.sent, [])
        self.assertEqual(len(monitor._batch), 1)

    def test_severe_event_alerts_once_per_30_seconds(self):
        async def scenario():
            logger, client = Logger(), Client(self.owner_id)
            monitor = SlowProcessMonitor(client, logger, alert_interval_seconds=30, batch_interval_seconds=3600)
            self.assertTrue(monitor.record(total_ms=1001, chat_id=1, message_id=2, handler="slow", now_epoch=100))
            self.assertFalse(monitor.record(total_ms=2000, chat_id=1, message_id=3, handler="slow", now_epoch=120))
            await monitor.queue.join()
            await monitor.close()
            return client, logger
        client, logger = asyncio.run(scenario())
        self.assertEqual(len(client.sent), 1)
        self.assertIn("گزارش کندی شدید", client.sent[0][1])
        self.assertEqual(logger.infos, [])

    def test_batch_aggregates_all_same_paths_without_terminal_log_noise(self):
        async def scenario():
            logger, client = Logger(), Client(self.owner_id)
            monitor = SlowProcessMonitor(client, logger, batch_interval_seconds=3600)
            for ms in (200, 600, 900):
                monitor.record(total_ms=ms, chat_id=5, message_id=ms, handler="pipeline")
            await monitor.flush_batch()
            await monitor.close()
            return client, logger
        client, logger = asyncio.run(scenario())
        self.assertEqual(len(client.sent), 1)
        report = client.sent[0][1]
        self.assertIn("تعداد=3", report)
        self.assertIn("بیشینه=900.0ms", report)
        self.assertEqual(logger.infos, [])

    def test_pressure_defers_alert_and_batch(self):
        async def scenario():
            logger, client = Logger(), Client(self.owner_id)
            client._sender = type("Sender", (), {"_pending_state": list(range(8))})()
            monitor = SlowProcessMonitor(client, logger, rpc_pressure_limit=8, batch_interval_seconds=3600)
            self.assertTrue(monitor.record(total_ms=1200, chat_id=1, message_id=2, handler="blocked"))
            await monitor.queue.join()
            self.assertFalse(await monitor.flush_batch())
            await monitor.close()
            return client, logger
        client, logger = asyncio.run(scenario())
        self.assertEqual(client.sent, [])
        self.assertEqual(logger.infos, [])

    def test_owner_worthy_filter_skips_one_off_and_noisy_short_events(self):
        self.assertFalse(owner_worthy_event({
            "handler": "pipeline", "max_ms": 400, "count": 1,
        }))
        self.assertTrue(owner_worthy_event({
            "handler": "pipeline", "max_ms": 400, "count": 3,
        }))
        self.assertTrue(owner_worthy_event({
            "handler": "process_incoming_message:receive",
            "max_ms": 1001,
            "count": 1,
        }))
        self.assertFalse(owner_worthy_event({
            "handler": "process_incoming_message:receive",
            "max_ms": 400,
            "count": 1,
        }))
        self.assertFalse(owner_worthy_event({
            "handler": "process_incoming_message:spam_check",
            "max_ms": 350,
            "count": 2,
        }))
        self.assertTrue(owner_worthy_event({
            "handler": "process_incoming_message:spam_check",
            "max_ms": 350,
            "count": 3,
        }))

    def test_receive_under_500ms_is_not_reported_until_it_repeats(self):
        async def scenario():
            logger, client = Logger(), Client(self.owner_id)
            monitor = SlowProcessMonitor(client, logger, batch_interval_seconds=3600)
            self.assertFalse(monitor.record(
                total_ms=400,
                chat_id=11,
                message_id=1,
                handler="process_incoming_message:receive",
            ))
            self.assertFalse(await monitor.flush_batch())
            self.assertEqual(client.sent, [])
            for index in range(3):
                self.assertFalse(monitor.record(
                    total_ms=220 + index,
                    chat_id=12,
                    message_id=10 + index,
                    handler="process_incoming_message:spam_check",
                ))
            self.assertTrue(await monitor.flush_batch())
            await monitor.close()
            return client
        client = asyncio.run(scenario())
        self.assertEqual(len(client.sent), 1)
        self.assertIn("گزارش تجمیعی", client.sent[0][1])
        self.assertIn("spam_check", client.sent[0][1])
        self.assertNotIn(":receive", client.sent[0][1])

    def test_owner_cooldown_allows_only_one_report_per_window(self):
        async def scenario():
            logger, client = Logger(), Client(self.owner_id)
            monitor = SlowProcessMonitor(
                client,
                logger,
                alert_interval_seconds=0,
                batch_interval_seconds=3600,
                cooldown_seconds=15 * 60,
            )
            self.assertTrue(monitor.record(
                total_ms=1001,
                chat_id=1,
                message_id=2,
                handler="slow",
                now_epoch=100,
            ))
            self.assertFalse(monitor.record(
                total_ms=2000,
                chat_id=1,
                message_id=3,
                handler="slow",
                now_epoch=160,
            ))
            await monitor.queue.join()
            self.assertFalse(await monitor.flush_batch())
            await monitor.close()
            return client
        client = asyncio.run(scenario())
        self.assertEqual(len(client.sent), 1)
        self.assertIn("گزارش کندی شدید", client.sent[0][1])

    def test_group_traffic_does_not_spam_owner_reports(self):
        async def scenario():
            logger, client = Logger(), Client(self.owner_id)
            monitor = SlowProcessMonitor(
                client,
                logger,
                alert_interval_seconds=0,
                batch_interval_seconds=3600,
                cooldown_seconds=15 * 60,
            )
            queued_severe = 0
            for index in range(250):
                if monitor.record(
                    total_ms=220 + (index % 80),
                    chat_id=9001,
                    message_id=index,
                    handler="process_incoming_message:receive",
                ):
                    queued_severe += 1
                if monitor.record(
                    total_ms=310 + (index % 50),
                    chat_id=9001,
                    message_id=1000 + index,
                    handler="process_incoming_message:spam_check",
                ):
                    queued_severe += 1
                if monitor.record(
                    total_ms=180,
                    chat_id=9001,
                    message_id=2000 + index,
                    handler="process_incoming_message:pipeline",
                ):
                    queued_severe += 1
            for index in range(40):
                if monitor.record(
                    total_ms=1100 + index,
                    chat_id=9001,
                    message_id=3000 + index,
                    handler="process_incoming_message:pipeline",
                ):
                    queued_severe += 1
            await monitor.queue.join()
            first_count = len(client.sent)
            flushed = await monitor.flush_batch()
            await monitor.close()
            return client, queued_severe, first_count, flushed
        client, queued_severe, first_count, flushed = asyncio.run(scenario())
        self.assertLessEqual(queued_severe, 1)
        self.assertLessEqual(first_count, 1)
        self.assertFalse(flushed)
        self.assertLessEqual(len(client.sent), 1)
        self.assertGreater(len(client.sent), 0)

    def test_repeated_short_receive_flush_sends_one_aggregate(self):
        async def scenario():
            logger, client = Logger(), Client(self.owner_id)
            monitor = SlowProcessMonitor(
                client,
                logger,
                batch_interval_seconds=3600,
                cooldown_seconds=15 * 60,
            )
            queued = 0
            for index in range(400):
                if monitor.record(
                    total_ms=200 + (index % 200),
                    chat_id=42,
                    message_id=index,
                    handler="process_incoming_message:receive",
                ):
                    queued += 1
                if monitor.record(
                    total_ms=210 + (index % 100),
                    chat_id=42,
                    message_id=5000 + index,
                    handler="process_incoming_message:spam_check",
                ):
                    queued += 1
            await monitor.queue.join()
            before = len(client.sent)
            flushed = await monitor.flush_batch()
            second = await monitor.flush_batch()
            await monitor.close()
            return client, queued, before, flushed, second
        client, queued, before, flushed, second = asyncio.run(scenario())
        self.assertEqual(queued, 0)
        self.assertEqual(before, 0)
        self.assertTrue(flushed)
        self.assertFalse(second)
        self.assertEqual(len(client.sent), 1)
        self.assertIn("گزارش تجمیعی", client.sent[0][1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
