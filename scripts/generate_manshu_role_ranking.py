#!/usr/bin/env python3
"""Generate a JSON prototype for manshu race ranking and boat roles."""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import pandas as pd


JST = timezone(timedelta(hours=9))
VERSION = "manshu-role-ranking-v1"


def safe_float(value: Any) -> float | None:
    try:
        if value in (None, "") or pd.isna(value):
            return None
        output = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(output):
        return None
    return round(output, 3)


def safe_int(value: Any) -> int | None:
    try:
        if value in (None, "") or pd.isna(value):
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def role_score(row: pd.Series, mode: str, role: str) -> float | None:
    if role == "head":
        return safe_float(row.get(f"head_score_{mode}"))
    if role == "axis":
        return safe_float(row.get(f"axis_score_{mode}"))
    if role == "toss":
        return safe_float(row.get(f"toss_score_{mode}"))
    return safe_float(row.get("strength_score"))


def probability_proxy(score: float | None, low: float, span: float) -> float | None:
    if score is None:
        return None
    # Transparent proxy for sorting/display. This is not a calibrated betting probability.
    value = low + (max(0.0, min(100.0, score)) / 100.0) * span
    return round(value, 4)


def formation_points(roles: dict[str, list[int]], name: str) -> int:
    heads = roles.get("head", [])
    axes = roles.get("axis", [])
    toss = set(roles.get("toss", []))
    opponent = roles.get("opponent", [])
    no_toss = [lane for lane in heads + axes + opponent if lane not in toss]
    combos: set[tuple[int, int, int]] = set()
    if name == "A":
        first, second, third = heads, no_toss, no_toss
    elif name == "B":
        first, second, third = heads, axes + opponent, no_toss
    elif name == "C":
        first, second, third = heads, axes, no_toss
    elif name == "D":
        first, second, third = heads[:1], heads[1:] + axes, no_toss
    else:
        return 0
    for a in first:
        for b in second:
            for c in third:
                if len({a, b, c}) == 3 and not toss.intersection({a, b, c}):
                    combos.add((a, b, c))
    return len(combos)


def build_race(group: pd.DataFrame, mode: str) -> dict[str, Any]:
    row = group.iloc[0]
    role_col = f"role_{mode}"
    rank_col = f"role_rank_{mode}"
    roles: dict[str, list[int]] = {"head": [], "axis": [], "toss": [], "opponent": []}
    boats: list[dict[str, Any]] = []
    for _, boat in group.sort_values("lane").iterrows():
        role = str(boat.get(role_col))
        lane = int(boat["lane"])
        roles.setdefault(role, []).append(lane)
        boats.append(
            {
                "lane": lane,
                "registration_no": str(boat.get("registration_no") or ""),
                "name": boat.get("name"),
                "class": boat.get("class"),
                "role": role,
                "role_rank": safe_int(boat.get(rank_col)),
                "role_score": role_score(boat, mode, role),
                "role_reason": {
                    "head": boat.get("head_reasons"),
                    "axis": boat.get("axis_reasons"),
                    "toss": boat.get("toss_reasons"),
                }.get(role, "相手候補"),
                "scores": {
                    "head": safe_float(boat.get(f"head_score_{mode}")),
                    "axis": safe_float(boat.get(f"axis_score_{mode}")),
                    "toss": safe_float(boat.get(f"toss_score_{mode}")),
                    "strength": safe_float(boat.get("strength_score")),
                    "start": safe_float(boat.get("start_score")),
                    "exhibition": safe_float(boat.get("exhibition_score")),
                    "outside_attack": safe_float(boat.get("outside_attack_score")),
                },
                "features": {
                    "national_win_rate": safe_float(boat.get("national_win_rate")),
                    "local_win_rate": safe_float(boat.get("local_win_rate")),
                    "avg_st": safe_float(boat.get("avg_st")),
                    "motor_quinella_rate": safe_float(boat.get("motor_quinella_rate")),
                    "exhibition_time": safe_float(boat.get("exhibition_time")),
                    "exhibition_rank": safe_int(boat.get("exhibition_rank")),
                },
            }
        )
    for key in roles:
        roles[key].sort()
    chaos = safe_float(row.get("chaos_score"))
    skip = bool(safe_int(row.get(f"skip_{mode}")) or 0)
    return {
        "race_id": row.get("race_id"),
        "date": row.get("date"),
        "jcd": str(row.get("jcd")).zfill(2),
        "venue_name": row.get("venue_name"),
        "race_no": safe_int(row.get("race_no")),
        "race_name": row.get("race_name"),
        "deadline": row.get("deadline"),
        "grade": row.get("grade"),
        "time_zone": row.get("time_zone"),
        "scores": {
            "manshu_score": chaos,
            "manshu_probability_proxy": probability_proxy(chaos, 0.08, 0.24),
            "target_arare_probability_proxy": probability_proxy(chaos, 0.18, 0.35),
            "data_quality_score": safe_float(row.get("data_quality_score")),
        },
        "risk_flags": {
            "lane1_not_a1": bool(safe_int(row.get("lane1_not_a1")) or 0),
            "lane1_b_class": bool(safe_int(row.get("lane1_b_class")) or 0),
            "outer_a_count": safe_int(row.get("outer_a_count")),
            "outer_motor_strong": bool(safe_int(row.get("outer_motor_strong_flag")) or 0),
            "outer_exhibition_top": bool(safe_int(row.get("outer_exhibition_top_flag")) or 0),
            "national_win_range": safe_float(row.get("national_win_range")),
            "lane1_vs_avg_win_diff": safe_float(row.get("lane1_vs_avg_win_diff")),
            "wind_speed_m": safe_float(row.get("wind_speed_m")),
            "wave_cm": safe_float(row.get("wave_cm")),
        },
        "role_summary": roles,
        "boats": sorted(boats, key=lambda item: (item["role"], item["role_rank"] or 9, item["lane"])),
        "formations": {
            "A": {"points": formation_points(roles, "A"), "definition": "頭2艇-消し以外-消し以外"},
            "B": {"points": formation_points(roles, "B"), "definition": "頭2艇-軸2艇+相手-消し以外"},
            "C": {"points": formation_points(roles, "C"), "definition": "頭2艇-軸2艇-消し以外"},
            "D": {"points": formation_points(roles, "D"), "definition": "頭1番手-頭2番手+軸2艇-消し以外"},
        },
        "skip_recommendation": {
            "skip": skip,
            "reasons": [part for part in str(row.get(f"skip_reason_{mode}") or "").split("|") if part],
        },
        "notes": [
            "娯楽・研究用の分析出力です。舟券購入を推奨するものではありません。",
            "確率は表示用の暫定proxyで、実測的中確率や利益を保証しません。",
        ],
    }


def run(args: argparse.Namespace) -> int:
    boats = pd.read_csv(args.dataset)
    date_text = args.date
    if date_text:
        boats = boats[boats["date"].astype(str) == date_text]
    if boats.empty:
        raise SystemExit(f"no rows for date={date_text}")
    races = [build_race(group, args.mode) for _, group in boats.groupby("race_id", sort=False)]
    races.sort(key=lambda item: (item["scores"]["manshu_score"] or -1), reverse=True)
    if args.top:
        races = races[: args.top]
    output = {
        "version": VERSION,
        "mode": args.mode,
        "date": date_text,
        "generated_at": datetime.now(JST).isoformat(),
        "source": {
            "dataset": args.dataset,
            "official": True,
            "notes": [
                "既存正規化データから生成したプロトタイプJSONです。",
                "既存公開ページには未接続です。",
            ],
        },
        "races": races,
    }
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path} races={len(races)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default="data/analysis/boat_role_dataset.csv")
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--mode", choices=["morning", "preview"], default="preview")
    parser.add_argument("--top", type=int, default=24)
    parser.add_argument("--output", default="data/output/manshu_role_ranking_20260616.json")
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
