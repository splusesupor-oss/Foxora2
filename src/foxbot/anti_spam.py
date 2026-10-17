from __future__ import annotations

import hashlib
import re
import time
from collections import Counter, OrderedDict, deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from foxbot.matching import normalize_persian
from foxbot.site_policy import find_untrusted_link

MAX_TRACKED_SENDERS = 1024
MAX_MESSAGES_PER_SENDER = 32
MAX_SEEN_MESSAGES = 50_000
MAX_TRACKED_ALBUMS = 1024
MAX_TRACKED_FLAGS = 2048

_REPEAT_WINDOW_SECONDS = 30.0
_PROMO_WINDOW_SECONDS = 60.0
_FLOOD_WINDOW_SECONDS = 10.0
_GIF_FLAG_SECONDS = 60 * 60.0
_GIF_HISTORY_IDLE_SECONDS = _GIF_FLAG_SECONDS
_FORWARD_WINDOW_SECONDS = 10 * 60.0
_FORWARD_ALBUM_SECONDS = 30.0
_INCIDENT_SECONDS = 120.0

_TOKEN_RE = re.compile(
    r"[\w\ufb50-\ufdff\ufe70-\ufefc]+",
    re.IGNORECASE | re.UNICODE,
)
_NONWORD_RE = re.compile(r"[\W]+", re.UNICODE)
_FLOOD_PUNCT_RE = re.compile(r"[^\w\s]+", re.UNICODE)
_SOURCE_REPEAT_NONWORD_RE = re.compile(r"[^\wآ-ی]+", re.UNICODE)
_STRETCH_RE = re.compile(r"(.)\1{2,}", re.UNICODE)
_PROMO_STRETCH_RE = re.compile(r"(.)\1+", re.UNICODE)
_USERNAME_PATTERN = re.compile(
    r"(?<![\w.@])@[\w\d_]{4,32}(?![\w]|\.[A-Za-z]{2,}\b)|"
    r"(?:آیدی|ایدی|پی وی|پی‌وی|pv)\s*[:：]?\s*@?\w+",
    re.IGNORECASE | re.UNICODE,
)
_USERNAME_AD_RE = re.compile(r"جوین|عضو|کانال|گروه|add|join", re.IGNORECASE)
_GROUP_MENTION_RE = re.compile(r"(?<![\w@])@(?:everyone|all|here)\b", re.IGNORECASE)
_LIVE_AD_RE = re.compile(r"لایو|لایوه|\blive\b", re.IGNORECASE)
_LIVE_AD_EMOJI = frozenset("👙🍑🫦😈🍈🔯")
_PHONE_CANDIDATE_RE = re.compile(r"(?<!\d)\+?\d[\d\s().-]{7,}\d(?!\d)")
_PHONE_DIGIT_TRANSLATION = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
    "01234567890123456789",
)
_PROMO_TRANSLATION = str.maketrans(
    {"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا"}
)
_FLOOD_TRANSLATION = str.maketrans(
    {
        "ي": "ی", "ى": "ی", "ئ": "ی", "ك": "ک", "ة": "ه",
        "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ؤ": "و",
        "ـ": "", "\u200c": " ", "\u200d": " ", "\u200f": "",
        "\u200e": "", "\u202a": "", "\u202b": "",
        "\u202c": "", "\u202d": "", "\u202e": "",
        "\u064b": "", "\u064c": "", "\u064d": "",
        "\u064e": "", "\u064f": "", "\u0650": "",
        "\u0651": "", "\u0652": "", "\u0653": "",
        "\u0654": "", "\u0655": "", "\ufe0f": "", "\ufe0e": "",
    }
)

# These are signal fragments, not additions to the user-maintained banned-word
# file. A single ordinary mention of a broad topic is not enough to trigger a
# campaign; the detector requires repeated/near-identical promotional payloads.
_STRONG_PROMO_FRAGMENTS = (
    "بیوچک",
    "چکبیو",
    "بیاپیوی",
    "بیاپیویم",
    "بیاگروه",
    "بیاچنل",
    "بیاکانال",
    "جوینشو",
    "جوینکن",
    "جوینکانال",
    "عضوشو",
    "عضوکن",
    "عضوکانال",
    "فالوکن",
    "فولوکن",
    "فیلمدارم",
    "فیلمگذاشتم",
    "فیلمگذاشتمبیوم",
    "فیلمجدید",
    "ادپیوی",
    "پیویپیام",
    "حالپی",
    "تمامسانسور",
    "حالمیدم",
    "حالمیذم",
    "فیلمپی",
    "تادیرنشده",
    "بکوب",
)
_WEAK_PROMO_TOKENS = frozenset(
    {"جوین", "بیوچک", "چکبیو", "فیلم", "نود", "پیوی", "پیویم", "لینک", "فالو", "فولو"}
)
_CAMPAIGN_ANCHORS = frozenset(
    {
        "بیوچک", "چکبیو", "بیاپیوی", "بیاپیویم", "فیلمگذاشتم",
        "فیلمگذاشتمبیوم", "عضوشو", "جوینشو", "جوینکانال", "ادپیوی",
        "پیویپیام", "حالپی", "تمامسانسور",
        "حالمیدم", "فیلمپی", "🔞",
    }
)
_PROMO_SHORT_CHAT = frozenset(
    {
        "سلام", "سللام", "خوبی", "خوبین", "چطوری", "ممنون", "مرسی",
        "باشه", "اره", "نه", "خیلی", "اوکی", "ok", "صبح", "بخیر",
        "شب", "خداحافظ", "امروز",
    }
)
_BENIGN_SHORT_MESSAGES = frozenset(
    {
        "سلام",
        "سلام خوبی",
        "سلام خوبین",
        "سلام دوستان",
        "سلام همگی",
        "سلام بچه ها",
        "سلام به همه",
        "خوبی",
        "خوبین",
        "خوبم",
        "خوب",
        "بله",
        "اره",
        "آره",
        "نه",
        "مرسی",
        "ممنون",
        "باشه",
        "اوکی",
        "چطوری",
        "چطوری؟",
        "شب بخیر",
        "صبح بخیر",
        "خسته نباشی",
        "دمت گرم",
        "درسته",
        "آفرین",
        "hello",
        "hi",
        "hey",
        "good morning",
        "good night",
        "thanks",
        "thank you",
        "ok",
        "okay",
        "چخبر",
        "چخبرا",
        "فعلا",
        "بای",
        "خدانگهدار",
        "عشقم",
        "رل بزنیم",
        "زندگیم",
        "دوست دارم",
        "حالت چطوره",
        "چه کاره ای",
        "کی هستی",
        "هعی",
    }
)
_STOP_TOKENS = frozenset(
    {
        "سلام", "سللام", "من", "تو", "به", "از", "که", "را", "و", "در",
        "با", "برای", "این", "آن", "هم", "می", "است", "هست", "شما",
        "منم", "خب", "چرا", "چی", "خوبی", "خوبین", "چطوری", "ممنون",
        "مرسی", "باشه", "آره", "اره", "نه", "خیلی", "اوکی", "ok",
        "صبح", "بخیر", "شب", "خداحافظ", "امروز",
    }
)
_DECORATIVE_RANGES = (
    (0x2500, 0x257F),
    (0x2580, 0x259F),
    (0x25A0, 0x25FF),
    (0x2716, 0x2716),
    (0x2726, 0x2729),
    (0x2B1B, 0x2B1C),
    (0x1100, 0x11FF),
    (0x3130, 0x318F),
    (0xAC00, 0xD7AF),
)
_DECORATIVE_EXTRA = frozenset("▰▱◾◽▪▫★☆➖➕〰❚❙❘➤➣►◄\u0640")
_INNOCENT_RUN_CHARS = frozenset(
    '.!?؟،,…-_*~():;"\'`^#\u2764\u2665'
) | frozenset("😂🤣😹😅😭😍🥰❤♥🙂😐🗿💔✨⭐🌟")
_DECORATIVE_RUN_RE = re.compile(r"(.)\1{4,}", re.UNICODE)


@dataclass(frozen=True, slots=True)
class SpamDecision:
    """An auditable, platform-independent anti-spam finding."""

    kind: str
    reason: str
    message_ids: tuple[str, ...]
    policy: str
    action: str | None = None
    duration_seconds: int | None = None
    action_required: bool = False
    notify: bool = False
    generic: bool = False


@dataclass(frozen=True, slots=True)
class _Sample:
    message_id: str
    timestamp: float
    normalized: str
    fingerprint: str
    digest: str
    repeat_key: str
    flood_key: str
    tokens: tuple[str, ...]
    promo_level: int
    promo_anchors: frozenset[str]
    low_entropy: bool


@dataclass(slots=True)
class _Album:
    expires_at: float
    message_ids: OrderedDict[str, None] = field(default_factory=OrderedDict)
    is_forward: bool = False


def _text_views(
    text: str,
) -> tuple[str, str, str, str, str, tuple[str, ...], str]:
    raw = str(text or "")
    bounded = raw[:4000]
    normalized_full = normalize_persian(bounded)
    if len(raw) > len(bounded):
        tail = normalize_persian(raw[-400:])
        digest_input = f"{normalized_full}\0{tail}\0{len(raw)}"
    else:
        digest_input = normalized_full
    digest = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()
    # Match the source history key: lowercase, then remove non-word
    # characters; preserve letter stretching and Arabic/Persian code points.
    repeat_key = _SOURCE_REPEAT_NONWORD_RE.sub("", bounded.lower())
    flood_text = bounded.translate(_FLOOD_TRANSLATION).lower()
    flood_key = _STRETCH_RE.sub(
        r"\1\1", _FLOOD_PUNCT_RE.sub("", flood_text)
    )
    tokens = tuple(_TOKEN_RE.findall(normalized_full))[:80]
    compact = "".join(tokens)[:800]
    return (
        normalized_full[:600],
        compact,
        digest,
        repeat_key,
        flood_key,
        tokens,
        normalized_full,
    )


def _promo_normalize(text: str) -> str:
    value = normalize_persian(str(text or "")).translate(_PROMO_TRANSLATION)
    value = _PROMO_STRETCH_RE.sub(r"\1", value)
    return " ".join(_NONWORD_RE.sub(" ", value).split())


def _promo_info(
    normalized: str, compact: str, tokens: tuple[str, ...]
) -> tuple[int, frozenset[str]]:
    del compact, tokens
    promo_text = _promo_normalize(normalized)
    promo_compact = promo_text.replace(" ", "")
    promo_tokens = tuple(_TOKEN_RE.findall(promo_text))[:80]
    has_emoji_marker = "🔞" in str(normalized or "")
    has_text_marker = any(
        fragment in promo_compact for fragment in _STRONG_PROMO_FRAGMENTS
    )
    is_short_token = (
        len(promo_tokens) == 1 and promo_tokens[0] in _WEAK_PROMO_TOKENS
    )
    if not (has_emoji_marker or has_text_marker or is_short_token):
        return 0, frozenset()

    token_markers = sum(
        any(
            fragment in token
            for fragment in _STRONG_PROMO_FRAGMENTS
        )
        for token in promo_tokens
    )
    repeated_marker = any(
        promo_compact.count(fragment) >= 2
        for fragment in _STRONG_PROMO_FRAGMENTS
    )
    strong = (
        len(promo_tokens) >= 2
        or promo_compact in {"بیوچک", "چکبیو"}
        or len(promo_compact) >= 10
        or token_markers >= 2
        or repeated_marker
    )
    anchors = {
        marker for marker in _CAMPAIGN_ANCHORS if marker in promo_compact
    }
    if has_emoji_marker:
        anchors.add("🔞")
    return (2 if strong else 1), frozenset(anchors)


def _is_live_ad_emoji(text: str) -> bool:
    raw = str(text or "")[:4000]
    return bool(_LIVE_AD_RE.search(raw) and any(char in raw for char in _LIVE_AD_EMOJI))


def _username_signal(text: str) -> tuple[str, str] | None:
    raw = str(text or "")[:4000]
    mentions = _USERNAME_PATTERN.findall(raw)
    if len(mentions) >= 2:
        return "multiple_usernames", "چند آیدی"
    if len(mentions) == 1:
        if _USERNAME_AD_RE.search(raw):
            return "username_ad", "آیدی در متن تبلیغاتی"
        if len(raw.strip()) < 50 and "@" in raw:
            return "public_username", "آیدی عمومی ممنوع"
    return None


def _has_phone_number(text: str) -> bool:
    raw = str(text or "")[:4000].translate(_PHONE_DIGIT_TRANSLATION)
    for match in _PHONE_CANDIDATE_RE.finditer(raw):
        candidate = match.group(0)
        digits = "".join(char for char in candidate if char.isdigit())
        if not 10 <= len(digits) <= 15 or len(set(digits)) <= 1:
            continue
        explicit_country = candidate.lstrip().startswith("+") or digits.startswith("00")
        iranian_mobile = (
            (digits.startswith("09") and len(digits) == 11)
            or (digits.startswith("9") and len(digits) == 10)
            or (digits.startswith("98") and len(digits) == 12)
        )
        iranian_landline = (
            digits.startswith("0")
            and len(digits) in {10, 11}
            and digits[1:2] in "12345678"
        )
        if explicit_country or iranian_mobile or iranian_landline:
            return True
    return False


def _decorative_spam(text: str) -> bool:
    raw = str(text or "")[:4000]
    if not raw.strip():
        return False
    count = sum(
        1
        for char in raw
        if char in _DECORATIVE_EXTRA
        or any(low <= ord(char) <= high for low, high in _DECORATIVE_RANGES)
    )
    if count >= 4:
        return True
    for match in _DECORATIVE_RUN_RE.finditer(raw):
        char = match.group(1)
        if char.isalnum() or char.isspace() or char in _INNOCENT_RUN_CHARS:
            continue
        return True
    return False


def _is_low_entropy(text: str) -> bool:
    letters = [char for char in text if char.isalnum()]
    if len(letters) < 5:
        return False
    counts = Counter(letters)
    if len(counts) <= 2 and len(letters) >= 7:
        return True
    most_common = max(counts.values(), default=0)
    return most_common / max(len(letters), 1) >= 0.66


def is_long_repetitive_payload(text: str) -> bool:
    """Mirror the source's severe long-payload shape without adding terms."""
    raw_text = str(text or "")
    if len(raw_text) > 8000:
        return False
    raw = raw_text.strip()
    if len(raw) <= 300:
        return False
    words = raw.split()
    return len(set(words)) < 20


def _message_is_trivial(normalized: str, compact: str, tokens: tuple[str, ...]) -> bool:
    if normalized in _BENIGN_SHORT_MESSAGES or " ".join(tokens) in _BENIGN_SHORT_MESSAGES:
        return True
    if len(compact) < 4:
        return True
    return len(tokens) <= 2 and all(token in _STOP_TOKENS for token in tokens)


def _campaign_matches(left: _Sample, right: _Sample) -> bool:
    if left.digest == right.digest:
        return True
    left_text, right_text = left.normalized, right.normalized
    left_compact = left_text.replace(" ", "")
    right_compact = right_text.replace(" ", "")
    if left_text == right_text or left_compact == right_compact:
        return True
    if left.promo_anchors & right.promo_anchors:
        return True

    # The source accepts a 0.72 normalized similarity. Bound each comparison
    # to 256 code points to keep worst-case CPU predictable on long captions.
    similarity = max(
        SequenceMatcher(None, left_text[:256], right_text[:256]).ratio(),
        SequenceMatcher(None, left_compact[:256], right_compact[:256]).ratio(),
    )
    if similarity >= 0.72:
        return True

    left_ordered = tuple(_TOKEN_RE.findall(left_text))[:80]
    right_ordered = tuple(_TOKEN_RE.findall(right_text))[:80]
    for width in (4, 3, 2):
        if len(left_ordered) < width or len(right_ordered) < width:
            continue
        left_phrases = {
            left_ordered[index : index + width]
            for index in range(len(left_ordered) - width + 1)
        }
        right_phrases = {
            right_ordered[index : index + width]
            for index in range(len(right_ordered) - width + 1)
        }
        for phrase in left_phrases & right_phrases:
            if width >= 3 or not all(token in _PROMO_SHORT_CHAT for token in phrase):
                return True

    return bool(
        min(len(left_compact), len(right_compact)) >= 5
        and (left_compact in right_compact or right_compact in left_compact)
    )


def _phrase_repeat_count(tokens: tuple[str, ...]) -> int:
    highest = 0
    for width in range(2, 6):
        if len(tokens) < width:
            break
        counts: dict[str, int] = {}
        for start in range(len(tokens) - width + 1):
            phrase_tokens = tokens[start : start + width]
            if len(set(phrase_tokens)) < 2:
                continue
            if all(token in _STOP_TOKENS for token in phrase_tokens):
                continue
            if sum(map(len, phrase_tokens)) < 4:
                continue
            phrase = " ".join(phrase_tokens)
            counts[phrase] = counts.get(phrase, 0) + 1
            highest = max(highest, counts[phrase])
    return highest


def _packed_repeat(tokens: tuple[str, ...], compact: str) -> bool:
    usable = [token for token in tokens if token not in _STOP_TOKENS and len(token) >= 3]
    counts: dict[str, int] = {}
    for token in usable:
        counts[token] = counts.get(token, 0) + 1
        if counts[token] >= 6:
            return True
    if _phrase_repeat_count(tokens) >= 6:
        return True
    if len(compact) < 9 or all(token in _STOP_TOKENS for token in tokens):
        return False
    known_tokens = set(tokens)
    limit = min(16, len(compact) // 2)
    for width in range(3, limit + 1):
        for start in range(width):
            piece = compact[start : start + width]
            if len(piece) < 3 or piece in known_tokens:
                continue
            if _TOKEN_RE.fullmatch(piece) and compact.count(piece) >= 6:
                return True
    return False


def _promo_repeat(tokens: tuple[str, ...], promo_level: int) -> bool:
    if promo_level <= 0:
        return False
    usable = [token for token in tokens if token not in _STOP_TOKENS and len(token) >= 3]
    counts = Counter(usable)
    return max(counts.values(), default=0) >= 2 or _phrase_repeat_count(tokens) >= 2


class SpamGuard:
    """Bounded per-(group,user) spam state; no network or platform calls."""

    def __init__(
        self,
        *,
        generic_exemption: Callable[[str, str], bool] | None = None,
        site_link_policy_enabled: bool | Callable[[], bool] = True,
        check_phone_numbers: bool = True,
        check_usernames: bool = True,
        check_forwarded: bool = False,
        max_senders: int = MAX_TRACKED_SENDERS,
        max_history_per_sender: int = MAX_MESSAGES_PER_SENDER,
    ) -> None:
        if max_senders < 1 or max_history_per_sender < 8:
            raise ValueError("spam history bounds are too small")
        self.generic_exemption = generic_exemption
        self._site_link_policy_enabled = (
            site_link_policy_enabled
            if callable(site_link_policy_enabled)
            else lambda: bool(site_link_policy_enabled)
        )
        self.check_phone_numbers = bool(check_phone_numbers)
        self.check_usernames = bool(check_usernames)
        self.check_forwarded = bool(check_forwarded)
        self.max_senders = max_senders
        self.max_history_per_sender = max_history_per_sender
        self._history: OrderedDict[tuple[str, str], deque[_Sample]] = OrderedDict()
        self._seen_messages: OrderedDict[tuple[str, str, str], float] = OrderedDict()
        self._gif_history: OrderedDict[tuple[str, str], deque[tuple[float, str]]] = OrderedDict()
        self._gif_flags: OrderedDict[tuple[str, str], float] = OrderedDict()
        self._forward_history: OrderedDict[tuple[str, str], deque[tuple[float, str]]] = OrderedDict()
        self._forward_action_pending: OrderedDict[tuple[str, str], float] = OrderedDict()
        self._albums: OrderedDict[tuple[str, str, str], _Album] = OrderedDict()
        self._incidents: OrderedDict[tuple[str, str], float] = OrderedDict()
        self._inspections = 0

    def inspect(
        self,
        group_id: str | int,
        user_id: str | int,
        message_id: str | int,
        text: str = "",
        *,
        media_kind: str | None = None,
        forwarded: bool = False,
        grouped_id: str | int | None = None,
        is_command: bool = False,
        edited: bool = False,
        now: float | None = None,
    ) -> SpamDecision | None:
        if edited:
            return None
        gid, uid, mid = str(group_id), str(user_id), str(message_id)
        if not gid or not uid or not mid:
            return None
        current = time.monotonic() if now is None else float(now)
        key = (gid, uid)
        if self._mark_seen(key, mid, current):
            return None
        self._inspections += 1
        if self._inspections % 4096 == 0:
            self._prune_expired(current)

        if self.check_forwarded and (forwarded or grouped_id is not None):
            forward_decision = self._inspect_forward(
                key, mid, current, grouped_id, forwarded=forwarded
            )
            if forward_decision is not None:
                return forward_decision
        if media_kind == "gif":
            gif_decision = self._inspect_gif(key, mid, current)
            if gif_decision is not None:
                return gif_decision

        incident = self._incident_decision(key, mid, current)
        if incident is not None:
            return incident
        if is_command:
            return None
        if not str(text or "").strip():
            return None
        if _decorative_spam(text):
            message_ids = (mid,)
            (
                _,
                promo_compact,
                _,
                _,
                _,
                promo_tokens,
                promo_normalized,
            ) = _text_views(text)
            promo_level, _ = _promo_info(
                promo_normalized, promo_compact, promo_tokens
            )
            if promo_level:
                previous = self._history.get(key, ())
                message_ids = tuple(
                    item.message_id
                    for item in previous
                    if item.promo_level
                    and current - item.timestamp <= _PROMO_WINDOW_SECONDS
                ) + (mid,)
            return self._start_incident(
                key,
                current,
                kind="decorative_spam",
                reason="الگوی تزئینی تبلیغاتی",
                message_ids=message_ids,
                policy="ban",
            )

        (
            normalized,
            compact,
            digest,
            repeat_key,
            flood_key,
            tokens,
            full_normalized,
        ) = _text_views(text)
        if not compact and "🔞" not in str(text or ""):
            return None
        promo_level, anchors = _promo_info(full_normalized, compact, tokens)
        sample = _Sample(
            message_id=mid,
            timestamp=current,
            normalized=_promo_normalize(normalized),
            fingerprint=compact[:400],
            digest=digest,
            repeat_key=repeat_key,
            flood_key=flood_key,
            tokens=tokens,
            promo_level=promo_level,
            promo_anchors=anchors,
            low_entropy=_is_low_entropy(compact),
        )
        history = self._touch_history(key)
        self._prune_history(history, current, _PROMO_WINDOW_SECONDS)
        history.append(sample)
        while len(history) > self.max_history_per_sender:
            history.popleft()

        if _promo_repeat(tokens, promo_level) or _packed_repeat(tokens, compact):
            promotional_ids = tuple(
                prior.message_id for prior in history if prior.promo_level > 0
            )
            return self._start_incident(
                key,
                current,
                kind="packed_repeat",
                reason="تکرار فشردهٔ عبارت",
                message_ids=promotional_ids or (mid,),
                policy="ban",
            )

        if promo_level:
            if promo_level == 1:
                campaign = []
                for prior in reversed(history):
                    if (
                        prior.promo_level == 1
                        and current - prior.timestamp <= _PROMO_WINDOW_SECONDS
                        and _campaign_matches(sample, prior)
                    ):
                        campaign.append(prior)
                    else:
                        break
                campaign.reverse()
            else:
                campaign = [
                    prior
                    for prior in history
                    if prior.promo_level > 0
                    and current - prior.timestamp <= _PROMO_WINDOW_SECONDS
                    and _campaign_matches(sample, prior)
                ]
            required = 2 if promo_level >= 2 else 4
            if len(campaign) >= required:
                wave_rows = (
                    [
                        prior
                        for prior in history
                        if prior.promo_level
                        and current - prior.timestamp <= _PROMO_WINDOW_SECONDS
                    ]
                    if promo_level >= 2
                    else campaign
                )
                return self._start_incident(
                    key,
                    current,
                    kind="promo_wave",
                    reason="موج تبلیغاتی",
                    message_ids=tuple(item.message_id for item in wave_rows),
                    policy="ban",
                )

        generic_exempt = self._generic_is_exempt(gid, uid)
        if sample.low_entropy:
            low_entropy_rows = [
                prior
                for prior in history
                if prior.low_entropy and current - prior.timestamp <= _FLOOD_WINDOW_SECONDS
            ]
            if len(low_entropy_rows) >= 3 and not generic_exempt:
                return self._start_incident(
                    key,
                    current,
                    kind="low_entropy",
                    reason="پیام‌های کم‌تنوع",
                    message_ids=tuple(item.message_id for item in low_entropy_rows),
                    policy="ban",
                    generic=True,
                )

        if not _message_is_trivial(normalized, compact, tokens):
            repeats = [
                prior
                for prior in history
                if prior.repeat_key == sample.repeat_key
                and current - prior.timestamp <= _REPEAT_WINDOW_SECONDS
            ]
            # The source history key preserves stretching and counts the current
            # post after insertion: three exact normalized copies in 30 seconds.
            if len(repeats) >= 3 and not generic_exempt:
                return self._start_incident(
                    key,
                    current,
                    kind="repeat",
                    reason="اسپم تکراری",
                    message_ids=tuple(item.message_id for item in repeats),
                    policy="ban",
                    generic=True,
                )

            flood_repeats = [
                prior
                for prior in history
                if len(sample.flood_key) >= 3
                and prior.flood_key == sample.flood_key
                and current - prior.timestamp <= _FLOOD_WINDOW_SECONDS
            ]
            if len(flood_repeats) >= 4 and not generic_exempt:
                return self._start_incident(
                    key,
                    current,
                    kind="repeat",
                    reason="ارسال تکراری مشابه",
                    message_ids=tuple(item.message_id for item in flood_repeats),
                    policy="ban",
                    generic=True,
                )

        flood_rows = [
            prior
            for prior in history
            if current - prior.timestamp <= _FLOOD_WINDOW_SECONDS
            and len(prior.fingerprint) >= 6
            and not _message_is_trivial(prior.normalized, prior.fingerprint, prior.tokens)
        ]
        fingerprints = {prior.flood_key for prior in flood_rows}
        if (
            len(flood_rows) >= 10
            and len(fingerprints) <= 4
            and not generic_exempt
        ):
            return self._start_incident(
                key,
                current,
                kind="rapid_flood",
                reason="ارسال انبوه پیام‌های مشابه",
                message_ids=tuple(item.message_id for item in flood_rows),
                policy="ban",
                generic=True,
            )
        try:
            link_policy_enabled = bool(self._site_link_policy_enabled())
        except Exception:  # noqa: BLE001 - match the source default-on setting.
            link_policy_enabled = True
        if link_policy_enabled:
            link_match = find_untrusted_link(text)
            if link_match is not None:
                if link_match.classification == "blocked":
                    link_reason = f"دامنه مسدود: {link_match.host}"
                else:
                    link_reason = f"لینک ناشناس: {link_match.host or 'نامشخص'}"
                return SpamDecision(
                    kind="site_link",
                    reason=link_reason,
                    message_ids=(mid,),
                    policy="warning",
                )
        if self.check_phone_numbers and _has_phone_number(text):
            return SpamDecision(
                kind="phone_number",
                reason="شماره تماس",
                message_ids=(mid,),
                policy="warning",
            )
        bounded_text = str(text or "")[:4000]
        username_signal = _username_signal(bounded_text) if self.check_usernames else None
        if username_signal is not None:
            kind, reason = username_signal
            return SpamDecision(
                kind=kind,
                reason=reason,
                message_ids=(mid,),
                policy="warning",
            )
        if _GROUP_MENTION_RE.search(bounded_text):
            return SpamDecision(
                kind="group_mention",
                reason="منشن گروهی",
                message_ids=(mid,),
                policy="warning",
            )
        if _is_live_ad_emoji(bounded_text):
            return SpamDecision(
                kind="live_ad_emoji",
                reason="لایو تبلیغاتی",
                message_ids=(mid,),
                policy="warning",
            )
        return None

    def reset_gif_history(self, group_id: str | int, user_id: str | int) -> None:
        """Break a consecutive-GIF run without releasing an active flag."""
        self._gif_history.pop((str(group_id), str(user_id)), None)

    def clear_user(self, group_id: str | int, user_id: str | int) -> None:
        key = (str(group_id), str(user_id))
        self._history.pop(key, None)
        self._gif_history.pop(key, None)
        self._gif_flags.pop(key, None)
        self._forward_history.pop(key, None)
        self._forward_action_pending.pop(key, None)
        self._incidents.pop(key, None)
        for album_key in tuple(self._albums):
            if album_key[:2] == key:
                self._albums.pop(album_key, None)

    def resolve_action(self, group_id: str | int, user_id: str | int, kind: str, *, success: bool) -> None:
        key = (str(group_id), str(user_id))
        if kind == "gif_burst" and not success:
            # Source keeps the one-hour GIF flag even when mute verification
            # fails; future GIFs still queue for deletion, but no success is
            # announced and the mute is not retried on every update.
            self._gif_history.pop(key, None)
        elif kind == "forward":
            self._forward_history.pop(key, None)
            self._forward_action_pending.pop(key, None)
        elif not success:
            self._incidents.pop(key, None)

    def clear_incident(self, group_id: str | int, user_id: str | int) -> None:
        self._incidents.pop((str(group_id), str(user_id)), None)

    @property
    def tracked_sender_count(self) -> int:
        return len(set(self._history) | set(self._gif_history) | set(self._forward_history))

    def _generic_is_exempt(self, group_id: str, user_id: str) -> bool:
        if self.generic_exemption is None:
            return False
        try:
            return bool(self.generic_exemption(group_id, user_id))
        except Exception:  # noqa: BLE001 - an exemption failure must not disable detection
            return False

    def _mark_seen(self, key: tuple[str, str], message_id: str, now: float) -> bool:
        full_key = (key[0], key[1], message_id)
        previous = self._seen_messages.get(full_key)
        if previous is not None and now - previous <= 300.0:
            return True
        self._seen_messages[full_key] = now
        self._seen_messages.move_to_end(full_key)
        while len(self._seen_messages) > MAX_SEEN_MESSAGES:
            self._seen_messages.popitem(last=False)
        return False

    def _touch_history(self, key: tuple[str, str]) -> deque[_Sample]:
        history = self._history.get(key)
        if history is None:
            history = deque()
            self._history[key] = history
        self._history.move_to_end(key)
        while len(self._history) > self.max_senders:
            self._history.popitem(last=False)
        return history

    @staticmethod
    def _prune_history(history: deque[_Sample], now: float, max_age: float) -> None:
        while history and now - history[0].timestamp > max_age:
            history.popleft()

    def _inspect_gif(self, key: tuple[str, str], message_id: str, now: float) -> SpamDecision | None:
        flagged_until = self._gif_flags.get(key)
        if flagged_until is not None and flagged_until > now:
            self._gif_flags.move_to_end(key)
            return SpamDecision(
                kind="gif_burst",
                reason="ارسال گیف تکراری",
                message_ids=(message_id,),
                policy="fixed_mute",
                action="mute",
                duration_seconds=3600,
                action_required=False,
            )
        if flagged_until is not None:
            self._gif_flags.pop(key, None)
            self._gif_history.pop(key, None)
        history = self._gif_history.get(key)
        if history is None:
            history = deque(maxlen=6)
            self._gif_history[key] = history
        self._gif_history.move_to_end(key)
        if history and now - history[-1][0] >= _GIF_HISTORY_IDLE_SECONDS:
            history.clear()
        # Source counts consecutive GIFs, not GIFs inside an arbitrary
        # two-minute window; non-GIF messages reset this deque separately.
        history.append((now, message_id))
        self._trim_ordered(self._gif_history, MAX_TRACKED_FLAGS)
        if len(history) < 6:
            return None
        newly_flagged = key not in self._gif_flags
        self._gif_flags[key] = now + _GIF_FLAG_SECONDS
        self._gif_flags.move_to_end(key)
        self._trim_ordered(self._gif_flags, MAX_TRACKED_FLAGS)
        return SpamDecision(
            kind="gif_burst",
            reason="ارسال گیف تکراری",
            # Keep the first GIF as the source does; only delete the five
            # duplicate/threshold messages while still applying the mute.
            message_ids=tuple(message for _stamp, message in list(history)[1:]),
            policy="fixed_mute",
            action="mute",
            duration_seconds=3600,
            action_required=newly_flagged,
            notify=newly_flagged,
        )

    def _inspect_forward(
        self,
        key: tuple[str, str],
        message_id: str,
        now: float,
        grouped_id: str | int | None,
        *,
        forwarded: bool,
    ) -> SpamDecision | None:
        ids: Iterable[str] = (message_id,)
        album: _Album | None = None
        if grouped_id is not None:
            album_key = (key[0], key[1], str(grouped_id))
            album = self._albums.get(album_key)
            if album is None or album.expires_at <= now:
                album = _Album(expires_at=now + _FORWARD_ALBUM_SECONDS)
                self._albums[album_key] = album
            album.message_ids[message_id] = None
            album.expires_at = now + _FORWARD_ALBUM_SECONDS
            album.is_forward = album.is_forward or forwarded
            self._albums.move_to_end(album_key)
            while len(album.message_ids) > 20:
                album.message_ids.popitem(last=False)
            self._trim_ordered(self._albums, MAX_TRACKED_ALBUMS)
            if not album.is_forward:
                return None
            ids = tuple(album.message_ids)
        elif not forwarded:
            return None

        history = self._forward_history.get(key)
        if history is None:
            history = deque()
            self._forward_history[key] = history
        self._forward_history.move_to_end(key)
        while history and now - history[0][0] > _FORWARD_WINDOW_SECONDS:
            history.popleft()
        if all(existing_id != message_id for _stamp, existing_id in history):
            history.append((now, message_id))
        while len(history) > 128:
            history.popleft()
        self._trim_ordered(self._forward_history, MAX_TRACKED_FLAGS)

        pending_until = self._forward_action_pending.get(key, 0.0)
        should_mute = len(history) >= 3 and pending_until <= now
        if should_mute:
            self._forward_action_pending[key] = now + 60.0
            self._forward_action_pending.move_to_end(key)
            self._trim_ordered(self._forward_action_pending, MAX_TRACKED_FLAGS)
        return SpamDecision(
            kind="forward",
            reason="ارسال فوروارد تکراری",
            message_ids=tuple(ids),
            policy="fixed_mute" if should_mute else "delete_only",
            action="mute" if should_mute else None,
            duration_seconds=24 * 60 * 60 if should_mute else None,
            action_required=should_mute,
            notify=should_mute,
        )

    def _incident_decision(
        self, key: tuple[str, str], message_id: str, now: float
    ) -> SpamDecision | None:
        expires_at = self._incidents.get(key)
        if expires_at is None:
            return None
        if expires_at <= now:
            self._incidents.pop(key, None)
            return None
        self._incidents.move_to_end(key)
        return SpamDecision(
            kind="incident",
            reason="موج هرزنامهٔ فعال",
            message_ids=(message_id,),
            policy="delete_only",
        )

    def _start_incident(
        self,
        key: tuple[str, str],
        now: float,
        *,
        kind: str,
        reason: str,
        message_ids: tuple[str, ...],
        policy: str,
        generic: bool = False,
    ) -> SpamDecision:
        self._incidents[key] = now + _INCIDENT_SECONDS
        self._incidents.move_to_end(key)
        self._trim_ordered(self._incidents, MAX_TRACKED_FLAGS)
        return SpamDecision(
            kind=kind,
            reason=reason,
            message_ids=message_ids,
            policy=policy,
            action="ban" if policy == "ban" else None,
            action_required=True,
            notify=policy == "ban",
            generic=generic,
        )

    def _prune_expired(self, now: float) -> None:
        for key, expires_at in tuple(self._incidents.items()):
            if expires_at <= now:
                self._incidents.pop(key, None)
        for key, expires_at in tuple(self._gif_flags.items()):
            if expires_at <= now:
                self._gif_flags.pop(key, None)
                self._gif_history.pop(key, None)
        for key, album in tuple(self._albums.items()):
            if album.expires_at <= now:
                self._albums.pop(key, None)
        for key, history in tuple(self._forward_history.items()):
            self._prune_pair_history(history, now, _FORWARD_WINDOW_SECONDS)
            if not history:
                self._forward_history.pop(key, None)
        for key, history in tuple(self._gif_history.items()):
            if (
                not history
                or now - history[-1][0] >= _GIF_HISTORY_IDLE_SECONDS
            ):
                self._gif_history.pop(key, None)
        while self._seen_messages:
            oldest_key, stamp = next(iter(self._seen_messages.items()))
            if now - stamp <= 300.0:
                break
            self._seen_messages.pop(oldest_key, None)

    @staticmethod
    def _prune_pair_history(history: deque[tuple[float, str]], now: float, window: float) -> None:
        while history and now - history[0][0] > window:
            history.popleft()

    @staticmethod
    def _trim_ordered(mapping: OrderedDict, limit: int) -> None:
        while len(mapping) > limit:
            mapping.popitem(last=False)
