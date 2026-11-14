#!/usr/bin/env python3
"""Read-only manual servo capture for debugging false ANGLE_LIMITS rejections.

This deliberately does NOT load or save waypoint sequences. No calls to
move_all_sync(), enable_all_torque(), or servo target writes exist here.
The controller connection is used only to release torque and read raw ticks.

Saved files live under debug_captures/ and are NOT eligible for playback.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STOP_KEYS = {"q", "Q", " ", "\x1b", "\x03"}


def make_snapshot(measured, joint_names, *, timestamp=None):
    """Preserve measured encoder ticks without angle conversion or limit tests."""
    joints = tuple(joint_names)
    if not joints or any(j not in measured for j in joints):
        raise ValueError("Incomplete servo read: missing one or more joints.")
    raw = {}
    for name in joints:
        value = measured[name]
        if type(value) is not int:
            raise ValueError(f"Non-integer servo reading for {name}: {value!r}")
        raw[name] = value
    when = timestamp or datetime.now(timezone.utc).isoformat(timespec="microseconds")
    return {
        "kind": "torque_off_raw_debug_capture",
        "timestamp_utc": when,
        "playback_eligible": False,
        "torque_command": "DISABLED",
        "joint_order": list(joints),
        "positions_raw": raw,
        "angle_limits_checked": False,
        "note": ("Measured data only. Do not rename or place this file into "
                 "motions/. This script never commands movement."),
    }


def save_snapshot(snapshot, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = output_dir / f"raw_debug_{stamp}.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(snapshot, stream, indent=2)
        stream.write("\n")
    return path


def run_capture(*, output_dir):
    sys.path.insert(0, str(ROOT))
    import arm_settings as settings
    from arm_helpers import connect, disable_all_torque, read_all
    from terminal_keys import RawTerminal

    print("RAW CAPTURE DEBUG: READ-ONLY. This mode will not command servo motion.")
    print("Support the arm before disabling torque. All motor limits remain untouched.")
    answer = input("Enter = begin torque-off capture, Q = cancel: ").strip().lower()
    if answer == "q":
        return
    port, handler = connect()
    try:
        disable_all_torque(handler)
        print("Torque OFF. Position the supported arm by hand.")
        print("Press R to read/save raw ticks. Press Q / Space / Esc / Ctrl+C to quit.")
        with RawTerminal() as terminal:
            while True:
                key = terminal.read_key(0.05)
                if key in STOP_KEYS:
                    break
                if key not in ("r", "R"):
                    continue
                measured = read_all(handler)
                snapshot = make_snapshot(measured, settings.JOINT_NAMES)
                path = save_snapshot(snapshot, output_dir)
                print("\nRAW TICKS: " + " ".join(
                    f"{name}={snapshot['positions_raw'][name]}"
                    for name in settings.JOINT_NAMES), flush=True)
                print(f"Saved non-playback debug snapshot: {path}", flush=True)
    finally:
        port.closePort()
        print("Serial connection closed. Support arm: this script does not re-enable torque.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "debug_captures",
        help="Output folder for non-playback raw snapshots (default: debug_captures/)")
    args = parser.parse_args()
    run_capture(output_dir=args.output)


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\nRaw capture cancelled; no movement was commanded.")
