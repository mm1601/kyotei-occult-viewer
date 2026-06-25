import json
import pickle
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ClusterStabilityTest(unittest.TestCase):
    def test_cluster_model_has_reproducible_metadata(self):
        with (ROOT / "data/model/research_v2/manshu_cluster_model.pkl").open("rb") as handle:
            bundle = pickle.load(handle)
        self.assertEqual(bundle["version"], "research-v2-manshu-cluster-1")
        self.assertGreater(len(bundle["features"]), 5)
        self.assertGreater(bundle["threshold"], 0)

    def test_assignment_schema(self):
        payload = json.loads((ROOT / "data/output/research_v2/manshu_cluster_assignment_20260616.json").read_text(encoding="utf-8"))
        self.assertIn("assignments", payload)
        self.assertIn("cluster_id", payload["assignments"][0])
        self.assertIn("cluster_similarity", payload["assignments"][0])


if __name__ == "__main__":
    unittest.main()
