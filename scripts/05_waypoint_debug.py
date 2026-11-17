#!/usr/bin/env python3
"""Separate Script 5 debug menu with ONE speed/acceleration pair for all waypoints.

Kelvin's scripts/05_waypoint_sequence.py remains unchanged. This imports its
recording, validation, playback, timing, and Space-key E-stop functions; only
the two global waypoint playback settings are replaced for this process.
"""

import argparse
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from playback_speed import (  # noqa: E402
    DEFAULT_ACCELERATION,
    DEFAULT_SPEED,
    MAX_ACCELERATION,
    MAX_SPEED,
    MIN_ACCELERATION,
    MIN_SPEED,
    prompt_playback_settings,
    validate_playback_settings,
)


def load_script5():
    """Load the installed Kelvin Script 5, adding existing @bookmark support."""
    import arm_settings as settings
    from pose_bookmarks import load_with_bookmarks

    source = ROOT / "scripts" / "05_waypoint_sequence.py"
    if not source.is_file():
        raise FileNotFoundError(f"Missing original Script 5: {source}")
    spec = importlib.util.spec_from_file_location("kelvin_script5_debug_base", source)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load original Script 5: {source}")
    script5 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script5)

    original_loader = script5.load_sequence
    bookmark_path = ROOT / "motions" / "_pose_bookmarks.json"

    def linked_loader(path):
        return load_with_bookmarks(
            path, original_loader, script5.parse_point_values,
            settings.JOINT_NAMES, bookmark_path,
        )

    script5.load_sequence = linked_loader
    return script5, settings


def apply_speed(settings, speed, acceleration):
    """Set the only two knobs Kelvin's playback passes to each sync move."""
    speed, acceleration = validate_playback_settings(speed, acceleration)
    settings.POSE_SEQUENCE_MOVE_SPEED = speed
    settings.POSE_SEQUENCE_MOVE_ACCELERATION = acceleration
    return speed, acceleration


def confirm_high_speed(speed, acceleration, input_fn=input):
    """Require explicit approval before high-rate playback, not at import time."""
    if speed <= 600 and acceleration <= 35:
        return True
    print("WARNING: High-speed motion can hit the rack or dislodge the arm.")
    return input_fn("Type CONFIRM for high-speed debugging (else cancel): ").strip() == "CONFIRM"


def menu(script5, settings, *, speed, acceleration, input_fn=input,
         speed_prompt=prompt_playback_settings):
    """Script 5's original choices, plus an in-session global speed editor."""
    while True:
        print(f"\nSCRIPT 5 DEBUG | ALL waypoints: speed={speed}, acceleration={acceleration}")
        print("  1. Record waypoints (Kelvin Script 5)")
        print("  2. Play saved sequence (Kelvin Script 5 + @bookmarks)")
        print("  3. Change GLOBAL playback speed / acceleration")
        print("  q. Quit")
        choice = input_fn("Choose an action: ").strip().lower()
        if choice == "q":
            return
        if choice == "1":
            sequence_path = script5.create_sequence()
            if sequence_path is not None:
                script5.play_sequence(sequence_path)
        elif choice == "2":
            sequences = script5.find_sequences()
            if not sequences:
                print("No waypoint sequences found. Create one first.")
                continue
            for index, (path, data) in enumerate(sequences, start=1):
                print(
                    f"  {index}. {path.stem} - "
                    f"{data.get('description') or '(no description)'} "
                    f"({len(data.get('points', []))} points)"
                )
            selection = input_fn("Enter a number or q to cancel: ").strip().lower()
            if selection == "q":
                continue
            if not selection.isdigit() or not 1 <= int(selection) <= len(sequences):
                print("Please choose one of the listed numbers.")
                continue
            script5.play_sequence(sequences[int(selection) - 1][0])
        elif choice == "3":
            try:
                next_speed, next_acceleration = speed_prompt(
                    speed=speed, acceleration=acceleration, input_fn=input_fn,
                )
                speed, acceleration = apply_speed(settings, next_speed, next_acceleration)
            except ValueError as error:
                print(f"Settings unchanged: {error}")
            else:
                print(f"GLOBAL debug playback: speed={speed}, acceleration={acceleration}")
        else:
            print("Please enter 1, 2, 3, or q.")


def main(argv=None, input_fn=input):
    parser = argparse.ArgumentParser(
        description="Kelvin Script 5 debug clone: choose one speed for ALL waypoints"
    )
    parser.add_argument("--speed", type=int, default=DEFAULT_SPEED,
                        help=f"global speed {MIN_SPEED}..{MAX_SPEED} (default {DEFAULT_SPEED})")
    parser.add_argument("--accel", type=int, default=DEFAULT_ACCELERATION,
                        help=f"global acceleration {MIN_ACCELERATION}..{MAX_ACCELERATION}"
                             f" (default {DEFAULT_ACCELERATION})")
    args = parser.parse_args(argv)
    try:
        speed, acceleration = validate_playback_settings(args.speed, args.accel)
    except ValueError as error:
        parser.error(str(error))
    if not confirm_high_speed(speed, acceleration, input_fn):
        print("Cancelled; no connection or movement.")
        return

    script5, settings = load_script5()
    apply_speed(settings, speed, acceleration)
    print("Original Kelvin Script 5 left untouched. Space = E-stop during motion.")
    menu(script5, settings, speed=speed, acceleration=acceleration, input_fn=input_fn)


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\nScript 5 debug stopped.")
