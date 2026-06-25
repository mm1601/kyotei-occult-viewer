#!/usr/bin/env python3
"""Build venue/lane buff-debuff dictionaries and validate ranking flow.

The pipeline is intentionally separate from production ranking pages:

1. Learn buff/debuff labels from a training window.
2. Apply them to morning and preview race scoring.
3. Backtest ranking, role selection, and 10-15 ticket formations.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = Path("/Users/ohyabumasaya/Desktop/price_action_analysis/outputs/boaters_all_races.sqlite")
MODEL_DIR = ROOT / "data" / "model"
OUTPUT_DIR = ROOT / "data" / "output"
REPORT_DIR = ROOT / "reports"
JST_NOW = datetime.now().astimezone().isoformat(timespec="seconds")


Row = dict[str, Any]


FEATURES: dict[str, dict[str, Any]] = {
    "ai_3ren_pct": {"phase": "morning", "bin": "pct", "weight": 1.00},
    "general_3ren_pct": {"phase": "morning", "bin": "pct", "weight": 1.00},
    "ai_plus": {"phase": "morning", "bin": "ai_plus", "weight": 1.15},
    "ai_plus_rank": {"phase": "morning", "bin": "rank", "weight": 1.15},
    "st_rank_general": {"phase": "morning", "bin": "rank_float", "weight": 0.85},
    "st_time_avg_general": {"phase": "morning", "bin": "st_time", "weight": 0.65},
    "ai_prediction_pct": {"phase": "morning", "bin": "pct", "weight": 0.95},
    "odds_prediction_pct": {"phase": "morning", "bin": "pct", "weight": 0.75},
    "nige_pct_year": {"phase": "morning", "bin": "pct", "weight": 0.85},
    "sasare_pct_year": {"phase": "morning", "bin": "pct", "weight": 0.65},
    "makurare_pct_year": {"phase": "morning", "bin": "pct", "weight": 0.65},
    "sashi_pct_year": {"phase": "morning", "bin": "pct", "weight": 0.55},
    "makuri_pct_year": {"phase": "morning", "bin": "pct", "weight": 0.55},
    "makurizashi_pct_year": {"phase": "morning", "bin": "pct", "weight": 0.55},
    "wind_speed": {"phase": "morning", "bin": "wind", "weight": 0.35},
    "wave_height": {"phase": "morning", "bin": "wave", "weight": 0.35},
    "tenji_rank_calc": {"phase": "preview", "bin": "rank", "weight": 1.00},
    "tenji_diff": {"phase": "preview", "bin": "time_diff", "weight": 1.10},
    "isshu_rank_calc": {"phase": "preview", "bin": "rank", "weight": 1.05},
    "isshu_diff": {"phase": "preview", "bin": "time_diff", "weight": 1.15},
    "combo_diff": {"phase": "preview", "bin": "time_diff", "weight": 1.25},
    "chokusen_rank_calc": {"phase": "preview", "bin": "rank", "weight": 0.75},
    "chokusen_diff": {"phase": "preview", "bin": "time_diff", "weight": 0.75},
    "hanshu_rank_calc": {"phase": "preview", "bin": "rank", "weight": 0.70},
    "hanshu_diff": {"phase": "preview", "bin": "time_diff", "weight": 0.70},
    "mawariashi_rank_calc": {"phase": "preview", "bin": "rank", "weight": 0.85},
    "mawariashi_diff": {"phase": "preview", "bin": "time_diff", "weight": 0.85},
    "start_tenji_rank": {"phase": "preview", "bin": "rank", "weight": 0.80},
    "start_tenji_time": {"phase": "preview", "bin": "st_exhibit", "weight": 0.70},
    "tilt": {"phase": "preview", "bin": "tilt", "weight": 0.30},
    "super_slit_alert": {"phase": "preview", "bin": "bool", "weight": 1.30},
}


def as_float(value: Any) -> float | None:
    if value in (None, "", "-", "nan"):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def as_int(value: Any, default: int = 0) -> int:
    number = as_float(value)
    return default if number is None else int(number)


def pct(value: float | int | None) -> str:
    if value is None:
        return "-"
    return f"{float(value):.2f}%"


def pp(value: float | int | None) -> str:
    if value is None:
        return "-"
    sign = "+" if value > 0 else ""
    return f"{sign}{float(value):.2f}pt"


def open_db(path: Path) -> sqlite3.Connection:
    uri = f"file:{path}?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def parse_result(value: Any) -> list[int]:
    if not value:
        return []
    text = str(value).replace(" ", "")
    if text.isdigit() and len(text) >= 3:
        return [int(ch) for ch in text[:3] if "1" <= ch <= "6"]
    out: list[int] = []
    for part in text.split("-"):
        if part.isdigit():
            lane = int(part)
            if 1 <= lane <= 6:
                out.append(lane)
    return out[:3]


def rank_desc(values: dict[int, float | None]) -> dict[int, int | None]:
    usable = sorted(
        [(boat, value) for boat, value in values.items() if value is not None],
        key=lambda item: (-float(item[1]), item[0]),
    )
    return {boat: idx + 1 for idx, (boat, _value) in enumerate(usable)}


def rank_asc(values: dict[int, float | None]) -> dict[int, int | None]:
    usable = sorted(
        [(boat, value) for boat, value in values.items() if value is not None],
        key=lambda item: (float(item[1]), item[0]),
    )
    return {boat: idx + 1 for idx, (boat, _value) in enumerate(usable)}


def avg(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def bin_value(feature: str, value: Any) -> str | None:
    kind = FEATURES[feature]["bin"]
    number = as_float(value)
    if kind == "bool":
        if value is None:
            return None
        return "true" if bool(value) else "false"
    if number is None:
        return None
    if kind == "pct":
        if number < 20:
            return "<20"
        if number < 30:
            return "20-30"
        if number < 40:
            return "30-40"
        if number < 50:
            return "40-50"
        if number < 60:
            return "50-60"
        return "60+"
    if kind == "ai_plus":
        if number < 60:
            return "<60"
        if number < 80:
            return "60-80"
        if number < 100:
            return "80-100"
        if number < 120:
            return "100-120"
        if number < 140:
            return "120-140"
        return "140+"
    if kind == "rank":
        rank = int(round(number))
        if rank <= 1:
            return "1位"
        if rank == 2:
            return "2位"
        if rank == 3:
            return "3位"
        return "4-6位"
    if kind == "rank_float":
        if number <= 2:
            return "<=2"
        if number <= 3:
            return "2-3"
        if number <= 4:
            return "3-4"
        if number <= 5:
            return "4-5"
        return "5+"
    if kind == "time_diff":
        if number >= 0.15:
            return "+0.15以上"
        if number >= 0.10:
            return "+0.10-0.15"
        if number >= 0.05:
            return "+0.05-0.10"
        if number >= -0.05:
            return "-0.05-+0.05"
        if number >= -0.10:
            return "-0.10--0.05"
        return "-0.10以下"
    if kind == "st_time":
        if number <= 0.12:
            return "<=0.12"
        if number <= 0.14:
            return "0.12-0.14"
        if number <= 0.16:
            return "0.14-0.16"
        if number <= 0.18:
            return "0.16-0.18"
        return "0.18+"
    if kind == "st_exhibit":
        if number <= -0.05:
            return "F側<=-0.05"
        if number <= 0.05:
            return "-0.05-0.05"
        if number <= 0.10:
            return "0.05-0.10"
        if number <= 0.15:
            return "0.10-0.15"
        return "0.15+"
    if kind == "wind":
        if number < 2:
            return "<2m"
        if number < 4:
            return "2-4m"
        if number < 6:
            return "4-6m"
        return "6m+"
    if kind == "wave":
        if number < 2:
            return "<2cm"
        if number < 4:
            return "2-4cm"
        if number < 6:
            return "4-6cm"
        return "6cm+"
    if kind == "tilt":
        if number <= -0.5:
            return "-0.5以下"
        if number < 0:
            return "-0.5-0"
        if number == 0:
            return "0"
        if number <= 0.5:
            return "0-0.5"
        return "0.5超"
    return str(value)


def label_win(delta_pp: float, lift: float | None, n: int, min_n: int) -> str:
    if n < min_n:
        return "サンプル不足"
    if delta_pp >= 6 and (lift or 0) >= 1.20:
        return "超バフ"
    if delta_pp >= 3 and (lift or 0) >= 1.10:
        return "バフ"
    if delta_pp <= -6 and (lift or 99) <= 0.80:
        return "超デバフ"
    if delta_pp <= -3 and (lift or 99) <= 0.90:
        return "デバフ"
    return "中立"


def label_top3(delta_pp: float, lift: float | None, n: int, min_n: int) -> str:
    if n < min_n:
        return "サンプル不足"
    if delta_pp >= 10 and (lift or 0) >= 1.20:
        return "超バフ"
    if delta_pp >= 5 and (lift or 0) >= 1.10:
        return "バフ"
    if delta_pp <= -10 and (lift or 99) <= 0.80:
        return "超デバフ"
    if delta_pp <= -5 and (lift or 99) <= 0.90:
        return "デバフ"
    return "中立"


def signal_priority(label: str) -> int:
    return {"超バフ": 2, "バフ": 1, "中立": 0, "デバフ": -1, "超デバフ": -2}.get(label, 0)


def stronger_signal(win_label: str, top3_label: str) -> str:
    if abs(signal_priority(win_label)) >= abs(signal_priority(top3_label)):
        return win_label
    return top3_label


def query_rows(conn: sqlite3.Connection, start: str | None = None, end: str | None = None) -> list[sqlite3.Row]:
    where = [
        "rb.finish IS NOT NULL",
        "r.result_payout3t1 IS NOT NULL",
        "COALESCE(rb.is_absent, 0) = 0",
        "COALESCE(rb.henkan, 0) = 0",
    ]
    params: list[Any] = []
    if start:
        where.append("rb.date >= ?")
        params.append(start)
    if end:
        where.append("rb.date <= ?")
        params.append(end)
    sql = f"""
        SELECT
            rb.*,
            r.place_name,
            r.weather,
            r.wind_speed,
            r.wave_height,
            r.weather_degree,
            r.water_degree,
            r.result_payout3t1,
            r.winning_number3t1
        FROM race_boats rb
        JOIN races r ON r.race_id = rb.race_id
        WHERE {' AND '.join(where)}
        ORDER BY rb.date, rb.race_id, rb.boat_number
    """
    return list(conn.execute(sql, params))


def build_race_records(rows: list[sqlite3.Row]) -> list[Row]:
    by_race: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        by_race[str(row["race_id"])].append(row)

    records: list[Row] = []
    for race_id, group in by_race.items():
        if len(group) != 6:
            continue
        by_boat = {int(row["boat_number"]): row for row in group}
        top3 = set(parse_result(group[0]["winning_number3t1"]))
        payout = as_int(group[0]["result_payout3t1"])
        ai_plus = {
            boat: (
                as_float(row["ai_3ren_pct"]) + as_float(row["general_3ren_pct"])
                if as_float(row["ai_3ren_pct"]) is not None and as_float(row["general_3ren_pct"]) is not None
                else None
            )
            for boat, row in by_boat.items()
        }
        ai_plus_rank = rank_desc(ai_plus)
        ai_prediction_rank = rank_desc({boat: as_float(row["ai_prediction_pct"]) for boat, row in by_boat.items()})
        tenji_rank = rank_asc({boat: as_float(row["tenji_time"]) for boat, row in by_boat.items()})
        isshu_rank = rank_asc({boat: as_float(row["isshu_time"]) for boat, row in by_boat.items()})
        chokusen_rank = rank_asc({boat: as_float(row["chokusen_time"]) for boat, row in by_boat.items()})
        hanshu_rank = rank_asc({boat: as_float(row["hanshu_time"]) for boat, row in by_boat.items()})
        mawariashi_rank = rank_asc({boat: as_float(row["mawariashi_time"]) for boat, row in by_boat.items()})

        time_avgs: dict[str, float | None] = {}
        for field in ["tenji_time", "isshu_time", "chokusen_time", "hanshu_time", "mawariashi_time"]:
            vals = [as_float(row[field]) for row in group]
            time_avgs[field] = avg([v for v in vals if v is not None])
        combo_vals = []
        for row in group:
            tenji = as_float(row["tenji_time"])
            isshu = as_float(row["isshu_time"])
            if tenji is not None and isshu is not None:
                combo_vals.append(tenji + isshu)
        combo_avg = avg(combo_vals)

        for boat, row in by_boat.items():
            item: Row = {key: row[key] for key in row.keys()}
            item["race_id"] = race_id
            item["boat_number"] = boat
            item["place_name"] = row["place_name"]
            item["payout"] = payout
            item["is_manshu"] = int(payout >= 10000)
            item["in_top3"] = int(boat in top3 or as_int(row["finish"], 9) <= 3)
            item["is_win"] = int(as_int(row["finish"], 9) == 1)
            item["ai_plus"] = ai_plus.get(boat)
            item["ai_plus_rank"] = ai_plus_rank.get(boat)
            item["ai_prediction_rank"] = ai_prediction_rank.get(boat)
            item["tenji_rank_calc"] = tenji_rank.get(boat)
            item["isshu_rank_calc"] = isshu_rank.get(boat)
            item["chokusen_rank_calc"] = chokusen_rank.get(boat)
            item["hanshu_rank_calc"] = hanshu_rank.get(boat)
            item["mawariashi_rank_calc"] = mawariashi_rank.get(boat)
            for field, avg_value in time_avgs.items():
                value = as_float(row[field])
                item[f"{field.replace('_time', '')}_diff"] = (
                    round(avg_value - value, 4) if avg_value is not None and value is not None else None
                )
            tenji = as_float(row["tenji_time"])
            isshu = as_float(row["isshu_time"])
            item["combo_diff"] = (
                round(combo_avg - (tenji + isshu), 4)
                if combo_avg is not None and tenji is not None and isshu is not None
                else as_float(row["avg_isshu_diff"])
            )
            item["super_slit_alert"] = False
            item["super_slit_tenji_adv"] = None
            item["super_slit_st_rank_adv"] = None
            if boat > 1:
                left = by_boat[boat - 1]
                left_tenji = as_float(left["tenji_time"])
                own_tenji = as_float(row["tenji_time"])
                left_st = as_float(left["st_rank_general"])
                own_st = as_float(row["st_rank_general"])
                if left_tenji is not None and own_tenji is not None and left_st is not None and own_st is not None:
                    item["super_slit_tenji_adv"] = round(left_tenji - own_tenji, 4)
                    item["super_slit_st_rank_adv"] = round(left_st - own_st, 4)
                    item["super_slit_alert"] = item["super_slit_tenji_adv"] >= 0.10 and item["super_slit_st_rank_adv"] > 0
            records.append(item)
    return records


def build_dictionary(records: list[Row], min_n: int) -> list[Row]:
    baseline: dict[tuple[str, int], dict[str, int]] = defaultdict(lambda: {"n": 0, "win": 0, "top3": 0})
    groups: dict[tuple[str, int, str, str], dict[str, int]] = defaultdict(lambda: {"n": 0, "win": 0, "top3": 0})

    for row in records:
        place = str(row["place_name"])
        boat = int(row["boat_number"])
        for base_key in [(place, boat), ("__ALL__", boat)]:
            baseline[base_key]["n"] += 1
            baseline[base_key]["win"] += int(row["is_win"])
            baseline[base_key]["top3"] += int(row["in_top3"])
        for feature in FEATURES:
            binned = bin_value(feature, row.get(feature))
            if binned is None:
                continue
            for place_key in [place, "__ALL__"]:
                key = (place_key, boat, feature, binned)
                groups[key]["n"] += 1
                groups[key]["win"] += int(row["is_win"])
                groups[key]["top3"] += int(row["in_top3"])

    out: list[Row] = []
    for (place, boat, feature, bin_label), counts in groups.items():
        base = baseline[(place, boat)]
        if base["n"] <= 0:
            continue
        n = counts["n"]
        win_rate = counts["win"] / n * 100 if n else 0
        top3_rate = counts["top3"] / n * 100 if n else 0
        base_win = base["win"] / base["n"] * 100
        base_top3 = base["top3"] / base["n"] * 100
        win_delta = win_rate - base_win
        top3_delta = top3_rate - base_top3
        win_lift = win_rate / base_win if base_win > 0 else None
        top3_lift = top3_rate / base_top3 if base_top3 > 0 else None
        win_label = label_win(win_delta, win_lift, n, min_n)
        top3_label = label_top3(top3_delta, top3_lift, n, min_n)
        out.append(
            {
                "place_name": place,
                "boat_number": boat,
                "feature": feature,
                "feature_phase": FEATURES[feature]["phase"],
                "bin": bin_label,
                "n": n,
                "baseline_n": base["n"],
                "win_rate_pct": round(win_rate, 4),
                "baseline_win_rate_pct": round(base_win, 4),
                "win_delta_pp": round(win_delta, 4),
                "win_lift": round(win_lift, 4) if win_lift is not None else None,
                "win_label": win_label,
                "top3_rate_pct": round(top3_rate, 4),
                "baseline_top3_rate_pct": round(base_top3, 4),
                "top3_delta_pp": round(top3_delta, 4),
                "top3_lift": round(top3_lift, 4) if top3_lift is not None else None,
                "top3_label": top3_label,
                "overall_label": stronger_signal(win_label, top3_label),
                "score_head": round(clamp(win_delta, -15, 15) * FEATURES[feature]["weight"], 4),
                "score_axis": round(clamp(top3_delta, -20, 20) * FEATURES[feature]["weight"], 4),
            }
        )
    return sorted(out, key=lambda r: (str(r["place_name"]), int(r["boat_number"]), str(r["feature"]), str(r["bin"])))


def write_csv(path: Path, rows: list[Row]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    keys: list[str] = []
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def dictionary_lookup(dictionary_rows: list[Row]) -> dict[tuple[str, int, str, str], Row]:
    return {
        (str(row["place_name"]), int(row["boat_number"]), str(row["feature"]), str(row["bin"])): row
        for row in dictionary_rows
    }


def feature_entries_for_row(row: Row, lookup: dict[tuple[str, int, str, str], Row], phase: str) -> list[Row]:
    phases = {"morning"} if phase == "morning" else {"morning", "preview"}
    place = str(row["place_name"])
    boat = int(row["boat_number"])
    entries = []
    for feature, meta in FEATURES.items():
        if meta["phase"] not in phases:
            continue
        binned = bin_value(feature, row.get(feature))
        if binned is None:
            continue
        entry = lookup.get((place, boat, feature, binned)) or lookup.get(("__ALL__", boat, feature, binned))
        if entry:
            entries.append(entry)
    return entries


def score_boat(row: Row, lookup: dict[tuple[str, int, str, str], Row], phase: str) -> Row:
    entries = feature_entries_for_row(row, lookup, phase)
    head_score = 0.0
    axis_score = 0.0
    labels: list[str] = []
    reasons: list[str] = []
    for entry in entries:
        n = as_int(entry.get("n"))
        reliability = min(1.0, math.sqrt(max(n, 1) / 500))
        head_score += (as_float(entry.get("score_head")) or 0) * reliability
        axis_score += (as_float(entry.get("score_axis")) or 0) * reliability
        label = str(entry.get("overall_label"))
        if label in {"超バフ", "バフ", "デバフ", "超デバフ"}:
            labels.append(label)
            if len(reasons) < 5:
                reasons.append(f"{entry['feature']}={entry['bin']}:{label}")
    return {
        "boat_number": int(row["boat_number"]),
        "ai_plus_rank": row.get("ai_plus_rank"),
        "head_score": round(head_score, 4),
        "axis_score": round(axis_score, 4),
        "buff_count": sum(1 for label in labels if "バフ" in label and "デ" not in label),
        "debuff_count": sum(1 for label in labels if "デバフ" in label),
        "super_buff_count": labels.count("超バフ"),
        "super_debuff_count": labels.count("超デバフ"),
        "reasons": reasons,
    }


def unique(values: list[int | None]) -> list[int]:
    out: list[int] = []
    for value in values:
        if value is None:
            continue
        number = int(value)
        if number not in out:
            out.append(number)
    return out


def axis_pair(rows: list[Row]) -> list[int]:
    by_rank = {}
    for row in rows:
        rank = as_int(row.get("ai_plus_rank"), 0)
        if rank:
            by_rank[rank] = int(row["boat_number"])
    return unique([by_rank.get(2), by_rank.get(3), by_rank.get(1)])[:2]


def choose_roles(scored: list[Row], source_rows: list[Row]) -> Row:
    by_boat = {int(row["boat_number"]): row for row in scored}
    source_by_boat = {int(row["boat_number"]): row for row in source_rows}
    head_pool = [row for row in scored if int(row["boat_number"]) in {3, 4, 5, 6}]
    head_pool = sorted(head_pool, key=lambda r: (as_float(r["head_score"]) or -999, int(r["boat_number"]) in {5, 6}), reverse=True)
    heads = unique([as_int(row["boat_number"]) for row in head_pool[:2]])
    if len(heads) < 2:
        heads = unique(heads + [as_int(row["boat_number"]) for row in sorted(scored, key=lambda r: as_float(r["head_score"]) or -999, reverse=True)])[:2]
    axes = axis_pair(source_rows)
    excluded = set(heads + axes)
    toss_pool = [row for row in scored if int(row["boat_number"]) not in excluded]
    toss_pool = sorted(toss_pool, key=lambda r: (as_float(r["axis_score"]) or 999, -(as_float(r["debuff_count"]) or 0)))
    toss = int(toss_pool[0]["boat_number"]) if toss_pool else None
    support_pool = sorted(scored, key=lambda r: (as_float(r["axis_score"]) or -999, int(r["boat_number"]) in {5, 6}), reverse=True)
    supports = unique(axes + [5, 6] + [int(row["boat_number"]) for row in support_pool if int(row["boat_number"]) not in heads and int(row["boat_number"]) != toss])[:4]
    tickets = build_tickets(heads, axes, supports, toss, by_boat)
    buy = 10 <= len(tickets) <= 15
    if by_boat.get(1, {}).get("head_score", 0) > 10 and by_boat.get(1, {}).get("axis_score", 0) > 10:
        buy = False
    return {
        "decision": "買い" if buy else "見送り",
        "heads": heads,
        "axes": axes,
        "keshi": toss,
        "supports": supports,
        "tickets": tickets if buy else [],
        "points": len(tickets) if buy else 0,
        "reason": role_reason(heads, axes, toss, by_boat, source_by_boat),
    }


def ticket_score(ticket: str, scores: dict[int, Row]) -> float:
    a, b, c = [int(part) for part in ticket.split("-")]
    return (
        (as_float(scores.get(a, {}).get("head_score")) or 0) * 0.55
        + (as_float(scores.get(b, {}).get("axis_score")) or 0) * 0.25
        + (as_float(scores.get(c, {}).get("axis_score")) or 0) * 0.20
        + (2 if 5 in {a, b, c} or 6 in {a, b, c} else 0)
    )


def build_tickets(heads: list[int], axes: list[int], supports: list[int], toss: int | None, scores: dict[int, Row]) -> list[str]:
    pool = unique(axes + supports)
    if len(pool) < 4:
        pool = unique(pool + [1, 2, 3, 4, 5, 6])
    tickets = set()
    for head in heads:
        for second in pool:
            for third in pool:
                if len({head, second, third}) != 3:
                    continue
                if toss in {head, second, third}:
                    continue
                if not ({second, third} & set(axes)):
                    continue
                if not ({head, second, third} & {5, 6}):
                    continue
                tickets.add(f"{head}-{second}-{third}")
    ordered = sorted(tickets, key=lambda t: ticket_score(t, scores), reverse=True)
    if len(ordered) < 10:
        for head in heads:
            for second in pool:
                for third in unique(pool + axes):
                    if len({head, second, third}) == 3 and toss not in {head, second, third}:
                        ordered.append(f"{head}-{second}-{third}")
        ordered = sorted(set(ordered), key=lambda t: ticket_score(t, scores), reverse=True)
    return ordered[:15]


def role_reason(heads: list[int], axes: list[int], toss: int | None, scored: dict[int, Row], source_rows: dict[int, Row]) -> str:
    bits = []
    for boat in heads[:2]:
        reasons = scored.get(boat, {}).get("reasons") or []
        bits.append(f"{boat}頭:{'/'.join(reasons[:2]) if reasons else 'head_score上位'}")
    for boat in axes[:2]:
        rank = source_rows.get(boat, {}).get("ai_plus_rank")
        bits.append(f"{boat}軸:AI+{rank}位")
    if toss:
        reasons = scored.get(toss, {}).get("reasons") or []
        bits.append(f"{toss}消し:{'/'.join(reasons[:2]) if reasons else 'axis_score下位'}")
    return " / ".join(bits)


def score_race(group: list[Row], lookup: dict[tuple[str, int, str, str], Row], phase: str) -> Row:
    scored = [score_boat(row, lookup, phase) for row in group]
    by_boat = {int(row["boat_number"]): row for row in scored}
    b1 = by_boat.get(1, {})
    outer = [by_boat.get(5, {}), by_boat.get(6, {})]
    mids = [by_boat.get(3, {}), by_boat.get(4, {}), by_boat.get(5, {}), by_boat.get(6, {})]
    lane1_danger = max(0.0, -(as_float(b1.get("head_score")) or 0)) * 0.75 + max(0.0, -(as_float(b1.get("axis_score")) or 0)) * 0.35
    outer_signal = max([as_float(row.get("axis_score")) or -99 for row in outer] + [0])
    head_signal = max([as_float(row.get("head_score")) or -99 for row in mids] + [0])
    low_eval_signal = 0.0
    for source, score in zip(group, scored):
        if as_int(source.get("ai_plus_rank"), 0) >= 4:
            low_eval_signal = max(low_eval_signal, as_float(score.get("axis_score")) or 0, as_float(score.get("head_score")) or 0)
    super_slit_count = sum(1 for row in group if row.get("super_slit_alert"))
    buff_count = sum(as_int(row.get("buff_count")) for row in scored)
    debuff_count = sum(as_int(row.get("debuff_count")) for row in scored)
    race_score = 15 + lane1_danger * 0.65 + max(0, outer_signal) * 0.45 + max(0, head_signal) * 0.45 + max(0, low_eval_signal) * 0.35 + super_slit_count * 4 + buff_count * 0.20 + debuff_count * 0.10
    roles = choose_roles(scored, group)
    first = group[0]
    result = parse_result(first.get("winning_number3t1"))
    return {
        "race_id": first["race_id"],
        "date": first["date"],
        "place_name": first["place_name"],
        "round": int(first["round"]),
        "phase": phase,
        "buff_manshu_score": round(race_score, 4),
        "lane1_danger_score": round(lane1_danger, 4),
        "outer_signal_score": round(outer_signal, 4),
        "head_upset_score": round(head_signal, 4),
        "low_eval_signal_score": round(low_eval_signal, 4),
        "super_slit_count": super_slit_count,
        "decision": roles["decision"],
        "heads": roles["heads"],
        "axes": roles["axes"],
        "keshi": roles["keshi"],
        "supports": roles["supports"],
        "tickets": roles["tickets"],
        "points": roles["points"],
        "reason": roles["reason"],
        "payout": first.get("payout"),
        "is_manshu": int(first.get("is_manshu") or 0),
        "result": "-".join(map(str, result)) if result else "",
        "lane1_flying": int(bool(result) and result[0] != 1),
        "outer56_in_top3": int(bool(set(result).intersection({5, 6}))),
        "boat_scores": scored,
    }


def rank_records(records: list[Row], dictionary_rows: list[Row], phase: str, target_date: str | None = None) -> list[Row]:
    lookup = dictionary_lookup(dictionary_rows)
    by_race: dict[str, list[Row]] = defaultdict(list)
    for row in records:
        if target_date and row["date"] != target_date:
            continue
        by_race[str(row["race_id"])].append(row)
    races = [score_race(group, lookup, phase) for group in by_race.values() if len(group) == 6]
    return sorted(races, key=lambda row: (as_float(row["buff_manshu_score"]) or -999, row["date"]), reverse=True)


def topk_by_day(races: list[Row], k: int) -> list[Row]:
    by_date: dict[str, list[Row]] = defaultdict(list)
    for row in races:
        by_date[str(row["date"])].append(row)
    out = []
    for rows in by_date.values():
        out.extend(sorted(rows, key=lambda row: as_float(row["buff_manshu_score"]) or -999, reverse=True)[:k])
    return out


def summarize_selection(rows: list[Row], label: str) -> Row:
    n = len(rows)
    manshu = sum(as_int(row.get("is_manshu")) for row in rows)
    lane1_fly = sum(as_int(row.get("lane1_flying")) for row in rows)
    outer_top3 = sum(as_int(row.get("outer56_in_top3")) for row in rows)
    bought = [row for row in rows if row.get("decision") == "買い" and row.get("tickets")]
    purchase = sum(len(row.get("tickets") or []) * 100 for row in bought)
    returns = 0
    hits = 0
    manshu_hits = 0
    for row in bought:
        if row.get("result") in set(row.get("tickets") or []):
            hits += 1
            returns += as_int(row.get("payout"))
            manshu_hits += int(as_int(row.get("payout")) >= 10000)
    return {
        "selection": label,
        "races": n,
        "manshu": manshu,
        "manshu_rate_pct": round(manshu / n * 100, 4) if n else None,
        "lane1_fly_rate_pct": round(lane1_fly / n * 100, 4) if n else None,
        "outer56_top3_rate_pct": round(outer_top3 / n * 100, 4) if n else None,
        "buy_races": len(bought),
        "purchase_yen": purchase,
        "return_yen": returns,
        "roi_pct": round(returns / purchase * 100, 4) if purchase else None,
        "hit_rate_pct": round(hits / len(bought) * 100, 4) if bought else None,
        "manshu_hit_rate_pct": round(manshu_hits / len(bought) * 100, 4) if bought else None,
    }


def write_dictionary_report(path: Path, dictionary_rows: list[Row], train_records: list[Row]) -> None:
    notable = [
        row for row in dictionary_rows
        if row["place_name"] != "__ALL__" and row["overall_label"] in {"超バフ", "超デバフ"} and as_int(row["n"]) >= 120
    ]
    notable = sorted(notable, key=lambda row: abs(as_float(row["top3_delta_pp"]) or 0) + abs(as_float(row["win_delta_pp"]) or 0), reverse=True)
    lines = [
        "# 競艇場別 バフ/デバフ辞書",
        "",
        f"- 生成時刻: {JST_NOW}",
        f"- 学習艇データ数: {len(train_records):,}",
        f"- 辞書行数: {len(dictionary_rows):,}",
        "- 判定: 1着率と3着内率を、同じ競艇場・同じ号艇の基準値と比較。",
        "- 超バフ/超デバフは効果量とリフトが大きく、サンプル数が基準以上のもの。",
        "",
        "## 強いシグナル例",
        "",
        "| 場 | 艇 | データ | 数値帯 | 件数 | 1着差 | 3着内差 | 判定 |",
        "|---|---:|---|---|---:|---:|---:|---|",
    ]
    for row in notable[:40]:
        lines.append(
            f"| {row['place_name']} | {row['boat_number']} | {row['feature']} | {row['bin']} | {row['n']} | "
            f"{pp(as_float(row['win_delta_pp']))} | {pp(as_float(row['top3_delta_pp']))} | {row['overall_label']} |"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_backtest_report(path: Path, summaries: list[Row]) -> None:
    lines = [
        "# バフ辞書ランキング検証",
        "",
        f"- 生成時刻: {JST_NOW}",
        "- TOP5/TOP10は日別に選出。",
        "- 回収率は1点100円均等、的中した全払戻を含む。",
        "",
        "| phase | selection | races | 万舟率 | 1飛び率 | 5/6絡み | 買いR | 回収率 | 的中率 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summaries:
        lines.append(
            f"| {row['phase']} | {row['selection']} | {row['races']} | {pct(row.get('manshu_rate_pct'))} | "
            f"{pct(row.get('lane1_fly_rate_pct'))} | {pct(row.get('outer56_top3_rate_pct'))} | {row['buy_races']} | "
            f"{pct(row.get('roi_pct'))} | {pct(row.get('hit_rate_pct'))} |"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_outputs(args: argparse.Namespace) -> None:
    db_path = Path(args.db)
    conn = open_db(db_path)
    train_rows = query_rows(conn, start=args.train_start, end=args.train_end)
    train_records = build_race_records(train_rows)
    dictionary_rows = build_dictionary(train_records, min_n=args.min_n)
    model_payload = {
        "version": "buff-debuff-v1",
        "generated_at": JST_NOW,
        "source_db": str(db_path),
        "train_start": args.train_start,
        "train_end": args.train_end,
        "min_n": args.min_n,
        "feature_count": len(FEATURES),
        "rows": dictionary_rows,
    }
    write_json(Path(args.model_out), model_payload)
    write_csv(Path(args.dictionary_csv), dictionary_rows)
    write_dictionary_report(Path(args.dictionary_report), dictionary_rows, train_records)

    test_rows = query_rows(conn, start=args.test_start, end=args.test_end)
    test_records = build_race_records(test_rows)
    summaries: list[Row] = []
    detailed_rankings: dict[str, list[Row]] = {}
    for phase in ["morning", "preview"]:
        ranked = rank_records(test_records, dictionary_rows, phase)
        detailed_rankings[phase] = ranked[: args.keep_rank_rows]
        for k in [5, 10]:
            selected = topk_by_day(ranked, k)
            summary = summarize_selection(selected, f"top{k}_per_day")
            summary["phase"] = phase
            summaries.append(summary)
    write_csv(Path(args.backtest_csv), summaries)
    write_backtest_report(Path(args.backtest_report), summaries)
    write_json(
        Path(args.backtest_json),
        {
            "version": "buff-debuff-backtest-v1",
            "generated_at": JST_NOW,
            "train_start": args.train_start,
            "train_end": args.train_end,
            "test_start": args.test_start,
            "test_end": args.test_end,
            "summaries": summaries,
            "top_rankings": detailed_rankings,
        },
    )

    if args.rank_date:
        date_rows = query_rows(conn, start=args.rank_date, end=args.rank_date)
        date_records = build_race_records(date_rows)
        for phase in ["morning", "preview"]:
            ranked = rank_records(date_records, dictionary_rows, phase, target_date=args.rank_date)
            out = {
                "version": "buff-debuff-ranking-v1",
                "generated_at": JST_NOW,
                "date": args.rank_date,
                "phase": phase,
                "races": ranked[: args.top_n],
            }
            write_json(OUTPUT_DIR / f"buff_debuff_ranking_{phase}_{args.rank_date.replace('-', '')}.json", out)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--train-start", default="2022-01-01")
    parser.add_argument("--train-end", default="2025-12-31")
    parser.add_argument("--test-start", default="2026-01-01")
    parser.add_argument("--test-end", default="2026-06-18")
    parser.add_argument("--rank-date", default="2026-06-18")
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--keep-rank-rows", type=int, default=200)
    parser.add_argument("--min-n", type=int, default=80)
    parser.add_argument("--model-out", default=str(MODEL_DIR / "buff_debuff_dictionary.json"))
    parser.add_argument("--dictionary-csv", default=str(REPORT_DIR / "buff_debuff_dictionary.csv"))
    parser.add_argument("--dictionary-report", default=str(REPORT_DIR / "buff_debuff_dictionary.md"))
    parser.add_argument("--backtest-csv", default=str(REPORT_DIR / "buff_debuff_backtest_summary.csv"))
    parser.add_argument("--backtest-json", default=str(OUTPUT_DIR / "buff_debuff_backtest_summary.json"))
    parser.add_argument("--backtest-report", default=str(REPORT_DIR / "buff_debuff_backtest.md"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    build_outputs(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
