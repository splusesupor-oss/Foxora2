"""📥 هندلرِ قابلیت «دانلود عکس» — نقطهٔ اتصالِ مستقل به ربات.

``handle`` مقدار True برمی‌گرداند یعنی پیام مصرف شد و هندلر اصلی نباید
ادامه دهد.

فعال‌سازی فقط با دستورِ دقیق و مستقل «دانلود عکس»:
  - پیام‌هایی مثل «دانلود عکس گربه» یا «دانلود عکس چجوریه؟» این قابلیت را
    فعال نمی‌کنند؛ چون بعد از «دانلود عکس» متن دیگری دارند.
  - فقط «دانلود عکس» به‌تنهایی (بدون متن بعد از آن) جریان را شروع می‌کند.

جریان:
  «دانلود عکس» → درخواستِ عبارت → کاربر عبارت را می‌فرستد → پیامِ تأیید →
  (فیلتر + جستجو + دانلود + ارسال + کسرِ سکه) — همه با قفلِ گروه و صفِ کاربر.
"""
import asyncio

from modules import photo_download
from modules.photo_download import (
    COMMAND,
    ASK_QUERY,
    BUSY_GROUP,
    BUSY_USER,
)


def _log(bot, message):
    try:
        bot.logger.log_info(message)
    except Exception:
        pass


async def handle(bot, event, chat_id, user_id, sender, text, logger=None):
    """پیامِ مربوط به دانلود عکس را پردازش می‌کند. True یعنی مصرف شد."""
    clean = (text or "").strip()

    # ۱) دستورِ شروع — فقط با دستورِ دقیق و مستقل «دانلود عکس» فعال می‌شود.
    #    یعنی پیام باید دقیقاً برابرِ «دانلود عکس» باشد؛ اگر بعد از آن متنِ
    #    دیگری آمده باشد («دانلود عکس گربه»، «دانلود عکس چجوریه؟»، ...)
    #    این قابلیت شروع نمی‌شود و پیام به‌عنوان دستور دیگری پردازش می‌شود.
    if clean == COMMAND:
        if photo_download.is_busy(chat_id, user_id):
            if chat_id in photo_download._BUSY_GROUPS:
                await event.reply(BUSY_GROUP)
            else:
                await event.reply(BUSY_USER)
            return True
        photo_download.start_session(chat_id, user_id)
        await event.reply(ASK_QUERY)
        _log(bot, f"PHOTO DOWNLOAD START chat_id={chat_id} user_id={user_id}")
        return True

    # پیام‌های بعد از تأیید، تا پایان همان task، بخشی از جریان قبلی نیستند
    # و نباید دوباره به handle_confirm برسند.
    if photo_download.is_processing(chat_id, user_id):
        _log(bot, f"PHOTO MESSAGE IGNORED while processing chat_id={chat_id} user_id={user_id}")
        return True

    # ۲) اگر کاربر جریانِ فعال دارد، پیامِ او همان مرحلهٔ جریان است
    s = photo_download.session(chat_id, user_id)
    if s is None:
        return False

    # ۲-الف) اگر هنوز عبارت داده نشده
    if s.get("query") is None:
        _log(bot, f"PHOTO QUERY RECEIVED chat_id={chat_id} user_id={user_id} query={clean!r}")
        result, payload = photo_download.handle_query(chat_id, user_id, clean)
        if result == "no_session":
            return False
        if result == "blocked":
            await event.reply(payload)
            _log(bot, f"PHOTO DOWNLOAD BLOCKED chat_id={chat_id} user_id={user_id} query={clean!r}")
            return True
        if result == "insufficient":
            await event.reply(payload)
            _log(bot, f"PHOTO INSUFFICIENT chat_id={chat_id} user_id={user_id} query={clean!r}")
            return True
        if result == "ask_confirm":
            await event.reply(payload)
            _log(bot, f"PHOTO ASK_CONFIRM chat_id={chat_id} user_id={user_id} query={clean!r}")
            return True
        return True

    # ۲-ب) عبارت داده شده → بررسیِ تأیید/لغو
    result, payload = photo_download.handle_confirm(chat_id, user_id, clean)
    if result == "cancel":
        await event.reply(payload)
        _log(bot, f"PHOTO CONFIRM CANCEL chat_id={chat_id} user_id={user_id}")
        return True
    if result == "invalid":
        await event.reply(payload)
        return True
    if result == "no_session":
        return False
    if result == "start":
        if not photo_download.begin_processing(chat_id, user_id):
            _log(bot, f"PHOTO DUPLICATE REQUEST IGNORED chat_id={chat_id} user_id={user_id}")
            return True
        # تأیید شد: اجرای کامل بدون بلاک کردن Event Loop.
        # دانلود/جستجو/ارسال عملیاتِ سنگین هستند؛ اجرای آن‌ها داخلِ
        # create_task باعث نمی‌شود دریافتِ پیام‌ها/WebSocket قفل شود
        # (رفعِ «WebSocket closed while reading» / PingRequest).
        _log(bot, f"PHOTO CONFIRM START chat_id={chat_id} user_id={user_id} query={s.get('query')!r}")
        await event.reply("🔄 در حال جستجو و ارسال تصاویر...")

        async def _run_and_reply():
            outcome, message = await photo_download.process(chat_id, user_id, bot)
            if outcome in {"done", "no_results", "error"}:
                await event.reply(message)
                _log(bot, f"PHOTO DOWNLOAD {outcome.upper()} chat_id={chat_id} user_id={user_id}")

        asyncio.create_task(_run_and_reply())
        return True

    return False
