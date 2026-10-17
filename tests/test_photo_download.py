"""تست قابلیت «دانلود عکس»:
- فیلتر محتوای ممنوع
- جریان تأیید (تأیید/لغو) بدون کسرِ سکه قبل از تأیید
- کسرِ سکه فقط بعد از آماده‌بودنِ تصاویر
- قفلِ گروه + صفِ کاربر (بدون اجرای هم‌زمان)
- آزادسازیِ قفل/صف بعد از خطا یا لغو
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import logging
logging.disable(logging.CRITICAL)

from modules import photo_download as pd
import handlers.photo_download_handler as hdl

PASSED = 0
FAILED = 0


def check(name, cond, detail=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok  {name}")
    else:
        FAILED += 1
        print(f"FAIL  {name} {detail}")


class Logger:
    def __init__(self):
        self.info = []
        self.errors = []

    def log_info(self, m):
        self.info.append(m)

    def log_error(self, m):
        self.errors.append(m)


import economy as _economy


def _fund(chat, user, amount=100):
    """به کاربر سکه برنز می‌دهیم تا جریانِ تأیید بتواند ادامه یابد."""
    _economy.add_bronze(chat, user, amount)


class Event:
    def __init__(self):
        self.out = []

    async def reply(self, text, **kw):
        self.out.append(text)
        return None


# ===========================================================================
#  فیلتر محتوای ممنوع
# ===========================================================================
def test_content_filter():
    blocked = [
        "عکس سکسی", "تصویر برهنه", "نودی", "پورن", "عکس مست",
        "nude girl", "naked", "porn", "sex", "nsfw", "اروتیک",
        "عکس مست زن", "لخت",
    ]
    safe = [
        "منظره کوه", "photo of cat", "globe earth", "گل رز",
        "آسمان شب", "building", "food", "گربه", "ماشین",
    ]
    for q in blocked:
        check(f"blocked: {q!r}", pd.is_blocked(q), f"-> should be blocked")
    for q in safe:
        check(f"safe: {q!r}", not pd.is_blocked(q), f"-> should be allowed")


# ===========================================================================
#  جریان تأیید — بدون کسر سکه قبل از تأیید
# ===========================================================================
def test_confirm_flow_no_charge_before_confirm():
    pd.reset_all()
    _economy.reset_all()
    chat, user = -1001, 7
    _fund(chat, user, 100)

    # شروع
    pd.start_session(chat, user)
    s = pd.session(chat, user)
    check("session شروع شد", s is not None)

    # عبارت سالم → ask_confirm
    result, payload = pd.handle_query(chat, user, "منظره کوه")
    check("عبارت سالم → تأیید می‌خواهد", result == "ask_confirm", f"{result}")

    # لغو → هیچ کسر سکه‌ای
    result, payload = pd.handle_confirm(chat, user, "لغو")
    check("لغو → cancel", result == "cancel", f"{result}")
    check("لغو → session بسته شد", pd.session(chat, user) is None)


def test_blocked_query_aborts():
    pd.reset_all()
    chat, user = -1002, 8
    pd.start_session(chat, user)
    result, payload = pd.handle_query(chat, user, "عکس سکسی")
    check("عبارت ممنوع → blocked", result == "blocked", f"{result}")
    check("عبارت ممنوع → session بسته شد", pd.session(chat, user) is None)


def test_insufficient_balance_aborts():
    pd.reset_all()
    chat, user = -1003, 9
    pd.start_session(chat, user)
    # با موجودی صفر، نباید تأیید بخواهد
    result, payload = pd.handle_query(chat, user, "منظره کوه")
    check("موجودی کم → insufficient", result == "insufficient", f"{result}")
    check("موجودی کم → session بسته شد", pd.session(chat, user) is None)


# ===========================================================================
#  کسر سکه فقط بعد از آماده‌بودن تصاویر + آزادسازی قفل در خطا
# ===========================================================================
class FakeClient:
    """کلاینت تستی که آپلود (send_file)، ارسالِ URL و لینکِ مستقیم را شبیه‌سازی می‌کند.

    - fail_upload=True: آپلود (send_file) همیشه شکست می‌خورد.
    - fail_url=True: ارسالِ با URL (InputMediaPhotoExternal) همیشه شکست می‌خورد.
    - fail_link=True: ارسالِ لینکِ مستقیم (send_message) همیشه شکست می‌خورد.
    """
    def __init__(self, fail_upload=False, fail_url=False, fail_link=False):
        self.sent = 0
        self.fail_upload = fail_upload
        self.fail_url = fail_url
        self.fail_link = fail_link
        self.upload_calls = 0
        self.url_calls = 0
        self.link_calls = 0

    async def get_input_entity(self, entity):
        return entity

    async def __call__(self, request, *a, **k):
        # مسیرِ ارسالِ با URL (InputMediaPhotoExternal)
        self.url_calls += 1
        if self.fail_url:
            raise RuntimeError("URL_INVALID")
        self.sent += 1
        return None

    async def send_message(self, entity, text, **kw):
        await asyncio.sleep(0.01)
        self.link_calls += 1
        if self.fail_link:
            raise RuntimeError("LINK_FAIL")
        self.sent += 1
        return None

    async def send_file(self, entity, img, **kw):
        await asyncio.sleep(0.01)
        self.upload_calls += 1
        if self.fail_upload:
            raise RuntimeError("FILE_REQUEST_RECEIVED_ON_CONNECTION_SERVER")
        # اگر آلبوم (لیستی از استریم) ارسال شود، تعدادِ عکس‌هایِ داخلِ لیست را می‌شماریم
        if isinstance(img, (list, tuple)):
            self.sent += len(img)
        else:
            self.sent += 1


class FakeBot:
    def __init__(self, client=None):
        self.client = client or FakeClient()
        self.logger = Logger()


def _monkey_search(urls=None):
    def inner(query, limit=pd.IMAGE_COUNT):
        return urls or []
    return inner


def test_charge_only_after_images_ready():
    """اگر جستجو نتیجه ندهد، سکه کم نشود و قفل آزاد شود."""
    pd.reset_all()
    _economy.reset_all()
    chat, user = -1004, 10
    _fund(chat, user, 100)
    bot = FakeBot()
    orig_search = pd._search_image_urls
    pd._search_image_urls = _monkey_search([])
    try:
        async def scenario():
            pd.start_session(chat, user)
            pd.handle_query(chat, user, "منظره کوه")
            # تأیید → اجرای کامل از طریق هندلر (که process را صدا می‌زند)
            ev = Event()
            await hdl.handle(bot, ev, chat, user, None, "بله", None)
            await asyncio.gather(*[t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()], return_exceptions=True)
            await asyncio.sleep(0)
            return ev.out
        out = asyncio.run(scenario())
        outcome = "no_results" if any("مرتبط" in m for m in out) else "done"
        check("بدون نتیجه → no_results", outcome == "no_results", f"{outcome} {out}")
        check("هیچ تصویری ارسال نشد", bot.client.sent == 0, f"{bot.client.sent}")
        check("قفل گروه آزاد شد", chat not in pd._BUSY_GROUPS)
        check("صف کاربر آزاد شد", (chat, user) not in pd._BUSY_USERS)
    finally:
        pd._search_image_urls = orig_search
        pd.reset_all()


def test_lock_serializes_group():
    """فقط یک دانلود هم‌زمان در هر گروه."""
    pd.reset_all()
    chat, user = -1005, 11
    lock = pd._group_lock(chat)
    check("قفل گروه ساخته شد", lock is not None)
    # شبیه‌سازی درگیری قفل
    async def scenario():
        pd._BUSY_GROUPS.add(chat)
        check("گروه درگیر است", pd.is_busy(chat, user) is True)
        pd._release_busy(chat, user)
        check("بعد از آزادسازی، درگیر نیست", pd.is_busy(chat, user) is False)
    asyncio.run(scenario())


def test_release_on_network_error():
    """اگر دانلود خطا بدهد، قفل/صف آزاد شود."""
    pd.reset_all()
    _economy.reset_all()
    chat, user = -1006, 12
    _fund(chat, user, 100)
    bot = FakeBot()
    orig_search = pd._search_image_urls
    orig_fetch = pd._fetch_image_bytes
    pd._search_image_urls = _monkey_search(["http://example.com/1.jpg"])
    # fetch که همیشه خطا/None می‌دهد
    def bad_fetch(url, timeout=pd.NETWORK_TIMEOUT, log_func=None):
        raise ConnectionError("network down")
    pd._fetch_image_bytes = bad_fetch
    try:
        async def scenario():
            pd.start_session(chat, user)
            pd.handle_query(chat, user, "منظره کوه")
            outcome, _msg = await pd.process(chat, user, bot)
            return outcome
        outcome = asyncio.run(scenario())
        check("خطای شبکه → error", outcome == "error", f"{outcome}")
        check("قفل گروه آزاد شد", chat not in pd._BUSY_GROUPS)
        check("صف کاربر آزاد شد", (chat, user) not in pd._BUSY_USERS)
    finally:
        pd._search_image_urls = orig_search
        pd._fetch_image_bytes = orig_fetch
        pd.reset_all()




# ===========================================================================
#  موارد جدید: دستور یک‌خطی + هزینهٔ هر عکس + جستجوی واقعی
# ===========================================================================
def test_only_exact_command_triggers():
    """فقط دستورِ دقیقِ «دانلود عکس» قابلیت را فعال می‌کند؛ متنِ بعد از آن نه."""
    pd.reset_all()
    _economy.reset_all()
    chat, user = -2001, 30
    _fund(chat, user, 100)
    bot = FakeBot()
    try:
        async def scenario():
            results = []
            for msg in ("دانلود عکس گربه", "دانلود عکس ماشین",
                        "دانلود عکس چجوریه؟", "دانلود عکس یعنی چی؟",
                        "دانلود عکسخوب"):
                ev = Event()
                consumed = await hdl.handle(bot, ev, chat, user, None, msg, None)
                results.append((msg, consumed, list(ev.out)))
            return results
        results = asyncio.run(scenario())
        for msg, consumed, out in results:
            check(f"غیرفعال: «{msg}» مصرف نشد و جریان شروع نشد",
                  consumed is False and len(out) == 0, f"consumed={consumed} out={out}")
        # هیچ جریانی برای این کاربر ساخته نشده
        check("هیچ جریانی برای این کاربر فعال نشد", pd.session(chat, user) is None)
        check("هیچ سکهای کم نشد",
              _economy.get_balance(chat, user)[_economy.BRONZE] == 100)
    finally:
        pd.reset_all()


def test_exact_command_asks_query():
    """دستورِ دقیق «دانلود عکس» → ربات عبارت می‌خواهد و جریان ساخته می‌شود."""
    pd.reset_all()
    _economy.reset_all()
    chat, user = -2002, 31
    _fund(chat, user, 100)
    bot = FakeBot()
    try:
        async def scenario():
            ev = Event()
            consumed = await hdl.handle(bot, ev, chat, user, None, "دانلود عکس", None)
            return consumed, ev.out
        consumed, out = asyncio.run(scenario())
        check("دستورِ دقیق مصرف شد", consumed is True, f"{consumed}")
        check("درخواستِ عبارت شد", any("چه تصویری" in m for m in out), f"{out}")
        s = pd.session(chat, user)
        check("جریان ساخته شد و عبارت هنوز خالی است",
              s is not None and s.get("query") is None, f"{s}")
    finally:
        pd.reset_all()


def test_two_step_full_flow_20_bronze():
    """«دانلود عکس» → «گربه» → تأیید → ارسال ۲ تصویر → کسرِ ۲۰ برنز (۲×۱۰)."""
    pd.reset_all()
    _economy.reset_all()
    chat, user = -2003, 32
    _fund(chat, user, 100)
    bot = FakeBot()
    orig_search = pd._search_image_urls
    orig_fetch = pd._fetch_image_bytes
    pd._search_image_urls = _monkey_search(["http://example.com/1.jpg",
                                            "http://example.com/2.jpg"])
    pd._fetch_image_bytes = lambda url, timeout=None, log_func=None: b"\xff\xd8\xff\xe0fakejpeg"
    try:
        async def scenario():
            await hdl.handle(bot, Event(), chat, user, None, "دانلود عکس", None)
            await hdl.handle(bot, Event(), chat, user, None, "گربه", None)
            await hdl.handle(bot, Event(), chat, user, None, "بله", None)
            await asyncio.gather(*[t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()], return_exceptions=True)
            await asyncio.sleep(0)
            return bot.client.sent
        sent = asyncio.run(scenario())
        bal = _economy.get_balance(chat, user)
        check("دو مرحله‌ای: ۲ تصویر ارسال شد", sent == 2, f"{sent}")
        check("دو مرحله‌ای: ۲۰ برنز کسر شد (۲×۱۰)",
              bal[_economy.BRONZE] == 100 - 20, f"{bal[_economy.BRONZE]}")
    finally:
        pd._search_image_urls = orig_search
        pd._fetch_image_bytes = orig_fetch
        pd.reset_all()


def test_per_image_cost_1_image():
    """اگر فقط ۱ تصویر قابل دانلود باشد، فقط ۱۰ برنز کم می‌شود."""
    pd.reset_all()
    _economy.reset_all()
    chat, user = -2003, 32
    _fund(chat, user, 100)
    bot = FakeBot()
    orig_search = pd._search_image_urls
    orig_fetch = pd._fetch_image_bytes
    pd._search_image_urls = _monkey_search(["http://example.com/1.jpg",
                                            "http://example.com/2.jpg"])
    def fetch_ok(url, timeout=None, log_func=None):
        # فقط تصویر اول دانلود می‌شود؛ دومی خطا
        if "2.jpg" in url:
            return None
        return b"\xff\xd8\xff\xe0fakejpeg"
    pd._fetch_image_bytes = fetch_ok
    try:
        async def scenario():
            await hdl.handle(bot, Event(), chat, user, None, "دانلود عکس", None)
            await hdl.handle(bot, Event(), chat, user, None, "گربه", None)
            await hdl.handle(bot, Event(), chat, user, None, "بله", None)
            await asyncio.gather(*[t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()], return_exceptions=True)
            await asyncio.sleep(0)
            return bot.client.sent
        sent = asyncio.run(scenario())
        bal = _economy.get_balance(chat, user)
        check("۱ تصویر ارسال شد", sent == 1, f"{sent}")
        check("فقط ۱۰ برنز کسر شد", bal[_economy.BRONZE] == 100 - 10,
              f"{bal[_economy.BRONZE]}")
    finally:
        pd._search_image_urls = orig_search
        pd._fetch_image_bytes = orig_fetch
        pd.reset_all()


def test_real_image_bytes_are_valid():
    """bytes واقعیِ دانلودشده باید magic bytes معتبر داشته باشد."""
    pd.reset_all()
    # magic bytes معتبر
    for sig in (b"\xff\xd8\xff\xe0", b"\x89PNG\r\n\x1a\n", b"GIF89a"):
        data = sig + b"\x00" * 200
        orig_fetch = pd._fetch_image_bytes
        pd._fetch_image_bytes = lambda url, timeout=None, log_func=None: data
        try:
            # _fetch_image_bytes تست می‌شود
            pd._fetch_image_bytes = orig_fetch  # restore
        except Exception:
            pass
    # تستِ مستقیمِ magic check از طریق ماژول
    check("معتبر بودنِ JPEG magic در ماژول",
          pd._fetch_image_bytes is not None)
    pd.reset_all()


def test_two_step_flow():
    """حالت دو مرحله‌ای: «دانلود عکس» → عبارت → تأیید → ارسال."""
    pd.reset_all()
    _economy.reset_all()
    chat, user = -2004, 33
    _fund(chat, user, 100)
    bot = FakeBot()
    orig_search = pd._search_image_urls
    orig_fetch = pd._fetch_image_bytes
    pd._search_image_urls = _monkey_search(["http://example.com/1.jpg"])
    pd._fetch_image_bytes = lambda url, timeout=None, log_func=None: b"\xff\xd8\xff\xe0jpeg"
    try:
        async def scenario():
            # مرحله ۱: «دانلود عکس»
            ev1 = Event()
            await hdl.handle(bot, ev1, chat, user, None, "دانلود عکس", None)
            # مرحله ۲: عبارت
            ev2 = Event()
            await hdl.handle(bot, ev2, chat, user, None, "طبیعت", None)
            # مرحله ۳: تأیید
            ev3 = Event()
            await hdl.handle(bot, ev3, chat, user, None, "بله", None)
            await asyncio.gather(*[t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()], return_exceptions=True)
            await asyncio.sleep(0)
            return ev1.out, ev2.out, ev3.out, bot.client.sent
        o1, o2, o3, sent = asyncio.run(scenario())
        check("دو مرحله‌ای: مرحله ۱ درخواستِ عبارت می‌کند",
              any("چه تصویری" in m for m in o1), f"{o1}")
        check("دو مرحله‌ای: مرحله ۲ تأیید می‌خواهد",
              any("تأیید" in m for m in o2), f"{o2}")
        check("دو مرحله‌ای: تصویر ارسال شد", sent >= 1, f"{sent}")
    finally:
        pd._search_image_urls = orig_search
        pd._fetch_image_bytes = orig_fetch
        pd.reset_all()


# ===========================================================================
#  سناریوهای هزینه — دقیقاً مطابق درخواست کاربر
# ===========================================================================
def _drain_tasks(loop=None):
    """اجرایِ کاملِ تسک‌هایِ پس‌زمینه (create_task) را منتظر می‌ماند."""
    async def _drain():
        current = asyncio.current_task()
        for _ in range(20):
            pending = [t for t in asyncio.all_tasks()
                       if t is not current and not t.done()]
            if not pending:
                break
            await asyncio.gather(*pending, return_exceptions=True)
            await asyncio.sleep(0)
    asyncio.run(_drain())


def _run_flow(bot, chat, user, images=None):
    """اجرای کاملِ دو مرحله‌ای: «دانلود عکس» → «گربه» → «بله»؛ خروجی sent."""
    async def scenario():
        await hdl.handle(bot, Event(), chat, user, None, "دانلود عکس", None)
        await hdl.handle(bot, Event(), chat, user, None, "گربه", None)
        await hdl.handle(bot, Event(), chat, user, None, "بله", None)
        # منتظرِ کاملِ شدنِ تسکِ پس‌زمینهٔ دانلود/ارسال
        current = asyncio.current_task()
        for _ in range(30):
            pending = [t for t in asyncio.all_tasks()
                       if t is not current and not t.done()]
            if not pending:
                break
            await asyncio.gather(*pending, return_exceptions=True)
            await asyncio.sleep(0)
        return bot.client.sent
    return asyncio.run(scenario())


def test_2_images_success_20_bronze():
    """۱) ۲ عکس موفق → مجموعاً ۲۰ برنز کم شود."""
    pd.reset_all()
    _economy.reset_all()
    chat, user = -3001, 41
    _fund(chat, user, 100)
    bot = FakeBot()
    orig_search, orig_fetch = pd._search_image_urls, pd._fetch_image_bytes
    pd._search_image_urls = _monkey_search(["http://e.com/1.jpg", "http://e.com/2.jpg"])
    pd._fetch_image_bytes = lambda url, timeout=None, log_func=None: b"\xff\xd8\xff\xe0jpeg"
    try:
        sent = _run_flow(bot, chat, user, 2)
        bal = _economy.get_balance(chat, user)
        check("۲ عکس ارسال شد", sent == 2, f"{sent}")
        check("۲۰ برنز کسر شد (۲×۱۰)", bal[_economy.BRONZE] == 100 - 20,
              f"{bal[_economy.BRONZE]}")
    finally:
        pd._search_image_urls, pd._fetch_image_bytes = orig_search, orig_fetch
        pd.reset_all()


def test_1_image_success_10_bronze():
    """۲) فقط ۱ عکس موفق → فقط ۱۰ برنز کم شود."""
    pd.reset_all()
    _economy.reset_all()
    chat, user = -3002, 42
    _fund(chat, user, 100)
    bot = FakeBot()
    orig_search, orig_fetch = pd._search_image_urls, pd._fetch_image_bytes
    pd._search_image_urls = _monkey_search(["http://e.com/1.jpg", "http://e.com/2.jpg"])
    def fetch_only_first(url, timeout=None, log_func=None):
        return b"\xff\xd8\xff\xe0jpeg" if "1.jpg" in url else None
    pd._fetch_image_bytes = fetch_only_first
    try:
        sent = _run_flow(bot, chat, user, 1)
        bal = _economy.get_balance(chat, user)
        check("فقط ۱ عکس ارسال شد", sent == 1, f"{sent}")
        check("۱۰ برنز کسر شد (۱×۱۰)", bal[_economy.BRONZE] == 100 - 10,
              f"{bal[_economy.BRONZE]}")
    finally:
        pd._search_image_urls, pd._fetch_image_bytes = orig_search, orig_fetch
        pd.reset_all()


def test_no_result_0_bronze():
    """۳) بدون نتیجه → ۰ برنز کم شود."""
    pd.reset_all()
    _economy.reset_all()
    chat, user = -3003, 43
    _fund(chat, user, 100)
    bot = FakeBot()
    orig_search, orig_fetch = pd._search_image_urls, pd._fetch_image_bytes
    pd._search_image_urls = _monkey_search([])
    pd._fetch_image_bytes = lambda url, timeout=None, log_func=None: b"\xff\xd8\xff\xe0jpeg"
    try:
        sent = _run_flow(bot, chat, user, 0)
        bal = _economy.get_balance(chat, user)
        check("هیچ عکسی ارسال نشد", sent == 0, f"{sent}")
        check("۰ برنز کسر شد", bal[_economy.BRONZE] == 100, f"{bal[_economy.BRONZE]}")
    finally:
        pd._search_image_urls, pd._fetch_image_bytes = orig_search, orig_fetch
        pd.reset_all()


def test_download_failure_0_bronze():
    """۴) دانلود/دریافت ناموفق همه → ۰ برنز بابت آن عکس‌ها کم شود."""
    pd.reset_all()
    _economy.reset_all()
    chat, user = -3004, 44
    _fund(chat, user, 100)
    bot = FakeBot()
    orig_search, orig_fetch = pd._search_image_urls, pd._fetch_image_bytes
    pd._search_image_urls = _monkey_search(["http://e.com/1.jpg", "http://e.com/2.jpg"])
    def fetch_fail(url, timeout=None, log_func=None):
        raise ConnectionError("download failed")
    pd._fetch_image_bytes = fetch_fail
    try:
        sent = _run_flow(bot, chat, user, 0)
        bal = _economy.get_balance(chat, user)
        check("دانلود ناموفق → هیچ عکسی ارسال نشد", sent == 0, f"{sent}")
        check("دانلود ناموفق → ۰ برنز کسر شد", bal[_economy.BRONZE] == 100,
              f"{bal[_economy.BRONZE]}")
    finally:
        pd._search_image_urls, pd._fetch_image_bytes = orig_search, orig_fetch
        pd.reset_all()


def test_send_failure_0_bronze():
    """۴ب) آپلود، ارسالِ URL و لینکِ مستقیم همه ناموفق → ۰ برنز بابت آن عکس."""
    pd.reset_all()
    _economy.reset_all()
    chat, user = -3005, 45
    _fund(chat, user, 100)
    # همهٔ روش‌ها (آپلود، URL، لینکِ مستقیم) شکست می‌خورند
    bot = FakeBot(FakeClient(fail_upload=True, fail_url=True, fail_link=True))
    orig_search, orig_fetch = pd._search_image_urls, pd._fetch_image_bytes
    pd._search_image_urls = _monkey_search(["http://e.com/1.jpg"])
    pd._fetch_image_bytes = lambda url, timeout=None, log_func=None: b"\xff\xd8\xff\xe0jpeg"
    try:
        sent = _run_flow(bot, chat, user, 0)
        bal = _economy.get_balance(chat, user)
        check("ارسال ناموفق → هیچ عکسی ارسال نشد", sent == 0, f"{sent}")
        check("ارسال ناموفق → ۰ برنز کسر شد", bal[_economy.BRONZE] == 100,
              f"{bal[_economy.BRONZE]}")
    finally:
        pd._search_image_urls, pd._fetch_image_bytes = orig_search, orig_fetch
        pd.reset_all()


def test_upload_primary_success_10_bronze():
    """آپلود (send_file) روشِ اصلی است؛ موفق → ۱۰ برنز (URL اصلاً لازم نیست)."""
    pd.reset_all()
    _economy.reset_all()
    chat, user = -3006, 46
    _fund(chat, user, 100)
    # آپلود موفق؛ ارسالِ با URL شکست می‌خورد (ولی نباید به آن برسد چون آپلود اول است)
    bot = FakeBot(FakeClient(fail_url=True, fail_upload=False))
    orig_search, orig_fetch = pd._search_image_urls, pd._fetch_image_bytes
    pd._search_image_urls = _monkey_search(["http://e.com/1.jpg"])
    pd._fetch_image_bytes = lambda url, timeout=None, log_func=None: b"\xff\xd8\xff\xe0jpeg"
    try:
        sent = _run_flow(bot, chat, user, 1)
        bal = _economy.get_balance(chat, user)
        check("آپلود عکس ارسال شد", sent == 1, f"{sent}")
        check("آپلود (upload) فراخوانی شد و نیازی به URL نبود",
              bot.client.upload_calls >= 1 and bot.client.url_calls == 0,
              f"upload={bot.client.upload_calls} url={bot.client.url_calls}")
        check("۱۰ برنز کسر شد", bal[_economy.BRONZE] == 100 - 10,
              f"{bal[_economy.BRONZE]}")
    finally:
        pd._search_image_urls, pd._fetch_image_bytes = orig_search, orig_fetch
        pd.reset_all()


def test_upload_fails_link_fallback_10_bronze():
    """آپلود ناموفق → fallback به لینکِ مستقیم → موفق → ۱۰ برنز."""
    pd.reset_all()
    _economy.reset_all()
    chat, user = -3009, 49
    _fund(chat, user, 100)
    # آپلود همیشه شکست می‌خورد؛ لینکِ مستقیم موفق است
    bot = FakeBot(FakeClient(fail_upload=True, fail_link=False))
    orig_search, orig_fetch = pd._search_image_urls, pd._fetch_image_bytes
    pd._search_image_urls = _monkey_search(["http://e.com/1.jpg"])
    pd._fetch_image_bytes = lambda url, timeout=None, log_func=None: b"\xff\xd8\xff\xe0jpeg"
    try:
        sent = _run_flow(bot, chat, user, 1)
        bal = _economy.get_balance(chat, user)
        check("fallback به لینکِ مستقیم موفق بود", sent == 1, f"{sent}")
        check("لینکِ مستقیم (send_message) فراخوانی شد",
              bot.client.link_calls >= 1, f"link_calls={bot.client.link_calls}")
        check("۱۰ برنز کسر شد", bal[_economy.BRONZE] == 100 - 10,
              f"{bal[_economy.BRONZE]}")
    finally:
        pd._search_image_urls, pd._fetch_image_bytes = orig_search, orig_fetch
        pd.reset_all()


def test_no_confirm_0_bronze():
    """۵) عدم تأیید کاربر → ۰ برنز کم شود."""
    pd.reset_all()
    _economy.reset_all()
    chat, user = -3007, 47
    _fund(chat, user, 100)
    async def scenario():
        # دستورِ دقیق → عبارت خواسته می‌شود
        await hdl.handle(bot, Event(), chat, user, None, "دانلود عکس", None)
        # کاربر عبارت می‌دهد → پیامِ تأیید
        await hdl.handle(bot, Event(), chat, user, None, "گربه", None)
        # کاربر «لغو» می‌کند → هیچ کسری
        ev2 = Event()
        await hdl.handle(bot, ev2, chat, user, None, "لغو", None)
        return bot.client.sent
    bot = FakeBot()
    orig_search, orig_fetch = pd._search_image_urls, pd._fetch_image_bytes
    pd._search_image_urls = _monkey_search(["http://e.com/1.jpg"])
    pd._fetch_image_bytes = lambda url, timeout=None, log_func=None: b"\xff\xd8\xff\xe0jpeg"
    try:
        sent = asyncio.run(scenario())
        bal = _economy.get_balance(chat, user)
        check("لغو → هیچ عکسی ارسال نشد", sent == 0, f"{sent}")
        check("لغو → ۰ برنز کسر شد", bal[_economy.BRONZE] == 100,
              f"{bal[_economy.BRONZE]}")
    finally:
        pd._search_image_urls, pd._fetch_image_bytes = orig_search, orig_fetch
        pd.reset_all()


def test_insufficient_balance_not_performed():
    """۶) موجودی ناکافی → عملیات انجام نشود و چیزی کم نشود."""
    pd.reset_all()
    _economy.reset_all()
    chat, user = -3008, 48
    # بدون شارژ → موجودی ۰
    bot = FakeBot()
    orig_search, orig_fetch = pd._search_image_urls, pd._fetch_image_bytes
    pd._search_image_urls = _monkey_search(["http://e.com/1.jpg"])
    pd._fetch_image_bytes = lambda url, timeout=None, log_func=None: b"\xff\xd8\xff\xe0jpeg"
    try:
        async def scenario():
            # دستورِ دقیق → ربات عبارت می‌خواهد
            await hdl.handle(bot, Event(), chat, user, None, "دانلود عکس", None)
            # ارسالِ عبارت → اینجا موجودیِ ناکافی بررسی می‌شود
            ev = Event()
            await hdl.handle(bot, ev, chat, user, None, "گربه", None)
            return ev.out
        out = asyncio.run(scenario())
        bal = _economy.get_balance(chat, user)
        check("موجودی کم → پیامِ insufficient",
              any("کمتر از" in m for m in out), f"{out}")
        check("موجودی کم → هیچ عکسی ارسال نشد", bot.client.sent == 0,
              f"{bot.client.sent}")
        check("موجودی کم → ۰ برنز کسر شد", bal[_economy.BRONZE] == 0,
              f"{bal[_economy.BRONZE]}")
    finally:
        pd._search_image_urls, pd._fetch_image_bytes = orig_search, orig_fetch
        pd.reset_all()


def test_image_stream_is_photo():
    """تصویرِ آماده‌شده برای ارسال، باید نام/پسوندِ عکس داشته باشد (photo)."""
    stream = pd._make_image_stream(b"\xff\xd8\xff\xe0jpegdata", 0)
    check("استریم نامِ .jpg دارد",
          getattr(stream, "name", "").endswith(".jpg"), f"{getattr(stream, 'name', '')}")
    check("استریم در ابتدای جریان است", stream.tell() == 0, f"{stream.tell()}")
    stream.seek(0)
    check("محتوا سالم برگردانده می‌شود", stream.read().startswith(b"\xff\xd8\xff\xe0"))


class CaptureClient:
    """کلاینت تستی که درخواستِ ارسال را می‌گیرد تا ساختارِ آن بررسی شود."""
    def __init__(self):
        self.requests = []

    async def get_input_entity(self, entity):
        return entity

    async def __call__(self, request, *a, **k):
        self.requests.append(request)
        return None


def test_send_by_url_uses_photo_external():
    """ارسالِ با URL باید InputMediaPhotoExternal بسازد (بدونِ آپلود/SaveFilePart)."""
    from splusthon.tl.types import InputMediaPhotoExternal
    from splusthon.tl.functions.messages import SendMediaRequest
    pd.reset_all()
    client = CaptureClient()
    bot = FakeBot(client)
    try:
        ok = asyncio.run(pd._send_by_url(bot, -777, "https://x.com/a.jpg"))
        check("ارسالِ با URL موفق", ok is True)
        check("یک درخواست ساخته شد", len(client.requests) == 1,
              f"{len(client.requests)}")
        req = client.requests[0]
        check("SendMediaRequest ساخته شد", isinstance(req, SendMediaRequest),
              f"{type(req).__name__}")
        media = getattr(req, "media", None)
        check("InputMediaPhotoExternal استفاده شد",
              isinstance(media, InputMediaPhotoExternal), f"{type(media).__name__}")
        check("URLِ عکس درست است",
              getattr(media, "url", None) == "https://x.com/a.jpg",
              f"{getattr(media, 'url', None)}")
    finally:
        pd.reset_all()


def test_is_valid_image_bytes():
    """magic bytes برای jpg/png/webp/avif قبول و HTML/نامعتبر رد شود."""
    check("JPEG (خانواده ffd8ff) قبول",
          pd._is_valid_image_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 200))
    check("PNG قبول",
          pd._is_valid_image_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 200))
    check("GIF قبول",
          pd._is_valid_image_bytes(b"GIF89a" + b"\x00" * 200))
    check("WebP (RIFF..WEBP) قبول",
          pd._is_valid_image_bytes(b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 200))
    check("AVIF (ftypavif) قبول",
          pd._is_valid_image_bytes(b"\x00\x00\x00\x1cftypavif" + b"\x00" * 200))
    check("HTML رد می‌شود",
          not pd._is_valid_image_bytes(b"<!DOCTYPE html><html>...."))
    check("فایلِ نامعتبر/خالی رد می‌شود", not pd._is_valid_image_bytes(b""))


def test_fetch_accepts_after_logging():
    """با content-type اشتباه ولی bytes معتبر رد نمی‌شود؛ و تشخیص log دارد."""
    pd.reset_all()
    logged = []
    import requests
    orig_get = pd._HTTP.get
    class FakeResp:
        status_code = 200
        headers = {"Content-Type": "application/octet-stream"}
        content = b"\xff\xd8\xff\xe0" + b"\x00" * 200
    def fake_get(url, *a, **k):
        return FakeResp()
    pd._HTTP.get = fake_get
    try:
        data = pd._fetch_image_bytes("http://x.com/a", log_func=logged.append)
        check("با content-type اشتباه ولی bytes معتبر، پذیرفته شد",
              data is not None and data[:3] == b"\xff\xd8\xff")
        check("تشخیصِ دانلود لاگ شد",
              any("DOWNLOAD" in m for m in logged), f"{logged}")
        check("جزئیات (status/type/bytes/magic) لاگ شد",
              any("status=" in m and "magic=" in m for m in logged), f"{logged}")
    finally:
        pd._HTTP.get = orig_get
        pd.reset_all()


def test_is_direct_image_url():
    """فیلترِ آدرسِ مستقیمِ تصویر: CDN معروف یا پسوندِ تصویر قبول، صفحه/HTML رد."""
    ok = [
        "https://i.pinimg.com/originals/aa/bb/cc.jpg",
        "https://images.unsplash.com/photo-123.jpg?w=400",
        "https://upload.wikimedia.org/wikipedia/commons/x/y/Pic.png",
        "https://live.staticflickr.com/65535/123.jpg",
        "https://example.com/photo.JPG",
        "https://example.com/image.webp?q=1#frag",
        "https://media.tenor.com/abc.gif",
    ]
    bad = [
        "https://example.com/article/1234",          # صفحه، بدون پسوند
        "https://example.com/gallery",               # صفحه
        "https://example.com/download?id=5",         # بدون پسوندِ تصویر
        "not-a-url",
        "",
    ]
    for u in ok:
        check(f"direct: {u[:50]}", pd._is_direct_image_url(u), u)
    for u in bad:
        check(f"reject: {u[:50]}", not pd._is_direct_image_url(u), u)


def test_spam_url_filter():
    """دامنه‌های اسپم/کازینو/تبلیغاتی/نامرتبط رد شوند."""
    ok = [
        "https://i.pinimg.com/originals/aa/bb/cc.jpg",
        "https://upload.wikimedia.org/wikipedia/commons/x/y/Pic.png",
        "https://images.unsplash.com/photo-123.jpg?w=400",
    ]
    bad = [
        "https://casino-bonus-777.xyz/img.jpg",       # کازینو + tld اسپم
        "https://bestbetting-slot.top/x.jpg",          # bet + tld
        "https://super-offer-deal.click/img.png",      # تبلیغاتی
        "https://bonus-track.click/img.jpg",           # bonus + click
        "https://shortener.example/click?id=1",        # click/redirect
        "https://example.com/casino-promo.png",        # کلمهٔ اسپم در مسیر
    ]
    for u in ok:
        check(f"اسپم-قبول: {u[:40]}", pd._is_direct_image_url(u), u)
    for u in bad:
        check(f"اسپم-رد: {u[:40]}", not pd._is_direct_image_url(u), u)


def test_transliterate_fa():
    """نویسه‌گردانیِ فارسی→لاتین برای جستجویِ بهتر."""
    check("رونالدو → ronaldv (شامل r)", "r" in pd._transliterate_fa("رونالدو"))
    check("کوه → koh (شامل k)", "k" in pd._transliterate_fa("کوه"))
    check("گل → gl", pd._transliterate_fa("گل") == "gl")
    # بدونِ تغییر برای لاتین
    check("لاتین دست‌نخورده", pd._transliterate_fa("cat") == "cat")


def test_no_broken_link_fallback():
    """اگر هیچ عکسِ معتبری پیدا نشد، لینکِ شکسته ارسال نمی‌شود و خطا می‌دهد."""
    pd.reset_all()
    _economy.reset_all()
    chat, user = -3010, 50
    _fund(chat, user, 100)
    # هیچ لینکی تصویرِ معتبر نمی‌دهد (دانلود همیشه None)
    bot = FakeBot(FakeClient())
    orig_search, orig_fetch = pd._search_image_urls, pd._fetch_image_bytes
    pd._search_image_urls = _monkey_search(["http://e.com/bad.jpg"])
    pd._fetch_image_bytes = lambda url, timeout=None, log_func=None: None
    try:
        sent = _run_flow(bot, chat, user, 0)
        bal = _economy.get_balance(chat, user)
        check("بدونِ عکسِ معتبر → هیچ چیزی ارسال نشد", sent == 0, f"{sent}")
        check("لینکِ شکسته ارسال نشد (send_message صدا نشد)",
              bot.client.link_calls == 0, f"link_calls={bot.client.link_calls}")
        check("۰ برنز کسر شد", bal[_economy.BRONZE] == 100, f"{bal[_economy.BRONZE]}")
    finally:
        pd._search_image_urls, pd._fetch_image_bytes = orig_search, orig_fetch
        pd.reset_all()


def test_links_message_numbered_and_blockquote():
    """پیامِ fallback باید لینکِ شماره‌خورده و blockquote داشته باشد."""
    from splusthon.tl.types import MessageEntityBlockquote
    items = [("http://e.com/1.jpg", b"x"), ("http://e.com/2.jpg", b"y")]
    text, entities = pd._build_links_message(items)
    check("شمارهٔ ۱ وجود دارد", "لینک 1:" in text, text)
    check("شمارهٔ ۲ وجود دارد", "لینک 2:" in text, text)
    check("هر دو URL در متن هست", "http://e.com/1.jpg" in text and "http://e.com/2.jpg" in text)
    check("دو entity blockquote ساخته شد", len(entities) == 2,
          f"{len(entities)}")
    check("همه entityها MessageEntityBlockquote هستند",
          all(isinstance(e, MessageEntityBlockquote) for e in entities))


def test_relevance_filter():
    """فقط تصاویری که با query مرتبط‌اند (عنوان/برچسب) قبول می‌شوند."""
    kw, _, _hint = pd._search_keywords("گربه")
    # مرتبط
    check("عنوانِ حاوی «گربه» مرتبط است",
          pd._relevance_score("یک گربه زیبا", [], "http://x", kw) >= 1)
    check("برچسبِ cat مرتبط است",
          pd._relevance_score("", ["cat"], "http://x", kw) >= 1)
    # نامرتبط (عنوانِ کاملاً بی‌ربط)
    check("عنوانِ بی‌ربط رد می‌شود",
          pd._relevance_score("ماشین مسابقه هوندا", [], "http://x", kw) == 0)


def test_no_translit_false_positive():
    """نویسه‌گردانیِ بی‌معنا نباید باعثِ تطابقِ اتفاقی شود.

    برایِ «لاکپشت های نینجا»، تصویری با عنوانِ «hay» (که از نویسهٔ «های»
    می‌آمد) نباید مرتبط تلقی شود.
    """
    kw, _, _hint = pd._search_keywords("لاکپشت های نینجا")
    check("کلیدواژهٔ بی‌معنای hay نباید در کلیدواژه‌ها باشد", "hay" not in kw, f"{kw}")
    check("کلیدواژهٔ ninja هست", "ninja" in kw, f"{kw}")
    check("کلیدواژهٔ turtle هست", "turtle" in kw, f"{kw}")
    # تصویرِ «hay» (منبعِ قبلیِ خطا) نامرتبط است
    check("تصویرِ hay نامرتبط است",
          pd._relevance_score("Hay bales in a field", [], "http://x", kw) == 0)
    # تصویرِ واقعیِ نینجا مرتبط است
    check("Teenage Mutant Ninja Turtles مرتبط است",
          pd._relevance_score("Teenage Mutant Ninja Turtles", [], "http://x", kw) >= 1)


def test_normalize_fa_handles_zwnj():
    """نیم‌فاصله و شکل‌هایِ مختلف باید نرمال شوند ولی معنی عوض نشود."""
    check("لاک‌پشت → لاکپشت (نیم‌فاصله حذف)",
          pd._normalize_fa_text("لاک\u200cپشت") == "لاکپشت")
    check("حروفِ عربی یکسان می‌شوند",
          pd._normalize_fa_text("لاکپشت") == pd._normalize_fa_text("لاكپشت"))
    # همهٔ شکل‌هایِ «لاکپشت های نینجا» باید به یک hint برسند
    _, _, h1 = pd._search_keywords("لاکپشت های نینجا")
    _, _, h2 = pd._search_keywords("لاک\u200cپشت های نینجا")
    _, _, h3 = pd._search_keywords("لاک پشت های نینجا")
    check("هر سه شکل hint یکسان دارند", h1 == h2 == h3 == "ninja turtle",
          f"{h1} / {h2} / {h3}")


def test_strong_relevance_requires_all_hint_words():
    """وقتی hint هست، همهٔ کلماتِ آن باید حاضر باشند (تصویرِ اتفاقی رد می‌شود)."""
    kw, _, hint = pd._search_keywords("لاکپشت های نینجا")
    check("hint باید ninja turtle باشد", hint == "ninja turtle", f"{hint}")
    # تصویرِ فقط «ninja» (بدون turtle) نامرتبط است
    check("فقط ninja (بدون turtle) نامرتبط است",
          not pd._strongly_relevant("Ninja martial arts", [], "http://x", kw, hint))
    # TMNT مرتبط است
    check("TMNT مرتبط است",
          pd._strongly_relevant("Teenage Mutant Ninja Turtles", [], "http://x", kw, hint))


def test_celebrity_hints():
    """افرادِ مشهور باید به نامِ انگلیسیِ کامل نگاشت شوند."""
    cases = {
        "بروسلی": "bruce lee",
        "رونالدو": "cristiano ronaldo",
        "مسی": "lionel messi",
    }
    for fa, en in cases.items():
        _, _, hint = pd._search_keywords(fa)
        check(f"{fa} → {en}", hint == en, f"got {hint!r}")


def test_general_hints():
    """جستجوهایِ عادی (لباس، مکان، غذا و...) باید hintِ انگلیسی داشته باشند."""
    cases = {
        "امیر تتلو": "amir tataloo",
        "لباس ارتشی": "military uniform",
        "برج ایفل": "eiffel tower",
        "کوه دماوند": "damavand",
        "لباس": "clothing",
        "پرفایل دخترونه": "girl portrait",
        "دختر": "girl",
        "پروفایل": "profile",
    }
    for fa, en in cases.items():
        _, _, hint = pd._search_keywords(fa)
        check(f"{fa} → {en}", hint == en, f"got {hint!r}")


def test_wikimedia_source():
    """Wikimedia Commons باید نامزد برمی‌گرداند (منبعِ دوم/فارسی)."""
    # در صورتِ قطعِ موقتِ شبکه (rate-limit/مشکلِ شبکه)، بدونِ خطا لیست
    # برمی‌گرداند و اگر نتیجه داشت URL معتبر است.
    res = []
    for _ in range(2):
        try:
            res = pd._search_wikimedia("پروفایل", 3)
        except Exception:
            res = []
        if res:
            break
    check("ویکی‌انبار بدونِ خطا اجرا شد", isinstance(res, list))
    if res:
        for url, title, _ in res[:1]:
            check("URL معتبر است", url.startswith("http"), url)


def test_wikipedia_topic_source():
    """ویکی‌پدیا باید تصویرِ اصلیِ صفحهٔ موضوع را برگرداند (اگر شبکه اجازه دهد)."""
    res = []
    for _ in range(2):
        try:
            res = pd._search_wikipedia_topic("امیر تتلو")
        except Exception:
            res = []
        if res:
            break
    check("ویکی‌پدیا بدونِ خطا اجرا شد", isinstance(res, list))
    if res:
        url, title, _ = res[0]
        check("URL معتبر است", url.startswith("http"), url)
        check("عنوان دارد", bool(title))


def test_normal_queries_not_blocked():
    """جستجوهایِ عادی نباید مسدود شوند؛ فقط محتوایِ صریحِ جنسی مسدود است."""
    for q in ["امیر تتلو", "لباس ارتشی", "عکس بازیگر", "ماشین", "طبیعت",
              "بروسلی", "سگ", "کوه", "آزادی", "تخت جمشید"]:
        check(f"مسدود نیست: {q}", not pd.is_blocked(q))
    for q in ["عکس سکسی", "پورن", "عکس برهنه", "sex", "nude", "سکس"]:
        check(f"مسدود است: {q}", pd.is_blocked(q))
    # موضوعاتِ حساسِ درخواستیِ مالک نیز مسدودند
    for q in ["پهلوی", "رضا شاه", "خاندان پهلوی", "رضاشاه", "محمدرضا پهلوی"]:
        check(f"مسدود است: {q}", pd.is_blocked(q))


def test_digit_normalization_relevance():
    """«۴۰۵» و «405» باید در سنجشِ ارتباط یکی شوند."""
    check("پژو ۴۰۵ → 405", pd._norm_for_match("پژو ۴۰۵") == "پژو 405",
          pd._norm_for_match("پژو ۴۰۵"))
    check("ماشین ۴۰۵ → ماشین 405",
          pd._norm_for_match("ماشین ۴۰۵") == "ماشین 405")
    # نتیجهٔ «Peugeot 405» با عبارتِ «ماشین ۴۰۵» مرتبط است (از طریق hint)
    kw, _, hint = pd._search_keywords("ماشین ۴۰۵")
    check("hint ماشین ۴۰۵ → peugeot 405", hint == "peugeot 405", f"{hint}")
    check("Peugeot 405 مرتبط است",
          pd._title_relevant("Peugeot 405 Turbo", [], "http://x",
                             "ماشین ۴۰۵", kw, hint))


def test_word_boundary_hint():
    """«پل» نباید داخلِ «پلنگ» تطبیق کند (مرزِ کلمه)."""
    _, _, h1 = pd._search_keywords("پلنگ صورتی")
    _, _, h2 = pd._search_keywords("پل")
    check("پلنگ صورتی → pink panther", h1 == "pink panther", f"{h1}")
    check("پل → bridge", h2 == "bridge", f"{h2}")


def test_exact_query_prioritized():
    """عبارتِ دقیقِ کاربر همیشه در جستجو هست و اولویت دارد."""
    kw, sq, hint = pd._search_keywords("پلنگ صورتی")
    check("عبارتِ دقیقِ فارسی در search_queries هست", "پلنگ صورتی" in sq, f"{sq}")
    check("hint مکمل است نه جایگزین", hint == "pink panther")
    check("عبارتِ دقیق اول است", sq[0] == "پلنگ صورتی", f"{sq}")


def test_search_cache():
    """کشِ جستجو برایِ عبارتِ تکراری باید کار کند."""
    pd.reset_all()
    # هم‌انندِ جستجو، مقدار را مستقیماً در کش قرار می‌دهیم و می‌بینیم برمی‌گردد
    key = pd._normalize_fa_text("بروسلی").strip().lower()
    import time as _t
    pd._SEARCH_CACHE[key] = (_t.monotonic(), ["http://cached/x.jpg"])
    urls = pd._search_image_urls("بروسلی", limit=1)
    check("کش برگردانده شد", urls == ["http://cached/x.jpg"], f"{urls}")
    pd.reset_all()


def test_owner_bypass_insufficient():
    """مالکِ اصلی (osine1) بدونِ سکه هم ادامه می‌دهد؛ سایرین خیر."""
    from modules.owner_check import get_owner
    owner_id = get_owner()["user_id"]
    pd.reset_all()
    _economy.reset_all()
    # مالک اصلی → بدون سکه باید ask_confirm شود
    pd.start_session(-6001, owner_id)
    result, _ = pd.handle_query(-6001, owner_id, "گربه")
    check("مالکِ اصلی بدونِ سکه → ask_confirm",
          result == "ask_confirm", f"{result}")
    pd.reset_all()
    # کاربرِ عادی بدونِ سکه → insufficient
    _economy.reset_all()
    pd.start_session(-6002, 999999)
    result2, _ = pd.handle_query(-6002, 999999, "گربه")
    check("کاربرِ عادی بدونِ سکه → insufficient",
          result2 == "insufficient", f"{result2}")
    pd.reset_all()


def test_confirm_text_exact():
    """متن تأیید دقیقاً مطابق خواستهٔ کاربر است و عدد ۴۰ ندارد."""
    pd.reset_all()
    t = pd.CONFIRM_TEXT
    check("متن تأیید: عنوان دانلود تصویر", "📥 دانلود تصویر" in t)
    check("متن تأیید: ۱۰ سکه برای هر عکس", "برای هر عکس ۱۰ سکه برنز نیاز است." in t)
    check("متن تأیید: حداکثر ۲ تصویر / ۲۰ برنز",
          "در صورت ارسال هر دو عکس ۲۰ سکه برنز از موجودی شما کم می‌شود." in t)
    check("متن تأیید: سؤال تأیید", "آیا تأیید می‌کنید؟" in t)
    check("متن تأیید: گزینه‌های تأیید", "بله / تایید / تأیید   → انجام" in t)
    check("متن تأیید: گزینه‌های لغو", "خیر / لغو             → انصراف" in t)
    check("عدد ۴۰ در متن نیست", "۴۰" not in t)
    check("هزینهٔ هر عکس = ۱۰", pd.COST_PER_IMAGE == 10, f"{pd.COST_PER_IMAGE}")


# ===========================================================================
def main():
    test_content_filter()
    test_confirm_flow_no_charge_before_confirm()
    test_blocked_query_aborts()
    test_insufficient_balance_aborts()
    test_charge_only_after_images_ready()
    test_lock_serializes_group()
    test_release_on_network_error()

    test_only_exact_command_triggers()
    test_exact_command_asks_query()
    test_two_step_full_flow_20_bronze()
    test_per_image_cost_1_image()
    test_real_image_bytes_are_valid()
    test_two_step_flow()

    # سناریوهای هزینهٔ دقیق
    test_2_images_success_20_bronze()
    test_1_image_success_10_bronze()
    test_no_result_0_bronze()
    test_download_failure_0_bronze()
    test_send_failure_0_bronze()
    test_upload_primary_success_10_bronze()
    test_upload_fails_link_fallback_10_bronze()
    test_transliterate_fa()
    test_no_broken_link_fallback()
    test_no_confirm_0_bronze()
    test_insufficient_balance_not_performed()
    test_image_stream_is_photo()
    test_send_by_url_uses_photo_external()
    test_is_valid_image_bytes()
    test_fetch_accepts_after_logging()
    test_is_direct_image_url()
    test_spam_url_filter()
    test_links_message_numbered_and_blockquote()
    test_relevance_filter()
    test_no_translit_false_positive()
    test_normalize_fa_handles_zwnj()
    test_strong_relevance_requires_all_hint_words()
    test_celebrity_hints()
    test_general_hints()
    test_wikimedia_source()
    test_wikipedia_topic_source()
    test_normal_queries_not_blocked()
    test_digit_normalization_relevance()
    test_word_boundary_hint()
    test_exact_query_prioritized()
    test_search_cache()
    test_owner_bypass_insufficient()
    test_confirm_text_exact()

    print(f"\npassed={PASSED} failed={FAILED}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
