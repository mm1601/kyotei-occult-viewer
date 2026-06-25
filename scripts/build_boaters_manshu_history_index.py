#!/usr/bin/env python3
"""Build the static history index for Codex manshu ranking result pages."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "data" / "output"
RANKING_RE = re.compile(r"boaters_manshu_ranking_(\d{8})\.json$")


def iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def date_from_key(key: str) -> str:
    return f"{key[:4]}-{key[4:6]}-{key[6:8]}"


def key_from_date(date_text: str) -> str:
    return date_text.replace("-", "")


def as_num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def as_int(value: Any) -> int | None:
    number = as_num(value)
    return int(number) if number is not None else None


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def result_of(row: dict[str, Any]) -> dict[str, Any]:
    result = row.get("result") or {}
    payout = as_int(result.get("payout_yen"))
    if payout is None:
        payout = as_int(row.get("payout_yen") if row.get("payout_yen") is not None else row.get("payout"))
    trifecta = result.get("trifecta") or row.get("trifecta")
    return {"trifecta": trifecta, "payout_yen": payout, "manshu": bool(payout is not None and payout >= 10000)}


def race_label(row: dict[str, Any]) -> str:
    place = row.get("place_name") or ""
    round_no = row.get("round") if row.get("round") is not None else row.get("round_no")
    return f"{place}{round_no}R"


def stats(rows: list[dict[str, Any]], top_n: int) -> dict[str, Any]:
    picked = rows[:top_n]
    settled = []
    hits = []
    max_payout = 0
    for row in picked:
        result = result_of(row)
        payout = result["payout_yen"]
        if payout is None:
            continue
        settled.append(row)
        max_payout = max(max_payout, payout)
        if result["manshu"]:
            hits.append(
                {
                    "rank": as_int(row.get("rank")),
                    "race": race_label(row),
                    "trifecta": result["trifecta"],
                    "payout_yen": payout,
                    "manshu_rate_pct": as_num(row.get("manshu_rate_pct")),
                }
            )
    return {
        "selected": len(picked),
        "settled": len(settled),
        "manshu_hits": len(hits),
        "manshu_rate_pct": round(len(hits) / len(settled) * 100, 2) if settled else None,
        "max_payout_yen": max_payout or None,
        "hit_races": hits,
    }


def aggregate(day_items: list[dict[str, Any]], group: str, key: str) -> dict[str, Any]:
    selected = settled = manshu_hits = hit_days = days_with_settled = 0
    max_payout = 0
    for item in day_items:
        stat = ((item.get(group) or {}).get(key)) or {}
        selected += as_int(stat.get("selected")) or 0
        settled_count = as_int(stat.get("settled")) or 0
        hit_count = as_int(stat.get("manshu_hits")) or 0
        settled += settled_count
        manshu_hits += hit_count
        if settled_count:
            days_with_settled += 1
        if hit_count:
            hit_days += 1
        payout = as_int(stat.get("max_payout_yen")) or 0
        max_payout = max(max_payout, payout)
    return {
        "selected": selected,
        "settled": settled,
        "manshu_hits": manshu_hits,
        "manshu_rate_pct": round(manshu_hits / settled * 100, 2) if settled else None,
        "hit_days": hit_days,
        "days": days_with_settled,
        "calendar_days": len(day_items),
        "day_hit_rate_pct": round(hit_days / days_with_settled * 100, 2) if days_with_settled else None,
        "max_payout_yen": max_payout or None,
    }


def pick_payload_path(key: str) -> Path | None:
    codex_path = OUTPUT_DIR / f"boaters_manshu_ranking_codex_{key}.json"
    normal_path = OUTPUT_DIR / f"boaters_manshu_ranking_{key}.json"
    if codex_path.exists():
        return codex_path
    if normal_path.exists():
        return normal_path
    return None


def ranking_paths() -> list[tuple[str, Path]]:
    items: list[tuple[str, Path]] = []
    for path in sorted(OUTPUT_DIR.glob("boaters_manshu_ranking_*.json")):
        if path.name.startswith("boaters_manshu_ranking_codex_"):
            continue
        match = RANKING_RE.match(path.name)
        if not match:
            continue
        key = match.group(1)
        payload_path = pick_payload_path(key)
        if payload_path:
            items.append((key, payload_path))
    return items


def build(start_date: str | None, end_date: str | None, top_n: int) -> dict[str, Any]:
    start_key = key_from_date(start_date) if start_date else None
    end_key = key_from_date(end_date) if end_date else None
    dates: list[dict[str, Any]] = []
    for key, payload_path in ranking_paths():
        if start_key and key < start_key:
            continue
        if end_key and key > end_key:
            continue
        payload = load_json(payload_path)
        races = list(payload.get("races") or [])
        strict_value = payload.get("strict_races")
        strict_races = list(strict_value) if isinstance(strict_value, list) else []
        strict_is_fallback = False
        if not strict_races:
            row_types = {str(row.get("ranking_type") or "").strip() for row in races}
            row_statuses = {str(row.get("status") or "").strip() for row in races}
            if row_types == {"strict"} or row_statuses <= {"展示待ち", "確定", "厳選統合", "厳選統合・展示待ち"}:
                strict_races = races
                strict_is_fallback = True
        date_text = payload.get("date") or date_from_key(key)
        item = {
            "date": date_text,
            "key": key,
            "path": f"data/output/boaters_manshu_ranking_{key}.json",
            "codex_path": f"data/output/{payload_path.name}" if payload_path.name.startswith("boaters_manshu_ranking_codex_") else "",
            "logic_label": payload.get("logic_label"),
            "generated_at": payload.get("generated_at"),
            "all_venue": {
                "top1": stats(races, 1),
                "top3": stats(races, 3),
                "top5": stats(races, 5),
                "top10": stats(races, top_n),
            },
            "strict": {
                "top1": stats(strict_races, 1),
                "top3": stats(strict_races, 3),
                "top5": stats(strict_races, 5),
                "top10": stats(strict_races, top_n),
                "fallback_from_all_venue": strict_is_fallback,
            },
        }
        dates.append(item)
    dates.sort(key=lambda item: item["date"])
    aggregate_data = {
        "all_venue": {key: aggregate(dates, "all_venue", key) for key in ["top1", "top3", "top5", "top10"]},
        "strict": {key: aggregate(dates, "strict", key) for key in ["top1", "top3", "top5", "top10"]},
    }
    return {
        "version": "boaters-manshu-history-index-v3",
        "generated_at": iso_now(),
        "start_date": dates[0]["date"] if dates else start_date,
        "end_date": dates[-1]["date"] if dates else end_date,
        "top_n": top_n,
        "logic_label": "Codex厳選ランキング（全ファクター統合）結果集計",
        "dates": dates,
        "aggregate": aggregate_data,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", default="2026-05-01")
    parser.add_argument("--end-date")
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--out", default=str(OUTPUT_DIR / "boaters_manshu_history_index.json"))
    args = parser.parse_args()
    payload = build(args.start_date, args.end_date, args.top_n)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(
        json.dumps(
            {
                "out": str(out_path),
                "start_date": payload.get("start_date"),
                "end_date": payload.get("end_date"),
                "dates": len(payload.get("dates") or []),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
