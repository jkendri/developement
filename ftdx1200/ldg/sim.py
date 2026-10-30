"""In-process fake tuner for UI development without hardware."""

from __future__ import annotations

import math
import threading
import time

from .protocol import BAND_RANGES, build_frame


class SimulatedTunerPort:
    """Minimal serial-like object: write() / read() / close()."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rx = bytearray()
        self._meter = False
        self._antenna = 1
        self._mode = "auto"
        self._bypass = False
        self._closed = False
        self._t0 = time.monotonic()
        self._thread = threading.Thread(target=self._emit, daemon=True)
        self._thread.start()

    def write(self, data: bytes) -> int:
        if not data:
            return 0
        cmd = data[-1:]
        if cmd == b"S":
            self._meter = True
        elif cmd == b"X":
            self._meter = False
            with self._lock:
                self._rx.clear()
        elif cmd == b"A":
            self._antenna = 2 if self._antenna == 1 else 1
            self._queue(str(self._antenna).encode())
        elif cmd == b"P":
            self._bypass = True
            self._queue(b"P")
        elif cmd == b"C":
            self._bypass = False
            self._mode = "auto"
            self._queue(b"C")
        elif cmd == b"M":
            self._mode = "manual"
            self._queue(b"M")
        elif cmd == b"T":
            time.sleep(0.15)
            self._queue(b"T")
        elif cmd == b"F":
            time.sleep(0.25)
            self._queue(b"T")
        elif cmd == b"Z":
            self._queue(b"000000000000000AzAz")
        return len(data)

    def read(self, size: int = 1) -> bytes:
        deadline = time.monotonic() + 0.05
        while time.monotonic() < deadline:
            with self._lock:
                if self._rx:
                    n = min(size, len(self._rx))
                    chunk = bytes(self._rx[:n])
                    del self._rx[:n]
                    return chunk
            time.sleep(0.005)
        return b""

    def reset_input_buffer(self) -> None:
        with self._lock:
            self._rx.clear()

    def close(self) -> None:
        self._closed = True
        self._meter = False

    def _queue(self, data: bytes) -> None:
        with self._lock:
            self._rx.extend(data)

    def _emit(self) -> None:
        while not self._closed:
            if self._meter:
                t = time.monotonic() - self._t0
                cycle = (math.sin(t * 0.7) + 1) / 2
                # ADC counts that map to ~40–240 W forward at 13.8 V.
                fwd = int(150 + 220 * cycle)
                refl = int(25 + 35 * (1 - cycle))
                band_idx = int(t / 8) % len(BAND_RANGES)
                lo, hi, _ = BAND_RANGES[band_idx]
                freq = (lo + hi) // 2
                self._queue(build_frame(fwd, refl, freq))
            time.sleep(0.12)
