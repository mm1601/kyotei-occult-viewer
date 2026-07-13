#!/usr/bin/env python3
"""Store and derive official features that are not in beforeinfo.

The module is deliberately source-oriented:

* ``official_racelist_*`` stores the official BOATRACE entry list as-is.
* ``official_venue_entry_baselines`` turns motor/boat rates into venue-relative
  values without overwriting the raw rates.
* ``self_racer_course_profiles`` derives leakage-safe racer/course history
  using results strictly before ``as_of_date``.
* ``official_odds_*_features`` summarizes the already-collected time-series
  trifecta odds.
* ``race_environment_snapshots`` is a generic insertion point for tide/current
  data when an authoritative source is connected.

The combined view ``v_race_boats_feature_store`` is the stable interface for
future AI training and live prediction.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sqlite3
import ssl
import statistics
import unicodedata
import urllib.request
import warnings
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "output" / "boaters_all_races.sqlite"
JST = timezone(timedelta(hours=9))
SOURCE = "official_boatrace_racelist"
PARSER_VERSION = 1
RACELIST_URL = (
    "https://www.boatrace.jp/owpc/pc/race/racelist"
    "?rno={round_no}&jcd={place_id:02d}&hd={ymd}"
)

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)


def now_jst_text() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def as_int(value: Any) -> int | None:
    number = as_float(value)
    return int(number) if number is not None else None


def normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()


def numbers(value: Any) -> list[float]:
    text = normalize_text(value)
    return [float(item) for item in re.findall(r"(?<![A-Za-z])[-+]?\d+(?:\.\d+)?", text)]


def race_id_for(date_text: str, place_id: int, round_no: int) -> str:
    return f"{date_text}{int(place_id):02d}{int(round_no):02d}"


def racelist_url(race: Mapping[str, Any]) -> str:
    date_text = str(race.get("date") or "")
    return RACELIST_URL.format(
        round_no=int(race.get("round") or race.get("round_no") or 0),
        place_id=int(race.get("place_id") or 0),
        ymd=date_text.replace("-", ""),
    )


def fetch_url(url: str, timeout: int = 25) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept-Language": "ja,en-US;q=0.8,en;q=0.6",
        },
    )
    with urllib.request.urlopen(
        request,
        timeout=timeout,
        context=ssl._create_unverified_context(),
    ) as response:
        return response.read().decode("utf-8", errors="replace")


def _cell_lines(cell: Any) -> list[str]:
    return [normalize_text(item) for item in cell.stripped_strings if normalize_text(item)]


def _stats_triplet(cell: Any) -> tuple[float | None, float | None, float | None]:
    values = numbers(" ".join(_cell_lines(cell)))
    values.extend([None] * (3 - len(values)))
    return values[0], values[1], values[2]


def _equipment_triplet(cell: Any) -> tuple[int | None, float | None, float | None]:
    values = numbers(" ".join(_cell_lines(cell)))
    values.extend([None] * (3 - len(values)))
    number = int(values[0]) if values[0] is not None else None
    return number, values[1], values[2]


def _racer_fields(cell: Any) -> dict[str, Any]:
    lines = _cell_lines(cell)
    text = " | ".join(lines)
    link = cell.find("a", href=re.compile(r"racersearch/profile"))
    href = str(link.get("href") or "") if link else ""
    reg_match = re.search(r"toban=(\d{4})", href) or re.search(r"\b(\d{4})\b", text)
    class_match = re.search(r"\b(A1|A2|B1|B2)\b", text)
    age_weight = re.search(r"(\d{1,2})歳\s*/\s*([0-9.]+)kg", text)
    racer_name = normalize_text(link.get_text(" ", strip=True)) if link else ""

    branch = ""
    hometown = ""
    for line in lines:
        if "歳" in line or re.search(r"\b\d{4}\b", line) or line in {"/", "A1", "A2", "B1", "B2"}:
            continue
        if line == racer_name:
            continue
        match = re.fullmatch(r"([^/]+)/([^/]+)", line)
        if match:
            branch, hometown = match.groups()
            break

    return {
        "reg_no": int(reg_match.group(1)) if reg_match else None,
        "racer_id": reg_match.group(1) if reg_match else "",
        "racer_name": racer_name,
        "racer_class": class_match.group(1) if class_match else "",
        "branch": branch,
        "hometown": hometown,
        "age": int(age_weight.group(1)) if age_weight else None,
        "weight": float(age_weight.group(2)) if age_weight else None,
    }


def parse_racelist(text: str) -> dict[str, Any]:
    """Parse the official race entry table into one row per boat."""

    soup = BeautifulSoup(text or "", "lxml")
    table = None
    for candidate in soup.find_all("table"):
        header = normalize_text(candidate.get_text(" ", strip=True))
        if all(label in header for label in ("全国", "当地", "モーター", "ボート", "登録番号")):
            table = candidate
            break
    if table is None:
        return {
            "available": False,
            "boats": [],
            "boat_rows": 0,
            "error": "official racelist table not found",
        }

    parsed: list[dict[str, Any]] = []
    for body in table.find_all("tbody", recursive=False):
        first_row = body.find("tr", recursive=False)
        if first_row is None:
            continue
        cells = first_row.find_all("td", recursive=False)
        if len(cells) < 8:
            continue
        frame_numbers = numbers(" ".join(_cell_lines(cells[0])))
        if not frame_numbers:
            continue
        boat_number = int(frame_numbers[0])
        if not 1 <= boat_number <= 6:
            continue

        racer = _racer_fields(cells[2])
        fl_lines = _cell_lines(cells[3])
        fl_text = " ".join(fl_lines)
        f_match = re.search(r"\bF(\d+)\b", fl_text)
        l_match = re.search(r"\bL(\d+)\b", fl_text)
        avg_st_values = [value for value in numbers(fl_text) if 0 <= value < 1]
        national = _stats_triplet(cells[4])
        local = _stats_triplet(cells[5])
        motor = _equipment_triplet(cells[6])
        equipment_boat = _equipment_triplet(cells[7])
        parsed.append(
            {
                "boat_number": boat_number,
                **racer,
                "flying_count": int(f_match.group(1)) if f_match else None,
                "late_count": int(l_match.group(1)) if l_match else None,
                "avg_st": avg_st_values[-1] if avg_st_values else None,
                "national_win_rate": national[0],
                "national_2ren_pct": national[1],
                "national_3ren_pct": national[2],
                "local_win_rate": local[0],
                "local_2ren_pct": local[1],
                "local_3ren_pct": local[2],
                "motor_number": motor[0],
                "motor_2ren_pct": motor[1],
                "motor_3ren_pct": motor[2],
                "equipment_boat_number": equipment_boat[0],
                "equipment_boat_2ren_pct": equipment_boat[1],
                "equipment_boat_3ren_pct": equipment_boat[2],
            }
        )

    parsed.sort(key=lambda item: int(item["boat_number"]))
    title = soup.select_one(".heading2_titleName")
    return {
        "available": len(parsed) >= 1,
        "boats": parsed,
        "boat_rows": len(parsed),
        "series_title": normalize_text(title.get_text(" ", strip=True)) if title else "",
        "error": "" if parsed else "official racelist has no boat rows",
    }


def fetch_racelist(race: Mapping[str, Any], timeout: int = 25) -> dict[str, Any]:
    url = racelist_url(race)
    text = fetch_url(url, timeout=timeout)
    payload = parse_racelist(text)
    payload.update(
        {
            "source": SOURCE,
            "url": url,
            "parser_version": PARSER_VERSION,
            "fetched_at": now_jst_text(),
            "content_sha256": hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest(),
        }
    )
    return payload


def _object_exists(con: sqlite3.Connection, name: str) -> bool:
    return bool(
        con.execute(
            "SELECT 1 FROM sqlite_master WHERE name = ? AND type IN ('table', 'view')",
            (name,),
        ).fetchone()
    )


def _columns(con: sqlite3.Connection, name: str) -> set[str]:
    return {str(row[1]) for row in con.execute(f"PRAGMA table_info({name})")}


def _ensure_columns(con: sqlite3.Connection, table: str, columns: Mapping[str, str]) -> None:
    existing = _columns(con, table)
    for name, definition in columns.items():
        if name not in existing:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def init_schema(con: sqlite3.Connection) -> None:
    con.execute("PRAGMA busy_timeout = 30000")
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS official_racelist_races (
          race_id TEXT PRIMARY KEY,
          date TEXT NOT NULL,
          place_id INTEGER NOT NULL,
          place_name TEXT,
          round INTEGER NOT NULL,
          series_title TEXT,
          source TEXT,
          available INTEGER NOT NULL DEFAULT 0,
          url TEXT,
          parser_version INTEGER,
          expected_boats INTEGER,
          boat_rows INTEGER,
          content_sha256 TEXT,
          fetched_at TEXT,
          error TEXT
        );

        CREATE TABLE IF NOT EXISTS official_racelist_boats (
          race_id TEXT NOT NULL,
          date TEXT NOT NULL,
          place_id INTEGER NOT NULL,
          round INTEGER NOT NULL,
          boat_number INTEGER NOT NULL,
          racer_id TEXT,
          reg_no INTEGER,
          racer_name TEXT,
          racer_class TEXT,
          branch TEXT,
          hometown TEXT,
          age INTEGER,
          weight REAL,
          flying_count INTEGER,
          late_count INTEGER,
          avg_st REAL,
          national_win_rate REAL,
          national_2ren_pct REAL,
          national_3ren_pct REAL,
          local_win_rate REAL,
          local_2ren_pct REAL,
          local_3ren_pct REAL,
          motor_number INTEGER,
          motor_2ren_pct REAL,
          motor_3ren_pct REAL,
          equipment_boat_number INTEGER,
          equipment_boat_2ren_pct REAL,
          equipment_boat_3ren_pct REAL,
          source TEXT,
          fetched_at TEXT,
          PRIMARY KEY (race_id, boat_number)
        );

        CREATE TABLE IF NOT EXISTS official_venue_entry_baselines (
          date TEXT NOT NULL,
          place_id INTEGER NOT NULL,
          motor_sample INTEGER,
          motor_2ren_mean REAL,
          motor_3ren_mean REAL,
          equipment_boat_sample INTEGER,
          equipment_boat_2ren_mean REAL,
          equipment_boat_3ren_mean REAL,
          entrant_sample INTEGER,
          national_win_rate_mean REAL,
          local_win_rate_mean REAL,
          avg_st_mean REAL,
          updated_at TEXT,
          PRIMARY KEY (date, place_id)
        );

        CREATE TABLE IF NOT EXISTS self_racer_course_profiles (
          as_of_date TEXT NOT NULL,
          reg_no INTEGER NOT NULL,
          place_id INTEGER NOT NULL,
          course INTEGER NOT NULL,
          starts INTEGER NOT NULL,
          wins INTEGER NOT NULL,
          top3s INTEGER NOT NULL,
          win_pct REAL,
          top3_pct REAL,
          avg_start_time REAL,
          last_seen_date TEXT,
          source TEXT,
          updated_at TEXT,
          PRIMARY KEY (as_of_date, reg_no, place_id, course)
        );

        CREATE TABLE IF NOT EXISTS race_environment_snapshots (
          race_id TEXT NOT NULL,
          source TEXT NOT NULL,
          snapshot_at TEXT NOT NULL,
          date TEXT NOT NULL,
          place_id INTEGER NOT NULL,
          round INTEGER NOT NULL,
          source_url TEXT,
          observed_at TEXT,
          prediction_at TEXT,
          source_kind TEXT,
          applicability TEXT,
          quality TEXT,
          station_code TEXT,
          station_name TEXT,
          station_distance_km REAL,
          tide_level_cm REAL,
          predicted_tide_cm REAL,
          predicted_tide_tp_cm REAL,
          observed_tide_cm REAL,
          observed_tide_tp_cm REAL,
          observed_age_minutes REAL,
          observed_lead_to_race_minutes REAL,
          observed_prediction_anomaly_cm REAL,
          tide_phase TEXT,
          tide_delta_cm_per_hour REAL,
          daily_tide_min_cm REAL,
          daily_tide_max_cm REAL,
          daily_tide_range_cm REAL,
          tide_range_position REAL,
          previous_extreme_type TEXT,
          previous_extreme_at TEXT,
          previous_extreme_level_cm REAL,
          next_extreme_type TEXT,
          next_extreme_at TEXT,
          next_extreme_level_cm REAL,
          minutes_to_next_extreme REAL,
          current_speed_mps REAL,
          current_signed_knots REAL,
          current_region TEXT,
          current_prediction_at TEXT,
          current_quality TEXT,
          current_direction_deg REAL,
          current_direction_text TEXT,
          air_pressure_hpa REAL,
          precipitation_mm REAL,
          weather TEXT,
          air_temp_c REAL,
          water_temp_c REAL,
          wind_speed_mps REAL,
          wind_direction_deg REAL,
          wave_height_cm REAL,
          collection_status TEXT,
          error TEXT,
          raw_json TEXT,
          PRIMARY KEY (race_id, source, snapshot_at)
        );

        CREATE TABLE IF NOT EXISTS official_odds_race_features (
          race_id TEXT PRIMARY KEY,
          date TEXT NOT NULL,
          place_id INTEGER NOT NULL,
          round INTEGER NOT NULL,
          source TEXT,
          snapshot_count INTEGER,
          first_snapshot_at TEXT,
          close_snapshot_at TEXT,
          close_minutes_to_deadline REAL,
          combo_count INTEGER,
          favorite_combo TEXT,
          favorite_odds REAL,
          favorite_market_pct REAL,
          market_entropy REAL,
          raw_inverse_sum REAL,
          updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS official_odds_boat_features (
          race_id TEXT NOT NULL,
          date TEXT NOT NULL,
          place_id INTEGER NOT NULL,
          round INTEGER NOT NULL,
          boat_number INTEGER NOT NULL,
          source TEXT,
          first_market_win_pct REAL,
          close_market_win_pct REAL,
          market_win_move_pct REAL,
          first_market_top3_pct REAL,
          close_market_top3_pct REAL,
          market_top3_move_pct REAL,
          close_min_head_odds REAL,
          close_top10_head_count INTEGER,
          close_snapshot_at TEXT,
          updated_at TEXT,
          PRIMARY KEY (race_id, boat_number)
        );

        CREATE INDEX IF NOT EXISTS idx_official_racelist_races_date
          ON official_racelist_races(date, place_id, round);
        CREATE INDEX IF NOT EXISTS idx_official_racelist_boats_date
          ON official_racelist_boats(date, place_id, round, boat_number);
        CREATE INDEX IF NOT EXISTS idx_official_racelist_boats_reg
          ON official_racelist_boats(reg_no, date, place_id);
        CREATE INDEX IF NOT EXISTS idx_racer_course_profiles_lookup
          ON self_racer_course_profiles(as_of_date, reg_no, place_id, course);
        CREATE INDEX IF NOT EXISTS idx_environment_race_observed
          ON race_environment_snapshots(race_id, observed_at, snapshot_at);
        CREATE INDEX IF NOT EXISTS idx_odds_boat_features_date
          ON official_odds_boat_features(date, place_id, round, boat_number);

        DROP VIEW IF EXISTS v_latest_race_environment;
        CREATE VIEW v_latest_race_environment AS
        SELECT *
        FROM (
          SELECT e.*,
                 ROW_NUMBER() OVER (
                   PARTITION BY e.race_id
                   ORDER BY COALESCE(e.observed_at, e.snapshot_at) DESC, e.snapshot_at DESC
                 ) AS environment_row_number
          FROM race_environment_snapshots e
        )
        WHERE environment_row_number = 1;
        """
    )
    _ensure_columns(
        con,
        "race_environment_snapshots",
        {
            "prediction_at": "TEXT",
            "source_kind": "TEXT",
            "applicability": "TEXT",
            "quality": "TEXT",
            "station_code": "TEXT",
            "station_name": "TEXT",
            "station_distance_km": "REAL",
            "predicted_tide_cm": "REAL",
            "predicted_tide_tp_cm": "REAL",
            "observed_tide_cm": "REAL",
            "observed_tide_tp_cm": "REAL",
            "observed_age_minutes": "REAL",
            "observed_lead_to_race_minutes": "REAL",
            "observed_prediction_anomaly_cm": "REAL",
            "daily_tide_min_cm": "REAL",
            "daily_tide_max_cm": "REAL",
            "daily_tide_range_cm": "REAL",
            "tide_range_position": "REAL",
            "previous_extreme_type": "TEXT",
            "previous_extreme_at": "TEXT",
            "previous_extreme_level_cm": "REAL",
            "next_extreme_type": "TEXT",
            "next_extreme_at": "TEXT",
            "next_extreme_level_cm": "REAL",
            "minutes_to_next_extreme": "REAL",
            "current_signed_knots": "REAL",
            "current_region": "TEXT",
            "current_prediction_at": "TEXT",
            "current_quality": "TEXT",
            "collection_status": "TEXT",
            "error": "TEXT",
        },
    )
    con.execute("DROP VIEW IF EXISTS v_latest_race_environment")
    con.execute(
        """
        CREATE VIEW v_latest_race_environment AS
        SELECT *
        FROM (
          SELECT e.*,
                 ROW_NUMBER() OVER (
                   PARTITION BY e.race_id
                   ORDER BY COALESCE(e.observed_at, e.snapshot_at) DESC, e.snapshot_at DESC
                 ) AS environment_row_number
          FROM race_environment_snapshots e
        )
        WHERE environment_row_number = 1
        """
    )
    if _object_exists(con, "race_boats") and "reg_no" in _columns(con, "race_boats"):
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_race_boats_reg_date_place ON race_boats(reg_no, date, place_id)"
        )
    _rebuild_feature_view(con)
    con.commit()


def _rebuild_feature_view(con: sqlite3.Connection) -> None:
    con.execute("DROP VIEW IF EXISTS v_race_boats_feature_store")
    base = "v_race_boats_with_official_aux" if _object_exists(con, "v_race_boats_with_official_aux") else "race_boats"
    if not _object_exists(con, base):
        return
    base_columns = _columns(con, base)
    if "official_before_start_sinnyu" in base_columns:
        course_expr = "COALESCE(b.official_before_start_sinnyu, b.before_start_sinnyu, b.boat_number)"
    elif "before_start_sinnyu" in base_columns:
        course_expr = "COALESCE(b.before_start_sinnyu, b.boat_number)"
    else:
        course_expr = "b.boat_number"
    ai_win_gap = (
        "b.ai_prediction_pct - odds.close_market_win_pct"
        if "ai_prediction_pct" in base_columns
        else "NULL"
    )
    ai_top3_gap = (
        "b.ai_3ren_pct - odds.close_market_top3_pct"
        if "ai_3ren_pct" in base_columns
        else "NULL"
    )
    con.executescript(
        f"""
        CREATE VIEW v_race_boats_feature_store AS
        SELECT
          b.*,
          entry.racer_class AS official_racer_class,
          entry.branch AS official_racer_branch,
          entry.hometown AS official_racer_hometown,
          entry.age AS official_racer_age,
          entry.flying_count AS official_flying_count,
          entry.late_count AS official_late_count,
          entry.avg_st AS official_avg_st,
          entry.national_win_rate,
          entry.national_2ren_pct,
          entry.national_3ren_pct,
          entry.local_win_rate,
          entry.local_2ren_pct,
          entry.local_3ren_pct,
          entry.motor_number,
          entry.motor_2ren_pct,
          entry.motor_3ren_pct,
          entry.equipment_boat_number,
          entry.equipment_boat_2ren_pct,
          entry.equipment_boat_3ren_pct,
          entry.motor_2ren_pct - baseline.motor_2ren_mean AS motor_2ren_vs_venue,
          entry.motor_3ren_pct - baseline.motor_3ren_mean AS motor_3ren_vs_venue,
          entry.equipment_boat_2ren_pct - baseline.equipment_boat_2ren_mean AS equipment_boat_2ren_vs_venue,
          entry.equipment_boat_3ren_pct - baseline.equipment_boat_3ren_mean AS equipment_boat_3ren_vs_venue,
          local_profile.starts AS local_course_starts,
          local_profile.win_pct AS local_course_win_pct,
          local_profile.top3_pct AS local_course_top3_pct,
          local_profile.avg_start_time AS local_course_avg_start_time,
          national_profile.starts AS national_course_starts,
          national_profile.win_pct AS national_course_win_pct,
          national_profile.top3_pct AS national_course_top3_pct,
          national_profile.avg_start_time AS national_course_avg_start_time,
          env.tide_level_cm,
          COALESCE(
            env.applicability,
            CASE
              WHEN b.place_id = 8 THEN 'controlled'
              WHEN b.place_id IN (1, 2, 5, 10, 11, 12, 13, 21) THEN 'not_applicable'
              ELSE 'applicable'
            END
          ) AS water_applicability,
          COALESCE(
            env.quality,
            CASE
              WHEN b.place_id IN (1, 2, 5, 8, 10, 11, 12, 13, 21) THEN 'not_applicable'
              ELSE 'unavailable'
            END
          ) AS water_source_quality,
          env.predicted_tide_cm,
          env.predicted_tide_tp_cm,
          env.observed_tide_cm,
          env.observed_tide_tp_cm,
          env.observed_age_minutes,
          env.observed_lead_to_race_minutes,
          env.observed_prediction_anomaly_cm,
          env.tide_phase,
          env.tide_delta_cm_per_hour,
          env.daily_tide_range_cm,
          env.tide_range_position,
          env.minutes_to_next_extreme,
          env.next_extreme_type,
          env.current_speed_mps,
          env.current_signed_knots,
          env.current_quality,
          env.current_direction_deg,
          env.current_direction_text,
          env.air_pressure_hpa,
          env.precipitation_mm,
          odds.first_market_win_pct,
          odds.close_market_win_pct,
          odds.market_win_move_pct,
          odds.first_market_top3_pct,
          odds.close_market_top3_pct,
          odds.market_top3_move_pct,
          odds.close_min_head_odds,
          odds.close_top10_head_count,
          {ai_win_gap} AS stored_ai_market_win_gap_pct,
          {ai_top3_gap} AS stored_ai_market_top3_gap_pct,
          odds_race.snapshot_count AS odds_snapshot_count,
          odds_race.close_minutes_to_deadline AS odds_close_minutes_to_deadline,
          odds_race.favorite_combo AS odds_favorite_combo,
          odds_race.favorite_odds AS odds_favorite_value,
          odds_race.market_entropy AS odds_market_entropy
        FROM {base} b
        LEFT JOIN official_racelist_boats entry
          ON entry.race_id = b.race_id
         AND entry.boat_number = b.boat_number
        LEFT JOIN official_venue_entry_baselines baseline
          ON baseline.date = b.date
         AND baseline.place_id = b.place_id
        LEFT JOIN self_racer_course_profiles local_profile
          ON local_profile.as_of_date = b.date
         AND local_profile.reg_no = COALESCE(entry.reg_no, b.reg_no)
         AND local_profile.place_id = b.place_id
         AND local_profile.course = {course_expr}
        LEFT JOIN self_racer_course_profiles national_profile
          ON national_profile.as_of_date = b.date
         AND national_profile.reg_no = COALESCE(entry.reg_no, b.reg_no)
         AND national_profile.place_id = 0
         AND national_profile.course = {course_expr}
        LEFT JOIN v_latest_race_environment env
          ON env.race_id = b.race_id
        LEFT JOIN official_odds_boat_features odds
          ON odds.race_id = b.race_id
         AND odds.boat_number = b.boat_number
        LEFT JOIN official_odds_race_features odds_race
          ON odds_race.race_id = b.race_id;
        """
    )


def save_racelist(con: sqlite3.Connection, race: Mapping[str, Any], payload: Mapping[str, Any]) -> None:
    race_id = str(race.get("race_id") or race_id_for(str(race["date"]), int(race["place_id"]), int(race["round"])))
    boats = payload.get("boats") if isinstance(payload.get("boats"), list) else []
    fetched_at = str(payload.get("fetched_at") or now_jst_text())
    con.execute(
        """
        INSERT OR REPLACE INTO official_racelist_races (
          race_id, date, place_id, place_name, round, series_title, source,
          available, url, parser_version, expected_boats, boat_rows,
          content_sha256, fetched_at, error
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            race_id,
            race["date"],
            int(race["place_id"]),
            race.get("place_name") or "",
            int(race["round"]),
            payload.get("series_title") or "",
            payload.get("source") or SOURCE,
            1 if payload.get("available") else 0,
            payload.get("url") or racelist_url(race),
            int(payload.get("parser_version") or PARSER_VERSION),
            int(race.get("expected_boats") or 6),
            int(payload.get("boat_rows") or len(boats)),
            payload.get("content_sha256") or "",
            fetched_at,
            payload.get("error") or "",
        ),
    )
    con.execute("DELETE FROM official_racelist_boats WHERE race_id = ?", (race_id,))
    columns = (
        "race_id", "date", "place_id", "round", "boat_number", "racer_id", "reg_no",
        "racer_name", "racer_class", "branch", "hometown", "age", "weight",
        "flying_count", "late_count", "avg_st", "national_win_rate", "national_2ren_pct",
        "national_3ren_pct", "local_win_rate", "local_2ren_pct", "local_3ren_pct",
        "motor_number", "motor_2ren_pct", "motor_3ren_pct", "equipment_boat_number",
        "equipment_boat_2ren_pct", "equipment_boat_3ren_pct", "source", "fetched_at",
    )
    placeholders = ",".join("?" for _ in columns)
    for boat in boats:
        row = {
            **boat,
            "race_id": race_id,
            "date": race["date"],
            "place_id": int(race["place_id"]),
            "round": int(race["round"]),
            "source": payload.get("source") or SOURCE,
            "fetched_at": fetched_at,
        }
        con.execute(
            f"INSERT OR REPLACE INTO official_racelist_boats ({','.join(columns)}) VALUES ({placeholders})",
            tuple(row.get(column) for column in columns),
        )


def load_racelist_status(con: sqlite3.Connection, date_text: str) -> dict[str, sqlite3.Row]:
    return {
        str(row["race_id"]): row
        for row in con.execute(
            """
            SELECT race_id, available, boat_rows, fetched_at, error
            FROM official_racelist_races
            WHERE date = ?
            """,
            (date_text,),
        )
    }


def refresh_venue_entry_baselines(
    con: sqlite3.Connection,
    date_text: str,
    place_ids: Iterable[int] | None = None,
) -> int:
    ids = sorted({int(value) for value in (place_ids or []) if value})
    if not ids:
        ids = [int(row[0]) for row in con.execute(
            "SELECT DISTINCT place_id FROM official_racelist_boats WHERE date = ?",
            (date_text,),
        )]
    updated = 0
    for place_id in ids:
        rows = con.execute(
            """
            SELECT * FROM official_racelist_boats
            WHERE date = ? AND place_id = ?
            """,
            (date_text, place_id),
        ).fetchall()
        if not rows:
            continue
        names = [description[0] for description in con.execute(
            "SELECT * FROM official_racelist_boats LIMIT 0"
        ).description]
        records = [dict(zip(names, row)) for row in rows]

        def dedup_mean(number_key: str, value_key: str) -> tuple[int, float | None]:
            grouped: dict[int, list[float]] = defaultdict(list)
            for record in records:
                number = as_int(record.get(number_key))
                value = as_float(record.get(value_key))
                if number is not None and value is not None:
                    grouped[number].append(value)
            values = [statistics.fmean(group) for group in grouped.values() if group]
            return len(values), statistics.fmean(values) if values else None

        motor_sample, motor_2ren = dedup_mean("motor_number", "motor_2ren_pct")
        _, motor_3ren = dedup_mean("motor_number", "motor_3ren_pct")
        boat_sample, boat_2ren = dedup_mean("equipment_boat_number", "equipment_boat_2ren_pct")
        _, boat_3ren = dedup_mean("equipment_boat_number", "equipment_boat_3ren_pct")

        def entrant_mean(key: str) -> float | None:
            latest_by_reg: dict[int, float] = {}
            for record in records:
                reg_no = as_int(record.get("reg_no"))
                value = as_float(record.get(key))
                if reg_no is not None and value is not None:
                    latest_by_reg[reg_no] = value
            return statistics.fmean(latest_by_reg.values()) if latest_by_reg else None

        entrant_regs = {as_int(record.get("reg_no")) for record in records}
        entrant_regs.discard(None)
        con.execute(
            """
            INSERT OR REPLACE INTO official_venue_entry_baselines (
              date, place_id, motor_sample, motor_2ren_mean, motor_3ren_mean,
              equipment_boat_sample, equipment_boat_2ren_mean, equipment_boat_3ren_mean,
              entrant_sample, national_win_rate_mean, local_win_rate_mean, avg_st_mean, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                date_text, place_id, motor_sample, motor_2ren, motor_3ren,
                boat_sample, boat_2ren, boat_3ren, len(entrant_regs),
                entrant_mean("national_win_rate"), entrant_mean("local_win_rate"),
                entrant_mean("avg_st"), now_jst_text(),
            ),
        )
        updated += 1
    return updated


def refresh_racer_course_profiles(con: sqlite3.Connection, as_of_date: str) -> int:
    """Build national and local course profiles using only earlier results."""

    if not (_object_exists(con, "races") and _object_exists(con, "race_boats")):
        return 0
    target_pairs = {
        (int(row[0]), int(row[1]))
        for row in con.execute(
            """
            SELECT DISTINCT reg_no, place_id
            FROM official_racelist_boats
            WHERE date = ? AND reg_no IS NOT NULL
            """,
            (as_of_date,),
        )
    }
    if not target_pairs:
        return 0
    existing_national = {
        int(row[0])
        for row in con.execute(
            """
            SELECT DISTINCT reg_no FROM self_racer_course_profiles
            WHERE as_of_date = ? AND place_id = 0
            """,
            (as_of_date,),
        )
    }
    existing_local = {
        (int(row[0]), int(row[1]))
        for row in con.execute(
            """
            SELECT DISTINCT reg_no, place_id FROM self_racer_course_profiles
            WHERE as_of_date = ? AND place_id <> 0
            """,
            (as_of_date,),
        )
    }

    rows: list[tuple[Any, ...]] = []
    for reg_no in sorted({reg for reg, _place in target_pairs} - existing_national):
        before_count = len(rows)
        for course, starts, wins, top3s, avg_st, last_seen in con.execute(
            """
            SELECT
              CASE
                WHEN rb.result_start_sinnyu BETWEEN 1 AND 6 THEN rb.result_start_sinnyu
                ELSE rb.boat_number
              END AS course,
              COUNT(*) AS starts,
              SUM(CASE WHEN rb.finish_order = 1 THEN 1 ELSE 0 END) AS wins,
              SUM(CASE WHEN rb.finish_order <= 3 THEN 1 ELSE 0 END) AS top3s,
              AVG(rb.result_start_time) AS avg_start_time,
              MAX(r.date) AS last_seen_date
            FROM race_boats rb
            JOIN races r ON r.race_id = rb.race_id
            WHERE rb.reg_no = ? AND r.date < ?
              AND COALESCE(r.is_suspended, 0) = 0
              AND COALESCE(rb.is_absent, 0) = 0
              AND rb.finish_order BETWEEN 1 AND 6
            GROUP BY course
            """,
            (reg_no, as_of_date),
        ):
            rows.append((reg_no, 0, course, starts, wins, top3s, avg_st, last_seen))
        if len(rows) == before_count:
            rows.append((reg_no, 0, 0, 0, 0, 0, None, None))

    for reg_no, place_id in sorted(target_pairs - existing_local):
        before_count = len(rows)
        for course, starts, wins, top3s, avg_st, last_seen in con.execute(
            """
            SELECT
              CASE
                WHEN rb.result_start_sinnyu BETWEEN 1 AND 6 THEN rb.result_start_sinnyu
                ELSE rb.boat_number
              END AS course,
              COUNT(*) AS starts,
              SUM(CASE WHEN rb.finish_order = 1 THEN 1 ELSE 0 END) AS wins,
              SUM(CASE WHEN rb.finish_order <= 3 THEN 1 ELSE 0 END) AS top3s,
              AVG(rb.result_start_time) AS avg_start_time,
              MAX(r.date) AS last_seen_date
            FROM race_boats rb
            JOIN races r ON r.race_id = rb.race_id
            WHERE rb.reg_no = ? AND r.place_id = ? AND r.date < ?
              AND COALESCE(r.is_suspended, 0) = 0
              AND COALESCE(rb.is_absent, 0) = 0
              AND rb.finish_order BETWEEN 1 AND 6
            GROUP BY course
            """,
            (reg_no, place_id, as_of_date),
        ):
            rows.append((reg_no, place_id, course, starts, wins, top3s, avg_st, last_seen))
        if len(rows) == before_count:
            rows.append((reg_no, place_id, 0, 0, 0, 0, None, None))

    updated_at = now_jst_text()
    for reg_no, place_id, course, starts, wins, top3s, avg_st, last_seen in rows:
        con.execute(
            """
            INSERT OR REPLACE INTO self_racer_course_profiles (
              as_of_date, reg_no, place_id, course, starts, wins, top3s,
              win_pct, top3_pct, avg_start_time, last_seen_date, source, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                as_of_date, int(reg_no), int(place_id), int(course), int(starts), int(wins), int(top3s),
                float(wins) / float(starts) * 100.0 if starts else None,
                float(top3s) / float(starts) * 100.0 if starts else None,
                avg_st, last_seen, "local_result_history_pre_race", updated_at,
            ),
        )
    return len(rows)


def save_environment_snapshot(con: sqlite3.Connection, item: Mapping[str, Any]) -> str:
    date_text = str(item.get("date") or "")
    place_id = int(item.get("place_id") or item.get("venue_code") or 0)
    round_no = int(item.get("round") or item.get("race_no") or 0)
    if not date_text or not 1 <= place_id <= 24 or not 1 <= round_no <= 12:
        raise ValueError("environment row requires date, place_id(1..24), and round(1..12)")
    race_id = str(item.get("race_id") or race_id_for(date_text, place_id, round_no))
    source = str(item.get("source") or "external_environment")
    snapshot_at = str(item.get("snapshot_at") or item.get("observed_at") or now_jst_text())
    fields = (
        "race_id", "source", "snapshot_at", "date", "place_id", "round", "source_url",
        "observed_at", "prediction_at", "source_kind", "applicability", "quality",
        "station_code", "station_name", "station_distance_km",
        "tide_level_cm", "predicted_tide_cm", "predicted_tide_tp_cm",
        "observed_tide_cm", "observed_tide_tp_cm", "observed_age_minutes",
        "observed_lead_to_race_minutes", "observed_prediction_anomaly_cm",
        "tide_phase", "tide_delta_cm_per_hour", "daily_tide_min_cm",
        "daily_tide_max_cm", "daily_tide_range_cm", "tide_range_position",
        "previous_extreme_type", "previous_extreme_at", "previous_extreme_level_cm",
        "next_extreme_type", "next_extreme_at", "next_extreme_level_cm",
        "minutes_to_next_extreme", "current_speed_mps", "current_signed_knots",
        "current_region", "current_prediction_at", "current_quality",
        "current_direction_deg", "current_direction_text",
        "air_pressure_hpa", "precipitation_mm", "weather", "air_temp_c", "water_temp_c",
        "wind_speed_mps", "wind_direction_deg", "wave_height_cm",
        "collection_status", "error", "raw_json",
    )
    row = {
        **item,
        "race_id": race_id,
        "source": source,
        "snapshot_at": snapshot_at,
        "date": date_text,
        "place_id": place_id,
        "round": round_no,
        "raw_json": json.dumps(dict(item), ensure_ascii=False, sort_keys=True),
    }
    con.execute(
        f"INSERT OR REPLACE INTO race_environment_snapshots ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})",
        tuple(row.get(field) for field in fields),
    )
    return race_id


def import_environment_json(con: sqlite3.Connection, path: Path) -> int:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("rows"), list):
        rows = payload["rows"]
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = [payload]
    count = 0
    for item in rows:
        if not isinstance(item, dict):
            continue
        save_environment_snapshot(con, item)
        count += 1
    return count


def _market_snapshot(odds: Mapping[str, float]) -> dict[str, Any]:
    valid: dict[str, float] = {}
    for combo, value in odds.items():
        digits = "".join(ch for ch in str(combo) if ch.isdigit())
        odd = as_float(value)
        if len(digits) == 3 and len(set(digits)) == 3 and all(ch in "123456" for ch in digits) and odd and odd > 0:
            valid[digits] = odd
    raw_weights = {combo: 1.0 / odd for combo, odd in valid.items()}
    total = sum(raw_weights.values())
    probabilities = {combo: weight / total for combo, weight in raw_weights.items()} if total > 0 else {}
    win = {boat: 0.0 for boat in range(1, 7)}
    top3 = {boat: 0.0 for boat in range(1, 7)}
    min_head = {boat: None for boat in range(1, 7)}
    sorted_odds = sorted(valid.items(), key=lambda item: (item[1], item[0]))
    top10_head = {boat: 0 for boat in range(1, 7)}
    for combo, probability in probabilities.items():
        head = int(combo[0])
        win[head] += probability * 100.0
        for ch in combo:
            top3[int(ch)] += probability * 100.0
    for combo, odd in valid.items():
        head = int(combo[0])
        current = min_head[head]
        min_head[head] = odd if current is None else min(current, odd)
    for combo, _odd in sorted_odds[:10]:
        top10_head[int(combo[0])] += 1
    entropy = None
    if len(probabilities) > 1:
        entropy = -sum(p * math.log(p) for p in probabilities.values() if p > 0) / math.log(len(probabilities))
    favorite_combo, favorite_odds = sorted_odds[0] if sorted_odds else ("", None)
    return {
        "count": len(valid),
        "raw_inverse_sum": total,
        "win": win,
        "top3": top3,
        "min_head_odds": min_head,
        "top10_head_count": top10_head,
        "favorite_combo": favorite_combo,
        "favorite_odds": favorite_odds,
        "favorite_market_pct": probabilities.get(favorite_combo, 0.0) * 100.0 if favorite_combo else None,
        "entropy": entropy,
    }


def _deadline_for(con: sqlite3.Connection, race_id: str, date_text: str) -> datetime | None:
    if not _object_exists(con, "races"):
        return None
    row = con.execute("SELECT deadline_time FROM races WHERE race_id = ?", (race_id,)).fetchone()
    if not row or not row[0]:
        return None
    text = str(row[0])
    try:
        if re.fullmatch(r"\d{1,2}:\d{2}", text):
            return datetime.fromisoformat(f"{date_text}T{text}:00+09:00")
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=JST) if parsed.tzinfo is None else parsed.astimezone(JST)
    except ValueError:
        return None


def _snapshot_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=JST) if parsed.tzinfo is None else parsed.astimezone(JST)
    except (TypeError, ValueError):
        return None


def refresh_odds_features(con: sqlite3.Connection, odds_db: Path, date_text: str) -> dict[str, int]:
    if not odds_db.exists():
        return {"races": 0, "boats": 0}
    odds_con = sqlite3.connect(f"file:{odds_db}?mode=ro", uri=True)
    try:
        bounds = odds_con.execute(
            """
            SELECT venue_code, race_no, MIN(snapshot_at), MAX(snapshot_at), COUNT(DISTINCT snapshot_at)
            FROM odds_trifecta
            WHERE date = ?
            GROUP BY venue_code, race_no
            """,
            (date_text,),
        ).fetchall()
        grouped: dict[tuple[int, int], dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(dict))
        for venue_code, race_no, first_at, close_at, _count in bounds:
            rows = odds_con.execute(
                """
                SELECT snapshot_at, combo, odds
                FROM odds_trifecta
                WHERE date = ? AND venue_code = ? AND race_no = ?
                  AND snapshot_at IN (?, ?)
                """,
                (date_text, venue_code, race_no, first_at, close_at),
            ).fetchall()
            key = (int(venue_code), int(race_no))
            for snapshot_at, combo, odd in rows:
                grouped[key][str(snapshot_at)][str(combo)] = float(odd)
    finally:
        odds_con.close()

    con.execute("DELETE FROM official_odds_boat_features WHERE date = ?", (date_text,))
    con.execute("DELETE FROM official_odds_race_features WHERE date = ?", (date_text,))
    race_count = 0
    boat_count = 0
    bound_index = {(int(v), int(r)): (str(first), str(close), int(count)) for v, r, first, close, count in bounds}
    updated_at = now_jst_text()
    for (place_id, round_no), snapshots in grouped.items():
        first_at, close_at, snapshot_count = bound_index[(place_id, round_no)]
        first = _market_snapshot(snapshots.get(first_at, {}))
        close = _market_snapshot(snapshots.get(close_at, {}))
        race_id = race_id_for(date_text, place_id, round_no)
        deadline = _deadline_for(con, race_id, date_text)
        close_dt = _snapshot_datetime(close_at)
        minutes = (deadline - close_dt).total_seconds() / 60.0 if deadline and close_dt else None
        con.execute(
            """
            INSERT OR REPLACE INTO official_odds_race_features (
              race_id, date, place_id, round, source, snapshot_count, first_snapshot_at,
              close_snapshot_at, close_minutes_to_deadline, combo_count, favorite_combo,
              favorite_odds, favorite_market_pct, market_entropy, raw_inverse_sum, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                race_id, date_text, place_id, round_no, "official_trifecta_odds_history",
                snapshot_count, first_at, close_at, minutes, close["count"], close["favorite_combo"],
                close["favorite_odds"], close["favorite_market_pct"], close["entropy"],
                close["raw_inverse_sum"], updated_at,
            ),
        )
        race_count += 1
        for boat in range(1, 7):
            first_win = first["win"].get(boat)
            close_win = close["win"].get(boat)
            first_top3 = first["top3"].get(boat)
            close_top3 = close["top3"].get(boat)
            con.execute(
                """
                INSERT OR REPLACE INTO official_odds_boat_features (
                  race_id, date, place_id, round, boat_number, source,
                  first_market_win_pct, close_market_win_pct, market_win_move_pct,
                  first_market_top3_pct, close_market_top3_pct, market_top3_move_pct,
                  close_min_head_odds, close_top10_head_count, close_snapshot_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    race_id, date_text, place_id, round_no, boat, "official_trifecta_odds_history",
                    first_win, close_win, close_win - first_win if close_win is not None and first_win is not None else None,
                    first_top3, close_top3,
                    close_top3 - first_top3 if close_top3 is not None and first_top3 is not None else None,
                    close["min_head_odds"].get(boat), close["top10_head_count"].get(boat),
                    close_at, updated_at,
                ),
            )
            boat_count += 1
    _rebuild_feature_view(con)
    return {"races": race_count, "boats": boat_count}


def load_live_feature_rows(
    con: sqlite3.Connection,
    race: Mapping[str, Any],
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    race_id = str(race.get("race_id") or race_id_for(str(race["date"]), int(race["place_id"]), int(race["round"])))
    course_expr = "COALESCE(ob.official_before_start_sinnyu, entry.boat_number)" if _object_exists(con, "official_beforeinfo_boats") else "entry.boat_number"
    before_join = (
        "LEFT JOIN official_beforeinfo_boats ob ON ob.race_id=entry.race_id AND ob.boat_number=entry.boat_number"
        if _object_exists(con, "official_beforeinfo_boats")
        else ""
    )
    cursor = con.execute(
        f"""
        SELECT
          entry.*,
          entry.motor_2ren_pct - baseline.motor_2ren_mean AS motor_2ren_vs_venue,
          entry.motor_3ren_pct - baseline.motor_3ren_mean AS motor_3ren_vs_venue,
          entry.equipment_boat_2ren_pct - baseline.equipment_boat_2ren_mean AS equipment_boat_2ren_vs_venue,
          entry.equipment_boat_3ren_pct - baseline.equipment_boat_3ren_mean AS equipment_boat_3ren_vs_venue,
          lp.starts AS local_course_starts,
          lp.win_pct AS local_course_win_pct,
          lp.top3_pct AS local_course_top3_pct,
          lp.avg_start_time AS local_course_avg_start_time,
          np.starts AS national_course_starts,
          np.win_pct AS national_course_win_pct,
          np.top3_pct AS national_course_top3_pct,
          np.avg_start_time AS national_course_avg_start_time,
          lt.starts AS local_total_starts,
          lt.win_pct AS local_total_win_pct,
          lt.top3_pct AS local_total_top3_pct,
          lt.avg_start_time AS local_total_avg_start_time,
          nt.starts AS national_total_starts,
          nt.win_pct AS national_total_win_pct,
          nt.top3_pct AS national_total_top3_pct,
          nt.avg_start_time AS national_total_avg_start_time,
          odds.first_market_win_pct,
          odds.close_market_win_pct,
          odds.market_win_move_pct,
          odds.first_market_top3_pct,
          odds.close_market_top3_pct,
          odds.market_top3_move_pct,
          odds.close_min_head_odds,
          odds.close_top10_head_count
        FROM official_racelist_boats entry
        LEFT JOIN official_venue_entry_baselines baseline
          ON baseline.date=entry.date AND baseline.place_id=entry.place_id
        {before_join}
        LEFT JOIN self_racer_course_profiles lp
          ON lp.as_of_date=entry.date AND lp.reg_no=entry.reg_no
         AND lp.place_id=entry.place_id AND lp.course={course_expr}
        LEFT JOIN self_racer_course_profiles np
          ON np.as_of_date=entry.date AND np.reg_no=entry.reg_no
         AND np.place_id=0 AND np.course={course_expr}
        LEFT JOIN (
          SELECT
            as_of_date,
            reg_no,
            place_id,
            SUM(starts) AS starts,
            100.0 * SUM(wins) / NULLIF(SUM(starts), 0) AS win_pct,
            100.0 * SUM(top3s) / NULLIF(SUM(starts), 0) AS top3_pct,
            SUM(avg_start_time * starts)
              / NULLIF(SUM(CASE WHEN avg_start_time IS NOT NULL THEN starts ELSE 0 END), 0)
              AS avg_start_time
          FROM self_racer_course_profiles
          WHERE place_id <> 0
          GROUP BY as_of_date, reg_no, place_id
        ) lt
          ON lt.as_of_date=entry.date AND lt.reg_no=entry.reg_no
         AND lt.place_id=entry.place_id
        LEFT JOIN (
          SELECT
            as_of_date,
            reg_no,
            SUM(starts) AS starts,
            100.0 * SUM(wins) / NULLIF(SUM(starts), 0) AS win_pct,
            100.0 * SUM(top3s) / NULLIF(SUM(starts), 0) AS top3_pct,
            SUM(avg_start_time * starts)
              / NULLIF(SUM(CASE WHEN avg_start_time IS NOT NULL THEN starts ELSE 0 END), 0)
              AS avg_start_time
          FROM self_racer_course_profiles
          WHERE place_id = 0
          GROUP BY as_of_date, reg_no
        ) nt
          ON nt.as_of_date=entry.date AND nt.reg_no=entry.reg_no
        LEFT JOIN official_odds_boat_features odds
          ON odds.race_id=entry.race_id AND odds.boat_number=entry.boat_number
        WHERE entry.race_id=?
        ORDER BY entry.boat_number
        """,
        (race_id,),
    )
    names = [item[0] for item in cursor.description]
    rows = cursor.fetchall()
    by_boat = {int(row[names.index("boat_number")]): dict(zip(names, row)) for row in rows}
    env_row = con.execute(
        "SELECT * FROM v_latest_race_environment WHERE race_id = ?",
        (race_id,),
    ).fetchone()
    env = {}
    if env_row:
        env_names = [item[0] for item in con.execute("SELECT * FROM v_latest_race_environment LIMIT 0").description]
        env = dict(zip(env_names, env_row))
    return by_boat, env


def coverage_report(con: sqlite3.Connection, date_text: str) -> dict[str, Any]:
    def scalar(sql: str, params: tuple[Any, ...] = ()) -> int:
        row = con.execute(sql, params).fetchone()
        return int(row[0] or 0) if row else 0

    return {
        "date": date_text,
        "racelist_races": scalar("SELECT COUNT(*) FROM official_racelist_races WHERE date=? AND available=1", (date_text,)),
        "racelist_boats": scalar("SELECT COUNT(*) FROM official_racelist_boats WHERE date=?", (date_text,)),
        "venue_baselines": scalar("SELECT COUNT(*) FROM official_venue_entry_baselines WHERE date=?", (date_text,)),
        "racer_course_profiles": scalar("SELECT COUNT(*) FROM self_racer_course_profiles WHERE as_of_date=?", (date_text,)),
        "odds_feature_races": scalar("SELECT COUNT(*) FROM official_odds_race_features WHERE date=?", (date_text,)),
        "odds_feature_boats": scalar("SELECT COUNT(*) FROM official_odds_boat_features WHERE date=?", (date_text,)),
        "environment_snapshots": scalar("SELECT COUNT(*) FROM race_environment_snapshots WHERE date=?", (date_text,)),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    env = sub.add_parser("import-environment")
    env.add_argument("json_path")
    derived = sub.add_parser("refresh-derived")
    derived.add_argument("--date", required=True)
    derived.add_argument("--odds-db", default="")
    coverage = sub.add_parser("coverage")
    coverage.add_argument("--date", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path, timeout=30)
    try:
        init_schema(con)
        result: dict[str, Any] = {"command": args.command, "db": str(db_path)}
        if args.command == "import-environment":
            result["imported"] = import_environment_json(con, Path(args.json_path))
        elif args.command == "refresh-derived":
            result["venue_baselines"] = refresh_venue_entry_baselines(con, args.date)
            result["racer_course_profiles"] = refresh_racer_course_profiles(con, args.date)
            if args.odds_db:
                result["odds"] = refresh_odds_features(con, Path(args.odds_db), args.date)
        elif args.command == "coverage":
            result.update(coverage_report(con, args.date))
        con.commit()
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
