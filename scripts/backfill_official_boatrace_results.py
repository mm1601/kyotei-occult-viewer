#!/usr/bin/env python3
"""Backfill missing race results from the official BOATRACE result page."""

from __future__ import annotations

import argparse
import html
import json
import re
import ssl
import subprocess
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    import certifi
except Exception:  # pragma: no cover - certifi is installed in GitHub Actions.
    certifi = None


ROOT = Path(__file__).resolve().parents[1]
JST = ZoneInfo("Asia/Tokyo")


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, payload: dict) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")


def as_int(value):
    if value is None:
        return None
    try:
        return int(str(value).replace(",", "").replace("円", "").strip())
    except ValueError:
        return None


def official_result_url(date_text: str, place_id: int, round_no: int) -> str:
    key = date_text.replace("-", "")
    return (
        "https://www.boatrace.jp/owpc/pc/race/raceresult"
        f"?rno={round_no}&jcd={place_id:02d}&hd={key}"
    )


def fetch_text(url: str) -> str:
    context = ssl.create_default_context(cafile=certifi.where()) if certifi else None
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Codex BOATRACE result backfill)",
            "Accept-Language": "ja,en-US;q=0.8,en;q=0.6",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15, context=context) as response:
            return response.read().decode("utf-8", errors="replace")
    except Exception as exc:
        fallback = subprocess.run(
            ["curl", "-fsSL", "--max-time", "20", url],
            check=False,
            capture_output=True,
            text=True,
        )
        if fallback.returncode == 0 and fallback.stdout:
            return fallback.stdout
        raise exc


def clean_text(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def parse_official_result(text: str) -> dict | None:
    marker = re.search(r"<td[^>]*>\s*3連単\s*</td>", text)
    if not marker:
        return None
    segment = text[marker.start() : marker.start() + 5000]
    numbers = re.findall(r'numberSet1_number[^>]*>\s*(\d+)\s*</span>', segment)
    payout_match = re.search(r'is-payout1[^>]*>\s*(?:&yen;|¥)\s*([0-9,]+)', segment)
    if len(numbers) < 3 or not payout_match:
        return None
    payout = as_int(payout_match.group(1))
    if payout is None:
        return None
    popularity = None
    popularity_match = re.search(
        r'is-payout1[^>]*>\s*(?:&yen;|¥)\s*[0-9,]+\s*</span>\s*</td>\s*<td>\s*([^<]+)\s*</td>',
        segment,
        flags=re.S,
    )
    if popularity_match:
        popularity = as_int(clean_text(popularity_match.group(1)))
    trifecta = "-".join(numbers[:3])
    return {
        "trifecta": trifecta,
        "payout_yen": payout,
        "manshu": payout >= 10000,
        "popularity": popularity,
        "source": "official_boatrace_result_page",
    }


def result_missing(row: dict) -> bool:
    result = row.get("result") or {}
    return result.get("payout_yen") is None or not result.get("trifecta")


def parse_deadline(value) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=JST)
        return parsed.astimezone(JST)
    except ValueError:
        return None


def should_fetch_result(row: dict, now: datetime) -> bool:
    if not result_missing(row):
        return False
    deadline = parse_deadline(row.get("deadline_time"))
    if deadline is None:
        return False
    return deadline <= now


def iter_rows(payload: dict):
    for group_name in ("races", "strict_races", "morning_candidates"):
        for row in payload.get(group_name) or []:
            yield group_name, row


def fetch_result_once(date_text: str, key: tuple[int, int], cache: dict, errors: dict, sleep_sec: float):
    if key in cache:
        return cache[key]
    place_id, round_no = key
    url = official_result_url(date_text, place_id, round_no)
    try:
        result = parse_official_result(fetch_text(url))
        cache[key] = result
        time.sleep(sleep_sec)
        return result
    except Exception as exc:  # noqa: BLE001 - keep this collector non-fatal.
        cache[key] = None
        errors[f"{place_id}:{round_no}"] = str(exc)
        return None


def backfill_file(
    path: Path,
    dry_run: bool = False,
    sleep_sec: float = 0.15,
    cache: dict | None = None,
    shared_errors: dict | None = None,
    now: datetime | None = None,
) -> dict:
    payload = load_json(path)
    date_text = payload.get("date")
    if not date_text:
        return {"path": str(path), "changed": False, "error": "date missing"}

    now = now or datetime.now(JST)
    cache = cache if cache is not None else {}
    shared_errors = shared_errors if shared_errors is not None else {}
    needed = {}
    for _, row in iter_rows(payload):
        if not should_fetch_result(row, now):
            continue
        place_id = row.get("place_id")
        round_no = row.get("round")
        if place_id is None or round_no is None:
            continue
        needed[(int(place_id), int(round_no))] = {
            "place_name": row.get("place_name"),
            "round": int(round_no),
        }

    fetched = {}
    for key, info in sorted(needed.items()):
        result = fetch_result_once(date_text, key, cache, shared_errors, sleep_sec)
        if result:
            fetched[key] = result

    changed = False
    updated = []
    for group_name, row in iter_rows(payload):
        place_id = row.get("place_id")
        round_no = row.get("round")
        if place_id is None or round_no is None:
            continue
        result = fetched.get((int(place_id), int(round_no)))
        if not result or not result_missing(row):
            continue
        old = row.get("result") or {}
        row["result"] = {**old, **result}
        updated.append(
            {
                "group": group_name,
                "race_id": row.get("race_id"),
                "place_name": row.get("place_name"),
                "round": row.get("round"),
                "trifecta": result.get("trifecta"),
                "payout_yen": result.get("payout_yen"),
            }
        )
        changed = True

    if changed and not dry_run:
        payload["official_result_backfilled_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        save_json(path, payload)

    return {
        "path": str(path),
        "needed": len(needed),
        "fetched": len(fetched),
        "updated": len(updated),
        "changed": changed,
        "dry_run": dry_run,
        "updated_rows": updated[:20],
        "errors": {f"{key[0]}:{key[1]}": shared_errors[f"{key[0]}:{key[1]}"] for key in needed if f"{key[0]}:{key[1]}" in shared_errors},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ranking_json", nargs="+", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--sleep", type=float, default=0.15)
    args = parser.parse_args()

    cache = {}
    shared_errors = {}
    now = datetime.now(JST)
    results = [
        backfill_file(path, dry_run=args.dry_run, sleep_sec=args.sleep, cache=cache, shared_errors=shared_errors, now=now)
        for path in args.ranking_json
        if path.exists()
    ]
    print(json.dumps({"version": "official-result-backfill-v1", "results": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
