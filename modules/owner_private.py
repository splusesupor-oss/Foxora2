"""Resolve the configured global owner as a private *user* peer only.

A bare positive integer passed to SPlusthon may fall through to
``GetUsersRequest(InputUser(id, access_hash=0))`` and fail with ``NOT_FOUND``.
For this user-bot the global owner is normally the logged-in account itself, so
we first use ``get_me(input_peer=True)``.  That returns a concrete user input
peer with the correct access hash and cannot be reinterpreted as a group.
"""
from __future__ import annotations

import asyncio
import inspect
import time
import traceback
from typing import Any, List, Optional

from modules.atomic_write import write_json
from modules.owner_check import get_owner
from modules.runtime_paths import runtime_config_file


PEER_FILE = runtime_config_file("owner_peer.json", migrate=False)
_PEER_CACHE = {}
_PERSISTED_ACCESS_HASH = {}


class OwnerPrivateResolveError(RuntimeError):
    """No safe private-user peer could be resolved for the configured owner."""


def _log(logger: Any, level: str, message: str) -> None:
    if logger is None:
        return
    method = getattr(logger, "log_error" if level == "error" else "log_info", None)
    if callable(method):
        method(message)


def peer_user_id(peer: Any) -> Optional[int]:
    if peer is None:
        return None
    if getattr(peer, "channel_id", None) is not None:
        return None
    if getattr(peer, "chat_id", None) is not None:
        return None
    value = getattr(peer, "user_id", getattr(peer, "id", None))
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _validated_user_peer(peer: Any, owner_id: int, method: str) -> Any:
    if peer is None:
        raise OwnerPrivateResolveError(f"{method} returned None")
    if getattr(peer, "channel_id", None) is not None:
        raise OwnerPrivateResolveError(
            f"{method} resolved owner as channel peer"
        )
    if getattr(peer, "chat_id", None) is not None:
        raise OwnerPrivateResolveError(
            f"{method} resolved owner as chat peer"
        )
    peer_id = peer_user_id(peer)
    if peer_id != int(owner_id):
        raise OwnerPrivateResolveError(
            f"{method} user mismatch: expected={owner_id} actual={peer_id} "
            f"peer_type={peer.__class__.__name__}"
        )
    return peer


def _attempt_text(method: str, error: BaseException) -> str:
    return (
        f"method={method} error_type={type(error).__name__} "
        f"error={error!r}"
    )


def current_owner_user_id() -> int:
    """Read the sole owner authority through ``get_owner()``."""
    owner = get_owner()
    if not isinstance(owner, dict) or owner.get("user_id") is None:
        raise OwnerPrivateResolveError("global owner is not configured")
    try:
        owner_id = int(owner["user_id"])
    except (TypeError, ValueError) as error:
        raise OwnerPrivateResolveError("global owner user_id is invalid") from error
    if owner_id <= 0:
        raise OwnerPrivateResolveError("global owner user_id must be positive")
    return owner_id


async def _persist_owner_peer(
    owner_id: int,
    access_hash: int,
    logger: Any = None,
) -> None:
    payload = {
        "version": 1,
        "owner_id": int(owner_id),
        "access_hash": int(access_hash),
        "updated_at": time.time(),
    }
    try:
        await asyncio.to_thread(write_json, PEER_FILE, payload, indent=2)
        try:
            PEER_FILE.chmod(0o600)
        except OSError:
            pass
        _PERSISTED_ACCESS_HASH[int(owner_id)] = int(access_hash)
    except Exception as error:
        _log(
            logger,
            "error",
            "OWNER PRIVATE PEER PERSIST FAILED "
            f"owner_id={owner_id} error={error!r}",
        )


def _access_hash(peer: Any) -> Optional[int]:
    value = getattr(peer, "access_hash", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


async def remember_owner_peer(
    client: Any,
    *,
    event: Any = None,
    sender: Any = None,
    logger: Any = None,
) -> bool:
    """Capture the current owner's real user peer from an incoming event.

    SPlusthon user-bots cannot resolve an arbitrary numeric user with
    ``access_hash=0``.  Incoming owner messages carry the real User/InputPeer
    and access hash, which is safe to cache and persist for later background
    reports.
    """
    owner_id = current_owner_user_id()
    sender_id = getattr(sender, "id", None)
    if sender_id is None and event is not None:
        sender_id = getattr(event, "sender_id", None)
    try:
        if int(sender_id) != owner_id:
            return False
    except (TypeError, ValueError):
        return False

    candidates = []
    if sender is not None:
        try:
            from splusthon import utils

            candidates.append(("sender_user_object", utils.get_input_peer(sender)))
        except Exception:
            pass
    if event is not None:
        candidate = getattr(event, "input_sender", None)
        if candidate is not None:
            candidates.append(("event.input_sender", candidate))
        getter = getattr(event, "get_input_sender", None)
        if callable(getter):
            try:
                candidate = getter()
                if inspect.isawaitable(candidate):
                    candidate = await candidate
                if candidate is not None:
                    candidates.append(("event.get_input_sender()", candidate))
            except Exception as error:
                _log(
                    logger,
                    "error",
                    "OWNER PRIVATE PEER CAPTURE FAILED "
                    f"owner_id={owner_id} method=event.get_input_sender() "
                    f"error={error!r}",
                )

    for method, candidate in candidates:
        try:
            target = _validated_user_peer(candidate, owner_id, method)
            access_hash = _access_hash(target)
            if access_hash is None:
                continue
            previous = _access_hash(_PEER_CACHE.get(owner_id))
            _PEER_CACHE[owner_id] = target
            if previous != access_hash or _PERSISTED_ACCESS_HASH.get(owner_id) != access_hash:
                asyncio.create_task(
                    _persist_owner_peer(owner_id, access_hash, logger),
                    name="persist-owner-private-peer",
                )
            _log(
                logger,
                "info",
                "OWNER PRIVATE PEER CAPTURED "
                f"owner_id={owner_id} method={method} "
                f"peer_type={target.__class__.__name__}",
            )
            return True
        except Exception:
            continue
    return False


def _persisted_owner_peer(owner_id: int) -> Any:
    try:
        import json
        from splusthon import types

        data = json.loads(PEER_FILE.read_text(encoding="utf-8"))
        if int(data.get("owner_id")) != int(owner_id):
            return None
        access_hash = int(data["access_hash"])
        _PERSISTED_ACCESS_HASH[int(owner_id)] = access_hash
        return types.InputPeerUser(int(owner_id), access_hash)
    except (ImportError, OSError, KeyError, TypeError, ValueError):
        return None


async def _dialog_owner_peer(client: Any, owner_id: int) -> Any:
    getter = getattr(client, "get_dialogs", None)
    if not callable(getter):
        return None
    dialogs = getter(limit=200)
    if inspect.isawaitable(dialogs):
        dialogs = await dialogs
    for dialog in dialogs or ():
        dialog_id = getattr(dialog, "id", None)
        entity = getattr(dialog, "entity", None)
        if dialog_id is None:
            dialog_id = getattr(entity, "id", None)
        try:
            if int(dialog_id) != int(owner_id):
                continue
        except (TypeError, ValueError):
            continue
        candidate = getattr(dialog, "input_entity", None)
        if candidate is None and entity is not None:
            try:
                from splusthon import utils

                candidate = utils.get_input_peer(entity)
            except Exception:
                candidate = None
        return candidate
    return None


async def resolve_private_owner_peer(
    client: Any,
    *,
    logger: Any = None,
    context: str = "OWNER_REPORT",
) -> Any:
    """Return only a concrete private-user peer for the current ``get_owner()``.

    Resolution order is deliberately user-only:

    1. owner peer captured from a real incoming message;
    2. persisted owner peer/access hash;
    3. logged-in self peer, only when its ID matches;
    4. SPlusthon memory/session caches and private dialogs;
    5. explicit ``PeerUser(owner_id)`` network fallback with full error log.

    The final fallback may internally use ``GetUsersRequest``.  If it fails,
    the complete traceback, owner ID and method are logged as requested.
    """
    # Do not accept an owner ID from callers.  This prevents stale runtime or
    # legacy call sites from injecting a former owner into report delivery.
    configured_owner_id = current_owner_user_id()
    attempts: List[str] = []

    # Best path for a separate owner account: a real peer captured from an
    # owner message, containing the access hash that numeric IDs do not have.
    for method, candidate in (
        ("captured_owner_peer", _PEER_CACHE.get(configured_owner_id)),
        ("persisted_owner_peer", _persisted_owner_peer(configured_owner_id)),
    ):
        try:
            target = _validated_user_peer(candidate, configured_owner_id, method)
            _log(
                logger,
                "info",
                f"{context} OWNER PRIVATE RESOLVED "
                f"owner_id={configured_owner_id} method={method} "
                f"peer_type={target.__class__.__name__}",
            )
            return target
        except Exception as error:
            attempts.append(_attempt_text(method, error))

    # Self is valid only when the configured owner is the logged-in account.
    method = "get_me(input_peer=True)"
    get_me = getattr(client, "get_me", None)
    if callable(get_me):
        try:
            candidate = get_me(input_peer=True)
            if inspect.isawaitable(candidate):
                candidate = await candidate
            target = _validated_user_peer(candidate, configured_owner_id, method)
            _log(
                logger,
                "info",
                f"{context} OWNER PRIVATE RESOLVED "
                f"owner_id={configured_owner_id} method={method} "
                f"peer_type={target.__class__.__name__}",
            )
            return target
        except Exception as error:
            attempts.append(_attempt_text(method, error))
    else:
        attempts.append(f"method={method} error=get_me_unavailable")

    # Cache-only path: never makes GetUsersRequest and never guesses chat type.
    method = "memory_entity_cache(user_id)"
    try:
        cache = getattr(client, "_mb_entity_cache", None)
        if cache is None:
            raise OwnerPrivateResolveError("memory entity cache unavailable")
        entry = cache.get(configured_owner_id)
        if entry is None:
            raise OwnerPrivateResolveError("owner absent from memory entity cache")
        candidate = entry._as_input_peer()
        target = _validated_user_peer(candidate, configured_owner_id, method)
        _log(
            logger,
            "info",
            f"{context} OWNER PRIVATE RESOLVED "
            f"owner_id={configured_owner_id} method={method} "
            f"peer_type={target.__class__.__name__}",
        )
        return target
    except Exception as error:
        attempts.append(_attempt_text(method, error))

    # Persistent session cache, constrained explicitly to PeerUser.
    method = "session.get_input_entity(PeerUser)"
    try:
        from splusthon import types

        session = getattr(client, "session", None)
        resolver = getattr(session, "get_input_entity", None)
        if not callable(resolver):
            raise OwnerPrivateResolveError("session entity cache unavailable")
        candidate = resolver(types.PeerUser(configured_owner_id))
        if inspect.isawaitable(candidate):
            candidate = await candidate
        target = _validated_user_peer(candidate, configured_owner_id, method)
        _log(
            logger,
            "info",
            f"{context} OWNER PRIVATE RESOLVED "
            f"owner_id={configured_owner_id} method={method} "
            f"peer_type={target.__class__.__name__}",
        )
        return target
    except Exception as error:
        attempts.append(_attempt_text(method, error))

    # Existing private dialogs carry a concrete user entity/access hash and
    # can recover delivery immediately after an upgrade, before the owner sends
    # another message.
    method = "get_dialogs(private_user_id)"
    try:
        candidate = await _dialog_owner_peer(client, configured_owner_id)
        target = _validated_user_peer(candidate, configured_owner_id, method)
        access_hash = _access_hash(target)
        if access_hash is not None:
            _PEER_CACHE[configured_owner_id] = target
            if _PERSISTED_ACCESS_HASH.get(configured_owner_id) != access_hash:
                asyncio.create_task(
                    _persist_owner_peer(configured_owner_id, access_hash, logger),
                    name="persist-owner-private-peer",
                )
        _log(
            logger,
            "info",
            f"{context} OWNER PRIVATE RESOLVED "
            f"owner_id={configured_owner_id} method={method} "
            f"peer_type={target.__class__.__name__}",
        )
        return target
    except Exception as error:
        attempts.append(_attempt_text(method, error))

    # Last resort. Passing PeerUser prevents positive ID ambiguity. If the
    # access hash is absent SPlusthon may issue GetUsersRequest; retain its full
    # failure instead of silently retrying the raw integer as before.
    method = "get_input_entity(PeerUser)"
    try:
        from splusthon import types

        resolver = getattr(client, "get_input_entity", None)
        if not callable(resolver):
            raise OwnerPrivateResolveError("client resolver unavailable")
        candidate = resolver(types.PeerUser(configured_owner_id))
        if inspect.isawaitable(candidate):
            candidate = await candidate
        target = _validated_user_peer(candidate, configured_owner_id, method)
        _log(
            logger,
            "info",
            f"{context} OWNER PRIVATE RESOLVED "
            f"owner_id={configured_owner_id} method={method} "
            f"peer_type={target.__class__.__name__}",
        )
        return target
    except Exception as error:
        attempts.append(_attempt_text(method, error))
        full_traceback = traceback.format_exc()
        _log(
            logger,
            "error",
            f"{context} OWNER PRIVATE RESOLVE FAILED "
            f"owner_id={configured_owner_id} method={method} "
            f"error_type={type(error).__name__} error={error!r} "
            f"attempts={' | '.join(attempts)}\n"
            f"traceback={full_traceback}",
        )
        raise OwnerPrivateResolveError(
            f"could not resolve private owner user_id={configured_owner_id}; "
            f"last_method={method}; error={error!r}"
        ) from error
