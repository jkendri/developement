"""Live spectrum from an RTL-SDR dongle, centered on the radio frequency."""

from __future__ import annotations

import ctypes
import os
import sys
import threading
from ctypes import POINTER, byref, c_char_p, c_int, c_uint32, c_void_p
from pathlib import Path
from typing import Callable

import numpy as np

SAMPLE_RATE = 2_400_000
SPAN_HZ = 100_000
FFT_N = 4096
AVERAGES = 4
BINS = 480
SMOOTH = 0.35
DIRECT_BELOW_HZ = 24_000_000
TUNER_MAX_HZ = 1_766_000_000
# Dongle reads high. 1.5 kHz still left the trace a little low; 1 kHz put it 2 kHz high.
DONGLE_HIGH_HZ = 1_250
# Fixed spurs in the dongle's own tuning frame. Each entry is blanked ± this many Hz.
BIRDIE_HZ = (14_321_000,)
BIRDIE_HALF_HZ = 2_000
BIN_HZ = SAMPLE_RATE / FFT_N
DISPLAY_BINS = int(round(SPAN_HZ / BIN_HZ))
if DISPLAY_BINS % 2 == 0:
    DISPLAY_BINS -= 1
DISPLAY_SPAN_HZ = DISPLAY_BINS * BIN_HZ


def _blank_narrow_spikes(db: np.ndarray, margin: float = 8.0) -> np.ndarray:
    """Drop one-bin spikes. A signal a few kHz wide stays."""
    out = db
    for _ in range(2):
        left = out[:-2]
        mid = out[1:-1]
        right = out[2:]
        spur = (mid > left + margin) & (mid > right + margin)
        if not np.any(spur):
            break
        out = out.copy()
        out[1:-1] = np.where(spur, (left + right) * 0.5, mid)
    return out


def _blank_fixed_birdies(flat: np.ndarray, center_hz: float) -> np.ndarray:
    """Replace a known spur with a straight line between its shoulders."""
    n = flat.size
    if n < 4 or not BIRDIE_HZ:
        return flat
    bin_hz = SAMPLE_RATE / n
    half_bins = max(1, int(round(BIRDIE_HALF_HZ / bin_hz)))
    out = flat
    for birdie in BIRDIE_HZ:
        center_bin = (n // 2) + (birdie - center_hz) / bin_hz
        if not np.isfinite(center_bin):
            continue
        lo = int(round(center_bin)) - half_bins
        hi = int(round(center_bin)) + half_bins
        if hi < 1 or lo > n - 2:
            continue
        lo = max(1, lo)
        hi = min(n - 2, hi)
        left = float(out[lo - 1])
        right = float(out[hi + 1])
        span = hi - lo + 2
        if out is flat:
            out = flat.copy()
        ramp = np.linspace(left, right, span + 1)[1:-1]
        out[lo : hi + 1] = ramp
    return out


def _display_bins(power: np.ndarray, center_hz: float) -> list[float]:
    mid = power.size // 2
    db = 10.0 * np.log10(power + 1e-12)
    kernel = np.ones(49, dtype=np.float64) / 49.0
    baseline = np.convolve(db, kernel, mode="same")
    flat = _blank_fixed_birdies(_blank_narrow_spikes(db - baseline), center_hz)
    keep = min(DISPLAY_BINS, flat.size if flat.size % 2 else flat.size - 1)
    start = mid - keep // 2
    flat = flat[start : start + keep]
    columns = min(BINS, flat.size)
    edges = np.linspace(0, flat.size, columns + 1).astype(int)
    out = [0.0] * columns
    for i in range(columns):
        sl = flat[edges[i] : max(edges[i] + 1, edges[i + 1])]
        out[i] = round(float(np.mean(sl)), 1)
    return out


def _dll_dir() -> Path:
    packaged = Path(__file__).resolve().parent / "rtl"
    if (packaged / "rtlsdr.dll").exists():
        return packaged
    if getattr(sys, "frozen", False):
        cand = Path(getattr(sys, "_MEIPASS", "")) / "rtl"
        if (cand / "rtlsdr.dll").exists():
            return cand
    return packaged


class Panadapter:
    def __init__(self, freq_hz: Callable[[], int]) -> None:
        self._freq_hz = freq_hz
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._snap: dict = {
            "ok": False,
            "status": "Opening the RTL-SDR",
            "center": 0,
            "span": DISPLAY_SPAN_HZ,
            "direct": False,
            "bins": [],
        }
        self._lib = None
        self._dev = c_void_p()
        self._avg: np.ndarray | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="panadapter", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive() and threading.current_thread() is not thread:
            thread.join(timeout=2.0)
        self._close()

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._snap)

    def _publish(self, **fields) -> None:
        with self._lock:
            self._snap = {**self._snap, **fields}

    def _load(self):
        if self._lib is not None:
            return self._lib
        folder = _dll_dir()
        dll = folder / "rtlsdr.dll"
        if not dll.exists():
            raise FileNotFoundError("rtlsdr.dll is not installed with SHACKBOARD")
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(str(folder))
        lib = ctypes.WinDLL(str(dll))
        lib.rtlsdr_get_device_count.restype = c_uint32
        lib.rtlsdr_get_device_name.argtypes = [c_uint32]
        lib.rtlsdr_get_device_name.restype = c_char_p
        lib.rtlsdr_open.argtypes = [POINTER(c_void_p), c_uint32]
        lib.rtlsdr_open.restype = c_int
        lib.rtlsdr_close.argtypes = [c_void_p]
        lib.rtlsdr_close.restype = c_int
        lib.rtlsdr_set_sample_rate.argtypes = [c_void_p, c_uint32]
        lib.rtlsdr_set_sample_rate.restype = c_int
        lib.rtlsdr_set_center_freq.argtypes = [c_void_p, c_uint32]
        lib.rtlsdr_set_center_freq.restype = c_int
        lib.rtlsdr_set_agc_mode.argtypes = [c_void_p, c_int]
        lib.rtlsdr_set_agc_mode.restype = c_int
        lib.rtlsdr_set_direct_sampling.argtypes = [c_void_p, c_int]
        lib.rtlsdr_set_direct_sampling.restype = c_int
        lib.rtlsdr_reset_buffer.argtypes = [c_void_p]
        lib.rtlsdr_reset_buffer.restype = c_int
        lib.rtlsdr_read_sync.argtypes = [c_void_p, ctypes.c_void_p, c_int, POINTER(c_int)]
        lib.rtlsdr_read_sync.restype = c_int
        self._lib = lib
        return lib

    def _close(self) -> None:
        dev = self._dev
        lib = self._lib
        self._dev = c_void_p()
        if lib is not None and dev.value:
            try:
                lib.rtlsdr_close(dev)
            except Exception:
                pass

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    self._session()
                except Exception as exc:
                    self._close()
                    message = str(exc) or exc.__class__.__name__
                    self._publish(ok=False, bins=[], error=message, status=message)
                    self._stop.wait(1.5)
        finally:
            self._close()

    def _session(self) -> None:
        lib = self._load()
        if lib.rtlsdr_get_device_count() < 1:
            self._publish(
                ok=False,
                bins=[],
                error="No RTL-SDR is plugged in",
                status="No RTL-SDR is plugged in",
            )
            self._stop.wait(1.5)
            return
        dev = c_void_p()
        rc = lib.rtlsdr_open(byref(dev), 0)
        if rc != 0:
            message = f"Could not open the RTL-SDR ({rc})"
            self._publish(ok=False, bins=[], error=message, status=message)
            self._stop.wait(1.5)
            return
        self._dev = dev
        raw_name = lib.rtlsdr_get_device_name(0) or b"RTL-SDR"
        device = raw_name.decode("utf-8", "replace")
        if lib.rtlsdr_set_sample_rate(dev, SAMPLE_RATE) != 0:
            raise RuntimeError("Could not set the RTL-SDR sample rate")
        lib.rtlsdr_set_agc_mode(dev, 1)
        tuned = 0
        direct = -1
        self._avg = None
        window = np.hanning(FFT_N).astype(np.float32)
        buf_n = FFT_N * 2 * AVERAGES
        buf = (ctypes.c_uint8 * buf_n)()
        while not self._stop.is_set():
            radio_hz = int(self._freq_hz() or 14_250_000)
            radio_hz = max(500_000, min(TUNER_MAX_HZ - DONGLE_HIGH_HZ, radio_hz))
            want = radio_hz + DONGLE_HIGH_HZ
            use_direct = 2 if radio_hz < DIRECT_BELOW_HZ else 0
            if use_direct != direct:
                if lib.rtlsdr_set_direct_sampling(dev, use_direct) != 0:
                    raise RuntimeError("Could not set RTL-SDR direct sampling")
                direct = use_direct
                tuned = 0
            if tuned == 0 or abs(want - tuned) > 20:
                if lib.rtlsdr_set_center_freq(dev, want) != 0:
                    raise RuntimeError("Could not tune the RTL-SDR")
                tuned = want
                self._avg = None
                lib.rtlsdr_reset_buffer(dev)
            got = c_int()
            rc = lib.rtlsdr_read_sync(dev, buf, buf_n, byref(got))
            if rc != 0 or got.value < FFT_N * 2:
                raise RuntimeError("The RTL-SDR stopped sending samples")
            samples = np.frombuffer(buf, dtype=np.uint8, count=got.value).astype(np.float32)
            samples -= 127.4
            pairs = (samples.size // 2) // FFT_N * FFT_N
            if pairs < FFT_N:
                continue
            iq = samples[: pairs * 2]
            i_samples = iq[0::2]
            q_samples = iq[1::2]
            i_samples -= float(np.mean(i_samples))
            q_samples -= float(np.mean(q_samples))
            complex_samples = i_samples + 1j * q_samples
            acc = None
            used = 0
            for off in range(0, complex_samples.size - FFT_N + 1, FFT_N):
                chunk = complex_samples[off : off + FFT_N] * window
                spec = np.fft.fftshift(np.fft.fft(chunk))
                power = spec.real * spec.real + spec.imag * spec.imag
                acc = power if acc is None else acc + power
                used += 1
            if acc is None or used == 0:
                continue
            acc /= used
            if self._avg is None or self._avg.shape != acc.shape:
                self._avg = acc
            else:
                self._avg = self._avg * (1.0 - SMOOTH) + acc * SMOOTH
            out = _display_bins(self._avg, tuned)
            self._publish(
                ok=True,
                error="",
                status="live",
                device=device,
                center=tuned - DONGLE_HIGH_HZ,
                span=DISPLAY_SPAN_HZ,
                direct=bool(direct),
                bins=out,
            )
