#!/usr/bin/env python3
"""Monitor Codex BOATERS morning watchlist races and emit deadline alerts.

The betting flow is intentionally two-step:

1. Freeze a morning TOP list using only pre-exhibition data.
2. Near deadline, fetch BOATERS AI/exhibition/odds and alert only when the
   same morning-watch race still clears the post-exhibition threshold.
"""

from __future__ import annotations

import argparse
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
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
PRICE_DIR = ROOT.parent / "price_action_analysis"
PRICE_OUT = PRICE_DIR / "outputs"
PUBLIC_OUT = ROOT / "data" / "output"
PUSH_CONFIG = PUBLIC_OUT / "boaters_push_config.local.json"
WORK_OUT = PRICE_OUT if PRICE_DIR.exists() else PUBLIC_OUT
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
JST = ZoneInfo("Asia/Tokyo")
SUMMER_MONTHS = {6, 7, 8}
SUMMER_B1_FAST_DIFF = 0.10
SUMMER_B1_SLOW_DIFF = -0.10
SUMMER_B1_FAST_NIGE_DELTA_PP = 15
SUMMER_B1_SLOW_NIGE_DELTA_PP = -17
SUPER_SLIT_TENJI_ADV = 0.10
VALIDATED_BUY_STRATEGY_IDS = {"codex_post_core_ab_rank3"}
SUBCORE_WATCH_STRATEGY_IDS = {
    "codex_post_subcore_rank6_outer_exhibit_top2",
    "codex_popular_b1_exhibition_fly_watch",
}

SUPER_SLIT_ALERT_STATS = {
    2: {"win_rate_pct": 29.56, "top3_rate_pct": 70.91, "makuri_win_rate_pct": 11.53, "score_bonus": 11},
    3: {"win_rate_pct": 22.45, "top3_rate_pct": 66.55, "makuri_win_rate_pct": 10.76, "score_bonus": 10},
    4: {"win_rate_pct": 21.63, "top3_rate_pct": 61.09, "makuri_win_rate_pct": 12.94, "score_bonus": 12},
    5: {"win_rate_pct": 12.68, "top3_rate_pct": 49.43, "makuri_win_rate_pct": 5.45, "score_bonus": 11},
    6: {"win_rate_pct": 8.90, "top3_rate_pct": 40.69, "makuri_win_rate_pct": 4.16, "score_bonus": 10},
}

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


def state_path(date_text):
    return PUBLIC_OUT / f"boaters_manshu_alert_state_{date_text.replace('-', '')}.json"


def alerts_path(date_text):
    return PUBLIC_OUT / f"boaters_manshu_alerts_{date_text.replace('-', '')}.json"


def ranking_rows(payload, top_n):
    rows = payload.get("strict_races") or payload.get("races") or []
    return list(rows)[:top_n]


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
    return int(as_num(metrics.get("tenji_boats")) or 0) >= 6 and int(as_num(metrics.get("isshu_boats")) or 0) >= 6


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


def merge_live_metrics_into_public_ranking(date_text, updates, now):
    if not updates:
        return False
    changed_any = False
    for path in (public_ranking_path(date_text), public_codex_ranking_path(date_text)):
        changed_any = merge_live_metrics_into_ranking_path(path, updates, now) or changed_any
    return changed_any


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
                race["last_minute_manshu_rate_pct"] = update.get("last_minute_manshu_rate_pct")
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
    return config


def ntfy_url(config):
    topic = str(config.get("ntfy_topic") or "").strip()
    if not topic:
        return None
    if topic.startswith("http://") or topic.startswith("https://"):
        return topic
    server = str(config.get("ntfy_server") or "https://ntfy.sh").rstrip("/")
    return f"{server}/{topic}"


def send_ntfy(config, title, message, tags="rotating_light", priority=None):
    url = ntfy_url(config)
    if not url:
        return {"enabled": False, "reason": "ntfy_topic not configured"}
    headers = {
        "Title": title,
        "Tags": tags,
        "Priority": str(priority or config.get("ntfy_priority") or "high"),
    }
    token = config.get("ntfy_token")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = message.encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            return {"enabled": True, "ok": 200 <= response.status < 300, "status": response.status}
    except urllib.error.HTTPError as exc:
        return {"enabled": True, "ok": False, "status": exc.code, "error": str(exc)}
    except Exception as exc:
        return {"enabled": True, "ok": False, "error": str(exc)}


def push_notifications(payload, state, now):
    config = load_push_config()
    if not ntfy_url(config):
        return {"enabled": False, "sent": 0, "errors": []}

    pushed = state.setdefault("pushed", {})
    results = []
    errors = []

    for alert in payload.get("alerts") or []:
        key = f"alert:{alert.get('race_id')}:{alert.get('alert_type')}"
        if pushed.get(key):
            continue
        if alert.get("alert_type") == "late_riser_buy_ok":
            title = "BOATERS急浮上買い候補"
        elif alert.get("alert_type") in {"subcore_watch", "late_riser_subcore_watch"}:
            title = "BOATERS準本命候補"
        elif alert.get("alert_type") == "late_riser":
            title = "BOATERS急浮上"
        elif alert.get("alert_type") == "buy_ok":
            title = "BOATERS買い候補"
        else:
            title = "BOATERS万舟率上昇"
        result = send_ntfy(config, title, alert.get("message") or "", tags="moneybag,boat")
        results.append({"key": key, **result})
        if result.get("ok"):
            pushed[key] = now.isoformat(timespec="seconds")
        elif result.get("enabled"):
            errors.append({"key": key, "error": result.get("error"), "status": result.get("status")})

    for item in payload.get("inspected") or []:
        if item.get("status") != "fetch_failed":
            continue
        key = f"fetch_failed:{item.get('race_id')}"
        if pushed.get(key):
            continue
        race_text = f"{item.get('place_name')}{item.get('round')}R"
        message = (
            f"{race_text}のBOATERS直前データ取得に失敗しました。\n"
            f"締切まで約{item.get('minutes_to_deadline')}分\n"
            f"error: {item.get('error')}"
        )
        result = send_ntfy(config, "BOATERS取得失敗", message, tags="warning,boat", priority="urgent")
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
    if row.get("longshot_head_candidate"):
        reasons.append("穴頭候補に一致")
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

        win_score = math.log(max(ai_pred if ai_pred is not None else 16.67, 0.1))
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
    if sum(1 for row in rows if row.get("ai_3ren_pct") is not None) >= max(ranks):
        return rank_boats_for_key(rows, "ai_3ren_pct", ranks), f"AI3連対率の{rank_label}"
    if sum(1 for row in rows if row.get("ai_plus") is not None) >= max(ranks):
        return rank_boats_for_key(rows, "ai_plus", ranks), f"AI3連対率が不足したためAI+一般3連対の{rank_label}"
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
    odds_rank = int(as_num(metrics.get("boat1_odds_rank")) or 0) or None
    odds_pct = as_num(metrics.get("boat1_odds_prediction_pct"))
    has_popularity_data = top5_count >= 5 or odds_rank is not None or odds_pct is not None
    if not has_popularity_data:
        return False, ""
    top5_almost = top5_count >= 5 and top5_head_count >= 4
    odds_heavy = odds_rank == 1 and odds_pct is not None and odds_pct >= 40
    is_unpopular = (not trifecta_top5) and (not top5_almost) and (not odds_heavy)
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
    elif top5_count >= 5:
        popularity_text = f"人気上位5点中1号艇頭{top5_head_count}点"
    return True, f"{popularity_text}で売れすぎではないが逃げ材料が強い"


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
    if row.get("double_time"):
        score += 7
        reasons.append("ダブルタイム")
    if row.get("super_slit_alert"):
        score += 7 if boat in {2, 3} else 9
        reasons.append("スーパースリット")
    if row.get("low_outer_revive"):
        score += 5
        reasons.append("低評価外枠の展示復活")
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
    return round(score, 3), reasons[:4]


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
    if row.get("exhibit_rank", 9) <= 2:
        reasons.append("展示か1周が2位以内")
    if (row.get("avg_isshu_diff") or -9) >= 0.10:
        reasons.append("展示+1周平均との差が良い")
    if str(row.get("matchup_label") or "") in {"1号艇キラー", "相性バフ", "相性軸バフ"}:
        reasons.append(row.get("matchup_label"))
    return reasons


def select_keshi_boat(rows, protected=None):
    protected = set(protected or [])
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


def ticket_priority(ticket, heads, axes):
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
    if boats[1] == 1 and boats[2] == 2:
        score -= 2
    return score


def trim_tickets(tickets, heads, axes, max_points=15):
    if len(tickets) <= max_points:
        return tickets
    ordered = sorted(tickets, key=lambda ticket: (-ticket_priority(ticket, heads, axes), ticket))
    return set(ordered[:max_points])


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
    tickets = trim_tickets(tickets, heads, axes)
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


def combo_boats(value):
    combo = norm_combo(value)
    return [int(ch) for ch in combo] if len(combo) == 3 else []


def axis_hit(axes, trifecta):
    boats = set(combo_boats(trifecta))
    return bool(boats & set(axes or [])) if boats else None


def selection_payload(rows, race=None, strategies=None):
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
        "odds_filter": "3連単50倍未満は買わない",
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


def enrich_rows(by_boat, morning_metrics, date_text=None):
    rows = []
    for boat in range(1, 7):
        source = by_boat.get(boat, {})
        ai_3ren = as_num(source.get("ai_3ren_pct"))
        general = as_num(source.get("general_3ren_pct"))
        row = {
            "boat_number": boat,
            "_morning_metrics": morning_metrics,
            "ai_3ren_pct": ai_3ren,
            "general_3ren_pct": general,
            "st_rank_general": as_num(source.get("st_rank_general")),
            "ai_prediction_pct": as_num(source.get("ai_prediction_pct")),
            "odds_prediction_pct": as_num(source.get("odds_prediction_pct")),
            "tenji_time": as_num(source.get("tenji_time")),
            "isshu_time": as_num(source.get("isshu_time")),
            "nige_pct": as_num(source.get("nige_pct")),
            "sasare_pct": as_num(source.get("sasare_pct")),
            "makurare_pct": as_num(source.get("makurare_pct")),
        }
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
                row["super_slit_score_bonus"] = SUPER_SLIT_ALERT_STATS[boat]["score_bonus"]

    for row in rows:
        row["tenji_rank"] = row["tenji_time_rank"]
        row["isshu_rank"] = row["isshu_time_rank"]
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
        super_slit_score = 0
        if row["super_slit_alert"]:
            if row["boat_number"] in {2, 3}:
                super_slit_score = 0.80
            elif row["boat_number"] in {4, 5}:
                super_slit_score = 0.95
            elif row["boat_number"] == 6:
                super_slit_score = 0.75
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
            - row["low_outer_score_bonus"]
            - row["longshot_head_score_bonus"]
        )
        row["value_score"] = (
            row["comp_score"]
            - (0.45 if row["boat_number"] in {4, 5, 6} else 0)
            - (0.70 if row["outer_good"] else 0)
            - (0.30 if row["double_time"] and row["boat_number"] in {5, 6} else 0)
            - (0.35 if row["super_slit_alert"] and row["boat_number"] in {4, 5, 6} else 0)
            - (0.35 if row["matchup_label"] in {"1号艇キラー", "相性バフ"} else 0)
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


def race_metrics(rows, date_text=None, round_no=None):
    morning_metrics = rows[0].get("_morning_metrics") or {}
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
    isshu_boats = sum(1 for row in rows if row.get("isshu_time") is not None)
    summer_factor = summer_b1_isshu_factor(date_text, b1.get("isshu_avg_diff"), isshu_boats)
    slit_metrics = slit_rank_metrics(rows)
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
        if boat1_odds_rank_int == 1 and boat1_odds_pct >= 40:
            popular_score = 35 + max(0, boat1_odds_pct - 40) * 1.2
            popular_reasons = [f"展示後オッズ評価で1号艇が1位{boat1_odds_pct:.1f}%"]
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
            outer56_exhibit_top2_count = sum(1 for row in outer if row.get("exhibit_rank", 9) <= 2)
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
            popular_score = round(bounded(popular_score, 0, 100), 1)
            if popular_score >= 75:
                popular_level = "超危険"
            elif popular_score >= 60:
                popular_level = "危険"
            elif popular_score >= 45:
                popular_level = "注意"
            else:
                popular_level = "人気だが鉄板寄り"
            if matched_conditions:
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
                    "popular_b1_fly_score": popular_score,
                    "popular_b1_fly_level": popular_level,
                    "popular_b1_not_win_rate_pct": round(not_win_rate, 2) if not_win_rate is not None else None,
                    "popular_b1_top3_miss_rate_pct": round(top3_miss_rate, 2) if top3_miss_rate is not None else None,
                    "popular_b1_manshu_rate_pct": round(manshu_rate, 2) if manshu_rate is not None else None,
                    "popular_b1_rate_source": rate_source,
                    "popular_b1_reasons": popular_reasons[:7],
                    "popular_b1_matched_conditions": matched_conditions[:3],
                }
            )
        else:
            live_odds_context.update(
                {
                    "popular_b1_is_popular": False,
                    "popular_b1_source": "展示後BOATERSオッズ評価",
                    "popular_b1_fly_score": 0,
                    "popular_b1_fly_level": "人気不足",
                    "popular_b1_not_win_rate_pct": None,
                    "popular_b1_top3_miss_rate_pct": None,
                    "popular_b1_manshu_rate_pct": None,
                    "popular_b1_rate_source": "展示後オッズ評価で人気不足",
                    "popular_b1_reasons": [f"展示後オッズ評価{fmt_pct(boat1_odds_pct)}({boat1_odds_rank_int}位)で1号艇が売れすぎではない"],
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
                "avg_isshu_diff": row.get("avg_isshu_diff"),
                "super_slit_alert": bool(row.get("super_slit_alert")),
                "super_slit_tenji_adv": row.get("super_slit_tenji_adv"),
                "super_slit_st_rank_adv": row.get("super_slit_st_rank_adv"),
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
        "boat1_ai_plus": b1.get("ai_plus"),
        "boat1_ai_plus_order": b1.get("ai_plus_rank"),
        "boat1_nige_pct": b1.get("nige_pct"),
        "boat1_loss_pct": b1_loss,
        "boat1_avg_isshu_diff": b1.get("avg_isshu_diff"),
        "boat1_isshu_avg_diff": b1.get("isshu_avg_diff"),
        "avg_isshu_time": b1.get("avg_isshu_time"),
        "avg_exhibit_combo_time": b1.get("avg_exhibit_combo_time"),
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
        "outer56_exhibit_top2_count": sum(1 for row in outer if row.get("exhibit_rank", 9) <= 2),
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


def roi_strategies(race, metrics, rows):
    place = race.get("place_name")
    round_no = int(race.get("round") or 0)
    rank_no = int(race.get("rank") or race.get("morning_rank") or race.get("live_rank") or 99)
    b1_bad = (
        (metrics.get("boat1_tenji_rank", 9) >= 4)
        or (metrics.get("boat1_tenji_time_rank", 9) >= 4)
        or (metrics.get("boat1_isshu_rank", 9) >= 4)
    )
    strategies = []
    wind_wave = (weather_value(race, "wind_speed") or 0) >= 5 or (weather_value(race, "wave_height") or 0) >= 5
    b1_summer_fast = (metrics.get("b1_summer_isshu_factor") or metrics.get("boat1_summer_isshu_factor")) == "fast_hold"
    full_exhibition = metrics.get("tenji_boats", 0) >= 6 and metrics.get("isshu_boats", 0) >= 6
    outer56_ai_pred = metrics.get("outer56_best_ai_prediction_pct") or -1
    outer56_ai_plus = metrics.get("outer56_best_ai_plus") or -1
    outer56_avgdiff = metrics.get("outer56_best_avg_isshu_diff") or -9
    b1_ai_pred = metrics.get("boat1_ai_prediction_pct") or 999
    b1_avgdiff = metrics.get("boat1_avg_isshu_diff") if metrics.get("boat1_avg_isshu_diff") is not None else 9
    rank6_boat = int(metrics.get("ai_rank6_boat") or 0)
    rank6_ai_pred = metrics.get("ai_rank6_ai_prediction_pct") or -1
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
                "Codex直前本命: 朝TOP3+1AI30未満+外上昇A/B 10〜15点",
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
                "Codex準本命B: AI+最下位5/6が展示浮上 監視",
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
                "Codex準本命C: 人気1号艇が展示で危険 監視",
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
                "Codex本命型: TOP7 10〜12R 宮島除外 外頭2艇+外消し AI3軸 10〜15点",
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
        and metrics.get("boat1_tenji_time_rank", 9) >= 4
        and metrics.get("boat1_isshu_rank", 9) >= 4
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

    out = []
    for strategy_id, label, _ticket_func in strategies:
        tickets, roles = super_arunashi3(rows)
        if not tickets or roles is None:
            continue
        out.append(
            {
                "strategy_id": strategy_id,
                "label": label,
                "points": len(tickets),
                "heads": roles["heads"],
                "axes": roles["axes"],
                "alt_axes": roles.get("alt_axes", []),
                "axis_rule": "AI3連対率+一般3連対率の1位と3位",
                "alt_axis_rule": "比較用: AI3連対率+一般3連対率の2位と3位",
                "supports": roles.get("supports", []),
                "keshi": roles["keshi"],
                "keshi_reason": roles.get("keshi_reason"),
                "ai_plus_rank6_boat": roles.get("ai_plus_rank6_boat"),
                "ai_plus_rank6_revival": roles.get("ai_plus_rank6_revival", []),
                "role_note": roles["role_note"],
                "tickets": [fmt_ticket(ticket) for ticket in sorted(tickets)],
                "odds_filter": "3連単50倍未満は買わない",
            }
        )
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
    return f", 1オッズ評価{fmt_pct(pct)}({rank_text})"


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


def make_message(race, alert_type, metrics, checks, strategies):
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
        f"{fmt_longshot_head(metrics)}"
        f"{fmt_double_time(metrics)}"
        f"{fmt_super_slit(metrics)}"
        f"{fmt_summer_b1_isshu(metrics)}"
        f"{fmt_slit_shape(metrics)}"
        f"{fmt_matchup(metrics)}"
    )
    if alert_type in {"buy_ok", "late_riser_buy_ok", "subcore_watch", "late_riser_subcore_watch"} and strategies:
        s = strategies[0]
        support_text = f" / 相手: {fmt_list(s.get('supports'))}" if s.get("supports") else ""
        if s.get("strategy_id") == "codex_post_core_ab_rank3":
            title = "【本命買い候補】"
        elif s.get("strategy_id") in SUBCORE_WATCH_STRATEGY_IDS:
            title = "【準本命候補】"
        else:
            title = "【急浮上 買い候補】" if alert_type == "late_riser_buy_ok" else "【買い候補】"
        return (
            f"{title}{base}\n"
            f"{metric_text}\n"
            f"直前条件: {' / '.join(checks)}\n"
            f"買い方: {s['label']} / {s['points']}点 / {s['odds_filter']}\n"
            f"頭候補: {fmt_list(s['heads'])} / 軸: {fmt_list(s['axes'])}"
            f"({s.get('axis_rule','AI+1位3位')}) / 比較軸: {fmt_list(s.get('alt_axes'))}"
            f"{support_text} / 消し: {fmt_role(s['keshi'])}\n"
            f"消し理由: {s.get('keshi_reason') or '-'}\n"
            f"買い目: {' '.join(s['tickets'])}"
        )
    if alert_type == "late_riser":
        return (
            f"【急浮上】{base}\n"
            f"{metric_text}\n"
            f"直前条件: {' / '.join(checks)}"
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
    races = ranking_rows(ranking, args.top_n)
    morning_ids = {race.get("race_id") for race in races}
    state = load_json(state_path(date_text), {"sent": {}})
    sent = state.setdefault("sent", {})

    inspected = []
    alerts = []
    live_path = None
    live_rows = []
    live_by_id = {}
    if args.scan_risers and not args.offline:
        try:
            live_path = build_live_ranking(date_text, top_n=args.live_top_n, threshold=args.threshold)
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
        backfill_after_close = (
            missing_exhibition
            and minutes_to_deadline < -args.grace_minutes
            and abs(minutes_to_deadline) <= backfill_limit_minutes
        )
        if minutes_to_deadline > args.lookahead_minutes or minutes_to_deadline < -args.grace_minutes:
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
                return {"minutes_to_deadline": minutes_to_deadline, "backfill_only": True}
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
        return {"minutes_to_deadline": minutes_to_deadline, "backfill_only": False}

    def inspect_race(race, source_type, morning_rank=None, live_rank=None):
        window = inspect_window(race, source_type)
        if window is None:
            return
        minutes_to_deadline = window["minutes_to_deadline"]
        backfill_only = bool(window.get("backfill_only"))
        try:
            by_boat = fetch_live_race(race, refresh=not args.no_refresh)
            rows = enrich_rows(by_boat, race.get("metrics") or {}, date_text=race.get("date"))
            metrics = race_metrics(rows, date_text=race.get("date"), round_no=race.get("round"))
            confirmed, checks = condition_confirmed(race.get("condition"), metrics)
            all_strategies = roi_strategies(race, metrics, rows)
            buy_strategies = [
                strategy
                for strategy in all_strategies
                if strategy.get("strategy_id") in VALIDATED_BUY_STRATEGY_IDS
            ]
            subcore_strategies = [
                strategy
                for strategy in all_strategies
                if strategy.get("strategy_id") in SUBCORE_WATCH_STRATEGY_IDS
            ]
            selection_strategies = buy_strategies or subcore_strategies
            selection = selection_payload(rows, race=race, strategies=selection_strategies)
            preview_ready = has_full_exhibition(metrics)
            post_rate = as_num(race.get("manshu_rate_pct")) or 0
            alert_rate_ready = post_rate >= args.alert_threshold
            can_send_alert = preview_ready and alert_rate_ready
            if backfill_only:
                alert_type = None
            elif source_type == "morning_top" and can_send_alert:
                if buy_strategies:
                    alert_type = "buy_ok"
                elif subcore_strategies:
                    alert_type = "subcore_watch"
                else:
                    alert_type = None
            elif source_type != "morning_top" and not args.notify_risers:
                alert_type = None
            elif not can_send_alert:
                alert_type = None
            elif buy_strategies:
                alert_type = "late_riser_buy_ok"
            elif subcore_strategies:
                alert_type = "late_riser_subcore_watch"
            elif confirmed or (race.get("manshu_rate_pct") or 0) >= args.riser_threshold:
                alert_type = "late_riser"
            else:
                alert_type = None

            strategy_ids = [s["strategy_id"] for s in buy_strategies]
            subcore_strategy_ids = [s["strategy_id"] for s in subcore_strategies]
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
            }
            inspected.append(
                {
                    "race_id": race.get("race_id"),
                    "place_name": race.get("place_name"),
                    "round": race.get("round"),
                    "source": source_type,
                    "status": "backfilled_missing_exhibition" if backfill_only else "checked",
                    "minutes_to_deadline": round(minutes_to_deadline, 1),
                    "condition_confirmed": confirmed,
                    "checks": checks,
                    "strategies": strategy_ids,
                    "subcore_strategies": subcore_strategy_ids,
                    "candidate_strategy_ids": [s["strategy_id"] for s in all_strategies],
                    "preview_ready": preview_ready,
                    "alert_rate_ready": alert_rate_ready,
                    "alert_threshold_pct": args.alert_threshold,
                    "morning_manshu_rate_pct": race.get("morning_manshu_rate_pct"),
                    "post_exhibition_manshu_rate_pct": post_rate,
                    "rate_source": race.get("rate_source"),
                    "selection": selection,
                    "metrics": metrics,
                    "morning_rank": morning_rank,
                    "live_rank": live_rank,
                }
            )
            if alert_type is None:
                return
            alert_strategies = selection_strategies
            key = f"{race.get('race_id')}:{alert_type}:{','.join(s['strategy_id'] for s in alert_strategies)}"
            if sent.get(key):
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
                "place_name": race.get("place_name"),
                "round": race.get("round"),
                "deadline_time": race.get("deadline_time"),
                "manshu_rate_pct": race.get("manshu_rate_pct"),
                "recent_rate_pct": race.get("recent_rate_pct"),
                "condition": race.get("condition"),
                "checks": checks,
                "metrics": metrics,
                "selection": selection,
                "strategies": alert_strategies,
                "message": make_message(message_race, alert_type, metrics, checks, alert_strategies),
            }
            alerts.append(alert)
            sent[key] = now.isoformat(timespec="seconds")
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
        live_race = live_by_id.get(race.get("race_id"))
        merged_race = morning_race_with_live_rate(race, live_race)
        inspect_race(merged_race, "morning_top", morning_rank=rank, live_rank=merged_race.get("live_rank"))

    if args.scan_risers and not args.offline and live_rows:
        try:
            for live_rank, race in enumerate(live_rows[: args.riser_top_n], start=1):
                if race.get("race_id") in morning_ids:
                    continue
                if (race.get("manshu_rate_pct") or 0) < args.riser_threshold:
                    continue
                inspect_race(race, "late_riser", live_rank=live_rank)
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
        "public_metrics_updated": public_metrics_updated,
        "top_n": args.top_n,
        "live_top_n": args.live_top_n,
        "riser_top_n": args.riser_top_n,
        "lookahead_minutes": args.lookahead_minutes,
        "alert_threshold_pct": args.alert_threshold,
        "alert_policy": {
            "primary": "morning_top_only_post_exhibition_threshold",
            "description": "朝TOPリストに入った荒れ下地ありレースだけを、展示/AI取得後の万舟率で最終確認する",
            "morning_top_n": args.top_n,
            "post_exhibition_threshold_pct": args.alert_threshold,
            "full_exhibition_required": True,
            "notify_late_risers": bool(args.notify_risers),
        },
        "alerts": alerts,
        "inspected": inspected,
    }
    if not args.no_push:
        payload["push"] = push_notifications(payload, state, now)
    save_json(alerts_path(date_text), payload)
    state["updated_at"] = now.isoformat(timespec="seconds")
    save_json(state_path(date_text), state)
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="JST date, e.g. 2026-06-19. Defaults to today.")
    parser.add_argument("--now", help="Override current JST timestamp for tests.")
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--threshold", type=float, default=27.0)
    parser.add_argument("--lookahead-minutes", type=float, default=20.0)
    parser.add_argument("--grace-minutes", type=float, default=2.0)
    parser.add_argument("--alert-threshold", type=float, default=35.0, help="Minimum post-exhibition manshu rate required before sending smartphone alerts.")
    parser.add_argument("--scan-risers", action="store_true", help="Build a separate live ranking and log races rising from outside the morning TOP list.")
    parser.add_argument("--live-top-n", type=int, default=200, help="Live ranking depth used to attach post-exhibition rates to morning watchlist races.")
    parser.add_argument("--riser-top-n", type=int, default=10, help="Live ranking depth used for late-riser detection.")
    parser.add_argument("--riser-threshold", type=float, default=35.0, help="Minimum live manshu rate for late-riser alerts.")
    parser.add_argument(
        "--notify-risers",
        action="store_true",
        help="Also send smartphone alerts for races outside the morning TOP list. Default keeps them as research logs only.",
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
    parser.add_argument("--offline", action="store_true", help="Do not fetch BOATERS pages; only test scheduling windows.")
    parser.add_argument("--no-push", action="store_true", help="Disable smartphone push notifications.")
    parser.add_argument("--no-public-metrics-update", action="store_true", help="Do not merge fetched exhibition metrics back into the public morning-order ranking JSON.")
    parser.add_argument(
        "--backfill-missing-exhibition-hours",
        type=float,
        default=12.0,
        help="After deadline, still fetch ranking races with missing exhibition data for this many hours. Set 0 to disable.",
    )
    args = parser.parse_args()
    payload = monitor(args)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
