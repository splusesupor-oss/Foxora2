from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen


class FactualSearchError(RuntimeError):
    """A bounded public-source search could not produce a supported answer."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


_INTENT_MARKERS = (
    "?", "چیست", "چیه", "کجاست", "کیست", "کیه", "چگونه", "چطور", "چرا",
    "معرفی", "توضیح", "در مورد", "درباره", "بهترین", "فرق", "تاریخ",
    "اطلاعات", "مقایسه", "سرچ کن", "جستجو کن",
)
_INTENT_PREFIX = re.compile(
    r"^(?:لطفاً|لطفا|میشه|می شه|میتونی|می تونی|جستجو کن|سرچ کن|در مورد|درباره)\s+",
    re.IGNORECASE,
)
_INTENT_SUFFIX = re.compile(r"\s+(?:توضیح بده|معرفی کن|بگو|رو بگو|رو سرچ کن)$", re.IGNORECASE)
_SIMPLE_CHAT = {"سلام", "خوبی", "چه خبر", "عجب", "مرسی", "ممنون"}
_STOP_WORDS = {
    "چیست", "چیه", "کجاست", "کیست", "کیه", "چرا", "چطور", "چگونه", "بهترین",
    "اطلاعات", "درباره", "برای", "است", "هست", "را", "های", "ها", "یا", "در",
    "از", "با", "یک", "این", "آن", "چه", "شود", "کن", "کنید",
}
_QUERY_TOKEN = re.compile(r"[A-Za-z\u0600-\u06FF]{3,}")
_MAX_RESPONSE_BYTES = 1_000_000
_MAX_OUTPUT_CHARS = 1_250


def factual_intent(text: str) -> tuple[bool, str]:
    """Recognize a likely factual request and return a compact search topic."""
    value = " ".join(str(text or "").strip().split())
    normalized = value.replace("؟", "?").lower()
    if len(value) < 3 or normalized in _SIMPLE_CHAT:
        return False, ""
    if not any(marker in normalized for marker in _INTENT_MARKERS) and len(value) < 12:
        return False, ""
    topic = value
    while True:
        shortened = _INTENT_PREFIX.sub("", topic, count=1)
        if shortened == topic:
            break
        topic = shortened
    topic = _INTENT_SUFFIX.sub("", topic, count=1).strip(" ؟?،")
    return True, topic or value


class _SearchResultParser(HTMLParser):
    """Extract only visible result titles, snippets, and destination links."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.titles: list[tuple[str, str]] = []
        self.snippets: list[str] = []
        self._kind: str | None = None
        self._text: list[str] = []
        self._href = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a" or self._kind is not None:
            return
        values = dict(attrs)
        classes = set((values.get("class") or "").split())
        if "result__a" in classes:
            self._kind = "title"
            self._href = values.get("href") or ""
            self._text = []
        elif "result__snippet" in classes:
            self._kind = "snippet"
            self._href = ""
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._kind is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or self._kind is None:
            return
        text = " ".join(" ".join(self._text).split())
        if text and self._kind == "title":
            self.titles.append((text, self._href))
        elif text and self._kind == "snippet":
            self.snippets.append(text)
        self._kind = None
        self._href = ""
        self._text = []


def _safe_result_url(raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    parsed = urlparse(value)
    hostname = (parsed.hostname or "").lower()
    is_search_redirect = parsed.path.startswith("/l/") and (
        not parsed.netloc or hostname in {"duckduckgo.com", "www.duckduckgo.com"}
    )
    redirect_target = parse_qs(parsed.query).get("uddg", [""])[0]
    if is_search_redirect and redirect_target:
        value = redirect_target
        parsed = urlparse(value)
    if value.startswith("//"):
        value = "https:" + value
        parsed = urlparse(value)
    if parsed.scheme.lower() not in {"https", "http"} or not parsed.netloc:
        return ""
    return urlunparse(parsed._replace(fragment=""))


def _parse_search_results(page: str) -> list[tuple[str, str, str]]:
    parser = _SearchResultParser()
    parser.feed(str(page or ""))
    results: list[tuple[str, str, str]] = []
    for index, (title, href) in enumerate(parser.titles):
        if index >= len(parser.snippets):
            break
        snippet = parser.snippets[index]
        link = _safe_result_url(href)
        if title and snippet and link:
            results.append((title, snippet, link))
    return results


def _tokens(value: str) -> set[str]:
    return {
        item.lower()
        for item in _QUERY_TOKEN.findall(str(value or ""))
        if item.lower() not in _STOP_WORDS
    }


def _clean_visible_text(value: str, *, limit: int = 420) -> str:
    cleaned = " ".join(str(value or "").replace("\u200c", " ").split())
    return cleaned[:limit]


def _fetch(url: str, *, timeout: float = 8.0) -> bytes:
    request = Request(
        url,
        headers={
            "User-Agent": "FoxSoroushBot/1.0 (public-source factual search)",
            "Accept": "text/html,application/json;q=0.9,*/*;q=0.5",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", 200)
            if status != 200:
                raise FactualSearchError("unavailable")
            payload = response.read(_MAX_RESPONSE_BYTES + 1)
    except HTTPError as error:
        if error.code == 429 or error.code >= 500:
            raise FactualSearchError("unavailable") from error
        raise FactualSearchError("no_results") from error
    except (URLError, TimeoutError, OSError) as error:
        raise FactualSearchError("unavailable") from error
    if len(payload) > _MAX_RESPONSE_BYTES:
        raise FactualSearchError("response_too_large")
    return payload


def _search_web(query: str) -> list[tuple[str, str, str]]:
    url = "https://html.duckduckgo.com/html/?" + urlencode({"q": query})
    payload = _fetch(url)
    page = payload.decode("utf-8", errors="replace")
    if "<html" not in page.lower():
        raise FactualSearchError("no_results")
    return _parse_search_results(page)


def _rank_results(results: list[tuple[str, str, str]], topic: str) -> list[tuple[str, str, str]]:
    query_tokens = _tokens(topic)
    if not query_tokens:
        return []
    ranked: list[tuple[int, str, str, str]] = []
    seen: set[str] = set()
    for title, snippet, link in results:
        title = _clean_visible_text(title, limit=180)
        snippet = _clean_visible_text(snippet)
        combined = f"{title} {snippet}".lower()
        score = sum(token in combined for token in query_tokens)
        marker = re.sub(r"\W+", "", combined)
        if score and marker not in seen:
            seen.add(marker)
            ranked.append((score, title, snippet, link))
    ranked.sort(key=lambda row: row[0], reverse=True)
    return [(title, snippet, link) for _score, title, snippet, link in ranked[:2]]


def _wiki_fallback(query: str) -> tuple[str, str, str]:
    search_url = "https://fa.wikipedia.org/w/api.php?" + urlencode(
        {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "format": "json",
            "utf8": 1,
            "srlimit": 1,
        }
    )
    try:
        search = json.loads(_fetch(search_url).decode("utf-8"))
        rows = search.get("query", {}).get("search", [])
        title = rows[0].get("title") if rows and isinstance(rows[0], dict) else None
        if not isinstance(title, str) or not title.strip():
            raise FactualSearchError("no_results")
        summary_url = "https://fa.wikipedia.org/api/rest_v1/page/summary/" + quote(title, safe="")
        summary = json.loads(_fetch(summary_url).decode("utf-8"))
        extract = _clean_visible_text(summary.get("extract", ""), limit=700)
        shown_title = _clean_visible_text(summary.get("title") or title, limit=120)
        source_url = str(summary.get("content_urls", {}).get("desktop", {}).get("page", ""))
        source_url = _safe_result_url(source_url)
        if not extract or not shown_title or not source_url:
            raise FactualSearchError("no_results")
        return shown_title, extract, source_url
    except FactualSearchError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError, TypeError, KeyError) as error:
        raise FactualSearchError("invalid_public_response") from error


def _render_answer(rows: list[tuple[str, str, str]]) -> str:
    if not rows:
        raise FactualSearchError("no_results")
    parts = ["🔎 خلاصه بر پایهٔ منابع عمومی:\n"]
    for title, snippet, link in rows:
        parts.append(f"• {snippet}\nمنبع: {title} — {link}")
    return "\n\n".join(parts)[:_MAX_OUTPUT_CHARS]


def search_factual(topic: str) -> str:
    """Return a short, source-linked summary; this is search, not generative AI."""
    query = " ".join(str(topic or "").split())
    if len(query) < 3:
        raise FactualSearchError("no_results")
    if len(query) > 350:
        raise FactualSearchError("query_too_long")

    try:
        results = _rank_results(_search_web(query), query)
        if results:
            return _render_answer(results)
    except FactualSearchError as error:
        if error.reason == "query_too_long":
            raise

    try:
        title, extract, link = _wiki_fallback(query)
    except FactualSearchError as error:
        raise FactualSearchError(error.reason) from error
    return _render_answer([(title, extract, link)])
