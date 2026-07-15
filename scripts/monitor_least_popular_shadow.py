#!/usr/bin/env python3
"""Forward-only shadow monitor for two least-popular-boat trifecta rules.

The monitor captures an immutable pre-deadline snapshot, settles the virtual
tickets after the result is available, and evaluates adoption only at fixed
checkpoints.  Historical rows are never mixed into the adoption decision.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import functools
import hashlib
import html
import json
import math
import os
import platform
import random
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = ROOT / "data" / "output" / "least_popular_shadow"
DEFAULT_DB = ROOT / "data" / "output" / "boaters_all_races.sqlite"
PRODUCTION_DB = Path("/tmp/least-popular-schedule-primary.sqlite")
PRODUCTION_VERIFICATION_DB = Path("/tmp/least-popular-schedule-verification.sqlite")
PRODUCTION_OFFICIAL_ROOT = Path("/tmp/least-popular-official")
FORWARD_START_DATE = "2026-07-16"
RULE_VERSION = "least-popular-shadow-v1-20260715"
STAKE_PER_TICKET_YEN = 100
MINIMUM_CAPTURE_LEAD_MINUTES = 5.0
CAPTURE_LOOKAHEAD_MINUTES = 22.0
SETTLEMENT_OVERDUE_HOURS = 3.0
BOOTSTRAP_SAMPLES = 20_000
PRODUCTION_PYTHON_VERSION = "3.12.4"
JST = ZoneInfo("Asia/Tokyo")

PROTOCOL = {
    "protocol_id": "least-popular-prospective-v1-20260716",
    "rule_version": RULE_VERSION,
    "forward_start_date": FORWARD_START_DATE,
    "capture_policy": "first_complete_snapshot_in_fixed_window",
    "capture_window_minutes_before_deadline": [MINIMUM_CAPTURE_LEAD_MINUTES, CAPTURE_LOOKAHEAD_MINUTES],
    "stake_per_ticket_yen": STAKE_PER_TICKET_YEN,
    "market_definition": "unique_minimum_boaters_odds_prediction_pct",
    "scheduled_scan_cadence_minutes": 5,
    "settlement_overdue_hours": SETTLEMENT_OVERDUE_HOURS,
    "bootstrap_samples": BOOTSTRAP_SAMPLES,
    "production_python_version": PRODUCTION_PYTHON_VERSION,
    "independent_schedule_acquisitions": 2,
    "production_schedule_db": str(PRODUCTION_DB),
    "production_verification_db": str(PRODUCTION_VERIFICATION_DB),
    "official_venue_source": "boatrace.jp_daily_index",
}
PROTOCOL_SHA256 = hashlib.sha256(
    json.dumps(PROTOCOL, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()

STRATEGIES = {
    "c1_target_first_two": {
        "label": "最下位艇1着固定2点",
        "tickets_label": "T-1-2 / T-1-3",
        "first_checkpoint_races": 500,
        "checkpoint_increment_races": 250,
        "minimum_hits": 10,
        "minimum_hit_rate_pct": 1.3,
        "minimum_half_hits": 3,
        "segment_lane": 6,
        "minimum_segment_hits": 6,
        "maximum_current_misses": 250,
        "maximum_drawdown_yen": 50_000,
        "increment_minimum_hits": 2,
        "historical_reference": {
            "all_races": 894,
            "all_roi_pct": 254.89,
            "2025_plus_races": 535,
            "2025_plus_roi_pct": 235.62,
        },
    },
    "c4_target_second_three": {
        "label": "最下位艇2着固定・他艇2艇以上弱化3点",
        "tickets_label": "A-T-B / A-T-C / A-T-D",
        "first_checkpoint_races": 120,
        "checkpoint_increment_races": 60,
        "minimum_hits": 12,
        "minimum_hit_rate_pct": 8.0,
        "minimum_half_hits": 4,
        "segment_lane": 5,
        "minimum_segment_hits": 8,
        "maximum_current_misses": 40,
        "maximum_drawdown_yen": 12_000,
        "increment_minimum_hits": 3,
        "historical_reference": {
            "all_races": 148,
            "all_roi_pct": 371.24,
            "2025_plus_races": 92,
            "2025_plus_roi_pct": 365.80,
        },
    },
}

COMMON_GATES = {
    "minimum_roi_pct": 200.0,
    "minimum_bootstrap_lower_pct": 100.0,
    "minimum_roi_without_top_hit_pct": 150.0,
    "maximum_top_hit_share_pct": 40.0,
    "maximum_top3_hit_share_pct": 70.0,
    "minimum_half_roi_pct": 100.0,
    "minimum_hit_quarters": 4,
    "minimum_capture_integrity_pct": 99.0,
    "minimum_segment_roi_pct": 150.0,
    "minimum_increment_roi_pct": 100.0,
}


class IncompleteRaceData(ValueError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="JST date. Defaults to today.")
    parser.add_argument("--now", help="Override current JST timestamp for tests.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--verification-db", type=Path, help="Independent schedule index used to verify the full venue/race manifest.")
    parser.add_argument("--official-index-html", type=Path, help="Official BOATRACE daily index used to verify the complete venue set.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--start-date", default=FORWARD_START_DATE)
    parser.add_argument("--lookahead-minutes", type=float, default=CAPTURE_LOOKAHEAD_MINUTES)
    parser.add_argument("--race-id", action="append", default=[], help="Limit capture attempts to one or more race ids.")
    parser.add_argument("--offline", action="store_true", help="Use only the supplied SQLite rows; do not refresh live pages.")
    parser.add_argument("--no-result-fetch", action="store_true", help="Do not fetch result pages for open records absent from SQLite.")
    parser.add_argument("--notify-adoption", action="store_true", help="Test-only inline notification; forbidden for the production ledger.")
    parser.add_argument("--notify-from-status", action="store_true", help="Notify only from an already-persisted status.json; does not capture or settle.")
    parser.add_argument("--bootstrap-samples", type=int, default=BOOTSTRAP_SAMPLES)
    parser.add_argument("--workflow-run-id", default=os.environ.get("GITHUB_RUN_ID"))
    parser.add_argument("--workflow-run-attempt", default=os.environ.get("GITHUB_RUN_ATTEMPT"))
    return parser.parse_args()


def as_num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def as_int(value: Any) -> int | None:
    number = as_num(value)
    return int(number) if number is not None else None


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=JST)
    return parsed.astimezone(JST)


def normalize_ticket(value: Any) -> str:
    digits = "".join(character for character in str(value or "") if character.isdigit())
    return digits if len(digits) == 3 else ""


def display_ticket(value: Any) -> str:
    ticket = normalize_ticket(value)
    return "-".join(ticket) if ticket else ""


def canonical_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == rendered:
        return
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(path)


def load_json_strict(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(f"ledger JSON is unreadable: {path}: {exc}") from exc


@functools.lru_cache(maxsize=1)
def monitor_code_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


@functools.lru_cache(maxsize=1)
def monitor_bundle_sha256() -> str:
    digest = hashlib.sha256()
    paths = (
        Path(__file__),
        ROOT / "scripts" / "monitor_boaters_manshu_alerts.py",
        ROOT / "scripts" / "build_boaters_database.py",
        ROOT / "scripts" / "fetch_boatrace_data.py",
        ROOT / ".github" / "workflows" / "least-popular-shadow-monitor.yml",
    )
    for path in paths:
        if not path.exists():
            raise RuntimeError(f"protocol dependency is missing: {path}")
        relative = str(path.resolve().relative_to(ROOT.resolve()))
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def production_output(path: Path) -> bool:
    return path.expanduser().resolve() == DEFAULT_OUTPUT_DIR.resolve()


def validate_runtime_contract(args: argparse.Namespace, now: datetime) -> None:
    if not production_output(args.output_dir):
        return
    violations = []
    requested_date = args.date or now.date().isoformat()
    if args.start_date != FORWARD_START_DATE:
        violations.append("start-date")
    if not math.isclose(args.lookahead_minutes, CAPTURE_LOOKAHEAD_MINUTES):
        violations.append("lookahead-minutes")
    if args.offline:
        violations.append("offline")
    if args.no_result_fetch:
        violations.append("no-result-fetch")
    if args.race_id:
        violations.append("race-id")
    if args.now:
        violations.append("now")
    if args.notify_adoption:
        violations.append("notify-adoption")
    if args.db.resolve() != PRODUCTION_DB:
        violations.append("db")
    if args.verification_db is None or args.verification_db.resolve() != PRODUCTION_VERIFICATION_DB:
        violations.append("verification-db")
    expected_official = PRODUCTION_OFFICIAL_ROOT / requested_date.replace("-", "") / "official_index.html"
    if args.official_index_html is None or args.official_index_html.resolve() != expected_official:
        violations.append("official-index-html")
    if args.bootstrap_samples != BOOTSTRAP_SAMPLES:
        violations.append("bootstrap-samples")
    if not args.workflow_run_id or not args.workflow_run_attempt:
        violations.append("workflow-run-identity")
    if platform.python_version() != PRODUCTION_PYTHON_VERSION:
        violations.append("python-version")
    if requested_date != now.date().isoformat():
        violations.append("date")
    if violations:
        raise ValueError(f"production ledger forbids protocol overrides: {', '.join(violations)}")


@contextlib.contextmanager
def exclusive_ledger_lock(output_dir: Path):
    lock_path = output_dir / ".ledger.lock"
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(f"another monitor process owns the ledger lock: {lock_path}") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def ensure_protocol_lock(output_dir: Path, now: datetime) -> dict[str, Any]:
    path = output_dir / "protocol_lock.json"
    code_hash = monitor_code_sha256()
    bundle_hash = monitor_bundle_sha256()
    runtime = {
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
    }
    if path.exists():
        lock = load_json_strict(path)
        if (
            lock.get("protocol_sha256") != PROTOCOL_SHA256
            or lock.get("monitor_code_sha256") != code_hash
            or lock.get("monitor_bundle_sha256") != bundle_hash
            or lock.get("runtime") != runtime
            or lock.get("protocol") != PROTOCOL
        ):
            raise RuntimeError("protocol lock mismatch; bump the protocol/rule version before collecting more records")
        return lock
    lock = {
        "protocol": PROTOCOL,
        "protocol_sha256": PROTOCOL_SHA256,
        "monitor_code_sha256": code_hash,
        "monitor_bundle_sha256": bundle_hash,
        "runtime": runtime,
        "locked_at": now.isoformat(timespec="seconds"),
    }
    save_json(path, lock)
    return lock


def connect_readonly(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise FileNotFoundError(path)
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def competition_ranks(rows: list[dict[str, Any]], key: str, ascending: bool) -> dict[int, int]:
    values = []
    for row in rows:
        number = as_num(row.get(key))
        if number is None:
            raise IncompleteRaceData(f"missing {key} for boat {row.get('boat_number')}")
        values.append(number)
    ranks: dict[int, int] = {}
    for row, value in zip(rows, values):
        if ascending:
            rank = 1 + sum(other < value for other in values)
        else:
            rank = 1 + sum(other > value for other in values)
        ranks[int(row["boat_number"])] = rank
    return ranks


def list_races(connection: sqlite3.Connection, date_text: str) -> list[dict[str, Any]]:
    has_index = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='race_day_races'"
    ).fetchone()
    indexed_count = 0
    if has_index:
        indexed_count = int(
            connection.execute("SELECT COUNT(*) FROM race_day_races WHERE date=?", (date_text,)).fetchone()[0]
        )
    if indexed_count:
        rows = connection.execute(
            """
            SELECT COALESCE(r.race_id, rr.race_id, rr.crawled_race_id) AS race_id,
                   rr.date, rr.place_id, d.place_name, d.slug, rr.round,
                   COALESCE(r.deadline_time, rr.deadline_time) AS deadline_time,
                   COALESCE(r.is_suspended, rr.is_suspended) AS is_suspended,
                   r.result_payout3t1, r.winning_number3t1, r.fetched_at
            FROM race_day_races rr
            JOIN race_days d ON d.date=rr.date AND d.place_id=rr.place_id
            LEFT JOIN races r ON r.race_id=rr.race_id
            WHERE rr.date=?
            ORDER BY COALESCE(r.deadline_time, rr.deadline_time), rr.place_id, rr.round
            """,
            (date_text,),
        ).fetchall()
    else:
        rows = connection.execute(
            """
            SELECT race_id, date, place_id, place_name, slug, round, deadline_time,
                   is_suspended, result_payout3t1, winning_number3t1, fetched_at
            FROM races
            WHERE date=?
            ORDER BY deadline_time, place_id, round
            """,
            (date_text,),
        ).fetchall()
    return [dict(row) for row in rows]


def load_race_boats(connection: sqlite3.Connection, race_id: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT boat_number, is_absent, ai_3ren_pct, general_3ren_pct,
               ai_prediction_pct, odds_prediction_pct, tenji_time, isshu_time,
               start_tenji_time, finish, henkan
        FROM race_boats
        WHERE race_id=?
        ORDER BY boat_number
        """,
        (race_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def overlay_live_rows(rows: list[dict[str, Any]], live_by_boat: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for source in rows:
        row = dict(source)
        live = live_by_boat.get(int(row["boat_number"]), {})
        for key in (
            "is_absent",
            "ai_3ren_pct",
            "general_3ren_pct",
            "ai_prediction_pct",
            "odds_prediction_pct",
            "tenji_time",
            "isshu_time",
            "start_tenji_time",
        ):
            if as_num(live.get(key)) is not None:
                row[key] = as_num(live.get(key))
        result.append(row)
    return result


def evaluate_strategies(race: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if len(rows) != 6 or {as_int(row.get("boat_number")) for row in rows} != set(range(1, 7)):
        raise IncompleteRaceData("expected exactly six boats")
    if any(as_int(row.get("is_absent")) == 1 for row in rows):
        raise IncompleteRaceData("race contains an absent boat")

    common_required = (
        "odds_prediction_pct",
        "ai_prediction_pct",
        "ai_3ren_pct",
        "general_3ren_pct",
        "tenji_time",
        "isshu_time",
        "start_tenji_time",
    )
    for row in rows:
        for key in common_required:
            if as_num(row.get(key)) is None:
                raise IncompleteRaceData(f"missing {key} for boat {row['boat_number']}")

    odds_values = [as_num(row["odds_prediction_pct"]) for row in rows]
    minimum_market = min(odds_values)
    if sum(value == minimum_market for value in odds_values) != 1:
        return [], {"complete": True, "reason": "least_popular_tie"}
    target = next(row for row in rows if as_num(row["odds_prediction_pct"]) == minimum_market)
    target_boat = int(target["boat_number"])
    if target_boat not in {5, 6}:
        return [], {"complete": True, "reason": "least_popular_not_lane_5_or_6", "target_boat": target_boat}

    tenji_ranks = competition_ranks(rows, "tenji_time", ascending=True)
    isshu_ranks = competition_ranks(rows, "isshu_time", ascending=True)
    general_ranks = competition_ranks(rows, "general_3ren_pct", ascending=False)
    combos = {
        int(row["boat_number"]): as_num(row["tenji_time"]) + as_num(row["isshu_time"])
        for row in rows
    }
    average_combo = sum(combos.values()) / 6.0
    target_combo_diff = average_combo - combos[target_boat]
    target_preview_top2 = min(tenji_ranks[target_boat], isshu_ranks[target_boat]) <= 2
    target_ai = as_num(target["ai_prediction_pct"])
    target_exact14 = target_combo_diff >= 0.10 and target_preview_top2 and target_ai < 12.0

    prepared_rows: list[dict[str, Any]] = []
    multi_debuff_boats: list[int] = []
    for source in rows:
        boat = int(source["boat_number"])
        debuff_flags = {
            "tenji_bottom2": tenji_ranks[boat] >= 5,
            "isshu_bottom2": isshu_ranks[boat] >= 5,
            "start_slow_020": as_num(source["start_tenji_time"]) >= 0.20,
            "general3_bottom2": general_ranks[boat] >= 5,
        }
        debuff_points = sum(bool(value) for value in debuff_flags.values())
        if boat != target_boat and debuff_points >= 2:
            multi_debuff_boats.append(boat)
        prepared_rows.append(
            {
                "boat_number": boat,
                "odds_prediction_pct": as_num(source["odds_prediction_pct"]),
                "ai_prediction_pct": as_num(source["ai_prediction_pct"]),
                "ai_3ren_pct": as_num(source["ai_3ren_pct"]),
                "general_3ren_pct": as_num(source["general_3ren_pct"]),
                "tenji_time": as_num(source["tenji_time"]),
                "isshu_time": as_num(source["isshu_time"]),
                "start_tenji_time": as_num(source["start_tenji_time"]),
                "combo_time": round(combos[boat], 4),
                "tenji_rank": tenji_ranks[boat],
                "isshu_rank": isshu_ranks[boat],
                "general_3ren_rank": general_ranks[boat],
                "multi_debuff_points": debuff_points,
                "multi_debuff_flags": debuff_flags,
            }
        )

    diagnostic = {
        "complete": True,
        "target_boat": target_boat,
        "target_ai_prediction_pct": target_ai,
        "target_ai_3ren_pct": as_num(target["ai_3ren_pct"]),
        "target_combo_avgdiff": round(target_combo_diff, 4),
        "target_tenji_rank": tenji_ranks[target_boat],
        "target_isshu_rank": isshu_ranks[target_boat],
        "target_exact14": target_exact14,
        "favorite_market_pct": max(odds_values),
        "multi_debuff_boats": sorted(multi_debuff_boats),
        "multi_debuff_count": len(multi_debuff_boats),
    }
    if not target_exact14 or target_ai < 4.0:
        diagnostic["reason"] = "base_target_not_matched"
        return [], diagnostic

    candidates: list[dict[str, Any]] = []
    favorite_market_pct = max(odds_values)
    if favorite_market_pct <= 40.0:
        tickets = [f"{target_boat}-1-2", f"{target_boat}-1-3"]
        candidates.append(
            {
                "strategy_id": "c1_target_first_two",
                "target_boat": target_boat,
                "tickets": tickets,
                "stake_yen": len(tickets) * STAKE_PER_TICKET_YEN,
                "rule_inputs": {
                    "favorite_market_pct": favorite_market_pct,
                    "target_ai_prediction_pct": target_ai,
                    "target_combo_avgdiff": round(target_combo_diff, 4),
                },
                "boats": prepared_rows,
            }
        )

    target_ai3 = as_num(target["ai_3ren_pct"])
    if target_ai >= 6.0 and target_ai3 < 45.0 and len(multi_debuff_boats) >= 2:
        rivals = sorted(
            (boat for boat in range(1, 7) if boat != target_boat),
            key=lambda boat: (combos[boat], boat),
        )
        a, b, c, d, e = rivals
        tickets = [f"{a}-{target_boat}-{b}", f"{a}-{target_boat}-{c}", f"{a}-{target_boat}-{d}"]
        candidates.append(
            {
                "strategy_id": "c4_target_second_three",
                "target_boat": target_boat,
                "tickets": tickets,
                "stake_yen": len(tickets) * STAKE_PER_TICKET_YEN,
                "rule_inputs": {
                    "target_ai_prediction_pct": target_ai,
                    "target_ai_3ren_pct": target_ai3,
                    "target_combo_avgdiff": round(target_combo_diff, 4),
                    "multi_debuff_count": len(multi_debuff_boats),
                    "multi_debuff_boats": sorted(multi_debuff_boats),
                    "rival_order_A_to_E": rivals,
                    "erased_boat_E": e,
                },
                "boats": prepared_rows,
            }
        )
    return candidates, diagnostic


def capture_record(
    race: dict[str, Any],
    candidate: dict[str, Any],
    captured_at: datetime,
    minutes_to_deadline: float,
    prediction_seq: int = 1,
    source_provenance: dict[str, Any] | None = None,
    previous_capture_sha256: str = "GENESIS",
) -> dict[str, Any]:
    strategy_id = candidate["strategy_id"]
    capture = {
        "rule_version": RULE_VERSION,
        "protocol_id": PROTOCOL["protocol_id"],
        "protocol_sha256": PROTOCOL_SHA256,
        "monitor_code_sha256": monitor_code_sha256(),
        "monitor_bundle_sha256": monitor_bundle_sha256(),
        "runtime": {
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
        },
        "prediction_seq": prediction_seq,
        "previous_capture_sha256": previous_capture_sha256,
        "captured_at": captured_at.isoformat(timespec="seconds"),
        "deadline_time": race.get("deadline_time"),
        "minutes_to_deadline": round(minutes_to_deadline, 2),
        "race_id": race.get("race_id"),
        "race_key": canonical_race_key(race),
        "date": race.get("date"),
        "place_id": race.get("place_id"),
        "place_name": race.get("place_name"),
        "slug": race.get("slug"),
        "round": race.get("round"),
        "strategy_id": strategy_id,
        "strategy_label": STRATEGIES[strategy_id]["label"],
        "target_boat": candidate["target_boat"],
        "tickets": candidate["tickets"],
        "stake_yen": candidate["stake_yen"],
        "stake_per_ticket_yen": STAKE_PER_TICKET_YEN,
        "rule_inputs": candidate["rule_inputs"],
        "boats": candidate["boats"],
        "capture_integrity": True,
        "source_provenance": source_provenance or {"source": "offline_sqlite_test"},
    }
    capture["snapshot_sha256"] = canonical_hash(capture)
    return {
        "record_key": f"{RULE_VERSION}:{canonical_race_key(race)}:{strategy_id}",
        "status": "open",
        "capture": capture,
        "settlement": None,
    }


def capture_is_valid(capture: dict[str, Any]) -> bool:
    if (
        capture.get("rule_version") != RULE_VERSION
        or capture.get("protocol_id") != PROTOCOL["protocol_id"]
        or capture.get("protocol_sha256") != PROTOCOL_SHA256
        or capture.get("monitor_code_sha256") != monitor_code_sha256()
        or capture.get("monitor_bundle_sha256") != monitor_bundle_sha256()
        or capture.get("runtime", {}).get("python_version") != platform.python_version()
        or capture.get("runtime", {}).get("python_implementation") != platform.python_implementation()
        or (as_int(capture.get("prediction_seq")) or 0) < 1
        or not capture.get("previous_capture_sha256")
        or not capture.get("capture_integrity")
    ):
        return False
    stored_hash = str(capture.get("snapshot_sha256") or "")
    if not stored_hash:
        return False
    unhashed = dict(capture)
    unhashed.pop("snapshot_sha256", None)
    if canonical_hash(unhashed) != stored_hash:
        return False
    captured_at = parse_dt(capture.get("captured_at"))
    deadline = parse_dt(capture.get("deadline_time"))
    return bool(
        captured_at
        and deadline
        and captured_at < deadline
        and (as_num(capture.get("minutes_to_deadline")) or 0) >= MINIMUM_CAPTURE_LEAD_MINUTES
    )


def empty_daily_payload(date_text: str, start_date: str) -> dict[str, Any]:
    return {
        "version": "least-popular-shadow-daily-v1",
        "rule_version": RULE_VERSION,
        "protocol_id": PROTOCOL["protocol_id"],
        "protocol_sha256": PROTOCOL_SHA256,
        "forward_start_date": start_date,
        "date": date_text,
        "updated_at": None,
        "records": [],
        "race_audit": {},
        "race_manifest": None,
        "workflow_runs": {},
    }


def daily_path(output_dir: Path, date_text: str) -> Path:
    return output_dir / f"candidates_{date_text.replace('-', '')}.json"


def canonical_race_key(race: dict[str, Any]) -> str:
    return f"{race.get('date')}-{int(race.get('place_id')):02d}-{int(race.get('round')):02d}"


def manifest_is_valid(manifest: dict[str, Any]) -> bool:
    stored_hash = str(manifest.get("manifest_sha256") or "")
    unhashed = dict(manifest)
    unhashed.pop("manifest_sha256", None)
    races = manifest.get("races") or []
    keys = [item.get("race_key") for item in races]
    if (
        manifest.get("protocol_sha256") != PROTOCOL_SHA256
        or not stored_hash
        or canonical_hash(unhashed) != stored_hash
        or len(keys) != len(set(keys))
    ):
        return False
    by_place: dict[int, set[int]] = defaultdict(set)
    for race in races:
        if not race.get("race_id") or not race.get("deadline_time"):
            return False
        by_place[int(race["place_id"])].add(int(race["round"]))
    return bool(by_place) and all(rounds == set(range(1, 13)) for rounds in by_place.values())


def canonical_schedule(races: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for source in races:
        race = {
            key: source.get(key)
            for key in ("race_id", "date", "place_id", "place_name", "slug", "round", "deadline_time")
        }
        race["race_key"] = canonical_race_key(race)
        result.append(race)
    return sorted(result, key=lambda item: (item.get("deadline_time") or "", item["race_key"]))


def schedule_sha256(races: list[dict[str, Any]]) -> str:
    return hashlib.sha256(
        json.dumps(canonical_schedule(races), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def official_venue_snapshot(
    path: Path | None,
    fallback_races: list[dict[str, Any]],
) -> tuple[set[int], str | None]:
    if path is None:
        return {int(race["place_id"]) for race in fallback_races}, None
    text = path.read_text(encoding="utf-8")
    venue_ids = {
        int(value)
        for value in re.findall(r"raceindex\?jcd=(\d{2})", html.unescape(text))
    }
    if not venue_ids:
        raise RuntimeError("official BOATRACE index yielded no venues")
    return venue_ids, hashlib.sha256(text.encode("utf-8")).hexdigest()


def freeze_race_manifest(
    daily: dict[str, Any],
    races: list[dict[str, Any]],
    verification_races: list[dict[str, Any]],
    official_venue_ids: set[int],
    official_index_sha256: str | None,
    now: datetime,
) -> dict[str, Any]:
    primary_schedule = canonical_schedule(races)
    verification_schedule = canonical_schedule(verification_races)
    primary_hash = schedule_sha256(races)
    verification_hash = schedule_sha256(verification_races)
    if primary_schedule != verification_schedule or primary_hash != verification_hash:
        raise RuntimeError("independent BOATERS schedule acquisitions do not match")
    boaters_venue_ids = {int(race["place_id"]) for race in primary_schedule}
    if boaters_venue_ids != official_venue_ids:
        raise RuntimeError(
            f"BOATERS/official venue mismatch: BOATERS={sorted(boaters_venue_ids)}, "
            f"official={sorted(official_venue_ids)}"
        )
    venue_set_hash = hashlib.sha256(
        json.dumps(sorted(official_venue_ids), separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    existing = daily.get("race_manifest")
    if existing:
        if not manifest_is_valid(existing):
            raise RuntimeError("daily race manifest hash/completeness check failed")
        if existing.get("schedule_sha256") != primary_hash:
            daily["manifest_drift"] = {
                "detected_at": now.isoformat(timespec="seconds"),
                "frozen_schedule_sha256": existing.get("schedule_sha256"),
                "current_schedule_sha256": primary_hash,
            }
            raise RuntimeError("daily schedule drifted from the frozen manifest")
        if existing.get("official_venue_set_sha256") != venue_set_hash:
            raise RuntimeError("official venue set drifted from the frozen manifest")
        return existing
    manifest_races = primary_schedule
    manifest = {
        "protocol_sha256": PROTOCOL_SHA256,
        "frozen_at": now.isoformat(timespec="seconds"),
        "schedule_sha256": primary_hash,
        "verification_schedule_sha256": verification_hash,
        "independent_schedule_acquisitions": 2,
        "official_venue_ids": sorted(official_venue_ids),
        "official_venue_set_sha256": venue_set_hash,
        "initial_official_index_sha256": official_index_sha256,
        "race_count": len(manifest_races),
        "venue_count": len({int(item["place_id"]) for item in manifest_races}),
        "races": manifest_races,
    }
    manifest["manifest_sha256"] = canonical_hash(manifest)
    if not manifest_is_valid(manifest):
        raise RuntimeError("refusing to freeze a partial daily race manifest")
    daily["race_manifest"] = manifest
    return manifest


def next_prediction_sequences(payloads: dict[Path, dict[str, Any]]) -> dict[str, int]:
    sequences: dict[str, list[int]] = defaultdict(list)
    for payload in payloads.values():
        for record in payload.get("records") or []:
            capture = record.get("capture") or {}
            if capture.get("protocol_sha256") == PROTOCOL_SHA256:
                sequences[str(capture.get("strategy_id"))].append(as_int(capture.get("prediction_seq")) or 0)
    next_values = {}
    for strategy_id in STRATEGIES:
        values = sorted(value for value in sequences.get(strategy_id, []) if value > 0)
        if values and values != list(range(1, len(values) + 1)):
            raise RuntimeError(f"non-contiguous prediction sequence for {strategy_id}")
        next_values[strategy_id] = len(values) + 1
    return next_values


def prediction_chain_heads(payloads: dict[Path, dict[str, Any]]) -> dict[str, str]:
    captures: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for payload in payloads.values():
        for record in payload.get("records") or []:
            capture = record.get("capture") or {}
            if capture.get("protocol_sha256") == PROTOCOL_SHA256:
                captures[str(capture.get("strategy_id"))].append(capture)
    heads = {}
    for strategy_id in STRATEGIES:
        ordered = sorted(
            captures.get(strategy_id, []),
            key=lambda capture: as_int(capture.get("prediction_seq")) or 10**12,
        )
        previous = "GENESIS"
        for capture in ordered:
            if capture.get("previous_capture_sha256") != previous:
                raise RuntimeError(f"broken append-only capture chain for {strategy_id}")
            previous = str(capture.get("snapshot_sha256") or "")
        heads[strategy_id] = previous
    return heads


def record_workflow_run(daily: dict[str, Any], args: argparse.Namespace, now: datetime) -> None:
    if not args.workflow_run_id:
        return
    key = f"{args.workflow_run_id}:{args.workflow_run_attempt or '1'}"
    runs = daily.setdefault("workflow_runs", {})
    runs.setdefault(
        key,
        {
            "run_id": str(args.workflow_run_id),
            "run_attempt": str(args.workflow_run_attempt or "1"),
            "started_at": now.isoformat(timespec="seconds"),
        },
    )


def import_live_module():
    scripts_dir = str(ROOT / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import monitor_boaters_manshu_alerts as live  # type: ignore

    return live


def normalize_pct(value: Any) -> float | None:
    number = as_num(value)
    if number is None:
        return None
    return number * 100.0 if 0 <= number <= 1 else number


def next_data_payload(text: str) -> dict[str, Any]:
    match = re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', text, re.S)
    if not match:
        raise IncompleteRaceData("NEXT_DATA not found in live page")
    return json.loads(html.unescape(match.group(1)))


def live_race_object(text: str) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = next_data_payload(text)
    state = payload["props"]["pageProps"]["initialApolloState"]
    root = state.get("ROOT_QUERY") or {}
    for key, value in root.items():
        if str(key).startswith("raceRoundDetail("):
            race = dereference(state, value)
            if isinstance(race, dict):
                return state, race
    raise IncompleteRaceData("raceRoundDetail not found in live page")


def fetch_live_rows(race: dict[str, Any]) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    live = import_live_module()
    slug = race.get("slug") or getattr(live, "PLACE_SLUGS", {}).get(race.get("place_name"))
    if not slug:
        raise IncompleteRaceData(f"unknown place slug: {race.get('place_name')}")
    date_text = str(race.get("date") or "")
    round_no = int(race.get("round"))
    data_text = live.fetch_boaters_page(slug, date_text, round_no, "data", refresh=True)
    last_text = live.fetch_boaters_page(slug, date_text, round_no, "last-minute", refresh=True)
    data_rows = live.extract_data_page(data_text)
    last_rows = live.extract_last_minute_page(last_text)
    state, raw_race = live_race_object(data_text)
    _, raw_last_race = live_race_object(last_text)
    expected_race_id = str(race.get("race_id") or "")
    expected_round = int(race.get("round"))
    expected_date = str(race.get("date") or "")
    provider_deadlines = []
    for page_name, page_race in (("data", raw_race), ("last-minute", raw_last_race)):
        provider_race_id = str(page_race.get("raceId") or "")
        provider_round = as_int(page_race.get("round"))
        provider_deadline = parse_dt(page_race.get("deadlineTime"))
        if (
            provider_race_id != expected_race_id
            or provider_round != expected_round
            or not provider_race_id.startswith(expected_date)
            or provider_deadline is None
        ):
            raise IncompleteRaceData(
                f"{page_name} page identity mismatch: expected {expected_race_id}, got {provider_race_id}"
            )
        provider_deadlines.append(provider_deadline.isoformat(timespec="seconds"))
    market = raw_race.get("racerOddsProba") or {}
    racer_refs = raw_race.get("racers") or []
    racers = {}
    for ref in racer_refs:
        item = dereference(state, ref)
        if isinstance(item, dict) and as_int(item.get("boatNumber")) is not None:
            racers[int(item["boatNumber"])] = item
    by_boat: dict[int, dict[str, Any]] = {}
    for boat in range(1, 7):
        row = dict(data_rows.get(boat) or {})
        row.update(last_rows.get(boat) or {})
        row["boat_number"] = boat
        row["odds_prediction_pct"] = normalize_pct(market.get(f"racerOddsProba{boat}"))
        row["is_absent"] = int(bool((racers.get(boat) or {}).get("isAbsent")))
        by_boat[boat] = row
    completed_at = datetime.now(JST)
    provenance = {
        "source": "boaters_live_data_and_last_minute",
        "retrieval_completed_at": completed_at.isoformat(timespec="seconds"),
        "data_page_sha256": hashlib.sha256(data_text.encode("utf-8")).hexdigest(),
        "last_minute_page_sha256": hashlib.sha256(last_text.encode("utf-8")).hexdigest(),
        "slug": slug,
        "date": date_text,
        "round": round_no,
        "provider_race_id": expected_race_id,
        "provider_deadline_times": provider_deadlines,
    }
    return by_boat, provenance


def scan_current_date(
    connection: sqlite3.Connection,
    verification_connection: sqlite3.Connection,
    official_index_html: Path | None,
    daily: dict[str, Any],
    date_text: str,
    now: datetime,
    lookahead_minutes: float,
    offline: bool,
    race_ids: set[str],
    next_sequences: dict[str, int],
    chain_heads: dict[str, str],
) -> dict[str, int]:
    existing = {record.get("record_key") for record in daily.get("records") or []}
    audit = daily.setdefault("race_audit", {})
    counters = defaultdict(int)
    source_races = list_races(connection, date_text)
    verification_races = list_races(verification_connection, date_text)
    official_venue_ids, official_index_hash = official_venue_snapshot(official_index_html, source_races)
    manifest = freeze_race_manifest(
        daily,
        source_races,
        verification_races,
        official_venue_ids,
        official_index_hash,
        now,
    )
    for race in manifest.get("races") or []:
        race_id = str(race.get("race_id") or "")
        race_key = str(race.get("race_key") or canonical_race_key(race))
        if not race_id or (race_ids and race_id not in race_ids):
            continue
        deadline = parse_dt(race.get("deadline_time"))
        if deadline is None:
            counters["no_deadline"] += 1
            continue
        minutes = (deadline - now).total_seconds() / 60.0
        prior = audit.get(race_key) or {}
        if prior.get("status") == "evaluated":
            counters["already_evaluated"] += 1
            continue
        if minutes > lookahead_minutes:
            counters["outside_window"] += 1
            continue
        if minutes < MINIMUM_CAPTURE_LEAD_MINUTES:
            audit[race_key] = {
                **prior,
                "status": "missed_window",
                "deadline_time": race.get("deadline_time"),
                "last_checked_at": now.isoformat(timespec="seconds"),
                "minutes_to_deadline": round(minutes, 2),
                "reason": f"minimum_lead_{MINIMUM_CAPTURE_LEAD_MINUTES:g}_minutes_not_met",
            }
            counters["missed_window"] += 1
            continue

        audit_entry = {
            **prior,
            "deadline_time": race.get("deadline_time"),
            "last_checked_at": now.isoformat(timespec="seconds"),
            "attempts": int(prior.get("attempts") or 0) + 1,
        }
        try:
            source_provenance: dict[str, Any]
            if offline:
                rows = load_race_boats(connection, race_id)
                captured_at = now
                source_provenance = {
                    "source": "offline_sqlite_test",
                    "database_race_fetched_at": race.get("fetched_at"),
                    "retrieval_completed_at": captured_at.isoformat(timespec="seconds"),
                }
            else:
                live_rows, source_provenance = fetch_live_rows(race)
                rows = [live_rows[boat] for boat in range(1, 7)]
                captured_at = parse_dt(source_provenance.get("retrieval_completed_at")) or datetime.now(JST)
            provider_deadlines = [
                parsed
                for value in source_provenance.get("provider_deadline_times") or []
                for parsed in [parse_dt(value)]
                if parsed is not None
            ]
            effective_deadline = min([deadline, *provider_deadlines])
            completed_minutes = (effective_deadline - captured_at).total_seconds() / 60.0
            if completed_minutes < MINIMUM_CAPTURE_LEAD_MINUTES:
                audit_entry.update(
                    {
                        "status": "missed_window",
                        "reason": f"retrieval_completed_inside_{MINIMUM_CAPTURE_LEAD_MINUTES:g}_minute_guard",
                        "retrieval_completed_at": captured_at.isoformat(timespec="seconds"),
                        "minutes_to_deadline": round(completed_minutes, 2),
                    }
                )
                counters["missed_window"] += 1
                audit[race_key] = audit_entry
                continue
            candidates, diagnostic = evaluate_strategies(race, rows)
            audit_entry.update(
                {
                    "status": "evaluated",
                    "evaluated_at": captured_at.isoformat(timespec="seconds"),
                    "minutes_to_deadline": round(completed_minutes, 2),
                    "matched_strategies": [candidate["strategy_id"] for candidate in candidates],
                    "diagnostic": diagnostic,
                    "source_provenance": source_provenance,
                    "manifest_deadline_time": deadline.isoformat(timespec="seconds"),
                    "effective_deadline_time": effective_deadline.isoformat(timespec="seconds"),
                }
            )
            for candidate in candidates:
                strategy_id = candidate["strategy_id"]
                capture_race = dict(race)
                capture_race["deadline_time"] = effective_deadline.isoformat(timespec="seconds")
                record = capture_record(
                    capture_race,
                    candidate,
                    captured_at,
                    completed_minutes,
                    prediction_seq=next_sequences[strategy_id],
                    source_provenance=source_provenance,
                    previous_capture_sha256=chain_heads[strategy_id],
                )
                if record["record_key"] not in existing:
                    daily.setdefault("records", []).append(record)
                    existing.add(record["record_key"])
                    next_sequences[strategy_id] += 1
                    chain_heads[strategy_id] = record["capture"]["snapshot_sha256"]
                    counters["captured"] += 1
            counters["evaluated"] += 1
        except IncompleteRaceData as exc:
            audit_entry.update({"status": "incomplete", "error": str(exc)})
            counters["incomplete"] += 1
        except Exception as exc:
            audit_entry.update({"status": "fetch_failed", "error": str(exc)})
            counters["fetch_failed"] += 1
        audit[race_key] = audit_entry
    daily["updated_at"] = now.isoformat(timespec="seconds")
    daily["records"] = sorted(
        daily.get("records") or [],
        key=lambda record: (record.get("capture", {}).get("deadline_time") or "", record.get("record_key") or ""),
    )
    return dict(counters)


def sqlite_result(connection: sqlite3.Connection, race_id: str) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT r.is_suspended, r.winning_number3t1, r.result_payout3t1,
               GROUP_CONCAT(CASE WHEN COALESCE(b.henkan, 0)=1 THEN b.boat_number END) AS refund_boats,
               r.fetched_at
        FROM races r
        LEFT JOIN race_boats b ON b.race_id=r.race_id
        WHERE r.race_id=?
        GROUP BY r.race_id
        """,
        (race_id,),
    ).fetchone()
    if row is None:
        return None
    result = dict(row)
    if not normalize_ticket(result.get("winning_number3t1")) and not as_int(result.get("is_suspended")):
        return None
    result["result_source"] = "sqlite_exact_race_id"
    result["requested_race_id"] = race_id
    return result


def dereference(state: dict[str, Any], value: Any) -> Any:
    if isinstance(value, dict) and value.get("__ref"):
        return state.get(value["__ref"])
    return value


def parse_result_page(text: str) -> dict[str, Any] | None:
    match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', text, re.S)
    if not match:
        return None
    payload = json.loads(html.unescape(match.group(1)))
    state = payload["props"]["pageProps"]["initialApolloState"]
    root = state.get("ROOT_QUERY") or {}
    race = None
    for key, value in root.items():
        if str(key).startswith("raceRoundDetail("):
            race = dereference(state, value)
            break
    if not isinstance(race, dict):
        return None
    result = dereference(state, race.get("result")) or race.get("result") or {}
    if not isinstance(result, dict):
        return None
    racers = []
    for value in result.get("racers") or []:
        item = dereference(state, value)
        if isinstance(item, dict):
            racers.append(item)
    parsed = {
        "provider_race_id": race.get("raceId"),
        "provider_round": race.get("round"),
        "provider_deadline_time": race.get("deadlineTime"),
        "is_suspended": result.get("isSuspended"),
        "winning_number3t1": result.get("winningNumber3t1"),
        "result_payout3t1": result.get("resultPayout3t1"),
        "refund_boats": [
            int(item.get("boatNumber"))
            for item in racers
            if bool(item.get("henkan")) and as_int(item.get("boatNumber")) is not None
        ],
    }
    if not normalize_ticket(parsed.get("winning_number3t1")) and not parsed.get("is_suspended"):
        return None
    return parsed


def web_result(capture: dict[str, Any]) -> dict[str, Any] | None:
    live = import_live_module()
    text = live.fetch_boaters_page(
        capture.get("slug"),
        capture.get("date"),
        int(capture.get("round")),
        "result",
        refresh=True,
    )
    result = parse_result_page(text)
    if result is not None:
        if (
            str(result.get("provider_race_id") or "") != str(capture.get("race_id") or "")
            or as_int(result.get("provider_round")) != as_int(capture.get("round"))
        ):
            raise RuntimeError("result page identity does not match the captured race")
        result.update(
            {
                "result_source": "boaters_result_page",
                "result_page_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "retrieved_at": datetime.now(JST).isoformat(timespec="seconds"),
                "requested_race_key": capture.get("race_key"),
            }
        )
    return result


def refund_boat_numbers(value: Any) -> set[int]:
    if isinstance(value, list):
        return {
            converted
            for item in value
            for converted in [as_int(item)]
            if converted in set(range(1, 7))
        }
    return {
        int(item)
        for item in re.findall(r"[1-6]", str(value or ""))
    }


RESULT_PROVENANCE_KEYS = (
    "result_source",
    "result_page_sha256",
    "retrieved_at",
    "fetched_at",
    "requested_race_key",
    "requested_race_id",
    "provider_race_id",
    "provider_round",
    "provider_deadline_time",
)


def result_provenance(result: dict[str, Any]) -> dict[str, Any]:
    return {key: result.get(key) for key in RESULT_PROVENANCE_KEYS}


def seal_settlement(settlement: dict[str, Any]) -> dict[str, Any]:
    sealed = dict(settlement)
    sealed["settlement_sha256"] = canonical_hash(sealed)
    return sealed


def settlement_is_valid(record: dict[str, Any]) -> bool:
    settlement = record.get("settlement") or {}
    stored_hash = str(settlement.get("settlement_sha256") or "")
    unhashed = dict(settlement)
    unhashed.pop("settlement_sha256", None)
    if not stored_hash or canonical_hash(unhashed) != stored_hash:
        return False
    provenance = settlement.get("result_provenance") or {}
    source = provenance.get("result_source")
    if source == "boaters_result_page":
        return bool(
            provenance.get("result_page_sha256")
            and provenance.get("requested_race_key") == record.get("capture", {}).get("race_key")
            and provenance.get("provider_race_id") == record.get("capture", {}).get("race_id")
        )
    return source == "sqlite_exact_race_id" and provenance.get("requested_race_id") == record.get("capture", {}).get("race_id")


def settle_record(record: dict[str, Any], result: dict[str, Any], settled_at: datetime) -> bool:
    if record.get("status") != "open":
        return False
    suspended = bool(as_int(result.get("is_suspended")))
    if suspended:
        record["status"] = "void"
        record["settlement"] = seal_settlement({
            "status": "void",
            "settled_at": settled_at.isoformat(timespec="seconds"),
            "reason": "suspended",
            "investment_yen": 0,
            "return_yen": 0,
            "result_provenance": result_provenance(result),
        })
        return True
    winning = normalize_ticket(result.get("winning_number3t1"))
    payout = as_int(result.get("result_payout3t1"))
    if not winning or payout is None:
        return False
    tickets = {normalize_ticket(ticket) for ticket in record["capture"].get("tickets") or []}
    refunded_boats = refund_boat_numbers(result.get("refund_boats"))
    refunded_tickets = {
        ticket for ticket in tickets if any(str(boat) in ticket for boat in refunded_boats)
    }
    active_tickets = tickets - refunded_tickets
    if not active_tickets:
        record["status"] = "void"
        record["settlement"] = seal_settlement({
            "status": "void",
            "settled_at": settled_at.isoformat(timespec="seconds"),
            "reason": "all_tickets_refunded",
            "refund_boats": sorted(refunded_boats),
            "refunded_tickets": sorted(display_ticket(ticket) for ticket in refunded_tickets),
            "investment_yen": 0,
            "return_yen": 0,
            "result_provenance": result_provenance(result),
        })
        return True
    hit = winning in active_tickets
    stake = len(active_tickets) * STAKE_PER_TICKET_YEN
    record["status"] = "settled"
    record["settlement"] = seal_settlement({
        "status": "settled",
        "settled_at": settled_at.isoformat(timespec="seconds"),
        "winning_trifecta": "-".join(winning),
        "payout_yen_per_100": payout,
        "refund_boats": sorted(refunded_boats),
        "refunded_tickets": sorted(display_ticket(ticket) for ticket in refunded_tickets),
        "active_tickets": sorted(display_ticket(ticket) for ticket in active_tickets),
        "hit": hit,
        "investment_yen": stake,
        "return_yen": payout if hit else 0,
        "net_yen": (payout if hit else 0) - stake,
        "result_provenance": result_provenance(result),
    })
    return True


def validate_daily_ledger(payload: dict[str, Any], path: Path | None = None) -> None:
    records = payload.get("records") or []
    keys = [str(record.get("record_key") or "") for record in records]
    if not all(keys) or len(keys) != len(set(keys)):
        raise RuntimeError(f"duplicate or empty record key in {path or 'daily ledger'}")
    by_race_strategy = {}
    for record in records:
        capture = record.get("capture") or {}
        if not capture_is_valid(capture):
            raise RuntimeError(f"capture hash/protocol mismatch in {path or 'daily ledger'}: {record.get('record_key')}")
        if str(capture.get("date") or "") != str(payload.get("date") or ""):
            raise RuntimeError(f"capture date mismatch in {path or 'daily ledger'}")
        by_race_strategy[(capture.get("race_key"), capture.get("strategy_id"))] = record
        if record.get("status") in {"settled", "void"} and not settlement_is_valid(record):
            raise RuntimeError(f"settlement hash/provenance mismatch in {path or 'daily ledger'}")
    audit = payload.get("race_audit") or {}
    for race_key, entry in audit.items():
        if entry.get("status") != "evaluated":
            continue
        matched = set(entry.get("matched_strategies") or [])
        recorded = {
            strategy_id
            for (record_race_key, strategy_id) in by_race_strategy
            if record_race_key == race_key
        }
        if matched != recorded:
            raise RuntimeError(f"audit/candidate mismatch for {race_key} in {path or 'daily ledger'}")
    for race_key, strategy_id in by_race_strategy:
        entry = audit.get(race_key) or {}
        if entry.get("status") != "evaluated" or strategy_id not in set(entry.get("matched_strategies") or []):
            raise RuntimeError(f"candidate lacks matching evaluated audit for {race_key} in {path or 'daily ledger'}")


def load_all_daily(output_dir: Path, current_date: str, start_date: str) -> dict[Path, dict[str, Any]]:
    payloads: dict[Path, dict[str, Any]] = {}
    for path in sorted(output_dir.glob("candidates_*.json")):
        payload = load_json_strict(path)
        if not isinstance(payload, dict):
            raise RuntimeError(f"ledger payload is not an object: {path}")
        if (
            payload.get("rule_version") != RULE_VERSION
            or payload.get("protocol_sha256") != PROTOCOL_SHA256
            or payload.get("protocol_id") != PROTOCOL["protocol_id"]
        ):
            raise RuntimeError(f"ledger protocol mismatch: {path}")
        validate_daily_ledger(payload, path)
        payloads[path] = payload
    current_path = daily_path(output_dir, current_date)
    if current_path not in payloads:
        payloads[current_path] = empty_daily_payload(current_date, start_date)
    return payloads


def settle_open_records(
    payloads: dict[Path, dict[str, Any]],
    connection: sqlite3.Connection,
    now: datetime,
    fetch_results: bool,
) -> dict[str, int]:
    counters = defaultdict(int)
    for payload in payloads.values():
        for record in payload.get("records") or []:
            if record.get("status") != "open":
                continue
            capture = record.get("capture") or {}
            deadline = parse_dt(capture.get("deadline_time"))
            if deadline is None or (now - deadline).total_seconds() < 10 * 60:
                counters["not_ready"] += 1
                continue
            result = sqlite_result(connection, str(capture.get("race_id") or ""))
            if result is None and fetch_results:
                try:
                    result = web_result(capture)
                except Exception:
                    result = None
            if result is None:
                counters["still_open"] += 1
                continue
            if settle_record(record, result, now):
                payload["updated_at"] = now.isoformat(timespec="seconds")
                counters[record.get("status") or "unknown"] += 1
    return dict(counters)


def maximum_drawdown(net_values: list[int]) -> int:
    cumulative = 0
    peak = 0
    maximum = 0
    for value in net_values:
        cumulative += value
        peak = max(peak, cumulative)
        maximum = max(maximum, peak - cumulative)
    return maximum


def consecutive_misses(hit_values: list[bool], current_only: bool = False) -> int:
    if current_only:
        count = 0
        for hit in reversed(hit_values):
            if hit:
                break
            count += 1
        return count
    longest = 0
    current = 0
    for hit in hit_values:
        current = 0 if hit else current + 1
        longest = max(longest, current)
    return longest


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(math.floor(quantile * (len(ordered) - 1)))))
    return ordered[index]


def day_bootstrap_lower(records: list[dict[str, Any]], samples: int, seed: int) -> float | None:
    if not records or samples <= 0:
        return None
    daily: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for record in records:
        day = str(record["capture"].get("date") or "")
        settlement = record["settlement"]
        daily[day][0] += int(settlement.get("return_yen") or 0)
        daily[day][1] += int(settlement.get("investment_yen") or 0)
    values = list(daily.values())
    if not values:
        return None
    rng = random.Random(seed)
    rois = []
    for _ in range(samples):
        returns = 0
        stakes = 0
        for _ in values:
            chosen = values[rng.randrange(len(values))]
            returns += chosen[0]
            stakes += chosen[1]
        rois.append(returns / stakes * 100.0 if stakes else 0.0)
    value = percentile(rois, 0.05)
    return round(value, 2) if value is not None else None


def protocol_records(
    payloads: dict[Path, dict[str, Any]],
    start_date: str,
    strategy_id: str | None = None,
) -> list[dict[str, Any]]:
    result = []
    for payload in payloads.values():
        for record in payload.get("records") or []:
            capture = record.get("capture") or {}
            if (
                capture.get("rule_version") != RULE_VERSION
                or capture.get("protocol_sha256") != PROTOCOL_SHA256
                or str(capture.get("date") or "") < start_date
            ):
                continue
            if strategy_id and capture.get("strategy_id") != strategy_id:
                continue
            result.append(record)
    return sorted(
        result,
        key=lambda item: (
            as_int(item.get("capture", {}).get("prediction_seq")) or 10**12,
            item.get("record_key") or "",
        ),
    )


def settled_records(
    payloads: dict[Path, dict[str, Any]],
    start_date: str,
    strategy_id: str | None = None,
) -> list[dict[str, Any]]:
    return [
        record
        for record in protocol_records(payloads, start_date, strategy_id)
        if record.get("status") == "settled"
    ]


def audit_coverage(
    payloads: dict[Path, dict[str, Any]],
    start_date: str,
    now: datetime,
) -> dict[str, Any]:
    evaluated = 0
    total = 0
    by_status = defaultdict(int)
    valid_manifests = 0
    manifest_days = set()
    workflow_day_results = {}
    for payload in payloads.values():
        if str(payload.get("date") or "") < start_date:
            continue
        payload_date = str(payload.get("date") or "")
        manifest_days.add(payload_date)
        manifest = payload.get("race_manifest") or {}
        if not manifest_is_valid(manifest):
            by_status["invalid_or_missing_manifest"] += 1
            continue
        valid_manifests += 1
        run_times = sorted(
            parsed
            for run in (payload.get("workflow_runs") or {}).values()
            for parsed in [parse_dt(run.get("started_at"))]
            if parsed is not None
        )
        if date.fromisoformat(payload_date) < now.date():
            gaps = [
                (later - earlier).total_seconds() / 60.0
                for earlier, later in zip(run_times, run_times[1:])
            ]
            first_ok = bool(run_times) and run_times[0].hour * 60 + run_times[0].minute <= 7 * 60 + 30
            last_ok = bool(run_times) and run_times[-1].hour * 60 + run_times[-1].minute >= 21 * 60 + 30
            max_gap = max(gaps) if gaps else None
            workflow_day_results[payload_date] = {
                "runs": len(run_times),
                "first_run_at": run_times[0].isoformat(timespec="seconds") if run_times else None,
                "last_run_at": run_times[-1].isoformat(timespec="seconds") if run_times else None,
                "max_gap_minutes": round(max_gap, 2) if max_gap is not None else None,
                "complete": bool(first_ok and last_ok and max_gap is not None and max_gap <= 15.0),
            }
        audit = payload.get("race_audit") or {}
        for race in manifest.get("races") or []:
            deadline = parse_dt(race.get("deadline_time"))
            if deadline is not None and deadline > now:
                continue
            race_key = str(race.get("race_key") or "")
            entry = audit.get(race_key) or {}
            status = str(entry.get("status") or "missing_audit")
            total += 1
            by_status[status] += 1
            if status == "evaluated":
                evaluated += 1
    current = date.fromisoformat(start_date)
    last_required = now.date() - timedelta(days=1)
    missing_manifest_days = []
    while current <= last_required:
        key = current.isoformat()
        if key not in manifest_days:
            missing_manifest_days.append(key)
        current += timedelta(days=1)
    rate = evaluated / total * 100.0 if total else None
    manifest_rate = valid_manifests / len(manifest_days) * 100.0 if manifest_days else None
    workflow_complete_days = sum(bool(item["complete"]) for item in workflow_day_results.values())
    workflow_rate = (
        workflow_complete_days / len(workflow_day_results) * 100.0
        if workflow_day_results
        else None
    )
    return {
        "closed_races": total,
        "evaluated_races": evaluated,
        "complete_rate_pct": round(rate, 2) if rate is not None else None,
        "manifest_days": len(manifest_days),
        "valid_manifest_days": valid_manifests,
        "manifest_integrity_pct": round(manifest_rate, 2) if manifest_rate is not None else None,
        "missing_manifest_days": missing_manifest_days,
        "workflow_past_days": len(workflow_day_results),
        "workflow_complete_days": workflow_complete_days,
        "workflow_complete_rate_pct": round(workflow_rate, 2) if workflow_rate is not None else None,
        "workflow_days": workflow_day_results,
        "by_status": dict(sorted(by_status.items())),
    }


def metric_summary(records: list[dict[str, Any]], bootstrap_samples: int, seed: int) -> dict[str, Any]:
    races = len(records)
    stakes = [int(record["settlement"].get("investment_yen") or 0) for record in records]
    returns = [int(record["settlement"].get("return_yen") or 0) for record in records]
    hits = [bool(record["settlement"].get("hit")) for record in records]
    integrity = [capture_is_valid(record.get("capture") or {}) for record in records]
    settlement_integrity = [settlement_is_valid(record) for record in records]
    investment = sum(stakes)
    total_return = sum(returns)
    hit_returns = sorted((value for value in returns if value > 0), reverse=True)
    roi = total_return / investment * 100.0 if investment else None
    roi_without_top = (total_return - (hit_returns[0] if hit_returns else 0)) / investment * 100.0 if investment else None
    top_share = hit_returns[0] / total_return * 100.0 if total_return and hit_returns else None
    top3_share = sum(hit_returns[:3]) / total_return * 100.0 if total_return and hit_returns else None
    midpoint = races // 2
    first = records[:midpoint]
    second = records[midpoint:]

    def slice_values(values: list[dict[str, Any]]) -> dict[str, Any]:
        stake = sum(int(item["settlement"].get("investment_yen") or 0) for item in values)
        returned = sum(int(item["settlement"].get("return_yen") or 0) for item in values)
        hit_count = sum(bool(item["settlement"].get("hit")) for item in values)
        return {
            "races": len(values),
            "hits": hit_count,
            "roi_pct": round(returned / stake * 100.0, 2) if stake else None,
        }

    quarters = {
        f"{capture_time.year}Q{(capture_time.month - 1) // 3 + 1}"
        for record in records
        if record["settlement"].get("hit")
        for capture_time in [parse_dt(record["capture"].get("captured_at"))]
        if capture_time is not None
    }
    lanes: dict[str, dict[str, Any]] = {}
    for lane in (5, 6):
        lane_records = [record for record in records if as_int(record["capture"].get("target_boat")) == lane]
        lanes[str(lane)] = slice_values(lane_records)
    return {
        "races": races,
        "hits": sum(hits),
        "hit_rate_pct": round(sum(hits) / races * 100.0, 2) if races else None,
        "capture_integrity_pct": round(sum(integrity) / races * 100.0, 2) if races else None,
        "settlement_integrity_pct": round(sum(settlement_integrity) / races * 100.0, 2) if races else None,
        "investment_yen": investment,
        "return_yen": total_return,
        "net_yen": total_return - investment,
        "roi_pct": round(roi, 2) if roi is not None else None,
        "bootstrap_one_sided_95_lower_roi_pct": day_bootstrap_lower(records, bootstrap_samples, seed),
        "roi_without_top_hit_pct": round(roi_without_top, 2) if roi_without_top is not None else None,
        "top_hit_return_share_pct": round(top_share, 2) if top_share is not None else None,
        "top3_hit_return_share_pct": round(top3_share, 2) if top3_share is not None else None,
        "max_drawdown_yen": maximum_drawdown([returned - stake for returned, stake in zip(returns, stakes)]),
        "max_consecutive_misses": consecutive_misses(hits),
        "current_consecutive_misses": consecutive_misses(hits, current_only=True),
        "hit_calendar_quarters": len(quarters),
        "hit_quarter_labels": sorted(quarters),
        "first_half": slice_values(first),
        "second_half": slice_values(second),
        "by_target_lane": lanes,
    }


def gate(name: str, actual: Any, threshold: Any, passed: bool, detail: str = "") -> dict[str, Any]:
    return {
        "name": name,
        "actual": actual,
        "threshold": threshold,
        "pass": bool(passed),
        "detail": detail,
    }


def sequence_quality(records: list[dict[str, Any]]) -> dict[str, Any]:
    values = [as_int(record.get("capture", {}).get("prediction_seq")) or 0 for record in records]
    expected = list(range(1, len(values) + 1))
    previous = "GENESIS"
    chain_valid = True
    for record in records:
        capture = record.get("capture") or {}
        if capture.get("previous_capture_sha256") != previous:
            chain_valid = False
            break
        previous = str(capture.get("snapshot_sha256") or "")
    return {
        "records": len(values),
        "contiguous_unique_from_one": values == expected,
        "append_hash_chain_valid": chain_valid,
        "first_bad_position": next(
            (index for index, (actual, wanted) in enumerate(zip(values, expected), start=1) if actual != wanted),
            None,
        ),
    }


def resolved_prefix_records(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    settled = []
    resolved_predictions = 0
    for record in records:
        status = str(record.get("status") or "")
        if status == "open":
            break
        if status not in {"settled", "void"}:
            break
        resolved_predictions += 1
        if status == "settled":
            settled.append(record)
    return settled, resolved_predictions


def settlement_quality(records: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    due = []
    overdue_before = now - timedelta(hours=SETTLEMENT_OVERDUE_HOURS)
    for record in records:
        deadline = parse_dt(record.get("capture", {}).get("deadline_time"))
        if deadline is not None and deadline <= overdue_before:
            due.append(record)
    resolved = sum(record.get("status") in {"settled", "void"} for record in due)
    overdue_open = sum(record.get("status") == "open" for record in due)
    rate = resolved / len(due) * 100.0 if due else None
    return {
        "due_predictions": len(due),
        "resolved_predictions": resolved,
        "overdue_open_predictions": overdue_open,
        "complete_rate_pct": round(rate, 2) if rate is not None else None,
    }


def performance_gates(metrics: dict[str, Any], config: dict[str, Any]) -> list[dict[str, Any]]:
    first_half = metrics["first_half"]
    second_half = metrics["second_half"]
    segment = metrics["by_target_lane"][str(config["segment_lane"])]
    return [
        gate("累積ROI", metrics["roi_pct"], ">=200%", (metrics["roi_pct"] or 0) >= COMMON_GATES["minimum_roi_pct"]),
        gate("日bootstrap片側95%下限", metrics["bootstrap_one_sided_95_lower_roi_pct"], ">=100%", (metrics["bootstrap_one_sided_95_lower_roi_pct"] or 0) >= COMMON_GATES["minimum_bootstrap_lower_pct"]),
        gate("最大1本除外ROI", metrics["roi_without_top_hit_pct"], ">=150%", (metrics["roi_without_top_hit_pct"] or 0) >= COMMON_GATES["minimum_roi_without_top_hit_pct"]),
        gate("最大1本寄与率", metrics["top_hit_return_share_pct"], "<=40%", (metrics["top_hit_return_share_pct"] or 100) <= COMMON_GATES["maximum_top_hit_share_pct"]),
        gate("上位3本寄与率", metrics["top3_hit_return_share_pct"], "<=70%", (metrics["top3_hit_return_share_pct"] or 100) <= COMMON_GATES["maximum_top3_hit_share_pct"]),
        gate("最低的中数", metrics["hits"], f">={config['minimum_hits']}", metrics["hits"] >= config["minimum_hits"]),
        gate("最低的中率", metrics["hit_rate_pct"], f">={config['minimum_hit_rate_pct']}%", (metrics["hit_rate_pct"] or 0) >= config["minimum_hit_rate_pct"]),
        gate("前半ROI", first_half["roi_pct"], ">=100%", (first_half["roi_pct"] or 0) >= COMMON_GATES["minimum_half_roi_pct"]),
        gate("後半ROI", second_half["roi_pct"], ">=100%", (second_half["roi_pct"] or 0) >= COMMON_GATES["minimum_half_roi_pct"]),
        gate("前半的中数", first_half["hits"], f">={config['minimum_half_hits']}", first_half["hits"] >= config["minimum_half_hits"]),
        gate("後半的中数", second_half["hits"], f">={config['minimum_half_hits']}", second_half["hits"] >= config["minimum_half_hits"]),
        gate("的中四半期数", metrics["hit_calendar_quarters"], f">={COMMON_GATES['minimum_hit_quarters']}", metrics["hit_calendar_quarters"] >= COMMON_GATES["minimum_hit_quarters"]),
        gate(f"{config['segment_lane']}号艇区分的中数", segment["hits"], f">={config['minimum_segment_hits']}", segment["hits"] >= config["minimum_segment_hits"]),
        gate(f"{config['segment_lane']}号艇区分ROI", segment["roi_pct"], f">={COMMON_GATES['minimum_segment_roi_pct']}%", (segment["roi_pct"] or 0) >= COMMON_GATES["minimum_segment_roi_pct"]),
        gate("スナップショット完全率", metrics["capture_integrity_pct"], f">={COMMON_GATES['minimum_capture_integrity_pct']}%", (metrics["capture_integrity_pct"] or 0) >= COMMON_GATES["minimum_capture_integrity_pct"]),
        gate("結果レコード完全率", metrics["settlement_integrity_pct"], f">={COMMON_GATES['minimum_capture_integrity_pct']}%", (metrics["settlement_integrity_pct"] or 0) >= COMMON_GATES["minimum_capture_integrity_pct"]),
    ]


def evaluate_checkpoint(
    strategy_id: str,
    all_records: list[dict[str, Any]],
    coverage: dict[str, Any],
    now: datetime,
    bootstrap_samples: int,
) -> dict[str, Any]:
    config = STRATEGIES[strategy_id]
    first_n = int(config["first_checkpoint_races"])
    increment = int(config["checkpoint_increment_races"])
    stable_records, resolved_predictions = resolved_prefix_records(all_records)
    decision_records = [
        record
        for record in stable_records
        if str(record.get("capture", {}).get("date") or "") < now.date().isoformat()
    ]
    observed_n = len(decision_records)
    if observed_n < first_n:
        checkpoint_n = 0
    else:
        checkpoint_n = first_n + ((observed_n - first_n) // increment) * increment
    checkpoint_records = decision_records[:checkpoint_n] if checkpoint_n else []
    all_settled = [record for record in all_records if record.get("status") == "settled"]
    live_metrics = metric_summary(all_settled, min(bootstrap_samples, 5_000), seed=20260715 + len(all_settled))
    checkpoint_metrics = (
        metric_summary(checkpoint_records, bootstrap_samples, seed=20260715 + checkpoint_n)
        if checkpoint_records
        else None
    )
    capture_rate = coverage.get("complete_rate_pct")
    manifest_rate = coverage.get("manifest_integrity_pct")
    workflow_rate = coverage.get("workflow_complete_rate_pct")
    missing_manifest_days = coverage.get("missing_manifest_days") or []
    sequences = sequence_quality(all_records)
    settlements = settlement_quality(all_records, now)
    stable_metrics = metric_summary(stable_records, min(bootstrap_samples, 5_000), seed=20260715 + observed_n)
    risk_stop = (
        stable_metrics["current_consecutive_misses"] >= config["maximum_current_misses"]
        or stable_metrics["max_drawdown_yen"] > config["maximum_drawdown_yen"]
    )
    gates: list[dict[str, Any]] = []
    status = "WATCH"
    explanation = f"最初の固定判定は{first_n}レース到達時"
    first_checkpoint_metrics = None
    first_checkpoint_performance_pass = None
    if checkpoint_metrics:
        gates = performance_gates(checkpoint_metrics, config) + [
            gate("締切前監視完全率", capture_rate, f">={COMMON_GATES['minimum_capture_integrity_pct']}%", capture_rate is not None and capture_rate >= COMMON_GATES["minimum_capture_integrity_pct"]),
            gate("日次manifest完全率", manifest_rate, f">={COMMON_GATES['minimum_capture_integrity_pct']}%", manifest_rate is not None and manifest_rate >= COMMON_GATES["minimum_capture_integrity_pct"]),
            gate("workflow実行日完全率", workflow_rate, f">={COMMON_GATES['minimum_capture_integrity_pct']}%", workflow_rate is not None and workflow_rate >= COMMON_GATES["minimum_capture_integrity_pct"]),
            gate("欠落manifest日", len(missing_manifest_days), "=0", not missing_manifest_days),
            gate("予測通番", sequences["contiguous_unique_from_one"], "連番・重複なし", sequences["contiguous_unique_from_one"]),
            gate("予測append hash chain", sequences["append_hash_chain_valid"], "連鎖一致", sequences["append_hash_chain_valid"]),
            gate("結果確定完全率", settlements["complete_rate_pct"], f">={COMMON_GATES['minimum_capture_integrity_pct']}%", settlements["complete_rate_pct"] is not None and settlements["complete_rate_pct"] >= COMMON_GATES["minimum_capture_integrity_pct"]),
            gate("期限超過open", settlements["overdue_open_predictions"], "=0", settlements["overdue_open_predictions"] == 0),
        ]
        all_common_pass = all(item["pass"] for item in gates)
        status = "PROMISING" if all_common_pass else "HOLD"
        explanation = f"固定チェックポイント{checkpoint_n}レースの判定"
        if checkpoint_n >= first_n + increment:
            first_checkpoint_metrics = metric_summary(
                decision_records[:first_n], bootstrap_samples, seed=20260715 + first_n
            )
            first_checkpoint_performance_pass = all(
                item["pass"] for item in performance_gates(first_checkpoint_metrics, config)
            )
            gates.append(
                gate(
                    "初回固定チェックポイント実績",
                    first_checkpoint_performance_pass,
                    "全実績ゲート通過",
                    first_checkpoint_performance_pass,
                )
            )
            increment_gates = []
            for tranche_end in range(first_n + increment, checkpoint_n + 1, increment):
                tranche = decision_records[tranche_end - increment : tranche_end]
                tranche_metrics = metric_summary(
                    tranche,
                    min(bootstrap_samples, 5_000),
                    seed=20260715 + tranche_end + 1,
                )
                increment_gates.extend(
                    [
                        gate(
                            f"固定区間{tranche_end - increment + 1}-{tranche_end} ROI",
                            tranche_metrics["roi_pct"],
                            f">={COMMON_GATES['minimum_increment_roi_pct']}%",
                            (tranche_metrics["roi_pct"] or 0) >= COMMON_GATES["minimum_increment_roi_pct"],
                        ),
                        gate(
                            f"固定区間{tranche_end - increment + 1}-{tranche_end} 的中数",
                            tranche_metrics["hits"],
                            f">={config['increment_minimum_hits']}",
                            tranche_metrics["hits"] >= config["increment_minimum_hits"],
                        ),
                    ]
                )
            gates.extend(increment_gates)
            if first_checkpoint_performance_pass and all_common_pass and all(item["pass"] for item in increment_gates):
                status = "ADOPT_SMALL"
                explanation = f"{checkpoint_n}レース固定判定を通過。少額採用可"
                if (checkpoint_metrics["bootstrap_one_sided_95_lower_roi_pct"] or 0) >= 200.0:
                    status = "200_CONFIRMED"
                    explanation = "bootstrap片側95%下限も200%以上"
            else:
                status = "HOLD"
    if risk_stop:
        status = "HOLD"
        explanation = "連敗またはドローダウンの停止基準に到達"
    return {
        "strategy_id": strategy_id,
        "label": config["label"],
        "rule_version": RULE_VERSION,
        "forward_only": True,
        "observed_metrics": live_metrics,
        "stable_resolved_prefix_metrics": stable_metrics,
        "prediction_records": len(all_records),
        "resolved_prefix_predictions": resolved_predictions,
        "closed_day_decision_races": observed_n,
        "sequence_quality": sequences,
        "settlement_quality": settlements,
        "last_completed_checkpoint_races": checkpoint_n,
        "next_checkpoint_races": first_n if checkpoint_n == 0 else checkpoint_n + increment,
        "status": status,
        "explanation": explanation,
        "risk_stop": risk_stop,
        "checkpoint_metrics": checkpoint_metrics,
        "first_checkpoint_metrics": first_checkpoint_metrics,
        "first_checkpoint_performance_pass": first_checkpoint_performance_pass,
        "gates": gates,
        "historical_reference_not_used_for_adoption": config["historical_reference"],
    }


def build_status(
    payloads: dict[Path, dict[str, Any]],
    start_date: str,
    now: datetime,
    bootstrap_samples: int,
) -> dict[str, Any]:
    coverage = audit_coverage(payloads, start_date, now)
    strategies = {}
    for strategy_id in STRATEGIES:
        strategies[strategy_id] = evaluate_checkpoint(
            strategy_id,
            protocol_records(payloads, start_date, strategy_id),
            coverage,
            now,
            bootstrap_samples,
        )
    portfolio_records = settled_records(payloads, start_date)
    open_records = sum(
        1
        for payload in payloads.values()
        for record in payload.get("records") or []
        if record.get("status") == "open"
        and record.get("capture", {}).get("protocol_sha256") == PROTOCOL_SHA256
        and str(record.get("capture", {}).get("date") or "") >= start_date
    )
    void_records = sum(
        1
        for payload in payloads.values()
        for record in payload.get("records") or []
        if record.get("status") == "void"
        and record.get("capture", {}).get("protocol_sha256") == PROTOCOL_SHA256
        and str(record.get("capture", {}).get("date") or "") >= start_date
    )
    return {
        "version": "least-popular-shadow-status-v1",
        "rule_version": RULE_VERSION,
        "protocol": PROTOCOL,
        "protocol_sha256": PROTOCOL_SHA256,
        "forward_start_date": start_date,
        "generated_at": now.isoformat(timespec="seconds"),
        "decision_policy": {
            "historical_results_used_for_adoption": False,
            "fixed_checkpoints_only": True,
            "stake_per_ticket_yen": STAKE_PER_TICKET_YEN,
            "minimum_capture_lead_minutes": MINIMUM_CAPTURE_LEAD_MINUTES,
            "status_meanings": {
                "WATCH": "最低サンプル到達前",
                "PROMISING": "最初の固定チェックポイントを通過",
                "ADOPT_SMALL": "次の固定区間でも再現し、少額採用可",
                "200_CONFIRMED": "bootstrap片側95%下限も200%以上",
                "HOLD": "採用ゲートまたは停止基準を未通過",
            },
        },
        "coverage": coverage,
        "open_records": open_records,
        "void_records": void_records,
        "strategies": strategies,
        "combined_shadow_portfolio": metric_summary(
            portfolio_records,
            min(bootstrap_samples, 5_000),
            seed=20260715 + 999,
        ),
    }


def notify_status_transitions(status: dict[str, Any], state_path: Path) -> dict[str, Any]:
    state = load_json(state_path, {"last_status": {}, "notified": {}})
    last_status = state.setdefault("last_status", {})
    notified = state.setdefault("notified", {})
    notifications = []
    state_changed = False
    live = import_live_module()
    config = live.load_push_config()
    for strategy_id, result in status.get("strategies", {}).items():
        new_status = result.get("status")
        old_status = last_status.get(strategy_id)
        if old_status != new_status:
            last_status[strategy_id] = new_status
            state_changed = True
        notification_key = f"{RULE_VERSION}:{strategy_id}:{new_status}"
        if new_status not in {"ADOPT_SMALL", "200_CONFIRMED"} or notification_key in notified:
            continue
        metrics = result.get("checkpoint_metrics") or result.get("observed_metrics") or {}
        message = (
            f"{result.get('label')} が {new_status} に到達\n"
            f"前向き{metrics.get('races')}R / {metrics.get('hits')}的中 / "
            f"ROI {metrics.get('roi_pct')}% / 最大1本除外 {metrics.get('roi_without_top_hit_pct')}%\n"
            f"{result.get('explanation')}"
        )
        send_result = live.send_ntfy(
            config,
            "人気最下位艇シャドー監視・採用判定",
            message,
            tags="chart_with_upwards_trend,boat",
            priority="high",
        )
        notifications.append({"strategy_id": strategy_id, "from": old_status, "to": new_status, "send": send_result})
        if send_result.get("ok"):
            notified[notification_key] = status.get("generated_at")
            state_changed = True
    if notifications:
        state_changed = True
    if state_changed:
        state["updated_at"] = status.get("generated_at")
        save_json(state_path, state)
    return {"attempts": notifications, "state_changed": state_changed}


def run_locked(args: argparse.Namespace, now: datetime) -> int:
    if args.notify_from_status:
        protocol_lock = ensure_protocol_lock(args.output_dir, now)
        status_path = args.output_dir / "status.json"
        status = load_json_strict(status_path)
        stored_status_hash = str(status.get("status_sha256") or "")
        unhashed_status = dict(status)
        unhashed_status.pop("status_sha256", None)
        if (
            not stored_status_hash
            or canonical_hash(unhashed_status) != stored_status_hash
            or status.get("protocol_sha256") != PROTOCOL_SHA256
            or status.get("protocol_lock", {}).get("monitor_bundle_sha256")
            != protocol_lock.get("monitor_bundle_sha256")
        ):
            raise RuntimeError("committed status does not match the locked protocol bundle")
        result = notify_status_transitions(status, args.output_dir / "notification_state.json")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    date_text = args.date or now.date().isoformat()
    validate_runtime_contract(args, now)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    protocol_lock = ensure_protocol_lock(args.output_dir, now)
    connection = connect_readonly(args.db)
    verification_connection = (
        connect_readonly(args.verification_db) if args.verification_db is not None else connection
    )
    payloads = load_all_daily(args.output_dir, date_text, args.start_date)
    next_sequences = next_prediction_sequences(payloads)
    chain_heads = prediction_chain_heads(payloads)
    current_path = daily_path(args.output_dir, date_text)
    current = payloads[current_path]
    record_workflow_run(current, args, now)
    capture_summary = {}
    if date_text >= args.start_date:
        capture_summary = scan_current_date(
            connection,
            verification_connection,
            args.official_index_html,
            current,
            date_text,
            now,
            args.lookahead_minutes,
            args.offline,
            set(args.race_id),
            next_sequences,
            chain_heads,
        )
    settlement_summary = settle_open_records(
        payloads,
        connection,
        now,
        fetch_results=not args.no_result_fetch and not args.offline,
    )
    for path, payload in payloads.items():
        validate_daily_ledger(payload, path)
        save_json(path, payload)
    status = build_status(payloads, args.start_date, now, args.bootstrap_samples)
    status["protocol_lock"] = protocol_lock
    status["last_run"] = {
        "date": date_text,
        "database": str(args.db),
        "verification_database": str(args.verification_db) if args.verification_db else str(args.db),
        "official_index_html": str(args.official_index_html) if args.official_index_html else None,
        "workflow_run_id": args.workflow_run_id,
        "workflow_run_attempt": args.workflow_run_attempt,
        "offline": args.offline,
        "capture": capture_summary,
        "settlement": settlement_summary,
    }
    if args.notify_adoption:
        status["notification"] = notify_status_transitions(status, args.output_dir / "notification_state.json")
    status["status_sha256"] = canonical_hash(status)
    save_json(args.output_dir / "status.json", status)
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    args = parse_args()
    if args.now and not args.offline:
        raise ValueError("--now is restricted to --offline test runs")
    now = parse_dt(args.now) if args.now else datetime.now(JST)
    if now is None:
        raise ValueError("invalid --now")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with exclusive_ledger_lock(args.output_dir):
        return run_locked(args, now)


if __name__ == "__main__":
    raise SystemExit(main())
