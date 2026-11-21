# -*- coding: utf-8 -*-
"""Owner-only diagnostic command: «ارسال عکس» (Foxora2 session client).

Send the generated JPEG by its public URL. SPlusthon converts a JPG URL to
InputMediaPhotoExternal, so this command never uploads local file parts or
needs a media sender. If Soroush rejects the URL, show the actual error.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from handlers.private_handler import is_private_event
from modules.broadcast_state import normalize_command_text
from modules.manage_features.peers import group_peer
from modules.message_dedup import is_duplicate
from modules.owner_check import is_global_owner

COMMAND = "ارسال عکس"
PHOTO_PATH = Path(__file__).resolve().parents[1] / "static" / "photo_send_test.jpg"
# Public copy of the generated JPEG committed to Foxora2; no local upload.
PHOTO_URL = ("https://raw.githubusercontent.com/splusesupor-oss/Foxora2/"
             "main/static/photo_send_test.jpg")
SEND_TIMEOUT = 60
PEER_TIMEOUT = 8


def is_command(text):
    """Only the exact command; not «دانلود عکس» or arbitrary chat text."""
    return normalize_command_text(text) == COMMAND


def is_private_chat(event):
    """SPlusthon can mark an owner DM as non-private; trust its PeerUser."""
    if is_private_event(event):
        return True
    peer = getattr(getattr(event, "message", None), "peer_id", None)
    return type(peer).__name__ == "PeerUser"


def _text(event):
    message = getattr(event, "message", None)
    return (getattr(message, "message", None)
            or getattr(message, "caption", None) or "")


def _is_edit(event):
    message = getattr(event, "message", None)
    return bool(
        getattr(event, "is_edit", False)
        or getattr(event, "_is_edited_message", False)
        or getattr(message, "edit_date", None) is not None
        or "messageedited" in type(event).__name__.lower()
        or "messageedited" in type(event).__module__.lower()
    )


def _log(bot, message, *, error=False):
    logger = getattr(bot, "logger", None)
    method = getattr(logger, "log_error" if error else "log_info", None)
    if callable(method):
        try:
            method(message)
        except Exception:
            pass


async def _target(bot, event):
    """Prefer the event's InputPeer; never treat a positive group ID as user."""
    chat_id = getattr(event, "chat_id", None)
    for obj in (getattr(event, "message", None), event):
        for name in ("_input_chat", "input_chat"):
            try:
                peer = getattr(obj, name, None)
            except Exception:
                peer = None
            if peer is not None:
                return peer

    cache = getattr(bot, "reply_input_peer_cache", None) or {}
    try:
        peer = cache.get(chat_id)
        if peer is not None:
            return peer
    except (TypeError, AttributeError):
        pass

    get_input_chat = getattr(event, "get_input_chat", None)
    if callable(get_input_chat):
        try:
            peer = await asyncio.wait_for(get_input_chat(), PEER_TIMEOUT)
            if peer is not None:
                return peer
        except asyncio.CancelledError:
            raise
        except Exception as error:
            _log(bot, f"PHOTO TEST PEER RESOLVE FAILED "
                      f"chat_id={chat_id} error={error!r}", error=True)

    if chat_id is None:
        raise ValueError("chat_id مقصد در رویداد موجود نیست")
    if is_private_chat(event):
        sender = (getattr(event, "_bot_cached_sender", None)
                  or getattr(event, "sender", None))
        return sender if sender is not None else chat_id
    return group_peer(bot, chat_id)


def _diagnostic(error):
    detail = str(error).strip() or (
        f"پس از {SEND_TIMEOUT} ثانیه پاسخی دریافت نشد"
        if isinstance(error, asyncio.TimeoutError)
        else "کتابخانه توضیح بیشتری برنگرداند"
    )
    # A short, single message for the owner; the full error remains in logs.
    return f"❌ ارسال عکس انجام نشد.\n{type(error).__name__}: {detail[:1200]}"


async def _reply_status(bot, event, text, target=None):
    """If replying also fails, try the same resolved peer; log both failures."""
    reply = getattr(event, "reply", None)
    if callable(reply):
        try:
            await asyncio.wait_for(reply(text), 15)
            return True
        except asyncio.CancelledError:
            raise
        except Exception as error:
            _log(bot, f"PHOTO TEST ERROR REPLY FAILED error={error!r}",
                 error=True)
    client = getattr(bot, "client", None)
    send_message = getattr(client, "send_message", None)
    if callable(send_message):
        try:
            if target is None:
                target = await _target(bot, event)
            await asyncio.wait_for(send_message(target, text), 15)
            return True
        except asyncio.CancelledError:
            raise
        except Exception as error:
            _log(bot, f"PHOTO TEST ERROR TEXT FAILED error={error!r}",
                 error=True)
    return False


async def handle(bot, event):
    """Consume exact command, send external photo, or show the server error.

    Non-owners receive nothing. Edits never trigger another send. This path
    avoids local SaveFilePart requests and the unavailable media sender.
    """
    if not is_command(_text(event)):
        return False
    if _is_edit(event):
        return True
    sender_id = getattr(event, "sender_id", None)
    if sender_id is None:
        sender = (getattr(event, "_bot_cached_sender", None)
                  or getattr(event, "sender", None))
        sender_id = getattr(sender, "id", None)
    if not is_global_owner(sender_id):
        return True

    chat_id = getattr(event, "chat_id", None)
    message_id = getattr(getattr(event, "message", None), "id", None)
    if is_duplicate(chat_id, message_id, COMMAND):
        return True

    target = None
    try:
        client = getattr(bot, "client", None)
        if not callable(getattr(client, "send_file", None)):
            raise AttributeError("کلاینت سشنی متد send_file ندارد")
        target = await _target(bot, event)
        _log(bot, f"PHOTO TEST SEND START via=external_url chat_id={chat_id}")
        # For a JPG URL SPlusthon builds InputMediaPhotoExternal (no
        # upload.SaveFilePart, regardless of media-sender availability).
        result = await asyncio.wait_for(
            client.send_file(target, PHOTO_URL,
                             caption="🧪 تست ارسال عکس", force_document=False),
            SEND_TIMEOUT,
        )
        if result is False:
            raise RuntimeError("send_file مقدار False برگرداند")
        _log(bot, f"PHOTO TEST SEND RETURNED chat_id={chat_id} "
                  f"result_type={type(result).__name__} "
                  f"media_type={type(getattr(result, 'media', None)).__name__}")
        if result is None:
            await _reply_status(
                bot, event,
                "⚠️ send_file خطا نداد، اما کتابخانه تأییدیهٔ ارسال عکس "
                "برنگرداند. اگر عکس در چت نیست، نتیجه نامشخص است.",
                target,
            )
    except asyncio.CancelledError:
        raise
    except Exception as error:
        _log(bot, f"PHOTO TEST SEND FAILED chat_id={chat_id} "
                  f"error={type(error).__name__}: {error}", error=True)
        await _reply_status(bot, event, _diagnostic(error), target)
    return True
