from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, int] = {
    "BronzeValue": 1,
    "SilverValue": 10,
    "GoldValue": 100,
    "BronzeToSilverCost": 100,
    "BronzeToSilverGain": 12,
    "SilverToGoldCost": 70,
    "SilverToGoldGain": 10,
    "DailyRewardBronze": 25,
    "DailyRewardSilver": 0,
    "DailyRewardGold": 0,
    "DailyRewardCooldownSeconds": 86_400,
    "SettingsVersion": 2,
}
_UPGRADE_KEYS = ("BronzeToSilverGain", "SilverToGoldGain")
_ZERO_ALLOWED = {"DailyRewardBronze", "DailyRewardSilver", "DailyRewardGold"}
_MAX_SETTINGS_FILE_BYTES = 65_536
_MAX_SETTING_VALUE = 1_000_000_000
_MAX_SETTINGS_VERSION = 1_000_000
_MAX_DAILY_COOLDOWN_SECONDS = 366 * 24 * 60 * 60
_LOCK = threading.RLock()
_CACHE: dict[str, int] | None = None
_CACHE_PATH: str | None = None
_CACHE_SIGNATURE: tuple[int, int, int] | None = None


def settings_path() -> Path:
    return Path(os.getenv("FOX_ECONOMY_SETTINGS_FILE", "config/economy_settings.json")).expanduser()


def _file_signature(path: Path) -> tuple[int, int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size


def _coerce(key: str, value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = int(value)
    except (OverflowError, ValueError):
        return None
    if number != value:
        return None
    minimum = 0 if key in _ZERO_ALLOWED else 1
    maximum = (
        _MAX_DAILY_COOLDOWN_SECONDS
        if key == "DailyRewardCooldownSeconds"
        else _MAX_SETTINGS_VERSION
        if key == "SettingsVersion"
        else _MAX_SETTING_VALUE
    )
    return number if minimum <= number <= maximum else None


def _atomic_write(path: Path, values: dict[str, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix="economy-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(values, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def load() -> dict[str, int]:
    global _CACHE, _CACHE_PATH, _CACHE_SIGNATURE
    path = settings_path()
    path_key = str(path.resolve())
    signature = _file_signature(path)
    with _LOCK:
        if _CACHE is not None and _CACHE_PATH == path_key and _CACHE_SIGNATURE == signature:
            return dict(_CACHE)
        values = (
            dict(_CACHE)
            if _CACHE is not None and _CACHE_PATH == path_key
            else dict(DEFAULTS)
        )
        if signature is not None and signature[2] <= _MAX_SETTINGS_FILE_BYTES:
            try:
                with path.open("rb") as stream:
                    payload = stream.read(_MAX_SETTINGS_FILE_BYTES + 1)
                raw = (
                    json.loads(payload.decode("utf-8"))
                    if len(payload) <= _MAX_SETTINGS_FILE_BYTES
                    else None
                )
            except (OSError, UnicodeError, ValueError, RecursionError):
                raw = None
            if isinstance(raw, dict):
                for key in DEFAULTS:
                    candidate = _coerce(key, raw.get(key))
                    if candidate is not None:
                        values[key] = candidate
                version = _coerce("SettingsVersion", raw.get("SettingsVersion", 1)) or 1
                if version < DEFAULTS["SettingsVersion"]:
                    for key in _UPGRADE_KEYS:
                        values[key] = DEFAULTS[key]
                    values["SettingsVersion"] = DEFAULTS["SettingsVersion"]
        _CACHE = values
        _CACHE_PATH = path_key
        _CACHE_SIGNATURE = signature
        return dict(values)


def save(updates: dict[str, Any]) -> dict[str, int]:
    global _CACHE, _CACHE_PATH, _CACHE_SIGNATURE
    values = load()
    for key, value in updates.items():
        if key not in DEFAULTS:
            raise ValueError(f"unknown economy setting: {key}")
        coerced = _coerce(key, value)
        if coerced is None:
            raise ValueError(f"invalid economy setting: {key}")
        values[key] = coerced
    path = settings_path()
    with _LOCK:
        _atomic_write(path, values)
        _CACHE = values
        _CACHE_PATH = str(path.resolve())
        _CACHE_SIGNATURE = _file_signature(path)
        return dict(values)


def get(key: str, default: Any = None) -> Any:
    return load().get(key, DEFAULTS.get(key, default))


def coin_values() -> dict[str, int]:
    values = load()
    return {
        "bronze": values["BronzeValue"],
        "silver": values["SilverValue"],
        "gold": values["GoldValue"],
    }


def reset_cache() -> None:
    global _CACHE, _CACHE_PATH, _CACHE_SIGNATURE
    with _LOCK:
        _CACHE = None
        _CACHE_PATH = None
        _CACHE_SIGNATURE = None
