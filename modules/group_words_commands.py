from modules.group_words_storage import add_word, remove_word, get_words
from modules.admin_storage import is_admin
from modules.group_storage import get_group_owner
from modules.owner_check import is_global_owner


def _can_manage_group_words(bot, chat_id, user_id, username):
    group_owner_id = get_group_owner(chat_id)
    return (
        is_global_owner(user_id)
        or (group_owner_id is not None and str(user_id) == str(group_owner_id))
        or is_admin(chat_id, user_id, username)
    )


async def handle_group_word_command(bot, event, text, chat_id, user_id, username=None):

    text = text.strip()

    if not username:
        try:
            sender = await event.get_sender()
            username = getattr(sender, "username", None)
        except Exception:
            username = None

    is_filter_command = (
        text.startswith("/فیلتر ")
        or text.startswith("/رفع ")
        or text in ("/فیلترها", "فیلترها", "!فیلترها")
        or text.startswith("!فیلتر ")
        or text.startswith("!رفع ")
    )
    if not _can_manage_group_words(bot, chat_id, user_id, username):
        if is_filter_command:
            await event.reply("❌ فقط مالک یا ادمین ثبت‌شده اجازه استفاده از فیلترها را دارد")
            return True
        return False


    if text.startswith("/فیلتر ") or text.startswith("!فیلتر "):
        prefix = "/فیلتر " if text.startswith("/فیلتر ") else "!فیلتر "
        word = text.replace(prefix, "", 1).strip()

        if word:
            if add_word(chat_id, word):
                await event.reply(f"☑️ کلمه «{word}» فیلتر شد")
            else:
                await event.reply("⚠️ این کلمه قبلا برای این گروه ثبت شده")

            return True


    if text.startswith("/رفع ") or text.startswith("!رفع "):
        prefix = "/رفع " if text.startswith("/رفع ") else "!رفع "
        word = text.replace(prefix, "", 1).strip()

        if word:
            if remove_word(chat_id, word):
                await event.reply(f"✅ کلمه «{word}» از فیلتر این گروه حذف شد")
            else:
                await event.reply("⚠️ این کلمه در فیلترها نیست")

            return True


    if text in ("/فیلترها", "فیلترها", "!فیلترها"):

        words = get_words(chat_id)

        if words:
            try:
                from splusthon.tl.types import MessageEntitySpoiler, MessageEntityBold
                has_entities = True
            except ImportError:
                has_entities = False

            u16 = lambda s: len(s.encode("utf-16-le")) // 2
            title = "📋 کلمات فیلتر شده:"
            full_text = f"{title}\n\n"
            entities = []
            if has_entities:
                entities.append(MessageEntityBold(offset=0, length=u16(title)))

            for i, word in enumerate(words):
                start = u16(full_text)
                full_text += word
                word_len = u16(word)
                if has_entities:
                    entities.append(MessageEntitySpoiler(offset=start, length=word_len))
                if i < len(words) - 1:
                    full_text += "\n"

            if entities:
                await event.reply(full_text, formatting_entities=entities)
            else:
                await event.reply(full_text)
        else:
            await event.reply("📋 هیچ کلمه‌ای فیلتر نشده")

        return True


    return False
