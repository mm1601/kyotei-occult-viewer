#!/usr/bin/env python3
"""Backtest the fixed Codex core/subcore 3-ren-tan rules.

This script evaluates the operating rule used by the public monitor:

* Morning watchlist TOP10 is frozen from pre-exhibition data.
* After BOATERS AI/exhibition/odds data is available:
  * core: post-exhibition manshu rate >= 40%, 12 tickets.
  * subcore: 38.0-39.9% plus B1 danger, strong outer heads with 5/6, inner axis, 12 tickets.
* Only 3-ren-tan tickets are evaluated. 2-ren-tan is intentionally excluded.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
import time
import warnings
from collections import defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from monitor_boaters_manshu_alerts import (  # noqa: E402
    CORE_ALERT_RATE,
    SUBCORE_ALERT_RATE_MIN,
    core_40_arunashi12,
    subcore_38_arunashi12,
    subcore_entry_checks,
)
from rank_daily_manshu_candidates import (  # noqa: E402
    all_venue_adjustment,
    all_venue_edge_signals,
    build_morning_candidates,
    daily_features,
    int_num,
    num,
    row_summary,
)


DEFAULT_DB = "/Users/ohyabumasaya/Desktop/price_action_analysis/outputs/boaters_all_races.sqlite"
OUT_DIR = ROOT / "reports" / "postdata_manshu_backtest"


def dates_from_db(db_path: Path, start: str | None, end: str | None) -> list[str]:
    clauses = ["result_payout3t1 IS NOT NULL"]
    params: list[str] = []
    if start:
        clauses.append("date >= ?")
        params.append(start)
    if end:
        clauses.append("date <= ?")
        params.append(end)
    sql = f"SELECT DISTINCT date FROM races WHERE {' AND '.join(clauses)} ORDER BY date"
    with sqlite3.connect(db_path) as con:
        return [row[0] for row in con.execute(sql, params)]


def is_general_race(race: dict) -> bool:
    grade = str(race.get("race_grade") or "").upper()
    if any(token in grade for token in ("SG", "G1", "G2", "G3", "PG1")):
        return False
    return True


def norm_combo(value) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())[:3]


def ticket_hit(tickets: set[str], trifecta) -> bool:
    combo = norm_combo(trifecta)
    return len(combo) == 3 and combo in tickets


def metric_row(race: dict, boat: int, metrics: dict) -> dict:
    tenji_rank = num(race.get(f"b{boat}_tenji_rank"))
    tenji_time_rank = num(race.get(f"b{boat}_tenji_time_rank"))
    exhibit_rank = min([v for v in (tenji_rank, tenji_time_rank) if v is not None], default=9)
    return {
        "boat_number": boat,
        "ai_prediction_pct": num(race.get(f"b{boat}_ai_prediction_pct")),
        "ai_3ren_pct": num(race.get(f"b{boat}_ai_3ren_pct")),
        "general_3ren_pct": num(race.get(f"b{boat}_general_3ren_pct")),
        "ai_plus": num(race.get(f"b{boat}_ai_plus")),
        "ai_plus_rank": num(race.get(f"b{boat}_ai_plus_order")),
        "avg_isshu_diff": num(race.get(f"b{boat}_avg_isshu_diff")),
        "st_rank_general": num(race.get(f"b{boat}_st_rank_general")),
        "tenji_rank": tenji_rank,
        "tenji_time_rank": tenji_time_rank,
        "isshu_rank": num(race.get(f"b{boat}_isshu_rank")),
        "exhibit_rank": exhibit_rank,
        "double_time": bool(int_num(race.get(f"b{boat}_double_time"))),
        "super_slit_alert": bool(int_num(race.get(f"b{boat}_super_slit_alert"))),
        "low_outer_revive": int_num(race.get("low_outer_boat")) == boat
        and bool(int_num(race.get("low_outer_exhibit_top2"))),
        "_morning_metrics": metrics,
    }


def monitor_metrics(race: dict, post_rate: float) -> dict:
    metrics = dict(race)
    metrics["manshu_rate_pct"] = post_rate
    metrics["tenji_boats"] = int_num(race.get("tenji_boats")) or 0
    metrics["isshu_boats"] = int_num(race.get("isshu_boats")) or 0
    metrics["raw_isshu_boats"] = int_num(race.get("raw_isshu_boats")) or 0
    metrics["hanshu_boats"] = int_num(race.get("hanshu_boats")) or 0
    for boat in range(1, 7):
        prefix = f"boat{boat}"
        src = f"b{boat}"
        for name in (
            "ai_prediction_pct",
            "odds_prediction_pct",
            "odds_rank",
            "ai_plus",
            "ai_plus_order",
            "ai_3ren_pct",
            "general_3ren_pct",
            "st_rank_general",
            "st_time_avg_general",
            "tenji_time",
            "tenji_rank",
            "tenji_time_rank",
            "isshu_time",
            "isshu_rank",
            "avg_isshu_diff",
            "nige_pct",
            "loss_pct",
        ):
            metrics[f"{prefix}_{name}"] = race.get(f"{src}_{name}")
    return metrics


def post_exhibition_rate(race: dict) -> float:
    edges = all_venue_edge_signals(race)
    summary = row_summary(
        race,
        [],
        status="backtest",
        edge_signals=edges,
        base_rate_override=16.82,
        adjustment_func=all_venue_adjustment,
        condition_override="Codex固定本命/準本命バックテスト",
        ranking_type="strict",
    )
    return float(summary.get("best_manshu_rate_pct") or 0.0)


def choose_rule(race: dict, post_rate: float) -> tuple[str, set[str], dict | None, list[str]]:
    metrics = monitor_metrics(race, post_rate)
    rows = [metric_row(race, boat, metrics) for boat in range(1, 7)]
    if metrics["tenji_boats"] < 6 or metrics["isshu_boats"] < 6:
        return "skip_missing_exhibition", set(), None, ["展示/1周6艇未満"]
    if post_rate >= CORE_ALERT_RATE:
        tickets, roles = core_40_arunashi12(rows)
        if tickets and roles:
            return "core", tickets, roles, [f"展示後40%以上:OK({post_rate:.2f}%)"]
        return "skip_core_ticket_ng", set(), None, [f"展示後40%以上だが12点生成NG({post_rate:.2f}%)"]
    if SUBCORE_ALERT_RATE_MIN <= post_rate < CORE_ALERT_RATE:
        ok, checks = subcore_entry_checks({"manshu_rate_pct": post_rate}, metrics, rows)
        if ok:
            tickets, roles = subcore_38_arunashi12(rows)
            if tickets and roles:
                return "subcore", tickets, roles, checks
        return "skip_subcore_conditions_ng", set(), None, checks
    return "skip_rate_ng", set(), None, [f"展示後38%未満:NG({post_rate:.2f}%)"]


def max_losing_streak(rows: pd.DataFrame) -> int:
    streak = 0
    worst = 0
    for hit in rows["hit"].fillna(False).astype(bool):
        if hit:
            streak = 0
        else:
            streak += 1
            worst = max(worst, streak)
    return worst


def max_drawdown(rows: pd.DataFrame) -> int:
    equity = 0
    peak = 0
    worst = 0
    for _, row in rows.iterrows():
        equity += int(row.get("payback_yen") or 0) - int(row.get("stake_yen") or 0)
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return abs(worst)


def summarize(rows: pd.DataFrame, segment: str) -> dict:
    buy = rows[rows["buy"].eq(1)].copy()
    races = int(len(buy))
    points = int(buy["points"].sum()) if races else 0
    stake = int(buy["stake_yen"].sum()) if races else 0
    payback = int(buy["payback_yen"].sum()) if races else 0
    hits = int(buy["hit"].sum()) if races else 0
    manshu_hits = int((buy["hit"].eq(1) & buy["is_manshu"].eq(1)).sum()) if races else 0
    over5000_hits = int((buy["hit"].eq(1) & buy["is_over5000"].eq(1)).sum()) if races else 0
    return {
        "segment": segment,
        "watch_races": int(len(rows)),
        "buy_races": races,
        "total_points": points,
        "avg_points": round(points / races, 2) if races else None,
        "stake_yen": stake,
        "payback_yen": payback,
        "profit_yen": payback - stake,
        "roi_pct": round(payback / stake * 100, 2) if stake else None,
        "hit_rate_pct": round(hits / races * 100, 2) if races else None,
        "manshu_hit_rate_pct": round(manshu_hits / races * 100, 2) if races else None,
        "over5000_hit_rate_pct": round(over5000_hits / races * 100, 2) if races else None,
        "max_losing_streak": max_losing_streak(buy) if races else None,
        "max_drawdown_yen": max_drawdown(buy) if races else None,
    }


def grouped_roi(rows: pd.DataFrame, key: str) -> pd.DataFrame:
    out = []
    for value, group in rows[rows["buy"].eq(1)].groupby(key):
        out.append(summarize(group, str(value)))
    return pd.DataFrame(out).sort_values("buy_races", ascending=False) if out else pd.DataFrame()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--start", default="2025-01-01")
    parser.add_argument("--end", default="2026-06-29")
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--out-dir", default=str(OUT_DIR))
    parser.add_argument("--progress-every", type=int, default=100)
    args = parser.parse_args()

    warnings.filterwarnings("ignore", category=pd.errors.PerformanceWarning)
    db_path = Path(args.db)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    dates = dates_from_db(db_path, args.start, args.end)
    ledger: list[dict] = []
    started = time.time()
    for idx, date_text in enumerate(dates, start=1):
        df = daily_features(db_path, date_text, {})
        if df.empty:
            continue
        candidates = build_morning_candidates(df, args.top_n)
        race_map = {str(row["race_id"]): row for row in df.to_dict("records")}
        for rank, candidate in enumerate(candidates[: args.top_n], start=1):
            race = race_map.get(str(candidate.get("race_id")))
            if not race or not is_general_race(race):
                continue
            post_rate = post_exhibition_rate(race)
            rule, tickets, roles, checks = choose_rule(race, post_rate)
            payout = int(num(race.get("payout")) or 0)
            trifecta = race.get("trifecta")
            hit = ticket_hit(tickets, trifecta)
            ledger.append(
                {
                    "date": date_text,
                    "month": date_text[:7],
                    "place_name": race.get("place_name"),
                    "round": int_num(race.get("round_no")),
                    "race_id": race.get("race_id"),
                    "morning_rank": rank,
                    "post_rate_pct": round(post_rate, 2),
                    "rule": rule,
                    "buy": int(rule in {"core", "subcore"} and bool(tickets)),
                    "points": len(tickets),
                    "stake_yen": len(tickets) * 100,
                    "payback_yen": payout if hit else 0,
                    "payout_yen": payout,
                    "trifecta": trifecta,
                    "hit": int(hit),
                    "is_manshu": int(payout >= 10000),
                    "is_over5000": int(payout >= 5000),
                    "heads": ",".join(map(str, (roles or {}).get("heads") or [])),
                    "axes": ",".join(map(str, (roles or {}).get("axes") or [])),
                    "keshi": (roles or {}).get("keshi"),
                    "tickets": " ".join(sorted(tickets)),
                    "checks": " / ".join(checks[:12]),
                }
            )
        if args.progress_every and idx % args.progress_every == 0:
            print(f"progress {idx}/{len(dates)} elapsed={time.time() - started:.1f}s", flush=True)

    result = pd.DataFrame(ledger)
    if result.empty:
        raise SystemExit("no rows")

    summary_rows = [
        summarize(result, "本命+準本命"),
        summarize(result[result["rule"].eq("core")], "本命 40%以上"),
        summarize(result[result["rule"].eq("subcore")], "準本命 38〜39.9%条件成立"),
        summarize(result[result["rule"].str.startswith("skip")], "見送り"),
    ]
    summary = pd.DataFrame(summary_rows)
    by_month = grouped_roi(result, "month")
    by_venue = grouped_roi(result, "place_name")

    prefix = f"core_subcore_rules_{args.start.replace('-', '')}_{args.end.replace('-', '')}"
    result.to_csv(out_dir / f"{prefix}_ledger.csv", index=False)
    summary.to_csv(out_dir / f"{prefix}_summary.csv", index=False)
    by_month.to_csv(out_dir / f"{prefix}_by_month.csv", index=False)
    by_venue.to_csv(out_dir / f"{prefix}_by_venue.csv", index=False)

    payload = {
        "version": "core-subcore-rules-v1",
        "period": {"start": args.start, "end": args.end},
        "top_n": args.top_n,
        "summary": summary.to_dict("records"),
        "generated_at_epoch": time.time(),
    }
    (out_dir / f"{prefix}_summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Codex本命/準本命 3連単バックテスト",
        "",
        f"- 期間: {args.start}〜{args.end}",
        f"- 朝監視: TOP{args.top_n}",
        "- 買い方: 3連単のみ。2連単は除外。",
        "- 本命: 展示後40%以上 + 12点生成",
        "- 準本命: 展示後38〜39.9% + 1号艇危険 + 外頭2艇(5/6含む) + 内軸残り + 12点生成",
        "",
        "```text",
        summary.to_string(index=False),
        "```",
    ]
    (out_dir / f"{prefix}_summary.md").write_text("\n".join(lines), encoding="utf-8")
    print(summary.to_string(index=False))
    print(f"wrote {out_dir / f'{prefix}_summary.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
