#!/usr/bin/env python3
"""Search pre-race BOATERS composite conditions that lift manshu hit rates."""

from __future__ import annotations

import argparse
import itertools
import json
import math
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT.parent / "price_action_analysis" / "outputs" / "boaters_all_races.sqlite"
OUT_DIR = ROOT / "data" / "output" / "manshu_composite_condition_search"


def pct(value):
    if value is None or pd.isna(value):
        return None
    return round(float(value) * 100, 2)


def yen(value):
    if value is None or pd.isna(value):
        return None
    return int(round(float(value)))


def safe_div(num, den):
    return float(num) / float(den) if den else None


def clean_bool(mask):
    if hasattr(mask, "fillna"):
        mask = mask.fillna(False)
    return np.asarray(mask, dtype=bool)


def load_boats(db_path: Path, start_date: str) -> pd.DataFrame:
    sql = """
    SELECT
      r.race_id,
      r.date,
      r.place_name,
      r.round,
      r.wind_speed,
      r.wave_height,
      r.result_payout3t1 AS payout,
      r.winning_number3t1 AS trifecta,
      b.boat_number,
      b.finish,
      b.ai_3ren_pct,
      b.general_3ren_pct,
      b.ai_prediction_pct,
      b.odds_prediction_pct,
      b.st_rank_general,
      b.st_time_avg_general,
      b.tenji_time,
      b.isshu_time,
      b.avg_isshu_diff,
      b.chokusen_time,
      b.hanshu_time,
      b.mawariashi_time,
      b.tenji_rank,
      b.start_tenji_rank,
      b.nige_pct_year,
      b.sasare_pct_year,
      b.makurare_pct_year
    FROM races r
    JOIN race_boats b ON b.race_id = r.race_id
    WHERE r.date >= ?
      AND r.result_payout3t1 IS NOT NULL
      AND COALESCE(b.is_absent, 0) = 0
      AND b.finish IS NOT NULL
    """
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as con:
        boats = pd.read_sql_query(sql, con, params=(start_date,))

    boats["date"] = pd.to_datetime(boats["date"])
    boats["manshu"] = boats["payout"].ge(10000)
    boats["top3"] = boats["finish"].between(1, 3)
    boats["win"] = boats["finish"].eq(1)
    boats["b1_loss_pct"] = boats["sasare_pct_year"] + boats["makurare_pct_year"]
    boats["ai_plus"] = boats["ai_3ren_pct"] + boats["general_3ren_pct"]
    boats["ai_plus_rank"] = (
        boats.groupby("race_id")["ai_plus"]
        .rank(method="first", ascending=False, na_option="bottom")
        .astype(int)
    )
    boats["ai_prediction_rank"] = (
        boats.groupby("race_id")["ai_prediction_pct"]
        .rank(method="first", ascending=False, na_option="bottom")
        .astype(int)
    )
    boats["tenji_time_rank_calc"] = (
        boats.groupby("race_id")["tenji_time"]
        .rank(method="first", ascending=True, na_option="bottom")
        .astype(int)
    )
    boats["isshu_rank"] = (
        boats.groupby("race_id")["isshu_time"]
        .rank(method="first", ascending=True, na_option="bottom")
        .astype(int)
    )
    boats["avgdiff_rank"] = (
        boats.groupby("race_id")["avg_isshu_diff"]
        .rank(method="first", ascending=False, na_option="bottom")
        .astype(int)
    )
    boats["tenji_rank_use"] = boats["tenji_rank"].fillna(boats["tenji_time_rank_calc"])
    return boats


def build_race_frame(boats: pd.DataFrame) -> pd.DataFrame:
    race = (
        boats.groupby("race_id")
        .agg(
            date=("date", "first"),
            place_name=("place_name", "first"),
            round=("round", "first"),
            wind_speed=("wind_speed", "first"),
            wave_height=("wave_height", "first"),
            payout=("payout", "first"),
            trifecta=("trifecta", "first"),
            manshu=("manshu", "first"),
        )
        .reset_index()
    )
    for col in [
        "ai_plus",
        "ai_plus_rank",
        "ai_3ren_pct",
        "general_3ren_pct",
        "ai_prediction_pct",
        "ai_prediction_rank",
        "odds_prediction_pct",
        "st_rank_general",
        "st_time_avg_general",
        "tenji_rank_use",
        "tenji_time",
        "isshu_time",
        "isshu_rank",
        "avg_isshu_diff",
        "avgdiff_rank",
        "top3",
        "win",
        "nige_pct_year",
        "b1_loss_pct",
    ]:
        pivot = boats.pivot_table(index="race_id", columns="boat_number", values=col, aggfunc="first")
        pivot.columns = [f"b{int(c)}_{col}" for c in pivot.columns]
        race = race.join(pivot, on="race_id")

    rank_boats = boats.pivot_table(index="race_id", columns="ai_plus_rank", values="boat_number", aggfunc="first")
    rank_boats.columns = [f"ai_rank{int(c)}_boat" for c in rank_boats.columns]
    race = race.join(rank_boats, on="race_id")
    for rank in range(1, 7):
        for metric in ["avg_isshu_diff", "tenji_rank_use", "isshu_rank", "ai_plus"]:
            ranked = boats.loc[boats["ai_plus_rank"].eq(rank), ["race_id", metric]].set_index("race_id")
            race = race.join(ranked.rename(columns={metric: f"ai_rank{rank}_{metric}"}), on="race_id")

    winner = boats.loc[boats["win"], ["race_id", "boat_number", "ai_plus_rank"]]
    winner = winner.rename(columns={"boat_number": "winner_boat", "ai_plus_rank": "winner_ai_plus_rank"})
    race = race.merge(winner, on="race_id", how="left")

    outer_cols = [f"b{boat}_avg_isshu_diff" for boat in [5, 6]]
    race["outer56_best_avgdiff"] = race[outer_cols].max(axis=1)
    race["outer56_worst_avgdiff"] = race[outer_cols].min(axis=1)
    race["outer56_best_tenji_rank"] = race[["b5_tenji_rank_use", "b6_tenji_rank_use"]].min(axis=1)
    race["outer56_best_isshu_rank"] = race[["b5_isshu_rank", "b6_isshu_rank"]].min(axis=1)
    race["outer56_best_ai_plus"] = race[["b5_ai_plus", "b6_ai_plus"]].max(axis=1)
    race["outer56_best_ai_prediction"] = race[["b5_ai_prediction_pct", "b6_ai_prediction_pct"]].max(axis=1)
    race["outer56_top3"] = race[["b5_top3", "b6_top3"]].any(axis=1)
    for boat in range(1, 7):
        race[f"b{boat}_double_time"] = race[f"b{boat}_tenji_rank_use"].eq(1) & race[f"b{boat}_isshu_rank"].eq(1)
    race["mid234_double_time"] = race[["b2_double_time", "b3_double_time", "b4_double_time"]].any(axis=1)
    race["outer46_double_time"] = race[["b4_double_time", "b5_double_time", "b6_double_time"]].any(axis=1)
    race["outer56_double_time"] = race[["b5_double_time", "b6_double_time"]].any(axis=1)
    race["b1_fly"] = ~race["b1_top3"].astype(bool)
    race["winner_3to6"] = race["winner_boat"].isin([3, 4, 5, 6])
    race["mid_st_best"] = race[["b3_st_rank_general", "b4_st_rank_general"]].min(axis=1)
    race["outer_st_best"] = race[["b4_st_rank_general", "b5_st_rank_general", "b6_st_rank_general"]].min(axis=1)
    race["inner_st_best"] = race[["b1_st_rank_general", "b2_st_rank_general"]].min(axis=1)
    race["rank6_top3"] = False
    race["rank6_win"] = False
    for boat in range(1, 7):
        mask = race["ai_rank6_boat"].eq(boat)
        race.loc[mask, "rank6_top3"] = race.loc[mask, f"b{boat}_top3"].astype(bool)
        race.loc[mask, "rank6_win"] = race.loc[mask, f"b{boat}_win"].astype(bool)
    return race


def atom(atom_id, label, mask, category, family, priority=1):
    return {
        "id": atom_id,
        "label": label,
        "mask": clean_bool(mask),
        "category": category,
        "family": family,
        "priority": priority,
    }


def build_atoms(race: pd.DataFrame) -> list[dict]:
    atoms = []
    # 1号艇弱化
    atoms += [
        atom("b1_aiplus_rank_ge4", "1号艇AI+順位4位以下", race["b1_ai_plus_rank"].ge(4), "b1", "b1_ai_rank", 3),
        atom("b1_aiplus_rank_ge5", "1号艇AI+順位5位以下", race["b1_ai_plus_rank"].ge(5), "b1", "b1_ai_rank", 2),
        atom("b1_ai_pred_lt30", "1号艇AI予測30%未満", race["b1_ai_prediction_pct"].lt(30), "b1", "b1_ai_pred", 2),
        atom("b1_ai_pred_lt25", "1号艇AI予測25%未満", race["b1_ai_prediction_pct"].lt(25), "b1", "b1_ai_pred", 2),
        atom("b1_avgdiff_le0", "1号艇平均との差0以下", race["b1_avg_isshu_diff"].le(0), "b1", "b1_avgdiff", 3),
        atom("b1_avgdiff_le_m005", "1号艇平均との差-0.05以下", race["b1_avg_isshu_diff"].le(-0.05), "b1", "b1_avgdiff", 3),
        atom("b1_tenji_ge4", "1号艇展示4位以下", race["b1_tenji_rank_use"].ge(4), "b1", "b1_tenji", 3),
        atom("b1_tenji_ge5", "1号艇展示5位以下", race["b1_tenji_rank_use"].ge(5), "b1", "b1_tenji", 2),
        atom("b1_isshu_ge4", "1号艇1周4位以下", race["b1_isshu_rank"].ge(4), "b1", "b1_isshu", 2),
        atom("b1_nige_lt45", "1号艇逃げ率45%未満", race["b1_nige_pct_year"].lt(45), "b1", "b1_nige", 3),
        atom("b1_nige_lt40", "1号艇逃げ率40%未満", race["b1_nige_pct_year"].lt(40), "b1", "b1_nige", 2),
        atom("b1_loss_ge40", "1号艇逃げ失敗40%以上", race["b1_b1_loss_pct"].ge(40), "b1", "b1_loss", 3),
        atom("b1_loss_ge45", "1号艇逃げ失敗45%以上", race["b1_b1_loss_pct"].ge(45), "b1", "b1_loss", 2),
        atom("b1_st_ge4", "1号艇平均ST順位4位以下", race["b1_st_rank_general"].ge(4), "b1", "b1_st", 2),
    ]

    # 外枠・展示上昇
    atoms += [
        atom("outer56_avgdiff_ge010", "5/6号艇平均との差0.10以上", race["outer56_best_avgdiff"].ge(0.10), "outer", "outer_avgdiff", 4),
        atom("outer56_avgdiff_ge014", "5/6号艇平均との差0.14以上", race["outer56_best_avgdiff"].ge(0.14), "outer", "outer_avgdiff", 4),
        atom("outer56_avgdiff_ge020", "5/6号艇平均との差0.20以上", race["outer56_best_avgdiff"].ge(0.20), "outer", "outer_avgdiff", 2),
        atom("outer56_both_not_bad", "5/6号艇平均との差どちらも0以上", race["outer56_worst_avgdiff"].ge(0), "outer", "outer_both_avgdiff", 2),
        atom("outer56_tenji_top2", "5/6号艇に展示2位以内", race["outer56_best_tenji_rank"].le(2), "outer", "outer_tenji", 4),
        atom("outer56_tenji_top1", "5/6号艇に展示1位", race["outer56_best_tenji_rank"].le(1), "outer", "outer_tenji", 2),
        atom("outer56_isshu_top2", "5/6号艇に1周2位以内", race["outer56_best_isshu_rank"].le(2), "outer", "outer_isshu", 3),
        atom("outer56_aiplus_ge110", "5/6号艇AI+最大110以上", race["outer56_best_ai_plus"].ge(110), "outer", "outer_ai_plus", 2),
        atom("outer56_ai_pred_ge12", "5/6号艇AI予測最大12%以上", race["outer56_best_ai_prediction"].ge(12), "outer", "outer_ai_pred", 2),
        atom("outer56_double_time", "5/6号艇にダブルタイム", race["outer56_double_time"], "outer", "outer_double_time", 4),
        atom("outer46_double_time", "4〜6号艇にダブルタイム", race["outer46_double_time"], "outer", "outer_double_time", 3),
    ]

    # AI+下位艇を穴/消しに分ける材料
    atoms += [
        atom("rank6_boat_56", "AI+最下位が5/6号艇", race["ai_rank6_boat"].isin([5, 6]), "rank", "rank6_boat", 3),
        atom("rank6_boat_36", "AI+最下位が3〜6号艇", race["ai_rank6_boat"].isin([3, 4, 5, 6]), "rank", "rank6_boat", 2),
        atom("rank6_avgdiff_ge010", "AI+最下位平均との差0.10以上", race["ai_rank6_avg_isshu_diff"].ge(0.10), "rank", "rank6_avgdiff", 4),
        atom("rank6_avgdiff_ge014", "AI+最下位平均との差0.14以上", race["ai_rank6_avg_isshu_diff"].ge(0.14), "rank", "rank6_avgdiff", 3),
        atom("rank6_tenji_top2", "AI+最下位が展示2位以内", race["ai_rank6_tenji_rank_use"].le(2), "rank", "rank6_tenji", 3),
        atom("rank5_avgdiff_ge010", "AI+5位平均との差0.10以上", race["ai_rank5_avg_isshu_diff"].ge(0.10), "rank", "rank5_avgdiff", 2),
        atom("rank5_tenji_top2", "AI+5位が展示2位以内", race["ai_rank5_tenji_rank_use"].le(2), "rank", "rank5_tenji", 2),
    ]

    # ST/スリット隊形の近似
    atoms += [
        atom("mid_st_top2_b1_st_bad", "中枠ST上位+1号艇ST4位以下", race["mid_st_best"].le(2) & race["b1_st_rank_general"].ge(4), "st", "st_shape", 4),
        atom("outer_st_top2_b1_st_bad", "外寄りST上位+1号艇ST4位以下", race["outer_st_best"].le(2) & race["b1_st_rank_general"].ge(4), "st", "st_shape", 3),
        atom("b3_st_top2", "3号艇平均ST順位2位以内", race["b3_st_rank_general"].le(2), "st", "st_boat", 2),
        atom("b4_st_top2", "4号艇平均ST順位2位以内", race["b4_st_rank_general"].le(2), "st", "st_boat", 2),
        atom("b5_st_top2", "5号艇平均ST順位2位以内", race["b5_st_rank_general"].le(2), "st", "st_boat", 2),
        atom("b6_st_top2", "6号艇平均ST順位2位以内", race["b6_st_rank_general"].le(2), "st", "st_boat", 1),
        atom("mid234_double_time", "2〜4号艇にダブルタイム", race["mid234_double_time"], "exhibit", "double_time", 3),
        atom("b1_double_time", "1号艇ダブルタイム", race["b1_double_time"], "exhibit", "double_time", 2),
    ]

    # レース文脈
    atoms += [
        atom("front_1_6r", "前半1〜6R", race["round"].le(6), "context", "round", 2),
        atom("late_7_12r", "後半7〜12R", race["round"].ge(7), "context", "round", 2),
        atom("wind_wave_ge5", "風または波5以上", race["wind_speed"].ge(5) | race["wave_height"].ge(5), "context", "weather", 2),
        atom("round_1_3", "1〜3R", race["round"].le(3), "context", "round_detail", 1),
        atom("round_10_12", "10〜12R", race["round"].ge(10), "context", "round_detail", 1),
    ]
    hot_places = ["若松", "宮島", "福岡", "蒲郡", "戸田", "常滑", "徳山", "津", "下関", "芦屋", "平和島", "鳴門", "住之江"]
    for place in hot_places:
        atoms.append(atom(f"venue_{place}", f"会場:{place}", race["place_name"].eq(place), "venue", "venue", 1))
    return atoms


def trim_atoms(atoms, race, min_races):
    base_rate = race["manshu"].mean()
    manshu = race["manshu"].to_numpy(dtype=bool)
    rows = []
    for item in atoms:
        mask = item["mask"]
        n = int(mask.sum())
        if n < min_races:
            continue
        rate = float(manshu[mask].mean())
        score = (rate - base_rate) * 100 + item["priority"] * 1.4 + math.log10(n + 1)
        rows.append((score, item))
    keep_limits = {"b1": 12, "outer": 8, "rank": 7, "st": 5, "context": 5, "venue": 8}
    kept = []
    for category, limit in keep_limits.items():
        category_rows = sorted(
            [row for row in rows if row[1]["category"] == category],
            key=lambda row: row[0],
            reverse=True,
        )
        kept.extend(item for _, item in category_rows[:limit])
    return kept


def unique_combo(combo):
    families = [item["family"] for item in combo]
    if len(set(families)) != len(families):
        return False
    labels = {item["label"] for item in combo}
    if "前半1〜6R" in labels and "後半7〜12R" in labels:
        return False
    if "1〜3R" in labels and "10〜12R" in labels:
        return False
    return True


def product_combos(groups, size):
    seen = set()
    for raw in itertools.product(*groups):
        combo = tuple(sorted(raw, key=lambda item: item["id"]))
        key = tuple(item["id"] for item in combo)
        if key in seen or len(set(key)) != len(key):
            continue
        seen.add(key)
        if unique_combo(combo):
            yield combo


def fixed_product_combos(fixed, groups):
    seen = set()
    fixed = tuple(fixed)
    for raw in itertools.product(*groups):
        combo = tuple(sorted(fixed + tuple(raw), key=lambda item: item["id"]))
        key = tuple(item["id"] for item in combo)
        if key in seen or len(set(key)) != len(key):
            continue
        seen.add(key)
        if unique_combo(combo):
            yield combo


def candidate_combos(atoms):
    by_cat = {}
    for item in atoms:
        by_cat.setdefault(item["category"], []).append(item)
    for values in by_cat.values():
        values.sort(key=lambda item: item["priority"], reverse=True)

    b1 = by_cat["b1"]
    outer = by_cat["outer"]
    rank = by_cat["rank"]
    st = by_cat["st"]
    context = by_cat["context"] + by_cat["venue"]

    # 3条件: レース選別の骨格
    yield from product_combos([b1, outer, rank + st + context], 3)
    yield from product_combos([b1, rank, outer + st + context], 3)

    # 5条件: 1号艇弱化を2軸以上で確認し、外/AI/ST/文脈を重ねる
    for b1_pair in itertools.combinations(b1, 2):
        if not unique_combo(b1_pair):
            continue
        yield from fixed_product_combos(b1_pair, [outer, rank, st + context])
    yield from product_combos([b1, outer[:7], rank[:6], st[:5], context], 5)

    # 7条件: 強い買い目候補だけを見るための厳選セット
    b1_top = b1[:9]
    outer_top = outer[:7]
    rank_top = rank[:6]
    st_top = st[:5]
    context_top = context[:10]
    for b1_pair in itertools.combinations(b1_top, 2):
        if not unique_combo(b1_pair):
            continue
        for outer_pair in itertools.combinations(outer_top, 2):
            if not unique_combo(outer_pair):
                continue
            yield from fixed_product_combos(b1_pair + outer_pair, [rank_top, st_top, context_top])


def summarize(combo, mask, arrays, base_rate, recent_mask):
    n = int(mask.sum())
    if n == 0:
        return None
    recent = mask & recent_mask
    n_recent = int(recent.sum())
    manshu = int(arrays["manshu"][mask].sum())
    manshu_recent = int(arrays["manshu"][recent].sum()) if n_recent else 0
    rate = manshu / n
    recent_rate = manshu_recent / n_recent if n_recent else None
    b1_fly = arrays["b1_fly"][mask].mean()
    outer56 = arrays["outer56_top3"][mask].mean()
    rank6_top3 = arrays["rank6_top3"][mask].mean()
    winner_3to6 = arrays["winner_3to6"][mask].mean()
    score_recent = recent_rate if recent_rate is not None else max(0, rate - 0.03)
    score = (
        min(rate, score_recent) * 100
        + math.log10(n + 1) * 1.7
        + max(0, outer56 - 0.50) * 8
        + max(0, winner_3to6 - 0.40) * 8
        - max(0, 180 - n) / 80
    )
    return {
        "condition_count": len(combo),
        "condition": " × ".join(item["label"] for item in combo),
        "atom_ids": ",".join(item["id"] for item in combo),
        "races": n,
        "manshu_races": manshu,
        "manshu_rate_pct": pct(rate),
        "lift_vs_all": round(rate / base_rate, 3),
        "recent_races_2025_plus": n_recent,
        "recent_manshu_races_2025_plus": manshu_recent,
        "recent_manshu_rate_pct": pct(recent_rate),
        "avg_payout_yen": yen(arrays["payout"][mask].mean()),
        "b1_fly_pct": pct(b1_fly),
        "outer56_top3_pct": pct(outer56),
        "rank6_top3_pct": pct(rank6_top3),
        "winner_3to6_pct": pct(winner_3to6),
        "score": round(score, 4),
    }


def search_conditions(race: pd.DataFrame, min_races: int, min_recent: int) -> tuple[pd.DataFrame, list[dict]]:
    atoms_raw = build_atoms(race)
    atoms = trim_atoms(atoms_raw, race, min_races)
    base_rate = race["manshu"].mean()
    recent_mask = race["date"].ge(pd.Timestamp("2025-01-01")).to_numpy()
    arrays = {
        "manshu": race["manshu"].to_numpy(dtype=bool),
        "payout": race["payout"].to_numpy(dtype=float),
        "b1_fly": race["b1_fly"].to_numpy(dtype=bool),
        "outer56_top3": race["outer56_top3"].to_numpy(dtype=bool),
        "rank6_top3": race["rank6_top3"].to_numpy(dtype=bool),
        "winner_3to6": race["winner_3to6"].to_numpy(dtype=bool),
    }
    rows = []
    tried = 0
    beam = []
    for idx, item in enumerate(atoms):
        mask = item["mask"]
        row = summarize((item,), mask, arrays, base_rate, recent_mask)
        if row:
            beam.append({"idxs": (idx,), "mask": mask, "row": row})
    beam = sorted(beam, key=lambda item: item["row"]["score"], reverse=True)[:320]

    for size in range(2, 8):
        next_beam = []
        seen = set()
        for state in beam:
            start = state["idxs"][-1] + 1
            for idx in range(start, len(atoms)):
                combo = tuple(atoms[i] for i in state["idxs"] + (idx,))
                if not unique_combo(combo):
                    continue
                key = tuple(item["id"] for item in combo)
                if key in seen:
                    continue
                seen.add(key)
                tried += 1
                mask = state["mask"] & atoms[idx]["mask"]
                n = int(mask.sum())
                if n < min_races:
                    continue
                recent_n = int((mask & recent_mask).sum())
                if recent_n < max(10, min_recent // 2):
                    continue
                row = summarize(combo, mask, arrays, base_rate, recent_mask)
                if not row:
                    continue
                next_beam.append({"idxs": state["idxs"] + (idx,), "mask": mask, "row": row})
                categories = {item["category"] for item in combo}
                output_ready = (
                    size in {3, 5, 7}
                    and "b1" in categories
                    and ("outer" in categories or "rank" in categories)
                    and len(categories) >= 3
                    and n >= min_races
                    and recent_n >= min_recent
                )
                if output_ready and row["manshu_rate_pct"] >= 21.0:
                    recent_rate = row.get("recent_manshu_rate_pct")
                    if recent_rate is None or recent_rate >= 15.0:
                        rows.append(row)
        if not next_beam:
            break
        beam = sorted(next_beam, key=lambda item: item["row"]["score"], reverse=True)[:320]
    out = pd.DataFrame(rows)
    if out.empty:
        return out, [{"atoms_before_trim": len(atoms_raw), "atoms_after_trim": len(atoms), "tried_combos": tried, "candidate_rows": 0}]
    out = out.drop_duplicates("atom_ids")
    out = out.sort_values(["score", "manshu_rate_pct", "races"], ascending=False).reset_index(drop=True)
    return out, [{"atoms_before_trim": len(atoms_raw), "atoms_after_trim": len(atoms), "tried_combos": tried, "candidate_rows": int(len(out))}]


def select_diverse(top: pd.DataFrame, race: pd.DataFrame, limit: int) -> pd.DataFrame:
    if top.empty:
        return top
    atoms = {item["id"]: item for item in build_atoms(race)}
    selected = []
    selected_masks = []
    for _, row in top.iterrows():
        combo_ids = str(row["atom_ids"]).split(",")
        mask = np.logical_and.reduce([atoms[item_id]["mask"] for item_id in combo_ids])
        too_close = False
        for prev in selected_masks:
            inter = int((mask & prev).sum())
            union = int((mask | prev).sum())
            if union and inter / union > 0.72:
                too_close = True
                break
        if too_close:
            continue
        selected.append(row)
        selected_masks.append(mask)
        if len(selected) >= limit:
            break
    return pd.DataFrame(selected)


def make_html(path: Path, summary: dict, selected: pd.DataFrame, all_rows: pd.DataFrame) -> None:
    def table(df, n):
        if df.empty:
            return "<p>該当なし</p>"
        view = df.head(n).copy()
        keep = [
            "condition_count",
            "condition",
            "races",
            "manshu_rate_pct",
            "recent_manshu_rate_pct",
            "lift_vs_all",
            "b1_fly_pct",
            "outer56_top3_pct",
            "winner_3to6_pct",
            "rank6_top3_pct",
            "avg_payout_yen",
        ]
        return view[keep].to_html(index=False, escape=True)

    html = f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <title>万舟複合条件10選</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Hiragino Sans', 'Yu Gothic', sans-serif; margin: 32px; color: #222; }}
    h1 {{ margin-bottom: 4px; }}
    p {{ line-height: 1.7; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
    th, td {{ border-bottom: 1px solid #e5e7eb; padding: 7px 8px; text-align: right; vertical-align: top; }}
    th:first-child, td:first-child, th:nth-child(2), td:nth-child(2) {{ text-align: left; }}
    th {{ background: #f5f6f8; }}
  </style>
</head>
<body>
  <h1>万舟複合条件10選</h1>
  <p>対象: {summary['start_date']}以降 / {summary['races']:,}レース / 基準万舟率 {summary['base_manshu_rate_pct']:.2f}%。
  条件は予想時点で使えるBOATERSデータだけを使用しています。</p>
  <h2>採用候補10個</h2>
  {table(selected, 10)}
  <h2>探索上位</h2>
  {table(all_rows, 50)}
</body>
</html>
"""
    path.write_text(html, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--start-date", default="2020-01-01")
    parser.add_argument("--out-dir", default=str(OUT_DIR))
    parser.add_argument("--min-races", type=int, default=160)
    parser.add_argument("--min-recent", type=int, default=25)
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()

    boats = load_boats(Path(args.db), args.start_date)
    race = build_race_frame(boats)
    all_rows, meta = search_conditions(race, args.min_races, args.min_recent)
    selected = select_diverse(all_rows, race, args.limit)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    all_csv = out_dir / "all_composite_conditions.csv"
    selected_csv = out_dir / "selected_10_conditions.csv"
    all_rows.to_csv(all_csv, index=False, encoding="utf-8-sig")
    selected.to_csv(selected_csv, index=False, encoding="utf-8-sig")

    summary = {
        "db": str(Path(args.db)),
        "start_date": args.start_date,
        "races": int(len(race)),
        "boats": int(len(boats)),
        "base_manshu_rate_pct": round(float(race["manshu"].mean() * 100), 2),
        "selected_count": int(len(selected)),
        "search_meta": meta,
        "outputs": {
            "all_conditions_csv": str(all_csv),
            "selected_10_csv": str(selected_csv),
            "report_html": str(out_dir / "manshu_composite_condition_10.html"),
        },
        "selected_conditions": selected.to_dict("records"),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    make_html(out_dir / "manshu_composite_condition_10.html", summary, selected, all_rows)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
