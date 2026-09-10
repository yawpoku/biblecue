#!/usr/bin/env bash
set -e

PYTHON=python3
command -v python3 &>/dev/null || PYTHON=python

echo "Installing Python dependencies for BibleCue..."
echo

echo "[1/2] Core dependencies (required)..."
$PYTHON -m pip install websockets requests python-scriptures Pillow
echo

echo "[2/2] Audio dependencies (required for Deepgram mode)..."
$PYTHON -m pip install sounddevice scipy numpy
echo

echo "Done! Run 'npm start' to launch BibleCue."
