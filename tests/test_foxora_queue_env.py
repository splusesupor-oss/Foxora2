"""Env-knob contract of the preserved Foxora queue components."""
from __future__ import annotations

from modules.fair_queue import FairModerationQueue
from modules.rpc_queue import SerialRpcQueue


def test_serial_rpc_queue_env_defaults() -> None:
    queue = SerialRpcQueue.from_env()
    assert queue.max_pending_total == 256
    assert queue.max_pending_per_group == 32
    assert abs(queue.min_interval_seconds - 0.25) < 1e-9


def test_serial_rpc_queue_env_overrides(monkeypatch) -> None:
    monkeypatch.setenv("RPC_MAX_PENDING_TOTAL", "1000")
    monkeypatch.setenv("RPC_MAX_PENDING_PER_GROUP", "100")
    monkeypatch.setenv("RPC_MIN_INTERVAL_SECONDS", "1.5")
    queue = SerialRpcQueue.from_env()
    assert queue.max_pending_total == 1000
    assert queue.max_pending_per_group == 100
    assert abs(queue.min_interval_seconds - 1.5) < 1e-9


def test_serial_rpc_queue_env_is_clamped_to_documented_bounds(monkeypatch) -> None:
    monkeypatch.setenv("RPC_MAX_PENDING_TOTAL", "999999")
    monkeypatch.setenv("RPC_MAX_PENDING_PER_GROUP", "0")
    monkeypatch.setenv("RPC_MIN_INTERVAL_SECONDS", "500")
    queue = SerialRpcQueue.from_env()
    assert queue.max_pending_total == 4096  # accepted bound: 1-4096
    assert queue.max_pending_per_group == 1
    assert queue.min_interval_seconds == 60.0  # accepted bound: 0-60 seconds


def test_fair_moderation_queue_env_defaults() -> None:
    queue = FairModerationQueue.from_env()
    assert queue.max_pending_total == 256
    assert queue.max_pending_per_group == 16
    assert queue.max_groups == 128
