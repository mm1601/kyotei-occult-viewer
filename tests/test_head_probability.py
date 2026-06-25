import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class HeadProbabilityTest(unittest.TestCase):
    def test_boat_probabilities_sum_to_roughly_100(self):
        payload = json.loads((ROOT / "data/output/research_v2/head_prediction_20260616.json").read_text(encoding="utf-8"))
        for race in payload["races"]:
            total = sum(float(boat["preview_probability_pct"]) for boat in race["boats"])
            self.assertAlmostEqual(total, 100.0, delta=0.08)


if __name__ == "__main__":
    unittest.main()
