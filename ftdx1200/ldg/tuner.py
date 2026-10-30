"""Serial (or simulated) session with an LDG AT-1000ProII."""

from __future__ import annotations

import json
import math
import threading
import time
from pathlib import Path
from typing import Any, Protocol

from .protocol import (
    BAUD,
    CMD_ANTENNA,
    CMD_AUTO,
    CMD_BYPASS,
    CMD_CONTROL,
    CMD_FULL_TUNE,
    CMD_MANUAL,
    CMD_MEMORY_TUNE,
    CMD_METER,
    CMD_SYNC,
    TUNE_RESULTS,
    WAKE,
    FrameParser,
    MeterSample,
    decode_frame,
)
from .sim import SimulatedTunerPort

CONFIG_PATH = Path.home() / ".at1000-control.json"
PEAK_HOLD_S = 1.7
PEAK_FLOOR_W = 0.8
PEAK_CATCH_W = 0.7
PEAK_CATCH_FRAC = 0.015
PEAK_DECAY_S = 0.55
PEAK_DROP_S = 0.16
PEAK_CLEAR_W = 0.18

COMMANDS = {
    "antenna": (CMD_ANTENNA, 2.0, "antenna"),
    "bypass": (CMD_BYPASS, 2.0, "bypass"),
    "auto": (CMD_AUTO, 2.0, "auto"),
    "manual": (CMD_MANUAL, 2.0, "manual"),
    "memory_tune": (CMD_MEMORY_TUNE, 8.0, "tune"),
    "full_tune": (CMD_FULL_TUNE, 20.0, "tune"),
    "sync": (CMD_SYNC, 2.0, "sync"),
}


def antenna_from_ack(payload: bytes) -> str:
    """Pick ANT 1/2 from a tuner reply, ignoring leftover meter bytes."""
    found = ""
    for byte in payload:
        if byte in (ord("1"), ord("2")):
            found = chr(byte)
        elif byte in (ord("A"), ord("B")) and not found:
            found = "1" if byte == ord("A") else "2"
    return found


class SerialLike(Protocol):
    def write(self, data: bytes) -> int: ...
    def read(self, size: int = 1) -> bytes: ...
    def close(self) -> None: ...


class TunerSession:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._port: SerialLike | None = None
        self._reader: threading.Thread | None = None
        self._stop = threading.Event()
        self._parser = FrameParser()
        self._mode = "idle"
        self._ack = bytearray()
        self._ack_event = threading.Event()
        self._sample: MeterSample | None = None
        self._peak_fwd = 0.0
        self._peak_refl = 0.0
        self._peak_swr = 1.0
        self._peak_fwd_raw = 0
        self._peak_at = 0.0
        self._peak_tick = 0.0
        self._sample_at = 0.0
        self._log: list[dict[str, Any]] = []
        self._connected = False
        self._demo = False
        self._port_name = ""
        cfg = load_config()
        try:
            self.volt_ps = float(cfg.get("volt_ps") or 13.8)
        except (TypeError, ValueError):
            self.volt_ps = 13.8
        try:
            self.watt_cal = float(cfg.get("watt_cal") or 1.0)
        except (TypeError, ValueError):
            self.watt_cal = 1.0
        if self.watt_cal <= 0:
            self.watt_cal = 1.0
        self.meter_mode = "peak" if cfg.get("meter_mode") != "average" else "average"
        self.antenna = ""
        saved = str(cfg.get("antenna") or "")
        if saved in {"1", "2"}:
            self.antenna = saved
        self.tune_mode = "manual"
        self.bypass = False
        self.last_tune: dict[str, str] | None = None
        self.auto_connect = bool(cfg.get("auto_connect"))

    def set_auto_connect(self, on: bool) -> None:
        self.auto_connect = bool(on)
        save_config({"auto_connect": self.auto_connect})
        self._note("Auto connect on" if self.auto_connect else "Auto connect off")

    def try_auto_connect(self) -> None:
        if not self.auto_connect or self._connected:
            return
        port = str(load_config().get("port") or "")
        if not port:
            self._note("Auto connect: no saved port")
            return
        try:
            self.connect(port, demo=False)
        except Exception as exc:
            self._note(f"Auto connect failed: {exc}")

    def set_meter_mode(self, mode: str) -> None:
        if mode not in {"peak", "average"}:
            raise ValueError("meter_mode must be peak or average")
        if mode == self.meter_mode:
            return
        self.meter_mode = mode
        save_config({"meter_mode": mode})
        self._note("Meter peak hold" if mode == "peak" else "Meter average")

    def set_volt_ps(self, volts: float) -> None:
        self.volt_ps = float(volts)
        save_config({"volt_ps": self.volt_ps})
        self._note(f"PSU {self.volt_ps:.1f} V")

    def set_watt_cal(self, cal: float) -> None:
        cal = float(cal)
        if cal <= 0:
            raise ValueError("watt_cal must be positive")
        self.watt_cal = max(0.05, min(20.0, cal))
        save_config({"watt_cal": self.watt_cal})
        self._note(f"Power cal ×{self.watt_cal:.3f}")

    def calibrate_watts(self, known_w: float) -> None:
        known_w = float(known_w)
        if known_w < 1.0:
            raise ValueError("Known power must be at least 1 W")
        sample = self._sample
        live = sample.fwd_w if sample is not None else 0.0
        if live < 0.5:
            raise ValueError("Key a steady carrier first, then calibrate.")
        factor = known_w / live
        self.set_watt_cal(self.watt_cal * factor)
        self._peak_fwd *= factor
        self._peak_refl *= factor
        if sample is not None:
            self._sample = MeterSample(
                fwd_raw=sample.fwd_raw,
                refl_raw=sample.refl_raw,
                freq_raw=sample.freq_raw,
                fwd_w=round(sample.fwd_w * factor, 1),
                refl_w=round(sample.refl_w * factor, 1),
                swr=sample.swr,
                band=sample.band,
            )

    @property
    def snapshot(self) -> dict[str, Any]:
        self._decay_peak()
        sample = self._sample
        peak = self.meter_mode == "peak"
        return {
            "connected": self._connected,
            "demo": self._demo,
            "port": self._port_name,
            "mode": self._mode,
            "volt_ps": self.volt_ps,
            "watt_cal": round(self.watt_cal, 4),
            "meter_mode": self.meter_mode,
            "antenna": self.antenna,
            "tune_mode": self.tune_mode,
            "bypass": self.bypass,
            "last_tune": self.last_tune,
            "auto_connect": self.auto_connect,
            "meter": None
            if sample is None
            else {
                "fwd_w": round(self._peak_fwd if peak else sample.fwd_w, 1),
                "fwd_live_w": sample.fwd_w,
                "fwd_peak_w": round(self._peak_fwd, 1),
                "refl_w": round(self._peak_refl if peak else sample.refl_w, 1),
                "refl_live_w": sample.refl_w,
                "swr": round(self._peak_swr if peak else sample.swr, 2),
                "swr_live": sample.swr,
                "band": sample.band,
                "fwd_raw": (self._peak_fwd_raw or sample.fwd_raw) if peak else sample.fwd_raw,
                "refl_raw": sample.refl_raw,
                "freq_raw": sample.freq_raw,
            },
            "log": self._log[-40:],
        }

    def _note(self, message: str) -> None:
        self._log.append({"t": time.strftime("%H:%M:%S"), "msg": message})
        self._log = self._log[-80:]

    def connect(self, port_name: str, demo: bool = False) -> None:
        self.disconnect()
        if demo or port_name.lower() in {"demo", "sim", "simulator"}:
            self._port = SimulatedTunerPort()
            self._demo = True
            self._port_name = "DEMO"
            self.antenna = "1"
        else:
            import serial

            ser = serial.Serial(
                port=port_name,
                baudrate=BAUD,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.05,
                write_timeout=1.0,
                xonxoff=False,
                rtscts=False,
                dsrdtr=False,
            )
            self._port = ser
            self._demo = False
            self._port_name = port_name
            save_config({"port": port_name})
        self._stop.clear()
        self._parser.reset()
        self._sample = None
        self._peak_fwd = self._peak_refl = 0.0
        self._peak_swr = 1.0
        self._peak_fwd_raw = 0
        self._peak_at = 0.0
        self._peak_tick = 0.0
        self._sample_at = 0.0
        self._connected = True
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self._enter_meter()
        self._note(f"Connected on {self._port_name} at {BAUD} 8N1")
        try:
            self.command("manual")
        except Exception as exc:
            self.tune_mode = "manual"
            self._note(f"Could not force Manual: {exc}")

    def disconnect(self) -> None:
        self._stop.set()
        port = self._port
        self._port = None
        self._connected = False
        if self._reader and self._reader.is_alive() and threading.current_thread() is not self._reader:
            self._reader.join(timeout=1.0)
        self._reader = None
        if port is not None:
            try:
                port.close()
            except OSError:
                pass
        self._mode = "idle"
        self._note("Disconnected")

    def command(self, name: str) -> dict[str, Any]:
        if name not in COMMANDS:
            raise ValueError(f"unknown command {name}")
        if not self._connected or self._port is None:
            raise RuntimeError("not connected")
        cmd, timeout, kind = COMMANDS[name]
        with self._lock:
            ack = self._run_command(cmd, timeout, name)
        text = ack.decode("ascii", errors="replace").strip()
        self._apply_ack(name, text, kind)
        return {"command": name, "ack": text, "status": self.last_tune}

    def _run_command(self, cmd: bytes, timeout: float, name: str = "") -> bytes:
        assert self._port is not None
        self._mode = "control"
        self._send(CMD_CONTROL)
        time.sleep(0.08)
        reset = getattr(self._port, "reset_input_buffer", None)
        if callable(reset):
            reset()
        self._ack.clear()
        self._ack_event.clear()
        self._send(cmd)
        got = self._ack_event.wait(timeout)
        payload = bytes(self._ack)
        label = f" ({name})" if name else ""
        self._note(f"TX: {self._bytes_label(CMD_CONTROL)} then {self._bytes_label(cmd)}{label}")
        if not got and not payload:
            self._note("RX: (no ack)")
            self._enter_meter()
            return b"E"
        self._note(f"RX: {self._bytes_label(payload)}")
        self._enter_meter()
        return payload

    def _enter_meter(self) -> None:
        self._parser.reset()
        self._mode = "meter"
        self._send(CMD_METER)
        self._note(f"TX: {self._bytes_label(CMD_METER)}")

    def _send(self, cmd: bytes) -> None:
        assert self._port is not None
        self._port.write(WAKE)
        time.sleep(0.001)
        self._port.write(cmd)
        time.sleep(0.25)

    @staticmethod
    def _bytes_label(data: bytes) -> str:
        if not data:
            return "(empty)"
        if all(32 <= byte < 127 for byte in data):
            return repr(data.decode("ascii"))
        printable = data.decode("ascii", errors="replace")
        return f"{printable!r} [{data.hex(' ').upper()}]"

    def _read_loop(self) -> None:
        while not self._stop.is_set():
            port = self._port
            if port is None:
                break
            try:
                chunk = port.read(64)
            except Exception as exc:  # serial I/O can fail on unplug
                self._note(f"Serial error: {exc}")
                self._connected = False
                break
            if not chunk:
                continue
            if self._mode == "meter":
                for payload in self._parser.feed(chunk):
                    sample = decode_frame(payload, self.volt_ps, self.watt_cal)
                    self._sample = sample
                    self._sample_at = time.monotonic()
                    self._update_peak(sample)
            else:
                self._ack.extend(chunk)
                if any(b >= 0x30 for b in chunk):
                    self._ack_event.set()

    def _capture_peak(self, sample: MeterSample, now: float) -> None:
        self._peak_fwd = sample.fwd_w
        self._peak_refl = sample.refl_w
        self._peak_swr = sample.swr
        self._peak_fwd_raw = sample.fwd_raw
        self._peak_at = now
        self._peak_tick = now

    def _clear_peak(self, now: float) -> None:
        self._peak_fwd = 0.0
        self._peak_refl = 0.0
        self._peak_swr = 1.0
        self._peak_fwd_raw = 0
        self._peak_at = now
        self._peak_tick = now

    def _peak_should_catch(self, watts: float) -> bool:
        if watts <= 0.0:
            return False
        if self._peak_fwd < PEAK_FLOOR_W:
            return watts >= self._peak_fwd
        need = max(PEAK_CATCH_W, self._peak_fwd * PEAK_CATCH_FRAC)
        return watts > self._peak_fwd + need

    def _update_peak(self, sample: MeterSample, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        if self._peak_should_catch(sample.fwd_w):
            jump = sample.fwd_w - self._peak_fwd
            if sample.fwd_w >= 1.0 and jump >= max(2.0, self._peak_fwd * 0.08):
                self._note(f"Forward {sample.fwd_w:.1f} W (ADC {sample.fwd_raw})")
            self._capture_peak(sample, now)
            return
        self._decay_peak(now)

    def _decay_peak(self, now: float | None = None) -> None:
        if self._peak_fwd <= 0.0:
            return
        now = time.monotonic() if now is None else now
        dt = max(0.0, now - self._peak_tick) if self._peak_tick else 0.0
        self._peak_tick = now
        if now - self._peak_at < PEAK_HOLD_S:
            return
        live = self._sample.fwd_w if self._sample is not None else 0.0
        live_refl = self._sample.refl_w if self._sample is not None else 0.0
        live_swr = self._sample.swr if self._sample is not None else 1.0
        stale = self._sample_at > 0.0 and (now - self._sample_at) > PEAK_HOLD_S
        rf_gone = stale or live < PEAK_FLOOR_W
        tau = PEAK_DROP_S if rf_gone else PEAK_DECAY_S
        k = 1.0 - math.exp(-dt / tau) if dt > 0.0 else 0.0
        target_fwd = 0.0 if rf_gone else live
        target_refl = 0.0 if rf_gone else live_refl
        target_swr = 1.0 if rf_gone else live_swr
        self._peak_fwd += (target_fwd - self._peak_fwd) * k
        self._peak_refl += (target_refl - self._peak_refl) * k
        self._peak_swr += (target_swr - self._peak_swr) * k
        if rf_gone:
            self._peak_fwd_raw = int(round(self._peak_fwd_raw * (1.0 - k)))
        if self._peak_fwd < PEAK_CLEAR_W:
            self._clear_peak(now)

    def _apply_ack(self, name: str, text: str, kind: str) -> None:
        first = text[:1] if text else ""
        if kind == "antenna":
            ant = antenna_from_ack(text.encode("latin-1", errors="ignore"))
            if ant:
                self.antenna = ant
                save_config({"antenna": ant})
                self._note(f"Antenna {ant}")
            else:
                self._note(f"Antenna reply {text!r}")
        elif kind == "bypass":
            self.bypass = True
            self.tune_mode = "bypass"
            self._note("Bypass")
        elif kind == "auto":
            self.bypass = False
            self.tune_mode = "auto"
            self._note("Auto mode")
        elif kind == "manual":
            self.tune_mode = "manual"
            self._note("Manual mode")
        elif kind == "tune":
            code = first if first in TUNE_RESULTS else "E"
            label, detail = TUNE_RESULTS[code]
            self.last_tune = {"code": code, "label": label, "detail": detail}
            self._note(f"{name}: {label}")
        elif kind == "sync":
            self._note("Sync ok" if text else "Sync failed")
        else:
            self._note(f"{name} ack={text!r}")


def list_ports() -> list[dict[str, str]]:
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    ports = []
    for item in list_ports.comports():
        ports.append(
            {
                "device": item.device,
                "description": item.description or "",
                "hwid": item.hwid or "",
            }
        )
    return ports


def load_config() -> dict[str, Any]:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def save_config(data: dict[str, Any]) -> None:
    current = load_config()
    current.update(data)
    CONFIG_PATH.write_text(json.dumps(current, indent=2), encoding="utf-8")
