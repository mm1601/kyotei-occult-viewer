#!/usr/bin/env python3
"""Pre-race-only shadow evaluation for new venue buff/debuff candidates."""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CANDIDATES_PATH = ROOT / "data" / "output" / "venue_new_buff_debuff_candidates.json"
SHADOW_VERSION = "venue-buff-debuff-s-head-skip-shadow-v1"
POLICY_ID = "skip_s_head_debuff_v1"


def as_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def factor_id(factor: dict[str, Any]) -> str:
    return "|".join(
        (
            str(factor.get("venue") or ""),
            str(int(factor.get("lane") or 0)),
            str(factor.get("metric_id") or ""),
            str(factor.get("condition_id") or ""),
        )
    )


@lru_cache(maxsize=4)
def load_candidates(path_text: str = str(DEFAULT_CANDIDATES_PATH)) -> dict[str, Any]:
    path = Path(path_text)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def candidate_index(path: Path | str = DEFAULT_CANDIDATES_PATH) -> dict[tuple[str, int], list[dict[str, Any]]]:
    payload = load_candidates(str(Path(path).resolve()))
    index: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for factor in payload.get("factors") or []:
        venue = str(factor.get("venue") or "")
        lane = int(as_float(factor.get("lane")) or 0)
        if venue and lane:
            index.setdefault((venue, lane), []).append(factor)
    return index


def feature_value(
    row: dict[str, Any],
    race: dict[str, Any],
    metric_id: str,
    avg_weight: float | None,
) -> float | None:
    direct = {
        "st_time_avg_general": row.get("st_time_avg_general"),
        "st_rank_general": row.get("st_rank_general"),
        "weight": row.get("weight"),
        "general_3ren_count": row.get("general_3ren_count"),
        "nige_pct_year": row.get("nige_pct_year", row.get("nige_pct")),
        "sasare_pct_year": row.get("sasare_pct_year", row.get("sasare_pct")),
        "makurare_pct_year": row.get("makurare_pct_year", row.get("makurare_pct")),
        "sashi_pct_year": row.get("sashi_pct_year", row.get("sashi_pct")),
        "makuri_pct_year": row.get("makuri_pct_year", row.get("makuri_pct")),
        "makurizashi_pct_year": row.get("makurizashi_pct_year", row.get("makurizashi_pct")),
        "makurizasare_pct_year": row.get("makurizasare_pct_year", row.get("makurizasare_pct")),
        "nigashi_pct_year": row.get("nigashi_pct_year", row.get("nigashi_pct")),
    }
    if metric_id in direct:
        return as_float(direct[metric_id])
    if metric_id == "start_form_gap":
        historical = as_float(row.get("st_rank_general"))
        current = as_float(row.get("start_tenji_rank"))
        return historical - current if historical is not None and current is not None else None
    if metric_id == "ai_market_gap":
        ai = as_float(row.get("ai_prediction_pct"))
        market = as_float(row.get("odds_prediction_pct"))
        return ai - market if ai is not None and market is not None else None
    if metric_id == "ai_general_top3_gap":
        ai = as_float(row.get("ai_3ren_pct"))
        general = as_float(row.get("general_3ren_pct"))
        return ai - general if ai is not None and general is not None else None
    if metric_id == "weight_advantage":
        weight = as_float(row.get("weight"))
        return avg_weight - weight if avg_weight is not None and weight is not None else None
    return as_float(race.get(metric_id))


def fixed_condition_matches(metric_id: str, row: dict[str, Any], race: dict[str, Any]) -> bool:
    boat = int(row.get("boat_number") or 0)
    course = as_float(row.get("before_start_sinnyu"))
    tilt = as_float(row.get("tilt"))
    adjustment = as_float(row.get("weight_adjust"))
    round_no = int(race.get("round") or 0)
    date_text = str(race.get("date") or "")
    month = int(date_text[5:7]) if len(date_text) >= 7 and date_text[5:7].isdigit() else 0
    wind = as_float(race.get("wind_speed"))
    wave = as_float(race.get("wave_height"))
    checks = {
        "entry_inward": course is not None and course < boat,
        "entry_outward": course is not None and course > boat,
        "tilt_positive": tilt is not None and tilt >= 0.5,
        "tilt_zero": tilt == 0.0,
        "weight_adjusted": adjustment is not None and adjustment > 0,
        "weight_adjust_1plus": adjustment is not None and adjustment >= 1.0,
        "fixed_entry_race": race.get("sinnyu_method") == "fix",
        "early_round": round_no <= 4,
        "late_round": round_no >= 9,
        "summer": month in {6, 7, 8},
        "winter": month in {12, 1, 2},
        "strong_wind_4plus": wind is not None and wind >= 4.0,
        "calm_wind_1orless": wind is not None and wind <= 1.0,
        "high_wave_4plus": wave is not None and wave >= 4.0,
        "low_wave_1orless": wave is not None and wave <= 1.0,
    }
    return bool(checks.get(metric_id, False))


def factor_matches(
    factor: dict[str, Any],
    row: dict[str, Any],
    race: dict[str, Any],
    avg_weight: float | None,
) -> bool:
    metric_id = str(factor.get("metric_id") or "")
    if factor.get("threshold_operator") == "fixed":
        return fixed_condition_matches(metric_id, row, race)
    value = feature_value(row, race, metric_id, avg_weight)
    threshold = as_float(factor.get("threshold_value"))
    if value is None or threshold is None:
        return False
    operator = factor.get("threshold_operator")
    return value <= threshold if operator == "<=" else value >= threshold if operator == ">=" else False


def build_match_map(
    rows: list[dict[str, Any]],
    race: dict[str, Any],
    index: dict[tuple[str, int], list[dict[str, Any]]] | None = None,
) -> dict[int, list[dict[str, Any]]]:
    factor_index = index if index is not None else candidate_index()
    weights = [as_float(row.get("weight")) for row in rows]
    valid_weights = [value for value in weights if value is not None]
    avg_weight = sum(valid_weights) / len(valid_weights) if valid_weights else None
    venue = str(race.get("place_name") or "")
    out: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        boat = int(row.get("boat_number") or 0)
        out[boat] = [
            factor
            for factor in factor_index.get((venue, boat), [])
            if factor_matches(factor, row, race, avg_weight)
        ]
    return out


def evaluate_skip_s_head_debuff(
    race: dict[str, Any],
    rows: list[dict[str, Any]],
    tickets: list[str],
    *,
    candidates_path: Path | str = DEFAULT_CANDIDATES_PATH,
) -> dict[str, Any]:
    payload = load_candidates(str(Path(candidates_path).resolve()))
    index = candidate_index(candidates_path)
    base = {
        "version": SHADOW_VERSION,
        "policy_id": POLICY_ID,
        "active": False,
        "notification_enabled": False,
        "status": "not_triggered",
        "candidate_source": str(Path(candidates_path)),
        "candidate_version": payload.get("version"),
        "would_skip": False,
    }
    if not index:
        base.update({"status": "unavailable", "reason": "candidate_dictionary_missing_or_empty"})
        return base
    normalized = ["".join(ch for ch in str(ticket) if ch.isdigit())[:3] for ticket in tickets]
    heads = sorted({int(ticket[0]) for ticket in normalized if len(ticket) == 3})
    matches = build_match_map(rows, race, index)
    head_debuffs = []
    for boat in heads:
        for factor in matches.get(boat, []):
            if factor.get("confidence") != "S":
                continue
            if "head_debuff" not in set(factor.get("effect_targets") or []):
                continue
            head_debuffs.append(
                {
                    "factor_id": factor_id(factor),
                    "boat_number": boat,
                    "metric_label": factor.get("metric_label"),
                    "condition_id": factor.get("condition_id"),
                    "threshold_operator": factor.get("threshold_operator"),
                    "threshold_value": factor.get("threshold_value"),
                    "confidence": factor.get("confidence"),
                    "train_win_ai_adjusted_pp": (factor.get("train") or {}).get(
                        "win_residual_contrast_pp"
                    ),
                    "holdout_win_ai_adjusted_pp": (factor.get("holdout") or {}).get(
                        "win_residual_contrast_pp"
                    ),
                }
            )
    base.update(
        {
            "heads": heads,
            "matched_factor_count": sum(len(items) for items in matches.values()),
            "head_debuffs": head_debuffs,
            "would_skip": bool(head_debuffs),
            "status": "pending_result" if head_debuffs else "not_triggered",
            "reason": "s_confidence_head_debuff" if head_debuffs else "no_s_head_debuff_on_current_head",
        }
    )
    return base
