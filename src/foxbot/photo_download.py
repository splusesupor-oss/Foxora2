from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import re
import threading
import time
import uuid
from collections.abc import Awaitable, Callable, Iterable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from foxbot.storage import BotStore

ReplyCallback = Callable[..., Awaitable[Any]]
PhotoSender = Callable[[str, bytes, str], Awaitable[bool]]
PhotoUrlSender = Callable[[str, str], Awaitable[bool]]
OwnerCheck = Callable[[str | int], bool]

COMMAND = "دانلود عکس"
COST_PER_IMAGE = 10
IMAGE_COUNT = 2
SEARCH_CANDIDATES = 5
CONFIRM_TIMEOUT = 120
NETWORK_TIMEOUT = 6
DOWNLOAD_TIMEOUT = 8
PROCESS_TIMEOUT = 25
MAX_IMAGE_BYTES = 8_000_000
MAX_JSON_BYTES = 1_000_000
_SESSION_PREFIX = "photo_download:"

ASK_QUERY = "🖼️ چه تصویری می‌خواهید؟ عبارت موردنظر خود را بنویسید."
CONFIRM_TEXT = (
    "📥 دانلود تصویر\n\n"
    "برای هر عکس ۱۰ سکه برنز نیاز است.\n"
    "برای دریافت حداکثر ۲ تصویر، در صورت ارسال هر دو عکس ۲۰ سکه برنز از موجودی شما کم می‌شود.\n\n"
    "آیا تأیید می‌کنید؟\n\n"
    "بله / تایید / تأیید   → انجام\n"
    "خیر / لغو             → انصراف"
)
INSUFFICIENT = "❌ موجودی شما کمتر از ۱۰ سکه برنز است؛ این درخواست انجام نمی‌شود."
BLOCKED_CONTENT = "🚫 این درخواست ممنوع است و قابل انجام نیست."
CANCELLED = "❌ درخواست لغو شد؛ هیچ سکه‌ای کم نشد."
BUSY_GROUP = "⏳ یک دانلود عکس در همین گروه در حال پردازش است؛ لطفاً صبر کنید."
BUSY_USER = "⏳ شما درخواستِ دانلود عکسِ در حال پردازش دارید؛ لطفاً صبر کنید."
ERROR_MSG = "❌ مشکلی در جستجو/دانلود پیش آمد؛ دوباره تلاش کنید. هیچ سکه‌ای کم نشد."
NO_RESULTS = "❌ تصویر مرتبطی پیدا نشد، دوباره تلاش کنید. هیچ سکه‌ای کم نشد."
DELIVERY_CHARGE_FAILED = "⚠️ تصویر ارسال شد، اما کسر هزینه انجام نشد؛ موجودی خود را بررسی کنید."

_USER_AGENT = "FoxSoroushBot/1.0 (photo search; contact: bot administrator)"
_IMAGE_EXTENSION = re.compile(r"\.(jpe?g|png|gif|webp|avif|bmp)(?:[?#;].*)?$", re.IGNORECASE)
_QUERY_HINTS = {
    "گربه": "cat",
    "سگ": "dog",
    "روباه": "fox",
    "طبیعت": "nature",
    "ماشین": "car",
    "گل رز": "rose",
    "پروانه": "butterfly",
}
_QUERY_STOPWORDS = {"عکس", "تصویر", "تصاویر", "عکسی", "و", "با", "به", "از", "یک", "برای", "the", "a", "an", "of", "for", "and"}
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")

_TASKS: set[asyncio.Task[Any]] = set()
_LOCKS_GUARD = threading.Lock()
_GROUP_LOCKS: dict[tuple[int, str], asyncio.Lock] = {}
_GLOBAL_SEMAPHORES: dict[int, asyncio.Semaphore] = {}


def normalize(value: Any) -> str:
    text = str(value or "").strip().lower()
    for source, target in (("\u200c", " "), ("\u200f", ""), ("\u200e", ""), ("ي", "ی"), ("ك", "ک")):
        text = text.replace(source, target)
    return " ".join(text.split())


def _english_digits(value: Any) -> str:
    return str(value or "").translate(_DIGITS).strip()


def _fa(value: Any) -> str:
    return str(value).translate(_FA_DIGITS)


def _session_key(actor_id: str | int) -> str:
    return f"{_SESSION_PREFIX}{actor_id}"


def _get_session(store: BotStore, group_id: str | int, actor_id: str | int) -> dict[str, Any] | None:
    state = store.get_group_state(group_id, _session_key(actor_id))
    if not isinstance(state, dict):
        return None
    try:
        expires_at = float(state.get("expires_at", 0))
    except (TypeError, ValueError):
        expires_at = 0
    if expires_at <= time.time():
        store.delete_group_state(group_id, _session_key(actor_id))
        return None
    return state


def _group_is_processing(store: BotStore, group_id: str | int) -> bool:
    return any(
        gid == str(group_id)
        and isinstance(state, dict)
        and state.get("status") == "processing"
        for gid, _key, state in store.list_group_states_prefix(_SESSION_PREFIX, limit=1000)
    )


def _query_is_blocked(query: str, blocked_terms: Iterable[str]) -> bool:
    normalized = normalize(query)
    for term in blocked_terms:
        needle = normalize(term)
        if needle and needle in normalized:
            return True
    return False


def _is_free_owner(owner_check: OwnerCheck | None, actor_id: str | int) -> bool:
    if owner_check is None:
        return False
    try:
        return bool(owner_check(actor_id))
    except Exception:  # noqa: BLE001 - failed owner configuration must never grant free access
        return False


async def _send(event: Any, reply: ReplyCallback | None, text: str) -> Any:
    if reply is not None:
        return await reply(event, text, formatting_entities=None)
    method = getattr(event, "reply", None) or getattr(event, "respond", None)
    if not callable(method):
        raise TypeError("event has no supported reply method")
    result = method(text)
    return await result if hasattr(result, "__await__") else result


def _safe_public_url(url: str) -> bool:
    try:
        parsed = urlparse(str(url or ""))
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            return False
        host = parsed.hostname.lower().rstrip(".")
        if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
            return False
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return True
        return address.is_global
    except (TypeError, ValueError):
        return False


def _request_json(url: str) -> dict[str, Any]:
    if not _safe_public_url(url):
        return {}
    request = Request(url, headers={"User-Agent": _USER_AGENT, "Accept": "application/json"})
    try:
        with urlopen(request, timeout=NETWORK_TIMEOUT) as response:
            if getattr(response, "status", 200) != 200:
                return {}
            payload = response.read(MAX_JSON_BYTES + 1)
        if len(payload) > MAX_JSON_BYTES:
            return {}
        result = json.loads(payload.decode("utf-8"))
        return result if isinstance(result, dict) else {}
    except (HTTPError, URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}


def _query_tokens(query: str) -> set[str]:
    return {
        token.lower()
        for token in re.findall(r"[\w\u0600-\u06ff]+", normalize(query))
        if len(token) > 1 and token.lower() not in _QUERY_STOPWORDS
    }


def _relevant(query: str, title: str, tags: Iterable[str], url: str) -> bool:
    query_norm = normalize(query)
    text = normalize(" ".join((title, " ".join(tags), url)))
    if query_norm and len(query_norm) > 2 and query_norm in text:
        return True
    tokens = _query_tokens(query)
    if not tokens:
        return False
    return any(token in text for token in tokens)


def _direct_image_candidate(url: str, query: str, title: str, tags: Iterable[str]) -> bool:
    if not _safe_public_url(url):
        return False
    # Search endpoints can return landing pages. The image-byte validator below
    # is the final check; this only rejects obviously non-image links early.
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    likely_image_host = any(
        marker in host
        for marker in ("upload.wikimedia.org", "images.unsplash.com", "images.pexels.com", "openverse", "staticflickr", "imgur", "pinimg")
    )
    if not (_IMAGE_EXTENSION.search(url) or likely_image_host):
        return False
    return _relevant(query, title, tags, url)


def _search_openverse(query: str, limit: int = 20) -> list[tuple[str, str, list[str]]]:
    endpoint = "https://api.openverse.org/v1/images/?" + urlencode({"q": query, "page_size": limit})
    results = _request_json(endpoint).get("results", [])
    found: list[tuple[str, str, list[str]]] = []
    if not isinstance(results, list):
        return found
    for result in results:
        if not isinstance(result, dict):
            continue
        url = str(result.get("url") or "").strip()
        title = str(result.get("title") or "")
        raw_tags = result.get("tags") or []
        tags = [str(tag.get("name")) for tag in raw_tags if isinstance(tag, dict) and tag.get("name")]
        if url and _direct_image_candidate(url, query, title, tags):
            found.append((url, title, tags))
    return found


def _search_commons(query: str, limit: int = 15) -> list[tuple[str, str, list[str]]]:
    params = {
        "action": "query", "generator": "search", "gsrsearch": query,
        "gsrnamespace": "6", "gsrlimit": str(limit), "prop": "imageinfo",
        "iiprop": "url", "iiurlwidth": "800", "format": "json",
    }
    endpoint = "https://commons.wikimedia.org/w/api.php?" + urlencode(params)
    pages = _request_json(endpoint).get("query", {}).get("pages", {})
    found: list[tuple[str, str, list[str]]] = []
    if isinstance(pages, dict):
        for page in pages.values():
            if not isinstance(page, dict):
                continue
            info = (page.get("imageinfo") or [{}])[0]
            url = str(info.get("thumburl") or info.get("url") or "").strip()
            title = str(page.get("title") or "")
            if url and _direct_image_candidate(url, query, title, []):
                found.append((url, title, []))
    return found


def _search_wikipedia(query: str) -> list[tuple[str, str, list[str]]]:
    params = {"action": "query", "list": "search", "srsearch": query, "srlimit": "1", "format": "json"}
    endpoint = "https://fa.wikipedia.org/w/api.php?" + urlencode(params)
    hits = _request_json(endpoint).get("query", {}).get("search", [])
    if not isinstance(hits, list) or not hits:
        return []
    title = str(hits[0].get("title") or "")
    if not title:
        return []
    page_params = {
        "action": "query", "titles": title, "prop": "pageimages",
        "piprop": "thumbnail|original", "pithumbsize": "800", "format": "json",
    }
    page_url = "https://fa.wikipedia.org/w/api.php?" + urlencode(page_params)
    pages = _request_json(page_url).get("query", {}).get("pages", {})
    if not isinstance(pages, dict):
        return []
    for page in pages.values():
        if not isinstance(page, dict):
            continue
        thumbnail = page.get("thumbnail") or page.get("original") or {}
        url = str(thumbnail.get("source") or "").strip()
        if url and _direct_image_candidate(url, query, title, []):
            return [(url, title, [])]
    return []


def _english_hint(query: str) -> str | None:
    normalized = normalize(query)
    exact = _QUERY_HINTS.get(normalized)
    if exact:
        return exact
    tokens = normalized.split()
    if len(tokens) == 1:
        return _QUERY_HINTS.get(tokens[0])
    return None


async def _search_image_candidates(query: str) -> list[tuple[str, str, list[str]]]:
    exact = normalize(query)
    queries = [exact]
    hint = _english_hint(exact)
    if hint and hint != exact:
        queries.append(hint)
    providers = (_search_wikipedia, _search_commons, _search_openverse)
    jobs = [
        asyncio.to_thread(provider, search_query)
        for search_query in queries
        for provider in providers
    ]
    results = await asyncio.wait_for(
        asyncio.gather(*jobs, return_exceptions=True),
        timeout=PROCESS_TIMEOUT,
    )
    candidates: list[tuple[str, str, list[str]]] = []
    for result in results:
        if isinstance(result, BaseException):
            logging.getLogger(__name__).debug("photo provider failed: %s", type(result).__name__)
        elif isinstance(result, list):
            candidates.extend(result)
    unique: list[tuple[str, str, list[str]]] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate[0] not in seen:
            unique.append(candidate)
            seen.add(candidate[0])
        if len(unique) >= SEARCH_CANDIDATES:
            break
    return unique


def _image_format(content: bytes) -> str | None:
    if content[:3] == b"\xff\xd8\xff":
        return "jpg"
    if content[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if content[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if content[:2] == b"BM":
        return "bmp"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "webp"
    if b"ftypavif" in content[:32] or b"ftypavis" in content[:32]:
        return "avif"
    return None


def _download_image(url: str) -> bytes | None:
    if not _safe_public_url(url):
        return None
    request = Request(
        url,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,fa;q=0.8",
        },
    )
    try:
        with urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response:
            if getattr(response, "status", 200) != 200:
                return None
            final_url = str(getattr(response, "url", url) or url)
            if not _safe_public_url(final_url):
                return None
            content = response.read(MAX_IMAGE_BYTES + 1)
        if len(content) < 100 or len(content) > MAX_IMAGE_BYTES or _image_format(content) is None:
            return None
        return content
    except (HTTPError, URLError, TimeoutError, OSError):
        return None


async def _find_valid_images(query: str) -> list[dict[str, Any]]:
    candidates = await _search_image_candidates(query)
    if not candidates:
        return []
    semaphore = asyncio.Semaphore(3)

    async def download(candidate: tuple[str, str, list[str]]) -> tuple[tuple[str, str, list[str]], bytes | None]:
        async with semaphore:
            try:
                data = await asyncio.wait_for(
                    asyncio.to_thread(_download_image, candidate[0]),
                    timeout=DOWNLOAD_TIMEOUT + 1,
                )
            except TimeoutError:
                data = None
            return candidate, data

    downloaded = await asyncio.wait_for(
        asyncio.gather(*(download(candidate) for candidate in candidates)),
        timeout=PROCESS_TIMEOUT,
    )
    items: list[dict[str, Any]] = []
    for candidate, data in downloaded:
        if data is None:
            continue
        fmt = _image_format(data) or "jpg"
        items.append({"url": candidate[0], "data": data, "filename": f"photo_{len(items) + 1}.{fmt}"})
        if len(items) >= IMAGE_COUNT:
            break
    return items


def _group_lock(group_id: str | int) -> asyncio.Lock:
    loop_id = id(asyncio.get_running_loop())
    key = (loop_id, str(group_id))
    with _LOCKS_GUARD:
        lock = _GROUP_LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _GROUP_LOCKS[key] = lock
        return lock


def _global_semaphore() -> asyncio.Semaphore:
    loop_id = id(asyncio.get_running_loop())
    with _LOCKS_GUARD:
        semaphore = _GLOBAL_SEMAPHORES.get(loop_id)
        if semaphore is None:
            semaphore = asyncio.Semaphore(2)
            _GLOBAL_SEMAPHORES[loop_id] = semaphore
        return semaphore


def _links_message(items: list[dict[str, Any]]) -> str:
    lines = ["🖼️ تصویر (لینک‌های مستقیم):", ""]
    for index, item in enumerate(items, 1):
        lines.append(f"لینک {_fa(index)}:\n{item['url']}")
    return "\n\n".join(lines)


async def _send_items(
    group_id: str | int,
    items: list[dict[str, Any]],
    event: Any,
    reply: ReplyCallback | None,
    photo_sender: PhotoSender | None,
    url_sender: PhotoUrlSender | None,
) -> int:
    sent: list[bool] = []
    for item in items:
        delivered = False
        if photo_sender is not None:
            try:
                delivered = bool(
                    await photo_sender(
                        str(group_id), bytes(item["data"]), str(item["filename"])
                    )
                )
            except Exception as exc:  # noqa: BLE001 - try the platform's URL fallback
                logging.getLogger(__name__).warning("photo send failed: %s", type(exc).__name__)
        if not delivered and url_sender is not None:
            try:
                delivered = bool(await url_sender(str(group_id), str(item["url"])))
            except Exception as exc:  # noqa: BLE001 - fallback failures are reported once
                logging.getLogger(__name__).warning("photo URL send failed: %s", type(exc).__name__)
        sent.append(delivered)

    remaining = [item for item, delivered in zip(items, sent, strict=True) if not delivered]
    if remaining:
        try:
            result = await _send(event, reply, _links_message(remaining))
            if result is not False and result is not None:
                sent = [True if not delivered else delivered for delivered in sent]
        except Exception as exc:  # noqa: BLE001 - failed fallback must not trigger a debit
            logging.getLogger(__name__).warning("photo link fallback failed: %s", type(exc).__name__)
    return sum(sent)


async def _process_request(
    store: BotStore,
    *,
    event: Any,
    group_id: str | int,
    actor_id: str | int,
    reply: ReplyCallback | None,
    photo_sender: PhotoSender | None,
    url_sender: PhotoUrlSender | None,
    owner_check: OwnerCheck | None,
) -> tuple[str, str]:
    key = _session_key(actor_id)
    group_lock = _group_lock(group_id)
    semaphore = _global_semaphore()
    try:
        async with semaphore:  # noqa: SIM117 - a fixed order prevents per-group lock starvation
            async with group_lock:
                state = _get_session(store, group_id, actor_id)
                query = str((state or {}).get("query") or "").strip()
                if not query:
                    return "error", ERROR_MSG
                try:
                    items = await _find_valid_images(query)
                except Exception as exc:  # noqa: BLE001 - network/provider errors fail closed
                    logging.getLogger(__name__).warning("photo retrieval failed: %s", type(exc).__name__)
                    return "error", ERROR_MSG
                if not items:
                    return "no_results", NO_RESULTS

                free = _is_free_owner(owner_check, actor_id)
                available = IMAGE_COUNT
                if not free:
                    balance = store.get_economy_profile(group_id, actor_id)
                    available = min(IMAGE_COUNT, int(balance.get("bronze", 0)) // COST_PER_IMAGE)
                    if available < 1:
                        return "error", INSUFFICIENT
                    items = items[:available]

                delivered = await _send_items(
                    group_id, items, event, reply, photo_sender, url_sender,
                )
                if delivered <= 0:
                    return "error", ERROR_MSG
                if free:
                    return "done", f"✅ {delivered} تصویر ارسال شد. ۰ سکه برنز کسر شد."

                cost = COST_PER_IMAGE * delivered
                reference = f"photo-download:{group_id}:{actor_id}:{state.get('session_id')}"
                _profile, outcome = store.spend_group_coins(
                    group_id, actor_id, cost, "bronze", reference=reference,
                    display_name="", note=f"دانلود عکس ({delivered} تصویر)",
                )
                if outcome == "insufficient":
                    return "partial", DELIVERY_CHARGE_FAILED
                if outcome not in {"spent", "duplicate"}:
                    return "partial", DELIVERY_CHARGE_FAILED
                return "done", f"✅ {_fa(delivered)} تصویر ارسال شد. {_fa(cost)} سکه برنز کسر شد."
    finally:
        store.delete_group_state(group_id, key)


async def _run_background(
    store: BotStore,
    *,
    event: Any,
    group_id: str | int,
    actor_id: str | int,
    reply: ReplyCallback | None,
    photo_sender: PhotoSender | None,
    url_sender: PhotoUrlSender | None,
    owner_check: OwnerCheck | None,
) -> None:
    try:
        outcome, message = await _process_request(
            store,
            event=event,
            group_id=group_id,
            actor_id=actor_id,
            reply=reply,
            photo_sender=photo_sender,
            url_sender=url_sender,
            owner_check=owner_check,
        )
        if outcome in {"done", "no_results", "error", "partial"}:
            await _send(event, reply, message)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - background task must not crash the event loop
        logging.getLogger(__name__).warning("photo background task failed: %s", type(exc).__name__)
        try:
            await _send(event, reply, ERROR_MSG)
        except Exception as reply_exc:  # noqa: BLE001 - there is no further fallback
            logging.getLogger(__name__).debug("photo error notice failed: %s", type(reply_exc).__name__)
    finally:
        store.delete_group_state(group_id, _session_key(actor_id))


def _track_task(task: asyncio.Task[Any]) -> None:
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


async def handle_photo_download_command(
    event: Any,
    store: BotStore,
    *,
    group_id: str | int,
    actor_id: str | int,
    text: str,
    reply: ReplyCallback | None = None,
    photo_sender: PhotoSender | None = None,
    url_sender: PhotoUrlSender | None = None,
    owner_check: OwnerCheck | None = None,
    blocked_terms: Iterable[str] = (),
) -> bool:
    clean = str(text or "").strip()
    key = _session_key(actor_id)

    if clean == COMMAND:
        if _group_is_processing(store, group_id):
            await _send(event, reply, BUSY_GROUP)
            return True
        state = {
            "status": "query",
            "query": None,
            "session_id": uuid.uuid4().hex,
            "expires_at": time.time() + CONFIRM_TIMEOUT,
        }
        store.set_group_state(group_id, key, state)
        await _send(event, reply, ASK_QUERY)
        return True

    state = _get_session(store, group_id, actor_id)
    if state is None:
        return False
    status = state.get("status")
    if status == "processing":
        await _send(event, reply, BUSY_USER)
        return True

    if status == "query":
        query = clean[:200]
        if not query:
            await _send(event, reply, "❌ عبارت جستجو نمی‌تواند خالی باشد.")
            return True
        if _query_is_blocked(query, blocked_terms):
            store.delete_group_state(group_id, key)
            await _send(event, reply, BLOCKED_CONTENT)
            return True
        balance = store.get_economy_profile(group_id, actor_id)
        if int(balance.get("bronze", 0)) < COST_PER_IMAGE and not _is_free_owner(owner_check, actor_id):
            store.delete_group_state(group_id, key)
            await _send(event, reply, INSUFFICIENT)
            return True
        state["query"] = query
        state["status"] = "confirm"
        state["expires_at"] = time.time() + CONFIRM_TIMEOUT
        store.set_group_state(group_id, key, state)
        await _send(event, reply, CONFIRM_TEXT)
        return True

    if status == "confirm":
        choice = normalize(clean).translate(_DIGITS)
        if choice in {"لغو", "خیر", "نه", "انصراف", "بی‌خیال", "بیخیال", "cancel", "no"}:
            store.delete_group_state(group_id, key)
            await _send(event, reply, CANCELLED)
            return True
        if choice not in {"بله", "تایید", "تأیید", "آره", "اره", "بفرست", "ارسال", "ok", "yes", "بله بفرست"}:
            await _send(event, reply, CONFIRM_TEXT)
            return True
        if _group_is_processing(store, group_id):
            await _send(event, reply, BUSY_GROUP)
            return True
        state["status"] = "processing"
        state["expires_at"] = time.time() + PROCESS_TIMEOUT + 60
        store.set_group_state(group_id, key, state)
        await _send(event, reply, "🔄 در حال جستجو و ارسال تصاویر...")
        task = asyncio.create_task(_run_background(
            store,
            event=event,
            group_id=group_id,
            actor_id=actor_id,
            reply=reply,
            photo_sender=photo_sender,
            url_sender=url_sender,
            owner_check=owner_check,
        ))
        _track_task(task)
        return True

    store.delete_group_state(group_id, key)
    return False


def reset_all() -> None:
    for task in tuple(_TASKS):
        if not task.done():
            task.cancel()
    _TASKS.clear()
    with _LOCKS_GUARD:
        _GROUP_LOCKS.clear()
        _GLOBAL_SEMAPHORES.clear()


async def wait_for_background_tasks() -> None:
    tasks = tuple(_TASKS)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
