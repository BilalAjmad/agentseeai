import os, sys, time, subprocess
import pytest
import pyautogui

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import agentseeai

os.environ.setdefault("DISPLAY", ":99")

@pytest.fixture(autouse=True)
def reset_mouse():
    try:
        agentseeai._mouse[0] = None
        pyautogui.moveTo(640, 400)
    except Exception:
        pass
    time.sleep(0.1)
    yield


def test_doctor_runs():
    mons = agentseeai.get_monitors()
    assert len(mons) >= 1
    assert agentseeai.MAXW > 0


def test_screenshot_returns_image():
    img, msg = agentseeai.screenshot()
    assert img is not None
    assert img.width > 0 and img.height > 0
    assert "image" in msg


def test_cursor_position():
    img, msg = agentseeai.cursor_position()
    assert "cursor" in msg


def test_status():
    img, msg = agentseeai.status()
    import json
    info = json.loads(msg)
    assert info["platform"] in ("linux", "win32", "darwin")
    assert "monitors" in info


def test_screens():
    img, msg = agentseeai.screens()
    assert "[" in msg or "{" in msg


def test_type_and_key_e2e(tmp_path):
    # open xterm
    subprocess.Popen(["xterm", "-geometry", "80x24+10+10"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)
    agentseeai.focus("XTerm")
    time.sleep(0.5)
    target = "/tmp/ldtest_e2e.txt"
    try:
        os.remove(target)
    except FileNotFoundError:
        pass
    agentseeai.type_text("echo agentseeai_e2e > " + target)
    agentseeai.key("enter")
    time.sleep(1.0)
    assert os.path.exists(target), "file not created"
    assert "agentseeai_e2e" in open(target).read()


def test_click_runs():
    img, msg = agentseeai.click(100, 100)
    assert "done" in msg or "screen changed" in msg


def test_move_runs():
    img, msg = agentseeai.move(200, 200)
    assert "done" in msg


def test_scroll_runs():
    img, msg = agentseeai.scroll(3)
    assert "done" in msg


def test_hscroll_runs():
    img, msg = agentseeai.hscroll(2)
    assert "done" in msg


def test_drag_runs():
    img, msg = agentseeai.drag(100, 100, 200, 200)
    assert "done" in msg


def test_zoom_runs():
    img, msg = agentseeai.zoom(300, 300, 100, 100)
    assert "zoomed" in msg


def test_record_runs():
    img, msg = agentseeai.record(1.0, 2.0)
    assert "recorded" in msg


def test_read_screen_runs():
    img, msg = agentseeai.read_screen()
    assert "OCR" in msg


def test_wait_change_runs():
    img, msg = agentseeai.wait_change(1.0)
    assert "screen changed" in msg


def test_clip_roundtrip():
    agentseeai.clip_set("hello from test")
    img, msg = agentseeai.clip_get()
    assert "hello from test" in msg


def test_guard_stop_file(tmp_path, monkeypatch):
    stop = agentseeai.STOP_FILE
    stop.write_text("stopped")
    try:
        with pytest.raises(RuntimeError, match="stop file"):
            agentseeai.screenshot()
    finally:
        try:
            stop.unlink()
        except FileNotFoundError:
            pass


def test_human_takeover():
    img, msg = agentseeai.click(640, 400)
    import pyautogui
    pyautogui.moveTo(300, 300)
    time.sleep(0.2)
    with pytest.raises(RuntimeError, match="paused"):
        agentseeai.click(640, 400)


def test_corner_failsafe():
    img, msg = agentseeai.click(640, 400)
    import pyautogui
    pyautogui.moveTo(5, 5)
    time.sleep(0.1)
    with pytest.raises(RuntimeError, match="corner"):
        agentseeai.click(640, 400)


def test_ask_approval_flow():
    import pyautogui
    pyautogui.moveTo(640, 400)
    time.sleep(0.2)
    agentseeai.start_viewer()
    time.sleep(1.0)
    token = agentseeai.TOKEN
    port = agentseeai.PORT

    def worker():
        agentseeai.ask_approval("approve?", timeout=5.0)

    import threading
    t = threading.Thread(target=worker, daemon=True)
    t.start()
    time.sleep(1.0)
    q = agentseeai._approval_queue
    aid = list(q.keys())[-1]
    import urllib.request
    req = urllib.request.Request(
        "http://127.0.0.1:%d/answer?t=%s" % (port, token),
        data=('{"id":"%s","status":"approved","answer":"yes"}' % aid).encode(),
        headers={"Origin": "http://127.0.0.1:%d" % port, "Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req)
    t.join(timeout=5)
    assert q[aid]["status"] == "approved"