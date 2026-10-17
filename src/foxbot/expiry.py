from __future__ import annotations

import re


class InvalidExpiry(ValueError):
    pass


_DIGITS = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
    "01234567890123456789",
)
_DAY_PATTERN = re.compile(r"^\s*(\d{1,2})\s*(?:روز|day|days|d)?\s*$", re.IGNORECASE)


def parse_expiry_days(text: str) -> int:
    """Parse group expiry as an integer in the inclusive range 1..40."""
    normalized = (text or "").translate(_DIGITS).strip()
    match = _DAY_PATTERN.fullmatch(normalized)
    if not match:
        raise InvalidExpiry("expected a day count from 1 to 40")
    days = int(match.group(1))
    if not 1 <= days <= 40:
        raise InvalidExpiry("days must be between 1 and 40")
    return days
