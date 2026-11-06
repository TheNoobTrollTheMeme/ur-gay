#!/usr/bin/env bash
# One-click, non-destructive update of THIS clone from GitHub.
# Does not start the robot arm or change recorded waypoints directly.
set -u

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
status=0

echo "Robot code: pulling latest main from GitHub"
echo "Folder: $repo"
echo

if ! command -v git >/dev/null 2>&1; then
  echo "ERROR: Git is not installed."
  status=1
elif ! git -C "$repo" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "ERROR: This folder is not a Git clone."
  status=1
else
  branch="$(git -C "$repo" branch --show-current)"
  if [[ "$branch" != "main" ]]; then
    echo "ERROR: Expected branch main, found '${branch:-detached HEAD}'. Nothing changed."
    status=1
  elif git -C "$repo" pull --ff-only origin main; then
    echo
    echo "SUCCESS: Current commit:"
    git -C "$repo" log -1 --format='%h %s'
  else
    echo
    echo "ERROR: Pull stopped safely. Local edits/conflicts may need review."
    echo "No forced reset or overwrite was attempted."
    status=1
  fi
fi

if [[ "${PULL_PAUSE:-0}" == "1" ]]; then
  echo
  read -r -p "Press Enter to close this window..." _ || true
fi
exit "$status"
