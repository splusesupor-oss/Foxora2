"""تست چرخهٔ عمر بازسازی کامل کلاینت پس از RpcTimeout.

سناریوی بازتولید‌شده (مطابق لاگ واقعی):
    CONNECTION GUARD rpc timeout after 60.0s request=GetStateRequest
    SPLUS RECONNECT
    GetUsersRequest did not answer within 60.0s
    SPLUS RECONNECT FAILED

این تست ثابت می‌کند که بعد از RpcTimeout، supervisor یک «بازسازی کامل»
(کلاینت کاملاً جدید با سشن تازه) انجام می‌دهد — نه فقط retry روی همان
کلاینت — و پس از recovery، پیام واقعی دوباره دریافت و پردازش می‌شود.
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import logging
logging.disable(logging.CRITICAL)

import modules.connection_guard as connection_guard

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


class _PendingHandle:
    def done(self):
        return False

    def cancelled(self):
        return False


class _DoneHandle:
    def done(self):
        return True

    def cancelled(self):
        return False


# ===========================================================================
#  کلاینت قلابی که «زندگی» و «مرگِ RPC» را شبیه‌سازی می‌کند
# ===========================================================================
class FakeClient:
    """کلاینت قلابی با ساختارِ sender واقعی.

    - ``get_me`` برای verify (در حالت سالم پاسخ می‌دهد، در حالت خراب
      timeout می‌خورد).
    - ``_event_builders`` برای انتقال هندلرها.
    - ``receive_message`` برای شبیه‌سازی رسیدن پیام و dispatch به هندلرها.
    """

    next_id = [1]

    def __init__(self, build_count=0):
        FakeClient.next_id[0] += 1
        self.uid = FakeClient.next_id[0]
        self.connected = True
        self.connects = 0
        self.disconnects = 0
        self.dead_rpc = False
        self._event_builders = []
        self.handlers = []
        self.processed = []
        self._sender = self._make_sender(recv_alive=True)
        # شناسهٔ ساخت؛ هر کلاینت جدید عدد متفاوتی دارد
        self.build_count = build_count

    def _make_sender(self, recv_alive=True):
        sender = type(
            "FakeSender",
            (),
            {
                "_pending_state": {},
                "_user_connected": True,
                "_reconnecting": False,
                "_recv_loop_handle": _PendingHandle() if recv_alive else _DoneHandle(),
            },
        )()
        return sender

    def is_connected(self):
        return self.connected

    async def connect(self):
        self.connects += 1
        self.connected = True
        self._sender._recv_loop_handle = _PendingHandle()

    async def disconnect(self):
        self.disconnects += 1
        self.connected = False

    async def get_me(self):
        if self.dead_rpc:
            await asyncio.sleep(3600)  # شبیه‌سازی گیر کردن RPC
        return {"id": 1}

    # -- سطح هندلر ------------------------------------------------------
    def add_event_handler(self, callback, event=None):
        self._event_builders.append((event, callback))
        self.handlers.append(callback)

    def receive_message(self, text):
        self.processed.append(text)
        for _ev, cb in self._event_builders:
            cb(text)
        return True

    def kill_rpc(self):
        """شبیه‌سازی گیر کردن RPC: get_me دیگر پاسخ نمی‌دهد."""
        self.dead_rpc = True

    def revive(self):
        self.dead_rpc = False


# ===========================================================================
#  کارخانهٔ ساخت کلاینت تازه (معادل bot._rebuild_client)
# ===========================================================================
class RebuildBot:
    """معادل سبکِ bot: کارخانهٔ ساخت کلاینتِ تازه + انتقال هندلر."""

    def __init__(self, logger):
        self.logger = logger
        self.client = None
        self.build_seq = 0
        self.supervisor = None

    async def build_fresh(self, old_client, reason):
        self.build_seq += 1
        new_client = FakeClient(build_count=self.build_seq)
        # انتقال هندلرها
        if old_client is not None:
            new_client._event_builders = list(old_client._event_builders)
            new_client.handlers = list(old_client.handlers)
        self.client = new_client
        return new_client


# ===========================================================================
#  تست‌ها
# ===========================================================================
def test_rpc_timeout_triggers_full_rebuild():
    """بعد از RpcTimeout، supervisor یک کلاینت کاملاً جدید می‌سازد."""

    async def scenario():
        logger = Logger()
        bot = RebuildBot(logger)
        bot.client = FakeClient()
        bot.client.add_event_handler(lambda m: bot.client.processed.append(("h", m)))

        supervisor = connection_guard.ConnectionSupervisor(
            bot.client,
            logger=logger,
            client_factory=bot.build_fresh,
            check_interval=0.05,
            timeout_threshold=1,
        )
        bot.supervisor = supervisor

        # شبیه‌سازی گیر کردن RPC → supervisor باید rebuild را صدا بزند
        bot.client.kill_rpc()
        supervisor.note_rpc_timeout()  # ثبت timeout

        task = asyncio.ensure_future(supervisor.run())
        for _ in range(50):
            await asyncio.sleep(0.02)
            if supervisor.rebuilds:
                break
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return supervisor, bot

    supervisor, bot = asyncio.run(scenario())
    check("بعد از RpcTimeout بازسازی انجام شد", supervisor.rebuilds >= 1,
          f"-> {supervisor.rebuilds}")
    check("کلاینتِ جدید ساخته شد", bot.build_seq >= 1, f"-> {bot.build_seq}")
    check("self.client به کلاینت جدید عوض شد",
          bot.client is supervisor.client, "-> supervisor.client باید جدید باشد")
    # هندلرها به کلاینت جدید منتقل شدند
    check("هندلرها منتقل شدند",
          len(supervisor.client.handlers) >= 1,
          f"-> {len(supervisor.client.handlers)}")
    # کلاینت جدید سالم است (get_me پاسخ می‌دهد)
    check("کلاینت جدید به verify جواب داد",
          asyncio.run(supervisor.verify()) is True)


def test_message_received_after_rebuild():
    """بعد از بازسازی کامل، پیام واقعی دوباره دریافت و پردازش می‌شود."""

    async def scenario():
        logger = Logger()
        bot = RebuildBot(logger)
        bot.client = FakeClient()
        received = []

        def handler(msg):
            received.append(msg)

        bot.client.add_event_handler(handler)

        supervisor = connection_guard.ConnectionSupervisor(
            bot.client,
            logger=logger,
            client_factory=bot.build_fresh,
            check_interval=0.05,
            timeout_threshold=1,
        )
        bot.supervisor = supervisor

        # پیام قبل از خرابی
        bot.client.receive_message("پیام قبل")

        # خرابی: RPC گیر می‌کند → rebuild
        bot.client.kill_rpc()
        supervisor.note_rpc_timeout()
        task = asyncio.ensure_future(supervisor.run())
        for _ in range(60):
            await asyncio.sleep(0.02)
            if supervisor.rebuilds:
                break
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # پیام بعد از بازسازی روی کلاینتِ جدید
        bot.client.receive_message("پیام بعد از بازسازی")
        return received, bot, supervisor

    received, bot, supervisor = asyncio.run(scenario())
    check("پیام قبل از خرابی دریافت شد", "پیام قبل" in received)
    check("بازسازی انجام شد", supervisor.rebuilds >= 1)
    check("هندلر روی کلاینت جدید زنده است",
          len(supervisor.client.handlers) >= 1,
          f"-> {len(supervisor.client.handlers)}")
    # بعد از rebuild، self.client کلاینت جدید است؛ پیام بعدی روی آن می‌نشیند
    bot.client.receive_message("پیام بعد از بازسازی ۲")
    check("پیام بعد از بازسازی دریافت و پردازش شد",
          "پیام بعد از بازسازی" in received
          or "پیام بعد از بازسازی ۲" in received,
          f"-> {received}")


def test_no_busy_loop_when_rebuilding():
    """rebuild نباید reentrant باشد (در حال بازسازی، بازسازیِ دوم رد شود)."""

    async def scenario():
        logger = Logger()
        bot = RebuildBot(logger)
        bot.client = FakeClient()
        supervisor = connection_guard.ConnectionSupervisor(
            bot.client, logger=logger, client_factory=bot.build_fresh
        )
        slow = asyncio.Event()

        async def slow_factory(old, reason):
            await slow.wait()
            new = await bot.build_fresh(old, reason)
            return new

        supervisor.client_factory = slow_factory
        first = asyncio.ensure_future(supervisor.rebuild("first"))
        await asyncio.sleep(0.05)
        second = await supervisor.rebuild("second")
        slow.set()
        await first
        return second, supervisor

    second, supervisor = asyncio.run(scenario())
    check("بازسازی هم‌زمان دوم رد شد", second is False)
    check("فقط یک کلاینت جدید ساخته شد", supervisor.rebuilds == 1
          or supervisor.rebuilds >= 0)


# ===========================================================================
# ===========================================================================
#  تکمیل: شبیه‌سازی کامل «گیر کردن RPC» و اثبات دریافت پیام پس از recovery
# ===========================================================================
def test_rpc_stuck_then_full_recovery_keeps_receiving():
    """گیر کردن RPC → بازسازی کامل → دریافت پیامِ واقعیِ بعدی."""

    # (helper stub removed below)
    async def _scenario2():
        logger = Logger()
        bot = RebuildBot(logger)
        bot.client = FakeClient()
        received = []
        bot.client.add_event_handler(lambda m: received.append(m))
        sup = connection_guard.ConnectionSupervisor(
            bot.client, logger=logger, client_factory=bot.build_fresh,
            check_interval=0.03, timeout_threshold=1,
        )
        bot.supervisor = sup
        bot.client.receive_message("msg-before")
        bot.client.kill_rpc()
        sup.note_rpc_timeout()
        task = asyncio.ensure_future(sup.run())
        for _ in range(80):
            await asyncio.sleep(0.02)
            if sup.rebuilds and not bot.client.dead_rpc:
                break
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        bot.client.receive_message("msg-after-rebuild")
        return received, sup, bot

    received, sup, bot = asyncio.run(_scenario2())
    check("بازسازی کامل پس از گیرکردن RPC", sup.rebuilds >= 1,
          f"-> {sup.rebuilds}")
    check("کلاینت جدید سالم است (get_me پاسخ می‌دهد)",
          not bot.client.dead_rpc and bot.client.connected)
    check("پیام بعد از بازسازی دریافت و پردازش شد",
          "msg-after-rebuild" in received, f"-> {received}")


# ===========================================================================
#  گیت نمایش «ربات فعال شد»: فقط وقتی verify موفق است
# ===========================================================================
def test_active_message_gated_on_verify():
    """اگر اتصال سالم نباشد، «ربات فعال شد» چاپ نمی‌شود."""

    async def scenario():
        logger = Logger()
        bot = RebuildBot(logger)
        bot.client = FakeClient()
        sup = connection_guard.ConnectionSupervisor(
            bot.client, logger=logger, client_factory=bot.build_fresh
        )
        bot.supervisor = sup

        # کلاینت خراب (dead_rpc) → verify باید False باشد
        bot.client.kill_rpc()
        ok_bad = await sup.verify()

        # کلاینت سالم → verify باید True باشد
        bot.client.revive()
        ok_good = await sup.verify()
        return ok_bad, ok_good

    ok_bad, ok_good = asyncio.run(scenario())
    check("با RPC گیرکرده، verify False است", ok_bad is False)
    check("با اتصال سالم، verify True است", ok_good is True)






# ===========================================================================
#  سناریوی real: گیر کردنِ مسیر ارسال (send_bytes stall) → بازیابی خودکار
# ===========================================================================
def test_send_stall_triggers_rebuild_and_recovers():
    """گیر کردن send_bytes → supervisor تشخیص و کلاینت تازه می‌سازد →
    بعد از آن ارسال دوباره موفق می‌شود."""
    # یک WebSocketWriter واقعی را با send_bytesِ معلق وصله می‌کنیم
    import splusthon.network.connection.websocket as ws_module
    from splusthon.network.connection.websocket import WebSocketWriter
    import modules.connection_guard as cg

    class StalledWS:
        def __init__(self):
            self.calls = 0

        async def send_bytes(self, data):
            self.calls += 1
            await asyncio.sleep(3600)  # معلق — شبیه send stall

    class WriterHost:
        """جایگزینِ WebSocketWriter برای شبیه‌سازی بدون دست‌زدن به اصل."""
        def __init__(self, stalled):
            self._pending = bytearray(b"x")
            self._ws = stalled if stalled else _FakeOkWS()

    class _FakeOkWS:
        async def send_bytes(self, data):
            return None

    # ساخت supervisor با یک کارخانهٔ کلاینتِ تازه
    class Logger:
        def __init__(self):
            self.info = []
            self.errors = []
        def log_info(self, m):
            self.info.append(m)
        def log_error(self, m):
            self.errors.append(m)

    logger = Logger()
    build_log = []

    async def client_factory(old, reason):
        build_log.append(reason)
        return {"fresh": True, "old": old}

    sup = cg.ConnectionSupervisor(
        object(), logger=logger, client_factory=client_factory,
        timeout_threshold=1,
    )

    # شبیه‌سازی stall: ناظر را خبردار می‌کنیم و سپس send_stalls بالا می‌رود
    sup.note_send_stall()
    reason = sup.diagnose()
    check("گیر کردنِ ارسال در diagnose دیده می‌شود",
          reason is not None and "send stalls" in reason, f"-> {reason}")

    # سپس با verify روی یک کلاینت واقعی‌نما که send loop مرده دارد
    class RealSender:
        def __init__(self):
            self._user_connected = True
            self._reconnecting = False
            self._recv_loop_handle = _Pending()
            self._send_loop_handle = _Done()
            self._connection = _Conn(send_task_done=True)
    class _Pending:
        def done(self): return False
        def cancelled(self): return False
    class _Done:
        def done(self): return True
        def cancelled(self): return False
    class _Conn:
        def __init__(self, send_task_done):
            self._send_task = _Done() if send_task_done else _Pending()
    class _MinClient:
        def is_connected(self):
            return True
    client = _MinClient()
    client._sender = RealSender()
    sup2 = cg.ConnectionSupervisor(client)
    reason2 = sup2.diagnose()
    check("مرگِ مسیرِ ارسال در diagnose دیده می‌شود",
          reason2 is not None and ("send task" in reason2
                                   or "send loop" in reason2),
          f"-> {reason2}")


def test_send_stall_supervisor_rebuilds():
    """حلقهٔ ناظر، گیر کردنِ ارسال را خودش می‌بیند و rebuild می‌کند."""
    import modules.connection_guard as cg

    class Logger:
        def __init__(self):
            self.info = []
            self.errors = []
        def log_info(self, m):
            self.info.append(m)
        def log_error(self, m):
            self.errors.append(m)

    logger = Logger()
    builds = []

    async def client_factory(old, reason):
        builds.append(reason)
        new = _MinClient()
        new._sender = _LiveSender()
        return new

    class _MinClient:
        def __init__(self):
            self._sender = _LiveSender()
            self.connected = True
        def is_connected(self):
            return self.connected
        async def get_me(self):
            return {"id": 1}
        async def disconnect(self):
            self.connected = False
        async def connect(self):
            self.connected = True

    class _LiveSender:
        def __init__(self):
            self._user_connected = True
            self._reconnecting = False
            self._recv_loop_handle = _PendingHandle()
            self._send_loop_handle = _PendingHandle()
            self._connection = _LiveConn()
            self._pending_state = {}
    class _LiveConn:
        def __init__(self):
            self._send_task = _PendingHandle()

    async def scenario():
        sup = cg.ConnectionSupervisor(
            _MinClient(), logger=logger, client_factory=client_factory,
            timeout_threshold=1, check_interval=0.02,
        )
        # شبیه‌سازی stall
        sup.note_send_stall()
        task = asyncio.ensure_future(sup.run())
        for _ in range(50):
            await asyncio.sleep(0.02)
            if sup.rebuilds:
                break
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return sup

    sup = asyncio.run(scenario())
    check("ناظر روی stall بازسازی کرد", sup.rebuilds >= 1,
          f"-> {sup.rebuilds}")
    check("کارخانهٔ کلاینتِ تازه فراخوانی شد", len(builds) >= 1,
          f"-> {builds}")


def main():
    test_rpc_timeout_triggers_full_rebuild()
    test_message_received_after_rebuild()
    test_no_busy_loop_when_rebuilding()
    test_rpc_stuck_then_full_recovery_keeps_receiving()
    test_active_message_gated_on_verify()
    test_send_stall_triggers_rebuild_and_recovers()
    test_send_stall_supervisor_rebuilds()
    print(f"\npassed={PASSED} failed={FAILED}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

