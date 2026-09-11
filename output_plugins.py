"""
BibleShow — Output Plugins
Each plugin sends the detected verse to an external target.
fire_outputs() iterates the enabled list and calls the appropriate handler.
"""

import sys
import socket
import requests

_pro_session = requests.Session()


def fire_outputs(settings: dict, verse_text: str, reference: str, translation: str) -> tuple:
    """Returns (successes, errors) — each a list of plugin type strings."""
    successes, errors = [], []
    outputs = settings.get("outputs", [])
    for plugin in outputs:
        if not plugin.get("enabled"):
            continue
        ptype = plugin.get("type", "")
        try:
            if ptype == "propresenter":
                _output_propresenter(plugin, verse_text, reference, translation)
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
