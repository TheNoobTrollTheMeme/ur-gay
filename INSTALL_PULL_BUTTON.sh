#!/usr/bin/env bash
# Install desktop shortcut on a Raspberry Pi with a graphical desktop.
set -euo pipefail
repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
desktop="${XDG_DESKTOP_DIR:-$HOME/Desktop}"
mkdir -p "$desktop"
launcher="$desktop/Pull Robot Code.desktop"
# Desktop Exec uses double-quoted paths. Escape special characters in the path.
quote_desktop() {
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  s="${s//\$/\\$}"
  s="${s//\`/\\\`}"
  printf '"%s"' "$s"
}
script_path="$(quote_desktop "$repo/PULL_LATEST.sh")"
cat > "$launcher" <<EOF
[Desktop Entry]
Type=Application
Name=Pull Robot Code
Comment=Safely update this Raspberry Pi's robot code from GitHub
Exec=/usr/bin/env PULL_PAUSE=1 /bin/bash $script_path
Terminal=true
Icon=system-software-update
Categories=Development;
EOF
chmod +x "$launcher"
echo "Desktop launcher installed: $launcher"
echo "Double-click 'Pull Robot Code' on the Pi desktop to update."
echo "If your desktop requires it, right-click once and choose 'Allow Launching'."
