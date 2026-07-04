import sys, os, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from unittest.mock import patch
from output_plugins import fire_outputs

def _settings(*extra_outputs):
    base = {
        "outputs": [
            {"type": "clipboard",    "enabled": False},
            {"type": "http_webhook", "enabled": False, "url": "http://test.local/verse"},
            {"type": "text_file",    "enabled": False, "path": ""},
            {"type": "propresenter", "enabled": False, "ip": "", "port": "1025", "uuid": ""},
        ]
    }
    for o in extra_outputs:
        base["outputs"].append(o)
    return base

def test_no_enabled_outputs_does_not_raise():
    s = _settings()
    fire_outputs(s, "In the beginning", "Genesis 1:1", "KJV")

def test_text_file_output():
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        path = f.name
    try:
        s = _settings({"type": "text_file", "enabled": True, "path": path})
        fire_outputs(s, "In the beginning", "Genesis 1:1", "KJV")
        content = open(path).read()
        assert "In the beginning" in content
        assert "Genesis 1:1" in content
    finally:
        os.unlink(path)

def test_webhook_output():
    s = _settings({"type": "http_webhook", "enabled": True, "url": "http://test.local/verse"})
    with patch("output_plugins.requests.post") as mock_post:
        fire_outputs(s, "In the beginning", "Genesis 1:1", "KJV")
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args[1]
        body = call_kwargs["json"]
        assert body["verse"] == "In the beginning"
        assert body["reference"] == "Genesis 1:1"
        assert body["translation"] == "KJV"

def test_propresenter_skips_if_no_ip():
    s = _settings({"type": "propresenter", "enabled": True, "ip": "", "uuid": "abc"})
    with patch("output_plugins.requests.post") as mock_post:
        fire_outputs(s, "verse", "ref", "KJV")
        mock_post.assert_not_called()

def test_disabled_output_is_skipped():
    s = _settings({"type": "http_webhook", "enabled": False, "url": "http://test.local"})
    with patch("output_plugins.requests.post") as mock_post:
        fire_outputs(s, "verse", "ref", "KJV")
        mock_post.assert_not_called()

def test_failed_output_does_not_block_others():
    path = tempfile.mktemp(suffix='.txt')
    s = _settings(
        {"type": "http_webhook", "enabled": True, "url": "http://no-such-host.invalid"},
        {"type": "text_file",    "enabled": True, "path": path},
    )
    try:
        fire_outputs(s, "verse", "ref", "KJV")
        assert os.path.exists(path)
    finally:
        if os.path.exists(path):
            os.unlink(path)
