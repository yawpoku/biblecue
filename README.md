# BibleCue

Real-time Bible scripture display for church services. Listens to a preacher's microphone, detects scripture references from speech, and sends the verse text to your presentation software.

## Features

- Detects spoken Bible references automatically during sermons
- Supports accented speech (West African, Caribbean, South Asian English)
- Multiple output targets: ProPresenter, OBS Studio, Clipboard, HTTP Webhook, Text File, TCP
- Two transcription engines: Google Speech (free, browser) and Deepgram (real-time, 200 hrs/month free)

## Download

Download the latest installer from [Releases](../../releases).

No Python installation required — everything is bundled.

### macOS first launch

The app is not signed with an Apple Developer ID, so macOS will say it
"could not verify BibleCue is free of malware". To open it the first time:

1. In **Finder → Applications**, right-click **BibleCue** → **Open** → **Open**.
2. If macOS still refuses, run this once in **Terminal**:
   ```bash
   xattr -dr com.apple.quarantine /Applications/BibleCue.app
   ```

For **Google Speech** mode, set **Chrome or Edge** as your default browser —
Safari's speech recognition won't drive it.

## Supported Output Software

| Software | Output plugin |
|---|---|
| ProPresenter 7+ | ProPresenter |
| OBS Studio | OBS WebSocket |
| EasyWorship, MediaShout, Proclaim | HTTP Webhook |
| Any software that watches a file | Text File |
| Any | Clipboard |
| Legacy broadcast hardware | TCP Raw |

## Development Setup

Requirements: Node.js 18+, Python 3.10+

```bash
# Install Python dependencies
pip install sounddevice numpy scipy requests python-scriptures websockets Pillow

# Install Node dependencies
cd biblecue-desktop
npm install

# Run in development
npm start
```

## Building the installer

Requires Python 3.11 and PyInstaller (`pip install pyinstaller`).

```bash
# 1. Bundle the Python backend into a standalone executable
pyinstaller backend.spec                                    # -> dist/backend.exe

# 2. Copy it into the Electron app
copy dist\backend.exe biblecue-desktop\python\backend.exe   # Windows
# cp dist/backend biblecue-desktop/python/backend           # macOS

# 3. Build the platform installer
cd biblecue-desktop
npm run build        # Windows NSIS installer -> dist/
npm run build:mac    # macOS DMG -> dist/
```

The local Bible (KJV, WEB) downloads automatically from getbible.net on first
run and is cached on disk — there is no Bible build step.

## Adding a New Output Plugin

1. Add a `_output_yourplugin()` function in `output_plugins.py`
2. Add a case for `"yourplugin"` in `fire_outputs()`
3. Add the default config entry to `DEFAULT_SETTINGS["outputs"]` in `biblecue.py`
4. Add the UI row to the Outputs card in `biblecue-desktop/renderer/index.html`
5. Wire up the DOM references, `collectOutputs()`, and `populateOutputs()` in `app.js`

## License

MIT
