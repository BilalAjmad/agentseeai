# agentseeai v2.0.0 — THE FAST EDITION (2026-11-21)

Eyes + hands for ANY AI agent. No AI, no API key, 100% local.
**v2 = millisecond mode**: har action jo v1 me 400-700 ms leta tha, wo ab **0.2-5 ms**.

```
         v1 (slow)                      v2 (FAST)
  har CLI call = naya python      ek persistent fast server (127.0.0.1:8700)
  process + imports + mss naya    imports warm + mss zinda + frames cached
  -> 400-700 ms har kaam          -> 0.2-5 ms har kaam (measured, neeche)
```

## Quick start

```bash
pip install mss pyautogui pillow pyperclip          # base
pip install pytesseract                             # OCR (Tesseract binary bhi)
pip install rapidocr-onnxruntime                     # (optional) bohot fast OCR
pip install opencv-python numpy                      # (optional) find_image

agentseeai serve        # <-- ISKO window me chala rakho (millisecond mode ON)
agentseeai shot         # ab HAR command auto-forward -> [fast 2.1 ms]
agentseeai click 640 360
agentseeai click_text Login      # AX na ho to OCR se click karta hai (v2 NEW)
agentseeai do '[{"cmd":"click","args":[640,360],"no_look":true},{"cmd":"type","args":["hello"]}]'
```

Server band ho? Koi baat nahi — CLI waise hi chalti hai (local slow mode, full back-compat).

## Kya tez hua (asli numbers)

| Kaam | v1 | v2 | Kyun |
|---|---|---|---|
| CLI process startup | 300-600 ms | **0** | naya process hi nahi — server pe forward |
| Screenshot (mss) | 50-90 ms | 30-50 ms | persistent mss instance |
| Wahi frame dobara | 50-90 ms | **0.00 ms** | TTL cache (reads reuse) |
| OCR (full screen) | 400-1600 ms | same pehli baar | hash cache -> **~0 ms** dobara |
| OCR region (v2) | — | 5-10x faster | sirf wahi hissa padho |
| settle (har action ke baad) | 400 ms | **120 ms** (config) | adaptive |
| typing 100 chars | ~1 s | **~20 ms** | clipboard paste |
| open_app | fixed 1 s | adaptive | pehli change par turant |
| move/drag | 150-400 ms | ~0 ms | duration 0 |
| **Network round-trip (server)** | — | **median 0.21 ms** | sandbox me 50x measured |
| **bridge (agentvoiceai) -> server** | — | **median 0.15 ms** | real integration measured |

Sandbox bench (fallback black-frame, real numbers PC par aur behtar):
```
frame (TTL cached)   :     0.00 ms
ocr (hash cache hit) :     5.67 ms   (pehli baar 1159 ms -> 200x faster dobara)
server round-trip    :     median 0.21 ms
```
Apne PC par khud dekho: `agentseeai bench`

## Naya kya (smart + powerful)

- **`serve`** — fast server; CLI auto-detect kar ke forward karta hai (`--local` / `AGENTSEEAI_NO_SERVER=1` se disable)
- **`do` batch** — ek call me poora macro: `[{"cmd":...},...]`, `no_look:true` steps screenshot skip (chain ms-level)
- **`click_text` ab OCR-fallback** — accessibility tree na ho to bhi word ke coordinates pe click (games, canvas, Linux bhi)
- **fuzzy matching** — "Logn" bhi "Login" dhoondhta hai (rapidfuzz installed ho to aur behtar)
- **`find_text` top-k + coords** — AX + OCR dono, score ke saath
- **`read_screen region`** — `read_screen x y w h` sirf wahi hissa OCR (5-10x faster)
- **`find_image`** — template/image matching (cv2) — icons jo text nahi rakhte
- **`pixel` / `find_color`** — color-based detection (numpy fast)
- **MCP built-in** — `agentseeai mcp` bina `pip install mcp` ke (JSON-RPC stdio andar implement hai; 33 tools)
- **AX tree TTL cache** — Windows UIA walk jo 1-5 s leta tha, ab 500 ms cache
- **`bench` / `doctor` v2** — latency numbers + engine status live dikhta hai
- **lazy imports** — module import ~1 ms (v1: 200-400 ms kyunki pyautogui/mss top-level pe the)

Back-compat: v1 ka har command, naam, output format, MCP tool, safety (corner abort, stop-file, rate-limit, allow/deny) — sab waise hi. Purana config file chalta rahega (naye speed keys defaults se aayenge; purani `settle_ms: 400` override karti hai isliye fast mode ke liye use 120 ya hata do).

## Config (`~/.agentseeai.json`)

```json
{
  "settle_ms": 120,
  "frame_ttl_ms": 250,
  "ax_ttl_ms": 500,
  "fast_port": 8700,
  "input_pause_ms": 0,
  "move_duration_ms": 0,
  "clipboard_min_chars": 60,
  "max_actions_per_minute": 240,
  "ocr_engine": "auto",
  "open_app_adaptive": true
}
```

## agentvoiceai ke saath (voice mode)

agentvoiceai v1.2+ bridge khud fast server pe baat karta hai (`hands.fast_server=true`), aur `agentvoiceai setup` server ko background me khud chala deta hai. Voice command "chrome kholo" ka hands-part: **~1 ms** (v1: ~500 ms).

## Troubleshooting

| Masla | Hal |
|---|---|
| `agentseeai serve` me "address in use" | ya to purana chal raha hai (theek hai), ya `agentseeai server-stop` |
| `[fast ... ms]` print nahi ho raha | server band hai — `serve` chalao; ya `AGENTSEEAI_NO_SERVER=1` set hai |
| OCR bohot slow | `pip install rapidocr-onnxruntime` + `"ocr_engine": "auto"`; region OCR use karo |
| click_text kaam nahi kar raha | OCR engine "off" hai — pytesseract/rapidocr install; ya text nahi hai (icons) — `find_image` use karo |
| Windows pe coordinates off | DPI scaling — v2 khud DPI-aware hai; nahi to display scale 100% karo |
| Game me click nahi lagta | anti-cheat input block hai (by design off-limits) |

## Tests

```
cd agentseeai-v2 && python -m pytest tests/ -q    # 19 tests, headless-safe
python agentseeai.py doctor && python agentseeai.py bench
```

License: MIT — (c) 2026 Bilal Ahmad.
