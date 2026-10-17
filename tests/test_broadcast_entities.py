"""Offline proof that اطلاع رسانی preserves Soroush Plus formatting.

No network and no session: the client, event and logger are fakes. The whole
three-step workflow (اطلاع رسانی -> body -> تایید) is driven end to end and the
entities that reach each group are compared against the ones the owner sent.

    python tests/test_broadcast_entities.py
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from splusthon.tl.types import (
    MessageEntityBlockquote,
    MessageEntityBold,
    MessageEntityItalic,
    MessageEntityTextUrl,
)

import handlers.broadcast_handler as bh
import modules.broadcast_state as state

OWNER_ID = 68074059
GROUPS = [-1000023164149, -1000023093376]

PASSED = FAILED = 0


def check(label, cond, detail=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  PASS  {label}")
    else:
        FAILED += 1
        print(f"  FAIL  {label} {detail}")


def u16(value):
    return len((value or "").encode("utf-16-le")) // 2


class FakeLogger:
    def __init__(self):
        self.info = []
        self.error = []

    def log_info(self, m):
        self.info.append(m)

    def log_error(self, m):
        self.error.append(m)

    def lines(self, needle):
        return [m for m in self.info + self.error if needle in m]


class Sent:
    def __init__(self, target, text, entities):
        self.target = target
        self.text = text
        self.entities = list(entities) if entities else []


class FakeClient:
    def __init__(self):
        self.sent = []

    async def send_message(self, entity, message, formatting_entities=None, **kw):
        self.sent.append(Sent(entity, message, formatting_entities))
        return None

    def iter_dialogs(self):
        async def gen():
            for _ in ():
                yield _
        return gen()


class FakeMessage:
    def __init__(self, text, entities):
        self.message = text
        self.entities = list(entities) if entities else []
        self.id = 1


class FakeEvent:
    def __init__(self, text, entities=None):
        self.message = FakeMessage(text, entities)
        self.replies = []

    async def reply(self, text, formatting_entities=None, **kw):
        self.replies.append(Sent("owner", text, formatting_entities))
        return None


class FakeBot:
    def __init__(self):
        self.logger = FakeLogger()
        self.client = FakeClient()


def run_workflow(body_text, body_entities):
    """Drive اطلاع رسانی -> body -> تایید and return (bot, preview_event)."""
    state.clear(OWNER_ID)
    bot = FakeBot()

    e1 = FakeEvent("اطلاع رسانی")
    asyncio.run(bh.handle_private_broadcast(bot, e1, OWNER_ID, "اطلاع رسانی"))

    e2 = FakeEvent(body_text, body_entities)
    asyncio.run(bh.handle_private_broadcast(bot, e2, OWNER_ID, body_text))

    e3 = FakeEvent("تایید")
    asyncio.run(bh.handle_private_broadcast(bot, e3, OWNER_ID, "تایید"))
    return bot, e2


def decode(text, entity):
    data = text.encode("utf-16-le")
    return data[entity.offset * 2:(entity.offset + entity.length) * 2].decode("utf-16-le")


# --------------------------------------------------------------------------
def test_plain_text():
    print("\n### plain text announcement")
    bot, _ = run_workflow("سلام به همه", [])
    sends = bot.client.sent
    check("delivered to every active group", len(sends) == len(GROUPS),
          f"-> {len(sends)}")
    check("text intact", all(s.text == "سلام به همه" for s in sends))
    check("no entities invented", all(not s.entities for s in sends))


def test_bold():
    print("\n### bold announcement")
    body = "توجه مهم\nاین یک اطلاعیه است"
    ents = [MessageEntityBold(offset=0, length=u16("توجه مهم"))]
    bot, _ = run_workflow(body, ents)
    sends = bot.client.sent
    check("delivered to every group", len(sends) == len(GROUPS))
    check("bold survived", all(len(s.entities) == 1 for s in sends),
          f"-> {[len(s.entities) for s in sends]}")
    check("entity type preserved",
          all(isinstance(s.entities[0], MessageEntityBold) for s in sends))
    check("bold covers the right words",
          all(decode(s.text, s.entities[0]) == "توجه مهم" for s in sends),
          f"-> {[decode(s.text, s.entities[0]) for s in sends if s.entities]}")


def test_blockquote():
    print("\n### blockquote announcement")
    body = "اطلاعیه\nمتن داخل نقل قول"
    quote = "متن داخل نقل قول"
    ents = [
        MessageEntityBold(offset=0, length=u16("اطلاعیه")),
        MessageEntityBlockquote(offset=u16("اطلاعیه\n"), length=u16(quote)),
    ]
    bot, _ = run_workflow(body, ents)
    sends = bot.client.sent
    check("both entities survived", all(len(s.entities) == 2 for s in sends),
          f"-> {[len(s.entities) for s in sends]}")
    for s in sends:
        kinds = {type(e).__name__ for e in s.entities}
        check("bold + blockquote present in group message",
              kinds == {"MessageEntityBold", "MessageEntityBlockquote"}, f"-> {kinds}")
        bq = next(e for e in s.entities if isinstance(e, MessageEntityBlockquote))
        check("blockquote maps to the quoted text", decode(s.text, bq) == quote,
              f"-> {decode(s.text, bq)!r}")
        break


def test_mixed_entities():
    print("\n### mixed entities (bold + italic + link + quote)")
    body = "عنوان پررنگ\nمتن کج\nلینک سایت\nنقل قول پایانی"
    ents = [
        MessageEntityBold(offset=0, length=u16("عنوان پررنگ")),
        MessageEntityItalic(offset=u16("عنوان پررنگ\n"), length=u16("متن کج")),
        MessageEntityTextUrl(
            offset=u16("عنوان پررنگ\nمتن کج\n"),
            length=u16("لینک سایت"),
            url="https://example.com",
        ),
        MessageEntityBlockquote(
            offset=u16("عنوان پررنگ\nمتن کج\nلینک سایت\n"),
            length=u16("نقل قول پایانی"),
        ),
    ]
    bot, _ = run_workflow(body, ents)
    s = bot.client.sent[0]
    check("all four entities delivered", len(s.entities) == 4, f"-> {len(s.entities)}")
    check("order preserved",
          [type(e).__name__ for e in s.entities]
          == [type(e).__name__ for e in ents])
    for original in ents:
        match = next(
            (e for e in s.entities
             if type(e) is type(original) and e.offset == original.offset), None)
        check(f"{type(original).__name__.replace('MessageEntity','')} intact",
              match is not None and match.length == original.length)
    url = next(e for e in s.entities if isinstance(e, MessageEntityTextUrl))
    check("TextUrl keeps its url attribute", url.url == "https://example.com",
          f"-> {getattr(url, 'url', None)}")


def test_preview_offsets():
    print("\n### preview re-aligns offsets (header shift)")
    body = "عنوان\nبدنه پیام"
    ents = [MessageEntityBold(offset=0, length=u16("عنوان"))]
    state.clear(OWNER_ID)
    bot = FakeBot()
    asyncio.run(bh.handle_private_broadcast(
        bot, FakeEvent("اطلاع رسانی"), OWNER_ID, "اطلاع رسانی"))
    e2 = FakeEvent(body, ents)
    asyncio.run(bh.handle_private_broadcast(bot, e2, OWNER_ID, body))

    preview = e2.replies[-1]
    check("preview carries entities", bool(preview.entities))
    shift = u16(bh.PREVIEW_HEADER)
    check("offset shifted by header length",
          preview.entities[0].offset == shift,
          f"-> {preview.entities[0].offset} vs {shift}")
    check("preview bold still points at the title",
          decode(preview.text, preview.entities[0]) == "عنوان",
          f"-> {decode(preview.text, preview.entities[0])!r}")
    check("original entity object not mutated", ents[0].offset == 0,
          f"-> {ents[0].offset}")
    state.clear(OWNER_ID)


def test_state_roundtrip():
    print("\n### state stores and returns entities")
    ents = [MessageEntityBold(offset=0, length=5)]
    state.begin(OWNER_ID)
    state.set_message(OWNER_ID, "hello world", ents)
    stored = state.get(OWNER_ID)
    check("entities stored", len(stored["entities"]) == 1)
    text, out = state.consume_confirmation(OWNER_ID)
    check("text returned", text == "hello world")
    check("entities returned", len(out) == 1 and isinstance(out[0], MessageEntityBold))
    check("state destroyed after consume", state.get(OWNER_ID) is None)
    t2, e2 = state.consume_confirmation(OWNER_ID)
    check("second consume yields (None, [])", t2 is None and e2 == [])


def test_body_not_stripped():
    print("\n### body is stored unstripped (offsets stay aligned)")
    body = "  عنوان با فاصله"
    ents = [MessageEntityBold(offset=2, length=u16("عنوان با فاصله"))]
    bot, _ = run_workflow(body, ents)
    s = bot.client.sent[0]
    check("leading whitespace kept", s.text == body, f"-> {s.text!r}")
    check("bold still maps correctly",
          decode(s.text, s.entities[0]) == "عنوان با فاصله",
          f"-> {decode(s.text, s.entities[0])!r}")


def test_logging():
    print("\n### diagnostic logging")
    body = "عنوان\nمتن"
    ents = [
        MessageEntityBold(offset=0, length=u16("عنوان")),
        MessageEntityBlockquote(offset=u16("عنوان\n"), length=u16("متن")),
    ]
    bot, _ = run_workflow(body, ents)
    for needle in ("BROADCAST ROUTE ENTER HANDLER", "BROADCAST START",
                   "BROADCAST STATE CREATE", "BROADCAST MESSAGE RECEIVED",
                   "BROADCAST MESSAGE STORED", "PREVIEW CREATED",
                   "BROADCAST CONFIRM", "BROADCAST STARTED",
                   "BROADCAST SEND START", "BROADCAST SEND TARGETS",
                   "BROADCAST GROUP SENT", "BROADCAST SEND RESULT",
                   "BROADCAST GROUP SUMMARY", "BROADCAST FINISHED"):
        check(f"logged: {needle}", bool(bot.logger.lines(needle)))
    check("entity count logged", any("entity_count=2" in m for m in bot.logger.info))
    check("entity types logged",
          any("Bold@" in m and "Blockquote@" in m for m in bot.logger.info))


def test_cancel():
    print("\n### cancel discards the stored announcement")
    state.clear(OWNER_ID)
    bot = FakeBot()
    asyncio.run(bh.handle_private_broadcast(
        bot, FakeEvent("اطلاع رسانی"), OWNER_ID, "اطلاع رسانی"))
    asyncio.run(bh.handle_private_broadcast(
        bot, FakeEvent("متن", [MessageEntityBold(offset=0, length=3)]),
        OWNER_ID, "متن"))
    asyncio.run(bh.handle_private_broadcast(bot, FakeEvent("لغو"), OWNER_ID, "لغو"))
    check("nothing sent to groups", not bot.client.sent)
    check("state cleared", state.get(OWNER_ID) is None)


def test_non_broadcast_passthrough():
    print("\n### unrelated private text is not captured")
    state.clear(OWNER_ID)
    bot = FakeBot()
    handled = asyncio.run(
        bh.handle_private_broadcast(bot, FakeEvent("سلام"), OWNER_ID, "سلام"))
    check("returns False so other handlers run", handled is False)


def main():
    # is_active/load_groups are patched so the test never touches config files.
    bh.is_active = lambda gid: True
    bh.load_groups = lambda: list(GROUPS)

    test_state_roundtrip()
    test_plain_text()
    test_bold()
    test_blockquote()
    test_mixed_entities()
    test_preview_offsets()
    test_body_not_stripped()
    test_logging()
    test_cancel()
    test_non_broadcast_passthrough()

    print(f"\n{'=' * 50}")
    print(f"passed={PASSED} failed={FAILED}")
    print("=" * 50)
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
