"""Log a QSO to QRZ Logbook and local HRD Logbook."""

from __future__ import annotations

import atexit
import html
import os
import re
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .pota import band_for_hz
from .qrz import lookup_record
from .radio import _atomic_write_json, _read_json_file, load_config, save_config

QRZ_LOG_URL = "https://logbook.qrz.com/api"
USER_AGENT = "ftdx1200-control/1.0 (local ham CAT)"
HRD_HOST = "127.0.0.1"
HRD_PORT = 7826
HRD_TCP_PORTS = (7826, 7825)
HRD_UDP_N1MM = 12060
HRD_UDP_WSJT = 2333
HRD_HELP = (
    "This app starts HRD Logbook and IP Client when it can. "
    "It does not start HRD Rig Control (that would take COM4). "
    "If Logbook was already open with IP Client stopped, close Logbook once "
    "so Autostart can apply, or use View → IP Client → Start. "
    "UDP fallback: Tools → Configure → QSO Forwarding → UDP Receive "
    f"(N1MM {HRD_UDP_N1MM}, WSJT-X {HRD_UDP_WSJT})."
)
_HRD_ENSURE_LOCK = threading.Lock()
_HRD_STARTED_BY_US = False
_HRD_SHUTDOWN = False
_HRD_STOP_REGISTERED = False
_HRD_LAUNCHED = False
_HRD_IPCLI_NUDGED = False
_HRD_BG_STARTED = False
_HRD_MUTEX_NAME = "Local\\FTDX1200-HRDLogbook"
WORKED_PATH = Path.home() / ".ftdx1200-worked.json"
_WORKED_LOCK = threading.Lock()
_worked_cache: set[str] | None = None
_worked_dates: dict[str, str] | None = None
_WORKED_SYNC_STARTED = False
_qrz_worked_at = 0.0
_CREATE_NO_WINDOW = 0x08000000
_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_BREAKAWAY_FROM_JOB = 0x01000000
_SW_SHOWMINNOACTIVE = 7
N1MM_BAND = {
    "160m": "1.8",
    "80m": "3.5",
    "60m": "5.3",
    "40m": "7",
    "30m": "10",
    "20m": "14",
    "17m": "18",
    "15m": "21",
    "12m": "24",
    "10m": "28",
    "6m": "50",
    "2m": "144",
}


def qso_call(call: str) -> str:
    return str(call or "").strip().upper().split()[0]


def call_match_parts(call: str) -> list[str]:
    head = qso_call(call)
    bits: list[str] = []
    for piece in head.split("/"):
        tok = "".join(ch for ch in piece if ch.isalnum())
        if tok and tok not in bits:
            bits.append(tok)
    meat = [bit for bit in bits if re.search(r"[A-Z]+\d+[A-Z]+", bit)]
    if meat:
        return meat
    long_bits = [bit for bit in bits if len(bit) >= 3]
    return long_bits or bits


def log_match_key(call: str, ref: str = "") -> str:
    parts = call_match_parts(call)
    base = parts[0] if parts else ""
    park = str(ref or "").strip().upper()
    if not base:
        return ""
    return f"{base}|{park}" if park else base


def logged_today_keys() -> list[str]:
    today, _ = utc_stamp()
    raw = load_config().get("logged_today")
    if not isinstance(raw, dict) or str(raw.get("date") or "") != today:
        return []
    keys = raw.get("keys") or []
    if not isinstance(keys, list):
        return []
    out: list[str] = []
    for item in keys:
        key = str(item or "").strip().upper()
        if key and key not in out:
            out.append(key)
    return out


def remember_logged_qso(qso: dict[str, Any]) -> list[str]:
    park = str(qso.get("sig_info") or qso.get("reference") or "").strip().upper()
    today, _ = utc_stamp()
    keys = logged_today_keys()
    for part in call_match_parts(str(qso.get("call") or "")):
        for key in (part, f"{part}|{park}" if park else ""):
            if key and key not in keys:
                keys.append(key)
    if keys:
        save_config({"logged_today": {"date": today, "keys": keys}})
    return keys


def adif_field(name: str, value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return f"<{name.lower()}:{len(text)}>{text}"


def adif_modes(mode: str, hz: int = 0) -> tuple[str, str]:
    raw = str(mode or "").strip().upper()
    if raw in {"USB", "LSB"}:
        return "SSB", raw
    if raw in {"SSB", "PHONE", "PH"}:
        return "SSB", "USB" if hz >= 10_000_000 else "LSB"
    if raw in {"CW", "CW-R", "CWR"}:
        return "CW", "CW-R" if "R" in raw else ""
    if raw in {"RTTY", "RTTY-L", "RTTY-U"}:
        return "RTTY", raw if raw != "RTTY" else ""
    if raw in {"DATA-L", "DATA-U", "PKT", "DIG", "DATA"}:
        return "DIGI", ""
    if raw in {"FT8", "FT4", "JS8", "PSK31", "PSK"}:
        return raw if raw != "PSK" else "PSK31", ""
    if raw in {"AM", "FM", "FM-N"}:
        return "FM" if raw == "FM-N" else raw, "FMN" if raw == "FM-N" else ""
    return raw or "SSB", ""


def hrd_mode(mode: str, hz: int = 0) -> str:
    raw = str(mode or "").strip().upper()
    if raw in {"USB", "LSB", "CW", "AM", "FM"}:
        return raw
    if raw in {"CW-R", "CWR"}:
        return "CW"
    if raw in {"FM-N"}:
        return "FM"
    if raw in {"SSB", "PHONE", "PH"}:
        return "USB" if hz >= 10_000_000 else "LSB"
    if raw in {"RTTY", "RTTY-L", "RTTY-U"}:
        return "RTTY"
    if raw in {"DATA-L", "DATA-U", "PKT", "DIG", "DATA", "FT8", "FT4"}:
        return "PKT"
    return raw or "USB"


def enrich_qso_from_qrz(qso: dict[str, Any]) -> dict[str, Any]:
    try:
        rec = lookup_record(str(qso.get("call") or ""))
    except Exception:
        rec = {}
    if rec:
        if rec.get("name") and not qso.get("name"):
            qso["name"] = rec["name"]
        for key in ("qth", "addr1", "city", "state", "zip", "country", "county", "cqz", "ituz"):
            if rec.get(key) and not qso.get(key):
                qso[key] = rec[key]
        if rec.get("grid") and not qso.get("grid"):
            qso["grid"] = rec["grid"]
    if not qso.get("qth"):
        city = str(qso.get("city") or "").strip()
        state = str(qso.get("state") or "").strip()
        if city or state:
            qso["qth"] = f"{city}, {state}" if city and state else (city or state)
    return qso


def utc_stamp(now: datetime | None = None) -> tuple[str, str]:
    clock = now or datetime.now(timezone.utc)
    return clock.strftime("%Y%m%d"), clock.strftime("%H%M%S")


def build_adif(qso: dict[str, Any]) -> str:
    hz = int(qso.get("hz") or 0)
    mode, sub = adif_modes(str(qso.get("mode") or ""), hz)
    band = str(qso.get("band") or band_for_hz(hz) or "")
    mhz = str(qso.get("mhz") or "")
    if not mhz and hz:
        mhz = f"{hz / 1_000_000:.6f}".rstrip("0").rstrip(".")
    parts = [
        adif_field("call", qso.get("call")),
        adif_field("station_callsign", qso.get("station_callsign")),
        adif_field("qso_date", qso.get("qso_date")),
        adif_field("time_on", qso.get("time_on")),
        adif_field("band", band),
        adif_field("freq", mhz),
        adif_field("mode", mode),
        adif_field("submode", sub),
        adif_field("rst_sent", qso.get("rst_sent") or "59"),
        adif_field("rst_rcvd", qso.get("rst_rcvd") or "59"),
        adif_field("name", qso.get("name")),
        adif_field("qth", qso.get("qth")),
        adif_field("address", qso.get("addr1")),
        adif_field("state", qso.get("state")),
        adif_field("cnty", qso.get("county")),
        adif_field("country", qso.get("country")),
        adif_field("gridsquare", qso.get("grid")),
        adif_field("cqz", qso.get("cqz")),
        adif_field("ituz", qso.get("ituz")),
        adif_field("comment", qso.get("comment")),
        adif_field("sig", qso.get("sig")),
        adif_field("sig_info", qso.get("sig_info")),
    ]
    return "".join(part for part in parts if part) + "<eor>"


def hrd_fields(qso: dict[str, Any]) -> dict[str, str]:
    hz = int(qso.get("hz") or 0)
    band = str(qso.get("band") or band_for_hz(hz) or "")
    out = {
        "CALL": str(qso.get("call") or ""),
        "STATION_CALLSIGN": str(qso.get("station_callsign") or ""),
        "QSO_DATE": str(qso.get("qso_date") or ""),
        "TIME_ON": str(qso.get("time_on") or ""),
        "BAND": band,
        "MODE": hrd_mode(str(qso.get("mode") or ""), hz),
        "RST_SENT": str(qso.get("rst_sent") or "59"),
        "RST_RCVD": str(qso.get("rst_rcvd") or "59"),
        "FREQ": str(hz) if hz else "",
        "NAME": str(qso.get("name") or ""),
        "QTH": str(qso.get("qth") or ""),
        "CITY": str(qso.get("city") or ""),
        "ADDRESS": str(qso.get("addr1") or ""),
        "STATE": str(qso.get("state") or ""),
        "CNTY": str(qso.get("county") or ""),
        "COUNTRY": str(qso.get("country") or ""),
        "ZIP": str(qso.get("zip") or ""),
        "GRIDSQUARE": str(qso.get("grid") or ""),
        "CQZ": str(qso.get("cqz") or ""),
        "ITUZ": str(qso.get("ituz") or ""),
        "COMMENT": str(qso.get("comment") or ""),
        "SIG": str(qso.get("sig") or ""),
        "SIG_INFO": str(qso.get("sig_info") or ""),
        "QSO_COMPLETE": "Y",
    }
    return {key: val.strip() for key, val in out.items() if str(val).strip()}


def hrd_add_command(db_name: str, fields: dict[str, str]) -> str:
    name = str(db_name or "").strip() or "Logbook"
    body = " ".join(
        f'{key}="{val.replace(chr(34), chr(39)).replace(chr(13), " ").replace(chr(10), " ")}"'
        for key, val in fields.items()
    )
    return f'db add "{name.replace(chr(34), "")}" {{ {body} }}'


def parse_adif_logids(adif: str) -> list[int]:
    out: list[int] = []
    for match in re.finditer(r"<APP_QRZLOG_LOGID:(\d+)>([^<]*)", str(adif or ""), re.I):
        try:
            n = int(match.group(1))
            out.append(int(match.group(2)[:n]))
        except ValueError:
            continue
    return out


def parse_adif_calls(adif: str) -> list[str]:
    out: list[str] = []
    for match in re.finditer(r"<(?:CALL|CALLSIGN):(\d+)>([^<]*)", str(adif or ""), re.I):
        try:
            n = int(match.group(1))
        except ValueError:
            continue
        raw = match.group(2)[:n]
        for part in call_match_parts(raw):
            if part not in out:
                out.append(part)
    return out


def _adif_ymd(value: str) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def parse_adif_worked(adif: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for rec in re.split(r"<eor>", str(adif or ""), flags=re.I):
        calls = parse_adif_calls(rec)
        if not calls:
            continue
        date = ""
        match = re.search(r"<QSO_DATE(?::(\d+))?>([^<]*)", rec, re.I)
        if match:
            raw = match.group(2) or ""
            try:
                n = int(match.group(1) or "0")
            except ValueError:
                n = 0
            date = _adif_ymd(raw[:n] if n else raw)
        for part in calls:
            if date and (part not in out or date > out[part]):
                out[part] = date
            elif part not in out:
                out[part] = ""
    return {key: val for key, val in out.items() if key}


def _normalize_worked(raw: Any) -> set[str]:
    items = raw.get("calls") if isinstance(raw, dict) else raw
    out: set[str] = set()
    if not isinstance(items, list):
        return out
    for item in items:
        for part in call_match_parts(str(item or "")):
            out.add(part)
    return out


def _normalize_worked_dates(raw: Any) -> dict[str, str]:
    items = raw.get("dates") if isinstance(raw, dict) else None
    out: dict[str, str] = {}
    if not isinstance(items, dict):
        return out
    for key, val in items.items():
        day = _adif_ymd(str(val or ""))
        if not day:
            continue
        for part in call_match_parts(str(key or "")):
            if part not in out or day > out[part]:
                out[part] = day
    return out


def _read_worked_state() -> tuple[set[str], dict[str, str]]:
    data = _read_json_file(WORKED_PATH)
    if data is None and WORKED_PATH.exists():
        return set(), {}
    calls = _normalize_worked(data)
    dates = _normalize_worked_dates(data)
    calls.update(dates)
    return calls, dates


def _write_worked_state(calls: set[str], dates: dict[str, str]) -> None:
    clean = {key: dates[key] for key in sorted(dates) if key in calls and dates.get(key)}
    _atomic_write_json(WORKED_PATH, {"calls": sorted(calls), "dates": clean})


def load_worked_calls() -> set[str]:
    global _worked_cache, _worked_dates
    with _WORKED_LOCK:
        if _worked_cache is not None:
            return set(_worked_cache)
        calls, dates = _read_worked_state()
        _worked_cache = calls
        _worked_dates = dates
        return set(calls)


def load_worked_dates() -> dict[str, str]:
    load_worked_calls()
    with _WORKED_LOCK:
        return dict(_worked_dates or {})


def worked_calls_list() -> list[str]:
    return sorted(load_worked_calls())


def remember_worked_calls(
    calls: list[str] | set[str] | tuple[str, ...] | dict[str, str],
    dates: dict[str, str] | None = None,
) -> list[str]:
    global _worked_cache, _worked_dates
    extra_dates: dict[str, str] = {}
    extra: set[str] = set()
    if isinstance(calls, dict):
        dates = {**(dates or {}), **calls}
        calls = list(calls)
    for call in calls or []:
        extra.update(call_match_parts(str(call or "")))
    for key, val in (dates or {}).items():
        day = _adif_ymd(str(val or ""))
        for part in call_match_parts(str(key or "")):
            extra.add(part)
            if day and (part not in extra_dates or day > extra_dates[part]):
                extra_dates[part] = day
    if not extra:
        return worked_calls_list()
    with _WORKED_LOCK:
        if WORKED_PATH.exists() and _read_json_file(WORKED_PATH) is None:
            return sorted(_worked_cache or set())
        if _worked_cache is not None:
            current = set(_worked_cache)
            current_dates = dict(_worked_dates or {})
        else:
            current, current_dates = _read_worked_state()
        before = len(current)
        before_dates = dict(current_dates)
        current.update(extra)
        for key, day in extra_dates.items():
            if day and (key not in current_dates or day > current_dates[key]):
                current_dates[key] = day
        _worked_cache = current
        _worked_dates = current_dates
        if len(current) != before or current_dates != before_dates or not WORKED_PATH.exists():
            _write_worked_state(current, current_dates)
        return sorted(current)


def _qrz_adif_from_body(body: str) -> str:
    text = str(body or "")
    greedy = re.search(r"(?:^|[&\n])ADIF=(.*)\Z", text, re.I | re.S)
    raw = greedy.group(1).strip() if greedy else (parse_qrz_kv(text).get("ADIF") or "")
    return html.unescape(urllib.parse.unquote_plus(raw))


def _qrz_log_fetch(option: str) -> str:
    key = str(load_config().get("qrz_log_key") or "").strip()
    if not key:
        return ""
    form = urllib.parse.urlencode({
        "KEY": key,
        "ACTION": "FETCH",
        "OPTION": option,
    })
    station = str(load_config().get("my_call") or "ham").strip() or "ham"
    req = urllib.request.Request(
        QRZ_LOG_URL,
        data=form.encode("utf-8"),
        method="POST",
        headers={
            "User-Agent": f"{USER_AGENT} ({station})",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "text/plain",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError):
        return ""
    adif = _qrz_adif_from_body(body)
    if adif:
        return adif
    parsed = parse_qrz_kv(body)
    if (parsed.get("RESULT") or "").upper() in {"OK", "REPLACE"}:
        return parsed.get("ADIF") or ""
    if re.search(r"<CALL:\d+>", body, re.I):
        return body
    return ""


_qrz_sync_at = 0.0


def fetch_qrz_logged_today() -> list[str]:
    today, _ = utc_stamp()
    day = f"{today[:4]}-{today[4:6]}-{today[6:8]}"
    adif = _qrz_log_fetch(f"BETWEEN:{day}+{day},MAX:250,TYPE:ADIF")
    if not adif:
        return logged_today_keys()
    for call in parse_adif_calls(adif):
        remember_logged_qso({"call": call})
    if adif:
        remember_worked_calls(parse_adif_worked(adif))
    return logged_today_keys()


def fetch_qrz_worked_history(*, force: bool = False) -> list[str]:
    global _qrz_worked_at
    key = str(load_config().get("qrz_log_key") or "").strip()
    if not key:
        return worked_calls_list()
    now = time.time()
    if not force and now - _qrz_worked_at < 6 * 3600 and load_worked_dates():
        return worked_calls_list()
    after = 0
    for _ in range(200):
        adif = _qrz_log_fetch(f"MAX:250,AFTERLOGID:{after},TYPE:ADIF")
        mapping = parse_adif_worked(adif)
        if mapping:
            remember_worked_calls(mapping)
        ids = parse_adif_logids(adif)
        if len(ids) < 250:
            break
        after = max(ids) + 1
        time.sleep(0.15)
    _qrz_worked_at = time.time()
    return worked_calls_list()


def start_worked_sync(*, force: bool = False) -> None:
    global _WORKED_SYNC_STARTED
    if _WORKED_SYNC_STARTED and not force:
        return
    _WORKED_SYNC_STARTED = True

    def run() -> None:
        try:
            fetch_qrz_worked_history(force=force)
        except Exception:
            pass

    threading.Thread(target=run, daemon=True).start()


def sync_logged_today(*, min_age: float = 180.0) -> list[str]:
    global _qrz_sync_at
    now = time.time()
    if now - _qrz_sync_at >= min_age:
        _qrz_sync_at = now
        try:
            fetch_qrz_logged_today()
        except Exception:
            pass
    return logged_today_keys()


def parse_qrz_kv(body: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in str(body or "").replace("\r", "").replace("\n", "&").split("&"):
        if not part or "=" not in part:
            continue
        key, val = part.split("=", 1)
        out[urllib.parse.unquote_plus(key).upper()] = urllib.parse.unquote_plus(val)
    return out


def logbook_status() -> dict[str, Any]:
    cfg = load_config()
    key = str(cfg.get("qrz_log_key") or "").strip()
    port = int(cfg.get("hrd_port") or HRD_PORT)
    return {
        "my_call": str(cfg.get("my_call") or "").strip().upper(),
        "qrz_key_set": bool(key),
        "hrd_db": str(cfg.get("hrd_db") or "").strip(),
        "hrd_port": port if 1 <= port <= 65535 else HRD_PORT,
        "hrd_host": HRD_HOST,
        "logged_today": logged_today_keys(),
        "worked": worked_calls_list(),
        "worked_dates": load_worked_dates(),
        "worked_count": len(load_worked_calls()),
    }


def save_logbook_settings(body: dict[str, Any]) -> dict[str, Any]:
    cfg = load_config()
    my_call = qso_call(str(body.get("my_call") or ""))
    key = str(body.get("qrz_log_key") or "").strip()
    if not key:
        key = str(cfg.get("qrz_log_key") or "").strip()
    db_name = str(body.get("hrd_db") or "").strip()
    try:
        port = int(body.get("hrd_port") or cfg.get("hrd_port") or HRD_PORT)
    except (TypeError, ValueError):
        port = HRD_PORT
    if not 1 <= port <= 65535:
        port = HRD_PORT
    save_config({
        "my_call": my_call,
        "hrd_db": db_name,
        "hrd_port": port,
        **({"qrz_log_key": key} if key else {}),
    })
    try:
        start_worked_sync(force=True)
    except Exception:
        pass
    return logbook_status()


def _hrd_talk(command: str, port: int, timeout: float = 4.0) -> str:
    with socket.create_connection((HRD_HOST, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall((command.strip() + "\r\n").encode("ascii", errors="replace"))
        chunks: list[bytes] = []
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                data = sock.recv(4096)
            except (TimeoutError, socket.timeout):
                break
            if not data:
                break
            chunks.append(data)
            sock.settimeout(0.35)
        return b"".join(chunks).decode("utf-8", errors="replace").strip()


def n1mm_timestamp(qso: dict[str, Any]) -> str:
    date = str(qso.get("qso_date") or "")
    tod = str(qso.get("time_on") or "") + "000000"
    if len(date) < 8:
        return ""
    return f"{date[:4]}-{date[4:6]}-{date[6:8]} {tod[:2]}:{tod[2:4]}:{tod[4:6]}"


def n1mm_band_mhz(band: str, hz: int = 0) -> str:
    name = str(band or band_for_hz(hz) or "")
    return N1MM_BAND.get(name, str(hz // 1_000_000 if hz else ""))


def n1mm_contact_xml(qso: dict[str, Any]) -> str:
    hz = int(qso.get("hz") or 0)
    tens = str(max(0, hz // 10))
    stamp = n1mm_timestamp(qso)
    call = str(qso.get("call") or "")
    mycall = str(qso.get("station_callsign") or "")
    mode = hrd_mode(str(qso.get("mode") or ""), hz)
    band = n1mm_band_mhz(str(qso.get("band") or ""), hz)
    fields = {
        "app": "N1MM",
        "contestname": "DX",
        "timestamp": stamp,
        "oldtimestamp": stamp,
        "mycall": mycall,
        "band": band,
        "rxfreq": tens,
        "txfreq": tens,
        "operator": mycall,
        "mode": mode,
        "call": call,
        "oldcall": call,
        "stationprefix": mycall,
        "snt": str(qso.get("rst_sent") or "59"),
        "rcv": str(qso.get("rst_rcvd") or "59"),
        "gridsquare": str(qso.get("grid") or ""),
        "comment": str(qso.get("comment") or ""),
        "name": str(qso.get("name") or ""),
        "qth": str(qso.get("qth") or ""),
        "city": str(qso.get("city") or ""),
        "state": str(qso.get("state") or ""),
        "country": str(qso.get("country") or ""),
        "IsOriginal": "True",
        "IsClaimedQso": "1",
    }

    def esc(val: str) -> str:
        return (
            str(val)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    body = "".join(f"<{key}>{esc(val)}</{key}>" for key, val in fields.items() if val)
    return f'<?xml version="1.0" encoding="utf-8"?><contactinfo>{body}</contactinfo>'


def wsjt_adif_datagram(adif: str) -> str:
    header = adif_field("adif_ver", "3.1.0") + adif_field("programid", "Shackboard") + "<EOH>"
    return header + adif


def _udp_send(payload: bytes, port: int) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.sendto(payload, (HRD_HOST, port))
    finally:
        sock.close()


def log_hrd_udp(qso: dict[str, Any]) -> dict[str, Any]:
    xml = n1mm_contact_xml(qso)
    adif = wsjt_adif_datagram(build_adif(qso))
    try:
        _udp_send(xml.encode("utf-8"), HRD_UDP_N1MM)
        _udp_send(adif.encode("utf-8"), HRD_UDP_WSJT)
    except OSError as exc:
        return {"ok": False, "error": f"HRD UDP send failed ({exc})"}
    return {
        "ok": True,
        "db": f"UDP {HRD_UDP_N1MM}/{HRD_UDP_WSJT}",
        "reply": "sent QSO Forwarding UDP",
        "error": "",
    }


def _tcp_ports(preferred: int) -> list[int]:
    out: list[int] = []
    for port in (preferred, *HRD_TCP_PORTS):
        if port not in out and 1 <= port <= 65535:
            out.append(port)
    return out


def hrd_list_dbs(port: int) -> list[str]:
    text = _hrd_talk("db list", port)
    names: list[str] = []
    for line in text.splitlines():
        name = line.strip().strip('"')
        low = name.lower()
        if not name or "unrecognized" in low or low.startswith("error"):
            continue
        if name not in names:
            names.append(name)
    return names


def apply_ipcli_autostart(xml: str, port: int = HRD_PORT) -> str:
    text = str(xml or "")
    if not text:
        return text
    if re.search(r"<IPCli>\s*\d+\s*</IPCli>", text):
        text = re.sub(r"<IPCli>\s*\d+\s*</IPCli>", "<IPCli>1</IPCli>", text, count=1)
    if re.search(r"<IPCliPort>\s*\d+\s*</IPCliPort>", text):
        text = re.sub(
            r"<IPCliPort>\s*\d+\s*</IPCliPort>",
            f"<IPCliPort>{int(port)}</IPCliPort>",
            text,
            count=1,
        )
    return text


def hrd_tcp_ready(port: int, timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((HRD_HOST, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def hrd_settings_path() -> Path:
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "HRDLLC" / "HRD Logbook" / "LogbookUserSettings.xml"


def hrd_logbook_exe() -> Path | None:
    cfg = str(load_config().get("hrd_exe") or "").strip()
    if cfg:
        path = Path(cfg)
        if path.is_file() and path.name.lower() != "hamradiodeluxe.exe":
            return path
    roots = [
        os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)",
        os.environ.get("ProgramFiles") or r"C:\Program Files",
    ]
    for root in roots:
        path = Path(root) / "HRD Software LLC" / "Ham Radio Deluxe" / "HRDLogBook.exe"
        if path.is_file():
            return path
    return None


def _hrd_logbook_running() -> bool:
    return bool(_hrd_logbook_pids())


class _HrdLaunchMutex:
    """Cross-process lock so two FTDX copies cannot each start Logbook."""

    def __init__(self) -> None:
        self.handle = None

    def __enter__(self):
        _HRD_ENSURE_LOCK.acquire()
        if os.name != "nt":
            return self
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        self.handle = kernel32.CreateMutexW(None, False, _HRD_MUTEX_NAME)
        if self.handle:
            kernel32.WaitForSingleObject(self.handle, 30000)
        return self

    def __exit__(self, *_exc) -> None:
        if self.handle:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            kernel32.ReleaseMutex(self.handle)
            kernel32.CloseHandle(self.handle)
            self.handle = None
        _HRD_ENSURE_LOCK.release()


def _write_ipcli_autostart(port: int) -> bool:
    path = hrd_settings_path()
    if not path.is_file():
        return False
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    updated = apply_ipcli_autostart(raw, port)
    if updated == raw:
        return "<IPCli>1</IPCli>" in raw
    try:
        path.write_text(updated, encoding="utf-8")
    except OSError:
        return False
    return True


def _launch_hrd_logbook(exe: Path) -> bool:
    flags = 0
    startupinfo = None
    if os.name == "nt":
        flags = _DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP | _CREATE_BREAKAWAY_FROM_JOB
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = _SW_SHOWMINNOACTIVE
    try:
        subprocess.Popen(
            [str(exe)],
            cwd=str(exe.parent),
            close_fds=True,
            creationflags=flags,
            startupinfo=startupinfo,
        )
    except OSError:
        return False
    return True


def _wait_hrd_tcp(ports: list[int], wait_s: float, on_tick=None) -> int:
    deadline = time.time() + max(0.0, wait_s)
    while True:
        for port in ports:
            if hrd_tcp_ready(port):
                return port
        if time.time() >= deadline:
            return 0
        if on_tick:
            on_tick()
        time.sleep(0.35)


def _logbook_hwnds() -> list[int]:
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    hits: list[int] = []
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _enum(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, buf, 512)
        title = buf.value.lower()
        if "logbook" not in title:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not handle:
            return True
        try:
            image = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(32768)
            if kernel32.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(size)):
                if image.value.lower().endswith("\\hrdlogbook.exe"):
                    hits.append(int(hwnd))
        finally:
            kernel32.CloseHandle(handle)
        return True

    user32.EnumWindows(_enum, 0)
    return hits


def _start_ip_client_menu() -> None:
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    hwnds = _logbook_hwnds()
    if not hwnds:
        return
    hwnd = hwnds[0]
    WM_COMMAND = 0x0111
    MF_BYPOSITION = 0x0400
    menu = user32.GetMenu(hwnd)
    if menu:
        count = user32.GetMenuItemCount(menu)
        for i in range(max(0, int(count))):
            sub = user32.GetSubMenu(menu, i)
            if not sub:
                continue
            n = user32.GetMenuItemCount(sub)
            for j in range(max(0, int(n))):
                label = ctypes.create_unicode_buffer(256)
                user32.GetMenuStringW(sub, j, label, 256, MF_BYPOSITION)
                if "ip client" in label.value.lower().replace("&", ""):
                    cmd = user32.GetMenuItemID(sub, j)
                    if cmd and cmd != 0xFFFFFFFF:
                        user32.PostMessageW(hwnd, WM_COMMAND, cmd, 0)
                        _minimize_hrd_logbook()
                        return
    _minimize_hrd_logbook()
    return


def _wait_for_hrd_process(timeout_s: float = 8.0) -> bool:
    deadline = time.time() + max(0.0, timeout_s)
    while time.time() < deadline:
        if _hrd_logbook_pids():
            return True
        time.sleep(0.2)
    return bool(_hrd_logbook_pids())


def _claim_hrd_logbook() -> None:
    """This session owns the Logbook process and will close it on exit."""
    global _HRD_STARTED_BY_US
    _HRD_STARTED_BY_US = True


def ensure_hrd_logbook(wait_s: float = 20.0) -> dict[str, Any]:
    """Start HRD Logbook + IP Client listener; never start Rig Control."""
    global _HRD_LAUNCHED, _HRD_IPCLI_NUDGED
    cfg = logbook_status()
    preferred = int(cfg["hrd_port"])
    ports = _tcp_ports(preferred)
    started = False
    with _HrdLaunchMutex():
        if _HRD_SHUTDOWN:
            return {"ok": False, "port": 0, "started": False, "error": "shutting down"}
        for port in ports:
            if hrd_tcp_ready(port):
                _claim_hrd_logbook()
                return {"ok": True, "port": port, "started": False}
        running = bool(_hrd_logbook_pids())
        if running or _HRD_LAUNCHED:
            _claim_hrd_logbook()
            if not _HRD_IPCLI_NUDGED:
                _HRD_IPCLI_NUDGED = True
                _start_ip_client_menu()
        else:
            _write_ipcli_autostart(preferred)
            exe = hrd_logbook_exe()
            if exe is None:
                return {
                    "ok": False,
                    "port": 0,
                    "started": False,
                    "error": "HRDLogBook.exe was not found.",
                }
            _HRD_LAUNCHED = True
            if not _launch_hrd_logbook(exe):
                _HRD_LAUNCHED = False
                return {
                    "ok": False,
                    "port": 0,
                    "started": False,
                    "error": f"Could not start {exe}.",
                }
            _claim_hrd_logbook()
            started = True
    if _HRD_SHUTDOWN:
        stop_hrd_logbook()
        return {"ok": False, "port": 0, "started": started, "error": "shutting down"}
    if started:
        _wait_for_hrd_process(8.0)
        _keep_hrd_minimized(12.0)
    port = _wait_hrd_tcp(ports, wait_s, on_tick=_minimize_hrd_logbook)
    _minimize_hrd_logbook()
    if _HRD_SHUTDOWN:
        stop_hrd_logbook()
        return {"ok": False, "port": 0, "started": started, "error": "shutting down"}
    if port:
        return {"ok": True, "port": port, "started": started}
    return {
        "ok": False,
        "port": 0,
        "started": started,
        "error": "HRD Logbook TCP is not listening yet.",
    }


def _hrd_logbook_pids() -> list[int]:
    if os.name != "nt":
        return []
    pids = _hrd_pids_toolhelp()
    if pids:
        return pids
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", "IMAGENAME eq HRDLogBook.exe", "/FO", "CSV", "/NH"],
            text=True,
            timeout=5,
            stderr=subprocess.DEVNULL,
            creationflags=_CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    found: list[int] = []
    for line in out.splitlines():
        parts = [p.strip().strip('"') for p in line.split(",")]
        if len(parts) < 2 or parts[0].lower() != "hrdlogbook.exe":
            continue
        try:
            pid = int(parts[1])
        except ValueError:
            continue
        if pid not in found:
            found.append(pid)
    return found


def _hrd_pids_toolhelp() -> list[int]:
    import ctypes
    from ctypes import wintypes

    TH32CS_SNAPPROCESS = 0x2
    INVALID = ctypes.c_void_p(-1).value

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID:
        return []
    pids: list[int] = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not kernel32.Process32FirstW(snap, ctypes.byref(entry)):
            return []
        while True:
            if entry.szExeFile.lower() == "hrdlogbook.exe":
                pid = int(entry.th32ProcessID)
                if pid and pid not in pids:
                    pids.append(pid)
            if not kernel32.Process32NextW(snap, ctypes.byref(entry)):
                break
    finally:
        kernel32.CloseHandle(snap)
    return pids


def _hwnds_for_pids(pids: list[int]) -> list[int]:
    if os.name != "nt" or not pids:
        return []
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    wanted = set(pids)
    hits: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _enum(hwnd: int, _lparam: int) -> bool:
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in wanted:
            hits.append(int(hwnd))
        return True

    user32.EnumWindows(_enum, 0)
    return hits


def _keep_hrd_minimized(seconds: float = 12.0) -> None:
    def run() -> None:
        deadline = time.time() + max(0.0, seconds)
        while time.time() < deadline:
            _minimize_hrd_logbook()
            time.sleep(0.35)

    threading.Thread(target=run, daemon=True).start()


def _should_minimize_hrd_window(title: str, *, visible: bool, iconic: bool) -> bool:
    """Hidden Qt helper windows are also titled HRDLogBook. Showing them opens extras."""
    if not visible or iconic:
        return False
    return "logbook" in str(title or "").lower()


def _minimize_hrd_logbook() -> None:
    if os.name != "nt":
        return
    import ctypes

    user32 = ctypes.windll.user32
    for hwnd in _hwnds_for_pids(_hrd_logbook_pids()):
        title = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, title, 512)
        if not _should_minimize_hrd_window(
            title.value,
            visible=bool(user32.IsWindowVisible(hwnd)),
            iconic=bool(user32.IsIconic(hwnd)),
        ):
            continue
        user32.ShowWindow(hwnd, _SW_SHOWMINNOACTIVE)


def _post_logbook_close(pids: list[int]) -> None:
    if os.name != "nt" or not pids:
        return
    import ctypes

    user32 = ctypes.windll.user32
    WM_CLOSE = 0x0010
    for hwnd in _hwnds_for_pids(pids):
        if not user32.IsWindowVisible(hwnd):
            continue
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)


def _taskkill_hrd(pids: list[int]) -> None:
    """Force-end HRD Logbook only. Rig Control is a different executable."""
    if os.name != "nt":
        return
    commands: list[list[str]] = [
        ["taskkill", "/F", "/T", "/PID", str(int(pid))] for pid in pids if int(pid) > 0
    ]
    if _hrd_logbook_pids():
        commands.append(["taskkill", "/F", "/T", "/IM", "HRDLogBook.exe"])
    for cmd in commands:
        try:
            subprocess.run(
                cmd,
                timeout=5,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=_CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.SubprocessError):
            pass


def _shutdown_hrd_logbook() -> None:
    pids = _hrd_logbook_pids()
    if not pids:
        return
    _post_logbook_close(pids)
    deadline = time.monotonic() + 2.5
    while time.monotonic() < deadline and _hrd_logbook_pids():
        time.sleep(0.15)
    left = _hrd_logbook_pids()
    if left:
        _taskkill_hrd(left)


def stop_hrd_logbook() -> None:
    """Close HRD Logbook when this app is using it. Never kills Rig Control."""
    global _HRD_SHUTDOWN, _HRD_STARTED_BY_US, _HRD_LAUNCHED, _HRD_IPCLI_NUDGED
    with _HRD_ENSURE_LOCK:
        _HRD_SHUTDOWN = True
        if not _HRD_STARTED_BY_US:
            return
        try:
            _shutdown_hrd_logbook()
        finally:
            _HRD_STARTED_BY_US = False
            _HRD_LAUNCHED = False
            _HRD_IPCLI_NUDGED = False


def start_hrd_logbook_background() -> None:
    global _HRD_STOP_REGISTERED, _HRD_BG_STARTED
    if not _HRD_STOP_REGISTERED:
        atexit.register(stop_hrd_logbook)
        _HRD_STOP_REGISTERED = True
    if _HRD_BG_STARTED:
        return
    _HRD_BG_STARTED = True
    start_worked_sync()
    threading.Thread(target=ensure_hrd_logbook, kwargs={"wait_s": 25.0}, daemon=True).start()


def log_hrd(qso: dict[str, Any]) -> dict[str, Any]:
    ensure_hrd_logbook(wait_s=20.0)
    cfg = logbook_status()
    last_err = ""
    for port in _tcp_ports(int(cfg["hrd_port"])):
        try:
            names = hrd_list_dbs(port)
        except OSError as exc:
            last_err = f"{HRD_HOST}:{port} refused TCP ({exc})"
            continue
        db_name = str(cfg.get("hrd_db") or "").strip()
        if db_name and names and db_name not in names:
            return {"ok": False, "error": f'HRD logbook "{db_name}" not found. Open books: {", ".join(names)}'}
        if not db_name:
            db_name = names[0] if names else ""
        if not db_name:
            return {"ok": False, "error": "HRD Logbook returned no database name. Set the HRD logbook name in Setup."}
        cmd = hrd_add_command(db_name, hrd_fields(qso))
        try:
            reply = _hrd_talk(cmd, port)
        except OSError as exc:
            last_err = f"HRD Logbook write failed on port {port} ({exc})"
            continue
        low = reply.lower()
        ok = "added" in low and "not added" not in low
        if not ok and "valid field" in low:
            ok = True
        return {
            "ok": ok,
            "db": db_name,
            "reply": reply[:400],
            "error": "" if ok else (reply or "HRD did not confirm the QSO."),
        }
    udp = log_hrd_udp(qso)
    if udp.get("ok"):
        return udp
    return {
        "ok": False,
        "error": f"{last_err or 'HRD Logbook TCP is not listening'}. {HRD_HELP}",
    }


def log_qrz(adif: str) -> dict[str, Any]:
    key = str(load_config().get("qrz_log_key") or "").strip()
    if not key:
        return {"ok": False, "error": "QRZ Logbook API key is not set (Setup)."}
    form = urllib.parse.urlencode({"KEY": key, "ACTION": "INSERT", "ADIF": adif})
    req = urllib.request.Request(
        QRZ_LOG_URL,
        data=form.encode("utf-8"),
        method="POST",
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "text/plain",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else str(exc)
        parsed = parse_qrz_kv(body)
        reason = parsed.get("REASON") or parsed.get("RESULT") or body[:240] or str(exc)
        return {"ok": False, "error": reason, "result": parsed.get("RESULT", "FAIL")}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"ok": False, "error": str(exc)}
    parsed = parse_qrz_kv(body)
    result = (parsed.get("RESULT") or "").upper()
    ok = result in {"OK", "REPLACE"}
    return {
        "ok": ok,
        "result": result or "FAIL",
        "logid": parsed.get("LOGID") or parsed.get("LOGIDS") or "",
        "error": "" if ok else (parsed.get("REASON") or body[:240] or "QRZ insert failed."),
    }


def prepare_qso(body: dict[str, Any], radio: dict[str, Any]) -> dict[str, Any]:
    call = qso_call(str(body.get("call") or ""))
    if not call:
        raise ValueError("Enter or select a call sign to log.")
    cfg = logbook_status()
    station = qso_call(str(body.get("station_callsign") or cfg.get("my_call") or ""))
    if not station:
        raise ValueError("Set your call sign in Setup before logging.")
    try:
        hz = int(body.get("hz") or radio.get("hz") or 0)
    except (TypeError, ValueError):
        hz = 0
    if hz <= 0:
        raise ValueError("No frequency to log.")
    date, time_on = utc_stamp()
    comment = str(body.get("comment") or "").strip()
    park = str(body.get("park") or "").strip()
    ref = str(body.get("sig_info") or body.get("reference") or "").strip()
    if ref and park and park not in comment:
        comment = " · ".join(part for part in (comment, f"POTA {ref} {park}") if part)
    elif ref and f"POTA {ref}" not in comment:
        comment = " · ".join(part for part in (comment, f"POTA {ref}") if part)
    qso = {
        "call": call,
        "station_callsign": station,
        "hz": hz,
        "mhz": str(body.get("mhz") or radio.get("mhz") or f"{hz / 1_000_000:.6f}"),
        "mode": str(body.get("mode") or radio.get("mode") or "USB"),
        "band": str(body.get("band") or band_for_hz(hz) or ""),
        "rst_sent": str(body.get("rst_sent") or "59").strip() or "59",
        "rst_rcvd": str(body.get("rst_rcvd") or "59").strip() or "59",
        "name": str(body.get("name") or "").strip(),
        "qth": str(body.get("qth") or "").strip(),
        "addr1": str(body.get("addr1") or "").strip(),
        "city": str(body.get("city") or "").strip(),
        "state": str(body.get("state") or "").strip(),
        "zip": str(body.get("zip") or "").strip(),
        "country": str(body.get("country") or "").strip(),
        "county": str(body.get("county") or "").strip(),
        "cqz": str(body.get("cqz") or "").strip(),
        "ituz": str(body.get("ituz") or "").strip(),
        "grid": str(body.get("grid") or "").strip().upper(),
        "comment": comment,
        "sig": str(body.get("sig") or ("POTA" if ref else "")).strip(),
        "sig_info": ref,
        "qso_date": date,
        "time_on": time_on,
    }
    return enrich_qso_from_qrz(qso)


def log_qso(body: dict[str, Any], radio: dict[str, Any]) -> dict[str, Any]:
    qso = prepare_qso(body, radio)
    adif = build_adif(qso)
    qrz = log_qrz(adif)
    hrd = log_hrd(qso)
    ok = bool(qrz.get("ok") or hrd.get("ok"))
    parts = []
    if qrz.get("ok"):
        parts.append("QRZ logged")
    else:
        parts.append(f"QRZ: {qrz.get('error') or 'failed'}")
    if hrd.get("ok"):
        parts.append(f"HRD logged ({hrd.get('db')})")
    else:
        parts.append(f"HRD: {hrd.get('error') or 'failed'}")
    remember_logged_qso(qso)
    parts = call_match_parts(str(qso.get("call") or ""))
    remember_worked_calls(parts, {part: str(qso.get("qso_date") or "") for part in parts})
    return {
        "ok": ok,
        "message": " · ".join(parts),
        "qso": {key: qso[key] for key in ("call", "station_callsign", "mhz", "mode", "band", "qso_date", "time_on")},
        "adif": adif,
        "qrz": qrz,
        "hrd": hrd,
        **logbook_status(),
    }
