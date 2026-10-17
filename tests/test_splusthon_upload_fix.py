"""تست وصلهٔ آپلودِ فایل (media DC) — جلوگیری از
FILE_REQUEST_RECEIVED_ON_CONNECTION_SERVER در آپلودِ عکس.
"""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import logging
logging.disable(logging.CRITICAL)

from splusthon.tl.functions import upload as _up
from modules import splusthon_upload_fix as fx

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


# ---------------------------------------------------------------------------
#  انتخاب media DC از config
# ---------------------------------------------------------------------------
def _dc(dc_id, media_only=False, ip="im-server.splus.ir", port=443):
    return SimpleNamespace(id=dc_id, ip_address=ip, port=port,
                           media_only=media_only, cdn=False)


def test_find_media_dc():
    client = SimpleNamespace(
        _config=SimpleNamespace(dc_options=[
            _dc(1, media_only=True, ip="media-a.splus.ir"),
            _dc(2),
            _dc(3, media_only=True, ip="media-c.splus.ir"),
        ]),
        session=SimpleNamespace(dc_id=3),
    )
    dc = fx._find_media_dc(client)
    check("media DC مربوط به DC فعلی انتخاب شد",
          dc is not None and dc.id == 3 and dc.ip_address == "media-c.splus.ir",
          f"{dc}")
    check("media_only=True دارد", dc is not None and dc.media_only is True)


def test_find_media_dc_no_media():
    client = SimpleNamespace(
        _config=SimpleNamespace(dc_options=[_dc(1), _dc(2)]),
        session=SimpleNamespace(dc_id=2),
    )
    check("بدون media DC → None", fx._find_media_dc(client) is None)


def test_find_media_dc_no_config():
    client = SimpleNamespace(_config=None, session=SimpleNamespace(dc_id=1))
    check("بدون config → None", fx._find_media_dc(client) is None)


# ---------------------------------------------------------------------------
#  تشخیصِ درخواستِ آپلودِ فایل
# ---------------------------------------------------------------------------
def test_is_file_upload():
    check("SaveFilePart → True",
          fx._is_file_upload(_up.SaveFilePartRequest(1, 0, b"x")))
    check("SaveBigFilePart → True",
          fx._is_file_upload(_up.SaveBigFilePartRequest(1, 0, 2, b"x")))
    check("لیستِ شامل SaveFilePart → True",
          fx._is_file_upload([_up.SaveFilePartRequest(1, 0, b"x"),
                              SimpleNamespace()]))
    check("درخواستِ غیرفایل → False",
          not fx._is_file_upload(SimpleNamespace()))


# ---------------------------------------------------------------------------
#  هدایتِ واقعی در _redirected_call
# ---------------------------------------------------------------------------
class FakeSelf:
    def __init__(self):
        self.calls = []

    async def _call(self, sender, request, ordered=False, flood_sleep_threshold=None):
        self.calls.append((sender, request))
        return "ok"


async def _sender_returns(media):
    async def get_sender(client):
        return media
    return get_sender


def test_redirect_file_upload_to_media():
    async def run():
        fs = FakeSelf()
        media = SimpleNamespace(name="MEDIA")
        req = _up.SaveFilePartRequest(7, 0, b"data")
        result = await fx._redirected_call(
            fs, req, _orig_call=SimpleNamespace(),
            _get_sender=await _sender_returns(media))
        return result, fs.calls
    result, calls = asyncio.run(run())
    check("نتیجه از media sender آمد", result == "ok", f"{result}")
    check("به _call با media sender رفت",
          len(calls) == 1 and calls[0][0] is not None,
          f"{calls}")
    check("همان درخواستِ SaveFilePart ارسال شد",
          isinstance(calls[0][1], _up.SaveFilePartRequest))


def test_no_media_raises_error():
    """بدون media sender → به sender اصلی برنمی‌گردد و خطای واضح می‌دهد."""
    async def run():
        fs = FakeSelf()
        orig = []
        async def orig_call(self, request, ordered=False, flood_sleep_threshold=None):
            orig.append(request)
            return "orig"
        req = _up.SaveFilePartRequest(7, 0, b"data")
        try:
            await fx._redirected_call(
                fs, req, _orig_call=orig_call,
                _get_sender=await _sender_returns(None))
            return "no-error", orig, fs.calls
        except fx.MediaSenderUnavailableError:
            return "raised", orig, fs.calls
    result, orig, calls = asyncio.run(run())
    check("بدون media sender → MediaSenderUnavailableError",
          result == "raised", f"{result}")
    check("هرگز به _call با media sender نرفت", len(calls) == 0, f"{calls}")
    check("هرگز به orig_call (sender اصلی) نرسید", len(orig) == 0, f"{orig}")


def test_non_file_not_redirected():
    async def run():
        fs = FakeSelf()
        orig = []
        async def orig_call(self, request, ordered=False, flood_sleep_threshold=None):
            orig.append(request)
            return "orig"
        req = SimpleNamespace(some="thing")
        result = await fx._redirected_call(
            fs, req, _orig_call=orig_call,
            _get_sender=await _sender_returns(SimpleNamespace(name="MEDIA")))
        return result, orig, fs.calls
    result, orig, calls = asyncio.run(run())
    check("درخواستِ غیرفایل به media نمی‌رود", result == "orig", f"{result}")
    check("به _call نرفت", len(calls) == 0, f"{calls}")
    check("به orig_call رسید", len(orig) == 1)


def test_install_idempotent_and_patches():
    from splusthon import SoroushClient
    before = SoroushClient.__call__
    fx.install_media_upload()
    after = SoroushClient.__call__
    check("کلاس وصله شد", after is not before)
    fx.install_media_upload()
    check("idempotent (دوباره وصله نمی‌شود)", SoroushClient.__call__ is after)


def test_media_sender_retries_after_failure():
    """پس از شکستِ ساخت، بلاکِ دائمی نمی‌ماند و دفعهٔ بعد دوباره تلاش می‌شود."""
    calls = {"n": 0}

    class FakeSender:
        def __init__(self):
            self._fut = asyncio.get_event_loop().create_future()
            self.dc_id = None

        async def disconnect(self):
            self.dc_id = "disconnected"

    class FakeSelf:
        _config = SimpleNamespace(dc_options=[])
        session = SimpleNamespace(dc_id=3)
        _log = None
        _proxy = None
        _local_addr = None
        _media_sender = None
        _init_request = SimpleNamespace()

    def fake_create(client, dc):
        calls["n"] += 1
        raise RuntimeError("Server disconnected")

    orig_create = fx._create_media_sender
    fx._create_media_sender = fake_create
    try:
        async def run():
            client = FakeSelf()
            first = await fx._get_media_sender(client)
            # بارِ دوم هم باید دوباره تلاش کند (نه اینکه فوراً None برگردد)
            second = await fx._get_media_sender(client)
            return first, second, client
        first, second, client = asyncio.run(run())
        check("شکستِ اول → None", first is None)
        check("شکستِ دوم هم → None (اما تلاشِ دوباره شد)",
              second is None and calls["n"] == 2, f"attempts={calls['n']}")
        check("سعیِ دوباره انجام شد (rebuild)", calls["n"] >= 2, f"{calls['n']}")
    finally:
        fx._create_media_sender = orig_create


# ---------------------------------------------------------------------------
def main():
    test_find_media_dc()
    test_find_media_dc_no_media()
    test_find_media_dc_no_config()
    test_is_file_upload()
    test_redirect_file_upload_to_media()
    test_no_media_raises_error()
    test_non_file_not_redirected()
    test_install_idempotent_and_patches()
    test_media_sender_retries_after_failure()
    print(f"\npassed={PASSED} failed={FAILED}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
