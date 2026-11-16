from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SafeLauncherStopTests(unittest.TestCase):
    def test_stop_waits_for_verified_processes_and_does_not_unlink_live_lock_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / ".foxora2"
            config = runtime / "config"
            config.mkdir(parents=True)
            watchdog_lock = config / "watchdog.lock"
            pid_file = config / "bot.pid"
            env = os.environ.copy()
            env.update({
                "HOME": str(root),
                "BOT_INSTANCE": "foxora2",
                "SOROUSH_BOT_DATA_DIR_FOXORA2": str(runtime),
            })
            delayed_exit = (
                "import signal,time\n"
                "def stop(_sig,_frame): time.sleep(0.7); raise SystemExit(0)\n"
                "signal.signal(signal.SIGTERM,stop)\n"
                "while True: time.sleep(0.1)\n"
            )
            watchdog_proc = subprocess.Popen(
                [sys.executable, "-c", delayed_exit], env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            bot_proc = subprocess.Popen(
                [sys.executable, "-c", delayed_exit], env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            watchdog_lock.write_text(json.dumps({"pid": watchdog_proc.pid}), encoding="utf-8")
            pid_file.write_text(f"{bot_proc.pid}\n", encoding="utf-8")
            started = time.monotonic()
            try:
                result = subprocess.run(
                    [str(ROOT / "foxora2"), "stop"],
                    cwd=ROOT, env=env, text=True, capture_output=True,
                    timeout=20,
                )
                elapsed = time.monotonic() - started
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertGreaterEqual(elapsed, 1.2)
                self.assertIsNotNone(watchdog_proc.poll())
                self.assertIsNotNone(bot_proc.poll())
                # Launcher must leave persistent lock inodes intact. Their
                # owners clear records on clean shutdown; stale records are
                # harmless because flock, not file existence, owns the lock.
                self.assertTrue(watchdog_lock.exists())
                self.assertTrue(pid_file.exists())
                self.assertIn("watchdog", result.stdout)
                self.assertIn("Foxora2 (pid=", result.stdout)
            finally:
                for process in (watchdog_proc, bot_proc):
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=5)

    def test_watchdog_flock_record_is_not_unlinked_on_release(self):
        import watchdog

        with tempfile.TemporaryDirectory() as directory:
            lock_file = Path(directory) / "watchdog.lock"
            instance = watchdog.SingleInstance(lock_file)
            instance.acquire()
            instance.close()
            self.assertTrue(lock_file.exists())
            self.assertEqual(watchdog.SingleInstance.status(lock_file)["state"], "stale")


if __name__ == "__main__":
    unittest.main(verbosity=2)
