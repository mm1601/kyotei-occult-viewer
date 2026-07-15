#!/usr/bin/env python3
"""Monitor Codex BOATERS morning watchlist races and emit deadline alerts.

The betting flow is intentionally two-step:

1. Freeze a morning TOP list using only pre-exhibition data.
2. Near deadline, fetch BOATERS AI/exhibition/odds and alert only when the
   same morning-watch race still clears the post-exhibition threshold.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import os
import subprocess
import sys
import html
import re
import ssl
import sqlite3
import time
import urllib.error
import urllib.request
from contextlib import closing
from datetime import datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo

import joblib

import boatcast_original_exhibition as boatcast_original
import boaters_screenshot_data
import official_additional_features as additional_features
import original_boaters_24_forward as original_boaters_forward

try:
    import train_boaters_imitation_ai as imitation_ai
    import train_self_boatrace_ai as self_ai
    import train_trifecta_position_model as trifecta_position_model
    import train_venue_probability_overlay as venue_probability_overlay
except Exception:
    imitation_ai = None
    self_ai = None
    trifecta_position_model = None
    venue_probability_overlay = None


ROOT = Path(__file__).resolve().parents[1]
PRICE_DIR = ROOT.parent / "price_action_analysis"
PRICE_OUT = PRICE_DIR / "outputs"
PUBLIC_OUT = ROOT / "data" / "output"
PUSH_CONFIG = PUBLIC_OUT / "boaters_push_config.local.json"
DEFAULT_NTFY_TOPIC = "boat10000-codex-manshu-7d56f47f-ee5f-48f8-905a-ed6e5025b8db"
WORK_OUT = PRICE_OUT if PRICE_DIR.exists() else PUBLIC_OUT
TRIFECTA_ODDS_DB_CANDIDATES = [
    ROOT / "data" / "live_odds.db",
    Path.home() / "Desktop" / "kyotei_occult" / "data" / "live_odds.db",
]
HISTORY_DB = PRICE_OUT / "boaters_all_races.sqlite"
BUILD_DB_SCRIPT = (
    PRICE_DIR / "build_boaters_database.py"
    if (PRICE_DIR / "build_boaters_database.py").exists()
    else ROOT / "scripts" / "build_boaters_database.py"
)
RANK_SCRIPT = (
    ROOT / "scripts" / "rank_daily_manshu_candidates.py"
)
SITE_DATA_SCRIPT = ROOT / "scripts" / "build_boaters_manshu_site_data.py"
VENUE_EXHIBITION_FACTOR_DICTIONARY = PUBLIC_OUT / "venue_exhibition_factor_dictionary.json"
SUPER_SLIT_EFFECT_PROFILE = PUBLIC_OUT / "super_slit_place_boat_effects.json"
AVG_DIFF_THRESHOLD_EFFECT_PROFILE = PUBLIC_OUT / "avg_diff_threshold_effect_profile.json"
OFFICIAL_BEFOREINFO_URL = "https://www.boatrace.jp/owpc/pc/race/beforeinfo?rno={rno}&jcd={jcd}&hd={hd}"
OFFICIAL_TRIFECTA_ODDS_URL = "https://www.boatrace.jp/owpc/pc/race/odds3t?rno={rno}&jcd={jcd}&hd={hd}"
OFFICIAL_RACE_RESULT_URL = "https://www.boatrace.jp/owpc/pc/race/raceresult?rno={rno}&jcd={jcd}&hd={hd}"
OFFICIAL_BEFOREINFO_PARSER_VERSION = 3
BOATERS_IMITATION_MODEL_CACHE = {}
SELF_AI_MODEL_CACHE = {}
TRIFECTA_POSITION_MODEL_CACHE = {}
VENUE_PROBABILITY_OVERLAY_CACHE = {}
JST = ZoneInfo("Asia/Tokyo")
SUMMER_MONTHS = {6, 7, 8}
HALF_LAP_PLACE_NAMES = {"桐生", "江戸川"}
SUMMER_B1_FAST_DIFF = 0.10
SUMMER_B1_SLOW_DIFF = -0.10
SUMMER_B1_FAST_NIGE_DELTA_PP = 15
SUMMER_B1_SLOW_NIGE_DELTA_PP = -17
SUPER_SLIT_TENJI_ADV = 0.10
CORE_ALERT_RATE = 40.0
SUBCORE_ALERT_RATE_MIN = 38.0
SYNTHETIC_ODDS_MIN = 3.0
SYNTHETIC_ODDS_FILTER_LABEL = "合成オッズ3.0倍未満になる買い方はしない"
BUY_TICKET_MIN_POINTS = 5
BUY_TICKET_MAX_POINTS = 12
LOW_AI_VENUE_REVIVAL_MIN_TOP3_PP = 10.0
LOW_AI_VENUE_REVIVAL_MAIN_TOP3_PP = 12.0
LOW_AI_VENUE_REVIVAL_STRONG_TOP3_PP = 14.0
LOW_AI_VENUE_REVIVAL_HEAD_WIN_PP = 8.0
BIG50_SIGN_STRATEGY_IDS = {
    "codex_big50_top4_11r_b1odds50_b1tenji5_outer56chaos6_h2_balanced_10",
    "codex_big50_suminoe5_b1odds50_b1tenji4_outer56chaos6_h1_balanced_8",
    "codex_big50_top4_5r_b1odds40_b1avg020_outer56chaos6_static_no1_12",
    "codex_mikuni_big50_a_b1odds55_o56avg030_wave3_h1_ai13_has56_8",
    "codex_mikuni_big50_b_b1odds60_st6_o56avg030_box3_comp_has56_6",
}
TENJI_ONLY_VENUE_SIGN_STRATEGY_IDS = {
    "codex_edogawa_r9_12_b1odds45_nige40_outertop2_h1_ai13_8",
}
VALIDATED_BUY_STRATEGY_IDS = {
    "codex_big50_top4_11r_b1odds50_b1tenji5_outer56chaos6_h2_balanced_10",
    "codex_big50_suminoe5_b1odds50_b1tenji4_outer56chaos6_h1_balanced_8",
    "codex_big50_top4_5r_b1odds40_b1avg020_outer56chaos6_static_no1_12",
    "codex_ashiya_wave_b1weak_top3buff10_outer_h1_ai13_no1_has56_8",
    "codex_kiryu_wind6_b1odds45_h2_top3_no1_has56_12",
    "codex_toda_b1odds40_nige40_outerbox6",
    "codex_edogawa_r9_12_b1odds45_nige40_outertop2_h1_ai13_8",
    "codex_heiwajima_r9_12_b1odds55_nige65_outertop2_wave3_h2_no1_top6",
    "codex_tamagawa_r4_6_b1odds40_venue_debuff_h2_ai13_no1_has56_12",
    "codex_hamanako_r1_3_wave2_revival_b1avg000_outer56avg005_outerh2_no1_has56_4",
    "codex_gamagori_b1lap4_b1odds35_b1loss30_outer_h1_ai13_no1_has56_8",
    "codex_tokoname_b1loss40_b5top3rank1_wind4_h1_56_ai13_no1_has56_8",
    "codex_tsu_r4_8_top3buff12_top2heads56_h1_top3_has56_8",
    "codex_mikuni_big50_a_b1odds55_o56avg030_wave3_h1_ai13_has56_8",
    "codex_mikuni_big50_b_b1odds60_st6_o56avg030_box3_comp_has56_6",
    "codex_mikuni_r9_12_wind5_lowai_h2_ai13_no1_has56_12",
    "codex_biwako_top3buff15_lowai_box3_has56_6",
    "codex_suminoe_b1tenji5_avg010_h2_top3_no1_has56_12",
    "codex_amagasaki_r1_8_b1avg010_outer56avg050_h2_top3_no1_has56_12",
    "codex_naruto_r7_12_wave3_b1odds1_top3buff10_h2_top3_no1_has56_12",
    "codex_marugame_r4_8_b1loss45_b5top3rank1_h1_56_ai13_no1_has56_8",
    "codex_kojima_b1odds1_b1avg005_b1tenji4_outer56avg040_h1_ai13_no1_has56_8",
    "codex_miyajima_r1_3_b1odds3_b1lap5_b1tenji5_h2_ai13_no1_has56_12",
    "codex_tokuyama_r4_8_b1odds2_pct30_b1lap4_h1_ai13_no1_8",
    "codex_shimonoseki_r1_6_outer56avg010_b1odds50_h2_top3_no1_has56_12",
    "codex_wakamatsu_r4_8_head56_b1odds45_h2_ai13_no1_has56_12",
    "codex_fukuoka_r9_12_b1lap4_top3buff12_h1_ai13_has56_8",
    "codex_karatsu_b1loss45_top3buff10_b1odds45_h1_top3_8",
    "codex_omura_headbuff8_lowai_outer56avg020_h1_ai13_has56_8",
    "codex_odds_gap_b1_fade_strong12",
}
VENUE_SIGN_STRATEGY_IDS = VALIDATED_BUY_STRATEGY_IDS - {
    "codex_odds_gap_b1_fade_strong12",
}
VENUE_SIGN_ALERT_LOOKAHEAD_MINUTES = 10.0
VALIDATED_BUY_STRATEGY_ORDER = {
    strategy_id: index
    for index, strategy_id in enumerate(
        [
            "codex_big50_top4_11r_b1odds50_b1tenji5_outer56chaos6_h2_balanced_10",
            "codex_big50_top4_5r_b1odds40_b1avg020_outer56chaos6_static_no1_12",
            "codex_big50_suminoe5_b1odds50_b1tenji4_outer56chaos6_h1_balanced_8",
            "codex_ashiya_wave_b1weak_top3buff10_outer_h1_ai13_no1_has56_8",
            "codex_kiryu_wind6_b1odds45_h2_top3_no1_has56_12",
            "codex_toda_b1odds40_nige40_outerbox6",
            "codex_edogawa_r9_12_b1odds45_nige40_outertop2_h1_ai13_8",
            "codex_heiwajima_r9_12_b1odds55_nige65_outertop2_wave3_h2_no1_top6",
            "codex_tamagawa_r4_6_b1odds40_venue_debuff_h2_ai13_no1_has56_12",
            "codex_hamanako_r1_3_wave2_revival_b1avg000_outer56avg005_outerh2_no1_has56_4",
            "codex_gamagori_b1lap4_b1odds35_b1loss30_outer_h1_ai13_no1_has56_8",
            "codex_tokoname_b1loss40_b5top3rank1_wind4_h1_56_ai13_no1_has56_8",
            "codex_tsu_r4_8_top3buff12_top2heads56_h1_top3_has56_8",
            "codex_mikuni_big50_a_b1odds55_o56avg030_wave3_h1_ai13_has56_8",
            "codex_mikuni_big50_b_b1odds60_st6_o56avg030_box3_comp_has56_6",
            "codex_mikuni_r9_12_wind5_lowai_h2_ai13_no1_has56_12",
            "codex_biwako_top3buff15_lowai_box3_has56_6",
            "codex_suminoe_b1tenji5_avg010_h2_top3_no1_has56_12",
            "codex_amagasaki_r1_8_b1avg010_outer56avg050_h2_top3_no1_has56_12",
            "codex_naruto_r7_12_wave3_b1odds1_top3buff10_h2_top3_no1_has56_12",
            "codex_marugame_r4_8_b1loss45_b5top3rank1_h1_56_ai13_no1_has56_8",
            "codex_kojima_b1odds1_b1avg005_b1tenji4_outer56avg040_h1_ai13_no1_has56_8",
            "codex_miyajima_r1_3_b1odds3_b1lap5_b1tenji5_h2_ai13_no1_has56_12",
            "codex_tokuyama_r4_8_b1odds2_pct30_b1lap4_h1_ai13_no1_8",
            "codex_shimonoseki_r1_6_outer56avg010_b1odds50_h2_top3_no1_has56_12",
            "codex_wakamatsu_r4_8_head56_b1odds45_h2_ai13_no1_has56_12",
            "codex_fukuoka_r9_12_b1lap4_top3buff12_h1_ai13_has56_8",
            "codex_karatsu_b1loss45_top3buff10_b1odds45_h1_top3_8",
            "codex_omura_headbuff8_lowai_outer56avg020_h1_ai13_has56_8",
            "codex_odds_gap_b1_fade_strong12",
        ]
    )
}
VALIDATED_RULE_STATS = {
    "codex_big50_top4_11r_b1odds50_b1tenji5_outer56chaos6_h2_balanced_10": "5万舟警戒検証値: 回収率377.4% / 的中率7.4% / 5万舟捕捉46.7% / 平均10.0点 / 最大連敗40 / 年別ROI 2024年424.3%・2025年346.5%・2026年347.8%",
    "codex_big50_suminoe5_b1odds50_b1tenji4_outer56chaos6_h1_balanced_8": "5万舟警戒検証値: 回収率264.3% / 的中率4.7% / 5万舟捕捉10.0% / 8点固定 / 最大連敗47 / 年別ROI 2024年159.3%・2025年145.6%・2026年796.2%",
    "codex_big50_top4_5r_b1odds40_b1avg020_outer56chaos6_static_no1_12": "5万舟警戒検証値: 回収率1235.1% / 的中率14.3% / 5万舟捕捉33.3% / 12点固定 / 該当28Rの赤信号参考",
    "codex_ashiya_wave_b1weak_top3buff10_outer_h1_ai13_no1_has56_8": "実装検証値: 回収率267.40% / 188R / 平均3.30点 / 万舟7本 / 2024年284.43%・2025年236.71%・2026年302.35%",
    "codex_kiryu_wind6_b1odds45_h2_top3_no1_has56_12": "自前AI再検証値: 回収率232.12% / 231R / 平均4.82点 / 的中8本 / 2024-2025条件決定231.90%・2026未使用検証232.51% / 最大66連敗",
    "codex_toda_b1odds40_nige40_outerbox6": "実装検証値: 回収率272.32% / 94R / 6点固定 / 万舟4本 / 2024-2026年別すべて211%超",
    "codex_edogawa_r9_12_b1odds45_nige40_outertop2_h1_ai13_8": "実装検証値: 回収率284.51% / 42R / 平均7.71点 / 万舟3本 / 2024年202.22%・2025年455.25%・2026年162.25%",
    "codex_heiwajima_r9_12_b1odds55_nige65_outertop2_wave3_h2_no1_top6": "再分析検証値: 回収率332.13% / 121R / 8的中 / 6点固定 / 最大40連敗 / 最大配当除外229.41% / 2024年334.01%・2025年337.40%・2026年305.42%",
    "codex_tamagawa_r4_6_b1odds40_venue_debuff_h2_ai13_no1_has56_12": "再分析検証値: 回収率271.96% / 185R / 6的中 / 平均4.23点 / 最大80連敗 / 最大配当除外159.63% / 2024年253.37%・2025年298.43%・2026年257.33%",
    "codex_hamanako_r1_3_wave2_revival_b1avg000_outer56avg005_outerh2_no1_has56_4": "再分析検証値: 回収率418.78% / 74R / 14的中 / 4点固定 / 最大14連敗 / 最大配当除外243.38% / 2024年671.58%・2025年265.98%・2026年212.19%",
    "codex_gamagori_b1lap4_b1odds35_b1loss30_outer_h1_ai13_no1_has56_8": "実装検証値: 回収率269.69% / 95R / 平均3.05点 / 万舟2本 / 2024年189.74%・2025年422.68%・2026年142.90%",
    "codex_tokoname_b1loss40_b5top3rank1_wind4_h1_56_ai13_no1_has56_8": "実装検証値: 回収率249.56% / 20R / 平均8.00点 / 万舟1本 / 2024年221.88%・2025年221.41%・2026年291.56%",
    "codex_tsu_r4_8_top3buff12_top2heads56_h1_top3_has56_8": "実装検証値: 回収率283.84% / 89R / 平均5.24点 / 万舟3本 / 2025年225.94%・2026年317.09%（2024年該当0）",
    "codex_mikuni_big50_a_b1odds55_o56avg030_wave3_h1_ai13_has56_8": "5万舟警戒検証値: 回収率252.72% / 185R / 平均5.96点 / 的中16本 / 万舟9本 / 5万舟1本 / 2024年223.54%・2025年298.74%・2026年183.46%",
    "codex_mikuni_big50_b_b1odds60_st6_o56avg030_box3_comp_has56_6": "5万舟警戒検証値: 回収率215.59% / 211R / 6点固定 / 的中35本 / 万舟6本 / 5万舟1本 / 2024年217.63%・2025年241.00%・2026年143.24%",
    "codex_mikuni_r9_12_wind5_lowai_h2_ai13_no1_has56_12": "実装検証値: 回収率230.29% / 190R / 平均4.89点 / 万舟8本 / 2024年206.86%・2025年220.99%・2026年296.44%",
    "codex_biwako_top3buff15_lowai_box3_has56_6": "実装検証値: 回収率198.10% / 203R / 6点固定 / 万舟6本 / 2024年193.52%・2025年159.54%・2026年277.96%",
    "codex_suminoe_b1tenji5_avg010_h2_top3_no1_has56_12": "実装検証値: 回収率196.78% / 131R / 平均4.79点 / 万舟4本 / 2024年156.55%・2025年255.20%・2026年168.14%",
    "codex_amagasaki_r1_8_b1avg010_outer56avg050_h2_top3_no1_has56_12": "実装検証値: 回収率276.48% / 54R / 平均5.89点 / 万舟3本 / 2024年275.34%・2025年266.20%・2026年287.84%",
    "codex_naruto_r7_12_wave3_b1odds1_top3buff10_h2_top3_no1_has56_12": "実装検証値: 回収率182.90% / 489R / 平均3.33点 / 万舟6本 / 2024年167.44%・2025年166.38%・2026年244.26%",
    "codex_marugame_r4_8_b1loss45_b5top3rank1_h1_56_ai13_no1_has56_8": "実装検証値: 回収率250.09% / 28R / 平均8.00点 / 万舟2本 / 2024年350.97%・2025年161.44%・2026年290.83%",
    "codex_kojima_b1odds1_b1avg005_b1tenji4_outer56avg040_h1_ai13_no1_has56_8": "実装検証値: 回収率181.05% / 63R / 平均8.00点 / 万舟4本 / 2024年121.88%・2025年249.43%・2026年156.97%",
    "codex_miyajima_r1_3_b1odds3_b1lap5_b1tenji5_h2_ai13_no1_has56_12": "実装検証値: 回収率238.68% / 39R / 平均5.44点 / 万舟3本 / 2024年275.52%・2025年194.67%・2026年331.18%",
    "codex_tokuyama_r4_8_b1odds2_pct30_b1lap4_h1_ai13_no1_8": "実装検証値: 回収率182.08% / 180R / 平均4.03点 / 万舟3本 / 2024年243.35%・2025年106.85%・2026年215.00%",
    "codex_shimonoseki_r1_6_outer56avg010_b1odds50_h2_top3_no1_has56_12": "実装検証値: 回収率291.75% / 298R / 平均3.81点 / 万舟6本 / 2024年336.57%・2025年300.98%・2026年201.36%",
    "codex_wakamatsu_r4_8_head56_b1odds45_h2_ai13_no1_has56_12": "実装検証値: 回収率291.67% / 177R / 平均5.46点 / 万舟8本 / 2024年304.43%・2025年318.59%・2026年228.63%",
    "codex_fukuoka_r9_12_b1lap4_top3buff12_h1_ai13_has56_8": "実装検証値: 回収率247.51% / 60R / 平均5.63点 / 万舟3本 / 2024年224.88%・2025年262.35%・2026年288.75%",
    "codex_karatsu_b1loss45_top3buff10_b1odds45_h1_top3_8": "実装検証値: 回収率296.46% / 31R / 平均6.39点 / 万舟3本 / 2024年221.32%・2025年258.55%・2026年483.26%",
    "codex_omura_headbuff8_lowai_outer56avg020_h1_ai13_has56_8": "実装検証値: 回収率202.78% / 31R / 平均6.26点 / 万舟1本 / 2024年155.98%・2025年140.19%・2026年354.00%",
    "codex_odds_gap_b1_fade_strong12": "検証値: 万舟率25.0% / 1号艇飛び87.5%（8R）",
    "codex_odds_gap_b1_fade_filtered12": "参考検証値: 万舟率30.0% / 1号艇飛び80.0%（10R）。現行買い目では万舟的中未達のため参考",
    "codex_odds_gap_b1_danger_head1_8": "参考検証値: 万舟率30.77% / 1号艇飛び69.23%（13R）。現行買い目では本命昇格保留",
}
ENABLE_UNVALIDATED_EXPERIMENTAL_BUY_STRATEGIES = False
SUBCORE_WATCH_STRATEGY_IDS: set[str] = set()


def is_venue_sign_strategy(strategy):
    return (strategy or {}).get("strategy_id") in VENUE_SIGN_STRATEGY_IDS


def is_big50_sign_strategy(strategy):
    return (strategy or {}).get("strategy_id") in BIG50_SIGN_STRATEGY_IDS


def has_venue_sign_strategy(strategies):
    return any(is_venue_sign_strategy(strategy) for strategy in strategies or [])


def has_big50_sign_strategy(strategies):
    return any(is_big50_sign_strategy(strategy) for strategy in strategies or [])


def is_venue_sign_alert(alert):
    return bool((alert or {}).get("sign_alert")) or has_venue_sign_strategy((alert or {}).get("strategies") or [])


def venue_sign_strategy_ids(strategies):
    return [
        (strategy or {}).get("strategy_id")
        for strategy in strategies or []
        if is_venue_sign_strategy(strategy)
    ]


def sign_label_for_strategies(strategies):
    if has_big50_sign_strategy(strategies):
        return "5万舟警戒サイン"
    if has_venue_sign_strategy(strategies):
        return "24場サイン"
    return ""

PLACE_CODES = {
    "桐生": 1,
    "戸田": 2,
    "江戸川": 3,
    "平和島": 4,
    "多摩川": 5,
    "浜名湖": 6,
    "蒲郡": 7,
    "常滑": 8,
    "津": 9,
    "三国": 10,
    "びわこ": 11,
    "琵琶湖": 11,
    "住之江": 12,
    "尼崎": 13,
    "鳴門": 14,
    "丸亀": 15,
    "児島": 16,
    "宮島": 17,
    "徳山": 18,
    "下関": 19,
    "若松": 20,
    "芦屋": 21,
    "福岡": 22,
    "唐津": 23,
    "からつ": 23,
    "大村": 24,
}

SUPER_SLIT_ALERT_STATS = {
    2: {"win_rate_pct": 29.56, "top3_rate_pct": 70.91, "makuri_win_rate_pct": 11.53, "score_bonus": 11},
    3: {"win_rate_pct": 22.45, "top3_rate_pct": 66.55, "makuri_win_rate_pct": 10.76, "score_bonus": 10},
    4: {"win_rate_pct": 21.63, "top3_rate_pct": 61.09, "makuri_win_rate_pct": 12.94, "score_bonus": 12},
    5: {"win_rate_pct": 12.68, "top3_rate_pct": 49.43, "makuri_win_rate_pct": 5.45, "score_bonus": 11},
    6: {"win_rate_pct": 8.90, "top3_rate_pct": 40.69, "makuri_win_rate_pct": 4.16, "score_bonus": 10},
}
SUPER_SLIT_COMP_SCORE_BASE = {2: 0.80, 3: 0.80, 4: 0.95, 5: 0.95, 6: 0.75}
SUPER_SLIT_EFFECT_WIN_WEIGHT = 0.60
SUPER_SLIT_EFFECT_MULTIPLIER_MIN = 0.65
SUPER_SLIT_EFFECT_MULTIPLIER_MAX = 1.45

SLIT_FORMATION_STATS = {
    "b1_front_wall": {"label": "1前+2壁", "b1_win_pct": 34.93, "b1_fly_pct": 34.40, "winner_3to6_pct": 38.84, "manshu_rate_pct": 18.25},
    "b1_hole_vs_23": {"label": "1凹み", "b1_win_pct": 30.20, "b1_fly_pct": 38.18, "winner_3to6_pct": 45.89, "manshu_rate_pct": 19.07},
    "b2_wall_break_3peek": {"label": "2壁割れ3覗き", "b1_win_pct": 31.40, "b1_fly_pct": 36.83, "winner_3to6_pct": 51.12, "manshu_rate_pct": 19.10},
    "b3_peek_vs_12": {"label": "3覗き", "b1_win_pct": 31.00, "b1_fly_pct": 36.96, "winner_3to6_pct": 51.39, "manshu_rate_pct": 19.34},
    "b4_cadou_peek": {"label": "4カド覗き", "b1_win_pct": 29.81, "b1_fly_pct": 38.99, "winner_3to6_pct": 52.81, "manshu_rate_pct": 19.68},
    "outer456_pressure": {"label": "4〜6外圧", "b1_win_pct": 29.76, "b1_fly_pct": 38.20, "winner_3to6_pct": 52.31, "manshu_rate_pct": 20.87},
    "outer56_pressure_vs_1": {"label": "5/6外圧", "b1_win_pct": 29.26, "b1_fly_pct": 39.18, "winner_3to6_pct": 48.27, "manshu_rate_pct": 21.14},
    "center34_dent": {"label": "3/4中凹み", "b1_win_pct": 32.52, "b1_fly_pct": 34.90, "winner_3to6_pct": 43.68, "manshu_rate_pct": 20.09},
}

try:
    sys.path.insert(0, str(PRICE_DIR))
    from fill_joshi_boaters_data import (  # noqa: E402
        PLACE_SLUGS,
        extract_data_page,
        extract_last_minute_page,
        fetch_page,
    )
except Exception:
    PLACE_SLUGS = {
        "桐生": "kiryu",
        "戸田": "toda",
        "江戸川": "edogawa",
        "平和島": "heiwajima",
        "多摩川": "tamagawa",
        "浜名湖": "hamanako",
        "蒲郡": "gamagori",
        "常滑": "tokoname",
        "津": "tsu",
        "三国": "mikuni",
        "びわこ": "biwako",
        "琵琶湖": "biwako",
        "住之江": "suminoe",
        "尼崎": "amagasaki",
        "鳴門": "naruto",
        "丸亀": "marugame",
        "児島": "kojima",
        "宮島": "miyajima",
        "徳山": "tokuyama",
        "下関": "shimonoseki",
        "若松": "wakamatsu",
        "芦屋": "ashiya",
        "福岡": "fukuoka",
        "唐津": "karatsu",
        "からつ": "karatsu",
        "大村": "omura",
    }

    def load_next_data(text):
        match = re.search(
            r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
            text,
        )
        if not match:
            return None
        return json.loads(html.unescape(match.group(1)))

    def deref(state, item):
        if isinstance(item, dict) and "__ref" in item:
            return state.get(item["__ref"])
        return item

    def race_from_state(state):
        root = state.get("ROOT_QUERY", {})
        for key, value in root.items():
            if key.startswith("raceRoundDetail("):
                return deref(state, value)
        return None

    def fetch_page(slug, date, round_no, page, refresh=False):
        url = f"https://boaters-boatrace.com/race/{slug}/{date}/{round_no}R/{page}"
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Accept-Language": "ja,en-US;q=0.8,en;q=0.6",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                text = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"HTTP {exc.code}: {url}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"URL error: {url}: {exc}") from exc
        time.sleep(0.18)
        return text

    def extract_data_page(text):
        next_data = load_next_data(text)
        if not next_data:
            raise ValueError("NEXT_DATA not found")
        state = next_data["props"]["pageProps"]["initialApolloState"]
        race = race_from_state(state)
        if not race:
            raise ValueError("raceRoundDetail not found")

        ai_3ren = race.get("aiProba") or {}
        racer_ai = race.get("racerOddsProba") or {}
        waku_rows = [
            deref(state, item)
            for item in race.get('wakuAggregations({"boatNumbers":[1,2,3,4,5,6]})', [])
        ]
        waku_general = {
            item.get("waku"): item
            for item in waku_rows
            if item and item.get("aggType") == "一般"
        }
        start_rows = [
            deref(state, item)
            for item in race.get('startAggregations({"boatNumbers":[1,2,3,4,5,6]})', [])
        ]
        start_general = {
            item.get("waku"): item
            for item in start_rows
            if item and item.get("aggType") == "一般"
        }
        win_rows = race.get('winMethodAggregations({"boatNumbers":[1,2,3,4,5,6]})', [])
        boat1_year = next(
            (
                item
                for item in win_rows
                if item.get("waku") == 1 and item.get("aggregationRange") == "Year"
            ),
            {},
        )
        by_boat = {}
        for boat in range(1, 7):
            by_boat[boat] = {
                "ai_3ren_pct": pct(ai_3ren.get(f"aiProbaRacer{boat}3ren")),
                "general_3ren_pct": pct(
                    (waku_general.get(boat) or {}).get("result3renAvgWithWaku")
                ),
                "st_rank_general": (start_general.get(boat) or {}).get(
                    "startTimeRankAvgWithWaku"
                ),
                "ai_prediction_pct": pct(racer_ai.get(f"racerAiProba{boat}")),
                "odds_prediction_pct": pct(racer_ai.get(f"racerOddsProba{boat}")),
            }
        by_boat[1].update(
            {
                "nige_pct": pct(boat1_year.get("nigeRate")),
                "sasare_pct": pct(boat1_year.get("sasareRate")),
                "makurare_pct": pct(boat1_year.get("makurareRate")),
            }
        )
        return by_boat

    def keyed_by_boat(state, refs):
        result = {}
        for ref in refs or []:
            item = deref(state, ref) or {}
            result[item.get("boatNumber")] = item
        return result

    def extract_last_minute_page(text):
        next_data = load_next_data(text)
        if not next_data:
            raise ValueError("NEXT_DATA not found")
        state = next_data["props"]["pageProps"]["initialApolloState"]
        race = race_from_state(state)
        if not race:
            raise ValueError("raceRoundDetail not found")
        before = deref(state, race.get("beforeInfo")) or {}
        before_rows = keyed_by_boat(state, before.get("racers"))
        original_rows = keyed_by_boat(state, race.get("originalTenjis"))
        by_boat = {}
        for boat in range(1, 7):
            before_row = before_rows.get(boat) or {}
            original = original_rows.get(boat) or {}
            by_boat[boat] = {
                "tenji_time": before_row.get("tenjiTime"),
                "start_tenji_time": before_row.get("startTenjiTime"),
                "start_tenji_rank": before_row.get("startTenjiRank"),
                "tenji_rank": before_row.get("tenjiRank"),
                "before_start_sinnyu": before_row.get("startSinnyu"),
                "tilt": before_row.get("tilt"),
                "isshu_time": original.get("isshuTime"),
                "chokusen_time": original.get("chokusenTime"),
                "hanshu_time": original.get("hanshuTime"),
                "mawariashi_time": original.get("mawariashiTime"),
            }
        return by_boat


def as_num(value):
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def as_int(value):
    number = as_num(value)
    return None if number is None else int(number)


def is_summer_date(value):
    if not value:
        return False
    text = str(value)
    try:
        month = int(text[5:7])
    except (TypeError, ValueError):
        return False
    return month in SUMMER_MONTHS


def summer_b1_isshu_factor(date_value, b1_avg_diff, isshu_boats=None):
    b1_avg_diff = as_num(b1_avg_diff)
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


def pct(value):
    number = as_num(value)
    if number is None:
        return None
    if -1 <= number <= 1:
        number *= 100
    return round(number, 2)


def _live_next_data(text):
    match = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        text,
    )
    if not match:
        return None
    return json.loads(html.unescape(match.group(1)))


def _live_deref(state, item):
    if isinstance(item, dict) and "__ref" in item:
        return state.get(item["__ref"])
    return item


def _live_race_from_state(state):
    root = state.get("ROOT_QUERY", {})
    for key, value in root.items():
        if key.startswith("raceRoundDetail("):
            return _live_deref(state, value)
    return None


def extract_live_odds_page(text):
    """Read BOATERS AI odds probabilities from the data page.

    The normal page parser may come from an adjacent project, so this local
    helper keeps the monitor able to refresh odds after exhibition independently.
    """
    next_data = _live_next_data(text)
    if not next_data:
        return {}
    state = next_data.get("props", {}).get("pageProps", {}).get("initialApolloState") or {}
    race = _live_race_from_state(state) or {}
    odds_proba = race.get("racerOddsProba") or {}
    by_boat = {}
    for boat in range(1, 7):
        by_boat[boat] = {
            "odds_prediction_pct": pct(odds_proba.get(f"racerOddsProba{boat}")),
        }
    return by_boat


def fmt_pct(value):
    number = as_num(value)
    return "-" if number is None else f"{number:.1f}%"


def fmt_time(value):
    number = as_num(value)
    return "-" if number is None else f"{number:.2f}"


def norm_combo(value):
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def fmt_ticket(ticket):
    combo = norm_combo(ticket)
    return "-".join(combo) if len(combo) == 3 else str(ticket)


def parse_dt(value):
    if not value:
        return None
    return datetime.fromisoformat(str(value)).astimezone(JST)


def today_jst():
    return datetime.now(JST).date().isoformat()


def run_cmd(cmd, cwd):
    result = subprocess.run(cmd, cwd=str(cwd), text=True, capture_output=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(detail[-4000:] or f"command failed: {' '.join(cmd)}")
    return result.stdout


def fetch_boaters_page(slug, date, round_no, page, refresh=False):
    url = f"https://boaters-boatrace.com/race/{slug}/{date}/{round_no}R/{page}"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept-Language": "ja,en-US;q=0.8,en;q=0.6",
        },
    )
    context = None
    try:
        import certifi

        context = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        context = ssl.create_default_context()
    try:
        with urllib.request.urlopen(request, timeout=25, context=context) as response:
            text = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}: {url}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"URL error: {url}: {exc}") from exc
    time.sleep(0.18)
    return text


def public_ranking_path(date_text):
    return PUBLIC_OUT / f"boaters_manshu_ranking_{date_text.replace('-', '')}.json"


def public_codex_ranking_path(date_text):
    return PUBLIC_OUT / f"boaters_manshu_ranking_codex_{date_text.replace('-', '')}.json"


def morning_ranking_path(date_text):
    return PUBLIC_OUT / f"boaters_manshu_morning_ranking_{date_text.replace('-', '')}.json"


def live_ranking_path(date_text):
    return PUBLIC_OUT / f"boaters_manshu_live_ranking_{date_text.replace('-', '')}.json"


def official_live_ranking_path(date_text):
    return PUBLIC_OUT / f"official_live_ranking_{date_text.replace('-', '')}.json"


def state_path(date_text):
    return PUBLIC_OUT / f"boaters_manshu_alert_state_{date_text.replace('-', '')}.json"


def alerts_path(date_text):
    return PUBLIC_OUT / f"boaters_manshu_alerts_{date_text.replace('-', '')}.json"


def official_beforeinfo_path(date_text):
    return PUBLIC_OUT / f"official_boatrace_beforeinfo_{date_text.replace('-', '')}.json"


def forward_validation_path(date_text):
    return PUBLIC_OUT / "forward_validation" / f"core_focus_forward_{date_text.replace('-', '')}.json"


def original_boaters_shadow_path(date_text):
    return (
        PUBLIC_OUT
        / "forward_validation"
        / f"original_boaters_24_shadow_{date_text.replace('-', '')}.json"
    )


def original_boaters_shadow_summary_path():
    return PUBLIC_OUT / "forward_validation" / "original_boaters_24_shadow_summary.json"


def normalize_ranking_row(row, rank_no=None):
    out = dict(row or {})
    if rank_no is not None:
        out.setdefault("rank", rank_no)
    if out.get("manshu_rate_pct") is None and out.get("best_manshu_rate_pct") is not None:
        out["manshu_rate_pct"] = out.get("best_manshu_rate_pct")
    if out.get("recent_rate_pct") is None and out.get("best_recent_rate_pct") is not None:
        out["recent_rate_pct"] = out.get("best_recent_rate_pct")
    if out.get("condition") is None and out.get("best_condition") is not None:
        out["condition"] = out.get("best_condition")

    metrics = dict(out.get("metrics") or {})
    flat_metric_keys = {
        "b1_ai_prediction_pct": "boat1_ai_prediction_pct",
        "b1_odds_prediction_pct": "boat1_odds_prediction_pct",
        "b1_odds_rank": "boat1_odds_rank",
        "b1_popularity_level": "b1_popularity_level",
        "b1_popularity_source": "b1_popularity_source",
        "b1_trifecta_top5_1head": "b1_trifecta_top5_1head",
        "trifecta_top5_head1_count": "trifecta_top5_head1_count",
        "trifecta_top5_count": "trifecta_top5_count",
        "trifecta_top10_head1_count": "trifecta_top10_head1_count",
        "trifecta_top10_count": "trifecta_top10_count",
        "b1_trifecta_first_rank": "b1_trifecta_first_rank",
        "wind_speed": "wind_speed",
        "wave_height": "wave_height",
    }
    for source_key, metrics_key in flat_metric_keys.items():
        if metrics.get(metrics_key) is None and out.get(source_key) is not None:
            metrics[metrics_key] = out.get(source_key)
    if metrics:
        out["metrics"] = metrics
    return out


def ranking_rows(payload, top_n):
    rows = (
        payload.get("actual_rank_top")
        or payload.get("strict_races")
        or payload.get("races")
        or payload.get("unified_rank_top")
        or []
    )
    return [normalize_ranking_row(row, rank_no=index) for index, row in enumerate(list(rows)[:top_n], start=1)]


def morning_watch_rows(payload, top_n):
    rows = payload.get("morning_candidates") or []
    if not rows:
        rows = [
            row
            for row in payload.get("races") or []
            if str(row.get("ranking_type") or "") == "morning_watchlist"
            or str(row.get("candidate_phase") or "") == "morning_watchlist"
        ]
    if not rows:
        rows = ranking_rows(payload, top_n)
    unique = []
    seen = set()
    for row in rows:
        race_id = row.get("race_id") or (row.get("place_id"), row.get("round"))
        if race_id in seen:
            continue
        seen.add(race_id)
        unique.append(row)
        if len(unique) >= top_n:
            break
    return unique


def morning_race_with_live_rate(morning_race, live_race):
    """Keep the morning order, but replace rate/metrics with live final checks.

    The public page and notification flow use the morning list as the race
    universe.  When a refreshed live ranking exists, this helper carries the
    live post-exhibition rate into that fixed morning row without changing its
    morning rank.
    """
    if not live_race:
        row = dict(morning_race)
        row.setdefault("rate_source", "morning_pre_exhibition")
        return row
    row = dict(morning_race)
    row["morning_manshu_rate_pct"] = morning_race.get("manshu_rate_pct")
    row["morning_rate_source"] = "pre_exhibition_watchlist"
    row["last_minute_manshu_rate_pct"] = live_race.get("manshu_rate_pct")
    row["rate_source"] = "post_exhibition_live_ranking"
    row["live_rank"] = live_race.get("rank")
    for key in (
        "manshu_rate_pct",
        "base_manshu_rate_pct",
        "recent_rate_pct",
        "condition",
        "matched_logic_count",
        "composite_edges",
        "metrics",
        "selection",
        "candidate_reasons",
        "candidate_score",
        "weather",
        "weather_degree",
        "wind_speed",
        "wind_direction",
        "water_degree",
        "wave_height",
        "result",
    ):
        if live_race.get(key) is not None:
            row[key] = live_race.get(key)
    return row


def snapshot_morning_ranking(date_text, source_path):
    """Freeze the first available morning order for monitoring comparisons."""
    target = morning_ranking_path(date_text)
    if target.exists() or source_path is None or not source_path.exists():
        return target if target.exists() else source_path
    payload = load_json(source_path, {})
    if isinstance(payload, dict):
        payload["snapshot_type"] = "morning_fixed"
        payload["snapshot_created_at"] = datetime.now(JST).isoformat(timespec="seconds")
        payload["snapshot_source"] = str(source_path)
        save_json(target, payload)
        return target
    return source_path


def has_full_exhibition(metrics):
    lap_count = max(
        int(as_num(metrics.get("isshu_boats")) or 0),
        int(as_num(metrics.get("raw_isshu_boats")) or 0),
        int(as_num(metrics.get("hanshu_boats")) or 0),
        int(as_num(metrics.get("raw_hanshu_boats")) or 0),
    )
    return int(as_num(metrics.get("tenji_boats")) or 0) >= 6 and lap_count >= 6


def has_tenji_exhibition(metrics):
    return int(as_num(metrics.get("tenji_boats")) or 0) >= 6


def has_strategy_ready_exhibition(metrics, strategies=None):
    if has_full_exhibition(metrics):
        return True
    strategy_ids = {
        strategy.get("strategy_id")
        for strategy in strategies or []
        if strategy.get("strategy_id")
    }
    return bool(strategy_ids & TENJI_ONLY_VENUE_SIGN_STRATEGY_IDS) and has_tenji_exhibition(metrics)


def exhibition_missing_reason(metrics):
    if has_full_exhibition(metrics):
        return ""
    tenji_count = int(as_num(metrics.get("tenji_boats")) or 0)
    lap_count = max(
        int(as_num(metrics.get("isshu_boats")) or 0),
        int(as_num(metrics.get("raw_isshu_boats")) or 0),
        int(as_num(metrics.get("hanshu_boats")) or 0),
        int(as_num(metrics.get("raw_hanshu_boats")) or 0),
    )
    if tenji_count <= 0 and lap_count <= 0:
        return "BOATERS未公開"
    if tenji_count < 6 and lap_count < 6:
        return f"展示{tenji_count}/6・1周/半周{lap_count}/6"
    if tenji_count < 6:
        return f"展示{tenji_count}/6"
    return f"1周/半周{lap_count}/6"


def db_race_count(db_path):
    if not db_path.exists():
        return 0
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as con:
            return int(con.execute("SELECT COUNT(*) FROM races").fetchone()[0] or 0)
    except sqlite3.Error:
        return 0


def fetch_public_ranking(date_text, url_base):
    if not url_base:
        return None
    url = f"{str(url_base).rstrip('/')}/boaters_manshu_ranking_{date_text.replace('-', '')}.json"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Codex BOATERS monitor)",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            if response.status >= 400:
                return None
            payload = response.read().decode("utf-8", errors="replace")
    except Exception:
        return None
    public_json = public_ranking_path(date_text)
    public_json.parent.mkdir(parents=True, exist_ok=True)
    public_json.write_text(payload, encoding="utf-8")
    return public_json


def ensure_morning_ranking(
    date_text,
    top_n=10,
    threshold=27.0,
    rebuild=False,
    no_build=False,
    ranking_url_base=None,
):
    official_json = official_live_ranking_path(date_text)
    if no_build and not rebuild:
        return official_json if official_json.exists() else None
    public_json = public_ranking_path(date_text)
    if public_json.exists() and not rebuild:
        return snapshot_morning_ranking(date_text, public_json)
    if not rebuild:
        fetched = fetch_public_ranking(date_text, ranking_url_base)
        if fetched is not None:
            return snapshot_morning_ranking(date_text, fetched)
    if no_build:
        return None

    db_path = WORK_OUT / f"boaters_today_{date_text}.sqlite"
    if rebuild or not db_path.exists():
        cmd = [
            sys.executable,
            str(BUILD_DB_SCRIPT),
            "--mode",
            "full-daily",
            "--start-date",
            date_text,
            "--end-date",
            date_text,
            "--db",
            str(db_path),
            "--sleep",
            "0.08",
            "--workers",
            "3",
        ]
        if rebuild:
            # BOATERS releases originalTenjis shortly before deadline.  A DB
            # detail row fetched in the morning is still marked done, so force
            # refetch when the monitor explicitly rebuilds the same-day ranking.
            cmd.append("--refresh")
        run_cmd(cmd, BUILD_DB_SCRIPT.parent)

    if db_race_count(db_path) == 0:
        if public_json.exists():
            return public_json
        raise RuntimeError(f"BOATERS daily DB has no races: {db_path}")

    rank_json = WORK_OUT / f"manshu_daily_rank_{date_text}.json"
    rank_csv = WORK_OUT / f"manshu_daily_rank_{date_text}.csv"
    rank_html = WORK_OUT / "boaters_report" / f"manshu_daily_rank_{date_text}.html"
    run_cmd(
        [
            sys.executable,
            str(RANK_SCRIPT),
            "--date",
            date_text,
            "--today-db",
            str(db_path),
            "--history-db",
            str(HISTORY_DB if HISTORY_DB.exists() else PUBLIC_OUT / "boaters_all_races.sqlite"),
            "--threshold",
            str(threshold),
            "--top-n",
            str(top_n),
            "--json-out",
            str(rank_json),
            "--csv-out",
            str(rank_csv),
            "--html-out",
            str(rank_html),
        ],
        RANK_SCRIPT.parent,
    )
    run_cmd(
        [
            sys.executable,
            str(SITE_DATA_SCRIPT),
            "--source-json",
            str(rank_json),
            "--source-csv",
            str(rank_csv),
            "--out",
            str(public_json),
            "--top-n",
            str(top_n),
        ],
        ROOT,
    )
    return snapshot_morning_ranking(date_text, public_json)


def build_live_ranking(date_text, top_n=10, threshold=27.0):
    """Build a refreshed exhibition-aware ranking without replacing morning order."""
    db_path = WORK_OUT / f"boaters_today_{date_text}.sqlite"
    run_cmd(
        [
            sys.executable,
            str(BUILD_DB_SCRIPT),
            "--mode",
            "full-daily",
            "--start-date",
            date_text,
            "--end-date",
            date_text,
            "--db",
            str(db_path),
            "--sleep",
            "0.08",
            "--workers",
            "3",
            "--refresh",
        ],
        BUILD_DB_SCRIPT.parent,
    )
    if db_race_count(db_path) == 0:
        raise RuntimeError(f"BOATERS live DB has no races: {db_path}")

    rank_json = WORK_OUT / f"manshu_daily_rank_live_{date_text}.json"
    rank_csv = WORK_OUT / f"manshu_daily_rank_live_{date_text}.csv"
    rank_html = WORK_OUT / "boaters_report" / f"manshu_daily_rank_live_{date_text}.html"
    out_json = live_ranking_path(date_text)
    run_cmd(
        [
            sys.executable,
            str(RANK_SCRIPT),
            "--date",
            date_text,
            "--today-db",
            str(db_path),
            "--history-db",
            str(HISTORY_DB if HISTORY_DB.exists() else PUBLIC_OUT / "boaters_all_races.sqlite"),
            "--threshold",
            str(threshold),
            "--top-n",
            str(top_n),
            "--json-out",
            str(rank_json),
            "--csv-out",
            str(rank_csv),
            "--html-out",
            str(rank_html),
        ],
        RANK_SCRIPT.parent,
    )
    run_cmd(
        [
            sys.executable,
            str(SITE_DATA_SCRIPT),
            "--source-json",
            str(rank_json),
            "--source-csv",
            str(rank_csv),
            "--out",
            str(out_json),
            "--top-n",
            str(top_n),
        ],
        ROOT,
    )
    return out_json


def existing_live_ranking_path(date_text):
    candidates = [
        official_live_ranking_path(date_text),
        live_ranking_path(date_text),
        WORK_OUT / f"manshu_daily_rank_live_{date_text}.json",
        public_ranking_path(date_text),
        WORK_OUT / f"manshu_daily_rank_{date_text}.json",
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def load_json(path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def save_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


_SUPER_SLIT_EFFECT_PROFILE = None


def load_super_slit_effect_profile():
    """Load venue/boat specific super-slit lift profile, if generated."""

    global _SUPER_SLIT_EFFECT_PROFILE
    if _SUPER_SLIT_EFFECT_PROFILE is not None:
        return _SUPER_SLIT_EFFECT_PROFILE
    payload = load_json(SUPER_SLIT_EFFECT_PROFILE, {})
    if not isinstance(payload, dict):
        payload = {}
    _SUPER_SLIT_EFFECT_PROFILE = payload
    return payload


def super_slit_effect_index(stats):
    if not stats:
        return None
    top3_lift = as_num(stats.get("top3_lift_pp"))
    win_lift = as_num(stats.get("win_lift_pp"))
    if top3_lift is None and win_lift is None:
        return None
    return (top3_lift or 0.0) + (win_lift or 0.0) * SUPER_SLIT_EFFECT_WIN_WEIGHT


def super_slit_effect_for(place_name, boat_number):
    """Return score knobs for a super-slit alert at this venue/lane."""

    boat = int(as_num(boat_number) or 0)
    base = SUPER_SLIT_ALERT_STATS.get(boat) or {}
    base_score_bonus = int(base.get("score_bonus") or 0)
    base_comp_bonus = SUPER_SLIT_COMP_SCORE_BASE.get(boat, 0.0)
    profile = load_super_slit_effect_profile()
    overall = (profile.get("overall_by_boat") or {}).get(str(boat)) or {}
    base_index = super_slit_effect_index(overall)
    if base_index is None or base_index <= 0:
        base_index = super_slit_effect_index(
            {
                "win_lift_pp": base.get("win_rate_pct"),
                "top3_lift_pp": base.get("top3_rate_pct"),
            }
        ) or 1.0

    min_n = int(as_num(profile.get("min_alert_n_reliable")) or 30)
    key = f"{place_name}|{boat}" if place_name else ""
    stats = (profile.get("place_boat") or {}).get(key) or {}
    if int(as_num(stats.get("alert_n")) or 0) < min_n:
        stats = overall

    effect_index = super_slit_effect_index(stats) or base_index
    multiplier = bounded(
        effect_index / base_index,
        SUPER_SLIT_EFFECT_MULTIPLIER_MIN,
        SUPER_SLIT_EFFECT_MULTIPLIER_MAX,
    )
    score_bonus = int(round(base_score_bonus * multiplier))
    comp_bonus = round(base_comp_bonus * multiplier, 3)
    value_bonus = 0.0
    if boat in {4, 5, 6}:
        value_bonus = round(bounded(0.35 * multiplier, 0.08, 0.60), 3)

    return {
        "source": "place_boat" if stats and stats is not overall else "boat_default",
        "alert_n": int(as_num(stats.get("alert_n")) or 0),
        "multiplier": round(multiplier, 3),
        "score_bonus": score_bonus,
        "comp_bonus": comp_bonus,
        "value_bonus": value_bonus,
        "win_lift_pp": round(as_num(stats.get("win_lift_pp")) or 0.0, 2),
        "top3_lift_pp": round(as_num(stats.get("top3_lift_pp")) or 0.0, 2),
        "alert_win_rate_pct": round(as_num(stats.get("alert_win_rate_pct")) or 0.0, 2),
        "alert_top3_rate_pct": round(as_num(stats.get("alert_top3_rate_pct")) or 0.0, 2),
    }


_AVG_DIFF_THRESHOLD_FACTOR_INDEX = None


def load_avg_diff_threshold_factor_index():
    """Load fixed average-difference venue/lane factors, if generated."""

    global _AVG_DIFF_THRESHOLD_FACTOR_INDEX
    if _AVG_DIFF_THRESHOLD_FACTOR_INDEX is not None:
        return _AVG_DIFF_THRESHOLD_FACTOR_INDEX
    index = {}
    payload = load_json(AVG_DIFF_THRESHOLD_EFFECT_PROFILE, {})
    for factor in payload.get("factors") or []:
        if not factor.get("use_in_prediction"):
            continue
        if factor.get("confidence") not in {"S", "A"}:
            continue
        key = (
            str(factor.get("venue") or ""),
            int(as_num(factor.get("lane")) or 0),
            str(factor.get("metric_id") or ""),
        )
        if key[0] and key[1] and key[2]:
            index.setdefault(key, []).append(factor)
    _AVG_DIFF_THRESHOLD_FACTOR_INDEX = index
    return index


def avgdiff_threshold_value(row, metric_id):
    value_key = {
        "avg_combo_diff": "avg_isshu_diff",
        "avg_tenji_diff": "avg_tenji_diff",
        "avg_lap_diff": "isshu_avg_diff",
        "avg_chokusen_diff": "avg_chokusen_diff",
        "avg_mawariashi_diff": "avg_mawariashi_diff",
        "avg_start_tenji_diff": "avg_start_tenji_diff",
    }.get(metric_id)
    return as_num(row.get(value_key)) if value_key else None


def avgdiff_threshold_matches(row, place_name):
    index = load_avg_diff_threshold_factor_index()
    if not index or not place_name:
        return []
    boat = int(row.get("boat_number") or 0)
    candidates = []
    for (venue, lane, metric_id), factors in index.items():
        if venue != place_name or lane != boat:
            continue
        value = avgdiff_threshold_value(row, metric_id)
        if value is None:
            continue
        for factor in factors:
            threshold = as_num(factor.get("threshold_value"))
            operator = str(factor.get("threshold_operator") or "")
            if threshold is None:
                continue
            matched = value <= threshold if operator == "<=" else value >= threshold if operator == ">=" else False
            if not matched:
                continue
            item = dict(factor)
            item["actual_value"] = round(value, 4)
            item["source"] = "avg_diff_threshold_profile"
            candidates.append(item)

    best_by_metric = {}
    for item in candidates:
        metric_id = item.get("metric_id")
        score = (
            {"S": 2, "A": 1}.get(item.get("confidence"), 0),
            abs(as_num(item.get("top3_rate_pp")) or 0.0),
            abs(as_num(item.get("win_rate_pp")) or 0.0),
            as_num(item.get("sample_count")) or 0.0,
        )
        current = best_by_metric.get(metric_id)
        if current is None or score > current[0]:
            best_by_metric[metric_id] = (score, item)
    out = [item for _, item in best_by_metric.values()]
    out.sort(
        key=lambda item: (
            {"S": 0, "A": 1}.get(item.get("confidence"), 9),
            -abs(as_num(item.get("top3_rate_pp")) or 0.0),
            -abs(as_num(item.get("win_rate_pp")) or 0.0),
        )
    )
    return out[:4]


_VENUE_EXHIBITION_FACTOR_INDEX = None


def load_venue_exhibition_factor_index():
    """Load high-confidence venue/lane exhibition factors, if generated."""

    global _VENUE_EXHIBITION_FACTOR_INDEX
    if _VENUE_EXHIBITION_FACTOR_INDEX is not None:
        return _VENUE_EXHIBITION_FACTOR_INDEX
    index = {}
    payload = load_json(VENUE_EXHIBITION_FACTOR_DICTIONARY, {})
    for factor in payload.get("factors") or []:
        if not factor.get("use_in_prediction"):
            continue
        if factor.get("confidence") not in {"S", "A"}:
            continue
        key = (str(factor.get("venue") or ""), int(as_num(factor.get("lane")) or 0), str(factor.get("metric_id") or ""))
        if key[0] and key[1] and key[2]:
            index.setdefault(key, []).append(factor)
    _VENUE_EXHIBITION_FACTOR_INDEX = index
    return index


def factor_dictionary_status():
    """Report whether all venue-specific prediction dictionaries are usable."""

    venue_payload = load_json(VENUE_EXHIBITION_FACTOR_DICTIONARY, {})
    venue_index = load_venue_exhibition_factor_index()
    venue_names = {key[0] for key in venue_index}

    avgdiff_payload = load_json(AVG_DIFF_THRESHOLD_EFFECT_PROFILE, {})
    avgdiff_index = load_avg_diff_threshold_factor_index()
    avgdiff_names = {key[0] for key in avgdiff_index}

    super_slit_payload = load_super_slit_effect_profile()
    place_boat = super_slit_payload.get("place_boat") or {}
    super_slit_names = {
        str(key).split("|", 1)[0]
        for key in place_boat
        if "|" in str(key)
    }

    status = {
        "venue_exhibition": {
            "path": str(VENUE_EXHIBITION_FACTOR_DICTIONARY),
            "exists": VENUE_EXHIBITION_FACTOR_DICTIONARY.exists(),
            "version": venue_payload.get("version"),
            "generated_at": venue_payload.get("generated_at"),
            "factor_count": int(as_num(venue_payload.get("factor_count")) or 0),
            "active_index_keys": len(venue_index),
            "venue_count": len(venue_names),
            "ready": VENUE_EXHIBITION_FACTOR_DICTIONARY.exists() and len(venue_names) == 24,
        },
        "avg_diff_threshold": {
            "path": str(AVG_DIFF_THRESHOLD_EFFECT_PROFILE),
            "exists": AVG_DIFF_THRESHOLD_EFFECT_PROFILE.exists(),
            "version": avgdiff_payload.get("version"),
            "generated_at": avgdiff_payload.get("generated_at"),
            "factor_count": int(as_num(avgdiff_payload.get("factor_count")) or 0),
            "active_factor_count": sum(len(items) for items in avgdiff_index.values()),
            "active_index_keys": len(avgdiff_index),
            "venue_count": len(avgdiff_names),
            "ready": AVG_DIFF_THRESHOLD_EFFECT_PROFILE.exists() and len(avgdiff_names) == 24,
        },
        "super_slit": {
            "path": str(SUPER_SLIT_EFFECT_PROFILE),
            "exists": SUPER_SLIT_EFFECT_PROFILE.exists(),
            "generated_at": super_slit_payload.get("generated_at"),
            "place_boat_count": len(place_boat),
            "venue_count": len(super_slit_names),
            "ready": SUPER_SLIT_EFFECT_PROFILE.exists() and len(super_slit_names) == 24,
        },
    }
    status["ready"] = all(item["ready"] for item in status.values())
    return status


def venue_factor_value(row, factor):
    metric_id = str(factor.get("metric_id") or "")
    condition_id = str(factor.get("condition_id") or "")
    if condition_id.startswith("rank_") or factor.get("unit") == "rank":
        value_key = {
            "tenji_time": "tenji_time",
            "lap_time": "isshu_time",
            "chokusen_time": "chokusen_time",
            "mawariashi_time": "mawariashi_time",
            "start_tenji_time": "start_tenji_time",
        }.get(metric_id)
        if value_key and as_num(row.get(value_key)) is None:
            return None
        rank_key = {
            "tenji_time": "tenji_time_rank",
            "lap_time": "isshu_time_rank",
            "chokusen_time": "chokusen_time_rank",
            "mawariashi_time": "mawariashi_time_rank",
            "start_tenji_time": "start_tenji_time_rank",
        }.get(metric_id)
        return as_num(row.get(rank_key)) if rank_key else None
    value_key = {
        "tenji_time": "tenji_time",
        "lap_time": "isshu_time",
        "avg_tenji_diff": "avg_tenji_diff",
        "avg_lap_diff": "avg_isshu_diff",
        "avg_chokusen_diff": "avg_chokusen_diff",
        "avg_mawariashi_diff": "avg_mawariashi_diff",
        "avg_start_tenji_diff": "avg_start_tenji_diff",
        "chokusen_time": "chokusen_time",
        "mawariashi_time": "mawariashi_time",
        "start_tenji_time": "start_tenji_time",
    }.get(metric_id)
    return as_num(row.get(value_key)) if value_key else None


def venue_factor_matches(row, place_name):
    index = load_venue_exhibition_factor_index()
    if not index or not place_name:
        return []
    out = []
    boat = int(row.get("boat_number") or 0)
    for (venue, lane, metric_id), factors in index.items():
        if venue != place_name or lane != boat:
            continue
        for factor in factors:
            value = venue_factor_value(row, factor)
            threshold = as_num(factor.get("threshold_value"))
            operator = str(factor.get("threshold_operator") or "")
            if value is None or threshold is None:
                continue
            matched = value <= threshold if operator == "<=" else value >= threshold if operator == ">=" else False
            if matched:
                item = dict(factor)
                item["actual_value"] = round(value, 4)
                out.append(item)
    out.sort(
        key=lambda item: (
            {"S": 0, "A": 1}.get(item.get("confidence"), 9),
            -abs(as_num(item.get("win_rate_pp")) or 0),
            -abs(as_num(item.get("top3_rate_pp")) or 0),
        )
    )
    return out[:5]


def venue_low_ai_revival_profile(row):
    """Return the low-AI revival rule when a venue factor is strong enough."""

    ai_plus_rank = int(as_num(row.get("ai_plus_rank")) or 0)
    if ai_plus_rank not in {5, 6}:
        return None
    candidates = []
    for item in row.get("venue_factor_matches") or []:
        if item.get("direction") != "buff":
            continue
        targets = set(item.get("effect_targets") or [])
        top3_pp = as_num(item.get("top3_rate_pp")) or 0.0
        win_pp = as_num(item.get("win_rate_pp")) or 0.0
        has_top3_buff = bool(targets & {"top3_buff", "dont_keshi"})
        has_head_buff = "head_buff" in targets
        if not (
            has_top3_buff
            and top3_pp >= LOW_AI_VENUE_REVIVAL_MIN_TOP3_PP
        ) and not (
            has_head_buff
            and win_pp >= LOW_AI_VENUE_REVIVAL_HEAD_WIN_PP
        ):
            continue
        candidates.append(item)
    if not candidates:
        return None

    def revival_sort_key(item):
        top3_pp = as_num(item.get("top3_rate_pp")) or 0.0
        win_pp = as_num(item.get("win_rate_pp")) or 0.0
        confidence_score = {"S": 2, "A": 1}.get(item.get("confidence"), 0)
        return (confidence_score, top3_pp, win_pp, as_num(item.get("sample_count")) or 0)

    best = sorted(candidates, key=revival_sort_key, reverse=True)[0]
    top3_pp = as_num(best.get("top3_rate_pp")) or 0.0
    win_pp = as_num(best.get("win_rate_pp")) or 0.0
    confidence = str(best.get("confidence") or "")
    targets = set(best.get("effect_targets") or [])
    head_ok = "head_buff" in targets and win_pp >= LOW_AI_VENUE_REVIVAL_HEAD_WIN_PP
    strong = confidence == "S" or top3_pp >= LOW_AI_VENUE_REVIVAL_STRONG_TOP3_PP
    main = strong or top3_pp >= LOW_AI_VENUE_REVIVAL_MAIN_TOP3_PP
    if head_ok:
        role = "head_ok"
        role_label = "頭まで候補"
    elif ai_plus_rank == 6 and not main:
        role = "third_only"
        role_label = "3着だけ復活"
    else:
        role = "second_third"
        role_label = "2/3着復活"

    reason_bits = []
    if top3_pp >= LOW_AI_VENUE_REVIVAL_MIN_TOP3_PP:
        reason_bits.append(f"3着内差+{top3_pp:.1f}pt")
    if head_ok:
        reason_bits.append(f"1着差+{win_pp:.1f}pt")
    reason = (
        f"AI+{ai_plus_rank}位だが場別展示{confidence}バフ "
        f"{' / '.join(reason_bits)}"
    )
    reason += f"で{role_label}"
    return {
        "boat_number": row.get("boat_number"),
        "ai_plus_rank": ai_plus_rank,
        "role": role,
        "role_label": role_label,
        "head_ok": head_ok,
        "second_ok": role in {"second_third", "head_ok"},
        "third_ok": True,
        "strong": strong,
        "confidence": confidence,
        "metric_label": best.get("metric_label"),
        "condition_id": best.get("condition_id"),
        "actual_value": best.get("actual_value"),
        "top3_rate_pp": round(top3_pp, 2),
        "win_rate_pp": round(win_pp, 2),
        "sample_count": best.get("sample_count"),
        "reason": reason,
    }


def venue_low_ai_revival_summary(rows):
    out = []
    for row in rows or []:
        profile = row.get("venue_low_ai_revival_profile")
        if not profile:
            continue
        out.append(
            {
                "boat_number": row.get("boat_number"),
                "ai_plus_rank": profile.get("ai_plus_rank"),
                "role": profile.get("role"),
                "role_label": profile.get("role_label"),
                "top3_rate_pp": profile.get("top3_rate_pp"),
                "win_rate_pp": profile.get("win_rate_pp"),
                "confidence": profile.get("confidence"),
                "reason": profile.get("reason"),
            }
        )
    return out


def apply_venue_exhibition_factors(rows, place_name):
    for row in rows:
        base_matches = venue_factor_matches(row, place_name)
        avgdiff_matches = avgdiff_threshold_matches(row, place_name)
        row["venue_factor_matches"] = base_matches
        row["avgdiff_threshold_matches"] = avgdiff_matches
        avgdiff_head_delta = sum(as_num(item.get("head_score_delta")) or 0 for item in avgdiff_matches)
        avgdiff_top3_delta = sum(as_num(item.get("top3_score_delta")) or 0 for item in avgdiff_matches)
        avgdiff_manshu_delta = sum(as_num(item.get("manshu_score_delta")) or 0 for item in avgdiff_matches)
        row["avgdiff_head_score_delta"] = round(bounded(avgdiff_head_delta, -4.0, 4.0), 2)
        row["avgdiff_top3_score_delta"] = round(bounded(avgdiff_top3_delta, -4.0, 4.0), 2)
        row["avgdiff_manshu_score_delta"] = round(bounded(avgdiff_manshu_delta, -2.5, 2.5), 2)
        head_delta = sum(as_num(item.get("head_score_delta")) or 0 for item in base_matches)
        top3_delta = sum(as_num(item.get("top3_score_delta")) or 0 for item in base_matches)
        manshu_delta = sum(as_num(item.get("manshu_score_delta")) or 0 for item in base_matches)
        row["venue_head_score_delta"] = round(bounded(head_delta, -7.0, 7.0), 2)
        row["venue_top3_score_delta"] = round(bounded(top3_delta, -6.0, 6.0), 2)
        row["venue_manshu_score_delta"] = round(bounded(manshu_delta, -5.0, 5.0), 2)
        row["venue_score_bonus"] = round(
            bounded(row["venue_head_score_delta"] * 0.04 + row["venue_top3_score_delta"] * 0.03 + row["venue_manshu_score_delta"] * 0.02, -0.45, 0.45),
            3,
        )
        targets = {target for item in base_matches for target in (item.get("effect_targets") or [])}
        row["venue_effect_targets"] = sorted(targets)
        row["venue_dont_keshi"] = "dont_keshi" in targets
        row["venue_b1_head_debuff"] = row.get("boat_number") == 1 and "b1_head_debuff" in targets
        row["venue_b1_fly_manshu_watch"] = row.get("boat_number") == 1 and "b1_fly_manshu_watch" in targets
        row["venue_factor_reasons"] = [
            (
                f"{item.get('venue')}{int(item.get('lane') or row.get('boat_number'))}号艇 "
                f"{item.get('metric_label')} {item.get('condition_id')} "
                f"({item.get('confidence')}, 勝率差{item.get('win_rate_pp')}pt)"
            )
            for item in base_matches[:3]
        ]
        row["avgdiff_threshold_reasons"] = [
            (
                f"{item.get('venue')}{int(item.get('lane') or row.get('boat_number'))}号艇 "
                f"{item.get('metric_label')} {item.get('condition_id')} "
                f"({item.get('confidence')}, 1着差{item.get('win_rate_pp')}pt, 3着内差{item.get('top3_rate_pp')}pt)"
            )
            for item in avgdiff_matches[:3]
        ]
        revival = venue_low_ai_revival_profile(row)
        row["venue_low_ai_revival"] = bool(revival)
        row["venue_low_ai_revival_profile"] = revival or {}
        row["venue_low_ai_revival_role"] = (revival or {}).get("role") or ""
        row["venue_low_ai_revival_reasons"] = [revival["reason"]] if revival else []
        if revival:
            row["venue_dont_keshi"] = True
            if revival.get("head_ok"):
                row["venue_effect_targets"] = sorted(set(row["venue_effect_targets"]) | {"head_buff"})
    return rows


def refresh_ops_dashboard():
    script = ROOT / "scripts" / "build_manshu_ops_dashboard.py"
    if not script.exists():
        return {"ok": False, "error": f"missing script: {script}"}
    try:
        proc = subprocess.run(
            [sys.executable, str(script)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=90,
        )
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": (proc.stdout or "").strip()[-1200:],
            "stderr": (proc.stderr or "").strip()[-1200:],
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def merge_live_metrics_into_public_ranking(date_text, updates, now):
    if not updates:
        return False
    changed_any = False
    for path in (public_ranking_path(date_text), public_codex_ranking_path(date_text), morning_ranking_path(date_text)):
        changed_any = merge_live_metrics_into_ranking_path(path, updates, now) or changed_any
    return changed_any


def forward_history_db_path():
    for path in (HISTORY_DB, PUBLIC_OUT / "boaters_all_races.sqlite"):
        if path.exists():
            return path
    return PUBLIC_OUT / "boaters_all_races.sqlite"


def result_payload_from_race(row):
    result = (row or {}).get("result") or {}
    trifecta = norm_combo(
        result.get("trifecta")
        or (row or {}).get("trifecta")
        or (row or {}).get("winning_number3t1")
    )
    payout = as_int(
        result.get("payout_yen")
        or (row or {}).get("payout_yen")
        or (row or {}).get("result_payout_yen")
        or (row or {}).get("result_payout3t1")
    )
    if len(trifecta) != 3 or payout is None:
        return None
    return {
        "trifecta": fmt_ticket(trifecta),
        "trifecta_norm": trifecta,
        "payout_yen": payout,
        "manshu": payout >= 10000,
    }


def forward_result_index_from_rankings(date_text):
    out = {}
    for path in (
        live_ranking_path(date_text),
        public_ranking_path(date_text),
        public_codex_ranking_path(date_text),
        morning_ranking_path(date_text),
    ):
        payload = load_json(path, {})
        if not isinstance(payload, dict):
            continue
        for group_name in ("races", "strict_races", "actual_rank_top", "unified_rank_top", "morning_candidates"):
            for row in payload.get(group_name) or []:
                race_id = str(row.get("race_id") or "")
                if not race_id or race_id in out:
                    continue
                result = result_payload_from_race(row)
                if result:
                    out[race_id] = result
    return out


def forward_result_from_db(race_id):
    db_path = forward_history_db_path()
    if not db_path.exists() or not race_id:
        return None
    con = None
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        row = con.execute(
            """
            SELECT winning_number3t1, result_payout3t1
            FROM races
            WHERE race_id = ?
            """,
            (str(race_id),),
        ).fetchone()
        if not row:
            return None
        return result_payload_from_race(dict(row))
    except Exception:
        return None
    finally:
        if con is not None:
            con.close()


def parse_official_race_result_html(text):
    match = re.search(
        r"<td[^>]*>\s*3連単\s*</td>(.*?)(?:</tbody>|<td[^>]*>\s*3連複\s*</td>)",
        str(text or ""),
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None
    section = match.group(1)
    boats = re.findall(
        r'class=["\'][^"\']*\bnumberSet1_number\b[^"\']*["\'][^>]*>\s*([1-6])\s*</span>',
        section,
        flags=re.IGNORECASE,
    )
    payout_match = re.search(
        r'class=["\'][^"\']*\bis-payout1\b[^"\']*["\'][^>]*>\s*(?:&yen;|&#165;|¥|￥)?\s*([0-9,]+)',
        section,
        flags=re.IGNORECASE,
    )
    if len(boats) < 3 or not payout_match:
        return None
    return result_payload_from_race(
        {
            "winning_number3t1": "".join(boats[:3]),
            "result_payout3t1": payout_match.group(1).replace(",", ""),
        }
    )


def official_result_url_from_entry(entry):
    race_id_digits = norm_combo((entry or {}).get("race_id"))
    if len(race_id_digits) >= 12:
        hd = race_id_digits[:8]
        jcd = race_id_digits[-4:-2]
        rno = int(race_id_digits[-2:])
    else:
        hd = str((entry or {}).get("date") or "").replace("-", "")
        jcd = official_venue_code(entry)
        rno = int(as_num((entry or {}).get("round")) or 0)
    if len(hd) != 8 or not jcd or not (1 <= rno <= 12):
        return ""
    return OFFICIAL_RACE_RESULT_URL.format(rno=rno, jcd=jcd, hd=hd)


def forward_result_from_official(entry, now=None, grace_minutes=5):
    deadline = parse_dt((entry or {}).get("deadline_time"))
    if deadline is None:
        return None
    checked_at = now.astimezone(JST) if isinstance(now, datetime) else datetime.now(JST)
    if checked_at < deadline + timedelta(minutes=max(0, grace_minutes)):
        return None
    url = official_result_url_from_entry(entry)
    if not url:
        return None
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Codex BOATERS monitor)",
            "Accept-Language": "ja,en-US;q=0.8,en;q=0.6",
        },
    )
    try:
        import certifi

        context = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        context = ssl.create_default_context()
    try:
        with urllib.request.urlopen(request, timeout=12, context=context) as response:
            text = response.read().decode("utf-8", errors="replace")
    except Exception:
        return None
    return parse_official_race_result_html(text)


def resolve_forward_result(entry, result_index, now, official_cache=None):
    race_id = str((entry or {}).get("race_id") or "")
    result = forward_result_from_db(race_id) or (result_index or {}).get(race_id)
    if result is not None:
        return result
    cache = official_cache if official_cache is not None else {}
    if race_id not in cache:
        cache[race_id] = forward_result_from_official(entry, now=now)
    return cache.get(race_id)


def forward_entry_key(race_id, rule_id):
    return f"{race_id}:{rule_id or 'unknown'}"


def update_original_boaters_ticket_ev_from_target_odds(
    entry,
    odds_db=None,
    shadow_key="ticket_ev_shadow",
):
    shadow = entry.get(shadow_key)
    if (
        not isinstance(shadow, dict)
        or shadow.get("status") == "unavailable"
        or not shadow.get("tickets")
    ):
        return False
    odds_path = Path(odds_db) if odds_db else default_trifecta_odds_db()
    if not odds_path.exists():
        return False
    date_text = str(entry.get("date") or "")
    round_no = int(as_num(entry.get("round")) or 0)
    if not date_text or round_no <= 0:
        return False
    before = json.dumps(shadow, sort_keys=True, ensure_ascii=False)
    probability = {
        norm_combo(item.get("ticket") or item.get("combo")): float(
            as_num(item.get("probability")) or 0.0
        )
        for item in shadow.get("tickets") or []
        if len(norm_combo(item.get("ticket") or item.get("combo"))) == 3
    }
    if not probability:
        return False
    captures = []
    try:
        with closing(sqlite3.connect(f"file:{odds_path}?mode=ro", uri=True)) as con:
            table_exists = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='odds_target_capture'"
            ).fetchone()
            if not table_exists:
                return False
            for venue_code in venue_code_candidates(entry):
                rows = con.execute(
                    """
                    SELECT target_minutes, snapshot_at, minutes_to_deadline,
                           absolute_target_error_minutes, combo_count, source
                    FROM odds_target_capture
                    WHERE date=? AND CAST(venue_code AS INTEGER)=? AND race_no=?
                      AND status='complete' AND combo_count>=120
                    ORDER BY target_minutes DESC
                    """,
                    (date_text, int(float(venue_code)), round_no),
                ).fetchall()
                if rows:
                    captures = rows
                    break
            snapshots = shadow.setdefault("snapshots", {})
            for target, snapshot_at, minutes_to_deadline, target_error, combo_count, source in captures:
                odds_rows = con.execute(
                    """
                    SELECT combo, odds FROM odds_trifecta
                    WHERE date=? AND CAST(venue_code AS INTEGER)=? AND race_no=?
                      AND snapshot_at=?
                    """,
                    (date_text, int(float(venue_code)), round_no, snapshot_at),
                ).fetchall()
                all_odds = {
                    norm_combo(combo): float(value)
                    for combo, value in odds_rows
                    if len(norm_combo(combo)) == 3 and (as_num(value) or 0) > 0
                }
                inverse_total = sum(1.0 / value for value in all_odds.values())
                ticket_rows = []
                for combo, ticket_probability in probability.items():
                    value = all_odds.get(combo)
                    market_probability = (
                        (1.0 / value) / inverse_total
                        if value is not None and inverse_total > 0
                        else None
                    )
                    ticket_rows.append(
                        {
                            "ticket": fmt_ticket(combo),
                            "probability_pct": round(ticket_probability * 100.0, 6),
                            "odds": round(value, 2) if value is not None else None,
                            "expected_value": (
                                round(ticket_probability * value, 6)
                                if value is not None
                                else None
                            ),
                            "market_probability_pct": (
                                round(market_probability * 100.0, 6)
                                if market_probability is not None
                                else None
                            ),
                            "model_edge_pp": (
                                round((ticket_probability - market_probability) * 100.0, 6)
                                if market_probability is not None
                                else None
                            ),
                        }
                    )
                complete_rows = [item for item in ticket_rows if item["odds"] is not None]
                expected_value_sum = sum(
                    float(item["expected_value"] or 0.0) for item in complete_rows
                )
                inverse_selected = sum(
                    1.0 / float(item["odds"])
                    for item in complete_rows
                    if (item["odds"] or 0) > 0
                )
                key = f"t{int(target)}"
                snapshots[key] = {
                    "target_minutes": int(target),
                    "snapshot_at": snapshot_at,
                    "minutes_to_deadline": round(float(minutes_to_deadline), 4),
                    "absolute_target_error_minutes": round(float(target_error), 4),
                    "combo_count": int(combo_count),
                    "source": source,
                    "complete_ticket_odds": len(complete_rows) == len(probability),
                    "ticket_count": len(probability),
                    "positive_ev_ticket_count": sum(
                        (item["expected_value"] or 0) >= 1.0 for item in complete_rows
                    ),
                    "selected_probability_pct": round(
                        sum(probability.values()) * 100.0, 6
                    ),
                    "portfolio_expected_roi_pct": (
                        round(expected_value_sum / len(probability) * 100.0, 4)
                        if len(complete_rows) == len(probability) and probability
                        else None
                    ),
                    "synthetic_odds": (
                        round(1.0 / inverse_selected, 4)
                        if len(complete_rows) == len(probability) and inverse_selected > 0
                        else None
                    ),
                    "tickets": ticket_rows,
                }
    except (sqlite3.Error, OSError, ValueError):
        return False
    available_targets = sorted(shadow.get("snapshots") or {})
    shadow["available_targets"] = available_targets
    shadow["status"] = "odds_ready" if {"t10", "t5"} <= set(available_targets) else (
        "partial_odds" if available_targets else "awaiting_target_odds"
    )
    shadow["odds_db"] = str(odds_path)
    return before != json.dumps(shadow, sort_keys=True, ensure_ascii=False)


TICKET_EV_SHADOW_KEYS = (
    "ticket_ev_shadow",
    "ticket_position_shadow",
    "ticket_venue_probability_shadow",
)

TICKET_STRATEGY_SHADOW_ID = "ticket_strategy_compare_v1"
TICKET_STRATEGY_SHADOW_VERSION = "venue-sign-ticket-strategy-shadow-v1"
TICKET_STRATEGY_MIN_EXPECTED_VALUE = 1.0
TICKET_STRATEGY_MAX_RESCUE_POINTS = 12
TICKET_STRATEGY_MIN_DECISION_SAMPLE = 100
TICKET_STRATEGY_VARIANTS = (
    ("baseline_current", "現行買い目"),
    ("ev_pruned", "期待値100%未満を除外"),
    ("rescue12", "高確率救済を最大12点まで追加"),
)


def ticket_strategy_shadow_policy():
    return {
        "version": TICKET_STRATEGY_SHADOW_VERSION,
        "policy_id": TICKET_STRATEGY_SHADOW_ID,
        "active": False,
        "notification_enabled": False,
        "production_action": "none",
        "target_preference": ["t5", "t10"],
        "minimum_ticket_expected_roi_pct": round(
            TICKET_STRATEGY_MIN_EXPECTED_VALUE * 100.0,
            1,
        ),
        "rescue_max_points": TICKET_STRATEGY_MAX_RESCUE_POINTS,
        "minimum_forward_sample": TICKET_STRATEGY_MIN_DECISION_SAMPLE,
        "description": (
            "現行買い目は変えず、現行・期待値不足除外・最大12点救済の3案を"
            "同じ締切前確率、T-10/T-5オッズ、実結果で比較する。"
        ),
    }


def load_target_odds_snapshot(entry, target, odds_db=None):
    """Load one complete pre-race 120-combination odds snapshot."""
    odds_path = Path(odds_db) if odds_db else default_trifecta_odds_db()
    if not odds_path.exists():
        return None
    date_text = str((entry or {}).get("date") or "")
    round_no = int(as_num((entry or {}).get("round")) or 0)
    if not date_text or round_no <= 0:
        return None
    try:
        with closing(sqlite3.connect(f"file:{odds_path}?mode=ro", uri=True)) as con:
            tables = {
                str(row[0])
                for row in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if not {"odds_target_capture", "odds_trifecta"} <= tables:
                return None
            for venue_code in venue_code_candidates(entry):
                capture = con.execute(
                    """
                    SELECT target_minutes, snapshot_at, minutes_to_deadline,
                           absolute_target_error_minutes, combo_count, source
                    FROM odds_target_capture
                    WHERE date=? AND CAST(venue_code AS INTEGER)=? AND race_no=?
                      AND CAST(target_minutes AS INTEGER)=?
                      AND status='complete' AND combo_count>=120
                    ORDER BY absolute_target_error_minutes ASC, snapshot_at DESC
                    LIMIT 1
                    """,
                    (
                        date_text,
                        int(float(venue_code)),
                        round_no,
                        int(target),
                    ),
                ).fetchone()
                if not capture:
                    continue
                target_minutes, snapshot_at, minutes_to_deadline, target_error, combo_count, source = capture
                odds_rows = con.execute(
                    """
                    SELECT combo, odds FROM odds_trifecta
                    WHERE date=? AND CAST(venue_code AS INTEGER)=? AND race_no=?
                      AND snapshot_at=?
                    """,
                    (
                        date_text,
                        int(float(venue_code)),
                        round_no,
                        snapshot_at,
                    ),
                ).fetchall()
                odds = {
                    norm_combo(combo): float(value)
                    for combo, value in odds_rows
                    if len(norm_combo(combo)) == 3 and (as_num(value) or 0) > 0
                }
                if len(odds) < 120:
                    continue
                return {
                    "target": f"t{int(target_minutes)}",
                    "target_minutes": int(target_minutes),
                    "snapshot_at": snapshot_at,
                    "minutes_to_deadline": round(float(minutes_to_deadline), 4),
                    "absolute_target_error_minutes": round(float(target_error), 4),
                    "combo_count": int(combo_count),
                    "source": source,
                    "odds_db": str(odds_path),
                    "complete_all_combos": True,
                    "odds": odds,
                }
    except (sqlite3.Error, OSError, TypeError, ValueError):
        return None
    return None


def target_ticket_odds_from_shadows(entry):
    """Fallback to ticket-only odds already frozen in the forward log."""
    for target in ("t5", "t10"):
        odds = {}
        snapshots = []
        for shadow_key in TICKET_EV_SHADOW_KEYS:
            snapshot = (
                ((entry or {}).get(shadow_key) or {}).get("snapshots") or {}
            ).get(target)
            if not isinstance(snapshot, dict):
                continue
            snapshots.append(snapshot)
            for item in snapshot.get("tickets") or []:
                combo = norm_combo(item.get("ticket") or item.get("combo"))
                value = as_num(item.get("odds"))
                if len(combo) == 3 and value is not None and value > 0:
                    odds[combo] = float(value)
        if odds:
            snapshot = snapshots[0]
            return {
                "target": target,
                "target_minutes": int(target[1:]),
                "snapshot_at": snapshot.get("snapshot_at"),
                "minutes_to_deadline": snapshot.get("minutes_to_deadline"),
                "absolute_target_error_minutes": snapshot.get(
                    "absolute_target_error_minutes"
                ),
                "combo_count": len(odds),
                "source": snapshot.get("source") or "frozen_ticket_snapshot",
                "odds_db": None,
                "complete_all_combos": len(odds) >= 120,
                "odds": odds,
            }
    return None


def all_ticket_probabilities_from_position_shadow(shadow):
    """Rebuild the frozen role model's 120 sequential probabilities."""
    position_values = (shadow or {}).get("position_probabilities_pct") or {}
    roles = {}
    for position in (1, 2, 3):
        raw = position_values.get(f"position{position}") or {}
        values = {
            boat: float(as_num(raw.get(str(boat))) or 0.0) / 100.0
            for boat in range(1, 7)
        }
        total = sum(values.values())
        if total <= 0 or any(value <= 0 for value in values.values()):
            return {}
        roles[position] = {
            boat: value / total for boat, value in values.items()
        }
    probabilities = {}
    boats = tuple(range(1, 7))
    for first, second, third in itertools.permutations(boats, 3):
        second_total = sum(roles[2][boat] for boat in boats if boat != first)
        third_total = sum(
            roles[3][boat] for boat in boats if boat not in {first, second}
        )
        probabilities[f"{first}{second}{third}"] = (
            roles[1][first]
            * roles[2][second]
            / second_total
            * roles[3][third]
            / third_total
        )
    total = sum(probabilities.values())
    if total <= 0:
        return {}
    return {combo: value / total for combo, value in probabilities.items()}


def ticket_probability_models(entry):
    selected = {}
    full = {}
    model_labels = {
        "ticket_ev_shadow": "base_ai",
        "ticket_position_shadow": "position_ai",
        "ticket_venue_probability_shadow": "venue_probability_ai",
    }
    for shadow_key, label in model_labels.items():
        shadow = (entry or {}).get(shadow_key)
        if not isinstance(shadow, dict) or shadow.get("status") == "unavailable":
            continue
        ticket_values = {
            norm_combo(item.get("ticket") or item.get("combo")): float(
                as_num(item.get("probability")) or 0.0
            )
            for item in shadow.get("tickets") or []
            if len(norm_combo(item.get("ticket") or item.get("combo"))) == 3
            and (as_num(item.get("probability")) or 0) > 0
        }
        if ticket_values:
            selected[label] = ticket_values
        if shadow_key != "ticket_ev_shadow":
            all_values = all_ticket_probabilities_from_position_shadow(shadow)
            if all_values:
                full[label] = all_values
                selected[label] = all_values
    return selected, full


def composite_ticket_probability(combo, probability_models):
    values = [
        float(model[combo])
        for model in probability_models.values()
        if combo in model and model[combo] > 0
    ]
    if not values:
        return None
    return sum(values) / len(values)


def ticket_strategy_variant(variant_id, label, combos, probability_models, odds):
    rows = []
    for combo in combos:
        probability = composite_ticket_probability(combo, probability_models)
        value = odds.get(combo)
        expected_value = probability * value if probability is not None and value else None
        sources = [
            model_id
            for model_id, model in probability_models.items()
            if combo in model and model[combo] > 0
        ]
        rows.append(
            {
                "ticket": fmt_ticket(combo),
                "combo": combo,
                "probability_pct": (
                    round(probability * 100.0, 6)
                    if probability is not None
                    else None
                ),
                "odds": round(float(value), 2) if value else None,
                "expected_value": (
                    round(expected_value, 6)
                    if expected_value is not None
                    else None
                ),
                "expected_roi_pct": (
                    round(expected_value * 100.0, 4)
                    if expected_value is not None
                    else None
                ),
                "probability_models": sources,
                "probability_model_count": len(sources),
            }
        )
    complete = [
        row for row in rows if row.get("expected_value") is not None
    ]
    inverse_selected = sum(
        1.0 / float(row["odds"])
        for row in complete
        if (row.get("odds") or 0) > 0
    )
    return {
        "variant_id": variant_id,
        "label": label,
        "active": False,
        "notification_enabled": False,
        "production_action": "none",
        "status": "ready" if combos else "skip",
        "points": len(combos),
        "tickets": [fmt_ticket(combo) for combo in combos],
        "ticket_details": rows,
        "complete_ticket_evidence": len(complete) == len(combos),
        "selected_probability_pct": (
            round(
                sum(
                    float(row["probability_pct"] or 0.0) for row in rows
                ),
                6,
            )
            if rows
            else 0.0
        ),
        "portfolio_expected_roi_pct": (
            round(
                sum(float(row["expected_value"]) for row in complete)
                / len(combos)
                * 100.0,
                4,
            )
            if combos and len(complete) == len(combos)
            else None
        ),
        "synthetic_odds": (
            round(1.0 / inverse_selected, 4) if inverse_selected > 0 else None
        ),
    }


def update_ticket_strategy_shadow(entry, odds_db=None, updated_at=None):
    """Freeze three ticket strategies without changing production tickets."""
    current = entry.get("ticket_strategy_shadow")
    if isinstance(current, dict) and current.get("status") == "settled":
        return False
    baseline_combos = [
        combo
        for combo in (norm_combo(ticket) for ticket in entry.get("tickets") or [])
        if len(combo) == 3
    ]
    selected_models, full_models = ticket_probability_models(entry)
    odds_snapshot = None
    for target in (5, 10):
        odds_snapshot = load_target_odds_snapshot(
            entry,
            target,
            odds_db=odds_db,
        )
        if odds_snapshot:
            break
    if isinstance(current, dict) and current.get("status") == "ready":
        current_target = current.get("selected_target")
        candidate_target = (odds_snapshot or {}).get("target")
        if (
            odds_snapshot is None
            or current_target == "t5"
            or candidate_target == current_target
        ):
            return False
    if odds_snapshot is None:
        odds_snapshot = target_ticket_odds_from_shadows(entry)

    policy = ticket_strategy_shadow_policy()
    base_payload = {
        **policy,
        "baseline_tickets": [fmt_ticket(combo) for combo in baseline_combos],
        "baseline_points": len(baseline_combos),
        "pre_race_input_only": True,
        "construction_mode": (
            "pre_race_input_backfill"
            if entry.get("status") in {"hit", "miss"}
            else "live_pre_race"
        ),
        "constructed_at": (
            (current or {}).get("constructed_at")
            or updated_at
        ),
    }
    if not selected_models:
        proposed = {
            **base_payload,
            "status": "unavailable",
            "reason": "frozen_probability_models_missing",
            "variants": {},
        }
    elif not odds_snapshot:
        proposed = {
            **base_payload,
            "status": "awaiting_target_odds",
            "reason": "t10_t5_odds_missing",
            "probability_models": sorted(selected_models),
            "full_probability_models": sorted(full_models),
            "variants": {},
        }
    else:
        odds = odds_snapshot["odds"]
        baseline_variant = ticket_strategy_variant(
            "baseline_current",
            "現行買い目",
            baseline_combos,
            selected_models,
            odds,
        )
        retained = [
            row["combo"]
            for row in baseline_variant["ticket_details"]
            if (as_num(row.get("expected_value")) or 0)
            >= TICKET_STRATEGY_MIN_EXPECTED_VALUE
        ]
        pruned_variant = ticket_strategy_variant(
            "ev_pruned",
            "期待値100%未満を除外",
            retained,
            selected_models,
            odds,
        )
        pruned_variant["removed_tickets"] = [
            fmt_ticket(combo) for combo in baseline_combos if combo not in retained
        ]
        pruned_variant["removed_count"] = len(baseline_combos) - len(retained)

        rescue_candidates = []
        if odds_snapshot.get("complete_all_combos") and full_models:
            for combo, value in odds.items():
                if combo in baseline_combos:
                    continue
                probability = composite_ticket_probability(combo, full_models)
                if probability is None:
                    continue
                expected_value = probability * float(value)
                if expected_value < TICKET_STRATEGY_MIN_EXPECTED_VALUE:
                    continue
                rescue_candidates.append(
                    {
                        "combo": combo,
                        "probability": probability,
                        "expected_value": expected_value,
                        "model_count": sum(
                            combo in model for model in full_models.values()
                        ),
                    }
                )
        rescue_candidates.sort(
            key=lambda item: (
                -item["probability"],
                -item["model_count"],
                -item["expected_value"],
                item["combo"],
            )
        )
        add_count = max(
            0,
            TICKET_STRATEGY_MAX_RESCUE_POINTS - len(baseline_combos),
        )
        added = [item["combo"] for item in rescue_candidates[:add_count]]
        rescue_variant = ticket_strategy_variant(
            "rescue12",
            "高確率救済を最大12点まで追加",
            baseline_combos + added,
            {**selected_models, **full_models},
            odds,
        )
        rescue_variant["added_tickets"] = [fmt_ticket(combo) for combo in added]
        rescue_variant["added_count"] = len(added)
        rescue_variant["candidate_count"] = len(rescue_candidates)
        rescue_variant["rescue_available"] = bool(
            odds_snapshot.get("complete_all_combos") and full_models
        )

        proposed = {
            **base_payload,
            "status": "ready",
            "selected_target": odds_snapshot.get("target"),
            "odds_snapshot_at": odds_snapshot.get("snapshot_at"),
            "minutes_to_deadline": odds_snapshot.get("minutes_to_deadline"),
            "absolute_target_error_minutes": odds_snapshot.get(
                "absolute_target_error_minutes"
            ),
            "odds_source": odds_snapshot.get("source"),
            "complete_all_combo_odds": bool(
                odds_snapshot.get("complete_all_combos")
            ),
            "probability_models": sorted(selected_models),
            "full_probability_models": sorted(full_models),
            "variants": {
                "baseline_current": baseline_variant,
                "ev_pruned": pruned_variant,
                "rescue12": rescue_variant,
            },
        }
    before = json.dumps(current or {}, sort_keys=True, ensure_ascii=False)
    after = json.dumps(proposed, sort_keys=True, ensure_ascii=False)
    if before == after:
        return False
    entry["ticket_strategy_shadow"] = proposed
    return True


def update_ticket_strategy_shadow_result(entry, result, settled_at):
    shadow = entry.get("ticket_strategy_shadow")
    if not isinstance(shadow, dict) or shadow.get("status") not in {"ready", "settled"}:
        return False
    result_combo = result.get("trifecta_norm") or norm_combo(result.get("trifecta"))
    if len(result_combo) != 3:
        return False
    payout = int(as_num(result.get("payout_yen")) or 0)
    before = json.dumps(shadow, sort_keys=True, ensure_ascii=False)
    for variant in (shadow.get("variants") or {}).values():
        tickets = {
            norm_combo(ticket) for ticket in variant.get("tickets") or []
        }
        points = int(as_num(variant.get("points")) or len(tickets))
        hit = bool(points and result_combo in tickets)
        investment = points * 100
        payback = payout if hit else 0
        variant.update(
            {
                "status": "settled",
                "settled_at": variant.get("settled_at") or settled_at,
                "result_trifecta": result.get("trifecta") or fmt_ticket(result_combo),
                "result_payout_yen": payout,
                "bet": points > 0,
                "hit": hit if points > 0 else None,
                "outcome": "hit" if hit else ("miss" if points > 0 else "skip"),
                "investment_yen": investment,
                "payback_yen": payback,
                "profit_yen": payback - investment,
            }
        )
    shadow.update(
        {
            "status": "settled",
            "settled_at": shadow.get("settled_at") or settled_at,
            "result_trifecta": result.get("trifecta") or fmt_ticket(result_combo),
            "result_payout_yen": payout,
        }
    )
    return before != json.dumps(shadow, sort_keys=True, ensure_ascii=False)


def build_ticket_ev_shadows(rows, tickets):
    """Freeze the three pre-race ticket models used by every 24-venue sign."""
    shadows = {
        "ticket_ev_shadow": original_boaters_forward.evaluate_ticket_ev_shadow(
            rows,
            tickets,
        ),
        "ticket_position_shadow": (
            original_boaters_forward.evaluate_ticket_position_shadow(rows, tickets)
        ),
        "ticket_venue_probability_shadow": (
            original_boaters_forward.evaluate_ticket_venue_probability_shadow(
                rows,
                tickets,
            )
        ),
    }
    labels = {
        "ticket_ev_shadow": "基礎AI",
        "ticket_position_shadow": "着順AI",
        "ticket_venue_probability_shadow": "場別補正AI",
    }
    for key, shadow in shadows.items():
        if isinstance(shadow, dict):
            shadow["model_label"] = labels[key]
    return shadows


def refresh_ticket_ev_shadows(entry, odds_db=None):
    changed = False
    for shadow_key in TICKET_EV_SHADOW_KEYS:
        changed = (
            update_original_boaters_ticket_ev_from_target_odds(
                entry,
                odds_db=odds_db,
                shadow_key=shadow_key,
            )
            or changed
        )
    return changed


def backfill_ticket_ev_shadow_from_self_ai(entry):
    """Recover the base ticket model for older core entries with a frozen self-AI snapshot."""
    if isinstance(entry.get("ticket_ev_shadow"), dict):
        return False
    snapshot = entry.get("self_ai_snapshot") or {}
    per_boat = snapshot.get("per_boat") or []
    rows = []
    for item in per_boat:
        boat = int(as_num(item.get("boat_number")) or 0)
        win_pct = as_num(item.get("win_pct"))
        top3_pct = as_num(item.get("top3_pct"))
        if boat not in range(1, 7) or win_pct is None or top3_pct is None:
            continue
        rows.append(
            {
                "boat_number": boat,
                "ai_prediction_pct": win_pct,
                "ai_3ren_pct": top3_pct,
            }
        )
    if {row["boat_number"] for row in rows} != set(range(1, 7)):
        return False
    shadow = original_boaters_forward.evaluate_ticket_ev_shadow(
        rows,
        entry.get("tickets") or [],
    )
    if shadow.get("status") == "unavailable":
        return False
    shadow["model_label"] = "独自AI"
    shadow["probability_source"] = "frozen_self_ai_snapshot"
    entry["ticket_ev_shadow"] = shadow
    return True


def update_original_boaters_low_confidence_odds(entry):
    shadow = entry.get("low_confidence_shadow")
    if (
        not isinstance(shadow, dict)
        or shadow.get("status") in {"unavailable", "settled"}
    ):
        return False
    before = json.dumps(shadow, sort_keys=True, ensure_ascii=False)
    legacy = entry.get("ticket_ev_shadow") or {}
    position = entry.get("ticket_position_shadow") or {}

    def snapshot_metric(source, target, key):
        snapshot = (source.get("snapshots") or {}).get(target) or {}
        return as_num(snapshot.get(key))

    evidence = {
        "legacy_t10_expected_roi_pct": snapshot_metric(
            legacy, "t10", "portfolio_expected_roi_pct"
        ),
        "legacy_t5_expected_roi_pct": snapshot_metric(
            legacy, "t5", "portfolio_expected_roi_pct"
        ),
        "position_t10_expected_roi_pct": snapshot_metric(
            position, "t10", "portfolio_expected_roi_pct"
        ),
        "position_t5_expected_roi_pct": snapshot_metric(
            position, "t5", "portfolio_expected_roi_pct"
        ),
        "t10_synthetic_odds": snapshot_metric(legacy, "t10", "synthetic_odds"),
        "t5_synthetic_odds": snapshot_metric(legacy, "t5", "synthetic_odds"),
        "legacy_t5_positive_ev_ticket_count": snapshot_metric(
            legacy, "t5", "positive_ev_ticket_count"
        ),
        "position_t5_positive_ev_ticket_count": snapshot_metric(
            position, "t5", "positive_ev_ticket_count"
        ),
    }
    if evidence["legacy_t10_expected_roi_pct"] is not None and evidence[
        "legacy_t5_expected_roi_pct"
    ] is not None:
        evidence["legacy_ev_change_t5_minus_t10_pp"] = round(
            evidence["legacy_t5_expected_roi_pct"]
            - evidence["legacy_t10_expected_roi_pct"],
            4,
        )
    else:
        evidence["legacy_ev_change_t5_minus_t10_pp"] = None
    t10_synthetic = evidence["t10_synthetic_odds"]
    t5_synthetic = evidence["t5_synthetic_odds"]
    evidence["synthetic_odds_change_pct"] = (
        round((t5_synthetic / t10_synthetic - 1.0) * 100.0, 4)
        if t10_synthetic is not None
        and t5_synthetic is not None
        and t10_synthetic > 0
        else None
    )
    shadow["odds_evidence"] = evidence
    candidates = shadow.setdefault("candidate_decisions", {})

    def set_decision(candidate_id, available, would_skip):
        decision = candidates.setdefault(candidate_id, {})
        decision["decision_available"] = bool(available)
        decision["would_skip"] = bool(would_skip) if available else None
        decision["production_eligible"] = False

    legacy_t5 = evidence["legacy_t5_expected_roi_pct"]
    position_t5 = evidence["position_t5_expected_roi_pct"]
    both_t5 = legacy_t5 is not None and position_t5 is not None
    set_decision(
        "both_models_ev_below100_t5",
        both_t5,
        both_t5 and legacy_t5 < 100.0 and position_t5 < 100.0,
    )
    set_decision(
        "both_models_ev_below80_t5",
        both_t5,
        both_t5 and legacy_t5 < 80.0 and position_t5 < 80.0,
    )
    ev_change = evidence["legacy_ev_change_t5_minus_t10_pp"]
    set_decision(
        "legacy_ev_drop20_t10_to_t5",
        ev_change is not None,
        ev_change is not None and ev_change <= -20.0,
    )
    set_decision(
        "synthetic_odds_below3_t5",
        t5_synthetic is not None,
        t5_synthetic is not None and t5_synthetic < SYNTHETIC_ODDS_MIN,
    )
    synthetic_change = evidence["synthetic_odds_change_pct"]
    set_decision(
        "synthetic_odds_drop20pct_t10_to_t5",
        synthetic_change is not None,
        synthetic_change is not None and synthetic_change <= -20.0,
    )
    structural = candidates.get("structural_support_gap2") or {}
    set_decision(
        "structural_and_both_models_ev_below100",
        bool(structural.get("decision_available")) and both_t5,
        bool(structural.get("would_skip"))
        and both_t5
        and legacy_t5 < 100.0
        and position_t5 < 100.0,
    )
    if both_t5:
        shadow["status"] = "odds_ready"
    elif any(value is not None for value in evidence.values()):
        shadow["status"] = "partial_odds"
    else:
        shadow["status"] = "awaiting_target_odds"
    return before != json.dumps(shadow, sort_keys=True, ensure_ascii=False)


def update_original_boaters_ticket_ev_result(
    entry,
    result,
    settled_at,
    shadow_key="ticket_ev_shadow",
):
    shadow = entry.get(shadow_key)
    if not isinstance(shadow, dict) or shadow.get("status") == "unavailable" or not result:
        return False
    before = json.dumps(shadow, sort_keys=True, ensure_ascii=False)
    result_combo = result.get("trifecta_norm") or norm_combo(result.get("trifecta"))
    ticket_probability = {
        norm_combo(item.get("ticket") or item.get("combo")): as_num(item.get("probability_pct"))
        for item in shadow.get("tickets") or []
    }
    shadow.update(
        {
            "status": "settled",
            "settled_at": settled_at,
            "result_trifecta": result.get("trifecta"),
            "result_payout_yen": int(result.get("payout_yen") or 0),
            "baseline_hit": result_combo in ticket_probability,
            "result_ticket_probability_pct": ticket_probability.get(result_combo),
        }
    )
    return before != json.dumps(shadow, sort_keys=True, ensure_ascii=False)


def update_original_boaters_head_swap_result(entry, result, settled_at):
    shadow = entry.get("head_swap_shadow")
    if not isinstance(shadow, dict) or not shadow.get("active") or not result:
        return False
    before = json.dumps(shadow, sort_keys=True, ensure_ascii=False)
    result_combo = result.get("trifecta_norm") or norm_combo(result.get("trifecta"))
    baseline_tickets = {norm_combo(ticket) for ticket in entry.get("tickets") or []}
    shadow_tickets = {norm_combo(ticket) for ticket in shadow.get("shadow_tickets") or []}
    baseline_hit = bool(result_combo and result_combo in baseline_tickets)
    shadow_hit = bool(result_combo and result_combo in shadow_tickets)
    winner = int(result_combo[0]) if result_combo and result_combo[0].isdigit() else None
    baseline_heads = {
        int(value)
        for value in shadow.get("baseline_intended_heads") or []
        if str(value).isdigit()
    }
    candidate_heads = {
        int(value)
        for value in shadow.get("shadow_heads") or []
        if str(value).isdigit()
    }
    head_rescued = bool(
        winner is not None and winner in candidate_heads and winner not in baseline_heads
    )
    head_lost = bool(
        winner is not None and winner in baseline_heads and winner not in candidate_heads
    )
    ticket_rescued = shadow_hit and not baseline_hit
    ticket_lost = baseline_hit and not shadow_hit
    if ticket_rescued:
        outcome = "ticket_rescued"
    elif ticket_lost:
        outcome = "ticket_lost"
    elif head_rescued:
        outcome = "head_rescued_only"
    elif head_lost:
        outcome = "head_lost_only"
    else:
        outcome = "no_effect"
    points = int(as_num(entry.get("points")) or len(shadow_tickets) or 0)
    investment_yen = points * 100
    payout_yen = int(result.get("payout_yen") or 0)
    shadow_payback_yen = payout_yen if shadow_hit else 0
    shadow.update(
        {
            "status": "settled",
            "settled_at": settled_at,
            "result_trifecta": result.get("trifecta"),
            "result_payout_yen": payout_yen,
            "winner": winner,
            "baseline_hit": baseline_hit,
            "shadow_hit": shadow_hit,
            "head_rescued": head_rescued,
            "head_lost": head_lost,
            "ticket_rescued": ticket_rescued,
            "ticket_lost": ticket_lost,
            "outcome": outcome,
            "shadow_investment_yen": investment_yen,
            "shadow_payback_yen": shadow_payback_yen,
            "shadow_profit_yen": shadow_payback_yen - investment_yen,
        }
    )
    return before != json.dumps(shadow, sort_keys=True, ensure_ascii=False)


def update_new_buff_debuff_skip_result(entry, result, settled_at):
    shadow = entry.get("new_buff_debuff_shadow")
    if not isinstance(shadow, dict) or shadow.get("status") == "unavailable" or not result:
        return False
    before = json.dumps(shadow, sort_keys=True, ensure_ascii=False)
    result_combo = result.get("trifecta_norm") or norm_combo(result.get("trifecta"))
    tickets = {norm_combo(ticket) for ticket in entry.get("tickets") or []}
    baseline_hit = bool(result_combo and result_combo in tickets)
    would_skip = bool(shadow.get("would_skip"))
    shadow_buy = not would_skip
    shadow_hit = shadow_buy and baseline_hit
    points = int(as_num(entry.get("points")) or len(tickets) or 0)
    baseline_investment = points * 100
    shadow_investment = baseline_investment if shadow_buy else 0
    payout_yen = int(result.get("payout_yen") or 0)
    shadow_payback = payout_yen if shadow_hit else 0
    if would_skip and baseline_hit:
        outcome = "skipped_hit"
    elif would_skip:
        outcome = "avoided_loss"
    elif baseline_hit:
        outcome = "bought_hit"
    else:
        outcome = "bought_miss"
    shadow.update(
        {
            "status": "settled",
            "settled_at": settled_at,
            "result_trifecta": result.get("trifecta"),
            "result_payout_yen": payout_yen,
            "baseline_hit": baseline_hit,
            "shadow_buy": shadow_buy,
            "shadow_hit": shadow_hit,
            "outcome": outcome,
            "baseline_investment_yen": baseline_investment,
            "baseline_payback_yen": payout_yen if baseline_hit else 0,
            "shadow_investment_yen": shadow_investment,
            "shadow_payback_yen": shadow_payback,
            "shadow_profit_yen": shadow_payback - shadow_investment,
        }
    )
    return before != json.dumps(shadow, sort_keys=True, ensure_ascii=False)


def update_head56_confidence_skip_result(entry, result, settled_at):
    shadow = entry.get("head56_confidence_shadow")
    if not isinstance(shadow, dict) or not shadow.get("active") or not result:
        return False
    before = json.dumps(shadow, sort_keys=True, ensure_ascii=False)
    result_combo = result.get("trifecta_norm") or norm_combo(result.get("trifecta"))
    tickets = {norm_combo(ticket) for ticket in entry.get("tickets") or []}
    baseline_hit = bool(result_combo and result_combo in tickets)
    points = int(as_num(entry.get("points")) or len(tickets) or 0)
    baseline_investment = points * 100
    payout_yen = int(result.get("payout_yen") or 0)
    outcome = "skipped_hit" if baseline_hit else "avoided_loss"
    shadow.update(
        {
            "status": "settled",
            "settled_at": settled_at,
            "result_trifecta": result.get("trifecta"),
            "result_payout_yen": payout_yen,
            "baseline_hit": baseline_hit,
            "shadow_buy": False,
            "shadow_hit": False,
            "outcome": outcome,
            "baseline_investment_yen": baseline_investment,
            "baseline_payback_yen": payout_yen if baseline_hit else 0,
            "shadow_investment_yen": 0,
            "shadow_payback_yen": 0,
            "shadow_profit_yen": 0,
        }
    )
    return before != json.dumps(shadow, sort_keys=True, ensure_ascii=False)


def update_low_confidence_shadow_result(entry, result, settled_at):
    shadow = entry.get("low_confidence_shadow")
    if (
        not isinstance(shadow, dict)
        or shadow.get("status") == "unavailable"
        or not result
    ):
        return False
    before = json.dumps(shadow, sort_keys=True, ensure_ascii=False)
    result_combo = result.get("trifecta_norm") or norm_combo(result.get("trifecta"))
    tickets = {norm_combo(ticket) for ticket in entry.get("tickets") or []}
    baseline_hit = bool(result_combo and result_combo in tickets)
    points = int(as_num(entry.get("points")) or len(tickets) or 0)
    investment = points * 100
    payout = int(result.get("payout_yen") or 0)
    for decision in (shadow.get("candidate_decisions") or {}).values():
        if not isinstance(decision, dict) or not decision.get("decision_available"):
            continue
        would_skip = bool(decision.get("would_skip"))
        shadow_buy = not would_skip
        shadow_hit = shadow_buy and baseline_hit
        shadow_investment = investment if shadow_buy else 0
        shadow_payback = payout if shadow_hit else 0
        if would_skip and baseline_hit:
            outcome = "skipped_hit"
        elif would_skip:
            outcome = "avoided_loss"
        elif baseline_hit:
            outcome = "bought_hit"
        else:
            outcome = "bought_miss"
        decision.update(
            {
                "status": "settled",
                "shadow_buy": shadow_buy,
                "shadow_hit": shadow_hit,
                "outcome": outcome,
                "baseline_investment_yen": investment,
                "baseline_payback_yen": payout if baseline_hit else 0,
                "shadow_investment_yen": shadow_investment,
                "shadow_payback_yen": shadow_payback,
                "shadow_profit_yen": shadow_payback - shadow_investment,
            }
        )
    shadow.update(
        {
            "status": "settled",
            "settled_at": settled_at,
            "result_trifecta": result.get("trifecta"),
            "result_payout_yen": payout,
            "baseline_hit": baseline_hit,
        }
    )
    return before != json.dumps(shadow, sort_keys=True, ensure_ascii=False)


def update_forward_entry_result(entry, result, settled_at):
    if not result:
        entry.setdefault("status", "pending_result")
        return False
    tickets = {norm_combo(ticket) for ticket in entry.get("tickets") or []}
    result_combo = result.get("trifecta_norm") or norm_combo(result.get("trifecta"))
    hit = bool(result_combo and result_combo in tickets)
    points = int(as_num(entry.get("points")) or len(tickets) or 0)
    investment_yen = points * 100
    payback_yen = int(result.get("payout_yen") or 0) if hit else 0
    before = json.dumps(
        {
            "status": entry.get("status"),
            "result_trifecta": entry.get("result_trifecta"),
            "result_payout_yen": entry.get("result_payout_yen"),
            "hit": entry.get("hit"),
            "payback_yen": entry.get("payback_yen"),
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    entry.update(
        {
            "status": "hit" if hit else "miss",
            "settled_at": settled_at,
            "result": {
                "trifecta": result.get("trifecta"),
                "payout_yen": result.get("payout_yen"),
                "manshu": bool(result.get("manshu")),
            },
            "result_trifecta": result.get("trifecta"),
            "result_payout_yen": result.get("payout_yen"),
            "result_manshu": bool(result.get("manshu")),
            "hit": hit,
            "payback_yen": payback_yen,
            "investment_yen": investment_yen,
            "profit_yen": payback_yen - investment_yen,
        }
    )
    shadow_changed = update_original_boaters_head_swap_result(entry, result, settled_at)
    buff_debuff_shadow_changed = update_new_buff_debuff_skip_result(
        entry,
        result,
        settled_at,
    )
    head56_confidence_shadow_changed = update_head56_confidence_skip_result(
        entry,
        result,
        settled_at,
    )
    ticket_ev_shadow_changed = update_original_boaters_ticket_ev_result(
        entry,
        result,
        settled_at,
    )
    ticket_position_shadow_changed = update_original_boaters_ticket_ev_result(
        entry,
        result,
        settled_at,
        shadow_key="ticket_position_shadow",
    )
    ticket_venue_probability_shadow_changed = update_original_boaters_ticket_ev_result(
        entry,
        result,
        settled_at,
        shadow_key="ticket_venue_probability_shadow",
    )
    ticket_strategy_shadow_changed = update_ticket_strategy_shadow_result(
        entry,
        result,
        settled_at,
    )
    low_confidence_shadow_changed = update_low_confidence_shadow_result(
        entry,
        result,
        settled_at,
    )
    after = json.dumps(
        {
            "status": entry.get("status"),
            "result_trifecta": entry.get("result_trifecta"),
            "result_payout_yen": entry.get("result_payout_yen"),
            "hit": entry.get("hit"),
            "payback_yen": entry.get("payback_yen"),
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return (
        before != after
        or shadow_changed
        or buff_debuff_shadow_changed
        or head56_confidence_shadow_changed
        or ticket_ev_shadow_changed
        or ticket_position_shadow_changed
        or ticket_venue_probability_shadow_changed
        or ticket_strategy_shadow_changed
        or low_confidence_shadow_changed
    )


def forward_validation_summary(entries):
    settled = [entry for entry in entries if entry.get("status") in {"hit", "miss"}]
    hits = [entry for entry in settled if entry.get("hit")]
    investment = sum(as_int(entry.get("investment_yen")) or 0 for entry in settled)
    payback = sum(as_int(entry.get("payback_yen")) or 0 for entry in settled)
    return {
        "entry_count": len(entries),
        "pending_count": sum(1 for entry in entries if entry.get("status") == "pending_result"),
        "settled_count": len(settled),
        "hit_count": len(hits),
        "miss_count": len(settled) - len(hits),
        "hit_rate_pct": round(len(hits) / len(settled) * 100, 2) if settled else None,
        "investment_yen": investment,
        "payback_yen": payback,
        "profit_yen": payback - investment if settled else None,
        "roi_pct": round(payback / investment * 100, 2) if investment else None,
        "notification_sent_count": sum(1 for entry in entries if entry.get("notification_status") == "sent"),
        "notification_duplicate_sent_count": sum(1 for entry in entries if entry.get("notification_status") == "duplicate_sent"),
        "notification_failed_count": sum(1 for entry in entries if entry.get("notification_status") == "failed"),
        "notification_pending_count": sum(1 for entry in entries if entry.get("notification_status") == "pending"),
    }


def apply_forward_notification_status(entries, monitor_payload, now):
    push = (monitor_payload or {}).get("push")
    changed = False
    if not isinstance(push, dict):
        for entry in entries:
            if entry.get("push_key") and entry.get("notification_status") in {None, "pending"}:
                before = entry.get("notification_status")
                entry["notification_status"] = "no_push"
                entry["notification_ok"] = None
                entry["notification_error"] = "--no-push"
                changed = changed or before != entry.get("notification_status")
        return changed
    results_by_key = {item.get("key"): item for item in push.get("results") or [] if item.get("key")}
    for entry in entries:
        push_key = entry.get("push_key")
        if not push_key:
            continue
        result = results_by_key.get(push_key)
        before = json.dumps(
            {
                "notification_status": entry.get("notification_status"),
                "notification_ok": entry.get("notification_ok"),
                "notification_error": entry.get("notification_error"),
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        if result:
            entry["notification_attempted_at"] = now.isoformat(timespec="seconds")
            entry["notification_status"] = "duplicate_sent" if result.get("skipped_duplicate") else ("sent" if result.get("ok") else "failed")
            entry["notification_ok"] = bool(result.get("ok"))
            entry["notification_http_status"] = result.get("status")
            if result.get("sent_at"):
                entry["notification_sent_at"] = result.get("sent_at")
            elif result.get("ok") and not entry.get("notification_sent_at"):
                entry["notification_sent_at"] = now.isoformat(timespec="seconds")
            entry["notification_error"] = result.get("error") or result.get("curl_error")
        elif entry.get("notification_status") in {None, "pending"} and not push.get("enabled", True):
            entry["notification_status"] = "not_configured"
            entry["notification_ok"] = False
            entry["notification_error"] = "ntfy_topic not configured"
        after = json.dumps(
            {
                "notification_status": entry.get("notification_status"),
                "notification_ok": entry.get("notification_ok"),
                "notification_error": entry.get("notification_error"),
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        changed = changed or before != after
    return changed


def update_forward_validation_log(date_text, monitor_payload, now):
    alerts = (monitor_payload or {}).get("alerts") or []
    core_alerts = [
        alert
        for alert in alerts
        if alert.get("alert_type") in {"buy_ok", "venue_sign"}
        and is_venue_sign_alert(alert)
        and alert.get("rule_set_id") != original_boaters_forward.RULE_SET_ID
    ]
    path = forward_validation_path(date_text)
    log_payload = load_json(
        path,
        {
            "version": "venue-sign-forward-v2",
            "date": date_text,
            "target_roi_pct": 150,
            "rule_id": "venue_sign_24",
            "entries": [],
        },
    )
    log_payload["version"] = "venue-sign-forward-v2"
    log_payload["rule_id"] = "venue_sign_24"
    log_payload["ticket_strategy_shadow_policy"] = ticket_strategy_shadow_policy()
    entries = log_payload.setdefault("entries", [])
    by_key = {forward_entry_key(entry.get("race_id"), entry.get("rule_id") or log_payload.get("rule_id")): entry for entry in entries}
    changed = False
    for alert in core_alerts:
        selection = alert.get("selection") or {}
        strategy = next(
            (
                item
                for item in alert.get("strategies") or []
                if is_venue_sign_strategy(item)
            ),
            {},
        )
        rule_id = strategy.get("strategy_id") or selection.get("strategy_id") or log_payload.get("rule_id")
        key = forward_entry_key(alert.get("race_id"), rule_id)
        tickets = selection.get("tickets") or strategy.get("tickets") or []
        entry = by_key.get(key)
        if entry is None:
            entry = {
                "status": "pending_result",
                "created_at": now.isoformat(timespec="seconds"),
                "detected_at": alert.get("detected_at") or now.isoformat(timespec="seconds"),
                "notification_status": "pending",
                "result": None,
            }
            entries.append(entry)
            by_key[key] = entry
            changed = True
        minutes_to_deadline = as_num(alert.get("minutes_to_deadline"))
        before = json.dumps(entry, sort_keys=True, ensure_ascii=False)
        entry.update(
            {
                "date": alert.get("date"),
                "race_id": alert.get("race_id"),
                "place_name": alert.get("place_name"),
                "round": alert.get("round"),
                "deadline_time": alert.get("deadline_time"),
                "source_type": alert.get("source_type"),
                "alert_type": alert.get("alert_type"),
                "last_seen_at": now.isoformat(timespec="seconds"),
                "last_detected_at": alert.get("detected_at") or now.isoformat(timespec="seconds"),
                "first_minutes_to_deadline": entry.get("first_minutes_to_deadline")
                if entry.get("first_minutes_to_deadline") is not None
                else (round(minutes_to_deadline, 1) if minutes_to_deadline is not None else None),
                "minutes_to_deadline": round(minutes_to_deadline, 1) if minutes_to_deadline is not None else None,
                "detected_before_deadline": bool(minutes_to_deadline is not None and minutes_to_deadline >= 0),
                "morning_rank": alert.get("morning_rank"),
                "live_rank": alert.get("live_rank"),
                "manshu_rate_pct": alert.get("manshu_rate_pct"),
                "rule_id": rule_id,
                "rule_label": strategy.get("label") or selection.get("label"),
                "heads": selection.get("heads") or strategy.get("heads") or [],
                "base_heads": selection.get("base_heads") or strategy.get("base_heads") or [],
                "axes": selection.get("axes") or strategy.get("axes") or [],
                "keshi": selection.get("keshi") or strategy.get("keshi"),
                "points": len(tickets),
                "tickets": tickets,
                "role_note": selection.get("role_note") or strategy.get("role_note"),
                "entry_checks": strategy.get("entry_checks") or selection.get("entry_checks") or [],
                "odds_filter": strategy.get("odds_filter") or selection.get("odds_filter"),
                "push_key": alert.get("push_key"),
                "self_ai_snapshot": (alert.get("metrics") or {}).get("self_ai"),
            }
        )
        for shadow_key in TICKET_EV_SHADOW_KEYS:
            incoming_shadow = alert.get(shadow_key)
            if (
                not isinstance(entry.get(shadow_key), dict)
                and isinstance(incoming_shadow, dict)
            ):
                entry[shadow_key] = json.loads(
                    json.dumps(incoming_shadow, ensure_ascii=False)
                )
        if alert.get("ev_risk_note"):
            entry["ev_risk_note_at_notification"] = alert.get("ev_risk_note")
        changed = changed or before != json.dumps(entry, sort_keys=True, ensure_ascii=False)

    for entry in entries:
        changed = backfill_ticket_ev_shadow_from_self_ai(entry) or changed
        changed = refresh_ticket_ev_shadows(entry) or changed
        changed = update_ticket_strategy_shadow(
            entry,
            updated_at=now.isoformat(timespec="seconds"),
        ) or changed
        risk_note = original_boaters_ev_risk_note(entry)
        if entry.get("ev_risk_note") != risk_note:
            entry["ev_risk_note"] = risk_note
            changed = True

    result_index = forward_result_index_from_rankings(date_text)
    official_result_cache = {}
    for entry in entries:
        result = resolve_forward_result(
            entry,
            result_index,
            now,
            official_cache=official_result_cache,
        )
        changed = update_forward_entry_result(entry, result, now.isoformat(timespec="seconds")) or changed

    changed = apply_forward_notification_status(entries, monitor_payload, now) or changed

    log_payload["updated_at"] = now.isoformat(timespec="seconds")
    log_payload["entry_count"] = len(entries)
    summary = forward_validation_summary(entries)
    summary["ev_risk_note_count"] = sum(
        bool(entry.get("ev_risk_note")) for entry in entries
    )
    summary["ticket_ev_shadow"] = original_boaters_ticket_ev_shadow_performance(
        entries
    )
    summary["ticket_position_shadow"] = (
        original_boaters_ticket_position_shadow_performance(entries)
    )
    summary["ticket_venue_probability_shadow"] = (
        original_boaters_ticket_venue_probability_shadow_performance(entries)
    )
    summary["ticket_strategy_shadow"] = ticket_strategy_shadow_performance(entries)
    log_payload["summary"] = summary
    save_json(path, log_payload)
    return str(path)


def original_boaters_shadow_performance(entries, include_by_venue=True):
    ordered = sorted(
        entries,
        key=lambda entry: (
            str(entry.get("date") or ""),
            str(entry.get("deadline_time") or ""),
            str(entry.get("race_id") or ""),
        ),
    )
    summary = forward_validation_summary(ordered)
    settled = [entry for entry in ordered if entry.get("status") in {"hit", "miss"}]
    current_losing = 0
    max_losing = 0
    for entry in settled:
        if entry.get("hit"):
            current_losing = 0
        else:
            current_losing += 1
            max_losing = max(max_losing, current_losing)
    total_points = sum(int(as_num(entry.get("points")) or 0) for entry in ordered)
    summary.update(
        {
            "avg_points": round(total_points / len(ordered), 2) if ordered else None,
            "planned_stake_yen": total_points * 100,
            "max_losing_streak": max_losing if settled else None,
            "current_losing_streak": current_losing if settled else None,
            "manshu_result_count": sum(
                1 for entry in settled if bool(entry.get("result_manshu"))
            ),
            "manshu_hit_count": sum(
                1
                for entry in settled
                if entry.get("hit") and bool(entry.get("result_manshu"))
            ),
            "first_signal_at": ordered[0].get("detected_at") if ordered else None,
            "last_signal_at": ordered[-1].get("detected_at") if ordered else None,
        }
    )
    summary["head_swap_shadow"] = original_boaters_head_swap_shadow_performance(ordered)
    summary["new_buff_debuff_shadow"] = original_boaters_buff_debuff_shadow_performance(ordered)
    summary["head56_confidence_shadow"] = (
        original_boaters_head56_confidence_shadow_performance(ordered)
    )
    summary["low_confidence_shadow"] = (
        original_boaters_low_confidence_shadow_performance(ordered)
    )
    summary["ticket_ev_shadow"] = original_boaters_ticket_ev_shadow_performance(ordered)
    summary["ticket_position_shadow"] = (
        original_boaters_ticket_position_shadow_performance(ordered)
    )
    summary["ticket_venue_probability_shadow"] = (
        original_boaters_ticket_venue_probability_shadow_performance(ordered)
    )
    summary["ticket_strategy_shadow"] = ticket_strategy_shadow_performance(ordered)
    if include_by_venue:
        venues = sorted({str(entry.get("place_name") or "") for entry in ordered if entry.get("place_name")})
        summary["by_venue"] = {
            venue: original_boaters_shadow_performance(
                [entry for entry in ordered if entry.get("place_name") == venue],
                include_by_venue=False,
            )
            for venue in venues
        }
    return summary


def original_boaters_head_swap_shadow_performance(entries):
    active = [
        entry
        for entry in entries
        if isinstance(entry.get("head_swap_shadow"), dict)
        and entry["head_swap_shadow"].get("active")
    ]
    settled = [
        entry
        for entry in active
        if entry["head_swap_shadow"].get("status") == "settled"
    ]
    investment = sum(
        int(as_num(entry["head_swap_shadow"].get("shadow_investment_yen")) or 0)
        for entry in settled
    )
    baseline_payback = sum(
        int(as_num(entry["head_swap_shadow"].get("result_payout_yen")) or 0)
        for entry in settled
        if entry["head_swap_shadow"].get("baseline_hit")
    )
    shadow_payback = sum(
        int(as_num(entry["head_swap_shadow"].get("shadow_payback_yen")) or 0)
        for entry in settled
    )
    return {
        "version": "original-boaters-24-head-swap-shadow-v1",
        "policy_id": original_boaters_forward.HEAD_SWAP_SHADOW_ID,
        "notification_enabled": False,
        "active_count": len(active),
        "pending_count": len(active) - len(settled),
        "settled_count": len(settled),
        "would_change_tickets_count": sum(
            bool(entry["head_swap_shadow"].get("would_change_tickets")) for entry in active
        ),
        "head_rescued_count": sum(
            bool(entry["head_swap_shadow"].get("head_rescued")) for entry in settled
        ),
        "head_lost_count": sum(
            bool(entry["head_swap_shadow"].get("head_lost")) for entry in settled
        ),
        "net_head_gain": sum(
            int(bool(entry["head_swap_shadow"].get("head_rescued")))
            - int(bool(entry["head_swap_shadow"].get("head_lost")))
            for entry in settled
        ),
        "ticket_rescued_count": sum(
            bool(entry["head_swap_shadow"].get("ticket_rescued")) for entry in settled
        ),
        "ticket_lost_count": sum(
            bool(entry["head_swap_shadow"].get("ticket_lost")) for entry in settled
        ),
        "net_ticket_gain": sum(
            int(bool(entry["head_swap_shadow"].get("ticket_rescued")))
            - int(bool(entry["head_swap_shadow"].get("ticket_lost")))
            for entry in settled
        ),
        "baseline_hit_count": sum(
            bool(entry["head_swap_shadow"].get("baseline_hit")) for entry in settled
        ),
        "shadow_hit_count": sum(
            bool(entry["head_swap_shadow"].get("shadow_hit")) for entry in settled
        ),
        "investment_yen": investment,
        "baseline_payback_yen": baseline_payback,
        "shadow_payback_yen": shadow_payback,
        "baseline_roi_pct": round(baseline_payback / investment * 100, 2) if investment else None,
        "shadow_roi_pct": round(shadow_payback / investment * 100, 2) if investment else None,
        "roi_delta_pp": (
            round((shadow_payback - baseline_payback) / investment * 100, 2)
            if investment
            else None
        ),
    }


def original_boaters_buff_debuff_shadow_performance(entries):
    eligible = [
        entry
        for entry in entries
        if isinstance(entry.get("new_buff_debuff_shadow"), dict)
        and entry["new_buff_debuff_shadow"].get("candidate_version")
    ]
    settled = [
        entry
        for entry in eligible
        if entry["new_buff_debuff_shadow"].get("status") == "settled"
    ]
    baseline_investment = sum(
        int(as_num(entry["new_buff_debuff_shadow"].get("baseline_investment_yen")) or 0)
        for entry in settled
    )
    baseline_payback = sum(
        int(as_num(entry["new_buff_debuff_shadow"].get("baseline_payback_yen")) or 0)
        for entry in settled
    )
    shadow_investment = sum(
        int(as_num(entry["new_buff_debuff_shadow"].get("shadow_investment_yen")) or 0)
        for entry in settled
    )
    shadow_payback = sum(
        int(as_num(entry["new_buff_debuff_shadow"].get("shadow_payback_yen")) or 0)
        for entry in settled
    )
    return {
        "version": "venue-buff-debuff-s-head-skip-shadow-v1",
        "policy_id": "skip_s_head_debuff_v1",
        "active": False,
        "notification_enabled": False,
        "entry_count": len(eligible),
        "pending_count": len(eligible) - len(settled),
        "settled_count": len(settled),
        "triggered_count": sum(
            bool(entry["new_buff_debuff_shadow"].get("would_skip")) for entry in eligible
        ),
        "avoided_loss_count": sum(
            entry["new_buff_debuff_shadow"].get("outcome") == "avoided_loss"
            for entry in settled
        ),
        "skipped_hit_count": sum(
            entry["new_buff_debuff_shadow"].get("outcome") == "skipped_hit"
            for entry in settled
        ),
        "baseline_investment_yen": baseline_investment,
        "baseline_payback_yen": baseline_payback,
        "shadow_investment_yen": shadow_investment,
        "shadow_payback_yen": shadow_payback,
        "baseline_roi_pct": (
            round(baseline_payback / baseline_investment * 100, 2)
            if baseline_investment
            else None
        ),
        "shadow_roi_pct": (
            round(shadow_payback / shadow_investment * 100, 2)
            if shadow_investment
            else None
        ),
        "profit_delta_yen": (
            shadow_payback - shadow_investment - (baseline_payback - baseline_investment)
        ),
    }


def original_boaters_head56_confidence_shadow_performance(entries):
    eligible = [
        entry
        for entry in entries
        if isinstance(entry.get("head56_confidence_shadow"), dict)
        and entry["head56_confidence_shadow"].get("active")
    ]
    settled = [
        entry
        for entry in eligible
        if entry["head56_confidence_shadow"].get("status") == "settled"
    ]
    baseline_investment = sum(
        int(as_num(entry["head56_confidence_shadow"].get("baseline_investment_yen")) or 0)
        for entry in settled
    )
    baseline_payback = sum(
        int(as_num(entry["head56_confidence_shadow"].get("baseline_payback_yen")) or 0)
        for entry in settled
    )
    return {
        "version": "original-boaters-24-head56-confidence-shadow-v1",
        "policy_id": original_boaters_forward.HEAD56_CONFIDENCE_SHADOW_ID,
        "active": False,
        "notification_enabled": False,
        "entry_count": len(eligible),
        "pending_count": len(eligible) - len(settled),
        "settled_count": len(settled),
        "avoided_loss_count": sum(
            entry["head56_confidence_shadow"].get("outcome") == "avoided_loss"
            for entry in settled
        ),
        "skipped_hit_count": sum(
            entry["head56_confidence_shadow"].get("outcome") == "skipped_hit"
            for entry in settled
        ),
        "baseline_investment_yen": baseline_investment,
        "baseline_payback_yen": baseline_payback,
        "shadow_investment_yen": 0,
        "shadow_payback_yen": 0,
        "baseline_roi_pct": (
            round(baseline_payback / baseline_investment * 100, 2)
            if baseline_investment
            else None
        ),
        "shadow_roi_pct": None,
        "profit_delta_yen": baseline_investment - baseline_payback,
    }


def original_boaters_low_confidence_shadow_performance(entries):
    candidate_ids = sorted(
        {
            candidate_id
            for entry in entries
            for candidate_id in (
                (entry.get("low_confidence_shadow") or {}).get("candidate_decisions")
                or {}
            )
        }
    )
    candidates = {}
    for candidate_id in candidate_ids:
        decisions = []
        for entry in entries:
            decision = (
                ((entry.get("low_confidence_shadow") or {}).get("candidate_decisions") or {})
                .get(candidate_id)
            )
            if isinstance(decision, dict) and decision.get("decision_available"):
                decisions.append(decision)
        settled = [decision for decision in decisions if decision.get("status") == "settled"]
        baseline_investment = sum(
            int(as_num(decision.get("baseline_investment_yen")) or 0)
            for decision in settled
        )
        baseline_payback = sum(
            int(as_num(decision.get("baseline_payback_yen")) or 0)
            for decision in settled
        )
        shadow_investment = sum(
            int(as_num(decision.get("shadow_investment_yen")) or 0)
            for decision in settled
        )
        shadow_payback = sum(
            int(as_num(decision.get("shadow_payback_yen")) or 0)
            for decision in settled
        )
        candidates[candidate_id] = {
            "decision_count": len(decisions),
            "pending_count": len(decisions) - len(settled),
            "settled_count": len(settled),
            "triggered_count": sum(bool(decision.get("would_skip")) for decision in decisions),
            "avoided_loss_count": sum(
                decision.get("outcome") == "avoided_loss" for decision in settled
            ),
            "skipped_hit_count": sum(
                decision.get("outcome") == "skipped_hit" for decision in settled
            ),
            "baseline_investment_yen": baseline_investment,
            "baseline_payback_yen": baseline_payback,
            "shadow_investment_yen": shadow_investment,
            "shadow_payback_yen": shadow_payback,
            "baseline_roi_pct": (
                round(baseline_payback / baseline_investment * 100, 2)
                if baseline_investment
                else None
            ),
            "shadow_roi_pct": (
                round(shadow_payback / shadow_investment * 100, 2)
                if shadow_investment
                else None
            ),
            "profit_delta_yen": (
                shadow_payback
                - shadow_investment
                - (baseline_payback - baseline_investment)
            ),
        }
    return {
        "version": "original-boaters-24-low-confidence-shadow-v1",
        "policy_id": original_boaters_forward.LOW_CONFIDENCE_SHADOW_ID,
        "active": False,
        "notification_enabled": False,
        "entry_count": sum(
            isinstance(entry.get("low_confidence_shadow"), dict) for entry in entries
        ),
        "candidates": candidates,
    }


def original_boaters_ticket_shadow_performance(
    entries,
    *,
    shadow_key,
    policy_id,
    version,
):
    eligible = [
        entry
        for entry in entries
        if isinstance(entry.get(shadow_key), dict)
        and entry[shadow_key].get("policy_id") == policy_id
    ]

    def snapshot_values(target):
        return [
            entry[shadow_key].get("snapshots", {}).get(target)
            for entry in eligible
            if isinstance(entry[shadow_key].get("snapshots", {}).get(target), dict)
        ]

    payload = {
        "version": version,
        "policy_id": policy_id,
        "active": False,
        "notification_enabled": False,
        "entry_count": len(eligible),
        "settled_count": sum(
            entry[shadow_key].get("settled_at") is not None for entry in eligible
        ),
    }
    for target in ("t10", "t5"):
        snapshots = snapshot_values(target)
        expected_rois = [
            float(value)
            for value in (
                as_num(snapshot.get("portfolio_expected_roi_pct")) for snapshot in snapshots
            )
            if value is not None
        ]
        payload[f"{target}_snapshot_count"] = len(snapshots)
        payload[f"{target}_complete_ticket_odds_count"] = sum(
            bool(snapshot.get("complete_ticket_odds")) for snapshot in snapshots
        )
        payload[f"{target}_avg_portfolio_expected_roi_pct"] = (
            round(sum(expected_rois) / len(expected_rois), 2) if expected_rois else None
        )
        payload[f"{target}_synthetic_odds_ge3_count"] = sum(
            (as_num(snapshot.get("synthetic_odds")) or 0) >= SYNTHETIC_ODDS_MIN
            for snapshot in snapshots
        )
    payload["both_targets_count"] = sum(
        {"t10", "t5"} <= set(entry[shadow_key].get("snapshots") or {})
        for entry in eligible
    )
    return payload


def original_boaters_ticket_ev_shadow_performance(entries):
    return original_boaters_ticket_shadow_performance(
        entries,
        shadow_key="ticket_ev_shadow",
        policy_id=original_boaters_forward.TICKET_EV_SHADOW_ID,
        version="original-boaters-24-ticket-ev-shadow-v1",
    )


def original_boaters_ticket_position_shadow_performance(entries):
    return original_boaters_ticket_shadow_performance(
        entries,
        shadow_key="ticket_position_shadow",
        policy_id=original_boaters_forward.TICKET_POSITION_SHADOW_ID,
        version="original-boaters-24-ticket-position-shadow-v1",
    )


def original_boaters_ticket_venue_probability_shadow_performance(entries):
    return original_boaters_ticket_shadow_performance(
        entries,
        shadow_key="ticket_venue_probability_shadow",
        policy_id=original_boaters_forward.TICKET_VENUE_PROBABILITY_SHADOW_ID,
        version="original-boaters-24-ticket-venue-probability-shadow-v1",
    )


def ticket_strategy_shadow_performance(entries):
    eligible = sorted(
        [
            entry
            for entry in entries
            if isinstance(entry.get("ticket_strategy_shadow"), dict)
            and entry["ticket_strategy_shadow"].get("policy_id")
            == TICKET_STRATEGY_SHADOW_ID
        ],
        key=lambda entry: (
            str(entry.get("date") or ""),
            str(entry.get("deadline_time") or ""),
            str(entry.get("race_id") or ""),
        ),
    )
    payload = {
        **ticket_strategy_shadow_policy(),
        "entry_count": len(eligible),
        "ready_count": sum(
            entry["ticket_strategy_shadow"].get("status") == "ready"
            for entry in eligible
        ),
        "settled_count": sum(
            entry["ticket_strategy_shadow"].get("status") == "settled"
            for entry in eligible
        ),
        "target_t5_count": sum(
            entry["ticket_strategy_shadow"].get("selected_target") == "t5"
            for entry in eligible
        ),
        "target_t10_count": sum(
            entry["ticket_strategy_shadow"].get("selected_target") == "t10"
            for entry in eligible
        ),
        "variants": {},
    }
    for variant_id, label in TICKET_STRATEGY_VARIANTS:
        variants = [
            entry["ticket_strategy_shadow"].get("variants", {}).get(variant_id)
            for entry in eligible
        ]
        variants = [variant for variant in variants if isinstance(variant, dict)]
        settled = [
            variant for variant in variants if variant.get("status") == "settled"
        ]
        bets = [variant for variant in settled if variant.get("bet")]
        points = sum(int(as_num(variant.get("points")) or 0) for variant in settled)
        investment = sum(
            int(as_num(variant.get("investment_yen")) or 0) for variant in settled
        )
        payback = sum(
            int(as_num(variant.get("payback_yen")) or 0) for variant in settled
        )
        hits = sum(bool(variant.get("hit")) for variant in bets)
        current_losing = 0
        max_losing = 0
        for variant in settled:
            if not variant.get("bet"):
                continue
            if variant.get("hit"):
                current_losing = 0
            else:
                current_losing += 1
                max_losing = max(max_losing, current_losing)
        expected_rois = [
            float(value)
            for value in (
                as_num(variant.get("portfolio_expected_roi_pct"))
                for variant in variants
            )
            if value is not None
        ]
        payload["variants"][variant_id] = {
            "label": label,
            "entry_count": len(variants),
            "settled_count": len(settled),
            "bet_count": len(bets),
            "skip_count": len(settled) - len(bets),
            "hit_count": hits,
            "miss_count": len(bets) - hits,
            "hit_rate_pct": (
                round(hits / len(bets) * 100.0, 2) if bets else None
            ),
            "total_points": points,
            "avg_points_per_signal": (
                round(points / len(settled), 2) if settled else None
            ),
            "avg_points_per_bet": (
                round(points / len(bets), 2) if bets else None
            ),
            "investment_yen": investment,
            "payback_yen": payback,
            "profit_yen": payback - investment if settled else None,
            "roi_pct": (
                round(payback / investment * 100.0, 2) if investment else None
            ),
            "avg_expected_roi_pct": (
                round(sum(expected_rois) / len(expected_rois), 2)
                if expected_rois
                else None
            ),
            "max_losing_streak": max_losing if bets else None,
            "current_losing_streak": current_losing if bets else None,
        }
    baseline = payload["variants"].get("baseline_current") or {}
    for variant_id in ("ev_pruned", "rescue12"):
        variant = payload["variants"].get(variant_id) or {}
        if variant.get("roi_pct") is not None and baseline.get("roi_pct") is not None:
            variant["roi_delta_pp_vs_baseline"] = round(
                variant["roi_pct"] - baseline["roi_pct"],
                2,
            )
        else:
            variant["roi_delta_pp_vs_baseline"] = None
        if variant.get("profit_yen") is not None and baseline.get("profit_yen") is not None:
            variant["profit_delta_yen_vs_baseline"] = (
                variant["profit_yen"] - baseline["profit_yen"]
            )
        else:
            variant["profit_delta_yen_vs_baseline"] = None
    settled_count = payload["settled_count"]
    payload["decision_ready"] = settled_count >= TICKET_STRATEGY_MIN_DECISION_SAMPLE
    payload["remaining_to_decision"] = max(
        0,
        TICKET_STRATEGY_MIN_DECISION_SAMPLE - settled_count,
    )
    return payload


def refresh_original_boaters_shadow_summary(now):
    directory = original_boaters_shadow_summary_path().parent
    aggregate = {}
    daily_files = []
    for path in sorted(directory.glob("original_boaters_24_shadow_????????.json")):
        payload = load_json(path, {})
        if not isinstance(payload, dict) or payload.get("rule_set_id") != original_boaters_forward.RULE_SET_ID:
            continue
        entries = payload.get("entries") or []
        date_text = str(payload.get("date") or "")
        result_index = forward_result_index_from_rankings(date_text) if date_text else {}
        changed = False
        for entry in entries:
            race_id = str(entry.get("race_id") or "")
            strategy_status = (
                (entry.get("ticket_strategy_shadow") or {}).get("status")
            )
            if strategy_status not in {"ready", "settled"}:
                changed = refresh_ticket_ev_shadows(entry) or changed
                changed = update_ticket_strategy_shadow(
                    entry,
                    updated_at=now.isoformat(timespec="seconds"),
                ) or changed
            result = forward_result_from_db(race_id) or result_index.get(race_id)
            changed = update_forward_entry_result(
                entry,
                result,
                now.isoformat(timespec="seconds"),
            ) or changed
            aggregate[forward_entry_key(race_id, entry.get("rule_id"))] = entry
        if changed:
            payload["updated_at"] = now.isoformat(timespec="seconds")
            payload["summary"] = original_boaters_shadow_performance(entries)
            save_json(path, payload)
        daily_files.append(str(path))

    entries = list(aggregate.values())
    performance = original_boaters_shadow_performance(entries)
    target_minimum = 100
    target_preferred = 200
    count = len(entries)
    if count >= target_preferred:
        progress_status = "preferred_sample_reached"
    elif count >= target_minimum:
        progress_status = "minimum_sample_reached"
    else:
        progress_status = "collecting"
    progress = {
        "status": progress_status,
        "entry_count": count,
        "minimum_target": target_minimum,
        "preferred_target": target_preferred,
        "remaining_to_minimum": max(0, target_minimum - count),
        "remaining_to_preferred": max(0, target_preferred - count),
        "minimum_progress_pct": round(min(1.0, count / target_minimum) * 100, 1),
        "preferred_progress_pct": round(min(1.0, count / target_preferred) * 100, 1),
    }
    rules_payload = original_boaters_forward.load_rules()
    summary_payload = {
        "version": "original-boaters-24-forward-summary-v2",
        "rule_set_id": original_boaters_forward.RULE_SET_ID,
        "mode": "forward_notification_enabled",
        "updated_at": now.isoformat(timespec="seconds"),
        "rule_count": len(rules_payload.get("rules") or []),
        "source_period": rules_payload.get("source_period"),
        "source_report": rules_payload.get("source_report"),
        "ai_source_required": original_boaters_forward.ORIGINAL_AI_SOURCE,
        "head_swap_shadow_policy": {
            "policy_id": original_boaters_forward.HEAD_SWAP_SHADOW_ID,
            "notification_enabled": False,
            "description": (
                "候補外艇のAI1着率が現行最弱頭より4pt以上高く、"
                "現行複合点差が5点以内の時だけ反実仮想買い目を記録する。"
            ),
        },
        "new_buff_debuff_shadow_policy": {
            "policy_id": "skip_s_head_debuff_v1",
            "active": False,
            "notification_enabled": False,
            "description": (
                "現行頭候補に新規辞書のS級head_debuffが一致した場合、"
                "買い目や通知を変えず、見送り反実仮想だけを記録する。"
            ),
        },
        "head56_confidence_shadow_policy": {
            "policy_id": original_boaters_forward.HEAD56_CONFIDENCE_SHADOW_ID,
            "active": False,
            "notification_enabled": False,
            "description": (
                "5/6頭限定型で、券面に残らなかった5/6号艇のAI1着率が"
                "券面頭と同等以上なら、見送り反実仮想だけを記録する。"
            ),
        },
        "low_confidence_shadow_policy": {
            "policy_id": original_boaters_forward.LOW_CONFIDENCE_SHADOW_ID,
            "active": False,
            "notification_enabled": False,
            "description": (
                "未選択頭優位、AI・展示・スリット不一致、T-10/T-5期待値低下を"
                "候補別に固定し、未来結果だけで見送り効果を比較する。"
            ),
        },
        "ticket_ev_shadow_policy": {
            "policy_id": original_boaters_forward.TICKET_EV_SHADOW_ID,
            "active": False,
            "notification_enabled": False,
            "description": (
                "サイン時点のAI1着率・AI3連対率から買い目確率を固定し、"
                "締切10分前・5分前の公式オッズで期待値を記録する。"
            ),
        },
        "ticket_position_shadow_policy": {
            "policy_id": original_boaters_forward.TICKET_POSITION_SHADOW_ID,
            "active": False,
            "notification_enabled": False,
            "description": (
                "1着・2着・3着専用モデルから120通り確率を作り、"
                "同じ買い目・同じオッズで従来AI1/AI3方式と比較する。"
            ),
        },
        "ticket_venue_probability_shadow_policy": {
            "policy_id": original_boaters_forward.TICKET_VENUE_PROBABILITY_SHADOW_ID,
            "active": False,
            "notification_enabled": False,
            "description": (
                "場×艇番、展示、気象、水面、スリットを確率ポイントで補正し、"
                "同じ買い目・同じオッズで既存方式と比較する。"
            ),
        },
        "ticket_strategy_shadow_policy": ticket_strategy_shadow_policy(),
        "forward_validation_warning": (
            "過去全期間で選んだ探索ルールのため、ここに記録する未来データ100〜200件で別評価する。"
        ),
        "progress": progress,
        "performance": performance,
        "daily_file_count": len(daily_files),
        "daily_files": daily_files,
    }
    save_json(original_boaters_shadow_summary_path(), summary_payload)
    return summary_payload


def update_original_boaters_shadow_log(date_text, signs, now, monitor_payload=None):
    path = original_boaters_shadow_path(date_text)
    payload = load_json(
        path,
        {
            "version": "original-boaters-24-forward-v2",
            "rule_set_id": original_boaters_forward.RULE_SET_ID,
            "mode": "forward_notification_enabled",
            "date": date_text,
            "entries": [],
        },
    )
    payload["version"] = "original-boaters-24-forward-v2"
    payload["rule_set_id"] = original_boaters_forward.RULE_SET_ID
    payload["mode"] = "forward_notification_enabled"
    payload["head_swap_shadow_policy"] = {
        "policy_id": original_boaters_forward.HEAD_SWAP_SHADOW_ID,
        "notification_enabled": False,
        "ai_win_delta_min_pp": original_boaters_forward.HEAD_SWAP_AI_WIN_DELTA_MIN,
        "max_current_score_gap": original_boaters_forward.HEAD_SWAP_CURRENT_SCORE_GAP_MAX,
    }
    payload["new_buff_debuff_shadow_policy"] = {
        "policy_id": "skip_s_head_debuff_v1",
        "active": False,
        "notification_enabled": False,
        "candidate_path": str(
            ROOT / "data" / "output" / "venue_new_buff_debuff_candidates.json"
        ),
    }
    payload["head56_confidence_shadow_policy"] = {
        "policy_id": original_boaters_forward.HEAD56_CONFIDENCE_SHADOW_ID,
        "active": False,
        "notification_enabled": False,
        "historical_observations": 63,
        "historical_hits": 0,
    }
    payload["low_confidence_shadow_policy"] = {
        "policy_id": original_boaters_forward.LOW_CONFIDENCE_SHADOW_ID,
        "active": False,
        "notification_enabled": False,
        "production_action": "none",
        "target_minutes": [10, 5],
    }
    payload["ticket_ev_shadow_policy"] = {
        "policy_id": original_boaters_forward.TICKET_EV_SHADOW_ID,
        "active": False,
        "notification_enabled": False,
        "target_minutes": [10, 5],
        "synthetic_odds_min": SYNTHETIC_ODDS_MIN,
    }
    payload["ticket_position_shadow_policy"] = {
        "policy_id": original_boaters_forward.TICKET_POSITION_SHADOW_ID,
        "active": False,
        "notification_enabled": False,
        "target_minutes": [10, 5],
        "comparison_baseline": original_boaters_forward.TICKET_EV_SHADOW_ID,
    }
    payload["ticket_venue_probability_shadow_policy"] = {
        "policy_id": original_boaters_forward.TICKET_VENUE_PROBABILITY_SHADOW_ID,
        "active": False,
        "notification_enabled": False,
        "target_minutes": [10, 5],
        "comparison_baseline": original_boaters_forward.TICKET_EV_SHADOW_ID,
    }
    payload["ticket_strategy_shadow_policy"] = ticket_strategy_shadow_policy()
    entries = payload.setdefault("entries", [])
    by_key = {
        forward_entry_key(entry.get("race_id"), entry.get("rule_id")): entry
        for entry in entries
    }
    new_count = 0
    for sign in signs or []:
        key = forward_entry_key(sign.get("race_id"), sign.get("rule_id"))
        entry = by_key.get(key)
        minutes_to_deadline = as_num(sign.get("minutes_to_deadline"))
        if entry is None:
            entry = {
                "status": "pending_result",
                "result": None,
                "created_at": now.isoformat(timespec="seconds"),
                "detected_at": sign.get("detected_at") or now.isoformat(timespec="seconds"),
                "first_minutes_to_deadline": (
                    round(minutes_to_deadline, 1) if minutes_to_deadline is not None else None
                ),
                "notification_enabled": True,
                "notification_status": "pending",
                "date": sign.get("date"),
                "race_id": sign.get("race_id"),
                "place_name": sign.get("place_name"),
                "round": sign.get("round"),
                "deadline_time": sign.get("deadline_time"),
                "source_type": sign.get("source_type"),
                "ai_source": sign.get("ai_source"),
                "data_mode": sign.get("data_mode"),
                "rule_set_id": sign.get("rule_set_id"),
                "rule_id": sign.get("rule_id"),
                "rule_status": sign.get("rule_status"),
                "condition": sign.get("condition"),
                "base_id": sign.get("base_id"),
                "context_id": sign.get("context_id"),
                "template_id": sign.get("template_id"),
                "buy_method": sign.get("buy_method"),
                "historical": sign.get("historical") or {},
                "points": sign.get("points"),
                "planned_investment_yen": int(as_num(sign.get("points")) or 0) * 100,
                "tickets": sign.get("tickets") or [],
                "heads": sign.get("heads") or [],
                "axes": sign.get("axes") or [],
                "keshi": sign.get("keshi"),
                "condition_snapshot": sign.get("condition_snapshot") or {},
                "independent_probabilities": sign.get(
                    "independent_probabilities"
                )
                or [],
                "independent_probability_model": sign.get(
                    "independent_probability_model"
                )
                or {},
                "head_swap_shadow": sign.get("head_swap_shadow") or {},
                "head56_confidence_shadow": sign.get("head56_confidence_shadow") or {},
                "low_confidence_shadow": sign.get("low_confidence_shadow") or {},
                "ticket_ev_shadow": sign.get("ticket_ev_shadow") or {},
                "ticket_position_shadow": sign.get("ticket_position_shadow") or {},
                "ticket_venue_probability_shadow": sign.get(
                    "ticket_venue_probability_shadow"
                )
                or {},
                "new_buff_debuff_shadow": sign.get("new_buff_debuff_shadow") or {},
                "ev_risk_note_at_notification": sign.get("ev_risk_note") or "",
                "push_key": sign.get("push_key"),
            }
            entries.append(entry)
            by_key[key] = entry
            new_count += 1
        entry["last_seen_at"] = now.isoformat(timespec="seconds")
        entry["minutes_to_deadline"] = (
            round(minutes_to_deadline, 1) if minutes_to_deadline is not None else None
        )
        entry["notification_enabled"] = True
        if sign.get("independent_probabilities"):
            entry["independent_probabilities"] = sign.get(
                "independent_probabilities"
            ) or []
            entry["independent_probability_model"] = sign.get(
                "independent_probability_model"
            ) or {}
        if not entry.get("new_buff_debuff_shadow") and sign.get("new_buff_debuff_shadow"):
            entry["new_buff_debuff_shadow"] = sign.get("new_buff_debuff_shadow") or {}
        if not entry.get("head56_confidence_shadow") and sign.get(
            "head56_confidence_shadow"
        ):
            entry["head56_confidence_shadow"] = sign.get("head56_confidence_shadow") or {}
        if not entry.get("low_confidence_shadow") and sign.get("low_confidence_shadow"):
            entry["low_confidence_shadow"] = sign.get("low_confidence_shadow") or {}
        if not entry.get("ticket_ev_shadow") and sign.get("ticket_ev_shadow"):
            entry["ticket_ev_shadow"] = sign.get("ticket_ev_shadow") or {}
        if not entry.get("ticket_position_shadow") and sign.get(
            "ticket_position_shadow"
        ):
            entry["ticket_position_shadow"] = sign.get("ticket_position_shadow") or {}
        if not entry.get("ticket_venue_probability_shadow") and sign.get(
            "ticket_venue_probability_shadow"
        ):
            entry["ticket_venue_probability_shadow"] = (
                sign.get("ticket_venue_probability_shadow") or {}
            )
        if not entry.get("push_key") and sign.get("push_key"):
            entry["push_key"] = sign.get("push_key")
        if entry.get("notification_status") == "shadow_not_sent":
            entry["notification_status"] = "pending"

    result_index = forward_result_index_from_rankings(date_text)
    official_result_cache = {}
    for entry in entries:
        refresh_ticket_ev_shadows(entry)
        update_ticket_strategy_shadow(
            entry,
            updated_at=now.isoformat(timespec="seconds"),
        )
        update_original_boaters_low_confidence_odds(entry)
        entry["ev_risk_note"] = original_boaters_ev_risk_note(entry)
        result = resolve_forward_result(
            entry,
            result_index,
            now,
            official_cache=official_result_cache,
        )
        update_forward_entry_result(entry, result, now.isoformat(timespec="seconds"))

    if monitor_payload is not None:
        apply_forward_notification_status(entries, monitor_payload, now)

    payload["updated_at"] = now.isoformat(timespec="seconds")
    payload["entry_count"] = len(entries)
    payload["summary"] = original_boaters_shadow_performance(entries)
    save_json(path, payload)
    global_summary = refresh_original_boaters_shadow_summary(now)
    return {
        "enabled": True,
        "mode": "forward_notification_enabled",
        "signals_this_run": len(signs or []),
        "new_entries_this_run": new_count,
        "daily_log_path": str(path),
        "summary_path": str(original_boaters_shadow_summary_path()),
        "progress": global_summary.get("progress") or {},
        "performance": global_summary.get("performance") or {},
    }


def merge_live_metrics_into_ranking_path(path, updates, now):
    payload = load_json(path, {})
    if not isinstance(payload, dict):
        return False
    changed = False
    for group_name in ("races", "strict_races", "morning_candidates"):
        for race in payload.get(group_name) or []:
            race_id = race.get("race_id")
            update = updates.get(race_id)
            if not update:
                continue
            metrics = race.setdefault("metrics", {})
            before = json.dumps(metrics, sort_keys=True, ensure_ascii=False)
            metrics.update(update.get("metrics") or {})
            if update.get("selection"):
                old_selection = json.dumps(race.get("selection") or {}, sort_keys=True, ensure_ascii=False)
                race["selection"] = update.get("selection")
                changed = changed or old_selection != json.dumps(race.get("selection") or {}, sort_keys=True, ensure_ascii=False)
            if has_full_exhibition(metrics):
                status_text = str(race.get("status") or "")
                if "展示待ち" in status_text:
                    race["status"] = status_text.replace("・展示待ち", "").replace("展示待ち", "展示込み")
                elif "展示込み" not in status_text:
                    race["status"] = f"{status_text}・展示込み" if status_text else "展示込み"
            race["last_minute_checked_at"] = update.get("checked_at")
            race["last_minute_alert_type"] = update.get("alert_type")
            if update.get("last_minute_manshu_rate_pct") is not None:
                old_rate = race.get("manshu_rate_pct")
                race["last_minute_manshu_rate_pct"] = update.get("last_minute_manshu_rate_pct")
                race["post_exhibition_manshu_rate_pct"] = update.get("last_minute_manshu_rate_pct")
                if race.get("morning_manshu_rate_pct") is None and old_rate is not None:
                    race["morning_manshu_rate_pct"] = old_rate
                race["manshu_rate_pct"] = update.get("last_minute_manshu_rate_pct")
                changed = changed or old_rate != race.get("manshu_rate_pct")
            if update.get("morning_manshu_rate_pct") is not None:
                race["morning_manshu_rate_pct"] = update.get("morning_manshu_rate_pct")
            if update.get("rate_source"):
                race["last_minute_rate_source"] = update.get("rate_source")
            if update.get("source_type"):
                race["last_minute_source_type"] = update.get("source_type")
            if update.get("live_rank") is not None:
                race["last_minute_live_rank"] = update.get("live_rank")
            race["last_minute_checks"] = update.get("checks") or []
            race["last_minute_strategy_ids"] = update.get("strategy_ids") or []
            race["last_minute_subcore_strategy_ids"] = update.get("subcore_strategy_ids") or []
            race["last_minute_candidate_strategy_ids"] = update.get("candidate_strategy_ids") or []
            if update.get("buy_decision"):
                old_buy_decision = race.get("buy_decision")
                race["buy_decision"] = update.get("buy_decision")
                changed = changed or old_buy_decision != race.get("buy_decision")
            for source_key, target_key in (
                ("near_miss_level", "near_miss_level"),
                ("near_miss_summary", "near_miss_summary"),
                ("near_miss_reasons", "near_miss_reasons"),
                ("near_miss_positives", "near_miss_positives"),
            ):
                if source_key in update:
                    old_value = race.get(target_key)
                    race[target_key] = update.get(source_key)
                    changed = changed or old_value != race.get(target_key)
            post_rate = as_num(update.get("last_minute_manshu_rate_pct"))
            if post_rate is not None:
                checks = []
                if post_rate >= CORE_ALERT_RATE:
                    checks.append(f"展示後40%以上:OK({post_rate:.2f}%)")
                elif post_rate >= SUBCORE_ALERT_RATE_MIN:
                    checks.append(f"展示後38〜39.9%:OK({post_rate:.2f}%)")
                    checks.append("本命40%以上:NG")
                else:
                    checks.append(f"展示後38%未満:NG({post_rate:.2f}%)")
                    checks.append("本命40%以上:NG")
                if update.get("core_buy_ready"):
                    checks.append("本命買い条件:OK")
                elif update.get("subcore_buy_ready"):
                    checks.append("準本命買い条件:OK")
                elif metrics.get("core_front_no1_odds_blocked"):
                    checks.append("1号艇人気不足で1号艇消し買い:NG")
                elif has_full_exhibition(metrics):
                    checks.append("買い条件:NG")
                if update.get("near_miss_summary") and not (update.get("core_buy_ready") or update.get("subcore_buy_ready")):
                    checks.append(f"見送り理由:{update.get('near_miss_summary')}")
                old_checks = race.get("final_decision_checks")
                race["final_decision_checks"] = checks
                changed = changed or old_checks != checks
            after = json.dumps(metrics, sort_keys=True, ensure_ascii=False)
            changed = changed or before != after
    if changed:
        payload["last_minute_updated_at"] = now.isoformat(timespec="seconds")
        save_json(path, payload)
    return changed


def load_push_config():
    config = {}
    if PUSH_CONFIG.exists():
        loaded = load_json(PUSH_CONFIG, {})
        if isinstance(loaded, dict):
            config.update(loaded)
    env_map = {
        "ntfy_server": "BOATERS_NTFY_SERVER",
        "ntfy_topic": "BOATERS_NTFY_TOPIC",
        "ntfy_token": "BOATERS_NTFY_TOKEN",
        "ntfy_priority": "BOATERS_NTFY_PRIORITY",
    }
    for key, env_name in env_map.items():
        value = os.environ.get(env_name)
        if value:
            config[key] = value
    topic_file = os.environ.get("BOATERS_NTFY_TOPIC_FILE")
    topic_paths = [Path(topic_file).expanduser()] if topic_file else []
    topic_paths.extend(
        [
            ROOT / "data" / "ntfy_topic.txt",
        ]
    )
    if not config.get("ntfy_topic"):
        for path in topic_paths:
            try:
                if path == PUSH_CONFIG or not path.exists():
                    continue
                if path.suffix == ".json":
                    loaded = load_json(path, {})
                    if isinstance(loaded, dict) and loaded.get("ntfy_topic"):
                        config.update({k: v for k, v in loaded.items() if v})
                        break
                else:
                    topic = path.read_text(encoding="utf-8").strip()
                    if topic:
                        config["ntfy_topic"] = topic
                        break
            except Exception:
                continue
    config.setdefault("ntfy_server", "https://ntfy.sh")
    config.setdefault("ntfy_topic", DEFAULT_NTFY_TOPIC)
    return config


def ntfy_url(config):
    topic = str(config.get("ntfy_topic") or "").strip()
    if not topic:
        return None
    if topic.startswith("http://") or topic.startswith("https://"):
        return topic
    server = str(config.get("ntfy_server") or "https://ntfy.sh").rstrip("/")
    return f"{server}/{topic}"


def ascii_header(value, fallback):
    text = str(value or "")
    try:
        text.encode("latin-1")
        return text
    except UnicodeEncodeError:
        return fallback


def send_ntfy(config, title, message, tags="rotating_light", priority=None):
    url = ntfy_url(config)
    if not url:
        return {"enabled": False, "reason": "ntfy_topic not configured"}
    safe_title = ascii_header(title, "BOATERS Alert")
    if safe_title != str(title or ""):
        message = f"{title}\n\n{message}"
    headers = {
        "Title": safe_title,
        "Tags": ascii_header(tags, "boat"),
        "Priority": str(priority or config.get("ntfy_priority") or "high"),
    }
    token = config.get("ntfy_token")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = message.encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        context = ssl.create_default_context()
        try:
            import certifi  # type: ignore

            context = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            pass
        with urllib.request.urlopen(request, timeout=8, context=context) as response:
            return {"enabled": True, "ok": 200 <= response.status < 300, "status": response.status}
    except urllib.error.HTTPError as exc:
        return {"enabled": True, "ok": False, "status": exc.code, "error": str(exc)}
    except Exception as exc:
        curl_cmd = [
            "curl",
            "-sS",
            "--max-time",
            "8",
            "-o",
            "/dev/null",
            "-w",
            "%{http_code}",
            "-X",
            "POST",
            url,
        ]
        for key, value in headers.items():
            curl_cmd.extend(["-H", f"{key}: {value}"])
        curl_cmd.extend(["--data-binary", "@-"])
        try:
            result = subprocess.run(curl_cmd, input=data, capture_output=True, check=False)
            status_text = result.stdout.decode("utf-8", errors="replace").strip()
            status = int(status_text) if status_text.isdigit() else None
            ok = status is not None and 200 <= status < 300
            return {
                "enabled": True,
                "ok": ok,
                "status": status,
                "fallback": "curl",
                "python_error": str(exc),
                "curl_error": result.stderr.decode("utf-8", errors="replace").strip() or None,
            }
        except Exception as curl_exc:
            return {"enabled": True, "ok": False, "error": str(exc), "curl_error": str(curl_exc)}


def push_notifications(payload, state, now):
    config = load_push_config()
    sign_alerts = [
        alert
        for alert in payload.get("alerts") or []
        if alert.get("alert_type") == "venue_sign" and is_venue_sign_alert(alert)
    ]
    expected_attempts = len(sign_alerts)
    if not ntfy_url(config):
        return {
            "enabled": False,
            "sent": 0,
            "attempted": expected_attempts,
            "errors": [{"error": "ntfy_topic not configured"}] if expected_attempts else [],
            "results": [],
        }

    pushed = state.setdefault("pushed", {})
    results = []
    errors = []

    for alert in sign_alerts:
        key = f"alert:{alert.get('race_id')}:{alert.get('alert_type')}:venue_sign"
        if pushed.get(key):
            results.append(
                {
                    "key": key,
                    "enabled": True,
                    "ok": True,
                    "skipped_duplicate": True,
                    "sent_at": pushed.get(key),
                }
            )
            continue
        if alert.get("rule_set_id") == original_boaters_forward.RULE_SET_ID:
            title = "BOATERS 新24場サイン"
        elif alert.get("alert_type") == "venue_sign":
            title = "BOATERS 24場サイン"
        elif alert.get("alert_type") == "late_riser_buy_ok":
            title = "BOATERS 24場サイン急浮上"
        elif alert.get("alert_type") in {"subcore_watch", "late_riser_subcore_watch"}:
            title = "BOATERS 24場サイン準本命"
        elif alert.get("alert_type") == "late_riser":
            title = "BOATERS 24場サイン急浮上"
        elif alert.get("alert_type") == "buy_ok":
            title = "BOATERS 24場サイン本命"
        else:
            title = "BOATERS 24場サイン"
        result = send_ntfy(config, title, alert.get("message") or "", tags="moneybag,boat")
        results.append({"key": key, **result})
        if result.get("ok"):
            pushed[key] = now.isoformat(timespec="seconds")
        elif result.get("enabled"):
            errors.append({"key": key, "error": result.get("error"), "status": result.get("status")})

    return {
        "enabled": True,
        "sent": sum(1 for result in results if result.get("ok")),
        "attempted": len(results),
        "errors": errors,
        "results": results,
    }


def push_test_notification(now):
    config = load_push_config()
    result = send_ntfy(
        config,
        "BOATERS通知テスト",
        (
            "BOATERS万舟通知のテストです。\n"
            f"送信時刻: {now.isoformat(timespec='seconds')}\n"
            "この通知がスマホに届けば、ntfy送信経路は生きています。"
        ),
        tags="white_check_mark,boat",
        priority="urgent",
    )
    return {
        "version": "boaters-manshu-alerts-v1",
        "generated_at": now.isoformat(timespec="seconds"),
        "test_push": True,
        "push": result,
    }


def rank_values(rows, key, ascending=True):
    vals = sorted(
        {row[key] for row in rows if row.get(key) is not None},
        reverse=not ascending,
    )
    rank_by_val = {value: idx + 1 for idx, value in enumerate(vals)}
    for row in rows:
        row[f"{key}_rank"] = rank_by_val.get(row.get(key), 9)


def bounded(value, low, high):
    return max(low, min(high, value))


def pct_to_logit(value, default=16.67):
    p = bounded((value if value is not None else default) / 100.0, 0.01, 0.99)
    return math.log(p / (1.0 - p))


def sigmoid_pct(score):
    return 100.0 / (1.0 + math.exp(-bounded(score, -12, 12)))


def normalize_total(values, total, low, high):
    if not values:
        return []
    positive = [max(0.01, value) for value in values]
    scale = total / sum(positive)
    rates = [bounded(value * scale, low, high) for value in positive]
    for _ in range(8):
        diff = total - sum(rates)
        if abs(diff) < 0.01:
            break
        if diff > 0:
            free = [idx for idx, value in enumerate(rates) if value < high - 0.01]
        else:
            free = [idx for idx, value in enumerate(rates) if value > low + 0.01]
        if not free:
            break
        step = diff / len(free)
        for idx in free:
            rates[idx] = bounded(rates[idx] + step, low, high)
    return [round(value, 2) for value in rates]


def composite_rate_reasons(row, by_boat):
    boat = row["boat_number"]
    reasons = []
    ai_plus_rank = row.get("ai_plus_rank")
    if ai_plus_rank and ai_plus_rank <= 2:
        reasons.append(f"AI+{int(ai_plus_rank)}位で基本力が高い")
    elif ai_plus_rank and ai_plus_rank >= 5:
        reasons.append(f"AI+{int(ai_plus_rank)}位で基本力は低め")
    if row.get("double_time"):
        reasons.append("展示タイムと1周タイムが両方1位")
    elif row.get("exhibit_rank", 9) <= 2:
        reasons.append("展示か1周が2位以内")
    avg_diff = row.get("avg_isshu_diff")
    if avg_diff is not None:
        if avg_diff >= 0.10:
            reasons.append(f"展示+1周が平均より{avg_diff:.2f}秒速い")
        elif avg_diff <= -0.10:
            reasons.append(f"展示+1周が平均より{abs(avg_diff):.2f}秒遅い")
    if row.get("super_slit_alert"):
        reasons.append("左の艇より展示0.10秒速くST順位も上")
    right = by_boat.get(boat + 1)
    if right and right.get("super_slit_alert"):
        reasons.append(f"{boat + 1}号艇のスーパースリットで圧を受ける")
    if row.get("summer_b1_isshu_factor") == "fast_hold":
        reasons.append("夏場の1周タイムが平均より速くイン残り寄り")
    elif row.get("summer_b1_isshu_factor") == "slow_fly":
        reasons.append("夏場の1周タイムが平均より遅くイン飛び寄り")
    if row.get("matchup_label") in {"1号艇キラー", "相性バフ", "相性軸バフ", "相性デバフ"}:
        reasons.append(str(row.get("matchup_label")))
    if row.get("low_outer_revive"):
        reasons.append("低評価外枠だが展示で復活")
    for reason in row.get("venue_low_ai_revival_reasons") or []:
        reasons.append(reason)
    if row.get("longshot_head_candidate"):
        reasons.append("穴頭候補に一致")
    if row.get("mawariashi_rank") is not None and row.get("mawariashi_rank") <= 2 and boat in {5, 6}:
        reasons.append("外枠のまわり足が2位以内")
    if row.get("mawariashi_rank") is not None and row.get("mawariashi_rank") >= 5 and boat == 1:
        reasons.append("1号艇のまわり足が5位以下")
    if row.get("tilt") is not None and row.get("tilt") >= 0.5 and boat in {5, 6}:
        reasons.append("外枠チルト0.5以上")
    for reason in row.get("venue_factor_reasons") or []:
        reasons.append(reason)
    return reasons[:4]


def compute_composite_boat_rates(rows):
    by_boat = {row["boat_number"]: row for row in rows}
    win_scores = []
    top3_scores = []
    for row in rows:
        boat = row["boat_number"]
        ai_pred = row.get("ai_prediction_pct")
        ai_top3 = row.get("ai_3ren_pct")
        general = row.get("general_3ren_pct")
        ai_plus_rank = row.get("ai_plus_rank") or 4
        exhibit_rank = row.get("exhibit_rank") or 4
        st_rank = row.get("st_rank_general") if row.get("st_rank_general") is not None else 4
        avg_diff = bounded(row.get("avg_isshu_diff") or 0.0, -0.35, 0.35)
        venue_head_delta = bounded(row.get("venue_head_score_delta") or 0.0, -7.0, 7.0)
        venue_top3_delta = bounded(row.get("venue_top3_score_delta") or 0.0, -6.0, 6.0)
        venue_manshu_delta = bounded(row.get("venue_manshu_score_delta") or 0.0, -5.0, 5.0)

        win_score = math.log(max(ai_pred if ai_pred is not None else 16.67, 0.1))
        win_score += (3.5 - ai_plus_rank) * 0.08
        win_score += (3.5 - exhibit_rank) * 0.07
        win_score += (3.5 - st_rank) * 0.035
        win_score += avg_diff * 1.10
        win_score += venue_head_delta * 0.018 + venue_manshu_delta * 0.006
        if row.get("double_time"):
            win_score += 0.16 if boat == 1 else 0.25
        if row.get("super_slit_alert"):
            win_score += 0.22 if boat in {2, 3} else 0.30
        right = by_boat.get(boat + 1)
        if right and right.get("super_slit_alert"):
            win_score -= 0.22 if boat == 1 else 0.12
        if row.get("low_outer_revive"):
            win_score += 0.15
        if row.get("venue_low_ai_revival"):
            profile = row.get("venue_low_ai_revival_profile") or {}
            if profile.get("head_ok"):
                win_score += 0.10 + min((as_num(profile.get("win_rate_pp")) or 0) * 0.006, 0.08)
        if row.get("longshot_head_candidate"):
            win_score += 0.10
        if row.get("summer_b1_isshu_factor") == "fast_hold":
            win_score += 0.18
        elif row.get("summer_b1_isshu_factor") == "slow_fly":
            win_score -= 0.22
        if row.get("matchup_label") == "1号艇キラー":
            win_score += 0.22
        elif row.get("matchup_label") == "相性バフ":
            win_score += 0.18
        elif row.get("matchup_label") == "相性軸バフ":
            win_score += 0.12
        elif row.get("matchup_label") == "相性デバフ":
            win_score -= 0.18
        if boat == 1 and row.get("_morning_metrics", {}).get("matchup_lane1_bad_flag"):
            win_score -= 0.14
        win_scores.append(win_score)

        if ai_top3 is not None and general is not None:
            base_top3 = ai_top3 * 0.62 + general * 0.38
        elif ai_top3 is not None:
            base_top3 = ai_top3
        elif general is not None:
            base_top3 = general
        else:
            base_top3 = 50.0
        top3_score = pct_to_logit(base_top3, default=50.0)
        top3_score += (3.5 - ai_plus_rank) * 0.12
        top3_score += (3.5 - exhibit_rank) * 0.07
        top3_score += (3.5 - st_rank) * 0.04
        top3_score += avg_diff * 1.20
        top3_score += venue_top3_delta * 0.018
        if row.get("double_time"):
            top3_score += 0.14 if boat == 1 else 0.22
        if row.get("super_slit_alert"):
            top3_score += 0.20 if boat in {2, 3} else 0.26
        if right and right.get("super_slit_alert"):
            top3_score -= 0.16 if boat == 1 else 0.08
        if row.get("low_outer_revive"):
            top3_score += 0.16
        if row.get("venue_low_ai_revival"):
            profile = row.get("venue_low_ai_revival_profile") or {}
            top3_pp = as_num(profile.get("top3_rate_pp")) or 0.0
            if profile.get("role") == "third_only":
                top3_score += 0.09
            elif profile.get("strong"):
                top3_score += 0.18
            else:
                top3_score += 0.13
            top3_score += min(max(top3_pp - LOW_AI_VENUE_REVIVAL_MIN_TOP3_PP, 0.0) * 0.008, 0.06)
        if row.get("summer_b1_isshu_factor") == "fast_hold":
            top3_score += 0.14
        elif row.get("summer_b1_isshu_factor") == "slow_fly":
            top3_score -= 0.18
        if row.get("matchup_label") == "1号艇キラー":
            top3_score += 0.14
        elif row.get("matchup_label") == "相性バフ":
            top3_score += 0.12
        elif row.get("matchup_label") == "相性軸バフ":
            top3_score += 0.10
        elif row.get("matchup_label") == "相性デバフ":
            top3_score -= 0.16
        top3_scores.append(sigmoid_pct(top3_score))

    max_score = max(win_scores) if win_scores else 0
    win_weights = [math.exp(score - max_score) for score in win_scores]
    win_rates = normalize_total(win_weights, 100.0, 1.0, 70.0)
    top3_actual_rates = normalize_total(top3_scores, 300.0, 5.0, 92.0)
    top3_share_rates = normalize_total(top3_scores, 100.0, 1.0, 45.0)
    for idx, row in enumerate(rows):
        row["composite_win_pct"] = win_rates[idx]
        row["composite_top3_pct"] = top3_share_rates[idx]
        row["composite_top3_actual_pct"] = top3_actual_rates[idx]
        row["composite_rate_reasons"] = composite_rate_reasons(row, by_boat)


def sorted_boats(rows, keys):
    def sort_key(row):
        out = []
        for key, direction, missing in keys:
            value = row.get(key)
            if value is None:
                value = missing
            out.append(value if direction == "asc" else -value)
        out.append(row["boat_number"])
        return tuple(out)

    return [row["boat_number"] for row in sorted(rows, key=sort_key)]


def unique(seq):
    out = []
    seen = set()
    for item in seq:
        if item is None or item in seen:
            continue
        seen.add(int(item))
        out.append(int(item))
    return out


def add_permuted(tickets, head, supports):
    supports = unique(supports)
    for second in supports:
        for third in supports:
            if len({head, second, third}) == 3:
                tickets.add(f"{head}{second}{third}")


def order_mid(rows):
    mid = [row for row in rows if row["boat_number"] in {2, 3, 4}]
    return sorted_boats(
        mid,
        [
            ("comp_score", "asc", 9),
            ("ai_prediction_pct", "desc", -1),
            ("ai_plus", "desc", -1),
        ],
    )


def order_outer(rows):
    outer = [row for row in rows if row["boat_number"] in {5, 6}]
    return sorted_boats(
        outer,
        [
            ("exhibit_rank", "asc", 9),
            ("value_score", "asc", 9),
            ("ai_prediction_pct", "desc", -1),
        ],
    )


def order_comp(rows, pool=None, exclude=None):
    pool = set(pool or range(1, 7))
    exclude = set(exclude or [])
    selected = [row for row in rows if row["boat_number"] in pool and row["boat_number"] not in exclude]
    return sorted_boats(
        selected,
        [
            ("comp_score", "asc", 9),
            ("ai_prediction_pct", "desc", -1),
            ("ai_plus", "desc", -1),
        ],
    )


def order_value(rows, pool=None, exclude=None):
    pool = set(pool or range(1, 7))
    exclude = set(exclude or [])
    selected = [row for row in rows if row["boat_number"] in pool and row["boat_number"] not in exclude]
    return sorted_boats(
        selected,
        [
            ("value_score", "asc", 9),
            ("exhibit_rank", "asc", 9),
            ("ai_prediction_pct", "desc", -1),
        ],
    )


def rank_boat(rows, key, rank_no):
    ranked = sorted(
        [row for row in rows if row.get(key) is not None],
        key=lambda row: (-row.get(key), row["boat_number"]),
    )
    if 1 <= rank_no <= len(ranked):
        return ranked[rank_no - 1]["boat_number"]
    return None


def rank_boats_for_key(rows, key, ranks=(1, 3)):
    ranked = sorted(
        [row for row in rows if row.get(key) is not None],
        key=lambda row: (-(row.get(key) or 0), row["boat_number"]),
    )
    out = []
    for rank_no in ranks:
        if 1 <= rank_no <= len(ranked):
            out.append(ranked[rank_no - 1]["boat_number"])
    return unique(out)


def axis_boats_by_ai_plus(rows, ranks=(1, 3)):
    return unique(rank_boat(rows, "ai_plus", rank_no) for rank_no in ranks)


def axis_boats_for_roles(rows, ranks=(1, 3)):
    rank_label = "と".join(f"{rank}位" for rank in ranks)
    if sum(1 for row in rows if row.get("ai_plus") is not None) >= max(ranks):
        return rank_boats_for_key(rows, "ai_plus", ranks), f"AI3連対率+一般3連対率の{rank_label}"
    if sum(1 for row in rows if row.get("ai_3ren_pct") is not None) >= max(ranks):
        return rank_boats_for_key(rows, "ai_3ren_pct", ranks), f"AI+一般3連対が不足したためAI3連対率の{rank_label}"
    return rank_boats_for_key(rows, "composite_top3_actual_pct", ranks), f"AI+一般3連対が不足したため複合3着内率の{rank_label}"


def edge_head_boost(boat, metrics):
    boost = 0.0
    reasons = []
    longshot_boats = {
        int(part)
        for part in str(metrics.get("longshot_head_boats") or "").replace("、", ",").split(",")
        if str(part).strip().isdigit()
    }
    if boat in longshot_boats:
        boost += 7
        reasons.append("穴頭候補に一致")
    if int(as_num(metrics.get("low_outer_boat")) or 0) == boat:
        boost += 5
        reasons.append("低評価外枠の復活候補")
    for edge in metrics.get("composite_edges") or []:
        details = edge.get("details") or {}
        signal = str(details.get("signal") or edge.get("id") or "")
        role = str(edge.get("role") or "")
        if signal == "b5_left_adv" and boat == 5:
            boost += 7
            reasons.append("スリットで5号艇が左より良い")
        elif signal == "b6_left_adv" and boat == 6:
            boost += 7
            reasons.append("スリットで6号艇が左より良い")
        elif signal in {"b2_wall_break_3peek", "b3_peek_vs_12"} and boat == 3:
            boost += 5
            reasons.append("3号艇がのぞく形")
        elif signal == "b4_cadou_peek" and boat == 4:
            boost += 5
            reasons.append("4カドがのぞく形")
        elif signal == "outer56_pressure_vs_1" and boat in {5, 6}:
            boost += 4
            reasons.append("5/6外圧")
        elif signal == "outer456_pressure" and boat in {4, 5, 6}:
            boost += 3
            reasons.append("4〜6外圧")
        elif signal == "center34_dent" and boat in {5, 6}:
            boost += 3
            reasons.append("3/4中凹みで外が入りやすい")
        elif signal == "b1_hole_vs_23" and boat == 3:
            boost += 3
            reasons.append("1号艇が凹み3に出番")
        if role == "head_up" and boat in {3, 4, 5, 6}:
            boost += 3
            reasons.append("過去条件で穴頭寄り")
    return boost, reasons[:3]


def b1_unpopular_head_signal(row, metrics):
    trifecta_top5 = int(as_num(metrics.get("b1_trifecta_top5_1head")) or 0) == 1
    top5_head_count = int(as_num(metrics.get("trifecta_top5_head1_count")) or 0)
    top5_count = int(as_num(metrics.get("trifecta_top5_count")) or 0)
    top10_head_count = int(as_num(metrics.get("trifecta_top10_head1_count")) or 0)
    top10_count = int(as_num(metrics.get("trifecta_top10_count")) or 0)
    b1_first_rank = int(as_num(metrics.get("b1_trifecta_first_rank")) or 0) or None
    odds_rank = int(as_num(metrics.get("boat1_odds_rank")) or 0) or None
    odds_pct = as_num(metrics.get("boat1_odds_prediction_pct"))
    has_popularity_data = top5_count >= 5 or top10_count >= 10 or b1_first_rank is not None or odds_rank is not None or odds_pct is not None
    if not has_popularity_data:
        return False, ""
    top5_almost = top5_count >= 5 and top5_head_count >= 4
    top10_backed = top10_count >= 10 and top10_head_count >= 4
    top_rank_backed = b1_first_rank is not None and b1_first_rank <= 5
    odds_heavy = odds_rank == 1 and odds_pct is not None and odds_pct >= 40
    is_unpopular = (not trifecta_top5) and (not top5_almost) and (not top10_backed) and (not top_rank_backed) and (not odds_heavy)
    if not is_unpopular:
        return False, ""

    raw_win = row.get("composite_win_pct")
    if raw_win is None:
        raw_win = row.get("ai_prediction_pct")
    ai_pred = row.get("ai_prediction_pct") or metrics.get("boat1_ai_prediction_pct")
    nige = metrics.get("boat1_nige_pct")
    loss = metrics.get("boat1_loss_pct")
    avg_diff = row.get("avg_isshu_diff") if row.get("avg_isshu_diff") is not None else metrics.get("boat1_avg_isshu_diff")
    ai_plus_rank = row.get("ai_plus_rank") or metrics.get("boat1_ai_plus_order") or 9
    strong_time = (
        bool(row.get("double_time"))
        or (avg_diff is not None and avg_diff >= 0.10)
        or metrics.get("b1_summer_isshu_factor") == "fast_hold"
    )
    strong_head = (
        (raw_win is not None and raw_win >= 42 and (loss is None or loss < 55))
        or ((ai_pred or 0) >= 45 and (nige or 0) >= 50 and (loss is None or loss < 45))
        or ((nige or 0) >= 55 and (loss is None or loss < 35))
        or ((raw_win or 0) >= 35 and strong_time and (loss is None or loss < 50))
        or (ai_plus_rank <= 2 and (nige or 0) >= 50 and (loss is None or loss < 45))
    )
    if not strong_head:
        return False, ""
    popularity_text = "人気薄"
    if odds_rank == 1 and odds_pct is not None:
        popularity_text = f"1号艇オッズ評価{odds_pct:.1f}%"
    elif b1_first_rank is not None:
        popularity_text = f"1号艇頭の三連単初出{b1_first_rank}位"
    elif top5_count >= 5:
        popularity_text = f"人気上位5点中1号艇頭{top5_head_count}点"
    return True, f"{popularity_text}で売れすぎではないが逃げ材料が強い"


def valid_boat_rank(value):
    rank = as_num(value)
    if rank is None or rank < 1 or rank > 6:
        return None
    return rank


B1_POPULARITY_BUY_LEVELS = {"普通に人気", "かなり人気", "売れすぎ"}


def b1_popularity_context_from_values(
    odds_pct=None,
    odds_rank=None,
    trifecta_top5_count=None,
    trifecta_head1_count=None,
    trifecta_head1_flag=None,
    trifecta_top10_count=None,
    trifecta_top10_head1_count=None,
    b1_trifecta_first_rank=None,
):
    top5_count = int(as_num(trifecta_top5_count) or 0)
    head1_count_raw = as_num(trifecta_head1_count)
    head1_count = int(head1_count_raw) if head1_count_raw is not None else None
    top10_count = int(as_num(trifecta_top10_count) or 0)
    head1_top10_raw = as_num(trifecta_top10_head1_count)
    head1_top10_count = int(head1_top10_raw) if head1_top10_raw is not None else None
    first_rank_raw = as_num(b1_trifecta_first_rank)
    first_rank = int(first_rank_raw) if first_rank_raw is not None else None
    if head1_count is None and int(as_num(trifecta_head1_flag) or 0) == 1:
        head1_count = 5
    if top5_count >= 5 and head1_count is not None:
        if head1_count >= 5:
            level = "売れすぎ"
        elif head1_count == 4:
            level = "かなり人気"
        elif head1_count == 3:
            level = "普通に人気"
        elif top10_count >= 10 and head1_top10_count is not None and head1_top10_count >= 4:
            level = "普通に人気"
        else:
            level = "人気不足"
        return {
            "level": level,
            "source": "三連単人気上位5点+頭別分布",
            "is_backed": level in B1_POPULARITY_BUY_LEVELS,
            "head1_count": head1_count,
            "top5_count": top5_count,
            "head1_top10_count": head1_top10_count,
            "top10_count": top10_count,
            "b1_trifecta_first_rank": first_rank,
            "odds_prediction_pct": odds_pct,
            "odds_rank": odds_rank,
        }
    if top10_count >= 10 and head1_top10_count is not None:
        if head1_top10_count >= 7:
            level = "売れすぎ"
        elif head1_top10_count >= 5:
            level = "かなり人気"
        elif head1_top10_count >= 3:
            level = "普通に人気"
        else:
            level = "人気不足"
        return {
            "level": level,
            "source": "三連単人気上位10点",
            "is_backed": level in B1_POPULARITY_BUY_LEVELS,
            "head1_count": head1_count,
            "top5_count": top5_count,
            "head1_top10_count": head1_top10_count,
            "top10_count": top10_count,
            "b1_trifecta_first_rank": first_rank,
            "odds_prediction_pct": odds_pct,
            "odds_rank": odds_rank,
        }
    if first_rank is not None:
        level = "普通に人気" if first_rank <= 3 else "人気不足"
        return {
            "level": level,
            "source": "三連単1号艇頭の初出順位",
            "is_backed": level in B1_POPULARITY_BUY_LEVELS,
            "head1_count": head1_count,
            "top5_count": top5_count,
            "head1_top10_count": head1_top10_count,
            "top10_count": top10_count,
            "b1_trifecta_first_rank": first_rank,
            "odds_prediction_pct": odds_pct,
            "odds_rank": odds_rank,
        }

    pct = as_num(odds_pct)
    rank = as_num(odds_rank)
    if pct is None:
        level = "未取得"
        backed = False
    elif rank != 1 or pct < 40:
        level = "人気不足"
        backed = False
    elif pct < 45:
        level = "普通に人気"
        backed = True
    elif pct < 55:
        level = "かなり人気"
        backed = True
    else:
        level = "売れすぎ"
        backed = True
    return {
        "level": level,
        "source": "BOATERS AIオッズ評価" if pct is not None else "未取得",
        "is_backed": backed,
        "head1_count": head1_count,
        "top5_count": top5_count,
        "odds_prediction_pct": pct,
        "odds_rank": rank,
        "head1_top10_count": head1_top10_count,
        "top10_count": top10_count,
        "b1_trifecta_first_rank": first_rank,
    }


def b1_popularity_context(metrics):
    explicit_level = metrics.get("popular_b1_popularity_level") or metrics.get("b1_popularity_level")
    if explicit_level:
        return {
            "level": str(explicit_level),
            "source": str(metrics.get("popular_b1_popularity_source") or metrics.get("b1_popularity_source") or ""),
            "is_backed": str(explicit_level) in B1_POPULARITY_BUY_LEVELS,
        }
    if metrics.get("popular_b1_is_popular"):
        return {
            "level": "かなり人気",
            "source": str(metrics.get("popular_b1_source") or "旧データ人気フラグ"),
            "is_backed": True,
        }
    return b1_popularity_context_from_values(
        odds_pct=metrics.get("boat1_odds_prediction_pct"),
        odds_rank=metrics.get("boat1_odds_rank"),
        trifecta_top5_count=metrics.get("trifecta_top5_count"),
        trifecta_head1_count=metrics.get("trifecta_top5_head1_count"),
        trifecta_head1_flag=metrics.get("b1_trifecta_top5_1head"),
        trifecta_top10_count=metrics.get("trifecta_top10_count"),
        trifecta_top10_head1_count=metrics.get("trifecta_top10_head1_count"),
        b1_trifecta_first_rank=metrics.get("b1_trifecta_first_rank"),
    )


def b1_publicly_backed(metrics):
    return b1_popularity_context(metrics).get("level") in B1_POPULARITY_BUY_LEVELS


def b1_unpopular_head_value(rows, metrics):
    """Return True when 1号艇 is unpopular but still worth treating as a head candidate."""
    if b1_publicly_backed(metrics):
        return False, ""
    b1_row = row_by_boat(rows, 1)
    if not b1_row:
        return False, ""
    return b1_unpopular_head_signal(b1_row, metrics)


def b1_data_danger(metrics):
    level = str(metrics.get("popular_b1_fly_level") or "")
    score = as_num(metrics.get("popular_b1_fly_score")) or 0
    return level in {"危険", "超危険"} or score >= 70


def b1_exhibition_double_debuff(metrics):
    tenji_rank = valid_boat_rank(metrics.get("boat1_tenji_time_rank") or metrics.get("boat1_tenji_rank"))
    isshu_rank = valid_boat_rank(metrics.get("boat1_isshu_rank"))
    return tenji_rank is not None and isshu_rank is not None and tenji_rank > 3 and isshu_rank > 3


def b1_exhibition_filtered_debuff(metrics):
    if metrics.get("venue_b1_head_debuff"):
        return True
    tenji_rank = valid_boat_rank(metrics.get("boat1_tenji_time_rank") or metrics.get("boat1_tenji_rank"))
    isshu_rank = valid_boat_rank(metrics.get("boat1_isshu_rank"))
    avg_diff = as_num(metrics.get("boat1_avg_isshu_diff"))
    one_rank_weak = (tenji_rank is not None and tenji_rank > 3) or (isshu_rank is not None and isshu_rank > 3)
    return one_rank_weak and avg_diff is not None and avg_diff < 0


def b1_odds_gap_strong(metrics):
    return b1_publicly_backed(metrics) and b1_data_danger(metrics) and b1_exhibition_double_debuff(metrics)


def b1_odds_gap_filtered(metrics, round_no=None):
    if round_no is not None and round_no > 6:
        return False
    return b1_publicly_backed(metrics) and b1_data_danger(metrics) and b1_exhibition_filtered_debuff(metrics)


def ai_rank6_exhibit_top2(metrics):
    tenji_rank = valid_boat_rank(metrics.get("ai_rank6_tenji_rank"))
    isshu_rank = valid_boat_rank(metrics.get("ai_rank6_isshu_rank"))
    return (tenji_rank is not None and tenji_rank <= 2) or (isshu_rank is not None and isshu_rank <= 2)


def b1_recovery_manshu_power_signal(metrics):
    """Strict recovery gate for 25-40% manshu-rate alerts.

    The broad "popular 1 is dangerous" signal catches too many non-manshu
    races.  This gate keeps only cases where 1 is weak on exhibition and a
    low/outer counter-signal is visible.
    """
    popularity_level = b1_popularity_context(metrics).get("level") or "不明"
    b1_isshu_rank = valid_boat_rank(metrics.get("boat1_isshu_rank"))
    outer56_top2_count = int(as_num(metrics.get("outer56_exhibit_top2_count")) or 0)
    rank6_top2 = ai_rank6_exhibit_top2(metrics)
    ok = (
        popularity_level != "売れすぎ"
        and b1_isshu_rank is not None
        and b1_isshu_rank >= 4
        and (outer56_top2_count >= 2 or rank6_top2)
    )
    checks = [
        f"1号艇人気が売れすぎではない:{'OK' if popularity_level != '売れすぎ' else 'NG'}({popularity_level})",
        f"1号艇1周4位以下:{'OK' if b1_isshu_rank is not None and b1_isshu_rank >= 4 else 'NG'}({fmt_role(b1_isshu_rank)}位)",
        (
            "5/6展示上位2艇またはAI最下位艇浮上:"
            f"{'OK' if outer56_top2_count >= 2 or rank6_top2 else 'NG'}"
            f"(5/6上位{outer56_top2_count}艇 / AI最下位浮上{'あり' if rank6_top2 else 'なし'})"
        ),
    ]
    return ok, checks


def outer56_best_ai_3ren_pct(rows):
    values = []
    for row in rows or []:
        if row.get("boat_number") not in {5, 6}:
            continue
        value = as_num(row.get("ai_3ren_pct"))
        if value is None:
            value = as_num(row.get("composite_top3_actual_pct"))
        if value is not None:
            values.append(value)
    return max(values) if values else None


def head_candidate_score(row, manshu_head_mode=False):
    boat = row["boat_number"]
    metrics = row.get("_morning_metrics") or {}
    score = row.get("composite_win_pct")
    if score is None:
        score = row.get("ai_prediction_pct")
    if score is None:
        score = {1: 53, 2: 14, 3: 13, 4: 10, 5: 6, 6: 4}.get(boat, 10)
    reasons = [f"複合1着率{score:.1f}%"]
    if manshu_head_mode and boat in {3, 4, 5, 6}:
        score += 8
        reasons.append("万舟は3〜6号艇頭が多い")
        edge_boost, edge_reasons = edge_head_boost(boat, metrics)
        if edge_boost:
            score += edge_boost
            reasons.extend(edge_reasons)
    if boat == 1:
        danger = as_num(metrics.get("popular_b1_fly_score")) or 0
        loss = as_num(metrics.get("boat1_loss_pct"))
        unpopular_hold, unpopular_reason = b1_unpopular_head_signal(row, metrics)
        if unpopular_hold:
            score += 12
            reasons.append(unpopular_reason)
        if danger >= 75:
            score -= 18
            reasons.append("人気1号艇の超危険で下げ")
        elif danger >= 60:
            score -= 12
            reasons.append("人気1号艇の危険で下げ")
        elif loss is not None and loss >= 55:
            score -= 7
            reasons.append(f"逃げ失敗{loss:.1f}%で下げ")
        if metrics.get("b1_summer_isshu_factor") == "fast_hold":
            score += 5
            reasons.append("夏場1周が良くイン残り寄り")
        elif metrics.get("b1_summer_isshu_factor") == "slow_fly":
            score -= 6
            reasons.append("夏場1周が悪くイン飛び寄り")
    first_rank = as_num(metrics.get(f"b{boat}_trifecta_first_rank"))
    top10_head_count = as_num(metrics.get(f"b{boat}_trifecta_top10_head_count")) or 0
    if first_rank is not None:
        if first_rank <= 5:
            score += 3
            reasons.append(f"三連単頭初出{int(first_rank)}位")
        elif first_rank >= 20 and (row.get("ai_prediction_pct") or 0) < 5:
            score -= 3
            reasons.append(f"三連単頭初出{int(first_rank)}位で頭薄い")
    elif top10_head_count >= 2:
        score += 2
        reasons.append(f"上位10点に頭{int(top10_head_count)}点")
    recent_win = as_num(row.get("recent10_win_pct") or metrics.get(f"b{boat}_recent10_win_pct"))
    recent_top3 = as_num(row.get("recent10_top3_pct") or metrics.get(f"b{boat}_recent10_top3_pct"))
    recent_st_rank = as_num(row.get("recent10_st_rank_avg") or metrics.get(f"b{boat}_recent10_st_rank_avg"))
    recent_sashi = as_num(row.get("recent10_sashi_rate") or metrics.get(f"b{boat}_recent10_sashi_rate")) or 0
    recent_makuri = as_num(row.get("recent10_makuri_rate") or metrics.get(f"b{boat}_recent10_makuri_rate")) or 0
    recent_makurizashi = as_num(row.get("recent10_makurizashi_rate") or metrics.get(f"b{boat}_recent10_makurizashi_rate")) or 0
    if recent_win is not None:
        if recent_win >= 20:
            score += 4
            reasons.append(f"直近10走枠1着{recent_win:.0f}%")
        elif recent_win >= 10:
            score += 2
            reasons.append(f"直近10走枠1着{recent_win:.0f}%")
    if recent_top3 is not None and recent_top3 >= 60:
        score += 1
        reasons.append(f"直近10走枠3連対{recent_top3:.0f}%")
    if recent_st_rank is not None and recent_st_rank <= 2.5:
        score += 1.5
        reasons.append(f"直近10走ST順位{recent_st_rank:.1f}")
    if boat == 2 and recent_sashi >= 20:
        score += 2
        reasons.append(f"直近10走差し{recent_sashi:.0f}%")
    if boat in {3, 4, 5, 6} and max(recent_makuri, recent_makurizashi) >= 15:
        score += 2
        reasons.append("直近10走で攻め切り実績")
    if row.get("double_time"):
        score += 7
        reasons.append("ダブルタイム")
    if row.get("super_slit_alert"):
        score += 7 if boat in {2, 3} else 9
        reasons.append("スーパースリット")
    if row.get("low_outer_revive"):
        score += 5
        reasons.append("低評価外枠の展示復活")
    if row.get("venue_low_ai_revival"):
        profile = row.get("venue_low_ai_revival_profile") or {}
        if profile.get("head_ok"):
            score += 4
            reasons.extend((row.get("venue_low_ai_revival_reasons") or ["場別展示バフで低評価艇復活"])[:1])
    if row.get("longshot_head_candidate"):
        score += 5
        reasons.append("人気薄頭候補")
    avg_diff = row.get("avg_isshu_diff")
    if avg_diff is not None:
        if avg_diff >= 0.20:
            score += 5
            reasons.append(f"展示+1周平均との差+{avg_diff:.2f}")
        elif avg_diff >= 0.10:
            score += 3
            reasons.append(f"展示+1周平均との差+{avg_diff:.2f}")
        elif avg_diff <= -0.10:
            score -= 3
            reasons.append(f"展示+1周平均との差{avg_diff:.2f}")
    if (row.get("exhibit_rank") or 9) <= 2:
        score += 3
        reasons.append("展示か1周が2位以内")
    ai_plus_rank = row.get("ai_plus_rank")
    if ai_plus_rank and ai_plus_rank <= 2:
        score += 2
        reasons.append(f"AI+{int(ai_plus_rank)}位")
    elif ai_plus_rank and ai_plus_rank >= 5:
        score -= 2
        reasons.append(f"AI+{int(ai_plus_rank)}位")
    if boat in {5, 6} and metrics.get("slit_outer56_pressure_vs_1"):
        score += 2.5
        reasons.append("5/6外圧")
    venue_delta = as_num(row.get("venue_head_score_delta")) or 0
    if venue_delta >= 2:
        score += min(venue_delta, 7)
        reasons.extend((row.get("venue_factor_reasons") or ["場別展示バフ"])[:1])
    elif venue_delta <= -2:
        score += max(venue_delta, -7)
        reasons.extend((row.get("venue_factor_reasons") or ["場別展示デバフ"])[:1])
    return round(score, 3), reasons[:4]


def attack_candidate_score(row):
    """Race-breaker score: who can make the race rough, not who necessarily wins."""
    boat = row["boat_number"]
    metrics = row.get("_morning_metrics") or {}
    score = 0.0
    reasons = []
    edge_boost, edge_reasons = edge_head_boost(boat, metrics)
    if edge_boost:
        score += edge_boost
        reasons.extend(edge_reasons)
    straight_rank = valid_boat_rank(row.get("chokusen_rank"))
    start_rank = valid_boat_rank(row.get("start_tenji_rank") or row.get("start_tenji_time_rank"))
    exhibit_rank = valid_boat_rank(row.get("exhibit_rank") or row.get("tenji_rank") or row.get("tenji_time_rank"))
    if straight_rank is not None and straight_rank <= 2:
        score += 6
        reasons.append(f"直線{int(straight_rank)}位")
    if start_rank is not None and start_rank <= 2:
        score += 5
        reasons.append(f"展示ST{int(start_rank)}位")
    if exhibit_rank is not None and exhibit_rank <= 2:
        score += 3
        reasons.append(f"展示{int(exhibit_rank)}位")
    if row.get("super_slit_alert"):
        score += 8
        reasons.append("スーパースリット")
    if row.get("low_outer_revive"):
        score += 5
        reasons.append("低評価外枠の復活")
    if row.get("venue_low_ai_revival"):
        profile = row.get("venue_low_ai_revival_profile") or {}
        if profile.get("role") in {"second_third", "head_ok"}:
            score += 3
            reasons.extend((row.get("venue_low_ai_revival_reasons") or ["場別展示バフで低評価艇復活"])[:1])
    recent_st_rank = as_num(row.get("recent10_st_rank_avg") or metrics.get(f"b{boat}_recent10_st_rank_avg"))
    if recent_st_rank is not None and recent_st_rank <= 2.5:
        score += 3
        reasons.append(f"直近10走ST順位{recent_st_rank:.1f}")
    recent_attack_rate = max(
        as_num(row.get("recent10_makuri_rate") or metrics.get(f"b{boat}_recent10_makuri_rate")) or 0,
        as_num(row.get("recent10_makurizashi_rate") or metrics.get(f"b{boat}_recent10_makurizashi_rate")) or 0,
    )
    if boat in {3, 4, 5, 6} and recent_attack_rate >= 15:
        score += 3
        reasons.append(f"直近10走攻め決まり手{recent_attack_rate:.0f}%")
    if boat in {4, 5, 6}:
        score += 2
        reasons.append("外から攻める枠")
    avg_diff = row.get("avg_isshu_diff")
    if avg_diff is not None and avg_diff >= 0.10:
        score += 3
        reasons.append(f"展示+1周平均との差+{avg_diff:.2f}")
    venue_delta = as_num(row.get("venue_head_score_delta")) or 0
    if venue_delta >= 2:
        score += min(venue_delta * 0.7, 5)
        reasons.extend((row.get("venue_factor_reasons") or ["場別展示バフ"])[:1])
    return round(score, 3), reasons[:5]


def finish_head_candidate_score(row):
    """Finisher score: who can actually take 1st after the race gets rough."""
    boat = row["boat_number"]
    metrics = row.get("_morning_metrics") or {}
    base = row.get("composite_win_pct")
    if base is None:
        base = row.get("ai_prediction_pct")
    if base is None:
        base = 0.0
    score = float(base)
    reasons = [f"複合1着率{score:.1f}%"]

    ai_pred = row.get("ai_prediction_pct") or 0
    if ai_pred >= 10:
        score += 6
        reasons.append(f"AI1着率{ai_pred:.1f}%")
    elif ai_pred >= 5:
        score += 3
        reasons.append(f"AI1着率{ai_pred:.1f}%")
    else:
        score -= 8
        reasons.append(f"AI1着率{ai_pred:.1f}%で頭弱い")

    first_rank = as_num(metrics.get(f"b{boat}_trifecta_first_rank"))
    top10_head_count = as_num(metrics.get(f"b{boat}_trifecta_top10_head_count")) or 0
    min_head_odds = as_num(metrics.get(f"b{boat}_trifecta_min_head_odds"))
    if first_rank is not None:
        if first_rank <= 5:
            score += 3
            reasons.append(f"三連単頭初出{int(first_rank)}位")
        elif first_rank <= 12:
            score += 1
            reasons.append(f"三連単頭初出{int(first_rank)}位で妙味")
        elif ai_pred < 5:
            score -= 3
            reasons.append(f"三連単頭初出{int(first_rank)}位で市場も頭薄い")
        elif min_head_odds is not None and min_head_odds >= 60:
            score += 1
            reasons.append(f"頭最小オッズ{min_head_odds:.1f}倍で妙味")
    elif top10_head_count >= 2:
        score += 2
        reasons.append(f"上位10点に頭{int(top10_head_count)}点")

    recent_win = as_num(row.get("recent10_win_pct") or metrics.get(f"b{boat}_recent10_win_pct"))
    recent_top3 = as_num(row.get("recent10_top3_pct") or metrics.get(f"b{boat}_recent10_top3_pct"))
    recent_sashi = as_num(row.get("recent10_sashi_rate") or metrics.get(f"b{boat}_recent10_sashi_rate")) or 0
    recent_makuri = as_num(row.get("recent10_makuri_rate") or metrics.get(f"b{boat}_recent10_makuri_rate")) or 0
    recent_makurizashi = as_num(row.get("recent10_makurizashi_rate") or metrics.get(f"b{boat}_recent10_makurizashi_rate")) or 0
    if recent_win is not None:
        if recent_win >= 20:
            score += 4
            reasons.append(f"直近10走枠1着{recent_win:.0f}%")
        elif recent_win >= 10:
            score += 2
            reasons.append(f"直近10走枠1着{recent_win:.0f}%")
    if recent_top3 is not None and recent_top3 >= 60:
        score += 1
        reasons.append(f"直近10走枠3連対{recent_top3:.0f}%")
    if boat == 2 and recent_sashi >= 20:
        score += 2
        reasons.append(f"直近10走差し{recent_sashi:.0f}%")
    if boat in {3, 4, 5, 6} and max(recent_makuri, recent_makurizashi) >= 15:
        score += 2
        reasons.append("直近10走で攻め切り実績")

    ai_plus_rank = valid_boat_rank(row.get("ai_plus_rank"))
    if ai_plus_rank is not None and ai_plus_rank <= 3:
        score += 4
        reasons.append(f"AI+{int(ai_plus_rank)}位")
    elif ai_plus_rank is not None and ai_plus_rank >= 5:
        score -= 3
        reasons.append(f"AI+{int(ai_plus_rank)}位で弱い")

    exhibit_rank = valid_boat_rank(row.get("exhibit_rank") or row.get("tenji_rank") or row.get("tenji_time_rank"))
    isshu_rank = valid_boat_rank(row.get("isshu_rank"))
    mawari_rank = valid_boat_rank(row.get("mawariashi_rank"))
    straight_rank = valid_boat_rank(row.get("chokusen_rank"))
    start_rank = valid_boat_rank(row.get("start_tenji_rank") or row.get("start_tenji_time_rank"))

    for label, rank, plus, minus in (
        ("展示", exhibit_rank, 4, 4),
        ("1周", isshu_rank, 4, 4),
        ("回り足", mawari_rank, 4, 3),
    ):
        if rank is None:
            continue
        if rank <= 2:
            score += plus
            reasons.append(f"{label}{int(rank)}位")
        elif rank >= 4:
            score -= minus
            reasons.append(f"{label}{int(rank)}位で頭弱い")

    avg_diff = row.get("avg_isshu_diff")
    if avg_diff is not None:
        if avg_diff >= 0.10:
            score += 4
            reasons.append(f"展示+1周平均との差+{avg_diff:.2f}")
        elif avg_diff <= -0.10:
            score -= 4
            reasons.append(f"展示+1周平均との差{avg_diff:.2f}で頭弱い")

    if straight_rank is not None and straight_rank <= 2 and start_rank is not None and start_rank <= 2:
        score += 3
        reasons.append("直線+展示STで攻め切り材料")
    elif straight_rank is not None and straight_rank <= 2:
        score += 1
        reasons.append("直線上位")

    edge_boost, edge_reasons = edge_head_boost(boat, metrics)
    if edge_boost:
        score += min(edge_boost * 0.35, 4)
        reasons.extend(edge_reasons[:1])

    if boat == 4 and ai_pred < 5 and mawari_rank is not None and mawari_rank >= 4:
        score -= 5
        reasons.append("4カド攻め材料はあるが回り足/AI頭が弱く2着寄り")
    if boat in {5, 6} and ai_pred < 3 and mawari_rank is not None and mawari_rank >= 4:
        score -= 4
        reasons.append("外枠穴だが頭より3着穴寄り")
    if row.get("venue_low_ai_revival"):
        profile = row.get("venue_low_ai_revival_profile") or {}
        if profile.get("head_ok"):
            score += 4
            reasons.extend((row.get("venue_low_ai_revival_reasons") or ["場別展示バフで頭まで候補"])[:1])
    venue_delta = as_num(row.get("venue_head_score_delta")) or 0
    if venue_delta >= 2:
        score += min(venue_delta, 7)
        reasons.extend((row.get("venue_factor_reasons") or ["場別展示バフ"])[:1])
    elif venue_delta <= -2:
        score += max(venue_delta, -7)
        reasons.extend((row.get("venue_factor_reasons") or ["場別展示デバフ"])[:1])
    return round(score, 3), reasons[:6]


def role_split_details(rows, exclude=None):
    exclude = set(exclude or [])
    attack = []
    finish = []
    support = []
    for row in rows:
        boat = row["boat_number"]
        if boat in exclude:
            continue
        attack_score, attack_reasons = attack_candidate_score(row)
        finish_score, finish_reasons = finish_head_candidate_score(row)
        top3 = row.get("composite_top3_actual_pct")
        if top3 is None:
            top3 = row.get("ai_plus") or row.get("ai_3ren_pct") or 0
        support_reasons = []
        if row.get("ai_plus_rank") is not None:
            support_reasons.append(f"AI+{int(row.get('ai_plus_rank'))}位")
        if row.get("composite_top3_actual_pct") is not None:
            support_reasons.append(f"複合3着内率{row.get('composite_top3_actual_pct'):.1f}%")
        support.append((float(top3), boat, support_reasons[:3]))
        attack.append((attack_score, boat, attack_reasons))
        finish.append((finish_score, boat, finish_reasons))
    attack.sort(key=lambda item: (-item[0], item[1]))
    finish.sort(key=lambda item: (-item[0], item[1]))
    support.sort(key=lambda item: (-item[0], item[1]))
    return {
        "attackers": [boat for _score, boat, _reasons in attack[:3]],
        "attack_scores": {str(boat): {"score": score, "reasons": reasons} for score, boat, reasons in attack[:4]},
        "finishers": [boat for _score, boat, _reasons in finish[:3]],
        "finisher_scores": {str(boat): {"score": score, "reasons": reasons} for score, boat, reasons in finish[:4]},
        "support_boats": [boat for _score, boat, _reasons in support[:4]],
        "support_scores": {str(boat): {"score": round(score, 3), "reasons": reasons} for score, boat, reasons in support[:4]},
        "role_split_note": "荒れる材料を作る攻め艇と、1着を取り切る頭候補を別々に評価",
    }


def finish_head_score_details(rows, heads):
    details = {}
    for row in rows:
        boat = row["boat_number"]
        if boat not in set(heads):
            continue
        score, reasons = finish_head_candidate_score(row)
        details[str(boat)] = {"score": score, "reasons": reasons}
    return details


def odds_gap_head_candidate_score(row):
    """Score heads for 1号艇人気なのに危険な歪み本命."""
    boat = row["boat_number"]
    metrics = row.get("_morning_metrics") or {}
    base = as_num(row.get("composite_win_pct"))
    if base is None:
        base = as_num(row.get("ai_prediction_pct"))
    if base is None:
        base = 0.0
    score = float(base)
    reasons = [f"複合1着率{score:.1f}%"]

    ai_pred = as_num(row.get("ai_prediction_pct")) or 0.0
    # In this segment, historical checks favored composite win rate as the
    # main sorter. AI/display only break close calls so we do not over-steer
    # away from the strongest signal.
    if ai_pred >= 12:
        score += 0.18
        reasons.append(f"AI1着率{ai_pred:.1f}%")
    elif ai_pred >= 8:
        score += 0.10
        reasons.append(f"AI1着率{ai_pred:.1f}%")
    elif ai_pred < 3:
        score -= 0.10
        reasons.append(f"AI1着率{ai_pred:.1f}%で頭薄い")

    exhibit_rank = valid_boat_rank(row.get("exhibit_rank") or row.get("tenji_rank") or row.get("tenji_time_rank"))
    isshu_rank = valid_boat_rank(row.get("isshu_rank"))
    mawari_rank = valid_boat_rank(row.get("mawariashi_rank"))
    for label, rank in (("展示", exhibit_rank), ("1周", isshu_rank), ("回り足", mawari_rank)):
        if rank is None:
            continue
        if rank <= 2:
            score += 0.10
            reasons.append(f"{label}{int(rank)}位")
        elif rank >= 5:
            score -= 0.08
            reasons.append(f"{label}{int(rank)}位")

    avg_diff = as_num(row.get("avg_isshu_diff"))
    if avg_diff is not None:
        if avg_diff >= 0.10:
            score += 0.10
            reasons.append(f"展示+1周平均との差+{avg_diff:.2f}")
        elif avg_diff <= -0.10:
            score -= 0.10
            reasons.append(f"展示+1周平均との差{avg_diff:.2f}")

    ai_plus_rank = valid_boat_rank(row.get("ai_plus_rank"))
    if ai_plus_rank is not None:
        if ai_plus_rank <= 3:
            score += 0.08
            reasons.append(f"AI+{int(ai_plus_rank)}位")
        elif ai_plus_rank >= 5:
            score -= 0.06
            reasons.append(f"AI+{int(ai_plus_rank)}位")

    first_rank = as_num(metrics.get(f"b{boat}_trifecta_first_rank"))
    top10_head_count = as_num(metrics.get(f"b{boat}_trifecta_top10_head_count")) or 0
    if first_rank is not None:
        if first_rank <= 5:
            score += 0.06
            reasons.append(f"三連単頭初出{int(first_rank)}位")
        elif first_rank >= 30 and ai_pred < 5:
            score -= 0.06
            reasons.append(f"三連単頭初出{int(first_rank)}位")
    elif top10_head_count >= 2:
        score += 0.04
        reasons.append(f"上位10点に頭{int(top10_head_count)}点")

    recent_win = as_num(row.get("recent10_win_pct") or metrics.get(f"b{boat}_recent10_win_pct"))
    recent_top3 = as_num(row.get("recent10_top3_pct") or metrics.get(f"b{boat}_recent10_top3_pct"))
    if recent_win is not None and recent_win >= 15:
        score += 0.06
        reasons.append(f"直近10走枠1着{recent_win:.0f}%")
    if recent_top3 is not None and recent_top3 >= 60:
        score += 0.04
        reasons.append(f"直近10走枠3連対{recent_top3:.0f}%")

    if row.get("double_time"):
        score += 0.06
        reasons.append("ダブルタイム")
    if row.get("super_slit_alert"):
        score += 0.06
        reasons.append("スーパースリット")

    return round(score, 3), reasons[:6]


def odds_gap_head_candidates(rows, exclude=None, count=2):
    exclude = set(exclude or [])
    scored = []
    for row in rows:
        boat = row["boat_number"]
        if boat in exclude:
            continue
        score, reasons = odds_gap_head_candidate_score(row)
        scored.append((score, boat, reasons))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [boat for _score, boat, _reasons in scored[:count]]


def odds_gap_head_score_details(rows, heads):
    details = {}
    for row in rows:
        boat = row["boat_number"]
        if boat not in set(heads):
            continue
        score, reasons = odds_gap_head_candidate_score(row)
        details[str(boat)] = {"score": score, "reasons": reasons}
    return details


def inner_head_exception(row, outer_cut_score):
    boat = row["boat_number"]
    metrics = row.get("_morning_metrics") or {}
    raw_score, _ = head_candidate_score(row, manshu_head_mode=False)
    if boat == 1:
        unpopular_hold, _ = b1_unpopular_head_signal(row, metrics)
        if unpopular_hold and raw_score >= outer_cut_score + 4:
            return True
        if raw_score < outer_cut_score + 10:
            return False
        danger = as_num(metrics.get("popular_b1_fly_score")) or 0
        loss = as_num(metrics.get("boat1_loss_pct"))
        nige = as_num(metrics.get("boat1_nige_pct"))
        return (
            raw_score >= 42
            and danger < 45
            and (loss is None or loss < 45)
            and (nige is None or nige >= 50)
        )
    if raw_score < outer_cut_score + 10:
        return False
    if boat == 2:
        avg_diff = row.get("avg_isshu_diff")
        exhibit_rank = row.get("exhibit_rank") or 9
        ai_plus_rank = row.get("ai_plus_rank") or 9
        has_strong_push = (
            bool(row.get("double_time"))
            or bool(row.get("super_slit_alert"))
            or exhibit_rank == 1
            or (avg_diff is not None and avg_diff >= 0.20)
            or ai_plus_rank == 1
        )
        return raw_score >= 30 and has_strong_push
    return False


def head_boats_for_arunashi(rows, exclude=None):
    exclude = set(exclude or [])
    outer_scored = []
    inner_scored = []
    for row in rows:
        if row["boat_number"] in exclude:
            continue
        score, _ = head_candidate_score(row, manshu_head_mode=True)
        if row["boat_number"] in {3, 4, 5, 6}:
            outer_scored.append((score, row["boat_number"]))
        else:
            inner_scored.append((score, row["boat_number"]))
    outer_scored.sort(key=lambda item: (-item[0], item[1]))
    inner_scored.sort(key=lambda item: (-item[0], item[1]))
    heads = [boat for _, boat in outer_scored[:2]]
    if len(heads) < 2:
        return unique(heads + [boat for _, boat in inner_scored])[:2]
    if inner_scored:
        cut_score = outer_scored[1][0]
        for _, boat in inner_scored:
            row = next((item for item in rows if item["boat_number"] == boat), {})
            if inner_head_exception(row, cut_score):
                return [heads[0], boat]
    return heads


def head_score_details(rows, heads):
    details = {}
    for row in rows:
        boat = row["boat_number"]
        if boat not in set(heads):
            continue
        score, reasons = head_candidate_score(row, manshu_head_mode=True)
        if boat in {1, 2}:
            reasons = reasons[:3] + ["例外的に内側の頭力が高い"]
        details[str(boat)] = {"score": score, "reasons": reasons}
    return details


def row_by_boat(rows, boat):
    return next((row for row in rows if row.get("boat_number") == boat), {})


def revive_reasons(row):
    reasons = []
    if row.get("double_time"):
        reasons.append("ダブルタイム")
    if row.get("super_slit_alert"):
        reasons.append("スーパースリット")
    if row.get("low_outer_revive"):
        reasons.append("低評価外枠の展示復活")
    for reason in row.get("venue_low_ai_revival_reasons") or []:
        reasons.append(reason)
    exhibit_rank = valid_boat_rank(row.get("exhibit_rank") or row.get("tenji_rank") or row.get("tenji_time_rank"))
    isshu_rank = valid_boat_rank(row.get("isshu_rank"))
    if (exhibit_rank is not None and exhibit_rank <= 2) or (isshu_rank is not None and isshu_rank <= 2):
        reasons.append("展示か1周が2位以内")
    avg_isshu_diff = as_num(row.get("avg_isshu_diff"))
    if avg_isshu_diff is not None and avg_isshu_diff >= 0.10:
        reasons.append("展示+1周平均との差が良い")
    if str(row.get("matchup_label") or "") in {"1号艇キラー", "相性バフ", "相性軸バフ"}:
        reasons.append(row.get("matchup_label"))
    if row.get("venue_dont_keshi"):
        reasons.append("場別展示S/Aで消し禁止")
    return reasons


def select_keshi_boat(rows, protected=None):
    protected = set(protected or [])
    venue_protected = {row["boat_number"] for row in rows if row.get("venue_dont_keshi")}
    if venue_protected and len(venue_protected) < len(rows):
        protected |= venue_protected
    candidates = sorted(
        rows,
        key=lambda row: (
            row.get("ai_plus") if row.get("ai_plus") is not None else 999,
            row.get("ai_3ren_pct") if row.get("ai_3ren_pct") is not None else 999,
            row.get("ai_prediction_pct") if row.get("ai_prediction_pct") is not None else 999,
            row["boat_number"],
        ),
    )
    if not candidates:
        return None, "消し候補を作れるデータがありません", None, []
    last = candidates[0]
    last_revival = revive_reasons(last)
    chosen = next((row for row in candidates if row["boat_number"] not in protected), last)
    if last_revival and len(candidates) >= 2:
        for candidate in candidates[1:]:
            if candidate["boat_number"] not in protected and len(revive_reasons(candidate)) < len(last_revival):
                chosen = candidate
                break
    last_boat = last["boat_number"]
    if chosen["boat_number"] == last_boat:
        reason = (
            f"AI3連対率+一般3連対率が6位({fmt_pct(last.get('ai_plus'))})で、"
            f"展示・一周・スリットの復活材料が弱い"
        )
    elif last_boat in venue_protected:
        reason = (
            f"AI3連対率+一般3連対率6位の{last_boat}号艇は"
            f"場別展示S/Aで残す。代わりに{chosen['boat_number']}号艇を消し"
        )
    elif last_boat in protected:
        reason = (
            f"AI3連対率+一般3連対率6位の{last_boat}号艇は軸候補なので消さない。"
            f"次に消せる根拠が強い{chosen['boat_number']}号艇を消し"
        )
    else:
        reason = (
            f"AI3連対率+一般3連対率6位の{last_boat}号艇は"
            f"{'、'.join(last_revival)}があり残す。"
            f"代わりに{chosen['boat_number']}号艇を消し"
        )
    return chosen["boat_number"], reason, last_boat, last_revival


def ticket_priority(ticket, heads, axes, row_lookup=None):
    boats = combo_boats(ticket)
    if len(boats) != 3:
        return -999
    head = boats[0]
    score = 0
    if head in heads:
        score += 8 - heads.index(head)
    if head in {3, 4, 5, 6}:
        score += 4
    if any(boat in {5, 6} for boat in boats):
        score += 3
    if any(boat in set(axes or []) for boat in boats[1:]):
        score += 2
    if boats[1] in set(axes or []):
        score += 1
    row_lookup = row_lookup or {}
    for idx, boat in enumerate(boats):
        row = row_lookup.get(boat) or {}
        profile = row.get("venue_low_ai_revival_profile") or {}
        score += avgdiff_ticket_score_bonus(row, "head" if idx == 0 else "top3")
        if not profile:
            continue
        role = profile.get("role")
        if idx == 0:
            score += 2 if profile.get("head_ok") else -3
        elif idx == 1:
            score += 2 if role in {"second_third", "head_ok"} else -2
        elif idx == 2:
            score += 3
    if boats[1] == 1 and boats[2] == 2:
        score -= 2
    return score


def trim_tickets(tickets, heads, axes, max_points=15, rows=None):
    if len(tickets) <= max_points:
        return tickets
    row_lookup = {row["boat_number"]: row for row in rows or []}
    ordered = sorted(tickets, key=lambda ticket: (-ticket_priority(ticket, heads, axes, row_lookup), ticket))
    return set(ordered[:max_points])


def trim_tickets_balanced_heads(tickets, heads, axes, max_points=BUY_TICKET_MAX_POINTS, rows=None):
    if len(tickets) <= max_points:
        return set(tickets)
    row_lookup = {row["boat_number"]: row for row in rows or []}
    ordered = sorted(tickets, key=lambda ticket: (-ticket_priority(ticket, heads, axes, row_lookup), ticket))
    available_heads = [
        head
        for head in heads or []
        if any((combo_boats(ticket) or [None])[0] == head for ticket in ordered)
    ]
    if not available_heads:
        return set(ordered[:max_points])

    quota = max(1, max_points // len(available_heads))
    selected = []
    for head in available_heads:
        head_count = 0
        for ticket in ordered:
            boats = combo_boats(ticket)
            if len(boats) != 3 or boats[0] != head or ticket in selected:
                continue
            selected.append(ticket)
            head_count += 1
            if head_count >= quota or len(selected) >= max_points:
                break
        if len(selected) >= max_points:
            break

    for ticket in ordered:
        if len(selected) >= max_points:
            break
        if ticket not in selected:
            selected.append(ticket)
    return set(selected[:max_points])


def super_arunashi3(rows):
    axes, axis_rule = axis_boats_for_roles(rows, ranks=(1, 3))
    alt_axes, alt_axis_rule = axis_boats_for_roles(rows, ranks=(2, 3))
    keshi, keshi_reason, ai_plus_rank6_boat, ai_plus_rank6_revival = select_keshi_boat(rows, protected=axes)
    heads = head_boats_for_arunashi(rows, exclude=([keshi] if keshi else []))
    if len(heads) < 2 or len(axes) < 2 or keshi is None:
        return set(), None
    pool = [boat for boat in range(1, 7) if boat != keshi]
    tickets = set()
    for head in heads:
        if head == keshi:
            continue
        for axis in axes:
            if axis in {head, keshi}:
                continue
            for other in pool:
                if other in {head, axis}:
                    continue
                tickets.add(f"{head}{axis}{other}")
                tickets.add(f"{head}{other}{axis}")
    if not tickets:
        return set(), None
    tickets = trim_tickets(tickets, heads, axes, rows=rows)
    return tickets, {
        "heads": heads,
        "head_rule": "万舟は3〜6号艇頭が多いので3〜6号艇を優先。1/2号艇は強い1着根拠がある時だけ例外",
        "head_mode": "manshu_3to6_priority",
        "head_scores": head_score_details(rows, heads),
        "axes": axes,
        "axis_rule": axis_rule,
        "alt_axes": alt_axes,
        "alt_axis_rule": alt_axis_rule,
        "supports": pool,
        "keshi": keshi,
        "keshi_reason": keshi_reason,
        "ai_plus_rank6_boat": ai_plus_rank6_boat,
        "ai_plus_rank6_revival": ai_plus_rank6_revival,
        "role_note": (
            f"頭{heads[0]},{heads[1]} / 軸は{axis_rule}の{axes[0]},{axes[1]} / "
            f"2・3着は軸どちらか必須で消し{keshi}以外へ折り返し"
        ),
    }


def core_40_arunashi12(rows):
    heads = head_boats_for_arunashi(rows)
    axes, axis_rule = axis_boats_for_roles(rows, ranks=(2, 3))
    if len(heads) < 2 or len(axes) < 2:
        return set(), None

    keshi, keshi_reason, ai_plus_rank6_boat, ai_plus_rank6_revival = select_keshi_boat(
        rows, protected=set(heads + axes)
    )
    if keshi is None:
        return set(), None

    pool = [boat for boat in range(1, 7) if boat != keshi]
    tickets = set()
    for head in heads:
        if head == keshi:
            continue
        for axis in axes:
            if axis in {head, keshi}:
                continue
            for other in pool:
                if other in {head, axis}:
                    continue
                tickets.add(f"{head}{axis}{other}")
                tickets.add(f"{head}{other}{axis}")
    if not tickets:
        return set(), None

    tickets = trim_tickets(tickets, heads, axes, max_points=12, rows=rows)
    if len(tickets) != 12:
        return set(), None

    alt_axes, _alt_axis_rule = axis_boats_for_roles(rows, ranks=(1, 3))
    return tickets, {
        "heads": heads,
        "head_rule": "本命は3〜6号艇頭を優先。1/2号艇は強い1着根拠がある時だけ例外",
        "head_mode": "core_40_outer_priority",
        "head_scores": head_score_details(rows, heads),
        "axes": axes,
        "axis_rule": axis_rule,
        "alt_axes": alt_axes,
        "alt_axis_rule": "比較用: AI3連対率+一般3連対率の1位と3位",
        "supports": pool,
        "keshi": keshi,
        "keshi_reason": keshi_reason,
        "ai_plus_rank6_boat": ai_plus_rank6_boat,
        "ai_plus_rank6_revival": ai_plus_rank6_revival,
        "role_note": (
            f"本命専用。頭{heads[0]},{heads[1]} / 軸は{axis_rule}の{axes[0]},{axes[1]} / "
            f"消し{keshi}以外へ2・3着折り返し12点"
        ),
    }


def core_40_focus_head2_no1_outer56(rows):
    """High-ROI core filter: only the second head, no 1, and 5/6 involved."""
    base_tickets, roles = core_40_arunashi12(rows)
    if not base_tickets or roles is None:
        return set(), None
    heads = list(roles.get("heads") or [])
    if len(heads) < 2:
        return set(), None
    target_head = heads[1]
    tickets = {
        ticket
        for ticket in base_tickets
        if (boats := combo_boats(ticket))
        and boats[0] == target_head
        and 1 not in boats
        and bool({5, 6} & set(boats))
    }
    if not tickets:
        return set(), None
    tickets = trim_tickets(tickets, [target_head], roles.get("axes") or [], max_points=BUY_TICKET_MAX_POINTS, rows=rows)
    if not (BUY_TICKET_MIN_POINTS <= len(tickets) <= BUY_TICKET_MAX_POINTS):
        return set(), None

    focused = dict(roles)
    focused["base_heads"] = heads
    focused["heads"] = [target_head]
    focused["head_rule"] = (
        "本命絞りは、外頭2艇のうち2番手だけを頭にします。"
        "荒れた時は一番手より2番手外頭が配当を作りやすかったためです"
    )
    focused["head_mode"] = "core_front_head2_no1_outer56"
    focused["head_scores"] = head_score_details(rows, [target_head])
    focused["supports"] = sorted({boat for ticket in tickets for boat in combo_boats(ticket) if boat != target_head})
    focused["role_note"] = (
        f"本命絞り。前半1〜3R専用で、頭は外頭2番手の{target_head}号艇だけ。"
        "1号艇は買い目から外し、5/6号艇が絡む形だけを残す回収率重視の買い方"
    )
    return tickets, focused


def core_40_ultra_head2_b1_place_outer56(rows):
    """Ultra-strict core filter: second head, 1 only in 2/3, and 5/6 involved."""
    base_tickets, roles = core_40_arunashi12(rows)
    if not base_tickets or roles is None:
        return set(), None
    heads = list(roles.get("heads") or [])
    if len(heads) < 2:
        return set(), None
    target_head = heads[1]
    if target_head == 1:
        return set(), None
    tickets = {
        ticket
        for ticket in base_tickets
        if (boats := combo_boats(ticket))
        and boats[0] == target_head
        and bool({5, 6} & set(boats))
    }
    if not tickets:
        return set(), None
    tickets = trim_tickets(tickets, [target_head], roles.get("axes") or [], max_points=BUY_TICKET_MAX_POINTS, rows=rows)
    if not (BUY_TICKET_MIN_POINTS <= len(tickets) <= BUY_TICKET_MAX_POINTS):
        return set(), None

    focused = dict(roles)
    focused["base_heads"] = heads
    focused["heads"] = [target_head]
    focused["head_rule"] = (
        "超厳選は外頭2艇のうち2番手だけを頭にします。"
        "1号艇は頭では買わず、取り逃し対策として2・3着だけ許可します"
    )
    focused["head_mode"] = "core_ultra_head2_b1_place_outer56"
    focused["head_scores"] = head_score_details(rows, [target_head])
    focused["supports"] = sorted({boat for ticket in tickets for boat in combo_boats(ticket) if boat != target_head})
    focused["role_note"] = (
        f"超厳選強本命。頭は外頭2番手の{target_head}号艇だけ。"
        "1号艇頭は買わず、1号艇は2・3着だけ許可。"
        f"5/6号艇が絡む形だけを{len(tickets)}点残し、取り逃し万舟を拾う回収率重視の買い方"
    )
    return tickets, focused


def b1_underbet_head8(rows):
    """1号艇が売れていないのにデータが強い時だけ、1号艇頭で絞る。"""
    metrics = rows[0].get("_morning_metrics") or {}
    head_ok, head_reason = b1_unpopular_head_value(rows, metrics)
    if not head_ok:
        return set(), None

    head = 1
    axes, axis_rule = axis_boats_for_roles(rows, ranks=(1, 3))
    axes = [axis for axis in axes if axis != head]
    if len(axes) < 2:
        fallback_axes, fallback_rule = axis_boats_for_roles(rows, ranks=(2, 3))
        axes = unique(axes + [axis for axis in fallback_axes if axis != head])[:2]
        axis_rule = f"{axis_rule}（1号艇頭と重なる時は{fallback_rule}で補完）"
    if len(axes) < 2:
        axes = unique(
            axes
            + [
                row["boat_number"]
                for row in sorted(
                    rows,
                    key=lambda row: (
                        -(row.get("composite_top3_actual_pct") or 0),
                        row["boat_number"],
                    ),
                )
                if row["boat_number"] != head
            ]
        )[:2]
        axis_rule = f"{axis_rule}（不足分は複合3着内率上位で補完）"
    if len(axes) < 2:
        return set(), None

    keshi, keshi_reason, ai_plus_rank6_boat, ai_plus_rank6_revival = select_keshi_boat(
        rows, protected=set([head] + axes)
    )
    if keshi is None:
        return set(), None

    pool = [boat for boat in range(1, 7) if boat not in {head, keshi}]
    tickets = set()
    for axis in axes:
        if axis in {head, keshi}:
            continue
        for other in pool:
            if other == axis:
                continue
            tickets.add(f"{head}{axis}{other}")
            tickets.add(f"{head}{other}{axis}")
    if not tickets:
        return set(), None

    tickets = trim_tickets(tickets, [head], axes, max_points=BUY_TICKET_MAX_POINTS, rows=rows)
    if len(tickets) < BUY_TICKET_MIN_POINTS:
        return set(), None

    return tickets, {
        "heads": [head],
        "head_rule": "1号艇が人気不足なのに、AI・逃げ率・展示/1周などのデータでは頭で買える時だけ1号艇頭を採用",
        "head_mode": "b1_underbet_head_value",
        "head_scores": head_score_details(rows, [head]),
        "axes": axes,
        "axis_rule": axis_rule,
        "alt_axes": [],
        "alt_axis_rule": "人気薄1号艇頭専用: 軸は1号艇以外から選ぶ",
        "supports": pool,
        "keshi": keshi,
        "keshi_reason": keshi_reason,
        "ai_plus_rank6_boat": ai_plus_rank6_boat,
        "ai_plus_rank6_revival": ai_plus_rank6_revival,
        "role_note": (
            f"逆歪み本命。{head_reason}ため、1号艇頭を固定。"
            f"軸は{axis_rule}の{axes[0]},{axes[1]}。消し{keshi}以外へ2・3着折り返し{len(tickets)}点"
        ),
    }


def venue_top3_buff_items(rows, min_pp=10.0):
    out = []
    for row in rows or []:
        for item in row.get("venue_factor_matches") or []:
            if item.get("direction") != "buff":
                continue
            if (as_num(item.get("top3_rate_pp")) or 0.0) >= min_pp:
                out.append(item)
    out.sort(
        key=lambda item: (
            {"S": 0, "A": 1}.get(item.get("confidence"), 9),
            -(as_num(item.get("top3_rate_pp")) or 0.0),
            -(as_num(item.get("win_rate_pp")) or 0.0),
            -(as_num(item.get("sample_count")) or 0.0),
        )
    )
    return out


def venue_top3_buff_text(item):
    if not item:
        return ""
    lane = int(as_num(item.get("lane")) or 0)
    lane_text = f"{lane}号艇 " if lane else ""
    return (
        f"{item.get('venue')}{lane_text}{item.get('metric_label')} "
        f"3着内差+{as_num(item.get('top3_rate_pp')) or 0.0:.1f}pt"
        f"({item.get('confidence')})"
    )


def venue_head_buff_items(rows, min_pp=8.0):
    out = []
    for row in rows or []:
        for item in row.get("venue_factor_matches") or []:
            if item.get("direction") != "buff":
                continue
            if (as_num(item.get("win_rate_pp")) or 0.0) >= min_pp:
                out.append(item)
    out.sort(
        key=lambda item: (
            {"S": 0, "A": 1}.get(item.get("confidence"), 9),
            -(as_num(item.get("win_rate_pp")) or 0.0),
            -(as_num(item.get("top3_rate_pp")) or 0.0),
            -(as_num(item.get("sample_count")) or 0.0),
        )
    )
    return out


def venue_head_buff_text(item):
    if not item:
        return ""
    lane = int(as_num(item.get("lane")) or 0)
    lane_text = f"{lane}号艇 " if lane else ""
    return (
        f"{item.get('venue')}{lane_text}{item.get('metric_label')} "
        f"1着差+{as_num(item.get('win_rate_pp')) or 0.0:.1f}pt"
        f"({item.get('confidence')})"
    )


def ashiya_wave_b1weak_top3buff10_outer_h1_ai13_no1_has56_8(rows):
    metrics = rows[0].get("_morning_metrics") or {}
    b1_avgdiff = as_num(metrics.get("boat1_avg_isshu_diff"))
    if b1_avgdiff is None or b1_avgdiff > -0.05:
        return set(), None
    top3_buff_items = venue_top3_buff_items(rows, min_pp=10.0)
    if not top3_buff_items:
        return set(), None
    top3_buff_reason = venue_top3_buff_text(top3_buff_items[0])

    head_rows = sorted(
        [row for row in rows if row.get("boat_number") in {3, 4, 5, 6}],
        key=lambda row: (
            -venue_roi_win_score(row),
            row["boat_number"],
        ),
    )
    if not head_rows:
        return set(), None

    head = head_rows[0]["boat_number"]
    axes, axis_rule = axis_boats_for_roles(rows, ranks=(1, 3))
    if len(axes) < 2:
        return set(), None

    heads_non1 = [
        row["boat_number"]
        for row in sorted(
            [row for row in rows if row.get("boat_number") != 1],
            key=lambda row: (
                -venue_roi_win_score(row),
                row["boat_number"],
            ),
        )[:2]
    ]
    axis_top3_rows = sorted(
        rows,
        key=lambda row: (
            -venue_roi_top3_score(row),
            row["boat_number"],
        ),
    )
    top3_axes = [row["boat_number"] for row in axis_top3_rows[:2]]
    protected = set(heads_non1 + axes + top3_axes)
    keshi_row = sorted(
        rows,
        key=lambda row: (
            bool(row.get("venue_dont_keshi") or row["boat_number"] in protected),
            venue_roi_top3_score(row),
            row["boat_number"],
        ),
    )[0]
    keshi = keshi_row["boat_number"]
    pool = [boat for boat in range(2, 7) if boat != keshi]

    tickets = []
    seen = set()
    for axis in axes:
        if axis == head:
            continue
        for other in pool:
            if len({head, axis, other}) != 3:
                continue
            for ticket in (f"{head}{axis}{other}", f"{head}{other}{axis}"):
                nums = set(combo_boats(ticket))
                if 1 in nums or not (nums & {5, 6}):
                    continue
                if ticket in seen:
                    continue
                seen.add(ticket)
                tickets.append(ticket)
                if len(tickets) >= 8:
                    break
            if len(tickets) >= 8:
                break
        if len(tickets) >= 8:
            break
    if not tickets:
        return set(), None

    if len(tickets) < 2:
        return set(), None

    ticket_set = set(tickets)
    ticket_supports = sorted(
        {
            boat
            for ticket in ticket_set
            for boat in combo_boats(ticket)
            if boat not in {head}
        }
    )
    avg_text = f"{b1_avgdiff:+.2f}" if b1_avgdiff is not None else "不明"
    head_scores = {
        str(row["boat_number"]): {
            "score": round(venue_roi_win_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in head_rows[:1]
    }
    support_scores = {
        str(row["boat_number"]): {
            "score": round(venue_roi_top3_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in axis_top3_rows[:4]
    }
    ai_plus_rank6_row = next((row for row in rows if row.get("ai_plus_rank") == 6), {})
    ai_plus_rank6_boat = ai_plus_rank6_row.get("boat_number")
    ai_plus_rank6_revival = revive_reasons(ai_plus_rank6_row) if ai_plus_rank6_row else []
    return ticket_set, {
        "heads": [head],
        "head_rule": "芦屋専用。波3cm以上、1号艇の平均との差悪化、場別3着内バフ10pt以上が重なる時だけ、3〜6号艇の頭スコア最上位1艇に固定",
        "head_mode": "ashiya_wave_b1weak_top3buff10_outer_h1",
        "head_scores": head_scores,
        "attackers": [head],
        "attack_scores": head_scores,
        "finishers": [head],
        "finisher_scores": head_scores,
        "support_boats": ticket_supports,
        "support_scores": support_scores,
        "role_split_note": "芦屋の長期検証で強かった、波あり+1号艇弱化+場別3着内バフから外頭1艇に絞る小点数型",
        "axes": axes,
        "axis_rule": axis_rule,
        "alt_axes": [],
        "alt_axis_rule": "芦屋専用: AI+1位と3位を基本軸。1号艇が入る形は買い目から除外",
        "supports": pool,
        "keshi": keshi,
        "keshi_reason": f"芦屋ROIルール専用: 複合3着内スコアが弱い{keshi}号艇を消し",
        "ai_plus_rank6_boat": ai_plus_rank6_boat,
        "ai_plus_rank6_revival": ai_plus_rank6_revival,
        "role_note": (
            f"芦屋専用本命。波あり+1号艇平均との差{avg_text}+{top3_buff_reason}で1号艇を全消し。"
            f"頭は3〜6号艇の頭スコア最上位{head}号艇だけ。"
            f"軸は{axis_rule}、5/6絡みだけを残して{len(ticket_set)}点"
        ),
    }


def fukuoka_r9_12_b1lap4_top3buff12_h1_ai13_has56_8(rows):
    metrics = rows[0].get("_morning_metrics") or {}
    b1_isshu_rank = valid_boat_rank(metrics.get("boat1_isshu_rank"))
    top3_buff_items = venue_top3_buff_items(rows, min_pp=12.0)
    if not top3_buff_items:
        return set(), None
    top3_buff_reason = venue_top3_buff_text(top3_buff_items[0])

    head_rows = sorted(
        [row for row in rows if row.get("boat_number") != 1],
        key=lambda row: (
            -venue_roi_win_score(row),
            row["boat_number"],
        ),
    )
    heads_for_protection = [row["boat_number"] for row in head_rows[:2]]
    heads = heads_for_protection[:1]
    axes, axis_rule = axis_boats_for_roles(rows, ranks=(1, 3))
    if len(heads) < 1 or len(axes) < 2:
        return set(), None

    axis_top3_rows = sorted(
        rows,
        key=lambda row: (
            -venue_roi_top3_score(row),
            row["boat_number"],
        ),
    )
    top3_axes = [row["boat_number"] for row in axis_top3_rows[:2]]
    protected = set(heads_for_protection + axes + top3_axes)
    keshi_row = sorted(
        rows,
        key=lambda row: (
            bool(row.get("venue_dont_keshi") or row["boat_number"] in protected),
            venue_roi_top3_score(row),
            row["boat_number"],
        ),
    )[0]
    keshi = keshi_row["boat_number"]
    supports = [boat for boat in range(1, 7) if boat != keshi]

    tickets = []
    seen = set()
    head = heads[0]
    for axis in axes:
        if axis == head:
            continue
        for other in supports:
            if len({head, axis, other}) != 3:
                continue
            for ticket in (f"{head}{axis}{other}", f"{head}{other}{axis}"):
                nums = set(combo_boats(ticket))
                if not (nums & {5, 6}):
                    continue
                if ticket in seen:
                    continue
                seen.add(ticket)
                tickets.append(ticket)
                if len(tickets) >= 8:
                    break
            if len(tickets) >= 8:
                break
        if len(tickets) >= 8:
            break
    if len(tickets) < 2:
        return set(), None

    ticket_set = set(tickets)
    selected_boats = sorted({boat for ticket in ticket_set for boat in combo_boats(ticket)})
    selected_rows = [next(row for row in rows if row["boat_number"] == boat) for boat in selected_boats]
    selected_scores = {
        str(row["boat_number"]): {
            "head_score": round(venue_roi_win_score(row), 3),
            "top3_score": round(venue_roi_top3_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in selected_rows
    }
    head_scores = {
        str(row["boat_number"]): {
            "score": round(venue_roi_win_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in head_rows[:1]
    }
    support_scores = {
        str(row["boat_number"]): {
            "score": round(venue_roi_top3_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in axis_top3_rows[:4]
    }
    ai_plus_rank6_row = next((row for row in rows if row.get("ai_plus_rank") == 6), {})
    ai_plus_rank6_boat = ai_plus_rank6_row.get("boat_number")
    ai_plus_rank6_revival = revive_reasons(ai_plus_rank6_row) if ai_plus_rank6_row else []
    lap_text = f"{b1_isshu_rank:.0f}位" if b1_isshu_rank is not None else "不明"
    return ticket_set, {
        "heads": heads,
        "head_rule": "福岡専用。9〜12Rで1号艇の1周が4位以下、かつ場別3着内バフ12pt以上が出た時に1号艇頭を消し、非1号艇の複合1着率最上位1艇を頭にする",
        "head_mode": "fukuoka_r9_12_b1lap4_top3buff12_h1_ai13",
        "head_scores": head_scores,
        "attackers": heads,
        "attack_scores": head_scores,
        "finishers": heads,
        "finisher_scores": head_scores,
        "support_boats": selected_boats,
        "support_scores": {**selected_scores, **support_scores},
        "role_split_note": "福岡の長期検証で強かった、後半の人気1号艇1周弱化と場別3着内バフを合わせる1号艇頭消し型",
        "axes": axes,
        "axis_rule": axis_rule,
        "alt_axes": top3_axes,
        "alt_axis_rule": "消し保護では複合3着内率の上位2艇も参照",
        "supports": supports,
        "keshi": keshi,
        "keshi_reason": f"福岡ROIルール専用: 複合3着内スコアが弱い{keshi}号艇を消し",
        "ai_plus_rank6_boat": ai_plus_rank6_boat,
        "ai_plus_rank6_revival": ai_plus_rank6_revival,
        "role_note": (
            f"福岡専用本命。後半{lap_text}の1号艇は頭で買わず、{top3_buff_reason}を重視。"
            f"頭は非1号艇の複合1着率最上位{head}号艇、軸は{axis_rule}、"
            f"5/6絡みだけを{len(ticket_set)}点"
        ),
    }


def karatsu_b1loss45_top3buff10_b1odds45_h1_top3_8(rows):
    metrics = rows[0].get("_morning_metrics") or {}
    b1_odds_pct = as_num(metrics.get("boat1_odds_prediction_pct"))
    b1_loss_pct = as_num(metrics.get("boat1_loss_pct"))
    top3_buff_items = venue_top3_buff_items(rows, min_pp=10.0)
    if not top3_buff_items:
        return set(), None
    top3_buff_reason = venue_top3_buff_text(top3_buff_items[0])

    head_rows = sorted(
        [row for row in rows if row.get("boat_number") != 1],
        key=lambda row: (
            -venue_roi_win_score(row),
            row["boat_number"],
        ),
    )
    heads_for_protection = [row["boat_number"] for row in head_rows[:2]]
    heads = heads_for_protection[:1]
    axis_top3_rows = sorted(
        rows,
        key=lambda row: (
            -venue_roi_top3_score(row),
            row["boat_number"],
        ),
    )
    axes = [row["boat_number"] for row in axis_top3_rows[:2]]
    ai13_axes, _ai13_axis_rule = axis_boats_for_roles(rows, ranks=(1, 3))
    if len(heads) < 1 or len(axes) < 2:
        return set(), None

    protected = set(heads_for_protection + ai13_axes + axes)
    keshi_row = sorted(
        rows,
        key=lambda row: (
            bool(row.get("venue_dont_keshi") or row["boat_number"] in protected),
            venue_roi_top3_score(row),
            row["boat_number"],
        ),
    )[0]
    keshi = keshi_row["boat_number"]
    supports = [boat for boat in range(1, 7) if boat != keshi]

    tickets = []
    seen = set()
    head = heads[0]
    for axis in axes:
        if axis == head:
            continue
        for other in supports:
            if len({head, axis, other}) != 3:
                continue
            for ticket in (f"{head}{axis}{other}", f"{head}{other}{axis}"):
                if ticket in seen:
                    continue
                seen.add(ticket)
                tickets.append(ticket)
                if len(tickets) >= 8:
                    break
            if len(tickets) >= 8:
                break
        if len(tickets) >= 8:
            break
    if len(tickets) < 2:
        return set(), None

    ticket_set = set(tickets)
    selected_boats = sorted({boat for ticket in ticket_set for boat in combo_boats(ticket)})
    selected_rows = [next(row for row in rows if row["boat_number"] == boat) for boat in selected_boats]
    selected_scores = {
        str(row["boat_number"]): {
            "head_score": round(venue_roi_win_score(row), 3),
            "top3_score": round(venue_roi_top3_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in selected_rows
    }
    head_scores = {
        str(row["boat_number"]): {
            "score": round(venue_roi_win_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in head_rows[:1]
    }
    support_scores = {
        str(row["boat_number"]): {
            "score": round(venue_roi_top3_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in axis_top3_rows[:4]
    }
    ai_plus_rank6_row = next((row for row in rows if row.get("ai_plus_rank") == 6), {})
    ai_plus_rank6_boat = ai_plus_rank6_row.get("boat_number")
    ai_plus_rank6_revival = revive_reasons(ai_plus_rank6_row) if ai_plus_rank6_row else []
    axis_rule = "複合3着内率の1位と2位"
    return ticket_set, {
        "heads": heads,
        "head_rule": "唐津専用。1号艇がオッズ評価45%以上の1位、逃げ失敗45%以上、かつ場別3着内バフ10pt以上が重なる時に、非1号艇の複合1着率最上位1艇を頭にする",
        "head_mode": "karatsu_b1loss45_top3buff10_b1odds45_h1",
        "head_scores": head_scores,
        "attackers": heads,
        "attack_scores": head_scores,
        "finishers": heads,
        "finisher_scores": head_scores,
        "support_boats": selected_boats,
        "support_scores": {**selected_scores, **support_scores},
        "role_split_note": "唐津の長期検証で強かった、強人気1号艇の逃げ失敗率と場別3着内バフを合わせる頭1艇型",
        "axes": axes,
        "axis_rule": axis_rule,
        "alt_axes": ai13_axes,
        "alt_axis_rule": "消し保護ではAI+1位/3位も参照",
        "supports": supports,
        "keshi": keshi,
        "keshi_reason": f"唐津ROIルール専用: 複合3着内スコアが弱い{keshi}号艇を消し",
        "ai_plus_rank6_boat": ai_plus_rank6_boat,
        "ai_plus_rank6_revival": ai_plus_rank6_revival,
        "role_note": (
            f"唐津専用本命。1号艇オッズ評価1位{b1_odds_pct or 0.0:.1f}%だが、"
            f"逃げ失敗{b1_loss_pct or 0.0:.1f}%で過信気味。{top3_buff_reason}を重視し、"
            f"頭は非1号艇の複合1着率最上位{head}号艇、軸は{axis_rule}で{len(ticket_set)}点"
        ),
    }


def omura_headbuff8_lowai_outer56avg020_h1_ai13_has56_8(rows):
    metrics = rows[0].get("_morning_metrics") or {}
    outer56_avgdiff = as_num(metrics.get("outer56_best_avg_isshu_diff"))
    b1_tenji_rank = valid_boat_rank(metrics.get("boat1_tenji_rank") or metrics.get("boat1_tenji_time_rank"))
    head_buff_items = venue_head_buff_items(rows, min_pp=8.0)
    if not head_buff_items:
        return set(), None
    head_buff_reason = venue_head_buff_text(head_buff_items[0])
    revival_summary = venue_low_ai_revival_summary(rows)
    if not revival_summary:
        return set(), None
    revival_reason = next((item.get("reason") for item in revival_summary if item.get("reason")), "")

    head_rows = sorted(
        [row for row in rows if row.get("boat_number") != 1],
        key=lambda row: (
            -venue_roi_win_score(row),
            row["boat_number"],
        ),
    )
    heads_for_protection = [row["boat_number"] for row in head_rows[:2]]
    heads = heads_for_protection[:1]
    axes, axis_rule = axis_boats_for_roles(rows, ranks=(1, 3))
    if len(heads) < 1 or len(axes) < 2:
        return set(), None

    axis_top3_rows = sorted(
        rows,
        key=lambda row: (
            -venue_roi_top3_score(row),
            row["boat_number"],
        ),
    )
    top3_axes = [row["boat_number"] for row in axis_top3_rows[:2]]
    protected = set(heads_for_protection + axes + top3_axes)
    keshi_row = sorted(
        rows,
        key=lambda row: (
            bool(row.get("venue_dont_keshi") or row["boat_number"] in protected),
            venue_roi_top3_score(row),
            row["boat_number"],
        ),
    )[0]
    keshi = keshi_row["boat_number"]
    supports = [boat for boat in range(1, 7) if boat != keshi]

    tickets = []
    seen = set()
    head = heads[0]
    for axis in axes:
        if axis == head:
            continue
        for other in supports:
            if len({head, axis, other}) != 3:
                continue
            for ticket in (f"{head}{axis}{other}", f"{head}{other}{axis}"):
                nums = set(combo_boats(ticket))
                if not (nums & {5, 6}):
                    continue
                if ticket in seen:
                    continue
                seen.add(ticket)
                tickets.append(ticket)
                if len(tickets) >= 8:
                    break
            if len(tickets) >= 8:
                break
        if len(tickets) >= 8:
            break
    if len(tickets) < 2:
        return set(), None

    ticket_set = set(tickets)
    selected_boats = sorted({boat for ticket in ticket_set for boat in combo_boats(ticket)})
    selected_rows = [next(row for row in rows if row["boat_number"] == boat) for boat in selected_boats]
    selected_scores = {
        str(row["boat_number"]): {
            "head_score": round(venue_roi_win_score(row), 3),
            "top3_score": round(venue_roi_top3_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in selected_rows
    }
    head_scores = {
        str(row["boat_number"]): {
            "score": round(venue_roi_win_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in head_rows[:1]
    }
    support_scores = {
        str(row["boat_number"]): {
            "score": round(venue_roi_top3_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in axis_top3_rows[:4]
    }
    ai_plus_rank6_row = next((row for row in rows if row.get("ai_plus_rank") == 6), {})
    ai_plus_rank6_boat = ai_plus_rank6_row.get("boat_number")
    ai_plus_rank6_revival = revive_reasons(ai_plus_rank6_row) if ai_plus_rank6_row else []
    avg_text = f"{outer56_avgdiff:+.2f}" if outer56_avgdiff is not None else "不明"
    b1_tenji_text = f"{b1_tenji_rank:.0f}位" if b1_tenji_rank is not None else "不明"
    return ticket_set, {
        "heads": heads,
        "head_rule": "大村専用。場別頭バフ8pt以上、低評価艇復活バフ、5/6号艇平均との差+0.35以上、1号艇展示4位以下が揃った時に、非1号艇の複合1着率最上位1艇を頭にする",
        "head_mode": "omura_headbuff8_lowai_outer56avg020_h1_ai13",
        "head_scores": head_scores,
        "attackers": heads,
        "attack_scores": head_scores,
        "finishers": heads,
        "finisher_scores": head_scores,
        "support_boats": selected_boats,
        "support_scores": {**selected_scores, **support_scores},
        "role_split_note": "大村の長期検証で安定した、場別頭バフと低評価艇復活、5/6号艇の強い足色、1号艇展示弱化を合わせる頭1艇型",
        "axes": axes,
        "axis_rule": axis_rule,
        "alt_axes": top3_axes,
        "alt_axis_rule": "消し保護では複合3着内率の上位2艇も参照",
        "supports": supports,
        "keshi": keshi,
        "keshi_reason": f"大村ROIルール専用: 複合3着内スコアが弱い{keshi}号艇を消し",
        "ai_plus_rank6_boat": ai_plus_rank6_boat,
        "ai_plus_rank6_revival": ai_plus_rank6_revival,
        "venue_low_ai_revival": revival_summary,
        "role_note": (
            f"大村専用本命。{head_buff_reason}と{revival_reason or '低評価艇復活バフ'}、"
            f"5/6号艇平均との差{avg_text}、1号艇展示{b1_tenji_text}を重視。頭は非1号艇の複合1着率最上位{head}号艇、"
            f"軸は{axis_rule}、5/6絡みだけを{len(ticket_set)}点"
        ),
    }


def venue_roi_win_score(row):
    ai_pred = as_num(row.get("ai_prediction_pct")) or 0.0
    ai_plus = as_num(row.get("ai_plus")) or 0.0
    tenji_rank = int(valid_boat_rank(row.get("tenji_rank") or row.get("exhibit_rank") or row.get("tenji_time_rank")) or 9)
    lap_rank = int(valid_boat_rank(row.get("isshu_rank")) or 9)
    choku_rank = int(valid_boat_rank(row.get("chokusen_rank")) or 9)
    avg_diff = as_num(row.get("avg_isshu_diff")) or 0.0
    venue_head_delta = as_num(row.get("venue_head_score_delta")) or 0.0
    venue_manshu_delta = as_num(row.get("venue_manshu_score_delta")) or 0.0
    max_buff_win_pp = as_num((row.get("venue_low_ai_revival_profile") or {}).get("win_rate_pp")) or 0.0
    return (
        ai_pred
        + ai_plus * 0.055
        + (7 - min(tenji_rank, 7)) * 0.75
        + (7 - min(lap_rank, 7)) * 0.65
        + (7 - min(choku_rank, 7)) * 0.20
        + avg_diff * 8.0
        + venue_head_delta * 1.15
        + venue_manshu_delta * 0.35
        + (1.3 if max_buff_win_pp >= 8.0 else 0.0)
    )


def venue_roi_top3_score(row):
    ai_plus = as_num(row.get("ai_plus")) or 0.0
    tenji_rank = int(valid_boat_rank(row.get("tenji_rank") or row.get("exhibit_rank") or row.get("tenji_time_rank")) or 9)
    lap_rank = int(valid_boat_rank(row.get("isshu_rank")) or 9)
    mawari_rank = int(valid_boat_rank(row.get("mawariashi_rank")) or 9)
    avg_diff = as_num(row.get("avg_isshu_diff")) or 0.0
    venue_top3_delta = as_num(row.get("venue_top3_score_delta")) or 0.0
    venue_manshu_delta = as_num(row.get("venue_manshu_score_delta")) or 0.0
    venue_dont_keshi = 2.0 if row.get("venue_dont_keshi") else 0.0
    return (
        ai_plus
        + (7 - min(tenji_rank, 7)) * 2.0
        + (7 - min(lap_rank, 7)) * 1.7
        + (7 - min(mawari_rank, 7)) * 0.8
        + avg_diff * 18.0
        + venue_top3_delta * 3.0
        + venue_manshu_delta * 1.2
        + venue_dont_keshi
    )


def mikuni_big50_template_context(rows):
    head_rows_non1 = sorted(
        [row for row in rows if row.get("boat_number") != 1],
        key=lambda row: (
            -venue_roi_win_score(row),
            row["boat_number"],
        ),
    )
    heads_non1 = [row["boat_number"] for row in head_rows_non1]
    axes_ai13, axis_rule = axis_boats_for_roles(rows, ranks=(1, 3))
    axis_top3_rows = sorted(
        rows,
        key=lambda row: (
            -venue_roi_top3_score(row),
            row["boat_number"],
        ),
    )
    axes_top3 = [row["boat_number"] for row in axis_top3_rows[:2]]
    protected = set(heads_non1[:2] + axes_ai13 + axes_top3)
    keshi_row = sorted(
        rows,
        key=lambda row: (
            bool(row.get("venue_dont_keshi") or row["boat_number"] in protected),
            venue_roi_top3_score(row),
            row["boat_number"],
        ),
    )[0]
    keshi = keshi_row["boat_number"]
    supports = [boat for boat in range(1, 7) if boat != keshi]
    combo_rank = unique(heads_non1[:2] + axes_top3 + axes_ai13)
    return {
        "head_rows_non1": head_rows_non1,
        "heads_non1": heads_non1,
        "axes_ai13": axes_ai13,
        "axis_rule": axis_rule,
        "axis_top3_rows": axis_top3_rows,
        "axes_top3": axes_top3,
        "supports": supports,
        "combo_rank": combo_rank,
        "keshi": keshi,
        "keshi_row": keshi_row,
    }


def mikuni_big50_a_h1_ai13_has56_8(rows):
    ctx = mikuni_big50_template_context(rows)
    heads = ctx["heads_non1"][:1]
    axes = ctx["axes_ai13"]
    supports = ctx["supports"]
    if not heads or len(axes) < 2:
        return set(), None

    tickets = []
    seen = set()
    for head in heads:
        if head == 1:
            continue
        for axis in axes:
            if axis == head:
                continue
            for other in supports:
                if len({head, axis, other}) != 3:
                    continue
                for ticket in (f"{head}{axis}{other}", f"{head}{other}{axis}"):
                    nums = set(combo_boats(ticket))
                    if not (nums & {5, 6}):
                        continue
                    if ticket in seen:
                        continue
                    seen.add(ticket)
                    tickets.append(ticket)
                    if len(tickets) >= 8:
                        break
                if len(tickets) >= 8:
                    break
            if len(tickets) >= 8:
                break
    if not tickets:
        return set(), None

    head = heads[0]
    ticket_set = set(tickets)
    head_scores = {
        str(row["boat_number"]): {
            "score": round(venue_roi_win_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in ctx["head_rows_non1"][:2]
    }
    top3_scores = {
        str(row["boat_number"]): {
            "score": round(venue_roi_top3_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in ctx["axis_top3_rows"][:4]
    }
    return ticket_set, {
        "heads": heads,
        "head_rule": "三国5万舟警戒A。1号艇人気55%以上、5/6平均との差+0.30以上、波3cm以上で、頭は非1号艇の複合1着率最上位1艇",
        "head_mode": "mikuni_big50_a_h1_ai13_has56",
        "head_scores": head_scores,
        "attackers": heads,
        "attack_scores": head_scores,
        "finishers": heads,
        "finisher_scores": head_scores,
        "support_boats": sorted({boat for ticket in ticket_set for boat in combo_boats(ticket)}),
        "support_scores": top3_scores,
        "role_split_note": "三国Aは探索時テンプレートを固定再現。非1頭1艇、AI+1位/3位軸、5/6絡み最大8点",
        "axes": axes,
        "axis_rule": ctx["axis_rule"],
        "alt_axes": ctx["axes_top3"],
        "alt_axis_rule": "消し保護では複合3着内率上位2艇も参照",
        "supports": supports,
        "keshi": ctx["keshi"],
        "keshi_reason": f"三国A専用: 複合3着内スコアが弱い{ctx['keshi']}号艇を消し",
        "role_note": (
            "三国5万舟警戒A。1号艇人気55%以上、5/6平均との差+0.30以上、波3cm以上。"
            f"頭は非1号艇の複合1着率最上位{head}号艇、軸は{ctx['axis_rule']}、"
            f"5/6絡みだけを{len(ticket_set)}点"
        ),
    }


def mikuni_big50_b_box3_comp_has56_6(rows):
    ctx = mikuni_big50_template_context(rows)
    selected = ctx["combo_rank"][:3]
    if len(selected) < 3 or not ({5, 6} & set(selected)):
        return set(), None

    ticket_set = {
        f"{head}{second}{third}"
        for head in selected
        for second in selected
        for third in selected
        if len({head, second, third}) == 3 and ({5, 6} & {head, second, third})
    }
    if len(ticket_set) != 6:
        return set(), None

    selected_rows = [row_by_boat(rows, boat) for boat in selected]
    selected_scores = {
        str(row["boat_number"]): {
            "head_score": round(venue_roi_win_score(row), 3),
            "top3_score": round(venue_roi_top3_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in selected_rows
        if row
    }
    not_selected = sorted(set(range(1, 7)) - set(selected))
    return ticket_set, {
        "heads": selected,
        "head_rule": "三国5万舟警戒B。1号艇人気60%以上、1号艇ST展示6位以下、5/6平均との差+0.30以上で、複合上位3艇をBOX",
        "head_mode": "mikuni_big50_b_box3_comp_has56",
        "head_scores": selected_scores,
        "attackers": selected,
        "attack_scores": selected_scores,
        "finishers": selected,
        "finisher_scores": selected_scores,
        "support_boats": selected,
        "support_scores": selected_scores,
        "role_split_note": "三国Bは探索時テンプレートを固定再現。複合上位3艇BOX、5/6絡み6点",
        "axes": selected,
        "axis_rule": f"複合1着率上位2艇、複合3着内率上位2艇、{ctx['axis_rule']}を融合して選んだ3艇",
        "alt_axes": ctx["axes_ai13"],
        "alt_axis_rule": "BOX選抜ではAI+1位/3位も参照",
        "supports": selected,
        "keshi": not_selected[0] if not_selected else None,
        "keshi_reason": f"三国B専用: 選抜3艇以外({','.join(map(str, not_selected))})は買わない",
        "role_note": (
            "三国5万舟警戒B。1号艇人気60%以上、1号艇ST展示6位以下、5/6平均との差+0.30以上。"
            f"複合上位3艇{','.join(map(str, selected))}の5/6絡みBOX6点"
        ),
    }


def big50_rank(row, *keys):
    for key in keys:
        rank = valid_boat_rank(row.get(key))
        if rank is not None:
            return int(rank)
    return None


def big50_chaos_head_score(row):
    boat = int(row.get("boat_number") or 0)
    odds_rank = big50_rank(row, "odds_prediction_pct_rank")
    ai_rank = big50_rank(row, "ai_prediction_pct_rank")
    isshu_rank = big50_rank(row, "isshu_rank")
    tenji_rank = big50_rank(row, "tenji_rank", "exhibit_rank", "tenji_time_rank")
    avg_diff = as_num(row.get("avg_isshu_diff")) or 0.0
    score = 0
    reasons = []
    if boat >= 5:
        score += 2
        reasons.append("5/6号艇")
    if odds_rank is not None and odds_rank >= 4:
        score += 2
        reasons.append(f"オッズ人気薄{odds_rank}位")
    if ai_rank is not None and ai_rank >= 4:
        score += 2
        reasons.append(f"AI人気薄{ai_rank}位")
    if isshu_rank is not None and isshu_rank <= 2:
        score += 1
        reasons.append(f"1周{isshu_rank}位")
    if tenji_rank is not None and tenji_rank <= 2:
        score += 1
        reasons.append(f"展示{tenji_rank}位")
    if avg_diff >= 0.10:
        score += 1
        reasons.append(f"平均差+{avg_diff:.2f}")
    return score, reasons


def big50_outer56_chaos(rows):
    scored = []
    for row in rows or []:
        boat = int(row.get("boat_number") or 0)
        if boat not in {5, 6}:
            continue
        score, reasons = big50_chaos_head_score(row)
        scored.append((score, boat, reasons))
    scored.sort(key=lambda item: (-item[0], item[1]))
    best_score = scored[0][0] if scored else 0
    best_boats = [boat for score, boat, _ in scored if score == best_score]
    best_reasons = scored[0][2] if scored else []
    return best_score, best_boats, best_reasons


def big50_balanced_head_score(row):
    boat = int(row.get("boat_number") or 0)
    odds_rank = big50_rank(row, "odds_prediction_pct_rank")
    ai_rank = big50_rank(row, "ai_prediction_pct_rank")
    ai_3ren_rank = big50_rank(row, "ai_3ren_pct_rank")
    lap_rank = big50_rank(row, "isshu_rank")
    tenji_rank = big50_rank(row, "tenji_rank", "exhibit_rank", "tenji_time_rank")
    avg_diff = as_num(row.get("avg_isshu_diff")) or 0.0
    score = {6: 5.0, 5: 5.0, 4: 3.0, 3: 2.0, 2: 1.0}.get(boat, -999.0)
    reasons = []
    if boat in {5, 6}:
        reasons.append("外枠穴頭")
    if odds_rank is not None and odds_rank >= 4:
        score += 7.0
        reasons.append(f"オッズ人気薄{odds_rank}位")
    if ai_rank is not None and ai_rank >= 4:
        score += 7.0
        reasons.append(f"AI人気薄{ai_rank}位")
    if ai_3ren_rank is not None and ai_3ren_rank >= 4:
        score += 2.0
        reasons.append(f"AI3連対下位{ai_3ren_rank}位")
    if lap_rank is not None and lap_rank <= 2:
        score += 3.0
        reasons.append(f"1周{lap_rank}位")
    if tenji_rank is not None and tenji_rank <= 2:
        score += 2.0
        reasons.append(f"展示{tenji_rank}位")
    if avg_diff >= 0.10:
        score += 3.0
        reasons.append(f"平均差+{avg_diff:.2f}")
    if odds_rank is not None and odds_rank <= 2:
        score -= 2.0
        reasons.append(f"オッズ上位{odds_rank}位")
    if ai_rank is not None and ai_rank <= 2:
        score -= 1.0
        reasons.append(f"AI上位{ai_rank}位")
    return score, reasons


def big50_support_score(row, b1bonus=0.0):
    boat = int(row.get("boat_number") or 0)
    odds_rank = big50_rank(row, "odds_prediction_pct_rank")
    ai_rank = big50_rank(row, "ai_prediction_pct_rank")
    ai_3ren_rank = big50_rank(row, "ai_3ren_pct_rank")
    general_3ren_rank = big50_rank(row, "general_3ren_pct_rank")
    lap_rank = big50_rank(row, "isshu_rank")
    tenji_rank = big50_rank(row, "tenji_rank", "exhibit_rank", "tenji_time_rank")
    avg_diff = as_num(row.get("avg_isshu_diff")) or 0.0
    score = 0.0
    reasons = []
    if odds_rank is not None and odds_rank <= 3:
        score += 4.0
        reasons.append(f"オッズ{odds_rank}位")
    if ai_rank is not None and ai_rank <= 3:
        score += 4.0
        reasons.append(f"AI{ai_rank}位")
    if ai_3ren_rank is not None and ai_3ren_rank <= 3:
        score += 3.0
        reasons.append(f"AI3連対{ai_3ren_rank}位")
    if general_3ren_rank is not None and general_3ren_rank <= 3:
        score += 1.0
        reasons.append(f"一般3連対{general_3ren_rank}位")
    if boat >= 5:
        score += 3.0
        reasons.append("5/6絡み")
    if lap_rank is not None and lap_rank <= 2:
        score += 2.0
        reasons.append(f"1周{lap_rank}位")
    if tenji_rank is not None and tenji_rank <= 2:
        score += 1.0
        reasons.append(f"展示{tenji_rank}位")
    if avg_diff >= 0.10:
        score += 1.0
        reasons.append(f"平均差+{avg_diff:.2f}")
    if boat == 1 and b1bonus:
        score += float(b1bonus)
        reasons.append("人気1号艇の2/3着保護")
    return score, reasons


def big50_dynamic_warning_tickets(
    rows,
    *,
    head_allowed,
    head_count,
    support_count,
    max_points,
    require_56,
    b1bonus,
    role_note,
):
    head_pool = []
    for row in rows or []:
        boat = int(row.get("boat_number") or 0)
        if boat in set(head_allowed):
            score, reasons = big50_balanced_head_score(row)
            head_pool.append((boat, score, reasons))
    support_pool = []
    for row in rows or []:
        boat = int(row.get("boat_number") or 0)
        if boat:
            score, reasons = big50_support_score(row, b1bonus=b1bonus)
            support_pool.append((boat, score, reasons))
    head_pool.sort(key=lambda item: (-item[1], item[0]))
    support_pool.sort(key=lambda item: (-item[1], item[0]))
    heads = [boat for boat, _, _ in head_pool[:head_count]]
    supports = [boat for boat, _, _ in support_pool[:support_count]]
    if not heads or len(supports) < 2:
        return set(), None

    head_score_map = {boat: score for boat, score, _ in head_pool}
    support_score_map = {boat: score for boat, score, _ in support_pool}
    candidate_tickets = []
    for head in heads:
        for second in supports:
            for third in supports:
                if len({head, second, third}) < 3:
                    continue
                ticket = f"{head}{second}{third}"
                if require_56 and not ({5, 6} & {head, second, third}):
                    continue
                ticket_score = (
                    head_score_map.get(head, 0.0) * 3.0
                    + support_score_map.get(second, 0.0) * 1.4
                    + support_score_map.get(third, 0.0)
                )
                candidate_tickets.append((ticket_score, ticket))
    candidate_tickets.sort(key=lambda item: (-item[0], item[1]))
    tickets = []
    seen = set()
    for _, ticket in candidate_tickets:
        if ticket in seen:
            continue
        tickets.append(ticket)
        seen.add(ticket)
        if len(tickets) >= max_points:
            break
    if not tickets:
        return set(), None

    used_boats = sorted({int(ch) for ticket in tickets for ch in ticket})
    head_reasons = {boat: reasons for boat, _, reasons in head_pool if boat in heads}
    support_reasons = {boat: reasons for boat, _, reasons in support_pool if boat in supports}
    unused_rows = [row for row in rows or [] if int(row.get("boat_number") or 0) not in set(used_boats)]
    keshi = None
    if unused_rows:
        keshi = int(
            sorted(
                unused_rows,
                key=lambda row: (
                    big50_support_score(row, b1bonus=b1bonus)[0],
                    int(row.get("boat_number") or 9),
                ),
            )[0].get("boat_number")
            or 0
        ) or None
    top3_axes = axis_boats_for_roles(rows, ranks=(1, 3))
    return set(tickets), {
        "heads": heads,
        "head_rule": "5万舟警戒: 外頭候補をオッズ/AI人気薄・展示/1周上位・平均との差で複合採点",
        "head_scores": {boat: round(score, 2) for boat, score, _ in head_pool if boat in heads},
        "head_reasons": head_reasons,
        "attackers": heads,
        "finishers": heads,
        "support_boats": used_boats,
        "support_scores": {boat: round(score, 2) for boat, score, _ in support_pool if boat in supports},
        "support_reasons": support_reasons,
        "axes": supports[:2],
        "axis_rule": "AI/オッズ/3連対/展示/1周/平均との差の支援スコア上位",
        "alt_axes": top3_axes,
        "alt_axis_rule": "比較軸は複合3着内率の上位",
        "supports": supports,
        "keshi": keshi,
        "keshi_reason": "5万舟警戒スコアで買い目に残らなかった艇を消し" if keshi else "-",
        "role_note": role_note,
    }


def big50_top4_11r_dynamic_10(rows):
    return big50_dynamic_warning_tickets(
        rows,
        head_allowed=(4, 5, 6),
        head_count=2,
        support_count=5,
        max_points=10,
        require_56=False,
        b1bonus=2.0,
        role_note="4場11Rの5万舟警戒。1号艇が売れすぎで展示が弱く、5/6に穴頭スコアが出た時だけ、外頭2艇から10点以内に絞る。",
    )


def big50_suminoe5_dynamic_8(rows):
    return big50_dynamic_warning_tickets(
        rows,
        head_allowed=(3, 4, 5, 6),
        head_count=1,
        support_count=5,
        max_points=8,
        require_56=True,
        b1bonus=0.0,
        role_note="住之江5R型の5万舟警戒。1号艇が売れすぎで展示4位以下、5/6に穴頭スコアが出た時だけ、頭1艇かつ5/6絡みに絞る。",
    )


def big50_top4_5r_red_static_no1_12(rows):
    tickets = {"325", "352", "346", "425", "452", "462", "524", "542", "536", "563", "624", "642"}
    head_scores = {}
    support_scores = {}
    for row in rows or []:
        boat = int(row.get("boat_number") or 0)
        if boat in {3, 4, 5, 6}:
            head_scores[boat] = round(big50_balanced_head_score(row)[0], 2)
        if boat:
            support_scores[boat] = round(big50_support_score(row, b1bonus=0.0)[0], 2)
    top3_axes = axis_boats_for_roles(rows, ranks=(1, 3))
    return tickets, {
        "heads": [3, 4, 5, 6],
        "head_rule": "5万舟警戒赤信号: 1号艇を全消しして3〜6号艇の外頭を固定",
        "head_scores": head_scores,
        "attackers": [3, 4, 5, 6],
        "finishers": [3, 4, 5, 6],
        "support_boats": [2, 3, 4, 5, 6],
        "support_scores": support_scores,
        "axes": [2, 5],
        "axis_rule": "赤信号固定: 2/5/6絡みを厚め、1号艇は買わない",
        "alt_axes": top3_axes,
        "alt_axis_rule": "比較軸は複合3着内率の上位",
        "supports": [2, 3, 4, 5, 6],
        "keshi": 1,
        "keshi_reason": "4場5R赤信号では、人気1号艇の平均差が大きく悪いため1号艇全消し",
        "role_note": "4場5Rの5万舟赤信号。1号艇が売れているのに平均との差-0.20以下、5/6に穴頭スコアが出た時だけ、1号艇を完全に切る12点。",
    }


def kiryu_wind6_b1odds45_h2_top3_no1_has56_12(rows):
    metrics = rows[0].get("_morning_metrics") or {}
    b1_odds_pct = as_num(metrics.get("boat1_odds_prediction_pct"))
    b1_odds_rank = int(as_num(metrics.get("boat1_odds_rank")) or 9)
    if b1_odds_rank != 1 or b1_odds_pct is None or b1_odds_pct < 45:
        return set(), None

    head_rows = sorted(
        [row for row in rows if row.get("boat_number") != 1],
        key=lambda row: (
            -venue_roi_win_score(row),
            row["boat_number"],
        ),
    )
    heads = [row["boat_number"] for row in head_rows[:2]]
    axis_rows = sorted(
        rows,
        key=lambda row: (
            -venue_roi_top3_score(row),
            row["boat_number"],
        ),
    )
    axes = [row["boat_number"] for row in axis_rows[:2]]
    if len(heads) < 2 or len(axes) < 2:
        return set(), None

    ai13_axes, _ai13_axis_rule = axis_boats_for_roles(rows, ranks=(1, 3))
    protected = set(heads + axes + ai13_axes)
    keshi_row = sorted(
        rows,
        key=lambda row: (
            bool(row.get("venue_dont_keshi") or row["boat_number"] in protected),
            venue_roi_top3_score(row),
            row["boat_number"],
        ),
    )[0]
    keshi = keshi_row["boat_number"]
    keshi_reason = f"桐生ROIルール専用: 複合3着内スコアが最も弱い{keshi}号艇を消し"
    ai_plus_rank6_row = next((row for row in rows if row.get("ai_plus_rank") == 6), {})
    ai_plus_rank6_boat = ai_plus_rank6_row.get("boat_number")
    ai_plus_rank6_revival = revive_reasons(ai_plus_rank6_row) if ai_plus_rank6_row else []
    if keshi is None:
        return set(), None
    supports = [boat for boat in range(2, 7) if boat != keshi]
    tickets = []
    seen = set()
    for head in heads:
        if head == 1:
            continue
        for axis in axes:
            if axis == head:
                continue
            for other in supports:
                if len({head, axis, other}) != 3:
                    continue
                for ticket in (f"{head}{axis}{other}", f"{head}{other}{axis}"):
                    nums = set(combo_boats(ticket))
                    if 1 in nums or not (nums & {5, 6}):
                        continue
                    if ticket in seen:
                        continue
                    seen.add(ticket)
                    tickets.append(ticket)
                    if len(tickets) >= 12:
                        break
                if len(tickets) >= 12:
                    break
            if len(tickets) >= 12:
                break
        if len(tickets) >= 12:
            break
    if len(tickets) < 2:
        return set(), None

    ticket_set = set(tickets)
    support_boats = sorted(
        {
            boat
            for ticket in ticket_set
            for boat in combo_boats(ticket)
            if boat not in set(heads)
        }
    )
    head_scores = {
        str(row["boat_number"]): {
            "score": round(venue_roi_win_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in head_rows[:2]
    }
    support_scores = {
        str(row["boat_number"]): {
            "score": round(venue_roi_top3_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in axis_rows[:4]
    }
    return ticket_set, {
        "heads": heads,
        "head_rule": "桐生専用。風6m以上で1号艇がオッズ評価45%以上の人気時に、1号艇以外の複合1着率上位2艇を頭にする",
        "head_mode": "kiryu_wind6_b1odds45_h2",
        "head_scores": head_scores,
        "attackers": heads,
        "attack_scores": head_scores,
        "finishers": heads,
        "finisher_scores": head_scores,
        "support_boats": support_boats,
        "support_scores": support_scores,
        "role_split_note": "桐生の自前AI再検証で残った、風6m以上+人気1号艇を疑う1号艇全消し小点数型",
        "axes": axes,
        "axis_rule": "複合3着内率の上位2艇",
        "alt_axes": ai13_axes,
        "alt_axis_rule": "消し保護ではAI+1位と3位も参照",
        "supports": supports,
        "keshi": keshi,
        "keshi_reason": keshi_reason,
        "ai_plus_rank6_boat": ai_plus_rank6_boat,
        "ai_plus_rank6_revival": ai_plus_rank6_revival,
        "role_note": (
            f"桐生専用本命。風6m以上で1号艇がオッズ評価1位{b1_odds_pct:.1f}%。"
            "1号艇を全消しし、頭は複合1着率上位2艇。"
            f"軸は複合3着内率上位{axes[0]},{axes[1]}、5/6絡みだけを{len(ticket_set)}点"
        ),
    }


def toda_b1odds40_nige40_outerbox6(rows):
    metrics = rows[0].get("_morning_metrics") or {}
    b1_odds_pct = as_num(metrics.get("boat1_odds_prediction_pct"))
    b1_odds_rank = int(as_num(metrics.get("boat1_odds_rank")) or 9)
    b1_nige_pct = as_num(metrics.get("boat1_nige_pct"))
    if (
        b1_odds_rank != 1
        or b1_odds_pct is None
        or b1_odds_pct < 40
        or b1_nige_pct is None
        or b1_nige_pct > 40
    ):
        return set(), None

    outer_rows = [row for row in rows if row.get("boat_number") in {3, 4, 5, 6}]
    outer_top2 = []
    for row in outer_rows:
        tenji_rank = valid_boat_rank(row.get("tenji_rank") or row.get("exhibit_rank") or row.get("tenji_time_rank"))
        lap_rank = valid_boat_rank(row.get("isshu_rank"))
        if (tenji_rank is not None and tenji_rank <= 2) or (lap_rank is not None and lap_rank <= 2):
            outer_top2.append(row)
    if not outer_top2:
        return set(), None

    head_rows = sorted(
        outer_rows,
        key=lambda row: (
            -venue_roi_win_score(row),
            row["boat_number"],
        ),
    )
    axis_rows = sorted(
        rows,
        key=lambda row: (
            -venue_roi_top3_score(row),
            row["boat_number"],
        ),
    )
    outer_axis_rows = sorted(
        outer_rows,
        key=lambda row: (
            -venue_roi_top3_score(row),
            row["boat_number"],
        ),
    )

    selected = []
    for row in head_rows[:2] + [row for row in axis_rows[:2] if row.get("boat_number") in {3, 4, 5, 6}] + outer_axis_rows:
        boat = row["boat_number"]
        if boat in selected:
            continue
        selected.append(boat)
        if len(selected) >= 3:
            break
    if len(selected) < 3:
        return set(), None

    tickets = set()
    for head in selected:
        for axis in selected:
            for other in selected:
                if len({head, axis, other}) != 3:
                    continue
                tickets.add(f"{head}{axis}{other}")
    if len(tickets) != 6:
        return set(), None

    selected_rows = [next(row for row in rows if row["boat_number"] == boat) for boat in selected]
    selected_scores = {
        str(row["boat_number"]): {
            "head_score": round(venue_roi_win_score(row), 3),
            "top3_score": round(venue_roi_top3_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in selected_rows
    }
    return tickets, {
        "heads": selected,
        "head_rule": "戸田専用。1号艇がオッズ評価40%以上で人気、かつ逃げ率40%以下の時に3〜6号艇から強い3艇を選ぶ",
        "head_mode": "toda_b1odds40_nige40_outerbox",
        "head_scores": selected_scores,
        "attackers": selected,
        "attack_scores": selected_scores,
        "finishers": selected,
        "finisher_scores": selected_scores,
        "support_boats": selected,
        "support_scores": selected_scores,
        "role_split_note": "戸田の長期検証で強かった、人気1号艇を疑って外3艇だけで組むBOX6点型",
        "axes": selected,
        "axis_rule": "3〜6号艇から複合1着率上位、複合3着内率上位、展示/1周上位を融合して選んだ3艇",
        "alt_axes": [],
        "alt_axis_rule": "戸田専用: 1号艇は全消し、外3艇BOX固定",
        "supports": selected,
        "keshi": 1,
        "keshi_reason": "戸田ROIルール専用: 1号艇は全消し",
        "ai_plus_rank6_boat": None,
        "ai_plus_rank6_revival": [],
        "role_note": (
            f"戸田専用本命。1号艇オッズ評価1位{b1_odds_pct:.1f}%だが逃げ率{b1_nige_pct:.1f}%で弱い。"
            f"外3〜6号艇に展示/1周2位以内があり、選抜外3艇{','.join(map(str, selected))}のBOX6点"
        ),
    }


def edogawa_r9_12_b1odds45_nige40_outertop2_h1_ai13_8(rows):
    metrics = rows[0].get("_morning_metrics") or {}
    b1_odds_pct = as_num(metrics.get("boat1_odds_prediction_pct"))
    b1_nige_pct = as_num(metrics.get("boat1_nige_pct"))
    head_rows = sorted(
        [row for row in rows if row.get("boat_number") != 1],
        key=lambda row: (
            -venue_roi_win_score(row),
            row["boat_number"],
        ),
    )
    if not head_rows:
        return set(), None
    heads = [head_rows[0]["boat_number"]]
    head = heads[0]
    axes, axis_rule = axis_boats_for_roles(rows, ranks=(1, 3))
    if len(axes) < 2:
        return set(), None
    top3_rows = sorted(
        rows,
        key=lambda row: (
            -venue_roi_top3_score(row),
            row["boat_number"],
        ),
    )
    top3_axes = [row["boat_number"] for row in top3_rows[:2]]
    protected = set([row["boat_number"] for row in head_rows[:2]] + axes + top3_axes)
    keshi_row = sorted(
        rows,
        key=lambda row: (
            bool(row.get("venue_dont_keshi") or row["boat_number"] in protected),
            venue_roi_top3_score(row),
            row["boat_number"],
        ),
    )[0]
    keshi = keshi_row["boat_number"]
    supports = [boat for boat in range(1, 7) if boat != keshi]

    tickets = []
    seen = set()
    for head in heads:
        for axis in axes:
            if axis == head:
                continue
            for other in supports:
                if len({head, axis, other}) != 3:
                    continue
                for ticket in (f"{head}{axis}{other}", f"{head}{other}{axis}"):
                    if ticket in seen:
                        continue
                    seen.add(ticket)
                    tickets.append(ticket)
                    if len(tickets) >= 8:
                        break
                if len(tickets) >= 8:
                    break
            if len(tickets) >= 8:
                break
        if len(tickets) >= 8:
            break
    if len(tickets) < 2:
        return set(), None

    ticket_set = set(tickets)
    head_scores = {
        str(row["boat_number"]): {
            "score": round(venue_roi_win_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in head_rows[:1]
    }
    support_scores = {
        str(row["boat_number"]): {
            "score": round(venue_roi_top3_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in top3_rows[:4]
    }
    ai_plus_rank6_row = next((row for row in rows if row.get("ai_plus_rank") == 6), {})
    ai_plus_rank6_boat = ai_plus_rank6_row.get("boat_number")
    ai_plus_rank6_revival = revive_reasons(ai_plus_rank6_row) if ai_plus_rank6_row else []
    return ticket_set, {
        "heads": heads,
        "head_rule": "江戸川専用。9〜12Rで1号艇オッズ評価45%以上かつ逃げ率40%以下、外艇展示上位ありの時に非1号艇の複合1着率最上位を頭にする",
        "head_mode": "edogawa_r9_12_b1odds45_nige40_outertop2_h1_ai13",
        "head_scores": head_scores,
        "attackers": heads,
        "attack_scores": head_scores,
        "finishers": heads,
        "finisher_scores": head_scores,
        "support_boats": sorted(
            {
                boat
                for ticket in ticket_set
                for boat in combo_boats(ticket)
                if boat not in set(heads)
            }
        ),
        "support_scores": support_scores,
        "role_split_note": "江戸川の長期検証で強かった、後半レースの人気1号艇を頭で疑い、展示上位の外艇を含めてAI+1位/3位で組む型",
        "axes": axes,
        "axis_rule": axis_rule,
        "alt_axes": top3_axes,
        "alt_axis_rule": "消し保護では複合3着内率の上位2艇も参照",
        "supports": supports,
        "keshi": keshi,
        "keshi_reason": f"江戸川ROIルール専用: 複合3着内スコアが弱い{keshi}号艇を消し",
        "ai_plus_rank6_boat": ai_plus_rank6_boat,
        "ai_plus_rank6_revival": ai_plus_rank6_revival,
        "role_note": (
            f"江戸川専用本命。9〜12Rで1号艇オッズ評価{b1_odds_pct or 0.0:.1f}%、"
            f"逃げ率{b1_nige_pct or 0.0:.1f}%で頭を疑う。"
            f"頭は非1号艇の複合1着率最上位{head}号艇、相手は{axis_rule}、"
            f"{keshi}号艇を消して{len(ticket_set)}点"
        ),
    }


def heiwajima_r9_12_b1odds55_nige65_outertop2_wave3_h2_no1_top6(rows):
    metrics = rows[0].get("_morning_metrics") or {}
    b1_odds_pct = as_num(metrics.get("boat1_odds_prediction_pct"))
    b1_odds_rank = int(as_num(metrics.get("boat1_odds_rank")) or 9)
    b1_nige_pct = as_num(metrics.get("boat1_nige_pct"))
    if (
        b1_odds_rank != 1
        or b1_odds_pct is None
        or b1_odds_pct < 55
        or b1_nige_pct is None
        or b1_nige_pct > 65
    ):
        return set(), None

    def exhibit_or_lap_rank(row, default=9):
        tenji_rank = valid_boat_rank(row.get("tenji_rank") or row.get("exhibit_rank") or row.get("tenji_time_rank"))
        lap_rank = valid_boat_rank(row.get("isshu_rank"))
        ranks = [rank for rank in (tenji_rank, lap_rank) if rank is not None]
        return min(ranks) if ranks else default

    outer_rows = [row for row in rows if row.get("boat_number") in {3, 4, 5, 6}]
    outer_top2_boats = [
        row["boat_number"] for row in outer_rows if exhibit_or_lap_rank(row) <= 2
    ]
    if not outer_top2_boats:
        return set(), None

    # Keep the exact mined template used by the 2024-2026 validation.  The
    # formation scorer uses only information available before the deadline.
    ticket_order = original_boaters_forward.ticket_families(rows).get("non1_h2_no1", [])[:6]
    if len(ticket_order) != 6:
        return set(), None
    ticket_set = set(ticket_order)
    heads = original_boaters_forward.intended_heads(rows, "non1_h2_no1")
    if len(heads) != 2:
        return set(), None

    head_rows = sorted(
        [row for row in rows if row.get("boat_number") != 1],
        key=lambda row: (
            -original_boaters_forward.pre_race_win_score(row),
            row["boat_number"],
        ),
    )
    axis_rows = sorted(
        [row for row in rows if row.get("boat_number") != 1],
        key=lambda row: (
            -original_boaters_forward.pre_race_top3_score(row),
            row["boat_number"],
        ),
    )
    axes = [row["boat_number"] for row in axis_rows[:2]]
    supports = sorted(
        {
            boat
            for ticket in ticket_set
            for boat in combo_boats(ticket)
            if boat not in set(heads)
        }
    )
    head_scores = {
        str(row["boat_number"]): {
            "score": round(original_boaters_forward.pre_race_win_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in head_rows[:2]
    }
    support_scores = {
        str(row["boat_number"]): {
            "score": round(original_boaters_forward.pre_race_top3_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in axis_rows[:4]
    }
    ai_plus_rank6_row = next((row for row in rows if row.get("ai_plus_rank") == 6), {})
    ai_plus_rank6_boat = ai_plus_rank6_row.get("boat_number")
    ai_plus_rank6_revival = revive_reasons(ai_plus_rank6_row) if ai_plus_rank6_row else []
    return ticket_set, {
        "heads": heads,
        "head_rule": "平和島専用。後半9〜12Rで人気1号艇の支持55%以上・逃げ率65%以下、外展示上位、波3cm以上が揃った時に1号艇を消す",
        "head_mode": "heiwajima_r9_12_b1odds55_nige65_outertop2_wave3_h2_no1_top6",
        "head_scores": head_scores,
        "attackers": heads,
        "attack_scores": head_scores,
        "finishers": heads,
        "finisher_scores": head_scores,
        "support_boats": sorted(
            {
                boat
                for ticket in ticket_set
                for boat in combo_boats(ticket)
                if boat not in set(heads)
            }
        ),
        "support_scores": support_scores,
        "role_split_note": "平和島の時系列検証で選ばれた、後半荒れ水面の人気1号艇を疑う6点固定型",
        "axes": axes,
        "axis_rule": "固定軸なし。複合3着内評価で全組合せを順位付け",
        "alt_axes": [],
        "alt_axis_rule": "なし",
        "supports": supports,
        "keshi": 1,
        "keshi_reason": "平和島再分析ルール専用: 人気1号艇を全買い目から消し",
        "ai_plus_rank6_boat": ai_plus_rank6_boat,
        "ai_plus_rank6_revival": ai_plus_rank6_revival,
        "role_note": (
            f"平和島専用本命。1号艇オッズ評価1位{b1_odds_pct:.1f}%・逃げ率{b1_nige_pct:.1f}%だが、"
            f"外展示/1周2位以内{','.join(map(str, outer_top2_boats))}号艇がいる荒れ水面。"
            f"頭は締切前複合1着評価上位{heads[0]},{heads[1]}、1号艇全消しの上位{len(ticket_set)}点"
        ),
    }


def tamagawa_r4_6_b1odds40_venue_debuff_h2_ai13_no1_has56_12(rows):
    metrics = rows[0].get("_morning_metrics") or {}
    b1_odds_pct = as_num(metrics.get("boat1_odds_prediction_pct"))
    b1_odds_rank = int(as_num(metrics.get("boat1_odds_rank")) or 9)
    b1_row = next((row for row in rows if row.get("boat_number") == 1), {})
    if (
        b1_odds_rank != 1
        or b1_odds_pct is None
        or b1_odds_pct < 40
        or not b1_row.get("venue_b1_head_debuff")
    ):
        return set(), None

    head_rows = sorted(
        [row for row in rows if row.get("boat_number") != 1],
        key=lambda row: (
            -venue_roi_win_score(row),
            row["boat_number"],
        ),
    )
    heads = [row["boat_number"] for row in head_rows[:2]]
    axes, axis_rule = axis_boats_for_roles(rows, ranks=(1, 3))
    if len(heads) < 2 or len(axes) < 2:
        return set(), None

    axis_top3_rows = sorted(
        rows,
        key=lambda row: (
            -venue_roi_top3_score(row),
            row["boat_number"],
        ),
    )
    axes_top3 = [row["boat_number"] for row in axis_top3_rows[:2]]
    protected = set(heads + axes + axes_top3)
    keshi_row = sorted(
        rows,
        key=lambda row: (
            bool(row.get("venue_dont_keshi") or row["boat_number"] in protected),
            venue_roi_top3_score(row),
            row["boat_number"],
        ),
    )[0]
    keshi = keshi_row["boat_number"]
    supports = [boat for boat in range(2, 7) if boat != keshi]

    tickets = original_boaters_forward.tamagawa_h2_ai13_no1_has56(rows)
    if len(tickets) < 2:
        return set(), None

    ticket_set = set(tickets)
    head_scores = {
        str(row["boat_number"]): {
            "score": round(venue_roi_win_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in head_rows[:2]
    }
    support_scores = {
        str(row["boat_number"]): {
            "score": round(venue_roi_top3_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in axis_top3_rows[:4]
    }
    b1_debuff_reasons = b1_row.get("venue_factor_reasons") or []
    ai_plus_rank6_row = next((row for row in rows if row.get("ai_plus_rank") == 6), {})
    ai_plus_rank6_boat = ai_plus_rank6_row.get("boat_number")
    ai_plus_rank6_revival = revive_reasons(ai_plus_rank6_row) if ai_plus_rank6_row else []
    return ticket_set, {
        "heads": heads,
        "head_rule": "多摩川専用。4〜6Rで1号艇がオッズ評価40%以上の人気、かつ場別展示S/Aで1号艇頭デバフが出た時に1号艇を全消し",
        "head_mode": "tamagawa_b1odds40_venue_debuff_h2",
        "head_scores": head_scores,
        "attackers": heads,
        "attack_scores": head_scores,
        "finishers": heads,
        "finisher_scores": head_scores,
        "support_boats": sorted(
            {
                boat
                for ticket in ticket_set
                for boat in combo_boats(ticket)
                if boat not in set(heads)
            }
        ),
        "support_scores": support_scores,
        "role_split_note": "多摩川の時系列再分析で強かった、4〜6Rの人気1号艇に場別展示デバフが出た時の1号艇全消し型",
        "axes": axes,
        "axis_rule": axis_rule,
        "alt_axes": axes_top3,
        "alt_axis_rule": "消し保護では複合3着内率の上位2艇も参照",
        "supports": supports,
        "keshi": keshi,
        "keshi_reason": f"多摩川ROIルール専用: 複合3着内スコアが弱い{keshi}号艇を消し",
        "ai_plus_rank6_boat": ai_plus_rank6_boat,
        "ai_plus_rank6_revival": ai_plus_rank6_revival,
        "role_note": (
            f"多摩川専用本命。4〜6Rで1号艇オッズ評価1位{b1_odds_pct:.1f}%だが、"
            f"場別展示デバフ({'; '.join(b1_debuff_reasons[:2]) or '詳細あり'})で1号艇を全消し。"
            f"頭は複合1着率上位{heads[0]},{heads[1]}、軸は{axis_rule}、5/6絡みだけを{len(ticket_set)}点"
        ),
    }


def hamanako_r1_3_wave2_revival_b1avg000_outer56avg005_outerh2_no1_has56_4(rows):
    metrics = rows[0].get("_morning_metrics") or {}
    b1_avgdiff = as_num(metrics.get("boat1_avg_isshu_diff"))
    outer56_avgdiff = as_num(metrics.get("outer56_best_avg_isshu_diff"))
    revival_rows = [row for row in rows if row.get("venue_low_ai_revival")]
    if (
        not revival_rows
        or b1_avgdiff is None
        or b1_avgdiff > 0.0
        or outer56_avgdiff is None
        or outer56_avgdiff < 0.05
    ):
        return set(), None

    ticket_list = original_boaters_forward.ticket_families(rows).get(
        "outer_h2_no1_has56", []
    )[:4]
    if len(ticket_list) != 4:
        return set(), None
    tickets = set(ticket_list)

    outer_rows = sorted(
        [row for row in rows if row.get("boat_number") in {3, 4, 5, 6}],
        key=lambda row: (
            -original_boaters_forward.pre_race_win_score(row),
            row["boat_number"],
        ),
    )
    heads = [int(row["boat_number"]) for row in outer_rows[:2]]
    ticket_boats = sorted({boat for ticket in tickets for boat in combo_boats(ticket)})
    support_rows = sorted(
        [row for row in rows if row.get("boat_number") in ticket_boats],
        key=lambda row: (
            -original_boaters_forward.pre_race_top3_score(row),
            row["boat_number"],
        ),
    )
    supports = [int(row["boat_number"]) for row in support_rows]
    score_rows = [row for row in rows if row.get("boat_number") in ticket_boats]
    selected_scores = {
        str(row["boat_number"]): {
            "head_score": round(original_boaters_forward.pre_race_win_score(row), 3),
            "top3_score": round(original_boaters_forward.pre_race_top3_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in score_rows
    }
    revival_summary = venue_low_ai_revival_summary(rows)
    revival_reason = "; ".join(item.get("reason") or "" for item in revival_summary[:2]).strip("; ")
    return tickets, {
        "heads": heads,
        "head_rule": "浜名湖専用。1〜3R・波2cm以上・場別展示復活バフ・1号艇平均との差0.00以下・5/6号艇平均との差+0.05以上を同時に満たす時、3〜6号艇の複合1着評価上位2艇を頭にする",
        "head_mode": "hamanako_wave2_revival_b1avg000_outer56avg005_outer_h2",
        "head_scores": selected_scores,
        "attackers": heads,
        "attack_scores": selected_scores,
        "finishers": heads,
        "finisher_scores": selected_scores,
        "support_boats": supports,
        "support_scores": selected_scores,
        "role_split_note": "浜名湖の時系列再分析で強かった、弱い1号艇と浮上した5/6号艇を同時確認する1号艇全消し4点型",
        "axes": supports[:2],
        "axis_rule": "買い目内の複合3着内評価上位を相手軸にする",
        "alt_axes": [],
        "alt_axis_rule": "浜名湖専用: 5/6号艇を必ず含む上位4点固定",
        "supports": supports,
        "keshi": 1,
        "keshi_reason": f"浜名湖ROIルール専用: 1号艇平均との差{b1_avgdiff:+.2f}のため1号艇を全消し",
        "ai_plus_rank6_boat": next((row.get("boat_number") for row in rows if row.get("ai_plus_rank") == 6), None),
        "ai_plus_rank6_revival": revive_reasons(next((row for row in rows if row.get("ai_plus_rank") == 6), {})),
        "venue_low_ai_revivals": revival_summary,
        "role_note": (
            "浜名湖専用本命。前半の波あり水面で、"
            f"{revival_reason or '低評価艇に場別展示復活バフあり'}。"
            f"1号艇平均との差{b1_avgdiff:+.2f}、5/6号艇の良い方{outer56_avgdiff:+.2f}。"
            f"頭{','.join(map(str, heads))}、1号艇全消し、5/6絡みの上位4点"
        ),
    }


def gamagori_b1lap4_b1odds35_b1loss30_outer_h1_ai13_no1_has56_8(rows):
    metrics = rows[0].get("_morning_metrics") or {}
    b1_odds_pct = as_num(metrics.get("boat1_odds_prediction_pct"))
    b1_loss_pct = as_num(metrics.get("boat1_loss_pct"))

    head_rows_outer = sorted(
        [row for row in rows if row.get("boat_number") in {3, 4, 5, 6}],
        key=lambda row: (
            -venue_roi_win_score(row),
            row["boat_number"],
        ),
    )
    heads = [row["boat_number"] for row in head_rows_outer[:1]]
    axes, axis_rule = axis_boats_for_roles(rows, ranks=(1, 3))
    if len(heads) < 1 or len(axes) < 2:
        return set(), None

    head_rows_non1 = sorted(
        [row for row in rows if row.get("boat_number") != 1],
        key=lambda row: (
            -venue_roi_win_score(row),
            row["boat_number"],
        ),
    )
    axis_top3_rows = sorted(
        rows,
        key=lambda row: (
            -venue_roi_top3_score(row),
            row["boat_number"],
        ),
    )
    protected = set([row["boat_number"] for row in head_rows_non1[:2]] + axes + [row["boat_number"] for row in axis_top3_rows[:2]])
    keshi_row = sorted(
        rows,
        key=lambda row: (
            bool(row.get("venue_dont_keshi") or row["boat_number"] in protected),
            venue_roi_top3_score(row),
            row["boat_number"],
        ),
    )[0]
    keshi = keshi_row["boat_number"]
    supports = [boat for boat in range(2, 7) if boat != keshi]

    tickets = []
    seen = set()
    head = heads[0]
    for axis in axes:
        if axis == head:
            continue
        for other in supports:
            if len({head, axis, other}) != 3:
                continue
            for ticket in (f"{head}{axis}{other}", f"{head}{other}{axis}"):
                nums = set(combo_boats(ticket))
                if 1 in nums or not (nums & {5, 6}):
                    continue
                if ticket in seen:
                    continue
                seen.add(ticket)
                tickets.append(ticket)
                if len(tickets) >= 8:
                    break
            if len(tickets) >= 8:
                break
        if len(tickets) >= 8:
            break
    if len(tickets) < 2:
        return set(), None

    ticket_set = set(tickets)
    selected_boats = sorted({boat for ticket in ticket_set for boat in combo_boats(ticket)})
    selected_rows = [next(row for row in rows if row["boat_number"] == boat) for boat in selected_boats]
    selected_scores = {
        str(row["boat_number"]): {
            "head_score": round(venue_roi_win_score(row), 3),
            "top3_score": round(venue_roi_top3_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in selected_rows
    }
    head_scores = {
        str(row["boat_number"]): {
            "score": round(venue_roi_win_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in head_rows_outer[:1]
    }
    support_scores = {
        str(row["boat_number"]): {
            "score": round(venue_roi_top3_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in axis_top3_rows[:4]
    }
    ai_plus_rank6_row = next((row for row in rows if row.get("ai_plus_rank") == 6), {})
    ai_plus_rank6_boat = ai_plus_rank6_row.get("boat_number")
    ai_plus_rank6_revival = revive_reasons(ai_plus_rank6_row) if ai_plus_rank6_row else []
    return ticket_set, {
        "heads": heads,
        "head_rule": "蒲郡専用。1号艇が人気だが、1周4位以下かつ逃げ失敗率30%以上の時に外3〜6号艇の複合1着率最上位を頭にする",
        "head_mode": "gamagori_b1lap4_b1odds35_b1loss30_outer_h1",
        "head_scores": head_scores,
        "attackers": heads,
        "attack_scores": head_scores,
        "finishers": heads,
        "finisher_scores": head_scores,
        "support_boats": selected_boats,
        "support_scores": {**selected_scores, **support_scores},
        "role_split_note": "蒲郡の長期検証で強かった、人気1号艇の1周弱さと逃げ失敗率を起点にした外頭1艇型",
        "axes": axes,
        "axis_rule": axis_rule,
        "alt_axes": [row["boat_number"] for row in axis_top3_rows[:2]],
        "alt_axis_rule": "消し保護では複合3着内率の上位2艇も参照",
        "supports": supports,
        "keshi": keshi,
        "keshi_reason": f"蒲郡ROIルール専用: 複合3着内スコアが弱い{keshi}号艇を消し",
        "ai_plus_rank6_boat": ai_plus_rank6_boat,
        "ai_plus_rank6_revival": ai_plus_rank6_revival,
        "role_note": (
            f"蒲郡専用本命。1号艇オッズ評価1位{b1_odds_pct:.1f}%だが、"
            f"1周4位以下+逃げ失敗{b1_loss_pct:.1f}%で1号艇を全消し。"
            f"頭は外3〜6号艇の複合1着率最上位{head}号艇、軸は{axis_rule}、5/6絡みだけを{len(ticket_set)}点"
        ),
    }


def tokoname_b1loss40_b5top3rank1_wind4_h1_56_ai13_no1_has56_8(rows):
    metrics = rows[0].get("_morning_metrics") or {}
    b1_loss_pct = as_num(metrics.get("boat1_loss_pct"))
    b5_row = next((row for row in rows if row.get("boat_number") == 5), {})
    head_rows = sorted(
        [row for row in rows if row.get("boat_number") in {5, 6}],
        key=lambda row: (
            -venue_roi_win_score(row),
            row["boat_number"],
        ),
    )
    if not head_rows:
        return set(), None
    heads = [head_rows[0]["boat_number"]]
    head = heads[0]
    axes, axis_rule = axis_boats_for_roles(rows, ranks=(1, 3))
    axis_top3_rows = sorted(
        rows,
        key=lambda row: (
            -venue_roi_top3_score(row),
            row["boat_number"],
        ),
    )
    supports = [row["boat_number"] for row in axis_top3_rows if row.get("boat_number") not in {1, head}]
    if len(axes) < 2 or len(supports) < 2:
        return set(), None
    axis_candidates = []
    for boat in axes + supports[:2]:
        if boat not in axis_candidates:
            axis_candidates.append(boat)

    tickets = []
    seen = set()
    for axis in axis_candidates:
        if axis == head:
            continue
        for other in supports:
            if len({head, axis, other}) != 3:
                continue
            for ticket in (f"{head}{axis}{other}", f"{head}{other}{axis}"):
                nums = set(combo_boats(ticket))
                if 1 in nums or not (nums & {5, 6}):
                    continue
                if ticket in seen:
                    continue
                seen.add(ticket)
                tickets.append(ticket)
                if len(tickets) >= 8:
                    break
            if len(tickets) >= 8:
                break
        if len(tickets) >= 8:
            break
    if len(tickets) < 2:
        return set(), None

    ticket_set = set(tickets)
    head_scores = {
        str(row["boat_number"]): {
            "score": round(venue_roi_win_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in head_rows[:1]
    }
    support_scores = {
        str(row["boat_number"]): {
            "score": round(venue_roi_top3_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in axis_top3_rows[:4]
    }
    ai_plus_rank6_row = next((row for row in rows if row.get("ai_plus_rank") == 6), {})
    ai_plus_rank6_boat = ai_plus_rank6_row.get("boat_number")
    ai_plus_rank6_revival = revive_reasons(ai_plus_rank6_row) if ai_plus_rank6_row else []
    b5_top3_rank = next(
        (index for index, row in enumerate(axis_top3_rows, 1) if row.get("boat_number") == 5),
        None,
    )
    b5_top3_score = venue_roi_top3_score(b5_row) if b5_row else None
    support_boats = sorted({boat for ticket in ticket_set for boat in combo_boats(ticket) if boat != head})
    return ticket_set, {
        "heads": heads,
        "head_rule": "常滑専用。1号艇逃げ失敗率40%以上、5号艇が複合3着内スコア1位、風4m以上の時に1号艇を全消し",
        "head_mode": "tokoname_b1loss40_b5top3rank1_wind4_h1_56_ai13",
        "head_scores": head_scores,
        "attackers": heads,
        "attack_scores": head_scores,
        "finishers": heads,
        "finisher_scores": head_scores,
        "support_boats": support_boats,
        "support_scores": support_scores,
        "role_split_note": "常滑の長期検証で強かった、1号艇の逃げ不安と5号艇の複合3着内評価、風を組み合わせる5/6頭1艇型",
        "axes": axis_candidates,
        "axis_rule": axis_rule,
        "alt_axes": [row["boat_number"] for row in axis_top3_rows[:2]],
        "alt_axis_rule": "AI+1位/3位に加え、複合3着内スコア上位で補完",
        "supports": supports,
        "keshi": 1,
        "keshi_reason": "常滑ROIルール専用: 1号艇は全消し",
        "ai_plus_rank6_boat": ai_plus_rank6_boat,
        "ai_plus_rank6_revival": ai_plus_rank6_revival,
        "role_note": (
            f"常滑専用本命。1号艇逃げ失敗{b1_loss_pct or 0.0:.1f}%で危なく、"
            f"5号艇が複合3着内スコア{b5_top3_score or 0.0:.1f}の{b5_top3_rank or 9}位。"
            f"頭は5/6号艇の複合1着率上位{head}、相手はAI+1位/3位と複合3着内上位、"
            f"1号艇全消し+5/6絡みだけを{len(ticket_set)}点"
        ),
    }


def tsu_r4_8_top3buff12_top2heads56_h1_top3_has56_8(rows):
    top3_buff_items = venue_top3_buff_items(rows, min_pp=12.0)
    if not top3_buff_items:
        return set(), None
    top3_buff_reason = venue_top3_buff_text(top3_buff_items[0])

    head_rows = sorted(
        [row for row in rows if row.get("boat_number") != 1],
        key=lambda row: (
            -venue_roi_win_score(row),
            row["boat_number"],
        ),
    )
    heads_for_protection = [row["boat_number"] for row in head_rows[:2]]
    heads = heads_for_protection[:1]
    axis_top3_rows = sorted(
        rows,
        key=lambda row: (
            -venue_roi_top3_score(row),
            row["boat_number"],
        ),
    )
    axes = [row["boat_number"] for row in axis_top3_rows[:2]]
    ai13_axes, _ai13_axis_rule = axis_boats_for_roles(rows, ranks=(1, 3))
    if len(heads) < 1 or len(axes) < 2:
        return set(), None

    protected = set(heads_for_protection + ai13_axes + axes)
    keshi_row = sorted(
        rows,
        key=lambda row: (
            bool(row.get("venue_dont_keshi") or row["boat_number"] in protected),
            venue_roi_top3_score(row),
            row["boat_number"],
        ),
    )[0]
    keshi = keshi_row["boat_number"]
    supports = [boat for boat in range(1, 7) if boat != keshi]

    tickets = []
    seen = set()
    head = heads[0]
    for axis in axes:
        if axis == head:
            continue
        for other in supports:
            if len({head, axis, other}) != 3:
                continue
            for ticket in (f"{head}{axis}{other}", f"{head}{other}{axis}"):
                nums = set(combo_boats(ticket))
                if not (nums & {5, 6}):
                    continue
                if ticket in seen:
                    continue
                seen.add(ticket)
                tickets.append(ticket)
                if len(tickets) >= 8:
                    break
            if len(tickets) >= 8:
                break
        if len(tickets) >= 8:
            break
    if len(tickets) < 2:
        return set(), None

    ticket_set = set(tickets)
    selected_boats = sorted({boat for ticket in ticket_set for boat in combo_boats(ticket)})
    selected_rows = [next(row for row in rows if row["boat_number"] == boat) for boat in selected_boats]
    selected_scores = {
        str(row["boat_number"]): {
            "head_score": round(venue_roi_win_score(row), 3),
            "top3_score": round(venue_roi_top3_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in selected_rows
    }
    head_scores = {
        str(row["boat_number"]): {
            "score": round(venue_roi_win_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in head_rows[:1]
    }
    support_scores = {
        str(row["boat_number"]): {
            "score": round(venue_roi_top3_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in axis_top3_rows[:4]
    }
    ai_plus_rank6_row = next((row for row in rows if row.get("ai_plus_rank") == 6), {})
    ai_plus_rank6_boat = ai_plus_rank6_row.get("boat_number")
    ai_plus_rank6_revival = revive_reasons(ai_plus_rank6_row) if ai_plus_rank6_row else []
    axis_rule = "複合3着内率の1位と2位"
    return ticket_set, {
        "heads": heads,
        "head_rule": "津専用。4〜8Rで場別3着内バフ12pt以上があり、頭候補上位2艇に5/6号艇がいる時に、非1号艇の複合1着率最上位1艇を頭にする",
        "head_mode": "tsu_r4_8_top3buff12_top2heads56_h1_top3_has56",
        "head_scores": head_scores,
        "attackers": heads,
        "attack_scores": head_scores,
        "finishers": heads,
        "finisher_scores": head_scores,
        "support_boats": selected_boats,
        "support_scores": {**selected_scores, **support_scores},
        "role_split_note": "津のフル展示検証で強かった、中盤の場別3着内バフと5/6号艇の頭浮上を合わせる小点数型",
        "axes": axes,
        "axis_rule": axis_rule,
        "alt_axes": ai13_axes,
        "alt_axis_rule": "消し保護ではAI+1位/3位も参照",
        "supports": supports,
        "keshi": keshi,
        "keshi_reason": f"津ROIルール専用: 複合3着内スコアが弱い{keshi}号艇を消し",
        "ai_plus_rank6_boat": ai_plus_rank6_boat,
        "ai_plus_rank6_revival": ai_plus_rank6_revival,
        "role_note": (
            f"津専用本命。4〜8Rで{top3_buff_reason}、頭候補上位2艇に5/6号艇あり。"
            f"頭は非1号艇の複合1着率最上位{head}号艇、軸は{axis_rule}、"
            f"5/6絡みだけを{len(ticket_set)}点"
        ),
    }


def mikuni_r9_12_wind5_lowai_h2_ai13_no1_has56_12(rows):
    head_rows_non1 = sorted(
        [row for row in rows if row.get("boat_number") != 1],
        key=lambda row: (
            -venue_roi_win_score(row),
            row["boat_number"],
        ),
    )
    heads = [row["boat_number"] for row in head_rows_non1[:2]]
    axes, axis_rule = axis_boats_for_roles(rows, ranks=(1, 3))
    if len(heads) < 2 or len(axes) < 2:
        return set(), None

    axis_top3_rows = sorted(
        rows,
        key=lambda row: (
            -venue_roi_top3_score(row),
            row["boat_number"],
        ),
    )
    top3_axes = [row["boat_number"] for row in axis_top3_rows[:2]]
    protected = set(heads + axes + top3_axes)
    keshi_row = sorted(
        rows,
        key=lambda row: (
            bool(row.get("venue_dont_keshi") or row["boat_number"] in protected),
            venue_roi_top3_score(row),
            row["boat_number"],
        ),
    )[0]
    keshi = keshi_row["boat_number"]
    supports = [boat for boat in range(2, 7) if boat != keshi]

    tickets = []
    seen = set()
    for head in heads:
        if head == 1:
            continue
        for axis in axes:
            if axis == head:
                continue
            for other in supports:
                if len({head, axis, other}) != 3:
                    continue
                for ticket in (f"{head}{axis}{other}", f"{head}{other}{axis}"):
                    nums = set(combo_boats(ticket))
                    if 1 in nums or not (nums & {5, 6}):
                        continue
                    if ticket in seen:
                        continue
                    seen.add(ticket)
                    tickets.append(ticket)
                    if len(tickets) >= BUY_TICKET_MAX_POINTS:
                        break
                if len(tickets) >= BUY_TICKET_MAX_POINTS:
                    break
            if len(tickets) >= BUY_TICKET_MAX_POINTS:
                break
        if len(tickets) >= BUY_TICKET_MAX_POINTS:
            break
    if len(tickets) < 2:
        return set(), None

    ticket_set = set(tickets)
    head_scores = {
        str(row["boat_number"]): {
            "score": round(venue_roi_win_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in head_rows_non1[:2]
    }
    support_scores = {
        str(row["boat_number"]): {
            "score": round(venue_roi_top3_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in axis_top3_rows[:4]
    }
    low_ai_revival_items = venue_low_ai_revival_summary(rows)
    low_ai_revival_reasons = [
        item.get("reason")
        for item in low_ai_revival_items[:2]
        if item.get("reason")
    ]
    ai_plus_rank6_row = next((row for row in rows if row.get("ai_plus_rank") == 6), {})
    ai_plus_rank6_boat = ai_plus_rank6_row.get("boat_number")
    ai_plus_rank6_revival = revive_reasons(ai_plus_rank6_row) if ai_plus_rank6_row else []
    return ticket_set, {
        "heads": heads,
        "head_rule": "三国専用。9〜12Rで風5m以上、低AI艇に場別展示バフ復活が出た時に、1号艇を全消しして非1号艇の複合1着率上位2艇を頭にする",
        "head_mode": "mikuni_r9_12_wind5_lowai_h2_ai13_no1_has56",
        "head_scores": head_scores,
        "attackers": heads,
        "attack_scores": head_scores,
        "finishers": heads,
        "finisher_scores": head_scores,
        "support_boats": sorted(
            {
                boat
                for ticket in ticket_set
                for boat in combo_boats(ticket)
                if boat not in set(heads)
            }
        ),
        "support_scores": support_scores,
        "role_split_note": "三国のフル展示検証で安定した、後半の強風と低AI艇の場別バフ復活を合わせる1号艇全消し型",
        "axes": axes,
        "axis_rule": axis_rule,
        "alt_axes": top3_axes,
        "alt_axis_rule": "消し保護では複合3着内率上位2艇も参照",
        "supports": supports,
        "keshi": keshi,
        "keshi_reason": f"三国ROIルール専用: 複合3着内スコアが弱い{keshi}号艇を消し",
        "ai_plus_rank6_boat": ai_plus_rank6_boat,
        "ai_plus_rank6_revival": ai_plus_rank6_revival,
        "low_ai_revival_reasons": low_ai_revival_reasons,
        "role_note": (
            "三国専用本命。9〜12Rの風5m以上で低AI艇の場別展示バフ復活あり。"
            f"頭は非1号艇の複合1着率上位{heads[0]},{heads[1]}、軸は{axis_rule}、"
            f"1号艇全消し+5/6絡みだけを{len(ticket_set)}点"
        ),
    }


def biwako_top3buff15_lowai_box3_has56_6(rows):
    buff_items = [
        item
        for row in rows
        for item in (row.get("venue_factor_matches") or [])
        if item.get("direction") == "buff"
    ]
    top3_items = sorted(
        [item for item in buff_items if (as_num(item.get("top3_rate_pp")) or 0.0) >= 15.0],
        key=lambda item: (as_num(item.get("top3_rate_pp")) or 0.0),
        reverse=True,
    )
    if not top3_items or not any(row.get("venue_low_ai_revival") for row in rows):
        return set(), None

    head_rows = sorted(
        [row for row in rows if row.get("boat_number") != 1],
        key=lambda row: (
            -venue_roi_win_score(row),
            row["boat_number"],
        ),
    )
    axes, axis_rule = axis_boats_for_roles(rows, ranks=(1, 3))
    if len(axes) < 2:
        return set(), None

    axis_top3_rows = sorted(
        rows,
        key=lambda row: (
            -venue_roi_top3_score(row),
            row["boat_number"],
        ),
    )

    selected = []
    for row in head_rows[:2] + axis_top3_rows[:2] + [row_by_boat(rows, boat) for boat in axes]:
        boat = row.get("boat_number")
        if not boat or boat in selected:
            continue
        selected.append(boat)
        if len(selected) >= 3:
            break
    if len(selected) < 3 or not ({5, 6} & set(selected)):
        return set(), None

    ticket_set = {
        f"{head}{second}{third}"
        for head in selected
        for second in selected
        for third in selected
        if len({head, second, third}) == 3
    }
    if len(ticket_set) != 6:
        return set(), None

    head_scores = {
        str(row["boat_number"]): {
            "score": round(venue_roi_win_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in head_rows[:2]
    }
    selected_rows = [row_by_boat(rows, boat) for boat in selected]
    selected_scores = {
        str(row["boat_number"]): {
            "head_score": round(venue_roi_win_score(row), 3),
            "top3_score": round(venue_roi_top3_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in selected_rows
        if row
    }
    top3_scores = {
        str(row["boat_number"]): {
            "score": round(venue_roi_top3_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in axis_top3_rows[:4]
    }
    top3_reason = f"{top3_items[0].get('lane')}号艇{top3_items[0].get('metric_label')} 3着内差+{as_num(top3_items[0].get('top3_rate_pp')) or 0.0:.1f}pt"
    revival_summary = venue_low_ai_revival_summary(rows)
    revival_reason = "; ".join(item.get("reason") or "" for item in revival_summary[:2]).strip("; ")
    ai_plus_rank6_row = next((row for row in rows if row.get("ai_plus_rank") == 6), {})
    ai_plus_rank6_boat = ai_plus_rank6_row.get("boat_number")
    ai_plus_rank6_revival = revive_reasons(ai_plus_rank6_row) if ai_plus_rank6_row else []
    keshi_boats = sorted(set(range(1, 7)) - set(selected))
    return ticket_set, {
        "heads": selected,
        "head_rule": "びわこ専用。場別展示で3着内+15pt以上、かつ低AI艇に場別展示バフ復活が出た時に、複合上位3艇を選んで5/6絡みBOX6点にする",
        "head_mode": "biwako_top3buff15_lowai_box3_has56",
        "head_scores": {**head_scores, **selected_scores},
        "attackers": selected,
        "attack_scores": selected_scores,
        "finishers": selected,
        "finisher_scores": selected_scores,
        "support_boats": sorted(
            {
                boat
                for ticket in ticket_set
                for boat in combo_boats(ticket)
            }
        ),
        "support_scores": {**selected_scores, **top3_scores},
        "role_split_note": "びわこの長期検証で安定した、強い場別3着内バフと低AI艇の復活を合わせる3艇BOX型",
        "axes": selected,
        "axis_rule": f"複合1着率上位2艇、複合3着内率上位2艇、{axis_rule}を融合して選んだ3艇",
        "alt_axes": axes,
        "alt_axis_rule": "BOX選抜ではAI+1位/3位も参照",
        "supports": selected,
        "keshi": keshi_boats[0] if keshi_boats else None,
        "keshi_reason": f"びわこROIルール専用: 選抜3艇以外({','.join(map(str, keshi_boats))})は買わない",
        "ai_plus_rank6_boat": ai_plus_rank6_boat,
        "ai_plus_rank6_revival": ai_plus_rank6_revival,
        "venue_low_ai_revival": revival_summary,
        "role_note": (
            f"びわこ専用本命。場別3着内バフ({top3_reason})が強く、"
            f"{revival_reason or '低AI艇に場別展示復活バフあり'}。"
            f"複合上位3艇{','.join(map(str, selected))}の5/6絡みBOX6点"
        ),
    }


def suminoe_b1tenji5_avg010_h2_top3_no1_has56_12(rows):
    metrics = rows[0].get("_morning_metrics") or {}
    b1_avgdiff = as_num(metrics.get("boat1_avg_isshu_diff"))
    b1_tenji_rank = valid_boat_rank(metrics.get("boat1_tenji_rank") or metrics.get("boat1_tenji_time_rank"))

    head_rows = sorted(
        [row for row in rows if row.get("boat_number") != 1],
        key=lambda row: (
            -venue_roi_win_score(row),
            row["boat_number"],
        ),
    )
    heads = [row["boat_number"] for row in head_rows[:2]]
    axis_top3_rows = sorted(
        rows,
        key=lambda row: (
            -venue_roi_top3_score(row),
            row["boat_number"],
        ),
    )
    axes = [row["boat_number"] for row in axis_top3_rows[:2]]
    if len(heads) < 2 or len(axes) < 2:
        return set(), None

    ai13_axes, _ai13_axis_rule = axis_boats_for_roles(rows, ranks=(1, 3))
    protected = set(heads + axes + ai13_axes)
    keshi_row = sorted(
        rows,
        key=lambda row: (
            bool(row.get("venue_dont_keshi") or row["boat_number"] in protected),
            venue_roi_top3_score(row),
            row["boat_number"],
        ),
    )[0]
    keshi = keshi_row["boat_number"]
    supports = [boat for boat in range(2, 7) if boat != keshi]

    tickets = []
    seen = set()
    for head in heads:
        if head == 1:
            continue
        for axis in axes:
            if axis == head:
                continue
            for other in supports:
                if len({head, axis, other}) != 3:
                    continue
                for ticket in (f"{head}{axis}{other}", f"{head}{other}{axis}"):
                    nums = set(combo_boats(ticket))
                    if 1 in nums or not (nums & {5, 6}):
                        continue
                    if ticket in seen:
                        continue
                    seen.add(ticket)
                    tickets.append(ticket)
                    if len(tickets) >= BUY_TICKET_MAX_POINTS:
                        break
                if len(tickets) >= BUY_TICKET_MAX_POINTS:
                    break
            if len(tickets) >= BUY_TICKET_MAX_POINTS:
                break
        if len(tickets) >= BUY_TICKET_MAX_POINTS:
            break
    if len(tickets) < 2:
        return set(), None

    ticket_set = set(tickets)
    head_scores = {
        str(row["boat_number"]): {
            "score": round(venue_roi_win_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in head_rows[:2]
    }
    support_scores = {
        str(row["boat_number"]): {
            "score": round(venue_roi_top3_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in axis_top3_rows[:4]
    }
    ai_plus_rank6_row = next((row for row in rows if row.get("ai_plus_rank") == 6), {})
    ai_plus_rank6_boat = ai_plus_rank6_row.get("boat_number")
    ai_plus_rank6_revival = revive_reasons(ai_plus_rank6_row) if ai_plus_rank6_row else []
    return ticket_set, {
        "heads": heads,
        "head_rule": "住之江専用。1号艇の展示が5位以下、かつ平均との差が-0.10以下の時に1号艇を全消し",
        "head_mode": "suminoe_b1tenji5_avg010_h2_top3",
        "head_scores": head_scores,
        "attackers": heads,
        "attack_scores": head_scores,
        "finishers": heads,
        "finisher_scores": head_scores,
        "support_boats": sorted(
            {
                boat
                for ticket in ticket_set
                for boat in combo_boats(ticket)
                if boat not in set(heads)
            }
        ),
        "support_scores": support_scores,
        "role_split_note": "住之江の本番検証で安定した、1号艇の展示順位と平均との差悪化を合わせる1号艇全消し型",
        "axes": axes,
        "axis_rule": "複合3着内率の上位2艇",
        "alt_axes": ai13_axes,
        "alt_axis_rule": "消し保護ではAI+1位と3位も参照",
        "supports": supports,
        "keshi": keshi,
        "keshi_reason": f"住之江ROIルール専用: 複合3着内スコアが弱い{keshi}号艇を消し",
        "ai_plus_rank6_boat": ai_plus_rank6_boat,
        "ai_plus_rank6_revival": ai_plus_rank6_revival,
        "role_note": (
            f"住之江専用本命。1号艇は展示{b1_tenji_rank or 9:.0f}位、"
            f"平均との差{b1_avgdiff or 0.0:+.2f}で弱い。"
            f"頭は複合1着率上位{heads[0]},{heads[1]}、軸は複合3着内率上位{axes[0]},{axes[1]}、"
            f"1号艇全消し+5/6絡みだけを{len(ticket_set)}点"
        ),
    }


def amagasaki_r1_8_b1avg010_outer56avg050_h2_top3_no1_has56_12(rows):
    metrics = rows[0].get("_morning_metrics") or {}
    b1_avgdiff = as_num(metrics.get("boat1_avg_isshu_diff"))
    b1_isshu_rank = valid_boat_rank(metrics.get("boat1_isshu_rank"))
    outer56_avgdiff = as_num(metrics.get("outer56_best_avg_isshu_diff"))

    head_rows = sorted(
        [row for row in rows if row.get("boat_number") != 1],
        key=lambda row: (
            -venue_roi_win_score(row),
            row["boat_number"],
        ),
    )
    heads = [row["boat_number"] for row in head_rows[:2]]
    axis_top3_rows = sorted(
        rows,
        key=lambda row: (
            -venue_roi_top3_score(row),
            row["boat_number"],
        ),
    )
    axes = [row["boat_number"] for row in axis_top3_rows[:2]]
    if len(heads) < 2 or len(axes) < 2:
        return set(), None

    ai13_axes, _ai13_axis_rule = axis_boats_for_roles(rows, ranks=(1, 3))
    protected = set(heads + axes + ai13_axes)
    keshi_row = sorted(
        rows,
        key=lambda row: (
            bool(row.get("venue_dont_keshi") or row["boat_number"] in protected),
            venue_roi_top3_score(row),
            row["boat_number"],
        ),
    )[0]
    keshi = keshi_row["boat_number"]
    supports = [boat for boat in range(2, 7) if boat != keshi]

    tickets = []
    seen = set()
    for head in heads:
        if head == 1:
            continue
        for axis in axes:
            if axis == head:
                continue
            for other in supports:
                if len({head, axis, other}) != 3:
                    continue
                for ticket in (f"{head}{axis}{other}", f"{head}{other}{axis}"):
                    nums = set(combo_boats(ticket))
                    if 1 in nums or not (nums & {5, 6}):
                        continue
                    if ticket in seen:
                        continue
                    seen.add(ticket)
                    tickets.append(ticket)
                    if len(tickets) >= BUY_TICKET_MAX_POINTS:
                        break
                if len(tickets) >= BUY_TICKET_MAX_POINTS:
                    break
            if len(tickets) >= BUY_TICKET_MAX_POINTS:
                break
        if len(tickets) >= BUY_TICKET_MAX_POINTS:
            break
    if len(tickets) < 2:
        return set(), None

    ticket_set = set(tickets)
    head_scores = {
        str(row["boat_number"]): {
            "score": round(venue_roi_win_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in head_rows[:2]
    }
    support_scores = {
        str(row["boat_number"]): {
            "score": round(venue_roi_top3_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in axis_top3_rows[:4]
    }
    ai_plus_rank6_row = next((row for row in rows if row.get("ai_plus_rank") == 6), {})
    ai_plus_rank6_boat = ai_plus_rank6_row.get("boat_number")
    ai_plus_rank6_revival = revive_reasons(ai_plus_rank6_row) if ai_plus_rank6_row else []
    avg_text = f"{b1_avgdiff:+.2f}" if b1_avgdiff is not None else "不明"
    lap_text = f"{b1_isshu_rank:.0f}位" if b1_isshu_rank is not None else "不明"
    outer_text = f"{outer56_avgdiff:+.2f}" if outer56_avgdiff is not None else "不明"
    return ticket_set, {
        "heads": heads,
        "head_rule": "尼崎専用。1〜8Rで1号艇の平均との差が-0.10以下、5/6号艇の平均との差が+0.50以上の時に1号艇を全消し",
        "head_mode": "amagasaki_r1_8_b1avg010_outer56avg050_h2_top3",
        "head_scores": head_scores,
        "attackers": heads,
        "attack_scores": head_scores,
        "finishers": heads,
        "finisher_scores": head_scores,
        "support_boats": sorted(
            {
                boat
                for ticket in ticket_set
                for boat in combo_boats(ticket)
                if boat not in set(heads)
            }
        ),
        "support_scores": support_scores,
        "role_split_note": "尼崎の長期検証で安定した、1〜8Rの1号艇足色弱化+5/6号艇足色強化を起点にした1号艇全消し型",
        "axes": axes,
        "axis_rule": "複合3着内率の上位2艇",
        "alt_axes": ai13_axes,
        "alt_axis_rule": "消し保護ではAI+1位と3位も参照",
        "supports": supports,
        "keshi": keshi,
        "keshi_reason": f"尼崎ROIルール専用: 複合3着内スコアが弱い{keshi}号艇を消し",
        "ai_plus_rank6_boat": ai_plus_rank6_boat,
        "ai_plus_rank6_revival": ai_plus_rank6_revival,
        "role_note": (
            f"尼崎専用本命。1〜8Rで1号艇平均との差{avg_text}、5/6号艇平均との差{outer_text}。"
            f"1号艇1周は{lap_text}。"
            f"頭は複合1着率上位{heads[0]},{heads[1]}、軸は複合3着内率上位{axes[0]},{axes[1]}、"
            f"1号艇全消し+5/6絡みだけを{len(ticket_set)}点"
        ),
    }


def naruto_r7_12_wave3_b1odds1_top3buff10_h2_top3_no1_has56_12(rows):
    metrics = rows[0].get("_morning_metrics") or {}
    b1_odds_pct = as_num(metrics.get("boat1_odds_prediction_pct"))
    b1_odds_rank = int(as_num(metrics.get("boat1_odds_rank")) or 9)
    b1_tenji_rank = valid_boat_rank(metrics.get("boat1_tenji_rank") or metrics.get("boat1_tenji_time_rank"))
    b1_tenji_text = f"{b1_tenji_rank:.0f}位" if b1_tenji_rank is not None else "不明"

    head_rows = sorted(
        [row for row in rows if row.get("boat_number") != 1],
        key=lambda row: (
            -venue_roi_win_score(row),
            row["boat_number"],
        ),
    )
    heads = [row["boat_number"] for row in head_rows[:2]]
    axis_top3_rows = sorted(
        rows,
        key=lambda row: (
            -venue_roi_top3_score(row),
            row["boat_number"],
        ),
    )
    axes = [row["boat_number"] for row in axis_top3_rows[:2]]
    if len(heads) < 2 or len(axes) < 2:
        return set(), None

    ai13_axes, _ai13_axis_rule = axis_boats_for_roles(rows, ranks=(1, 3))
    protected = set(heads + axes + ai13_axes)
    keshi_row = sorted(
        rows,
        key=lambda row: (
            bool(row.get("venue_dont_keshi") or row["boat_number"] in protected),
            venue_roi_top3_score(row),
            row["boat_number"],
        ),
    )[0]
    keshi = keshi_row["boat_number"]
    supports = [boat for boat in range(2, 7) if boat != keshi]

    tickets = []
    seen = set()
    for head in heads:
        if head == 1:
            continue
        for axis in axes:
            if axis == head:
                continue
            for other in supports:
                if len({head, axis, other}) != 3:
                    continue
                for ticket in (f"{head}{axis}{other}", f"{head}{other}{axis}"):
                    nums = set(combo_boats(ticket))
                    if 1 in nums or not (nums & {5, 6}):
                        continue
                    if ticket in seen:
                        continue
                    seen.add(ticket)
                    tickets.append(ticket)
                    if len(tickets) >= BUY_TICKET_MAX_POINTS:
                        break
                if len(tickets) >= BUY_TICKET_MAX_POINTS:
                    break
            if len(tickets) >= BUY_TICKET_MAX_POINTS:
                break
        if len(tickets) >= BUY_TICKET_MAX_POINTS:
            break
    if len(tickets) < 2:
        return set(), None

    ticket_set = set(tickets)
    head_scores = {
        str(row["boat_number"]): {
            "score": round(venue_roi_win_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in head_rows[:2]
    }
    support_scores = {
        str(row["boat_number"]): {
            "score": round(venue_roi_top3_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in axis_top3_rows[:4]
    }
    ai_plus_rank6_row = next((row for row in rows if row.get("ai_plus_rank") == 6), {})
    ai_plus_rank6_boat = ai_plus_rank6_row.get("boat_number")
    ai_plus_rank6_revival = revive_reasons(ai_plus_rank6_row) if ai_plus_rank6_row else []
    venue_buff_reasons = [
        f"{row.get('boat_number')}号艇: {reason}"
        for row in rows
        for reason in (row.get("venue_factor_reasons") or [])
        if "3着内差" in str(reason) or "1着差" in str(reason) or "頭" in str(reason)
    ][:2]
    return ticket_set, {
        "heads": heads,
        "head_rule": "鳴門専用。7〜12Rで波3cm以上、1号艇がオッズ評価1位、場別3着内バフ+10pt以上の時に1号艇を全消し",
        "head_mode": "naruto_r7_12_wave3_b1odds1_top3buff10_h2_top3",
        "head_scores": head_scores,
        "attackers": heads,
        "attack_scores": head_scores,
        "finishers": heads,
        "finisher_scores": head_scores,
        "support_boats": sorted(
            {
                boat
                for ticket in ticket_set
                for boat in combo_boats(ticket)
                if boat not in set(heads)
            }
        ),
        "support_scores": support_scores,
        "role_split_note": "鳴門の長期検証で安定した、後半荒れ水面+場別3着内バフ+人気1号艇を疑う1号艇全消し型",
        "axes": axes,
        "axis_rule": "複合3着内率の上位2艇",
        "alt_axes": ai13_axes,
        "alt_axis_rule": "消し保護ではAI+1位と3位も参照",
        "supports": supports,
        "keshi": keshi,
        "keshi_reason": f"鳴門ROIルール専用: 複合3着内スコアが弱い{keshi}号艇を消し",
        "ai_plus_rank6_boat": ai_plus_rank6_boat,
        "ai_plus_rank6_revival": ai_plus_rank6_revival,
        "role_note": (
            f"鳴門専用本命。7〜12R・波3cm以上で1号艇展示{b1_tenji_text}、オッズ評価{b1_odds_rank}位{b1_odds_pct or 0.0:.1f}%、"
            f"場別3着内バフあり（{' / '.join(venue_buff_reasons) if venue_buff_reasons else '詳細は場別辞書参照'}）。"
            f"頭は複合1着率上位{heads[0]},{heads[1]}、軸は複合3着内率上位{axes[0]},{axes[1]}、"
            f"1号艇全消し+5/6絡みだけを{len(ticket_set)}点"
        ),
    }


def marugame_r4_8_b1loss45_b5top3rank1_h1_56_ai13_no1_has56_8(rows):
    metrics = rows[0].get("_morning_metrics") or {}
    b1_loss_pct = as_num(metrics.get("boat1_loss_pct"))
    b5_row = next((row for row in rows if row.get("boat_number") == 5), {})
    head_rows = sorted(
        [row for row in rows if row.get("boat_number") in {5, 6}],
        key=lambda row: (
            -venue_roi_win_score(row),
            row["boat_number"],
        ),
    )
    if not head_rows:
        return set(), None
    heads = [head_rows[0]["boat_number"]]
    head = heads[0]
    axes, axis_rule = axis_boats_for_roles(rows, ranks=(1, 3))
    axis_top3_rows = sorted(
        rows,
        key=lambda row: (
            -venue_roi_top3_score(row),
            row["boat_number"],
        ),
    )
    supports = [row["boat_number"] for row in axis_top3_rows if row.get("boat_number") not in {1, head}]
    if len(axes) < 2 or len(supports) < 2:
        return set(), None

    axis_candidates = []
    for boat in axes + supports[:2]:
        if boat not in axis_candidates:
            axis_candidates.append(boat)

    tickets = []
    seen = set()
    for axis in axis_candidates:
        if axis == head:
            continue
        for other in supports:
            if len({head, axis, other}) != 3:
                continue
            for ticket in (f"{head}{axis}{other}", f"{head}{other}{axis}"):
                nums = set(combo_boats(ticket))
                if 1 in nums or not (nums & {5, 6}):
                    continue
                if ticket in seen:
                    continue
                seen.add(ticket)
                tickets.append(ticket)
                if len(tickets) >= 8:
                    break
            if len(tickets) >= 8:
                break
        if len(tickets) >= 8:
            break
    if len(tickets) < 2:
        return set(), None

    ticket_set = set(tickets)
    head_scores = {
        str(row["boat_number"]): {
            "score": round(venue_roi_win_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in head_rows[:1]
    }
    support_scores = {
        str(row["boat_number"]): {
            "score": round(venue_roi_top3_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in axis_top3_rows[:4]
    }
    ai_plus_rank6_row = next((row for row in rows if row.get("ai_plus_rank") == 6), {})
    ai_plus_rank6_boat = ai_plus_rank6_row.get("boat_number")
    ai_plus_rank6_revival = revive_reasons(ai_plus_rank6_row) if ai_plus_rank6_row else []
    b5_top3_rank = next(
        (index for index, row in enumerate(axis_top3_rows, 1) if row.get("boat_number") == 5),
        None,
    )
    b5_top3_score = venue_roi_top3_score(b5_row) if b5_row else None
    support_boats = sorted({boat for ticket in ticket_set for boat in combo_boats(ticket) if boat != head})
    return ticket_set, {
        "heads": heads,
        "head_rule": "丸亀専用。4〜8Rで1号艇逃げ失敗率45%以上、5号艇が複合3着内スコア1位の時に1号艇を全消し",
        "head_mode": "marugame_r4_8_b1loss45_b5top3rank1_h1_56_ai13",
        "head_scores": head_scores,
        "attackers": heads,
        "attack_scores": head_scores,
        "finishers": heads,
        "finisher_scores": head_scores,
        "support_boats": support_boats,
        "support_scores": support_scores,
        "role_split_note": "丸亀の長期検証で強かった、中盤レースの人気1号艇不安と5号艇複合3着内評価を組み合わせる5/6頭1艇型",
        "axes": axis_candidates,
        "axis_rule": axis_rule,
        "alt_axes": [row["boat_number"] for row in axis_top3_rows[:2]],
        "alt_axis_rule": "AI+1位/3位に加え、複合3着内スコア上位で補完",
        "supports": supports,
        "keshi": 1,
        "keshi_reason": "丸亀ROIルール専用: 1号艇は全消し",
        "ai_plus_rank6_boat": ai_plus_rank6_boat,
        "ai_plus_rank6_revival": ai_plus_rank6_revival,
        "role_note": (
            f"丸亀専用本命。4〜8Rで1号艇逃げ失敗{b1_loss_pct or 0.0:.1f}%、"
            f"5号艇が複合3着内スコア{b5_top3_score or 0.0:.1f}の{b5_top3_rank or 9}位。"
            f"頭は5/6号艇の複合1着率上位{head}、相手はAI+1位/3位と複合3着内上位、"
            f"1号艇全消し+5/6絡みだけを{len(ticket_set)}点"
        ),
    }


def kojima_b1odds1_b1avg005_b1tenji4_outer56avg040_h1_ai13_no1_has56_8(rows):
    metrics = rows[0].get("_morning_metrics") or {}
    b1_avgdiff = as_num(metrics.get("boat1_avg_isshu_diff"))
    b1_odds_rank = int(as_num(metrics.get("boat1_odds_rank")) or 9)
    b1_tenji_rank = valid_boat_rank(metrics.get("boat1_tenji_rank") or metrics.get("boat1_tenji_time_rank"))
    outer56_avgdiff = as_num(metrics.get("outer56_best_avg_isshu_diff"))

    head_rows = sorted(
        [row for row in rows if row.get("boat_number") != 1],
        key=lambda row: (
            -venue_roi_win_score(row),
            row["boat_number"],
        ),
    )
    if not head_rows:
        return set(), None
    head = head_rows[0]["boat_number"]
    heads = [head]
    heads_for_protection = [row["boat_number"] for row in head_rows[:2]]
    ai13_axes, axis_rule = axis_boats_for_roles(rows, ranks=(1, 3))
    axis_top3_rows = sorted(
        rows,
        key=lambda row: (
            -venue_roi_top3_score(row),
            row["boat_number"],
        ),
    )
    supports = [
        row["boat_number"]
        for row in axis_top3_rows
        if row.get("boat_number") not in {1, head}
    ]
    if len(ai13_axes) < 2 or len(supports) < 2:
        return set(), None

    axes = []
    for boat in ai13_axes + supports[:2]:
        if boat not in axes:
            axes.append(boat)

    tickets = []
    seen = set()
    for axis in axes:
        if axis == head:
            continue
        for other in supports:
            if len({head, axis, other}) != 3:
                continue
            for ticket in (f"{head}{axis}{other}", f"{head}{other}{axis}"):
                nums = set(combo_boats(ticket))
                if 1 in nums or not (nums & {5, 6}):
                    continue
                if ticket in seen:
                    continue
                seen.add(ticket)
                tickets.append(ticket)
                if len(tickets) >= 8:
                    break
            if len(tickets) >= 8:
                break
        if len(tickets) >= 8:
            break
    if len(tickets) < 2:
        return set(), None

    ticket_set = set(tickets)
    head_scores = {
        str(row["boat_number"]): {
            "score": round(venue_roi_win_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in head_rows[:1]
    }
    support_scores = {
        str(row["boat_number"]): {
            "score": round(venue_roi_top3_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in axis_top3_rows[:4]
    }
    ai_plus_rank6_row = next((row for row in rows if row.get("ai_plus_rank") == 6), {})
    ai_plus_rank6_boat = ai_plus_rank6_row.get("boat_number")
    ai_plus_rank6_revival = revive_reasons(ai_plus_rank6_row) if ai_plus_rank6_row else []
    avg_text = f"{b1_avgdiff:+.2f}" if b1_avgdiff is not None else "不明"
    tenji_text = f"{b1_tenji_rank:.0f}位" if b1_tenji_rank is not None else "不明"
    outer_text = f"{outer56_avgdiff:+.2f}" if outer56_avgdiff is not None else "不明"
    ticket_supports = sorted(
        {
            boat
            for ticket in ticket_set
            for boat in combo_boats(ticket)
            if boat != head
        }
    )
    return ticket_set, {
        "heads": heads,
        "head_rule": "児島専用。1号艇がオッズ評価1位なのに平均との差-0.05以下、展示4位以下、5/6号艇平均との差+0.40以上の時に1号艇を全消し",
        "head_mode": "kojima_b1odds1_b1avg005_b1tenji4_outer56avg040_h1_ai13",
        "head_scores": head_scores,
        "attackers": heads,
        "attack_scores": head_scores,
        "finishers": heads,
        "finisher_scores": head_scores,
        "support_boats": ticket_supports,
        "support_scores": support_scores,
        "role_split_note": "児島の長期検証で安定寄りだった、人気1号艇の展示弱化と5/6号艇の展示+一周平均差を組み合わせる外頭1艇8点型",
        "axes": axes,
        "axis_rule": axis_rule,
        "alt_axes": supports[:2],
        "alt_axis_rule": "AI+1位/3位に加え、複合3着内スコア上位で補完",
        "supports": supports,
        "keshi": 1,
        "keshi_reason": "児島ROIルール専用: 1号艇は全消し",
        "ai_plus_rank6_boat": ai_plus_rank6_boat,
        "ai_plus_rank6_revival": ai_plus_rank6_revival,
        "role_note": (
            f"児島専用本命。1号艇はオッズ評価{b1_odds_rank}位だが、平均との差{avg_text}、"
            f"展示{tenji_text}で弱く、5/6号艇平均との差は{outer_text}。頭は複合1着率最上位{head}に固定、"
            f"相手はAI+1位/3位と複合3着内スコア上位、1号艇全消し+5/6絡みだけを{len(ticket_set)}点"
        ),
    }


def miyajima_r1_3_b1odds3_b1lap5_b1tenji5_h2_ai13_no1_has56_12(rows):
    metrics = rows[0].get("_morning_metrics") or {}
    b1_odds_pct = as_num(metrics.get("boat1_odds_prediction_pct"))
    b1_odds_rank = int(as_num(metrics.get("boat1_odds_rank")) or 9)
    b1_isshu_rank = valid_boat_rank(metrics.get("boat1_isshu_rank"))
    b1_tenji_rank = valid_boat_rank(metrics.get("boat1_tenji_rank") or metrics.get("boat1_tenji_time_rank"))

    head_rows = sorted(
        [row for row in rows if row.get("boat_number") != 1],
        key=lambda row: (
            -venue_roi_win_score(row),
            row["boat_number"],
        ),
    )
    heads = [row["boat_number"] for row in head_rows[:2]]
    axes, axis_rule = axis_boats_for_roles(rows, ranks=(1, 3))
    axis_top3_rows = sorted(
        rows,
        key=lambda row: (
            -venue_roi_top3_score(row),
            row["boat_number"],
        ),
    )
    top3_axes = [row["boat_number"] for row in axis_top3_rows[:2]]
    if len(heads) < 2 or len(axes) < 2:
        return set(), None

    protected = set(heads + axes + top3_axes)
    keshi_row = sorted(
        rows,
        key=lambda row: (
            bool(row.get("venue_dont_keshi") or row["boat_number"] in protected),
            venue_roi_top3_score(row),
            row["boat_number"],
        ),
    )[0]
    keshi = keshi_row["boat_number"]
    supports = [boat for boat in range(2, 7) if boat != keshi]

    tickets = []
    seen = set()
    for head in heads:
        if head == 1:
            continue
        for axis in axes:
            if axis == head:
                continue
            for other in supports:
                if len({head, axis, other}) != 3:
                    continue
                for ticket in (f"{head}{axis}{other}", f"{head}{other}{axis}"):
                    nums = set(combo_boats(ticket))
                    if 1 in nums or not (nums & {5, 6}):
                        continue
                    if ticket in seen:
                        continue
                    seen.add(ticket)
                    tickets.append(ticket)
                    if len(tickets) >= BUY_TICKET_MAX_POINTS:
                        break
                if len(tickets) >= BUY_TICKET_MAX_POINTS:
                    break
            if len(tickets) >= BUY_TICKET_MAX_POINTS:
                break
        if len(tickets) >= BUY_TICKET_MAX_POINTS:
            break
    if len(tickets) < 2:
        return set(), None

    ticket_set = set(tickets)
    head_scores = {
        str(row["boat_number"]): {
            "score": round(venue_roi_win_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in head_rows[:2]
    }
    support_scores = {
        str(row["boat_number"]): {
            "score": round(venue_roi_top3_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in axis_top3_rows[:4]
    }
    ai_plus_rank6_row = next((row for row in rows if row.get("ai_plus_rank") == 6), {})
    ai_plus_rank6_boat = ai_plus_rank6_row.get("boat_number")
    ai_plus_rank6_revival = revive_reasons(ai_plus_rank6_row) if ai_plus_rank6_row else []
    lap_text = f"{b1_isshu_rank:.0f}位" if b1_isshu_rank is not None else "不明"
    tenji_text = f"{b1_tenji_rank:.0f}位" if b1_tenji_rank is not None else "不明"
    return ticket_set, {
        "heads": heads,
        "head_rule": "宮島専用。1〜3Rで1号艇がオッズ評価3位以内、展示5位以下、1周5位以下の時に1号艇を全消し",
        "head_mode": "miyajima_r1_3_b1odds3_b1lap5_b1tenji5_h2_ai13",
        "head_scores": head_scores,
        "attackers": heads,
        "attack_scores": head_scores,
        "finishers": heads,
        "finisher_scores": head_scores,
        "support_boats": sorted(
            {
                boat
                for ticket in ticket_set
                for boat in combo_boats(ticket)
                if boat not in set(heads)
            }
        ),
        "support_scores": support_scores,
        "role_split_note": "宮島の長期検証で安定寄りだった、前半で1号艇がオッズ圏内なのに展示と1周がともに弱い時にAI+軸で疑う1号艇全消し型",
        "axes": axes,
        "axis_rule": axis_rule,
        "alt_axes": top3_axes,
        "alt_axis_rule": "消し保護では複合3着内率の上位2艇も参照",
        "supports": supports,
        "keshi": keshi,
        "keshi_reason": f"宮島ROIルール専用: 複合3着内スコアが弱い{keshi}号艇を消し",
        "ai_plus_rank6_boat": ai_plus_rank6_boat,
        "ai_plus_rank6_revival": ai_plus_rank6_revival,
        "role_note": (
            f"宮島専用本命。前半1〜3Rで1号艇オッズ評価{b1_odds_rank}位{b1_odds_pct or 0.0:.1f}%、"
            f"展示{tenji_text}・1周{lap_text}で弱い。頭は複合1着率上位{heads[0]},{heads[1]}、"
            f"軸は{axis_rule}、1号艇全消し+5/6絡みだけを{len(ticket_set)}点"
        ),
    }


def tokuyama_r4_8_b1odds2_pct30_b1lap4_h1_ai13_no1_8(rows):
    metrics = rows[0].get("_morning_metrics") or {}
    b1_odds_pct = as_num(metrics.get("boat1_odds_prediction_pct"))
    b1_odds_rank = int(as_num(metrics.get("boat1_odds_rank")) or 9)
    b1_avgdiff = as_num(metrics.get("boat1_avg_isshu_diff"))
    b1_isshu_rank = valid_boat_rank(metrics.get("boat1_isshu_rank"))

    head_rows = sorted(
        [row for row in rows if row.get("boat_number") != 1],
        key=lambda row: (
            -venue_roi_win_score(row),
            row["boat_number"],
        ),
    )
    heads_for_protection = [row["boat_number"] for row in head_rows[:2]]
    heads = heads_for_protection[:1]
    axes, axis_rule = axis_boats_for_roles(rows, ranks=(1, 3))
    axis_top3_rows = sorted(
        rows,
        key=lambda row: (
            -venue_roi_top3_score(row),
            row["boat_number"],
        ),
    )
    top3_axes = [row["boat_number"] for row in axis_top3_rows[:2]]
    if len(heads) < 1 or len(axes) < 2:
        return set(), None

    protected = set(heads_for_protection + axes + top3_axes)
    keshi_row = sorted(
        rows,
        key=lambda row: (
            bool(row.get("venue_dont_keshi") or row["boat_number"] in protected),
            venue_roi_top3_score(row),
            row["boat_number"],
        ),
    )[0]
    keshi = keshi_row["boat_number"]
    supports = [boat for boat in range(2, 7) if boat != keshi]

    tickets = []
    seen = set()
    for head in heads:
        if head == 1:
            continue
        for axis in axes:
            if axis == head:
                continue
            for other in supports:
                if len({head, axis, other}) != 3:
                    continue
                for ticket in (f"{head}{axis}{other}", f"{head}{other}{axis}"):
                    nums = set(combo_boats(ticket))
                    if 1 in nums:
                        continue
                    if ticket in seen:
                        continue
                    seen.add(ticket)
                    tickets.append(ticket)
                    if len(tickets) >= 8:
                        break
                if len(tickets) >= 8:
                    break
            if len(tickets) >= 8:
                break
        if len(tickets) >= 8:
            break
    if len(tickets) < 2:
        return set(), None

    ticket_set = set(tickets)
    head_scores = {
        str(row["boat_number"]): {
            "score": round(venue_roi_win_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in head_rows[:1]
    }
    support_scores = {
        str(row["boat_number"]): {
            "score": round(venue_roi_top3_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in axis_top3_rows[:4]
    }
    ai_plus_rank6_row = next((row for row in rows if row.get("ai_plus_rank") == 6), {})
    ai_plus_rank6_boat = ai_plus_rank6_row.get("boat_number")
    ai_plus_rank6_revival = revive_reasons(ai_plus_rank6_row) if ai_plus_rank6_row else []
    avg_text = f"{b1_avgdiff:+.2f}" if b1_avgdiff is not None else "不明"
    lap_text = f"{b1_isshu_rank:.0f}位" if b1_isshu_rank is not None else "不明"
    head = heads[0]
    return ticket_set, {
        "heads": heads,
        "head_rule": "徳山専用。4〜8Rで1号艇がオッズ評価2位以内かつ30%以上、1周4位以下の時に1号艇を全消し",
        "head_mode": "tokuyama_r4_8_b1odds2_pct30_b1lap4_h1_ai13",
        "head_scores": head_scores,
        "attackers": heads,
        "attack_scores": head_scores,
        "finishers": heads,
        "finisher_scores": head_scores,
        "support_boats": sorted(
            {
                boat
                for ticket in ticket_set
                for boat in combo_boats(ticket)
                if boat not in set(heads)
            }
        ),
        "support_scores": support_scores,
        "role_split_note": "徳山の長期検証で安定寄りだった、中盤で1号艇がオッズ圏内なのに1周が弱い時に頭1艇で絞る1号艇全消し型",
        "axes": axes,
        "axis_rule": axis_rule,
        "alt_axes": top3_axes,
        "alt_axis_rule": "消し保護では複合3着内率の上位2艇も参照",
        "supports": supports,
        "keshi": keshi,
        "keshi_reason": f"徳山ROIルール専用: 複合3着内スコアが弱い{keshi}号艇を消し",
        "ai_plus_rank6_boat": ai_plus_rank6_boat,
        "ai_plus_rank6_revival": ai_plus_rank6_revival,
        "role_note": (
            f"徳山専用本命。4〜8Rで1号艇オッズ評価{b1_odds_rank}位{b1_odds_pct or 0.0:.1f}%、"
            f"1周{lap_text}で弱い。平均との差は{avg_text}。頭は複合1着率最上位{head}号艇だけ、"
            f"軸は{axis_rule}、1号艇全消しで{len(ticket_set)}点"
        ),
    }


def shimonoseki_r1_6_outer56avg010_b1odds50_h2_top3_no1_has56_12(rows):
    metrics = rows[0].get("_morning_metrics") or {}
    b1_odds_pct = as_num(metrics.get("boat1_odds_prediction_pct"))
    b1_odds_rank = int(as_num(metrics.get("boat1_odds_rank")) or 9)
    outer56_avgdiff = as_num(metrics.get("outer56_best_avg_isshu_diff"))

    head_rows = sorted(
        [row for row in rows if row.get("boat_number") != 1],
        key=lambda row: (
            -venue_roi_win_score(row),
            row["boat_number"],
        ),
    )
    heads = [row["boat_number"] for row in head_rows[:2]]
    axis_top3_rows = sorted(
        rows,
        key=lambda row: (
            -venue_roi_top3_score(row),
            row["boat_number"],
        ),
    )
    axes = [row["boat_number"] for row in axis_top3_rows[:2]]
    if len(heads) < 2 or len(axes) < 2:
        return set(), None

    ai13_axes, _ai13_axis_rule = axis_boats_for_roles(rows, ranks=(1, 3))
    protected = set(heads + axes + ai13_axes)
    keshi_row = sorted(
        rows,
        key=lambda row: (
            bool(row.get("venue_dont_keshi") or row["boat_number"] in protected),
            venue_roi_top3_score(row),
            row["boat_number"],
        ),
    )[0]
    keshi = keshi_row["boat_number"]
    supports = [boat for boat in range(2, 7) if boat != keshi]

    tickets = []
    seen = set()
    for head in heads:
        if head == 1:
            continue
        for axis in axes:
            if axis == head:
                continue
            for other in supports:
                if len({head, axis, other}) != 3:
                    continue
                for ticket in (f"{head}{axis}{other}", f"{head}{other}{axis}"):
                    nums = set(combo_boats(ticket))
                    if 1 in nums or not (nums & {5, 6}):
                        continue
                    if ticket in seen:
                        continue
                    seen.add(ticket)
                    tickets.append(ticket)
                    if len(tickets) >= BUY_TICKET_MAX_POINTS:
                        break
                if len(tickets) >= BUY_TICKET_MAX_POINTS:
                    break
            if len(tickets) >= BUY_TICKET_MAX_POINTS:
                break
        if len(tickets) >= BUY_TICKET_MAX_POINTS:
            break
    if len(tickets) < 2:
        return set(), None

    ticket_set = set(tickets)
    head_scores = {
        str(row["boat_number"]): {
            "score": round(venue_roi_win_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in head_rows[:2]
    }
    support_scores = {
        str(row["boat_number"]): {
            "score": round(venue_roi_top3_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in axis_top3_rows[:4]
    }
    ai_plus_rank6_row = next((row for row in rows if row.get("ai_plus_rank") == 6), {})
    ai_plus_rank6_boat = ai_plus_rank6_row.get("boat_number")
    ai_plus_rank6_revival = revive_reasons(ai_plus_rank6_row) if ai_plus_rank6_row else []
    outer_text = f"{outer56_avgdiff:+.2f}" if outer56_avgdiff is not None else "不明"
    return ticket_set, {
        "heads": heads,
        "head_rule": "下関専用。1〜6Rで5/6号艇の平均との差+0.10以上、1号艇がオッズ評価50%以上の1位なら1号艇を全消し",
        "head_mode": "shimonoseki_r1_6_outer56avg010_b1odds50_h2_top3",
        "head_scores": head_scores,
        "attackers": heads,
        "attack_scores": head_scores,
        "finishers": heads,
        "finisher_scores": head_scores,
        "support_boats": sorted(
            {
                boat
                for ticket in ticket_set
                for boat in combo_boats(ticket)
                if boat not in set(heads)
            }
        ),
        "support_scores": support_scores,
        "role_split_note": "下関の長期検証で強かった、前半の強人気1号艇を外5/6の展示足で疑う1号艇全消し型",
        "axes": axes,
        "axis_rule": "複合3着内率の上位2艇",
        "alt_axes": ai13_axes,
        "alt_axis_rule": "消し保護ではAI+1位と3位も参照",
        "supports": supports,
        "keshi": keshi,
        "keshi_reason": f"下関ROIルール専用: 複合3着内スコアが弱い{keshi}号艇を消し",
        "ai_plus_rank6_boat": ai_plus_rank6_boat,
        "ai_plus_rank6_revival": ai_plus_rank6_revival,
        "role_note": (
            f"下関専用本命。前半1〜6Rで1号艇オッズ評価{b1_odds_rank}位{b1_odds_pct or 0.0:.1f}%、"
            f"5/6号艇の平均との差最大{outer_text}。"
            f"頭は複合1着率上位{heads[0]},{heads[1]}、軸は複合3着内率上位{axes[0]},{axes[1]}、"
            f"1号艇全消し+5/6絡みだけを{len(ticket_set)}点"
        ),
    }


def wakamatsu_r4_8_head56_b1odds45_h2_ai13_no1_has56_12(rows):
    metrics = rows[0].get("_morning_metrics") or {}
    b1_odds_pct = as_num(metrics.get("boat1_odds_prediction_pct"))
    b1_odds_rank = int(as_num(metrics.get("boat1_odds_rank")) or 9)

    head_rows = sorted(
        [row for row in rows if row.get("boat_number") != 1],
        key=lambda row: (
            -venue_roi_win_score(row),
            row["boat_number"],
        ),
    )
    heads = [row["boat_number"] for row in head_rows[:2]]
    axes, axis_rule = axis_boats_for_roles(rows, ranks=(1, 3))
    axis_top3_rows = sorted(
        rows,
        key=lambda row: (
            -venue_roi_top3_score(row),
            row["boat_number"],
        ),
    )
    top3_axes = [row["boat_number"] for row in axis_top3_rows[:2]]
    if len(heads) < 2 or len(axes) < 2:
        return set(), None

    protected = set(heads + axes + top3_axes)
    keshi_row = sorted(
        rows,
        key=lambda row: (
            bool(row.get("venue_dont_keshi") or row["boat_number"] in protected),
            venue_roi_top3_score(row),
            row["boat_number"],
        ),
    )[0]
    keshi = keshi_row["boat_number"]
    supports = [boat for boat in range(2, 7) if boat != keshi]

    tickets = []
    seen = set()
    for head in heads:
        if head == 1:
            continue
        for axis in axes:
            if axis == head:
                continue
            for other in supports:
                if len({head, axis, other}) != 3:
                    continue
                for ticket in (f"{head}{axis}{other}", f"{head}{other}{axis}"):
                    nums = set(combo_boats(ticket))
                    if 1 in nums or not (nums & {5, 6}):
                        continue
                    if ticket in seen:
                        continue
                    seen.add(ticket)
                    tickets.append(ticket)
                    if len(tickets) >= BUY_TICKET_MAX_POINTS:
                        break
                if len(tickets) >= BUY_TICKET_MAX_POINTS:
                    break
            if len(tickets) >= BUY_TICKET_MAX_POINTS:
                break
        if len(tickets) >= BUY_TICKET_MAX_POINTS:
            break
    if len(tickets) < 2:
        return set(), None

    ticket_set = set(tickets)
    head_scores = {
        str(row["boat_number"]): {
            "score": round(venue_roi_win_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in head_rows[:2]
    }
    support_scores = {
        str(row["boat_number"]): {
            "score": round(venue_roi_top3_score(row), 3),
            "reasons": row.get("composite_rate_reasons") or [],
        }
        for row in axis_top3_rows[:4]
    }
    ai_plus_rank6_row = next((row for row in rows if row.get("ai_plus_rank") == 6), {})
    ai_plus_rank6_boat = ai_plus_rank6_row.get("boat_number")
    ai_plus_rank6_revival = revive_reasons(ai_plus_rank6_row) if ai_plus_rank6_row else []
    return ticket_set, {
        "heads": heads,
        "head_rule": "若松専用。4〜8Rで1号艇がオッズ評価45%以上の1位、かつ頭候補最上位が5/6号艇なら1号艇を全消し",
        "head_mode": "wakamatsu_r4_8_head56_b1odds45_h2_ai13",
        "head_scores": head_scores,
        "attackers": heads,
        "attack_scores": head_scores,
        "finishers": heads,
        "finisher_scores": head_scores,
        "support_boats": sorted(
            {
                boat
                for ticket in ticket_set
                for boat in combo_boats(ticket)
                if boat not in set(heads)
            }
        ),
        "support_scores": support_scores,
        "role_split_note": "若松の長期検証で強かった、中盤で5/6号艇が頭候補最上位に出た時の1号艇全消し型",
        "axes": axes,
        "axis_rule": axis_rule,
        "alt_axes": top3_axes,
        "alt_axis_rule": "消し保護では複合3着内率の上位2艇も参照",
        "supports": supports,
        "keshi": keshi,
        "keshi_reason": f"若松ROIルール専用: 複合3着内スコアが弱い{keshi}号艇を消し",
        "ai_plus_rank6_boat": ai_plus_rank6_boat,
        "ai_plus_rank6_revival": ai_plus_rank6_revival,
        "role_note": (
            f"若松専用本命。4〜8Rで1号艇オッズ評価{b1_odds_rank}位{b1_odds_pct or 0.0:.1f}%、"
            f"頭候補最上位が{heads[0]}号艇。頭は複合1着率上位{heads[0]},{heads[1]}、"
            f"軸は{axis_rule}、1号艇全消し+5/6絡みだけを{len(ticket_set)}点"
        ),
    }


def odds_gap_b1_fade_strong12(rows):
    metrics = rows[0].get("_morning_metrics") or {}
    if not b1_odds_gap_strong(metrics):
        return set(), None
    popularity_level = b1_popularity_context(metrics).get("level") or "人気あり"
    role_split = role_split_details(rows, exclude={1})
    heads = odds_gap_head_candidates(rows, exclude={1}, count=2)
    axes, axis_rule = axis_boats_for_roles(rows, ranks=(1, 3))
    if len(heads) < 2 or len(axes) < 2:
        return set(), None
    keshi, keshi_reason, ai_plus_rank6_boat, ai_plus_rank6_revival = select_keshi_boat(
        rows, protected=set(heads + axes)
    )
    if keshi is None:
        return set(), None
    pool = [boat for boat in range(1, 7) if boat != keshi]
    tickets = set()
    for head in heads:
        if head in {1, keshi}:
            continue
        for axis in axes:
            if axis in {head, keshi}:
                continue
            for other in pool:
                if other in {head, axis}:
                    continue
                tickets.add(f"{head}{axis}{other}")
                tickets.add(f"{head}{other}{axis}")
    if not tickets:
        return set(), None
    tickets = trim_tickets_balanced_heads(tickets, heads, axes, max_points=BUY_TICKET_MAX_POINTS, rows=rows)
    if not (BUY_TICKET_MIN_POINTS <= len(tickets) <= BUY_TICKET_MAX_POINTS):
        return set(), None
    b1_tenji_rank = valid_boat_rank(metrics.get("boat1_tenji_time_rank") or metrics.get("boat1_tenji_rank"))
    b1_isshu_rank = valid_boat_rank(metrics.get("boat1_isshu_rank"))
    return tickets, {
        "heads": heads,
        "head_rule": f"1号艇が{popularity_level}で危険。頭は2〜6号艇の複合1着率を軸に、展示・1周・AI頭材料で補正した上位2艇",
        "head_mode": "odds_gap_b1_fade_strong",
        "head_scores": odds_gap_head_score_details(rows, heads),
        "attackers": role_split.get("attackers") or [],
        "attack_scores": role_split.get("attack_scores") or {},
        "finishers": role_split.get("finishers") or [],
        "finisher_scores": role_split.get("finisher_scores") or {},
        "support_boats": role_split.get("support_boats") or [],
        "support_scores": role_split.get("support_scores") or {},
        "role_split_note": role_split.get("role_split_note"),
        "axes": axes,
        "axis_rule": axis_rule,
        "alt_axes": [],
        "alt_axis_rule": "歪み本命専用: 軸はAI3連対率+一般3連対率の1位と3位",
        "supports": pool,
        "keshi": keshi,
        "keshi_reason": keshi_reason,
        "ai_plus_rank6_boat": ai_plus_rank6_boat,
        "ai_plus_rank6_revival": ai_plus_rank6_revival,
        "role_note": (
            f"歪み強本命。1号艇は{popularity_level}+危険+展示{b1_tenji_rank:.0f}位/1周{b1_isshu_rank:.0f}位。"
            f"攻め艇{','.join(map(str, role_split.get('attackers') or []))}とは分け、"
            f"頭は複合1着率+展示/AI頭補正の{heads[0]},{heads[1]}。"
            f"軸は{axis_rule}の{axes[0]},{axes[1]} / 1号艇頭は買わない{len(tickets)}点"
        ),
    }


def odds_gap_b1_fade_filtered12(rows):
    metrics = rows[0].get("_morning_metrics") or {}
    if not b1_odds_gap_filtered(metrics):
        return set(), None
    popularity_level = b1_popularity_context(metrics).get("level") or "人気あり"
    role_split = role_split_details(rows, exclude={1})
    heads = odds_gap_head_candidates(rows, exclude={1}, count=2)
    axes, axis_rule = axis_boats_for_roles(rows, ranks=(1, 3))
    if len(heads) < 2 or len(axes) < 2:
        return set(), None
    keshi, keshi_reason, ai_plus_rank6_boat, ai_plus_rank6_revival = select_keshi_boat(
        rows, protected=set(heads + axes)
    )
    if keshi is None:
        return set(), None
    pool = [boat for boat in range(1, 7) if boat != keshi]
    tickets = set()
    for head in heads:
        if head in {1, keshi}:
            continue
        for axis in axes:
            if axis in {head, keshi}:
                continue
            for other in pool:
                if other in {head, axis}:
                    continue
                tickets.add(f"{head}{axis}{other}")
                tickets.add(f"{head}{other}{axis}")
    if not tickets:
        return set(), None
    tickets = trim_tickets_balanced_heads(tickets, heads, axes, max_points=BUY_TICKET_MAX_POINTS, rows=rows)
    if not (BUY_TICKET_MIN_POINTS <= len(tickets) <= BUY_TICKET_MAX_POINTS):
        return set(), None
    b1_tenji_rank = valid_boat_rank(metrics.get("boat1_tenji_time_rank") or metrics.get("boat1_tenji_rank"))
    b1_isshu_rank = valid_boat_rank(metrics.get("boat1_isshu_rank"))
    b1_avg_diff = as_num(metrics.get("boat1_avg_isshu_diff"))
    rank_bits = []
    if b1_tenji_rank is not None:
        rank_bits.append(f"展示{b1_tenji_rank:.0f}位")
    if b1_isshu_rank is not None:
        rank_bits.append(f"1周{b1_isshu_rank:.0f}位")
    if b1_avg_diff is not None:
        rank_bits.append(f"平均との差{b1_avg_diff:+.2f}")
    return tickets, {
        "heads": heads,
        "head_rule": f"1号艇が{popularity_level}で危険。頭は2〜6号艇の複合1着率を軸に、展示・1周・AI頭材料で補正した上位2艇",
        "head_mode": "odds_gap_b1_fade_filtered",
        "head_scores": odds_gap_head_score_details(rows, heads),
        "attackers": role_split.get("attackers") or [],
        "attack_scores": role_split.get("attack_scores") or {},
        "finishers": role_split.get("finishers") or [],
        "finisher_scores": role_split.get("finisher_scores") or {},
        "support_boats": role_split.get("support_boats") or [],
        "support_scores": role_split.get("support_scores") or {},
        "role_split_note": role_split.get("role_split_note"),
        "axes": axes,
        "axis_rule": axis_rule,
        "alt_axes": [],
        "alt_axis_rule": "歪み本命専用: 軸はAI3連対率+一般3連対率の1位と3位",
        "supports": pool,
        "keshi": keshi,
        "keshi_reason": keshi_reason,
        "ai_plus_rank6_boat": ai_plus_rank6_boat,
        "ai_plus_rank6_revival": ai_plus_rank6_revival,
        "role_note": (
            f"歪み本命。1号艇は{popularity_level}+危険+前半1〜6Rで"
            f"{'・'.join(rank_bits)}。"
            f"攻め艇{','.join(map(str, role_split.get('attackers') or []))}とは分け、"
            f"頭は複合1着率+展示/AI頭補正の{heads[0]},{heads[1]}。"
            f"軸は{axis_rule}の{axes[0]},{axes[1]} / 1号艇頭は買わない{len(tickets)}点"
        ),
    }


def odds_gap_b1_overbet_front_head1_8(rows):
    metrics = rows[0].get("_morning_metrics") or {}
    popularity_level = b1_popularity_context(metrics).get("level") or "人気あり"
    if popularity_level != "売れすぎ" or not b1_data_danger(metrics):
        return set(), None

    role_split = role_split_details(rows, exclude={1})
    heads = odds_gap_head_candidates(rows, exclude={1}, count=1)
    axes, axis_rule = axis_boats_for_roles(rows, ranks=(1, 3))
    if len(heads) < 1 or len(axes) < 2:
        return set(), None

    head = heads[0]
    keshi, keshi_reason, ai_plus_rank6_boat, ai_plus_rank6_revival = select_keshi_boat(
        rows, protected=set(heads + axes)
    )
    if keshi is None:
        return set(), None

    pool = [boat for boat in range(1, 7) if boat != keshi]
    tickets = set()
    for axis in axes:
        if axis in {head, keshi}:
            continue
        for other in pool:
            if other in {head, axis}:
                continue
            tickets.add(f"{head}{axis}{other}")
            tickets.add(f"{head}{other}{axis}")
    if not tickets:
        return set(), None

    tickets = trim_tickets(tickets, heads, axes, max_points=BUY_TICKET_MAX_POINTS, rows=rows)
    if len(tickets) < BUY_TICKET_MIN_POINTS:
        return set(), None

    b1_tenji_rank = valid_boat_rank(metrics.get("boat1_tenji_time_rank") or metrics.get("boat1_tenji_rank"))
    b1_isshu_rank = valid_boat_rank(metrics.get("boat1_isshu_rank"))
    return tickets, {
        "heads": heads,
        "head_rule": (
            f"1号艇が{popularity_level}で危険。前半は頭を広げすぎず、"
            "2〜6号艇の複合1着率・展示・1周・AI頭材料で最上位の1艇だけを頭にする"
        ),
        "head_mode": "odds_gap_b1_overbet_front_head1",
        "head_scores": odds_gap_head_score_details(rows, heads),
        "attackers": role_split.get("attackers") or [],
        "attack_scores": role_split.get("attack_scores") or {},
        "finishers": role_split.get("finishers") or [],
        "finisher_scores": role_split.get("finisher_scores") or {},
        "support_boats": role_split.get("support_boats") or [],
        "support_scores": role_split.get("support_scores") or {},
        "role_split_note": role_split.get("role_split_note"),
        "axes": axes,
        "axis_rule": axis_rule,
        "alt_axes": [],
        "alt_axis_rule": "売れすぎ1号艇飛び専用: 軸はAI3連対率+一般3連対率の1位と3位",
        "supports": pool,
        "keshi": keshi,
        "keshi_reason": keshi_reason,
        "ai_plus_rank6_boat": ai_plus_rank6_boat,
        "ai_plus_rank6_revival": ai_plus_rank6_revival,
        "role_note": (
            f"歪み本命拡張。1号艇は{popularity_level}+危険"
            f"（展示{fmt_role(b1_tenji_rank)}位/1周{fmt_role(b1_isshu_rank)}位）。"
            f"頭は1艇に絞って{head}号艇。軸は{axis_rule}の{axes[0]},{axes[1]}。"
            f"1号艇頭は買わず、消し{keshi}以外へ2・3着折り返し{len(tickets)}点"
        ),
    }


def odds_gap_b1_danger_head1_8(rows):
    metrics = rows[0].get("_morning_metrics") or {}
    if not (b1_publicly_backed(metrics) and b1_data_danger(metrics)):
        return set(), None

    popularity_level = b1_popularity_context(metrics).get("level") or "人気あり"
    role_split = role_split_details(rows, exclude={1})
    heads = odds_gap_head_candidates(rows, exclude={1}, count=1)
    axes, axis_rule = axis_boats_for_roles(rows, ranks=(1, 3))
    if len(heads) < 1 or len(axes) < 2:
        return set(), None

    head = heads[0]
    keshi, keshi_reason, ai_plus_rank6_boat, ai_plus_rank6_revival = select_keshi_boat(
        rows, protected=set(heads + axes)
    )
    if keshi is None:
        return set(), None

    pool = [boat for boat in range(1, 7) if boat != keshi]
    tickets = set()
    for axis in axes:
        if axis in {head, keshi}:
            continue
        for other in pool:
            if other in {head, axis}:
                continue
            tickets.add(f"{head}{axis}{other}")
            tickets.add(f"{head}{other}{axis}")
    if not tickets:
        return set(), None

    tickets = trim_tickets(tickets, heads, axes, max_points=BUY_TICKET_MAX_POINTS, rows=rows)
    if len(tickets) < BUY_TICKET_MIN_POINTS:
        return set(), None

    return {
        ticket
        for ticket in tickets
        if len(norm_combo(ticket)) == 3 and "1" not in norm_combo(ticket)[:1]
    }, {
        "heads": heads,
        "head_rule": (
            f"1号艇が{popularity_level}で危険。取り逃し対策として頭を広げず、"
            "2〜6号艇の歪み頭スコア最上位1艇だけを頭にする"
        ),
        "head_mode": "odds_gap_b1_danger_head1_recovery",
        "head_scores": odds_gap_head_score_details(rows, heads),
        "attackers": role_split.get("attackers") or [],
        "attack_scores": role_split.get("attack_scores") or {},
        "finishers": role_split.get("finishers") or [],
        "finisher_scores": role_split.get("finisher_scores") or {},
        "support_boats": role_split.get("support_boats") or [],
        "support_scores": role_split.get("support_scores") or {},
        "role_split_note": role_split.get("role_split_note"),
        "axes": axes,
        "axis_rule": axis_rule,
        "alt_axes": [],
        "alt_axis_rule": "人気1号艇危険リカバリー: 軸はAI3連対率+一般3連対率の1位と3位",
        "supports": pool,
        "keshi": keshi,
        "keshi_reason": keshi_reason,
        "ai_plus_rank6_boat": ai_plus_rank6_boat,
        "ai_plus_rank6_revival": ai_plus_rank6_revival,
        "role_note": (
            f"取り逃し対策。1号艇は{popularity_level}+危険なので頭では買わない。"
            f"頭は最上位の{head}号艇だけ。軸は{axis_rule}の{axes[0]},{axes[1]}。"
            f"消し{keshi}以外へ2・3着折り返し{len(tickets)}点"
        ),
    }


def combo_boats(value):
    combo = norm_combo(value)
    return [int(ch) for ch in combo] if len(combo) == 3 else []


def axis_hit(axes, trifecta):
    boats = set(combo_boats(trifecta))
    return bool(boats & set(axes or [])) if boats else None


def default_trifecta_odds_db():
    for path in TRIFECTA_ODDS_DB_CANDIDATES:
        if path.exists():
            return path
    return TRIFECTA_ODDS_DB_CANDIDATES[0]


def venue_code_candidates(race):
    candidates = []

    def add(value):
        if value is None or value == "":
            return
        text = str(value).strip()
        if not text:
            return
        candidates.append(text)
        try:
            number = int(float(text))
        except (TypeError, ValueError):
            return
        candidates.append(str(number))
        candidates.append(f"{number:02d}")

    add((race or {}).get("place_id"))
    add((race or {}).get("venue_code"))
    add((race or {}).get("jcd"))
    add(PLACE_CODES.get((race or {}).get("place_name")))
    race_id_digits = norm_combo((race or {}).get("race_id"))
    if len(race_id_digits) >= 12:
        add(race_id_digits[-4:-2])

    unique = []
    seen = set()
    for item in candidates:
        if item in seen:
            continue
        seen.add(item)
        unique.append(item)
    return unique


def official_venue_code(race):
    for candidate in venue_code_candidates(race):
        try:
            number = int(float(candidate))
        except (TypeError, ValueError):
            continue
        if 1 <= number <= 24:
            return f"{number:02d}"
    return ""


def official_beforeinfo_url(race):
    venue = official_venue_code(race)
    date_text = (race or {}).get("date")
    try:
        round_no = int((race or {}).get("round") or (race or {}).get("round_no") or 0)
    except (TypeError, ValueError):
        round_no = 0
    if not venue or not date_text or round_no <= 0:
        return ""
    return OFFICIAL_BEFOREINFO_URL.format(
        rno=round_no,
        jcd=venue,
        hd=str(date_text).replace("-", ""),
    )


class OfficialBeforeInfoTableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables = []
        self._in_table = 0
        self._row = None
        self._cell = None
        self._buffer = []

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._in_table += 1
            if self._in_table == 1:
                self._table = []
        elif self._in_table and tag == "tr":
            self._row = []
        elif self._in_table and tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag):
        if tag in {"td", "th"} and self._cell is not None:
            text = html.unescape("".join(self._cell))
            text = re.sub(r"\s+", " ", text).strip()
            self._row.append(text)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if any(cell for cell in self._row):
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._in_table:
            if getattr(self, "_table", None):
                self.tables.append(self._table)
            self._in_table -= 1


def parse_official_st_time(value):
    text = str(value or "").strip().upper().replace(" ", "")
    if not text:
        return None
    sign = -1 if text.startswith("F") else 1
    text = text.lstrip("FL")
    if text.startswith("."):
        text = f"0{text}"
    try:
        number = float(text)
    except ValueError:
        return None
    if 0 <= number < 1:
        return round(sign * number, 3)
    return None


def parse_official_float(value):
    text = str(value or "").strip().replace(",", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    number = as_num(match.group(0))
    return number


def parse_official_weather_info(text):
    payload = {}
    match = re.search(r'<div class="weather1">(.*?)<div class="weather1_stand">', text or "", re.S)
    if not match:
        return payload
    block = match.group(1)
    title = re.search(r'class="weather1_title">\s*([^<]+?)\s*</p>', block, re.S)
    if title:
        payload["weather_info_title"] = re.sub(r"\s+", " ", html.unescape(title.group(1))).strip()
    weather = re.search(
        r'is-weather".*?weather1_bodyUnitLabelTitle">\s*([^<]+?)\s*</span>',
        block,
        re.S,
    )
    if weather:
        payload["weather"] = re.sub(r"\s+", " ", html.unescape(weather.group(1))).strip()
    direction = re.search(r'is-windDirection".*?is-wind(\d+)', block, re.S)
    if direction:
        payload["wind_direction"] = direction.group(1)
    label_map = {
        "気温": "weather_degree",
        "風速": "wind_speed",
        "水温": "water_degree",
        "波高": "wave_height",
    }
    for label, key in label_map.items():
        value_match = re.search(
            rf'weather1_bodyUnitLabelTitle">\s*{label}\s*</span>\s*'
            r'<span class="weather1_bodyUnitLabelData">\s*([^<]+?)\s*</span>',
            block,
            re.S,
        )
        if value_match:
            number = parse_official_float(value_match.group(1))
            if number is not None:
                payload[key] = number
    return payload


def rank_official_beforeinfo_boats(boats):
    def rank_field(value_key, rank_key, ascending=True):
        values = [
            (boat, data.get(value_key))
            for boat, data in boats.items()
            if data.get(value_key) is not None
        ]
        values.sort(key=lambda item: item[1], reverse=not ascending)
        for rank, (boat, _) in enumerate(values, start=1):
            boats[boat][rank_key] = rank

    rank_field("tenji_time", "tenji_rank", ascending=True)
    rank_field("start_tenji_time", "start_tenji_time_rank", ascending=True)
    return boats


def parse_official_beforeinfo(text):
    parser = OfficialBeforeInfoTableParser()
    parser.feed(text or "")
    boats = {boat: {"boat_number": boat} for boat in range(1, 7)}
    for table in parser.tables:
        flat = " ".join(cell for row in table for cell in row)
        is_exhibition_table = "展示" in flat and "タイム" in flat and "チルト" in flat
        is_start_table = "スタート展示" in flat and "ST" in flat
        if not is_exhibition_table and not is_start_table:
            continue
        tenji_col = None
        tilt_col = None
        if is_exhibition_table:
            for row in table:
                normalized = [re.sub(r"\s+", "", str(cell or "")) for cell in row]
                if tenji_col is None and "展示タイム" in normalized:
                    tenji_col = normalized.index("展示タイム")
                if tilt_col is None and "チルト" in normalized:
                    tilt_col = normalized.index("チルト")
                if tenji_col is not None and tilt_col is not None:
                    break
        start_course = 0
        for row in table:
            raw_cells = list(row)
            cells = [cell for cell in row if cell]
            if not cells:
                continue
            try:
                boat = int(str(raw_cells[0]).strip())
            except (TypeError, ValueError):
                boat = None
                if is_start_table:
                    match = re.match(r"^([1-6])\s+(.+)$", str(cells[0]).strip())
                    if match:
                        boat = int(match.group(1))
                        cells = [match.group(1), match.group(2)]
                if boat is None:
                    continue
            if boat not in boats:
                continue
            if is_exhibition_table:
                tenji = (
                    parse_official_float(raw_cells[tenji_col])
                    if tenji_col is not None and tenji_col < len(raw_cells)
                    else None
                )
                if tenji is not None and 5.0 <= tenji <= 8.5:
                    boats[boat]["tenji_time"] = tenji
                tilt = (
                    parse_official_float(raw_cells[tilt_col])
                    if tilt_col is not None and tilt_col < len(raw_cells)
                    else None
                )
                if tilt is not None and -2.0 <= tilt <= 3.0:
                    boats[boat]["tilt"] = round(tilt, 1)
            if is_start_table:
                st_value = None
                for cell in cells[1:]:
                    st_value = parse_official_st_time(cell)
                    if st_value is not None:
                        break
                if st_value is not None:
                    start_course += 1
                    boats[boat]["start_tenji_time"] = st_value
                    boats[boat]["start_tenji_rank"] = start_course
                    boats[boat]["before_start_sinnyu"] = start_course
    boats = rank_official_beforeinfo_boats(boats)
    payload = {
        "source": "official_boatrace",
        "boats": {str(boat): data for boat, data in boats.items()},
        "tenji_boats": sum(1 for data in boats.values() if data.get("tenji_time") is not None),
        "start_tenji_boats": sum(1 for data in boats.values() if data.get("start_tenji_time") is not None),
        "tilt_boats": sum(1 for data in boats.values() if data.get("tilt") is not None),
    }
    payload.update(parse_official_weather_info(text or ""))
    return payload


def official_beforeinfo_cache_key(race):
    venue = official_venue_code(race)
    date_text = (race or {}).get("date") or ""
    try:
        round_no = int((race or {}).get("round") or (race or {}).get("round_no") or 0)
    except (TypeError, ValueError):
        round_no = 0
    if not venue or not date_text or round_no <= 0:
        return ""
    return f"{date_text}:{venue}:{round_no}"


def fetch_official_beforeinfo(race):
    date_text = (race or {}).get("date")
    key = official_beforeinfo_cache_key(race)
    url = official_beforeinfo_url(race)
    if not date_text or not key or not url:
        return {}
    path = official_beforeinfo_path(date_text)
    payload = load_json(
        path,
        {
            "version": "official-boatrace-beforeinfo-v1",
            "date": date_text,
            "races": {},
        },
    )
    races = payload.setdefault("races", {})
    cached = races.get(key)
    if (
        isinstance(cached, dict)
        and cached.get("tenji_boats")
        and cached.get("start_tenji_boats")
        and int(cached.get("parser_version") or 0) >= OFFICIAL_BEFOREINFO_PARSER_VERSION
    ):
        return cached
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept-Language": "ja,en-US;q=0.8,en;q=0.6",
        },
    )
    with urllib.request.urlopen(request, timeout=20, context=ssl._create_unverified_context()) as response:
        text = response.read().decode("utf-8", errors="replace")
    parsed = parse_official_beforeinfo(text)
    parsed.update(
        {
            "url": url,
            "key": key,
            "parser_version": OFFICIAL_BEFOREINFO_PARSER_VERSION,
            "fetched_at": datetime.now(JST).isoformat(timespec="seconds"),
        }
    )
    if parsed.get("tenji_boats") or parsed.get("start_tenji_boats"):
        races[key] = parsed
        payload["updated_at"] = parsed["fetched_at"]
        save_json(path, payload)
    return parsed


def rows_need_official_aux(rows):
    for row in rows or []:
        if row.get("tenji_time") is None:
            return True
        if row.get("start_tenji_time") is None:
            return True
        if row.get("tilt") is None:
            return True
    return False


def apply_official_beforeinfo_aux(rows, race):
    if not rows_need_official_aux(rows):
        return None
    try:
        aux = fetch_official_beforeinfo(race)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
        summary = {
            "source": "official_boatrace",
            "available": False,
            "error": str(exc)[-300:],
            "url": official_beforeinfo_url(race),
        }
        for row in rows:
            row["_official_aux_summary"] = summary
        return summary
    boats = aux.get("boats") if isinstance(aux, dict) else {}
    if not isinstance(boats, dict):
        return None
    for row in rows:
        boat = row.get("boat_number")
        data = boats.get(str(boat)) or {}
        row["official_tenji_time"] = data.get("tenji_time")
        row["official_tenji_rank"] = data.get("tenji_rank")
        row["official_start_tenji_time"] = data.get("start_tenji_time")
        row["official_start_tenji_time_rank"] = data.get("start_tenji_time_rank")
        row["official_start_tenji_rank"] = data.get("start_tenji_rank")
        row["official_before_start_sinnyu"] = data.get("before_start_sinnyu")
        row["official_tilt"] = data.get("tilt")
        has_official_value = any(
            data.get(key) is not None
            for key in (
                "tenji_time",
                "start_tenji_time",
                "before_start_sinnyu",
                "tilt",
            )
        )
        row["official_data_source"] = "official_boatrace" if has_official_value else ""
    summary = {
        "source": "official_boatrace",
        "available": bool(aux.get("tenji_boats") or aux.get("start_tenji_boats")),
        "url": aux.get("url") or official_beforeinfo_url(race),
        "fetched_at": aux.get("fetched_at") or "",
        "tenji_boats": int(aux.get("tenji_boats") or 0),
        "start_tenji_boats": int(aux.get("start_tenji_boats") or 0),
        "tilt_boats": int(aux.get("tilt_boats") or 0),
    }
    for row in rows:
        row["_official_aux_summary"] = summary
    return summary


def merge_official_aux_into_live_by_boat(by_boat, rows):
    """Use official BOATRACE beforeinfo as live display/start fallback values."""

    changed = False
    field_map = [
        ("tenji_time", "official_tenji_time"),
        ("tenji_rank", "official_tenji_rank"),
        ("start_tenji_time", "official_start_tenji_time"),
        ("start_tenji_rank", "official_start_tenji_time_rank"),
        ("before_start_sinnyu", "official_before_start_sinnyu"),
        ("tilt", "official_tilt"),
    ]
    for row in rows:
        boat = int(row.get("boat_number") or 0)
        if boat not in by_boat:
            continue
        target = by_boat[boat]
        for target_key, official_key in field_map:
            current = as_num(target.get(target_key))
            official = as_num(row.get(official_key))
            if current is None and official is not None:
                target[target_key] = official
                changed = True
    return changed


def attach_row_aux_summaries(rows, source_rows=None, imitation_summary=None, self_ai_summary=None):
    source_by_boat = {int(row.get("boat_number") or 0): row for row in source_rows or []}
    aux_keys = [
        "official_tenji_time",
        "official_tenji_rank",
        "official_start_tenji_time",
        "official_start_tenji_time_rank",
        "official_start_tenji_rank",
        "official_before_start_sinnyu",
        "official_tilt",
        "official_data_source",
        "_official_aux_summary",
    ]
    for row in rows:
        source = source_by_boat.get(int(row.get("boat_number") or 0), {})
        for key in aux_keys:
            if key in source:
                row[key] = source[key]
        if imitation_summary:
            row["_boaters_imitation_summary"] = imitation_summary
        if self_ai_summary:
            row["_self_ai_summary"] = self_ai_summary


def load_boaters_imitation_artifact(model_path):
    if not model_path:
        return None
    if self_ai is None or imitation_ai is None:
        raise RuntimeError("self AI modules are unavailable; cannot apply BOATERS-imitation model")
    path = Path(model_path).expanduser()
    key = str(path.resolve())
    if key not in BOATERS_IMITATION_MODEL_CACHE:
        BOATERS_IMITATION_MODEL_CACHE[key] = joblib.load(path)
    return BOATERS_IMITATION_MODEL_CACHE[key]


def load_self_ai_artifact(model_path):
    if not model_path:
        return None
    if self_ai is None:
        raise RuntimeError("self AI module is unavailable")
    path = Path(model_path).expanduser()
    key = str(path.resolve())
    if key not in SELF_AI_MODEL_CACHE:
        SELF_AI_MODEL_CACHE[key] = joblib.load(path)
    return SELF_AI_MODEL_CACHE[key]


def load_trifecta_position_artifact(model_path):
    if not model_path:
        return None
    if self_ai is None or trifecta_position_model is None:
        raise RuntimeError("trifecta position model modules are unavailable")
    path = Path(model_path).expanduser()
    key = str(path.resolve())
    if key not in TRIFECTA_POSITION_MODEL_CACHE:
        TRIFECTA_POSITION_MODEL_CACHE[key] = joblib.load(path)
    return TRIFECTA_POSITION_MODEL_CACHE[key]


def load_venue_probability_overlay_artifact(model_path):
    if not model_path:
        return None
    if self_ai is None or venue_probability_overlay is None:
        raise RuntimeError("venue probability overlay modules are unavailable")
    path = Path(model_path).expanduser()
    key = str(path.resolve())
    if key not in VENUE_PROBABILITY_OVERLAY_CACHE:
        VENUE_PROBABILITY_OVERLAY_CACHE[key] = joblib.load(path)
    return VENUE_PROBABILITY_OVERLAY_CACHE[key]


def first_non_empty(*values):
    for value in values:
        if value is not None and value != "":
            return value
    return None


def first_num(*values):
    for value in values:
        number = as_num(value)
        if number is not None:
            return number
    return None


def live_feature_frame_from_rows(rows, race, source_by_boat):
    if self_ai is None:
        raise RuntimeError("train_self_boatrace_ai is unavailable")
    morning_metrics = rows[0].get("_morning_metrics") if rows else {}
    morning_metrics = morning_metrics or {}
    place_name = race.get("place_name") or morning_metrics.get("place_name") or ""
    place_id = first_non_empty(
        race.get("place_id"),
        race.get("jcd"),
        morning_metrics.get("place_id"),
        PLACE_CODES.get(place_name),
    )
    round_no = first_num(race.get("round"), race.get("rno"), morning_metrics.get("round"))
    race_id = str(
        first_non_empty(
            race.get("race_id"),
            f"{race.get('date')}_{place_id}_{int(round_no or 0):02d}",
        )
    )
    records = []
    for row in rows:
        boat = int(row.get("boat_number") or 0)
        source = source_by_boat.get(boat, {}) if source_by_boat else {}
        records.append(
            {
                "race_id": race_id,
                "date": race.get("date") or morning_metrics.get("date"),
                "place_id": place_id,
                "place_name": place_name,
                "round": round_no,
                "race_grade": first_non_empty(race.get("race_grade"), race.get("grade"), morning_metrics.get("race_grade"), ""),
                "weather": first_non_empty(race.get("weather"), morning_metrics.get("weather"), ""),
                "weather_degree": first_num(race.get("weather_degree"), morning_metrics.get("weather_degree")),
                "wind_speed": first_num(race.get("wind_speed"), morning_metrics.get("wind_speed")),
                "wind_direction": first_non_empty(race.get("wind_direction"), morning_metrics.get("wind_direction"), ""),
                "water_degree": first_num(race.get("water_degree"), morning_metrics.get("water_degree")),
                "wave_height": first_num(race.get("wave_height"), morning_metrics.get("wave_height")),
                "boat_number": boat,
                "racer_id": first_non_empty(source.get("racer_id"), row.get("racer_id"), ""),
                "reg_no": first_num(source.get("reg_no"), row.get("reg_no")),
                "rank": first_non_empty(source.get("rank"), row.get("rank"), ""),
                "general_3ren_pct": first_num(row.get("general_3ren_pct"), source.get("general_3ren_pct")),
                "general_3ren_count": first_num(source.get("general_3ren_count"), row.get("general_3ren_count")),
                "st_rank_general": first_num(row.get("st_rank_general"), source.get("st_rank_general")),
                "st_time_avg_general": first_num(source.get("st_time_avg_general"), row.get("st_time_avg_general")),
                "tenji_time": first_num(row.get("tenji_time"), source.get("tenji_time")),
                "tenji_rank": first_num(row.get("tenji_rank"), row.get("tenji_time_rank"), source.get("tenji_rank")),
                "isshu_time": first_num(row.get("raw_isshu_time"), source.get("isshu_time")),
                "hanshu_time": first_num(row.get("hanshu_time"), source.get("hanshu_time")),
                "avg_isshu_diff": first_num(row.get("avg_isshu_diff"), source.get("avg_isshu_diff")),
                "chokusen_time": first_num(row.get("chokusen_time"), source.get("chokusen_time")),
                "mawariashi_time": first_num(row.get("mawariashi_time"), source.get("mawariashi_time")),
                "before_start_sinnyu": first_num(row.get("before_start_sinnyu"), source.get("before_start_sinnyu")),
                "start_tenji_time": first_num(row.get("start_tenji_time"), source.get("start_tenji_time")),
                "start_tenji_rank": first_num(row.get("start_tenji_rank"), row.get("start_tenji_time_rank"), source.get("start_tenji_rank")),
                "tilt": first_num(row.get("tilt"), source.get("tilt")),
                "weight": first_num(source.get("weight"), row.get("weight")),
                "weight_adjust": first_num(source.get("weight_adjust"), row.get("weight_adjust")),
                "nige_pct_year": first_num(source.get("nige_pct_year"), source.get("nige_pct"), row.get("nige_pct")),
                "sasare_pct_year": first_num(source.get("sasare_pct_year"), source.get("sasare_pct"), row.get("sasare_pct")),
                "makurare_pct_year": first_num(source.get("makurare_pct_year"), source.get("makurare_pct"), row.get("makurare_pct")),
                "sashi_pct_year": first_num(source.get("sashi_pct_year"), row.get("sashi_pct_year")),
                "makuri_pct_year": first_num(source.get("makuri_pct_year"), row.get("makuri_pct_year")),
                "makurizashi_pct_year": first_num(source.get("makurizashi_pct_year"), row.get("makurizashi_pct_year")),
                "makurizasare_pct_year": first_num(source.get("makurizasare_pct_year"), row.get("makurizasare_pct_year")),
                "nigashi_pct_year": first_num(source.get("nigashi_pct_year"), row.get("nigashi_pct_year")),
                "win_method_race_count_year": first_num(source.get("win_method_race_count_year"), row.get("win_method_race_count_year")),
                "official_avg_st": first_num(source.get("avg_st"), row.get("official_avg_st")),
                "official_flying_count": first_num(source.get("flying_count"), row.get("official_flying_count")),
                "official_late_count": first_num(source.get("late_count"), row.get("official_late_count")),
                "national_win_rate": first_num(source.get("national_win_rate"), row.get("national_win_rate")),
                "national_2ren_pct": first_num(source.get("national_2ren_pct"), row.get("national_2ren_pct")),
                "national_3ren_pct": first_num(source.get("national_3ren_pct"), row.get("national_3ren_pct")),
                "local_win_rate": first_num(source.get("local_win_rate"), row.get("local_win_rate")),
                "local_2ren_pct": first_num(source.get("local_2ren_pct"), row.get("local_2ren_pct")),
                "local_3ren_pct": first_num(source.get("local_3ren_pct"), row.get("local_3ren_pct")),
                "motor_2ren_pct": first_num(source.get("motor_2ren_pct"), row.get("motor_2ren_pct")),
                "motor_3ren_pct": first_num(source.get("motor_3ren_pct"), row.get("motor_3ren_pct")),
                "equipment_boat_2ren_pct": first_num(source.get("equipment_boat_2ren_pct"), row.get("equipment_boat_2ren_pct")),
                "equipment_boat_3ren_pct": first_num(source.get("equipment_boat_3ren_pct"), row.get("equipment_boat_3ren_pct")),
                "motor_2ren_vs_venue": first_num(source.get("motor_2ren_vs_venue"), row.get("motor_2ren_vs_venue")),
                "motor_3ren_vs_venue": first_num(source.get("motor_3ren_vs_venue"), row.get("motor_3ren_vs_venue")),
                "equipment_boat_2ren_vs_venue": first_num(source.get("equipment_boat_2ren_vs_venue"), row.get("equipment_boat_2ren_vs_venue")),
                "equipment_boat_3ren_vs_venue": first_num(source.get("equipment_boat_3ren_vs_venue"), row.get("equipment_boat_3ren_vs_venue")),
                "local_course_starts": first_num(source.get("local_course_starts"), row.get("local_course_starts")),
                "local_course_win_pct": first_num(source.get("local_course_win_pct"), row.get("local_course_win_pct")),
                "local_course_top3_pct": first_num(source.get("local_course_top3_pct"), row.get("local_course_top3_pct")),
                "local_course_avg_start_time": first_num(source.get("local_course_avg_start_time"), row.get("local_course_avg_start_time")),
                "national_course_starts": first_num(source.get("national_course_starts"), row.get("national_course_starts")),
                "national_course_win_pct": first_num(source.get("national_course_win_pct"), row.get("national_course_win_pct")),
                "national_course_top3_pct": first_num(source.get("national_course_top3_pct"), row.get("national_course_top3_pct")),
                "national_course_avg_start_time": first_num(source.get("national_course_avg_start_time"), row.get("national_course_avg_start_time")),
                "local_total_starts": first_num(source.get("local_total_starts"), row.get("local_total_starts")),
                "local_total_win_pct": first_num(source.get("local_total_win_pct"), row.get("local_total_win_pct")),
                "local_total_top3_pct": first_num(source.get("local_total_top3_pct"), row.get("local_total_top3_pct")),
                "local_total_avg_start_time": first_num(source.get("local_total_avg_start_time"), row.get("local_total_avg_start_time")),
                "national_total_starts": first_num(source.get("national_total_starts"), row.get("national_total_starts")),
                "national_total_win_pct": first_num(source.get("national_total_win_pct"), row.get("national_total_win_pct")),
                "national_total_top3_pct": first_num(source.get("national_total_top3_pct"), row.get("national_total_top3_pct")),
                "national_total_avg_start_time": first_num(source.get("national_total_avg_start_time"), row.get("national_total_avg_start_time")),
                "first_market_win_pct": first_num(source.get("first_market_win_pct"), row.get("first_market_win_pct")),
                "close_market_win_pct": first_num(source.get("close_market_win_pct"), row.get("close_market_win_pct")),
                "market_win_move_pct": first_num(source.get("market_win_move_pct"), row.get("market_win_move_pct")),
                "first_market_top3_pct": first_num(source.get("first_market_top3_pct"), row.get("first_market_top3_pct")),
                "close_market_top3_pct": first_num(source.get("close_market_top3_pct"), row.get("close_market_top3_pct")),
                "market_top3_move_pct": first_num(source.get("market_top3_move_pct"), row.get("market_top3_move_pct")),
                "close_min_head_odds": first_num(source.get("close_min_head_odds"), row.get("close_min_head_odds")),
                "close_top10_head_count": first_num(source.get("close_top10_head_count"), row.get("close_top10_head_count")),
                "tide_level_cm": first_num(race.get("tide_level_cm"), morning_metrics.get("tide_level_cm")),
                "predicted_tide_cm": first_num(race.get("predicted_tide_cm"), morning_metrics.get("predicted_tide_cm")),
                "predicted_tide_tp_cm": first_num(race.get("predicted_tide_tp_cm"), morning_metrics.get("predicted_tide_tp_cm")),
                "observed_tide_cm": first_num(race.get("observed_tide_cm"), morning_metrics.get("observed_tide_cm")),
                "observed_tide_tp_cm": first_num(race.get("observed_tide_tp_cm"), morning_metrics.get("observed_tide_tp_cm")),
                "observed_age_minutes": first_num(race.get("observed_age_minutes"), morning_metrics.get("observed_age_minutes")),
                "observed_lead_to_race_minutes": first_num(race.get("observed_lead_to_race_minutes"), morning_metrics.get("observed_lead_to_race_minutes")),
                "observed_prediction_anomaly_cm": first_num(race.get("observed_prediction_anomaly_cm"), morning_metrics.get("observed_prediction_anomaly_cm")),
                "tide_delta_cm_per_hour": first_num(race.get("tide_delta_cm_per_hour"), morning_metrics.get("tide_delta_cm_per_hour")),
                "daily_tide_range_cm": first_num(race.get("daily_tide_range_cm"), morning_metrics.get("daily_tide_range_cm")),
                "tide_range_position": first_num(race.get("tide_range_position"), morning_metrics.get("tide_range_position")),
                "minutes_to_next_extreme": first_num(race.get("minutes_to_next_extreme"), morning_metrics.get("minutes_to_next_extreme")),
                "current_speed_mps": first_num(race.get("current_speed_mps"), morning_metrics.get("current_speed_mps")),
                "current_signed_knots": first_num(race.get("current_signed_knots"), morning_metrics.get("current_signed_knots")),
                "current_direction_deg": first_num(race.get("current_direction_deg"), morning_metrics.get("current_direction_deg")),
                "air_pressure_hpa": first_num(race.get("air_pressure_hpa"), morning_metrics.get("air_pressure_hpa")),
                "precipitation_mm": first_num(race.get("precipitation_mm"), morning_metrics.get("precipitation_mm")),
                "tide_phase": first_non_empty(race.get("tide_phase"), morning_metrics.get("tide_phase"), ""),
                "water_applicability": first_non_empty(race.get("water_applicability"), morning_metrics.get("water_applicability"), ""),
                "water_source_quality": first_non_empty(race.get("water_source_quality"), morning_metrics.get("water_source_quality"), ""),
                "next_extreme_type": first_non_empty(race.get("next_extreme_type"), morning_metrics.get("next_extreme_type"), ""),
                "current_direction_text": first_non_empty(
                    race.get("current_direction_text"),
                    morning_metrics.get("current_direction_text"),
                    "",
                ),
                "current_quality": first_non_empty(race.get("current_quality"), morning_metrics.get("current_quality"), ""),
                "ai_prediction_pct": first_num(row.get("ai_prediction_pct"), source.get("ai_prediction_pct")),
                "odds_prediction_pct": first_num(row.get("odds_prediction_pct"), source.get("odds_prediction_pct")),
                "ai_3ren_pct": first_num(row.get("ai_3ren_pct"), source.get("ai_3ren_pct")),
            }
        )
    return self_ai.pd.DataFrame(records)


def apply_boaters_imitation_to_live_by_boat(by_boat, rows, race, model_path, odds_mode):
    artifact = load_boaters_imitation_artifact(model_path)
    if not artifact:
        return None
    frame = live_feature_frame_from_rows(rows, race, by_boat)
    if frame.empty:
        return None
    frame = self_ai.add_derived_features(frame)
    matrix, _ = self_ai.build_matrix(
        frame,
        artifact["feature_columns"],
        artifact.get("numeric_features"),
        artifact.get("categorical_features"),
    )
    predicted = imitation_ai.add_imitation_predictions(
        frame,
        matrix,
        artifact["models"],
        calibration=artifact.get("calibration"),
        target_specs=artifact.get("target_specs"),
    )
    applied = 0
    for pred in predicted.to_dict("records"):
        boat = int(pred.get("boat_number") or 0)
        if boat not in by_boat:
            continue
        target = by_boat[boat]
        target["original_ai_prediction_pct"] = target.get("ai_prediction_pct")
        target["original_ai_3ren_pct"] = target.get("ai_3ren_pct")
        target["original_odds_prediction_pct"] = target.get("odds_prediction_pct")
        target["imit_ai_prediction_pct"] = round(float(pred["imit_ai_prediction_pct"]), 4)
        target["imit_ai_3ren_pct"] = round(float(pred["imit_ai_3ren_pct"]), 4)
        target["imit_odds_prediction_pct"] = round(float(pred["imit_odds_prediction_pct"]), 4)
        target["ai_prediction_pct"] = target["imit_ai_prediction_pct"]
        target["ai_3ren_pct"] = target["imit_ai_3ren_pct"]
        if as_num(target.get("general_3ren_pct")) is None:
            target["general_3ren_pct"] = 0.0
            target["general_3ren_source"] = "missing_zero_for_local_ai_plus"
        if odds_mode == "imitate":
            target["odds_prediction_pct"] = target["imit_odds_prediction_pct"]
        target["ai_source"] = "local_boaters_imitation"
        applied += 1
    calibration = artifact.get("calibration") or {}
    return {
        "source": "local_boaters_imitation_ai",
        "available": applied == 6,
        "model_path": str(Path(model_path).expanduser()),
        "odds_mode": odds_mode,
        "calibration_mode": calibration.get("mode") or "",
        "enabled_calibration_venues": calibration.get("enabled_venues"),
        "boats_applied": applied,
    }


def apply_self_ai_to_live_by_boat(by_boat, rows, race, model_path, mode="shadow", odds_mode="keep"):
    artifact = load_self_ai_artifact(model_path)
    if not artifact:
        return None
    frame = live_feature_frame_from_rows(rows, race, by_boat)
    if frame.empty:
        return None
    frame = self_ai.add_derived_features(frame)
    matrix, _ = self_ai.build_matrix(
        frame,
        artifact["feature_columns"],
        artifact.get("numeric_features"),
        artifact.get("categorical_features"),
    )
    win_proba = artifact["win_model"].predict_proba(matrix)[:, 1]
    top3_proba = artifact["top3_model"].predict_proba(matrix)[:, 1]
    win_temperature = float(as_num(artifact.get("win_temperature")) or 1.0)
    top3_temperature = float(as_num(artifact.get("top3_temperature")) or 1.0)
    win_proba = self_ai.np.power(
        self_ai.np.clip(win_proba, 1e-6, 1.0 - 1e-6),
        1.0 / win_temperature,
    )
    top3_proba = self_ai.np.clip(top3_proba, 1e-6, 1.0 - 1e-6)
    top3_logit = self_ai.np.log(top3_proba / (1.0 - top3_proba)) / top3_temperature
    top3_proba = 1.0 / (1.0 + self_ai.np.exp(-top3_logit))
    predicted = self_ai.add_predictions(frame, win_proba, top3_proba)
    b1_mask = frame["boat_number"].astype(int) == 1
    b1_no_win_pct = None
    b1_outside_top3_pct = None
    if b1_mask.any() and artifact.get("b1_no_win_model") is not None:
        b1_no_win_pct = round(
            float(artifact["b1_no_win_model"].predict_proba(matrix.loc[b1_mask])[:, 1][0] * 100.0),
            4,
        )
    if b1_mask.any() and artifact.get("b1_outside_top3_model") is not None:
        b1_outside_top3_pct = round(
            float(artifact["b1_outside_top3_model"].predict_proba(matrix.loc[b1_mask])[:, 1][0] * 100.0),
            4,
        )

    numeric_features = artifact.get("numeric_features") or []
    available_numeric = sum(
        1
        for feature in numeric_features
        if feature in frame.columns and frame[feature].notna().any()
    )
    per_boat = []
    applied = 0
    for pred in predicted.to_dict("records"):
        boat = int(pred.get("boat_number") or 0)
        if boat not in by_boat:
            continue
        target = by_boat[boat]
        win_pct = round(float(pred["self_ai_win_pct"]), 4)
        top3_pct = round(float(pred["self_ai_top3_pct"]), 4)
        target["self_ai_win_pct"] = win_pct
        target["self_ai_top3_pct"] = top3_pct
        target["self_ai_win_rank"] = int(pred["self_ai_win_rank"])
        target["self_ai_top3_rank"] = int(pred["self_ai_top3_rank"])
        target["self_ai_plus"] = round(float(pred["self_ai_plus"]), 4)
        target["self_ai_plus_rank"] = int(pred["self_ai_plus_rank"])
        if boat == 1:
            target["self_ai_b1_no_win_pct"] = (
                b1_no_win_pct if b1_no_win_pct is not None else round(100.0 - win_pct, 4)
            )
            target["self_ai_b1_outside_top3_pct"] = (
                b1_outside_top3_pct
                if b1_outside_top3_pct is not None
                else round(100.0 - top3_pct, 4)
            )
        if mode == "replace":
            target["original_ai_prediction_pct"] = target.get("ai_prediction_pct")
            target["original_ai_3ren_pct"] = target.get("ai_3ren_pct")
            target["original_odds_prediction_pct"] = target.get("odds_prediction_pct")
            target["ai_prediction_pct"] = win_pct
            target["ai_3ren_pct"] = top3_pct
            if odds_mode == "self_win":
                target["odds_prediction_pct"] = win_pct
            target["ai_source"] = "local_self_ai"
        per_boat.append(
            {
                "boat_number": boat,
                "win_pct": win_pct,
                "top3_pct": top3_pct,
                "win_rank": int(pred["self_ai_win_rank"]),
                "top3_rank": int(pred["self_ai_top3_rank"]),
            }
        )
        applied += 1
    per_boat.sort(key=lambda item: item["boat_number"])
    b1 = next((item for item in per_boat if item["boat_number"] == 1), None)
    return {
        "source": "local_self_ai",
        "available": applied == 6,
        "model_path": str(Path(model_path).expanduser()),
        "model_version": artifact.get("version") or "",
        "win_temperature": win_temperature,
        "top3_temperature": top3_temperature,
        "mode": mode,
        "odds_mode": odds_mode,
        "boats_applied": applied,
        "numeric_feature_coverage": {
            "available": available_numeric,
            "total": len(numeric_features),
            "pct": round(available_numeric / len(numeric_features) * 100.0, 2) if numeric_features else None,
        },
        "boat1_no_win_pct": (
            b1_no_win_pct if b1_no_win_pct is not None else (round(100.0 - b1["win_pct"], 4) if b1 else None)
        ),
        "boat1_outside_top3_pct": (
            b1_outside_top3_pct
            if b1_outside_top3_pct is not None
            else (round(100.0 - b1["top3_pct"], 4) if b1 else None)
        ),
        "per_boat": per_boat,
    }


def apply_trifecta_position_model_to_live_by_boat(by_boat, rows, race, model_path):
    artifact = load_trifecta_position_artifact(model_path)
    if not artifact:
        return None
    frame = live_feature_frame_from_rows(rows, race, by_boat)
    if frame.empty or len(frame) != 6:
        return {
            "source": "trifecta_position_model",
            "available": False,
            "reason": "six_boats_required",
        }
    required_ai = frame[["ai_prediction_pct", "ai_3ren_pct", "odds_prediction_pct"]]
    if not bool(required_ai.notna().all(axis=1).all()):
        return {
            "source": "trifecta_position_model",
            "available": False,
            "reason": "boaters_ai_fields_required",
        }
    frame = trifecta_position_model.add_position_features(frame)
    matrix, _ = trifecta_position_model.build_matrix(
        frame,
        artifact["feature_columns"],
    )
    raw_probabilities = {
        position: artifact["models"][position].predict_proba(matrix)[:, 1]
        for position in (1, 2, 3)
    }
    predicted = trifecta_position_model.normalize_role_probabilities(
        frame,
        raw_probabilities,
    )
    per_boat = []
    summary = {
        "source": "trifecta_position_model",
        "available": True,
        "model_path": str(Path(model_path).expanduser()),
        "model_version": artifact.get("version") or "",
        "policy_id": artifact.get("policy_id") or "",
        "active": False,
        "notification_enabled": False,
    }
    for pred in predicted.to_dict("records"):
        boat = int(pred.get("boat_number") or 0)
        if boat not in by_boat:
            continue
        target = by_boat[boat]
        item = {"boat_number": boat}
        for position in (1, 2, 3):
            key = f"trifecta_position{position}_pct"
            value = round(float(pred[key]), 6)
            target[key] = value
            item[f"position{position}_pct"] = value
        target["trifecta_position_policy_id"] = summary["policy_id"]
        target["trifecta_position_model_summary"] = summary
        per_boat.append(item)
    per_boat.sort(key=lambda item: item["boat_number"])
    summary["boats_applied"] = len(per_boat)
    summary["available"] = len(per_boat) == 6
    summary["per_boat"] = per_boat
    return summary


def apply_venue_probability_overlay_to_live_by_boat(
    by_boat,
    rows,
    race,
    model_path,
    *,
    ai_source,
):
    unavailable = {
        "source": "venue_probability_overlay",
        "available": False,
        "active": False,
        "notification_enabled": False,
    }
    if ai_source != original_boaters_forward.ORIGINAL_AI_SOURCE:
        return {**unavailable, "reason": "original_boaters_ai_required"}
    artifact = load_venue_probability_overlay_artifact(model_path)
    if not artifact:
        return {**unavailable, "reason": "artifact_unavailable"}
    frame = live_feature_frame_from_rows(rows, race, by_boat)
    if frame.empty or len(frame) != 6:
        return {**unavailable, "reason": "six_boats_required"}
    required_ai = frame[["ai_prediction_pct", "ai_3ren_pct"]]
    if not bool(required_ai.notna().all(axis=1).all()):
        return {**unavailable, "reason": "boaters_ai_fields_required"}

    frame = self_ai.add_derived_features(frame).copy()
    frame = venue_probability_overlay.add_super_slit(frame)
    frame = venue_probability_overlay.add_legacy_position_probabilities(frame)
    frame = venue_probability_overlay.add_factor_columns(
        frame,
        artifact.get("thresholds") or {},
    )
    predicted = venue_probability_overlay.apply_overlay(
        frame,
        artifact["effects"],
        artifact["scales"],
    )
    report = artifact.get("report") or {}
    summary = {
        "source": "venue_probability_overlay",
        "available": True,
        "model_path": str(Path(model_path).expanduser()),
        "model_version": artifact.get("version") or "",
        "policy_id": artifact.get("policy_id") or "",
        "selected_scales": artifact.get("scales") or {},
        "test_period": (report.get("periods") or {}).get("test") or {},
        "active": False,
        "notification_enabled": False,
    }
    per_boat = []
    for pred in predicted.to_dict("records"):
        boat = int(pred.get("boat_number") or 0)
        if boat not in by_boat:
            continue
        target = by_boat[boat]
        item = {"boat_number": boat}
        for position in (1, 2, 3):
            probability_key = f"venue_probability_position{position}_pct"
            adjustment_key = f"venue_probability_position{position}_adjustment_pp"
            factor_count_key = f"venue_probability_position{position}_factor_count"
            probability = round(float(pred[probability_key]), 6)
            adjustment = round(float(pred[adjustment_key]), 6)
            matches = int(pred[factor_count_key])
            target[probability_key] = probability
            target[adjustment_key] = adjustment
            target[factor_count_key] = matches
            item[f"position{position}_pct"] = probability
            item[f"position{position}_adjustment_pp"] = adjustment
            item[f"position{position}_factor_count"] = matches
        target["venue_probability_policy_id"] = summary["policy_id"]
        target["venue_probability_overlay_summary"] = summary
        per_boat.append(item)
    per_boat.sort(key=lambda item: item["boat_number"])
    summary["boats_applied"] = len(per_boat)
    summary["available"] = len(per_boat) == 6
    summary["per_boat"] = per_boat
    return summary


def parse_official_trifecta_odds(text):
    class Parser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.rows = []
            self.row = None
            self.current = None
            self.buffer = ""

        def handle_starttag(self, tag, attrs):
            if tag == "tr":
                self.row = []
            elif tag == "td" and self.row is not None:
                data = dict(attrs)
                self.current = {
                    "class": data.get("class", "") or "",
                    "rowspan": data.get("rowspan", "") or "",
                    "text": "",
                }
                self.buffer = ""

        def handle_data(self, data):
            if self.current is not None:
                self.buffer += data

        def handle_endtag(self, tag):
            if tag == "td" and self.current is not None:
                self.current["text"] = self.buffer.strip()
                self.row.append(self.current)
                self.current = None
            elif tag == "tr" and self.row is not None:
                self.rows.append(self.row)
                self.row = None

    parser = Parser()
    parser.feed(text)
    current_second = [None] * 6
    odds = {}
    for row in parser.rows:
        cells = [
            cell
            for cell in row
            if ("oddsPoint" in cell["class"])
            or (cell["text"] in {"1", "2", "3", "4", "5", "6"} and "boatColor" in cell["class"])
        ]
        if not cells:
            continue
        block_start = any(cell["rowspan"] not in {"", "1"} for cell in cells)
        step = 3 if block_start else 2
        if len(cells) % step != 0:
            continue
        for col in range(len(cells) // step):
            part = cells[col * step : (col + 1) * step]
            if block_start:
                current_second[col] = int(part[0]["text"])
                third_cell, odds_cell = part[1], part[2]
            else:
                third_cell, odds_cell = part[0], part[1]
            if current_second[col] is None:
                continue
            first = col + 1
            second = current_second[col]
            third = int(third_cell["text"])
            text_value = odds_cell["text"].replace(",", "")
            try:
                odds_value = float(text_value)
            except ValueError:
                continue
            if odds_value > 0:
                odds[f"{first}{second}{third}"] = odds_value
    return odds


def fetch_official_trifecta_odds(race):
    venue = official_venue_code(race)
    date_text = (race or {}).get("date")
    try:
        round_no = int((race or {}).get("round") or (race or {}).get("round_no") or 0)
    except (TypeError, ValueError):
        round_no = 0
    if not venue or not date_text or round_no <= 0:
        return {}
    url = OFFICIAL_TRIFECTA_ODDS_URL.format(
        rno=round_no,
        jcd=venue,
        hd=str(date_text).replace("-", ""),
    )
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept-Language": "ja,en-US;q=0.8,en;q=0.6",
        },
    )
    with urllib.request.urlopen(request, timeout=20, context=ssl._create_unverified_context()) as response:
        text = response.read().decode("utf-8", errors="replace")
    return parse_official_trifecta_odds(text)


def save_trifecta_odds_snapshot(race, odds, odds_db=None):
    if not odds:
        return None, None
    odds_path = Path(odds_db) if odds_db else default_trifecta_odds_db()
    odds_path.parent.mkdir(parents=True, exist_ok=True)
    date_text = (race or {}).get("date")
    venue = official_venue_code(race)
    try:
        round_no = int((race or {}).get("round") or (race or {}).get("round_no") or 0)
    except (TypeError, ValueError):
        round_no = 0
    if not date_text or not venue or round_no <= 0:
        return None, str(odds_path)
    snapshot = datetime.now().isoformat(timespec="seconds")
    try:
        with sqlite3.connect(odds_path) as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS odds_trifecta(
                  date TEXT, venue_code TEXT, race_no INTEGER, combo TEXT, odds REAL, snapshot_at TEXT);
                CREATE INDEX IF NOT EXISTS ix_ot ON odds_trifecta(date,venue_code,race_no);
                CREATE TABLE IF NOT EXISTS collect_log(
                  date TEXT, venue_code TEXT, race_no INTEGER, snapshot_at TEXT, n_odds INTEGER);
                """
            )
            con.executemany(
                """
                INSERT INTO odds_trifecta(date, venue_code, race_no, combo, odds, snapshot_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (date_text, venue, round_no, fmt_ticket(combo), float(value), snapshot)
                    for combo, value in odds.items()
                    if len(norm_combo(combo)) == 3 and as_num(value) is not None
                ],
            )
            con.execute(
                """
                INSERT INTO collect_log(date, venue_code, race_no, snapshot_at, n_odds)
                VALUES (?, ?, ?, ?, ?)
                """,
                (date_text, venue, round_no, snapshot, len(odds)),
            )
            con.commit()
    except sqlite3.Error:
        return None, str(odds_path)
    return snapshot, str(odds_path)


def load_latest_trifecta_odds(race, odds_db=None):
    odds_path = Path(odds_db) if odds_db else default_trifecta_odds_db()
    if not odds_path.exists():
        try:
            fetched = fetch_official_trifecta_odds(race)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            return {}, None, str(odds_path)
        snapshot, saved_path = save_trifecta_odds_snapshot(race, fetched, odds_path)
        return fetched, snapshot, saved_path
    date_text = (race or {}).get("date")
    try:
        round_no = int((race or {}).get("round") or (race or {}).get("round_no") or 0)
    except (TypeError, ValueError):
        round_no = 0
    if not date_text or round_no <= 0:
        return {}, None, str(odds_path)

    try:
        with sqlite3.connect(f"file:{odds_path}?mode=ro", uri=True) as con:
            for venue_code in venue_code_candidates(race):
                snapshot = con.execute(
                    """
                    SELECT MAX(snapshot_at)
                    FROM odds_trifecta
                    WHERE date = ? AND venue_code = ? AND race_no = ?
                    """,
                    (date_text, venue_code, round_no),
                ).fetchone()[0]
                if not snapshot:
                    continue
                rows = con.execute(
                    """
                    SELECT combo, odds
                    FROM odds_trifecta
                    WHERE date = ? AND venue_code = ? AND race_no = ? AND snapshot_at = ?
                    """,
                    (date_text, venue_code, round_no, snapshot),
                ).fetchall()
                odds = {}
                for combo, value in rows:
                    combo_key = norm_combo(combo)
                    number = as_num(value)
                    if len(combo_key) == 3 and number is not None and number > 0:
                        odds[combo_key] = number
                if odds:
                    return odds, snapshot, str(odds_path)
    except sqlite3.Error:
        return {}, None, str(odds_path)
    try:
        fetched = fetch_official_trifecta_odds(race)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return {}, None, str(odds_path)
    snapshot, saved_path = save_trifecta_odds_snapshot(race, fetched, odds_path)
    return fetched, snapshot, saved_path


def compact_boat_map(values, digits=0):
    parts = []
    for boat in range(1, 7):
        value = values.get(boat)
        if value is None:
            continue
        if digits <= 0:
            parts.append(f"{boat}:{int(value)}")
        else:
            parts.append(f"{boat}:{float(value):.{digits}f}")
    return ",".join(parts)


def official_trifecta_popularity_features(odds):
    valid = []
    for combo, value in (odds or {}).items():
        key = norm_combo(combo)
        number = as_num(value)
        if len(key) == 3 and number is not None and number > 0:
            valid.append((key, number))
    valid.sort(key=lambda item: item[1])
    if not valid:
        return {}, {}

    inverse_total = sum(1.0 / value for _, value in valid)
    head_inverse = {boat: 0.0 for boat in range(1, 7)}
    min_head_odds = {boat: None for boat in range(1, 7)}
    first_rank = {boat: None for boat in range(1, 7)}
    for rank, (combo, value) in enumerate(valid, start=1):
        head = int(combo[0])
        head_inverse[head] += 1.0 / value
        if min_head_odds[head] is None:
            min_head_odds[head] = value
            first_rank[head] = rank

    popularity = {
        boat: round((head_inverse[boat] / inverse_total * 100.0), 4) if inverse_total > 0 else None
        for boat in range(1, 7)
    }

    def head_counts(limit):
        counts = {boat: 0 for boat in range(1, 7)}
        for combo, _ in valid[:limit]:
            counts[int(combo[0])] += 1
        return counts

    top5 = valid[:5]
    top10 = valid[:10]
    top20 = valid[:20]
    top5_counts = head_counts(5)
    top10_counts = head_counts(10)
    top20_counts = head_counts(20)
    features = {
        "odds_snapshot_source": "official_trifecta_odds",
        "b1_trifecta_top5_1head": int(len(top5) == 5 and top5_counts.get(1, 0) == 5),
        "trifecta_top5_head1_count": int(top5_counts.get(1, 0)),
        "trifecta_top5_count": int(len(top5)),
        "trifecta_top10_head1_count": int(top10_counts.get(1, 0)),
        "trifecta_top10_count": int(len(top10)),
        "trifecta_top20_head1_count": int(top20_counts.get(1, 0)),
        "trifecta_top20_count": int(len(top20)),
        "trifecta_top10_head_counts": compact_boat_map(top10_counts),
        "trifecta_top20_head_counts": compact_boat_map(top20_counts),
        "trifecta_head_first_ranks": compact_boat_map(first_rank),
        "trifecta_head_min_odds": compact_boat_map(min_head_odds, digits=1),
        "trifecta_top1_odds": round(float(top5[0][1]), 1) if top5 else None,
        "trifecta_top5_avg_odds": round(sum(value for _, value in top5) / len(top5), 1) if top5 else None,
        "trifecta_top5_combos": " ".join(fmt_ticket(combo) for combo, _ in top5),
    }
    for boat in range(1, 7):
        features[f"b{boat}_trifecta_first_rank"] = first_rank.get(boat)
        features[f"b{boat}_trifecta_min_head_odds"] = min_head_odds.get(boat)
        features[f"b{boat}_trifecta_top10_head_count"] = int(top10_counts.get(boat, 0))
        features[f"b{boat}_trifecta_top20_head_count"] = int(top20_counts.get(boat, 0))
    return popularity, features


def seed_live_by_boat_from_race_metrics(race):
    metrics = (race or {}).get("metrics") or {}
    boats = metrics.get("boats") if isinstance(metrics.get("boats"), list) else []
    boats_by_number = {
        int(item.get("boat_number") or 0): item
        for item in boats
        if isinstance(item, dict)
    }
    by_boat = {}
    for boat in range(1, 7):
        item = boats_by_number.get(boat, {})
        by_boat[boat] = {
            "ai_prediction_pct": first_num(item.get("win_pct"), metrics.get(f"boat{boat}_ai_prediction_pct")),
            "ai_3ren_pct": first_num(item.get("top3_pct"), item.get("three_ren_pct"), metrics.get(f"boat{boat}_ai_3ren_pct")),
            "general_3ren_pct": first_num(item.get("general_top3_pct"), metrics.get(f"boat{boat}_general_3ren_pct")),
            "st_rank_general": first_num(item.get("st_rank_general"), metrics.get(f"boat{boat}_st_rank_general")),
            "st_time_avg_general": first_num(item.get("st_time_avg_general"), metrics.get(f"boat{boat}_st_time_avg_general")),
            "odds_prediction_pct": first_num(item.get("odds_prediction_pct"), metrics.get(f"boat{boat}_odds_prediction_pct")),
            "tenji_time": first_num(item.get("tenji_time"), metrics.get(f"boat{boat}_tenji_time")),
            "tenji_rank": first_num(item.get("tenji_rank"), item.get("tenji_time_rank"), metrics.get(f"boat{boat}_tenji_rank")),
            "isshu_time": first_num(item.get("isshu_time"), metrics.get(f"boat{boat}_isshu_time")),
            "chokusen_time": first_num(item.get("chokusen_time"), metrics.get(f"boat{boat}_chokusen_time")),
            "mawariashi_time": first_num(item.get("mawariashi_time"), metrics.get(f"boat{boat}_mawariashi_time")),
            "hanshu_time": first_num(item.get("hanshu_time"), metrics.get(f"boat{boat}_hanshu_time")),
            "start_tenji_time": first_num(item.get("start_tenji_time"), metrics.get(f"boat{boat}_start_tenji_time")),
            "start_tenji_rank": first_num(
                item.get("start_tenji_time_rank"),
                item.get("start_tenji_rank"),
                metrics.get(f"boat{boat}_start_tenji_time_rank"),
            ),
            "before_start_sinnyu": first_num(item.get("before_start_sinnyu"), metrics.get(f"boat{boat}_before_start_sinnyu")),
            "tilt": first_num(item.get("tilt"), metrics.get(f"boat{boat}_tilt")),
        }
        if boat == 1:
            by_boat[boat]["nige_pct"] = first_num(metrics.get("boat1_nige_pct"))
            if metrics.get("boat1_loss_pct") is not None:
                by_boat[boat]["sasare_pct"] = first_num(metrics.get("boat1_loss_pct"))
                by_boat[boat]["makurare_pct"] = 0.0
    return by_boat


def apply_official_beforeinfo_to_live_by_boat(by_boat, race):
    try:
        aux = fetch_official_beforeinfo(race)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
        return {
            "source": "official_boatrace",
            "available": False,
            "error": str(exc)[-300:],
            "url": official_beforeinfo_url(race),
        }
    boats = aux.get("boats") if isinstance(aux, dict) else {}
    if not isinstance(boats, dict):
        return {"source": "official_boatrace", "available": False, "error": "invalid official beforeinfo payload"}
    metrics = race.setdefault("metrics", {})
    for key in ("weather", "weather_degree", "wind_speed", "wind_direction", "water_degree", "wave_height"):
        value = aux.get(key)
        if value is not None and value != "":
            race[key] = value
            metrics[key] = value
    for boat in range(1, 7):
        data = boats.get(str(boat)) or {}
        target = by_boat.setdefault(boat, {})
        for target_key, source_key in [
            ("tenji_time", "tenji_time"),
            ("tenji_rank", "tenji_rank"),
            ("start_tenji_time", "start_tenji_time"),
            ("start_tenji_rank", "start_tenji_time_rank"),
            ("before_start_sinnyu", "before_start_sinnyu"),
            ("tilt", "tilt"),
        ]:
            value = as_num(data.get(source_key))
            if value is not None:
                target[target_key] = value
        target["official_data_source"] = "official_boatrace"
    return {
        "source": "official_boatrace",
        "available": bool(aux.get("tenji_boats") or aux.get("start_tenji_boats")),
        "url": aux.get("url") or official_beforeinfo_url(race),
        "fetched_at": aux.get("fetched_at") or "",
        "tenji_boats": int(aux.get("tenji_boats") or 0),
        "start_tenji_boats": int(aux.get("start_tenji_boats") or 0),
        "tilt_boats": int(aux.get("tilt_boats") or 0),
        "weather": aux.get("weather") or "",
        "weather_degree": aux.get("weather_degree"),
        "wind_speed": aux.get("wind_speed"),
        "wind_direction": aux.get("wind_direction") or "",
        "water_degree": aux.get("water_degree"),
        "wave_height": aux.get("wave_height"),
    }


def apply_boatcast_original_to_live_by_boat(by_boat, race, expected_boats=6):
    """Merge only complete BOATCAST original timing columns into live rows."""

    expected = int(as_num(expected_boats) or 6)
    if expected not in {5, 6}:
        expected = 6
    try:
        payload = boatcast_original.fetch_original_exhibition(race)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
        return {
            "source": boatcast_original.SOURCE,
            "available": False,
            "status": "fetch_error",
            "expected_boats": expected,
            "usable_fields": [],
            "error": str(exc)[-300:],
            "url": boatcast_original.original_exhibition_url(race),
        }

    boats = payload.get("boats") if isinstance(payload, dict) else {}
    if not isinstance(boats, dict):
        boats = {}
    usable = boatcast_original.usable_fields(payload, expected)
    for boat in range(1, 7):
        data = boats.get(str(boat)) or {}
        target = by_boat.setdefault(boat, {})
        for field in usable:
            value = as_num(data.get(field))
            if value is not None:
                target[field] = value
        if any(as_num(data.get(field)) is not None for field in usable):
            target["original_exhibition_source"] = boatcast_original.SOURCE
            target["original_exhibition_racer_name"] = data.get("racer_name") or ""

    return {
        "source": payload.get("source") or boatcast_original.SOURCE,
        "available": bool(payload.get("available")),
        "status": payload.get("status") or "pending",
        "status_code": int(payload.get("status_code") or 0),
        "expected_boats": expected,
        "boat_rows": int(payload.get("boat_rows") or 0),
        "isshu_boats": int(payload.get("isshu_boats") or 0),
        "hanshu_boats": int(payload.get("hanshu_boats") or 0),
        "mawariashi_boats": int(payload.get("mawariashi_boats") or 0),
        "chokusen_boats": int(payload.get("chokusen_boats") or 0),
        "headers": payload.get("headers") or [],
        "usable_fields": usable,
        "url": payload.get("url") or boatcast_original.original_exhibition_url(race),
        "fetched_at": payload.get("fetched_at") or "",
        "error": payload.get("error") or "",
    }


def apply_official_odds_to_live_by_boat(by_boat, race):
    odds, snapshot_at, odds_db_path = load_latest_trifecta_odds(race)
    popularity, features = official_trifecta_popularity_features(odds)
    metrics = race.setdefault("metrics", {})
    metrics.update(features)
    if snapshot_at:
        metrics["trifecta_odds_snapshot_at"] = snapshot_at
    for boat, pct in popularity.items():
        if pct is None:
            continue
        target = by_boat.setdefault(boat, {})
        target["odds_prediction_pct"] = pct
        target["official_odds_prediction_pct"] = pct
    return {
        "source": "official_trifecta_odds",
        "available": bool(odds),
        "odds_count": len(odds or {}),
        "snapshot_at": snapshot_at,
        "odds_db": odds_db_path,
    }


ADDITIONAL_LIVE_BOAT_FIELDS = (
    "reg_no",
    "racer_id",
    "racer_name",
    "racer_class",
    "branch",
    "hometown",
    "age",
    "weight",
    "flying_count",
    "late_count",
    "avg_st",
    "national_win_rate",
    "national_2ren_pct",
    "national_3ren_pct",
    "local_win_rate",
    "local_2ren_pct",
    "local_3ren_pct",
    "motor_number",
    "motor_2ren_pct",
    "motor_3ren_pct",
    "equipment_boat_number",
    "equipment_boat_2ren_pct",
    "equipment_boat_3ren_pct",
    "motor_2ren_vs_venue",
    "motor_3ren_vs_venue",
    "equipment_boat_2ren_vs_venue",
    "equipment_boat_3ren_vs_venue",
    "local_course_starts",
    "local_course_win_pct",
    "local_course_top3_pct",
    "local_course_avg_start_time",
    "national_course_starts",
    "national_course_win_pct",
    "national_course_top3_pct",
    "national_course_avg_start_time",
    "local_total_starts",
    "local_total_win_pct",
    "local_total_top3_pct",
    "local_total_avg_start_time",
    "national_total_starts",
    "national_total_win_pct",
    "national_total_top3_pct",
    "national_total_avg_start_time",
    "first_market_win_pct",
    "close_market_win_pct",
    "market_win_move_pct",
    "first_market_top3_pct",
    "close_market_top3_pct",
    "market_top3_move_pct",
    "close_min_head_odds",
    "close_top10_head_count",
    "trifecta_position1_pct",
    "trifecta_position2_pct",
    "trifecta_position3_pct",
    "trifecta_position_policy_id",
    "trifecta_position_model_summary",
    "venue_probability_position1_pct",
    "venue_probability_position2_pct",
    "venue_probability_position3_pct",
    "venue_probability_position1_adjustment_pp",
    "venue_probability_position2_adjustment_pp",
    "venue_probability_position3_adjustment_pp",
    "venue_probability_position1_factor_count",
    "venue_probability_position2_factor_count",
    "venue_probability_position3_factor_count",
    "venue_probability_policy_id",
    "venue_probability_overlay_summary",
)


def apply_additional_official_features_to_live_by_boat(by_boat, race):
    db_candidates = [PUBLIC_OUT / "boaters_all_races.sqlite", HISTORY_DB]
    db_path = next((path for path in db_candidates if path.exists()), None)
    if db_path is None:
        return {
            "source": "official_additional_feature_store",
            "available": False,
            "error": "main feature database not found",
        }
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            feature_rows, environment = additional_features.load_live_feature_rows(con, race)
        finally:
            con.close()
    except (sqlite3.Error, OSError, ValueError) as exc:
        return {
            "source": "official_additional_feature_store",
            "available": False,
            "db": str(db_path),
            "error": str(exc)[-300:],
        }

    for boat, data in feature_rows.items():
        target = by_boat.setdefault(int(boat), {})
        for field in ADDITIONAL_LIVE_BOAT_FIELDS:
            value = data.get(field)
            if value is not None and value != "":
                target[field] = value
        if not target.get("rank") and data.get("racer_class"):
            target["rank"] = data["racer_class"]
        if as_num(target.get("general_3ren_pct")) is None and as_num(data.get("national_3ren_pct")) is not None:
            target["general_3ren_pct"] = as_num(data.get("national_3ren_pct"))
            target["general_3ren_source"] = "official_national_3ren_pct"
        if as_num(target.get("st_time_avg_general")) is None and as_num(data.get("avg_st")) is not None:
            target["st_time_avg_general"] = as_num(data.get("avg_st"))
            target["st_time_avg_source"] = "official_racelist_avg_st"

    ranked_st = sorted(
        (
            (as_num(data.get("st_time_avg_general")), boat)
            for boat, data in by_boat.items()
            if as_num(data.get("st_time_avg_general")) is not None
        ),
        key=lambda item: (item[0], item[1]),
    )
    for rank, (_value, boat) in enumerate(ranked_st, 1):
        if as_num(by_boat[boat].get("st_rank_general")) is None:
            by_boat[boat]["st_rank_general"] = rank

    metrics = race.setdefault("metrics", {})
    environment_fields = (
        "tide_level_cm",
        "predicted_tide_cm",
        "predicted_tide_tp_cm",
        "observed_tide_cm",
        "observed_tide_tp_cm",
        "observed_age_minutes",
        "observed_lead_to_race_minutes",
        "observed_prediction_anomaly_cm",
        "tide_phase",
        "tide_delta_cm_per_hour",
        "daily_tide_range_cm",
        "tide_range_position",
        "minutes_to_next_extreme",
        "next_extreme_type",
        "water_applicability",
        "water_source_quality",
        "current_speed_mps",
        "current_signed_knots",
        "current_direction_deg",
        "current_direction_text",
        "current_quality",
        "air_pressure_hpa",
        "precipitation_mm",
    )
    for field in environment_fields:
        value = environment.get(field)
        if value is not None and value != "":
            race[field] = value
            metrics[field] = value

    return {
        "source": "official_additional_feature_store",
        "available": len(feature_rows) >= 5,
        "db": str(db_path),
        "boats_applied": len(feature_rows),
        "environment_available": bool(environment),
        "racelist_source": additional_features.SOURCE,
    }


def fetch_official_live_race(race):
    by_boat = seed_live_by_boat_from_race_metrics(race)
    beforeinfo_summary = apply_official_beforeinfo_to_live_by_boat(by_boat, race)
    additional_summary = apply_additional_official_features_to_live_by_boat(by_boat, race)
    expected_boats = int(beforeinfo_summary.get("tenji_boats") or 6)
    original_summary = apply_boatcast_original_to_live_by_boat(
        by_boat,
        race,
        expected_boats=expected_boats,
    )
    odds_summary = apply_official_odds_to_live_by_boat(by_boat, race)
    for boat in range(1, 7):
        by_boat.setdefault(boat, {})["live_source"] = "official"
    race["_official_live_summary"] = {
        "source": "official",
        "beforeinfo": beforeinfo_summary,
        "additional_features": additional_summary,
        "original_exhibition": original_summary,
        "odds": odds_summary,
    }
    return by_boat


def synthetic_odds_from_values(values):
    inverse_sum = 0.0
    count = 0
    for value in values:
        number = as_num(value)
        if number is None or number <= 0:
            continue
        inverse_sum += 1.0 / number
        count += 1
    if count <= 0 or inverse_sum <= 0:
        return None
    return round(1.0 / inverse_sum, 2)


def apply_synthetic_odds_filter(payload, odds_lookup, odds_snapshot_at=None, odds_db_path=None):
    tickets = [norm_combo(ticket) for ticket in payload.get("tickets") or []]
    tickets = [ticket for ticket in tickets if len(ticket) == 3]
    original_note = payload.get("odds_filter")
    if original_note and original_note != SYNTHETIC_ODDS_FILTER_LABEL:
        payload["odds_filter_note"] = original_note

    payload["odds_filter"] = SYNTHETIC_ODDS_FILTER_LABEL
    payload["synthetic_odds_min"] = SYNTHETIC_ODDS_MIN
    payload["synthetic_odds_ready"] = False
    payload["odds_filter_passed"] = True
    if odds_snapshot_at:
        payload["trifecta_odds_snapshot_at"] = odds_snapshot_at
    if odds_db_path:
        payload["trifecta_odds_db"] = odds_db_path
    if not tickets:
        payload["odds_filter"] = f"{SYNTHETIC_ODDS_FILTER_LABEL}（買い目なし）"
        return payload

    ticket_odds = {}
    missing = []
    for ticket in tickets:
        value = odds_lookup.get(ticket)
        if value is None:
            missing.append(fmt_ticket(ticket))
            continue
        ticket_odds[fmt_ticket(ticket)] = round(float(value), 1)

    if ticket_odds:
        payload["ticket_odds"] = ticket_odds
    if missing:
        payload["synthetic_odds_missing_count"] = len(missing)
        payload["odds_filter"] = f"{SYNTHETIC_ODDS_FILTER_LABEL}（実オッズ未取得）"
        return payload

    synthetic = synthetic_odds_from_values(ticket_odds.values())
    if synthetic is None:
        payload["odds_filter"] = f"{SYNTHETIC_ODDS_FILTER_LABEL}（実オッズ未取得）"
        return payload

    payload["synthetic_odds"] = synthetic
    payload["synthetic_odds_ready"] = True
    payload["odds_filter_passed"] = synthetic >= SYNTHETIC_ODDS_MIN
    status = "OK" if payload["odds_filter_passed"] else "見送り"
    payload["odds_filter"] = f"{SYNTHETIC_ODDS_FILTER_LABEL}（現在{synthetic:.2f}倍/{status}）"
    return payload


def selection_payload(rows, race=None, strategies=None):
    primary_strategy = next((strategy for strategy in (strategies or []) if strategy.get("tickets")), None)
    if primary_strategy:
        result = (race or {}).get("result") or {}
        trifecta = result.get("trifecta") or (race or {}).get("trifecta")
        tickets = {norm_combo(ticket) for ticket in primary_strategy.get("tickets") or []}
        tickets = {ticket for ticket in tickets if len(ticket) == 3}
        return {
            "version": "codex_roles_v2",
            "strategy_id": primary_strategy.get("strategy_id"),
            "label": primary_strategy.get("label") or "Codex候補",
            "heads": primary_strategy.get("heads") or [],
            "base_heads": primary_strategy.get("base_heads") or [],
            "head_rule": primary_strategy.get("head_rule"),
            "head_mode": primary_strategy.get("head_mode"),
            "head_scores": primary_strategy.get("head_scores") or {},
            "attackers": primary_strategy.get("attackers") or [],
            "attack_scores": primary_strategy.get("attack_scores") or {},
            "finishers": primary_strategy.get("finishers") or primary_strategy.get("heads") or [],
            "finisher_scores": primary_strategy.get("finisher_scores") or primary_strategy.get("head_scores") or {},
            "support_boats": primary_strategy.get("support_boats") or primary_strategy.get("supports") or [],
            "support_scores": primary_strategy.get("support_scores") or {},
            "role_split_note": primary_strategy.get("role_split_note"),
            "axes": primary_strategy.get("axes") or [],
            "axis_rule": primary_strategy.get("axis_rule"),
            "alt_axes": primary_strategy.get("alt_axes") or [],
            "alt_axis_rule": primary_strategy.get("alt_axis_rule"),
            "supports": primary_strategy.get("supports") or [],
            "keshi": primary_strategy.get("keshi"),
            "keshi_reason": primary_strategy.get("keshi_reason"),
            "ai_plus_rank6_boat": primary_strategy.get("ai_plus_rank6_boat"),
            "ai_plus_rank6_revival": primary_strategy.get("ai_plus_rank6_revival") or [],
            "points": len(tickets),
            "tickets": [fmt_ticket(ticket) for ticket in sorted(tickets)],
            "role_note": primary_strategy.get("role_note"),
            "entry_checks": primary_strategy.get("entry_checks") or [],
            "axis_hit": axis_hit(primary_strategy.get("axes"), trifecta),
            "alt_axis_hit": axis_hit(primary_strategy.get("alt_axes"), trifecta),
            "odds_filter": primary_strategy.get("odds_filter") or SYNTHETIC_ODDS_FILTER_LABEL,
            "odds_filter_note": primary_strategy.get("odds_filter_note"),
            "odds_filter_passed": primary_strategy.get("odds_filter_passed"),
            "synthetic_odds": primary_strategy.get("synthetic_odds"),
            "synthetic_odds_min": primary_strategy.get("synthetic_odds_min"),
            "synthetic_odds_ready": primary_strategy.get("synthetic_odds_ready"),
            "synthetic_odds_missing_count": primary_strategy.get("synthetic_odds_missing_count"),
            "ticket_odds": primary_strategy.get("ticket_odds") or {},
            "trifecta_odds_snapshot_at": primary_strategy.get("trifecta_odds_snapshot_at"),
            "source_strategy_ids": [s.get("strategy_id") for s in (strategies or [])],
        }
    tickets, roles = super_arunashi3(rows)
    if not tickets or roles is None:
        return {}
    result = (race or {}).get("result") or {}
    trifecta = result.get("trifecta") or (race or {}).get("trifecta")
    return {
        "version": "codex_roles_v2",
        "label": "Codex候補",
        "heads": roles["heads"],
        "head_rule": roles.get("head_rule"),
        "head_scores": roles.get("head_scores") or {},
        "attackers": roles.get("attackers") or [],
        "attack_scores": roles.get("attack_scores") or {},
        "finishers": roles.get("finishers") or roles.get("heads") or [],
        "finisher_scores": roles.get("finisher_scores") or roles.get("head_scores") or {},
        "support_boats": roles.get("support_boats") or roles.get("supports") or [],
        "support_scores": roles.get("support_scores") or {},
        "role_split_note": roles.get("role_split_note"),
        "axes": roles["axes"],
        "axis_rule": roles.get("axis_rule") or "AI3連対率の1位と3位",
        "alt_axes": roles.get("alt_axes") or [],
        "alt_axis_rule": "比較用: " + (roles.get("alt_axis_rule") or "AI3連対率の2位と3位"),
        "supports": roles.get("supports") or [],
        "keshi": roles.get("keshi"),
        "keshi_reason": roles.get("keshi_reason"),
        "ai_plus_rank6_boat": roles.get("ai_plus_rank6_boat"),
        "ai_plus_rank6_revival": roles.get("ai_plus_rank6_revival") or [],
        "points": len(tickets),
        "tickets": [fmt_ticket(ticket) for ticket in sorted(tickets)],
        "role_note": roles.get("role_note"),
        "axis_hit": axis_hit(roles.get("axes"), trifecta),
        "alt_axis_hit": axis_hit(roles.get("alt_axes"), trifecta),
        "odds_filter": SYNTHETIC_ODDS_FILTER_LABEL,
        "source_strategy_ids": [s.get("strategy_id") for s in (strategies or [])],
    }


def wakamatsu_mo12(rows):
    mid = order_mid(rows)
    outer = order_outer(rows)
    if len(mid) < 3 or not outer:
        return set(), None
    m1, m2, m3 = mid[:3]
    o1 = outer[0]
    o2 = outer[1] if len(outer) > 1 else None
    tickets = set()
    add_permuted(tickets, m1, [1, m2, m3])
    add_permuted(tickets, o1, [1, m1, m2])
    return tickets, {
        "heads": [m1, o1],
        "axes": [1, m2, m3],
        "keshi": o2,
        "role_note": f"{m1}頭は1,{m2},{m3} / {o1}頭は1,{m1},{m2}",
    }


def mid_heads_support_156(rows):
    mid = order_mid(rows)
    if len(mid) < 3:
        return set(), None
    m1, m2, m3 = mid[:3]
    tickets = set()
    add_permuted(tickets, m1, [1, 5, 6])
    add_permuted(tickets, m2, [1, 5, 6])
    return tickets, {
        "heads": [m1, m2],
        "axes": [1, 5, 6],
        "keshi": m3,
        "role_note": f"{m1},{m2}頭 / 2-3着は1,5,6",
    }


def mid_heads_outer_no1(rows):
    mid = order_mid(rows)
    if len(mid) < 2:
        return set(), None
    m1, m2 = mid[:2]
    tickets = set()
    add_permuted(tickets, m1, [5, 6, m2])
    add_permuted(tickets, m2, [5, 6, m1])
    return tickets, {
        "heads": [m1, m2],
        "axes": [5, 6, m1, m2],
        "keshi": 1,
        "role_note": f"1号艇を全消し / {m1},{m2}頭で5,6を厚め",
    }


def codex_logic29_outer_required(rows):
    heads = order_value(rows, pool={2, 3, 4, 5, 6})[:2]
    if len(heads) < 2:
        return set(), None

    exclude = set(heads)
    pool = unique([1, 5, 6] + order_comp(rows, exclude=exclude | {1, 5, 6})[:2])
    pool = [boat for boat in pool if boat not in exclude]
    candidates = unique([1] + pool)[:4]
    if len(candidates) < 3:
        return set(), None

    tickets = set()
    for head in heads:
        for second in candidates:
            for third in candidates:
                if second == third or not ({second, third} & {5, 6}):
                    continue
                if len({head, second, third}) == 3:
                    tickets.add(f"{head}{second}{third}")

    if not (10 <= len(tickets) <= 15):
        return set(), None
    return tickets, {
        "heads": heads,
        "axes": [1],
        "supports": candidates,
        "keshi": None,
        "role_note": f"{heads[0]},{heads[1]}頭 / 2-3着は{','.join(map(str, candidates))} / 5,6どちらか必須",
    }


def boat_score_live(row, mode):
    ai_pred = row.get("ai_prediction_pct") or 0
    ai_plus = row.get("ai_plus") or 0
    ai_rank = row.get("ai_plus_rank") or 6
    avgdiff = row.get("avg_isshu_diff")
    avgdiff = -0.5 if avgdiff is None else avgdiff
    tenji = row.get("tenji_rank") or row.get("tenji_time_rank") or 6
    isshu = row.get("isshu_rank") or 6
    st_rank = row.get("st_rank_general") or 6
    double_time = bool(row.get("double_time"))
    summer_bonus = row.get("summer_b1_score_bonus") or 0
    super_slit_bonus = row.get("super_slit_score_bonus") or 0
    matchup_bonus = row.get("matchup_score_bonus") or 0
    double_bonus = 0
    if double_time:
        boat = row.get("boat_number")
        if boat == 1:
            double_bonus = 8
        elif boat in {2, 3, 4}:
            double_bonus = 12
        elif boat == 5:
            double_bonus = 10
        elif boat == 6:
            double_bonus = 8
    if mode == "ai_pred":
        return (
            ai_pred
            + (double_bonus * 0.25)
            + (summer_bonus * 0.25)
            + (super_slit_bonus * 0.25)
            + (matchup_bonus * 0.22)
        )
    if mode == "ai_plus":
        return ai_plus + double_bonus + summer_bonus + super_slit_bonus + matchup_bonus
    if mode == "exhibit":
        return (
            avgdiff * 55
            + (7 - tenji) * 6
            + (7 - isshu) * 4
            + ai_pred * 0.25
            + double_bonus
            + summer_bonus
            + super_slit_bonus
            + matchup_bonus
        )
    if mode == "st_exhibit":
        return (
            (7 - st_rank) * 8
            + avgdiff * 40
            + (7 - tenji) * 5
            + ai_pred * 0.2
            + double_bonus
            + summer_bonus
            + super_slit_bonus
            + matchup_bonus
        )
    if mode == "worst_ai_plus":
        return -(
            ai_plus * 0.45
            + ai_pred * 0.35
            + avgdiff * 40
            + (7 - tenji) * 4
            + double_bonus
            + summer_bonus
            + super_slit_bonus
            + matchup_bonus
        )
    return 0


def top_boats_live(rows, pool, mode, n):
    pool = set(pool)
    selected = [row for row in rows if row["boat_number"] in pool]
    selected = sorted(
        selected,
        key=lambda row: (boat_score_live(row, mode), -row["boat_number"]),
        reverse=True,
    )
    return unique(row["boat_number"] for row in selected[:n])


def codex_stable_front_wind11(rows):
    kill = top_boats_live(rows, range(1, 7), "worst_ai_plus", 1)
    heads = [boat for boat in top_boats_live(rows, {3, 4, 5, 6}, "st_exhibit", 2) if boat not in kill]
    if len(heads) != 2:
        return set(), None

    second = [boat for boat in unique([5, 6] + top_boats_live(rows, {1, 2, 3, 4}, "ai_pred", 3)) if boat not in kill]
    third = [boat for boat in unique([1] + top_boats_live(rows, {2, 3, 4, 5, 6}, "ai_pred", 1)) if boat not in kill]
    tickets = set()
    for head in heads:
        for second_boat in second:
            for third_boat in third:
                if len({head, second_boat, third_boat}) == 3:
                    tickets.add(f"{head}{second_boat}{third_boat}")

    if not (10 <= len(tickets) <= 15):
        return set(), None
    return tickets, {
        "heads": heads,
        "axes": third,
        "supports": second,
        "keshi": kill[0] if kill else None,
        "role_note": f"{heads[0]},{heads[1]}頭 / 2着は5,6+AI予測上位 / 3着は1+AI予測上位 / 最弱AI+を消し",
    }


def codex_rank56_exhibit10(rows):
    kill = top_boats_live(rows, range(1, 7), "worst_ai_plus", 1)
    heads = [boat for boat in top_boats_live(rows, {3, 4, 5, 6}, "st_exhibit", 2) if boat not in kill]
    if len(heads) != 2:
        return set(), None

    second = [boat for boat in top_boats_live(rows, range(1, 7), "ai_pred", 4) if boat not in kill]
    third = [boat for boat in top_boats_live(rows, range(1, 7), "ai_plus", 2) if boat not in kill]
    tickets = set()
    for head in heads:
        for second_boat in second:
            for third_boat in third:
                if len({head, second_boat, third_boat}) == 3:
                    tickets.add(f"{head}{second_boat}{third_boat}")

    if not (10 <= len(tickets) <= 15):
        return set(), None
    return tickets, {
        "heads": heads,
        "axes": third,
        "supports": second,
        "keshi": kill[0] if kill else None,
        "role_note": f"{heads[0]},{heads[1]}頭 / 2着はAI予測上位4艇 / 3着はAI+上位2艇 / 最弱AI+を消し",
    }


def weather_value(race, key):
    value = as_num(race.get(key))
    if value is not None:
        return value
    metrics = race.get("metrics") or {}
    value = as_num(metrics.get(key))
    if value is not None:
        return value
    result = race.get("result") or {}
    return as_num(result.get(key))


def enrich_rows(by_boat, morning_metrics, date_text=None, place_name=None):
    rows = []
    use_half_lap = place_name in HALF_LAP_PLACE_NAMES
    for boat in range(1, 7):
        source = by_boat.get(boat, {})
        ai_3ren = as_num(source.get("ai_3ren_pct"))
        general = as_num(source.get("general_3ren_pct"))
        raw_isshu_time = as_num(source.get("isshu_time"))
        hanshu_time = as_num(source.get("hanshu_time"))
        effective_lap_time = raw_isshu_time
        lap_time_type = "1周" if raw_isshu_time is not None else ""
        if effective_lap_time is None and use_half_lap and hanshu_time is not None:
            effective_lap_time = hanshu_time
            lap_time_type = "半周"
        row = {
            "boat_number": boat,
            "_morning_metrics": morning_metrics,
            "ai_3ren_pct": ai_3ren,
            "general_3ren_pct": general,
            "st_rank_general": as_num(source.get("st_rank_general")),
            "ai_prediction_pct": as_num(source.get("ai_prediction_pct")),
            "odds_prediction_pct": as_num(source.get("odds_prediction_pct")),
            "tenji_time": as_num(source.get("tenji_time")),
            "raw_isshu_time": raw_isshu_time,
            "isshu_time": effective_lap_time,
            "lap_time_type": lap_time_type,
            "chokusen_time": as_num(source.get("chokusen_time")),
            "mawariashi_time": as_num(source.get("mawariashi_time")),
            "hanshu_time": hanshu_time,
            "original_exhibition_source": source.get("original_exhibition_source") or "",
            "original_exhibition_racer_name": source.get("original_exhibition_racer_name") or "",
            "start_tenji_time": as_num(source.get("start_tenji_time")),
            "start_tenji_rank": as_num(source.get("start_tenji_rank")),
            "before_start_sinnyu": as_num(source.get("before_start_sinnyu")),
            "tilt": as_num(source.get("tilt")),
            "nige_pct": as_num(source.get("nige_pct")),
            "sasare_pct": as_num(source.get("sasare_pct")),
            "makurare_pct": as_num(source.get("makurare_pct")),
        }
        row["rank"] = source.get("rank") or source.get("racer_class") or ""
        for field in ADDITIONAL_LIVE_BOAT_FIELDS:
            value = source.get(field)
            if value is not None and value != "":
                row[field] = value
        row["ai_plus"] = (
            row["ai_3ren_pct"] + row["general_3ren_pct"]
            if row["ai_3ren_pct"] is not None and row["general_3ren_pct"] is not None
            else None
        )
        matchup_label = str(morning_metrics.get(f"b{boat}_matchup_label") or "")
        row["matchup_label"] = matchup_label
        row["matchup_score_bonus"] = {
            "1号艇キラー": 12,
            "相性バフ": 10,
            "相性軸バフ": 7,
            "相性デバフ": -8,
        }.get(matchup_label, 0)
        if boat == 1 and morning_metrics.get("matchup_lane1_bad_flag"):
            row["matchup_score_bonus"] -= 6
        rows.append(row)

    isshu_values = [row["isshu_time"] for row in rows if row.get("isshu_time") is not None]
    avg_isshu = sum(isshu_values) / len(isshu_values) if isshu_values else None
    combo_values = [
        row["tenji_time"] + row["isshu_time"]
        for row in rows
        if row.get("tenji_time") is not None and row.get("isshu_time") is not None
    ]
    avg_combo = sum(combo_values) / len(combo_values) if combo_values else None
    avg_by_key = {}
    for key in ("tenji_time", "chokusen_time", "mawariashi_time", "start_tenji_time"):
        values = [row[key] for row in rows if row.get(key) is not None]
        avg_by_key[key] = sum(values) / len(values) if values else None
    for row in rows:
        row["isshu_avg_diff"] = (
            round(avg_isshu - row["isshu_time"], 4)
            if avg_isshu is not None and row.get("isshu_time") is not None
            else None
        )
        row["avg_isshu_diff"] = (
            round(avg_combo - (row["tenji_time"] + row["isshu_time"]), 4)
            if avg_combo is not None
            and row.get("tenji_time") is not None
            and row.get("isshu_time") is not None
            else None
        )
        row["avg_isshu_time"] = avg_isshu
        row["avg_exhibit_combo_time"] = avg_combo
        row["avg_tenji_diff"] = (
            round(avg_by_key["tenji_time"] - row["tenji_time"], 4)
            if avg_by_key.get("tenji_time") is not None and row.get("tenji_time") is not None
            else None
        )
        row["avg_chokusen_diff"] = (
            round(avg_by_key["chokusen_time"] - row["chokusen_time"], 4)
            if avg_by_key.get("chokusen_time") is not None and row.get("chokusen_time") is not None
            else None
        )
        row["avg_mawariashi_diff"] = (
            round(avg_by_key["mawariashi_time"] - row["mawariashi_time"], 4)
            if avg_by_key.get("mawariashi_time") is not None and row.get("mawariashi_time") is not None
            else None
        )
        row["avg_start_tenji_diff"] = (
            round(avg_by_key["start_tenji_time"] - row["start_tenji_time"], 4)
            if avg_by_key.get("start_tenji_time") is not None and row.get("start_tenji_time") is not None
            else None
        )

    if rows[0]["nige_pct"] is None:
        rows[0]["nige_pct"] = as_num(morning_metrics.get("boat1_nige_pct"))
    if rows[0]["sasare_pct"] is None or rows[0]["makurare_pct"] is None:
        loss = as_num(morning_metrics.get("boat1_loss_pct"))
        if loss is not None:
            rows[0]["sasare_pct"] = loss
            rows[0]["makurare_pct"] = 0.0

    rank_values(rows, "ai_prediction_pct", ascending=False)
    rank_values(rows, "odds_prediction_pct", ascending=False)
    rank_values(rows, "ai_3ren_pct", ascending=False)
    rank_values(rows, "ai_plus", ascending=False)
    rank_values(rows, "general_3ren_pct", ascending=False)
    rank_values(rows, "tenji_time", ascending=True)
    rank_values(rows, "isshu_time", ascending=True)
    rank_values(rows, "chokusen_time", ascending=True)
    rank_values(rows, "mawariashi_time", ascending=True)
    rank_values(rows, "start_tenji_time", ascending=True)
    apply_venue_exhibition_factors(rows, place_name)

    low_outer_boat = int(as_num(morning_metrics.get("low_outer_boat")) or 0)
    if low_outer_boat not in {5, 6}:
        low_outer_candidates = [
            row
            for row in rows
            if row["boat_number"] in {5, 6}
            and int(as_num(row.get("ai_plus_rank")) or 0) in {5, 6}
        ]
        low_outer_candidates.sort(key=lambda row: row.get("ai_plus_rank", 9), reverse=True)
        low_outer_boat = low_outer_candidates[0]["boat_number"] if low_outer_candidates else 0
    longshot_head_boats = {
        int(part)
        for part in str(morning_metrics.get("longshot_head_boats") or "").split(",")
        if part.isdigit()
    }

    by_number = {row["boat_number"]: row for row in rows}
    for boat in range(1, 7):
        row = by_number[boat]
        row["super_slit_alert"] = False
        row["super_slit_tenji_adv"] = None
        row["super_slit_st_rank_adv"] = None
        row["super_slit_score_bonus"] = 0
        row["super_slit_comp_bonus"] = 0.0
        row["super_slit_value_bonus"] = 0.0
        row["super_slit_effect_profile"] = {}
        row["super_slit_effect_multiplier"] = 1.0
        row["super_slit_win_lift_pp"] = None
        row["super_slit_top3_lift_pp"] = None
        if boat == 1:
            continue
        left = by_number[boat - 1]
        if (
            row.get("tenji_time") is not None
            and left.get("tenji_time") is not None
            and row.get("st_rank_general") is not None
            and left.get("st_rank_general") is not None
        ):
            row["super_slit_tenji_adv"] = round(left["tenji_time"] - row["tenji_time"], 3)
            row["super_slit_st_rank_adv"] = round(left["st_rank_general"] - row["st_rank_general"], 3)
            row["super_slit_alert"] = (
                row["super_slit_tenji_adv"] >= SUPER_SLIT_TENJI_ADV
                and row["super_slit_st_rank_adv"] > 0
            )
            if row["super_slit_alert"]:
                effect = super_slit_effect_for(place_name, boat)
                row["super_slit_effect_profile"] = effect
                row["super_slit_effect_multiplier"] = effect["multiplier"]
                row["super_slit_score_bonus"] = effect["score_bonus"]
                row["super_slit_comp_bonus"] = effect["comp_bonus"]
                row["super_slit_value_bonus"] = effect["value_bonus"]
                row["super_slit_win_lift_pp"] = effect["win_lift_pp"]
                row["super_slit_top3_lift_pp"] = effect["top3_lift_pp"]

    for row in rows:
        row["tenji_rank"] = row["tenji_time_rank"]
        row["isshu_rank"] = row["isshu_time_rank"]
        row["chokusen_rank"] = row["chokusen_time_rank"]
        row["mawariashi_rank"] = row["mawariashi_time_rank"]
        if row.get("start_tenji_rank") is None:
            row["start_tenji_rank"] = row["start_tenji_time_rank"]
        row["double_time"] = row["tenji_rank"] == 1 and row["isshu_rank"] == 1
        row["summer_b1_isshu_factor"] = ""
        row["summer_b1_nige_delta_pp"] = 0
        row["summer_b1_score_bonus"] = 0
        if row["boat_number"] == 1:
            summer_factor = summer_b1_isshu_factor(date_text, row["isshu_avg_diff"], len(isshu_values))
            row["summer_b1_isshu_factor"] = summer_factor["signal"]
            row["summer_b1_nige_delta_pp"] = summer_factor["nige_delta_pp"]
            row["summer_b1_score_bonus"] = summer_factor["score_bonus"]
        row["exhibit_rank"] = min(row["tenji_time_rank"], row["isshu_time_rank"])
        row["outer_good"] = int(row["boat_number"] in {5, 6} and row["exhibit_rank"] <= 2)
        row["low_outer_revive"] = False
        row["low_outer_score_bonus"] = 0.0
        row["longshot_head_candidate"] = row["boat_number"] in longshot_head_boats
        row["longshot_head_score_bonus"] = 0.75 if row["longshot_head_candidate"] else 0.0
        if row["boat_number"] == low_outer_boat:
            row["low_outer_revive"] = True
            if (
                (row.get("avg_isshu_diff") or -9) >= 0.10
                and row.get("exhibit_rank", 9) <= 2
                and (row.get("ai_prediction_pct") or -1) >= 8
            ):
                row["low_outer_score_bonus"] = 1.10
            elif (
                (row.get("avg_isshu_diff") or -9) >= 0.10
                and row.get("exhibit_rank", 9) <= 2
                and (row.get("ai_prediction_pct") or -1) >= 5
            ):
                row["low_outer_score_bonus"] = 0.85
            elif row.get("exhibit_rank", 9) <= 2:
                row["low_outer_score_bonus"] = 0.55
        st_rank = row["st_rank_general"] if row["st_rank_general"] is not None else 4
        double_score = 0
        if row["double_time"]:
            if row["boat_number"] == 1:
                double_score = 0.30
            elif row["boat_number"] in {2, 3, 4}:
                double_score = 0.90
            elif row["boat_number"] == 5:
                double_score = 0.80
            elif row["boat_number"] == 6:
                double_score = 0.65
        summer_score = 0
        if row["boat_number"] == 1:
            if row["summer_b1_isshu_factor"] == "fast_hold":
                summer_score = 0.90
            elif row["summer_b1_isshu_factor"] == "slow_fly":
                summer_score = -1.00
        super_slit_score = row.get("super_slit_comp_bonus") or 0
        matchup_score = 0
        if row["matchup_label"] == "1号艇キラー":
            matchup_score = 0.90
        elif row["matchup_label"] == "相性バフ":
            matchup_score = 0.75
        elif row["matchup_label"] == "相性軸バフ":
            matchup_score = 0.55
        elif row["matchup_label"] == "相性デバフ":
            matchup_score = -0.70
        if row["boat_number"] == 1 and morning_metrics.get("matchup_lane1_bad_flag"):
            matchup_score -= 0.45
        extra_exhibition_score = 0
        if row.get("mawariashi_rank", 9) <= 2 and row["boat_number"] in {5, 6}:
            extra_exhibition_score += 0.12
        if row.get("mawariashi_rank", 9) >= 5 and row["boat_number"] == 1:
            extra_exhibition_score -= 0.14
        if row.get("tilt") is not None and row.get("tilt") >= 0.5 and row["boat_number"] in {5, 6}:
            extra_exhibition_score += 0.08
        row.setdefault("venue_score_bonus", 0.0)
        row["comp_score"] = (
            row["ai_prediction_pct_rank"] * 0.34
            + row["ai_plus_rank"] * 0.30
            + row["general_3ren_pct_rank"] * 0.12
            + row["exhibit_rank"] * 0.18
            + st_rank * 0.06
            - double_score
            - summer_score
            - super_slit_score
            - matchup_score
            - extra_exhibition_score
            - row["venue_score_bonus"]
            - row["low_outer_score_bonus"]
            - row["longshot_head_score_bonus"]
        )
        row["value_score"] = (
            row["comp_score"]
            - (0.45 if row["boat_number"] in {4, 5, 6} else 0)
            - (0.70 if row["outer_good"] else 0)
            - (0.30 if row["double_time"] and row["boat_number"] in {5, 6} else 0)
            - (row.get("super_slit_value_bonus") or 0)
            - (0.35 if row["matchup_label"] in {"1号艇キラー", "相性バフ"} else 0)
            - (0.18 if row.get("mawariashi_rank", 9) <= 2 and row["boat_number"] in {5, 6} else 0)
            - (0.12 if row.get("tilt") is not None and row.get("tilt") >= 0.5 and row["boat_number"] in {5, 6} else 0)
            - row["venue_score_bonus"]
            - (0.25 if row["low_outer_revive"] else 0)
            - (0.25 if row["longshot_head_candidate"] else 0)
        )
    return rows


def slit_rank_metrics(rows):
    by_boat = {row["boat_number"]: row for row in rows}

    def rank(boat, default=9):
        value = by_boat.get(boat, {}).get("st_rank_general")
        return default if value is None else float(value)

    b1 = rank(1)
    b2 = rank(2)
    b3 = rank(3)
    b4 = rank(4)
    b5 = rank(5)
    b6 = rank(6)
    b1_front_wall = b1 <= 2 and b2 <= 3 and b3 >= 3
    b1_hole_vs_23 = b1 >= 4 and min(b2, b3) <= 2
    b2_wall_break_3peek = b3 <= 2 and (b2 - b3) >= 1
    b3_peek_vs_12 = b3 <= 2 and b3 < min(b1, b2)
    b4_cadou_peek = b4 <= 2 and b4 < min(b1, b2, b3)
    outer456_pressure = min(b4, b5, b6) < min(b1, b2, b3)
    outer56_pressure_vs_1 = min(b5, b6) < b1
    b5_left_adv = b5 < b4
    b6_left_adv = b6 < b5
    center34_dent = b3 >= 4 and b4 >= 4 and min(b1, b2, b5, b6) <= 2
    slit_dekoboko = max(b1, b2, b3, b4, b5, b6) - min(b1, b2, b3, b4, b5, b6) >= 4
    if b1_front_wall:
        label = "1前+2壁"
    elif b2_wall_break_3peek:
        label = "2壁割れ3覗き"
    elif b1_hole_vs_23 and outer456_pressure:
        label = "1凹み+外圧"
    elif b1_hole_vs_23:
        label = "1凹み"
    elif b4_cadou_peek:
        label = "4カド覗き"
    elif b3_peek_vs_12:
        label = "3覗き"
    elif outer456_pressure:
        label = "外圧"
    elif center34_dent:
        label = "3/4中凹み"
    elif slit_dekoboko:
        label = "デコボコ"
    else:
        label = ""
    return {
        "slit_shape_label": label,
        "slit_b1_front_wall": b1_front_wall,
        "slit_b1_hole_vs_23": b1_hole_vs_23,
        "slit_b2_wall_break_3peek": b2_wall_break_3peek,
        "slit_b3_peek_vs_12": b3_peek_vs_12,
        "slit_b4_cadou_peek": b4_cadou_peek,
        "slit_outer456_pressure": outer456_pressure,
        "slit_outer56_pressure_vs_1": outer56_pressure_vs_1,
        "slit_b5_left_adv": b5_left_adv,
        "slit_b6_left_adv": b6_left_adv,
        "slit_center34_dent": center34_dent,
        "slit_dekoboko": slit_dekoboko,
    }


def verified_popular_b1_exhibition_conditions(metrics, round_no):
    """検証済みの「人気1号艇＋展示悪化＋外枠上振れ」条件を返す。"""

    b1_nige = as_num(metrics.get("boat1_nige_pct"))
    b1_avg = as_num(metrics.get("boat1_avg_isshu_diff"))
    b1_tenji_rank = as_num(metrics.get("boat1_tenji_time_rank") or metrics.get("boat1_tenji_rank"))
    outer56_avg = as_num(metrics.get("outer56_best_avg_isshu_diff"))
    outer56_ai = as_num(metrics.get("outer56_best_ai_prediction_pct"))
    outer56_exhibit_top2 = int(as_num(metrics.get("outer56_exhibit_top2_count")) or 0)
    ai_rank6_tenji = as_num(metrics.get("ai_rank6_tenji_rank"))
    ai_rank6_isshu = as_num(metrics.get("ai_rank6_isshu_rank"))
    ai_rank5_tenji = as_num(metrics.get("ai_rank5_tenji_rank"))
    ai_rank5_isshu = as_num(metrics.get("ai_rank5_isshu_rank"))
    rank6_exhibit_top2 = (ai_rank6_tenji is not None and ai_rank6_tenji <= 2) or (
        ai_rank6_isshu is not None and ai_rank6_isshu <= 2
    )
    rank5_exhibit_top2 = (ai_rank5_tenji is not None and ai_rank5_tenji <= 2) or (
        ai_rank5_isshu is not None and ai_rank5_isshu <= 2
    )
    early = round_no is not None and round_no <= 6
    definitions = [
        {
            "id": "codex_popular_b1_verified_a_nige50_avg015_outertop2_early",
            "label": "検証済みA: 人気1号艇でも逃げ率50%未満、1の平均との差+0.15以下、5/6展示上位、1〜6R",
            "matched": b1_nige is not None
            and b1_nige < 50
            and b1_avg is not None
            and b1_avg <= 0.15
            and outer56_exhibit_top2 >= 1
            and early,
            "sample_races": 21,
            "b1_not_win_rate_pct": 71.43,
            "b1_top3_miss_rate_pct": 28.57,
            "manshu_rate_pct": 28.57,
        },
        {
            "id": "codex_popular_b1_verified_b_avg030_outerai10_early",
            "label": "検証済みB: 人気1号艇でも1の平均との差+0.30以下、5/6AI1着10%以上、1〜6R",
            "matched": b1_avg is not None and b1_avg <= 0.30 and outer56_ai is not None and outer56_ai >= 10 and early,
            "sample_races": 23,
            "b1_not_win_rate_pct": 69.57,
            "b1_top3_miss_rate_pct": 30.43,
            "manshu_rate_pct": 30.43,
        },
        {
            "id": "codex_popular_b1_verified_c_b1bad_rank6revive_early",
            "label": "検証済みC: 人気1号艇でも1の平均との差+0.30以下、展示4位以下、5/6上振れ、AI+6位展示上位、1〜6R",
            "matched": b1_avg is not None
            and b1_avg <= 0.30
            and b1_tenji_rank is not None
            and b1_tenji_rank >= 4
            and outer56_avg is not None
            and outer56_avg >= 0.10
            and rank6_exhibit_top2
            and early,
            "sample_races": 21,
            "b1_not_win_rate_pct": 66.67,
            "b1_top3_miss_rate_pct": 42.86,
            "manshu_rate_pct": 33.33,
        },
        {
            "id": "codex_popular_b1_verified_d_b1bad_rank5revive_early",
            "label": "検証済みD: 人気1号艇でも1の平均との差+0.15以下、展示4位以下、5/6上振れ、AI+5位展示上位、1〜6R",
            "matched": b1_avg is not None
            and b1_avg <= 0.15
            and b1_tenji_rank is not None
            and b1_tenji_rank >= 4
            and outer56_avg is not None
            and outer56_avg >= 0.05
            and rank5_exhibit_top2
            and early,
            "sample_races": 20,
            "b1_not_win_rate_pct": 65.00,
            "b1_top3_miss_rate_pct": 35.00,
            "manshu_rate_pct": 35.00,
        },
    ]
    return [{key: value for key, value in item.items() if key != "matched"} for item in definitions if item["matched"]]


def dominant_b1_hold_guard_from_values(
    b1_ai_prediction_pct,
    b1_ai_plus,
    b1_odds_prediction_pct,
    b1_odds_rank,
    b1_isshu_avg_diff=None,
    outer56_best_avg_isshu_diff=None,
    outer56_exhibit_top2_count=0,
):
    """検証済みの「1号艇が強すぎる時は外枠上振れを買い材料にしすぎない」ガード。"""

    if not (
        b1_ai_prediction_pct is not None
        and b1_ai_prediction_pct >= 70
        and b1_ai_plus is not None
        and b1_ai_plus >= 180
        and b1_odds_prediction_pct is not None
        and b1_odds_prediction_pct >= 60
        and int(b1_odds_rank or 9) == 1
    ):
        return None

    apparent_slow_lap = b1_isshu_avg_diff is not None and b1_isshu_avg_diff <= -0.10
    apparent_outer_flash = (
        outer56_best_avg_isshu_diff is not None and outer56_best_avg_isshu_diff >= 0.14
    ) or int(outer56_exhibit_top2_count or 0) >= 1
    if apparent_slow_lap and apparent_outer_flash:
        return {
            "id": "codex_dominant_b1_hold_guard_slow_outer",
            "label": "強い1号艇ガード: 1周遅れ+5/6展示上振れでもAI/オッズで1号艇が圧倒的",
            "sample_races": 352,
            "b1_win_rate_pct": 76.70,
            "b1_not_win_rate_pct": 23.30,
            "b1_top3_miss_rate_pct": 5.40,
            "manshu_rate_pct": 13.07,
            "over5000_rate_pct": 21.59,
            "median_payout_yen": 1470,
        }
    return {
        "id": "codex_dominant_b1_hold_guard",
        "label": "強い1号艇ガード: AI/オッズで1号艇が圧倒的",
        "sample_races": 9719,
        "b1_win_rate_pct": 80.75,
        "b1_not_win_rate_pct": 19.25,
        "b1_top3_miss_rate_pct": 4.67,
        "manshu_rate_pct": 11.23,
        "over5000_rate_pct": 18.94,
        "median_payout_yen": 1350,
    }


def race_metrics(rows, date_text=None, round_no=None):
    morning_metrics = rows[0].get("_morning_metrics") or {}
    official_aux = rows[0].get("_official_aux_summary") or {}
    b1 = next(row for row in rows if row["boat_number"] == 1)
    outer = [row for row in rows if row["boat_number"] in {5, 6}]
    outer46 = [row for row in rows if row["boat_number"] in {4, 5, 6}]
    b1_loss = None
    if b1.get("sasare_pct") is not None and b1.get("makurare_pct") is not None:
        b1_loss = b1["sasare_pct"] + b1["makurare_pct"]
    outer_tenji = [row["tenji_time"] for row in outer if row.get("tenji_time") is not None]
    outer_isshu = [row["isshu_time"] for row in outer if row.get("isshu_time") is not None]
    outer_avgdiff = [row["avg_isshu_diff"] for row in outer if row.get("avg_isshu_diff") is not None]
    outer_ai_pred = [row["ai_prediction_pct"] for row in outer if row.get("ai_prediction_pct") is not None]
    outer_ai_plus = [row["ai_plus"] for row in outer if row.get("ai_plus") is not None]
    outer56_best_tenji = min(outer_tenji) if outer_tenji else None
    outer56_best_isshu = min(outer_isshu) if outer_isshu else None
    outer56_best_avgdiff = max(outer_avgdiff) if outer_avgdiff else None
    b1_tenji = b1.get("tenji_time")
    b1_isshu = b1.get("isshu_time")
    rank6 = next((row for row in rows if row.get("ai_plus_rank") == 6), {})
    rank5 = next((row for row in rows if row.get("ai_plus_rank") == 5), {})
    low_outer_boat = int(as_num(morning_metrics.get("low_outer_boat")) or 0)
    if low_outer_boat not in {5, 6}:
        if rank6.get("boat_number") in {5, 6}:
            low_outer_boat = rank6.get("boat_number")
        elif rank5.get("boat_number") in {5, 6}:
            low_outer_boat = rank5.get("boat_number")
    low_outer = next((row for row in rows if row.get("boat_number") == low_outer_boat), {})
    double_time_boats = [row["boat_number"] for row in rows if row.get("double_time")]
    super_slit_boats = [row["boat_number"] for row in rows if row.get("super_slit_alert")]
    super_slit_effects = [
        {
            "boat_number": row["boat_number"],
            "effect_multiplier": row.get("super_slit_effect_multiplier"),
            "score_bonus": row.get("super_slit_score_bonus"),
            "comp_bonus": row.get("super_slit_comp_bonus"),
            "value_bonus": row.get("super_slit_value_bonus"),
            "win_lift_pp": row.get("super_slit_win_lift_pp"),
            "top3_lift_pp": row.get("super_slit_top3_lift_pp"),
        }
        for row in rows
        if row.get("super_slit_alert")
    ]
    isshu_boats = sum(1 for row in rows if row.get("isshu_time") is not None)
    raw_isshu_boats = sum(1 for row in rows if row.get("raw_isshu_time") is not None)
    hanshu_boats = sum(1 for row in rows if row.get("hanshu_time") is not None)
    lap_time_type = "半周" if any(row.get("lap_time_type") == "半周" for row in rows) else ("1周" if isshu_boats else "")
    b1_extra_bad_count = sum(
        1
        for key in ("start_tenji_time_rank", "mawariashi_rank", "chokusen_rank")
        if b1.get(key) is not None and b1.get(key, 9) >= 4
    )
    outer56_chokusen_top2_count = sum(1 for row in outer if row.get("chokusen_rank", 9) <= 2)
    outer56_mawariashi_top2_count = sum(1 for row in outer if row.get("mawariashi_rank", 9) <= 2)
    outer56_start_tenji_top2_count = sum(1 for row in outer if row.get("start_tenji_time_rank", 9) <= 2)
    outer46_chokusen_top2_count = sum(1 for row in outer46 if row.get("chokusen_rank", 9) <= 2)
    outer46_mawariashi_top2_count = sum(1 for row in outer46 if row.get("mawariashi_rank", 9) <= 2)
    outer46_start_tenji_top2_count = sum(1 for row in outer46 if row.get("start_tenji_time_rank", 9) <= 2)
    outer56_extra_top2_count = sum(
        1
        for count in (outer56_chokusen_top2_count, outer56_mawariashi_top2_count, outer56_start_tenji_top2_count)
        if count >= 1
    )
    outer46_extra_top2_count = sum(
        1
        for count in (outer46_chokusen_top2_count, outer46_mawariashi_top2_count, outer46_start_tenji_top2_count)
        if count >= 1
    )
    outer56_tilt_plus_count = sum(
        1 for row in outer if row.get("tilt") is not None and row.get("tilt") >= 0.5
    )
    summer_factor = summer_b1_isshu_factor(date_text, b1.get("isshu_avg_diff"), isshu_boats)
    slit_metrics = slit_rank_metrics(rows)
    outer56_exhibit_top2_count = sum(1 for row in outer if row.get("exhibit_rank", 9) <= 2)
    live_odds_context = {}
    live_odds_boats = {}
    for row in rows:
        boat = row["boat_number"]
        odds_pct = as_num(row.get("odds_prediction_pct"))
        odds_rank = as_num(row.get("odds_prediction_pct_rank"))
        if odds_pct is None:
            continue
        live_odds_context[f"boat{boat}_odds_prediction_pct"] = odds_pct
        live_odds_context[f"boat{boat}_odds_rank"] = odds_rank
        live_odds_boats[str(boat)] = {
            "odds_prediction_pct": odds_pct,
            "odds_prediction_rank": odds_rank,
        }
    boat1_odds_pct = (
        as_num(b1.get("odds_prediction_pct"))
        if as_num(b1.get("odds_prediction_pct")) is not None
        else as_num(morning_metrics.get("boat1_odds_prediction_pct"))
    )
    boat1_odds_rank = (
        as_num(b1.get("odds_prediction_pct_rank"))
        if as_num(b1.get("odds_prediction_pct_rank")) is not None
        else as_num(morning_metrics.get("boat1_odds_rank"))
    )
    live_odds_context["boat1_odds_prediction_pct"] = boat1_odds_pct
    live_odds_context["boat1_odds_rank"] = boat1_odds_rank
    live_odds_context["odds_snapshot_source"] = (
        "boaters_after_exhibition" if live_odds_boats else morning_metrics.get("odds_snapshot_source") or "morning_saved"
    )
    if live_odds_boats:
        live_odds_context["odds_boats"] = live_odds_boats
    if live_odds_boats and boat1_odds_pct is not None:
        boat1_odds_rank_int = int(boat1_odds_rank or 9)
        popularity_context = b1_popularity_context_from_values(
            odds_pct=boat1_odds_pct,
            odds_rank=boat1_odds_rank_int,
            trifecta_top5_count=morning_metrics.get("trifecta_top5_count"),
            trifecta_head1_count=morning_metrics.get("trifecta_top5_head1_count"),
            trifecta_head1_flag=morning_metrics.get("b1_trifecta_top5_1head"),
            trifecta_top10_count=morning_metrics.get("trifecta_top10_count"),
            trifecta_top10_head1_count=morning_metrics.get("trifecta_top10_head1_count"),
            b1_trifecta_first_rank=morning_metrics.get("b1_trifecta_first_rank"),
        )
        popularity_level = popularity_context["level"]
        if popularity_level in B1_POPULARITY_BUY_LEVELS:
            level_bonus = {"普通に人気": 0, "かなり人気": 6, "売れすぎ": 11}.get(popularity_level, 0)
            popular_score = 35 + max(0, boat1_odds_pct - 40) * 1.2 + level_bonus
            popular_reasons = [f"1号艇は{popularity_level}（展示後オッズ評価1位{boat1_odds_pct:.1f}%）"]
            dominant_guard = dominant_b1_hold_guard_from_values(
                as_num(b1.get("ai_prediction_pct")),
                as_num(b1.get("ai_plus")),
                boat1_odds_pct,
                boat1_odds_rank_int,
                b1.get("isshu_avg_diff"),
                outer56_best_avgdiff,
                outer56_exhibit_top2_count,
            )
            if b1_loss is not None and b1_loss >= 45:
                popular_score += 13 if b1_loss < 55 else 20
                popular_reasons.append(f"逃げ失敗率{b1_loss:.1f}%")
            if b1.get("avg_isshu_diff") is not None and b1.get("avg_isshu_diff") <= 0:
                popular_score += 10 if b1.get("avg_isshu_diff") > -0.10 else 16
                popular_reasons.append(f"展示+1周平均との差{b1.get('avg_isshu_diff'):.2f}")
            if outer56_best_avgdiff is not None and outer56_best_avgdiff >= 0.10:
                popular_score += 8
                popular_reasons.append(f"5/6号艇の展示+1周平均との差+{outer56_best_avgdiff:.2f}")
            if slit_metrics.get("slit_outer56_pressure_vs_1") or slit_metrics.get("slit_b1_hole_vs_23"):
                popular_score += 8
                popular_reasons.append("スリットで1号艇に外圧")
            if summer_factor["signal"] == "slow_fly":
                popular_score += 9
                popular_reasons.append("夏場1周が悪い")
            if b1.get("venue_b1_head_debuff"):
                popular_score += 7
                popular_reasons.append("場別展示S/Aで1号艇頭デバフ")
            if b1.get("venue_b1_fly_manshu_watch"):
                popular_score += 8
                popular_reasons.append("場別展示S/Aで1号艇飛び万舟注意")
            verified_metrics = {
                "boat1_nige_pct": b1.get("nige_pct"),
                "boat1_avg_isshu_diff": b1.get("avg_isshu_diff"),
                "boat1_tenji_rank": b1.get("tenji_rank"),
                "boat1_tenji_time_rank": b1.get("tenji_time_rank"),
                "outer56_best_avg_isshu_diff": outer56_best_avgdiff,
                "outer56_best_ai_prediction_pct": max(outer_ai_pred) if outer_ai_pred else None,
                "outer56_exhibit_top2_count": outer56_exhibit_top2_count,
                "ai_rank6_tenji_rank": rank6.get("tenji_rank"),
                "ai_rank6_isshu_rank": rank6.get("isshu_rank"),
                "ai_rank5_tenji_rank": rank5.get("tenji_rank"),
                "ai_rank5_isshu_rank": rank5.get("isshu_rank"),
            }
            verified_conditions = verified_popular_b1_exhibition_conditions(verified_metrics, int(as_num(round_no) or 0) or None)
            if verified_conditions:
                popular_score += 15
                best_verified = max(verified_conditions, key=lambda item: item.get("b1_not_win_rate_pct") or 0)
                popular_reasons.append(
                    f"検証済み同型条件に一致（1着外{best_verified.get('b1_not_win_rate_pct'):.1f}%）"
                )
            morning_conditions = morning_metrics.get("popular_b1_matched_conditions") or []
            matched_by_key = {}
            for item in list(morning_conditions) + verified_conditions:
                if isinstance(item, dict):
                    stats_key = (
                        item.get("sample_races"),
                        item.get("b1_not_win_rate_pct"),
                        item.get("b1_top3_miss_rate_pct"),
                        item.get("manshu_rate_pct"),
                    )
                    if stats_key == (None, None, None, None):
                        stats_key = (item.get("id") or item.get("label") or str(len(matched_by_key)),)
                    existing = matched_by_key.get(stats_key)
                    if existing is None or str(item.get("id") or "").startswith("codex_popular_b1_verified"):
                        matched_by_key[stats_key] = item
            matched_conditions = sorted(
                matched_by_key.values(),
                key=lambda item: (
                    item.get("b1_not_win_rate_pct") or 0,
                    item.get("manshu_rate_pct") or 0,
                    item.get("sample_races") or 0,
                ),
                reverse=True,
            )
            popular_score = max(popular_score, as_num(morning_metrics.get("popular_b1_fly_score")) or 0)
            if dominant_guard:
                popular_score = min(popular_score, 44.0)
                popular_reasons.append("強い1号艇ガードで危険判定を抑制")
            popular_score = round(bounded(popular_score, 0, 100), 1)
            if popular_score >= 75:
                popular_level = "超危険"
            elif popular_score >= 60:
                popular_level = "危険"
            elif popular_score >= 45:
                popular_level = "注意"
            else:
                popular_level = "人気だが鉄板寄り"
            if dominant_guard:
                not_win_rate = dominant_guard["b1_not_win_rate_pct"]
                top3_miss_rate = dominant_guard["b1_top3_miss_rate_pct"]
                manshu_rate = dominant_guard["manshu_rate_pct"]
                rate_source = "強い1号艇ガードの長期検証"
                matched_conditions = [dominant_guard]
            elif matched_conditions:
                not_win_rate = max((item.get("b1_not_win_rate_pct") or 0 for item in matched_conditions), default=0) or None
                top3_miss_rate = max((item.get("b1_top3_miss_rate_pct") or 0 for item in matched_conditions), default=0) or None
                manshu_rate = max((item.get("manshu_rate_pct") or 0 for item in matched_conditions), default=0) or None
                rate_source = "展示後の検証済み同型条件"
            else:
                not_win_rate = round(bounded(31.87 + (popular_score - 45) * 0.62, 31.87, 72.0), 2)
                top3_miss_rate = round(bounded(10.28 + (popular_score - 45) * 0.36, 10.28, 43.0), 2)
                manshu_rate = round(bounded(16.6 + (popular_score - 45) * 0.25, 16.6, 36.0), 2)
                rate_source = "展示後オッズ評価+直前展示からの目安"
            live_odds_context.update(
                {
                    "popular_b1_is_popular": True,
                    "popular_b1_source": "展示後BOATERSオッズ評価",
                    "popular_b1_popularity_level": popularity_level,
                    "popular_b1_popularity_source": popularity_context.get("source"),
                    "b1_popularity_level": popularity_level,
                    "b1_popularity_source": popularity_context.get("source"),
                    "popular_b1_fly_score": popular_score,
                    "popular_b1_fly_level": popular_level,
                    "popular_b1_not_win_rate_pct": round(not_win_rate, 2) if not_win_rate is not None else None,
                    "popular_b1_top3_miss_rate_pct": round(top3_miss_rate, 2) if top3_miss_rate is not None else None,
                    "popular_b1_manshu_rate_pct": round(manshu_rate, 2) if manshu_rate is not None else None,
                    "popular_b1_rate_source": rate_source,
                    "popular_b1_reasons": popular_reasons[:7],
                    "popular_b1_matched_conditions": matched_conditions[:3],
                    "dominant_b1_hold_guard": bool(dominant_guard),
                    "dominant_b1_hold_guard_stats": dominant_guard or {},
                }
            )
        else:
            live_odds_context.update(
                {
                    "popular_b1_is_popular": False,
                    "popular_b1_source": "展示後BOATERSオッズ評価",
                    "popular_b1_popularity_level": popularity_level,
                    "popular_b1_popularity_source": popularity_context.get("source"),
                    "b1_popularity_level": popularity_level,
                    "b1_popularity_source": popularity_context.get("source"),
                    "popular_b1_fly_score": 0,
                    "popular_b1_fly_level": "人気不足",
                    "popular_b1_not_win_rate_pct": None,
                    "popular_b1_top3_miss_rate_pct": None,
                    "popular_b1_manshu_rate_pct": None,
                    "popular_b1_rate_source": "展示後オッズ評価で人気不足",
                    "popular_b1_reasons": [f"1号艇は{popularity_level}（展示後オッズ評価{fmt_pct(boat1_odds_pct)}・{boat1_odds_rank_int}位）なので、人気イン飛び狙いの主役ではない"],
                    "popular_b1_matched_conditions": [],
                }
            )
    for row in rows:
        row["_morning_metrics"] = {**morning_metrics, **live_odds_context}
    morning_metrics = rows[0].get("_morning_metrics") or {}
    compute_composite_boat_rates(rows)
    _, selection_roles = super_arunashi3(rows)
    boats = []
    for row in sorted(rows, key=lambda item: item["boat_number"]):
        boats.append(
            {
                "boat_number": row["boat_number"],
                "win_pct": row.get("ai_prediction_pct"),
                "top3_pct": row.get("ai_3ren_pct"),
                "general_top3_pct": row.get("general_3ren_pct"),
                "odds_prediction_pct": row.get("odds_prediction_pct"),
                "odds_prediction_rank": row.get("odds_prediction_pct_rank"),
                "trifecta_first_rank": morning_metrics.get(f"b{row['boat_number']}_trifecta_first_rank"),
                "trifecta_min_head_odds": morning_metrics.get(f"b{row['boat_number']}_trifecta_min_head_odds"),
                "trifecta_top10_head_count": morning_metrics.get(f"b{row['boat_number']}_trifecta_top10_head_count"),
                "trifecta_top20_head_count": morning_metrics.get(f"b{row['boat_number']}_trifecta_top20_head_count"),
                "recent10_st_time_avg": morning_metrics.get(f"b{row['boat_number']}_recent10_st_time_avg"),
                "recent10_st_rank_avg": morning_metrics.get(f"b{row['boat_number']}_recent10_st_rank_avg"),
                "recent10_win_pct": morning_metrics.get(f"b{row['boat_number']}_recent10_win_pct"),
                "recent10_top3_pct": morning_metrics.get(f"b{row['boat_number']}_recent10_top3_pct"),
                "recent10_sashi_rate": morning_metrics.get(f"b{row['boat_number']}_recent10_sashi_rate"),
                "recent10_makuri_rate": morning_metrics.get(f"b{row['boat_number']}_recent10_makuri_rate"),
                "recent10_makurizashi_rate": morning_metrics.get(f"b{row['boat_number']}_recent10_makurizashi_rate"),
                "composite_win_pct": row.get("composite_win_pct"),
                "composite_top3_pct": row.get("composite_top3_pct"),
                "composite_top3_actual_pct": row.get("composite_top3_actual_pct"),
                "composite_rate_reasons": row.get("composite_rate_reasons") or [],
                "ai_plus": row.get("ai_plus"),
                "ai_prediction_rank": row.get("ai_prediction_pct_rank"),
                "top3_rank": row.get("ai_3ren_pct_rank"),
                "ai_plus_rank": row.get("ai_plus_rank"),
                "st_rank_general": row.get("st_rank_general"),
                "tenji_time": row.get("tenji_time"),
                "tenji_rank": row.get("tenji_rank"),
                "isshu_time": row.get("isshu_time"),
                "isshu_rank": row.get("isshu_rank"),
                "chokusen_time": row.get("chokusen_time"),
                "chokusen_rank": row.get("chokusen_rank"),
                "mawariashi_time": row.get("mawariashi_time"),
                "mawariashi_rank": row.get("mawariashi_rank"),
                "start_tenji_time": row.get("start_tenji_time"),
                "start_tenji_time_rank": row.get("start_tenji_time_rank"),
                "start_tenji_rank": row.get("start_tenji_rank"),
                "before_start_sinnyu": row.get("before_start_sinnyu"),
                "tilt": row.get("tilt"),
                "official_tenji_time": row.get("official_tenji_time"),
                "official_tenji_rank": row.get("official_tenji_rank"),
                "official_start_tenji_time": row.get("official_start_tenji_time"),
                "official_start_tenji_time_rank": row.get("official_start_tenji_time_rank"),
                "official_start_tenji_rank": row.get("official_start_tenji_rank"),
                "official_before_start_sinnyu": row.get("official_before_start_sinnyu"),
                "official_tilt": row.get("official_tilt"),
                "official_data_source": row.get("official_data_source") or "",
                "avg_isshu_diff": row.get("avg_isshu_diff"),
                "avg_tenji_diff": row.get("avg_tenji_diff"),
                "avg_chokusen_diff": row.get("avg_chokusen_diff"),
                "avg_mawariashi_diff": row.get("avg_mawariashi_diff"),
                "avg_start_tenji_diff": row.get("avg_start_tenji_diff"),
                "venue_head_score_delta": row.get("venue_head_score_delta"),
                "venue_top3_score_delta": row.get("venue_top3_score_delta"),
                "venue_manshu_score_delta": row.get("venue_manshu_score_delta"),
                "avgdiff_head_score_delta": row.get("avgdiff_head_score_delta"),
                "avgdiff_top3_score_delta": row.get("avgdiff_top3_score_delta"),
                "avgdiff_manshu_score_delta": row.get("avgdiff_manshu_score_delta"),
                "venue_effect_targets": row.get("venue_effect_targets") or [],
                "venue_factor_reasons": row.get("venue_factor_reasons") or [],
                "avgdiff_threshold_reasons": row.get("avgdiff_threshold_reasons") or [],
                "avgdiff_threshold_matches": row.get("avgdiff_threshold_matches") or [],
                "venue_low_ai_revival": bool(row.get("venue_low_ai_revival")),
                "venue_low_ai_revival_role": row.get("venue_low_ai_revival_role") or "",
                "venue_low_ai_revival_profile": row.get("venue_low_ai_revival_profile") or {},
                "venue_low_ai_revival_reasons": row.get("venue_low_ai_revival_reasons") or [],
                "super_slit_alert": bool(row.get("super_slit_alert")),
                "super_slit_tenji_adv": row.get("super_slit_tenji_adv"),
                "super_slit_st_rank_adv": row.get("super_slit_st_rank_adv"),
                "super_slit_effect_multiplier": row.get("super_slit_effect_multiplier"),
                "super_slit_score_bonus": row.get("super_slit_score_bonus"),
                "super_slit_comp_bonus": row.get("super_slit_comp_bonus"),
                "super_slit_value_bonus": row.get("super_slit_value_bonus"),
                "super_slit_win_lift_pp": row.get("super_slit_win_lift_pp"),
                "super_slit_top3_lift_pp": row.get("super_slit_top3_lift_pp"),
                "super_slit_effect_profile": row.get("super_slit_effect_profile") or {},
                "double_time": bool(row.get("double_time")),
            }
        )
    return {
        "boats": boats,
        "boat1_ai_prediction_pct": b1.get("ai_prediction_pct"),
        "boat1_odds_prediction_pct": boat1_odds_pct,
        "boat1_odds_rank": boat1_odds_rank,
        "odds_snapshot_source": live_odds_context.get("odds_snapshot_source"),
        "odds_boats": live_odds_context.get("odds_boats") or {},
        **{
            key: value
            for key, value in live_odds_context.items()
            if re.match(r"boat[1-6]_odds_(prediction_pct|rank)$", key)
        },
        **{
            key: value
            for key, value in live_odds_context.items()
            if key.startswith("popular_b1_")
        },
        **{
            key: morning_metrics.get(key)
            for key in [
                "b1_trifecta_top5_1head",
                "trifecta_top5_head1_count",
                "trifecta_top5_count",
                "trifecta_top10_head1_count",
                "trifecta_top10_count",
                "trifecta_top20_head1_count",
                "trifecta_top20_count",
                "trifecta_top10_head_counts",
                "trifecta_top20_head_counts",
                "trifecta_head_first_ranks",
                "trifecta_head_min_odds",
                "trifecta_top1_odds",
                "trifecta_top5_avg_odds",
                "trifecta_top5_combos",
                "trifecta_odds_snapshot_at",
            ]
            if key in morning_metrics
        },
        **{
            f"b{boat}_{suffix}": morning_metrics.get(f"b{boat}_{suffix}")
            for boat in range(1, 7)
            for suffix in [
                "trifecta_first_rank",
                "trifecta_min_head_odds",
                "trifecta_top10_head_count",
                "trifecta_top20_head_count",
                "recent10_st_time_avg",
                "recent10_st_rank_avg",
                "recent10_waku_race_count",
                "recent10_win_pct",
                "recent10_second_pct",
                "recent10_third_pct",
                "recent10_top3_pct",
                "recent10_nige_rate",
                "recent10_sasare_rate",
                "recent10_makurare_rate",
                "recent10_sashi_rate",
                "recent10_makuri_rate",
                "recent10_makurizashi_rate",
                "recent10_makurizasare_rate",
                "recent10_nigashi_rate",
            ]
            if f"b{boat}_{suffix}" in morning_metrics
        },
        "boat1_ai_plus": b1.get("ai_plus"),
        "boat1_ai_plus_order": b1.get("ai_plus_rank"),
        "boat1_nige_pct": b1.get("nige_pct"),
        "boat1_loss_pct": b1_loss,
        "boat1_avg_isshu_diff": b1.get("avg_isshu_diff"),
        "boat1_isshu_avg_diff": b1.get("isshu_avg_diff"),
        "avg_isshu_time": b1.get("avg_isshu_time"),
        "avg_exhibit_combo_time": b1.get("avg_exhibit_combo_time"),
        "lap_time_type": lap_time_type,
        "is_summer": is_summer_date(date_text),
        "b1_summer_isshu_factor": summer_factor["signal"],
        "b1_summer_nige_delta_pp": summer_factor["nige_delta_pp"],
        "boat1_summer_isshu_factor": summer_factor["signal"],
        "boat1_summer_nige_delta_pp": summer_factor["nige_delta_pp"],
        "boat1_tenji_time": b1_tenji,
        "boat1_isshu_time": b1_isshu,
        "boat1_tenji_rank": b1.get("tenji_rank"),
        "boat1_tenji_time_rank": b1.get("tenji_time_rank"),
        "boat1_isshu_rank": b1.get("isshu_rank"),
        "boat1_chokusen_time": b1.get("chokusen_time"),
        "boat1_chokusen_rank": b1.get("chokusen_rank"),
        "boat1_mawariashi_time": b1.get("mawariashi_time"),
        "boat1_mawariashi_rank": b1.get("mawariashi_rank"),
        "boat1_start_tenji_time": b1.get("start_tenji_time"),
        "boat1_start_tenji_time_rank": b1.get("start_tenji_time_rank"),
        "boat1_start_tenji_rank": b1.get("start_tenji_rank"),
        "boat1_before_start_sinnyu": b1.get("before_start_sinnyu"),
        "boat1_tilt": b1.get("tilt"),
        "aux_exhibition_source": official_aux.get("source") if official_aux.get("available") else "",
        "venue_exhibition_factor_source": str(VENUE_EXHIBITION_FACTOR_DICTIONARY) if VENUE_EXHIBITION_FACTOR_DICTIONARY.exists() else "",
        "avgdiff_threshold_effect_source": str(AVG_DIFF_THRESHOLD_EFFECT_PROFILE) if AVG_DIFF_THRESHOLD_EFFECT_PROFILE.exists() else "",
        "venue_exhibition_factor_matches": [
            {
                "boat_number": row["boat_number"],
                "reasons": row.get("venue_factor_reasons") or [],
                "avgdiff_reasons": row.get("avgdiff_threshold_reasons") or [],
                "targets": row.get("venue_effect_targets") or [],
                "head_delta": row.get("venue_head_score_delta"),
                "top3_delta": row.get("venue_top3_score_delta"),
                "manshu_delta": row.get("venue_manshu_score_delta"),
                "avgdiff_head_delta": row.get("avgdiff_head_score_delta"),
                "avgdiff_top3_delta": row.get("avgdiff_top3_score_delta"),
                "avgdiff_manshu_delta": row.get("avgdiff_manshu_score_delta"),
            }
            for row in rows
            if row.get("venue_factor_matches")
        ],
        "avgdiff_threshold_matches": [
            {
                "boat_number": row["boat_number"],
                "reasons": row.get("avgdiff_threshold_reasons") or [],
                "head_delta": row.get("avgdiff_head_score_delta"),
                "top3_delta": row.get("avgdiff_top3_score_delta"),
                "manshu_delta": row.get("avgdiff_manshu_score_delta"),
            }
            for row in rows
            if row.get("avgdiff_threshold_matches")
        ],
        "venue_low_ai_revivals": venue_low_ai_revival_summary(rows),
        "venue_b1_head_debuff": bool(b1.get("venue_b1_head_debuff")),
        "venue_b1_fly_manshu_watch": bool(b1.get("venue_b1_fly_manshu_watch")),
        "venue_b1_factor_reasons": b1.get("venue_factor_reasons") or [],
        "official_beforeinfo_url": official_aux.get("url") or "",
        "official_aux_fetched_at": official_aux.get("fetched_at") or "",
        "official_aux_error": official_aux.get("error") or "",
        "official_tenji_boats": int(official_aux.get("tenji_boats") or 0),
        "official_start_tenji_boats": int(official_aux.get("start_tenji_boats") or 0),
        "official_tilt_boats": int(official_aux.get("tilt_boats") or 0),
        "official_aux_note": (
            f"公式補助: 展示{int(official_aux.get('tenji_boats') or 0)}/6・ST{int(official_aux.get('start_tenji_boats') or 0)}/6"
            if official_aux.get("available")
            else (f"公式補助取得失敗: {official_aux.get('error')}" if official_aux.get("error") else "")
        ),
        "boat1_official_tenji_time": b1.get("official_tenji_time"),
        "boat1_official_tenji_rank": b1.get("official_tenji_rank"),
        "boat1_official_start_tenji_time": b1.get("official_start_tenji_time"),
        "boat1_official_start_tenji_time_rank": b1.get("official_start_tenji_time_rank"),
        "boat1_official_start_tenji_rank": b1.get("official_start_tenji_rank"),
        "boat1_official_before_start_sinnyu": b1.get("official_before_start_sinnyu"),
        "boat1_official_tilt": b1.get("official_tilt"),
        "b1_extra_exhibition_bad_count": b1_extra_bad_count,
        "outer56_best_tenji_time": outer56_best_tenji,
        "outer56_best_isshu_time": outer56_best_isshu,
        "outer56_best_avg_isshu_diff": outer56_best_avgdiff,
        "outer56_best_ai_prediction_pct": max(outer_ai_pred) if outer_ai_pred else None,
        "outer56_best_ai_plus": max(outer_ai_plus) if outer_ai_plus else None,
        "ai_rank6_boat": rank6.get("boat_number"),
        "ai_rank6_avg_isshu_diff": rank6.get("avg_isshu_diff"),
        "ai_rank6_ai_prediction_pct": rank6.get("ai_prediction_pct"),
        "ai_rank6_tenji_rank": rank6.get("tenji_rank"),
        "ai_rank6_isshu_rank": rank6.get("isshu_rank"),
        "ai_rank5_boat": rank5.get("boat_number"),
        "ai_rank5_avg_isshu_diff": rank5.get("avg_isshu_diff"),
        "ai_rank5_ai_prediction_pct": rank5.get("ai_prediction_pct"),
        "ai_rank5_tenji_rank": rank5.get("tenji_rank"),
        "ai_rank5_isshu_rank": rank5.get("isshu_rank"),
        "low_outer_boat": low_outer_boat if low_outer_boat in {5, 6} else None,
        "low_outer_ai_plus_rank": low_outer.get("ai_plus_rank"),
        "low_outer_avg_isshu_diff": low_outer.get("avg_isshu_diff"),
        "low_outer_ai_prediction_pct": low_outer.get("ai_prediction_pct"),
        "low_outer_tenji_rank": low_outer.get("tenji_rank"),
        "low_outer_isshu_rank": low_outer.get("isshu_rank"),
        "low_outer_exhibit_top2": bool(low_outer.get("exhibit_rank", 9) <= 2),
        "center_attack_wall_outer": bool(morning_metrics.get("center_attack_wall_outer")),
        "weather_pressure": bool(morning_metrics.get("weather_pressure")),
        "outer_isshu_priority_b1weak": bool(morning_metrics.get("outer_isshu_priority_b1weak")),
        "b1_full_tobashi_shape": bool(morning_metrics.get("b1_full_tobashi_shape")),
        "longshot_head_boats": morning_metrics.get("longshot_head_boats") or "",
        "longshot_head_candidate_count": int(as_num(morning_metrics.get("longshot_head_candidate_count")) or 0),
        "longshot_head_with_b1_gap": bool(morning_metrics.get("longshot_head_with_b1_gap")),
        "double_time_boats": double_time_boats,
        "super_slit_boats": super_slit_boats,
        "super_slit_effects": super_slit_effects,
        "super_slit_alert_count": len(super_slit_boats),
        "mid234_super_slit_count": sum(1 for row in rows if row["boat_number"] in {2, 3, 4} and row.get("super_slit_alert")),
        "outer456_super_slit_count": sum(1 for row in rows if row["boat_number"] in {4, 5, 6} and row.get("super_slit_alert")),
        "outer56_super_slit_count": sum(1 for row in outer if row.get("super_slit_alert")),
        **slit_metrics,
        "boat1_double_time": bool(b1.get("double_time")),
        "mid234_double_time_count": sum(1 for row in rows if row["boat_number"] in {2, 3, 4} and row.get("double_time")),
        "outer46_double_time_count": sum(1 for row in outer46 if row.get("double_time")),
        "outer56_double_time_count": sum(1 for row in outer if row.get("double_time")),
        "outer56_tenji_advantage": (
            b1_tenji - outer56_best_tenji
            if b1_tenji is not None and outer56_best_tenji is not None
            else None
        ),
        "outer56_isshu_advantage": (
            b1_isshu - outer56_best_isshu
            if b1_isshu is not None and outer56_best_isshu is not None
            else None
        ),
        "outer56_tenji_top2_count": sum(
            1 for row in outer if row.get("tenji_time") is not None and row.get("tenji_time_rank", 9) <= 2
        ),
        "outer56_isshu_top2_count": sum(
            1 for row in outer if row.get("isshu_time") is not None and row.get("isshu_rank", 9) <= 2
        ),
        "outer56_chokusen_top2_count": outer56_chokusen_top2_count,
        "outer56_mawariashi_top2_count": outer56_mawariashi_top2_count,
        "outer56_start_tenji_top2_count": outer56_start_tenji_top2_count,
        "outer46_chokusen_top2_count": outer46_chokusen_top2_count,
        "outer46_mawariashi_top2_count": outer46_mawariashi_top2_count,
        "outer46_start_tenji_top2_count": outer46_start_tenji_top2_count,
        "outer56_extra_exhibition_top2_count": outer56_extra_top2_count,
        "outer46_extra_exhibition_top2_count": outer46_extra_top2_count,
        "outer56_tilt_plus_count": outer56_tilt_plus_count,
        "b1_tilt_minus": bool(b1.get("tilt") is not None and b1.get("tilt") < 0),
        "extra_exhibition_b1weak_outer56strong": bool(b1_extra_bad_count >= 2 and outer56_extra_top2_count >= 2),
        "extra_exhibition_b1weak_outer46strong": bool(b1_extra_bad_count >= 2 and outer46_extra_top2_count >= 2),
        "extra_exhibition_b1weak_outer56tilt": bool(
            b1_extra_bad_count >= 2 and outer56_extra_top2_count >= 1 and outer56_tilt_plus_count >= 1
        ),
        "outer56_exhibit_top2_count": outer56_exhibit_top2_count,
        "outer56_low_aiplus_exhibit_top2_count": sum(
            1 for row in outer if row.get("ai_plus_rank", 9) >= 5 and row.get("exhibit_rank", 9) <= 2
        ),
        "outer56_low_aipred_exhibit_top2_count": sum(
            1 for row in outer if row.get("ai_prediction_pct_rank", 9) >= 5 and row.get("exhibit_rank", 9) <= 2
        ),
        "outer46_exhibit_top2_count": sum(1 for row in outer46 if row.get("exhibit_rank", 9) <= 2),
        "outer46_low_aiplus_exhibit_top2_count": sum(
            1 for row in outer46 if row.get("ai_plus_rank", 9) >= 5 and row.get("exhibit_rank", 9) <= 2
        ),
        "matchup_lane1_pressure_score": as_num(morning_metrics.get("matchup_lane1_pressure_score")),
        "matchup_outer_good_count": int(as_num(morning_metrics.get("matchup_outer_good_count")) or 0),
        "matchup_lane1_bad_flag": bool(morning_metrics.get("matchup_lane1_bad_flag")),
        "matchup_notes": morning_metrics.get("matchup_notes") or "",
        "matchup_buff_boats": morning_metrics.get("matchup_buff_boats") or "",
        "b1_matchup_label": morning_metrics.get("b1_matchup_label") or "",
        "b2_matchup_label": morning_metrics.get("b2_matchup_label") or "",
        "b3_matchup_label": morning_metrics.get("b3_matchup_label") or "",
        "b4_matchup_label": morning_metrics.get("b4_matchup_label") or "",
        "b5_matchup_label": morning_metrics.get("b5_matchup_label") or "",
        "b6_matchup_label": morning_metrics.get("b6_matchup_label") or "",
        "head_primary_boats": (selection_roles or {}).get("heads") or [],
        "axis_primary_boats": (selection_roles or {}).get("axes") or [],
        "axis_alt_boats": (selection_roles or {}).get("alt_axes") or [],
        "keshi_boat": (selection_roles or {}).get("keshi"),
        "keshi_reason": (selection_roles or {}).get("keshi_reason"),
        "ai_plus_rank6_boat": (selection_roles or {}).get("ai_plus_rank6_boat"),
        "ai_plus_rank6_revival": (selection_roles or {}).get("ai_plus_rank6_revival") or [],
        "tenji_boats": sum(1 for row in rows if row.get("tenji_time") is not None),
        "raw_isshu_boats": raw_isshu_boats,
        "hanshu_boats": hanshu_boats,
        "isshu_boats": isshu_boats,
    }


def condition_confirmed(condition, metrics):
    checks = []
    text = str(condition or "")
    if "1号艇平均との差" in text or "1号艇 展示+一周平均との差" in text:
        if "0.30以下" in text or "+0.30以下" in text:
            checks.append(("1号艇 展示+一周平均との差+0.30以下", (metrics.get("boat1_avg_isshu_diff") or 9) <= 0.30))
        elif "0.10以下" in text or "+0.10以下" in text:
            checks.append(("1号艇 展示+一周平均との差+0.10以下", (metrics.get("boat1_avg_isshu_diff") or 9) <= 0.10))
        elif "-0.05以下" in text:
            checks.append(("1号艇 展示+一周平均との差-0.05以下", (metrics.get("boat1_avg_isshu_diff") or 9) <= -0.05))
        elif "0以下" in text:
            checks.append(("1号艇 展示+一周平均との差0以下", (metrics.get("boat1_avg_isshu_diff") or 9) <= 0))
        elif "0.65以上" in text or "+0.65以上" in text:
            checks.append(("1号艇 展示+一周平均との差+0.65以上", (metrics.get("boat1_avg_isshu_diff") or -9) >= 0.65))
        elif "0.30以上" in text or "+0.30以上" in text:
            checks.append(("1号艇 展示+一周平均との差+0.30以上", (metrics.get("boat1_avg_isshu_diff") or -9) >= 0.30))
        elif "0.10以上" in text:
            checks.append(("1号艇 展示+一周平均との差0.10以上", (metrics.get("boat1_avg_isshu_diff") or -9) >= 0.10))

    if "夏場" in text and "1号艇" in text and ("1周" in text or "平均との差" in text):
        checks.append(("夏場6〜8月", bool(metrics.get("is_summer"))))
        if "-0.10以下" in text or "0.10秒遅い" in text:
            checks.append(("夏場1号艇1周平均との差-0.10以下", (metrics.get("boat1_isshu_avg_diff") or 9) <= SUMMER_B1_SLOW_DIFF))
        elif "0.10以上" in text or "0.10秒速い" in text:
            checks.append(("夏場1号艇1周平均との差0.10以上", (metrics.get("boat1_isshu_avg_diff") or -9) >= SUMMER_B1_FAST_DIFF))

    if "5/6号艇平均との差" in text or "5/6号艇 展示+一周平均との差" in text:
        if "0.14以上" in text:
            checks.append(("5/6 展示+一周平均との差0.14以上", (metrics.get("outer56_best_avg_isshu_diff") or -9) >= 0.14))
        elif "0.10以上" in text:
            checks.append(("5/6 展示+一周平均との差0.10以上", (metrics.get("outer56_best_avg_isshu_diff") or -9) >= 0.10))

    if "人気1号艇" in text:
        checks.append(
            (
                "1号艇オッズ評価45%以上1位",
                (metrics.get("boat1_odds_prediction_pct") or -1) >= 45
                and int(metrics.get("boat1_odds_rank") or 9) == 1,
            )
        )
        if "1周-0.10以下" in text:
            checks.append(("1号艇1周平均との差-0.10以下", (metrics.get("boat1_isshu_avg_diff") or 9) <= -0.10))
        if "逃げ率45未満" in text:
            checks.append(("1号艇逃げ率45%未満", (metrics.get("boat1_nige_pct") or 999) < 45))

    if "低評価外枠" in text:
        checks.append(("低評価外枠が5/6号艇", int(metrics.get("low_outer_boat") or 0) in {5, 6}))
        if "AI予測8%以上" in text:
            checks.append(("低評価外枠AI予測8%以上", (metrics.get("low_outer_ai_prediction_pct") or -1) >= 8))
        elif "AI予測5%以上" in text:
            checks.append(("低評価外枠AI予測5%以上", (metrics.get("low_outer_ai_prediction_pct") or -1) >= 5))
        if "平均との差+0.10以上" in text:
            checks.append(("低評価外枠 展示+一周平均との差+0.10以上", (metrics.get("low_outer_avg_isshu_diff") or -9) >= 0.10))
        if "展示/1周2位以内" in text:
            checks.append(("低評価外枠 展示/1周2位以内", bool(metrics.get("low_outer_exhibit_top2"))))
        if "1号艇逃げ失敗40%以上" in text:
            checks.append(("1号艇逃げ失敗40%以上", (metrics.get("boat1_loss_pct") or -1) >= 40))
        if "外圧" in text:
            checks.append(("スリット5/6外圧", bool(metrics.get("slit_outer56_pressure_vs_1"))))

    if "人気薄頭" in text:
        checks.append(("3〜6人気薄頭候補あり", metrics.get("longshot_head_candidate_count", 0) >= 1))
        if "1過信" in text:
            checks.append(("人気薄頭+1過信", bool(metrics.get("longshot_head_with_b1_gap"))))

    if "3/4攻撃" in text:
        checks.append(("3/4攻撃+外圧", bool(metrics.get("center_attack_wall_outer"))))

    if "会場風波" in text:
        checks.append(("風波+1弱+外圧", bool(metrics.get("weather_pressure"))))

    if "外枠一周優先" in text:
        checks.append(("外枠一周優先+1弱", bool(metrics.get("outer_isshu_priority_b1weak"))))

    if "1号艇完全飛ばし" in text:
        checks.append(("1号艇完全飛ばし型", bool(metrics.get("b1_full_tobashi_shape"))))

    if "AI+最下位の平均との差0.10以上" in text or "AI+最下位の展示+一周平均との差0.10以上" in text:
        checks.append(("AI+最下位 展示+一周平均との差0.10以上", (metrics.get("ai_rank6_avg_isshu_diff") or -9) >= 0.10))

    if "AI+最下位が5/6号艇" in text:
        checks.append(("AI+最下位が5/6号艇", int(metrics.get("ai_rank6_boat") or 0) in {5, 6}))

    if "スーパースリット" in text or "スーパーST" in text:
        if "2艇以上" in text:
            checks.append(("スーパースリット2艇以上", metrics.get("super_slit_alert_count", 0) >= 2))
        elif "5/6" in text:
            checks.append(("5/6号艇にスーパースリット", metrics.get("outer56_super_slit_count", 0) >= 1))
        elif "4〜6" in text:
            checks.append(("4〜6号艇にスーパースリット", metrics.get("outer456_super_slit_count", 0) >= 1))
        else:
            checks.append(("スーパースリットあり", metrics.get("super_slit_alert_count", 0) >= 1))

    if "スリット隊形" in text:
        if "1前" in text or "2壁" in text:
            checks.append(("スリット隊形1前+2壁", bool(metrics.get("slit_b1_front_wall"))))
        elif "1凹み" in text:
            checks.append(("スリット隊形1凹み", bool(metrics.get("slit_b1_hole_vs_23"))))
        elif "3覗き" in text:
            checks.append(("スリット隊形3覗き", bool(metrics.get("slit_b3_peek_vs_12")) or bool(metrics.get("slit_b2_wall_break_3peek"))))
        elif "4カド" in text:
            checks.append(("スリット隊形4カド覗き", bool(metrics.get("slit_b4_cadou_peek"))))
        elif "外圧" in text:
            checks.append(("スリット隊形外圧", bool(metrics.get("slit_outer456_pressure")) or bool(metrics.get("slit_outer56_pressure_vs_1"))))
        else:
            checks.append(("スリット隊形あり", bool(metrics.get("slit_shape_label"))))

    if "対戦相性" in text:
        if "2艇以上" in text:
            checks.append(("対戦相性バフ艇2艇以上", metrics.get("matchup_outer_good_count", 0) >= 2))
        elif "1号艇" in text and "劣勢" in text:
            checks.append(("対戦相性1号艇劣勢", bool(metrics.get("matchup_lane1_bad_flag"))))
        elif "相性バフ" in text:
            checks.append(("対戦相性バフ艇あり", bool(metrics.get("matchup_buff_boats"))))
        else:
            checks.append(
                (
                    "対戦相性あり",
                    bool(metrics.get("matchup_buff_boats"))
                    or bool(metrics.get("matchup_lane1_bad_flag"))
                    or (metrics.get("matchup_outer_good_count", 0) >= 1),
                )
            )

    if "AI+最下位" in text and "展示4位以下" in text:
        checks.append(("AI+最下位展示4位以下", (metrics.get("ai_rank6_tenji_rank") or 9) >= 4))

    if "1号艇展示順位5" in text or "1号艇展示タイム5" in text:
        checks.append(("1号艇展示5位以下", metrics.get("boat1_tenji_time_rank", 9) >= 5))
    elif "1号艇展示順位4" in text or "1号艇展示タイム4" in text:
        checks.append(("1号艇展示4位以下", metrics.get("boat1_tenji_time_rank", 9) >= 4))
    elif "1号艇展示" in text and "4〜6位" in text:
        checks.append(("1号艇展示4位以下", metrics.get("boat1_tenji_time_rank", 9) >= 4))

    if "1周4" in text and "1号艇" in text:
        checks.append(("1号艇1周4位以下", metrics.get("boat1_isshu_rank", 9) >= 4))

    if "AI+下位4〜6号艇" in text:
        checks.append(("AI+下位4〜6号艇に展示上位", metrics.get("outer46_low_aiplus_exhibit_top2_count", 0) >= 1))
    elif "AI+下位5/6号艇" in text:
        checks.append(("AI+下位5/6号艇に展示上位", metrics.get("outer56_low_aiplus_exhibit_top2_count", 0) >= 1))
    elif "AI予測下位5/6号艇" in text:
        checks.append(("AI予測下位5/6号艇に展示上位", metrics.get("outer56_low_aipred_exhibit_top2_count", 0) >= 1))
    elif "4〜6号艇" in text:
        checks.append(("4〜6号艇に展示上位", metrics.get("outer46_exhibit_top2_count", 0) >= 1))
    elif "5/6号艇が2艇とも" in text:
        checks.append(("5/6号艇が2艇とも展示上位", metrics.get("outer56_exhibit_top2_count", 0) >= 2))
    elif "5/6号艇に" in text or "5/6号艇が" in text:
        checks.append(("5/6号艇に展示上位", metrics.get("outer56_exhibit_top2_count", 0) >= 1))

    for threshold in (0.15, 0.10, 0.08, 0.05, 0.03):
        pattern = f"{threshold:.2f}秒以上速い"
        idx = text.find(pattern)
        if idx < 0:
            continue
        prefix = text[max(0, idx - 20) : idx]
        is_isshu = prefix.rfind("1周") > prefix.rfind("展示")
        key = "outer56_isshu_advantage" if is_isshu else "outer56_tenji_advantage"
        label = f"5/6の{'1周' if is_isshu else '展示'}が{threshold:.2f}秒速い"
        checks.append((label, (metrics.get(key) or -9) >= threshold))
        break

    if not checks:
        return False, ["直前展示条件なし"]
    return all(ok for _label, ok in checks), [f"{label}:{'OK' if ok else 'NG'}" for label, ok in checks]


def b1_danger_for_subcore(metrics):
    checks = []
    b1_ai_pred = as_num(metrics.get("boat1_ai_prediction_pct"))
    b1_nige = as_num(metrics.get("boat1_nige_pct"))
    b1_loss = as_num(metrics.get("boat1_loss_pct"))
    b1_avg = as_num(metrics.get("boat1_avg_isshu_diff"))
    b1_tenji_rank = int(as_num(metrics.get("boat1_tenji_rank")) or 9)
    b1_isshu_rank = int(as_num(metrics.get("boat1_isshu_rank")) or 9)
    popular_score = as_num(metrics.get("popular_b1_fly_score")) or 0
    dominant_guard = bool(metrics.get("dominant_b1_hold_guard"))
    if dominant_guard:
        danger = (
            (b1_ai_pred is not None and b1_ai_pred < 35)
            or (b1_nige is not None and b1_nige < 40)
            or (b1_loss is not None and b1_loss >= 55)
        )
    else:
        danger = (
            (b1_ai_pred is not None and b1_ai_pred < 35)
            or (b1_nige is not None and b1_nige < 40)
            or (b1_loss is not None and b1_loss >= 40)
            or (b1_avg is not None and b1_avg <= 0.10)
            or b1_tenji_rank >= 4
            or b1_isshu_rank >= 4
            or popular_score >= 60
        )
    checks.append(f"1号艇危険:{'OK' if danger else 'NG'}")
    if dominant_guard:
        checks.append("強い1号艇ガード:OK")
    checks.append(f"1AI予測{fmt_pct(b1_ai_pred)}")
    checks.append(f"1逃げ{fmt_pct(b1_nige)}")
    checks.append(f"1逃げ失敗{fmt_pct(b1_loss)}")
    checks.append(f"1展示+周平均との差{fmt_time(b1_avg)}")
    return danger, checks


def subcore_outer_head_checks(rows, heads):
    head_rows = [row_by_boat(rows, boat) for boat in heads]
    head_scores = [head_candidate_score(row, manshu_head_mode=True)[0] for row in head_rows if row]
    second_score = min(head_scores) if len(head_scores) >= 2 else 0
    outer_heads = len(heads) >= 2 and set(heads).issubset({3, 4, 5, 6})
    material_flags = [
        row
        and (
            (row.get("ai_prediction_pct") or 0) >= 10
            or (row.get("avg_isshu_diff") is not None and row.get("avg_isshu_diff") >= 0.05)
            or row.get("double_time")
            or row.get("super_slit_alert")
            or (row.get("exhibit_rank") or 9) <= 2
        )
        for row in head_rows
    ]
    has_material = all(material_flags) if material_flags else False
    has_56 = any(boat in {5, 6} for boat in heads)
    outer_strong = outer_heads and second_score >= 30 and has_material
    return outer_strong, has_56, [
        f"外頭2艇の2番手まで強い:{'OK' if outer_strong else 'NG'}(頭{','.join(map(str, heads or [])) or '-'},下限{second_score:.1f})",
        f"5/6絡み:{'OK' if has_56 else 'NG'}",
    ]


def subcore_inner_axis_checks(rows, heads):
    axes, axis_rule = axis_boats_for_roles(rows, ranks=(1, 3))
    if any(axis in set(heads or []) for axis in axes) or len(axes) < 2:
        fallback, fallback_rule = axis_boats_for_roles(rows, ranks=(2, 3))
        fill = [boat for boat in fallback if boat not in set(heads or [])]
        axes = unique([boat for boat in axes if boat not in set(heads or [])] + fill)
        axis_rule = f"{axis_rule}（頭と重なる時は{fallback_rule}で補完）"
    axes = axes[:2]
    inner_axis = len(axes) >= 2 and any(axis in {1, 2} for axis in axes)
    return inner_axis, axes, [
        f"内軸残り:{'OK' if inner_axis else 'NG'}(軸{','.join(map(str, axes or [])) or '-'})",
        f"軸ルール:{axis_rule}",
    ]


def subcore_entry_checks(race, metrics, rows):
    rate = as_num(race.get("manshu_rate_pct")) or 0
    rate_ok = SUBCORE_ALERT_RATE_MIN <= rate < CORE_ALERT_RATE
    heads = head_boats_for_arunashi(rows)
    b1_ok, b1_checks = b1_danger_for_subcore(metrics)
    outer_ok, has_56, outer_checks = subcore_outer_head_checks(rows, heads)
    inner_axis_ok, _axes, axis_checks = subcore_inner_axis_checks(rows, heads)
    checks = [
        f"展示後38〜39.9%:{'OK' if rate_ok else 'NG'}({rate:.2f}%)",
        *b1_checks,
        *outer_checks,
        *axis_checks,
    ]
    return rate_ok and b1_ok and outer_ok and has_56 and inner_axis_ok, checks


def near_miss_explanation(
    race,
    metrics,
    rows,
    source_type,
    preview_ready,
    core_rate_ready,
    subcore_rate_ready,
    core_buy_ready,
    subcore_buy_ready,
    all_strategies,
    buy_strategies,
    subcore_strategies,
):
    """Explain why a monitored race did or did not become a buy alert."""
    post_rate = as_num(race.get("manshu_rate_pct")) or 0
    round_no = int(as_num(race.get("round")) or 0)
    reasons = []
    positives = []

    def add_reason(text):
        if text and text not in reasons:
            reasons.append(text)

    def add_positive(text):
        if text and text not in positives:
            positives.append(text)

    if not preview_ready:
        missing = exhibition_missing_reason(metrics) or "展示データ不足"
        return {
            "level": "取得待ち",
            "summary": f"{missing}のため本命判定前",
            "reasons": [missing, "展示・1周が揃ってから本命/見送りを確定"],
            "positives": [],
        }

    if core_buy_ready:
        return {
            "level": "本命",
            "summary": "展示後40%以上かつ検証済み本命買い条件OK",
            "reasons": [],
            "positives": [strategy.get("label") for strategy in buy_strategies[:2] if strategy.get("label")],
        }
    if subcore_buy_ready:
        return {
            "level": "準本命",
            "summary": "38%台かつ準本命買い条件OK",
            "reasons": [],
            "positives": [strategy.get("label") for strategy in subcore_strategies[:2] if strategy.get("label")],
        }

    if core_rate_ready:
        level = "本命手前"
        add_positive(f"展示後40%以上:OK({post_rate:.2f}%)")
    elif subcore_rate_ready:
        level = "準本命手前"
        add_positive(f"展示後38%台:OK({post_rate:.2f}%)")
        add_reason(f"本命40%以上に届かず({post_rate:.2f}%)")
    else:
        level = "見送り"
        add_reason(f"展示後38%未満({post_rate:.2f}%)")

    if source_type != "morning_top":
        add_reason("朝監視TOP10外なので本命扱いではない")

    b1_context = b1_popularity_context(metrics)
    b1_level = b1_context.get("level") or "不明"
    if b1_level:
        add_positive(f"1号艇人気: {b1_level}")
    if b1_publicly_backed(metrics):
        add_positive("1号艇人気あり")
    elif core_rate_ready and not metrics.get("b1_unpopular_head_value"):
        add_reason("1号艇が人気不足で、飛ばしても配当が伸びにくい")

    if b1_data_danger(metrics):
        add_positive(f"1号艇危険度: {metrics.get('popular_b1_fly_level') or '危険'}")
    elif core_rate_ready:
        add_reason("1号艇の危険度が本命条件まで届かない")

    if round_no:
        if round_no <= 3:
            add_positive(f"前半1〜3R:OK({round_no}R)")
        elif round_no <= 6:
            add_positive(f"前半1〜6R:OK({round_no}R)")
        elif core_rate_ready:
            add_reason(f"前半条件外({round_no}R)")

    if b1_exhibition_double_debuff(metrics):
        add_positive("1号艇展示Wデバフ:OK")
    elif b1_exhibition_filtered_debuff(metrics):
        add_positive("1号艇展示弱化:OK")
    elif core_rate_ready and b1_publicly_backed(metrics):
        add_reason("1号艇の展示弱化が本命条件まで揃わない")

    if metrics.get("core_front_no1_odds_block_reason"):
        add_reason(metrics.get("core_front_no1_odds_block_reason"))

    if core_rate_ready and not buy_strategies:
        validated_ids = {strategy.get("strategy_id") for strategy in all_strategies}
        if validated_ids:
            add_reason("参考ロジックはあるが検証済み本命買い条件ではない")
        else:
            add_reason("検証済み本命買い条件に未一致")

    if subcore_rate_ready and not subcore_buy_ready:
        subcore_ok, subcore_checks = subcore_entry_checks(race, metrics, rows)
        if not subcore_ok:
            ng_checks = [check for check in subcore_checks if ":NG" in check][:3]
            for check in ng_checks:
                add_reason(check)

    if all_strategies:
        labels = [strategy.get("label") for strategy in all_strategies[:2] if strategy.get("label")]
        if labels:
            add_positive("参考戦略: " + " / ".join(labels))

    if not reasons:
        add_reason("買い条件:NG")

    summary = reasons[0]
    if level == "本命手前" and positives:
        summary = f"{positives[0]}だが、{summary}"
    elif level == "準本命手前":
        summary = reasons[0]

    return {
        "level": level,
        "summary": summary,
        "reasons": reasons[:6],
        "positives": positives[:6],
    }


def subcore_38_arunashi12(rows):
    scored = []
    for row in rows:
        boat = row["boat_number"]
        if boat not in {3, 4, 5, 6}:
            continue
        score, _reasons = head_candidate_score(row, manshu_head_mode=True)
        scored.append((score, boat))
    scored.sort(key=lambda item: (-item[0], item[1]))
    heads = [boat for _score, boat in scored[:2]]
    if len(heads) < 2 or not any(boat in {5, 6} for boat in heads):
        return set(), None

    outer_ok, has_56, _outer_checks = subcore_outer_head_checks(rows, heads)
    inner_axis_ok, axes, axis_checks = subcore_inner_axis_checks(rows, heads)
    if not (outer_ok and has_56 and inner_axis_ok and len(axes) >= 2):
        return set(), None

    keshi, keshi_reason, ai_plus_rank6_boat, ai_plus_rank6_revival = select_keshi_boat(
        rows, protected=set(heads + axes)
    )
    if keshi is None:
        return set(), None

    pool = [boat for boat in range(1, 7) if boat != keshi]
    tickets = set()
    for head in heads:
        if head in {1, 2, keshi}:
            continue
        for axis in axes:
            if axis in {head, keshi}:
                continue
            for other in pool:
                if other in {head, axis}:
                    continue
                tickets.add(f"{head}{axis}{other}")
                tickets.add(f"{head}{other}{axis}")
    if not tickets:
        return set(), None

    tickets = trim_tickets(tickets, heads, axes, max_points=12, rows=rows)
    if len(tickets) != 12:
        return set(), None

    axis_rule = axis_checks[1].replace("軸ルール:", "") if len(axis_checks) >= 2 else "軸候補に1号艇または2号艇が残る"
    return tickets, {
        "heads": heads,
        "head_rule": "準本命は3〜6号艇から頭2艇を選び、片方に5/6号艇を入れる",
        "head_mode": "subcore_38_outer56_required",
        "head_scores": head_score_details(rows, heads),
        "axes": axes,
        "axis_rule": axis_rule,
        "alt_axes": [],
        "alt_axis_rule": "準本命専用: 軸候補に1号艇または2号艇が残ることを確認",
        "supports": pool,
        "keshi": keshi,
        "keshi_reason": keshi_reason,
        "ai_plus_rank6_boat": ai_plus_rank6_boat,
        "ai_plus_rank6_revival": ai_plus_rank6_revival,
        "role_note": (
            f"準本命専用。頭は3〜6号艇から{heads[0]},{heads[1]}で、片方に5/6号艇を含む。"
            f"軸は{axis_rule}の{axes[0]},{axes[1]}。"
            f"消し{keshi}以外へ2・3着折り返し12点"
        ),
    }


def strategy_text_blob(strategy_id, roles=None, meta=None):
    roles = roles or {}
    meta = meta or {}
    values = [
        strategy_id or "",
        roles.get("head_rule") or "",
        roles.get("role_note") or "",
        roles.get("keshi_reason") or "",
        meta.get("odds_filter") or "",
    ]
    values.extend(str(item) for item in meta.get("entry_checks") or [])
    return " / ".join(str(value) for value in values if value)


def venue_sign_requires_no1(strategy_id, roles=None, meta=None):
    text = strategy_text_blob(strategy_id, roles, meta)
    return (
        "_no1" in str(strategy_id or "")
        or "1号艇は全消し" in text
        or "1号艇全消し" in text
        or "1消し" in text
    )


def venue_sign_requires_56(strategy_id, roles=None, meta=None):
    text = strategy_text_blob(strategy_id, roles, meta)
    return (
        "has56" in str(strategy_id or "")
        or "head56" in str(strategy_id or "")
        or "5/6絡み" in text
        or "5/6号艇絡み" in text
        or "5/6号艇が絡む" in text
    )


def venue_sign_excludes_head1(strategy_id, roles=None, meta=None):
    text = strategy_text_blob(strategy_id, roles, meta)
    return (
        "頭は非1号艇" in text
        or "非1号艇の" in text
        or "1号艇を頭では買わない" in text
        or "1号艇は頭で買わない" in text
    )


def no1_strategy_b1_hold_guard(rows):
    metrics = (rows[0].get("_morning_metrics") if rows else {}) or {}
    b1 = next((row for row in rows if row.get("boat_number") == 1), {})
    b1_ai = as_num(b1.get("ai_prediction_pct") or metrics.get("boat1_ai_prediction_pct"))
    b1_ai_plus = as_num(b1.get("ai_plus") or metrics.get("boat1_ai_plus"))
    b1_odds = as_num(b1.get("odds_prediction_pct") or metrics.get("boat1_odds_prediction_pct"))
    b1_odds_rank = int(as_num(metrics.get("boat1_odds_rank")) or 9)
    b1_avg = as_num(
        b1.get("avg_isshu_diff")
        if b1.get("avg_isshu_diff") is not None
        else metrics.get("boat1_avg_isshu_diff")
    )
    b1_loss = as_num(metrics.get("boat1_loss_pct"))
    b1_lap_rank = valid_boat_rank(b1.get("isshu_rank") or metrics.get("boat1_isshu_rank"))
    if (
        b1_ai is not None
        and b1_ai >= 70
        and b1_ai_plus is not None
        and b1_ai_plus >= 175
        and b1_odds is not None
        and b1_odds >= 55
        and b1_odds_rank == 1
        and b1_avg is not None
        and b1_avg >= 0.30
        and (b1_loss is None or b1_loss < 35)
        and (b1_lap_rank is None or b1_lap_rank <= 2)
    ):
        return (
            f"強い1号艇ガード: 1AI{b1_ai:.1f}%・AI+{b1_ai_plus:.1f}・"
            f"オッズ評価{b1_odds:.1f}%・平均との差+{b1_avg:.2f}で1号艇を消しすぎない"
        )
    return ""


def avgdiff_ticket_score_bonus(row, role="top3"):
    """Use avg-diff thresholds for ticket preference only, not sign triggers."""

    head_delta = as_num(row.get("avgdiff_head_score_delta")) or 0.0
    top3_delta = as_num(row.get("avgdiff_top3_score_delta")) or 0.0
    manshu_delta = as_num(row.get("avgdiff_manshu_score_delta")) or 0.0
    if role == "head":
        return bounded(head_delta * 0.20 + top3_delta * 0.08 + manshu_delta * 0.05, -1.20, 1.20)
    return bounded(top3_delta * 0.18 + head_delta * 0.05 + manshu_delta * 0.06, -1.00, 1.00)


def composite_ticket_score(ticket, row_lookup, require_56=False, original_heads=None, original_axes=None, original_keshi=None):
    boats = combo_boats(ticket)
    if len(boats) != 3:
        return -999999.0
    head, second, third = boats
    head_row = row_lookup.get(head, {})
    second_row = row_lookup.get(second, {})
    third_row = row_lookup.get(third, {})
    score = (
        venue_roi_win_score(head_row) * 1.65
        + venue_roi_top3_score(second_row) * 0.50
        + venue_roi_top3_score(third_row) * 0.42
    )
    score += avgdiff_ticket_score_bonus(head_row, "head")
    score += avgdiff_ticket_score_bonus(second_row, "top3") * 0.75
    score += avgdiff_ticket_score_bonus(third_row, "top3") * 0.60
    if require_56 and any(boat in {5, 6} for boat in boats):
        score += 5.0
    if head in {5, 6}:
        score += 4.0
    elif head in {3, 4}:
        score += 1.5
    if head in set(original_heads or []):
        score += 1.5
    if second in set(original_axes or []):
        score += 1.0
    if third in set(original_axes or []):
        score += 0.5
    for idx, boat in enumerate(boats):
        row = row_lookup.get(boat, {})
        if row.get("venue_low_ai_revival"):
            score += 1.5 if idx == 0 else 1.0
        if row.get("venue_dont_keshi"):
            score += 1.0
        if row.get("super_slit_alert"):
            score += 1.2 if idx == 0 else 0.8
    if original_keshi and original_keshi in boats:
        score -= 4.0
    return score


def refine_venue_sign_tickets(rows, tickets, roles, strategy_id, meta):
    """Keep the venue-sign tickets produced by the original mined templates.

    The high-ROI venue-sign figures were mined with each venue strategy's
    fixed template formation.  A later composite re-picker changed the actual
    tickets and broke that reproduction, so venue signs now pass through the
    original template tickets unchanged.
    """

    return tickets, roles


def roi_strategies(race, metrics, rows):
    rows = [dict(row, _morning_metrics=metrics) for row in (rows or [])]
    place = race.get("place_name")
    round_no = int(race.get("round") or 0)
    rank_no = int(race.get("rank") or race.get("morning_rank") or race.get("live_rank") or 99)
    b1_tenji_rank = valid_boat_rank(metrics.get("boat1_tenji_rank"))
    b1_tenji_time_rank = valid_boat_rank(metrics.get("boat1_tenji_time_rank"))
    b1_tenji_signal_rank = b1_tenji_rank if b1_tenji_rank is not None else b1_tenji_time_rank
    b1_isshu_rank = valid_boat_rank(metrics.get("boat1_isshu_rank"))
    b1_bad = any(
        rank is not None and rank >= 4
        for rank in (b1_tenji_rank, b1_tenji_time_rank, b1_isshu_rank)
    )
    strategies = []
    wind_speed = weather_value(race, "wind_speed") or 0
    wave_height = weather_value(race, "wave_height") or 0
    wind_wave = wind_speed >= 5 or wave_height >= 5
    b1_summer_fast = (metrics.get("b1_summer_isshu_factor") or metrics.get("boat1_summer_isshu_factor")) == "fast_hold"
    full_exhibition = has_full_exhibition(metrics)
    outer56_ai_pred = metrics.get("outer56_best_ai_prediction_pct") or -1
    outer56_ai_3ren = outer56_best_ai_3ren_pct(rows)
    outer56_ai_plus = metrics.get("outer56_best_ai_plus") or -1
    outer56_avgdiff = metrics.get("outer56_best_avg_isshu_diff") or -9
    b1_ai_pred = metrics.get("boat1_ai_prediction_pct") or 999
    b1_avgdiff = metrics.get("boat1_avg_isshu_diff") if metrics.get("boat1_avg_isshu_diff") is not None else 9
    b1_odds_pct = as_num(metrics.get("boat1_odds_prediction_pct")) or 0
    b1_odds_rank = int(as_num(metrics.get("boat1_odds_rank")) or 9)
    b1_nige_pct = as_num(metrics.get("boat1_nige_pct"))
    b1_loss_pct = as_num(metrics.get("boat1_loss_pct"))
    b1_row = next((row for row in rows if row.get("boat_number") == 1), {})
    b1_start_tenji_rank = valid_boat_rank(
        metrics.get("boat1_start_tenji_time_rank")
        or metrics.get("boat1_start_tenji_rank")
        or b1_row.get("start_tenji_time_rank")
        or b1_row.get("start_tenji_rank")
    )
    roi_head_rows_non1 = sorted(
        [row for row in rows if row.get("boat_number") != 1],
        key=lambda row: (
            -venue_roi_win_score(row),
            row.get("boat_number") or 9,
        ),
    )
    roi_head_top_boat = int(roi_head_rows_non1[0].get("boat_number") or 0) if roi_head_rows_non1 else 0
    roi_head_top2_boats = [
        int(row.get("boat_number") or 0)
        for row in roi_head_rows_non1[:2]
        if int(row.get("boat_number") or 0)
    ]
    b5_top3_rank = None
    if rows:
        for index, row in enumerate(
            sorted(
                rows,
                key=lambda item: (
                    -venue_roi_top3_score(item),
                    item.get("boat_number") or 9,
                ),
            ),
            1,
        ):
            if row.get("boat_number") == 5:
                b5_top3_rank = index
                break
    b1_venue_debuff = bool(b1_row.get("venue_b1_head_debuff") or metrics.get("venue_b1_head_debuff"))
    b1_venue_debuff_reasons = b1_row.get("venue_factor_reasons") or []
    low_ai_venue_revival = any(row.get("venue_low_ai_revival") for row in rows)
    low_ai_venue_revival_reasons = [
        item.get("reason")
        for item in venue_low_ai_revival_summary(rows)
        if item.get("reason")
    ]
    venue_buff_items = [
        item
        for row in rows
        for item in (row.get("venue_factor_matches") or [])
        if item.get("direction") == "buff"
    ]
    venue_top3_buff15_items = [
        item for item in venue_buff_items if (as_num(item.get("top3_rate_pp")) or 0.0) >= 15.0
    ]
    venue_top3_buff12_items = [
        item for item in venue_buff_items if (as_num(item.get("top3_rate_pp")) or 0.0) >= 12.0
    ]
    venue_top3_buff10_items = [
        item for item in venue_buff_items if (as_num(item.get("top3_rate_pp")) or 0.0) >= 10.0
    ]
    venue_head_buff8_items = [
        item for item in venue_buff_items if (as_num(item.get("win_rate_pp")) or 0.0) >= 8.0
    ]
    any_venue_top3_buff_ge10 = bool(venue_top3_buff10_items)
    any_venue_top3_buff_ge12 = bool(venue_top3_buff12_items)
    any_venue_top3_buff_ge15 = bool(venue_top3_buff15_items)
    any_venue_head_buff_ge8 = bool(venue_head_buff8_items)
    venue_strong_buff_reasons = [
        f"{item.get('venue')}{int(item.get('lane') or 0)}号艇 {item.get('metric_label')} "
        f"勝率差{as_num(item.get('win_rate_pp')) or 0.0:.1f}pt/3着内差{as_num(item.get('top3_rate_pp')) or 0.0:.1f}pt"
        for item in (venue_top3_buff15_items[:1] + venue_head_buff8_items[:1])
    ]
    venue_top3_buff10_reasons = [
        f"{item.get('venue')}{int(item.get('lane') or 0)}号艇 {item.get('metric_label')} "
        f"3着内差{as_num(item.get('top3_rate_pp')) or 0.0:.1f}pt"
        for item in venue_top3_buff10_items[:2]
    ]
    rank6_boat = int(metrics.get("ai_rank6_boat") or 0)
    rank6_ai_pred = metrics.get("ai_rank6_ai_prediction_pct") or -1
    post_rate = as_num(race.get("manshu_rate_pct")) or 0
    rank6_exhibit_top2 = (
        (metrics.get("ai_rank6_tenji_rank") or 9) <= 2
        or (metrics.get("ai_rank6_isshu_rank") or 9) <= 2
    )
    outer36_ai_plus_top1 = any(
        row.get("boat_number") in {3, 4, 5, 6} and row.get("ai_plus_rank") == 1
        for row in rows
    )
    outer36_ai_pred_top1 = any(
        row.get("boat_number") in {3, 4, 5, 6} and row.get("ai_prediction_pct_rank") == 1
        for row in rows
    )
    outer36_double_time = any(
        row.get("boat_number") in {3, 4, 5, 6} and row.get("double_time")
        for row in rows
    )
    outer36_exhibit_top2 = any(
        row.get("boat_number") in {3, 4, 5, 6}
        and (
            (
                valid_boat_rank(row.get("tenji_rank") or row.get("exhibit_rank") or row.get("tenji_time_rank"))
                is not None
                and valid_boat_rank(row.get("tenji_rank") or row.get("exhibit_rank") or row.get("tenji_time_rank")) <= 2
            )
            or (
                valid_boat_rank(row.get("isshu_rank")) is not None
                and valid_boat_rank(row.get("isshu_rank")) <= 2
            )
        )
        for row in rows
    )
    outer36_exhibit_top1 = any(
        row.get("boat_number") in {3, 4, 5, 6}
        and (
            (
                valid_boat_rank(row.get("tenji_rank") or row.get("exhibit_rank") or row.get("tenji_time_rank"))
                is not None
                and valid_boat_rank(row.get("tenji_rank") or row.get("exhibit_rank") or row.get("tenji_time_rank")) <= 1
            )
            or (
                valid_boat_rank(row.get("isshu_rank")) is not None
                and valid_boat_rank(row.get("isshu_rank")) <= 1
            )
        )
        for row in rows
    )
    outer56_exhibit_top2 = any(
        row.get("boat_number") in {5, 6}
        and (
            (
                valid_boat_rank(row.get("tenji_rank") or row.get("exhibit_rank") or row.get("tenji_time_rank"))
                is not None
                and valid_boat_rank(row.get("tenji_rank") or row.get("exhibit_rank") or row.get("tenji_time_rank")) <= 2
            )
            or (
                valid_boat_rank(row.get("isshu_rank")) is not None
                and valid_boat_rank(row.get("isshu_rank")) <= 2
            )
        )
        for row in rows
    )
    b1_popularity = b1_popularity_context(metrics)
    b1_popularity_level = b1_popularity.get("level") or "不明"
    recovery_power_ok, recovery_power_checks = b1_recovery_manshu_power_signal(metrics)
    rank6_power_ok = ai_rank6_exhibit_top2(metrics)
    b1_head_value, b1_head_value_reason = b1_unpopular_head_value(rows, metrics)
    metrics["b1_unpopular_head_value"] = bool(b1_head_value)
    if b1_head_value_reason:
        metrics["b1_unpopular_head_value_reason"] = b1_head_value_reason
    outer56_big50_chaos_score, outer56_big50_chaos_boats, outer56_big50_chaos_reasons = big50_outer56_chaos(rows)
    outer56_big50_chaos_text = (
        f"{outer56_big50_chaos_score}点"
        + (f"({fmt_list(outer56_big50_chaos_boats)}号艇: {', '.join(outer56_big50_chaos_reasons[:3])})" if outer56_big50_chaos_boats else "")
    )
    if (
        full_exhibition
        and place in {"芦屋", "住之江", "児島", "三国"}
        and round_no == 11
        and b1_odds_rank == 1
        and b1_odds_pct >= 50
        and b1_tenji_signal_rank is not None
        and b1_tenji_signal_rank >= 5
        and outer56_big50_chaos_score >= 6
    ):
        strategies.append(
            (
                "codex_big50_top4_11r_b1odds50_b1tenji5_outer56chaos6_h2_balanced_10",
                "Codex5万舟警戒D: 4場11R+1人気展示5位以下+外穴頭 10点",
                big50_top4_11r_dynamic_10,
                {
                    "tier": "big50_warning_sign",
                    "rate_gate_exempt": True,
                    "entry_checks": [
                        VALIDATED_RULE_STATS["codex_big50_top4_11r_b1odds50_b1tenji5_outer56chaos6_h2_balanced_10"],
                        f"場:{place} 11R:OK",
                        f"1号艇オッズ予測1位かつ50%以上:OK({b1_odds_pct:.1f}%)",
                        f"1号艇展示順位5位以下:OK({b1_tenji_signal_rank:.0f}位)",
                        f"5/6穴頭スコア6以上:OK({outer56_big50_chaos_text})",
                        "5万舟警戒サインのため展示後率に関係なく通知対象",
                    ],
                    "odds_filter": SYNTHETIC_ODDS_FILTER_LABEL,
                },
            )
        )
    if (
        full_exhibition
        and place == "住之江"
        and round_no == 5
        and b1_odds_rank == 1
        and b1_odds_pct >= 50
        and b1_tenji_signal_rank is not None
        and b1_tenji_signal_rank >= 4
        and outer56_big50_chaos_score >= 6
        and not (b1_avgdiff is not None and b1_avgdiff <= -0.20)
    ):
        strategies.append(
            (
                "codex_big50_suminoe5_b1odds50_b1tenji4_outer56chaos6_h1_balanced_8",
                "Codex5万舟警戒A: 住之江5R+1人気展示4位以下+5/6穴頭 8点",
                big50_suminoe5_dynamic_8,
                {
                    "tier": "big50_warning_sign",
                    "rate_gate_exempt": True,
                    "entry_checks": [
                        VALIDATED_RULE_STATS["codex_big50_suminoe5_b1odds50_b1tenji4_outer56chaos6_h1_balanced_8"],
                        "場:住之江 5R:OK",
                        f"1号艇オッズ予測1位かつ50%以上:OK({b1_odds_pct:.1f}%)",
                        f"1号艇展示順位4位以下:OK({b1_tenji_signal_rank:.0f}位)",
                        f"5/6穴頭スコア6以上:OK({outer56_big50_chaos_text})",
                        "5万舟警戒サインのため展示後率に関係なく通知対象",
                    ],
                    "odds_filter": SYNTHETIC_ODDS_FILTER_LABEL,
                },
            )
        )
    if (
        full_exhibition
        and place in {"芦屋", "住之江", "児島", "三国"}
        and round_no == 5
        and b1_odds_rank == 1
        and b1_odds_pct >= 40
        and b1_avgdiff is not None
        and b1_avgdiff <= -0.20
        and outer56_big50_chaos_score >= 6
    ):
        strategies.append(
            (
                "codex_big50_top4_5r_b1odds40_b1avg020_outer56chaos6_static_no1_12",
                "Codex5万舟警戒C: 4場5R赤信号 1消し外頭12点",
                big50_top4_5r_red_static_no1_12,
                {
                    "tier": "big50_warning_sign",
                    "rate_gate_exempt": True,
                    "entry_checks": [
                        VALIDATED_RULE_STATS["codex_big50_top4_5r_b1odds40_b1avg020_outer56chaos6_static_no1_12"],
                        f"場:{place} 5R:OK",
                        f"1号艇オッズ予測1位かつ40%以上:OK({b1_odds_pct:.1f}%)",
                        f"1号艇1周平均との差-0.20以下:OK({b1_avgdiff:.2f})",
                        f"5/6穴頭スコア6以上:OK({outer56_big50_chaos_text})",
                        "件数少なめの赤信号参考寄り。ただし過去検証では高配当捕捉が強い",
                        "5万舟警戒サインのため展示後率に関係なく通知対象",
                    ],
                    "odds_filter": SYNTHETIC_ODDS_FILTER_LABEL,
                },
            )
        )
    if ENABLE_UNVALIDATED_EXPERIMENTAL_BUY_STRATEGIES and full_exhibition and post_rate >= CORE_ALERT_RATE and b1_head_value:
        strategies.append(
            (
                "codex_b1_underbet_head8",
                "Codex逆歪み本命: 人気薄1号艇データ強 1頭最大12点",
                b1_underbet_head8,
                {
                    "tier": "core_b1_underbet",
                    "entry_checks": [
                        f"展示後40%以上:OK({post_rate:.2f}%)",
                        f"1号艇人気レベル: {b1_popularity_level}",
                        f"1号艇データ強:OK({b1_head_value_reason})",
                        "1号艇頭で配当妙味を狙う",
                    ],
                    "odds_filter": "1号艇が売れていない時だけ。1号艇頭でも低配当なら買わない",
                },
            )
        )
    if (
        ENABLE_UNVALIDATED_EXPERIMENTAL_BUY_STRATEGIES
        and full_exhibition
        and post_rate >= CORE_ALERT_RATE
        and round_no <= 3
        and b1_popularity_level == "売れすぎ"
        and b1_data_danger(metrics)
        and b1_isshu_rank is not None
        and b1_isshu_rank >= 4
        and outer56_ai_3ren is not None
        and outer56_ai_3ren >= 35
    ):
        strategies.append(
            (
                "codex_ultra_strict_b1_overbet_head2_b1_place_outer56",
                "Codex超厳選強本命: 売れすぎ1号艇危険+外頭2番手+1は2/3着+5/6絡み",
                core_40_ultra_head2_b1_place_outer56,
                {
                    "tier": "core_ultra_strict",
                    "entry_checks": [
                        f"展示後40%以上:OK({post_rate:.2f}%)",
                        f"前半1〜3R:OK({round_no}R)",
                        "1号艇人気レベル: 売れすぎ",
                        "データ上は1号艇危険",
                        f"1号艇の1周タイム4位以下:OK({b1_isshu_rank:.0f}位)",
                        f"5/6号艇のAI3連対率35%以上:OK({outer56_ai_3ren:.2f}%)",
                        "1号艇頭は買わず、2・3着だけ許可",
                    ],
                    "odds_filter": "1号艇頭は買わない。1号艇は2・3着だけ許可。合成オッズ3倍未満なら見送り",
                },
            )
        )
    if (
        full_exhibition
        and post_rate >= CORE_ALERT_RATE
        and b1_publicly_backed(metrics)
        and b1_data_danger(metrics)
        and recovery_power_ok
    ):
        strategies.append(
            (
                "codex_odds_gap_b1_danger_head1_8",
                f"Codexリカバリー参考: 1号艇{b1_popularity_level}+危険+外/低評価浮上 5〜12点",
                odds_gap_b1_danger_head1_8,
                {
                    "tier": "core_odds_gap_recovery",
                    "entry_checks": [
                        f"展示後40%以上:OK({post_rate:.2f}%)",
                        VALIDATED_RULE_STATS["codex_odds_gap_b1_danger_head1_8"],
                        f"1号艇人気レベル: {b1_popularity_level}",
                        "データ上は1号艇危険",
                        *recovery_power_checks,
                        "広い人気1危険ではなく、万舟強度条件を通った時だけ参考表示",
                        "買い目の万舟的中が未達のため本命買いからは除外",
                        "頭は最上位1艇だけ",
                        "1号艇頭は買わない",
                    ],
                    "odds_filter": "頭を1艇に絞り、合成オッズ3倍未満なら見送り",
                },
            )
        )
    if (
        ENABLE_UNVALIDATED_EXPERIMENTAL_BUY_STRATEGIES
        and full_exhibition
        and post_rate >= CORE_ALERT_RATE
        and round_no <= 3
        and b1_popularity_level == "売れすぎ"
        and b1_data_danger(metrics)
    ):
        strategies.append(
            (
                "codex_odds_gap_b1_overbet_front_head1_8",
                "Codex歪み本命: 売れすぎ1号艇危険 前半1頭最大12点",
                odds_gap_b1_overbet_front_head1_8,
                {
                    "tier": "core_odds_gap_overbet_front",
                    "entry_checks": [
                        f"展示後40%以上:OK({post_rate:.2f}%)",
                        f"前半1〜3R:OK({round_no}R)",
                        "1号艇人気レベル: 売れすぎ",
                        "データ上は1号艇危険",
                        "頭は2艇に広げず最上位1艇だけ",
                        "5/6号艇は必須にしない",
                    ],
                    "odds_filter": "1号艇頭は買わない。合成オッズ3倍未満なら見送り",
                },
            )
        )
    if full_exhibition and place == "桐生" and wind_speed >= 6 and b1_odds_rank == 1 and b1_odds_pct >= 45:
        strategies.append(
            (
                "codex_kiryu_wind6_b1odds45_h2_top3_no1_has56_12",
                "Codex桐生本命: 風6m以上+1号艇オッズ評価45%以上 1消し5/6絡み最大12点",
                kiryu_wind6_b1odds45_h2_top3_no1_has56_12,
                {
                    "tier": "venue_roi_core",
                    "rate_gate_exempt": True,
                    "entry_checks": [
                        "桐生専用ROI条件:OK",
                        f"風速6m以上:OK({wind_speed:.0f}m)",
                        f"1号艇オッズ評価1位:OK({b1_odds_rank}位)",
                        f"1号艇オッズ評価45%以上:OK({b1_odds_pct:.2f}%)",
                        VALIDATED_RULE_STATS["codex_kiryu_wind6_b1odds45_h2_top3_no1_has56_12"],
                        "展示後万舟率40%未満でも検証済みROIルールとして本命判定",
                        "1号艇は全消し",
                        "5/6号艇絡みだけ",
                    ],
                    "odds_filter": "1号艇全消し。5/6絡み。合成オッズ3倍未満なら見送り",
                },
            )
        )
    if (
        full_exhibition
        and place == "戸田"
        and b1_odds_rank == 1
        and b1_odds_pct >= 40
        and b1_nige_pct is not None
        and b1_nige_pct <= 40
        and outer36_exhibit_top2
    ):
        strategies.append(
            (
                "codex_toda_b1odds40_nige40_outerbox6",
                "Codex戸田本命: 1号艇人気+逃げ率40%以下 外3艇BOX6点",
                toda_b1odds40_nige40_outerbox6,
                {
                    "tier": "venue_roi_core",
                    "rate_gate_exempt": True,
                    "entry_checks": [
                        "戸田専用ROI条件:OK",
                        f"1号艇オッズ評価1位:OK({b1_odds_rank}位)",
                        f"1号艇オッズ評価40%以上:OK({b1_odds_pct:.2f}%)",
                        f"1号艇逃げ率40%以下:OK({b1_nige_pct:.2f}%)",
                        "3〜6号艇の展示/1周2位以内:OK",
                        VALIDATED_RULE_STATS["codex_toda_b1odds40_nige40_outerbox6"],
                        "展示後万舟率40%未満でも検証済みROIルールとして本命判定",
                        "1号艇は全消し",
                        "外3艇BOX6点",
                    ],
                    "odds_filter": "1号艇全消し。外3艇BOX6点。合成オッズ3倍未満なら見送り",
                },
            )
        )
    if (
        has_tenji_exhibition(metrics)
        and place == "江戸川"
        and 9 <= round_no <= 12
        and b1_odds_rank == 1
        and b1_odds_pct >= 45
        and b1_nige_pct is not None
        and b1_nige_pct <= 40
        and outer36_exhibit_top2
    ):
        strategies.append(
            (
                "codex_edogawa_r9_12_b1odds45_nige40_outertop2_h1_ai13_8",
                "Codex江戸川本命: 後半+1号艇強人気/逃げ率低め+外展示上位 非1頭最大8点",
                edogawa_r9_12_b1odds45_nige40_outertop2_h1_ai13_8,
                {
                    "tier": "venue_roi_core",
                    "rate_gate_exempt": True,
                    "entry_checks": [
                        "江戸川専用ROI条件:OK",
                        f"後半9〜12R:OK({round_no}R)",
                        f"1号艇オッズ評価1位45%以上:OK({b1_odds_pct:.2f}%)",
                        f"1号艇逃げ率40%以下:OK({b1_nige_pct:.2f}%)",
                        "外艇展示上位あり:OK",
                        VALIDATED_RULE_STATS["codex_edogawa_r9_12_b1odds45_nige40_outertop2_h1_ai13_8"],
                        "展示後万舟率40%未満でも検証済みROIルールとして本命判定",
                        "江戸川はDB上1周/半周なしのため展示タイム6艇で判定",
                        "1号艇は頭で買わない",
                    ],
                    "odds_filter": "1号艇を頭にしない最大8点。合成オッズ3倍未満なら見送り",
                },
            )
        )
    if (
        full_exhibition
        and place == "平和島"
        and 9 <= round_no <= 12
        and b1_odds_rank == 1
        and b1_odds_pct >= 55
        and b1_nige_pct is not None
        and b1_nige_pct <= 65
        and outer36_exhibit_top2
        and wave_height >= 3
    ):
        strategies.append(
            (
                "codex_heiwajima_r9_12_b1odds55_nige65_outertop2_wave3_h2_no1_top6",
                "Codex平和島本命: 後半+1号艇強人気/逃げ65%以下+外展示上位+波3cm 1消し6点",
                heiwajima_r9_12_b1odds55_nige65_outertop2_wave3_h2_no1_top6,
                {
                    "tier": "venue_roi_core",
                    "rate_gate_exempt": True,
                    "entry_checks": [
                        "平和島専用ROI条件:OK",
                        f"後半9〜12R:OK({round_no}R)",
                        f"1号艇オッズ評価1位:OK({b1_odds_rank}位)",
                        f"1号艇オッズ評価55%以上:OK({b1_odds_pct:.2f}%)",
                        f"1号艇逃げ率65%以下:OK({b1_nige_pct:.2f}%)",
                        "3〜6号艇の展示/1周2位以内:OK",
                        f"波高3cm以上:OK({wave_height:.0f}cm)",
                        VALIDATED_RULE_STATS["codex_heiwajima_r9_12_b1odds55_nige65_outertop2_wave3_h2_no1_top6"],
                        "展示後万舟率40%未満でも検証済みROIルールとして本命判定",
                        "1号艇は全消し",
                        "締切前複合評価順の上位6点固定",
                    ],
                    "odds_filter": "1号艇全消し6点。合成オッズ3倍未満なら見送り",
                },
            )
        )
    if (
        full_exhibition
        and place == "多摩川"
        and 4 <= round_no <= 6
        and b1_odds_rank == 1
        and b1_odds_pct >= 40
        and b1_venue_debuff
    ):
        strategies.append(
            (
                "codex_tamagawa_r4_6_b1odds40_venue_debuff_h2_ai13_no1_has56_12",
                "Codex多摩川本命: 4〜6R+1号艇人気+場別展示デバフ 1消し5/6絡み最大12点",
                tamagawa_r4_6_b1odds40_venue_debuff_h2_ai13_no1_has56_12,
                {
                    "tier": "venue_roi_core",
                    "rate_gate_exempt": True,
                    "entry_checks": [
                        "多摩川専用ROI条件:OK",
                        f"4〜6R限定:OK({round_no}R)",
                        f"1号艇オッズ評価1位:OK({b1_odds_rank}位)",
                        f"1号艇オッズ評価40%以上:OK({b1_odds_pct:.2f}%)",
                        "1号艇場別展示デバフ:OK",
                        *(b1_venue_debuff_reasons[:2] or []),
                        VALIDATED_RULE_STATS["codex_tamagawa_r4_6_b1odds40_venue_debuff_h2_ai13_no1_has56_12"],
                        "展示後万舟率40%未満でも検証済みROIルールとして本命判定",
                        "1号艇は全消し",
                        "5/6号艇絡みだけ",
                    ],
                    "odds_filter": "1号艇全消し。5/6絡み。合成オッズ3倍未満なら見送り",
                },
            )
        )
    if (
        full_exhibition
        and place == "浜名湖"
        and round_no <= 3
        and wave_height >= 2
        and low_ai_venue_revival
        and b1_avgdiff <= 0.0
        and outer56_avgdiff >= 0.05
    ):
        strategies.append(
            (
                "codex_hamanako_r1_3_wave2_revival_b1avg000_outer56avg005_outerh2_no1_has56_4",
                "Codex浜名湖本命: 前半波2cm以上+場別復活+1弱5/6浮上 1号艇消し4点",
                hamanako_r1_3_wave2_revival_b1avg000_outer56avg005_outerh2_no1_has56_4,
                {
                    "tier": "venue_roi_core",
                    "rate_gate_exempt": True,
                    "entry_checks": [
                        "浜名湖専用ROI条件:OK",
                        f"前半1〜3R:OK({round_no}R)",
                        f"波高2cm以上:OK({wave_height:.0f}cm)",
                        "低評価艇の場別展示復活バフ:OK",
                        *(low_ai_venue_revival_reasons[:2] or []),
                        f"1号艇 展示+1周平均との差0.00以下:OK({b1_avgdiff:+.2f})",
                        f"5/6号艇の良い方 展示+1周平均との差+0.05以上:OK({outer56_avgdiff:+.2f})",
                        VALIDATED_RULE_STATS["codex_hamanako_r1_3_wave2_revival_b1avg000_outer56avg005_outerh2_no1_has56_4"],
                        "展示後万舟率40%未満でも検証済みROIルールとして本命判定",
                        "1号艇は全消し",
                        "3〜6号艇の複合1着評価上位2艇を頭",
                        "5/6号艇絡みだけを上位4点",
                    ],
                    "odds_filter": "1号艇全消し。5/6絡み4点。合成オッズ3倍未満なら見送り",
                },
            )
        )
    if (
        full_exhibition
        and place == "蒲郡"
        and b1_odds_rank == 1
        and b1_odds_pct >= 35
        and b1_isshu_rank is not None
        and b1_isshu_rank >= 4
        and b1_loss_pct is not None
        and b1_loss_pct >= 30
    ):
        strategies.append(
            (
                "codex_gamagori_b1lap4_b1odds35_b1loss30_outer_h1_ai13_no1_has56_8",
                "Codex蒲郡本命: 1号艇人気+1周4位以下+逃げ失敗30%以上 外頭1艇5/6絡み",
                gamagori_b1lap4_b1odds35_b1loss30_outer_h1_ai13_no1_has56_8,
                {
                    "tier": "venue_roi_core",
                    "rate_gate_exempt": True,
                    "entry_checks": [
                        "蒲郡専用ROI条件:OK",
                        f"1号艇オッズ評価1位:OK({b1_odds_rank}位)",
                        f"1号艇オッズ評価35%以上:OK({b1_odds_pct:.2f}%)",
                        f"1号艇1周4位以下:OK({b1_isshu_rank:.0f}位)",
                        f"1号艇逃げ失敗30%以上:OK({b1_loss_pct:.2f}%)",
                        VALIDATED_RULE_STATS["codex_gamagori_b1lap4_b1odds35_b1loss30_outer_h1_ai13_no1_has56_8"],
                        "展示後万舟率40%未満でも検証済みROIルールとして本命判定",
                        "1号艇は全消し",
                        "外頭1艇",
                        "5/6号艇絡みだけ",
                    ],
                    "odds_filter": "1号艇全消し。外頭1艇。5/6絡み。合成オッズ3倍未満なら見送り",
                },
            )
        )
    if (
        full_exhibition
        and place == "常滑"
        and b1_loss_pct is not None
        and b1_loss_pct >= 40
        and b5_top3_rank is not None
        and b5_top3_rank <= 1
        and wind_speed >= 4
    ):
        strategies.append(
            (
                "codex_tokoname_b1loss40_b5top3rank1_wind4_h1_56_ai13_no1_has56_8",
                "Codex常滑本命: 1号艇逃げ失敗40%以上+5号艇複合3着内1位+風4m以上 5/6頭1艇",
                tokoname_b1loss40_b5top3rank1_wind4_h1_56_ai13_no1_has56_8,
                {
                    "tier": "venue_roi_core",
                    "rate_gate_exempt": True,
                    "entry_checks": [
                        "常滑専用ROI条件:OK",
                        f"1号艇逃げ失敗40%以上:OK({b1_loss_pct:.2f}%)",
                        f"5号艇複合3着内スコア1位:OK({b5_top3_rank}位)",
                        f"風速4m以上:OK({wind_speed:.0f}m)",
                        VALIDATED_RULE_STATS["codex_tokoname_b1loss40_b5top3rank1_wind4_h1_56_ai13_no1_has56_8"],
                        "展示後万舟率40%未満でも検証済みROIルールとして本命判定",
                        "1号艇は全消し",
                        "頭は5/6号艇から1艇",
                        "5/6号艇絡みだけ",
                    ],
                    "odds_filter": "1号艇全消し。5/6頭1艇。最大8点。合成オッズ3倍未満なら見送り",
                },
            )
        )
    if (
        full_exhibition
        and place == "津"
        and 4 <= round_no <= 8
        and any_venue_top3_buff_ge12
        and bool({5, 6} & set(roi_head_top2_boats))
    ):
        tsu_top3_buff_reason = venue_top3_buff_text(venue_top3_buff12_items[0])
        strategies.append(
            (
                "codex_tsu_r4_8_top3buff12_top2heads56_h1_top3_has56_8",
                "Codex津本命: 4〜8R+場別3着内バフ+頭候補5/6絡み 頭1艇最大8点",
                tsu_r4_8_top3buff12_top2heads56_h1_top3_has56_8,
                {
                    "tier": "venue_roi_core",
                    "rate_gate_exempt": True,
                    "entry_checks": [
                        "津専用ROI条件:OK",
                        f"中盤4〜8R:OK({round_no}R)",
                        f"場別3着内バフ12pt以上:OK({tsu_top3_buff_reason})",
                        f"頭候補上位2艇に5/6号艇:OK({','.join(map(str, roi_head_top2_boats[:2]))})",
                        VALIDATED_RULE_STATS["codex_tsu_r4_8_top3buff12_top2heads56_h1_top3_has56_8"],
                        "展示後万舟率40%未満でも検証済みROIルールとして本命判定",
                        "頭は非1号艇の最上位1艇",
                        "軸は複合3着内率上位2艇",
                        "5/6号艇絡みだけ",
                    ],
                    "odds_filter": "1号艇頭なし。複合3着内率上位2艇軸。5/6絡み。合成オッズ3倍未満なら見送り",
                },
            )
        )
    if (
        full_exhibition
        and place == "三国"
        and b1_odds_pct >= 55
        and outer56_avgdiff >= 0.30
        and wave_height >= 3
    ):
        strategies.append(
            (
                "codex_mikuni_big50_a_b1odds55_o56avg030_wave3_h1_ai13_has56_8",
                "Codex三国5万舟警戒A: 1号艇人気+5/6足色強化+波3cm 非1頭1艇",
                mikuni_big50_a_h1_ai13_has56_8,
                {
                    "tier": "big50_warning_sign",
                    "rate_gate_exempt": True,
                    "entry_checks": [
                        "三国5万舟警戒A条件:OK",
                        f"1号艇オッズ評価55%以上:OK({b1_odds_pct:.2f}%)",
                        f"5/6号艇平均との差+0.30以上:OK({outer56_avgdiff:+.2f})",
                        f"波高3cm以上:OK({wave_height:.0f}cm)",
                        VALIDATED_RULE_STATS["codex_mikuni_big50_a_b1odds55_o56avg030_wave3_h1_ai13_has56_8"],
                        "5万舟警戒サインのため展示後率に関係なく通知対象",
                        "頭は非1号艇の最上位1艇",
                        "軸はAI+1位/3位",
                        "5/6号艇絡みだけ",
                    ],
                    "odds_filter": "1号艇は頭で買わない。AI+1位/3位軸。5/6絡み。最大8点。合成オッズ3倍未満なら見送り",
                },
            )
        )
    if (
        full_exhibition
        and place == "三国"
        and b1_odds_pct >= 60
        and b1_start_tenji_rank is not None
        and b1_start_tenji_rank >= 6
        and outer56_avgdiff >= 0.30
    ):
        strategies.append(
            (
                "codex_mikuni_big50_b_b1odds60_st6_o56avg030_box3_comp_has56_6",
                "Codex三国5万舟警戒B: 1号艇強人気+ST展示6位+5/6足色強化 BOX6点",
                mikuni_big50_b_box3_comp_has56_6,
                {
                    "tier": "big50_warning_sign",
                    "rate_gate_exempt": True,
                    "entry_checks": [
                        "三国5万舟警戒B条件:OK",
                        f"1号艇オッズ評価60%以上:OK({b1_odds_pct:.2f}%)",
                        f"1号艇ST展示6位以下:OK({b1_start_tenji_rank:.0f}位)",
                        f"5/6号艇平均との差+0.30以上:OK({outer56_avgdiff:+.2f})",
                        VALIDATED_RULE_STATS["codex_mikuni_big50_b_b1odds60_st6_o56avg030_box3_comp_has56_6"],
                        "5万舟警戒サインのため展示後率に関係なく通知対象",
                        "複合上位3艇BOX",
                        "5/6号艇絡みだけ",
                    ],
                    "odds_filter": "複合上位3艇BOX6点。5/6絡み。合成オッズ3倍未満なら見送り",
                },
            )
        )
    if (
        full_exhibition
        and place == "三国"
        and 9 <= round_no <= 12
        and wind_speed >= 5
        and low_ai_venue_revival
    ):
        strategies.append(
            (
                "codex_mikuni_r9_12_wind5_lowai_h2_ai13_no1_has56_12",
                "Codex三国本命: 後半強風+低AI艇復活 1消し5/6絡み",
                mikuni_r9_12_wind5_lowai_h2_ai13_no1_has56_12,
                {
                    "tier": "venue_roi_core",
                    "rate_gate_exempt": True,
                    "entry_checks": [
                        "三国専用ROI条件:OK",
                        f"後半9〜12R:OK({round_no}R)",
                        f"風速5m以上:OK({wind_speed:.0f}m)",
                        "低評価艇の場別展示復活バフ:OK",
                        *(low_ai_venue_revival_reasons[:2] or []),
                        VALIDATED_RULE_STATS["codex_mikuni_r9_12_wind5_lowai_h2_ai13_no1_has56_12"],
                        "展示後万舟率40%未満でも検証済みROIルールとして本命判定",
                        "1号艇は全消し",
                        "頭は非1号艇の上位2艇",
                        "AI+1位/3位を軸",
                        "5/6号艇絡みだけ",
                    ],
                    "odds_filter": "1号艇全消し。非1号艇上位2艇頭。AI+1位/3位軸。5/6絡み。合成オッズ3倍未満なら見送り",
                },
            )
        )
    if (
        full_exhibition
        and place == "びわこ"
        and any_venue_top3_buff_ge15
        and low_ai_venue_revival
    ):
        strategies.append(
            (
                "codex_biwako_top3buff15_lowai_box3_has56_6",
                "Codexびわこ本命: 強場別3着内バフ+低AI復活 3艇BOX6点",
                biwako_top3buff15_lowai_box3_has56_6,
                {
                    "tier": "venue_roi_core",
                    "rate_gate_exempt": True,
                    "entry_checks": [
                        "びわこ専用ROI条件:OK",
                        "場別3着内バフ+15pt以上:OK",
                        *venue_strong_buff_reasons[:2],
                        "低評価艇の場別展示復活バフ:OK",
                        *(low_ai_venue_revival_reasons[:2] or []),
                        VALIDATED_RULE_STATS["codex_biwako_top3buff15_lowai_box3_has56_6"],
                        "展示後万舟率40%未満でも検証済みROIルールとして本命判定",
                        "複合上位3艇BOX6点",
                        "5/6号艇絡みだけ",
                    ],
                    "odds_filter": "複合上位3艇BOX6点。5/6絡み。合成オッズ3倍未満なら見送り",
                },
            )
        )
    if (
        full_exhibition
        and place == "住之江"
        and b1_avgdiff <= -0.10
        and b1_tenji_rank is not None
        and b1_tenji_rank >= 5
    ):
        strategies.append(
            (
                "codex_suminoe_b1tenji5_avg010_h2_top3_no1_has56_12",
                "Codex住之江本命: 1号艇展示5位以下+平均との差悪化 1消し5/6絡み",
                suminoe_b1tenji5_avg010_h2_top3_no1_has56_12,
                {
                    "tier": "venue_roi_core",
                    "rate_gate_exempt": True,
                    "entry_checks": [
                        "住之江専用ROI条件:OK",
                        f"1号艇平均との差-0.10以下:OK({b1_avgdiff:+.2f})",
                        f"1号艇展示5位以下:OK({b1_tenji_rank:.0f}位)",
                        VALIDATED_RULE_STATS["codex_suminoe_b1tenji5_avg010_h2_top3_no1_has56_12"],
                        "展示後万舟率40%未満でも検証済みROIルールとして本命判定",
                        "1号艇は全消し",
                        "5/6号艇絡みだけ",
                    ],
                    "odds_filter": "1号艇全消し。5/6絡み。合成オッズ3倍未満なら見送り",
                },
            )
        )
    if (
        full_exhibition
        and place == "尼崎"
        and round_no <= 8
        and b1_avgdiff <= -0.10
        and outer56_avgdiff >= 0.50
    ):
        strategies.append(
            (
                "codex_amagasaki_r1_8_b1avg010_outer56avg050_h2_top3_no1_has56_12",
                "Codex尼崎本命: 1〜8R+1号艇足色弱化+5/6足色強化 1消し5/6絡み",
                amagasaki_r1_8_b1avg010_outer56avg050_h2_top3_no1_has56_12,
                {
                    "tier": "venue_roi_core",
                    "rate_gate_exempt": True,
                    "entry_checks": [
                        "尼崎専用ROI条件:OK",
                        f"1〜8R:OK({round_no}R)",
                        f"1号艇平均との差-0.10以下:OK({b1_avgdiff:+.2f})",
                        f"5/6号艇平均との差+0.50以上:OK({outer56_avgdiff:+.2f})",
                        VALIDATED_RULE_STATS["codex_amagasaki_r1_8_b1avg010_outer56avg050_h2_top3_no1_has56_12"],
                        "展示後万舟率40%未満でも検証済みROIルールとして本命判定",
                        "1号艇は全消し",
                        "5/6号艇絡みだけ",
                    ],
                    "odds_filter": "1号艇全消し。5/6絡み。合成オッズ3倍未満なら見送り",
                },
            )
        )
    if (
        full_exhibition
        and place == "鳴門"
        and round_no >= 7
        and wave_height >= 3
        and any_venue_top3_buff_ge10
        and b1_odds_rank == 1
    ):
        strategies.append(
            (
                "codex_naruto_r7_12_wave3_b1odds1_top3buff10_h2_top3_no1_has56_12",
                "Codex鳴門本命: 後半+波3cm以上+1号艇人気+場別3着内バフ 1消し5/6絡み",
                naruto_r7_12_wave3_b1odds1_top3buff10_h2_top3_no1_has56_12,
                {
                    "tier": "venue_roi_core",
                    "rate_gate_exempt": True,
                    "entry_checks": [
                        "鳴門専用ROI条件:OK",
                        f"後半7〜12R:OK({round_no}R)",
                        f"波高3cm以上:OK({wave_height:.0f}cm)",
                        "場別3着内バフ+10pt以上:OK",
                        *venue_top3_buff10_reasons[:2],
                        f"1号艇オッズ評価1位:OK({b1_odds_rank}位)",
                        f"1号艇オッズ評価: {b1_odds_pct:.2f}%",
                        VALIDATED_RULE_STATS["codex_naruto_r7_12_wave3_b1odds1_top3buff10_h2_top3_no1_has56_12"],
                        "展示後万舟率40%未満でも検証済みROIルールとして本命判定",
                        "1号艇は全消し",
                        "5/6号艇絡みだけ",
                    ],
                    "odds_filter": "1号艇全消し。5/6絡み。合成オッズ3倍未満なら見送り",
                },
            )
        )
    if (
        full_exhibition
        and place == "丸亀"
        and 4 <= round_no <= 8
        and b1_loss_pct is not None
        and b1_loss_pct >= 45
        and b5_top3_rank is not None
        and b5_top3_rank <= 1
    ):
        strategies.append(
            (
                "codex_marugame_r4_8_b1loss45_b5top3rank1_h1_56_ai13_no1_has56_8",
                "Codex丸亀本命: 4〜8R+1号艇逃げ失敗45%以上+5号艇複合3着内1位 1消し5/6絡み",
                marugame_r4_8_b1loss45_b5top3rank1_h1_56_ai13_no1_has56_8,
                {
                    "tier": "venue_roi_core",
                    "rate_gate_exempt": True,
                    "entry_checks": [
                        "丸亀専用ROI条件:OK",
                        f"4〜8R:OK({round_no}R)",
                        f"1号艇逃げ失敗45%以上:OK({b1_loss_pct:.2f}%)",
                        f"5号艇複合3着内スコア1位:OK({b5_top3_rank}位)",
                        VALIDATED_RULE_STATS["codex_marugame_r4_8_b1loss45_b5top3rank1_h1_56_ai13_no1_has56_8"],
                        "展示後万舟率40%未満でも検証済みROIルールとして本命判定",
                        "1号艇は全消し",
                        "5/6号艇絡みだけ",
                    ],
                    "odds_filter": "1号艇全消し。5/6絡み最大8点。合成オッズ3倍未満なら見送り",
                },
            )
        )
    if (
        full_exhibition
        and place == "児島"
        and b1_odds_rank == 1
        and b1_avgdiff <= -0.05
        and b1_tenji_signal_rank is not None
        and b1_tenji_signal_rank >= 4
        and outer56_avgdiff >= 0.40
    ):
        strategies.append(
            (
                "codex_kojima_b1odds1_b1avg005_b1tenji4_outer56avg040_h1_ai13_no1_has56_8",
                "Codex児島本命: 1号艇人気1位+平均との差-0.05以下+展示4位以下+5/6平均との差+0.40以上 1消し5/6絡み",
                kojima_b1odds1_b1avg005_b1tenji4_outer56avg040_h1_ai13_no1_has56_8,
                {
                    "tier": "venue_roi_core",
                    "rate_gate_exempt": True,
                    "entry_checks": [
                        "児島専用ROI条件:OK",
                        f"1号艇オッズ評価1位:OK({b1_odds_rank}位)",
                        f"1号艇平均との差-0.05以下:OK({b1_avgdiff:+.2f})",
                        f"1号艇展示4位以下:OK({b1_tenji_signal_rank:.0f}位)",
                        f"5/6号艇平均との差+0.40以上:OK({outer56_avgdiff:+.2f})",
                        VALIDATED_RULE_STATS["codex_kojima_b1odds1_b1avg005_b1tenji4_outer56avg040_h1_ai13_no1_has56_8"],
                        "展示後万舟率40%未満でも検証済みROIルールとして本命判定",
                        "1号艇は全消し",
                        "5/6号艇絡みだけ",
                    ],
                    "odds_filter": "1号艇全消し。5/6絡み最大8点。合成オッズ3倍未満なら見送り",
                },
            )
        )
    if (
        full_exhibition
        and place == "宮島"
        and round_no <= 3
        and b1_odds_rank <= 3
        and b1_isshu_rank is not None
        and b1_isshu_rank >= 5
        and b1_tenji_signal_rank is not None
        and b1_tenji_signal_rank >= 5
    ):
        strategies.append(
            (
                "codex_miyajima_r1_3_b1odds3_b1lap5_b1tenji5_h2_ai13_no1_has56_12",
                "Codex宮島本命: 前半+1号艇オッズ3位以内+展示5位以下+1周5位以下 AI軸1消し5/6絡み",
                miyajima_r1_3_b1odds3_b1lap5_b1tenji5_h2_ai13_no1_has56_12,
                {
                    "tier": "venue_roi_core",
                    "rate_gate_exempt": True,
                    "entry_checks": [
                        "宮島専用ROI条件:OK",
                        f"前半1〜3R:OK({round_no}R)",
                        f"1号艇オッズ評価3位以内:OK({b1_odds_rank}位)",
                        f"1号艇展示5位以下:OK({b1_tenji_signal_rank:.0f}位)",
                        f"1号艇1周5位以下:OK({b1_isshu_rank:.0f}位)",
                        VALIDATED_RULE_STATS["codex_miyajima_r1_3_b1odds3_b1lap5_b1tenji5_h2_ai13_no1_has56_12"],
                        "展示後万舟率40%未満でも検証済みROIルールとして本命判定",
                        "1号艇は全消し",
                        "AI+1位/3位を軸",
                        "5/6号艇絡みだけ",
                    ],
                    "odds_filter": "1号艇全消し。AI+1位/3位軸。5/6絡み。合成オッズ3倍未満なら見送り",
                },
            )
        )
    if (
        full_exhibition
        and place == "徳山"
        and 4 <= round_no <= 8
        and b1_odds_rank <= 2
        and b1_odds_pct >= 30
        and b1_isshu_rank is not None
        and b1_isshu_rank >= 4
    ):
        strategies.append(
            (
                "codex_tokuyama_r4_8_b1odds2_pct30_b1lap4_h1_ai13_no1_8",
                "Codex徳山本命: 4〜8R+1号艇オッズ2位以内30%以上+1周4位以下 頭1艇AI軸1消し",
                tokuyama_r4_8_b1odds2_pct30_b1lap4_h1_ai13_no1_8,
                {
                    "tier": "venue_roi_core",
                    "rate_gate_exempt": True,
                    "entry_checks": [
                        "徳山専用ROI条件:OK",
                        f"4〜8R:OK({round_no}R)",
                        f"1号艇オッズ評価2位以内:OK({b1_odds_rank}位)",
                        f"1号艇オッズ評価30%以上:OK({b1_odds_pct:.2f}%)",
                        f"1号艇1周4位以下:OK({b1_isshu_rank:.0f}位)",
                        VALIDATED_RULE_STATS["codex_tokuyama_r4_8_b1odds2_pct30_b1lap4_h1_ai13_no1_8"],
                        "展示後万舟率40%未満でも検証済みROIルールとして本命判定",
                        "1号艇は全消し",
                        "頭は1艇だけ",
                        "AI+1位/3位を軸",
                    ],
                    "odds_filter": "1号艇全消し。頭1艇+AI+1位/3位軸。合成オッズ3倍未満なら見送り",
                },
            )
        )
    if (
        full_exhibition
        and place == "下関"
        and round_no <= 6
        and outer56_avgdiff >= 0.10
        and b1_odds_rank == 1
        and b1_odds_pct >= 50
    ):
        strategies.append(
            (
                "codex_shimonoseki_r1_6_outer56avg010_b1odds50_h2_top3_no1_has56_12",
                "Codex下関本命: 前半+5/6平均との差上昇+1号艇強人気 1消し5/6絡み",
                shimonoseki_r1_6_outer56avg010_b1odds50_h2_top3_no1_has56_12,
                {
                    "tier": "venue_roi_core",
                    "rate_gate_exempt": True,
                    "entry_checks": [
                        "下関専用ROI条件:OK",
                        f"前半1〜6R:OK({round_no}R)",
                        f"5/6号艇平均との差+0.10以上:OK({outer56_avgdiff:+.2f})",
                        f"1号艇オッズ評価1位:OK({b1_odds_rank}位)",
                        f"1号艇オッズ評価50%以上:OK({b1_odds_pct:.2f}%)",
                        VALIDATED_RULE_STATS["codex_shimonoseki_r1_6_outer56avg010_b1odds50_h2_top3_no1_has56_12"],
                        "展示後万舟率40%未満でも検証済みROIルールとして本命判定",
                        "1号艇は全消し",
                        "軸は複合3着内率上位2艇",
                        "5/6号艇絡みだけ",
                    ],
                    "odds_filter": "1号艇全消し。複合3着内率上位2艇軸。5/6絡み。合成オッズ3倍未満なら見送り",
                },
            )
        )
    if (
        full_exhibition
        and place == "若松"
        and 4 <= round_no <= 8
        and b1_odds_rank == 1
        and b1_odds_pct >= 45
        and roi_head_top_boat in {5, 6}
    ):
        strategies.append(
            (
                "codex_wakamatsu_r4_8_head56_b1odds45_h2_ai13_no1_has56_12",
                "Codex若松本命: 4〜8R+1号艇強人気+頭候補5/6最上位 1消し5/6絡み",
                wakamatsu_r4_8_head56_b1odds45_h2_ai13_no1_has56_12,
                {
                    "tier": "venue_roi_core",
                    "rate_gate_exempt": True,
                    "entry_checks": [
                        "若松専用ROI条件:OK",
                        f"中盤4〜8R:OK({round_no}R)",
                        f"1号艇オッズ評価1位:OK({b1_odds_rank}位)",
                        f"1号艇オッズ評価45%以上:OK({b1_odds_pct:.2f}%)",
                        f"頭候補最上位が5/6号艇:OK({roi_head_top_boat}号艇)",
                        VALIDATED_RULE_STATS["codex_wakamatsu_r4_8_head56_b1odds45_h2_ai13_no1_has56_12"],
                        "展示後万舟率40%未満でも検証済みROIルールとして本命判定",
                        "1号艇は全消し",
                        "AI+1位/3位を軸",
                        "5/6号艇絡みだけ",
                    ],
                    "odds_filter": "1号艇全消し。AI+1位/3位軸。5/6絡み。合成オッズ3倍未満なら見送り",
                },
            )
        )
    if (
        full_exhibition
        and place == "福岡"
        and 9 <= round_no <= 12
        and b1_isshu_rank is not None
        and b1_isshu_rank >= 4
        and any_venue_top3_buff_ge12
    ):
        fukuoka_top3_buff_reason = venue_top3_buff_text(venue_top3_buff12_items[0])
        strategies.append(
            (
                "codex_fukuoka_r9_12_b1lap4_top3buff12_h1_ai13_has56_8",
                "Codex福岡本命: 後半9〜12R+1号艇1周4位以下+場別3着内バフ 1頭消し5/6絡み",
                fukuoka_r9_12_b1lap4_top3buff12_h1_ai13_has56_8,
                {
                    "tier": "venue_roi_core",
                    "rate_gate_exempt": True,
                    "entry_checks": [
                        "福岡専用ROI条件:OK",
                        f"後半9〜12R:OK({round_no}R)",
                        f"1号艇1周4位以下:OK({b1_isshu_rank:.0f}位)",
                        f"場別3着内バフ12pt以上:OK({fukuoka_top3_buff_reason})",
                        VALIDATED_RULE_STATS["codex_fukuoka_r9_12_b1lap4_top3buff12_h1_ai13_has56_8"],
                        "展示後万舟率40%未満でも検証済みROIルールとして本命判定",
                        "1号艇は頭だけ消し",
                        "頭は非1号艇の最上位1艇",
                        "AI+1位/3位を軸",
                        "5/6号艇絡みだけ",
                    ],
                    "odds_filter": "1号艇頭なし。AI+1位/3位軸。5/6絡み。合成オッズ3倍未満なら見送り",
                },
            )
        )
    if (
        full_exhibition
        and place == "唐津"
        and b1_odds_rank == 1
        and b1_odds_pct >= 45
        and b1_loss_pct is not None
        and b1_loss_pct >= 45
        and any_venue_top3_buff_ge10
    ):
        karatsu_top3_buff_reason = venue_top3_buff_text(venue_top3_buff10_items[0])
        strategies.append(
            (
                "codex_karatsu_b1loss45_top3buff10_b1odds45_h1_top3_8",
                "Codex唐津本命: 1号艇強人気+逃げ失敗45%以上+場別3着内バフ 頭1艇最大8点",
                karatsu_b1loss45_top3buff10_b1odds45_h1_top3_8,
                {
                    "tier": "venue_roi_core",
                    "rate_gate_exempt": True,
                    "entry_checks": [
                        "唐津専用ROI条件:OK",
                        f"1号艇オッズ評価1位:OK({b1_odds_rank}位)",
                        f"1号艇オッズ評価45%以上:OK({b1_odds_pct:.2f}%)",
                        f"1号艇逃げ失敗45%以上:OK({b1_loss_pct:.2f}%)",
                        f"場別3着内バフ10pt以上:OK({karatsu_top3_buff_reason})",
                        VALIDATED_RULE_STATS["codex_karatsu_b1loss45_top3buff10_b1odds45_h1_top3_8"],
                        "展示後万舟率40%未満でも検証済みROIルールとして本命判定",
                        "頭は非1号艇の最上位1艇",
                        "軸は複合3着内率上位2艇",
                        "1号艇は2・3着に残す",
                    ],
                    "odds_filter": "1号艇頭なし。複合3着内率上位2艇軸。合成オッズ3倍未満なら見送り",
                },
            )
        )
    if (
        full_exhibition
        and place == "大村"
        and any_venue_head_buff_ge8
        and low_ai_venue_revival
        and outer56_avgdiff >= 0.35
        and b1_tenji_rank is not None
        and b1_tenji_rank >= 4
    ):
        omura_head_buff_reason = venue_head_buff_text(venue_head_buff_items(rows, min_pp=8.0)[0])
        strategies.append(
            (
                "codex_omura_headbuff8_lowai_outer56avg020_h1_ai13_has56_8",
                "Codex大村本命: 5/6足色強化+1号艇展示弱化 頭1艇5/6絡み",
                omura_headbuff8_lowai_outer56avg020_h1_ai13_has56_8,
                {
                    "tier": "venue_roi_core",
                    "rate_gate_exempt": True,
                    "entry_checks": [
                        "大村専用ROI条件:OK",
                        f"場別頭バフ8pt以上:OK({omura_head_buff_reason})",
                        "低評価艇の場別展示復活バフ:OK",
                        *(low_ai_venue_revival_reasons[:2] or []),
                        f"5/6号艇平均との差+0.35以上:OK({outer56_avgdiff:+.2f})",
                        f"1号艇展示4位以下:OK({b1_tenji_rank:.0f}位)",
                        VALIDATED_RULE_STATS["codex_omura_headbuff8_lowai_outer56avg020_h1_ai13_has56_8"],
                        "展示後万舟率40%未満でも検証済みROIルールとして本命判定",
                        "頭は非1号艇の最上位1艇",
                        "AI+1位/3位を軸",
                        "5/6号艇絡みだけ",
                    ],
                    "odds_filter": "1号艇頭なし。AI+1位/3位軸。5/6絡み。合成オッズ3倍未満なら見送り",
                },
            )
        )
    if (
        full_exhibition
        and place == "芦屋"
        and wave_height >= 3
        and b1_avgdiff <= -0.05
        and any_venue_top3_buff_ge10
    ):
        ashiya_top3_buff_reason = venue_top3_buff_text(venue_top3_buff10_items[0])
        strategies.append(
            (
                "codex_ashiya_wave_b1weak_top3buff10_outer_h1_ai13_no1_has56_8",
                "Codex芦屋本命: 波3cm以上+1号艇平均との差悪化+場別3着内バフ 外頭1艇 2〜8点",
                ashiya_wave_b1weak_top3buff10_outer_h1_ai13_no1_has56_8,
                {
                    "tier": "venue_roi_core",
                    "rate_gate_exempt": True,
                    "entry_checks": [
                        "芦屋専用ROI条件:OK",
                        f"波高3cm以上:OK({wave_height:.0f}cm)",
                        f"1号艇平均との差-0.05以下:OK({b1_avgdiff:+.2f})",
                        f"場別3着内バフ10pt以上:OK({ashiya_top3_buff_reason})",
                        VALIDATED_RULE_STATS["codex_ashiya_wave_b1weak_top3buff10_outer_h1_ai13_no1_has56_8"],
                        "展示後万舟率40%未満でも検証済みROIルールとして本命判定",
                        "頭は3〜6号艇の最上位1艇",
                        "1号艇は全消し",
                        "5/6号艇絡みだけ",
                    ],
                    "odds_filter": "1号艇全消し。5/6絡み。合成オッズ3倍未満なら見送り",
                },
            )
        )
    if full_exhibition and post_rate >= CORE_ALERT_RATE and b1_odds_gap_strong(metrics):
        strategies.append(
            (
                "codex_odds_gap_b1_fade_strong12",
                f"Codex歪み強本命: 1号艇{b1_popularity_level}+危険+展示Wデバフ 5〜12点",
                odds_gap_b1_fade_strong12,
                {
                    "tier": "core_odds_gap",
                    "entry_checks": [
                        f"展示後40%以上:OK({post_rate:.2f}%)",
                        VALIDATED_RULE_STATS["codex_odds_gap_b1_fade_strong12"],
                        f"1号艇人気レベル: {b1_popularity_level}",
                        "データ上は1号艇危険",
                        "1号艇が展示タイム・1周タイムとも上位3外",
                        "5/6号艇は必須にしない",
                    ],
                    "odds_filter": "1号艇頭は買わない。低配当ではなく世間とデータの歪みを狙う",
                },
            )
        )
    if (
        full_exhibition
        and post_rate >= CORE_ALERT_RATE
        and not b1_odds_gap_strong(metrics)
        and b1_odds_gap_filtered(metrics, round_no)
        and rank6_power_ok
    ):
        b1_tenji_rank = valid_boat_rank(metrics.get("boat1_tenji_time_rank") or metrics.get("boat1_tenji_rank"))
        b1_isshu_rank = valid_boat_rank(metrics.get("boat1_isshu_rank"))
        b1_avg_diff = as_num(metrics.get("boat1_avg_isshu_diff"))
        tenji_text = f"{b1_tenji_rank:.0f}位" if b1_tenji_rank is not None else "不明"
        isshu_text = f"{b1_isshu_rank:.0f}位" if b1_isshu_rank is not None else "不明"
        avg_text = f"{b1_avg_diff:+.2f}" if b1_avg_diff is not None else "不明"
        strategies.append(
            (
                "codex_odds_gap_b1_fade_filtered12",
                f"Codex歪み参考: 1号艇{b1_popularity_level}+危険+前半展示弱化 5〜12点",
                odds_gap_b1_fade_filtered12,
                {
                    "tier": "core_odds_gap_filtered",
                    "entry_checks": [
                        f"展示後40%以上:OK({post_rate:.2f}%)",
                        VALIDATED_RULE_STATS["codex_odds_gap_b1_fade_filtered12"],
                        f"前半1〜6R:OK({round_no}R)",
                        f"1号艇人気レベル: {b1_popularity_level}",
                        "データ上は1号艇危険",
                        f"1号艇の展示または1周が4位以下:OK(展示{tenji_text}/1周{isshu_text})",
                        f"1号艇の展示+1周平均との差がマイナス:OK({avg_text})",
                        "AI最下位艇が展示または1周で2位以内に浮上:OK",
                        "買い目の万舟的中が未達のため本命買いからは除外",
                    ],
                    "odds_filter": "1号艇頭は買わない。人気1号艇をデータで疑える時だけ狙う",
                },
            )
        )
    if full_exhibition and post_rate >= CORE_ALERT_RATE and round_no <= 3:
        if b1_publicly_backed(metrics):
            strategies.append(
                (
                    "codex_post_core_front_head2_no1_outer56",
                    "Codex参考: 前半1〜3R+人気1号艇消し+外頭2番手+5/6絡み",
                    core_40_focus_head2_no1_outer56,
                    {
                        "tier": "core_focus",
                        "entry_checks": [
                            f"展示後40%以上:OK({post_rate:.2f}%)",
                            f"前半1〜3R:OK({round_no}R)",
                            f"1号艇人気レベル:OK({b1_popularity_level})",
                            "頭は外頭2艇の2番手だけ",
                            "1号艇を買い目から外す",
                            "5/6号艇が買い目に絡む",
                        ],
                        "odds_filter": "人気1号艇をデータで疑える時だけ、1号艇頭と低配当形を買わない",
                    },
                )
            )
        elif not b1_head_value:
            metrics["core_front_no1_odds_blocked"] = True
            metrics["core_front_no1_odds_block_reason"] = (
                f"1号艇が{b1_popularity_level}なので、1号艇を飛ばしても配当が伸びにくい。"
                "1号艇頭で買える強い根拠がある時だけ別枠で検討"
            )
    if full_exhibition and post_rate >= CORE_ALERT_RATE:
        strategies.append(
            (
                "codex_post_core_rate40",
                "Codex参考: 展示後40%以上 外頭2艇+AI+一般2位3位軸 12点",
                core_40_arunashi12,
                {
                    "tier": "core_reference",
                    "entry_checks": [f"展示後40%以上:OK({post_rate:.2f}%)"],
                },
            )
        )
    elif full_exhibition and SUBCORE_ALERT_RATE_MIN <= post_rate < CORE_ALERT_RATE:
        subcore_ok, subcore_checks = subcore_entry_checks(race, metrics, rows)
        if subcore_ok:
            strategies.append(
                (
                    "codex_post_subcore_rate38_conditions",
                    "Codex参考: 38〜39.9%+1危険+外頭2艇(5/6含む)+内軸残り 12点",
                    subcore_38_arunashi12,
                    {
                        "tier": "subcore",
                        "entry_checks": subcore_checks,
                    },
                )
            )
    post_core_a = (
        full_exhibition
        and not b1_summer_fast
        and rank_no <= 3
        and b1_ai_pred < 30
        and outer56_ai_pred >= 12
        and outer36_double_time
    )
    post_core_b = (
        full_exhibition
        and not b1_summer_fast
        and rank_no <= 3
        and round_no <= 6
        and b1_ai_pred < 30
        and outer36_ai_plus_top1
        and metrics.get("super_slit_alert_count", 0) >= 1
    )
    if post_core_a or post_core_b:
        strategies.append(
            (
                "codex_post_core_ab_rank3",
                "Codex直前参考: 朝TOP3+1AI30未満+外上昇A/B 10〜15点",
                codex_logic29_outer_required,
            )
        )
    subcore_rank6_outer_exhibit = (
        full_exhibition
        and not b1_summer_fast
        and not (post_core_a or post_core_b)
        and rank_no <= 5
        and rank6_boat in {5, 6}
        and rank6_ai_pred >= 5
        and (rank6_exhibit_top2 or metrics.get("outer56_tenji_top2_count", 0) >= 1)
    )
    if subcore_rank6_outer_exhibit:
        strategies.append(
            (
                "codex_post_subcore_rank6_outer_exhibit_top2",
                "Codex参考B: AI+最下位5/6が展示浮上 監視",
                codex_logic29_outer_required,
            )
        )
    popular_verified_conditions = [
        item
        for item in (metrics.get("popular_b1_matched_conditions") or [])
        if str(item.get("id") or "").startswith("codex_popular_b1_verified")
    ]
    if (
        full_exhibition
        and not b1_summer_fast
        and popular_verified_conditions
        and (metrics.get("popular_b1_fly_score") or 0) >= 60
    ):
        strategies.append(
            (
                "codex_popular_b1_exhibition_fly_watch",
                "Codex参考C: 人気1号艇が展示で危険 監視",
                codex_logic29_outer_required,
            )
        )
    # These post-data signals are still used by the ranking lift model, but the
    # long backtest showed that buying all of them is too broad.
    allow_exploratory_post_strategies = False
    base_tickets, base_roles = super_arunashi3(rows)
    late_outer_head_keshi_signal = (
        rank_no <= 7
        and place != "宮島"
        and round_no >= 10
        and (race.get("manshu_rate_pct") or 0) >= 27
        and bool(base_tickets)
        and base_roles is not None
        and set(base_roles.get("heads") or []).issubset({3, 4, 5, 6})
        and len(base_roles.get("heads") or []) == 2
        and int(base_roles.get("keshi") or 0) in {3, 4, 5, 6}
        and 10 <= len(base_tickets) <= 15
    )
    if late_outer_head_keshi_signal:
        strategies.append(
            (
                "codex_late_outer_head_keshi15",
                "Codex参考型: TOP7 10〜12R 宮島除外 外頭2艇+外消し AI3軸 10〜15点",
                super_arunashi3,
            )
        )
    if (
        round_no <= 3
        and (metrics.get("boat1_nige_pct") or 999) < 40
        and (metrics.get("outer56_best_ai_prediction_pct") or -1) >= 12
        and (metrics.get("ai_rank6_tenji_rank") or 9) <= 2
        and (metrics.get("ai_rank5_tenji_rank") or 9) <= 2
        and metrics.get("tenji_boats", 0) >= 6
        and metrics.get("isshu_boats", 0) >= 6
    ):
        strategies.append(
            (
                "codex_rank56_exhibit10",
                "Codex安定型: AI+下位展示浮上 前半10点",
                codex_rank56_exhibit10,
            )
        )
    if (
        round_no <= 3
        and (metrics.get("boat1_loss_pct") or -1) >= 45
        and (metrics.get("boat1_ai_prediction_pct") or 999) < 25
        and (metrics.get("outer56_best_ai_prediction_pct") or -1) >= 12
        and wind_wave
        and metrics.get("tenji_boats", 0) >= 6
        and metrics.get("isshu_boats", 0) >= 6
    ):
        strategies.append(
            (
                "codex_stable_front_wind11",
                "Codex安定型: 1弱+5/6AI+風波 前半10〜15点",
                codex_stable_front_wind11,
            )
        )
    if allow_exploratory_post_strategies and full_exhibition and not b1_summer_fast and outer56_ai_pred >= 10 and outer56_ai_plus >= 100 and metrics.get("outer56_isshu_top2_count", 0) >= 1:
        strategies.append(
            (
                "codex_post_outer56_ai10_aiplus100_isshu2",
                "Codex直前上げ: 5/6AI10%+AI+100+1周2位以内",
                codex_logic29_outer_required,
            )
        )
    if allow_exploratory_post_strategies and full_exhibition and not b1_summer_fast and b1_ai_pred < 30 and outer56_ai_pred >= 10 and rank6_boat in {5, 6} and rank6_exhibit_top2:
        strategies.append(
            (
                "codex_post_b1aipred30_outer10_rank6exh",
                "Codex直前上げ: 1AI30未満+5/6AI10+AI+最下位5/6展示浮上",
                codex_logic29_outer_required,
            )
        )
    if allow_exploratory_post_strategies and full_exhibition and not b1_summer_fast and b1_ai_pred < 30 and outer36_ai_plus_top1 and metrics.get("super_slit_alert_count", 0) >= 1:
        strategies.append(
            (
                "codex_post_b1aipred30_outeraiplus1_superslit",
                "Codex直前上げ: 1AI30未満+外AI+1位+スーパースリット",
                codex_logic29_outer_required,
            )
        )
    if allow_exploratory_post_strategies and full_exhibition and not b1_summer_fast and outer56_ai_pred >= 12 and outer56_avgdiff >= 0.10 and outer36_double_time:
        strategies.append(
            (
                "codex_post_outer56_ai12_avg010_outerdouble",
                "Codex直前強上げ: 5/6AI12+平均との差0.10+外ダブルタイム",
                codex_logic29_outer_required,
            )
        )
    if allow_exploratory_post_strategies and full_exhibition and not b1_summer_fast and b1_ai_pred < 30 and outer56_ai_pred >= 12 and outer36_double_time:
        strategies.append(
            (
                "codex_post_b1aipred30_outer56_ai12_outerdouble",
                "Codex直前強上げ: 1AI30未満+5/6AI12+外ダブルタイム",
                codex_logic29_outer_required,
            )
        )
    if allow_exploratory_post_strategies and full_exhibition and not b1_summer_fast and outer56_ai_pred >= 10 and outer36_ai_pred_top1 and b1_avgdiff <= 0:
        strategies.append(
            (
                "codex_post_outer56_ai10_outerhead_b1avg0",
                "Codex直前強上げ: 5/6AI10+外AI頭1位+1平均との差0以下",
                codex_logic29_outer_required,
            )
        )
    if allow_exploratory_post_strategies and full_exhibition and not b1_summer_fast and rank6_boat in {5, 6} and rank6_ai_pred >= 5 and metrics.get("outer56_tenji_top2_count", 0) >= 1:
        strategies.append(
            (
                "codex_post_rank6_outer_ai5_outertenji2",
                "Codex直前上げ: AI+最下位5/6がAI5%+外展示2位以内",
                codex_logic29_outer_required,
            )
        )
    if allow_exploratory_post_strategies and full_exhibition and not b1_summer_fast and rank6_boat in {5, 6} and rank6_ai_pred >= 5 and rank6_exhibit_top2:
        strategies.append(
            (
                "codex_post_rank6_outer_ai5_rank6exh",
                "Codex直前強上げ: AI+最下位5/6がAI5%+本人展示/1周2位以内",
                codex_logic29_outer_required,
            )
        )
    if (
        round_no >= 7
        and (race.get("manshu_rate_pct") or 0) >= 29
        and metrics.get("tenji_boats", 0) >= 6
        and metrics.get("isshu_boats", 0) >= 6
    ):
        strategies.append(
            (
                "codex_logic29_late_outer12",
                "Codex: 万舟率29%+後半 value頭 5/6絡み 10〜15点",
                codex_logic29_outer_required,
            )
        )
    if (
        (race.get("manshu_rate_pct") or 0) >= 27
        and metrics.get("tenji_boats", 0) >= 6
        and metrics.get("isshu_boats", 0) >= 6
        and not b1_summer_fast
        and (
            metrics.get("matchup_outer_good_count", 0) >= 2
            or bool(metrics.get("matchup_lane1_bad_flag"))
        )
    ):
        strategies.append(
            (
                "codex_matchup_outer_good12",
                "Codex相性型: 1劣勢+相性バフ艇 10〜15点",
                codex_logic29_outer_required,
            )
        )
    if (
        (race.get("manshu_rate_pct") or 0) >= 27
        and metrics.get("tenji_boats", 0) >= 6
        and metrics.get("isshu_boats", 0) >= 6
        and metrics.get("longshot_head_candidate_count", 0) >= 1
        and not b1_summer_fast
    ):
        strategies.append(
            (
                "codex_longshot_head12",
                "Codex妙味型: 人気薄頭候補+外枠絡み 10〜15点",
                codex_logic29_outer_required,
            )
        )
    if (
        (race.get("manshu_rate_pct") or 0) >= 27
        and metrics.get("tenji_boats", 0) >= 6
        and metrics.get("isshu_boats", 0) >= 6
        and (metrics.get("boat1_odds_prediction_pct") or -1) >= 45
        and int(metrics.get("boat1_odds_rank") or 9) == 1
        and int(metrics.get("low_outer_boat") or 0) in {5, 6}
        and (metrics.get("low_outer_avg_isshu_diff") or -9) >= 0.10
        and (metrics.get("low_outer_ai_prediction_pct") or -1) >= 5
        and metrics.get("low_outer_exhibit_top2")
        and not b1_summer_fast
    ):
        strategies.append(
            (
                "codex_popular_b1_low_outer12",
                "Codex妙味型: 人気1号艇飛び+低評価外枠復活 10〜15点",
                codex_logic29_outer_required,
            )
        )
    if (
        (race.get("manshu_rate_pct") or 0) >= 27
        and metrics.get("tenji_boats", 0) >= 6
        and metrics.get("isshu_boats", 0) >= 6
        and (metrics.get("boat1_loss_pct") or -1) >= 40
        and metrics.get("slit_outer56_pressure_vs_1")
        and int(metrics.get("low_outer_boat") or 0) in {5, 6}
        and (metrics.get("low_outer_ai_prediction_pct") or -1) >= 8
        and metrics.get("low_outer_exhibit_top2")
        and not b1_summer_fast
    ):
        strategies.append(
            (
                "codex_low_outer_revive12",
                "Codex穴外枠型: 1弱+外圧+低評価外枠復活 10〜15点",
                codex_logic29_outer_required,
            )
        )
    if (
        place == "若松"
        and round_no <= 6
        and (metrics.get("boat1_nige_pct") or 999) < 35
        and b1_bad
        and metrics.get("outer56_low_aiplus_exhibit_top2_count", 0) >= 1
    ):
        strategies.append(("wakamatsu_strict_mo12", "若松 strict: 1弱+外低評価浮上 12点", wakamatsu_mo12))
    if (
        place == "若松"
        and (metrics.get("boat1_ai_prediction_pct") or 999) < 40
        and (metrics.get("boat1_ai_plus") or 999) < 140
        and (metrics.get("boat1_nige_pct") or 999) < 35
        and metrics.get("outer56_low_aiplus_exhibit_top2_count", 0) >= 1
    ):
        strategies.append(("wakamatsu_broad_mo12", "若松 broad: 1低評価+外低評価浮上 12点", wakamatsu_mo12))
    if (
        place == "芦屋"
        and b1_tenji_time_rank is not None
        and b1_tenji_time_rank >= 4
        and b1_isshu_rank is not None
        and b1_isshu_rank >= 4
        and (metrics.get("outer56_isshu_advantage") or -9) >= 0.10
    ):
        strategies.append(("ashiya_bad1_mid12", "芦屋: 1号艇展示/1周悪化 中枠頭 12点", mid_heads_support_156))
    if (
        place == "宮島"
        and not b1_summer_fast
        and (metrics.get("boat1_nige_pct") or 999) < 50
        and (metrics.get("boat1_loss_pct") or -1) >= 40
        and metrics.get("outer56_exhibit_top2_count", 0) >= 2
        and (metrics.get("outer56_tenji_advantage") or -9) >= 0.03
    ):
        strategies.append(("miyajima_outer_no1", "宮島: 外2艇展示浮上 1号艇全消し 12点", mid_heads_outer_no1))
    if (
        place == "丸亀"
        and not b1_summer_fast
        and (metrics.get("boat1_nige_pct") or 999) < 45
        and (metrics.get("boat1_loss_pct") or -1) >= 45
        and metrics.get("outer56_isshu_top2_count", 0) >= 1
        and metrics.get("outer56_low_aipred_exhibit_top2_count", 0) >= 1
    ):
        strategies.append(("marugame_outer_no1", "丸亀: 1弱+外低評価浮上 1号艇全消し 12点", mid_heads_outer_no1))

    if not strategies:
        return []

    trifecta_odds, trifecta_odds_snapshot_at, trifecta_odds_db = load_latest_trifecta_odds(race)
    out = []
    for item in strategies:
        strategy_id, label, ticket_func = item[:3]
        meta = item[3] if len(item) >= 4 and isinstance(item[3], dict) else {}
        tickets, roles = ticket_func(rows)
        tickets, roles = refine_venue_sign_tickets(rows, tickets, roles, strategy_id, meta)
        if not tickets or roles is None:
            continue
        payload = {
            "strategy_id": strategy_id,
            "label": label,
            "points": len(tickets),
            "heads": roles["heads"],
            "base_heads": roles.get("base_heads", []),
            "head_rule": roles.get("head_rule"),
            "head_mode": roles.get("head_mode"),
            "head_scores": roles.get("head_scores", {}),
            "attackers": roles.get("attackers", []),
            "attack_scores": roles.get("attack_scores", {}),
            "finishers": roles.get("finishers", roles.get("heads", [])),
            "finisher_scores": roles.get("finisher_scores", roles.get("head_scores", {})),
            "support_boats": roles.get("support_boats", roles.get("supports", [])),
            "support_scores": roles.get("support_scores", {}),
            "role_split_note": roles.get("role_split_note"),
            "axes": roles["axes"],
            "alt_axes": roles.get("alt_axes", []),
            "axis_rule": roles.get("axis_rule"),
            "alt_axis_rule": roles.get("alt_axis_rule"),
            "supports": roles.get("supports", []),
            "keshi": roles["keshi"],
            "keshi_reason": roles.get("keshi_reason"),
            "ai_plus_rank6_boat": roles.get("ai_plus_rank6_boat"),
            "ai_plus_rank6_revival": roles.get("ai_plus_rank6_revival", []),
            "venue_low_ai_revivals": roles.get("venue_low_ai_revivals", venue_low_ai_revival_summary(rows)),
            "role_note": roles["role_note"],
            "tickets": [fmt_ticket(ticket) for ticket in sorted(tickets)],
            "odds_filter": SYNTHETIC_ODDS_FILTER_LABEL,
        }
        payload.update(meta)
        apply_synthetic_odds_filter(
            payload,
            trifecta_odds,
            odds_snapshot_at=trifecta_odds_snapshot_at,
            odds_db_path=trifecta_odds_db,
        )
        if payload.get("synthetic_odds_ready") and not payload.get("odds_filter_passed"):
            continue
        out.append(payload)
    return out


def fmt_list(values):
    values = [str(value) for value in values or []]
    return ",".join(values) if values else "-"


def fmt_role(value):
    return "-" if value is None else str(value)


def fmt_double_time(metrics):
    boats = metrics.get("double_time_boats") or []
    if not boats:
        return ""
    return f", DT{fmt_list(boats)}"


def fmt_super_slit(metrics):
    boats = metrics.get("super_slit_boats") or []
    if not boats:
        return ""
    return f", SSA{fmt_list(boats)}"


def fmt_summer_b1_isshu(metrics):
    signal = metrics.get("b1_summer_isshu_factor") or metrics.get("boat1_summer_isshu_factor")
    if not signal:
        return ""
    delta = as_num(metrics.get("b1_summer_nige_delta_pp") or metrics.get("boat1_summer_nige_delta_pp"))
    if delta is None:
        return ""
    sign = "+" if delta > 0 else ""
    return f", 夏1周逃げ{sign}{delta:.0f}pt"


def fmt_slit_shape(metrics):
    label = metrics.get("slit_shape_label")
    if not label:
        return ""
    return f", 隊形{label}"


def fmt_matchup(metrics):
    boats = str(metrics.get("matchup_buff_boats") or "").strip()
    notes = str(metrics.get("matchup_notes") or "").strip()
    lane1_bad = bool(metrics.get("matchup_lane1_bad_flag"))
    if boats:
        return f", 相性バフ{boats}"
    if lane1_bad:
        return ", 相性1劣勢"
    if notes:
        return f", 相性{notes}"
    return ""


def fmt_b1_odds(metrics):
    pct = metrics.get("boat1_odds_prediction_pct")
    rank = metrics.get("boat1_odds_rank")
    if pct is None and rank is None:
        return ""
    rank_text = "-" if rank is None else f"{int(rank)}位"
    popularity = b1_popularity_context(metrics).get("level") or ""
    pop_text = f"/{popularity}" if popularity else ""
    return f", 1オッズ評価{fmt_pct(pct)}({rank_text}{pop_text})"


def fmt_low_outer(metrics):
    boat = int(metrics.get("low_outer_boat") or 0)
    if boat not in {5, 6}:
        return ""
    return (
        f", 低外{boat}号"
        f" AI{fmt_pct(metrics.get('low_outer_ai_prediction_pct'))}"
        f" 差{fmt_time(metrics.get('low_outer_avg_isshu_diff'))}"
        f" 展{fmt_role(metrics.get('low_outer_tenji_rank'))}位"
        f"/周{fmt_role(metrics.get('low_outer_isshu_rank'))}位"
    )


def fmt_venue_low_ai_revival_items(items):
    parts = []
    for item in items or []:
        boat = item.get("boat_number")
        role = item.get("role_label") or item.get("role") or "復活"
        top3_pp = as_num(item.get("top3_rate_pp"))
        pp_text = f"+{top3_pp:.1f}pt" if top3_pp is not None else ""
        parts.append(f"{boat}号{role}{pp_text}")
    return " / ".join(parts[:3])


def fmt_venue_low_ai(metrics):
    text = fmt_venue_low_ai_revival_items(metrics.get("venue_low_ai_revivals") or [])
    return f", 低評価復活{text}" if text else ""


def fmt_longshot_head(metrics):
    boats = str(metrics.get("longshot_head_boats") or "").strip()
    if not boats:
        return ""
    return f", 人気薄頭候補{boats}"


def fetch_live_race(race, refresh=True):
    place = race.get("place_name")
    slug = race.get("slug") or PLACE_SLUGS.get(place)
    if not slug:
        raise RuntimeError(f"unknown place slug: {place}")
    date_text = race.get("date")
    round_no = int(race.get("round"))
    data_text = fetch_boaters_page(slug, date_text, round_no, "data", refresh=refresh)
    data = extract_data_page(data_text)
    odds = extract_live_odds_page(data_text)
    last = extract_last_minute_page(fetch_boaters_page(slug, date_text, round_no, "last-minute", refresh=refresh))
    by_boat = {}
    for boat in range(1, 7):
        row = {}
        row.update(data.get(boat, {}))
        row.update({k: v for k, v in (odds.get(boat) or {}).items() if v is not None})
        row.update(last.get(boat, {}))
        by_boat[boat] = row
    return by_boat


def original_boaters_ev_risk_note(evaluation):
    evidence = (
        ((evaluation or {}).get("low_confidence_shadow") or {}).get("odds_evidence")
        or {}
    )
    for target, label in (("t5", "T-5"), ("t10", "T-10")):
        legacy_shadow = (evaluation or {}).get("ticket_ev_shadow") or {}
        position_shadow = (evaluation or {}).get("ticket_position_shadow") or {}
        venue_shadow = (
            (evaluation or {}).get("ticket_venue_probability_shadow") or {}
        )
        legacy = as_num(evidence.get(f"legacy_{target}_expected_roi_pct"))
        if legacy is None:
            legacy = as_num(
                ((legacy_shadow.get("snapshots") or {}).get(target) or {}).get(
                    "portfolio_expected_roi_pct"
                )
            )
        position = as_num(evidence.get(f"position_{target}_expected_roi_pct"))
        if position is None:
            position = as_num(
                ((position_shadow.get("snapshots") or {}).get(target) or {}).get(
                    "portfolio_expected_roi_pct"
                )
            )
        venue = as_num(
            ((venue_shadow.get("snapshots") or {}).get(target) or {}).get(
                "portfolio_expected_roi_pct"
            )
        )
        if legacy is None or position is None:
            continue
        if legacy < 100.0 and position < 100.0:
            level = "強" if legacy < 80.0 and position < 80.0 else "あり"
            legacy_label = legacy_shadow.get("model_label") or "旧"
            position_label = position_shadow.get("model_label") or "着順"
            venue_text = (
                f" / {venue_shadow.get('model_label') or '場別補正'}{venue:.1f}%"
                if venue is not None
                else ""
            )
            return (
                f"期待値警戒{level}({label}): "
                f"{legacy_label}{legacy:.1f}% / {position_label}{position:.1f}%"
                f"{venue_text} "
                "（検証中・自動見送りには未使用）"
            )
        return ""
    return ""


def make_original_boaters_24_message(race, evaluation, minutes_to_deadline):
    snapshot = evaluation.get("condition_snapshot") or {}
    historical = evaluation.get("historical") or {}
    deadline = parse_dt(race.get("deadline_time"))
    deadline_text = deadline.strftime("%H:%M") if deadline else "--:--"
    rank = snapshot.get("b1_odds_rank")
    rank_text = f"{int(rank)}位" if rank is not None else "-"
    mode_text = "展示+一周/半周" if evaluation.get("data_mode") == "full" else "展示タイム"
    ev_risk_note = original_boaters_ev_risk_note(evaluation)
    return (
        f"【新24場サイン】{evaluation.get('venue')}{race.get('round')}R\n"
        f"締切{deadline_text} / 残り{minutes_to_deadline:.1f}分 / 判定データ: {mode_text}\n"
        f"条件: {evaluation.get('condition')}\n"
        f"1号艇: 人気{rank_text} {fmt_pct(snapshot.get('b1_odds_pct'))} / "
        f"AI1着{fmt_pct(snapshot.get('b1_ai_win'))} / 逃げ{fmt_pct(snapshot.get('b1_nige_pct'))}\n"
        f"展示順位{fmt_role(snapshot.get('b1_tenji_rank'))}位 / "
        f"一周・半周順位{fmt_role(snapshot.get('b1_lap_rank'))}位 / "
        f"平均との差{fmt_time(snapshot.get('b1_avg_diff'))} / "
        f"外艇上位{int(snapshot.get('outer_top2_count') or 0)}艇 / "
        f"風{fmt_time(snapshot.get('wind_speed'))}m 波{fmt_time(snapshot.get('wave_height'))}cm\n"
        f"買い方: {evaluation.get('buy_method')} / {evaluation.get('points')}点\n"
        f"買い目: {' '.join(evaluation.get('tickets') or [])}\n"
        f"{ev_risk_note + chr(10) if ev_risk_note else ''}"
        f"過去探索値: {historical.get('races', '-')}R / "
        f"的中率{fmt_pct(historical.get('hit_rate_pct'))} / "
        f"回収率{fmt_pct(historical.get('roi_pct'))}\n"
        "注意: 過去探索値は将来の利益を保証しません。未来成績を同時記録中です。"
    )


def make_message(race, alert_type, metrics, checks, strategies, ev_risk_note=""):
    base = (
        f"{race.get('place_name')}{race.get('round')}R "
        f"万舟率{fmt_pct(race.get('manshu_rate_pct'))}"
    )
    if race.get("morning_rank"):
        base += f" / 朝{race.get('morning_rank')}位"
    if race.get("live_rank"):
        base += f" / 直前{race.get('live_rank')}位"
    deadline = parse_dt(race.get("deadline_time"))
    deadline_text = deadline.strftime("%H:%M") if deadline else "--:--"
    metric_text = (
        f"締切{deadline_text} / 1号艇逃げ{fmt_pct(metrics.get('boat1_nige_pct'))}, "
        f"逃げ失敗{fmt_pct(metrics.get('boat1_loss_pct'))}, "
        f"1展示+1周平均との差{fmt_time(metrics.get('boat1_avg_isshu_diff'))}, "
        f"展示+1周平均{fmt_time(metrics.get('avg_exhibit_combo_time'))}, "
        f"1展示{fmt_time(metrics.get('boat1_tenji_time'))}"
        f"({metrics.get('boat1_tenji_time_rank')}位), "
        f"5/6展示+1周平均との差{fmt_time(metrics.get('outer56_best_avg_isshu_diff'))}"
        f"{fmt_b1_odds(metrics)}"
        f"{fmt_low_outer(metrics)}"
        f"{fmt_venue_low_ai(metrics)}"
        f"{fmt_longshot_head(metrics)}"
        f"{fmt_double_time(metrics)}"
        f"{fmt_super_slit(metrics)}"
        f"{fmt_summer_b1_isshu(metrics)}"
        f"{fmt_slit_shape(metrics)}"
        f"{fmt_matchup(metrics)}"
    )
    if (
        alert_type in {"buy_ok", "late_riser_buy_ok", "subcore_watch", "late_riser_subcore_watch", "venue_sign"}
        or (alert_type == "late_riser" and has_venue_sign_strategy(strategies))
    ) and strategies:
        s = strategies[0]
        is_sign_strategy = is_venue_sign_strategy(s)
        support_text = f" / 相手: {fmt_list(s.get('supports'))}" if s.get("supports") else ""
        base_head_text = f" / 元外頭候補: {fmt_list(s.get('base_heads'))}" if s.get("base_heads") else ""
        split_text = ""
        if s.get("attackers") or s.get("finishers") or s.get("support_boats"):
            split_text = (
                f"役割分解: 攻め艇{fmt_list(s.get('attackers'))} / "
                f"頭{fmt_list(s.get('finishers') or s.get('heads'))} / "
                f"相手{fmt_list(s.get('support_boats') or s.get('supports'))}\n"
            )
        revival_text = ""
        if s.get("venue_low_ai_revivals"):
            revival_text = f"低評価復活: {fmt_venue_low_ai_revival_items(s.get('venue_low_ai_revivals'))}\n"
        entry_checks = s.get("entry_checks") or []
        all_checks = list(entry_checks or checks or [])
        if is_sign_strategy:
            if is_big50_sign_strategy(s):
                title = "【5万舟警戒サイン 急浮上】" if alert_type == "late_riser" else "【5万舟警戒サイン】"
                sign_text = "サイン: 5万舟以上の高配当警戒条件に一致\n"
            else:
                title = "【24場サイン 急浮上】" if alert_type == "late_riser" else "【24場サイン】" if alert_type == "venue_sign" else "【24場サイン 本命】"
                sign_text = "サイン: 24場別の検証済み条件に一致\n"
            handling_text = "\n扱い: 朝監視TOP10外のサインです。買うなら慎重に。" if alert_type == "late_riser" else ""
        elif s.get("strategy_id") in VALIDATED_BUY_STRATEGY_IDS:
            title = "【本命買い候補】"
            sign_text = ""
            handling_text = ""
        elif s.get("strategy_id") in SUBCORE_WATCH_STRATEGY_IDS:
            title = "【準本命候補】"
            sign_text = ""
            handling_text = ""
        else:
            title = "【急浮上 買い候補】" if alert_type == "late_riser_buy_ok" else "【買い候補】"
            sign_text = ""
            handling_text = ""
        return (
            f"{title}{base}\n"
            f"{sign_text}"
            f"{metric_text}\n"
            f"直前条件: {' / '.join(all_checks)}\n"
            f"買い方: {s['label']} / {s['points']}点 / {s['odds_filter']}\n"
            f"頭候補: {fmt_list(s['heads'])} / 軸: {fmt_list(s['axes'])}"
            f"{base_head_text}({s.get('axis_rule','AI+1位3位')}) / 比較軸: {fmt_list(s.get('alt_axes'))}"
            f"{support_text} / 消し: {fmt_role(s['keshi'])}\n"
            f"{split_text}"
            f"{revival_text}"
            f"荒れた時はこの買い目: {' '.join(s['tickets'])}\n"
            f"{ev_risk_note + chr(10) if ev_risk_note else ''}"
            f"根拠: {s.get('role_note') or '本命絞り'} / 消し理由: {s.get('keshi_reason') or '-'}"
            f"{handling_text}"
        )
    if alert_type == "late_riser":
        return (
            f"【急浮上参考】{base}\n"
            f"{metric_text}\n"
            f"直前条件: 朝監視TOP10外 / 展示後40%以上 / {' / '.join(checks)}\n"
            f"扱い: 本命ではなく参考枠です。買うならかなり慎重に。"
        )
    if alert_type in {"subcore_watch", "late_riser_subcore_watch"}:
        return (
            f"【準本命候補】{base}\n"
            f"{metric_text}\n"
            f"直前条件: 展示後38〜39.9% / {' / '.join(checks)}"
        )
    if alert_type in {"buy_ok", "late_riser_buy_ok"}:
        return (
            f"【本命候補】{base}\n"
            f"{metric_text}\n"
            f"直前条件: 展示後40%以上 / {' / '.join(checks)}"
        )
    return (
        f"【万舟率上昇候補】{base}\n"
        f"{metric_text}\n"
        f"直前条件: {' / '.join(checks)}"
    )


def monitor(args):
    date_text = args.date or today_jst()
    now = parse_dt(args.now) if args.now else datetime.now(JST)
    public_updates = {}
    original_boaters_shadow_signs = []
    original_boaters_shadow_seen = set()
    ranking_path = ensure_morning_ranking(
        date_text,
        top_n=args.top_n,
        threshold=args.threshold,
        rebuild=args.rebuild_morning,
        no_build=args.no_build_morning,
        ranking_url_base=args.ranking_url_base,
    )
    if ranking_path is None:
        payload = {
            "version": "boaters-manshu-alerts-v1",
            "date": date_text,
            "generated_at": now.isoformat(timespec="seconds"),
            "ranking_path": None,
            "top_n": args.top_n,
            "lookahead_minutes": args.lookahead_minutes,
            "alerts": [],
            "inspected": [
                {
                    "status": "skip_no_ranking",
                    "message": "morning ranking JSON not found and no-build mode is enabled",
                }
            ],
        }
        state = load_json(state_path(date_text), {"sent": {}})
        if not args.no_push:
            payload["push"] = push_notifications(payload, state, now)
        save_json(alerts_path(date_text), payload)
        state["updated_at"] = now.isoformat(timespec="seconds")
        save_json(state_path(date_text), state)
        return payload
    ranking = load_json(ranking_path, {})
    races = morning_watch_rows(ranking, args.top_n)
    morning_ids = {race.get("race_id") for race in races}
    only_race_ids = {str(race_id) for race_id in getattr(args, "only_race_id", []) if race_id}
    state = load_json(state_path(date_text), {"sent": {}})
    pushed = state.setdefault("pushed", {})

    inspected = []
    alerts = []
    live_path = None
    live_rows = []
    live_by_id = {}
    if args.scan_risers and not args.offline:
        try:
            if args.no_build_live:
                live_path = existing_live_ranking_path(date_text)
                if live_path is None:
                    raise RuntimeError("existing live ranking JSON not found and --no-build-live is enabled")
            else:
                live_path = build_live_ranking(date_text, top_n=args.live_top_n, threshold=0.0)
            live_ranking = load_json(live_path, {})
            live_rows = ranking_rows(live_ranking, args.live_top_n)
            live_by_id = {race.get("race_id"): race for race in live_rows if race.get("race_id")}
        except Exception as exc:
            inspected.append(
                {
                    "status": "live_ranking_failed",
                    "source": "post_exhibition_refresh",
                    "error": str(exc),
                }
            )

    def inspect_window(race, source_type):
        deadline = parse_dt(race.get("deadline_time"))
        if deadline is None:
            inspected.append({"race_id": race.get("race_id"), "source": source_type, "status": "skip_no_deadline"})
            return None
        minutes_to_deadline = (deadline - now).total_seconds() / 60
        metrics = race.get("metrics") or {}
        missing_exhibition = not has_full_exhibition(metrics)
        backfill_limit_minutes = max(0.0, args.backfill_missing_exhibition_hours) * 60
        after_deadline_within_backfill = (
            minutes_to_deadline < -args.grace_minutes
            and abs(minutes_to_deadline) <= backfill_limit_minutes
        )
        backfill_after_close = (
            after_deadline_within_backfill
            and (
                missing_exhibition
                # The morning TOP10 is the public watchlist.  Even when the
                # refreshed live ranking already has exhibition metrics, the
                # frozen morning row still needs a post-deadline fetch so the
                # public page and status notification do not stay stale.
                or source_type == "morning_top"
            )
        )
        preview_fetch_limit = max(args.lookahead_minutes, args.preview_fetch_lookahead_minutes)
        active_alert_limit = args.lookahead_minutes
        if args.venue_sign_only or source_type == "all_venue_sign":
            # 24場サインは展示後データ必須。締切10分前からだけ通知判定する。
            active_alert_limit = min(args.lookahead_minutes, VENUE_SIGN_ALERT_LOOKAHEAD_MINUTES)
        preview_refresh_before_alert_window = (
            source_type != "all_venue_sign"
            and minutes_to_deadline > active_alert_limit
            and minutes_to_deadline <= preview_fetch_limit
            and (
                missing_exhibition
                # Keep the public watchlist fresh even when the frozen morning
                # row still says "展示待ち" but the live ranking has started to
                # carry post-exhibition metrics.
                or source_type == "morning_top"
            )
        )
        if minutes_to_deadline > active_alert_limit or minutes_to_deadline < -args.grace_minutes:
            if preview_refresh_before_alert_window:
                if args.offline:
                    inspected.append(
                        {
                            "race_id": race.get("race_id"),
                            "place_name": race.get("place_name"),
                            "round": race.get("round"),
                            "source": source_type,
                            "status": "offline_preview_refresh",
                            "minutes_to_deadline": round(minutes_to_deadline, 1),
                        }
                    )
                    return None
                return {
                    "minutes_to_deadline": minutes_to_deadline,
                    "backfill_only": True,
                    "fetch_reason": "preview_refresh",
                }
            if backfill_after_close:
                if args.offline:
                    inspected.append(
                        {
                            "race_id": race.get("race_id"),
                            "place_name": race.get("place_name"),
                            "round": race.get("round"),
                            "source": source_type,
                            "status": "offline_backfill_missing_exhibition",
                            "minutes_to_deadline": round(minutes_to_deadline, 1),
                        }
                    )
                    return None
                return {
                    "minutes_to_deadline": minutes_to_deadline,
                    "backfill_only": True,
                    "fetch_reason": "after_close_backfill",
                }
            inspected.append(
                {
                    "race_id": race.get("race_id"),
                    "place_name": race.get("place_name"),
                    "round": race.get("round"),
                    "source": source_type,
                    "status": "outside_window",
                    "minutes_to_deadline": round(minutes_to_deadline, 1),
                }
            )
            return None
        if args.offline:
            inspected.append(
                {
                    "race_id": race.get("race_id"),
                    "place_name": race.get("place_name"),
                    "round": race.get("round"),
                    "source": source_type,
                    "status": "offline_window_match",
                    "minutes_to_deadline": round(minutes_to_deadline, 1),
                }
            )
            return None
        return {
            "minutes_to_deadline": minutes_to_deadline,
            "backfill_only": False,
            "fetch_reason": "venue_sign_alert_window" if source_type == "all_venue_sign" else "alert_window",
        }

    def inspect_race(race, source_type, morning_rank=None, live_rank=None):
        window = inspect_window(race, source_type)
        if window is None:
            return
        minutes_to_deadline = window["minutes_to_deadline"]
        backfill_only = bool(window.get("backfill_only"))
        fetch_reason = str(window.get("fetch_reason") or "alert_window")
        try:
            if args.live_source == "official":
                by_boat = fetch_official_live_race(race)
            else:
                by_boat = fetch_live_race(race, refresh=not args.no_refresh)
            screenshot_summary = boaters_screenshot_data.apply_approved_to_by_boat(
                by_boat,
                race,
                args.boaters_screenshot_dir,
            )
            rows = enrich_rows(
                by_boat,
                race.get("metrics") or {},
                date_text=race.get("date"),
                place_name=race.get("place_name"),
            )
            official_source_rows = rows
            if not args.no_official_aux and args.live_source != "official":
                apply_official_beforeinfo_aux(rows, race)
                if merge_official_aux_into_live_by_boat(by_boat, rows):
                    official_source_rows = rows
                    rows = enrich_rows(
                        by_boat,
                        race.get("metrics") or {},
                        date_text=race.get("date"),
                        place_name=race.get("place_name"),
                    )
                    attach_row_aux_summaries(rows, official_source_rows)
            self_ai_summary = None
            if args.self_ai_model:
                self_ai_summary = apply_self_ai_to_live_by_boat(
                    by_boat,
                    rows,
                    race,
                    args.self_ai_model,
                    args.self_ai_mode,
                    args.self_ai_odds_mode,
                )
                if self_ai_summary:
                    rows = enrich_rows(
                        by_boat,
                        race.get("metrics") or {},
                        date_text=race.get("date"),
                        place_name=race.get("place_name"),
                    )
                    attach_row_aux_summaries(
                        rows,
                        official_source_rows,
                        self_ai_summary=self_ai_summary,
                    )
            imitation_summary = None
            if args.boaters_imitation_model and not screenshot_summary:
                imitation_summary = apply_boaters_imitation_to_live_by_boat(
                    by_boat,
                    rows,
                    race,
                    args.boaters_imitation_model,
                    args.boaters_imitation_odds_mode,
                )
                if imitation_summary:
                    rows = enrich_rows(
                        by_boat,
                        race.get("metrics") or {},
                        date_text=race.get("date"),
                        place_name=race.get("place_name"),
                    )
                    attach_row_aux_summaries(
                        rows,
                        official_source_rows,
                        imitation_summary,
                        self_ai_summary,
                    )
            trifecta_position_summary = None
            if args.trifecta_position_model:
                trifecta_position_summary = apply_trifecta_position_model_to_live_by_boat(
                    by_boat,
                    rows,
                    race,
                    args.trifecta_position_model,
                )
                if trifecta_position_summary and trifecta_position_summary.get("available"):
                    rows = enrich_rows(
                        by_boat,
                        race.get("metrics") or {},
                        date_text=race.get("date"),
                        place_name=race.get("place_name"),
                    )
                    attach_row_aux_summaries(
                        rows,
                        official_source_rows,
                        imitation_summary,
                        self_ai_summary,
                    )
            probability_overlay_ai_source = (
                original_boaters_forward.ORIGINAL_AI_SOURCE
                if (
                    screenshot_summary
                    and screenshot_summary.get("original_boaters_ready")
                    and screenshot_summary.get("boaters_exhibition_ready")
                    and not (self_ai_summary and args.self_ai_mode == "replace")
                )
                or (
                    args.live_source == "boaters"
                    and not imitation_summary
                    and not (self_ai_summary and args.self_ai_mode == "replace")
                )
                else str((race.get("metrics") or {}).get("ai_field_source") or args.live_source or "unknown")
            )
            venue_probability_summary = None
            if args.venue_probability_overlay:
                venue_probability_summary = apply_venue_probability_overlay_to_live_by_boat(
                    by_boat,
                    rows,
                    race,
                    args.venue_probability_overlay,
                    ai_source=probability_overlay_ai_source,
                )
                if venue_probability_summary and venue_probability_summary.get("available"):
                    rows = enrich_rows(
                        by_boat,
                        race.get("metrics") or {},
                        date_text=race.get("date"),
                        place_name=race.get("place_name"),
                    )
                    attach_row_aux_summaries(
                        rows,
                        official_source_rows,
                        imitation_summary,
                        self_ai_summary,
                    )
            metrics = race_metrics(rows, date_text=race.get("date"), round_no=race.get("round"))
            if args.live_source == "official":
                metrics["live_source"] = "official"
                metrics["official_live_summary"] = race.get("_official_live_summary") or {}
            if screenshot_summary:
                metrics["boaters_user_screenshot"] = screenshot_summary
                metrics["ai_field_source"] = "original_boaters_user_screenshot"
                metrics["exhibition_field_source"] = (
                    "original_boaters_user_screenshot"
                    if screenshot_summary.get("boaters_exhibition_ready")
                    else "official_fallback"
                )
                metrics["boaters_exhibition_mode"] = (
                    screenshot_summary.get("boaters_exhibition_mode") or "missing"
                )
            if imitation_summary:
                metrics["boaters_imitation_ai"] = imitation_summary
                metrics["ai_field_source"] = "local_boaters_imitation"
            if self_ai_summary:
                metrics["self_ai"] = self_ai_summary
                if args.self_ai_mode == "replace":
                    metrics["ai_field_source"] = "local_self_ai"
            if trifecta_position_summary:
                metrics["trifecta_position_model"] = trifecta_position_summary
            if venue_probability_summary:
                metrics["venue_probability_overlay"] = venue_probability_summary
            shadow_evaluation = None
            if not backfill_only and 0 <= minutes_to_deadline <= VENUE_SIGN_ALERT_LOOKAHEAD_MINUTES:
                shadow_evaluation = original_boaters_forward.evaluate(
                    race,
                    metrics,
                    rows,
                    ai_source=probability_overlay_ai_source,
                )
                if shadow_evaluation.get("matched"):
                    shadow_key = forward_entry_key(
                        race.get("race_id"),
                        shadow_evaluation.get("rule_id"),
                    )
                    original_push_key = (
                        f"alert:{race.get('race_id')}:venue_sign:venue_sign"
                    )
                    frozen_sign = {
                        **shadow_evaluation,
                        "date": race.get("date") or date_text,
                        "race_id": race.get("race_id"),
                        "place_name": original_boaters_forward.normalize_venue(
                            race.get("place_name")
                        ),
                        "round": race.get("round"),
                        "deadline_time": race.get("deadline_time"),
                        "source_type": source_type,
                        "detected_at": now.isoformat(timespec="seconds"),
                        "minutes_to_deadline": round(minutes_to_deadline, 1),
                        "push_key": original_push_key,
                        "independent_probabilities": (
                            (self_ai_summary or {}).get("per_boat") or []
                        ),
                        "independent_probability_model": {
                            "available": bool(
                                self_ai_summary
                                and self_ai_summary.get("available")
                            ),
                            "version": (self_ai_summary or {}).get(
                                "model_version"
                            ),
                            "feature_coverage": (self_ai_summary or {}).get(
                                "numeric_feature_coverage"
                            ),
                            "win_temperature": (self_ai_summary or {}).get(
                                "win_temperature"
                            ),
                            "top3_temperature": (self_ai_summary or {}).get(
                                "top3_temperature"
                            ),
                        },
                    }
                    refresh_ticket_ev_shadows(frozen_sign)
                    update_original_boaters_low_confidence_odds(frozen_sign)
                    frozen_sign["ev_risk_note"] = original_boaters_ev_risk_note(
                        frozen_sign
                    )
                    # Existing entries still receive refreshed independent
                    # probabilities. The seen gate below only suppresses a
                    # duplicate notification.
                    original_boaters_shadow_signs.append(frozen_sign)
                    if shadow_key not in original_boaters_shadow_seen:
                        original_boaters_shadow_seen.add(shadow_key)
                        frozen_strategy = {
                            "strategy_id": shadow_evaluation.get("rule_id"),
                            "label": shadow_evaluation.get("buy_method"),
                            "points": shadow_evaluation.get("points"),
                            "heads": shadow_evaluation.get("heads") or [],
                            "axes": shadow_evaluation.get("axes") or [],
                            "keshi": shadow_evaluation.get("keshi"),
                            "tickets": shadow_evaluation.get("tickets") or [],
                            "entry_checks": [shadow_evaluation.get("condition")],
                            "rule_status": shadow_evaluation.get("rule_status"),
                            "historical": shadow_evaluation.get("historical") or {},
                        }
                        alerts.append(
                            {
                                "alert_type": "venue_sign",
                                "race_id": race.get("race_id"),
                                "date": race.get("date") or date_text,
                                "rank": race.get("rank"),
                                "morning_rank": morning_rank,
                                "live_rank": live_rank,
                                "source_type": source_type,
                                "detected_at": now.isoformat(timespec="seconds"),
                                "checked_at": now.isoformat(timespec="seconds"),
                                "minutes_to_deadline": round(minutes_to_deadline, 1),
                                "push_key": original_push_key,
                                "place_name": frozen_sign.get("place_name"),
                                "round": race.get("round"),
                                "deadline_time": race.get("deadline_time"),
                                "manshu_rate_pct": race.get("manshu_rate_pct"),
                                "recent_rate_pct": race.get("recent_rate_pct"),
                                "condition": shadow_evaluation.get("condition"),
                                "ev_risk_note": frozen_sign.get("ev_risk_note"),
                                "checks": [shadow_evaluation.get("condition")],
                                "metrics": metrics,
                                "selection": frozen_strategy,
                                "rule_set_id": original_boaters_forward.RULE_SET_ID,
                                "rule_id": shadow_evaluation.get("rule_id"),
                                "sign_alert": True,
                                "sign_label": "新24場サイン",
                                "sign_strategy_ids": [shadow_evaluation.get("rule_id")],
                                "strategies": [frozen_strategy],
                                "message": make_original_boaters_24_message(
                                    race,
                                    frozen_sign,
                                    minutes_to_deadline,
                                ),
                            }
                        )
            confirmed, checks = condition_confirmed(race.get("condition"), metrics)
            all_strategies = roi_strategies(race, metrics, rows)
            buy_strategies = [
                strategy
                for strategy in all_strategies
                if strategy.get("strategy_id") in VALIDATED_BUY_STRATEGY_IDS
            ]
            buy_strategies.sort(
                key=lambda strategy: VALIDATED_BUY_STRATEGY_ORDER.get(strategy.get("strategy_id"), 99)
            )
            subcore_strategies = [
                strategy
                for strategy in all_strategies
                if strategy.get("strategy_id") in SUBCORE_WATCH_STRATEGY_IDS
            ]
            selection_strategies = buy_strategies or subcore_strategies
            selection = selection_payload(rows, race=race, strategies=selection_strategies)
            metrics["preview_fetch_attempted"] = True
            metrics["preview_fetch_attempted_at"] = now.isoformat(timespec="seconds")
            metrics["preview_fetch_reason"] = fetch_reason
            preview_ready = has_strategy_ready_exhibition(metrics, buy_strategies)
            metrics["preview_missing_reason"] = "" if preview_ready else exhibition_missing_reason(metrics)
            post_rate = as_num(race.get("manshu_rate_pct")) or 0
            core_rate_ready = post_rate >= args.core_alert_threshold
            subcore_rate_ready = SUBCORE_ALERT_RATE_MIN <= post_rate < args.core_alert_threshold
            rate_gate_exempt_buy_ready = any(strategy.get("rate_gate_exempt") for strategy in buy_strategies)
            core_buy_ready = bool(buy_strategies) and (core_rate_ready or rate_gate_exempt_buy_ready)
            subcore_buy_ready = subcore_rate_ready and bool(subcore_strategies)
            venue_sign_buy_ready = any(
                is_venue_sign_strategy(strategy)
                and (core_rate_ready or strategy.get("rate_gate_exempt"))
                for strategy in buy_strategies
            )
            core_ev_evaluation = {}
            core_ev_risk_note = ""
            if preview_ready and venue_sign_buy_ready and selection.get("tickets"):
                core_ev_evaluation = {
                    "date": race.get("date") or date_text,
                    "race_id": race.get("race_id"),
                    "place_name": race.get("place_name"),
                    "round": race.get("round"),
                    "deadline_time": race.get("deadline_time"),
                    **build_ticket_ev_shadows(rows, selection.get("tickets") or []),
                }
                refresh_ticket_ev_shadows(core_ev_evaluation)
                core_ev_risk_note = original_boaters_ev_risk_note(
                    core_ev_evaluation
                )
            alert_rate_ready = core_buy_ready or subcore_buy_ready
            can_send_alert = preview_ready and alert_rate_ready
            if args.venue_sign_only:
                alert_type = "venue_sign" if preview_ready and venue_sign_buy_ready else None
            elif backfill_only:
                alert_type = None
            elif source_type == "morning_top" and can_send_alert:
                if core_buy_ready:
                    alert_type = "buy_ok"
                elif subcore_buy_ready:
                    alert_type = "subcore_watch"
                else:
                    alert_type = None
            elif source_type == "all_venue_sign" and preview_ready and venue_sign_buy_ready:
                alert_type = "venue_sign"
            elif source_type != "morning_top" and not args.notify_risers:
                alert_type = None
            elif not can_send_alert:
                alert_type = None
            elif source_type != "morning_top" and core_buy_ready:
                alert_type = "late_riser"
            else:
                alert_type = None

            strategy_ids = [s["strategy_id"] for s in buy_strategies]
            subcore_strategy_ids = [s["strategy_id"] for s in subcore_strategies]
            near_miss = near_miss_explanation(
                race,
                metrics,
                rows,
                source_type,
                preview_ready,
                core_rate_ready,
                subcore_rate_ready,
                core_buy_ready,
                subcore_buy_ready,
                all_strategies,
                buy_strategies,
                subcore_strategies,
            )
            public_updates[race.get("race_id")] = {
                "metrics": metrics,
                "selection": selection,
                "checked_at": now.isoformat(timespec="seconds"),
                "alert_type": alert_type,
                "last_minute_manshu_rate_pct": post_rate,
                "morning_manshu_rate_pct": race.get("morning_manshu_rate_pct"),
                "rate_source": race.get("rate_source"),
                "source_type": source_type,
                "live_rank": live_rank or race.get("live_rank"),
                "checks": checks,
                "strategy_ids": strategy_ids,
                "subcore_strategy_ids": subcore_strategy_ids,
                "candidate_strategy_ids": [s["strategy_id"] for s in all_strategies],
                "core_rate_ready": core_rate_ready,
                "subcore_rate_ready": subcore_rate_ready,
                "rate_gate_exempt_buy_ready": rate_gate_exempt_buy_ready,
                "core_buy_ready": core_buy_ready,
                "subcore_buy_ready": subcore_buy_ready,
                "venue_sign_buy_ready": venue_sign_buy_ready,
                "ev_risk_note": core_ev_risk_note,
                "original_boaters_24_shadow_status": (
                    shadow_evaluation.get("status") if shadow_evaluation else "outside_forward_window"
                ),
                "original_boaters_24_shadow_match": bool(
                    shadow_evaluation and shadow_evaluation.get("matched")
                ),
                "near_miss_level": near_miss.get("level"),
                "near_miss_summary": near_miss.get("summary"),
                "near_miss_reasons": near_miss.get("reasons") or [],
                "near_miss_positives": near_miss.get("positives") or [],
                "buy_decision": (
                    "本命"
                    if source_type == "morning_top" and core_buy_ready
                    else (
                        "準本命"
                        if source_type == "morning_top" and subcore_buy_ready
                        else (
                            "24場サイン"
                            if source_type == "all_venue_sign" and venue_sign_buy_ready
                            else ("急浮上参考" if source_type != "morning_top" and core_buy_ready else ("見送り" if preview_ready else None))
                        )
                    )
                ),
                "core_alert_threshold_pct": args.core_alert_threshold,
                "subcore_alert_threshold_min_pct": SUBCORE_ALERT_RATE_MIN,
            }
            inspected.append(
                {
                    "race_id": race.get("race_id"),
                    "place_name": race.get("place_name"),
                    "round": race.get("round"),
                    "source": source_type,
                    "status": (
                        "preview_refreshed"
                        if fetch_reason == "preview_refresh"
                        else ("after_close_backfilled" if fetch_reason == "after_close_backfill" else "checked")
                    ),
                    "fetch_reason": fetch_reason,
                    "minutes_to_deadline": round(minutes_to_deadline, 1),
                    "condition_confirmed": confirmed,
                    "checks": checks,
                    "strategies": strategy_ids,
                    "subcore_strategies": subcore_strategy_ids,
                    "candidate_strategy_ids": [s["strategy_id"] for s in all_strategies],
                    "preview_ready": preview_ready,
                    "alert_rate_ready": alert_rate_ready,
                    "core_rate_ready": core_rate_ready,
                    "subcore_rate_ready": subcore_rate_ready,
                    "rate_gate_exempt_buy_ready": rate_gate_exempt_buy_ready,
                    "core_buy_ready": core_buy_ready,
                    "subcore_buy_ready": subcore_buy_ready,
                    "venue_sign_buy_ready": venue_sign_buy_ready,
                    "ev_risk_note": core_ev_risk_note,
                    "original_boaters_24_shadow_status": (
                        shadow_evaluation.get("status") if shadow_evaluation else "outside_forward_window"
                    ),
                    "original_boaters_24_shadow_match": bool(
                        shadow_evaluation and shadow_evaluation.get("matched")
                    ),
                    "near_miss_level": near_miss.get("level"),
                    "near_miss_summary": near_miss.get("summary"),
                    "near_miss_reasons": near_miss.get("reasons") or [],
                    "near_miss_positives": near_miss.get("positives") or [],
                    "core_alert_threshold_pct": args.core_alert_threshold,
                    "subcore_alert_threshold_min_pct": SUBCORE_ALERT_RATE_MIN,
                    "morning_manshu_rate_pct": race.get("morning_manshu_rate_pct"),
                    "post_exhibition_manshu_rate_pct": post_rate,
                    "rate_source": race.get("rate_source"),
                    "selection": selection,
                    "metrics": metrics,
                    "morning_rank": morning_rank,
                    "live_rank": live_rank,
                }
            )
            if shadow_evaluation and shadow_evaluation.get("matched"):
                # The frozen-rule alert already represents this race.  Do not
                # send a second notification if an older venue rule also hit.
                return
            if alert_type is None:
                return
            alert_strategies = selection_strategies
            sign_strategy_ids = venue_sign_strategy_ids(alert_strategies)
            push_suffix = ":venue_sign" if sign_strategy_ids else ""
            push_key = f"alert:{race.get('race_id')}:{alert_type}{push_suffix}"
            if pushed.get(push_key):
                return
            message_race = dict(race)
            if morning_rank:
                message_race["morning_rank"] = morning_rank
            if live_rank:
                message_race["live_rank"] = live_rank
            alert = {
                "alert_type": alert_type,
                "race_id": race.get("race_id"),
                "date": race.get("date"),
                "rank": race.get("rank"),
                "morning_rank": morning_rank,
                "live_rank": live_rank,
                "source_type": source_type,
                "detected_at": now.isoformat(timespec="seconds"),
                "checked_at": now.isoformat(timespec="seconds"),
                "minutes_to_deadline": round(minutes_to_deadline, 1),
                "push_key": push_key,
                "place_name": race.get("place_name"),
                "round": race.get("round"),
                "deadline_time": race.get("deadline_time"),
                "manshu_rate_pct": race.get("manshu_rate_pct"),
                "recent_rate_pct": race.get("recent_rate_pct"),
                "condition": race.get("condition"),
                "checks": checks,
                "metrics": metrics,
                "selection": selection,
                "sign_alert": bool(sign_strategy_ids),
                "sign_label": sign_label_for_strategies(alert_strategies) if sign_strategy_ids else "",
                "sign_strategy_ids": sign_strategy_ids,
                "strategies": alert_strategies,
                "ev_risk_note": core_ev_risk_note,
                **{
                    key: core_ev_evaluation.get(key) or {}
                    for key in TICKET_EV_SHADOW_KEYS
                },
                "message": make_message(
                    message_race,
                    alert_type,
                    metrics,
                    checks,
                    alert_strategies,
                    ev_risk_note=core_ev_risk_note,
                ),
            }
            alerts.append(alert)
        except Exception as exc:
            inspected.append(
                {
                    "race_id": race.get("race_id"),
                    "place_name": race.get("place_name"),
                    "round": race.get("round"),
                    "source": source_type,
                    "status": "fetch_failed",
                    "minutes_to_deadline": round(minutes_to_deadline, 1),
                    "error": str(exc),
                }
            )

    for rank, race in enumerate(races, start=1):
        if only_race_ids and str(race.get("race_id") or "") not in only_race_ids:
            continue
        live_race = live_by_id.get(race.get("race_id"))
        merged_race = morning_race_with_live_rate(race, live_race)
        inspect_race(merged_race, "morning_top", morning_rank=rank, live_rank=merged_race.get("live_rank"))

    if args.scan_risers and not args.offline and live_rows:
        try:
            for live_rank, race in enumerate(live_rows, start=1):
                if only_race_ids and str(race.get("race_id") or "") not in only_race_ids:
                    continue
                if race.get("race_id") in morning_ids:
                    continue
                inspect_race(race, "all_venue_sign", live_rank=live_rank)
        except Exception as exc:
            inspected.append(
                {
                    "status": "live_ranking_failed",
                    "source": "late_riser",
                    "error": str(exc),
                }
            )

    public_metrics_updated = False
    if not args.no_public_metrics_update:
        public_metrics_updated = merge_live_metrics_into_public_ranking(date_text, public_updates, now)

    payload = {
        "version": "boaters-manshu-alerts-v1",
        "date": date_text,
        "generated_at": now.isoformat(timespec="seconds"),
        "ranking_path": str(ranking_path),
        "public_ranking_path": str(public_ranking_path(date_text)),
        "live_ranking_path": str(live_path) if live_path else None,
        "forward_validation_log_path": None,
        "original_boaters_24_shadow": None,
        "original_boaters_24_forward": None,
        "factor_dictionaries": factor_dictionary_status(),
        "public_metrics_updated": public_metrics_updated,
        "top_n": args.top_n,
        "live_top_n": args.live_top_n,
        "riser_top_n": args.riser_top_n,
        "live_source": args.live_source,
        "boaters_user_screenshot": {
            "enabled": True,
            "approved_dir": args.boaters_screenshot_dir,
            "description": (
                "ブラウザ巡回で保存したBOATERSスクリーンショットをローカルOCRし、"
                "6艇分のAI・展示の厳格検査に合格した原値だけを24場サインへ使う。"
            ),
        },
        "lookahead_minutes": args.lookahead_minutes,
        "preview_fetch_lookahead_minutes": args.preview_fetch_lookahead_minutes,
        "alert_threshold_pct": SUBCORE_ALERT_RATE_MIN,
        "core_alert_threshold_pct": args.core_alert_threshold,
        "subcore_alert_threshold_min_pct": SUBCORE_ALERT_RATE_MIN,
        "boaters_imitation_ai": {
            "enabled": bool(args.boaters_imitation_model),
            "model_path": args.boaters_imitation_model,
            "odds_mode": args.boaters_imitation_odds_mode if args.boaters_imitation_model else "",
            "description": (
                "ライブ取得後にAI1着率・AI3連対率・人気率をローカルBOATERS互換AIへ差し替えてから24場サインを判定する。"
                if args.boaters_imitation_model
                else ""
            ),
        },
        "self_ai": {
            "enabled": bool(args.self_ai_model),
            "model_path": args.self_ai_model,
            "mode": args.self_ai_mode if args.self_ai_model else "",
            "odds_mode": args.self_ai_odds_mode if args.self_ai_model else "",
            "description": (
                "自前AI1着率・3着内率・1号艇の1着失敗率/3着外率を記録する。shadowでは24場サイン判定を変更しない。"
                if args.self_ai_model
                else ""
            ),
        },
        "trifecta_position_model": {
            "enabled": bool(args.trifecta_position_model),
            "model_path": args.trifecta_position_model,
            "active": False,
            "notification_enabled": False,
            "description": (
                "1着・2着・3着専用モデルの120通り確率と期待値を影記録する。24場サインと買い目は変更しない。"
                if args.trifecta_position_model
                else ""
            ),
        },
        "venue_probability_overlay": {
            "enabled": bool(args.venue_probability_overlay),
            "model_path": args.venue_probability_overlay,
            "active": False,
            "notification_enabled": False,
            "description": (
                "場×艇番、展示、風・波・潮・潮流、平均との差、スーパースリットを"
                "1着・2着・3着率のポイント補正として影記録する。24場サインと買い目は変更しない。"
                if args.venue_probability_overlay
                else ""
            ),
        },
        "alert_policy": {
            "primary": "all_races_venue_sign_only",
            "description": "ライブランキングに入る全レースを締切前に確認し、場別に実装した24場サインが出た時だけ買い通知にする。既存の汎用買い候補、通常の本命候補、準本命、急浮上参考はスマホ通知しない。場別・艇番別の展示バフ/デバフS/A条件は頭候補、3着内候補、消し禁止、人気1号艇危険度の補正に使う。AI+5〜6位でも場別展示バフで3着内率差+10pt以上なら消しから復活し、+12pt以上やS評価は2/3着、頭バフかつ1着率差+8pt以上だけ頭候補にも補正する。買い目は基本絞るが最大12点まで許容し、合成オッズ3倍未満になる買い方は見送る",
            "morning_top_n": args.top_n,
            "post_exhibition_core_threshold_pct": args.core_alert_threshold,
            "post_exhibition_subcore_range_pct": None,
            "preview_fetch_lookahead_minutes": args.preview_fetch_lookahead_minutes,
            "full_exhibition_required": True,
            "tenji_only_venue_sign_strategy_ids": sorted(TENJI_ONLY_VENUE_SIGN_STRATEGY_IDS),
            "scan_all_live_races_for_venue_signs": bool(args.scan_risers),
            "notify_late_risers": False,
            "original_boaters_24_forward_notifications": True,
        },
        "alerts": alerts,
        "inspected": inspected,
    }
    if not args.no_push:
        payload["push"] = push_notifications(payload, state, now)
    payload["forward_validation_log_path"] = update_forward_validation_log(date_text, payload, now)
    try:
        original_boaters_forward_status = update_original_boaters_shadow_log(
            date_text,
            original_boaters_shadow_signs,
            now,
            monitor_payload=payload,
        )
        payload["original_boaters_24_forward"] = original_boaters_forward_status
        payload["original_boaters_24_shadow"] = original_boaters_forward_status
    except Exception as exc:
        original_boaters_forward_status = {
            "enabled": True,
            "mode": "forward_notification_enabled",
            "error": str(exc),
        }
        payload["original_boaters_24_forward"] = original_boaters_forward_status
        payload["original_boaters_24_shadow"] = original_boaters_forward_status
    save_json(alerts_path(date_text), payload)
    state["updated_at"] = now.isoformat(timespec="seconds")
    save_json(state_path(date_text), state)
    if not args.no_ops_update:
        payload["ops_dashboard"] = refresh_ops_dashboard()
        save_json(alerts_path(date_text), payload)
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="JST date, e.g. 2026-06-19. Defaults to today.")
    parser.add_argument("--now", help="Override current JST timestamp for tests.")
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--threshold", type=float, default=27.0)
    parser.add_argument("--lookahead-minutes", type=float, default=20.0)
    parser.add_argument(
        "--preview-fetch-lookahead-minutes",
        type=float,
        default=240.0,
        help=(
            "Fetch BOATERS exhibition data for display this many minutes before deadline. "
            "Normal alert sending still uses --lookahead-minutes; 24-venue signs use a 10-minute exhibition-data window."
        ),
    )
    parser.add_argument("--grace-minutes", type=float, default=2.0)
    parser.add_argument(
        "--alert-threshold",
        type=float,
        default=SUBCORE_ALERT_RATE_MIN,
        help="Backward-compatible floor for post-exhibition checks. Current buy policy alerts only core focus at 40.0+.",
    )
    parser.add_argument(
        "--core-alert-threshold",
        type=float,
        default=CORE_ALERT_RATE,
        help="Post-exhibition manshu rate that becomes a core buy alert.",
    )
    parser.add_argument("--scan-risers", action="store_true", help="Build a live ranking and scan all races for 24-venue sign alerts.")
    parser.add_argument(
        "--no-build-live",
        action="store_true",
        help="Do not rebuild the BOATERS live DB; scan existing local ranking JSON only.",
    )
    parser.add_argument(
        "--venue-sign-only",
        action="store_true",
        help="Suppress generic buy/subcore/riser alerts and notify only validated 24-venue signs.",
    )
    parser.add_argument("--live-top-n", type=int, default=400, help="Live ranking depth used to scan essentially all same-day races for 24-venue signs.")
    parser.add_argument("--riser-top-n", type=int, default=10, help="Legacy option. 24-venue sign scanning now checks every live ranking race.")
    parser.add_argument("--riser-threshold", type=float, default=CORE_ALERT_RATE, help="Legacy option. 24-venue sign notifications do not use this rate gate.")
    parser.add_argument(
        "--only-race-id",
        action="append",
        default=[],
        help="Inspect only the given race_id. Can be passed multiple times by deadline dispatchers.",
    )
    parser.add_argument(
        "--notify-risers",
        action="store_true",
        help="Legacy option for non-sign risers. 24-venue sign alerts are sent from all live races regardless of this flag.",
    )
    parser.add_argument("--rebuild-morning", action="store_true")
    parser.add_argument(
        "--no-build-morning",
        action="store_true",
        help="Do not build the morning ranking DB; use local/public ranking JSON only.",
    )
    parser.add_argument(
        "--ranking-url-base",
        default="https://mm1601.github.io/kyotei-occult-viewer/data/output",
        help="Public base URL for daily ranking JSON fallback.",
    )
    parser.add_argument("--no-refresh", action="store_true", help="Use cached BOATERS pages when available.")
    parser.add_argument(
        "--live-source",
        choices=("boaters", "official"),
        default="boaters",
        help="Live data source. 'official' uses BOATRACE official beforeinfo and trifecta odds instead of BOATERS pages.",
    )
    parser.add_argument(
        "--no-official-aux",
        action="store_true",
        help="Do not fetch BOATRACE official beforeinfo as auxiliary display/start exhibition data.",
    )
    parser.add_argument(
        "--boaters-imitation-model",
        default="",
        help=(
            "Optional local BOATERS-compatible AI joblib model. When set, live AI1着率/AI3連対率 "
            "are replaced before 24-venue sign evaluation."
        ),
    )
    parser.add_argument(
        "--boaters-screenshot-dir",
        default=str(boaters_screenshot_data.DEFAULT_APPROVED_DIR),
        help=(
            "Directory containing strictly validated BOATERS browser-screenshot JSON. "
            "A complete matching race override takes priority over local imitation AI."
        ),
    )
    parser.add_argument(
        "--boaters-imitation-odds-mode",
        choices=("keep", "imitate"),
        default="imitate",
        help="'imitate' also replaces odds_prediction_pct with the local BOATERS-compatible popularity score.",
    )
    parser.add_argument(
        "--self-ai-model",
        default="",
        help="Optional local self AI joblib model for win/top3 probabilities and boat-1 fly-risk logging.",
    )
    parser.add_argument(
        "--self-ai-mode",
        choices=("shadow", "replace"),
        default="shadow",
        help="'shadow' logs self AI without changing 24-venue sign decisions; 'replace' replaces AI fields.",
    )
    parser.add_argument(
        "--self-ai-odds-mode",
        choices=("keep", "self_win"),
        default="keep",
        help="With --self-ai-mode replace, 'self_win' also replaces odds_prediction_pct.",
    )
    default_position_model = (
        PUBLIC_OUT / "self_ai_models" / "trifecta_position_model_current.joblib"
    )
    parser.add_argument(
        "--trifecta-position-model",
        default=str(default_position_model) if default_position_model.exists() else "",
        help=(
            "Optional first/second/third-place model. It only records a second EV shadow and "
            "never changes the 24-venue sign or production tickets."
        ),
    )
    default_venue_probability_overlay = (
        PUBLIC_OUT / "self_ai_models" / "venue_probability_overlay_current.joblib"
    )
    parser.add_argument(
        "--venue-probability-overlay",
        default=(
            str(default_venue_probability_overlay)
            if default_venue_probability_overlay.exists()
            else ""
        ),
        help=(
            "Optional venue/lane probability-point overlay. It records a third EV shadow "
            "and never changes the 24-venue sign or production tickets."
        ),
    )
    parser.add_argument("--offline", action="store_true", help="Do not fetch BOATERS pages; only test scheduling windows.")
    parser.add_argument("--test-push", action="store_true", help="Send a smartphone test notification through the configured ntfy route and exit.")
    parser.add_argument("--no-push", action="store_true", help="Disable smartphone push notifications.")
    parser.add_argument("--no-public-metrics-update", action="store_true", help="Do not merge fetched exhibition metrics back into the public morning-order ranking JSON.")
    parser.add_argument("--no-ops-update", action="store_true", help="Do not rebuild the operations SQLite/summary JSON after monitoring.")
    parser.add_argument(
        "--compact-output",
        action="store_true",
        help="Print a compact run summary instead of the full alert payload.",
    )
    parser.add_argument(
        "--backfill-missing-exhibition-hours",
        type=float,
        default=12.0,
        help="After deadline, still fetch ranking races with missing exhibition data for this many hours. Set 0 to disable.",
    )
    args = parser.parse_args()
    if args.self_ai_mode == "replace" and args.boaters_imitation_model:
        parser.error("--self-ai-mode replace cannot be combined with --boaters-imitation-model")
    now = parse_dt(args.now) if args.now else datetime.now(JST)
    payload = push_test_notification(now) if args.test_push else monitor(args)
    if args.compact_output:
        inspected = payload.get("inspected") or []
        summary = {
            "date": payload.get("date"),
            "alerts": len(payload.get("alerts") or []),
            "inspected": len(inspected),
            "statuses": {},
            "preview_ready": sum(1 for row in inspected if row.get("preview_ready")),
            "fetch_failed": sum(1 for row in inspected if row.get("status") == "fetch_failed"),
            "public_metrics_updated": payload.get("public_metrics_updated"),
        }
        for row in inspected:
            status = row.get("status") or "unknown"
            summary["statuses"][status] = summary["statuses"].get(status, 0) + 1
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    push = payload.get("push") or {}
    if args.test_push:
        return 0 if push.get("ok") else 1
    if push.get("attempted", 0) and (push.get("errors") or not push.get("enabled", True)):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
