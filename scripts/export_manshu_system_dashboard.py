#!/usr/bin/env python3
"""Export the current 24-venue sign system as a compact public dashboard JSON."""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", default=str(ROOT))
    parser.add_argument("--date", default="")
    parser.add_argument(
        "--out-dir",
        default=str(ROOT / "data" / "output"),
    )
    return parser.parse_args()


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


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


def newest(paths: list[Path]) -> Path | None:
    existing = [path for path in paths if path.exists()]
    return max(existing, key=lambda path: (path.stat().st_mtime, path.name)) if existing else None


def public_paths(value: Any, source_root: Path) -> Any:
    if isinstance(value, dict):
        return {key: public_paths(item, source_root) for key, item in value.items()}
    if isinstance(value, list):
        return [public_paths(item, source_root) for item in value]
    if isinstance(value, str) and Path(value).is_absolute():
        try:
            return str(Path(value).relative_to(source_root))
        except ValueError:
            return Path(value).name
    return value


def snapshot_summary(shadow: dict[str, Any], target: str) -> dict[str, Any]:
    snapshot = ((shadow or {}).get("snapshots") or {}).get(target) or {}
    return {
        "available": bool(snapshot),
        "expected_roi_pct": as_num(snapshot.get("portfolio_expected_roi_pct")),
        "synthetic_odds": as_num(snapshot.get("synthetic_odds")),
        "positive_ev_tickets": as_int(snapshot.get("positive_ev_ticket_count")),
        "snapshot_at": snapshot.get("snapshot_at"),
        "complete_odds": bool(snapshot.get("complete_ticket_odds")),
    }


def probability_rows(
    boats: Any, *, allow_compact_independent: bool = False
) -> list[dict[str, Any]]:
    if isinstance(boats, dict):
        boat_items = []
        for boat_number, values in boats.items():
            if isinstance(values, dict):
                boat_items.append({"boat_number": boat_number, **values})
    elif isinstance(boats, list):
        boat_items = boats
    else:
        boat_items = []
    rows = []
    for boat in boat_items:
        boat_number = as_int(boat.get("boat_number"))
        if boat_number is None:
            continue
        win_pct = as_num(boat.get("self_ai_win_pct"))
        top3_pct = as_num(boat.get("self_ai_top3_pct"))
        if allow_compact_independent:
            win_pct = win_pct if win_pct is not None else as_num(boat.get("win_pct"))
            top3_pct = top3_pct if top3_pct is not None else as_num(boat.get("top3_pct"))
        if win_pct is None or top3_pct is None:
            continue
        rows.append(
            {
                "boat": boat_number,
                "win_pct": win_pct,
                "top3_pct": top3_pct,
                "general_top3_pct": as_num(boat.get("general_3ren_pct")),
                "source": "independent_composite",
            }
        )
    return sorted(rows, key=lambda row: row["boat"])


def venue_round_key(venue: Any, round_number: Any) -> str:
    round_value = as_int(round_number)
    return f"{venue}:{round_value:02d}" if venue and round_value is not None else ""


def approved_probability_map(
    source_root: Path, date_key: str
) -> dict[str, list[dict[str, Any]]]:
    approved_dir = (
        source_root
        / "data"
        / "input"
        / "boaters_screenshots"
        / "approved"
        / date_key
    )
    result = {}
    for path in sorted(approved_dir.glob("*.json")):
        payload = read_json(path, {})
        rows = probability_rows(payload.get("boats") or {})
        key = venue_round_key(payload.get("place_name"), payload.get("round"))
        if key and len(rows) == 6:
            result[key] = rows
    return result


def probabilities_for_entry(
    probabilities: dict[str, list[dict[str, Any]]], entry: dict[str, Any]
) -> list[dict[str, Any]]:
    frozen = probability_rows(
        entry.get("independent_probabilities") or [],
        allow_compact_independent=True,
    )
    if len(frozen) == 6:
        return frozen
    race_id = str(entry.get("race_id") or "")
    venue_key = venue_round_key(entry.get("place_name"), entry.get("round"))
    return probabilities.get(race_id) or probabilities.get(venue_key) or []


def live_probability_map(
    source_root: Path, date_key: str, monitor: dict[str, Any]
) -> dict[str, list[dict[str, Any]]]:
    candidates = []
    configured = monitor.get("live_ranking_path")
    if configured:
        configured_path = Path(str(configured))
        candidates.append(
            configured_path
            if configured_path.is_absolute()
            else source_root / configured_path
        )
    candidates.append(
        source_root / "data" / "output" / f"boaters_manshu_live_ranking_{date_key}.json"
    )
    path = next((candidate for candidate in candidates if candidate.exists()), None)
    payload = read_json(path, {}) if path else {}
    result = {}
    for race in payload.get("races") or []:
        rows = probability_rows(((race.get("metrics") or {}).get("boats") or []))
        if len(rows) == 6:
            race_id = str(race.get("race_id") or "")
            if race_id:
                result[race_id] = rows
            key = venue_round_key(
                race.get("place_name") or race.get("venue"),
                race.get("round") or race.get("race_number"),
            )
            if key:
                result[key] = rows
    result.update(approved_probability_map(source_root, date_key))
    return result


def signal_row(
    entry: dict[str, Any], probabilities: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    condition = entry.get("condition_snapshot") or {}
    result = entry.get("result") or {}
    legacy_ev = entry.get("ticket_ev_shadow") or {}
    overlay_ev = entry.get("ticket_venue_probability_shadow") or {}
    return {
        "race_id": entry.get("race_id"),
        "date": entry.get("date"),
        "venue": entry.get("place_name"),
        "round": as_int(entry.get("round")),
        "deadline_time": entry.get("deadline_time"),
        "detected_at": entry.get("detected_at"),
        "first_minutes_to_deadline": as_num(entry.get("first_minutes_to_deadline")),
        "status": entry.get("status"),
        "notification_status": entry.get("notification_status"),
        "notification_sent_at": entry.get("notification_sent_at"),
        "notification_ok": entry.get("notification_ok"),
        "rule_status": entry.get("rule_status"),
        "rule_id": entry.get("rule_id"),
        "condition": entry.get("condition"),
        "buy_method": entry.get("buy_method"),
        "data_mode": entry.get("data_mode"),
        "points": as_int(entry.get("points")),
        "tickets": entry.get("tickets") or [],
        "heads": entry.get("heads") or [],
        "axes": entry.get("axes") or [],
        "keshi": as_int(entry.get("keshi")),
        "historical": entry.get("historical") or {},
        "probabilities": probabilities or [],
        "conditions": {
            "wind_speed": as_num(condition.get("wind_speed")),
            "wave_height": as_num(condition.get("wave_height")),
            "boat1_popularity_pct": as_num(condition.get("b1_odds_pct")),
            "boat1_ai_win_pct": as_num(condition.get("b1_ai_win")),
            "boat1_escape_pct": as_num(condition.get("b1_nige_pct")),
            "boat1_exhibition_rank": as_int(condition.get("b1_tenji_rank")),
            "boat1_lap_rank": as_int(condition.get("b1_lap_rank")),
            "boat1_average_diff": as_num(condition.get("b1_avg_diff")),
            "outer56_average_diff": as_num(condition.get("outer56_avg_diff")),
        },
        "legacy_ev": {
            "status": legacy_ev.get("status"),
            "t10": snapshot_summary(legacy_ev, "t10"),
            "t5": snapshot_summary(legacy_ev, "t5"),
        },
        "probability_overlay_ev": {
            "status": overlay_ev.get("status"),
            "t10": snapshot_summary(overlay_ev, "t10"),
            "t5": snapshot_summary(overlay_ev, "t5"),
        },
        "result": {
            "trifecta": result.get("trifecta") or entry.get("result_trifecta"),
            "payout_yen": as_int(result.get("payout_yen") or entry.get("result_payout_yen")),
            "manshu": bool(result.get("manshu") or entry.get("result_manshu")),
            "hit": entry.get("hit"),
            "profit_yen": as_int(entry.get("profit_yen")),
        },
    }


def monitor_row(row: dict[str, Any]) -> dict[str, Any]:
    selection = row.get("selection") or {}
    return {
        "race_id": row.get("race_id"),
        "venue": row.get("place_name"),
        "round": as_int(row.get("round")),
        "status": row.get("status"),
        "minutes_to_deadline": as_num(row.get("minutes_to_deadline")),
        "preview_ready": bool(row.get("preview_ready")),
        "fetch_reason": row.get("fetch_reason"),
        "near_miss_level": row.get("near_miss_level"),
        "near_miss_summary": row.get("near_miss_summary"),
        "near_miss_reasons": row.get("near_miss_reasons") or [],
        "near_miss_positives": row.get("near_miss_positives") or [],
        "post_exhibition_manshu_rate_pct": as_num(
            row.get("post_exhibition_manshu_rate_pct")
        ),
        "heads": selection.get("heads") or [],
        "axes": selection.get("axes") or [],
        "keshi": as_int(selection.get("keshi")),
    }


def rule_row(rule: dict[str, Any]) -> dict[str, Any]:
    return {
        "venue": rule.get("venue"),
        "status": rule.get("status"),
        "condition": rule.get("condition"),
        "buy_method": rule.get("buy_method"),
        "base_id": rule.get("base_id"),
        "context_id": rule.get("context_id"),
        "template_id": rule.get("template_id"),
        "historical": rule.get("historical") or {},
    }


def build_payload(source_root: Path, date_text: str) -> dict[str, Any]:
    output_dir = source_root / "data" / "output"
    if date_text:
        monitor_path = output_dir / f"boaters_manshu_alerts_{date_text.replace('-', '')}.json"
    else:
        monitor_path = newest(list(output_dir.glob("boaters_manshu_alerts_????????.json")))
    if monitor_path is None or not monitor_path.exists():
        raise FileNotFoundError("monitor JSON was not found")
    monitor = read_json(monitor_path, {})
    date_text = str(monitor.get("date") or date_text)
    date_key = date_text.replace("-", "")

    forward_path = (
        output_dir
        / "forward_validation"
        / f"original_boaters_24_shadow_{date_key}.json"
    )
    forward = read_json(forward_path, {})
    forward_summary = read_json(
        output_dir
        / "forward_validation"
        / "original_boaters_24_shadow_summary.json",
        {},
    )
    rules = read_json(
        source_root / "data" / "config" / "original_boaters_24_rules_v1.json",
        {},
    )
    probability_report_path = newest(
        list((source_root / "reports" / "venue_probability_overlay").glob("*.json"))
    )
    probability_report = read_json(probability_report_path, {}) if probability_report_path else {}
    independent_report_path = newest(
        list((source_root / "reports" / "independent_probability").glob("*.json"))
    )
    independent_report = (
        read_json(independent_report_path, {}) if independent_report_path else {}
    )

    inspected = monitor.get("inspected") or []
    status_counts: dict[str, int] = {}
    for row in inspected:
        status = str(row.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    active_monitor_rows = [
        monitor_row(row)
        for row in inspected
        if row.get("status") != "outside_window"
        or abs(as_num(row.get("minutes_to_deadline")) or 9999) <= 30
    ]
    active_monitor_rows = active_monitor_rows[-40:]

    probabilities = live_probability_map(source_root, date_key, monitor)
    signals = [
        signal_row(entry, probabilities_for_entry(probabilities, entry))
        for entry in forward.get("entries") or []
    ]
    signals.sort(key=lambda row: str(row.get("detected_at") or ""), reverse=True)
    performance = (
        forward_summary.get("performance")
        or forward.get("summary")
        or (monitor.get("original_boaters_24_forward") or {}).get("performance")
        or {}
    )
    progress = (
        forward_summary.get("progress")
        or (monitor.get("original_boaters_24_forward") or {}).get("progress")
        or {}
    )
    test_metrics = probability_report.get("test_metrics") or {}
    sign_comparison = probability_report.get("sign_comparison") or {}
    historical_24_sign = dict(sign_comparison.get("production") or {})
    if not historical_24_sign.get("race_count"):
        hits = as_num(historical_24_sign.get("hits"))
        hit_rate_pct = as_num(historical_24_sign.get("hit_rate_pct"))
        if hits is not None and hit_rate_pct:
            historical_24_sign["race_count"] = round(hits * 100 / hit_rate_pct)
    push = monitor.get("push") or {}
    payload = {
        "version": "manshu-system-dashboard-v1",
        "date": date_text,
        "exported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_generated_at": monitor.get("generated_at"),
        "source_monitor_file": str(monitor_path.relative_to(source_root)),
        "system": {
            "name": "24場サイン管制盤",
            "mode": "all_races_venue_sign_only",
            "live_source": monitor.get("live_source"),
            "lookahead_minutes": as_num(monitor.get("lookahead_minutes")),
            "signal_count": len(signals),
            "monitored_count": len(inspected),
            "checked_count": status_counts.get("checked", 0),
            "preview_ready_count": sum(bool(row.get("preview_ready")) for row in inspected),
            "fetch_failed_count": status_counts.get("fetch_failed", 0),
            "notifications_sent": as_int(push.get("sent")) or 0,
            "notifications_failed": len(push.get("errors") or []),
            "status_counts": status_counts,
        },
        "models": {
            "independent_probability": {
                "available": bool(independent_report),
                "version": independent_report.get("version"),
                "generated_at": independent_report.get("generated_at"),
                "selected_candidate": independent_report.get("selected_candidate"),
                "feature_count": as_int(independent_report.get("feature_count")),
                "source_policy": independent_report.get("source_policy") or {},
            },
            "probability_overlay": public_paths(
                monitor.get("venue_probability_overlay") or {}, source_root
            ),
            "position_model": public_paths(
                monitor.get("trifecta_position_model") or {}, source_root
            ),
            "factor_dictionaries": public_paths(
                monitor.get("factor_dictionaries") or {}, source_root
            ),
        },
        "signals": signals,
        "monitor": active_monitor_rows,
        "validation": {
            "progress": progress,
            "forward": performance,
            "historical_24_sign": historical_24_sign,
            "probability_baseline": test_metrics.get("legacy") or {},
            "probability_overlay": test_metrics.get("overlay") or {},
            "probability_scales": probability_report.get("selected_scales") or {},
            "probability_adjustments": probability_report.get("test_adjustment_summary") or {},
            "probability_report": (
                str(probability_report_path.relative_to(source_root))
                if probability_report_path
                else ""
            ),
            "independent_probability": (
                independent_report.get("test_metrics") or {}
            ),
            "independent_probability_report": (
                str(independent_report_path.relative_to(source_root))
                if independent_report_path
                else ""
            ),
        },
        "rules": [rule_row(rule) for rule in rules.get("rules") or []],
        "notes": [
            "24場サインの条件と買い目は現行のままです。",
            "1着率・3着内率はBOATERS値ではなく、独自複合モデルの確率です。",
            "場別確率補正は影運用で、本番買い目と通知を変更しません。",
            "過去回収率は将来の利益を保証しません。",
        ],
    }
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    args = parse_args()
    source_root = Path(args.source_root).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    payload = build_payload(source_root, args.date)
    date_key = payload["date"].replace("-", "")
    latest_path = out_dir / "manshu_system_dashboard_latest.json"
    dated_path = out_dir / f"manshu_system_dashboard_{date_key}.json"
    write_json(latest_path, payload)
    write_json(dated_path, payload)
    print(
        json.dumps(
            {
                "ok": True,
                "date": payload["date"],
                "signals": len(payload["signals"]),
                "rules": len(payload["rules"]),
                "latest": str(latest_path),
                "dated": str(dated_path),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
