"""Serial connection defaults for Yaesu FTDX-1200 (8N2)."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class SerialSettings:
    """COM port settings used when opening the radio connection."""

    port: str = "COM4"
    baudrate: int = 38400
    bytesize: int = 8  # serial.EIGHTBITS
    parity: str = "N"  # serial.PARITY_NONE
    stopbits: float = 2  # serial.STOPBITS_TWO
    timeout: float = 1.0
    write_timeout: float = 1.0

    def to_dict(self) -> dict:
        return asdict(self)


# Single shared defaults object — connection path reads from here.
DEFAULT_SERIAL = SerialSettings()
