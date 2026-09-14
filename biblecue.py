"""
BibleCue — Real-time Bible verse display for church services
=============================================================
Listens to a preacher's microphone, detects scripture references
from speech, and sends the verse text to presentation software.

FREE AND OPEN-SOURCE — MIT License
https://github.com/your-username/biblecue

─────────────────────────────────────────────
  CONTRIBUTOR NAVIGATION MAP  (Ctrl+G to jump)
─────────────────────────────────────────────
  LINE  ~115   SETTINGS          — load/save probible_settings.json
  LINE  ~185   BROWSER HTML      — listener page served to Chrome/Edge
  LINE  ~360   FAMOUS PASSAGES   — add "the shepherd psalm"-style lookups HERE
  LINE  ~415   BIBLE BOOKS       — add accent/mishearing aliases HERE
  LINE  ~770   SCRIPTURE PARSER  — parse_scripture() main detection function
  LINE  ~825   BIBLE API         — fetch_verse() with local JSON + HTTP fallback
  LINE  ~930   WS SERVER         — receives transcripts from browser listener
  LINE  ~985   HTTP SERVER       — serves the browser listener HTML page
  LINE  ~1165  DEEPGRAM          — real-time cloud transcription engine
  LINE  ~1315  PROBIBLEAPP       — legacy tkinter desktop UI (standalone mode)
  LINE  ~2255  HEADLESSAPP       — Electron mode (primary — this is what runs)
  LINE  ~2595  ENTRY POINT       — __main__ dispatch

─────────────────────────────────────────────
  HOW TO CONTRIBUTE
─────────────────────────────────────────────
  Add a book alias:      BIBLE_BOOKS dict  (~line 415)
  Add a famous passage:  FAMOUS_PASSAGES dict (~line 360)
  Add an output plugin:  output_plugins.py (separate file)
  Add a STT engine:      subclass pattern — see DeepgramListener
  Change the UI:         biblecue-desktop/renderer/  (HTML + CSS + JS, no framework)

─────────────────────────────────────────────
  ARCHITECTURE
─────────────────────────────────────────────
  Electron renderer  ──WebSocket──►  HeadlessApp (this file, --headless flag)
  Chrome/Edge tab    ──WebSocket──►  HeadlessApp  (Google Speech transcripts)
  HeadlessApp  ──HTTP──►  ProPresenter / OBS / webhook / file  (output_plugins.py)

─────────────────────────────────────────────
  REQUIREMENTS
─────────────────────────────────────────────
  pip install sounddevice numpy scipy requests python-scriptures websockets Pillow
"""

import os
import re
import sys
import json
import time
import queue
import asyncio
import threading
import webbrowser
import collections
import http.server
import socketserver
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
from tkinter import simpledialog as _simpledialog
import requests
import numpy as np

try:
    import scriptures
except ImportError:
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "python-scriptures"], check=False)
    import scriptures

try:
    import sounddevice as sd
    import scipy.io.wavfile as wav
    AUDIO_AVAILABLE = True
except ImportError:
    AUDIO_AVAILABLE = False

try:
    import websockets
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "websockets"], check=False)
    try:
        import websockets
        WEBSOCKETS_AVAILABLE = True
    except ImportError:
        WEBSOCKETS_AVAILABLE = False

_OUTPUT_PLUGINS_MISSING = False
_OUTPUT_PLUGINS_ERR = None
try:
    from output_plugins import fire_outputs as _fire_outputs
    from output_plugins import sync_ndi_output as _sync_ndi_output
except Exception as _e:
    _OUTPUT_PLUGINS_MISSING = True
    _OUTPUT_PLUGINS_ERR = f"{type(_e).__name__}: {_e}"
    def _fire_outputs(settings, verse_text, reference, translation):
        return [], []
    def _sync_ndi_output(settings):
        pass


# ── HEADLESS MODE (Electron spawns with --headless to skip tkinter) ──────────
HEADLESS = '--headless' in sys.argv

# ── FONT SETUP ────────────────────────────────────────────────────────────────
if not HEADLESS:
    import tkinter as _tk_test
    def _best_font(preferred="Segoe UI", fallback="TkDefaultFont"):
        """Return preferred font name if available, else fallback."""
        try:
            _r = _tk_test.Tk(); _r.withdraw()
            import tkinter.font as _tf
            available = _tf.families()
            _r.destroy()
            return preferred if preferred in available else fallback
        except Exception:
            return fallback
    _FONT = _best_font("Segoe UI")
    _MONO = _best_font("Consolas", "Courier")
else:
    _FONT = "Segoe UI"
    _MONO = "Consolas"

# ═══════════════════════════════════════════════════════════════
#  SETTINGS  (~line 115)
#  Load/save settings from biblecue_settings.json.
#  Add new setting keys to DEFAULT_SETTINGS below — they will be
#  automatically applied on first run without breaking old installs.
# ═══════════════════════════════════════════════════════════════
def _settings_path():
    import shutil
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    new_path = os.path.join(base, "biblecue_settings.json")

    # Migration: copy old settings filename to new one within same directory
    for old_name in ("bibleshow_settings.json", "probible_settings.json"):
        old_path = os.path.join(base, old_name)
        if not os.path.exists(new_path) and os.path.exists(old_path):
            shutil.copy2(old_path, new_path)
            break

    # Packaged-exe fallback: if no settings alongside the exe, check the
    # project root two levels up (resources/python/ → resources/ → app root)
    if getattr(sys, 'frozen', False) and not os.path.exists(new_path):
        for up in (2, 3):
            candidate_dir = base
            for _ in range(up):
                candidate_dir = os.path.dirname(candidate_dir)
            for name in ("biblecue_settings.json", "probible_settings.json"):
                candidate = os.path.join(candidate_dir, name)
                if os.path.exists(candidate):
                    return candidate

    return new_path

SETTINGS_FILE = _settings_path()

DEFAULT_SETTINGS = {
    "pro_ip":        "",
    "pro_port":      "1025",
    "message_uuid":  "",
    "translation":   "KJV",
    "mode":          "google",    # "google" | "deepgram"
    "deepgram_key":  "",
    "cooldown_secs": 8,
    "ws_port":       8765,
    "audio_device":  -1,  # -1 means default
    "fuzzy_threshold":  0.72,
    "outputs": [
        {"type": "propresenter", "enabled": False, "ip": "", "port": "1025", "uuid": ""},
        {"type": "easyworship", "enabled": False, "path": ""},
        {"type": "clipboard",    "enabled": False},
        {"type": "http_webhook", "enabled": False, "url": ""},
        {"type": "text_file",    "enabled": False, "path": ""},
        {"type": "obs_websocket","enabled": False, "ip": "127.0.0.1", "port": "4455", "password": "", "source": "BibleVerse"},
        {"type": "tcp_raw",      "enabled": False, "ip": "", "port": ""},
        {"type": "ndi",          "enabled": False, "stream_name": "BibleCue"},
    ],
    # Look of the live/fullscreen display — applied to both the fullscreen
    # window and (rendered as a video frame) the NDI output.
    "display": {
        "bg_type":       "color",   # "color" | "image"
        "bg_color":      "#060B18",
        "bg_image":      "",        # data: URL, chosen via the Display settings file picker
        "text_color":    "#F8FAFC",
        "accent_color":  "#F59E0B",
        "font_family":   "Playfair Display",
        "font_weight":   "500",
        "verse_size":    100,   # percent scale, 100 = default clamp() size
        "ref_size":      100,
        "align_h":       "center",  # left | center | right
        "align_v":       "middle",  # top | middle | bottom
    },
}

def load_settings():
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            s = json.load(f)
            for k, v in DEFAULT_SETTINGS.items():
                s.setdefault(k, v)
            # Always sync pro_ip → outputs so editing the file directly takes effect
            if s.get("pro_ip") and isinstance(s.get("outputs"), list):
                for out in s["outputs"]:
                    if out.get("type") == "propresenter":
                        out["ip"]      = s.get("pro_ip", "")
                        out["port"]    = s.get("pro_port", "1025")
                        out["uuid"]    = s.get("message_uuid", "")
                        out["enabled"] = bool(out["ip"] and out["uuid"])
                        break
            return s
    except Exception:
        return dict(DEFAULT_SETTINGS)

def _dbg(msg):
    """Appends a line to biblecue_debug.log — pythonw has no visible stdout,
    so this is the only way to see backend errors during dev/support.
    Explicit utf-8 (unlike the debug logging this replaced) so writing the
    message itself can't throw on Windows' non-utf8 default encoding."""
    try:
        import pathlib, datetime
        _d = pathlib.Path(os.path.dirname(os.path.abspath(__file__))) / "biblecue_debug.log"
        with _d.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.datetime.now()} {msg}\n")
    except Exception:
        pass


def save_settings(s):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(s, f, indent=2)
    except Exception as exc:
        _dbg(f"save_settings FAILED: {exc}")

# ═══════════════════════════════════════════════════════════════
#  BROWSER LISTENER HTML  (~line 185)
#  Self-contained HTML page served locally on port 8766.
#  Opens in Chrome/Edge, uses the Web Speech API to transcribe
#  the microphone, and sends transcripts back to HeadlessApp via
#  WebSocket. Edit get_listener_html() to change its appearance
#  or behaviour.
# ═══════════════════════════════════════════════════════════════
def get_listener_html(ws_port: int) -> str:
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8"/>
<title>BibleCue</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:Arial,sans-serif;background:#0f172a;color:#e2e8f0;padding:20px}}
h2{{color:#7eb8f7;font-size:17px;margin-bottom:4px}}
.sub{{font-size:11px;color:#475569;margin-bottom:14px}}
.mic{{display:inline-flex;align-items:center;justify-content:center;
      width:64px;height:64px;border-radius:50%;background:#1d6b3e;
      cursor:pointer;font-size:28px;margin-bottom:8px;border:none}}
.mic.active{{background:#dc2626;animation:pulse 1s infinite}}
@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.7}}}}
#status{{font-size:12px;color:#64748b;margin-bottom:8px;min-height:18px}}
.tx{{background:#1e293b;border-radius:6px;padding:12px;font-size:12px;
     min-height:48px;color:#94a3b8;line-height:1.5;margin-bottom:6px}}
.last{{color:#4ade80;font-size:12px;font-weight:bold;min-height:16px}}
#ws{{font-size:10px;color:#475569;margin-top:8px}}
.tip{{font-size:10px;color:#334155;margin-top:8px;border-top:1px solid #1e293b;padding-top:6px}}
</style>
</head>
<body>
<h2>BibleCue — Listener</h2>
<p class="sub">Keep this tab open and visible.</p>
<button class="mic" id="mic" onclick="toggleMic()">&#127908;</button>
<div id="status">Connecting...</div>
<div class="tx" id="transcript">Waiting for speech...</div>
<div class="last" id="last"></div>
<div id="ws">WebSocket: waiting...</div>
<div class="tip">Keep tab visible — Chrome pauses mic in background tabs.</div>
<script>
var WS_PORT = {ws_port};
var WS_URL  = 'ws://127.0.0.1:' + WS_PORT;
var ws = null, rec = null, running = false, restarting = false;
var lastFinal = '', ka = null, restarts = 0, dgMode = false;
var audioCtx = null, mediaSrc = null, audioProc = null;

function el(id) {{ return document.getElementById(id); }}
function setWS(m,c) {{ el('ws').textContent='WebSocket: '+m; el('ws').style.color=c||'#475569'; }}
function setStatus(m,c) {{ el('status').textContent=m; el('status').style.color=c||'#64748b'; }}
function sendWS(obj) {{ if(ws&&ws.readyState===1) try{{ws.send(JSON.stringify(obj));}}catch(e){{}} }}

function connectWS() {{
  setWS('connecting...','#f97316');
  try {{ ws = new WebSocket(WS_URL); }} catch(e) {{ setWS('failed','#f87171'); setTimeout(connectWS,3000); return; }}
  ws.onopen = function() {{
    setWS('Connected','#4ade80');
    setStatus(running?'Listening':'Click mic to start','#4ade80');
    if(ka) clearInterval(ka);
    ka = setInterval(function(){{ sendWS({{type:'ping'}}); }}, 20000);
  }};
  ws.onmessage = function(e) {{
    if(typeof e.data!=='string') return;
    try {{
      var d = JSON.parse(e.data);
      if(d.type==='start') {{
        if(!running) toggleMic();
      }} else if(d.type==='dg_mode') {{
        dgMode = d.active;
        if(d.active && !audioProc) {{ if(!running){{running=true;}} startDGAudio(); }}
        else if(!d.active) {{ stopDGAudio(); }}
      }} else if(d.type==='stop') {{
        if(rec) {{ try{{rec.stop();}}catch(e){{}} rec=null; }} running=false;
        stopDGAudio(); window.close();
      }}
    }} catch(ex) {{}}
  }};
  ws.onclose = function() {{ setWS('reconnecting...','#f97316'); if(ka){{clearInterval(ka);ka=null;}} setTimeout(connectWS,2000); }};
  ws.onerror = function() {{ setWS('error','#f87171'); }};
}}

function buildRec() {{
  var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if(!SR) {{ setStatus('Not supported — use Chrome or Edge','#f87171'); return null; }}
  var r = new SR();
  r.continuous=true; r.interimResults=true; r.lang='en-US'; r.maxAlternatives=1;
  r.onstart = function() {{
    restarting=false;
    el('mic').classList.add('active');
    setStatus(restarts>0?'Listening ('+restarts+')':'Listening','#4ade80');
  }};
  r.onresult = function(e) {{
    var fin='', itr='';
    for(var i=e.resultIndex;i<e.results.length;i++) {{
      var t=e.results[i][0].transcript;
      if(e.results[i].isFinal) fin+=t; else itr+=t;
    }}
    el('transcript').textContent=itr||fin||'...';
    if(itr.trim()) sendWS({{type:'interim',text:itr.trim()}});
    if(fin.trim()&&fin.trim()!==lastFinal) {{
      lastFinal=fin.trim(); el('last').textContent=lastFinal;
      sendWS({{type:'transcript',text:lastFinal}});
    }}
  }};
  r.onerror = function(e) {{
    if(e.error==='not-allowed') {{
      setStatus('Mic blocked — allow in browser','#f87171');
      running=false; restarting=false; el('mic').classList.remove('active');
    }}
  }};
  r.onend = function() {{
    if(!running||restarting) return;
    restarting=true; restarts++;
    setStatus('Restarting...','#facc15');
    setTimeout(function() {{
      if(!running){{restarting=false;return;}}
      rec=buildRec();
      if(rec) try{{rec.start();}}catch(err){{restarting=false;setTimeout(function(){{rec=buildRec();if(rec)try{{rec.start();}}catch(e2){{}}}},1000);}}
    }},600);
  }};
  return r;
}}

function startDGAudio() {{
  if(audioProc) return;
  navigator.mediaDevices.getUserMedia({{audio:{{sampleRate:16000,channelCount:1,echoCancellation:false,noiseSuppression:false}}}})
  .then(function(stream) {{
    mediaSrc=stream;
    audioCtx=new (window.AudioContext||window.webkitAudioContext)({{sampleRate:16000}});
    var src=audioCtx.createMediaStreamSource(stream);
    audioProc=audioCtx.createScriptProcessor(4096,1,1);
    audioProc.onaudioprocess=function(ev) {{
      if(!running||!ws||ws.readyState!==1) return;
      var f32=ev.inputBuffer.getChannelData(0);
      var i16=new Int16Array(f32.length);
      for(var i=0;i<f32.length;i++) i16[i]=Math.max(-32768,Math.min(32767,f32[i]*32768));
      try{{ws.send(i16.buffer);}}catch(e){{}}
    }};
    src.connect(audioProc); audioProc.connect(audioCtx.destination);
    el('mic').classList.add('active');
    setStatus('Deepgram: streaming audio','#a78bfa');
  }}).catch(function(err){{ setStatus('Mic error: '+err.message,'#f87171'); }});
}}

function stopDGAudio() {{
  if(audioProc){{try{{audioProc.disconnect();}}catch(e){{}}audioProc=null;}}
  if(audioCtx){{try{{audioCtx.close();}}catch(e){{}}audioCtx=null;}}
  if(mediaSrc){{mediaSrc.getTracks().forEach(function(t){{t.stop();}});mediaSrc=null;}}
}}

function toggleMic() {{
  if(running) {{
    running=false; restarting=false;
    if(rec){{try{{rec.stop();}}catch(e){{}}rec=null;}}
    stopDGAudio();
    el('mic').classList.remove('active');
    setStatus('Stopped','#606080');
  }} else {{
    running=true; lastFinal=''; restarts=0; restarting=false;
    if(dgMode) {{ startDGAudio(); }}
    else {{
      rec=buildRec();
      if(rec){{try{{rec.start();}}catch(e){{setStatus('Failed: '+e.message,'#f87171');running=false;}}}}
      else {{ running=false; }}
    }}
  }}
}}

window.onload=function(){{
  connectWS();
  setTimeout(function(){{if(!running&&!dgMode) toggleMic();}},1500);
}};
window.addEventListener('beforeunload',function(e){{if(running){{e.preventDefault();e.returnValue='';}}}});
</script>
</body>
</html>"""


# ═══════════════════════════════════════════════════════════════
#  SCRIPTURE PARSER  (~line 360)
#  Detects Bible references from transcribed speech with no LLM.
#  Three things to know as a contributor:
#
#  1. FAMOUS_PASSAGES — add entries here for common phrases like
#     "the shepherd psalm" or "the love chapter". Format:
#       "spoken phrase": ("Book", chapter, verse)
#
#  2. BIBLE_BOOKS — add entries here for accent variants and
#     mishearings. Format:
#       "mishearing": "Canonical Book Name"
#
#  3. parse_scripture() — the main detection function. It runs
#     four passes: famous passages → normalise spoken numbers →
#     python-scriptures library → strict regex. Most fixes only
#     need FAMOUS_PASSAGES or BIBLE_BOOKS, not this function.
# ═══════════════════════════════════════════════════════════════

# ── FAMOUS PASSAGES ───────────────────────────────────────────
# Phrases people say instead of a chapter:verse reference.
# TO ADD: "phrase people say": ("Book", chapter, verse)
FAMOUS_PASSAGES = {
    "shepherd psalm":          ("Psalms",    23, 1),
    "23rd psalm":              ("Psalms",    23, 1),
    "psalm 23":                ("Psalms",    23, 1),
    "lord is my shepherd":     ("Psalms",    23, 1),
    "love chapter":            ("1 Corinthians", 13, 1),
    "faith chapter":           ("Hebrews",   11, 1),
    "hall of faith":           ("Hebrews",   11, 1),
    "armor of god":            ("Ephesians",  6, 11),
    "armour of god":           ("Ephesians",  6, 11),
    "lord's prayer":           ("Matthew",    6, 9),
    "beatitudes":              ("Matthew",    5, 3),
    "sermon on the mount":     ("Matthew",    5, 1),
    "ten commandments":        ("Exodus",    20, 1),
    "great commission":        ("Matthew",   28, 19),
    "golden rule":             ("Matthew",    7, 12),
    "in the beginning":        ("Genesis",    1, 1),
    "for god so loved":        ("John",       3, 16),
    "john 316":                ("John",       3, 16),
    "john three sixteen":      ("John",       3, 16),
    "fruits of the spirit":    ("Galatians",  5, 22),
    "fruit of the spirit":     ("Galatians",  5, 22),
    "i can do all things":     ("Philippians",4, 13),
    "all things through christ":("Philippians",4, 13),
    "renew your mind":         ("Romans",    12, 2),
    "be transformed":          ("Romans",    12, 2),
    "no weapon formed":        ("Isaiah",    54, 17),
    "greater is he":           ("1 John",     4, 4),
    "the truth shall set":     ("John",       8, 32),
    "truth will set you free": ("John",       8, 32),
    "come to me all":          ("Matthew",   11, 28),
    "weary and burdened":      ("Matthew",   11, 28),
    "plans to prosper":        ("Jeremiah",  29, 11),
    "know the plans":          ("Jeremiah",  29, 11),
    "all things work together":("Romans",     8, 28),
    "good to those who love":  ("Romans",     8, 28),
    "fear not":                ("Isaiah",    41, 10),
    "do not fear":             ("Isaiah",    41, 10),
    "be strong and courageous":("Joshua",     1, 9),
    "cast all your anxiety":   ("1 Peter",    5, 7),
    "lean not on your own":    ("Proverbs",   3, 5),
    "trust in the lord":       ("Proverbs",   3, 5),
    "new creation":            ("2 Corinthians",5,17),
    "old has gone":            ("2 Corinthians",5,17),
    "whatsoever things are true":("Philippians",4,8),
    "think on these things":   ("Philippians", 4, 8),
    # Psalms — dropped-P spoken forms
    "salm 23":              ("Psalms", 23, 1),
    "salm twenty three":    ("Psalms", 23, 1),
    "the 23rd salm":        ("Psalms", 23, 1),
    "shepherd salm":        ("Psalms", 23, 1),
    "23rd salm":            ("Psalms", 23, 1),
    # Resurrection / Easter passages
    "renewing of your mind":              ("Romans",         12,  2),
    "god of all grace":                   ("1 Peter",         5, 10),
    "after you have suffered a while":    ("1 Peter",         5, 10),
    "suffered a while":                   ("1 Peter",         5, 10),
    "because i live you shall live":      ("John",           14, 19),
    "i am the resurrection":              ("John",           11, 25),
    "he spared not his own son":          ("Romans",          8, 32),
    "freely give us all things":          ("Romans",          8, 32),
    "justified by faith we have peace":   ("Romans",          5,  1),
    "peace with god through our lord":    ("Romans",          5,  1),
    "if christ be not risen":             ("1 Corinthians",  15, 14),
    "our faith is also in vain":          ("1 Corinthians",  15, 14),
    "led captivity captive":              ("Ephesians",       4,  8),
    "gave gifts unto men":                ("Ephesians",       4,  8),
    "redeemed us from the curse":         ("Galatians",       3, 13),
}

# Spoken number words to digits
WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    "hundred": 100, "first": 1, "second": 2, "third": 3,
    "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7,
    "eighth": 8, "ninth": 9, "tenth": 10,
}

# Compound spoken numbers "twenty one" → 21
def words_to_number(text: str) -> int:
    """Convert spoken number text to integer. Returns 0 if not parseable."""
    t = text.strip().lower()
    # Direct digit
    if t.isdigit():
        return int(t)
    # Single word
    if t in WORD_NUMBERS:
        return WORD_NUMBERS[t]
    # Compound: "twenty one", "thirty two" etc.
    parts = t.split()
    if len(parts) == 2 and parts[0] in WORD_NUMBERS and parts[1] in WORD_NUMBERS:
        tens = WORD_NUMBERS[parts[0]]
        ones = WORD_NUMBERS[parts[1]]
        if tens >= 20 and ones < 10:
            return tens + ones
    return 0

# ── BIBLE BOOKS ───────────────────────────────────────────────
# Maps every spoken/misheared form to the canonical book name.
# TO ADD AN ACCENT VARIANT: "how it sounds": "Canonical Name"
# Examples already here: "salm" → Psalms, "fillippians" → Philippians
BIBLE_BOOKS = {
    # Full names
    "genesis": "Genesis", "exodus": "Exodus", "leviticus": "Leviticus",
    "numbers": "Numbers", "deuteronomy": "Deuteronomy", "joshua": "Joshua",
    "judges": "Judges", "ruth": "Ruth", "ezra": "Ezra",
    "nehemiah": "Nehemiah", "esther": "Esther", "job": "Job",
    "psalms": "Psalms", "psalm": "Psalms", "proverbs": "Proverbs",
    "ecclesiastes": "Ecclesiastes", "isaiah": "Isaiah",
    "jeremiah": "Jeremiah", "lamentations": "Lamentations",
    "ezekiel": "Ezekiel", "daniel": "Daniel", "hosea": "Hosea",
    "joel": "Joel", "amos": "Amos", "obadiah": "Obadiah",
    "jonah": "Jonah", "micah": "Micah", "nahum": "Nahum",
    "habakkuk": "Habakkuk", "zephaniah": "Zephaniah", "haggai": "Haggai",
    "zechariah": "Zechariah", "malachi": "Malachi", "matthew": "Matthew",
    "mark": "Mark", "luke": "Luke", "john": "John", "acts": "Acts",
    "romans": "Romans", "galatians": "Galatians", "ephesians": "Ephesians",
    "philippians": "Philippians", "colossians": "Colossians",
    "titus": "Titus", "philemon": "Philemon", "hebrews": "Hebrews",
    "james": "James", "jude": "Jude", "revelation": "Revelation",
    "revelations": "Revelation",
    # Numbered books
    "1 samuel": "1 Samuel", "2 samuel": "2 Samuel",
    "1 kings": "1 Kings", "2 kings": "2 Kings",
    "1 chronicles": "1 Chronicles", "2 chronicles": "2 Chronicles",
    "1 corinthians": "1 Corinthians", "2 corinthians": "2 Corinthians",
    "1 thessalonians": "1 Thessalonians", "2 thessalonians": "2 Thessalonians",
    "1 timothy": "1 Timothy", "2 timothy": "2 Timothy",
    "1 peter": "1 Peter", "2 peter": "2 Peter",
    "1 john": "1 John", "2 john": "2 John", "3 john": "3 John",
    # Spoken ordinals for numbered books
    "first samuel": "1 Samuel", "second samuel": "2 Samuel",
    "first kings": "1 Kings", "second kings": "2 Kings",
    "first chronicles": "1 Chronicles", "second chronicles": "2 Chronicles",
    "first corinthians": "1 Corinthians", "second corinthians": "2 Corinthians",
    "first thessalonians": "1 Thessalonians", "second thessalonians": "2 Thessalonians",
    "first timothy": "1 Timothy", "second timothy": "2 Timothy",
    "first peter": "1 Peter", "second peter": "2 Peter",
    "first john": "1 John", "second john": "2 John", "third john": "3 John",
    # Common abbreviations / mishearings
    "song of solomon": "Song of Solomon", "songs": "Song of Solomon",
    "song of songs": "Song of Solomon",
    "corinthians": "1 Corinthians",  # when said without number
    "thessalonians": "1 Thessalonians",
    "timothy": "1 Timothy",
    "peter": "1 Peter",
    "kings": "1 Kings",
    "samuel": "1 Samuel",
    "chronicles": "1 Chronicles",
    # Extended phonetic mishearings from real transcripts
    "leak": "Luke", "louk": "Luke", "loke": "Luke",
    "nehemia": "Nehemiah", "hemia": "Nehemiah", "nemia": "Nehemiah",
    "st peter": "1 Peter", "saint peter": "1 Peter",
    "st john": "1 John", "saint john": "1 John",
    "extra": "Ezra", "ezrah": "Ezra",
    "now": "Numbers",        # "now chapter X" mishearing of "Numbers chapter X"
    "phase": "1 Peter",      # phonetic mishearing common in some accents
    "petter": "1 Peter", "petah": "1 Peter",
    "jss": "James", "jas": "James", "jame": "James",
    "isa": "Isaiah",
    "rev": "Revelation", "reve": "Revelation",
    "zech": "Zechariah", "zach": "Zechariah",
    "mal": "Malachi",
    "hab": "Habakkuk",
    "zeph": "Zephaniah",
    "hag": "Haggai",
    "nah": "Nahum",
    # ── Accent-aware aliases ──────────────────────────────────────
    # Philippians variants (West African / Caribbean speech)
    "fillippians":      "Philippians",
    "phillipians":      "Philippians",
    "phillipan":        "Philippians",
    "philipians":       "Philippians",
    "phillipon":        "Philippians",
    # Psalms — dropped P (very common in West African, Caribbean, South Asian speech)
    "salm":             "Psalms",
    "salms":            "Psalms",
    "sam":              "Psalms",
    "sams":             "Psalms",
    "salom":            "Psalms",
    "psalmist":         "Psalms",
    # Other common accent mishearings
    "colossian":        "Colossians",
    "ephesian":         "Ephesians",
    "thessalonian":     "1 Thessalonians",
    "corinthian":       "1 Corinthians",
    "ecclesiastical":   "Ecclesiastes",
    "obadiyah":         "Obadiah",
    "habakuk":          "Habakkuk",
    "revelator":        "Revelation",
    "galation":         "Galatians",
    "galations":        "Galatians",
    # Phonetic/mishearing variants (from real transcripts)
    "obediah": "Obadiah", "obediance": "Obadiah", "obidiah": "Obadiah",
    "habacuck": "Habakkuk", "habakook": "Habakkuk",
    "malachy": "Malachi",
    "zepania": "Zephaniah", "zephannia": "Zephaniah",
    "collossians": "Colossians", "collosians": "Colossians",
    # West African / Nigerian English accent mishearings (Deepgram + Google)
    "efficient": "Ephesians",    # most common — "Ephesians" sounds like "efficient"
    "efficients": "Ephesians",
    "efeshans": "Ephesians", "epheshans": "Ephesians", "ephesans": "Ephesians",
    "efesians": "Ephesians", "phesians": "Ephesians", "feeshans": "Ephesians",
    "ephisian": "Ephesians", "ephisians": "Ephesians", "epheshion": "Ephesians",
    "galashians": "Galatians", "galashian": "Galatians",
    "galashan": "Galatians", "galashons": "Galatians",
    "corinthans": "1 Corinthians", "corintians": "1 Corinthians",
    "corinthins": "1 Corinthians", "corithians": "1 Corinthians",
    "thessalonans": "1 Thessalonians", "thesalonians": "1 Thessalonians",
    "thesalonans": "1 Thessalonians",
    "timothie": "1 Timothy", "timothee": "1 Timothy",
    "tim": "1 Timothy", "2 tim": "2 Timothy", "second tim": "2 Timothy",
    "1 cor": "1 Corinthians", "2 cor": "2 Corinthians",
    "1 pet": "1 Peter", "2 pet": "2 Peter",
    "jeremia": "Jeremiah", "jeremi": "Jeremiah",
    "jerimiah": "Jeremiah", "jeremias": "Jeremiah",
    "mathew": "Matthew", "matthiew": "Matthew", "mathieu": "Matthew",
    "revelashon": "Revelation", "revelashons": "Revelation",
    "hebrues": "Hebrews", "ebrews": "Hebrews",
    "phillipans": "Philippians", "filipans": "Philippians",
    "colossans": "Colossians", "collosans": "Colossians",
    "colashans": "Colossians",
    "lukah": "Luke", "looka": "Luke",
    "actes": "Acts", "actz": "Acts",
    "romance": "Romans", "roman": "Romans", "romanes": "Romans",
    "he brings": "Hebrews", "hebrings": "Hebrews", "he brews": "Hebrews",
    "hebs": "Hebrews", "ebrus": "Hebrews", "hebrus": "Hebrews",
    "he grievous": "Hebrews", "hebrious": "Hebrews",
    "king": "1 Kings", "second kings": "2 Kings", "2nd kings": "2 Kings",
    "jonás": "Jonah", "jonahs": "Jonah",
    "mich": "Micah", "micha": "Micah",
    "isaia": "Isaiah", "esaiah": "Isaiah", "isayah": "Isaiah",
    # Google Speech hears "epistle" as "hospital" — consistently maps to 1 Peter
    "hospital": "1 Peter", "hospit": "1 Peter",
    # Google Speech hears "Colossians" as "pollutions"
    "pollutions": "Colossians", "pollution": "Colossians",
}

NUM_WORDS = r'(?:' + '|'.join(sorted(WORD_NUMBERS.keys(), key=len, reverse=True)) + r'|\d+)'
BOOK_PATTERN = r'(?:' + '|'.join(
    sorted(BIBLE_BOOKS.keys(), key=len, reverse=True)) + r')'

_ROMAN_BOOK_MAP = {
    "I Samuel": "1 Samuel",   "II Samuel": "2 Samuel",
    "I Kings": "1 Kings",     "II Kings": "2 Kings",
    "I Chronicles": "1 Chronicles", "II Chronicles": "2 Chronicles",
    "I Corinthians": "1 Corinthians", "II Corinthians": "2 Corinthians",
    "I Thessalonians": "1 Thessalonians", "II Thessalonians": "2 Thessalonians",
    "I Timothy": "1 Timothy", "II Timothy": "2 Timothy",
    "I Peter": "1 Peter",     "II Peter": "2 Peter",
    "I John": "1 John",       "II John": "2 John", "III John": "3 John",
    "I Maccabees": "1 Maccabees", "II Maccabees": "2 Maccabees",
}

def _canon_book(book: str) -> str:
    """Normalize Roman numeral book names to Arabic form."""
    return _ROMAN_BOOK_MAP.get(book, book)


# Pre-normalise spoken ordinals so "second Corinthians" → "2 Corinthians"
# before _autocorrect_books / python-scriptures see the text.
_ORDINAL_BOOK_RE = re.compile(
    r'\b(first|second|third)\s+'
    r'(samuel|kings|chronicles|corinthians|thessalonians|timothy|peter|john)\b',
    re.IGNORECASE
)
_ORDINAL_TO_NUM = {'first': '1', 'second': '2', 'third': '3'}


def _normalize_ordinals(text: str) -> str:
    """Convert 'second Corinthians' → '2 Corinthians' etc. before parsing."""
    def _repl(m):
        return f"{_ORDINAL_TO_NUM[m.group(1).lower()]} {m.group(2).title()}"
    return _ORDINAL_BOOK_RE.sub(_repl, text)


def _words_to_numbers(text):
    """Convert spoken word-numbers to digits for scripture detection."""
    ones = {
        'zero':0,'one':1,'two':2,'three':3,'four':4,'five':5,'six':6,
        'seven':7,'eight':8,'nine':9,'ten':10,'eleven':11,'twelve':12,
        'thirteen':13,'fourteen':14,'fifteen':15,'sixteen':16,
        'seventeen':17,'eighteen':18,'nineteen':19
    }
    tens = {
        'twenty':20,'thirty':30,'forty':40,'fifty':50,
        'sixty':60,'seventy':70,'eighty':80,'ninety':90
    }
    one_keys = '|'.join(sorted(ones.keys(), key=len, reverse=True))
    ten_keys = '|'.join(sorted(tens.keys(), key=len, reverse=True))
    # Pass 1: hundreds — "one hundred [and] [tens] [ones]"
    pat_h = re.compile(
        r'\b(' + one_keys + r')\s+hundred(?:\s+and)?(?:\s+(' + ten_keys + r'))?(?:\s+(' + one_keys + r'))?\b'
    )
    def h_replace(m):
        base = ones[m.group(1)] * 100
        extra_tens = tens.get(m.group(2) or '', 0)
        extra_ones = ones.get(m.group(3) or '', 0)
        return str(base + extra_tens + extra_ones)
    text = pat_h.sub(h_replace, text)
    # Pass 2: tens + optional ones — "twenty one", "thirty", etc.
    pat_t = re.compile(r'\b(' + ten_keys + r')(?:\s+(' + one_keys + r'))?\b')
    def t_replace(m):
        return str(tens[m.group(1)] + ones.get(m.group(2) or '', 0))
    text = pat_t.sub(t_replace, text)
    # Pass 3: bare ones / teens
    pat_o = re.compile(r'\b(' + one_keys + r')\b')
    text = pat_o.sub(lambda m: str(ones[m.group(1)]), text)
    return text


def normalise_spoken(text: str) -> str:
    """Normalise spoken forms before parsing."""
    t = text.lower().strip()
    # Strip punctuation that speech recognition inserts mid-phrase, e.g.
    # "Joshua chapter 4, verse 5" — the comma breaks regex matching.
    t = t.replace(',', ' ').replace(';', ' ')
    t = re.sub(r'  +', ' ', t)
    # Guard: 'first psalm(s)' must not become '1 Samuel' via numbered-book substitution
    t = re.sub(r'\bfirst\s+psalms?\b', 'psalm', t)
    # Convert spoken word-numbers to digits before chapter/verse regex matching
    t = _words_to_numbers(t)
    # "first/second/third X" → "1/2/3 X" for book prefixes
    t = re.sub(r'\bfirst\s+(samuel|kings|chronicles|corinthians|thessalonians|timothy|peter|john|tim|cor|pet|sam|kgs|chr|thes|jn)\b',
               r'1 \1', t)
    t = re.sub(r'\bsecond\s+(samuel|kings|chronicles|corinthians|thessalonians|timothy|peter|john|tim|cor|pet|sam|kgs|chr|thes|jn)\b',
               r'2 \1', t)
    t = re.sub(r'\bthird\s+(john|jn)\b', r'3 \1', t)
    t = re.sub(r'\b1st\b', '1', t)
    t = re.sub(r'\b2nd\b', '2', t)
    t = re.sub(r'\b3rd\b', '3', t)
    # Reorder inverted spoken form: "verse N of Book chapter M" → "Book chapter M verse N"
    t = re.sub(
        r'\bverse\s+(\d+)\s+of\s+([\w][\w ]{1,20}?)\s+(chapter\s+\d+)',
        r'\2 \3 verse \1', t, flags=re.I)
    # Strip verse ranges: "verse 12 to 15" → "verse 12", "verse 20-22" → "verse 20"
    t = re.sub(r'\b(verse\s+\d+)\s*(?:to|through|and|-)\s*\d+\b', r'\1', t, flags=re.I)
    # "chapter X verse Y" → "X:Y" with flexible connectors
    _cv_conn = r'(?:and\s+|from\s+|in\s+|of\s+|i\s+believe\s+|i\s+think\s+|that\s+is\s+|which\s+is\s+)?'
    t = re.sub(rf'chapter\s+(\d+)\s+{_cv_conn}verse\s+(\d+)', r'\1:\2', t, flags=re.I)
    t = re.sub(rf'(\d+)\s+{_cv_conn}verse\s+(\d+)', r'\1:\2', t, flags=re.I)
    t = re.sub(r'\bverse\s+(\d+)', r':\1', t, flags=re.I)
    t = re.sub(r'\bchapter\s+(\d+)', r' \1', t, flags=re.I)
    # Strip trailing verse ranges after colon-format refs: "3:12 to 15" → "3:12"
    t = re.sub(r'(\d+:\d+)\s*(?:to|through|and|-)\s*\d+', r'\1', t, flags=re.I)
    t = re.sub(r'(\d+)\.(\d+)', r'\1:\2', t)
    # Rejoin verse numbers split by speech recognition: "3:2 3" → "3:23"
    t = re.sub(r'(\d+:\d)\s+(\d)\b', lambda m: m.group(1) + m.group(2), t)
    return t

def extract_spoken_numbers(text: str) -> str:
    """Replace spoken number sequences with digits. e.g. 'three sixteen' → '3:16'"""
    def replace_pair(m):
        n1 = words_to_number(m.group(1))
        n2 = words_to_number(m.group(2))
        if n1 > 0 and n2 > 0:
            return f"{n1}:{n2}"
        return m.group(0)

    word_num = r'(?:twenty\s+(?:one|two|three|four|five|six|seven|eight|nine)|' \
               r'thirty\s+(?:one|two|three|four|five|six|seven|eight|nine)|' \
               r'one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|' \
               r'thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|' \
               r'thirty|forty|fifty|sixty|seventy|eighty|ninety|\d+)'

    # Negative lookbehind (?<!:) prevents re-pairing digits already after a colon
    text = re.sub(
        rf'(?<!:)\b({word_num})\s+({word_num})\b',
        replace_pair,
        text,
        flags=re.I
    )
    return text


def _fuzzy_correct_book(word: str) -> str:
    """
    Uses difflib to fuzzy-match a potentially misheared book name
    to the closest real Bible book. Only corrects if confidence >= 0.72.
    """
    import difflib
    word_lower = word.lower().strip()
    if word_lower in BIBLE_BOOKS:
        return BIBLE_BOOKS[word_lower]
    candidates = list(BIBLE_BOOKS.keys())
    matches = difflib.get_close_matches(word_lower, candidates, n=1, cutoff=0.72)
    if matches:
        return BIBLE_BOOKS[matches[0]]
    canonical = list(set(BIBLE_BOOKS.values()))
    canonical_lower = {b.lower(): b for b in canonical}
    matches2 = difflib.get_close_matches(word_lower, list(canonical_lower.keys()), n=1, cutoff=0.72)
    if matches2:
        return canonical_lower[matches2[0]]
    return None


def _autocorrect_books(text: str) -> str:
    """
    Scan text for words near chapter/verse patterns and attempt
    to autocorrect misheared book names using fuzzy matching.
    Only corrects words immediately before chapter/verse numbers.
    """
    def try_fix(m):
        raw_book = m.group(1).strip()
        if len(raw_book) < 3:
            return m.group(0)  # skip fuzzy correction for very short tokens
        # Don't overwrite already-normalized numbered books like "2 Corinthians"
        pre = text[:m.start(1)].rstrip()
        if pre and pre[-1].isdigit():
            return m.group(0)
        rest = m.group(2)
        corrected = _fuzzy_correct_book(raw_book)
        if corrected and corrected.lower() != raw_book.lower():
            return f"{corrected} {rest}"
        return m.group(0)

    text = re.sub(
        r'\b([a-zA-Z][a-zA-Z ]{1,20}?)\s+(chapter|verse|\d+[:\.]\d+)',
        try_fix, text, flags=re.IGNORECASE
    )
    return text


# Navigation number pattern — single words and compounds like "twenty eight"
_NAV_NUM_PAT = (
    r'(?:twenty\s+(?:one|two|three|four|five|six|seven|eight|nine)|'
    r'thirty\s+(?:one|two|three|four|five|six|seven|eight|nine)|'
    r'forty\s+(?:one|two|three|four|five|six|seven|eight|nine)|'
    r'fifty\s+(?:one|two|three|four|five|six|seven|eight|nine)|'
    r'sixty\s+(?:one|two|three|four|five|six|seven|eight|nine)|'
    r'seventy\s+(?:one|two|three|four|five|six|seven|eight|nine)|'
    r'eighty\s+(?:one|two|three|four|five|six|seven|eight|nine)|'
    r'ninety\s+(?:one|two|three|four|five|six|seven|eight|nine)|'
    r'one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|'
    r'thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|'
    r'twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|\d+)'
)


def _detect_navigation(text: str, cur_book: str, cur_chap: int, cur_verse: int):
    """
    Detects navigation commands relative to the current verse on screen.
    Returns (book, chapter, verse) if a navigation command is found, else None.

    Handles:
      "next verse"                     -> cur_verse + 1
      "previous verse" / "back verse"  -> cur_verse - 1
      "next"                           -> cur_verse + 1   (no book name in text)
      "next chapter"                   -> cur_chap + 1, verse 1
      "previous chapter" / "back chapter" -> cur_chap - 1, verse 1
      "back" / "previous"              -> cur_verse - 1   (no book name in text)
      "go back to verse 3"             -> cur_chap:3      (specific target wins)
      "verse 5" / "verse five"         -> cur_chap:5      (same book and chapter)
      "verse twenty eight"             -> cur_chap:28
      "first verse"                    -> cur_chap:1
      "chapter 3 verse 2"              -> same book, ch3:v2
      "chapter 3"                      -> same book, ch3:v1  (no book in text)
      "Acts chapter 4"                 -> Acts 4:1
      "go to Romans 5"                 -> Romans 5:1  (nav verb required)
    """
    if not cur_book:
        return None   # nothing on screen yet, can't navigate

    # Normalise ordinals and autocorrect misheared book names before computing
    # has_book so "second Corinthians" / garbled names are handled correctly.
    text = _normalize_ordinals(text)
    text = _autocorrect_books(text)
    t = text.lower().strip()

    # Compute has_book FIRST — must gate all relative-nav patterns below
    has_book = bool(_NAV_BOOK_RE.search(t))

    # ── Explicit next / previous verse (highest priority) ─────
    if re.search(r'\bnext\s+verse\b', t):
        return (cur_book, cur_chap, cur_verse + 1)
    if re.search(r'\bprevious\s+verse\b|\bprev\s+verse\b|\bback\s+verse\b', t):
        return (cur_book, cur_chap, max(1, cur_verse - 1))

    # ── "next" / "back" / "previous" — only when no book name present ──
    if not has_book and not re.search(r'\d+:\d+', t):
        if re.search(r'\bnext\s+chapter\b', t):
            return (cur_book, cur_chap + 1, 1)
        if re.search(r'\bnext\b', t):
            return (cur_book, cur_chap, cur_verse + 1)
        if re.search(r'\b(previous|prev|back)\s+chapter\b', t):
            return (cur_book, max(1, cur_chap - 1), 1)
        if re.search(r'\b(previous|prev|back)\b', t):
            # If a specific verse is named ("go back to verse 3"), use it
            mv = re.search(rf'\bverse\s+({_NAV_NUM_PAT})\b', t, re.I)
            if mv:
                v = _words_to_num_nav(mv.group(1))
                if v > 0:
                    return (cur_book, cur_chap, v)
            return (cur_book, cur_chap, max(1, cur_verse - 1))

    # ── "chapter N verse M" with no book ──────────────────────
    m2 = re.search(r'\bchapter\s+(\d+)\s+(?:and\s+|from\s+|in\s+)?verse\s+(\d+)\b', t, re.I)
    if m2 and not has_book:
        return (cur_book, int(m2.group(1)), int(m2.group(2)))

    # ── "verse N" with no book name — same book and chapter ───
    m = re.search(rf'\bverse\s+({_NAV_NUM_PAT})\b', t, re.I)
    if m:
        remaining = t.replace(m.group(0), '')
        if not re.search(r'\d+:\d+', remaining) and not has_book:
            v = _words_to_num_nav(m.group(1))
            if v > 0:
                return (cur_book, cur_chap, v)

    # ── "first verse" → verse 1 of current chapter ────────────
    if re.search(r'\bfirst\s+verse\b', t) and not has_book:
        return (cur_book, cur_chap, 1)

    # ── "chapter N" alone — no book, no verse ─────────────────
    m3 = re.search(r'\bchapter\s+(\d+)\b', t, re.I)
    if m3 and not re.search(r'\bverse\b|\d+:\d+', t) and not has_book:
        return (cur_book, int(m3.group(1)), 1)

    # ── Book + chapter without verse ──────────────────────────
    # "Acts chapter 4", "go to Romans 5", "turn to Galatians 3"
    if has_book and not re.search(r'\d+:\d+', t):
        nav_verb = bool(re.search(
            r"\b(go to|turn to|head to|show|open|let'?s\s+(?:go to|read from))\b", t, re.I))
        is_short = len(t.split()) <= 6
        m4 = re.search(r'\bchapter\s+(\d+)\b', t, re.I)
        if m4 and not re.search(r'\bverse\b', t) and (is_short or nav_verb):
            bm = _NAV_BOOK_RE.search(t)
            if bm:
                raw = bm.group(0).lower()
                book = BIBLE_BOOKS.get(raw) or bm.group(0).title()
                return (book, int(m4.group(1)), 1)
        if nav_verb:
            bk_chap = re.search(
                r'\b(' + '|'.join(re.escape(b) for b in _NAV_BOOK_CHECK) + r')\s+(\d+)\b',
                t, re.I)
            if bk_chap and not re.search(r'\bverse\b', t):
                raw = bk_chap.group(1).lower()
                book = BIBLE_BOOKS.get(raw) or bk_chap.group(1).title()
                return (book, int(bk_chap.group(2)), 1)

    return None


def _words_to_num_nav(word: str) -> int:
    """Convert spoken number word or digit string to int for navigation.
    Handles singles (one…nineteen), tens (twenty…ninety), and
    all compounds (twenty one … ninety nine)."""
    _ones = {
        "one":1,"two":2,"three":3,"four":4,"five":5,"six":6,"seven":7,
        "eight":8,"nine":9,"ten":10,"eleven":11,"twelve":12,"thirteen":13,
        "fourteen":14,"fifteen":15,"sixteen":16,"seventeen":17,"eighteen":18,
        "nineteen":19,
    }
    _tens = {
        "twenty":20,"thirty":30,"forty":40,"fifty":50,
        "sixty":60,"seventy":70,"eighty":80,"ninety":90,
    }
    w = word.strip().lower()
    if w.isdigit(): return int(w)
    if w in _ones: return _ones[w]
    if w in _tens: return _tens[w]
    # Compound: "twenty one", "thirty five", "ninety nine", etc.
    parts = w.split(None, 1)
    if len(parts) == 2:
        t_val = _tens.get(parts[0], 0)
        o_val = _ones.get(parts[1], 0)
        if t_val and o_val:
            return t_val + o_val
    return 0


# A small set of book name fragments to check whether the text
# contains a book reference (used to avoid triggering nav on full refs)
_NAV_BOOK_CHECK = [
    "genesis","exodus","leviticus","numbers","deuteronomy","joshua","judges",
    "ruth","samuel","kings","chronicles","ezra","nehemiah","esther","job",
    "psalms","psalm","proverbs","ecclesiastes","isaiah","jeremiah","lamentations",
    "ezekiel","daniel","hosea","joel","amos","obadiah","jonah","micah","nahum",
    "habakkuk","zephaniah","haggai","zechariah","malachi","matthew","mark",
    "luke","john","acts","romans","corinthians","galatians","ephesians",
    "philippians","colossians","thessalonians","timothy","titus","philemon",
    "hebrews","james","peter","jude","revelation","revelations",
    "solomon","song","songs",
]
_NAV_BOOK_RE = re.compile(
    r'\b(?:' + '|'.join(re.escape(b) for b in _NAV_BOOK_CHECK) + r')\b',
    re.IGNORECASE
)

# Unambiguous operator nav phrases — safe to fire on interim without waiting
# for the final result (cuts latency from ~1.5 s to ~200 ms).
_FAST_NAV_RE = re.compile(
    r'\b(?:next\s+verse|previous\s+verse|prev\s+verse|back\s+verse'
    r'|next\s+chapter|previous\s+chapter|prev\s+chapter|back\s+chapter)\b',
    re.IGNORECASE
)


def _has_verse_number(text: str) -> bool:
    """
    Returns True only if the text contains an explicit verse number.
    Prevents interim like "John chapter 3" from defaulting to verse 1.
    Passes:   "John 3:16"  "chapter 3 verse 16"  "John three sixteen"
              "verse sixteen"  "chapter three and verse sixteen"
    Blocks:   "John chapter 3"  "in the book of Romans"
    """
    # Strip commas/semicolons so "chapter 4, verse 5" is treated the same as
    # "chapter 4 verse 5" before running the pattern checks.
    t = text.lower().replace(',', ' ').replace(';', ' ')
    # Spoken number words (covers raw speech before word→digit conversion)
    num = (r'(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|'
           r'thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|'
           r'twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|\d+)')
    # Explicit colon format: "3:16"
    if re.search(r'\d+:\d+', t):
        return True
    # "verse N" — digit or spoken word: "verse 16" or "verse sixteen"
    if re.search(rf'\bverse\s+{num}\b', t):
        return True
    # "chapter N verse N" — digits or spoken words, optional "and"/"from" between
    if re.search(rf'\bchapter\s+{num}\b.*?\bverse\s+{num}\b', t):
        return True
    # Two spoken numbers after a book name e.g. "John three sixteen"
    if re.search(rf'\b{num}\s+{num}\b', t):
        return True
    return False


def parse_scripture(raw_text: str) -> list:
    """
    Main parser. Returns list of (book, chapter, verse).
    Handles:
      - Direct: "John 3:16", "John 3 16", "John chapter 3 verse 16"
      - Spoken numbers: "John three sixteen"
      - Famous passages: "the shepherd psalm", "the love chapter"
      - Indirect: uses python-scriptures as final pass
      - Commas in transcript: "Matthew 7, verse 11" — commas are stripped
        before detection so they never break pattern matching.
    """
    # Strip commas and semicolons before any processing
    raw_text = raw_text.replace(',', ' ').replace(';', ' ')
    raw_text = re.sub(r'  +', ' ', raw_text).strip()
    # Normalise ordinals ("second Corinthians" → "2 Corinthians") before
    # autocorrect so the numbered-book lookup always has digits to work with.
    raw_text = _normalize_ordinals(raw_text)
    # Apply fuzzy autocorrect before parsing
    corrected = _autocorrect_books(raw_text)
    results = []
    text = corrected.lower()
    # ── Pass 1: Famous passages (instant lookup)
    for phrase, ref in FAMOUS_PASSAGES.items():
        if phrase in text:
            results.append(ref)

    # ── Pass 2: Normalise and handle spoken numbers
    normalised = normalise_spoken(text)
    with_digits = extract_spoken_numbers(normalised)

    # ── Pass 3: python-scriptures on normalised text
    try:
        found = scriptures.extract(with_digits)
        # Filter out verse-1 defaults: python-scriptures returns v=1 when only a
        # book+chapter is found (no explicit verse). Only keep v=1 results if
        # the original raw text contains an explicit verse number reference.
        found_filtered = [(b, c, v, vs, ve) for b, c, v, vs, ve in found
                          if v != 1 or _has_verse_number(raw_text)]
        for b, c, v, _, _ in found_filtered:
            if c > 0 and v > 0:
                ref = (_canon_book(b), c, v)
                if ref not in results:
                    results.append(ref)
    except Exception:
        pass

    # ── Pass 4: Strict regex on normalised text
    pattern = re.compile(
        rf'({BOOK_PATTERN})\s+(\d{{1,3}}):(\d{{1,3}})',
        re.IGNORECASE
    )
    for m in pattern.finditer(with_digits):
        raw_book = m.group(1).lower()
        book = BIBLE_BOOKS.get(raw_book)
        if not book:
            continue
        chapter = int(m.group(2))
        verse   = int(m.group(3))
        if chapter > 150 or verse > 176:
            continue
        # Filter out verse-1 defaults from regex matches as well
        if verse == 1 and not _has_verse_number(raw_text):
            continue
        ref = (book, chapter, verse)
        if ref not in results:
            results.append(ref)

    return results[:3]   # cap at 3 per transcript

# ═══════════════════════════════════════════════════════════════
#  BIBLE API  (~line 825)
#  fetch_verse() resolves a (book, chapter, verse, translation)
#  tuple to display text using a 3-level lookup:
#    1. Local Bible (getbible.net JSON, downloaded once) — instant
#    2. In-memory session cache — microseconds
#    3. bible-api.com — network fallback
#  The local Bible is downloaded in the background on first run.
# ═══════════════════════════════════════════════════════════════

_LOCAL_BIBLE_DIR = os.path.join(os.path.dirname(SETTINGS_FILE), "bible_local")

# Canonical book name → getbible.net book number (Protestant canon order)
_GETBIBLE_BOOK_NUM = {
    "Genesis":1,"Exodus":2,"Leviticus":3,"Numbers":4,"Deuteronomy":5,
    "Joshua":6,"Judges":7,"Ruth":8,"1 Samuel":9,"2 Samuel":10,
    "1 Kings":11,"2 Kings":12,"1 Chronicles":13,"2 Chronicles":14,"Ezra":15,
    "Nehemiah":16,"Esther":17,"Job":18,"Psalms":19,"Proverbs":20,
    "Ecclesiastes":21,"Song of Solomon":22,"Isaiah":23,"Jeremiah":24,
    "Lamentations":25,"Ezekiel":26,"Daniel":27,"Hosea":28,"Joel":29,
    "Amos":30,"Obadiah":31,"Jonah":32,"Micah":33,"Nahum":34,"Habakkuk":35,
    "Zephaniah":36,"Haggai":37,"Zechariah":38,"Malachi":39,
    "Matthew":40,"Mark":41,"Luke":42,"John":43,"Acts":44,
    "Romans":45,"1 Corinthians":46,"2 Corinthians":47,"Galatians":48,
    "Ephesians":49,"Philippians":50,"Colossians":51,
    "1 Thessalonians":52,"2 Thessalonians":53,
    "1 Timothy":54,"2 Timothy":55,"Titus":56,"Philemon":57,"Hebrews":58,
    "James":59,"1 Peter":60,"2 Peter":61,
    "1 John":62,"2 John":63,"3 John":64,"Jude":65,"Revelation":66,
}
# Reverse: book number → canonical name
_GETBIBLE_NUM_BOOK = {v: k for k, v in _GETBIBLE_BOOK_NUM.items()}

# getbible.net translation slug for each internal translation key
_GETBIBLE_TRANS = {
    "KJV": "kjv", "NKJV": "kjv",
    "WEB": "web", "NIV": "web",
    "ASV": "asv", "ESV": "asv", "NASB": "asv",
    "BBE": "bbe", "DARBY": "darby", "YLT": "ylt",
}

# In-memory local Bible: (book_lower, chapter, verse, trans_upper) → (text, ref)
_LOCAL_BIBLE: dict = {}
_local_bible_lock = threading.Lock()


def _local_bible_file(trans: str) -> str:
    return os.path.join(_LOCAL_BIBLE_DIR, f"bible_{trans.lower()}.json")


def _load_local_bible(trans: str = "KJV"):
    """Load pre-downloaded Bible JSON into _LOCAL_BIBLE (called at startup)."""
    path = _local_bible_file(trans)
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        with _local_bible_lock:
            for k, v in raw.items():
                parts = k.split("|")
                if len(parts) == 4:
                    _LOCAL_BIBLE[(parts[0], int(parts[1]), int(parts[2]), parts[3])] = tuple(v)
    except Exception:
        pass


def _save_local_bible(trans: str, data: dict):
    """Persist *data* (same key format as _LOCAL_BIBLE) to disk."""
    os.makedirs(_LOCAL_BIBLE_DIR, exist_ok=True)
    path = _local_bible_file(trans)
    try:
        raw = {f"{k[0]}|{k[1]}|{k[2]}|{k[3]}": list(v) for k, v in data.items()}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(raw, f)
    except Exception:
        pass


def _download_local_bible(trans_upper: str = "KJV"):
    """
    Download the full Bible for *trans_upper* from getbible.net one book at a
    time and store it locally.  Runs in a daemon thread — the API fallback
    keeps working while this is in progress.  Saves after each book so progress
    survives an app restart.  After completion, every verse lookup is instant.
    """
    slug = _GETBIBLE_TRANS.get(trans_upper, "kjv")
    # Load whatever is already on disk so we can append to it
    existing = {}
    path = _local_bible_file(trans_upper)
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            for k, v in raw.items():
                parts = k.split("|")
                if len(parts) == 4:
                    existing[(parts[0], int(parts[1]), int(parts[2]), parts[3])] = tuple(v)
        except Exception:
            pass

    for book_num in range(1, 67):
        book_name = _GETBIBLE_NUM_BOOK.get(book_num)
        if not book_name:
            continue
        # Skip if all chapters of this book already downloaded
        if any(k[0] == book_name.lower() and k[3] == trans_upper for k in existing):
            continue
        url = f"https://getbible.net/v2/{slug}/{book_num}.json"
        try:
            r = requests.get(url, timeout=15)
            if r.status_code != 200:
                continue
            book_data = r.json()
            chapters = book_data.get("chapters", {})
            book_entries = {}
            for ch_str, ch_data in chapters.items():
                try:
                    ch = int(ch_str)
                except ValueError:
                    continue
                for v_str, v_data in ch_data.get("verses", {}).items():
                    try:
                        vnum = int(v_str)
                    except ValueError:
                        continue
                    vtxt = " ".join(v_data.get("text", "").split())
                    if vtxt:
                        ck = (book_name.lower(), ch, vnum, trans_upper)
                        book_entries[ck] = (vtxt, f"{book_name} {ch}:{vnum}")
            if book_entries:
                existing.update(book_entries)
                with _local_bible_lock:
                    _LOCAL_BIBLE.update(book_entries)
                # Save after each book so progress is preserved on restart
                _save_local_bible(trans_upper, existing)
        except Exception:
            continue  # skip books that fail; use API fallback for those


def _ensure_local_bible(trans_upper: str = "KJV"):
    """Start a background download of the local Bible if not yet fully present."""
    path = _local_bible_file(trans_upper)
    # Check if the file is reasonably complete (>30 MB = full Bible)
    if os.path.exists(path) and os.path.getsize(path) > 30_000_000:
        return
    threading.Thread(
        target=_download_local_bible, args=(trans_upper,), daemon=True
    ).start()


# Load whatever we already have on disk at startup
_load_local_bible("KJV")
_load_local_bible("WEB")
_ensure_local_bible("KJV")   # kick off download if missing

# In-memory verse cache — avoids repeated API calls for the same verse.
# Keyed by (book, chapter, verse, translation). Holds up to 5000 entries.
_VERSE_CACHE: dict = {}
_CACHE_MAX = 5000
_DISK_CACHE_FILE = os.path.join(os.path.dirname(SETTINGS_FILE), "verse_cache.json")

def _load_disk_cache():
    try:
        with open(_DISK_CACHE_FILE, encoding="utf-8") as f:
            raw = json.load(f)
        for k, v in raw.items():
            parts = k.split("|")
            if len(parts) == 4:
                _VERSE_CACHE[(parts[0], int(parts[1]), int(parts[2]), parts[3])] = tuple(v)
    except Exception:
        pass

def _save_disk_cache():
    try:
        raw = {f"{k[0]}|{k[1]}|{k[2]}|{k[3]}": list(v) for k, v in _VERSE_CACHE.items()}
        with open(_DISK_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(raw, f)
    except Exception:
        pass

_load_disk_cache()

# Tracks (book_lower, chapter, translation) already prefetched this session
_prefetched_chapters: set = set()

def _prefetch_chapter(book: str, chapter: int, translation: str):
    """
    Background-fetch every verse in *chapter* from bible-api.com and store
    them all in the in-memory + disk cache.  Called after the first verse in a
    chapter is fetched, and also speculatively from _on_interim the moment a
    'Book chapter N' pattern is heard — before the verse number is spoken.
    This means by the time the pastor finishes the verse reference the text is
    already in cache and display is instant.
    """
    key_id = (book.lower(), chapter, translation.upper())
    if key_id in _prefetched_chapters:
        return
    _prefetched_chapters.add(key_id)

    trans_map = {
        "KJV":"kjv","WEB":"web","ASV":"asv","BBE":"bbe",
        "DARBY":"darby","YLT":"ylt","NKJV":"kjv","NIV":"web",
        "ESV":"asv","NASB":"asv",
    }
    t = trans_map.get(translation.upper(), "kjv")

    def _work():
        # Request a wide verse range — bible-api clips at the chapter boundary
        url = f"https://bible-api.com/{book}+{chapter}:1-176?translation={t}"
        try:
            r = requests.get(url, timeout=8)
            if r.status_code != 200:
                return
            d = r.json()
            verses = d.get("verses", [])
            if not verses:
                return
            added = 0
            for v in verses:
                vch  = int(v.get("chapter", chapter))
                vnum = int(v.get("verse", 0))
                vtxt = " ".join(v.get("text", "").split())
                vtxt = re.sub(r'\s*[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\s+\d+:\d+\s*$', '', vtxt).strip()
                if vnum > 0 and vtxt:
                    ck = (book.lower(), vch, vnum, translation.upper())
                    if ck not in _VERSE_CACHE:
                        _VERSE_CACHE[ck] = (vtxt, f"{book} {vch}:{vnum}")
                        added += 1
            if added:
                _save_disk_cache()
        except Exception:
            pass

    threading.Thread(target=_work, daemon=True).start()

# Compiled pattern for early book+chapter detection in interim text
# Matches "Book Chapter N" without requiring a verse number
_EARLY_CHAPTER_RE = re.compile(
    r'\b(' + '|'.join(re.escape(b) for b in sorted(BIBLE_BOOKS.keys(), key=len, reverse=True)) + r')'
    r'\s+(?:chapter\s+)?(\d{1,3})\b',
    re.IGNORECASE
)


def fetch_verse(book: str, chapter: int, verse: int, translation: str):
    """Return verse text.  Priority: local Bible → session cache → bible-api.com."""
    cache_key = (book.lower(), chapter, verse, translation.upper())
    # 1. Local Bible (instant — no network)
    with _local_bible_lock:
        if cache_key in _LOCAL_BIBLE:
            return _LOCAL_BIBLE[cache_key]
    # 2. Session cache (instant)
    if cache_key in _VERSE_CACHE:
        return _VERSE_CACHE[cache_key]
    ref = f"{book} {chapter}:{verse}"
    # bible-api.com supports: kjv, web, asv, bbe, darby, ylt, oeb-us, webbe
    # Map unsupported translations to closest available
    trans_map = {
        "KJV":"kjv","WEB":"web","ASV":"asv","BBE":"bbe",
        "DARBY":"darby","YLT":"ylt","NKJV":"kjv","NIV":"web",
        "ESV":"asv","NASB":"asv",
    }
    t = trans_map.get(translation.upper(), "kjv")
    url = f"https://bible-api.com/{ref}?translation={t}"
    try:
        r = requests.get(url, timeout=3)
        if r.status_code == 200:
            d = r.json()
            if "error" in d:
                return None, None
            text = " ".join(d["text"].split())
            # Strip any trailing reference bible-api sometimes appends
            text = re.sub(
                r'\s*[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\s+\d+:\d+\s*$',
                '', text).strip()
            result = (text, d.get("reference", ref))
            if len(_VERSE_CACHE) >= _CACHE_MAX:
                try: del _VERSE_CACHE[next(iter(_VERSE_CACHE))]
                except Exception: pass
            _VERSE_CACHE[cache_key] = result
            _save_disk_cache()
            # Warm the rest of this chapter in the background so any nearby
            # verse the pastor references next is served from cache instantly.
            _prefetch_chapter(book, chapter, translation)
            return result
        return None, None
    except Exception:
        return None, None

# ═══════════════════════════════════════════════════════════════
#  WEBSOCKET SERVER  (~line 930)
#  WSServer — receives transcript messages from the browser
#  listener page (Google Speech mode). Runs on ws_port (8765).
#  Both the browser listener and the Electron frontend connect
#  here. The HeadlessApp has its own built-in WebSocket handler
#  (see HeadlessApp below) — WSServer is used only in tkinter mode.
# ═══════════════════════════════════════════════════════════════
class WSServer:
    def __init__(self, port: int, on_transcript, on_interim=None):
        self.port = port
        self.on_transcript = on_transcript
        self.on_interim = on_interim or (lambda t: None)
        self._thread = None
        self._loop   = None
        self._clients = set()
        self.autostart = False  # if True, send "start" to browser on connect

    def set_audio_callback(self, cb):
        self._audio_cb = cb

    def broadcast(self, msg: dict):
        if not self._loop: return
        data = json.dumps(msg)
        async def _s():
            for c in list(self._clients):
                try: await c.send(data)
                except Exception: self._clients.discard(c)
        asyncio.run_coroutine_threadsafe(_s(), self._loop)

    def start(self):
        self.port = _find_free_port(self.port)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._serve())

    async def _serve(self):
        async def handler(websocket):
            self._clients.add(websocket)
            try:
                # Auto-start mic if listening was already active when browser connected
                if self.autostart:
                    await websocket.send(json.dumps({"type": "start"}))
                async for message in websocket:
                    try:
                        data = json.loads(message)
                        msg_type = data.get("type", "")
                        text = data.get("text", "").strip()
                        if not text:
                            continue
                        if msg_type == "transcript":
                            self.on_transcript(text)
                        elif msg_type == "interim":
                            self.on_interim(text)
                    except Exception:
                        pass
            finally:
                self._clients.discard(websocket)
        async with websockets.serve(handler, "127.0.0.1", self.port, reuse_address=True):
            await asyncio.Future()  # run forever

# ═══════════════════════════════════════════════════════════════
#  HTTP SERVER  (~line 985)
#  Serves the browser listener HTML page on port 8766.
#  The page auto-opens in Chrome/Edge when listening starts.
#  To change the listener page appearance, edit get_listener_html().
# ═══════════════════════════════════════════════════════════════
class SilentHandler(http.server.BaseHTTPRequestHandler):
    html_content = ""

    def do_GET(self):
        try:
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(self.html_content.encode())
        except (ConnectionAbortedError, BrokenPipeError, OSError):
            pass  # browser closed tab before response finished — harmless

    def log_message(self, format, *args):
        pass  # suppress console output

    def handle_error(self, request, client_address):
        pass  # suppress all connection error tracebacks


def _find_free_port(preferred: int, attempts: int = 20) -> int:
    """Return preferred port if free, else the next available port."""
    import socket as _socket
    for p in range(preferred, preferred + attempts):
        try:
            with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as s:
                s.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
                s.bind(("", p))
                return p
        except OSError:
            continue
    # Fall back to OS-assigned port
    with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def start_http_server(html: str, port: int = 8766):
    SilentHandler.html_content = html
    port = _find_free_port(port)
    socketserver.TCPServer.allow_reuse_address = True
    server = socketserver.TCPServer(("127.0.0.1", port), SilentHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return port

# ─────────────────────────────────────────────────────────────
#  APP
# ─────────────────────────────────────────────────────────────

# ═══════════════════════════════════════════════════════════════
#  DEEPGRAM LISTENER  (~line 1165)
#  Real-time cloud transcription via Deepgram nova-2.
#  Free tier: 200 hrs/month. Get a key at console.deepgram.com
#  Audio is streamed directly from the microphone over WebSocket.
#  Automatically reconnects if the connection drops mid-service.
# ═══════════════════════════════════════════════════════════════
class DeepgramListener:
    def __init__(self, api_key, on_transcript, on_status, on_interim=None, device_id=-1):
        self.key           = api_key
        self.on_transcript = on_transcript
        self.on_status     = on_status
        self.on_interim    = on_interim
        self.device_id     = device_id
        self.running       = False
        self.fs            = 16000
        self._audio_q      = queue.Queue(maxsize=200)

    def start(self, device_id=None):
        if not self.key:
            self.on_status("No API key"); return
        if device_id is not None:
            self.device_id = device_id
        try:
            import sounddevice as _sd
        except ImportError:
            self.on_status("sounddevice not installed"); return
        self.running = True
        self._audio_q = queue.Queue(maxsize=200)
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self.running = False
        self.on_status("Stopped")

    def _run(self):
        # Reconnect loop — Deepgram sends 1011 internal error occasionally,
        # which is a server-side timeout. We just reconnect automatically.
        while self.running:
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(self._stream(loop))
            except Exception as e:
                if self.running:
                    self.on_status(f"Error: {str(e)[:50]}")
                    time.sleep(2)
            finally:
                try: loop.close()
                except Exception: pass
            if self.running:
                self.on_status("Reconnecting...")
                time.sleep(1.5)

    async def _stream(self, loop):
        import sounddevice as _sd
        url = (
            "wss://api.deepgram.com/v1/listen"
            "?model=nova-2&language=en-US&punctuate=true"
            "&interim_results=true&endpointing=300"
            "&smart_format=true&utterance_end_ms=1000"
        )
        # websockets 16.0 requires a list of tuples, not a dict
        auth = [("Authorization", f"Token {self.key}")]

        def audio_cb(indata, frames, time_info, status):
            if self.running:
                try: self._audio_q.put_nowait(bytes(indata))
                except queue.Full: pass

        # Use specific device if provided, else default
        dev = self.device_id if self.device_id >= 0 else None

        stream = _sd.RawInputStream(
            device=dev,
            samplerate=self.fs, channels=1, dtype="int16",
            blocksize=int(self.fs * 0.1), callback=audio_cb)

        self.on_status("Connecting...")
        ws = None
        try:
            # additional_headers is the correct kwarg for websockets 14+/16.0
            ws = await websockets.connect(url, additional_headers=auth)

            self.on_status("Live - real-time")
            stream.start()

            async def sender():
                while self.running:
                    try:
                        # Use the explicit loop reference - safe across threads
                        chunk = await loop.run_in_executor(
                            None, lambda: self._audio_q.get(timeout=0.5))
                    except Exception:
                        if not self.running: break
                        continue  # queue timeout — keep waiting for audio
                    try:
                        await ws.send(chunk)
                    except Exception:
                        break  # WebSocket closed — exit so gather() can complete
                try: await ws.send(json.dumps({"type": "CloseStream"}))
                except Exception: pass

            async def receiver():
                async for msg in ws:
                    if not self.running: break
                    try:
                        d = json.loads(msg)
                        if d.get("type") == "Results":
                            alts = d.get("channel", {}).get("alternatives", [])
                            if alts:
                                txt = alts[0].get("transcript", "").strip()
                                if not txt: continue
                                if d.get("is_final", False):
                                    # Final — run scripture detection
                                    self.on_transcript(txt)
                                else:
                                    # Interim — show in transcript panel immediately
                                    if hasattr(self, 'on_interim') and self.on_interim:
                                        self.on_interim(txt)
                        elif d.get("type") == "Metadata":
                            pass # connection established
                        elif d.get("type") == "Error":
                            self.on_status(f"DG Error: {d.get('message')}")
                    except Exception as e:
                        pass

            async def keepalive():
                while self.running:
                    for _ in range(80):
                        if not self.running: return
                        await asyncio.sleep(0.1)
                    try: await ws.send(json.dumps({"type":"KeepAlive"}))
                    except Exception: return
            await asyncio.gather(sender(), receiver(), keepalive())

        except Exception as e:
            msg = str(e)
            if "401" in msg or "403" in msg:
                self.on_status("Invalid API key — check console.deepgram.com")
                self.running = False  # auth error won't fix itself — stop retrying
            elif "404" in msg:
                self.on_status("API endpoint not found — check your Deepgram plan")
                self.running = False
            else:
                self.on_status(f"Error: {msg[:50]}")
        finally:
            try: stream.stop(); stream.close()
            except Exception: pass
            if ws:
                try: await ws.close()
                except Exception: pass

# ═══════════════════════════════════════════════════════════════
#  PROBIBLEAPP  (~line 1315)  — LEGACY TKINTER UI
#  The original standalone desktop GUI using tkinter.
#  Still works but is NOT the primary interface — the Electron
#  frontend (HeadlessApp below) is what ships with BibleCue.
#  This class is preserved for users who want a no-Electron build.
#  If you only care about the Electron app, skip to HeadlessApp.
# ═══════════════════════════════════════════════════════════════
class ProBibleApp:
    # Embedded logo — base64 encoded PNG
    _LOGO_B64 = """/9j/4AAQSkZJRgABAQAAAQABAAD/4gHYSUNDX1BST0ZJTEUAAQEAAAHIAAAAAAQwAABtbnRyUkdCIFhZWiAH4AABAAEAAAAAAABhY3NwAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAQAA9tYAAQAAAADTLQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAlkZXNjAAAA8AAAACRyWFlaAAABFAAAABRnWFlaAAABKAAAABRiWFlaAAABPAAAABR3dHB0AAABUAAAABRyVFJDAAABZAAAAChnVFJDAAABZAAAAChiVFJDAAABZAAAAChjcHJ0AAABjAAAADxtbHVjAAAAAAAAAAEAAAAMZW5VUwAAAAgAAAAcAHMAUgBHAEJYWVogAAAAAAAAb6IAADj1AAADkFhZWiAAAAAAAABimQAAt4UAABjaWFlaIAAAAAAAACSgAAAPhAAAts9YWVogAAAAAAAA9tYAAQAAAADTLXBhcmEAAAAAAAQAAAACZmYAAPKnAAANWQAAE9AAAApbAAAAAAAAAABtbHVjAAAAAAAAAAEAAAAMZW5VUwAAACAAAAAcAEcAbwBvAGcAbABlACAASQBuAGMALgAgADIAMAAxADb/2wBDAAUDBAQEAwUEBAQFBQUGBwwIBwcHBw8LCwkMEQ8SEhEPERETFhwXExQaFRERGCEYGh0dHx8fExciJCIeJBweHx7/2wBDAQUFBQcGBw4ICA4eFBEUHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh7/wAARCAfQB9ADASIAAhEBAxEB/8QAHQABAAICAwEBAAAAAAAAAAAAAAcIBgkDBAUBAv/EAE8QAQABAwMDAgMFAwkEBgcJAQABAgMEBQYRBxIhCDETQVEUImFxkTKBoRVCUlNicpKxwRYXI1QYMzRDgtEkNURFVaKzJSY2Y3OElKOydf/EABQBAQAAAAAAAAAAAAAAAAAAAAD/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIRAxEAPwCmQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA9nbuh3tWyqLUW7801T70Up82V6cNL1zTYysrUNas1dvPFFNHH8aQVqEr9V+luDs6a4xsvPu9v9dFP+kQigAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAABmOwdi6lubKtRYtVTRMx7Q6WxNtZO4dUox7duqqJnj2X49O/TDG0XTLV7Jx47opifMA8XoZ0SxNPtWr+o41PMREzzCweHouBgYlVnGsU0x2/R6Nq3Raoii3TFNMe0Q+1RzEx9QUv9X2JTbsXq4oiPdTmfdf31g7drv6LcvWqZmZpmfEKD5di5j367dymYmmZgHCAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA7mjYN3UdQtYtqiapqqiPDqUxNU8RHMrL+l/pdGtZNnUL9rmImJ8wCUPTN0kjEt2NRy8eI54nzC1WJjWsWzFqzTFNMfR1tA02zpWmWsSzRFMUU+eId8AAGCdYtFo1fQLlNdEVcUT8muDrRo9Gk7huWqKe2JrltN1vGpytNvW5jnmiWuD1W6fXi7uqmKZinvkEJgAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA5sO1N/Kt2o96quAZZ0o2zd3LuG3i0W5qjuiPZsa6G7Mja+iW6KrcU1dkfJXr0n9PasXNs6lesT21TE8zC59ummiimmmOIiOIB+gAAAfK47qJpn5xwqf6p+mtzUbd/U7drnjmeeFsWP7/ANLt6ptrJx6rcVVTTPHgGo/UsarEzruPXHE0VTHDrpZ667Ju6FrmTkTammmqqZ9kTAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAPsRMzxEcy5MaxdyLsWrNE11T7RCfOgnRjK3Hft3s/HqimZifNIIf0Daep6tHNqzXET+D5uLbGZo1POTTVH5tjO1uiejaRYoj4dvmI8+Fd/WNs6jSKZnEs8U/hAKnD7XTVTV21RxL4AAAAAAAybp1pF/U9yYlNFuaqIuRz4Y3boquVRTTHMytz6RenFvUqLedk2vMcVczALSdHNDx9N2dhzFqmK5pj5fgzl1dLw6MHCt41uOKaI4h2gAAAAHyummumaao5ifd9AVW9Z22bc6TcyMezE1TTz4hRK7RVbuVUVxMVRPExLbX1D2hi7q06uxkUxP3JiOWu71B9P7219x3fgWZi3NU+0AiMfZiYnifeHwAAAAAAAABy/Z7/b3fCr4+vD2Nm6Hka3q9nHtUTVTNUc8Qur079PWm6hti1fzLVFNdVMftQCh0xMe74sb126H5Og3rtenY9U00zM/dpV6zsPIwr82ci3NFcTxxMA64AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAD7TTNVUUx7zPEPjNOmO0MrcmrWabVqqqmK49oBJnp36V39a1Ozl5FmarczE+YX32ZtfT9v6bas41immuKY5mIYp0M2hY0HbdmLlmKbkUx7wkwBFPXbZEbo0q9XNuKpoomfZKzjybdN7HuWqo5iqmYmAakepOkV6LujIwq6O2KZnhjK4fqd6T1XMvI1bGsTz5nxCouoYd/CyKrN+iaZpnjzAOsAAAAD7ETM8R7gy3pdos61uC3j9vdHdDY50F2zGg6BbiKIp5oj5KWelLb97K3Xbv3LU9s1x8mxfScajFwLVqiOOKYB2gAAAAAAAEF+orp/a1nR8nPizFVdNMz7J0dTV8G1qOBcxL0RNNcceQah91add03W8jHuW5o4rnjw8pcj1KdF4tTe1DCsczPNXMQqFquDe0/Mrxr9M01Uzx5B1AAAAAAH2ImZiIjmZfGe9I9nX9ybgs2qrUzb7o+QJm9Ieyf5TyreRkWPaeeZhevSsWnCwbWNRERFEceGB9Gth4u1dHs1UW4prmmPkkYHk7h0DT9axblrLsU1zVTMRMwpl6juilzE+PqWFj8UczMcQvG8rdGi42uaXcwsmimqKonjmAag83Gu4mTXYvUzTVTPHlwLFep7phG39RvZOLZ4iZmfEK710VUVTTVHEwD8gAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA5Me1Veu026feZXT9GW1caPh38qzTM8c+YVE2Rhzna/YsxHPNUNiPpz29VpmlWbs25p5pgE0WbVuzbii3TFNMfKH7AAAHibt0HG1vTLmNdt0zMx84Ug9RvSO/p2TdycTHnjmZ8Qv0x/d21sDcOPNrJt0zzHHMwDUbmYt7Fv1Wb1FVNVM8eYcK6PXjoLZx8W9madj81TEzHbSqRre2tV0m9coysaummiZjmYB4oADuaLYqydUx7NMc91cQ6aUeiG1KtZ1vHuzRNUU1RPsC3fpm2JbwtKsahNqImaYnnhYymOIiPoxrpvptGmbYxrFNPbMUxyyUAAAAAAAAAAHl7k0jG1jTbuNft01c0zxzCgPqX6a5Gna5ey8SxMURMz4hsSYN1J2Lg7j029FdqmquaZ+QNUVyiq3XNFcTFUTxMPymzrx0xv7by71+1YqinumfEIUqiaZmJjiYB8AABzYmNeyrsWrFE11T8oByaXi3czNtWLNE11VVRHiF7PS/07osadY1C7YiK4iJ8wjH0vdHKtVybebqWPMRHFX3oXb2zomNoen04mNTEUxHHgHp2qIot00R7Uxw/QAAAibr3s6jXtGvXptxVMUz8muXqLpVek7kv41VE0xFU8eG2nVcajLwL1iuOYqpmFGPU709ow83I1Cm1x5meeAVYH6u09tyqn6Tw/IAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAPtMc1RH1kEm+n/Rqs7d+PXVTzT3R/m2Y7OwLWDoOLbopiJ+HEqM+l3RO7Use/2c/eieeF99Np7cCxT9KIB2AAAAAAdfPwsfOsTZybcV0THHEoT6sdF8DW7dyrDx6Ymr6Up0Aa1Op/RDVNuVXsiizX2RzPHCF8nHvY92q3dt1U1UzxPMNue8Nq6fuLCrsZNqiZmOOZhVLrN0DosfGv4GNzM8z92AU0W39HukUZcWrs0xPtKvmr9ONx4ebXZpwrlVMT4nhcP0Z7Wy9K02mc21VTVFPPmAWbw7UWMai3HtEOYAAAAAAAAAAAAARn1m6fY+6tLriizTNfbPya/esHT3O2zq93ixV2RVPtDadPmOJRb1c6YadubT796LNM3ZiZ9gauppqieJieSKap9qZ/RZvU+hGTTqtdNGLV2d39Fk+1fT7ReuUxfxvH40gqvtfbmfrmbTYsWa+JnjnhaPot6f7/ADZzsyxMxPEzzCcNh9DNG0Sqi/VZoiqPPsmLTsOxg4tGPYoimmmOPEA8nZu3MTb+n0WMe3TTMU8TxD3gAAAAAQX6rdIou7TvZFNMczTKdEcdfsL7bs67b45+7INWuoUTRnXqZjjiuXAyTf8Apk6frl+nt4ia5Y2AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA5MeOb9uPrVDjdrSbc3dSx7cfOuAXb9Kei92Fj3+z5RPstjZp7LVNP0hC3pb0qizs+zemnieyE1gAAAAAAAAODLxMfLomi/aprifrDnAYpn7D0HKud84luJ/uvW0LQ8PR6OzFoppjjjxD1QAAAAAAAAAAAAAAAmImJiY5iQB1KtNwaqu6ceiZ+vDltYuPa/6u1TT+5zAAAAAAAAADFOp9qLu3btM/0ZZWxfqTPG37v92Qa4evONRZ1u7NMfz5Ralj1ATzrVz+/wD6onAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAettOmKtdxon+nDyXv7Fxa8jXrHbEzxVANlPpypinZNmI/owlBGvp9sVWNmWaao4nthJQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAADF+pMc7fu/3ZZQxPqjdi1ty7Mz/ADZBrr9QEca1c/vonSh13yIva3diJ/nT/mi8AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAB9ojmqI+r4/dn/AK6j+9AM+2t05ztbxIv2bdcxMc+IedunYmq6PVP/AKPXMR+C53pQ0bS9Q2vbi/RRVXNEe6V9f6Ubf1WiqLlmjmf7INV9ePfomYrs3KZj60y45iYniYmF/t5enzTeK6sbGpnn24hXvqV0R1XCrquYWNX4+lIIESp0D0n+UNatz293FbE8vYuv41Xbcxaon+7KwXpG2TmUapTdzLNUff58wC4nTPC+w7dtW+3j7sMqcGDj0Y2PRaojiIiHOAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAwDrhf+z7Su188fdln6JfUzmxjbKu8TxPZINeXVHN+069ejnn70sOejuLIqydXyK5nn78vOAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAfq3+3T+cPy+0/tR+YLpek3VrtvFsWouTEeFwcavvsUV/WFDPS9qkW8nHtd3zhe3Sau/TrNX1pB2a6aa44qpiY/F0M7RtNzbc0X8W3Vz8+HoAI01/pZpObemu3jW+Jn+i9vZey8Pb8xNm1TTMfSGYAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAACBPVllTG2L1uJ9qJT3PiJlWv1Y5HOiZFPPykFAc2ecu9P9uf8ANwuXK85Vzj51z/m9DT9A1LNp7rFmqY/IHlDvalpeXp88ZFuaf3OiAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAACcvTdk1067j0xM8d0Ni23J50XGn+xDW/6cb1Ebgx6Znz3w2P7amJ0TFmP6EA9EAAAAAAAAH5uV0W6JqrqimI+cg/Q8PO3RpWJVMXb9PMfi8vI6h7esft5NP+IGYPk1Ux7zEIz1jrJtjDtVduTRNUfWpG+5OvunU1VfByqf3SCyXxbX9ZT+r8zkWI97tEfvU8yfUFTFdXGT/Fj2ueoa/ET8LIq/UF4a87DojmrJtR/4nTydf0mxEzXmW/H0lrx1r1Ba5d7qce9cn97FsvrTu2/VM/HmIn61yDY/l760ezMxGRR/iePndTtKsc8X6P1a573VTc92fvX5/wAUunf6h7gvftX5/wAUg2G3usOmUTx8ej9XBX1n0yn/AL+j9Wu6reWt3J/66ufymXz/AGn12v2ruz+oNhlXWzTYn/r6P1fP99um/wBdR+rXlOv67Pzu/wAT+Xtdj53f4g2GT1t02P8AvqP1fI63adM/9dR+rXlVuLW6feq7H6vx/tPq9Pveqj98g2LW+tGnVTEfGo/V7em9U9LyeOb9H6taNO7tYpnxfq/V3cbqBr9j9i/P+KQbRtM3lo+XxH2miJn8XtWtTwLsc0Zdqf8AxNWWJ1Z3TjTE0X5/xyyDSuvG58eqIvXrnEfSoGzKMrGn2v25/wDE/cXrU+1yn9Wv/RfUNn8UxdyK/wB8swwPUHM2o7smefzBdGLlE+1cT+9+lT9v9fsaq9HxcmOPxlJm3+ue3simmi9k25mf7QJkGFYnUvbeTTE0ZNPn+1D1MTeGjZMxFvIp8/iDIRw4uTZyaO+zXFUfhLmAAAAAAB8ueKKvyVW9WV+Y0y/TE+8StRe82qo/BX3rxs3N3DRXbs25qifpAKG7Q0DM13cNGNatVVRNzz4/FeHpV0Uxqdv2b+TYp7qo88x+B0C6MY2lZM5moY0RVHnzCwmXVa0nSKotUxTTbp8QDX36sNrYu3dSqtWKaaeKuPEK+LE+r7V6tS1uvmefvq7AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAkHobqM4m8cWjniJrj/ADbPNj5FORtnDriefuQ1VdMKpp3fiTE8fej/ADbOuj92u5tax3Vc8UQDNgfKqqaY5qmIj8QfR5+brGDi0zVcvU+PxY1qfUnb2B3fFyKOY/tAzSqqmmOapiI/FxVZWNT+1foj96vm/OvOl2Zrt4uTRHHtxKGtz9f8qIr+Bkz+6QXU1jculadYqruZVuZiPbuYBqnV7TMWuqIv0ePxUQ3H1m3LqV2uKL9cUTPzqYpk701zJqma79UzP4yC9OueoXCwu7tvU+PxRvu71QRcpqtWb3P5SqpT/LOr19v/ABaufze3pXTTceo8TZx6uJ/syDN9xdbtS1C5VNq9X5/FiGpdQtx5cTFu7d8/jLPNpdANav1UXMuzcnn+ylbQPT7RFun42P5/GAVKy9U3Dm1TNy9lVc/KOX4xdO1/Nq4t2cqrn5zyvjoPQHTIqp+LjU/vhn2j9Ftr4VEd1iiavwpBr10bp9uLNmO61djn8JZFR0f1q5RzXar/AElsJw+nW3cXj4eNHj+zD0KdoaNTHEY8fpANemndD9SyLkU1WK/P4SyfA9OGXfiJm1X/ABXot7d0fF+/8Kinj68P39s0fEjjm1HH5Aplp3pav3pjutV/xZBhelGiIiarFPP4wtBm700TDie+9TER9Jh4+R1V21ZmYqve39qAQdj+lyxb45s0PQs+mnFojzZo/RK1XWTa0Vdvxf8A5nt6F1A0TV7kUY1fMz7eQQnHpvxP6mj9H3/o24n9TR+ixOp6ti4GJOTemezjlgGqdaNtYF+bVyqOYnj9oEXZHpnxq4nizQ83J9Ldm5E8WaEv4/W/al2P+s4/8TtUdZNrVf8Ae/8AzAgDK9KVM8zTYif3Mf1P0u37NU9tmqPyhaqx1W21e/Zvf/M9DH39oOTxFN2mefrMApNn+nLMsUzMWa/4sbz+h2o2K5imxc8fm2F0anombT72vP5Pv8h6Jmfeim1VM/TgGuavo3q1FMzTZr/SWP6x063DhzPZauePwls4q2jo0xxOPH6Q6GX0829k89+PH6QDVhm6Nr+DV/xLGTH408uC1ka3j1xVRcy6Jj82zvVejW1s2iY+BTE/jSwXXugGkzNXwceiY/CAUe0re25cLiJvXpiPnPLLdF6xavg10zevVxxP1WC1b0/Y80T8PH/gjLePp71CJqqxLFcTHtxAPX2p6mbmFVRRdu1fvlLu2fURhajaomu/TzP4qe6x0f3Rp81TVYqmmPrSx6vSde0SuY7btPHyjkGyLQOrGl59ymmq/R5/FIGBrOnZlmm7ayrUxPy7mqLF3ruDTrkdt2umY+syzXbXXTceBVRbv3a5oifeKgbNKL9mv9i5TV+UuRTHYfqCiqqj7Xkce3PMp72p1l27qliimvIt/EmP6QJTHkabuLTc+mKrN+mefxetTVTVHNMxMfgD7Mcxw4a8XHrnmu1TV+cOYB+LVq3aji3RTTH4QxrqTlfZdv3a+ePuyyhHvXXKjE2lduTVx92QUA9QOf8Aa9cueefvyihl3U3UIzdcvTTVzxXLEQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAZBsC/Tjbmxrtc8RFUL/9L+omm4O27VFV+iJiiPm1yY96uxdi5RPFUMixt661j2fhW79UU+3uC9m7uv8AiaXcqptXqZ4/FGm5/U/VVT227s/ulUvUtbz8+qar96qefxefboru18RzVILDa36hczNtVU03a/P4ow3L1G1bU66poyLkc/i8XRtp6tqldNOPZqnn8Gd6D0N3NnzTVXYuRRP0pBGF3L1DMuTVVdvXKp+ky9DRtta1q1+mi1i3ppmfMzErRdNvT1fpvW/ttjx8+YT3ofTTa+3bNHx7NnviPpAKfbP6EZ2q0UTesVxM/XlK+1/S7bmmLl2zH74Wo27pmkU24qxLVviPbiGQUxFMcUxEQCu+gennBwLlNVVmjx+CUtsdPdJ0qimKse3Mx+EMyysizjWpu3q4ppj6yxDWepGgaZNUXr9PMf2gZXZwMKzTFNvGtUxH9lyVTjWo8/Co/SEMa9172/jxVRZyLcT+aK96eoC1Nuv7Lkxz8uJBbHL1fTMS3Nd3Ls0xHy7mB7q6q6RpUVfDybfj8YUW3b1y3Bm367eLfr7effu8MTsbi3RufJ+z03LtyavftmQW93H6mcTT7lVNGRR4+ksRzfVnc5mm1dn9yDcTozu/Vafj/Duz3efvUzL3dI9O+5L1cfaabkR9Ip4BmurepfVtQpmLFdyefox271k3VmXPuU35ifzSHsX06UUV2/tdmfx5hN+3ug+1sTGp+0WKZr49opBU+Nz7s1iIjtv/AHvzexo+0d06txVVF/735rbx002lpNib82qKKafnVEPNu7p2ZoFU0d9iO38gQDp/RzcN+umuubsfqljpr071HRr9u5emvxMe71tQ637Tx6ZptXbHMfjD2dk9TdJ3Bk027F23PdPjiQZPuPRa9T0acWJmJmnhAu6ehGXn5Vd2m9VHM8+6dt/7ht6DpE5s1xERTz7oA1L1C2LWXXa+NT92ePcHj/7g9VsR9y7cnj8XUy+jOvWImaZuzx+Ms00X1BaZXMfHvW/3yyfF657YvTEXLtnz+MAgnN2HubTuZpi94/N4uTXurTK+e2/4/Naa11F2ZqkRTVcsTNX5PWwNv7S3FR3W4s1c/KOAU2yepm69M8dt/wAfm7el+oPX8CYm/N2OPqtrqnRnaubbmJsUxM/WlFe//Tzp1y3X9jsR5jxxAMFxPVjk2aIpuXKplkeieqazlzEXL1MTP1Q7uP07a1av1zh27kRz7drD87oxurDuTHwbnj5xSC6e2+vGm6hVTFzJtxz+MJJ0LfugalRTzm2qap+stcmJsbeWBP8Aw6ciOPze3p9vf+n3KZp+08R+YNkFvUNMvRzRk49fP4w5Ph4N7+ZYr/dClOyN0bts10U5dV7iPrynjYO5M7Jqt03pq/HkEka3tXSdSs1U1YtqJn6UwjLcXRHTtRuV1U2Lfn8Ey4NybmPTVPvw5wVV3B6YsXLiaqLNP6I33L6aL2BFdVmzPj24hfJ1NTpxvgTORRTMfjANWW9One4NAvz8LHvTRH9HmGP6TrGv6JlU1015NEUz5pq5bONT2xtfW6qrd61ZmqfwhEXUnoJp+ZbuXNPsU+fbtgFfNrdeNQ0u3RTdu1zNP4ykrbvqirpmi3Xdn98oy3T6fNwY127dxLdcURzPHaiPcu3s7QMqbGXHFVM8A2B7H674esXKKb16mOfrKZtH3BpmpY9F2zlW+Zj25alNI3DqWmVROPfqjj8UmbC6067p2XbtZORX2c+/cDZnTcoqjmmqJj68oJ9Ve4bWPta9jUVx3RTPzYbtz1AYcaVEXsmO/t+coW659TaNx03bdq93U1c/MEB6neqyM+9dqnnmuXWfa55rmfrL4AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAPc0TbOparb+JYtVdv14fjWdv5elxM3444/AHjMm2Dh2MzVbdu9McTVHuxmImfZ28HKysC7F2zFVNUeYmYBsB6H7G2rRplnLyarHdERPnhN1m5tnTseKbU4lNMR8oiZlrI0Xq5unTLMWbWRPZHyiplOhdatcys61ayci5ETMRPMgu/ufedGHXV/J9nmI9pphiManqu4c2mbkXKKOXH0v1/Q9R0Gm/qV2iapp5nulxb36j7X0LFrjDu2oriPlIJY27lafo2mx9pyaYq4881MW3n1a0zSrdybORRPb9JUz6kdbdWysu5awMmvtmfHEot1Pe2uahFUX8mqefxBZPqN6kb1y7cxbF2ePMeJQPu3qVqur3a6qb9yO78WBXbld25NddU1VT7zLm07Cv52TTYx6Jqqn6QD9Xs/PybnNeRerqn5RVLvaboWu6nciixi5FcT85iUwdHeiWq6vn2srLsVTb5j3jwtpoHTrau1dLpu59qzFdNPnmIBVPpn0Ez9d+HVnY1cRV7zMLH9POiG2NpVU3c34MTTETPdw7e4+r20ttYNyxgfBi5RExHHCsfU3r9r+ralXj6ZcrimZ4jiQXQ1LdGz9Ex/s2LGPVXTHHEcOLbm68bUMqIoxI7Jn37VVujO3d47q1S1nahN/4FcxP3ueFo8u9oOydBicq5bi9TR55n5gkWNQ02xYi7Xds2o45nniEddQ+rWmaDZrnGyKKppj5TCrHWjrhl1ZV2xpOTV28zEdsoI1re2uarFUZOTVMT+ILCdSvUjl583cSxfq48x4lAe5N96xqt+qqMq5EVf2pYlVVNVU1VTzM+8vgO1XqGdXVNVWXemZ/tylLoTvXK0bWbPx8iqaIqj3qRG5sXJvY1yK7NU0zALy9VuoeNrWy5s28inv+H/S/BSfX7uRGqXpm7XxNUzHEuxc3Pq1dn4VV+Zp/N5F+9XermqueZB+qcnIp9r9yP/FL9052bTPMZV6P/HLrAPb03c+r4V2mqnMuzEf2ku9N+uOo6Nk2bd7Ir45j3lA5E8TzANn/AEj6rYG5sW3TkZFEVzHvMpTt38bIj7ly3cj8+WpzZ2/NX27foqsX64oiflKzHRzrvXeybGPm35mqqY95BcurExqv2se1P50w6Gbt/Ssrn4mJa5n6UuLbGvY+s4Vu9amJ7qeXtAxmvZWi1T/2a3/hdbI2DotymYjFtf4WXgI8vdNtP7+bdmiP3O/o+zLeBciqmmI4ZoA4sa18K1FH0coAPL3Hj3MjCqpt888fJ6jr52XYxLFVy/cppiI+cghTWKNX0rNm9HxOyJ5eroHUvAs0xj6hcpiqPE90uPqd1E2zhYF63du2vicTHvCkPVHqBkXdduTpd+YomqZ5ifALzbw6l7WsaRemPgTVVRMfJQbrbuDE1rcV6vF7e2apnwx7L3jrudRFm5kVVRV44iZdi1srW8zEnP8AhVzTVHMzMAxWinurin6vSp0PPuWou2LFdcfhDr38S9g5lNF+mYmmrzysv6bdM0XcV63h5UW5qniPIKz3rWoY0cXaci1H48w61Vyur9quqfzlfDrL0Dxbum1XNMsUzM08/dhUHevT/Vtu5F2Ltmvspn6AwsfZiYniY4l8AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAH2mmaqoppiZmfaGb7O6ba3uOuj7PZr7ap+VIMS0zTsrUMimzj2qqpqnjmIWA6TdBMvcUWq8ixVxVxPmErdD+gVOJVau6nYiJjiZ7oWJyb+gbF0yJ/4duKafwiQRLf6S6Nsvat69eotxXRT49vopj1b1rHydcycSxEdtM8Rwn/ANQ/XGNQpv4GDeiLc8xxTKqNq3k6zrE1UxNdVy55/UEl9GumN/dWXarm1VVRM/RZCr0wYd/T6Zm3T3TTzxwzL0pbUx8DalvJvWYi52x7wngFAepHp5u6Nj3LuNYmZpjmOIV/1TQNX0fNmm5jXaZoq8VcNuGqaVhalam3lWqa4n8EU9SejWiaph3LmLi0d8xPyBQfTeomqafg/ZKbtyniOOIljOtbg1LU71Vd/JuTEz7dycd9+nvWrefdu4NmuKeflHhgWqdH9yYFM1XLVfj+yCNqpmqeZmZn8Xe0fDjMvxbmPeXY1nb+oaXVMX7NXEe/h1tIzZwsqm5xzHIJu6Y9H7G4rluLlMcVfVYXZ3px0nSr9rIroonjifkgbpt1Xs6HatzzFNVMJNj1H824p+NEcR9QWax8HStrbfmnGs24qpp+UeVbOtO6de1S7exsKm7FM8xHby46eveHnU/CyL8TTPv5djG6h7OyJ+JfqtTVP1mAQRpfTbdGv6r8TIm/2V1eeeU19PvTfhxkWM7UaKeaPM9z3sfqjs/AjusTZiY9vZ4O7fULZx7FVvAu0xHHEcSCe9a1PbXTvZfGPFim5bo8ccc8wo31v6vZ25dSv2ca/VFvumI4nw8vqN1e1bcdu5jTdrmiefmimqqa65qnzMzzIP1eu3Ltc13K5qqn3mZfh6ukaDn6lVEWbNXE+3hmOl9JNxZ9MTbtV+f7II5djGwcvJqiLNiuvn6QmrQfT/uC9fom/ZuTH5J46YdBbWPVbnOxo8THPMArJ0+6Waprl6j42PXFNX9lPu2fTFaysaiu7ajmY+cLQ7e2JoekWbdNnGoiqmPeIZTat0WqIoopimmPlAKfa36W8ezYqrt2Y5iPlCEeo3RrUNDmv7PYrnt+lLZhVEVRMVRzEvB1raWj6rTVGTjUz3fgDUpmaTqGJXVTfxblPH4OlMTE8THEtifUzobpuXZuV4OLTzVzxxCuG7fT3rdGTXXi2K4j8IBXoShndF9y4kTNdq54/ssS1nZ+q6ZMxds1+PwB4uDYi/dihJ3S3aWRka1j3qZmKYqhGFmu7h5EVVUTExPtMJU6fb7s6XTRVXTETAL+dIsa3gaNaouXI7ooiPMs+puUVe1UT+9SPSOvNGLRTRF7iI/Fl2l+oXDiiPiZEfqC2HMfWDmPrCsVPqH03/maf1fKvUPp3H/aaf1BZyq5bp966Y/e4q8zFo/av0R+9VPVPUJiVUz8PJj9WFa91/u1U1fCyZ/dILtXNX023EzXmWo4/tMQ3f1H0fSLVXw8q3VNP9pQvcXXPXr9dVONkXPP4sF1bqFuTUqqvjZdXFXy5kFw92epCzp1dcW78ePpKKd2epjM1Ki5atV3OJjjmOUDado+tbivRVFNyqZn3mJZhpfRXdGdFNVFqrif7AMX3buzV9zZ01fGvVUzP7NMz5d/YuwdX3Dn0U3Ma7FuqfnEp36U+nzMtZdFzUceqrz57oWt2b000HRMO1xi0fFiIn2BW/YXpmtXps5d+zHjifMJ1sdI9MxtAqxIs0TVFHHtCVLFm3YtxbtUxTTHyh+wa1PUts2nbeq3KqKO2Jr+jDuj28r+1dft34uzFPdE+60XrP2vd1CLl+za548+IUoy8e5iZE2q+YqpkGzPpT1X0fc2BbtZt+33TTEfel39/wDTPQt42Kqsai1PfHvHHDW1tzeet6HcoqxMmqKaZ545Wn6IeoiMezas6tc5jiIqiqQdPqX6aJ07GvZeLZ7vEzHbHKru59talouo3ce/jXIppmeJ4bONA6k7a3VbjFouW5puRxMTVyxXq10b0fXdOu5eDYom5XHPiAa1J8TxImPqL0X1rRcm7et2K4txMz+yiPNxruJkVWL1M01Uzx5gHAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA58XEycqrtsWqrk/hDl0bAvalqFrEsUTVVXVEeFwugXQqLlmxmajj/cr4n70AiHot0gzteyrN7Lx6oiaonzSvB0w6aabtrDtTVYomuKY8TDKNtbR0jQrFFGJj0xNMe/Dua9reFo1j4mTcpp/CZBgXV7fEbSxblWNR2TRHy8KW9Vut+q6/dv40Xq4jmY91nOrebp26sW9TbuUzzE+0qWdT9sW9Kzbt63PjmZBhOXlZGZfm5erqrqqn6pm9M2ybmv6/RXetTNEVx7wifaeHOfruPixHPfVxw2Eem3YdrRtOs5vwYiZiJ9gS9s3Q7ehaVbxbccRFMPcH4v3aLNqq7XMRTTHMg/ZMRMcSjbX+rugaRqE4l67R3RPHuyLbW9dJ1uimqxeoju9vIMhu4uNdiYuWLdXP1peBruz9K1K3MTj0RM/gySmYqiJpmJifnD6CCN39BNN1amuaLdHM/ghzdHpqjEmuuzZ/SF2nFfx7V+ntu0RVANbm4ei2r4c1fAt3PH0hhef023LYqnts3Zj8pbRMnbWk5HPxMamefwefd2Jt25+1h0z+6Aas7+z9y2apicK/PH05ccbb3NHiMPK/WW0a7012tc98Gn9IcX+6/avPP2Kn/DANY+PtDdd+YiMXIjn6zL3NM6Ybkyqo+Nbu8T+Etkdrpxte3+zg0/pDt2dkbftfsYdMfugFCNA6GZ2XNPxrFXn6wkrbPpmt5E0V3LP8Fvcbb+l4/Hw8emOPwelZs27NPbboimPwBBO1egOm6VFE1W6OY/CEn6DsnSdNtxT9nomY/BlQDrWcDDtRxbxrcf8Ahc9NFFP7NMR+UP06Gr6tiaZZm5kXaaePlyDvurkajhY88Xsiij85RVvXrXoekY9yii9RFccx7qxdTuvGVk5FycHKq4mfHEgvdZ1bTr1XFvLt1T+Eu7TMVRzTPMS1x7J65arYz6Jy8qvt588ys/0969aNl4Nu1lXqKq+I96gT7VEVRxMRP5uK5iY1yOK7Fur86Xgbd3lpWsxE2L1Ec/iySmYqjmJiYn5wDxNT2vpWbbmmcaiJn8Ed7o6LaXq1VUxatxz+CYAFUN3embEqtVXLNumZ/CETbk6B52F3/At1+PbiGweqIqiYmOYl0MrR8DJ5+LYpnn8Aaw9Z6V7hxqp+Hau+PwljOZs7cuLVMVYd+qI+cctqF/Zeg3v28Smf3Q8/J6bbXv8APdhU/pANWU6BuCJ4nCyf4n8gbg/5LJ/i2gVdJ9qzPP2Oj/DB/un2r/ydH+GAawre2tw3J4jByP38vY0zp5uPLmJqxrlMT+EtlFnpVtW3Vz9jo/ww9HH6f7bsREUYVPj8IBr9230V1LNrpi9Yr8/WlLG0PTTbya7dy9Y/WFu8XbGkY0xNrGpjj8HrWLFqxT22qIpj8AQvtDoZpejdkzatzx+EJS0fbWl6fappoxrczH4PaAcduxZt/sWqKfyhyPxeu0Wbc13Koppj5ywbdPU7QtBqmm/domYn+kDPBh+yN/aTumeMO5RM/hLMAYH1b2zZ1nQciuqiKqoolrU6saRe0zd2Vam3MURVPHj8W2HOsxkYl2zVHMV0zClvqn6fW8WrI1KizETPM88Arz052xi69eotXeO6qeI5Ztvjo3q+l6V9u063c7YjmO2EabH1y/ou4ceqKuKKbnE/q2H9IdV2/vLadnDyvhV3Jo4mJ4+gKC7M3nrm0NUpjIu3qIpq9qpnwup0L62YOtYtjG1DIp444+9KLvUx0Jrs3b2p6RZ4opmavuwq/i6prO2c+vHsXa7Ny1VxMT4BtT13StB3Xp1Vqn4F2qunxNPCpfqE6C1YNu7qGBY555mO2HidDOuuZhX7NnUsmZnmI+9K4m2tf0TfWk00XPh3O6nmaefcGqzU9E1LT71dvIxblPbPHMw81sW62dF9KzNNv5enY1MVdszxEeYUP39tnL0DWL1q5ZqpoiqfkDGQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAHvbN2/k6/qVGPZomqJnjxD5tbbWbruVRax7dU908eIXM9NvRX+TLljO1CxER+15gHB0M9P1i18HVc2zETTxP3oWI1jceh7R0y1ifFtUVUU9sUxPDodQd7aTsrR7lmiu3RXTRPbHPsoX1n6tapre4KvsmVV8Omv5SC+l7etN3RbmfYr5pppmeYlULrp1vzczVLunYt+rupq44iUndD9Uq1/pxXj3bvddrtce/wCCMbPQbUtX3vezr1qubE3O7zHgGC6NvTcdvFqvXabs0THPMsK3pum7rFVdu5E93tKynUrRdubR29Vh3aLcXoo4VQy8eM7XrtvGjmmu544BIfQLaeRqm58XK+HVNMVR8mynZ2FTgbfxbFNPExRHKvnpR2NZs6PZzL1mO6KYnmYWZt0U0URRTHERHEA/SL+vG8f9mdEu/f7Zqo+qTb92mzam5XPEQp961d0W7+HXj493zEccRIKx9RN5ajqu47uTbyaopiuZjiWQ7K6yaroNFFPxLk9v4ox0/Evajm02LfNVdcpJ0roruHP0/wC2UU3Io45/ZBM+2vU3k1WqLdy9MTHieZShs7rzj6hVTF/Ip8/WpQzc2iZW39RnEyJmK4/c4MLWtRxJj4GRVTx+INqmgdQtD1C3TFWXbpqmP6TI7OtaZdiJt5durn8WqXTeoW5cK7TVRm1zEfLmUkbW636pYiiMjKr8e/NQNjlvKsXP2LtM/vcsVUz7TClu3/UFbtU0/Gyv/mZXi+ozT4pjuyaf8QLUCstr1IaXEecmn/E5J9SOk8f9pp/xAss/NVdEe9UR+9WO96j9MnnjJp/xPNzfUVg1UT25Mf4gWouZuLb/AG71Efvda7rel2oma8y1HH4qW7g6/VXe74WTP+JG+5ut2sXoqpx8muefpUC++u9Q9CwLdUU5duqqP7UIn3j14x9PrqixkU+PpUpBqHULcmZcqquZlcRPy5l4mbreo5kzN/Iqq5/EFtdS9TmRamqKb8/qj7eXqG1LVaKrdF2vz9JV6qqqqnmqZn83wHv7l3RqOs5FVd2/X2zPPHLzMPAzc6uIsWq7ky6aefTXo+HquqWLeVapqiZj3gEO5e3dYxLXxb2Jcpp+vDr6fqmdp96KrN65T2z7cr7ddOn2i4GyKsnHx7cV/D58R+ChGuW4tapfoiOIiqQSds3rNquhxT/xbk8filfQfU/mRZpouXqomPrKpgC9m2fUTGZfppu34iJ/tJm2j1Q0bU7NHxsq3E1fPuatsXOysarutXaqZ/N7mn743BhTT8HMriI+XMg2wWNf0i/ETbzbVXP4u3bzcW5+xeon97WbtbrPruLcppyMm5xH9pK23Ov9Vumn42TP75BeOKqZ9qol9VTwfUZgU0x3ZMf4nqWPUhpUR5yaf8QLMCuFPqS0f55FH6v1PqS0bj/tFH6gsaTMR7zEK2XPUjpPE8ZFP+J5uZ6jdOq57cmn/EC0FzJsW/27tMfvdLJ13SsemaruZbjj8VQNyeoO3dt1RZyfP4VIj3h1s1jKmunFya/P9oF7dz9TNF0+zV8LKtzMf2kO7r9QlvBuVxZyI4j6VKYZ++tx5kzN3Nq4n5cy8a9m5+oXe2u5Xcqq+UAszun1O5t+iqzZu11fLxKFN8dR9V3Fdmqb1ynmfq4tv9N9b1exF21ariJjn9l09x7I1TROftNFURH1gEr+mjqHkaNqlu1k5EzE1RHmV/to6rTrGj2sumee6IakdvZd7C1jGrt1TTxdjnj82y704av9s2bj266uau2ASwiP1L6NRn7Pu1xRzV2ylxivU7DjN25dtTTz92QapdyYVzTtYvW5iaeK5mP1SD0k6pahtfUbMTfqi3Ex834686TTp+t3aoo7eav9UWx4nmAbP+mW/NE39tucTNrt1XblHHFU8/JAXqS6ERjze1nTMfmK4mqJphX7pZ1D1Pa+rWZpyKotRVHzX16V9Q9E6haBZ07NuWq7k08TzP4A1q5WPmaVmzbu012rtur8vZL3SPrJqO3MqzZuX64piYjzKT/VL0Vqxsm5qelWv+HzNXNMKnZuNewsuuxdiaa6JBs86UdTNL3hpnwcu/RNy5TxxM+7FusHRHB3DjXs3Gs0TNUTV4hR7prv/VNtavYrpyKotRVHPlfPo/1o0jcmmY+HkXqJv8RTMzIKH9VdiZm1dUrtfBqiiJ49mBNlfWvpdg7q0+vLxLVFdVdMzExCi3U/pzqW1s6/VctVRaiZ48Aj0AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAB2dPwcnOv02ca1VXVM8eIcWPaqv3qLVEc1VTxC0fpd6VfyhmWcvPx+6iZifvQDMvSf0s/9Gt5mo4/ExHd96Fjt/wCoVbb23FWBb4minx2w9vT8HTNu6bFFmmizbpp4n5csM3lurRtQtThXLtEx7ccgpL1w3Ju3ceq3bdum/NE1T7cogvbe1qmZquYV7mfnML+07e2jduTfu02ZmfPnhyU7D21rNcWsW1Zqn8IgEA+mXO1rEzLODct3ItzMRxMLsXcnD0ba05l23TTXNvmfHnljex+mGkaFf+1VWaKZp8+zEfUhvTF0zQruHj34jimY4iQVI9Tm6bmsbmuW7V2fh98+IlinRvRK9X3BbiKJq4rhju68+rVdZru8zVM1SsR6O9qTd1ii/ft8xNXPmAW86OaV/Je2rdqaO2e2GcuLFx7eNapt26YiIjhyVzxRVP0gGMdSdYtaVtfKvTciKopnjy1tda933tc17IszXNVMVTHutH6ot9VYWPkYNN6Y5iY45Ubzr1WZqVy7PMzcuAznoXodzVd6Y1M25miKo+X4tkGl7ewdM2J2Rj09/wBn59vnwrB6X9k27OPY1e9binjirmYWczt4aZGH9hm9Rz29s+Qa/vUBoOq5O9r13Hw66rc1TxxDCNG2Rrufk02/sVymmZ4nw2DZm3NsardnIvRZqqn68O3pe2NoYNcV9tiJj8IBSjUOjeqWdGnJpxa+/t5/ZRxkbV12zdqt1afdntnjmIbPMmvat/F+y82OOOPkxTL2btG9dq4osc1fhANc06Fq8Tx9ivRP5Oa3tnX7kc0adfn9zYhi9HtEy7kXrePbmiZ+jILfTjaelWo+1WbFM8fOIBrSna24o99NyI/c/M7Z3BH/ALuyP0bJ721tkV/s0Y/6Q6tez9mT7UY/6QDXDO2tf/8Ah+R+h/s1r0/+77/6NjFezdnT7UWP0h+Y2Zs/+hY/gDXR/sxrs/8Au69+jJ9s9Mdf1S1N2vEuUxx4jhfGnZmz+Y5osfwezpeLs/SqfhxFiPl8ga6tf6c7h067MU4Vyqn8ni/7K69/8Ou/o2a5+mbL1K1zNOPMz+Txp2ds2efuY/6QDXFO2Ncj30+7+j8TtzWYnj7Dd/RsTy9m7QpnxRY/g/WndNtt512JtWbNUfhEAoBoWxNd1HIopnDuU0zP0Wq9O/TbK0vKsXr1qqnjifMLGaN0227gU01RiW5qj6UwyfB0jBwuPs9imjj6QCPutej3s7Z9eNapmqfh8eGu7fuzdZwdcvzGJcqpqqn5NrWXiWMq38O9RFVP0YNuTpfoWqXJu/ZrfdP1pBq6nQdWj3wrn6OOrR9Sp98S5H7myHI6LaRMTxi2/wDC8bP6H6fVM9mLR/hBr2jSNRn2xbn6Oxjbd1i/XFFvCuTM/gvxj9DMKK4mrGp/wso290a0bHu013cW3xH9kGvqz063Hctd8Ylz/C6OZsnceLM9+n3Jj8IbTLGwtt2rUURg254j6Q6uf022zlUTH2K3TP8AdgGq+rQdaonicG/H7nz+RNZ/5O/+jZTqHRrRLlyZoxbfH911Kei2jx/7Lb/wg1w/yJrP/J3z+RNZ/wCTvtj/APuX0f8A5W3/AIT/AHL6P/ytv/CDXB/Ims/8nfI0PWZ/9ivz+5sf/wBy+j/8rb/wvtnovo8Vxzi2/wDCDXPj7V1/InijT7s/nD2tN6bbiyaqe/EuUxP9lsh0rpVtzFiJrxLcz/de3Z2Nty1x2YNEcfhAKDbR6IZmdcojIxqvP1pTjsf014NMW79+xTHz8wszibe0rFmJs41FPH4PUoopopimmIiI+QI+2v0x0nR8aLUWaJ4jj2RR6m9g4lnQr2Xj2Yj7szzELMo86849ORsq/TMRP3ZBq4oomxrcUVRx2X+P4tgPpc1O1Oi49rvjntj5qIb3xvse5sjiOI+JzH6rC+lHdldWqWcKbk+KojjkF84nmIn6urqmLTmYldmr2mJc2JV3Y1qr60xLkBr59YOhVYOq1V0UTx3K2r6ernblOfau3otxPHM+yiuq4842dcszHHEg/WHpedmRzjY9dz8oZ90x1Ldu0dZtX7NrIt2u6Puzyk3006Po+fatTn02554/aWN1TZmzpx6a6aLHMRz8ger06zbvUDZ/wtTtd1c2/M1fkqt6jukORpOq5GXh488TMzHELU7I3LoW3Z+w2rlummfHHLMtwaHpG8tMi5MW7kzT+YNVNO3tYqqmmnBuzMfSHv7Qp3ZtzUqMrFx8i1TE8zHyXznpfoGn5Ezfx7URz84cuTsvZ1y3ETRYifygHL6bNzapuDQ6KNToqmYt/wA5+PUR07xtf0S7dx8eJrmmeeI+b3dr6ht3a1qbWNXbpjjjxLMdM1rS9dtTZprorir+bz7g1Xb+2lm7e1O5ars1RTFU/Jik+J8thnqL6UYufp2RqOJj0zNNM1eIUE3HhXNP1jIxblE0TRXMcA84AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAHqbd0i9q+dRj2YmZmYjwDOOi+xsrcWs496mzVVRFUTPhsV6abewdsbax6rlNNurs8zMeyNfStsHH0fb1OTl2Ke/sj3h6nqL37a29oNePi3oorppmPEgjn1Odaq9Gy68DT7/PE9sRTKs2T1Z1e9kzdm5c8zz7sU3zuHI3Dq9zKv3Kq/vTMcyx4Eo/72tXuUxaouXOZ8R5WK9Kmr67qmfRezKbs25nxMqi9PNNnVN1YeNNua6Zrjnw2V9MtB0baexcfNm1Rbri33TPHHyB1OuXUbG2rpFdFN2Kbs0z81COp3UnP3Jn36KrlVVE1Tx5Zl6r96X9Y3PXYx78zaiqfaUBzPM8yDv6Ha+0araonzzUvr6WtJoxsWzcijieIUZ2Fjzk7mxbURzzVDZN0J0GrA2/jXqqeOaYBKjgz7lNrDu11TxxRLnY/vvK+zaHeqieJ7ZBQD1aavcvbsuWaK/uzXPzRx0y25VruqWqezuiK4e76iMicjeNdUzz96pl/pSwreXqXFVMTPeCwNjPx9l9PuyOKK6bX+irut9X9UnWsiu3cuTRFyeOJWz6q7Fz9V0GbWPTV2zR8vyVF3N0e1rAv3rnw7nb3TPHAO3Y63avbp477v6l7rdrFccfEu/rKKtU07K07KqsZFqqmYn3mHTBLFvrLrNNzu+Ld/V6Om9bNXuahYom5c4qriPdCzNOle2LuvbhxYimaqIuRPH7wbIuiOuTqezbeXk1cT2xMzP5IM9UfVevR8q5Ywb08xPERTUlTHtTtDpbE080TFn/RQLq/uS7ru4sma65qim5PzBklHWzWaf+9u/q5I64ax/WXf1Q6AmP8A34ax/WXf1ff9+Or/ANZd/VDYCZP9+Oscf9Zd/WXQzOsWsX6u74t2P3oqASxi9ZtZsxx8W7+rtx1w1j+sufrKHAEw3etmsXZimLl3n81m/TJunUtesW7mR3zE8e6lfTfQo13XbWPNPdHdHhsV9P8As3H0HQrdXwopntj5Alq35op/J9I8QAAAAAcQAAAAAAAAAAAAAAw3q7YnI2reoiOfuyzJ5O68SMvSLtuY5+7INXnWTT6sXXbtU0zH35/zel6ctRqwd62vvcUzVEsq9Umkxg6lcqimI+//AKox6V5X2Tc9m5zx5gG1PaOdTnaLj3InmeyP8nsI06H6pObolqnu54ohJYIg9QGBTf0e9VNPP3Za5d/Wos7kyKIjjz/q2gdXtPnK27kVxHPFEtZnVazNjd+TRMcfen/MHLs/e+Zt6mIsVVRx9GXXOter10ds3Lvt9UQAJFu9UdVqy4vxdueJ591j/S31gy9X1KjTcq5VPnjiZUrZ70Y3PRtfcVGbXV2x3RPuDYl1su38XbtWdjRPijumY/JSHcPWbVcTV7+NTcuzFFUx7rs7Q17TupGwKrMV011zY49/moz196Y5u29yZuXTbqizVVNXt4B52Z1g1e/Vz8S5+rOukHW3OxtZt0ZV+qKe6PeVcp8S5LF25Yuxct1TTVHzgG2LaOs4m9drVU1TTXF2jz81TPVL0etaTcv6riWv2omrmmHq+kvqpbxbdrTsy9zMx28VSsz1D0CzvHbNPbRTXFyiePHINUV61cs1zRcpmmYnjy40k9eNsVbb3Ncx/h9tPfPyRsAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAD7ETM8R7pn9Me2L+p7vt13bNU2+6Pkibb+POXrGNjxHPfXENhfpr6e2NM0XG1OqzEVVxE88Al2qzj7d2pcqt8UfDs8/wAGv31Kb4v6prmRiUXpmnumOOVp/U11Hs7f0u9p9N2IqmmaeIlr43VqVWq61fy5qme+rkHlPtMTVVFMe8vjJOn+iV6zrdmzTT3R3QCxfpO6Y2tSy7GqZFrxHFXMwmv1M7tsbX2ZOBh3opqpt8cRL3ekOif7LbB+0dkUVRa59vwU99TO88jVdfyMKq7NVMVTHHIId3Bq1/V82vIvVTMzMz5eYAJE6GaZOXu7GuTTzEVx/m2dbHsU4+2cOimOP+HDXp6bsWmrW8evj+dDYptqO3RMWPpRAPRRz1s1KMHQrv3uPuSkZBPqqzpxNBr4q4+4CiPV3NjN3NcriefvSmH0Z4ld3V4q7Z471fdev1ZusXKueea5hbr0W6RTZuWrlVPvxILh28e1XiUW67dMx2xHmPwY/rmy9L1KiqmuzR978GUR4jh+bk9tuqqPlEyCtXVfortu3gX82um1TcjmfZS3qNpOLpepXLOPx2xVMRwtX6oN66zgXMjHx5rmjzHhTXWtSy9SyqruTM90z7SDz1n/AEaaHb1DU7N2ujuiK4+SsuLZqyL1Nqn3mV4fRtoVemaR9ruUccU888Aln1F5uLp/T+7jU1U01RRMRH7mszW65uatlVzPPNyVsfVtv659su6bTdnjzHHKot6v4l6uuf508g/AAAAAABHmeB2dLszf1CzaiOe6qIBMnpV0W7mbxoruWp7O+Pk2P6PiW8PAtWqI44phWX0t7GpxcexnzaiJmInnhaWmOKYj6QD6AAAAAAAAAAAADiyr1OPYqu1+1MA5RCHU/rLg7byJom9THE/V++mnXHStyXKceq7R3TPHuCbBx41+3kWabtqqKqao5jhyAAAPxeoi5aqon2mH7AUU9a2B8DKrqinj7ytG0702dYtVxPHmFvvXBjU1U3KuPkptpFU06han+0DYt6XcycjSrcTPP3U7q5+kauatMt8/0VjAeTu6zTf2/l0VRz/w5axOv2H9n3tkVRHETXMfxbQ9fjnRsqP/AMuWtv1KY8U7myK+PPfIIZAAI8ewAs56Tuo1zRsqxpt+9zTVxTxMrBeonbtnc+zas/HsxVVVb55iFB+mmZcxN34NVNcxE3I+bZ3srGsaz05sWrvFfxLPHn8gasNw6Zf0vUrti7RNPFU8cvNTX6pNDtaPua5TboimJuT7IUBlPTPVrml7pxLkXJoo7458tm/SLcuDrO08Oi3fpquU0RExz7tUNq5XauRXRPFUe0rQ+kXfmXRrFrCyr9XZFURxMgz71Y9OZ1O/e1O1Z/Z5q5iFJ9Tx5xM67j1RxNFXDbJv/AxdZ2dlVRRTXNdvmmWsvq9oF/S91ZdU25pomufkDBgAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAH2I5niHxy40d12I458AkvodtavV9w4t7smqKa4n2bHNuVWtB2Papr4om1a/0VU9Ge3qc6bV+u3E8efZYzrrqEaNtCubdXbHZMeAUs9WG469W3FcppuzNPfPjlArKeo2rVanrt6qapniqWLAJk9L2BGduyimae7iuPkhyImZiI95Wc9GWgXP5d+13KJ7e7n2Bcnd02NK6d3aPFHFj/RrE6q5c5W9M6qauYiueF4fVLvunSNDnBoudszR28RKgOu5f23VL2TM899XIOiBALC+mi3E6njz/AGobBNvxxo+PH9iFAPTN/wCscf8AOGwDQf8A1Rj/ANyAd5Wn1nZM2tErpiePuf6LLKtetiZ/km7/AHQUVsVd+fFU/OvleD0fzE02o4+ijuF/2u3/AHl4fR/HEWv3AtTnXfg41Vz6I813qHhaXlTYyL1NPPjzKQdTtzdwrlEe8wo16sb+paRn13rFyumIn5AsVm7P291EsVXu61XVV5n2Va9TvSzC2ZbuXcWimI9/DzejPW7P27HZl5VXEfWp+OvXVa1vXBqtxciqZj6gifp7hfbtyWLHHPMx/m2O9GtAjS9iTX2dsza5/goN6fcSMvfmPTVET96P82yTJyLOi9P6avFMfZ+P4A15eqLIru79vUTVzEVSiJIvX7Npzt737tM8/elHQAAAAAADJum+HGZujFomOYiuGMsk6eahRp+4se9XPEd8A2e9G9LtYGz8SqmiImqiGboz6Jbsw9U2xi49Fyma4pj5pMAAAAAAAAAAABg+/d6Ubas13bk8RTHPkGcMV6n6tRpe1cm93xFXbPHlBGqepvTrd2uzF2iJpnj3Rp1R692te0u7i2b/ADFUTHiQQp1o3Rk61uS/R8aqaKap8csf2XujO25qNGRYu1xTExMxy8fU8icrOu35nnvq5dYGxL0zdTLm6MW1j37vdxER5lYaPMcqAejbPnH1Kima5iO76r84VyLuLbrieeaYBzAAAAqN62Y/4Vzn6KVad/6wt/3lyvW/lU00XKeflKm2k0zXqFqI/pAv76Qf/Vlv+6sgrl6Rbc06Zb5j+asaDo69/wCqMn/9OWuX1LzH+0OR/elsZ3BPGjZU/wD5ctbHqTyoq3RkURP8+QQ6AAADu6Hkzh6rYyYnjsqiWwD0wdQ6dX0e3ptV2Jminjjn8GvKPCf/AEgazdxN1/CruT2TXHzBlPrU0G/Vqk5lu3PHd3eyrFVM01TTVHEw2Y9fdnWNwbRnNpoiqYtd3PH4Ncu8cP7BuHJxeOOyoHjsv6X7guaDr1q9TX2x3QxB+rdU0VxVTPExINpXRfW/9qtn0Rdq7qfhxEq++sXZONpti5nWrcR3RzzEM+9GWsW69s0Ytyv71VERHMvU9Xuk3NS2xMW6eeLcg10ZNPZdmmPo4nr7twa9O1q5i1xxNNNM8fnDyAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAHsbPwp1DXLeNEczNFU/pDx2edBsSM3qNi48xzE2Ls/pRILp+j/AEWNO0SLlVHH3HL6vddt2NtVY1FccxRLNej+BGmbSi5TTx9xWX1YbhuX8q/jzXPETMe4Kq5dyb2TcuVTzNVUuJ9nzMy+A7mjWftGpWbX9Kpfr0rbdtYejRkzRET2c88KJbKoi5uXDon51w2O9IbP8mbHpvccf8L/AEBWL1q6hcnXYsU1z29/CsycfVdqEZu5Z+9zMVyg4AAFhfTRc41THj+1DYJt+edHx/7kNc3pwzaaNbx6Jq/nQ2LbZq7tDxavrRAPSVk9aePNei3Koj+Z/os2gL1ZYFWXoNzinn7gNdmJHbm0R9KuF4fR/TVxamfwUn1GzVhavXTXHHFcyur6O8u3cwaLlM8zTALTavqGPp2HXfyK4piIn3lRX1cbmwNWu3bWNXTVVzx4Sj6qOqN/R7NeDYqmmZjjxKleRqWbuLXqfj3aqou1+0yDxaMe9X5pt1STj3o97dX6LX9M+htetaJRl/B55iPkyS/6cLlXPFj/AOUFe/TXj3p6gY9XZVx3R8vxXp6v6nGH09iiau2fhf6MC6W9Da9uazRmV2eOJ59mc9cdCyc/bc4tmmZ4o48A109QsuMvcV+uJ5+9LHEidQtiapg6jevfArnzPyYBfx79iqabtqqiY+sA4gAAAAAH7s3KrV2m5TPE0zzD8ALI+lXqDlWNx28HIvTFEVRERMr/AOnZVrLxLd61VFUVUxPhqP2Nrk6DrFGZFXbMTHnlf/04dScXXdKt2b+RTNXbEeagTyPlFVNdMVUzExPzh9AAAAAAAAARN6hNuVajtTIu2ue7sn2Syx/qBRRXtnJi5Ecdsg1Pbq03K0/Wsq1firxcny8nmUq9ebWNa1u/8LtiZrn2RSAACVugm56NB1e3NdcUx3R818trdVNvzt+zXfyrffFMfzmryxfu2K4qtVzTMfR60bq1ymzFmjOuU0x9JBtJ2vvbD1/KmjEu01xz8pZnT7QpP6K9dzM3NpjLvVV8VceZXZj2gB+L9cW7Ndc/KH7eJvTOjB0S9dmePuyClvrU1Gb+VXTFXP3la9pWPj6zao458wlj1M65Gpapcoirniv/AFYD0lxJzN02rcRz5gF8fS/hTjaVbnjj7qdkddFtJnA0OzVNPH3YSKDxt55FOPt3LqmeObctYXXjLnJ3vkxzzEVTP8WxnrFn/Ztu36YnjmiWs/qnd+Nu7Jrmf50/5gxQAAABIfQ/U69N3Lbrpq45rhHj3tj5c4ut2auf50A2Z4Go/wAr9NK+/wC9M2P9GubrRhVYu98ye3iJrn/NfbpDl/bOnkxM8/8AB/0Uw9R9m3b3RkTERE98/wCYIgI9wBZv0sbvqwNQsYvxOPMRxyuHvnTaNf2dVdqp7pm3z/Bre6J6nXh7wxbfdxFVcNnO1ojL2RjxV577E/5A1oeo/To0vqnmYdNPbFOPZnj86IRwmf1n2Ix+vGpWojjjExp//rhDAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAACUvS3ai91fwaJjnnGv/AP8AiUWpX9KExHWbAmf+WyP/AKcg2J7TxeNnU26I8zQp/wCpbZOsZ+pX7mParqiZmfZc7ZlVMbdt1T+zFKMerO4dDw7lcZNNuZj35Br/AP8AYHcnPEYVX6S+/wCwG5P+Sq/SVrMTeO1a70xNFn+DvRunakzE9ln+AKy7B6c7kq3Tg114ddNFNyJmeJbDdvaPcxenVOPNMxd+Dx/BFmzN47Tuanbs0UWe/u8eyepy8erQPj2+Ph9nINf3XrYeu524Ll/HsV1xFc/JGdPTbc808/Y6o/dK53UDe+3cDProyqbU1RPz4Y5a6ibSqtxMUWf4Aqne6c7ltUTVViVcR/Zl4Gp6LnadMxk2pp4XF1Tf+1LmLVTTRZ54/BXzq3q+nZ9VycSKY559geR0U1SrC3hi0zVxTNcf5tnewc21l7Zw6qKomfhw1Q7Kvzj7ixrsTxxU2N+nzWpzNFx7VVfPFMAmNHPW3S6c/QrvNPP3JSM8TeWF9s0W/TxzMUSDVr1bwIwNzXLdNPHNUrB+h3VIm9OLdq8TVx5Q56j8SrF3nXTMcR3VO96b90xoGu24mvt5r+oLOeqvpfVrmFOdi0TVVNPPiFRdr9P9ftbrs26sSvst3PfiWyza+Vibq21arvRFyJpiPPn5Ovi9PtEs5k5EY1vu55/ZB0+iGFcwNn2bF232VxTHPhnrgw8WziWotWaYpphzgOpqOBZzbc0XaYmJdsBHuv8ASzRNWmqbtqjz+CEerfp/w6LFd3Ax4nmPHbC2Djv2LV+jsu0RXT9JBq13X0n3Bp2RXNnEuTRE/wBFitez9doq7asOuJ/KW1rVdpaJn2qqLmHbiZ+fajHefTfSsG1Xk0Y1HEef2Qa+rOxdw3Y5ow6p/dLk/wB3+5f+Sq/SVl9Y3ZoWg51WNfsW6e2ePMOfG6g7UvUx92z/AABWH/YDcn/JVfpJ/sBuT/kqv0laOd87V/oWf0g/252t/Qs/pAKuf7Abk/5Kr9JI6f7k/wCTq/SVo/8Abna39Cz+kH+3W1Y/mWf0gFXJ6f7l48YVX6SzPpdTu7a+tY9FFF2i3Nccx58Jxjfe1eP2LP8AB0sjeW1/ixepotd0eY9gWm6U6jk6htyzcyp5udkc8swVG0Xr5p+idtm3dpiiPHHKVNh9a9P3JeotW66Jqq8eJBMo8XVNex8HAjKu1RFMxz5cG3t04Orz22rlMz+EgyEI8xzAAAAAAx/qDYu5G18qiz+12y9HWtVxdLxKr+Rcpp4jniZQR1A6+aVgXrmD8SiY54nyCqHVzZe4M3cd6u3Yrrjvn5MLp6dbmq/9iq/SVov95G2c658e7TamavPydy3v3aUU/sWf4Aql/u53N/ydX6Sf7udzf8nV+krXf7e7T/oWP4H+3u0v6Fj9IBVH/dzub/k6v0l+aunm5onj7FV+krX/AO3u0v6Fj+DrX+oG06Zj7ln+API9Im1dX0fOpry7NVHNXPsuzZ/6qjn6Qh3oxrOi6xNM4NNv9yXr2TZscU11RAOZg3Wm9NjaF+uJ4+7LNrVyi5T3UTzCK/UhrGPh7Nv2puRFfbPjkGu/qlnTl7gvU93PFcsu9NGh3szelm5Vbns7o+SNdZvfbdxXa+eYqvcfxXF9K20KKbdjN+HHPieeAWu29h0YelWLVMRH3I5/R6D82ae21RT9IiH6meI5kEN+oXUqMXSL1FVcR92WujfV+MjcWRcpnmOVvfWNuqMKq5Yt3PeePdTSii5qepzEczVXUCR+mHTu9ujTPjWrNVc9vPiHnbv6W6/peZVFrEuTR/dW89HW2rONoUTk2Yn7nzhOet7L0PU6ebmJbir+6DVbOzNfieJwq/0l2MXYW4MieKMarn+7LY3q3S3SLeNcu041v7sc/soh1vK0XbWpVUZFi3TTTPzgFVrPSndFyOfstf8Ahe/tjo/uKNSs3LmNc4iqPksdb6o7Rs0cTRY8fk9bQOq208jKot0U2eZn8AZ30m2/laZsmcW9RMV/D44VS9RewNay9ev5OPYrqjumY8Ly7X1bCz9Ji/Yin4fbz4RR1X3Vt3EvXLeRFqao+vAKGRsHck+2FV+kk7B3JHvhVfpK02LvbavfPNFn+Dkvb02rPtRZ/gCvXTPYW4Kd4YV25i10U0VxMzw2V7Js1Y2zsK1cjiqmz5Vv2LvDbORrFu3bps9/dxHss9ptdF/QqK7X7NVqeOAa5/XDMT6g9Tmn2+x4v/0oQem31t0zT1/1OJ9/seL/APShCQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAACTPTNe+B1ZwrnPHGPf/wD8SjNm3RPLjC3/AI2RM8RFm7H60yDZZsXKm9s+mYnmexUX1Q6jlWM+/FNdURzKzPQrUKdU2xTbiefuIK9Xe3qrM37/AGePM8gqRb1zOormqLtX6uaNy6hEf9bV+rxao4qmPpL4DNdhbkzrW6sOqq9VxNyOfLYvsPNr1Xp/TTFXdV8L/RrD29dixrGPdn+bXy2GembXrOftyMaa4n7nHAKoepucrC3JXR31UxNcoeo1PMpp7YvVfqsJ60cD4O4Yu0UeO9W8HenVMyfe7V+rr3sm7e/bqmXCA7Wl3vgZ1u5zxxK7/pL3HbzJs4/xImY4jjlRaPCc/S1umrRtwUU3bkxT3/OQbJXHlURcxrlExzzTMPN2tq1vV9Nt5FueYmmPL1p8xwDX96tNp3qddvZtFqeIqmeeFddJzLun59u7RVNM01xy2Udf9kW9X0DJyabUVVRTPya6N66Nf0jW79q5RNNPfPHgF+fSvvnBzduWsG9kU/EmmI8z81gYmJiJj2lqr6P77zNs63Yn41UWoqj5thPSLqPgbp06xb+NT8SKIj3BJYRMTHMTzAAAAAA6esYFrUMOuxciJ5jh3AFNPUX0fy8mq/lafbqmrzPiFTtY0vWtvZE28qm5bmJ488tuebgYmZRNGRZpuRMcTzCvXXvohi69Zu5GBj0x4mfEAoH/ACrmf1tX6n8q5n9bV+rK+oHT3Uts5FdFdmuaaZ+jB6qaqZ4qiYB3f5VzP62r9T+Vcz+tq/V0QHe/lTM/rav1ff5WzOOPi1fq6ADlvZF67V3V3Kp/emb015WVG48ej4tU0zXHzQom701RTG4saZj+dALVeoHV8nS9gUX7NUxVFrnx+SEPTt1RyLuvfZcm/PM18REynH1C4lOX03mJjn/g/wCiinT7MuaVvu18OqYiL0x/EG1zRMmMvTLN+J57qeXdYb0i1Gc/aeLNXmYohmQAADzNwavj6Tg3L96uKe2OfL00H+o/VsrF0PIps1TH3J9gQv6hutVzm9h4ORzMc08UyqjresZmq5dWRkXapmZ5933c2ZkZmsZNd+uqqfiT4mXmA7dnUcu1T203auPzcsavmx/3tX6vPAd/+V83+tq/U/lbN/rav1dAB3/5Wzf62r9X4uall1+92r9XTAXM9EGdeyLf/ErmZhM/Vvd9zQ8umaq+2mPxQt6F7E02omY92Zerm1Vbwq7lvmJiPkCSenPULC1PSa6pv0TVFP1Vj9VPUa7kahe0+1e5pnmOIlFeyupeZoM3rFd6qI5mPdhe+tdubg1mrLqqmrkHm6PZuZmsWIppmqarsTP6tk/po0mMbaVi7XRxPZHyU/8ATX06vbj1K1kXLMzTFUTHhsG2To1OiaJaw6aeO2IgHuPH3dqH8naTcv8AdxxEvYRl6hNZt6Vs+7XNcRV2z8wUm9UWv1atrNdPxO7iv6o/6R6dGpbptWe3u+9H+bob51mrWNYu188/8SUrelza17J3TZyq6Jmmao+QLr9JNHp0HbFF6qjsiLcS/d7qlo2Nq/2G/kURPPHmWU6pjxjbRu2bccTTj8ePya5utet6tpO+bl23frimLk+O78QbJ8DOw9XwfiY9yK7dceeFavVptKjB0e7qVieO6JmJhhvRPr1c03S6bOVc7uKOPvS6fqC61Ym6NBnBorp57ZjiAVUv5+XVcrib1XvPzexsjLy51uzFN2ufvR82O3J5rqmPnMs66NabOo7jtURT3cVwC+nSS7dtdPZu1zPMWv8ARTj1D7myq905Fii7PiufmvFommxpfTGqao7Z+B/o11daMn4++c3z+zXP+YMZp1fNpnn4tX6v1OtZv9bV+rzQGddKNWzP9s8On4lUxVXHPn8Wz/Y9c1bMwq6vf4PMtZnQrSq8vd2Nd7eYiqP82zDb9UYeyLM1eOyxP+QNfPrgqir1BanMf8ni/wD0oQemT1k34yOuuo3YnmJxMaP/AOuENgAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAPU2xlThavRfpniYpqj9YeW5cWe29E/gDYH6NdYjM0SLddfM9nh7fq10ijK2lXfpo5qmieUR+jDV6MabNquvjniOOVhuvGLOo7SuU247o7JBq61GzVYzbtuqOOKpddlHUbT6sHXb1M09v3pYuD9W65oriuPeFrvRpuS5cz4xK6/nxxyqem/0oah9i3ZTzPETXAJ79Wuybmp4M51FqaoiOeeFHtSx5xc27YqjiaZ4bVeo+Fj6t0+uXaqaap+DzE/uaxepWN9l3hm2ojiIrngGNgAPX2tql3TNUs3bdU0xFUcvIfYniYmPkDZh6aN0Y+o7Ut27l6Jr7Y+aZ6ZiY5j2a7PTX1Cu6frGPptd2YpmYjjlsG0LJoy9Kx79FUTFVESD96rh287Au41ymJprjjyov6t9iW9Ju3My1Z455nxC+SKOv8AsSN2aJdmmjuqpo+gNX8TNNXMTMTCU+jfUnP21qlq3N+qKO6PmxTqRtjJ23uC9iXLVUU908eGMW65t3Ka6Z4mmeQbW+kW77G5Nv2r1V6mbkxHzZ217+njqzXpOdj6ffvTFHMR5le3aOvY2uabav2bkVTNMTPEg9sAAAAAB8uUU10TRXETTMcTEvoCKOrnTHS9c0u/fpsUd/Ez7KCdZdq07d1S5aoo7Yipsz31n04Gh3rlU8RNMtdnqR1a1qGtXItzHPeCGQAAAE1+m2f/ALxY0f2oQomj03T/APeXG/vQC2/XvIix03mZ/qf9FCdm49zP33Z7Imeb8zP6rx+pGqr/AHcRFPztf6Ksen3Qoy94Rerp54uc/wAQX+6O4P2PZ2Lz7zRDNXkbQsxj6BjWojiIph64AACMOu+3o1LbGRXRTzV2Sk95O7qbFWhZMX+O3tn3Bqf3zo+TpuvZdN2iYp754Y6nH1H2MO3ql+cbt5mqfZB/E/SQfAAAAH7sUTcu00R85fh6O3rM39UtURHPkF1vRjg/ZsGivjjw9P1bZtP2K7RzH7L2fS9gRibei7VTx9z/AERV6t9etzl3bFNf1j3BUfNnnLuz/bl7nT7So1jcNnDmmau6YeF21X8ntoiZmurwsz6YulmRf1exqt61PbzE+YBZj08bHtbb0i1d+DFM1URMeExOtpuNRiYNmxRERFFEQ7IOHNvU4+LcvVTERTTMqaeqXqPRlW8jS6L8cxzHESsF1w3nZ2/ol+zNyKappn5tb/UnW7+sbnyciq5VNM1Tx5/EHh6ZYqzNWtWoiapruef1X49MeyowtMx82uzxzETzwqX0M2he1vXbF/4c1UxVE+34tkXTnS7el7YxrEURFUUxz4/AHq7ipmdDyqaY8/DmIa2vUZpGoV7tuVxaqmma5+TZjk24vWK7c+1UcIc390ixtey5v/BpmeefYFQ+kHTPO1rTJuxRX3dvPs8PqT0r17TMqu/Fm5Vbj6xLYB0w2Fi7YxJt1Wafbjjh7G7tn6VrenV2a8W3FXHieAamM3BycSqab1uaZj8E2+kTS6c/dsTco5piuGTdeOnGJpuo3bdimmJmrxEMw9JezL2n5f2uq3MRE888An/rJruLoOxq8aK6aZm3xx+5rR37lRm7ozMiJ5iquVqfWVu29h1xg0XJ4n7vHKnmRdqvXqrlU8zVPION9j3fH6oiZriIjmZkFnfSttK5m5tjKi1MxExPPC3u/NTo0LZ1VuqqKeLfCLPRhplFO2Kci5RHdFH0el6v9VnTNrz8Ortmbc+wKVeo3UKdU6pZmXTVFUVY9mOfyohHL1t151Woazcya55mqmmP0h5IAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAD7TM0zzD4Al3oDu25pGvY1j4nbFVcR7tiGlWLe4dmW6r3FfxbXj9GqTbWXVg63i5MTx2VxLYv6eOoWNq23sTTpuUzXTTET5BUf1U7ejR9x19tuaY7/og5sI9UXS7+X8K/qdu3zxzPsoRuHTq9K1W9h1xxNE8A85nXR7WaNH3BbvV19sd8MFfuzcrtXKa6KppmJ58A2k7T1OncvTiaLVcVzNn/RQT1AbeyNN3blXqrcxE1ysf6Sd+2atLtaXl3Y5qp7eJlzeq/YUZmmXdUxLPd3U90TEAoyObMx7mNkV2btM01Uzx5cIAAPa2ZqNWmbgx8uKpp7KvLYZ6duodncGn2MOb8TVTTFPHLW1EzE8xPEpW6CdQ7+09dtRcu1dlVce8g2gvzdopuW6rdURNNUcTDFumm6rG6dBt5duuJq7Y58srBVn1M9K7GXj39VtY8d0RM8xCjGr4teFqN7HrpmnsqmPLbtu3R7etaNewrlMT3R4Un699DL+DfvZ+NZnieavEAq7gZV3CyqMizVNNdM8xwt96Wer1Nqq1g6jkce1P3pVG1fBu6dm1412Jiqmfm/Wi6nlaXnW8nGu1UTRVE+JBuB0zOsahiUZONXFdFUcxMOyqv6a+stvM0+xpWXeia/EeZWkxr1F+xRdt1RNNUcxwDkAAAABx5N2LNiu7V7UxyCPuvWbThbPu3Kq4p+7LWr1G1GM/W71VNXdHfK3fqr6kWq9Ov6Zaux3RE08RKj+Rcqu3q7lUzM1VTIOMAAABM3pv/wDxLjf3oQymn03RzuTG/vwC1HqGo7+nlH/6X+iBvTlYincM+P56ffUFxT09p5/qv9ECenfIojcXHP8APBe/QPGlWf7rvuht+edKsz/Zd8AHyqeKZn6A6upajjafam5k1xTTEK+9eesWBhadfw8PIp7u2Y4iXJ6od83NF0u7RZuTTMUz7SoXuXcOoa3qFd29erqiqfEcgyu1dz9+birt0xVciqvhmWvdGc/T9FnMqxqoiKeeeGU+jraMZWs2r+Ta5ia4nzC3vVrTdPt7CzY+Bbp7LfETx+ANVWpWJxs67YmOJoq4dZ7e+KKaN050U+3xJ4eIAAAyDYc26detVXeO2Jhj7nwsirGvRconiYBefZfUbSdv7U+FF6imr4fHv+CrnXLd1W4dx3K7dzuomZ+bCsvXtRv24t/aK6aPpy9fYG0c/dmqUWqaLldNU8c/UGR9DNkXd06tbmLM1UxX9PxbE+le1sfb+gWbXwYprimPkwD05dKLW0dPtZF+1HdNMT5hOdMRTHERxAPrq6rnWNOwrmVkVRTRRHPl2L1cW7VVyfamOVa/Un1Wt4Gk39Ox7sU3IiY8SCIPVv1Do1POuY2Ff54q44iVatOxb+qajTaoiaq66vL9a9qeTquo3cm/cqqmqqZjmUqenLZ2Rq+5rN67Zmbc1R7wCyfpN2BThadbyMqxx93nmYWesWqbNuKKI4iHjbN0Wzo2lWrNumKZ7I54h7gAADH9+bgtbd0S7m3a4piKZ93tZuRbxcau/cqiKaI58qh+rTqvbr065pOJeiKuJp8SCKepfUurcnUG3j2577dy9x4/Nc7o9peJpuwrOf2U0VVWu6Z/cor0A6eZm69x4mfdpqqpoud0zK6fUvXMfZHTr7HTdimumzxxz+AKhernVI1Dc1XbX3RTcQKynqHuKrX9Xu36qpn78sWAZX020KrXNbt48UTV96GK0UzXVFNMczPssr6Stl38nXLeVfsz2TVE8zALZ9C9Dp2xtKiLtPZT8OJlX31i7ysajbu4Nu7E9sTTxErRdQcvH0PZN/tqpomi3208fk1ndXtwX9W3Rl0zcmqmK5+YMLyau67M88uMAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAfaZmmYmPeE6+lrd17T91WrF+9MUd0e8oJeztPWK9G1KjKomYmJieQbX9RuY+v7Vv0WuLnxLP8eGuz1GbKytG3Dk5k2aopmuZ9lwvS5vKxr226Ld+/TNzsjxNT1euHTHG3hhVzas0zVVHygGsMWmv+mqq1eqi5Ex5fY9N9nt55/iCDulO6svQd04VVNyqLc3IieJbINvfYN87Bs0Xqaa6pszzz+SrmienWxjalavzMfcq591q+lekY239HpxKr1PMUxERMgoJ6ltmxtvctyqzZmmia59oQ42VdcekmPvK3XkWaIqnj3iFd49NtcZlVN2maaefmCrotpR6ZceqI4qj9XYx/S7j11RzV/EFQ36tVzRcprpniYnnlce96W8S3j1VRMcxCDurfTKnaM1zT8gS96ZeqtGlWrGn5GR4niPMrnbf1vE1fEt3se5TVNVPPiWonSNSydNzLeRYuVUzTPylcz0qdUac2u3iZt/2nj70gt88rcui4utafcx8i3TVzTMRzD0MbJsZNqm5ZuU101RzHEuUGur1GdJ9TwNxXszCxqvhTVPtHjhAmdh38O9Nq/RNNUfVtw3ftfT9fwLlq9YomuY4ieFIvUz0jv6JeuZWHjzMe/wB2AQX083Df0HXbORRdqppiqJ92wj0+9QrW5dPs2a78VVRTEeZa1b1i9Yrmm7broqifnHCZvTfv+5t3WLdi7emKO6PeQbLR4GytwYmuaJYyrV6iappjmOXvgAAMS6ma/a0fb2TVVVxV2Sy2ZiPdhnUbb1jcGFXYm7ETVHE+Qa1usW5L+sbty+K6vhxVLA11td9MWJqOoV5XdHNU8+7q/wDRXxO3jmP1BTMXIr9LGJ/Sj9XHV6WcWP50fqCnYuBV6WsaP50fq/E+lzH/AKX8QVCTR6aO65ubHiKZ8Vwk+56Ycemff+KT+j/RDG23l28iIjmmeQcvqYrqs9OImInxa/0VP6B7mjD3lRZu8x8SvmP1X76o7Ko3LtyrT5iOOzhXPbnp6t6Nue1nc8RTXMz+oLbbMvxk7exbse1VL2Hj7YoxcDRcbEpvUfcp4n7z16a6K45pqifykH1x5HPwK+Po5HyqImmYn2kFKPWVcqixe55VN0Gim5q2PbqjmKq4hsa689M8fdeNXT45qhB+2/TtYx9XtXqpjiioEtelzRLGJp9nIotxE8RPsy71E7jsads7Isd33qqZ5ZN0223i7e0qixTVTzFMR7vD6tbLsboxa7PxY8xxxyDWNuTJ+165l349qrk8POW91D0y49zKuXImPvTz7uGn0wWZ+f8AEFSH2I5W2v8ApisWrFVyZ9vxRR1H6dY22aa6aZjmkEPj93o7btUR8pevtLQMvXdXs4dmzXMVT5ngH3a+29Q13Lt2sWzVVFVURMxC9/pr6UWNH02xmZmPEXIiJ8w4PT70bx9Lw7OXmWI54ifMLH4WLZxLFNmzRFNNMceAcluim3RFFERFMRxEQ+1VRTTNVU8RD6xDqlubF29tjIyq71MVxTPEcg83qf1C0rb2kX6asiiLk0zHu109Zt2Xtf3PkXLd6arU1T83pdZepOfuTVb9q1kVxbiuY8SjnS8O9qedTZp5rrqkHqbM2xn7g1KxaxrVVVFVcRPENiPp+6bYegbdx8i/Yim92x8kY+lDpnbx7FrKzsf2ju5qhay1XiYtqmzRct0U0xxEd0A7ERERER7QOOm/Zq/Zu0T+UuQB8qmKaZqn2iH1x3q7UUTTcrppiY48yCBfUR1Ux9uYN/Fou9tVUTT4lRPWMzUt9bpmLXfcmuvxHv8ANcP1DdMa92arNeLc7o7ueIl0ehnQO3oet05+baiae6J8wDLfTzs+rZ+ypzsux23KLXdzMfgrj6muqOVrGuZOlWa6uymZifPiF/tZw8GvQb+n0Tat0zb7YiJhUTe3p9tavuHIze6Ji5VM88gp3M8zzL4tdT6abHHmf4uvk+muOYiz5kFf+nWh3tb3Ji2Kbc1UTXHPhsq6MbKwNv7YxL8Wqabvb3T4RV0a6D0aHft5N+1HNExPmEy9Rdy4u0NrTT300zTamI8gr16r+pF3Crv6Zav8RMzHESpXqGRVlZly/XPM11cs+647oq3HuS5divup7p+aOgAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAASt0N6i5+2dXsYtN+qm3VVEe7Yt0z3HY3BtzHvRciq5NMc+WpnDvVY+TbvU+9E8rVemrq3OJftYOTe4pjinzILF9atduaFbuX6KOIiJnwrjlddq7WZVYmrjtnhaPdeiY2/dvd2PMVzXR8lJuufSLVNt5d/KtW6+3mZ8QDOL3Xaum3zTcjn83nUdfc6M2iIyJinn6qzXKr1Fc0V1VxVE8TEy/HdVzz3TyDaJ0K37Y3To1NNy7TVcmI45l1PULuGNq6RGbaoiJ7efCmHQPqjf2zqlixcuzFHMR7rd63jY3VvafwrNcXK5t+0SCA9K9Qt6q7XTXXxxPHmXpT6hLtHmm7H6of6y9JtX2bmXKqLNyLcVTM8QiWq5diZia6omPeOQW5/wCkTkVzNFV3xP4or6v79o3NYq+9EzKGviXP6dX6vlVyur3qmf3g/L3doblztu51ORiXKqeKuZ4l4QC8Xp/60zqVzHwczJ5rniPMrYYV+jJxbd+iqKqa6YnmGofZ+u5Gg6zZzbVyqIonzHK6XRjr9ZzcWxg5N6OY4jzILWPA3htbTtyYlVnNtU1TNPETMO7t7VsfV8CjJsVxVzHM8S9IFJevPQ77HRfyNPxvuxzP3YVXz8PP2/qfFdNVuuirxLblrmlY2q4VePft01d0cczCrvXDoLbzqbuXi2Y+c+IBEvRfrXl6VFrEysiqminiJiZXG6Y9SdM3Ni0U036JrmPq1s702dqu28+umbN2LdM+8RPh7vSfqVn7SzqfiX6+yKvnINp8TExExPMSK1bF9QOLnYNqm7ep54+cssy+tWn2sWbsXqOYj6gkHqNuC1t/Q7mVXcimYiZ91Ud0eoC7Z1CumxkcxTVMe7wuvvW+rW8a9hY97xPMeJVfvZF29cquV11TNU8z5BbPF9RmVFviq9/Fzf8ASMyf63+KofxK/wClP6vvxLn9Or9QW6/6ReRP/e/xP+kTfn/vf4qi/Euf06v1PiXP6dX6gt1/0h78/wDe/wAX3/pC3f62P1VE+Jc/p1fqfFuf06v1Bbi56gblVUf8WP1Z90u6yVazqFrHm5E90xHuoT8W5/Tq/VNPpst5FzcOPVNUzHfAL7by3PGlbanOmf5nKsG7OvFVGXdt27sRNM/VNPWi1cnp1NNM+fhf6Nde6/j29ayKa66v2p+YLEYnX7NjIiJyaop5+qXem/XbDybtu1lZMc1fWVBO+qJ57p/V29P1PMwsmi9Zv1xNE8+4Nuu3dewtaxKb+Ndpq5jnxLr7v3Hh6Fp1y9eu001RTzHlQ7pn19ytAw6bN+9VzEceZcPU/rvk7hxqrNm9V5jjxIJd3x13tW865atX4mKZ492J2+u9VFc1Rcj3+qquZnZOVkV3rl2qZqnn3cHxbv8AWVfqC62h9f6Krc/FyIjx9Xhbi9QV6jL/AOBkc08/VUinIv0+12uP3vzVduVTzVXVP5yC3GN6grlVuO+7HP5uzb9QMx/3sfqp9F25HtXV+r78a7/WVfqC2+q+oCuvFqopux5j6oD6jb/zNw5lfnmmZ+rBaKr92qKKZrqmflCUul3SbUN0126rlmuKap+gMV6f7O1DdWpUUY9qqqmavPELrdDOimNpdFjLzMamLlMRM8wyXob0bw9p2Ld/IsUzVxz5hN1u3Rbp7bdEUx9IgHHhY1rEx6bFmmKaaY48OYYfv3e2n7bw667l6iK6Y9uQcnUfduJtjSq7t67TRVNMzHMqMdeOsWVrly9g4+RVNqZmPEux6jesN7cWRcxsW9Pbzx4lXW7cru1zXcqmqqZ5mZB8rqquXJqnzVVPKXfTftC9rW8bNV2zM2omPkirS8W7mZ9mxZomqquuI8R+LYR6YOntjSNv2NWy7cUVTETzMcfIElarm6XsTaXNPZbri1H+SqW+eveXa1q5Rj5M9kVfKWQesXfnwYrwcLI5iPu8RKmuVkXci9VduVTNVU8+4LgbF69XL2bat5GTPFUxHmVrtj7lxdc0q1et3Iqqqj6tSuBk3rGXauW66omKo+a9HpT3LcyMazYyLk8REe8gtFmXIs4ty7M8RTTM8qrdbOtsaLqF3Dx8j71E+YiUk9fup+JtfQMi1avUxXXRMR5UJs4es9Q96XLtFNy5Rdr9/PHALadBt6Z+9c6iuuKq6ZnnynvfOs2Nr7Zqy65pommlHPp82LibB2tRmZ8U27lNvn73j5Ii9VnV2jMxLukYd6PHNPiQedu7r7fp1K5FjJntiqY8S6mJ16u1Wo77vM/mqtdvXLlc1111TMzz7vkXLke1dX6gtXf6/wBdujxXz+9KnQjqNVuvU7du5T3RMx7qcdN9i6punMt026K5oqmF4egnSido2bGdlR2fPyCcdSyLeBpt7Inimm3RMqO+p7qjOoXb+nWMjzRzHESmv1G9WsXQtKytNx71PdVHHiWvzcuqXtW1jIzLtcz8SuZjyDoX7td67NyuZmZcYAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAO7pOpZOmZMX8auaaonnxLpALzelLq9ZvYFvT9Tv8zMRT5lPm/wDbGnbz0Cquimi5NVHieOeWrfam4MvQtQoyMe5VTFM8zxK6vpm6129Ym3peff8ApT96QV+65dJtQ0LU7uRjY8xb7pmeIQndoqt3KrdccVUzxMNsm/dp6VuzRrlNNu1crmmeJp4UL679I83budkZWPj1ds1TPiAQhauV2rlNyiZiqmeYlZP0p9XLug6vTh6lf/4U1cfen5K2XbVy1V23KJpn6TD9YuRexr1N2zXNFdM8xMA2h730jQepO1si5ixbuXZo+77TKgXVfpjrG2dZyq5sVfAiqZj7qSfTt1pydK1bF0zPv1TbqmIq7p8Ldby2rt/qFtq3dx6bNV29b55jjkGrKqJpmYmOJh8Sr1x6aajtTXb3wsaubPdPtCLK6aqKppqiYmPlIPyAA7+i6plaXl038a5VTMTEzxLpW47quHftaZdvUd1umZBcD09da7NjGsYmfkfSPMrYbb3Bg67i03sS5TVzHPES1G2L2fpWTTcoquWqqZ5/BYPof10ydEyLOLlXquPEeZBsGcWVj2smzVau0xVTP1Yp0+3vp25NLovxkURXMc+7L6K6a6e6iqKo+sAiTqx0m0jXNHv12Memb3E/zVHupfSDWtF1C9ds2KotczPHa2ezETHExzDGN47Q03XsOq3Xj0d8xxzwDVHazdT0W/VYiuu3VTPtLtXd2avctTbqv1cfmtj1n9PVFGNdzsWzEz5mOIVN3PtnUdFzrli7j19tM+J4B4uReuX7k3LtU1VT9XG+1U1UzxVExP4vgAAAAAACfPTJH/23jf3oQGn70xx/9tY/96AW26yzxsCf/wBL/Rrp3xPOt3v70tivWmeNgTz/AFX+jXRvSYnW73H9KQeGAAAAAAD9UUVVzxTTNU/gD8u/pWlZmpXYoxrVVUz+D2dlbQ1HX9Ros0Y9fZM/Rcboz0Hs2cG1l5NmOeInzAIQ6J9GtR1PULd7Ox5mmZj3pXg6c7C03bunWopsURciI+T3Ns7Z0/RcamizZoiqI9+HugRERHEPldVNFM1VTERHvL83r1qzRNd2uKYj6yhrrR1XwdAwL1qxfp7oiY8SDL979RtF2/hX/iX6YuU0zx95Q7rv1Xz9d1u9Zw8iqbUzPtUxvqd1L1LceZdi3kVxRNU/NG9ddVdU1V1TVM/OQfrIvXL92bl2qaqpn5vuNYuZF2LduOapcTP+iu2MvXt349n4FU2u6OZ4BKnpr6U3dVzLGdmY8zTTVFXMwtF1a3fh7H2NODiV00XKLfEcT+DJ9n6HpGzNpd1cW7ddNjunnxPso/6mOoN7VNyZGFYvTVaiZjxIIp39unO3JrGRfybk1UzcmY5ljL7VPMzM/N8ByY8xF6iZ+Upx6V9QLO2rEVRc4mI+qCmTbG2vq249Wt4uJZuzTM+Z4kElb11nVOpms28fHm5Xbqq48LOen7pPp21dDo1PU7dFNdNMVfehwdEuj+HtfSLep6rapiummKvvQ8Tr51lxtHw7ulabeintiafuyDp+pXrDTp1m5pml34piOaeKZUy17V8vWM2vJyblVU1Tz5l2N1a9la7qFeRkXKqomefMvHpiap4iOZApiapiIjmZZnsjYWq7gzbVNuzVNEzHyfOnOzdR17WbFNOPX8Puj5NgfRbphgaFo+NlZWPTFfbE+YB0PTz0wxtu6NRkZtimLkUxxzDvdeupWJtnQq7GJeppuRTPtL0+rnUPTtraVdsY92imuKJjimVBOr3UPM3LqV+j41U0d0/MHhdSt45u59Wu3bt6qqnun5sPJ8gAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAD3dmbhytvarRl41yqniY54l4QC83QnrpbyK7GHn5ETVXxT96VgdzbX0be2iRdrot1/Fo5ieOWqfRNTydL1Czl2LlVM26onxK4PRLr/NGJY07Lv+IiInmQYh1z6FZeDdvZWBjz2Ucz92FaNUwMjTsuvGyKJprpnjzDbFpmo6HvLQZo5tXJuUeY8c8q8dX/Ttazci5nYtiJiqefEAo/iZF3FyKL9mqaa6J5iVlOgPXnN0jJsYep3Zm3b4p8z44eJl9CcqzcqibFXEfg6k9I68O5FXm3MSC4msYe2ep+g1X7du1Xero59o554U+6z9G9R0bOvZOLj1RbiZnxSn7oDkW9vXbWPlZPNEcRPNSety6dt3c2lV2blViua6fuzHHINS+Xj3cW/VZvUzTVTPzcK5XUv08U5WfcysOx3UTMz4hGOd0Mysa5xXYqiI/AECUzMTzDNNj6niU3abWTRHmePKSsTo7h+17tifxd7H6N2KMm3XZmOIqifAOHVOmv8AL2i/bcGxzzTz4hCO4dEz9v6hNu/RVRNFXirjhsW6QaTo2BolOBm1W+e3jywzrj0Y07X8e7laZaprqnmfuwCq3TTqxrGgZFqxXkVxaiYj9pcfo71q07VbFvHy8ima6oiPMqL782Hq22825TXi3PhUz78ezydr7iz9C1Ci9ZvVxFM+Y5Bt1wMuzm4tGRYqiqiqPEw51N+kfqDm3p9nCyL3nxE8ystsbfOm7gxaZjIo75j6gyvPxLObjVWL9EVUVe8Shnqn0a0rVrVd/GxqJqn+ymyiqmumKqZiYn5w+zETHExzANdnUroZqWDTdyMfHqimOZjiEE6zpeVpWVVj5NuaZiePMNu2t6LhaphV496xRPdHvwrZ1b9PuPql+vJx7MeZmfEAoaJj6gdHNQ0Smuqxj1z2/SlE+bp2Zh11UZFiuiY+sA6gO5pVinIyqbVXtMg6YmzZ3TDC1izRXV28zDNLXQPCrtxVEQCr6ePTDlR/L2PRNP8AOhk1fQTDpq47YSR0i6SWdF1O1ft0x92YkEgdfcr4HTyaoj/uv9GujX8icnVb9yY4+/LaF1L2tRrW1Zwq/bs4VQ1foTiRnXKpiPvVTIKuizdPQfDmnnth5mtdF8LCsVV8U+IBXcZZvDQLGk3aqaOPDFJB8HJYsXb9fZZt1V1fSISR086X6nr9dM3sauKap+gI/wBJ07J1LKpx8aiaqpn5QsD0p6IZ2oWreRk41UxPmeYS10j9PdnAv28rIsx78+YWb29oWFpGBRj2bNEcR5ngEX9MOkGl6LTRfvY9EVR5/ZTDiY9rFsU2bNMU0U+0Q5YiIjiI4fLldNumaq6opiPnIPrzdwazh6NhVZOVcppimOeJljPUDf8Ape3cGuuMmiblMfVT3rX17ydVqv4GNenieY8SCUesvXjFtW71jAyYiqnmPEqgb53zqm4s27Vdv1TbmZ95Y1nZ2VnZNd29drqqrq58yzvYGxrWudk3ojz9QRyLH3eimn/Zu6mae7h0cPoVfysmKbVmaqZn5QCLemm0MzdOsWrNi1VVR3RHsu7036eabsLQo1jNsU03KKOeZj6Of0+dJcLaHZkZ1mmmqPMd0JL6sW8LVNp38G1ft98xMRFMgqd1869X8mbun6Xdnjzb4plVzVM7I1HMrysmruuVzzKxOZ0XpytSvXL891NVcz5cWT0U063TxE08griJ+q6I13qp+z2pqj8IZFtH05387Ko+PjzFPPzgEMdNen2q7r1CzTYsVzbmr6Lr9JunOk7F0ijVNUsUU100881R9GbdJ+nGh7Gxafi02ab0R4mrjw/PXHPwMzbNeHi5NHfxP7MgiDrr17w8fAu6dpVyIimJp+7KmW6dfy9e1G5lZFdU908xEyl7N6c16lqN6u5e7+6qZ8y+W+iOTdribdmqaZ/AEF0xNVUUxHMz7JV6QdMdR3Fqdq5csVTbmY+SV9k+nG7l5dm7csTxFXPstP0/2No+ytKpryKLdE0U+8xAPI6VdJtH29p1vIyLFEXYp59n3qz1R0vbOlXMaxeopuU09vifo8jq51j0/RcLIsYWRTz2zEcSot1Q35n7j1S9Px6uyap+YPT6vdSM/ceqXIov1TbmZ+aL6pmqqapnmZ9yZmZ5meZfAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAHYwMy/hXou49c01R9JdcBYTob1mzNGy7NnMyKooiYiealz9ndUNB3JiWrfxbc110xFUctV1FdVFXdTVNM/WGabF3/AKpt3Jpri/XNNM+PINo+RoGl5uPNdu3RPfHMTEK6dc9Ly9Ji7Xi2p4p59ofnoh15o1KLOJm5HieInmU6bgwNB3hpkdty1XVcp+oNeOodSNU0rPqtx8SmqJ+rJdo9eNXsZtuL2RXFET85Tb1H9NNjLsX8/Ft0zVETVER7qmb62DrG3tSuWoxLlVumZjxAL4dMus+h6vpNFrOu25uVREeZhn+TgaRuLTqruH8Kapjxxw1aaXq+r6HlU1U3L1uIn9mZlYjol15vYOTaws2/VxzEfekGXdZMXVdt5dy7aoq7ImZ8Itwurl/FuzZvxMVR48rf5Fe1+oG3u+9es/Erp+cwhHqD6eMOq1dzdOmmrmJmO0EWah1ozbNUVY1+qnz8pTB0R684t6qixrV+mqmrx96VT96bM1jQ9WuY9eJdqoiZ4mIY5/6Xh3ImJu2aonx5mAbJN3bc2t1A27du4NNmq9XEzHHH0U76mdE9b0XNv5FizV8HmZ/Zd7od1ky9v51jEzsiubfMR96fC5ug7k2jvrQItZFdj4lynjzx9AayZnM0nNqt81W7lE+Y9ki9Nequr6Hn2qbmRX2RMfzk39fOhFim3d1bSrUVxMTVHbCp+raNqGm5Ny1kY1yjsnjngGwrpd100fN0+1YzMima+I96kz6Br+BrNmLmLdpnn5ctRuk6vnabkU3bF+5HbPtynDpt17z9Ers2Lt2vjmImZkGxh8roprpmmumJifqiLpV1c07cWLb+05FEVVRHvKVcfPw8iI+DkW6+fpIPH1vZ+j6tbrpyMeme6Por51j6E42Tbu16djU8zEzHbStI/NdFFccV0xVH4wDV1ubo5uDTMi7xYr7ImePusC1DTs7RMuPjW6qaqZ95hti3BtLS9Vs1RVj0RVV+CEeonp7wtVouXrNqnnzPiAVE2N1GytMqot1zMRH4pT0/rPFNmmKrse31YL1P6N6lt+uucTHrmKZ+UIoydK1TGqmm7i36Jj8AWVv9ZqJriYux+qYOinUC1reVatzcp+9MfNr+rm7RPFU1xP0mUo9D96V6FrFn4l6YimqPeQbDep+sU6RtyrKiuP2OVR9w9X4o1K5R8WPE/VmPVPqhj6xs6bFF+mapt8e/4Kaa3kXLupXq5rmeavqCyNHWSmLXHxY/Vjm6uq9eVjV00XInmPqga38a5V20d9U/SHraNtzV9Uy7dm1iXpiqfMzAPzrmsZWsZU8xM90+Ievt3p/retRTOPZr+9/ZWB6P+n2rUqbWRmWJieImeYWb2L0p0vQKaIqs0T2/gCs/Q/oJlfard3VMeZiZ5nupW12v0/0TRca3Tax6O+mPlSyrGxcfGoiizZooiPpDmB+LVui1RFFumKaY+j9uO/kWbEc3btNEfjKPupHUrTNu4Vyq3k0TXTH1BmWva3g6PjzeyrtNMR8uUF9XeuulYWn3cbByKYuRE+1SvvVrr5mavevYuPdq45mPEoA1fVs3Usqu9kX66u6fbkGZ786na3r2fej7TX8KZn+cj+9cru3KrlyqZqqnmZfgB+rc8VxP0lIu0d5/yPYopt/tQj/GxMnJqimxZrrmZ48QmDpH0i1HX8m1czLNdFuZifMAkbpnrur7nzrVFNFc0TMLYbcwNN0Lb9GVqHw6a4iJnliPT3ZO2djaXN7IuWablFvu8zHvwgf1CdcZozrulabfmaI5j7kgkrrX1u0/Taa8bTr9NMxHH3ZQDHXLULuTVTcyapo5/pIO1nVs7WM2q9fu3K5qnmI5frTNB1XPv02sfEuzNU+/AJ5yus0xajtnmqY+r09nbl1fc2dbi1RX21S4ekHQPJ1u1bvalTNNPvPcsXszYW1dl2O+9esRXbj5zAMi6a6BZxtNpyNSiiPu8/ecm7Ooe3drY1z4dyzFdP4whPrn1vxtDoqwdLvx9IiiVVd6b81fcuRVxfu8VT7RM8gnzqp6gsm/k1/Ysie3n+bKLbvWHWNSufCuV3Ku6fqjvRtuazq+XRatYt6runzVMSsl0j9PF/UqLOVlWJj2meYB5HTLM1bV9VtTNuvtqqjnldDZW2cKNDsV5NimblURPsxnZnS7SNrU0136bdPb85fvqR1P0vaunVUYuRR3W48REgzLXNb0bauN3XqrduOOZjmIlW31B9eMWrTbuJpN+ImImI7akK9Yut2fuLJvWbF6riZmPEoNzMvIy7tVy/drrqqnmeZB7Gvbr1bWLldWVkVTFUzPu8CfM8yAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAPQ0fV83Sr8XcS7VRMTz4lNfS7rjq2nZNq3mZNfbTx71IEfYmYnmJmAbLunnXDQ9exbeLk3bc3aoimZ7vdleu9Pds7ow5yPhW6qrkcxVERMNX22dw5+i59GRYv3IimeZjuWO6b+pHMwotYeReq4jiPMgyHq96eLl69cnTbPznjthXbdfTXcO1r3xbtu5zRPMT2r/9O+rOha9j0fbL1qK6o96uJe1vLaO29548RZjHrrmPemIBry2fvbeWmX6LNmb8W6Zj6rWdGt/52q2reLqvPE8RPcy/H6BaTa5mLVnmfyebuTpvc2zZ+1YNH7PmO0Gd690u25uTDjJm1b+JXTzE9sK9dUPTpcu3bk4FuOOZ47Ycmsdc9X2pM4d2bkU0Tx5e5sf1H6dqWTRbz66fM8T3ArZuDopuTSblVfw7k00zzH3XDtzWN47Z1SxZt/HiimqIn3bAtP1/ZG5sGJuV43dXHz4eHn9Ltr6tfm7iU2KpnzHHAOPopr9G69t0YesUxVXNMRPc8bq/0L0rVsa7f0+1RTXXzPHHl6d7amZtGib2BRVFNPmO1gm5Os2t6RnRYybVzspnieYBX7dvp+3Dp167dsUV/CiZn9lE+4tu6jol+beVarjiffhsP2N1O29ubDjH1GLVNdfie50t+dIdtbrtXb+HNmqZpmY4BQnbW9Na0O5ROLkVxTT8uU99LuvOVj3Lf2/Jq4jjnmpHnVrpLqG3c679jxq6qKZn2hFeRiZmJVMXrNy1MfXwDZlsfrVoWtWrdqq/RNyeI55Srp+bYzsem9YriqmY+UtR21d0ahoWbRetX7k00zzxysL089SGXhU28a9dqj2jyC+hPmOJQbsfrXg6nRbm/kURz9ZSvom59K1S1FVnKt8z8uQceu7S0fWKaoysemqavwRTvzohpGVZrqxMajmYn2pTpTVTVT3UzExPzh9mIn3jkGvrqD6f9UouXLuJj1xET44hDm4tka3tyubl21ciaPnw2w5GHjZFuaLtiiqJ+tKNN/8ASXS9w27nbZoiavwBrNu65qVdr4Nd+rtjxxL1Np7O1Xct6Ps1que6ffhbbN9LmLczaq4sxxM8+yVul3RrTtsxRNyzRPb+AK3dN/T9n/FtXczHqmJ8zzCzOxOjuh6bj27uRjUd8cfzUr2MbHsW4otWaKYj24hyg6el6biadZi1i2oop/CHcfLldNFM1V1RTEfOWL7j3ro+k0VRXk2++I+oMpnxHMsR3nvrStu2a6r96jupj6oP6oeoG1pFq5Ti34n8pVb6jdX9U3PNymm7ciKufPIJ+6p9f7VVN6jByfMc8cSrFvLqLrev5NzvyK/hzPtM+7HNP0zVNXyI+Hbu3O6fNU8pP2905xqcSm/nxTTVxz94EdaJtvP1ivutxPNU8+YZL/uq1ybXxIoqmOPozzTL2l6JlxbszRVMT8k29M8mxrk27NViJpnx7AqJe6fa1bu/DmzXz/dZLtnoruDV7lHFquKJnz91fCrp1oPFGRfotU+OZ5h1ty67tTZul11Y82ZuUx444BE3SH08Wsaq1XqVFPEcTPdCcNf0HSNobaruYNqim5RT48cTKA73X/Ub2rTj6darmnniO2Gc7e1nXt60U2cu3d7K/fkFeurG/wDdmfql7Ew5vfDmZp8c+yPMDppujcuVOTNu5NdzzMzTMr0Wuj2jWb0Zedbt8z5nuZPpmNsfbmP3VV40VUR+AKedPvTpq9WXRc1C3VxM/OFnun/RDQtKx7d3Ls0VVxHtFLxepPXTbmgxVThV2uafEdqM9P8AUrnahm/AxKqpiZ48AnHqlq+LsnR67emU001RR4iPdS7fPULemp6xdpx6r/wqpmI45WWwcTUuoNNNzJprmmv6so03oZptuimu9atd3vPMAovb2VufdGXF67Rdqrq+sTKWOm3p61G5ftX82zXMTMc90LgaFsHb+g8XL9qzER9YdrcO9Ns6FhVxbvY/dTHiKePAMb2F0c2/o+HbuZGPRVd4+kPY3RvXb+x8OqxFVqnsj25QV1E9R8adVds4d7x5iOJVj6k9UNU3VeuTN+5FNU/UFierHqFpybNyjT8iI8TEdsqubt35rWvZF2b+RXNuqfaZYpXcuVzzXXVV+cvwD7MzM8zPMy+AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA+01TTVFVM8THzfAGQaRu/W9M7fs2VXTFP4p16Mdec/TciinUcmriJ890q1PtNU0zzTMx+QNmu2Ou+3tUt26a67fxJ4ieKkiYGqaTubBmimuiqmqPbmGprRtd1DTMim5ZyLnET7cpc2h171XRLdFHxLk8fmCznWboXh63Ny/iUxM1eY4hXHWvT9uTTcqq7hUXoiJ5jiJTJ079RtrU6rVGbcieeInuWB21vjbGt4lNU3semqY8xVEAoBqNjf20aYnnJimj82VdNOvus6PmUWtSqueJ893K5m7Ns7U3PjVWqacaquqOOaYhEWtemHTs6/Vfx/hUd3mPMQD3dE6+bf1jT6LWZ8PmrxPLr7j0Xa+9rFVzC+F8SqPlwi7d3p81fQrdVzDmuYp8x2yxLTM7d2zsjt+Hfqppn6SDJ9c6U7g0rL+Npd25TRzzHbKUejlvcmHdos6jdrmn2nuRngdZdbrmm1k4lzx4nmlmO2+pXfcpru0fDn8gT7rOy9G1vE/9Lx6Kq66ffhXfq50Dt5c3J0/Gj58dtKY9r9SdOuUUUX8inj8ZZnjbn0LLoiac2z5+Ug1ubl6Jbg0q5cq+Fc7Kf7KONY0fO0nI7L9uumYn34bYNZwdu6zj1W64x66qo94hFO6+gei67VXct27fkGvvT9y6vgxEY+VVTEfikzpn1i1fSsqijMyq+2J/pJF6lenerSrNyvDx+eOeOIV817ZOu6XlV25wrtVMT4mIBeHp/6gtKu2rdnKv0TMxHPMph21v3R9b7fgXqPvf2mqaLWq6dci5237NVM+7P8AZXVrVtAmjm7cntBtIoqprpiqmYmJ+cPql2wvUnfyKrVjIvTEeInmVjti9S9H1nEom7lW6a6oj5gkMdG3q2nXKe6jKtzH5vO1zdmj6XjVXLmXb7ojxHIPfmYiOZmIhje5t4aXodFVWReo8e/lXjqx6g6dKm7aw78T8o4lXDfHWnVtwUV0xcuR3AtT1N6/6VZxbuPh36Iq4mI4qVF6gdVNc1fVLk4+VXFvmfmwKudT1S/Nzi9eqqllmyem+t63l0fEw7lNEz9AYrlZupa1kU0Xa671VU+zPtidINb3Bk2qvhV/DmYmY7Vi+lnpys9tvKyrER7TPdCyOzNlaXtvHppos2+6mPE8ArrtfpPi7Z0uLmZjR3xTz5hh2+9K1DPrqxtKt1RHtHbC5W5tKw9VxfhVTRE+3hjejbP0PTcj4uR8OfPPkFQNpdGdy5mfRkZVq7NMzz5iVo+lvT2NFxrdVyjtriI94SJGq7fwrfbTfxrcR8oh4Wv7+0fDtTFjIoqmPnEg8TqZGp2sKu3iXJiYjiOFctY2XuzcWo1UXr12bU1fVKW7upVN/u+HMVI7y+qmp4V6qMfFqq/KkGW7J6YaLtu1Tl6tFE1R5nuZVf6o7P2rFVGLFruoj5cIJ1/e+7Nx0/At2L9MVePES49s9HNx7pyIuZPxYiueZ5BkfVH1JXMi1XZ0+Z+kdqGKd1753ZmVfZqsjsrn5crGaf6WLHbTcy6qJn3mJqhJmyOmO1tqRTGRRY5p+vAKbU9HN57hmm7kRfnmefMSmHo36eK8LJtXtRpmJieZ7oWb1Dc20tFxZmLmLEUx4ppiEK9Ruv8Ap2kV3IwaqI49u0E46RpGkbT02O2aKeyPefDFtydY9vaPNdNy5bmqn61Kibx9SOpapFdm3cuds+PHKFt07w1PWr9VdeRcimqfPkFqervqFoyrNyjTsiI8TEdsqzbg6kbh1TJuVVZdfZVPt3Swuu5XX+1XVV+cvyDsZuZkZlz4l+5Nc/jLrgAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAADsYeZkYlzvsXJon8JZTovUTcWmTHwsuvtj+1LDgE+bH66atiX7c5OTX4nzzKwuzfUnpX2ai3n3Ldc8fOWv8AiZj2fqm7cp/ZuVR+Ug2a4fWva2s0xZqrs9tXymrl7GDp+ydxz3VU2Jmr8msTS9e1HAu01Wsm5xHy5SFtrrJrOkdvF67PH4gv3n9IdqZFE1WLFNFU+08Rwj/c3R74ddX2KeI+XCDNG9UOqWbdNq7cucR9eWWaX6mPjTHxa4n8wepndMdyWJmce5d8e3D8YW1d5YFfNVd/iPzZhtPrvpWbXRGRNvz78pS0rf8AtTUrMTXdsUzMfPgEP6Xm7gwKoi9Nzx9WZ6RvzNwrcfG58fVmeXk7Qy47ou4/M/SYeVlaNtvMiYtXrXn6SDzb3UDTNQ5tZluirnxPLwtZ03a+rxM04tqaqvwe3PT7Srt3vt3qP1etp+x8KxxMXYnj8QVp6q9O8OrFuXcTGj2mfEKubn0i/peoV267VVNPP0bSMjZ2DnWZs1U01RMcI/3P6edG1i7VcqotxMg1x2L12xciu1VNNUfOGT6Dv7cGk3aarOXXNNPy5XOyPSvpE0T8Om1M/JFPUv065Wk0V1YeLPiPHEA8Db/XTULeDFN7Jr7uPnUw3fHVvXdVvVW8fKriifnyx3U+nm5MPJm19irqjniPD3dl9JdwarmUU3sOuKZn24BH2o6jmahc78q9Vcl29t6Fl6xmUWrNquaZnzMQt3tL0wUZWHRcycemmZj+d4SbtH0+6ZolVNcUWpqgEP8ARjpDam3avZmPExxEzzCxGj6Ltrb2NT/6PaiumPoybTtvW9NsRZt2oiIjjmHn6ltarOuczVxH5g+1b90zCtfDot09tPtwxjWuqVFy5Nu1HEfg9e/sHFmj/iXI/V509PtIoud9y7R+oPMtb1ysinm33eXlatq+t5kzFqbnn6M6w9E25gxEV3rXj8XqYt/aWPMd16x4/GAQPqWh7uz6p+FVeiJ/NxYvTzdGR/2i5d8/VYbK3ZtPT7PdF/H8R7RwwDdvWbQcGK/s9Vrx9AY1oXSXJruUzlVTx8+UhaR0g27atU15dumur8oQlrfqPtY9VXwaqY4+jDNZ9UOfNNVuzdq/dILUajtvY+g0fEqosU1U/LmHhX+rO0duzNu1VYiKfHiYUt3R1v1nWIqib12OfxRprG4tS1G7VVcybnE/iC+u6fUnotvGrowa7VNXHvFXlAG/uvepZl65OLk1cT7cSrpVeu1ftXK5/Op+ZmZ95mQZvrXU3cmo1TFWZcimf7UsV1HVc3Pqmcm9VXz+LogAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAD9UV10TzTVMPyA9HD1fNxpibeRXTx9Ht4m+Nbx4iKNSv0xH0YmAkTG6m63biO7Vsnx+DItF6x5mLVE39Vy/3W+UMgLNaX6g8KxEfG1TP/HjHmf9WR4PqX2/aiPi6nqX/wDFn/zVCAXa0/1TbOszE3NR1T/+HP8A5vaserfYlMRFeoapP/7Cf/NQsBsGxvV70yiI+NmarP5adP8A5uPVPVh0kzbE27l/Va/Hz06f/Nr9AXG1Pr10kycj4lNGfMc/PBn/AM3tbd9R/R/TeJq/lGmY+mnzP+qj4DYdZ9XnSWiiKZytX4iP/h0/+b7c9X3Sfj7mXq/P/wDzqv8Aza8AF+871bdOLv8A1edq0f8A7CY/1ebf9VuxJifh6hqsf/sZ/wDNRYBcjVvU7trI5+BqepR+eJMf6sU1T1CYF+Kos6pn+frjzH+qsQCatZ6y5WTM/A1XM/fb4Y5kdT9arqmadWyf0RwAzfN6ha5fp4nVMifzeHm7j1LJ5+JmXKufq8QBy3si9dnmu5VP73EAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAP//Z"""

    def __init__(self, root):
        self.root        = root
        self.root.title("BibleCue")
        self.root.resizable(True, True)
        self.settings    = load_settings()
        self.listening   = False
        self.last_fired: dict = {}
        self._fired_lock = threading.Lock()
        # Track the current verse on screen for navigation
        self._cur_book:   str = ""
        self._cur_chap:   int = 0
        self._cur_verse:  int = 0
        # Fetch queue — 3 persistent worker threads drain it immediately
        self._fetch_q = queue.Queue()
        # Persistent HTTP session — reuses TCP connection to ProPresenter
        # avoids TCP handshake on every verse (saves ~100ms per send)
        self._pro_session = requests.Session()
        self._ws_server  = None
        self._deepgram   = None
        self._http_port  = None

        self._pulse_running = False
        self._build_ui()
        self._bind_autosave()
        self._start_ws_server()
        # Start persistent fetch workers — always ready, no thread spawn delay
        for _ in range(3):
            threading.Thread(target=self._fetch_worker, daemon=True).start()
        
        # Audio device logging
        if AUDIO_AVAILABLE:
            try:
                import sounddevice as sd
                dev = sd.query_devices(kind='input')
                self._logd(f"Mic: {dev.get('name', 'Unknown')}")
            except Exception as e:
                self._logd(f"Mic error: {e}")

        # Auto-start if enabled
        if self.settings.get("autostart", False):
            self.root.after(1500, self._toggle_listening)

    # ── UI ───────────────────────────────────────────────────
    def _ent(self, parent, var, width=20, font=None):
        """Styled entry widget used throughout the UI."""
        font = font or ("Segoe UI", 10)
        C = getattr(self, "_C", {
            "input_bg": "#0a1220", "fg": "#e2e8f0",
            "blue": "#4f8ef7", "border": "#1e2d4a"
        })
        return tk.Entry(
            parent, textvariable=var, width=width, font=font,
            bg=C["input_bg"], fg=C["fg"],
            insertbackground="white",
            relief="flat",
            highlightthickness=2,
            highlightcolor=C["blue"],
            highlightbackground=C["border"])

    def _build_ui(self):
        # ── CONSTANTS ────────────────────────────────────────────────
        C = {
            "bg":        "#0a0f1e",
            "sidebar":   "#0d1424",
            "card":      "#111d35",
            "card_hi":   "#172240",
            "header":    "#0d1830",
            "input_bg":  "#0a1220",
            "blue":      "#4f8ef7",
            "gold":      "#f59e0b",
            "purple":    "#a78bfa",
            "green":     "#22c55e",
            "red":       "#ef4444",
            "fg":        "#e2e8f0",
            "fg2":       "#94a3b8",
            "fg3":       "#475569",
            "fg4":       "#1e3a5f",
            "border":    "#1e2d4a",
            "divider":   "#0d1628",
            "log_tx_fg": "#7eb8f7",
            "log_dt_fg": "#86efac",
            "log_bg":    "#060c1a",
        }
        F = {
            "title":     ("Segoe UI", 18, "bold"),
            "subtitle":  ("Segoe UI", 10),
            "body":      ("Segoe UI", 10),
            "body_b":    ("Segoe UI", 10, "bold"),
            "caption":   ("Segoe UI", 9),
            "caption_b": ("Segoe UI", 9, "bold"),
            "btn_main":  ("Segoe UI", 13, "bold"),
            "verse":     ("Segoe UI", 13),
            "verse_ref": ("Segoe UI", 11, "bold"),
            "log":       ("Consolas", 9),
            "status":    ("Segoe UI", 10, "bold"),
            "mono_key":  ("Consolas", 9),
        }
        self._C = C
        self._F = F

        # ── TTK STYLE ────────────────────────────────────────────────
        _style = ttk.Style()
        _style.theme_use("clam")
        _style.configure("TCombobox",
            fieldbackground=C["input_bg"], background=C["card"],
            foreground=C["fg"], selectbackground=C["card_hi"],
            selectforeground=C["fg"], bordercolor=C["border"],
            darkcolor=C["card"], lightcolor=C["card"],
            arrowcolor=C["fg2"], relief="flat", padding=4)
        _style.map("TCombobox",
            fieldbackground=[("readonly", C["input_bg"])],
            foreground=[("readonly", C["fg"])],
            selectbackground=[("readonly", C["card_hi"])])
        _style.configure("TScrollbar",
            background=C["card"], troughcolor=C["bg"],
            bordercolor=C["card"], arrowcolor=C["fg3"], relief="flat")

        # ── NESTED HELPERS ───────────────────────────────────────────
        def _make_card(parent, fill="x", padx=0, pady=(0, 8),
                       padx_inner=14, pady_inner=10, bg=None, expand=False):
            bg_ = bg or C["card"]
            outer = tk.Frame(parent, bg=bg_,
                             highlightbackground=C["border"],
                             highlightthickness=1, relief="flat")
            outer.pack(fill=fill, padx=padx, pady=pady, expand=expand)
            inner = tk.Frame(outer, bg=bg_, padx=padx_inner, pady=pady_inner)
            inner.pack(fill="both", expand=True)
            return inner

        def _dot(parent, color=None, size=9, bg=None):
            color = color or C["fg3"]
            bg_ = bg or parent.cget("bg")
            cv = tk.Canvas(parent, width=size, height=size,
                           bg=bg_, highlightthickness=0)
            cv.create_oval(1, 1, size-1, size-1, fill=color, outline="")
            return cv

        def _section_hdr(parent, text, dot_color=None, bg=None, fg=None):
            bg_ = bg or C["card"]
            fg_ = fg or C["fg2"]
            row = tk.Frame(parent, bg=bg_)
            row.pack(fill="x", pady=(0, 4))
            dot = None
            if dot_color is not None:
                dot = _dot(row, dot_color, bg=bg_)
                dot.pack(side="left", padx=(0, 5), pady=1)
            tk.Label(row, text=text.upper(), font=F["caption_b"],
                     bg=bg_, fg=fg_).pack(side="left")
            tk.Frame(parent, bg=C["divider"], height=1).pack(fill="x", pady=(0, 8))
            return dot

        def _field_group(parent, label, bg=None):
            bg_ = bg or C["card"]
            f = tk.Frame(parent, bg=bg_)
            tk.Label(f, text=label, font=F["caption"],
                     bg=bg_, fg=C["fg2"]).pack(anchor="w", pady=(0, 2))
            return f

        # ── WINDOW ───────────────────────────────────────────────────
        self.root.configure(bg=C["bg"])
        self.root.minsize(960, 660)

        # ════════════════════════════════════════════════════════════
        # HEADER
        # ════════════════════════════════════════════════════════════
        hdr = tk.Frame(self.root, bg=C["header"])
        hdr.pack(fill="x", ipady=12)
        try:
            import base64 as _b64, io as _io
            from PIL import Image, ImageTk
            _logo_data = _b64.b64decode(self.__class__._LOGO_B64)
            _img = Image.open(_io.BytesIO(_logo_data)).convert("RGBA")
            _img = _img.resize((48, 48), Image.LANCZOS)
            r, g, b, _a = _img.split()
            lum = Image.blend(Image.blend(r, g, 0.5), b, 0.33)
            _final = Image.new("RGBA", _img.size, (13, 24, 48, 255))
            _white = Image.new("RGBA", _img.size, (255, 255, 255, 255))
            _final.paste(_white, mask=lum)
            _tk_img = ImageTk.PhotoImage(_final)
            self._logo_ref = _tk_img
            tk.Label(hdr, image=_tk_img, bg=C["header"]).pack(side="left", padx=(18, 8))
        except Exception:
            tk.Label(hdr, text="\u271d", font=("Segoe UI", 20, "bold"),
                     bg=C["header"], fg=C["gold"]).pack(side="left", padx=(18, 8))
        tf = tk.Frame(hdr, bg=C["header"])
        tf.pack(side="left", pady=4)
        tk.Label(tf, text="BibleCue", font=F["title"],
                 bg=C["header"], fg=C["fg"]).pack(anchor="w")
        tk.Label(tf, text="Live Bible Verse Display", font=F["subtitle"],
                 bg=C["header"], fg=C["fg3"]).pack(anchor="w")
        hdr_right = tk.Frame(hdr, bg=C["header"])
        hdr_right.pack(side="right", padx=(0, 20))
        self._status_dot = _dot(hdr_right, C["green"], size=10, bg=C["header"])
        self._status_dot.pack(side="left", padx=(0, 6), pady=2)
        self.status_lbl = tk.Label(hdr_right, text="Ready", font=F["status"],
                                    bg=C["header"], fg=C["green"])
        self.status_lbl.pack(side="left")
        tk.Frame(self.root, bg=C["gold"], height=2).pack(fill="x")

        # ════════════════════════════════════════════════════════════
        # TWO-COLUMN BODY
        # ════════════════════════════════════════════════════════════
        body = tk.Frame(self.root, bg=C["bg"])
        body.pack(fill="both", expand=True)

        # Scrollable sidebar wrapper
        sidebar_outer = tk.Frame(body, bg=C["sidebar"], width=285) # Slightly wider
        sidebar_outer.pack(side="left", fill="y")
        sidebar_outer.pack_propagate(False)
        sidebar_canvas = tk.Canvas(sidebar_outer, bg=C["sidebar"],
                                   highlightthickness=0, bd=0)
        sidebar_canvas.pack(side="left", fill="both", expand=True)
        _sb = tk.Scrollbar(sidebar_outer, orient="vertical",
                           command=sidebar_canvas.yview,
                           bg=C["sidebar"], troughcolor=C["bg"],
                           activebackground=C["border"], relief="flat",
                           width=10, bd=0) # Wider scrollbar
        _sb.pack(side="right", fill="y")
        sidebar_canvas.configure(yscrollcommand=_sb.set)
        sidebar = tk.Frame(sidebar_canvas, bg=C["sidebar"])
        _sb_win = sidebar_canvas.create_window((0, 0), window=sidebar, anchor="nw")
        
        def _sync_sidebar(e=None):
            # Force update of idle tasks to get correct bbox
            sidebar.update_idletasks()
            sidebar_canvas.configure(scrollregion=sidebar_canvas.bbox("all"))
            
        def _resize_sidebar_win(e):
            sidebar_canvas.itemconfig(_sb_win, width=e.width)
            
        sidebar.bind("<Configure>", _sync_sidebar)
        sidebar_canvas.bind("<Configure>", _resize_sidebar_win)
        def _on_sidebar_scroll(e):
            sidebar_canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
        sidebar_canvas.bind("<MouseWheel>", _on_sidebar_scroll)
        sidebar.bind("<MouseWheel>", _on_sidebar_scroll)
        tk.Frame(body, bg=C["border"], width=1).pack(side="left", fill="y")
        main = tk.Frame(body, bg=C["bg"])
        main.pack(side="left", fill="both", expand=True)

        # ════════════════════════════════════════════════════════════
        # SIDEBAR: MODE SELECTOR
        # ════════════════════════════════════════════════════════════
        mode_card = _make_card(sidebar, fill="x", padx=10, pady=(10, 6),
                               padx_inner=12, pady_inner=10)
        _section_hdr(mode_card, "Transcription Mode",
                     dot_color=C["fg3"], bg=C["card"])
        self.mode_var = tk.StringVar(value=self.settings.get("mode", "google"))
        for val, icon, label in [
            ("google",   "\U0001f310", "Google Speech  (browser)"),
            ("deepgram", "\u26a1", "Deepgram  (real-time)"),
        ]:
            rb_row = tk.Frame(mode_card, bg=C["card"])
            rb_row.pack(fill="x", pady=2)
            tk.Radiobutton(
                rb_row, text=f"{icon}  {label}",
                variable=self.mode_var, value=val,
                command=self._on_mode_change,
                bg=C["card"], fg=C["fg"],
                selectcolor=C["card_hi"],
                activebackground=C["card"],
                activeforeground=C["fg"],
                font=F["body_b"],
            ).pack(side="left")
        self.mode_status = tk.Label(mode_card, text="",
                                     font=F["caption"], bg=C["card"], fg=C["fg3"])
        self.mode_status.pack(anchor="w", pady=(4, 0))

        # ════════════════════════════════════════════════════════════
        # SIDEBAR: PROPRESENTER CONNECTION
        # ════════════════════════════════════════════════════════════
        pro_card = _make_card(sidebar, fill="x", padx=10, pady=(0, 6),
                              padx_inner=12, pady_inner=10)
        pro_hdr_row = tk.Frame(pro_card, bg=C["card"])
        pro_hdr_row.pack(fill="x", pady=(0, 4))
        self._pro_dot = _dot(pro_hdr_row, C["fg3"], bg=C["card"])
        self._pro_dot.pack(side="left", padx=(0, 5), pady=1)
        tk.Label(pro_hdr_row, text="PROPRESENTER", font=F["caption_b"],
                 bg=C["card"], fg=C["blue"]).pack(side="left")
        tk.Frame(pro_card, bg=C["divider"], height=1).pack(fill="x", pady=(0, 8))

        row1 = tk.Frame(pro_card, bg=C["card"])
        row1.pack(fill="x", pady=(0, 6))
        ip_f = _field_group(row1, "IP Address", bg=C["card"])
        ip_f.pack(side="left", padx=(0, 8), fill="x", expand=True)
        self.ip_var = tk.StringVar(value=self.settings["pro_ip"])
        self._ent(ip_f, self.ip_var, width=14).pack(fill="x")
        port_f = _field_group(row1, "Port", bg=C["card"])
        port_f.pack(side="left")
        self.port_var = tk.StringVar(value=self.settings["pro_port"])
        self._ent(port_f, self.port_var, width=6).pack()

        trans_f = _field_group(pro_card, "Bible Translation", bg=C["card"])
        trans_f.pack(fill="x", pady=(0, 6))
        self.trans_var = tk.StringVar(value=self.settings["translation"])
        ttk.Combobox(trans_f, textvariable=self.trans_var,
                     values=["KJV","WEB","ASV","BBE","DARBY","YLT"],
                     width=10, state="readonly").pack(anchor="w")

        uuid_f = _field_group(pro_card, "Message UUID", bg=C["card"])
        uuid_f.pack(fill="x", pady=(0, 6))
        self.uuid_var = tk.StringVar(value=self.settings["message_uuid"])
        self._ent(uuid_f, self.uuid_var, width=28).pack(fill="x")

        cool_f = _field_group(pro_card, "Cooldown (seconds)", bg=C["card"])
        cool_f.pack(fill="x", pady=(0, 4))
        self.cooldown_var = tk.StringVar(value=str(self.settings["cooldown_secs"]))
        self._ent(cool_f, self.cooldown_var, width=6).pack(anchor="w")

        self.save_lbl = tk.Label(pro_card, text="", font=F["caption"],
                                  bg=C["card"], fg=C["green"])
        self.save_lbl.pack(anchor="e", pady=(4, 0))

        # ════════════════════════════════════════════════════════════
        # SIDEBAR: COLLAPSIBLE ADVANCED SETTINGS
        # ════════════════════════════════════════════════════════════
        self._advanced_open = tk.BooleanVar(value=False)
        adv_hdr = tk.Frame(sidebar, bg=C["sidebar"], cursor="hand2")
        adv_hdr.pack(fill="x", padx=10)
        adv_hdr_inner = tk.Frame(adv_hdr, bg=C["sidebar"], padx=12, pady=7)
        adv_hdr_inner.pack(fill="x")
        self._adv_arrow = tk.Label(adv_hdr_inner, text="\u25b8",
                                    font=F["caption_b"], bg=C["sidebar"],
                                    fg=C["blue"], cursor="hand2")
        self._adv_arrow.pack(side="left", padx=(0, 6))
        tk.Label(adv_hdr_inner, text="Advanced Settings",
                 font=F["caption_b"], bg=C["sidebar"], fg=C["fg2"],
                 cursor="hand2").pack(side="left")
        tk.Frame(sidebar, bg=C["border"], height=1).pack(fill="x", padx=10)
        self._adv_body = tk.Frame(sidebar, bg=C["card"])

        def _toggle_adv(e=None):
            if self._advanced_open.get():
                self._adv_body.pack_forget()
                self._advanced_open.set(False)
                self._adv_arrow.config(text="\u25b8")
            else:
                self._adv_body.pack(fill="x", padx=10, pady=(0, 6))
                self._advanced_open.set(True)
                self._adv_arrow.config(text="\u25be")

        adv_hdr.bind("<Button-1>", _toggle_adv)
        adv_hdr_inner.bind("<Button-1>", _toggle_adv)
        for w in adv_hdr_inner.winfo_children():
            w.bind("<Button-1>", _toggle_adv)

        # ── Deepgram key ──────────────────────────────────────
        dg_sec = tk.Frame(self._adv_body, bg=C["card"], padx=12, pady=10)
        dg_sec.pack(fill="x")
        dg_top = tk.Frame(dg_sec, bg=C["card"])
        dg_top.pack(fill="x", pady=(0, 6))
        tk.Label(dg_top, text="\u26a1  Deepgram API Key",
                 font=F["body_b"], bg=C["card"], fg=C["purple"]).pack(side="left")
        tk.Label(dg_top, text="console.deepgram.com",
                 font=F["caption"], bg=C["card"], fg=C["fg3"]).pack(side="right")
        dg_row = tk.Frame(dg_sec, bg=C["card"])
        dg_row.pack(fill="x")
        tk.Label(dg_row, text="Key:", font=F["caption"],
                 bg=C["card"], fg=C["fg2"]).pack(side="left")
        self.dg_key_var = tk.StringVar(value=self.settings.get("deepgram_key", ""))
        self._dge = self._ent(dg_row, self.dg_key_var, width=20, font=F["mono_key"])
        self._dge.pack(side="left", padx=(6, 8), fill="x", expand=True)
        self._dge.config(show="*")
        self._show_key = tk.BooleanVar()
        tk.Checkbutton(dg_row, text="Show", variable=self._show_key,
                       command=self._toggle_key_show,
                       bg=C["card"], fg=C["fg2"], selectcolor=C["input_bg"],
                       activebackground=C["card"],
                       font=F["caption"]).pack(side="left")
        self.dg_status_lbl = tk.Label(dg_sec, text="",
                                       font=F["caption"], bg=C["card"], fg=C["purple"])
        self.dg_status_lbl.pack(anchor="w", pady=(4, 0))
        self.dg_frame = dg_sec
        tk.Frame(self._adv_body, bg=C["divider"], height=1).pack(fill="x")

        # ── Audio Device ──────────────────────────────────────
        dev_sec = tk.Frame(self._adv_body, bg=C["card"], padx=12, pady=10)
        dev_sec.pack(fill="x")
        tk.Label(dev_sec, text="\U0001f3a4  Input Device",
                 font=F["body_b"], bg=C["card"], fg=C["gold"]).pack(
            anchor="w", pady=(0, 6))
        dr = tk.Frame(dev_sec, bg=C["card"])
        dr.pack(fill="x")
        
        device_list = ["Default"]
        self._device_map = {-1: "Default"}
        if AUDIO_AVAILABLE:
            try:
                import sounddevice as sd
                devices = sd.query_devices()
                for i, d in enumerate(devices):
                    if d.get('max_input_channels', 0) > 0:
                        name = f"{i}: {d.get('name', 'Unknown')[:30]}"
                        device_list.append(name)
                        self._device_map[i] = name
            except Exception: pass

        self.device_var = tk.StringVar(value="Default")
        # Set initial value from settings
        curr_dev = self.settings.get("audio_device", -1)
        if curr_dev in self._device_map:
            self.device_var.set(self._device_map[curr_dev])

        self._dev_cb = ttk.Combobox(dr, textvariable=self.device_var,
                     values=device_list,
                     width=25, state="readonly")
        self._dev_cb.pack(side="left", padx=(0, 12))
        self._dev_cb.bind("<<ComboboxSelected>>", self._on_device_select)
        
        self.dev_status = tk.Label(dr, text="", font=F["caption"], bg=C["card"], fg=C["fg3"])
        self.dev_status.pack(side="left")
        tk.Frame(self._adv_body, bg=C["divider"], height=1).pack(fill="x")

        # ── Manual verse ──────────────────────────────────────
        man_sec = tk.Frame(self._adv_body, bg=C["card"], padx=12, pady=10)
        man_sec.pack(fill="x")
        tk.Label(man_sec, text="Send Verse Manually",
                 font=F["body_b"], bg=C["card"], fg=C["blue"]).pack(
            anchor="w", pady=(0, 6))
        mr = tk.Frame(man_sec, bg=C["card"])
        mr.pack(fill="x")
        self.manual_var = tk.StringVar()
        me = self._ent(mr, self.manual_var, width=20, font=F["body"])
        me.pack(side="left", padx=(0, 8), fill="x", expand=True, ipady=4)
        me.bind("<Return>", lambda e: self._manual_send())
        tk.Button(mr, text="Send \u2192", command=self._manual_send,
                  bg=C["blue"], fg="white", font=F["body_b"],
                  relief="flat", padx=10, pady=4, cursor="hand2",
                  activebackground=C["card_hi"],
                  activeforeground="white").pack(side="left")
        tk.Label(man_sec, text="e.g.  John 3:16  \u00b7  Psalm 23  \u00b7  next verse",
                 font=F["caption"], bg=C["card"], fg=C["fg3"]).pack(
            anchor="w", pady=(4, 0))

        # ════════════════════════════════════════════════════════════
        # SIDEBAR: START / STOP LISTENING
        # ════════════════════════════════════════════════════════════
        ctrl_card = _make_card(sidebar, fill="x", padx=10, pady=(6, 6),
                               padx_inner=12, pady_inner=12)
        
        # Deepgram status moved here so it is always visible
        self.dg_status_lbl = tk.Label(ctrl_card, text="",
                                       font=F["caption"], bg=C["card"], fg=C["purple"])
        self.dg_status_lbl.pack(anchor="w", pady=(0, 6))

        pulse_row = tk.Frame(ctrl_card, bg=C["card"])
        pulse_row.pack(fill="x", pady=(0, 8))
        self._pulse_dot = tk.Canvas(pulse_row, width=12, height=12,
                                     bg=C["card"], highlightthickness=0)
        self._pulse_dot_id = self._pulse_dot.create_oval(
            2, 2, 10, 10, fill=C["fg3"], outline="")
        self._pulse_dot.pack(side="left", padx=(0, 6))
        self.listen_status = tk.Label(pulse_row, text="Not listening",
                                       font=F["caption_b"], bg=C["card"], fg=C["fg3"])
        self.listen_status.pack(side="left")
        self.start_btn = tk.Button(
            ctrl_card,
            text="\u25b6  Start Listening",
            command=self._toggle_listening,
            bg=C["green"], fg="white",
            font=F["btn_main"],
            relief="flat", padx=0, pady=11,
            cursor="hand2",
            activebackground="#16a34a",
            activeforeground="white")
        self.start_btn.pack(fill="x")
        auto_f = tk.Frame(ctrl_card, bg=C["card"])
        auto_f.pack(fill="x", pady=(8, 0))
        self._autostart_var = tk.BooleanVar(
            value=bool(self.settings.get("autostart", False)))
        tk.Checkbutton(auto_f, text="Auto-start on open",
                       variable=self._autostart_var,
                       command=self._on_setting_change,
                       bg=C["card"], fg=C["fg2"],
                       selectcolor=C["input_bg"],
                       activebackground=C["card"],
                       font=F["caption"]).pack(anchor="w")

        # ════════════════════════════════════════════════════════════
        # MAIN: NOW ON SCREEN
        # ════════════════════════════════════════════════════════════
        scr_outer = tk.Frame(main, bg=C["bg"],
                             highlightbackground=C["gold"],
                             highlightthickness=1)
        scr_outer.pack(fill="x", padx=14, pady=(14, 8))
        scr = tk.Frame(scr_outer, bg=C["bg"], padx=16, pady=14)
        scr.pack(fill="both", expand=True)
        scr_hdr_row = tk.Frame(scr, bg=C["bg"])
        scr_hdr_row.pack(fill="x", pady=(0, 6))
        tk.Label(scr_hdr_row, text="NOW ON SCREEN",
                 font=F["caption_b"], bg=C["bg"], fg=C["gold"]).pack(side="left")
        tk.Label(scr_hdr_row,
                 text="next  \u00b7  previous  \u00b7  verse 5  \u00b7  next chapter",
                 font=F["caption"], bg=C["bg"], fg=C["fg4"]).pack(side="right")
        self.verse_lbl = tk.Label(scr, text="\u2014", wraplength=580,
                                   justify="left", bg=C["bg"], fg=C["fg"],
                                   font=F["verse"], pady=4)
        self.verse_lbl.pack(anchor="w", fill="x")
        self.ref_lbl = tk.Label(scr, text="", bg=C["bg"], fg=C["gold"],
                                 font=F["verse_ref"], pady=2)
        self.ref_lbl.pack(anchor="w")

        def _on_main_resize(event):
            new_wrap = max(200, event.width - 40)
            self.verse_lbl.configure(wraplength=new_wrap)
        scr_outer.bind("<Configure>", _on_main_resize)

        # ════════════════════════════════════════════════════════════
        # MAIN: DUAL LOG PANELS
        # ════════════════════════════════════════════════════════════
        log_outer = tk.Frame(main, bg=C["bg"])
        log_outer.pack(fill="both", expand=True, padx=14, pady=(0, 12))
        for _side, attr, label, icon, accent, border_c in [
            ("left",  "t_log", "Live Transcript",
             "\U0001f3a4", C["log_tx_fg"], C["blue"]),
            ("right", "d_log", "Scripture Detection",
             "\U0001f4d6", C["log_dt_fg"], C["green"]),
        ]:
            col = tk.Frame(log_outer, bg=C["bg"])
            col_pad = (0, 6) if _side == "left" else (6, 0)
            col.pack(side="left", fill="both", expand=True, padx=col_pad)
            panel_outer = tk.Frame(col, bg=C["card"],
                                   highlightbackground=border_c,
                                   highlightthickness=1)
            panel_outer.pack(fill="both", expand=True)
            panel_hdr = tk.Frame(panel_outer, bg=C["card_hi"], padx=10, pady=6)
            panel_hdr.pack(fill="x")
            tk.Label(panel_hdr, text=f"{icon}  {label}",
                     font=F["caption_b"], bg=C["card_hi"],
                     fg=accent).pack(side="left")
            tk.Frame(panel_outer, bg=C["divider"], height=1).pack(fill="x")
            log = scrolledtext.ScrolledText(
                panel_outer, height=10, state="disabled",
                bg=C["log_bg"], fg=accent,
                font=F["log"], relief="flat",
                wrap="word", padx=8, pady=6,
                insertbackground=accent,
                selectbackground=C["card_hi"])
            log.pack(fill="both", expand=True)
            setattr(self, attr, log)

    def _pulse_animation(self):
        """Animate the listening dot by toggling between bright and dim green."""
        if not getattr(self, "_pulse_running", False):
            return
        current = self._pulse_dot.itemcget(self._pulse_dot_id, "fill")
        bright, dim = "#22c55e", "#166534"
        next_color = dim if current == bright else bright
        self._pulse_dot.itemconfig(self._pulse_dot_id, fill=next_color)
        self.root.after(600, self._pulse_animation)

    def _toggle_key_show(self):
        """Show Deepgram key only after admin password verification."""
        if self._show_key.get():
            pw = _simpledialog.askstring(
                "Authentication", "Enter password to reveal API key:",
                show="*", parent=self.root)
            if pw != "admin":
                self._show_key.set(False)
                if pw is not None:   # None = cancelled
                    messagebox.showerror("Incorrect", "Wrong password.", parent=self.root)
                return
            self._dge.config(show="")
        else:
            self._dge.config(show="*")

    # ── AUTO-SAVE ────────────────────────────────────────────
    def _bind_autosave(self):
        for v in [self.ip_var, self.port_var, self.trans_var,
                  self.uuid_var, self.cooldown_var,
                  self.dg_key_var]:
            v.trace_add("write", self._on_setting_change)

    def _on_setting_change(self, *args):
        if hasattr(self, '_save_id'):
            self.root.after_cancel(self._save_id)
        self._save_id = self.root.after(800, self._autosave)

    def _on_mode_change(self, *args):
        self._on_setting_change()
        # Stop any running listener when mode changes
        was_listening = getattr(self, '_listening', False)
        self._stop_all_listeners()
        if was_listening:
            # Restart in the newly selected mode
            self.root.after(200, self._start_selected_mode)

    def _autosave(self):
        try:
            self.settings.update({
                "pro_ip":        self.ip_var.get().strip(),
                "pro_port":      self.port_var.get().strip(),
                "message_uuid":  self.uuid_var.get().strip(),
                "translation":   self.trans_var.get(),
                "mode":          self.mode_var.get(),
                "deepgram_key":  self.dg_key_var.get().strip(),
                "autostart":     bool(self._autostart_var.get()),
                "cooldown_secs": int(self.cooldown_var.get() or 12),
            })
            save_settings(self.settings)
            self.root.after(0, lambda: self.save_lbl.configure(text="✓ Saved"))
            self.root.after(2500, lambda: self.save_lbl.configure(text=""))
        except Exception:
            pass

    # ── HELPERS ─────────────────────────────────────────────
    def _logt(self, msg):
        def _do():
            self.t_log.configure(state="normal")
            self.t_log.insert("end", msg + "\n")
            self.t_log.see("end")
            self.t_log.configure(state="disabled")
        self.root.after(0, _do)

    def _logd(self, msg):
        def _do():
            self.d_log.configure(state="normal")
            self.d_log.insert("end", msg + "\n")
            self.d_log.see("end")
            self.d_log.configure(state="disabled")
        self.root.after(0, _do)

    def _set_status(self, t, c="#4ade80"):
        def _do():
            self.status_lbl.configure(text=t, fg=c)
            if hasattr(self, "_status_dot"):
                self._status_dot.itemconfig(1, fill=c)
        self.root.after(0, _do)

    def _update_display(self, vtext, vref, trans):
        self.root.after(0, lambda: (
            self.verse_lbl.configure(
                text=vtext[:250] + ("…" if len(vtext) > 250 else "")),
            self.ref_lbl.configure(text=f"{vref}  ({trans})")
        ))

    # ── WEBSOCKET SERVER ─────────────────────────────────────
    def _start_ws_server(self):
        if not WEBSOCKETS_AVAILABLE:
            self._logt("websockets not installed - run: pip install websockets")
            return
        ws_port = int(self.settings.get("ws_port", 8765))
        self._ws_server = WSServer(ws_port, self._on_transcript, self._on_interim)
        self._ws_server.start()                          # resolves actual port
        html = get_listener_html(self._ws_server.port)  # use resolved port in HTML
        self._http_port = start_http_server(html, 8766)
        self._logd("Ready - select mode and open browser or start Deepgram")

    def _open_browser(self):
        url = f"http://127.0.0.1:{self._http_port or 8766}"
        try:
            webbrowser.open(url)
            self._logd(f"Browser opened -> click the mic button there")
        except Exception as e:
            self._logd(f"Could not open browser: {e}")
            self._logd(f"Manually open: {url}")

    def _stop_all_listeners(self):
        if self._deepgram:
            self._deepgram.stop()
            self._deepgram = None

    def _start_selected_mode(self):
        mode = self.mode_var.get()
        if mode not in ("google", "deepgram"):
            mode = "google"
        self._stop_all_listeners()

        if mode == "google":
            self._logd("--- Google Speech mode ---")
            self._logd("Uses Chrome/Edge browser microphone.")
            self._logd("Works with any audio interface including Focusrite.")
            self._open_browser()
            return

        elif mode == "deepgram":
            self._logd("--- Deepgram mode (browser audio) ---")
            key = self.dg_key_var.get().strip()
            if not key:
                self._logd("No API key — get free at console.deepgram.com")
                return
            self._deepgram = DeepgramListener(
                key, self._on_transcript, self._set_dg_status,
                on_interim=self._on_interim)
            dg = self._deepgram
            if self._ws_server:
                self._ws_server.set_audio_callback(
                    lambda chunk: dg._audio_q.put_nowait(chunk)
                    if dg and dg.running else None)
            self._deepgram.start()
            self._open_browser()
            delay = 1200 if getattr(self, "_browser_open", False) else 3500
            def _act(ws=self._ws_server, d=self._deepgram):
                if ws and d and d.running:
                    ws.broadcast({"type":"dg_mode","active":True})
                    self._logd("DG audio streaming activated")
            self.root.after(delay, _act)

    def _set_dg_status(self, msg):
        self.root.after(0, lambda: self.dg_status_lbl.configure(text=msg))

    def _on_device_select(self, event=None):
        selection = self.device_var.get()
        device_id = -1
        for i, name in self._device_map.items():
            if name == selection:
                device_id = i
                break
        
        self.settings["audio_device"] = device_id
        self._autosave()
        self.dev_status.configure(text="✓ Updated (restart listening)", fg=self._C["green"])
        self.root.after(3000, lambda: self.dev_status.configure(text=""))

    # ── TOGGLE LISTENING ────────────────────────────────────
    def _toggle_listening(self):
        C = getattr(self, "_C", {"green": "#22c55e", "red": "#ef4444", "fg3": "#475569"})
        if not hasattr(self, '_listening') or not self._listening:
            self._listening = True
            self.start_btn.configure(text="■  Stop Listening", bg=C["red"])
            self.root.after(0, lambda: self.listen_status.configure(
                text="Live", fg=C["green"]))
            self._pulse_running = True
            self._pulse_animation()
            self._start_selected_mode()
        else:
            self._listening = False
            self._browser_open = False
            self.start_btn.configure(text="▶  Start Listening", bg=C["green"])
            self.root.after(0, lambda: self.listen_status.configure(
                text="Not listening", fg=C["fg3"]))
            self._pulse_running = False
            self._pulse_dot.itemconfig(self._pulse_dot_id, fill=C["fg3"])
            self._stop_all_listeners()
            self._logd("Stopped.")

    # ── ON INTERIM ───────────────────────────────────────────
    def _on_interim(self, text: str):
        """Interim result — show live in transcript AND run scripture detection.
        Detection on interim means the verse fires while the pastor is still
        speaking, without waiting for a pause. Cooldown prevents duplicates."""
        # ── Update transcript panel ──
        def _do():
            self.t_log.configure(state="normal")
            try:
                last_line = self.t_log.get("end-2l linestart", "end-1l lineend")
                if last_line.startswith(">>"):
                    self.t_log.delete("end-2l linestart", "end-1c")
                    self.t_log.insert("end", f">> {text}\n")
                else:
                    self.t_log.insert("end", f">> {text}\n")
            except Exception:
                self.t_log.insert("end", f">> {text}\n")
            self.t_log.see("end")
            self.t_log.configure(state="disabled")
        self.root.after(0, _do)
        # ── Scripture detection on interim only ─────────────
        # Navigation is intentionally NOT run on interim — "next" could be
        # part of "next week" or "next chapter", and chaining nav on each
        # interim chunk causes double/triple verse skipping.
        # Nav runs only on the confirmed final result in _on_transcript.
        if _has_verse_number(text):
            refs = parse_scripture(text)
            if refs:
                self._process_refs(refs)

    # ── ON TRANSCRIPT ────────────────────────────────────────
    def _on_transcript(self, text: str):
        """Final result — update transcript panel only.
        Detection already ran on the interim version of this text."""
        def _do():
            self.t_log.configure(state="normal")
            try:
                last_line = self.t_log.get("end-2l linestart", "end-1l lineend")
                if last_line.startswith(">>"):
                    self.t_log.delete("end-2l linestart", "end-1c")
                    self.t_log.insert("end", f"  {text}\n")
                else:
                    self.t_log.insert("end", f"  {text}\n")
            except Exception:
                self.t_log.insert("end", f"  {text}\n")
            self.t_log.see("end")
            self.t_log.configure(state="disabled")
        self.root.after(0, _do)
        # Navigation: runs on final result only — complete phrase confirmed.
        # This prevents "next" (interim) chaining before "verse" is spoken.
        nav_ref = _detect_navigation(text, self._cur_book, self._cur_chap, self._cur_verse)
        if nav_ref:
            self._process_refs([nav_ref], nav=True)
            return   # nav took it — skip scripture parse
        # Scripture fallback for finals with no interim and any missed interims
        refs = parse_scripture(text)
        if refs:
            self._process_refs(refs)

    # ── MANUAL ──────────────────────────────────────────────
    def _manual_send(self):
        t = self.manual_var.get().strip()
        if not t:
            return
        self.manual_var.set("")
        refs = parse_scripture(t)
        if not refs:
            self._logd(f"❓ Not found: {t}")
            return
        self._process_refs(refs)

    # ── PROCESS ─────────────────────────────────────────────
    def _process_refs(self, refs: list, nav: bool = False):
        """Atomic check-and-set under lock, then push to fetch queue.
        nav=True uses a short 1.5s cooldown so verse navigation feels instant.
        Scripture detection uses the user-configured cooldown (default 8s)."""
        cooldown = 1.5 if nav else float(self.settings.get("cooldown_secs", 8))
        now = time.time()
        with self._fired_lock:
            for book, chap, verse in refs:
                key = f"{book}{chap}{verse}"
                if now - self.last_fired.get(key, 0) < cooldown:
                    continue
                self.last_fired[key] = now
                self._logd(f"{'Nav' if nav else 'Detected'}: {book} {chap}:{verse}")
                self._fetch_q.put((book, chap, verse))

    def _fetch_worker(self):
        """Persistent worker — drains fetch queue immediately, no spawn overhead."""
        while True:
            book, chap, verse = self._fetch_q.get()
            self._fetch_and_send(book, chap, verse)

    # ── FETCH + SEND ─────────────────────────────────────────
    def _fetch_and_send(self, book: str, chap: int, verse: int):
        trans = self.trans_var.get()
        vtext, vref = fetch_verse(book, chap, verse, trans)
        if not vtext:
            self._logd(f"⚠️  Not found: {book} {chap}:{verse}")
            return
        ok = self._send_to_pro(vtext, vref, trans)
        if ok:
            # Remember this as the current verse for navigation
            self._cur_book  = book
            self._cur_chap  = chap
            self._cur_verse = verse
            self._update_display(vtext, vref, trans)

    # ── PROPRESENTER ─────────────────────────────────────────
    def _send_to_pro(self, verse_text: str, reference: str, trans: str) -> bool:
        uuid = self.uuid_var.get().strip()
        ip   = self.ip_var.get().strip()
        port = self.port_var.get().strip()
        url  = f"http://{ip}:{port}/v1/message/{uuid}/trigger"
        payload = [
            {"name": "{{VerseText}}", "text": {"text": verse_text}},
            {"name": "{{Reference}}",  "text": {"text": f"{reference} ({trans})"}}
        ]
        try:
            # Use persistent session + tight timeout for minimum latency
            r = self._pro_session.post(url, json=payload, timeout=2)
            if r.status_code in (200, 204):
                self._logd(f"🚀 ON SCREEN → {reference} ({trans})")
                return True
            self._logd(f"⚠️  HTTP {r.status_code}")
            return False
        except requests.exceptions.ConnectionError:
            self._logd("❌ Cannot reach ProPresenter")
            # Reset session on connection error so next attempt reconnects cleanly
            self._pro_session = requests.Session()
            return False
        except Exception as e:
            self._logd(f"❌ {e}")
            return False


# ═══════════════════════════════════════════════════════════════
#  HEADLESSAPP  (~line 2255)  — PRIMARY ELECTRON BACKEND
#  This is the class that runs when BibleCue is launched normally.
#  It starts a WebSocket server that the Electron renderer connects
#  to, handles all messages from the frontend, detects scripture,
#  fetches verses, and fires output plugins.
#
#  Key methods:
#    handler()          — processes every WebSocket message
#    _on_transcript()   — called when STT produces a final result
#    _fetch_and_send()  — detects scripture + fetches verse + fires outputs
#    _fire_all_outputs()— calls fire_outputs() in output_plugins.py
#
#  To add a new WebSocket message type, add a case in handler().
#  To change what happens when a verse is detected, edit _fetch_and_send().
# ═══════════════════════════════════════════════════════════════
class HeadlessApp:
    """WebSocket control server for the Electron frontend.
    Implements the same protocol as ProBibleApp but without any tkinter UI."""

    def __init__(self):
        self.settings       = load_settings()
        self._clients       = set()       # connected Electron WS clients
        self._electron_loop = None        # asyncio loop for the control server
        self._listening     = False
        self._deepgram      = None
        self._ws_server     = None        # browser transcript WS server
        self._http_port     = None
        self._browser_opened = False   # only open browser once per session
        self._listener_url  = None    # http://127.0.0.1:<port> for Google Speech
        self._fetch_q       = queue.Queue()
        self._fired_lock    = threading.Lock()
        self.last_fired     = {}
        self._last_nav_time: float = 0.0
        self._cur_lock      = threading.Lock()
        self._cur_book      = None
        self._cur_chap      = None
        self._cur_verse     = None
        self._interim_detected_ref  = None  # ref tuple detected on last interim
        self._interim_detected_time = 0.0   # time of last interim detection
        self._last_final_text: str = ""  # rolling buffer for split-utterance detection
        self._pro_session   = requests.Session()

        # Start fetch worker thread
        threading.Thread(target=self._fetch_worker, daemon=True).start()

        # Start the browser transcript WS server (for Google Speech browser page)
        self._start_browser_ws()
        self._listener_url = f"http://127.0.0.1:{self._http_port or 8766}"

        # If NDI was left enabled from a previous session, start it now
        _sync_ndi_output(self.settings)

    # ── BROADCAST ──────────────────────────────────────────────
    def broadcast(self, msg: dict):
        """Send a JSON message to all connected Electron clients (thread-safe)."""
        if not self._electron_loop:
            return
        data = json.dumps(msg)
        async def _s():
            for c in list(self._clients):
                try:
                    await c.send(data)
                except Exception:
                    self._clients.discard(c)
        asyncio.run_coroutine_threadsafe(_s(), self._electron_loop)

    # ── LOGGING HELPERS ─────────────────────────────────────────
    def _logt(self, text: str):
        self.broadcast({"type": "log", "panel": "transcript", "text": text})

    def _logd(self, text: str, subtype: str = "info"):
        self.broadcast({"type": "log", "panel": "detection", "text": text, "subtype": subtype})

    def _set_status(self, text: str, color: str = "#4ade80"):
        c = "green" if "4ade" in color or "22c5" in color else \
            "red"   if "ef44" in color or "f871" in color else "gray"
        self.broadcast({"type": "status", "text": text, "color": c})

    def _update_display(self, vtext: str, vref: str, trans: str):
        self.broadcast({"type": "verse_display",
                        "verse_text": vtext, "reference": vref, "translation": trans})

    def _set_dg_status(self, msg: str):
        self.broadcast({"type": "dg_status", "text": msg})

    # ── BROWSER WS SERVER ───────────────────────────────────────
    def _start_browser_ws(self):
        """Start the Google Speech browser transcript listener."""
        if not WEBSOCKETS_AVAILABLE:
            return
        ws_port = int(self.settings.get("ws_port", 8765))
        # Use a different port for the browser server to avoid conflict with control server
        browser_port = ws_port + 2  # e.g. 8767
        self._browser_ws = WSServer(browser_port, self._on_transcript, self._on_interim)
        self._browser_ws.start()
        html = get_listener_html(self._browser_ws.port)
        self._http_port = start_http_server(html, 8766)

    # ── AUDIO DEVICES ───────────────────────────────────────────
    def _get_audio_devices(self):
        devices = [{"id": -1, "name": "Default"}]
        if AUDIO_AVAILABLE:
            try:
                import sounddevice as sd
                for i, d in enumerate(sd.query_devices()):
                    if d["max_input_channels"] > 0:
                        devices.append({"id": i, "name": d["name"]})
            except Exception:
                pass
        return devices

    # ── ELECTRON MESSAGE HANDLER ────────────────────────────────
    async def _handle_msg(self, msg: dict, websocket):
        t = msg.get("type", "")

        if t == "get_settings":
            await websocket.send(json.dumps(
                {"type": "settings_response", "data": self.settings}))

        elif t == "set_settings":
            data = msg.get("data", {})
            self.settings.update(data)
            # Sync propresenter outputs ↔ legacy keys without losing ip/uuid
            for out in self.settings.get("outputs", []):
                if out.get("type") == "propresenter":
                    # If the frontend sent empty ip/uuid (UI not yet populated),
                    # restore from legacy keys so we never overwrite good config.
                    if not out.get("ip", "").strip():
                        out["ip"]   = self.settings.get("pro_ip", "")
                    if not out.get("uuid", "").strip():
                        out["uuid"] = self.settings.get("message_uuid", "")
                    if not out.get("port", "").strip():
                        out["port"] = self.settings.get("pro_port", "1025")
                    # Now write back to legacy keys (only non-empty values)
                    if out.get("ip"):
                        self.settings["pro_ip"]      = out["ip"]
                    if out.get("uuid"):
                        self.settings["message_uuid"] = out["uuid"]
                    if out.get("port"):
                        self.settings["pro_port"]     = out["port"]
                    break
            save_settings(self.settings)
            ndi_status = _sync_ndi_output(self.settings)
            if ndi_status:
                self._logd(ndi_status, subtype="warn" if "⚠️" in ndi_status else "info")
            # Broadcast so any other open window (e.g. the fullscreen display)
            # picks up display/background/font changes immediately, live.
            self.broadcast({"type": "settings_response", "data": self.settings})

        elif t == "get_devices":
            await websocket.send(json.dumps(
                {"type": "devices_response", "list": self._get_audio_devices()}))

        elif t == "start_listening":
            if not self._listening:
                self._listening = True
                self.broadcast({"type": "listening_state", "active": True})
                self._set_status("Listening...", "#4ade80")
                self._start_selected_mode()

        elif t == "stop_listening":
            if self._listening:
                self._listening = False
                self._stop_all_listeners()
                self.broadcast({"type": "listening_state", "active": False})
                self._set_status("Stopped", "#475569")
                self._logd("Stopped.")

        elif t == "manual_verse":
            text = msg.get("text", "").strip()
            if text:
                refs = parse_scripture(text)
                if refs:
                    self._process_refs(refs)
                else:
                    self._logd(f"❓ Not found: {text}")

        elif t == "open_browser":
            url = f"http://127.0.0.1:{self._http_port or 8766}"
            self.broadcast({"type": "open_browser_url", "url": url})
            self._browser_opened = True
            self._logd(f"Browser opened → click the mic button there")

    # ── ELECTRON WS SERVER ──────────────────────────────────────
    async def _electron_serve(self):
        port = int(self.settings.get("ws_port", 8765))

        async def handler(websocket):
            self._clients.add(websocket)
            print(f"[headless] Electron client connected ({len(self._clients)} total)")
            try:
                # Send initial state immediately on connect
                settings_payload = dict(self.settings)
                if self._listener_url:
                    settings_payload["listener_url"] = self._listener_url
                await websocket.send(json.dumps(
                    {"type": "settings_response", "data": settings_payload}))
                await websocket.send(json.dumps(
                    {"type": "listening_state", "active": self._listening}))
                await websocket.send(json.dumps(
                    {"type": "status", "text": "Connected to backend", "color": "green"}))
                await websocket.send(json.dumps(
                    {"type": "devices_response", "list": self._get_audio_devices()}))
                # Send the listener URL for display only — does NOT open the browser
                # (browser opens only when listening actually starts via open_browser_url)
                if self._listener_url:
                    await websocket.send(json.dumps(
                        {"type": "listener_url", "url": self._listener_url}))

                async for message in websocket:
                    try:
                        data = json.loads(message)
                        await self._handle_msg(data, websocket)
                    except Exception as e:
                        import traceback
                        _dbg(f"Message error: {e}\n{traceback.format_exc()}")
                        print(f"[headless] Message error: {e}")
            except Exception as e:
                print(f"[headless] WS error: {e}")
            finally:
                self._clients.discard(websocket)
                print(f"[headless] Electron client disconnected ({len(self._clients)} remaining)")

        print(f"[headless] Electron control server starting on ws://127.0.0.1:{port}")
        async with websockets.serve(handler, "127.0.0.1", port, reuse_address=True):
            await asyncio.Future()  # run forever

    # ── TRANSCRIPTION MODES ─────────────────────────────────────
    def _start_selected_mode(self):
        mode = self.settings.get("mode", "google")
        if mode not in ("google", "deepgram"):
            mode = "google"
        self._stop_all_listeners()

        if mode == "google":
            self._logd("--- Google Speech mode ---")
            self._logd("Uses Chrome/Edge browser microphone.")
            url = f"http://127.0.0.1:{self._http_port or 8766}"
            if hasattr(self, '_browser_ws') and self._browser_ws:
                self._browser_ws.autostart = True
            # Frontend opens the browser via shell.openExternal on this message
            self.broadcast({"type": "open_browser_url", "url": url})
            if not self._browser_opened:
                self._browser_opened = True
                self._logd("Browser opened — mic will start automatically")
            else:
                self._logd(f"Re-opening browser listener → {url}")

        elif mode == "deepgram":
            self._logd("--- Deepgram mode ---")
            key = self.settings.get("deepgram_key", "").strip()
            if not key:
                self._logd("No API key — get free at console.deepgram.com")
                self._listening = False
                self.broadcast({"type": "listening_state", "active": False})
                return
            self._deepgram = DeepgramListener(
                key, self._on_transcript, self._set_dg_status,
                on_interim=self._on_interim,
                device_id=int(self.settings.get("audio_device", -1)))
            if hasattr(self, '_browser_ws') and self._browser_ws:
                dg = self._deepgram
                self._browser_ws.set_audio_callback(
                    lambda chunk: dg._audio_q.put_nowait(chunk)
                    if dg and dg.running else None)
            self._deepgram.start()
            if not self._browser_opened:
                url = f"http://127.0.0.1:{self._http_port or 8766}"
                self.broadcast({"type": "open_browser_url", "url": url})
                self._browser_opened = True

    def _stop_all_listeners(self):
        if self._deepgram:
            self._deepgram.stop()
            self._deepgram = None
        # Tell the browser listener page to stop mic and close
        if hasattr(self, '_browser_ws') and self._browser_ws:
            self._browser_ws.autostart = False
            self._browser_ws.broadcast({"type": "stop"})

    # ── TRANSCRIPTION CALLBACKS ─────────────────────────────────
    def _on_interim(self, text: str):
        if not self._listening:
            return
        self.broadcast({"type": "transcript", "text": f">> {text}", "interim": True})
        # ── Fast-path nav on interim ─────────────────────────
        if _FAST_NAV_RE.search(text):
            with self._cur_lock:
                cb, cc, cv = self._cur_book, self._cur_chap, self._cur_verse
            nav_ref = _detect_navigation(text, cb, cc, cv)
            if nav_ref:
                self._interim_detected_ref = None
                self._process_refs([nav_ref], nav=True)
                return
        # Autocorrect first so misheared book names are recognised
        _corrected = _autocorrect_books(text)
        # ── Speculative chapter pre-fetch ────────────────────
        # Fire as soon as "Book Chapter N" is heard, before the verse number is
        # spoken, so the chapter is in cache by the time detection triggers.
        m_early = _EARLY_CHAPTER_RE.search(_corrected)
        if m_early and not _has_verse_number(_corrected):
            raw_bk = m_early.group(1).lower()
            bk = BIBLE_BOOKS.get(raw_bk, m_early.group(1).title())
            ch = int(m_early.group(2))
            trans = self.settings.get("translation", "KJV")
            _prefetch_chapter(bk, ch, trans)
        if _has_verse_number(_corrected):
            refs = parse_scripture(_corrected)
            if refs:
                self._interim_detected_ref  = refs[0]
                self._interim_detected_time = time.time()
                self._process_refs(refs[:1])

    def _on_transcript(self, text: str):
        if not self._listening:
            return
        self.broadcast({"type": "transcript", "text": text, "interim": False})
        with self._cur_lock:
            cb, cc, cv = self._cur_book, self._cur_chap, self._cur_verse
        nav_ref = _detect_navigation(text, cb, cc, cv)
        if nav_ref:
            self._interim_detected_ref = None
            self._process_refs([nav_ref], nav=True)
            return
        # Try current text; if no ref, also try combined with previous final to
        # catch references split across utterance boundaries
        # (e.g. "Proverbs chapter 3" | "verse 4 says...").
        prev = self._last_final_text
        self._last_final_text = text
        for candidate in ([text] if not prev else [text, prev + " " + text]):
            _corrected = _autocorrect_books(candidate)
            if _has_verse_number(_corrected):
                refs = parse_scripture(_corrected)
                if refs:
                    interim_ref  = self._interim_detected_ref
                    interim_age  = time.time() - self._interim_detected_time
                    self._interim_detected_ref = None
                    if refs[0] == interim_ref and interim_age < 5.0:
                        return
                    self._process_refs(refs[:1])
                    return
        self._interim_detected_ref = None

    # ── VERSE PROCESSING ────────────────────────────────────────
    def _process_refs(self, refs: list, nav: bool = False):
        nav_gap = float(self.settings.get("nav_cooldown_secs", 4))
        now = time.time()
        with self._fired_lock:
            if nav and (now - self._last_nav_time < nav_gap):
                return
            for book, chap, verse in refs:
                key = f"{book}{chap}{verse}"
                if nav:
                    if now - self.last_fired.get(key, 0) < 1.5:
                        continue
                else:
                    with self._cur_lock:
                        already = (book == self._cur_book and
                                   chap == self._cur_chap and
                                   verse == self._cur_verse)
                    if already:
                        continue
                    if now - self.last_fired.get(key, 0) < 2.0:
                        continue
                self.last_fired[key] = now
                if nav:
                    self._last_nav_time = now
                self._logd(f"{'Nav' if nav else 'Detected'}: {book} {chap}:{verse}", subtype="nav" if nav else "detect")
                self._fetch_q.put((book, chap, verse))
                with self._cur_lock:
                    self._cur_book  = book
                    self._cur_chap  = chap
                    self._cur_verse = verse

    def _fetch_worker(self):
        while True:
            book, chap, verse = self._fetch_q.get()
            try:
                self._fetch_and_send(book, chap, verse)
            except Exception as e:
                self._logd(f"❌ fetch error: {e}")

    def _fetch_and_send(self, book: str, chap: int, verse: int):
        trans  = self.settings.get("translation", "KJV")
        vtext, vref = fetch_verse(book, chap, verse, trans)
        if not vtext:
            ref_str = f"{book} {chap}:{verse}"
            self._logd(f"⚠️  Not found: {ref_str}", subtype="warn")
            self._set_status(f"Not found: {ref_str}", "#f97316")
            # Clear last_fired so the verse can be retried immediately
            with self._fired_lock:
                self.last_fired.pop(f"{book}{chap}{verse}", None)
            return
        # Update display immediately — don't wait for ProPresenter
        with self._cur_lock:
            self._cur_book  = book
            self._cur_chap  = chap
            self._cur_verse = verse
        self._update_display(vtext, vref, trans)
        self._send_to_pro(vtext, vref, trans)          # direct ProPresenter delivery
        self._fire_other_outputs(vtext, vref, trans)   # OBS, clipboard, webhook, etc.

    def _fire_other_outputs(self, verse_text: str, reference: str, trans: str) -> None:
        """Fire all output plugins except ProPresenter (handled by _send_to_pro)."""
        try:
            if _OUTPUT_PLUGINS_MISSING:
                return
            other_outputs = [o for o in self.settings.get("outputs", [])
                             if o.get("type") != "propresenter"]
            if not other_outputs:
                return
            other_settings = dict(self.settings)
            other_settings["outputs"] = other_outputs
            successes, errors = _fire_outputs(other_settings, verse_text, reference, trans)
            for ptype, err in errors:
                self._logd(f"❌ {ptype}: {err}")
        except Exception as e:
            self._logd(f"❌ outputs error: {e}")

    # ── PROPRESENTER ─────────────────────────────────────────
    def _send_to_pro(self, verse_text: str, reference: str, trans: str) -> bool:
        pro_cfg = next((o for o in self.settings.get("outputs", [])
                         if o.get("type") == "propresenter"), None)
        if not pro_cfg or not pro_cfg.get("enabled"):
            return False  # ProPresenter output turned off — don't fire, don't log
        uuid = self.settings.get("message_uuid", "").strip()
        ip   = self.settings.get("pro_ip", "").strip()
        port = str(self.settings.get("pro_port", "1025")).strip()
        if not ip or not uuid:
            self._logd("⚠️  ProPresenter IP or UUID not set")
            return False
        url = f"http://{ip}:{port}/v1/message/{uuid}/trigger"
        payload = [
            {"name": "{{VerseText}}", "text": {"text": verse_text}},
            {"name": "{{Reference}}",  "text": {"text": f"{reference} ({trans})"}}
        ]
        try:
            r = self._pro_session.post(url, json=payload, timeout=2)
            if r.status_code in (200, 204):
                self._logd(f"🚀 ON SCREEN → {reference} ({trans})", subtype="fire")
                return True
            self._logd(f"⚠️  HTTP {r.status_code}", subtype="warn")
            return False
        except requests.exceptions.ConnectionError:
            self._logd("❌ Cannot reach ProPresenter", subtype="warn")
            self._pro_session = requests.Session()
            return False
        except Exception as e:
            self._logd(f"❌ {e}", subtype="warn")
            return False

    # ── RUN ─────────────────────────────────────────────────────
    def run(self):
        """Block forever running the Electron control WebSocket server."""
        self._electron_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._electron_loop)
        try:
            self._electron_loop.run_until_complete(self._electron_serve())
        except KeyboardInterrupt:
            pass


# ─────────────────────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if HEADLESS:
        # Electron mode: run as pure WebSocket control server, no UI
        print("[headless] Starting BibleCue in headless (Electron) mode")
        HeadlessApp().run()
    else:
        root = tk.Tk()
        w, h = 1060, 700
        x = (root.winfo_screenwidth()  - w) // 2
        y = (root.winfo_screenheight() - h) // 2
        root.geometry(f"{w}x{h}+{x}+{y}")
        ProBibleApp(root)
        root.mainloop()
