#!/usr/bin/env python3
import argparse
import csv
import html
import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from build_boat_role_dataset import build_matchup_context, read_matchup_profiles


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "output"
REPORT_DIR = OUT_DIR / "boaters_report"
HISTORY_DB = OUT_DIR / "boaters_all_races.sqlite"
DEFAULT_LOGIC_CSV = ROOT / "data" / "model" / "manshu_condition_combo_search.csv"
DEFAULT_MATCHUP_PROFILE = ROOT / "data" / "analysis" / "matchup_profiles.csv"
TRIFECTA_ODDS_DB_CANDIDATES = [
    ROOT / "data" / "live_odds.db",
    Path.home() / "Desktop" / "kyotei_occult" / "data" / "live_odds.db",
]

FIXED_TOP6_VENUES = {"平和島", "鳴門", "戸田", "桐生", "江戸川", "浜名湖"}
FIXED_TOP10_VENUES = FIXED_TOP6_VENUES | {"児島", "三国", "宮島", "若松"}
SUMMER_MONTHS = {6, 7, 8}
SUMMER_B1_FAST_DIFF = 0.10
SUMMER_B1_SLOW_DIFF = -0.10
SUMMER_B1_FAST_NIGE_DELTA_PP = 15
SUMMER_B1_SLOW_NIGE_DELTA_PP = -17
SUPER_SLIT_TENJI_ADV = 0.10


def default_trifecta_odds_db():
    for path in TRIFECTA_ODDS_DB_CANDIDATES:
        if path.exists():
            return path
    return None

SLIT_FORMATION_STATS = {
    "b1_front_wall": {
        "label": "スリット隊形: 1前+2壁あり",
        "candidate_b1_win_pct": 34.93,
        "candidate_b1_fly_pct": 34.40,
        "candidate_winner_3to6_pct": 38.84,
        "candidate_outer56_top3_pct": 63.60,
        "candidate_manshu_rate_pct": 18.25,
        "bonus_pct": -1.2,
        "role": "b1_hold_down",
    },
    "b1_hole_vs_23": {
        "label": "スリット隊形: 1号艇が2/3号艇より0.020遅い",
        "candidate_b1_win_pct": 30.20,
        "candidate_b1_fly_pct": 38.18,
        "candidate_winner_3to6_pct": 45.89,
        "candidate_outer56_top3_pct": 63.18,
        "candidate_manshu_rate_pct": 19.07,
        "bonus_pct": 0.9,
        "role": "b1_fly_up",
    },
    "b2_wall_break_3peek": {
        "label": "スリット隊形: 2壁割れ+3覗き",
        "candidate_b1_win_pct": 31.40,
        "candidate_b1_fly_pct": 36.83,
        "candidate_winner_3to6_pct": 51.12,
        "candidate_outer56_top3_pct": 63.38,
        "candidate_manshu_rate_pct": 19.10,
        "bonus_pct": 1.2,
        "role": "head_up",
    },
    "b3_peek_vs_12": {
        "label": "スリット隊形: 3号艇が1/2号艇より0.015速い",
        "candidate_b1_win_pct": 31.00,
        "candidate_b1_fly_pct": 36.96,
        "candidate_winner_3to6_pct": 51.39,
        "candidate_outer56_top3_pct": 63.65,
        "candidate_manshu_rate_pct": 19.34,
        "bonus_pct": 1.1,
        "role": "head_up",
    },
    "b4_cadou_peek": {
        "label": "スリット隊形: 4カド覗き",
        "candidate_b1_win_pct": 29.81,
        "candidate_b1_fly_pct": 38.99,
        "candidate_winner_3to6_pct": 52.81,
        "candidate_outer56_top3_pct": 64.92,
        "candidate_manshu_rate_pct": 19.68,
        "bonus_pct": 1.5,
        "role": "head_up",
    },
    "outer456_pressure": {
        "label": "スリット隊形: 4〜6外圧",
        "candidate_b1_win_pct": 29.76,
        "candidate_b1_fly_pct": 38.20,
        "candidate_winner_3to6_pct": 52.31,
        "candidate_outer56_top3_pct": 68.77,
        "candidate_manshu_rate_pct": 20.87,
        "bonus_pct": 1.8,
        "role": "outer_top3_up",
    },
    "outer56_pressure_vs_1": {
        "label": "スリット隊形: 5/6外圧",
        "candidate_b1_win_pct": 29.26,
        "candidate_b1_fly_pct": 39.18,
        "candidate_winner_3to6_pct": 48.27,
        "candidate_outer56_top3_pct": 68.00,
        "candidate_manshu_rate_pct": 21.14,
        "bonus_pct": 1.9,
        "role": "outer_top3_up",
    },
    "b5_left_adv": {
        "label": "スリット隊形: 5号艇が左4より0.015速い",
        "candidate_b1_win_pct": 32.19,
        "candidate_b1_fly_pct": 35.17,
        "candidate_winner_3to6_pct": 45.45,
        "candidate_outer56_top3_pct": 68.55,
        "candidate_manshu_rate_pct": 20.30,
        "bonus_pct": 1.1,
        "role": "outer_top3_up",
    },
    "b6_left_adv": {
        "label": "スリット隊形: 6号艇が左5より0.015速い",
        "candidate_b1_win_pct": 31.88,
        "candidate_b1_fly_pct": 35.96,
        "candidate_winner_3to6_pct": 46.62,
        "candidate_outer56_top3_pct": 63.97,
        "candidate_manshu_rate_pct": 20.66,
        "bonus_pct": 1.0,
        "role": "outer_top3_up",
    },
    "center34_dent": {
        "label": "スリット隊形: 3/4中凹み",
        "candidate_b1_win_pct": 32.52,
        "candidate_b1_fly_pct": 34.90,
        "candidate_winner_3to6_pct": 43.68,
        "candidate_outer56_top3_pct": 66.36,
        "candidate_manshu_rate_pct": 20.09,
        "bonus_pct": 0.8,
        "role": "outer_top3_up",
    },
}

SUPER_SLIT_ALERT_STATS = {
    2: {
        "rows": 1492,
        "win_rate_pct": 29.56,
        "top3_rate_pct": 70.91,
        "makuri_win_rate_pct": 11.53,
        "win_uplift_pp": 16.13,
        "top3_uplift_pp": 13.90,
        "makuri_win_uplift_pp": 7.79,
        "manshu_rate_pct": 17.23,
        "bonus_pct": 0.6,
    },
    3: {
        "rows": 3857,
        "win_rate_pct": 22.45,
        "top3_rate_pct": 66.55,
        "makuri_win_rate_pct": 10.76,
        "win_uplift_pp": 10.21,
        "top3_uplift_pp": 12.94,
        "makuri_win_uplift_pp": 6.00,
        "manshu_rate_pct": 16.85,
        "bonus_pct": 0.5,
    },
    4: {
        "rows": 3454,
        "win_rate_pct": 21.63,
        "top3_rate_pct": 61.09,
        "makuri_win_rate_pct": 12.94,
        "win_uplift_pp": 11.91,
        "top3_uplift_pp": 15.45,
        "makuri_win_uplift_pp": 8.63,
        "manshu_rate_pct": 17.98,
        "bonus_pct": 1.0,
    },
    5: {
        "rows": 3140,
        "win_rate_pct": 12.68,
        "top3_rate_pct": 49.43,
        "makuri_win_rate_pct": 5.45,
        "win_uplift_pp": 7.03,
        "top3_uplift_pp": 14.32,
        "makuri_win_uplift_pp": 4.23,
        "manshu_rate_pct": 18.47,
        "bonus_pct": 1.2,
    },
    6: {
        "rows": 3249,
        "win_rate_pct": 8.90,
        "top3_rate_pct": 40.69,
        "makuri_win_rate_pct": 4.16,
        "win_uplift_pp": 5.94,
        "top3_uplift_pp": 14.58,
        "makuri_win_uplift_pp": 3.42,
        "manshu_rate_pct": 18.34,
        "bonus_pct": 1.1,
    },
}

JOSHI_KEYWORDS = ("女子", "レディース", "ヴィーナス", "ビーナス", "なでしこ")
JOSHI_RACE_KINDS = {"Lady", "Venus"}

JOSHI_STRATEGY_FACTORS = [
    {
        "id": "joshi_avg010_outer014_dekoboko_lady",
        "label": "女子戦攻略: 1号艇平均との差+0.10以下、5/6平均との差+0.14以上、5/6AI上振れ、デコボコ、オールレディース系",
        "atom_ids": [
            "b1_avgdiff_le010",
            "outer56_avgdiff_ge014",
            "outer56_ai_pred_ge10",
            "outer56_aiplus_ge100",
            "slit_dekoboko",
            "lady_series",
        ],
        "races": 60,
        "manshu_rate_pct": 30.00,
        "recent_manshu_rate_pct": 33.33,
        "b1_win_pct": 31.67,
        "b1_fly_pct": 36.67,
        "winner_3to6_pct": 43.33,
        "outer56_top3_pct": 80.00,
        "bonus_pct": 4.0,
    },
    {
        "id": "joshi_avg010_outer014_outer56_slit_early",
        "label": "女子戦攻略: 1号艇平均との差+0.10以下、5/6平均との差+0.14以上、5/6AI上振れ、5/6外圧、前半",
        "atom_ids": [
            "b1_avgdiff_le010",
            "outer56_avgdiff_ge014",
            "outer56_ai_pred_ge10",
            "outer56_aiplus_ge100",
            "slit_outer56_pressure_vs_1",
            "round_early",
        ],
        "races": 65,
        "manshu_rate_pct": 29.23,
        "recent_manshu_rate_pct": 31.37,
        "b1_win_pct": 32.31,
        "b1_fly_pct": 36.92,
        "winner_3to6_pct": 46.15,
        "outer56_top3_pct": 76.92,
        "bonus_pct": 3.8,
    },
    {
        "id": "joshi_avg010_outerai_outer456_lady",
        "label": "女子戦攻略: 1号艇平均との差+0.10以下、5/6AI上振れ、4〜6外圧、オールレディース系",
        "atom_ids": [
            "b1_avgdiff_le010",
            "outer56_ai_pred_ge10",
            "outer56_aiplus_ge100",
            "slit_outer456_pressure",
            "lady_series",
        ],
        "races": 62,
        "manshu_rate_pct": 29.03,
        "recent_manshu_rate_pct": 30.23,
        "b1_win_pct": 35.48,
        "b1_fly_pct": 38.71,
        "winner_3to6_pct": 43.55,
        "outer56_top3_pct": 87.10,
        "bonus_pct": 3.6,
    },
    {
        "id": "joshi_rank6_36_avg010_outer014_dekoboko_early",
        "label": "女子戦攻略: 1号艇平均との差+0.10以下、5/6平均との差+0.14以上、AI最下位3〜6号艇、デコボコ、前半",
        "atom_ids": [
            "b1_avgdiff_le010",
            "outer56_avgdiff_ge014",
            "outer56_ai_pred_ge10",
            "outer56_aiplus_ge100",
            "rank6_boat_3to6",
            "slit_dekoboko",
            "round_early",
        ],
        "races": 76,
        "manshu_rate_pct": 28.95,
        "recent_manshu_rate_pct": 29.82,
        "b1_win_pct": 34.21,
        "b1_fly_pct": 32.89,
        "winner_3to6_pct": 47.37,
        "outer56_top3_pct": 76.32,
        "bonus_pct": 3.6,
    },
    {
        "id": "joshi_b1_aiplus5_st4_outer46_exhibit_outer56_slit_early",
        "label": "女子戦攻略: 1号艇AI+5位以下かつ平均ST順位4位以下、4〜6展示上位、5/6外圧、デコボコ、前半",
        "atom_ids": [
            "b1_aiplus_ge5",
            "b1_st_rank_ge4",
            "outer46_exhibit_top2",
            "slit_outer56_pressure_vs_1",
            "slit_dekoboko",
            "round_early",
        ],
        "races": 65,
        "manshu_rate_pct": 27.69,
        "recent_manshu_rate_pct": 30.00,
        "b1_win_pct": 24.62,
        "b1_fly_pct": 56.92,
        "winner_3to6_pct": 47.69,
        "outer56_top3_pct": 66.15,
        "bonus_pct": 3.4,
    },
    {
        "id": "joshi_b1_aiplus4_rank6_avg010_top2_dekoboko",
        "label": "女子戦攻略: 1号艇AI+4位以下、4〜6展示上位、AI最下位の平均との差+0.10以上かつ展示/1周2位以内、デコボコ",
        "atom_ids": [
            "b1_aiplus_ge4",
            "outer46_exhibit_top2",
            "rank6_avgdiff_ge010",
            "rank6_exhibit_top2",
            "slit_dekoboko",
        ],
        "races": 60,
        "manshu_rate_pct": 28.33,
        "recent_manshu_rate_pct": 28.89,
        "b1_win_pct": 30.00,
        "b1_fly_pct": 45.00,
        "winner_3to6_pct": 36.67,
        "outer56_top3_pct": 68.33,
        "bonus_pct": 3.2,
    },
    {
        "id": "joshi_b1_avg030_aipred35_outerai_outer456_early",
        "label": "女子戦攻略: 1号艇平均との差+0.30以下かつAI予測35%未満、5/6AI上振れ、4〜6外圧、前半",
        "atom_ids": [
            "b1_avgdiff_le030",
            "b1_ai_pred_lt35",
            "outer56_ai_pred_ge10",
            "outer56_aiplus_ge100",
            "slit_outer456_pressure",
            "round_early",
        ],
        "races": 74,
        "manshu_rate_pct": 29.73,
        "recent_manshu_rate_pct": 25.93,
        "b1_win_pct": 29.73,
        "b1_fly_pct": 43.24,
        "winner_3to6_pct": 47.30,
        "outer56_top3_pct": 79.73,
        "bonus_pct": 3.2,
    },
]

BOATERS_B1_AVGDIFF_SUPER_DEBUFF = {
    "びわこ": {"threshold": 0.15, "win_delta_pp": -11.50, "top3_miss_delta_pp": 8.73},
    "三国": {"threshold": 0.00, "win_delta_pp": -10.27, "top3_miss_delta_pp": 8.03},
    "下関": {"threshold": 0.00, "win_delta_pp": -10.38, "top3_miss_delta_pp": 6.11},
    "丸亀": {"threshold": -0.10, "win_delta_pp": -11.21, "top3_miss_delta_pp": 7.28},
    "住之江": {"threshold": 0.15, "win_delta_pp": -11.56, "top3_miss_delta_pp": 7.85},
    "児島": {"threshold": 0.15, "win_delta_pp": -10.22, "top3_miss_delta_pp": 5.59},
    "唐津": {"threshold": 0.15, "win_delta_pp": -10.18, "top3_miss_delta_pp": 6.06},
    "多摩川": {"threshold": 0.10, "win_delta_pp": -9.34, "top3_miss_delta_pp": 8.42},
    "大村": {"threshold": -0.10, "win_delta_pp": -10.24, "top3_miss_delta_pp": 6.87},
    "宮島": {"threshold": 0.10, "win_delta_pp": -9.80, "top3_miss_delta_pp": 8.20},
    "尼崎": {"threshold": 0.10, "win_delta_pp": -10.96, "top3_miss_delta_pp": 8.90},
    "常滑": {"threshold": 0.10, "win_delta_pp": -10.71, "top3_miss_delta_pp": 7.14},
    "平和島": {"threshold": -0.05, "win_delta_pp": -10.87, "top3_miss_delta_pp": 8.57},
    "徳山": {"threshold": 0.20, "win_delta_pp": -11.47, "top3_miss_delta_pp": 8.00},
    "戸田": {"threshold": 0.15, "win_delta_pp": -9.86, "top3_miss_delta_pp": 8.10},
    "津": {"threshold": 0.15, "win_delta_pp": -10.30, "top3_miss_delta_pp": 5.97},
    "浜名湖": {"threshold": 0.20, "win_delta_pp": -10.24, "top3_miss_delta_pp": 6.89},
    "福岡": {"threshold": 0.25, "win_delta_pp": -10.08, "top3_miss_delta_pp": 6.32},
    "芦屋": {"threshold": 0.15, "win_delta_pp": -10.03, "top3_miss_delta_pp": 6.86},
    "若松": {"threshold": 0.15, "win_delta_pp": -10.67, "top3_miss_delta_pp": 6.61},
    "蒲郡": {"threshold": -0.05, "win_delta_pp": -10.98, "top3_miss_delta_pp": 7.64},
    "鳴門": {"threshold": 0.15, "win_delta_pp": -10.26, "top3_miss_delta_pp": 6.23},
}

BOATERS_AVGDIFF_GLOBAL_BUFFS = {
    1: {"buff_threshold": 0.30, "super_buff_threshold": 0.65, "win_uplift_pp": 5.34, "top3_uplift_pp": 3.56},
    2: {"buff_threshold": 0.00, "super_buff_threshold": 0.15, "win_uplift_pp": 4.56, "top3_uplift_pp": 8.29},
    3: {"buff_threshold": 0.00, "super_buff_threshold": 0.05, "win_uplift_pp": 4.38, "top3_uplift_pp": 8.73},
    4: {"buff_threshold": 0.00, "super_buff_threshold": 0.00, "win_uplift_pp": 3.48, "top3_uplift_pp": 8.83},
    5: {"buff_threshold": 0.00, "super_buff_threshold": 0.00, "win_uplift_pp": 2.53, "top3_uplift_pp": 9.43},
    6: {"buff_threshold": 0.00, "super_buff_threshold": 0.00, "win_uplift_pp": 1.60, "top3_uplift_pp": 8.83},
}

BOATERS_AVGDIFF_LANE_EDGES = {
    ("多摩川", 5): {"threshold": 0.00, "top3_uplift_pp": 12.55, "win_uplift_pp": 2.95, "samples": 2101},
    ("びわこ", 5): {"threshold": 0.00, "top3_uplift_pp": 11.66, "win_uplift_pp": 3.42, "samples": 1933},
    ("福岡", 5): {"threshold": 0.00, "top3_uplift_pp": 11.43, "win_uplift_pp": 2.25, "samples": 1384},
    ("びわこ", 4): {"threshold": 0.00, "top3_uplift_pp": 11.26, "win_uplift_pp": 4.47, "samples": 2139},
    ("浜名湖", 5): {"threshold": 0.00, "top3_uplift_pp": 11.02, "win_uplift_pp": 3.07, "samples": 2162},
    ("平和島", 5): {"threshold": 0.00, "top3_uplift_pp": 10.38, "win_uplift_pp": 2.97, "samples": 1998},
    ("常滑", 5): {"threshold": 0.00, "top3_uplift_pp": 10.29, "win_uplift_pp": 2.98, "samples": 2163},
    ("戸田", 5): {"threshold": 0.00, "top3_uplift_pp": 10.18, "win_uplift_pp": 3.04, "samples": 2029},
    ("福岡", 6): {"threshold": 0.00, "top3_uplift_pp": 10.12, "win_uplift_pp": 1.35, "samples": 1226},
    ("下関", 4): {"threshold": 0.00, "top3_uplift_pp": 10.03, "win_uplift_pp": 3.46, "samples": 2153},
    ("下関", 5): {"threshold": 0.00, "top3_uplift_pp": 9.95, "win_uplift_pp": 2.11, "samples": 1969},
    ("鳴門", 5): {"threshold": 0.10, "top3_uplift_pp": 9.94, "win_uplift_pp": 1.84, "samples": 1312},
    ("徳山", 6): {"threshold": 0.00, "top3_uplift_pp": 9.84, "win_uplift_pp": 1.84, "samples": 1991},
    ("多摩川", 6): {"threshold": 0.00, "top3_uplift_pp": 9.80, "win_uplift_pp": 1.91, "samples": 2154},
    ("下関", 6): {"threshold": 0.00, "top3_uplift_pp": 9.75, "win_uplift_pp": 1.65, "samples": 2177},
    ("常滑", 4): {"threshold": 0.00, "top3_uplift_pp": 9.65, "win_uplift_pp": 3.90, "samples": 2382},
    ("尼崎", 4): {"threshold": 0.00, "top3_uplift_pp": 9.63, "win_uplift_pp": 3.31, "samples": 2297},
    ("丸亀", 4): {"threshold": 0.00, "top3_uplift_pp": 9.51, "win_uplift_pp": 3.98, "samples": 2376},
    ("蒲郡", 3): {"threshold": 0.05, "top3_uplift_pp": 9.46, "win_uplift_pp": 3.68, "samples": 2287},
    ("徳山", 5): {"threshold": 0.00, "top3_uplift_pp": 9.46, "win_uplift_pp": 2.19, "samples": 1836},
    ("若松", 6): {"threshold": 0.00, "top3_uplift_pp": 9.35, "win_uplift_pp": 1.93, "samples": 1882},
    ("丸亀", 6): {"threshold": 0.00, "top3_uplift_pp": 9.35, "win_uplift_pp": 1.55, "samples": 2349},
    ("尼崎", 6): {"threshold": 0.00, "top3_uplift_pp": 9.35, "win_uplift_pp": 1.22, "samples": 2122},
    ("徳山", 4): {"threshold": 0.00, "top3_uplift_pp": 9.34, "win_uplift_pp": 3.70, "samples": 2093},
    ("常滑", 3): {"threshold": 0.05, "top3_uplift_pp": 9.33, "win_uplift_pp": 4.27, "samples": 2204},
    ("芦屋", 5): {"threshold": 0.00, "top3_uplift_pp": 9.30, "win_uplift_pp": 2.68, "samples": 1961},
    ("蒲郡", 4): {"threshold": 0.00, "top3_uplift_pp": 9.29, "win_uplift_pp": 2.98, "samples": 2497},
    ("三国", 5): {"threshold": 0.00, "top3_uplift_pp": 9.25, "win_uplift_pp": 2.13, "samples": 2041},
    ("びわこ", 2): {"threshold": 0.10, "top3_uplift_pp": 9.24, "win_uplift_pp": 5.01, "samples": 2364},
    ("福岡", 3): {"threshold": 0.10, "top3_uplift_pp": 9.22, "win_uplift_pp": 4.77, "samples": 2132},
    ("平和島", 4): {"threshold": 0.00, "top3_uplift_pp": 9.18, "win_uplift_pp": 3.89, "samples": 2182},
    ("徳山", 3): {"threshold": 0.05, "top3_uplift_pp": 9.12, "win_uplift_pp": 4.04, "samples": 2023},
    ("児島", 6): {"threshold": 0.00, "top3_uplift_pp": 9.06, "win_uplift_pp": 1.71, "samples": 2252},
    ("尼崎", 2): {"threshold": 0.10, "top3_uplift_pp": 9.05, "win_uplift_pp": 4.64, "samples": 2554},
    ("平和島", 3): {"threshold": 0.10, "top3_uplift_pp": 9.02, "win_uplift_pp": 6.19, "samples": 1635},
    ("大村", 4): {"threshold": 0.00, "top3_uplift_pp": 9.01, "win_uplift_pp": 2.29, "samples": 2695},
}


EXHIBIT_PREFIXES = (
    "b1_tenji",
    "b1_isshu",
    "b1_avgdiff",
    "b1_exhibit",
    "rank5_avgdiff",
    "rank6_avgdiff",
    "outer56_tenji",
    "outer56_isshu",
    "outer56_avgdiff",
    "outer56_both",
    "outer56_exhibit",
    "outer46_exhibit",
    "outer56_low_aiplus_exhibit",
    "outer56_low_aipred_exhibit",
    "outer46_low_aiplus_exhibit",
    "super_slit",
    "mid234_super_slit",
    "outer456_super_slit",
    "outer56_super_slit",
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


def add_empty_trifecta_odds_features(df):
    defaults = {
        "b1_trifecta_top5_1head": 0,
        "trifecta_top5_head1_count": np.nan,
        "trifecta_top5_count": np.nan,
        "trifecta_top1_odds": np.nan,
        "trifecta_top5_avg_odds": np.nan,
        "trifecta_top5_combos": "",
        "trifecta_odds_snapshot_at": "",
    }
    for col, value in defaults.items():
        if col not in df.columns:
            df[col] = value
    return df


def add_trifecta_odds_features(df, odds_db, target_date):
    df = add_empty_trifecta_odds_features(df)
    if not odds_db:
        return df
    odds_path = Path(odds_db)
    if not odds_path.exists():
        return df
    sql = """
    WITH latest AS (
      SELECT date, venue_code, race_no, MAX(snapshot_at) AS snapshot_at
      FROM odds_trifecta
      WHERE date = ?
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
    try:
        with connect_ro(odds_path) as con:
            odds = pd.read_sql_query(sql, con, params=(target_date,))
    except sqlite3.Error:
        return df
    if odds.empty:
        return df
    odds["odds"] = pd.to_numeric(odds["odds"], errors="coerce")
    odds = odds.sort_values(["date", "venue_code", "race_no", "odds", "combo"])
    odds["odds_rank"] = odds.groupby(["date", "venue_code", "race_no"]).cumcount() + 1
    top5 = odds[odds["odds_rank"] <= 5]
    features = top5.groupby(["date", "venue_code", "race_no"], as_index=False).agg(
        trifecta_top5_combos=("combo", lambda values: " ".join(map(str, values))),
        trifecta_top5_avg_odds=("odds", "mean"),
        trifecta_top1_odds=("odds", "min"),
        trifecta_top5_head1_count=("combo", lambda values: sum(str(value).startswith("1-") for value in values)),
        trifecta_top5_count=("combo", "count"),
        trifecta_odds_snapshot_at=("snapshot_at", "max"),
    )
    features["b1_trifecta_top5_1head"] = (
        features["trifecta_top5_count"].eq(5) & features["trifecta_top5_head1_count"].eq(5)
    ).astype(int)
    features["place_id"] = pd.to_numeric(features["venue_code"], errors="coerce")
    features["round_no"] = pd.to_numeric(features["race_no"], errors="coerce")
    features = features.drop(columns=["venue_code", "race_no"])
    merged = df.merge(features, on=["date", "place_id", "round_no"], how="left", suffixes=("", "_odds"))
    for col in [
        "b1_trifecta_top5_1head",
        "trifecta_top5_head1_count",
        "trifecta_top5_count",
        "trifecta_top1_odds",
        "trifecta_top5_avg_odds",
        "trifecta_top5_combos",
        "trifecta_odds_snapshot_at",
    ]:
        odds_col = f"{col}_odds"
        if odds_col in merged.columns:
            merged[col] = merged[odds_col].combine_first(merged[col])
            merged = merged.drop(columns=[odds_col])
    merged["b1_trifecta_top5_1head"] = merged["b1_trifecta_top5_1head"].fillna(0).astype(int)
    return merged


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


def daily_features(today_db, target_date, matchup_profiles=None):
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
                r.race_kind,
                r.series_title,
                r.deadline_time,
                r.weather,
                r.wind_speed,
                r.wave_height,
                r.result_payout3t1 AS payout,
                r.winning_number3t1 AS trifecta,
                b.boat_number,
                b.reg_no,
                b.is_absent,
                b.ai_3ren_pct,
                b.general_3ren_pct,
                CASE
                    WHEN b.ai_3ren_pct IS NOT NULL AND b.general_3ren_pct IS NOT NULL
                    THEN b.ai_3ren_pct + b.general_3ren_pct
                END AS ai_plus,
                b.ai_prediction_pct,
                b.odds_prediction_pct,
                b.st_rank_general,
                b.st_time_avg_general,
                b.tenji_time,
                b.isshu_time,
                b.avg_isshu_diff AS isshu_avg_diff,
                CASE
                    WHEN b.tenji_time IS NOT NULL AND b.isshu_time IS NOT NULL
                    THEN b.tenji_time + b.isshu_time
                END AS exhibit_combo_time,
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
                    ORDER BY CASE WHEN odds_prediction_pct IS NULL THEN 1 ELSE 0 END, odds_prediction_pct DESC
                ) AS odds_rank_raw,
                RANK() OVER (
                    PARTITION BY race_id
                    ORDER BY CASE WHEN tenji_time IS NULL THEN 1 ELSE 0 END, tenji_time ASC
                ) AS tenji_time_rank_raw,
                RANK() OVER (
                    PARTITION BY race_id
                    ORDER BY CASE WHEN isshu_time IS NULL THEN 1 ELSE 0 END, isshu_time ASC
                ) AS isshu_rank_raw,
                AVG(exhibit_combo_time) OVER (PARTITION BY race_id) AS avg_exhibit_combo_time
            FROM base
        ),
        rb AS (
            SELECT
                *,
                CASE
                    WHEN exhibit_combo_time IS NOT NULL AND avg_exhibit_combo_time IS NOT NULL
                    THEN avg_exhibit_combo_time - exhibit_combo_time
                END AS boaters_avgdiff,
                CASE WHEN ai_plus IS NOT NULL THEN ai_plus_rank_raw END AS ai_plus_rank,
                CASE WHEN ai_plus IS NOT NULL THEN ai_plus_order_raw END AS ai_plus_order,
                CASE WHEN ai_prediction_pct IS NOT NULL THEN ai_prediction_rank_raw END AS ai_prediction_rank,
                CASE WHEN odds_prediction_pct IS NOT NULL THEN odds_rank_raw END AS odds_rank,
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
            MAX(race_kind) AS race_kind,
            MAX(series_title) AS series_title,
            MAX(deadline_time) AS deadline_time,
            MAX(weather) AS weather,
            MAX(wind_speed) AS wind_speed,
            MAX(wave_height) AS wave_height,
            MAX(payout) AS payout,
            MAX(trifecta) AS trifecta,

            MAX(CASE WHEN boat_number = 1 THEN ai_prediction_pct END) AS b1_ai_prediction_pct,
            MAX(CASE WHEN boat_number = 1 THEN odds_prediction_pct END) AS b1_odds_prediction_pct,
            MAX(CASE WHEN boat_number = 1 THEN odds_rank END) AS b1_odds_rank,
            MAX(CASE WHEN boat_number = 1 THEN ai_plus END) AS b1_ai_plus,
            MAX(CASE WHEN boat_number = 1 THEN ai_plus_order END) AS b1_ai_plus_order,
            MAX(CASE WHEN boat_number = 1 THEN ai_3ren_pct END) AS b1_ai_3ren_pct,
            MAX(CASE WHEN boat_number = 1 THEN general_3ren_pct END) AS b1_general_3ren_pct,
            MAX(CASE WHEN boat_number = 1 THEN st_rank_general END) AS b1_st_rank_general,
            MAX(CASE WHEN boat_number = 1 THEN st_time_avg_general END) AS b1_st_time_avg_general,
            MAX(CASE WHEN boat_number = 1 THEN tenji_time END) AS b1_tenji_time,
            MAX(CASE WHEN boat_number = 1 THEN isshu_time END) AS b1_isshu_time,
            MAX(CASE WHEN boat_number = 1 THEN boaters_avgdiff END) AS b1_avg_isshu_diff,
            MAX(CASE WHEN boat_number = 1 THEN isshu_avg_diff END) AS b1_isshu_avg_diff,
            MAX(CASE WHEN boat_number = 1 THEN tenji_rank END) AS b1_tenji_rank,
            MAX(CASE WHEN boat_number = 1 THEN tenji_time_rank END) AS b1_tenji_time_rank,
            MAX(CASE WHEN boat_number = 1 THEN isshu_rank END) AS b1_isshu_rank,
            MAX(CASE WHEN boat_number = 1 THEN nige_pct_year END) AS b1_nige_pct,
            MAX(CASE WHEN boat_number = 1 THEN sasare_pct_year + makurare_pct_year END) AS b1_loss_pct,
            MAX(CASE WHEN boat_number = 1 THEN reg_no END) AS b1_registration_no,

            MAX(CASE WHEN boat_number = 2 THEN reg_no END) AS b2_registration_no,
            MAX(CASE WHEN boat_number = 3 THEN reg_no END) AS b3_registration_no,
            MAX(CASE WHEN boat_number = 4 THEN reg_no END) AS b4_registration_no,
            MAX(CASE WHEN boat_number = 5 THEN reg_no END) AS b5_registration_no,
            MAX(CASE WHEN boat_number = 6 THEN reg_no END) AS b6_registration_no,
            MAX(CASE WHEN boat_number = 2 THEN ai_prediction_pct END) AS b2_ai_prediction_pct,
            MAX(CASE WHEN boat_number = 3 THEN ai_prediction_pct END) AS b3_ai_prediction_pct,
            MAX(CASE WHEN boat_number = 4 THEN ai_prediction_pct END) AS b4_ai_prediction_pct,
            MAX(CASE WHEN boat_number = 5 THEN ai_prediction_pct END) AS b5_ai_prediction_pct,
            MAX(CASE WHEN boat_number = 6 THEN ai_prediction_pct END) AS b6_ai_prediction_pct,
            MAX(CASE WHEN boat_number = 2 THEN ai_plus END) AS b2_ai_plus,
            MAX(CASE WHEN boat_number = 3 THEN ai_plus END) AS b3_ai_plus,
            MAX(CASE WHEN boat_number = 4 THEN ai_plus END) AS b4_ai_plus,
            MAX(CASE WHEN boat_number = 5 THEN ai_plus END) AS b5_ai_plus,
            MAX(CASE WHEN boat_number = 6 THEN ai_plus END) AS b6_ai_plus,
            MAX(CASE WHEN boat_number = 2 THEN ai_plus_order END) AS b2_ai_plus_order,
            MAX(CASE WHEN boat_number = 3 THEN ai_plus_order END) AS b3_ai_plus_order,
            MAX(CASE WHEN boat_number = 4 THEN ai_plus_order END) AS b4_ai_plus_order,
            MAX(CASE WHEN boat_number = 5 THEN ai_plus_order END) AS b5_ai_plus_order,
            MAX(CASE WHEN boat_number = 6 THEN ai_plus_order END) AS b6_ai_plus_order,
            MAX(CASE WHEN boat_number = 2 THEN ai_3ren_pct END) AS b2_ai_3ren_pct,
            MAX(CASE WHEN boat_number = 3 THEN ai_3ren_pct END) AS b3_ai_3ren_pct,
            MAX(CASE WHEN boat_number = 4 THEN ai_3ren_pct END) AS b4_ai_3ren_pct,
            MAX(CASE WHEN boat_number = 5 THEN ai_3ren_pct END) AS b5_ai_3ren_pct,
            MAX(CASE WHEN boat_number = 6 THEN ai_3ren_pct END) AS b6_ai_3ren_pct,
            MAX(CASE WHEN boat_number = 2 THEN general_3ren_pct END) AS b2_general_3ren_pct,
            MAX(CASE WHEN boat_number = 3 THEN general_3ren_pct END) AS b3_general_3ren_pct,
            MAX(CASE WHEN boat_number = 4 THEN general_3ren_pct END) AS b4_general_3ren_pct,
            MAX(CASE WHEN boat_number = 5 THEN general_3ren_pct END) AS b5_general_3ren_pct,
            MAX(CASE WHEN boat_number = 6 THEN general_3ren_pct END) AS b6_general_3ren_pct,
            MAX(CASE WHEN boat_number = 2 THEN odds_prediction_pct END) AS b2_odds_prediction_pct,
            MAX(CASE WHEN boat_number = 3 THEN odds_prediction_pct END) AS b3_odds_prediction_pct,
            MAX(CASE WHEN boat_number = 4 THEN odds_prediction_pct END) AS b4_odds_prediction_pct,
            MAX(CASE WHEN boat_number = 5 THEN odds_prediction_pct END) AS b5_odds_prediction_pct,
            MAX(CASE WHEN boat_number = 6 THEN odds_prediction_pct END) AS b6_odds_prediction_pct,
            MAX(CASE WHEN boat_number = 2 THEN odds_rank END) AS b2_odds_rank,
            MAX(CASE WHEN boat_number = 3 THEN odds_rank END) AS b3_odds_rank,
            MAX(CASE WHEN boat_number = 4 THEN odds_rank END) AS b4_odds_rank,
            MAX(CASE WHEN boat_number = 5 THEN odds_rank END) AS b5_odds_rank,
            MAX(CASE WHEN boat_number = 6 THEN odds_rank END) AS b6_odds_rank,
            MAX(CASE WHEN boat_number = 2 THEN boaters_avgdiff END) AS b2_avg_isshu_diff,
            MAX(CASE WHEN boat_number = 3 THEN boaters_avgdiff END) AS b3_avg_isshu_diff,
            MAX(CASE WHEN boat_number = 4 THEN boaters_avgdiff END) AS b4_avg_isshu_diff,
            MAX(CASE WHEN boat_number = 5 THEN boaters_avgdiff END) AS b5_avg_isshu_diff,
            MAX(CASE WHEN boat_number = 6 THEN boaters_avgdiff END) AS b6_avg_isshu_diff,
            MAX(CASE WHEN boat_number = 2 THEN isshu_avg_diff END) AS b2_isshu_avg_diff,
            MAX(CASE WHEN boat_number = 3 THEN isshu_avg_diff END) AS b3_isshu_avg_diff,
            MAX(CASE WHEN boat_number = 4 THEN isshu_avg_diff END) AS b4_isshu_avg_diff,
            MAX(CASE WHEN boat_number = 5 THEN isshu_avg_diff END) AS b5_isshu_avg_diff,
            MAX(CASE WHEN boat_number = 6 THEN isshu_avg_diff END) AS b6_isshu_avg_diff,
            AVG(isshu_time) AS avg_isshu_time,
            AVG(exhibit_combo_time) AS avg_exhibit_combo_time,
            MAX(CASE WHEN boat_number = 2 THEN tenji_time END) AS b2_tenji_time,
            MAX(CASE WHEN boat_number = 3 THEN tenji_time END) AS b3_tenji_time,
            MAX(CASE WHEN boat_number = 4 THEN tenji_time END) AS b4_tenji_time,
            MAX(CASE WHEN boat_number = 5 THEN tenji_time END) AS b5_tenji_time,
            MAX(CASE WHEN boat_number = 6 THEN tenji_time END) AS b6_tenji_time,
            MAX(CASE WHEN boat_number = 2 THEN st_rank_general END) AS b2_st_rank_general,
            MAX(CASE WHEN boat_number = 3 THEN st_rank_general END) AS b3_st_rank_general,
            MAX(CASE WHEN boat_number = 4 THEN st_rank_general END) AS b4_st_rank_general,
            MAX(CASE WHEN boat_number = 5 THEN st_rank_general END) AS b5_st_rank_general,
            MAX(CASE WHEN boat_number = 6 THEN st_rank_general END) AS b6_st_rank_general,
            MAX(CASE WHEN boat_number = 2 THEN st_time_avg_general END) AS b2_st_time_avg_general,
            MAX(CASE WHEN boat_number = 3 THEN st_time_avg_general END) AS b3_st_time_avg_general,
            MAX(CASE WHEN boat_number = 4 THEN st_time_avg_general END) AS b4_st_time_avg_general,
            MAX(CASE WHEN boat_number = 5 THEN st_time_avg_general END) AS b5_st_time_avg_general,
            MAX(CASE WHEN boat_number = 6 THEN st_time_avg_general END) AS b6_st_time_avg_general,
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
            MAX(CASE WHEN ai_plus_order = 6 THEN boaters_avgdiff END) AS ai_rank6_avg_isshu_diff,
            MAX(CASE WHEN ai_plus_order = 6 THEN ai_prediction_pct END) AS ai_rank6_ai_prediction_pct,
            MAX(CASE WHEN ai_plus_order = 6 THEN tenji_time_rank END) AS ai_rank6_tenji_time_rank,
            MAX(CASE WHEN ai_plus_order = 6 THEN tenji_rank END) AS ai_rank6_tenji_rank,
            MAX(CASE WHEN ai_plus_order = 6 THEN isshu_rank END) AS ai_rank6_isshu_rank,
            MAX(CASE WHEN ai_plus_order = 5 THEN boat_number END) AS ai_rank5_boat,
            MAX(CASE WHEN ai_plus_order = 5 THEN boaters_avgdiff END) AS ai_rank5_avg_isshu_diff,
            MAX(CASE WHEN ai_plus_order = 5 THEN ai_prediction_pct END) AS ai_rank5_ai_prediction_pct,
            MAX(CASE WHEN ai_plus_order = 5 THEN tenji_time_rank END) AS ai_rank5_tenji_time_rank,
            MAX(CASE WHEN ai_plus_order = 5 THEN tenji_rank END) AS ai_rank5_tenji_rank,
            MAX(CASE WHEN ai_plus_order = 5 THEN isshu_rank END) AS ai_rank5_isshu_rank,

            MIN(CASE WHEN boat_number IN (5, 6) THEN tenji_time END) AS outer56_best_tenji_time,
            MIN(CASE WHEN boat_number IN (5, 6) THEN isshu_time END) AS outer56_best_isshu_time,
            MAX(CASE WHEN boat_number IN (5, 6) THEN boaters_avgdiff END) AS outer56_best_avg_isshu_diff,
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
    add_matchup_features(df, matchup_profiles or {})
    for boat in range(2, 7):
        left = boat - 1
        df[f"b{boat}_super_slit_tenji_adv"] = df[f"b{left}_tenji_time"] - df[f"b{boat}_tenji_time"]
        df[f"b{boat}_super_slit_st_rank_adv"] = df[f"b{left}_st_rank_general"] - df[f"b{boat}_st_rank_general"]
        df[f"b{boat}_super_slit_alert"] = (
            df[f"b{boat}_super_slit_tenji_adv"].ge(SUPER_SLIT_TENJI_ADV)
            & df[f"b{boat}_super_slit_st_rank_adv"].gt(0)
        ).astype(int)
    df["super_slit_alert_count"] = sum(df[f"b{boat}_super_slit_alert"] for boat in range(2, 7))
    df["mid234_super_slit_count"] = sum(df[f"b{boat}_super_slit_alert"] for boat in (2, 3, 4))
    df["outer456_super_slit_count"] = sum(df[f"b{boat}_super_slit_alert"] for boat in (4, 5, 6))
    df["outer56_super_slit_count"] = sum(df[f"b{boat}_super_slit_alert"] for boat in (5, 6))
    df["outer56_tenji_advantage"] = df["b1_tenji_time"] - df["outer56_best_tenji_time"]
    df["outer56_isshu_advantage"] = df["b1_isshu_time"] - df["outer56_best_isshu_time"]
    add_slit_formation_features(df)
    add_low_outer_value_features(df)
    add_next_factor_candidate_features(df)
    return df


def add_next_factor_candidate_features(df):
    for col in [
        "b1_odds_prediction_pct",
        "b1_odds_rank",
        "b1_avg_isshu_diff",
        "b1_isshu_avg_diff",
        "b1_nige_pct",
        "b1_loss_pct",
        "outer56_best_avg_isshu_diff",
        "outer56_best_ai_prediction_pct",
        "outer56_tenji_top2_count",
        "outer56_isshu_top2_count",
        "outer456_pressure",
        "outer56_pressure_vs_1",
        "wind_speed",
        "wave_height",
    ]:
        if col not in df.columns:
            df[col] = np.nan

    df["b1_popular40"] = df["b1_odds_rank"].eq(1) & df["b1_odds_prediction_pct"].ge(40)
    df["b1_popular45"] = df["b1_odds_rank"].eq(1) & df["b1_odds_prediction_pct"].ge(45)
    df["center_attack_wall_outer"] = (
        df["b2_wall_break_3peek"].eq(1)
        & (df["b3_peek_vs_12"].eq(1) | df["b4_cadou_peek"].eq(1))
        & df["outer56_best_avg_isshu_diff"].ge(0.10)
    ).fillna(False).astype(int)
    df["weather_pressure"] = (
        ((df["wind_speed"].fillna(0).ge(5)) | (df["wave_height"].fillna(0).ge(5)))
        & df["b1_loss_pct"].ge(40)
        & (df["outer456_pressure"].eq(1) | df["outer56_pressure_vs_1"].eq(1))
    ).fillna(False).astype(int)
    df["outer_isshu_priority_b1weak"] = (
        df["outer56_isshu_top2_count"].ge(1)
        & df["outer56_tenji_top2_count"].fillna(0).eq(0)
        & df[["b5_isshu_avg_diff", "b6_isshu_avg_diff"]].max(axis=1).ge(0.10)
        & df["b1_avg_isshu_diff"].le(0.10)
    ).fillna(False).astype(int)
    df["b1_full_tobashi_shape"] = (
        df["b1_popular40"]
        & df["b1_avg_isshu_diff"].le(-0.05)
        & df["b1_isshu_avg_diff"].le(-0.10)
        & (df["outer456_pressure"].eq(1) | df["outer56_pressure_vs_1"].eq(1))
    ).fillna(False).astype(int)

    longshot_boats = []
    for _, row in df.iterrows():
        boats = []
        for boat in range(3, 7):
            odds_rank = row.get(f"b{boat}_odds_rank")
            odds_pct = row.get(f"b{boat}_odds_prediction_pct")
            ai_pred = row.get(f"b{boat}_ai_prediction_pct")
            avgdiff = row.get(f"b{boat}_avg_isshu_diff")
            tenji_rank = row.get(f"b{boat}_tenji_rank")
            isshu_rank = row.get(f"b{boat}_isshu_rank")
            st_rank = row.get(f"b{boat}_st_rank_general")
            value_gap = (
                (pd.notna(odds_rank) and odds_rank >= 4)
                or (pd.notna(odds_pct) and odds_pct <= 12)
            )
            data_up = (
                pd.notna(ai_pred)
                and ai_pred >= 8
                and pd.notna(avgdiff)
                and avgdiff >= 0.05
                and (
                    (pd.notna(tenji_rank) and tenji_rank <= 2)
                    or (pd.notna(isshu_rank) and isshu_rank <= 2)
                    or (pd.notna(st_rank) and st_rank <= 2)
                )
            )
            if value_gap and data_up:
                boats.append(str(boat))
        longshot_boats.append(",".join(boats))
    df["longshot_head_boats"] = longshot_boats
    df["longshot_head_candidate_count"] = df["longshot_head_boats"].map(lambda text: len([part for part in str(text).split(",") if part]))
    df["longshot_head_with_b1_gap"] = (
        df["b1_popular40"]
        & df["b1_avg_isshu_diff"].le(0.10)
        & df["longshot_head_candidate_count"].ge(1)
    ).fillna(False).astype(int)


def add_low_outer_value_features(df):
    """Pick the AI+ 5/6位の5・6号艇 when it has revival-value signals."""
    for col in [
        "ai_rank6_boat",
        "ai_rank6_avg_isshu_diff",
        "ai_rank6_ai_prediction_pct",
        "ai_rank6_tenji_rank",
        "ai_rank6_tenji_time_rank",
        "ai_rank6_isshu_rank",
        "ai_rank5_boat",
        "ai_rank5_avg_isshu_diff",
        "ai_rank5_ai_prediction_pct",
        "ai_rank5_tenji_rank",
        "ai_rank5_tenji_time_rank",
        "ai_rank5_isshu_rank",
    ]:
        if col not in df.columns:
            df[col] = np.nan

    rank6_outer = df["ai_rank6_boat"].isin([5, 6])
    rank5_outer = ~rank6_outer & df["ai_rank5_boat"].isin([5, 6])

    df["low_outer_boat"] = np.where(
        rank6_outer,
        df["ai_rank6_boat"],
        np.where(rank5_outer, df["ai_rank5_boat"], np.nan),
    )
    df["low_outer_ai_plus_rank"] = np.where(rank6_outer, 6, np.where(rank5_outer, 5, np.nan))
    df["low_outer_avg_isshu_diff"] = np.where(
        rank6_outer,
        df["ai_rank6_avg_isshu_diff"],
        np.where(rank5_outer, df["ai_rank5_avg_isshu_diff"], np.nan),
    )
    df["low_outer_ai_prediction_pct"] = np.where(
        rank6_outer,
        df["ai_rank6_ai_prediction_pct"],
        np.where(rank5_outer, df["ai_rank5_ai_prediction_pct"], np.nan),
    )
    rank6_tenji = df["ai_rank6_tenji_rank"].combine_first(df["ai_rank6_tenji_time_rank"])
    rank5_tenji = df["ai_rank5_tenji_rank"].combine_first(df["ai_rank5_tenji_time_rank"])
    df["low_outer_tenji_rank"] = np.where(rank6_outer, rank6_tenji, np.where(rank5_outer, rank5_tenji, np.nan))
    df["low_outer_isshu_rank"] = np.where(
        rank6_outer,
        df["ai_rank6_isshu_rank"],
        np.where(rank5_outer, df["ai_rank5_isshu_rank"], np.nan),
    )
    df["low_outer_exists"] = df["low_outer_boat"].isin([5, 6]).astype(int)
    df["low_outer_exhibit_top2"] = (
        df["low_outer_exists"].eq(1)
        & (
            pd.Series(df["low_outer_tenji_rank"]).le(2)
            | pd.Series(df["low_outer_isshu_rank"]).le(2)
        )
    ).fillna(False).astype(int)


def add_matchup_features(df, matchup_profiles):
    defaults = {
        "matchup_lane1_pressure_score": 0.0,
        "matchup_outer_good_count": 0,
        "matchup_lane1_bad_flag": 0,
        "matchup_notes": "",
    }
    for key, value in defaults.items():
        df[key] = value
    for boat in range(1, 7):
        df[f"b{boat}_matchup_label"] = "相性データ薄"
        df[f"b{boat}_matchup_meetings"] = np.nan
        df[f"b{boat}_matchup_win_rate"] = np.nan
        df[f"b{boat}_matchup_top3_rate"] = np.nan
        df[f"b{boat}_matchup_ahead_rate"] = np.nan
        df[f"b{boat}_matchup_lane1_ahead_rate"] = np.nan
        df[f"b{boat}_matchup_head_bonus"] = 0.0
        df[f"b{boat}_matchup_axis_bonus"] = 0.0
        df[f"b{boat}_matchup_toss_bonus"] = 0.0
    if not matchup_profiles or df.empty:
        return
    for idx, row in df.iterrows():
        race = row.to_dict()
        for boat in range(1, 7):
            race[f"lane{boat}_registration_no"] = race.get(f"b{boat}_registration_no")
        metrics_by_lane, summary = build_matchup_context(race, matchup_profiles)
        for key, value in summary.items():
            df.at[idx, key] = value
        for boat, metrics in metrics_by_lane.items():
            df.at[idx, f"b{boat}_matchup_label"] = metrics.get("matchup_label")
            df.at[idx, f"b{boat}_matchup_meetings"] = metrics.get("matchup_total_meetings")
            df.at[idx, f"b{boat}_matchup_win_rate"] = metrics.get("matchup_win_rate")
            df.at[idx, f"b{boat}_matchup_top3_rate"] = metrics.get("matchup_top3_rate")
            df.at[idx, f"b{boat}_matchup_ahead_rate"] = metrics.get("matchup_ahead_rate")
            df.at[idx, f"b{boat}_matchup_lane1_ahead_rate"] = metrics.get("matchup_lane1_ahead_rate")
            df.at[idx, f"b{boat}_matchup_head_bonus"] = metrics.get("matchup_head_bonus") or 0.0
            df.at[idx, f"b{boat}_matchup_axis_bonus"] = metrics.get("matchup_axis_bonus") or 0.0
            df.at[idx, f"b{boat}_matchup_toss_bonus"] = metrics.get("matchup_toss_bonus") or 0.0


def add_slit_formation_features(df):
    st_cols = [f"b{boat}_st_time_avg_general" for boat in range(1, 7)]
    rank_cols = [f"b{boat}_st_rank_general" for boat in range(1, 7)]
    for col in st_cols + rank_cols:
        if col not in df.columns:
            df[col] = np.nan
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["slit_range"] = df[st_cols].max(axis=1) - df[st_cols].min(axis=1)
    df["b1_slit_gap_vs_23"] = df["b1_st_time_avg_general"] - df[["b2_st_time_avg_general", "b3_st_time_avg_general"]].min(axis=1)
    df["b1_slit_gap_vs_all"] = df["b1_st_time_avg_general"] - df[[f"b{boat}_st_time_avg_general" for boat in range(2, 7)]].min(axis=1)
    df["b2_slit_gap_vs_3"] = df["b2_st_time_avg_general"] - df["b3_st_time_avg_general"]
    df["b3_slit_adv_vs_12"] = df[["b1_st_time_avg_general", "b2_st_time_avg_general"]].min(axis=1) - df["b3_st_time_avg_general"]
    df["b4_slit_adv_vs_123"] = df[["b1_st_time_avg_general", "b2_st_time_avg_general", "b3_st_time_avg_general"]].min(axis=1) - df["b4_st_time_avg_general"]
    df["outer456_slit_adv_vs_123"] = df[["b1_st_time_avg_general", "b2_st_time_avg_general", "b3_st_time_avg_general"]].min(axis=1) - df[["b4_st_time_avg_general", "b5_st_time_avg_general", "b6_st_time_avg_general"]].min(axis=1)
    df["outer56_slit_adv_vs_1"] = df["b1_st_time_avg_general"] - df[["b5_st_time_avg_general", "b6_st_time_avg_general"]].min(axis=1)
    df["b5_slit_adv_vs_4"] = df["b4_st_time_avg_general"] - df["b5_st_time_avg_general"]
    df["b6_slit_adv_vs_5"] = df["b5_st_time_avg_general"] - df["b6_st_time_avg_general"]
    df["center34_dent_gap"] = df[["b3_st_time_avg_general", "b4_st_time_avg_general"]].mean(axis=1) - df[[
        "b1_st_time_avg_general",
        "b2_st_time_avg_general",
        "b5_st_time_avg_general",
        "b6_st_time_avg_general",
    ]].min(axis=1)

    rank_min_23 = df[["b2_st_rank_general", "b3_st_rank_general"]].min(axis=1)
    df["b1_front_wall"] = (
        (
            df["b1_st_time_avg_general"].le(df[["b2_st_time_avg_general", "b3_st_time_avg_general"]].min(axis=1) + 0.005)
            & df["b2_st_rank_general"].le(3)
            & df["b2_slit_gap_vs_3"].le(0.015)
        )
        | (df["b1_st_rank_general"].le(2) & df["b2_st_rank_general"].le(3) & df["b3_st_rank_general"].ge(3))
    ).fillna(False).astype(int)
    df["b1_hole_vs_23"] = (
        df["b1_slit_gap_vs_23"].ge(0.020)
        | (df["b1_st_rank_general"].ge(4) & rank_min_23.le(2))
    ).fillna(False).astype(int)
    df["b1_hole_vs_all"] = (
        df["b1_slit_gap_vs_all"].ge(0.020)
        | (df["b1_st_rank_general"].ge(4) & df[[f"b{boat}_st_rank_general" for boat in range(2, 7)]].min(axis=1).le(2))
    ).fillna(False).astype(int)
    df["b2_wall_break_3peek"] = (
        (df["b2_slit_gap_vs_3"].ge(0.015) & df["b3_slit_adv_vs_12"].ge(0.010))
        | (df["b3_st_rank_general"].le(2) & (df["b2_st_rank_general"] - df["b3_st_rank_general"]).ge(1))
    ).fillna(False).astype(int)
    df["b3_peek_vs_12"] = (
        df["b3_slit_adv_vs_12"].ge(0.015)
        | (df["b3_st_rank_general"].le(2) & df["b3_st_rank_general"].lt(df[["b1_st_rank_general", "b2_st_rank_general"]].min(axis=1)))
    ).fillna(False).astype(int)
    df["b4_cadou_peek"] = (
        df["b4_slit_adv_vs_123"].ge(0.015)
        | (df["b4_st_rank_general"].le(2) & df["b4_st_rank_general"].lt(df[["b1_st_rank_general", "b2_st_rank_general", "b3_st_rank_general"]].min(axis=1)))
    ).fillna(False).astype(int)
    df["outer456_pressure"] = (
        df["outer456_slit_adv_vs_123"].ge(0.020)
        | df[["b4_st_rank_general", "b5_st_rank_general", "b6_st_rank_general"]].min(axis=1).lt(df[["b1_st_rank_general", "b2_st_rank_general", "b3_st_rank_general"]].min(axis=1))
    ).fillna(False).astype(int)
    df["outer56_pressure_vs_1"] = (
        df["outer56_slit_adv_vs_1"].ge(0.020)
        | df[["b5_st_rank_general", "b6_st_rank_general"]].min(axis=1).lt(df["b1_st_rank_general"])
    ).fillna(False).astype(int)
    df["b5_left_adv"] = (
        df["b5_slit_adv_vs_4"].ge(0.015)
        | df["b5_st_rank_general"].lt(df["b4_st_rank_general"])
    ).fillna(False).astype(int)
    df["b6_left_adv"] = (
        df["b6_slit_adv_vs_5"].ge(0.015)
        | df["b6_st_rank_general"].lt(df["b5_st_rank_general"])
    ).fillna(False).astype(int)
    df["center34_dent"] = df["center34_dent_gap"].ge(0.020).fillna(False).astype(int)
    df["slit_dekoboko"] = df["slit_range"].ge(0.040).fillna(False).astype(int)
    labels = []
    for _, row in df.iterrows():
        if row.get("b1_front_wall"):
            labels.append("1前+2壁")
        elif row.get("b2_wall_break_3peek"):
            labels.append("2壁割れ3覗き")
        elif row.get("b1_hole_vs_23") and row.get("outer456_pressure"):
            labels.append("1凹み+外圧")
        elif row.get("b1_hole_vs_23"):
            labels.append("1凹み")
        elif row.get("b4_cadou_peek"):
            labels.append("4カド覗き")
        elif row.get("b3_peek_vs_12"):
            labels.append("3覗き")
        elif row.get("outer456_pressure"):
            labels.append("外圧")
        elif row.get("center34_dent"):
            labels.append("3/4中凹み")
        elif row.get("slit_dekoboko"):
            labels.append("デコボコ")
        else:
            labels.append("")
    df["slit_shape_label"] = labels


def mask_lt(series, value):
    return (series.notna() & (series < value)).to_numpy()


def mask_le(series, value):
    return (series.notna() & (series <= value)).to_numpy()


def mask_ge(series, value):
    return (series.notna() & (series >= value)).to_numpy()


def atom_masks(df, top6_venues, top10_venues):
    n = len(df)
    summer = df["date"].astype(str).str.slice(5, 7).isin([f"{month:02d}" for month in SUMMER_MONTHS]).to_numpy()
    masks = {
        "all": np.ones(n, dtype=bool),
        "summer": summer,
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
        "b1_avgdiff_le030": mask_le(df["b1_avg_isshu_diff"], 0.30),
        "b1_avgdiff_le010": mask_le(df["b1_avg_isshu_diff"], 0.10),
        "b1_avgdiff_le0": mask_le(df["b1_avg_isshu_diff"], 0.00),
        "b1_avgdiff_le_m005": mask_le(df["b1_avg_isshu_diff"], -0.05),
        "outer56_tenji_top2": mask_ge(df["outer56_tenji_top2_count"], 1),
        "outer56_isshu_top2": mask_ge(df["outer56_isshu_top2_count"], 1),
        "outer56_exhibit_top2": mask_ge(df["outer56_exhibit_top2_count"], 1),
        "outer56_exhibit_top2_two": mask_ge(df["outer56_exhibit_top2_count"], 2),
        "outer46_exhibit_top2": mask_ge(df["outer46_exhibit_top2_count"], 1),
        "outer56_low_aiplus_exhibit_top2": mask_ge(df["outer56_low_aiplus_exhibit_top2_count"], 1),
        "outer56_low_aipred_exhibit_top2": mask_ge(df["outer56_low_aipred_exhibit_top2_count"], 1),
        "outer46_low_aiplus_exhibit_top2": mask_ge(df["outer46_low_aiplus_exhibit_top2_count"], 1),
        "summer_b1_isshu_fast010": summer & mask_ge(df["b1_isshu_avg_diff"], SUMMER_B1_FAST_DIFF),
        "summer_b1_isshu_slow_m010": summer & mask_le(df["b1_isshu_avg_diff"], SUMMER_B1_SLOW_DIFF),
        "super_slit_alert": mask_ge(df["super_slit_alert_count"], 1),
        "super_slit_alert_ge2": mask_ge(df["super_slit_alert_count"], 2),
        "mid234_super_slit": mask_ge(df["mid234_super_slit_count"], 1),
        "outer456_super_slit": mask_ge(df["outer456_super_slit_count"], 1),
        "outer56_super_slit": mask_ge(df["outer56_super_slit_count"], 1),
        "slit_b1_front_wall": mask_ge(df["b1_front_wall"], 1),
        "slit_b1_hole_vs_23": mask_ge(df["b1_hole_vs_23"], 1),
        "slit_b1_hole_vs_all": mask_ge(df["b1_hole_vs_all"], 1),
        "slit_b2_wall_break_3peek": mask_ge(df["b2_wall_break_3peek"], 1),
        "slit_b3_peek_vs_12": mask_ge(df["b3_peek_vs_12"], 1),
        "slit_b4_cadou_peek": mask_ge(df["b4_cadou_peek"], 1),
        "slit_outer456_pressure": mask_ge(df["outer456_pressure"], 1),
        "slit_outer56_pressure_vs_1": mask_ge(df["outer56_pressure_vs_1"], 1),
        "slit_b5_left_adv": mask_ge(df["b5_left_adv"], 1),
        "slit_b6_left_adv": mask_ge(df["b6_left_adv"], 1),
        "slit_center34_dent": mask_ge(df["center34_dent"], 1),
        "slit_dekoboko": mask_ge(df["slit_dekoboko"], 1),
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
    for value in [0.10, 0.14, 0.20]:
        masks[f"outer56_avgdiff_ge{int(value * 100):03d}"] = mask_ge(df["outer56_best_avg_isshu_diff"], value)
        masks[f"rank6_avgdiff_ge{int(value * 100):03d}"] = mask_ge(df["ai_rank6_avg_isshu_diff"], value)
    masks["outer56_both_not_bad"] = mask_ge(df["b5_avg_isshu_diff"], 0) & mask_ge(df["b6_avg_isshu_diff"], 0)
    masks["rank5_avgdiff_ge010"] = mask_ge(df["ai_rank5_avg_isshu_diff"], 0.10)
    for value in [10, 12, 15]:
        masks[f"outer56_ai_pred_ge{value}"] = mask_ge(df["outer56_best_ai_prediction_pct"], value)
    for value in [100, 110, 120]:
        masks[f"outer56_aiplus_ge{value}"] = mask_ge(df["outer56_best_ai_plus"], value)
    return masks


def num(value):
    if value is None or pd.isna(value):
        return None
    return float(value)


def int_num(value, default=0):
    value = num(value)
    return default if value is None else int(value)


def json_safe(value):
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        value = float(value)
    if isinstance(value, float):
        return None if pd.isna(value) or not np.isfinite(value) else value
    try:
        if value is not None and not isinstance(value, str) and pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def text_has_any(value, keywords):
    text = str(value or "")
    return any(keyword in text for keyword in keywords)


def is_lady_series(race):
    race_kind = str(race.get("race_kind") or "")
    text = " ".join(str(race.get(key) or "") for key in ("title", "series_title", "race_grade"))
    return race_kind == "Lady" or "オールレディース" in text or "レディース" in text


def is_joshi_race(race):
    race_kind = str(race.get("race_kind") or "")
    text = " ".join(str(race.get(key) or "") for key in ("title", "series_title", "race_grade"))
    return race_kind in JOSHI_RACE_KINDS or text_has_any(text, JOSHI_KEYWORDS)


def joshi_atom_flags(race):
    rank6_boat = int_num(race.get("ai_rank6_boat"))
    rank6_tenji = num(race.get("ai_rank6_tenji_rank"))
    if rank6_tenji is None:
        rank6_tenji = num(race.get("ai_rank6_tenji_time_rank"))
    rank6_isshu = num(race.get("ai_rank6_isshu_rank"))
    flags = {
        "b1_avgdiff_le010": (num(race.get("b1_avg_isshu_diff")) is not None and num(race.get("b1_avg_isshu_diff")) <= 0.10),
        "b1_avgdiff_le030": (num(race.get("b1_avg_isshu_diff")) is not None and num(race.get("b1_avg_isshu_diff")) <= 0.30),
        "b1_ai_pred_lt35": (num(race.get("b1_ai_prediction_pct")) is not None and num(race.get("b1_ai_prediction_pct")) < 35),
        "b1_aiplus_ge4": (num(race.get("b1_ai_plus_order")) is not None and num(race.get("b1_ai_plus_order")) >= 4),
        "b1_aiplus_ge5": (num(race.get("b1_ai_plus_order")) is not None and num(race.get("b1_ai_plus_order")) >= 5),
        "b1_st_rank_ge4": (num(race.get("b1_st_rank_general")) is not None and num(race.get("b1_st_rank_general")) >= 4),
        "outer56_avgdiff_ge014": (
            num(race.get("outer56_best_avg_isshu_diff")) is not None
            and num(race.get("outer56_best_avg_isshu_diff")) >= 0.14
        ),
        "outer56_ai_pred_ge10": (
            num(race.get("outer56_best_ai_prediction_pct")) is not None
            and num(race.get("outer56_best_ai_prediction_pct")) >= 10
        ),
        "outer56_aiplus_ge100": (
            num(race.get("outer56_best_ai_plus")) is not None
            and num(race.get("outer56_best_ai_plus")) >= 100
        ),
        "outer46_exhibit_top2": int_num(race.get("outer46_exhibit_top2_count")) >= 1,
        "rank6_boat_3to6": rank6_boat in {3, 4, 5, 6},
        "rank6_avgdiff_ge010": (
            num(race.get("ai_rank6_avg_isshu_diff")) is not None
            and num(race.get("ai_rank6_avg_isshu_diff")) >= 0.10
        ),
        "rank6_exhibit_top2": (
            (rank6_tenji is not None and rank6_tenji <= 2)
            or (rank6_isshu is not None and rank6_isshu <= 2)
        ),
        "slit_dekoboko": int_num(race.get("slit_dekoboko")) == 1,
        "slit_outer56_pressure_vs_1": int_num(race.get("outer56_pressure_vs_1")) == 1,
        "slit_outer456_pressure": int_num(race.get("outer456_pressure")) == 1,
        "round_early": int_num(race.get("round_no")) <= 6,
        "lady_series": is_lady_series(race),
    }
    return flags


def best_joshi_strategy_factor(race):
    if not is_joshi_race(race):
        return None, {}
    flags = joshi_atom_flags(race)
    matches = [
        factor
        for factor in JOSHI_STRATEGY_FACTORS
        if all(flags.get(atom_id, False) for atom_id in factor["atom_ids"])
    ]
    if not matches:
        return None, flags
    best = max(
        matches,
        key=lambda factor: (
            factor["bonus_pct"],
            factor["manshu_rate_pct"],
            factor["recent_manshu_rate_pct"],
            factor["races"],
        ),
    )
    return best, flags


def is_summer_date(value):
    if value is None or pd.isna(value):
        return False
    text = str(value)
    try:
        month = int(text[5:7])
    except (TypeError, ValueError):
        return False
    return month in SUMMER_MONTHS


def summer_b1_isshu_factor(date_value, b1_avg_diff, isshu_boats=None):
    if isshu_boats is not None:
        try:
            if int(isshu_boats or 0) < 6:
                return {"signal": "", "nige_delta_pp": 0, "score_bonus": 0}
        except (TypeError, ValueError):
            return {"signal": "", "nige_delta_pp": 0, "score_bonus": 0}
    if not is_summer_date(date_value) or b1_avg_diff is None:
        return {"signal": "", "nige_delta_pp": 0, "score_bonus": 0}
    if b1_avg_diff >= SUMMER_B1_FAST_DIFF:
        return {"signal": "fast_hold", "nige_delta_pp": SUMMER_B1_FAST_NIGE_DELTA_PP, "score_bonus": 12}
    if b1_avg_diff <= SUMMER_B1_SLOW_DIFF:
        return {"signal": "slow_fly", "nige_delta_pp": SUMMER_B1_SLOW_NIGE_DELTA_PP, "score_bonus": -14}
    return {"signal": "", "nige_delta_pp": 0, "score_bonus": 0}


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
    b1_isshu_avg = num(race.get("b1_isshu_avg_diff"))
    b1_tenji_rank = tenji_rank_use(race, 1)
    b1_loss = num(race.get("b1_loss_pct"))
    b1_nige = num(race.get("b1_nige_pct"))
    b1_ai_pred = num(race.get("b1_ai_prediction_pct"))
    b1_ai_order = num(race.get("b1_ai_plus_order"))
    b1_odds_pct = num(race.get("b1_odds_prediction_pct"))
    b1_odds_rank = int_num(race.get("b1_odds_rank"), default=9)
    has_trifecta_top5 = int_num(race.get("trifecta_top5_count"), default=0) == 5
    b1_trifecta_top5_1head = int_num(race.get("b1_trifecta_top5_1head"), default=0) == 1
    b1_odds_popular45 = b1_odds_pct is not None and b1_odds_rank == 1 and b1_odds_pct >= 45
    b1_odds_popular40 = b1_odds_pct is not None and b1_odds_rank == 1 and b1_odds_pct >= 40
    b1_popular = b1_trifecta_top5_1head or (not has_trifecta_top5 and b1_odds_popular45)
    b1_popular40 = b1_trifecta_top5_1head or (not has_trifecta_top5 and b1_odds_popular40)
    b1_unpopular = b1_odds_pct is not None and (b1_odds_rank != 1 or b1_odds_pct < 45)
    outer_ai_pred = num(race.get("outer56_best_ai_prediction_pct"))
    outer_ai_plus = num(race.get("outer56_best_ai_plus"))
    outer_avg = num(race.get("outer56_best_avg_isshu_diff"))
    outer_exhibit_top2 = num(race.get("outer56_exhibit_top2_count")) or 0
    outer_tenji_top2 = int_num(race.get("outer56_tenji_top2_count"))
    outer_isshu_top2 = int_num(race.get("outer56_isshu_top2_count")) or 0
    round_no = int_num(race.get("round_no"))
    wind_wave = (num(race.get("wind_speed")) or 0) >= 5 or (num(race.get("wave_height")) or 0) >= 5
    rank6_boat = int_num(race.get("ai_rank6_boat"))
    rank6_avg = num(race.get("ai_rank6_avg_isshu_diff"))
    rank6_ai_pred = num(race.get("ai_rank6_ai_prediction_pct"))
    rank6_tenji = num(race.get("ai_rank6_tenji_rank"))
    if rank6_tenji is None:
        rank6_tenji = num(race.get("ai_rank6_tenji_time_rank"))
    rank6_isshu = num(race.get("ai_rank6_isshu_rank"))
    rank6_exhibit_top2 = (
        (rank6_tenji is not None and rank6_tenji <= 2)
        or (rank6_isshu is not None and rank6_isshu <= 2)
    )
    rank5_tenji = num(race.get("ai_rank5_tenji_rank"))
    if rank5_tenji is None:
        rank5_tenji = num(race.get("ai_rank5_tenji_time_rank"))
    rank5_isshu = num(race.get("ai_rank5_isshu_rank"))
    rank5_exhibit_top2 = (
        (rank5_tenji is not None and rank5_tenji <= 2)
        or (rank5_isshu is not None and rank5_isshu <= 2)
    )
    low_outer_boat = int_num(race.get("low_outer_boat"))
    low_outer_avg = num(race.get("low_outer_avg_isshu_diff"))
    low_outer_tenji = num(race.get("low_outer_tenji_rank"))
    low_outer_isshu = num(race.get("low_outer_isshu_rank"))
    low_outer_ai_pred = num(race.get("low_outer_ai_prediction_pct"))
    low_outer_exhibit_top2 = int_num(race.get("low_outer_exhibit_top2"))
    slit_outer_pressure = int_num(race.get("outer56_pressure_vs_1")) == 1
    center_attack_wall_outer = int_num(race.get("center_attack_wall_outer")) == 1
    weather_pressure = int_num(race.get("weather_pressure")) == 1
    outer_isshu_priority_b1weak = int_num(race.get("outer_isshu_priority_b1weak")) == 1
    b1_full_tobashi_shape = int_num(race.get("b1_full_tobashi_shape")) == 1
    longshot_head_count = int_num(race.get("longshot_head_candidate_count"))
    longshot_head_boats = [int(part) for part in str(race.get("longshot_head_boats") or "").split(",") if part.isdigit()]
    double_time_boats = [boat for boat in range(1, 7) if int_num(race.get(f"b{boat}_double_time")) == 1]
    super_slit_boats = [boat for boat in range(2, 7) if int_num(race.get(f"b{boat}_super_slit_alert")) == 1]
    outer36_double_time = any(boat in {3, 4, 5, 6} for boat in double_time_boats)
    ai_pred_values = {boat: num(race.get(f"b{boat}_ai_prediction_pct")) for boat in range(1, 7)}
    ai_pred_available = [value for value in ai_pred_values.values() if value is not None]
    ai_pred_max = max(ai_pred_available) if ai_pred_available else None
    outer36_ai_pred_top1 = any(
        ai_pred_max is not None and ai_pred_values.get(boat) is not None and ai_pred_values[boat] == ai_pred_max
        for boat in (3, 4, 5, 6)
    )
    outer36_ai_plus_top1 = any(num(race.get(f"b{boat}_ai_plus_order")) == 1 for boat in (3, 4, 5, 6))
    slit_signal_ids = [
        "b1_front_wall",
        "b1_hole_vs_23",
        "b2_wall_break_3peek",
        "b3_peek_vs_12",
        "b4_cadou_peek",
        "outer456_pressure",
        "outer56_pressure_vs_1",
        "b5_left_adv",
        "b6_left_adv",
        "center34_dent",
    ]

    for signal_id in slit_signal_ids:
        if int_num(race.get(signal_id)) != 1:
            continue
        stats = SLIT_FORMATION_STATS[signal_id]
        details = {
            "slit_shape": race.get("slit_shape_label"),
            "signal": signal_id,
            "candidate_b1_win_pct": stats["candidate_b1_win_pct"],
            "candidate_b1_fly_pct": stats["candidate_b1_fly_pct"],
            "candidate_winner_3to6_pct": stats["candidate_winner_3to6_pct"],
            "candidate_outer56_top3_pct": stats["candidate_outer56_top3_pct"],
            "b1_gap_vs_23": num(race.get("b1_slit_gap_vs_23")),
            "b3_adv_vs_12": num(race.get("b3_slit_adv_vs_12")),
            "b4_adv_vs_123": num(race.get("b4_slit_adv_vs_123")),
            "outer56_adv_vs_1": num(race.get("outer56_slit_adv_vs_1")),
        }
        add_edge(
            signals,
            f"codex_slit_formation_{signal_id}",
            stats["label"],
            stats["candidate_manshu_rate_pct"],
            stats["bonus_pct"],
            stats["role"],
            details,
        )

    joshi_factor, joshi_flags = best_joshi_strategy_factor(race)
    if joshi_factor:
        add_edge(
            signals,
            f"codex_{joshi_factor['id']}",
            joshi_factor["label"],
            joshi_factor["manshu_rate_pct"],
            joshi_factor["bonus_pct"],
            "joshi_manshu_up",
            {
                "races": joshi_factor["races"],
                "recent_manshu_rate_pct": joshi_factor["recent_manshu_rate_pct"],
                "b1_win_pct": joshi_factor["b1_win_pct"],
                "b1_fly_pct": joshi_factor["b1_fly_pct"],
                "winner_3to6_pct": joshi_factor["winner_3to6_pct"],
                "outer56_top3_pct": joshi_factor["outer56_top3_pct"],
                "atom_ids": joshi_factor["atom_ids"],
                "matched_flags": {key: bool(value) for key, value in joshi_flags.items() if value},
            },
        )

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

    summer_factor = summer_b1_isshu_factor(race.get("date"), b1_isshu_avg, race.get("isshu_boats"))
    if summer_factor["signal"] == "fast_hold":
        add_edge(
            signals,
            "codex_summer_b1_isshu_fast010_hold",
            "夏場: 1号艇1周が6艇平均より0.10秒速いのでイン逃げ率+15pt",
            None,
            -2.2,
            "b1_hold_down",
            {
                "boat": 1,
                "isshu_avg_diff": b1_isshu_avg,
                "threshold": SUMMER_B1_FAST_DIFF,
                "nige_delta_pp": SUMMER_B1_FAST_NIGE_DELTA_PP,
                "season": "summer_6_8",
            },
        )
    elif summer_factor["signal"] == "slow_fly":
        add_edge(
            signals,
            "codex_summer_b1_isshu_slow_m010_fly",
            "夏場: 1号艇1周が6艇平均より0.10秒遅いのでイン逃げ率-17pt",
            None,
            2.8,
            "b1_fly_up",
            {
                "boat": 1,
                "isshu_avg_diff": b1_isshu_avg,
                "threshold": SUMMER_B1_SLOW_DIFF,
                "nige_delta_pp": SUMMER_B1_SLOW_NIGE_DELTA_PP,
                "season": "summer_6_8",
            },
        )

    popular_details = {
        "b1_trifecta_top5_1head": int(b1_trifecta_top5_1head),
        "trifecta_top5_head1_count": int_num(race.get("trifecta_top5_head1_count")),
        "trifecta_top5_combos": race.get("trifecta_top5_combos"),
        "b1_odds_prediction_pct": b1_odds_pct,
        "b1_odds_rank": b1_odds_rank,
        "popular_source": "trifecta_top5" if b1_trifecta_top5_1head else "boaters_odds_prediction",
    }
    if (
        b1_popular
        and b1_nige is not None
        and b1_nige < 50
        and b1_avg is not None
        and b1_avg <= 0.15
        and outer_exhibit_top2 >= 1
        and round_no is not None
        and round_no <= 6
    ):
        add_edge(
            signals,
            "codex_popular_b1_top5_fly_nige50_avg015_outertop2_early",
            "人気1号艇飛び再構築: 1が売れ過ぎでも逃げ率50%未満+平均との差0.15以下、5/6展示上位、1〜6R",
            28.57,
            3.8,
            "popular_b1_fly_up",
            {
                **popular_details,
                "sample_races": 21,
                "b1_not_win_rate_pct": 71.43,
                "b1_top3_miss_pct": 28.57,
                "manshu_rate_pct": 28.57,
                "b1_nige_pct": b1_nige,
                "b1_avg_isshu_diff": b1_avg,
                "outer56_exhibit_top2_count": outer_exhibit_top2,
            },
        )

    if (
        b1_popular
        and b1_avg is not None
        and b1_avg <= 0.30
        and outer_ai_pred is not None
        and outer_ai_pred >= 10
        and round_no is not None
        and round_no <= 6
    ):
        add_edge(
            signals,
            "codex_popular_b1_top5_fly_avg030_outerai10_early",
            "人気1号艇飛び再構築: 1が売れ過ぎでも平均との差0.30以下、5/6にAI1着10%以上、1〜6R",
            30.43,
            4.0,
            "popular_b1_fly_up",
            {
                **popular_details,
                "sample_races": 23,
                "b1_not_win_rate_pct": 69.57,
                "b1_top3_miss_pct": 30.43,
                "manshu_rate_pct": 30.43,
                "b1_avg_isshu_diff": b1_avg,
                "outer56_ai_prediction_pct": outer_ai_pred,
            },
        )

    if (
        b1_popular
        and b1_avg is not None
        and b1_avg <= 0.30
        and b1_tenji_rank is not None
        and b1_tenji_rank >= 4
        and outer_avg is not None
        and outer_avg >= 0.10
        and rank6_exhibit_top2
        and round_no is not None
        and round_no <= 6
    ):
        add_edge(
            signals,
            "codex_popular_b1_top5_fly_b1bad_rank6revive_early",
            "人気1号艇飛び再構築: 1の平均との差0.30以下+展示4位以下、5/6上振れ、AI+6位が展示上位、1〜6R",
            33.33,
            4.4,
            "popular_b1_fly_up",
            {
                **popular_details,
                "sample_races": 21,
                "b1_not_win_rate_pct": 66.67,
                "b1_top3_miss_pct": 42.86,
                "manshu_rate_pct": 33.33,
                "b1_tenji_rank": b1_tenji_rank,
                "outer56_avg_isshu_diff": outer_avg,
                "ai_rank6_tenji_rank": rank6_tenji,
                "ai_rank6_isshu_rank": rank6_isshu,
            },
        )

    if (
        b1_popular
        and b1_nige is not None
        and b1_nige < 50
        and b1_avg is not None
        and b1_avg <= 0.15
        and outer_avg is not None
        and outer_avg >= 0.05
        and outer_exhibit_top2 >= 1
        and round_no is not None
        and round_no <= 6
    ):
        add_edge(
            signals,
            "codex_popular_b1_top5_fly_nige50_outeravg005_early",
            "人気1号艇飛び再構築: 逃げ率50%未満+1の平均との差0.15以下、5/6平均との差0.05以上+展示上位、1〜6R",
            25.00,
            3.2,
            "popular_b1_fly_up",
            {
                **popular_details,
                "sample_races": 20,
                "b1_not_win_rate_pct": 70.00,
                "b1_top3_miss_pct": 30.00,
                "manshu_rate_pct": 25.00,
                "b1_nige_pct": b1_nige,
                "b1_avg_isshu_diff": b1_avg,
                "outer56_avg_isshu_diff": outer_avg,
                "outer56_exhibit_top2_count": outer_exhibit_top2,
            },
        )

    if (
        b1_popular
        and b1_avg is not None
        and b1_avg <= 0.15
        and b1_tenji_rank is not None
        and b1_tenji_rank >= 4
        and outer_avg is not None
        and outer_avg >= 0.05
        and rank5_exhibit_top2
        and round_no is not None
        and round_no <= 6
    ):
        add_edge(
            signals,
            "codex_popular_b1_top5_fly_b1bad_rank5revive_early",
            "人気1号艇飛び再構築: 1の平均との差0.15以下+展示4位以下、5/6上振れ、AI+5位が展示上位、1〜6R",
            35.00,
            4.6,
            "popular_b1_fly_up",
            {
                **popular_details,
                "sample_races": 20,
                "b1_not_win_rate_pct": 65.00,
                "b1_top3_miss_pct": 35.00,
                "manshu_rate_pct": 35.00,
                "b1_tenji_rank": b1_tenji_rank,
                "outer56_avg_isshu_diff": outer_avg,
                "ai_rank5_tenji_rank": rank5_tenji,
                "ai_rank5_isshu_rank": rank5_isshu,
            },
        )

    if (
        b1_popular
        and b1_isshu_avg is not None
        and b1_isshu_avg <= -0.10
        and b1_nige is not None
        and b1_nige < 45
        and outer_avg is not None
        and outer_avg >= 0.14
        and outer_tenji_top2 >= 1
    ):
        add_edge(
            signals,
            "codex_popular_b1_fly_outer56_isshu_slow",
            "人気1号艇飛び: オッズ評価45%以上1位だが、1周-0.10以下+逃げ率45未満+5/6上振れ",
            24.46,
            3.4,
            "popular_b1_fly_up",
            {
                "b1_odds_prediction_pct": b1_odds_pct,
                "b1_odds_rank": b1_odds_rank,
                "b1_fly_rate_pct": 60.87,
                "b1_top3_miss_pct": 21.74,
                "outer56_top3_pct": 63.04,
                "winner_3to6_pct": 42.39,
                "b1_isshu_avg_diff": b1_isshu_avg,
                "b1_nige_pct": b1_nige,
                "outer56_avg_isshu_diff": outer_avg,
                "outer56_tenji_top2_count": outer_tenji_top2,
            },
        )
    elif (
        b1_popular
        and b1_isshu_avg is not None
        and b1_isshu_avg <= -0.10
        and b1_nige is not None
        and b1_nige < 45
        and outer_avg is not None
        and outer_avg >= 0.10
        and outer_tenji_top2 >= 1
    ):
        add_edge(
            signals,
            "codex_popular_b1_fly_outer56_isshu_slow_light",
            "人気1号艇飛び: 1周-0.10以下+逃げ率45未満、5/6に平均との差+0.10以上と展示上位",
            23.35,
            2.7,
            "popular_b1_fly_up",
            {
                "b1_odds_prediction_pct": b1_odds_pct,
                "b1_odds_rank": b1_odds_rank,
                "b1_fly_rate_pct": 58.88,
                "b1_top3_miss_pct": 20.30,
                "b1_isshu_avg_diff": b1_isshu_avg,
                "b1_nige_pct": b1_nige,
                "outer56_avg_isshu_diff": outer_avg,
                "outer56_tenji_top2_count": outer_tenji_top2,
            },
        )

    if (
        b1_popular
        and b1_avg is not None
        and b1_avg <= -0.05
        and b1_tenji_rank is not None
        and b1_tenji_rank >= 4
        and b1_nige is not None
        and b1_nige < 45
        and outer_avg is not None
        and outer_avg >= 0.14
    ):
        add_edge(
            signals,
            "codex_popular_b1_fly_avg_bad_outer56",
            "人気1号艇飛び: オッズ人気ありでも展示+1周平均との差-0.05以下、展示4位以下、5/6平均との差+0.14以上",
            25.17,
            3.2,
            "popular_b1_fly_up",
            {
                "b1_odds_prediction_pct": b1_odds_pct,
                "b1_avg_isshu_diff": b1_avg,
                "b1_tenji_rank": b1_tenji_rank,
                "b1_nige_pct": b1_nige,
                "outer56_avg_isshu_diff": outer_avg,
            },
        )

    if (
        b1_unpopular
        and (
            (b1_nige is not None and b1_nige < 40)
            or (b1_loss is not None and b1_loss >= 45)
            or (b1_ai_pred is not None and b1_ai_pred < 35)
        )
    ):
        add_edge(
            signals,
            "codex_b1_unpopular_fly_low_value",
            "1号艇オッズ評価45%未満または1位外: イン飛びは織り込み済みで妙味を下げる",
            None,
            -1.2,
            "low_value_b1_fly",
            {
                "b1_odds_prediction_pct": b1_odds_pct,
                "b1_odds_rank": b1_odds_rank,
            },
        )

    if (
        low_outer_boat in {5, 6}
        and b1_loss is not None
        and b1_loss >= 40
        and slit_outer_pressure
        and low_outer_exhibit_top2
        and low_outer_ai_pred is not None
        and low_outer_ai_pred >= 8
    ):
        add_edge(
            signals,
            "codex_low_outer_revive_b1loss_slit",
            "低評価外枠復活: AI+5/6位の5/6号艇が展示/1周2位以内、AI予測8%以上、1号艇逃げ失敗40%以上+外圧",
            32.00,
            3.8,
            "low_outer_manshu_up",
            {
                "low_outer_boat": low_outer_boat,
                "low_outer_top3_pct": 55.20,
                "b1_fly_pct": 74.40,
                "b1_top3_miss_pct": 51.20,
                "outer56_top3_pct": 85.60,
                "low_outer_avg_isshu_diff": low_outer_avg,
                "low_outer_ai_prediction_pct": low_outer_ai_pred,
                "low_outer_tenji_rank": low_outer_tenji,
                "low_outer_isshu_rank": low_outer_isshu,
            },
        )
    elif (
        low_outer_boat in {5, 6}
        and low_outer_avg is not None
        and low_outer_avg >= 0.10
        and low_outer_exhibit_top2
        and low_outer_ai_pred is not None
        and low_outer_ai_pred >= 8
    ):
        add_edge(
            signals,
            "codex_low_outer_revive_avg010",
            "低評価外枠復活: AI+5/6位の5/6号艇が平均との差+0.10以上、展示/1周2位以内、AI予測8%以上",
            27.63,
            3.2,
            "low_outer_top3_up",
            {
                "low_outer_boat": low_outer_boat,
                "low_outer_top3_pct": 54.61,
                "outer56_top3_pct": 86.84,
                "low_outer_avg_isshu_diff": low_outer_avg,
                "low_outer_ai_prediction_pct": low_outer_ai_pred,
                "low_outer_tenji_rank": low_outer_tenji,
                "low_outer_isshu_rank": low_outer_isshu,
            },
        )

    if (
        b1_popular
        and low_outer_boat in {5, 6}
        and outer_tenji_top2 >= 1
        and low_outer_avg is not None
        and low_outer_avg >= 0.10
        and low_outer_ai_pred is not None
        and low_outer_ai_pred >= 5
    ):
        add_edge(
            signals,
            "codex_popular_b1_low_outer_value_gap",
            "人気1号艇×低評価外枠復活: 人気イン飛びと消し外枠3着内の配当ギャップ",
            26.62,
            2.5,
            "value_gap_up",
            {
                "b1_odds_prediction_pct": b1_odds_pct,
                "low_outer_boat": low_outer_boat,
                "low_outer_top3_pct": 49.43,
                "b1_fly_pct": 42.21,
                "low_outer_avg_isshu_diff": low_outer_avg,
                "low_outer_ai_prediction_pct": low_outer_ai_pred,
            },
        )

    if (
        outer_ai_pred is not None
        and outer_ai_pred >= 10
        and outer_ai_plus is not None
        and outer_ai_plus >= 100
        and outer_isshu_top2 >= 1
    ):
        add_edge(
            signals,
            "codex_post_ai_exh_outer56_ai10_aiplus100_isshu2",
            "直前上げ: 5/6号艇AI10%以上+AI+100以上+1周2位以内",
            23.31,
            2.8,
            "postdata_manshu_up",
            {
                "races": 858,
                "recent_manshu_rate_pct": 24.34,
                "outer56_ai_prediction_pct": outer_ai_pred,
                "outer56_ai_plus": outer_ai_plus,
                "outer56_isshu_top2_count": outer_isshu_top2,
            },
        )

    if (
        b1_ai_pred is not None
        and b1_ai_pred < 30
        and outer_ai_pred is not None
        and outer_ai_pred >= 10
        and rank6_boat in {5, 6}
        and rank6_exhibit_top2
    ):
        add_edge(
            signals,
            "codex_post_ai_exh_b1aipred30_outer10_rank6exh",
            "直前上げ: 1号艇AI30%未満+5/6AI10%以上+AI+最下位5/6が展示/1周2位以内",
            23.20,
            2.7,
            "postdata_manshu_up",
            {
                "races": 582,
                "recent_manshu_rate_pct": 24.02,
                "b1_ai_prediction_pct": b1_ai_pred,
                "outer56_ai_prediction_pct": outer_ai_pred,
                "ai_rank6_boat": rank6_boat,
                "ai_rank6_tenji_rank": rank6_tenji,
                "ai_rank6_isshu_rank": rank6_isshu,
            },
        )

    if b1_ai_pred is not None and b1_ai_pred < 30 and outer36_ai_plus_top1 and super_slit_boats:
        add_edge(
            signals,
            "codex_post_ai_exh_b1aipred30_outeraiplus1_superslit",
            "直前上げ: 1号艇AI30%未満+3〜6号艇にAI+1位+スーパースリット",
            23.25,
            2.8,
            "postdata_manshu_up",
            {
                "races": 314,
                "recent_manshu_rate_pct": 24.36,
                "b1_ai_prediction_pct": b1_ai_pred,
                "super_slit_boats": super_slit_boats,
            },
        )

    if (
        outer_ai_pred is not None
        and outer_ai_pred >= 12
        and outer_avg is not None
        and outer_avg >= 0.10
        and outer36_double_time
    ):
        add_edge(
            signals,
            "codex_post_ai_exh_outer56_ai12_avg010_outerdouble",
            "直前強上げ: 5/6号艇AI12%以上+平均との差0.10以上+3〜6号艇ダブルタイム",
            26.92,
            4.2,
            "postdata_manshu_strong_up",
            {
                "races": 156,
                "recent_manshu_rate_pct": 30.61,
                "outer56_ai_prediction_pct": outer_ai_pred,
                "outer56_avg_isshu_diff": outer_avg,
                "double_time_boats": double_time_boats,
            },
        )

    if b1_ai_pred is not None and b1_ai_pred < 30 and outer_ai_pred is not None and outer_ai_pred >= 12 and outer36_double_time:
        add_edge(
            signals,
            "codex_post_ai_exh_b1aipred30_outer56_ai12_outerdouble",
            "直前強上げ: 1号艇AI30%未満+5/6号艇AI12%以上+3〜6号艇ダブルタイム",
            27.10,
            4.2,
            "postdata_manshu_strong_up",
            {
                "races": 155,
                "recent_manshu_rate_pct": 29.29,
                "b1_ai_prediction_pct": b1_ai_pred,
                "outer56_ai_prediction_pct": outer_ai_pred,
                "double_time_boats": double_time_boats,
            },
        )

    if (
        outer_ai_pred is not None
        and outer_ai_pred >= 10
        and outer36_ai_pred_top1
        and b1_avg is not None
        and b1_avg <= 0
    ):
        add_edge(
            signals,
            "codex_post_ai_exh_outer56_ai10_outerhead_b1avg0",
            "直前強上げ: 5/6号艇AI10%以上+3〜6号艇にAI予測1位+1号艇平均との差0以下",
            26.67,
            3.9,
            "postdata_manshu_strong_up",
            {
                "races": 195,
                "recent_manshu_rate_pct": 26.23,
                "outer56_ai_prediction_pct": outer_ai_pred,
                "b1_avg_isshu_diff": b1_avg,
            },
        )

    if rank6_boat in {5, 6} and rank6_ai_pred is not None and rank6_ai_pred >= 5 and outer_tenji_top2 >= 1:
        add_edge(
            signals,
            "codex_post_ai_exh_rank6_outer_ai5_outertenji2",
            "直前上げ: AI+最下位5/6号艇がAI予測5%以上+5/6展示2位以内",
            24.31,
            3.0,
            "rank6_outer_revive",
            {
                "races": 362,
                "recent_manshu_rate_pct": 41.51,
                "ai_rank6_boat": rank6_boat,
                "ai_rank6_ai_prediction_pct": rank6_ai_pred,
                "outer56_tenji_top2_count": outer_tenji_top2,
            },
        )

    if rank6_boat in {5, 6} and rank6_ai_pred is not None and rank6_ai_pred >= 5 and rank6_exhibit_top2:
        add_edge(
            signals,
            "codex_post_ai_exh_rank6_outer_ai5_rank6exh",
            "直前強上げ: AI+最下位5/6号艇がAI予測5%以上+本人が展示/1周2位以内",
            26.03,
            3.6,
            "rank6_outer_revive",
            {
                "races": 242,
                "recent_manshu_rate_pct": 40.00,
                "ai_rank6_boat": rank6_boat,
                "ai_rank6_ai_prediction_pct": rank6_ai_pred,
                "ai_rank6_tenji_rank": rank6_tenji,
                "ai_rank6_isshu_rank": rank6_isshu,
            },
        )

    if center_attack_wall_outer:
        add_edge(
            signals,
            "codex_center_attack_wall_outer",
            "3/4攻撃+外圧: 2壁崩壊、3/4攻撃、5/6平均との差+0.10以上",
            19.86,
            1.7,
            "head_up",
            {
                "races": 1183,
                "b1_fly_pct": 56.13,
                "b1_top3_miss_pct": 28.40,
                "winner_3to6_pct": 47.42,
                "outer56_top3_pct": 63.57,
                "outer56_avg_isshu_diff": outer_avg,
            },
        )

    if weather_pressure:
        add_edge(
            signals,
            "codex_weather_pressure_b1_outer",
            "会場風波: 風5m以上または波5cm以上、1号艇逃げ失敗40%以上、外圧あり",
            20.10,
            1.5,
            "weather_up",
            {
                "races": 2682,
                "b1_fly_pct": 69.02,
                "b1_top3_miss_pct": 35.53,
                "winner_3to6_pct": 47.54,
                "outer56_top3_pct": 63.20,
                "wind_speed": num(race.get("wind_speed")),
                "wave_height": num(race.get("wave_height")),
            },
        )

    if longshot_head_count >= 1:
        bonus = 2.2 if int_num(race.get("longshot_head_with_b1_gap")) == 1 else 1.4
        hist_rate = 21.58 if int_num(race.get("longshot_head_with_b1_gap")) == 1 else 20.08
        label = (
            "人気薄頭+1過信: 人気1の弱化と3〜6人気薄頭候補"
            if int_num(race.get("longshot_head_with_b1_gap")) == 1
            else "人気薄頭候補: 3〜6低オッズ評価艇にAI8%以上+展示/一周/ST上振れ"
        )
        add_edge(
            signals,
            "codex_longshot_head_with_b1_gap" if int_num(race.get("longshot_head_with_b1_gap")) == 1 else "codex_longshot_head_candidate",
            label,
            hist_rate,
            bonus,
            "head_up",
            {
                "longshot_head_boats": longshot_head_boats,
                "races": 2150 if int_num(race.get("longshot_head_with_b1_gap")) == 1 else 15660,
                "winner_3to6_pct": 31.44 if int_num(race.get("longshot_head_with_b1_gap")) == 1 else 37.88,
                "outer56_top3_pct": 56.51 if int_num(race.get("longshot_head_with_b1_gap")) == 1 else 60.50,
                "b1_odds_prediction_pct": b1_odds_pct,
                "b1_avg_isshu_diff": b1_avg,
            },
        )

    if outer_isshu_priority_b1weak:
        add_edge(
            signals,
            "codex_outer_isshu_priority_b1weak",
            "外枠一周優先+1弱: 5/6が展示ではなく1周上位、1号艇平均との差0.10以下",
            18.77,
            1.0,
            "outer_top3_up",
            {
                "races": 5334,
                "b1_fly_pct": 54.80,
                "outer56_top3_pct": 57.87,
                "outer56_isshu_top2_count": int_num(race.get("outer56_isshu_top2_count")),
                "outer56_tenji_top2_count": int_num(race.get("outer56_tenji_top2_count")),
            },
        )

    if b1_full_tobashi_shape:
        add_edge(
            signals,
            "codex_b1_full_tobashi_shape",
            "1号艇完全飛ばし型: 人気1、1平均との差-0.05以下、1周-0.10以下、外圧",
            19.46,
            1.4,
            "b1_miss_up",
            {
                "races": 1254,
                "b1_fly_pct": 42.03,
                "b1_top3_miss_pct": 14.83,
                "b1_2nd_or_3rd_pct": 27.19,
                "b1_odds_prediction_pct": b1_odds_pct,
                "b1_avg_isshu_diff": b1_avg,
                "b1_isshu_avg_diff": b1_isshu_avg,
            },
        )

    if (
        is_joshi_race(race)
        and b1_avg is not None
        and b1_avg <= 0.30
        and outer_ai_pred is not None
        and outer_ai_pred >= 10
    ):
        add_edge(
            signals,
            "codex_joshi_outer_ai10_b1avg030",
            "女子戦寄り: 1号艇平均との差+0.30以下、5/6AI予測10%以上",
            19.71,
            1.3,
            "joshi_manshu_up",
            {
                "races": 619,
                "b1_fly_pct": 59.77,
                "outer56_top3_pct": 73.83,
                "b1_avg_isshu_diff": b1_avg,
                "outer56_ai_prediction_pct": outer_ai_pred,
            },
        )

    for boat in super_slit_boats:
        stats = SUPER_SLIT_ALERT_STATS[boat]
        add_edge(
            signals,
            f"codex_super_slit_alert_{boat}",
            f"スーパースリットアラート: {boat}号艇が左隣より展示0.10秒速く平均ST順位も上位",
            stats["manshu_rate_pct"],
            stats["bonus_pct"],
            "super_slit_up",
            {
                "boat": boat,
                "left_boat": boat - 1,
                "tenji_adv": num(race.get(f"b{boat}_super_slit_tenji_adv")),
                "st_rank_adv": num(race.get(f"b{boat}_super_slit_st_rank_adv")),
                "rows": stats["rows"],
                "win_rate_pct": stats["win_rate_pct"],
                "top3_rate_pct": stats["top3_rate_pct"],
                "makuri_win_rate_pct": stats["makuri_win_rate_pct"],
                "win_uplift_pp": stats["win_uplift_pp"],
                "top3_uplift_pp": stats["top3_uplift_pp"],
                "makuri_win_uplift_pp": stats["makuri_win_uplift_pp"],
            },
        )
    if len(super_slit_boats) >= 2:
        add_edge(
            signals,
            "codex_super_slit_alert_multi",
            "スーパースリットアラートが2艇以上: レース万舟率22.20%",
            22.20,
            2.0,
            "super_slit_multi",
            {"boats": super_slit_boats, "base_manshu_rate_pct": 16.74},
        )

    matchup_pressure = num(race.get("matchup_lane1_pressure_score"))
    matchup_outer_good = int_num(race.get("matchup_outer_good_count"))
    matchup_lane1_bad = int_num(race.get("matchup_lane1_bad_flag"))
    matchup_notes = [part for part in str(race.get("matchup_notes") or "").split("|") if part]
    if matchup_outer_good >= 2:
        add_edge(
            signals,
            "codex_matchup_outer_good2",
            "対戦相性: 1号艇に強い相性バフ艇が2艇以上",
            None,
            2.2,
            "matchup_manshu_up",
            {
                "matchup_outer_good_count": matchup_outer_good,
                "matchup_lane1_pressure_score": matchup_pressure,
                "matchup_notes": matchup_notes,
            },
        )
    elif matchup_pressure is not None and matchup_pressure >= 4.0:
        add_edge(
            signals,
            "codex_matchup_lane1_pressure4",
            "対戦相性: 今回の1号艇へ相性圧力あり",
            None,
            1.4,
            "b1_fly_up",
            {
                "matchup_lane1_pressure_score": matchup_pressure,
                "matchup_notes": matchup_notes,
            },
        )
    if matchup_lane1_bad:
        add_edge(
            signals,
            "codex_matchup_lane1_bad",
            "対戦相性: 1号艇が今回メンバー相手に劣勢",
            None,
            1.4,
            "b1_fly_up",
            {
                "matchup_lane1_pressure_score": matchup_pressure,
                "matchup_notes": matchup_notes,
            },
        )
    for boat in range(2, 7):
        label = str(race.get(f"b{boat}_matchup_label") or "")
        if label == "1号艇キラー":
            add_edge(
                signals,
                f"codex_matchup_lane1_killer_{boat}",
                f"対戦相性: {boat}号艇が今回1号艇への先着率高め",
                None,
                1.3,
                "head_up",
                {
                    "boat": boat,
                    "label": label,
                    "meetings": race.get(f"b{boat}_matchup_meetings"),
                    "lane1_ahead_rate": race.get(f"b{boat}_matchup_lane1_ahead_rate"),
                    "head_bonus": race.get(f"b{boat}_matchup_head_bonus"),
                    "axis_bonus": race.get(f"b{boat}_matchup_axis_bonus"),
                },
            )
        elif label == "相性バフ":
            add_edge(
                signals,
                f"codex_matchup_buff_{boat}",
                f"対戦相性: {boat}号艇が今回メンバー相手に上振れ",
                None,
                0.9,
                "head_up",
                {
                    "boat": boat,
                    "label": label,
                    "meetings": race.get(f"b{boat}_matchup_meetings"),
                    "ahead_rate": race.get(f"b{boat}_matchup_ahead_rate"),
                    "top3_rate": race.get(f"b{boat}_matchup_top3_rate"),
                    "head_bonus": race.get(f"b{boat}_matchup_head_bonus"),
                    "axis_bonus": race.get(f"b{boat}_matchup_axis_bonus"),
                },
            )
        elif label == "相性軸バフ":
            add_edge(
                signals,
                f"codex_matchup_axis_buff_{boat}",
                f"対戦相性: {boat}号艇が今回メンバー相手に3着内上振れ",
                None,
                0.7,
                "outer_top3_up",
                {
                    "boat": boat,
                    "label": label,
                    "meetings": race.get(f"b{boat}_matchup_meetings"),
                    "ahead_rate": race.get(f"b{boat}_matchup_ahead_rate"),
                    "top3_rate": race.get(f"b{boat}_matchup_top3_rate"),
                    "axis_bonus": race.get(f"b{boat}_matchup_axis_bonus"),
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

    b1_super_debuff = BOATERS_B1_AVGDIFF_SUPER_DEBUFF.get(race.get("place_name"))
    if b1_super_debuff and b1_avg is not None and b1_avg <= b1_super_debuff["threshold"]:
        add_edge(
            signals,
            f"codex_boaters_avgdiff_b1_super_debuff_{race.get('place_name')}",
            f"1号艇 展示+一周平均との差{b1_super_debuff['threshold']:+.2f}以下: イン飛び超デバフ",
            None,
            2.6,
            "b1_fly_up",
            {
                "boat": 1,
                "avgdiff": b1_avg,
                "threshold": b1_super_debuff["threshold"],
                "win_delta_pp": b1_super_debuff["win_delta_pp"],
                "top3_miss_delta_pp": b1_super_debuff["top3_miss_delta_pp"],
            },
        )
    elif b1_avg is not None and b1_avg <= 0.30:
        add_edge(
            signals,
            "codex_boaters_avgdiff_b1_debuff030",
            "1号艇 展示+一周平均との差+0.30以下: イン飛びデバフ",
            None,
            1.4,
            "b1_fly_up",
            {
                "boat": 1,
                "avgdiff": b1_avg,
                "threshold": 0.30,
                "win_delta_pp": -6.72,
                "top3_miss_delta_pp": 4.48,
            },
        )

    if b1_avg is not None and b1_avg >= 0.65:
        add_edge(
            signals,
            "codex_boaters_avgdiff_b1_super_buff065",
            "1号艇 展示+一周平均との差+0.65以上: イン堅さ超バフ",
            None,
            -2.4,
            "b1_hold_down",
            {
                "boat": 1,
                "avgdiff": b1_avg,
                "threshold": 0.65,
                "win_delta_pp": 10.13,
                "top3_uplift_pp": 6.56,
            },
        )
    elif b1_avg is not None and b1_avg >= 0.30:
        add_edge(
            signals,
            "codex_boaters_avgdiff_b1_buff030",
            "1号艇 展示+一周平均との差+0.30以上: イン堅さバフ",
            None,
            -1.2,
            "b1_hold_down",
            {
                "boat": 1,
                "avgdiff": b1_avg,
                "threshold": 0.30,
                "win_delta_pp": 5.34,
                "top3_uplift_pp": 3.56,
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
            "1号艇 展示+一周平均との差-0.05以下+展示5位以下+逃げ失敗40%以上、5/6 展示+一周平均との差0.14以上",
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
            "1号艇 展示+一周平均との差0以下、5/6 展示+一周平均との差0.10以上+展示2位以内、風波5以上",
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
            "AI+最下位の展示+一周平均との差0.10以上、1号艇AI+4位以下、5/6展示2位以内",
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
            "AI+最下位が5/6号艇、展示+一周平均との差マイナス、展示4位以下",
            15.05,
            -1.2,
            "rank6_keshi",
            {"rank6_boat": rank6_boat},
        )

    if b1_avg is not None and b1_avg <= 0 and b1_tenji_rank is not None and b1_tenji_rank >= 4:
        add_edge(
            signals,
            "codex_b1_fly_avg0_tenji4",
            "1号艇 展示+一周平均との差0以下+展示4位以下",
            19.32,
            1.2,
            "b1_fly_up",
        )

    if b1_avg is not None and b1_avg >= 0.30 and b1_ai_order == 1:
        add_edge(
            signals,
            "codex_b1_strong_avg030_aiplus1",
            "1号艇 展示+一周平均との差+0.30以上+AI+1位",
            15.60,
            -1.6,
            "b1_hold_down",
        )

    place = race.get("place_name")
    for boat in range(1, 7):
        boat_avg = num(race.get(f"b{boat}_avg_isshu_diff"))
        global_edge = BOATERS_AVGDIFF_GLOBAL_BUFFS.get(boat)
        if boat != 1 and global_edge and boat_avg is not None and boat_avg >= global_edge["super_buff_threshold"]:
            bonus = min(1.8, max(0.8, global_edge["top3_uplift_pp"] / 9.0))
            add_edge(
                signals,
                f"codex_boaters_avgdiff_global_{boat}_{global_edge['super_buff_threshold']:.2f}",
                f"{boat}号艇 展示+一周平均との差{global_edge['super_buff_threshold']:+.2f}以上: 3着内超バフ",
                None,
                round(bonus, 2),
                "lane_top3_up",
                {
                    "boat": boat,
                    "avgdiff": boat_avg,
                    "threshold": global_edge["super_buff_threshold"],
                    "top3_uplift_pp": global_edge["top3_uplift_pp"],
                    "win_uplift_pp": global_edge["win_uplift_pp"],
                },
            )

        edge = BOATERS_AVGDIFF_LANE_EDGES.get((place, boat))
        if edge and boat_avg is not None and boat_avg >= edge["threshold"]:
            bonus = min(2.2, max(0.8, edge["top3_uplift_pp"] / 12.0))
            add_edge(
                signals,
                f"codex_boaters_lane_avgdiff_{place}_{boat}_{edge['threshold']:.2f}",
                f"{place}{boat}号艇 展示+一周平均との差{edge['threshold']:+.2f}以上で3着内上振れ",
                None,
                round(bonus, 2),
                "lane_top3_up",
                {
                    "boat": boat,
                    "avgdiff": boat_avg,
                    "threshold": edge["threshold"],
                    "top3_uplift_pp": edge["top3_uplift_pp"],
                    "win_uplift_pp": edge["win_uplift_pp"],
                    "samples": edge["samples"],
                },
            )

    signals.sort(key=lambda item: (item["bonus_pct"], item.get("historical_rate_pct") or 0), reverse=True)
    return signals


def composite_adjustment(signals):
    positive = [signal["bonus_pct"] for signal in signals if signal["bonus_pct"] > 0]
    negative = [signal["bonus_pct"] for signal in signals if signal["bonus_pct"] < 0]
    bonus = min(5.0, sum(positive[:3])) + sum(negative)
    return round(max(-3.0, min(5.0, bonus)), 2)


def all_venue_adjustment(signals):
    positive = [signal["bonus_pct"] for signal in signals if signal["bonus_pct"] > 0]
    negative = [signal["bonus_pct"] for signal in signals if signal["bonus_pct"] < 0]
    bonus = min(13.0, sum(positive[:7])) + sum(negative)
    return round(max(-5.0, min(14.0, bonus)), 2)


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


def race_has_full_exhibition(race):
    return (int_num(race.get("tenji_boats")) or 0) >= 6 and (int_num(race.get("isshu_boats")) or 0) >= 6


def all_venue_edge_signals(race):
    signals = list(composite_edge_signals(race))
    b1_loss = num(race.get("b1_loss_pct"))
    b1_nige = num(race.get("b1_nige_pct"))
    b1_st_rank = num(race.get("b1_st_rank_general"))
    b1_ai_pred = num(race.get("b1_ai_prediction_pct"))
    b1_ai_order = num(race.get("b1_ai_plus_order"))
    outer_ai_pred = num(race.get("outer56_best_ai_prediction_pct"))
    outer_ai_plus = num(race.get("outer56_best_ai_plus"))
    outer_avg = num(race.get("outer56_best_avg_isshu_diff"))
    outer_exhibit_top2 = num(race.get("outer56_exhibit_top2_count")) or 0
    rank6_boat = int_num(race.get("ai_rank6_boat"))
    rank6_avg = num(race.get("ai_rank6_avg_isshu_diff"))
    round_no = int_num(race.get("round_no"))
    wind_wave = (num(race.get("wind_speed")) or 0) >= 5 or (num(race.get("wave_height")) or 0) >= 5

    if b1_loss is not None and b1_loss >= 60:
        add_edge(signals, "codex_allvenue_b1_loss60", "全場: 1号艇差され+まくられ率60%以上", None, 2.8, "b1_fly_up", {"b1_loss_pct": b1_loss})
    elif b1_loss is not None and b1_loss >= 45:
        add_edge(signals, "codex_allvenue_b1_loss45", "全場: 1号艇差され+まくられ率45%以上", None, 1.9, "b1_fly_up", {"b1_loss_pct": b1_loss})

    if b1_nige is not None and b1_nige < 25:
        add_edge(signals, "codex_allvenue_b1_nige25", "全場: 1号艇逃げ率25%未満", None, 2.5, "b1_fly_up", {"b1_nige_pct": b1_nige})
    elif b1_nige is not None and b1_nige < 40:
        add_edge(signals, "codex_allvenue_b1_nige40", "全場: 1号艇逃げ率40%未満", None, 1.6, "b1_fly_up", {"b1_nige_pct": b1_nige})

    if b1_st_rank is not None and b1_st_rank >= 5:
        add_edge(signals, "codex_allvenue_b1_st5", "全場: 1号艇平均ST順位5位以下", None, 1.4, "b1_fly_up", {"b1_st_rank_general": b1_st_rank})
    elif b1_st_rank is not None and b1_st_rank >= 4:
        add_edge(signals, "codex_allvenue_b1_st4", "全場: 1号艇平均ST順位4位以下", None, 0.9, "b1_fly_up", {"b1_st_rank_general": b1_st_rank})

    if b1_ai_pred is not None and b1_ai_pred < 25:
        add_edge(signals, "codex_allvenue_b1_aipred25", "全場: 1号艇AI予測25%未満", None, 1.5, "b1_fly_up", {"b1_ai_prediction_pct": b1_ai_pred})
    elif b1_ai_pred is not None and b1_ai_pred < 35:
        add_edge(signals, "codex_allvenue_b1_aipred35", "全場: 1号艇AI予測35%未満", None, 0.9, "b1_fly_up", {"b1_ai_prediction_pct": b1_ai_pred})

    if b1_ai_order is not None and b1_ai_order >= 5:
        add_edge(signals, "codex_allvenue_b1_aiplus5", "全場: 1号艇AI+順位5位以下", None, 1.5, "b1_fly_up", {"b1_ai_plus_order": b1_ai_order})
    elif b1_ai_order is not None and b1_ai_order >= 4:
        add_edge(signals, "codex_allvenue_b1_aiplus4", "全場: 1号艇AI+順位4位以下", None, 1.0, "b1_fly_up", {"b1_ai_plus_order": b1_ai_order})

    if outer_ai_pred is not None and outer_ai_pred >= 12:
        add_edge(signals, "codex_allvenue_outer56_aipred12", "全場: 5/6号艇AI予測最大12%以上", None, 1.4, "outer_top3_up", {"outer56_ai_prediction_pct": outer_ai_pred})
    elif outer_ai_pred is not None and outer_ai_pred >= 10:
        add_edge(signals, "codex_allvenue_outer56_aipred10", "全場: 5/6号艇AI予測最大10%以上", None, 1.0, "outer_top3_up", {"outer56_ai_prediction_pct": outer_ai_pred})

    if outer_ai_plus is not None and outer_ai_plus >= 110:
        add_edge(signals, "codex_allvenue_outer56_aiplus110", "全場: 5/6号艇AI+最大110以上", None, 1.2, "outer_top3_up", {"outer56_ai_plus": outer_ai_plus})
    elif outer_ai_plus is not None and outer_ai_plus >= 100:
        add_edge(signals, "codex_allvenue_outer56_aiplus100", "全場: 5/6号艇AI+最大100以上", None, 0.8, "outer_top3_up", {"outer56_ai_plus": outer_ai_plus})

    if outer_avg is not None and outer_avg >= 0.14:
        add_edge(signals, "codex_allvenue_outer56_avg014", "全場: 5/6号艇 展示+一周平均との差+0.14以上", None, 1.5, "outer_top3_up", {"outer56_avg_isshu_diff": outer_avg})
    elif outer_avg is not None and outer_avg >= 0.10:
        add_edge(signals, "codex_allvenue_outer56_avg010", "全場: 5/6号艇 展示+一周平均との差+0.10以上", None, 1.0, "outer_top3_up", {"outer56_avg_isshu_diff": outer_avg})

    if outer_exhibit_top2 >= 1:
        add_edge(signals, "codex_allvenue_outer56_exhibit_top2", "全場: 5/6号艇に展示/1周2位以内", None, 1.0, "outer_top3_up", {"outer56_exhibit_top2_count": outer_exhibit_top2})
    if rank6_boat in {3, 4, 5, 6} and rank6_avg is not None and rank6_avg >= 0.10:
        add_edge(signals, "codex_allvenue_rank6_avg010", "全場: AI+最下位が3〜6号艇で平均との差+0.10以上", None, 1.2, "rank6_ana", {"rank6_boat": rank6_boat})
    if wind_wave:
        add_edge(signals, "codex_allvenue_wind_wave", "全場: 風または波5以上", None, 0.8, "weather_up")
    if round_no <= 6 and any(signal["role"] in {"b1_fly_up", "outer_top3_up", "head_up"} for signal in signals):
        add_edge(signals, "codex_allvenue_early", "全場: 前半1〜6Rで荒れ材料あり", None, 0.4, "context_up")

    signals.sort(key=lambda item: (item["bonus_pct"], item.get("historical_rate_pct") or 0), reverse=True)
    return signals


def morning_candidate_signals(race):
    """BOATERS AI・展示・直前オッズが出る前の材料だけで朝の監視リストを作る。"""
    signals = []
    b1_loss = num(race.get("b1_loss_pct"))
    b1_nige = num(race.get("b1_nige_pct"))
    b1_general = num(race.get("b1_general_3ren_pct"))
    b1_st_rank = num(race.get("b1_st_rank_general"))
    round_no = int_num(race.get("round_no"))
    matchup_pressure = num(race.get("matchup_lane1_pressure_score"))
    matchup_outer_good = int_num(race.get("matchup_outer_good_count"))
    matchup_lane1_bad = int_num(race.get("matchup_lane1_bad_flag"))
    has_trifecta_top5 = int_num(race.get("trifecta_top5_count"), default=0) == 5
    b1_top5_heads = int_num(race.get("trifecta_top5_head1_count"), default=0) or 0
    b1_top5_all_head = int_num(race.get("b1_trifecta_top5_1head"), default=0) == 1
    wind_wave = (num(race.get("wind_speed")) or 0) >= 5 or (num(race.get("wave_height")) or 0) >= 5

    if b1_nige is not None and b1_nige < 25:
        add_edge(signals, "morning_b1_nige25", "展示前: 1号艇の逃げ率25%未満", None, 3.2, "b1_fly_up", {"b1_nige_pct": b1_nige})
    elif b1_nige is not None and b1_nige < 40:
        add_edge(signals, "morning_b1_nige40", "展示前: 1号艇の逃げ率40%未満", None, 1.9, "b1_fly_up", {"b1_nige_pct": b1_nige})

    if b1_loss is not None and b1_loss >= 60:
        add_edge(signals, "morning_b1_loss60", "展示前: 1号艇の逃げ失敗60%以上", None, 3.2, "b1_fly_up", {"b1_loss_pct": b1_loss})
    elif b1_loss is not None and b1_loss >= 45:
        add_edge(signals, "morning_b1_loss45", "展示前: 1号艇の逃げ失敗45%以上", None, 2.1, "b1_fly_up", {"b1_loss_pct": b1_loss})

    if b1_general is not None and b1_general < 40:
        add_edge(signals, "morning_b1_general40", "展示前: 1号艇の一般3連対率40%未満", None, 1.7, "b1_fly_up", {"b1_general_3ren_pct": b1_general})
    elif b1_general is not None and b1_general < 50:
        add_edge(signals, "morning_b1_general50", "展示前: 1号艇の一般3連対率50%未満", None, 1.0, "b1_fly_up", {"b1_general_3ren_pct": b1_general})

    if b1_st_rank is not None and b1_st_rank >= 5:
        add_edge(signals, "morning_b1_st5", "展示前: 1号艇の平均ST順位5位以下", None, 1.8, "b1_fly_up", {"b1_st_rank_general": b1_st_rank})
    elif b1_st_rank is not None and b1_st_rank >= 4:
        add_edge(signals, "morning_b1_st4", "展示前: 1号艇の平均ST順位4位以下", None, 1.1, "b1_fly_up", {"b1_st_rank_general": b1_st_rank})

    outer56_general = max([num(race.get(f"b{boat}_general_3ren_pct")) or -1 for boat in (5, 6)])
    outer36_general = max([num(race.get(f"b{boat}_general_3ren_pct")) or -1 for boat in range(3, 7)])
    center34_general = max([num(race.get(f"b{boat}_general_3ren_pct")) or -1 for boat in (3, 4)])
    if outer56_general >= 45:
        add_edge(signals, "morning_outer56_general45", "展示前: 5/6号艇に一般3連対率45%以上", None, 1.5, "outer_top3_up", {"outer56_general_3ren_pct": outer56_general})
    elif outer56_general >= 38:
        add_edge(signals, "morning_outer56_general38", "展示前: 5/6号艇に一般3連対率38%以上", None, 0.8, "outer_top3_up", {"outer56_general_3ren_pct": outer56_general})
    if outer36_general >= 55:
        add_edge(signals, "morning_36_general55", "展示前: 3〜6号艇に一般3連対率55%以上", None, 1.1, "head_up", {"outer36_general_3ren_pct": outer36_general})
    if center34_general >= 52:
        add_edge(signals, "morning_center34_general52", "展示前: 3/4号艇に一般3連対率52%以上", None, 0.9, "head_up", {"center34_general_3ren_pct": center34_general})

    if b1_general is not None and outer36_general >= 45 and outer36_general - b1_general >= 8:
        add_edge(
            signals,
            "morning_outer36_general_gap8",
            "展示前: 3〜6号艇の一般3連対率が1号艇より8pt以上上",
            None,
            1.5,
            "head_up",
            {"b1_general_3ren_pct": b1_general, "outer36_general_3ren_pct": outer36_general},
        )

    st_ranks = {boat: num(race.get(f"b{boat}_st_rank_general")) for boat in range(1, 7)}
    if b1_st_rank is not None:
        center_attack = [
            boat for boat in (3, 4)
            if st_ranks.get(boat) is not None and st_ranks[boat] + 1.0 <= b1_st_rank
        ]
        outer_fast = [
            boat for boat in (5, 6)
            if st_ranks.get(boat) is not None and st_ranks[boat] <= 2
        ]
        if center_attack:
            add_edge(signals, "morning_center_st_attack", "展示前: 3/4号艇の平均ST順位が1号艇より良い", None, 1.5, "head_up", {"center_st_boats": ",".join(map(str, center_attack))})
        if outer_fast:
            add_edge(signals, "morning_outer56_st_top2", "展示前: 5/6号艇に平均ST順位2位以内", None, 1.0, "outer_top3_up", {"outer56_st_boats": ",".join(map(str, outer_fast))})

    if int_num(race.get("b2_wall_break_3peek")) or int_num(race.get("b3_peek_vs_12")) or int_num(race.get("b4_cadou_peek")):
        add_edge(signals, "morning_slit_attack_shape", "展示前: 平均STから中枠が攻めやすい隊形", None, 1.3, "head_up", {"slit_shape": race.get("slit_shape_label")})
    if int_num(race.get("outer456_pressure")) or int_num(race.get("outer56_pressure_vs_1")):
        add_edge(signals, "morning_outer_st_pressure", "展示前: 平均STから外側の圧力あり", None, 1.1, "outer_top3_up", {"slit_shape": race.get("slit_shape_label")})

    b1_bad = any(signal.get("role") == "b1_fly_up" and signal.get("bonus_pct", 0) >= 1.7 for signal in signals)
    outer_or_head = any(signal.get("role") in {"outer_top3_up", "head_up"} for signal in signals)
    if b1_bad and outer_or_head:
        add_edge(signals, "morning_combo_b1bad_outer", "展示前: 1号艇不安＋外/中枠に上位材料", None, 2.2, "combo_manshu_up")

    if has_trifecta_top5:
        if b1_top5_all_head and b1_bad:
            add_edge(signals, "morning_popular_b1_bad", "展示前: 人気1〜5が1号艇頭なのに1号艇が弱い", None, 2.4, "popular_b1_fly_up", {"trifecta_top5_head1_count": b1_top5_heads})
        elif b1_top5_heads >= 3 and b1_bad:
            add_edge(signals, "morning_popular_b1_some_bad", "展示前: 1号艇頭が売れているのに1号艇に不安", None, 1.6, "popular_b1_fly_up", {"trifecta_top5_head1_count": b1_top5_heads})
        elif b1_top5_heads <= 1 and b1_bad and not outer_or_head:
            add_edge(signals, "morning_b1_unpopular_bad_discount", "展示前: 1号艇不安は既にオッズに出ている", None, -0.8, "value_down", {"trifecta_top5_head1_count": b1_top5_heads})

    if matchup_outer_good >= 2:
        add_edge(signals, "morning_matchup_outer_good2", "展示前: 1号艇に強い相性バフ艇が2艇以上", None, 1.8, "matchup_manshu_up", {"matchup_outer_good_count": matchup_outer_good})
    elif matchup_pressure is not None and matchup_pressure >= 4.0:
        add_edge(signals, "morning_matchup_pressure4", "展示前: 今回メンバーが1号艇へ相性圧力", None, 1.2, "matchup_manshu_up", {"matchup_lane1_pressure_score": matchup_pressure})
    if matchup_lane1_bad:
        add_edge(signals, "morning_matchup_lane1_bad", "展示前: 1号艇が今回メンバー相手に劣勢", None, 1.2, "b1_fly_up", {"matchup_lane1_bad_flag": 1})

    if wind_wave:
        add_edge(signals, "morning_wind_wave", "展示前: 風または波5以上", None, 0.8, "weather_up")
    if round_no <= 3:
        add_edge(signals, "morning_early_race", "展示前: 1〜3Rは荒れやすさを少し加点", None, 0.6, "context_up")
    elif 9 <= round_no <= 12 and race.get("place_name") != "宮島":
        add_edge(signals, "morning_late_race", "展示前: 後半9〜12R・宮島以外を少し加点", None, 0.6, "context_up")

    signals.sort(key=lambda item: (item["bonus_pct"], item.get("historical_rate_pct") or 0), reverse=True)
    return signals


def morning_candidate_adjustment(signals):
    return round(max(-3.0, min(20.0, morning_scored_total(signals))), 2)


def material_signals(signals):
    return [
        signal
        for signal in signals
        if signal.get("bonus_pct", 0) > 0 and signal.get("role") != "context_up"
    ]


def material_signal_score(signals):
    return round(sum(signal.get("bonus_pct", 0) for signal in material_signals(signals)), 2)


def morning_scored_total(signals):
    materials = material_signals(signals)
    material_score = sum(signal.get("bonus_pct", 0) for signal in materials)
    context_score = min(
        1.2,
        sum(signal.get("bonus_pct", 0) for signal in signals if signal.get("role") == "context_up" and signal.get("bonus_pct", 0) > 0),
    )
    negative_score = sum(signal.get("bonus_pct", 0) for signal in signals if signal.get("bonus_pct", 0) < 0)
    roles = {signal.get("role") for signal in materials}
    diversity_bonus = min(2.0, max(0, len(roles) - 1) * 0.55)
    combo_bonus = 0.0
    if "b1_fly_up" in roles and ({"head_up", "outer_top3_up"} & roles):
        combo_bonus += 1.2
    if "popular_b1_fly_up" in roles:
        combo_bonus += 1.0
    if "matchup_manshu_up" in roles and ({"head_up", "outer_top3_up", "b1_fly_up"} & roles):
        combo_bonus += 0.8
    if "weather_up" in roles and ({"head_up", "outer_top3_up"} & roles):
        combo_bonus += 0.5
    return round(max(0.0, min(20.0, material_score + context_score + negative_score + diversity_bonus + combo_bonus)), 2)


def has_morning_core_shape(signals):
    materials = material_signals(signals)
    roles = {signal.get("role") for signal in materials}
    ids = {signal.get("id") for signal in materials}
    if len(materials) >= 2 and {"b1_fly_up", "outer_top3_up", "head_up", "popular_b1_fly_up"} & roles:
        return True
    if {"matchup_manshu_up", "weather_up"} & roles and len(materials) >= 3:
        return True
    if {
        "morning_b1_loss45",
        "morning_b1_loss60",
        "morning_b1_nige40",
        "morning_b1_nige25",
        "morning_combo_b1bad_outer",
        "morning_popular_b1_bad",
    } & ids:
        return True
    return False


def morning_candidate_type(race, signals):
    round_no = int_num(race.get("round_no"))
    score = morning_scored_total(signals)
    if score >= 10.0:
        return "重点監視"
    if score >= 6.0:
        return "監視"
    if score >= 3.0 or (has_morning_core_shape(signals) and 9 <= round_no <= 12 and race.get("place_name") != "宮島"):
        return "後半軽監視"
    return "軽監視"


def build_morning_candidates(df, top_n):
    rows = []
    for _, race in df.iterrows():
        signals = morning_candidate_signals(race)
        score = morning_scored_total(signals)
        material_score = material_signal_score(signals)
        material_count = len(material_signals(signals))
        candidate_type = morning_candidate_type(race, signals)
        if score <= 0:
            continue

        reasons = [signal["label"] for signal in signals if signal["bonus_pct"] > 0][:5]
        row = row_summary(
            race,
            [],
            status="展示前ランキング",
            edge_signals=signals,
            base_rate_override=16.82,
            adjustment_func=morning_candidate_adjustment,
            condition_override=f"{candidate_type}: 展示前データ得点式。一般成績・平均ST・逃げ傾向・スリット近似・風波・相性・展示前オッズを合算。",
            ranking_type="morning_watchlist",
        )
        row["candidate_type"] = candidate_type
        row["candidate_phase"] = "morning_watchlist"
        row["candidate_source_scope"] = "pre_boaters_ai_pre_exhibition_pre_live_odds"
        row["candidate_score"] = score
        row["candidate_material_count"] = material_count
        row["candidate_material_score"] = material_score
        row["pre_exhibition_manshu_score"] = score
        row["pre_exhibition_logic"] = "展示前データだけを点数化。1号艇不安と外/中枠浮上が重なるほど万舟率を上げる。"
        row["candidate_reasons"] = reasons
        row["finalize_rule"] = "締切10〜15分前にBOATERS AI・展示・実オッズを取得して、買い/見送りへ更新"
        rows.append(row)

    rows.sort(
        key=lambda row: (
            row.get("candidate_score") or 0,
            row.get("best_manshu_rate_pct") or 0,
            row.get("matched_logic_count") or 0,
        ),
        reverse=True,
    )
    return rows[:top_n]


def rankable_final_row(row, threshold):
    if row.get("matched_logic_count", 0) > 0:
        return True
    edge_signals = row.get("composite_edges") or []
    materials = material_signals(edge_signals)
    material_score = material_signal_score(edge_signals)
    edge_base = row.get("composite_edge_base_rate_pct")
    edge_bonus = row.get("composite_edge_bonus_pct") or 0
    rate = row.get("best_manshu_rate_pct") or 0
    if edge_base is not None and material_score >= 3.0 and rate >= threshold:
        return True
    if len(materials) >= 2 and material_score >= 5.0 and edge_bonus >= 4.0:
        return True
    return False


def take_diverse_rows(rows, top_n, max_per_place=2):
    picked = []
    counts = {}
    for row in rows:
        place = row.get("place_name")
        if counts.get(place, 0) >= max_per_place:
            continue
        picked.append(row)
        counts[place] = counts.get(place, 0) + 1
        if len(picked) >= top_n:
            return picked
    for row in rows:
        if row in picked:
            continue
        picked.append(row)
        if len(picked) >= top_n:
            break
    return picked


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

    unified_rows = []
    actual_rows = []
    watch_rows = []
    for _, race in df.iterrows():
        actual = actual_by_race.get(race["race_id"], [])
        watch = watch_by_race.get(race["race_id"], [])
        edge_signals = composite_edge_signals(race)
        all_factor_signals = all_venue_edge_signals(race)
        unified_matches = actual if actual else watch
        unified_status = "厳選統合・展示待ち" if watch and not actual and not race_has_full_exhibition(race) else "厳選統合"
        unified_rows.append(
            row_summary(
                race,
                unified_matches,
                status=unified_status,
                edge_signals=all_factor_signals,
                base_rate_override=16.82,
                adjustment_func=all_venue_adjustment,
                condition_override="Codex厳選ランキング: 過去27%以上条件、1号艇弱化、外枠上振れ、AI/オッズ、展示+1周、スリット隊形、女子戦、天候、対戦相性を全統合",
                ranking_type="strict",
            )
        )
        if actual:
            actual_rows.append(row_summary(race, actual, status="確定", edge_signals=edge_signals, ranking_type="strict"))
        elif edge_signals:
            edge_row = row_summary(race, [], status="複合補正", edge_signals=edge_signals, ranking_type="strict")
            has_positive_edge = any(signal["bonus_pct"] > 0 for signal in edge_signals)
            if has_positive_edge and edge_row["best_manshu_rate_pct"] >= threshold:
                actual_rows.append(edge_row)
        if watch:
            watch_rows.append(row_summary(race, watch, status="展示待ち", edge_signals=[], ranking_type="strict"))

    key = lambda row: (
        row["best_manshu_rate_pct"],
        row["best_recent_rate_pct"] if row["best_recent_rate_pct"] is not None else -1,
        row["matched_logic_count"],
    )
    unified_rows.sort(key=key, reverse=True)
    actual_rows.sort(key=key, reverse=True)
    watch_rows.sort(key=key, reverse=True)
    return unified_rows, actual_rows, watch_rows, sorted(unknown_atoms)


def row_summary(
    race,
    matches,
    status,
    edge_signals=None,
    base_rate_override=None,
    adjustment_func=composite_adjustment,
    condition_override=None,
    ranking_type=None,
):
    edge_signals = edge_signals or []
    summer_factor = summer_b1_isshu_factor(
        race.get("date"),
        num(race.get("b1_isshu_avg_diff")),
        race.get("isshu_boats"),
    )
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
    base_rate = max([rate for rate in [logic_rate, edge_base, base_rate_override] if rate is not None], default=0.0)
    edge_bonus = adjustment_func(edge_signals)
    adjusted_rate = max(0.0, min(40.0, base_rate + edge_bonus))
    if condition_override:
        condition = condition_override
    elif best:
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
        "race_kind": race.get("race_kind"),
        "series_title": race.get("series_title"),
        "is_joshi": int(is_joshi_race(race)),
        "ranking_type": ranking_type,
        "best_manshu_rate_pct": round(adjusted_rate, 2),
        "base_manshu_rate_pct": None if logic_rate is None and base_rate_override is None else round(float(logic_rate if logic_rate is not None else base_rate_override), 2),
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
        "b1_odds_prediction_pct": race.get("b1_odds_prediction_pct"),
        "b1_odds_rank": race.get("b1_odds_rank"),
        "b1_trifecta_top5_1head": int_num(race.get("b1_trifecta_top5_1head")),
        "trifecta_top5_head1_count": int_num(race.get("trifecta_top5_head1_count")),
        "trifecta_top5_count": int_num(race.get("trifecta_top5_count")),
        "trifecta_top1_odds": race.get("trifecta_top1_odds"),
        "trifecta_top5_avg_odds": race.get("trifecta_top5_avg_odds"),
        "trifecta_top5_combos": race.get("trifecta_top5_combos"),
        "trifecta_odds_snapshot_at": race.get("trifecta_odds_snapshot_at"),
        "b1_ai_plus": race.get("b1_ai_plus"),
        "b1_ai_plus_order": race.get("b1_ai_plus_order"),
        "b1_nige_pct": race.get("b1_nige_pct"),
        "b1_loss_pct": race.get("b1_loss_pct"),
        "b1_avg_isshu_diff": race.get("b1_avg_isshu_diff"),
        "b1_isshu_avg_diff": race.get("b1_isshu_avg_diff"),
        "avg_isshu_time": race.get("avg_isshu_time"),
        "avg_exhibit_combo_time": race.get("avg_exhibit_combo_time"),
        "is_summer": int(is_summer_date(race.get("date"))),
        "b1_summer_isshu_factor": summer_factor["signal"],
        "b1_summer_nige_delta_pp": summer_factor["nige_delta_pp"],
        "b1_tenji_time": race.get("b1_tenji_time"),
        "b1_tenji_rank": race.get("b1_tenji_rank"),
        "b1_tenji_time_rank": race.get("b1_tenji_time_rank"),
        "b1_isshu_time": race.get("b1_isshu_time"),
        "b1_isshu_rank": race.get("b1_isshu_rank"),
        "outer56_best_avg_isshu_diff": race.get("outer56_best_avg_isshu_diff"),
        "outer56_best_ai_prediction_pct": race.get("outer56_best_ai_prediction_pct"),
        "outer56_best_ai_plus": race.get("outer56_best_ai_plus"),
        "outer56_best_tenji_time": race.get("outer56_best_tenji_time"),
        "outer56_best_isshu_time": race.get("outer56_best_isshu_time"),
        "outer56_tenji_top2_count": int_num(race.get("outer56_tenji_top2_count")),
        "outer56_isshu_top2_count": int_num(race.get("outer56_isshu_top2_count")),
        "outer56_exhibit_top2_count": int_num(race.get("outer56_exhibit_top2_count")),
        "ai_rank6_boat": race.get("ai_rank6_boat"),
        "ai_rank6_avg_isshu_diff": race.get("ai_rank6_avg_isshu_diff"),
        "ai_rank6_ai_prediction_pct": race.get("ai_rank6_ai_prediction_pct"),
        "ai_rank6_tenji_rank": race.get("ai_rank6_tenji_rank"),
        "ai_rank6_isshu_rank": race.get("ai_rank6_isshu_rank"),
        "ai_rank5_boat": race.get("ai_rank5_boat"),
        "ai_rank5_avg_isshu_diff": race.get("ai_rank5_avg_isshu_diff"),
        "ai_rank5_ai_prediction_pct": race.get("ai_rank5_ai_prediction_pct"),
        "ai_rank5_tenji_rank": race.get("ai_rank5_tenji_rank"),
        "ai_rank5_isshu_rank": race.get("ai_rank5_isshu_rank"),
        "low_outer_boat": race.get("low_outer_boat"),
        "low_outer_ai_plus_rank": race.get("low_outer_ai_plus_rank"),
        "low_outer_avg_isshu_diff": race.get("low_outer_avg_isshu_diff"),
        "low_outer_ai_prediction_pct": race.get("low_outer_ai_prediction_pct"),
        "low_outer_tenji_rank": race.get("low_outer_tenji_rank"),
        "low_outer_isshu_rank": race.get("low_outer_isshu_rank"),
        "low_outer_exhibit_top2": int_num(race.get("low_outer_exhibit_top2")),
        "center_attack_wall_outer": int_num(race.get("center_attack_wall_outer")),
        "weather_pressure": int_num(race.get("weather_pressure")),
        "outer_isshu_priority_b1weak": int_num(race.get("outer_isshu_priority_b1weak")),
        "b1_full_tobashi_shape": int_num(race.get("b1_full_tobashi_shape")),
        "longshot_head_boats": race.get("longshot_head_boats"),
        "longshot_head_candidate_count": int_num(race.get("longshot_head_candidate_count")),
        "longshot_head_with_b1_gap": int_num(race.get("longshot_head_with_b1_gap")),
        "double_time_boats": ",".join(str(boat) for boat in range(1, 7) if int_num(race.get(f"b{boat}_double_time")) == 1),
        "super_slit_boats": ",".join(str(boat) for boat in range(2, 7) if int_num(race.get(f"b{boat}_super_slit_alert")) == 1),
        "super_slit_alert_count": int_num(race.get("super_slit_alert_count")),
        "mid234_super_slit_count": int_num(race.get("mid234_super_slit_count")),
        "outer456_super_slit_count": int_num(race.get("outer456_super_slit_count")),
        "outer56_super_slit_count": int_num(race.get("outer56_super_slit_count")),
        "slit_shape_label": race.get("slit_shape_label"),
        "b1_slit_gap_vs_23": race.get("b1_slit_gap_vs_23"),
        "b3_slit_adv_vs_12": race.get("b3_slit_adv_vs_12"),
        "b4_slit_adv_vs_123": race.get("b4_slit_adv_vs_123"),
        "outer56_slit_adv_vs_1": race.get("outer56_slit_adv_vs_1"),
        "outer456_slit_adv_vs_123": race.get("outer456_slit_adv_vs_123"),
        "slit_dekoboko": int_num(race.get("slit_dekoboko")),
        "slit_b1_front_wall": int_num(race.get("b1_front_wall")),
        "slit_b1_hole_vs_23": int_num(race.get("b1_hole_vs_23")),
        "slit_b2_wall_break_3peek": int_num(race.get("b2_wall_break_3peek")),
        "slit_b3_peek_vs_12": int_num(race.get("b3_peek_vs_12")),
        "slit_b4_cadou_peek": int_num(race.get("b4_cadou_peek")),
        "slit_outer456_pressure": int_num(race.get("outer456_pressure")),
        "slit_outer56_pressure_vs_1": int_num(race.get("outer56_pressure_vs_1")),
        "matchup_lane1_pressure_score": race.get("matchup_lane1_pressure_score"),
        "matchup_outer_good_count": int_num(race.get("matchup_outer_good_count")),
        "matchup_lane1_bad_flag": int_num(race.get("matchup_lane1_bad_flag")),
        "matchup_notes": race.get("matchup_notes"),
        "matchup_buff_boats": ",".join(
            str(boat)
            for boat in range(1, 7)
            if str(race.get(f"b{boat}_matchup_label") or "") in {"1号艇キラー", "相性バフ", "相性軸バフ"}
        ),
        **{
            f"b{boat}_matchup_label": race.get(f"b{boat}_matchup_label")
            for boat in range(1, 7)
        },
        **{
            f"b{boat}_ai_prediction_pct": race.get(f"b{boat}_ai_prediction_pct")
            for boat in range(1, 7)
        },
        **{
            f"b{boat}_ai_3ren_pct": race.get(f"b{boat}_ai_3ren_pct")
            for boat in range(1, 7)
        },
        **{
            f"b{boat}_general_3ren_pct": race.get(f"b{boat}_general_3ren_pct")
            for boat in range(1, 7)
        },
        **{
            f"b{boat}_st_rank_general": race.get(f"b{boat}_st_rank_general")
            for boat in range(1, 7)
        },
        **{
            f"b{boat}_st_time_avg_general": race.get(f"b{boat}_st_time_avg_general")
            for boat in range(1, 7)
        },
        **{
            f"b{boat}_ai_plus": race.get(f"b{boat}_ai_plus")
            for boat in range(1, 7)
        },
        **{
            f"b{boat}_ai_plus_order": race.get(f"b{boat}_ai_plus_order")
            for boat in range(1, 7)
        },
        "boat1_double_time": int_num(race.get("b1_double_time")),
        "mid234_double_time_count": int_num(race.get("mid234_double_time_count")),
        "outer46_double_time_count": int_num(race.get("outer46_double_time_count")),
        "outer56_double_time_count": int_num(race.get("outer56_double_time_count")),
        "wind_speed": race.get("wind_speed"),
        "wave_height": race.get("wave_height"),
        "tenji_boats": int_num(race.get("tenji_boats")),
        "isshu_boats": int_num(race.get("isshu_boats")),
    }


def make_report(path, date_text, all_venue_rows, strict_rows, watch_rows, top_n):
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
  <div class="meta">過去検証27%以上条件、全場補正、展示/1周、AI/オッズ、スリット隊形、女子戦、天候、対戦相性をすべて混ぜたCodex統合厳選ランキング。</div>
  <h2>厳選ランキング TOP{top_n}（全ファクター統合）</h2>
  <table>
    <thead><tr><th>#</th><th>状態</th><th>場</th><th>R</th><th>締切</th><th>補正後</th><th>元率</th><th>補正pt</th><th>直近率</th><th>一致数</th><th>代表条件</th><th>1号艇AI</th><th>1展示</th><th>5/6最速展示</th></tr></thead>
    <tbody>{table(all_venue_rows)}</tbody>
  </table>
  <h2>厳選ランキング TOP{top_n}（互換表示）</h2>
  <table>
    <thead><tr><th>#</th><th>状態</th><th>場</th><th>R</th><th>締切</th><th>補正後</th><th>元率</th><th>補正pt</th><th>直近率</th><th>一致数</th><th>代表条件</th><th>1号艇AI</th><th>1展示</th><th>5/6最速展示</th></tr></thead>
    <tbody>{table(strict_rows)}</tbody>
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
    default_odds_db = default_trifecta_odds_db()
    parser.add_argument("--date", required=True)
    parser.add_argument("--today-db", required=True)
    parser.add_argument("--logic-csv", default=str(DEFAULT_LOGIC_CSV))
    parser.add_argument("--history-db", default=str(HISTORY_DB))
    parser.add_argument("--matchup-profile", default=str(DEFAULT_MATCHUP_PROFILE))
    parser.add_argument("--trifecta-odds-db", default=str(default_odds_db) if default_odds_db else "")
    parser.add_argument("--threshold", type=float, default=27.0)
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--csv-out")
    parser.add_argument("--json-out")
    parser.add_argument("--html-out")
    args = parser.parse_args()

    top6, top10 = historical_venue_sets(args.history_db)
    matchup_profile = Path(args.matchup_profile) if args.matchup_profile else None
    matchup_profiles = read_matchup_profiles(matchup_profile) if matchup_profile and matchup_profile.exists() else {}
    df = daily_features(args.today_db, args.date, matchup_profiles=matchup_profiles)
    df = add_trifecta_odds_features(df, args.trifecta_odds_db, args.date)
    logic_df = pd.read_csv(args.logic_csv)
    logic_df = logic_df[logic_df["manshu_rate_pct"] >= args.threshold].copy()
    masks = atom_masks(df, top6, top10)
    all_venue_rows, actual_rows, watch_rows, unknown_atoms = build_rankings(
        df,
        logic_df.to_dict("records"),
        masks,
        threshold=args.threshold,
    )
    morning_candidates = build_morning_candidates(df, args.top_n)

    base_name = f"manshu_daily_rank_{args.date}"
    csv_path = Path(args.csv_out) if args.csv_out else OUT_DIR / f"{base_name}.csv"
    json_path = Path(args.json_out) if args.json_out else OUT_DIR / f"{base_name}.json"
    html_path = Path(args.html_out) if args.html_out else REPORT_DIR / f"{base_name}.html"

    rankable_rows = [row for row in all_venue_rows if rankable_final_row(row, args.threshold)]
    unified_top = rankable_rows[: args.top_n]
    strict_rows = unified_top
    combined = unified_top
    write_csv(csv_path, combined)
    payload = {
        "date": args.date,
        "threshold_pct": args.threshold,
        "logic_label": "Codex厳選ランキング（全ファクター統合）",
        "logic_summary": "過去検証27%以上の強条件、三連単人気1〜5が1号艇頭の売れ過ぎイン飛び、1号艇弱化、外枠上振れ、AI+下位の穴、AI/オッズ評価、展示タイム+1周タイム、夏場1周補正、スーパースリットアラート、平均STタイム/順位で近似したスリット隊形、女子戦攻略ファクター、天候/風波、今回メンバー同士の対戦相性をすべて同じスコアに混ぜた統合厳選ランキング。",
        "matchup_profile": str(matchup_profile) if matchup_profile else None,
        "matchup_pairs_loaded": len(matchup_profiles),
        "trifecta_odds_db": args.trifecta_odds_db,
        "races_with_trifecta_top5": int(df["trifecta_top5_count"].fillna(0).eq(5).sum()),
        "races_with_b1_trifecta_top5_1head": int(df["b1_trifecta_top5_1head"].fillna(0).eq(1).sum()),
        "races": int(len(df)),
        "races_with_full_tenji": int((df["tenji_boats"] >= 6).sum()),
        "races_with_full_isshu": int((df["isshu_boats"] >= 6).sum()),
        "baseline_only_hidden_count": int(len(all_venue_rows) - len(rankable_rows)),
        "unified_rank_top": unified_top,
        "all_venue_rank_top": unified_top,
        "strict_rank_top": unified_top,
        "actual_rank_top": actual_rows[: args.top_n],
        "watch_rank_top": watch_rows[: args.top_n],
        "morning_candidate_top": morning_candidates,
        "unknown_atoms": unknown_atoms,
        "outputs": {
            "csv": str(csv_path),
            "json": str(json_path),
            "html": str(html_path),
        },
    }
    safe_payload = json_safe(payload)
    json_path.write_text(json.dumps(safe_payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    make_report(html_path, args.date, unified_top, strict_rows[: args.top_n], watch_rows, args.top_n)
    print(json.dumps(safe_payload, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
