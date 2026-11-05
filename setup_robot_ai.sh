#!/usr/bin/env bash
set -Eeuo pipefail

REPO="https://github.com/TheNoobTrollTheMeme/ur-gay.git"
BRANCH="codex-pi-safe-setup"
WORKSPACE="$HOME/robot-ai-workspace"

echo "=== Raspberry Pi Robot AI Setup (code-only, no motor commands) ==="
if [[ "$(uname -s)" != "Linux" ]]; then
  echo "This installer is for Linux / Raspberry Pi OS." >&2; exit 1
fi
case "$(uname -m)" in
  aarch64|x86_64) ;;
  *) echo "64-bit Linux required for the supplied Codex installer. Got $(uname -m)." >&2; exit 1 ;;
esac
command -v curl >/dev/null || { echo "Please install curl first: sudo apt install curl" >&2; exit 1; }
command -v git >/dev/null || { echo "Please install git first: sudo apt install git" >&2; exit 1; }

if ! command -v codex >/dev/null 2>&1; then
  echo "[1/4] Installing official OpenAI Codex CLI..."
  tmp="$(mktemp)"
  trap 'rm -f "$tmp"' EXIT
  curl --fail --location --silent --show-error https://chatgpt.com/codex/install.sh -o "$tmp"
  sh "$tmp"
  export PATH="$HOME/.local/bin:$HOME/.codex/bin:$PATH"
else
  echo "[1/4] Codex already available."
fi
command -v codex >/dev/null 2>&1 || {
  echo "Codex installed but its executable is not in PATH. Open a new shell and run codex." >&2; exit 1;
}

echo "[2/4] Selecting isolated workspace..."
# Always use an isolated workspace: never overwrite uncommitted robot code.
if [[ -d "$WORKSPACE/.git" ]]; then
  echo "Existing workspace found; leaving its files and branch untouched."
elif [[ -e "$WORKSPACE" ]]; then
  echo "Refusing to overwrite existing $WORKSPACE" >&2; exit 1
else
  git clone --branch "$BRANCH" --single-branch "$REPO" "$WORKSPACE"
fi

if [[ ! -e "$WORKSPACE/AGENTS.md" ]]; then
cat > "$WORKSPACE/AGENTS.md" <<'POLICY'
# Robot AI development rules
- Default to CODE REVIEW / SIMULATION ONLY. The real STS3215 arm is not a test device.
- NEVER execute scripts that move servos, power motors, alter calibration, or access serial/USB servo control unless the operator explicitly authorizes that exact test.
- Do not use unrestricted or bypass-approval execution.
- Preserve original M/P trajectories and exactly two user-recorded waypoints per motion.
- Geometry, timings, servo limits and sensor feedback must be verified, not guessed.
- Keep hardware drivers behind a simulation/mock interface.
- Do not push to main or automatically deploy to the live arm.
- Ensure an operator is present with an accessible emergency stop for physical testing.
POLICY
fi

echo "[3/4] Sign in (only necessary once)..."
if ! codex login status >/dev/null 2>&1; then
  echo "Follow the device-code login instructions; do NOT paste credentials in chat."
  codex login --device-auth
fi

echo "[4/4] Starting Codex in $WORKSPACE"
echo "This is a separate copy. Existing live scripts remain untouched."
echo "Ask Codex to inspect the repo, build collision checks, and create a test report."
echo "To run again: cd '$WORKSPACE' && codex"
cd "$WORKSPACE"
exec codex
