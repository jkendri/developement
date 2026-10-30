"""Upcoming ISS passes from a CelesTrak TLE and SGP4."""

from __future__ import annotations

import json
import math
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sgp4.api import Satrec, jday

NORAD = 25544
NAME = "ISS"
TLE_URL = "https://celestrak.org/NORAD/elements/gp.php?CATNR=25544&FORMAT=TLE"
USER_AGENT = "ftdx1200-control/1.31 (local ham shack; ISS passes)"
CACHE_PATH = Path.home() / ".ftdx1200-iss-tle.json"
CACHE_SEC = 6 * 3600
STALE_SEC = 7 * 24 * 3600
SEARCH_DAYS = 5
STEP_SEC = 15

# WGS-72, the ellipsoid SGP4 itself uses.
_A_KM = 6378.135
_F = 1.0 / 298.26
_E2 = _F * (2.0 - _F)
_B_KM = _A_KM * (1.0 - _F)
_EP2 = (_A_KM * _A_KM - _B_KM * _B_KM) / (_B_KM * _B_KM)

_lock = threading.Lock()
_mem: dict[str, Any] = {"at": 0.0, "tle": None}


def _gst(jd_ut1: float) -> float:
    """Greenwich mean sidereal time, radians."""
    tut1 = (jd_ut1 - 2451545.0) / 36525.0
    seconds = (
        -6.2e-6 * tut1 * tut1 * tut1
        + 0.093104 * tut1 * tut1
        + (876600.0 * 3600.0 + 8640184.812866) * tut1
        + 67310.54841
    )
    theta = math.fmod(seconds * (math.pi / 180.0) / 240.0, 2.0 * math.pi)
    if theta < 0.0:
        theta += 2.0 * math.pi
    return theta


def _ecef(lat_deg: float, lon_deg: float, alt_km: float = 0.0) -> tuple[float, float, float]:
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    sin_lat = math.sin(lat)
    cos_lat = math.cos(lat)
    n = _A_KM / math.sqrt(1.0 - _E2 * sin_lat * sin_lat)
    return (
        (n + alt_km) * cos_lat * math.cos(lon),
        (n + alt_km) * cos_lat * math.sin(lon),
        (n * (1.0 - _E2) + alt_km) * sin_lat,
    )


def _geodetic(x: float, y: float, z: float) -> tuple[float, float]:
    p = math.hypot(x, y)
    theta = math.atan2(_A_KM * z, _B_KM * p)
    lon = math.atan2(y, x)
    lat = math.atan2(
        z + _EP2 * _B_KM * math.sin(theta) ** 3,
        p - _E2 * _A_KM * math.cos(theta) ** 3,
    )
    return math.degrees(lat), math.degrees(lon)


def _look(lat_deg: float, lon_deg: float, obs: tuple[float, float, float], r_teme: tuple[float, float, float], jd: float) -> tuple[float, float]:
    theta = _gst(jd)
    cos_th = math.cos(theta)
    sin_th = math.sin(theta)
    x = r_teme[0] * cos_th + r_teme[1] * sin_th
    y = -r_teme[0] * sin_th + r_teme[1] * cos_th
    z = r_teme[2]
    dx = x - obs[0]
    dy = y - obs[1]
    dz = z - obs[2]
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    sin_lat, cos_lat = math.sin(lat), math.cos(lat)
    sin_lon, cos_lon = math.sin(lon), math.cos(lon)
    south = sin_lat * cos_lon * dx + sin_lat * sin_lon * dy - cos_lat * dz
    east = -sin_lon * dx + cos_lon * dy
    zenith = cos_lat * cos_lon * dx + cos_lat * sin_lon * dy + sin_lat * dz
    rng = math.hypot(south, east, zenith)
    if rng <= 0.0:
        return 90.0, 0.0
    elev = math.degrees(math.asin(max(-1.0, min(1.0, zenith / rng))))
    az = math.degrees(math.atan2(east, -south)) % 360.0
    return elev, az


def _jd(when: datetime) -> tuple[float, float]:
    when = when.astimezone(timezone.utc)
    sec = when.second + when.microsecond / 1_000_000.0
    return jday(when.year, when.month, when.day, when.hour, when.minute, sec)


def _epoch(line1: str) -> datetime:
    year = int(line1[18:20])
    year += 2000 if year < 57 else 1900
    day = float(line1[20:32])
    return datetime(year, 1, 1, tzinfo=timezone.utc) + timedelta(days=day - 1.0)


def parse_tle(text: str) -> tuple[str, str, str]:
    name = NAME
    line1 = ""
    line2 = ""
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if line.startswith("1 "):
            line1 = line
        elif line.startswith("2 "):
            line2 = line
        elif line and not line1:
            name = line
    if not line1 or not line2:
        raise ValueError("TLE did not include both element lines")
    return name, line1, line2


def _read_cache() -> dict[str, Any] | None:
    try:
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    if not data.get("line1") or not data.get("line2"):
        return None
    return data


def _write_cache(tle: dict[str, Any]) -> None:
    try:
        CACHE_PATH.write_text(json.dumps(tle), encoding="utf-8")
    except OSError:
        return


def load_tle(*, force: bool = False) -> dict[str, Any]:
    now = time.time()
    with _lock:
        cached = _mem.get("tle")
        if not force and cached and now - float(_mem.get("at") or 0) < CACHE_SEC:
            return dict(cached)
    disk = _read_cache()
    if not force and disk and now - float(disk.get("fetched") or 0) < CACHE_SEC:
        with _lock:
            _mem["tle"] = disk
            _mem["at"] = now
        return dict(disk)
    req = urllib.request.Request(TLE_URL, headers={"User-Agent": USER_AGENT, "Accept": "text/plain"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            text = resp.read().decode("utf-8", errors="replace")
        name, line1, line2 = parse_tle(text)
    except (OSError, urllib.error.URLError, ValueError) as exc:
        if disk and now - float(disk.get("fetched") or 0) < STALE_SEC:
            return dict(disk)
        raise RuntimeError("Could not download the ISS element set from CelesTrak") from exc
    tle = {"name": name, "line1": line1, "line2": line2, "fetched": now}
    _write_cache(tle)
    with _lock:
        _mem["tle"] = tle
        _mem["at"] = now
    return tle


class _Tracker:
    def __init__(self, line1: str, line2: str, lat: float, lon: float) -> None:
        self.sat = Satrec.twoline2rv(line1, line2)
        self.lat = lat
        self.lon = lon
        self.obs = _ecef(lat, lon, 0.0)

    def look(self, when: datetime) -> tuple[float, float] | None:
        jd, fr = _jd(when)
        err, r, _v = self.sat.sgp4(jd, fr)
        if err != 0:
            return None
        return _look(self.lat, self.lon, self.obs, (r[0], r[1], r[2]), jd + fr)

    def subpoint(self, when: datetime) -> tuple[float, float] | None:
        jd, fr = _jd(when)
        err, r, _v = self.sat.sgp4(jd, fr)
        if err != 0:
            return None
        theta = _gst(jd + fr)
        cos_th = math.cos(theta)
        sin_th = math.sin(theta)
        x = r[0] * cos_th + r[1] * sin_th
        y = -r[0] * sin_th + r[1] * cos_th
        return _geodetic(x, y, r[2])


def _stamp(when: datetime) -> str:
    when = when.astimezone(timezone.utc) + timedelta(microseconds=500_000)
    return when.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _cross(track: _Tracker, lo: datetime, hi: datetime, limit: float, *, above: bool) -> datetime:
    """First time inside [lo, hi] on the requested side of the elevation limit."""
    for _ in range(18):
        mid = lo + (hi - lo) / 2
        sample = track.look(mid)
        el = sample[0] if sample else -90.0
        if (el >= limit) == above:
            hi = mid
        else:
            lo = mid
    return hi


def _peak(track: _Tracker, aos: datetime, los: datetime) -> tuple[datetime, float, float]:
    best_t = aos
    best = track.look(aos) or (-90.0, 0.0)
    step = timedelta(seconds=2)
    t = aos
    while t < los:
        t += step
        if t > los:
            t = los
        sample = track.look(t)
        if sample and sample[0] > best[0]:
            best = sample
            best_t = t
        if t == los:
            break
    return best_t, best[0], best[1]


def predict_passes(
    lat: float,
    lon: float,
    *,
    min_el: float = 10.0,
    min_duration_min: float = 2.0,
    count: int = 8,
    tle: tuple[str, str] | None = None,
    name: str = NAME,
    start: datetime | None = None,
) -> dict[str, Any]:
    """Return the next passes that clear the elevation and duration limits."""
    lat_f = float(lat)
    lon_f = float(lon)
    if not -90.0 <= lat_f <= 90.0 or not -180.0 <= lon_f <= 180.0:
        raise ValueError("Latitude or longitude is out of range")
    min_el_f = max(0.0, min(80.0, float(min_el)))
    min_sec = max(0.0, min(40.0, float(min_duration_min))) * 60.0
    want = max(1, min(24, int(count)))
    if tle is None:
        loaded = load_tle()
        line1, line2 = str(loaded["line1"]), str(loaded["line2"])
        name = str(loaded.get("name") or name)
        fetched = float(loaded.get("fetched") or 0)
    else:
        line1, line2 = tle
        fetched = 0.0
    epoch = _epoch(line1)
    track = _Tracker(line1, line2, lat_f, lon_f)
    begin = start.astimezone(timezone.utc) if start else datetime.now(timezone.utc)
    begin = begin.replace(microsecond=0)
    end = begin + timedelta(days=SEARCH_DAYS)
    step = timedelta(seconds=STEP_SEC)

    def elev(when: datetime) -> float:
        sample = track.look(when)
        return sample[0] if sample else -90.0

    passes: list[dict[str, Any]] = []
    prev_t = begin
    prev_el = elev(begin)
    in_pass = prev_el >= min_el_f
    aos = begin
    if in_pass:
        back = begin
        for _ in range(80):
            earlier = back - step
            if elev(earlier) < min_el_f:
                aos = _cross(track, earlier, back, min_el_f, above=True)
                break
            back = earlier
        else:
            aos = back

    t = begin
    while t < end and len(passes) < want:
        t = min(end, t + step)
        el = elev(t)
        if not in_pass and prev_el < min_el_f <= el:
            aos = _cross(track, prev_t, t, min_el_f, above=True)
            in_pass = True
        elif in_pass and el < min_el_f <= prev_el:
            los = _cross(track, prev_t, t, min_el_f, above=False)
            duration = (los - aos).total_seconds()
            if duration + 1.0 >= min_sec:
                peak_t, peak_el, peak_az = _peak(track, aos, los)
                aos_look = track.look(aos) or (min_el_f, 0.0)
                los_look = track.look(los) or (min_el_f, 0.0)
                if peak_el + 0.05 >= min_el_f:
                    passes.append({
                        "aos": _stamp(aos),
                        "los": _stamp(los),
                        "peak": _stamp(peak_t),
                        "duration_sec": int(round(duration)),
                        "max_el": round(peak_el, 1),
                        "aos_az": int(round(aos_look[1])) % 360,
                        "los_az": int(round(los_look[1])) % 360,
                        "peak_az": int(round(peak_az)) % 360,
                    })
            in_pass = False
        prev_t = t
        prev_el = el

    age_h = None
    if fetched:
        age_h = round(max(0.0, (time.time() - fetched) / 3600.0), 1)
    return {
        "ok": True,
        "name": name,
        "norad": NORAD,
        "lat": round(lat_f, 4),
        "lon": round(lon_f, 4),
        "min_el": min_el_f,
        "min_duration_min": min_sec / 60.0,
        "count": want,
        "search_days": SEARCH_DAYS,
        "downlink_mhz": 145.8,
        "tle_epoch": epoch.isoformat().replace("+00:00", "Z"),
        "tle_age_hours": age_h,
        "passes": passes,
    }
