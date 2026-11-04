#!/usr/bin/env python3
"""Install the replace-one-waypoint menu in the HKU STS3215 recorder.

Offline installer: backs up the existing script, checks version markers,
modifies code only (does not touch motors or saved motion JSONs).
"""
import argparse
import ast
from datetime import datetime
from pathlib import Path
import shutil
import sys

DEFAULT_PROJECT = Path.home() / "robot-arm-lab" / "STS3215-5DOF-Robot-Arm"
MARKER = "# REPLACE_ONE_WAYPOINT_FEATURE_v1"

FEATURE = r'''# REPLACE_ONE_WAYPOINT_FEATURE_v1

def replace_one_waypoint():
    """Replace exactly one saved pose; never drive servos or change torque."""
    import hashlib
    import shutil

    sequences = find_sequences()
    if not sequences:
        print("No saved waypoint files found in motions/.")
        return

    print("\nSelect a motion file to edit:")
    for number, (path, data) in enumerate(sequences, start=1):
        print(f"  {number}. {path.stem} ({len(data.get('points', []))} waypoints, "
              f"{data.get('input_type', 'unknown')})")
    selection = input("File number (q to cancel): ").strip().lower()
    if selection in ("q", ""):
        return
    if not selection.isdecimal() or not 1 <= int(selection) <= len(sequences):
        print("Invalid file number; no changes made.")
        return

    path = sequences[int(selection) - 1][0]
    original_bytes = path.read_bytes()
    original_hash = hashlib.sha256(original_bytes).digest()
    try:
        sequence, checked_points = load_sequence(path)
    except (ValueError, OSError, json.JSONDecodeError) as error:
        print(f"Cannot edit this file: {error}")
        return
    original_points = sequence["points"]
    input_type = sequence["input_type"]
    print(f"\nEditing {path.name} ({len(checked_points)} waypoints)")
    for i, point in enumerate(original_points, start=1):
        shown = point.get("input_values", point.get("positions_raw", "legacy"))
        if isinstance(shown, list):
            shown = " ".join(map(str, shown))
        print(f"  {i}. {shown}")
    selected_point = input("Waypoint to REPLACE (q to cancel): ").strip().lower()
    if selected_point in ("q", ""):
        return
    if not selected_point.isdecimal() or not 1 <= int(selected_point) <= len(original_points):
        print("Invalid waypoint number; no changes made.")
        return
    index = int(selected_point) - 1

    order = ", ".join(settings.JOINT_NAMES)
    print(f"\nInput format: {input_type}; joint order: {order}")
    if input_type == "raw":
        print("Enter five raw servo positions (0-4095), or type 'capture'")
        print("to READ the arm's current encoder positions without moving it.")
    else:
        print("Enter five joint angles in degrees, in the displayed order.")
        print("Automatic capture is unavailable for angle files: angle calibration may differ.")
    entry = input("New waypoint values (q to cancel): ").strip()
    if entry.lower() in ("q", ""):
        return

    if entry.lower() == "capture":
        if input_type != "raw":
            print("Capture supports raw-valued motions only; no changes made.")
            return
        print("WARNING: Support the arm. This command only reads positions; it does NOT")
        print("disable torque, move joints, or make the arm safe to reposition by hand.")
        if input("Type CAPTURE to read the current pose: ").strip() != "CAPTURE":
            print("Cancelled; no changes made.")
            return
        try:
            from arm_helpers import read_all
            port, packet = connect()
            try:
                readings = read_all(packet)
            finally:
                port.closePort()
            new_values = [str(readings[joint]) for joint in settings.JOINT_NAMES]
            print("Read servo positions:", " ".join(new_values))
        except Exception as error:
            print(f"Servo read failed: {error}; original file unchanged.")
            return
    else:
        new_values = entry.split()

    try:
        converted = parse_point_values(new_values, input_type)
    except (ValueError, TypeError) as error:
        print(f"Invalid values: {error}; no changes made.")
        return

    old_point = original_points[index]
    print(f"\nReplacing ONLY waypoint {index + 1} of {path.name}")
    print("  Before:", old_point.get("input_values", old_point.get("positions_raw")))
    print("  After: ", new_values)
    print("  Raw ticks:", " ".join(str(converted[j]) for j in settings.JOINT_NAMES))
    if input("Type YES to save this single-point change: ").strip() != "YES":
        print("Cancelled; original file unchanged.")
        return
    if hashlib.sha256(path.read_bytes()).digest() != original_hash:
        print("File changed during editing; refusing to overwrite. Try again.")
        return

    new_point = dict(old_point)
    new_point.pop("positions_raw", None)
    new_point["input_values"] = new_values
    sequence["points"][index] = new_point

    # An isolated backup prevents the playback menu from treating backups as motions.
    backup_folder = MOTION_FOLDER / ".waypoint_backups"
    backup_folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")
    backup_path = backup_folder / f"{path.stem}.{stamp}.json"
    shutil.copy2(path, backup_path)
    try:
        save_sequence(path, sequence)  # Existing project's atomic-save helper.
    except Exception as error:
        print(f"Save failed: {error}")
        print(f"Backup retained at: {backup_path}")
        return
    print(f"Saved waypoint {index + 1} in {path}")
    print(f"Original backed up at {backup_path}")
    print("No playback was started. Review the motion before testing it physically.")

'''

MENU_ANCHOR = '        print("  2. Play a saved waypoint sequence")\n'
MENU_REPLACE = MENU_ANCHOR + '        print("  3. Replace one waypoint in an existing sequence")\n'
ROUTER_ANCHOR = '        elif choice == "2":\n'
ROUTER_REPLACE = '        elif choice == "3":\n            replace_one_waypoint()\n' + ROUTER_ANCHOR
ERROR_ANCHOR = '            print("Please enter 1, 2, or q.")'
ERROR_REPLACE = '            print("Please enter 1, 2, 3, or q.")'


def modified_text(original: str) -> str:
    if MARKER in original:
        return original
    required = ("def main():\n", MENU_ANCHOR, ROUTER_ANCHOR, ERROR_ANCHOR,
                "def save_sequence(", "def load_sequence(", "def find_sequences(",
                "def parse_point_values(")
    missing = [s for s in required if original.count(s) != 1]
    if missing:
        raise ValueError("Unrecognized recorder version; refusing blind patch. Missing/multiple markers: " + repr(missing))
    result = original.replace("def main():\n", FEATURE + "\ndef main():\n", 1)
    result = result.replace(MENU_ANCHOR, MENU_REPLACE, 1)
    result = result.replace(ROUTER_ANCHOR, ROUTER_REPLACE, 1)
    result = result.replace(ERROR_ANCHOR, ERROR_REPLACE, 1)
    ast.parse(result)
    return result


def install(project: Path) -> str:
    script = project / "scripts" / "05_waypoint_sequence.py"
    if not script.is_file():
        raise FileNotFoundError(f"Cannot find {script}. Check the folder or use --project.")
    original = script.read_text(encoding="utf-8")
    if MARKER in original:
        return f"Already installed: {script}"
    patched = modified_text(original)
    backup = script.with_name(script.name + ".before_replace_waypoint_" +
                              datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".bak")
    shutil.copy2(script, backup)
    temporary = script.with_suffix(script.suffix + ".waypoint_tmp")
    try:
        temporary.write_text(patched, encoding="utf-8")
        temporary.replace(script)
    finally:
        temporary.unlink(missing_ok=True)
    return (f"Installed: {script}\nOriginal script backup: {backup}\n"
            "New menu: 3. Replace one waypoint in an existing sequence")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=DEFAULT_PROJECT,
                        help="Root of the installed STS3215 robot-arm project")
    args = parser.parse_args()
    try:
        print(install(args.project.expanduser().resolve()))
    except (ValueError, OSError, SyntaxError) as error:
        print(f"Installer stopped: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
