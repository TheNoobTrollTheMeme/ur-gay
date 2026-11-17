#!/usr/bin/env bash
# ENGG1101 STS3215 arm launcher: Kevin/Kelvin's originals + custom scripts.
# Choosing a menu entry only launches the selected program. No auto-playback.
set -uo pipefail

INSTALL_PATH="$HOME/.local/bin/arm"

help_text() {
    cat <<'EOF'
Usage: arm [command] [arguments]
       arm                 interactive menu (works over SSH)
       arm 1..5            Kelvin arm Scripts 01..05 (5 supports @bookmarks)
       arm 6               custom live grid / waypoint editor
       arm 7               read-only raw debug capture (torque OFF)
       arm board           terminal arm board / macro maker
       arm competition     direct competition queue editor
       arm remap           offline tray / winning-line remapper
       arm attack          full live square attack selector
       arm sweep           9-square live forward/back diagnostic
       arm joycon          Joy-Con diagnostics (no movement)
       arm edit            jump straight into Script 6 recording editor
       arm paste            batch-paste ONE bookmark joint into chosen waypoints
       arm bookmark        bookmark manager (capture, list, delete)
       arm goto NAME       move to a saved bookmark, with MOVE confirmation
       arm saved NAME      play saved sequence or @bookmark (asks before motion)
       arm dry NAME        preview saved sequence, no hardware access
       arm --install       install command in ~/.local/bin + shell PATH

Set ARM_PROJECT_DIR=/path/to/STS3215-5DOF-Robot-Arm if auto-discovery fails.
Neither this launcher nor --install updates or edits motion JSON files.
EOF
}

install_launcher() {
    mkdir -p -- "$HOME/.local/bin"
    if [[ "$(readlink -f -- "${BASH_SOURCE[0]}")" != "$(readlink -m -- "$INSTALL_PATH")" ]]; then
        cp -- "${BASH_SOURCE[0]}" "$INSTALL_PATH" || return 1
    fi
    chmod 0755 -- "$INSTALL_PATH" || return 1
    if ! grep -Fq '# ENGG1101 arm launcher PATH' "$HOME/.bashrc" 2>/dev/null; then
        {
            printf '\n# ENGG1101 arm launcher PATH\n'
            printf 'export PATH="$HOME/.local/bin:$PATH"\n'
        } >> "$HOME/.bashrc"
    fi
    echo "Installed: $INSTALL_PATH"
    echo 'Use: arm 1, arm 6, arm board, or just arm for the full menu.'
    echo 'New shells will find arm automatically; for this shell run: export PATH="$HOME/.local/bin:$PATH"'
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" || "${1:-}" == "help" ]]; then
    help_text
    exit 0
fi
if [[ "${1:-}" == "--install" ]]; then
    install_launcher
    exit $?
fi

valid_project() {
    [[ -f "$1/arm_settings.py" && -f "$1/scripts/05_waypoint_sequence.py" ]]
}

project=""
if [[ -n "${ARM_PROJECT_DIR:-}" ]]; then
    candidate="${ARM_PROJECT_DIR/#\~/$HOME}"
    if valid_project "$candidate"; then
        project="$(cd -- "$candidate" && pwd -P)"
    else
        printf 'ERROR: ARM_PROJECT_DIR is not an installed arm project: %s\n' "$candidate" >&2
        exit 1
    fi
else
    for candidate in \
        "$PWD" \
        "$(dirname -- "$PWD")" \
        "$HOME/robot-arm-lab/STS3215-5DOF-Robot-Arm" \
        "$HOME/STS3215-5DOF-Robot-Arm" \
        "$HOME/robot-arm-lab"; do
        if valid_project "$candidate"; then
            project="$(cd -- "$candidate" && pwd -P)"
            break
        fi
    done
fi
if [[ -z "$project" ]]; then
    echo 'ERROR: Cannot find the installed Kelvin STS3215 arm project.' >&2
    echo 'Expected: ~/robot-arm-lab/STS3215-5DOF-Robot-Arm' >&2
    echo 'Or set ARM_PROJECT_DIR=/path/to/your/installed/project' >&2
    exit 1
fi

python_cmd="python3"
if [[ -x "$project/.venv/bin/python3" ]]; then
    python_cmd="$project/.venv/bin/python3"
elif [[ -x "$project/.venv/bin/python" ]]; then
    python_cmd="$project/.venv/bin/python"
fi
cd -- "$project" || exit 1

run_script() {
    local script="$1"
    shift
    if [[ ! -f "$project/$script" ]]; then
        echo "Missing installed script: $project/$script" >&2
        echo 'Copy/update your custom scripts into the INSTALLED arm project first.' >&2
        return 1
    fi
    echo "Starting $script"
    "$python_cmd" "$project/$script" "$@"
}

# Script 6 has a Python-callable queue/editor API, so do not modify upstream files.
run_script6_function() {
    local function="$1"
    shift
    if [[ ! -f "$project/scripts/06_live_grid_tool.py" ]]; then
        echo 'Missing Script 6 in installed arm project.' >&2
        return 1
    fi
    "$python_cmd" -c "$(cat <<'PY'
import importlib.util
from pathlib import Path
import sys

root = Path(sys.argv[1])
function = sys.argv[2]
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / 'scripts'))
script = root / 'scripts' / '06_live_grid_tool.py'
spec = importlib.util.spec_from_file_location('arm_script6', script)
if spec is None or spec.loader is None:
    raise RuntimeError('Could not import installed Script 6')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
try:
    getattr(module, function)(*sys.argv[3:])
except (KeyboardInterrupt, EOFError):
    print('\nStopped.')
PY
)" "$project" "$function" "$@"
}

ask_torque_off() {
    local answer
    echo 'WARNING: This script disables servo torque. SUPPORT the arm first.'
    read -r -p 'Enter = continue, Q = cancel: ' answer || return 1
    [[ "$answer" != 'q' && "$answer" != 'Q' ]]
}

run_goto() {
    local name="${1:-}"
    if [[ -z "$name" ]]; then
        read -r -p 'Bookmark name (e.g. safe_left): ' name || return 1
    fi
    name="${name#@}"
    if [[ -z "$name" ]]; then
        echo 'No bookmark selected.'
        return 1
    fi
    # Script 6 validates the bookmark and requires typing MOVE before motion.
    run_script6_function go_to_bookmark "$name"
}

run_saved() {
    local dry="$1"
    shift
    local name="${1:-}"
    if [[ -z "$name" ]]; then
        read -r -p 'Saved motion name (e.g. grab1): ' name || return 1
    fi
    if [[ -z "$name" ]]; then
        echo 'No motion selected.'
        return 1
    fi
    if [[ "$dry" == yes ]]; then
        run_script scripts/06_live_grid_tool.py --sequence "$name" --dry-run
    else
        run_script scripts/06_live_grid_tool.py --sequence "$name"
    fi
}

dispatch() {
    local cmd="${1:-}"
    if (( $# > 0 )); then shift; fi
    case "${cmd,,}" in
        1|01|read|positions) ask_torque_off && run_script scripts/01_read_positions.py "$@" ;;
        2|02|servo|raw) run_script scripts/02_move_servo.py "$@" ;;
        3|03|angles|angle) run_script scripts/03_move_joint_angle.py "$@" ;;
        4|04|timestamp|timestamps) run_script scripts/04_timestamp_motion.py "$@" ;;
        5|05|waypoint|waypoints) run_script scripts/05_bookmark_compat.py "$@" ;;
        6|06|grid|script6|mine) run_script scripts/06_live_grid_tool.py "$@" ;;
        7|07|debug|capture) ask_torque_off && run_script scripts/07_raw_debug_capture.py "$@" ;;
        b|board|macro|macros) run_script scripts/arm_board.py --terminal "$@" ;;
        c|competition|queue) run_script6_function competition_placements ;;
        m|remap|mapping|tray) run_script scripts/arm_board.py --remap ;;
        a|attack) run_script scripts/06_full_live_attack.py "$@" ;;
        t|test|sweep|roundtrip|round-trip) run_script scripts/06_live_square_sweep.py "$@" ;;
        j|joycon|joy) run_script scripts/smash_joycon.py --diagnose ;;
        e|edit|record) run_script6_function record ;;
        x|paste|batch|jointpaste) run_script scripts/batch_paste_bookmark_joint.py "$@" ;;
        p|bookmark|bookmarks|point|points) run_script6_function bookmark_menu ;;
        g|goto|go) run_goto "$@" ;;
        s|saved|play) run_saved no "$@" ;;
        d|dry|preview) run_saved yes "$@" ;;
        q|quit|exit|0) return 99 ;;
        *) echo "Unknown selection: $cmd. Use 'arm --help' for commands." >&2; return 2 ;;
    esac
}

menu() {
    local response status
    local -a words
    while true; do
        cat <<'EOF'

==================== STS3215 ARM ====================
  KEVIN / KELVIN                  YOUR SCRIPTS
  1  Read current positions       6  Live grid / waypoint editor
  2  Move to raw servo values     7  Raw debug capture
  3  Move using joint angles      B  Board / macro maker
  4  Timestamp motions            C  Competition queue
  5  Waypoint record & playback   M  Tray / winning line remap
                                  A  Full live square attack
                                  T  Live 9-square round-trip test
                                  E  Edit recorded waypoints
                                  X  Batch-paste bookmark coordinate
                                  P  Bookmark manager (capture/list)
                                  G  Go to named bookmark
                                  S  Play saved motion by name
                                  D  Preview motion (no hardware)
                                  J  Joy-Con diagnostics
  Q  Quit
====================================================
EOF
        printf 'Installed project: %s\n' "$project"
        if ! read -r -p 'arm> ' response; then
            echo
            return 0
        fi
        [[ -z "${response//[[:space:]]/}" ]] && continue
        read -r -a words <<< "$response"
        if dispatch "${words[@]}"; then
            :
        else
            status=$?
            [[ "$status" == 99 ]] && return 0
            echo "Returned to launcher (status $status)."
        fi
    done
}

if (( $# == 0 )); then
    menu
else
    if dispatch "$@"; then
        exit 0
    else
        status=$?
        [[ "$status" == 99 ]] && exit 0
        exit "$status"
    fi
fi
