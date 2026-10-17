import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modules.broadcast_state import match_broadcast_trigger

CASES = {
    "اطلاع رسانی": "اطلاع رسانی",
    "اطلاع‌رسانی": "اطلاع رسانی",
    "اطلاع  رسانی": "اطلاع رسانی",
    "  اعلان  ": "اعلان",
}
for raw, expected in CASES.items():
    assert match_broadcast_trigger(raw) == expected, (raw, match_broadcast_trigger(raw))
print("BROADCAST TRIGGER REGRESSION: PASS")
