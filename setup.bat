@echo off
echo Installing Agentseeai dependencies...
python -m pip install --upgrade pip
python -m pip install mss pyautogui pillow pyperclip pynput
echo.
echo Optional (for OCR): python -m pip install pytesseract
echo Optional (Windows UI tree): python -m pip install uiautomation
echo.
echo Done. Test with: python agentseeai.py doctor
pause