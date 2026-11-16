import atexit
import asyncio
import errno
import os
import sys

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


_INSTANCE_LOCK_STREAM = None


def _acquire_instance_lock():
    """قفل اتمیک و ماندگار برای جلوگیری از دو کلاینت با یک نشست.

    فایل bot.pid عمداً پس از خروج حذف نمی‌شود: حذف فایلِ در حال flock می‌تواند
    باعث شود اجرای بعدی روی inode تازه قفل بگیرد و هم‌زمان با قفل قدیمی اجرا شود.
    قفل هسته‌ای تا خروج فرایند نگه داشته می‌شود؛ PID فقط برای status/stop است.
    """
    global _INSTANCE_LOCK_STREAM
    try:
        import fcntl
        from pathlib import Path
        from modules.runtime_paths import runtime_config_file

        lock = Path(runtime_config_file("bot.pid", migrate=False))
        stream = lock.open("a+", encoding="utf-8")
    except Exception as error:
        print(f"⛔ قفل اجرای Foxora2 باز نشد؛ برای جلوگیری از اجرای هم‌زمان خارج می‌شوم: {error}", file=sys.stderr)
        raise SystemExit(1)

    try:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        try:
            stream.seek(0)
            old_pid = (stream.read().strip() or "unknown")
        except OSError:
            old_pid = "unknown"
        stream.close()
        print("=" * 62)
        print(f" ⛔ BOT INSTANCE ALREADY RUNNING (pid={old_pid})")
        print("    با یک session string فقط یک پروس update می‌گیرد؛")
        print("    پروس دوم زنده است ولی هیچ پیامی نمی‌بیند.")
        print("    ابتدا پروس قبلی را با ./foxora2 stop متوقف کنید.")
        print("=" * 62)
        raise SystemExit(1)
    except OSError as error:
        stream.close()
        print(f"⛔ قفل اجرای Foxora2 برقرار نشد؛ اجرا را ادامه نمی‌دهم: {error}", file=sys.stderr)
        raise SystemExit(1)

    pid = os.getpid()
    try:
        stream.seek(0)
        stream.truncate()
        stream.write(f"{pid}\n")
        stream.flush()
        os.fsync(stream.fileno())
    except OSError as error:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()
        print(f"⛔ PID قفل Foxora2 ذخیره نشد؛ اجرا را ادامه نمی‌دهم: {error}", file=sys.stderr)
        raise SystemExit(1)

    _INSTANCE_LOCK_STREAM = stream
    atexit.register(_release_instance_lock, lock, pid, stream)
    return lock


def _release_instance_lock(lock, pid, stream=None):
    """PID را بی‌خطر خالی می‌کند و سپس قفل همان inode را آزاد می‌کند."""
    global _INSTANCE_LOCK_STREAM
    stream = stream or _INSTANCE_LOCK_STREAM
    if stream is None:
        return
    try:
        stream.seek(0)
        current = stream.read().strip()
        if current == str(pid):
            stream.seek(0)
            stream.truncate()
            stream.write("0\n")
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        pass
    try:
        import fcntl
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    except (ImportError, OSError):
        pass
    try:
        stream.close()
    except OSError:
        pass
    if _INSTANCE_LOCK_STREAM is stream:
        _INSTANCE_LOCK_STREAM = None


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
