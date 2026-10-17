"""ضداسپم GIF: استقلال کامل و حذف بدون جاماندن.

    python tests/test_gif_antispam.py
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import modules.gif_spam_detector as gsd

PASSED = FAILED = 0
CHAT = -100777


def check(label, cond, detail=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  PASS  {label}")
    else:
        FAILED += 1
        print(f"  FAIL  {label} {detail}")


class Doc:
    def __init__(self, mime="image/gif"):
        self.mime_type = mime
        self.attributes = []


class Media:
    def __init__(self, doc):
        self.document = doc


class GifMsg:
    def __init__(self, mid):
        self.id = mid
        self.document = Doc()
        self.media = Media(self.document)
        self.gif = True


class TextMsg:
    def __init__(self, mid):
        self.id = mid
        self.document = None
        self.media = None


class Client:
    """کلاینت جعلی که حذف‌ها را ثبت می‌کند."""

    def __init__(self, fail_times=0, always_fail_ids=()):
        self.deleted = []
        self.calls = 0
        self.fail_times = fail_times
        self.always_fail_ids = set(always_fail_ids)

    async def delete_messages(self, chat_id, ids):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("transient network failure")
        bad = [i for i in ids if i in self.always_fail_ids]
        if bad and len(ids) > 1:
            raise RuntimeError("batch contains an undeletable message")
        if bad:
            raise RuntimeError("undeletable message")
        self.deleted.extend(ids)


class Logger:
    def __init__(self):
        self.errors = []

    def log_info(self, *a, **k):
        pass

    def log_error(self, m):
        self.errors.append(m)


def send_burst(user_id, count, client=None, logger=None):
    """count گیف پشت سر هم و برگرداندن مجموعهٔ idهای صف‌شده."""
    queued = set()
    for mid in range(1, count + 1):
        ids, _flagged = gsd.handle_gif(CHAT, user_id, mid, client, logger)
        queued.update(ids)
    return queued


def test_detection():
    print("\n### تشخیص GIF مستقل است")
    gsd.reset_all()
    check("پیام GIF شناسایی می‌شود", gsd.is_gif_message(GifMsg(1)))
    check("پیام متنی GIF شناخته نمی‌شود", not gsd.is_gif_message(TextMsg(1)))
    doc = Doc("video/mp4")
    msg = TextMsg(2)
    msg.document = doc
    msg.media = Media(doc)
    check("ویدیوی معمولی GIF نیست", not gsd.is_gif_message(msg))


def test_threshold():
    print("\n### آستانهٔ تشخیص")
    gsd.reset_all()
    for mid in range(1, gsd.GIF_THRESHOLD):
        ids, flagged = gsd.track_gif(CHAT, 1, mid)
        check(f"گیف {mid} هنوز حذف نمی‌شود", ids == [] and not flagged, f"-> {ids}")
    ids, flagged = gsd.track_gif(CHAT, 1, gsd.GIF_THRESHOLD)
    check("در آستانه کل دسته برگردانده می‌شود",
          len(ids) == gsd.GIF_THRESHOLD, f"-> {ids}")
    check("کاربر flagged شد", flagged and gsd.is_flagged(CHAT, 1))


def test_no_gif_left_behind():
    """هستهٔ باگ: هیچ GIFی نباید جا بماند."""
    print("\n### هیچ GIF جا نمی‌ماند")
    for count in (6, 7, 10, 12, 25, 50, 100, 137):
        gsd.reset_all()
        queued = send_burst(2000 + count, count)
        left = [m for m in range(1, count + 1) if m not in queued]
        check(f"{count} گیف: همه صف شدند", not left,
              f"-> {len(left)} جامانده {left[:6]}")


def test_flag_persists_after_reset_history():
    print("\n### پاک‌کردن شمارنده، حالت flagged را باز نمی‌گرداند")
    gsd.reset_all()
    send_burst(3001, gsd.GIF_THRESHOLD)
    check("کاربر flagged است", gsd.is_flagged(CHAT, 3001))
    gsd.reset_gif_history(CHAT, 3001)
    check("پس از reset_gif_history هنوز flagged است",
          gsd.is_flagged(CHAT, 3001))
    ids, _ = gsd.track_gif(CHAT, 3001, 999)
    check("گیف بعدی بلافاصله حذف می‌شود", ids == [999], f"-> {ids}")


def test_clear_user_releases():
    print("\n### clear_user کاربر را آزاد می‌کند")
    gsd.reset_all()
    send_burst(3002, gsd.GIF_THRESHOLD)
    gsd.clear_user(CHAT, 3002)
    check("کاربر آزاد شد", not gsd.is_flagged(CHAT, 3002))
    ids, _ = gsd.track_gif(CHAT, 3002, 500)
    check("شمارش از نو آغاز می‌شود", ids == [], f"-> {ids}")


def test_per_user_isolation():
    print("\n### محدودیت فقط برای همان کاربر")
    gsd.reset_all()
    send_burst(4001, gsd.GIF_THRESHOLD)
    check("کاربر اول flagged است", gsd.is_flagged(CHAT, 4001))
    check("کاربر دوم flagged نیست", not gsd.is_flagged(CHAT, 4002))
    ids, _ = gsd.track_gif(CHAT, 4002, 1)
    check("گیف کاربر دوم حذف نمی‌شود", ids == [], f"-> {ids}")


def test_queue_dedupes():
    print("\n### صف حذف تکراری نمی‌پذیرد")
    gsd.reset_all()
    gsd.queue_delete(CHAT, [1, 2, 3])
    gsd.queue_delete(CHAT, [2, 3, 4])
    check("idهای تکراری یک بار ثبت می‌شوند",
          gsd.pending_count(CHAT) == 4, f"-> {gsd.pending_count(CHAT)}")


def test_flush_deletes_everything():
    print("\n### flush کل صف را حذف می‌کند")
    gsd.reset_all()
    client = Client()
    count = 40
    queued = send_burst(5001, count)
    deleted = asyncio.run(gsd.flush_deletes(client, CHAT, Logger()))
    check(f"همهٔ {len(queued)} پیام حذف شدند",
          set(client.deleted) == queued, f"-> {deleted}")
    check("صف خالی شد", gsd.pending_count(CHAT) == 0)
    left = [m for m in range(1, count + 1) if m not in client.deleted]
    check("هیچ گیفی در گروه نماند", not left, f"-> {left[:6]}")


def test_flush_retries_on_failure():
    print("\n### تلاش مجدد هنگام خطای گذرا")
    gsd.reset_all()
    logger = Logger()
    client = Client(fail_times=2)
    send_burst(5002, 10)
    asyncio.run(gsd.flush_deletes(client, CHAT, logger))
    check("پس از دو خطا همه حذف شدند", len(client.deleted) == 10,
          f"-> {len(client.deleted)}")
    check("خطاها لاگ شدند", len(logger.errors) >= 2, f"-> {len(logger.errors)}")
    check("صف خالی است", gsd.pending_count(CHAT) == 0)


def test_undeletable_message_isolated():
    print("\n### یک پیام غیرقابل‌حذف کل دسته را از بین نمی‌برد")
    gsd.reset_all()
    client = Client(always_fail_ids={7})
    send_burst(5003, 10)
    asyncio.run(gsd.flush_deletes(client, CHAT, Logger()))
    check("۹ پیام سالم حذف شدند", len(client.deleted) == 9,
          f"-> {len(client.deleted)}")
    check("پیام خراب حذف نشد", 7 not in client.deleted)
    check("پیام خراب برای تلاش بعدی در صف ماند",
          gsd.pending_count(CHAT) == 1, f"-> {gsd.pending_count(CHAT)}")


def test_cleanup_expired_drops_idle_not_live():
    print("\n### cleanup_expired شمارندهٔ بیکار را پاک می‌کند، زنده را نه")
    gsd.reset_all()
    gsd.track_gif(CHAT, 8001, 1)
    gsd.track_gif(CHAT, 8001, 2)
    live_key = (CHAT, 8001)
    check("شمارنده بعد از track وجود دارد", live_key in gsd.GIF_COUNTER)
    check("لمس ثبت شده", live_key in gsd._COUNTER_TOUCHED)
    now = gsd._now()
    removed = gsd.cleanup_expired(now)
    check("شمارنده تازه در همان لحظه پاک نمی‌شود",
          live_key in gsd.GIF_COUNTER, f"-> removed={removed}")
    gsd._COUNTER_TOUCHED[live_key] = now - gsd.FLAG_DURATION - 1
    removed = gsd.cleanup_expired(now)
    check("شمارندهٔ بیکار بعد از FLAG_DURATION پاک شد",
          live_key not in gsd.GIF_COUNTER and live_key not in gsd._COUNTER_TOUCHED,
          f"-> removed={removed}")
    check("حداقل یک کلید حذف شد", removed >= 1, f"-> {removed}")


def test_cleanup_expired_drops_expired_flags():
    print("\n### cleanup_expired فلگ منقضی را بدون سقف مصنوعی پاک می‌کند")
    gsd.reset_all()
    gsd.flag_user(CHAT, 8002, duration=10)
    key = (CHAT, 8002)
    check("فلگ تازه زنده است", gsd.is_flagged(CHAT, 8002))
    gsd._FLAGGED[key] = gsd._now() - 1
    removed = gsd.cleanup_expired()
    check("فلگ منقضی از نقشه رفت", key not in gsd._FLAGGED, f"-> {removed}")
    check("is_flagged بعد از جارو False است", not gsd.is_flagged(CHAT, 8002))


def test_reset_all_clears_counter_touch():
    print("\n### reset_all لمس شمارنده را هم پاک می‌کند")
    gsd.reset_all()
    gsd.track_gif(CHAT, 8003, 1)
    gsd.reset_all()
    check("GIF_COUNTER خالی", len(gsd.GIF_COUNTER) == 0)
    check("_COUNTER_TOUCHED خالی", len(gsd._COUNTER_TOUCHED) == 0)
    check("_FLAGGED خالی", len(gsd._FLAGGED) == 0)


def test_independent_state():
    print("\n### استقلال از سایر ماژول‌های ضداسپم")
    import modules.spam_detector as sd
    import modules.spam_history as sh
    gsd.reset_all()
    send_burst(6001, gsd.GIF_THRESHOLD)
    gif_names = {id(gsd.GIF_COUNTER), id(gsd._FLAGGED), id(gsd._DELETE_QUEUE)}
    other = {id(getattr(sd, n)) for n in dir(sd) if not n.startswith("__")
             and isinstance(getattr(sd, n), (dict, set, list))}
    other |= {id(getattr(sh, n)) for n in dir(sh) if not n.startswith("__")
              and isinstance(getattr(sh, n), (dict, set, list))}
    check("هیچ ساختار داده‌ای مشترک نیست", not (gif_names & other))
    check("ماژول صف و آستانهٔ خودش را دارد",
          hasattr(gsd, "GIF_THRESHOLD") and hasattr(gsd, "flush_deletes"))
    check("تابع مسیر مستقل موجود است", callable(gsd.handle_gif))


def test_end_to_end_burst():
    print("\n### سناریوی واقعی: رگبار گیف با flush زمان‌بندی‌شده")

    async def scenario(count):
        gsd.reset_all()
        client = Client()
        logger = Logger()
        for mid in range(1, count + 1):
            gsd.handle_gif(CHAT, 7001, mid, client, logger)
            await asyncio.sleep(0)          # شبیه‌سازی رسیدن پیاپی پیام‌ها
        for _ in range(8):
            await asyncio.sleep(0.1)
        await gsd.flush_deletes(client, CHAT, logger)
        return client

    for count in (6, 20, 60, 120):
        client = asyncio.run(scenario(count))
        left = [m for m in range(1, count + 1) if m not in client.deleted]
        check(f"رگبار {count} گیف: همه حذف شدند", not left,
              f"-> {len(left)} جامانده {left[:6]}")


def main():
    test_detection()
    test_threshold()
    test_no_gif_left_behind()
    test_flag_persists_after_reset_history()
    test_clear_user_releases()
    test_per_user_isolation()
    test_queue_dedupes()
    test_flush_deletes_everything()
    test_flush_retries_on_failure()
    test_undeletable_message_isolated()
    test_cleanup_expired_drops_idle_not_live()
    test_cleanup_expired_drops_expired_flags()
    test_reset_all_clears_counter_touch()
    test_independent_state()
    test_end_to_end_burst()

    print(f"\n{'=' * 52}")
    print(f"passed={PASSED} failed={FAILED}")
    print("=" * 52)
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
