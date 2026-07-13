#!/usr/bin/env python3
"""Frozen 24-venue BOATERS-original rules for forward-only shadow logging.

The formulas in this module intentionally mirror
``search_all_venues_stable_rules.py``.  It has no result or payout inputs, so
the condition and ticket order can be evaluated before a race starts.
"""

from __future__ import annotations

import itertools
import json
import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import venue_buff_debuff_shadow as venue_factor_shadow


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RULES_PATH = ROOT / "data" / "config" / "original_boaters_24_rules_v1.json"
RULE_SET_ID = "original_boaters_24_v1"
TENJI_FALLBACK_VENUES = {"江戸川", "津"}
VENUE_ALIASES = {"琵琶湖": "びわこ", "からつ": "唐津"}
ORIGINAL_AI_SOURCE = "original_boaters"
HEAD_SWAP_SHADOW_ID = "aiwin_delta4_scoregap5_v1"
HEAD_SWAP_AI_WIN_DELTA_MIN = 4.0
HEAD_SWAP_CURRENT_SCORE_GAP_MAX = 5.0
HEAD56_CONFIDENCE_SHADOW_ID = "head56_unselected_ai_not_lower_v1"
TICKET_EV_SHADOW_ID = "aiwin_top3_sequential_v1"
TICKET_POSITION_SHADOW_ID = "position123_hgb_calibrated_v1"
TICKET_VENUE_PROBABILITY_SHADOW_ID = "venue_lane_probability_point_overlay_v1"
LOW_CONFIDENCE_SHADOW_ID = "three_gate_low_confidence_v1"


def number(value: Any, default: float | None = None) -> float | None:
    if value is None or value == "":
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def valid_rank(value: Any) -> int | None:
    parsed = number(value)
    return int(parsed) if parsed is not None and 1 <= parsed <= 6 else None


def normalize_venue(value: Any) -> str:
    venue = str(value or "")
    return VENUE_ALIASES.get(venue, venue)


@lru_cache(maxsize=4)
def _load_rules_cached(path_text: str) -> dict[str, Any]:
    path = Path(path_text)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("rule_set_id") != RULE_SET_ID:
        raise ValueError(f"unexpected rule set: {payload.get('rule_set_id')}")
    rules = payload.get("rules") or []
    venues = [normalize_venue(rule.get("venue")) for rule in rules]
    if len(rules) != 24 or len(set(venues)) != 24:
        raise ValueError(f"frozen rule set must contain 24 unique venues: {len(rules)}")
    return payload


def load_rules(path: Path | str = DEFAULT_RULES_PATH) -> dict[str, Any]:
    return _load_rules_cached(str(Path(path).resolve()))


def rules_by_venue(path: Path | str = DEFAULT_RULES_PATH) -> dict[str, dict[str, Any]]:
    return {
        normalize_venue(rule.get("venue")): rule
        for rule in load_rules(path).get("rules") or []
    }


def row_for(rows: list[dict[str, Any]], boat: int) -> dict[str, Any]:
    return next((row for row in rows if int(number(row.get("boat_number"), 0) or 0) == boat), {})


def pre_race_win_score(row: dict[str, Any]) -> float:
    ai_win = number(row.get("ai_prediction_pct"), 0.0) or 0.0
    ai_plus = number(row.get("ai_plus"), 0.0) or 0.0
    tenji = valid_rank(row.get("tenji_rank") or row.get("exhibit_rank") or row.get("tenji_time_rank")) or 7
    lap = valid_rank(row.get("isshu_rank")) or 7
    choku = valid_rank(row.get("chokusen_rank")) or 7
    avg_diff = number(row.get("avg_isshu_diff"), 0.0) or 0.0
    return (
        ai_win
        + ai_plus * 0.05
        + (7 - tenji) * 0.8
        + (7 - lap) * 0.7
        + (7 - choku) * 0.2
        + max(-0.35, min(0.35, avg_diff)) * 8.0
        + (2.0 if row.get("super_slit_alert") else 0.0)
        + (1.0 if row.get("double_time") else 0.0)
    )


def pre_race_top3_score(row: dict[str, Any]) -> float:
    ai_top3 = number(row.get("ai_3ren_pct"), 0.0) or 0.0
    general = number(row.get("general_3ren_pct"), 0.0) or 0.0
    tenji = valid_rank(row.get("tenji_rank") or row.get("exhibit_rank") or row.get("tenji_time_rank")) or 7
    lap = valid_rank(row.get("isshu_rank")) or 7
    mawari = valid_rank(row.get("mawariashi_rank")) or 7
    avg_diff = number(row.get("avg_isshu_diff"), 0.0) or 0.0
    base = ai_top3 * 0.72 + general * 0.28 if general else ai_top3
    return (
        base
        + (7 - tenji) * 1.2
        + (7 - lap) * 1.0
        + (7 - mawari) * 0.4
        + max(-0.35, min(0.35, avg_diff)) * 12.0
        + (2.0 if row.get("super_slit_alert") else 0.0)
        + (1.2 if row.get("double_time") else 0.0)
    )


def ranked_formation(
    rows: list[dict[str, Any]],
    heads: list[int],
    *,
    allow_b1_place: bool,
    require_56: bool = False,
    limit: int = 12,
) -> list[str]:
    by_boat = {int(row["boat_number"]): row for row in rows}
    scored: list[tuple[float, str]] = []
    for head in heads:
        if head == 1 or head not in by_boat:
            continue
        for second in range(1, 7):
            for third in range(1, 7):
                combo = (head, second, third)
                if len(set(combo)) != 3:
                    continue
                if not allow_b1_place and 1 in combo:
                    continue
                if require_56 and not set(combo) & {5, 6}:
                    continue
                if second not in by_boat or third not in by_boat:
                    continue
                head_row = by_boat[head]
                second_row = by_boat[second]
                third_row = by_boat[third]
                ai_product = (
                    (number(head_row.get("ai_prediction_pct"), 0.0) or 0.0)
                    * (number(second_row.get("ai_3ren_pct"), 0.0) or 0.0)
                    * (number(third_row.get("ai_3ren_pct"), 0.0) or 0.0)
                )
                score = pre_race_win_score(head_row) * 4.0
                score += pre_race_top3_score(second_row) * 1.8
                score += pre_race_top3_score(third_row) * 1.5
                score += ai_product * 0.0008
                scored.append((score, "".join(map(str, combo))))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [combo for _score, combo in scored[:limit]]


def box3(rows: list[dict[str, Any]], boats: set[int]) -> list[str]:
    pool = [row for row in rows if int(number(row.get("boat_number"), 0) or 0) in boats]
    pool.sort(
        key=lambda row: (
            -(pre_race_win_score(row) * 0.45 + pre_race_top3_score(row) * 0.55),
            row["boat_number"],
        )
    )
    selected = [int(row["boat_number"]) for row in pool[:3]]
    if len(selected) != 3:
        return []
    return ["".join(map(str, combo)) for combo in itertools.permutations(selected, 3)]


def eligible_head_pool(family: str) -> set[int]:
    if family.startswith("head56_"):
        return {5, 6}
    if family.startswith("outer_") or family == "outer_box3":
        return {3, 4, 5, 6}
    return {2, 3, 4, 5, 6}


def intended_head_count(family: str) -> int:
    if family.endswith("box3"):
        return 3
    if "_h2_" in family:
        return 2
    if "_h1_" in family:
        return 1
    raise KeyError(f"unsupported head family: {family}")


def intended_heads(rows: list[dict[str, Any]], family: str) -> list[int]:
    pool = eligible_head_pool(family)
    candidates = [row for row in rows if int(number(row.get("boat_number"), 0) or 0) in pool]
    if family.endswith("box3"):
        candidates.sort(
            key=lambda row: (
                -(pre_race_win_score(row) * 0.45 + pre_race_top3_score(row) * 0.55),
                int(row["boat_number"]),
            )
        )
    else:
        candidates.sort(
            key=lambda row: (-pre_race_win_score(row), int(row["boat_number"])),
        )
    return [int(row["boat_number"]) for row in candidates[: intended_head_count(family)]]


def _signal_leaders(
    by_boat: dict[int, dict[str, Any]],
    boats: set[int],
    getter,
    *,
    higher_is_better: bool = True,
) -> list[int]:
    values = []
    for boat in sorted(boats):
        if boat not in by_boat:
            continue
        value = number(getter(by_boat[boat]))
        if value is not None:
            values.append((boat, float(value)))
    if not values:
        return []
    best = (max if higher_is_better else min)(value for _boat, value in values)
    return [boat for boat, value in values if math.isclose(value, best, abs_tol=1e-9)]


def low_confidence_diagnostics(
    rows: list[dict[str, Any]],
    family: str,
    baseline_tickets: list[str],
) -> dict[str, Any]:
    """Describe pre-race conflicts without consulting a result or payout."""
    by_boat = {int(row["boat_number"]): row for row in rows}
    ticket_heads = set()
    for ticket in baseline_tickets:
        combo = re.sub(r"\D", "", str(ticket))
        if combo:
            ticket_heads.add(int(combo[0]))
    eligible = eligible_head_pool(family) & set(by_boat)
    selected = eligible & ticket_heads
    unselected = eligible - selected
    if not selected or not unselected:
        return {
            "available": False,
            "reason": "no_comparable_selected_and_unselected_heads",
            "ticket_heads": sorted(ticket_heads),
            "eligible_heads": sorted(eligible),
        }

    signals = {
        "ai_win": _signal_leaders(
            by_boat, eligible, lambda row: row.get("ai_prediction_pct")
        ),
        "ai_top3": _signal_leaders(
            by_boat, eligible, lambda row: row.get("ai_3ren_pct")
        ),
        "market_support": _signal_leaders(
            by_boat, eligible, lambda row: row.get("odds_prediction_pct")
        ),
        "win_score": _signal_leaders(
            by_boat, eligible, pre_race_win_score
        ),
        "top3_score": _signal_leaders(
            by_boat, eligible, pre_race_top3_score
        ),
        "exhibition": _signal_leaders(
            by_boat,
            eligible,
            lambda row: (
                (7 - (valid_rank(row.get("tenji_rank") or row.get("exhibit_rank")) or 7))
                + (7 - (valid_rank(row.get("isshu_rank")) or 7))
                + (number(row.get("avg_isshu_diff"), 0.0) or 0.0) * 5.0
            ),
        ),
        "lap": _signal_leaders(
            by_boat,
            eligible,
            lambda row: valid_rank(row.get("isshu_rank")),
            higher_is_better=False,
        ),
        "avg_diff": _signal_leaders(
            by_boat, eligible, lambda row: row.get("avg_isshu_diff")
        ),
    }
    slit_leaders = sorted(
        boat for boat in eligible if bool(by_boat[boat].get("super_slit_alert"))
    )
    if slit_leaders:
        signals["super_slit"] = slit_leaders

    support = {boat: 0 for boat in eligible}
    for leaders in signals.values():
        for boat in leaders:
            support[boat] += 1
    selected_best_support = max(support[boat] for boat in selected)
    unselected_best_support = max(support[boat] for boat in unselected)
    support_gap = unselected_best_support - selected_best_support

    def descending_rank(boat: int, getter) -> int | None:
        values = {
            candidate: number(getter(row)) for candidate, row in by_boat.items()
        }
        target = values.get(boat)
        if target is None:
            return None
        return 1 + sum(
            value is not None and float(value) > float(target)
            for candidate, value in values.items()
            if candidate != boat
        )

    consensus_support = {}
    for boat, row in by_boat.items():
        ai_win_rank = descending_rank(boat, lambda item: item.get("ai_prediction_pct"))
        ai_top3_rank = descending_rank(boat, lambda item: item.get("ai_3ren_pct"))
        win_score_rank = descending_rank(boat, pre_race_win_score)
        top3_score_rank = descending_rank(boat, pre_race_top3_score)
        avg_diff_rank = descending_rank(boat, lambda item: item.get("avg_isshu_diff"))
        tenji_rank = valid_rank(
            row.get("tenji_rank") or row.get("exhibit_rank") or row.get("tenji_time_rank")
        )
        lap_rank = valid_rank(row.get("isshu_rank"))
        consensus_support[boat] = sum(
            (
                ai_win_rank is not None and ai_win_rank <= 1,
                ai_top3_rank is not None and ai_top3_rank <= 2,
                win_score_rank is not None and win_score_rank <= 1,
                top3_score_rank is not None and top3_score_rank <= 2,
                tenji_rank is not None and tenji_rank <= 2,
                lap_rank is not None and lap_rank <= 2,
                avg_diff_rank is not None and avg_diff_rank <= 2,
                bool(row.get("super_slit_alert")),
            )
        )
    selected_best_consensus = max(consensus_support[boat] for boat in selected)
    unselected_best_consensus = max(consensus_support[boat] for boat in unselected)
    consensus_gap = unselected_best_consensus - selected_best_consensus

    ai_leaders = set(signals.get("ai_win") or [])
    exhibition_leaders = set(signals.get("exhibition") or [])
    ai_selected = bool(ai_leaders & selected)
    ai_unselected = bool(ai_leaders & unselected)
    exhibition_selected = bool(exhibition_leaders & selected)
    exhibition_unselected = bool(exhibition_leaders & unselected)
    ai_exhibition_disagree = bool(
        (ai_selected and exhibition_unselected and not exhibition_selected)
        or (ai_unselected and exhibition_selected and not ai_selected)
    )
    selected_slit = bool(set(slit_leaders) & selected)
    unselected_slit = bool(set(slit_leaders) & unselected)
    unselected_only_slit = unselected_slit and not selected_slit

    selected_ai = max(
        float(number(by_boat[boat].get("ai_prediction_pct"), 0.0) or 0.0)
        for boat in selected
    )
    unselected_ai = max(
        float(number(by_boat[boat].get("ai_prediction_pct"), 0.0) or 0.0)
        for boat in unselected
    )
    risk_flags = {
        "unselected_support_gap_ge2": support_gap >= 2,
        "ai_leader_unselected": ai_unselected and not ai_selected,
        "exhibition_leader_unselected": exhibition_unselected and not exhibition_selected,
        "ai_exhibition_selection_disagree": ai_exhibition_disagree,
        "unselected_only_super_slit": unselected_only_slit,
    }
    multi_source_disagreement = bool(
        ai_exhibition_disagree and (unselected_only_slit or support_gap >= 1)
    )
    return {
        "available": True,
        "family": family,
        "ticket_heads": sorted(ticket_heads),
        "eligible_heads": sorted(eligible),
        "selected_heads": sorted(selected),
        "unselected_heads": sorted(unselected),
        "leaders_by_signal": signals,
        "support_by_head": {str(boat): support[boat] for boat in sorted(support)},
        "selected_best_support": selected_best_support,
        "unselected_best_support": unselected_best_support,
        "unselected_minus_selected_support": support_gap,
        "consensus_support_by_head": {
            str(boat): consensus_support[boat] for boat in sorted(eligible)
        },
        "selected_best_consensus_support": selected_best_consensus,
        "unselected_best_consensus_support": unselected_best_consensus,
        "unselected_minus_selected_consensus_support": consensus_gap,
        "selected_best_ai_win_pct": round(selected_ai, 4),
        "unselected_best_ai_win_pct": round(unselected_ai, 4),
        "unselected_minus_selected_ai_win_pp": round(unselected_ai - selected_ai, 4),
        "ai_exhibition_selection_disagree": ai_exhibition_disagree,
        "selected_has_super_slit": selected_slit,
        "unselected_has_super_slit": unselected_slit,
        "unselected_only_super_slit": unselected_only_slit,
        "risk_flags": risk_flags,
        "structural_risk_score": sum(bool(value) for value in risk_flags.values()),
        "multi_source_disagreement": multi_source_disagreement,
    }


def tickets_for_heads(
    rows: list[dict[str, Any]],
    family: str,
    heads: list[int],
    points: int,
) -> list[str]:
    if family.endswith("box3"):
        return ["".join(map(str, combo)) for combo in itertools.permutations(heads, 3)][:points]
    return ranked_formation(
        rows,
        heads,
        allow_b1_place="b1place" in family,
        require_56="has56" in family,
        limit=120,
    )[:points]


def evaluate_head_swap_shadow(
    rows: list[dict[str, Any]],
    family: str,
    points: int,
    baseline_tickets: list[str],
) -> dict[str, Any]:
    current_heads = intended_heads(rows, family)
    by_boat = {int(row["boat_number"]): row for row in rows}
    excluded = sorted(eligible_head_pool(family) - set(current_heads))
    base = {
        "version": "original-boaters-24-head-swap-shadow-v1",
        "policy_id": HEAD_SWAP_SHADOW_ID,
        "notification_enabled": False,
        "active": False,
        "status": "not_triggered",
        "ai_win_delta_min_pp": HEAD_SWAP_AI_WIN_DELTA_MIN,
        "max_current_score_gap": HEAD_SWAP_CURRENT_SCORE_GAP_MAX,
        "baseline_intended_heads": current_heads,
        "baseline_ticket_heads": sorted({int(ticket[0]) for ticket in baseline_tickets}),
        "baseline_tickets": [format_ticket(ticket) for ticket in baseline_tickets],
        "shadow_heads": current_heads,
        "shadow_tickets": [format_ticket(ticket) for ticket in baseline_tickets],
        "would_change_tickets": False,
    }
    if not current_heads or not excluded:
        base["reason"] = "no_replaceable_head"
        return base
    weakest = min(
        current_heads,
        key=lambda boat: (pre_race_win_score(by_boat[boat]), -boat),
    )
    valid_alternates = [
        boat for boat in excluded if number(by_boat[boat].get("ai_prediction_pct")) is not None
    ]
    weak_ai_win = number(by_boat[weakest].get("ai_prediction_pct"))
    if weak_ai_win is None or not valid_alternates:
        base["reason"] = "ai_win_missing"
        return base
    alternate = max(
        valid_alternates,
        key=lambda boat: (
            number(by_boat[boat].get("ai_prediction_pct"), -999.0),
            pre_race_win_score(by_boat[boat]),
            -boat,
        ),
    )
    alternate_ai_win = number(by_boat[alternate].get("ai_prediction_pct"))
    weak_score = pre_race_win_score(by_boat[weakest])
    alternate_score = pre_race_win_score(by_boat[alternate])
    ai_win_delta = float(alternate_ai_win) - float(weak_ai_win)
    current_score_gap = weak_score - alternate_score
    base.update(
        {
            "promoted_head": alternate,
            "demoted_head": weakest,
            "promoted_ai_win_pct": round(float(alternate_ai_win), 4),
            "demoted_ai_win_pct": round(float(weak_ai_win), 4),
            "ai_win_delta_pp": round(ai_win_delta, 4),
            "promoted_current_score": round(alternate_score, 4),
            "demoted_current_score": round(weak_score, 4),
            "current_score_gap": round(current_score_gap, 4),
        }
    )
    if ai_win_delta < HEAD_SWAP_AI_WIN_DELTA_MIN:
        base["reason"] = "ai_win_delta_below_threshold"
        return base
    if current_score_gap > HEAD_SWAP_CURRENT_SCORE_GAP_MAX:
        base["reason"] = "current_score_gap_above_threshold"
        return base
    shadow_heads = [boat for boat in current_heads if boat != weakest] + [alternate]
    shadow_tickets = tickets_for_heads(rows, family, shadow_heads, points)
    base.update(
        {
            "active": True,
            "status": "pending_result",
            "reason": "thresholds_matched",
            "shadow_heads": shadow_heads,
            "shadow_tickets": [format_ticket(ticket) for ticket in shadow_tickets],
            "would_change_tickets": shadow_tickets != baseline_tickets,
        }
    )
    return base


def evaluate_head56_confidence_shadow(
    rows: list[dict[str, Any]],
    family: str,
    baseline_tickets: list[str],
) -> dict[str, Any]:
    """Log a skip counterfactual when the other 5/6 head has no lower AI win rate."""
    ticket_heads = sorted({int(ticket[0]) for ticket in baseline_tickets})
    eligible_heads = {5, 6}
    selected = sorted(eligible_heads & set(ticket_heads))
    unselected = sorted(eligible_heads - set(ticket_heads))
    base = {
        "version": "original-boaters-24-head56-confidence-shadow-v1",
        "policy_id": HEAD56_CONFIDENCE_SHADOW_ID,
        "notification_enabled": False,
        "active": False,
        "status": "not_triggered",
        "would_skip": False,
        "family": family,
        "baseline_ticket_heads": ticket_heads,
        "selected_56_heads": selected,
        "unselected_56_heads": unselected,
        "baseline_tickets": [format_ticket(ticket) for ticket in baseline_tickets],
    }
    if not family.startswith("head56"):
        base["reason"] = "not_head56_family"
        return base
    if not selected or not unselected:
        base["reason"] = "no_comparable_unselected_56_head"
        return base
    by_boat = {int(row["boat_number"]): row for row in rows}
    selected_values = [
        number(by_boat.get(boat, {}).get("ai_prediction_pct")) for boat in selected
    ]
    unselected_values = [
        number(by_boat.get(boat, {}).get("ai_prediction_pct")) for boat in unselected
    ]
    if any(value is None for value in selected_values + unselected_values):
        base["reason"] = "ai_win_missing"
        return base
    selected_best = max(float(value) for value in selected_values if value is not None)
    unselected_best = max(float(value) for value in unselected_values if value is not None)
    gap = unselected_best - selected_best
    base.update(
        {
            "selected_best_ai_win_pct": round(selected_best, 4),
            "unselected_best_ai_win_pct": round(unselected_best, 4),
            "unselected_minus_selected_ai_win_pp": round(gap, 4),
        }
    )
    if gap < 0:
        base["reason"] = "selected_head_ai_win_higher"
        return base
    base.update(
        {
            "active": True,
            "status": "pending_result",
            "would_skip": True,
            "reason": "unselected_56_ai_win_not_lower",
        }
    )
    return base


def evaluate_low_confidence_shadow(
    rows: list[dict[str, Any]],
    family: str,
    baseline_tickets: list[str],
) -> dict[str, Any]:
    """Freeze explainable skip candidates for forward-only comparison."""
    diagnostics = low_confidence_diagnostics(rows, family, baseline_tickets)
    unavailable_decision = {
        "decision_available": False,
        "would_skip": None,
        "production_eligible": False,
    }
    if not diagnostics.get("available"):
        return {
            "version": "original-boaters-24-low-confidence-shadow-v1",
            "policy_id": LOW_CONFIDENCE_SHADOW_ID,
            "active": False,
            "notification_enabled": False,
            "status": "unavailable",
            "reason": diagnostics.get("reason"),
            "diagnostics": diagnostics,
            "candidate_decisions": {},
        }

    support_gap = int(diagnostics["unselected_minus_selected_support"])
    consensus_gap = int(
        diagnostics["unselected_minus_selected_consensus_support"]
    )
    structural = consensus_gap >= 2
    disagreement = bool(diagnostics["multi_source_disagreement"])
    head56_conflict = bool(
        family.startswith("head56")
        and diagnostics["unselected_minus_selected_ai_win_pp"] >= 0
    )
    candidates = {
        "structural_support_gap2": {
            "decision_available": True,
            "would_skip": structural,
            "production_eligible": False,
            "reason": "未選択頭の歴史検証用強材料が選択頭より2個以上多い",
            "historical_status": "2026_holdout_failed",
        },
        "multi_source_disagreement": {
            "decision_available": True,
            "would_skip": disagreement,
            "production_eligible": False,
            "reason": "AIと展示の支持先が食い違い、未選択側にも別材料がある",
            "historical_status": "new_forward_candidate",
        },
        "head56_unselected_ai_not_lower": {
            "decision_available": True,
            "would_skip": head56_conflict,
            "production_eligible": False,
            "reason": "5・6号艇頭型で未選択側のAI1着率が同等以上",
            "historical_status": "existing_shadow_candidate",
        },
        "both_models_ev_below100_t5": {
            **unavailable_decision,
            "reason": "T-5で旧・着順モデル双方の期待回収率が100%未満",
        },
        "both_models_ev_below80_t5": {
            **unavailable_decision,
            "reason": "T-5で旧・着順モデル双方の期待回収率が80%未満",
        },
        "legacy_ev_drop20_t10_to_t5": {
            **unavailable_decision,
            "reason": "旧モデルの期待回収率がT-10からT-5で20ポイント以上低下",
        },
        "synthetic_odds_below3_t5": {
            **unavailable_decision,
            "reason": "T-5の合成オッズが3倍未満",
        },
        "synthetic_odds_drop20pct_t10_to_t5": {
            **unavailable_decision,
            "reason": "合成オッズがT-10からT-5で20%以上急落",
        },
        "structural_and_both_models_ev_below100": {
            **unavailable_decision,
            "reason": "構造的な頭不信とT-5期待値不足が同時発生",
        },
    }
    return {
        "version": "original-boaters-24-low-confidence-shadow-v1",
        "policy_id": LOW_CONFIDENCE_SHADOW_ID,
        "active": False,
        "notification_enabled": False,
        "status": "awaiting_target_odds",
        "production_action": "none",
        "baseline_tickets": [format_ticket(ticket) for ticket in baseline_tickets],
        "diagnostics": diagnostics,
        "candidate_decisions": candidates,
        "odds_evidence": {},
    }


def evaluate_ticket_ev_shadow(
    rows: list[dict[str, Any]],
    baseline_tickets: list[str],
) -> dict[str, Any]:
    """Freeze pre-race ticket probabilities for later T-10/T-5 EV joins."""
    by_boat = {int(row["boat_number"]): row for row in rows}
    if set(by_boat) != set(range(1, 7)):
        return {
            "version": "original-boaters-24-ticket-ev-shadow-v1",
            "policy_id": TICKET_EV_SHADOW_ID,
            "active": False,
            "notification_enabled": False,
            "status": "unavailable",
            "reason": "six_boats_required",
            "snapshots": {},
        }
    win_weights: dict[int, float] = {}
    place_weights: dict[int, float] = {}
    for boat, row in by_boat.items():
        ai_win = number(row.get("ai_prediction_pct"))
        ai_top3 = number(row.get("ai_3ren_pct"))
        if ai_win is None or ai_top3 is None:
            return {
                "version": "original-boaters-24-ticket-ev-shadow-v1",
                "policy_id": TICKET_EV_SHADOW_ID,
                "active": False,
                "notification_enabled": False,
                "status": "unavailable",
                "reason": "ai_probability_missing",
                "snapshots": {},
            }
        win_probability = min(0.995, max(0.0005, float(ai_win) / 100.0))
        top3_probability = min(0.999, max(win_probability, float(ai_top3) / 100.0))
        conditional_place = (top3_probability - win_probability) / max(
            0.001, 1.0 - win_probability
        )
        win_weights[boat] = win_probability
        place_weights[boat] = max(0.0005, conditional_place)

    all_probabilities: dict[str, float] = {}
    first_total = sum(win_weights.values())
    for first, second, third in itertools.permutations(range(1, 7), 3):
        second_total = sum(place_weights[boat] for boat in range(1, 7) if boat != first)
        third_total = sum(
            place_weights[boat]
            for boat in range(1, 7)
            if boat not in {first, second}
        )
        probability = (
            win_weights[first]
            / first_total
            * place_weights[second]
            / second_total
            * place_weights[third]
            / third_total
        )
        all_probabilities[f"{first}{second}{third}"] = probability

    probability_sum = sum(all_probabilities.values())
    if probability_sum <= 0:
        return {
            "version": "original-boaters-24-ticket-ev-shadow-v1",
            "policy_id": TICKET_EV_SHADOW_ID,
            "active": False,
            "notification_enabled": False,
            "status": "unavailable",
            "reason": "probability_generation_failed",
            "snapshots": {},
        }
    normalized = {
        combo: probability / probability_sum for combo, probability in all_probabilities.items()
    }
    tickets = []
    for ticket in baseline_tickets:
        combo = re.sub(r"\D", "", str(ticket))
        if combo not in normalized:
            continue
        tickets.append(
            {
                "ticket": format_ticket(combo),
                "combo": combo,
                "probability": round(normalized[combo], 8),
                "probability_pct": round(normalized[combo] * 100.0, 6),
            }
        )
    return {
        "version": "original-boaters-24-ticket-ev-shadow-v1",
        "policy_id": TICKET_EV_SHADOW_ID,
        "active": False,
        "notification_enabled": False,
        "status": "awaiting_target_odds",
        "probability_method": (
            "AI1着率を1着重み、(AI3連対率-AI1着率)/(1-AI1着率)を"
            "2・3着重みにした逐次条件付きモデル"
        ),
        "probability_sum_all_combos": round(sum(normalized.values()), 8),
        "selected_probability_pct": round(
            sum(item["probability"] for item in tickets) * 100.0, 6
        ),
        "tickets": tickets,
        "snapshots": {},
    }


def evaluate_ticket_position_shadow(
    rows: list[dict[str, Any]],
    baseline_tickets: list[str],
    *,
    field_prefix: str = "trifecta_position",
    summary_key: str = "trifecta_position_model_summary",
    policy_id: str = TICKET_POSITION_SHADOW_ID,
    version: str = "original-boaters-24-ticket-position-shadow-v1",
    probability_method: str = "1着・2着・3着の専用モデルを逐次条件付きで120通りへ変換",
) -> dict[str, Any]:
    """Freeze role-specific first/second/third probabilities for EV comparison."""
    by_boat = {int(row["boat_number"]): row for row in rows}
    unavailable = {
        "version": version,
        "policy_id": policy_id,
        "active": False,
        "notification_enabled": False,
        "status": "unavailable",
        "snapshots": {},
    }
    if set(by_boat) != set(range(1, 7)):
        return {**unavailable, "reason": "six_boats_required"}

    roles: dict[int, dict[int, float]] = {}
    for position in (1, 2, 3):
        values = {
            boat: number(row.get(f"{field_prefix}{position}_pct"))
            for boat, row in by_boat.items()
        }
        if any(value is None or value < 0 for value in values.values()):
            return {**unavailable, "reason": f"position{position}_probability_missing"}
        total = sum(float(value) for value in values.values())
        if total <= 0:
            return {**unavailable, "reason": f"position{position}_probability_invalid"}
        roles[position] = {
            boat: max(1e-12, float(value) / total)
            for boat, value in values.items()
        }

    all_probabilities: dict[str, float] = {}
    boats = tuple(range(1, 7))
    for first, second, third in itertools.permutations(boats, 3):
        second_total = sum(roles[2][boat] for boat in boats if boat != first)
        third_total = sum(
            roles[3][boat] for boat in boats if boat not in {first, second}
        )
        all_probabilities[f"{first}{second}{third}"] = (
            roles[1][first]
            * roles[2][second]
            / second_total
            * roles[3][third]
            / third_total
        )
    probability_sum = sum(all_probabilities.values())
    normalized = {
        combo: probability / probability_sum
        for combo, probability in all_probabilities.items()
    }
    tickets = []
    for ticket in baseline_tickets:
        combo = re.sub(r"\D", "", str(ticket))
        if combo not in normalized:
            continue
        tickets.append(
            {
                "ticket": format_ticket(combo),
                "combo": combo,
                "probability": round(normalized[combo], 8),
                "probability_pct": round(normalized[combo] * 100.0, 6),
            }
        )
    model_summary = next(
        (
            row.get(summary_key)
            for row in rows
            if isinstance(row.get(summary_key), dict)
        ),
        {},
    )
    return {
        "version": version,
        "policy_id": policy_id,
        "active": False,
        "notification_enabled": False,
        "status": "awaiting_target_odds",
        "probability_method": probability_method,
        "model_path": model_summary.get("model_path"),
        "model_version": model_summary.get("model_version"),
        "probability_sum_all_combos": round(sum(normalized.values()), 8),
        "selected_probability_pct": round(
            sum(item["probability"] for item in tickets) * 100.0,
            6,
        ),
        "position_probabilities_pct": {
            f"position{position}": {
                str(boat): round(probability * 100.0, 6)
                for boat, probability in roles[position].items()
            }
            for position in (1, 2, 3)
        },
        "tickets": tickets,
        "snapshots": {},
    }


def evaluate_ticket_venue_probability_shadow(
    rows: list[dict[str, Any]],
    baseline_tickets: list[str],
) -> dict[str, Any]:
    """Freeze venue/lane probability-point overlay probabilities for EV comparison."""
    return evaluate_ticket_position_shadow(
        rows,
        baseline_tickets,
        field_prefix="venue_probability_position",
        summary_key="venue_probability_overlay_summary",
        policy_id=TICKET_VENUE_PROBABILITY_SHADOW_ID,
        version="original-boaters-24-ticket-venue-probability-shadow-v1",
        probability_method=(
            "BOATERS AI1/AI3を基準に、場×艇番・展示・気象・水面・スリットを"
            "着順別の確率ポイントとして補正し120通りへ変換"
        ),
    )


def ticket_families(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    non1 = sorted(
        [row for row in rows if int(number(row.get("boat_number"), 0) or 0) != 1],
        key=lambda row: (-pre_race_win_score(row), row["boat_number"]),
    )
    outer = [row for row in non1 if int(row["boat_number"]) in {3, 4, 5, 6}]
    non1_heads = [int(row["boat_number"]) for row in non1]
    outer_heads = [int(row["boat_number"]) for row in outer]
    head56 = [boat for boat in non1_heads if boat in {5, 6}]
    return {
        "outer_box3": box3(rows, {3, 4, 5, 6}),
        "non1_box3": box3(rows, {2, 3, 4, 5, 6}),
        "outer_h1_no1": ranked_formation(rows, outer_heads[:1], allow_b1_place=False),
        "outer_h2_no1": ranked_formation(rows, outer_heads[:2], allow_b1_place=False),
        "non1_h1_no1": ranked_formation(rows, non1_heads[:1], allow_b1_place=False),
        "non1_h2_no1": ranked_formation(rows, non1_heads[:2], allow_b1_place=False),
        "outer_h1_b1place": ranked_formation(rows, outer_heads[:1], allow_b1_place=True),
        "outer_h2_b1place": ranked_formation(rows, outer_heads[:2], allow_b1_place=True),
        "non1_h1_b1place": ranked_formation(rows, non1_heads[:1], allow_b1_place=True),
        "non1_h2_b1place": ranked_formation(rows, non1_heads[:2], allow_b1_place=True),
        "outer_h2_no1_has56": ranked_formation(
            rows, outer_heads[:2], allow_b1_place=False, require_56=True
        ),
        "non1_h2_no1_has56": ranked_formation(
            rows, non1_heads[:2], allow_b1_place=False, require_56=True
        ),
        "head56_h1_no1": ranked_formation(rows, head56[:1], allow_b1_place=False),
        "head56_h2_no1": ranked_formation(rows, head56[:2], allow_b1_place=False),
        "head56_h1_b1place": ranked_formation(rows, head56[:1], allow_b1_place=True),
        "head56_h2_b1place": ranked_formation(rows, head56[:2], allow_b1_place=True),
    }


def _metric_count(metrics: dict[str, Any], *keys: str) -> int:
    return max((int(number(metrics.get(key), 0) or 0) for key in keys), default=0)


def exhibition_mode(venue: str, metrics: dict[str, Any]) -> str:
    forced_mode = str(metrics.get("boaters_exhibition_mode") or "")
    if forced_mode in {"full", "tenji_only"}:
        return forced_mode
    tenji_count = _metric_count(metrics, "tenji_boats")
    lap_count = _metric_count(
        metrics,
        "isshu_boats",
        "raw_isshu_boats",
        "hanshu_boats",
        "raw_hanshu_boats",
    )
    if tenji_count >= 6 and lap_count >= 6:
        return "full"
    if normalize_venue(venue) in TENJI_FALLBACK_VENUES and tenji_count >= 6:
        return "tenji_only"
    return "missing"


def make_feature(
    race: dict[str, Any],
    metrics: dict[str, Any],
    rows: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, str]:
    venue = normalize_venue(race.get("place_name"))
    by_boat = {
        int(number(row.get("boat_number"), 0) or 0): row
        for row in rows
        if int(number(row.get("boat_number"), 0) or 0) in range(1, 7)
    }
    if len(by_boat) != 6:
        return None, "six_boats_required"
    required = ("ai_prediction_pct", "ai_3ren_pct", "odds_prediction_pct")
    if any(number(row.get(field)) is None for row in by_boat.values() for field in required):
        return None, "original_boaters_ai_or_popularity_missing"
    mode = exhibition_mode(venue, metrics)
    if mode == "missing":
        return None, "full_exhibition_missing"

    normalized_rows = [by_boat[boat] for boat in range(1, 7)]
    b1 = by_boat[1]
    outer = [by_boat[boat] for boat in range(3, 7)]
    outer56 = [by_boat[boat] for boat in (5, 6)]
    outer_top2 = 0
    outer_top1 = 0
    for row in outer:
        tenji = valid_rank(row.get("tenji_rank") or row.get("exhibit_rank") or row.get("tenji_time_rank"))
        lap = valid_rank(row.get("isshu_rank")) if mode == "full" else None
        if (tenji is not None and tenji <= 2) or (lap is not None and lap <= 2):
            outer_top2 += 1
        if tenji == 1 or lap == 1:
            outer_top1 += 1

    top3_order = sorted(
        normalized_rows,
        key=lambda row: (-(number(row.get("ai_3ren_pct"), 0.0) or 0.0), row["boat_number"]),
    )
    top3_rank = {int(row["boat_number"]): index for index, row in enumerate(top3_order, start=1)}
    b1_ai = number(b1.get("ai_prediction_pct"))
    b1_odds = number(metrics.get("boat1_odds_prediction_pct"), number(b1.get("odds_prediction_pct")))
    feature = {
        "date": str(race.get("date") or ""),
        "race_id": str(race.get("race_id") or ""),
        "place_name": venue,
        "round": int(number(race.get("round"), 0) or 0),
        "data_mode": mode,
        "wind_speed": number(race.get("wind_speed"), number(metrics.get("wind_speed"), 0.0)),
        "wave_height": number(race.get("wave_height"), number(metrics.get("wave_height"), 0.0)),
        "b1_odds_pct": b1_odds,
        "b1_odds_rank": int(number(metrics.get("boat1_odds_rank"), number(b1.get("odds_prediction_pct_rank"), 9)) or 9),
        "b1_nige_pct": number(metrics.get("boat1_nige_pct"), number(b1.get("nige_pct"))),
        "b1_ai_win": b1_ai,
        "b1_ai_top3": number(b1.get("ai_3ren_pct")),
        "b1_overbet_gap": b1_odds - b1_ai if b1_odds is not None and b1_ai is not None else None,
        "b1_tenji_rank": int(number(metrics.get("boat1_tenji_time_rank") or metrics.get("boat1_tenji_rank"), 9) or 9),
        "b1_lap_rank": number(metrics.get("boat1_isshu_rank")) if mode == "full" else None,
        "b1_avg_diff": number(metrics.get("boat1_avg_isshu_diff")) if mode == "full" else None,
        "outer56_avg_diff": (
            number(metrics.get("outer56_best_avg_isshu_diff")) if mode == "full" else None
        ),
        "outer_top2_count": outer_top2,
        "outer_top1_count": outer_top1,
        "outer_ai_win_max": max(number(row.get("ai_prediction_pct"), 0.0) or 0.0 for row in outer),
        "outer_ai_top3_max": max(number(row.get("ai_3ren_pct"), 0.0) or 0.0 for row in outer),
        "outer56_ai_win_max": max(number(row.get("ai_prediction_pct"), 0.0) or 0.0 for row in outer56),
        "boat5_ai_top3_rank": top3_rank.get(5),
        "boat6_ai_top3_rank": top3_rank.get(6),
    }
    return feature, ""


def base_matches(base_id: str, feature: dict[str, Any]) -> bool:
    match = re.fullmatch(r"pop([12])_odds(\d+)_nige(\d+)", base_id)
    if match:
        max_rank, odds_min, nige_max = map(int, match.groups())
        return (
            feature.get("b1_odds_rank") is not None
            and feature["b1_odds_rank"] <= max_rank
            and feature.get("b1_odds_pct") is not None
            and feature["b1_odds_pct"] >= odds_min
            and feature.get("b1_nige_pct") is not None
            and feature["b1_nige_pct"] <= nige_max
        )
    match = re.fullmatch(r"pop1_aiwin(\d+)", base_id)
    if match:
        ai_max = int(match.group(1))
        return (
            feature.get("b1_odds_rank") == 1
            and feature.get("b1_odds_pct") is not None
            and feature["b1_odds_pct"] >= 30
            and feature.get("b1_ai_win") is not None
            and feature["b1_ai_win"] <= ai_max
        )
    match = re.fullmatch(r"pop1_overbet(\d+)", base_id)
    if match:
        gap = int(match.group(1))
        return (
            feature.get("b1_odds_rank") == 1
            and feature.get("b1_odds_pct") is not None
            and feature["b1_odds_pct"] >= 30
            and feature.get("b1_overbet_gap") is not None
            and feature["b1_overbet_gap"] >= gap
        )
    raise KeyError(f"unsupported base rule: {base_id}")


def _ge(feature: dict[str, Any], key: str, threshold: float) -> bool:
    value = number(feature.get(key))
    return value is not None and value >= threshold


def _le(feature: dict[str, Any], key: str, threshold: float) -> bool:
    value = number(feature.get(key))
    return value is not None and value <= threshold


def context_matches(context_id: str, feature: dict[str, Any]) -> bool:
    round_no = int(number(feature.get("round"), 0) or 0)
    predicates = {
        "early_b1tenji4": lambda: round_no <= 6 and _ge(feature, "b1_tenji_rank", 4),
        "b1tenji4": lambda: _ge(feature, "b1_tenji_rank", 4),
        "b1tenji5": lambda: _ge(feature, "b1_tenji_rank", 5),
        "b1lap3": lambda: _ge(feature, "b1_lap_rank", 3),
        "b1lap4": lambda: _ge(feature, "b1_lap_rank", 4),
        "outer56_ai12": lambda: _ge(feature, "outer56_ai_win_max", 12),
        "late_b1tenji4": lambda: round_no >= 9 and _ge(feature, "b1_tenji_rank", 4),
        "wind5": lambda: _ge(feature, "wind_speed", 5),
        "outer2_b1tenji4": lambda: _ge(feature, "outer_top2_count", 2) and _ge(feature, "b1_tenji_rank", 4),
        "round9_12": lambda: round_no >= 9,
        "outer2_wind5": lambda: _ge(feature, "outer_top2_count", 2) and _ge(feature, "wind_speed", 5),
        "outer2_wind3": lambda: _ge(feature, "outer_top2_count", 2) and _ge(feature, "wind_speed", 3),
        "outer2_wave3": lambda: _ge(feature, "outer_top2_count", 2) and _ge(feature, "wave_height", 3),
        "outertop2_1": lambda: _ge(feature, "outer_top2_count", 1),
        "outer2_o56avg010": lambda: _ge(feature, "outer_top2_count", 2) and _ge(feature, "outer56_avg_diff", 0.10),
        "wind4_outer2": lambda: _ge(feature, "wind_speed", 4) and _ge(feature, "outer_top2_count", 2),
        "round1_3": lambda: round_no <= 3,
        "b1avg000": lambda: _le(feature, "b1_avg_diff", 0.0),
        "late_b1lap4": lambda: round_no >= 9 and _ge(feature, "b1_lap_rank", 4),
        "outer2_early": lambda: round_no <= 6 and _ge(feature, "outer_top2_count", 2),
        "b1avg_m005": lambda: _le(feature, "b1_avg_diff", -0.05),
        "b1lap4_outertop355": lambda: _ge(feature, "b1_lap_rank", 4)
        and _ge(feature, "outer_ai_top3_max", 55),
    }
    if context_id not in predicates:
        raise KeyError(f"unsupported context rule: {context_id}")
    return bool(predicates[context_id]())


def template_parts(template_id: str, feature: dict[str, Any]) -> tuple[str, int]:
    if template_id in {"outer_box3_6", "non1_box3_6"}:
        return template_id.removesuffix("_6"), 6
    adaptive = re.fullmatch(r"(.+)_top4_plus5_outertop3_ge(65|70)", template_id)
    if adaptive:
        family, threshold = adaptive.groups()
        points = 5 if _ge(feature, "outer_ai_top3_max", int(threshold)) else 4
        return family, points
    match = re.fullmatch(r"(.+)_top(\d+)", template_id)
    if not match:
        raise KeyError(f"unsupported ticket template: {template_id}")
    return match.group(1), int(match.group(2))


def format_ticket(combo: str) -> str:
    return "-".join(combo) if len(combo) == 3 and combo.isdigit() else combo


def evaluate(
    race: dict[str, Any],
    metrics: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    ai_source: str,
    rules_path: Path | str = DEFAULT_RULES_PATH,
) -> dict[str, Any]:
    venue = normalize_venue(race.get("place_name"))
    rule = rules_by_venue(rules_path).get(venue)
    if not rule:
        return {"status": "skip_unknown_venue", "matched": False, "venue": venue}
    if ai_source != ORIGINAL_AI_SOURCE:
        return {
            "status": "skip_incompatible_ai_source",
            "matched": False,
            "venue": venue,
            "ai_source": ai_source,
        }
    feature, reason = make_feature(race, metrics, rows)
    if feature is None:
        return {"status": "skip_missing_data", "matched": False, "venue": venue, "reason": reason}
    base_ok = base_matches(rule["base_id"], feature)
    context_ok = context_matches(rule["context_id"], feature)
    if not (base_ok and context_ok):
        return {
            "status": "not_matched",
            "matched": False,
            "venue": venue,
            "base_ok": base_ok,
            "context_ok": context_ok,
        }
    family, points = template_parts(rule["template_id"], feature)
    tickets = ticket_families(rows).get(family, [])[:points]
    if len(tickets) != points:
        return {
            "status": "skip_ticket_generation_failed",
            "matched": False,
            "venue": venue,
            "expected_points": points,
            "actual_points": len(tickets),
        }
    formatted = [format_ticket(ticket) for ticket in tickets]
    heads = sorted({int(ticket[0]) for ticket in tickets})
    axes = sorted({int(ticket[1]) for ticket in tickets})
    head_swap_shadow = evaluate_head_swap_shadow(rows, family, points, tickets)
    head56_confidence_shadow = evaluate_head56_confidence_shadow(rows, family, tickets)
    low_confidence_shadow = evaluate_low_confidence_shadow(rows, family, tickets)
    ticket_ev_shadow = evaluate_ticket_ev_shadow(rows, tickets)
    ticket_position_shadow = evaluate_ticket_position_shadow(rows, tickets)
    ticket_venue_probability_shadow = evaluate_ticket_venue_probability_shadow(
        rows,
        tickets,
    )
    buff_debuff_shadow = venue_factor_shadow.evaluate_skip_s_head_debuff(
        race,
        rows,
        tickets,
    )
    return {
        "status": "matched",
        "matched": True,
        "rule_set_id": RULE_SET_ID,
        "rule_id": f"{RULE_SET_ID}:{venue}:{rule['base_id']}:{rule['context_id']}:{rule['template_id']}",
        "rule_status": rule.get("status"),
        "venue": venue,
        "condition": rule.get("condition"),
        "buy_method": rule.get("buy_method"),
        "base_id": rule.get("base_id"),
        "context_id": rule.get("context_id"),
        "template_id": rule.get("template_id"),
        "historical": rule.get("historical") or {},
        "ai_source": ai_source,
        "data_mode": feature.get("data_mode"),
        "points": points,
        "tickets": formatted,
        "heads": heads,
        "axes": axes,
        "keshi": 1 if all("1" not in ticket for ticket in tickets) else None,
        "condition_snapshot": feature,
        "head_swap_shadow": head_swap_shadow,
        "head56_confidence_shadow": head56_confidence_shadow,
        "low_confidence_shadow": low_confidence_shadow,
        "ticket_ev_shadow": ticket_ev_shadow,
        "ticket_position_shadow": ticket_position_shadow,
        "ticket_venue_probability_shadow": ticket_venue_probability_shadow,
        "new_buff_debuff_shadow": buff_debuff_shadow,
    }


def validate_rule_coverage(path: Path | str = DEFAULT_RULES_PATH) -> dict[str, Any]:
    payload = load_rules(path)
    errors = []
    for rule in payload.get("rules") or []:
        feature = {
            "round": 12,
            "b1_odds_rank": 1,
            "b1_odds_pct": 100.0,
            "b1_nige_pct": 0.0,
            "b1_ai_win": 0.0,
            "b1_overbet_gap": 100.0,
            "b1_tenji_rank": 6,
            "b1_lap_rank": 6,
            "b1_avg_diff": -1.0,
            "outer56_avg_diff": 1.0,
            "outer_top2_count": 4,
            "outer_ai_top3_max": 100.0,
            "outer56_ai_win_max": 100.0,
            "wind_speed": 10.0,
            "wave_height": 10.0,
        }
        try:
            base_matches(rule["base_id"], feature)
            context_matches(rule["context_id"], feature)
            template_parts(rule["template_id"], feature)
        except Exception as exc:
            errors.append({"venue": rule.get("venue"), "error": str(exc)})
    return {"ok": not errors, "rule_count": len(payload.get("rules") or []), "errors": errors}
