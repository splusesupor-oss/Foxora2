"""تست رفع دو مشکل:
۱) مقاوم‌شدن ارسال نقش خون‌آشام در برابر خطای موقت RPC/اتصال
۲) cooldown و Lock گروهی برای دستور پاک
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
#  بخش ۱: خون‌آشام
# ===========================================================================
class TimeoutOnceClient:
    """اولین send_message معلق می‌ماند (شبیه RPC timeout)، سپس موفق می‌شود."""

    def __init__(self):
        self.calls = 0
        self.dm = []

    async def send_message(self, target, text, **kwargs):
        self.calls += 1
        if self.calls == 1:
            await asyncio.sleep(3600)  # timeout
        uid = getattr(target, "id", target)
        self.dm.append((uid, text))


class FailThenSucceedClient:
    """اولین فراخوانی خطای موقت (timeout) می‌دهد، سپس موفق می‌شود."""

    def __init__(self):
        self.calls = 0
        self.dm = []

    async def send_message(self, target, text, **kwargs):
        self.calls += 1
        if self.calls <= 2:
            raise asyncio.TimeoutError("connection timed out")
        uid = getattr(target, "id", target)
        self.dm.append((uid, text))


class PrivacyClient:
    """برخی کاربران پیوی‌شان بسته است (خطای دائمی)."""

    def __init__(self, blocked):
        self.blocked = set(blocked)
        self.dm = []

    async def send_message(self, target, text, **kwargs):
        uid = getattr(target, "id", target)
        if uid in self.blocked:
            raise Exception("CausedBy privacy: can't send to user")
        self.dm.append((uid, text))


def test_transient_timeout_does_not_abort():
    """خطای موقتِ timeout هنگام ارسال نقش → تلاشِ مجدد → موفق، بازی ادامه می‌یابد."""
    async def scenario():
        lg = Logger()
        vp.reset_all(-101)
        vp.start(-101, lg)
        for uid in [1, 2, 3, 4]:
            vp.join(-101, uid, User(uid), lg)
        cli = TimeoutOnceClient()
        old_timeout = vp.DM_TIMEOUT
        vp.DM_TIMEOUT = 0.1  # برای تستِ سریع
        try:
            ok, err, transient = await vp.send_role_dm(
                cli, {"user_id": 1, "peer": User(1)}, logger=lg, chat_id=-101)
        finally:
            vp.DM_TIMEOUT = old_timeout
        return ok, cli.calls, cli.dm
    ok, calls, dm = asyncio.run(scenario())
    check("timeout موقت با تلاشِ مجدد موفق شد", ok is True, f"{ok}")
    check("بیش از یک تلاش انجام شد", calls >= 2, f"{calls}")
    check("نقش به درستی ارسال شد", len(dm) == 1 and dm[0][1] == vp.ROLE_MESSAGE,
          f"{dm}")


def test_transient_retry_then_success():
    """چند خطای موقت پشت‌سرهم → تلاش مجدد → در نهایت موفق."""
    async def scenario():
        lg = Logger()
        cli = FailThenSucceedClient()
        old_timeout = vp.DM_TIMEOUT
        vp.DM_TIMEOUT = 0.2
        try:
            ok, err, transient = await vp.send_role_dm(
                cli, {"user_id": 7, "peer": User(7)}, logger=lg, chat_id=-102)
        finally:
            vp.DM_TIMEOUT = old_timeout
        return ok, cli.calls, cli.dm
    ok, calls, dm = asyncio.run(scenario())
    check("با خطاهای موقت متوالی در نهایت موفق شد", ok is True, f"{ok}")
    check("چند بار تلاش شد", calls >= 3, f"{calls}")
    check("نقش ارسال شد", len(dm) == 1, f"{dm}")


def test_permanent_privacy_reassigns_and_continues():
    """خطای دائمیِ حریم خصوصی → نقش به بازیکنِ در دسترس جابه‌جا می‌شود."""
    async def scenario():
        lg = Logger()
        vp.reset_all(-103)
        vp.start(-103, lg)
        for uid in [1, 2, 3, 4]:
            vp.join(-103, uid, User(uid), lg)
        chosen = vp.choose_vampire(-103, lg)
        # بازیکن انتخاب‌شده را مجبور می‌کنیم بلاک‌شده باشد
        blocked = {1, 2}
        cli = PrivacyClient(blocked)
        # chosen را روی بازیکن ۱ می‌گذاریم
        chosen = {"number": 1, "player": dict(chosen["players"][0]),
                  "players": list(chosen["players"])}
        res, mode = await vp.deliver_role(cli, -103, chosen, logger=lg)
        return res, mode, cli.dm
    res, mode, dm = asyncio.run(scenario())
    check("بازی لغو نشد (mode=dm)", mode == "dm", f"{mode}")
    check("نقش به بازیکنِ در دسترس رسید",
          res is not None and res["player"]["user_id"] in (3, 4),
          f"{res}")
    check("DM به کاربر در دسترس ارسال شد", len(dm) == 1, f"{dm}")


def test_vampire_state_clean_after_many_rounds():
    """بعد از چندین دور، Session/Task/Lock تمیز می‌شوند و بازی دوباره شروع می‌شود."""
    async def scenario():
        lg = Logger()
        vp.reset_all()  # پاک‌سازی کامل همهٔ چت‌ها تا حالتِ تست‌های قبلی نماند
        for i in range(10):
            vp.reset_all(-104)
            st = vp.start(-104, lg)
            sid = st["session_id"]
            for uid in [1, 2, 3, 4]:
                vp.join(-104, uid, User(uid), lg)
            chosen = vp.choose_vampire(-104, lg)
            cli = PrivacyClient(blocked=[])
            res, mode = await vp.deliver_role(cli, -104, chosen, logger=lg)
            vp.open_guessing(-104, sid, lg)
            # حدس درست
            v = vp.vampire_player(-104)
            if v:
                for uid in [1, 2, 3, 4]:
                    if uid != v["user_id"]:
                        vp.guess(-104, uid, str(v["user_id"]), lg)
                        break
        return (len(vp._STORE._sessions), len(vp._STORE._tasks),
                len(vp._STORE._locks))
    s, t, l = asyncio.run(scenario())
    check("سشن‌ها پاک شدند", s == 0, f"{s}")
    check("تسک‌ها پاک شدند", t == 0, f"{t}")
    check("قفل‌ها پاک شدند", l == 0, f"{l}")
    check("دوباره شروع می‌شود",
          asyncio.run(_start_again(-104)) is True)


async def _start_again(chat):
    lg = Logger()
    vp.reset_all(chat)
    st = vp.start(chat, lg)
    return st is not None


# ===========================================================================
#  بخش ۲: دستور پاک
# ===========================================================================
def test_delete_cooldown_is_group_wide():
    """cooldown پاک باید برای کل گروه اعمال شود، نه فقط هر کاربر."""
    import handlers.message_handler as mh

    async def scenario():
        mh.DELETE_COMMAND_COOLDOWNS.clear()
        mh._DELETE_GROUP_LOCKS.clear()
        chat = 5001
        r1 = mh._delete_cooldown_allowed(chat)          # user A
        r2 = mh._delete_cooldown_allowed(chat)          # user B (همان گروه)
        r3 = mh._delete_cooldown_allowed(chat + 1)      # گروه دیگر
        return r1, r2, r3
    r1, r2, r3 = asyncio.run(scenario())
    check("اولین پاک مجاز است", r1 is True)
    check("پاکِ دومِ همان گروه (کاربر دیگر) مسدود شد", r2 is False)
    check("گروهِ دیگر مجاز است", r3 is True)


def test_delete_group_lock_serializes():
    """قفل گروهی، عملیات حذف هم‌زمان را سریال می‌کند (فقط یکی هم‌زمان)."""
    import handlers.message_handler as mh

    class FC:
        def __init__(self):
            self.active = 0
            self.max_active = 0
        async def get_messages(self, chat_id, limit):
            return [type("M", (), {"id": i})() for i in range(limit)]
        async def delete_messages(self, chat_id, ids):
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            await asyncio.sleep(0.03)
            self.active -= 1

    async def scenario():
        mh.DELETE_COMMAND_COOLDOWNS.clear()
        mh._DELETE_GROUP_LOCKS.clear()
        cli = FC()
        chat = 6001
        lock = mh._delete_group_lock(chat)

        async def do_delete():
            async with lock:
                msgs = await cli.get_messages(chat, 10)
                ids = [m.id for m in msgs]
                await cli.delete_messages(chat, ids)

        await asyncio.gather(do_delete(), do_delete())
        return cli.max_active
    maxa = asyncio.run(scenario())
    check("حداکثر یک حذف هم‌زمان در گروه", maxa == 1, f"{maxa}")


def test_delete_cooldown_memory_bounded():
    """حافظهٔ cooldown محدود است و بی‌نهایت رشد نمی‌کند."""
    import handlers.message_handler as mh

    async def scenario():
        now = asyncio.get_running_loop().time()
        mh.DELETE_COMMAND_COOLDOWNS.clear()
        for i in range(mh.DELETE_COOLDOWN_MAX_ENTRIES + 500):
            mh.DELETE_COMMAND_COOLDOWNS[70000 + i] = now
        mh._prune_delete_cooldowns()
        return len(mh.DELETE_COMMAND_COOLDOWNS)
    size = asyncio.run(scenario())
    check("حافظهٔ cooldown محدود است",
          size <= mh.DELETE_COOLDOWN_MAX_ENTRIES, f"{size}")


# ===========================================================================
def main():
    test_transient_timeout_does_not_abort()
    test_transient_retry_then_success()
    test_permanent_privacy_reassigns_and_continues()
    test_vampire_state_clean_after_many_rounds()

    test_delete_cooldown_is_group_wide()
    test_delete_group_lock_serializes()
    test_delete_cooldown_memory_bounded()

    print(f"\npassed={PASSED} failed={FAILED}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
