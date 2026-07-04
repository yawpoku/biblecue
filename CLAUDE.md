# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

## What this app does

**BibleCue** is a church broadcast tool that listens to a preacher's microphone in real-time, detects Bible scripture references from speech, fetches the verse text, and sends it to **ProPresenter** (church presentation software) via its HTTP API. It is packaged as an Electron desktop app with a Python backend.

## Running in development

```bash
cd biblecue-desktop
npm install        # first time only
npm start          # launches Electron + spawns ../biblecue.py automatically
```

Python dependencies (install once):
```bash
pip install faster-whisper sounddevice numpy scipy requests python-scriptures websockets Pillow
```

## Building for distribution

```bash
# 1. Build the Python backend into a standalone .exe (requires Python 3.11 + PyInstaller)
pyinstaller backend.spec        # from project root — produces dist/backend.exe

# 2. Copy it into the Electron app
copy dist\backend.exe biblecue-desktop\python\backend.exe

# 3. Build the Windows NSIS installer
cd biblecue-desktop
npm run build                   # produces dist/BibleCue Setup x.y.z.exe
```

## Architecture

Two independent layers communicating over a local WebSocket:

```
biblecue.py  (Python backend, port 8765)
      │  WebSocket ws://127.0.0.1:8765
      ▼
biblecue-desktop/renderer/app.js  (Electron frontend)
```

### Python backend (`biblecue.py`)
- Single large file (~3200 lines). Contains all logic: speech transcription, scripture detection, ProPresenter delivery, settings persistence, and the WebSocket server.
- Starts a WebSocket server on `ws_port` from settings (default **8765** in `biblecue_settings.json`).
- Also starts a local HTTP server on port **8766** serving a browser page that uses the Web Speech API for Google transcription mode.
- Three transcription engines: **Google Speech** (browser Web Speech API), **Deepgram** (streaming cloud API), **Whisper** (local CPU via `faster-whisper`).
- Output plugins are in `output_plugins.py` and handle ProPresenter, OBS, clipboard, HTTP webhook, text file, and TCP raw.

### Electron frontend (`biblecue-desktop/`)
- `main.js` — creates the BrowserWindow, spawns `biblecue.py` (dev) or `python/backend.exe` (packaged), handles window state.
- `renderer/app.js` — connects to `ws://127.0.0.1:8765`, sends/receives JSON messages, updates UI.
- `renderer/index.html` + `renderer/style.css` — single-page UI, no framework.
- `preload.js` — exposes `window.electronAPI` to renderer via `contextBridge`.

### WebSocket message protocol
Frontend → Backend:
- `{ type: "get_settings" }` / `{ type: "set_settings", data: {...} }`
- `{ type: "get_devices" }`
- `{ type: "start_listening" }` / `{ type: "stop_listening" }`
- `{ type: "manual_verse", text: "John 3:16" }`

Backend → Frontend:
- `{ type: "settings_response", data: {...} }`
- `{ type: "devices_response", list: [{id, name}] }`
- `{ type: "listening_state", active: bool }`
- `{ type: "transcript", text, interim: bool }`
- `{ type: "verse_display", verse_text, reference, translation }`
- `{ type: "log", panel: "transcript"|"detection", text, subtype: "info"|"fire"|"warn" }`
- `{ type: "status", text, color }`

### Python script path resolution (`main.js`)
- Dev mode: resolves `path.join(__dirname, '..', 'biblecue.py')`
- Packaged mode: resolves `path.join(process.resourcesPath, 'python', 'backend.exe')`

## Key settings (`biblecue_settings.json`)
| Key | Default | Notes |
|-----|---------|-------|
| `ws_port` | `8765` | Must match `WS_URL` in `app.js` |
| `pro_ip` / `pro_port` | — | ProPresenter machine IP and port |
| `message_uuid` | — | ProPresenter message UUID to target |
| `translation` | `KJV` | Bible translation |
| `mode` | `google` | `google` / `deepgram` / `whisper` |
| `cooldown_secs` | `12` | Min seconds between auto-sends |

## Scripture detection pipeline
1. **FAMOUS_PASSAGES** dict — catch well-known phrases ("for god so loved the world" → John 3:16)
2. **normalise_spoken()** — convert spoken form to chapter:verse notation
3. **extract_spoken_numbers()** — convert number words to digits
4. **python-scriptures** library — regex-based reference parser
5. **BOOK_PATTERN** regex — strict final pass with verse-1 filtering

## Tests
```bash
python -m pytest tests/
```
