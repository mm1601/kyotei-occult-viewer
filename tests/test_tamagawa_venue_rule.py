import importlib.util
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
MODULE_PATH = SCRIPTS / "monitor_boaters_manshu_alerts.py"
SPEC = importlib.util.spec_from_file_location("monitor_boaters_manshu_alerts_tamagawa_test", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


STRATEGY_ID = "codex_tamagawa_r4_6_b1odds40_venue_debuff_h2_ai13_no1_has56_12"


def sample_rows():
    ai_win = [38, 25, 22, 18, 12, 8]
    ai_top3 = [28, 70, 64, 58, 52, 45]
    general_top3 = [30, 66, 60, 54, 48, 42]
    odds = [45, 20, 14, 9, 7, 5]
    tenji_ranks = [5, 3, 1, 2, 4, 6]
    lap_ranks = [5, 2, 1, 3, 4, 6]
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
            "avg_isshu_diff": [-0.12, 0.03, 0.17, 0.10, -0.02, -0.08][boat - 1],
            "venue_head_score_delta": [ -3.0, 1.0, 2.0, 0.5, 0.0, -0.5][boat - 1],
            "venue_top3_score_delta": [ -2.0, 1.0, 2.0, 0.5, 0.0, -0.5][boat - 1],
            "venue_manshu_score_delta": [ -1.0, 0.5, 1.0, 0.5, 0.0, 0.0][boat - 1],
            "venue_dont_keshi": boat == 3,
            "venue_b1_head_debuff": boat == 1,
            "venue_factor_reasons": ["多摩川1号艇の場別展示頭デバフ"] if boat == 1 else [],
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
        "boat1_odds_prediction_pct": 45.0,
        "boat1_odds_rank": 1,
        "boat1_nige_pct": 62.0,
        "boat1_tenji_rank": 5,
        "boat1_tenji_time_rank": 5,
        "boat1_isshu_rank": 5,
        "boat1_avg_isshu_diff": -0.12,
    }


def sample_race():
    return {
        "race_id": "2026-07-160504",
        "date": "2026-07-16",
        "place_id": 5,
        "place_name": "多摩川",
        "slug": "tamagawa",
        "round": 4,
        "wind_speed": 2.0,
        "wave_height": 2.0,
        "manshu_rate_pct": 0.0,
    }


class TamagawaVenueRuleTests(unittest.TestCase):
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

    def test_matching_race_uses_shared_variable_point_formation(self):
        rows = sample_rows()
        strategy = self.strategy(rows=rows)
        self.assertIsNotNone(strategy)
        expected = {
            MODULE.fmt_ticket(ticket)
            for ticket in MODULE.original_boaters_forward.tamagawa_h2_ai13_no1_has56(rows)
        }
        self.assertEqual(set(strategy["tickets"]), expected)
        self.assertGreaterEqual(strategy["points"], 2)
        self.assertLessEqual(strategy["points"], 12)
        for ticket in strategy["tickets"]:
            boats = {int(part) for part in ticket.split("-")}
            self.assertNotIn(1, boats)
            self.assertTrue(boats.intersection({5, 6}))

    def test_every_entry_boundary_is_enforced(self):
        cases = []
        for round_no in (3, 7):
            race = sample_race()
            race["round"] = round_no
            cases.append((f"round_{round_no}", race, sample_metrics(), sample_rows()))

        metrics = sample_metrics()
        metrics["boat1_odds_prediction_pct"] = 39.9
        cases.append(("support", sample_race(), metrics, sample_rows()))

        metrics = sample_metrics()
        metrics["boat1_odds_rank"] = 2
        cases.append(("popularity_rank", sample_race(), metrics, sample_rows()))

        rows = sample_rows()
        rows[0]["venue_b1_head_debuff"] = False
        cases.append(("venue_debuff", sample_race(), sample_metrics(), rows))

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
        rule = next(item for item in config["rules"] if item["venue"] == "多摩川")
        self.assertEqual(rule["context_id"], "round4_6_b1venue_debuff")
        self.assertEqual(rule["template_id"], "tamagawa_h2_ai13_no1_has56_all12")
        self.assertEqual(rule["historical"]["roi_pct"], 271.96)

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
            for ticket in MODULE.original_boaters_forward.tamagawa_h2_ai13_no1_has56(rows)
        }
        self.assertEqual(set(evaluation["tickets"]), expected)
        self.assertEqual(evaluation["points"], len(expected))


if __name__ == "__main__":
    unittest.main()
