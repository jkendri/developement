"""Open and talk to the FTDX-1200 over serial CAT."""

from __future__ import annotations

from typing import Optional

import serial

from ftdx1200.config import DEFAULT_SERIAL, SerialSettings

# Yaesu CAT frames end with semicolon.
CAT_TERMINATOR = b";"


class RadioSerial:
    """Thin wrapper around pyserial for CAT send/receive."""

    def __init__(self, settings: Optional[SerialSettings] = None) -> None:
        self.settings = settings or DEFAULT_SERIAL
        self._port: Optional[serial.Serial] = None

    @property
    def is_open(self) -> bool:
        return self._port is not None and self._port.is_open

    def connect(self, settings: Optional[SerialSettings] = None) -> None:
        if settings is not None:
            self.settings = settings
        self.disconnect()
        s = self.settings
        self._port = serial.Serial(
            port=s.port,
            baudrate=s.baudrate,
            bytesize=s.bytesize,
            parity=s.parity,
            stopbits=s.stopbits,
            timeout=s.timeout,
            write_timeout=s.write_timeout,
        )

    def disconnect(self) -> None:
        if self._port is not None:
            try:
                if self._port.is_open:
                    self._port.close()
            finally:
                self._port = None

    def send_command(self, command: str) -> str:
        """Send a CAT command string and return the radio's response text.

        Appends ';' if the caller omitted the terminator. Reads until ';'
        or until the serial read timeout elapses.
        """
        if not self.is_open or self._port is None:
            raise RuntimeError("Serial port is not open")

        payload = command.strip()
        if not payload:
            raise ValueError("Command is empty")
        if not payload.endswith(";"):
            payload += ";"

        self._port.reset_input_buffer()
        self._port.write(payload.encode("ascii", errors="strict"))
        self._port.flush()

        chunks: list[bytes] = []
        while True:
            byte = self._port.read(1)
            if not byte:
                break
            chunks.append(byte)
            if byte == CAT_TERMINATOR:
                break

        return b"".join(chunks).decode("ascii", errors="replace")
