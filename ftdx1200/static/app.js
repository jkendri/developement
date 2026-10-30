const $ = (id) => document.getElementById(id);
if (new URLSearchParams(location.search).get("embed") === "1") {
  document.documentElement.classList.add("embed");
  document.body.classList.add("embed");
}

async function jsonFetch(url, options) {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const text = await res.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; } catch (_) {
    data = { error: text.replace(/<[^>]+>/g, " ").trim().slice(0, 180) };
  }
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

function fillPorts(ports, saved) {
  const sel = $("port");
  sel.innerHTML = "";
  if (!ports.length) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = "No COM ports found";
    sel.append(opt);
    return;
  }
  for (const p of ports) {
    const opt = document.createElement("option");
    opt.value = p.device;
    opt.textContent = `${p.device} — ${p.description}`;
    if (p.device === saved) opt.selected = true;
    sel.append(opt);
  }
}

const STEPS = [
  ["-10 k", -10000],
  ["-1 k", -1000],
  ["-100", -100],
  ["-10", -10],
  ["+10", 10],
  ["+100", 100],
  ["+1 k", 1000],
  ["+10 k", 10000],
];

function ensureKeys() {
  if ($("steps").dataset.ready) return;
  $("steps").dataset.ready = "1";
  for (const [label, hz] of STEPS) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = label;
    btn.addEventListener("click", () => send({ nudge: hz }));
    $("steps").append(btn);
  }
}

function renderModes(state) {
  const host = $("modes");
  const modes = state.modes || [];
  const current = Date.now() < modeLockUntil && lockedMode ? lockedMode : state.mode;
  if (!host.dataset.ready) {
    host.dataset.ready = "1";
    for (const name of modes) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.dataset.mode = name;
      btn.textContent = name;
      btn.addEventListener("click", () => {
        lockedMode = name;
        modeLockUntil = Date.now() + 1200;
        host.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.mode === name));
        send({ mode: name });
      });
      host.append(btn);
    }
  }
  host.querySelectorAll("button").forEach((btn) => {
    btn.classList.toggle("on", btn.dataset.mode === current);
  });
}

function renderBands(state) {
  const host = $("bands");
  const bands = state.bands || [];
  if (!host.dataset.ready) {
    host.dataset.ready = "1";
    for (const band of bands) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.dataset.band = band.id;
      btn.textContent = band.id;
      btn.addEventListener("click", () => send({ band: band.id }));
      host.append(btn);
    }
  }
}

function writeLog(rows) {
  const el = $("log");
  const text = (rows || []).map((row) => `${row.t}  ${row.msg}`).join("\n");
  if (el.textContent !== text) el.textContent = text;
  el.scrollTop = el.scrollHeight;
}

function render(state) {
  const badge = $("linkBadge");
  badge.classList.remove("off", "demo");
  if (!state.connected) {
    badge.textContent = "Disconnected";
    badge.classList.add("off");
  } else if (state.demo) {
    badge.textContent = "Demo";
    badge.classList.add("demo");
  } else {
    badge.textContent = `Connected · ${state.port}`;
  }
  $("connect").disabled = state.connected;
  $("demo").disabled = state.connected;
  $("disconnect").disabled = !state.connected;
  $("powerOn").disabled = !state.connected;
  $("powerOff").disabled = !state.connected;
  if (!digitTuning && !dialing && Date.now() > tuneLockUntil) {
    if (state.hz) lastHz = state.hz;
    if (state.mhz && document.activeElement !== $("freq")) {
      $("freq").value = state.mhz;
      paintFreqDigits();
    }
  }
  $("vfoLabel").textContent = state.vfo || "VFO-A";
  if (state.mode) lastMode = state.mode;
  applyLive(state);
  paintPanadapter();
  if (document.activeElement !== $("power") && Date.now() > powerLockUntil) {
    $("power").value = String(state.power || 20);
  }
  $("powerVal").textContent = `${$("power").value} W`;
  if (!afDragging && Date.now() > afLockUntil) {
    if (state.af != null) afCatLive = Number(state.af);
    $("af").value = String(afCatToUi(afCatLive));
    $("afVal").textContent = `${$("af").value}%`;
  }
  if ($("rfGain") && !rfGainDragging && Date.now() > rfGainLockUntil) {
    if (state.rf_gain != null) rfGainCatLive = Number(state.rf_gain);
    $("rfGain").value = String(rfGainCatToUi(rfGainCatLive));
    $("rfGainVal").textContent = `${$("rfGain").value}%`;
  }
  maybeForceStartupRfGain(state);
  const audio = state.audio || {};
  $("rxMonitor").classList.toggle("on", !!audio.monitor);
  $("rxMute").classList.toggle("on", !!audio.mute);
  if (document.activeElement !== $("rxVol")) {
    $("rxVol").value = String(Math.round((audio.volume ?? 0.8) * 100));
  }
  applyAudioLive(audio);
  ensureKeys();
  renderModes(state);
  renderBands(state);
  writeLog(state.log);
}

let needleShown = null;
let needleTarget = 0;
let poShown = null;
let poTarget = 0;
let swrLive = 0;
let meterTx = false;
let lastNeedleTs = 0;
const NEEDLE_UP_MS = 45;
const NEEDLE_DOWN_MS = 110;

function sNeedleFrac(sm) {
  const v = Math.max(0, Math.min(255, Number(sm) || 0));
  if (v <= 100) return (v / 100) * 0.62;
  return 0.62 + ((v - 100) / 155) * 0.38;
}

const PO_SCALE = [
  [0, 0],
  [10, 0.25],
  [25, 0.48],
  [50, 0.62],
  [100, 1],
];

function poFrac(watts) {
  const w = Math.max(0, Math.min(100, Number(watts) || 0));
  for (let i = 1; i < PO_SCALE.length; i += 1) {
    if (w <= PO_SCALE[i][0]) {
      const [a, fa] = PO_SCALE[i - 1];
      const [b, fb] = PO_SCALE[i];
      return fa + ((w - a) / (b - a)) * (fb - fa);
    }
  }
  return 1;
}

function swrFromRm(rm) {
  const v = Math.max(0, Math.min(255, Number(rm) || 0));
  const pts = [
    [0, 1],
    [48, 1.5],
    [80, 2],
    [120, 3],
    [255, 5],
  ];
  for (let i = 1; i < pts.length; i += 1) {
    if (v <= pts[i][0]) {
      const [a, sa] = pts[i - 1];
      const [b, sb] = pts[i];
      return sa + ((v - a) / (b - a)) * (sb - sa);
    }
  }
  return 5;
}

function stepNeedle(shown, target, dt) {
  if (shown == null) return target;
  const tau = target >= shown ? NEEDLE_UP_MS : NEEDLE_DOWN_MS;
  const k = 1 - Math.exp(-dt / tau);
  const next = shown + (target - shown) * k;
  if (Math.abs(next - shown) < 0.0003 && Math.abs(target - shown) < 0.0003) return target;
  return next;
}

function formatClockDate(year, month, day, weekday) {
  const days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const pad = (n) => String(n).padStart(2, "0");
  return `${days[weekday]} ${pad(day)} ${months[month]}`;
}

function placeLocalClockFirst() {
  const local = $("localClock");
  const box = local && local.closest(".utc-clock");
  const pair = box && box.parentElement;
  if (pair && pair.firstElementChild !== box) pair.insertBefore(box, pair.firstElementChild);
}

function drawUtcClock() {
  placeLocalClockFirst();
  const utc = $("utcClock");
  const local = $("localClock");
  const utcDate = $("utcDate");
  const localDate = $("localDate");
  const now = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  if (utc) utc.textContent = `${pad(now.getUTCHours())}:${pad(now.getUTCMinutes())}:${pad(now.getUTCSeconds())}`;
  if (local) local.textContent = `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
  if (utcDate) utcDate.textContent = formatClockDate(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), now.getUTCDay());
  if (localDate) localDate.textContent = formatClockDate(now.getFullYear(), now.getMonth(), now.getDate(), now.getDay());
}

function loadSolarBanner() {
  const img = $("solarBanner");
  const credit = $("solarCredit");
  if (!img) return;
  img.onload = () => {
    if (credit) credit.textContent = "N0NBH · hamqsl.com";
  };
  img.onerror = () => {
    if (credit) credit.textContent = "HamQSL solar banner unavailable";
  };
  img.src = `/api/solar?t=${Date.now()}`;
}

const S_GRAPH_SEGS = 28;

function ensureSGraph() {
  const track = $("sGraphTrack");
  const marks = $("sGraphMarks");
  if (!track || track.dataset.ready) return;
  track.dataset.ready = "1";
  for (let i = 0; i < S_GRAPH_SEGS; i += 1) {
    const seg = document.createElement("i");
    track.append(seg);
  }
  if (marks) {
    for (let i = 0; i < S_GRAPH_SEGS; i += 1) {
      const cell = document.createElement("span");
      marks.append(cell);
    }
  }
}

function drawAnalogScale() {
  const canvas = $("sAnalog");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const cssW = canvas.clientWidth || 360;
  const cssH = canvas.clientHeight || 180;
  if (canvas.width !== Math.round(cssW * dpr) || canvas.height !== Math.round(cssH * dpr)) {
    canvas.width = Math.round(cssW * dpr);
    canvas.height = Math.round(cssH * dpr);
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssW, cssH);
  const css = getComputedStyle(document.body);
  ctx.fillStyle = css.getPropertyValue("--meter-face").trim() || "#141c12";
  ctx.fillRect(0, 0, cssW, cssH);

  const face = Math.min(cssW, cssH);
  const numPx = Math.max(7, Math.min(13, face * 0.1));
  const titlePx = Math.max(8, Math.min(12, face * 0.11));
  const padTop = Math.max(numPx * 1.7 + 6, cssH * 0.12);
  const cx = cssW * 0.5;
  const r = Math.max(8, Math.min(cssW * 0.46, cssH - padTop - Math.max(16, numPx + 6)));
  const cy = padTop + r;
  const sweep = 1.32;
  const mid = 1.5 * Math.PI;
  const startAng = mid - sweep;
  const endAng = mid + sweep;
  const span = endAng - startAng;
  const angOf = (frac) => startAng + span * frac;
  const ink = css.getPropertyValue("--ink").trim() || "#dce8d0";
  const red = css.getPropertyValue("--meter-red").trim() || "#c23b2a";
  const green = css.getPropertyValue("--live").trim() || css.getPropertyValue("--accent").trim() || "#7cff6b";
  const numFace = `"Cascadia Mono", Consolas, monospace`;
  const tickOut = Math.max(4, r * 0.012);
  const tickMajor = Math.max(12, Math.min(22, r * 0.09));
  const tickMinor = Math.max(8, Math.min(14, r * 0.05));
  const labelR = r + numPx * 1.25;

  ctx.beginPath();
  ctx.strokeStyle = green;
  ctx.lineWidth = 5;
  ctx.arc(cx, cy, r, startAng, angOf(0.62), false);
  ctx.stroke();
  ctx.beginPath();
  ctx.strokeStyle = red;
  ctx.lineWidth = 6;
  ctx.arc(cx, cy, r, angOf(0.62), endAng, false);
  ctx.stroke();

  const labelAt = (text, frac, color, font) => {
    const a = angOf(frac);
    ctx.save();
    ctx.fillStyle = color;
    ctx.font = font;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(text, cx + Math.cos(a) * labelR, cy + Math.sin(a) * labelR);
    ctx.restore();
  };
  const tickAt = (frac, major, color) => {
    const a = angOf(frac);
    ctx.beginPath();
    ctx.strokeStyle = color;
    ctx.lineWidth = major ? 2 : 1;
    ctx.moveTo(cx + Math.cos(a) * (r + tickOut), cy + Math.sin(a) * (r + tickOut));
    ctx.lineTo(cx + Math.cos(a) * (r + (major ? tickMajor : tickMinor)), cy + Math.sin(a) * (r + (major ? tickMajor : tickMinor)));
    ctx.stroke();
  };
  const needle = (frac, color, width) => {
    const na = angOf(Math.max(0, Math.min(1, frac)));
    const hub = Math.max(10, r * 0.08);
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    ctx.lineCap = "round";
    ctx.beginPath();
    ctx.moveTo(cx + Math.cos(na) * hub, cy + Math.sin(na) * hub);
    ctx.lineTo(cx + Math.cos(na) * (r - 6), cy + Math.sin(na) * (r - 6));
    ctx.stroke();
  };

  if (meterTx) {
    const poMajors = new Set([0, 5, 10, 25, 50, 100]);
    for (let w = 0; w <= 100; w += 5) {
      const frac = poFrac(w);
      const major = poMajors.has(w);
      const color = w >= 50 ? red : ink;
      tickAt(frac, major, color);
      if (major) labelAt(String(w), frac, color, `bold ${numPx}px ${numFace}`);
    }
    ctx.fillStyle = ink;
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    ctx.font = `bold ${titlePx}px Segoe UI, sans-serif`;
    ctx.fillText("PO", cssW * 0.12, padTop * 0.22);
    ctx.fillStyle = red;
    const swr = swrFromRm(swrLive);
    ctx.font = `bold ${titlePx}px ${numFace}`;
    ctx.fillText(swr >= 4.5 ? "SWR ∞" : `SWR ${swr.toFixed(1)}`, cssW * 0.82, padTop * 0.22);
    needle(poShown == null ? poTarget : poShown, red, 4);
  } else {
    for (let n = 1; n <= 9; n += 1) {
      const frac = (n / 9) * 0.62;
      const major = n % 2 === 1;
      tickAt(frac, major, ink);
      if (major) labelAt(String(n), frac, ink, `bold ${numPx}px ${numFace}`);
    }
    [[0.78, "+20"], [1, "+60"]].forEach(([frac, text]) => {
      tickAt(frac, true, red);
      labelAt(text, frac, red, `bold ${numPx}px ${numFace}`);
    });
    ctx.fillStyle = ink;
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    ctx.font = `bold ${titlePx}px Segoe UI, sans-serif`;
    ctx.fillText("S", cssW * 0.12, padTop * 0.22);
    ctx.fillStyle = red;
    ctx.fillText("dB", cssW * 0.88, padTop * 0.22);
    needle(needleShown == null ? needleTarget : needleShown, red, 4);
  }
  ctx.beginPath();
  ctx.fillStyle = red;
  ctx.arc(cx, cy, Math.max(5, face * 0.018), 0, Math.PI * 2);
  ctx.fill();
  canvas.setAttribute("aria-label", meterTx ? "RF power and SWR meter" : "Analog S-meter");
}

function drawSAnalog() {
  drawAnalogScale();
  ensureSGraph();
  const track = $("sGraphTrack");
  const graph = $("sGraph");
  if (!track || !graph) return;
  const level = Math.max(0, Math.min(1, meterTx
    ? (poShown == null ? poTarget : poShown)
    : (needleShown == null ? needleTarget : needleShown)));
  const hotAt = meterTx ? poFrac(50) : 0.62;
  const marks = meterTx
    ? [[0, "0"], [poFrac(10), "10"], [poFrac(25), "25"], [poFrac(50), "50"], [1, "100"]]
    : [[0.62 / 9, "1"], [0.62 * 3 / 9, "3"], [0.62 * 5 / 9, "5"], [0.62 * 7 / 9, "7"], [0.62, "9"], [0.78, "+20"], [1, "+60"]];
  const title = $("sGraphTitle");
  const side = $("sGraphSide");
  if (title) title.textContent = meterTx ? "PO" : "S";
  if (side) {
    if (meterTx) {
      const swr = swrFromRm(swrLive);
      side.textContent = swr >= 4.5 ? "SWR ∞" : `SWR ${swr.toFixed(1)}`;
    } else {
      side.textContent = "dB";
    }
  }
  graph.setAttribute("aria-label", meterTx ? "RF power and SWR meter" : "S-meter");
  const cells = $("sGraphMarks") ? $("sGraphMarks").children : [];
  const labeled = new Map();
  marks.forEach(([frac, text]) => {
    const idx = Math.max(0, Math.min(S_GRAPH_SEGS - 1, Math.round(frac * (S_GRAPH_SEGS - 1))));
    labeled.set(idx, text);
  });
  for (let i = 0; i < cells.length; i += 1) {
    const text = labeled.get(i) || "";
    if (cells[i].textContent !== text) cells[i].textContent = text;
    cells[i].classList.toggle("hot", text.startsWith("+") || text === "50" || text === "100");
  }
  const segs = track.children;
  for (let i = 0; i < segs.length; i += 1) {
    const f0 = i / segs.length;
    const f1 = (i + 1) / segs.length;
    const mid = (f0 + f1) / 2;
    const lit = Math.max(0, Math.min(1, (level - f0) / (f1 - f0)));
    segs[i].style.setProperty("--lit", lit.toFixed(3));
    if (meterTx) {
      segs[i].style.removeProperty("--seg");
      segs[i].classList.toggle("hot", mid >= hotAt);
    } else {
      segs[i].classList.remove("hot");
      segs[i].style.setProperty("--seg", sBarColor(mid));
    }
  }
}

function sBarColor(frac) {
  const s5 = (5 / 9) * 0.62;
  const s9 = 0.62;
  const plus20 = 0.78;
  const green = [124, 255, 107];
  const yellow = [255, 220, 60];
  const orange = [255, 140, 32];
  const red = [194, 59, 42];
  const mix = (a, b, t) => {
    const u = Math.max(0, Math.min(1, t));
    return a.map((v, i) => Math.round(v + (b[i] - v) * u));
  };
  const rgb = (c) => `rgb(${c[0]}, ${c[1]}, ${c[2]})`;
  if (frac <= s5) return rgb(green);
  if (frac <= s9) return rgb(mix(green, yellow, (frac - s5) / (s9 - s5)));
  if (frac <= plus20) return rgb(mix(yellow, orange, (frac - s9) / (plus20 - s9)));
  return rgb(mix(orange, red, (frac - plus20) / (1 - plus20)));
}

function needleFrame(ts) {
  requestAnimationFrame(needleFrame);
  const dt = lastNeedleTs ? Math.min(48, ts - lastNeedleTs) : 16;
  lastNeedleTs = ts;
  if (meterTx) {
    const nextPo = stepNeedle(poShown, poTarget, dt);
    if (nextPo === poShown) return;
    poShown = nextPo;
  } else {
    const next = stepNeedle(needleShown, needleTarget, dt);
    if (next === needleShown) return;
    needleShown = next;
  }
  drawSAnalog();
}

function applyLive(state) {
  const tx = !!state.tx;
  $("txLamp").textContent = tx ? "TX" : "RX";
  $("txLamp").classList.toggle("on", tx);
  $("ptt").classList.toggle("on", tx);
  if (tx !== meterTx) {
    meterTx = tx;
    if (tx) {
      poShown = 0;
    }
    drawSAnalog();
  }
  if (tx) {
    poTarget = poFrac(Number(state.power) || 0);
    const nextSwr = Number(state.swr_meter) || 0;
    if (nextSwr !== swrLive) {
      swrLive = nextSwr;
      drawSAnalog();
    }
    if (poShown == null) poShown = poTarget;
  } else {
    needleTarget = sNeedleFrac(state.s_meter);
    if (needleShown == null) needleShown = needleTarget;
  }
  applyAudioLive(state.audio || {});
}

function applyAudioLive(audio) {
  $("rxBar").style.width = `${Math.max(0, Math.min(100, (audio.level || 0) * 100))}%`;
  $("rxErr").textContent = audio.error || (audio.available === false ? "no audio" : "");
}

async function send(body) {
  try {
    render(await jsonFetch("/api/command", { method: "POST", body: JSON.stringify(body) }));
  } catch (err) {
    alert(err.message);
  }
}

let lastMode = "USB";
let favorites = [];

function favCaption(item) {
  const mhz = item.mhz || "";
  const mode = item.mode ? ` · ${item.mode}` : "";
  if (item.name && item.name !== mhz) return `${item.name}  ${mhz}${mode}`;
  return `${mhz}${mode}`;
}

function renderFavorites(items) {
  favorites = items || [];
  const host = $("favList");
  if (!host) return;
  host.innerHTML = "";
  if (!favorites.length) {
    const empty = document.createElement("p");
    empty.className = "hint";
    empty.textContent = "No favorites yet. Tune, then Save current.";
    host.append(empty);
    return;
  }
  for (const item of favorites) {
    const row = document.createElement("div");
    row.className = "fav-item";
    const go = document.createElement("button");
    go.type = "button";
    go.className = "fav-go";
    go.textContent = favCaption(item);
        go.addEventListener("click", async () => {
      try {
        if (potaScanOn) stopPotaScan();
        const body = {};
        if (item.mode) {
          body.mode = item.mode;
          lockedMode = item.mode;
          modeLockUntil = Date.now() + 1200;
        }
        if (item.hz) body.hz = item.hz;
        else if (item.mhz) body.mhz = item.mhz;
        await jsonFetch("/api/command", { method: "POST", body: JSON.stringify(body) });
        lastHz = item.hz || Math.round(Number(item.mhz) * 1_000_000);
        paintFreqDigits();
        render(await jsonFetch("/api/status?full=1"));
      } catch (err) {
        alert(err.message);
      }
    });
    const del = document.createElement("button");
    del.type = "button";
    del.className = "fav-del";
    del.setAttribute("aria-label", `Remove ${favCaption(item)}`);
    del.textContent = "×";
    del.addEventListener("click", async () => {
      try {
        const data = await jsonFetch(`/api/favorites/${encodeURIComponent(item.id)}`, { method: "DELETE" });
        renderFavorites(data.favorites);
      } catch (err) {
        alert(err.message);
      }
    });
    row.append(go, del);
    host.append(row);
  }
}

async function loadFavorites() {
  try {
    const data = await jsonFetch("/api/favorites");
    renderFavorites(data.favorites);
  } catch (_) {
    renderFavorites([]);
  }
}

let potaSpots = [];
let potaMeter = "20m";
let potaMode = "SSB";
let potaHideLogged = false;
let potaHideWorked = false;
try {
  potaHideLogged = localStorage.getItem("ftdx-hide-logged") === "1";
  potaHideWorked = localStorage.getItem("ftdx-hide-worked") === "1";
} catch (_) { /* ignore */ }
const POTA_METERS = ["160m", "80m", "40m", "30m", "20m", "17m", "15m", "12m", "10m", "6m", "2m"];
const POTA_MODES = ["SSB", "CW", "FT8", "FT4"];

function fillPotaChipRow(host, attr, values, setValue) {
  if (!host || host.dataset.ready) return;
  host.dataset.ready = "1";
  const make = (id, label) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.dataset[attr] = id;
    btn.textContent = label;
    btn.addEventListener("click", () => {
      setValue(id);
      paintPotaChips();
      renderPota();
    });
    host.append(btn);
  };
  make("", "All");
  for (const name of values) make(name, name);
}

function ensurePotaFilters() {
  fillPotaChipRow($("potaMeters"), "meter", POTA_METERS, (v) => { potaMeter = v; });
  fillPotaChipRow($("potaModes"), "mode", POTA_MODES, (v) => { potaMode = v; });
  paintPotaChips();
}

function paintPotaChips() {
  const meters = $("potaMeters");
  if (meters) {
    meters.querySelectorAll("button").forEach((btn) => {
      btn.classList.toggle("on", btn.dataset.meter === potaMeter);
    });
  }
  const modes = $("potaModes");
  if (modes) {
    modes.querySelectorAll("button").forEach((btn) => {
      btn.classList.toggle("on", btn.dataset.mode === potaMode);
    });
  }
}

function potaModeMatch(item) {
  if (!potaMode) return true;
  const m = (item.mode || "").toUpperCase();
  if (potaMode === "SSB") return ["SSB", "USB", "LSB", "PHONE", "PH"].includes(m);
  if (potaMode === "CW") return m === "CW" || m === "CW-R" || m === "CWR";
  return m === potaMode;
}

function potaAge(item) {
  const sec = Number(item.expire) || 0;
  if (sec >= 60) return `${Math.round(sec / 60)}m`;
  if (sec > 0) return `${sec}s`;
  return "";
}

function potaSpotKey(item) {
  return `${item.id || ""}|${item.activator || ""}|${item.hz || ""}|${item.reference || ""}`;
}

function potaSameSpotKey(item) {
  const ref = String(item?.reference || "").trim().toUpperCase();
  const khz = String(item?.khz || "").trim();
  return ref && khz ? `${ref}|${khz}` : "";
}

function clusterPotaRows(rows) {
  const buckets = new Map();
  const order = [];
  for (const item of rows) {
    const key = potaSameSpotKey(item) || `solo:${potaSpotKey(item)}`;
    if (!buckets.has(key)) {
      buckets.set(key, []);
      order.push(key);
    }
    buckets.get(key).push(item);
  }
  const out = [];
  for (const key of order) {
    const group = buckets.get(key);
    if (group.length > 1) {
      group.sort((a, b) => {
        const age = (Number(b.expire) || 0) - (Number(a.expire) || 0);
        if (age) return age;
        return String(a.activator || "").localeCompare(String(b.activator || ""), undefined, { sensitivity: "base" });
      });
    }
    out.push(...group);
  }
  return out;
}

function potaActivatorKey(item) {
  const call = String(item?.activator || "").trim().toUpperCase();
  const ref = String(item?.reference || "").trim().toUpperCase();
  return call ? `${call}|${ref}` : "";
}

function callMatchParts(call) {
  const head = String(call || "").trim().toUpperCase().split(/\s+/)[0] || "";
  const bits = [];
  for (const piece of head.split("/")) {
    const tok = piece.replace(/[^A-Z0-9]/g, "");
    if (tok && !bits.includes(tok)) bits.push(tok);
  }
  const meat = bits.filter((bit) => /[A-Z]+\d+[A-Z]+/.test(bit));
  if (meat.length) return meat;
  const longBits = bits.filter((bit) => bit.length >= 3);
  return longBits.length ? longBits : bits;
}

function qrzCallKey(call) {
  return callMatchParts(call)[0] || "";
}

function potaLogKey(item) {
  const call = qrzCallKey(item?.activator || item?.call || "");
  const ref = String(item?.reference || item?.sig_info || "").trim().toUpperCase();
  return call ? (ref ? `${call}|${ref}` : call) : "";
}

let loggedToday = new Set();
let workedCalls = new Set();
let workedDates = {};

function utcDayStamp() {
  return new Date().toISOString().slice(0, 10).replace(/-/g, "");
}

function formatWorkedDate(raw) {
  const digits = String(raw || "").replace(/\D/g, "");
  if (digits.length < 8) return "";
  return `${digits.slice(0, 4)}-${digits.slice(4, 6)}-${digits.slice(6, 8)}`;
}

function lastWorkedDate(call) {
  const parts = callMatchParts(call);
  let best = "";
  for (const part of parts) {
    const day = String(workedDates[part] || "").replace(/\D/g, "");
    if (day.length >= 8 && day > best) best = day;
  }
  if (!best) {
    for (const part of parts) {
      for (const [key, val] of Object.entries(workedDates)) {
        if (String(key).split("|")[0] !== part) continue;
        const day = String(val || "").replace(/\D/g, "");
        if (day.length >= 8 && day > best) best = day;
      }
    }
  }
  return formatWorkedDate(best);
}

function workedFlagTitle(call, logged) {
  if (logged) return "Logged today";
  const when = lastWorkedDate(call);
  return when ? `Last worked ${when}` : "Worked before";
}

function persistLoggedLocal() {
  try {
    localStorage.setItem("ftdx-logged-today", JSON.stringify({ date: utcDayStamp(), keys: [...loggedToday] }));
  } catch (_) { /* ignore */ }
}

function rememberLoggedCall(call, ref) {
  const park = String(ref || "").trim().toUpperCase();
  const parts = callMatchParts(call);
  for (const part of parts) {
    loggedToday.add(part);
    if (park) loggedToday.add(`${part}|${park}`);
  }
  persistLoggedLocal();
  applyWorked(parts, Object.fromEntries(parts.map((part) => [part, utcDayStamp()])));
  return parts[0] || "";
}

function applyLoggedToday(keys) {
  if (!Array.isArray(keys)) return;
  for (const key of keys) {
    const text = String(key || "").trim().toUpperCase();
    if (text) loggedToday.add(text);
  }
  persistLoggedLocal();
}

function isLoggedToday(item) {
  return isCallLoggedToday(item?.activator || item?.call || "");
}

function applyWorked(keys, dates) {
  if (Array.isArray(keys)) {
    for (const key of keys) {
      for (const part of callMatchParts(key)) workedCalls.add(part);
      const text = String(key || "").trim().toUpperCase().split("|")[0];
      if (text) workedCalls.add(text);
    }
  }
  if (dates && typeof dates === "object" && !Array.isArray(dates)) {
    for (const [key, val] of Object.entries(dates)) {
      const day = String(val || "").replace(/\D/g, "").slice(0, 8);
      if (day.length < 8) continue;
      for (const part of callMatchParts(key)) {
        const prev = String(workedDates[part] || "").replace(/\D/g, "");
        if (!prev || day > prev) workedDates[part] = day;
        workedCalls.add(part);
      }
    }
  }
  try { localStorage.setItem("ftdx-worked", JSON.stringify([...workedCalls])); } catch (_) { /* ignore */ }
  try { localStorage.setItem("ftdx-worked-dates", JSON.stringify(workedDates)); } catch (_) { /* ignore */ }
}

function isCallWorked(call) {
  const parts = callMatchParts(call);
  if (!parts.length) return false;
  for (const part of parts) {
    if (workedCalls.has(part)) return true;
    for (const item of workedCalls) {
      const base = String(item || "").split("|")[0];
      if (base === part) return true;
    }
  }
  return false;
}

function isCallLoggedToday(call) {
  const parts = callMatchParts(call);
  if (!parts.length) return false;
  for (const part of parts) {
    if (loggedToday.has(part)) return true;
    for (const item of loggedToday) {
      const base = String(item || "").split("|")[0];
      if (base === part) return true;
    }
  }
  return false;
}

try {
  const savedLog = JSON.parse(localStorage.getItem("ftdx-logged-today") || "null");
  if (savedLog && savedLog.date === utcDayStamp()) applyLoggedToday(savedLog.keys);
} catch (_) { /* ignore */ }
try {
  const savedWorked = JSON.parse(localStorage.getItem("ftdx-worked") || "null");
  if (Array.isArray(savedWorked)) applyWorked(savedWorked);
} catch (_) { /* ignore */ }
try {
  const savedDates = JSON.parse(localStorage.getItem("ftdx-worked-dates") || "null");
  if (savedDates && typeof savedDates === "object") applyWorked([], savedDates);
} catch (_) { /* ignore */ }

function rematchPotaKey(rows, key, match) {
  if (key && rows.some((item) => potaSpotKey(item) === key)) return key;
  if (!match) return key || "";
  const found = rows.find((item) => potaActivatorKey(item) === match);
  return found ? potaSpotKey(found) : key || "";
}

function potaSpotAgeSec(item) {
  let raw = String(item?.spot_time || "").trim();
  if (!raw) return null;
  if (!/[zZ]|[+-]\d{2}:?\d{2}$/.test(raw)) raw += "Z";
  const ms = Date.parse(raw);
  if (!Number.isFinite(ms)) return null;
  return (Date.now() - ms) / 1000;
}

function filteredPotaRows() {
  const q = ($("potaFilter")?.value || "").trim().toLowerCase();
  const maxAge = potaMaxAgeMin() * 60;
  const rows = potaSpots.filter((item) => {
    const age = potaSpotAgeSec(item);
    if (age == null || age > maxAge) return false;
    if (potaMeter && item.band !== potaMeter) return false;
    if (!potaModeMatch(item)) return false;
    if (potaHideLogged && isLoggedToday(item)) return false;
    if (potaHideWorked && !isLoggedToday(item) && isCallWorked(item.activator || item.call || "")) return false;
    if (!q) return true;
    const blob = [item.activator, qrzNameFor(item.activator), item.reference, item.park, item.location, item.mode, item.khz, item.band, item.comments].join(" ").toLowerCase();
    return blob.includes(q);
  });
  return clusterPotaRows(rows);
}

let qrzNames = {};

function qrzNameFor(call) {
  return qrzNames[qrzCallKey(call)] || "";
}

function operatorFirstName(name) {
  const text = String(name || "").trim();
  if (!text) return "";
  return text.split(/\s+/)[0].replace(/,+$/, "");
}

function paintQrzNames() {
  document.querySelectorAll(".pota-item").forEach((el) => {
    const call = el.dataset.call || el.querySelector(".call")?.textContent || "";
    const op = el.querySelector(".op");
    if (!op) return;
    const name = operatorFirstName(qrzNameFor(call));
    op.textContent = name;
    op.classList.toggle("has-name", Boolean(name));
    op.title = `QRZ profile · ${call}`.trim();
  });
}

let qrzCardCall = "";

function hideQrzCard() {
  const card = $("qrzCard");
  if (card) card.hidden = true;
  qrzCardCall = "";
}

function fillQrzCard(data) {
  const call = (data && data.call) || qrzCardCall;
  $("qrzCardCall").textContent = call || "";
  $("qrzCardName").textContent = (data && data.name) || qrzNameFor(call) || "";
  $("qrzCardQth").textContent = (data && data.qth) || "";
  const bits = [];
  if (data && data.grid) bits.push(data.grid);
  if (data && data.county) bits.push(data.county);
  if (data && data.country) bits.push(data.country);
  if (data && data.licclass) bits.push(data.licclass);
  $("qrzCardMeta").textContent = bits.join(" · ");
  $("qrzCardErr").textContent = (data && data.error) || "";
  const photo = $("qrzCardPhoto");
  if (data && data.image) {
    photo.src = data.image;
    photo.hidden = false;
  } else {
    photo.removeAttribute("src");
    photo.hidden = true;
  }
}

async function showQrzProfile(call) {
  const key = qrzCallKey(call);
  if (!key) return;
  qrzCardCall = key;
  const card = $("qrzCard");
  if (!card) return;
  card.hidden = false;
  fillQrzCard({ call: key, name: qrzNameFor(key) });
  try {
    const data = await jsonFetch(`/api/qrz/profile?call=${encodeURIComponent(key)}`);
    if (qrzCardCall !== key) return;
    if (data.name) qrzNames[key] = data.name;
    fillQrzCard(data);
    paintQrzNames();
  } catch (err) {
    if (qrzCardCall !== key) return;
    fillQrzCard({ call: key, name: qrzNameFor(key), error: err.message });
  }
}

async function lookupQrzNames(rows) {
  const missing = [];
  const seen = new Set();
  for (const item of rows || []) {
    const key = qrzCallKey(item.activator);
    if (!key || qrzNames[key] !== undefined || seen.has(key)) continue;
    seen.add(key);
    missing.push(key);
  }
  if (!missing.length) return;
  try {
    const data = await jsonFetch("/api/qrz/lookup", {
      method: "POST",
      body: JSON.stringify({ calls: missing }),
    });
    Object.assign(qrzNames, data.names || {});
    for (const key of missing) {
      if (qrzNames[key] === undefined && !data.pending) qrzNames[key] = "";
    }
    paintQrzNames();
    paintQrzStatus(data);
    if (data.pending) setTimeout(() => lookupQrzNames(rows), 700);
  } catch (_) { /* ignore */ }
}

function paintQrzStatus(data) {
  const el = $("qrzStatus");
  if (!el) return;
  if (data && data.error) {
    el.textContent = data.error;
    return;
  }
  if (data && data.configured) el.textContent = `QRZ · ${data.username}`;
  else el.textContent = "QRZ not set";
}

async function loadQrz() {
  try {
    const data = await jsonFetch("/api/qrz");
    if ($("qrzUser") && data.username) $("qrzUser").value = data.username;
    paintQrzStatus(data);
  } catch (_) { /* ignore */ }
}
let potaScanOn = false;
let potaScanTimer = 0;
let potaScanIndex = 0;
let potaScanKey = "";
let potaScanMatch = "";
let potaScanGen = 0;
let potaSelectedKey = "";
let potaSelectedMatch = "";
const POTA_AGE_KEY = "ftdx-pota-max-age";
let potaAgeTimer = 0;

function potaMaxAgeMin() {
  const el = $("potaMaxAge");
  const n = Number(el && el.value);
  if (!Number.isFinite(n)) return 8;
  return Math.max(1, Math.min(60, Math.round(n)));
}

function potaWindowText() {
  return `last ${potaMaxAgeMin()} min`;
}

function paintPotaMaxAge() {
  const val = $("potaMaxAgeVal");
  if (val) val.textContent = `${potaMaxAgeMin()} min`;
}

const POTA_SCAN_DWELL_MS = 2000;

function potaScanDelayMs() {
  return POTA_SCAN_DWELL_MS;
}

function paintPotaScanDelay() {
  const val = $("potaScanDelayVal");
  if (val) val.textContent = `${potaScanDelayMs()} ms`;
}

function paintLoggedFlags() {
  const host = $("potaList");
  if (!host) return;
  host.querySelectorAll(".pota-item").forEach((el) => {
    const call = el.dataset.call || "";
    const on = isCallLoggedToday(call);
    const worked = !on && isCallWorked(call);
    el.classList.toggle("logged", on);
    el.classList.toggle("worked", worked);
    const flag = el.querySelector(".logged-flag");
    if (flag) {
      flag.textContent = on ? "LOG" : (worked ? "WKD" : "");
      flag.title = on || worked ? workedFlagTitle(call, on) : "";
      flag.classList.toggle("on", on);
      flag.classList.toggle("worked", worked);
    }
  });
}

function paintPotaScan() {
  const btn = $("potaScan");
  if (btn) {
    btn.classList.toggle("on", potaScanOn);
    btn.textContent = potaScanOn ? "Stop" : "Scan";
  }
  const host = $("potaList");
  if (!host) return;
  host.querySelectorAll(".pota-item").forEach((el) => {
    el.classList.toggle("scanning", potaScanOn && el.dataset.spotKey === potaScanKey);
    el.classList.toggle("selected", el.dataset.spotKey === potaSelectedKey);
  });
  paintLoggedFlags();
}

function stopPotaScan() {
  potaScanOn = false;
  potaScanGen += 1;
  if (potaScanTimer) {
    clearTimeout(potaScanTimer);
    potaScanTimer = 0;
  }
  paintPotaScan();
}

async function tunePotaSpot(item, scanGen) {
  potaSelectedKey = potaSpotKey(item);
  potaSelectedMatch = potaActivatorKey(item);
  fillLogFromPota(item);
  paintPotaScan();
  if (!potaScanOn) showPotaPath(item).catch((err) => setRadarPathMeta(err.message || "Path failed"));
  const body = {};
  if (item.cat_mode) {
    body.mode = item.cat_mode;
    lockedMode = item.cat_mode;
    modeLockUntil = Date.now() + 1200;
  }
  if (item.hz) body.hz = item.hz;
  else if (item.mhz) body.mhz = item.mhz;
  if (scanGen != null && (scanGen !== potaScanGen || !potaScanOn)) return;
  await jsonFetch("/api/command", { method: "POST", body: JSON.stringify(body) });
  if (scanGen != null && (scanGen !== potaScanGen || !potaScanOn)) return;
  lastHz = item.hz || Math.round(Number(item.mhz) * 1_000_000);
  paintFreqDigits();
  render(await jsonFetch("/api/status?full=1"));
}

async function potaScanTick() {
  if (!potaScanOn) return;
  const gen = potaScanGen;
  const rows = filteredPotaRows();
  if (!rows.length) {
    stopPotaScan();
    return;
  }
  if (gen !== potaScanGen) return;
  if (potaScanIndex >= rows.length) potaScanIndex = 0;
  const item = rows[potaScanIndex];
  potaScanKey = potaSpotKey(item);
  potaScanMatch = potaActivatorKey(item);
  paintPotaScan();
  const btn = $("potaList")?.querySelector(".pota-item.scanning");
  btn?.scrollIntoView({ block: "nearest" });
  try {
    await tunePotaSpot(item, gen);
  } catch (_) {
    if (gen === potaScanGen) stopPotaScan();
    return;
  }
  if (!potaScanOn || gen !== potaScanGen) return;
  potaScanIndex += 1;
  potaScanTimer = setTimeout(potaScanTick, potaScanDelayMs());
}

function startPotaScan() {
  if (potaScanOn) return;
  const rows = filteredPotaRows();
  if (!rows.length) return;
  potaScanOn = true;
  potaScanIndex = 0;
  paintPotaScan();
  potaScanTick();
}

function renderPota() {
  ensurePotaFilters();
  const host = $("potaList");
  const meta = $("potaMeta");
  if (!host) return;
  const rows = filteredPotaRows();
  host.innerHTML = "";
  if (meta) {
    meta.textContent = potaSpots.length
      ? `${rows.length} of ${potaSpots.length} spots · ${potaWindowText()} · ${meta.dataset.at || "pota.app"} UTC`
      : (meta.dataset.err || `USA · ${potaWindowText()} · pota.app`);
  }
  if (!rows.length) {
    const empty = document.createElement("p");
    empty.className = "hint";
    empty.textContent = potaSpots.length ? "No spots match that filter." : `No USA activations in the ${potaWindowText()}.`;
    host.append(empty);
    if (potaScanOn) stopPotaScan();
    return;
  }
  for (let i = 0; i < rows.length; i += 1) {
    const item = rows[i];
    const same = potaSameSpotKey(item);
    const prev = i > 0 ? potaSameSpotKey(rows[i - 1]) : "";
    const next = i + 1 < rows.length ? potaSameSpotKey(rows[i + 1]) : "";
    const clustered = Boolean(same && (same === prev || same === next));
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "pota-item";
    if (clustered) {
      btn.classList.add("cluster");
      if (same !== prev) btn.classList.add("cluster-start");
      if (same !== next) btn.classList.add("cluster-end");
    }
    btn.dataset.spotKey = potaSpotKey(item);
    btn.dataset.call = item.activator || "";
    if (/\bqrt\b/i.test(item.comments || "")) btn.classList.add("qrt");
    const logged = isLoggedToday(item);
    const worked = !logged && isCallWorked(item.activator || item.call || "");
    if (logged) btn.classList.add("logged");
    if (worked) btn.classList.add("worked");
    btn.title = [item.park, item.location, item.comments].filter(Boolean).join(" · ");
    const flag = document.createElement("span");
    flag.className = logged ? "logged-flag on" : (worked ? "logged-flag worked" : "logged-flag");
    flag.textContent = logged ? "LOG" : (worked ? "WKD" : "");
    flag.title = logged || worked ? workedFlagTitle(item.activator || item.call || "", logged) : "";
    const call = document.createElement("span");
    call.className = "call";
    call.textContent = item.activator || "—";
    call.title = "QRZ profile";
    const op = document.createElement("span");
    op.className = "op";
    const opName = operatorFirstName(qrzNameFor(item.activator));
    op.textContent = opName;
    op.classList.toggle("has-name", Boolean(opName));
    op.title = `QRZ profile · ${item.activator || ""}`.trim();
    const freq = document.createElement("span");
    freq.className = "freq";
    freq.textContent = item.khz ? `${item.khz}` : "";
    const mode = document.createElement("span");
    mode.className = "mode";
    mode.textContent = item.mode || potaAge(item);
    const park = document.createElement("span");
    park.className = "park";
    park.textContent = [item.reference, item.park, item.location].filter(Boolean).join("  ");
    btn.append(flag, call, op, freq, mode, park);
    btn.addEventListener("click", async (ev) => {
      const hit = ev.target instanceof Element ? ev.target : ev.target && ev.target.parentElement;
      if (hit && hit.closest(".call, .op")) {
        ev.preventDefault();
        ev.stopPropagation();
        showQrzProfile(item.activator);
        return;
      }
      try {
        if (potaScanOn) stopPotaScan();
        await tunePotaSpot(item);
      } catch (err) {
        alert(err.message);
      }
    });
    host.append(btn);
  }
  potaSelectedKey = rematchPotaKey(rows, potaSelectedKey, potaSelectedMatch);
  potaScanKey = rematchPotaKey(rows, potaScanKey, potaScanMatch);
  paintPotaScan();
  lookupQrzNames(rows);
  fitPotaRowFont();
}

async function loadPota(force) {
  const meta = $("potaMeta");
  try {
    const query = new URLSearchParams();
    query.set("age", String(potaMaxAgeMin()));
    if (force) query.set("refresh", "1");
    const data = await jsonFetch(`/api/pota?${query}`);
    potaSpots = data.spots || [];
    if (meta) {
      meta.dataset.at = data.fetched_at || "";
      meta.dataset.err = data.error || "";
    }
    applyLoggedToday(data.logged_today);
    applyWorked(data.worked, data.worked_dates);
    renderPota();
    if (data.error && meta) meta.textContent = data.error;
  } catch (err) {
    potaSpots = [];
    if (meta) {
      meta.dataset.err = err.message;
      meta.textContent = err.message;
    }
    renderPota();
  }
}

async function loadPorts() {
  const data = await jsonFetch("/api/ports");
  fillPorts(data.ports || [], data.saved);
  $("baud").value = String(data.baud || 4800);
  $("stopbits").value = String(data.stopbits || 2);
  $("autoConnect").checked = !!data.auto_connect;
}

function fillAudioDevices(sel, devices, selected) {
  const current = selected == null ? "" : String(selected);
  sel.innerHTML = "";
  const def = document.createElement("option");
  def.value = "";
  def.textContent = "System default";
  sel.append(def);
  for (const dev of devices || []) {
    const opt = document.createElement("option");
    opt.value = String(dev.id);
    opt.textContent = dev.hostapi ? `${dev.name} (${dev.hostapi})` : dev.name;
    sel.append(opt);
  }
  sel.value = [...sel.options].some((o) => o.value === current) ? current : "";
}

async function loadAudio() {
  const data = await jsonFetch("/api/audio");
  fillAudioDevices($("rxIn"), data.inputs, data.input);
  fillAudioDevices($("rxOut"), data.outputs, data.output);
  $("rxMonitor").classList.toggle("on", !!data.monitor);
  $("rxMute").classList.toggle("on", !!data.mute);
  $("rxVol").value = String(Math.round((data.volume ?? 0.8) * 100));
}

async function setAudio(body) {
  const data = await jsonFetch("/api/audio", { method: "POST", body: JSON.stringify(body) });
  $("rxMonitor").classList.toggle("on", !!data.monitor);
  $("rxMute").classList.toggle("on", !!data.mute);
  $("rxBar").style.width = `${Math.max(0, Math.min(100, (data.level || 0) * 100))}%`;
  $("rxErr").textContent = data.error || "";
  if (data.error && body.monitor) alert(data.error);
}

$("qrzSave").addEventListener("click", async () => {
  try {
    const body = { username: $("qrzUser").value };
    const pwd = $("qrzPass").value;
    if (pwd) body.password = pwd;
    const data = await jsonFetch("/api/qrz", {
      method: "POST",
      body: JSON.stringify(body),
    });
    $("qrzPass").value = "";
    paintQrzStatus(data);
    qrzNames = {};
    lookupQrzNames(filteredPotaRows());
    if (data.error) alert(data.error);
  } catch (err) {
    alert(err.message);
  }
});

function paintLogbookStatus(data) {
  const el = $("logbookStatus");
  if (!el) return;
  const parts = [];
  if (data && data.my_call) parts.push(data.my_call);
  parts.push(data && data.qrz_key_set ? "QRZ log key set" : "QRZ log key missing");
  if (data && Number.isFinite(Number(data.worked_count))) parts.push(`${data.worked_count} worked`);
  if (data && data.hrd_db) parts.push(`HRD ${data.hrd_db}`);
  else parts.push(`HRD ${data && data.hrd_host ? data.hrd_host : "127.0.0.1"}:${data && data.hrd_port ? data.hrd_port : 7826}`);
  el.textContent = parts.join(" · ");
}

async function loadLogbook() {
  try {
    const data = await jsonFetch("/api/logbook");
    if ($("myCall") && data.my_call) $("myCall").value = data.my_call;
    if ($("hrdDb") && data.hrd_db) $("hrdDb").value = data.hrd_db;
    if ($("hrdPort") && data.hrd_port) $("hrdPort").value = String(data.hrd_port);
    paintLogbookStatus(data);
    applyLoggedToday(data.logged_today);
    applyWorked(data.worked, data.worked_dates);
    renderPota();
    loadHomeQth(true).catch(() => {});
  } catch (_) { /* ignore */ }
}

function selectedPotaItem() {
  return filteredPotaRows().find((item) => potaSpotKey(item) === potaSelectedKey) || null;
}

function fillLogFromPota(item) {
  if (!item || !$("logCall")) return;
  $("logCall").value = item.activator || "";
}

async function logCurrentQso() {
  const item = selectedPotaItem();
  const call = ($("logCall")?.value || item?.activator || "").trim();
  const status = $("logQsoStatus");
  rememberLoggedCall(call, item?.reference);
  paintLoggedFlags();
  if (call) {
    jsonFetch("/api/logged-today", {
      method: "POST",
      body: JSON.stringify({ call, reference: item?.reference || "" }),
    }).then((data) => {
      applyLoggedToday(data.logged_today);
    applyWorked(data.worked, data.worked_dates);
      paintLoggedFlags();
    }).catch(() => {});
  }
  if (status) status.textContent = isCallLoggedToday(call) ? "LOG on that POTA row · logging…" : "Logging…";
  try {
    const data = await jsonFetch("/api/logbook/qso", {
      method: "POST",
      body: JSON.stringify({
        call,
        rst_sent: $("logRstS")?.value || "59",
        rst_rcvd: $("logRstR")?.value || "59",
        hz: lastHz || item?.hz,
        mhz: lastHz ? (lastHz / 1_000_000).toFixed(6) : item?.mhz,
        mode: lastMode || item?.cat_mode || item?.mode,
        band: item?.band,
        name: qrzNameFor(call),
        grid: item?.grid || "",
        reference: item?.reference || "",
        park: item?.park || "",
        sig: item?.reference ? "POTA" : "",
        sig_info: item?.reference || "",
      }),
    });
    paintLogbookStatus(data);
    applyLoggedToday(data.logged_today);
    applyWorked(data.worked, data.worked_dates);
    rememberLoggedCall(data.qso?.call || call, item?.reference);
    paintLoggedFlags();
    renderPota();
    if (status) status.textContent = `${data.message || data.error || (data.ok ? "Logged" : "Log failed")} · orange LOG on the call` ;
    if (!data.ok) alert(data.message || data.error || "Log failed");
  } catch (err) {
    renderPota();
    if (status) status.textContent = `${err.message} · LOG still marked on that call`;
    alert(err.message);
  }
}

$("logbookSave").addEventListener("click", async () => {
  try {
    const body = {
      my_call: ($("myCall") && $("myCall").value) || "",
      hrd_db: ($("hrdDb") && $("hrdDb").value) || "",
      hrd_port: ($("hrdPort") && $("hrdPort").value) || "7826",
    };
    const key = (($("qrzLogKey") && $("qrzLogKey").value) || "").trim();
    if (key) body.qrz_log_key = key;
    const data = await jsonFetch("/api/logbook", {
      method: "POST",
      body: JSON.stringify(body),
    });
    if ($("qrzLogKey")) $("qrzLogKey").value = "";
    paintLogbookStatus(data);
    loadPota(true).catch(() => {});
    loadHomeQth(true).catch(() => {});
  } catch (err) {
    alert(err.message);
  }
});
function paintHideFilters() {
  const logged = $("potaHideLogged");
  if (logged) logged.classList.toggle("on", potaHideLogged);
  const worked = $("potaHideWorked");
  if (worked) worked.classList.toggle("on", potaHideWorked);
}

$("potaHideLogged").addEventListener("click", () => {
  potaHideLogged = !potaHideLogged;
  try { localStorage.setItem("ftdx-hide-logged", potaHideLogged ? "1" : "0"); } catch (_) { /* ignore */ }
  paintHideFilters();
  renderPota();
});
$("potaHideWorked")?.addEventListener("click", () => {
  potaHideWorked = !potaHideWorked;
  try { localStorage.setItem("ftdx-hide-worked", potaHideWorked ? "1" : "0"); } catch (_) { /* ignore */ }
  paintHideFilters();
  renderPota();
});
$("logQso").addEventListener("click", () => { logCurrentQso(); });

function setSetupOpen(on) {
  document.body.classList.toggle("setup-open", on);
  $("setupBtn").classList.toggle("on", on);
  $("setupBtn").setAttribute("aria-pressed", on ? "true" : "false");
  const setup = $("connectPanel");
  if (setup) setup.style.zIndex = on ? "80" : "";
  layoutPotaForWindow();
}
function setLogOpen(on) {
  document.body.classList.toggle("log-open", on);
  $("logBtn").classList.toggle("on", on);
  $("logBtn").setAttribute("aria-pressed", on ? "true" : "false");
  try { localStorage.setItem("ftdx-log-open", on ? "1" : "0"); } catch (_) { /* ignore */ }
  const log = $("logPanel");
  if (log) log.style.zIndex = on ? "80" : "";
}
let radarMap = null;
let radarBase = null;
let radarLabels = null;
let radarLayer = null;
let homeLatLng = null;
let potaPathLine = null;
let potaPathHalo = null;
let potaHomeMark = null;
let potaParkMark = null;
const USA_BOUNDS = [[24.4, -125.0], [49.5, -66.5]];
const RADAR_TILE = "https://mesonet.agron.iastate.edu/cache/tile.py/1.0.0/nexrad-n0q-900913/{z}/{x}/{y}.png";
const RADAR_MIN_ZOOM = 3;
const RADAR_MAX_ZOOM = 10;

function radarTileOpts(extra) {
  return {
    minZoom: RADAR_MIN_ZOOM,
    maxZoom: RADAR_MAX_ZOOM,
    keepBuffer: 0,
    updateWhenIdle: true,
    updateWhenZooming: false,
    updateInterval: 400,
    ...extra,
  };
}

function radarTileUrl() {
  return `${RADAR_TILE}?v=${Math.floor(Date.now() / 120000)}`;
}

function radarBaseLayer() {
  const url = themeMapLight()
    ? "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}"
    : "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}";
  return L.tileLayer(url, radarTileOpts({ attribution: "Tiles &copy; Esri" }));
}

function radarLabelLayer() {
  const url = themeMapLight()
    ? "https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places_Alternate/MapServer/tile/{z}/{y}/{x}"
    : "https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}";
  return L.tileLayer(url, radarTileOpts({ attribution: "Esri", pane: "radarLabels" }));
}

function syncRadarBasemap() {
  if (!radarMap || typeof L === "undefined") return;
  if (radarBase) radarMap.removeLayer(radarBase);
  if (radarLabels) radarMap.removeLayer(radarLabels);
  radarBase = radarBaseLayer().addTo(radarMap);
  radarLabels = radarLabelLayer().addTo(radarMap);
  if (radarLayer) radarLayer.bringToFront();
  if (potaPathHalo) potaPathHalo.bringToFront();
  if (potaPathLine) potaPathLine.bringToFront();
  if (radarLabels) radarLabels.bringToFront();
  stylePotaPath();
}

function resizeRadarMap() {
  if (!radarMap) return;
  radarMap.invalidateSize();
}

function refreshRadarLayer() {
  if (!radarMap || typeof L === "undefined") return;
  const url = radarTileUrl();
  if (radarLayer) {
    radarLayer.setUrl(url);
  } else {
    radarLayer = L.tileLayer(url, radarTileOpts({
      attribution: "NEXRAD via Iowa State IEM",
      opacity: 0.72,
    })).addTo(radarMap);
  }
  if (potaPathHalo) potaPathHalo.bringToFront();
  if (potaPathLine) potaPathLine.bringToFront();
  if (radarLabels) radarLabels.bringToFront();
}

function maidenheadToLatLng(grid) {
  const text = String(grid || "").toUpperCase().replace(/[^A-Z0-9]/g, "");
  if (text.length < 4) return null;
  const A = "A".charCodeAt(0);
  const fieldLon = text.charCodeAt(0) - A;
  const fieldLat = text.charCodeAt(1) - A;
  if (fieldLon < 0 || fieldLon > 17 || fieldLat < 0 || fieldLat > 17) return null;
  if (!/[0-9]/.test(text[2]) || !/[0-9]/.test(text[3])) return null;
  let lon = fieldLon * 20 - 180 + Number(text[2]) * 2;
  let lat = fieldLat * 10 - 90 + Number(text[3]);
  let width = 2;
  let height = 1;
  if (text.length >= 6) {
    const subLon = text.charCodeAt(4) - A;
    const subLat = text.charCodeAt(5) - A;
    if (subLon < 0 || subLon > 23 || subLat < 0 || subLat > 23) return null;
    lon += subLon * (2 / 24);
    lat += subLat * (1 / 24);
    width = 2 / 24;
    height = 1 / 24;
  }
  if (text.length >= 8) {
    if (!/[0-9]/.test(text[6]) || !/[0-9]/.test(text[7])) return null;
    lon += Number(text[6]) * (2 / 240);
    lat += Number(text[7]) * (1 / 240);
    width = 2 / 240;
    height = 1 / 240;
  }
  return [lat + height / 2, lon + width / 2];
}

function parseLatLng(lat, lon) {
  if (lat == null || lon == null || lat === "" || lon === "") return null;
  const a = Number(lat);
  const b = Number(lon);
  if (!Number.isFinite(a) || !Number.isFinite(b)) return null;
  if (a === 0 && b === 0) return null;
  if (a < -90 || a > 90 || b < -180 || b > 180) return null;
  return [a, b];
}

function setRadarPathMeta(text) {
  const el = $("radarPathMeta");
  if (el) el.textContent = text || "";
}

function pathColor() {
  return themeMapLight() ? "#c56a00" : "#6ec8ff";
}

function stylePotaPath() {
  if (potaPathHalo) potaPathHalo.setStyle({ color: "#111", weight: 8, opacity: 0.85 });
  if (potaPathLine) potaPathLine.setStyle({ color: "#ff9f1a", weight: 5, opacity: 1 });
}

async function loadHomeQth(force) {
  const typed = ($("myCall")?.value || "").trim();
  if (!force && homeLatLng) return homeLatLng;
  try {
    const url = typed
      ? `/api/home?call=${encodeURIComponent(typed)}`
      : "/api/home";
    const data = await jsonFetch(url);
    homeLatLng = parseLatLng(data.lat, data.lon) || maidenheadToLatLng(data.grid);
    if (homeLatLng) {
      try { localStorage.setItem("ftdx-home-latlng", JSON.stringify(homeLatLng)); } catch (_) { /* ignore */ }
    }
  } catch (_) {
    homeLatLng = null;
  }
  if (!homeLatLng) {
    try {
      const saved = JSON.parse(localStorage.getItem("ftdx-home-latlng") || "");
      homeLatLng = parseLatLng(saved && saved[0], saved && saved[1]);
    } catch (_) { /* ignore */ }
  }
  return homeLatLng;
}

async function parkLatLng(item) {
  const direct = parseLatLng(item?.lat, item?.lon);
  if (direct) return direct;
  const fromGrid = maidenheadToLatLng(item?.grid);
  if (fromGrid) return fromGrid;
  const ref = String(item?.reference || "").trim();
  if (!ref) return null;
  const data = await jsonFetch(`/api/pota/park?ref=${encodeURIComponent(ref)}`);
  return parseLatLng(data.lat, data.lon) || maidenheadToLatLng(data.grid);
}

function clearPotaPath() {
  if (potaPathHalo) {
    radarMap?.removeLayer(potaPathHalo);
    potaPathHalo = null;
  }
  if (potaPathLine) {
    radarMap?.removeLayer(potaPathLine);
    potaPathLine = null;
  }
  if (potaHomeMark) {
    radarMap?.removeLayer(potaHomeMark);
    potaHomeMark = null;
  }
  if (potaParkMark) {
    radarMap?.removeLayer(potaParkMark);
    potaParkMark = null;
  }
}

async function showPotaPath(item) {
  initRadarMap();
  if (!radarMap || typeof L === "undefined") {
    setRadarPathMeta("Radar map not ready");
    return;
  }
  const overlay = radarMap.getPane("overlayPane");
  if (overlay) overlay.style.zIndex = "700";
  const home = await loadHomeQth();
  let park;
  try {
    park = await parkLatLng(item);
  } catch (err) {
    setRadarPathMeta(err.message || "Park location unknown");
    return;
  }
  if (!park) {
    setRadarPathMeta("Park location unknown");
    return;
  }
  const color = "#ff9f1a";
  const label = [item.reference, item.park].filter(Boolean).join(" · ") || "POTA";
  clearPotaPath();
  const lineOpts = { weight: 5, opacity: 1, pane: "overlayPane" };
  if (home) {
    potaPathHalo = L.polyline([home, park], { ...lineOpts, color: "#111", weight: 8, opacity: 0.85 }).addTo(radarMap);
    potaPathLine = L.polyline([home, park], { ...lineOpts, color }).addTo(radarMap);
    potaHomeMark = L.circleMarker(home, {
      radius: 7,
      color: "#111",
      fillColor: "#3ecf7a",
      fillOpacity: 1,
      weight: 2,
    }).addTo(radarMap).bindTooltip("Home", { permanent: false });
  } else {
    potaPathLine = null;
  }
  potaParkMark = L.circleMarker(park, {
    radius: 7,
    color: "#111",
    fillColor: color,
    fillOpacity: 1,
    weight: 2,
  }).addTo(radarMap).bindTooltip(label, { permanent: false });
  resizeRadarMap();
  if (home) {
    const bounds = L.latLngBounds([home, park]).pad(0.2);
    radarMap.fitBounds(bounds, { maxZoom: 7, padding: [28, 28], animate: false });
    setRadarPathMeta(`${item.activator || "POTA"} · ${label}`);
  } else {
    radarMap.setView(park, Math.min(6, radarMap.getZoom() || 5));
    setRadarPathMeta("No home QTH (set My callsign + QRZ)");
  }
}

function pauseRadarTiles() {
  try { radarMap?.stop(); } catch (_) { /* ignore */ }
}

function resumeRadarTiles() {
  try { radarMap?.start(); } catch (_) { /* ignore */ }
}

function initRadarMap() {
  if (radarMap || typeof L === "undefined") return;
  const host = $("radarMap");
  if (!host) return;
  try {
    radarMap = L.map(host, {
      zoomControl: true,
      attributionControl: true,
      worldCopyJump: false,
      fadeAnimation: false,
      zoomAnimation: false,
      markerZoomAnimation: false,
      inertia: false,
      minZoom: RADAR_MIN_ZOOM,
      maxZoom: RADAR_MAX_ZOOM,
      maxBounds: L.latLngBounds(USA_BOUNDS).pad(0.15),
      maxBoundsViscosity: 0.8,
    });
    radarMap.createPane("radarLabels");
    radarMap.getPane("radarLabels").style.zIndex = "650";
    radarMap.getPane("radarLabels").style.pointerEvents = "none";
    radarMap.getPane("overlayPane").style.zIndex = "700";
    radarMap.setView([39.5, -98.35], 4);
    syncRadarBasemap();
    refreshRadarLayer();
    requestAnimationFrame(resizeRadarMap);
    $("radarRefresh")?.addEventListener("click", () => {
      refreshRadarLayer();
      resizeRadarMap();
    });
    setInterval(() => {
      if (document.visibilityState === "visible") refreshRadarLayer();
    }, 120000);
  } catch (_) {
    radarMap = null;
    radarBase = null;
    radarLabels = null;
    radarLayer = null;
    potaPathLine = null;
    potaPathHalo = null;
    potaHomeMark = null;
    potaParkMark = null;
  }
}

function scheduleRadarMap() {
  const start = () => {
    initRadarMap();
    resizeRadarMap();
  };
  const delay = () => setTimeout(start, 1500);
  if (document.readyState === "complete") delay();
  else window.addEventListener("load", delay, { once: true });
}

const THEMES = [
  { id: "dark", label: "Dark", mapLight: false },
  { id: "light", label: "Light", mapLight: true },
  { id: "night", label: "Night", mapLight: false },
  { id: "ocean", label: "Ocean", mapLight: false },
  { id: "high", label: "High", mapLight: false },
  { id: "rigone", label: "DX1200", mapLight: false },
];
const THEME_IDS = THEMES.map((t) => t.id);

function themeInfo(name) {
  return THEMES.find((t) => t.id === name) || THEMES[0];
}

function currentThemeId() {
  const cls = THEME_IDS.find((id) => id !== "dark" && document.body.classList.contains(`theme-${id}`));
  return cls || "dark";
}

function themeMapLight() {
  return !!themeInfo(currentThemeId()).mapLight;
}

function setTheme(name) {
  const theme = themeInfo(name);
  THEME_IDS.forEach((id) => {
    if (id !== "dark") document.body.classList.remove(`theme-${id}`);
  });
  if (theme.id !== "dark") document.body.classList.add(`theme-${theme.id}`);
  const btn = $("themeBtn");
  if (btn) {
    btn.textContent = theme.label;
    btn.classList.toggle("on", theme.id !== "dark");
    btn.setAttribute("aria-pressed", theme.id !== "dark" ? "true" : "false");
    btn.title = "Cycle themes: Dark, Light, Night, Ocean, High, DX1200";
  }
  try { localStorage.setItem("ftdx-theme", theme.id); } catch (_) { /* ignore */ }
  drawSAnalog();
  syncRadarBasemap();
}

function nextThemeId() {
  const i = THEME_IDS.indexOf(currentThemeId());
  return THEME_IDS[(i + 1) % THEME_IDS.length];
}

const LAYOUTS_KEY = "ftdx-named-layouts";
const COLOR_KEY = "ftdx-colors";
const COLOR_FIELDS = [
  ["colorBg", "bg"],
  ["colorPanel", "panel"],
  ["colorMeter", "meter"],
  ["colorAccent", "accent"],
  ["colorInk", "ink"],
];

function cloneLayout(pos) {
  try { return JSON.parse(JSON.stringify(pos || {})); }
  catch (_) { return {}; }
}

function readNamedLayouts() {
  try {
    const raw = JSON.parse(localStorage.getItem(LAYOUTS_KEY) || "{}");
    return raw && typeof raw === "object" ? raw : {};
  } catch (_) { return {}; }
}

function writeNamedLayouts(all) {
  try { localStorage.setItem(LAYOUTS_KEY, JSON.stringify(all)); } catch (_) { /* ignore */ }
  jsonFetch("/api/layout", {
    method: "POST",
    body: JSON.stringify({ named_layouts: all }),
  }).catch(() => {});
}

function layoutPanelEls() {
  return [...document.querySelectorAll("#desk .panel")];
}

function fillLayoutSelect() {
  const sel = $("layoutPick");
  if (!sel) return;
  const names = Object.keys(readNamedLayouts()).sort((a, b) => a.localeCompare(b));
  const current = sel.value;
  sel.replaceChildren();
  if (!names.length) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = "No saved layouts";
    sel.append(opt);
    return;
  }
  for (const name of names) {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    sel.append(opt);
  }
  if (names.includes(current)) sel.value = current;
}

function applyNamedLayout(name) {
  const pos = readNamedLayouts()[name];
  if (!pos) return;
  const copy = migrateClockPanel(cloneLayout(pos));
  writePanelPos(copy);
  if (scaleModel && scaleModel.on) {
    scaleModel.window = windowBox();
    scaleModel.base = floatedPanelBoxes(copy);
    copy.__scale = scaleModel;
  }
  persistPanelLayout(copy);
  layoutPanelEls().forEach((el) => applySavedPanel(el, copy[el.id]));
  alignTitleToTuner();
  growDesk();
  layoutPotaForWindow();
  resizeRadarMap();
  fitUtcClock();
  drawSAnalog();
}

function mixHex(a, b, t) {
  const parse = (hex) => {
    const n = parseInt(String(hex).replace("#", ""), 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  };
  const [ar, ag, ab] = parse(a);
  const [br, bg, bb] = parse(b);
  const ch = (x, y) => Math.round(x + (y - x) * t).toString(16).padStart(2, "0");
  return `#${ch(ar, br)}${ch(ag, bg)}${ch(ab, bb)}`;
}

function readColors() {
  try {
    const raw = JSON.parse(localStorage.getItem(COLOR_KEY) || "null");
    if (!raw || typeof raw !== "object") return null;
    const out = {};
    for (const [, key] of COLOR_FIELDS) {
      if (/^#[0-9a-fA-F]{6}$/.test(raw[key] || "")) out[key] = raw[key].toLowerCase();
    }
    return Object.keys(out).length ? out : null;
  } catch (_) { return null; }
}

function applyColors(colors) {
  const body = document.body;
  ["--bg", "--wash", "--panel", "--meter-face", "--accent", "--live", "--ink", "--heading", "--input", "--btn", "--line", "--muted"].forEach((name) => {
    body.style.removeProperty(name);
  });
  if (colors) {
    if (colors.bg) {
      body.style.setProperty("--bg", colors.bg);
      body.style.setProperty("--wash", mixHex(colors.bg, "#ffffff", 0.14));
      body.style.setProperty("--input", mixHex(colors.bg, "#000000", 0.28));
    }
    if (colors.panel) {
      body.style.setProperty("--panel", colors.panel);
      body.style.setProperty("--btn", mixHex(colors.panel, "#000000", 0.18));
    }
    if (colors.meter) body.style.setProperty("--meter-face", colors.meter);
    if (colors.accent) {
      body.style.setProperty("--accent", colors.accent);
      body.style.setProperty("--live", colors.accent);
    }
    if (colors.ink) {
      body.style.setProperty("--ink", colors.ink);
      body.style.setProperty("--heading", colors.ink);
      body.style.setProperty("--muted", mixHex(colors.ink, colors.bg || "#888888", 0.45));
    }
    if (colors.panel && colors.ink) {
      body.style.setProperty("--line", mixHex(colors.panel, colors.ink, 0.28));
    }
  }
  drawSAnalog();
  window.dispatchEvent(new Event("ftdx-colors"));
}

function writeColors(colors) {
  if (colors) localStorage.setItem(COLOR_KEY, JSON.stringify(colors));
  else localStorage.removeItem(COLOR_KEY);
  jsonFetch("/api/layout", {
    method: "POST",
    body: JSON.stringify({ colors: colors || {} }),
  }).catch(() => {});
  applyColors(colors);
}

function currentVarHex(name, fallback) {
  const raw = getComputedStyle(document.body).getPropertyValue(name).trim();
  return /^#[0-9a-fA-F]{6}$/.test(raw) ? raw : fallback;
}

function syncColorInputs() {
  const saved = readColors() || {};
  const fallbacks = { bg: "#0c100b", panel: "#141c12", meter: "#10160e", accent: "#8fbf78", ink: "#e7eee0" };
  const vars = { bg: "--bg", panel: "--panel", meter: "--meter-face", accent: "--accent", ink: "--ink" };
  for (const [id, key] of COLOR_FIELDS) {
    const input = $(id);
    if (input) input.value = saved[key] || currentVarHex(vars[key], fallbacks[key]);
  }
}

function colorsFromInputs() {
  const out = {};
  for (const [id, key] of COLOR_FIELDS) {
    const value = $(id)?.value || "";
    if (/^#[0-9a-fA-F]{6}$/.test(value)) out[key] = value.toLowerCase();
  }
  return out;
}

function setLookOpen(open) {
  const pop = $("lookPop");
  const btn = $("lookBtn");
  if (!pop || !btn) return;
  pop.hidden = !open;
  btn.classList.toggle("on", open);
  btn.setAttribute("aria-expanded", open ? "true" : "false");
  if (open) syncColorInputs();
}

function wireLook() {
  fillLayoutSelect();
  applyColors(readColors());
  $("lookBtn")?.addEventListener("click", (ev) => {
    ev.stopPropagation();
    setLookOpen($("lookPop")?.hidden !== false);
  });
  $("lookPop")?.addEventListener("click", (ev) => ev.stopPropagation());
  document.addEventListener("click", () => setLookOpen(false));
  $("layoutSave")?.addEventListener("click", () => {
    const name = ($("layoutName")?.value || "").trim().slice(0, 24);
    if (!name) return;
    const all = readNamedLayouts();
    if (!all[name] && Object.keys(all).length >= 12) return;
    all[name] = cloneLayout(readPanelPos());
    writeNamedLayouts(all);
    $("layoutName").value = "";
    fillLayoutSelect();
    $("layoutPick").value = name;
  });
  $("layoutUse")?.addEventListener("click", () => applyNamedLayout($("layoutPick")?.value || ""));
  $("scaleToggle")?.addEventListener("click", () => setProportionalScale(!(scaleModel && scaleModel.on)));
  $("layoutDelete")?.addEventListener("click", () => {
    const name = $("layoutPick")?.value || "";
    if (!name) return;
    const all = readNamedLayouts();
    delete all[name];
    writeNamedLayouts(all);
    fillLayoutSelect();
  });
  for (const [id] of COLOR_FIELDS) {
    $(id)?.addEventListener("input", () => writeColors(colorsFromInputs()));
  }
  $("colorReset")?.addEventListener("click", () => {
    writeColors(null);
    syncColorInputs();
  });
}

function mergeLookFromRemote(data) {
  if (!data || typeof data !== "object") return;
  const local = readNamedLayouts();
  const remote = data.named_layouts;
  if (!Object.keys(local).length && remote && typeof remote === "object" && Object.keys(remote).length) {
    try { localStorage.setItem(LAYOUTS_KEY, JSON.stringify(remote)); } catch (_) { /* ignore */ }
    fillLayoutSelect();
  }
  if (!readColors() && data.colors && typeof data.colors === "object" && Object.keys(data.colors).length) {
    try { localStorage.setItem(COLOR_KEY, JSON.stringify(data.colors)); } catch (_) { /* ignore */ }
    applyColors(readColors());
    syncColorInputs();
  }
}

setSetupOpen(false);
setLogOpen(localStorage.getItem("ftdx-log-open") === "1");
setTheme(localStorage.getItem("ftdx-theme") || "dark");
wireLook();
$("setupBtn").addEventListener("click", () => {
  setSetupOpen(!document.body.classList.contains("setup-open"));
});
$("themeBtn").addEventListener("click", () => {
  setTheme(nextThemeId());
});
const METER_BAR_KEY = "ftdx-meter-bar";

function fitVfoMeterHeight() {
  const panel = $("vfoPanel");
  if (!panel || panel.classList.contains("resized-h")) {
    drawSAnalog();
    return;
  }
  if (!(panel.classList.contains("panel-float"))) return;
  const prev = panel.style.height;
  panel.style.height = "auto";
  const next = panel.offsetHeight;
  panel.style.height = prev;
  if (!(next > 96) || Math.abs(next - panel.offsetHeight) <= 2) {
    drawSAnalog();
    return;
  }
  const delta = next - panel.offsetHeight;
  sizePanel(panel, null, next);
  savePanelLayout(panel);
  growDesk();
  requestAnimationFrame(() => drawSAnalog());
}

function shiftFloatersBelow(anchor, delta) {
  if (!anchor || !delta) return;
  const left = parseFloat(anchor.style.left) || 0;
  const right = left + anchor.offsetWidth;
  const oldBottom = (parseFloat(anchor.style.top) || 0) + anchor.offsetHeight - delta;
  document.querySelectorAll("#desk .panel.panel-float").forEach((el) => {
    if (el === anchor || isClockPanel(el)) return;
    const elLeft = parseFloat(el.style.left) || 0;
    const elRight = elLeft + el.offsetWidth;
    const overlap = Math.min(right, elRight) - Math.max(left, elLeft);
    if (overlap < Math.min(anchor.offsetWidth, el.offsetWidth) * 0.45) return;
    const top = parseFloat(el.style.top) || 0;
    if (top + 2 < oldBottom) return;
    el.style.top = `${Math.round(top + delta)}px`;
    savePanelLayout(el);
  });
}

function setMeterBar(on, resize) {
  const panel = $("vfoPanel");
  const was = !!panel?.classList.contains("vfo-meter-bar");
  if (panel) panel.classList.toggle("vfo-meter-bar", !!on);
  const btn = $("meterStyle");
  if (btn) {
    btn.textContent = on ? "Scale" : "Bar";
    btn.title = on ? "Show the analog S-meter" : "Show the S-meter as a bar graph";
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    btn.classList.toggle("on", !!on);
  }
  try { localStorage.setItem(METER_BAR_KEY, on ? "1" : "0"); } catch (_) { /* ignore */ }
  if (resize && was !== !!on) fitVfoMeterHeight();
  else drawSAnalog();
}
$("meterStyle")?.addEventListener("click", (ev) => {
  ev.stopPropagation();
  setMeterBar(!$("vfoPanel").classList.contains("vfo-meter-bar"), true);
});
setMeterBar(localStorage.getItem(METER_BAR_KEY) === "1", false);
$("logBtn").addEventListener("click", () => {
  setLogOpen(!document.body.classList.contains("log-open"));
});
$("qrzCardClose")?.addEventListener("click", hideQrzCard);
$("qrzCardOpen")?.addEventListener("click", async () => {
  const call = qrzCardCall;
  if (!call) return;
  try {
    await jsonFetch("/api/qrz/open", { method: "POST", body: JSON.stringify({ call }) });
  } catch (err) {
    $("qrzCardErr").textContent = err.message;
  }
});
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape" && $("qrzCard") && !$("qrzCard").hidden) {
    hideQrzCard();
    return;
  }
  if (ev.key === "Escape") {
    selectPanel(null);
    return;
  }
  const dir = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[ev.key];
  if (!dir || !focusedPanel) return;
  const t = ev.target;
  if (t instanceof Element && (t.closest("input, textarea, select") || t.id === "dial" || t.isContentEditable)) return;
  ev.preventDefault();
  const step = ev.shiftKey ? 8 : (ev.ctrlKey || ev.altKey ? 1 : 2);
  nudgeFocusedPanel(dir[0] * step, dir[1] * step);
}, true);
const PANEL_LOCK_KEY = "ftdx-panels-locked";
function panelsLocked() {
  return document.body.classList.contains("panels-locked");
}
function setPanelsLocked(on) {
  document.body.classList.toggle("panels-locked", !!on);
  const btn = $("panelLock");
  if (btn) {
    btn.textContent = on ? "Unlock" : "Lock";
    btn.title = on ? "Unlock panels so they can be moved or resized" : "Lock panels so they cannot be moved or resized";
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    btn.classList.toggle("on", !!on);
  }
  try { localStorage.setItem(PANEL_LOCK_KEY, on ? "1" : "0"); } catch (_) { /* ignore */ }
}
setPanelsLocked(localStorage.getItem(PANEL_LOCK_KEY) === "1");
$("panelLock")?.addEventListener("click", () => setPanelsLocked(!panelsLocked()));
document.addEventListener("click", (ev) => {
  if (!panelsLocked()) return;
  const t = ev.target;
  if (!(t instanceof Element) || t.closest("#panelLock")) return;
  if (t.closest("#modeBandHide, #modeBandShow, #favHide, #favShow, #tunerHide, #tunerShow, #voacapHide, #voacapShow, #issHide, #issShow")) {
    ev.preventDefault();
    ev.stopPropagation();
  }
}, true);
function setModeBandCollapsed(on) {
  document.body.classList.toggle("mode-band-collapsed", on);
  try { localStorage.setItem("ftdx-mode-band-collapsed", on ? "1" : "0"); } catch (_) { /* ignore */ }
  growDesk();
}
setModeBandCollapsed(localStorage.getItem("ftdx-mode-band-collapsed") === "1");
$("modeBandHide").addEventListener("click", () => {
  setModeBandCollapsed(true);
});
$("modeBandShow").addEventListener("click", () => {
  setModeBandCollapsed(false);
  requestAnimationFrame(() => {
    ensureModeBandVisible();
    keepModeBandAbovePota();
  });
});
function setFavCollapsed(on) {
  document.body.classList.toggle("fav-collapsed", on);
  try { localStorage.setItem("ftdx-fav-collapsed", on ? "1" : "0"); } catch (_) { /* ignore */ }
  growDesk();
}
setFavCollapsed(localStorage.getItem("ftdx-fav-collapsed") === "1");
function setTunerCollapsed(on) {
  document.body.classList.toggle("tuner-collapsed", on);
  try { localStorage.setItem("ftdx-tuner-collapsed", on ? "1" : "0"); } catch (_) { /* ignore */ }
  growDesk();
}
setTunerCollapsed(localStorage.getItem("ftdx-tuner-collapsed") === "1");
$("tunerHide")?.addEventListener("click", () => setTunerCollapsed(true));
$("tunerShow")?.addEventListener("click", () => {
  setTunerCollapsed(false);
  requestAnimationFrame(() => parkTunerIfNew());
});
function setVoacapCollapsed(on) {
  document.body.classList.toggle("voacap-collapsed", on);
  try { localStorage.setItem("ftdx-voacap-collapsed", on ? "1" : "0"); } catch (_) { /* ignore */ }
  growDesk();
}
setVoacapCollapsed(localStorage.getItem("ftdx-voacap-collapsed") === "1");
$("voacapHide")?.addEventListener("click", () => setVoacapCollapsed(true));
$("voacapShow")?.addEventListener("click", () => {
  setVoacapCollapsed(false);
  requestAnimationFrame(() => parkVoacapIfNew());
});
function setIssCollapsed(on) {
  document.body.classList.toggle("iss-collapsed", !!on);
  try { localStorage.setItem("ftdx-iss-collapsed", on ? "1" : "0"); } catch (_) { /* ignore */ }
  if (!on && typeof window.loadIss === "function") window.loadIss();
}
setIssCollapsed(localStorage.getItem("ftdx-iss-collapsed") === "1");
$("issHide")?.addEventListener("click", () => setIssCollapsed(true));
$("issShow")?.addEventListener("click", () => {
  setIssCollapsed(false);
  requestAnimationFrame(() => {
    parkIssIfNew();
    keepIssOnScreen();
  });
});
function setPanCollapsed(on) {
  document.body.classList.toggle("pan-collapsed", !!on);
  try { localStorage.setItem("ftdx-pan-collapsed", on ? "1" : "0"); } catch (_) { /* ignore */ }
  if (!on) requestAnimationFrame(() => paintPanadapter());
  growDesk();
}
setPanCollapsed(localStorage.getItem("ftdx-pan-collapsed") === "1");
function hidePanadapter(ev) {
  ev.preventDefault();
  ev.stopPropagation();
  setPanCollapsed(true);
}
$("panHide")?.addEventListener("pointerdown", hidePanadapter);
$("panHide")?.addEventListener("click", hidePanadapter);
$("panShow")?.addEventListener("click", () => {
  setPanCollapsed(false);
  requestAnimationFrame(() => parkPanadapterIfNew());
});
let panFrame = null;
let panFall = null;
let panFallCenter = 0;
let panFloor = null;
const PAN_ZOOMS = [1, 2, 4, 8];
let panZoom = 1;
let panHoverFrac = null;
try {
  const savedZoom = Number(localStorage.getItem("ftdx-pan-zoom"));
  if (PAN_ZOOMS.includes(savedZoom)) panZoom = savedZoom;
} catch (_) { /* keep the full span */ }
function setPanZoom(next) {
  if (!PAN_ZOOMS.includes(next)) return;
  panZoom = next;
  try { localStorage.setItem("ftdx-pan-zoom", String(next)); } catch (_) { /* ignore */ }
  const zoomOut = $("panZoomOut");
  const zoomIn = $("panZoomIn");
  if (zoomOut) zoomOut.disabled = next === PAN_ZOOMS[0];
  if (zoomIn) zoomIn.disabled = next === PAN_ZOOMS[PAN_ZOOMS.length - 1];
  try { paintPanadapter(); } catch (_) { /* the next sweep redraws */ }
}
function panHzAt(frac) {
  const frame = panFrame && panFrame.ok && Array.isArray(panFrame.bins) ? panFrame : null;
  if (!frame) return null;
  const center = Number(frame.center) || Number(lastHz) || 0;
  const span = Number(frame.span) || 100_000;
  const zoom = PAN_ZOOMS.includes(panZoom) ? panZoom : 1;
  if (!(frac >= 0 && frac <= 1) || !(span > 0)) return null;
  const hz = Math.round((center + (frac - 0.5) * (span / zoom)) / 1000) * 1000;
  return Number.isFinite(hz) ? hz : null;
}
function panRamp(t) {
  const ramp = [
    [8, 14, 10],
    [12, 58, 42],
    [36, 168, 78],
    [214, 214, 64],
    [255, 248, 224],
  ];
  const x = Math.max(0, Math.min(0.999, t)) * (ramp.length - 1);
  const i = Math.floor(x);
  const f = x - i;
  const a = ramp[i];
  const b = ramp[i + 1];
  return [
    Math.round(a[0] + (b[0] - a[0]) * f),
    Math.round(a[1] + (b[1] - a[1]) * f),
    Math.round(a[2] + (b[2] - a[2]) * f),
  ];
}
let panLatest = null;
let panSeq = 0;
let panDrawnSeq = 0;
let panFallZoom = 1;
function pushPanWaterfall(bins, center) {
  panSeq += 1;
  panLatest = { bins, center, seq: panSeq };
}
function scrollPanFall(width, height, bins, center, zoom) {
  const w = Math.max(1, Math.round(width));
  const h = Math.max(1, Math.round(height));
  const reset = !panFall || panFall.width !== w || panFall.height !== h
    || panFallZoom !== zoom
    || Math.abs(center - panFallCenter) > 50_000;
  if (reset) {
    panFall = document.createElement("canvas");
    panFall.width = w;
    panFall.height = h;
    panFloor = null;
    panFallZoom = zoom;
  }
  panFallCenter = center;
  const sorted = bins.map((v) => Number(v)).sort((a, b) => a - b);
  const sample = sorted[Math.floor(sorted.length * 0.3)] || 0;
  panFloor = panFloor == null ? sample : panFloor * 0.85 + sample * 0.15;
  const range = 18;
  const ctx = panFall.getContext("2d", { willReadFrequently: true });
  const img = ctx.getImageData(0, 0, w, h);
  img.data.copyWithin(w * 4, 0);
  const n = bins.length;
  const view = n / zoom;
  const origin = (n - view) / 2;
  for (let x = 0; x < w; x += 1) {
    const i = Math.max(0, Math.min(n - 1, Math.floor(origin + ((x + 0.5) / w) * view)));
    const [r, g, b] = panRamp((Number(bins[i]) - panFloor) / range);
    const o = x * 4;
    img.data[o] = r;
    img.data[o + 1] = g;
    img.data[o + 2] = b;
    img.data[o + 3] = 255;
  }
  ctx.putImageData(img, 0, 0);
}
function paintPanadapter() {
  const canvas = $("panScope");
  const panel = $("panadapterPanel");
  if (!canvas || !panel || document.body.classList.contains("pan-collapsed")) return;
  const width = panel.clientWidth - 16;
  const height = Math.max(48, canvas.clientHeight || panel.clientHeight - 40);
  if (width < 40) return;
  const dpr = window.devicePixelRatio || 1;
  const pxW = Math.round(width * dpr);
  const pxH = Math.round(height * dpr);
  if (canvas.width !== pxW || canvas.height !== pxH) {
    canvas.width = pxW;
    canvas.height = pxH;
  }
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const style = getComputedStyle(document.body);
  const bg = style.getPropertyValue("--input").trim() || "#141c12";
  const line = style.getPropertyValue("--line").trim() || "#31402c";
  const ink = style.getPropertyValue("--muted").trim() || "#8aa584";
  const accent = style.getPropertyValue("--live").trim() || style.getPropertyValue("--accent").trim() || "#7cff6b";
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, width, height);
  const frame = panFrame && panFrame.ok && Array.isArray(panFrame.bins) && panFrame.bins.length > 1
    ? panFrame
    : null;
  const center = frame ? Number(frame.center) || Number(lastHz) : Number(lastHz) || 14_250_000;
  const span = frame ? Number(frame.span) || 100_000 : 100_000;
  const zoom = PAN_ZOOMS.includes(panZoom) ? panZoom : 1;
  const viewSpan = span / zoom;
  const half = viewSpan / 2;
  const plotBottom = height - 16;
  ctx.font = "11px Segoe UI, sans-serif";
  ctx.textAlign = "center";
  if (panLatest && panLatest.seq !== panDrawnSeq && frame && Array.isArray(frame.bins)) {
    scrollPanFall(width, plotBottom, frame.bins, center, zoom);
    panDrawnSeq = panLatest.seq;
  }
  if (panFall && panFall.width > 1) {
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(panFall, 0, 0, width, plotBottom);
  }
  ctx.strokeStyle = line;
  ctx.lineWidth = 1;
  const markStep = [1e6, 5e5, 2.5e5, 1e5, 5e4, 2.5e4, 1e4, 5e3].find((step) => {
    const count = Math.floor((half * 0.72) / step);
    return count >= 1 && count <= 3;
  }) || 5e3;
  const marks = [];
  const markCount = Math.min(4, Math.floor((half * 0.72) / markStep));
  for (let n = 1; n <= markCount; n += 1) {
    marks.push(-n * markStep, n * markStep);
  }
  marks.forEach((hz) => {
    const x = Math.round(((hz + half) / viewSpan) * width) + 0.5;
    ctx.globalAlpha = 0.35;
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, plotBottom);
    ctx.stroke();
    ctx.globalAlpha = 1;
  });
  const mid = Math.round(width / 2) + 0.5;
  ctx.strokeStyle = accent;
  ctx.beginPath();
  ctx.moveTo(mid, 0);
  ctx.lineTo(mid, plotBottom);
  ctx.stroke();
  ctx.fillStyle = bg;
  ctx.fillRect(0, plotBottom, width, height - plotBottom);
  ctx.fillStyle = ink;
  marks.forEach((hz) => {
    const x = Math.round(((hz + half) / viewSpan) * width);
    const abs = Math.abs(hz);
    const text = abs >= 1e6
      ? `${hz > 0 ? "+" : ""}${(hz / 1e6).toFixed(1)}`
      : `${hz > 0 ? "+" : ""}${Math.round(hz / 1000)}k`;
    ctx.fillText(text, x, height - 3);
  });
  const edgeBody = half >= 1e6 ? `${(half / 1e6).toFixed(1)}` : `${Math.round(half / 1000)}k`;
  ctx.textAlign = "left";
  ctx.fillText(`-${edgeBody}`, 4, height - 3);
  ctx.textAlign = "right";
  ctx.fillText(`+${edgeBody}`, width - 4, height - 3);
  ctx.textAlign = "center";
  const label = `${(center / 1e6).toFixed(3)} MHz`;
  const labelWidth = ctx.measureText(label).width;
  ctx.fillStyle = "rgba(8, 14, 10, 0.72)";
  ctx.fillRect(mid - labelWidth / 2 - 6, 2, labelWidth + 12, 16);
  ctx.fillStyle = accent;
  ctx.fillText(label, mid, 14);
  if (panHoverFrac != null && frame) {
    const hoverHz = panHzAt(panHoverFrac);
    if (hoverHz) {
      const x = Math.round(panHoverFrac * width) + 0.5;
      ctx.strokeStyle = ink;
      ctx.globalAlpha = 0.9;
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, plotBottom);
      ctx.stroke();
      ctx.globalAlpha = 1;
      const hoverLabel = `${(hoverHz / 1e6).toFixed(6)}`;
      const hoverWidth = ctx.measureText(hoverLabel).width;
      const cx = Math.max(hoverWidth / 2 + 8, Math.min(width - hoverWidth / 2 - 8, x));
      ctx.fillStyle = "rgba(8, 14, 10, 0.88)";
      ctx.fillRect(cx - hoverWidth / 2 - 6, 22, hoverWidth + 12, 16);
      ctx.fillStyle = "#f4f7ef";
      ctx.fillText(hoverLabel, cx, 34);
    }
  }
  const meta = $("panMeta");
  if (!meta) return;
  if (!panFrame) meta.textContent = "Opening the RTL-SDR";
  else if (!frame) meta.textContent = panFrame.error || panFrame.status || "RTL-SDR unavailable";
  else {
    const how = frame.direct ? "direct sampling" : "tuner";
    const widthText = half >= 1e6 ? `±${(half / 1e6).toFixed(3)} MHz` : `±${Math.round(half / 1000)} kHz`;
    const zoomText = zoom > 1 ? ` · ${zoom}×` : "";
    meta.textContent = `RTL-SDR · follows the radio · ${widthText}${zoomText} · ${how}`;
  }
}
try {
  $("panZoomIn")?.addEventListener("click", () => {
    const index = PAN_ZOOMS.indexOf(panZoom);
    setPanZoom(PAN_ZOOMS[Math.min(PAN_ZOOMS.length - 1, index + 1)]);
  });
  $("panZoomOut")?.addEventListener("click", () => {
    const index = PAN_ZOOMS.indexOf(panZoom);
    setPanZoom(PAN_ZOOMS[Math.max(0, index - 1)]);
  });
  $("panScope")?.addEventListener("wheel", (ev) => {
    ev.preventDefault();
    const index = PAN_ZOOMS.indexOf(panZoom);
    setPanZoom(PAN_ZOOMS[Math.max(0, Math.min(PAN_ZOOMS.length - 1, index + (ev.deltaY < 0 ? 1 : -1)))]);
  }, { passive: false });
  $("panScope")?.addEventListener("dblclick", () => setPanZoom(1));
  $("panScope")?.addEventListener("pointermove", (ev) => {
    const canvas = $("panScope");
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    if (rect.width < 40) return;
    panHoverFrac = Math.max(0, Math.min(1, (ev.clientX - rect.left) / rect.width));
    try { paintPanadapter(); } catch (_) { /* the next sweep redraws */ }
  });
  $("panScope")?.addEventListener("pointerleave", () => {
    panHoverFrac = null;
    try { paintPanadapter(); } catch (_) { /* the next sweep redraws */ }
  });
  $("panScope")?.addEventListener("click", (ev) => {
    try {
      const canvas = $("panScope");
      if (!canvas) return;
      const rect = canvas.getBoundingClientRect();
      if (rect.width < 40) return;
      const frac = (ev.clientX - rect.left) / rect.width;
      const hz = panHzAt(frac);
      if (!hz) return;
      if (potaScanOn) stopPotaScan();
      setHz(hz);
      panFall = null;
      scheduleTune();
    } catch (_) { /* leave the radio where it is */ }
  });
  const zoomOut = $("panZoomOut");
  const zoomIn = $("panZoomIn");
  if (zoomOut) zoomOut.disabled = panZoom === PAN_ZOOMS[0];
  if (zoomIn) zoomIn.disabled = panZoom === PAN_ZOOMS[PAN_ZOOMS.length - 1];
} catch (_) { /* zoom controls are optional */ }
async function pollPanadapter() {
  if (document.hidden || document.body.classList.contains("pan-collapsed")) return;
  try {
    const res = await fetch("/api/panadapter", { cache: "no-store" });
    if (!res.ok) return;
    panFrame = await res.json();
    if (panFrame.ok && Array.isArray(panFrame.bins) && panFrame.bins.length > 1) {
      pushPanWaterfall(panFrame.bins, Number(panFrame.center) || Number(lastHz) || 0);
    }
    paintPanadapter();
  } catch (_) { /* the next poll retries */ }
}
setInterval(pollPanadapter, 250);
pollPanadapter();
function parkPanadapterIfNew() {
  const el = $("panadapterPanel");
  if (!el || panelIsCollapsed("panadapterPanel") || el.classList.contains("panel-float")) return;
  const saved = readPanelPos();
  const pos = saved.panadapterPanel;
  if (pos && Number(pos.w) > 0) {
    applySavedPanel(el, pos);
    return;
  }
  floatPanel(el, 8, 8, 960, 200);
  savePanelLayout(el);
}
$("favHide").addEventListener("click", () => {
  setFavCollapsed(true);
});
$("favShow").addEventListener("click", () => {
  setFavCollapsed(false);
});
$("favId")?.addEventListener("click", async () => {
  const banner = $("favIdBanner");
  const kicker = $("favIdKicker");
  const title = $("favIdTitle");
  const detail = $("favIdDetail");
  const meta = $("favIdMeta");
  if (banner) banner.hidden = false;
  banner?.classList.remove("found", "miss", "on-air");
  if (kicker) kicker.textContent = `${(lastHz / 1_000_000).toFixed(6)} MHz`;
  if (title) title.textContent = "Searching…";
  if (detail) detail.textContent = "HamQTH DX cluster and short-wave.info";
  if (meta) meta.textContent = "";
  try {
    const data = await jsonFetch("/api/station/identify", {
      method: "POST",
      body: JSON.stringify({ hz: lastHz, mode: lastMode }),
    });
    banner?.classList.add(data.found ? "found" : "miss");
    if (data.on_air) banner?.classList.add("on-air");
    if (kicker) kicker.textContent = data.kicker || "";
    if (title) title.textContent = data.title || "";
    if (detail) detail.textContent = data.detail || "";
    if (meta) meta.textContent = data.meta || "";
    if (banner) {
      banner.dataset.call = data.call || "";
      banner.dataset.page = data.page || "";
    }
  } catch (err) {
    banner?.classList.add("miss");
    if (title) title.textContent = "Lookup failed";
    if (detail) detail.textContent = err.message;
  }
});
$("favIdClose")?.addEventListener("click", (ev) => {
  ev.stopPropagation();
  const banner = $("favIdBanner");
  if (banner) banner.hidden = true;
});
$("favIdBanner")?.addEventListener("click", () => {
  const banner = $("favIdBanner");
  const call = banner?.dataset.call;
  const page = banner?.dataset.page;
  if (call) {
    showQrzProfile(call);
    return;
  }
  if (page) {
    jsonFetch("/api/station/open", { method: "POST", body: JSON.stringify({ page }) }).catch(() => {});
  }
});
$("favSave").addEventListener("click", async () => {
  try {
    const data = await jsonFetch("/api/favorites", {
      method: "POST",
      body: JSON.stringify({
        name: $("favName").value,
        mhz: (lastHz / 1_000_000).toFixed(6),
        mode: lastMode,
      }),
    });
    $("favName").value = "";
    renderFavorites(data.favorites);
  } catch (err) {
    alert(err.message);
  }
});
$("favName").addEventListener("keydown", (ev) => {
  if (ev.key === "Enter") {
    ev.preventDefault();
    $("favSave").click();
  }
});
$("potaRefresh").addEventListener("click", () => {
  loadPota(true).catch(() => {});
});
$("potaFilter").addEventListener("input", () => renderPota());
$("potaScan").addEventListener("click", () => {
  if (potaScanOn) stopPotaScan();
  else startPotaScan();
});
$("potaScanDelay").addEventListener("input", () => {
  paintPotaScanDelay();
});
$("potaMaxAge").addEventListener("input", () => {
  paintPotaMaxAge();
  try { localStorage.setItem(POTA_AGE_KEY, String(potaMaxAgeMin())); } catch (_) { /* ignore */ }
  renderPota();
  if (potaAgeTimer) clearTimeout(potaAgeTimer);
  potaAgeTimer = setTimeout(() => {
    potaAgeTimer = 0;
    loadPota().catch(() => {});
  }, 250);
});
try {
  const savedAge = localStorage.getItem(POTA_AGE_KEY);
  const ageNum = Number(savedAge);
  if (savedAge != null && Number.isFinite(ageNum) && $("potaMaxAge")) {
    $("potaMaxAge").value = String(Math.max(1, Math.min(60, Math.round(ageNum))));
  }
} catch (_) { /* ignore */ }
paintPotaMaxAge();
paintPotaScanDelay();
$("connect").addEventListener("click", async () => {
  pauseRadarTiles();
  try {
    render(await jsonFetch("/api/connect", {
      method: "POST",
      body: JSON.stringify({
        port: $("port").value,
        baud: Number($("baud").value),
        stopbits: Number($("stopbits").value),
      }),
    }));
  } catch (err) {
    alert(err.message);
  } finally {
    resumeRadarTiles();
  }
});
$("demo").addEventListener("click", async () => {
  try {
    render(await jsonFetch("/api/connect", { method: "POST", body: JSON.stringify({ demo: true }) }));
  } catch (err) {
    alert(err.message);
  }
});
$("disconnect").addEventListener("click", async () => {
  render(await jsonFetch("/api/disconnect", { method: "POST", body: "{}" }));
});
$("powerOn").addEventListener("click", async () => {
  await send({ power_on: true });
});
$("powerOff").addEventListener("click", async () => {
  if (!window.confirm("Turn the radio off?")) return;
  await send({ power_off: true });
});
$("autoConnect").addEventListener("change", () => {
  jsonFetch("/api/settings", {
    method: "POST",
    body: JSON.stringify({ auto_connect: $("autoConnect").checked }),
  }).catch((err) => alert(err.message));
});
$("freqForm").addEventListener("submit", (ev) => {
  ev.preventDefault();
  stopFreqEdit();
  send({ mhz: $("freq").value });
});

const DIAL_STEP_HZ = 1000;
const DIAL_DETENT_DEG = 15;
const FREQ_PLACES = [
  { step: 10_000_000 },
  { step: 1_000_000 },
  { sep: "." },
  { step: 100_000 },
  { step: 10_000 },
  { step: 1_000 },
  { sep: "." },
  { step: 100 },
  { step: 10 },
  { step: 1 },
];
let lastHz = 14_250_000;
let dialing = false;
let digitTuning = false;
let digitStep = DIAL_STEP_HZ;
let digitLastY = 0;
let digitAcc = 0;
let dialDeg = 0;
let dialAcc = 0;
let lastDialAngle = null;
let dialTimer = 0;
let tuneBusy = false;
let tuneQueued = false;
let tuneLockUntil = 0;
let afDragging = false;
let afLockUntil = 0;
let afBusy = false;
let rfGainBusy = false;
let rfGainQueued = null;
let rfGainDragging = false;
let rfGainLockUntil = 0;
let rfGainCatLive = 255;
let startupRfGainSent = false;
let afQueued = null;
let afCatLive = 128;
let afDragUi = 50;
let afDragCat = 128;
let afDragArmed = false;
let powerBusy = false;
let powerQueued = null;
let powerLockUntil = 0;
let modeLockUntil = 0;
let lockedMode = "";

function paintFreqDigits() {
  const host = $("freqDigits");
  const text = String(lastHz).padStart(8, "0");
  let i = 0;
  if (!host.dataset.ready) {
    host.dataset.ready = "1";
    host.innerHTML = "";
    for (const place of FREQ_PLACES) {
      if (place.sep) {
        const sep = document.createElement("span");
        sep.className = "sep";
        sep.textContent = place.sep;
        host.append(sep);
        continue;
      }
      const el = document.createElement("span");
      el.className = "d";
      el.dataset.step = String(place.step);
      host.append(el);
    }
  }
  host.querySelectorAll(".d").forEach((el) => {
    el.textContent = text[i] || "0";
    i += 1;
  });
  $("freq").value = (lastHz / 1_000_000).toFixed(6);
}

function setHz(hz, snapHz) {
  if (snapHz) hz = Math.round(hz / snapHz) * snapHz;
  lastHz = Math.max(30_000, Math.min(56_000_000, Math.round(hz)));
  paintFreqDigits();
}

function showHz(hz) {
  setHz(hz, DIAL_STEP_HZ);
}

function startFreqEdit() {
  $("freqReadout").hidden = true;
  $("freq").hidden = false;
  $("freq").focus();
  $("freq").select();
}

function stopFreqEdit() {
  $("freq").hidden = true;
  $("freqReadout").hidden = false;
  paintFreqDigits();
}

function flushDial() {
  dialTimer = 0;
  pushTune();
}

function scheduleTune() {
  if (!dialTimer) dialTimer = setTimeout(flushDial, 50);
}

async function pushTune() {
  tuneLockUntil = Date.now() + 700;
  if (tuneBusy) {
    tuneQueued = true;
    return;
  }
  tuneBusy = true;
  const mhz = (lastHz / 1_000_000).toFixed(6);
  try {
    await jsonFetch("/api/command", { method: "POST", body: JSON.stringify({ mhz }) });
  } catch (err) {
    alert(err.message);
  } finally {
    tuneBusy = false;
    if (tuneQueued) {
      tuneQueued = false;
      pushTune();
    }
  }
}

function digitFromEvent(ev) {
  const el = ev.target.closest?.(".d");
  if (!el) return 0;
  const step = Number(el.dataset.step);
  return Number.isFinite(step) && step > 0 ? step : 0;
}

const readout = $("freqReadout");
let lastWheelAt = 0;
readout.addEventListener("wheel", (ev) => {
  ev.preventDefault();
  ev.stopPropagation();
  const step = digitFromEvent(ev);
  if (!step) return;
  const now = performance.now();
  const gap = step >= 10_000 ? 90 : 45;
  if (now - lastWheelAt < gap) return;
  lastWheelAt = now;
  digitTuning = true;
  setHz(lastHz + (ev.deltaY < 0 ? 1 : -1) * step);
  scheduleTune();
  clearTimeout(readout._wheelEnd);
  readout._wheelEnd = setTimeout(() => { digitTuning = false; scheduleTune(); }, 180);
}, { passive: false });
readout.addEventListener("pointerdown", (ev) => {
  if (ev.detail === 2) return;
  const step = digitFromEvent(ev);
  if (!step) return;
  ev.preventDefault();
  digitTuning = true;
  digitStep = step;
  digitLastY = ev.clientY;
  digitAcc = 0;
  readout.setPointerCapture(ev.pointerId);
});
readout.addEventListener("pointermove", (ev) => {
  if (!digitTuning || !readout.hasPointerCapture(ev.pointerId)) return;
  digitAcc += digitLastY - ev.clientY;
  digitLastY = ev.clientY;
  const px = digitStep >= 1000 ? 22 : 16;
  const steps = Math.trunc(digitAcc / px);
  if (!steps) return;
  digitAcc -= steps * px;
  setHz(lastHz + steps * digitStep);
  scheduleTune();
});
function endDigitTune(ev) {
  if (!digitTuning || dialing) return;
  if (ev && readout.hasPointerCapture(ev.pointerId)) readout.releasePointerCapture(ev.pointerId);
  digitTuning = false;
  scheduleTune();
}
readout.addEventListener("pointerup", endDigitTune);
readout.addEventListener("pointercancel", endDigitTune);
readout.addEventListener("dblclick", (ev) => {
  ev.preventDefault();
  startFreqEdit();
});
$("freq").addEventListener("blur", () => {
  if (!$("freq").hidden) stopFreqEdit();
});

function paintDial() {
  $("dial").style.transform = `rotate(${dialDeg}deg)`;
}

function nudgeDial(steps) {
  if (!steps) return;
  dialDeg += steps * DIAL_DETENT_DEG;
  paintDial();
  showHz(lastHz + steps * DIAL_STEP_HZ);
  scheduleTune();
}

function dialAngle(ev, el) {
  const box = el.getBoundingClientRect();
  return Math.atan2(ev.clientY - (box.top + box.height / 2), ev.clientX - (box.left + box.width / 2)) * 180 / Math.PI;
}

function wrapDelta(delta) {
  if (delta > 180) return delta - 360;
  if (delta < -180) return delta + 360;
  return delta;
}

const dial = $("dial");
dial.addEventListener("pointerdown", (ev) => {
  ev.preventDefault();
  dialing = true;
  lastDialAngle = dialAngle(ev, dial);
  dial.setPointerCapture(ev.pointerId);
});
dial.addEventListener("pointermove", (ev) => {
  if (!dialing || lastDialAngle == null) return;
  const ang = dialAngle(ev, dial);
  const delta = wrapDelta(ang - lastDialAngle);
  lastDialAngle = ang;
  dialAcc += delta;
  const steps = Math.trunc(dialAcc / DIAL_DETENT_DEG);
  if (steps) {
    dialAcc -= steps * DIAL_DETENT_DEG;
    nudgeDial(steps);
  }
});
function endDial(ev) {
  if (!dialing) return;
  dialing = false;
  lastDialAngle = null;
  if (ev && dial.hasPointerCapture?.(ev.pointerId)) dial.releasePointerCapture(ev.pointerId);
  if (!dialTimer) flushDial();
}
dial.addEventListener("pointerup", endDial);
dial.addEventListener("pointercancel", endDial);
dial.addEventListener("lostpointercapture", () => { if (dialing) endDial(); });
dial.addEventListener("wheel", (ev) => {
  ev.preventDefault();
  const now = performance.now();
  if (now - lastWheelAt < 45) return;
  lastWheelAt = now;
  nudgeDial(ev.deltaY < 0 ? 1 : -1);
}, { passive: false });
dial.addEventListener("keydown", (ev) => {
  if (ev.key === "ArrowRight" || ev.key === "ArrowUp") {
    ev.preventDefault();
    nudgeDial(1);
  } else if (ev.key === "ArrowLeft" || ev.key === "ArrowDown") {
    ev.preventDefault();
    nudgeDial(-1);
  }
});
paintDial();
paintFreqDigits();
const POWER_LOCK_KEY = "ftdx-rf-power-lock";
let rfPowerLocked = true;

function setRfPowerLocked(on) {
  rfPowerLocked = !!on;
  const wrap = document.querySelector(".rf-power");
  const slider = $("power");
  const btn = $("powerLock");
  wrap?.classList.toggle("locked", rfPowerLocked);
  if (slider) slider.disabled = rfPowerLocked;
  if (btn) {
    btn.classList.toggle("on", rfPowerLocked);
    btn.setAttribute("aria-pressed", rfPowerLocked ? "true" : "false");
    const tip = rfPowerLocked ? "Unlock RF power to change it" : "Lock RF power";
    btn.title = tip;
    btn.setAttribute("aria-label", tip);
  }
  try { localStorage.setItem(POWER_LOCK_KEY, rfPowerLocked ? "1" : "0"); } catch (_) { /* ignore */ }
}

setRfPowerLocked(localStorage.getItem(POWER_LOCK_KEY) !== "0");
$("powerLock")?.addEventListener("click", (ev) => {
  ev.preventDefault();
  setRfPowerLocked(!rfPowerLocked);
});
$("power").addEventListener("input", () => {
  if (rfPowerLocked) return;
  $("powerVal").textContent = `${$("power").value} W`;
  sendPower();
});
async function sendPower() {
  if (rfPowerLocked) return;
  const watts = Math.max(5, Math.min(100, Number($("power").value) || 5));
  powerLockUntil = Date.now() + 400;
  if (powerBusy) {
    powerQueued = watts;
    return;
  }
  powerBusy = true;
  try {
    while (true) {
      const level = powerQueued == null ? watts : powerQueued;
      powerQueued = null;
      await jsonFetch("/api/command", { method: "POST", body: JSON.stringify({ power: level }) });
      if (powerQueued == null) break;
    }
  } catch (err) {
    alert(err.message);
  } finally {
    powerBusy = false;
  }
}
const AF_EXP = 0.55;
function afUiToCat(ui) {
  const x = Math.max(0, Math.min(100, Number(ui))) / 100;
  return Math.round(255 * Math.pow(x, AF_EXP));
}
function afCatToUi(cat) {
  const y = Math.max(0, Math.min(255, Number(cat))) / 255;
  if (y <= 0) return 0;
  return Math.round(100 * Math.pow(y, 1 / AF_EXP));
}
function afFromDrag() {
  const ui = Number($("af").value);
  const delta = afUiToCat(ui) - afUiToCat(afDragUi);
  return Math.max(0, Math.min(255, afDragCat + delta));
}
async function sendAf() {
  const cat = afFromDrag();
  if (cat === afCatLive && afQueued == null) return;
  afLockUntil = Date.now() + 400;
  if (afBusy) {
    afQueued = cat;
    return;
  }
  afBusy = true;
  try {
    while (true) {
      const level = afQueued == null ? cat : afQueued;
      afQueued = null;
      await jsonFetch("/api/command", { method: "POST", body: JSON.stringify({ af: level }) });
      afCatLive = level;
      if (afQueued == null) break;
    }
  } catch (err) {
    alert(err.message);
  } finally {
    afBusy = false;
  }
}
function afUiFromClientX(clientX) {
  const el = $("af");
  const rect = el.getBoundingClientRect();
  const min = Number(el.min) || 0;
  const max = Number(el.max) || 100;
  const pad = 7;
  const span = Math.max(1, rect.width - pad * 2);
  const x = Math.max(0, Math.min(1, (clientX - rect.left - pad) / span));
  return Math.round(min + x * (max - min));
}
$("af").addEventListener("pointerdown", (ev) => {
  ev.stopPropagation();
  selectPanel(null);
  afDragging = true;
  afDragArmed = true;
  afDragUi = Number($("af").value);
  afDragCat = afCatLive;
  if (ev.button !== 0) return;
  const jumped = afUiFromClientX(ev.clientX);
  if (jumped !== afDragUi) {
    $("af").value = String(jumped);
    $("afVal").textContent = `${jumped}%`;
    sendAf();
  }
});
$("af").addEventListener("pointerup", () => {
  afDragging = false;
  if (Number($("af").value) !== afDragUi) sendAf();
});
$("af").addEventListener("pointercancel", () => { afDragging = false; });
$("af").addEventListener("input", () => {
  $("afVal").textContent = `${$("af").value}%`;
  sendAf();
});
function rfGainUiToCat(ui) {
  return Math.round(Math.max(0, Math.min(100, Number(ui))) / 100 * 255);
}
function rfGainCatToUi(cat) {
  return Math.round(Math.max(0, Math.min(255, Number(cat))) / 255 * 100);
}
function maybeForceStartupRfGain(state) {
  if (startupRfGainSent || !state.connected || state.radio_on === false || !$("rfGain")) return;
  startupRfGainSent = true;
  rfGainLockUntil = Date.now() + 1500;
  rfGainCatLive = -1;
  $("rfGain").value = "100";
  $("rfGainVal").textContent = "100%";
  sendRfGain();
}
async function sendRfGain() {
  const cat = rfGainUiToCat($("rfGain").value);
  if (cat === rfGainCatLive && rfGainQueued == null) return;
  rfGainLockUntil = Date.now() + 400;
  if (rfGainBusy) {
    rfGainQueued = cat;
    return;
  }
  rfGainBusy = true;
  try {
    while (true) {
      const level = rfGainQueued == null ? cat : rfGainQueued;
      rfGainQueued = null;
      await jsonFetch("/api/command", { method: "POST", body: JSON.stringify({ rf_gain: level }) });
      rfGainCatLive = level;
      if (rfGainQueued == null) break;
    }
  } catch (err) {
    alert(err.message);
  } finally {
    rfGainBusy = false;
  }
}
function sliderUiFromClientX(el, clientX) {
  const rect = el.getBoundingClientRect();
  const min = Number(el.min) || 0;
  const max = Number(el.max) || 100;
  const pad = 7;
  const span = Math.max(1, rect.width - pad * 2);
  const x = Math.max(0, Math.min(1, (clientX - rect.left - pad) / span));
  return Math.round(min + x * (max - min));
}
$("rfGain")?.addEventListener("pointerdown", (ev) => {
  ev.stopPropagation();
  selectPanel(null);
  rfGainDragging = true;
  if (ev.button !== 0) return;
  const jumped = sliderUiFromClientX($("rfGain"), ev.clientX);
  if (jumped !== Number($("rfGain").value)) {
    $("rfGain").value = String(jumped);
    $("rfGainVal").textContent = `${jumped}%`;
    sendRfGain();
  }
});
$("rfGain")?.addEventListener("pointerup", () => { rfGainDragging = false; });
$("rfGain")?.addEventListener("pointercancel", () => { rfGainDragging = false; });
$("rfGain")?.addEventListener("input", () => {
  $("rfGainVal").textContent = `${$("rfGain").value}%`;
  sendRfGain();
});
$("rxMonitor").addEventListener("click", () => {
  setAudio({
    monitor: !$("rxMonitor").classList.contains("on"),
    input: $("rxIn").value === "" ? null : Number($("rxIn").value),
    output: $("rxOut").value === "" ? null : Number($("rxOut").value),
  }).catch((err) => alert(err.message));
});
$("rxMute").addEventListener("click", () => {
  setAudio({ mute: !$("rxMute").classList.contains("on") }).catch((err) => alert(err.message));
});
$("rxIn").addEventListener("change", () => {
  setAudio({ input: $("rxIn").value === "" ? null : Number($("rxIn").value) }).catch((err) => alert(err.message));
});
$("rxOut").addEventListener("change", () => {
  setAudio({ output: $("rxOut").value === "" ? null : Number($("rxOut").value) }).catch((err) => alert(err.message));
});
$("rxVol").addEventListener("change", () => {
  setAudio({ volume: Number($("rxVol").value) / 100 }).catch((err) => alert(err.message));
});
$("swap").addEventListener("click", () => send({ swap: true }));
$("copyAB").addEventListener("click", () => send({ copy_ab: true }));
$("tunerStart").addEventListener("click", () => send({ tuner: "start" }));
$("tunerOn").addEventListener("click", () => send({ tuner: "on" }));
$("tunerOff").addEventListener("click", () => send({ tuner: "off" }));

const ptt = $("ptt");
function pttOn() { send({ tx: true }); }
function pttOff() { send({ tx: false }); }
ptt.addEventListener("pointerdown", (ev) => { ev.preventDefault(); ptt.setPointerCapture(ev.pointerId); pttOn(); });
ptt.addEventListener("pointerup", pttOff);
ptt.addEventListener("pointercancel", pttOff);
ptt.addEventListener("lostpointercapture", pttOff);

let liveBusy = false;
let fullBusy = false;

async function pollLive() {
  if (liveBusy) return;
  liveBusy = true;
  try {
    applyLive(await jsonFetch("/api/status"));
  } catch (_) {
    /* keep last screen */
  } finally {
    liveBusy = false;
  }
}

async function poll() {
  if (fullBusy) return;
  if (tuneBusy || afBusy || afDragging || rfGainBusy || rfGainDragging || powerBusy || Date.now() < tuneLockUntil || Date.now() < afLockUntil || Date.now() < rfGainLockUntil || Date.now() < powerLockUntil) {
    await pollLive();
    return;
  }
  fullBusy = true;
  try {
    render(await jsonFetch("/api/status?full=1"));
  } catch (_) {
    /* keep last screen */
  } finally {
    fullBusy = false;
  }
}

loadPorts().catch(() => {});
loadAudio().catch(() => {});
loadFavorites().catch(() => {});
loadQrz().catch(() => {});
loadLogbook().catch(() => {});
paintHideFilters();
loadPota().catch(() => {});
poll();
setInterval(pollLive, 80);
setInterval(poll, 800);
setInterval(() => loadPota().catch(() => {}), 60000);
window.addEventListener("resize", () => {
  document.documentElement.style.fontSize = "";
  layoutPotaForWindow();
  drawSAnalog();
  resizeRadarMap();
  layoutClocksForWindow();
  alignTitleToTuner();
  paintPanadapter();
});
requestAnimationFrame(needleFrame);
drawSAnalog();
drawUtcClock();
setInterval(drawUtcClock, 250);
requestAnimationFrame(fitUtcClock);
loadSolarBanner();
setInterval(loadSolarBanner, 3600000);
$("solarBannerBtn")?.addEventListener("click", () => {
  jsonFetch("/api/solar/open", { method: "POST", body: "{}" }).catch(() => {});
});

function alignTitleToTuner() {
  const title = document.querySelector(".header-brand h1");
  const tuner = $("tunerPanel");
  if (!title || !tuner) return;
  const shift = tuner.getBoundingClientRect().left - title.getBoundingClientRect().left;
  if (Math.abs(shift) < 0.5) return;
  const current = parseFloat(title.style.marginLeft) || 0;
  title.style.marginLeft = `${Math.max(0, Math.round(current + shift))}px`;
  placeHeaderCall();
}

function placeHeaderCall() {
  const call = $("headerCall");
  const version = document.querySelector(".app-version");
  const header = document.querySelector("header");
  const clock = $("clockPanel");
  if (!call || !version || !header) return;
  const headerBox = header.getBoundingClientRect();
  const versionBox = version.getBoundingClientRect();
  let rightEdge = headerBox.right;
  if (clock && clock.offsetParent) {
    const clockBox = clock.getBoundingClientRect();
    if (clockBox.width > 20) rightEdge = clockBox.left;
  }
  const gapLeft = versionBox.right + 12;
  const gapRight = rightEdge - 12;
  if (gapRight - gapLeft < call.offsetWidth) {
    call.style.visibility = "hidden";
    return;
  }
  call.style.visibility = "visible";
  call.style.left = `${(gapLeft + gapRight) / 2 - headerBox.left}px`;
}

function isClockPanel(el) {
  return !!el && el.id === "clockPanel";
}

function clockFitSize(panel) {
  if (!isClockPanel(panel) || panel.clientWidth < 8) return null;
  const half = panel.clientWidth / 2;
  const h = panel.clientHeight;
  return Math.max(11, Math.min(16, half / 6.2, Math.max(14, h - 14) * 0.5));
}

function applyClockSize(panel, size) {
  panel.querySelectorAll("#utcClock, #localClock").forEach((clock) => {
    clock.style.fontSize = `${size}px`;
    clock.style.padding = `${Math.max(2, size * 0.12)}px ${Math.max(4, size * 0.2)}px`;
  });
  const label = Math.max(9, Math.min(11, size * 0.42));
  panel.querySelectorAll(".clock-meta").forEach((el) => {
    el.style.fontSize = `${label}px`;
  });
}

function fitUtcClock() {
  const panel = $("clockPanel");
  if (!panel) return;
  const size = clockFitSize(panel);
  if (Number.isFinite(size)) applyClockSize(panel, size);
}

let clockWindow = { w: window.innerWidth || 1, h: window.innerHeight || 1 };
let clockLayoutSaveTimer = 0;
let clockPinned = false;

function alignClockToPota(save) {
  const clock = $("clockPanel");
  const pota = $("potaPanel");
  const tools = document.querySelector(".header-tools");
  if (!clock || !pota || !tools || !clockPinned) return;
  const origin = deskOrigin();
  const potaBox = pota.getBoundingClientRect();
  const toolBox = tools.getBoundingClientRect();
  const clockW = clock.offsetWidth || 280;
  const clockH = clock.offsetHeight || 56;
  const x = Math.round(potaBox.left + (potaBox.width - clockW) / 2 - origin.x + origin.sl);
  const y = Math.round(toolBox.top + (toolBox.height - clockH) / 2 - origin.y + origin.st);
  clock.classList.add("panel-float", "resized", "resized-h");
  clock.style.left = `${x}px`;
  clock.style.top = `${y}px`;
  clock.style.zIndex = "80";
  fitUtcClock();
  placeHeaderCall();
  if (save !== false) savePanelLayout(clock);
}

function unpinClock() {
  if (!clockPinned) return;
  clockPinned = false;
  const all = readPanelPos();
  all.__clock = { pinned: false };
  writePanelPos(all);
}

let scaleModel = null;
let scalingPanels = false;

function windowBox() {
  return {
    w: Math.max(1, window.innerWidth || 1),
    h: Math.max(1, window.innerHeight || 1),
  };
}

function scaleFactors() {
  const base = scaleModel && scaleModel.window;
  const now = windowBox();
  if (!base || !base.w || !base.h) return { sx: 1, sy: 1 };
  const s = Math.min(now.w / base.w, now.h / base.h);
  return { sx: s, sy: s };
}

function floatedPanelBoxes(saved) {
  const panels = {};
  for (const [id, pos] of Object.entries(saved || {})) {
    if (!pos || typeof pos !== "object" || id.startsWith("__")) continue;
    if (pos.floated === false || !Number.isFinite(Number(pos.x)) || !Number.isFinite(Number(pos.y))) continue;
    panels[id] = {
      x: Number(pos.x),
      y: Number(pos.y),
      w: Number(pos.w) > 0 ? Number(pos.w) : 0,
      h: Number(pos.h) > 0 ? Number(pos.h) : 0,
    };
  }
  return panels;
}

function captureScale(saved) {
  const panels = floatedPanelBoxes(saved);
  saved.__scale = {
    on: true,
    window: windowBox(),
    base: cloneLayout(panels),
    snapshot: cloneLayout(panels),
  };
  return saved.__scale;
}

function rebasePanel(el) {
  if (!scaleModel || !scaleModel.on || !el || !el.id) return;
  const { sx, sy } = scaleFactors();
  if (!sx || !sy) return;
  if (!scaleModel.base) scaleModel.base = {};
  scaleModel.base[el.id] = {
    x: (parseFloat(el.style.left) || 0) / sx,
    y: (parseFloat(el.style.top) || 0) / sy,
    w: el.offsetWidth / sx,
    h: el.offsetHeight / sy,
  };
}

function paintScaleToggle() {
  const btn = $("scaleToggle");
  if (!btn) return;
  const on = !!(scaleModel && scaleModel.on);
  btn.classList.toggle("on", on);
  btn.setAttribute("aria-pressed", on ? "true" : "false");
  btn.textContent = on ? "Proportional sizing on" : "Proportional sizing";
}

function scheduleScaleSave() {
  clearTimeout(clockLayoutSaveTimer);
  clockLayoutSaveTimer = setTimeout(() => {
    const all = readPanelPos();
    document.querySelectorAll("#desk .panel.panel-float").forEach((el) => {
      const prev = all[el.id] && typeof all[el.id] === "object" ? all[el.id] : {};
      const w = el.offsetWidth;
      const h = el.offsetHeight;
      all[el.id] = {
        ...prev,
        x: parseFloat(el.style.left) || 0,
        y: parseFloat(el.style.top) || 0,
        w: w > 0 ? w : (Number(prev.w) || 0),
        h: h > 0 ? h : (Number(prev.h) || 0),
        floated: true,
        resized: true,
        resizedH: true,
      };
    });
    if (scaleModel) all.__scale = scaleModel;
    persistPanelLayout(all);
  }, 400);
}

function applyScaleToPanels() {
  if (!scaleModel || !scaleModel.on || scalingPanels) return;
  const { sx, sy } = scaleFactors();
  if (Math.abs(sx - 1) < 0.004 && Math.abs(sy - 1) < 0.004) return;
  scalingPanels = true;
  try {
    for (const [id, base] of Object.entries(scaleModel.base || {})) {
      const el = document.getElementById(id);
      if (!el || !el.classList.contains("panel-float") || el.classList.contains("dragging")) continue;
      if (isClockPanel(el) && clockPinned) continue;
      el.style.left = `${Math.round(base.x * sx)}px`;
      el.style.top = `${Math.round(base.y * sy)}px`;
      if (base.w > 0) {
        el.style.width = `${Math.max(panelMinW(el), Math.round(base.w * sx))}px`;
        el.classList.add("resized");
      }
      if (base.h > 0) {
        el.style.height = `${Math.max(panelMinH(el), Math.round(base.h * sy))}px`;
        el.classList.add("resized-h");
      }
    }
    if (clockPinned) alignClockToPota(false);
    growDesk();
    drawSAnalog();
    resizeRadarMap();
    layoutPotaForWindow();
    fitUtcClock();
    alignTitleToTuner();
  } finally {
    scalingPanels = false;
  }
  scheduleScaleSave();
}

function setProportionalScale(on) {
  const all = readPanelPos();
  if (on) {
    scaleModel = captureScale(all);
    persistPanelLayout(all);
    paintScaleToggle();
    return;
  }
  const snap = (scaleModel && scaleModel.snapshot) || (all.__scale && all.__scale.snapshot);
  if (snap) {
    for (const [id, box] of Object.entries(snap)) {
      const el = document.getElementById(id);
      const prev = all[id] && typeof all[id] === "object" ? all[id] : {};
      all[id] = {
        ...prev,
        x: box.x,
        y: box.y,
        w: box.w,
        h: box.h,
        floated: true,
        resized: box.w > 0,
        resizedH: box.h > 0,
      };
      if (el) applySavedPanel(el, all[id]);
    }
  }
  all.__scale = { on: false, snapshot: snap || {} };
  scaleModel = null;
  persistPanelLayout(all);
  paintScaleToggle();
  growDesk();
  drawSAnalog();
  resizeRadarMap();
  layoutPotaForWindow();
  fitUtcClock();
}

function layoutClocksForWindow() {
  if (scaleModel && scaleModel.on) {
    applyScaleToPanels();
    return;
  }
  if (clockPinned) {
    alignClockToPota();
    return;
  }
  const w = Math.max(1, window.innerWidth || 1);
  const h = Math.max(1, window.innerHeight || 1);
  const sx = w / clockWindow.w;
  const sy = h / clockWindow.h;
  clockWindow = { w, h };
  const scale = Math.abs(sx - 1) > 0.004 || Math.abs(sy - 1) > 0.004;
  let moved = false;
  if (scale) {
    [$("clockPanel")].forEach((el) => {
      if (!el || !el.classList.contains("panel-float")) return;
      const left = parseFloat(el.style.left);
      const top = parseFloat(el.style.top);
      if (Number.isFinite(left)) el.style.left = `${Math.round(left * sx)}px`;
      if (Number.isFinite(top)) el.style.top = `${Math.round(top * sy)}px`;
      if (el.classList.contains("resized")) {
        el.style.width = `${Math.max(panelMinW(el), Math.round(el.offsetWidth * sx))}px`;
      }
      if (el.classList.contains("resized-h")) {
        el.style.height = `${Math.max(panelMinH(el), Math.round(el.offsetHeight * sy))}px`;
      }
      moved = true;
    });
  }
  fitUtcClock();
  if (moved) {
    clearTimeout(clockLayoutSaveTimer);
    clockLayoutSaveTimer = setTimeout(() => {
      [$("clockPanel")].forEach((el) => {
        if (el && el.classList.contains("panel-float")) savePanelLayout(el);
      });
    }, 400);
  }
}

function panelMinW(el) {
  if (isClockPanel(el)) return 176;
  if (el && el.id === "vfoPanel") return 200;
  if (el && el.id === "favPanel") return 280;
  if (el && el.id === "tunerPanel") return 300;
  if (el && el.id === "voacapPanel") return 420;
  if (el && el.id === "issPanel") return 420;
  if (el && el.id === "panadapterPanel") return 480;
  return PANEL_MIN_W;
}

function panelMinH(el) {
  if (isClockPanel(el)) return 40;
  if (el && el.id === "vfoPanel") return el.classList.contains("vfo-meter-bar") ? 96 : 120;
  if (el && el.id === "favPanel") return 120;
  if (el && el.id === "tunerPanel") return 220;
  if (el && el.id === "voacapPanel") return 180;
  if (el && el.id === "issPanel") return 180;
  if (el && el.id === "panadapterPanel") return 160;
  return PANEL_MIN_H;
}

const PANEL_POS_KEY = "ftdx-panel-pos";
const PANEL_LAYOUT_GEN = 31;
const PANEL_GAP = 8;
const OPS_TOP_IDS = ["vfoPanel", "potaPanel", "voacapPanel", "blankPanel"];
const PANEL_MIN_W = 200;
const PANEL_MIN_H = 96;
let panelZ = 10;

function readPanelPos() {
  try {
    return JSON.parse(localStorage.getItem(PANEL_POS_KEY) || "{}") || {};
  } catch (_) {
    return {};
  }
}

function writePanelPos(all) {
  try { localStorage.setItem(PANEL_POS_KEY, JSON.stringify(all)); } catch (_) { /* ignore */ }
}

function persistPanelLayout(all) {
  if (all && typeof all === "object") all.__layout = { gen: PANEL_LAYOUT_GEN };
  writePanelPos(all);
  jsonFetch("/api/layout", { method: "POST", body: JSON.stringify({ panel_pos: all }) }).catch(() => {});
}

function deskOrigin() {
  const desk = $("desk");
  const r = desk.getBoundingClientRect();
  return { x: r.left, y: r.top, sl: desk.scrollLeft, st: desk.scrollTop };
}

function sizePanel(el, w, h) {
  if (w != null && Number(w) > 0) {
    el.style.width = `${Math.max(panelMinW(el), Number(w))}px`;
    el.classList.add("resized");
  }
  if (h != null && Number(h) > 0) {
    el.style.height = `${Math.max(panelMinH(el), Number(h))}px`;
    el.classList.add("resized-h");
  }
  if (isClockPanel(el)) fitUtcClock();
  if (focusedPanel === el) armPanelFocusTimeout();
}

function floatPanel(el, x, y, w, h) {
  el.classList.add("panel-float");
  const minX = isClockPanel(el) ? -400 : 0;
  const minY = isClockPanel(el) ? -120 : 0;
  el.style.left = `${Math.max(minX, x)}px`;
  el.style.top = `${Math.max(minY, y)}px`;
  if (w || h) sizePanel(el, w, h);
  el.style.zIndex = String(++panelZ);
}

function migrateClockPanel(saved) {
  if (!saved || typeof saved !== "object") return saved;
  const boxes = ["utcPanel", "localPanel"]
    .map((id) => saved[id])
    .filter((pos) => pos && pos.floated !== false && Number.isFinite(Number(pos.x)) && Number.isFinite(Number(pos.y)));
  if (!saved.clockPanel && boxes.length) {
    const left = Math.min(...boxes.map((pos) => Number(pos.x)));
    const top = Math.min(...boxes.map((pos) => Number(pos.y)));
    const right = Math.max(...boxes.map((pos) => Number(pos.x) + (Number(pos.w) > 0 ? Number(pos.w) : 148)));
    const bottom = Math.max(...boxes.map((pos) => Number(pos.y) + (Number(pos.h) > 0 ? Number(pos.h) : 64)));
    saved.clockPanel = {
      x: Math.round(left),
      y: Math.round(top),
      w: Math.max(220, Math.round(right - left)),
      h: Math.max(48, Math.round(bottom - top)),
      floated: true,
      resized: true,
      resizedH: true,
    };
  } else if (!saved.clockPanel) {
    saved.clockPanel = { x: 280, y: 6, w: 280, h: 56, floated: true, resized: true, resizedH: true };
  }
  delete saved.utcPanel;
  delete saved.localPanel;
  return saved;
}

function pinPanelFloat(el) {
  if (!el || el.classList.contains("panel-float")) return;
  const rect = el.getBoundingClientRect();
  const origin = deskOrigin();
  const keepSize = el.id === "blankPanel" || el.id === "potaPanel" || isClockPanel(el) || el.id === "vfoPanel" || el.id === "favPanel" || el.id === "tunerPanel" || el.id === "voacapPanel" || el.id === "issPanel";
  floatPanel(
    el,
    rect.left - origin.x + origin.sl,
    rect.top - origin.y + origin.st,
    el.classList.contains("resized") || keepSize ? rect.width : null,
    el.classList.contains("resized-h") || keepSize ? rect.height : null,
  );
}

let focusedPanel = null;
let panelNudgeSaveTimer = 0;
let panelFocusTimer = 0;
const PANEL_FOCUS_MS = 10000;

function armPanelFocusTimeout() {
  clearTimeout(panelFocusTimer);
  if (!focusedPanel) return;
  panelFocusTimer = setTimeout(() => selectPanel(null), PANEL_FOCUS_MS);
}

function keepVfoAboveTuner() {
  const vfo = $("vfoPanel");
  const tuner = $("tunerPanel");
  if (!vfo || !tuner || !vfo.classList.contains("panel-float")) return;
  const tunerZ = parseInt(tuner.style.zIndex, 10) || 0;
  const vfoZ = parseInt(vfo.style.zIndex, 10) || 0;
  if (vfoZ <= tunerZ) vfo.style.zIndex = String(++panelZ);
}

function keepClockAbovePota() {
  const clock = $("clockPanel");
  const pota = $("potaPanel");
  if (!clock || !pota || !clock.classList.contains("panel-float")) return;
  const potaZ = parseInt(pota.style.zIndex, 10) || 0;
  const clockZ = parseInt(clock.style.zIndex, 10) || 0;
  if (clockZ <= potaZ) clock.style.zIndex = String(++panelZ);
}

function keepModeBandAbovePota() {
  const mode = $("modeBandPanel");
  const pota = $("potaPanel");
  if (!mode || !pota || !mode.classList.contains("panel-float")) return;
  if (document.body.classList.contains("mode-band-collapsed")) return;
  const potaZ = parseInt(pota.style.zIndex, 10) || 0;
  const modeZ = parseInt(mode.style.zIndex, 10) || 0;
  if (modeZ <= potaZ) mode.style.zIndex = String(++panelZ);
}

function selectPanel(el) {
  document.querySelectorAll(".panel.panel-focus").forEach((p) => p.classList.remove("panel-focus"));
  focusedPanel = el && el.classList.contains("panel") ? el : null;
  clearTimeout(panelFocusTimer);
  if (!focusedPanel) return;
  focusedPanel.classList.add("panel-focus");
  focusedPanel.style.zIndex = String(++panelZ);
  if (focusedPanel.id !== "vfoPanel") keepVfoAboveTuner();
  if (focusedPanel.id !== "clockPanel") keepClockAbovePota();
  if (focusedPanel.id !== "modeBandPanel") keepModeBandAbovePota();
  armPanelFocusTimeout();
}

function nudgeFocusedPanel(dx, dy) {
  if (document.body.classList.contains("panels-locked") && !isClockPanel(focusedPanel)) return;
  const el = focusedPanel;
  if (!el) return;
  pinPanelFloat(el);
  const minX = isClockPanel(el) ? -400 : 0;
  const minY = isClockPanel(el) ? -120 : 0;
  const x = (parseFloat(el.style.left) || 0) + dx;
  const y = (parseFloat(el.style.top) || 0) + dy;
  el.style.left = `${Math.max(minX, Math.round(x))}px`;
  el.style.top = `${Math.max(minY, Math.round(y))}px`;
  growDesk();
  if (el.id === "blankPanel") resizeRadarMap();
  if (el.id === "vfoPanel") drawSAnalog();
  clearTimeout(panelNudgeSaveTimer);
  panelNudgeSaveTimer = setTimeout(() => savePanelLayout(el), 280);
  armPanelFocusTimeout();
}

function dockedPanelPos() {
  return { floated: false, x: null, y: null, w: null, h: null, resized: false, resizedH: false };
}

function defaultPanelLayout() {
  const gap = 10;
  const clockH = 56;
  const top = 6 + clockH + gap;
  const deskW = Math.max(980, Math.min(1888, (window.innerWidth || 1920) - 32));
  const deskH = Math.max(560, Math.min(980, (window.innerHeight || 1080) - 88));
  const vfoW = 460;
  const vfoH = 420;
  const leftover = Math.max(700, deskW - vfoW - gap * 2);
  const potaW = Math.max(380, Math.min(560, Math.round(leftover * 0.52)));
  const blankW = Math.max(340, leftover - potaW);
  const potaX = vfoW + gap;
  const blankX = potaX + potaW + gap;
  return {
    __layout: { gen: PANEL_LAYOUT_GEN },
    clockPanel: { x: 280, y: 6, w: 280, h: clockH, floated: true, resized: true, resizedH: true },
    vfoPanel: { x: 0, y: top, w: vfoW, h: vfoH, floated: true, resized: true, resizedH: true },
    potaPanel: { x: potaX, y: top, w: potaW, h: Math.max(vfoH, deskH - 24), floated: true, resized: true, resizedH: true },
    blankPanel: { x: blankX, y: top, w: blankW, h: vfoH, floated: true, resized: true, resizedH: true },
    favPanel: dockedPanelPos(),
    modeBandPanel: dockedPanelPos(),
  };
}

function layoutGenOf(all) {
  const n = Number(all && all.__layout && all.__layout.gen);
  return Number.isFinite(n) ? n : 0;
}

function evenPanelGaps(saved) {
  if (!saved || typeof saved !== "object") return saved;
  const boxes = Object.keys(saved).filter((id) => {
    if (id.startsWith("__") || !document.getElementById(id)) return false;
    const pos = saved[id];
    return pos && pos.floated !== false && Number.isFinite(Number(pos.x)) && Number.isFinite(Number(pos.y)) && Number(pos.w) > 0 && Number(pos.h) > 0;
  }).map((id) => ({
    id,
    x: Number(saved[id].x),
    y: Number(saved[id].y),
    w: Number(saved[id].w),
    h: Number(saved[id].h),
  }));
  const overlays = [];
  const tiles = [];
  for (const box of boxes) {
    const host = boxes.find((other) => {
      if (other.id === box.id || other.w * other.h < box.w * box.h * 1.4) return false;
      return box.x >= other.x - 4 && box.x + box.w <= other.x + other.w + 8 && box.y >= other.y - 4 && box.y + box.h <= other.y + other.h + 8;
    });
    if (host) overlays.push({ box, hostId: host.id });
    else tiles.push(box);
  }
  tiles.sort((a, b) => a.x - b.x || a.y - b.y);
  const columns = [];
  for (const tile of tiles) {
    const col = columns.find((group) => {
      const left = Math.min(...group.map((item) => item.x));
      const right = Math.max(...group.map((item) => item.x + item.w));
      const overlap = Math.min(tile.x + tile.w, right) - Math.max(tile.x, left);
      return overlap > Math.min(tile.w, right - left) * 0.45;
    });
    if (col) col.push(tile);
    else columns.push([tile]);
  }
  columns.sort((a, b) => Math.min(...a.map((item) => item.x)) - Math.min(...b.map((item) => item.x)));
  const placed = {};
  let x = PANEL_GAP;
  for (const col of columns) {
    const w = Math.round(Math.max(...col.map((item) => item.w)));
    col.sort((a, b) => a.y - b.y);
    let y = PANEL_GAP;
    for (const tile of col) {
      placed[tile.id] = { x, y, w, h: Math.round(tile.h) };
      y += Math.round(tile.h) + PANEL_GAP;
    }
    x += w + PANEL_GAP;
  }
  const rows = [];
  Object.keys(placed).forEach((id) => {
    const item = { id, y: placed[id].y };
    const row = rows.find((group) => Math.abs(group[0].y - item.y) <= 24);
    if (row) row.push(item);
    else rows.push([item]);
  });
  rows.forEach((row) => {
    if (row.length < 2) return;
    const y = Math.max(...row.map((item) => item.y));
    row.forEach((item) => {
      const delta = y - placed[item.id].y;
      if (delta <= 0) return;
      placed[item.id].y = y;
      const col = columns.find((group) => group.some((tile) => tile.id === item.id));
      const index = col.findIndex((tile) => tile.id === item.id);
      if (index > 0) placed[col[index - 1].id].h += delta;
    });
  });
  Object.keys(placed).forEach((id) => {
    Object.assign(saved[id], placed[id], { floated: true, resized: true, resizedH: true });
  });
  overlays.forEach(({ box, hostId }) => {
    const host = placed[hostId];
    if (!host || !saved[box.id]) return;
    saved[box.id].x = Math.round(host.x + host.w - box.w - PANEL_GAP);
    saved[box.id].y = Math.round(host.y + PANEL_GAP);
    saved[box.id].floated = true;
  });
  return saved;
}

const COLLAPSED_PANEL = {
  modeBandPanel: "mode-band-collapsed",
  favPanel: "fav-collapsed",
  tunerPanel: "tuner-collapsed",
  voacapPanel: "voacap-collapsed",
  issPanel: "iss-collapsed",
  panadapterPanel: "pan-collapsed",
};

function panelIsCollapsed(id) {
  const cls = COLLAPSED_PANEL[id];
  return !!(cls && document.body.classList.contains(cls));
}

function fitIssHeight() {
  const el = $("issPanel");
  if (!el || panelIsCollapsed("issPanel") || !el.classList.contains("panel-float")) return;
  const prev = el.offsetHeight;
  const prevOverflow = el.style.overflow;
  el.style.overflow = "visible";
  el.style.height = "auto";
  const content = el.scrollHeight;
  const room = Math.round(window.innerHeight - el.getBoundingClientRect().top - 16);
  const next = Math.max(panelMinH(el), Math.min(content, Math.max(panelMinH(el), room)));
  el.style.overflow = prevOverflow;
  el.style.height = `${Math.round(next)}px`;
  if (Math.abs(next - prev) <= 2) return;
  sizePanel(el, null, next);
  savePanelLayout(el);
}
window.fitIssHeight = fitIssHeight;

function restoreIssBox(saved) {
  if (!saved || typeof saved !== "object" || panelIsCollapsed("issPanel")) return saved;
  const iss = saved.issPanel;
  if (!iss || typeof iss !== "object") return saved;
  const vfo = saved.vfoPanel;
  const tuner = saved.tunerPanel;
  const col = vfo && Number(vfo.w) > 0 ? vfo : tuner;
  const x = col && Number.isFinite(Number(col.x)) ? Math.round(Number(col.x)) : PANEL_GAP;
  const w = col && Number(col.w) > 0 ? Math.round(Number(col.w)) : 560;
  const y = vfo && Number(vfo.h) > 0 && Number.isFinite(Number(vfo.y))
    ? Math.round(Number(vfo.y) + Number(vfo.h) + PANEL_GAP)
    : PANEL_GAP;
  const h = 280;
  Object.assign(iss, { x, y, w, h, floated: true, resized: true, resizedH: true });
  const scale = saved.__scale;
  const win = scale && scale.window;
  const now = windowBox();
  const sx = win && win.w ? now.w / win.w : 1;
  const sy = win && win.h ? now.h / win.h : 1;
  if (scale && typeof scale === "object") {
    if (scale.base && sx && sy) scale.base.issPanel = { x: x / sx, y: y / sy, w: w / sx, h: h / sy };
    if (scale.snapshot) scale.snapshot.issPanel = { x, y, w, h };
  }
  return saved;
}

function shrinkAlignedBoard(saved) {
  if (!saved || typeof saved !== "object") return saved;
  const gap = PANEL_GAP;
  const tuner = { x: gap, y: gap, w: 505, h: 385 };
  const vfo = { x: gap, y: tuner.y + tuner.h + gap, w: 505, h: 250 };
  const rowY = vfo.y + vfo.h + gap;
  const rowH = 196;
  const iss = { x: gap, y: rowY, w: 505, h: rowH };
  const pota = { x: tuner.x + tuner.w + gap, y: gap, w: 560, h: vfo.y + vfo.h - gap };
  const fav = { x: pota.x, y: rowY, w: 560, h: rowH };
  const blank = { x: pota.x + pota.w + gap, y: gap, w: 456, h: pota.h };
  const voacap = { x: blank.x, y: rowY, w: 456, h: rowH };
  const orig = {
    tunerPanel: tuner,
    vfoPanel: vfo,
    issPanel: iss,
    potaPanel: pota,
    favPanel: fav,
    blankPanel: blank,
    voacapPanel: voacap,
  };
  for (const [id, box] of Object.entries(orig)) {
    const prev = saved[id] && typeof saved[id] === "object" ? saved[id] : {};
    saved[id] = { ...prev, ...box, floated: true, resized: true, resizedH: true };
  }
  const boxes = {};
  Object.keys(orig).forEach((id) => {
    const p = saved[id];
    boxes[id] = { x: p.x, y: p.y, w: p.w, h: p.h };
  });
  const clock = saved.clockPanel;
  if (clock && typeof clock === "object") {
    boxes.clockPanel = {
      x: Number(clock.x) || 0,
      y: Number(clock.y) || 0,
      w: Number(clock.w) || 249,
      h: Number(clock.h) || 47,
    };
  }
  saved.__scale = {
    on: true,
    window: windowBox(),
    base: JSON.parse(JSON.stringify(boxes)),
    snapshot: JSON.parse(JSON.stringify(boxes)),
  };
  return saved;
}

function restoreBoard(saved) {
  if (!saved || typeof saved !== "object") return saved;
  const orig = {
    tunerPanel: { x: 8, y: 8, w: 505, h: 385 },
    vfoPanel: { x: 8, y: 402, w: 505, h: 250 },
    issPanel: { x: 8, y: 660, w: 505, h: 196 },
    potaPanel: { x: 521, y: 8, w: 560, h: 643 },
    favPanel: { x: 521, y: 659, w: 560, h: 197 },
    blankPanel: { x: 1089, y: 8, w: 456, h: 494 },
    voacapPanel: { x: 1089, y: 510, w: 456, h: 350 },
  };
  const availW = Math.max(1100, (window.innerWidth || 1600) - 16);
  const availH = Math.max(640, (window.innerHeight || 900) - 78);
  const sx = availW / 1553;
  const sy = availH / 864;
  for (const [id, box] of Object.entries(orig)) {
    const prev = saved[id] && typeof saved[id] === "object" ? saved[id] : {};
    const x = Math.round(8 + (box.x - 8) * sx);
    const y = Math.round(8 + (box.y - 8) * sy);
    const w = Math.round(box.w * sx);
    const h = Math.round(box.h * sy);
    saved[id] = { ...prev, x, y, w, h, floated: true, resized: true, resizedH: true };
  }
  const boxes = {};
  Object.keys(orig).forEach((id) => {
    const p = saved[id];
    boxes[id] = { x: p.x, y: p.y, w: p.w, h: p.h };
  });
  const clock = saved.clockPanel;
  if (clock && typeof clock === "object") {
    boxes.clockPanel = {
      x: Number(clock.x) || 0,
      y: Number(clock.y) || 0,
      w: Number(clock.w) || 249,
      h: Number(clock.h) || 47,
    };
  }
  saved.__scale = {
    on: true,
    window: windowBox(),
    base: JSON.parse(JSON.stringify(boxes)),
    snapshot: JSON.parse(JSON.stringify(boxes)),
  };
  return saved;
}

function spacePanelsEvenly(saved) {
  if (!saved || typeof saved !== "object") return saved;
  const boxes = Object.keys(saved).filter((id) => {
    if (id.startsWith("__") || id === "clockPanel" || panelIsCollapsed(id) || !document.getElementById(id)) return false;
    const pos = saved[id];
    if (!pos || pos.floated === false || !Number.isFinite(Number(pos.x)) || !Number.isFinite(Number(pos.y))) return false;
    if (!(Number(pos.w) > 0)) pos.w = 456;
    if (!(Number(pos.h) > 0)) pos.h = 188;
    return true;
  }).map((id) => ({
    id,
    x: Number(saved[id].x),
    y: Number(saved[id].y),
    w: Number(saved[id].w),
    h: Number(saved[id].h),
  }));
  if (boxes.length < 2) return saved;
  boxes.sort((a, b) => a.x - b.x || a.y - b.y);
  const columns = [];
  for (const tile of boxes) {
    const col = columns.find((group) => {
      const left = Math.min(...group.map((item) => item.x));
      const right = Math.max(...group.map((item) => item.x + item.w));
      const overlap = Math.min(tile.x + tile.w, right) - Math.max(tile.x, left);
      return overlap > Math.min(tile.w, right - left) * 0.45;
    });
    if (col) col.push(tile);
    else columns.push([tile]);
  }
  columns.sort((a, b) => Math.min(...a.map((item) => item.x)) - Math.min(...b.map((item) => item.x)));
  const placed = {};
  let x = PANEL_GAP;
  for (const col of columns) {
    const w = Math.round(Math.max(...col.map((item) => item.w)));
    col.sort((a, b) => a.y - b.y);
    let y = PANEL_GAP;
    for (const tile of col) {
      placed[tile.id] = { x, y, w, h: Math.round(tile.h) };
      y += Math.round(tile.h) + PANEL_GAP;
    }
    x += w + PANEL_GAP;
  }
  Object.keys(placed).forEach((id) => {
    Object.assign(saved[id], placed[id], { floated: true, resized: true, resizedH: true });
  });
  const scale = saved.__scale;
  if (scale && typeof scale === "object") {
    for (const bucket of [scale.base, scale.snapshot]) {
      if (!bucket || typeof bucket !== "object") continue;
      Object.keys(placed).forEach((id) => {
        if (bucket[id] && typeof bucket[id] === "object") Object.assign(bucket[id], placed[id]);
      });
    }
  }
  return saved;
}

function applyEvenGapsOnDesk() {
  const desk = $("desk");
  if (!desk) return;
  const boxes = [];
  desk.querySelectorAll(".panel.panel-float").forEach((el) => {
    if (!el.id || isClockPanel(el) || panelIsCollapsed(el.id) || el.classList.contains("dragging")) return;
    if (el.offsetWidth < 2 || el.offsetHeight < 2) return;
    boxes.push({
      el,
      x: parseFloat(el.style.left) || 0,
      y: parseFloat(el.style.top) || 0,
      w: el.offsetWidth,
      h: el.offsetHeight,
    });
  });
  if (boxes.length < 2) return;
  boxes.sort((a, b) => a.x - b.x || a.y - b.y);
  const columns = [];
  for (const tile of boxes) {
    const col = columns.find((group) => {
      const left = Math.min(...group.map((item) => item.x));
      const right = Math.max(...group.map((item) => item.x + item.w));
      const overlap = Math.min(tile.x + tile.w, right) - Math.max(tile.x, left);
      return overlap > Math.min(tile.w, right - left) * 0.45;
    });
    if (col) col.push(tile);
    else columns.push([tile]);
  }
  columns.sort((a, b) => Math.min(...a.map((item) => item.x)) - Math.min(...b.map((item) => item.x)));
  const placed = [];
  let x = PANEL_GAP;
  for (const col of columns) {
    const w = Math.round(Math.max(...col.map((item) => item.w)));
    col.sort((a, b) => a.y - b.y);
    let y = PANEL_GAP;
    for (const tile of col) {
      placed.push({ el: tile.el, x, y, w, h: Math.round(tile.h) });
      y += Math.round(tile.h) + PANEL_GAP;
    }
    x += w + PANEL_GAP;
  }
  placed.forEach((tile) => {
    tile.el.style.left = `${tile.x}px`;
    tile.el.style.top = `${tile.y}px`;
    tile.el.style.width = `${tile.w}px`;
    tile.el.classList.add("resized");
  });
  const all = readPanelPos();
  placed.forEach((tile) => {
    const prev = all[tile.el.id] && typeof all[tile.el.id] === "object" ? all[tile.el.id] : {};
    all[tile.el.id] = {
      ...prev,
      x: tile.x,
      y: tile.y,
      w: tile.w,
      h: tile.h,
      floated: true,
      resized: true,
      resizedH: true,
    };
  });
  if (scaleModel && scaleModel.on) {
    const { sx, sy } = scaleFactors();
    if (sx && sy) {
      if (!scaleModel.base) scaleModel.base = {};
      placed.forEach((tile) => {
        scaleModel.base[tile.el.id] = { x: tile.x / sx, y: tile.y / sy, w: tile.w / sx, h: tile.h / sy };
      });
      if (scaleModel.snapshot) {
        placed.forEach((tile) => {
          scaleModel.snapshot[tile.el.id] = { x: tile.x, y: tile.y, w: tile.w, h: tile.h };
        });
      }
      all.__scale = scaleModel;
    }
  }
  persistPanelLayout(all);
  growDesk();
}

function dropPanelsForClock(saved) {
  if (!saved || typeof saved !== "object") return saved;
  const clock = saved.clockPanel;
  const clockH = clock && Number(clock.h) > 0 ? Number(clock.h) : 56;
  const shift = Math.round(clockH + PANEL_GAP);
  let top = Infinity;
  for (const [id, pos] of Object.entries(saved)) {
    if (!pos || typeof pos !== "object" || id.startsWith("__") || id === "clockPanel") continue;
    if (pos.floated === false || !Number.isFinite(Number(pos.y))) continue;
    pos.y = Math.round(Number(pos.y) + shift);
    top = Math.min(top, pos.y);
  }
  if (clock && typeof clock === "object" && Number.isFinite(top)) {
    clock.y = Math.round(top - shift);
    clock.floated = true;
  }
  return saved;
}

function raisePanels(saved, amount) {
  if (!saved || typeof saved !== "object") return saved;
  const lift = Math.max(0, Math.round(amount));
  for (const [id, pos] of Object.entries(saved)) {
    if (!pos || typeof pos !== "object" || id.startsWith("__") || id === "clockPanel") continue;
    if (pos.floated === false || !Number.isFinite(Number(pos.y))) continue;
    pos.y = Math.round(Number(pos.y) - lift);
  }
  return saved;
}

function writeLayoutBox(saved, id, box) {
  const scale = saved && saved.__scale;
  if (!scale || typeof scale !== "object") return;
  for (const bucket of [scale.base, scale.snapshot]) {
    if (!bucket || typeof bucket !== "object" || !bucket[id] || typeof bucket[id] !== "object") continue;
    Object.assign(bucket[id], box);
  }
}

function alignFavSpacing(saved) {
  if (!saved || typeof saved !== "object") return saved;
  const pota = saved.potaPanel;
  const fav = saved.favPanel;
  if (!pota || !fav || typeof pota !== "object" || typeof fav !== "object") return saved;
  const px = Number(pota.x);
  const py = Number(pota.y);
  const pw = Number(pota.w);
  const ph = Number(pota.h);
  if (!Number.isFinite(px) || !Number.isFinite(py) || !(pw > 0) || !(ph > 0)) return saved;
  let gap = PANEL_GAP;
  const leftEdge = ["vfoPanel", "tunerPanel", "issPanel", "modeBandPanel"]
    .map((id) => saved[id])
    .filter((pos) => pos && Number(pos.w) > 0 && Number(pos.x) + Number(pos.w) <= px + 2)
    .reduce((edge, pos) => Math.max(edge, Number(pos.x) + Number(pos.w)), Number.NEGATIVE_INFINITY);
  if (Number.isFinite(leftEdge)) {
    const side = Math.round(px - leftEdge);
    if (side >= 4 && side <= 24) gap = side;
  }
  const x = Math.round(px);
  const w = Math.round(pw);
  const y = Math.round(py + ph + gap);
  let h = Number(fav.h) > 0 ? Math.round(Number(fav.h)) : 196;
  const mates = ["issPanel", "voacapPanel"].map((id) => saved[id]).filter((pos) => {
    if (!pos || !(Number(pos.h) > 0) || !Number.isFinite(Number(pos.y))) return false;
    const top = Number(pos.y);
    const bottom = top + Number(pos.h);
    return bottom > y + 20 && top < y + h + gap;
  });
  if (mates.length) {
    const bottom = Math.max(...mates.map((pos) => Number(pos.y) + Number(pos.h)));
    h = Math.max(120, Math.round(bottom - y));
  }
  Object.assign(fav, { x, y, w, h, floated: true, resized: true, resizedH: true });
  writeLayoutBox(saved, "favPanel", { x, y, w, h });
  return saved;
}

function alignPanadapterSpacing(saved) {
  if (!saved || typeof saved !== "object") return saved;
  const ids = ["tunerPanel", "vfoPanel", "issPanel", "potaPanel", "favPanel", "blankPanel", "voacapPanel"];
  const boxes = ids
    .map((id) => saved[id])
    .filter((pos) => pos && Number(pos.w) > 0 && Number(pos.h) > 0 && Number.isFinite(Number(pos.x)) && Number.isFinite(Number(pos.y)) && Number(pos.y) >= 0);
  if (!boxes.length) return saved;
  const left = Math.min(...boxes.map((pos) => Number(pos.x)));
  const right = Math.max(...boxes.map((pos) => Number(pos.x) + Number(pos.w)));
  const bottom = Math.max(...boxes.map((pos) => Number(pos.y) + Number(pos.h)));
  const rowY = Math.max(...boxes.map((pos) => Number(pos.y)));
  const row = boxes.filter((pos) => Math.abs(Number(pos.y) - rowY) <= 12);
  const h = Math.max(160, Math.round(Math.max(...row.map((pos) => Number(pos.h)))));
  const box = {
    x: Math.round(left),
    y: Math.round(bottom + PANEL_GAP),
    w: Math.max(480, Math.round(right - left)),
    h,
  };
  saved.panadapterPanel = { ...(saved.panadapterPanel || {}), ...box, floated: true, resized: true, resizedH: true };
  const scale = saved.__scale;
  if (scale && typeof scale === "object") {
    for (const bucket of [scale.base, scale.snapshot]) {
      if (bucket && typeof bucket === "object") bucket.panadapterPanel = { ...box };
    }
  }
  return saved;
}

function placePanadapter(saved) {
  if (!saved || typeof saved !== "object") return saved;
  const prev = saved.panadapterPanel;
  if (prev && Number(prev.w) > 0 && Number(prev.h) > 0 && Number.isFinite(Number(prev.x)) && Number.isFinite(Number(prev.y))) {
    return saved;
  }
  let bottom = PANEL_GAP;
  let left = PANEL_GAP;
  let right = 640;
  for (const [id, pos] of Object.entries(saved)) {
    if (!pos || typeof pos !== "object" || id.startsWith("__") || id === "clockPanel" || id === "panadapterPanel") continue;
    if (!document.getElementById(id) || panelIsCollapsed(id)) continue;
    const x = Number(pos.x);
    const y = Number(pos.y);
    const w = Number(pos.w);
    const h = Number(pos.h);
    if (!(w > 0) || !(h > 0) || !Number.isFinite(x) || !Number.isFinite(y)) continue;
    bottom = Math.max(bottom, y + h);
    left = Math.min(left, x);
    right = Math.max(right, x + w);
  }
  const box = {
    x: Math.round(left),
    y: Math.round(bottom + PANEL_GAP),
    w: Math.max(640, Math.round(right - left)),
    h: 200,
  };
  saved.panadapterPanel = { ...box, floated: true, resized: true, resizedH: true };
  const scale = saved.__scale;
  if (scale && typeof scale === "object") {
    for (const bucket of [scale.base, scale.snapshot]) {
      if (bucket && typeof bucket === "object") bucket.panadapterPanel = { ...box };
    }
  }
  return saved;
}

function matchWeatherVoacapWidth(saved) {
  if (!saved || typeof saved !== "object") return saved;
  const weather = saved.blankPanel;
  const voacap = saved.voacapPanel;
  if (!weather || !voacap || typeof weather !== "object" || typeof voacap !== "object") return saved;
  const wx = Number(weather.x);
  const ww = Number(weather.w);
  const vx = Number(voacap.x);
  const vw = Number(voacap.w);
  if (!(ww > 0) && !(vw > 0)) return saved;
  const left = Math.min(Number.isFinite(wx) ? wx : vx, Number.isFinite(vx) ? vx : wx);
  const right = Math.max(
    (Number.isFinite(wx) ? wx : left) + (ww > 0 ? ww : 0),
    (Number.isFinite(vx) ? vx : left) + (vw > 0 ? vw : 0),
  );
  const w = Math.max(420, Math.round(right - left));
  const x = Math.round(left);
  weather.x = x;
  weather.w = w;
  weather.resized = true;
  voacap.x = x;
  voacap.w = w;
  voacap.resized = true;
  const scale = saved.__scale;
  if (scale && typeof scale === "object") {
    for (const bucket of [scale.base, scale.snapshot]) {
      if (!bucket || typeof bucket !== "object") continue;
      for (const id of ["blankPanel", "voacapPanel"]) {
        if (bucket[id] && typeof bucket[id] === "object") {
          bucket[id].x = x;
          bucket[id].w = w;
        }
      }
    }
  }
  return saved;
}

function matchWeatherWidth(saved) {
  if (!saved || typeof saved !== "object") return saved;
  const weather = saved.blankPanel;
  const width = weather && Number(weather.w);
  if (!(width > 0)) return saved;
  for (const id of ["tunerPanel", "vfoPanel", "modeBandPanel"]) {
    const pos = saved[id];
    if (!pos || typeof pos !== "object" || pos.floated === false) continue;
    pos.w = width;
    pos.resized = true;
  }
  const scale = saved.__scale;
  if (scale && typeof scale === "object") {
    for (const bucket of [scale.base, scale.snapshot]) {
      if (!bucket || typeof bucket !== "object") continue;
      for (const id of ["tunerPanel", "vfoPanel", "modeBandPanel"]) {
        if (bucket[id] && typeof bucket[id] === "object") bucket[id].w = width;
      }
    }
  }
  return saved;
}

function matchVoacapGap(saved) {
  if (!saved || typeof saved !== "object") return saved;
  const pota = saved.potaPanel;
  const voacap = saved.voacapPanel;
  const potaX = pota && Number(pota.x);
  const potaW = pota && Number(pota.w);
  const voacapX = voacap && Number(voacap.x);
  if (![potaX, potaW, voacapX].every(Number.isFinite)) return saved;
  const gap = voacapX - (potaX + potaW);
  const ids = ["tunerPanel", "vfoPanel", "modeBandPanel"];
  function place(pos) {
    if (!pos || typeof pos !== "object") return;
    const w = Number(pos.w);
    if (!Number.isFinite(w)) return;
    pos.x = Math.round(potaX - gap - w);
  }
  for (const id of ids) {
    const pos = saved[id];
    if (!pos || pos.floated === false) continue;
    place(pos);
  }
  const scale = saved.__scale;
  if (scale && typeof scale === "object") {
    for (const bucket of [scale.base, scale.snapshot]) {
      if (!bucket || typeof bucket !== "object") continue;
      for (const id of ids) place(bucket[id]);
    }
  }
  return saved;
}

function matchVoacapSideMargin(saved) {
  if (!saved || typeof saved !== "object") return saved;
  const desk = $("desk");
  const voacap = saved.voacapPanel;
  if (!desk || !voacap) return saved;
  const vx = Number(voacap.x);
  const vw = Number(voacap.w);
  if (!Number.isFinite(vx) || !(vw > 0)) return saved;
  const viewW = window.innerWidth || document.documentElement.clientWidth;
  const deskLeft = desk.getBoundingClientRect().left;
  const rightGap = viewW - (deskLeft + vx + vw);
  const x = Math.max(0, Math.round(rightGap - deskLeft));
  const ids = ["tunerPanel", "vfoPanel", "modeBandPanel"];
  for (const id of ids) {
    const pos = saved[id];
    if (!pos || typeof pos !== "object" || pos.floated === false) continue;
    pos.x = x;
  }
  const scale = saved.__scale;
  let storedX = x;
  if (scale && scale.on && scale.window && Number(scale.window.w) > 0) {
    const sx = (window.innerWidth || viewW) / Number(scale.window.w);
    if (sx > 0) storedX = x / sx;
  }
  if (scale && typeof scale === "object") {
    for (const bucket of [scale.base, scale.snapshot]) {
      if (!bucket || typeof bucket !== "object") continue;
      for (const id of ids) {
        if (bucket[id] && typeof bucket[id] === "object") bucket[id].x = storedX;
      }
    }
  }
  return saved;
}

function closePotaGap(saved) {
  if (!saved || typeof saved !== "object") return saved;
  const left = saved.tunerPanel;
  const pota = saved.potaPanel;
  const voacap = saved.voacapPanel;
  const leftX = left && Number(left.x);
  const leftW = left && Number(left.w);
  const potaX = pota && Number(pota.x);
  const potaW = pota && Number(pota.w);
  const voacapX = voacap && Number(voacap.x);
  if (![leftX, leftW, potaX, potaW, voacapX].every(Number.isFinite)) return saved;
  const gap = Math.max(0, Math.round(voacapX - (potaX + potaW)));
  const x = Math.round(leftX + leftW + gap);
  const w = Math.round(voacapX - gap - x);
  if (!(w > 0)) return saved;
  const ids = ["potaPanel", "favPanel"];
  for (const id of ids) {
    const pos = saved[id];
    if (!pos || typeof pos !== "object" || pos.floated === false) continue;
    pos.x = x;
    pos.w = w;
    pos.resized = true;
  }
  const scale = saved.__scale;
  let storedX = x;
  let storedW = w;
  if (scale && scale.on && scale.window && Number(scale.window.w) > 0) {
    const sx = (window.innerWidth || 1) / Number(scale.window.w);
    if (sx > 0) {
      storedX = x / sx;
      storedW = w / sx;
    }
  }
  if (scale && typeof scale === "object") {
    for (const bucket of [scale.base, scale.snapshot]) {
      if (!bucket || typeof bucket !== "object") continue;
      for (const id of ids) {
        if (bucket[id] && typeof bucket[id] === "object") {
          bucket[id].x = storedX;
          bucket[id].w = storedW;
        }
      }
    }
  }
  return saved;
}

function alignOpsPanelTops(all) {
  if (!all || typeof all !== "object") return;
  const ys = [];
  for (const id of OPS_TOP_IDS) {
    const pos = all[id];
    const y = pos && Number(pos.y);
    if (pos && pos.floated !== false && Number.isFinite(y)) ys.push(y);
  }
  if (!ys.length) return;
  const y = Math.min(...ys);
  for (const id of OPS_TOP_IDS) {
    const pos = all[id];
    if (pos && pos.floated !== false && Number.isFinite(Number(pos.y))) pos.y = y;
  }
}

function growDesk() {
  const desk = $("desk");
  if (!desk) return;
  let bottom = 0;
  desk.querySelectorAll(".panel-float").forEach((el) => {
    bottom = Math.max(bottom, el.offsetTop + el.offsetHeight);
  });
  const next = `${Math.max(80, bottom + 24)}px`;
  if (desk.style.minHeight !== next) desk.style.minHeight = next;
}

function keepPanelsOffVfo() {
  const vfo = $("vfoPanel");
  const desk = $("desk");
  if (!vfo || !desk) return;
  const origin = deskOrigin();
  const box = vfo.getBoundingClientRect();
  const vfoBottom = box.bottom - origin.y + origin.st + 8;
  const vfoRight = box.right - origin.x + origin.sl;
  desk.querySelectorAll(".panel-float").forEach((el) => {
    if (el.id === "vfoPanel" || el.id === "potaPanel" || el.id === "blankPanel" || isClockPanel(el) || el.id === "favPanel" || el.id === "modeBandPanel" || el.id === "tunerPanel" || el.id === "voacapPanel" || el.id === "issPanel") return;
    const left = parseFloat(el.style.left) || 0;
    const top = parseFloat(el.style.top) || 0;
    if (left < vfoRight && top < vfoBottom) el.style.top = `${Math.round(vfoBottom)}px`;
  });
}

function parkModeBandUnderVfo() {
  const el = $("modeBandPanel");
  const vfo = $("vfoPanel");
  const desk = $("desk");
  if (!el || !vfo || !desk) return;
  const origin = deskOrigin();
  const box = vfo.getBoundingClientRect();
  const x = Math.max(0, box.left - origin.x + origin.sl);
  const y = Math.max(0, box.bottom - origin.y + origin.st + 8);
  const w = Math.max(el.offsetWidth || 280, box.width);
  floatPanel(el, x, y, w, null);
  el.style.zIndex = String(++panelZ);
  savePanelLayout(el);
}

function ensureModeBandVisible() {
  const el = $("modeBandPanel");
  const pota = $("potaPanel");
  const vfo = $("vfoPanel");
  if (!el || !vfo || document.body.classList.contains("mode-band-collapsed")) return;
  const floatsCoverDesk = vfo.classList.contains("panel-float") || (pota && pota.classList.contains("panel-float"));
  if (!el.classList.contains("panel-float") && floatsCoverDesk) parkModeBandUnderVfo();
}

function fitPotaRowFont() {
  const panel = $("potaPanel");
  if (!panel) return;
  panel.style.setProperty("--pota-row", "11px");
}

function layoutPotaForWindow() {
  fitPotaRowFont();
}

function syncWeatherVoacapWidth(el) {
  if (!el || (el.id !== "blankPanel" && el.id !== "voacapPanel")) return null;
  const other = document.getElementById(el.id === "blankPanel" ? "voacapPanel" : "blankPanel");
  if (!other || other.classList.contains("dragging")) return null;
  const x = parseFloat(el.style.left);
  if (Number.isFinite(x)) other.style.left = `${Math.round(x)}px`;
  sizePanel(other, el.offsetWidth, null);
  if (other.id === "blankPanel") resizeRadarMap();
  return other;
}

function panelPosFromEl(el) {
  const floated = el.classList.contains("panel-float");
  return {
    x: floated ? (Number.isFinite(parseFloat(el.style.left)) ? parseFloat(el.style.left) : 0) : null,
    y: floated ? (Number.isFinite(parseFloat(el.style.top)) ? parseFloat(el.style.top) : 0) : null,
    w: el.classList.contains("resized") ? el.offsetWidth : null,
    h: el.classList.contains("resized-h") ? el.offsetHeight : null,
    floated,
    resized: el.classList.contains("resized"),
    resizedH: el.classList.contains("resized-h"),
  };
}

function savePanelLayout(el) {
  if (scaleModel && scaleModel.on && !scalingPanels && el) {
    rebasePanel(el);
    const paired = (el.id === "blankPanel" || el.id === "voacapPanel")
      ? document.getElementById(el.id === "blankPanel" ? "voacapPanel" : "blankPanel")
      : null;
    if (paired && !paired.classList.contains("dragging")) rebasePanel(paired);
  }
  const all = readPanelPos();
  all[el.id] = panelPosFromEl(el);
  const paired = (el.id === "blankPanel" || el.id === "voacapPanel")
    ? document.getElementById(el.id === "blankPanel" ? "voacapPanel" : "blankPanel")
    : null;
  if (paired && !paired.classList.contains("dragging")) all[paired.id] = panelPosFromEl(paired);
  if (scaleModel) all.__scale = scaleModel;
  persistPanelLayout(all);
  growDesk();
}

function resetPanel(el) {
  if (el.id === "clockPanel") {
    const pos = { x: 280, y: 6, w: 280, h: 56, floated: true, resized: true, resizedH: true };
    applySavedPanel(el, pos);
    const all = readPanelPos();
    all.clockPanel = pos;
    persistPanelLayout(all);
    fitUtcClock();
    return;
  }
  el.classList.remove("panel-float", "dragging", "resized", "resized-h");
  el.style.left = "";
  el.style.top = "";
  el.style.width = "";
  el.style.height = "";
  el.style.zIndex = "";
  const all = readPanelPos();
  delete all[el.id];
  persistPanelLayout(all);
  growDesk();
  drawSAnalog();
  layoutPotaForWindow();
  fitUtcClock();
  if (el.id === "modeBandPanel") parkModeBandUnderVfo();
  if (el.id === "tunerPanel") parkTunerIfNew();
  if (el.id === "voacapPanel") parkVoacapIfNew();
  if (el.id === "issPanel") parkIssIfNew();
}

function applySavedPanel(el, pos) {
  if (!pos) return;
  const lockW = !!pos.resized && Number.isFinite(Number(pos.w));
  const lockH = !!pos.resizedH && Number.isFinite(Number(pos.h));
  const x = Number(pos.x);
  const y = Number(pos.y);
  const hasXY = pos.x != null && pos.y != null && Number.isFinite(x) && Number.isFinite(y);
  if (hasXY && pos.floated !== false) {
    const lockPanelH = lockH;
    floatPanel(el, x, y, lockW ? Number(pos.w) : null, lockPanelH ? Number(pos.h) : null);
    if (isClockPanel(el)) el.style.zIndex = "80";
    return;
  }
  el.classList.remove("panel-float", "resized", "resized-h");
  el.style.left = "";
  el.style.top = "";
  el.style.width = "";
  el.style.height = "";
  el.style.zIndex = "";
  if (isClockPanel(el)) fitUtcClock();
  if (el.id === "modeBandPanel") parkModeBandUnderVfo();
  if (el.id === "tunerPanel") parkTunerIfNew();
  if (el.id === "voacapPanel") parkVoacapIfNew();
  if (el.id === "issPanel") parkIssIfNew();
  if (el.id === "panadapterPanel") parkPanadapterIfNew();
}

function parkIssIfNew() {
  const el = $("issPanel");
  const desk = $("desk");
  if (!el || !desk || document.body.classList.contains("iss-collapsed")) return;
  if (el.classList.contains("panel-float")) return;
  const w = 560;
  const h = 280;
  const x = 12;
  const y = 12;
  floatPanel(el, x, y, w, h);
  savePanelLayout(el);
}

function keepIssOnScreen() {
}

function parkVoacapIfNew() {
  const el = $("voacapPanel");
  const vfo = $("vfoPanel");
  const desk = $("desk");
  if (!el || !desk || el.classList.contains("panel-float")) return;
  const origin = deskOrigin();
  let x = 0;
  let y = 6;
  let h = null;
  if (vfo) {
    const box = vfo.getBoundingClientRect();
    y = Math.max(0, box.top - origin.y + origin.st);
    x = Math.max(0, box.right - origin.x + origin.sl + 8);
    h = vfo.offsetHeight || null;
  }
  floatPanel(el, x, y, 640, h);
  savePanelLayout(el);
}

function revealModeBand(saved) {
  if (!saved || typeof saved !== "object") return saved;
  const vfo = saved.vfoPanel;
  const mode = saved.modeBandPanel;
  if (!mode || typeof mode !== "object") return saved;
  const x = vfo && Number.isFinite(Number(vfo.x)) ? Number(vfo.x) : 7;
  const w = vfo && Number(vfo.w) > 0 ? Number(vfo.w) : 456;
  const vfoBottom = vfo && Number.isFinite(Number(vfo.y)) && Number(vfo.h) > 0
    ? Number(vfo.y) + Number(vfo.h)
    : Number(mode.y) || 0;
  const y = Math.round(vfoBottom + 8);
  const h = Number(mode.h) > 0 ? Number(mode.h) : 188;
  mode.x = x;
  mode.y = y;
  mode.w = w;
  mode.h = h;
  mode.floated = true;
  mode.resized = true;
  mode.resizedH = true;
  const modeBottom = y + h;
  const iss = saved.issPanel;
  if (iss && typeof iss === "object" && iss.floated !== false) {
    const issX = Number(iss.x);
    const issW = Number(iss.w) > 0 ? Number(iss.w) : 0;
    const issY = Number(iss.y);
    const issH = Number(iss.h) > 0 ? Number(iss.h) : 0;
    const overlapsX = Number.isFinite(issX) && issX < x + w && issX + issW > x;
    const overlapsY = Number.isFinite(issY) && issY < modeBottom && issY + issH > y;
    if (overlapsX && overlapsY) iss.y = Math.round(modeBottom + 8);
  }
  const scale = saved.__scale;
  if (scale && typeof scale === "object") {
    for (const bucket of [scale.base, scale.snapshot]) {
      if (!bucket || typeof bucket !== "object") continue;
      if (bucket.modeBandPanel && typeof bucket.modeBandPanel === "object") {
        bucket.modeBandPanel.x = mode.x;
        bucket.modeBandPanel.y = mode.y;
        bucket.modeBandPanel.w = mode.w;
        bucket.modeBandPanel.h = mode.h;
      }
      if (iss && bucket.issPanel && typeof bucket.issPanel === "object") {
        bucket.issPanel.y = iss.y;
      }
    }
  }
  return saved;
}

function placeFavAndModeBelowPota(saved) {
  if (!saved || typeof saved !== "object") return saved;
  if (saved.__sidePanels && saved.__sidePanels.belowPota) return saved;
  const pota = saved.potaPanel;
  const mode = saved.modeBandPanel;
  const fav = saved.favPanel;
  if (!pota || pota.x == null || pota.y == null || !(Number(pota.h) > 0)) return saved;
  const gap = 8;
  const x = Number(pota.x);
  const y = Number(pota.y) + Number(pota.h) + gap;
  if (mode && typeof mode === "object") {
    mode.x = x;
    mode.y = y;
    mode.floated = true;
  }
  const modeH = mode && Number(mode.h) > 0 ? Number(mode.h) : 204;
  if (fav && typeof fav === "object") {
    fav.x = x;
    fav.y = y + modeH + gap;
    fav.floated = true;
  }
  saved.__sidePanels = { belowPota: true };
  return saved;
}

function parkTunerIfNew() {
  const el = $("tunerPanel");
  const vfo = $("vfoPanel");
  const desk = $("desk");
  if (!el || !vfo || !desk || el.classList.contains("panel-float")) return;
  const origin = deskOrigin();
  const box = vfo.getBoundingClientRect();
  const x = Math.max(0, box.left - origin.x + origin.sl);
  const y = Math.max(0, box.bottom - origin.y + origin.st + 8);
  floatPanel(el, x, y, 440, 520);
  savePanelLayout(el);
}

async function enablePanelDrag() {
  const desk = $("desk");
  if (!desk) return;
  let saved = readPanelPos();
  try {
    const data = await jsonFetch("/api/layout");
    mergeLookFromRemote(data);
    const remote = data.panel_pos || {};
    if (remote && typeof remote === "object" && Object.keys(remote).length) {
      saved = { ...remote, ...saved };
    }
  } catch (_) { /* keep local */ }
  const layoutGen = layoutGenOf(saved);
  const alignClock = layoutGen < 8;
  if (layoutGen < 4) saved = defaultPanelLayout();
  else if (layoutGen < 5) alignOpsPanelTops(saved);
  saved = migrateClockPanel(saved);
  saved = placeFavAndModeBelowPota(saved);
  if (layoutGen >= 4 && layoutGen < 6) saved = evenPanelGaps(saved);
  if (layoutGen >= 4 && layoutGen < 7) saved = dropPanelsForClock(saved);
  if (layoutGen >= 7 && layoutGen < 9) saved = raisePanels(saved, 32);
  if (layoutGen >= 9 && layoutGen < 10) saved = raisePanels(saved, 16);
  if (layoutGen >= 10 && layoutGen < 11) saved = matchWeatherWidth(saved);
  if (layoutGen >= 11 && layoutGen < 12) saved = matchVoacapGap(saved);
  if (layoutGen >= 12 && layoutGen < 14) saved = matchVoacapSideMargin(saved);
  if (layoutGen >= 14 && layoutGen < 15) saved = closePotaGap(saved);
  if (layoutGen >= 17 && layoutGen < 18) {
    saved = revealModeBand(saved);
    setModeBandCollapsed(false);
  }
  if (layoutGen < 21) saved = spacePanelsEvenly(saved);
  if (layoutGen < 22) saved = restoreIssBox(saved);
  if (layoutGen < 25) saved = restoreBoard(saved);
  if (layoutGen < 27) saved = shrinkAlignedBoard(saved);
  if (layoutGen < 28) saved = matchWeatherVoacapWidth(saved);
  if (layoutGen < 29) saved = placePanadapter(saved);
  if (layoutGen < 30) saved = alignFavSpacing(saved);
  if (layoutGen < 31) saved = alignPanadapterSpacing(saved);
  if (layoutGen < 17 && saved.__scale && saved.__scale.snapshot) {
    Object.keys(saved.__scale.snapshot).forEach((id) => {
      const box = saved.__scale.snapshot[id];
      if (!box || typeof box !== "object") return;
      const prev = saved[id] && typeof saved[id] === "object" ? saved[id] : {};
      saved[id] = {
        ...prev,
        x: box.x,
        y: box.y,
        w: box.w,
        h: box.h,
        floated: true,
        resized: true,
        resizedH: true,
      };
    });
    delete saved.issPanel;
  }
  persistPanelLayout(saved);
  desk.querySelectorAll(".panel").forEach(wirePanel);
  function wirePanel(el) {
    if (!el.id || el.dataset.panelWired) return;
    el.dataset.panelWired = "1";
    el.addEventListener("pointerdown", (ev) => {
      if (ev.target instanceof Element && ev.target.closest("input, button, select, textarea, .vfo-af")) return;
      selectPanel(el);
    });
    const grip = document.createElement("div");
    grip.className = "panel-drag";
    grip.title = "Drag to move. Double-click to restore.";
    const handle = document.createElement("div");
    handle.className = "panel-resize";
    handle.title = "Drag to resize";
    el.prepend(grip);
    el.append(handle);
    function wireSideResize(side) {
      const west = side === "w";
      const bar = document.createElement("div");
      bar.className = west ? "panel-resize-w" : "panel-resize-e";
      bar.title = west ? "Drag to resize from the left" : "Drag to resize from the right";
      if (west) el.prepend(bar);
      else el.append(bar);
      bar.addEventListener("pointerdown", (ev) => {
        if ((panelsLocked() && !isClockPanel(el)) || ev.button !== 0) return;
        ev.preventDefault();
        ev.stopPropagation();
        pinPanelFloat(el);
        const startX = ev.clientX;
        const startLeft = parseFloat(el.style.left) || 0;
        const startW = el.offsetWidth;
        const right = startLeft + startW;
        el.classList.add("dragging");
        const move = (mv) => {
          if (west) {
            const minLeft = 0;
            let nextLeft = Math.max(minLeft, startLeft + (mv.clientX - startX));
            let nextW = right - nextLeft;
            if (nextW < panelMinW(el)) {
              nextW = panelMinW(el);
              nextLeft = Math.max(minLeft, right - nextW);
            }
            el.style.left = `${nextLeft}px`;
            el.style.width = `${nextW}px`;
          } else {
            el.style.width = `${Math.max(panelMinW(el), startW + (mv.clientX - startX))}px`;
          }
          el.classList.add("resized");
          if (isClockPanel(el)) fitUtcClock();
          growDesk();
          if (focusedPanel === el) armPanelFocusTimeout();
          if (el.id === "potaPanel") layoutPotaForWindow();
          if (el.id === "blankPanel") {
            pauseRadarTiles();
            resizeRadarMap();
          }
          syncWeatherVoacapWidth(el);
        };
        const up = () => {
          el.classList.remove("dragging");
          window.removeEventListener("pointermove", move);
          window.removeEventListener("pointerup", up);
          window.removeEventListener("pointercancel", up);
          if (el.id === "potaPanel") layoutPotaForWindow();
          if (el.id === "blankPanel") {
            resumeRadarTiles();
            resizeRadarMap();
          }
          syncWeatherVoacapWidth(el);
          savePanelLayout(el);
        };
        window.addEventListener("pointermove", move);
        window.addEventListener("pointerup", up);
        window.addEventListener("pointercancel", up);
      });
    }
    if (el.id === "potaPanel") wireSideResize("w");
    if (el.id === "blankPanel" || el.id === "voacapPanel" || el.id === "clockPanel" || el.id === "issPanel" || el.id === "panadapterPanel") {
      wireSideResize("w");
      wireSideResize("e");
    }
    if (isClockPanel(el) || el.id === "voacapPanel" || el.id === "issPanel" || el.id === "panadapterPanel") {
      const south = document.createElement("div");
      south.className = "panel-resize-s";
      south.title = isClockPanel(el) ? "Drag to change clock height" : "Drag to resize";
      el.append(south);
      south.addEventListener("pointerdown", (ev) => {
        if ((panelsLocked() && !isClockPanel(el)) || ev.button !== 0) return;
        ev.preventDefault();
        ev.stopPropagation();
        pinPanelFloat(el);
        const startY = ev.clientY;
        const startH = el.offsetHeight;
        sizePanel(el, el.offsetWidth, startH);
        el.classList.add("dragging");
        const move = (mv) => {
          sizePanel(el, null, startH + (mv.clientY - startY));
          growDesk();
        };
        const up = () => {
          el.classList.remove("dragging");
          window.removeEventListener("pointermove", move);
          window.removeEventListener("pointerup", up);
          window.removeEventListener("pointercancel", up);
          savePanelLayout(el);
        };
        window.addEventListener("pointermove", move);
        window.addEventListener("pointerup", up);
        window.addEventListener("pointercancel", up);
      });
    }
    applySavedPanel(el, saved[el.id]);
    grip.addEventListener("dblclick", (ev) => {
      ev.preventDefault();
      if (panelsLocked()) return;
      resetPanel(el);
    });
    function startMove(ev) {
      if ((panelsLocked() && !isClockPanel(el)) || ev.button !== 0) return;
      if (ev.target instanceof Element && ev.target.closest(".panel-resize, .panel-resize-s, .panel-resize-w, .panel-resize-e")) return;
      ev.preventDefault();
      ev.stopPropagation();
      pinPanelFloat(el);
      selectPanel(el);
      const startX = ev.clientX;
      const startY = ev.clientY;
      const origLeft = parseFloat(el.style.left) || 0;
      const origTop = parseFloat(el.style.top) || 0;
      el.classList.add("dragging");
      if (focusedPanel === el) armPanelFocusTimeout();
      const move = (mv) => {
        const nx = origLeft + (mv.clientX - startX);
        const ny = origTop + (mv.clientY - startY);
        if (isClockPanel(el)) {
          el.style.left = `${nx}px`;
          el.style.top = `${ny}px`;
        } else {
          el.style.left = `${Math.max(0, nx)}px`;
          el.style.top = `${Math.max(0, ny)}px`;
        }
        growDesk();
        if (focusedPanel === el) armPanelFocusTimeout();
        if (el.id === "tunerPanel") alignTitleToTuner();
        if (isClockPanel(el)) placeHeaderCall();
        if (el.id === "potaPanel") layoutPotaForWindow();
        if (el.id === "blankPanel") pauseRadarTiles();
        if (el.id === "vfoPanel") drawSAnalog();
      };
      const up = (mv) => {
        el.classList.remove("dragging");
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
        window.removeEventListener("pointercancel", up);
        if (el.id === "potaPanel") layoutPotaForWindow();
        if (el.id === "blankPanel") {
          resumeRadarTiles();
          resizeRadarMap();
        }
        if (isClockPanel(el)) unpinClock();
        savePanelLayout(el);
        drawSAnalog();
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
      window.addEventListener("pointercancel", up);
    }
    grip.addEventListener("pointerdown", startMove);
    if (isClockPanel(el)) el.addEventListener("pointerdown", startMove);
    if (el.id === "issPanel" || el.id === "panadapterPanel") {
      const bar = el.querySelector(el.id === "issPanel" ? ".iss-bar" : ".pan-bar");
      if (bar) {
        bar.addEventListener("pointerdown", (ev) => {
          if (ev.target instanceof Element && ev.target.closest("input, button, select, textarea")) return;
          startMove(ev);
        });
      }
    }
    handle.addEventListener("pointerdown", (ev) => {
      if ((panelsLocked() && !isClockPanel(el)) || ev.button !== 0) return;
      ev.preventDefault();
      ev.stopPropagation();
      pinPanelFloat(el);
      const startX = ev.clientX;
      const startY = ev.clientY;
      const startW = el.offsetWidth;
      const startH = el.offsetHeight;
      sizePanel(el, startW, startH);
      el.classList.add("dragging");
      const move = (mv) => {
        const nextW = startW + (mv.clientX - startX);
        const nextH = startH + (mv.clientY - startY);
        sizePanel(el, nextW, nextH);
        growDesk();
        drawSAnalog();
        if (el.id === "vfoPanel" || el.id === "blankPanel") resizeRadarMap();
        if (el.id === "panadapterPanel") paintPanadapter();
        syncWeatherVoacapWidth(el);
      };
      const up = () => {
        el.classList.remove("dragging");
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
        window.removeEventListener("pointercancel", up);
        syncWeatherVoacapWidth(el);
        savePanelLayout(el);
        if (el.id === "potaPanel") layoutPotaForWindow();
        if (el.id === "vfoPanel" || el.id === "blankPanel") resizeRadarMap();
        if (el.id === "panadapterPanel") paintPanadapter();
        drawSAnalog();
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
      window.addEventListener("pointercancel", up);
    });
  }
  growDesk();
  layoutPotaForWindow();
  fitVfoMeterHeight();
  keepVfoAboveTuner();
  keepClockAbovePota();
  keepModeBandAbovePota();
  keepPanelsOffVfo();
  ensureModeBandVisible();
  parkTunerIfNew();
  parkVoacapIfNew();
  parkIssIfNew();
  parkPanadapterIfNew();
  keepIssOnScreen();
  alignTitleToTuner();
  resizeRadarMap();
  fitUtcClock();
  requestAnimationFrame(() => {
    paintPanadapter();
    const pan = $("panadapterPanel");
    if (pan && layoutGen < 29 && !document.body.classList.contains("pan-collapsed")) {
      pan.scrollIntoView({ block: "nearest", inline: "nearest" });
    }
  });
  if (!saved.__scale) {
    captureScale(saved);
    persistPanelLayout(saved);
  }
  scaleModel = saved.__scale && saved.__scale.on ? saved.__scale : null;
  paintScaleToggle();
  if (alignClock) {
    clockPinned = true;
    const all = readPanelPos();
    all.__clock = { pinned: true };
    writePanelPos(all);
    requestAnimationFrame(() => alignClockToPota());
  } else {
    clockPinned = readPanelPos().__clock?.pinned !== false;
    if (clockPinned) requestAnimationFrame(() => alignClockToPota());
  }
  desk.addEventListener("pointerdown", (ev) => {
    if (!(ev.target instanceof Element) || !ev.target.closest(".panel")) selectPanel(null);
  });
}

enablePanelDrag();
scheduleRadarMap();
