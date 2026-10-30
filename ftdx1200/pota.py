"""Live Parks on the Air activator spots from api.pota.app."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from .geo import maidenhead_to_latlon, parse_coord

SPOTS_URL = "https://api.pota.app/spot/activator"
PARK_URL = "https://api.pota.app/park/"
PARK_PAGE = "https://pota.app/#/park/"
CACHE_SEC = 45.0
DEFAULT_SPOT_AGE_SEC = 8 * 60
MIN_SPOT_AGE_SEC = 60
MAX_SPOT_AGE_SEC = 60 * 60
USER_AGENT = "ftdx1200-control/1.0 (local ham CAT; +https://pota.app)"

_lock = threading.Lock()
_cache: dict[str, Any] = {"at": 0.0, "pool": None, "fetched_at": "", "error": ""}
_park_cache: dict[str, dict[str, Any]] = {}


def khz_to_hz(value: Any) -> int:
    text = str(value or "").strip().replace(",", "")
    if not text:
        raise ValueError("missing frequency")
    khz = float(text)
    if khz <= 0:
        raise ValueError("bad frequency")
    if khz < 500:
        khz *= 1000.0
    return int(round(khz * 1000.0))


BAND_METERS: tuple[tuple[str, int, int], ...] = (
    ("160m", 1_800_000, 2_000_000),
    ("80m", 3_500_000, 4_000_000),
    ("60m", 5_330_000, 5_405_000),
    ("40m", 7_000_000, 7_300_000),
    ("30m", 10_100_000, 10_150_000),
    ("20m", 14_000_000, 14_350_000),
    ("17m", 18_068_000, 18_168_000),
    ("15m", 21_000_000, 21_450_000),
    ("12m", 24_890_000, 24_990_000),
    ("10m", 28_000_000, 29_700_000),
    ("6m", 50_000_000, 54_000_000),
    ("2m", 144_000_000, 148_000_000),
)


def band_for_hz(hz: int) -> str:
    for name, lo, hi in BAND_METERS:
        if lo <= hz <= hi:
            return name
    return ""


def parse_spot_time(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def spot_age_sec(value: str, *, now: datetime | None = None) -> int | None:
    dt = parse_spot_time(value)
    if dt is None:
        return None
    clock = now or datetime.now(timezone.utc)
    return int((clock - dt).total_seconds())


def clamp_spot_age_sec(seconds: int) -> int:
    return max(MIN_SPOT_AGE_SEC, min(MAX_SPOT_AGE_SEC, int(seconds)))


def spot_age_sec_from_minutes(raw: Any) -> int:
    """Minutes from the POTA age setting. Blank or invalid values use the default."""
    if raw is None or str(raw).strip() == "":
        return DEFAULT_SPOT_AGE_SEC
    try:
        minutes = float(str(raw).strip())
    except ValueError:
        return DEFAULT_SPOT_AGE_SEC
    if minutes != minutes:
        return DEFAULT_SPOT_AGE_SEC
    return clamp_spot_age_sec(int(round(minutes * 60)))


def is_recent_spot(
    item: dict[str, Any],
    *,
    now: datetime | None = None,
    max_age_sec: int | None = None,
) -> bool:
    age = spot_age_sec(str(item.get("spot_time") or ""), now=now)
    if age is None:
        return False
    limit = DEFAULT_SPOT_AGE_SEC if max_age_sec is None else int(max_age_sec)
    return age <= limit


def is_usa_spot(item: dict[str, Any]) -> bool:
    ref = str(item.get("reference") or "").strip().upper()
    if ref.startswith("US-"):
        return True
    loc = str(item.get("location") or "").strip().upper()
    parts = [part.strip() for part in loc.replace(";", ",").split(",") if part.strip()]
    return any(part == "US" or part.startswith("US-") for part in parts)


def cat_mode_for(mode: str, hz: int) -> str:
    name = (mode or "").strip().upper()
    if name in {"LSB", "USB", "CW", "CW-R", "AM", "FM", "FM-N", "DATA-L", "DATA-U", "RTTY-L", "RTTY-U"}:
        return name
    if name in {"CWR"}:
        return "CW"
    if name in {"FT8", "FT4", "JS8", "DIG", "DATA", "PKT", "RTTY", "PSK"}:
        return "DATA-U" if hz >= 10_000_000 else "DATA-L"
    if name in {"SSB", "PHONE", "PH"}:
        return "USB" if hz >= 10_000_000 else "LSB"
    return ""


def normalize_spot(raw: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or raw.get("invalid"):
        return None
    try:
        hz = khz_to_hz(raw.get("frequency"))
    except (TypeError, ValueError):
        return None
    mode = str(raw.get("mode") or "").strip()
    park = str(raw.get("name") or raw.get("parkName") or "").strip()
    grid = str(raw.get("grid6") or raw.get("grid4") or "").strip()
    lat = parse_coord(raw.get("latitude") or raw.get("lat"), lo=-90.0, hi=90.0)
    lon = parse_coord(raw.get("longitude") or raw.get("lon"), lo=-180.0, hi=180.0)
    if lat is None or lon is None:
        pair = maidenhead_to_latlon(grid)
        if pair:
            lat, lon = pair
    return {
        "id": raw.get("spotId"),
        "activator": str(raw.get("activator") or "").strip(),
        "reference": str(raw.get("reference") or "").strip(),
        "park": park,
        "location": str(raw.get("locationDesc") or "").strip(),
        "grid": grid,
        "lat": lat,
        "lon": lon,
        "khz": f"{hz / 1000.0:.1f}".rstrip("0").rstrip("."),
        "mhz": f"{hz / 1_000_000:.6f}",
        "hz": hz,
        "band": band_for_hz(hz),
        "mode": mode,
        "cat_mode": cat_mode_for(mode, hz),
        "comments": str(raw.get("comments") or "").strip(),
        "spotter": str(raw.get("spotter") or "").strip(),
        "source": str(raw.get("source") or "").strip(),
        "spot_time": str(raw.get("spotTime") or "").strip(),
        "expire": int(raw.get("expire") or 0),
        "count": int(raw.get("count") or 0),
    }


def _fetch_raw() -> list[Any]:
    req = urllib.request.Request(
        SPOTS_URL,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=12) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    data = json.loads(body)
    if not isinstance(data, list):
        raise ValueError("unexpected POTA response")
    return data


def _spots_payload(pool: list[dict[str, Any]], max_age_sec: int, *, fetched_at: str, error: str = "") -> dict[str, Any]:
    fresh = [
        item
        for item in pool
        if is_usa_spot(item) and is_recent_spot(item, max_age_sec=max_age_sec)
    ]
    return {
        "spots": fresh,
        "count": len(fresh),
        "fetched_at": fetched_at,
        "error": error,
        "source": "pota.app",
        "max_age_min": max(1, int(round(max_age_sec / 60))),
    }


def fetch_spots(*, force: bool = False, max_age_sec: int | None = None) -> dict[str, Any]:
    limit = DEFAULT_SPOT_AGE_SEC if max_age_sec is None else clamp_spot_age_sec(int(max_age_sec))
    now = time.time()
    with _lock:
        pool = _cache.get("pool")
        fetched_at = str(_cache.get("fetched_at") or "")
        if not force and pool is not None and now - float(_cache["at"]) < CACHE_SEC:
            return _spots_payload(pool, limit, fetched_at=fetched_at, error=str(_cache.get("error") or ""))
    try:
        spots = [item for item in (_normalize_or_none(row) for row in _fetch_raw()) if item]
        spots = [
            item
            for item in spots
            if is_usa_spot(item) and is_recent_spot(item, max_age_sec=MAX_SPOT_AGE_SEC)
        ]
        spots.sort(key=lambda item: (-int(item["expire"]), str(item["activator"])))
        fetched_at = time.strftime("%H:%M:%S", time.gmtime())
        with _lock:
            _cache["at"] = time.time()
            _cache["pool"] = spots
            _cache["fetched_at"] = fetched_at
            _cache["error"] = ""
        return _spots_payload(spots, limit, fetched_at=fetched_at)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
        with _lock:
            pool = _cache.get("pool")
            fetched_at = str(_cache.get("fetched_at") or "")
        if pool:
            payload = _spots_payload(pool, limit, fetched_at=fetched_at)
            payload["error"] = f"POTA refresh failed: {exc}"
            return payload
        raise RuntimeError(f"Could not load POTA spots: {exc}") from exc


def _normalize_or_none(row: Any) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    return normalize_spot(row)


def park_page_url(reference: str) -> str:
    key = str(reference or "").strip().upper()
    if not key:
        raise ValueError("missing park reference")
    return f"{PARK_PAGE}{urllib.parse.quote(key, safe='-')}"


def fetch_park(reference: str) -> dict[str, Any]:
    key = str(reference or "").strip().upper()
    if not key:
        raise ValueError("missing park reference")
    with _lock:
        hit = _park_cache.get(key)
        if hit:
            return dict(hit)
    req = urllib.request.Request(
        f"{PARK_URL}{urllib.parse.quote(key)}",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=12) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    data = json.loads(body)
    if not isinstance(data, dict):
        raise ValueError("unexpected POTA park response")
    grid = str(data.get("grid6") or data.get("grid4") or data.get("grid") or "").strip()
    lat = parse_coord(data.get("latitude") or data.get("lat"), lo=-90.0, hi=90.0)
    lon = parse_coord(data.get("longitude") or data.get("lon"), lo=-180.0, hi=180.0)
    if lat is None or lon is None:
        pair = maidenhead_to_latlon(grid)
        if pair:
            lat, lon = pair
    if lat is None or lon is None:
        raise RuntimeError(f"No coordinates for park {key}")
    out = {
        "reference": str(data.get("reference") or key),
        "park": str(data.get("name") or data.get("parkName") or "").strip(),
        "grid": grid,
        "lat": lat,
        "lon": lon,
    }
    with _lock:
        _park_cache[key] = out
    return dict(out)
