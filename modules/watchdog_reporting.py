"""Crash-report handoff between the external watchdog and the running bot.

The watchdog cannot safely open the same SPlusthon session while the bot is
running.  It therefore persists a bounded pending incident.  After a healthy
restart the bot sends the report with its already-connected client.  If the
bot repeatedly fails before reaching that point, the watchdog may call
``deliver_with_fresh_client`` while no child process is alive.

Only the global owner returned by :mod:`modules.owner_check` is ever accepted
as a destination.  No owner ID is defined in this module.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from modules.owner_private import (
    OwnerPrivateResolveError,
    peer_user_id,
    resolve_private_owner_peer,
)
from modules.runtime_paths import PROJECT_ROOT, runtime_config_file


PENDING_FILE = runtime_config_file("watchdog_pending.json", migrate=False)
_DEFAULT_MESSAGE_LIMIT = 3500
_DEFAULT_TRACEBACK_LIMIT = 12000
_SENT_RETENTION_SECONDS = 7 * 24 * 60 * 60
_MAX_PENDING_INCIDENTS = 10


class WatchdogDeliveryError(RuntimeError):
    """A pending watchdog report could not be delivered safely."""


def _state_path(path: Optional[os.PathLike] = None) -> Path:
    return Path(path) if path is not None else PENDING_FILE


def _empty_state() -> Dict[str, Any]:
    return {"version": 1, "pending": [], "sent": {}, "suppressed": {}}


def load_state(path: Optional[os.PathLike] = None) -> Dict[str, Any]:
    target = _state_path(path)
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _empty_state()
    except (OSError, ValueError, TypeError):
        return _empty_state()
    data.setdefault("version", 1)
    data.setdefault("pending", [])
    data.setdefault("sent", {})
    data.setdefault("suppressed", {})
    if not isinstance(data["pending"], list):
        data["pending"] = []
    if not isinstance(data["sent"], dict):
        data["sent"] = {}
    if not isinstance(data["suppressed"], dict):
        data["suppressed"] = {}
    return data


def save_state(state: Dict[str, Any], path: Optional[os.PathLike] = None) -> None:
    """Atomically persist pending-delivery state."""
    target = _state_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, target)
        try:
            target.chmod(0o600)
        except OSError:
            pass
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def incident_fingerprint(incident: Dict[str, Any]) -> str:
    raw = "|".join(
        str(incident.get(key, ""))
        for key in ("error_type", "file", "line", "summary")
    )
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()


def queue_incident(
    incident: Dict[str, Any],
    *,
    cooldown_seconds: float = 15 * 60,
    state_path: Optional[os.PathLike] = None,
    now: Optional[float] = None,
) -> bool:
    """Queue one owner notification, deduplicating repeated crash loops.

    Returns ``True`` only when a new pending owner report was created.  Every
    crash may still have its own full local log; this function only controls
    private-message volume.
    """
    timestamp = float(now if now is not None else time.time())
    item = dict(incident)
    item.setdefault("id", uuid.uuid4().hex)
    item.setdefault("created_at_epoch", timestamp)
    item.setdefault("last_seen_epoch", timestamp)
    item.setdefault("occurrences", 1)
    item.setdefault("next_chunk", 0)
    fingerprint = item.get("fingerprint") or incident_fingerprint(item)
    item["fingerprint"] = fingerprint

    state = load_state(state_path)
    sent = state["sent"]
    # Keep the dedup state bounded across months of uptime.
    for key, value in list(sent.items()):
        try:
            age = timestamp - float(value)
        except (TypeError, ValueError):
            age = _SENT_RETENTION_SECONDS + 1
        if age > _SENT_RETENTION_SECONDS:
            sent.pop(key, None)
            state["suppressed"].pop(key, None)

    try:
        last_sent = float(sent.get(fingerprint, 0))
    except (TypeError, ValueError):
        last_sent = 0
    if last_sent and timestamp - last_sent < max(0.0, cooldown_seconds):
        state["suppressed"][fingerprint] = int(
            state["suppressed"].get(fingerprint, 0)
        ) + 1
        save_state(state, state_path)
        return False

    for pending in state["pending"]:
        if pending.get("fingerprint") != fingerprint:
            continue
        pending["occurrences"] = int(pending.get("occurrences", 1)) + 1
        pending["last_seen_epoch"] = timestamp
        # The newest crash log contains the most useful complete traceback.
        for key in ("traceback", "crash_log", "exit_code", "uptime_seconds"):
            if item.get(key) not in (None, ""):
                pending[key] = item[key]
        save_state(state, state_path)
        return False

    state["pending"].append(item)
    if len(state["pending"]) > _MAX_PENDING_INCIDENTS:
        state["pending"] = state["pending"][-_MAX_PENDING_INCIDENTS:]
    save_state(state, state_path)
    return True


def pending_count(state_path: Optional[os.PathLike] = None) -> int:
    return len(load_state(state_path).get("pending", []))


def _traceback_for_message(incident: Dict[str, Any]) -> str:
    traceback_text = str(incident.get("traceback") or "Traceback ثبت نشده است.")
    try:
        limit = max(
            1000,
            int(os.environ.get(
                "WATCHDOG_OWNER_TRACEBACK_MAX_CHARS",
                str(_DEFAULT_TRACEBACK_LIMIT),
            )),
        )
    except (TypeError, ValueError):
        limit = _DEFAULT_TRACEBACK_LIMIT
    if len(traceback_text) <= limit:
        return traceback_text
    head = limit // 2
    tail = limit - head
    crash_log = incident.get("crash_log") or "watchdog crash log"
    return (
        traceback_text[:head]
        + "\n\n... بخش میانی برای جلوگیری از پیام‌های بیش‌ازحد طولانی حذف شد؛ "
          "نسخه کامل در این فایل ثبت است:\n"
        + str(crash_log)
        + "\n...\n\n"
        + traceback_text[-tail:]
    )


def format_owner_report(
    incident: Dict[str, Any],
    status: str = "ربات دوباره راه‌اندازی شد",
) -> str:
    """Build the exact Persian owner-only crash report."""
    summary = str(incident.get("summary") or "توقف غیرعادی فرایند ربات")
    occurrences = int(incident.get("occurrences", 1) or 1)
    if occurrences > 1:
        summary += f"\nتعداد تکرار پیش از بازیابی: {occurrences}"
    crash_log = incident.get("crash_log")
    if crash_log:
        summary += f"\nگزارش کامل محلی: {crash_log}"
    return (
        "🚨 خطای ربات\n\n"
        "زمان:\n"
        f"{incident.get('time_local') or '-'}\n\n"
        "نوع خطا:\n"
        f"{incident.get('error_type') or 'UnexpectedExit'}\n\n"
        "فایل:\n"
        f"{incident.get('file') or '-'}\n\n"
        "خط:\n"
        f"{incident.get('line') or '-'}\n\n"
        "جزئیات:\n"
        f"{summary}\n\n"
        "Traceback کامل:\n"
        f"{_traceback_for_message(incident)}\n\n"
        "وضعیت:\n"
        f"{status}"
    )


def split_message(text: str, limit: Optional[int] = None) -> List[str]:
    """Split a long report without losing any of its selected content."""
    if limit is None:
        try:
            limit = int(os.environ.get(
                "WATCHDOG_OWNER_MESSAGE_LIMIT", str(_DEFAULT_MESSAGE_LIMIT)
            ))
        except (TypeError, ValueError):
            limit = _DEFAULT_MESSAGE_LIMIT
    limit = max(500, int(limit))
    if len(text) <= limit:
        return [text]

    payload_limit = max(400, limit - 40)
    raw_parts = [
        text[index:index + payload_limit]
        for index in range(0, len(text), payload_limit)
    ]
    total = len(raw_parts)
    return [
        f"گزارش Watchdog — بخش {index} از {total}\n\n{part}"
        for index, part in enumerate(raw_parts, 1)
    ]


async def _resolve_private_owner(
    client: Any,
    logger: Any = None,
) -> Any:
    """Resolve only the user ID currently returned by get_owner()."""
    try:
        return await resolve_private_owner_peer(
            client,
            logger=logger,
            context="WATCHDOG_CRASH",
        )
    except OwnerPrivateResolveError as error:
        raise WatchdogDeliveryError(str(error)) from error


def _log(logger: Any, level: str, message: str) -> None:
    if logger is None:
        return
    method = getattr(logger, "log_error" if level == "error" else "log_info", None)
    if callable(method):
        method(message)


async def deliver_pending_reports(
    client: Any,
    *,
    status: str = "ربات دوباره راه‌اندازی شد",
    logger: Any = None,
    state_path: Optional[os.PathLike] = None,
    message_limit: Optional[int] = None,
    send_timeout: float = 60.0,
) -> int:
    """Send all pending reports to the single configured private owner.

    Chunk progress is persisted after every successful send, so a temporary
    network failure does not resend already delivered chunks.
    """
    state = load_state(state_path)
    if not state["pending"]:
        return 0

    target = await _resolve_private_owner(client, logger)
    owner_id = peer_user_id(target)
    delivered = 0

    for snapshot in list(state["pending"]):
        incident_id = snapshot.get("id")
        state = load_state(state_path)
        incident = next(
            (row for row in state["pending"] if row.get("id") == incident_id),
            None,
        )
        if incident is None:
            continue
        delivery_status = incident.get("delivery_status") or status
        incident["delivery_status"] = delivery_status
        report = format_owner_report(incident, delivery_status)
        chunks = split_message(report, message_limit)
        next_chunk = max(0, int(incident.get("next_chunk", 0) or 0))
        save_state(state, state_path)

        try:
            for index in range(next_chunk, len(chunks)):
                result = client.send_message(target, chunks[index])
                if inspect.isawaitable(result):
                    await asyncio.wait_for(result, timeout=send_timeout)
                incident["next_chunk"] = index + 1
                save_state(state, state_path)
        except Exception as error:
            _log(
                logger,
                "error",
                "WATCHDOG OWNER REPORT FAILED "
                f"incident={incident_id} chunk={incident.get('next_chunk', 0)} "
                f"error={error!r}",
            )
            raise

        state["pending"] = [
            row for row in state["pending"] if row.get("id") != incident_id
        ]
        fingerprint = incident.get("fingerprint") or incident_fingerprint(incident)
        state["sent"][fingerprint] = time.time()
        state["suppressed"].pop(fingerprint, None)
        save_state(state, state_path)
        delivered += 1
        _log(
            logger,
            "info",
            "WATCHDOG OWNER REPORT SENT "
            f"incident={incident_id} owner_id={owner_id} chunks={len(chunks)}",
        )
    return delivered


def _deployment_config() -> Dict[str, Any]:
    config_file = PROJECT_ROOT / "config" / "config.json"
    try:
        data = json.loads(config_file.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


async def deliver_with_fresh_client(
    *,
    status: str = "نیاز به بررسی دارد",
    logger: Any = None,
    state_path: Optional[os.PathLike] = None,
) -> int:
    """Fallback delivery used only while the supervised bot is not running."""
    from dotenv import load_dotenv
    from splusthon import SoroushClient
    from splusthon.sessions import StringSession

    load_dotenv(PROJECT_ROOT / ".env")
    config = _deployment_config()
    session_string = (
        os.environ.get("SOROUSH_SESSION_STRING")
        or config.get("session_string")
        or ""
    )
    if not session_string:
        raise WatchdogDeliveryError("SOROUSH_SESSION_STRING is not configured")
    api_id = os.environ.get("API_ID") or config.get("api_id")
    api_hash = os.environ.get("API_HASH") or config.get("api_hash")
    session = StringSession(session_string)
    client = (
        SoroushClient(session, api_id, api_hash)
        if api_id and api_hash
        else SoroushClient(session)
    )
    await asyncio.wait_for(client.connect(), timeout=60.0)
    try:
        return await deliver_pending_reports(
            client,
            status=status,
            logger=logger,
            state_path=state_path,
        )
    finally:
        try:
            await asyncio.wait_for(client.disconnect(), timeout=20.0)
        except Exception:
            pass
