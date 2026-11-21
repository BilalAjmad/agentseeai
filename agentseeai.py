#!/usr/bin/env python3
"""
Agentseeai v2.0.0 — THE FAST EDITION (2026-11-21).
Eyes + hands for ANY AI agent. No AI and no API key inside.
The agent you already use (Claude Code, Codex, Gemini CLI, Cursor...) is the brain.

WHAT'S NEW IN v2 (why it is fast — milliseconds):
  1. FAST SERVER  : `agentseeai serve` = persistent process. CLI auto-forwards
                    to it -> no Python startup per action (saves 300-600 ms).
  2. FRAME CACHE : screenshot TTL cache + persistent mss instance
                    (saves 30-80 ms per look; reads reuse, writes invalidate).
  3. OCR UPGRADE  : word BOXES + hash cache + REGION OCR + RapidOCR engine
                    (auto: rapidocr if installed, else tesseract).
                    click_text now works even with no accessibility tree —
                    it clicks text found by OCR.
  4. FUZZY FIND   : "Logn" still finds "Login" (rapidfuzz or difflib).
  5. BATCH `do`   : one call = many actions in one process (macros).
  6. ADAPTIVE     : settle 400ms->120ms (config), instant typing via clipboard,
                    open_app waits for real change instead of fixed sleep,
                    wait_change early-exits.
  7. NEW TOOLS    : find_image (template), pixel, find_color, read_screen(region),
                    do(steps), bench, doctor v2.
  8. MCP BUILT-IN : MCP stdio server implemented inside (no `pip install mcp`
                    needed anymore). `agentseeai mcp` just works.

SETUP (one time)
    pip install mss pyautogui pillow pyperclip
    # Optional (OCR): pip install pytesseract   (+ Tesseract binary)
    # Optional (faster OCR): pip install rapidocr-onnxruntime
    # Optional (template/image find): pip install opencv-python numpy

USE WITH AN MCP AGENT
    agentseeai mcp
    (Claude Code: claude mcp add agentseeai -- agentseeai mcp)

FAST MODE (start once, then every CLI call is milliseconds)
    agentseeai serve            # keep this window running
    agentseeai click 640 360    # auto-forwarded to the server -> ~1-5 ms
    agentseeai do '[{"cmd":"click","args":[640,360]},{"cmd":"type","args":["hi"]}]'

USE WITH ANY AGENT THAT HAS A TERMINAL (same commands as v1)
    agentseeai shot | click | type | key | wait | tree | find | click_text
    agentseeai events | doctor | bench

SAFETY (unchanged from v1)
    - Slam the mouse into any screen corner = instant abort.
    - Move the mouse yourself = agent pauses and must re-look.
    - touch ~/.agentseeai_stop = hard stop. Delete the file to resume.
    - Viewer is read-only control, localhost only, needs a random token.
    - App allowlist/denylist + max actions per minute in config.
    - Fast server binds 127.0.0.1 ONLY (no network exposure).

SCOPE (real desktop, no sandbox needed)
    - Windows / macOS / Linux X11 (Wayland experimental).
    - Run elevated (Administrator) to act on elevated windows.
    - Not 60 fps video: agent thinking time is seconds per STEP,
      but each individual eye/hand operation is now milliseconds.
"""
VERSION = "2.1.0"
__version__ = VERSION
BUILD_DATE = "2026-11-21"

import hashlib, hmac, inspect, io, json, logging, os, secrets, socket, subprocess, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# ---------------------------------------------------------------- config
CONFIG_PATH = Path.home() / ".agentseeai.json"
DEFAULT_CONFIG = {
    "max_width": 1280,
    "port": 8765,
    "monitor": 1,
    "allowlist": [],
    "denylist": ["Taskmgr.exe", "SystemSettings.exe"],
    "require_confirm": ["powershell.exe", "cmd.exe", "terminal", "sudo"],
    # --- speed (v2 defaults; old config file ki values override ho jati hain) ---
    "settle_ms": 120,             # v1: 400 | har action ke baad intna wait
    "frame_ttl_ms": 250,          # screenshot cache TTL (0 = hamesha fresh)
    "ax_ttl_ms": 500,             # accessibility tree cache TTL
    "fast_port": 8700,            # fast server TCP port (127.0.0.1 only)
    "input_pause_ms": 0,          # pyautogui.PAUSE (v1: 30ms har event)
    "move_duration_ms": 0,        # moveTo duration (v1: 150ms)
    "clipboard_min_chars": 60,    # isse lamba text = clipboard paste (instant)
    "max_actions_per_minute": 240,# v1: 60 — v2 fast mode ke liye 4x
    # --- ocr ---
    "ocr_lang": "eng",
    "ocr_engine": "auto",         # auto | rapidocr | tesseract | off
    # --- misc v1 ---
    "change_threshold": 0.5,
    "jpeg_quality": 60,
    "stream_fps": 6,
    "log_actions": True,
    "log_path": str(Path.home() / ".agentseeai_actions.log"),
    "mouse_abort_corner": True,
    "mouse_pause_threshold": 8,
    "mouse_pause_wait": 1.5,
    "events_path": str(Path.home() / ".agentseeai_events.jsonl"),
    "open_app_adaptive": True,    # window change ka wait (fixed 1s nahi)
}
CFG: Dict[str, Any] = DEFAULT_CONFIG.copy()
if CONFIG_PATH.exists():
    try:
        with open(CONFIG_PATH) as f:
            user = json.load(f)
        CFG.update(user)
    except Exception:
        pass

MAXW = CFG["max_width"]
PORT = int(os.environ.get("AGENTSEEAI_PORT", CFG["port"]))
FAST_PORT = int(os.environ.get("AGENTSEEAI_FAST_PORT", CFG["fast_port"]))
STOP_FILE = Path.home() / ".agentseeai_stop"
EVENTS_PATH = Path(CFG["events_path"])
MONITOR_IDX = CFG["monitor"]

logging.basicConfig(
    level=logging.INFO if CFG["log_actions"] else logging.WARNING,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(CFG["log_path"]) if CFG["log_path"] else logging.NullHandler(),
        logging.StreamHandler(sys.stderr),
    ],
)
log = logging.getLogger("agentseeai")

# shared state
_lock = threading.RLock()
_rate_lock = threading.Lock()
_prev = [None]            # last committed thumbnail (change detection)
_mouse = [None]          # last known mouse pos (human-move detection)
_session_log: List[dict] = []
_approval_queue: Dict[str, dict] = {}
_action_times: List[float] = []


def load_config():
    global CFG
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH) as f:
                user = json.load(f)
            CFG.update({**DEFAULT_CONFIG, **user})
        except Exception:
            pass


def save_config(cfg):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)


# ---------------------------------------------------------------- lazy deps
# v1 me ye sab module-load pe import hota tha (~200-400 ms extra per process).
# v2 me SIRF zaroorat pe. Doctor/--help/server ka boot turant.
_PIL = None
_MSS = None
_PAG = None
_HAS = {}


def _pil():
    global _PIL
    if _PIL is None:
        from PIL import Image
        _PIL = Image
    return _PIL


def _mss_mod():
    global _MSS
    if _MSS is None:
        import mss
        _MSS = mss
    return _MSS


def _probe(name: str) -> bool:
    if name not in _HAS:
        try:
            __import__(name)
            _HAS[name] = True
        except Exception:
            _HAS[name] = False
    return _HAS[name]


def _has(name):  # runtime probe (cached)
    return _probe(name)


def _pyautogui():
    """Import + configure pyautogui once, lazily."""
    global _PAG
    if _PAG is None:
        import pyautogui
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = float(CFG.get("input_pause_ms", 0)) / 1000.0
        _PAG = pyautogui
    return _PAG


# DPI awareness on Windows (once, lazily safe)
def _dpi_aware():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                import ctypes
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


_dpi_aware()


# ---------------------------------------------------------------- utils
def _hash_bytes(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()[:12]


_SENSITIVE = {"type_text", "clip_set", "type", "clip"}

def log_action(action, params, result, changed=None):
    safe_params = "[redacted]" if action in _SENSITIVE else params
    entry = {"t": time.time(), "action": action, "params": safe_params, "result": (result or "")[:200]}
    _session_log.append(entry)
    if CFG["log_actions"]:
        log.info(f"{action}({safe_params}) -> {str(result)[:120]}")
    try:
        with open(EVENTS_PATH, "a") as f:
            f.write(json.dumps({"t": entry["t"], "action": action, "changed": changed}) + "\n")
    except Exception:
        pass



def _extract_changed(msg: str):
    import re as _re
    m = _re.search(r"changed\s+(\d+\.?\d*)%", str(msg or ""))
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def check_rate_limit():
    with _rate_lock:
        now = time.time()
        window = [t for t in _action_times if now - t < 60]
        if len(window) >= CFG["max_actions_per_minute"]:
            raise RuntimeError(f"Rate limit: {CFG['max_actions_per_minute']} actions/min exceeded. Wait.")
        _action_times.append(now)
        while len(_action_times) > 200:
            _action_times.pop(0)


# ---------------------------------------------------------------- FrameEngine
class FrameEngine:
    """Persistent-mss screenshot engine + TTL cache (THE speed core).

    v1: har frame() pe naya mss instance + full grab. v2: mss instance
    zinda rehta hai (30-80 ms bachat) aur TTL ke andar wali image dobara
    use hoti hai (reads). Likhne wale actions cache ko invalidate karte
    hain taake agent ko hamesha NAYI tasveer mile.
    """

    def __init__(self) -> None:
        self._sct = None
        self._img = None            # scaled cached PIL image
        self._raw = None             # full-res PIL image
        self._ts = 0.0
        self._mon_idx = None
        self._out = [0, 0]           # scaled size (v1 compat global)
        self._mon_bounds = (0, 0, 1280, 800)  # (left, top, width, height) of captured monitor

    # -- mss lifecycle ---------------------------------------------------
    def _grabber(self):
        if self._sct is None:
            mss_mod = _mss_mod()
            self._sct = mss_mod.mss() if hasattr(mss_mod, "mss") else mss_mod.MSS()
        return self._sct

    def _reset(self):
        try:
            if self._sct is not None:
                self._sct.close()
        except Exception:
            pass
        self._sct = None

    # -- raw capture ------------------------------------------------------
    def grab(self, region: Optional[dict] = None):
        """Full-res capture (mss persistent; one retry on stale instance)."""
        PIL = _pil()
        for attempt in (0, 1):
            try:
                s = self._grabber()
                mons = s.monitors
                idx = self._mon_idx if self._mon_idx is not None else MONITOR_IDX
                mon = region or (
                    mons[idx] if 1 <= idx < len(mons) else (mons[1] if len(mons) > 1 else mons[0])
                )
                raw = s.grab(mon)
                self._mon_bounds = (int(mon["left"]), int(mon["top"]), int(mon["width"]), int(mon["height"]))
                return PIL.Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
            except Exception:
                self._reset()
                if attempt:
                    break
        # fallbacks (headless / odd X servers)
        raw_img = None
        for cmd in (["scrot", "-o", "/tmp/_ld_shot.png"], ["gnome-screenshot", "-f", "/tmp/_ld_shot.png"]):
            try:
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
                raw_img = PIL.open("/tmp/_ld_shot.png").convert("RGB")
                break
            except Exception:
                continue
        if raw_img is None:
            try:
                raw_img = _pyautogui().screenshot()
            except Exception:
                raw_img = PIL.new("RGB", (1280, 800), (0, 0, 0))
        return raw_img

    # -- cached scaled frame -----------------------------------------------
    def frame(self, fresh: bool = False, max_age_ms: Optional[float] = None, monitor_idx=None):
        """Scaled (<= MAXW) frame. TTL-cache reads; fresh=True forces grab."""
        with _lock:
            ttl = CFG.get("frame_ttl_ms", 250) if max_age_ms is None else max_age_ms
            stale_mon = monitor_idx is not None and self._mon_idx is not None and monitor_idx != self._mon_idx
            if (
                not fresh
                and self._img is not None
                and not stale_mon
                and (ttl <= 0 or (time.time() - self._ts) * 1000.0 <= ttl)
                and (monitor_idx is None or monitor_idx == self._mon_idx)
            ):
                return self._img
            self._mon_idx = monitor_idx if monitor_idx is not None else MONITOR_IDX
            raw = self.grab()
            w = min(MAXW, raw.width)
            img = raw.resize((w, round(raw.height * w / raw.width)))
            self._img = img
            self._raw = raw
            self._ts = time.time()
            self._out[:] = [img.width, img.height]
            return img

    def raw(self, fresh: bool = False):
        self.frame(fresh=fresh)  # ensures cache + _raw in sync
        return self._raw

    def invalidate(self):
        with _lock:
            self._ts = -1e9  # TTL dead; next read grabs fresh

    # -- change detection ----------------------------------------------------
    def thumb(self, img):
        return img.resize((160, 90)).convert("L").tobytes()

    def change(self, img, commit: bool = True) -> float:
        t, p = self.thumb(img), _prev[0]
        pct = 0.0 if p is None else sum(abs(a - b) for a, b in zip(t, p)) / (len(t) * 255) * 100
        if commit:
            with _lock:
                _prev[0] = t
        return round(pct, 1)


_E = FrameEngine()


def frame(monitor_idx=None):
    """v1-compatible global frame() — TTL-cached (reads fast, writes invalid)."""
    return _E.frame(monitor_idx=monitor_idx)


def get_monitors():
    try:
        s = _E._grabber()
        return s.monitors
    except Exception:
        return [{"left": 0, "top": 0, "width": 1280, "height": 800}]


def encode(img, fmt="PNG", **kw):
    b = io.BytesIO()
    img.save(b, fmt, **kw)
    return b.getvalue()


# ---------------------------------------------------------------- OCR engine
class OCREngine:
    """Multi-engine OCR + word boxes + hash cache + region OCR.

    engine: rapidocr (if installed, bohot fast on CPU) -> tesseract -> off
    cache : image hash -> {text, words[(w, x, y, w, h, conf)]}
    boxes : click_text ab bina accessibility ke bhi kaam karta hai
    """

    def __init__(self) -> None:
        self._cache: Dict[str, dict] = {}
        self._rapid = None
        self._engine = None

    def _pick(self) -> str:
        if self._engine:
            return self._engine
        want = str(CFG.get("ocr_engine", "auto")).lower()
        if want == "off":
            self._engine = "off"
        elif want == "rapidocr":
            self._engine = "rapidocr" if self._load_rapid() else "off"
        elif want == "tesseract":
            self._engine = "tesseract" if _probe("pytesseract") else "off"
        else:  # auto
            if self._load_rapid():
                self._engine = "rapidocr"
            elif _probe("pytesseract"):
                self._engine = "tesseract"
            else:
                self._engine = "off"
        return self._engine

    def _load_rapid(self):
        if self._rapid is not None:
            return True
        if not _probe("numpy"):
            return False
        try:
            try:
                from rapidocr_onnxruntime import RapidOCR  # classic package name
            except ImportError:
                from rapidocr import RapidOCR  # newer
            self._rapid = RapidOCR()
            return True
        except Exception:
            return False

    # -- core ---------------------------------------------------------------
    def _tess_words(self, img) -> List[dict]:
        import pytesseract
        gray = img.convert("L")
        if gray.width < 1300:
            gray = gray.resize((gray.width * 2, gray.height * 2))
            scale = 2.0
        else:
            scale = 1.0
        data = pytesseract.image_to_data(gray, lang=CFG.get("ocr_lang", "eng"), output_type=pytesseract.Output.DICT)
        words = []
        n = len(data.get("text", []))
        for i in range(n):
            w = (data["text"][i] or "").strip()
            try:
                conf = float(data["conf"][i])
            except (TypeError, ValueError):
                conf = -1.0
            if not w or conf < 30:
                continue
            x = data["left"][i] / scale
            y = data["top"][i] / scale
            ww = data["width"][i] / scale
            hh = data["height"][i] / scale
            words.append({"w": w, "x": x, "y": y, "w_px": ww, "h_px": hh, "conf": conf})
        return words

    def _rapid_words(self, img) -> List[dict]:
        import numpy as np
        arr = np.array(img.convert("RGB"))[:, :, ::-1]  # RGB->BGR
        try:
            out = self._rapid(arr)
        except Exception:
            out = None
        # rapidocr versions differ: some return (result, elapse), some just result
        if isinstance(out, tuple):
            result = out[0]
        else:
            result = out
        words = []
        if result:
            for box, text, score in result:
                try:
                    xs = [p[0] for p in box]
                    ys = [p[1] for p in box]
                    words.append({
                        "w": str(text), "x": min(xs), "y": min(ys),
                        "w_px": max(xs) - min(xs), "h_px": max(ys) - min(ys),
                        "conf": float(score) if score is not None else 90.0,
                    })
                except Exception:
                    continue
        return words

    def analyze(self, img) -> dict:
        """{text, words} — cached by image hash."""
        h = _hash_bytes(img.tobytes())
        hit = self._cache.get(h)
        if hit is not None:
            return hit
        engine = self._pick()
        words = []
        try:
            if engine == "rapidocr":
                try:
                    words = self._rapid_words(img)
                except Exception:
                    # fallback to tesseract if rapidocr breaks
                    if _probe("pytesseract"):
                        engine = "tesseract"
                        words = self._tess_words(img)
                    else:
                        raise
            elif engine == "tesseract":
                words = self._tess_words(img)
            else:
                words = []
        except Exception as e:
            return {"text": f"OCR error: {e}", "words": [], "engine": engine}
        text = " ".join(x["w"] for x in words)
        entry = {"text": text, "words": words, "engine": engine}
        # cache last few frames only
        if len(self._cache) > 6:
            for k in list(self._cache.keys())[:-3]:
                self._cache.pop(k, None)
        self._cache[h] = entry
        return entry

    def text(self, img) -> str:
        if self._pick() == "off":
            return "OCR not available (install pytesseract or rapidocr)"
        return self.analyze(img)["text"]

    def region_text(self, img, box: Tuple[int, int, int, int]) -> str:
        """OCR sirf region (x,y,w,h) — 5-10x faster than full screen."""
        x, y, w, h = box
        crop = img.crop((max(0, x), max(0, y), min(img.width, x + w), min(img.height, y + h)))
        if self._pick() == "off":
            return "OCR not available"
        return self.analyze(crop)["text"]


_OCR = OCREngine()


def ocr_text(img):
    """v1-compatible ocr_text."""
    return _OCR.text(img)


# ---------------------------------------------------------------- fuzzy find
def _ratio(a: str, b: str) -> float:
    """Similarity 0..100 (rapidfuzz if installed, else difflib)."""
    a, b = a.lower(), b.lower()
    if not a or not b:
        return 0.0
    try:
        from rapidfuzz import fuzz
        return float(fuzz.ratio(a, b))
    except ImportError:
        pass
    try:
        from difflib import SequenceMatcher
        return SequenceMatcher(None, a, b).ratio() * 100.0
    except Exception:
        return 100.0 if a == b else 0.0


def fuzzy_rank(query: str, candidates: List[str], limit: int = 5) -> List[Tuple[float, str]]:
    """Best matches first. Substring bonus ke saath scoring."""
    q = query.lower()
    scored = []
    for c in candidates:
        cl = (c or "").strip()
        if not cl:
            continue
        score = _ratio(q, cl)
        if q and q in cl.lower():
            score = max(score, 90.0 + min(9.0, len(q) * 1.5))
        scored.append((score, cl))
    scored.sort(key=lambda t: -t[0])
    return scored[:limit]


# ---------------------------------------------------------------- a11y (cached)
AX_TTL = [0.0]
_AX_CACHE: List = []


def ax_tree():
    """Accessibility tree — TTL cache (v1 me har call pe poori walk)."""
    now = time.time()
    if (now - AX_TTL[0]) * 1000.0 <= CFG.get("ax_ttl_ms", 500) and _AX_CACHE:
        return _AX_CACHE
    nodes = _ax_walk()
    with _lock:
        AX_TTL[0] = now
        _AX_CACHE[:] = nodes
    return nodes


def _ax_walk() -> List:
    """v1 walk logic (Windows UIA / win32 / macOS AX / Linux AT-SPI)."""
    from dataclasses import dataclass as _dc

    @_dc
    class AXNode:
        id: str
        role: str
        name: str
        value: str
        bounds: Tuple[int, int, int, int]
        enabled: bool = True
        focused: bool = False
        selected: bool = False

    nodes: List[AXNode] = []
    has_ax = False
    if sys.platform == "win32":
        try:
            import uiautomation as auto
            has_ax = True
            try:
                import pythoncom
                pythoncom.CoInitialize()
            except Exception:
                pass
            desktop = auto.GetRootControl()

            def walk(el, depth=0):
                if depth > 12:
                    return
                try:
                    name = el.Name or ""
                    role = el.ControlTypeName or ""
                    rect = el.BoundingRectangle
                    x, y = int(rect.left), int(rect.top)
                    w, h = int(rect.width()), int(rect.height())
                    eid = f"{role}:{name}:{x}:{y}:{w}:{h}"[:120]
                    if w > 0 and h > 0:
                        val = ""
                        try:
                            val = el.GetValuePattern().Value or ""
                        except Exception:
                            pass
                        nodes.append(AXNode(eid, role, name, val, (x, y, w, h), bool(el.IsEnabled), bool(el.HasKeyboardFocus), False))
                except Exception:
                    pass
                try:
                    for child in el.GetChildren():
                        walk(child, depth + 1)
                except Exception:
                    pass

            walk(desktop)
        except Exception as e:
            log.warning(f"Windows UIA failed: {e}")
            try:
                import win32gui

                def enum_windows(hwnd, _):
                    if win32gui.IsWindowVisible(hwnd):
                        title = win32gui.GetWindowText(hwnd)
                        if title:
                            rect = win32gui.GetWindowRect(hwnd)
                            x, y, w, h = rect[0], rect[1], rect[2] - rect[0], rect[3] - rect[1]
                            eid = f"window:{title}:{x}:{y}:{w}:{h}"[:120]
                            nodes.append(AXNode(eid, "window", title, "", (x, y, w, h), True, False, False))

                win32gui.EnumWindows(enum_windows, None)
            except Exception:
                pass
    elif sys.platform == "darwin":
        try:
            import atomacos
            has_ax = True
            app = atomacos.getFrontmostApplication()

            def walk(el):
                try:
                    role = el.AXRole or ""
                    name = el.AXTitle or el.AXDescription or ""
                    if not name and el.AXValue is not None:
                        name = str(el.AXValue)
                    pos = el.AXPosition or (0, 0)
                    size = el.AXSize or (0, 0)
                    x, y = int(pos[0]), int(pos[1])
                    w, h = int(size[0]), int(size[1])
                    val = str(el.AXValue) if el.AXValue is not None else ""
                    eid = f"{role}:{name}:{x}:{y}:{w}:{h}"[:120]
                    if w > 0 and h > 0:
                        nodes.append(AXNode(eid, role, name, val, (x, y, w, h), True, False, False))
                except Exception:
                    pass
                try:
                    for child in (el.AXChildren or []):
                        walk(child)
                except Exception:
                    pass

            walk(app)
        except Exception as e:
            log.warning(f"macOS AX failed: {e}")
    else:
        try:
            import gi
            gi.require_version("Atspi", "2.0")
            from gi.repository import Atspi
            has_ax = True
            desktop = Atspi.get_desktop(0)

            def walk(el, depth=0):
                if depth > 12:
                    return
                try:
                    name = el.get_name() or ""
                    role = el.get_role_name() or ""
                    states = el.get_state_set()
                    enabled = bool(states.contains(Atspi.StateType.ENABLED)) if states else True
                    focused = bool(states.contains(Atspi.StateType.FOCUSED)) if states else False
                    selected = bool(states.contains(Atspi.StateType.SELECTED)) if states else False
                    ext = el.get_extents(Atspi.CoordType.SCREEN_COORDS)
                    x, y, w, h = int(ext.x), int(ext.y), int(ext.width), int(ext.height)
                    eid = f"{role}:{name}:{x}:{y}:{w}:{h}"[:120]
                    if w > 0 and h > 0:
                        nodes.append(AXNode(eid, role, name, "", (x, y, w, h), enabled, focused, selected))
                except Exception:
                    pass
                try:
                    for i in range(el.get_child_count()):
                        walk(el.get_child_at_index(i), depth + 1)
                except Exception:
                    pass

            for i in range(desktop.get_child_count()):
                walk(desktop.get_child_at_index(i))
        except Exception as e:
            log.warning(f"AT-SPI error: {e}")

    interactive = {"button", "link", "menuitem", "checkbox", "radiobutton", "slider", "textbox", "combobox", "listitem", "tab", "pushbutton", "menu", "text", "edit", "document", "dialog", "alert", "label", "hyperlink", "dataitem", "treeitem"}

    def _norm_role(r):
        r = (r or "").lower()
        if r.endswith("control"):
            r = r[:-7]
        return r

    if not has_ax:
        return [AXNode("no-ax", "error", "Accessibility not available", "", (0, 0, 0, 0), False, False, False)]
    filtered = [n for n in nodes if _norm_role(n.role) in interactive and n.bounds[2] > 0 and n.bounds[3] > 0]
    seen = set()
    out = []
    for n in filtered:
        if n.id in seen:
            continue
        seen.add(n.id)
        out.append(n)
    return out


AXNode = None  # ax_tree dataclass andar hai; type-only use


def ax_to_text(nodes, max_lines=300):
    lines = []
    for n in nodes[:max_lines]:
        x, y, w, h = n.bounds
        flags = []
        if not n.enabled:
            flags.append("DISABLED")
        if n.focused:
            flags.append("FOCUSED")
        if n.selected:
            flags.append("SELECTED")
        flag_str = f" [{', '.join(flags)}]" if flags else ""
        val = f" = '{n.value}'" if n.value else ""
        lines.append(f"{n.id} | {n.role} | '{n.name}'{val} @({x},{y},{w},{h}){flag_str}")
    if len(nodes) > max_lines:
        lines.append(f"... and {len(nodes) - max_lines} more")
    return "\n".join(lines)


def find_element(nodes, query):
    """Best-match element by name/value. Enabled + visible only. No id matching."""
    q = (query or "").lower().strip()
    if not q:
        return None
    candidates = [n for n in nodes if getattr(n, "enabled", True)]
    # exact name match
    for n in candidates:
        if (n.name or "").lower() == q:
            return n
    # name contains full query
    for n in candidates:
        if q in (n.name or "").lower():
            return n
    # value contains full query
    for n in candidates:
        if q in (n.value or "").lower():
            return n
    # word containment (all query words present in name)
    words = [w for w in q.split() if len(w) > 2]
    if words:
        for n in candidates:
            nl = (n.name or "").lower()
            if all(w in nl for w in words):
                return n
    # fuzzy best match by name (threshold higher to avoid wrong picks)
    best, best_score = None, 0.0
    for n in candidates:
        score = _ratio(q, (n.name or "").lower())
        if score > best_score:
            best, best_score = n, score
    if best is not None and best_score >= 70.0:
        return best
    return None


# ---------------------------------------------------------------- guard
def guard():
    check_rate_limit()
    if STOP_FILE.exists():
        raise RuntimeError("Emergency stop file exists (~/.agentseeai_stop). Ask the human.")
    pag = _pyautogui()
    cur = pag.position()
    if CFG["mouse_abort_corner"]:
        W, H = pag.size()
        corners = [(cur.x < 10 and cur.y < 10), (cur.x > W - 10 and cur.y < 10),
                   (cur.x < 10 and cur.y > H - 10), (cur.x > W - 10 and cur.y > H - 10)]
        if any(corners):
            raise RuntimeError("Mouse in screen corner: ABORT")
    if _mouse[0] and abs(cur.x - _mouse[0][0]) + abs(cur.y - _mouse[0][1]) > CFG["mouse_pause_threshold"]:
        quiet_since, last = time.time(), cur
        while time.time() - quiet_since < CFG["mouse_pause_wait"]:
            time.sleep(0.2)
            now = pag.position()
            if now != last:
                last, quiet_since = now, time.time()
        _mouse[0] = (last.x, last.y)
        raise RuntimeError("The human used the mouse, so I paused. Take a new screenshot and re-plan.")


def guard_look():
    """Light guard for read-only operations: stop file + corner abort only.
    Does NOT rate-limit or pause on mouse movement (those block looking)."""
    if STOP_FILE.exists():
        raise RuntimeError("Emergency stop file exists (~/.agentseeai_stop). Ask the human.")
    pag = _pyautogui()
    cur = pag.position()
    if CFG["mouse_abort_corner"]:
        W, H = pag.size()
        corners = [(cur.x < 10 and cur.y < 10), (cur.x > W - 10 and cur.y < 10),
                   (cur.x < 10 and cur.y > H - 10), (cur.x > W - 10 and cur.y > H - 10)]
        if any(corners):
            raise RuntimeError("Mouse in screen corner: ABORT")


def check_app_allowed():
    if not CFG["allowlist"] and not CFG["denylist"]:
        return True
    try:
        if sys.platform == "win32":
            import win32gui, win32process, psutil
            hwnd = win32gui.GetForegroundWindow()
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            name = psutil.Process(pid).name().lower()
        elif sys.platform == "darwin":
            from AppKit import NSWorkspace
            name = NSWorkspace.sharedWorkspace().frontmostApplication().localizedName().lower()
        else:
            out = subprocess.check_output(["xdotool", "getwindowfocus", "getwindowname"], text=True, stderr=subprocess.DEVNULL)
            name = out.strip().lower()
        for deny in CFG["denylist"]:
            if deny.lower() in name:
                return False
        if CFG["allowlist"]:
            for allow in CFG["allowlist"]:
                if allow.lower() in name:
                    return True
            return False
        return True
    except Exception as e:
        log.warning(f"check_app_allowed failed ({e}); allowing action (denylist inactive)")
        return True


def to_screen(x, y):
    with _lock:
        if not _E._out[0]:
            _E.frame()
        lb, tb, mw, mh = _E._mon_bounds
        ox, oy = _E._out[0], _E._out[1]
    return round(x * mw / ox) + lb, round(y * mh / oy) + tb


def act(fn, settle=None, no_look: bool = False):
    """v2 act: cache-invalidate on write + optional no_look (fast chains)."""
    guard()
    if not check_app_allowed():
        raise RuntimeError("Current app is denylisted or not in allowlist.")
    _E.invalidate()  # action screen badal deta hai — purani image jhoot bolegi
    fn()
    pag = _pyautogui()
    p = pag.position()
    _mouse[0] = (p.x, p.y)
    s = settle if settle is not None else CFG.get("settle_ms", 120)
    if s:
        time.sleep(s / 1000.0)
    if no_look:
        return None, "done (fast, no screenshot)"
    img = _E.frame(fresh=True)
    pct = _E.change(img)
    return img, f"done. screen changed {pct}%. image {img.width}x{img.height}"


# ---------------------------------------------------------------- hands (v1 API + speed)
def screenshot():
    guard_look()
    img = _E.frame(fresh=True)
    pct = _E.change(img, commit=False)
    return img, f"image {img.width}x{img.height}. changed {pct}% since last look"


def click(x: int, y: int, button: str = "left", clicks: int = 1, no_look: bool = False):
    return act(lambda: _pyautogui().click(*to_screen(x, y), clicks=clicks, button=button), no_look=no_look)


def move(x: int, y: int, duration_ms: int = None):
    d = (CFG.get("move_duration_ms", 0) if duration_ms is None else duration_ms) / 1000.0
    return act(lambda: _pyautogui().moveTo(*to_screen(x, y), duration=d))


def drag(x1: int, y1: int, x2: int, y2: int):
    def go():
        pag = _pyautogui()
        pag.moveTo(*to_screen(x1, y1))
        pag.dragTo(*to_screen(x2, y2), duration=0.4, button="left")
    return act(go)


def scroll(amount: int, x: int = None, y: int = None, no_look: bool = False):
    def go():
        pag = _pyautogui()
        if x is not None and y is not None:
            pag.moveTo(*to_screen(x, y))
        pag.scroll(amount)
    return act(go, no_look=no_look)


def hscroll(amount: int, x: int = None, y: int = None):
    def go():
        pag = _pyautogui()
        if x is not None and y is not None:
            pag.moveTo(*to_screen(x, y))
        pag.hscroll(amount)
    return act(go)


def type_text(text: str, no_look: bool = False):
    """v2: lambi/unicode text = clipboard paste (instant), chhoti = write(0ms)."""
    def go():
        pag = _pyautogui()
        if (len(text) >= CFG.get("clipboard_min_chars", 60)) or not text.isascii():
            import pyperclip
            try:
                old = pyperclip.paste()
            except Exception:
                old = None
            pyperclip.copy(text)
            time.sleep(0.05)  # let clipboard settle before paste
            pag.hotkey("command" if sys.platform == "darwin" else "ctrl", "v")
            time.sleep(0.05)
            # restore clipboard (best effort)
            try:
                if old is not None:
                    pyperclip.copy(old)
            except Exception:
                pass
        else:
            pag.write(text, interval=0)
    return act(go, no_look=no_look)


_KEY_ALIASES = {"cmd": "command", "ctrl": "ctrl", "control": "ctrl", "win": "win", "super": "win", "alt": "alt", "option": "alt", "shift": "shift", "enter": "enter", "return": "enter", "esc": "esc", "escape": "esc", "tab": "tab", "space": "space", "backspace": "backspace", "delete": "delete", "del": "delete", "up": "up", "down": "down", "left": "left", "right": "right", "home": "home", "end": "end", "pageup": "pageup", "pagedown": "pagedown"}


def key(keys: str, no_look: bool = False):
    parts = keys.lower().replace(" ", "").split("+")
    mapped = [_KEY_ALIASES.get(p, p) for p in parts]
    return act(lambda: _pyautogui().hotkey(*mapped), no_look=no_look)


def hold_key(keys: str, duration: float = 1.0):
    parts = keys.lower().replace(" ", "").split("+")
    mapped = [_KEY_ALIASES.get(p, p) for p in parts]

    def go():
        pag = _pyautogui()
        for k in mapped:
            pag.keyDown(k)
        time.sleep(float(duration))
        for k in reversed(mapped):
            pag.keyUp(k)
    return act(go)


def mouse_down(button: str = "left", x: int = None, y: int = None):
    def go():
        pag = _pyautogui()
        if x is not None and y is not None:
            pag.moveTo(*to_screen(x, y))
        pag.mouseDown(button=button)
    return act(go)


def mouse_up(button: str = "left", x: int = None, y: int = None):
    def go():
        pag = _pyautogui()
        if x is not None and y is not None:
            pag.moveTo(*to_screen(x, y))
        pag.mouseUp(button=button)
    return act(go)


def cursor_position():
    guard_look()
    pag = _pyautogui()
    p = pag.position()
    if _E._out[0]:
        W, H = pag.size()
        sx = round(p.x * _E._out[0] / W)
        sy = round(p.y * _E._out[1] / H)
    else:
        sx, sy = p.x, p.y
    img = _E.frame()
    return img, f"cursor @({sx},{sy}) scaled, real ({p.x},{p.y})"


def wait_change(timeout: float = 5.0):
    """v2: early-exit poll (fixed interval nahi) + har poll pe cached compare."""
    guard_look()
    base = _prev[0]
    end = time.time() + float(timeout)
    last_pct = 0.0
    while time.time() < end:
        img = _E.frame(fresh=True)
        t = _E.thumb(img)
        d = 0 if base is None else sum(abs(a - b) for a, b in zip(t, base)) / (len(t) * 255) * 100
        if d > CFG["change_threshold"]:
            time.sleep(min(0.3, CFG.get("settle_ms", 120) / 1000.0))
            break
        last_pct = d
        time.sleep(0.15)
    img = _E.frame(fresh=True)
    pct = _E.change(img)
    return img, f"screen changed {pct}% (last poll delta {round(last_pct,1)}%). image {img.width}x{img.height}"


def wait_for_text(text: str, timeout: float = 10.0, interval: float = 0.5, region: str = ""):
    """v2: OCR + AX dono check + optional region 'x,y,w,h' (10x faster)."""
    guard_look()
    q = text.lower()
    box = _parse_region(region) if region else None
    end = time.time() + float(timeout)
    last = ""
    while time.time() < end:
        img = _E.frame(fresh=True)
        t = _OCR.region_text(img, box) if box else _OCR.text(img)
        if q in t.lower():
            pct = _E.change(img)
            return img, f"FOUND '{text}' on screen. changed {pct}%"
        nodes = ax_tree()
        for n in nodes:
            if q in (n.name or "").lower() or q in (n.value or "").lower():
                pct = _E.change(img)
                return img, f"FOUND '{text}' in UI. changed {pct}%"
        last = t
        time.sleep(float(interval))
    img = _E.frame(fresh=True)
    pct = _E.change(img)
    return img, f"NOT FOUND '{text}' within {timeout}s. last ocr: {last[:200]}"


def zoom(x: int, y: int, w: int = 200, h: int = 200):
    guard()
    _E.frame()
    pag = _pyautogui()
    W, H = pag.size()
    ox, oy = _E._out[0], _E._out[1]
    cx = round(x * W / ox)
    cy = round(y * H / oy)
    cw = round(w * W / ox)
    ch = round(h * H / oy)
    left = max(0, cx - cw // 2)
    top = max(0, cy - ch // 2)
    right = min(W, left + cw)
    bottom = min(H, top + ch)
    try:
        region = {"left": left, "top": top, "width": right - left, "height": bottom - top}
        zimg = _E.grab(region=region)
    except Exception:
        full = pag.screenshot()
        zimg = full.crop((left, top, right, bottom))
    zimg = zimg.resize((zimg.width * 2, zimg.height * 2))
    return zimg, f"zoomed {zimg.width}x{zimg.height} around ({x},{y})"


def record(seconds: float = 3.0, fps: float = 2.0):
    guard()
    PIL = _pil()
    frames = []
    end = time.time() + float(seconds)
    while time.time() < end:
        frames.append(_E.frame(fresh=True).copy())
        time.sleep(1.0 / float(fps))
    if not frames:
        return _E.frame(fresh=True), "no frames"
    cols = 4
    rows = (len(frames) + cols - 1) // cols
    tw, th = frames[0].width // 2, frames[0].height // 2
    sheet = PIL.new("RGB", (cols * tw, rows * th), (0, 0, 0))
    for i, f in enumerate(frames):
        r, c = divmod(i, cols)
        sheet.paste(f.resize((tw, th)), (c * tw, r * th))
    return sheet, f"recorded {len(frames)} frames over {seconds}s -> contact sheet {sheet.width}x{sheet.height}"


def _parse_region(region: str) -> Optional[Tuple[int, int, int, int]]:
    """'x,y,w,h' string -> tuple (invalid -> None)."""
    try:
        parts = [int(p.strip()) for p in str(region).split(",")]
        if len(parts) == 4 and all(p >= 0 for p in parts):
            return (parts[0], parts[1], parts[2], parts[3])
    except Exception:
        pass
    return None


def read_screen(x: int = None, y: int = None, w: int = None, h: int = None):
    """v2: region OCR optional (sirf us hisse ki padhai — 5-10x faster)."""
    guard_look()
    img = _E.frame()
    if x is not None and y is not None and w and h:
        text = _OCR.region_text(img, (x, y, w, h))
        return img, f"OCR region({x},{y},{w},{h}) ({len(text)} chars):\n{text[:4000]}"
    text = _OCR.text(img)
    return img, f"OCR ({len(text)} chars):\n{text[:4000]}"


# ---------------------------------------------------------------- smart find (v2)
def _scaled_word_center(word, img):
    """OCR word center in SCALED screenshot coords (click() isi ko use karta hai)."""
    cx = word["x"] + word["w_px"] / 2.0
    cy = word["y"] + word["h_px"] / 2.0
    return round(cx), round(cy)


def find_text(query: str, top: int = 3):
    """v2: AX + OCR + fuzzy, top matches with coords (clickable)."""
    guard_look()
    img = _E.frame()
    matches: List[str] = []

    nodes = ax_tree()
    el = find_element(nodes, query)
    if el:
        x, y, w, h = el.bounds
        cx, cy = x + w // 2, y + h // 2
        if _E._out[0]:
            pag = _pyautogui()
            W, H = pag.size()
            sx = round(cx * _E._out[0] / W)
            sy = round(cy * _E._out[1] / H)
        else:
            sx, sy = cx, cy
        matches.append(f"AX: {el.id} @({sx},{sy}) role={el.role} name='{el.name}'")

    analysis = _OCR.analyze(img)
    words = [wd for wd in analysis["words"] if wd["w"]]
    ranked = fuzzy_rank(query, [wd["w"] for wd in words], limit=max(1, top))
    for score, wtext in ranked:
        if score < 50:
            continue
        for wd in words:
            if wd["w"] == wtext:
                sx, sy = _scaled_word_center(wd, img)
                matches.append(f"OCR: '{wd['w']}' @({sx},{sy}) conf={round(wd['conf'])} match={round(score)}%")
                break

    if not matches:
        if query.lower() in analysis.get("text", "").lower():
            matches.append(f"OCR text: '{query}' (coords nahi mile — zoom use karo)")
    if not matches:
        return img, f"NOT FOUND: '{query}' in {len(nodes)} elements, {len(words)} OCR words"
    return img, "FOUND:\n" + "\n".join(matches[:top])


def click_text(query: str, button: str = "left", clicks: int = 1):
    """v2: AX -> OCR word fallback (a11y na ho to bhi click ho jata hai)."""
    guard()
    nodes = ax_tree()
    el = find_element(nodes, query)
    if el:
        x, y, w, h = el.bounds
        cx, cy = x + w // 2, y + h // 2
        if _E._out[0]:
            pag = _pyautogui()
            W, H = pag.size()
            sx = round(cx * _E._out[0] / W)
            sy = round(cy * _E._out[1] / H)
        else:
            sx, sy = cx, cy
        return click(sx, sy, button=button, clicks=clicks)

    img = _E.frame()
    analysis = _OCR.analyze(img)
    words = [wd for wd in analysis["words"] if wd["w"]]
    ranked = fuzzy_rank(query, [wd["w"] for wd in words], limit=1)
    if ranked and ranked[0][0] >= 60:
        target = ranked[0][1]
        for wd in words:
            if wd["w"] == target:
                sx, sy = _scaled_word_center(wd, img)
                res = click(sx, sy, button=button, clicks=clicks)
                return res[0], f"clicked OCR word '{target}' @({sx},{sy}) {round(ranked[0][0])}% match. {res[1]}"
    return img, f"NOT FOUND: '{query}' (AX + OCR dono me nahi)"


def find_image(template_path: str, threshold: float = 0.8):
    """v2 NEW: image template find (cv2) — buttons/icons jo text nahi rakhte."""
    guard()
    if not _probe("cv2"):
        return _E.frame(), "opencv not installed (pip install opencv-python)"
    import cv2
    import numpy as np
    img = _E.frame(fresh=True)
    try:
        screen_cv = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
        template = cv2.imread(template_path, cv2.IMREAD_COLOR)
        if template is None:
            return img, f"template not readable: {template_path}"
        result = cv2.matchTemplate(screen_cv, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        h, w = template.shape[:2]
        cx, cy = max_loc[0] + w // 2, max_loc[1] + h // 2
        if max_val >= threshold:
            return img, f"FOUND image @({cx},{cy}) score={round(max_val, 2)} (click {cx} {cy})"
        return img, f"NOT FOUND (best score {round(max_val, 2)} < {threshold})"
    except Exception as e:
        return img, f"find_image error: {e}"


def pixel(x: int, y: int):
    """v2 NEW: ek pixel ka color."""
    guard()
    rx, ry = to_screen(x, y)
    img = _E.grab(region={"left": rx, "top": ry, "width": 1, "height": 1})
    rgb = img.getpixel((0, 0))
    return None, f"pixel({x},{y}) = rgb{tuple(rgb)}"


def find_color(r: int, g: int, b: int, tol: int = 16, region: str = ""):
    """v2 NEW: color dhoondo (numpy -> fast, warna PIL scan)."""
    guard()
    box = _parse_region(region) if region else None
    img = _E.frame(fresh=True)
    if box:
        img = img.crop((box[0], box[1], box[0] + box[2], box[1] + box[3]))
    try:
        import numpy as np
        arr = np.array(img.convert("RGB")).astype(int)
        mask = (abs(arr[:, :, 0] - r) <= tol) & (abs(arr[:, :, 1] - g) <= tol) & (abs(arr[:, :, 2] - b) <= tol)
        ys, xs = mask.nonzero()
        if len(xs):
            x, y = int(xs[0]), int(ys[0])
            off_x = box[0] if box else 0
            off_y = box[1] if box else 0
            return None, f"FOUND color rgb({r},{g},{b}) @({x + off_x},{y + off_y}) ({len(xs)} px)"
        return None, "NOT FOUND color"
    except ImportError:
        rgb_img = img.convert("RGB")
        for yy in range(0, rgb_img.height, 2):
            for xx in range(0, rgb_img.width, 2):
                p = rgb_img.getpixel((xx, yy))
                if abs(p[0] - r) <= tol and abs(p[1] - g) <= tol and abs(p[2] - b) <= tol:
                    off_x = box[0] if box else 0
                    off_y = box[1] if box else 0
                    return None, f"FOUND color rgb({r},{g},{b}) @({xx + off_x},{yy + off_y})"
        return None, "NOT FOUND color"


def elements():
    guard_look()
    nodes = ax_tree()
    text = ax_to_text(nodes)
    img = _E.frame()
    return img, text


# ---------------------------------------------------------------- windows / apps
def windows():
    guard_look()
    out = []
    if sys.platform == "win32":
        try:
            import win32gui

            def cb(hwnd, _):
                if win32gui.IsWindowVisible(hwnd):
                    t = win32gui.GetWindowText(hwnd)
                    if t:
                        out.append({"title": t, "hwnd": hwnd, "rect": list(win32gui.GetWindowRect(hwnd))})

            win32gui.EnumWindows(cb, None)
        except Exception as e:
            return _E.frame(), f"windows error: {e}"
    elif sys.platform == "darwin":
        try:
            r = subprocess.check_output(["osascript", "-e", 'tell application "System Events" to get name of every process whose background only is false'], text=True)
            for name in r.strip().split(", "):
                out.append({"title": name})
        except Exception as e:
            return _E.frame(), f"windows error: {e}"
    else:
        try:
            out_ids = subprocess.check_output(["xdotool", "search", "--name", ""], text=True, stderr=subprocess.DEVNULL).split()
            for wid in out_ids:
                try:
                    name = subprocess.check_output(["xdotool", "getwindowname", wid], text=True, stderr=subprocess.DEVNULL).strip()
                    out.append({"id": wid, "title": name})
                except Exception:
                    pass
            if not out:
                try:
                    r = subprocess.check_output(["wmctrl", "-l"], text=True, stderr=subprocess.DEVNULL)
                    for line in r.strip().splitlines():
                        parts = line.split(None, 3)
                        if len(parts) >= 4:
                            out.append({"id": parts[0], "desktop": parts[1], "title": parts[3]})
                except Exception:
                    pass
        except Exception as e:
            return _E.frame(), f"windows error: {e}"
    img = _E.frame()
    return img, json.dumps(out, indent=2)[:4000]


def focus(title_query: str):
    guard()
    q = title_query.lower()
    if sys.platform == "win32":
        try:
            import win32gui

            def cb(hwnd, _):
                if win32gui.IsWindowVisible(hwnd) and q in win32gui.GetWindowText(hwnd).lower():
                    win32gui.SetForegroundWindow(hwnd)

            win32gui.EnumWindows(cb, None)
        except Exception as e:
            return _E.frame(), f"focus error: {e}"
    elif sys.platform == "darwin":
        try:
            subprocess.run(["osascript", "-e", f'tell application "{title_query}" to activate'], check=False)
        except Exception as e:
            return _E.frame(), f"focus error: {e}"
    else:
        try:
            wids = []
            try:
                wids = subprocess.check_output(["xdotool", "search", "--name", title_query], text=True, stderr=subprocess.DEVNULL).split()
            except Exception:
                pass
            if not wids:
                try:
                    wids = subprocess.check_output(["xdotool", "search", "--class", title_query], text=True, stderr=subprocess.DEVNULL).split()
                except Exception:
                    pass
            if wids:
                subprocess.run(["xdotool", "windowfocus", wids[0]], check=False, stderr=subprocess.DEVNULL)
                subprocess.run(["xdotool", "windowraise", wids[0]], check=False, stderr=subprocess.DEVNULL)
            else:
                r = subprocess.check_output(["wmctrl", "-l"], text=True, stderr=subprocess.DEVNULL)
                for line in r.strip().splitlines():
                    parts = line.split(None, 3)
                    if len(parts) >= 4 and q in parts[3].lower():
                        subprocess.run(["wmctrl", "-ia", parts[0]], check=False)
                        break
        except Exception as e:
            return _E.frame(), f"focus error: {e}"
    time.sleep(0.15)
    img = _E.frame(fresh=True)
    return img, f"focused '{title_query}'. changed {_E.change(img)}%"


def open_app(name: str):
    """v2: fixed 1s sleep nahi — real screen/window change ka adaptive wait."""
    guard()
    deny = [d.lower() for d in CFG["denylist"]]
    if any(d in name.lower() for d in deny):
        return _E.frame(), f"app '{name}' is denylisted"
    try:
        if sys.platform == "win32":
            subprocess.Popen(["cmd", "/c", "start", "", name], shell=False)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-a", name])
        else:
            subprocess.Popen([name])
    except Exception as e:
        return _E.frame(), f"open_app error: {e}"
    if CFG.get("open_app_adaptive", True):
        end = time.time() + 3.0
        while time.time() < end:
            img = _E.frame(fresh=True)
            pct = _E.change(img, commit=False)
            if pct > CFG["change_threshold"]:
                return img, f"opened '{name}'. changed {round(pct,1)}% (adaptive, {round(3.0 - (end - time.time()), 1)}s)"
            time.sleep(0.12)
        return img, f"opened '{name}' (screen unchanged after 3s — app background me ho sakta hai)"
    time.sleep(0.6)
    img = _E.frame(fresh=True)
    return img, f"opened '{name}'. changed {_E.change(img)}%"


def clip_get():
    guard()
    try:
        import pyperclip
        text = pyperclip.paste()
    except Exception as e:
        return _E.frame(), f"clipboard error: {e}"
    return None, f"clipboard: {text[:2000]}"


def clip_set(text: str):
    guard()
    try:
        import pyperclip
        pyperclip.copy(text)
    except Exception as e:
        return _E.frame(), f"clipboard error: {e}"
    return None, f"clipboard set ({len(text)} chars)"


def screens():
    guard_look()
    mons = get_monitors()
    img = _E.frame()
    return img, json.dumps(mons, indent=2)


def set_screen(idx: int):
    global MONITOR_IDX
    MONITOR_IDX = int(idx)
    CFG["monitor"] = MONITOR_IDX
    save_config(CFG)
    _E.invalidate()
    img = _E.frame(fresh=True)
    return img, f"monitor set to {MONITOR_IDX}. image {img.width}x{img.height}"


def status():
    guard_look()
    img = _E.frame()
    info = {
        "version": VERSION,
        "build": BUILD_DATE,
        "platform": sys.platform,
        "python": sys.version.split()[0],
        "monitor": MONITOR_IDX,
        "monitors": len(get_monitors()),
        "out_size": list(_E._out),
        "ocr_engine": _OCR._pick(),
        "has_tess": _probe("pytesseract"),
        "has_rapidocr": _OCR._rapid is not None,
        "has_cv2": _probe("cv2"),
        "has_ax": bool(ax_tree() and ax_tree()[0].role != "error"),
        "stop_file": STOP_FILE.exists(),
        "token": TOKEN,
        "port": PORT,
        "fast_port": FAST_PORT,
        "fast_server_alive": fast_server_alive(),
        "actions_logged": len(_session_log),
    }
    return img, json.dumps(info, indent=2)


def ask_approval(question: str, timeout: float = 60.0):
    guard()
    aid = secrets.token_urlsafe(6)
    _approval_queue[aid] = {"status": "pending", "question": question, "answer": None, "created": time.time()}
    try:
        with open(EVENTS_PATH, "a") as f:
            f.write(json.dumps({"t": time.time(), "approval": aid, "question": question}) + "\n")
    except Exception:
        pass
    end = time.time() + float(timeout)
    while time.time() < end:
        st = _approval_queue[aid]["status"]
        if st in ("approved", "denied"):
            ans = _approval_queue[aid]["answer"]
            img = _E.frame()
            return img, f"approval {aid}: {st}. answer={ans}"
        time.sleep(0.5)
    _approval_queue[aid]["status"] = "timeout"
    img = _E.frame()
    return img, f"approval {aid}: timeout after {timeout}s"


# ---------------------------------------------------------------- batch `do` (v2 NEW)
CLI_NAMES = {
    "shot": screenshot, "screenshot": screenshot,
    "click": click, "move": move, "drag": drag, "scroll": scroll, "hscroll": hscroll,
    "type": type_text, "type_text": type_text, "key": key, "hold_key": hold_key,
    "mouse_down": mouse_down, "mouse_up": mouse_up, "cursor_position": cursor_position,
    "wait": wait_change, "wait_change": wait_change, "wait_for_text": wait_for_text,
    "zoom": zoom, "record": record, "read_screen": read_screen, "ocr": read_screen,
    "find": find_text, "find_text": find_text, "click_text": click_text,
    "find_image": find_image, "pixel": pixel, "find_color": find_color,
    "tree": elements, "elements": elements,
    "windows": windows, "focus": focus, "open_app": open_app,
    "clip_get": clip_get, "clip_set": clip_set, "screens": screens, "set_screen": set_screen,
    "status": status, "ask_approval": ask_approval,
}


def execute_steps(steps: List[dict]) -> Tuple[Optional[Any], str]:
    """Batch runner: [{cmd, args, kwargs, no_look}] — ek process, ek journey.

    har step me no_look:true ho to screenshot skip (ms-level chain),
    aakhri step ya jo chahe wo full look karta hai.
    """
    guard()
    msgs = []
    img = None
    for i, st in enumerate(steps):
        if not isinstance(st, dict):
            return img, f"step {i} not an object: {st!r}"
        cmd = st.get("cmd") or st.get("command") or ""
        fn = CLI_NAMES.get(cmd)
        if fn is None:
            return img, f"step {i}: unknown command '{cmd}'"
        args = st.get("args", [])
        kwargs = dict(st.get("kwargs") or {})
        if st.get("no_look"):
            sig = inspect.signature(fn)
            if "no_look" in sig.parameters:
                kwargs["no_look"] = True
        try:
            img, msg = fn(*args, **kwargs)
            msgs.append(f"{i + 1}. {cmd}: {msg}")
        except Exception as e:
            msgs.append(f"{i + 1}. {cmd}: ERROR {e}")
            return img, "\n".join(msgs)
    if img is not None:
        pass  # aakhri img wapas
    else:
        img = _E.frame()
    return img, "\n".join(msgs)


def do(steps: str):
    """MCP/CLI batch tool — JSON string of steps (bad input -> ValueError).

    Parse pehle, guard baad me: bad JSON pe clean error milta hai bina GUI
    dependencies ke (doctor/headless environments me bhi).
    """
    try:
        parsed = json.loads(steps)
    except Exception as e:
        raise ValueError(f"bad JSON steps: {e}")
    if not isinstance(parsed, list):
        raise ValueError("steps must be a JSON array")
    if not parsed:
        raise ValueError("no steps (empty batch)")
    return execute_steps(parsed)


TOKEN = os.environ.get("AGENTSEEAI_TOKEN") or secrets.token_urlsafe(16)


# ---------------------------------------------------------------- viewer (v1 compat)
def _parse_token(path):
    if "t=" in path:
        part = path.split("t=", 1)[1]
        return part.split("&", 1)[0].split(" ", 1)[0]
    return None


class Viewer(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _token_ok(self):
        tok = _parse_token(self.path)
        if tok is None:
            return False
        return hmac.compare_digest(tok, TOKEN)

    def _origin_ok(self):
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        allowed = f"http://127.0.0.1:{PORT}"
        return origin in (allowed, f"http://localhost:{PORT}")

    def do_GET(self):
        if not self._token_ok():
            return self.send_error(403)
        if self.path.startswith("/stream"):
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=f")
            self.end_headers()
            try:
                while True:
                    j = encode(_E.frame(fresh=True), "JPEG", quality=CFG["jpeg_quality"])
                    self.wfile.write(b"--f\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(j) + j + b"\r\n")
                    time.sleep(1.0 / CFG["stream_fps"])
            except Exception:
                return
        if self.path.startswith("/approvals"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(_approval_queue).encode())
            return
        page = (
            f'<meta name=viewport content="width=device-width"><title>Agentseeai v2</title>'
            f'<body style="margin:0;background:#111;color:#eee;font-family:sans-serif">'
            f'<div style="padding:8px"><button onclick="act(\'stop\')">STOP</button> '
            f'<button onclick="act(\'resume\')">Resume</button> <span id=s></span></div>'
            f'<img style="width:100%" src="/stream?t={TOKEN}">'
            f'<script>'
            f'function act(a){{fetch("/"+a+"?t={TOKEN}",{{method:"POST",headers:{{"Origin":"http://127.0.0.1:{PORT}"}}}}).then(r=>r.text()).then(t=>document.getElementById("s").innerText=t);}}'
            f'</script></body>'
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(page)

    def do_POST(self):
        if not self._token_ok():
            return self.send_error(403)
        if not self._origin_ok():
            return self.send_error(403)
        if self.path.startswith("/stop"):
            STOP_FILE.write_text("stopped")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"stopped")
            return
        if self.path.startswith("/resume"):
            try:
                STOP_FILE.unlink()
            except FileNotFoundError:
                pass
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"resumed")
            return
        if self.path.startswith("/answer"):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode() if length else ""
            try:
                data = json.loads(body)
                aid = data.get("id")
                status = data.get("status", "approved")
                answer = data.get("answer")
                if aid in _approval_queue:
                    _approval_queue[aid]["status"] = status
                    _approval_queue[aid]["answer"] = answer
            except Exception:
                pass
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok")
            return
        self.send_error(404)


def start_viewer():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Viewer)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"Live view: http://127.0.0.1:{PORT}/?t={TOKEN}", file=sys.stderr)
    return srv


# ---------------------------------------------------------------- FastServer (v2 core)
"""Millisecond engine: ek persistent process, TCP localhost JSON-lines.

  agentseeai serve                     # window khula rakho
  -> ab HAR CLI call auto-forward hota hai is server pe:
     python startup (300-600 ms) GAYA, mss/imports warm, frames cached.
"""

_STATS = {"served": 0, "cache_hits": 0, "started": time.time()}


class _FastHandler(__import__("socketserver").BaseRequestHandler):
    def _read_line(self) -> Optional[bytes]:
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = self.request.recv(65536)
            if not chunk:
                return None if not buf else buf
            buf += chunk
        return buf

    def _send(self, obj: dict):
        try:
            self.request.sendall((json.dumps(obj) + "\n").encode())
        except Exception:
            pass

    def handle(self):
        while True:
            try:
                line = self._read_line()
            except Exception:
                return
            if line is None:
                return
            try:
                req = json.loads(line.decode("utf-8", errors="replace"))
            except Exception:
                self._send({"ok": False, "msg": "bad JSON"})
                continue
            t0 = time.time()
            try:
                with _lock:
                    resp = _fast_dispatch(req)
            except Exception as e:
                resp = {"ok": False, "msg": f"{type(e).__name__}: {e}"}
            resp["ms"] = round((time.time() - t0) * 1000.0, 2)
            _STATS["served"] += 1
            self._send(resp)


def _fast_dispatch(req: dict) -> dict:
    cmd = req.get("cmd", "")
    if cmd == "__ping":
        return {"ok": True, "msg": "pong", "uptime_s": round(time.time() - _STATS["started"], 1)}
    if cmd == "__stats":
        return {"ok": True, "msg": json.dumps(_STATS)}
    if cmd == "__shutdown":
        def _bye():
            time.sleep(0.05)
            try:
                _fast_server[0].shutdown()
                _fast_server[0].server_close()
            except Exception:
                pass
            if _serve_mode[0]:
                os._exit(0)  # serve process poori tarah band (port free)
        threading.Thread(target=_bye, daemon=True).start()
        return {"ok": True, "msg": "shutting down"}
    if cmd == "do":
        steps = (req.get("args") or [""])[0] if req.get("args") else ""
        img, msg = do(steps)
        out: dict = {"ok": True, "msg": str(msg)}
        if img is not None and req.get("want_img"):
            import base64 as _b64
            out["img_b64_png"] = _b64.b64encode(encode(img, "PNG")).decode()
        return out
    fn = CLI_NAMES.get(cmd)
    if fn is None:
        return {"ok": False, "msg": f"unknown command: {cmd}"}
    args = coerce_args(fn, req.get("args", []))
    kwargs = dict(req.get("kwargs") or {})
    img, msg = fn(*args, **kwargs)
    try:
        log_action(cmd, {"args": args, "kwargs": kwargs}, msg, changed=_extract_changed(msg))
    except Exception:
        pass
    out = {"ok": True, "msg": str(msg)}
    if img is not None and req.get("want_img"):
        import base64 as _b64
        out["img_b64_png"] = _b64.b64encode(encode(img, "PNG")).decode()
    return out


_fast_server = [None]
_serve_mode = [False]   # True jab `serve` command ka main process ho


class _FastTCPServer(__import__("socketserver").ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def start_fast_server(port: int = None, quiet: bool = False):
    import socketserver
    port = port or FAST_PORT
    srv = _FastTCPServer(("127.0.0.1", port), _FastHandler)
    _fast_server[0] = srv
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    if not quiet:
        print(f"fast server: 127.0.0.1:{port} (millisecond mode ON)")
    return srv


def fast_send(obj: dict, port: int = None, timeout: float = 60.0) -> Optional[dict]:
    """TCP JSON-lines client (CLI forward isi se karta hai)."""
    port = port or FAST_PORT
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout) as s:
            s.sendall((json.dumps(obj) + "\n").encode())
            data = b""
            while not data.endswith(b"\n"):
                chunk = s.recv(1 << 20)
                if not chunk:
                    break
                data += chunk
        if not data:
            return None
        return json.loads(data.decode("utf-8", errors="replace"))
    except Exception:
        return None


def fast_server_alive(port: int = None, timeout: float = 0.25) -> bool:
    resp = fast_send({"cmd": "__ping"}, port=port, timeout=timeout)
    return bool(resp and resp.get("ok"))


def cmd_serve(argv: List[str]):
    port = FAST_PORT
    if "--port" in argv:
        try:
            port = int(argv[argv.index("--port") + 1])
        except Exception:
            pass
    if fast_server_alive(port):
        print(f"fast server already running on 127.0.0.1:{port}")
        return 0
    _serve_mode[0] = True
    try:
        start_fast_server(port)
    except OSError as e:
        print(f"fast server start fail: {e}")
        print("TIP: `agentseeai server-stop` (zombie kill), ya --port doosra (e.g. --port 8701)")
        return 1
    if "--view" in argv:
        start_viewer()
    print("commands yahin se bhi chalein (nayi terminal me): agentseeai <cmd> ...")
    print("Ctrl+C = band. CLI bina server ke bhi chalti hai (slow mode).")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            _fast_server[0].server_close()
        except Exception:
            pass
    print("\nfast server band.")
    return 0


# ---------------------------------------------------------------- CLI (auto-forward)
def coerce_args(fn, argv_list: List[str]) -> list:
    """v1 logic: annotation-int/float/bool + int/float fallback."""
    params = list(inspect.signature(fn).parameters.values())
    args = []
    for pp, v in zip(params, argv_list):
        ann = pp.annotation
        if ann in (int, float):
            args.append(ann(v))
        elif ann == bool:
            args.append(str(v).lower() in ("true", "1", "yes"))
        else:
            try:
                args.append(int(v))
            except (ValueError, TypeError):
                try:
                    args.append(float(v))
                except (ValueError, TypeError):
                    args.append(v)
    return args


def _print_result(img, msg) -> None:
    print(msg)
    if img is not None:
        path = os.path.join(tempfile.gettempdir(), "agentseeai_last.png")
        try:
            img.save(path)
            print("screenshot:", path)
        except Exception:
            pass


def run_cli(argv):
    argv = [a for a in argv if a != "--local"]
    if not argv:
        print(__doc__)
        return 0
    cmd = argv[0]
    if cmd == "serve":
        return cmd_serve(argv[1:])
    if cmd == "server-stop":
        resp = fast_send({"cmd": "__shutdown"})
        print("server stopped" if resp and resp.get("ok") else "server nahi chal raha")
        return 0
    if cmd == "mcp":
        run_mcp()
        return 0
    if cmd == "view":
        start_viewer()
        threading.Event().wait()
        return 0
    if cmd == "events":
        if not EVENTS_PATH.exists():
            print("no events yet")
            return 0
        for line in EVENTS_PATH.read_text().splitlines()[-50:]:
            print(line)
        return 0
    if cmd == "doctor":
        return doctor()
    if cmd == "bench":
        return bench()
    if cmd == "version":
        print(f"agentseeai {VERSION} ({BUILD_DATE})")
        return 0
    if cmd not in CLI_NAMES and cmd != "do":
        print(f"unknown command: {cmd}")
        print("commands:", ", ".join(sorted(set(CLI_NAMES) | {"mcp", "serve", "view", "events", "doctor", "bench", "do", "server-stop"})))
        return 1

    # ---- v2 FAST PATH: running server pe forward (no process startup) ----
    if os.environ.get("AGENTSEEAI_NO_SERVER") != "1" and fast_server_alive():
        if cmd == "do":
            req = {"cmd": "do", "args": [argv[1] if len(argv) > 1 else ""], "want_img": True}
        else:
            req = {"cmd": cmd, "args": argv[1:], "want_img": True}
        resp = fast_send(req, timeout=float(CFG.get("agents_timeout_s", 120) or 120))
        if resp is not None:
            import base64 as _b64
            print(resp.get("msg", ""))
            img_b64 = resp.get("img_b64_png")
            if img_b64:
                path = os.path.join(tempfile.gettempdir(), "agentseeai_last.png")
                try:
                    with open(path, "wb") as f:
                        f.write(_b64.b64decode(img_b64))
                    print("screenshot:", path)
                except Exception:
                    pass
            print(f"[fast {resp.get('ms', '?')} ms]")
            return 0 if resp.get("ok") else 1
        print("[--] server gayab ho gaya — local mode me chal raha hoon")

    # ---- local mode (back-compat) ----
    if cmd == "do":
        try:
            img, msg = do(argv[1] if len(argv) > 1 else "")
        except ValueError as e:
            print(f"do: {e}")
            return 1
        _print_result(img, msg)
        return 0
    fn = CLI_NAMES[cmd]
    args = coerce_args(fn, argv[1:])
    img, msg = fn(*args)
    try:
        log_action(cmd, {"args": args}, msg, changed=_extract_changed(msg))
    except Exception:
        pass
    _print_result(img, msg)
    return 0


# ---------------------------------------------------------------- MCP (built-in stdio, no SDK dep)
def _schema_for(fn) -> dict:
    props: Dict[str, dict] = {}
    required = []
    for name, p in inspect.signature(fn).parameters.items():
        ann = p.annotation
        t = "string"
        if ann is int or ann is float:
            t = "integer" if ann is int else "number"
        elif ann is bool:
            t = "boolean"
        elif ann is inspect.Parameter.empty or ann is None:
            t = "string"
        props[name] = {"type": t}
        if p.default is inspect.Parameter.empty:
            required.append(name)
    return {"type": "object", "properties": props, "required": required}


_MCP_TOOLS = ["screenshot", "click", "move", "drag", "scroll", "hscroll", "type_text", "key", "hold_key",
              "mouse_down", "mouse_up", "cursor_position", "wait_change", "wait_for_text", "zoom", "record",
              "read_screen", "find_text", "click_text", "find_image", "pixel", "find_color",
              "elements", "windows", "focus", "open_app", "clip_get", "clip_set", "screens", "set_screen",
              "status", "ask_approval", "do"]


def _mcp_tool_entry(name: str) -> dict:
    fn = do if name == "do" else CLI_NAMES[name]
    doc = (fn.__doc__ or "").strip().splitlines()
    desc = doc[0] if doc else name
    return {"name": name, "description": desc, "inputSchema": _schema_for(fn)}


def _mcp_call(name: str, arguments: dict) -> dict:
    import base64 as _b64
    fn = do if name == "do" else CLI_NAMES.get(name)
    if fn is None:
        return {"content": [{"type": "text", "text": f"unknown tool: {name}"}], "isError": True}
    try:
        img, msg = fn(**arguments)
    except Exception as e:
        return {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}], "isError": True}
    try:
        log_action(name, arguments, msg, changed=_extract_changed(msg))
    except Exception:
        pass
    content = [{"type": "text", "text": str(msg)}]
    if img is not None:
        content.append({"type": "image", "data": _b64.b64encode(encode(img, "PNG")).decode(), "mimeType": "image/png"})
    return {"content": content}


def _rpc_result(resp_id, result):
    return json.dumps({"jsonrpc": "2.0", "id": resp_id, "result": result}) + "\n"


def _rpc_error(resp_id, code, message):
    return json.dumps({"jsonrpc": "2.0", "id": resp_id, "error": {"code": code, "message": message}}) + "\n"


def run_mcp():
    """MCP stdio server — built-in JSON-RPC (no `pip install mcp` needed).

    v1 me ye SDK pe depend karta tha; v2 me protocol andar hai.
    """
    if CFG.get("server_in_mcp", True) and not fast_server_alive():
        try:
            start_fast_server(quiet=True)  # CLI + MCP dono millisecond mode me
        except Exception:
            pass  # port busy ho to MCP phir bhi chalega (tools in-process hain)
    out = sys.stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            out.write(_rpc_error(None, -32700, "parse error"))
            out.flush()
            continue
        method = req.get("method", "")
        rid = req.get("id")
        if method == "initialize":
            result = {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "agentseeai", "version": VERSION},
            }
            out.write(_rpc_result(rid, result))
        elif method == "notifications/initialized":
            pass  # notification — no response
        elif method == "ping":
            out.write(_rpc_result(rid, {}))
        elif method == "tools/list":
            out.write(_rpc_result(rid, {"tools": [_mcp_tool_entry(n) for n in _MCP_TOOLS]}))
        elif method == "tools/call":
            params = req.get("params") or {}
            out.write(_rpc_result(rid, _mcp_call(params.get("name", ""), params.get("arguments") or {})))
        elif rid is not None:
            out.write(_rpc_error(rid, -32601, f"method not found: {method}"))
        out.flush()


# ---------------------------------------------------------------- doctor / bench
def doctor():
    """v2 doctor: asli probes + fast server + engine status."""
    alive = fast_server_alive()
    info = {
        "version": f"{VERSION} ({BUILD_DATE})",
        "platform": sys.platform,
        "python": sys.version.split()[0],
        "has_mss": _probe("mss"),
        "has_pyautogui": _probe("pyautogui"),
        "has_pillow": _probe("PIL"),
        "has_pyperclip": _probe("pyperclip"),
        "has_ocr_tess": _probe("pytesseract"),
        "has_ocr_rapid": _OCR._load_rapid() or _probe("rapidocr"),
        "ocr_engine_picked": _OCR._pick(),
        "has_cv2": _probe("cv2"),
        "has_ax": bool(ax_tree() and ax_tree()[0].role != "error"),
        "monitors": len(get_monitors()),
        "stop_file": STOP_FILE.exists(),
        "fast_server": f"{'RUNNING' if alive else 'not running'} (127.0.0.1:{FAST_PORT})",
        "speed_config": {
            "settle_ms": CFG.get("settle_ms"),
            "frame_ttl_ms": CFG.get("frame_ttl_ms"),
            "input_pause_ms": CFG.get("input_pause_ms"),
            "max_actions_per_minute": CFG.get("max_actions_per_minute"),
        },
    }
    print(json.dumps(info, indent=2))
    if not alive:
        print("\nTIP: `agentseeai serve` chalao -> phir har CLI call milliseconds me.")
    return 0


def bench():
    """Latency micro-benchmarks (jo sandbox/PC par napna mumkin hai)."""
    print(f"agentseeai v{VERSION} bench\n" + "-" * 46)
    t0 = time.perf_counter()
    _probe("mss"), _probe("PIL"), _probe("pyautogui")
    print(f"lazy imports probe        : {(time.perf_counter() - t0) * 1000:8.2f} ms")
    t0 = time.perf_counter()
    img = _E.frame(fresh=True)
    print(f"frame (fresh, fallback)   : {(time.perf_counter() - t0) * 1000:8.2f} ms  -> {img.width}x{img.height}")
    t0 = time.perf_counter()
    _E.frame()
    print(f"frame (TTL cached)        : {(time.perf_counter() - t0) * 1000:8.2f} ms")
    t0 = time.perf_counter()
    _OCR.analyze(img)
    print(f"ocr ({_OCR._pick():8s})       : {(time.perf_counter() - t0) * 1000:8.2f} ms")
    t0 = time.perf_counter()
    _OCR.analyze(img)
    print(f"ocr (hash cache hit)      : {(time.perf_counter() - t0) * 1000:8.2f} ms")
    alive = fast_server_alive()
    if alive:
        ts = []
        for _ in range(20):
            t0 = time.perf_counter()
            fast_send({"cmd": "__ping"})
            ts.append((time.perf_counter() - t0) * 1000)
        ts.sort()
        print(f"server round-trip (20x)   : median {ts[len(ts) // 2]:8.2f} ms | min {ts[0]:.2f}")
    else:
        print("server round-trip         : (server band hai — `agentseeai serve` chalao)")
    t0 = time.perf_counter()
    fuzzy_rank("login", ["Login", "Logout", "Sign in", "Sign up"], limit=3)
    print(f"fuzzy rank (4 candidates) : {(time.perf_counter() - t0) * 1000:8.2f} ms")
    print("-" * 46)
    return 0


def main():
    sys.exit(run_cli(sys.argv[1:]) or 0)


if __name__ == "__main__":
    main()


