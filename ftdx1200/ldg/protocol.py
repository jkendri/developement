"""LDG AT-1000ProII / AT-600ProII Ext. Meter jack protocol.

The rear 4-pin mini-DIN meter jack is a TTL (0–5 V) UART at 38400 8N1,
not RS-232. Use a USB-TTL adapter. Do not wire a standard RS-232 port
directly, and do not use an S-Video cable (pins can be bridged, and one
tuner pin is +12 V).

Commands are one ASCII byte. The tuner CPU sleeps, so send a wake byte
(space, 0x20) then wait ~1 ms before the command. Meter streaming ('S')
must be paused ('X') before reading command acknowledgements.

Meter frames: 2-byte BE forward ADC, 2-byte BE reflected ADC, 2-byte BE
frequency/band hint, then ASCII ';;' (0x3B 0x3B).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

BAUD = 38400
WAKE = b" "
EOM = b";;"
FRAME_PAYLOAD = 6

CMD_ANTENNA = b"A"
CMD_MEMORY_TUNE = b"T"
CMD_FULL_TUNE = b"F"
CMD_BYPASS = b"P"
CMD_AUTO = b"C"
CMD_MANUAL = b"M"
CMD_SYNC = b"Z"
CMD_METER = b"S"
CMD_CONTROL = b"X"

TUNE_RESULTS = {
    "T": ("GOOD", "Match 1.5:1 or better"),
    "M": ("OKAY", "Usable match, not a full 1.5:1"),
    "F": ("FAIL", "Could not find a match"),
    "E": ("ERROR", "No valid acknowledgement"),
}

BAND_RANGES: tuple[tuple[int, int, str], ...] = (
    (8230, 9145, "160m"),
    (4110, 4710, "80m"),
    (3060, 3080, "60m"),
    (2250, 2355, "40m"),
    (1600, 1630, "30m"),
    (1140, 1180, "20m"),
    (900, 915, "17m"),
    (766, 782, "15m"),
    (655, 662, "12m"),
    (550, 590, "10m"),
    (320, 360, "6m"),
)


@dataclass(frozen=True)
class MeterSample:
    fwd_raw: int
    refl_raw: int
    freq_raw: int
    fwd_w: float
    refl_w: float
    swr: float
    band: str


class FrameParser:
    """Reassemble 6-byte payloads terminated by ';;'."""

    def __init__(self) -> None:
        self._buf = bytearray(FRAME_PAYLOAD)
        self._idx = 0
        self._eom = 0
        self._error = False

    def reset(self) -> None:
        self._idx = 0
        self._eom = 0
        self._error = False

    def feed(self, data: bytes) -> list[bytes]:
        frames: list[bytes] = []
        for byte in data:
            if self._error:
                if byte == 0x3B:
                    self._eom += 1
                    if self._eom >= 2:
                        self.reset()
                else:
                    self._eom = 0
                continue
            if self._idx < FRAME_PAYLOAD:
                self._buf[self._idx] = byte
                self._idx += 1
            elif byte == 0x3B:
                self._eom += 1
                if self._eom == 2:
                    frames.append(bytes(self._buf))
                    self._idx = 0
                    self._eom = 0
            else:
                self._idx = 0
                self._eom = 0
                self._error = True
        return frames


def decode_u16_be(pair: bytes) -> int:
    return (pair[0] << 8) | pair[1]


def encode_u16_be(value: int) -> bytes:
    value &= 0xFFFF
    return bytes([(value >> 8) & 0xFF, value & 0xFF])


def adc_to_watts(raw: int, volt_ps: float = 13.8, watt_cal: float = 1.0) -> float:
    """Convert 16-bit ADC sample to watts.

    Same Ext. Meter / M-1000 coupler scaling as LDGControl: Bruene sample
    referred to the DC supply, coupler factor 1000, and 0.707 (peak↔RMS).
    Square that voltage and divide by 50 Ω, then apply watt_cal.
    """
    vrms = (1000.0 * volt_ps * raw) / (65536.0 * 0.707)
    return (vrms * vrms) / 50.0 * watt_cal


def swr_from_power(fwd_w: float, refl_w: float) -> float:
    if fwd_w <= 0.05:
        return 1.0
    if refl_w <= 0.0:
        return 1.0
    gamma = math.sqrt(max(0.0, min(0.999, refl_w / fwd_w)))
    return (1.0 + gamma) / (1.0 - gamma)


def band_from_freq_raw(freq_raw: int) -> str:
    for lo, hi, name in BAND_RANGES:
        if lo <= freq_raw <= hi:
            return name
    return "Unknown"


def decode_frame(
    payload: bytes, volt_ps: float = 13.8, watt_cal: float = 1.0
) -> MeterSample:
    if len(payload) != FRAME_PAYLOAD:
        raise ValueError("meter payload must be 6 bytes")
    fwd_raw = decode_u16_be(payload[0:2])
    refl_raw = decode_u16_be(payload[2:4])
    freq_raw = decode_u16_be(payload[4:6])
    fwd_w = adc_to_watts(fwd_raw, volt_ps, watt_cal)
    refl_w = adc_to_watts(refl_raw, volt_ps, watt_cal)
    return MeterSample(
        fwd_raw=fwd_raw,
        refl_raw=refl_raw,
        freq_raw=freq_raw,
        fwd_w=round(fwd_w, 1),
        refl_w=round(refl_w, 1),
        swr=round(swr_from_power(fwd_w, refl_w), 2),
        band=band_from_freq_raw(freq_raw),
    )


def build_frame(fwd_raw: int, refl_raw: int, freq_raw: int) -> bytes:
    return encode_u16_be(fwd_raw) + encode_u16_be(refl_raw) + encode_u16_be(freq_raw) + EOM
