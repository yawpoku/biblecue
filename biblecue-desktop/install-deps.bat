@echo off
echo Installing Python dependencies for BibleCue...
echo.
echo [1/3] Core dependencies (required)...
pip install websockets requests python-scriptures
echo.
echo [2/3] Audio dependencies (for Deepgram mode)...
pip install sounddevice scipy numpy
echo.
echo [3/3] Optional: Whisper local transcription...
echo       (Skip this if you only use Google Speech or Deepgram)
echo       To install: pip install faster-whisper
echo.
echo Done! Run start.bat to launch BibleCue.
pause
