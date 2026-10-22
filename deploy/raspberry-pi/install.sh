#!/usr/bin/env bash
# Install a systemd timer that runs collect.sh twice an hour as the current user.
# Run from the repository clone on the Pi:  bash deploy/raspberry-pi/install.sh
set -euo pipefail
repo="$(cd "$(dirname "$0")/../.." && pwd)"
user="$(id -un)"

if [[ "$user" == "root" ]]; then
  echo "Run this as the user that owns the clone, not as root (it asks for sudo)." >&2
  exit 1
fi
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' \
  || { echo "Python 3.10 or newer is required; found $(python3 --version)." >&2; exit 1; }

sudo tee /etc/systemd/system/dubsfc-collector.service >/dev/null <<EOF
[Unit]
Description=Collect DubsFC Tracker matches and push them to GitHub
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
User=$user
WorkingDirectory=$repo
ExecStart=/usr/bin/env bash $repo/deploy/raspberry-pi/collect.sh
TimeoutStartSec=15min
Nice=10
EOF

# :07 and :37 past each hour, plus up to two minutes of random delay. Persistent
# runs a missed collection soon after the Pi starts again. A run still in
# progress is never started twice.
sudo tee /etc/systemd/system/dubsfc-collector.timer >/dev/null <<EOF
[Unit]
Description=Run the DubsFC Tracker collector twice an hour

[Timer]
OnCalendar=*-*-* *:07,37:00
RandomizedDelaySec=120
Persistent=true

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now dubsfc-collector.timer
systemctl list-timers dubsfc-collector.timer --no-pager
echo "Installed. Logs: journalctl -u dubsfc-collector -n 50 --no-pager"
