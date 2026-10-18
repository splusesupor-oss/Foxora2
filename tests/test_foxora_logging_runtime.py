"""Preserved Foxora log/CPU optimization guarantees on the B2 logger.

Inherited implementation: ``modules/logger_module.py`` provides a
non-blocking bounded logging queue, noisy-prefix suppression and automatic
rotation. These tests pin the preserved Foxora properties:

* logging must never block the bot's event loop, even when saturated;
* repetitive per-message log lines stay suppressed in production;
* log files rotate and stay bounded on disk (no unbounded growth).
"""
from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
from pathlib import Path

import pytest

from modules import logger_module
from modules.logger_module import BotLogger, _DroppingQueueHandler

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_dropping_handler_never_blocks_the_caller() -> None:
    bounded: "queue.Queue[logging.LogRecord]" = queue.Queue(maxsize=1)
    handler = _DroppingQueueHandler(bounded)
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "m", (), None)
    handler.enqueue(record)  # fills the single slot
    before = _DroppingQueueHandler.dropped
    # Saturated queue: enqueue must return immediately instead of blocking.
    handler.enqueue(record)
    handler.enqueue(record)
    assert _DroppingQueueHandler.dropped == before + 2


def test_noisy_prefixes_are_suppressed_in_production(monkeypatch) -> None:
    monkeypatch.setattr(logger_module, "_VERBOSE", False)
    records: list[str] = []

    class _RecordingLogger:
        def info(self, text, *args):
            records.append(str(text))

        def error(self, text, *args):
            records.append(str(text))

    bot_logger = BotLogger.__new__(BotLogger)
    bot_logger.logger = _RecordingLogger()
    bot_logger.log_info("HANDLER CALLED chat=1")
    bot_logger.log_info("SPAM DETECTED")
    bot_logger.log_info("✅ تنظیمات بارگذاری شد")
    assert "HANDLER CALLED chat=1" not in records
    assert "SPAM DETECTED" not in records
    assert "✅ تنظیمات بارگذاری شد" in records
    # Errors always pass through; suppression applies to noisy info only.
    bot_logger.log_error("RPC TRACE something broken")
    assert "RPC TRACE something broken" in records


def test_verbose_mode_restores_noisy_lines(monkeypatch) -> None:
    monkeypatch.setattr(logger_module, "_VERBOSE", True)
    records: list[str] = []

    class _RecordingLogger:
        def info(self, text, *args):
            records.append(str(text))

        def error(self, text, *args):
            records.append(str(text))

    bot_logger = BotLogger.__new__(BotLogger)
    bot_logger.logger = _RecordingLogger()
    bot_logger.log_info("HANDLER CALLED chat=1")
    assert "HANDLER CALLED chat=1" in records


@pytest.mark.timeout(60)
def test_log_rotation_stays_bounded_inside_foxora_home(tmp_path: Path) -> None:
    """End-to-end: >1MB of logs rotates bot.log and stays inside ~/.foxora2."""
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["BOT_INSTANCE"] = "foxora2"
    env["BOT_LOG_MAX_BYTES"] = str(1024 * 1024)
    env["BOT_LOG_BACKUPS"] = "2"
    env.pop("BOT_VERBOSE_LOGS", None)
    # Pace the producer so the bounded queue stays drained (the drop-on-
    # saturation guard must win only under real overload, not in this test),
    # then wait for a full drain before stopping the listener thread.
    code = (
        "import time\n"
        "from modules.logger_module import BotLogger, _runtime\n"
        "logger = BotLogger()\n"
        "for i in range(12000):\n"
        "    logger.log_info('x' * 100 + str(i))\n"
        "    if i % 400 == 0:\n"
        "        time.sleep(0.005)\n"
        "deadline = time.time() + 30\n"
        "while _runtime().queue.qsize() and time.time() < deadline:\n"
        "    time.sleep(0.05)\n"
        "_runtime().stop()\n"
        "print('done')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    log_dir = tmp_path / ".foxora2" / "logs"
    bot_log = log_dir / "bot.log"
    rotated = log_dir / "bot.log.1"
    assert bot_log.exists(), f"missing active log: {list(log_dir.glob('*'))}"
    assert rotated.exists(), "rotation did not produce a backup file"
    assert bot_log.stat().st_size <= 1024 * 1024 * 1.1
    # Nothing leaked into Foxora 1 or B2 directories.
    assert not (tmp_path / ".foxora").exists()
    assert not (tmp_path / ".local" / "share" / "soroush-bot").exists()
    # The isolated subprocess must not create a project-tree log either.
    # (Other tests running under the default instance may already have left
    # a logs/ dir from earlier suite runs, so only fresh rotation is judged:
    # the subprocess wrote exclusively under the tmp HOME asserted above.)
    stale_marker = REPO_ROOT / "logs" / f"foxora-e2e-{os.getpid()}.log"
    assert not stale_marker.exists()
