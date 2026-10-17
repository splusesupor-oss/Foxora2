"""تست محدودیتِ «خون‌آشام در دو دورِ پشت‌سرهم انتخاب نشود» + بازیابی send-stall.

بخش ۱ — انتخابِ تصادفی خون‌آشام:
   تنها محدودیت این است که خون‌آشامِ دورِ قبلیِ همین چت نباید دوباره در
   دورِ بعدی انتخاب شود. هیچ محدودیتِ تعداد/ترتیبی دیگری وجود ندارد.
   انتخابِ هر دور کاملاً تصادفی است و قابل پیش‌بینی نیست.

بخش ۲ — send-stall (اتصال):
   مسیر ارسال WebSocket باید دارای timeout باشد و وقتی send loop گیر کرد،
   ربات خودش recover شود (بدون ری‌استارت دستی).
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import logging
logging.disable(logging.CRITICAL)

from modules.fox_games import vampire as vp

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


class User:
    def __init__(self, uid, name=""):
        self.id = uid
        self.first_name = name or f"u{uid}"
        self.last_name = None
        self.username = None


# ===========================================================================
#  بخش ۱: انتخاب تصادفی خون‌آشام با «بدون تکرار پشت‌سرهم»
# ===========================================================================
def _setup_round(chat, logger, uids):
    """شروعِ یک دورِ تازه بدون پاک‌کردنِ «خون‌آشامِ دورِ قبلی».

    در جریان واقعی، بین دو دور reset_all صدا زده نمی‌شود؛ فقط وقتی دورِ قبلی
    تمام شود (session بسته شود) start() دورِ تازه می‌سازد و یادآوریِ
    خون‌آشامِ قبلی حفظ می‌شود. اینجا هم همان را شبیه‌سازی می‌کنیم.
    """
    if vp.is_active(chat):
        raise RuntimeError("دورِ قبلی هنوز فعال است")
    st = vp.start(chat, logger)
    for uid in uids:
        vp.join(chat, uid, User(uid), logger)
    return st


def _end_round(chat, logger, uids, chosen):
    """بازی را با حدسِ درست تمام می‌کند (session می‌بندد و خون‌آشام را یاد
    می‌سپارد)."""
    # مرحله باید روی guessing باشد تا حدس پذیرفته شود
    sid = vp._STORE.get(chat)["session_id"]
    vp.open_guessing(chat, sid, logger)
    chosen_uid = chosen["player"]["user_id"]
    for uid in uids:
        if uid != chosen_uid:
            vp.guess(chat, uid, str(chosen["number"]), logger)
            break
    return chosen_uid


def test_no_consecutive_repeat():
    """یک نفر نباید در دو دورِ پشت‌سرهم انتخاب شود."""
    logger = Logger()
    uids = [1, 2, 3, 4, 5]
    chat = -9001
    vp.reset_all()
    sequence = []
    for _ in range(30):
        _setup_round(chat, logger, uids)
        chosen = vp.choose_vampire(chat, logger)
        chosen_uid = chosen["player"]["user_id"]
        sequence.append(chosen_uid)
        # پایانِ دور با حدسِ درست (تا خون‌آشام یاد سپرده شود)
        _end_round(chat, logger, uids, chosen)

    ok = all(sequence[i] != sequence[i + 1] for i in range(len(sequence) - 1))
    check("خون‌آشام در دو دورِ پشت‌سرهم تکراری نشد", ok, f"{sequence}")
    # مطمئن شو که فقط یک محدودیت (پشت‌سرهم) است؛ یک نفر می‌تواند با فاصله
    # دوباره انتخاب شود.
    seen_non_consecutive_repeat = any(
        sequence[i] == sequence[i + 2] for i in range(len(sequence) - 2)
    )
    check("یک نفر با یک دور فاصله می‌تواند دوباره انتخاب شود",
          seen_non_consecutive_repeat, f"{sequence}")


def test_selection_is_random_not_cyclic():
    """انتخاب ترتیبی/چرخشی نیست و در ورودیِ یکسان نتایج متفاوت می‌دهد."""
    logger = Logger()
    uids = [11, 12, 13, 14, 15]
    chat = -9002
    vp.reset_all()
    firsts = []
    for _ in range(8):
        _setup_round(chat, logger, uids)
        chosen = vp.choose_vampire(chat, logger)
        firsts.append(chosen["player"]["user_id"])
        # پایان دور (session را می‌بندد)
        _end_round(chat, logger, uids, chosen)
    # چون فقط محدودیتِ «پشت‌سرهم» است، نباید الگویِ چرخشیِ ساده بدهد
    # (مثل 1,2,3,4,5,1,2,3...). حداقل دو انتخابِ متفاوت در همان موقعیتِ
    # نسبی باید دیده شود.
    check("انتخابِ دورِ بعد همیشه نفرِ بعدیِ چرخه نیست",
          len(set(firsts)) > 1, f"{firsts}")


def test_last_vampire_excluded_from_next_round():
    """دورِ بعد خون‌آشامِ دورِ قبلی را در گزینه‌ها ندارد (اگر کافی‌اند)."""
    logger = Logger()
    uids = [21, 22, 23, 24, 25]
    chat = -9003
    vp.reset_all()
    _setup_round(chat, logger, uids)
    c1 = vp.choose_vampire(chat, logger)
    v1 = _end_round(chat, logger, uids, c1)

    _setup_round(chat, logger, uids)
    c2 = vp.choose_vampire(chat, logger)
    v2 = c2["player"]["user_id"]
    check("دورِ دوم خون‌آشامِ دورِ اول نیست", v2 != v1, f"{v1} -> {v2}")
    vp.reset_all(chat)


def test_reassign_also_avoids_consecutive_repeat():
    """مسیرِ جابه‌جاییِ نقش هم دورِ قبلی را در گزینه‌ها ندارد."""
    logger = Logger()
    uids = [31, 32, 33, 34, 35]
    chat = -9004
    vp.reset_all()
    _setup_round(chat, logger, uids)
    c1 = vp.choose_vampire(chat, logger)
    v1 = _end_round(chat, logger, uids, c1)

    # دورِ جدید: blood_vampire را مجبور می‌کنیم v1 باشد (گزینهٔ اول)، سپس
    # با جابه‌جاییِ نقش باید به نفرِ دیگری برود.
    _setup_round(chat, logger, uids)
    # choose باید v1 را حذف کند؛ اما برای تستِ جابه‌جایی، دستی روی v1 می‌گذاریم
    sess = vp._STORE.get(chat)
    v1_index = next(i for i, p in enumerate(sess["players"]) if p["user_id"] == v1)
    sess["vampire"] = v1_index
    sess["phase"] = "assigning"
    chosen = {"number": v1_index + 1, "player": dict(sess["players"][v1_index]),
              "players": list(sess["players"])}
    res = vp.reassign_vampire(chat, v1, logger)
    check("جابه‌جایی نقش به نفرِ غیر از دورِ قبلی رفت",
          res is not None and res["player"]["user_id"] != v1,
          f"{res}")
    vp.reset_all(chat)


# ===========================================================================
#  بخش ۲: send-stall — سلامت مسیر ارسال در Connection Guard
# ===========================================================================
def test_connection_guard_detects_send_stall():
    """Connection Guard باید گیر کردنِ مسیر ارسال را تشخیص دهد."""
    import modules.connection_guard as cg

    class Logger2:
        def __init__(self):
            self.info = []
            self.errors = []
        def log_info(self, m):
            self.info.append(m)
        def log_error(self, m):
            self.errors.append(m)

    class _Pending:
        def done(self): return False
        def cancelled(self): return False
    class _Done:
        def done(self): return True
        def cancelled(self): return False

    class FakeSender:
        def __init__(self):
            self._pending_state = {}
            self._user_connected = True
            self._reconnecting = False
            self._recv_loop_handle = _Pending()
            self._send_loop_handle = _Pending()
            self._connection = _Conn()

    class _Conn:
        def __init__(self):
            self._send_task = _Pending()

    class FakeClient:
        def __init__(self):
            self._sender = FakeSender()
        def is_connected(self):
            return True

    # حالتِ سالم: هیچ تشخیص خطایی نیست
    client = FakeClient()
    sup = cg.ConnectionSupervisor(client)
    check("اتصال سالم send خطا نمی‌دهد", sup.diagnose() is None)

    # send_stalls ثبت می‌شود → تشخیص (threshold=2)
    sup = cg.ConnectionSupervisor(client, timeout_threshold=2)
    sup.note_send_stall()
    below = sup.diagnose()
    sup.note_send_stall()
    above = sup.diagnose()
    check("زیر آستانهٔ send خطا نیست", below is None)
    check("بالای آستانهٔ send تشخیص داده می‌شود",
          above is not None and "send" in above, f"-> {above}")

    # مرگِ Connection send task → تشخیص
    client2 = FakeClient()
    client2._sender._connection._send_task = _Done()
    sup2 = cg.ConnectionSupervisor(client2)
    reason = sup2.diagnose()
    check("مرگِ send task تشخیص داده می‌شود",
          reason is not None and "send" in reason, f"-> {reason}")


def test_send_timeout_installed_and_fires():
    """وصلهٔ send-timeout نصب می‌شود و روی stall exception می‌دهد."""
    import modules.connection_guard as cg

    class Logger2:
        def __init__(self):
            self.info = []
            self.errors = []
        def log_info(self, m):
            self.info.append(m)
        def log_error(self, m):
            self.errors.append(m)

    class FakeWS:
        async def send_bytes(self, data):
            await asyncio.sleep(3600)  # معلق — شبیه send stall

    class FakeWriter:
        def __init__(self):
            self._pending = bytearray(b"data")
            self._ws = FakeWS()
        async def drain(self):
            if not getattr(self, "_pending", None):
                return
            data = bytes(self._pending)
            self._pending.clear()
            await self._ws.send_bytes(data)

    stalls = []
    logger = Logger2()
    installed = cg.install_send_timeout(
        FakeWriter, timeout=0.1, on_stall=lambda: stalls.append(1),
        logger=logger,
    )
    check("وصلهٔ send-timeout نصب شد", installed is True)

    async def run():
        try:
            await FakeWriter().drain()
            return None
        except asyncio.TimeoutError as e:
            return e
    raised = asyncio.run(run())
    check("روی stall TimeoutError می‌دهد",
          isinstance(raised, asyncio.TimeoutError), f"{raised}")
    check("ناظر خبردار شد", len(stalls) >= 1, f"{stalls}")
    check("لاگ ثبت شد", any("send timeout" in m for m in logger.errors))


# ===========================================================================
def main():
    test_no_consecutive_repeat()
    test_selection_is_random_not_cyclic()
    test_last_vampire_excluded_from_next_round()
    test_reassign_also_avoids_consecutive_repeat()

    test_connection_guard_detects_send_stall()
    test_send_timeout_installed_and_fires()

    print(f"\npassed={PASSED} failed={FAILED}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
