#!/usr/bin/env bash
# Download code ONLY to isolated Raspberry Pi workspace.
# Do not launch Python, import servo drivers, copy files into the live arm
# controller, modify calibration, or command hardware.
set -Eeuo pipefail
REPO='https://github.com/TheNoobTrollTheMeme/ur-gay.git'
BRANCH='codex-pi-safe-setup'
TARGET="$HOME/robot-ai-workspace"
mkdir -p "$HOME/.cache"
exec 9>"$HOME/.cache/robot-ai-sync.lock"
flock -n 9 || exit 0
if [[ ! -e "$TARGET" ]]; then
  git clone --single-branch --branch "$BRANCH" "$REPO" "$TARGET"
else
  [[ -d "$TARGET/.git" ]] || { echo 'REFUSE: workspace is not a Git checkout' >&2; exit 1; }
  [[ "$(git -C "$TARGET" remote get-url origin)" == "$REPO" ]] || { echo 'REFUSE: wrong upstream' >&2; exit 1; }
  [[ "$(git -C "$TARGET" branch --show-current)" == "$BRANCH" ]] || { echo 'REFUSE: wrong branch' >&2; exit 1; }
  [[ -z "$(git -C "$TARGET" status --porcelain)" ]] || { echo 'REFUSE: uncommitted local workspace changes' >&2; exit 1; }
  git -C "$TARGET" fetch --quiet origin "$BRANCH"
  git -C "$TARGET" merge --ff-only "origin/$BRANCH"
fi
echo "Staging checkout synced at $(git -C "$TARGET" rev-parse --short HEAD)"
echo 'NO hardware files were deployed. NO tests or motor-control code were executed on the Pi.'
