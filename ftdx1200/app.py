"""Minimal FTDX-1200 CAT command console (local web UI)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from ftdx1200.config import DEFAULT_SERIAL, SerialSettings
from ftdx1200.serial_client import RadioSerial

_PACKAGE_DIR = Path(__file__).resolve().parent

app = Flask(
    __name__,
    template_folder=str(_PACKAGE_DIR / "templates"),
    static_folder=str(_PACKAGE_DIR / "static"),
)
radio = RadioSerial()


def _settings_from_request(data: dict) -> SerialSettings:
    """Build settings from JSON, falling back to FTDX-1200 defaults."""
    base = radio.settings if radio.is_open else DEFAULT_SERIAL
    return replace(
        base,
        port=str(data.get("port", base.port)),
        baudrate=int(data.get("baudrate", base.baudrate)),
        bytesize=int(data.get("bytesize", base.bytesize)),
        parity=str(data.get("parity", base.parity)),
        stopbits=float(data.get("stopbits", base.stopbits)),
        timeout=float(data.get("timeout", base.timeout)),
    )


@app.get("/")
def index():
    return render_template("index.html", settings=DEFAULT_SERIAL)


@app.get("/api/status")
def status():
    return jsonify(
        {
            "connected": radio.is_open,
            "settings": radio.settings.to_dict(),
        }
    )


@app.post("/api/connect")
def connect():
    data = request.get_json(silent=True) or {}
    settings = _settings_from_request(data)
    try:
        radio.connect(settings)
    except Exception as exc:  # noqa: BLE001 — surface to UI
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "settings": radio.settings.to_dict()})


@app.post("/api/disconnect")
def disconnect():
    radio.disconnect()
    return jsonify({"ok": True, "connected": False})


@app.post("/api/send")
def send():
    data = request.get_json(silent=True) or {}
    command = str(data.get("command", ""))
    try:
        response = radio.send_command(command)
    except Exception as exc:  # noqa: BLE001 — surface to UI
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "command": command, "response": response})


def main() -> None:
    app.run(host="127.0.0.1", port=8765, debug=False)


if __name__ == "__main__":
    main()
