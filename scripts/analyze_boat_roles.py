#!/usr/bin/env python3
"""Validate boat role assignments against historical race results.

This script evaluates role labels only. It does not change the production
prediction logic and does not treat any result-only field as a feature.
"""

from __future__ import annotations

import argparse
import csv
import math
from collections import defaultdict
from pathlib import Path
from typing import Any


Row = dict[str, Any]


USECOLS = {
    "race_id",
    "date",
    "jcd",
    "venue_name",
    "grade",
    "race_no",
    "time_zone",
    "payout_yen",
    "manshu_flag",
    "big_manshu_flag",
    "target_arare_flag",
    "mid_arare_flag",
    "existing_score",
    "chaos_score",
    "data_quality_score",
    "result_trifecta",
    "lane",
    "role_morning",
    "role_rank_morning",
    "skip_morning",
    "skip_reason_morning",
    "role_preview",
    "role_rank_preview",
    "skip_preview",
    "skip_reason_preview",
}


def as_float(value: Any) -> float | None:
    try:
        if value in (None, "", "-", "nan"):
            return None
        output = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(output) else output


def as_int(value: Any, default: int = 0) -> int:
    number = as_float(value)
    return default if number is None else int(number)


def pct(value: float | int | None) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "-"
    return f"{100 * float(value):.2f}%"


def yen(value: float | int | None) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "-"
    return f"{float(value):,.0f}円"


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else float("nan")


def ci95(successes: int, total: int) -> tuple[float, float]:
    if total <= 0:
        return (float("nan"), float("nan"))
    p = successes / total
    z = 1.96
    denom = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denom
    return max(0.0, center - margin), min(1.0, center + margin)


def parse_result(value: Any) -> list[int]:
    if not value:
        return []
    lanes: list[int] = []
    for part in str(value).replace(" ", "").split("-"):
        if part.isdigit():
            lane = int(part)
            if 1 <= lane <= 6:
                lanes.append(lane)
    return lanes[:3]


def load_groups(path: Path) -> dict[str, list[Row]]:
    groups: dict[str, list[Row]] = defaultdict(list)
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for source in reader:
            row = {key: source.get(key, "") for key in USECOLS}
            groups[row["race_id"]].append(row)
    return groups


def role_lanes(group: list[Row], mode: str, role: str) -> list[int]:
    role_col = f"role_{mode}"
    rank_col = f"role_rank_{mode}"
    lanes = [
        (as_int(row.get(rank_col), 9), as_int(row.get("lane")))
        for row in group
        if row.get(role_col) == role
    ]
    return [lane for _, lane in sorted(lanes)]


def race_role_summary(group: list[Row], mode: str) -> Row:
    row = group[0]
    result = parse_result(row.get("result_trifecta"))
    top3 = set(result)
    winner = result[0] if result else None
    head_lanes = role_lanes(group, mode, "head")
    axis_lanes = role_lanes(group, mode, "axis")
    toss_lanes = role_lanes(group, mode, "toss")
    opponent_lanes = role_lanes(group, mode, "opponent")
    toss_lane = toss_lanes[0] if toss_lanes else None
    head1_lane = head_lanes[0] if head_lanes else None
    axis1_lane = axis_lanes[0] if axis_lanes else None
    return {
        "race_id": row["race_id"],
        "date": row["date"],
        "jcd": str(row["jcd"]).zfill(2),
        "venue_name": row.get("venue_name"),
        "grade": row.get("grade"),
        "race_no": as_int(row.get("race_no")),
        "time_zone": row.get("time_zone"),
        "payout_yen": as_int(row.get("payout_yen")),
        "manshu_flag": as_int(row.get("manshu_flag")),
        "big_manshu_flag": as_int(row.get("big_manshu_flag")),
        "target_arare_flag": as_int(row.get("target_arare_flag")),
        "mid_arare_flag": as_int(row.get("mid_arare_flag")),
        "existing_score": as_float(row.get("existing_score")),
        "chaos_score": as_float(row.get("chaos_score")) or 0.0,
        "data_quality_score": as_float(row.get("data_quality_score")) or 0.0,
        "skip": as_int(row.get(f"skip_{mode}")),
        "skip_reason": row.get(f"skip_reason_{mode}", ""),
        "result_trifecta": row.get("result_trifecta"),
        "head_lanes": "-".join(str(v) for v in head_lanes),
        "axis_lanes": "-".join(str(v) for v in axis_lanes),
        "toss_lane": toss_lane,
        "opponent_lane": opponent_lanes[0] if opponent_lanes else None,
        "head1_lane": head1_lane,
        "axis1_lane": axis1_lane,
        "winner_lane": winner,
        "head1_win": int(head1_lane == winner),
        "head2_win": int(winner in head_lanes),
        "axis1_top3": int(axis1_lane in top3) if axis1_lane is not None else 0,
        "axis_any_top3": int(bool(top3.intersection(axis_lanes))),
        "axis_both_top3": int(len(top3.intersection(axis_lanes)) == 2),
        "toss_out_top3": int(toss_lane not in top3) if toss_lane is not None else 0,
        "toss_won": int(toss_lane == winner) if toss_lane is not None else 0,
        "toss_lane1": int(toss_lane == 1),
        "lane1_toss_success": int(toss_lane == 1 and 1 not in top3),
        "role_core_success": int((winner in head_lanes) and bool(top3.intersection(axis_lanes)) and (toss_lane not in top3)),
        "role_strict_success": int((winner in head_lanes) and (len(top3.intersection(axis_lanes)) == 2) and (toss_lane not in top3)),
    }


def summarize_metric(records: list[Row], metric: str, label: str, baseline: float | None = None) -> Row:
    total = len(records)
    successes = sum(as_int(row.get(metric)) for row in records)
    rate = successes / total if total else float("nan")
    lo, hi = ci95(successes, total)
    return {
        "label": label,
        "n": total,
        "success": successes,
        "rate": rate,
        "lift_vs_baseline": rate / baseline if baseline and baseline > 0 else float("nan"),
        "ci95_low": lo,
        "ci95_high": hi,
    }


def topk_per_day(records: list[Row], k: int, score_col: str = "chaos_score") -> list[Row]:
    by_date: dict[str, list[Row]] = defaultdict(list)
    for row in records:
        if as_float(row.get(score_col)) is not None:
            by_date[str(row.get("date"))].append(row)
    selected: list[Row] = []
    for rows in by_date.values():
        selected.extend(sorted(rows, key=lambda item: as_float(item.get(score_col)) or -1, reverse=True)[:k])
    return selected


def rate(records: list[Row], column: str) -> float:
    return mean([as_int(row.get(column)) for row in records])


def build_summaries(records: list[Row], mode: str) -> tuple[list[Row], list[Row]]:
    baseline_manshu = rate(records, "manshu_flag")
    baseline_target = rate(records, "target_arare_flag")
    rows: list[Row] = []
    scopes = [
        ("all_valid_races", records),
        ("skip_excluded", [row for row in records if as_int(row.get("skip")) == 0]),
        ("chaos_top3_per_day", topk_per_day(records, 3)),
        ("chaos_top5_per_day", topk_per_day(records, 5)),
        ("chaos_top10_per_day", topk_per_day(records, 10)),
        ("manshu_races_only_label_check", [row for row in records if as_int(row.get("manshu_flag")) == 1]),
        ("target_arare_races_only_label_check", [row for row in records if as_int(row.get("target_arare_flag")) == 1]),
    ]
    metrics = [
        ("head1_win", "頭1番手が1着", None),
        ("head2_win", "頭候補2艇のどちらかが1着", None),
        ("axis1_top3", "軸1番手が3着内", None),
        ("axis_any_top3", "軸候補2艇のどちらかが3着内", None),
        ("axis_both_top3", "軸候補2艇が両方3着内", None),
        ("toss_out_top3", "消し候補が3着外", None),
        ("role_core_success", "頭候補的中×軸1艇以上×消し成功", None),
        ("role_strict_success", "頭候補的中×軸2艇両方×消し成功", None),
        ("manshu_flag", "万舟率", baseline_manshu),
        ("target_arare_flag", "中荒れ以上率", baseline_target),
    ]
    for scope, scoped_records in scopes:
        for metric, label, base in metrics:
            item = summarize_metric(scoped_records, metric, label, base)
            item["mode"] = mode
            item["scope"] = scope
            rows.append(item)

    segment_rows: list[Row] = []
    for column, label in [
        ("venue_name", "場別"),
        ("race_no", "R番号別"),
        ("grade", "グレード別"),
        ("time_zone", "時間帯別"),
        ("toss_lane", "消し艇番別"),
        ("head1_lane", "頭1番手艇番別"),
    ]:
        grouped: dict[str, list[Row]] = defaultdict(list)
        for row in records:
            grouped[str(row.get(column))].append(row)
        for value, grouped_records in grouped.items():
            if len(grouped_records) < 30:
                continue
            payouts = [as_int(row.get("payout_yen")) for row in grouped_records]
            segment_rows.append(
                {
                    "mode": mode,
                    "segment": label,
                    "value": value,
                    "n": len(grouped_records),
                    "manshu_rate": rate(grouped_records, "manshu_flag"),
                    "target_arare_rate": rate(grouped_records, "target_arare_flag"),
                    "head2_win_rate": rate(grouped_records, "head2_win"),
                    "axis_any_top3_rate": rate(grouped_records, "axis_any_top3"),
                    "toss_success_rate": rate(grouped_records, "toss_out_top3"),
                    "role_core_success_rate": rate(grouped_records, "role_core_success"),
                    "avg_payout_yen": mean(payouts),
                }
            )
    return rows, segment_rows


def time_split_summary(records: list[Row], mode: str) -> list[Row]:
    dates = sorted({str(row.get("date")) for row in records})
    split_index = max(1, int(len(dates) * 0.7))
    split_date = dates[split_index - 1]
    rows: list[Row] = []
    for label, scoped in [
        (f"train_to_{split_date}", [row for row in records if str(row.get("date")) <= split_date]),
        (f"valid_after_{split_date}", [row for row in records if str(row.get("date")) > split_date]),
    ]:
        selections = [
            ("all", scoped),
            ("skip_excluded", [row for row in scoped if as_int(row.get("skip")) == 0]),
            ("chaos_top5_per_day", topk_per_day(scoped, 5)),
            ("chaos_top10_per_day", topk_per_day(scoped, 10)),
        ]
        for selection, selected in selections:
            rows.append(
                {
                    "mode": mode,
                    "period": label,
                    "selection": selection,
                    "n": len(selected),
                    "manshu_rate": rate(selected, "manshu_flag"),
                    "target_arare_rate": rate(selected, "target_arare_flag"),
                    "head2_win_rate": rate(selected, "head2_win"),
                    "axis_any_top3_rate": rate(selected, "axis_any_top3"),
                    "toss_success_rate": rate(selected, "toss_out_top3"),
                    "role_core_success_rate": rate(selected, "role_core_success"),
                    "role_strict_success_rate": rate(selected, "role_strict_success"),
                }
            )
    return rows


def existing_score_comparison(records: list[Row]) -> list[Row]:
    rows: list[Row] = []
    for score_col in ["chaos_score", "existing_score"]:
        usable = [row for row in records if as_float(row.get(score_col)) is not None]
        for k in [3, 5, 10]:
            selected = topk_per_day(usable, k, score_col)
            payouts = [as_int(row.get("payout_yen")) for row in selected]
            rows.append(
                {
                    "score": score_col,
                    "selection": f"top{k}_per_day",
                    "n": len(selected),
                    "manshu_rate": rate(selected, "manshu_flag"),
                    "target_arare_rate": rate(selected, "target_arare_flag"),
                    "avg_payout_yen": mean(payouts),
                    "max_payout_yen": max(payouts) if payouts else float("nan"),
                }
            )
    return rows


def write_csv(path: Path, rows: list[Row]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                fields.append(key)
                seen.add(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def format_value(column: str, value: Any) -> str:
    number = as_float(value)
    if number is not None:
        if "rate" in column or "ci95" in column:
            return pct(number)
        if "payout" in column:
            return yen(number)
        return f"{number:.3f}"
    return str(value)


def format_table(rows: list[Row], columns: list[str]) -> str:
    if not rows:
        return "_該当なし_\n"
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(format_value(column, row.get(column)) for column in columns) + " |")
    return "\n".join(lines) + "\n"


def write_report(path: Path, race_count: int, date_min: str, date_max: str, summary: list[Row], split: list[Row], comparison: list[Row]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    focus_labels = {
        "万舟率",
        "中荒れ以上率",
        "頭候補2艇のどちらかが1着",
        "軸候補2艇のどちらかが3着内",
        "消し候補が3着外",
        "頭候補的中×軸1艇以上×消し成功",
    }
    focus_scopes = {"all_valid_races", "skip_excluded", "chaos_top5_per_day", "chaos_top10_per_day"}
    focus = [
        row
        for row in summary
        if row.get("scope") in focus_scopes and row.get("label") in focus_labels
    ]
    for row in focus:
        row["rate_text"] = pct(as_float(row.get("rate")))
        row["ci95_text"] = f"{pct(as_float(row.get('ci95_low')))} - {pct(as_float(row.get('ci95_high')))}"
    split_focus = [row for row in split if row.get("selection") in {"all", "chaos_top5_per_day", "chaos_top10_per_day"}]
    text = [
        "# Boat Role Validation",
        "",
        "このレポートは、既存ロジックを変更せず、艇別の役割分類を検証するための分析です。舟券購入を推奨するものではありません。",
        "",
        f"- 期間: {date_min} - {date_max}",
        f"- 対象レース数: {race_count:,}",
        "- 単位: 1レース内で `head` 2艇、`axis` 2艇、`toss` 1艇、`opponent` 1艇",
        "- データリーク防止: 着順、払戻、人気、結果は検証ラベルとしてのみ使用",
        "",
        "## Role Success Summary",
        "",
        format_table(focus, ["mode", "scope", "label", "n", "success", "rate_text", "ci95_text"]),
        "## Time Split Validation",
        "",
        format_table(
            split_focus,
            [
                "mode",
                "period",
                "selection",
                "n",
                "manshu_rate",
                "target_arare_rate",
                "head2_win_rate",
                "axis_any_top3_rate",
                "toss_success_rate",
                "role_core_success_rate",
            ],
        ),
        "## Existing Score Comparison",
        "",
        "既存スコアは欠損が多いため、比較可能なレース内での参考値です。新しい `chaos_score` は分析用で、本番置換ではありません。",
        "",
        format_table(comparison, ["score", "selection", "n", "manshu_rate", "target_arare_rate", "avg_payout_yen", "max_payout_yen"]),
        "## Interpretation",
        "",
        "- `toss` の3着外率は高く出やすい一方、1艇を消す設計なので、消し候補が勝つケースはフォーメーション全体の失敗に直結します。",
        "- `head` 2艇の1着率と `axis` の3着内率を同時に満たす `role_core_success` が、買い目ではなく役割分類として見るべき中心指標です。",
        "- 朝版と直前版は別指標です。直前版は展示・気象が入るため、朝版の判断へ混ぜない前提で扱います。",
    ]
    path.write_text("\n".join(text) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> int:
    groups = load_groups(Path(args.dataset))
    all_summary: list[Row] = []
    all_segments: list[Row] = []
    all_split: list[Row] = []
    comparison: list[Row] = []
    for mode in ["morning", "preview"]:
        records = [race_role_summary(group, mode) for group in groups.values()]
        summary, segments = build_summaries(records, mode)
        all_summary.extend(summary)
        all_segments.extend(segments)
        all_split.extend(time_split_summary(records, mode))
        if mode == "preview":
            comparison = existing_score_comparison(records)
    dates = [row[0].get("date", "") for row in groups.values() if row]
    write_csv(Path(args.summary_csv), all_summary)
    write_csv(Path(args.segment_csv), all_segments)
    write_csv(Path(args.time_split_csv), all_split)
    write_csv(Path(args.score_comparison_csv), comparison)
    write_report(Path(args.report), len(groups), min(dates), max(dates), all_summary, all_split, comparison)
    print(f"wrote {args.report}")
    print(f"wrote {args.summary_csv}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default="data/analysis/boat_role_dataset.csv")
    parser.add_argument("--report", default="reports/boat_role_validation.md")
    parser.add_argument("--summary-csv", default="reports/boat_role_validation_summary.csv")
    parser.add_argument("--segment-csv", default="reports/boat_role_validation_segments.csv")
    parser.add_argument("--time-split-csv", default="reports/boat_role_time_split.csv")
    parser.add_argument("--score-comparison-csv", default="reports/role_score_comparison.csv")
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
