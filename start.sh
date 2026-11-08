#!/bin/bash
set -euo pipefail
export DISPLAY=:99
cleanup() { trap - EXIT TERM INT; kill $(jobs -pr) 2>/dev/null || true; wait || true; }
trap cleanup EXIT TERM INT
rm -f /tmp/.X99-lock
Xvfb :99 -screen 0 1100x820x24 -nolisten tcp &
for i in {1..40}; do if xdpyinfo -display :99 >/dev/null 2>&1; then break; fi; sleep .25; done
openbox >/tmp/openbox.log 2>&1 &
x11vnc -display :99 -forever -shared -localhost -rfbport 5900 -nopw -quiet >/tmp/x11vnc.log 2>&1 &
websockify --web=/usr/share/novnc 127.0.0.1:6080 127.0.0.1:5900 >/tmp/websockify.log 2>&1 &
nginx -c /app/nginx.conf -g 'daemon off;' &
python -u /app/main.py &
wait -n
exit 1
