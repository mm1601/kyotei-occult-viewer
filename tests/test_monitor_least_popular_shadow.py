import importlib.util
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "monitor_least_popular_shadow.py"
SPEC = importlib.util.spec_from_file_location("monitor_least_popular_shadow", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def sample_rows():
    tenji = [6.90, 6.92, 6.95, 6.97, 6.80, 7.00]
    isshu = [37.00, 37.20, 37.10, 37.30, 36.80, 37.40]
    general = [60, 55, 40, 30, 35, 20]
    ai3 = [70, 65, 55, 50, 40, 25]
    ai = [32, 24, 18, 10, 8, 3]
    market = [35, 25, 18, 12, 5, 8]
    starts = [0.08, 0.12, 0.15, 0.22, 0.11, 0.21]
    return [
        {
            "boat_number": boat,
            "is_absent": 0,
            "ai_3ren_pct": ai3[boat - 1],
            "general_3ren_pct": general[boat - 1],
            "ai_prediction_pct": ai[boat - 1],
            "odds_prediction_pct": market[boat - 1],
            "tenji_time": tenji[boat - 1],
            "isshu_time": isshu[boat - 1],
            "start_tenji_time": starts[boat - 1],
            "finish": None,
            "henkan": 0,
        }
        for boat in range(1, 7)
    ]


class LeastPopularShadowTests(unittest.TestCase):
    def setUp(self):
        self.race = {
            "race_id": "2026-07-150101",
            "date": "2026-07-15",
            "place_id": 1,
            "place_name": "桐生",
            "slug": "kiryu",
            "round": 1,
            "deadline_time": "2026-07-15T10:00:00+09:00",
        }

    def test_both_fixed_rules_match(self):
        candidates, diagnostic = MODULE.evaluate_strategies(self.race, sample_rows())
        by_id = {candidate["strategy_id"]: candidate for candidate in candidates}
        self.assertEqual(set(by_id), {"c1_target_first_two", "c4_target_second_three"})
        self.assertEqual(by_id["c1_target_first_two"]["tickets"], ["5-1-2", "5-1-3"])
        self.assertEqual(
            by_id["c4_target_second_three"]["tickets"],
            ["1-5-3", "1-5-2", "1-5-4"],
        )
        self.assertEqual(diagnostic["target_boat"], 5)
        self.assertEqual(diagnostic["multi_debuff_boats"], [4, 6])
        self.assertTrue(diagnostic["target_exact14"])

    def test_exact_14_excludes_ai_12_or_more(self):
        rows = sample_rows()
        rows[4]["ai_prediction_pct"] = 12
        candidates, diagnostic = MODULE.evaluate_strategies(self.race, rows)
        self.assertEqual(candidates, [])
        self.assertFalse(diagnostic["target_exact14"])

    def test_market_tie_is_not_captured(self):
        rows = sample_rows()
        rows[5]["odds_prediction_pct"] = 5
        candidates, diagnostic = MODULE.evaluate_strategies(self.race, rows)
        self.assertEqual(candidates, [])
        self.assertEqual(diagnostic["reason"], "least_popular_tie")

    def test_settlement_uses_100_yen_payout(self):
        candidate = MODULE.evaluate_strategies(self.race, sample_rows())[0][1]
        captured = MODULE.capture_record(
            self.race,
            candidate,
            datetime.fromisoformat("2026-07-15T09:50:00+09:00"),
            10,
        )
        changed = MODULE.settle_record(
            captured,
            {
                "is_suspended": 0,
                "has_refund": 0,
                "winning_number3t1": "1-5-4",
                "result_payout3t1": 4780,
                "result_source": "sqlite_exact_race_id",
                "requested_race_id": self.race["race_id"],
            },
            datetime.fromisoformat("2026-07-15T10:20:00+09:00"),
        )
        self.assertTrue(changed)
        self.assertEqual(captured["status"], "settled")
        self.assertTrue(captured["settlement"]["hit"])
        self.assertEqual(captured["settlement"]["return_yen"], 4780)
        self.assertEqual(captured["settlement"]["investment_yen"], 300)
        self.assertTrue(MODULE.settlement_is_valid(captured))

    def test_settlement_hash_detects_tampering(self):
        candidate = MODULE.evaluate_strategies(self.race, sample_rows())[0][0]
        captured = MODULE.capture_record(
            self.race,
            candidate,
            datetime.fromisoformat("2026-07-15T09:50:00+09:00"),
            10,
        )
        MODULE.settle_record(
            captured,
            {
                "is_suspended": 0,
                "refund_boats": [],
                "winning_number3t1": "5-1-2",
                "result_payout3t1": 10000,
                "result_source": "sqlite_exact_race_id",
                "requested_race_id": self.race["race_id"],
            },
            datetime.fromisoformat("2026-07-15T10:20:00+09:00"),
        )
        self.assertTrue(MODULE.settlement_is_valid(captured))
        captured["settlement"]["return_yen"] = 999999
        self.assertFalse(MODULE.settlement_is_valid(captured))

    def test_partial_refund_keeps_unaffected_tickets(self):
        candidate = MODULE.evaluate_strategies(self.race, sample_rows())[0][1]
        captured = MODULE.capture_record(
            self.race,
            candidate,
            datetime.fromisoformat("2026-07-15T09:50:00+09:00"),
            10,
        )
        MODULE.settle_record(
            captured,
            {
                "is_suspended": 0,
                "refund_boats": [3],
                "winning_number3t1": "1-5-4",
                "result_payout3t1": 4780,
                "result_source": "sqlite_exact_race_id",
                "requested_race_id": self.race["race_id"],
            },
            datetime.fromisoformat("2026-07-15T10:20:00+09:00"),
        )
        self.assertEqual(captured["status"], "settled")
        self.assertEqual(captured["settlement"]["refunded_tickets"], ["1-5-3"])
        self.assertEqual(captured["settlement"]["investment_yen"], 200)
        self.assertEqual(captured["settlement"]["return_yen"], 4780)

    def test_snapshot_under_five_minutes_is_not_valid(self):
        candidate = MODULE.evaluate_strategies(self.race, sample_rows())[0][0]
        captured = MODULE.capture_record(
            self.race,
            candidate,
            datetime.fromisoformat("2026-07-15T09:56:00+09:00"),
            4,
        )
        self.assertFalse(MODULE.capture_is_valid(captured["capture"]))

    def test_first_c4_checkpoint_can_be_promising(self):
        start = datetime.fromisoformat("2026-07-15T10:00:00+09:00")
        records = []
        previous_capture_sha256 = "GENESIS"
        for index in range(120):
            captured_at = start + timedelta(days=index * 3)
            hit = index % 6 == 0
            returned = 6_000 if hit else 0
            capture = {
                "rule_version": MODULE.RULE_VERSION,
                "protocol_id": MODULE.PROTOCOL["protocol_id"],
                "protocol_sha256": MODULE.PROTOCOL_SHA256,
                "monitor_code_sha256": MODULE.monitor_code_sha256(),
                "monitor_bundle_sha256": MODULE.monitor_bundle_sha256(),
                "runtime": {
                    "python_version": __import__("platform").python_version(),
                    "python_implementation": __import__("platform").python_implementation(),
                },
                "prediction_seq": index + 1,
                "previous_capture_sha256": previous_capture_sha256,
                "strategy_id": "c4_target_second_three",
                "target_boat": 5,
                "captured_at": captured_at.isoformat(),
                "deadline_time": (captured_at + timedelta(minutes=10)).isoformat(),
                "minutes_to_deadline": 10,
                "date": captured_at.date().isoformat(),
                "race_id": f"race-{index}",
                "capture_integrity": True,
            }
            capture["snapshot_sha256"] = MODULE.canonical_hash(capture)
            previous_capture_sha256 = capture["snapshot_sha256"]
            settlement = MODULE.seal_settlement(
                {
                    "status": "settled",
                    "hit": hit,
                    "investment_yen": 300,
                    "return_yen": returned,
                    "result_provenance": {
                        "result_source": "sqlite_exact_race_id",
                        "requested_race_id": f"race-{index}",
                    },
                }
            )
            records.append(
                {
                    "record_key": f"r{index}",
                    "status": "settled",
                    "capture": capture,
                    "settlement": settlement,
                }
            )
        result = MODULE.evaluate_checkpoint(
            "c4_target_second_three",
            records,
            {
                "complete_rate_pct": 100.0,
                "manifest_integrity_pct": 100.0,
                "workflow_complete_rate_pct": 100.0,
                "missing_manifest_days": [],
            },
            start + timedelta(days=120 * 3, hours=4),
            bootstrap_samples=2_000,
        )
        self.assertEqual(result["last_completed_checkpoint_races"], 120)
        self.assertEqual(result["status"], "PROMISING")
        self.assertTrue(all(item["pass"] for item in result["gates"]))

    def test_unresolved_early_prediction_blocks_later_results(self):
        records = [
            {"status": "open", "capture": {"prediction_seq": 1}},
            {
                "status": "settled",
                "capture": {"prediction_seq": 2},
                "settlement": {"investment_yen": 200, "return_yen": 1000, "hit": True},
            },
        ]
        stable, resolved_predictions = MODULE.resolved_prefix_records(records)
        self.assertEqual(stable, [])
        self.assertEqual(resolved_predictions, 0)

    def test_independent_schedule_mismatch_is_rejected(self):
        def races_for_place(place_id):
            return [
                {
                    "race_id": f"2026-07-15{place_id:02d}{round_no:02d}",
                    "date": "2026-07-15",
                    "place_id": place_id,
                    "place_name": f"place-{place_id}",
                    "slug": f"place-{place_id}",
                    "round": round_no,
                    "deadline_time": f"2026-07-15T{8 + round_no:02d}:00:00+09:00",
                }
                for round_no in range(1, 13)
            ]

        daily = MODULE.empty_daily_payload("2026-07-15", MODULE.FORWARD_START_DATE)
        primary = races_for_place(1)
        verification = primary + races_for_place(2)
        with self.assertRaises(RuntimeError):
            MODULE.freeze_race_manifest(
                daily,
                primary,
                verification,
                {1},
                "official-test-hash",
                datetime.fromisoformat("2026-07-15T07:02:00+09:00"),
            )


if __name__ == "__main__":
    unittest.main()
