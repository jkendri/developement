#!/bin/bash
set -euo pipefail

# FTDX-1200 Control on Raspberry Pi (Python source, not the Windows .exe)
# Usage:
#   ./run-pi.sh              # venv + browser UI on this Pi
#   ./run-pi.sh --setup      # install OS packages + Python deps, then exit
#   ./run-pi.sh --lan        # listen on all interfaces (http://<pi-ip>:8770/)
#   ./run-pi.sh --window     # pywebview GTK window (needs WebKitGTK)
#   ./run-pi.sh --no-browser # server only
# Extra args are passed to the app.

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
VENV="$ROOT/.venv-pi"
PY="${PYTHON:-python3}"
HOST="${HOST:-127.0.0.1}"
APP_PORT="${APP_PORT:-8770}"
UI=(--browser)

need_cmd() {
  command -v "$1" >/dev/null 2>&1
}

setup_os() {
  echo "Installing Raspberry Pi packages (sudo)..."
  sudo apt-get update
  sudo apt-get install -y \
    python3 python3-venv python3-pip python3-dev \
    build-essential libportaudio2 libffi-dev \
    gir1.2-gtk-3.0 gir1.2-webkit2-4.1 \
    python3-gi python3-gi-cairo
}

setup_venv() {
  if ! need_cmd "$PY"; then
    echo "python3 is not installed. Run:  ./run-pi.sh --setup" >&2
    exit 1
  fi
  if [ ! -x "$VENV/bin/python" ]; then
    echo "Creating $VENV"
    "$PY" -m venv "$VENV"
  fi
  # shellcheck disable=SC1091
  source "$VENV/bin/activate"
  python -m pip install --upgrade pip
  python -m pip install -r "$ROOT/requirements.txt"
}

warn_serial() {
  if ! id -nG "$USER" | grep -qw dialout; then
    echo "Add this user to dialout so CAT USB serial works, then log out/in:"
    echo "  sudo usermod -aG dialout $USER"
  fi
  echo "Serial devices:"
  ls -l /dev/ttyUSB* /dev/ttyACM* /dev/serial/by-id 2>/dev/null || echo "  (none yet — plug in the radio adapter)"
}

ARGS=()
DO_SETUP=0
for arg in "$@"; do
  case "$arg" in
    --setup) DO_SETUP=1 ;;
    --lan) HOST="0.0.0.0" ;;
    --window) UI=(--window) ;;
    --no-browser) UI=(--no-browser) ;;
    --browser) UI=(--browser) ;;
    *) ARGS+=("$arg") ;;
  esac
done

if [ "$DO_SETUP" -eq 1 ]; then
  setup_os
  setup_venv
  warn_serial
  echo
  echo "Setup done. Start with:  ./run-pi.sh"
  echo "Other PC on the LAN:    ./run-pi.sh --lan"
  exit 0
fi

if [ ! -x "$VENV/bin/python" ]; then
  setup_venv
else
  # shellcheck disable=SC1091
  source "$VENV/bin/activate"
fi

warn_serial
echo "Starting FTDX-1200 on http://${HOST}:${APP_PORT}/"
if [ "${#ARGS[@]}" -eq 0 ]; then
  exec python -m ftdx1200 --host "$HOST" --port "$APP_PORT" "${UI[@]}"
fi
exec python -m ftdx1200 --host "$HOST" --port "$APP_PORT" "${UI[@]}" "${ARGS[@]}"
