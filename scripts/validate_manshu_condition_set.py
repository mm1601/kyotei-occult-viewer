#!/usr/bin/env python3
"""Validate a saved manshu condition list against a race-level dataset."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Any

from analyze_manshu_deep_combos import atoms, as_int, fmt_num, fmt_pct, p_value, rate, time_split, wilson_ci


Row = dict[str, Any]


def read_dataset(path: Path) -> list[Row]:
    with path.open(encoding="utf-8", newline="") as handle:
        return [row for row in csv.DictReader(handle) if as_int(row.get("valid_for_analysis")) == 1]


def read_conditions(path: Path) -> list[Row]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def condition_parts(condition_name: str) -> list[str]:
    return [part.strip() for part in condition_name.split("×") if part.strip()]


def match_condition(row: Row, condition_name: str, atom_map: dict[str, Any]) -> bool:
    for part in condition_parts(condition_name):
        atom = atom_map.get(part)
        if atom is None:
            raise KeyError(f"unknown condition atom: {part}")
        if not atom.predicate(row):
            return False
    return True


def classify(new_rate: float, old_rate: float, baseline: float, valid_rate: float | None, valid_baseline: float) -> str:
    if new_rate >= old_rate - 0.05 and new_rate >= baseline * 1.8 and valid_rate is not None and valid_rate >= valid_baseline:
        return "維持"
    if new_rate >= baseline * 1.5 and valid_rate is not None and valid_rate >= valid_baseline:
        return "やや低下だが有効"
    return "要注意"


def evaluate(rows: list[Row], saved_conditions: list[Row]) -> tuple[list[Row], dict[str, Any]]:
    atom_map = {atom.name: atom for atom in atoms()}
    train_dates, valid_dates = time_split(rows)
    baseline_count = sum(as_int(row.get("manshu_flag")) for row in rows)
    baseline_rate = baseline_count / len(rows)
    valid_rows = [row for row in rows if row["date"] in valid_dates]
    valid_baseline_count = sum(as_int(row.get("manshu_flag")) for row in valid_rows)
    valid_baseline = valid_baseline_count / len(valid_rows)

    output: list[Row] = []
    for rank, saved in enumerate(saved_conditions, 1):
        condition_name = saved["condition_name"]
        matched = [row for row in rows if match_condition(row, condition_name, atom_map)]
        valid_matched = [row for row in matched if row["date"] in valid_dates]
        manshu_n = sum(as_int(row.get("manshu_flag")) for row in matched)
        valid_manshu_n = sum(as_int(row.get("manshu_flag")) for row in valid_matched)
        n = len(matched)
        valid_n = len(valid_matched)
        new_rate = rate(manshu_n, n) or 0.0
        old_rate = float(saved.get("manshu_rate") or 0.0)
        new_valid_rate = rate(valid_manshu_n, valid_n)
        ci_low, ci_high = wilson_ci(manshu_n, n)
        output.append(
            {
                "old_rank": rank,
                "condition_name": condition_name,
                "combo_size": len(condition_parts(condition_name)),
                "phase": saved.get("phase"),
                "old_n": saved.get("n"),
                "old_manshu_n": saved.get("manshu_n"),
                "old_manshu_rate": old_rate,
                "new_n": n,
                "new_manshu_n": manshu_n,
                "new_manshu_rate": new_rate,
                "new_diff_vs_old": new_rate - old_rate,
                "new_diff_vs_baseline": new_rate - baseline_rate,
                "new_lift": new_rate / baseline_rate if baseline_rate else None,
                "new_ci95_low": ci_low,
                "new_ci95_high": ci_high,
                "new_p_value": p_value(manshu_n, n, baseline_rate),
                "new_valid_n": valid_n,
                "new_valid_manshu_n": valid_manshu_n,
                "new_valid_rate": new_valid_rate,
                "new_valid_lift": new_valid_rate / valid_baseline if new_valid_rate is not None and valid_baseline else None,
                "judgment": classify(new_rate, old_rate, baseline_rate, new_valid_rate, valid_baseline),
            }
        )

    meta = {
        "row_count": len(rows),
        "manshu_count": baseline_count,
        "baseline_rate": baseline_rate,
        "valid_count": len(valid_rows),
        "valid_manshu_count": valid_baseline_count,
        "valid_baseline_rate": valid_baseline,
    }
    return output, meta


def write_csv(path: Path, rows: list[Row]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "old_rank",
        "condition_name",
        "combo_size",
        "phase",
        "old_n",
        "old_manshu_n",
        "old_manshu_rate",
        "new_n",
        "new_manshu_n",
        "new_manshu_rate",
        "new_diff_vs_old",
        "new_diff_vs_baseline",
        "new_lift",
        "new_ci95_low",
        "new_ci95_high",
        "new_p_value",
        "new_valid_n",
        "new_valid_manshu_n",
        "new_valid_rate",
        "new_valid_lift",
        "judgment",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fields})


def write_markdown(path: Path, rows: list[Row], meta: dict[str, Any], source_conditions: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 90日Top10条件の20k検証",
        "",
        "この検証は娯楽・研究・検証用です。舟券購入、利益、的中を推奨または保証するものではありません。",
        "",
        "## 前提",
        "",
        f"- 検証元条件: `{source_conditions}`",
        f"- 検証対象レース数: {meta['row_count']}",
        f"- 万舟数: {meta['manshu_count']}",
        f"- 全体万舟率: {fmt_pct(meta['baseline_rate'])}",
        f"- 後半検証レース数: {meta['valid_count']}",
        f"- 後半検証万舟率: {fmt_pct(meta['valid_baseline_rate'])}",
        "",
        "## 照合結果",
        "",
        "| 前回順位 | 判定 | 旧万舟率 | 新件数 | 新万舟数 | 新万舟率 | 差分 | 新リフト | 後半検証率 | 条件 |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        lines.append(
            "| {rank} | {judgment} | {old_rate} | {new_n} | {new_manshu_n} | {new_rate} | {diff} | {lift} | {valid_rate} | {condition} |".format(
                rank=row["old_rank"],
                judgment=row["judgment"],
                old_rate=fmt_pct(row["old_manshu_rate"]),
                new_n=row["new_n"],
                new_manshu_n=row["new_manshu_n"],
                new_rate=fmt_pct(row["new_manshu_rate"]),
                diff=fmt_pct(row["new_diff_vs_old"]),
                lift=fmt_num(row["new_lift"]),
                valid_rate=fmt_pct(row["new_valid_rate"]),
                condition=row["condition_name"],
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> int:
    rows = read_dataset(Path(args.dataset))
    conditions = read_conditions(Path(args.conditions))
    if args.limit:
        conditions = conditions[: args.limit]
    result_rows, meta = evaluate(rows, conditions)
    write_csv(Path(args.output_csv), result_rows)
    write_markdown(Path(args.output_md), result_rows, meta, Path(args.conditions))
    print(f"rows={meta['row_count']} baseline={meta['baseline_rate']:.4f} conditions={len(result_rows)}")
    print(f"wrote {args.output_csv}")
    print(f"wrote {args.output_md}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default="data/analysis/race_dataset.csv")
    parser.add_argument("--conditions", default="reports/manshu_combined_top10.csv")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--output-csv", default="reports/manshu_top10_20k_validation.csv")
    parser.add_argument("--output-md", default="reports/manshu_top10_20k_validation.md")
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
