#!/usr/bin/env python3
"""Build GitHub Pages JSON for the Codex BOATERS manshu ranking widget."""

from __future__ import annotations

import argparse
import ast
import csv
import json
import math
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLACE_IDS = {
    "桐生": 1,
    "戸田": 2,
    "江戸川": 3,
    "平和島": 4,
    "多摩川": 5,
    "浜名湖": 6,
    "蒲郡": 7,
    "常滑": 8,
    "津": 9,
    "三国": 10,
    "びわこ": 11,
    "住之江": 12,
    "尼崎": 13,
    "鳴門": 14,
    "丸亀": 15,
    "児島": 16,
    "宮島": 17,
    "徳山": 18,
    "下関": 19,
    "若松": 20,
    "芦屋": 21,
    "福岡": 22,
    "唐津": 23,
    "大村": 24,
}


def iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def as_num(value):
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def as_int(value):
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if math.isfinite(number) else None


def format_trifecta(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) == 3:
        return "-".join(digits)
    return text


def race_place_id(row: dict) -> int | None:
    if row.get("place_id") is not None:
        return as_int(row.get("place_id"))
    race_id = str(row.get("race_id") or "")
    if len(race_id) >= 15 and race_id[:10].count("-") == 2:
        return as_int(race_id[10:12])
    return PLACE_IDS.get(str(row.get("place_name") or ""))


def result_from_openapi(row: dict) -> dict:
    trifecta = None
    payout = None
    trifectas = (row.get("payouts") or {}).get("trifecta") or []
    if trifectas:
        trifecta = trifectas[0].get("combination")
        payout = as_int(trifectas[0].get("payout"))
    if not trifecta:
        places = {}
        for boat in row.get("boats") or []:
            place = as_int(boat.get("racer_place_number"))
            lane = as_int(boat.get("racer_boat_number"))
            if place and lane:
                places[place] = lane
        if all(place in places for place in [1, 2, 3]):
            trifecta = f"{places[1]}-{places[2]}-{places[3]}"
    return {
        "trifecta": trifecta,
        "payout_yen": payout,
        "manshu": bool(payout is not None and payout >= 10000),
        "win_method": row.get("race_technique_number"),
        "weather": row.get("race_weather_number"),
        "wind_speed": as_num(row.get("race_wind")),
        "wave_height": as_num(row.get("race_wave")),
    }


def load_results_map(path_text: str | None) -> dict[tuple[int, int], dict]:
    if not path_text:
        return {}
    path = Path(path_text)
    if not path.exists():
        raise FileNotFoundError(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    results = {}
    for row in data.get("results") or []:
        place_id = as_int(row.get("race_stadium_number"))
        round_no = as_int(row.get("race_number"))
        if place_id and round_no:
            results[(place_id, round_no)] = result_from_openapi(row)
    return results


def load_csv_rows(path_text: str | None) -> list[dict]:
    if not path_text:
        return []
    path = Path(path_text)
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = []
        for row in csv.DictReader(handle):
            row = dict(row)
            if isinstance(row.get("composite_edges"), str) and row["composite_edges"].strip():
                try:
                    row["composite_edges"] = ast.literal_eval(row["composite_edges"])
                except Exception:
                    row["composite_edges"] = []
            rows.append(row)
        return rows


def unique_rows(rows: list[dict], top_n: int) -> list[dict]:
    seen = set()
    unique = []
    for row in rows:
        key = row.get("race_id") or (
            row.get("date"),
            row.get("place_name"),
            str(row.get("round") if row.get("round") is not None else row.get("round_no")),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
        if len(unique) >= top_n:
            break
    return unique


def normalize_result(row: dict, live_result: dict | None = None) -> dict:
    result = row.get("result") or {}
    payout = result.get("payout_yen")
    if payout is None:
        payout = row.get("payout_yen")
    if payout is None:
        payout = row.get("payout")
    trifecta = result.get("trifecta") or row.get("trifecta") or row.get("winning_number3t1")
    if live_result:
        trifecta = live_result.get("trifecta") or trifecta
        payout = live_result.get("payout_yen") if live_result.get("payout_yen") is not None else payout
    return {
        "trifecta": format_trifecta(trifecta),
        "payout_yen": as_int(payout),
        "manshu": bool(as_int(payout) and as_int(payout) >= 10000),
        "win_method": (live_result or {}).get("win_method") or result.get("win_method"),
        "weather": (live_result or {}).get("weather") or result.get("weather") or row.get("weather"),
        "wind_speed": as_num(
            (live_result or {}).get("wind_speed")
            if (live_result or {}).get("wind_speed") is not None
            else result.get("wind_speed")
            if result.get("wind_speed") is not None
            else row.get("wind_speed")
        ),
        "wave_height": as_num(
            (live_result or {}).get("wave_height")
            if (live_result or {}).get("wave_height") is not None
            else result.get("wave_height")
            if result.get("wave_height") is not None
            else row.get("wave_height")
        ),
    }


def normalize_row(row: dict, rank: int, date_text: str, results_map: dict[tuple[int, int], dict] | None = None) -> dict:
    place_id = race_place_id(row)
    round_no = as_int(row.get("round") if row.get("round") is not None else row.get("round_no"))
    live_result = (results_map or {}).get((place_id, round_no))
    rate = row.get("manshu_rate_pct")
    if rate is None:
        rate = row.get("best_manshu_rate_pct")
    recent = row.get("recent_rate_pct")
    if recent is None:
        recent = row.get("best_recent_rate_pct")
    condition = row.get("condition")
    if condition is None:
        condition = row.get("best_condition")
    metrics = row.get("metrics") or {}
    metric_map = {
        "boat1_ai_prediction_pct": "b1_ai_prediction_pct",
        "boat1_odds_prediction_pct": "b1_odds_prediction_pct",
        "boat1_odds_rank": "b1_odds_rank",
        "boat1_ai_plus": "b1_ai_plus",
        "boat1_ai_plus_order": "b1_ai_plus_order",
        "boat1_nige_pct": "b1_nige_pct",
        "boat1_loss_pct": "b1_loss_pct",
        "is_joshi": "is_joshi",
        "boat1_avg_isshu_diff": "b1_avg_isshu_diff",
        "boat1_isshu_avg_diff": "b1_isshu_avg_diff",
        "avg_isshu_time": "avg_isshu_time",
        "avg_exhibit_combo_time": "avg_exhibit_combo_time",
        "is_summer": "is_summer",
        "b1_summer_isshu_factor": "b1_summer_isshu_factor",
        "b1_summer_nige_delta_pp": "b1_summer_nige_delta_pp",
        "boat1_tenji_time": "b1_tenji_time",
        "boat1_isshu_time": "b1_isshu_time",
        "outer56_best_avg_isshu_diff": "outer56_best_avg_isshu_diff",
        "outer56_best_ai_prediction_pct": "outer56_best_ai_prediction_pct",
        "outer56_best_tenji_time": "outer56_best_tenji_time",
        "outer56_best_isshu_time": "outer56_best_isshu_time",
        "ai_rank6_boat": "ai_rank6_boat",
        "ai_rank6_avg_isshu_diff": "ai_rank6_avg_isshu_diff",
        "ai_rank6_ai_prediction_pct": "ai_rank6_ai_prediction_pct",
        "ai_rank6_tenji_rank": "ai_rank6_tenji_rank",
        "ai_rank6_isshu_rank": "ai_rank6_isshu_rank",
        "ai_rank5_boat": "ai_rank5_boat",
        "ai_rank5_avg_isshu_diff": "ai_rank5_avg_isshu_diff",
        "ai_rank5_ai_prediction_pct": "ai_rank5_ai_prediction_pct",
        "ai_rank5_tenji_rank": "ai_rank5_tenji_rank",
        "ai_rank5_isshu_rank": "ai_rank5_isshu_rank",
        "low_outer_boat": "low_outer_boat",
        "low_outer_ai_plus_rank": "low_outer_ai_plus_rank",
        "low_outer_avg_isshu_diff": "low_outer_avg_isshu_diff",
        "low_outer_ai_prediction_pct": "low_outer_ai_prediction_pct",
        "low_outer_tenji_rank": "low_outer_tenji_rank",
        "low_outer_isshu_rank": "low_outer_isshu_rank",
        "low_outer_exhibit_top2": "low_outer_exhibit_top2",
        "center_attack_wall_outer": "center_attack_wall_outer",
        "weather_pressure": "weather_pressure",
        "outer_isshu_priority_b1weak": "outer_isshu_priority_b1weak",
        "b1_full_tobashi_shape": "b1_full_tobashi_shape",
        "longshot_head_boats": "longshot_head_boats",
        "longshot_head_candidate_count": "longshot_head_candidate_count",
        "longshot_head_with_b1_gap": "longshot_head_with_b1_gap",
        "double_time_boats": "double_time_boats",
        "super_slit_boats": "super_slit_boats",
        "super_slit_alert_count": "super_slit_alert_count",
        "mid234_super_slit_count": "mid234_super_slit_count",
        "outer456_super_slit_count": "outer456_super_slit_count",
        "outer56_super_slit_count": "outer56_super_slit_count",
        "slit_shape_label": "slit_shape_label",
        "b1_slit_gap_vs_23": "b1_slit_gap_vs_23",
        "b3_slit_adv_vs_12": "b3_slit_adv_vs_12",
        "b4_slit_adv_vs_123": "b4_slit_adv_vs_123",
        "outer56_slit_adv_vs_1": "outer56_slit_adv_vs_1",
        "outer456_slit_adv_vs_123": "outer456_slit_adv_vs_123",
        "slit_dekoboko": "slit_dekoboko",
        "slit_b1_front_wall": "slit_b1_front_wall",
        "slit_b1_hole_vs_23": "slit_b1_hole_vs_23",
        "slit_b2_wall_break_3peek": "slit_b2_wall_break_3peek",
        "slit_b3_peek_vs_12": "slit_b3_peek_vs_12",
        "slit_b4_cadou_peek": "slit_b4_cadou_peek",
        "slit_outer456_pressure": "slit_outer456_pressure",
        "slit_outer56_pressure_vs_1": "slit_outer56_pressure_vs_1",
        "matchup_lane1_pressure_score": "matchup_lane1_pressure_score",
        "matchup_outer_good_count": "matchup_outer_good_count",
        "matchup_lane1_bad_flag": "matchup_lane1_bad_flag",
        "matchup_notes": "matchup_notes",
        "matchup_buff_boats": "matchup_buff_boats",
        "b1_matchup_label": "b1_matchup_label",
        "b2_matchup_label": "b2_matchup_label",
        "b3_matchup_label": "b3_matchup_label",
        "b4_matchup_label": "b4_matchup_label",
        "b5_matchup_label": "b5_matchup_label",
        "b6_matchup_label": "b6_matchup_label",
        "boat1_double_time": "boat1_double_time",
        "mid234_double_time_count": "mid234_double_time_count",
        "outer46_double_time_count": "outer46_double_time_count",
        "outer56_double_time_count": "outer56_double_time_count",
        "wind_speed": "wind_speed",
        "wave_height": "wave_height",
        "tenji_boats": "tenji_boats",
        "isshu_boats": "isshu_boats",
    }
    normalized_metrics = {}
    for out_key, in_key in metric_map.items():
        value = metrics.get(out_key)
        if value is None:
            value = row.get(in_key)
        if out_key in {
            "double_time_boats",
            "super_slit_boats",
            "b1_summer_isshu_factor",
            "slit_shape_label",
            "matchup_notes",
            "matchup_buff_boats",
            "longshot_head_boats",
            "b1_matchup_label",
            "b2_matchup_label",
            "b3_matchup_label",
            "b4_matchup_label",
            "b5_matchup_label",
            "b6_matchup_label",
        }:
            normalized_metrics[out_key] = value or ""
        else:
            normalized_metrics[out_key] = as_num(value)
    normalized_metrics["tenji_boats"] = as_int(normalized_metrics["tenji_boats"]) or 0
    normalized_metrics["isshu_boats"] = as_int(normalized_metrics["isshu_boats"]) or 0
    return {
        "rank": rank,
        "status": row.get("status") or "未確定",
        "date": row.get("date") or date_text,
        "race_id": row.get("race_id"),
        "place_id": place_id,
        "place_name": row.get("place_name"),
        "round": round_no,
        "deadline_time": row.get("deadline_time"),
        "race_kind": row.get("race_kind"),
        "series_title": row.get("series_title"),
        "ranking_type": row.get("ranking_type"),
        "manshu_rate_pct": as_num(rate),
        "base_manshu_rate_pct": as_num(row.get("base_manshu_rate_pct")),
        "composite_edge_base_rate_pct": as_num(row.get("composite_edge_base_rate_pct")),
        "composite_edge_bonus_pct": as_num(row.get("composite_edge_bonus_pct")),
        "composite_edges": row.get("composite_edges") or [],
        "recent_rate_pct": as_num(recent),
        "condition": condition,
        "matched_logic_count": as_int(row.get("matched_logic_count")) or 0,
        "metrics": normalized_metrics,
        "result": normalize_result(row, live_result),
    }


def build_payload(source: dict, top_n: int, results_map: dict[tuple[int, int], dict] | None = None) -> dict:
    date_text = source["date"]
    strict_source_rows = []
    unified_source_rows = source.get("unified_rank_top")
    if isinstance(unified_source_rows, list) and unified_source_rows:
        rows = list(unified_source_rows)
        strict_source_rows = list(unified_source_rows)
    elif isinstance(source.get("all_venue_rank_top"), list):
        rows = list(source.get("all_venue_rank_top") or [])
        strict_source_rows = list(source.get("strict_rank_top") or [])
        if not strict_source_rows:
            strict_source_rows = list(source.get("actual_rank_top") or []) + list(source.get("watch_rank_top") or [])
    elif isinstance(source.get("races"), list):
        rows = list(source.get("races") or [])
        strict_source_rows = list(source.get("strict_races") or [])
    else:
        rows = list(source.get("actual_rank_top") or []) + list(source.get("watch_rank_top") or [])
    rows = unique_rows(rows, top_n)
    strict_rows = unique_rows(strict_source_rows, top_n) if strict_source_rows else []
    races = [normalize_row(row, idx + 1, date_text, results_map) for idx, row in enumerate(rows)]
    strict_races = [normalize_row(row, idx + 1, date_text, results_map) for idx, row in enumerate(strict_rows)]
    settled = [race for race in races if race["result"].get("payout_yen") is not None]
    manshu_hits = [race for race in settled if race["result"].get("manshu")]
    strict_settled = [race for race in strict_races if race["result"].get("payout_yen") is not None]
    strict_manshu_hits = [race for race in strict_settled if race["result"].get("manshu")]
    all_races = as_int(source.get("_all_races_count"))
    if all_races is None:
        source_summary = source.get("summary") or {}
        all_races = as_int(source_summary.get("all_races"))
    if all_races is None:
        all_races = len(source.get("races") or []) if isinstance(source.get("races"), list) else as_int(source.get("races")) or 0
    source_summary = source.get("summary") or {}
    with_tenji = source.get("races_with_full_tenji")
    if with_tenji is None:
        with_tenji = source_summary.get("races_with_full_tenji")
    with_isshu = source.get("races_with_full_isshu")
    if with_isshu is None:
        with_isshu = source_summary.get("races_with_full_isshu")
    return {
        "version": "boaters-manshu-logic-v2",
        "date": date_text,
        "generated_at": iso_now(),
        "threshold_pct": as_num(source.get("threshold_pct")) or 27.0,
        "logic_label": source.get("logic_label") or "Codex BOATERS展示込み 万舟率ロジック",
        "logic_summary": source.get("logic_summary")
        or "BOATERS DBのAI3連対率・一般3連対率、1号艇の逃げ/差され/まくられ傾向、オリジナル展示の展示タイム・1周タイムを組み合わせたCodex側ランキング。",
        "source": {
            "ranking_json": str(source.get("outputs", {}).get("json") or ""),
            "database": "/Users/ohyabumasaya/Desktop/price_action_analysis/outputs/boaters_all_races.sqlite",
        },
        "summary": {
            "all_races": all_races,
            "races_with_full_tenji": as_int(with_tenji) or 0,
            "races_with_full_isshu": as_int(with_isshu) or 0,
            "displayed_top_n": len(races),
            "strict_displayed_top_n": len(strict_races),
            "settled_top_n": len(settled),
            "manshu_hits_top_n": len(manshu_hits),
            "actual_manshu_rate_top_n_pct": round(len(manshu_hits) / len(settled) * 100, 2) if settled else None,
            "strict_settled_top_n": len(strict_settled),
            "strict_manshu_hits_top_n": len(strict_manshu_hits),
            "strict_actual_manshu_rate_top_n_pct": round(len(strict_manshu_hits) / len(strict_settled) * 100, 2) if strict_settled else None,
        },
        "races": races,
        "strict_races": strict_races,
    }


def fill_source_from_csv(source: dict, csv_rows: list[dict]) -> None:
    if not csv_rows:
        return
    source["_all_races_count"] = source.get("races")
    all_rows = [
        row
        for row in csv_rows
        if str(row.get("ranking_type") or "").strip() == "all_venue"
        or str(row.get("status") or "").strip() == "全場スコア"
    ]
    strict_rows = [
        row
        for row in csv_rows
        if str(row.get("ranking_type") or "").strip() == "strict"
        or str(row.get("status") or "").strip() in {"確定", "展示待ち"}
    ]
    if not source.get("all_venue_rank_top") and all_rows:
        source["all_venue_rank_top"] = all_rows
    if not source.get("strict_rank_top") and strict_rows:
        source["strict_rank_top"] = strict_rows
    if not source.get("unified_rank_top") and strict_rows:
        source["unified_rank_top"] = strict_rows
    if (not isinstance(source.get("races"), list) or not source.get("races")) and all_rows:
        source["races"] = all_rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-json", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--source-csv", help="Optional full daily CSV ranking. Used to fill TOP10 before falling back to JSON top lists.")
    parser.add_argument("--results-json", help="Optional BoatraceOpenAPI results/v2 JSON to embed static trifecta payouts.")
    args = parser.parse_args()
    source_path = Path(args.source_json)
    out_path = Path(args.out)
    source = json.loads(source_path.read_text(encoding="utf-8"))
    csv_rows = load_csv_rows(args.source_csv)
    fill_source_from_csv(source, csv_rows)
    payload = build_payload(source, args.top_n, load_results_map(args.results_json))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not payload.get("races") and not payload.get("strict_races") and out_path.exists():
        existing = json.loads(out_path.read_text(encoding="utf-8"))
        if existing.get("races") or existing.get("strict_races"):
            print(
                json.dumps(
                    {
                        "out": str(out_path),
                        "date": payload["date"],
                        "kept_existing": True,
                        "reason": "new payload has no ranking rows",
                    },
                    ensure_ascii=False,
                )
            )
            return 0
    payload_text = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
    out_path.write_text(payload_text, encoding="utf-8")
    if out_path.name.startswith("boaters_manshu_ranking_") and not out_path.name.startswith("boaters_manshu_ranking_codex_"):
        codex_name = out_path.name.replace("boaters_manshu_ranking_", "boaters_manshu_ranking_codex_", 1)
        out_path.with_name(codex_name).write_text(payload_text, encoding="utf-8")
    print(json.dumps({"out": str(out_path), "date": payload["date"], "races": len(payload["races"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
