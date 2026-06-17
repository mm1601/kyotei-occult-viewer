#!/usr/bin/env python3
"""Backtest fixed formations built from boat role assignments.

The formation definitions are fixed in this file before evaluation. They are
for historical validation only and are not a purchase recommendation.
"""

from __future__ import annotations

import argparse
import csv
import itertools
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
    "payout_yen",
    "manshu_flag",
    "big_manshu_flag",
    "target_arare_flag",
    "mid_arare_flag",
    "chaos_score",
    "existing_score",
    "result_trifecta",
    "lane",
    "role_morning",
    "role_rank_morning",
    "skip_morning",
    "role_preview",
    "role_rank_preview",
    "skip_preview",
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


def median(values: list[float]) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def load_groups(path: Path) -> dict[str, list[Row]]:
    groups: dict[str, list[Row]] = defaultdict(list)
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for source in reader:
            row = {key: source.get(key, "") for key in USECOLS}
            groups[row["race_id"]].append(row)
    return groups


def parse_result(value: Any) -> tuple[int, int, int] | None:
    if not value:
        return None
    lanes: list[int] = []
    for part in str(value).replace(" ", "").split("-"):
        if part.isdigit():
            lane = int(part)
            if 1 <= lane <= 6:
                lanes.append(lane)
    return tuple(lanes[:3]) if len(lanes) >= 3 else None


def role_lanes(group: list[Row], mode: str) -> dict[str, list[int]]:
    role_col = f"role_{mode}"
    rank_col = f"role_rank_{mode}"
    roles: dict[str, list[tuple[int, int]]] = {"head": [], "axis": [], "toss": [], "opponent": []}
    for row in group:
        role = str(row.get(role_col))
        if role in roles:
            roles[role].append((as_int(row.get(rank_col), 9), as_int(row.get("lane"))))
    return {role: [lane for _, lane in sorted(values)] for role, values in roles.items()}


def combos(first: list[int], second: list[int], third: list[int], toss: list[int]) -> set[tuple[int, int, int]]:
    toss_set = set(toss)
    output: set[tuple[int, int, int]] = set()
    for a, b, c in itertools.product(first, second, third):
        if len({a, b, c}) < 3:
            continue
        if toss_set.intersection({a, b, c}):
            continue
        output.add((a, b, c))
    return output


def formation_combos(roles: dict[str, list[int]], name: str) -> set[tuple[int, int, int]]:
    heads = roles.get("head", [])
    axes = roles.get("axis", [])
    toss = roles.get("toss", [])
    opponent = roles.get("opponent", [])
    support = heads + axes + opponent
    no_toss = [lane for lane in support if lane not in toss]
    if name == "A":
        return combos(heads, no_toss, no_toss, toss)
    if name == "B":
        return combos(heads, axes + opponent, no_toss, toss)
    if name == "C":
        return combos(heads, axes, no_toss, toss)
    if name == "D":
        return combos(heads[:1], heads[1:] + axes, no_toss, toss)
    raise ValueError(f"unknown formation: {name}")


def race_summary(group: list[Row], mode: str) -> Row:
    row = group[0]
    roles = role_lanes(group, mode)
    result = parse_result(row.get("result_trifecta"))
    record: Row = {
        "race_id": row["race_id"],
        "date": row["date"],
        "jcd": str(row["jcd"]).zfill(2),
        "venue_name": row.get("venue_name"),
        "grade": row.get("grade"),
        "race_no": as_int(row.get("race_no")),
        "payout_yen": as_int(row.get("payout_yen")),
        "manshu_flag": as_int(row.get("manshu_flag")),
        "big_manshu_flag": as_int(row.get("big_manshu_flag")),
        "target_arare_flag": as_int(row.get("target_arare_flag")),
        "mid_arare_flag": as_int(row.get("mid_arare_flag")),
        "chaos_score": as_float(row.get("chaos_score")) or 0.0,
        "existing_score": as_float(row.get("existing_score")),
        "skip": as_int(row.get(f"skip_{mode}")),
        "result": result,
    }
    for form in ["A", "B", "C", "D"]:
        tickets = formation_combos(roles, form)
        hit = result in tickets if result else False
        payout = record["payout_yen"]
        record[f"{form}_points"] = len(tickets)
        record[f"{form}_hit"] = int(hit)
        record[f"{form}_hit_payout_yen"] = payout if hit else 0
        record[f"{form}_hit_manshu"] = int(hit and payout >= 10000)
        record[f"{form}_hit_target_arare"] = int(hit and payout >= 5000)
        record[f"{form}_return_per_100yen"] = payout if hit else 0
    return record


def topk_per_day(records: list[Row], k: int, score_col: str = "chaos_score") -> list[Row]:
    by_date: dict[str, list[Row]] = defaultdict(list)
    for row in records:
        if as_float(row.get(score_col)) is not None:
            by_date[str(row.get("date"))].append(row)
    selected: list[Row] = []
    for rows in by_date.values():
        selected.extend(sorted(rows, key=lambda item: as_float(item.get(score_col)) or -1, reverse=True)[:k])
    return selected


def aggregate(records: list[Row], mode: str, scope: str, form: str) -> Row:
    n = len(records)
    hits = sum(as_int(row.get(f"{form}_hit")) for row in records)
    manshu_hits = sum(as_int(row.get(f"{form}_hit_manshu")) for row in records)
    target_hits = sum(as_int(row.get(f"{form}_hit_target_arare")) for row in records)
    total_manshu = sum(as_int(row.get("manshu_flag")) for row in records)
    total_target = sum(as_int(row.get("target_arare_flag")) for row in records)
    points = [as_int(row.get(f"{form}_points")) for row in records]
    hit_payouts = [as_int(row.get("payout_yen")) for row in records if as_int(row.get(f"{form}_hit"))]
    points_sum = sum(points)
    return {
        "mode": mode,
        "scope": scope,
        "formation": form,
        "n": n,
        "avg_points": mean(points),
        "hit_count": hits,
        "hit_rate": hits / n if n else float("nan"),
        "manshu_hit_count": manshu_hits,
        "manshu_hit_rate": manshu_hits / n if n else float("nan"),
        "manshu_capture_rate": manshu_hits / total_manshu if total_manshu else float("nan"),
        "target_arare_hit_count": target_hits,
        "target_arare_hit_rate": target_hits / n if n else float("nan"),
        "target_arare_capture_rate": target_hits / total_target if total_target else float("nan"),
        "avg_hit_payout_yen": mean(hit_payouts),
        "median_hit_payout_yen": median(hit_payouts),
        "max_hit_payout_yen": max(hit_payouts) if hit_payouts else float("nan"),
        "reference_return_rate_100yen_each": sum(as_int(row.get(f"{form}_return_per_100yen")) for row in records) / (points_sum * 100) if points_sum else float("nan"),
    }


def time_split(records: list[Row], mode: str) -> list[Row]:
    dates = sorted({str(row.get("date")) for row in records})
    split_index = max(1, int(len(dates) * 0.7))
    split_date = dates[split_index - 1]
    rows: list[Row] = []
    for period, period_records in [
        (f"train_to_{split_date}", [row for row in records if str(row.get("date")) <= split_date]),
        (f"valid_after_{split_date}", [row for row in records if str(row.get("date")) > split_date]),
    ]:
        for scope, scoped in [
            ("all", period_records),
            ("skip_excluded", [row for row in period_records if as_int(row.get("skip")) == 0]),
            ("chaos_top5_per_day", topk_per_day(period_records, 5)),
            ("chaos_top10_per_day", topk_per_day(period_records, 10)),
        ]:
            for form in ["A", "B", "C", "D"]:
                item = aggregate(scoped, mode, scope, form)
                item["period"] = period
                rows.append(item)
    return rows


def build_outputs(groups: dict[str, list[Row]], mode: str) -> tuple[list[Row], list[Row], list[Row]]:
    records = [race_summary(group, mode) for group in groups.values()]
    summary: list[Row] = []
    for scope, scoped in [
        ("all", records),
        ("skip_excluded", [row for row in records if as_int(row.get("skip")) == 0]),
        ("chaos_top3_per_day", topk_per_day(records, 3)),
        ("chaos_top5_per_day", topk_per_day(records, 5)),
        ("chaos_top10_per_day", topk_per_day(records, 10)),
        ("existing_top10_per_day", topk_per_day([row for row in records if as_float(row.get("existing_score")) is not None], 10, "existing_score")),
    ]:
        for form in ["A", "B", "C", "D"]:
            summary.append(aggregate(scoped, mode, scope, form))
    return summary, time_split(records, mode), records


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
        if "rate" in column:
            return pct(number)
        if "payout" in column:
            return yen(number)
        if "points" in column:
            return f"{number:.1f}"
        return f"{number:.3f}"
    return str(value)


def format_table(rows: list[Row], columns: list[str]) -> str:
    if not rows:
        return "_該当なし_\n"
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(format_value(column, row.get(column)) for column in columns) + " |")
    return "\n".join(lines) + "\n"


def write_report(path: Path, summary: list[Row], split: list[Row], race_count: int, date_min: str, date_max: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    focus = [row for row in summary if row.get("scope") in {"all", "chaos_top5_per_day", "chaos_top10_per_day", "existing_top10_per_day"}]
    split_focus = [row for row in split if row.get("scope") in {"all", "chaos_top10_per_day"}]
    text = [
        "# Formation Backtest",
        "",
        "このレポートは、固定した役割フォーメーションA-Dの過去照合です。舟券購入を推奨するものではありません。回収率風の列は100円均等仮定の参考値であり、利益化の根拠には使いません。",
        "",
        f"- 期間: {date_min} - {date_max}",
        f"- 対象レース数: {race_count:,}",
        "- A: 頭2艇を1着、消し以外5艇を2・3着",
        "- B: 頭2艇を1着、軸2艇+残り相手を2着、消し以外5艇を3着",
        "- C: 頭2艇を1着、軸2艇を2着、消し以外5艇を3着",
        "- D: 頭1番手を1着、頭2番手+軸2艇を2着、消し以外5艇を3着",
        "",
        "## Overall And Ranked Backtest",
        "",
        format_table(
            focus,
            [
                "mode",
                "scope",
                "formation",
                "n",
                "avg_points",
                "hit_rate",
                "manshu_hit_rate",
                "manshu_capture_rate",
                "target_arare_hit_rate",
                "target_arare_capture_rate",
                "avg_hit_payout_yen",
                "max_hit_payout_yen",
                "reference_return_rate_100yen_each",
            ],
        ),
        "## Time Split",
        "",
        format_table(
            split_focus,
            [
                "mode",
                "period",
                "scope",
                "formation",
                "n",
                "hit_rate",
                "manshu_hit_rate",
                "manshu_capture_rate",
                "target_arare_hit_rate",
                "reference_return_rate_100yen_each",
            ],
        ),
        "## Reading Notes",
        "",
        "- Aは点数が多く拾いやすい一方、検証用の網が広いです。",
        "- C/Dは点数を絞るため、的中率と万舟捕捉率が落ちる代わりに説明しやすいです。",
        "- A-D定義はこのスクリプトで固定して評価しています。ここから数字を見て形を変える場合は別バージョンとして扱い、前向きログで再検証してください。",
    ]
    path.write_text("\n".join(text) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> int:
    groups = load_groups(Path(args.dataset))
    all_summary: list[Row] = []
    all_split: list[Row] = []
    dates = [group[0].get("date", "") for group in groups.values() if group]
    for mode in ["morning", "preview"]:
        summary, split, _records = build_outputs(groups, mode)
        all_summary.extend(summary)
        all_split.extend(split)
    write_csv(Path(args.summary_csv), all_summary)
    write_csv(Path(args.time_split_csv), all_split)
    write_report(Path(args.report), all_summary, all_split, len(groups), min(dates), max(dates))
    print(f"wrote {args.report}")
    print(f"wrote {args.summary_csv}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default="data/analysis/boat_role_dataset.csv")
    parser.add_argument("--report", default="reports/formation_backtest.md")
    parser.add_argument("--summary-csv", default="reports/formation_backtest_summary.csv")
    parser.add_argument("--time-split-csv", default="reports/formation_backtest_time_split.csv")
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
