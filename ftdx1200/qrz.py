"""QRZ.com XML callsign lookup (operator name)."""

from __future__ import annotations

import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any

from .radio import ensure_qrz_secrets, load_config, save_qrz_secrets

QRZ_URL = "https://xmldata.qrz.com/xml/current/"
USER_AGENT = "ftdx1200-control/1.0 (local ham CAT)"
CACHE_SEC = 7 * 24 * 3600
LOOKUP_LIMIT = 40

_lock = threading.Lock()
_session_key = ""
_session_at = 0.0
_names: dict[str, str] = {}
_records: dict[str, dict[str, str]] = {}
_misses: dict[str, float] = {}
_queue: list[str] = []
_worker = False


def normalize_call(call: str) -> str:
    text = str(call or "").strip().upper().split()[0] if str(call or "").strip() else ""
    if not text:
        return ""
    base = text.split("/", 1)[0]
    return "".join(ch for ch in base if ch.isalnum())


def operator_name(fname: str, name: str) -> str:
    first = " ".join(str(fname or "").split())
    last = " ".join(str(name or "").split())
    last = last.title() if last.isupper() else last
    first = first.title() if first.isupper() else first
    return " ".join(part for part in (first, last) if part)


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def format_qth(info: dict[str, str]) -> str:
    city = _clean(info.get("addr2") or info.get("city") or "")
    state = _clean(info.get("state") or "")
    country = _clean(info.get("country") or "")
    if city and state:
        loc = f"{city}, {state}"
    else:
        loc = city or state
    skip_country = country.upper() in {"", "UNITED STATES", "UNITED STATES OF AMERICA", "USA", "US"}
    if loc and not skip_country:
        return f"{loc}, {country}"
    return loc or country


def record_from_info(info: dict[str, str]) -> dict[str, str]:
    name = operator_name(info.get("fname", ""), info.get("name", ""))
    city = _clean(info.get("addr2") or "")
    call = normalize_call(info.get("call") or "")
    return {
        "call": call,
        "name": name,
        "qth": format_qth(info),
        "addr1": _clean(info.get("addr1") or ""),
        "city": city,
        "state": _clean(info.get("state") or ""),
        "zip": _clean(info.get("zip") or ""),
        "country": _clean(info.get("country") or ""),
        "county": _clean(info.get("county") or ""),
        "grid": _clean(info.get("grid") or "").upper(),
        "lat": _clean(info.get("lat") or info.get("latitude") or ""),
        "lon": _clean(info.get("lon") or info.get("longitude") or ""),
        "cqz": _clean(info.get("cqzone") or ""),
        "ituz": _clean(info.get("ituzone") or ""),
        "email": _clean(info.get("email") or ""),
        "licclass": _clean(info.get("class") or ""),
        "image": _clean(info.get("image") or ""),
        "url": _clean(info.get("url") or ""),
    }


def qrz_page_url(call: str) -> str:
    key = normalize_call(call)
    return f"https://www.qrz.com/db/{key}" if key else "https://www.qrz.com/"


def _local_tag(tag: str) -> str:
    return tag.split("}", 1)[-1]


def _xml_map(root: ET.Element) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for child in list(root):
        section = _local_tag(child.tag)
        fields: dict[str, str] = {}
        for node in list(child):
            fields[_local_tag(node.tag)] = (node.text or "").strip()
        out[section] = fields
    return out


def _get(params: dict[str, str]) -> dict[str, dict[str, str]]:
    query = urllib.parse.urlencode(params)
    req = urllib.request.Request(
        f"{QRZ_URL}?{query}",
        headers={"User-Agent": USER_AGENT, "Accept": "application/xml"},
    )
    with urllib.request.urlopen(req, timeout=12) as resp:
        body = resp.read()
    root = ET.fromstring(body)
    return _xml_map(root)


def qrz_configured() -> dict[str, Any]:
    ensure_qrz_secrets()
    cfg = load_config()
    user = str(cfg.get("qrz_user") or "").strip()
    return {"configured": bool(user and cfg.get("qrz_pass")), "username": user}


def save_qrz_login(username: str, password: str) -> dict[str, Any]:
    user = str(username or "").strip()
    pwd = str(password or "")
    if user:
        save_qrz_secrets({"qrz_user": user, "qrz_pass": pwd})
    else:
        save_qrz_secrets({"qrz_user": "", "qrz_pass": ""}, allow_clear=True)
    with _lock:
        global _session_key, _session_at
        _session_key = ""
        _session_at = 0.0
    status = qrz_configured()
    if status["configured"]:
        try:
            _session(force=True)
            status["ok"] = True
            status["error"] = ""
        except Exception as exc:
            status["ok"] = False
            status["error"] = str(exc)
    else:
        status["ok"] = False
        status["error"] = ""
    return status


def _session(*, force: bool = False) -> str:
    global _session_key, _session_at
    now = time.time()
    if not force and _session_key and now - _session_at < 3600:
        return _session_key
    cfg = load_config()
    user = str(cfg.get("qrz_user") or "").strip()
    pwd = str(cfg.get("qrz_pass") or "")
    if not user or not pwd:
        raise RuntimeError("QRZ username and password are not set (Setup).")
    data = _get({"username": user, "password": pwd, "agent": "ftdx1200"})
    sess = data.get("Session") or {}
    err = sess.get("Error") or ""
    key = sess.get("Key") or ""
    if err or not key:
        raise RuntimeError(err or "QRZ login failed.")
    _session_key = key
    _session_at = now
    return key


def lookup_record(call: str) -> dict[str, str]:
    key = normalize_call(call)
    if not key:
        return {}
    with _lock:
        cached = _records.get(key)
        if cached is not None and "image" in cached:
            return dict(cached)
        miss_at = _misses.get(key, 0.0)
        if miss_at and time.time() - miss_at < 3600:
            return dict(cached) if cached else {}
    try:
        sess = _session()
        data = _get({"s": sess, "callsign": key})
    except (urllib.error.URLError, TimeoutError, OSError, ET.ParseError, RuntimeError):
        with _lock:
            cached = _records.get(key)
            if cached:
                return dict(cached)
            _misses[key] = time.time()
        return {}
    sess = data.get("Session") or {}
    if sess.get("Error") and "session" in sess.get("Error", "").lower():
        try:
            sess_key = _session(force=True)
            data = _get({"s": sess_key, "callsign": key})
            sess = data.get("Session") or {}
        except Exception:
            with _lock:
                cached = _records.get(key)
                if cached:
                    return dict(cached)
            return {}
    info = data.get("Callsign") or {}
    rec = record_from_info(info)
    if not rec.get("call"):
        rec["call"] = key
    useful = any(rec.get(field) for field in ("name", "qth", "city", "grid", "country", "lat", "lon"))
    with _lock:
        if useful:
            _records[key] = rec
            if rec.get("name"):
                _names[key] = rec["name"]
        else:
            _misses[key] = time.time()
    return rec if useful else {}


def lookup_call(call: str) -> str:
    return lookup_record(call).get("name") or ""


def _work() -> None:
    global _worker
    while True:
        with _lock:
            if not _queue:
                _worker = False
                return
            key = _queue.pop(0)
        lookup_call(key)
        time.sleep(0.12)


def lookup_calls(calls: list[str]) -> dict[str, Any]:
    seen: list[str] = []
    for raw in calls:
        key = normalize_call(raw)
        if key and key not in seen:
            seen.append(key)
    out: dict[str, str] = {}
    pending = False
    if not qrz_configured()["configured"]:
        return {"names": out, "pending": False}
    with _lock:
        global _worker
        for key in seen[:LOOKUP_LIMIT]:
            if key in _names:
                out[key] = _names[key]
                continue
            rec = _records.get(key) or {}
            if rec.get("name"):
                _names[key] = rec["name"]
                out[key] = rec["name"]
                continue
            miss_at = _misses.get(key, 0.0)
            if miss_at and time.time() - miss_at < 3600:
                continue
            pending = True
            if key not in _queue:
                _queue.append(key)
        if pending and not _worker:
            _worker = True
            threading.Thread(target=_work, daemon=True).start()
    return {"names": out, "pending": pending}
