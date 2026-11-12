#!/usr/bin/env python3
"""Press Space during a supervised gripper closing trial to mark 'good enough'.

Space is a TEACH marker during closing, not a calibrated force sensor.
The gripper is released immediately and the pre-release position/load/current
are saved. This does NOT enable production force control in script 6.
"""
import argparse
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gripper_force_test import (ForceTester, TrialConfig, TrialError,
                                describe_result, position_angle,
                                _existing_limits)

# Defaults are measured manually on the repaired arm, not generic STS3215 values.
MEASURED_OPEN = 1979
MEASURED_CLOSED = 3073
LOG_FILE = ROOT / "gripper_space_teach_samples.jsonl"
CANCEL_KEYS = {"q", "Q", "\x03", "\x1b"}


def endpoints_within_limits(settings, open_ticks, closed_ticks, cap):
    """Only allow tiny, inward, explicitly reported one/two-tick corrections.

    Never widen arm_settings limits or move farther than measured travel.
    If calibration is substantially inconsistent, abort before connecting.
    """
    lo, hi = settings.ANGLE_LIMITS["gripper"]
    permitted = [ticks for ticks in range(4096)
                 if lo <= position_angle(settings, ticks) <= hi]
    if not permitted:
        raise TrialError("Gripper angle calibration has no permitted raw positions.")
    bottom, top = permitted[0], permitted[-1]
    corrected = []
    for label, requested in (("OPEN", open_ticks), ("CLOSED", closed_ticks)):
        actual = min(max(requested, bottom), top)
        if abs(actual - requested) > 2:
            raise TrialError(
                f"{label}={requested} outside arm_settings raw interval "
                f"{bottom}..{top} by more than 2 ticks. "
                "Check the calibration; no motor will be enabled."
            )
        if actual != requested:
            print(f"{label}: measured {requested}, using {actual} to respect "
                  f"arm_settings raw interval {bottom}..{top}.")
        corrected.append(actual)
    config = TrialConfig(corrected[0], corrected[1], cap)
    return config.validate(settings)


def stop_keys(terminal, marker, *, teaching):
    """Read at most one key; never reinterpret Q/Esc as a teach sample."""
    key = terminal.read_key(0.0)
    if key == " ":
        marker["key"] = "space" if teaching else "cancel"
        return True
    if key in CANCEL_KEYS:
        marker["key"] = "cancel"
        return True
    return False


def record_sample(label, result, feedback, cap):
    if feedback is None:
        raise TrialError("Cannot save the sample without measured feedback.")
    sample = {
        "time_utc": datetime.now(timezone.utc).isoformat(),
        "label": label,
        "source": "space_pressed_during_closing",
        "position": feedback.position,
        "load": feedback.load,
        "current_raw": feedback.current,
        "temperature_c": feedback.temperature,
        "peak_load": result.peak_load,
        "peak_current_raw": result.peak_current,
        "elapsed_seconds": round(result.elapsed, 3),
        "trial_torque_cap": cap,
        "calibrated_force": False,
    }
    # A training sample is observational data, NOT verified force calibration.
    with LOG_FILE.open("a", encoding="utf-8") as file:
        file.write(json.dumps(sample, sort_keys=True) + "\n")
    return sample


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--open", type=int, default=MEASURED_OPEN)
    parser.add_argument("--closed", type=int, default=MEASURED_CLOSED)
    parser.add_argument("--cap", type=int, default=50,
                        help="Experimental cap 1..50/1000; never auto-increased")
    parser.add_argument("--label", default="block", help="Name of block shape")
    args = parser.parse_args(argv)
    import arm_settings as settings

    # Software validation and local settings come BEFORE any motor connection.
    try:
        config = endpoints_within_limits(settings, args.open, args.closed, args.cap)
        existing = _existing_limits(settings)
        cutoff = existing.max_current_raw if existing else None
        if existing:
            config = replace(config, requested_cap=min(config.requested_cap,
                                                       existing.close_torque))
    except (ValueError, TrialError, KeyError, ZeroDivisionError) as exc:
        print(f"STOP (no motion): {exc}")
        return 1

    if not sys.stdin.isatty():
        print("STOP: Run in a real Pi terminal so key presses can be detected.")
        return 1
    print("\nSPACE-TO-TEACH GRIPPER (experimental, not automatic calibration)")
    print(f"Open={config.open_ticks}, close bound={config.closed_ticks}, "
          f"cap <= {config.requested_cap}/1000.")
    print("The whole arm MUST be supported, with other servos unpowered.")
    print("Keep hands clear of the jaws. Physical motor power-stop must be accessible.")
    print("Space DURING closing = 'gut genug' and release. Q/Esc = abort.")
    print("A load trip, current cutoff, stall, or timeout stops first; no force is increased.")
    from arm_helpers import connect, check_result
    from terminal_keys import RawTerminal

    port = None
    tester = None
    try:
        if input("Jaws EMPTY. Press Enter to open them; Q to cancel: ").strip().lower() == "q":
            return 1
        port, handler = connect()
        tester = ForceTester(handler, settings, check_result, config,
                             verified_current_cutoff=cutoff)

        opening_marker = {"key": None}
        with RawTerminal() as terminal:
            tester.stop_requested = lambda: stop_keys(
                terminal, opening_marker, teaching=False)
            tester.prepare()  # Checks all other joints are torque OFF.
            opening = tester.move(config.open_ticks, "Opening empty jaws")
        print(describe_result("Opening", opening))
        if opening.reason != "endpoint":
            print("STOP: Opening did not finish cleanly. Do not test a block yet.")
            return 1

        if input("Place the cylinder ON A SUPPORT between open jaws. "
                 "Remove hands; Enter to CLOSE, Q cancels: ").strip().lower() == "q":
            return 1

        closing_marker = {"key": None}
        with RawTerminal() as terminal:
            tester.stop_requested = lambda: stop_keys(
                terminal, closing_marker, teaching=True)
            tester.prepare()
            print("CLOSING NOW. Press SPACE once when grip feels sufficient "
                  "(observe it, don't touch the jaws).", flush=True)
            result = tester.move(config.closed_ticks, "Closing / Space to teach")

        # move() always releases torque before returning. The captured feedback
        # is the last sample from BEFORE release, not a post-stop idle reading.
        if result.reason == "cancelled" and closing_marker["key"] == "space":
            sample = record_sample(args.label, result, tester.last_feedback,
                                   tester.effective_cap)
            print(f"\nGUT GENUG [SPACE]! position={sample['position']} "
                  f"load={sample['load']} current_raw={sample['current_raw']}")
            print(f"Sample saved to {LOG_FILE}. Gripper torque is OFF; support the block.")
            print("This is a training sample, NOT a validated torque threshold.")
            return 0

        print(describe_result("Closing", result))
        print("No 'good enough' sample saved. If it stalled or hit a resistance "
              "limit first, inspect the gripper; do NOT raise torque to force it.")
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled. Check the gripper is released.")
        return 1
    except (TrialError, ValueError, OSError, RuntimeError) as exc:
        print(f"STOP: {exc}")
        return 1
    finally:
        if tester:
            try:
                tester.release()
            except TrialError as exc:
                print(f"STOP: {exc}. Use the physical motor power-stop.")
        if port:
            port.closePort()


if __name__ == "__main__":
    raise SystemExit(main())
