from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import re
import sys

from foxbot.runtime import RuntimeSettings, create_soroush_client

_PHONE_RE = re.compile(r"^\+?[0-9]{8,15}$")


def _prompt_phone() -> str:
    phone = input("شماره تلفن (با کد کشور، مانند +98912…): ").strip().replace(" ", "").replace("-", "")
    if not _PHONE_RE.fullmatch(phone):
        raise ValueError("شماره تلفن نامعتبر است؛ شماره را با کد کشور و فقط با رقم وارد کنید.")
    return phone


async def initialize_session(settings: RuntimeSettings | None = None) -> str:
    """Create/sign in to a local SPlusthon session using its supported start API."""
    settings = settings or RuntimeSettings.from_env()
    if settings.session_string:
        raise ValueError("برای ورود تعاملی، SOROUSH_SESSION_STRING را خالی بگذارید.")
    phone = _prompt_phone()
    previous_umask = os.umask(0o077)
    client = None
    try:
        client = create_soroush_client(settings)
        await client.start(
            phone=phone,
            password=lambda: getpass.getpass("رمز دومرحله‌ای (در صورت درخواست): "),
            code_callback=lambda: input("کد تأیید سروش‌پلاس: ").strip(),
        )
        me = await client.get_me()
        try:
            settings.session_path.chmod(0o600)
        except FileNotFoundError:
            pass
        return str(me.id)
    finally:
        try:
            if client is not None:
                await client.disconnect()
        finally:
            os.umask(previous_umask)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="fox-soroush-login",
        description="ورود تعاملی و ساخت فایل سشن محلی SPlusthon.",
    )
    parser.parse_args()
    try:
        user_id = asyncio.run(initialize_session())
    except (KeyboardInterrupt, EOFError):
        print("ورود لغو شد.", file=sys.stderr)
        raise SystemExit(1)
    except Exception as exc:  # noqa: BLE001 -- redact all auth/transport exception messages
        # Avoid printing exception text: some transport/library errors may contain credentials.
        name = type(exc).__name__
        if name in {"PhoneNumberInvalidError", "PhoneNumberUnoccupiedError"}:
            message = "شماره تلفن نامعتبر است یا حسابی برای آن پیدا نشد."
        elif name in {"PhoneCodeInvalidError", "CodeInvalidError", "PhoneCodeExpiredError"}:
            message = "کد تأیید اشتباه یا منقضی شده است؛ دوباره دستور ورود را اجرا کنید."
        elif name in {"PasswordHashInvalidError", "PasswordEmptyError", "PasswordRequiredError"}:
            message = "رمز دومرحله‌ای نادرست است یا ورود به آن نیاز دارد."
        elif isinstance(exc, (ConnectionError, TimeoutError, OSError)):
            message = "اتصال برقرار نشد یا قطع شد؛ اینترنت را بررسی و دوباره تلاش کنید."
        else:
            message = "ورود ناموفق بود. اطلاعات محرمانه چاپ نشده است؛ اتصال و نسخه SPlusthon را بررسی کنید."
        print(f"{message} ({name})", file=sys.stderr)
        raise SystemExit(1) from None
    print(f"ورود موفق بود (شناسه حساب: {user_id}). فایل سشن در مسیر محلی تنظیم‌شده ذخیره شد.")


if __name__ == "__main__":
    main()
