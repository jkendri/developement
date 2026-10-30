"""Identify a station on the current VFO frequency from public internet sources."""

from __future__ import annotations

import html as htmlmod
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from .pota import band_for_hz

USER_AGENT = "ftdx1200-control/1.0 (local ham CAT; station ID)"
SW_URL = "https://www.short-wave.info/index.php"
HAMQTH_DX = "https://www.hamqth.com/dxc_csv.php"
DX_WINDOW_KHZ = 1.5
SW_WINDOW_KHZ = 5.0

_TAG = re.compile(r"<[^>]+>", re.I)
_ROW = re.compile(r"<tr\b[^>]*id=\"row\d+\"[^>]*>(.*?)</tr>", re.I | re.S)
_CELL = re.compile(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", re.I | re.S)


def mhz_text(hz: int) -> str:
    return f"{hz / 1_000_000:.6f}"


def _get(url: str, timeout: float = 14) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,text/plain,application/json,*/*",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _plain(chunk: str) -> str:
    text = htmlmod.unescape(_TAG.sub(" ", chunk or ""))
    return " ".join(text.replace("\u2009", " ").split())


def _hhmm_to_min(value: str) -> int | None:
    m = re.match(r"^(\d{1,2}):(\d{2})$", (value or "").strip())
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2))
    if hour > 23 or minute > 59:
        return None
    return hour * 60 + minute


def window_contains_now(start: str, end: str, days: str, *, now: datetime | None = None) -> bool:
    clock = now or datetime.now(timezone.utc)
    lo = _hhmm_to_min(start)
    hi = _hhmm_to_min(end)
    if lo is None or hi is None:
        return False
    cur = clock.hour * 60 + clock.minute
    if lo == hi:
        in_win = True
    elif lo < hi:
        in_win = lo <= cur < hi
    else:
        in_win = cur >= lo or cur < hi
    if not in_win:
        return False
    raw = re.sub(r"[^1-7]", "", days or "")
    if not raw:
        return True
    return str(clock.isoweekday()) in raw


_NA_CONTINENTS = {"NA", "NAM", "NORTH AMERICA"}
_NA_ITU = {
    "USA", "CAN", "MEX", "CUB", "HTI", "DOM", "JAM", "BHS", "PTR", "PRI",
    "GRL", "BER", "BMU", "BLZ", "GTM", "HND", "NIC", "CTR", "CRI", "PNR",
    "PAN", "SLV", "ATG", "BRB", "TRD", "TTO", "VCT", "LCA", "GRD", "DMA",
    "KNA", "CYM", "TCA", "AIA", "MSR", "SPM", "VIR", "ALS", "HWA",
}
_NA_ISO3 = _NA_ITU | {
    "USA", "CAN", "MEX", "CUB", "GTM", "HND", "NIC", "CRI", "PAN", "SLV",
}
_NA_COUNTRY_WORDS = (
    "united states", "usa", "canada", "mexico", "cuba", "haiti",
    "jamaica", "bahamas", "bermuda", "greenland", "puerto rico",
    "alaska", "hawaii", "belize", "guatemala", "honduras", "nicaragua",
    "costa rica", "panama", "el salvador", "dominican",
)
_NA_CALL_PREFIXES = (
    ("VP2", "Anguilla / BVI / Montserrat"),
    ("VP5", "Turks and Caicos"),
    ("VP9", "Bermuda"),
    ("CY0", "Sable Island"),
    ("CY9", "St. Paul Island"),
    ("VE", "Canada"),
    ("VA", "Canada"),
    ("VO", "Canada"),
    ("VY", "Canada"),
    ("XE", "Mexico"),
    ("XF", "Mexico"),
    ("4A", "Mexico"),
    ("6D", "Mexico"),
    ("CO", "Cuba"),
    ("CM", "Cuba"),
    ("HI", "Dominican Republic"),
    ("TG", "Guatemala"),
    ("YS", "El Salvador"),
    ("HR", "Honduras"),
    ("YN", "Nicaragua"),
    ("TI", "Costa Rica"),
    ("HP", "Panama"),
    ("V2", "Antigua"),
    ("V3", "Belize"),
    ("V4", "St. Kitts"),
    ("C6", "Bahamas"),
    ("6Y", "Jamaica"),
    ("9Y", "Trinidad"),
    ("J3", "Grenada"),
    ("J6", "St. Lucia"),
    ("J7", "Dominica"),
    ("J8", "St. Vincent"),
    ("KL", "United States"),
    ("NL", "United States"),
    ("WL", "United States"),
    ("KH", "United States"),
    ("NH", "United States"),
    ("WH", "United States"),
    ("KP", "United States"),
    ("NP", "United States"),
    ("WP", "United States"),
    ("AA", "United States"),
    ("AB", "United States"),
    ("AC", "United States"),
    ("AD", "United States"),
    ("AE", "United States"),
    ("AF", "United States"),
    ("AG", "United States"),
    ("AH", "United States"),
    ("AI", "United States"),
    ("AJ", "United States"),
    ("AK", "United States"),
    ("AL", "United States"),
    ("K", "United States"),
    ("N", "United States"),
    ("W", "United States"),
)


def _na_prefix(call: str) -> tuple[str, str] | None:
    key = re.sub(r"[^A-Z0-9]", "", str(call or "").upper().split("/")[0])
    if not key:
        return None
    for prefix, country in _NA_CALL_PREFIXES:
        if key.startswith(prefix):
            return prefix, country
    return None


def country_from_call(call: str) -> str:
    hit = _na_prefix(call)
    return hit[1] if hit else ""


def is_north_america(row: dict[str, Any]) -> bool:
    if _na_prefix(str(row.get("call") or "")):
        return True
    itu = str(row.get("itu") or row.get("continent") or "").strip().upper()
    if str(row.get("kind") or "") == "amateur":
        return False
    if itu in _NA_CONTINENTS or itu in _NA_ITU:
        return True
    country = str(row.get("country") or "").strip()
    site = str(row.get("site") or "").strip()
    for token in (country.split()[:1] + site.split()[:1]):
        if token.upper() in _NA_ISO3 or token.upper() in _NA_ITU:
            return True
    blob = f"{country} {site} {row.get('qth') or ''}".lower()
    return any(word in blob for word in _NA_COUNTRY_WORDS)


def rank_key(row: dict[str, Any], khz: float) -> tuple:
    return (
        0 if is_north_america(row) else 1,
        0 if row.get("on_air") else 1,
        abs(float(row.get("khz") or 0) - khz),
    )


def pick_station(rows: list[dict[str, Any]], khz: float) -> dict[str, Any] | None:
    if not rows:
        return None
    local = [row for row in rows if is_north_america(row)]
    pool = local or list(rows)
    pool.sort(key=lambda row: rank_key(row, khz))
    return pool[0]


def parse_shortwave_html(html: str, khz: float) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row_html in _ROW.findall(html or ""):
        cells = [_plain(cell) for cell in _CELL.findall(row_html)]
        if len(cells) < 6:
            continue
        freq_txt = re.sub(r"[^\d.]", "", cells[0])
        try:
            freq = float(freq_txt)
        except ValueError:
            continue
        if abs(freq - khz) > SW_WINDOW_KHZ:
            continue
        flag = ""
        flagged = re.search(r'class="([A-Z0-9]+)\s+flag"', row_html, re.I)
        if flagged:
            flag = flagged.group(1).upper()
        site = cells[8] if len(cells) > 8 else ""
        site = re.sub(r"Latitude:.*", "", site, flags=re.I).strip()
        item = {
            "kind": "broadcast",
            "station": cells[1],
            "call": "",
            "khz": freq,
            "start": cells[2],
            "end": cells[3],
            "days": cells[4] if len(cells) > 4 else "",
            "language": cells[5] if len(cells) > 5 else "",
            "power": cells[6] if len(cells) > 6 else "",
            "site": site,
            "itu": flag,
            "country": flag or (site.split()[0] if site else ""),
            "source": "short-wave.info",
            "page": f"{SW_URL}?freq={int(round(freq))}&now=1",
        }
        item["on_air"] = window_contains_now(item["start"], item["end"], item["days"])
        out.append(item)
    out.sort(key=lambda row: rank_key(row, khz))
    return out


def fetch_shortwave(khz: float) -> list[dict[str, Any]]:
    target = int(round(khz))
    query = urllib.parse.urlencode({"freq": str(target), "now": "1"})
    html = _get(f"{SW_URL}?{query}")
    return parse_shortwave_html(html, khz)


def _parse_dx_line(line: str, khz: float) -> dict[str, Any] | None:
    parts = [part.strip() for part in (line or "").split("^")]
    if len(parts) < 5:
        return None
    call = parts[0].upper()
    try:
        freq = float(parts[1])
    except ValueError:
        return None
    if abs(freq - khz) > DX_WINDOW_KHZ:
        return None
    if re.search(r"\d{4}-\d{2}-\d{2}|:\d{2}", parts[2]):
        when, spotter, comment = parts[2], parts[3], parts[4]
    else:
        spotter, comment, when = parts[2], parts[3], parts[4]
    country = country_from_call(call)
    return {
        "kind": "amateur",
        "station": call,
        "call": call,
        "khz": freq,
        "spotter": spotter.upper(),
        "comment": comment,
        "when": when,
        "country": country,
        "continent": "NA" if country else "",
        "on_air": True,
        "source": "HamQTH DX cluster",
        "page": f"https://www.qrz.com/db/{urllib.parse.quote(call)}",
    }


def parse_dx_csv(text: str, khz: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in (text or "").splitlines():
        item = _parse_dx_line(line, khz)
        if item:
            rows.append(item)
    rows.sort(key=lambda row: rank_key(row, khz))
    return rows


def fetch_dx_spots(khz: float, band: str) -> list[dict[str, Any]]:
    params = {"limit": "200"}
    if band:
        params["band"] = f"{band.upper().replace('M', '')}M"
    query = urllib.parse.urlencode(params)
    text = _get(f"{HAMQTH_DX}?{query}", timeout=12)
    return parse_dx_csv(text, khz)


def _banner(hz: int, mode: str, hit: dict[str, Any] | None, error: str = "") -> dict[str, Any]:
    khz = hz / 1000.0
    if not hit:
        return {
            "found": False,
            "hz": hz,
            "mhz": mhz_text(hz),
            "khz": khz,
            "mode": mode,
            "station": "",
            "call": "",
            "title": "No station listed",
            "kicker": f"{mhz_text(hz)} MHz" + (f" · {mode}" if mode else ""),
            "detail": error or "No DX cluster or shortwave listing for this frequency right now.",
            "meta": "HamQTH · short-wave.info",
            "on_air": False,
            "source": "",
            "page": f"{SW_URL}?freq={int(round(khz))}&now=1",
            "kind": "none",
        }
    kind = str(hit.get("kind") or "")
    station = str(hit.get("station") or "")
    call = str(hit.get("call") or "")
    lines: list[str] = []
    if kind == "amateur":
        if hit.get("country"):
            lines.append(str(hit["country"]))
        if hit.get("comment"):
            lines.append(str(hit["comment"]))
        extra = []
        if hit.get("spotter"):
            extra.append(f"spotted by {hit['spotter']}")
        if hit.get("when"):
            extra.append(str(hit["when"]))
        detail = " · ".join(lines) if lines else "Live DX cluster spot"
        meta = " · ".join(part for part in extra + [str(hit.get("source") or "")] if part)
        title = station + (f" — {hit['country']}" if hit.get("country") and hit["country"] not in station else "")
        kicker = f"{mhz_text(hz)} MHz" + (f" · {mode}" if mode else "") + " · amateur"
        if is_north_america(hit):
            meta = "North America · " + meta if meta else "North America"
    else:
        bits = [str(hit.get("language") or ""), str(hit.get("site") or "")]
        if hit.get("power") and str(hit["power"]) not in {"", "ND"}:
            bits.append(f"{hit['power']} kW")
        detail = " · ".join(part for part in bits if part) or "Shortwave broadcast"
        span = "–".join(part for part in (str(hit.get("start") or ""), str(hit.get("end") or "")) if part)
        air = "On air now" if hit.get("on_air") else "Scheduled"
        meta = " · ".join(part for part in (air, span + " UTC" if span else "", str(hit.get("source") or "")) if part)
        title = station
        kicker = f"{hit.get('khz') or round(khz)} kHz · shortwave"
        if is_north_america(hit):
            meta = "North America · " + meta if meta else "North America"
    return {
        "found": True,
        "hz": hz,
        "mhz": mhz_text(hz),
        "khz": float(hit.get("khz") or khz),
        "mode": mode,
        "station": station,
        "call": call,
        "title": title,
        "kicker": kicker,
        "detail": detail,
        "meta": meta,
        "on_air": bool(hit.get("on_air")),
        "source": str(hit.get("source") or ""),
        "page": str(hit.get("page") or ""),
        "kind": kind,
        "qth": str(hit.get("qth") or ""),
        "name": str(hit.get("name") or ""),
    }


def identify_station(hz: int, mode: str = "") -> dict[str, Any]:
    freq = int(hz)
    if freq <= 0:
        raise ValueError("Tune a frequency first.")
    mode_name = str(mode or "").strip()
    khz = freq / 1000.0
    band = band_for_hz(freq)
    dx: list[dict[str, Any]] = []
    sw: list[dict[str, Any]] = []
    errors: list[str] = []

    def job_dx() -> None:
        try:
            dx.extend(fetch_dx_spots(khz, band))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            errors.append(f"HamQTH: {exc}")

    def job_sw() -> None:
        try:
            sw.extend(fetch_shortwave(khz))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            errors.append(f"short-wave.info: {exc}")

    workers = [threading.Thread(target=job_sw, daemon=True)]
    if band:
        workers.append(threading.Thread(target=job_dx, daemon=True))
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=16)

    hit: dict[str, Any] | None = pick_station(list(dx) + list(sw), khz)
    if hit and hit.get("kind") == "amateur":
        try:
            from .qrz import lookup_record

            rec = lookup_record(str(hit.get("call") or ""))
            if rec:
                if rec.get("name"):
                    hit["name"] = rec["name"]
                    hit["station"] = f"{hit['call']}  {rec['name']}"
                if rec.get("qth"):
                    hit["qth"] = rec["qth"]
                    extra_qth = rec["qth"]
                    hit["comment"] = f"{extra_qth} · {hit['comment']}" if hit.get("comment") else extra_qth
                if rec.get("country") and not hit.get("country"):
                    hit["country"] = rec["country"]
        except Exception:
            pass
    err = "; ".join(errors) if errors and not hit else ""
    return _banner(freq, mode_name, hit, err)
