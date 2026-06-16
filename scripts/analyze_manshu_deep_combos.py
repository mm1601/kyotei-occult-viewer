#!/usr/bin/env python3
"""Search 5- and 6-condition manshu patterns.

This is deliberately exploratory. It uses the existing race-level dataset,
keeps result-only fields out of predicates, and reports train/validation
rates with a time split so overfit-looking combinations are visible.
"""

from __future__ import annotations

import argparse
import csv
import math
import statistics
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Any, Callable


Row = dict[str, Any]
Predicate = Callable[[Row], bool]


@dataclass(frozen=True)
class Atom:
    name: str
    family: str
    phase: str
    predicate: Predicate


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def as_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def read_rows(path: Path) -> list[Row]:
    with path.open(encoding="utf-8", newline="") as handle:
        return [row for row in csv.DictReader(handle) if as_int(row.get("valid_for_analysis")) == 1]


def time_split(rows: list[Row], train_ratio: float = 0.7) -> tuple[set[str], set[str]]:
    dates = sorted({row["date"] for row in rows})
    cut_index = max(1, min(len(dates) - 1, int(len(dates) * train_ratio)))
    return set(dates[:cut_index]), set(dates[cut_index:])


def normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def wilson_ci(success: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total == 0:
        return 0.0, 0.0
    p = success / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denominator
    return max(0.0, center - margin), min(1.0, center + margin)


def p_value(success: int, total: int, baseline_rate: float) -> float | None:
    if total == 0 or baseline_rate in (0, 1):
        return None
    p = success / total
    se = math.sqrt(baseline_rate * (1 - baseline_rate) / total)
    if se == 0:
        return None
    z = (p - baseline_rate) / se
    return 2 * (1 - normal_cdf(abs(z)))


def bit_indices(mask: int) -> list[int]:
    indices: list[int] = []
    while mask:
        low = mask & -mask
        indices.append(low.bit_length() - 1)
        mask ^= low
    return indices


def avg(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def rate(success: int, total: int) -> float | None:
    return success / total if total else None


def fmt_pct(value: Any) -> str:
    if value is None or value == "":
        return ""
    return f"{float(value) * 100:.2f}%"


def fmt_num(value: Any, digits: int = 2) -> str:
    if value is None or value == "":
        return ""
    return f"{float(value):.{digits}f}"


def atoms() -> list[Atom]:
    return [
        Atom("荒れ寄り場(桐生/戸田/江戸川/三国)", "venue", "morning", lambda r: str(r.get("jcd")).zfill(2) in {"01", "02", "03", "10"}),
        Atom("場=江戸川", "venue", "morning", lambda r: str(r.get("jcd")).zfill(2) == "03"),
        Atom("場=三国", "venue", "morning", lambda r: str(r.get("jcd")).zfill(2) == "10"),
        Atom("場=桐生", "venue", "morning", lambda r: str(r.get("jcd")).zfill(2) == "01"),
        Atom("場=戸田", "venue", "morning", lambda r: str(r.get("jcd")).zfill(2) == "02"),
        Atom("場=浜名湖", "venue", "morning", lambda r: str(r.get("jcd")).zfill(2) == "06"),
        Atom("グレードSG/G2", "grade", "morning", lambda r: str(r.get("grade")) in {"SG", "G2"}),
        Atom("グレードSG", "grade", "morning", lambda r: str(r.get("grade")) == "SG"),
        Atom("2/3/6/10R", "race_slot", "morning", lambda r: as_int(r.get("race_no")) in {2, 3, 6, 10}),
        Atom("2Rまたは6R", "race_slot", "morning", lambda r: as_int(r.get("race_no")) in {2, 6}),
        Atom("早いレース(1-4R)", "race_slot", "morning", lambda r: as_int(r.get("early_race")) == 1),
        Atom("デイ時間帯", "time_zone", "morning", lambda r: str(r.get("time_zone")) == "day"),
        Atom("ナイター/ミッドナイト", "time_zone", "morning", lambda r: str(r.get("time_zone")) in {"night", "midnight"}),
        Atom("1号艇A1でない", "lane1_class", "morning", lambda r: as_int(r.get("lane1_not_a1")) == 1),
        Atom("1号艇B級", "lane1_class", "morning", lambda r: as_int(r.get("lane1_b_class")) == 1),
        Atom("1号艇全国勝率<5.0", "lane1_national", "morning", lambda r: (as_float(r.get("lane1_national_win_rate")) or 99) < 5.0),
        Atom("1号艇全国勝率<4.5", "lane1_national", "morning", lambda r: (as_float(r.get("lane1_national_win_rate")) or 99) < 4.5),
        Atom("1号艇当地勝率<5.0", "lane1_local", "morning", lambda r: (as_float(r.get("lane1_local_win_rate")) or 99) < 5.0),
        Atom("1号艇が外枠最強艇より勝率低い", "lane1_relative", "morning", lambda r: (as_float(r.get("lane1_vs_best_outer_win_diff")) or 99) < 0),
        Atom("1号艇が全体平均以下", "lane1_relative", "morning", lambda r: (as_float(r.get("lane1_vs_avg_win_diff")) or 99) <= 0),
        Atom("勝率レンジ<=1.5", "balance", "morning", lambda r: (as_float(r.get("national_win_range")) or 99) <= 1.5),
        Atom("勝率レンジ<=2.0", "balance", "morning", lambda r: (as_float(r.get("national_win_range")) or 99) <= 2.0),
        Atom("当地勝率レンジ<=2.0", "local_balance", "morning", lambda r: (as_float(r.get("local_win_range")) or 99) <= 2.0),
        Atom("外枠A級2人以上", "outer_class", "morning", lambda r: as_int(r.get("outer_a_count")) >= 2),
        Atom("外枠A1あり", "outer_class", "morning", lambda r: as_int(r.get("outer_a1_count")) >= 1),
        Atom("外枠A1が2人以上", "outer_class", "morning", lambda r: as_int(r.get("outer_a1_count")) >= 2),
        Atom("B級4人以上", "b_count", "morning", lambda r: as_int(r.get("b_count")) >= 4),
        Atom("B級3人以上", "b_count", "morning", lambda r: as_int(r.get("b_count")) >= 3),
        Atom("A1が1人以下", "a1_count", "morning", lambda r: as_int(r.get("a1_count")) <= 1),
        Atom("外枠モーター強者あり", "outer_motor", "morning", lambda r: as_int(r.get("outer_motor_strong_flag")) == 1),
        Atom("モーター2連率レンジ>=25", "motor_range", "morning", lambda r: (as_float(r.get("motor_quinella_range")) or 0) >= 25),
        Atom("進入固定ではない", "entry_rule", "morning", lambda r: as_int(r.get("fixed_entry")) == 0),
        Atom("風速5m以上", "wind", "preview", lambda r: (as_float(r.get("wind_speed_m")) or 0) >= 5),
        Atom("風速6m以上", "wind", "preview", lambda r: (as_float(r.get("wind_speed_m")) or 0) >= 6),
        Atom("波高5cm以上", "wave", "preview", lambda r: (as_float(r.get("wave_cm")) or 0) >= 5),
        Atom("波高8cm以上", "wave", "preview", lambda r: (as_float(r.get("wave_cm")) or 0) >= 8),
        Atom("1号艇展示4位以下", "lane1_exhibition", "preview", lambda r: as_int(r.get("lane1_exhibition_rank4plus")) == 1),
        Atom("外枠展示上位あり", "outer_exhibition", "preview", lambda r: as_int(r.get("outer_exhibition_top_flag")) == 1),
        Atom("外枠が1号艇より展示上位", "outer_exhibition", "preview", lambda r: as_int(r.get("outer_exhibition_beats_lane1")) == 1),
        Atom("展示タイム差>=0.08", "exhibition_range", "preview", lambda r: (as_float(r.get("exhibition_time_range")) or 0) >= 0.08),
    ]


def build_masks(rows: list[Row], atom_list: list[Atom]) -> dict[int, int]:
    masks: dict[int, int] = {}
    for idx, atom in enumerate(atom_list):
        mask = 0
        for row_index, row in enumerate(rows):
            if atom.predicate(row):
                mask |= 1 << row_index
        masks[idx] = mask
    return masks


def combo_phase(atom_list: list[Atom], combo: tuple[int, ...]) -> str:
    return "直前版" if any(atom_list[index].phase == "preview" for index in combo) else "朝版"


def score_priority(stat: dict[str, Any], valid_baseline: float, min_valid: int) -> str:
    valid_rate = stat["valid_rate"] or 0.0
    train_rate = stat["train_rate"] or 0.0
    if stat["valid_n"] >= max(50, min_valid) and valid_rate >= valid_baseline + 0.05 and train_rate >= stat["baseline_rate"]:
        return "高"
    if stat["valid_n"] >= min_valid and valid_rate >= valid_baseline + 0.025 and train_rate >= stat["baseline_rate"]:
        return "中"
    return "参考"


def evaluate_combo(
    rows: list[Row],
    atom_list: list[Atom],
    combo: tuple[int, ...],
    mask: int,
    train_mask: int,
    valid_mask: int,
    manshu_mask: int,
    baseline_rate: float,
    train_baseline: float,
    valid_baseline: float,
    min_valid: int,
) -> dict[str, Any]:
    n = mask.bit_count()
    manshu_n = (mask & manshu_mask).bit_count()
    train_combo = mask & train_mask
    valid_combo = mask & valid_mask
    train_n = train_combo.bit_count()
    valid_n = valid_combo.bit_count()
    train_manshu = (train_combo & manshu_mask).bit_count()
    valid_manshu = (valid_combo & manshu_mask).bit_count()
    manshu_rate = rate(manshu_n, n) or 0.0
    valid_rate = rate(valid_manshu, valid_n)
    ci_low, ci_high = wilson_ci(manshu_n, n)
    condition_name = " × ".join(atom_list[index].name for index in combo)
    matched_payouts = [as_float(rows[index].get("payout_yen")) or 0.0 for index in bit_indices(mask)]
    stat = {
        "combo_size": len(combo),
        "phase": combo_phase(atom_list, combo),
        "condition_name": condition_name,
        "n": n,
        "manshu_n": manshu_n,
        "manshu_rate": manshu_rate,
        "diff_vs_baseline": manshu_rate - baseline_rate,
        "lift": manshu_rate / baseline_rate if baseline_rate else None,
        "ci95_low": ci_low,
        "ci95_high": ci_high,
        "p_value": p_value(manshu_n, n, baseline_rate),
        "train_n": train_n,
        "train_manshu_n": train_manshu,
        "train_rate": rate(train_manshu, train_n),
        "valid_n": valid_n,
        "valid_manshu_n": valid_manshu,
        "valid_rate": valid_rate,
        "valid_diff_vs_baseline": (valid_rate - valid_baseline) if valid_rate is not None else None,
        "valid_lift": (valid_rate / valid_baseline) if valid_rate is not None and valid_baseline else None,
        "mean_payout": avg(matched_payouts),
        "median_payout": statistics.median(matched_payouts) if matched_payouts else None,
        "max_payout": max(matched_payouts) if matched_payouts else None,
        "baseline_rate": baseline_rate,
        "train_baseline": train_baseline,
        "valid_baseline": valid_baseline,
        "families": ",".join(atom_list[index].family for index in combo),
    }
    stat["priority"] = score_priority(stat, valid_baseline, min_valid)
    if valid_n < min_valid:
        stat["reproducibility"] = "検証件数不足"
    elif valid_rate is not None and valid_rate >= valid_baseline and (stat["train_rate"] or 0.0) >= train_baseline:
        stat["reproducibility"] = "再現あり"
    elif valid_rate is not None and valid_rate >= valid_baseline:
        stat["reproducibility"] = "検証のみ上振れ"
    else:
        stat["reproducibility"] = "検証で低下"
    return stat


def search_combos(
    rows: list[Row],
    sizes: list[int],
    min_count: int,
    min_valid: int,
    max_rows: int,
    sort_by: str,
    keep_duplicate_matches: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    atom_list = atoms()
    masks = build_masks(rows, atom_list)
    all_mask = (1 << len(rows)) - 1
    manshu_mask = 0
    for idx, row in enumerate(rows):
        if as_int(row.get("manshu_flag")) == 1:
            manshu_mask |= 1 << idx
    train_dates, valid_dates = time_split(rows)
    train_mask = 0
    valid_mask = 0
    for idx, row in enumerate(rows):
        if row["date"] in train_dates:
            train_mask |= 1 << idx
        elif row["date"] in valid_dates:
            valid_mask |= 1 << idx
    baseline_rate = (manshu_mask & all_mask).bit_count() / len(rows)
    train_baseline = (manshu_mask & train_mask).bit_count() / train_mask.bit_count()
    valid_baseline = (manshu_mask & valid_mask).bit_count() / valid_mask.bit_count()

    results: list[dict[str, Any]] = []
    seen_masks: set[int] = set()
    for size in sizes:
        for combo in combinations(range(len(atom_list)), size):
            families = [atom_list[index].family for index in combo]
            if len(families) != len(set(families)):
                continue
            mask = all_mask
            for index in combo:
                mask &= masks[index]
                if mask.bit_count() < min_count:
                    break
            n = mask.bit_count()
            if n < min_count:
                continue
            valid_n = (mask & valid_mask).bit_count()
            if valid_n < min_valid:
                continue
            if not keep_duplicate_matches:
                if mask in seen_masks:
                    continue
                seen_masks.add(mask)
            results.append(
                evaluate_combo(
                    rows,
                    atom_list,
                    combo,
                    mask,
                    train_mask,
                    valid_mask,
                    manshu_mask,
                    baseline_rate,
                    train_baseline,
                    valid_baseline,
                    min_valid,
                )
            )
    if sort_by == "manshu_rate":
        sort_key = lambda row: (
            row.get("manshu_rate") or 0.0,
            row.get("valid_rate") or 0.0,
            row.get("n") or 0,
        )
    elif sort_by == "valid_rate":
        sort_key = lambda row: (
            row.get("valid_rate") or 0.0,
            row.get("valid_n") or 0,
            row.get("manshu_rate") or 0.0,
        )
    elif sort_by == "lift":
        sort_key = lambda row: (
            row.get("lift") or 0.0,
            row.get("manshu_rate") or 0.0,
            row.get("valid_rate") or 0.0,
        )
    else:
        sort_key = lambda row: (
            row["priority"] == "高",
            row["reproducibility"] == "再現あり",
            row.get("valid_rate") or 0.0,
            row.get("valid_n") or 0,
            row.get("manshu_rate") or 0.0,
        )
    results.sort(key=sort_key, reverse=True)
    meta = {
        "row_count": len(rows),
        "manshu_count": manshu_mask.bit_count(),
        "baseline_rate": baseline_rate,
        "train_count": train_mask.bit_count(),
        "train_baseline": train_baseline,
        "valid_count": valid_mask.bit_count(),
        "valid_baseline": valid_baseline,
        "atom_count": len(atom_list),
        "result_count": len(results),
        "duplicate_matches": "kept" if keep_duplicate_matches else "deduped",
        "sizes": ",".join(str(size) for size in sizes),
        "min_count": min_count,
        "min_valid": min_valid,
        "sort_by": sort_by,
    }
    return results[:max_rows], meta


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "combo_size",
        "phase",
        "condition_name",
        "n",
        "manshu_n",
        "manshu_rate",
        "diff_vs_baseline",
        "lift",
        "ci95_low",
        "ci95_high",
        "p_value",
        "train_n",
        "train_manshu_n",
        "train_rate",
        "valid_n",
        "valid_manshu_n",
        "valid_rate",
        "valid_diff_vs_baseline",
        "valid_lift",
        "mean_payout",
        "median_payout",
        "max_payout",
        "reproducibility",
        "priority",
        "families",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fields})


def markdown_table(rows: list[dict[str, Any]], limit: int = 15) -> list[str]:
    lines = [
        "| 条件 | 版 | 件数 | 万舟数 | 万舟率 | 検証件数 | 検証万舟率 | リフト | 評価 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows[:limit]:
        lines.append(
            "| {condition} | {phase} | {n} | {manshu_n} | {rate} | {valid_n} | {valid_rate} | {lift} | {priority} |".format(
                condition=row["condition_name"],
                phase=row["phase"],
                n=row["n"],
                manshu_n=row["manshu_n"],
                rate=fmt_pct(row["manshu_rate"]),
                valid_n=row["valid_n"],
                valid_rate=fmt_pct(row["valid_rate"]),
                lift=fmt_num(row["lift"]),
                priority=row["priority"],
            )
        )
    return lines


def write_markdown(path: Path, rows: list[dict[str, Any]], meta: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    high = [row for row in rows if row["priority"] == "高"]
    medium = [row for row in rows if row["priority"] == "中"]
    five = [row for row in rows if row["combo_size"] == 5]
    six = [row for row in rows if row["combo_size"] == 6]
    morning = [row for row in rows if row["phase"] == "朝版"]
    preview = [row for row in rows if row["phase"] == "直前版"]
    lines = [
        "# 複合条件 万舟率Top10",
        "",
        "この探索は娯楽・研究・検証用です。舟券購入、利益、的中を推奨または保証するものではありません。",
        "",
        "## 前提",
        "",
        f"- 対象レース数: {meta['row_count']}",
        f"- 万舟数: {meta['manshu_count']}",
        f"- 全体万舟率: {fmt_pct(meta['baseline_rate'])}",
        f"- 学習期間: {meta['train_count']}レース / {fmt_pct(meta['train_baseline'])}",
        f"- 検証期間: {meta['valid_count']}レース / {fmt_pct(meta['valid_baseline'])}",
        f"- 探索候補条件数: {meta['atom_count']}",
        f"- 条件数: {meta['sizes']}",
        f"- 最低件数: {meta['min_count']}レース",
        f"- 検証最低件数目安: {meta['min_valid']}レース",
        f"- 並び順: {meta['sort_by']}",
        f"- 同一対象レース集合: {meta['duplicate_matches']}",
        f"- 件数下限を満たした組み合わせ数: {meta['result_count']}",
        "",
        "## 万舟率Top10",
        "",
        *markdown_table(rows, 10),
        "",
        "## 高評価",
        "",
        *markdown_table(high, 15),
        "",
        "## 中評価",
        "",
        *markdown_table(medium, 20),
        "",
        "## 5条件 上位",
        "",
        *markdown_table(five, 20),
        "",
        "## 6条件 上位",
        "",
        *markdown_table(six, 20),
        "",
        "## 朝版のみ 上位",
        "",
        *markdown_table(morning, 15),
        "",
        "## 直前版込み 上位",
        "",
        *markdown_table(preview, 15),
        "",
        "## 注意",
        "",
        "- 5条件・6条件は過学習しやすいので、検証期間の万舟率を重視する。",
        "- 同じカテゴリの条件は同一組み合わせに重複させないよう制限している。",
        "- `直前版` は風速、波高、展示順位など締切前に分かる情報を含む。",
        "- 結果、払戻金、人気、着順、決まり手は条件に使っていない。",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> int:
    rows = read_rows(Path(args.dataset))
    if not rows:
        raise SystemExit("dataset has no valid rows")
    results, meta = search_combos(
        rows,
        args.sizes,
        args.min_count,
        args.min_valid,
        args.max_rows,
        args.sort_by,
        args.keep_duplicate_matches,
    )
    write_csv(Path(args.output_csv), results)
    write_markdown(Path(args.output_md), results, meta)
    print(f"rows={meta['row_count']} baseline={meta['baseline_rate']:.4f} combos={meta['result_count']}")
    print(f"wrote {args.output_csv}")
    print(f"wrote {args.output_md}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default="data/analysis/race_dataset.csv")
    parser.add_argument("--sizes", nargs="+", type=int, default=[5, 6])
    parser.add_argument("--min-count", type=int, default=80)
    parser.add_argument("--min-valid", type=int, default=25)
    parser.add_argument("--max-rows", type=int, default=1000)
    parser.add_argument("--sort-by", choices=["priority", "manshu_rate", "valid_rate", "lift"], default="priority")
    parser.add_argument("--keep-duplicate-matches", action="store_true", help="keep combinations that match the exact same race set")
    parser.add_argument("--output-csv", default="reports/manshu_5_6_condition_patterns.csv")
    parser.add_argument("--output-md", default="reports/manshu_5_6_condition_patterns.md")
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
