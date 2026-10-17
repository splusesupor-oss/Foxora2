"""Regression test for the permanent broadcast entry triggers."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.broadcast_state import match_broadcast_trigger


CASES = {
    "اطلاع رسانی": "اطلاع رسانی",
    "اطلاع‌رسانی": "اطلاع رسانی",
    "  اطلاع  رسانی  ": "اطلاع رسانی",
    "اعلان": "اعلان",
}


def main():
    for text, expected in CASES.items():
        actual = match_broadcast_trigger(text)
        assert actual == expected, (text, actual, expected)
    print("BROADCAST TRIGGER REGRESSION TEST: PASS")


if __name__ == "__main__":
    main()
