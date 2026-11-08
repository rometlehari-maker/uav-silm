#!/usr/bin/env bash
# SILM Ground Station - start it on macOS or Linux (also Ubuntu in WSL2):
#     ./start_ground_station.sh
# The first start sets up its own Python environment (needs internet once).
# It then opens in your web browser at http://127.0.0.1:8765
set -e
cd "$(dirname "$0")"
PY="$(command -v python3 || command -v python || true)"
if [ -z "$PY" ]; then
  echo "Python 3 was not found. Install it (macOS: https://www.python.org/downloads/ ;"
  echo "Ubuntu: sudo apt install python3 python3-venv) and run this again."
  exit 1
fi
if [ ! -x .venv/bin/python ]; then
  echo "First start: setting up the Ground Station (needs internet once)..."
  "$PY" -m venv .venv || { echo "Could not create a Python environment. Ubuntu: sudo apt install python3-venv"; exit 1; }
  .venv/bin/python -m pip install --upgrade pip >/dev/null
  .venv/bin/python -m pip install -r groundstation/requirements.txt
fi
exec .venv/bin/python -m groundstation "$@"
