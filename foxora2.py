#!/usr/bin/env python3
"""Foxora2 launcher — اجرای ربات مستقل Foxora2 با مسیر داده و نشست جدا از Foxora و B2.

استفاده:
    python3 foxora2.py
    ./foxora2            (اگر فایل شل foxora2 اجرایی باشد)

همهٔ داده‌ها، دیتابیس، لاگ‌ها و تنظیمات زمان اجرا در ``~/.foxora2`` ذخیره می‌شوند
و هیچ اشتراکی با Foxora (``~/.foxora``) یا B2 (``~/.local/share/soroush-bot``) ندارند.
"""
import asyncio
import os
import sys

os.environ["BOT_INSTANCE"] = "foxora2"

from main import main  # noqa: E402


if __name__ == "__main__":
    asyncio.run(main())
