#!/bin/bash
set -e

# start virtual display
Xvfb :99 -screen 0 1280x800x24 -ac &
sleep 2
openbox &
sleep 1

# start vnc (optional, for human viewing) - skip if not installed
if command -v x11vnc >/dev/null 2>&1; then
    x11vnc -display :99 -nopw -listen 0.0.0.0 -xkb -forever -shared >/tmp/x11vnc.log 2>&1 &
    sleep 1
fi

# start agentseeai viewer
export DISPLAY=:99
python3 /opt/agentseeai/agentseeai.py view > /tmp/viewer.log 2>&1 &
sleep 2
cat /tmp/viewer.log

# keep container alive
tail -f /dev/null