#!/usr/bin/env bash
# One-time setup. Run from a clone of codex-pi-safe-setup on the Raspberry Pi.
# Only copies the fixed updater and installs a user-level timer.
set -Eeuo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/pi_sync.sh"
[[ -f "$SRC" ]] || { echo 'Missing pi_sync.sh' >&2; exit 1; }
command -v systemctl >/dev/null || { echo 'systemd required for timer setup' >&2; exit 1; }
command -v flock >/dev/null || { echo 'flock required for lock handling' >&2; exit 1; }
mkdir -p "$HOME/.local/bin" "$HOME/.config/systemd/user"
install -m 0755 "$SRC" "$HOME/.local/bin/robot-ai-sync"
cat > "$HOME/.config/systemd/user/robot-ai-sync.service" <<'EOF'
[Unit]
Description=Stage Codex robot-arm source updates (NO motor execution)
[Service]
Type=oneshot
ExecStart=%h/.local/bin/robot-ai-sync
EOF
cat > "$HOME/.config/systemd/user/robot-ai-sync.timer" <<'EOF'
[Unit]
Description=Check robot-arm development updates every 15 minutes
[Timer]
OnBootSec=2min
OnUnitActiveSec=15min
Persistent=true
Unit=robot-ai-sync.service
[Install]
WantedBy=timers.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now robot-ai-sync.timer
systemctl --user start robot-ai-sync.service
echo 'Installed. Source updates go ONLY to ~/robot-ai-workspace.'
echo 'View status: systemctl --user status robot-ai-sync.timer'
echo 'View logs: journalctl --user -u robot-ai-sync.service -n 30 --no-pager'
echo 'User-level systemd timers may stop after logout unless lingering is enabled.'
