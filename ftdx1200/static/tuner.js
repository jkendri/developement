(() => {
  const el = (id) => document.getElementById(id);
  if (!el("tunerPanel")) return;

  const history = [];
  let chartPaused = false;
  let chartVisible = false;
  let logVisible = localStorage.getItem("at1000-log") === "on";
  let lastState = null;
  let analogParam = localStorage.getItem("at1000-analog") || "fwd";
  const HIGH_SCALE = 500;
  let analogScale = localStorage.getItem("at1000-scale") === "100" ? 100 : HIGH_SCALE;
  let poBarOn = localStorage.getItem("at1000-bar") === "1";
  const PO_GRAPH_SEGS = 28;
  let needleShown = null;
  let needleTarget = 0;
  let lastNeedleTs = 0;
  const NEEDLE_UP_MS = 95;
  const NEEDLE_DOWN_MS = 175;

  function cssVar(name, fallback) {
    const value = getComputedStyle(document.body).getPropertyValue(name).trim();
    return value || fallback;
  }

  function barPct(value, max) {
    return `${Math.max(0, Math.min(100, (value / max) * 100))}%`;
  }

  function fillPorts(ports, saved) {
    const sel = el("tunerPort");
    sel.innerHTML = "";
    if (!ports.length) {
      const opt = document.createElement("option");
      opt.value = "";
      opt.textContent = "No tuner ports";
      sel.append(opt);
      return;
    }
    for (const p of ports) {
      const opt = document.createElement("option");
      opt.value = p.device;
      opt.textContent = `${p.device} — ${p.description || "serial"}`;
      if (p.device === saved) opt.selected = true;
      sel.append(opt);
    }
  }

  function writeLog(text) {
    const box = el("tunerLog");
    if (box.textContent !== text) box.textContent = text;
    box.scrollTop = box.scrollHeight;
  }

  function setLogVisible(on) {
    logVisible = on;
    localStorage.setItem("at1000-log", on ? "on" : "off");
    el("tunerLog").hidden = !on;
    el("tunerShowLog").checked = on;
    if (on && lastState) {
      writeLog((lastState.log || []).map((row) => `${row.t}  ${row.msg}`).join("\n"));
    }
  }

  function setChartVisible(on) {
    chartVisible = on;
    localStorage.setItem("at1000-chart", on ? "on" : "off");
    el("tunerChartWrap").classList.toggle("off", !on);
    el("tunerChartToggle").textContent = on ? "Hide" : "Show";
    el("tunerChartToggle").setAttribute("aria-pressed", on ? "true" : "false");
    if (on) drawChart();
  }

  function pushSample(state) {
    if (!chartVisible || chartPaused || !state.connected || !state.meter) return;
    const m = state.meter;
    history.push({
      t: Date.now(),
      fwd: m.fwd_live_w ?? m.fwd_w,
      refl: m.refl_live_w ?? m.refl_w,
      swr: m.swr_live ?? m.swr,
    });
    const cutoff = Date.now() - 130000;
    while (history.length && history[0].t < cutoff) history.shift();
  }

  function niceMax(value, floor) {
    const v = Math.max(floor, value);
    const mag = 10 ** Math.floor(Math.log10(v));
    return Math.ceil(v / mag) * mag;
  }

  function drawChart() {
    if (!chartVisible) return;
    const canvas = el("tunerStrip");
    const ctx = canvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    const cssW = canvas.clientWidth || 400;
    const cssH = canvas.clientHeight || 120;
    if (canvas.width !== Math.round(cssW * dpr) || canvas.height !== Math.round(cssH * dpr)) {
      canvas.width = Math.round(cssW * dpr);
      canvas.height = Math.round(cssH * dpr);
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);
    const pad = { l: 36, r: 36, t: 8, b: 18 };
    const plotW = cssW - pad.l - pad.r;
    const plotH = cssH - pad.t - pad.b;
    const windowMs = Number(el("tunerChartWindow").value) * 1000;
    const now = Date.now();
    const t0 = now - windowMs;
    const pts = history.filter((p) => p.t >= t0);
    const peakW = pts.reduce((m, p) => Math.max(m, p.fwd, p.refl), 0);
    const peakS = pts.reduce((m, p) => Math.max(m, p.swr), 1);
    const maxW = niceMax(peakW * 1.15, 50);
    const maxS = Math.max(3, Math.ceil(peakS * 2) / 2);
    ctx.fillStyle = cssVar("--bar-track", cssVar("--input", "#0b0f0a"));
    ctx.fillRect(0, 0, cssW, cssH);
    ctx.strokeStyle = cssVar("--line", "#31402c");
    ctx.lineWidth = 1;
    ctx.font = "11px Segoe UI, sans-serif";
    ctx.fillStyle = cssVar("--muted", "#9bb08a");
    for (let i = 0; i <= 4; i += 1) {
      const y = pad.t + (plotH * i) / 4;
      ctx.beginPath();
      ctx.moveTo(pad.l, y);
      ctx.lineTo(pad.l + plotW, y);
      ctx.stroke();
      const watts = maxW * (1 - i / 4);
      const swr = 1 + (maxS - 1) * (1 - i / 4);
      ctx.textAlign = "right";
      ctx.fillText(watts.toFixed(0), pad.l - 4, y + 3);
      ctx.textAlign = "left";
      ctx.fillText(`${swr.toFixed(1)}`, pad.l + plotW + 4, y + 3);
    }
    const xOf = (t) => pad.l + ((t - t0) / windowMs) * plotW;
    const yW = (w) => pad.t + plotH * (1 - w / maxW);
    const yS = (s) => pad.t + plotH * (1 - (s - 1) / (maxS - 1));
    function stroke(color, yFn, key) {
      if (pts.length < 2) return;
      ctx.beginPath();
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.6;
      pts.forEach((p, i) => {
        const x = xOf(p.t);
        const y = yFn(p[key]);
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.stroke();
    }
    stroke(cssVar("--accent", "#7cff6b"), yW, "fwd");
    stroke(cssVar("--warn", "#ffb347"), yW, "refl");
    stroke(cssVar("--bad", "#ff6b6b"), yS, "swr");
    ctx.strokeStyle = cssVar("--line", "#31402c");
    ctx.strokeRect(pad.l, pad.t, plotW, plotH);
    if (!pts.length) {
      ctx.fillStyle = cssVar("--muted", "#9bb08a");
      ctx.textAlign = "center";
      ctx.fillText("Connect to record traces", cssW / 2, pad.t + plotH / 2);
    }
  }

  function needleFrac(state) {
    const m = state && state.meter;
    if (analogParam === "swr") {
      const s = m ? Math.max(1, m.swr) : 1;
      if (s <= 1) return 0;
      if (s <= 1.5) return 0.28 * ((s - 1) / 0.5);
      if (s <= 2) return 0.28 + 0.22 * ((s - 1.5) / 0.5);
      if (s <= 3) return 0.5 + 0.22 * ((s - 2) / 1);
      return Math.min(1, 0.72 + 0.28 * Math.log10(s / 3) / Math.log10(10));
    }
    const watts = analogParam === "refl" ? (m ? m.refl_w : 0) : (m ? m.fwd_w : 0);
    return Math.max(0, Math.min(1, watts / analogScale));
  }

  function forwardFrac(state) {
    const watts = state && state.meter ? state.meter.fwd_w : 0;
    return Math.max(0, Math.min(1, watts / analogScale));
  }

  function snapNeedle(state) {
    needleShown = needleTarget = (poBarOn ? forwardFrac(state) : needleFrac(state));
  }

  function ensurePoGraph() {
    const track = el("tunerGraphTrack");
    const marks = el("tunerGraphMarks");
    if (!track || track.dataset.ready) return;
    track.dataset.ready = "1";
    for (let i = 0; i < PO_GRAPH_SEGS; i += 1) {
      track.append(document.createElement("i"));
      if (marks) marks.append(document.createElement("span"));
    }
  }

  function poMarks() {
    if (analogScale === 100) {
      return [[0, "0"], [0.25, "25"], [0.5, "50"], [0.75, "75"], [1, "100"]];
    }
    return [[0, "0"], [0.25, "125"], [0.5, "250"], [0.75, "375"], [1, "500"]];
  }

  function drawPoGraph(state) {
    ensurePoGraph();
    const track = el("tunerGraphTrack");
    const graph = el("tunerPoGraph");
    if (!track || !graph) return;
    const level = Math.max(0, Math.min(1, needleShown == null ? forwardFrac(state) : needleShown));
    const hotAt = 0.72;
    const m = state && state.meter;
    const swr = m ? Number(m.swr) : 0;
    const side = el("tunerGraphSide");
    if (side) {
      if (!m) side.textContent = "SWR";
      else if (swr >= 9.9) side.textContent = "SWR ∞";
      else side.textContent = `SWR ${swr.toFixed(1)}`;
    }
    const cells = el("tunerGraphMarks") ? el("tunerGraphMarks").children : [];
    const labeled = new Map();
    poMarks().forEach(([frac, text]) => {
      const idx = Math.max(0, Math.min(PO_GRAPH_SEGS - 1, Math.round(frac * (PO_GRAPH_SEGS - 1))));
      labeled.set(idx, text);
    });
    for (let i = 0; i < cells.length; i += 1) {
      const text = labeled.get(i) || "";
      if (cells[i].textContent !== text) cells[i].textContent = text;
      const value = Number(text);
      cells[i].classList.toggle("hot", Number.isFinite(value) && value / analogScale >= hotAt);
    }
    const segs = track.children;
    for (let i = 0; i < segs.length; i += 1) {
      const f0 = i / segs.length;
      const f1 = (i + 1) / segs.length;
      const mid = (f0 + f1) / 2;
      const lit = Math.max(0, Math.min(1, (level - f0) / (f1 - f0)));
      segs[i].style.setProperty("--lit", lit.toFixed(3));
      segs[i].style.removeProperty("--seg");
      segs[i].classList.toggle("hot", mid >= hotAt);
    }
  }

  function setPoBar(on) {
    poBarOn = !!on;
    el("tunerPanel").classList.toggle("tuner-meter-bar", poBarOn);
    const btn = el("tunerBarStyle");
    if (btn) {
      btn.textContent = poBarOn ? "Analog" : "Bar";
      btn.title = poBarOn ? "Show the analog wattmeter" : "Show power output as a bar graph";
      btn.setAttribute("aria-pressed", poBarOn ? "true" : "false");
      btn.classList.toggle("on", poBarOn);
    }
    try { localStorage.setItem("at1000-bar", poBarOn ? "1" : "0"); } catch (_) { /* ignore */ }
    snapNeedle(lastState);
    if (poBarOn) drawPoGraph(lastState);
    else drawAnalog(lastState);
  }

  function drawAnalog(state) {
    const canvas = el("tunerAnalog");
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    const cssW = canvas.clientWidth || 400;
    const cssH = canvas.clientHeight || 188;
    if (canvas.width !== Math.round(cssW * dpr) || canvas.height !== Math.round(cssH * dpr)) {
      canvas.width = Math.round(cssW * dpr);
      canvas.height = Math.round(cssH * dpr);
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);
    const faceRadius = Math.min(18, cssW * 0.05, cssH * 0.1);
    ctx.save();
    ctx.beginPath();
    if (ctx.roundRect) ctx.roundRect(0, 0, cssW, cssH, faceRadius);
    else ctx.rect(0, 0, cssW, cssH);
    ctx.clip();
    ctx.fillStyle = cssVar("--meter-face", "#141c12");
    ctx.fillRect(0, 0, cssW, cssH);

    const cx = cssW * 0.5;
    const cy = cssH * 0.9;
    const r = Math.min(cssW * 0.46, cssH * 0.8);
    const mid = Math.PI * 1.5;
    const sweep = Math.PI * 0.5;
    const startAng = mid - sweep;
    const endAng = mid + sweep;
    const angOf = (frac) => startAng + (endAng - startAng) * frac;
    const meterInk = cssVar("--ink", "#dce4ec");
    const meterRed = cssVar("--meter-red", "#c23b2a");

    ctx.beginPath();
    ctx.strokeStyle = meterInk;
    ctx.lineWidth = Math.max(2, r * 0.018);
    ctx.arc(cx, cy, r, startAng, endAng, false);
    ctx.stroke();
    ctx.beginPath();
    ctx.strokeStyle = meterRed;
    ctx.arc(cx, cy, r, angOf(0.72), endAng, false);
    ctx.stroke();

    const labelEvery = analogScale === 100 ? 10 : 50;
    const tickStep = analogScale === 100 ? 5 : 10;
    const labelPx = Math.max(8, Math.min(11, cssW * 0.028));
    const majorLen = Math.max(11, r * 0.14);
    const midLen = Math.max(7, r * 0.09);
    const minorLen = Math.max(4, r * 0.05);
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    for (let w = 0; w <= analogScale; w += tickStep) {
      const frac = w / analogScale;
      const a = angOf(frac);
      const major = w % labelEvery === 0;
      const ten = !major && analogScale !== 100 && w % 25 === 0;
      const tickLen = major ? majorLen : ten ? midLen : minorLen;
      ctx.beginPath();
      ctx.strokeStyle = meterInk;
      ctx.lineWidth = major ? 2 : 1;
      ctx.moveTo(cx + Math.cos(a) * (r - 4), cy + Math.sin(a) * (r - 4));
      ctx.lineTo(cx + Math.cos(a) * (r - tickLen), cy + Math.sin(a) * (r - tickLen));
      ctx.stroke();
      if (major) {
        ctx.fillStyle = meterInk;
        ctx.font = `${labelPx}px Segoe UI, sans-serif`;
        ctx.fillText(String(w), cx + Math.cos(a) * (r - tickLen - labelPx * 0.85), cy + Math.sin(a) * (r - tickLen - labelPx * 0.85));
      }
    }

    [[1.5, 0.28], [2, 0.5], [3, 0.72]].forEach(([label, frac]) => {
      const a = angOf(frac);
      ctx.fillStyle = meterInk;
      ctx.font = `${Math.max(8, labelPx)}px Segoe UI, sans-serif`;
      ctx.fillText(String(label), cx + Math.cos(a) * (r * 0.58), cy + Math.sin(a) * (r * 0.58));
    });
    const infA = angOf(0.97);
    ctx.fillStyle = meterRed;
    ctx.font = `bold ${Math.max(9, labelPx)}px Segoe UI, sans-serif`;
    ctx.fillText("∞", cx + Math.cos(infA) * (r * 0.52), cy + Math.sin(infA) * (r * 0.52));

    const frac = needleShown == null ? needleFrac(state) : needleShown;
    const na = angOf(Math.max(0, Math.min(1, frac)));
    ctx.strokeStyle = meterRed;
    ctx.lineWidth = Math.max(2.5, r * 0.02);
    ctx.lineCap = "round";
    ctx.beginPath();
    ctx.moveTo(cx - Math.cos(na) * 12, cy - Math.sin(na) * 12);
    ctx.lineTo(cx + Math.cos(na) * (r - 8), cy + Math.sin(na) * (r - 8));
    ctx.stroke();
    ctx.fillStyle = meterInk;
    ctx.beginPath();
    ctx.arc(cx, cy, Math.max(5, r * 0.045), 0, Math.PI * 2);
    ctx.fill();

    ctx.fillStyle = meterInk;
    ctx.textAlign = "center";
    ctx.font = `bold ${Math.max(9, labelPx)}px Segoe UI, sans-serif`;
    ctx.fillText("Watts", cx, cy - r * 0.46);
    ctx.fillText("SWR", cx, cy - r * 0.16);
    ctx.textAlign = "left";
    ctx.fillText("M-1000", 12, 16);
    ctx.restore();
  }

  function updateLeds(state) {
    el("tunerLed100").classList.toggle("on", analogScale === 100);
    el("tunerLed1000").classList.toggle("on", analogScale === HIGH_SCALE);
    const peak = !state || (state.meter_mode || "peak") !== "average";
    el("tunerLedPeak").classList.toggle("on", peak);
    el("tunerLedAve").classList.toggle("on", !peak);
    el("tunerLedFwd").classList.toggle("on", analogParam === "fwd");
    el("tunerLedRev").classList.toggle("on", analogParam === "refl");
    el("tunerLedSwr").classList.toggle("on", analogParam === "swr");
  }

  function renderTuner(state) {
    lastState = state;
    pushSample(state);
    drawChart();
    needleTarget = poBarOn ? forwardFrac(state) : needleFrac(state);
    if (needleShown == null) needleShown = needleTarget;
    if (poBarOn) drawPoGraph(state);
    updateLeds(state);
    const connected = !!state.connected;
    el("tunerConnect").disabled = connected;
    el("tunerDemo").disabled = connected;
    el("tunerDisconnect").disabled = !connected;
    el("tunerPort").disabled = connected;
    if (typeof state.auto_connect === "boolean") el("tunerAuto").checked = state.auto_connect;
    document.querySelectorAll("[data-tuner-cmd]").forEach((btn) => {
      btn.disabled = !connected || state.mode === "control";
    });
    const badge = el("tunerBadge");
    if (!connected) {
      badge.textContent = "Disconnected";
      badge.className = "badge off";
    } else if (state.demo) {
      badge.textContent = "Demo";
      badge.className = "badge demo";
    } else {
      badge.textContent = state.port || "On";
      badge.className = "badge on";
    }
    const peak = (state.meter_mode || "peak") !== "average";
    el("tunerMeterPeak").classList.toggle("on", peak);
    el("tunerMeterAvg").classList.toggle("on", !peak);
    el("tunerFwdLabel").textContent = peak ? "Forward (peak)" : "Forward (avg)";
    el("tunerFwdAltLabel").textContent = peak ? "avg" : "peak";
    const m = state.meter;
    if (m) {
      el("tunerFwd").textContent = Number(m.fwd_w).toFixed(1);
      const alt = peak ? (m.fwd_live_w ?? m.fwd_w) : (m.fwd_peak_w ?? m.fwd_w);
      el("tunerFwdAvg").textContent = Number(alt).toFixed(1);
      el("tunerFwdRaw").textContent = String(m.fwd_raw ?? 0);
      el("tunerRefl").textContent = Number(m.refl_w).toFixed(1);
      el("tunerSwr").textContent = Number(m.swr).toFixed(2);
      el("tunerFwdBar").style.width = barPct(m.fwd_w, analogScale);
      el("tunerReflBar").style.width = barPct(m.refl_w, Math.max(50, analogScale / 5));
      el("tunerSwrBar").style.width = barPct(m.swr - 1, 3);
      el("tunerBand").textContent = m.band || "—";
    }
    el("tunerAntenna").textContent = state.antenna ? `ANT ${state.antenna}` : "—";
    el("tunerMode").textContent = state.tune_mode
      ? state.tune_mode.charAt(0).toUpperCase() + state.tune_mode.slice(1)
      : "—";
    el("tunerLast").textContent = state.last_tune
      ? `${state.last_tune.label} (${state.last_tune.code})`
      : "—";
    if (state.volt_ps != null) {
      const v = Number(state.volt_ps).toFixed(1);
      if ([...el("tunerVolts").options].some((opt) => opt.value === v)) el("tunerVolts").value = v;
    }
    if (logVisible) writeLog((state.log || []).map((row) => `${row.t}  ${row.msg}`).join("\n"));
  }

  function needleFrame(ts) {
    requestAnimationFrame(needleFrame);
    const dt = lastNeedleTs ? Math.min(48, ts - lastNeedleTs) : 16;
    lastNeedleTs = ts;
    if (needleShown == null) needleShown = needleTarget;
    const tau = needleTarget >= needleShown ? NEEDLE_UP_MS : NEEDLE_DOWN_MS;
    const k = 1 - Math.exp(-dt / tau);
    const next = needleShown + (needleTarget - needleShown) * k;
    if (Math.abs(next - needleShown) < 0.0004 && Math.abs(needleTarget - needleShown) < 0.0004) return;
    needleShown = Math.abs(needleTarget - next) < 0.0004 ? needleTarget : next;
    if (document.body.classList.contains("tuner-collapsed")) return;
    if (poBarOn) drawPoGraph(lastState);
    else drawAnalog(lastState);
  }
  requestAnimationFrame(needleFrame);

  async function refreshPorts() {
    const data = await jsonFetch("/api/tuner/ports");
    fillPorts(data.ports || [], data.saved || "");
    if (typeof data.auto_connect === "boolean") el("tunerAuto").checked = data.auto_connect;
  }

  async function pollTuner() {
    try {
      renderTuner(await jsonFetch("/api/tuner/status"));
    } catch (err) {
      writeLog(String(err.message || err));
    }
  }

  el("tunerRefresh").addEventListener("click", () => refreshPorts().catch((err) => alert(err.message)));
  el("tunerConnect").addEventListener("click", async () => {
    try {
      renderTuner(await jsonFetch("/api/tuner/connect", {
        method: "POST",
        body: JSON.stringify({ port: el("tunerPort").value, demo: false }),
      }));
    } catch (err) {
      alert(err.message);
    }
  });
  el("tunerDemo").addEventListener("click", async () => {
    try {
      renderTuner(await jsonFetch("/api/tuner/connect", {
        method: "POST",
        body: JSON.stringify({ port: "demo", demo: true }),
      }));
    } catch (err) {
      alert(err.message);
    }
  });
  el("tunerDisconnect").addEventListener("click", async () => {
    renderTuner(await jsonFetch("/api/tuner/disconnect", { method: "POST", body: "{}" }));
  });
  el("tunerAuto").addEventListener("change", async () => {
    const on = el("tunerAuto").checked;
    try {
      renderTuner(await jsonFetch("/api/tuner/settings", {
        method: "POST",
        body: JSON.stringify({ auto_connect: on }),
      }));
      if (on && !el("tunerConnect").disabled) {
        const port = el("tunerPort").value;
        if (port) {
          renderTuner(await jsonFetch("/api/tuner/connect", {
            method: "POST",
            body: JSON.stringify({ port, demo: false }),
          }));
        }
      }
    } catch (err) {
      el("tunerAuto").checked = !on;
      alert(err.message);
    }
  });
  el("tunerVolts").addEventListener("change", () => {
    jsonFetch("/api/tuner/settings", {
      method: "POST",
      body: JSON.stringify({ volt_ps: Number(el("tunerVolts").value) }),
    }).then(renderTuner).catch((err) => alert(err.message));
  });
  el("tunerCalibrate").addEventListener("click", async () => {
    try {
      renderTuner(await jsonFetch("/api/tuner/settings", {
        method: "POST",
        body: JSON.stringify({ calibrate: true, known_w: Number(el("tunerCalWatts").value) }),
      }));
    } catch (err) {
      alert(err.message);
    }
  });
  el("tunerCalReset").addEventListener("click", async () => {
    try {
      renderTuner(await jsonFetch("/api/tuner/settings", {
        method: "POST",
        body: JSON.stringify({ watt_cal: 1 }),
      }));
    } catch (err) {
      alert(err.message);
    }
  });
  async function setMeterMode(mode) {
    try {
      renderTuner(await jsonFetch("/api/tuner/settings", {
        method: "POST",
        body: JSON.stringify({ meter_mode: mode }),
      }));
    } catch (err) {
      alert(err.message);
    }
  }
  el("tunerMeterPeak").addEventListener("click", () => setMeterMode("peak"));
  el("tunerMeterAvg").addEventListener("click", () => setMeterMode("average"));
  el("tunerAnalogPeak").addEventListener("click", () => setMeterMode("peak"));
  el("tunerAnalogAve").addEventListener("click", () => setMeterMode("average"));
  el("tunerScale").addEventListener("click", () => {
    analogScale = analogScale === HIGH_SCALE ? 100 : HIGH_SCALE;
    localStorage.setItem("at1000-scale", String(analogScale));
    updateLeds(lastState);
    snapNeedle(lastState);
    if (poBarOn) drawPoGraph(lastState);
    else drawAnalog(lastState);
    if (lastState) renderTuner(lastState);
  });
  el("tunerBarStyle").addEventListener("click", () => setPoBar(!poBarOn));
  el("tunerAnalogMeter").addEventListener("click", () => {
    analogParam = analogParam === "fwd" ? "refl" : analogParam === "refl" ? "swr" : "fwd";
    localStorage.setItem("at1000-analog", analogParam);
    updateLeds(lastState);
    snapNeedle(lastState);
    drawAnalog(lastState);
  });
  document.querySelectorAll("[data-tuner-cmd]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      btn.classList.add("busy");
      try {
        renderTuner(await jsonFetch("/api/tuner/command", {
          method: "POST",
          body: JSON.stringify({ name: btn.dataset.tunerCmd }),
        }));
      } catch (err) {
        alert(err.message);
      } finally {
        btn.classList.remove("busy");
      }
    });
  });
  el("tunerChartToggle").addEventListener("click", () => setChartVisible(!chartVisible));
  el("tunerShowLog").addEventListener("change", () => setLogVisible(el("tunerShowLog").checked));
  el("tunerChartPause").addEventListener("click", () => {
    chartPaused = !chartPaused;
    el("tunerChartPause").textContent = chartPaused ? "Resume" : "Pause";
  });
  el("tunerChartClear").addEventListener("click", () => {
    history.length = 0;
    drawChart();
  });
  el("tunerChartWindow").addEventListener("change", drawChart);
  if (window.ResizeObserver) {
    const watch = new ResizeObserver(() => {
      drawChart();
      if (poBarOn) drawPoGraph(lastState);
      else drawAnalog(lastState);
    });
    watch.observe(el("tunerPanel"));
  }
  function setSetupOpen(on) {
    el("tunerPanel").classList.toggle("setup-collapsed", !on);
    localStorage.setItem("ftdx-tuner-setup", on ? "1" : "0");
    const btn = el("tunerSetupToggle");
    btn.textContent = on ? "Hide setup" : "Setup";
    btn.setAttribute("aria-pressed", on ? "true" : "false");
  }
  setSetupOpen(localStorage.getItem("ftdx-tuner-setup") !== "0");
  el("tunerSetupToggle").addEventListener("click", () => {
    setSetupOpen(el("tunerPanel").classList.contains("setup-collapsed"));
  });
  function setStatusOpen(on) {
    el("tunerPanel").classList.toggle("status-collapsed", !on);
    localStorage.setItem("ftdx-tuner-status", on ? "1" : "0");
    const btn = el("tunerStatusToggle");
    btn.textContent = on ? "Hide status" : "Status";
    btn.setAttribute("aria-pressed", on ? "true" : "false");
  }
  setStatusOpen(localStorage.getItem("ftdx-tuner-status") !== "0");
  el("tunerStatusToggle").addEventListener("click", () => {
    setStatusOpen(el("tunerPanel").classList.contains("status-collapsed"));
  });
  function setKeysOpen(on) {
    el("tunerPanel").classList.toggle("keys-collapsed", !on);
    localStorage.setItem("ftdx-tuner-keys", on ? "1" : "0");
    const btn = el("tunerKeysToggle");
    btn.textContent = on ? "Hide keys" : "Keys";
    btn.setAttribute("aria-pressed", on ? "true" : "false");
  }
  setKeysOpen(localStorage.getItem("ftdx-tuner-keys") !== "0");
  el("tunerKeysToggle").addEventListener("click", () => {
    setKeysOpen(el("tunerPanel").classList.contains("keys-collapsed"));
  });
  setChartVisible(chartVisible);
  setLogVisible(logVisible);
  window.addEventListener("ftdx-colors", () => {
    drawChart();
    if (poBarOn) drawPoGraph(lastState);
    else drawAnalog(lastState);
  });
  setPoBar(poBarOn);
  updateLeds(null);
  if (!poBarOn) drawAnalog(null);
  drawChart();
  refreshPorts()
    .then(async () => {
      const state = await jsonFetch("/api/tuner/status");
      renderTuner(state);
      if (state.connected || !el("tunerAuto").checked) return;
      const port = el("tunerPort").value;
      if (!port) return;
      renderTuner(await jsonFetch("/api/tuner/connect", {
        method: "POST",
        body: JSON.stringify({ port, demo: false }),
      }));
    })
    .catch(() => {});
  setInterval(pollTuner, 120);
})();
