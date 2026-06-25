import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ExhibitionFallbackTest(unittest.TestCase):
    def test_missing_preview_data_is_warned_not_zero_filled(self):
        payload = json.loads((ROOT / "data/output/research_v2/exhibition_adjustment_20260625.json").read_text(encoding="utf-8"))
        missing = [race for race in payload["races"] if race["missing_warning"] == "preview_data_missing"]
        self.assertGreater(len(missing), 0)
        self.assertIsNotNone(missing[0]["morning_manshu_probability_pct"])


if __name__ == "__main__":
    unittest.main()
