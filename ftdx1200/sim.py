"""In-process fake FTDX-1200 for UI work without hardware."""

from __future__ import annotations

import threading
import time

from .cat import encode_hz


class SimulatedRadioPort:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rx = bytearray()
        self._cmd = bytearray()
        self._closed = False
        self.hz = 14_250_000
        self.hz_b = 14_200_000
        self.mode = "2"
        self.power = 20
        self.af = 128
        self.rf_gain = 255
        self.tx = False
        self.s_meter = 80
        self.antenna = "1"
        self.preamp = "1"
        self.tuner = "0"
        self.utune = "0"
        self.powered = True
        self._asleep = False

    def write(self, data: bytes) -> int:
        if not data:
            return 0
        self._cmd.extend(data)
        while True:
            try:
                idx = self._cmd.index(ord(";"))
            except ValueError:
                break
            cmd = bytes(self._cmd[: idx + 1]).decode("ascii", errors="ignore")
            del self._cmd[: idx + 1]
            self._handle(cmd)
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

    def close(self) -> None:
        self._closed = True

    def _queue(self, text: str) -> None:
        with self._lock:
            self._rx.extend(text.encode("ascii"))

    def _handle(self, cmd: str) -> None:
        if self._asleep and not self.powered:
            # First CAT bytes only wake the UART; the next command can power on.
            self._asleep = False
            return
        name = cmd[:2].upper()
        body = cmd[2:-1] if cmd.endswith(";") else cmd[2:]
        if name == "FA":
            if body:
                self.hz = int("".join(ch for ch in body if ch.isdigit()) or self.hz)
            self._queue(f"FA{encode_hz(self.hz)};")
        elif name == "FB":
            if body:
                self.hz_b = int("".join(ch for ch in body if ch.isdigit()) or self.hz_b)
            self._queue(f"FB{encode_hz(self.hz_b)};")
        elif name == "IF":
            self._queue(self._if())
        elif name == "MD":
            if len(body) >= 2:
                self.mode = body[1].upper()
            self._queue(f"MD0{self.mode};")
        elif name == "PC":
            if body:
                self.power = max(5, min(100, int(body)))
            self._queue(f"PC{self.power:03d};")
        elif name == "AG":
            if len(body) >= 4:
                self.af = max(0, min(255, int(body[1:4])))
            self._queue(f"AG0{self.af:03d};")
        elif name == "RG":
            if len(body) >= 4:
                self.rf_gain = max(0, min(255, int(body[1:4])))
            self._queue(f"RG0{self.rf_gain:03d};")
        elif name == "SM":
            level = self.s_meter
            if self.tx:
                level = 180
            self._queue(f"SM0{level:03d};")
        elif name == "TX":
            if body[:1] == "1":
                self.tx = True
            elif body[:1] == "0":
                self.tx = False
            self._queue("TX1;" if self.tx else "TX0;")
        elif name == "ID":
            self._queue("ID0583;")
        elif name == "BS":
            table = {
                "00": 1_800_000,
                "01": 3_500_000,
                "03": 7_000_000,
                "04": 10_100_000,
                "05": 14_000_000,
                "06": 18_068_000,
                "07": 21_000_000,
                "08": 24_890_000,
                "09": 28_000_000,
                "10": 50_000_000,
                "11": 15_000_000,
            }
            if body in table:
                self.hz = table[body]
            self._queue(self._if())
        elif name == "SV":
            self.hz, self.hz_b = self.hz_b, self.hz
            self._queue(self._if())
        elif name == "AB":
            self.hz_b = self.hz
        elif name == "AN":
            if len(body) >= 2:
                self.antenna = body[1]
            self._queue(f"AN0{self.antenna}0;")
        elif name == "PA":
            if len(body) >= 2:
                self.preamp = body[1]
            self._queue(f"PA0{self.preamp};")
        elif name == "PS":
            if body[:1] == "0":
                self.powered = False
                self._asleep = True
            elif body[:1] == "1":
                self.powered = True
            self._queue(f"PS{'1' if self.powered else '0'};")
        elif name == "VF":
            if len(body) >= 2 and body[1] in "012":
                self.utune = body[1]
            self._queue(f"VF0{self.utune}0000;")
        elif name == "RI":
            kind = body[:1] or "0"
            flag = self.utune if kind == "9" else "0"
            self._queue(f"RI{kind}{flag};")
        elif name == "AC":
            if len(body) >= 3:
                self.tuner = body[2]
            self._queue(f"AC00{self.tuner};")
        elif name == "RM":
            meter = body[:1] or "1"
            if not self.tx:
                val = 0
            elif meter == "5":
                val = max(0, min(255, int(round(self.power / 100 * 255))))
            elif meter == "6":
                val = 40
            else:
                val = 120
            self._queue(f"RM{meter}{val:03d};")

    def _if(self) -> str:
        return f"IF000{encode_hz(self.hz)}+000000{self.mode}00000;"
