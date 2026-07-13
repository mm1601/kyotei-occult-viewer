#!/usr/bin/env python3
"""Validate and load BOATERS values captured from user-provided screenshots."""

from __future__ import annotations

import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCREENSHOT_ROOT = ROOT / "data" / "input" / "boaters_screenshots"
DEFAULT_APPROVED_DIR = SCREENSHOT_ROOT / "approved"

VENUES = (
    "桐生",
    "戸田",
    "江戸川",
    "平和島",
    "多摩川",
    "浜名湖",
    "蒲郡",
    "常滑",
    "津",
    "三国",
    "びわこ",
    "住之江",
    "尼崎",
    "鳴門",
    "丸亀",
    "児島",
    "宮島",
    "徳山",
    "下関",
    "若松",
    "芦屋",
    "福岡",
    "唐津",
    "大村",
)

PLACE_ALIASES = {
    "琵琶湖": "びわこ",
    "びわ湖": "びわこ",
    "BOAT RACEびわこ": "びわこ",
    "ボートレースびわこ": "びわこ",
}

PER_BOAT_REQUIRED_FIELDS = (
    "ai_prediction_pct",
    "ai_3ren_pct",
    "odds_prediction_pct",
    "general_3ren_pct",
)
OPTIONAL_PER_BOAT_FIELDS = (
    "st_rank_general",
    "tenji_time",
    "start_tenji_time",
    "isshu_time",
    "hanshu_time",
    "chokusen_time",
    "mawariashi_time",
)
BOAT1_FIELDS = ("nige_pct", "sasare_pct", "makurare_pct")
HALF_LAP_PLACE_NAMES = {"桐生", "江戸川"}
BOATERS_TENJI_ONLY_VENUES = {"江戸川", "津"}
EXHIBITION_FIELDS = (
    "tenji_time",
    "start_tenji_time",
    "isshu_time",
    "hanshu_time",
    "chokusen_time",
    "mawariashi_time",
)
PERCENT_FIELDS = {
    "ai_prediction_pct",
    "ai_3ren_pct",
    "odds_prediction_pct",
    "general_3ren_pct",
    "nige_pct",
    "sasare_pct",
    "makurare_pct",
}


def number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        result = float(str(value).replace("％", "").replace("%", "").replace(",", "."))
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def normalize_date(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) != 8:
        return ""
    try:
        return datetime.strptime(digits, "%Y%m%d").date().isoformat()
    except ValueError:
        return ""


def normalize_place(value: Any) -> str:
    text = re.sub(r"\s+", "", str(value or "")).strip()
    text = text.replace("BOATRACE", "").replace("ボートレース", "")
    text = PLACE_ALIASES.get(text, text)
    return text if text in VENUES else ""


def normalize_round(value: Any) -> int:
    match = re.search(r"(\d{1,2})", str(value or ""))
    if not match:
        return 0
    result = int(match.group(1))
    return result if 1 <= result <= 12 else 0


def canonical_boats(value: Any) -> dict[int, dict[str, float]]:
    result: dict[int, dict[str, float]] = {}
    if isinstance(value, list):
        iterable = ((row.get("boat_number"), row) for row in value if isinstance(row, dict))
    elif isinstance(value, dict):
        iterable = value.items()
    else:
        iterable = ()
    for raw_boat, raw_row in iterable:
        boat = normalize_round(raw_boat)
        if boat not in range(1, 7) or not isinstance(raw_row, dict):
            continue
        row: dict[str, float] = {}
        for field in PER_BOAT_REQUIRED_FIELDS + OPTIONAL_PER_BOAT_FIELDS + BOAT1_FIELDS:
            parsed = number(raw_row.get(field))
            if parsed is not None:
                row[field] = parsed
        result[boat] = row
    return result


def _sum_check(
    boats: dict[int, dict[str, float]],
    field: str,
    low: float,
    high: float,
    errors: list[str],
) -> float | None:
    values = [number(boats.get(boat, {}).get(field)) for boat in range(1, 7)]
    if any(value is None for value in values):
        return None
    total = float(sum(value for value in values if value is not None))
    if not low <= total <= high:
        errors.append(f"{field}_sum_out_of_range:{total:.2f}")
    return round(total, 4)


def validate_payload(payload: dict[str, Any], *, strict_sums: bool = True) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    date_text = normalize_date(payload.get("date"))
    place_name = normalize_place(payload.get("place_name") or payload.get("venue"))
    round_no = normalize_round(payload.get("round"))
    if not date_text:
        errors.append("date_missing_or_invalid")
    if not place_name:
        errors.append("place_missing_or_invalid")
    if not round_no:
        errors.append("round_missing_or_invalid")

    boats = canonical_boats(payload.get("boats"))
    if set(boats) != set(range(1, 7)):
        errors.append("six_boats_required")
    missing: list[str] = []
    for boat in range(1, 7):
        row = boats.get(boat, {})
        for field in PER_BOAT_REQUIRED_FIELDS:
            if number(row.get(field)) is None:
                missing.append(f"boat{boat}.{field}")
        for field, value in row.items():
            if field in PERCENT_FIELDS and not 0.0 <= value <= 100.0:
                errors.append(f"boat{boat}.{field}_out_of_range:{value}")
    if number(boats.get(1, {}).get("nige_pct")) is None:
        missing.append("boat1.nige_pct")
    if missing:
        errors.append("required_values_missing")

    def complete(field: str) -> bool:
        return all(number(boats.get(boat, {}).get(field)) is not None for boat in range(1, 7))

    lap_field = "hanshu_time" if place_name in HALF_LAP_PLACE_NAMES else "isshu_time"
    tenji_complete = complete("tenji_time")
    lap_complete = complete(lap_field)
    chokusen_complete = complete("chokusen_time")
    mawariashi_complete = complete("mawariashi_time")
    start_complete = complete("start_tenji_time")
    tenji_only_allowed = place_name in BOATERS_TENJI_ONLY_VENUES
    boaters_original_exhibition_ready = (
        lap_complete and chokusen_complete and mawariashi_complete
    )
    boaters_exhibition_ready = tenji_complete and (
        boaters_original_exhibition_ready or tenji_only_allowed
    )
    boaters_exhibition_mode = (
        "full"
        if tenji_complete and boaters_original_exhibition_ready
        else "tenji_only"
        if tenji_complete and tenji_only_allowed
        else "missing"
    )
    if not boaters_exhibition_ready:
        warnings.append(f"boaters_exhibition_incomplete:{lap_field}")
    if not boaters_original_exhibition_ready:
        warnings.append("boaters_original_exhibition_incomplete")
    if not start_complete:
        warnings.append("boaters_start_exhibition_incomplete")

    sum_ranges = {
        "ai_prediction_pct": (95.0, 105.0) if strict_sums else (85.0, 115.0),
        "odds_prediction_pct": (95.0, 105.0) if strict_sums else (85.0, 115.0),
        "ai_3ren_pct": (280.0, 320.0) if strict_sums else (250.0, 350.0),
    }
    sums = {
        field: _sum_check(boats, field, low, high, errors)
        for field, (low, high) in sum_ranges.items()
    }
    if any(number(boats.get(boat, {}).get("st_rank_general")) is None for boat in range(1, 7)):
        warnings.append("st_rank_general_incomplete")
    if number(boats.get(1, {}).get("sasare_pct")) is None:
        warnings.append("boat1_sasare_pct_missing")
    if number(boats.get(1, {}).get("makurare_pct")) is None:
        warnings.append("boat1_makurare_pct_missing")
    return {
        "ok": not errors,
        "original_boaters_ready": not errors,
        "boaters_ai_ready": not errors,
        "boaters_exhibition_ready": boaters_exhibition_ready,
        "boaters_exhibition_mode": boaters_exhibition_mode,
        "boaters_original_exhibition_ready": boaters_original_exhibition_ready,
        "boaters_start_exhibition_ready": start_complete,
        "boaters_lap_field": lap_field,
        "date": date_text,
        "place_name": place_name,
        "round": round_no,
        "boats": boats,
        "missing": missing,
        "errors": errors,
        "warnings": warnings,
        "sums": sums,
    }


def approved_filename(date_text: str, place_name: str, round_no: int) -> str:
    return f"{date_text.replace('-', '')}_{place_name}_{round_no:02d}R.json"


def normalized_payload(payload: dict[str, Any], *, strict_sums: bool = True) -> dict[str, Any]:
    validation = validate_payload(payload, strict_sums=strict_sums)
    result = dict(payload)
    result["version"] = "boaters-user-screenshot-v1"
    result["date"] = validation["date"]
    result["place_name"] = validation["place_name"]
    result["round"] = validation["round"]
    result["boats"] = {str(boat): row for boat, row in validation["boats"].items()}
    result["validation"] = {key: value for key, value in validation.items() if key != "boats"}
    return result


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def load_approved_for_race(
    race: dict[str, Any], approved_dir: Path | str = DEFAULT_APPROVED_DIR
) -> tuple[dict[str, Any] | None, str]:
    directory = Path(approved_dir).expanduser()
    date_text = normalize_date(race.get("date"))
    place_name = normalize_place(race.get("place_name"))
    round_no = normalize_round(race.get("round"))
    if not (date_text and place_name and round_no):
        return None, "race_identity_invalid"
    direct = directory / date_text.replace("-", "") / approved_filename(date_text, place_name, round_no)
    candidates = [direct]
    flat = directory / approved_filename(date_text, place_name, round_no)
    if flat != direct:
        candidates.append(flat)
    if not any(path.exists() for path in candidates) and directory.exists():
        candidates.extend(directory.rglob("*.json"))

    matches: list[tuple[str, Path, dict[str, Any]]] = []
    seen: set[Path] = set()
    for path in candidates:
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        payload = _read_json(path)
        if not payload or payload.get("status") != "approved":
            continue
        validation = validate_payload(payload, strict_sums=True)
        if not validation["ok"]:
            continue
        if (
            validation["date"] != date_text
            or validation["place_name"] != place_name
            or validation["round"] != round_no
        ):
            continue
        normalized = normalized_payload(payload, strict_sums=True)
        imported_at = str(normalized.get("approved_at") or normalized.get("imported_at") or "")
        matches.append((imported_at, path, normalized))
    if not matches:
        return None, "approved_screenshot_not_found"
    matches.sort(key=lambda item: (item[0], str(item[1])), reverse=True)
    _, path, payload = matches[0]
    payload["_approved_path"] = str(path)
    return payload, ""


def apply_approved_to_by_boat(
    by_boat: dict[int, dict[str, Any]],
    race: dict[str, Any],
    approved_dir: Path | str = DEFAULT_APPROVED_DIR,
) -> dict[str, Any] | None:
    payload, reason = load_approved_for_race(race, approved_dir)
    if payload is None:
        return None
    boats = canonical_boats(payload.get("boats"))
    fields = PER_BOAT_REQUIRED_FIELDS + OPTIONAL_PER_BOAT_FIELDS + BOAT1_FIELDS
    applied = 0
    exhibition_applied = 0
    for boat in range(1, 7):
        source = boats.get(boat, {})
        target = by_boat.setdefault(boat, {})
        for field in fields:
            if field in source:
                target[field] = source[field]
        target["ai_source"] = "original_boaters_user_screenshot"
        target["boaters_screenshot_source"] = payload.get("_approved_path") or ""
        if any(field in source for field in EXHIBITION_FIELDS):
            target["exhibition_source"] = "original_boaters_user_screenshot"
            target["original_exhibition_source"] = "original_boaters_user_screenshot"
            exhibition_applied += 1
        applied += 1
    validation = payload.get("validation", {})
    return {
        "source": "original_boaters_user_screenshot",
        "available": applied == 6,
        "original_boaters_ready": bool(validation.get("original_boaters_ready")),
        "boaters_ai_ready": bool(validation.get("boaters_ai_ready")),
        "boaters_exhibition_ready": bool(validation.get("boaters_exhibition_ready")),
        "boaters_exhibition_mode": validation.get("boaters_exhibition_mode") or "missing",
        "boaters_original_exhibition_ready": bool(
            validation.get("boaters_original_exhibition_ready")
        ),
        "boaters_start_exhibition_ready": bool(
            validation.get("boaters_start_exhibition_ready")
        ),
        "boats_applied": applied,
        "exhibition_boats_applied": exhibition_applied,
        "approved_path": payload.get("_approved_path"),
        "source_images": payload.get("source_images") or [],
        "validation": validation,
        "reason": reason,
    }
