"""Single-forward detection must not miss Soroush/album cases. No network."""
import sys
import types
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if "splusthon" not in sys.modules:
    fake = types.ModuleType("splusthon")
    fake.Button = object
    fake.types = types.ModuleType("splusthon.types")
    tl = types.ModuleType("splusthon.tl")
    tl_types = types.ModuleType("splusthon.tl.types")

    class _Ent:
        def __init__(self, offset=0, length=0, **_kwargs):
            self.offset = offset
            self.length = length

    tl_types.MessageEntityBold = _Ent
    tl_types.MessageEntityBlockquote = _Ent
    tl.types = tl_types
    tl.functions = types.ModuleType("splusthon.tl.functions")
    fake.tl = tl
    sys.modules["splusthon"] = fake
    sys.modules["splusthon.tl"] = tl
    sys.modules["splusthon.tl.types"] = tl_types
    sys.modules["splusthon.tl.functions"] = tl.functions
    sys.modules["splusthon.types"] = fake.types

import handlers.message_handler as handler

PASSED = FAILED = 0


def check(label, cond, detail=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  PASS  {label}")
    else:
        FAILED += 1
        print(f"  FAIL  {label} {detail}")


class _Header:
    def __init__(self, from_id=None, from_name=None):
        self.from_id = from_id
        self.from_name = from_name


def test_fwd_from_header_is_detected():
    print("\n### fwd_from رسمی تشخیص داده می‌شود")
    message = SimpleNamespace(fwd_from=_Header(from_id=7), id=11)
    hit, field, _fields = handler._get_forward_metadata(message)
    check("fwd_from detected", hit is True)
    check("field is fwd_from", field == "fwd_from")


def test_empty_looking_header_is_still_a_forward():
    print("\n### هدر خالی‌نما (بدون from_id) هم فوروارد است")
    message = SimpleNamespace(fwd_from=_Header(), id=12)
    hit, field, _fields = handler._get_forward_metadata(message)
    check("empty header still True", hit is True, f"-> {hit} {field}")


def test_forward_property_without_fwd_from():
    print("\n### property forward وقتی fwd_from نیست")
    message = SimpleNamespace(fwd_from=None, forward=_Header(from_name="Ali"), id=13)
    hit, field, _fields = handler._get_forward_metadata(message)
    check("forward property detected", hit is True)
    check("field is forward", field == "forward")


def test_event_level_header_is_used():
    print("\n### هدر روی event وقتی روی message نیست")
    message = SimpleNamespace(id=14)
    event = SimpleNamespace(fwd_from=_Header(from_id=9))
    hit, field, _fields = handler._get_forward_metadata(message, event)
    check("event header detected", hit is True)
    check("field is fwd_from", field == "fwd_from")


def test_boolean_flag_true_only():
    print("\n### فلگ boolean فقط وقتی True است")
    hit_true, field, _ = handler._get_forward_metadata(
        SimpleNamespace(forwarded=True, id=15)
    )
    hit_false, _, _ = handler._get_forward_metadata(
        SimpleNamespace(forwarded=False, id=16)
    )
    check("forwarded=True", hit_true is True and field == "forwarded")
    check("forwarded=False is not forward", hit_false is False)


def test_forwards_count_is_not_a_forward():
    print("\n### شمارنده forwards فوروارد نیست")
    hit, field, _ = handler._get_forward_metadata(
        SimpleNamespace(forwards=12, views=80, id=17)
    )
    check("view-count not a forward", hit is False, f"-> {hit} {field}")


def test_plain_message_is_not_forward():
    print("\n### پیام عادی فوروارد نیست")
    hit, _, _ = handler._get_forward_metadata(
        SimpleNamespace(message="سلام", id=18)
    )
    check("plain text False", hit is False)


def test_command_text_still_detects_forward():
    print("\n### متن دستوری فوروارد بودن را پنهان نمی‌کند")
    hit, _, _ = handler._get_forward_metadata(
        SimpleNamespace(message="راهنما", fwd_from=_Header(from_id=1), id=19)
    )
    check("forwarded راهنما still detected", hit is True)


def test_album_untagged_then_tagged_collects_both():
    print("\n### آلبوم: آیتم بدون هدر بعداً با هدر پاک می‌شود")
    bot = SimpleNamespace()
    first = SimpleNamespace(id=101, grouped_id=555)
    second = SimpleNamespace(id=102, grouped_id=555, fwd_from=_Header(from_id=3))
    empty = handler._album_forward_ids(bot, -100, first, False)
    both = handler._album_forward_ids(bot, -100, second, True)
    check("first untagged yields nothing yet", empty == set(), f"-> {empty}")
    check("tagged sibling returns both ids", both == {101, 102}, f"-> {both}")


def test_album_tagged_then_untagged_still_deletes():
    print("\n### آلبوم: آیتم بعدی بدون هدر هم حذف می‌شود")
    bot = SimpleNamespace()
    first = SimpleNamespace(id=201, grouped_id=777, fwd_from=_Header(from_id=4))
    second = SimpleNamespace(id=202, grouped_id=777)
    first_ids = handler._album_forward_ids(bot, -200, first, True)
    second_ids = handler._album_forward_ids(bot, -200, second, False)
    check("first tagged deletes itself", first_ids == {201}, f"-> {first_ids}")
    check("later untagged still in wave", 202 in second_ids, f"-> {second_ids}")
    check("later wave keeps first id", 201 in second_ids, f"-> {second_ids}")


def test_single_forward_without_album_is_one_id():
    print("\n### فوروارد تکی بدون آلبوم یک id دارد")
    bot = SimpleNamespace()
    message = SimpleNamespace(id=301, grouped_id=None, fwd_from=_Header(from_id=5))
    ids = handler._album_forward_ids(bot, -300, message, True)
    check("single id", ids == {301}, f"-> {ids}")
    plain = SimpleNamespace(id=302, grouped_id=None)
    check(
        "plain single empty",
        handler._album_forward_ids(bot, -300, plain, False) == set(),
    )


if __name__ == "__main__":
    test_fwd_from_header_is_detected()
    test_empty_looking_header_is_still_a_forward()
    test_forward_property_without_fwd_from()
    test_event_level_header_is_used()
    test_boolean_flag_true_only()
    test_forwards_count_is_not_a_forward()
    test_plain_message_is_not_forward()
    test_command_text_still_detects_forward()
    test_album_untagged_then_tagged_collects_both()
    test_album_tagged_then_untagged_still_deletes()
    test_single_forward_without_album_is_one_id()
    print(f"\n{PASSED} passed, {FAILED} failed")
    raise SystemExit(1 if FAILED else 0)
