"""تست بازیابی واقعی لایه‌ی اتصال: مرگِ بی‌صدای Receive.

این فایل با «کد واقعی SPlusthon» کار می‌کند (رمزنگاری واقعی، `MTProtoState`
واقعی، وصله‌های `connection_guard` واقعی) و فقط سوکت/سِندرِ شبکه را شبیه‌سازی
می‌کند — همان فلسفه‌ی `tests/test_connection_guard.py`.

چه چیزی ثابت می‌شود:
  ۱) «هیتمن‌ترِ لایو-نس» واقعاً در مسیر رمزگشاییِ واقعی نصب شده است:
     هر قابِ معتبری که `decrypt_message_data` رمزگشایی کند (حتی قابِ کهنه‌ی
     سشن قبلی) باید `tracker.last_data_ts` را تازه کند.
  ۲) «مرگِ بی‌صدا» (دریافت هیچ قابی برای مدت طولانی، در حالی که کلاینت هنوز
     خودش را متصل می‌داند) توسط ناظر تشخیص داده می‌شود.
  ۳) مرگِ بی‌صدای حلقه‌ی دریافت (`_recv_loop_handle` تمام‌شده ولی متصل) تشخیص
     داده می‌شود.
  ۴) ناظر، کلاینت را بازسازی می‌کند؛ هندلر پیام دست‌نخورده می‌ماند و پس از
     بازسازی، پیام تازه دوباره دریافت و پردازش می‌شود.

یادداشتِ صادقانه: اتصالِ زنده به سرور واقعی سروش (حساب واقعی) در این محیط ممکن
نیست — نه حسابی در دسترس است و نه مجاز به استفاده از credentialهای موجود در
`.env`/`session.session` هستیم. برای همین این تست، کل لایه‌ی شبکه‌ای SPlusthon را
با کد واقعی و سوکتِ شبیه‌سازی‌شده می‌سنجد.
"""

import asyncio
import logging
import os
import struct
import sys
import time
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.disable(logging.CRITICAL)

PASSED = 0
FAILED = 0


def check(name, condition, detail=""):
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  ok  {name}")
    else:
        FAILED += 1
        print(f"FAIL  {name} {detail}")


class Loggers(dict):
    def __missing__(self, key):
        value = logging.getLogger(key)
        self[key] = value
        return value


class RecordingLogger:
    def __init__(self):
        self.info = []
        self.errors = []

    def log_info(self, message):
        self.info.append(message)

    def log_error(self, message):
        self.errors.append(message)


# aiohttp قلابی تا کد واقعی websocket.py بدون شبکه اجرا شود
class _WSMsgType:
    CLOSE = 1
    CLOSING = 2
    CLOSED = 3
    ERROR = 4
    BINARY = 5


class _FakeWS:
    def __init__(self):
        self._writer = None
        self.closed = False

    async def receive(self):
        await asyncio.sleep(3600)

    async def send_bytes(self, data):
        pass

    async def close(self):
        self.closed = True


class _FakeSession:
    def __init__(self):
        self.closed = False

    async def ws_connect(self, *args, **kwargs):
        return _FakeWS()

    async def close(self):
        self.closed = True


class _FakeAiohttp:
    WSMsgType = _WSMsgType

    @staticmethod
    def ClientSession(*args, **kwargs):
        return _FakeSession()

    @staticmethod
    def ClientTimeout(*args, **kwargs):
        return None


import splusthon.network.connection.websocket as ws_module  # noqa: E402

ws_module.aiohttp = _FakeAiohttp

from splusthon import helpers  # noqa: E402
from splusthon.crypto import AES, AuthKey  # noqa: E402
from splusthon.network.connection.websocket import ConnectionWebSocket  # noqa: E402
from splusthon.network.mtprotostate import MTProtoState  # noqa: E402
from splusthon.tl.types import Pong  # noqa: E402

from modules import connection_guard  # noqa: E402


# ===========================================================================
#  ابزار: قابِ رمزشده‌ی معتبر سمت سرور (کد واقعی MTProtoState)
# ===========================================================================
def server_frame(auth_key, session_id, salt=0):
    body = bytes(Pong(msg_id=1, ping_id=2))
    msg_id = (int(time.time()) << 32) | 1
    if msg_id % 2 == 0:
        msg_id += 1
    inner = struct.pack("<qii", msg_id, 1, len(body)) + body
    data = struct.pack("<qq", salt, session_id) + inner
    padding = os.urandom(-(len(data) + 12) % 16 + 12)
    msg_key = sha256(auth_key.key[96:96 + 32] + data + padding).digest()[8:24]
    aes_key, aes_iv = MTProtoState._calc_key(auth_key.key, msg_key, False)
    return (
        struct.pack("<Q", auth_key.key_id)
        + msg_key
        + AES.encrypt_ige(data + padding, aes_key, aes_iv)
    )


# ===========================================================================
#  ۱) هیتمن‌ترِ لایو-نس در مسیر رمزگشاییِ واقعی
# ===========================================================================
def test_real_decrypt_tracks_liveness():
    """قابِ معتبرِ واقعی، `last_data_ts` را در حالتِ واقعی تازه می‌کند."""
    auth = AuthKey(os.urandom(256))

    class Isolated(MTProtoState):
        pass

    tracker = connection_guard.StaleSessionTracker()
    logger = RecordingLogger()
    installed = connection_guard.install_stale_session_filter(
        Isolated, tracker, logger
    )
    check("وصله روی کلاسِ واقعی نشست", installed is True)

    state = Isolated(auth, loggers=Loggers())
    check("قبل از دریافت، لایو-نس نیست", tracker.last_data_ts is None)

    good = server_frame(auth, state.id)
    msg = state.decrypt_message_data(good)
    check("قاب سالم واقعی رمزگشایی شد", msg is not None)
    check("لایو-نس پس از قابِ سالم تازه شد",
          tracker.last_data_ts is not None)

    # قابِ کهنه‌ی سشن قبلی هم «رسیدنِ داده» است → لایو-نس باید تازه بماند
    old_session = state.id
    state.reset()
    check("reset سشن تازه می‌سازد", state.id != old_session)
    stale = server_frame(auth, old_session)
    before = tracker.last_data_ts
    result = state.decrypt_message_data(stale)
    check("قابِ کهنه به‌جای خطا None برمی‌گرداند", result is None)
    check("قابِ کهنه هم لایو-نس را تازه نگه داشت",
          tracker.last_data_ts is not None and tracker.last_data_ts >= before)
    check("قابِ کهنه شمرده شد", tracker.total == 1)


# ===========================================================================
#  ابزار: یک کلاینتِ سِندر-ساختار که لایه‌ی شبکه‌ی واقعی را مدل می‌کند
# ===========================================================================
class _DoneHandle:
    def done(self):
        return True

    def cancelled(self):
        return False


class _PendingHandle:
    def done(self):
        return False

    def cancelled(self):
        return False


class RecoveryClient:
    """کلاینت قلابی با همان سطحِ ساختارِ `MTProtoSender` واقعی.

    پیام‌ها با «دریافت یک قاب» مدل می‌شوند: هر قاب، `note_received()` را صدا
    می‌زند و یک هندلرِ ثبت‌شده را فراخوانی می‌کند (شبیه dispatch). پس از
    بازسازی، اگر هندلرها دست‌نخورده بمانند، پیامِ بعدی دوباره پردازش می‌شود.
    """

    def __init__(self, logger=None):
        self.connected = True
        self.connects = 0
        self.disconnects = 0
        self.handlers = []
        self.processed = []
        self._sender = self._make_sender(recv_alive=True)
        self._recv_handle_done = False
        self.logger = logger

    def _make_sender(self, recv_alive):
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

    # -- سطح هندلر ------------------------------------------------------
    def register_handler(self, name):
        self.handlers.append(name)

    def receive_message(self, text, tracker):
        """شبیه‌سازی رسیدنِ یک قابِ پیام و dispatch آن به هندلرها."""
        tracker.note_received()
        self.processed.append(text)
        for name in self.handlers:
            # هندلرهای سالم، پیام را می‌گیرند؛ خطا نباید حلقه را بکشد
            self.logger.log_info(f"HANDLER {name} processed {text!r}")
        return True

    # -- شبیه‌سازی خرابی -------------------------------------------------
    def kill_recv_loop_silently(self):
        """حلقه‌ی دریافت خارج می‌شود ولی کلاینت هنوز «متصل» است (مرگِ بی‌صدا)."""
        self._sender._recv_loop_handle = _DoneHandle()
        self.connected = True


# ===========================================================================
#  ۲) تشخیص و بازیابی مرگِ بی‌صدا
# ===========================================================================
def test_recovery_from_silent_death():
    """ناظر، سکوتِ طولانی را می‌بیند و بازسازی می‌کند؛ پیام بعدی دوباره می‌رسد."""

    async def scenario():
        logger = RecordingLogger()
        client = RecoveryClient(logger=logger)
        tracker = connection_guard.StaleSessionTracker()
        supervisor = connection_guard.ConnectionSupervisor(
            client,
            logger=logger,
            tracker=tracker,
            receive_dead_threshold=120.0,
        )
        client.register_handler("main_handler")

        # مرحله‌ی سالم: پیام‌ها می‌رسند و لایو-نس تازه می‌ماند
        client.receive_message("سلام", tracker)
        healthy = supervisor.diagnose()
        for _ in range(5):
            client.receive_message("پیام", tracker)
        check("در حالت سالم بازسازی لازم نیست", healthy is None, f"-> {healthy}")

        # مرگِ بی‌صدا: داده قطع می‌شود (فقط ناظر باقی می‌ماند)
        tracker.last_data_ts = time.monotonic() - 600.0
        reason = supervisor.diagnose()
        check("مرگِ بی‌صدا تشخیص داده شد",
              reason is not None and "no data" in reason, f"-> {reason}")

        # بازسازی
        before_connects = client.connects
        await supervisor.rebuild(reason)
        check("کلاینت دوباره وصل شد", client.connects == before_connects + 1,
              f"-> {client.connects}")
        check("هندلرها دست‌نخورده ماندند", client.handlers == ["main_handler"],
              f"-> {client.handlers}")

        # پیام واقعی پس از بازسازی دوباره دریافت و پردازش می‌شود
        tracker.last_data_ts = time.monotonic()
        ok = client.receive_message("پیام پس از بازسازی", tracker)
        check("پیام پس از بازسازی پردازش شد", ok is True)
        check("پیام در صفِ پردازش ثبت شد",
              "پیام پس از بازسازی" in client.processed,
              f"-> {client.processed[-1:]!r}")

    asyncio.run(scenario())


def test_recovery_from_dead_recv_loop():
    """هندلِ تمام‌شده‌ی دریافت در حالی که کلاینت متصل است → بازسازی."""

    async def scenario():
        logger = RecordingLogger()
        client = RecoveryClient(logger=logger)
        supervisor = connection_guard.ConnectionSupervisor(client)
        check("در حالت سالم بازسازی لازم نیست", supervisor.diagnose() is None)

        client.kill_recv_loop_silently()
        reason = supervisor.diagnose()
        check("مرگِ حلقه‌ی دریافت تشخیص داده شد",
              reason is not None and "receive loop" in reason, f"-> {reason}")

        await supervisor.rebuild(reason)
        # بعد از بازسازی، هندلِ دریافت باید دوباره زنده باشد
        live = not client._sender._recv_loop_handle.done()
        check("حلقه‌ی دریافت پس از بازسازی زنده شد", live is True)
        check("کلاینت متصل ماند", client.connected is True)

    asyncio.run(scenario())


def test_recovery_loop_watches_silence_continuously():
    """حلقه‌ی ناظر باید به‌تنهایی، بدون پیامِ خطا، مرگِ بی‌صدا را بگیرد."""

    async def scenario():
        logger = RecordingLogger()
        client = RecoveryClient(logger=logger)
        tracker = connection_guard.StaleSessionTracker()
        tracker.note_received()
        tracker.last_data_ts = time.monotonic() - 9999.0  # خیلی کهنه
        supervisor = connection_guard.ConnectionSupervisor(
            client,
            logger=logger,
            tracker=tracker,
            receive_dead_threshold=0.05,
            check_interval=0.01,
        )
        task = asyncio.ensure_future(supervisor.run())
        for _ in range(50):
            await asyncio.sleep(0.01)
            if supervisor.rebuilds:
                break
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return supervisor.rebuilds, client.connects, supervisor.last_reason

    rebuilds, connects, reason = asyncio.run(scenario())
    check("ناظر روی سکوت، بدون خطا، بازسازی کرد", rebuilds >= 1,
          f"-> {rebuilds}")
    check("کلاینت دوباره وصل شد", connects >= 1, f"-> {connects}")
    check("دلیل، سکوت است",
          reason is not None and "no data" in reason, f"-> {reason}")


# ===========================================================================
def main():
    test_real_decrypt_tracks_liveness()
    test_recovery_from_silent_death()
    test_recovery_from_dead_recv_loop()
    test_recovery_loop_watches_silence_continuously()

    print(f"\npassed={PASSED} failed={FAILED}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
