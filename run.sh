#!/usr/bin/env bash
# =============================================================================
#  Suraj AI — launcher
#  Starts the FastAPI server with uvicorn.
# =============================================================================
set -euo pipefail

cd "$(dirname "$0")"

# ---- load .env if present -------------------------------------------------
if [ -f .env ]; then
  set -a
  source .env
  set +a
fi

# ---- check Python ---------------------------------------------------------
if ! command -v python3 &>/dev/null; then
  echo "ERROR: python3 not found. Install Python 3.10+."
  exit 1
fi

# ---- check deps ------------------------------------------------------------
echo "📦 Checking dependencies…"
python3 -c "import fastapi, uvicorn, PIL" 2>/dev/null || {
  echo "   installing…"
  pip3 install -r requirements.txt
}

# ---- check adb -------------------------------------------------------------
if ! command -v adb &>/dev/null; then
  echo ""
  echo "⚠️  WARNING: 'adb' not found on PATH."
  echo "   The server will start but can't control a phone until you install"
  echo "   Android platform-tools:"
  echo "     https://developer.android.com/tools"
  echo ""
fi

# ---- start -----------------------------------------------------------------
PORT="${PORT:-8000}"
HOST="${HOST:-0.0.0.0}"

echo ""
echo "📱 Suraj AI starting on http://$HOST:$PORT"
echo "   Open this URL in your browser to access the dashboard."
echo ""

export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}$(pwd)"
exec python3 server.py
