# Contributing to agentseeai

Thanks for your interest. Contributions are welcome.

## How to contribute

1. Fork the repo and create a branch: `git checkout -b feature/my-change`
2. Make your changes in `agentseeai.py` (single file core) or `tests/test_agentseeai.py`.
3. Run tests: `python -m pytest -q` (on Xvfb on Linux, or real display on Windows/macOS).
4. Keep code simple: fewest files, fewest dependencies, shortest code that works.
5. Open a PR with a clear description of what changed and why.

## Guidelines

- No AI model, no API key, no cloud inside the core. agentseeai is eyes and hands only.
- Every action must return (new screenshot, "changed X%").
- Safety first: mouse-corner failsafe, human-takeover pause, stop file must keep working.
- Test on a real display (or Xvfb) before submitting. Do not say "works" for untested code.
- Code and comments in English. Talk to users in their language.

## Reporting bugs

Use the bug report issue template. Include OS, Python version, and steps to reproduce.