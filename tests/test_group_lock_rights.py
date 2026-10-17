"""Offline proof that قفل/باز only ever touches the text-message rights.

Runs with no network and no Soroush session: the client and logger are fakes.

Two server behaviours are modelled:

* plain      - stores exactly the ChatBannedRights it is given.
* normalising - mirrors a real server that expands a ``send_messages`` ban into
  the granular ``send_plain`` ban. This is the behaviour that made "باز" report
  success while the group stayed silent.

    python tests/test_group_lock_rights.py
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from splusthon.tl import functions, types

import modules.group_actions as ga
from modules.group_actions import (
    BANNED_RIGHT_FLAGS,
    TEXT_MESSAGE_FLAGS,
    GroupActions,
)

CHAT_ID = -1000023164149
CHANNEL_ID = 23164149


class FakeLogger:
    def __init__(self):
        self.info = []
        self.error = []

    def log_info(self, message):
        self.info.append(message)

    def log_error(self, message):
        self.error.append(message)

    def lines(self, needle):
        return [m for m in self.info + self.error if needle in m]


class FakeClient:
    """Minimal stand-in that records the requests it is handed."""

    def __init__(self, server_rights, normalise=False):
        self.server_rights = server_rights
        self.normalise = normalise
        self.sent = []

    def _store(self, rights):
        if not self.normalise:
            self.server_rights = rights
            return
        values = {f: bool(getattr(rights, f, None)) for f in BANNED_RIGHT_FLAGS}
        if values["send_messages"]:
            # Server expands the legacy umbrella ban into the granular one.
            values["send_plain"] = True
        self.server_rights = types.ChatBannedRights(until_date=None, **values)

    async def get_input_entity(self, chat_id):
        if int(chat_id) != CHAT_ID:
            raise ValueError(f"unknown peer {chat_id}")
        return types.InputPeerChannel(channel_id=CHANNEL_ID, access_hash=12345)

    async def get_entity(self, chat_id):
        raise AssertionError(
            "get_entity() must not be the primary source (it can serve stale cache)"
        )

    async def __call__(self, request):
        self.sent.append(request)
        if isinstance(request, functions.channels.GetFullChannelRequest):
            chat = types.Channel(
                id=CHANNEL_ID,
                title="Test Group",
                photo=None,
                date=None,
                creator=False,
                left=False,
                broadcast=False,
                megagroup=True,
                default_banned_rights=self.server_rights,
            )
            return types.messages.ChatFull(
                full_chat=types.ChannelFull(
                    id=CHANNEL_ID,
                    about="",
                    read_inbox_max_id=0,
                    read_outbox_max_id=0,
                    unread_count=0,
                    chat_photo=None,
                    notify_settings=None,
                    bot_info=[],
                    pts=0,
                ),
                chats=[chat],
                users=[],
            )
        if isinstance(
            request, functions.messages.EditChatDefaultBannedRightsRequest
        ):
            self._store(request.banned_rights)
            return types.Updates(
                updates=[], users=[], chats=[], date=None, seq=0
            )
        raise AssertionError(f"unexpected request {type(request).__name__}")

    @property
    def edit_requests(self):
        return [
            r
            for r in self.sent
            if isinstance(r, functions.messages.EditChatDefaultBannedRightsRequest)
        ]


def flags_of(rights):
    if rights is None:
        return {flag: False for flag in BANNED_RIGHT_FLAGS}
    return {flag: bool(getattr(rights, flag, None)) for flag in BANNED_RIGHT_FLAGS}


def can_write_text(rights):
    """The property that actually matters to a group member."""
    return not any(flags_of(rights)[f] for f in TEXT_MESSAGE_FLAGS)


PASSED = 0
FAILED = 0


def check(label, condition, detail=""):
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  PASS  {label}")
    else:
        FAILED += 1
        print(f"  FAIL  {label} {detail}")


def assert_only_text_flags_changed(before, after):
    b, a = flags_of(before), flags_of(after)
    others = [
        f for f in BANNED_RIGHT_FLAGS if f not in TEXT_MESSAGE_FLAGS and b[f] != a[f]
    ]
    check("no non-text permission changed", not others, f"-> changed: {others}")


def scenario(name, server_rights, normalise):
    print(f"\n### {name}  (server normalise={normalise})")
    logger = FakeLogger()
    client = FakeClient(server_rights, normalise=normalise)
    actions = GroupActions(client, logger)
    before = flags_of(server_rights)

    asyncio.run(actions.lock_group(CHAT_ID))
    locked = client.server_rights
    check("lock used EditChatDefaultBannedRightsRequest", len(client.edit_requests) >= 1)
    assert_only_text_flags_changed(server_rights, locked)
    check("after lock: members CANNOT write text", not can_write_text(locked))

    asyncio.run(actions.unlock_group(CHAT_ID))
    unlocked = client.server_rights
    assert_only_text_flags_changed(locked, unlocked)
    check(
        "after unlock: members CAN write text",
        can_write_text(unlocked),
        f"-> send_messages={unlocked.send_messages} send_plain={unlocked.send_plain}",
    )

    after = flags_of(unlocked)
    drifted = [
        f
        for f in BANNED_RIGHT_FLAGS
        if f not in TEXT_MESSAGE_FLAGS and before[f] != after[f]
    ]
    check("full cycle preserves every other flag", not drifted, f"-> drifted: {drifted}")

    names = [type(r).__name__ for r in client.sent]
    check("ToggleJoinToSendRequest never used",
          not any("ToggleJoinToSend" in n for n in names))
    check("EditBannedRequest never used", not any("EditBanned" in n for n in names))

    check("UNLOCK RPC START logged", bool(logger.lines("UNLOCK RPC START")))
    check("UNLOCK RPC SUCCESS logged", bool(logger.lines("UNLOCK RPC SUCCESS")))
    check("UNLOCK RPC FAILED not logged", not logger.lines("UNLOCK RPC FAILED"))
    check("UNLOCK RPC VERIFY logged", bool(logger.lines("UNLOCK RPC VERIFY")))
    check("sent + server_now recorded in verify log",
          any("sent=[" in m and "server_now=[" in m
              for m in logger.lines("UNLOCK RPC VERIFY")))


def test_regression_send_plain():
    """The exact real-world bug: unlock must clear send_plain too."""
    print("\n### REGRESSION: server normalises send_messages -> send_plain")
    logger = FakeLogger()
    client = FakeClient(
        types.ChatBannedRights(until_date=None, send_media=True, send_gifs=True),
        normalise=True,
    )
    actions = GroupActions(client, logger)

    asyncio.run(actions.lock_group(CHAT_ID))
    locked = client.server_rights
    check("lock set send_messages", locked.send_messages is True)
    check("server also set send_plain", locked.send_plain is True)

    asyncio.run(actions.unlock_group(CHAT_ID))
    out = client.server_rights
    check("unlock cleared send_messages", not out.send_messages)
    check("unlock cleared send_plain  <-- the bug", not out.send_plain,
          "-> send_plain still banned, group stays silent")
    check("members can write text again", can_write_text(out))
    check("send_media still banned", out.send_media is True)
    check("send_gifs still banned", out.send_gifs is True)


def test_unlock_detects_ineffective_rpc():
    """If the server accepts the RPC but does not open the group, we must raise."""
    print("\n### unlock raises when the server ignores the change")

    class StubbornClient(FakeClient):
        def _store(self, rights):
            # Accept the request but keep the group locked.
            if not rights.send_messages and not rights.send_plain:
                return
            super()._store(rights)

    logger = FakeLogger()
    client = StubbornClient(
        types.ChatBannedRights(until_date=None, send_messages=True, send_plain=True)
    )
    actions = GroupActions(client, logger)

    raised = None
    try:
        asyncio.run(actions.unlock_group(CHAT_ID))
    except Exception as error:
        raised = error

    check("unlock raised instead of reporting false success", raised is not None)
    check("UNLOCK RPC FAILED logged", bool(logger.lines("UNLOCK RPC FAILED")))
    check("UNLOCK RPC INEFFECTIVE logged", bool(logger.lines("UNLOCK RPC INEFFECTIVE")))
    check("UNLOCK RPC SUCCESS NOT logged", not logger.lines("UNLOCK RPC SUCCESS"))
    if raised is not None:
        check("error names the mismatched flags",
              "send_messages" in str(raised) or "send_plain" in str(raised))
        check("error reports server state", "Server state" in str(raised))


def test_unlock_restores_snapshot():
    """A right closed before the lock must not be re-opened by unlock."""
    print("\n### unlock restores the pre-lock snapshot")

    class DriftingClient(FakeClient):
        def __init__(self, rights):
            super().__init__(rights)
            self.calls = 0

        def _store(self, rights):
            self.calls += 1
            values = {f: bool(getattr(rights, f, None)) for f in BANNED_RIGHT_FLAGS}
            # On the unlock write the server wrongly clears pin_messages.
            if self.calls == 2:
                values["pin_messages"] = False
            self.server_rights = types.ChatBannedRights(until_date=None, **values)

    logger = FakeLogger()
    client = DriftingClient(
        types.ChatBannedRights(until_date=None, pin_messages=True, send_media=True)
    )
    actions = GroupActions(client, logger)

    asyncio.run(actions.lock_group(CHAT_ID))
    asyncio.run(actions.unlock_group(CHAT_ID))
    out = client.server_rights

    check("group is open", can_write_text(out))
    check("pin_messages restored to banned", out.pin_messages is True,
          "-> unlock silently granted pin_messages")
    check("send_media untouched", out.send_media is True)
    check("UNLOCK RESTORE logged", bool(logger.lines("UNLOCK RESTORE")))


def test_no_rights_object():
    print("\n### group with default_banned_rights = None")
    logger = FakeLogger()
    client = FakeClient(None)
    actions = GroupActions(client, logger)
    asyncio.run(actions.lock_group(CHAT_ID))
    r = client.server_rights
    check("text banned after lock", not can_write_text(r))
    others = [
        f for f in BANNED_RIGHT_FLAGS if f not in TEXT_MESSAGE_FLAGS and getattr(r, f)
    ]
    check("no other flag invented", not others, f"-> {others}")

    asyncio.run(actions.unlock_group(CHAT_ID))
    check("text allowed after unlock", can_write_text(client.server_rights))


def test_clone_helper():
    print("\n### clone_banned_rights unit checks")
    src = types.ChatBannedRights(
        until_date=None, send_gifs=True, send_polls=True, pin_messages=True
    )
    out = ga.clone_banned_rights(src, send_messages=True)
    check("send_gifs preserved", out.send_gifs is True)
    check("send_polls preserved", out.send_polls is True)
    check("pin_messages preserved", out.pin_messages is True)
    check("send_messages applied", out.send_messages is True)
    check("send_media untouched", out.send_media is False)
    check("source object not mutated", src.send_messages in (None, False))

    check("text_is_banned(None) is False", ga.text_is_banned(None) is False)
    check("text_is_banned send_plain only",
          ga.text_is_banned(types.ChatBannedRights(until_date=None, send_plain=True)))
    snap = ga.rights_to_dict(src)
    check("rights_to_dict covers every flag", set(snap) == set(BANNED_RIGHT_FLAGS))
    check("rights_to_dict(None) is None", ga.rights_to_dict(None) is None)


def main():
    test_clone_helper()

    busy = dict(
        send_media=True, send_stickers=True, send_gifs=True, send_polls=True,
        embed_links=True, invite_users=True, pin_messages=True, change_info=True,
    )
    for normalise in (False, True):
        scenario(
            "group with many pre-existing restrictions",
            types.ChatBannedRights(until_date=None, **busy),
            normalise,
        )
        scenario(
            "group with zero restrictions",
            types.ChatBannedRights(until_date=None),
            normalise,
        )
        scenario(
            "group already locked before the command",
            types.ChatBannedRights(
                until_date=None, send_messages=True, send_media=True
            ),
            normalise,
        )

    test_regression_send_plain()
    test_unlock_detects_ineffective_rpc()
    test_unlock_restores_snapshot()
    test_no_rights_object()

    print(f"\n{'=' * 46}")
    print(f"passed={PASSED} failed={FAILED}")
    print("=" * 46)
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
