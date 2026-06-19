#!/usr/bin/env python3
import argparse
import csv
import html
import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "output"
REPORT_DIR = OUT_DIR / "boaters_report"
HISTORY_DB = OUT_DIR / "boaters_all_races.sqlite"
DEFAULT_LOGIC_CSV = ROOT / "data" / "model" / "manshu_condition_combo_search.csv"

FIXED_TOP6_VENUES = {"平和島", "鳴門", "戸田", "桐生", "江戸川", "浜名湖"}
FIXED_TOP10_VENUES = FIXED_TOP6_VENUES | {"児島", "三国", "宮島", "若松"}

AVGDIFF_LANE_EDGES = {
    ("芦屋", 5): {"threshold": 0.40, "top3_uplift_pp": 27.28, "win_uplift_pp": 8.55},
    ("下関", 5): {"threshold": 0.40, "top3_uplift_pp": 26.30, "win_uplift_pp": 4.95},
    ("福岡", 6): {"threshold": 0.40, "top3_uplift_pp": 25.69, "win_uplift_pp": 4.06},
    ("びわこ", 5): {"threshold": 0.40, "top3_uplift_pp": 23.75, "win_uplift_pp": 8.64},
    ("芦屋", 6): {"threshold": 0.40, "top3_uplift_pp": 23.04, "win_uplift_pp": 5.69},
    ("住之江", 6): {"threshold": 0.40, "top3_uplift_pp": 23.00, "win_uplift_pp": 4.63},
    ("浜名湖", 5): {"threshold": 0.40, "top3_uplift_pp": 22.98, "win_uplift_pp": 9.50},
    ("尼崎", 6): {"threshold": 0.40, "top3_uplift_pp": 22.88, "win_uplift_pp": 3.72},
    ("徳山", 6): {"threshold": 0.40, "top3_uplift_pp": 22.26, "win_uplift_pp": 4.44},
    ("三国", 5): {"threshold": 0.40, "top3_uplift_pp": 21.80, "win_uplift_pp": 7.18},
    ("平和島", 4): {"threshold": 0.40, "top3_uplift_pp": 21.55, "win_uplift_pp": 10.54},
    ("下関", 4): {"threshold": 0.40, "top3_uplift_pp": 21.24, "win_uplift_pp": 8.40},
    ("戸田", 5): {"threshold": 0.40, "top3_uplift_pp": 21.21, "win_uplift_pp": 6.97},
    ("児島", 5): {"threshold": 0.40, "top3_uplift_pp": 21.15, "win_uplift_pp": 6.34},
}


EXHIBIT_PREFIXES = (
    "b1_tenji",
    "b1_isshu",
    "b1_exhibit",
    "outer56_tenji",
    "outer56_isshu",
    "outer56_exhibit",
    "outer46_exhibit",
    "outer56_low_aiplus_exhibit",
    "outer56_low_aipred_exhibit",
    "outer46_low_aiplus_exhibit",
)


PLACE_NAMES = {
    1: "桐生",
    2: "戸田",
    3: "江戸川",
    4: "平和島",
    5: "多摩川",
    6: "浜名湖",
    7: "蒲郡",
    8: "常滑",
    9: "津",
    10: "三国",
    11: "びわこ",
    12: "住之江",
    13: "尼崎",
    14: "鳴門",
    15: "丸亀",
    16: "児島",
    17: "宮島",
    18: "徳山",
    19: "下関",
    20: "若松",
    21: "芦屋",
    22: "福岡",
    23: "唐津",
    24: "大村",
}


def connect_ro(path):
    return sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True)


def fmt_num(value):
    if value is None or pd.isna(value):
        return "-"
    return f"{int(round(float(value))):,}"


def fmt_pct(value):
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):.2f}%"


def fmt_time(value):
    if value is None or pd.isna(value):
        return "-"
    return str(value)


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def historical_venue_sets(history_db):
    if not history_db or not Path(history_db).exists():
        return set(FIXED_TOP6_VENUES), set(FIXED_TOP10_VENUES)
    with connect_ro(history_db) as con:
        df = pd.read_sql_query(
            """
            SELECT place_name, AVG(CASE WHEN result_payout3t1 >= 10000 THEN 1.0 ELSE 0 END) AS rate
            FROM races
            WHERE result_payout3t1 IS NOT NULL
            GROUP BY place_name
            ORDER BY rate DESC
            """,
            con,
        )
    return set(df.head(6)["place_name"]), set(df.head(10)["place_name"])


def daily_features(today_db, target_date):
    with connect_ro(today_db) as con:
        sql = """
        WITH base AS (
            SELECT
                r.race_id,
                r.date,
                r.place_id,
                r.place_name,
                r.slug,
                r.round,
                r.title,
                r.race_grade,
                r.deadline_time,
                r.weather,
                r.wind_speed,
                r.wave_height,
                r.result_payout3t1 AS payout,
                r.winning_number3t1 AS trifecta,
                b.boat_number,
                b.is_absent,
                b.ai_3ren_pct,
                b.general_3ren_pct,
                CASE
                    WHEN b.ai_3ren_pct IS NOT NULL AND b.general_3ren_pct IS NOT NULL
                    THEN b.ai_3ren_pct + b.general_3ren_pct
                END AS ai_plus,
                b.ai_prediction_pct,
                b.st_rank_general,
                b.tenji_time,
                b.isshu_time,
                b.avg_isshu_diff,
                b.tenji_rank,
                b.start_tenji_rank,
                b.nige_pct_year,
                b.sasare_pct_year,
                b.makurare_pct_year
            FROM races r
            JOIN race_boats b ON b.race_id = r.race_id
            WHERE r.date = ?
              AND COALESCE(b.is_absent, 0) = 0
        ),
        ranked AS (
            SELECT
                base.*,
                RANK() OVER (
                    PARTITION BY race_id
                    ORDER BY CASE WHEN ai_plus IS NULL THEN 1 ELSE 0 END, ai_plus DESC
                ) AS ai_plus_rank_raw,
                ROW_NUMBER() OVER (
                    PARTITION BY race_id
                    ORDER BY CASE WHEN ai_plus IS NULL THEN 1 ELSE 0 END, ai_plus DESC, boat_number
                ) AS ai_plus_order_raw,
                RANK() OVER (
                    PARTITION BY race_id
                    ORDER BY CASE WHEN ai_prediction_pct IS NULL THEN 1 ELSE 0 END, ai_prediction_pct DESC
                ) AS ai_prediction_rank_raw,
                RANK() OVER (
                    PARTITION BY race_id
                    ORDER BY CASE WHEN tenji_time IS NULL THEN 1 ELSE 0 END, tenji_time ASC
                ) AS tenji_time_rank_raw,
                RANK() OVER (
                    PARTITION BY race_id
                    ORDER BY CASE WHEN isshu_time IS NULL THEN 1 ELSE 0 END, isshu_time ASC
                ) AS isshu_rank_raw
            FROM base
        ),
        rb AS (
            SELECT
                *,
                CASE WHEN ai_plus IS NOT NULL THEN ai_plus_rank_raw END AS ai_plus_rank,
                CASE WHEN ai_plus IS NOT NULL THEN ai_plus_order_raw END AS ai_plus_order,
                CASE WHEN ai_prediction_pct IS NOT NULL THEN ai_prediction_rank_raw END AS ai_prediction_rank,
                CASE WHEN tenji_time IS NOT NULL THEN tenji_time_rank_raw END AS tenji_time_rank,
                CASE WHEN isshu_time IS NOT NULL THEN isshu_rank_raw END AS isshu_rank
            FROM ranked
        )
        SELECT
            race_id,
            MAX(date) AS date,
            MAX(place_id) AS place_id,
            MAX(place_name) AS place_name,
            MAX(slug) AS slug,
            MAX(round) AS round_no,
            MAX(title) AS title,
            MAX(race_grade) AS race_grade,
            MAX(deadline_time) AS deadline_time,
            MAX(weather) AS weather,
            MAX(wind_speed) AS wind_speed,
            MAX(wave_height) AS wave_height,
            MAX(payout) AS payout,
            MAX(trifecta) AS trifecta,

            MAX(CASE WHEN boat_number = 1 THEN ai_prediction_pct END) AS b1_ai_prediction_pct,
            MAX(CASE WHEN boat_number = 1 THEN ai_plus END) AS b1_ai_plus,
            MAX(CASE WHEN boat_number = 1 THEN ai_plus_order END) AS b1_ai_plus_order,
            MAX(CASE WHEN boat_number = 1 THEN ai_3ren_pct END) AS b1_ai_3ren_pct,
            MAX(CASE WHEN boat_number = 1 THEN general_3ren_pct END) AS b1_general_3ren_pct,
            MAX(CASE WHEN boat_number = 1 THEN st_rank_general END) AS b1_st_rank_general,
            MAX(CASE WHEN boat_number = 1 THEN tenji_time END) AS b1_tenji_time,
            MAX(CASE WHEN boat_number = 1 THEN isshu_time END) AS b1_isshu_time,
            MAX(CASE WHEN boat_number = 1 THEN avg_isshu_diff END) AS b1_avg_isshu_diff,
            MAX(CASE WHEN boat_number = 1 THEN tenji_rank END) AS b1_tenji_rank,
            MAX(CASE WHEN boat_number = 1 THEN tenji_time_rank END) AS b1_tenji_time_rank,
            MAX(CASE WHEN boat_number = 1 THEN isshu_rank END) AS b1_isshu_rank,
            MAX(CASE WHEN boat_number = 1 THEN nige_pct_year END) AS b1_nige_pct,
            MAX(CASE WHEN boat_number = 1 THEN sasare_pct_year + makurare_pct_year END) AS b1_loss_pct,

            MAX(CASE WHEN boat_number = 2 THEN avg_isshu_diff END) AS b2_avg_isshu_diff,
            MAX(CASE WHEN boat_number = 3 THEN avg_isshu_diff END) AS b3_avg_isshu_diff,
            MAX(CASE WHEN boat_number = 4 THEN avg_isshu_diff END) AS b4_avg_isshu_diff,
            MAX(CASE WHEN boat_number = 5 THEN avg_isshu_diff END) AS b5_avg_isshu_diff,
            MAX(CASE WHEN boat_number = 6 THEN avg_isshu_diff END) AS b6_avg_isshu_diff,
            MAX(CASE WHEN boat_number = 2 THEN tenji_time_rank END) AS b2_tenji_time_rank,
            MAX(CASE WHEN boat_number = 3 THEN tenji_time_rank END) AS b3_tenji_time_rank,
            MAX(CASE WHEN boat_number = 4 THEN tenji_time_rank END) AS b4_tenji_time_rank,
            MAX(CASE WHEN boat_number = 5 THEN tenji_time_rank END) AS b5_tenji_time_rank,
            MAX(CASE WHEN boat_number = 6 THEN tenji_time_rank END) AS b6_tenji_time_rank,
            MAX(CASE WHEN boat_number = 2 THEN tenji_rank END) AS b2_tenji_rank,
            MAX(CASE WHEN boat_number = 3 THEN tenji_rank END) AS b3_tenji_rank,
            MAX(CASE WHEN boat_number = 4 THEN tenji_rank END) AS b4_tenji_rank,
            MAX(CASE WHEN boat_number = 5 THEN tenji_rank END) AS b5_tenji_rank,
            MAX(CASE WHEN boat_number = 6 THEN tenji_rank END) AS b6_tenji_rank,
            MAX(CASE WHEN boat_number = 1 THEN CASE WHEN tenji_time_rank = 1 AND isshu_rank = 1 THEN 1 ELSE 0 END END) AS b1_double_time,
            MAX(CASE WHEN boat_number = 2 THEN CASE WHEN tenji_time_rank = 1 AND isshu_rank = 1 THEN 1 ELSE 0 END END) AS b2_double_time,
            MAX(CASE WHEN boat_number = 3 THEN CASE WHEN tenji_time_rank = 1 AND isshu_rank = 1 THEN 1 ELSE 0 END END) AS b3_double_time,
            MAX(CASE WHEN boat_number = 4 THEN CASE WHEN tenji_time_rank = 1 AND isshu_rank = 1 THEN 1 ELSE 0 END END) AS b4_double_time,
            MAX(CASE WHEN boat_number = 5 THEN CASE WHEN tenji_time_rank = 1 AND isshu_rank = 1 THEN 1 ELSE 0 END END) AS b5_double_time,
            MAX(CASE WHEN boat_number = 6 THEN CASE WHEN tenji_time_rank = 1 AND isshu_rank = 1 THEN 1 ELSE 0 END END) AS b6_double_time,
            MAX(CASE WHEN ai_plus_order = 6 THEN boat_number END) AS ai_rank6_boat,
            MAX(CASE WHEN ai_plus_order = 6 THEN avg_isshu_diff END) AS ai_rank6_avg_isshu_diff,
            MAX(CASE WHEN ai_plus_order = 6 THEN tenji_time_rank END) AS ai_rank6_tenji_time_rank,
            MAX(CASE WHEN ai_plus_order = 6 THEN tenji_rank END) AS ai_rank6_tenji_rank,
            MAX(CASE WHEN ai_plus_order = 5 THEN boat_number END) AS ai_rank5_boat,
            MAX(CASE WHEN ai_plus_order = 5 THEN avg_isshu_diff END) AS ai_rank5_avg_isshu_diff,
            MAX(CASE WHEN ai_plus_order = 5 THEN tenji_time_rank END) AS ai_rank5_tenji_time_rank,
            MAX(CASE WHEN ai_plus_order = 5 THEN tenji_rank END) AS ai_rank5_tenji_rank,

            MIN(CASE WHEN boat_number IN (5, 6) THEN tenji_time END) AS outer56_best_tenji_time,
            MIN(CASE WHEN boat_number IN (5, 6) THEN isshu_time END) AS outer56_best_isshu_time,
            MAX(CASE WHEN boat_number IN (5, 6) THEN avg_isshu_diff END) AS outer56_best_avg_isshu_diff,
            MAX(CASE WHEN boat_number IN (5, 6) THEN ai_prediction_pct END) AS outer56_best_ai_prediction_pct,
            MAX(CASE WHEN boat_number IN (5, 6) THEN ai_plus END) AS outer56_best_ai_plus,

            SUM(CASE WHEN boat_number IN (5, 6) AND tenji_time IS NOT NULL AND tenji_time_rank <= 2 THEN 1 ELSE 0 END) AS outer56_tenji_top2_count,
            SUM(CASE WHEN boat_number IN (5, 6) AND isshu_time IS NOT NULL AND isshu_rank <= 2 THEN 1 ELSE 0 END) AS outer56_isshu_top2_count,
            SUM(CASE WHEN boat_number IN (5, 6)
                       AND (
                         (tenji_time IS NOT NULL AND tenji_time_rank <= 2)
                         OR (isshu_time IS NOT NULL AND isshu_rank <= 2)
                         OR (tenji_rank IS NOT NULL AND tenji_rank <= 2)
                       ) THEN 1 ELSE 0 END) AS outer56_exhibit_top2_count,
            SUM(CASE WHEN boat_number IN (4, 5, 6)
                       AND (
                         (tenji_time IS NOT NULL AND tenji_time_rank <= 2)
                         OR (isshu_time IS NOT NULL AND isshu_rank <= 2)
                         OR (tenji_rank IS NOT NULL AND tenji_rank <= 2)
                       ) THEN 1 ELSE 0 END) AS outer46_exhibit_top2_count,
            SUM(CASE WHEN boat_number IN (5, 6)
                       AND ai_plus_rank >= 5
                       AND (
                         (tenji_time IS NOT NULL AND tenji_time_rank <= 2)
                         OR (isshu_time IS NOT NULL AND isshu_rank <= 2)
                         OR (tenji_rank IS NOT NULL AND tenji_rank <= 2)
                       ) THEN 1 ELSE 0 END) AS outer56_low_aiplus_exhibit_top2_count,
            SUM(CASE WHEN boat_number IN (5, 6)
                       AND ai_prediction_rank >= 5
                       AND (
                         (tenji_time IS NOT NULL AND tenji_time_rank <= 2)
                         OR (isshu_time IS NOT NULL AND isshu_rank <= 2)
                         OR (tenji_rank IS NOT NULL AND tenji_rank <= 2)
                       ) THEN 1 ELSE 0 END) AS outer56_low_aipred_exhibit_top2_count,
            SUM(CASE WHEN boat_number IN (4, 5, 6)
                       AND ai_plus_rank >= 5
                       AND (
                         (tenji_time IS NOT NULL AND tenji_time_rank <= 2)
                         OR (isshu_time IS NOT NULL AND isshu_rank <= 2)
                         OR (tenji_rank IS NOT NULL AND tenji_rank <= 2)
                       ) THEN 1 ELSE 0 END) AS outer46_low_aiplus_exhibit_top2_count,
            SUM(CASE WHEN boat_number IN (2, 3, 4)
                       AND tenji_time_rank = 1
                       AND isshu_rank = 1 THEN 1 ELSE 0 END) AS mid234_double_time_count,
            SUM(CASE WHEN boat_number IN (4, 5, 6)
                       AND tenji_time_rank = 1
                       AND isshu_rank = 1 THEN 1 ELSE 0 END) AS outer46_double_time_count,
            SUM(CASE WHEN boat_number IN (5, 6)
                       AND tenji_time_rank = 1
                       AND isshu_rank = 1 THEN 1 ELSE 0 END) AS outer56_double_time_count,

            SUM(CASE WHEN tenji_time IS NOT NULL THEN 1 ELSE 0 END) AS tenji_boats,
            SUM(CASE WHEN isshu_time IS NOT NULL THEN 1 ELSE 0 END) AS isshu_boats
        FROM rb
        GROUP BY race_id
        ORDER BY place_id, round_no
        """
        df = pd.read_sql_query(sql, con, params=(target_date,))
    df["outer56_tenji_advantage"] = df["b1_tenji_time"] - df["outer56_best_tenji_time"]
    df["outer56_isshu_advantage"] = df["b1_isshu_time"] - df["outer56_best_isshu_time"]
    return df


def mask_lt(series, value):
    return (series.notna() & (series < value)).to_numpy()


def mask_le(series, value):
    return (series.notna() & (series <= value)).to_numpy()


def mask_ge(series, value):
    return (series.notna() & (series >= value)).to_numpy()


def atom_masks(df, top6_venues, top10_venues):
    n = len(df)
    masks = {
        "all": np.ones(n, dtype=bool),
        "venue_top6": df["place_name"].isin(top6_venues).to_numpy(),
        "venue_top10": df["place_name"].isin(top10_venues).to_numpy(),
        "round_early": mask_le(df["round_no"], 6),
        "round_late": mask_ge(df["round_no"], 7),
        "round_1_3": mask_le(df["round_no"], 3),
        "round_10_12": mask_ge(df["round_no"], 10),
        "wind5": mask_ge(df["wind_speed"], 5),
        "wave5": mask_ge(df["wave_height"], 5),
        "wind_or_wave": mask_ge(df["wind_speed"], 5) | mask_ge(df["wave_height"], 5),
        "b1_st_rank_ge4": mask_ge(df["b1_st_rank_general"], 4),
        "b1_st_rank_ge5": mask_ge(df["b1_st_rank_general"], 5),
        "b1_tenji_rank_ge4": mask_ge(df["b1_tenji_rank"], 4),
        "b1_tenji_rank_ge5": mask_ge(df["b1_tenji_rank"], 5),
        "b1_tenji_time_rank_ge4": mask_ge(df["b1_tenji_time_rank"], 4),
        "b1_tenji_time_rank_ge5": mask_ge(df["b1_tenji_time_rank"], 5),
        "b1_isshu_rank_ge4": mask_ge(df["b1_isshu_rank"], 4),
        "b1_isshu_rank_ge5": mask_ge(df["b1_isshu_rank"], 5),
        "b1_exhibit_bad_both": mask_ge(df["b1_tenji_time_rank"], 4) & mask_ge(df["b1_isshu_rank"], 4),
        "outer56_tenji_top2": mask_ge(df["outer56_tenji_top2_count"], 1),
        "outer56_isshu_top2": mask_ge(df["outer56_isshu_top2_count"], 1),
        "outer56_exhibit_top2": mask_ge(df["outer56_exhibit_top2_count"], 1),
        "outer56_exhibit_top2_two": mask_ge(df["outer56_exhibit_top2_count"], 2),
        "outer46_exhibit_top2": mask_ge(df["outer46_exhibit_top2_count"], 1),
        "outer56_low_aiplus_exhibit_top2": mask_ge(df["outer56_low_aiplus_exhibit_top2_count"], 1),
        "outer56_low_aipred_exhibit_top2": mask_ge(df["outer56_low_aipred_exhibit_top2_count"], 1),
        "outer46_low_aiplus_exhibit_top2": mask_ge(df["outer46_low_aiplus_exhibit_top2_count"], 1),
    }
    for place in PLACE_NAMES.values():
        masks[f"venue_{place}"] = df["place_name"].eq(place).to_numpy()
    for value in [20, 25, 30, 35, 40]:
        masks[f"b1_ai_pred_lt{value}"] = mask_lt(df["b1_ai_prediction_pct"], value)
    for value in [110, 120, 130, 140, 150]:
        masks[f"b1_aiplus_lt{value}"] = mask_lt(df["b1_ai_plus"], value)
    for value in [45, 50, 55, 60]:
        masks[f"b1_general_lt{value}"] = mask_lt(df["b1_general_3ren_pct"], value)
    for value in [35, 40, 45, 50]:
        masks[f"b1_nige_lt{value}"] = mask_lt(df["b1_nige_pct"], value)
    for value in [30, 35, 40, 45]:
        masks[f"b1_loss_ge{value}"] = mask_ge(df["b1_loss_pct"], value)
    for value in [0.03, 0.05, 0.08, 0.10]:
        masks[f"outer56_tenji_adv_ge{int(value * 100):02d}"] = mask_ge(df["outer56_tenji_advantage"], value)
    for value in [0.05, 0.10, 0.15, 0.20]:
        masks[f"outer56_isshu_adv_ge{int(value * 100):02d}"] = mask_ge(df["outer56_isshu_advantage"], value)
    for value in [10, 12, 15]:
        masks[f"outer56_ai_pred_ge{value}"] = mask_ge(df["outer56_best_ai_prediction_pct"], value)
    for value in [100, 110, 120]:
        masks[f"outer56_aiplus_ge{value}"] = mask_ge(df["outer56_best_ai_plus"], value)
    return masks


def num(value):
    if value is None or pd.isna(value):
        return None
    return float(value)


def tenji_rank_use(race, boat):
    rank = num(race.get(f"b{boat}_tenji_rank"))
    if rank is None:
        rank = num(race.get(f"b{boat}_tenji_time_rank"))
    return rank


def add_edge(signals, signal_id, label, historical_rate_pct, bonus_pct, role, details=None):
    signals.append(
        {
            "id": signal_id,
            "label": label,
            "historical_rate_pct": historical_rate_pct,
            "bonus_pct": bonus_pct,
            "role": role,
            "details": details or {},
        }
    )


def composite_edge_signals(race):
    signals = []
    b1_avg = num(race.get("b1_avg_isshu_diff"))
    b1_tenji_rank = tenji_rank_use(race, 1)
    b1_loss = num(race.get("b1_loss_pct"))
    b1_nige = num(race.get("b1_nige_pct"))
    b1_ai_pred = num(race.get("b1_ai_prediction_pct"))
    b1_ai_order = num(race.get("b1_ai_plus_order"))
    outer_ai_pred = num(race.get("outer56_best_ai_prediction_pct"))
    outer_avg = num(race.get("outer56_best_avg_isshu_diff"))
    outer_exhibit_top2 = num(race.get("outer56_exhibit_top2_count")) or 0
    round_no = int(race.get("round_no") or 0)
    wind_wave = (num(race.get("wind_speed")) or 0) >= 5 or (num(race.get("wave_height")) or 0) >= 5
    rank6_boat = int(race.get("ai_rank6_boat") or 0)
    rank6_avg = num(race.get("ai_rank6_avg_isshu_diff"))
    rank6_tenji = num(race.get("ai_rank6_tenji_rank"))
    if rank6_tenji is None:
        rank6_tenji = num(race.get("ai_rank6_tenji_time_rank"))
    rank5_tenji = num(race.get("ai_rank5_tenji_rank"))
    if rank5_tenji is None:
        rank5_tenji = num(race.get("ai_rank5_tenji_time_rank"))
    double_time_boats = [boat for boat in range(1, 7) if int(race.get(f"b{boat}_double_time") or 0) == 1]

    if 1 in double_time_boats:
        add_edge(
            signals,
            "codex_double_time_1_hold",
            "1号艇ダブルタイム: 展示1位+1周1位でイン堅さ上昇",
            14.30,
            -3.2,
            "b1_hold_down",
            {
                "boat": 1,
                "win_rate_pct": 67.12,
                "top3_rate_pct": 88.26,
                "win_uplift_pp": 14.44,
                "top3_uplift_pp": 8.33,
            },
        )

    for boat, manshu_rate, win_rate, top3_rate, win_uplift, top3_uplift, bonus in [
        (2, 16.08, 27.90, 73.39, 14.97, 17.10, 2.8),
        (3, 15.77, 26.31, 73.32, 14.25, 19.80, 2.8),
        (4, 16.15, 23.34, 66.82, 13.78, 21.31, 2.6),
    ]:
        if boat in double_time_boats:
            add_edge(
                signals,
                f"codex_double_time_{boat}_head",
                f"{boat}号艇ダブルタイム: 頭候補上昇",
                manshu_rate,
                bonus,
                "head_up",
                {
                    "boat": boat,
                    "manshu_rate_pct": manshu_rate,
                    "win_rate_pct": win_rate,
                    "top3_rate_pct": top3_rate,
                    "win_uplift_pp": win_uplift,
                    "top3_uplift_pp": top3_uplift,
                },
            )

    for boat, manshu_rate, win_rate, top3_rate, win_uplift, top3_uplift, bonus in [
        (5, 18.76, 14.26, 56.51, 8.84, 22.01, 2.7),
        (6, 20.42, 8.56, 45.94, 5.62, 20.04, 2.3),
    ]:
        if boat in double_time_boats:
            add_edge(
                signals,
                f"codex_double_time_{boat}_top3",
                f"{boat}号艇ダブルタイム: 3着内候補上昇・消し回避",
                manshu_rate,
                bonus,
                "outer_top3_up",
                {
                    "boat": boat,
                    "manshu_rate_pct": manshu_rate,
                    "win_rate_pct": win_rate,
                    "top3_rate_pct": top3_rate,
                    "win_uplift_pp": win_uplift,
                    "top3_uplift_pp": top3_uplift,
                },
            )

    if (
        b1_loss is not None
        and b1_loss >= 45
        and b1_ai_pred is not None
        and b1_ai_pred < 25
        and outer_ai_pred is not None
        and outer_ai_pred >= 12
        and wind_wave
        and round_no <= 3
    ):
        add_edge(
            signals,
            "codex_buy_stable_front_wind11",
            "買い方候補: 1号艇逃げ失敗45%以上+AI予測25%未満、5/6AI予測12%以上、風波5以上、1〜3R",
            29.41,
            4.2,
            "buy_strategy_stable",
            {
                "strategy_id": "codex_stable_front_wind11",
                "train_manshu_roi_pct": 101.84,
                "validation_manshu_roi_pct": 118.03,
                "validation_50plus_roi_pct": 138.94,
                "points": "10-15",
            },
        )

    if (
        b1_nige is not None
        and b1_nige < 40
        and outer_ai_pred is not None
        and outer_ai_pred >= 12
        and rank6_tenji is not None
        and rank6_tenji <= 2
        and rank5_tenji is not None
        and rank5_tenji <= 2
        and round_no <= 3
    ):
        add_edge(
            signals,
            "codex_buy_stable_rank56_exhibit10",
            "買い方候補: 1号艇逃げ率40%未満、5/6AI予測12%以上、AI+5位/最下位が展示2位以内、1〜3R",
            29.36,
            3.8,
            "buy_strategy_stable",
            {
                "strategy_id": "codex_rank56_exhibit10",
                "train_manshu_roi_pct": 117.2,
                "validation_manshu_roi_pct": 169.43,
                "points": "10",
            },
        )

    if (
        b1_avg is not None
        and b1_avg <= -0.05
        and b1_tenji_rank is not None
        and b1_tenji_rank >= 5
        and b1_loss is not None
        and b1_loss >= 40
        and outer_avg is not None
        and outer_avg >= 0.14
        and round_no <= 6
    ):
        add_edge(
            signals,
            "codex_composite_front_b1bad_outer56_avg014",
            "1号艇平均との差-0.05以下+展示5位以下+逃げ失敗40%以上、5/6平均との差0.14以上",
            24.05,
            3.0,
            "manshu_rate_up",
        )

    if (
        b1_avg is not None
        and b1_avg <= 0
        and outer_avg is not None
        and outer_avg >= 0.10
        and outer_exhibit_top2 >= 1
        and round_no <= 6
        and wind_wave
    ):
        add_edge(
            signals,
            "codex_composite_front_weather_outer56_avg010",
            "1号艇平均との差0以下、5/6平均との差0.10以上+展示2位以内、風波5以上",
            23.93,
            2.8,
            "manshu_rate_up",
        )

    if (
        b1_ai_order is not None
        and b1_ai_order >= 5
        and b1_nige is not None
        and b1_nige < 45
        and b1_loss is not None
        and b1_loss >= 40
        and round_no >= 7
    ):
        add_edge(
            signals,
            "codex_composite_late_b1_aiplus5_loss40",
            "後半7〜12Rで1号艇AI+5位以下、逃げ率45未満+逃げ失敗40%以上",
            23.90,
            2.8,
            "manshu_rate_up",
        )

    if (
        b1_ai_order is not None
        and b1_ai_order >= 4
        and rank6_avg is not None
        and rank6_avg >= 0.10
        and outer_exhibit_top2 >= 1
    ):
        add_edge(
            signals,
            "codex_rank6_ana_avg010_b1weak_outertop2",
            "AI+最下位の平均との差0.10以上、1号艇AI+4位以下、5/6展示2位以内",
            24.26,
            3.2,
            "rank6_ana",
            {"rank6_boat": rank6_boat},
        )

    if (
        b1_ai_order is not None
        and b1_ai_order >= 4
        and rank6_boat in {5, 6}
        and rank6_avg is not None
        and rank6_avg < 0
        and rank6_tenji is not None
        and rank6_tenji >= 4
    ):
        add_edge(
            signals,
            "codex_rank6_keshi_outer_bad_avg",
            "AI+最下位が5/6号艇、平均との差マイナス、展示4位以下",
            15.05,
            -1.2,
            "rank6_keshi",
            {"rank6_boat": rank6_boat},
        )

    if b1_avg is not None and b1_avg <= 0 and b1_tenji_rank is not None and b1_tenji_rank >= 4:
        add_edge(
            signals,
            "codex_b1_fly_avg0_tenji4",
            "1号艇平均との差0以下+展示4位以下",
            19.32,
            1.2,
            "b1_fly_up",
        )

    if b1_avg is not None and b1_avg >= 0.10 and b1_ai_order == 1:
        add_edge(
            signals,
            "codex_b1_strong_avg010_aiplus1",
            "1号艇平均との差0.10以上+AI+1位",
            15.60,
            -1.6,
            "b1_hold_down",
        )

    place = race.get("place_name")
    for boat in range(1, 7):
        edge = AVGDIFF_LANE_EDGES.get((place, boat))
        boat_avg = num(race.get(f"b{boat}_avg_isshu_diff"))
        if edge and boat_avg is not None and boat_avg >= edge["threshold"]:
            bonus = min(2.2, max(0.8, edge["top3_uplift_pp"] / 12.0))
            add_edge(
                signals,
                f"codex_lane_avgdiff_{place}_{boat}_{edge['threshold']:.2f}",
                f"{place}{boat}号艇平均との差{edge['threshold']:.2f}以上で3着内上振れ",
                None,
                round(bonus, 2),
                "lane_top3_up",
                {
                    "boat": boat,
                    "threshold": edge["threshold"],
                    "top3_uplift_pp": edge["top3_uplift_pp"],
                    "win_uplift_pp": edge["win_uplift_pp"],
                },
            )

    signals.sort(key=lambda item: (item["bonus_pct"], item.get("historical_rate_pct") or 0), reverse=True)
    return signals


def composite_adjustment(signals):
    positive = [signal["bonus_pct"] for signal in signals if signal["bonus_pct"] > 0]
    negative = [signal["bonus_pct"] for signal in signals if signal["bonus_pct"] < 0]
    bonus = min(5.0, sum(positive[:3])) + sum(negative)
    return round(max(-3.0, min(5.0, bonus)), 2)


def composite_base_rate(signals):
    rates = [signal["historical_rate_pct"] for signal in signals if signal.get("historical_rate_pct") is not None]
    return max(rates) if rates else None


def composite_label(signals, limit=2):
    if not signals:
        return ""
    labels = [signal["label"] for signal in signals if signal["bonus_pct"] > 0]
    if not labels:
        labels = [signal["label"] for signal in signals]
    return " / ".join(labels[:limit])


def is_exhibit_atom(atom_id):
    return atom_id.startswith(EXHIBIT_PREFIXES)


def is_non_exhibit_signal_atom(atom_id):
    if is_exhibit_atom(atom_id):
        return False
    if atom_id == "all" or atom_id.startswith("venue_"):
        return False
    if atom_id.startswith("round_") or atom_id in {"wind5", "wave5", "wind_or_wave"}:
        return False
    return True


def split_atoms(combo_id):
    return [part.strip() for part in str(combo_id).split("&") if part.strip()]


def build_rankings(df, logic_rows, masks, threshold=27.0):
    actual_by_race = {race_id: [] for race_id in df["race_id"]}
    watch_by_race = {race_id: [] for race_id in df["race_id"]}
    unknown_atoms = set()

    for logic in logic_rows:
        atom_ids = split_atoms(logic["combo_id"])
        row_mask = np.ones(len(df), dtype=bool)
        non_exhibit_mask = np.ones(len(df), dtype=bool)
        has_exhibit = False
        has_non_exhibit_signal = False
        for atom_id in atom_ids:
            mask = masks.get(atom_id)
            if mask is None:
                unknown_atoms.add(atom_id)
                row_mask &= False
                non_exhibit_mask &= False
                continue
            row_mask &= mask
            if is_exhibit_atom(atom_id):
                has_exhibit = True
            else:
                non_exhibit_mask &= mask
                if is_non_exhibit_signal_atom(atom_id):
                    has_non_exhibit_signal = True

        for idx in np.flatnonzero(row_mask):
            actual_by_race[df.iloc[idx]["race_id"]].append(logic)
        if has_exhibit and has_non_exhibit_signal:
            for idx in np.flatnonzero(non_exhibit_mask):
                watch_by_race[df.iloc[idx]["race_id"]].append(logic)

    actual_rows = []
    watch_rows = []
    for _, race in df.iterrows():
        actual = actual_by_race.get(race["race_id"], [])
        watch = watch_by_race.get(race["race_id"], [])
        edge_signals = composite_edge_signals(race)
        if actual:
            actual_rows.append(row_summary(race, actual, status="確定", edge_signals=edge_signals))
        elif edge_signals:
            edge_row = row_summary(race, [], status="複合補正", edge_signals=edge_signals)
            has_positive_edge = any(signal["bonus_pct"] > 0 for signal in edge_signals)
            if has_positive_edge and edge_row["best_manshu_rate_pct"] >= threshold:
                actual_rows.append(edge_row)
        if watch:
            watch_rows.append(row_summary(race, watch, status="展示待ち", edge_signals=[]))

    key = lambda row: (
        row["best_manshu_rate_pct"],
        row["best_recent_rate_pct"] if row["best_recent_rate_pct"] is not None else -1,
        row["matched_logic_count"],
    )
    actual_rows.sort(key=key, reverse=True)
    watch_rows.sort(key=key, reverse=True)
    return actual_rows, watch_rows, sorted(unknown_atoms)


def row_summary(race, matches, status, edge_signals=None):
    edge_signals = edge_signals or []
    matches = sorted(
        matches,
        key=lambda item: (
            item["manshu_rate_pct"],
            item.get("recent_manshu_rate_pct_2025_2026") or -1,
            item.get("races") or 0,
        ),
        reverse=True,
    )
    best = matches[0] if matches else None
    edge_base = composite_base_rate(edge_signals)
    logic_rate = float(best["manshu_rate_pct"]) if best else None
    base_rate = max([rate for rate in [logic_rate, edge_base] if rate is not None], default=0.0)
    edge_bonus = composite_adjustment(edge_signals)
    adjusted_rate = max(0.0, min(40.0, base_rate + edge_bonus))
    if best:
        condition = best["condition"]
    else:
        condition = "Codex複合補正"
    edge_text = composite_label(edge_signals)
    if edge_text:
        condition = f"{condition} × Codex複合補正: {edge_text}"
    return {
        "status": status,
        "date": race["date"],
        "place_name": race["place_name"],
        "round": int(race["round_no"]),
        "deadline_time": race.get("deadline_time"),
        "race_id": race["race_id"],
        "best_manshu_rate_pct": round(adjusted_rate, 2),
        "base_manshu_rate_pct": None if logic_rate is None else round(logic_rate, 2),
        "composite_edge_base_rate_pct": None if edge_base is None else round(float(edge_base), 2),
        "composite_edge_bonus_pct": edge_bonus,
        "composite_edges": edge_signals,
        "best_recent_rate_pct": None
        if best is None or pd.isna(best.get("recent_manshu_rate_pct_2025_2026"))
        else float(best.get("recent_manshu_rate_pct_2025_2026")),
        "best_condition": condition,
        "matched_logic_count": len(matches),
        "payout": race.get("payout"),
        "trifecta": race.get("trifecta"),
        "b1_ai_prediction_pct": race.get("b1_ai_prediction_pct"),
        "b1_ai_plus": race.get("b1_ai_plus"),
        "b1_ai_plus_order": race.get("b1_ai_plus_order"),
        "b1_nige_pct": race.get("b1_nige_pct"),
        "b1_loss_pct": race.get("b1_loss_pct"),
        "b1_avg_isshu_diff": race.get("b1_avg_isshu_diff"),
        "b1_tenji_time": race.get("b1_tenji_time"),
        "b1_isshu_time": race.get("b1_isshu_time"),
        "outer56_best_avg_isshu_diff": race.get("outer56_best_avg_isshu_diff"),
        "outer56_best_ai_prediction_pct": race.get("outer56_best_ai_prediction_pct"),
        "outer56_best_tenji_time": race.get("outer56_best_tenji_time"),
        "outer56_best_isshu_time": race.get("outer56_best_isshu_time"),
        "ai_rank6_boat": race.get("ai_rank6_boat"),
        "ai_rank6_avg_isshu_diff": race.get("ai_rank6_avg_isshu_diff"),
        "ai_rank6_tenji_rank": race.get("ai_rank6_tenji_rank"),
        "ai_rank5_boat": race.get("ai_rank5_boat"),
        "ai_rank5_avg_isshu_diff": race.get("ai_rank5_avg_isshu_diff"),
        "ai_rank5_tenji_rank": race.get("ai_rank5_tenji_rank"),
        "double_time_boats": ",".join(str(boat) for boat in range(1, 7) if int(race.get(f"b{boat}_double_time") or 0) == 1),
        "boat1_double_time": int(race.get("b1_double_time") or 0),
        "mid234_double_time_count": int(race.get("mid234_double_time_count") or 0),
        "outer46_double_time_count": int(race.get("outer46_double_time_count") or 0),
        "outer56_double_time_count": int(race.get("outer56_double_time_count") or 0),
        "wind_speed": race.get("wind_speed"),
        "wave_height": race.get("wave_height"),
        "tenji_boats": int(race.get("tenji_boats") or 0),
        "isshu_boats": int(race.get("isshu_boats") or 0),
    }


def make_report(path, date_text, actual_rows, watch_rows, top_n):
    def table(rows):
        trs = []
        for i, row in enumerate(rows[:top_n], 1):
            trs.append(
                "<tr>"
                f"<td>{i}</td>"
                f"<td>{html.escape(row['status'])}</td>"
                f"<td>{html.escape(row['place_name'])}</td>"
                f"<td>{row['round']}R</td>"
                f"<td>{html.escape(str(row.get('deadline_time') or '-'))}</td>"
                f"<td>{fmt_pct(row['best_manshu_rate_pct'])}</td>"
                f"<td>{fmt_pct(row.get('base_manshu_rate_pct'))}</td>"
                f"<td>{fmt_pct(row.get('composite_edge_bonus_pct'))}</td>"
                f"<td>{fmt_pct(row['best_recent_rate_pct'])}</td>"
                f"<td>{fmt_num(row['matched_logic_count'])}</td>"
                f"<td>{html.escape(row['best_condition'])}</td>"
                f"<td>{fmt_pct(row.get('b1_ai_prediction_pct'))}</td>"
                f"<td>{fmt_time(row.get('b1_tenji_time'))}</td>"
                f"<td>{fmt_time(row.get('outer56_best_tenji_time'))}</td>"
                "</tr>"
            )
        return "\n".join(trs) or "<tr><td colspan='14'>該当なし</td></tr>"

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <title>{date_text} 万舟率ランキング</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Hiragino Sans", "Yu Gothic", sans-serif; margin: 32px; background: #f8fafc; color: #1f2933; }}
    h1 {{ margin-bottom: 6px; }}
    .meta {{ color: #62748a; margin-bottom: 24px; }}
    table {{ width: 100%; border-collapse: collapse; background: white; border: 1px solid #d9e2ec; margin-bottom: 28px; }}
    th, td {{ border-bottom: 1px solid #e6edf5; padding: 8px 9px; font-size: 13px; vertical-align: top; }}
    th {{ background: #edf2f7; text-align: left; }}
    td:nth-child(1), td:nth-child(4), td:nth-child(6), td:nth-child(7), td:nth-child(8), td:nth-child(10), td:nth-child(11), td:nth-child(12) {{ text-align: right; white-space: nowrap; }}
  </style>
</head>
<body>
  <h1>{date_text} 万舟率ランキング</h1>
  <div class="meta">27%以上ロジック + Codex複合補正 + ダブルタイム補正 / 確定ランキングは展示・1周が出ているレースのみ / 展示待ちは非展示条件だけ一致</div>
  <h2>確定ランキング TOP{top_n}</h2>
  <table>
    <thead><tr><th>#</th><th>状態</th><th>場</th><th>R</th><th>締切</th><th>補正後</th><th>元率</th><th>補正pt</th><th>直近率</th><th>一致数</th><th>代表条件</th><th>1号艇AI</th><th>1展示</th><th>5/6最速展示</th></tr></thead>
    <tbody>{table(actual_rows)}</tbody>
  </table>
  <h2>展示待ち候補 TOP{top_n}</h2>
  <table>
    <thead><tr><th>#</th><th>状態</th><th>場</th><th>R</th><th>締切</th><th>補正後</th><th>元率</th><th>補正pt</th><th>直近率</th><th>一致数</th><th>代表条件</th><th>1号艇AI</th><th>1展示</th><th>5/6最速展示</th></tr></thead>
    <tbody>{table(watch_rows)}</tbody>
  </table>
</body>
</html>
""",
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--today-db", required=True)
    parser.add_argument("--logic-csv", default=str(DEFAULT_LOGIC_CSV))
    parser.add_argument("--history-db", default=str(HISTORY_DB))
    parser.add_argument("--threshold", type=float, default=27.0)
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--csv-out")
    parser.add_argument("--json-out")
    parser.add_argument("--html-out")
    args = parser.parse_args()

    top6, top10 = historical_venue_sets(args.history_db)
    df = daily_features(args.today_db, args.date)
    logic_df = pd.read_csv(args.logic_csv)
    logic_df = logic_df[logic_df["manshu_rate_pct"] >= args.threshold].copy()
    masks = atom_masks(df, top6, top10)
    actual_rows, watch_rows, unknown_atoms = build_rankings(
        df,
        logic_df.to_dict("records"),
        masks,
        threshold=args.threshold,
    )

    base_name = f"manshu_daily_rank_{args.date}"
    csv_path = Path(args.csv_out) if args.csv_out else OUT_DIR / f"{base_name}.csv"
    json_path = Path(args.json_out) if args.json_out else OUT_DIR / f"{base_name}.json"
    html_path = Path(args.html_out) if args.html_out else REPORT_DIR / f"{base_name}.html"

    combined = actual_rows + watch_rows
    write_csv(csv_path, combined)
    payload = {
        "date": args.date,
        "threshold_pct": args.threshold,
        "logic_label": "Codex BOATERS展示込み 万舟率ロジック + 複合補正",
        "logic_summary": "既存の27%以上ロジックに、1号艇平均との差/展示弱化、5・6号艇の平均との差上振れ、AI+最下位の穴/消し判定、場×艇番平均との差エッジ、展示タイム+1周タイム1位のダブルタイム補正を加点・減点したランキング。",
        "races": int(len(df)),
        "races_with_full_tenji": int((df["tenji_boats"] >= 6).sum()),
        "races_with_full_isshu": int((df["isshu_boats"] >= 6).sum()),
        "actual_rank_top": actual_rows[: args.top_n],
        "watch_rank_top": watch_rows[: args.top_n],
        "unknown_atoms": unknown_atoms,
        "outputs": {
            "csv": str(csv_path),
            "json": str(json_path),
            "html": str(html_path),
        },
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    make_report(html_path, args.date, actual_rows, watch_rows, args.top_n)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
