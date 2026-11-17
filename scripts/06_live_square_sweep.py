#!/usr/bin/env python3
"""Guided, LIVE 3x3 board sweep: 9 cells out, then the same cells back.

Each motions/live_c#r#.json route begins at a SHARED, manually taught
@sweep_home bookmark, travels through taught clearance waypoints to a square,
then reverses those exact waypoints to the shared home. Never interpolate
directly between square endpoints. Routes/bookmarks reload between visits.

No path planner can establish physical ceiling clearance from servo ticks.
Operate only after teaching and checking every transition on the real rig.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MOTIONS = ROOT / "motions"
EDITOR_PATH = ROOT / "scripts" / "06_live_grid_tool.py"
HOME = "sweep_home"
ORDER = ("c1r1", "c2r1", "c3r1", "c3r2", "c2r2", "c1r2", "c1r3", "c2r3", "c3r3")
ROUND_TRIP = ORDER + ORDER[-2::-1]  # 9 outward and 8 back; do not repeat the turn.
MAX_JOINT_STEP = 250  # raw ticks; a guard, NOT a collision/height calculation
HOME_TOLERANCE = 50
ARRIVAL_TOLERANCE = 90
STOP_KEYS = frozenset((" ", "q", "\x03", "\x1b"))


def load_editor():
    """Import only installed Script 6, never the upstream Kelvin file directly."""
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("sweep_script6", EDITOR_PATH)
    if spec is None or spec.loader is None:
        raise FileNotFoundError(f"Script 6 missing: {EDITOR_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def route_path(cell, motions=MOTIONS):
    if cell not in ORDER:
        raise ValueError(f"Unknown board cell: {cell}")
    return motions / f"live_{cell}.json"


def arm_joints(editor):
    return tuple(j for j in editor.settings.JOINT_NAMES if j != "gripper")


def max_arm_delta(editor, a, b):
    return max(abs(int(a[j]) - int(b[j])) for j in arm_joints(editor))


def home_pose(editor):
    pose = editor.get_bookmark(editor.BOOKMARK_FILE, HOME, editor.settings.JOINT_NAMES)
    editor.validate_competition_target(pose)
    editor.validate_target(pose)
    return pose


def initialize_routes(editor, motions=MOTIONS):
    """Create NON-RUNNABLE teaching templates without touching existing files."""
    home_pose(editor)  # requires a real, captured @sweep_home first
    motions.mkdir(parents=True, exist_ok=True)
    count = 0
    for cell in ORDER:
        path = route_path(cell, motions)
        data = {
            "schema_version": 1,
            "type": "pose_sequence",
            "name": path.stem,
            "description": "TEACH BEFORE RUNNING: common home, safe clearance, square target",
            "input_type": "raw",
            "joint_order": [
                {"servo_id": editor.settings.SERVO_IDS[j], "joint": j}
                for j in editor.settings.JOINT_NAMES
            ],
            "created_at": datetime.now().astimezone().isoformat(),
            "points": [{"bookmark": HOME}, {"bookmark": HOME}, {"bookmark": HOME}],
        }
        try:
            with path.open("x", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2)
                handle.write("\n")
            count += 1
        except FileExistsError:
            print(f"Preserved existing {path.name}")
    print(f"Created {count} teaching template(s). No arm movement.")
    print("Edit each live_c#r# in 'arm edit': keep #1 @sweep_home; "
          "replace #2 with a VERIFIED clearance pose and #3 with its square pose. "
          "Insert more waypoints as needed. Placeholder routes cannot run.")


def read_route(editor, cell, anchor, motions=MOTIONS):
    """Read a fresh, live-linked route and reject unsafe/malformed skeletons."""
    path = route_path(cell, motions)
    if not path.is_file():
        raise ValueError(f"Missing {path.name}. Capture @{HOME}, then run 'arm sweep --init'.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read {path.name}: {exc}") from exc
    points_def = data.get("points")
    if (not isinstance(points_def, list) or len(points_def) < 3
            or points_def[0] != {"bookmark": HOME}):
        raise ValueError(
            f"{path.name}: needs 3+ taught points, starting with linked @{HOME}.")
    if data.get("input_type") != "raw":
        raise ValueError(f"{path.name}: expected raw position recording.")
    _, points = editor.wp.load_sequence(path)  # resolves CURRENT linked bookmarks
    if len(points) != len(points_def):
        raise ValueError(f"{path.name}: number of decoded waypoints changed.")
    for n, point in enumerate(points, 1):
        editor.validate_competition_target(point)
        editor.validate_target(point)
        if n > 1:
            jump = max_arm_delta(editor, points[n - 2], point)
            if jump > MAX_JOINT_STEP:
                raise ValueError(
                    f"{path.name} segment {n-1}->{n}: {jump} raw ticks on an arm joint "
                    f"(limit {MAX_JOINT_STEP}). Teach additional intermediate poses.")
    if max_arm_delta(editor, points[0], anchor):
        raise ValueError(f"{path.name}: first waypoint no longer matches @{HOME}.")
    if max_arm_delta(editor, points[-1], anchor) <= HOME_TOLERANCE:
        raise ValueError(f"{path.name}: final square target is still the home placeholder.")
    if max_arm_delta(editor, points[1], anchor) <= 0:
        raise ValueError(f"{path.name}: waypoint #2 is still the home placeholder.")
    return tuple(dict(p) for p in points)


def preflight(editor, motions=MOTIONS):
    """Validate all nine live paths before ANY hardware connection."""
    anchor = home_pose(editor)
    routes = {}
    errors = []
    for cell in ORDER:
        try:
            routes[cell] = read_route(editor, cell, anchor, motions)
        except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
            errors.append(f"{cell.upper()}: {exc}")
    if errors:
        raise ValueError("Sweep preflight failed; no motor connection:\n  " +
                         "\n  ".join(errors))
    return anchor, routes


def preview(editor, motions=MOTIONS):
    _, routes = preflight(editor, motions)
    print("LIVE 9-SQUARE SWEEP, NO MOTOR CONNECTION")
    print("Out:  " + " -> ".join(cell.upper() for cell in ORDER))
    print("Back: " + " -> ".join(cell.upper() for cell in ORDER[-2::-1]))
    print("Every visit: @sweep_home -> taught clearance -> square -> REVERSE -> @sweep_home")
    for cell in ORDER:
        print(f"  {cell.upper():5} {len(routes[cell])} linked/taught points, "
              f"{2 * (len(routes[cell]) - 1)} motor steps per visit")
    print("All joints checked for range and angle limits; per-step delta limited.")
    print("Ceiling/rack clearance CANNOT be proven from encoder positions. Inspect routes in person.")


def await_enter(terminal, message):
    print(f"\r\n{message} | ENTER=move, Space/Q/Esc=STOP", flush=True)
    while True:
        key = terminal.read_key(0.05)
        if key in STOP_KEYS:
            return False
        if key in ("\r", "\n"):
            return True


def run(editor, motions=MOTIONS, *, continuous=False):
    """Physical motion only after explicit confirmation and complete preflight."""
    anchor, _ = preflight(editor, motions)
    preview(editor, motions)
    print("Board EMPTY; arm supported; clear the rack and ceiling.")
    print("Start with the arm ALREADY at @sweep_home. No automatic homing.")
    print("Stop releases torque. This is NOT a collision-free path planner.")
    expected = "CONTINUOUS" if continuous else "RUN"
    if input(f"Type {expected} to enable physical sweep, otherwise cancel: ").strip() != expected:
        print("Cancelled; no hardware access.")
        return False

    port, handler = editor.connect()
    completed = False
    try:
        with editor.RawTerminal() as terminal:
            current = editor.read_all(handler)
            editor.validate_competition_target(current)
            if max_arm_delta(editor, current, anchor) > HOME_TOLERANCE:
                raise RuntimeError(
                    f"Arm not at @{HOME} (>{HOME_TOLERANCE} ticks away). "
                    "Stopped before enabling torque. Position it manually and retry.")
            # Hold measured pose first; never jump straight to another route.
            editor.move_all_sync(handler, current, speed=300, acceleration=20)
            if editor.stopped(terminal):
                return False
            editor.enable_all_torque(handler)

            for visit, cell in enumerate(ROUND_TRIP, 1):
                # Values are LIVE across visits, but frozen during each out/back leg.
                if home_pose(editor) != anchor:
                    raise RuntimeError("Home bookmark changed mid-sweep. Stopping.")
                route = read_route(editor, cell, anchor, motions)
                steps = [(i, route[i]) for i in range(1, len(route))]
                steps += [(i, route[i]) for i in range(len(route)-2, -1, -1)]
                print(f"\r\nVISIT {visit}/{len(ROUND_TRIP)}: {cell.upper()} via @{HOME}", flush=True)
                for index, target in steps:
                    if editor.stopped(terminal):
                        return False
                    # Keep the gripper at its measured START position: no grabbing.
                    target = dict(target, gripper=current["gripper"])
                    before = editor.read_all(handler)
                    if max_arm_delta(editor, before, target) > MAX_JOINT_STEP:
                        raise RuntimeError(f"{cell.upper()} point #{index+1}: "
                                           "unexpected live joint jump. Stopping.")
                    if not continuous and not await_enter(
                            terminal, f"{cell.upper()} point {index+1}/{len(route)}"):
                        return False
                    if editor.stopped(terminal):
                        return False
                    editor.move_all_sync(handler, target, speed=300, acceleration=20)
                    if not editor.wait_move(handler, terminal):
                        return False
                    measured = editor.read_all(handler)
                    if max_arm_delta(editor, measured, target) > ARRIVAL_TOLERANCE:
                        raise RuntimeError(
                            f"{cell.upper()} point #{index+1}: arm did not reach requested pose. "
                            "Stopped without starting the next move.")
            completed = True
        print("\nRound trip finished at @sweep_home. Torque remains on.")
        return True
    finally:
        try:
            if not completed:
                print("\nSTOP: releasing torque. SUPPORT the arm.", flush=True)
                editor.disable_all_torque(handler)
        finally:
            port.closePort()


def menu():
    editor = load_editor()
    print("\nLIVE 9-square test: I make teaching templates, P dry-run, R guided run, Q back")
    choice = input("sweep> ").strip().lower()
    if choice == "i":
        initialize_routes(editor)
    elif choice == "p":
        preview(editor)
    elif choice == "r":
        run(editor)
    elif choice != "q":
        print("Choose I, P, R, or Q.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--init", action="store_true", help="create safe teaching templates (no movement)")
    mode.add_argument("--run", action="store_true", help="guided physical test, Enter per waypoint")
    mode.add_argument("--continuous", action="store_true",
                      help="continuous physical test after separate confirmation; use only after guided test")
    args = parser.parse_args(argv)
    editor = load_editor()
    if args.init:
        initialize_routes(editor)
    elif args.run or args.continuous:
        run(editor, continuous=args.continuous)
    else:
        preview(editor)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, TimeoutError, KeyboardInterrupt, EOFError) as exc:
        print(f"\nSweep stopped: {exc}")
        raise SystemExit(1)
