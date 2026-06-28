#!/usr/bin/env python3
"""Backtest the current pre-exhibition manshu score without reimplementing it."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
import sys
import warnings
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from rank_daily_manshu_candidates import (  # noqa: E402
    add_trifecta_odds_features,
    build_morning_candidates,
    daily_features,
    default_trifecta_odds_db,
    read_matchup_profiles,
)

try:
    warnings.filterwarnings("ignore", category=pd.errors.PerformanceWarning)
except Exception:
    pass


def race_dates(db_path: Path, start: str, end: str) -> list[str]:
    with sqlite3.connect(db_path) as con:
        rows = con.execute(
            """
            SELECT DISTINCT date
            FROM races
            WHERE date BETWEEN ? AND ?
              AND result_payout3t1 IS NOT NULL
            ORDER BY date
            """,
            (start, end),
        ).fetchall()
    return [row[0] for row in rows]


def base_counts(db_path: Path, start: str, end: str) -> dict:
    with sqlite3.connect(db_path) as con:
        row = con.execute(
            """
            SELECT
              COUNT(*) AS races,
              SUM(CASE WHEN result_payout3t1 >= 10000 THEN 1 ELSE 0 END) AS manshu
            FROM races
            WHERE date BETWEEN ? AND ?
              AND result_payout3t1 IS NOT NULL
            """,
            (start, end),
        ).fetchone()
    races = int(row[0] or 0)
    manshu = int(row[1] or 0)
    return {
        "races": races,
        "manshu": manshu,
        "manshu_rate_pct": round(manshu / races * 100, 2) if races else None,
    }


def update_bucket(bucket: dict, row: dict) -> None:
    payout = row.get("payout")
    payout_value = None
    if payout is not None:
        try:
            payout_float = float(payout)
            if not math.isnan(payout_float):
                payout_value = payout_float
        except Exception:
            payout_value = None
    is_manshu = payout_value is not None and payout_value >= 10000
    bucket["selected_races"] += 1
    bucket["manshu_hits"] += int(is_manshu)
    bucket["payout_sum"] += payout_value or 0
    bucket["score_sum"] += float(row.get("candidate_score") or 0)
    bucket["rate_sum"] += float(row.get("best_manshu_rate_pct") or 0)


def finalize_bucket(bucket: dict, baseline_rate: float | None) -> dict:
    selected = int(bucket.get("selected_races") or 0)
    manshu = int(bucket.get("manshu_hits") or 0)
    rate = manshu / selected * 100 if selected else None
    return {
        "selected_races": selected,
        "manshu_hits": manshu,
        "actual_manshu_rate_pct": round(rate, 2) if rate is not None else None,
        "lift_vs_all": round(rate / baseline_rate, 2) if selected and baseline_rate else None,
        "avg_score": round(bucket.get("score_sum", 0) / selected, 2) if selected else None,
        "avg_predicted_manshu_rate_pct": round(bucket.get("rate_sum", 0) / selected, 2) if selected else None,
        "avg_payout_yen": round(bucket.get("payout_sum", 0) / selected, 0) if selected else None,
    }


def write_csv(path: Path, rows: list[dict], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        rows = []
    if fieldnames is None:
        keys = []
        for row in rows:
            for key in row:
                if key not in keys:
                    keys.append(key)
        fieldnames = keys
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})


def reason_at(row: dict, index: int) -> str:
    reasons = row.get("candidate_reasons") or []
    return reasons[index] if index < len(reasons) else ""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--start-date", default="2025-01-01")
    parser.add_argument("--end-date", default="2026-06-18")
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--matchup-profile", default=str(ROOT / "data" / "analysis" / "matchup_profiles.csv"))
    parser.add_argument("--trifecta-odds-db", default=str(default_trifecta_odds_db() or ""))
    parser.add_argument("--out-dir", default=str(ROOT / "reports"))
    parser.add_argument("--progress-every", type=int, default=25)
    args = parser.parse_args()

    db_path = Path(args.db)
    out_dir = Path(args.out_dir)
    matchup_path = Path(args.matchup_profile) if args.matchup_profile else None
    matchup_profiles = read_matchup_profiles(matchup_path) if matchup_path and matchup_path.exists() else {}
    odds_db = args.trifecta_odds_db if args.trifecta_odds_db and Path(args.trifecta_odds_db).exists() else ""
    dates = race_dates(db_path, args.start_date, args.end_date)
    baseline = base_counts(db_path, args.start_date, args.end_date)
    baseline_rate = baseline["manshu_rate_pct"]

    top_buckets = {k: defaultdict(float) for k in (1, 3, 5, 10)}
    month_buckets = defaultdict(lambda: defaultdict(float))
    venue_buckets = defaultdict(lambda: defaultdict(float))
    year_buckets = defaultdict(lambda: defaultdict(float))
    selections: list[dict] = []
    day_rows: list[dict] = []
    errors: list[dict] = []

    for idx, date_text in enumerate(dates, start=1):
        if args.progress_every and (idx == 1 or idx % args.progress_every == 0):
            print(f"[{idx}/{len(dates)}] {date_text}", flush=True)
        try:
            df = daily_features(db_path, date_text, matchup_profiles=matchup_profiles)
            df = add_trifecta_odds_features(df, odds_db, date_text)
            candidates = build_morning_candidates(df, args.top_n)
        except Exception as exc:
            errors.append({"date": date_text, "error": str(exc)})
            continue

        day_selected = 0
        day_top5_manshu = 0
        for rank, row in enumerate(candidates[: args.top_n], start=1):
            payout = row.get("payout")
            payout_value = None
            if payout is not None:
                try:
                    payout_float = float(payout)
                    if not math.isnan(payout_float):
                        payout_value = payout_float
                except Exception:
                    payout_value = None
            is_manshu = payout_value is not None and payout_value >= 10000
            month = str(row.get("date"))[:7]
            year = str(row.get("date"))[:4]
            venue = row.get("place_name") or "不明"
            record = {
                "date": row.get("date"),
                "rank": rank,
                "place_name": venue,
                "round": row.get("round"),
                "deadline_time": row.get("deadline_time"),
                "predicted_manshu_rate_pct": row.get("best_manshu_rate_pct"),
                "score": row.get("candidate_score"),
                "material_count": row.get("candidate_material_count"),
                "material_score": row.get("candidate_material_score"),
                "payout_yen": payout_value,
                "trifecta": row.get("trifecta"),
                "is_manshu": int(is_manshu),
                "reason_1": reason_at(row, 0),
                "reason_2": reason_at(row, 1),
                "reason_3": reason_at(row, 2),
            }
            selections.append(record)
            day_selected += 1
            if rank <= 5:
                day_top5_manshu += int(is_manshu)
            for k, bucket in top_buckets.items():
                if rank <= k:
                    update_bucket(bucket, row)
            if rank <= 5:
                update_bucket(month_buckets[month], row)
                update_bucket(venue_buckets[venue], row)
                update_bucket(year_buckets[year], row)

        day_rows.append(
            {
                "date": date_text,
                "candidate_count": len(candidates),
                "top5_selected": min(5, len(candidates)),
                "top5_manshu_hits": day_top5_manshu,
            }
        )

    summary_rows = []
    for k in (1, 3, 5, 10):
        result = finalize_bucket(top_buckets[k], baseline_rate)
        result["rank_group"] = f"TOP{k}"
        result["baseline_manshu_rate_pct"] = baseline_rate
        result["recall_of_all_manshu_pct"] = (
            round(result["manshu_hits"] / baseline["manshu"] * 100, 2) if baseline["manshu"] else None
        )
        summary_rows.append(result)

    month_rows = [
        {"month": key, **finalize_bucket(bucket, baseline_rate)}
        for key, bucket in sorted(month_buckets.items())
    ]
    venue_rows = [
        {"place_name": key, **finalize_bucket(bucket, baseline_rate)}
        for key, bucket in sorted(venue_buckets.items(), key=lambda item: item[1]["selected_races"], reverse=True)
    ]
    year_rows = [
        {"year": key, **finalize_bucket(bucket, baseline_rate)}
        for key, bucket in sorted(year_buckets.items())
    ]

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "logic_version": "pre_exhibition_manshu_v1",
        "db": str(db_path),
        "start_date": args.start_date,
        "end_date": args.end_date,
        "baseline": baseline,
        "dates_attempted": len(dates),
        "dates_failed": len(errors),
        "summary": summary_rows,
        "by_year_top5": year_rows,
        "notes": [
            "Ranking calls build_morning_candidates() from rank_daily_manshu_candidates.py directly.",
            "The score excludes exhibition time, 1-lap time, BOATERS AI prediction, and last-minute AI odds evaluation.",
            "TOPK means taking up to K ranked candidates per race day.",
        ],
        "errors": errors[:20],
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "pre_exhibition_manshu_v1_summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_csv(out_dir / "pre_exhibition_manshu_v1_summary.csv", summary_rows)
    write_csv(out_dir / "pre_exhibition_manshu_v1_by_month_top5.csv", month_rows)
    write_csv(out_dir / "pre_exhibition_manshu_v1_by_venue_top5.csv", venue_rows)
    write_csv(out_dir / "pre_exhibition_manshu_v1_by_year_top5.csv", year_rows)
    write_csv(out_dir / "pre_exhibition_manshu_v1_selections.csv", selections)
    write_csv(out_dir / "pre_exhibition_manshu_v1_daily.csv", day_rows)

    lines = [
        "# 展示前 万舟率ランキング v1 バックテスト",
        "",
        f"- 期間: {args.start_date}〜{args.end_date}",
        f"- 全体レース: {baseline['races']:,}R",
        f"- 全体万舟: {baseline['manshu']:,}R",
        f"- 全体万舟率: {baseline_rate}%",
        f"- 失敗日数: {len(errors)}日",
        "",
        "## TOP別",
        "",
        "|対象|選出R|万舟R|万舟率|全体比|万舟捕捉率|平均点|平均予測率|",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary_rows:
        lines.append(
            "|{rank_group}|{selected_races}|{manshu_hits}|{actual_manshu_rate_pct}%|{lift_vs_all}x|{recall_of_all_manshu_pct}%|{avg_score}|{avg_predicted_manshu_rate_pct}%|".format(
                **row
            )
        )
    lines.extend(
        [
            "",
            "## 年別 TOP5",
            "",
            "|年|選出R|万舟R|万舟率|全体比|",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for row in year_rows:
        lines.append(
            f"|{row['year']}|{row['selected_races']}|{row['manshu_hits']}|{row['actual_manshu_rate_pct']}%|{row['lift_vs_all']}x|"
        )
    lines.extend(
        [
            "",
            "## 注意",
            "",
            "- これはv1固定の実力測定です。ここではまだ点数最適化はしていません。",
            "- 展示前ランキングなので、展示タイム・1周タイム・直前BOATERS AIはスコアに使っていません。",
            "- 次工程で、2025年を調整期間、2026年を検証期間として重みを見直します。",
        ]
    )
    (out_dir / "pre_exhibition_manshu_v1_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
