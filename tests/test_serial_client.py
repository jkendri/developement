"""Unit tests for CAT serial helpers (no hardware required)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from ftdx1200.config import DEFAULT_SERIAL
from ftdx1200.serial_client import RadioSerial


class ConfigTests(unittest.TestCase):
    def test_defaults_match_ftdx1200(self) -> None:
        self.assertEqual(DEFAULT_SERIAL.port, "COM4")
        self.assertEqual(DEFAULT_SERIAL.baudrate, 38400)
        self.assertEqual(DEFAULT_SERIAL.bytesize, 8)
        self.assertEqual(DEFAULT_SERIAL.parity, "N")
        self.assertEqual(DEFAULT_SERIAL.stopbits, 2)


class SerialClientTests(unittest.TestCase):
    @patch("ftdx1200.serial_client.serial.Serial")
    def test_connect_uses_defaults(self, serial_cls: MagicMock) -> None:
        port = MagicMock()
        serial_cls.return_value = port
        radio = RadioSerial()
        radio.connect()
        serial_cls.assert_called_once()
        kwargs = serial_cls.call_args.kwargs
        self.assertEqual(kwargs["port"], "COM4")
        self.assertEqual(kwargs["baudrate"], 38400)
        self.assertEqual(kwargs["bytesize"], 8)
        self.assertEqual(kwargs["parity"], "N")
        self.assertEqual(kwargs["stopbits"], 2)

    @patch("ftdx1200.serial_client.serial.Serial")
    def test_send_appends_terminator_and_reads_response(
        self, serial_cls: MagicMock
    ) -> None:
        port = MagicMock()
        port.is_open = True
        port.read.side_effect = [b"I", b"D", b"0", b"6", b"7", b";"]
        serial_cls.return_value = port

        radio = RadioSerial()
        radio.connect()
        response = radio.send_command("ID")

        port.write.assert_called_once_with(b"ID;")
        self.assertEqual(response, "ID067;")


if __name__ == "__main__":
    unittest.main()
