"""یک سیاست واحد برای نمایش اطلاعات کاربر در پیام‌های قابل‌مشاهده."""


def format_user(user, fallback="کاربر ناشناس"):
    """``@username``، سپس نام نمایشی، سپس مقدار جایگزین.

    شناسهٔ عددی هرگز به کاربر نمایش داده نمی‌شود. این تابع فقط برای متن‌های
    قابل‌مشاهده است و هیچ نقشی در شناسایی/مجوز/ذخیره‌سازی کاربران ندارد.
    """
    if user is None:
        return fallback

    username = getattr(user, "username", None)
    if isinstance(user, dict):
        username = user.get("username")
    username = str(username or "").strip().lstrip("@").replace("\n", " ").replace("\r", " ").strip()
    if username and not username.isdigit():
        return "@" + username

    if isinstance(user, dict):
        first, last = user.get("first_name"), user.get("last_name")
    else:
        first, last = getattr(user, "first_name", None), getattr(user, "last_name", None)
    name = " ".join(
        str(part).replace("\n", " ").replace("\r", " ")
        for part in (first, last)
        if part and str(part).strip()
    )
    name = " ".join(name.replace("|", " ").replace("☫", " ").split())
    return name or fallback
