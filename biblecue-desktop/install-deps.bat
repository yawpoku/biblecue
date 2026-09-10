@echo off
echo Installing Python dependencies for BibleCue...
echo.
echo [1/2] Core dependencies (required)...
pip install websockets requests python-scriptures Pillow
echo.
echo [2/2] Audio dependencies (required for Deepgram mode)...
pip install sounddevice scipy numpy
echo.
echo Done! Run start.bat to launch BibleCue.
pause
