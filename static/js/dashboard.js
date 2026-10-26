// static/js/dashboard.js — Fast Direct POS Scanner & Customer Display (IE11 & ES5 Compatible)

(function () {
  "use strict";

  // Polyfills for legacy browsers (IE11)
  if (!String.prototype.padStart) {
    String.prototype.padStart = function (targetLength, padString) {
      targetLength = targetLength >> 0;
      padString = String(typeof padString !== "undefined" ? padString : " ");
      if (this.length > targetLength) return String(this);
      targetLength = targetLength - this.length;
      if (targetLength > padString.length) {
        padString += padString.repeat ? padString.repeat(targetLength / padString.length) : padString;
      }
      return padString.slice(0, targetLength) + String(this);
    };
  }

  if (!String.prototype.trim) {
    String.prototype.trim = function () {
      return this.replace(/^\s+|\s+$/g, "");
    };
  }

  var currentExitSessionId = null;
  var currentEntrySessionId = null;
  var lastReceiptUrl = null;
  var autoResetTimer = null;
  // Incremented on every new entry/reset so late long-poll replies are ignored
  var captureToken = 0;
  var ENTRY_RESET_MS = 6000;
  var CAPTURE_POLL_RETRY_MS = 500;
  // Extra slack on top of the server deadline before the client gives up
  var CAPTURE_CLIENT_SLACK_MS = 3000;

  var cameraTimerSeconds = 410; // 00:06:50 live counter
  function updateCameraTimer() {
    cameraTimerSeconds++;
    var timerEl = document.getElementById("camera-live-timer");
    if (!timerEl) return;
    var hrs = Math.floor(cameraTimerSeconds / 3600);
    var mins = Math.floor((cameraTimerSeconds % 3600) / 60);
    var secs = cameraTimerSeconds % 60;
    timerEl.textContent = padZero(hrs) + ":" + padZero(mins) + ":" + padZero(secs);
  }
  setInterval(updateCameraTimer, 1000);

  function stopAutoReset() {
    if (autoResetTimer) {
      clearTimeout(autoResetTimer);
      autoResetTimer = null;
    }
  }

  function updatePlatePreview(val) {
    var plateEl = document.getElementById("entry-plate-text");
    if (plateEl) {
      var trimmed = (val || "").trim();
      plateEl.textContent = trimmed || "—";
    }
  }

  function saveManualPlate(val) {
    val = (val || "").trim();
    var plateEl = document.getElementById("entry-plate-text");
    if (plateEl) plateEl.textContent = val || "—";

    var plateInput = document.getElementById("plate-manual-input");
    var targetId = currentEntrySessionId || (plateInput ? plateInput.getAttribute("data-session-id") : null);
    if (!targetId) return;

    var xhr = new XMLHttpRequest();
    xhr.open("POST", "/ui/operator/api/sessions/" + targetId + "/plate-override", true);
    xhr.setRequestHeader("Content-Type", "application/json;charset=UTF-8");
    xhr.onreadystatechange = function () {
      if (xhr.readyState === 4 && xhr.status >= 200 && xhr.status < 300) {
        refreshRecentSessions();
      }
    };
    xhr.send(JSON.stringify({ plate_number: val }));
  }

  function padZero(num) {
    return (num < 10 ? "0" : "") + num;
  }

  // Web Audio API tactile feedback with safe fallback
  function playSound(type) {
    try {
      var AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (!AudioCtx) return;
      var ctx = new AudioCtx();
      var osc = ctx.createOscillator();
      var gain = ctx.createGain();
      osc.connect(gain);
      gain.connect(ctx.destination);

      if (type === "success") {
        osc.type = "sine";
        osc.frequency.setValueAtTime(587.33, ctx.currentTime);
        osc.frequency.exponentialRampToValueAtTime(880, ctx.currentTime + 0.15);
        gain.gain.setValueAtTime(0.15, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.2);
        osc.start(0);
        osc.stop(ctx.currentTime + 0.2);
      } else if (type === "checkout") {
        osc.type = "triangle";
        osc.frequency.setValueAtTime(523.25, ctx.currentTime);
        osc.frequency.setValueAtTime(659.25, ctx.currentTime + 0.08);
        osc.frequency.setValueAtTime(783.99, ctx.currentTime + 0.16);
        osc.frequency.setValueAtTime(1046.50, ctx.currentTime + 0.24);
        gain.gain.setValueAtTime(0.2, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.35);
        osc.start(0);
        osc.stop(ctx.currentTime + 0.35);
      } else if (type === "error") {
        osc.type = "sawtooth";
        osc.frequency.setValueAtTime(220, ctx.currentTime);
        osc.frequency.setValueAtTime(160, ctx.currentTime + 0.1);
        gain.gain.setValueAtTime(0.2, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.25);
        osc.start(0);
        osc.stop(ctx.currentTime + 0.25);
      }
    } catch (e) {
      // AudioContext unavailable or blocked
    }
  }

  // 100% ES5 Clock with no ES6+ dependencies and no complex Intl options
  function updateLiveClock() {
    var el = document.getElementById("live-clock");
    if (!el) return;

    var now = new Date();
    var hours = now.getHours();
    var minutes = now.getMinutes();
    var seconds = now.getSeconds();
    var isPM = hours >= 12;
    var ampm = isPM ? "م" : "ص";

    hours = hours % 12;
    hours = hours ? hours : 12;

    var timeStr = padZero(hours) + ":" + padZero(minutes) + ":" + padZero(seconds);
    var arabicDigits = {
      "0": "٠", "1": "١", "2": "٢", "3": "٣", "4": "٤",
      "5": "٥", "6": "٦", "7": "٧", "8": "٨", "9": "٩"
    };

    var arabicTime = "";
    for (var i = 0; i < timeStr.length; i++) {
      var c = timeStr.charAt(i);
      arabicTime += arabicDigits[c] ? arabicDigits[c] : c;
    }

    el.textContent = arabicTime + " " + ampm;
  }

  function startClock() {
    updateLiveClock();
    setInterval(updateLiveClock, 1000);
  }

  function initDirectPOSScanner() {
    var input = document.getElementById("card-code-input");
    if (!input) return;

    function maintainFocus() {
      if (document.activeElement && (
        document.activeElement.tagName === "INPUT" ||
        document.activeElement.tagName === "BUTTON" ||
        document.activeElement.tagName === "TEXTAREA"
      )) {
        return;
      }
      input.focus();
    }

    document.addEventListener("click", function () {
      setTimeout(maintainFocus, 100);
    });

    maintainFocus();

    // Keyboard shortcuts (Escape, Enter, P)
    document.addEventListener("keydown", function (e) {
      var key = e.key || "";
      var code = e.keyCode || e.which;

      if (key === "Escape" || key === "Esc" || code === 27) {
        resetCustomerDisplay();
      } else if (key === "Enter" || code === 13 || key === "p" || key === "P" || code === 80) {
        var exitBox = document.getElementById("exit-display-box");
        if (exitBox && exitBox.className.indexOf("hidden") === -1 && lastReceiptUrl) {
          if ((key === "Enter" || code === 13) && input && input.value && input.value.replace(/^\s+|\s+$/g, "").length > 0) {
            return;
          }
          if (e.preventDefault) e.preventDefault();
          else e.returnValue = false;
          printLastReceipt();
        }
      }
    });

    // Load recent sessions immediately on startup
    refreshRecentSessions();

    // Auto poll session count every 10 seconds
    setInterval(refreshRecentSessions, 10000);
  }

  function handleDirectScan(e) {
    if (e) {
      if (e.preventDefault) e.preventDefault();
      else e.returnValue = false;
    }

    var input = document.getElementById("card-code-input");
    var spinner = document.getElementById("scan-spinner");
    if (!input) return;

    var cardCode = input.value ? input.value.replace(/^\s+|\s+$/g, "") : "";
    if (!cardCode) return;

    dismissError();
    if (autoResetTimer) {
      clearTimeout(autoResetTimer);
      autoResetTimer = null;
    }

    if (spinner) {
      spinner.className = spinner.className.replace(/\bhidden\b/g, "").replace(/\s+/g, " ");
    }

    var xhr = new XMLHttpRequest();
    xhr.open("POST", "/ui/operator/api/scan", true);
    xhr.setRequestHeader("Content-Type", "application/json;charset=UTF-8");
    xhr.setRequestHeader("Accept", "application/json");

    xhr.onreadystatechange = function () {
      if (xhr.readyState === 4) {
        if (spinner) {
          if (spinner.className.indexOf("hidden") === -1) {
            spinner.className += " hidden";
          }
        }

        var data = null;
        try {
          data = JSON.parse(xhr.responseText);
        } catch (parseErr) {
          data = null;
        }

        if (xhr.status >= 200 && xhr.status < 300 && data && data.success) {
          if (data.type === "entry") {
            playSound("success");
            showEntrySuccess(data);
            input.value = "";
            refreshRecentSessions();
          } else if (data.type === "exit_completed" || data.type === "exit_preview") {
            playSound("checkout");
            showExitCompleted(data);
            input.value = "";
            refreshRecentSessions();
          } else {
            playSound("success");
            input.value = "";
            refreshRecentSessions();
          }
        } else {
          playSound("error");
          var errMsg = (data && data.error) ? data.error : "تعذر إتمام العملية";
          showError(errMsg);
          if (input.select) input.select();
        }
      }
    };

    xhr.onerror = function () {
      if (spinner) {
        if (spinner.className.indexOf("hidden") === -1) {
          spinner.className += " hidden";
        }
      }
      playSound("error");
      showError("حدث خطأ في الاتصال بالخادم. يرجى إعادة المحاولة.");
    };

    xhr.send(JSON.stringify({ card_code: cardCode }));
  }

  function showExitCompleted(data) {
    currentExitSessionId = data.session_id;
    lastReceiptUrl = data.receipt_url || null;

    var standbyBox = document.getElementById("standby-display");
    var exitBox = document.getElementById("exit-display-box");
    var entryBox = document.getElementById("entry-display-box");

    if (!exitBox) return;

    if (standbyBox) standbyBox.classList.add("hidden");
    if (entryBox) entryBox.classList.add("hidden");
    exitBox.classList.remove("hidden");

    var priceEl = document.getElementById("exit-huge-price");
    var badgeText = document.getElementById("exit-badge-text");
    var cardCodeEl = document.getElementById("exit-card-code");
    var plateEl = document.getElementById("exit-plate-number");
    var entryTimeEl = document.getElementById("exit-entry-time");
    var durationEl = document.getElementById("exit-duration");
    var rateLabelEl = document.getElementById("exit-rate-label");

    if (priceEl) {
      var p = (data.total_egp !== undefined && data.total_egp !== null) ? data.total_egp : "0";
      var pNum = parseFloat(p);
      if (!isNaN(pNum)) {
        priceEl.textContent = String(Math.round(pNum));
      } else {
        priceEl.textContent = String(p).replace(/\.00$/, "");
      }
    }
    if (cardCodeEl) cardCodeEl.textContent = data.card_code || "—";
    if (entryTimeEl) entryTimeEl.textContent = data.entry_time || "—";
    if (durationEl) durationEl.textContent = data.duration_display || "—";
    if (rateLabelEl) {
      rateLabelEl.textContent = data.is_subscribed
        ? "اشتراك معتمد (مجاني)"
        : (data.rate_label || "عادي") + " (" + (data.rate_per_hour || "") + ")";
    }

    if (badgeText) {
      badgeText.textContent = data.is_subscribed
        ? "مشترك معتمد — خروج مجاني"
        : "تم تأكيد الخروج بنجاح — المبلغ المستحق";
    }

    if (plateEl) {
      plateEl.textContent = data.plate_number || "—";
    }

    var input = document.getElementById("card-code-input");
    if (input) {
      input.focus();
    }
  }

  function printLastReceipt() {
    if (!lastReceiptUrl) return;
    try {
      window.open(lastReceiptUrl, "_blank", "width=420,height=600");
    } catch (e) {
      // Popup blocked
    }
  }

  function showEntrySuccess(data) {
    currentEntrySessionId = data.session_id || null;

    var cardCodeEl = document.getElementById("entry-card-code");
    var plateText = document.getElementById("entry-plate-text");
    var plateInput = document.getElementById("plate-manual-input");
    var statusText = document.getElementById("entry-status-text");
    var subPill = document.getElementById("entry-subscriber-pill");
    var confBadge = document.getElementById("entry-confidence-badge");
    var confText = document.getElementById("entry-confidence-text");
    var boundingBox = document.getElementById("plate-bounding-box");
    var standbyView = document.getElementById("camera-standby-view");
    var captureImg = document.getElementById("entry-capture-img");

    if (cardCodeEl) cardCodeEl.textContent = data.card_code || "—";

    var plate = data.plate_number || (data.subscription && data.subscription.plate_number) || "";
    if (plateText) plateText.textContent = plate || "—";
    if (plateInput) {
      plateInput.value = plate || "";
      if (data.session_id) {
        plateInput.setAttribute("data-session-id", data.session_id);
      }
    }

    if (data.is_subscribed) {
      if (statusText) statusText.textContent = "تم تسجيل دخول المشترك";
      if (subPill) subPill.classList.remove("hidden");
    } else {
      if (statusText) statusText.textContent = "تم تسجيل الدخول بنجاح";
      if (subPill) subPill.classList.add("hidden");
    }

    if (confBadge) {
      if (data.plate_capture && data.plate_capture.status === "SUCCESS" && plate) {
        var conf = typeof data.plate_capture.confidence === "number" ? Math.round(data.plate_capture.confidence * 100) : 85;
        if (conf < 50) {
          confBadge.className = "w-full bg-amber-950/60 border border-amber-500/60 text-amber-300 rounded-lg py-1 px-2.5 text-center text-xs font-bold shadow-sm my-1";
          if (confText) confText.textContent = "دقة القراءة " + conf + "% - يرجى المراجعة";
        } else {
          confBadge.className = "w-full bg-emerald-950/60 border border-emerald-500/60 text-emerald-300 rounded-lg py-1 px-2.5 text-center text-xs font-bold shadow-sm my-1";
          if (confText) confText.textContent = "دقة القراءة " + conf + "% - تمت قراءة اللوحة بنجاح";
        }
      } else if (data.plate_capture && (data.plate_capture.status === "NO_PLATE" || data.plate_capture.status === "FAILED")) {
        confBadge.className = "w-full bg-amber-950/60 border border-amber-500/60 text-amber-300 rounded-lg py-1 px-2.5 text-center text-xs font-bold shadow-sm my-1";
        if (confText) confText.textContent = "لم يتم التعرف على اللوحة تلقائياً — يرجى كتابتها يدوياً";
      } else {
        confBadge.className = "w-full bg-cyan-950/60 border border-cyan-500/60 text-cyan-300 rounded-lg py-1 px-2.5 text-center text-xs font-bold shadow-sm my-1";
        if (confText) confText.textContent = "جاري التقاط صورة السيارة وقراءة اللوحة...";
      }
      confBadge.classList.remove("hidden");
    }

    if (captureImg) {
      captureImg.classList.remove("hidden");
      if (standbyView) standbyView.classList.add("hidden");
    }
    if (boundingBox) {
      if (plate) {
        boundingBox.classList.remove("hidden");
      } else {
        boundingBox.classList.add("hidden");
      }
    }

    var capturePending = startPlateCapture(data);
    if (!capturePending) {
      scheduleEntryReset();
    }
  }

  function scheduleEntryReset() {
    var plateInput = document.getElementById("plate-manual-input");
    if (plateInput && document.activeElement === plateInput) {
      return;
    }
    if (autoResetTimer) clearTimeout(autoResetTimer);
    autoResetTimer = setTimeout(function () {
      resetCustomerDisplay();
    }, ENTRY_RESET_MS);
  }

  // ---------------------------------------------------------------------
  // Edge camera plate capture (long-poll; XHR only for legacy browsers)
  // ---------------------------------------------------------------------

  var CAPTURE_MESSAGES = {
    PENDING: "جاري التقاط صورة السيارة وقراءة اللوحة…",
    SUCCESS: "تمت قراءة اللوحة بنجاح",
    NO_PLATE: "تم التقاط الصورة — لم يتم التعرف على اللوحة",
    FAILED: "تعذر التقاط الصورة من الكاميرا",
    TIMEOUT: "انتهت مهلة قراءة اللوحة",
    OFFLINE: "كاميرا البوابة غير متصلة حالياً"
  };
  var CAPTURE_COLORS = {
    PENDING: "#cbd5e1",
    SUCCESS: "#34d399",
    NO_PLATE: "#fcd34d",
    FAILED: "#fb7185",
    TIMEOUT: "#fcd34d",
    OFFLINE: "#fb7185"
  };

  // Returns true while a capture result is still expected.
  function startPlateCapture(data) {
    captureToken++;
    var box = document.getElementById("entry-capture-box");
    var img = document.getElementById("entry-capture-img");
    var badge = document.getElementById("entry-camera-badge");
    if (img && !img.hasAttribute("src")) {
      img.classList.add("hidden");
    }
    if (badge) badge.classList.add("hidden");

    var cap = data && data.plate_capture;
    if (!cap || !cap.status) {
      return false;
    }

    if (box) box.classList.remove("hidden");
    renderCapture(cap, data);
    if (cap.status !== "PENDING" || !cap.poll_url) return false;

    var deadline = new Date().getTime() + (cap.timeout_ms || 4000) + CAPTURE_CLIENT_SLACK_MS;
    pollPlateCapture(captureToken, cap.poll_url, deadline, data);
    return true;
  }

  function pollPlateCapture(token, url, deadline, entryData) {
    if (token !== captureToken) return;
    if (new Date().getTime() > deadline) {
      finishCapture(token, { status: "TIMEOUT" }, entryData);
      return;
    }

    var xhr = new XMLHttpRequest();
    xhr.open("GET", url + "?wait=1", true);
    xhr.setRequestHeader("Accept", "application/json");
    xhr.timeout = 12000;

    function retry() {
      if (token !== captureToken) return;
      setTimeout(function () {
        pollPlateCapture(token, url, deadline, entryData);
      }, CAPTURE_POLL_RETRY_MS);
    }

    xhr.onreadystatechange = function () {
      if (xhr.readyState !== 4 || token !== captureToken) return;
      if (xhr.status === 0) return;
      var res = null;
      try {
        res = JSON.parse(xhr.responseText);
      } catch (e) {
        res = null;
      }
      if (xhr.status >= 200 && xhr.status < 300 && res && res.status) {
        if (res.status === "PENDING") {
          pollPlateCapture(token, url, deadline, entryData);
        } else {
          finishCapture(token, res, entryData);
        }
      } else if (xhr.status === 404 || xhr.status === 401 || xhr.status === 403) {
        finishCapture(token, { status: "FAILED" }, entryData);
      } else {
        retry();
      }
    };
    xhr.onerror = retry;
    xhr.ontimeout = retry;
    xhr.send(null);
  }

  function finishCapture(token, res, entryData) {
    if (token !== captureToken) return;
    renderCapture(res, entryData);
    playSound(res.status === "SUCCESS" ? "success" : "error");
    scheduleEntryReset();
  }

  function renderCapture(res, entryData) {
    var captureImg = document.getElementById("entry-capture-img");
    var standbyView = document.getElementById("camera-standby-view");
    var plateText = document.getElementById("entry-plate-text");
    var plateInput = document.getElementById("plate-manual-input");
    var confBadge = document.getElementById("entry-confidence-badge");
    var confText = document.getElementById("entry-confidence-text");
    var boundingBox = document.getElementById("plate-bounding-box");
    var state = res.status;

    if (captureImg && res.image_url) {
      captureImg.onload = function () {
        captureImg.classList.remove("hidden");
        if (standbyView) standbyView.classList.add("hidden");
      };
      captureImg.src = res.image_url;
    }

    if (state === "SUCCESS" && res.plate_number) {
      if (plateText) plateText.textContent = res.plate_number;
      if (plateInput) plateInput.value = res.plate_number;
      if (boundingBox) boundingBox.classList.remove("hidden");
    } else {
      var fallback = (entryData && (entryData.plate_number || (entryData.subscription && entryData.subscription.plate_number))) || "";
      if (plateText) plateText.textContent = fallback || "—";
      if (plateInput && !plateInput.value) plateInput.value = fallback || "";
      if (boundingBox) boundingBox.classList.add("hidden");
    }

    if (confBadge) {
      var conf = typeof res.confidence === "number" ? Math.round(res.confidence * 100) : 0;
      if (state === "SUCCESS" && res.plate_number) {
        if (conf < 50) {
          confBadge.className = "w-full bg-amber-950/60 border border-amber-500/60 text-amber-300 rounded-lg py-1 px-2.5 text-center text-xs font-bold shadow-sm my-1";
          if (confText) confText.textContent = "دقة القراءة " + conf + "% - يرجى المراجعة";
        } else {
          confBadge.className = "w-full bg-emerald-950/60 border border-emerald-500/60 text-emerald-300 rounded-lg py-1 px-2.5 text-center text-xs font-bold shadow-sm my-1";
          if (confText) confText.textContent = "دقة القراءة " + conf + "% - تمت قراءة اللوحة بنجاح";
        }
      } else {
        confBadge.className = "w-full bg-amber-950/60 border border-amber-500/60 text-amber-300 rounded-lg py-1 px-2.5 text-center text-xs font-bold shadow-sm my-1";
        if (confText) confText.textContent = "لم يتم التعرف على اللوحة تلقائياً — يرجى كتابتها يدوياً";
      }
      confBadge.classList.remove("hidden");
    }
  }

  function resetCustomerDisplay() {
    currentExitSessionId = null;
    currentEntrySessionId = null;
    lastReceiptUrl = null;
    captureToken++;
    if (autoResetTimer) {
      clearTimeout(autoResetTimer);
      autoResetTimer = null;
    }

    var standbyBox = document.getElementById("standby-display");
    var exitBox = document.getElementById("exit-display-box");
    if (exitBox) exitBox.classList.add("hidden");
    if (standbyBox) standbyBox.classList.remove("hidden");

    // Reset Gate 1 UI elements
    var entryCardCode = document.getElementById("entry-card-code");
    if (entryCardCode) entryCardCode.textContent = "—";

    var entryPlate = document.getElementById("entry-plate-text");
    if (entryPlate) entryPlate.textContent = "—";

    var entryInput = document.getElementById("plate-manual-input");
    if (entryInput) {
      entryInput.value = "";
      entryInput.removeAttribute("data-session-id");
    }

    var entryStatus = document.getElementById("entry-status-text");
    if (entryStatus) entryStatus.textContent = "في انتظار مسح الكارت...";

    var confBadge = document.getElementById("entry-confidence-badge");
    if (confBadge) confBadge.classList.add("hidden");

    var boundingBox = document.getElementById("plate-bounding-box");
    if (boundingBox) boundingBox.classList.add("hidden");

    var captureImg = document.getElementById("entry-capture-img");
    if (captureImg) {
      captureImg.classList.add("hidden");
      captureImg.src = "";
    }

    var standbyView = document.getElementById("camera-standby-view");
    if (standbyView) standbyView.classList.remove("hidden");

    var subPill = document.getElementById("entry-subscriber-pill");
    if (subPill) subPill.classList.add("hidden");

    var input = document.getElementById("card-code-input");
    if (input) {
      input.value = "";
      input.focus();
    }
  }

  function showError(msg) {
    var box = document.getElementById("status-error-box");
    var msgEl = document.getElementById("error-message");
    if (box && msgEl) {
      msgEl.textContent = msg;
      box.classList.remove("hidden");
    }
  }

  function dismissError() {
    var box = document.getElementById("status-error-box");
    if (box) box.classList.add("hidden");
  }

  function refreshRecentSessions() {
    var xhr = new XMLHttpRequest();
    xhr.open("GET", "/ui/operator/api/recent-sessions", true);
    xhr.setRequestHeader("Accept", "application/json");

    xhr.onreadystatechange = function () {
      if (xhr.readyState === 4 && xhr.status >= 200 && xhr.status < 300) {
        var data = null;
        try {
          data = JSON.parse(xhr.responseText);
        } catch (e) {
          return;
        }

        if (!data || !data.sessions) return;

        var countEl = document.getElementById("session-count");
        if (countEl && typeof data.total === "number") {
          countEl.textContent = data.total;
        }

        var tbody = document.getElementById("recent-sessions-tbody");
        if (!tbody) return;

        if (data.sessions.length === 0) {
          tbody.innerHTML = '<tr><td colspan="4" class="py-4 text-center text-xs text-slate-400">لا توجد عمليات مسجلة بعد في هذه الوردية.</td></tr>';
          return;
        }

        var isGate1 = document.getElementById("camera-live-timer") !== null;
        var html = "";
        for (var i = 0; i < data.sessions.length; i++) {
          var s = data.sessions[i];
          var amount = s.amount_formatted
            ? '<span class="font-bold text-emerald-400 font-digital text-sm">' + s.amount_formatted + '</span>'
            : '<span class="text-slate-500 font-bold">-</span>';

          if (isGate1) {
            var status = s.exit_time
              ? '<span class="text-slate-400 font-bold text-xs">' + s.exit_time + '</span>'
              : '<span class="text-emerald-400 font-bold bg-emerald-950/80 px-2.5 py-0.5 rounded-full border border-emerald-500/40 text-[11px]">داخل الجراج</span>';

            var plateDisplay = s.plate_number
              ? '<span class="font-bold text-cyan-300 font-digital text-xs sm:text-sm">' + s.plate_number + '</span>'
              : '<span class="text-slate-500 font-bold">—</span>';

            html += '<tr class="border-b border-slate-800/60 hover:bg-slate-800/40 transition">' +
              '<td class="py-2 px-3 font-digital text-slate-200 font-bold text-sm">' + (s.card_code || "—") + '</td>' +
              '<td class="py-2 px-3 text-slate-300 font-digital text-xs">' + (s.entry_time || "—") + '</td>' +
              '<td class="py-2 px-3 font-digital">' + plateDisplay + '</td>' +
              '<td class="py-2 px-3">' + status + '</td>' +
              '</tr>';
          } else {
            var exitStatus = s.exit_time
              ? s.exit_time
              : '<span class="text-emerald-400 font-bold bg-emerald-950/80 px-2.5 py-0.5 rounded-full border border-emerald-500/40 text-xs">داخل الجراج</span>';

            html += '<tr class="border-b border-slate-800/60 hover:bg-slate-800/40 transition">' +
              '<td class="py-2 px-3 font-digital text-slate-200 font-bold text-sm">' + (s.card_code || "—") + '</td>' +
              '<td class="py-2 px-3 text-slate-300 font-digital text-xs">' + (s.entry_time || "—") + '</td>' +
              '<td class="py-2 px-3 text-slate-300 font-digital text-xs">' + exitStatus + '</td>' +
              '<td class="py-2 px-3 font-digital">' + amount + '</td>' +
              '</tr>';
          }
        }
        tbody.innerHTML = html;
      }
    };

    xhr.send(null);
  }

  // Export functions to window
  window.startClock = startClock;
  window.updateLiveClock = updateLiveClock;
  window.initDirectPOSScanner = initDirectPOSScanner;
  window.handleDirectScan = handleDirectScan;
  window.resetCustomerDisplay = resetCustomerDisplay;
  window.printLastReceipt = printLastReceipt;
  window.dismissError = dismissError;
  window.refreshRecentSessions = refreshRecentSessions;
  window.saveManualPlate = saveManualPlate;
  window.updatePlatePreview = updatePlatePreview;
  window.stopAutoReset = stopAutoReset;

  // Auto initialize clock immediately
  startClock();
})();

