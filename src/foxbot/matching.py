from __future__ import annotations

import re
import unicodedata

from foxbot.domain import MatchMode, Member

_DIACRITICS = re.compile(r"[\u064b-\u065f\u0670]")
_TRANSLATION = str.maketrans(
    {
        "ي": "ی",
        "ى": "ی",
        "ئ": "ی",
        "ك": "ک",
        "ۀ": "ه",
        "ة": "ه",
        "ؤ": "و",
    }
)
_INVISIBLE = re.compile(r"[\u200b\u200e\u200f\u202a-\u202e\u2060\ufeff]")
_SEPARATORS = re.compile(r"[\u200c\u200d\u00a0\t\r\n]+")
_SPACES = re.compile(r"\s+")


def normalize_persian(value: str | None) -> str:
    """Normalize common Arabic/Persian spelling and spacing differences.

    The original text is never modified in storage; normalization is only used
    for comparisons. ZWNJ and other spacing variants are treated as spaces.
    """
    text = unicodedata.normalize("NFKC", value or "").casefold()
    text = text.translate(_TRANSLATION)
    text = text.replace("ـ", "")
    text = _DIACRITICS.sub("", text)
    text = _INVISIBLE.sub("", text)
    text = _SEPARATORS.sub(" ", text)
    return _SPACES.sub(" ", text).strip()


def name_matches(
    member: Member,
    query: str,
    mode: MatchMode = MatchMode.CONTAINS,
    *,
    include_bio: bool = False,
) -> bool:
    """Match a criterion against display name, optionally against an available bio.

    A missing biography is simply not checked. Callers must separately report
    whether the API supplied biographies for the full scan.
    """
    needle = normalize_persian(query)
    if not needle:
        return False
    candidates = [normalize_persian(member.display_name)]
    if include_bio and member.bio is not None:
        candidates.append(normalize_persian(member.bio))
    if mode is MatchMode.EXACT:
        return any(candidate == needle for candidate in candidates)
    if mode is MatchMode.CONTAINS:
        return any(needle in candidate for candidate in candidates)
    raise ValueError(f"Unsupported match mode: {mode!r}")
