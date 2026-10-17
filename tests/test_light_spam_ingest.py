"""Light-path ingest before GroupDispatcher. No network."""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import message_tracker
from modules.group_dispatch import PRIORITY_ADMIN, PRIORITY_NORMAL, GroupDispatcher
from modules.light_spam_ingest import incident_key, ingest, ingest_event

PASSED = FAILED = 0


def check(label, cond, detail=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  PASS  {label}")
    else:
        FAILED += 1
        print(f"  FAIL  {label} {detail}")


def _bot():
    bot = SimpleNamespace(
        _big_spam_incidents={},
        punished_users=set(),
        bot_account_id=999999,
        native_group_admin_cache={},
        started=[],
    )

    def start(event, chat_id, user_id, _sender, ids, reason):
        key = incident_key(chat_id, user_id)
        incident = bot._big_spam_incidents.setdefault(key, {"ids": set()})
        incident["ids"].update(
            message_id for message_id in (ids or ())
            if isinstance(message_id, int) and message_id > 0
        )
        incident["ids"].update(message_tracker.spam_snapshot(chat_id, user_id))
        bot.started.append((chat_id, user_id, reason))
        return True

    bot._queue_big_spam_ban = start
    bot._light_admin_bypass = lambda _chat, _user: False
    return bot


def _event(chat_id, user_id, message_id, text, is_private=False):
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("light path must not await get_sender/get_chat")

    return SimpleNamespace(
        chat_id=chat_id,
        sender_id=user_id,
        is_private=is_private,
        message=SimpleNamespace(id=message_id, message=text, caption=None),
        get_sender=forbidden,
        get_chat=forbidden,
    )


def test_hundred_separate_promo_not_lost_to_overflow():
    print("\\n### ۱۰۰ پیام تبلیغاتی جدا: قبل از detector گم نشوند")
    message_tracker.reset_all()
    bot = _bot()
    overflowed = []
    submitted = 0
    skipped = 0
    detected_at = None
    chat_id, user_id = -4101, 77
    variants = ("بیوچک🥺", "بیوچککک🧸", "بیوچکک🥲", "بیوچک🐥")
    pending_normal = 0
    max_pending_normal = 40

    for index in range(1, 101):
        text = variants[(index - 1) % 4]
        result = ingest(bot, chat_id, user_id, index, text)
        if result.detected and detected_at is None:
            detected_at = index
        if result.skip_heavy:
            skipped += 1
            continue
        if pending_normal >= max_pending_normal:
            overflowed.append(index)
            continue
        pending_normal += 1
        submitted += 1

    ids = set(message_tracker.spam_snapshot(chat_id, user_id))
    incident = bot._big_spam_incidents.get(incident_key(chat_id, user_id), {})
    cleanup_ids = set(incident.get("ids", ()))
    check("tracker هر ۱۰۰ id را دارد", ids == set(range(1, 101)), f"-> {len(ids)}")
    check("هیچ id قبل از detector در overflow گم نشد", overflowed == [], f"-> {overflowed}")
    check("تشخیص حداکثر روی پیام ۴ است", detected_at is not None and detected_at <= 4,
          f"-> {detected_at}")
    check("cleanup به همه ۱۰۰ id دسترسی دارد", cleanup_ids == set(range(1, 101)),
          f"-> missing={sorted(set(range(1, 101)) - cleanup_ids)}")
    check("بعد از incident شغل سنگین ساخته نشد", skipped >= 96, f"-> skipped={skipped}")
    check("صف سنگین زیر سقف ۴۰ ماند", submitted <= 40, f"-> submitted={submitted}")
    message_tracker.reset_all()


def test_packed_box_is_one_message_id():
    print("\\n### یک کادر با ۶۰ تکرار: فقط یک message_id")
    message_tracker.reset_all()
    bot = _bot()
    packed = "".join("بیوچک🥺بیوچککک🧸" for _ in range(30))
    result = ingest(bot, -4102, 8, 501, packed)
    rows = message_tracker.get_user_recent_messages(-4102, 8)
    ids = set(message_tracker.spam_snapshot(-4102, 8))
    incident = bot._big_spam_incidents.get(incident_key(-4102, 8), {})
    check("تشخیص intra-message", result.detected, f"-> {result.reason}")
    check("شغل سنگین لازم نیست", result.skip_heavy)
    check("فقط یک ردیف tracker", len(rows) == 1, f"-> {len(rows)}")
    check("فقط همان message_id", ids == {501}, f"-> {ids}")
    check("cleanup همان یک id را دارد", set(incident.get("ids", ())) == {501},
          f"-> {incident.get('ids')}")
    check("reason جعبهٔ فشرده است", result.reason == "repeated_promotional_phrase",
          f"-> {result.reason}")
    message_tracker.reset_all()


def test_fast_ordinary_messages_are_not_spam():
    print("\\n### پیام عادی سریع false positive ندارد")
    message_tracker.reset_all()
    bot = _bot()
    words = ("سلام", "خوبی", "امروز", "ممنون")
    detected = []
    for index in range(1, 41):
        text = words[(index - 1) % 4]
        result = ingest(bot, -4103, 3, index, text)
        if result.detected:
            detected.append((index, text, result.reason))
    check("ده پیام سریع از همان کاربر flood است",
          detected and detected[0][0] == 10 and detected[0][2] == "rapid_message_flood",
          f"-> {detected[:2]}")
    check("incident ساخته شد", incident_key(-4103, 3) in bot._big_spam_incidents)
    check("tracker همه را ثبت کرد",
          set(message_tracker.spam_snapshot(-4103, 3)) == set(range(1, 41)))
    for word in words:
        result = ingest(bot, -4104, 4, 1, word * 1)
        check(f"تک‌پیام {word!r} اسپم نیست", not result.detected)
    message_tracker.reset_all()
    for index in range(1, 6):
        result = ingest(bot, -4105, 5, index, "سلام")
        check(f"سلام #{index} اسپم نیست", not result.detected)
    message_tracker.reset_all()


def test_stress_spam_does_not_freeze_admin_or_other_group():
    print("\\n### فشار: موج اسپم + ادمین + پیام عادی")

    async def scenario():
        message_tracker.reset_all()
        bot = _bot()
        dispatcher = GroupDispatcher(max_pending_normal=40)
        order = []
        hold = asyncio.Event()
        overflowed = []

        async def heavy():
            order.append("heavy")
            await hold.wait()
            order.append("heavy_done")

        async def admin_job():
            order.append("admin")

        async def other_group():
            order.append("other")

        chat_spam, user = -4200, 12
        for index in range(1, 81):
            result = ingest(bot, chat_spam, user, index, "بیوچک🥺")
            if result.skip_heavy:
                continue
            dispatcher.submit(
                chat_spam, heavy, priority=PRIORITY_NORMAL,
                on_overflow=lambda: overflowed.append("spam"),
            )
        dispatcher.submit(chat_spam, admin_job, priority=PRIORITY_ADMIN, kind="admin")
        dispatcher.submit(-4300, other_group, priority=PRIORITY_NORMAL)
        await asyncio.sleep(0.05)
        isolated = "other" in order and "heavy_done" not in order
        hold.set()
        await dispatcher.join(timeout=1)
        ids = set(message_tracker.spam_snapshot(chat_spam, user))
        cleanup = set(bot._big_spam_incidents.get(incident_key(chat_spam, user), {}).get("ids", ()))
        message_tracker.reset_all()
        return isolated, order, overflowed, ids, cleanup, dispatcher.stats["dropped"]

    isolated, order, overflowed, ids, cleanup, dropped = asyncio.run(scenario())
    check("گروه دیگر پشت موج اسپم نماند", isolated, f"-> {order}")
    check("دستور ادمین همان گروه بعد از شغل جاری اجرا شد", "admin" in order, f"-> {order}")
    check("overflow اسپم را قبل از detect حذف نکرد", overflowed == [], f"-> {overflowed}")
    check("هر ۸۰ id برای cleanup هست", ids == set(range(1, 81)) and cleanup == ids,
          f"-> tracker={len(ids)} cleanup={len(cleanup)}")
    check("صف سنگین overflow نشد", dropped == 0, f"-> dropped={dropped}")


def test_light_path_never_calls_rpc():
    print("\\n### مسیر سبک get_sender/get_chat صدا نمی‌زند")
    message_tracker.reset_all()
    bot = _bot()
    event = _event(-4400, 6, 1, "بیوچک🥺")
    result = ingest_event(bot, event)
    check("بدون RPC اجرا شد", not result.detected)
    event4 = None
    for index in range(2, 5):
        event4 = _event(-4400, 6, index, "بیوچک🧸")
        result = ingest_event(bot, event4)
    check("روی پیام ۴ تشخیص داد بدون RPC", result.detected and result.skip_heavy)
    message_tracker.reset_all()


def test_duplicate_message_id_is_not_double_tracked():
    print("\\n### message_id تکراری دو بار در tracker نمی‌رود")
    message_tracker.reset_all()
    message_tracker.add_message(-4500, 1, 10, "بیوچک🥺")
    message_tracker.add_message(-4500, 1, 10, "بیوچک🥺")
    rows = message_tracker.get_user_recent_messages(-4500, 1)
    check("یک ردیف برای یک id", len(rows) == 1, f"-> {len(rows)}")
    message_tracker.reset_all()


def test_admin_command_is_not_swallowed():
    print("\\n### دستور ادمین وارد مسیر skip_heavy نمی‌شود")
    message_tracker.reset_all()
    bot = _bot()
    result = ingest(bot, -4600, 2, 1, "پاک")
    check("پاک skip_heavy نیست", not result.skip_heavy and not result.detected)
    result = ingest(bot, -4600, 2, 2, "بن")
    check("بن skip_heavy نیست", not result.skip_heavy and not result.detected)
    message_tracker.reset_all()


def main():
    test_hundred_separate_promo_not_lost_to_overflow()
    test_packed_box_is_one_message_id()
    test_fast_ordinary_messages_are_not_spam()
    test_stress_spam_does_not_freeze_admin_or_other_group()
    test_light_path_never_calls_rpc()
    test_duplicate_message_id_is_not_double_tracked()
    test_admin_command_is_not_swallowed()
    print(f"\\npassed={PASSED} failed={FAILED}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
