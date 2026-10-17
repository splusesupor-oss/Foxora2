from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

# Kept separate from the user's banned-word file: these are URL-domain policy
# sets, not word-filter terms. The domain tuples reflect the active source
# policy data; the parser and matching implementation here are clean-room.
_TRUSTED_DOMAINS = frozenset(
    {
        "google.com", "youtube.com", "gmail.com", "googleapis.com",
        "github.com", "gitlab.com", "stackoverflow.com", "stackexchange.com",
        "wikipedia.org", "wikimedia.org", "archive.org", "mozilla.org",
        "firefox.com", "microsoft.com", "apple.com", "icloud.com",
        "amazon.com", "aws.amazon.com", "cloudflare.com", "docker.com",
        "kubernetes.io", "python.org", "pypi.org", "npmjs.com", "nodejs.org",
        "ubuntu.com", "linux.org", "debian.org", "redhat.com", "oracle.com",
        "ibm.com", "intel.com", "nvidia.com", "adobe.com", "dropbox.com",
        "mega.nz", "reddit.com", "linkedin.com", "twitter.com", "x.com",
        "facebook.com", "instagram.com", "telegram.org", "discord.com",
        "twitch.tv", "spotify.com", "netflix.com", "imdb.com", "w3.org",
        "ietf.org", "acm.org", "ieee.org", "mit.edu", "stanford.edu",
        "harvard.edu", "ox.ac.uk", "cam.ac.uk", "nih.gov", "nist.gov",
        "who.int", "un.org", "europa.eu",
    }
)
_SEARCH_DOMAINS = frozenset(
    {
        "google.com", "bing.com", "duckduckgo.com", "github.com",
        "wikipedia.org", "archive.org", "stackoverflow.com", "reddit.com",
        "youtube.com", "medium.com", "dev.to",
    }
)
_BLOCKED_DOMAINS = frozenset(
    {
        "pornhub.com", "xvideos.com", "xnxx.com", "xhamster.com",
        "redtube.com", "youporn.com", "onlyfans.com", "chaturbate.com",
        "stripchat.com", "cam4.com", "livejasmin.com", "spankbang.com",
        "beeg.com", "eporner.com", "hqporner.com",
    }
)
_URL_RE = re.compile(
    r"(?:https?://|www\.)[^\s<>()]+|"
    r"(?:t\.me|telegram\.me|sapp\.ir|splus\.ir|soroush\.ir)/[^\s<>()]+|"
    r"sapp\.ir|"
    r"\b[a-zA-Z0-9.-]+\.(?:com|ir|net|org|info|me|co|io|app|xyz|tv|edu|gov)"
    r"\b[^\s<>()]*",
    re.IGNORECASE,
)
_TRAILING_URL_PUNCTUATION = ".,!?:;،؟؛)]}»\"'"


@dataclass(frozen=True, slots=True)
class LinkPolicyMatch:
    """Classification for one URL-like value found in message text."""

    url: str
    host: str
    classification: str


def find_first_link(text: str) -> str | None:
    """Return the first URL-like span, following the source's first-link flow."""
    match = _URL_RE.search(str(text or "")[:4000])
    if match is None:
        return None
    return match.group(0).rstrip(_TRAILING_URL_PUNCTUATION) or None


def _host(value: str) -> str:
    cleaned = str(value or "").strip().rstrip(_TRAILING_URL_PUNCTUATION)
    if not cleaned:
        return ""
    candidate = cleaned if "://" in cleaned else f"//{cleaned}"
    try:
        host = urlsplit(candidate).hostname
    except ValueError:
        return ""
    return str(host or "").lower().rstrip(".").lstrip(".")


def _matches(host: str, domains: frozenset[str]) -> bool:
    return any(host == domain or host.endswith(f".{domain}") for domain in domains)


def classify_link(value: str) -> LinkPolicyMatch:
    """Classify a URL without DNS, HTTP, or any other network access."""
    raw_url = str(value or "").strip()
    host = _host(raw_url)
    if not host:
        return LinkPolicyMatch(raw_url, "", "unknown")
    if _matches(host, _BLOCKED_DOMAINS):
        classification = "blocked"
    elif _matches(host, _TRUSTED_DOMAINS):
        classification = "trusted"
    elif _matches(host, _SEARCH_DOMAINS):
        classification = "search"
    else:
        classification = "unknown"
    return LinkPolicyMatch(raw_url, host, classification)


def find_untrusted_link(text: str) -> LinkPolicyMatch | None:
    """Return blocked/unknown links; allow source-listed trusted/search sites.

    Soroush Plus meeting invitations on the real ``splus.ir`` host are exempt.
    A URL that merely contains that text in another host is not exempt.
    """
    url = find_first_link(text)
    if not url:
        return None
    match = classify_link(url)
    candidate = url if "://" in url else f"//{url}"
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        parsed = None
    if (
        match.host == "splus.ir"
        and parsed is not None
        and parsed.path.lower().startswith("/meet/")
    ):
        return None
    if match.classification in {"trusted", "search"}:
        return None
    return match
