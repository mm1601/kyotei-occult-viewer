import importlib.util
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
MODULE_PATH = SCRIPTS / "monitor_boaters_manshu_alerts.py"
SPEC = importlib.util.spec_from_file_location("monitor_boaters_manshu_alerts_test", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


STRATEGY_ID = "codex_heiwajima_r9_12_b1odds55_nige65_outertop2_wave3_h2_no1_top6"


def sample_rows():
    ai_win = [42, 24, 18, 10, 5, 3]
    ai_top3 = [78, 62, 58, 48, 35, 27]
    general_top3 = [72, 60, 54, 45, 37, 25]
    odds = [58, 18, 10, 7, 4, 3]
    tenji_ranks = [4, 3, 1, 2, 5, 6]
    lap_ranks = [4, 2, 1, 3, 5, 6]
    return [
        {
            "boat_number": boat,
            "ai_prediction_pct": ai_win[boat - 1],
            "ai_3ren_pct": ai_top3[boat - 1],
            "general_3ren_pct": general_top3[boat - 1],
            "ai_plus": ai_top3[boat - 1] + general_top3[boat - 1],
            "odds_prediction_pct": odds[boat - 1],
            "tenji_rank": tenji_ranks[boat - 1],
            "tenji_time_rank": tenji_ranks[boat - 1],
            "isshu_rank": lap_ranks[boat - 1],
            "chokusen_rank": lap_ranks[boat - 1],
            "mawariashi_rank": lap_ranks[boat - 1],
            "avg_isshu_diff": [ -0.08, 0.04, 0.18, 0.12, -0.04, -0.12][boat - 1],
            "st_rank_general": boat,
            "super_slit_alert": False,
            "double_time": boat == 3,
        }
        for boat in range(1, 7)
    ]


def sample_metrics():
    return {
        "boaters_exhibition_mode": "full",
        "tenji_boats": 6,
        "isshu_boats": 6,
        "raw_isshu_boats": 6,
        "boat1_odds_prediction_pct": 58.0,
        "boat1_odds_rank": 1,
        "boat1_nige_pct": 65.0,
        "boat1_tenji_rank": 4,
        "boat1_tenji_time_rank": 4,
        "boat1_isshu_rank": 4,
        "boat1_avg_isshu_diff": -0.08,
    }


def sample_race():
    return {
        "race_id": "2026-07-160409",
        "date": "2026-07-16",
        "place_id": 4,
        "place_name": "平和島",
        "slug": "heiwajima",
        "round": 9,
        "wind_speed": 2.0,
        "wave_height": 3.0,
        "manshu_rate_pct": 0.0,
    }


class HeiwajimaVenueRuleTests(unittest.TestCase):
    def setUp(self):
        self.original_odds_loader = MODULE.load_latest_trifecta_odds
        MODULE.load_latest_trifecta_odds = lambda _race: ({}, None, None)

    def tearDown(self):
        MODULE.load_latest_trifecta_odds = self.original_odds_loader

    def strategy(self, race=None, metrics=None, rows=None):
        strategies = MODULE.roi_strategies(
            race or sample_race(),
            metrics or sample_metrics(),
            rows or sample_rows(),
        )
        return next(
            (item for item in strategies if item["strategy_id"] == STRATEGY_ID),
            None,
        )

    def test_matching_race_uses_exact_validated_six_tickets(self):
        rows = sample_rows()
        strategy = self.strategy(rows=rows)
        self.assertIsNotNone(strategy)
        expected = {
            MODULE.fmt_ticket(ticket)
            for ticket in MODULE.original_boaters_forward.ticket_families(rows)[
                "non1_h2_no1"
            ][:6]
        }
        self.assertEqual(strategy["points"], 6)
        self.assertEqual(set(strategy["tickets"]), expected)
        self.assertTrue(all("1" not in ticket.replace("-", "") for ticket in strategy["tickets"]))
        self.assertEqual(strategy["keshi"], 1)

    def test_every_entry_boundary_is_enforced(self):
        cases = []

        race = sample_race()
        race["round"] = 8
        cases.append(("round", race, sample_metrics(), sample_rows()))

        metrics = sample_metrics()
        metrics["boat1_odds_prediction_pct"] = 54.9
        cases.append(("support", sample_race(), metrics, sample_rows()))

        metrics = sample_metrics()
        metrics["boat1_nige_pct"] = 65.1
        cases.append(("escape", sample_race(), metrics, sample_rows()))

        race = sample_race()
        race["wave_height"] = 2.0
        cases.append(("wave", race, sample_metrics(), sample_rows()))

        rows = sample_rows()
        for row in rows:
            if row["boat_number"] in {3, 4, 5, 6}:
                row["tenji_rank"] = 3
                row["tenji_time_rank"] = 3
                row["isshu_rank"] = 3
        cases.append(("outer_exhibition", sample_race(), sample_metrics(), rows))

        for label, case_race, case_metrics, case_rows in cases:
            with self.subTest(label=label):
                self.assertIsNone(self.strategy(case_race, case_metrics, case_rows))
                evaluation = MODULE.original_boaters_forward.evaluate(
                    case_race,
                    case_metrics,
                    case_rows,
                    ai_source=MODULE.original_boaters_forward.ORIGINAL_AI_SOURCE,
                )
                self.assertFalse(evaluation["matched"])

    def test_config_and_validated_strategy_registry_match(self):
        self.assertIn(STRATEGY_ID, MODULE.VALIDATED_BUY_STRATEGY_IDS)
        self.assertIn(STRATEGY_ID, MODULE.VENUE_SIGN_STRATEGY_IDS)
        config = json.loads(
            (ROOT / "data" / "config" / "original_boaters_24_rules_v1.json").read_text(
                encoding="utf-8"
            )
        )
        rule = next(item for item in config["rules"] if item["venue"] == "平和島")
        self.assertEqual(rule["template_id"], "non1_h2_no1_top6")
        self.assertEqual(rule["historical"]["roi_pct"], 332.13)

        evaluation = MODULE.original_boaters_forward.evaluate(
            sample_race(),
            sample_metrics(),
            sample_rows(),
            ai_source=MODULE.original_boaters_forward.ORIGINAL_AI_SOURCE,
        )
        self.assertTrue(evaluation["matched"])
        self.assertEqual(evaluation["context_id"], "round9_12_outertop2_wave3")
        self.assertEqual(evaluation["points"], 6)


if __name__ == "__main__":
    unittest.main()
