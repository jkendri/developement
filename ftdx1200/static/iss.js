(function () {
  const elInput = document.getElementById("issEl");
  const durInput = document.getElementById("issDur");
  const countInput = document.getElementById("issCount");
  const gridEl = document.getElementById("issGrid");
  const meta = document.getElementById("issMeta");
  const body = document.getElementById("issBody");
  if (!elInput || !durInput || !countInput || !gridEl || !meta || !body) return;

  const KEYS = {
    el: "ftdx-iss-el",
    dur: "ftdx-iss-dur",
    count: "ftdx-iss-count",
    grid: "ftdx-iss-grid",
  };
  try {
    const savedEl = localStorage.getItem(KEYS.el);
    const savedDur = localStorage.getItem(KEYS.dur);
    const savedCount = localStorage.getItem(KEYS.count);
    const savedGrid = localStorage.getItem(KEYS.grid);
    if (savedEl != null) elInput.value = savedEl;
    if (savedDur != null) durInput.value = savedDur;
    if (savedCount != null) countInput.value = savedCount;
    if (savedGrid) gridEl.value = savedGrid;
  } catch (_) { /* ignore */ }

  let rows = [];
  let tick = 0;

  function clamp(input, lo, hi, fallback) {
    const n = Number(input.value);
    const next = Number.isFinite(n) ? Math.min(hi, Math.max(lo, Math.round(n))) : fallback;
    input.value = String(next);
    return next;
  }

  function saveCriteria() {
    try {
      localStorage.setItem(KEYS.el, elInput.value);
      localStorage.setItem(KEYS.dur, durInput.value);
      localStorage.setItem(KEYS.count, countInput.value);
      localStorage.setItem(KEYS.grid, gridEl.value.trim().toUpperCase());
    } catch (_) { /* ignore */ }
  }

  function compass(deg) {
    const names = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
    const n = ((Number(deg) % 360) + 360) % 360;
    return names[Math.round(n / 22.5) % 16];
  }

  function zoneName() {
    try {
      const part = new Intl.DateTimeFormat(undefined, { timeZoneName: "short" })
        .formatToParts(new Date())
        .find((p) => p.type === "timeZoneName");
      return (part && part.value) || "Local";
    } catch (_) {
      return "Local";
    }
  }

  function localParts(iso) {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return { date: "—", time: "—" };
    const mon = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][d.getMonth()];
    const hh = String(d.getHours()).padStart(2, "0");
    const mm = String(d.getMinutes()).padStart(2, "0");
    const ss = String(d.getSeconds()).padStart(2, "0");
    return { date: `${d.getDate()} ${mon}`, time: `${hh}:${mm}:${ss}` };
  }

  function durationText(sec) {
    const n = Math.max(0, Number(sec) || 0);
    const m = Math.floor(n / 60);
    const s = n % 60;
    return `${m}m ${String(s).padStart(2, "0")}s`;
  }

  function startsText(isoAos, isoLos) {
    const now = Date.now();
    const aos = new Date(isoAos).getTime();
    const los = new Date(isoLos).getTime();
    if (!Number.isFinite(aos) || !Number.isFinite(los)) return "—";
    if (now >= aos && now <= los) return "Up now";
    if (now > los) return "Passed";
    let left = Math.round((aos - now) / 1000);
    const days = Math.floor(left / 86400);
    left -= days * 86400;
    const hours = Math.floor(left / 3600);
    left -= hours * 3600;
    const mins = Math.floor(left / 60);
    if (days > 0) return `in ${days}d ${hours}h`;
    if (hours > 0) return `in ${hours}h ${mins}m`;
    if (mins > 0) return `in ${mins}m`;
    return "in <1m";
  }

  function elClass(maxEl) {
    if (maxEl >= 40) return "high";
    if (maxEl >= 20) return "mid";
    return "low";
  }

  function paint() {
    body.replaceChildren();
    if (!rows.length) {
      const tr = document.createElement("tr");
      const td = document.createElement("td");
      td.colSpan = 7;
      td.className = "iss-empty";
      td.textContent = "No passes match those limits in the next 5 days.";
      tr.appendChild(td);
      body.appendChild(tr);
      return;
    }
    const zone = zoneName();
    const head = document.querySelector("#issTable thead th");
    if (head) head.textContent = zone;
    const now = Date.now();
    const leadMs = 30 * 60 * 1000;
    const nextSoon = rows.findIndex((item) => {
      const aos = new Date(item.aos).getTime();
      const los = new Date(item.los).getTime();
      return Number.isFinite(aos) && Number.isFinite(los) && now <= los && now >= aos - leadMs;
    });
    rows.forEach((item, index) => {
      const aos = localParts(item.aos);
      const los = localParts(item.los);
      const tr = document.createElement("tr");
      const cells = [
        aos.date,
        aos.time,
        los.date === aos.date ? los.time : `${los.time} ${los.date}`,
        durationText(item.duration_sec),
        `${Number(item.max_el).toFixed(1)}°`,
        `${compass(item.aos_az)} ${item.aos_az}° → ${compass(item.los_az)} ${item.los_az}°`,
        startsText(item.aos, item.los),
      ];
      cells.forEach((text, i) => {
        const td = document.createElement("td");
        td.textContent = text;
        if (i === 4) td.className = elClass(Number(item.max_el));
        if (i === 6 && text === "Up now") td.className = "up";
        tr.appendChild(td);
      });
      const peak = localParts(item.peak);
      tr.title = `Peak ${peak.time} ${zone} at ${item.peak_az}° ${compass(item.peak_az)} · max elevation ${Number(item.max_el).toFixed(1)}°`;
      if (index === nextSoon) tr.classList.add("iss-soon");
      body.appendChild(tr);
    });
  }

  async function loadIss() {
    if (document.body.classList.contains("iss-collapsed")) return;
    const minEl = clamp(elInput, 0, 80, 10);
    const minDur = clamp(durInput, 0, 20, 2);
    const count = clamp(countInput, 1, 24, 8);
    gridEl.value = gridEl.value.trim().toUpperCase();
    saveCriteria();
    meta.textContent = "Calculating passes…";
    const params = new URLSearchParams({
      min_el: String(minEl),
      min_duration: String(minDur),
      count: String(count),
    });
    const grid = gridEl.value;
    if (grid) params.set("grid", grid);
    try {
      if (!grid) {
        const home = await jsonFetch("/api/home");
        if (home && home.lat != null && home.lon != null) {
          params.set("lat", String(home.lat));
          params.set("lon", String(home.lon));
        }
      }
      const data = await jsonFetch("/api/iss?" + params.toString());
      if (!data.ok) throw new Error(data.error || "ISS prediction failed");
      rows = data.passes || [];
      const where = data.grid ? data.grid : `${Number(data.lat).toFixed(1)}, ${Number(data.lon).toFixed(1)}`;
      const age = data.tle_age_hours == null ? "" : ` · TLE ${data.tle_age_hours} h old`;
      meta.textContent = `${where} · ${rows.length} pass${rows.length === 1 ? "" : "es"} · ≥ ${data.min_el}° for ≥ ${data.min_duration_min} min · 145.800 MHz FM${age}`;
      paint();
    } catch (err) {
      rows = [];
      body.replaceChildren();
      meta.textContent = err && err.message ? err.message : "ISS prediction failed";
    }
  }

  function onCriteria() {
    loadIss();
  }

  elInput.addEventListener("change", onCriteria);
  durInput.addEventListener("change", onCriteria);
  countInput.addEventListener("change", onCriteria);
  gridEl.addEventListener("change", onCriteria);
  document.getElementById("issRefresh")?.addEventListener("click", loadIss);
  document.getElementById("issShow")?.addEventListener("click", () => {
    requestAnimationFrame(() => loadIss());
  });
  tick = window.setInterval(() => {
    if (rows.length && !document.body.classList.contains("iss-collapsed")) paint();
  }, 30000);
  window.addEventListener("beforeunload", () => window.clearInterval(tick));
  window.loadIss = loadIss;
  loadIss();
})();
