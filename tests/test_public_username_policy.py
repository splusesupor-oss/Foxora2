"""Regression checks for ordinary-user @username moderation and edits."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from modules.spam_detector import SpamDetector

class Config:
    def get(self, _key, default=None): return default
    banned_words = []
    _banned_words_version = 0

detector = SpamDetector(Config())
assert detector.has_public_username("@osineal")
assert detector.has_public_username("بیا @some_channel")
assert not detector.has_public_username("سلام")
assert not detector.has_public_username("mail@test.com")
source = (ROOT / "core" / "bot_working_split_ok.py").read_text(encoding="utf-8")
assert "@self.client.on(events.MessageEdited())" in source
print("public username policy OK")
