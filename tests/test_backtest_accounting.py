import unittest

from scripts.research_v2.backtest_saved_rankings import summarize


class BacktestAccountingTest(unittest.TestCase):
    def test_roi_uses_all_hit_payouts(self):
        events = [
            {"date": "2026-01-01", "place_id": 1, "round": 1, "bet": True, "points": 10, "hit": True, "hit_manshu": False, "hit_payout_yen": 5000, "payout_yen": 5000, "is_manshu_race": False, "net_yen": 4000},
            {"date": "2026-01-01", "place_id": 1, "round": 2, "bet": True, "points": 10, "hit": True, "hit_manshu": True, "hit_payout_yen": 12000, "payout_yen": 12000, "is_manshu_race": True, "net_yen": 11000},
        ]
        row = summarize(events, {"scope": "unit"})
        self.assertEqual(row["purchase_yen"], 2000)
        self.assertEqual(row["return_yen"], 17000)
        self.assertEqual(row["roi_pct"], 850.0)

    def test_refund_or_missing_result_is_not_a_bet(self):
        events = [{"date": "2026-01-01", "place_id": 1, "round": 1, "bet": False, "points": 18, "hit": False, "hit_manshu": False, "hit_payout_yen": 0, "payout_yen": None, "is_manshu_race": False, "net_yen": 0}]
        row = summarize(events, {"scope": "unit"})
        self.assertEqual(row["bought_races"], 0)
        self.assertEqual(row["purchase_yen"], 0)


if __name__ == "__main__":
    unittest.main()
