# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this app does

**WCI BibleShow** is a church broadcast tool that listens to a preacher's microphone in real-time, detects Bible scripture references from speech, fetches the verse text, and sends it to **ProPresenter** (church presentation software) via its WebSocket API. It is packaged as an Electron desktop app with a Python backend.

## Running in development

```bash
cd electron-app
npm install        # first time only
npm start          # launches Electron + spawns Python backend automatically
```

Python dependencies (install once):
```bash
pip install faster-whisper sounddevice numpy scipy requests python-scriptures websockets Pillow
```

## Building for distribution

```bash
# Electron installer (Windows .exe via NSIS)
cd electron-app
npm run build

# Standalone Python executable (no Electron — legacy mode)
python build.py    # from project root; produces dist/WCIBibleShow.exe
```

## Architecture

The app has two independent layers that communicate over a local WebSocket:

```
WCIBibleshow.py  (Python backend, port 8765)
      │  WebSocket ws://127.0.0.1:8765
      ▼
electron-app/renderer/app.js  (Electron frontend)
```

### Python backend (`WCIBibleshow.py`)
- Single large file (~2000 lines). Contains all logic: speech transcription, scripture detection, ProPresenter integration, settings persistence, and the WebSocket server.
- Starts a WebSocket server on `ws_port` from settings (default **8765** in `probible_settings.json`).
- Also starts a local HTTP server on port **8766** serving a browser page that uses the Web Speech API for Google transcription mode.
- Three transcription engines: **Google Speech** (browser Web Speech API via the HTTP page), **Deepgram** (streaming WebSocket API), **Whisper** (local CPU via `faster-whisper`).
- Settings are persisted to `probible_settings.json` at the project root.

### Electron frontend (`electron-app/`)
- `main.js` — creates the BrowserWindow, spawns `WCIBibleshow.py` as a child process, handles IPC for window controls and python restart.
- `renderer/app.js` — connects to `ws://127.0.0.1:8765`, sends/receives JSON messages, updates all UI state.
- `renderer/index.html` + `renderer/style.css` — single-page UI, no framework.
- `preload.js` — exposes `window.electronAPI` (minimize/maximize/close) to the renderer via `contextBridge`.

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
- `{ type: "log", panel: "transcript"|"detection", text }`
- `{ type: "status", text, color }`
- `{ type: "dg_status", text }`

### Python script path resolution (`main.js`)
In dev: resolves `../WCIBibleshow.py` (one level above `electron-app/`).
In packaged build: resolves `resources/python/backend.py`.

## Key settings (`probible_settings.json`)
| Key | Default | Notes |
|-----|---------|-------|
| `ws_port` | `8765` | Must match `WS_URL` in `app.js` |
| `pro_ip` / `pro_port` | — | ProPresenter machine address |
| `message_uuid` | — | ProPresenter message UUID to target |
| `translation` | `KJV` | Bible translation for verse lookup |
| `mode` | `google` | `google` / `deepgram` / `whisper` |
| `cooldown_secs` | `12` | Min seconds between auto-sends |

## UI structure (`renderer/`)
- **Sidebar** (300px): transcription mode selector → ProPresenter settings → advanced settings (collapsible) → listen control with waveform
- **Main** (flex 1): NOW ON SCREEN verse panel (hero, Playfair Display font) → split log panels (Live Transcript / Scripture Detection) → status bar
- CSS uses CSS custom properties (`--bg`, `--gold`, `--blue`, etc.) defined in `:root`. Design tokens are the single source of truth for theming.
- Collapsible advanced panel is controlled by toggling `.open` class on `.collapsible-body` and `.collapse-arrow` — do **not** use inline `style.maxHeight`.
