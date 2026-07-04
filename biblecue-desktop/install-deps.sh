#!/usr/bin/env bash
set -e

PYTHON=python3
command -v python3 &>/dev/null || PYTHON=python

echo "Installing Python dependencies for BibleCue..."
echo

echo "[1/3] Core dependencies (required)..."
$PYTHON -m pip install websockets requests python-scriptures Pillow
echo

echo "[2/3] Audio dependencies (required for Deepgram and Whisper modes)..."
$PYTHON -m pip install sounddevice scipy numpy
echo

echo "[3/3] Whisper local transcription (optional - large download ~1GB)..."
echo "      Skip this if you only use Google Speech or Deepgram."
read -r -p "Install faster-whisper now? (y/N): " answer
if [[ "$answer" =~ ^[Yy]$ ]]; then
    $PYTHON -m pip install faster-whisper
    echo " OK: faster-whisper installed."
else
    echo " Skipped. To install later, run: pip install faster-whisper"
fi
echo

echo "Done! Run 'npm start' to launch BibleCue."
