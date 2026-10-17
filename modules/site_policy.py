"""In-memory domain policy for trusted, searchable, and blocked sites."""
import re
from pathlib import Path
from urllib.parse import urlparse

_BASE = Path(__file__).resolve().parent.parent / "data"
_FILES = {
    "trusted": _BASE / "trusted_sites.txt",
    "search": _BASE / "search_sites.txt",
    "blocked": _BASE / "blocked_sites.txt",
}
_CACHE = None
_URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>()]+|\b[a-zA-Z0-9.-]+\.(?:com|ir|net|org|info|me|co|io|app|xyz|tv|edu|gov)\b[^\s<>()]*", re.I)


def _read(path):
    try:
        return {
            line.strip().lower().lstrip(".")
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
    except OSError:
        return set()


def load():
    """Load all lists once into RAM; explicit reload is intentionally absent."""
    global _CACHE
    if _CACHE is None:
        _CACHE = {name: _read(path) for name, path in _FILES.items()}
    return _CACHE


def _host(value):
    value = str(value or "").strip().rstrip(".,!?،؟")
    if not value:
        return ""
    parsed = urlparse(value if "://" in value else "//" + value)
    return (parsed.hostname or "").lower().lstrip(".")


def _matches(host, domains):
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def classify(value):
    """Return ``blocked``, ``trusted``, ``search``, or ``unknown``."""
    host = _host(value)
    if not host:
        return "unknown", ""
    lists = load()
    if _matches(host, lists["blocked"]):
        return "blocked", host
    if _matches(host, lists["trusted"]):
        return "trusted", host
    if _matches(host, lists["search"]):
        return "search", host
    return "unknown", host


def links(text):
    return [match.group(0) for match in _URL_RE.finditer(str(text or ""))]
