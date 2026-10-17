"""Offline proof that «ثبت ادمین» and «ثبت اسم» route to different handlers.

No network and no session: only the pure routing helpers are exercised, plus a
regression guard that the old startswith("ثبت ") logic is gone.

    python tests/test_command_routing.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from handlers.message_handler import (
    ADMIN_OBJECT_WORDS,
    MEMORY_REGISTER_PREFIXES,
    RESERVED_COMMANDS,
    match_reserved_command,
    normalize_command,
    resolve_registration_prefix,
)

PASSED = FAILED = 0


def check(label, cond, detail=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  PASS  {label}")
    else:
        FAILED += 1
        print(f"  FAIL  {label} {detail}")


def route(text):
    """Return the handler a message would reach: 'memory' or a reserved name."""
    command, handler = match_reserved_command(text)
    if command is not None:
        return handler
    if resolve_registration_prefix(text) is not None:
        return "group_memory.set_name"
    return "<falls through>"


# --------------------------------------------------------------------------
def test_reported_admin_commands():
    """هر دستور مدیریتی «ثبت …» باید به handler خودش برود، نه ثبت اسم."""
    print("\n### دستورهای مدیریتی گزارش‌شده")
    expected = {
        "ثبت مالک": "group_owner.set",
        "ثبت گروه": "group_registration",
        "ثبت ادمین": "admin_registration",
        "ثبت قوانین": "group_rules.set",
        "ثبت اصل": "user_original.set",
        "لغو مالک": "group_owner.remove",
        "لغو ادمین": "admin_removal",
        "برکناری ادمین": "admin_removal",
        "حذف گروه": "group_removal",
        "لیست ادمین": "admin_list",
    }
    for text, handler in expected.items():
        check(f"{text!r} -> {handler}", route(text) == handler, f"-> {route(text)}")
        check(f"{text!r} never enters name validation",
              resolve_registration_prefix(text) is None,
              f"-> prefix={resolve_registration_prefix(text)!r}")


def test_reported_name_registrations():
    """فقط «ثبت <اسم>» و «ثبت اسم <اسم>» وارد حافظهٔ گروه می‌شوند."""
    print("\n### ثبت اسم‌های گزارش‌شده")
    for text in ("ثبت کیانا", "ثبت اسم کیانا", "ثبت علی", "ثبت اسم علی"):
        check(f"{text!r} -> group_memory.set_name",
              route(text) == "group_memory.set_name", f"-> {route(text)}")


def test_structural_guard():
    """«ثبت <واژهٔ مدیریتی>» حتی بدون ثبت در جدول هم به اسم نمی‌رود."""
    print("\n### نگهبان ساختاری ADMIN_OBJECT_WORDS")
    for word in ("مدیر", "کاربر", "عضو", "ربات", "کانال", "فیلتر", "لیست"):
        text = f"ثبت {word}"
        check(f"{text!r} به ثبت اسم نمی‌رود",
              resolve_registration_prefix(text) is None,
              f"-> prefix={resolve_registration_prefix(text)!r}")
    check("همهٔ دستورهای رزروشدهٔ «ثبت …» واژهٔ دومشان پوشش دارد",
          all(c.split()[1] in ADMIN_OBJECT_WORDS
              for c, _ in RESERVED_COMMANDS
              if c.startswith("ثبت ") and len(c.split()) == 2),
          "-> یک دستور جا افتاده")


def test_required_cases():
    print("\n### the three cases from the bug report")
    check("«ثبت ادمین» -> admin_registration",
          route("ثبت ادمین") == "admin_registration",
          f"-> {route('ثبت ادمین')}")
    check("«ثبت اسم علی» -> group_memory.set_name",
          route("ثبت اسم علی") == "group_memory.set_name",
          f"-> {route('ثبت اسم علی')}")
    check("«ثبت اصل» -> user_original.set",
          route("ثبت اصل") == "user_original.set",
          f"-> {route('ثبت اصل')}")


def test_admin_commands_never_reach_memory():
    print("\n### admin commands must never enter the memory handler")
    for text in ("ثبت ادمین", "ثبت ادمین @ali", "ثبت ادمین علی",
                 "لغو ادمین", "لغو ادمین @ali",
                 "برکناری ادمین", "برکناری ادمین @ali"):
        check(f"{text!r} not swallowed by memory",
              resolve_registration_prefix(text) is None,
              f"-> prefix={resolve_registration_prefix(text)!r}")


def test_memory_commands_still_work():
    print("\n### memory registration still works")
    for text, expected_prefix in (
        ("ثبت اسم علی", "ثبت اسم "),
        ("ثبت اسم محمد رضا", "ثبت اسم "),
        ("ثبت علی", "ثبت "),
        ("ثبت مهدی", "ثبت "),
    ):
        check(f"{text!r} -> prefix {expected_prefix!r}",
              resolve_registration_prefix(text) == expected_prefix,
              f"-> {resolve_registration_prefix(text)!r}")
        check(f"{text!r} routes to memory",
              route(text) == "group_memory.set_name")


def test_other_reserved_commands():
    print("\n### every reserved command routes to its own handler")
    for command, handler in RESERVED_COMMANDS:
        check(f"{command!r} -> {handler}", route(command) == handler,
              f"-> {route(command)}")


def test_bare_and_incomplete():
    print("\n### bare / incomplete input")
    check("«ثبت» alone is not a memory registration",
          resolve_registration_prefix("ثبت") is None)
    check("«ثبت » with no name is not a registration",
          resolve_registration_prefix("ثبت ") is None)
    check("«ثبت اسم» with no name is not a registration",
          resolve_registration_prefix("ثبت اسم") is None,
          f"-> {resolve_registration_prefix('ثبت اسم')!r}")
    check("empty text is safe", resolve_registration_prefix("") is None)
    check("None is safe", resolve_registration_prefix(None) is None)


def test_no_false_positives():
    print("\n### near-miss words must NOT match reserved commands")
    for text in ("ثبت ادمینها", "ثبت ادمینی", "ثبت اصلی", "قوانینی"):
        command, _ = match_reserved_command(text)
        check(f"{text!r} does not match a reserved command", command is None,
              f"-> matched {command!r}")
    # ...and the structural guard keeps admin-object plurals out of name
    # registration too: «ادمینها» is never a person's name.
    check("«ثبت ادمینها» does not become a stored name",
          route("ثبت ادمینها") != "group_memory.set_name",
          f"-> {route('ثبت ادمینها')}")
    # A real name that merely *starts* like an admin word is still a name.
    check("«ثبت مالکه» (a real name) still registers",
          resolve_registration_prefix("ثبت مالکه") == "ثبت ",
          f"-> {resolve_registration_prefix('ثبت مالکه')!r}")


def test_normalization():
    print("\n### normalization: spacing, ZWNJ, Arabic letters")
    check("double space collapses",
          normalize_command("ثبت  ادمین") == "ثبت ادمین")
    check("leading/trailing space stripped",
          normalize_command("  ثبت ادمین  ") == "ثبت ادمین")
    check("ZWNJ becomes a space",
          normalize_command("ثبت\u200cادمین") == "ثبت ادمین")
    check("Arabic yeh normalised",
          normalize_command("ثبت ادمين") == "ثبت ادمین")
    for variant in ("ثبت  ادمین", "  ثبت ادمین ", "ثبت\u200cادمین", "ثبت ادمين"):
        check(f"variant {variant!r} routes to admin",
              route(variant) == "admin_registration", f"-> {route(variant)}")


def test_ordering_longest_first():
    print("\n### routing order: specific before generic")
    names = [c for c, _ in RESERVED_COMMANDS]
    check("«ثبت ادمین» listed before generic memory prefixes",
          "ثبت ادمین" in names)
    idx_specific = names.index("ثبت ادمین")
    for generic in ("قوانین", "اصلم"):
        if generic in names:
            check(f"«ثبت ادمین» checked before «{generic}»",
                  idx_specific < names.index(generic))
    check("«ثبت اسم » prefix is tried before «ثبت »",
          MEMORY_REGISTER_PREFIXES.index("ثبت اسم ")
          < MEMORY_REGISTER_PREFIXES.index("ثبت "))


def test_no_generic_startswith_remains():
    print("\n### regression: the old generic startswith is gone")
    src = (ROOT / "handlers" / "message_handler.py").read_text(encoding="utf-8")
    # Comment lines are allowed to mention the old pattern; only real code counts.
    code = "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("#")
    )
    bad = re.findall(r'startswith\(\s*"ثبت\s', code)
    check("no startswith(\"ثبت \") in handler code", not bad, f"-> {bad}")
    bad2 = re.findall(r'startswith\(\("برکناری ادمین"', src)
    check("no startswith((\"برکناری ادمین\"...)", not bad2, f"-> {bad2}")
    check("admin handler uses exact reserved match",
          '_reserved_command == "ثبت ادمین"' in src)


def main():
    test_reported_admin_commands()
    test_reported_name_registrations()
    test_structural_guard()
    test_required_cases()
    test_admin_commands_never_reach_memory()
    test_memory_commands_still_work()
    test_other_reserved_commands()
    test_bare_and_incomplete()
    test_no_false_positives()
    test_normalization()
    test_ordering_longest_first()
    test_no_generic_startswith_remains()

    print(f"\n{'=' * 52}")
    print(f"passed={PASSED} failed={FAILED}")
    print("=" * 52)
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
