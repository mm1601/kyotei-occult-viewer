import importlib.util
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
MODULE_PATH = SCRIPTS / "monitor_boaters_manshu_alerts.py"
SPEC = importlib.util.spec_from_file_location("monitor_boaters_manshu_alerts_hamanako_test", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


STRATEGY_ID = (
    "codex_hamanako_r1_3_wave2_revival_b1avg000_"
    "outer56avg005_outerh2_no1_has56_4"
)


def sample_rows():
    ai_win = [34, 18, 24, 21, 17, 12]
    ai_top3 = [55, 52, 70, 65, 61, 48]
    general_top3 = [58, 54, 68, 62, 60, 46]
    odds = [39, 18, 15, 12, 9, 7]
    tenji_ranks = [5, 4, 1, 2, 3, 6]
    lap_ranks = [5, 4, 1, 3, 2, 6]
    avg_diffs = [-0.05, -0.02, 0.12, 0.07, 0.08, 0.06]
    rows = []
    for boat in range(1, 7):
        revival = boat == 5
        rows.append(
            {
                "boat_number": boat,
                "ai_prediction_pct": ai_win[boat - 1],
                "ai_3ren_pct": ai_top3[boat - 1],
                "general_3ren_pct": general_top3[boat - 1],
                "ai_plus": ai_top3[boat - 1] + general_top3[boat - 1],
                "odds_prediction_pct": odds[boat - 1],
                "odds_prediction_pct_rank": boat,
                "tenji_rank": tenji_ranks[boat - 1],
                "tenji_time_rank": tenji_ranks[boat - 1],
                "isshu_rank": lap_ranks[boat - 1],
                "chokusen_rank": lap_ranks[boat - 1],
                "mawariashi_rank": lap_ranks[boat - 1],
                "avg_isshu_diff": avg_diffs[boat - 1],
                "venue_low_ai_revival": revival,
                "venue_low_ai_revival_profile": (
                    {
                        "ai_plus_rank": 5,
                        "role": "second_third",
                        "role_label": "2・3着復活",
                        "top3_rate_pp": 16.0,
                        "win_rate_pp": 4.0,
                        "confidence": "A",
                        "reason": "浜名湖5号艇の場別展示復活バフ",
                    }
                    if revival
                    else {}
                ),
                "venue_low_ai_revival_reasons": (
                    ["浜名湖5号艇の場別展示復活バフ"] if revival else []
                ),
                "st_rank_general": boat,
                "super_slit_alert": False,
                "double_time": boat == 3,
            }
        )
    return rows


def sample_metrics():
    return {
        "boaters_exhibition_mode": "full",
        "tenji_boats": 6,
        "isshu_boats": 6,
        "raw_isshu_boats": 6,
        "boat1_odds_prediction_pct": 39.0,
        "boat1_odds_rank": 1,
        "boat1_nige_pct": 61.0,
        "boat1_tenji_rank": 5,
        "boat1_tenji_time_rank": 5,
        "boat1_isshu_rank": 5,
        "boat1_avg_isshu_diff": -0.05,
        "outer56_best_avg_isshu_diff": 0.08,
    }


def sample_race():
    return {
        "race_id": "2026-07-160602",
        "date": "2026-07-16",
        "place_id": 6,
        "place_name": "浜名湖",
        "slug": "hamanako",
        "round": 2,
        "wind_speed": 2.0,
        "wave_height": 2.0,
        "manshu_rate_pct": 0.0,
    }


class HamanakoVenueRuleTests(unittest.TestCase):
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
        return next((item for item in strategies if item["strategy_id"] == STRATEGY_ID), None)

    def test_matching_race_uses_shared_four_point_formation(self):
        rows = sample_rows()
        strategy = self.strategy(rows=rows)
        self.assertIsNotNone(strategy)
        expected = {
            MODULE.fmt_ticket(ticket)
            for ticket in MODULE.original_boaters_forward.ticket_families(rows)[
                "outer_h2_no1_has56"
            ][:4]
        }
        self.assertEqual(set(strategy["tickets"]), expected)
        self.assertEqual(strategy["points"], 4)
        for ticket in strategy["tickets"]:
            boats = {int(part) for part in ticket.split("-")}
            self.assertNotIn(1, boats)
            self.assertTrue(boats.intersection({5, 6}))

    def test_every_entry_boundary_is_enforced(self):
        cases = []

        race = sample_race()
        race["round"] = 4
        cases.append(("round", race, sample_metrics(), sample_rows()))

        race = sample_race()
        race["wave_height"] = 1.9
        cases.append(("wave", race, sample_metrics(), sample_rows()))

        rows = sample_rows()
        for row in rows:
            row["venue_low_ai_revival"] = False
            row["venue_low_ai_revival_profile"] = {}
        cases.append(("revival", sample_race(), sample_metrics(), rows))

        metrics = sample_metrics()
        metrics["boat1_avg_isshu_diff"] = 0.01
        cases.append(("boat1_avg_diff", sample_race(), metrics, sample_rows()))

        metrics = sample_metrics()
        metrics["outer56_best_avg_isshu_diff"] = 0.049
        cases.append(("outer56_avg_diff", sample_race(), metrics, sample_rows()))

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

    def test_config_registry_and_original_evaluator_match(self):
        self.assertIn(STRATEGY_ID, MODULE.VALIDATED_BUY_STRATEGY_IDS)
        self.assertIn(STRATEGY_ID, MODULE.VENUE_SIGN_STRATEGY_IDS)
        config = json.loads(
            (ROOT / "data" / "config" / "original_boaters_24_rules_v1.json").read_text(
                encoding="utf-8"
            )
        )
        rule = next(item for item in config["rules"] if item["venue"] == "浜名湖")
        self.assertEqual(rule["base_id"], "all")
        self.assertEqual(
            rule["context_id"],
            "round1_3_wave2_revival_b1avg000_outer56avg005",
        )
        self.assertEqual(rule["template_id"], "outer_h2_no1_has56_top4")
        self.assertEqual(rule["historical"]["roi_pct"], 418.78)

        rows = sample_rows()
        evaluation = MODULE.original_boaters_forward.evaluate(
            sample_race(),
            sample_metrics(),
            rows,
            ai_source=MODULE.original_boaters_forward.ORIGINAL_AI_SOURCE,
        )
        self.assertTrue(evaluation["matched"])
        expected = {
            MODULE.original_boaters_forward.format_ticket(ticket)
            for ticket in MODULE.original_boaters_forward.ticket_families(rows)[
                "outer_h2_no1_has56"
            ][:4]
        }
        self.assertEqual(set(evaluation["tickets"]), expected)
        self.assertEqual(evaluation["points"], 4)


if __name__ == "__main__":
    unittest.main()
