from __future__ import annotations

import asyncio
import inspect
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from splusthon import SoroushClient
from splusthon.tl import functions, types

from foxbot.platform import PlatformCapabilityUnavailable
from foxbot.rpc_queue import SerialRpcQueue
from foxbot.soroush_adapter import SPlusthonAdapter


class FakeClient:
    def __init__(self, participant=None, user=None) -> None:
        self.participant = participant
        self.user = user
        self.requests = []
        self.ban_steps: list[object] = []
        self.kicked = False

    async def get_input_entity(self, entity):
        return entity

    async def __call__(self, request):
        self.requests.append(request)
        name = type(request).__name__
        if name == "GetParticipantRequest":
            if self.kicked:
                class UserNotParticipantError(Exception):
                    pass
                raise UserNotParticipantError("not in group")
            return SimpleNamespace(participant=self.participant, users=[self.user])
        if name == "GetParticipantsRequest":
            return SimpleNamespace(
                users=[self.user],
                participants=[self.participant],
                count=1,
            )
        if name == "GetFullUserRequest":
            return SimpleNamespace(full_user=SimpleNamespace(about="bio"))
        if name == "EditBannedRequest":
            rights = request.banned_rights
            self.ban_steps.append(rights)
            if rights.view_messages:
                self.kicked = False
                self._kick_pending = True
            elif getattr(self, "_kick_pending", False):
                self.kicked = True
                self._kick_pending = False
            else:
                self.participant = types.ChannelParticipantBanned(
                    peer=types.PeerUser(user_id=self.user.id),
                    kicked_by=2,
                    date=datetime.now(UTC),
                    banned_rights=rights,
                    left=False,
                )
            return SimpleNamespace(ok=True)
        raise AssertionError(f"unexpected SPlusthon request: {name}")


def normal_participant(user_id: int = 7):
    return types.ChannelParticipant(user_id=user_id, date=datetime.now(UTC))


def make_user(user_id: int = 7):
    return SimpleNamespace(id=user_id, first_name="Test", last_name="User", username="test", bot=False)


def test_adapter_uses_installed_splusthon_tl_types_and_maps_member_page() -> None:
    async def scenario() -> None:
        participant = normal_participant()
        client = FakeClient(participant, make_user())
        queue = SerialRpcQueue()
        adapter = SPlusthonAdapter(client, queue)
        page = await adapter.list_members_page("g", 0, 10, include_bio=True)
        assert len(page.members) == 1
        member = page.members[0]
        assert member.user_id == "7"
        assert member.admin_status_known and not member.is_admin
        assert member.mute_status_known and not member.is_muted
        assert member.bio_available and member.bio == "bio"
        assert page.raw_count == 1
        assert any(isinstance(request, functions.channels.GetParticipantsRequest) for request in client.requests)
        await queue.close()

    asyncio.run(scenario())


def test_private_message_uses_authenticated_user_peer_and_serial_rpc_queue() -> None:
    async def scenario() -> None:
        calls = []

        async def send_message(peer, text):
            calls.append((peer, text))
            return SimpleNamespace(id=91)

        queue = SerialRpcQueue()
        adapter = SPlusthonAdapter(SimpleNamespace(send_message=send_message), queue)
        result = await adapter.send_private_message(
            "group-1", "42", 987654321, "نقش محرمانه"
        )
        assert result.id == 91
        assert len(calls) == 1
        peer, text = calls[0]
        assert isinstance(peer, types.InputPeerUser)
        assert peer.user_id == 42
        assert peer.access_hash == 987654321
        assert text == "نقش محرمانه"
        assert queue.metrics["completed"] == 1
        await queue.close()

    asyncio.run(scenario())


def test_adapter_lists_only_current_unexpired_mutes() -> None:
    async def scenario() -> None:
        participant = types.ChannelParticipantBanned(
            peer=types.PeerUser(user_id=7),
            kicked_by=2,
            date=datetime.now(UTC),
            banned_rights=types.ChatBannedRights(
                until_date=datetime.now(UTC) + timedelta(hours=1),
                send_messages=True,
            ),
            left=False,
        )
        client = FakeClient(participant, make_user())
        queue = SerialRpcQueue()
        adapter = SPlusthonAdapter(client, queue)
        page = await adapter.list_active_mutes_page("g", 0, 10)
        assert len(page.members) == 1
        assert page.members[0].is_muted
        assert page.members[0].admin_status_known and not page.members[0].is_admin
        await queue.close()

    asyncio.run(scenario())


def test_kick_temporarily_removes_then_restores_rights_for_rejoin() -> None:
    async def scenario() -> None:
        original_until = datetime.now(UTC) + timedelta(days=2)
        rights = types.ChatBannedRights(until_date=original_until, send_media=True)
        participant = types.ChannelParticipantBanned(
            peer=types.PeerUser(user_id=7),
            kicked_by=2,
            date=datetime.now(UTC),
            banned_rights=rights,
            left=False,
        )
        client = FakeClient(participant, make_user())
        queue = SerialRpcQueue()
        adapter = SPlusthonAdapter(client, queue)
        result = await adapter.kick_member("g", "7")
        assert result.success and result.verified
        assert len(client.ban_steps) == 2
        assert client.ban_steps[0].view_messages is True
        assert client.ban_steps[1].view_messages is False
        assert client.ban_steps[1].send_media is True
        assert client.ban_steps[1].until_date == original_until
        await queue.close()

    asyncio.run(scenario())


def test_ban_is_permanent_and_verifies_server_rights() -> None:
    async def scenario() -> None:
        class PermanentBanClient(FakeClient):
            async def __call__(self, request):
                self.requests.append(request)
                name = type(request).__name__
                if name == "GetParticipantRequest":
                    return SimpleNamespace(participant=self.participant, users=[self.user])
                if name == "EditBannedRequest":
                    self.participant = types.ChannelParticipantBanned(
                        peer=types.PeerUser(user_id=self.user.id),
                        kicked_by=2,
                        date=datetime.now(UTC),
                        banned_rights=request.banned_rights,
                        left=False,
                    )
                    return SimpleNamespace(ok=True)
                raise AssertionError(f"unexpected SPlusthon request: {name}")

        participant = normal_participant()
        client = PermanentBanClient(participant, make_user())
        queue = SerialRpcQueue()
        adapter = SPlusthonAdapter(client, queue)
        result = await adapter.ban_member("g", "7")
        assert result.success and result.verified
        edit = next(request for request in client.requests if isinstance(request, functions.channels.EditBannedRequest))
        assert edit.banned_rights.view_messages is True
        assert edit.banned_rights.until_date is None
        await queue.close()

    asyncio.run(scenario())


def test_photo_upload_and_group_update_are_verified(tmp_path) -> None:
    async def scenario() -> None:
        class PhotoClient(FakeClient):
            def __init__(self):
                super().__init__()
                self.photo = types.ChatPhoto(photo_id=99, dc_id=1)
                self.uploaded_path = None
                self.change_photo_on_edit = True

            async def upload_file(self, path, *, file_name=None):
                self.uploaded_path = (path, file_name)
                return types.InputFile(id=123, parts=1, name=file_name, md5_checksum="")

            async def __call__(self, request):
                self.requests.append(request)
                name = type(request).__name__
                if name == "EditPhotoRequest":
                    if self.change_photo_on_edit:
                        self.photo = types.ChatPhoto(photo_id=100, dc_id=1)
                    return SimpleNamespace(ok=True)
                if name == "GetFullChannelRequest":
                    return SimpleNamespace(full_chat=SimpleNamespace(chat_photo=self.photo), chats=[])
                raise AssertionError(f"unexpected SPlusthon request: {name}")

        photo_path = tmp_path / "group.png"
        photo_path.write_bytes(b"image-data")
        client = PhotoClient()
        queue = SerialRpcQueue()
        adapter = SPlusthonAdapter(client, queue)
        assert await adapter.change_group_photo("g", str(photo_path)) is True
        assert client.uploaded_path == (str(photo_path.resolve()), "group.png")
        assert any(isinstance(request, functions.channels.EditPhotoRequest) for request in client.requests)
        await queue.close()

        unchanged_client = PhotoClient()
        unchanged_client.change_photo_on_edit = False
        unchanged_queue = SerialRpcQueue()
        unchanged_adapter = SPlusthonAdapter(unchanged_client, unchanged_queue)
        with pytest.raises(PlatformCapabilityUnavailable, match="could not be verified"):
            await unchanged_adapter.change_group_photo("g", str(photo_path))
        await unchanged_queue.close()

    asyncio.run(scenario())


def test_unmute_can_restore_known_pre_mute_rights() -> None:
    async def scenario() -> None:
        participant = types.ChannelParticipantBanned(
            peer=types.PeerUser(user_id=7),
            kicked_by=2,
            date=datetime.now(UTC),
            banned_rights=types.ChatBannedRights(
                until_date=datetime.now(UTC) + timedelta(hours=1),
                send_messages=True,
                send_media=True,
                send_plain=True,
            ),
            left=False,
        )
        client = FakeClient(participant, make_user())
        queue = SerialRpcQueue()
        adapter = SPlusthonAdapter(client, queue)
        # GetParticipantRequest returns participant object once before and once after edit.
        result = await adapter.unmute_member(
            "g",
            "7",
            restore_rights={"send_messages": False, "send_media": True, "send_plain": False},
            restore_until=datetime.now(UTC) + timedelta(days=1),
        )
        assert result.success and result.verified
        edit = next(request for request in client.requests if isinstance(request, functions.channels.EditBannedRequest))
        assert edit.banned_rights.send_messages is False
        assert edit.banned_rights.send_media is True
        assert edit.banned_rights.send_plain is False
        assert edit.banned_rights.view_messages is False
        await queue.close()

    asyncio.run(scenario())


def test_group_message_delivery_uses_one_serial_non_retrying_rpc() -> None:
    async def scenario() -> None:
        class MessageClient:
            def __init__(self, *, result: Any = None) -> None:
                self.result = result
                self.calls: list[tuple[str | int, str, dict[str, Any]]] = []
                self.in_flight = 0
                self.max_in_flight = 0

            async def send_message(self, group_id: str | int, text: str, **kwargs: Any) -> Any:
                self.in_flight += 1
                self.max_in_flight = max(self.max_in_flight, self.in_flight)
                self.calls.append((group_id, text, kwargs))
                await asyncio.sleep(0)
                self.in_flight -= 1
                return self.result

        result = SimpleNamespace(id=91)
        client = MessageClient(result=result)
        queue = SerialRpcQueue(min_interval_seconds=0)
        adapter = SPlusthonAdapter(client, queue)
        entity = types.MessageEntityBold(offset=0, length=4)
        sent = await asyncio.gather(
            adapter.send_group_message(
                "g1", "news", formatting_entities=[entity]
            ),
            adapter.send_group_message("g2", "news"),
        )
        assert sent == [result, result]
        assert [call[0] for call in client.calls] == ["g1", "g2"]
        assert client.calls[0][2] == {"formatting_entities": [entity]}
        assert client.calls[1][2] == {}
        assert client.max_in_flight == 1
        await adapter.send_group_message("-10012345", "numeric chat")
        assert client.calls[2][0] == -10012345
        await queue.close()

        missing_result_client = MessageClient(result=None)
        missing_result_queue = SerialRpcQueue(min_interval_seconds=0)
        missing_result_adapter = SPlusthonAdapter(
            missing_result_client, missing_result_queue
        )
        with pytest.raises(RuntimeError, match="did not confirm"):
            await missing_result_adapter.send_group_message("g1", "news")
        assert len(missing_result_client.calls) == 1
        await missing_result_queue.close()

    asyncio.run(scenario())


def test_installed_splusthon_group_send_supports_formatting_entities() -> None:
    parameters = inspect.signature(SoroushClient.send_message).parameters
    assert "entity" in parameters
    assert "message" in parameters
    assert "formatting_entities" in parameters
    assert parameters["formatting_entities"].kind is inspect.Parameter.KEYWORD_ONLY
