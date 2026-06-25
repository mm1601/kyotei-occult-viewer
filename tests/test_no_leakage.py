import unittest

from scripts.research_v2 import head_exhibition_validation as head
from scripts.research_v2 import data_coverage_cluster as cluster


class NoLeakageTest(unittest.TestCase):
    def test_head_features_exclude_result_columns(self):
        for mode in ["morning", "preview"]:
            self.assertFalse(set(head.feature_columns(mode)) & head.RESULT_COLUMNS)

    def test_cluster_features_exclude_result_columns(self):
        self.assertFalse(set(cluster.CLUSTER_FEATURES) & cluster.RESULT_OR_LEAKAGE_COLUMNS)


if __name__ == "__main__":
    unittest.main()
