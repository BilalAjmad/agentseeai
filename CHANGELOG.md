# Changelog

## [2.1.0] - 2026-11-21

### Fixed (from code review)
- Multi-monitor: to_screen now uses captured monitor bounds/offset (was primary-screen only)
- Corner abort threshold 50px -> 10px (fewer false aborts on edge clicks)
- guard() no longer runs rate-limit/mouse-pause in read-only ops (screenshot/wait/find no longer blocked)
- Rate limiter now thread-safe (lock)
- Fast server dispatch serialized with _lock (mss thread safety)
- RapidOCR: handle non-tuple return + fallback to tesseract on failure
- find_element: best-match ranking, enabled-only, no id/coordinate matching
- type_text: clipboard save/restore + settle delay (race fix)
- log_action: redact params for sensitive actions (passwords not logged); events now record changed%
- Accessibility tree: removed noisy pane/window/image roles
- Change detection: larger thumbnail (160x90) and lower threshold (0.5%)

## [2.0.0] - 2026-11-21
## [0.1.0] - 2026-10-31

### Added
- 29 tools: screenshot, click, move, drag, scroll, hscroll, type_text, key, hold_key, mouse_down, mouse_up, cursor_position, wait_change, wait_for_text, zoom, record, read_screen, find_text, click_text, elements, windows, focus, open_app, clip_get, clip_set, screens, set_screen, status, ask_approval
- MCP stdio server (works with Claude Code, Cursor, Codex, Gemini CLI)
- CLI twin for every tool plus mcp/view/events/doctor commands
- Live viewer at localhost:8765 (MJPEG stream, STOP/Resume, Approve/Deny)
- Safety: mouse-corner failsafe, human-takeover pause, ~/.agentseeai_stop hard stop, app allow/denylist, rate limit, action log
- OCR cache by frame hash
- DPI awareness on Windows
- RLock on shared state
- Cross-OS key aliases (cmd/ctrl/win/super)
- Windows UIA (uiautomation), Linux AT-SPI, macOS AX support
- Docker sandbox (optional)
- GitHub Actions CI (Ubuntu/Xvfb + Windows/macOS smoke)
- 20 pytest tests

### Security
- hmac.compare_digest token check
- Origin check on POST requests
- POST for stop/resume/answer (not GET)