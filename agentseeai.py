#!/usr/bin/env python3
"""
Agentseeai - eyes and hands for ANY AI agent. No AI and no API key inside.
The agent you already use (Claude Code, Codex, Gemini CLI, Cursor...) is the brain.

SETUP (one time)
    pip install mcp mss pyautogui pillow pyperclip pynput
    # Optional (OCR): pip install pytesseract
    # Optional (Windows a11y): pip install comtypes psutil uiautomation
    # Optional (macOS a11y): pip install atomacos
    # Optional (Linux a11y): pip install pyatspi python-xlib
    # Optional (cv): pip install opencv-python numpy

USE WITH AN MCP AGENT
    agentseeai mcp
    (Claude Code: claude mcp add agentseeai -- agentseeai mcp)

USE WITH ANY AGENT THAT HAS A TERMINAL
    agentseeai shot            -> saves screen, prints the image path
    agentseeai click 640 360
    agentseeai type "hello"
    agentseeai key ctrl+s
    agentseeai wait 8          -> returns when the screen changes
    agentseeai tree            -> dumps accessibility tree (text)
    agentseeai find "Login"    -> finds element by text, returns coords
    agentseeai click_text Login -> clicks element by text
    agentseeai events          -> tail JSON lines per screen change
    agentseeai doctor          -> report platform + deps

WATCH LIVE (you, in a browser)
    agentseeai view            -> prints http://127.0.0.1:8765/?t=TOKEN

SAFETY
    - Slam the mouse into any screen corner = instant abort.
    - Move the mouse yourself = agent pauses and must re-look.
    - touch ~/.agentseeai_stop = hard stop. Delete the file to resume.
    - Viewer is read-only control, localhost only, needs a random token.
    - App allowlist/denylist + max actions per minute in config.

SCOPE (real desktop, no sandbox needed)
    - Controls your real desktop fully. Run elevated (Administrator) to also
      act on elevated windows. Only OS-protected things are off-limits: secure
      desktop (UAC/lock screen), DRM frames, anti-cheat input, macOS permission
      prompts (grant once), Wayland (needs ydotool/portal). Everything else is
      controllable. No Docker, no sandbox, no cloud, no API key.
    - Not truly real-time. Agent thinking time = seconds per step.
    - "Live" = instant feedback after every action, not 60 fps.
    - Windows/macOS/Linux X11. Wayland marked experimental.
    - Elevated/admin apps need elevated Agentseeai.
    - Primary monitor by default; multi-monitor via config.
"""
import asyncio, base64, functools, hashlib, hmac, io, json, logging, os, secrets, sys, tempfile, threading, time
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import mss
import pyautogui
from PIL import Image as PILImage

# Optional deps
try:
    import cv2, numpy as np
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False
    cv2 = None
    np = None

try:
    import pytesseract
    HAS_TESS = True
except ImportError:
    HAS_TESS = False

# Platform a11y
HAS_AX = False
if sys.platform == "win32":
    try:
        import comtypes.client
        import comtypes.gen.UIAutomationClient as UIA
        HAS_AX = True
    except ImportError:
        pass
    try:
        import win32gui, win32process, psutil
        HAS_WIN32 = True
    except ImportError:
        HAS_WIN32 = False
elif sys.platform == "darwin":
    try:
        import atomacos
        HAS_AX = True
    except ImportError:
        pass
    try:
        from AppKit import NSWorkspace
        HAS_APPKIT = True
    except ImportError:
        HAS_APPKIT = False
else:
    try:
        import gi
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
        HAS_AX = True
    except Exception:
        pass
    try:
        import subprocess as _sp
        HAS_XDOTOOL = True
    except ImportError:
        HAS_XDOTOOL = False

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.03

# DPI awareness on Windows
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

def _mss_factory():
    if hasattr(mss, "MSS"):
        return mss.MSS()
    return mss.mss()

CONFIG_PATH = Path.home() / ".agentseeai.json"
DEFAULT_CONFIG = {
    "max_width": 1280,
    "port": 8765,
    "monitor": 1,
    "allowlist": [],
    "denylist": ["Taskmgr.exe", "SystemSettings.exe"],
    "require_confirm": ["powershell.exe", "cmd.exe", "terminal", "sudo"],
    "settle_ms": 400,
    "change_threshold": 1.5,
    "jpeg_quality": 60,
    "stream_fps": 6,
    "log_actions": True,
    "log_path": str(Path.home() / ".agentseeai_actions.log"),
    "ocr_lang": "eng",
    "mouse_abort_corner": True,
    "mouse_pause_threshold": 8,
    "mouse_pause_wait": 1.5,
    "max_actions_per_minute": 60,
    "events_path": str(Path.home() / ".agentseeai_events.jsonl"),
}
CFG = {}
if CONFIG_PATH.exists():
    try:
        with open(CONFIG_PATH) as f:
            user = json.load(f)
        CFG = {**DEFAULT_CONFIG, **user}
    except Exception:
        CFG = DEFAULT_CONFIG.copy()
else:
    CFG = DEFAULT_CONFIG.copy()

MAXW = CFG["max_width"]
PORT = int(os.environ.get("AGENTSEEAI_PORT", CFG["port"]))
TOKEN = os.environ.get("AGENTSEEAI_TOKEN") or secrets.token_urlsafe(16)
STOP_FILE = Path.home() / ".agentseeai_stop"
MONITOR_IDX = CFG["monitor"]
EVENTS_PATH = Path(CFG["events_path"])

# Shared state with lock
_lock = threading.RLock()
_out = [0, 0]
_prev = [None]
_mouse = [None]
_session_log = []
_ocr_cache = {"hash": None, "text": None}
_approval_queue = {}  # id -> {"status": pending/approved/denied, "question": str}
_events = []
_action_times = []

logging.basicConfig(
    level=logging.INFO if CFG["log_actions"] else logging.WARNING,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(CFG["log_path"]) if CFG["log_path"] else logging.NullHandler(),
        logging.StreamHandler(sys.stderr),
    ],
)
log = logging.getLogger("agentseeai")


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


def _hash_img(img):
    return hashlib.md5(img.tobytes()).hexdigest()[:12]


def log_action(action, params, result):
    entry = {"t": time.time(), "action": action, "params": params, "result": (result or "")[:200]}
    _session_log.append(entry)
    if CFG["log_actions"]:
        log.info(f"{action}({params}) -> {str(result)[:120]}")
    # events
    try:
        with open(EVENTS_PATH, "a") as f:
            f.write(json.dumps({"t": entry["t"], "action": action, "changed": None}) + "\n")
    except Exception:
        pass


def check_rate_limit():
    now = time.time()
    window = [t for t in _action_times if now - t < 60]
    if len(window) >= CFG["max_actions_per_minute"]:
        raise RuntimeError(f"Rate limit: {CFG['max_actions_per_minute']} actions/min exceeded. Wait.")
    _action_times.append(now)
    # trim
    while len(_action_times) > 200:
        _action_times.pop(0)


# ---- Eyes ----
def get_monitors():
    try:
        with _mss_factory() as s:
            return s.monitors
    except Exception:
        return [{"left": 0, "top": 0, "width": 1280, "height": 800}]


def frame(monitor_idx=None):
    with _lock:
        idx = monitor_idx if monitor_idx is not None else MONITOR_IDX
        try:
            with _mss_factory() as s:
                mons = s.monitors
                if idx == 0:
                    imgs = []
                    for m in mons[1:]:
                        raw = s.grab(m)
                        img = PILImage.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
                        imgs.append((m["left"], m["top"], img))
                    min_x = min(x for x, _, _ in imgs)
                    min_y = min(y for _, y, _ in imgs)
                    max_x = max(x + img.width for x, _, img in imgs)
                    max_y = max(y + img.height for _, y, img in imgs)
                    combined = PILImage.new("RGB", (max_x - min_x, max_y - min_y))
                    for x, y, img in imgs:
                        combined.paste(img, (x - min_x, y - min_y))
                    raw_img = combined
                elif idx == -1:
                    raw = s.grab(mons[-1])
                    raw_img = PILImage.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
                elif 1 <= idx < len(mons):
                    raw = s.grab(mons[idx])
                    raw_img = PILImage.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
                else:
                    raw = s.grab(mons[1])
                    raw_img = PILImage.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        except Exception:
            # Fallback for displays mss cannot handle (e.g. 16-bit Xvfb in CI)
            raw_img = None
            for cmd in (["scrot", "-o", "/tmp/_ld_shot.png"],
                        ["gnome-screenshot", "-f", "/tmp/_ld_shot.png"]):
                try:
                    import subprocess as _sp
                    _sp.run(cmd, check=True, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL, timeout=10)
                    raw_img = PILImage.open("/tmp/_ld_shot.png").convert("RGB")
                    break
                except Exception:
                    continue
            if raw_img is None:
                try:
                    raw_img = pyautogui.screenshot()
                except Exception:
                    raw_img = PILImage.new("RGB", (1280, 800), (0, 0, 0))
        w = min(MAXW, raw_img.width)
        img = raw_img.resize((w, round(raw_img.height * w / raw_img.width)))
        _out[:] = [img.width, img.height]
        return img


def encode(img, fmt="PNG", **kw):
    b = io.BytesIO()
    img.save(b, fmt, **kw)
    return b.getvalue()


def thumb(img):
    return img.resize((96, 54)).convert("L").tobytes()


def change(img, commit=True):
    t, p = thumb(img), _prev[0]
    pct = 0.0 if p is None else sum(abs(a - b) for a, b in zip(t, p)) / (len(t) * 255) * 100
    if commit:
        with _lock:
            _prev[0] = t
    return round(pct, 1)


def ocr_text(img):
    if not HAS_TESS:
        return "OCR not available (install pytesseract)"
    h = _hash_img(img)
    if _ocr_cache["hash"] == h and _ocr_cache["text"] is not None:
        return _ocr_cache["text"]
    try:
        gray = img.convert("L")
        if gray.width < 1000:
            gray = gray.resize((gray.width * 2, gray.height * 2), PILImage.LANCZOS)
        text = pytesseract.image_to_string(gray, lang=CFG.get("ocr_lang", "eng"))
        text = text.strip()
        _ocr_cache["hash"] = h
        _ocr_cache["text"] = text
        return text
    except Exception as e:
        return f"OCR error: {e}"


# ---- Accessibility (elements) ----
@dataclass
class AXNode:
    id: str
    role: str
    name: str
    value: str
    bounds: Tuple[int, int, int, int]
    enabled: bool = True
    focused: bool = False
    selected: bool = False


def ax_tree():
    nodes = []
    if not HAS_AX:
        return [AXNode("no-ax", "error", "Accessibility not available", "", (0, 0, 0, 0), False, False, False)]
    if sys.platform == "win32":
        try:
            try:
                import pythoncom
                pythoncom.CoInitialize()
            except Exception:
                pass
            import uiautomation as auto
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
                    enabled = bool(el.IsEnabled)
                    focused = bool(el.HasKeyboardFocus)
                    val = ""
                    try:
                        val = el.GetValuePattern().Value or ""
                    except Exception:
                        pass
                    eid = f"{role}:{name}:{x}:{y}:{w}:{h}"[:120]
                    if w > 0 and h > 0:
                        nodes.append(AXNode(eid, role, name, val, (x, y, w, h), enabled, focused, False))
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
                    enabled = bool(el.AXEnabled) if hasattr(el, "AXEnabled") else True
                    focused = bool(el.AXFocused) if hasattr(el, "AXFocused") else False
                    val = str(el.AXValue) if el.AXValue is not None else ""
                    eid = f"{role}:{name}:{x}:{y}:{w}:{h}"[:120]
                    if w > 0 and h > 0:
                        nodes.append(AXNode(eid, role, name, val, (x, y, w, h), enabled, focused, False))
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

    interactive = {"button", "link", "menuitem", "checkbox", "radiobutton", "slider", "textbox", "combobox", "listitem", "tab", "pushbutton", "menu", "text", "edit", "document", "pane", "window", "dialog", "alert", "label", "image", "hyperlink", "dataitem", "treeitem"}
    def _norm_role(r):
        r = (r or "").lower()
        if r.endswith("control"):
            r = r[:-7]
        return r
    filtered = [n for n in nodes if _norm_role(n.role) in interactive and n.bounds[2] > 0 and n.bounds[3] > 0]
    # dedupe by id
    seen = set()
    out = []
    for n in filtered:
        if n.id in seen:
            continue
        seen.add(n.id)
        out.append(n)
    return out


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
    q = query.lower()
    for n in nodes:
        if q in n.name.lower() or q in n.value.lower() or q in n.id.lower():
            return n
    # fuzzy: contains any word
    for n in nodes:
        if any(w in n.name.lower() for w in q.split() if len(w) > 2):
            return n
    return None


# ---- Template matching ----
def find_template(screen, template_path, threshold=0.8):
    if not HAS_CV2:
        return None
    try:
        screen_cv = cv2.cvtColor(np.array(screen), cv2.COLOR_RGB2BGR)
        template = cv2.imread(template_path, cv2.IMREAD_COLOR)
        if template is None:
            return None
        result = cv2.matchTemplate(screen_cv, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        if max_val >= threshold:
            h, w = template.shape[:2]
            return (max_loc[0] + w // 2, max_loc[1] + h // 2)
    except Exception:
        pass
    return None


# ---- Guard / Safety ----
def guard():
    check_rate_limit()
    if STOP_FILE.exists():
        raise RuntimeError("Emergency stop file exists (~/.agentseeai_stop). Ask the human.")
    cur = pyautogui.position()
    if CFG["mouse_abort_corner"]:
        W, H = pyautogui.size()
        corners = [(cur.x < 50 and cur.y < 50), (cur.x > W - 50 and cur.y < 50),
                   (cur.x < 50 and cur.y > H - 50), (cur.x > W - 50 and cur.y > H - 50)]
        if any(corners):
            raise RuntimeError("Mouse in screen corner: ABORT")
    if _mouse[0] and abs(cur.x - _mouse[0][0]) + abs(cur.y - _mouse[0][1]) > CFG["mouse_pause_threshold"]:
        quiet_since, last = time.time(), cur
        while time.time() - quiet_since < CFG["mouse_pause_wait"]:
            time.sleep(0.2)
            now = pyautogui.position()
            if now != last:
                last, quiet_since = now, time.time()
        _mouse[0] = (last.x, last.y)
        raise RuntimeError("The human used the mouse, so I paused. Take a new screenshot and re-plan.")


def check_app_allowed():
    if not CFG["allowlist"] and not CFG["denylist"]:
        return True
    try:
        if sys.platform == "win32":
            import win32gui, win32process, psutil
            hwnd = win32gui.GetForegroundWindow()
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            p = psutil.Process(pid)
            name = p.name().lower()
        elif sys.platform == "darwin":
            from AppKit import NSWorkspace
            name = NSWorkspace.sharedWorkspace().frontmostApplication().localizedName().lower()
        else:
            import subprocess
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
    except Exception:
        return True


def to_screen(x, y):
    with _lock:
        if not _out[0]:
            frame()
        W, H = pyautogui.size()
        ox, oy = _out[0], _out[1]
    return round(x * W / ox), round(y * H / oy)


def act(fn, settle=None):
    guard()
    if not check_app_allowed():
        raise RuntimeError("Current app is denylisted or not in allowlist.")
    fn()
    p = pyautogui.position()
    _mouse[0] = (p.x, p.y)
    time.sleep((settle if settle is not None else CFG["settle_ms"]) / 1000)
    img = frame()
    pct = change(img)
    return img, f"done. screen changed {pct}%. image {img.width}x{img.height}"


# ---- Hands ----
def screenshot():
    guard()
    img = frame()
    pct = change(img, commit=False)
    return img, f"image {img.width}x{img.height}. changed {pct}% since last look"


def click(x: int, y: int, button: str = "left", clicks: int = 1):
    return act(lambda: pyautogui.click(*to_screen(x, y), clicks=clicks, button=button))


def move(x: int, y: int):
    return act(lambda: pyautogui.moveTo(*to_screen(x, y), duration=0.15))


def drag(x1: int, y1: int, x2: int, y2: int):
    def go():
        pyautogui.moveTo(*to_screen(x1, y1))
        pyautogui.dragTo(*to_screen(x2, y2), duration=0.4, button="left")
    return act(go)


def scroll(amount: int, x: int = None, y: int = None):
    def go():
        if x is not None and y is not None:
            pyautogui.moveTo(*to_screen(x, y))
        pyautogui.scroll(amount)
    return act(go)


def hscroll(amount: int, x: int = None, y: int = None):
    def go():
        if x is not None and y is not None:
            pyautogui.moveTo(*to_screen(x, y))
        pyautogui.hscroll(amount)
    return act(go)


def type_text(text: str):
    def go():
        if text.isascii():
            pyautogui.write(text, interval=0.01)
        else:
            import pyperclip
            pyperclip.copy(text)
            pyautogui.hotkey("command" if sys.platform == "darwin" else "ctrl", "v")
    return act(go)


_KEY_ALIASES = {"cmd": "command", "ctrl": "ctrl", "control": "ctrl", "win": "win", "super": "win", "alt": "alt", "option": "alt", "shift": "shift", "enter": "enter", "return": "enter", "esc": "esc", "escape": "esc", "tab": "tab", "space": "space", "backspace": "backspace", "delete": "delete", "del": "delete", "up": "up", "down": "down", "left": "left", "right": "right", "home": "home", "end": "end", "pageup": "pageup", "pagedown": "pagedown"}


def key(keys: str):
    parts = keys.lower().replace(" ", "").split("+")
    mapped = [_KEY_ALIASES.get(p, p) for p in parts]
    return act(lambda: pyautogui.hotkey(*mapped))


def hold_key(keys: str, duration: float = 1.0):
    parts = keys.lower().replace(" ", "").split("+")
    mapped = [_KEY_ALIASES.get(p, p) for p in parts]
    def go():
        for k in mapped:
            pyautogui.keyDown(k)
        time.sleep(float(duration))
        for k in reversed(mapped):
            pyautogui.keyUp(k)
    return act(go, settle=CFG["settle_ms"])


def mouse_down(button: str = "left", x: int = None, y: int = None):
    def go():
        if x is not None and y is not None:
            pyautogui.moveTo(*to_screen(x, y))
        pyautogui.mouseDown(button=button)
    return act(go)


def mouse_up(button: str = "left", x: int = None, y: int = None):
    def go():
        if x is not None and y is not None:
            pyautogui.moveTo(*to_screen(x, y))
        pyautogui.mouseUp(button=button)
    return act(go)


def cursor_position():
    guard()
    p = pyautogui.position()
    # map to scaled coords
    if _out[0]:
        W, H = pyautogui.size()
        sx = round(p.x * _out[0] / W)
        sy = round(p.y * _out[1] / H)
    else:
        sx, sy = p.x, p.y
    img = frame()
    return img, f"cursor @({sx},{sy}) scaled, real ({p.x},{p.y})"


def wait_change(timeout: float = 5.0):
    guard()
    base = _prev[0]
    end = time.time() + float(timeout)
    while time.time() < end:
        img = frame()
        t = thumb(img)
        d = 0 if base is None else sum(abs(a - b) for a, b in zip(t, base)) / (len(t) * 255) * 100
        if d > CFG["change_threshold"]:
            time.sleep(0.3)
            break
        time.sleep(0.2)
    img = frame()
    pct = change(img)
    return img, f"screen changed {pct}%. image {img.width}x{img.height}"


def wait_for_text(text: str, timeout: float = 10.0, interval: float = 0.5):
    guard()
    q = text.lower()
    end = time.time() + float(timeout)
    last = ""
    while time.time() < end:
        img = frame()
        t = ocr_text(img)
        if q in t.lower():
            pct = change(img)
            return img, f"FOUND '{text}' on screen. changed {pct}%"
        last = t
        time.sleep(float(interval))
    img = frame()
    pct = change(img)
    return img, f"NOT FOUND '{text}' within {timeout}s. last ocr: {last[:200]}"


def zoom(x: int, y: int, w: int = 200, h: int = 200):
    guard()
    img = frame()
    rx, ry = to_screen(x, y)
    # crop region in scaled space -> map to real
    W, H = pyautogui.size()
    ox, oy = _out[0], _out[1]
    cx = round(x * W / ox)
    cy = round(y * H / oy)
    cw = round(w * W / ox)
    ch = round(h * H / oy)
    left = max(0, cx - cw // 2)
    top = max(0, cy - ch // 2)
    right = min(W, left + cw)
    bottom = min(H, top + ch)
    # grab real region via mss (fallback to full screenshot crop)
    try:
        with _mss_factory() as s:
            mon = s.monitors[MONITOR_IDX if 1 <= MONITOR_IDX < len(s.monitors) else 1]
            region = {"left": mon["left"] + left, "top": mon["top"] + top, "width": right - left, "height": bottom - top}
            raw = s.grab(region)
            zimg = PILImage.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
    except Exception:
        full = pyautogui.screenshot()
        zimg = full.crop((left, top, right, bottom))
    zimg = zimg.resize((zimg.width * 2, zimg.height * 2), PILImage.NEAREST)
    return zimg, f"zoomed {zimg.width}x{zimg.height} around ({x},{y})"


def record(seconds: float = 3.0, fps: float = 2.0):
    guard()
    frames = []
    end = time.time() + float(seconds)
    while time.time() < end:
        img = frame()
        frames.append(img.copy())
        time.sleep(1.0 / float(fps))
    # build contact sheet
    if not frames:
        img = frame()
        return img, "no frames"
    cols = 4
    rows = (len(frames) + cols - 1) // cols
    tw, th = frames[0].width // 2, frames[0].height // 2
    sheet = PILImage.new("RGB", (cols * tw, rows * th), (0, 0, 0))
    for i, f in enumerate(frames):
        r, c = divmod(i, cols)
        sheet.paste(f.resize((tw, th)), (c * tw, r * th))
    return sheet, f"recorded {len(frames)} frames over {seconds}s -> contact sheet {sheet.width}x{sheet.height}"


def read_screen():
    guard()
    img = frame()
    text = ocr_text(img)
    return img, f"OCR ({len(text)} chars):\n{text[:4000]}"


def find_text(query: str):
    guard()
    img = frame()
    nodes = ax_tree()
    el = find_element(nodes, query)
    if el:
        x, y, w, h = el.bounds
        cx, cy = x + w // 2, y + h // 2
        if _out[0]:
            W, H = pyautogui.size()
            sx = round(cx * _out[0] / W)
            sy = round(cy * _out[1] / H)
        else:
            sx, sy = cx, cy
        return img, f"FOUND: {el.id} @({sx},{sy}) bounds({x},{y},{w},{h}) role={el.role} name='{el.name}'"
    # fallback OCR
    text = ocr_text(img)
    if query.lower() in text.lower():
        return img, f"FOUND in OCR text: '{query}' (use screenshot to locate)"
    return img, f"NOT FOUND: '{query}' in {len(nodes)} elements"


def click_text(query: str, button: str = "left", clicks: int = 1):
    guard()
    nodes = ax_tree()
    el = find_element(nodes, query)
    if not el:
        img = frame()
        return img, f"NOT FOUND: '{query}'"
    x, y, w, h = el.bounds
    cx, cy = x + w // 2, y + h // 2
    # convert real screen coords to scaled screenshot coords
    if _out[0]:
        W, H = pyautogui.size()
        sx = round(cx * _out[0] / W)
        sy = round(cy * _out[1] / H)
    else:
        sx, sy = cx, cy
    return click(sx, sy, button=button, clicks=clicks)


def elements():
    guard()
    nodes = ax_tree()
    text = ax_to_text(nodes)
    img = frame()
    return img, text


def windows():
    guard()
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
            return frame(), f"windows error: {e}"
    elif sys.platform == "darwin":
        try:
            import subprocess
            r = subprocess.check_output(["osascript", "-e", 'tell application "System Events" to get name of every process whose background only is false'], text=True)
            for name in r.strip().split(", "):
                out.append({"title": name})
        except Exception as e:
            return frame(), f"windows error: {e}"
    else:
        try:
            import subprocess
            out_ids = subprocess.check_output(["xdotool", "search", "--name", ""], text=True, stderr=subprocess.DEVNULL).split()
            for wid in out_ids:
                try:
                    name = subprocess.check_output(["xdotool", "getwindowname", wid], text=True, stderr=subprocess.DEVNULL).strip()
                    out.append({"id": wid, "title": name})
                except Exception:
                    pass
            if not out:
                # fallback wmctrl
                try:
                    r = subprocess.check_output(["wmctrl", "-l"], text=True, stderr=subprocess.DEVNULL)
                    for line in r.strip().splitlines():
                        parts = line.split(None, 3)
                        if len(parts) >= 4:
                            out.append({"id": parts[0], "desktop": parts[1], "title": parts[3]})
                except Exception:
                    pass
        except Exception as e:
            return frame(), f"windows error: {e}"
    img = frame()
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
            return frame(), f"focus error: {e}"
    elif sys.platform == "darwin":
        try:
            import subprocess
            subprocess.run(["osascript", "-e", f'tell application "{title_query}" to activate'], check=False)
        except Exception as e:
            return frame(), f"focus error: {e}"
    else:
        try:
            import subprocess
            # try by name then by class
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
                try:
                    subprocess.run(["xdotool", "windowraise", wids[0]], check=False, stderr=subprocess.DEVNULL)
                except Exception:
                    pass
            else:
                # fallback wmctrl
                r = subprocess.check_output(["wmctrl", "-l"], text=True, stderr=subprocess.DEVNULL)
                for line in r.strip().splitlines():
                    parts = line.split(None, 3)
                    if len(parts) >= 4 and q in parts[3].lower():
                        subprocess.run(["wmctrl", "-ia", parts[0]], check=False)
                        break
        except Exception as e:
            return frame(), f"focus error: {e}"
    time.sleep(0.3)
    img = frame()
    return img, f"focused '{title_query}'. changed {change(img)}%"


def open_app(name: str):
    guard()
    # allowlist/denylist check on app name
    deny = [d.lower() for d in CFG["denylist"]]
    if any(d in name.lower() for d in deny):
        return frame(), f"app '{name}' is denylisted"
    if CFG["allowlist"] and not any(a.lower() in name.lower() for a in CFG["allowlist"]):
        # allowlist is for foreground app; still try
        pass
    try:
        if sys.platform == "win32":
            import subprocess
            subprocess.Popen(["cmd", "/c", "start", "", name], shell=False)
        elif sys.platform == "darwin":
            import subprocess
            subprocess.Popen(["open", "-a", name])
        else:
            import subprocess
            subprocess.Popen([name])
    except Exception as e:
        return frame(), f"open_app error: {e}"
    time.sleep(1.0)
    img = frame()
    return img, f"opened '{name}'. changed {change(img)}%"


def clip_get():
    guard()
    try:
        import pyperclip
        text = pyperclip.paste()
    except Exception as e:
        return frame(), f"clipboard error: {e}"
    img = frame()
    return img, f"clipboard: {text[:2000]}"


def clip_set(text: str):
    guard()
    try:
        import pyperclip
        pyperclip.copy(text)
    except Exception as e:
        return frame(), f"clipboard error: {e}"
    img = frame()
    return img, f"clipboard set ({len(text)} chars)"


def screens():
    guard()
    mons = get_monitors()
    img = frame()
    return img, json.dumps(mons, indent=2)


def set_screen(idx: int):
    global MONITOR_IDX
    MONITOR_IDX = int(idx)
    CFG["monitor"] = MONITOR_IDX
    save_config(CFG)
    img = frame()
    return img, f"monitor set to {MONITOR_IDX}. image {img.width}x{img.height}"


def status():
    guard()
    img = frame()
    info = {
        "platform": sys.platform,
        "python": sys.version.split()[0],
        "monitor": MONITOR_IDX,
        "monitors": len(get_monitors()),
        "out_size": list(_out),
        "has_ax": HAS_AX,
        "has_tess": HAS_TESS,
        "has_cv2": HAS_CV2,
        "stop_file": STOP_FILE.exists(),
        "token": TOKEN,
        "port": PORT,
        "actions_logged": len(_session_log),
    }
    return img, json.dumps(info, indent=2)


def ask_approval(question: str, timeout: float = 60.0):
    guard()
    aid = secrets.token_urlsafe(6)
    _approval_queue[aid] = {"status": "pending", "question": question, "answer": None, "created": time.time()}
    # write to events
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
            img = frame()
            return img, f"approval {aid}: {st}. answer={ans}"
        time.sleep(0.5)
    _approval_queue[aid]["status"] = "timeout"
    img = frame()
    return img, f"approval {aid}: timeout after {timeout}s"


# ---- CLI helpers for approval via viewer ----
def _parse_token(path):
    # extract token from query
    if "t=" in path:
        part = path.split("t=", 1)[1]
        return part.split("&", 1)[0].split(" ", 1)[0]
    return None


# ---- Viewer ----
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
            return True  # same-origin / no origin header (CLI)
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
                    j = encode(frame(), "JPEG", quality=CFG["jpeg_quality"])
                    self.wfile.write(b"--f\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(j) + j + b"\r\n")
                    time.sleep(1.0 / CFG["stream_fps"])
            except Exception:
                return
        if self.path.startswith("/approvals"):
            import json as _j
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(_j.dumps(_approval_queue).encode())
            return
        page = (
            f'<meta name=viewport content="width=device-width"><title>Agentseeai</title>'
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


# ---- MCP Server ----
TOOLS = [screenshot, click, move, drag, scroll, hscroll, type_text, key, hold_key, mouse_down, mouse_up, cursor_position, wait_change, wait_for_text, zoom, record, read_screen, find_text, click_text, elements, windows, focus, open_app, clip_get, clip_set, screens, set_screen, status, ask_approval]
TOOL_NAMES = ["screenshot", "click", "move", "drag", "scroll", "hscroll", "type_text", "key", "hold_key", "mouse_down", "mouse_up", "cursor_position", "wait_change", "wait_for_text", "zoom", "record", "read_screen", "find_text", "click_text", "elements", "windows", "focus", "open_app", "clip_get", "clip_set", "screens", "set_screen", "status", "ask_approval"]


def _to_content(img, msg):
    import base64 as _b64
    data = _b64.b64encode(encode(img, "PNG")).decode()
    from mcp.types import ImageContent, TextContent
    return [TextContent(type="text", text=str(msg)), ImageContent(type="image", data=data, mimeType="image/png")]


def run_mcp():
    from mcp.server import MCPServer
    start_viewer()
    server = MCPServer("agentseeai")

    def make_wrapper(fn):
        def w(*a, **k):
            img, msg = fn(*a, **k)
            log_action(fn.__name__, {"args": a, "kwargs": k}, msg)
            return _to_content(img, msg)
        w.__name__ = fn.__name__
        w.__doc__ = fn.__doc__
        import inspect
        w.__signature__ = inspect.signature(fn)
        return w

    for fn in TOOLS:
        server.tool()(make_wrapper(fn))
    server.run("stdio")


# ---- CLI ----
CLI_NAMES = {
    "shot": screenshot, "screenshot": screenshot,
    "click": click, "move": move, "drag": drag, "scroll": scroll, "hscroll": hscroll,
    "type": type_text, "type_text": type_text, "key": key, "hold_key": hold_key,
    "mouse_down": mouse_down, "mouse_up": mouse_up, "cursor_position": cursor_position,
    "wait": wait_change, "wait_change": wait_change, "wait_for_text": wait_for_text,
    "zoom": zoom, "record": record, "read_screen": read_screen, "ocr": read_screen,
    "find": find_text, "find_text": find_text, "click_text": click_text,
    "tree": elements, "elements": elements,
    "windows": windows, "focus": focus, "open_app": open_app,
    "clip_get": clip_get, "clip_set": clip_set, "screens": screens, "set_screen": set_screen,
    "status": status, "ask_approval": ask_approval,
}


def run_cli(argv):
    if not argv:
        print(__doc__)
        return
    cmd = argv[0]
    if cmd == "mcp":
        run_mcp()
        return
    if cmd == "view":
        start_viewer()
        threading.Event().wait()
        return
    if cmd == "events":
        # tail events file
        if not EVENTS_PATH.exists():
            print("no events yet")
            return
        lines = EVENTS_PATH.read_text().splitlines()
        for line in lines[-50:]:
            print(line)
        return
    if cmd == "doctor":
        print(json.dumps({
            "platform": sys.platform,
            "python": sys.version.split()[0],
            "has_mss": True,
            "has_pyautogui": True,
            "has_pillow": True,
            "has_ocr": HAS_TESS,
            "has_cv2": HAS_CV2,
            "has_ax": HAS_AX,
            "monitors": len(get_monitors()),
            "stop_file": STOP_FILE.exists(),
        }, indent=2))
        return
    if cmd not in CLI_NAMES:
        print(f"unknown command: {cmd}")
        print("commands:", ", ".join(sorted(set(CLI_NAMES) | {"mcp", "view", "events", "doctor"})))
        return
    fn = CLI_NAMES[cmd]
    import inspect
    params = list(inspect.signature(fn).parameters.values())
    args = []
    for pp, v in zip(params, argv[1:]):
        ann = pp.annotation
        if ann in (int, float):
            args.append(ann(v))
        elif ann == bool:
            args.append(v.lower() in ("true", "1", "yes"))
        else:
            try:
                args.append(int(v))
            except (ValueError, TypeError):
                try:
                    args.append(float(v))
                except (ValueError, TypeError):
                    args.append(v)
    img, msg = fn(*args)
    path = os.path.join(tempfile.gettempdir(), "agentseeai_last.png")
    img.save(path)
    print(msg)
    print("screenshot:", path)


def main():
    run_cli(sys.argv[1:])


if __name__ == "__main__":
    main()
