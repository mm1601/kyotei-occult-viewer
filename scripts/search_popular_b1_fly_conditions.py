#!/usr/bin/env python3
import argparse
import csv
import itertools
import json
import sqlite3
import warnings
from pathlib import Path

import pandas as pd

warnings.filterwarnings("ignore", category=pd.errors.PerformanceWarning)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BOATERS_DB = ROOT / "data" / "output" / "boaters_all_races.sqlite"
DESKTOP_BOATERS_DB = Path.home() / "Desktop" / "price_action_analysis" / "outputs" / "boaters_all_races.sqlite"
DESKTOP_ODDS_DB = Path.home() / "Desktop" / "kyotei_occult" / "data" / "live_odds.db"
OUT_CSV = ROOT / "data" / "output" / "popular_b1_fly_conditions.csv"
OUT_JSON = ROOT / "data" / "output" / "popular_b1_fly_conditions.json"
OUT_MD = ROOT / "reports" / "popular_b1_fly_conditions.md"


def pick_existing(*paths):
    for path in paths:
        if path and Path(path).exists():
            return Path(path)
    return Path(paths[0])


def connect_ro(path):
    return sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True)


def result_boats(value):
    if value is None or pd.isna(value):
        return []
    text = str(value).replace("-", "").replace(" ", "")
    if text.endswith(".0"):
        text = text[:-2]
    text = text.zfill(3)
    return [int(ch) for ch in text[:3] if ch.isdigit()]


def rate(mask):
    count = int(mask.count())
    if count == 0:
        return None
    return round(float(mask.mean() * 100), 2)


def pct(value):
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):.2f}%"


def load_latest_top5_odds(path, start_date=None, end_date=None):
    where = []
    params = []
    if start_date:
        where.append("date >= ?")
        params.append(start_date)
    if end_date:
        where.append("date <= ?")
        params.append(end_date)
    where_sql = "WHERE " + " AND ".join(where) if where else ""
    sql = f"""
    WITH latest AS (
      SELECT date, venue_code, race_no, MAX(snapshot_at) AS snapshot_at
      FROM odds_trifecta
      {where_sql}
      GROUP BY date, venue_code, race_no
    )
    SELECT o.date, o.venue_code, o.race_no, o.combo, o.odds, o.snapshot_at
    FROM odds_trifecta o
    JOIN latest l
      ON l.date = o.date
     AND l.venue_code = o.venue_code
     AND l.race_no = o.race_no
     AND l.snapshot_at = o.snapshot_at
    """
    with connect_ro(path) as con:
        odds = pd.read_sql_query(sql, con, params=params)
    if odds.empty:
        return odds
    odds["odds"] = pd.to_numeric(odds["odds"], errors="coerce")
    odds = odds.sort_values(["date", "venue_code", "race_no", "odds", "combo"])
    odds["odds_rank"] = odds.groupby(["date", "venue_code", "race_no"]).cumcount() + 1
    top5 = odds[odds["odds_rank"] <= 5].copy()
    grouped = top5.groupby(["date", "venue_code", "race_no"], as_index=False).agg(
        top5_combos=("combo", lambda values: " ".join(map(str, values))),
        top5_avg_odds=("odds", "mean"),
        top1_odds=("odds", "min"),
        top5_head1_count=("combo", lambda values: sum(str(value).startswith("1-") for value in values)),
        top5_count=("combo", "count"),
        snapshot_at=("snapshot_at", "max"),
    )
    grouped["b1_trifecta_top5_1head"] = (
        grouped["top5_count"].eq(5) & grouped["top5_head1_count"].eq(5)
    ).astype(int)
    grouped["place_id"] = pd.to_numeric(grouped["venue_code"], errors="coerce").astype("Int64")
    grouped = grouped.rename(columns={"race_no": "round"})
    grouped["round"] = pd.to_numeric(grouped["round"], errors="coerce").astype("Int64")
    return grouped


def load_boaters_boats(path, start_date, end_date):
    sql = """
    SELECT
      r.race_id,
      r.date,
      r.place_id,
      r.place_name,
      r.round,
      r.weather,
      r.wind_speed,
      r.wave_height,
      r.result_payout3t1 AS payout,
      r.winning_number3t1 AS trifecta,
      b.boat_number,
      b.ai_3ren_pct,
      b.general_3ren_pct,
      b.ai_prediction_pct,
      b.odds_prediction_pct,
      b.st_rank_general,
      b.st_time_avg_general,
      b.tenji_time,
      b.isshu_time,
      b.avg_isshu_diff AS isshu_avg_diff,
      b.tenji_rank,
      b.start_tenji_rank,
      b.nige_pct_year,
      b.sasare_pct_year,
      b.makurare_pct_year,
      b.finish_order
    FROM races r
    JOIN race_boats b ON b.race_id = r.race_id
    WHERE r.date >= ?
      AND r.date <= ?
      AND COALESCE(b.is_absent, 0) = 0
    """
    with connect_ro(path) as con:
        return pd.read_sql_query(sql, con, params=(start_date, end_date))


def add_group_ranks(boats):
    boats = boats.sort_values(["race_id", "boat_number"]).copy()
    boats["ai_plus"] = boats["ai_3ren_pct"] + boats["general_3ren_pct"]
    boats["exhibit_combo_time"] = boats["tenji_time"] + boats["isshu_time"]
    boats["avg_exhibit_combo_time"] = boats.groupby("race_id")["exhibit_combo_time"].transform("mean")
    boats["boaters_avgdiff"] = boats["avg_exhibit_combo_time"] - boats["exhibit_combo_time"]
    rank_specs = [
        ("ai_plus", "ai_plus_order", False),
        ("ai_prediction_pct", "ai_prediction_order", False),
        ("odds_prediction_pct", "odds_order", False),
        ("tenji_time", "tenji_time_rank", True),
        ("isshu_time", "isshu_rank", True),
    ]
    for source, rank_col, ascending in rank_specs:
        boats[rank_col] = boats.groupby("race_id")[source].rank(ascending=ascending, method="first")
    return boats


def build_race_features(boats, top5_odds):
    boats = add_group_ranks(boats)
    race_cols = [
        "race_id",
        "date",
        "place_id",
        "place_name",
        "round",
        "weather",
        "wind_speed",
        "wave_height",
        "payout",
        "trifecta",
    ]
    races = boats[race_cols].drop_duplicates("race_id").set_index("race_id")
    feature_cols = [
        "ai_3ren_pct",
        "general_3ren_pct",
        "ai_plus",
        "ai_plus_order",
        "ai_prediction_pct",
        "ai_prediction_order",
        "odds_prediction_pct",
        "odds_order",
        "st_rank_general",
        "st_time_avg_general",
        "tenji_time",
        "isshu_time",
        "isshu_avg_diff",
        "boaters_avgdiff",
        "tenji_rank",
        "tenji_time_rank",
        "isshu_rank",
        "nige_pct_year",
        "sasare_pct_year",
        "makurare_pct_year",
        "finish_order",
    ]
    for boat in range(1, 7):
        sub = boats[boats["boat_number"] == boat].set_index("race_id")
        for col in feature_cols:
            races[f"b{boat}_{col}"] = sub[col]

    races["b1_loss_pct"] = races["b1_sasare_pct_year"] + races["b1_makurare_pct_year"]
    races["outer56_best_avgdiff"] = races[["b5_boaters_avgdiff", "b6_boaters_avgdiff"]].max(axis=1)
    races["outer56_best_ai_prediction_pct"] = races[["b5_ai_prediction_pct", "b6_ai_prediction_pct"]].max(axis=1)
    races["outer56_best_ai_plus"] = races[["b5_ai_plus", "b6_ai_plus"]].max(axis=1)
    races["outer56_tenji_top2_count"] = sum(
        races[f"b{boat}_tenji_time_rank"].le(2).fillna(False).astype(int) for boat in (5, 6)
    )
    races["outer56_isshu_top2_count"] = sum(
        races[f"b{boat}_isshu_rank"].le(2).fillna(False).astype(int) for boat in (5, 6)
    )
    races["outer56_exhibit_top2_count"] = sum(
        (
            races[f"b{boat}_tenji_time_rank"].le(2)
            | races[f"b{boat}_isshu_rank"].le(2)
            | races[f"b{boat}_tenji_rank"].le(2)
        )
        .fillna(False)
        .astype(int)
        for boat in (5, 6)
    )
    races["outer456_pressure"] = 0
    races["outer56_pressure_vs_1"] = 0
    for boat in range(2, 7):
        left = boat - 1
        races[f"b{boat}_super_slit_alert"] = (
            (races[f"b{left}_tenji_time"] - races[f"b{boat}_tenji_time"]).ge(0.10)
            & (races[f"b{left}_st_rank_general"] - races[f"b{boat}_st_rank_general"]).gt(0)
        ).fillna(False).astype(int)
    races["outer456_super_slit_count"] = sum(races[f"b{boat}_super_slit_alert"] for boat in (4, 5, 6))
    races["outer56_super_slit_count"] = sum(races[f"b{boat}_super_slit_alert"] for boat in (5, 6))

    for order in (5, 6):
        sub = boats[boats["ai_plus_order"] == order].drop_duplicates("race_id").set_index("race_id")
        prefix = f"ai_rank{order}"
        races[f"{prefix}_boat"] = sub["boat_number"]
        races[f"{prefix}_avgdiff"] = sub["boaters_avgdiff"]
        races[f"{prefix}_ai_prediction_pct"] = sub["ai_prediction_pct"]
        races[f"{prefix}_tenji_rank"] = sub["tenji_time_rank"]
        races[f"{prefix}_isshu_rank"] = sub["isshu_rank"]
        races[f"{prefix}_exhibit_top2"] = (
            sub["tenji_time_rank"].le(2) | sub["isshu_rank"].le(2) | sub["tenji_rank"].le(2)
        ).astype(int)

    races["longshot_head_candidate_count"] = 0
    for boat in range(3, 7):
        value_gap = races[f"b{boat}_odds_order"].ge(4) | races[f"b{boat}_odds_prediction_pct"].le(12)
        data_up = (
            races[f"b{boat}_ai_prediction_pct"].ge(8)
            & races[f"b{boat}_boaters_avgdiff"].ge(0.05)
            & (
                races[f"b{boat}_tenji_time_rank"].le(2)
                | races[f"b{boat}_isshu_rank"].le(2)
                | races[f"b{boat}_st_rank_general"].le(2)
            )
        )
        races["longshot_head_candidate_count"] += (value_gap & data_up).fillna(False).astype(int)

    races["result_boats"] = races["trifecta"].map(result_boats)
    races["winner"] = races["result_boats"].map(lambda values: values[0] if values else None)
    races["b1_not_win"] = races["winner"].ne(1)
    races["b1_top3_miss"] = races["result_boats"].map(lambda values: 1 not in values if values else None)
    races["winner_3to6"] = races["winner"].map(lambda value: value in {3, 4, 5, 6} if value else None)
    races["outer56_top3"] = races["result_boats"].map(lambda values: any(value in {5, 6} for value in values) if values else None)
    races["manshu"] = pd.to_numeric(races["payout"], errors="coerce").ge(10000)

    odds_cols = [
        "date",
        "place_id",
        "round",
        "top5_combos",
        "top5_avg_odds",
        "top1_odds",
        "top5_head1_count",
        "b1_trifecta_top5_1head",
        "snapshot_at",
    ]
    merged = races.reset_index().merge(
        top5_odds[odds_cols],
        on=["date", "place_id", "round"],
        how="inner",
    )
    return merged


def atom_definitions(df):
    return [
        ("b1_ai_pred_lt40", "1号艇AI1着予測40%未満", df["b1_ai_prediction_pct"].lt(40)),
        ("b1_ai_pred_lt35", "1号艇AI1着予測35%未満", df["b1_ai_prediction_pct"].lt(35)),
        ("b1_ai_plus_not1", "1号艇AI+順位が1位ではない", df["b1_ai_plus_order"].gt(1)),
        ("b1_ai_plus_3plus", "1号艇AI+順位が3位以下", df["b1_ai_plus_order"].ge(3)),
        ("b1_nige_lt50", "1号艇逃げ率50%未満", df["b1_nige_pct_year"].lt(50)),
        ("b1_nige_lt45", "1号艇逃げ率45%未満", df["b1_nige_pct_year"].lt(45)),
        ("b1_loss_ge35", "1号艇差され+まくられ35%以上", df["b1_loss_pct"].ge(35)),
        ("b1_loss_ge40", "1号艇差され+まくられ40%以上", df["b1_loss_pct"].ge(40)),
        ("b1_st_rank_ge3", "1号艇平均ST順位3位以下", df["b1_st_rank_general"].ge(3)),
        ("b1_st_rank_ge4", "1号艇平均ST順位4位以下", df["b1_st_rank_general"].ge(4)),
        ("b1_avgdiff_le030", "1号艇 展示+1周平均との差+0.30以下", df["b1_boaters_avgdiff"].le(0.30)),
        ("b1_avgdiff_le015", "1号艇 展示+1周平均との差+0.15以下", df["b1_boaters_avgdiff"].le(0.15)),
        ("b1_avgdiff_le000", "1号艇 展示+1周平均との差0.00以下", df["b1_boaters_avgdiff"].le(0.00)),
        ("b1_isshu_le_m005", "1号艇1周平均との差-0.05以下", df["b1_isshu_avg_diff"].le(-0.05)),
        ("b1_isshu_le_m010", "1号艇1周平均との差-0.10以下", df["b1_isshu_avg_diff"].le(-0.10)),
        ("b1_tenji_rank_ge3", "1号艇展示タイム順位3位以下", df["b1_tenji_time_rank"].ge(3)),
        ("b1_tenji_rank_ge4", "1号艇展示タイム順位4位以下", df["b1_tenji_time_rank"].ge(4)),
        ("outer56_avg_ge005", "5/6号艇どちらか 展示+1周平均との差+0.05以上", df["outer56_best_avgdiff"].ge(0.05)),
        ("outer56_avg_ge010", "5/6号艇どちらか 展示+1周平均との差+0.10以上", df["outer56_best_avgdiff"].ge(0.10)),
        ("outer56_avg_ge014", "5/6号艇どちらか 展示+1周平均との差+0.14以上", df["outer56_best_avgdiff"].ge(0.14)),
        ("outer56_ai_pred_ge10", "5/6号艇どちらかAI1着予測10%以上", df["outer56_best_ai_prediction_pct"].ge(10)),
        ("outer56_ai_pred_ge12", "5/6号艇どちらかAI1着予測12%以上", df["outer56_best_ai_prediction_pct"].ge(12)),
        ("outer56_exhibit_top2", "5/6号艇が展示・1周どちらか2位以内", df["outer56_exhibit_top2_count"].ge(1)),
        ("outer56_super_slit", "5/6号艇にスーパースリット", df["outer56_super_slit_count"].ge(1)),
        ("outer456_super_slit", "4〜6号艇にスーパースリット", df["outer456_super_slit_count"].ge(1)),
        ("rank6_outer56", "AI+6位が5/6号艇", df["ai_rank6_boat"].isin([5, 6])),
        ("rank6_avg_ge010", "AI+6位が平均との差+0.10以上", df["ai_rank6_avgdiff"].ge(0.10)),
        ("rank6_exhibit_top2", "AI+6位が展示・1周どちらか2位以内", df["ai_rank6_exhibit_top2"].eq(1)),
        ("rank5_exhibit_top2", "AI+5位が展示・1周どちらか2位以内", df["ai_rank5_exhibit_top2"].eq(1)),
        ("longshot_head_exists", "3〜6号艇に人気薄の頭候補", df["longshot_head_candidate_count"].ge(1)),
        ("wind_or_wave5", "風5m以上または波5cm以上", df["wind_speed"].fillna(0).ge(5) | df["wave_height"].fillna(0).ge(5)),
        ("round_1to6", "1〜6R", df["round"].le(6)),
        ("round_7to12", "7〜12R", df["round"].ge(7)),
    ]


def search_conditions(df, min_sample):
    popular = df["b1_trifecta_top5_1head"].eq(1).fillna(False).to_numpy(dtype=bool)
    atoms = [
        (atom_id, label, atom_mask.fillna(False).to_numpy(dtype=bool))
        for atom_id, label, atom_mask in atom_definitions(df)
    ]
    b1_not_win = df["b1_not_win"].fillna(False).to_numpy(dtype=bool)
    b1_top3_miss = df["b1_top3_miss"].fillna(False).to_numpy(dtype=bool)
    manshu_hit = df["manshu"].fillna(False).to_numpy(dtype=bool)
    winner_3to6 = df["winner_3to6"].fillna(False).to_numpy(dtype=bool)
    outer56_top3 = df["outer56_top3"].fillna(False).to_numpy(dtype=bool)
    base_n = int(popular.sum())
    base_b1_fly = float(b1_not_win[popular].mean() * 100) if base_n else 0.0
    base_b1_miss = float(b1_top3_miss[popular].mean() * 100) if base_n else 0.0
    base_manshu = float(manshu_hit[popular].mean() * 100) if base_n else 0.0
    base_winner36 = float(winner_3to6[popular].mean() * 100) if base_n else 0.0
    base_outer56 = float(outer56_top3[popular].mean() * 100) if base_n else 0.0
    rows = []
    for width in (3, 4, 5):
        for combo in itertools.combinations(atoms, width):
            groups = [atom_group(item[0]) for item in combo]
            if len(groups) != len(set(groups)):
                continue
            mask = popular.copy()
            for _, _, atom_mask in combo:
                mask &= atom_mask
            n = int(mask.sum())
            if n < min_sample:
                continue
            b1_fly = float(b1_not_win[mask].mean() * 100)
            b1_miss = float(b1_top3_miss[mask].mean() * 100)
            manshu = float(manshu_hit[mask].mean() * 100)
            winner36 = float(winner_3to6[mask].mean() * 100)
            outer56 = float(outer56_top3[mask].mean() * 100)
            score = (b1_fly - base_b1_fly) + (manshu - base_manshu) * 0.45 + min(8.0, n / 8.0)
            rows.append(
                {
                    "condition_id": "&".join(item[0] for item in combo),
                    "condition": " × ".join(item[1] for item in combo),
                    "atoms": [item[0] for item in combo],
                    "sample_races": n,
                    "b1_not_win_rate_pct": round(b1_fly, 2),
                    "b1_not_win_lift_pp": round(b1_fly - base_b1_fly, 2),
                    "b1_top3_miss_rate_pct": round(b1_miss, 2),
                    "manshu_rate_pct": round(manshu, 2),
                    "manshu_lift_pp": round(manshu - base_manshu, 2),
                    "winner_3to6_pct": round(winner36, 2),
                    "outer56_top3_pct": round(outer56, 2),
                    "score": round(score, 4),
                }
            )
    rows.sort(key=lambda row: (row["score"], row["sample_races"]), reverse=True)
    selected = []
    used = set()
    for row in rows:
        atom_set = frozenset(row["atoms"])
        if atom_set in used:
            continue
        selected.append(row)
        used.add(atom_set)
        if len(selected) >= 20:
            break
    return selected, {
        "popular_b1_top5_races": base_n,
        "base_b1_not_win_rate_pct": round(base_b1_fly, 2),
        "base_b1_top3_miss_rate_pct": round(base_b1_miss, 2),
        "base_manshu_rate_pct": round(base_manshu, 2),
        "base_winner_3to6_pct": round(base_winner36, 2),
        "base_outer56_top3_pct": round(base_outer56, 2),
    }


def atom_group(atom_id):
    if atom_id.startswith("b1_ai_pred"):
        return "b1_ai_pred"
    if atom_id.startswith("b1_ai_plus"):
        return "b1_ai_plus"
    if atom_id.startswith("b1_nige"):
        return "b1_nige"
    if atom_id.startswith("b1_loss"):
        return "b1_loss"
    if atom_id.startswith("b1_st_rank"):
        return "b1_st_rank"
    if atom_id.startswith("b1_avgdiff"):
        return "b1_avgdiff"
    if atom_id.startswith("b1_isshu"):
        return "b1_isshu"
    if atom_id.startswith("b1_tenji_rank"):
        return "b1_tenji_rank"
    if atom_id.startswith("outer56_avg"):
        return "outer56_avg"
    if atom_id.startswith("outer56_ai_pred"):
        return "outer56_ai_pred"
    if atom_id.startswith("round_"):
        return "round"
    return atom_id


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        if not rows:
            f.write("")
            return
        writer = csv.DictWriter(f, fieldnames=[key for key in rows[0].keys() if key != "atoms"])
        writer.writeheader()
        for row in rows:
            out = {key: value for key, value in row.items() if key != "atoms"}
            writer.writerow(out)


def write_markdown(path, summary, selected, data_summary):
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 人気1号艇が飛ぶ複合条件",
        "",
        "三連単オッズの人気1〜5位がすべて1号艇頭だったレースだけを分母にして、1号艇が1着を外す条件を探索した結果です。",
        "",
        "## 分母",
        "",
        f"- 対象期間: {data_summary['start_date']} 〜 {data_summary['end_date']}",
        f"- 人気1号艇レース数: {summary['popular_b1_top5_races']}",
        f"- 人気1号艇ベースの1着外率: {pct(summary['base_b1_not_win_rate_pct'])}",
        f"- 人気1号艇ベースの3着外率: {pct(summary['base_b1_top3_miss_rate_pct'])}",
        f"- 人気1号艇ベースの万舟率: {pct(summary['base_manshu_rate_pct'])}",
        "",
        "## 上位条件",
        "",
    ]
    for i, row in enumerate(selected[:5], 1):
        lines.extend(
            [
                f"### {i}. {row['condition']}",
                "",
                f"- レース数: {row['sample_races']}",
                f"- 1号艇1着外率: {pct(row['b1_not_win_rate_pct'])}（ベース比 {row['b1_not_win_lift_pp']:+.2f}pt）",
                f"- 1号艇3着外率: {pct(row['b1_top3_miss_rate_pct'])}",
                f"- 万舟率: {pct(row['manshu_rate_pct'])}（ベース比 {row['manshu_lift_pp']:+.2f}pt）",
                f"- 3〜6号艇1着率: {pct(row['winner_3to6_pct'])}",
                f"- 5/6号艇3着内率: {pct(row['outer56_top3_pct'])}",
                "",
            ]
        )
    lines.extend(
        [
            "## 注意",
            "",
            "- この分析は、保存済み三連単オッズがある期間だけを使っています。",
            "- 結果や配当は目的変数としてだけ使い、条件側には入れていません。",
            "- サンプル数が少ない条件は、ランキング補正では強すぎないボーナスとして扱います。",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--boaters-db", default=str(pick_existing(DESKTOP_BOATERS_DB, DEFAULT_BOATERS_DB)))
    parser.add_argument("--odds-db", default=str(DESKTOP_ODDS_DB))
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--min-sample", type=int, default=20)
    parser.add_argument("--csv-out", default=str(OUT_CSV))
    parser.add_argument("--json-out", default=str(OUT_JSON))
    parser.add_argument("--md-out", default=str(OUT_MD))
    args = parser.parse_args()

    if not Path(args.odds_db).exists():
        raise SystemExit(f"odds db not found: {args.odds_db}")
    if not Path(args.boaters_db).exists():
        raise SystemExit(f"boaters db not found: {args.boaters_db}")

    top5 = load_latest_top5_odds(args.odds_db, args.start_date, args.end_date)
    if top5.empty:
        raise SystemExit("no odds rows found")
    start_date = args.start_date or str(top5["date"].min())
    end_date = args.end_date or str(top5["date"].max())
    boats = load_boaters_boats(args.boaters_db, start_date, end_date)
    df = build_race_features(boats, top5)

    min_sample = args.min_sample
    selected, summary = search_conditions(df, min_sample)
    while len(selected) < 5 and min_sample > 8:
        min_sample -= 4
        selected, summary = search_conditions(df, min_sample)

    joined_start_date = str(df["date"].min()) if not df.empty else start_date
    joined_end_date = str(df["date"].max()) if not df.empty else end_date
    data_summary = {
        "start_date": joined_start_date,
        "end_date": joined_end_date,
        "odds_start_date": start_date,
        "odds_end_date": end_date,
        "joined_races": int(len(df)),
        "min_sample_used": min_sample,
        "odds_db": str(args.odds_db),
        "boaters_db": str(args.boaters_db),
    }
    payload = {
        "data_summary": data_summary,
        "base_summary": summary,
        "conditions": selected,
    }
    write_csv(Path(args.csv_out), selected)
    Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json_out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(Path(args.md_out), summary, selected, data_summary)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
