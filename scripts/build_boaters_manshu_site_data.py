#!/usr/bin/env python3
"""Build GitHub Pages JSON for the Codex BOATERS manshu ranking widget."""

from __future__ import annotations

import argparse
import ast
import csv
import json
import math
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLACE_IDS = {
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
    "大村": 24,
}


def iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def as_num(value):
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def as_int(value):
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if math.isfinite(number) else None


def format_trifecta(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) == 3:
        return "-".join(digits)
    return text


def race_place_id(row: dict) -> int | None:
    if row.get("place_id") is not None:
        return as_int(row.get("place_id"))
    race_id = str(row.get("race_id") or "")
    if len(race_id) >= 15 and race_id[:10].count("-") == 2:
        return as_int(race_id[10:12])
    return PLACE_IDS.get(str(row.get("place_name") or ""))


def result_from_openapi(row: dict) -> dict:
    trifecta = None
    payout = None
    trifectas = (row.get("payouts") or {}).get("trifecta") or []
    if trifectas:
        trifecta = trifectas[0].get("combination")
        payout = as_int(trifectas[0].get("payout"))
    if not trifecta:
        places = {}
        for boat in row.get("boats") or []:
            place = as_int(boat.get("racer_place_number"))
            lane = as_int(boat.get("racer_boat_number"))
            if place and lane:
                places[place] = lane
        if all(place in places for place in [1, 2, 3]):
            trifecta = f"{places[1]}-{places[2]}-{places[3]}"
    return {
        "trifecta": trifecta,
        "payout_yen": payout,
        "manshu": bool(payout is not None and payout >= 10000),
        "win_method": row.get("race_technique_number"),
        "weather": row.get("race_weather_number"),
        "wind_speed": as_num(row.get("race_wind")),
        "wave_height": as_num(row.get("race_wave")),
    }


def load_results_map(path_text: str | None) -> dict[tuple[int, int], dict]:
    if not path_text:
        return {}
    path = Path(path_text)
    if not path.exists():
        raise FileNotFoundError(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    results = {}
    for row in data.get("results") or []:
        place_id = as_int(row.get("race_stadium_number"))
        round_no = as_int(row.get("race_number"))
        if place_id and round_no:
            results[(place_id, round_no)] = result_from_openapi(row)
    return results


def load_csv_rows(path_text: str | None) -> list[dict]:
    if not path_text:
        return []
    path = Path(path_text)
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = []
        for row in csv.DictReader(handle):
            row = dict(row)
            if isinstance(row.get("composite_edges"), str) and row["composite_edges"].strip():
                try:
                    row["composite_edges"] = ast.literal_eval(row["composite_edges"])
                except Exception:
                    row["composite_edges"] = []
            rows.append(row)
        return rows


def unique_rows(rows: list[dict], top_n: int) -> list[dict]:
    seen = set()
    unique = []
    for row in rows:
        key = row.get("race_id") or (
            row.get("date"),
            row.get("place_name"),
            str(row.get("round") if row.get("round") is not None else row.get("round_no")),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
        if len(unique) >= top_n:
            break
    return unique


def normalize_result(row: dict, live_result: dict | None = None) -> dict:
    result = row.get("result") or {}
    payout = result.get("payout_yen")
    if payout is None:
        payout = row.get("payout_yen")
    if payout is None:
        payout = row.get("payout")
    trifecta = result.get("trifecta") or row.get("trifecta") or row.get("winning_number3t1")
    if live_result:
        trifecta = live_result.get("trifecta") or trifecta
        payout = live_result.get("payout_yen") if live_result.get("payout_yen") is not None else payout
    return {
        "trifecta": format_trifecta(trifecta),
        "payout_yen": as_int(payout),
        "manshu": bool(as_int(payout) and as_int(payout) >= 10000),
        "win_method": (live_result or {}).get("win_method") or result.get("win_method"),
        "weather": (live_result or {}).get("weather") or result.get("weather") or row.get("weather"),
        "wind_speed": as_num(
            (live_result or {}).get("wind_speed")
            if (live_result or {}).get("wind_speed") is not None
            else result.get("wind_speed")
            if result.get("wind_speed") is not None
            else row.get("wind_speed")
        ),
        "wave_height": as_num(
            (live_result or {}).get("wave_height")
            if (live_result or {}).get("wave_height") is not None
            else result.get("wave_height")
            if result.get("wave_height") is not None
            else row.get("wave_height")
        ),
    }


LANE_WIN_PRIOR = {1: 52.0, 2: 15.0, 3: 12.0, 4: 10.0, 5: 7.0, 6: 4.0}
LANE_TOP3_PRIOR = {1: 78.0, 2: 58.0, 3: 52.0, 4: 47.0, 5: 37.0, 6: 28.0}


def parse_boat_numbers(value) -> set[int]:
    if isinstance(value, list):
        parts = value
    else:
        parts = str(value or "").replace("、", ",").split(",")
    boats = set()
    for part in parts:
        number = as_int(part)
        if number and 1 <= number <= 6:
            boats.add(number)
    return boats


def bounded(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def pct_to_logit(value, default=50.0) -> float:
    base = default if value is None else value
    p = bounded(float(base) / 100.0, 0.01, 0.99)
    return math.log(p / (1.0 - p))


def sigmoid_pct(score: float) -> float:
    return 100.0 / (1.0 + math.exp(-bounded(score, -12.0, 12.0)))


def normalize_total(values: list[float], total: float, low: float, high: float) -> list[float]:
    if not values:
        return []
    positive = [max(0.01, value) for value in values]
    scale = total / sum(positive)
    rates = [bounded(value * scale, low, high) for value in positive]
    for _ in range(10):
        diff = total - sum(rates)
        if abs(diff) < 0.01:
            break
        free = [idx for idx, value in enumerate(rates) if (value < high - 0.01 if diff > 0 else value > low + 0.01)]
        if not free:
            break
        step = diff / len(free)
        for idx in free:
            rates[idx] = bounded(rates[idx] + step, low, high)
    return [round(value, 2) for value in rates]


POPULAR_B1_BASE_NOT_WIN = 31.87
POPULAR_B1_BASE_TOP3_MISS = 10.28
POPULAR_B1_BASE_MANSHU = 15.62


def best_popular_b1_edges(edges: list[dict]) -> list[dict]:
    popular_edges = []
    for edge in edges or []:
        if edge.get("role") != "popular_b1_fly_up":
            continue
        details = edge.get("details") or {}
        popular_edges.append(
            {
                "id": edge.get("id") or "",
                "label": edge.get("label") or "人気1号艇飛び条件",
                "sample_races": as_int(details.get("sample_races")),
                "b1_not_win_rate_pct": as_num(details.get("b1_not_win_rate_pct")),
                "b1_top3_miss_rate_pct": as_num(details.get("b1_top3_miss_pct")),
                "manshu_rate_pct": as_num(details.get("manshu_rate_pct")),
            }
        )
    return sorted(
        popular_edges,
        key=lambda item: (
            item.get("b1_not_win_rate_pct") or 0,
            item.get("manshu_rate_pct") or 0,
            item.get("sample_races") or 0,
        ),
        reverse=True,
    )


def verified_popular_b1_exhibition_conditions(metrics: dict, round_no: int | None) -> list[dict]:
    """検証済みの「人気1号艇＋展示悪化＋外枠上振れ」条件。

    分母は保存済みオッズで三連単人気上位5点が1号艇頭だったレース。
    2026-06-02〜2026-06-18のBOATERS展示結合データで確認した率を表示用に持つ。
    """

    b1_nige = as_num(metrics.get("boat1_nige_pct"))
    b1_avg = as_num(metrics.get("boat1_avg_isshu_diff"))
    b1_tenji_rank = as_int(metrics.get("boat1_tenji_time_rank")) or as_int(metrics.get("boat1_tenji_rank"))
    outer56_avg = as_num(metrics.get("outer56_best_avg_isshu_diff"))
    outer56_ai = as_num(metrics.get("outer56_best_ai_prediction_pct"))
    outer56_exhibit_top2 = as_int(metrics.get("outer56_exhibit_top2_count")) or 0
    ai_rank6_tenji = as_int(metrics.get("ai_rank6_tenji_rank"))
    ai_rank6_isshu = as_int(metrics.get("ai_rank6_isshu_rank"))
    ai_rank5_tenji = as_int(metrics.get("ai_rank5_tenji_rank"))
    ai_rank5_isshu = as_int(metrics.get("ai_rank5_isshu_rank"))
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


def estimated_popular_b1_rate(score: float, base: float, slope: float, high: float) -> float:
    return round(bounded(base + max(0.0, score - 40.0) * slope, base, high), 2)


def build_popular_b1_fly_logic(metrics: dict, edges: list[dict], round_no: int | None) -> dict:
    boats = metrics.get("boats") if isinstance(metrics.get("boats"), list) else []
    by_boat = {as_int(item.get("boat_number")): item for item in boats}
    b1 = by_boat.get(1) or {}
    b5 = by_boat.get(5) or {}
    b6 = by_boat.get(6) or {}

    trifecta_top5 = as_int(metrics.get("b1_trifecta_top5_1head")) == 1
    top5_head_count = as_int(metrics.get("trifecta_top5_head1_count")) or 0
    top5_count = as_int(metrics.get("trifecta_top5_count")) or 0
    odds_rank = as_int(metrics.get("boat1_odds_rank"))
    odds_pct = as_num(metrics.get("boat1_odds_prediction_pct"))
    odds_popular45 = odds_rank == 1 and odds_pct is not None and odds_pct >= 45
    odds_popular40 = odds_rank == 1 and odds_pct is not None and odds_pct >= 40
    top5_almost = top5_count >= 5 and top5_head_count >= 4

    if trifecta_top5:
        popular_source = "三連単人気上位5点がすべて1号艇頭"
        popular_strength = 42
    elif odds_popular45:
        popular_source = "BOATERSのAIオッズ評価で1号艇が45%以上の1位"
        popular_strength = 36
    elif odds_popular40:
        popular_source = "BOATERSのAIオッズ評価で1号艇が40%以上の1位"
        popular_strength = 30
    elif top5_almost:
        popular_source = "三連単人気上位5点のうち4点以上が1号艇頭"
        popular_strength = 26
    else:
        return {
            "popular_b1_is_popular": False,
            "popular_b1_source": "",
            "popular_b1_fly_score": 0,
            "popular_b1_fly_level": "人気不足",
            "popular_b1_not_win_rate_pct": None,
            "popular_b1_top3_miss_rate_pct": None,
            "popular_b1_manshu_rate_pct": None,
            "popular_b1_rate_source": "",
            "popular_b1_reasons": [],
            "popular_b1_matched_conditions": [],
        }

    score = float(popular_strength)
    reasons = [popular_source]
    checks = []

    def add(points: float, reason: str, check_id: str) -> None:
        nonlocal score
        score += points
        reasons.append(reason)
        checks.append(check_id)

    b1_nige = as_num(metrics.get("boat1_nige_pct"))
    if b1_nige is not None:
        if b1_nige < 45:
            add(18, f"1号艇の逃げ率が{b1_nige:.1f}%で低い", "b1_nige_lt45")
        elif b1_nige < 50:
            add(12, f"1号艇の逃げ率が{b1_nige:.1f}%で50%を切っている", "b1_nige_lt50")

    b1_loss = as_num(metrics.get("boat1_loss_pct"))
    if b1_loss is not None:
        if b1_loss >= 50:
            add(16, f"1号艇の差され・まくられ率が{b1_loss:.1f}%で高い", "b1_loss_ge50")
        elif b1_loss >= 40:
            add(10, f"1号艇の差され・まくられ率が{b1_loss:.1f}%で高め", "b1_loss_ge40")

    b1_avg = as_num(metrics.get("boat1_avg_isshu_diff"))
    if b1_avg is None:
        b1_avg = as_num(b1.get("avg_isshu_diff"))
    if b1_avg is not None:
        if b1_avg <= 0:
            add(18, f"1号艇の展示+有効ラップが6艇平均より悪い（平均との差{b1_avg:.2f}）", "b1_avgdiff_le000")
        elif b1_avg <= 0.15:
            add(14, f"1号艇の展示+有効ラップが強くない（平均との差{b1_avg:.2f}）", "b1_avgdiff_le015")
        elif b1_avg <= 0.30:
            add(8, f"1号艇の展示+有効ラップが平均より少し良い程度（平均との差{b1_avg:.2f}）", "b1_avgdiff_le030")

    b1_isshu = as_num(metrics.get("boat1_isshu_avg_diff"))
    if b1_isshu is not None:
        if b1_isshu <= -0.10:
            add(12, f"1号艇の有効ラップが平均より{abs(b1_isshu):.2f}秒遅い", "b1_isshu_le_m010")
        elif b1_isshu <= -0.05:
            add(8, f"1号艇の有効ラップが平均より{abs(b1_isshu):.2f}秒遅い", "b1_isshu_le_m005")

    b1_exhibit_rank = min(
        as_int(b1.get("tenji_time_rank")) or 9,
        as_int(b1.get("isshu_time_rank")) or 9,
        as_int(b1.get("exhibit_rank")) or 9,
    )
    if b1_exhibit_rank >= 4 and b1_exhibit_rank < 9:
        add(12, f"1号艇の展示順位が{b1_exhibit_rank}位で目立たない", "b1_exhibit_rank_ge4")
    elif b1_exhibit_rank == 3:
        add(7, "1号艇の展示順位が3位で抜けていない", "b1_exhibit_rank_ge3")

    outer56_avg = as_num(metrics.get("outer56_best_avg_isshu_diff"))
    if outer56_avg is None:
        outer56_avg = max(
            as_num(b5.get("avg_isshu_diff")) or -99,
            as_num(b6.get("avg_isshu_diff")) or -99,
        )
        if outer56_avg == -99:
            outer56_avg = None
    if outer56_avg is not None:
        if outer56_avg >= 0.14:
            add(14, f"5/6号艇に展示+有効ラップがかなり良い艇がいる（平均との差+{outer56_avg:.2f}）", "outer56_avg_ge014")
        elif outer56_avg >= 0.10:
            add(10, f"5/6号艇に展示+有効ラップが良い艇がいる（平均との差+{outer56_avg:.2f}）", "outer56_avg_ge010")
        elif outer56_avg >= 0.05:
            add(6, f"5/6号艇に展示+有効ラップが少し良い艇がいる（平均との差+{outer56_avg:.2f}）", "outer56_avg_ge005")

    outer56_ai = as_num(metrics.get("outer56_best_ai_prediction_pct"))
    if outer56_ai is not None:
        if outer56_ai >= 12:
            add(10, f"5/6号艇にAI1着予測{outer56_ai:.1f}%以上の艇がいる", "outer56_ai_ge12")
        elif outer56_ai >= 10:
            add(8, f"5/6号艇にAI1着予測{outer56_ai:.1f}%の艇がいる", "outer56_ai_ge10")

    outer56_top2 = any((as_int((by_boat.get(boat) or {}).get("exhibit_rank")) or 9) <= 2 for boat in (5, 6))
    if outer56_top2:
        add(10, "5/6号艇のどちらかが展示か1周で2位以内", "outer56_exhibit_top2")

    if (as_int(metrics.get("outer56_super_slit_count")) or 0) >= 1:
        add(10, "5/6号艇にスーパースリットアラート", "outer56_super_slit")
    elif (as_int(metrics.get("outer456_super_slit_count")) or 0) >= 1:
        add(7, "4〜6号艇にスーパースリットアラート", "outer456_super_slit")
    if (as_int(metrics.get("slit_outer56_pressure_vs_1")) or 0) >= 1:
        add(8, "スリット隊形で5/6号艇が1号艇に圧をかける形", "outer56_pressure_vs_1")

    if round_no is not None and round_no <= 6:
        add(6, "前半1〜6Rで荒れやすい時間帯", "round_1to6")

    matched_by_key = {}
    for item in best_popular_b1_edges(edges) + verified_popular_b1_exhibition_conditions(metrics, round_no):
        stats_key = (
            item.get("sample_races"),
            item.get("b1_not_win_rate_pct"),
            item.get("b1_top3_miss_rate_pct"),
            item.get("manshu_rate_pct"),
        )
        if stats_key == (None, None, None, None):
            stats_key = (item.get("id") or item.get("label"),)
        existing = matched_by_key.get(stats_key)
        if existing is None or str(item.get("id") or "").startswith("codex_popular_b1_verified"):
            matched_by_key[stats_key] = item
    matched = sorted(
        matched_by_key.values(),
        key=lambda item: (
            item.get("b1_not_win_rate_pct") or 0,
            item.get("manshu_rate_pct") or 0,
            item.get("sample_races") or 0,
        ),
        reverse=True,
    )
    if matched:
        add(15, "保存済みの人気1号艇飛び条件に一致", "matched_popular_b1_condition")

    score = round(bounded(score, 0.0, 100.0), 1)
    if score >= 75:
        level = "超危険"
    elif score >= 60:
        level = "危険"
    elif score >= 45:
        level = "注意"
    else:
        level = "人気だが鉄板寄り"

    if matched:
        not_win = max((item.get("b1_not_win_rate_pct") or 0 for item in matched), default=0) or None
        top3_miss = max((item.get("b1_top3_miss_rate_pct") or 0 for item in matched), default=0) or None
        manshu = max((item.get("manshu_rate_pct") or 0 for item in matched), default=0) or None
        rate_source = "保存済み同型条件"
    else:
        not_win = estimated_popular_b1_rate(score, POPULAR_B1_BASE_NOT_WIN, 0.62, 72.0)
        top3_miss = estimated_popular_b1_rate(score, POPULAR_B1_BASE_TOP3_MISS, 0.36, 43.0)
        manshu = estimated_popular_b1_rate(score, POPULAR_B1_BASE_MANSHU, 0.25, 36.0)
        rate_source = "危険度からの目安"

    return {
        "popular_b1_is_popular": True,
        "popular_b1_source": popular_source,
        "popular_b1_fly_score": score,
        "popular_b1_fly_level": level,
        "popular_b1_not_win_rate_pct": round(not_win, 2) if not_win is not None else None,
        "popular_b1_top3_miss_rate_pct": round(top3_miss, 2) if top3_miss is not None else None,
        "popular_b1_manshu_rate_pct": round(manshu, 2) if manshu is not None else None,
        "popular_b1_rate_source": rate_source,
        "popular_b1_reasons": reasons[:7],
        "popular_b1_checks": checks,
        "popular_b1_matched_conditions": matched[:3],
    }


def rank_rows(rows: list[dict], key: str, ascending: bool) -> None:
    values = sorted(
        {row.get(key) for row in rows if as_num(row.get(key)) is not None},
        reverse=not ascending,
    )
    rank_by_value = {value: idx + 1 for idx, value in enumerate(values)}
    for row in rows:
        row[f"{key}_rank"] = rank_by_value.get(row.get(key), 9)


def composite_rate_reasons(row: dict, by_boat: dict[int, dict]) -> list[str]:
    boat = row["boat_number"]
    reasons: list[str] = []
    ai_plus_rank = as_int(row.get("ai_plus_rank"))
    if ai_plus_rank and ai_plus_rank <= 2:
        reasons.append(f"AI+一般3連対が{ai_plus_rank}位")
    elif ai_plus_rank and ai_plus_rank >= 5:
        reasons.append(f"AI+一般3連対が{ai_plus_rank}位で弱め")
    if row.get("double_time"):
        reasons.append("展示タイムと有効ラップが両方1位")
    elif (as_int(row.get("exhibit_rank")) or 9) <= 2:
        reasons.append("展示か1周が2位以内")
    avg_diff = as_num(row.get("avg_isshu_diff"))
    if avg_diff is not None:
        if avg_diff >= 0.10:
            reasons.append(f"展示+有効ラップが平均より{avg_diff:.2f}秒速い")
        elif avg_diff <= -0.10:
            reasons.append(f"展示+有効ラップが平均より{abs(avg_diff):.2f}秒遅い")
    if row.get("super_slit_alert"):
        reasons.append("スーパースリットアラート")
    right = by_boat.get(boat + 1)
    if right and right.get("super_slit_alert"):
        reasons.append(f"{boat + 1}号艇のスリット圧を受ける")
    if row.get("summer_b1_isshu_factor") == "fast_hold":
        reasons.append("夏場の有効ラップが良くイン残り寄り")
    elif row.get("summer_b1_isshu_factor") == "slow_fly":
        reasons.append("夏場の有効ラップが悪くイン飛び寄り")
    if row.get("matchup_label") in {"1号艇キラー", "相性バフ", "相性軸バフ", "相性デバフ"}:
        reasons.append(str(row.get("matchup_label")))
    if row.get("low_outer_revive"):
        reasons.append("低評価外枠だが展示で復活")
    if row.get("longshot_head_candidate"):
        reasons.append("穴頭候補に一致")
    return reasons[:4] or ["AI・3連対率・展示/スリット材料を総合"]


def build_boat_rows(normalized_metrics: dict, source_metrics: dict) -> list[dict]:
    source_boats = source_metrics.get("boats") if isinstance(source_metrics.get("boats"), list) else []
    by_boat = {}
    for item in source_boats:
        boat = as_int(item.get("boat_number"))
        if boat and 1 <= boat <= 6:
            by_boat[boat] = dict(item)
    double_time_boats = parse_boat_numbers(normalized_metrics.get("double_time_boats"))
    super_slit_boats = parse_boat_numbers(normalized_metrics.get("super_slit_boats"))
    matchup_boats = parse_boat_numbers(normalized_metrics.get("matchup_buff_boats"))
    longshot_head_boats = parse_boat_numbers(normalized_metrics.get("longshot_head_boats"))
    low_outer_boat = as_int(normalized_metrics.get("low_outer_boat"))
    rows = []
    for boat in range(1, 7):
        item = by_boat.get(boat, {})
        ai_top3 = as_num(item.get("top3_pct"))
        general = as_num(item.get("general_top3_pct"))
        ai_plus = as_num(item.get("ai_plus"))
        if ai_plus is None and ai_top3 is not None and general is not None:
            ai_plus = ai_top3 + general
        row = {
            "boat_number": boat,
            "win_pct": as_num(item.get("win_pct")) if item.get("win_pct") is not None else as_num(normalized_metrics.get(f"boat{boat}_ai_prediction_pct")),
            "top3_pct": ai_top3 if ai_top3 is not None else as_num(normalized_metrics.get(f"boat{boat}_ai_3ren_pct")),
            "general_top3_pct": general if general is not None else as_num(normalized_metrics.get(f"boat{boat}_general_3ren_pct")),
            "ai_plus": ai_plus if ai_plus is not None else as_num(normalized_metrics.get(f"boat{boat}_ai_plus")),
            "ai_plus_rank": as_int(item.get("ai_plus_rank")) if item.get("ai_plus_rank") is not None else as_int(normalized_metrics.get(f"boat{boat}_ai_plus_order")),
            "odds_prediction_pct": as_num(item.get("odds_prediction_pct")) if item.get("odds_prediction_pct") is not None else as_num(normalized_metrics.get(f"boat{boat}_odds_prediction_pct")),
            "odds_prediction_rank": as_int(item.get("odds_prediction_rank")) if item.get("odds_prediction_rank") is not None else as_int(normalized_metrics.get(f"boat{boat}_odds_rank")),
            "st_rank_general": as_num(item.get("st_rank_general")) if item.get("st_rank_general") is not None else as_num(normalized_metrics.get(f"boat{boat}_st_rank_general")),
            "tenji_time": as_num(item.get("tenji_time")) if item.get("tenji_time") is not None else as_num(normalized_metrics.get(f"boat{boat}_tenji_time")),
            "tenji_rank": as_int(item.get("tenji_rank")) if item.get("tenji_rank") is not None else as_int(normalized_metrics.get(f"boat{boat}_tenji_rank")),
            "isshu_time": as_num(item.get("isshu_time")) if item.get("isshu_time") is not None else as_num(normalized_metrics.get(f"boat{boat}_isshu_time")),
            "isshu_rank": as_int(item.get("isshu_rank")) if item.get("isshu_rank") is not None else as_int(normalized_metrics.get(f"boat{boat}_isshu_rank")),
            "avg_isshu_diff": as_num(item.get("avg_isshu_diff")) if item.get("avg_isshu_diff") is not None else as_num(normalized_metrics.get(f"boat{boat}_avg_isshu_diff")),
            "double_time": bool(item.get("double_time")) or boat in double_time_boats or bool(normalized_metrics.get(f"boat{boat}_double_time")),
            "super_slit_alert": bool(item.get("super_slit_alert")) or boat in super_slit_boats,
            "super_slit_tenji_adv": as_num(item.get("super_slit_tenji_adv")),
            "super_slit_st_rank_adv": as_num(item.get("super_slit_st_rank_adv")),
            "matchup_label": item.get("matchup_label") or normalized_metrics.get(f"b{boat}_matchup_label") or "",
            "matchup_buff": boat in matchup_boats,
            "low_outer_revive": bool(boat == low_outer_boat and low_outer_boat in {5, 6}),
            "longshot_head_candidate": boat in longshot_head_boats,
            "summer_b1_isshu_factor": normalized_metrics.get("b1_summer_isshu_factor") if boat == 1 else "",
        }
        row["three_ren_pct"] = row["ai_plus"] if row["ai_plus"] is not None else row["top3_pct"] if row["top3_pct"] is not None else row["general_top3_pct"]
        rows.append(row)

    explicit_ai_plus_ranks = {row["boat_number"]: row.get("ai_plus_rank") for row in rows}
    explicit_odds_ranks = {row["boat_number"]: row.get("odds_prediction_rank") for row in rows}
    rank_rows(rows, "win_pct", ascending=False)
    rank_rows(rows, "odds_prediction_pct", ascending=False)
    rank_rows(rows, "top3_pct", ascending=False)
    rank_rows(rows, "general_top3_pct", ascending=False)
    if sum(1 for row in rows if row.get("ai_plus") is not None) >= 2:
        rank_rows(rows, "ai_plus", ascending=False)
    else:
        for row in rows:
            row["ai_plus_rank"] = None
    for row in rows:
        if explicit_ai_plus_ranks.get(row["boat_number"]) is not None:
            row["ai_plus_rank"] = explicit_ai_plus_ranks[row["boat_number"]]
        if explicit_odds_ranks.get(row["boat_number"]) is not None:
            row["odds_prediction_rank"] = explicit_odds_ranks[row["boat_number"]]
        else:
            row["odds_prediction_rank"] = row.get("odds_prediction_pct_rank")
    rank_rows(rows, "tenji_time", ascending=True)
    rank_rows(rows, "isshu_time", ascending=True)
    for row in rows:
        row["exhibit_rank"] = min(as_int(row.get("tenji_rank")) or row.get("tenji_time_rank") or 9, as_int(row.get("isshu_rank")) or row.get("isshu_time_rank") or 9)
    compute_composite_boat_rates(rows, normalized_metrics)
    return rows


def compute_composite_boat_rates(rows: list[dict], metrics: dict) -> None:
    by_boat = {row["boat_number"]: row for row in rows}
    win_scores = []
    top3_scores = []
    for row in rows:
        boat = row["boat_number"]
        lane_win = LANE_WIN_PRIOR[boat]
        lane_top3 = LANE_TOP3_PRIOR[boat]
        ai_pred = as_num(row.get("win_pct"))
        if boat == 1 and as_num(metrics.get("boat1_nige_pct")) is not None:
            ai_pred = (ai_pred * 0.60 + as_num(metrics.get("boat1_nige_pct")) * 0.40) if ai_pred is not None else as_num(metrics.get("boat1_nige_pct"))
        ai_top3 = as_num(row.get("top3_pct"))
        general = as_num(row.get("general_top3_pct"))
        ai_plus_rank = as_num(row.get("ai_plus_rank")) or 4.0
        exhibit_rank = as_num(row.get("exhibit_rank")) or 4.0
        st_rank = as_num(row.get("st_rank_general")) or 4.0
        avg_diff = bounded(as_num(row.get("avg_isshu_diff")) or 0.0, -0.35, 0.35)

        win_score = math.log(max(ai_pred if ai_pred is not None else lane_win, 0.1))
        win_score += (3.5 - ai_plus_rank) * 0.08
        win_score += (3.5 - exhibit_rank) * 0.07
        win_score += (3.5 - st_rank) * 0.035
        win_score += avg_diff * 1.10
        if row.get("double_time"):
            win_score += 0.16 if boat == 1 else 0.25
        if row.get("super_slit_alert"):
            win_score += 0.22 if boat in {2, 3} else 0.30
        right = by_boat.get(boat + 1)
        if right and right.get("super_slit_alert"):
            win_score -= 0.22 if boat == 1 else 0.12
        if row.get("low_outer_revive"):
            win_score += 0.15
        if row.get("longshot_head_candidate"):
            win_score += 0.10
        if row.get("summer_b1_isshu_factor") == "fast_hold":
            win_score += 0.18
        elif row.get("summer_b1_isshu_factor") == "slow_fly":
            win_score -= 0.22
        if row.get("matchup_label") == "1号艇キラー":
            win_score += 0.22
        elif row.get("matchup_label") == "相性バフ" or row.get("matchup_buff"):
            win_score += 0.18
        elif row.get("matchup_label") == "相性軸バフ":
            win_score += 0.12
        elif row.get("matchup_label") == "相性デバフ":
            win_score -= 0.18
        if boat == 1 and metrics.get("matchup_lane1_bad_flag"):
            win_score -= 0.14
        if boat == 1 and as_num(metrics.get("boat1_loss_pct")) is not None and as_num(metrics.get("boat1_loss_pct")) >= 50:
            win_score -= 0.16
        win_scores.append(win_score)

        if ai_top3 is not None and general is not None:
            base_top3 = ai_top3 * 0.62 + general * 0.38
        elif ai_top3 is not None:
            base_top3 = ai_top3
        elif general is not None:
            base_top3 = general
        else:
            base_top3 = lane_top3
        top3_score = pct_to_logit(base_top3, default=lane_top3)
        top3_score += (3.5 - ai_plus_rank) * 0.12
        top3_score += (3.5 - exhibit_rank) * 0.07
        top3_score += (3.5 - st_rank) * 0.04
        top3_score += avg_diff * 1.20
        if row.get("double_time"):
            top3_score += 0.14 if boat == 1 else 0.22
        if row.get("super_slit_alert"):
            top3_score += 0.20 if boat in {2, 3} else 0.26
        if right and right.get("super_slit_alert"):
            top3_score -= 0.16 if boat == 1 else 0.08
        if row.get("low_outer_revive"):
            top3_score += 0.16
        if row.get("summer_b1_isshu_factor") == "fast_hold":
            top3_score += 0.14
        elif row.get("summer_b1_isshu_factor") == "slow_fly":
            top3_score -= 0.18
        if row.get("matchup_label") == "1号艇キラー":
            top3_score += 0.14
        elif row.get("matchup_label") == "相性バフ" or row.get("matchup_buff"):
            top3_score += 0.12
        elif row.get("matchup_label") == "相性軸バフ":
            top3_score += 0.10
        elif row.get("matchup_label") == "相性デバフ":
            top3_score -= 0.16
        top3_scores.append(sigmoid_pct(top3_score))

    max_score = max(win_scores) if win_scores else 0.0
    win_weights = [math.exp(score - max_score) for score in win_scores]
    win_rates = normalize_total(win_weights, 100.0, 1.0, 70.0)
    top3_actual_rates = normalize_total(top3_scores, 300.0, 5.0, 92.0)
    top3_share_rates = normalize_total(top3_scores, 100.0, 1.0, 45.0)
    for idx, row in enumerate(rows):
        row["composite_win_pct"] = win_rates[idx]
        row["composite_top3_pct"] = top3_share_rates[idx]
        row["composite_top3_actual_pct"] = top3_actual_rates[idx]
        row["composite_rate_reasons"] = composite_rate_reasons(row, by_boat)


def rank_boats_for_key(rows: list[dict], key: str, ranks: tuple[int, ...]) -> list[int]:
    ranked = sorted(
        [row for row in rows if as_num(row.get(key)) is not None],
        key=lambda row: (-(as_num(row.get(key)) or 0.0), row["boat_number"]),
    )
    out = []
    for rank in ranks:
        if 1 <= rank <= len(ranked):
            out.append(ranked[rank - 1]["boat_number"])
    return list(dict.fromkeys(out))


def visible_axis_candidates(rows: list[dict], ranks: tuple[int, ...] = (1, 3)) -> tuple[list[int], str]:
    rank_label = "と".join(f"{rank}位" for rank in ranks)
    if sum(1 for row in rows if as_num(row.get("ai_plus")) is not None) >= max(ranks):
        return rank_boats_for_key(rows, "ai_plus", ranks), f"AI3連対率+一般3連対率の{rank_label}"
    if sum(1 for row in rows if as_num(row.get("top3_pct")) is not None) >= max(ranks):
        return rank_boats_for_key(rows, "top3_pct", ranks), f"AI+一般3連対が不足したためAI3連対率の{rank_label}"
    return rank_boats_for_key(rows, "composite_top3_actual_pct", ranks), f"AI+一般3連対が不足したため複合3着内率の{rank_label}"


def edge_head_boost(boat: int, metrics: dict) -> tuple[float, list[str]]:
    boost = 0.0
    reasons = []
    longshot_boats = {
        as_int(part)
        for part in str(metrics.get("longshot_head_boats") or "").replace("、", ",").split(",")
        if as_int(part) is not None
    }
    if boat in longshot_boats:
        boost += 7.0
        reasons.append("穴頭候補に一致")
    if as_int(metrics.get("low_outer_boat")) == boat:
        boost += 5.0
        reasons.append("低評価外枠の復活候補")
    for edge in metrics.get("composite_edges") or []:
        details = edge.get("details") or {}
        signal = str(details.get("signal") or edge.get("id") or "")
        role = str(edge.get("role") or "")
        if signal == "b5_left_adv" and boat == 5:
            boost += 7.0
            reasons.append("スリットで5号艇が左より良い")
        elif signal == "b6_left_adv" and boat == 6:
            boost += 7.0
            reasons.append("スリットで6号艇が左より良い")
        elif signal in {"b2_wall_break_3peek", "b3_peek_vs_12"} and boat == 3:
            boost += 5.0
            reasons.append("3号艇がのぞく形")
        elif signal == "b4_cadou_peek" and boat == 4:
            boost += 5.0
            reasons.append("4カドがのぞく形")
        elif signal == "outer56_pressure_vs_1" and boat in {5, 6}:
            boost += 4.0
            reasons.append("5/6外圧")
        elif signal == "outer456_pressure" and boat in {4, 5, 6}:
            boost += 3.0
            reasons.append("4〜6外圧")
        elif signal == "center34_dent" and boat in {5, 6}:
            boost += 3.0
            reasons.append("3/4中凹みで外が入りやすい")
        elif signal == "b1_hole_vs_23" and boat == 3:
            boost += 3.0
            reasons.append("1号艇が凹み3に出番")
        if role == "head_up" and boat in {3, 4, 5, 6}:
            boost += 3.0
            reasons.append("過去条件で穴頭寄り")
    return boost, reasons[:3]


def b1_unpopular_head_signal(row: dict, metrics: dict) -> tuple[bool, str]:
    trifecta_top5 = as_int(metrics.get("b1_trifecta_top5_1head")) == 1
    top5_head_count = as_int(metrics.get("trifecta_top5_head1_count")) or 0
    top5_count = as_int(metrics.get("trifecta_top5_count")) or 0
    odds_rank = as_int(metrics.get("boat1_odds_rank"))
    odds_pct = as_num(metrics.get("boat1_odds_prediction_pct"))
    has_popularity_data = top5_count >= 5 or odds_rank is not None or odds_pct is not None
    if not has_popularity_data:
        return False, ""
    top5_almost = top5_count >= 5 and top5_head_count >= 4
    odds_heavy = odds_rank == 1 and odds_pct is not None and odds_pct >= 40
    is_unpopular = (not trifecta_top5) and (not top5_almost) and (not odds_heavy)
    if not is_unpopular:
        return False, ""

    raw_win = as_num(row.get("composite_win_pct"))
    if raw_win is None:
        raw_win = as_num(row.get("win_pct"))
    ai_pred = as_num(row.get("win_pct")) or as_num(metrics.get("boat1_ai_prediction_pct"))
    nige = as_num(metrics.get("boat1_nige_pct"))
    loss = as_num(metrics.get("boat1_loss_pct"))
    avg_diff = as_num(row.get("avg_isshu_diff")) or as_num(metrics.get("boat1_avg_isshu_diff"))
    ai_plus_rank = as_int(row.get("ai_plus_rank")) or as_int(metrics.get("boat1_ai_plus_order")) or 9
    strong_time = (
        bool(row.get("double_time"))
        or (avg_diff is not None and avg_diff >= 0.10)
        or metrics.get("b1_summer_isshu_factor") == "fast_hold"
    )
    strong_head = (
        (raw_win is not None and raw_win >= 42.0 and (loss is None or loss < 55.0))
        or ((ai_pred or 0) >= 45.0 and (nige or 0) >= 50.0 and (loss is None or loss < 45.0))
        or ((nige or 0) >= 55.0 and (loss is None or loss < 35.0))
        or ((raw_win or 0) >= 35.0 and strong_time and (loss is None or loss < 50.0))
        or (ai_plus_rank <= 2 and (nige or 0) >= 50.0 and (loss is None or loss < 45.0))
    )
    if not strong_head:
        return False, ""
    popularity_text = "人気薄"
    if odds_rank == 1 and odds_pct is not None:
        popularity_text = f"1号艇オッズ評価{odds_pct:.1f}%"
    elif top5_count >= 5:
        popularity_text = f"人気上位5点中1号艇頭{top5_head_count}点"
    return True, f"{popularity_text}で売れすぎではないが逃げ材料が強い"


def head_candidate_score(row: dict, metrics: dict, manshu_head_mode: bool = False) -> tuple[float, list[str]]:
    boat = row["boat_number"]
    score = as_num(row.get("composite_win_pct"))
    if score is None:
        score = as_num(row.get("win_pct"))
    if score is None:
        score = LANE_WIN_PRIOR.get(boat, 10.0)
    reasons = [f"複合1着率{score:.1f}%"]
    if manshu_head_mode and boat in {3, 4, 5, 6}:
        score += 8.0
        reasons.append("万舟は3〜6号艇頭が多い")
        edge_boost, edge_reasons = edge_head_boost(boat, metrics)
        if edge_boost:
            score += edge_boost
            reasons.extend(edge_reasons)
    if boat == 1:
        danger = as_num(metrics.get("popular_b1_fly_score")) or 0.0
        loss = as_num(metrics.get("boat1_loss_pct"))
        unpopular_hold, unpopular_reason = b1_unpopular_head_signal(row, metrics)
        if unpopular_hold:
            score += 12.0
            reasons.append(unpopular_reason)
        if danger >= 75:
            score -= 18.0
            reasons.append("人気1号艇の超危険で頭評価を下げ")
        elif danger >= 60:
            score -= 12.0
            reasons.append("人気1号艇の危険で頭評価を下げ")
        elif loss is not None and loss >= 55:
            score -= 7.0
            reasons.append(f"逃げ失敗{loss:.1f}%で頭評価を下げ")
        if metrics.get("b1_summer_isshu_factor") == "fast_hold":
            score += 5.0
            reasons.append("夏場1周が良くイン残り寄り")
        elif metrics.get("b1_summer_isshu_factor") == "slow_fly":
            score -= 6.0
            reasons.append("夏場1周が悪くイン飛び寄り")
    if row.get("double_time"):
        score += 7.0
        reasons.append("ダブルタイム")
    if row.get("super_slit_alert"):
        score += 7.0 if boat in {2, 3} else 9.0
        reasons.append("スーパースリット")
    if row.get("low_outer_revive"):
        score += 5.0
        reasons.append("低評価外枠の展示復活")
    if row.get("longshot_head_candidate"):
        score += 5.0
        reasons.append("人気薄頭候補")
    avg_diff = as_num(row.get("avg_isshu_diff"))
    if avg_diff is not None:
        if avg_diff >= 0.20:
            score += 5.0
            reasons.append(f"展示+有効ラップ平均との差+{avg_diff:.2f}")
        elif avg_diff >= 0.10:
            score += 3.0
            reasons.append(f"展示+有効ラップ平均との差+{avg_diff:.2f}")
        elif avg_diff <= -0.10:
            score -= 3.0
            reasons.append(f"展示+有効ラップ平均との差{avg_diff:.2f}")
    exhibit_rank = as_int(row.get("exhibit_rank")) or 9
    if exhibit_rank <= 2:
        score += 3.0
        reasons.append("展示か1周が2位以内")
    ai_plus_rank = as_int(row.get("ai_plus_rank"))
    if ai_plus_rank and ai_plus_rank <= 2:
        score += 2.0
        reasons.append(f"AI+{ai_plus_rank}位")
    elif ai_plus_rank and ai_plus_rank >= 5:
        score -= 2.0
        reasons.append(f"AI+{ai_plus_rank}位")
    if boat in {5, 6} and metrics.get("slit_outer56_pressure_vs_1"):
        score += 2.5
        reasons.append("5/6外圧")
    return round(score, 3), reasons[:4]


def inner_head_exception(row: dict, outer_cut_score: float, metrics: dict) -> bool:
    boat = row["boat_number"]
    raw_score, _ = head_candidate_score(row, metrics, manshu_head_mode=False)
    if boat == 1:
        unpopular_hold, _ = b1_unpopular_head_signal(row, metrics)
        if unpopular_hold and raw_score >= outer_cut_score + 4.0:
            return True
        if raw_score < outer_cut_score + 10.0:
            return False
        danger = as_num(metrics.get("popular_b1_fly_score")) or 0.0
        loss = as_num(metrics.get("boat1_loss_pct"))
        nige = as_num(metrics.get("boat1_nige_pct"))
        return (
            raw_score >= 42.0
            and danger < 45.0
            and (loss is None or loss < 45.0)
            and (nige is None or nige >= 50.0)
        )
    if raw_score < outer_cut_score + 10.0:
        return False
    if boat == 2:
        avg_diff = as_num(row.get("avg_isshu_diff"))
        exhibit_rank = as_int(row.get("exhibit_rank")) or 9
        ai_plus_rank = as_int(row.get("ai_plus_rank")) or 9
        has_strong_push = (
            bool(row.get("double_time"))
            or bool(row.get("super_slit_alert"))
            or exhibit_rank == 1
            or (avg_diff is not None and avg_diff >= 0.20)
            or ai_plus_rank == 1
        )
        return raw_score >= 30.0 and has_strong_push
    return False


def visible_head_candidates(metrics: dict) -> tuple[list[int], dict[int, dict], str]:
    rows = metrics.get("boats") if isinstance(metrics.get("boats"), list) else []
    outer_scored = []
    inner_scored = []
    details = {}
    for row in rows:
        score, reasons = head_candidate_score(row, metrics, manshu_head_mode=True)
        boat = row["boat_number"]
        details[boat] = {"score": score, "reasons": reasons}
        if boat in {3, 4, 5, 6}:
            outer_scored.append((score, boat))
        else:
            inner_scored.append((score, boat))
    outer_scored.sort(key=lambda item: (-item[0], item[1]))
    inner_scored.sort(key=lambda item: (-item[0], item[1]))
    heads = [boat for _, boat in outer_scored[:2]]
    rule = "万舟は3〜6号艇頭が多いので、3〜6号艇から2艇を優先"
    if len(heads) < 2:
        heads = list(dict.fromkeys(heads + [boat for _, boat in inner_scored]))[:2]
        rule = "3〜6号艇が不足したため、内側も含めて2艇"
    elif inner_scored:
        cut_score = outer_scored[1][0]
        for _, boat in inner_scored:
            row = next((item for item in rows if item["boat_number"] == boat), {})
            if inner_head_exception(row, cut_score, metrics):
                heads = [heads[0], boat]
                details[boat]["reasons"] = (details[boat].get("reasons") or [])[:3] + ["例外的に内側の頭力が高い"]
                rule = "3〜6号艇優先。ただし内側に強い1着根拠があるため例外採用"
                break
    return heads, details, rule


def build_visible_selection(metrics: dict) -> dict:
    rows = metrics.get("boats") if isinstance(metrics.get("boats"), list) else []
    if not rows:
        return {}
    heads, head_details, head_rule = visible_head_candidates(metrics)
    axes, axis_rule = visible_axis_candidates(rows, ranks=(1, 3))
    alt_axes, alt_axis_rule = visible_axis_candidates(rows, ranks=(2, 3))
    if len(heads) < 2 or len(axes) < 2:
        return {}
    return {
        "version": "codex_visible_roles_v1",
        "label": "Codex候補",
        "heads": heads,
        "head_rule": head_rule,
        "head_mode": "manshu_3to6_priority",
        "head_scores": {str(boat): head_details.get(boat, {}) for boat in heads},
        "axes": axes,
        "axis_rule": axis_rule,
        "alt_axes": alt_axes,
        "alt_axis_rule": alt_axis_rule,
    }


def normalize_row(row: dict, rank: int, date_text: str, results_map: dict[tuple[int, int], dict] | None = None) -> dict:
    place_id = race_place_id(row)
    round_no = as_int(row.get("round") if row.get("round") is not None else row.get("round_no"))
    live_result = (results_map or {}).get((place_id, round_no))
    rate = row.get("manshu_rate_pct")
    if rate is None:
        rate = row.get("best_manshu_rate_pct")
    recent = row.get("recent_rate_pct")
    if recent is None:
        recent = row.get("best_recent_rate_pct")
    condition = row.get("condition")
    if condition is None:
        condition = row.get("best_condition")
    metrics = row.get("metrics") or {}
    metric_map = {
        "boat1_ai_prediction_pct": "b1_ai_prediction_pct",
        "boat1_odds_prediction_pct": "b1_odds_prediction_pct",
        "boat1_odds_rank": "b1_odds_rank",
        "odds_snapshot_source": "odds_snapshot_source",
        "b1_trifecta_top5_1head": "b1_trifecta_top5_1head",
        "trifecta_top5_head1_count": "trifecta_top5_head1_count",
        "trifecta_top5_count": "trifecta_top5_count",
        "trifecta_top1_odds": "trifecta_top1_odds",
        "trifecta_top5_avg_odds": "trifecta_top5_avg_odds",
        "trifecta_top5_combos": "trifecta_top5_combos",
        "trifecta_odds_snapshot_at": "trifecta_odds_snapshot_at",
        "boat1_ai_plus": "b1_ai_plus",
        "boat1_ai_plus_order": "b1_ai_plus_order",
        "boat1_nige_pct": "b1_nige_pct",
        "boat1_loss_pct": "b1_loss_pct",
        "is_joshi": "is_joshi",
        "boat1_avg_isshu_diff": "b1_avg_isshu_diff",
        "boat1_isshu_avg_diff": "b1_isshu_avg_diff",
        "avg_isshu_time": "avg_isshu_time",
        "avg_exhibit_combo_time": "avg_exhibit_combo_time",
        "lap_time_type": "lap_time_type",
        "is_summer": "is_summer",
        "b1_summer_isshu_factor": "b1_summer_isshu_factor",
        "b1_summer_nige_delta_pp": "b1_summer_nige_delta_pp",
        "boat1_tenji_time": "b1_tenji_time",
        "boat1_tenji_rank": "b1_tenji_rank",
        "boat1_tenji_time_rank": "b1_tenji_time_rank",
        "boat1_isshu_time": "b1_isshu_time",
        "boat1_isshu_rank": "b1_isshu_rank",
        "outer56_best_avg_isshu_diff": "outer56_best_avg_isshu_diff",
        "outer56_best_ai_prediction_pct": "outer56_best_ai_prediction_pct",
        "outer56_best_ai_plus": "outer56_best_ai_plus",
        "outer56_best_tenji_time": "outer56_best_tenji_time",
        "outer56_best_isshu_time": "outer56_best_isshu_time",
        "outer56_tenji_top2_count": "outer56_tenji_top2_count",
        "outer56_isshu_top2_count": "outer56_isshu_top2_count",
        "outer56_exhibit_top2_count": "outer56_exhibit_top2_count",
        "ai_rank6_boat": "ai_rank6_boat",
        "ai_rank6_avg_isshu_diff": "ai_rank6_avg_isshu_diff",
        "ai_rank6_ai_prediction_pct": "ai_rank6_ai_prediction_pct",
        "ai_rank6_tenji_rank": "ai_rank6_tenji_rank",
        "ai_rank6_isshu_rank": "ai_rank6_isshu_rank",
        "ai_rank5_boat": "ai_rank5_boat",
        "ai_rank5_avg_isshu_diff": "ai_rank5_avg_isshu_diff",
        "ai_rank5_ai_prediction_pct": "ai_rank5_ai_prediction_pct",
        "ai_rank5_tenji_rank": "ai_rank5_tenji_rank",
        "ai_rank5_isshu_rank": "ai_rank5_isshu_rank",
        "low_outer_boat": "low_outer_boat",
        "low_outer_ai_plus_rank": "low_outer_ai_plus_rank",
        "low_outer_avg_isshu_diff": "low_outer_avg_isshu_diff",
        "low_outer_ai_prediction_pct": "low_outer_ai_prediction_pct",
        "low_outer_tenji_rank": "low_outer_tenji_rank",
        "low_outer_isshu_rank": "low_outer_isshu_rank",
        "low_outer_exhibit_top2": "low_outer_exhibit_top2",
        "center_attack_wall_outer": "center_attack_wall_outer",
        "weather_pressure": "weather_pressure",
        "outer_isshu_priority_b1weak": "outer_isshu_priority_b1weak",
        "b1_full_tobashi_shape": "b1_full_tobashi_shape",
        "longshot_head_boats": "longshot_head_boats",
        "longshot_head_candidate_count": "longshot_head_candidate_count",
        "longshot_head_with_b1_gap": "longshot_head_with_b1_gap",
        "double_time_boats": "double_time_boats",
        "super_slit_boats": "super_slit_boats",
        "super_slit_alert_count": "super_slit_alert_count",
        "mid234_super_slit_count": "mid234_super_slit_count",
        "outer456_super_slit_count": "outer456_super_slit_count",
        "outer56_super_slit_count": "outer56_super_slit_count",
        "slit_shape_label": "slit_shape_label",
        "b1_slit_gap_vs_23": "b1_slit_gap_vs_23",
        "b3_slit_adv_vs_12": "b3_slit_adv_vs_12",
        "b4_slit_adv_vs_123": "b4_slit_adv_vs_123",
        "outer56_slit_adv_vs_1": "outer56_slit_adv_vs_1",
        "outer456_slit_adv_vs_123": "outer456_slit_adv_vs_123",
        "slit_dekoboko": "slit_dekoboko",
        "slit_b1_front_wall": "slit_b1_front_wall",
        "slit_b1_hole_vs_23": "slit_b1_hole_vs_23",
        "slit_b2_wall_break_3peek": "slit_b2_wall_break_3peek",
        "slit_b3_peek_vs_12": "slit_b3_peek_vs_12",
        "slit_b4_cadou_peek": "slit_b4_cadou_peek",
        "slit_outer456_pressure": "slit_outer456_pressure",
        "slit_outer56_pressure_vs_1": "slit_outer56_pressure_vs_1",
        "matchup_lane1_pressure_score": "matchup_lane1_pressure_score",
        "matchup_outer_good_count": "matchup_outer_good_count",
        "matchup_lane1_bad_flag": "matchup_lane1_bad_flag",
        "matchup_notes": "matchup_notes",
        "matchup_buff_boats": "matchup_buff_boats",
        "b1_matchup_label": "b1_matchup_label",
        "b2_matchup_label": "b2_matchup_label",
        "b3_matchup_label": "b3_matchup_label",
        "b4_matchup_label": "b4_matchup_label",
        "b5_matchup_label": "b5_matchup_label",
        "b6_matchup_label": "b6_matchup_label",
        "boat1_double_time": "boat1_double_time",
        "mid234_double_time_count": "mid234_double_time_count",
        "outer46_double_time_count": "outer46_double_time_count",
        "outer56_double_time_count": "outer56_double_time_count",
        "wind_speed": "wind_speed",
        "wave_height": "wave_height",
        "tenji_boats": "tenji_boats",
        "raw_isshu_boats": "raw_isshu_boats",
        "hanshu_boats": "hanshu_boats",
        "isshu_boats": "isshu_boats",
    }
    for boat in range(1, 7):
        metric_map.update(
            {
                f"boat{boat}_ai_prediction_pct": f"b{boat}_ai_prediction_pct",
                f"boat{boat}_ai_3ren_pct": f"b{boat}_ai_3ren_pct",
                f"boat{boat}_general_3ren_pct": f"b{boat}_general_3ren_pct",
                f"boat{boat}_st_rank_general": f"b{boat}_st_rank_general",
                f"boat{boat}_st_time_avg_general": f"b{boat}_st_time_avg_general",
                f"boat{boat}_ai_plus": f"b{boat}_ai_plus",
                f"boat{boat}_ai_plus_order": f"b{boat}_ai_plus_order",
                f"boat{boat}_odds_prediction_pct": f"b{boat}_odds_prediction_pct",
                f"boat{boat}_odds_rank": f"b{boat}_odds_rank",
            }
        )
    normalized_metrics = {}
    for out_key, in_key in metric_map.items():
        value = metrics.get(out_key)
        if value is None:
            value = row.get(in_key)
        if out_key in {
            "double_time_boats",
            "super_slit_boats",
            "b1_summer_isshu_factor",
            "slit_shape_label",
            "matchup_notes",
            "matchup_buff_boats",
            "longshot_head_boats",
            "b1_matchup_label",
            "b2_matchup_label",
            "b3_matchup_label",
            "b4_matchup_label",
            "b5_matchup_label",
            "b6_matchup_label",
            "trifecta_top5_combos",
            "trifecta_odds_snapshot_at",
            "odds_snapshot_source",
            "lap_time_type",
        }:
            normalized_metrics[out_key] = value or ""
        else:
            normalized_metrics[out_key] = as_num(value)
    normalized_metrics["odds_boats"] = (
        metrics.get("odds_boats")
        if isinstance(metrics.get("odds_boats"), dict)
        else row.get("odds_boats")
        if isinstance(row.get("odds_boats"), dict)
        else {}
    )
    normalized_metrics["tenji_boats"] = as_int(normalized_metrics["tenji_boats"]) or 0
    normalized_metrics["raw_isshu_boats"] = as_int(normalized_metrics.get("raw_isshu_boats")) or 0
    normalized_metrics["hanshu_boats"] = as_int(normalized_metrics.get("hanshu_boats")) or 0
    normalized_metrics["isshu_boats"] = as_int(normalized_metrics["isshu_boats"]) or 0
    if not normalized_metrics.get("lap_time_type"):
        normalized_metrics["lap_time_type"] = "半周" if row.get("place_name") == "江戸川" else "1周"
    normalized_metrics["composite_edges"] = row.get("composite_edges") or metrics.get("composite_edges") or []
    normalized_metrics["boats"] = build_boat_rows(normalized_metrics, metrics)
    normalized_metrics.update(
        build_popular_b1_fly_logic(normalized_metrics, row.get("composite_edges") or [], round_no)
    )
    status = row.get("status") or "未確定"
    selection = build_visible_selection(normalized_metrics)
    old_selection = row.get("selection") or {}
    if old_selection.get("tickets"):
        selection["tickets"] = old_selection.get("tickets")
        selection["points"] = old_selection.get("points")
    rate_num = as_num(rate) or 0.0
    preview_full = normalized_metrics["tenji_boats"] >= 6 and normalized_metrics["isshu_boats"] >= 6
    alert_type = row.get("last_minute_alert_type")
    buy_decision = row.get("buy_decision")
    final_decision_checks = list(row.get("final_decision_checks") or [])
    subcore_strategy_ids = set(row.get("last_minute_subcore_strategy_ids") or [])
    has_subcore_buy = "codex_post_subcore_rate38_conditions" in subcore_strategy_ids
    if alert_type in {"buy_ok", "late_riser_buy_ok"}:
        buy_decision = "本命"
        final_decision_checks.append(f"展示後40%以上:OK({rate_num:.2f}%)")
    elif alert_type in {"subcore_watch", "late_riser_subcore_watch"} and has_subcore_buy:
        buy_decision = "準本命"
    elif preview_full and rate_num >= 40.0:
        buy_decision = "本命"
        final_decision_checks.append(f"展示後40%以上:OK({rate_num:.2f}%)")
    elif preview_full and 38.0 <= rate_num < 40.0 and has_subcore_buy:
        buy_decision = "準本命"
        final_decision_checks.append(f"展示後38〜39.9%:OK({rate_num:.2f}%)")
        final_decision_checks.append("準本命条件:OK")
    elif preview_full and 38.0 <= rate_num < 40.0:
        buy_decision = "見送り"
        final_decision_checks.append(f"展示後38〜39.9%:OK({rate_num:.2f}%)")
        final_decision_checks.append("準本命条件不足: 1号艇危険・外頭2艇(5/6含む)・内軸残り・12点生成まで揃わず")
    elif not preview_full:
        buy_decision = "展示待ち"
    else:
        buy_decision = "見送り"
        if preview_full:
            final_decision_checks.append(f"展示後38%未満:NG({rate_num:.2f}%)")
            final_decision_checks.append("本命40%以上:NG")
    if (
        row.get("ranking_type") != "morning_watchlist"
        and normalized_metrics["tenji_boats"] >= 6
        and normalized_metrics["isshu_boats"] >= 6
    ):
        if "展示待ち" in str(status):
            status = str(status).replace("・展示待ち", "").replace("展示待ち", "展示込み")
        elif "展示込み" not in str(status):
            status = f"{status}・展示込み"
    elif (
        row.get("ranking_type") != "morning_watchlist"
        and normalized_metrics["tenji_boats"] >= 6
        and normalized_metrics["isshu_boats"] < 6
        and "未取得" not in str(status)
    ):
        missing_lap = "半周未取得" if normalized_metrics.get("lap_time_type") == "半周" else "一周未取得"
        status = f"{status}・{missing_lap}"
    normalized = {
        "rank": rank,
        "status": status,
        "date": row.get("date") or date_text,
        "race_id": row.get("race_id"),
        "place_id": place_id,
        "place_name": row.get("place_name"),
        "round": round_no,
        "deadline_time": row.get("deadline_time"),
        "race_grade": row.get("race_grade"),
        "race_kind": row.get("race_kind"),
        "series_title": row.get("series_title"),
        "ranking_type": row.get("ranking_type"),
        "manshu_rate_pct": as_num(rate),
        "base_manshu_rate_pct": as_num(row.get("base_manshu_rate_pct")),
        "composite_edge_base_rate_pct": as_num(row.get("composite_edge_base_rate_pct")),
        "composite_edge_bonus_pct": as_num(row.get("composite_edge_bonus_pct")),
        "composite_edges": row.get("composite_edges") or [],
        "recent_rate_pct": as_num(recent),
        "condition": condition,
        "matched_logic_count": as_int(row.get("matched_logic_count")) or 0,
        "metrics": normalized_metrics,
        "selection": selection,
        "last_minute_checked_at": row.get("last_minute_checked_at"),
        "last_minute_alert_type": row.get("last_minute_alert_type"),
        "last_minute_checks": row.get("last_minute_checks") or [],
        "last_minute_strategy_ids": row.get("last_minute_strategy_ids") or [],
        "last_minute_subcore_strategy_ids": row.get("last_minute_subcore_strategy_ids") or [],
        "buy_decision": buy_decision,
        "final_decision_checks": list(dict.fromkeys(final_decision_checks)),
        "result": normalize_result(row, live_result),
    }
    if any(row.get(key) is not None for key in ("candidate_type", "candidate_phase", "candidate_score", "finalize_rule")) or row.get("candidate_reasons"):
        normalized.update(
            {
                "candidate_type": row.get("candidate_type"),
                "candidate_phase": row.get("candidate_phase"),
                "candidate_source_scope": row.get("candidate_source_scope"),
                "candidate_score": as_num(row.get("candidate_score")),
                "candidate_material_count": as_int(row.get("candidate_material_count")) or 0,
                "candidate_material_score": as_num(row.get("candidate_material_score")),
                "pre_exhibition_manshu_score": as_num(row.get("pre_exhibition_manshu_score")),
                "pre_exhibition_manshu_rate_pct": as_num(row.get("pre_exhibition_manshu_rate_pct")),
                "pre_exhibition_v1_probability_pct": as_num(row.get("pre_exhibition_v1_probability_pct")),
                "pre_exhibition_v2_probability_pct": as_num(row.get("pre_exhibition_v2_probability_pct")),
                "pre_exhibition_v2_rank_score": as_num(row.get("pre_exhibition_v2_rank_score")),
                "pre_exhibition_v2_score_bin": row.get("pre_exhibition_v2_score_bin"),
                "pre_exhibition_v2_venue_adjust_pp": as_num(row.get("pre_exhibition_v2_venue_adjust_pp")),
                "pre_exhibition_v2_material_adjust_pp": as_num(row.get("pre_exhibition_v2_material_adjust_pp")),
                "pre_exhibition_v2_global_rate_pct": as_num(row.get("pre_exhibition_v2_global_rate_pct")),
                "pre_exhibition_logic_version": row.get("pre_exhibition_logic_version"),
                "pre_exhibition_diversified": as_int(row.get("pre_exhibition_diversified")) or 0,
                "pre_exhibition_venue_diversity_rule": row.get("pre_exhibition_venue_diversity_rule"),
                "pre_exhibition_venue_count_before_pick": as_int(row.get("pre_exhibition_venue_count_before_pick")) or 0,
                "pre_exhibition_venue_extra_allowed": as_int(row.get("pre_exhibition_venue_extra_allowed")) or 0,
                "pre_exhibition_skipped_same_venue_count": as_int(row.get("pre_exhibition_skipped_same_venue_count")) or 0,
                "pre_exhibition_logic": row.get("pre_exhibition_logic"),
                "candidate_reasons": row.get("candidate_reasons") or [],
                "finalize_rule": row.get("finalize_rule"),
            }
        )
    return normalized


def build_payload(source: dict, top_n: int, results_map: dict[tuple[int, int], dict] | None = None) -> dict:
    date_text = source["date"]
    strict_source_rows = []
    unified_source_rows = source.get("unified_rank_top")
    if isinstance(unified_source_rows, list) and unified_source_rows:
        rows = list(unified_source_rows)
        strict_source_rows = list(unified_source_rows)
    elif isinstance(source.get("all_venue_rank_top"), list):
        rows = list(source.get("all_venue_rank_top") or [])
        strict_source_rows = list(source.get("strict_rank_top") or [])
        if not strict_source_rows:
            strict_source_rows = list(source.get("actual_rank_top") or []) + list(source.get("watch_rank_top") or [])
    elif isinstance(source.get("races"), list):
        rows = list(source.get("races") or [])
        strict_source_rows = list(source.get("strict_races") or [])
    else:
        rows = list(source.get("actual_rank_top") or []) + list(source.get("watch_rank_top") or [])
    rows = unique_rows(rows, top_n)
    strict_rows = unique_rows(strict_source_rows, top_n) if strict_source_rows else []
    morning_source_rows = (
        source.get("morning_candidate_top")
        if isinstance(source.get("morning_candidate_top"), list)
        else source.get("morning_candidates")
        if isinstance(source.get("morning_candidates"), list)
        else []
    )
    morning_rows = unique_rows(list(morning_source_rows or []), top_n)
    races = [normalize_row(row, idx + 1, date_text, results_map) for idx, row in enumerate(rows)]
    strict_races = [normalize_row(row, idx + 1, date_text, results_map) for idx, row in enumerate(strict_rows)]
    morning_candidates = [normalize_row(row, idx + 1, date_text, results_map) for idx, row in enumerate(morning_rows)]
    settled = [race for race in races if race["result"].get("payout_yen") is not None]
    manshu_hits = [race for race in settled if race["result"].get("manshu")]
    strict_settled = [race for race in strict_races if race["result"].get("payout_yen") is not None]
    strict_manshu_hits = [race for race in strict_settled if race["result"].get("manshu")]
    all_races = as_int(source.get("_all_races_count"))
    if all_races is None:
        source_summary = source.get("summary") or {}
        all_races = as_int(source_summary.get("all_races"))
    if all_races is None:
        all_races = len(source.get("races") or []) if isinstance(source.get("races"), list) else as_int(source.get("races")) or 0
    source_summary = source.get("summary") or {}
    with_tenji = source.get("races_with_full_tenji")
    if with_tenji is None:
        with_tenji = source_summary.get("races_with_full_tenji")
    with_isshu = source.get("races_with_full_isshu")
    if with_isshu is None:
        with_isshu = source_summary.get("races_with_full_isshu")
    with_raw_isshu = source.get("races_with_full_raw_isshu")
    if with_raw_isshu is None:
        with_raw_isshu = source_summary.get("races_with_full_raw_isshu")
    with_hanshu = source.get("races_with_full_hanshu")
    if with_hanshu is None:
        with_hanshu = source_summary.get("races_with_full_hanshu")
    return {
        "version": "boaters-manshu-logic-v2",
        "date": date_text,
        "generated_at": iso_now(),
        "threshold_pct": as_num(source.get("threshold_pct")) or 27.0,
        "logic_label": source.get("logic_label") or "Codex BOATERS展示込み 万舟率ロジック",
        "logic_summary": source.get("logic_summary")
        or "BOATERS DBのAI3連対率・一般3連対率、1号艇の逃げ/差され/まくられ傾向、オリジナル展示の展示タイム・有効ラップ（通常は1周、江戸川は半周）を組み合わせたCodex側ランキング。",
        "source": {
            "ranking_json": str(source.get("outputs", {}).get("json") or ""),
            "database": "/Users/ohyabumasaya/Desktop/price_action_analysis/outputs/boaters_all_races.sqlite",
        },
        "summary": {
            "all_races": all_races,
            "races_with_full_tenji": as_int(with_tenji) or 0,
            "races_with_full_raw_isshu": as_int(with_raw_isshu) or 0,
            "races_with_full_hanshu": as_int(with_hanshu) or 0,
            "races_with_full_isshu": as_int(with_isshu) or 0,
            "displayed_top_n": len(races),
            "strict_displayed_top_n": len(strict_races),
            "settled_top_n": len(settled),
            "manshu_hits_top_n": len(manshu_hits),
            "actual_manshu_rate_top_n_pct": round(len(manshu_hits) / len(settled) * 100, 2) if settled else None,
            "strict_settled_top_n": len(strict_settled),
            "strict_manshu_hits_top_n": len(strict_manshu_hits),
            "strict_actual_manshu_rate_top_n_pct": round(len(strict_manshu_hits) / len(strict_settled) * 100, 2) if strict_settled else None,
            "morning_candidate_count": len(morning_candidates),
            "baseline_only_hidden_count": as_int(source.get("baseline_only_hidden_count")) or 0,
        },
        "races": races,
        "strict_races": strict_races,
        "morning_candidates": morning_candidates,
    }


def fill_source_from_csv(source: dict, csv_rows: list[dict]) -> None:
    if not csv_rows:
        return
    source["_all_races_count"] = source.get("races")
    all_rows = [
        row
        for row in csv_rows
        if str(row.get("ranking_type") or "").strip() == "all_venue"
        or str(row.get("status") or "").strip() == "全場スコア"
    ]
    strict_rows = [
        row
        for row in csv_rows
        if str(row.get("ranking_type") or "").strip() == "strict"
        or str(row.get("status") or "").strip() in {"確定", "展示待ち"}
    ]
    if not source.get("all_venue_rank_top") and all_rows:
        source["all_venue_rank_top"] = all_rows
    if not source.get("strict_rank_top") and strict_rows:
        source["strict_rank_top"] = strict_rows
    if not source.get("unified_rank_top") and strict_rows:
        source["unified_rank_top"] = strict_rows
    if (not isinstance(source.get("races"), list) or not source.get("races")) and all_rows:
        source["races"] = all_rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-json", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--source-csv", help="Optional full daily CSV ranking. Used to fill TOP10 before falling back to JSON top lists.")
    parser.add_argument("--results-json", help="Optional BoatraceOpenAPI results/v2 JSON to embed static trifecta payouts.")
    args = parser.parse_args()
    source_path = Path(args.source_json)
    out_path = Path(args.out)
    source = json.loads(source_path.read_text(encoding="utf-8"))
    csv_rows = load_csv_rows(args.source_csv)
    fill_source_from_csv(source, csv_rows)
    payload = build_payload(source, args.top_n, load_results_map(args.results_json))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not payload.get("races") and not payload.get("strict_races") and out_path.exists():
        existing = json.loads(out_path.read_text(encoding="utf-8"))
        if existing.get("races") or existing.get("strict_races"):
            print(
                json.dumps(
                    {
                        "out": str(out_path),
                        "date": payload["date"],
                        "kept_existing": True,
                        "reason": "new payload has no ranking rows",
                    },
                    ensure_ascii=False,
                )
            )
            return 0
    payload_text = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
    out_path.write_text(payload_text, encoding="utf-8")
    if out_path.name.startswith("boaters_manshu_ranking_") and not out_path.name.startswith("boaters_manshu_ranking_codex_"):
        codex_name = out_path.name.replace("boaters_manshu_ranking_", "boaters_manshu_ranking_codex_", 1)
        out_path.with_name(codex_name).write_text(payload_text, encoding="utf-8")
    print(json.dumps({"out": str(out_path), "date": payload["date"], "races": len(payload["races"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
