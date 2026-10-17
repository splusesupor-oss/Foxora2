import time

MEDIA_HISTORY = {}

MAX_MEDIA = 5
TIME_WINDOW = 10


def cleanup_expired(now=None):
    now = time.time() if now is None else now
    removed = 0
    for user_id, stamps in list(MEDIA_HISTORY.items()):
        fresh = [stamp for stamp in stamps if now - stamp < TIME_WINDOW]
        if fresh:
            MEDIA_HISTORY[user_id] = fresh[-MAX_MEDIA:]
        else:
            MEDIA_HISTORY.pop(user_id, None)
            removed += 1
    return removed


def check_media_spam(user_id, message):
    try:
        now = time.time()

        if not getattr(message, "file", None):
            return False

        history = MEDIA_HISTORY.setdefault(user_id, [])
        history[:] = [x for x in history if now - x < TIME_WINDOW]
        history.append(now)

        if len(MEDIA_HISTORY[user_id]) >= MAX_MEDIA:
            return True

        return False

    except Exception:
        return False


def clear_media(user_id):
    try:
        MEDIA_HISTORY.pop(user_id, None)
    except Exception:
        pass
