#!/usr/bin/env python3
"""Export 2026 24-venue sign history for the public dashboard."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--details-csv", required=True)
    parser.add_argument("--rules", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--forward-dir", default="")
    parser.add_argument("--live-ranking-dir", default="")
    parser.add_argument("--approved-root", default="")
    parser.add_argument("--start-date", default="2026-01-01")
    parser.add_argument("--end-date", default=date.today().isoformat())
    parser.add_argument(
        "--out",
        default=str(ROOT / "data" / "output" / "manshu_system_history_2026.json"),
    )
    return parser.parse_args()


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def as_num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def as_int(value: Any) -> int | None:
    number = as_num(value)
    return int(number) if number is not None else None


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def int_list(value: Any) -> list[int]:
    if isinstance(value, list):
        items = value
    else:
        items = str(value or "").replace("/", ",").split(",")
    result: list[int] = []
    for item in items:
        number = as_int(item)
        if number is not None and 1 <= number <= 6 and number not in result:
            result.append(number)
    return result


def tickets(value: Any) -> list[str]:
    if isinstance(value, list):
        items = value
    else:
        items = str(value or "").replace(",", " ").split()
    result: list[str] = []
    for item in items:
        boats = [char for char in str(item) if char in "123456"]
        if len(boats) == 3:
            result.append("-".join(boats))
    return result


def trifecta(value: Any) -> str:
    boats = [char for char in str(value or "") if char in "123456"]
    return "-".join(boats[:3]) if len(boats) >= 3 else ""


def rule_map(path: Path) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    payload = read_json(path, {})
    result: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for rule in payload.get("rules") or []:
        key = (
            str(rule.get("venue") or ""),
            str(rule.get("base_id") or ""),
            str(rule.get("context_id") or ""),
            str(rule.get("template_id") or ""),
        )
        result[key] = rule
    return result


def fallback_condition(row: dict[str, Any]) -> str:
    base_id = str(row.get("base_id") or "")
    context_id = str(row.get("context_id") or "")
    base_label = base_id
    match = re.fullmatch(r"pop([12])_odds(\d+)_nige(\d+)", base_id)
    if match:
        rank, odds, escape = match.groups()
        base_label = f"1号艇がBOATERS人気{rank}位以内・支持率{odds}%以上・逃げ率{escape}%以下"
    match = re.fullmatch(r"pop1_aiwin(\d+)", base_id)
    if match:
        base_label = f"1号艇がBOATERS人気1位だがAI1着率{match.group(1)}%以下"
    match = re.fullmatch(r"pop1_overbet(\d+)", base_id)
    if match:
        base_label = f"1号艇がBOATERS人気1位で市場支持がAI1着率より{match.group(1)}pt以上高い"
    context_labels = {
        "b1lap4": "1号艇の一周/半周順位4位以下",
        "outertop2_1": "3-6号艇に展示/周回2位以内が1艇以上",
        "outer2_b1tenji4": "外艇上位2艇以上かつ1号艇展示4位以下",
    }
    return f"{base_label} / {context_labels.get(context_id, context_id)}"


def fallback_buy_method(row: dict[str, Any]) -> str:
    template_labels = {
        "non1_h1_b1place_top4": "2-6号艇の頭1艇・1号艇は2/3着可・予測順位上位4点",
        "head56_h1_b1place_top11": "5/6号艇の頭1艇・1号艇は2/3着可・予測順位上位11点",
        "head56_h1_b1place_top7": "5/6号艇の頭1艇・1号艇は2/3着可・予測順位上位7点",
    }
    template_id = str(row.get("template_id") or "")
    return template_labels.get(template_id, f"保存済み買い方（{template_id}）")


def chunks(values: list[str], size: int = 400) -> list[list[str]]:
    return [values[start : start + size] for start in range(0, len(values), size)]


def probability_rows(boats: Any) -> list[dict[str, Any]]:
    if isinstance(boats, dict):
        boat_items = []
        for boat_number, values in boats.items():
            if isinstance(values, dict):
                boat_items.append({"boat_number": boat_number, **values})
    elif isinstance(boats, list):
        boat_items = boats
    else:
        boat_items = []
    rows = []
    for boat in boat_items:
        boat_number = as_int(boat.get("boat_number"))
        if boat_number is None:
            continue
        rows.append(
            {
                "boat": boat_number,
                "win_pct": as_num(
                    boat.get("ai_prediction_pct", boat.get("win_pct"))
                ),
                "top3_pct": as_num(boat.get("ai_3ren_pct", boat.get("top3_pct"))),
                "general_top3_pct": as_num(boat.get("general_3ren_pct")),
                "source": "original_boaters",
            }
        )
    return sorted(rows, key=lambda row: row["boat"])


def venue_round_key(venue: Any, round_number: Any) -> str:
    round_value = as_int(round_number)
    return f"{venue}:{round_value:02d}" if venue and round_value is not None else ""


def probabilities_for_entry(
    probabilities: dict[str, list[dict[str, Any]]], entry: dict[str, Any]
) -> list[dict[str, Any]]:
    race_id = str(entry.get("race_id") or "")
    venue_key = venue_round_key(entry.get("place_name"), entry.get("round"))
    return probabilities.get(race_id) or probabilities.get(venue_key) or []


def database_probability_map(
    db_path: Path, race_ids: list[str]
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        for group in chunks(sorted(set(race_ids))):
            placeholders = ",".join("?" for _ in group)
            sql = f"""
                SELECT race_id, boat_number, ai_prediction_pct,
                       ai_3ren_pct, general_3ren_pct
                FROM v_race_boats_with_official_aux
                WHERE race_id IN ({placeholders}) AND is_absent = 0
                ORDER BY race_id, boat_number
            """
            for raw in connection.execute(sql, group):
                row = dict(raw)
                grouped[str(row["race_id"])].append(row)
    finally:
        connection.close()
    return {
        race_id: probability_rows(boats)
        for race_id, boats in grouped.items()
        if len(boats) == 6
    }


def ranking_probability_map(path: Path) -> dict[str, list[dict[str, Any]]]:
    payload = read_json(path, {})
    result = {}
    for race in payload.get("races") or []:
        rows = probability_rows(((race.get("metrics") or {}).get("boats") or []))
        if len(rows) == 6:
            race_id = str(race.get("race_id") or "")
            if race_id:
                result[race_id] = rows
            key = venue_round_key(
                race.get("place_name") or race.get("venue"),
                race.get("round") or race.get("race_number"),
            )
            if key:
                result[key] = rows
    return result


def approved_probability_map(
    approved_root: Path, date_key: str
) -> dict[str, list[dict[str, Any]]]:
    result = {}
    for path in sorted((approved_root / date_key).glob("*.json")):
        payload = read_json(path, {})
        rows = probability_rows(payload.get("boats") or {})
        key = venue_round_key(payload.get("place_name"), payload.get("round"))
        if key and len(rows) == 6:
            result[key] = rows
    return result


def condition_fields(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "wind_speed": as_num(row.get("wind_speed")),
        "wave_height": as_num(row.get("wave_height")),
        "boat1_popularity_pct": as_num(row.get("b1_odds_pct")),
        "boat1_ai_win_pct": as_num(row.get("b1_ai_win")),
        "boat1_ai_top3_pct": as_num(row.get("b1_ai_top3")),
        "boat1_escape_pct": as_num(row.get("b1_nige_pct")),
        "boat1_exhibition_rank": as_int(row.get("b1_tenji_rank")),
        "boat1_lap_rank": as_int(row.get("b1_lap_rank")),
        "boat1_average_diff": as_num(row.get("b1_avg_diff")),
        "outer56_average_diff": as_num(
            row.get("outer56_avg_diff", row.get("outer56_average_diff"))
        ),
        "outer_top2_count": as_int(row.get("outer_top2_count")),
        "outer_top1_count": as_int(row.get("outer_top1_count")),
    }


def historical_signal(
    row: dict[str, Any],
    rules: dict[tuple[str, str, str, str], dict[str, Any]],
    probabilities: list[dict[str, Any]],
) -> dict[str, Any]:
    venue = str(row.get("place_name") or "")
    key = (
        venue,
        str(row.get("base_id") or ""),
        str(row.get("context_id") or ""),
        str(row.get("template_id") or ""),
    )
    rule = rules.get(key) or {}
    hit = as_bool(row.get("hit"))
    payout = as_int(row.get("payout_yen"))
    return {
        "date": row.get("date"),
        "race_id": row.get("race_id"),
        "venue": venue,
        "round": as_int(row.get("round")),
        "record_kind": "historical_backtest",
        "source_label": "過去検証",
        "status": "settled",
        "notification_status": "historical",
        "rule_status": row.get("rule_status"),
        "condition": rule.get("condition") or fallback_condition(row),
        "buy_method": rule.get("buy_method") or fallback_buy_method(row),
        "data_mode": row.get("data_mode"),
        "points": as_int(row.get("points")),
        "tickets": tickets(row.get("tickets")),
        "heads": int_list(row.get("heads")),
        "axes": int_list(row.get("axes")),
        "keshi": as_int(row.get("keshi")),
        "historical": rule.get("historical") or {},
        "probabilities": probabilities,
        "conditions": condition_fields(row),
        "result": {
            "trifecta": trifecta(row.get("result")),
            "payout_yen": payout,
            "manshu": bool(payout is not None and payout >= 10000),
            "hit": hit,
        },
        "hit": hit,
        "investment_yen": as_int(row.get("investment_yen")) or 0,
        "payback_yen": as_int(row.get("payback_yen")) or 0,
        "profit_yen": as_int(row.get("profit_yen")) or 0,
    }


def forward_signal(
    entry: dict[str, Any], probabilities: list[dict[str, Any]]
) -> dict[str, Any]:
    snapshot = entry.get("condition_snapshot") or {}
    raw_result = entry.get("result") or {}
    payout = as_int(raw_result.get("payout_yen"))
    return {
        "date": entry.get("date"),
        "race_id": entry.get("race_id"),
        "venue": entry.get("place_name"),
        "round": as_int(entry.get("round")),
        "record_kind": "forward_live",
        "source_label": "実運用ログ",
        "status": entry.get("status"),
        "notification_status": entry.get("notification_status"),
        "notification_ok": entry.get("notification_ok"),
        "detected_at": entry.get("detected_at"),
        "deadline_time": entry.get("deadline_time"),
        "first_minutes_to_deadline": as_num(entry.get("first_minutes_to_deadline")),
        "rule_status": entry.get("rule_status"),
        "condition": entry.get("condition"),
        "buy_method": entry.get("buy_method"),
        "data_mode": entry.get("data_mode"),
        "points": as_int(entry.get("points")),
        "tickets": tickets(entry.get("tickets")),
        "heads": int_list(entry.get("heads")),
        "axes": int_list(entry.get("axes")),
        "keshi": as_int(entry.get("keshi")),
        "historical": entry.get("historical") or {},
        "probabilities": probabilities,
        "conditions": condition_fields(snapshot),
        "result": {
            "trifecta": trifecta(raw_result.get("trifecta")),
            "payout_yen": payout,
            "manshu": bool(payout is not None and payout >= 10000),
            "hit": raw_result.get("hit"),
        },
        "hit": raw_result.get("hit"),
        "investment_yen": as_int(raw_result.get("investment_yen")) or 0,
        "payback_yen": as_int(raw_result.get("payback_yen")) or 0,
        "profit_yen": as_int(raw_result.get("profit_yen")),
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    settled = [row for row in rows if (row.get("result") or {}).get("trifecta")]
    investment = sum(as_int(row.get("investment_yen")) or 0 for row in settled)
    payback = sum(as_int(row.get("payback_yen")) or 0 for row in settled)
    hits = sum(bool(row.get("hit")) for row in settled)
    return {
        "signals": len(rows),
        "settled": len(settled),
        "hits": hits,
        "hit_rate_pct": round(hits / len(settled) * 100, 3) if settled else None,
        "investment_yen": investment,
        "payback_yen": payback,
        "profit_yen": payback - investment if settled else None,
        "roi_pct": round(payback / investment * 100, 3) if investment else None,
    }


def date_range(start: date, end: date) -> list[date]:
    result: list[date] = []
    current = start
    while current <= end:
        result.append(current)
        current += timedelta(days=1)
    return result


def main() -> int:
    args = parse_args()
    start = date.fromisoformat(args.start_date)
    end = date.fromisoformat(args.end_date)
    rules = rule_map(Path(args.rules))

    source_rows: list[dict[str, Any]] = []
    with Path(args.details_csv).open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            row_date = str(row.get("date") or "")
            if args.start_date <= row_date <= args.end_date:
                source_rows.append(row)

    database_probabilities = database_probability_map(
        Path(args.db), [str(row.get("race_id") or "") for row in source_rows]
    )
    historical = [
        historical_signal(
            row,
            rules,
            database_probabilities.get(str(row.get("race_id") or ""), []),
        )
        for row in source_rows
    ]

    historical.sort(key=lambda row: (row.get("date") or "", row.get("race_id") or ""))
    complete_through = max((row["date"] for row in historical), default=args.start_date)
    known_ids = {row.get("race_id") for row in historical}

    forward_dates: set[str] = set()
    forward_rows: list[dict[str, Any]] = []
    if args.forward_dir:
        for path in sorted(Path(args.forward_dir).glob("original_boaters_24_shadow_????????.json")):
            payload = read_json(path, {})
            log_date = str(payload.get("date") or "")
            if not (args.start_date <= log_date <= args.end_date):
                continue
            forward_dates.add(log_date)
            live_probabilities = {}
            if args.live_ranking_dir:
                ranking_path = (
                    Path(args.live_ranking_dir)
                    / f"boaters_manshu_live_ranking_{log_date.replace('-', '')}.json"
                )
                live_probabilities = ranking_probability_map(ranking_path)
            if args.approved_root:
                live_probabilities.update(
                    approved_probability_map(
                        Path(args.approved_root), log_date.replace("-", "")
                    )
                )
            for entry in payload.get("entries") or []:
                if entry.get("race_id") in known_ids:
                    continue
                signal = forward_signal(
                    entry,
                    probabilities_for_entry(live_probabilities, entry),
                )
                forward_rows.append(signal)
                known_ids.add(signal.get("race_id"))

    signals = sorted(
        historical + forward_rows,
        key=lambda row: (row.get("date") or "", row.get("race_id") or ""),
    )
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_month: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in signals:
        by_day[str(row.get("date") or "")].append(row)
        by_month[str(row.get("date") or "")[:7]].append(row)

    days: dict[str, dict[str, Any]] = {}
    unavailable_dates: list[str] = []
    for day in date_range(start, end):
        day_text = day.isoformat()
        if day_text <= complete_through:
            status = "historical_complete"
        elif day_text in forward_dates:
            status = "forward_complete"
        else:
            status = "unavailable"
            unavailable_dates.append(day_text)
        days[day_text] = {"status": status, **summarize(by_day.get(day_text, []))}

    months: list[dict[str, Any]] = []
    month_cursor = start.replace(day=1)
    while month_cursor <= end:
        month = month_cursor.strftime("%Y-%m")
        month_days = [key for key in days if key.startswith(month)]
        months.append(
            {
                "month": month,
                **summarize(by_month.get(month, [])),
                "unavailable_days": sum(days[key]["status"] == "unavailable" for key in month_days),
            }
        )
        if month_cursor.month == 12:
            month_cursor = month_cursor.replace(year=month_cursor.year + 1, month=1)
        else:
            month_cursor = month_cursor.replace(month=month_cursor.month + 1)

    payload = {
        "version": "manshu-system-history-v1",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "period": {"start": args.start_date, "end": args.end_date},
        "coverage": {
            "historical_complete_through": complete_through,
            "forward_logged_dates": sorted(forward_dates),
            "unavailable_dates": unavailable_dates,
        },
        "totals": {
            "all": summarize(signals),
            "historical_backtest": summarize(historical),
            "forward_live": summarize(forward_rows),
        },
        "months": months,
        "days": days,
        "signals": signals,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "ok": True,
                "out": str(out),
                "historical_signals": len(historical),
                "forward_signals": len(forward_rows),
                "unavailable_dates": len(unavailable_dates),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
