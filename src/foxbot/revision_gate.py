from __future__ import annotations

import hashlib
from collections import OrderedDict


class MessageRevisionGate:
    """Deduplicate identical revisions without suppressing a real edit.

    The key is (group, message); a changed text digest is always admitted,
    even if it arrives immediately after the previous version. Capacity is
    bounded so this cache cannot grow forever.
    """

    def __init__(self, max_entries: int = 100_000) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        self._max_entries = max_entries
        self._seen: OrderedDict[tuple[str, str], str] = OrderedDict()

    def should_process(self, group_id: str | int, message_id: str | int, text: str) -> bool:
        key = (str(group_id), str(message_id))
        hasher = hashlib.sha256()
        value = text or ""
        for offset in range(0, len(value), 4096):
            hasher.update(value[offset:offset + 4096].encode("utf-8", errors="surrogatepass"))
        digest = hasher.hexdigest()
        old = self._seen.get(key)
        if old == digest:
            self._seen.move_to_end(key)
            return False
        self._seen[key] = digest
        self._seen.move_to_end(key)
        while len(self._seen) > self._max_entries:
            self._seen.popitem(last=False)
        return True

    def forget(self, group_id: str | int, message_id: str | int) -> None:
        self._seen.pop((str(group_id), str(message_id)), None)
