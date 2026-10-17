from __future__ import annotations

import asyncio
import inspect
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any

from foxbot.domain import Member, MemberPage, ModerationResult
from foxbot.platform import IncompleteMemberScan, PlatformCapabilityUnavailable
from foxbot.rpc_queue import SerialRpcQueue

# TL flag names used by SPlusthon's group participant restrictions. Unknown
# fields are preserved dynamically when the installed library exposes them.
_KNOWN_RIGHT_FLAGS = (
    "view_messages", "send_messages", "send_media", "send_stickers", "send_gifs",
    "send_games", "send_inline", "embed_links", "send_polls", "change_info",
    "invite_users", "pin_messages", "manage_topics", "send_photos", "send_videos",
    "send_roundvideos", "send_audios", "send_voices", "send_docs", "send_plain",
)
_MUTE_FLAGS = (
    "send_messages", "send_media", "send_stickers", "send_gifs", "send_games",
    "send_inline", "embed_links", "send_polls", "send_photos", "send_videos",
    "send_roundvideos", "send_audios", "send_voices", "send_docs", "send_plain",
)


class _NamedBytesIO(BytesIO):
    def __init__(self, content: bytes, name: str) -> None:
        super().__init__(content)
        self.name = name


class SPlusthonAdapter:
    """SPlusthon User-Bot adapter; every top-level API call uses SerialRpcQueue.

    The official Bot API does not expose the same member-management surface in
    its published documentation. This adapter intentionally targets the
    SPlusthon User-Bot protocol and fails closed when a TL capability is absent.
    """

    def __init__(self, client: Any, rpc_queue: SerialRpcQueue) -> None:
        self.client = client
        self.rpc_queue = rpc_queue

    async def _rpc(self, group_id: str, operation: str, call, *, retry_safe: bool = False):
        return await self.rpc_queue.submit(
            group_id,
            operation,
            call,
            retry_safe=retry_safe,
            max_attempts=2 if retry_safe else 1,
        )

    @staticmethod
    def _require_tl():
        try:
            from splusthon.tl import functions, types
        except ImportError as exc:
            raise PlatformCapabilityUnavailable("SPlusthon is not installed") from exc
        return functions, types

    @staticmethod
    def _user_id(participant: Any) -> str | None:
        for candidate in (
            getattr(participant, "user_id", None),
            getattr(getattr(participant, "peer", None), "user_id", None),
        ):
            if candidate is not None:
                return str(candidate)
        return None

    @staticmethod
    def _rights(participant: Any) -> dict[str, bool] | None:
        rights = getattr(participant, "banned_rights", None)
        if rights is None:
            return None
        return {name: bool(getattr(rights, name, False)) for name in _KNOWN_RIGHT_FLAGS}

    @staticmethod
    def _is_admin_participant(participant: Any) -> bool | None:
        name = type(participant).__name__.lower()
        if "creator" in name or "admin" in name:
            return True
        if "banned" in name or "kicked" in name or "left" in name:
            return False
        if "participant" in name:
            return False
        return None

    @staticmethod
    def _normalize_until(value: datetime | int | str | None) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=UTC)
        if isinstance(value, int):
            return datetime.fromtimestamp(value, UTC)
        text = str(value).strip()
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            try:
                return datetime.fromtimestamp(float(text), UTC)
            except (ValueError, OverflowError, OSError) as exc:
                raise ValueError("invalid stored restriction expiry") from exc
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)

    @staticmethod
    def _is_expired(rights: Any) -> bool:
        until = getattr(rights, "until_date", None)
        if until is None:
            return False
        if isinstance(until, (int, float)):
            return until > 0 and until <= datetime.now(UTC).timestamp()
        if isinstance(until, datetime):
            if until.tzinfo is None:
                until = until.replace(tzinfo=UTC)
            return until <= datetime.now(UTC)
        return False

    @staticmethod
    def _is_muted_rights(rights: Any) -> bool:
        if rights is None or bool(getattr(rights, "view_messages", False)):
            return False
        if SPlusthonAdapter._is_expired(rights):
            return False
        return any(bool(getattr(rights, flag, False)) for flag in ("send_messages", "send_plain"))

    @staticmethod
    def _is_banned_rights(rights: Any) -> bool:
        return bool(
            rights is not None
            and getattr(rights, "view_messages", False)
            and not SPlusthonAdapter._is_expired(rights)
        )

    @staticmethod
    def _user_display(user: Any) -> str:
        return " ".join(
            rendered
            for part in (
                getattr(user, "first_name", None),
                getattr(user, "last_name", None),
            )
            if part and (rendered := str(part)[:128].strip())
        ).strip()[:128]

    async def list_members_page(
        self, group_id: str, offset: int, limit: int, *, include_bio: bool = False
    ) -> MemberPage:
        if limit < 1 or offset < 0:
            raise ValueError("invalid pagination")
        functions, types = self._require_tl()
        search_cls = getattr(types, "ChannelParticipantsSearch", None)
        request_cls = getattr(getattr(functions, "channels", None), "GetParticipantsRequest", None)
        if search_cls is None or request_cls is None:
            raise PlatformCapabilityUnavailable("member enumeration is unavailable in this SPlusthon build")

        async def call():
            channel = await self.client.get_input_entity(group_id)
            request = request_cls(
                channel=channel,
                filter=search_cls(""),
                offset=offset,
                limit=limit,
                hash=0,
            )
            return await self.client(request)

        result = await self._rpc(group_id, "members.page", call, retry_safe=True)
        users = list(getattr(result, "users", ()) or ())
        participants = list(getattr(result, "participants", ()) or ())
        participant_by_id = {
            uid: participant
            for participant in participants
            if (uid := self._user_id(participant)) is not None
        }
        members: list[Member] = []
        for user in users:
            uid = getattr(user, "id", None)
            if uid is None:
                raise IncompleteMemberScan("member result contains a user without an ID")
            participant = participant_by_id.get(str(uid))
            admin_status = self._is_admin_participant(participant) if participant is not None else None
            rights = self._rights(participant) if participant is not None else None
            bio = None
            bio_available = False
            if include_bio:
                bio, bio_available = await self._get_bio(group_id, user)
            members.append(
                Member(
                    group_id=str(group_id),
                    user_id=str(uid),
                    display_name=self._user_display(user),
                    username=getattr(user, "username", None),
                    bio=bio,
                    bio_available=bio_available,
                    is_member=True,
                    membership_status_known=True,
                    is_admin=bool(admin_status),
                    admin_status_known=admin_status is not None,
                    is_bot=bool(getattr(user, "bot", False)),
                    is_muted=self._is_muted_rights(getattr(participant, "banned_rights", None)),
                    mute_status_known=participant is not None,
                    is_banned=self._is_banned_rights(getattr(participant, "banned_rights", None)),
                    ban_status_known=participant is not None,
                    restriction_rights=rights,
                    restriction_until=getattr(getattr(participant, "banned_rights", None), "until_date", None),
                )
            )
        total = getattr(result, "count", None)
        total = int(total) if isinstance(total, int) and total >= 0 else None
        next_offset = offset + len(users) if len(users) >= limit else None
        if total is not None and offset + len(users) >= total:
            next_offset = None
        if not users:
            next_offset = None
        return MemberPage(tuple(members), next_offset, total, raw_count=len(users))

    async def list_active_mutes_page(self, group_id: str, offset: int, limit: int) -> MemberPage:
        if limit < 1 or offset < 0:
            raise ValueError("invalid pagination")
        functions, types = self._require_tl()
        request_cls = getattr(getattr(functions, "channels", None), "GetParticipantsRequest", None)
        filter_cls = getattr(types, "ChannelParticipantsBanned", None)
        if request_cls is None or filter_cls is None:
            raise PlatformCapabilityUnavailable("restricted-member enumeration is unavailable")
        try:
            participant_filter = filter_cls(q="")
        except TypeError:
            participant_filter = filter_cls()

        async def call():
            channel = await self.client.get_input_entity(group_id)
            request = request_cls(
                channel=channel,
                filter=participant_filter,
                offset=offset,
                limit=limit,
                hash=0,
            )
            return await self.client(request)

        result = await self._rpc(group_id, "muted_members.page", call, retry_safe=True)
        users = list(getattr(result, "users", ()) or ())
        participants = list(getattr(result, "participants", ()) or ())
        by_id = {str(getattr(user, "id", "")): user for user in users if getattr(user, "id", None) is not None}
        if len(participants) != len(users):
            raise IncompleteMemberScan("restricted-member result has mismatched user/participant counts")
        members: list[Member] = []
        for participant in participants:
            uid = self._user_id(participant)
            user = by_id.get(uid or "")
            rights = getattr(participant, "banned_rights", None)
            if uid is None or user is None or rights is None:
                raise IncompleteMemberScan("restricted-member result is missing user or rights data")
            if not self._is_muted_rights(rights):
                continue
            members.append(
                Member(
                    group_id=str(group_id),
                    user_id=uid,
                    display_name=self._user_display(user),
                    username=getattr(user, "username", None),
                    is_member=not bool(getattr(participant, "left", False)),
                    membership_status_known=True,
                    is_admin=self._is_admin_participant(participant) is True,
                    admin_status_known=self._is_admin_participant(participant) is not None,
                    is_bot=bool(getattr(user, "bot", False)),
                    is_muted=True,
                    mute_status_known=True,
                    is_banned=False,
                    ban_status_known=True,
                    restriction_rights=self._rights(participant),
                    restriction_until=getattr(rights, "until_date", None),
                )
            )
        total = getattr(result, "count", None)
        total = int(total) if isinstance(total, int) and total >= 0 else None
        next_offset = offset + len(users) if len(users) >= limit else None
        if total is not None and offset + len(users) >= total:
            next_offset = None
        if not users:
            next_offset = None
        return MemberPage(tuple(members), next_offset, total, raw_count=len(users))

    async def get_member(
        self, group_id: str, user_id: str, *, include_bio: bool = False
    ) -> Member | None:
        functions, _types = self._require_tl()
        request_cls = getattr(getattr(functions, "channels", None), "GetParticipantRequest", None)
        if request_cls is None:
            raise PlatformCapabilityUnavailable("participant status lookup is unavailable")

        async def call():
            channel = await self.client.get_input_entity(group_id)
            user = await self.client.get_input_entity(user_id)
            request = request_cls(channel=channel, participant=user)
            return await self.client(request)

        try:
            result = await self._rpc(group_id, "member.status", call, retry_safe=True)
        except Exception as exc:
            error_name = type(exc).__name__.lower()
            if "notparticipant" in error_name or "usernotparticipant" in error_name:
                return None
            raise
        participant = getattr(result, "participant", None)
        users = list(getattr(result, "users", ()) or ())
        user = next(
            (candidate for candidate in users if str(getattr(candidate, "id", "")) == str(user_id)),
            None,
        )
        if participant is None or user is None:
            raise IncompleteMemberScan("participant lookup returned incomplete member data")
        rights = getattr(participant, "banned_rights", None)
        admin_status = self._is_admin_participant(participant)
        bio, bio_available = await self._get_bio(group_id, user) if include_bio else (None, False)
        return Member(
            group_id=str(group_id),
            user_id=str(user_id),
            display_name=self._user_display(user),
            username=getattr(user, "username", None),
            bio=bio,
            bio_available=bio_available,
            is_member=not bool(getattr(participant, "left", False)) and not self._is_banned_rights(rights),
            membership_status_known=True,
            is_admin=bool(admin_status),
            admin_status_known=admin_status is not None,
            is_bot=bool(getattr(user, "bot", False)),
            is_muted=self._is_muted_rights(rights),
            mute_status_known=True,
            is_banned=self._is_banned_rights(rights),
            ban_status_known=True,
            restriction_rights=self._rights(participant),
            restriction_until=getattr(rights, "until_date", None),
        )

    async def _get_bio(self, group_id: str, user: Any) -> tuple[str | None, bool]:
        functions, _ = self._require_tl()
        request_cls = getattr(getattr(functions, "users", None), "GetFullUserRequest", None)
        if request_cls is None:
            return None, False

        async def call():
            return await self.client(request_cls(user))

        try:
            result = await self._rpc(group_id, "member.bio", call, retry_safe=True)
        except Exception:  # noqa: BLE001 - unavailable or failed bio lookup means search is incomplete
            return None, False
        full_user = getattr(result, "full_user", None)
        if full_user is None or not hasattr(full_user, "about"):
            return None, False
        return str(full_user.about or ""), True

    async def send_private_message(
        self,
        origin_group_id: str | int,
        user_id: str | int,
        access_hash: int,
        text: str,
        *,
        timeout_seconds: float = 12.0,
    ) -> Any:
        """Send a DM through a verified SPlusthon InputPeerUser and the RPC queue."""
        if timeout_seconds <= 0:
            raise ValueError("private-message timeout must be positive")
        _, types = self._require_tl()
        peer_cls = getattr(types, "InputPeerUser", None)
        if peer_cls is None or not callable(getattr(self.client, "send_message", None)):
            raise PlatformCapabilityUnavailable("SPlusthon private messages are unavailable")
        try:
            peer = peer_cls(user_id=int(user_id), access_hash=int(access_hash))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("private-message peer data is invalid") from exc
        message = str(text)
        if not message or len(message) > 4096:
            raise ValueError("private-message text must contain 1 to 4096 characters")

        async def call():
            result = self.client.send_message(peer, message)
            if inspect.isawaitable(result):
                return await asyncio.wait_for(result, timeout=float(timeout_seconds))
            return result

        result = await self._rpc(str(origin_group_id), "game.private_message", call)
        if result is None or result is False:
            raise RuntimeError("SPlusthon did not confirm the private message")
        return result

    async def send_group_message(
        self,
        group_id: str | int,
        text: str,
        *,
        formatting_entities: list[Any] | None = None,
    ) -> Any:
        """Send one group text through the serial RPC queue, without retries."""
        sender = getattr(self.client, "send_message", None)
        if not callable(sender):
            raise PlatformCapabilityUnavailable("SPlusthon group text sending is unavailable")
        message = str(text)
        if not message or len(message.encode("utf-16-le")) // 2 > 4096:
            raise ValueError("group message must contain 1 to 4096 UTF-16 code units")

        group_key = str(group_id)
        try:
            target: str | int = int(group_key)
        except ValueError:
            target = group_key

        async def call():
            kwargs = {"formatting_entities": formatting_entities} if formatting_entities else {}
            result = sender(target, message, **kwargs)
            return await result if inspect.isawaitable(result) else result

        result = await self._rpc(
            str(group_id), "broadcast.send", call, retry_safe=False
        )
        if result is None or result is False or (
            isinstance(result, (int, str)) and not result
        ):
            raise RuntimeError("SPlusthon did not confirm the group message")
        return result

    async def send_photo(
        self,
        group_id: str | int,
        content: bytes,
        filename: str,
    ) -> bool:
        """Upload and send exactly one image in one queued, ordered operation."""
        data = bytes(content)
        name = Path(str(filename)).name
        extension = Path(name).suffix.lower()
        mime_by_extension = {
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
            ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
            ".avif": "image/avif",
        }
        mime_type = mime_by_extension.get(extension)
        if not data or len(data) > 8_000_000 or mime_type is None:
            raise ValueError("photo payload or image filename is invalid")
        if not callable(getattr(self.client, "send_file", None)):
            raise PlatformCapabilityUnavailable("SPlusthon file sending is unavailable")

        async def call():
            stream = _NamedBytesIO(data, name)
            return await self.client.send_file(
                str(group_id), stream, force_document=False, mime_type=mime_type
            )

        result = await self._rpc(str(group_id), "photo.send", call, retry_safe=False)
        if isinstance(result, (list, tuple)):
            return bool(result)
        return result is not None and result is not False

    async def send_photo_url(self, group_id: str | int, url: str) -> bool:
        """Send one validated HTTPS photo URL as a native photo request."""
        from urllib.parse import urlparse

        parsed = urlparse(str(url or ""))
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("photo URL must be an HTTPS URL")
        functions, types = self._require_tl()
        messages = getattr(functions, "messages", None)
        request_cls = getattr(messages, "SendMediaRequest", None)
        media_cls = getattr(types, "InputMediaPhotoExternal", None)
        if request_cls is None or media_cls is None:
            raise PlatformCapabilityUnavailable("SPlusthon external photo sending is unavailable")

        async def call():
            peer = await self.client.get_input_entity(str(group_id))
            media = media_cls(url=str(url))
            return await self.client(request_cls(peer=peer, media=media, message=""))

        result = await self._rpc(str(group_id), "photo.send_url", call, retry_safe=False)
        return result is not None and result is not False

    async def get_message(self, group_id: str | int, message_id: str | int) -> Any | None:
        message_id = int(message_id)
        if message_id <= 0:
            raise ValueError("message ID must be positive")
        async def call():
            return await self.client.get_messages(str(group_id), ids=message_id)
        message = await self._rpc(str(group_id), "message.get", call, retry_safe=True)
        if isinstance(message, (tuple, list)):
            return message[0] if message else None
        return message

    async def get_recent_messages(self, group_id: str | int, limit: int) -> list[Any]:
        if not 1 <= int(limit) <= 700:
            raise ValueError("message scan limit must be between 1 and 700")
        async def call():
            return await self.client.get_messages(str(group_id), limit=int(limit))
        result = await self._rpc(str(group_id), "messages.recent", call, retry_safe=True)
        return list(result or ())

    async def resolve_replied_user(self, group_id: str | int, event: Any) -> Any | None:
        header = getattr(event, "reply_to", None)
        reply_id = getattr(header, "reply_to_msg_id", None)
        if reply_id is None:
            message = getattr(event, "message", None)
            reply_id = getattr(message, "reply_to_msg_id", None)
            if reply_id is None:
                reply_id = getattr(getattr(message, "reply_to", None), "reply_to_msg_id", None)
        if reply_id is None:
            return None
        replied = await self.get_message(group_id, reply_id)
        if replied is None:
            return None
        sender = getattr(replied, "sender", None)
        if sender is None:
            get_sender = getattr(replied, "get_sender", None)
            if callable(get_sender):
                async def call_sender():
                    value = get_sender()
                    return await value if inspect.isawaitable(value) else value
                sender = await self._rpc(str(group_id), "message.reply_sender", call_sender, retry_safe=True)
        return sender

    async def resolve_username(self, group_id: str | int, username: str) -> Any | None:
        value = str(username or "").strip().lstrip("@")
        if not value or value.isdigit():
            return None
        async def call():
            return await self.client.get_entity(value)
        return await self._rpc(str(group_id), "user.resolve", call, retry_safe=True)

    async def change_group_title(self, group_id: str | int, title: str) -> bool:
        value = " ".join(str(title or "").split())
        if not value or len(value) > 128:
            raise ValueError("group title must contain 1 to 128 characters")
        functions, _types = self._require_tl()
        request_cls = getattr(getattr(functions, "channels", None), "EditTitleRequest", None)
        if request_cls is None:
            raise PlatformCapabilityUnavailable("group title editing is unavailable")
        async def call():
            peer = await self.client.get_input_entity(group_id)
            return await self.client(request_cls(channel=peer, title=value))
        await self._rpc(str(group_id), "group.title", call, retry_safe=False)
        async def verify():
            return await self.client.get_entity(group_id)
        chat = await self._rpc(str(group_id), "group.title.verify", verify, retry_safe=True)
        if str(getattr(chat, "title", "")).strip() != value:
            raise RuntimeError("group title change was not confirmed")
        return True

    @staticmethod
    def _copy_default_rights(rights: Any, *, text_banned: bool, restore: dict[str, bool] | None = None):
        flags = {name: bool(getattr(rights, name, False)) for name in _KNOWN_RIGHT_FLAGS}
        if restore:
            for name, value in restore.items():
                if name in flags and name not in {"send_messages", "send_plain"}:
                    flags[name] = bool(value)
        flags["send_messages"] = text_banned
        flags["send_plain"] = text_banned
        _, types = SPlusthonAdapter._require_tl()
        rights_cls = getattr(types, "ChatBannedRights", None)
        if rights_cls is None:
            raise PlatformCapabilityUnavailable("ChatBannedRights is unavailable")
        try:
            parameters = inspect.signature(rights_cls.__init__).parameters
            supported = {
                name for name, parameter in parameters.items()
                if name not in {"self", "until_date"}
                and parameter.kind not in (inspect.Parameter.VAR_KEYWORD, inspect.Parameter.VAR_POSITIONAL)
            }
        except (TypeError, ValueError):
            supported = set(_KNOWN_RIGHT_FLAGS)
        if not supported:
            supported = set(_KNOWN_RIGHT_FLAGS)
        flags = {key: value for key, value in flags.items() if key in supported}
        if "send_messages" not in flags or "send_plain" not in flags:
            raise PlatformCapabilityUnavailable("text message restriction flags are unavailable")
        return rights_cls(
            until_date=getattr(rights, "until_date", None),
            **flags,
        )

    async def _get_group_default_rights(self, group_id: str | int) -> tuple[Any, Any]:
        functions, _types = self._require_tl()
        channels = getattr(functions, "channels", None)
        request_cls = getattr(channels, "GetFullChannelRequest", None)
        if request_cls is None:
            raise PlatformCapabilityUnavailable("group rights lookup is unavailable")
        async def call():
            peer = await self.client.get_input_entity(group_id)
            result = await self.client(request_cls(channel=peer))
            chats = list(getattr(result, "chats", ()) or ())
            wanted = str(group_id)
            chat = next((item for item in chats if str(getattr(item, "id", "")) == wanted), None)
            if chat is None and chats:
                chat = chats[0]
            if chat is None:
                chat = await self.client.get_entity(group_id)
            rights = getattr(chat, "default_banned_rights", None)
            return peer, rights
        return await self._rpc(str(group_id), "group.default_rights", call, retry_safe=True)

    async def change_group_photo(self, group_id: str | int, file_path: str) -> bool:
        path = Path(file_path).expanduser().resolve(strict=True)
        if not path.is_file() or path.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
            raise ValueError("group photo must be an existing JPG or PNG file")
        if path.stat().st_size <= 0 or path.stat().st_size > 10 * 1024 * 1024:
            raise ValueError("group photo must be between 1 byte and 10 MiB")
        functions, types = self._require_tl()
        channels = getattr(functions, "channels", None)
        edit_cls = getattr(channels, "EditPhotoRequest", None)
        get_full_cls = getattr(channels, "GetFullChannelRequest", None)
        upload_photo_cls = getattr(types, "InputChatUploadedPhoto", None)
        if edit_cls is None or get_full_cls is None or upload_photo_cls is None:
            raise PlatformCapabilityUnavailable("group photo editing is unavailable")

        async def read_full_channel():
            peer = await self.client.get_input_entity(group_id)
            return await self.client(get_full_cls(channel=peer))

        def current_photo_id(result: Any) -> str | None:
            full_chat = getattr(result, "full_chat", None)
            photo = getattr(full_chat, "chat_photo", None)
            if photo is None:
                chats = list(getattr(result, "chats", ()) or ())
                photo = next(
                    (
                        getattr(chat, "photo", None)
                        for chat in chats
                        if getattr(chat, "photo", None) is not None
                    ),
                    None,
                )
            if photo is None or type(photo).__name__.lower().endswith("empty"):
                return None
            photo_id = getattr(photo, "photo_id", None)
            if photo_id is None:
                photo_id = getattr(photo, "id", None)
            return None if photo_id is None else str(photo_id)

        before = await self._rpc(
            str(group_id), "group.photo.before", read_full_channel, retry_safe=True
        )
        previous_photo_id = current_photo_id(before)

        async def call():
            peer = await self.client.get_input_entity(group_id)
            uploaded = await self.client.upload_file(str(path), file_name=path.name)
            return await self.client(
                edit_cls(channel=peer, photo=upload_photo_cls(file=uploaded))
            )

        edited = await self._rpc(str(group_id), "group.photo", call, retry_safe=False)
        if edited is None or edited is False:
            raise PlatformCapabilityUnavailable("group photo update was not acknowledged")
        result = await self._rpc(
            str(group_id), "group.photo.verify", read_full_channel, retry_safe=True
        )
        photo_id = current_photo_id(result)
        if photo_id is None or photo_id == previous_photo_id:
            raise PlatformCapabilityUnavailable("group photo change could not be verified")
        return True

    async def set_group_locked(
        self,
        group_id: str | int,
        locked: bool,
        *,
        restore_rights: dict[str, bool] | None = None,
    ) -> dict[str, bool]:
        """Change only default text-send rights, then verify server state."""
        functions, _types = self._require_tl()
        messages = getattr(functions, "messages", None)
        request_cls = getattr(messages, "EditChatDefaultBannedRightsRequest", None)
        if request_cls is None:
            raise PlatformCapabilityUnavailable("group lock/unlock is unavailable")
        peer, current = await self._get_group_default_rights(group_id)
        snapshot = {name: bool(getattr(current, name, False)) for name in _KNOWN_RIGHT_FLAGS}
        updated = self._copy_default_rights(current, text_banned=bool(locked), restore=restore_rights)
        async def call():
            return await self.client(request_cls(peer=peer, banned_rights=updated))
        await self._rpc(str(group_id), "group.lock" if locked else "group.unlock", call, retry_safe=False)
        _peer, verified = await self._get_group_default_rights(group_id)
        actual_locked = any(bool(getattr(verified, name, False)) for name in ("send_messages", "send_plain"))
        if actual_locked is not bool(locked):
            raise RuntimeError("group lock/unlock did not take effect on the platform")
        return snapshot

    async def pin_group_message(self, group_id: str | int, message_id: str | int) -> bool:
        mid = int(message_id)
        if mid <= 0:
            raise ValueError("message ID must be positive")
        async def pin():
            return await self.client.pin_message(str(group_id), mid, notify=False)
        await self._rpc(str(group_id), "message.pin", pin, retry_safe=False)
        pinned_id = await self.get_pinned_message_id(group_id)
        return pinned_id == mid

    async def get_pinned_message_id(self, group_id: str | int) -> int | None:
        functions, _types = self._require_tl()
        channels = getattr(functions, "channels", None)
        request_cls = getattr(channels, "GetFullChannelRequest", None)
        if request_cls is None:
            raise PlatformCapabilityUnavailable("pinned-message lookup is unavailable")
        async def call():
            peer = await self.client.get_input_entity(group_id)
            return await self.client(request_cls(channel=peer))
        full = await self._rpc(str(group_id), "message.pinned_id", call, retry_safe=True)
        full_chat = getattr(full, "full_chat", None)
        value = getattr(full_chat, "pinned_msg_id", None)
        return int(value) if value is not None else None

    async def get_group_member_count(self, group_id: str | int) -> int | None:
        functions, _types = self._require_tl()
        channels = getattr(functions, "channels", None)
        request_cls = getattr(channels, "GetFullChannelRequest", None)
        if request_cls is None:
            raise PlatformCapabilityUnavailable("group member count is unavailable")
        async def call():
            peer = await self.client.get_input_entity(group_id)
            return await self.client(request_cls(channel=peer))
        result = await self._rpc(str(group_id), "group.member_count", call, retry_safe=True)
        full_chat = getattr(result, "full_chat", None)
        count = getattr(full_chat, "participants_count", None)
        return int(count) if isinstance(count, int) and count >= 0 else None

    async def unban_member(self, group_id: str, user_id: str) -> ModerationResult:
        current = await self.get_member(group_id, user_id)
        if current is None:
            return ModerationResult(True, "already_released", verified=True)
        if current.group_id != str(group_id) or current.user_id != str(user_id):
            return ModerationResult(False, "member_scope_mismatch", verified=False)
        if not current.ban_status_known:
            return ModerationResult(False, "ban_state_unknown", verified=False)
        if not current.is_banned:
            return ModerationResult(True, "already_released", verified=True)
        if current.admin_status_known and current.is_admin:
            return ModerationResult(False, "protected_admin", verified=True)
        _, types = self._require_tl()
        rights_cls = getattr(types, "ChatBannedRights", None)
        if rights_cls is None:
            raise PlatformCapabilityUnavailable("ChatBannedRights is unavailable")
        flags = dict(current.restriction_rights or {})
        flags["view_messages"] = False
        try:
            parameters = inspect.signature(rights_cls.__init__).parameters
            supported = {name for name, parameter in parameters.items() if name not in {"self", "until_date"}}
        except (TypeError, ValueError):
            supported = set(_KNOWN_RIGHT_FLAGS)
        values = {name: bool(flags.get(name, False)) for name in _KNOWN_RIGHT_FLAGS if name in supported}
        rights = rights_cls(until_date=None, **values)
        await self._apply_rights(group_id, user_id, rights, "member.unban")
        verified = await self.get_member(group_id, user_id)
        if verified is None or (verified.ban_status_known and not verified.is_banned):
            return ModerationResult(True, "", verified=True)
        return ModerationResult(False, "unban_not_verified", verified=False)

    async def delete_message(self, group_id: str, message_id: str | int) -> bool:
        """Delete exactly one message from the event's own group and confirm response."""
        message_id = int(message_id)
        if message_id <= 0:
            raise ValueError("message ID must be positive")

        async def call():
            return await self.client.delete_messages(
                group_id, [message_id], revoke=True
            )

        result = await self._rpc(group_id, "message.delete", call, retry_safe=False)
        return bool(result)

    async def is_native_admin(self, group_id: str, user_id: str) -> bool:
        try:
            member = await self.get_member(group_id, user_id)
        except Exception:  # noqa: BLE001 - authorization lookup fails closed
            return False
        return bool(member and member.membership_status_known and member.admin_status_known and member.is_admin)

    def _rights_object(self, current: Member | None, *, mute: bool, until_date: datetime | None):
        _, types = self._require_tl()
        rights_cls = getattr(types, "ChatBannedRights", None)
        if rights_cls is None:
            raise PlatformCapabilityUnavailable("ChatBannedRights is unavailable")
        current_rights = (current.restriction_rights or {}) if current else {}
        try:
            parameters = inspect.signature(rights_cls.__init__).parameters
            supported = [
                name for name, parameter in parameters.items()
                if name not in {"self", "until_date"}
                and parameter.kind not in (inspect.Parameter.VAR_KEYWORD, inspect.Parameter.VAR_POSITIONAL)
            ]
        except (TypeError, ValueError):
            supported = list(_KNOWN_RIGHT_FLAGS)
        flags = {name: bool(current_rights.get(name, False)) for name in (supported or _KNOWN_RIGHT_FLAGS)}
        if mute:
            for name in _MUTE_FLAGS:
                if name in flags:
                    flags[name] = True
        else:
            for name in _MUTE_FLAGS:
                if name in flags:
                    flags[name] = False
        if "view_messages" in flags and current and current.is_banned:
            # A mute/unmute must never lift an existing ban.
            flags["view_messages"] = True
        return rights_cls(until_date=until_date, **flags)

    async def _apply_rights(self, group_id: str, user_id: str, rights: Any, operation: str) -> Any:
        functions, _ = self._require_tl()
        request_cls = getattr(getattr(functions, "channels", None), "EditBannedRequest", None)
        if request_cls is None:
            raise PlatformCapabilityUnavailable("EditBannedRequest is unavailable")

        async def call():
            channel = await self.client.get_input_entity(group_id)
            user = await self.client.get_input_entity(user_id)
            return await self.client(
                request_cls(channel=channel, participant=user, banned_rights=rights)
            )

        return await self._rpc(group_id, operation, call, retry_safe=False)

    async def mute_member(
        self, group_id: str, user_id: str, *, duration_seconds: int | None
    ) -> ModerationResult:
        current = await self.get_member(group_id, user_id)
        if current is None or not current.membership_status_known or not current.is_member:
            return ModerationResult(False, "not_member", verified=True)
        if not current.admin_status_known:
            return ModerationResult(False, "admin_state_unknown", verified=False)
        if current.is_admin:
            return ModerationResult(False, "protected_admin", verified=True)
        if not current.ban_status_known:
            return ModerationResult(False, "ban_state_unknown", verified=False)
        if current.is_banned:
            return ModerationResult(False, "already_banned", verified=True)
        if not current.mute_status_known:
            return ModerationResult(False, "mute_state_unknown", verified=False)
        if current.is_muted:
            return ModerationResult(True, "already_muted", verified=True)
        if duration_seconds is not None and duration_seconds <= 0:
            return ModerationResult(False, "invalid_duration", verified=True)
        until = (
            None if duration_seconds is None
            else datetime.now(UTC) + timedelta(seconds=duration_seconds)
        )
        rights = self._rights_object(current, mute=True, until_date=until)
        await self._apply_rights(group_id, user_id, rights, "member.mute")
        verified = await self.get_member(group_id, user_id)
        ok = bool(verified and verified.mute_status_known and verified.is_muted)
        return ModerationResult(ok, "" if ok else "mute_not_verified", verified=bool(verified and verified.mute_status_known))

    async def unmute_member(
        self,
        group_id: str,
        user_id: str,
        *,
        restore_rights: dict[str, bool] | None = None,
        restore_until: datetime | int | str | None = None,
    ) -> ModerationResult:
        current = await self.get_member(group_id, user_id)
        if current is None or not current.membership_status_known or not current.is_member:
            return ModerationResult(False, "not_member", verified=True)
        if not current.admin_status_known:
            return ModerationResult(False, "admin_state_unknown", verified=False)
        if current.is_admin:
            return ModerationResult(False, "protected_admin", verified=True)
        if not current.ban_status_known:
            return ModerationResult(False, "ban_state_unknown", verified=False)
        if current.is_banned:
            return ModerationResult(False, "banned_user_not_unmuted", verified=True)
        if not current.mute_status_known:
            return ModerationResult(False, "mute_state_unknown", verified=False)
        if not current.is_muted:
            return ModerationResult(True, "already_unmuted", verified=True)
        if restore_rights is None:
            rights = self._rights_object(current, mute=False, until_date=None)
        else:
            _, types = self._require_tl()
            rights_cls = getattr(types, "ChatBannedRights", None)
            if rights_cls is None:
                raise PlatformCapabilityUnavailable("ChatBannedRights is unavailable")
            try:
                parameters = inspect.signature(rights_cls.__init__).parameters
                supported = [
                    name for name, parameter in parameters.items()
                    if name not in {"self", "until_date"}
                    and parameter.kind not in (inspect.Parameter.VAR_KEYWORD, inspect.Parameter.VAR_POSITIONAL)
                ]
            except (TypeError, ValueError):
                supported = list(_KNOWN_RIGHT_FLAGS)
            flags = {name: bool(restore_rights.get(name, False)) for name in (supported or _KNOWN_RIGHT_FLAGS)}
            if "view_messages" in flags:
                flags["view_messages"] = False
            until = self._normalize_until(restore_until)
            rights = rights_cls(until_date=until, **flags)
        await self._apply_rights(group_id, user_id, rights, "member.unmute")
        verified = await self.get_member(group_id, user_id)
        ok = bool(verified and verified.mute_status_known and not verified.is_muted)
        return ModerationResult(ok, "" if ok else "unmute_not_verified", verified=bool(verified and verified.mute_status_known))

    async def ban_member(self, group_id: str, user_id: str) -> ModerationResult:
        """Apply and verify a permanent visibility restriction (a real ban)."""
        current = await self.get_member(group_id, user_id)
        if current is None or not current.membership_status_known or not current.is_member:
            return ModerationResult(False, "not_member", verified=True)
        if not current.admin_status_known:
            return ModerationResult(False, "admin_state_unknown", verified=False)
        if current.is_admin or current.is_bot or current.protected:
            return ModerationResult(False, "protected_member", verified=True)
        if not current.ban_status_known:
            return ModerationResult(False, "ban_state_unknown", verified=False)
        if current.is_banned:
            return ModerationResult(True, "already_banned", verified=True)
        _, types = self._require_tl()
        rights_cls = getattr(types, "ChatBannedRights", None)
        if rights_cls is None:
            raise PlatformCapabilityUnavailable("ChatBannedRights is unavailable")
        try:
            parameters = inspect.signature(rights_cls.__init__).parameters
            supported = {
                name for name, parameter in parameters.items()
                if name not in {"self", "until_date"}
                and parameter.kind not in (inspect.Parameter.VAR_KEYWORD, inspect.Parameter.VAR_POSITIONAL)
            }
        except (TypeError, ValueError):
            supported = set(_KNOWN_RIGHT_FLAGS)
        if not supported:
            supported = set(_KNOWN_RIGHT_FLAGS)
        flags = {
            name: bool((current.restriction_rights or {}).get(name, False))
            for name in _KNOWN_RIGHT_FLAGS if name in supported
        }
        if "view_messages" not in flags:
            raise PlatformCapabilityUnavailable("view_messages right is unavailable")
        flags["view_messages"] = True
        rights = rights_cls(until_date=None, **flags)
        await self._apply_rights(group_id, user_id, rights, "member.ban")
        verified = await self.get_member(group_id, user_id)
        ok = bool(verified and verified.ban_status_known and verified.is_banned)
        return ModerationResult(ok, "" if ok else "ban_not_verified", verified=bool(verified and verified.ban_status_known))

    async def kick_member(self, group_id: str, user_id: str) -> ModerationResult:
        current = await self.get_member(group_id, user_id)
        if current is None or not current.membership_status_known or not current.is_member:
            return ModerationResult(False, "not_member", verified=True)
        if not current.admin_status_known:
            return ModerationResult(False, "admin_state_unknown", verified=False)
        if current.is_admin:
            return ModerationResult(False, "protected_admin", verified=True)
        if not current.ban_status_known or current.is_banned:
            return ModerationResult(False, "ban_state_unknown_or_existing", verified=current.ban_status_known)
        _, types = self._require_tl()
        rights_cls = getattr(types, "ChatBannedRights", None)
        if rights_cls is None:
            raise PlatformCapabilityUnavailable("ChatBannedRights is unavailable")
        try:
            parameters = inspect.signature(rights_cls.__init__).parameters
            supported = [
                name for name, parameter in parameters.items()
                if name not in {"self", "until_date"}
                and parameter.kind not in (inspect.Parameter.VAR_KEYWORD, inspect.Parameter.VAR_POSITIONAL)
            ]
        except (TypeError, ValueError):
            supported = list(_KNOWN_RIGHT_FLAGS)
        names = supported or list(_KNOWN_RIGHT_FLAGS)
        if "view_messages" not in names:
            raise PlatformCapabilityUnavailable("view_messages right is unavailable")
        original = current.restriction_rights or {}
        values = {name: bool(original.get(name, False)) for name in names}
        values["view_messages"] = True
        banned = rights_cls(until_date=None, **values)
        await self._apply_rights(group_id, user_id, banned, "member.kick.remove")
        # Kick is implemented as remove-then-unban so it is not a permanent ban.
        # Restore the member's other pre-existing restrictions rather than
        # clearing unrelated rights while allowing them to rejoin.
        restored = {name: bool(original.get(name, False)) for name in names}
        restored["view_messages"] = False
        cleared = rights_cls(until_date=current.restriction_until, **restored)
        try:
            await self._apply_rights(group_id, user_id, cleared, "member.kick.allow_rejoin")
        except Exception as exc:  # noqa: BLE001 - do not report kick success if rejoin rights fail
            try:
                await self._apply_rights(
                    group_id, user_id, cleared, "member.kick.allow_rejoin.recovery"
                )
            except Exception as recovery_exc:  # noqa: BLE001 - preserve fail-closed result
                return ModerationResult(
                    False,
                    "kick_unban_failed",
                    detail=f"{type(exc).__name__}; recovery={type(recovery_exc).__name__}",
                    verified=False,
                )
        verified_member = await self.get_member(group_id, user_id)
        if verified_member is None:
            return ModerationResult(True, "", detail="not_banned", verified=True)
        if verified_member.membership_status_known and not verified_member.is_member and not verified_member.is_banned:
            return ModerationResult(True, "", detail="not_banned", verified=True)
        return ModerationResult(False, "kick_not_verified", detail="membership_or_ban_state_unknown", verified=False)
