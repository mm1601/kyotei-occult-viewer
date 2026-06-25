import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class OutputSchemaTest(unittest.TestCase):
    def test_existing_ranking_schema_still_has_required_keys(self):
        payload = json.loads((ROOT / "data/output/boaters_manshu_ranking_20260624.json").read_text(encoding="utf-8"))
        for key in ["version", "date", "summary", "races", "strict_races"]:
            self.assertIn(key, payload)

    def test_candidate_ranking_schema(self):
        payload = json.loads((ROOT / "data/output/research_v2/manshu_candidate_ranking_20260625.json").read_text(encoding="utf-8"))
        self.assertTrue(payload["production_unchanged"])
        row = payload["races"][0]
        for key in ["current_rank", "candidate_rank", "current_manshu_rate_pct", "candidate_manshu_probability_pct", "missing_warnings"]:
            self.assertIn(key, row)


if __name__ == "__main__":
    unittest.main()
