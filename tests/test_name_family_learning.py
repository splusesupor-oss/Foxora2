import tempfile
import unittest
from pathlib import Path

import modules.name_family_learning as learning


class NameFamilyLearningTests(unittest.TestCase):
    def setUp(self):
        self.original_file = learning.FILE
        self.temp = tempfile.TemporaryDirectory()
        learning.FILE = Path(self.temp.name) / "learning.json"

    def tearDown(self):
        learning.FILE = self.original_file
        self.temp.cleanup()

    def test_promotes_only_after_independent_confidence(self):
        kwargs = dict(
            category="شهر",
            letter="ن",
            raw_answer="نیکشهر",
            normalized_answer="نیکشهر",
            min_observations=3,
            min_unique_users=3,
            min_unique_chats=2,
        )
        first = learning.record(chat_id=1, user_id=1, **kwargs)
        second = learning.record(chat_id=1, user_id=2, **kwargs)
        self.assertEqual(first["status"], "learning")
        self.assertEqual(second["status"], "learning")
        third = learning.record(chat_id=2, user_id=3, **kwargs)
        self.assertEqual(third["status"], "learned")
        self.assertIn("نیکشهر", learning.learned_words()["شهر"])


if __name__ == "__main__":
    unittest.main()
