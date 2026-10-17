from modules.security.attack_guard import check_attack, clear_attack
from modules.security.media_spam import check_media_spam, clear_media
from modules.security.delete_queue import add_delete


def check_security(user_id, message):
    result = {
        "attack": False,
        "media_spam": False
    }

    try:
        if check_attack(user_id):
            result["attack"] = True

        if check_media_spam(user_id, message):
            result["media_spam"] = True

    except Exception:
        pass

    return result


async def remove_message(chat_id, message_id):
    await add_delete(chat_id, message_id)
