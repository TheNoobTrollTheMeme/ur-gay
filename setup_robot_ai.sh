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

echo "[4/4] Building safety features with Codex (simulation-only)..."
echo "Workspace is isolated from your live robot scripts."
cd "$WORKSPACE"
# Read-only system inspection, workspace-only edits; do not grant unrestricted access.
cat > "$WORKSPACE/SAFETY_BUILD_TASK.md" <<'TASK'
Inspect the existing robot-arm source code and map the current M/P motion files, waypoint formats, servo interface, and execution entry points. Create an implementation report describing what each part currently does and which details remain unknown. Build and unit-test in SIMULATION ONLY:
1. A default-disabled hardware gate, mock servo driver, fault-latching safety supervisor with manual reset.
2. Tracking-error detection, stale feedback timeout, communication failure and command timeouts. Add current/load and temperature checks ONLY where supported by real code. Use conservative configurable thresholds marked UNCALIBRATED until baseline logs have been reviewed; do not claim calibrated sensitivity.
3. Safe stop that clears queued movement and requests documented hold/stop behavior on driver integration. NEVER auto-home or auto-retract after a jam.
4. Mocked servo feedback and timestamped flight recorder logs for faults.
5. Collision-checking scaffolding using measured geometry only, evaluating swept volumes of gripper and links. Unknown geometry must BLOCK hardware-ready claims.
6. Preserve exactly two USER-RECORDED waypoints per M/P motion; sampled interpolation for validation is acceptable.
7. Add reproducible unit tests for normal tracking, jams, overheating (if supported), stale feedback, unavailable telemetry, and reset-required faults.
8. Produce SAFETY_STATUS.md: which features are implemented, wired to real driver, tested only in simulation, and still require human calibration.
No serial/USB device I/O, no motor commands, no hardware tests, no auto-deployment, no push to main. Never run existing movement scripts. Do not modify calibration or overwrite live files.
TASK
# Running Codex is an explicit operator action via this installer. It remains sandboxed.
codex exec --full-auto "$(cat SAFETY_BUILD_TASK.md)" || {
  echo "Automatic code generation did not finish. Resume interactively: cd '$WORKSPACE' && codex"
  exit 1
}
echo "Simulation-only safety implementation finished. Review $WORKSPACE/SAFETY_STATUS.md"
echo "Nothing has been deployed to the real robot. Human verification required."
echo "To inspect changes: cd '$WORKSPACE' && git diff"
