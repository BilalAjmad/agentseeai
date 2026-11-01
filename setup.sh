#!/bin/bash
set -e
echo "Installing Agentseeai dependencies..."
python3 -m pip install --upgrade pip
python3 -m pip install mss pyautogui pillow pyperclip pynput
echo ""
echo "Optional (for OCR): python3 -m pip install pytesseract"
echo "Optional (Linux UI tree): sudo apt install python3-gi gir1.2-atspi-2.0"
echo "Optional (macOS UI tree): python3 -m pip install atomacos"
echo ""
echo "Done. Test with: python3 agentseeai.py doctor"