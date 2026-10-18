#!/usr/bin/env python3
"""One-shot primary SoroushPlus login for Foxora2 without manual API ID / API Hash.

Run only while the bot is stopped.  It makes a single connect/code/sign-in
attempt using SPlusthon's built-in SoroushPlus client defaults
(``SoroushClient(StringSession())``) and exits cleanly on any failure.

نشست جدید **فقط** داخل ``~/Foxora2/.env`` و **فقط** داخل متغیر اختصاصی
``SOROUSH_SESSION_STRING_FOXORA2`` ذخیره می‌شود و به متغیرهای
``SOROUSH_SESSION_STRING_FOXORA`` یا ``SOROUSH_SESSION_STRING`` و فایل‌های
``Foxora`` یا ``B2`` هیچ‌وقت دست نمی‌زند.
"""
from __future__ import annotations

import asyncio
import inspect
import os
import re
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"
FOXORA2_INSTANCE = "foxora2"
FOXORA2_SESSION_KEY = "SOROUSH_SESSION_STRING_FOXORA2"

_DIGIT_TRANSLATION = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
    "01234567890123456789",
)
_STRIP_CHARS_RE = re.compile(r"[\s\-\(\)\u200c\u200d\u200e\u200f\u202a-\u202e]+")
_PHONE_RE = re.compile(r"^\+[1-9][0-9]{7,14}$")


def session_env_key() -> str:
    """نام متغیر نشستِ اختصاصی Foxora2.

    در این مخزن هویت ربات «foxora2» است و خروجی همواره
    ``SOROUSH_SESSION_STRING_FOXORA2`` خواهد بود تا حتی اگر در محیط شل یا
    فایل ``.env`` متغیر ``BOT_INSTANCE`` به اشتباه از ربات دیگری (مثل
    ``foxora`` یا ``main``) کپی شده باشد، هرگز نشست ربات‌های دیگر هدف قرار نگیرد.
    """
    return FOXORA2_SESSION_KEY


def load_env(env_file: Path | None = None) -> None:
    """بارگذاری متغیرهای ``~/Foxora2/.env`` بدون دست‌زدن به متغیرهای محیط فعلی."""
    target = env_file or ENV_FILE
    try:
        rows = target.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for row in rows:
        if "=" not in row or row.lstrip().startswith("#"):
            continue
        key, value = row.split("=", 1)
        clean_key = key.strip()
        if clean_key and clean_key not in os.environ:
            os.environ[clean_key] = value.strip().strip('"').strip("'")


def normalize_phone(raw_phone: str) -> str:
    """نرمال‌سازی شماره تلفن (تبدیل ارقام فارسی/عربی و پیش‌شمارهٔ ایران به ``+98...``)."""
    cleaned = _STRIP_CHARS_RE.sub("", (raw_phone or "").translate(_DIGIT_TRANSLATION))
    if not cleaned:
        raise ValueError("شماره تلفن خالی است.")
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    elif cleaned.startswith("09") and len(cleaned) == 11 and cleaned.isdigit():
        cleaned = "+98" + cleaned[1:]
    elif cleaned.startswith("9") and len(cleaned) == 10 and cleaned.isdigit():
        cleaned = "+98" + cleaned
    elif cleaned.startswith("98") and len(cleaned) == 12 and cleaned.isdigit():
        cleaned = "+" + cleaned
    elif not cleaned.startswith("+") and cleaned.isdigit():
        cleaned = "+" + cleaned
    if not _PHONE_RE.fullmatch(cleaned):
        raise ValueError(
            "فرمت شماره تلفن معتبر نیست (مثال معتبر: +989121234567 یا 09121234567)."
        )
    return cleaned


def normalize_code(raw_code: str) -> str:
    """نرمال‌سازی کد تأیید سروش‌پلاس (تبدیل ارقام فارسی/عربی و حذف فاصله‌ها)."""
    cleaned = _STRIP_CHARS_RE.sub("", (raw_code or "").translate(_DIGIT_TRANSLATION))
    if not cleaned:
        raise ValueError("کد تأیید خالی است.")
    return cleaned


def resolve_custom_api_credentials(env_get=None) -> tuple[int | None, str | None]:
    """بررسی override اختیاری ``API_ID_FOXORA2`` و ``API_HASH_FOXORA2``.

    به‌طور پیش‌فرض ``(None, None)`` برمی‌گرداند تا ``SoroushClient(StringSession())``
    از مقادیر پیش‌فرض رسمیِ داخل پکیج ``SPlusthon`` استفاده کند و کاربر نیازی به
    وارد کردن دستی API ID و API Hash نداشته باشد. متغیرهای عمومی ``API_ID`` و
    ``API_HASH`` عمداً نادیده گرفته می‌شوند تا مقادیر نمونه یا متعلق به ربات دیگر
    مانع اتصال سروش‌پلاس نشوند.
    """
    if env_get is None:
        env_get = os.getenv
    raw_id = (env_get("API_ID_FOXORA2") or "").strip()
    raw_hash = (env_get("API_HASH_FOXORA2") or "").strip()
    if not raw_id or not raw_hash:
        return None, None
    try:
        api_id = int(raw_id)
    except ValueError:
        return None, None
    if api_id <= 0:
        return None, None
    return api_id, raw_hash


def create_client(session=None, *, env_get=None):
    """ساخت ``SoroushClient(StringSession())`` بدون نیاز به API ID / API Hash دستی."""
    try:
        from splusthon import SoroushClient
        from splusthon.sessions import StringSession
    except ImportError as error:
        raise SystemExit("با python3 همان محیطی که splusthon نصب است اجرا کنید.") from error

    session_obj = session if session is not None else StringSession()
    api_id, api_hash = resolve_custom_api_credentials(env_get=env_get)
    if api_id is not None and api_hash is not None:
        return SoroushClient(session_obj, api_id, api_hash)
    return SoroushClient(session_obj)


def save_primary(value: str, *, key: str | None = None, env_file: Path | None = None) -> None:
    """نشست را فقط در ``SOROUSH_SESSION_STRING_FOXORA2`` داخل ``~/Foxora2/.env`` می‌نویسد.

    خط ``SOROUSH_SESSION_STRING=`` (عمومی، احتمالاً متعلق به B2) و هر متغیر
    اختصاصی دیگر (مثل ``SOROUSH_SESSION_STRING_FOXORA=``) دقیقاً همان‌طور که هست
    باقی می‌ماند. تمام داده‌ها فقط در ``.env`` همین مخزن (کنار ریشهٔ Foxora2)
    نوشته می‌شود.
    """
    key = key or session_env_key()
    target = env_file or ENV_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    prefix = key + "="
    rows = target.read_text(encoding="utf-8").splitlines() if target.exists() else []
    output, found = [], False
    for row in rows:
        if row.startswith(prefix):
            output.append(prefix + value)
            found = True
        else:
            output.append(row)
    if not found:
        if output and output[-1].strip():
            output.append("")
        output.append(prefix + value)
    fd, tmp = tempfile.mkstemp(prefix=".env.login.", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write("\n".join(output) + "\n")
        os.replace(tmp, target)
        try:
            target.chmod(0o600)
        except OSError:
            pass
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _is_two_step_password_error(error: BaseException) -> bool:
    for cls in type(error).__mro__:
        if "password" in cls.__name__.lower():
            return True
    text = str(error).lower()
    return "two-step" in text or "sessionpasswordneeded" in text or "2fa" in text


async def _sign_in_with_password(client, phone: str, password: str):
    try:
        sig = inspect.signature(client.sign_in)
        if "phone" in sig.parameters:
            return await asyncio.wait_for(
                client.sign_in(phone=phone, password=password), timeout=30
            )
    except (TypeError, ValueError):
        pass
    return await asyncio.wait_for(client.sign_in(password=password), timeout=30)


async def login_once(
    *,
    input_fn=input,
    env_file: Path | None = None,
    client=None,
) -> str:
    """اجرای کامل یک نوبت ورود تعاملی و ذخیرهٔ سشن در ``SOROUSH_SESSION_STRING_FOXORA2``."""
    target_env = env_file or ENV_FILE
    load_env(target_env)
    client = client if client is not None else create_client()
    try:
        raw_phone = input_fn("شماره تلفن سروش‌پلاس (مثال: +98912... یا 0912...): ")
        phone = normalize_phone(raw_phone)

        print("در حال اتصال به سروش‌پلاس (بدون نیاز به API ID / API Hash دستی)...")
        await asyncio.wait_for(client.connect(), timeout=25)
        await asyncio.wait_for(client.send_code_request(phone), timeout=25)

        raw_code = input_fn("کد تأیید: ")
        code = normalize_code(raw_code)
        try:
            await asyncio.wait_for(client.sign_in(phone, code), timeout=30)
        except Exception as error:
            if not _is_two_step_password_error(error):
                raise
            password = input_fn("رمز دومرحله‌ای (2FA): ").strip()
            if not password:
                raise ValueError("رمز دومرحله‌ای وارد نشد.") from error
            await _sign_in_with_password(client, phone, password)

        # اطمینان از همگام بودن auth_key روی شیء session پیش از ذخیره
        sender = getattr(client, "_sender", None)
        if (
            getattr(client, "session", None) is not None
            and getattr(client.session, "auth_key", None) is None
            and getattr(sender, "auth_key", None) is not None
        ):
            client.session.auth_key = sender.auth_key

        value = client.session.save() if getattr(client, "session", None) is not None else ""
        if not value:
            raise RuntimeError("رشتهٔ سشن (session string) خالی است؛ ورود تکمیل نشد.")

        key = session_env_key()
        save_primary(value, key=key, env_file=target_env)
        print(f"✅ نشست جدید با موفقیت فقط در {key} داخل {target_env} ذخیره شد.")
        print("   به سشن، .env، دیتابیس و تنظیمات Foxora و B2 هیچ دستی زده نشد.")
        return key
    finally:
        try:
            await asyncio.wait_for(client.disconnect(), timeout=10)
        except Exception:
            pass


async def main() -> None:
    try:
        await login_once()
    except (KeyboardInterrupt, EOFError):
        print("\n❌ عملیات ورود توسط کاربر لغو شد.")
        raise SystemExit(1)
    except Exception as error:
        print(f"❌ ورود انجام نشد: {type(error).__name__}: {error}")
        print("هیچ retry خودکاری انجام نشد؛ پس از بررسی، دوباره همین دستور را اجرا کنید.")
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
