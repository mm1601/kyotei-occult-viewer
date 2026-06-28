#!/usr/bin/env python3
"""Calibrate and validate pre-exhibition manshu v2 using time split data.

Training uses 2025 only. Validation uses 2026 only. The script calls the
current v1 scorer instead of copying its logic, then applies a transparent
calibration layer:

- score-band empirical probability
- shrinked venue adjustment
- small material-count adjustment
"""

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


def baseline_counts(db_path: Path, start: str, end: str) -> dict:
    with sqlite3.connect(db_path) as con:
        row = con.execute(
            """
            SELECT
              COUNT(*),
              SUM(CASE WHEN result_payout3t1 >= 10000 THEN 1 ELSE 0 END)
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


def payout_value(row: dict) -> float | None:
    payout = row.get("payout")
    if payout is None:
        return None
    try:
        value = float(payout)
    except Exception:
        return None
    return None if math.isnan(value) else value


def row_record(row: dict, rank: int, source_rank: str) -> dict:
    payout = payout_value(row)
    reasons = row.get("candidate_reasons") or []
    return {
        "date": row.get("date"),
        "rank": rank,
        "source_rank": source_rank,
        "place_name": row.get("place_name") or "不明",
        "round": row.get("round"),
        "deadline_time": row.get("deadline_time"),
        "v1_score": float(row.get("candidate_score") or 0),
        "v1_predicted_pct": float(row.get("best_manshu_rate_pct") or 0),
        "material_count": int(row.get("candidate_material_count") or 0),
        "material_score": float(row.get("candidate_material_score") or 0),
        "v2_probability_pct": float(row.get("v2_probability_pct") or 0),
        "v2_rank_score": float(row.get("v2_rank_score") or 0),
        "payout_yen": payout,
        "trifecta": row.get("trifecta"),
        "is_manshu": int(payout is not None and payout >= 10000),
        "reason_1": reasons[0] if len(reasons) > 0 else "",
        "reason_2": reasons[1] if len(reasons) > 1 else "",
        "reason_3": reasons[2] if len(reasons) > 2 else "",
    }


def collect_candidates(
    db_path: Path,
    dates: list[str],
    matchup_profiles: dict,
    odds_db: str,
    pool_size: int,
    progress_every: int,
) -> tuple[list[dict], list[dict]]:
    rows: list[dict] = []
    errors: list[dict] = []
    for idx, date_text in enumerate(dates, start=1):
        if progress_every and (idx == 1 or idx % progress_every == 0):
            print(f"[{idx}/{len(dates)}] {date_text}", flush=True)
        try:
            df = daily_features(db_path, date_text, matchup_profiles=matchup_profiles)
            df = add_trifecta_odds_features(df, odds_db, date_text)
            candidates = build_morning_candidates(df, pool_size)
        except Exception as exc:
            errors.append({"date": date_text, "error": str(exc)})
            continue
        for rank, row in enumerate(candidates, start=1):
            record = dict(row)
            record["_v1_rank"] = rank
            payout = payout_value(record)
            record["_is_manshu"] = int(payout is not None and payout >= 10000)
            rows.append(record)
    return rows, errors


def bin_for_score(score: float) -> str:
    if score >= 20:
        return "20+"
    if score >= 18:
        return "18-20"
    if score >= 16:
        return "16-18"
    if score >= 14:
        return "14-16"
    if score >= 12:
        return "12-14"
    return "<12"


def rate(hits: int, total: int) -> float | None:
    return hits / total * 100 if total else None


def shrink_rate(hits: int, total: int, prior_rate: float, prior_n: int = 250) -> float:
    return (hits + prior_rate / 100 * prior_n) / (total + prior_n) * 100


def build_calibration(train_rows: list[dict]) -> dict:
    total = len(train_rows)
    hits = sum(int(row.get("_is_manshu") or 0) for row in train_rows)
    global_rate = hits / total * 100 if total else 16.57

    score_bins: dict[str, dict] = {}
    for row in train_rows:
        key = bin_for_score(float(row.get("candidate_score") or 0))
        bucket = score_bins.setdefault(key, {"total": 0, "hits": 0})
        bucket["total"] += 1
        bucket["hits"] += int(row.get("_is_manshu") or 0)
    for key, bucket in score_bins.items():
        bucket["rate_pct"] = round(shrink_rate(bucket["hits"], bucket["total"], global_rate, 200), 3)

    venues: dict[str, dict] = {}
    for row in train_rows:
        key = row.get("place_name") or "不明"
        bucket = venues.setdefault(key, {"total": 0, "hits": 0})
        bucket["total"] += 1
        bucket["hits"] += int(row.get("_is_manshu") or 0)
    for key, bucket in venues.items():
        venue_rate = shrink_rate(bucket["hits"], bucket["total"], global_rate, 350)
        bucket["rate_pct"] = round(venue_rate, 3)
        bucket["adjust_pp"] = round(max(-4.0, min(4.0, venue_rate - global_rate)), 3)

    material_bins: dict[str, dict] = {}
    for row in train_rows:
        count = int(row.get("candidate_material_count") or 0)
        key = "10+" if count >= 10 else "7-9" if count >= 7 else "4-6" if count >= 4 else "<4"
        bucket = material_bins.setdefault(key, {"total": 0, "hits": 0})
        bucket["total"] += 1
        bucket["hits"] += int(row.get("_is_manshu") or 0)
    for key, bucket in material_bins.items():
        mat_rate = shrink_rate(bucket["hits"], bucket["total"], global_rate, 250)
        bucket["rate_pct"] = round(mat_rate, 3)
        bucket["adjust_pp"] = round(max(-2.0, min(2.0, mat_rate - global_rate)), 3)

    return {
        "global_rate_pct": round(global_rate, 3),
        "score_bins": score_bins,
        "venues": venues,
        "material_bins": material_bins,
    }


def material_key(row: dict) -> str:
    count = int(row.get("candidate_material_count") or 0)
    if count >= 10:
        return "10+"
    if count >= 7:
        return "7-9"
    if count >= 4:
        return "4-6"
    return "<4"


def apply_calibration(rows: list[dict], calibration: dict) -> list[dict]:
    global_rate = float(calibration["global_rate_pct"])
    for row in rows:
        score = float(row.get("candidate_score") or 0)
        score_key = bin_for_score(score)
        score_rate = calibration["score_bins"].get(score_key, {}).get("rate_pct", global_rate)
        venue = row.get("place_name") or "不明"
        venue_adj = calibration["venues"].get(venue, {}).get("adjust_pp", 0.0)
        mat_adj = calibration["material_bins"].get(material_key(row), {}).get("adjust_pp", 0.0)
        # Keep v2 ranking transparent and conservative. Score still matters,
        # but calibrated empirical probability is the display value.
        prob = max(8.0, min(32.0, float(score_rate) + float(venue_adj) + 0.5 * float(mat_adj)))
        row["v2_probability_pct"] = round(prob, 2)
        row["v2_rank_score"] = round(prob + min(1.5, max(0.0, score - 16.0) * 0.12), 3)
    return rows


def summarize_ranked(rows_by_date: dict[str, list[dict]], rank_field: str, baseline_rate: float, top_values=(1, 3, 5, 10)) -> list[dict]:
    buckets = {k: defaultdict(float) for k in top_values}
    for date_rows in rows_by_date.values():
        ranked = sorted(
            date_rows,
            key=lambda row: (
                float(row.get(rank_field) or 0),
                float(row.get("candidate_score") or 0),
                -int(row.get("_v1_rank") or 999),
            ),
            reverse=True,
        )
        for rank, row in enumerate(ranked, start=1):
            payout = payout_value(row)
            for k, bucket in buckets.items():
                if rank <= k:
                    bucket["selected_races"] += 1
                    bucket["manshu_hits"] += int(payout is not None and payout >= 10000)
                    bucket["payout_sum"] += payout or 0
                    bucket["prob_sum"] += float(row.get("v2_probability_pct") or row.get("best_manshu_rate_pct") or 0)
                    bucket["score_sum"] += float(row.get("candidate_score") or 0)
    output = []
    for k in top_values:
        bucket = buckets[k]
        selected = int(bucket["selected_races"])
        hits = int(bucket["manshu_hits"])
        actual = rate(hits, selected)
        output.append(
            {
                "rank_group": f"TOP{k}",
                "selected_races": selected,
                "manshu_hits": hits,
                "actual_manshu_rate_pct": round(actual, 2) if actual is not None else None,
                "lift_vs_all": round(actual / baseline_rate, 2) if actual is not None and baseline_rate else None,
                "avg_probability_pct": round(bucket["prob_sum"] / selected, 2) if selected else None,
                "avg_score": round(bucket["score_sum"] / selected, 2) if selected else None,
                "avg_payout_yen": round(bucket["payout_sum"] / selected, 0) if selected else None,
            }
        )
    return output


def group_by_date(rows: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("date"))].append(row)
    return grouped


def ranked_selections(rows_by_date: dict[str, list[dict]], rank_field: str, top_n: int, source_rank: str) -> list[dict]:
    selected: list[dict] = []
    for date_text in sorted(rows_by_date):
        ranked = sorted(
            rows_by_date[date_text],
            key=lambda row: (
                float(row.get(rank_field) or 0),
                float(row.get("candidate_score") or 0),
                -int(row.get("_v1_rank") or 999),
            ),
            reverse=True,
        )
        for rank, row in enumerate(ranked[:top_n], start=1):
            selected.append(row_record(row, rank, source_rank))
    return selected


def write_csv(path: Path, rows: list[dict], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        keys: list[str] = []
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--train-start", default="2025-01-01")
    parser.add_argument("--train-end", default="2025-12-31")
    parser.add_argument("--test-start", default="2026-01-01")
    parser.add_argument("--test-end", default="2026-06-18")
    parser.add_argument("--pool-size", type=int, default=24)
    parser.add_argument("--matchup-profile", default=str(ROOT / "data" / "analysis" / "matchup_profiles.csv"))
    parser.add_argument("--trifecta-odds-db", default=str(default_trifecta_odds_db() or ""))
    parser.add_argument("--out-dir", default=str(ROOT / "reports"))
    parser.add_argument("--model-out", default=str(ROOT / "data" / "model" / "pre_exhibition_manshu_v2_calibration.json"))
    parser.add_argument("--progress-every", type=int, default=100)
    args = parser.parse_args()

    db_path = Path(args.db)
    out_dir = Path(args.out_dir)
    matchup_path = Path(args.matchup_profile) if args.matchup_profile else None
    matchup_profiles = read_matchup_profiles(matchup_path) if matchup_path and matchup_path.exists() else {}
    odds_db = args.trifecta_odds_db if args.trifecta_odds_db and Path(args.trifecta_odds_db).exists() else ""

    train_dates = race_dates(db_path, args.train_start, args.train_end)
    test_dates = race_dates(db_path, args.test_start, args.test_end)
    print("collect train", flush=True)
    train_rows, train_errors = collect_candidates(db_path, train_dates, matchup_profiles, odds_db, args.pool_size, args.progress_every)
    calibration = build_calibration(train_rows)
    print("collect test", flush=True)
    test_rows, test_errors = collect_candidates(db_path, test_dates, matchup_profiles, odds_db, args.pool_size, args.progress_every)
    apply_calibration(test_rows, calibration)

    train_baseline = baseline_counts(db_path, args.train_start, args.train_end)
    test_baseline = baseline_counts(db_path, args.test_start, args.test_end)
    test_baseline_rate = float(test_baseline["manshu_rate_pct"] or 0)
    test_by_date = group_by_date(test_rows)

    v1_summary = summarize_ranked(test_by_date, "candidate_score", test_baseline_rate)
    v2_summary = summarize_ranked(test_by_date, "v2_probability_pct", test_baseline_rate)
    v1_top10 = ranked_selections(test_by_date, "candidate_score", 10, "v1_score")
    v2_top10 = ranked_selections(test_by_date, "v2_probability_pct", 10, "v2_calibrated_probability")

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "logic_version": "pre_exhibition_manshu_v2_calibrated",
        "train_period": {"start": args.train_start, "end": args.train_end},
        "test_period": {"start": args.test_start, "end": args.test_end},
        "train_baseline": train_baseline,
        "test_baseline": test_baseline,
        "train_candidate_rows": len(train_rows),
        "test_candidate_rows": len(test_rows),
        "train_errors": train_errors[:20],
        "test_errors": test_errors[:20],
        "calibration": calibration,
        "v1_test_summary": v1_summary,
        "v2_test_summary": v2_summary,
        "decision": "PROMOTE_CANDIDATE" if (v2_summary[2]["actual_manshu_rate_pct"] or 0) > (v1_summary[2]["actual_manshu_rate_pct"] or 0) else "HOLD_FOR_FORWARD_TEST",
        "notes": [
            "Training uses only 2025. Test uses 2026 through 2026-06-18.",
            "v2 display probability is calibrated from 2025 empirical score bands plus shrinked venue/material adjustments.",
            "v2 ranking sorts by calibrated probability first so the public ranking matches the displayed manshu rate.",
            "Morning candidates use the venue diversity rule from rank_daily_manshu_candidates.py: max two races per venue unless an extra race is exceptionally strong.",
            "v2 ranking is not allowed to use 2026 outcomes for weight selection.",
        ],
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "pre_exhibition_manshu_v2_summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    model_path = Path(args.model_out)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    model_payload = {
        "model_version": "pre_exhibition_manshu_v2_calibrated_2025_train",
        "generated_at": payload["generated_at"],
        "train_period": payload["train_period"],
        "test_period": payload["test_period"],
        "train_baseline": payload["train_baseline"],
        "test_baseline": payload["test_baseline"],
        "calibration": calibration,
        "validation_summary": {
            "v1_test_summary": v1_summary,
            "v2_test_summary": v2_summary,
            "decision": payload["decision"],
        },
        "notes": payload["notes"],
    }
    model_path.write_text(json.dumps(model_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_csv(out_dir / "pre_exhibition_manshu_v2_v1_test_summary.csv", v1_summary)
    write_csv(out_dir / "pre_exhibition_manshu_v2_v2_test_summary.csv", v2_summary)
    write_csv(out_dir / "pre_exhibition_manshu_v2_v1_test_selections.csv", v1_top10)
    write_csv(out_dir / "pre_exhibition_manshu_v2_v2_test_selections.csv", v2_top10)

    lines = [
        "# 展示前 万舟率ランキング v2 校正検証",
        "",
        f"- 学習期間: {args.train_start}〜{args.train_end}",
        f"- 検証期間: {args.test_start}〜{args.test_end}",
        f"- 検証全体万舟率: {test_baseline['manshu_rate_pct']}%",
        f"- 判定: {payload['decision']}",
        "",
        "## 2026検証 v1",
        "",
        "|対象|選出R|万舟R|万舟率|全体比|平均表示率|",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in v1_summary:
        lines.append(
            f"|{row['rank_group']}|{row['selected_races']}|{row['manshu_hits']}|{row['actual_manshu_rate_pct']}%|{row['lift_vs_all']}x|{row['avg_probability_pct']}%|"
        )
    lines.extend(["", "## 2026検証 v2", "", "|対象|選出R|万舟R|万舟率|全体比|平均表示率|", "|---|---:|---:|---:|---:|---:|"])
    for row in v2_summary:
        lines.append(
            f"|{row['rank_group']}|{row['selected_races']}|{row['manshu_hits']}|{row['actual_manshu_rate_pct']}%|{row['lift_vs_all']}x|{row['avg_probability_pct']}%|"
        )
    lines.extend(
        [
            "",
            "## 読み取り",
            "",
            "- v2は2025年だけで確率表示と場別補正を作り、2026年で検証しています。",
            "- 展示前ランキングは、同じ場を原則2Rまでに抑える場分散ルール込みで検証しています。",
            "- v2の主目的は、35%台に出すぎていた表示万舟率を実測に近づけることです。",
            "- v2のTOP5万舟率がv1を上回らない場合、本番採用せず前向き検証に回します。",
        ]
    )
    (out_dir / "pre_exhibition_manshu_v2_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
