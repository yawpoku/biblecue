@echo off
echo Installing Python dependencies for BibleCue...
echo.
echo [1/3] Core dependencies (required)...
pip install websockets requests python-scriptures Pillow
echo.
echo [2/3] Audio dependencies (required for Deepgram and Whisper modes)...
pip install sounddevice scipy numpy
echo.
echo [3/3] Whisper local transcription (optional - large download ~1GB)...
echo       Skip this if you only use Google Speech or Deepgram.
set /p INSTALL_WHISPER=Install faster-whisper now? (y/N):
if /i "%INSTALL_WHISPER%"=="y" (
    pip install faster-whisper
    echo  OK: faster-whisper installed.
) else (
    echo  Skipped. To install later, run: pip install faster-whisper
)
echo.
echo Done! Run start.bat to launch BibleCue.
pause
