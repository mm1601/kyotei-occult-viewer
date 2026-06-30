#!/usr/bin/env python3
"""Backfill missing race results from the official BOATRACE result page."""

from __future__ import annotations

import argparse
import html
import json
import re
import ssl
import time
import urllib.request
from pathlib import Path

try:
    import certifi
except Exception:  # pragma: no cover - certifi is installed in GitHub Actions.
    certifi = None


ROOT = Path(__file__).resolve().parents[1]


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
    with urllib.request.urlopen(request, timeout=20, context=context) as response:
        return response.read().decode("utf-8", errors="replace")


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


def iter_rows(payload: dict):
    for group_name in ("races", "strict_races", "morning_candidates"):
        for row in payload.get(group_name) or []:
            yield group_name, row


def backfill_file(path: Path, dry_run: bool = False, sleep_sec: float = 0.15) -> dict:
    payload = load_json(path)
    date_text = payload.get("date")
    if not date_text:
        return {"path": str(path), "changed": False, "error": "date missing"}

    needed = {}
    for _, row in iter_rows(payload):
        if not result_missing(row):
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
    errors = {}
    for key, info in sorted(needed.items()):
        place_id, round_no = key
        url = official_result_url(date_text, place_id, round_no)
        try:
            result = parse_official_result(fetch_text(url))
            if result:
                fetched[key] = result
            time.sleep(sleep_sec)
        except Exception as exc:  # noqa: BLE001 - keep this collector non-fatal.
            errors[f"{place_id}:{round_no}"] = str(exc)

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
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ranking_json", nargs="+", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--sleep", type=float, default=0.15)
    args = parser.parse_args()

    results = [backfill_file(path, dry_run=args.dry_run, sleep_sec=args.sleep) for path in args.ranking_json if path.exists()]
    print(json.dumps({"version": "official-result-backfill-v1", "results": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
