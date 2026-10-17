from __future__ import annotations

import re
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

_GROUP_FILTER_MARKS = re.compile(r"[\u0640\u064b-\u065f]")
_GROUP_FILTER_SEPARATORS = re.compile(
    r"[\u200c\u200d\u200e\u200f\ufeff\u00a0\-_.,/\\;:!؟،؛|()\[\]{}<>+=*&^%$#@~\"'`«»…]+"
)
_GROUP_FILTER_TRANSLATION = str.maketrans(
    {"ي": "ی", "ك": "ک", "ة": "ه", "آ": "ا", "أ": "ا", "إ": "ا"}
)
_GROUP_WORD_CHARS = r"a-zA-Z0-9_\u0600-\u06FF\uFB50-\uFDFF\uFE70-\uFEFC"
_GLOBAL_WORD_CHAR_RE = re.compile(rf"[{_GROUP_WORD_CHARS}]", re.UNICODE)
_GLOBAL_UNICODE_WORD_RE = re.compile(r"\w", re.UNICODE)
_GLOBAL_SEPARATOR_EXTRA = frozenset("_\u200c\u200d\u0640")
_GLOBAL_FILTER_MARKS = re.compile(r"[\u0640\u064b-\u065f\u0670]")
_GLOBAL_FILTER_TRANSLATION = str.maketrans(
    {
        "ي": "ی", "ى": "ی", "ئ": "ی", "ك": "ک", "ة": "ه",
        "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ؤ": "و", "ص": "س",
    }
)
_GLOBAL_SIMILAR_LETTERS = {
    "ک": "کك",
    "ی": "یيىئ",
    "س": "سصث",
    "ا": "اأإآٱ",
    "ه": "هة",
    "و": "وؤ",
    "ت": "تط",
}
_MAX_GLOBAL_FUZZY_TEXT = 8000
MAX_GLOBAL_WORD_FILE_BYTES = 262_144
MAX_GLOBAL_TERMS = 2048
MAX_GLOBAL_TERM_LENGTH = 512
MAX_GROUP_WORD_LIST_BYTES = 65_536
MAX_GROUP_WORD_TERMS = 256
MAX_GROUP_WORD_TERM_LENGTH = 256
MAX_GROUP_FILTER_TEXT_LENGTH = 8000


def _fold_global_filter_text(value: str | None) -> str:
    text = str(value or "").lower().replace("\ufe0f", "").replace("\ufe0e", "")
    return _GLOBAL_FILTER_MARKS.sub("", text).translate(_GLOBAL_FILTER_TRANSLATION)


def _global_letter_class(char: str) -> frozenset[str]:
    return frozenset(_GLOBAL_SIMILAR_LETTERS.get(char, char))


def _is_global_word_char(char: str) -> bool:
    return bool(_GLOBAL_WORD_CHAR_RE.fullmatch(char))


def _is_global_separator(char: str) -> bool:
    return (
        char in _GLOBAL_SEPARATOR_EXTRA
        or not bool(_GLOBAL_UNICODE_WORD_RE.fullmatch(char))
    )


@dataclass(frozen=True, slots=True)
class _GlobalPattern:
    character_classes: tuple[frozenset[str], ...]
    required_separator_before: tuple[bool, ...]
    fast_pattern: re.Pattern[str] | None


def _global_class_pattern(characters: frozenset[str]) -> str:
    if len(characters) == 1:
        return re.escape(next(iter(characters)))
    return "[" + "".join(re.escape(char) for char in sorted(characters)) + "]"


def _compile_fast_global_pattern(
    character_classes: tuple[frozenset[str], ...],
    required_separator_before: tuple[bool, ...],
) -> re.Pattern[str] | None:
    if any(
        not all(_is_global_word_char(char) and not _is_global_separator(char) for char in group)
        for group in character_classes
    ):
        return None
    if any(
        character_classes[index] & character_classes[index + 1]
        for index in range(len(character_classes) - 1)
    ):
        return None

    groups: list[tuple[frozenset[str], bool, int]] = []
    for index, character_class in enumerate(character_classes):
        required_separator = required_separator_before[index]
        if (
            groups
            and not required_separator
            and groups[-1][0] == character_class
        ):
            previous_class, previous_required, count = groups[-1]
            groups[-1] = (previous_class, previous_required, count + 1)
        else:
            groups.append((character_class, required_separator, 1))

    separator = r"[\s\u200c\u200d\u0640\W_]"
    parts: list[str] = []
    for index, (character_class, required_separator, count) in enumerate(groups):
        class_pattern = _global_class_pattern(character_class)
        if index:
            parts.append(f"(?:{separator})++" if required_separator else f"(?:{separator})*+")
        parts.append(
            f"{class_pattern}(?:{separator}*+{class_pattern}){{{count - 1},}}+"
        )
    expression = (
        rf"(?<![{_GROUP_WORD_CHARS}]){''.join(parts)}(?![{_GROUP_WORD_CHARS}])"
    )
    return re.compile(expression, re.UNICODE)


def _compile_global_pattern(term: str) -> _GlobalPattern | None:
    folded = _fold_global_filter_text(term)
    character_classes: list[frozenset[str]] = []
    required_separator_before: list[bool] = []
    need_word_separator = False
    for char in folded:
        if char.isspace() or char in "\u200c\u200d":
            if character_classes:
                need_word_separator = True
            continue
        character_classes.append(_global_letter_class(char))
        required_separator_before.append(bool(character_classes[:-1]) and need_word_separator)
        need_word_separator = False
    if not character_classes:
        return None
    classes = tuple(character_classes)
    separators = tuple(required_separator_before)
    return _GlobalPattern(
        classes,
        separators,
        _compile_fast_global_pattern(classes, separators),
    )


def _matches_global_pattern(text: str, pattern: _GlobalPattern) -> bool:
    if pattern.fast_pattern is not None:
        return pattern.fast_pattern.search(text) is not None
    classes = pattern.character_classes
    required = pattern.required_separator_before
    last_index = len(classes) - 1
    active: set[int] = set()
    text_length = len(text)

    for position, char in enumerate(text):
        next_active: set[int] = set()
        if (
            (position == 0 or not _is_global_word_char(text[position - 1]))
            and char in classes[0]
        ):
            next_active.add(0)

        is_separator = _is_global_separator(char)
        for state in active:
            if state <= last_index:
                if char in classes[state]:
                    next_active.add(state)
                next_index = state + 1
                if next_index <= last_index:
                    if required[next_index]:
                        if is_separator:
                            next_active.add(len(classes) + next_index)
                    else:
                        if char in classes[next_index]:
                            next_active.add(next_index)
                        if is_separator:
                            next_active.add(len(classes) + next_index)
            else:
                next_index = state - len(classes)
                if is_separator:
                    next_active.add(state)
                if char in classes[next_index]:
                    next_active.add(next_index)

        if last_index in next_active and (
            position + 1 == text_length
            or not _is_global_word_char(text[position + 1])
        ):
            return True
        active = next_active
    return False


def _normalize_group_filter(value: str | None) -> str:
    text = _GROUP_FILTER_MARKS.sub("", str(value or ""))
    text = _GROUP_FILTER_SEPARATORS.sub(" ", text)
    text = text.translate(_GROUP_FILTER_TRANSLATION).lower()
    return " ".join(text.split())


def _compile_group_filter_pattern(term: str) -> re.Pattern[str] | None:
    needle = _normalize_group_filter(term)
    if not needle:
        return None
    return re.compile(
        rf"(?<![{_GROUP_WORD_CHARS}]){re.escape(needle)}(?![{_GROUP_WORD_CHARS}])"
    )


@dataclass(frozen=True, slots=True)
class FilterHit:
    scope: str
    original_term: str


class WordFilter:
    """Keep global and group-specific lists separate; add no inferred variants."""

    def __init__(self, global_terms: Iterable[str] = ()) -> None:
        self._global_lock = threading.RLock()
        self._group_lock = threading.RLock()
        self._global_terms: tuple[str, ...] = ()
        self._global_patterns: tuple[tuple[str, _GlobalPattern], ...] = ()
        self._group_terms: dict[str, tuple[str, ...]] = {}
        self._group_patterns: dict[str, tuple[tuple[str, re.Pattern[str]], ...]] = {}
        self._global_disabled_groups: set[str] = set()
        self.replace_global_terms(global_terms)

    @staticmethod
    def read_file(path: str | Path) -> tuple[str, ...]:
        """Load the provided file verbatim by line; no merging or variant synthesis."""
        path = Path(path)
        try:
            with path.open("rb") as stream:
                payload = stream.read(MAX_GLOBAL_WORD_FILE_BYTES + 1)
        except FileNotFoundError:
            return ()
        if len(payload) > MAX_GLOBAL_WORD_FILE_BYTES:
            raise ValueError("approved global word file exceeds the configured size limit")
        terms: list[str] = []
        for raw in payload.decode("utf-8").splitlines():
            term = raw.strip()
            if not term or term.startswith("#"):
                continue
            if len(term) > MAX_GLOBAL_TERM_LENGTH:
                raise ValueError("approved global word term exceeds the configured length limit")
            if len(terms) >= MAX_GLOBAL_TERMS:
                raise ValueError("approved global word file exceeds the configured term limit")
            terms.append(term)
        return tuple(terms)

    @property
    def global_terms(self) -> tuple[str, ...]:
        with self._global_lock:
            return self._global_terms

    def replace_global_terms(self, terms: Iterable[str]) -> None:
        """Atomically replace only the caller-approved global list."""
        new_terms_list: list[str] = []
        total_bytes = 0
        for term in terms:
            if not isinstance(term, str):
                raise TypeError("global word terms must be strings")
            if not term.strip():
                continue
            if len(term) > MAX_GLOBAL_TERM_LENGTH:
                raise ValueError("approved global word term exceeds the configured length limit")
            total_bytes += len(term.encode("utf-8"))
            if total_bytes > MAX_GLOBAL_WORD_FILE_BYTES:
                raise ValueError("approved global word list exceeds the configured size limit")
            if len(new_terms_list) >= MAX_GLOBAL_TERMS:
                raise ValueError("approved global word list exceeds the configured term limit")
            new_terms_list.append(term)
        new_terms = tuple(new_terms_list)
        compiled_patterns = []
        for term in new_terms:
            pattern = _compile_global_pattern(term)
            if pattern is not None:
                compiled_patterns.append((term, pattern))
        compiled_patterns.sort(key=lambda item: len(item[0]), reverse=True)
        with self._global_lock:
            self._global_terms = new_terms
            self._global_patterns = tuple(compiled_patterns)

    def set_group_terms(self, group_id: str | int, terms: Iterable[str]) -> None:
        if isinstance(terms, str):
            raise TypeError("group word terms must be an iterable of strings, not one string")
        new_terms: list[str] = []
        patterns: list[tuple[str, re.Pattern[str]]] = []
        seen: set[str] = set()
        total_bytes = 0
        input_count = 0
        for term in terms:
            input_count += 1
            if input_count > MAX_GROUP_WORD_TERMS:
                raise ValueError("group word input exceeds the configured term limit")
            if not isinstance(term, str):
                raise TypeError("group word terms must be strings")
            if len(term) > MAX_GROUP_WORD_TERM_LENGTH:
                raise ValueError("group word term exceeds the configured length limit")
            if not term.strip() or term in seen:
                continue
            total_bytes += len(term.encode("utf-8"))
            if total_bytes > MAX_GROUP_WORD_LIST_BYTES:
                raise ValueError("group word list exceeds the configured size limit")
            if len(new_terms) >= MAX_GROUP_WORD_TERMS:
                raise ValueError("group word list exceeds the configured term limit")
            seen.add(term)
            new_terms.append(term)
            pattern = _compile_group_filter_pattern(term)
            if pattern is not None:
                patterns.append((term, pattern))
        group = str(group_id)
        with self._group_lock:
            if new_terms:
                self._group_terms[group] = tuple(new_terms)
                self._group_patterns[group] = tuple(patterns)
            else:
                self._group_terms.pop(group, None)
                self._group_patterns.pop(group, None)

    def group_terms(self, group_id: str | int) -> tuple[str, ...]:
        with self._group_lock:
            return self._group_terms.get(str(group_id), ())

    def clear_group_terms(self) -> None:
        with self._group_lock:
            self._group_terms.clear()
            self._group_patterns.clear()
            self._global_disabled_groups.clear()

    def set_global_enabled(self, group_id: str | int, enabled: bool) -> None:
        group = str(group_id)
        with self._group_lock:
            if enabled:
                self._global_disabled_groups.discard(group)
            else:
                self._global_disabled_groups.add(group)

    def is_global_enabled(self, group_id: str | int) -> bool:
        with self._group_lock:
            return str(group_id) not in self._global_disabled_groups

    def match_global(self, text: str) -> FilterHit | None:
        """Match only the approved global list, independent of group toggles."""
        raw_text = str(text or "")
        fuzzy_text = _fold_global_filter_text(raw_text[:_MAX_GLOBAL_FUZZY_TEXT])
        if not fuzzy_text:
            return None
        with self._global_lock:
            patterns = self._global_patterns
        fallback_first_chars = {
            char
            for _term, pattern in patterns
            if pattern.fast_pattern is None
            for char in pattern.character_classes[0]
        }
        observed_fallback_starts = {
            char
            for position, char in enumerate(fuzzy_text)
            if char in fallback_first_chars
            and (position == 0 or not _is_global_word_char(fuzzy_text[position - 1]))
        }
        for term, pattern in patterns:
            if (
                pattern.fast_pattern is None
                and not pattern.character_classes[0].intersection(observed_fallback_starts)
            ):
                continue
            if _matches_global_pattern(fuzzy_text, pattern):
                return FilterHit(scope="global", original_term=term)
        return None

    def match(
        self,
        group_id: str | int,
        text: str,
        *,
        include_global: bool = True,
    ) -> FilterHit | None:
        raw_text = str(text or "")
        with self._group_lock:
            group_patterns = self._group_patterns.get(str(group_id), ())
            global_enabled = str(group_id) not in self._global_disabled_groups
        if group_patterns:
            normalized_group_text = _normalize_group_filter(
                raw_text[:MAX_GROUP_FILTER_TEXT_LENGTH]
            )
            for term, pattern in group_patterns:
                if pattern.search(normalized_group_text) is not None:
                    return FilterHit(scope="group", original_term=term)

        if not include_global or not global_enabled:
            return None
        return self.match_global(raw_text)
