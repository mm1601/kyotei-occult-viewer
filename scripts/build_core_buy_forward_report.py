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
BUY_STRATEGY_IDS = {
    "codex_odds_gap_b1_fade_strong12",
    "codex_odds_gap_b1_fade_filtered12",
    "codex_post_core_front_head2_no1_outer56",
    "codex_post_core_rate40",
    "codex_post_subcore_rate38_conditions",
}
STRATEGY_LABELS = {
    "codex_odds_gap_b1_fade_strong12": "強本命: 1号艇人気の歪み+展示Wデバフ 12点",
    "codex_odds_gap_b1_fade_filtered12": "本命: 1号艇人気の歪み+前半展示弱化 12点",
    "codex_post_core_front_head2_no1_outer56": "本命絞り: 前半1〜3R+外頭2番手+1号艇消し+5/6絡み",
    "codex_post_core_rate40": "本命参考: 展示後40%以上 外頭2艇+AI+一般2位3位軸 12点",
    "codex_post_subcore_rate38_conditions": "準本命: 38〜39.9%+1危険+外頭2艇+内軸残り 12点",
}
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
    for strategy_id in row.get("last_minute_candidate_strategy_ids") or []:
        if strategy_id and strategy_id not in ids:
            ids.append(str(strategy_id))
    for strategy_id in ((row.get("selection") or {}).get("source_strategy_ids")) or []:
        if strategy_id and strategy_id not in ids:
            ids.append(str(strategy_id))
    return ids


def matching_strategy(item: dict[str, Any], strategy_id: str) -> dict[str, Any]:
    for strategy in item.get("strategies") or []:
        if (strategy or {}).get("strategy_id") == strategy_id:
            return strategy or {}
    selection = item.get("selection") or {}
    if strategy_id in (selection.get("source_strategy_ids") or []):
        return selection
    if strategy_id in (item.get("last_minute_candidate_strategy_ids") or []):
        return selection
    return {}


def tickets_from_item(item: dict[str, Any], strategy_id: str | None = None) -> list[str]:
    if strategy_id:
        strategy = matching_strategy(item, strategy_id)
        tickets = [normalize_combo(ticket) for ticket in strategy.get("tickets") or []]
        if tickets:
            return sorted({ticket for ticket in tickets if ticket})
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
    for strategy_id in sorted(set(strategy_ids) & BUY_STRATEGY_IDS):
        strategy = matching_strategy(source, strategy_id)
        selection = source.get("selection") or {}
        tickets = tickets_from_item(source, strategy_id)
        if not tickets:
            continue
        key = f"{date_text}:{race_id}:{strategy_id}"
        previous = candidates.get(key)
        if previous and previous.get("source_type") == "alert":
            continue
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
            "strategy_id": strategy_id,
            "strategy_label": STRATEGY_LABELS.get(strategy_id) or strategy.get("label") or selection.get("label") or strategy_id,
            "source_type": source_type,
            "heads": strategy.get("heads") or selection.get("heads") or [],
            "axes": strategy.get("axes") or selection.get("axes") or [],
            "keshi": strategy.get("keshi") if strategy.get("keshi") is not None else selection.get("keshi"),
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


def summarize_by_strategy(picks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for pick in picks:
        groups.setdefault(str(pick.get("strategy_id") or ""), []).append(pick)
    out = []
    for strategy_id in sorted(groups):
        summary = summarize(groups[strategy_id])
        summary["strategy_id"] = strategy_id
        summary["strategy_label"] = STRATEGY_LABELS.get(strategy_id, strategy_id)
        out.append(summary)
    return out


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
        "strategy_ids": sorted(BUY_STRATEGY_IDS),
        "strategy_label": "Codex本命系: 展示後40%・準本命38%・1号艇人気歪みを戦略別に前向き検証",
        "start_date": start_date,
        "end_date": end_date,
        "summary": summarize(picks),
        "by_strategy": summarize_by_strategy(picks),
        "picks": picks,
    }


def write_csv(path: Path, picks: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "date",
        "race",
        "race_id",
        "strategy_id",
        "strategy_label",
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
        writer = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
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
        f"- 戦略: {payload.get('strategy_label')}",
        f"- 期間: {payload.get('start_date') or '-'} 〜 {payload.get('end_date') or '-'}",
        f"- 候補: {summary.get('candidate_count')}R / 確定: {summary.get('settled_count')}R / 未確定: {summary.get('pending_count')}R",
        f"- 購入: {summary.get('buy_races')}R / {summary.get('total_points')}点 / {summary.get('investment_yen'):,}円",
        f"- 払戻: {summary.get('payback_yen'):,}円 / 収支: {summary.get('profit_yen'):,}円 / 回収率: {roi_text}",
        f"- 的中: {summary.get('hit_count')}R / 万舟的中: {summary.get('manshu_hit_count')}R",
        "",
        "## 戦略別",
        "",
        "| 戦略 | 候補 | 確定 | 購入額 | 払戻 | 回収率 | 的中 | 万舟的中 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in payload.get("by_strategy") or []:
        strategy_roi = "--" if row.get("roi_pct") is None else f"{row.get('roi_pct')}%"
        lines.append(
            "| {label} | {candidate} | {settled} | {investment} | {payback} | {roi} | {hits} | {manshu_hits} |".format(
                label=row.get("strategy_label") or row.get("strategy_id") or "",
                candidate=row.get("candidate_count") or 0,
                settled=row.get("settled_count") or 0,
                investment=f"{row.get('investment_yen') or 0:,}円",
                payback=f"{row.get('payback_yen') or 0:,}円",
                roi=strategy_roi,
                hits=row.get("hit_count") or 0,
                manshu_hits=row.get("manshu_hit_count") or 0,
            )
        )
    lines.extend(
        [
            "",
            "## 候補一覧",
            "",
            "| 日付 | レース | 戦略 | 万舟率 | 頭 | 軸 | 消し | 点数 | 結果 | 配当 | 的中 |",
            "| --- | --- | --- | ---: | --- | --- | --- | ---: | --- | ---: | --- |",
        ]
    )
    for pick in payload.get("picks") or []:
        lines.append(
            "| {date} | {race} | {strategy} | {rate} | {heads} | {axes} | {keshi} | {points} | {result} | {payout} | {hit} |".format(
                date=pick.get("date") or "",
                race=pick.get("race") or "",
                strategy=STRATEGY_LABELS.get(pick.get("strategy_id"), pick.get("strategy_id") or ""),
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
