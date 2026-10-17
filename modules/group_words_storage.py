import json
import re
from pathlib import Path

from modules.runtime_paths import runtime_config_file
from modules.atomic_write import write_json

from modules.group_id import normalize_group_id

FILE = runtime_config_file("group_words.json")

PERSIAN_WORD_CHARS = r"a-zA-Z0-9_\u0600-\u06FF\uFB50-\uFDFF\uFE70-\uFEFC"

_cache = None
_cache_mtime = None


def _file_mtime():
    try:
        return FILE.stat().st_mtime_ns
    except OSError:
        return None


def load_words():
    global _cache, _cache_mtime
    mtime = _file_mtime()
    if _cache is not None and mtime == _cache_mtime:
        return _cache

    if mtime is None:
        _cache = {}
    else:
        try:
            _cache = json.loads(FILE.read_text(encoding="utf-8"))
        except Exception:
            _cache = {}

    _cache_mtime = mtime
    return _cache


def save_words(data):
    global _cache, _cache_mtime
    write_json(FILE, data, indent=2)
    _cache = data
    _cache_mtime = _file_mtime()


def add_word(group_id, word):
    data = load_words()
    gid = normalize_group_id(group_id)

    if gid not in data:
        data[gid] = []

    word = word.strip()

    if word and word not in data[gid]:
        data[gid].append(word)
        save_words(data)
        return True

    return False


def remove_word(group_id, word):
    data = load_words()
    gid = normalize_group_id(group_id)

    if gid in data and word in data[gid]:
        data[gid].remove(word)
        save_words(data)
        return True

    return False


def get_words(group_id):
    data = load_words()
    return data.get(normalize_group_id(group_id), [])


def normalize_filter_text(value):
    """Fold ي/ك, ZWNJ, punctuation, symbols, tatweel and diacritics for group-filter matching."""
    if not value:
        return ""
    # حذف «کشیده» (ـ tatweel) و علائم حرکات و اعراب
    t = re.sub(r"[\u0640\u064b-\u065f]", "", str(value))
    # تبدیل نیم‌فاصله، نشانه‌های جهت، فاصله‌های خاص، علائم نگارشی و نمادها به فاصله
    t = re.sub(r"[\u200c\u200d\u200e\u200f\ufeff\u00a0\-_.,/\\;:!؟،؛|()\[\]{}<>+=*&^%$#@~\"\'`«»…]+", " ", t)
    # یکسان‌سازی حروف مشابه فارسی/عربی
    t = t.replace("ي", "ی").replace("ك", "ک").replace("ة", "ه").replace("آ", "ا").replace("أ", "ا").replace("إ", "ا")
    return " ".join(t.lower().split())


def find_matching_filter_word(text, words):
    """Return the first group filter occurring as a whole word or phrase in the message."""
    haystack = normalize_filter_text(text)
    if not haystack:
        return None
    for word in words or ():
        needle = normalize_filter_text(word)
        if not needle:
            continue
        pattern = re.compile(
            rf"(?<![{PERSIAN_WORD_CHARS}]){re.escape(needle)}(?![{PERSIAN_WORD_CHARS}])"
        )
        if pattern.search(haystack):
            return word
    return None
