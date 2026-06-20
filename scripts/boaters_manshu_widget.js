(function () {
  "use strict";

  function pageDate() {
    if (window.RDATE) return window.RDATE;
    var m = document.body && document.body.textContent.match(/\d{4}-\d{2}-\d{2}/);
    return m ? m[0] : "";
  }

  function assetPrefix() {
    return location.pathname.indexOf("/manshu/") >= 0 ? "../" : "";
  }

  var STATE = {
    data: null,
    resultMap: {}
  };

  function dataUrl(date) {
    return assetPrefix() + "data/output/boaters_manshu_ranking_" + date.replace(/-/g, "") + ".json";
  }

  function resultUrl(date) {
    return "https://boatraceopenapi.github.io/results/v2/" + date.slice(0, 4) + "/" + date.replace(/-/g, "") + ".json";
  }

  function fmtPct(v) {
    return v == null ? "--" : Number(v).toFixed(2).replace(/\.00$/, "") + "%";
  }

  function fmtSec(v) {
    return v == null ? "--" : Number(v).toFixed(2).replace(/0$/, "").replace(/\.0$/, "");
  }

  function fmtYen(v) {
    return v == null ? "結果待ち" : Number(v).toLocaleString("ja-JP") + "円";
  }

  function raceKey(r) {
    if (r.place_id && r.round) return Number(r.place_id) + "-" + Number(r.round);
    var id = String(r.race_id || "");
    var m = id.match(/^\d{4}-\d{2}-\d{2}(\d{2})(\d{2})/);
    if (m) return Number(m[1]) + "-" + Number(m[2]);
    return "";
  }

  function trifectaOf(r) {
    var t = (r.payouts && r.payouts.trifecta && r.payouts.trifecta[0]) || null;
    if (t && t.payout != null) return { combination: t.combination, payout_yen: t.payout };
    var pl = {};
    (r.boats || []).forEach(function (b) {
      if (b.racer_place_number) pl[b.racer_place_number] = b.racer_boat_number;
    });
    if (pl[1] && pl[2] && pl[3]) return { combination: pl[1] + "-" + pl[2] + "-" + pl[3], payout_yen: null };
    return null;
  }

  function mergedResult(r) {
    var key = raceKey(r);
    var live = key && STATE.resultMap[key];
    var base = r.result || {};
    var combo = live && live.combination ? live.combination : base.trifecta;
    var payout = live && live.payout_yen != null ? live.payout_yen : base.payout_yen;
    return {
      trifecta: combo || "",
      payout_yen: payout == null ? null : Number(payout),
      manshu: payout != null && Number(payout) >= 10000
    };
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function injectStyle() {
    if (document.getElementById("boaters-manshu-style")) return;
    var style = document.createElement("style");
    style.id = "boaters-manshu-style";
    style.textContent = [
      ".boaters-manshu{border-left-color:#dc2626;position:relative;overflow:hidden}",
      ".boaters-manshu h2{color:#991b1b}",
      ".boaters-manshu .bm-summary{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0 14px}",
      ".boaters-manshu .bm-chip{background:#fff7ed;border:1px solid #fed7aa;border-radius:999px;color:#9a3412;font-size:12px;font-weight:700;padding:6px 10px}",
      ".boaters-manshu .bm-chip.hot{background:#fee2e2;border-color:#fecaca;color:#991b1b}",
      ".boaters-manshu .bm-table{width:100%;border-collapse:collapse;margin-top:8px}",
      ".boaters-manshu .bm-table th,.boaters-manshu .bm-table td{border-top:1px solid #e5e7eb;padding:9px 8px;text-align:left;vertical-align:top}",
      ".boaters-manshu .bm-table th{font-size:12px;color:#475569;background:#f8fafc}",
      ".boaters-manshu .bm-rate{font-size:20px;font-weight:800;color:#dc2626;white-space:nowrap}",
      ".boaters-manshu .bm-race{font-weight:800;color:#111827;white-space:nowrap}",
      ".boaters-manshu .bm-status{display:inline-block;border-radius:999px;background:#fee2e2;color:#991b1b;font-size:11px;font-weight:800;padding:2px 7px;margin-top:4px}",
      ".boaters-manshu .bm-status.wait{background:#e0f2fe;color:#0369a1}",
      ".boaters-manshu .bm-cond{color:#374151;font-size:13px;line-height:1.45;max-width:680px}",
      ".boaters-manshu .bm-mini{color:#64748b;font-size:12px;line-height:1.55}",
      ".boaters-manshu .bm-subhead{font-size:15px;font-weight:900;color:#334155;margin:18px 0 4px}",
      ".boaters-manshu .bm-hit{color:#dc2626;font-weight:800}",
      ".boaters-manshu .bm-miss{color:#475569;font-weight:700}",
      "@media(max-width:720px){.boaters-manshu .bm-table,.boaters-manshu .bm-table thead,.boaters-manshu .bm-table tbody,.boaters-manshu .bm-table tr,.boaters-manshu .bm-table th,.boaters-manshu .bm-table td{display:block}.boaters-manshu .bm-table thead{display:none}.boaters-manshu .bm-table tr{border-top:1px solid #e5e7eb;padding:10px 0}.boaters-manshu .bm-table td{border:0;padding:4px 0}.boaters-manshu .bm-rate{font-size:18px}}"
    ].join("");
    document.head.appendChild(style);
  }

  function raceRow(r) {
    var result = mergedResult(r);
    var metrics = r.metrics || {};
    var manshu = result.manshu;
    var deadline = r.deadline_time ? String(r.deadline_time).slice(11, 16) : "--:--";
    var status = r.status || (metrics.tenji_boats >= 6 || metrics.isshu_boats >= 6 ? "確定" : "展示待ち");
    var statusClass = status === "展示待ち" ? " wait" : "";
    var edgeText = "";
    var doubleText = "";
    var superSlitText = "";
    var summerText = "";
    var slitText = "";
    var joshiText = metrics.is_joshi ? " / 女子戦" : "";
    if (metrics.double_time_boats) {
      doubleText = " / DT " + esc(String(metrics.double_time_boats));
    }
    if (metrics.super_slit_boats) {
      superSlitText = " / SSA " + esc(String(metrics.super_slit_boats));
    }
    if (metrics.b1_summer_isshu_factor) {
      var delta = Number(metrics.b1_summer_nige_delta_pp || 0);
      summerText = " / 夏1周 逃げ" + (delta > 0 ? "+" : "") + esc(String(delta)) + "pt";
    }
    if (metrics.slit_shape_label) {
      slitText = " / 隊形 " + esc(String(metrics.slit_shape_label));
    }
    if (r.composite_edge_bonus_pct) {
      var edges = (r.composite_edges || [])
        .filter(function (edge) { return Number(edge.bonus_pct || 0) > 0; })
        .slice(0, 2)
        .map(function (edge) { return edge.label; })
        .join(" / ");
      edgeText = "<br><span class=\"bm-mini\">Codex複合補正 " + esc(fmtPct(r.composite_edge_bonus_pct)) + " / 元率 " + esc(fmtPct(r.base_manshu_rate_pct || r.composite_edge_base_rate_pct)) + (edges ? " / " + esc(edges) : "") + "</span>";
    }
    return [
      "<tr>",
      "<td><span class=\"bm-rate\">" + esc(fmtPct(r.manshu_rate_pct)) + "</span><br><span class=\"bm-mini\">直近 " + esc(fmtPct(r.recent_rate_pct)) + "</span>" + edgeText + "</td>",
      "<td><span class=\"bm-race\">" + esc(r.rank) + ". " + esc(r.place_name) + esc(r.round) + "R</span><br><span class=\"bm-mini\">" + esc(deadline) + "締切 / ロジック" + esc(r.matched_logic_count) + "件</span><br><span class=\"bm-status" + statusClass + "\">" + esc(status) + "</span></td>",
      "<td class=\"bm-cond\">" + esc(r.condition) + "<br><span class=\"bm-mini\">1号艇 AI予測 " + esc(fmtPct(metrics.boat1_ai_prediction_pct)) + " / AI+一般3連対 " + esc(fmtPct(metrics.boat1_ai_plus)) + " / 展示+1周平均との差 " + esc(fmtSec(metrics.boat1_avg_isshu_diff)) + " / 展示+1周平均 " + esc(fmtSec(metrics.avg_exhibit_combo_time)) + " / 展示 " + esc(fmtSec(metrics.boat1_tenji_time)) + " / 1周 " + esc(fmtSec(metrics.boat1_isshu_time)) + " / 5・6号艇 展示+1周平均との差 " + esc(fmtSec(metrics.outer56_best_avg_isshu_diff)) + joshiText + doubleText + superSlitText + summerText + slitText + "</span></td>",
      "<td><b>" + esc(result.trifecta || "--") + "</b><br><span class=\"" + (manshu ? "bm-hit" : "bm-miss") + "\">" + esc(fmtYen(result.payout_yen)) + (manshu ? " 万舟" : "") + "</span></td>",
      "</tr>"
    ].join("");
  }

  function render(data) {
    var oldRank = document.querySelector(".card.rank");
    var existing = document.getElementById("boaters-manshu-card");
    if (existing) existing.remove();
    if (!oldRank) oldRank = document.querySelector("footer");
    if (!oldRank) return;
    var summary = data.summary || {};
    var strictRows = data.strict_races || [];
    var strictHtml = strictRows.length ? [
      "<div class=\"bm-subhead\">Codex厳選ランキング TOP" + esc(strictRows.length) + "</div>",
      "<p class=\"lead\">過去検証で万舟率" + esc(fmtPct(data.threshold_pct)) + "以上だった条件に一致したレースです。会場別の強い型をそのまま残しています。</p>",
      "<table class=\"bm-table\"><thead><tr><th>万舟率</th><th>レース</th><th>該当ロジック・展示根拠</th><th>結果</th></tr></thead><tbody>",
      strictRows.slice(0, 10).map(raceRow).join(""),
      "</tbody></table>"
    ].join("") : "";
    var section = document.createElement("section");
    section.id = "boaters-manshu-card";
    section.className = "card boaters-manshu";
    section.innerHTML = [
      "<h2>Codex全場ランキング TOP10</h2>",
      "<p class=\"lead\"><b>" + esc(data.logic_label || "Codex全場ランキング") + "</b>で算出。会場指定だけで偏らないよう、1号艇弱化、外枠上振れ、展示タイム・1周タイム、展示+1周平均との差、AI+最下位の穴/消し判定、スリット隊形、女子戦攻略ファクターを総合評価しています。全場を見渡すため、同一会場は最大2Rまで表示します。</p>",
      "<div class=\"bm-summary\">",
      "<span class=\"bm-chip hot\">全場TOP" + esc(summary.displayed_top_n || 0) + "</span>",
      "<span class=\"bm-chip\">厳選TOP" + esc(summary.strict_displayed_top_n || 0) + "</span>",
      "<span class=\"bm-chip\">万舟 " + esc(summary.manshu_hits_top_n || 0) + "/" + esc(summary.settled_top_n || 0) + "本</span>",
      "<span class=\"bm-chip\">展示6艇取得 " + esc(summary.races_with_full_tenji || 0) + "R</span>",
      "<span class=\"bm-chip\">1周6艇取得 " + esc(summary.races_with_full_isshu || 0) + "R</span>",
      "</div>",
      "<table class=\"bm-table\"><thead><tr><th>万舟率</th><th>レース</th><th>該当ロジック・展示根拠</th><th>結果</th></tr></thead><tbody>",
      (data.races || []).slice(0, 10).map(raceRow).join(""),
      "</tbody></table>",
      strictHtml,
      "<p class=\"muted\">※これは万舟が出やすい条件のランキングです。買い目や利益を保証するものではありません。</p>"
    ].join("");
    if (oldRank.classList && oldRank.classList.contains("rank")) {
      oldRank.parentNode.replaceChild(section, oldRank);
    } else {
      oldRank.parentNode.insertBefore(section, oldRank);
    }
  }

  async function loadResults(date) {
    try {
      var res = await fetch(resultUrl(date), { cache: "no-store" });
      if (!res.ok) return;
      var data = await res.json();
      STATE.resultMap = {};
      (data.results || []).forEach(function (r) {
        var tri = trifectaOf(r);
        if (tri) STATE.resultMap[Number(r.race_stadium_number) + "-" + Number(r.race_number)] = tri;
      });
      if (STATE.data) render(STATE.data);
    } catch (e) {
      // Result JSON may not exist yet during the day.
    }
  }

  async function load() {
    injectStyle();
    var date = pageDate();
    if (!date) return;
    try {
      var res = await fetch(dataUrl(date), { cache: "no-store" });
      if (!res.ok) return;
      STATE.data = await res.json();
      render(STATE.data);
      loadResults(date);
    } catch (e) {
      // Static pages remain usable if the BOATERS JSON has not been generated yet.
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", load);
  } else {
    load();
  }
})();
