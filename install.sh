#!/usr/bin/env bash
# Install the Yoga Pen 2 bridge. Safe to re-run: nothing is copied that is
# already correct, and sudo is only used for files that actually need changing.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$HOME/.local/bin/yoga-pen-bridge.py"
UNIT="$HOME/.config/systemd/user/yoga-pen-bridge.service"
RULE=/etc/udev/rules.d/70-yoga-pen.rules

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }

if [ "$(id -u)" -eq 0 ]; then
    echo "Run this as your normal user, not as root: it installs a user service." >&2
    exit 1
fi

if cmp -s "$REPO_DIR/src/yoga-pen-bridge.py" "$BIN"; then
    say "bridge already up to date"
else
    say "installing bridge to $BIN"
    install -Dm755 "$REPO_DIR/src/yoga-pen-bridge.py" "$BIN"
fi

if cmp -s "$REPO_DIR/systemd/yoga-pen-bridge.service" "$UNIT"; then
    say "systemd unit already up to date"
else
    say "installing systemd --user unit"
    install -Dm644 "$REPO_DIR/systemd/yoga-pen-bridge.service" "$UNIT"
fi

if cmp -s "$REPO_DIR/udev/70-yoga-pen.rules" "$RULE" 2>/dev/null; then
    say "udev rule already up to date"
else
    say "installing udev rule (sudo)"
    sudo install -Dm644 "$REPO_DIR/udev/70-yoga-pen.rules" "$RULE"
    sudo udevadm control --reload
    sudo udevadm trigger --subsystem-match=hidraw --action=change
    sudo udevadm trigger --subsystem-match=input --action=change
fi

say "enabling the user service"
systemctl --user daemon-reload
systemctl --user enable --now yoga-pen-bridge.service

for _ in 1 2 3 4 5 6 7 8 9 10; do
    sleep 0.5
    systemctl --user is-active --quiet yoga-pen-bridge.service && break
done

say "service: $(systemctl --user is-active yoga-pen-bridge.service)"
if grep -q 'Yoga Pen 2' /proc/bus/input/devices 2>/dev/null; then
    say "pen device present:"
    grep -A2 'Yoga Pen 2' /proc/bus/input/devices | sed 's/^/    /'
else
    say "no pen device yet - check: journalctl --user -u yoga-pen-bridge -n 30"
fi
