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

BibleCue is not signed with an Apple Developer ID, so the first time you
open it macOS will say it "could not verify BibleCue is free of malware":

1. Drag **BibleCue** to **Applications**.
2. Try to open it once (it will be blocked), then go to
   **System Settings → Privacy & Security**, scroll down, and click
   **Open Anyway**. On older macOS: right-click **BibleCue** → **Open** → **Open**.
3. That's it — the app clears the quarantine flag from its bundled
   backend on its own. If it still won't connect, run once in **Terminal**:
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

## Setting Up Each Output

Turn on **exactly one** output in BibleCue's Outputs card — enabling several at
once sends every detected verse to all of them.

### ProPresenter (the tricky one — getting the Message UUID)

BibleCue doesn't drive ProPresenter's Bible/Scripture module — it triggers a
**Message**, and ProPresenter never displays a message's UUID anywhere in its
own interface, so you pull it from the API itself:

1. **Turn on the Network API.** In ProPresenter: **Preferences → Network**,
   enable the network/remote API, and note the **port** (default `1025`,
   which is also BibleCue's default). Find the Mac/PC's **local IP address**
   (e.g. in System Settings → Network) — that's what goes in BibleCue's IP field.
2. **Create the Message.** Open ProPresenter's **Messages** panel (the flag
   icon in the toolbar, or **View → Messages**), add a new message, and build
   its layout with two text layers. Type the literal text **`{{VerseText}}`**
   into one layer and **`{{Reference}}`** into the other — these are
   placeholder tokens; ProPresenter fills them in live when BibleCue triggers
   the message. Save it and give it a name you'll recognize, e.g. "BibleCue".
3. **Get its UUID.** With the API running, open this in a browser (swap in
   your ProPresenter machine's IP/port):
   ```
   http://<propresenter-ip>:<port>/v1/messages
   ```
   This returns every message as JSON. Find the one you just made by its
   `"name"` and copy the value under `"id": { "uuid": "..." }`.
4. In BibleCue's **ProPresenter** output: paste the **IP**, **Port**, and
   **Message UUID**, then enable it. Send a manual verse (Advanced Settings →
   Send Verse Manually) to confirm it fires the message on the ProPresenter
   screen.

Both machines must be on the same network. ProPresenter's exact menu wording
varies a little by version — look for "Network" under Preferences/Settings if
the path above doesn't match exactly.

### OBS Studio

Requires a text source already added to your OBS scene (its name goes in
BibleCue's **Text Source** field — must match exactly).

1. In OBS: **Tools → WebSocket Server Settings**, enable the server, note the
   **port** (default `4455`) and the **password** (click "Show Connect Info").
2. In BibleCue's **OBS Studio** output: IP `127.0.0.1` if BibleCue runs on the
   same machine as OBS (otherwise OBS's local IP), the port, the password, and
   the exact name of the text source to update.

### HTTP Webhook

Enter any URL that accepts a `POST` with JSON body
`{"verse": "...", "reference": "...", "translation": "..."}` — e.g. an
EasyWorship/Proclaim integration, a Zapier/Make webhook, or your own script.

### Text File

Enter a file path (e.g. `C:\verse.txt` or `/Users/you/verse.txt`). BibleCue
overwrites it with the verse text and reference on every detection — point
whatever software you use (a lower-third tool, OBS's "Read from file" text
source, vMix, etc.) at that same path.

### TCP Raw

Enter the IP and port of a device listening on a plain TCP socket (legacy
broadcast character generators, custom scripts). BibleCue sends
`"<verse text> — <reference>\n"` as UTF-8 on each detection — no protocol
beyond that.

### Clipboard

No setup — copies `"<verse text> — <reference>"` to the system clipboard on
every detection. Paste it into anything.

## Development Setup

Requirements: Node.js 18+, Python 3.10+

```bash
# Install Python dependencies
pip install sounddevice numpy scipy requests python-scriptures websockets Pillow obs-websocket-py

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
