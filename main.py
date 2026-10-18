import os
import sys
import atexit

# launcher این clone — BOT_INSTANCE باید قبل از هر import پروجه
# (و قبل از لود شدن .env) قطعی شود: modules/runtime_paths در زمان
# import مقدارهای INSTANCE_NAME/DATA_DIR را از همین متغیر می‌سازد.
#
# ⚠️ این clone ممکن است instance غیر-main باشد (bot2/bot3). نام instance
# از فایل نشانگر `.bot_instance` کنار همین فایل خوانده می‌شود تا اجرای
# مستقیم `python3 main.py` هرگز به runtime ربات main برنگردد.
def _default_instance():
    marker = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          ".bot_instance")
    try:
        with open(marker, "r", encoding="utf-8") as fh:
            name = fh.read().strip()
        if name:
            return name
    except OSError:
        pass
    return "foxora2"


os.environ.setdefault("BOT_INSTANCE", _default_instance())

# .env باید قبل از هر import پروجه لود شود (override=False معنی
# متگیرهای محیتهای فعلی — از جمله BOT_INSTANCE بالا — بر .env برتری دارند).
from dotenv import load_dotenv
load_dotenv(override=False)

from core.bot_working_split_ok import FatalAuthError, SoroushAntiSpamBot
from modules.time_utils import TEHRAN, now_local
from modules.watchdog_reporting import FATAL_AUTH_EXIT_CODE
import asyncio


def _acquire_instance_lock():
    """Single-instance guard.

    دو پروس با همان session string نمی‌توانند همزمان کار کنند: سرور
    MTProto updateها را فقط به یکی می‌دهد و پروس دیگر زنده ولی
    بی‌تحرک می‌ماند (همه گروه‌ها «مرده» به نظر می‌رسند). این قفل از
    اجرای همزمان جلوگیری می‌کند و دلایلش را در ترمینال می‌نویسد.
    """
    try:
        from pathlib import Path
        from modules.runtime_paths import runtime_config_file
        # 🐛 fix: migrate=False الزامی است — bot.pid یک فایل گذرا و
        # per-process است؛ کپی‌شدن bot.pid کهنهٔ instance اصلی (main) به
        # config یک instance تازه (bot2/bot3) باعث می‌شد instance جدید فکر
        # کند پروسه‌ی دیگری قفل را دارد و بی‌دلیل خارج شود.
        lock = Path(runtime_config_file("bot.pid", migrate=False))
    except Exception:
        return None
    pid = os.getpid()
    if lock.exists():
        try:
            old_pid = int((lock.read_text(encoding="utf-8").strip() or "0"))
        except Exception:
            old_pid = 0
        if 0 < old_pid and old_pid != pid:
            alive = False
            try:
                os.kill(old_pid, 0)
                alive = True
            except ProcessLookupError:
                alive = False
            except PermissionError:
                alive = False  # پروس دیگر متعلق به ما نیست؛ قفل را می‌گیریم
            if alive:
                try:
                    env_bytes = Path(f"/proc/{old_pid}/environ").read_bytes()
                    cur_inst = os.environ.get("BOT_INSTANCE", "foxora2").strip()
                    for item in env_bytes.split(b"\x00"):
                        if item.startswith(b"BOT_INSTANCE="):
                            other_inst = item.split(b"=", 1)[1].decode("utf-8", "ignore").strip()
                            if other_inst and other_inst != cur_inst:
                                alive = False
                            break
                except Exception:
                    pass
            if alive:
                print("=" * 62)
                print(" ⛔ BOT INSTANCE ALREADY RUNNING (pid=%d)" % old_pid)
                print("    با یک session string فقط یک پروس update می‌گیرد؛")
                print("    پروس دوم زنده است ولی هیچ پیامی نمی‌بیند.")
                print("    پروس فعلی (pid=%d) خارج می‌شود." % pid)
                print("    اول پروس قدیمی را ببند: kill %d" % old_pid)
                print("=" * 62)
                sys.exit(1)
    try:
        lock.write_text(str(pid), encoding="utf-8")
        atexit.register(
            lambda: _release_instance_lock(lock, pid))
    except Exception:
        pass
    return lock


def _release_instance_lock(lock, pid):
    try:
        if int((lock.read_text(encoding="utf-8").strip() or "0")) == pid:
            lock.unlink()
    except Exception:
        pass


async def main():
    _acquire_instance_lock()
    try:
        from modules import runtime_paths
        _info = runtime_paths.describe()
        print(
            f"[INSTANCE] bot_instance={_info['instance']} "
            f"data_dir={_info['data_dir']} config_dir={_info['config_dir']} "
            f"log_dir={_info['log_dir']} db_dir={_info['db_dir']}"
        )
    except Exception:
        pass
    # نمایشِ زمانِ واقعیِ تهران در ترمینال هنگام راه‌اندازی، تا مشخص باشد
    # ربات واقعاً با چه زمانی کار می‌کند.
    _now = now_local()
    print(f"[START] Tehran time: {_now.strftime('%Y-%m-%d %H:%M:%S')} "
          f"({TEHRAN})")
    bot = SoroushAntiSpamBot()
    try:
        await bot.run()
    except FatalAuthError as error:
        # خروجِ قطعی، تمیز و فقط یک بار: نشست منقضی با reconnect درمان
        # نمی‌شود؛ watchdog با همین کد خاص پروسه را دوباره بالا نمی‌آورد.
        print("=" * 62)
        print(" ⛔ نشست سروش Foxora2 نامعتبر/منقضی شده است:")
        print(f"    {error}")
        print()
        print(" راه‌حل (فقط یک بار انجام می‌شود):")
        print("    1) python3 tools/login_primary_once.py")
        print("    2) ./foxora2")
        print(" نشست جدید فقط در SOROUSH_SESSION_STRING_FOXORA2 ذخیره می‌شود؛")
        print(" نشست/دادهٔ ربات‌های دیگر (Foxora / B2) تغییر نمی‌کند.")
        print("=" * 62)
        raise SystemExit(FATAL_AUTH_EXIT_CODE)

if __name__ == "__main__":
    asyncio.run(main())
