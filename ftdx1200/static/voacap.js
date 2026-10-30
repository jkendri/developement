(function () {
  const gridEl = document.getElementById("voacapGrid");
  const modeEl = document.getElementById("voacapMode");
  const powerEl = document.getElementById("voacapPower");
  const meta = document.getElementById("voacapMeta");
  const head = document.getElementById("voacapHead");
  const body = document.getElementById("voacapBody");
  if (!gridEl || !modeEl || !powerEl || !meta || !head || !body) return;

  try {
    const savedGrid = localStorage.getItem("ftdx-voacap-grid");
    if (savedGrid) gridEl.value = savedGrid;
    const savedMode = localStorage.getItem("ftdx-voacap-mode");
    if (savedMode) modeEl.value = savedMode;
    const savedPower = localStorage.getItem("ftdx-voacap-power");
    if (savedPower) powerEl.value = savedPower;
  } catch (_) { /* ignore */ }

  function tone(rel) {
    if (rel >= 0.6) return "good";
    if (rel >= 0.3) return "fair";
    if (rel >= 0.1) return "poor";
    return "closed";
  }

  function paint(data) {
    head.replaceChildren();
    body.replaceChildren();
    const corner = document.createElement("th");
    corner.textContent = "Path";
    head.appendChild(corner);
    const mufHead = document.createElement("th");
    mufHead.textContent = "MUF";
    mufHead.title = "Maximum usable frequency, MHz";
    head.appendChild(mufHead);
    (data.bands || []).forEach((band) => {
      const th = document.createElement("th");
      th.textContent = band.band;
      head.appendChild(th);
    });
    (data.paths || []).forEach((path) => {
      const tr = document.createElement("tr");
      const label = document.createElement("th");
      label.textContent = path.name;
      label.title = `${path.km} km · ${path.bearing}°`;
      tr.appendChild(label);
      const muf = document.createElement("td");
      muf.className = "muf";
      const mufMhz = Number(path.muf);
      muf.textContent = Number.isFinite(mufMhz) && mufMhz > 0 ? mufMhz.toFixed(1) : "—";
      const fot = Number(path.fot);
      muf.title = Number.isFinite(fot) && fot > 0
        ? `MUF ${muf.textContent} MHz · FOT ${fot.toFixed(1)} MHz · ${path.layer || ""}`.trim()
        : "Maximum usable frequency";
      tr.appendChild(muf);
      (path.bands || []).forEach((cell) => {
        const td = document.createElement("td");
        const pct = Math.round(Number(cell.rel) * 100);
        td.textContent = String(pct);
        td.className = tone(Number(cell.rel));
        td.title = `${path.name} ${cell.band} · ${pct}% · SNR ${cell.snr} dB · ${cell.hop}`;
        tr.appendChild(td);
      });
      body.appendChild(tr);
    });
    const where = data.grid ? data.grid : `${Number(data.lat).toFixed(1)}, ${Number(data.lon).toFixed(1)}`;
    meta.textContent = `${data.call ? data.call + " · " : ""}${where} · ${String(data.hour).padStart(2, "0")} UTC · SSN ${data.ssn} · ${data.power} W ${String(data.mode).toUpperCase()} · short path`;
  }

  async function loadVoacap() {
    meta.textContent = "Calculating…";
    const params = new URLSearchParams({
      mode: modeEl.value || "ssb",
      power: String(powerEl.value || "100"),
    });
    const grid = gridEl.value.trim();
    if (grid) params.set("grid", grid);
    try {
      const home = await jsonFetch("/api/home");
      if (!grid && home && home.lat != null && home.lon != null) {
        params.set("lat", home.lat);
        params.set("lon", home.lon);
      }
      const data = await jsonFetch("/api/voacap?" + params.toString());
      if (home && home.call) data.call = home.call;
      if (home && home.grid) data.grid = home.grid;
      if (!data.ok) throw new Error(data.error || "VOACAP failed");
      paint(data);
    } catch (err) {
      meta.textContent = err && err.message ? err.message : "VOACAP failed";
    }
  }

  gridEl.addEventListener("change", () => {
    try { localStorage.setItem("ftdx-voacap-grid", gridEl.value.trim().toUpperCase()); } catch (_) { /* ignore */ }
    gridEl.value = gridEl.value.trim().toUpperCase();
    loadVoacap();
  });
  modeEl.addEventListener("change", () => {
    try { localStorage.setItem("ftdx-voacap-mode", modeEl.value); } catch (_) { /* ignore */ }
    loadVoacap();
  });
  powerEl.addEventListener("change", () => {
    try { localStorage.setItem("ftdx-voacap-power", powerEl.value); } catch (_) { /* ignore */ }
    loadVoacap();
  });
  document.getElementById("voacapRefresh")?.addEventListener("click", loadVoacap);
  document.getElementById("voacapSite")?.addEventListener("click", () => {
    jsonFetch("/api/voacap/open", { method: "POST" }).catch(() => {});
  });
  loadVoacap();
})();
