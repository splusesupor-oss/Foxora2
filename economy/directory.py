"""📇 دفترچهٔ یوزرنیم → شناسهٔ کاربر، به تفکیک گروه.

انتقال سکه با یوزرنیم انجام می‌شود، پس باید بتوانیم «@username» را به
شناسهٔ عددی تبدیل کنیم. هر بار کاربری در گروه پیام می‌دهد، یوزرنیمش
اینجا ثبت می‌شود.

هر گروه دفترچهٔ خودش را دارد، دقیقاً مثل کیف پول؛ پس یوزرنیم یکسان در
دو گروه با دو حساب متفاوت قاطی نمی‌شود.

داده‌ها زیر کلید ``usernames`` در همان فایل اقتصاد می‌نشینند.
"""
from economy import storage
from economy.coins import accounts


def normalize(username):
    """``"@Ali "`` → ``"ali"``؛ ``None`` اگر یوزرنیم معتبر نباشد."""
    if username is None:
        return None
    value = str(username).strip()
    for junk in ("\u200c", "\u200f", "\u200e"):
        value = value.replace(junk, "")
    value = value.strip().lstrip("@").strip().lower()
    return value or None


def is_valid(username):
    """آیا این متن یک یوزرنیم است (نه شناسهٔ عددی، نه متن دلخواه).

    قواعد سروش پلاس مثل تلگرام است: حروف انگلیسی، رقم و زیرخط، و باید
    با حرف شروع شود. شناسهٔ عددی عمداً رد می‌شود.
    """
    value = normalize(username)
    if not value or len(value) < 3 or len(value) > 32:
        return False
    if value.isdigit():
        return False
    if not (value[0].isalpha() and value[0].isascii()):
        return False
    return all((char.isascii() and (char.isalnum() or char == "_"))
               for char in value)


# سقف دفترچهٔ هر گروه: وقتی تعداد usernameهای ثبت‌شده از این عدد بگذرد،
# قدیمی‌ترین entryها (از ابتدای دیکشنری = اولین ثبت) حذف می‌شوند تا دادهٔ
# پایدار (SQLite/JSON) با چرخش usernameهای اعضا بی‌نهایت رشد نکند.
# entry حذف‌شده با اولین پیام بعدی همان کاربر دوباره ثبت می‌شود (remember
# برای هر پیام گروهی صدا زده می‌شود)؛ تا آن لحظه lookup با آن username
# مثل قبل None می‌دهد و transfer با آن نام «شناسهٔ شناخته‌نشده» می‌ماند.
USERNAME_BOOK_MAX = 500


def remember(chat_id, user_id, username):
    """یوزرنیم این کاربر را در دفترچهٔ همین گروه ثبت می‌کند."""
    key = normalize(username)
    if not key or user_id is None:
        return None
    try:
        chat = accounts.chat_key(chat_id)
    except Exception:
        return None
    target = str(user_id)
    try:
        existing = storage.read_path("usernames", chat, key, default=None)
        if existing is not None and str(existing) == target:
            # همان کاربر، همان username: تغییری نیست و هیچ نوشتنی انجام
            # نمی‌شود (مسیر داغ بی‌هزینه می‌ماند). اگر entry قبلاً evict
            # شده باشد existing=None است و مسیر ثبتِ پایین آن را بازمی‌سازد.
            return target
    except Exception:
        existing = None
    try:
        with storage.transaction(defer=True) as data:
            if not isinstance(data, dict):
                return None
            books = data.get("usernames")
            if not isinstance(books, dict):
                books = {}
                data["usernames"] = books
            book = books.get(chat)
            if not isinstance(book, dict):
                book = {}
                books[chat] = book
            if book.get(key) == target:
                return target
            # (دوباره) در انتهای دیکشنری درج می‌شود تا ترتیب دیکشنری
            # همان ترتیبِ «اولین ثبت» بماند؛ همان ترتیبی که هرسِ پایین
            # بر اساس آن قدیمی‌ترین‌ها را می‌ریزد.
            book.pop(key, None)
            book[key] = target
            # محدودسازی: قدیمی‌ترین entryها را از ابتدای دیکشنری می‌ریزد.
            # داخل همین transaction (defer) انجام می‌شود، پس هم RAM و هم
            # رکورد پایدار SQLite/JSON همیشه ≤ سقف می‌مانند و I/O اضافی به
            # مسیر پردازش پیام نمی‌آید (نوشتن در همان flush بعدی است).
            while len(book) > USERNAME_BOOK_MAX:
                book.pop(next(iter(book)), None)
            return target
    except Exception:
        return None


def lookup(chat_id, username):
    """شناسهٔ کاربر با این یوزرنیم در همین گروه، یا ``None``."""
    key = normalize(username)
    if not key:
        return None
    chat = accounts.chat_key(chat_id)
    found = storage.read_path("usernames", chat, key, default=None)
    return str(found) if found is not None else None


def username_of(chat_id, user_id):
    """یوزرنیم ثبت‌شدهٔ یک کاربر در این گروه، یا ``None``."""
    chat = accounts.chat_key(chat_id)
    book = storage.read_path("usernames", chat, default={})
    target = str(user_id)
    for name, stored in book.items():
        if str(stored) == target:
            return name
    return None


def forget(chat_id, username):
    key = normalize(username)
    if not key:
        return False
    chat = accounts.chat_key(chat_id)
    with storage.transaction() as data:
        book = data.setdefault("usernames", {}).setdefault(chat, {})
        return book.pop(key, None) is not None


def entries(chat_id):
    """کپی فقط-خواندنی از دفترچهٔ این گروه."""
    chat = accounts.chat_key(chat_id)
    return dict(storage.read_path("usernames", chat, default={}))
