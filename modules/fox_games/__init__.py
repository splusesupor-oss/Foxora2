"""بازی‌های Fox AI — کاملاً مستقل از بازی‌های قدیمی ربات.

هر بازی در فایل خودش زندگی می‌کند و session، تایمر، حافظه و state مخصوص خود
را دارد. هیچ ساختار دادهٔ سراسری با اسم فامیل، حدس پرچم، چیستان یا جای خالی
مشترک نیست؛ اتصال به ربات فقط از طریق لایهٔ Command Router انجام می‌شود.
"""

from modules.fox_games import (  # noqa: F401
    laugh_or_lose,
    survival,
    lucky_box,
    minesweeper,
    vampire,
)

__all__ = ["laugh_or_lose", "survival", "lucky_box", "minesweeper", "vampire"]
