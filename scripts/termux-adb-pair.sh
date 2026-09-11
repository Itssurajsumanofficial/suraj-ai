#!/usr/bin/env bash
# =============================================================================
#  termux-adb-pair.sh — pair ADB with THIS phone's wireless debugging
#
#  Android 11+ lets you control your own phone from Termux via wireless ADB.
#  This script walks you through the pairing flow.
#
#  Prerequisite:
#    Settings → System → Developer options → enable "Wireless debugging"
# =============================================================================
set -euo pipefail

G="\033[1;32m"; Y="\033[1;33m"; C="\033[1;36m"; R="\033[1;31m"; B="\033[0m"
info()  { echo -e "${G}✓${B} $1"; }
warn()  { echo -e "${Y}!${B} $1"; }
err()   { echo -e "${R}✗${B} $1"; }
ask()   { echo -en "${Y}?${B} $1 "; read -r "$2"; }

if ! command -v adb &>/dev/null; then
  err "adb not found. Run:  pkg install android-tools"
  exit 1
fi

echo -e "${C}"
echo "  ┌─────────────────────────────────────────────┐"
echo "  │  ADB Wireless Debugging — Self-Pairing       │"
echo "  └─────────────────────────────────────────────┘"
echo -e "${B}"

echo -e "Step 1: ${Y}Enable Wireless debugging${B}"
echo -e "  ${C}Settings → System → Developer options → Wireless debugging${B} → ON"
echo ""
echo -e "Step 2: Open the ${Y}'Pair device with pairing code'${B} screen."
echo -e "  It shows an IP:PORT and a 6-digit code."
echo ""
ask "Enter the pair IP:PORT (e.g. 192.168.1.5:43210):" PAIR_ADDR
ask "Enter the 6-digit pairing code:" PAIR_CODE

echo ""
echo -e "${C}Pairing…${B}"
if adb pair "${PAIR_ADDR}" "${PAIR_CODE}"; then
  info "Paired successfully."
else
  warn "Pairing command returned non-zero — it may still have worked."
  warn "If it failed, toggle Wireless debugging off/on and retry."
fi

echo ""
echo -e "Step 3: Note the ${Y}IP:PORT${B} on the ${Y}main${B} Wireless debugging screen"
echo -e "  (this is different from the pair port)."
echo ""
ask "Enter the connect IP:PORT (e.g. 192.168.1.5:37521):" CONN_ADDR

echo ""
echo -e "${C}Connecting…${B}"
if adb connect "${CONN_ADDR}"; then
  info "Connected."
else
  err "Connect failed. Make sure the phone is unlocked and on the same WiFi."
  exit 1
fi

echo ""
echo -e "${C}Device list:${B}"
adb devices

echo ""
if adb devices 2>/dev/null | tail -n +2 | grep -q "device$"; then
  info "ADB is connected to this phone. Suraj AI can now control it."
else
  warn "No 'device' entry — check 'adb devices' output above."
fi
