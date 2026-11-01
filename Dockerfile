FROM debian:12-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV DISPLAY=:99

RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb x11vnc openbox xterm \
    python3 python3-pip python3-tk \
    tesseract-ocr \
    wmctrl xdotool \
    firefox-esr \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --break-system-packages --no-cache-dir \
    mcp mss pyautogui pillow pyperclip pynput pytesseract opencv-python-headless numpy

# app
COPY livedesk.py /opt/livedesk/livedesk.py
RUN chmod +x /opt/livedesk/livedesk.py

# startup script
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 5900 8765

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]