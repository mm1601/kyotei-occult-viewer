#!/usr/bin/env python3
"""Fetch and store BOATCAST original exhibition timing data.

BOATCAST is operated by the BOAT RACE Promotion Association. Its race page
loads venue-specific original exhibition values from a small tab-separated
text resource. This module reads that resource conservatively and keeps the
data separate from archived BOATERS values.
"""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import ssl
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "output"
JST = ZoneInfo("Asia/Tokyo")
PARSER_VERSION = 1
SOURCE = "official_boatcast_original_exhibition"
URL_TEMPLATE = (
    "https://race.boatcast.jp/txt/{jcd}/"
    "bc_oriten_{ymd}_{jcd}_{round_no}.txt"
)

FIELD_ALIASES = {
    "一周": "isshu_time",
    "1周": "isshu_time",
    "周回": "isshu_time",
    "半周": "hanshu_time",
    "まわり足": "mawariashi_time",
    "周り足": "mawariashi_time",
    "回り足": "mawariashi_time",
    "旋回": "mawariashi_time",
    "直線": "chokusen_time",
}
TIMING_FIELDS = (
    "isshu_time",
    "hanshu_time",
    "mawariashi_time",
    "chokusen_time",
)


def now_jst() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def as_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def as_float(value: Any) -> float | None:
    text = str(value or "").strip().replace(",", ".")
    if not text or text in {"-", "--", "―", "ー"}:
        return None
    match = re.search(r"\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        number = float(match.group(0))
    except ValueError:
        return None
    if not math.isfinite(number) or not 0 < number < 180:
        return None
    return number


def normalize_header(value: Any) -> str:
    return re.sub(r"[\s\u3000]+", "", str(value or "")).strip()


def header_field(value: Any) -> str | None:
    normalized = normalize_header(value)
    if normalized in FIELD_ALIASES:
        return FIELD_ALIASES[normalized]
    for label, field in FIELD_ALIASES.items():
        if label in normalized:
            return field
    return None


def venue_code(race: Mapping[str, Any]) -> str:
    for key in ("place_id", "venue_code", "jcd"):
        number = as_int(race.get(key))
        if 1 <= number <= 24:
            return f"{number:02d}"
    race_id = re.sub(r"\D", "", str(race.get("race_id") or ""))
    if len(race_id) >= 12:
        number = as_int(race_id[-4:-2])
        if 1 <= number <= 24:
            return f"{number:02d}"
    return ""


def cache_key(race: Mapping[str, Any]) -> str:
    date_text = str(race.get("date") or "")
    jcd = venue_code(race)
    round_no = as_int(race.get("round") or race.get("round_no"))
    if not date_text or not jcd or not 1 <= round_no <= 12:
        return ""
    return f"{date_text}:{jcd}:{round_no}"


def original_exhibition_url(race: Mapping[str, Any]) -> str:
    date_text = str(race.get("date") or "")
    jcd = venue_code(race)
    round_no = as_int(race.get("round") or race.get("round_no"))
    if not date_text or not jcd or not 1 <= round_no <= 12:
        return ""
    return URL_TEMPLATE.format(
        ymd=date_text.replace("-", ""),
        jcd=jcd,
        round_no=round_no,
    )


def cache_path(date_text: str, output_dir: Path = OUT_DIR) -> Path:
    return output_dir / f"official_boatcast_original_{date_text.replace('-', '')}.json"


def _rank_boats(boats: dict[str, dict[str, Any]]) -> None:
    for field in TIMING_FIELDS:
        values = [
            (as_int(boat), data.get(field))
            for boat, data in boats.items()
            if data.get(field) is not None
        ]
        values.sort(key=lambda item: (item[1], item[0]))
        rank = 0
        previous = None
        for index, (boat, value) in enumerate(values, start=1):
            if previous is None or value != previous:
                rank = index
            boats[str(boat)][f"{field}_rank"] = rank
            previous = value


def parse_original_exhibition(text: str) -> dict[str, Any]:
    payload_text = str(text or "").lstrip("\ufeff")
    if payload_text.startswith("data="):
        payload_text = payload_text[5:]
    lines = [line.rstrip("\r") for line in payload_text.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)

    if not lines:
        return {
            "source": SOURCE,
            "status": "pending",
            "status_code": 0,
            "available": False,
            "headers": [],
            "boats": {},
        }

    status_parts = [part.strip() for part in lines[0].split("\t")]
    status_code = as_int(status_parts[0] if status_parts else 0)
    metric_count = as_int(status_parts[1] if len(status_parts) > 1 else 0)
    if status_code != 1:
        return {
            "source": SOURCE,
            "status": "not_measured" if status_code == 2 else "pending",
            "status_code": status_code,
            "available": False,
            "metric_count": metric_count,
            "headers": [],
            "boats": {},
        }

    headers = [part.strip() for part in (lines[1].split("\t") if len(lines) > 1 else [])]
    if metric_count > 0:
        headers = headers[:metric_count]
    fields = [header_field(header) for header in headers]
    boats: dict[str, dict[str, Any]] = {}
    for line in lines[2:]:
        cells = [part.strip() for part in line.split("\t")]
        if len(cells) < 2:
            continue
        boat = as_int(cells[0])
        if not 1 <= boat <= 6:
            continue
        data: dict[str, Any] = {
            "boat_number": boat,
            "racer_name": cells[1],
            "raw_metrics": {},
        }
        for index, header in enumerate(headers):
            raw_value = cells[index + 2] if index + 2 < len(cells) else ""
            data["raw_metrics"][header] = raw_value
            field = fields[index]
            if field:
                data[field] = as_float(raw_value)
        boats[str(boat)] = data

    _rank_boats(boats)
    counts = {
        f"{field.removesuffix('_time')}_boats": sum(
            1 for data in boats.values() if data.get(field) is not None
        )
        for field in TIMING_FIELDS
    }
    recognized = [field for field in fields if field]
    status = "available" if recognized else "unsupported"
    return {
        "source": SOURCE,
        "status": status,
        "status_code": status_code,
        "available": status == "available",
        "metric_count": metric_count or len(headers),
        "headers": headers,
        "recognized_fields": recognized,
        "boat_rows": len(boats),
        "boats": boats,
        **counts,
    }


def _load_cache(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    payload.setdefault("version", "official-boatcast-original-v1")
    payload.setdefault("races", {})
    return payload


def _save_cache(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _cache_is_recent(cached: Mapping[str, Any], retry_seconds: int) -> bool:
    if retry_seconds <= 0 or not cached.get("fetched_at"):
        return False
    try:
        fetched_at = datetime.fromisoformat(str(cached["fetched_at"]))
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=JST)
        age = datetime.now(JST) - fetched_at.astimezone(JST)
    except (TypeError, ValueError):
        return False
    return age.total_seconds() < retry_seconds


def fetch_original_exhibition(
    race: Mapping[str, Any],
    *,
    refresh: bool = False,
    retry_seconds: int = 240,
    output_dir: Path = OUT_DIR,
) -> dict[str, Any]:
    date_text = str(race.get("date") or "")
    key = cache_key(race)
    url = original_exhibition_url(race)
    if not date_text or not key or not url:
        return {
            "source": SOURCE,
            "status": "invalid_race",
            "available": False,
            "error": "BOATCAST original exhibition URL could not be built",
        }

    path = cache_path(date_text, output_dir)
    cache = _load_cache(path)
    cached = cache["races"].get(key)
    if isinstance(cached, dict) and not refresh:
        terminal = cached.get("status") in {"available", "not_measured", "unsupported"}
        parser_ready = as_int(cached.get("parser_version")) >= PARSER_VERSION
        if terminal and parser_ready:
            return cached
        if parser_ready and _cache_is_recent(cached, retry_seconds):
            return cached

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept-Language": "ja,en-US;q=0.8,en;q=0.6",
        },
    )
    try:
        with urllib.request.urlopen(
            request,
            timeout=20,
            context=ssl._create_unverified_context(),
        ) as response:
            text = response.read().decode("utf-8", errors="replace")
        parsed = parse_original_exhibition(text)
        error = ""
    except Exception as exc:
        parsed = {
            "source": SOURCE,
            "status": "fetch_error",
            "available": False,
            "headers": [],
            "boats": {},
        }
        error = str(exc)[-300:]

    fetched_at = now_jst()
    parsed.update(
        {
            "url": url,
            "key": key,
            "parser_version": PARSER_VERSION,
            "fetched_at": fetched_at,
            "error": error,
        }
    )
    cache["date"] = date_text
    cache["updated_at"] = fetched_at
    cache["races"][key] = parsed
    _save_cache(path, cache)
    return parsed


def init_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS official_original_exhibition_races (
          race_id TEXT PRIMARY KEY,
          date TEXT,
          place_id INTEGER,
          place_name TEXT,
          round INTEGER,
          source TEXT,
          status TEXT,
          status_code INTEGER,
          available INTEGER,
          expected_boats INTEGER,
          boat_rows INTEGER,
          isshu_boats INTEGER,
          hanshu_boats INTEGER,
          mawariashi_boats INTEGER,
          chokusen_boats INTEGER,
          headers_json TEXT,
          url TEXT,
          parser_version INTEGER,
          fetched_at TEXT,
          error TEXT
        );

        CREATE TABLE IF NOT EXISTS official_original_exhibition_boats (
          race_id TEXT NOT NULL,
          date TEXT,
          place_id INTEGER,
          round INTEGER,
          boat_number INTEGER NOT NULL,
          racer_name TEXT,
          isshu_time REAL,
          hanshu_time REAL,
          mawariashi_time REAL,
          chokusen_time REAL,
          raw_metrics_json TEXT,
          source TEXT,
          PRIMARY KEY (race_id, boat_number)
        );

        CREATE INDEX IF NOT EXISTS idx_official_original_races_date
          ON official_original_exhibition_races(date, place_id, round);
        CREATE INDEX IF NOT EXISTS idx_official_original_boats_date
          ON official_original_exhibition_boats(date, place_id, round, boat_number);
        """
    )


def save_to_db(
    con: sqlite3.Connection,
    race: Mapping[str, Any],
    payload: Mapping[str, Any],
    *,
    expected_boats: int = 6,
) -> None:
    race_id = str(race.get("race_id") or "")
    if not race_id:
        raise ValueError("race_id is required to save original exhibition data")
    counts = {
        name: as_int(payload.get(name))
        for name in ("isshu_boats", "hanshu_boats", "mawariashi_boats", "chokusen_boats")
    }
    con.execute(
        """
        INSERT OR REPLACE INTO official_original_exhibition_races (
          race_id, date, place_id, place_name, round, source, status,
          status_code, available, expected_boats, boat_rows, isshu_boats,
          hanshu_boats, mawariashi_boats, chokusen_boats, headers_json,
          url, parser_version, fetched_at, error
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            race_id,
            race.get("date"),
            as_int(race.get("place_id")),
            race.get("place_name"),
            as_int(race.get("round")),
            payload.get("source") or SOURCE,
            payload.get("status") or "pending",
            as_int(payload.get("status_code")),
            1 if payload.get("available") else 0,
            max(1, as_int(expected_boats) or 6),
            as_int(payload.get("boat_rows")),
            counts["isshu_boats"],
            counts["hanshu_boats"],
            counts["mawariashi_boats"],
            counts["chokusen_boats"],
            json.dumps(payload.get("headers") or [], ensure_ascii=False),
            payload.get("url") or original_exhibition_url(race),
            as_int(payload.get("parser_version")),
            payload.get("fetched_at") or now_jst(),
            payload.get("error") or "",
        ),
    )
    con.execute(
        "DELETE FROM official_original_exhibition_boats WHERE race_id = ?",
        (race_id,),
    )
    boats = payload.get("boats") if isinstance(payload.get("boats"), dict) else {}
    for boat_number in range(1, 7):
        data = boats.get(str(boat_number)) or {}
        if not data:
            continue
        con.execute(
            """
            INSERT OR REPLACE INTO official_original_exhibition_boats (
              race_id, date, place_id, round, boat_number, racer_name,
              isshu_time, hanshu_time, mawariashi_time, chokusen_time,
              raw_metrics_json, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                race_id,
                race.get("date"),
                as_int(race.get("place_id")),
                as_int(race.get("round")),
                boat_number,
                data.get("racer_name") or "",
                data.get("isshu_time"),
                data.get("hanshu_time"),
                data.get("mawariashi_time"),
                data.get("chokusen_time"),
                json.dumps(data.get("raw_metrics") or {}, ensure_ascii=False),
                payload.get("source") or SOURCE,
            ),
        )


def usable_fields(payload: Mapping[str, Any], expected_boats: int) -> list[str]:
    expected = max(1, as_int(expected_boats) or 6)
    fields = []
    for field in TIMING_FIELDS:
        count_key = f"{field.removesuffix('_time')}_boats"
        if as_int(payload.get(count_key)) >= expected:
            fields.append(field)
    return fields

