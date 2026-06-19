#!/usr/bin/env python3
"""Monitor Codex BOATERS manshu TOP10 races and emit deadline alerts."""

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


def pct(value):
    number = as_num(value)
    if number is None:
        return None
    if -1 <= number <= 1:
        number *= 100
    return round(number, 2)


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


def state_path(date_text):
    return PUBLIC_OUT / f"boaters_manshu_alert_state_{date_text.replace('-', '')}.json"


def alerts_path(date_text):
    return PUBLIC_OUT / f"boaters_manshu_alerts_{date_text.replace('-', '')}.json"


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
        return public_json
    if not rebuild:
        fetched = fetch_public_ranking(date_text, ranking_url_base)
        if fetched is not None:
            return fetched
    if no_build:
        return None

    db_path = WORK_OUT / f"boaters_today_{date_text}.sqlite"
    if rebuild or not db_path.exists():
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
            ],
            BUILD_DB_SCRIPT.parent,
        )

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
    return public_json


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
        title = "BOATERS買い候補" if alert.get("alert_type") == "buy_ok" else "BOATERS万舟率上昇"
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
        return ai_pred + (double_bonus * 0.25)
    if mode == "ai_plus":
        return ai_plus + double_bonus
    if mode == "exhibit":
        return avgdiff * 55 + (7 - tenji) * 6 + (7 - isshu) * 4 + ai_pred * 0.25 + double_bonus
    if mode == "st_exhibit":
        return (7 - st_rank) * 8 + avgdiff * 40 + (7 - tenji) * 5 + ai_pred * 0.2 + double_bonus
    if mode == "worst_ai_plus":
        return -(ai_plus * 0.45 + ai_pred * 0.35 + avgdiff * 40 + (7 - tenji) * 4 + double_bonus)
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


def enrich_rows(by_boat, morning_metrics):
    rows = []
    for boat in range(1, 7):
        source = by_boat.get(boat, {})
        ai_3ren = as_num(source.get("ai_3ren_pct"))
        general = as_num(source.get("general_3ren_pct"))
        row = {
            "boat_number": boat,
            "ai_3ren_pct": ai_3ren,
            "general_3ren_pct": general,
            "st_rank_general": as_num(source.get("st_rank_general")),
            "ai_prediction_pct": as_num(source.get("ai_prediction_pct")),
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
        rows.append(row)

    isshu_values = [row["isshu_time"] for row in rows if row.get("isshu_time") is not None]
    avg_isshu = sum(isshu_values) / len(isshu_values) if isshu_values else None
    for row in rows:
        row["avg_isshu_diff"] = (
            round(avg_isshu - row["isshu_time"], 4)
            if avg_isshu is not None and row.get("isshu_time") is not None
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
    rank_values(rows, "ai_plus", ascending=False)
    rank_values(rows, "general_3ren_pct", ascending=False)
    rank_values(rows, "tenji_time", ascending=True)
    rank_values(rows, "isshu_time", ascending=True)

    for row in rows:
        row["tenji_rank"] = row["tenji_time_rank"]
        row["isshu_rank"] = row["isshu_time_rank"]
        row["double_time"] = row["tenji_rank"] == 1 and row["isshu_rank"] == 1
        row["exhibit_rank"] = min(row["tenji_time_rank"], row["isshu_time_rank"])
        row["outer_good"] = int(row["boat_number"] in {5, 6} and row["exhibit_rank"] <= 2)
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
        row["comp_score"] = (
            row["ai_prediction_pct_rank"] * 0.34
            + row["ai_plus_rank"] * 0.30
            + row["general_3ren_pct_rank"] * 0.12
            + row["exhibit_rank"] * 0.18
            + st_rank * 0.06
            - double_score
        )
        row["value_score"] = (
            row["comp_score"]
            - (0.45 if row["boat_number"] in {4, 5, 6} else 0)
            - (0.70 if row["outer_good"] else 0)
            - (0.30 if row["double_time"] and row["boat_number"] in {5, 6} else 0)
        )
    return rows


def race_metrics(rows):
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
    outer56_best_tenji = min(outer_tenji) if outer_tenji else None
    outer56_best_isshu = min(outer_isshu) if outer_isshu else None
    outer56_best_avgdiff = max(outer_avgdiff) if outer_avgdiff else None
    b1_tenji = b1.get("tenji_time")
    b1_isshu = b1.get("isshu_time")
    rank6 = next((row for row in rows if row.get("ai_plus_rank") == 6), {})
    rank5 = next((row for row in rows if row.get("ai_plus_rank") == 5), {})
    double_time_boats = [row["boat_number"] for row in rows if row.get("double_time")]
    return {
        "boat1_ai_prediction_pct": b1.get("ai_prediction_pct"),
        "boat1_ai_plus": b1.get("ai_plus"),
        "boat1_ai_plus_order": b1.get("ai_plus_rank"),
        "boat1_nige_pct": b1.get("nige_pct"),
        "boat1_loss_pct": b1_loss,
        "boat1_avg_isshu_diff": b1.get("avg_isshu_diff"),
        "boat1_tenji_time": b1_tenji,
        "boat1_isshu_time": b1_isshu,
        "boat1_tenji_rank": b1.get("tenji_rank"),
        "boat1_tenji_time_rank": b1.get("tenji_time_rank"),
        "boat1_isshu_rank": b1.get("isshu_rank"),
        "outer56_best_tenji_time": outer56_best_tenji,
        "outer56_best_isshu_time": outer56_best_isshu,
        "outer56_best_avg_isshu_diff": outer56_best_avgdiff,
        "outer56_best_ai_prediction_pct": max(outer_ai_pred) if outer_ai_pred else None,
        "ai_rank6_boat": rank6.get("boat_number"),
        "ai_rank6_avg_isshu_diff": rank6.get("avg_isshu_diff"),
        "ai_rank6_tenji_rank": rank6.get("tenji_rank"),
        "ai_rank5_boat": rank5.get("boat_number"),
        "ai_rank5_avg_isshu_diff": rank5.get("avg_isshu_diff"),
        "ai_rank5_tenji_rank": rank5.get("tenji_rank"),
        "double_time_boats": double_time_boats,
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
        "tenji_boats": sum(1 for row in rows if row.get("tenji_time") is not None),
        "isshu_boats": sum(1 for row in rows if row.get("isshu_time") is not None),
    }


def condition_confirmed(condition, metrics):
    checks = []
    text = str(condition or "")
    if "1号艇平均との差" in text:
        if "-0.05以下" in text:
            checks.append(("1号艇平均との差-0.05以下", (metrics.get("boat1_avg_isshu_diff") or 9) <= -0.05))
        elif "0以下" in text:
            checks.append(("1号艇平均との差0以下", (metrics.get("boat1_avg_isshu_diff") or 9) <= 0))
        elif "0.10以上" in text:
            checks.append(("1号艇平均との差0.10以上", (metrics.get("boat1_avg_isshu_diff") or -9) >= 0.10))

    if "5/6号艇平均との差" in text:
        if "0.14以上" in text:
            checks.append(("5/6平均との差0.14以上", (metrics.get("outer56_best_avg_isshu_diff") or -9) >= 0.14))
        elif "0.10以上" in text:
            checks.append(("5/6平均との差0.10以上", (metrics.get("outer56_best_avg_isshu_diff") or -9) >= 0.10))

    if "AI+最下位の平均との差0.10以上" in text:
        checks.append(("AI+最下位平均との差0.10以上", (metrics.get("ai_rank6_avg_isshu_diff") or -9) >= 0.10))

    if "AI+最下位が5/6号艇" in text:
        checks.append(("AI+最下位が5/6号艇", int(metrics.get("ai_rank6_boat") or 0) in {5, 6}))

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
    b1_bad = (
        (metrics.get("boat1_tenji_rank", 9) >= 4)
        or (metrics.get("boat1_tenji_time_rank", 9) >= 4)
        or (metrics.get("boat1_isshu_rank", 9) >= 4)
    )
    strategies = []
    wind_wave = (weather_value(race, "wind_speed") or 0) >= 5 or (weather_value(race, "wave_height") or 0) >= 5
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
        and (metrics.get("boat1_nige_pct") or 999) < 50
        and (metrics.get("boat1_loss_pct") or -1) >= 40
        and metrics.get("outer56_exhibit_top2_count", 0) >= 2
        and (metrics.get("outer56_tenji_advantage") or -9) >= 0.03
    ):
        strategies.append(("miyajima_outer_no1", "宮島: 外2艇展示浮上 1号艇全消し 12点", mid_heads_outer_no1))
    if (
        place == "丸亀"
        and (metrics.get("boat1_nige_pct") or 999) < 45
        and (metrics.get("boat1_loss_pct") or -1) >= 45
        and metrics.get("outer56_isshu_top2_count", 0) >= 1
        and metrics.get("outer56_low_aipred_exhibit_top2_count", 0) >= 1
    ):
        strategies.append(("marugame_outer_no1", "丸亀: 1弱+外低評価浮上 1号艇全消し 12点", mid_heads_outer_no1))

    out = []
    for strategy_id, label, ticket_func in strategies:
        tickets, roles = ticket_func(rows)
        if not tickets or roles is None:
            continue
        out.append(
            {
                "strategy_id": strategy_id,
                "label": label,
                "points": len(tickets),
                "heads": roles["heads"],
                "axes": roles["axes"],
                "supports": roles.get("supports", []),
                "keshi": roles["keshi"],
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


def fetch_live_race(race, refresh=True):
    place = race.get("place_name")
    slug = race.get("slug") or PLACE_SLUGS.get(place)
    if not slug:
        raise RuntimeError(f"unknown place slug: {place}")
    date_text = race.get("date")
    round_no = int(race.get("round"))
    data = extract_data_page(fetch_boaters_page(slug, date_text, round_no, "data", refresh=refresh))
    last = extract_last_minute_page(fetch_boaters_page(slug, date_text, round_no, "last-minute", refresh=refresh))
    by_boat = {}
    for boat in range(1, 7):
        row = {}
        row.update(data.get(boat, {}))
        row.update(last.get(boat, {}))
        by_boat[boat] = row
    return by_boat


def make_message(race, alert_type, metrics, checks, strategies):
    base = (
        f"{race.get('place_name')}{race.get('round')}R "
        f"万舟率{fmt_pct(race.get('manshu_rate_pct'))}"
    )
    deadline = parse_dt(race.get("deadline_time"))
    deadline_text = deadline.strftime("%H:%M") if deadline else "--:--"
    metric_text = (
        f"締切{deadline_text} / 1号艇逃げ{fmt_pct(metrics.get('boat1_nige_pct'))}, "
        f"逃げ失敗{fmt_pct(metrics.get('boat1_loss_pct'))}, "
        f"1平均との差{fmt_time(metrics.get('boat1_avg_isshu_diff'))}, "
        f"1展示{fmt_time(metrics.get('boat1_tenji_time'))}"
        f"({metrics.get('boat1_tenji_time_rank')}位), "
        f"5/6平均との差{fmt_time(metrics.get('outer56_best_avg_isshu_diff'))}"
        f"{fmt_double_time(metrics)}"
    )
    if alert_type == "buy_ok" and strategies:
        s = strategies[0]
        support_text = f" / 相手: {fmt_list(s.get('supports'))}" if s.get("supports") else ""
        return (
            f"【買い候補】{base}\n"
            f"{metric_text}\n"
            f"直前条件: {' / '.join(checks)}\n"
            f"買い方: {s['label']} / {s['points']}点 / {s['odds_filter']}\n"
            f"頭候補: {fmt_list(s['heads'])} / 軸: {fmt_list(s['axes'])}{support_text} / 消し: {fmt_role(s['keshi'])}\n"
            f"買い目: {' '.join(s['tickets'])}"
        )
    return (
        f"【万舟率上昇候補】{base}\n"
        f"{metric_text}\n"
        f"直前条件: {' / '.join(checks)}"
    )


def monitor(args):
    date_text = args.date or today_jst()
    now = parse_dt(args.now) if args.now else datetime.now(JST)
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
    races = (ranking.get("races") or [])[: args.top_n]
    state = load_json(state_path(date_text), {"sent": {}})
    sent = state.setdefault("sent", {})

    inspected = []
    alerts = []
    for race in races:
        deadline = parse_dt(race.get("deadline_time"))
        if deadline is None:
            inspected.append({"race_id": race.get("race_id"), "status": "skip_no_deadline"})
            continue
        minutes_to_deadline = (deadline - now).total_seconds() / 60
        if minutes_to_deadline > args.lookahead_minutes or minutes_to_deadline < -args.grace_minutes:
            inspected.append(
                {
                    "race_id": race.get("race_id"),
                    "place_name": race.get("place_name"),
                    "round": race.get("round"),
                    "status": "outside_window",
                    "minutes_to_deadline": round(minutes_to_deadline, 1),
                }
            )
            continue
        if args.offline:
            inspected.append(
                {
                    "race_id": race.get("race_id"),
                    "place_name": race.get("place_name"),
                    "round": race.get("round"),
                    "status": "offline_window_match",
                    "minutes_to_deadline": round(minutes_to_deadline, 1),
                }
            )
            continue

        try:
            by_boat = fetch_live_race(race, refresh=not args.no_refresh)
            rows = enrich_rows(by_boat, race.get("metrics") or {})
            metrics = race_metrics(rows)
            confirmed, checks = condition_confirmed(race.get("condition"), metrics)
            strategies = roi_strategies(race, metrics, rows)
            alert_type = "buy_ok" if strategies else "rate_up" if confirmed else None
            inspected.append(
                {
                    "race_id": race.get("race_id"),
                    "place_name": race.get("place_name"),
                    "round": race.get("round"),
                    "status": "checked",
                    "minutes_to_deadline": round(minutes_to_deadline, 1),
                    "condition_confirmed": confirmed,
                    "checks": checks,
                    "strategies": [s["strategy_id"] for s in strategies],
                    "metrics": metrics,
                }
            )
            if alert_type is None:
                continue
            key = f"{race.get('race_id')}:{alert_type}:{','.join(s['strategy_id'] for s in strategies)}"
            if sent.get(key):
                continue
            alert = {
                "alert_type": alert_type,
                "race_id": race.get("race_id"),
                "date": race.get("date"),
                "rank": race.get("rank"),
                "place_name": race.get("place_name"),
                "round": race.get("round"),
                "deadline_time": race.get("deadline_time"),
                "manshu_rate_pct": race.get("manshu_rate_pct"),
                "recent_rate_pct": race.get("recent_rate_pct"),
                "condition": race.get("condition"),
                "checks": checks,
                "metrics": metrics,
                "strategies": strategies,
                "message": make_message(race, alert_type, metrics, checks, strategies),
            }
            alerts.append(alert)
            sent[key] = now.isoformat(timespec="seconds")
        except Exception as exc:
            inspected.append(
                {
                    "race_id": race.get("race_id"),
                    "place_name": race.get("place_name"),
                    "round": race.get("round"),
                    "status": "fetch_failed",
                    "minutes_to_deadline": round(minutes_to_deadline, 1),
                    "error": str(exc),
                }
            )

    payload = {
        "version": "boaters-manshu-alerts-v1",
        "date": date_text,
        "generated_at": now.isoformat(timespec="seconds"),
        "ranking_path": str(ranking_path),
        "top_n": args.top_n,
        "lookahead_minutes": args.lookahead_minutes,
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
    parser.add_argument("--rebuild-morning", action="store_true")
    parser.add_argument(
        "--no-build-morning",
        action="store_true",
        help="Do not build the morning ranking DB; use local/public ranking JSON only.",
    )
    parser.add_argument(
        "--ranking-url-base",
        default="https://boat10000.github.io/kyotei-occult-viewer/data/output",
        help="Public base URL for daily ranking JSON fallback.",
    )
    parser.add_argument("--no-refresh", action="store_true", help="Use cached BOATERS pages when available.")
    parser.add_argument("--offline", action="store_true", help="Do not fetch BOATERS pages; only test scheduling windows.")
    parser.add_argument("--no-push", action="store_true", help="Disable smartphone push notifications.")
    args = parser.parse_args()
    payload = monitor(args)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
