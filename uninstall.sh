#!/usr/bin/env bash
# Remove the Yoga Pen 2 bridge.
set -euo pipefail

BIN="$HOME/.local/bin/yoga-pen-bridge.py"
UNIT="$HOME/.config/systemd/user/yoga-pen-bridge.service"

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }

say "stopping and disabling the service"
systemctl --user disable --now yoga-pen-bridge.service 2>/dev/null || true
rm -f "$UNIT"
systemctl --user daemon-reload

say "removing the bridge"
rm -f "$BIN"

say "removing the udev rule (sudo)"
sudo rm -f /etc/udev/rules.d/70-yoga-pen.rules
sudo udevadm control --reload || true

say "done. The pen device disappears immediately; nothing under /etc remains."
