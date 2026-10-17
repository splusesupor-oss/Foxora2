"""یکسان‌سازی شناسهٔ گروه در تمام storageهای فعال SPlusthon."""

CHANNEL_ID_OFFSET = 1_000_000_000_000


def normalize_group_id(group_id):
    """کلید پایدارِ گروه را برای شناسهٔ کوتاه و شکل -100... برمی‌گرداند."""
    try:
        value = int(group_id)
    except (TypeError, ValueError):
        return str(group_id)

    if value <= -CHANNEL_ID_OFFSET:
        value = abs(value) - CHANNEL_ID_OFFSET
    return str(value)


def merge_unique(first, second):
    """دو لیست JSON را بدون حذف هیچ ورودی ادغام می‌کند."""
    result = []
    seen = set()
    for item in list(first or []) + list(second or []):
        marker = repr(item)
        if marker not in seen:
            seen.add(marker)
            result.append(item)
    return result
