/**
 * Admin Dashboard Live Refresh
 */

function convertToArabicIndic(numStr) {
  if (numStr === null || numStr === undefined) return "";
  const map = {
    '0': '٠', '1': '١', '2': '٢', '3': '٣', '4': '٤',
    '5': '٥', '6': '٦', '7': '٧', '8': '٨', '9': '٩'
  };
  return String(numStr).replace(/[0-9]/g, function (d) {
    return map[d] || d;
  });
}

async function refreshLiveStats(statsUrl, gatesUrl) {
  try {
    const [statsRes, gatesRes] = await Promise.all([
      fetch(statsUrl),
      fetch(gatesUrl)
    ]);

    if (!statsRes.ok || !gatesRes.ok) {
      throw new Error("Fetch failed");
    }

    const [statsJson, gatesJson] = await Promise.all([
      statsRes.json(),
      gatesRes.json()
    ]);

    const statData = statsJson.data || statsJson;
    const gatesList = gatesJson.data || (Array.isArray(gatesJson) ? gatesJson : []);

    const activeEl = document.getElementById("stat-active-sessions");
    if (activeEl && statData.active_sessions !== undefined) {
      activeEl.textContent = convertToArabicIndic(statData.active_sessions);
    }

    const occEl = document.getElementById("stat-occupancy");
    if (occEl && statData.occupancy_pct !== undefined) {
      occEl.textContent = convertToArabicIndic(statData.occupancy_pct) + "٪";
    }

    const revEl = document.getElementById("stat-revenue-today");
    if (revEl && statData.revenue_today_piastres !== undefined) {
      const egpVal = Math.round(statData.revenue_today_piastres / 100);
      revEl.textContent = egpVal.toLocaleString("en-US");
    }

    const shiftsEl = document.getElementById("stat-open-shifts");
    if (shiftsEl && statData.open_shifts !== undefined) {
      shiftsEl.textContent = convertToArabicIndic(statData.open_shifts);
    }

    const gateRows = document.querySelectorAll("[data-gate]");
    gateRows.forEach(function (row) {
      const gateNum = parseInt(row.getAttribute("data-gate"), 10);
      const gate = gatesList.find(function (g) { return g.gate_number === gateNum; });
      if (!gate) return;

      const opEl = row.querySelector("[data-gate-operator]");
      if (opEl) {
        opEl.textContent = gate.operator_name || "—";
      }

      const sessEl = row.querySelector("[data-gate-sessions]");
      if (sessEl) {
        sessEl.textContent = convertToArabicIndic(gate.active_sessions !== undefined ? gate.active_sessions : 0);
      }

      const statusEl = row.querySelector("[data-gate-status]");
      if (statusEl) {
        statusEl.textContent = gate.operator_name ? "مفتوح" : "مغلق";
      }
    });
  } catch (err) {
    console.error("Failed to refresh admin live stats:", err);
  }
}

function startAutoRefresh(statsUrl, gatesUrl, intervalMs = 30000) {
  refreshLiveStats(statsUrl, gatesUrl);
  return setInterval(function () {
    refreshLiveStats(statsUrl, gatesUrl);
  }, intervalMs);
}
