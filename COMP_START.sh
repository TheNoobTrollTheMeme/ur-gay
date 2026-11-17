#!/usr/bin/env bash
# One pasted command from any folder: find/clone ur-gay, pull, deploy, start Quick COMP.
# No motor movement until the competition interface explicitly starts a queue.
set -euo pipefail
remote="https://github.com/TheNoobTrollTheMeme/ur-gay.git"
check_repo() {
    local where="$1" origin
    [[ -d "$where/.git" ]] || return 1
    origin="$(git -C "$where" remote get-url origin 2>/dev/null || true)"
    [[ "$origin" == *"TheNoobTrollTheMeme/ur-gay"* ]]
}
folder=""
if [[ -n "${UR_GAY_DIR:-}" ]]; then
    if ! check_repo "$UR_GAY_DIR"; then
        echo "ERROR: UR_GAY_DIR must point to the existing ur-gay GitHub clone."
        exit 1
    fi
    folder="$UR_GAY_DIR"
else
    for candidate in "$PWD" "$HOME/ur-gay" "$HOME/Ur-gay" \
                     "$HOME/ur gay" "$HOME/Ur gay" "$HOME/Ur Gay" \
                     "$HOME/robot-arm-lab/ur-gay" "$HOME/robot-arm-lab/ur gay" \
                     "$HOME/robot-arm-lab/Ur gay" \
                     "$HOME/robot-arm-lab/STS3215-5DOF-Robot-Arm/ur-gay"; do
        if check_repo "$candidate"; then
            folder="$candidate"
            break
        fi
    done
fi
if [[ -z "$folder" ]]; then
    folder="$HOME/ur-gay"
    if [[ -e "$folder" ]]; then
        echo "ERROR: $folder already exists but is not the expected GitHub clone."
        echo "Set UR_GAY_DIR to the actual clone, or move the conflicting folder yourself."
        exit 1
    fi
    echo "Cloning Competition scripts from $remote"
    git clone -- "$remote" "$folder"
fi
echo "Found ur-gay GitHub clone: $folder"
# Pass through all quick-mode flags; the installed project's recording files
# and locally saved macros are never overwritten by the updater.
bash "$folder/ONE_CLICK_COMPETITION.sh" --comp "$@"
