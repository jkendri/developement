"""Local VOACAP-model HF reliability for the propagation panel.

Runs the DVOACAP engine on this machine. It does not call voacap.com.
"""

from __future__ import annotations

import json
import math
import threading
import time
import urllib.request
from datetime import datetime, timezone

BANDS = (
    ("80m", 3.6),
    ("40m", 7.1),
    ("30m", 10.1),
    ("20m", 14.2),
    ("17m", 18.1),
    ("15m", 21.2),
    ("12m", 24.9),
    ("10m", 28.4),
)

# Short-path targets. Coordinates are representative city centers.
TARGETS = (
    ("eu", "Europe", 51.5, -0.1),
    ("ja", "Japan", 35.7, 139.7),
    ("vk", "Australia", -33.9, 151.2),
    ("sa", "S. America", -23.6, -46.6),
    ("af", "S. Africa", -26.2, 28.0),
    ("kh6", "Hawaii", 21.3, -157.8),
    ("car", "Caribbean", 18.4, -66.1),
    ("w2", "US East", 40.7, -74.0),
    ("w6", "US West", 37.8, -122.4),
)

# Required SNR in this engine's dB scale (signal power minus noise).
MODES = {"ssb": 10.0, "cw": 0.0, "ft8": -12.0}

_SSN_URL = "https://services.swpc.noaa.gov/json/solar-cycle/observed-solar-cycle-indices.json"
_lock = threading.Lock()
_ssn_cache = {"value": 100.0, "at": 0.0}
_pred_cache: dict[tuple, tuple[float, dict]] = {}


def current_ssn() -> float:
    now = time.time()
    if _ssn_cache["at"] and now - _ssn_cache["at"] < 12 * 3600:
        return float(_ssn_cache["value"])
    try:
        with urllib.request.urlopen(_SSN_URL, timeout=12) as resp:
            rows = json.loads(resp.read().decode("utf-8", errors="replace"))
        value = 0.0
        for row in reversed(rows if isinstance(rows, list) else []):
            for key in ("observed_swpc_ssn", "ssn"):
                try:
                    number = float(row.get(key))
                except (TypeError, ValueError):
                    continue
                if number > 0:
                    value = number
                    break
            if value > 0:
                break
        if value > 0:
            _ssn_cache["value"] = value
            _ssn_cache["at"] = now
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        if not _ssn_cache["at"]:
            _ssn_cache["value"] = 100.0
    return float(_ssn_cache["value"])


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> tuple[int, int]:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    km = 2 * r * math.asin(min(1.0, math.sqrt(a)))
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    bearing = (math.degrees(math.atan2(y, x)) + 360.0) % 360.0
    return int(round(km)), int(round(bearing)) % 360


def predict_chart(lat: float, lon: float, power: int = 100, mode: str = "ssb", ssn: float | None = None) -> dict:
    mode_id = mode if mode in MODES else "ssb"
    watts = max(5, min(1500, int(power)))
    now = datetime.now(timezone.utc)
    sun = round(float(ssn if ssn is not None else current_ssn()), 1)
    key = ("muf", now.year, now.month, now.hour, round(lat, 1), round(lon, 1), watts, mode_id, sun)
    cached = _pred_cache.get(key)
    if cached and time.time() - cached[0] < 3600:
        return cached[1]
    from dvoacap.path_geometry import GeoPoint
    from dvoacap.prediction_engine import PredictionEngine

    engine = PredictionEngine()
    engine.params.ssn = sun
    engine.params.month = now.month
    engine.params.tx_power = float(watts)
    engine.params.required_snr = MODES[mode_id]
    engine.params.tx_location = GeoPoint.from_degrees(lat, lon)
    freqs = [mhz for _, mhz in BANDS]
    paths = []
    with _lock:
        for tid, name, rlat, rlon in TARGETS:
            km, bearing = _haversine(lat, lon, rlat, rlon)
            engine.predict(GeoPoint.from_degrees(rlat, rlon), utc_time=now.hour / 24.0, frequencies=freqs)
            cells = []
            for (band, mhz), pred in zip(BANDS, engine.predictions):
                rel = max(0.0, min(1.0, float(pred.signal.reliability)))
                cells.append({
                    "band": band,
                    "mhz": mhz,
                    "rel": round(rel, 3),
                    "snr": round(float(pred.signal.snr_db), 1),
                    "hop": pred.get_mode_name(engine.path.dist),
                })
            circuit = engine.circuit_muf
            paths.append({
                "id": tid,
                "name": name,
                "km": km,
                "bearing": bearing,
                "muf": round(float(circuit.muf), 1) if circuit else None,
                "fot": round(float(circuit.fot), 1) if circuit else None,
                "layer": circuit.layer if circuit else "",
                "bands": cells,
            })
    out = {
        "ok": True,
        "model": "VOACAP",
        "month": now.month,
        "hour": now.hour,
        "ssn": sun,
        "power": watts,
        "mode": mode_id,
        "lat": lat,
        "lon": lon,
        "bands": [{"band": band, "mhz": mhz} for band, mhz in BANDS],
        "paths": paths,
    }
    _pred_cache[key] = (time.time(), out)
    if len(_pred_cache) > 12:
        oldest = min(_pred_cache, key=lambda item: _pred_cache[item][0])
        _pred_cache.pop(oldest, None)
    return out
