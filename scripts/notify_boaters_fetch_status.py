#!/usr/bin/env python3
"""Send a compact ntfy status after BOATERS acquisition/result refresh."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_OUT = ROOT / "data" / "output"
JST = ZoneInfo("Asia/Tokyo")

sys.path.insert(0, str(ROOT / "scripts"))
from monitor_boaters_manshu_alerts import load_push_config, send_ntfy  # noqa: E402


def today_jst() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d")


def date_key(date_text: str) -> str:
    return date_text.replace("-", "")


def load_json(path: Path, default):
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")


def alert_path(date_text: str) -> Path:
    return PUBLIC_OUT / f"boaters_manshu_alerts_{date_key(date_text)}.json"


def ranking_path(date_text: str) -> Path:
    return PUBLIC_OUT / f"boaters_manshu_ranking_{date_key(date_text)}.json"


def state_path(date_text: str) -> Path:
    return PUBLIC_OUT / f"boaters_fetch_status_state_{date_key(date_text)}.json"


def output_path(date_text: str) -> Path:
    return PUBLIC_OUT / f"boaters_fetch_status_{date_key(date_text)}.json"


def race_label(item: dict) -> str:
    place = item.get("place_name") or "不明"
    round_no = item.get("round") or "?"
    return f"{place}{round_no}R"


def race_key(item: dict):
    return item.get("race_id") or (item.get("place_name"), item.get("round"))


def fmt_pct(value) -> str:
    try:
        if value is None:
            return "--"
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return "--"


def as_float(value):
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def summarize_fetch(alert_payload: dict) -> dict:
    inspected = alert_payload.get("inspected") or []
    success_statuses = {"checked", "backfilled_missing_exhibition"}
    successes = [item for item in inspected if item.get("status") in success_statuses]
    failures = [item for item in inspected if item.get("status") == "fetch_failed"]
    live_failures = [item for item in inspected if item.get("status") == "live_ranking_failed"]
    ranking_missing = [item for item in inspected if item.get("status") == "skip_no_ranking"]
    preview_ready = [item for item in successes if item.get("preview_ready")]
    full_attempts = successes + failures

    return {
        "attempted_count": len(full_attempts),
        "success_count": len(successes),
        "preview_ready_count": len(preview_ready),
        "failure_count": len(failures),
        "live_failure_count": len(live_failures),
        "ranking_missing_count": len(ranking_missing),
        "successes": successes,
        "failures": failures,
        "live_failures": live_failures,
        "ranking_missing": ranking_missing,
    }


def summarize_results(ranking_payload: dict) -> dict:
    rows = ranking_payload.get("races") or []
    settled = []
    manshu = []
    for row in rows:
        result = row.get("result") or {}
        payout = result.get("payout_yen")
        if payout is None:
            continue
        settled.append(row)
        try:
            if int(payout) >= 10000:
                manshu.append(row)
        except (TypeError, ValueError):
            pass
    return {
        "top_count": len(rows),
        "settled_count": len(settled),
        "manshu_count": len(manshu),
        "settled": settled,
        "manshu": manshu,
    }


def decision_text(item: dict) -> str:
    if item.get("core_buy_ready"):
        return "本命"
    if item.get("subcore_buy_ready"):
        return "準本命"
    if item.get("preview_ready"):
        return "見送り"
    if item.get("status") in {"checked", "backfilled_missing_exhibition"}:
        return "展示不足"
    return str(item.get("status") or "")


def success_line(item: dict) -> str:
    return (
        f"{race_label(item)} {decision_text(item)} "
        f"{fmt_pct(item.get('post_exhibition_manshu_rate_pct'))}"
    ).strip()


def failure_line(item: dict) -> str:
    error = str(item.get("error") or "").replace("\n", " ")
    if len(error) > 80:
        error = error[:77] + "..."
    return f"{race_label(item)} {error}".strip()


def result_line(row: dict) -> str:
    result = row.get("result") or {}
    trifecta = result.get("trifecta") or "-"
    payout = result.get("payout_yen")
    payout_text = f"{int(payout):,}円" if isinstance(payout, int) else f"{payout}円"
    return f"{race_label(row)} {trifecta} {payout_text}"


def result_text(row: dict | None) -> str:
    if not row:
        return "結果待ち"
    result = row.get("result") or {}
    payout = result.get("payout_yen")
    if payout is None:
        return "結果待ち"
    trifecta = result.get("trifecta") or "-"
    payout_text = f"{int(payout):,}円" if isinstance(payout, int) else f"{payout}円"
    return f"結果 {trifecta} {payout_text}"


def combined_status_line(fetch_item: dict, result_by_key: dict) -> str:
    return (
        f"{race_label(fetch_item)} {decision_text(fetch_item)} "
        f"{fmt_pct(fetch_item.get('post_exhibition_manshu_rate_pct'))} / "
        f"{result_text(result_by_key.get(race_key(fetch_item)))}"
    ).strip()


def is_display_target(item: dict) -> bool:
    source = str(item.get("source") or "")
    if source == "morning_top":
        return True
    rate = as_float(item.get("post_exhibition_manshu_rate_pct")) or 0.0
    return source == "late_riser" and rate >= 40.0


def display_fetch_items(fetch_summary: dict) -> list[dict]:
    return [item for item in fetch_summary["successes"] if is_display_target(item)]


def relevant_result_rows(fetch_items: list[dict], result_summary: dict) -> list[dict]:
    result_by_key = {race_key(row): row for row in result_summary["settled"]}
    rows = []
    seen = set()
    for item in fetch_items:
        key = race_key(item)
        if key in seen:
            continue
        seen.add(key)
        row = result_by_key.get(key)
        if row:
            rows.append(row)
    return rows


def build_message(date_text: str, fetch_summary: dict, result_summary: dict, previous_result_count: int | None) -> tuple[str, str, bool]:
    display_items = display_fetch_items(fetch_summary)
    display_results = relevant_result_rows(display_items, result_summary)
    display_manshu_count = 0
    for row in display_results:
        try:
            if int((row.get("result") or {}).get("payout_yen") or 0) >= 10000:
                display_manshu_count += 1
        except (TypeError, ValueError):
            pass

    has_fetch_event = (
        fetch_summary["attempted_count"] > 0
        or fetch_summary["failure_count"] > 0
        or fetch_summary["live_failure_count"] > 0
        or fetch_summary["ranking_missing_count"] > 0
    )
    result_count = len(display_results)
    result_changed = previous_result_count is None or result_count != previous_result_count
    should_notify = has_fetch_event or result_changed

    if fetch_summary["failure_count"] or fetch_summary["live_failure_count"] or fetch_summary["ranking_missing_count"]:
        title = "BOATERS取得ステータス: 要確認"
    elif fetch_summary["attempted_count"] or result_changed:
        title = "BOATERS取得ステータス"
    else:
        title = "BOATERS取得ステータス: 変化なし"

    lines = [f"{date_text} 取得確認"]
    if fetch_summary["attempted_count"]:
        lines.append(
            "BOATERS展示/AI: "
            f"{fetch_summary['success_count']}/{fetch_summary['attempted_count']}R取得 "
            f"(展示6艇揃い {fetch_summary['preview_ready_count']}R)"
        )
    elif fetch_summary["ranking_missing_count"]:
        lines.append("BOATERS展示/AI: 朝ランキングJSONがなく取得対象を作れません")
    else:
        lines.append("BOATERS展示/AI: 今回の取得対象なし")

    lines.append(
        "結果反映: "
        f"監視/急上昇{len(display_items)}R中 {len(display_results)}R確定 "
        f"/ 万舟 {display_manshu_count}R"
    )
    if previous_result_count is not None:
        delta = result_summary["settled_count"] - previous_result_count
        if delta:
            sign = "+" if delta > 0 else ""
            lines.append(f"結果の増減: {sign}{delta}R")

    result_by_key = {race_key(row): row for row in result_summary["settled"]}
    successes = display_items[:8]
    if successes:
        lines.append("")
        lines.append("監視/急上昇レース状況:")
        lines.extend(f"- {combined_status_line(item, result_by_key)}" for item in successes)
        if len(display_items) > len(successes):
            lines.append(f"- ほか{len(display_items) - len(successes)}R")

    failures = (fetch_summary["failures"] + fetch_summary["live_failures"])[:5]
    if failures:
        lines.append("")
        lines.append("取得できなかったレース:")
        lines.extend(f"- {failure_line(item)}" for item in failures)

    return title, "\n".join(lines), should_notify


def fingerprint(date_text: str, fetch_summary: dict, result_summary: dict) -> str:
    targets = display_fetch_items(fetch_summary)
    target_results = relevant_result_rows(targets, result_summary)
    payload = {
        "date": date_text,
        "success": [
            [
                item.get("race_id"),
                item.get("status"),
                item.get("preview_ready"),
                item.get("core_buy_ready"),
                item.get("subcore_buy_ready"),
                item.get("post_exhibition_manshu_rate_pct"),
            ]
            for item in fetch_summary["successes"]
        ],
        "display_targets": [
            [
                item.get("race_id"),
                item.get("source"),
                item.get("post_exhibition_manshu_rate_pct"),
                item.get("core_buy_ready"),
                item.get("subcore_buy_ready"),
            ]
            for item in targets
        ],
        "failure": [[item.get("race_id"), item.get("status"), item.get("error")] for item in fetch_summary["failures"]],
        "live_failure": [[item.get("source"), item.get("error")] for item in fetch_summary["live_failures"]],
        "ranking_missing": bool(fetch_summary["ranking_missing_count"]),
        "settled_count": len(target_results),
        "manshu_count": sum(1 for row in target_results if int((row.get("result") or {}).get("payout_yen") or 0) >= 10000),
        "settled_ids": [row.get("race_id") for row in target_results],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=today_jst())
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="Send even if the same status was already notified.")
    args = parser.parse_args()

    alerts = load_json(alert_path(args.date), {})
    ranking = load_json(ranking_path(args.date), {})
    state = load_json(state_path(args.date), {"sent": {}})
    fetch_summary = summarize_fetch(alerts)
    result_summary = summarize_results(ranking)
    targets = display_fetch_items(fetch_summary)
    target_results = relevant_result_rows(targets, result_summary)
    previous_result_count = state.get("last_relevant_result_count")
    title, message, should_notify = build_message(args.date, fetch_summary, result_summary, previous_result_count)
    key = fingerprint(args.date, fetch_summary, result_summary)

    output = {
        "version": "boaters-fetch-status-v1",
        "date": args.date,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "alert_path": str(alert_path(args.date)),
        "ranking_path": str(ranking_path(args.date)),
        "status_key": key,
        "should_notify": should_notify,
        "title": title,
        "message": message,
        "fetch_summary": {k: v for k, v in fetch_summary.items() if not isinstance(v, list)},
        "result_summary": {
            **{k: v for k, v in result_summary.items() if not isinstance(v, list)},
            "relevant_target_count": len(targets),
            "relevant_settled_count": len(target_results),
            "relevant_manshu_count": sum(
                1 for row in target_results if int((row.get("result") or {}).get("payout_yen") or 0) >= 10000
            ),
        },
    }

    already_sent = bool((state.get("sent") or {}).get(key))
    if should_notify and (args.force or not already_sent) and not args.dry_run:
        config = load_push_config()
        priority = "urgent" if "要確認" in title else "default"
        output["push"] = send_ntfy(config, title, message, tags="white_check_mark,boat", priority=priority)
        if output["push"].get("ok"):
            state.setdefault("sent", {})[key] = output["generated_at"]
    else:
        output["push"] = {
            "enabled": False,
            "reason": "dry_run" if args.dry_run else ("duplicate_or_no_change" if already_sent or not should_notify else "not_sent"),
        }

    state["last_result_count"] = result_summary["settled_count"]
    state["last_relevant_result_count"] = len(target_results)
    state["updated_at"] = output["generated_at"]
    if not args.dry_run:
        save_json(state_path(args.date), state)
        save_json(output_path(args.date), output)
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
