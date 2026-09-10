#!/usr/bin/env bash
set -e

echo "================================================"
echo " BibleCue v1.2.0 - Setup"
echo "================================================"
echo

# --- Check Node.js ---
echo "[1/4] Checking Node.js..."
if ! command -v node &>/dev/null; then
    echo " ERROR: Node.js is not installed."
    echo " Install it from https://nodejs.org/ then re-run this script."
    exit 1
fi
echo " OK: $(node --version) found."
echo

# --- Check Python ---
echo "[2/4] Checking Python..."
if command -v python3 &>/dev/null; then
    PYTHON=python3
elif command -v python &>/dev/null; then
    PYTHON=python
else
    echo " ERROR: Python is not installed."
    echo " Install it from https://www.python.org/ then re-run this script."
    exit 1
fi
echo " OK: $($PYTHON --version) found."
echo

# --- npm install ---
echo "[3/4] Installing Node.js dependencies..."
cd "$(dirname "$0")"
npm install
echo " OK: Node.js dependencies installed."
echo

# --- Python dependencies ---
echo "[4/4] Installing Python dependencies..."
echo " Installing core packages: websockets requests python-scriptures Pillow"
$PYTHON -m pip install websockets requests python-scriptures Pillow
echo
echo " NOTE: Optional packages for Deepgram mode:"
echo "   Run install-deps.sh to install sounddevice, numpy, scipy"
echo

echo "================================================"
echo " Setup complete!"
echo "================================================"
echo
echo " To launch BibleCue, run:"
echo "   npm start"
echo "   (or double-click start.command if created)"
echo
