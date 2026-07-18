(function (root) {
  "use strict";

  var MAX_POINTS = 12;
  var FRAGMENT_KEY = "manshu";
  var QUEUE_URL = "https://mm1601.github.io/kyotei-occult-viewer/data/output/manshu_purchase_queue_latest.json";
  var OVERLAY_ID = "manshu-autofill-overlay";
  var RUN_FLAG = "__manshuAutofillActive";
  var TICKET_PATTERN = /^([1-6])-([1-6])-([1-6])$/;
  var QUEUE_CORE_KEYS = [
    "version", "date", "race_id", "venue", "venue_code", "round",
    "deadline_at", "detected_at", "rule_id", "rule_label", "buy_method",
    "tickets", "default_unit_yen", "max_points", "capture_verified"
  ];

  function normalizeText(value) {
    return String(value == null ? "" : value).replace(/\s+/g, "").trim();
  }

  function decodeBase64Url(value) {
    var normalized = String(value || "").replace(/-/g, "+").replace(/_/g, "/");
    while (normalized.length % 4) normalized += "=";
    var binary = atob(normalized);
    var bytes = new Uint8Array(binary.length);
    for (var index = 0; index < binary.length; index += 1) {
      bytes[index] = binary.charCodeAt(index);
    }
    return new TextDecoder().decode(bytes);
  }

  function parsePayload(hash) {
    var params = new URLSearchParams(String(hash || "").replace(/^#/, ""));
    var encoded = params.get(FRAGMENT_KEY);
    if (!encoded) throw new Error("24場サインの入力データがありません");
    try {
      return JSON.parse(decodeBase64Url(encoded));
    } catch (error) {
      throw new Error("24場サインの入力データを読めませんでした");
    }
  }

  function sortObject(value) {
    if (Array.isArray(value)) return value.map(sortObject);
    if (value && typeof value === "object") {
      return Object.keys(value).sort().reduce(function (result, key) {
        result[key] = sortObject(value[key]);
        return result;
      }, {});
    }
    return value;
  }

  async function sha256(value) {
    if (!root.crypto || !root.crypto.subtle) throw new Error("通知データを検証できません");
    var bytes = new TextEncoder().encode(value);
    var digest = await root.crypto.subtle.digest("SHA-256", bytes);
    return Array.from(new Uint8Array(digest)).map(function (byte) {
      return byte.toString(16).padStart(2, "0");
    }).join("");
  }

  function queueCore(order) {
    return QUEUE_CORE_KEYS.reduce(function (core, key) {
      core[key] = order[key];
      return core;
    }, {});
  }

  async function verifyQueueOrder(order) {
    if (!order || !/^mpq_[0-9a-f]{24}$/.test(String(order.order_id || ""))) return false;
    var digest = await sha256(JSON.stringify(sortObject(queueCore(order))));
    return order.order_id === "mpq_" + digest.slice(0, 24);
  }

  function queueOrderPayload(order) {
    var unitYen = Number(order.default_unit_yen) || 100;
    var rows = (order.tickets || []).map(function (combination) {
      return { combination: combination, amount_yen: unitYen };
    });
    return {
      version: Number(order.version),
      order_id: String(order.order_id || ""),
      date: String(order.date || ""),
      venue: String(order.venue || ""),
      venue_code: String(order.venue_code || "").padStart(2, "0"),
      round: Number(order.round),
      deadline_at: String(order.deadline_at || ""),
      max_points: Number(order.max_points || MAX_POINTS),
      tickets: rows.map(function (row) {
        return { combination: row.combination, amount_yen: Number(row.amount_yen) };
      })
    };
  }

  function ticketSetKey(order) {
    return JSON.stringify(queueOrderPayload(order).tickets.slice().sort(function (left, right) {
      return String(left.combination).localeCompare(String(right.combination));
    }));
  }

  function selectQueueOrder(queue, currentUrl, now) {
    var url = currentUrl instanceof URL ? currentUrl : new URL(String(currentUrl));
    var venueCode = String(url.searchParams.get("jyoCode") || "").padStart(2, "0");
    var round = Number(url.searchParams.get("raceNo"));
    var currentTime = Number(now == null ? Date.now() : now);
    var matches = (queue && Array.isArray(queue.orders) ? queue.orders : []).filter(function (order) {
      var deadline = new Date(order && order.deadline_at || "").getTime();
      var rows = order && Array.isArray(order.tickets) ? order.tickets : [];
      return order
        && order.status === "ready"
        && order.capture_verified === true
        && String(order.venue_code || "").padStart(2, "0") === venueCode
        && Number(order.round) === round
        && Number.isFinite(deadline)
        && currentTime < deadline
        && rows.length > 0
        && rows.length <= MAX_POINTS;
    }).sort(function (left, right) {
      return Date.parse(left.detected_at || left.last_seen_at || 0)
        - Date.parse(right.detected_at || right.last_seen_at || 0);
    });
    if (!matches.length) throw new Error("この場・レースに有効な24場サインの買い目がありません");
    var unique = new Map();
    matches.forEach(function (order) { unique.set(ticketSetKey(order), order); });
    if (unique.size !== 1) {
      throw new Error("同じレースに異なる買い目が複数あるため、安全のため停止しました");
    }
    return Array.from(unique.values())[0];
  }

  async function resolvePayload(hash, currentUrl, now, fetchImpl) {
    var hashParams = new URLSearchParams(String(hash || "").replace(/^#/, ""));
    if (hashParams.get(FRAGMENT_KEY)) return parsePayload(hash);
    var url = currentUrl instanceof URL ? currentUrl : new URL(String(currentUrl));
    if (url.protocol !== "https:" || url.hostname !== "spweb.brtb.jp" || url.pathname.replace(/\/$/, "") !== "/bet") {
      throw new Error("公式サイトで対象レースの3連単通常投票画面を開いてから実行してください");
    }
    var request = fetchImpl || (root.fetch && root.fetch.bind(root));
    if (!request) throw new Error("最新の通知買い目を取得できません");
    var response = await request(QUEUE_URL + "?t=" + Date.now(), { cache: "no-store", credentials: "omit" });
    if (!response || !response.ok) throw new Error("最新の通知買い目を取得できません");
    var order = selectQueueOrder(await response.json(), url, now);
    if (!await verifyQueueOrder(order)) throw new Error("通知買い目の改ざん検知で停止しました");
    return queueOrderPayload(order);
  }

  function forbiddenActionText(value) {
    var text = normalizeText(value);
    return text.indexOf("投票へ進む") >= 0 || text === "投票" || text === "投票する";
  }

  function normalizeTickets(values) {
    if (!Array.isArray(values)) return { tickets: [], errors: ["買い目がありません"] };
    var tickets = [];
    var errors = [];
    var seen = new Set();
    values.forEach(function (raw) {
      var combination = String(raw && raw.combination || "").trim().replace(/→/g, "-");
      var match = TICKET_PATTERN.exec(combination);
      var amountYen = Number(raw && raw.amount_yen);
      if (!match || new Set(match.slice(1)).size !== 3) {
        errors.push("不正な買い目: " + (combination || "-"));
        return;
      }
      if (seen.has(combination)) {
        errors.push("重複した買い目: " + combination);
        return;
      }
      if (!Number.isInteger(amountYen) || amountYen < 100 || amountYen % 100 !== 0) {
        errors.push("金額が100円単位ではありません: " + combination);
        return;
      }
      seen.add(combination);
      tickets.push({ combination: combination, amount_yen: amountYen });
    });
    if (!tickets.length) errors.push("有効な買い目がありません");
    if (tickets.length > MAX_POINTS) errors.push("買い目が12点を超えています");
    return { tickets: tickets, errors: errors };
  }

  function validatePayload(payload, currentUrl, now) {
    var errors = [];
    var url = currentUrl instanceof URL ? currentUrl : new URL(String(currentUrl));
    var venueCode = String(payload && payload.venue_code || "").padStart(2, "0");
    var round = Number(payload && payload.round);
    var ticketResult = normalizeTickets(payload && payload.tickets);
    errors = errors.concat(ticketResult.errors);

    if (!payload || Number(payload.version) !== 1) errors.push("入力データの版が違います");
    if (!/^mpq_[0-9a-f]{24}$/.test(String(payload && payload.order_id || ""))) {
      errors.push("通知IDが不正です");
    }
    if (!/^(0[1-9]|1[0-9]|2[0-4])$/.test(venueCode)) errors.push("場コードが不正です");
    if (!Number.isInteger(round) || round < 1 || round > 12) errors.push("レース番号が不正です");
    if (Number(payload && payload.max_points || MAX_POINTS) > MAX_POINTS) {
      errors.push("許可点数が12点を超えています");
    }
    var deadline = new Date(payload && payload.deadline_at || "");
    if (Number.isNaN(deadline.getTime())) errors.push("締切時刻が不明です");
    if (!Number.isNaN(deadline.getTime()) && Number(now == null ? Date.now() : now) >= deadline.getTime()) {
      errors.push("締切を過ぎています");
    }

    if (url.protocol !== "https:" || url.hostname !== "spweb.brtb.jp") {
      errors.push("公式投票サイト以外では動かせません");
    }
    if (url.pathname.replace(/\/$/, "") !== "/bet") errors.push("通常投票画面ではありません");
    if (url.searchParams.get("jyoCode") !== venueCode) errors.push("公式画面の場が通知と違います");
    if (url.searchParams.get("raceNo") !== String(round).padStart(2, "0")) {
      errors.push("公式画面のレース番号が通知と違います");
    }
    if (url.searchParams.get("kachishiki") !== "6") errors.push("3連単画面ではありません");
    if (url.searchParams.get("betWay") !== "1") errors.push("通常投票画面ではありません");
    return { valid: errors.length === 0, errors: errors, tickets: ticketResult.tickets };
  }

  function sleep(milliseconds) {
    return new Promise(function (resolve) { setTimeout(resolve, milliseconds); });
  }

  async function waitFor(check, timeoutMs, label) {
    var started = Date.now();
    while (Date.now() - started < timeoutMs) {
      var value = check();
      if (value) return value;
      await sleep(100);
    }
    throw new Error(label + "を確認できませんでした");
  }

  function createOverlay(payload) {
    var existing = document.getElementById(OVERLAY_ID);
    if (existing) existing.remove();
    var host = document.createElement("div");
    host.id = OVERLAY_ID;
    host.style.cssText = "position:fixed;inset:0;z-index:2147483647;background:rgba(0,0,0,.72);display:flex;align-items:center;justify-content:center;padding:16px;font-family:-apple-system,BlinkMacSystemFont,'Hiragino Kaku Gothic ProN','Yu Gothic',sans-serif;";
    var panel = document.createElement("div");
    panel.style.cssText = "width:min(440px,100%);background:#fff;color:#171a1f;border-radius:8px;padding:18px;box-shadow:0 16px 48px rgba(0,0,0,.35);line-height:1.5;letter-spacing:0;";
    var title = document.createElement("div");
    title.style.cssText = "font-size:19px;font-weight:900;margin-bottom:4px;";
    title.textContent = "24場サイン 買い目入力";
    var race = document.createElement("div");
    race.style.cssText = "font-size:13px;color:#586171;margin-bottom:14px;";
    race.textContent = String(payload.venue || "") + Number(payload.round) + "R";
    var status = document.createElement("div");
    status.style.cssText = "font-size:15px;font-weight:800;min-height:48px;padding:12px;background:#edf4fc;border-left:4px solid #2864ad;";
    var progress = document.createElement("div");
    progress.style.cssText = "font-size:12px;color:#69717d;margin-top:10px;";
    var cancel = document.createElement("button");
    cancel.type = "button";
    cancel.textContent = "中止";
    cancel.style.cssText = "width:100%;min-height:44px;margin-top:14px;border:1px solid #b9c0ca;border-radius:5px;background:#fff;color:#171a1f;font-weight:900;font-size:14px;";
    panel.appendChild(title);
    panel.appendChild(race);
    panel.appendChild(status);
    panel.appendChild(progress);
    panel.appendChild(cancel);
    host.appendChild(panel);
    document.body.appendChild(host);
    return {
      host: host,
      status: status,
      progress: progress,
      cancel: cancel,
      setStatus: function (message, detail, kind) {
        status.textContent = message;
        progress.textContent = detail || "";
        status.style.background = kind === "error" ? "#fff0f2" : kind === "success" ? "#eaf7f4" : "#edf4fc";
        status.style.borderLeftColor = kind === "error" ? "#b42338" : kind === "success" ? "#087f6b" : "#2864ad";
      }
    };
  }

  function guardedClick(element, kind) {
    if (!element) throw new Error("操作する部品が見つかりません");
    var text = normalizeText(element.textContent || element.value || "");
    if (forbiddenActionText(text) || element.classList && (element.classList.contains("btn-purchase") || element.classList.contains("btn-inverse"))) {
      throw new Error("安全装置が投票操作を停止しました");
    }
    if (kind === "ticket") {
      if (element.tagName !== "INPUT" || !/^bet[1-3]-[1-6]$/.test(element.id)) {
        throw new Error("買い目以外への操作を停止しました");
      }
    } else if (kind === "add") {
      if (text.indexOf("ベットリストに追加して") < 0 || text.indexOf("入力を続ける") < 0) {
        throw new Error("安全な追加ボタンを確認できません");
      }
    } else if (kind === "betlist") {
      if (!element.classList || !element.classList.contains("betlist")) {
        throw new Error("ベットリスト以外への移動を停止しました");
      }
    } else {
      throw new Error("許可されていない操作です");
    }
    element.click();
  }

  function setReactInputValue(input, value) {
    if (!input || input.tagName !== "INPUT") throw new Error("金額欄が見つかりません");
    var descriptor = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value");
    if (!descriptor || !descriptor.set) throw new Error("金額欄を操作できません");
    descriptor.set.call(input, String(value));
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
    input.dispatchEvent(new Event("blur", { bubbles: true }));
  }

  function selectedInputs() {
    return Array.from(document.querySelectorAll('input[id^="bet1-"]:checked,input[id^="bet2-"]:checked,input[id^="bet3-"]:checked'));
  }

  async function clearSelections(cancelled) {
    selectedInputs().forEach(function (input) { guardedClick(input, "ticket"); });
    await waitFor(function () { return cancelled() || selectedInputs().length === 0; }, 3000, "前の買い目の解除");
    if (cancelled()) throw new Error("入力を中止しました");
  }

  async function selectCombination(combination, cancelled) {
    var boats = combination.split("-");
    await clearSelections(cancelled);
    for (var position = 1; position <= 3; position += 1) {
      if (cancelled()) throw new Error("入力を中止しました");
      var input = document.getElementById("bet" + position + "-" + boats[position - 1]);
      if (!input || input.disabled || input.closest(".is-out")) {
        throw new Error(combination + " は欠場・入力不可の艇を含みます");
      }
      if (!input.checked) guardedClick(input, "ticket");
      await waitFor(function () { return cancelled() || input.checked; }, 3000, combination + " の選択");
    }
  }

  function findAmountInput() {
    return document.querySelector(".vote-box .input-money-block input.textbox[type='tel'],.vote-box .input-money-block input.textbox");
  }

  function findSafeAddButton() {
    return Array.from(document.querySelectorAll(".vote-box .btn.btn-break.btn-bold")).find(function (element) {
      var text = normalizeText(element.textContent);
      return text.indexOf("ベットリストに追加して") >= 0 && text.indexOf("入力を続ける") >= 0 && !forbiddenActionText(text) && !element.classList.contains("btn-inverse");
    });
  }

  function isEnabled(element) {
    return Boolean(element) && !element.classList.contains("is-disabled") && element.getAttribute("aria-disabled") !== "true";
  }

  async function addTicket(ticket, cancelled) {
    await selectCombination(ticket.combination, cancelled);
    var amountInput = await waitFor(findAmountInput, 5000, "購入金額欄");
    setReactInputValue(amountInput, ticket.amount_yen / 100);
    await waitFor(function () {
      var button = findSafeAddButton();
      return cancelled() || (isEnabled(button) && button);
    }, 5000, "ベットリスト追加ボタン");
    if (cancelled()) throw new Error("入力を中止しました");
    guardedClick(findSafeAddButton(), "add");
    await waitFor(function () {
      var message = document.querySelector(".bet-add-message");
      return cancelled() || (message && normalizeText(message.textContent).indexOf("追加しました") >= 0);
    }, 10000, ticket.combination + " の追加完了");
    if (cancelled()) throw new Error("入力を中止しました");
    await waitFor(function () { return !document.querySelector(".bet-add-message"); }, 8000, "追加完了画面の終了");
  }

  function removePayloadFragment() {
    var url = new URL(location.href);
    url.hash = "";
    history.replaceState(history.state, "", url.pathname + url.search);
  }

  function findBetListButton() {
    return document.querySelector(".header-nav-btn.betlist");
  }

  async function run() {
    if (!root || !root.document || !root.location) return;
    if (root[RUN_FLAG]) {
      var current = document.getElementById(OVERLAY_ID);
      if (current) current.style.display = "flex";
      return;
    }
    root[RUN_FLAG] = true;
    var payload;
    var overlay;
    var cancelled = false;
    try {
      payload = await resolvePayload(location.hash, new URL(location.href), Date.now());
      overlay = createOverlay(payload);
      overlay.cancel.onclick = function () {
        cancelled = true;
        overlay.setStatus("入力を中止しています", "追加済みの買い目は公式ベットリストで確認してください", "error");
      };
      var validation = validatePayload(payload, new URL(location.href), Date.now());
      if (!validation.valid) throw new Error(validation.errors.join(" / "));
      overlay.setStatus("公式画面を確認しています", "3連単・場・レース番号・締切を照合中");
      await waitFor(function () {
        return document.querySelector("#bet1-1") && document.querySelector("#bet2-1") && document.querySelector("#bet3-1");
      }, 20000, "公式3連単入力画面");

      for (var index = 0; index < validation.tickets.length; index += 1) {
        var ticket = validation.tickets[index];
        overlay.setStatus(
          ticket.combination + "  " + ticket.amount_yen.toLocaleString("ja-JP") + "円",
          String(index + 1) + " / " + validation.tickets.length + "点を入力中"
        );
        await addTicket(ticket, function () { return cancelled; });
      }

      removePayloadFragment();
      overlay.setStatus("全買い目を追加しました", "ベットリストへ移動します。最終の投票はご自身で確認して押してください。", "success");
      overlay.cancel.textContent = "閉じる";
      overlay.cancel.onclick = function () { overlay.host.remove(); };
      var betListButton = await waitFor(findBetListButton, 5000, "ベットリスト");
      await sleep(700);
      guardedClick(betListButton, "betlist");
    } catch (error) {
      if (!overlay) overlay = createOverlay(payload || { venue: "", round: "" });
      overlay.setStatus("自動入力を停止しました", error && error.message || String(error), "error");
      overlay.cancel.textContent = "閉じる";
      overlay.cancel.onclick = function () { overlay.host.remove(); };
    } finally {
      root[RUN_FLAG] = false;
    }
  }

  var api = {
    decodeBase64Url: decodeBase64Url,
    parsePayload: parsePayload,
    queueOrderPayload: queueOrderPayload,
    selectQueueOrder: selectQueueOrder,
    verifyQueueOrder: verifyQueueOrder,
    resolvePayload: resolvePayload,
    forbiddenActionText: forbiddenActionText,
    normalizeTickets: normalizeTickets,
    validatePayload: validatePayload,
    run: run
  };

  if (typeof module === "object" && module.exports) module.exports = api;
  if (root && root.document && root.location) run();
})(typeof window !== "undefined" ? window : typeof globalThis !== "undefined" ? globalThis : this);
