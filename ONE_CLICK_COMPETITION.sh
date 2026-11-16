#!/usr/bin/env bash
# One-click updater + terminal launcher for ENGG1101 ROBOARM.
# Updates the ur-gay checkout, backs up replaced installed scripts,
# and opens Script 6's menu. It never starts a motor movement by itself.
set -euo pipefail

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
pause_on_exit="${ONE_CLICK_PAUSE:-0}"

finish() {
    local status=$?
    if [[ "$pause_on_exit" == "1" ]]; then
        echo
        read -r -p "Press Enter to close this window..." _ || true
    fi
    exit "$status"
}
trap finish EXIT

if [[ "${1:-}" == "--install-desktop" ]]; then
    desktop="${XDG_DESKTOP_DIR:-$HOME/Desktop}"
    mkdir -p -- "$desktop"
    shortcut="$desktop/ROBOARM Competition.desktop"
    # Escape characters that are special inside a Desktop Entry quoted Exec argument.
    quoted_path="${repo//\\/\\\\}"
    quoted_path="${quoted_path//\"/\\\"}"
    quoted_path="${quoted_path//\$/\\$}"
    quoted_path="${quoted_path//\`/\\\`}"
    cat > "$shortcut" <<EOF
[Desktop Entry]
Type=Application
Name=ROBOARM Competition
Comment=Pull code, safely back up and deploy scripts, open Script 6 menu
Exec=/usr/bin/env ONE_CLICK_PAUSE=1 /bin/bash "$quoted_path/ONE_CLICK_COMPETITION.sh"
Terminal=true
Icon=applications-engineering
Categories=Education;Development;
EOF
    chmod +x -- "$shortcut"
    echo "Desktop shortcut installed: $shortcut"
    echo "Double-click it to update and open the Script 6 menu."
    exit 0
fi

if [[ $# -ne 0 ]]; then
    echo "Usage: bash ONE_CLICK_COMPETITION.sh [--install-desktop]" >&2
    exit 2
fi

echo "== ROBOARM: update code, then open Script 6 =="
[[ -f "$repo/PULL_LATEST.sh" ]] || {
    echo "ERROR: PULL_LATEST.sh missing from $repo" >&2
    exit 1
}
PULL_PAUSE=0 bash "$repo/PULL_LATEST.sh"

if [[ -n "${ARM_PROJECT_DIR:-}" ]]; then
    project="${ARM_PROJECT_DIR%/}"
else
    project=""
    for candidate in \
        "$HOME/robot-arm-lab/STS3215-5DOF-Robot-Arm" \
        "$HOME/STS3215-5DOF-Robot-Arm" \
        "$HOME/robot-arm-lab" \
        "$repo"; do
        if [[ -f "$candidate/arm_settings.py" && -f "$candidate/scripts/05_waypoint_sequence.py" ]]; then
            project="$candidate"
            break
        fi
    done
fi

if [[ -z "$project" || ! -f "$project/arm_settings.py" ||
      ! -f "$project/arm_helpers.py" || ! -f "$project/terminal_keys.py" ||
      ! -f "$project/scripts/05_waypoint_sequence.py" ]]; then
    echo "ERROR: Could not find a complete installed STS3215 arm project." >&2
    echo "Specify its path, for example:" >&2
    echo "ARM_PROJECT_DIR=~/robot-arm-lab/STS3215-5DOF-Robot-Arm bash '$repo/ONE_CLICK_COMPETITION.sh'" >&2
    exit 1
fi

# Deploy only the linked launcher/playback dependencies; never modify motions/*.json.
scripts=(
    06_live_grid_tool.py
    competition_queue.py
    playback_speed.py
    raw_playback_validation.py
    raw_capture_conversion.py
    validated_raw_save.py
    arm_board.py
    arm_board_terminal.py
    smash_joycon.py
)

for file in "${scripts[@]}"; do
    [[ -f "$repo/scripts/$file" ]] || {
        echo "ERROR: Updated source missing scripts/$file; nothing deployed." >&2
        exit 1
    }
done

# Check Python syntax before overwriting any installed file.
python3 - "$repo" "${scripts[@]}" <<'PY'
from pathlib import Path
import sys
root = Path(sys.argv[1]) / "scripts"
for name in sys.argv[2:]:
    path = root / name
    compile(path.read_bytes(), str(path), "exec")
print("Source syntax preflight: OK")
PY

timestamp="$(date +%Y%m%d-%H%M%S)"
backup_dir="$project/.ur-gay-script-backups/$timestamp"
changed=0
for file in "${scripts[@]}"; do
    source="$repo/scripts/$file"
    target="$project/scripts/$file"
    if [[ "$source" == "$target" ]] || cmp -s -- "$source" "$target"; then
        continue
    fi
    if [[ -e "$target" ]]; then
        mkdir -p -- "$backup_dir"
        cp -p -- "$target" "$backup_dir/$file"
    fi
    staged="$(mktemp "$project/scripts/.one-click-$file.XXXXXX")"
    if ! cp -- "$source" "$staged"; then
        rm -f -- "$staged"
        echo "ERROR: Could not stage $file." >&2
        exit 1
    fi
    chmod 0644 -- "$staged"
    mv -f -- "$staged" "$target"
    printf 'Updated: scripts/%s\n' "$file"
    changed=$((changed + 1))
done

echo
echo "Installed arm folder: $project"
echo "Updated $changed script(s). Recorded motions were NOT changed."
if [[ -d "$backup_dir" ]]; then
    echo "Original installed scripts backed up to: $backup_dir"
fi
echo "Opening Script 6. Choose option 8 for Competition Mode."
echo "No robot motion occurs until you explicitly start playback."
echo

cd -- "$project"
python3 scripts/06_live_grid_tool.py
