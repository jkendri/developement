"""Local web UI for FTDX-1200 CAT control."""

from __future__ import annotations

import argparse
import atexit
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

from .audio import RxAudio
from .panadapter import Panadapter
from .solar import PAGE_URL as SOLAR_PAGE_URL
from .solar import fetch_solar_banner
from .geo import maidenhead_to_latlon, parse_coord
from .stationid import identify_station
from .pota import fetch_park, fetch_spots, park_page_url, spot_age_sec_from_minutes
from .logqso import (
    log_qso,
    logbook_status,
    remember_logged_qso,
    save_logbook_settings,
    start_hrd_logbook_background,
    start_worked_sync,
    stop_hrd_logbook,
    sync_logged_today,
    worked_calls_list,
)
from .qrz import lookup_calls, lookup_record, normalize_call, qrz_configured, qrz_page_url, save_qrz_login
from .ldg.tuner import TunerSession, list_ports as tuner_list_ports, load_config as load_tuner_config
from .radio import (
    RadioSession,
    add_favorite,
    delete_favorite,
    list_favorites,
    list_ports,
    load_config,
    load_panel_layout,
    load_named_layouts,
    load_ui_colors,
    load_window_geom,
    preferred_radio_port,
    save_panel_layout,
    save_named_layouts,
    save_ui_colors,
    save_window_geom,
    ensure_qrz_secrets,
)


def _static_dir() -> Path:
    here = Path(__file__).resolve().parent / "static"
    if (here / "index.html").exists():
        return here
    if getattr(sys, "frozen", False):
        meipass = Path(sys._MEIPASS)
        for rel in (Path("ftdx1200") / "static", Path("static")):
            cand = meipass / rel
            if (cand / "index.html").exists():
                return cand
    return here


STATIC = _static_dir()
session = RadioSession()
tuner = TunerSession()
ensure_qrz_secrets()
rx_audio = RxAudio()
panadapter = Panadapter(lambda: int(session.hz or 0))
atexit.register(rx_audio.stop)
atexit.register(panadapter.stop)


def _stop_tuner() -> None:
    try:
        tuner.disconnect()
    except Exception:
        pass


def _stop_radio() -> None:
    try:
        session.disconnect()
    except Exception:
        pass


atexit.register(_stop_tuner)
atexit.register(_stop_radio)
app = Flask(__name__, static_folder=None)


@app.errorhandler(Exception)
def api_unhandled(exc: Exception):
    if isinstance(exc, HTTPException):
        return exc
    return jsonify({"error": str(exc) or exc.__class__.__name__}), 500


def _state() -> dict:
    snap = session.snapshot
    rx_audio.set_tx(bool(snap["tx"]))
    snap["audio"] = rx_audio.snapshot(devices=False)
    return snap


def _fresh(resp):
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/")
def index():
    return _fresh(send_from_directory(STATIC, "index.html"))


@app.get("/static/<path:name>")
def static_file(name: str):
    return _fresh(send_from_directory(STATIC, name))


@app.get("/api/ports")
def api_ports():
    ports = list_ports()
    saved = preferred_radio_port(ports, str(load_config().get("port") or ""))
    return jsonify({
        "ports": ports,
        "saved": saved,
        "baud": session.baud,
        "stopbits": session.stopbits,
        "auto_connect": bool(session.auto_connect),
    })


@app.get("/api/status")
def api_status():
    full = str(request.args.get("full") or "") in {"1", "true", "yes"}
    if session.snapshot["connected"] and full:
        try:
            session.poll()
        except Exception as exc:
            return jsonify({"error": str(exc), **_state()}), 400
    return jsonify(_state())


@app.post("/api/connect")
def api_connect():
    body = request.get_json(force=True, silent=True) or {}
    demo = bool(body.get("demo"))
    port = str(body.get("port") or "")
    if "baud" in body or "stopbits" in body:
        try:
            session.set_serial_opts(
                int(body.get("baud") or session.baud),
                int(body.get("stopbits") or session.stopbits),
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
    if not demo and not port:
        return jsonify({"error": "Choose a COM port or use Demo mode."}), 400
    try:
        session.connect(port, demo=demo)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(_state())


@app.post("/api/disconnect")
def api_disconnect():
    session.disconnect()
    return jsonify(_state())


def _tuner_ports() -> list[dict]:
    busy = set()
    radio_port = str(session.snapshot.get("port") or "").strip().upper()
    saved_radio = str(load_config().get("port") or "").strip().upper()
    if session.snapshot.get("connected") and radio_port:
        busy.add(radio_port)
    if saved_radio:
        busy.add(saved_radio)
    return [
        item for item in tuner_list_ports()
        if str(item.get("device") or "").strip().upper() not in busy
    ]


@app.get("/api/tuner/ports")
def api_tuner_ports():
    return jsonify({
        "ports": _tuner_ports(),
        "saved": load_tuner_config().get("port", ""),
        "auto_connect": bool(tuner.auto_connect),
    })


@app.get("/api/tuner/status")
def api_tuner_status():
    return jsonify(tuner.snapshot)


@app.post("/api/tuner/connect")
def api_tuner_connect():
    body = request.get_json(force=True, silent=True) or {}
    demo = bool(body.get("demo"))
    port = str(body.get("port") or "")
    if not demo and not port:
        return jsonify({"error": "Choose a tuner COM port or use Demo mode."}), 400
    try:
        tuner.connect(port, demo=demo)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(tuner.snapshot)


@app.post("/api/tuner/disconnect")
def api_tuner_disconnect():
    tuner.disconnect()
    return jsonify(tuner.snapshot)


@app.post("/api/tuner/command")
def api_tuner_command():
    body = request.get_json(force=True, silent=True) or {}
    name = str(body.get("name") or "")
    try:
        result = tuner.command(name)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
    snap = tuner.snapshot
    snap["result"] = result
    return jsonify(snap)


@app.post("/api/tuner/settings")
def api_tuner_settings():
    body = request.get_json(force=True, silent=True) or {}
    if "volt_ps" in body:
        tuner.set_volt_ps(float(body["volt_ps"]))
    if "watt_cal" in body:
        try:
            tuner.set_watt_cal(float(body["watt_cal"]))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
    if body.get("calibrate"):
        try:
            tuner.calibrate_watts(float(body.get("known_w") or 10))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
    if "meter_mode" in body:
        try:
            tuner.set_meter_mode(str(body["meter_mode"]))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
    if "auto_connect" in body:
        tuner.set_auto_connect(bool(body["auto_connect"]))
    return jsonify(tuner.snapshot)


@app.post("/api/settings")
def api_settings():
    body = request.get_json(force=True, silent=True) or {}
    if "auto_connect" in body:
        session.set_auto_connect(bool(body["auto_connect"]))
    if "baud" in body or "stopbits" in body:
        try:
            session.set_serial_opts(
                int(body.get("baud") or session.baud),
                int(body.get("stopbits") or session.stopbits),
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
    return jsonify(_state())


@app.get("/api/favorites")
def api_favorites_get():
    return jsonify({"favorites": list_favorites()})


@app.post("/api/favorites")
def api_favorites_post():
    body = request.get_json(force=True, silent=True) or {}
    mhz = str(body.get("mhz") or "").strip()
    if not mhz:
        hz = session.snapshot.get("hz")
        if not hz:
            return jsonify({"error": "Tune a frequency first."}), 400
        mhz = f"{int(hz) / 1_000_000:.6f}"
    mode = str(body.get("mode") or session.snapshot.get("mode") or "")
    try:
        items = add_favorite(str(body.get("name") or ""), mhz, mode)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"favorites": items})


@app.delete("/api/favorites/<fav_id>")
def api_favorites_delete(fav_id: str):
    return jsonify({"favorites": delete_favorite(fav_id)})


@app.post("/api/station/identify")
def api_station_identify():
    body = request.get_json(force=True, silent=True) or {}
    snap = session.snapshot
    raw_hz = body.get("hz") if body.get("hz") not in (None, "") else snap.get("hz")
    try:
        hz = int(raw_hz or 0)
    except (TypeError, ValueError):
        hz = 0
    mode = str(body.get("mode") or snap.get("mode") or "")
    try:
        return jsonify(identify_station(hz, mode))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.post("/api/station/open")
def api_station_open():
    body = request.get_json(force=True, silent=True) or {}
    url = str(body.get("page") or "").strip()
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    allowed = {
        "www.qrz.com", "qrz.com",
        "www.short-wave.info", "short-wave.info",
        "www.hamqth.com", "hamqth.com",
    }
    if not url.lower().startswith("https://") or host not in allowed:
        return jsonify({"error": "Blocked URL."}), 400
    webbrowser.open(url)
    return jsonify({"ok": True, "page": url})


@app.get("/api/layout")
def api_layout_get():
    return jsonify({
        "panel_pos": load_panel_layout(),
        "named_layouts": load_named_layouts(),
        "colors": load_ui_colors(),
    })


@app.post("/api/layout")
def api_layout_post():
    body = request.get_json(force=True, silent=True) or {}
    out = {
        "panel_pos": load_panel_layout(),
        "named_layouts": load_named_layouts(),
        "colors": load_ui_colors(),
    }
    if "panel_pos" in body:
        out["panel_pos"] = save_panel_layout(body.get("panel_pos"))
    if "named_layouts" in body:
        out["named_layouts"] = save_named_layouts(body.get("named_layouts"))
    if "colors" in body:
        out["colors"] = save_ui_colors(body.get("colors"))
    return jsonify(out)


@app.get("/api/pota")
def api_pota():
    force = str(request.args.get("refresh") or "") in {"1", "true", "yes"}
    extra = logbook_status()
    extra["logged_today"] = sync_logged_today()
    if force:
        start_worked_sync(force=True)
    try:
        payload = fetch_spots(force=force, max_age_sec=spot_age_sec_from_minutes(request.args.get("age")))
        payload["logged_today"] = extra.get("logged_today") or []
        payload["worked"] = extra.get("worked") or worked_calls_list()
        payload["worked_dates"] = extra.get("worked_dates") or {}
        return jsonify(payload)
    except Exception as exc:
        return jsonify({
            "error": str(exc),
            "spots": [],
            "count": 0,
            "source": "pota.app",
            "logged_today": extra.get("logged_today") or [],
            "worked": extra.get("worked") or worked_calls_list(),
            "worked_dates": extra.get("worked_dates") or {},
        }), 502


@app.get("/api/home")
def api_home():
    call = normalize_call(str(request.args.get("call") or logbook_status().get("my_call") or ""))
    if not call:
        return jsonify({"error": "Set My callsign in Log.", "lat": None, "lon": None, "grid": ""})
    rec = {}
    err = ""
    try:
        rec = lookup_record(call)
    except Exception as exc:
        err = str(exc)
    if not rec and not err and not qrz_configured()["configured"]:
        err = "QRZ username and password are not set (Setup)."
    lat = parse_coord(rec.get("lat"), lo=-90.0, hi=90.0)
    lon = parse_coord(rec.get("lon"), lo=-180.0, hi=180.0)
    grid = rec.get("grid") or ""
    if lat is None or lon is None:
        pair = maidenhead_to_latlon(grid)
        if pair:
            lat, lon = pair
    return jsonify({
        "call": rec.get("call") or call,
        "grid": grid,
        "lat": lat,
        "lon": lon,
        "qth": rec.get("qth") or "",
        "error": err,
        **qrz_configured(),
    })


@app.get("/api/pota/park")
def api_pota_park():
    ref = str(request.args.get("ref") or request.args.get("reference") or "").strip()
    try:
        return jsonify(fetch_park(ref))
    except Exception as exc:
        return jsonify({"error": str(exc), "reference": ref}), 502


@app.post("/api/pota/open")
def api_pota_open():
    body = request.get_json(force=True, silent=True) or {}
    ref = str(body.get("ref") or body.get("reference") or request.args.get("ref") or "").strip()
    try:
        url = park_page_url(ref)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    webbrowser.open(url)
    return jsonify({"ok": True, "page": url, "reference": ref.strip().upper()})


@app.get("/api/solar")
def api_solar():
    force = str(request.args.get("refresh") or "") in {"1", "true", "yes"}
    try:
        body, ctype = fetch_solar_banner(force=force)
        resp = Response(body, mimetype=ctype)
        resp.headers["Cache-Control"] = "private, max-age=300"
        return resp
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502


@app.post("/api/solar/open")
def api_solar_open():
    webbrowser.open(SOLAR_PAGE_URL)
    return jsonify({"ok": True, "page": SOLAR_PAGE_URL})


@app.post("/api/logged-today")
def api_logged_today():
    body = request.get_json(force=True, silent=True) or {}
    keys = remember_logged_qso({
        "call": body.get("call") or "",
        "sig_info": body.get("reference") or body.get("sig_info") or "",
    })
    return jsonify({"logged_today": keys})


@app.get("/api/qrz")
def api_qrz_get():
    return jsonify(qrz_configured())


@app.post("/api/qrz")
def api_qrz_save():
    body = request.get_json(force=True, silent=True) or {}
    return jsonify(save_qrz_login(str(body.get("username") or ""), str(body.get("password") or "")))


@app.post("/api/qrz/lookup")
def api_qrz_lookup():
    body = request.get_json(force=True, silent=True) or {}
    raw = body.get("calls") or []
    if not isinstance(raw, list):
        raw = []
    calls = [str(item) for item in raw[:40]]
    try:
        result = lookup_calls(calls)
    except Exception as exc:
        return jsonify({"error": str(exc), "names": {}, "pending": False}), 502
    return jsonify({**result, **qrz_configured()})


@app.get("/api/qrz/profile")
def api_qrz_profile():
    call = normalize_call(str(request.args.get("call") or ""))
    if not call:
        return jsonify({"error": "No callsign."}), 400
    try:
        rec = lookup_record(call)
    except Exception as exc:
        rec = {}
        err = str(exc)
    else:
        err = ""
        if not rec and not qrz_configured()["configured"]:
            err = "QRZ username and password are not set (Setup)."
    lat = parse_coord(rec.get("lat"), lo=-90.0, hi=90.0)
    lon = parse_coord(rec.get("lon"), lo=-180.0, hi=180.0)
    if lat is None or lon is None:
        pair = maidenhead_to_latlon(rec.get("grid") or "")
        if pair:
            lat, lon = pair
    return jsonify({
        "call": rec.get("call") or call,
        "name": rec.get("name") or "",
        "qth": rec.get("qth") or "",
        "grid": rec.get("grid") or "",
        "lat": lat,
        "lon": lon,
        "country": rec.get("country") or "",
        "county": rec.get("county") or "",
        "licclass": rec.get("licclass") or "",
        "image": rec.get("image") or "",
        "url": rec.get("url") or "",
        "page": qrz_page_url(call),
        "error": err,
        **qrz_configured(),
    })


@app.post("/api/qrz/open")
def api_qrz_open():
    body = request.get_json(force=True, silent=True) or {}
    call = normalize_call(str(body.get("call") or request.args.get("call") or ""))
    if not call:
        return jsonify({"error": "No callsign."}), 400
    url = qrz_page_url(call)
    webbrowser.open(url)
    return jsonify({"ok": True, "page": url, "call": call})


@app.get("/api/logbook")
def api_logbook_get():
    status = logbook_status()
    status["logged_today"] = sync_logged_today()
    return jsonify(status)


@app.post("/api/logbook")
def api_logbook_save():
    body = request.get_json(force=True, silent=True) or {}
    try:
        return jsonify(save_logbook_settings(body))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc) or exc.__class__.__name__, **logbook_status()}), 500


@app.post("/api/logbook/qso")
def api_logbook_qso():
    body = request.get_json(force=True, silent=True) or {}
    try:
        result = log_qso(body, session.snapshot)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc), **logbook_status()}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc), **logbook_status()}), 502
    if not result.get("ok"):
        result["error"] = result.get("message") or result.get("error") or "Log failed"
    return jsonify(result)


@app.get("/api/voacap")
def api_voacap():
    from .voacap import predict_chart

    lat = request.args.get("lat", type=float)
    lon = request.args.get("lon", type=float)
    grid = (request.args.get("grid") or "").strip()
    if grid and (lat is None or lon is None):
        pair = maidenhead_to_latlon(grid)
        if pair:
            lat, lon = pair
    if lat is None or lon is None:
        call = normalize_call(str(logbook_status().get("my_call") or ""))
        rec = {}
        if call:
            try:
                rec = lookup_record(call)
            except Exception:
                rec = {}
        lat = parse_coord(rec.get("lat"), lo=-90.0, hi=90.0)
        lon = parse_coord(rec.get("lon"), lo=-180.0, hi=180.0)
        if lat is None or lon is None:
            pair = maidenhead_to_latlon(rec.get("grid") or "")
            if pair:
                lat, lon = pair
    if lat is None or lon is None:
        return jsonify({
            "ok": False,
            "error": "No home location. Enter a grid, or set your callsign so QRZ can supply one.",
        }), 400
    try:
        power = int(request.args.get("power") or 100)
    except (TypeError, ValueError):
        power = 100
    mode = (request.args.get("mode") or "ssb").strip().lower()
    try:
        return jsonify(predict_chart(float(lat), float(lon), power, mode))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.get("/api/iss")
def api_iss():
    from .iss import predict_passes

    lat = request.args.get("lat", type=float)
    lon = request.args.get("lon", type=float)
    grid = (request.args.get("grid") or "").strip()
    if grid and (lat is None or lon is None):
        pair = maidenhead_to_latlon(grid)
        if pair:
            lat, lon = pair
    if lat is None or lon is None:
        call = normalize_call(str(logbook_status().get("my_call") or ""))
        rec = {}
        if call:
            try:
                rec = lookup_record(call)
            except Exception:
                rec = {}
        lat = parse_coord(rec.get("lat"), lo=-90.0, hi=90.0)
        lon = parse_coord(rec.get("lon"), lo=-180.0, hi=180.0)
        if lat is None or lon is None:
            pair = maidenhead_to_latlon(rec.get("grid") or "")
            if pair:
                lat, lon = pair
        if not grid:
            grid = rec.get("grid") or ""
    if lat is None or lon is None:
        return jsonify({
            "ok": False,
            "error": "No home location. Enter a grid, or set your callsign so QRZ can supply one.",
        }), 400
    try:
        min_el = float(request.args.get("min_el") if request.args.get("min_el") not in (None, "") else 10)
        min_duration = float(request.args.get("min_duration") if request.args.get("min_duration") not in (None, "") else 2)
        count = int(request.args.get("count") or 8)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Height, duration, and pass count must be numbers."}), 400
    try:
        payload = predict_passes(float(lat), float(lon), min_el=min_el, min_duration_min=min_duration, count=count)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc) or "ISS prediction failed"}), 502
    payload["grid"] = grid
    return jsonify(payload)


@app.post("/api/voacap/open")
def api_voacap_open():
    webbrowser.open("https://www.voacap.com/hf/")
    return jsonify({"ok": True})


@app.post("/api/command")
def api_command():
    body = request.get_json(force=True, silent=True) or {}
    try:
        if "mode" in body or "mhz" in body or "hz" in body:
            if "mode" in body:
                session.set_mode(str(body["mode"]))
            if "hz" in body and body.get("hz") not in (None, ""):
                session.set_hz(int(body["hz"]))
            elif "mhz" in body:
                session.set_frequency(str(body["mhz"]))
        elif "nudge" in body:
            session.nudge(int(body["nudge"]))
        elif "band" in body:
            session.set_band(str(body["band"]))
        elif "power" in body:
            session.set_power(int(body["power"]))
        elif body.get("power_off"):
            session.power_off()
        elif body.get("power_on"):
            session.power_on()
        elif "af" in body:
            session.set_af(int(body["af"]))
        elif "rf_gain" in body:
            session.set_rf_gain(int(body["rf_gain"]))
        elif "tx" in body:
            session.set_tx(bool(body["tx"]))
        elif body.get("swap"):
            session.swap_vfo()
        elif body.get("copy_ab"):
            session.copy_a_to_b()
        elif "tuner" in body:
            session.tuner(str(body["tuner"]))
        else:
            raise ValueError("no command")
        return jsonify(_state())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.get("/api/panadapter")
def api_panadapter():
    return _fresh(jsonify(panadapter.snapshot()))


@app.get("/api/audio")
def api_audio_get():
    return jsonify(rx_audio.snapshot(devices=True))


@app.post("/api/audio")
def api_audio_post():
    body = request.get_json(force=True, silent=True) or {}
    try:
        return jsonify(rx_audio.configure(body))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


def _alert(title: str, message: str) -> None:
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, message, title, 0x40)
    else:
        print(f"{title}: {message}")


def radio_should_prompt_power_off(snap: dict) -> bool:
    return bool(snap.get("connected") and snap.get("radio_on") and not snap.get("demo"))


def _ask_power_off_on_close() -> str:
    """yes powers the radio off, no leaves it on, cancel keeps the window open."""
    if not radio_should_prompt_power_off(session.snapshot):
        return "no"
    if sys.platform != "win32":
        return "no"
    import ctypes

    user32 = ctypes.windll.user32
    hwnd = user32.FindWindowW(None, "SHACKBOARD")
    message = (
        "The radio is still on.\n\n"
        "Yes turns it off, then closes SHACKBOARD.\n"
        "No closes SHACKBOARD and leaves the radio on.\n"
        "Cancel keeps SHACKBOARD open."
    )
    answer = int(user32.MessageBoxW(hwnd, message, "SHACKBOARD", 0x23 | 0x10000))
    if answer == 6:
        return "yes"
    if answer == 7:
        return "no"
    return "cancel"


_INSTANCE_MUTEX = None


def _claim_single_instance() -> bool:
    """Keep one desktop copy so HRD Logbook is not started twice."""
    global _INSTANCE_MUTEX
    if os.name != "nt":
        return True
    import ctypes

    kernel32 = ctypes.windll.kernel32
    kernel32.SetLastError(0)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    _INSTANCE_MUTEX = kernel32.CreateMutexW(None, True, "Local\\Shackboard-UI")
    return kernel32.GetLastError() != 183


def _focus_existing_window() -> None:
    if os.name != "nt":
        return
    import ctypes

    user32 = ctypes.windll.user32
    hwnd = user32.FindWindowW(None, "SHACKBOARD")
    if not hwnd:
        return
    user32.ShowWindow(hwnd, 9)
    user32.SetForegroundWindow(hwnd)


def _wait_http(url: str, seconds: float = 10.0) -> None:
    deadline = time.time() + seconds
    last = None
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=0.3)
            return
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
            time.sleep(0.1)
    raise RuntimeError(f"server did not start: {last}")


def _open_desktop_window(url: str) -> None:
    import webview

    storage = Path.home() / ".ftdx1200-control-webview"
    try:
        storage.mkdir(parents=True, exist_ok=True)
    except OSError:
        storage = None
    geom = load_window_geom()
    width = geom.get("width") or 1600
    height = geom.get("height") or 900
    win_kw = {
        "width": width,
        "height": height,
        "min_size": (880, 500),
        "resizable": True,
    }
    if "x" in geom and "y" in geom:
        win_kw["x"] = geom["x"]
        win_kw["y"] = geom["y"]
    window = webview.create_window("SHACKBOARD", f"{url}?v=board32", **win_kw)
    save_lock = threading.Lock()
    save_timer: threading.Timer | None = None

    def persist(*_args: object) -> None:
        try:
            save_window_geom(int(window.width), int(window.height), int(window.x), int(window.y))
        except Exception:
            pass

    def schedule_persist(*_args: object) -> None:
        nonlocal save_timer
        with save_lock:
            if save_timer is not None:
                save_timer.cancel()
            save_timer = threading.Timer(0.4, persist)
            save_timer.daemon = True
            save_timer.start()

    try:
        window.events.resized += schedule_persist
        window.events.moved += schedule_persist
        def close_and_stop(*_args: object) -> bool | None:
            choice = _ask_power_off_on_close()
            if choice == "cancel":
                return False
            persist()
            if choice == "yes":
                try:
                    session.power_off()
                except Exception:
                    pass
            _stop_radio()
            _stop_tuner()
            stop_hrd_logbook()
            return None

        window.events.closing += close_and_stop
    except Exception:
        pass
    kwargs: dict = {"private_mode": False}
    if storage is not None:
        kwargs["storage_path"] = str(storage)
    icon = STATIC / "ftdx1200.ico"
    if icon.exists():
        cached = Path.home() / ".ftdx1200-control.ico"
        try:
            data = icon.read_bytes()
            if not cached.exists() or cached.stat().st_size != len(data):
                cached.write_bytes(data)
            kwargs["icon"] = str(cached)
        except OSError:
            kwargs["icon"] = str(icon)
    try:
        webview.start(**kwargs)
    except Exception:
        kwargs.pop("icon", None)
        webview.start(**kwargs)


def _start_flask(host: str, port: int) -> threading.Thread:
    errors: list[BaseException] = []

    def run() -> None:
        try:
            app.run(host=host, port=port, threaded=True, use_reloader=False)
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    time.sleep(0.15)
    if errors:
        raise errors[0]
    return thread


def serve(host: str = "127.0.0.1", port: int = 8770, auto_connect: bool = True) -> str:
    """Start the HTTP UI in a background thread. Returns the base URL."""
    url = f"http://{host}:{port}/"
    _start_flask(host, port)
    _wait_http(url)
    panadapter.start()
    start_worked_sync()
    if auto_connect:
        start_hrd_logbook_background()
        session.try_auto_connect()
        tuner.try_auto_connect()
    return url


def main() -> None:
    parser = argparse.ArgumentParser(description="SHACKBOARD")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--browser", action="store_true")
    parser.add_argument("--window", action="store_true")
    args = parser.parse_args()
    frozen = getattr(sys, "frozen", False)
    if args.no_browser:
        ui = "none"
    elif args.browser:
        ui = "browser"
    elif args.window or frozen:
        ui = "window"
    else:
        ui = "browser"

    if ui == "window" and not _claim_single_instance():
        _focus_existing_window()
        raise SystemExit(0)

    try:
        url = serve(args.host, args.port, auto_connect=ui != "none")
    except Exception as exc:
        _alert(
            "SHACKBOARD",
            f"Could not start on http://{args.host}:{args.port}/\n\n{exc}\n\nClose any other copy of this app and try again.",
        )
        raise SystemExit(1) from exc

    print(f"SHACKBOARD: {url}")
    if ui == "window":
        try:
            _open_desktop_window(url)
        except Exception as exc:
            _alert("SHACKBOARD", f"Could not open the desktop window.\n\n{exc}")
            raise SystemExit(1) from exc
        finally:
            stop_hrd_logbook()
        return
    if ui == "browser":
        webbrowser.open(url)
    threading.Event().wait()


if __name__ == "__main__":
    main()
