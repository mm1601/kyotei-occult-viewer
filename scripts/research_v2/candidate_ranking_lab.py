#!/usr/bin/env python3
"""Build research-only candidate ranking and lab outputs."""

from __future__ import annotations

import argparse
import csv
import json
import math
import pickle
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
JST = timezone(timedelta(hours=9))
PLACE_ID_TO_JCD = {i: f"{i:02d}" for i in range(1, 25)}


def now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, "", "-", "nan") or pd.isna(value):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def as_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value in (None, "", "-", "nan") or pd.isna(value):
            return default
        out = float(value)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(out) else out


def pct(num: float, den: float) -> float | None:
    return round(num / den * 100, 2) if den else None


def race_key(date_text: str, place_id: Any, round_no: Any) -> str:
    jcd = PLACE_ID_TO_JCD.get(as_int(place_id))
    return f"{date_text}_{jcd}_{as_int(round_no):02d}" if jcd else ""


def parse_result(row: dict[str, Any]) -> tuple[str, int | None, bool | None]:
    result = row.get("result") or {}
    payout = result.get("payout_yen")
    payout_int = as_int(payout, -1) if payout is not None else None
    return result.get("trifecta") or "", payout_int, None if payout_int is None else payout_int >= 10000


def load_role_groups(path: Path) -> dict[str, pd.DataFrame]:
    df = pd.read_csv(path, low_memory=False)
    return {race_id: group.copy() for race_id, group in df.groupby("race_id")}


def role_adjustment(group: pd.DataFrame | None) -> tuple[float, dict[str, Any], list[str]]:
    if group is None or group.empty:
        return 0.0, {}, ["role_dataset_missing"]
    group = group.copy()
    group["lane"] = pd.to_numeric(group["lane"], errors="coerce")
    group["head_score_preview"] = pd.to_numeric(group["head_score_preview"], errors="coerce").fillna(0)
    ordered = group.sort_values("head_score_preview", ascending=False)
    top2 = [as_int(v) for v in ordered.head(2)["lane"].tolist()]
    gap_2_3 = None
    if len(ordered) >= 3:
        gap_2_3 = float(ordered.iloc[1]["head_score_preview"] - ordered.iloc[2]["head_score_preview"])
    adjustment = 0.0
    reasons = []
    if 1 not in top2:
        adjustment += 1.2
        reasons.append("head_top2_without_lane1")
    if any(lane in {5, 6} for lane in top2):
        adjustment += 0.8
        reasons.append("outer56_head_candidate")
    if gap_2_3 is not None and gap_2_3 < 4.0:
        adjustment -= 0.5
        reasons.append("head_spread_too_wide")
    return adjustment, {"head_candidates": top2, "head_gap_2_3": round(gap_2_3, 3) if gap_2_3 is not None else None}, reasons


def cluster_assignments(df: pd.DataFrame, model_path: Path) -> dict[str, dict[str, Any]]:
    if not model_path.exists():
        return {}
    with model_path.open("rb") as handle:
        bundle = pickle.load(handle)
    features = bundle["features"]
    scaler = bundle["scaler"]
    model = bundle["model"]
    threshold = float(bundle["threshold"])
    work = df[df["valid_for_analysis"].astype(int) == 1].copy()
    x = work[features].copy()
    for col in features:
        x[col] = pd.to_numeric(x[col], errors="coerce")
    x = x.fillna(x.median(numeric_only=True)).fillna(0.0)
    scaled = scaler.transform(x)
    distances = model.transform(scaled)
    nearest = distances.argmin(axis=1)
    nearest_distance = distances.min(axis=1)
    out: dict[str, dict[str, Any]] = {}
    for idx, row in enumerate(work.itertuples()):
        key = f"{row.date}_{str(row.jcd).zfill(2)}_{int(row.race_no):02d}"
        dist = float(nearest_distance[idx])
        cluster_id = f"C{int(nearest[idx])}" if dist <= threshold else "unknown"
        out[key] = {
            "cluster_id": cluster_id,
            "cluster_similarity": round(max(0.0, 1.0 - dist / threshold), 4) if threshold else 0.0,
        }
    return out


def cluster_lifts(path: Path) -> dict[str, float]:
    if not path.exists():
        return {}
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    return {row["cluster_id"]: as_float(row.get("baseline_lift"), 1.0) or 1.0 for row in rows}


def candidate_rows_for_payload(
    payload: dict[str, Any],
    role_groups: dict[str, pd.DataFrame],
    cluster_map: dict[str, dict[str, Any]],
    lifts: dict[str, float],
) -> list[dict[str, Any]]:
    rows = payload.get("strict_races") or payload.get("races") or []
    output = []
    for row in rows[:10]:
        key = race_key(payload.get("date"), row.get("place_id"), row.get("round"))
        current = as_float(row.get("manshu_rate_pct"), 0.0) or 0.0
        base = as_float(row.get("base_manshu_rate_pct"), current)
        exhibition_delta = current - (base if base is not None else current)
        role_bonus, role_info, role_reasons = role_adjustment(role_groups.get(key))
        cluster = cluster_map.get(key, {"cluster_id": "unknown", "cluster_similarity": None})
        lift = lifts.get(cluster["cluster_id"], 1.0)
        cluster_bonus = max(-1.0, min(2.0, (lift - 1.0) * 4.0))
        candidate = current + role_bonus + cluster_bonus + min(1.5, max(-1.5, exhibition_delta * 0.08))
        trifecta, payout, manshu = parse_result(row)
        warnings = []
        if key not in role_groups:
            warnings.append("role_dataset_missing")
        if cluster["cluster_id"] == "unknown":
            warnings.append("cluster_unknown")
        metrics = row.get("metrics") or {}
        if not metrics.get("tenji_boats"):
            warnings.append("preview_data_missing")
        output.append(
            {
                "race_key": key,
                "current_rank": row.get("rank"),
                "candidate_rank": None,
                "place_name": row.get("place_name"),
                "round": row.get("round"),
                "deadline_time": row.get("deadline_time"),
                "current_manshu_rate_pct": round(current, 2),
                "candidate_manshu_probability_pct": round(candidate, 2),
                "rank_delta": None,
                "morning_manshu_probability_pct": round(base, 2) if base is not None else None,
                "preview_manshu_probability_pct": round(current, 2),
                "exhibition_adjustment_pct": round(exhibition_delta, 2),
                "head_candidates": role_info.get("head_candidates"),
                "head_reason_checks": role_reasons,
                "cluster_id": cluster["cluster_id"],
                "cluster_similarity": cluster.get("cluster_similarity"),
                "used_features": ["current_manshu_rate", "role_head_shape", "cluster_lift", "exhibition_delta"],
                "missing_warnings": sorted(set(warnings)),
                "skip_flag": bool(warnings and "role_dataset_missing" in warnings),
                "model_version": "research-v2-candidate-ranking-1",
                "prediction_created_at": now_iso(),
                "result": {"trifecta": trifecta, "payout_yen": payout, "manshu": manshu},
            }
        )
    ordered = sorted(output, key=lambda item: item["candidate_manshu_probability_pct"], reverse=True)
    rank_by_key = {item["race_key"]: idx + 1 for idx, item in enumerate(ordered)}
    for item in output:
        item["candidate_rank"] = rank_by_key[item["race_key"]]
        item["rank_delta"] = as_int(item["current_rank"]) - as_int(item["candidate_rank"])
    return sorted(output, key=lambda item: item["candidate_rank"])


def evaluate_order(rows: list[dict[str, Any]], order_key: str, k: int) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda item: item[order_key] if order_key.endswith("rank") else -item[order_key])
    top = ordered[:k]
    settled = [row for row in top if row["result"]["payout_yen"] is not None]
    hits = [row for row in settled if row["result"]["manshu"]]
    return {"selected": len(top), "settled": len(settled), "manshu_hits": len(hits), "manshu_rate_pct": pct(len(hits), len(settled))}


def compare_rankings(payloads: list[dict[str, Any]], role_groups: dict[str, pd.DataFrame], cluster_map: dict[str, dict[str, Any]], lifts: dict[str, float]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    by_date: list[tuple[str, list[dict[str, Any]]]] = []
    for payload in payloads:
        cand = candidate_rows_for_payload(payload, role_groups, cluster_map, lifts)
        cand = [row for row in cand if "role_dataset_missing" not in row["missing_warnings"]]
        if cand:
            by_date.append((payload.get("date"), cand))
    for k in [1, 3, 5, 10]:
        current_parts = [evaluate_order(cand, "current_rank", k) for _date, cand in by_date]
        candidate_parts = [evaluate_order(cand, "candidate_rank", k) for _date, cand in by_date]
        for label, parts in [("current", current_parts), ("candidate", candidate_parts)]:
            settled = sum(part["settled"] for part in parts)
            hits = sum(part["manshu_hits"] for part in parts)
            rows.append(
                {
                    "ranking": label,
                    "top_k": k,
                    "days": len(parts),
                    "settled": settled,
                    "manshu_hits": hits,
                    "manshu_rate_pct": pct(hits, settled),
                }
            )
    return rows


def load_payloads(start_date: str, end_date: str) -> list[dict[str, Any]]:
    start = start_date.replace("-", "")
    end = end_date.replace("-", "")
    payloads = []
    for path in sorted((ROOT / "data" / "output").glob("boaters_manshu_ranking_*.json")):
        if path.name.startswith("boaters_manshu_ranking_codex_"):
            continue
        key = path.stem.rsplit("_", 1)[-1]
        if len(key) == 8 and start <= key <= end:
            payloads.append(json.loads(path.read_text(encoding="utf-8")))
    return payloads


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def write_report(path: Path, comparison: list[dict[str, Any]]) -> str:
    current10 = next((row for row in comparison if row["ranking"] == "current" and row["top_k"] == 10), {})
    candidate10 = next((row for row in comparison if row["ranking"] == "candidate" and row["top_k"] == 10), {})
    decision = "HOLD_FOR_FORWARD_TEST"
    if candidate10 and current10 and (candidate10.get("manshu_rate_pct") or 0) < (current10.get("manshu_rate_pct") or 0):
        decision = "REJECT"
    table = ["| ランキング | TOP | 日数 | 消化R | 万舟 | 万舟率 |", "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for row in comparison:
        table.append(f"| {row['ranking']} | {row['top_k']} | {row['days']} | {row['settled']} | {row['manshu_hits']} | {row['manshu_rate_pct']} |")
    text = [
        "# research_v2 Candidate Ranking Comparison",
        "",
        "候補ランキングは本番TOP10を置き換えず、保存済み厳選TOP10内で固定重みの研究用並べ替えを行っただけです。",
        "",
        "\n".join(table),
        "",
        "## 判定",
        "",
        f"- 判定: `{decision}`",
        "- 本番反映はしません。ユーザーの明示承認と前向き検証が必要です。",
        "- 100%以上の回収率を作るための条件後付けは行っていません。",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(text) + "\n", encoding="utf-8")
    return decision


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default="2026-06-25")
    parser.add_argument("--start-date", default="2026-05-01")
    parser.add_argument("--end-date", default="2026-06-25")
    parser.add_argument("--role-dataset", default="data/analysis/boat_role_dataset.csv")
    parser.add_argument("--race-dataset", default="data/analysis/race_dataset.csv")
    parser.add_argument("--out-dir", default="data/output/research_v2")
    parser.add_argument("--report-dir", default="reports/research_v2")
    args = parser.parse_args()
    role_groups = load_role_groups(ROOT / args.role_dataset)
    race_df = pd.read_csv(ROOT / args.race_dataset, low_memory=False)
    cluster_map = cluster_assignments(race_df, ROOT / "data/model/research_v2/manshu_cluster_model.pkl")
    lifts = cluster_lifts(ROOT / "reports/research_v2/manshu_cluster_profiles.csv")
    payloads = load_payloads(args.start_date, args.end_date)
    target_payload = next((payload for payload in payloads if payload.get("date") == args.date), payloads[-1])
    candidate_rows = candidate_rows_for_payload(target_payload, role_groups, cluster_map, lifts)
    out = {
        "version": "research-v2-candidate-ranking-1",
        "date": target_payload.get("date"),
        "generated_at": now_iso(),
        "production_unchanged": True,
        "model_version": "research-v2-candidate-ranking-1",
        "note": "研究用。manshu.htmlや既存ランキングJSONには接続しない。",
        "races": candidate_rows,
    }
    out_dir = ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"manshu_candidate_ranking_{str(target_payload.get('date')).replace('-', '')}.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    comparison = compare_rankings(payloads, role_groups, cluster_map, lifts)
    report_dir = ROOT / args.report_dir
    write_csv(report_dir / "candidate_ranking_comparison.csv", comparison)
    decision = write_report(report_dir / "candidate_ranking_comparison.md", comparison)
    (out_dir / "candidate_decision.json").write_text(
        json.dumps({"version": "research-v2-candidate-decision-1", "decision": decision, "generated_at": now_iso(), "comparison": comparison}, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"candidate_json": out_path.as_posix(), "decision": decision}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
