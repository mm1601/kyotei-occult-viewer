#!/usr/bin/env python3
"""Build the ClaudeCode TOP10 x Codex buy-logic report block."""

from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path
from typing import Any


PLACE_IDS = {
    "桐生": 1,
    "戸田": 2,
    "江戸川": 3,
    "平和島": 4,
    "多摩川": 5,
    "浜名湖": 6,
    "蒲郡": 7,
    "常滑": 8,
    "津": 9,
    "三国": 10,
    "びわこ": 11,
    "住之江": 12,
    "尼崎": 13,
    "鳴門": 14,
    "丸亀": 15,
    "児島": 16,
    "宮島": 17,
    "徳山": 18,
    "下関": 19,
    "若松": 20,
    "芦屋": 21,
    "福岡": 22,
    "唐津": 23,
    "大村": 24,
}


REPORT_START = "<!-- CLAUDE_TOP10_BUY_LOGIC_START -->"
REPORT_END = "<!-- CLAUDE_TOP10_BUY_LOGIC_END -->"


def clean(text: str) -> str:
    text = re.sub(r"<br\s*/?>", " / ", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def pct(text: str | None) -> float | None:
    if not text:
        return None
    match = re.search(r"(-?\d+(?:\.\d+)?)%", text)
    return float(match.group(1)) if match else None


def split_archive_blocks(source: str) -> dict[str, str]:
    blocks: dict[str, str] = {}
    for match in re.finditer(r'<tr class="accrow" id="acc-([^"]+)"', source):
        key = match.group(1)
        start = match.start()
        next_row = source.find('<tr class="rrow"', match.end())
        end_rank = source.find("</tbody></table></div>", match.end())
        candidates = [pos for pos in [next_row, end_rank] if pos != -1]
        end = min(candidates) if candidates else len(source)
        blocks[key] = source[start:end]
    return blocks


def parse_lane_table(block: str) -> dict[int, dict[str, Any]]:
    table_match = re.search(r"艇\(級/選手\).*?<tbody>(.*?)</tbody>", block, re.S)
    if not table_match:
        return {}
    rows: dict[int, dict[str, Any]] = {}
    for row_html in re.findall(r"<tr>(.*?)</tr>", table_match.group(1), re.S):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.S)
        if len(cells) < 4:
            continue
        lane_match = re.search(r"\b([1-6])\b", clean(cells[0]))
        if not lane_match:
            continue
        lane = int(lane_match.group(1))
        label = clean(cells[0])
        rows[lane] = {
            "lane": lane,
            "label": label,
            "grade": next((g for g in ["A1", "A2", "B1", "B2"] if g in label), ""),
            "local_win_pct": pct(cells[1]),
            "local_top3_pct": pct(cells[2]),
        }
    return rows


def result_map(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    output: dict[str, dict[str, Any]] = {}
    for row in data.get("results", []):
        key = f"{int(row['race_stadium_number'])}-{int(row['race_number'])}"
        trifectas = (row.get("payouts") or {}).get("trifecta") or []
        combination = None
        payout = None
        if trifectas:
            combination = trifectas[0].get("combination")
            payout = trifectas[0].get("payout")
        if not combination:
            places = {}
            for boat in row.get("boats") or []:
                place = boat.get("racer_place_number")
                lane = boat.get("racer_boat_number")
                if place and lane:
                    places[int(place)] = int(lane)
            if all(i in places for i in [1, 2, 3]):
                combination = f"{places[1]}-{places[2]}-{places[3]}"
        output[key] = {
            "trifecta": combination,
            "payout_yen": int(payout) if payout is not None else None,
            "manshu": bool(payout is not None and int(payout) >= 10000),
        }
    return output


def parse_ranking(source: str) -> list[dict[str, Any]]:
    blocks = split_archive_blocks(source)
    rows: list[dict[str, Any]] = []
    for idx, match in enumerate(
        re.finditer(r'<tr class="rrow" data-vr="(\d+)-(\d+)" data-kind="score".*?</tr>', source, re.S),
        1,
    ):
        key = f"{int(match.group(1))}-{int(match.group(2))}"
        row_html = match.group(0)
        text = clean(row_html)
        place_match = re.search(r"([一-龥ぁ-んァ-ヶー]+)(\d+)R", text)
        place_name = place_match.group(1) if place_match else ""
        round_no = int(place_match.group(2)) if place_match else int(match.group(2))
        values = re.findall(r'<td class="p"><b>(\d+)%</b>|<td class="p">(\d+)%</td>', row_html)
        flat_values = [int(a or b) for a, b in values]
        rate = flat_values[0] if flat_values else None
        b1_fly = flat_values[1] if len(flat_values) > 1 else None
        heads = [
            {"lane": int(lane), "prob_pct": int(prob)}
            for lane, prob in re.findall(r"([1-6])号艇(\d+)%", clean(row_html))
        ]
        underdog_match = re.search(r"最低人気([1-6])号艇", clean(row_html))
        block = blocks.get(key, "")
        why_match = re.search(r'<p class="why">(.*?)</p>', block, re.S)
        rows.append(
            {
                "rank": idx,
                "key": key,
                "place_id": int(match.group(1)),
                "place_name": place_name,
                "round": round_no,
                "manshu_rate_pct": rate,
                "b1_fly_pct": b1_fly,
                "head3": heads,
                "lane_stats": parse_lane_table(block),
                "underdog_lane": int(underdog_match.group(1)) if underdog_match else None,
                "why": clean(why_match.group(1)) if why_match else "",
            }
        )
        if len(rows) >= 10:
            break
    return rows


def grade_bonus(grade: str) -> float:
    return {"A1": 10, "A2": 6, "B1": 1, "B2": -4}.get(grade, 0)


def lane_axis_score(race: dict[str, Any], lane: int) -> float:
    stat = race["lane_stats"].get(lane, {})
    top3 = stat.get("local_top3_pct")
    score = float(top3 if top3 is not None else 30.0)
    score += grade_bonus(stat.get("grade", ""))
    if lane == race.get("underdog_lane"):
        score += 18
    if lane == 1:
        if race.get("b1_fly_pct", 0) >= 55 and score >= 50:
            score += 12
        if race.get("b1_fly_pct", 0) >= 75:
            score -= 8
    if lane in [h["lane"] for h in race.get("head3", [])[:2]]:
        score -= 4
    return score


def lane_keshi_score(race: dict[str, Any], lane: int, protected: set[int]) -> float:
    stat = race["lane_stats"].get(lane, {})
    top3 = stat.get("local_top3_pct")
    score = 100.0 - float(top3 if top3 is not None else 35.0)
    if stat.get("grade") in {"B1", "B2"}:
        score += 8
    if lane == race.get("underdog_lane"):
        score -= 25
    if lane in protected:
        score -= 45
    if lane == 1 and race.get("b1_fly_pct", 0) >= 75:
        score += 10
    return score


def add_ticket(tickets: list[str], a: int, b: int, c: int, keshi: int) -> None:
    if len({a, b, c}) != 3 or keshi in {a, b, c}:
        return
    ticket = f"{a}-{b}-{c}"
    if ticket not in tickets:
        tickets.append(ticket)


def ticket_score(ticket: str, race: dict[str, Any], axes: list[int], supports: list[int]) -> float:
    a, b, c = [int(x) for x in ticket.split("-")]
    head_prob = {h["lane"]: h["prob_pct"] for h in race.get("head3", [])}.get(a, 0)
    score = head_prob * 2.0
    score += (lane_axis_score(race, b) + lane_axis_score(race, c)) * 0.35
    if race.get("underdog_lane") in {b, c}:
        score += 22
    if {5, 6}.intersection({b, c}):
        score += 10
    if 1 in {b, c}:
        score += 7
    score += sum(5 for lane in [b, c] if lane in axes)
    score += sum(3 for lane in [b, c] if lane in supports)
    return score


def build_logic(race: dict[str, Any], res: dict[str, Any] | None) -> dict[str, Any]:
    heads = [h["lane"] for h in race.get("head3", [])[:2]]
    if len(heads) < 2:
        heads = (heads + [2, 3])[:2]

    axis_candidates = sorted(
        [lane for lane in range(1, 7) if lane not in heads],
        key=lambda lane: lane_axis_score(race, lane),
        reverse=True,
    )
    axes = axis_candidates[:2]
    protected = set(heads + axes)
    keshi = max(range(1, 7), key=lambda lane: lane_keshi_score(race, lane, protected))

    supports: list[int] = []
    for lane in [race.get("underdog_lane"), 1, 5, 6]:
        if lane and lane not in heads and lane not in axes and lane != keshi and lane not in supports:
            supports.append(lane)
    for lane in sorted(range(1, 7), key=lambda x: lane_axis_score(race, x), reverse=True):
        if lane not in heads and lane not in axes and lane != keshi and lane not in supports:
            supports.append(lane)
    supports = supports[:2] or [lane for lane in range(1, 7) if lane not in heads and lane != keshi][:2]

    tickets: list[str] = []
    for h in heads:
        for a in axes:
            for s in supports:
                add_ticket(tickets, h, a, s, keshi)
                add_ticket(tickets, h, s, a, keshi)

    pool = [lane for lane in range(1, 7) if lane != keshi]
    for h in heads:
        for b in pool:
            for c in pool:
                if len(tickets) >= 18:
                    break
                if b in axes or c in axes or b in supports or c in supports:
                    add_ticket(tickets, h, b, c, keshi)

    tickets = sorted(tickets, key=lambda t: ticket_score(t, race, axes, supports), reverse=True)
    target_points = 12
    if len(tickets) < 10:
        target_points = min(15, max(10, len(tickets)))
    tickets = tickets[:target_points]

    combo = (res or {}).get("trifecta")
    payout = (res or {}).get("payout_yen")
    settled = bool(combo and payout is not None)
    hit = bool(settled and combo in tickets)
    cost = len(tickets) * 100
    return_yen = int(payout) if hit and payout is not None else (0 if settled else None)
    roi_pct = round(return_yen / cost * 100, 1) if settled and cost and return_yen is not None else None

    rank = race.get("rank", 99)
    buy = (
        (rank <= 5 and (race.get("manshu_rate_pct") or 0) >= 24)
        or ((race.get("b1_fly_pct") or 0) >= 75 and (race.get("manshu_rate_pct") or 0) >= 24)
        or (race.get("underdog_lane") is not None and (race.get("manshu_rate_pct") or 0) >= 24)
    )
    if len(tickets) < 10:
        buy = False

    reasons = []
    if rank <= 5:
        reasons.append("ClaudeCodeランキングTOP5圏内")
    if (race.get("b1_fly_pct") or 0) >= 60:
        reasons.append(f"1号艇飛び率{race.get('b1_fly_pct')}%で1着固定を避ける")
    if race.get("underdog_lane"):
        reasons.append(f"最低人気{race['underdog_lane']}号艇を2/3着の妙味として残す")
    if {5, 6}.intersection(set(heads + axes + supports)):
        reasons.append("5/6号艇絡みを残して万舟化を狙う")
    reasons.append("頭はClaudeCodeの非1号艇上位2艇、軸は当地3着内率・級別・過小評価補正で選定")

    return {
        "decision": "買い" if buy else "見送り",
        "head_candidates": heads,
        "axis_candidates": axes,
        "support_candidates": supports,
        "keshi_candidate": keshi,
        "tickets": tickets,
        "points": len(tickets),
        "cost_yen": cost if buy else 0,
        "reference_cost_yen": cost,
        "result": {
            "trifecta": combo,
            "payout_yen": payout,
            "settled": settled,
            "hit": hit,
            "return_yen": return_yen if buy else None,
            "reference_return_yen": return_yen,
            "roi_pct": roi_pct if buy else None,
            "reference_roi_pct": roi_pct,
        },
        "reasons": reasons,
    }


def render_section(date_text: str, rows: list[dict[str, Any]]) -> str:
    bought = [r for r in rows if r["logic"]["decision"] == "買い"]
    planned_cost = sum(r["logic"]["cost_yen"] for r in bought)
    settled_bought = [r for r in bought if r["logic"]["result"].get("settled")]
    settled_cost = sum(r["logic"]["cost_yen"] for r in settled_bought)
    settled_return = sum(r["logic"]["result"].get("return_yen") or 0 for r in settled_bought)
    roi = round(settled_return / settled_cost * 100, 1) if settled_cost else None
    trs = []
    for race in rows:
        logic = race["logic"]
        result = logic["result"]
        result_text = result["trifecta"] or "結果待ち"
        payout = result["payout_yen"]
        payout_text = "結果待ち" if payout is None else f"{payout:,}円"
        if result.get("hit"):
            outcome = f"的中 / {result['reference_roi_pct']}%"
        elif result.get("settled"):
            outcome = f"不的中 / 0%"
        else:
            outcome = "結果待ち"
        if logic["decision"] == "見送り" and result["trifecta"]:
            outcome = f"参考{outcome}"
        trs.append(
            "<tr>"
            f"<td>{race['rank']}</td>"
            f"<td><b>{html.escape(race['place_name'])}{race['round']}R</b><br>"
            f"<span class='muted'>万舟率{race['manshu_rate_pct']}% / 1飛び{race['b1_fly_pct']}%</span></td>"
            f"<td><b>{logic['decision']}</b></td>"
            f"<td>頭 {', '.join(str(x) for x in logic['head_candidates'])}<br>"
            f"軸 {', '.join(str(x) for x in logic['axis_candidates'])}<br>"
            f"消 {logic['keshi_candidate']}</td>"
            f"<td class='buytickets'>{' / '.join(logic['tickets'])}</td>"
            f"<td>{html.escape(' / '.join(logic['reasons'][:3]))}</td>"
            f"<td>{html.escape(result_text)}<br>{html.escape(payout_text)}<br>{html.escape(outcome)}</td>"
            "</tr>"
        )
    return (
        f"{REPORT_START}\n"
        "<section class=\"card buylogic\" id=\"claude-top10-buy-logic\">"
        f"<h2>ClaudeCode TOP10 × Codex買い目ロジック（{html.escape(date_text)}）</h2>"
        "<p class=\"lead\">ClaudeCodeの万舟率ランキングを入口にして、これまでのCodex側ロジックで頭候補・軸候補・消し候補・10〜15点の三連単を組んだ検証表示です。</p>"
        f"<p class=\"stat\">買い対象 {len(bought)}/{len(rows)}R / 予定投資 {planned_cost:,}円 / 確定 {len(settled_bought)}/{len(bought)}R / 確定分回収 {settled_return:,}円 / 回収率 {('--' if roi is None else str(roi) + '%')}</p>"
        "<table><thead><tr><th>順位</th><th>レース</th><th>判定</th><th>候補</th><th>買い目</th><th>理由</th><th>結果・回収率</th></tr></thead><tbody>"
        + "\n".join(trs)
        + "</tbody></table><p class=\"muted\">※見送り行の回収率は、参考として同じ買い目を買っていた場合の単発計算です。</p></section>\n"
        f"{REPORT_END}"
    )


def insert_section(path: Path, section: str) -> bool:
    text = path.read_text(encoding="utf-8")
    original = text
    pattern = re.compile(re.escape(REPORT_START) + r".*?" + re.escape(REPORT_END), re.S)
    if pattern.search(text):
        text = pattern.sub(section, text, count=1)
    else:
        marker = '<details class="card">'
        if marker in text:
            text = text.replace(marker, section + "\n" + marker, 1)
        else:
            text = text.replace("</body>", section + "\n</body>", 1)
    if text != original:
        path.write_text(text, encoding="utf-8")
        return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--source-html", default="manshu.html")
    parser.add_argument("--results-json", required=True)
    parser.add_argument("--json-out")
    parser.add_argument("--patch-html", action="append", default=[])
    args = parser.parse_args()

    source = Path(args.source_html).read_text(encoding="utf-8")
    results = result_map(Path(args.results_json))
    rows = parse_ranking(source)
    for race in rows:
        race["logic"] = build_logic(race, results.get(race["key"]))
        race["result"] = race["logic"]["result"]
    payload = {
        "date": args.date,
        "logic_label": "ClaudeCodeランキングTOP10を入口にしたCodex買い目ロジック",
        "races": rows,
    }
    out_path = Path(args.json_out or f"data/output/claude_top10_buy_logic_{args.date.replace('-', '')}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    section = render_section(args.date, rows)
    for html_path in args.patch_html:
        insert_section(Path(html_path), section)
    print(json.dumps({"json": str(out_path), "patched": args.patch_html, "races": len(rows)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
