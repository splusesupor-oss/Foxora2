"""Canonical owner source and stale runtime migration tests.

    python3 tests/test_owner_source.py
"""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import owner_check
from modules.performance_monitor import SlowProcessMonitor


class Logger:
    def __init__(self):
        self.infos = []
        self.errors = []

    def log_info(self, message):
        self.infos.append(message)

    def log_error(self, message):
        self.errors.append(message)


class PeerUser:
    def __init__(self, user_id):
        self.user_id = user_id


class OwnerClient:
    def __init__(self, owner_id):
        self.owner_id = owner_id
        self.get_me_calls = []
        self.sent = []

    async def get_me(self, input_peer=False):
        self.get_me_calls.append(input_peer)
        return PeerUser(self.owner_id)

    async def send_message(self, target, text):
        self.sent.append((target, text))
        return {"id": len(self.sent)}


class OwnerSourceTests(unittest.TestCase):
    def test_canonical_deployment_owner_replaces_stale_runtime_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            deployment = root / "deployment-owner.json"
            runtime = root / "runtime-owner.json"
            canonical = {"user_id": 42424242, "username": "current_owner"}
            stale = {"user_id": 31313131, "username": "former_owner"}
            deployment.write_text(
                json.dumps(canonical, ensure_ascii=False), encoding="utf-8"
            )
            runtime.write_text(
                json.dumps(stale, ensure_ascii=False), encoding="utf-8"
            )

            owner = owner_check._get_owner_from_files(deployment, runtime)
            migrated = json.loads(runtime.read_text(encoding="utf-8"))

        self.assertEqual(owner, canonical)
        self.assertEqual(migrated, canonical)

    def test_stale_runtime_owner_is_never_fallback_authority(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            deployment = root / "missing-owner.json"
            runtime = root / "runtime-owner.json"
            runtime.write_text(
                json.dumps({
                    "user_id": 51515151,
                    "username": "stale_runtime_only",
                }),
                encoding="utf-8",
            )
            owner = owner_check._get_owner_from_files(deployment, runtime)
        self.assertEqual(owner, {"user_id": None, "username": None})

    def test_repository_owner_file_is_the_get_owner_source(self):
        expected = json.loads(
            (ROOT / "config" / "owner.json").read_text(encoding="utf-8")
        )
        actual = owner_check.get_owner()
        self.assertEqual(actual["user_id"], int(expected["user_id"]))
        self.assertEqual(
            actual["username"],
            owner_check.normalize_username(expected.get("username")),
        )

    def test_slow_process_report_targets_current_get_owner_user(self):
        async def scenario(state_file):
            current = owner_check.get_owner()
            owner_id = current["user_id"]
            self.assertIsNotNone(owner_id)
            client = OwnerClient(owner_id)
            logger = Logger()
            monitor = SlowProcessMonitor(
                client,
                logger,
                cooldown_seconds=0,
                global_min_interval_seconds=0,
                owner_notify_threshold_ms=150,
                state_path=state_file,
            )
            monitor.start()
            self.assertTrue(monitor.record(
                total_ms=1001,
                chat_id=700,
                message_id=701,
                handler="canonical_owner_test",
            ))
            await monitor.queue.join()
            await monitor.close()
            return current, client, logger

        with tempfile.TemporaryDirectory() as directory:
            current, client, logger = asyncio.run(scenario(
                Path(directory) / "performance-state.json"
            ))

        self.assertEqual(client.get_me_calls, [True])
        self.assertEqual(len(client.sent), 1)
        target, report = client.sent[0]
        self.assertEqual(target.user_id, current["user_id"])
        self.assertIn("گزارش کندی شدید", report)
        self.assertFalse(logger.errors)


if __name__ == "__main__":
    unittest.main(verbosity=2)
