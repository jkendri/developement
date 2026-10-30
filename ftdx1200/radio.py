"""Serial (or simulated) session with a Yaesu FTDX-1200."""

from __future__ import annotations

import ctypes
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Protocol

from .cat import (
    BANDS,
    MODE_CODES,
    encode_hz,
    parse_ag,
    parse_fa,
    parse_id,
    parse_if,
    parse_md,
    parse_mhz,
    parse_pc,
    parse_ps,
    parse_rg,
    parse_rm,
    parse_sm,
    parse_tx,
    split_replies,
)
from .sim import SimulatedRadioPort

CONFIG_PATH = Path.home() / ".ftdx1200-control.json"
FAVORITES_PATH = Path.home() / ".ftdx1200-favorites.json"
QRZ_PATH = Path.home() / ".ftdx1200-qrz.json"
QRZ_SECRET_KEYS = ("qrz_user", "qrz_pass", "qrz_log_key")
_CONFIG_LOCK = threading.Lock()
_FAV_LOCK = threading.Lock()
_QRZ_LOCK = threading.Lock()
BAUDS = (4800, 9600, 19200, 38400)
DEFAULT_RF_POWER = 20
FULL_RF_GAIN = 255

def _win_com_path(port_name: str) -> str:
    name = str(port_name).strip()
    if os.name == "nt" and name.upper().startswith("COM") and not name.startswith("\\\\.\\"):
        return "\\\\.\\" + name
    return name


def _open_port_error(port_name: str, exc: Exception) -> RuntimeError:
    text = str(exc).strip() or exc.__class__.__name__
    low = text.lower()
    if any(s in low for s in ("could not open", "access is denied", "permission", "in use", "being used")):
        return RuntimeError(
            f"Could not open {port_name}. Another program already has it "
            "(another FTDX window, Shack, HRD, or WSJT-X). Close that copy and try Connect again."
        )
    return RuntimeError(f"Could not open {port_name}: {text}")


class SerialLike(Protocol):
    def write(self, data: bytes) -> int: ...
    def read(self, size: int = 1) -> bytes: ...
    def close(self) -> None: ...


def _read_json_file(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.strip():
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        bak = path.with_name(path.name + ".corrupt")
        try:
            bak.write_text(text, encoding="utf-8")
        except OSError:
            pass
        return None


def _atomic_write_json(path: Path, data: Any) -> None:
    payload = json.dumps(data, indent=2)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    last: OSError | None = None
    for attempt in range(8):
        try:
            tmp.replace(path)
            return
        except OSError as exc:
            last = exc
            time.sleep(0.05 * (attempt + 1))
    if last is not None:
        raise last


def _qrz_from_dict(data: Any) -> dict[str, str]:
    if not isinstance(data, dict):
        return {}
    return {key: str(data.get(key) or "") for key in QRZ_SECRET_KEYS}


def _qrz_file_unreadable() -> bool:
    if not QRZ_PATH.exists():
        return False
    return _read_json_file(QRZ_PATH) is None


def load_qrz_secrets() -> dict[str, str]:
    data = _read_json_file(QRZ_PATH)
    if data is None and QRZ_PATH.exists():
        return {}
    return _qrz_from_dict(data)


def save_qrz_secrets(update: dict[str, Any], *, allow_clear: bool = False) -> dict[str, str]:
    if not isinstance(update, dict):
        return load_qrz_secrets()
    with _QRZ_LOCK:
        if _qrz_file_unreadable():
            raise RuntimeError("QRZ credentials file is unreadable. Existing login was not changed.")
        current = _qrz_from_dict(_read_json_file(QRZ_PATH))
        for key in QRZ_SECRET_KEYS:
            if key not in update:
                continue
            raw = str(update.get(key) or "")
            val = raw if key == "qrz_pass" else raw.strip()
            if not val and current.get(key) and not allow_clear:
                continue
            current[key] = val
        _atomic_write_json(QRZ_PATH, current)
        return dict(current)


def ensure_qrz_secrets() -> dict[str, str]:
    current: dict[str, str] = {}
    with _QRZ_LOCK:
        if _qrz_file_unreadable():
            return _qrz_from_dict(_read_json_file(CONFIG_PATH))
        current = _qrz_from_dict(_read_json_file(QRZ_PATH))
        if not any(current.values()):
            moved = _qrz_from_dict(_read_json_file(CONFIG_PATH))
            if any(moved.values()):
                current = moved
                _atomic_write_json(QRZ_PATH, current)
    if any(current.values()):
        _strip_qrz_from_main()
    return current


def _strip_qrz_from_main() -> None:
    with _CONFIG_LOCK:
        cfg = _read_json_file(CONFIG_PATH)
        if not isinstance(cfg, dict):
            return
        if not any(key in cfg for key in QRZ_SECRET_KEYS):
            return
        secrets = load_qrz_secrets()
        if _qrz_file_unreadable() or not any(secrets.values()):
            return
        for key in QRZ_SECRET_KEYS:
            cfg.pop(key, None)
        _atomic_write_json(CONFIG_PATH, cfg)


def load_config() -> dict[str, Any]:
    data = _read_json_file(CONFIG_PATH)
    cfg = dict(data) if isinstance(data, dict) else {}
    secrets = load_qrz_secrets()
    for key, val in secrets.items():
        if val:
            cfg[key] = val
    return cfg


def save_config(update: dict[str, Any]) -> None:
    if not isinstance(update, dict) or not update:
        return
    update = {key: val for key, val in update.items() if key != "favorites"}
    qrz_update = {key: update.pop(key) for key in list(update) if key in QRZ_SECRET_KEYS}
    if qrz_update:
        try:
            save_qrz_secrets(qrz_update)
        except RuntimeError:
            pass
        if not update:
            return
    if not update:
        return
    ensure_qrz_secrets()
    with _CONFIG_LOCK:
        existed = CONFIG_PATH.exists()
        raw_ok = True
        if existed:
            try:
                raw = CONFIG_PATH.read_text(encoding="utf-8")
            except OSError:
                raw = ""
            if raw.strip():
                try:
                    parsed = json.loads(raw)
                    raw_ok = isinstance(parsed, dict)
                except json.JSONDecodeError:
                    raw_ok = False
                    bak = CONFIG_PATH.with_name(CONFIG_PATH.name + ".corrupt")
                    try:
                        bak.write_text(raw, encoding="utf-8")
                    except OSError:
                        pass
                    return
        data = _read_json_file(CONFIG_PATH)
        cfg = dict(data) if isinstance(data, dict) else {}
        if existed and raw_ok and not cfg and CONFIG_PATH.stat().st_size > 0:
            return
        cfg.update(update)
        for key in QRZ_SECRET_KEYS:
            cfg.pop(key, None)
        cfg.pop("favorites", None)
        _atomic_write_json(CONFIG_PATH, cfg)


def _normalize_favorites(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, dict):
        raw = raw.get("items")
    out: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            hz = int(item.get("hz") or parse_mhz(str(item.get("mhz") or "")))
        except (TypeError, ValueError):
            continue
        out.append({
            "id": str(item.get("id") or uuid.uuid4().hex[:12]),
            "name": str(item.get("name") or "").strip() or f"{hz / 1_000_000:.6f}",
            "hz": hz,
            "mhz": f"{hz / 1_000_000:.6f}",
            "mode": str(item.get("mode") or "").strip(),
        })
    return out


def _write_favorites(items: list[dict[str, Any]]) -> None:
    _atomic_write_json(FAVORITES_PATH, {"items": items})


def _migrate_favorites_from_config() -> list[dict[str, Any]]:
    cfg = load_config()
    moved = _normalize_favorites(cfg.get("favorites"))
    if not moved:
        return []
    if not FAVORITES_PATH.exists():
        _write_favorites(moved)
    if "favorites" in cfg:
        with _CONFIG_LOCK:
            latest = load_config()
            latest.pop("favorites", None)
            if latest:
                _atomic_write_json(CONFIG_PATH, latest)
    return moved


def list_favorites() -> list[dict[str, Any]]:
    with _FAV_LOCK:
        data = _read_json_file(FAVORITES_PATH)
        if data is None and FAVORITES_PATH.exists():
            return []
        items = _normalize_favorites(data)
        if items:
            return items
        return _migrate_favorites_from_config()


def add_favorite(name: str, mhz: str, mode: str = "") -> list[dict[str, Any]]:
    hz = parse_mhz(mhz)
    label = (name or "").strip() or f"{hz / 1_000_000:.6f}"
    with _FAV_LOCK:
        data = _read_json_file(FAVORITES_PATH)
        if data is None and FAVORITES_PATH.exists():
            raise RuntimeError(
                "Favorites file is unreadable. The list was not changed."
            )
        items = _normalize_favorites(data)
        if not items:
            items = _migrate_favorites_from_config()
        items.append({
            "id": uuid.uuid4().hex[:12],
            "name": label,
            "hz": hz,
            "mhz": f"{hz / 1_000_000:.6f}",
            "mode": (mode or "").strip(),
        })
        _write_favorites(items)
        return items


def delete_favorite(fav_id: str) -> list[dict[str, Any]]:
    fid = str(fav_id or "")
    with _FAV_LOCK:
        data = _read_json_file(FAVORITES_PATH)
        if data is None and FAVORITES_PATH.exists():
            raise RuntimeError(
                "Favorites file is unreadable. The list was not changed."
            )
        items = _normalize_favorites(data)
        if not items:
            items = _migrate_favorites_from_config()
        items = [item for item in items if item["id"] != fid]
        _write_favorites(items)
        return items


def load_panel_layout() -> dict[str, Any]:
    raw = load_config().get("panel_pos")
    if not isinstance(raw, dict):
        return {}
    return {
        str(key): val
        for key, val in raw.items()
        if str(key) and isinstance(val, dict)
    }


def save_panel_layout(pos: Any) -> dict[str, Any]:
    clean = load_panel_layout()
    if isinstance(pos, dict):
        clean = {
            str(key): val
            for key, val in pos.items()
            if str(key) and isinstance(val, dict)
        }
    save_config({"panel_pos": clean})
    return clean


_COLOR_KEYS = ("bg", "panel", "meter", "accent", "ink")


def _hex_color(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) == 7 and text.startswith("#"):
        try:
            int(text[1:], 16)
        except ValueError:
            return ""
        return text.lower()
    return ""


def load_ui_colors() -> dict[str, str]:
    raw = load_config().get("ui_colors")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key in _COLOR_KEYS:
        color = _hex_color(raw.get(key))
        if color:
            out[key] = color
    return out


def save_ui_colors(raw: Any) -> dict[str, str]:
    clean = load_ui_colors()
    if isinstance(raw, dict):
        clean = {}
        for key in _COLOR_KEYS:
            color = _hex_color(raw.get(key))
            if color:
                clean[key] = color
    save_config({"ui_colors": clean})
    return clean


def load_named_layouts() -> dict[str, Any]:
    raw = load_config().get("named_layouts")
    if not isinstance(raw, dict):
        return {}
    return _clean_named_layouts(raw)


def save_named_layouts(raw: Any) -> dict[str, Any]:
    clean = _clean_named_layouts(raw if isinstance(raw, dict) else {})
    save_config({"named_layouts": clean})
    return clean


def _clean_named_layouts(raw: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, val in raw.items():
        name = str(key or "").strip()[:24]
        if not name or name.startswith("__") or not isinstance(val, dict):
            continue
        panels = {
            str(panel): item
            for panel, item in val.items()
            if str(panel) and isinstance(item, dict)
        }
        if panels:
            out[name] = panels
        if len(out) >= 12:
            break
    return out


def load_window_geom() -> dict[str, int]:
    raw = load_config().get("window")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, int] = {}
    for key in ("width", "height", "x", "y"):
        try:
            out[key] = int(raw[key])
        except (KeyError, TypeError, ValueError):
            continue
    if "x" in out and "y" in out:
        w = out.get("width") or 1100
        h = out.get("height") or 980
        if not _geom_on_screen(out["x"], out["y"], w, h):
            out.pop("x", None)
            out.pop("y", None)
    return _fit_window_geom(out)


def save_window_geom(width: int, height: int, x: int | None = None, y: int | None = None) -> dict[str, int]:
    geom = load_window_geom()
    if x is not None and y is not None and _window_covers_screen(int(x), int(y), int(width), int(height)):
        return geom
    w = max(720, min(4000, int(width)))
    h = max(520, min(3000, int(height)))
    geom["width"] = w
    geom["height"] = h
    if x is not None and y is not None and _geom_on_screen(int(x), int(y), w, h):
        geom["x"] = int(x)
        geom["y"] = int(y)
    save_config({"window": geom})
    return geom


def _virtual_screen() -> tuple[int, int, int, int] | None:
    if os.name != "nt":
        return None
    try:
        user32 = ctypes.windll.user32
        return (
            int(user32.GetSystemMetrics(76)),
            int(user32.GetSystemMetrics(77)),
            int(user32.GetSystemMetrics(78)),
            int(user32.GetSystemMetrics(79)),
        )
    except Exception:
        return None


def _monitor_work_area(x: int, y: int) -> tuple[int, int, int, int] | None:
    if os.name != "nt":
        return None
    try:
        user32 = ctypes.windll.user32

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        class MONITORINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.c_ulong),
                ("rcMonitor", RECT),
                ("rcWork", RECT),
                ("dwFlags", ctypes.c_ulong),
            ]

        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        monitor = user32.MonitorFromPoint(POINT(int(x), int(y)), 2)
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if not monitor or not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return None
        work = info.rcWork
        return (int(work.left), int(work.top), int(work.right - work.left), int(work.bottom - work.top))
    except Exception:
        return None


def _window_covers_screen(x: int, y: int, w: int, h: int) -> bool:
    work = _monitor_work_area(x + max(w, 0) // 2, max(0, y) + max(h, 0) // 2)
    if work is None:
        return False
    _wx, _wy, ww, wh = work
    return w >= ww - 16 or h >= wh - 16


def _fit_window_geom(geom: dict[str, int]) -> dict[str, int]:
    if "width" not in geom and "height" not in geom:
        return geom
    x = geom.get("x", 80)
    y = geom.get("y", 40)
    w = geom.get("width") or 1600
    h = geom.get("height") or 900
    if not _window_covers_screen(x, y, w, h):
        return geom
    work = _monitor_work_area(x + w // 2, max(0, y) + h // 2)
    if work is None:
        return geom
    wx, wy, ww, wh = work
    margin = 48
    fitted = dict(geom)
    fitted["width"] = max(880, ww - margin * 2)
    fitted["height"] = max(500, wh - margin * 2)
    fitted["x"] = wx + max(0, (ww - fitted["width"]) // 2)
    fitted["y"] = wy + max(0, (wh - fitted["height"]) // 2)
    return fitted


def _geom_on_screen(x: int, y: int, w: int, h: int) -> bool:
    if x <= -10000 or y <= -10000:
        return False
    screen = _virtual_screen()
    if screen is None:
        return x > -200 and y > -200
    vx, vy, vw, vh = screen
    return x + w > vx + 80 and x < vx + vw - 80 and y + 32 > vy and y < vy + vh - 32


class RadioSession:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._port: SerialLike | None = None
        self._reader: threading.Thread | None = None
        self._meter: threading.Thread | None = None
        self._stop = threading.Event()
        self._meter_stop = threading.Event()
        self._buf = bytearray()
        self._replies: list[str] = []
        self._reply_event = threading.Event()
        self._connected = False
        self._demo = False
        self._port_name = ""
        self._log: list[dict[str, Any]] = []
        cfg = load_config()
        self.baud = int(cfg.get("baud") or 4800)
        if self.baud not in BAUDS:
            self.baud = 4800
        self.stopbits = 2 if str(cfg.get("stopbits") or "2") != "1" else 1
        self.auto_connect = bool(cfg.get("auto_connect"))
        self.hz = 14_250_000
        self.mode = "USB"
        self.s_meter = 0
        self.po_meter = 0
        self.swr_meter = 0
        self.power = DEFAULT_RF_POWER
        self.af = 128
        self.rf_gain = 255
        self._rf_gain_hold_until = 0.0
        self.transmitting = False
        self._utune_on = False
        self.radio_id = ""
        self.vfo = "VFO-A"
        self.radio_on = True

    def _note(self, message: str) -> None:
        self._log.append({"t": time.strftime("%H:%M:%S"), "msg": message})
        self._log = self._log[-80:]

    @property
    def snapshot(self) -> dict[str, Any]:
        return {
            "connected": self._connected,
            "demo": self._demo,
            "port": self._port_name,
            "baud": self.baud,
            "stopbits": self.stopbits,
            "auto_connect": self.auto_connect,
            "hz": self.hz,
            "mhz": f"{self.hz / 1_000_000:.6f}",
            "mode": self.mode,
            "s_meter": self.s_meter,
            "po_meter": self.po_meter,
            "swr_meter": self.swr_meter,
            "power": self.power,
            "af": self.af,
            "rf_gain": self.rf_gain,
            "tx": self.transmitting,
            "radio_id": self.radio_id,
            "radio_on": self.radio_on,
            "vfo": self.vfo,
            "bands": BANDS,
            "modes": list(MODE_CODES),
            "log": list(self._log),
        }

    def set_auto_connect(self, on: bool) -> None:
        self.auto_connect = bool(on)
        save_config({"auto_connect": self.auto_connect})
        self._note("Auto connect on" if self.auto_connect else "Auto connect off")

    def set_serial_opts(self, baud: int, stopbits: int) -> None:
        if baud not in BAUDS:
            raise ValueError("baud must be 4800, 9600, 19200, or 38400")
        bits = 2 if int(stopbits) != 1 else 1
        self.baud = baud
        self.stopbits = bits
        save_config({"baud": baud, "stopbits": bits})

    def try_auto_connect(self) -> None:
        if not self.auto_connect or self._connected:
            return
        port = preferred_radio_port(saved=str(load_config().get("port") or ""))
        if not port:
            self._note("Auto connect: no saved port")
            return
        try:
            self.connect(port, demo=False)
        except Exception as exc:
            self._note(f"Auto connect failed: {exc}")

    def connect(self, port_name: str, demo: bool = False) -> None:
        self.disconnect()
        if demo or port_name.lower() in {"demo", "sim", "simulator"}:
            self._port = SimulatedRadioPort()
            self._demo = True
            self._port_name = "DEMO"
        else:
            import serial

            try:
                ser = serial.Serial(
                    port=_win_com_path(port_name),
                    baudrate=self.baud,
                    bytesize=serial.EIGHTBITS,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_TWO if self.stopbits == 2 else serial.STOPBITS_ONE,
                    timeout=0.05,
                    write_timeout=1.0,
                    xonxoff=False,
                    rtscts=False,
                    dsrdtr=False,
                )
            except Exception as exc:
                raise _open_port_error(port_name, exc) from exc
            ser.dtr = True
            ser.rts = True
            self._port = ser
            self._demo = False
            self._port_name = port_name
            save_config({"port": port_name, "baud": self.baud, "stopbits": self.stopbits})
        self._stop.clear()
        self._buf.clear()
        self._replies.clear()
        self._connected = True
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self._meter_stop.clear()
        self._meter = threading.Thread(target=self._meter_loop, daemon=True)
        self._meter.start()
        ident = self.query("ID;", timeout=0.4)
        if not ident and not self._demo:
            ident = self._detect_cat_baud()
        self.radio_id = parse_id(ident) if ident else ""
        self._note(f"Connected on {self._port_name} at {self.baud} 8N{self.stopbits}")
        if self.radio_id:
            self._note(f"Radio ID {self.radio_id}")
        elif not self._demo:
            self._note("No CAT reply — check baud (menu 039) and Power on")
        self.radio_on = True
        try:
            saved = None if self._demo else self._saved_af()
            if saved is not None:
                self.set_af(saved)
        except Exception:
            pass
        try:
            self.poll()
        except Exception:
            pass
        try:
            self._force_full_rf_gain()
        except Exception:
            pass
        self.set_utune(True)

    def set_utune(self, on: bool) -> None:
        """Turn the rear μ-Tune path on or off. On is the RX IN route used by the panadapter."""
        if not self._connected or self._demo:
            return
        try:
            self.query("VF01+0;" if on else "VF00+0;", timeout=0.4, log=True)
            status = self.query("RI9;", timeout=0.4, log=False)
        except Exception as exc:
            self._note(f"μ-Tune {'on' if on else 'off'} failed: {exc}")
            return
        enabled = status.upper().startswith("RI9") and len(status) > 3 and status[3] == "1"
        if enabled != bool(on):
            self._note("μ-Tune stayed off — this radio did not accept the CAT command")
            return
        self._utune_on = bool(on)
        self._note("μ-Tune on" if on else "μ-Tune off")

    def _detect_cat_baud(self) -> str:
        ser = self._port
        if ser is None or not hasattr(ser, "baudrate"):
            return ""
        tried = {int(self.baud)}
        for baud in (38400, 19200, 9600, 4800):
            if baud in tried:
                continue
            tried.add(baud)
            try:
                ser.baudrate = baud
            except Exception:
                continue
            time.sleep(0.06)
            self._buf.clear()
            ident = self.query("ID;", timeout=0.4)
            if ident:
                self.baud = baud
                save_config({"baud": baud})
                return ident
        try:
            ser.baudrate = self.baud
        except Exception:
            pass
        return ""

    def disconnect(self) -> None:
        was_connected = self._connected and not self._demo
        if was_connected and self._utune_on:
            try:
                self.query("VF00+0;", timeout=0.35, log=True)
            except Exception:
                pass
            self._utune_on = False
            self._note("μ-Tune off")
        if self._connected and self.transmitting:
            try:
                self.command("TX0;")
            except Exception:
                pass
        self._meter_stop.set()
        if self._meter and self._meter.is_alive() and threading.current_thread() is not self._meter:
            self._meter.join(timeout=0.6)
        self._meter = None
        self._stop.set()
        port = self._port
        self._port = None
        self._connected = False
        self.transmitting = False
        if self._reader and self._reader.is_alive() and threading.current_thread() is not self._reader:
            self._reader.join(timeout=1.0)
        self._reader = None
        if port is not None:
            try:
                port.close()
            except OSError:
                pass
        if was_connected:
            self._remember_af(self.af)
        self._note("Disconnected")

    def poll(self) -> dict[str, Any]:
        if not self._connected or not self.radio_on:
            return self.snapshot
        info = parse_if(self.query("IF;", log=False))
        if info:
            self.hz = int(info["hz"])
            self.vfo = str(info["vfo"])
        else:
            hz = parse_fa(self.query("FA;", log=False))
            if hz:
                self.hz = hz
        md = parse_md(self.query("MD0;", log=False))
        if md:
            self.mode = md
        pc = parse_pc(self.query("PC;", log=False))
        if pc is not None:
            self.power = pc
        ag = parse_ag(self.query("AG0;", log=False))
        if ag is not None:
            self.af = ag
        rg = parse_rg(self.query("RG0;", log=False))
        if rg is not None and time.monotonic() >= self._rf_gain_hold_until:
            self.rf_gain = rg
        tx = self.query("TX;", log=False)
        if tx:
            self.transmitting = parse_tx(tx)
        ps = parse_ps(self.query("PS;", log=False))
        if ps is not None:
            self.radio_on = ps
        return self.snapshot

    def set_frequency(self, text: str) -> dict[str, Any]:
        hz = parse_mhz(text)
        return self.set_hz(hz)

    def set_hz(self, hz: int) -> dict[str, Any]:
        hz = max(30_000, min(56_000_000, int(hz)))
        cmd = f"FA{encode_hz(hz)};"
        self.query(cmd, timeout=0.35, log=False)
        time.sleep(0.04 if self._demo else 0.08)
        got = parse_fa(self.query("FA;", timeout=0.35, log=False))
        if got is None:
            info = parse_if(self.query("IF;", timeout=0.35, log=False))
            got = int(info["hz"]) if info else None
        if got is not None and abs(got - hz) > 100:
            self.query(cmd, timeout=0.35, log=False)
        self.hz = hz
        return self.snapshot

    def nudge(self, delta_hz: int) -> dict[str, Any]:
        return self.set_hz(self.hz + int(delta_hz))

    def set_mode(self, name: str) -> dict[str, Any]:
        code = MODE_CODES.get(name.upper(), MODE_CODES.get(name))
        if not code:
            raise ValueError(f"unknown mode {name}")
        self.query(f"MD0{code};", timeout=0.3, log=False)
        md = parse_md(self.query("MD0;", timeout=0.35, log=False))
        self.mode = md or name.upper()
        self._note(f"Mode {self.mode}")
        return self.snapshot

    def set_band(self, band_id: str) -> dict[str, Any]:
        band = next((row for row in BANDS if row["id"] == str(band_id)), None)
        if not band:
            raise ValueError(f"unknown band {band_id}")
        self.command(f"BS{band['code']};")
        self._note(f"Band {band['id']}")
        return self.poll()

    def set_power(self, watts: int) -> dict[str, Any]:
        watts = max(5, min(100, int(watts)))
        self.command(f"PC{watts:03d};")
        self.power = watts
        self._note(f"Power {watts} W")
        return self.poll()

    def power_off(self) -> dict[str, Any]:
        if not self._connected:
            raise RuntimeError("not connected")
        if self.transmitting:
            try:
                self.command("TX0;")
            except Exception:
                pass
            self.transmitting = False
        try:
            self.query("PS0;", timeout=0.35, log=True)
        except Exception:
            pass
        self.radio_on = False
        self._note("Radio power off")
        return self.snapshot

    def _wakeup_cat(self) -> None:
        """Yaesu CAT is asleep when the radio is off; a dummy ';' wakes the UART."""
        if not self._connected or self._port is None:
            return
        with self._lock:
            try:
                self._port.write(b"\x00;")
            except Exception:
                pass
            self._note("TX: (CAT wakeup)")

    def power_on(self) -> dict[str, Any]:
        if not self._connected:
            port = str(load_config().get("port") or "")
            if not port:
                raise RuntimeError("not connected")
            self.connect(port, demo=False)
        self.radio_on = False
        delay = 0.05 if self._demo else 0.8
        wait = 0.25 if self._demo else 1.0
        time.sleep(0.04)
        self._wakeup_cat()
        time.sleep(delay)
        try:
            self.query("PS1;", timeout=wait, log=True)
        except Exception:
            pass
        time.sleep(delay)
        try:
            ps = parse_ps(self.query("PS;", timeout=wait, log=True))
            if ps is not True:
                self.query("PS1;", timeout=wait, log=True)
                time.sleep(delay)
        except Exception:
            pass
        self.radio_on = True
        self._note("Radio power on")
        try:
            saved = None if self._demo else self._saved_af()
            if saved is not None:
                self.set_af(saved)
        except Exception:
            pass
        try:
            self.poll()
        except Exception:
            pass
        try:
            self._force_full_rf_gain()
        except Exception:
            pass
        self.set_utune(True)
        return self.snapshot

    def _saved_af(self) -> int | None:
        try:
            level = int(load_config().get("af"))
        except (TypeError, ValueError):
            return None
        return max(0, min(255, level))

    def _remember_af(self, level: int) -> None:
        if self._demo:
            return
        save_config({"af": max(0, min(255, int(level)))})

    def set_af(self, level: int) -> dict[str, Any]:
        level = max(0, min(255, int(level)))
        self.query(f"AG0{level:03d};", timeout=0.08, log=False)
        if abs(level - self.af) >= 8:
            self._note(f"AF {level}")
        self.af = level
        self._remember_af(level)
        return self.snapshot

    def _force_full_rf_gain(self) -> None:
        """Command RF gain to 100% and keep a stale read from putting the old value back."""
        pause = 0.02 if self._demo else 0.12
        for _ in range(3):
            self.set_rf_gain(FULL_RF_GAIN)
            time.sleep(pause)
            got = parse_rg(self.query("RG0;", timeout=0.45, log=False))
            if got == FULL_RF_GAIN:
                self.rf_gain = FULL_RF_GAIN
                self._rf_gain_hold_until = time.monotonic() + 2.0
                return
        self.rf_gain = FULL_RF_GAIN
        self._rf_gain_hold_until = time.monotonic() + 2.0
        self._note("RF gain 100%")

    def set_rf_gain(self, level: int) -> dict[str, Any]:
        level = max(0, min(255, int(level)))
        self.query(f"RG0{level:03d};", timeout=0.45, log=False)
        if abs(level - self.rf_gain) >= 8:
            self._note(f"RF gain {level}")
        self.rf_gain = level
        self._rf_gain_hold_until = time.monotonic() + 2.0
        return self.snapshot

    def set_tx(self, on: bool) -> dict[str, Any]:
        self.command("TX1;" if on else "TX0;")
        self.transmitting = bool(on)
        self._note("TX on" if on else "TX off")
        return self.poll()

    def swap_vfo(self) -> dict[str, Any]:
        self.command("SV;")
        self._note("Swap VFO")
        return self.poll()

    def copy_a_to_b(self) -> dict[str, Any]:
        self.command("AB;")
        self._note("A → B")
        return self.poll()

    def tuner(self, action: str) -> dict[str, Any]:
        if action == "start":
            self.command("AC002;")
            self._note("Tuner start")
        elif action == "on":
            self.command("AC001;")
            self._note("Tuner on")
        else:
            self.command("AC000;")
            self._note("Tuner off")
        return self.poll()

    def query(self, cmd: str, timeout: float = 0.6, log: bool = True) -> str:
        with self._lock:
            return self._transact(cmd, timeout, log=log)

    def command(self, cmd: str) -> str:
        return self.query(cmd)

    def _transact(self, cmd: str, timeout: float, log: bool = True) -> str:
        if not self._connected or self._port is None:
            raise RuntimeError("not connected")
        if not cmd.endswith(";"):
            cmd += ";"
        prefix = cmd[:2].upper()
        self._replies.clear()
        self._reply_event.clear()
        self._port.write(cmd.encode("ascii"))
        if log:
            self._note(f"TX: {cmd}")
        deadline = time.monotonic() + max(0.02, float(timeout))
        matched = ""
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            self._reply_event.wait(remaining)
            self._reply_event.clear()
            leftover: list[str] = []
            for reply in self._replies:
                head = reply[:2].upper()
                if not matched and head == prefix:
                    matched = reply
                elif head == "IF" and prefix != "IF":
                    continue
                else:
                    leftover.append(reply)
            self._replies = leftover
            if matched:
                break
        if log:
            if matched:
                self._note(f"RX: {matched};")
            else:
                self._note("RX: (no reply)")
        return matched

    def _read_loop(self) -> None:
        while not self._stop.is_set():
            port = self._port
            if port is None:
                break
            try:
                chunk = port.read(64)
            except Exception as exc:
                self._note(f"Serial error: {exc}")
                self._connected = False
                break
            if not chunk:
                continue
            self._buf.extend(chunk)
            replies, leftover = split_replies(bytes(self._buf))
            self._buf[:] = leftover
            if replies:
                self._replies.extend(replies)
                self._reply_event.set()

    def _meter_loop(self) -> None:
        ticks = 0
        while not self._meter_stop.is_set():
            if self._connected and self._port is not None and self.radio_on:
                try:
                    ticks += 1
                    if ticks % 3 == 0:
                        tx = self.query("TX;", timeout=0.12, log=False)
                        if tx:
                            self.transmitting = parse_tx(tx)
                    if self.transmitting:
                        po = parse_rm(self.query("RM5;", timeout=0.12, log=False) or "")
                        if po and po[0] == "5":
                            self.po_meter = po[1]
                        swr = parse_rm(self.query("RM6;", timeout=0.12, log=False) or "")
                        if swr and swr[0] == "6":
                            self.swr_meter = swr[1]
                    else:
                        sm = parse_sm(self.query("SM0;", timeout=0.12, log=False))
                        if sm is not None:
                            self.s_meter = sm
                except Exception:
                    pass
            self._meter_stop.wait(0.02)


def _tuner_com() -> str:
    try:
        raw = json.loads((Path.home() / ".at1000-control.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return ""
    return str((raw or {}).get("port") or "").strip().upper()


def preferred_radio_port(ports: list[dict[str, str]] | None = None, saved: str = "") -> str:
    """Pick the radio CAT port; never default to the AT-1000 tuner COM."""
    items = ports if ports is not None else list_ports()
    names = [str(p.get("device") or "") for p in items if p.get("device")]
    upper = [n.upper() for n in names]
    tuner = _tuner_com()
    saved_u = str(saved or "").strip().upper()
    if os.name != "nt" and saved_u.startswith("COM"):
        saved_u = ""
    if saved_u and saved_u in upper and saved_u != tuner:
        return names[upper.index(saved_u)]
    if os.name == "nt":
        usb = [n for n, u in zip(names, upper) if u.startswith("COM") and u not in {"COM3", tuner}]
        for want in ("COM4", "COM6", "COM7"):
            for n in usb:
                if n.upper() == want:
                    return n
        return usb[0] if usb else (saved or "")

    def rank(name: str) -> tuple[int, str]:
        u = name.upper()
        if u == tuner:
            return (9, u)
        blob = u
        if any(tag in blob for tag in ("YAESU", "FTDX", "FT-DX", "FT232", "CP210", "CH340")):
            return (0, u)
        if "/DEV/SERIAL/BY-ID/" in u:
            return (1, u)
        if "TTYUSB" in u:
            return (2, u)
        if "TTYACM" in u:
            return (3, u)
        return (8, u)

    ranked = [n for n in names if n.upper() != tuner]
    ranked.sort(key=rank)
    return ranked[0] if ranked else (saved or "")


def list_ports() -> list[dict[str, str]]:
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    return [
        {"device": item.device, "description": item.description or item.device}
        for item in list_ports.comports()
    ]
