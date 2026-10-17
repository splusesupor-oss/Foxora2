from __future__ import annotations

import json
import logging
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

_CHANNEL_ID_OFFSET = 1_000_000_000_000
_LOG = logging.getLogger(__name__)


def _group_aliases(group_id: str | int) -> tuple[str, ...]:
    raw = str(group_id)
    try:
        value = int(group_id)
    except (TypeError, ValueError):
        return (raw,)
    normalized = str(abs(value) - _CHANNEL_ID_OFFSET) if value <= -_CHANNEL_ID_OFFSET else raw
    return tuple(dict.fromkeys((raw, normalized)))


def _username(value: str | None) -> str:
    return str(value or "").lstrip("@").strip().lower()


class LegacyAdminRegistry:
    """Read-only compatibility lookup for source ``admins.json`` records.

    New admin registrations are stored by stable user ID in SQLite. This
    helper recognizes both source ID records and old username-only strings,
    then lets the event bridge promote a matching sender to an ID record.
    The source JSON is never modified.
    """

    _FILE_POLL_SECONDS = 5.0
    _DECISION_TTL_SECONDS = 30.0
    _MAX_DECISIONS = 4096
    _MAX_FILE_BYTES = 1_048_576

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._mtime_ns: int | None = None
        self._loaded = False
        self._loaded_at = 0.0
        self._data: dict[str, Any] = {}
        self._decisions: OrderedDict[tuple[str, str, str], tuple[float, bool]] = OrderedDict()
        self._promoted: set[tuple[str, str]] = set()
        self._last_error_signature: tuple[int | None, str] | None = None

    def _load(self) -> dict[str, Any]:
        now = time.monotonic()
        if self._loaded and now - self._loaded_at < self._FILE_POLL_SECONDS:
            return self._data
        try:
            file_stat = self.path.stat()
            mtime_ns = file_stat.st_mtime_ns
            size_bytes = file_stat.st_size
        except OSError:
            mtime_ns = None
            size_bytes = 0
        if self._loaded and mtime_ns == self._mtime_ns:
            self._loaded_at = now
            return self._data

        self._loaded = True
        self._loaded_at = now
        self._mtime_ns = mtime_ns
        self._data = {}
        self._decisions.clear()
        if mtime_ns is None:
            return self._data
        if size_bytes > self._MAX_FILE_BYTES:
            signature = (mtime_ns, "too-large")
            if signature != self._last_error_signature:
                _LOG.warning("legacy admin registry too large path=%s", self.path)
                self._last_error_signature = signature
            return self._data
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            signature = (mtime_ns, type(exc).__name__)
            if signature != self._last_error_signature:
                _LOG.warning(
                    "legacy admin registry unreadable path=%s error=%s",
                    self.path,
                    type(exc).__name__,
                )
                self._last_error_signature = signature
            return self._data
        if not isinstance(value, dict):
            signature = (mtime_ns, "invalid-root")
            if signature != self._last_error_signature:
                _LOG.warning("legacy admin registry invalid path=%s", self.path)
                self._last_error_signature = signature
            return self._data
        self._last_error_signature = None
        self._data = value
        return self._data

    def is_registered_admin(
        self,
        group_id: str | int,
        user_id: str | int | None,
        username: str | None = None,
    ) -> bool:
        if user_id is None:
            return False
        current_id = str(user_id)
        current_username = _username(username)
        cache_key = (str(group_id), current_id, current_username)
        now = time.monotonic()
        cached = self._decisions.get(cache_key)
        if cached is not None and cached[0] > now:
            self._decisions.move_to_end(cache_key)
            return cached[1]

        data = self._load()
        entries: list[Any] = []
        for alias in _group_aliases(group_id):
            group_entries = data.get(alias)
            if isinstance(group_entries, list):
                entries.extend(group_entries[:1000])
        authorized = any(
            (
                isinstance(entry, dict)
                and str(entry.get("user_id") or "") == current_id
            )
            or (
                isinstance(entry, str)
                and bool(current_username)
                and _username(entry) == current_username
            )
            for entry in entries
        )
        self._decisions[cache_key] = (now + self._DECISION_TTL_SECONDS, authorized)
        self._decisions.move_to_end(cache_key)
        while len(self._decisions) > self._MAX_DECISIONS:
            self._decisions.popitem(last=False)
        return authorized

    def is_promoted(self, group_id: str | int, user_id: str | int) -> bool:
        return (str(group_id), str(user_id)) in self._promoted

    def mark_promoted(self, group_id: str | int, user_id: str | int) -> None:
        key = (str(group_id), str(user_id))
        self._promoted.add(key)
        if len(self._promoted) > self._MAX_DECISIONS:
            self._promoted.pop()
