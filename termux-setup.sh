#!/usr/bin/env bash
# =============================================================================
#  Suraj AI — Termux (Android) setup script
#
#  Runs the ENTIRE Suraj AI stack on your phone, inside Termux:
#    - Python + FastAPI server
#    - ADB (android-tools) to control THIS phone via wireless debugging
#    - Opens the dashboard in your phone's browser
#
#  No computer needed.  Root NOT required (Android 11+).
#
#  Usage:
#    bash termux-setup.sh          # full interactive setup
#    bash termux-setup.sh --start  # skip setup, just start the server
#    bash termux-setup.sh --pair   # just (re)pair ADB to this phone
# =============================================================================
set -euo pipefail

# colours
G="\033[1;32m"; Y="\033[1;33m"; C="\033[1;36m"; R="\033[1;31m"; B="\033[0m"

banner() {
  echo -e "${C}"
  echo "  ┌─────────────────────────────────────┐"
  echo "  │        Suraj AI — Termux setup      │"
  echo "  └─────────────────────────────────────┘"
  echo -e "${B}"
}

info()  { echo -e "${G}✓${B} $1"; }
warn()  { echo -e "${Y}!${B} $1"; }
err()   { echo -e "${R}✗${B} $1"; }
ask()   { echo -en "${Y}?${B} $1 "; read -r "$2"; }

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

banner

# ---------------------------------------------------------------------------
#  Phase 0 — quick-start flags
# ---------------------------------------------------------------------------
if [ "${1:-}" = "--start" ]; then
  info "Starting server only…"
  export SURAJ_NO_RELOAD=1
  exec python3 server.py
fi

if [ "${1:-}" = "--pair" ]; then
  exec bash "$ROOT/scripts/termux-adb-pair.sh"
fi

# ---------------------------------------------------------------------------
#  Phase 1 — check we're actually in Termux
# ---------------------------------------------------------------------------
if [ -z "${TERMUX_VERSION:-}" ] && ! command -v pkg &>/dev/null; then
  err "This script is for Termux on Android."
  err "On a desktop, use ./run.sh instead."
  exit 1
fi
info "Running in Termux ${TERMUX_VERSION:-} on $(uname -m)"

# ---------------------------------------------------------------------------
#  Phase 2 — install system packages
# ---------------------------------------------------------------------------
echo ""
echo -e "${C}[1/4] Installing system packages…${B}"
pkg update -y >/dev/null 2>&1 || true
# python-pip is deprecated in newer Termux (pip ships with python); install
# each package individually so a missing one doesn't abort the rest.
pkg install -y python        || warn "python install issue"
pkg install -y android-tools || warn "android-tools not available (needed for adb)"
pkg install -y git           || true
# ensure pip is available
python3 -m pip --version >/dev/null 2>&1 || pkg install -y python-pip || true
info "System packages ready"

# ---------------------------------------------------------------------------
#  Phase 3 — install Python dependencies (minimal, no openai)
# ---------------------------------------------------------------------------
echo ""
echo -e "${C}[2/4] Installing Python dependencies…${B}"
# On Termux, avoid uvloop / httptools (C extensions that may not build).
# Use plain uvicorn + websockets.  Pillow has Termux wheels.
DEPS="fastapi>=0.103 uvicorn>=0.23 websockets>=11 pillow>=10.0"

pip_install() {
  pip install --no-cache-dir "$@"
}

# Newer Termux (Python 3.11+) marks the env as "externally managed" (PEP 668)
# and refuses plain `pip install`.  --break-system-packages is the Termux fix.
if ! pip_install $DEPS 2>&1; then
  echo -e "${Y}!${B} Plain pip blocked — retrying with --break-system-packages…"
  if ! pip_install --break-system-packages $DEPS 2>&1; then
    err "Python dependency install failed. Manual run:"
    err "  pip install --break-system-packages fastapi uvicorn websockets pillow"
    exit 1
  fi
fi

# verify the imports actually work
python3 -c "import fastapi, uvicorn, PIL, websockets" 2>/dev/null || {
  err "Import check failed — one of fastapi/uvicorn/PIL/websockets is missing."
  err "Try:  pip install --break-system-packages fastapi uvicorn websockets pillow"
  exit 1
}
info "Python dependencies ready"

# ---------------------------------------------------------------------------
#  Phase 4 — ADB: pair with this phone's wireless debugging
# ---------------------------------------------------------------------------
echo ""
echo -e "${C}[3/4] Connecting ADB to this phone…${B}"

if ! command -v adb &>/dev/null; then
  warn "adb not installed — phone control will not work."
  warn "Install with:  pkg install android-tools"
else
  # Check if already connected
  if adb devices 2>/dev/null | grep -q "device$"; then
    info "ADB already connected to a device:"
    adb devices
  else
    echo -e "${Y}You need to enable Wireless Debugging on this phone:${B}"
    echo -e "  1. ${C}Settings → About phone${B} → tap ${Y}Build number${B} 7×"
    echo -e "  2. ${C}Settings → System → Developer options${B}"
    echo -e "  3. Enable ${Y}Wireless debugging${B}"
    echo ""
    ask "Done? Press Enter to continue pairing, or 's' to skip" DONE
    if [ "${DONE:-}" != "s" ]; then
      if [ -f "$ROOT/scripts/termux-adb-pair.sh" ]; then
        bash "$ROOT/scripts/termux-adb-pair.sh"
      else
        # inline fallback
        echo -e "${Y}Open Wireless debugging → 'Pair device with pairing code'${B}"
        ask "Enter the IP:PORT shown (e.g. 192.168.1.5:43210):" PAIR_ADDR
        ask "Enter the 6-digit pairing code:" PAIR_CODE
        adb pair "${PAIR_ADDR}" "${PAIR_CODE}" || warn "Pairing failed — see Wireless debugging settings"
        echo -e "${Y}Now use the IP:PORT from the main Wireless debugging screen (not the pair screen):${B}"
        ask "Enter the connect IP:PORT (e.g. 192.168.1.5:37521):" CONN_ADDR
        adb connect "${CONN_ADDR}" || warn "Connect failed"
        adb devices
      fi
    fi
  fi
fi

# ---------------------------------------------------------------------------
#  Phase 5 — start the server
# ---------------------------------------------------------------------------
echo ""
echo -e "${C}[4/4] Starting Suraj AI…${B}"
cat <<'INTRO'

  ┌────────────────────────────────────────────────────────┐
  │  Suraj AI is ready!                                     │
  │                                                         │
  │  Dashboard:  http://localhost:8000                      │
  │  (open this in your phone's browser)                    │
  │                                                         │
  │  Try saying:  "screenshot", "open whatsapp",           │
  │               "volume up", "go home"                    │
  └────────────────────────────────────────────────────────┘

INTRO

export SURAJ_NO_RELOAD=1        # file-watching reload is flaky on Android
export HOST="${HOST:-127.0.0.1}"
export PORT="${PORT:-8000}"

# optionally open the browser automatically
if command -v termux-open-url &>/dev/null; then
  (sleep 3 && termux-open-url "http://localhost:${PORT}") &
fi

exec python3 server.py
