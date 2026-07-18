(function (root) {
  "use strict";

  var CORE_URL = "https://mm1601.github.io/kyotei-occult-viewer/manshu_autofill_core.js";
  var CORE_LOADING_FLAG = "__manshuAutofillCoreLoading";

  if (typeof module === "object" && module.exports) {
    module.exports = require("./manshu_autofill_core.js");
    return;
  }
  if (!root || !root.document) return;
  if (root.__manshuAutofillApi && typeof root.__manshuAutofillApi.run === "function") {
    root.__manshuAutofillApi.run();
    return;
  }
  if (root[CORE_LOADING_FLAG]) return;
  root[CORE_LOADING_FLAG] = true;

  var script = root.document.createElement("script");
  script.src = CORE_URL + "?t=" + Date.now();
  script.async = true;
  script.onload = function () {
    root[CORE_LOADING_FLAG] = false;
  };
  script.onerror = function () {
    root[CORE_LOADING_FLAG] = false;
    root.alert("24場入力補助の最新版を読み込めませんでした");
  };
  root.document.head.appendChild(script);
})(typeof window !== "undefined" ? window : typeof globalThis !== "undefined" ? globalThis : this);
