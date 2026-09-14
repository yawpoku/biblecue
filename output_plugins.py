"""
BibleShow — Output Plugins
Each plugin sends the detected verse to an external target.
fire_outputs() iterates the enabled list and calls the appropriate handler.
"""

import sys
import socket
import threading
import requests

_pro_session = requests.Session()


def fire_outputs(settings: dict, verse_text: str, reference: str, translation: str) -> tuple:
    """Returns (successes, errors) — each a list of plugin type strings."""
    successes, errors = [], []
    outputs = settings.get("outputs", [])
    display_cfg = settings.get("display", {})
    for plugin in outputs:
        if not plugin.get("enabled"):
            continue
        ptype = plugin.get("type", "")
        try:
            if ptype == "propresenter":
                _output_propresenter(plugin, verse_text, reference, translation)
            elif ptype == "easyworship":
                _output_easyworship(plugin, verse_text, reference)
            elif ptype == "clipboard":
                _output_clipboard(verse_text, reference)
            elif ptype == "http_webhook":
                _output_webhook(plugin, verse_text, reference, translation)
            elif ptype == "text_file":
                _output_text_file(plugin, verse_text, reference)
            elif ptype == "obs_websocket":
                _output_obs(plugin, verse_text, reference)
            elif ptype == "tcp_raw":
                _output_tcp(plugin, verse_text, reference)
            elif ptype == "ndi":
                _output_ndi(verse_text, reference, translation, display_cfg)
            successes.append(ptype)
        except Exception as e:
            errors.append((ptype, str(e)))
    return successes, errors


def _output_propresenter(cfg: dict, verse_text: str, reference: str, translation: str) -> None:
    global _pro_session
    ip   = cfg.get("ip",   "").strip()
    port = str(cfg.get("port", "1025")).strip()
    uuid = cfg.get("uuid", "").strip()
    if not ip or not uuid:
        return
    url  = f"http://{ip}:{port}/v1/message/{uuid}/trigger"
    payload = [
        {"name": "{{VerseText}}", "text": {"text": verse_text}},
        {"name": "{{Reference}}",  "text": {"text": f"{reference} ({translation})"}},
    ]
    try:
        r = _pro_session.post(url, json=payload, timeout=3)
        r.raise_for_status()
    except requests.exceptions.ConnectionError:
        _pro_session = requests.Session()
        raise


def _output_easyworship(cfg: dict, verse_text: str, reference: str) -> None:
    """EasyWorship has no live push API, so this writes the verse to a file
    EasyWorship's Announcements module (or a similar live-text feature) can
    be pointed at and set to auto-refresh. Same mechanism as the generic
    Text File output, kept separate so it gets its own toggle/default path
    and setup docs."""
    path = cfg.get("path", "").strip()
    if not path:
        return
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"{verse_text}\n{reference}")


def _output_clipboard(verse_text: str, reference: str) -> None:
    import subprocess
    text = f"{verse_text} — {reference}"
    if sys.platform == "win32":
        p = subprocess.Popen(["clip"], stdin=subprocess.PIPE, close_fds=True)
        p.communicate(input=text.encode("utf-8"))
    elif sys.platform == "darwin":
        p = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE, close_fds=True)
        p.communicate(input=text.encode("utf-8"))
    else:
        p = subprocess.Popen(["xclip", "-selection", "c"], stdin=subprocess.PIPE)
        p.communicate(input=text.encode("utf-8"))


def _output_webhook(cfg: dict, verse_text: str, reference: str, translation: str) -> None:
    url = cfg.get("url", "").strip()
    if not url:
        return
    requests.post(
        url,
        json={"verse": verse_text, "reference": reference, "translation": translation},
        timeout=4
    )


def _output_text_file(cfg: dict, verse_text: str, reference: str) -> None:
    path = cfg.get("path", "").strip()
    if not path:
        return
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"{verse_text}\n{reference}")


def _output_obs(cfg: dict, verse_text: str, reference: str) -> None:
    try:
        import obswebsocket
        from obswebsocket import requests as obsreq
    except ImportError:
        return  # obs-websocket-py not installed — silently skip
    ip       = cfg.get("ip",       "127.0.0.1")
    port     = int(cfg.get("port", 4455))
    password = cfg.get("password", "")
    source   = cfg.get("source",   "BibleVerse")
    client = obswebsocket.obsws(ip, port, password)
    client.connect()
    try:
        client.call(obsreq.SetInputSettings(
            inputName=source,
            inputSettings={"text": f"{verse_text}\n{reference}"}
        ))
    finally:
        client.disconnect()


def _output_tcp(cfg: dict, verse_text: str, reference: str) -> None:
    ip   = cfg.get("ip",   "").strip()
    port = cfg.get("port", "")
    if not ip or not port:
        return
    text = f"{verse_text} — {reference}\n"
    with socket.create_connection((ip, int(port)), timeout=3) as s:
        s.sendall(text.encode("utf-8"))


# ── NDI ──────────────────────────────────────────────────────────────────
# Unlike every other output above, NDI is a continuous video stream, not a
# fire-and-forget call: a receiver expects a steady flow of frames, not one
# frame that then goes stale. So this keeps a persistent sender + background
# thread alive for as long as the NDI output is enabled — started/stopped by
# sync_ndi_output() (call whenever settings are loaded or changed, not per
# detection), while _output_ndi() just swaps the frame that thread is
# pushing. Requires the optional `ndi-python` package and the NDI runtime;
# silently does nothing if either is missing, same as the OBS plugin.
_ndi_lock = threading.Lock()
_ndi_state = {"sender": None, "thread": None, "running": False, "name": None, "frame": None}

NDI_W, NDI_H = 1920, 1080


def sync_ndi_output(settings: dict):
    """Start, stop, or rename the persistent NDI sender to match current
    settings. Cheap to call often — it only does real work when the desired
    state actually differs from what's running.

    Returns a short status string when something changed (worth logging to
    the UI so "enabled but nothing happens" isn't a silent black box), or
    None when there was nothing to do.
    """
    cfg = next((o for o in settings.get("outputs", []) if o.get("type") == "ndi"), None)
    enabled = bool(cfg and cfg.get("enabled"))
    name = ((cfg or {}).get("stream_name") or "BibleCue").strip() or "BibleCue"
    with _ndi_lock:
        if not enabled:
            if _ndi_state["running"]:
                _stop_ndi_locked()
                return "NDI stopped"
            return None
        if _ndi_state["running"] and _ndi_state["name"] == name:
            return None
        _stop_ndi_locked()
        return _start_ndi_locked(name, settings.get("display", {}))


def _start_ndi_locked(name: str, display_cfg: dict):
    try:
        import NDIlib as ndi
    except ImportError:
        return ("⚠️  NDI enabled but the ndi-python package isn't installed "
                "in this build — output won't run.")
    try:
        if not ndi.initialize():
            return ("⚠️  NDI enabled but the NDI runtime couldn't initialize — "
                    "install NDI Tools from ndi.video and restart BibleCue.")
        send_settings = ndi.SendCreate()
        send_settings.ndi_name = name
        sender = ndi.send_create(send_settings)
        if not sender:
            return "⚠️  NDI enabled but the sender failed to start."
    except Exception as exc:
        return f"⚠️  NDI failed to start: {exc}"
    _ndi_state["sender"]  = sender
    _ndi_state["name"]    = name
    _ndi_state["running"] = True
    _ndi_state["frame"]   = _render_ndi_frame("", "", "", display_cfg)
    t = threading.Thread(target=_ndi_loop, args=(ndi,), daemon=True)
    _ndi_state["thread"] = t
    t.start()
    return f'📡 NDI broadcasting as "{name}" — look for it by that name in your NDI receiver.'


def _stop_ndi_locked() -> None:
    _ndi_state["running"] = False
    t = _ndi_state.get("thread")
    if t and t.is_alive():
        t.join(timeout=2)
    sender = _ndi_state.get("sender")
    if sender is not None:
        try:
            import NDIlib as ndi
            ndi.send_destroy(sender)
        except Exception:
            pass
    _ndi_state["sender"] = None
    _ndi_state["thread"] = None
    _ndi_state["name"] = None
    _ndi_state["frame"] = None


def _ndi_loop(ndi) -> None:
    """Keeps pushing whatever frame is currently set at a steady ~30fps so
    receivers see a live source rather than a frozen one, even between
    verses."""
    import time
    while _ndi_state["running"]:
        frame_arr = _ndi_state["frame"]
        sender = _ndi_state["sender"]
        if frame_arr is not None and sender is not None:
            try:
                vf = ndi.VideoFrameV2()
                vf.data = frame_arr
                vf.FourCC = ndi.FOURCC_VIDEO_TYPE_RGBA
                vf.xres = frame_arr.shape[1]
                vf.yres = frame_arr.shape[0]
                vf.frame_rate_N = 30
                vf.frame_rate_D = 1
                vf.line_stride_in_bytes = frame_arr.shape[1] * 4
                ndi.send_send_video_v2(sender, vf)
            except Exception:
                break
        time.sleep(1 / 30)


def _output_ndi(verse_text: str, reference: str, translation: str, display_cfg: dict) -> None:
    if not _ndi_state["running"]:
        return
    _ndi_state["frame"] = _render_ndi_frame(verse_text, reference, translation, display_cfg)


def _render_ndi_frame(verse_text: str, reference: str, translation: str, display_cfg: dict):
    """Renders the current verse (or a blank background between verses) as
    an RGBA frame, using the same background/font/color/alignment settings
    as the fullscreen display window, so NDI output matches what the
    fullscreen window shows."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    import base64
    import io
    import re as _re

    bg_type   = display_cfg.get("bg_type", "color")
    bg_color  = display_cfg.get("bg_color", "#060B18")
    bg_image  = display_cfg.get("bg_image", "")
    text_color   = display_cfg.get("text_color", "#F8FAFC")
    accent_color = display_cfg.get("accent_color", "#F59E0B")
    verse_scale = max(0.3, float(display_cfg.get("verse_size", 100) or 100) / 100.0)
    ref_scale   = max(0.3, float(display_cfg.get("ref_size", 100) or 100) / 100.0)
    align_h = display_cfg.get("align_h", "center")
    align_v = display_cfg.get("align_v", "middle")

    img = Image.new("RGBA", (NDI_W, NDI_H), _hex_to_rgba(bg_color))

    if bg_type == "image" and bg_image.startswith("data:"):
        try:
            b64_data = bg_image.split(",", 1)[1]
            raw = base64.b64decode(b64_data)
            bg = Image.open(io.BytesIO(raw)).convert("RGBA")
            # cover-fit into the frame
            src_ratio = bg.width / bg.height
            dst_ratio = NDI_W / NDI_H
            if src_ratio > dst_ratio:
                new_h = NDI_H
                new_w = int(new_h * src_ratio)
            else:
                new_w = NDI_W
                new_h = int(new_w / src_ratio)
            bg = bg.resize((new_w, new_h), Image.LANCZOS)
            x = (new_w - NDI_W) // 2
            y = (new_h - NDI_H) // 2
            img.paste(bg.crop((x, y, x + NDI_W, y + NDI_H)), (0, 0))
        except Exception:
            pass  # fall back to solid bg_color already drawn above

    if verse_text and verse_text != "—":
        draw = ImageDraw.Draw(img)
        margin_x = int(NDI_W * 0.08)
        max_width = NDI_W - margin_x * 2

        verse_font = _load_font(int(44 * verse_scale))
        ref_font   = _load_font(int(26 * ref_scale))

        verse_lines = _wrap_text(draw, verse_text, verse_font, max_width)
        line_h = verse_font.size * 1.3
        block_h = line_h * len(verse_lines) + ref_font.size * 1.8

        if align_v == "top":
            y = int(NDI_H * 0.1)
        elif align_v == "bottom":
            y = NDI_H - int(NDI_H * 0.1) - block_h
        else:
            y = (NDI_H - block_h) / 2

        for line in verse_lines:
            w = draw.textlength(line, font=verse_font)
            x = _align_x(align_h, margin_x, max_width, w)
            draw.text((x, y), line, font=verse_font, fill=_hex_to_rgba(text_color))
            y += line_h

        y += ref_font.size * 0.5
        ref_line = f"{reference}{'  ·  ' + translation if translation else ''}".upper()
        w = draw.textlength(ref_line, font=ref_font)
        x = _align_x(align_h, margin_x, max_width, w)
        draw.text((x, y), ref_line, font=ref_font, fill=_hex_to_rgba(accent_color))

    arr = np.asarray(img, dtype=np.uint8)
    return np.ascontiguousarray(arr)


def _align_x(align_h, margin_x, max_width, text_w):
    if align_h == "left":
        return margin_x
    if align_h == "right":
        return margin_x + max_width - text_w
    return margin_x + (max_width - text_w) / 2


def _wrap_text(draw, text, font, max_width):
    words = text.split()
    lines, cur = [], ""
    for word in words:
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=font) <= max_width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _hex_to_rgba(hex_color: str, alpha: int = 255):
    hex_color = (hex_color or "#000000").lstrip("#")
    if len(hex_color) != 6:
        return (6, 11, 24, alpha)
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
    return (r, g, b, alpha)


_font_cache = {}


def _load_font(size: int):
    from PIL import ImageFont
    if size in _font_cache:
        return _font_cache[size]
    for candidate in ("georgia.ttf", "Georgia.ttf", "DejaVuSerif.ttf", "arial.ttf"):
        try:
            font = ImageFont.truetype(candidate, size)
            _font_cache[size] = font
            return font
        except Exception:
            continue
    font = ImageFont.load_default()
    _font_cache[size] = font
    return font
