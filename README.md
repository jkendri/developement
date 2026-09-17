# FTDX-1200 CAT Console

Minimal local app to open a serial link to a Yaesu FTDX-1200 and send CAT
commands with the reply shown on screen.

## Default serial settings (editable in the UI)

| Setting | Value |
|---------|-------|
| Port | `COM4` |
| Baud | `38400` |
| Data bits | `8` |
| Parity | None (`N`) |
| Stop bits | `2` (8N2) |

## Requirements

- Windows (typical for `COM4`) or another OS with the radio's serial device
- Python 3.10+
- A USB/serial cable to the FTDX-1200 with CAT enabled in the radio menu

## Install and run

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

Then open http://127.0.0.1:8765 in a browser.

1. Confirm port/baud (defaults are COM4 / 38400 / 8N2) and click **Connect**.
2. Type a CAT string (for example `ID;` or `FA;`) and click **Send**.
3. The radio response appears in the **Response** panel.

On Linux/macOS, set the port field to the device path (for example `/dev/ttyUSB0`) instead of `COM4`.

## Project layout

- `ftdx1200/config.py` — default serial settings
- `ftdx1200/serial_client.py` — open port, send CAT, read until `;`
- `ftdx1200/app.py` — small Flask UI + JSON API
- `run.py` — entry point
