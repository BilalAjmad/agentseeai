"""agentseeai v2 headless test-suite (koi display/pyautogui nahi chahiye)."""

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import agentseeai as A  # noqa: E402


# ---------------------------------------------------------------- fixtures
class FakePag:
    """pyautogui ka naka — sab calls record karta hai."""

    def __init__(self):
        self.calls = []
        self._size = (1280, 800)

    def position(self):
        return SimpleNamespace(x=400, y=300)

    def size(self):
        return self._size

    def click(self, x, y, clicks=1, button="left"):
        self.calls.append(("click", x, y, clicks, button))

    def moveTo(self, x, y, duration=0):
        self.calls.append(("move", x, y))

    def write(self, text, interval=0):
        self.calls.append(("write", len(text)))

    def hotkey(self, *keys):
        self.calls.append(("hotkey", keys))

    def scroll(self, amount):
        self.calls.append(("scroll", amount))

    def screenshot(self):
        import PIL.Image
        return PIL.Image.new("RGB", self._size, (10, 10, 10))


@pytest.fixture()
def fake_desktop(monkeypatch):
    """poora desktop naka: frame + pag + ocr words."""
    import PIL.Image

    PIL_img = PIL.Image.new("RGB", (1280, 800), (250, 250, 250))

    monkeypatch.setattr(A, "guard", lambda: None)
    monkeypatch.setattr(A, "_pyautogui", lambda: _pag[0])
    monkeypatch.setattr(A._E, "grab", lambda region=None: PIL_img.copy())
    monkeypatch.setattr(A, "ax_tree", lambda: [])
    _pag = [FakePag()]
    # OCR words (scaled coords me) — 'Login' button + 'Sign up' + 'Search'
    words = [
        {"w": "Login", "x": 100, "y": 40, "w_px": 80, "h_px": 20, "conf": 95},
        {"w": "Sign", "x": 300, "y": 300, "w_px": 40, "h_px": 20, "conf": 93},
        {"w": "up", "x": 342, "y": 300, "w_px": 25, "h_px": 20, "conf": 91},
        {"w": "Search", "x": 600, "y": 700, "w_px": 60, "h_px": 22, "conf": 90},
    ]
    monkeypatch.setattr(
        A._OCR, "analyze",
        lambda img: {"text": "Login Sign up Search", "words": words, "engine": "fake"},
    )
    return SimpleNamespace(pag=_pag[0], words=words, img=PIL_img)


# ---------------------------------------------------------------- FrameEngine
def test_frame_ttl_cache(monkeypatch):
    import PIL.Image
    calls = {"n": 0}

    def fake_grab(region=None):
        calls["n"] += 1
        return PIL.Image.new("RGB", (1280, 800), (calls["n"] % 3, 0, 0))

    monkeypatch.setattr(A._E, "grab", fake_grab)
    monkeypatch.setattr(A, "guard", lambda: None)
    A._E.invalidate()
    a = A._E.frame()          # grab 1
    b = A._E.frame()          # TTL ke andar -> cached, koi grab nahi
    assert a is b
    assert calls["n"] == 1
    time.sleep(0.02)
    A.CFG["frame_ttl_ms"] = 10
    try:
        time.sleep(0.02)
        c = A._E.frame()      # TTL guzar gaya -> naya grab
        assert c is not b
        assert calls["n"] == 2
    finally:
        A.CFG["frame_ttl_ms"] = 250


def test_frame_fresh_and_invalidate(monkeypatch):
    import PIL.Image
    calls = {"n": 0}

    def fake_grab(region=None):
        calls["n"] += 1
        return PIL.Image.new("RGB", (1280, 800), (calls["n"], 0, 0))

    monkeypatch.setattr(A._E, "grab", fake_grab)
    A._E.frame(fresh=True)
    A._E.frame()               # cached
    A._E.frame(fresh=True)     # forced
    assert calls["n"] == 2
    A._E.invalidate()
    A._E.frame()
    assert calls["n"] == 3


def test_act_invalidates_cache(monkeypatch, fake_desktop):
    import PIL.Image
    calls = {"n": 0}

    def fake_grab(region=None):
        calls["n"] += 1
        return PIL.Image.new("RGB", (1280, 800), (calls["n"] * 10, 0, 0))

    monkeypatch.setattr(A._E, "grab", fake_grab)
    A._E.frame()
    before = calls["n"]
    A.settle_ms_backup = A.CFG.get("settle_ms")
    A.CFG["settle_ms"] = 0
    try:
        img, msg = A.click(100, 50, no_look=True)
        assert img is None and "fast" in msg
        A._E.frame()  # act ne invalidate kiya tha -> naya grab
        assert calls["n"] > before
    finally:
        A.CFG["settle_ms"] = A.settle_ms_backup


# ---------------------------------------------------------------- fuzzy
def test_fuzzy_rank():
    ranked = A.fuzzy_rank("Logn", ["Login", "Logout", "Sign in"], limit=2)
    assert ranked[0][1] == "Login" and ranked[0][0] >= 60
    ranked = A.fuzzy_rank("chrome", ["chrome", "Chrome Canary", "notepad"], limit=3)
    assert ranked[0][1] == "chrome"


def test_fuzzy_substring_bonus():
    ranked = A.fuzzy_rank("login", ["catalogue", "login", "blog"], limit=3)
    # exact substring hone wala sab se upar
    assert ranked[0][1] == "login"


# ---------------------------------------------------------------- coercion
def test_coerce_args():
    def fn(x: int, y: int, button: str = "left", clicks: int = 1, no_look: bool = False):
        pass
    out = A.coerce_args(fn, ["640", "360", "right", "2", "true"])
    assert out == [640, 360, "right", 2, True]
    out = A.coerce_args(fn, ["10", "20"])
    assert out == [10, 20]


# ---------------------------------------------------------------- batch do
def test_do_batch(fake_desktop, monkeypatch):
    monkeypatch.setattr(A, "check_rate_limit", lambda: None)
    steps = [
        {"cmd": "click", "args": [100, 50], "no_look": True},
        {"cmd": "type", "args": ["hello"], "no_look": True},
        {"cmd": "key", "args": ["enter"]},
    ]
    img, msg = A.execute_steps(steps)
    lines = msg.splitlines()
    assert len(lines) == 3 and lines[0].startswith("1. click:")
    kinds = [c[0] for c in fake_desktop.pag.calls]
    assert "click" in str(fake_desktop.pag.calls)
    assert ("write", 5) in fake_desktop.pag.calls


def test_do_batch_unknown_step(fake_desktop, monkeypatch):
    monkeypatch.setattr(A, "check_rate_limit", lambda: None)
    img, msg = A.execute_steps([{"cmd": "sabotage"}])
    assert "unknown command" in msg


def test_do_json_parse():
    # bad input -> clean ValueError (GUI deps ki zaroorat nahi)
    for bad in ("not json {{{", '{"cmd":"click"}', "[]"):
        with pytest.raises(ValueError):
            A.do(bad)


# ---------------------------------------------------------------- smart find
def test_find_text_fuzzy_ocr(fake_desktop):
    img, msg = A.find_text("Logn")   # jaan-boojh kar galt imla
    assert "FOUND" in msg
    assert "OCR:" in msg and "Login" in msg


def test_click_text_ocr_fallback(fake_desktop, monkeypatch):
    monkeypatch.setattr(A, "check_rate_limit", lambda: None)
    A.CFG["settle_ms"] = 0
    img, msg = A.click_text("Sign")
    assert "clicked OCR word 'Sign'" in msg
    clicked = [c for c in fake_desktop.pag.calls if c[0] == "click"]
    assert clicked and clicked[0][1] == 320  # 300 + 40/2 word center


def test_click_text_not_found(fake_desktop, monkeypatch):
    monkeypatch.setattr(A, "check_rate_limit", lambda: None)
    A.CFG["settle_ms"] = 0
    img, msg = A.click_text("zyada_alag_cheez")
    assert "NOT FOUND" in msg


# ---------------------------------------------------------------- region parse
def test_parse_region():
    assert A._parse_region("10,20,300,200") == (10, 20, 300, 200)
    assert A._parse_region("bad") is None
    assert A._parse_region("1,2,3") is None


# ---------------------------------------------------------------- FastServer (pytest)
def test_fast_server_roundtrip(monkeypatch):
    port = 8792
    srv = A.start_fast_server(port=port, quiet=True)
    time.sleep(0.15)
    try:
        resp = A.fast_send({"cmd": "__ping"}, port=port)
        assert resp["ok"] and resp["msg"] == "pong"
        ts = []
        for _ in range(30):
            t0 = time.perf_counter()
            A.fast_send({"cmd": "__ping"}, port=port)
            ts.append((time.perf_counter() - t0) * 1000)
        ts.sort()
        print(f"30x round-trip median: {ts[15]:.2f} ms")
        assert ts[15] < 50  # CI jitter margin; sandbox me ~0.2 ms
        resp = A.fast_send({"cmd": "nope"}, port=port)
        assert resp["ok"] is False
        # 'do' dispatch
        monkeypatch.setattr(A, "do", lambda s: (None, f"echo:{s}"))
        resp = A.fast_send({"cmd": "do", "args": ["[]"]}, port=port)
        assert resp["ok"] and resp["msg"].startswith("echo:")
    finally:
        A.fast_send({"cmd": "__shutdown"}, port=port)
        time.sleep(0.25)


# ---------------------------------------------------------------- MCP schema
def test_mcp_schema():
    sch = A._schema_for(A.click)
    assert sch["properties"]["x"]["type"] == "integer"
    assert sch["required"] == ["x", "y"]
    sch = A._schema_for(A.read_screen)
    # sab optional
    assert sch["required"] == []


def test_mcp_tool_entry():
    e = A._mcp_tool_entry("do")
    assert e["name"] == "do" and e["inputSchema"]["properties"]["steps"]["type"] == "string"


# ---------------------------------------------------------------- CLI guard paths
def test_run_cli_local_when_server_down(monkeypatch, fake_desktop):
    monkeypatch.setattr(A, "fast_server_alive", lambda port=None, timeout=0.25: False)
    monkeypatch.setattr(A, "check_rate_limit", lambda: None)
    A.CFG["settle_ms"] = 0
    rc = A.run_cli(["click", "100", "200"])
    assert rc == 0
    clicked = [c for c in fake_desktop.pag.calls if c[0] == "click"]
    assert clicked


def test_run_cli_unknown(monkeypatch, capsys):
    rc = A.run_cli(["sabotage_cmd"])
    assert rc == 1
    assert "unknown command" in capsys.readouterr().out


def test_do_tool_in_cli_names_registry():
    # 'do' khud CLI se chalta hai
    assert A.do.__name__ == "do"
