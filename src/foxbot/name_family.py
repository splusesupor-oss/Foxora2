from __future__ import annotations

import asyncio
import html
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from collections.abc import Callable
from typing import Any, ClassVar

from foxbot.filters import WordFilter
from foxbot.matching import normalize_persian
from foxbot.storage import BotStore
from foxbot.who_knows_bank import WHO_KNOWS_BANK

NAME_FAMILY_CATEGORIES = (
    "نام", "فامیل", "شهر", "میوه", "وسیله", "حیوان", "خواننده"
)

_FIRST_NAMES = (
    "آراد", "آرش", "امیر", "امیرعلی", "امین", "باران", "بهار", "بهنام",
    "پارسا", "پرهام", "پویا", "پریا", "ترانه", "نیما", "نازنین", "نرگس",
    "سارا", "سحر", "سامان", "سپیده", "شایان", "شهاب", "علی", "یاسمن",
    "یلدا", "کیان", "کوروش", "مهسا", "مریم", "ندا", "هلیا", "رها",
    "آتنا", "آیدا", "آوین", "امید", "امیرحسین", "بهاره", "بهنوش", "پریسا",
    "پدرام", "تینا", "ثنا", "حسین",
)
_SURNAMES = (
    "احمدی", "اکبری", "امیری", "اسدی", "بابایی", "بهرامی", "بهشتی", "پاکزاد",
    "پارسایی", "حیدری", "حسینی", "خادمی", "رضایی", "رستمی", "رحیمی", "زارعی",
    "زمانی", "سلیمانی", "شریفی", "صادقی", "طاهری", "عباسی", "عابدی", "فراهانی",
    "قاسمی", "کریمی", "محمدی", "موسوی", "نادری", "نصیری", "یوسفی", "یزدانی",
)
_SINGERS = (
    "ابی", "آرون افشار", "بابک جهانبخش", "بنیامین", "پوریا حیدری", "داریوش",
    "رضا صادقی", "سالار عقیلی", "سامان جلیلی", "سیاوش قمیشی", "شادمهر", "فرهاد",
    "محسن چاوشی", "محسن یگانه", "محمد اصفهانی", "محمدرضا شجریان", "معین", "همایون",
    "حامد همایون", "علیرضا قربانی", "علیرضا طلیسچی", "مهدی احمدوند", "مهدی یراحی",
    "ناصر عبداللهی", "هایده", "گوگوش", "لیلا فروهر", "حبیب", "فریدون فروغی",
    "احسان خواجه‌امیری", "گرشا رضایی", "ماکان بند",
)
_ROUND_ADDITIONS = {
    "پ": ("پارسا", "پاکزاد", "پلدختر", "پرتقال", "پرچم", "پلنگ", "پرواز همای"),
    "ت": ("ترانه", "تهرانی", "تهران", "توت", "تبر", "تمساح", "تورج شعبانخانی"),
    "ج": ("جمشید", "جلالی", "جیرفت", "جوز", "جارو", "جغد", "جلال همتی"),
    "د": ("دارا", "درویشی", "دزفول", "دستنبو", "دماسنج", "دلفین", "داریوش"),
    "ر": ("رها", "رضایی", "رشت", "رطب", "رادیو", "روباه", "رضا صادقی"),
    "ز": ("زینب", "زارعی", "زنجان", "زردآلو", "زنگ", "زرافه", "زانیار خسروی"),
    "ع": ("علی", "عباسی", "عجب‌شیر", "عناب", "عینک", "عقاب", "علیرضا قربانی"),
    "ف": ("فرزاد", "فراهانی", "فومن", "فندق", "فنجان", "فیل", "فرهاد"),
    "گ": ("گلناز", "گودرزی", "گرگان", "گلابی", "گیره", "گربه", "گرشا رضایی"),
    "ل": ("لیلا", "لطفی", "لاهیجان", "لیمو", "لیوان", "لک‌لک", "لیلا فروهر"),
    "ه": ("هلیا", "هاشمی", "همدان", "هندوانه", "هدفون", "هدهد", "هایده"),
    "ی": ("یلدا", "یعقوبی", "یزد", "یوسفی", "یخچال", "یوز", "یاس"),
}


def normalize_answer(value: str) -> str:
    text = normalize_persian(str(value or "")).casefold()
    return "".join(char for char in text if not char.isspace() and char not in "،,.;:!?؟!ـ\"'«»()[]{}")


def _category_bank() -> dict[str, tuple[str, ...]]:
    banks: dict[str, tuple[str, ...]] = {
        "نام": _FIRST_NAMES,
        "فامیل": _SURNAMES,
        "شهر": tuple(WHO_KNOWS_BANK["شهر"]),
        "میوه": tuple(WHO_KNOWS_BANK["میوه"]),
        "وسیله": tuple(WHO_KNOWS_BANK["وسیله"]),
        "حیوان": tuple(WHO_KNOWS_BANK["حیوان"]),
        "خواننده": _SINGERS,
    }
    for answers in _ROUND_ADDITIONS.values():
        for category, answer in zip(NAME_FAMILY_CATEGORIES, answers, strict=True):
            banks[category] = (*banks[category], answer)
    return {
        category: tuple(dict.fromkeys(answers))
        for category, answers in banks.items()
    }


def _has_distinct_answers(category_answers: list[set[str]]) -> bool:
    if not category_answers or any(not answers for answers in category_answers):
        return False
    answer_owner: dict[str, int] = {}

    def assign(category_index: int, seen_answers: set[str]) -> bool:
        for answer in category_answers[category_index]:
            if answer in seen_answers:
                continue
            seen_answers.add(answer)
            previous_owner = answer_owner.get(answer)
            if previous_owner is None or assign(previous_owner, seen_answers):
                answer_owner[answer] = category_index
                return True
        return False

    return all(
        assign(index, set())
        for index in sorted(range(len(category_answers)), key=lambda item: len(category_answers[item]))
    )


def playable_name_family_letters(
    candidates: tuple[str, ...], word_filter: WordFilter | None = None
) -> tuple[str, ...]:
    safe_banks: list[set[str]] = []
    for answers in _category_bank().values():
        safe_answers: set[str] = set()
        for answer in answers:
            normalized = normalize_answer(answer)
            if normalized and (
                word_filter is None or word_filter.match_global(answer) is None
            ):
                safe_answers.add(normalized)
        safe_banks.append(safe_answers)

    playable: list[str] = []
    seen_letters: set[str] = set()
    for letter in candidates:
        normalized_letter = normalize_answer(letter)
        if (
            not normalized_letter
            or normalized_letter in seen_letters
            or (word_filter is not None and word_filter.match_global(letter) is not None)
        ):
            continue
        category_answers = [
            {answer for answer in answers if answer.startswith(normalized_letter)}
            for answers in safe_banks
        ]
        if _has_distinct_answers(category_answers):
            playable.append(letter)
            seen_letters.add(normalized_letter)
    return tuple(playable)


_CATEGORY_LABELS = {
    normalize_answer(label): category
    for category, labels in {
        "نام": ("نام", "اسم"),
        "فامیل": ("فامیل", "نام خانوادگی", "نام‌خانوادگی"),
        "شهر": ("شهر",),
        "میوه": ("میوه",),
        "وسیله": ("وسیله", "ابزار"),
        "حیوان": ("حیوان", "جانور"),
        "خواننده": ("خواننده", "آوازخوان"),
    }.items()
    for label in labels
}


def parse_submission(text: str) -> dict[str, str] | None:
    """Accept seven ordered lines or seven explicitly labeled category lines."""
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    if len(lines) != len(NAME_FAMILY_CATEGORIES):
        return None
    labeled: dict[str, str] = {}
    for line in lines:
        separator = next((mark for mark in (":", "：", "-", "—") if mark in line), None)
        if separator is None:
            return {
                category: value
                for category, value in zip(NAME_FAMILY_CATEGORIES, lines, strict=True)
            }
        label, answer = line.split(separator, 1)
        category = _CATEGORY_LABELS.get(normalize_answer(label))
        if category is None or category in labeled or not answer.strip():
            return None
        labeled[category] = answer.strip()
    return labeled if len(labeled) == len(NAME_FAMILY_CATEGORIES) else None


class NameFamilyService:
    """Validate category answers offline first, then use a bounded public lookup."""

    _CATEGORY_HINTS: ClassVar[dict[str, tuple[str, ...]]] = {
        "نام": ("نام کوچک", "نام", "given name", "first name"),
        "فامیل": ("نام خانوادگی", "surname", "family name"),
        "شهر": ("شهر", "city", "town"),
        "میوه": ("میوه", "fruit"),
        "وسیله": ("ابزار", "وسیله", "tool", "device", "instrument"),
        "حیوان": ("جانور", "حیوان", "animal", "species"),
        "خواننده": ("خواننده", "singer", "musician"),
    }

    def __init__(
        self,
        word_filter: WordFilter,
        *,
        min_observations: int = 5,
        min_unique_users: int = 3,
        min_unique_chats: int = 2,
        external_validator: Callable[[str, str], bool] | None = None,
        timeout_seconds: float = 2.0,
    ) -> None:
        if min(min_observations, min_unique_users, min_unique_chats) < 1:
            raise ValueError("name-family learning thresholds must be positive")
        if not 0.2 <= timeout_seconds <= 5.0:
            raise ValueError("name-family lookup timeout must be between 0.2 and 5 seconds")
        self.word_filter = word_filter
        self.min_observations = min_observations
        self.min_unique_users = min_unique_users
        self.min_unique_chats = min_unique_chats
        self.external_validator = external_validator
        self.timeout_seconds = timeout_seconds
        self._known_answers: dict[str, frozenset[str]] = {}
        self._request_lock = threading.Lock()
        self._async_request_lock = asyncio.Lock()
        self._cache_lock = threading.RLock()
        self._cache: OrderedDict[tuple[str, str], tuple[float, bool]] = OrderedDict()
        self.refresh_known_answers()

    def refresh_known_answers(self) -> None:
        self._known_answers = {
            category: frozenset(
                normalized
                for answer in answers
                if answer and self.word_filter.match_global(answer) is None
                if (normalized := normalize_answer(answer))
            )
            for category, answers in _category_bank().items()
        }
        with self._cache_lock:
            self._cache.clear()

    async def is_valid(
        self,
        store: BotStore,
        *,
        category: str,
        letter: str,
        answer: str,
        group_id: str | int,
        user_id: str | int,
    ) -> bool:
        normalized = normalize_answer(answer)
        normalized_letter = normalize_answer(letter)
        if (
            category not in NAME_FAMILY_CATEGORIES
            or not normalized
            or len(normalized) > 80
            or not normalized_letter
            or not normalized.startswith(normalized_letter)
            or self.word_filter.match_global(answer) is not None
        ):
            return False
        if normalized in self._known_answers.get(category, frozenset()):
            return True
        if store.name_family_is_learned(
            category=category,
            letter=normalized_letter,
            normalized_answer=normalized,
            min_observations=self.min_observations,
            min_unique_users=self.min_unique_users,
            min_unique_chats=self.min_unique_chats,
        ):
            return True
        cache_key = (category, normalized)
        with self._cache_lock:
            cached = self._cache.get(cache_key)
            if cached is not None and cached[0] > time.monotonic():
                self._cache.move_to_end(cache_key)
                confirmed = cached[1]
            else:
                confirmed = None
        if confirmed is None:
            try:
                async with self._async_request_lock:
                    with self._cache_lock:
                        cached = self._cache.get(cache_key)
                        if cached is not None and cached[0] > time.monotonic():
                            self._cache.move_to_end(cache_key)
                            confirmed = cached[1]
                    if confirmed is None:
                        confirmed = await asyncio.to_thread(
                            self._run_external_sync, category, answer
                        )
                        with self._cache_lock:
                            self._cache[cache_key] = (
                                time.monotonic() + 900.0, bool(confirmed)
                            )
                            self._cache.move_to_end(cache_key)
                            while len(self._cache) > 2048:
                                self._cache.popitem(last=False)
            except Exception:  # noqa: BLE001 - provider failure is a bounded negative result
                confirmed = False
        if not confirmed:
            return False
        store.record_name_family_observation(
            category=category,
            letter=normalized_letter,
            normalized_answer=normalized,
            answer=str(answer).strip(),
            group_id=group_id,
            user_id=user_id,
        )
        return True

    def _run_external_sync(self, category: str, answer: str) -> bool:
        with self._request_lock:
            validator = self.external_validator or self._lookup_category
            return bool(validator(category, answer))

    def _request_json(self, url: str) -> Any | None:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "FoxBot/1.0 (name-family validation)"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except (OSError, TimeoutError, ValueError, urllib.error.URLError):
            return None

    def _lookup_category(self, category: str, answer: str) -> bool:
        expected = normalize_answer(answer)
        hints = tuple(normalize_answer(value) for value in self._CATEGORY_HINTS[category])
        params = urllib.parse.urlencode(
            {
                "action": "wbsearchentities",
                "search": answer,
                "language": "fa",
                "format": "json",
                "limit": "5",
            }
        )
        payload = self._request_json(f"https://www.wikidata.org/w/api.php?{params}")
        results = payload.get("search", ()) if isinstance(payload, dict) else ()
        for result in results if isinstance(results, list) else ():
            if not isinstance(result, dict):
                continue
            label = normalize_answer(str(result.get("label") or ""))
            description = normalize_answer(str(result.get("description") or ""))
            if label == expected and any(hint and hint in description for hint in hints):
                return True

        query = urllib.parse.urlencode(
            {
                "action": "query",
                "list": "search",
                "srsearch": f"{answer} {category}",
                "utf8": "1",
                "format": "json",
                "srlimit": "3",
            }
        )
        wikipedia = self._request_json(f"https://fa.wikipedia.org/w/api.php?{query}")
        search = (
            wikipedia.get("query", {}).get("search", ())
            if isinstance(wikipedia, dict)
            else ()
        )
        for result in search if isinstance(search, list) else ():
            if not isinstance(result, dict):
                continue
            title = normalize_answer(str(result.get("title") or ""))
            snippet = normalize_answer(
                html.unescape(re.sub(r"<[^>]+>", " ", str(result.get("snippet") or "")))
            )
            if title == expected and any(hint and hint in snippet for hint in hints):
                return True
        return False



def is_static_answer(
    *,
    category: str,
    letter: str,
    answer: str,
    word_filter: WordFilter | None = None,
) -> bool:
    normalized = normalize_answer(answer)
    normalized_letter = normalize_answer(letter)
    if not normalized or not normalized_letter or not normalized.startswith(normalized_letter):
        return False
    if word_filter is not None and word_filter.match_global(answer) is not None:
        return False
    return normalized in {
        normalize_answer(value)
        for value in _category_bank().get(category, ())
        if value and (word_filter is None or word_filter.match_global(value) is None)
    }
