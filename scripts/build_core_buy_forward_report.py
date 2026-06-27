#!/usr/bin/env python3
"""Build a forward-validation ledger for the validated Codex core buy alert."""

from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "data" / "output"
REPORT_DIR = ROOT / "reports"
CORE_STRATEGY_ID = "codex_post_core_ab_rank3"
ALERT_RE = re.compile(r"boaters_manshu_alerts_(\d{8})\.json$")
RANKING_RE = re.compile(r"boaters_manshu_ranking_(\d{8})\.json$")


def iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def date_from_key(key: str) -> str:
    return f"{key[:4]}-{key[4:6]}-{key[6:8]}"


def key_from_date(date_text: str) -> str:
    return date_text.replace("-", "")


def as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def as_num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def compact_key(path: Path, pattern: re.Pattern[str]) -> str | None:
    match = pattern.match(path.name)
    return match.group(1) if match else None


def in_range(key: str, start_date: str | None, end_date: str | None) -> bool:
    if start_date and key < key_from_date(start_date):
        return False
    if end_date and key > key_from_date(end_date):
        return False
    return True


def normalize_combo(value: Any) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return "-".join(digits[:3]) if len(digits) >= 3 else ""


def result_of(row: dict[str, Any]) -> dict[str, Any]:
    result = row.get("result") or {}
    trifecta = normalize_combo(result.get("trifecta") or row.get("trifecta"))
    payout = as_int(result.get("payout_yen"))
    if payout is None:
        payout = as_int(row.get("payout_yen") if row.get("payout_yen") is not None else row.get("payout"))
    return {
        "trifecta": trifecta,
        "payout_yen": payout,
        "manshu": bool(payout is not None and payout >= 10000),
    }


def race_label(item: dict[str, Any]) -> str:
    return f"{item.get('place_name') or ''}{item.get('round') or item.get('round_no') or ''}R"


def strategy_ids_from_alert(alert: dict[str, Any]) -> list[str]:
    ids = []
    for strategy in alert.get("strategies") or []:
        strategy_id = strategy.get("strategy_id")
        if strategy_id:
            ids.append(str(strategy_id))
    selection_ids = ((alert.get("selection") or {}).get("source_strategy_ids")) or []
    for strategy_id in selection_ids:
        if strategy_id and strategy_id not in ids:
            ids.append(str(strategy_id))
    return ids


def strategy_ids_from_row(row: dict[str, Any]) -> list[str]:
    ids = [str(value) for value in row.get("last_minute_strategy_ids") or [] if value]
    for strategy_id in ((row.get("selection") or {}).get("source_strategy_ids")) or []:
        if strategy_id and strategy_id not in ids:
            ids.append(str(strategy_id))
    return ids


def tickets_from_item(item: dict[str, Any]) -> list[str]:
    selection = item.get("selection") or {}
    tickets = [normalize_combo(ticket) for ticket in selection.get("tickets") or []]
    if not tickets:
        for strategy in item.get("strategies") or []:
            tickets.extend(normalize_combo(ticket) for ticket in strategy.get("tickets") or [])
    return sorted({ticket for ticket in tickets if ticket})


def result_index(start_date: str | None, end_date: str | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(OUTPUT_DIR.glob("boaters_manshu_ranking_*.json")):
        if path.name.startswith("boaters_manshu_ranking_codex_"):
            continue
        key = compact_key(path, RANKING_RE)
        if not key or not in_range(key, start_date, end_date):
            continue
        payload = load_json(path)
        for group_name in ("races", "strict_races", "morning_candidates"):
            for row in payload.get(group_name) or []:
                race_id = row.get("race_id")
                if not race_id:
                    continue
                result = result_of(row)
                if result["payout_yen"] is None and race_id in out:
                    continue
                out[str(race_id)] = {
                    "date": row.get("date") or payload.get("date") or date_from_key(key),
                    "race_id": race_id,
                    "place_name": row.get("place_name"),
                    "round": row.get("round") or row.get("round_no"),
                    "result": result,
                }
    return out


def add_candidate(candidates: dict[str, dict[str, Any]], source: dict[str, Any], source_type: str, date_text: str) -> None:
    race_id = source.get("race_id")
    if not race_id:
        return
    strategy_ids = strategy_ids_from_alert(source) if source_type == "alert" else strategy_ids_from_row(source)
    if CORE_STRATEGY_ID not in strategy_ids:
        return
    tickets = tickets_from_item(source)
    if not tickets:
        return
    key = f"{date_text}:{race_id}:{CORE_STRATEGY_ID}"
    previous = candidates.get(key)
    if previous and previous.get("source_type") == "alert":
        return
    candidates[key] = {
        "date": source.get("date") or date_text,
        "race_id": race_id,
        "place_name": source.get("place_name"),
        "round": source.get("round") or source.get("round_no"),
        "deadline_time": source.get("deadline_time"),
        "rank": source.get("rank"),
        "morning_rank": source.get("morning_rank"),
        "live_rank": source.get("live_rank"),
        "manshu_rate_pct": as_num(source.get("manshu_rate_pct")),
        "alert_type": source.get("alert_type") or source.get("last_minute_alert_type"),
        "strategy_id": CORE_STRATEGY_ID,
        "source_type": source_type,
        "heads": (source.get("selection") or {}).get("heads") or [],
        "axes": (source.get("selection") or {}).get("axes") or [],
        "keshi": (source.get("selection") or {}).get("keshi"),
        "tickets": tickets,
        "points": len(tickets),
    }


def collect_candidates(start_date: str | None, end_date: str | None) -> dict[str, dict[str, Any]]:
    candidates: dict[str, dict[str, Any]] = {}
    for path in sorted(OUTPUT_DIR.glob("boaters_manshu_alerts_*.json")):
        key = compact_key(path, ALERT_RE)
        if not key or not in_range(key, start_date, end_date):
            continue
        payload = load_json(path)
        date_text = payload.get("date") or date_from_key(key)
        for alert in payload.get("alerts") or []:
            add_candidate(candidates, alert, "alert", date_text)
    for path in sorted(OUTPUT_DIR.glob("boaters_manshu_ranking_*.json")):
        if path.name.startswith("boaters_manshu_ranking_codex_"):
            continue
        key = compact_key(path, RANKING_RE)
        if not key or not in_range(key, start_date, end_date):
            continue
        payload = load_json(path)
        date_text = payload.get("date") or date_from_key(key)
        for group_name in ("races", "strict_races", "morning_candidates"):
            for row in payload.get(group_name) or []:
                add_candidate(candidates, row, group_name, date_text)
    return candidates


def summarize(picks: list[dict[str, Any]]) -> dict[str, Any]:
    settled = [pick for pick in picks if pick.get("result_payout_yen") is not None]
    buys = [pick for pick in settled if pick.get("points", 0) > 0]
    investment = sum((as_int(pick.get("points")) or 0) * 100 for pick in buys)
    payback = sum(as_int(pick.get("payback_yen")) or 0 for pick in buys)
    hits = [pick for pick in buys if pick.get("hit")]
    manshu_hits = [pick for pick in hits if (as_int(pick.get("result_payout_yen")) or 0) >= 10000]
    pending = [pick for pick in picks if pick.get("result_payout_yen") is None]
    return {
        "candidate_count": len(picks),
        "settled_count": len(settled),
        "pending_count": len(pending),
        "buy_races": len(buys),
        "total_points": sum(as_int(pick.get("points")) or 0 for pick in buys),
        "investment_yen": investment,
        "payback_yen": payback,
        "profit_yen": payback - investment,
        "roi_pct": round(payback / investment * 100, 2) if investment else None,
        "hit_count": len(hits),
        "hit_rate_pct": round(len(hits) / len(buys) * 100, 2) if buys else None,
        "manshu_hit_count": len(manshu_hits),
        "manshu_hit_rate_pct": round(len(manshu_hits) / len(buys) * 100, 2) if buys else None,
        "max_hit_payout_yen": max([as_int(pick.get("result_payout_yen")) or 0 for pick in hits], default=0) or None,
        "latest_candidate_date": picks[-1]["date"] if picks else None,
    }


def build(start_date: str | None, end_date: str | None) -> dict[str, Any]:
    candidates = collect_candidates(start_date, end_date)
    results = result_index(start_date, end_date)
    picks: list[dict[str, Any]] = []
    for item in sorted(candidates.values(), key=lambda row: (str(row.get("date")), str(row.get("deadline_time")), str(row.get("race_id")))):
        result_row = results.get(str(item.get("race_id"))) or {}
        result = result_row.get("result") or {}
        trifecta = result.get("trifecta") or ""
        hit = bool(trifecta and trifecta in item.get("tickets", []))
        payout = as_int(result.get("payout_yen"))
        item["race"] = race_label(item)
        item["result_trifecta"] = trifecta or None
        item["result_payout_yen"] = payout
        item["result_manshu"] = bool(payout is not None and payout >= 10000)
        item["hit"] = hit
        item["payback_yen"] = payout if hit and payout is not None else 0
        item["investment_yen"] = (as_int(item.get("points")) or 0) * 100 if payout is not None else 0
        item["profit_yen"] = item["payback_yen"] - item["investment_yen"] if payout is not None else None
        picks.append(item)
    return {
        "version": "codex-core-buy-forward-v1",
        "generated_at": iso_now(),
        "strategy_id": CORE_STRATEGY_ID,
        "strategy_label": "Codex直前本命: 朝TOP3+1AI30未満+外上昇A/B",
        "start_date": start_date,
        "end_date": end_date,
        "summary": summarize(picks),
        "picks": picks,
    }


def write_csv(path: Path, picks: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "date",
        "race",
        "race_id",
        "alert_type",
        "manshu_rate_pct",
        "heads",
        "axes",
        "keshi",
        "points",
        "tickets",
        "result_trifecta",
        "result_payout_yen",
        "hit",
        "investment_yen",
        "payback_yen",
        "profit_yen",
    ]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for pick in picks:
            row = {field: pick.get(field) for field in fields}
            row["heads"] = ",".join(map(str, pick.get("heads") or []))
            row["axes"] = ",".join(map(str, pick.get("axes") or []))
            row["tickets"] = " ".join(pick.get("tickets") or [])
            writer.writerow(row)


def write_markdown(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    summary = payload.get("summary") or {}
    roi_text = "--" if summary.get("roi_pct") is None else f"{summary.get('roi_pct')}%"
    lines = [
        "# Codex直前本命 前向き検証",
        "",
        f"- 戦略: `{payload.get('strategy_id')}`",
        f"- 期間: {payload.get('start_date') or '-'} 〜 {payload.get('end_date') or '-'}",
        f"- 候補: {summary.get('candidate_count')}R / 確定: {summary.get('settled_count')}R / 未確定: {summary.get('pending_count')}R",
        f"- 購入: {summary.get('buy_races')}R / {summary.get('total_points')}点 / {summary.get('investment_yen'):,}円",
        f"- 払戻: {summary.get('payback_yen'):,}円 / 収支: {summary.get('profit_yen'):,}円 / 回収率: {roi_text}",
        f"- 的中: {summary.get('hit_count')}R / 万舟的中: {summary.get('manshu_hit_count')}R",
        "",
        "| 日付 | レース | 万舟率 | 頭 | 軸 | 消し | 点数 | 結果 | 配当 | 的中 |",
        "| --- | --- | ---: | --- | --- | --- | ---: | --- | ---: | --- |",
    ]
    for pick in payload.get("picks") or []:
        lines.append(
            "| {date} | {race} | {rate} | {heads} | {axes} | {keshi} | {points} | {result} | {payout} | {hit} |".format(
                date=pick.get("date") or "",
                race=pick.get("race") or "",
                rate="" if pick.get("manshu_rate_pct") is None else pick.get("manshu_rate_pct"),
                heads=",".join(map(str, pick.get("heads") or [])),
                axes=",".join(map(str, pick.get("axes") or [])),
                keshi="" if pick.get("keshi") is None else pick.get("keshi"),
                points=pick.get("points") or 0,
                result=pick.get("result_trifecta") or "未確定",
                payout="" if pick.get("result_payout_yen") is None else f"{pick.get('result_payout_yen'):,}",
                hit="○" if pick.get("hit") else "",
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", default="2026-06-19")
    parser.add_argument("--end-date")
    parser.add_argument("--out-json", default=str(OUTPUT_DIR / "core_buy_forward_validation.json"))
    parser.add_argument("--out-csv", default=str(OUTPUT_DIR / "core_buy_forward_validation.csv"))
    parser.add_argument("--out-md", default=str(REPORT_DIR / "core_buy_forward_validation.md"))
    args = parser.parse_args()
    payload = build(args.start_date, args.end_date)
    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    write_csv(Path(args.out_csv), payload.get("picks") or [])
    write_markdown(Path(args.out_md), payload)
    print(json.dumps({"out": str(out_json), "candidates": payload["summary"]["candidate_count"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
