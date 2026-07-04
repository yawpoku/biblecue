# BibleCue

Real-time Bible scripture display for church services. Listens to a preacher's microphone, detects scripture references from speech, and sends the verse text to your presentation software.

## Features

- Detects spoken Bible references automatically during sermons
- Supports accented speech (West African, Caribbean, South Asian English)
- Offline verse lookup — no internet required for KJV, WEB, ASV, BBE, YLT, DARBY
- Multiple output targets: ProPresenter, OBS Studio, Clipboard, HTTP Webhook, Text File, TCP
- Three transcription engines: Google Speech (free), Deepgram (real-time), Whisper (offline, accent-robust)

## Download

Download the latest installer from [Releases](../../releases).

No Python installation required — everything is bundled.

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
pip install faster-whisper sounddevice numpy scipy requests python-scriptures websockets Pillow

# Install Node dependencies
cd biblecue-desktop
npm install

# Run in development
npm start
```

## Building

```bash
# Build the SQLite Bible (run once — takes ~30 minutes)
python scripts/build_bible_db.py

# Build the Windows installer
cd biblecue-desktop
npm run build
```

## Adding a New Output Plugin

1. Add a `_output_yourplugin()` function in `output_plugins.py`
2. Add a case for `"yourplugin"` in `fire_outputs()`
3. Add the default config entry to `DEFAULT_SETTINGS["outputs"]` in `biblecue.py`
4. Add the UI row to the Outputs card in `biblecue-desktop/renderer/index.html`
5. Wire up the DOM references, `collectOutputs()`, and `populateOutputs()` in `app.js`

## License

MIT
