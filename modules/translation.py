"""Real English-to-Persian translation workflow using MyMemory API."""
import time
from urllib.parse import urlencode

import requests
from requests.exceptions import ConnectionError, ReadTimeout, RequestException

_WAITING = {}
_WAITING_TTL = 10 * 60
_WAITING_MAX = 2000
API = "https://api.mymemory.translated.net/get"
ERROR = "❌ ارتباط با سرویس ترجمه برقرار نشد، دوباره تلاش کنید."


def _prune_waiting(now=None):
    now = time.monotonic() if now is None else now
    for key, created in list(_WAITING.items()):
        if now - float(created) > _WAITING_TTL:
            _WAITING.pop(key, None)
    while len(_WAITING) > _WAITING_MAX:
        _WAITING.pop(next(iter(_WAITING)), None)


def begin(chat_id, user_id):
    _prune_waiting()
    _WAITING[(str(chat_id), str(user_id))] = time.monotonic()


def waiting(chat_id, user_id):
    _prune_waiting()
    return (str(chat_id), str(user_id)) in _WAITING


def clear(chat_id, user_id):
    _WAITING.pop((str(chat_id), str(user_id)), None)


def translate_to_persian(text):
    query = (text or "").strip()
    if not query:
        return None, "❌ متن انگلیسی ارسال نشده است."
    url = f"{API}?{urlencode({'q': query, 'langpair': 'en|fa'})}"
    for attempt in range(3):
        try:
            response = requests.get(url, timeout=(8, 20), headers={"User-Agent": "SoroushPlusBot/1.0"})
            data = response.json()
            translated = data.get("responseData", {}).get("translatedText")
            if response.status_code == 200 and translated:
                return translated, None
            return None, ERROR
        except (ConnectionError, ReadTimeout, ValueError):
            if attempt == 2:
                return None, ERROR
            time.sleep(1 + attempt)
        except RequestException:
            return None, ERROR
    return None, ERROR
