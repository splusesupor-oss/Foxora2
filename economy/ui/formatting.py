"""🎨 ابزار مشترک قالب‌بندی متن اقتصاد.

این فایل هیچ وابستگی‌ای به splusthon ندارد؛ فقط «span» خنثی تولید
می‌کند و لایهٔ هندلر آن را به entity واقعی تبدیل می‌کند.
"""

_DIGITS = str.maketrans("0123456789", "𝟬𝟭𝟮𝟯𝟰𝟱𝟲𝟳𝟴𝟵")


def fa(value):
    """عدد را با ارقام فارسی نمایش می‌دهد."""
    return f"{value:,}".translate(_DIGITS) if isinstance(value, int) \
        else str(value).translate(_DIGITS)


def fa_plain(value):
    """عدد را با ارقام فارسی و *بدون* جداکنندهٔ هزارگان نمایش می‌دهد.

    پروفایل باید دقیقاً «۱۴۲۰» را نشان دهد نه «۱٬۴۲۰»، پس اینجا از
    قالب‌بندی سه‌رقمی خبری نیست.
    """
    return str(value).translate(_DIGITS)


def u16(value):
    """طول رشته بر حسب واحد UTF-16 (همان چیزی که entity می‌خواهد)."""
    return len(value.encode("utf-16-le")) // 2


def spans_for(text, pieces, kind="bold"):
    """برای هر تکه، span با offset درست تولید می‌کند.

    اگر تکه‌ای در متن نباشد نادیده گرفته می‌شود تا هرگز offset اشتباه
    ساخته نشود.
    """
    spans = []
    for piece in pieces:
        index = text.find(piece)
        if index < 0:
            continue
        spans.append((kind, u16(text[:index]), u16(piece)))
    return spans


def quote_spans(text, piece):
    """ناحیه را هم Bold می‌کند هم داخل «نقل قول شیشه‌ای» می‌گذارد.

    هیچ نشانه‌گذاری Markdown تولید نمی‌شود؛ فقط span خنثی که لایهٔ
    هندلر آن را به entity واقعی سروش پلاس تبدیل می‌کند.
    """
    index = text.find(piece)
    if index < 0:
        return []
    offset, length = u16(text[:index]), u16(piece)
    return [("bold", offset, length), ("blockquote", offset, length)]


def format_duration(seconds):
    """ثانیه را به «۳ ساعت و ۱۲ دقیقه» تبدیل می‌کند."""
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours and minutes:
        return f"{fa(hours)} ساعت و {fa(minutes)} دقیقه"
    if hours:
        return f"{fa(hours)} ساعت"
    if minutes:
        return f"{fa(minutes)} دقیقه"
    return "کمتر از یک دقیقه"
