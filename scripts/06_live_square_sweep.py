#!/usr/bin/env python3
"""Replay each saved square move forward, then retrace its recorded waypoints.

Order: c1r1m, c1r2m, c1r3m, c2r1m, c2r2m, c2r3m, c3r1m, c3r2m, c3r3m.
Each installed motions/c#r#m.json runs once: 1 -> 2 -> ... -> N -> ... -> 2 -> 1.
The return uses the same resolved waypoint snapshot, including the gripper.
The next recording is reloaded before its turn. No generated home or route.
"""
from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
from playback_speed import (DEFAULT_SPEED, DEFAULT_ACCELERATION,
                            validate_playback_settings)

MOTIONS = ROOT / "motions"
EDITOR_PATH = SCRIPTS / "06_live_grid_tool.py"
ORDER = ("c1r1", "c1r2", "c1r3", "c2r1", "c2r2", "c2r3", "c3r1", "c3r2", "c3r3")
ARRIVAL_TOLERANCE = 90
STOP_KEYS = frozenset((" ", "q", "\x03", "\x1b"))


def load_editor():
    """Import installed Script 6, including its normal bookmark resolver."""
    sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location("sweep_script6", EDITOR_PATH)
    if spec is None or spec.loader is None:
        raise FileNotFoundError(f"Script 6 missing: {EDITOR_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def route_path(cell, motions=MOTIONS):
    if cell not in ORDER:
        raise ValueError(f"Unknown board cell: {cell}")
    return Path(motions) / f"{cell}m.json"


def arm_joints(editor):
    return tuple(j for j in editor.settings.JOINT_NAMES if j != "gripper")


def max_arm_delta(editor, a, b):
    return max(abs(int(a[j]) - int(b[j])) for j in arm_joints(editor))


def read_route(editor, cell, motions=MOTIONS):
    """Resolve the existing move recording once and copy its complete poses."""
    path = route_path(cell, motions)
    if not path.is_file():
        raise ValueError(
            f"Missing {path}. Sweep uses your installed motions/{cell}m.json recording.")
    data, points = editor.wp.load_sequence(path)
    if not isinstance(data, dict) or data.get("input_type") != "raw":
        raise ValueError(f"{path.name}: expected a saved raw position recording.")
    definitions = data.get("points")
    if not isinstance(definitions, list) or len(definitions) < 2:
        raise ValueError(f"{path.name}: needs at least two saved waypoints.")
    if len(points) != len(definitions):
        raise ValueError(f"{path.name}: number of decoded waypoints changed.")
    for point in points:
        # Match raw COMP playback: exact joints and integer servo register range.
        editor.validate_competition_target(point)
    return tuple(dict(point) for point in points)


def round_trip_steps(route):
    """A,B,C becomes A,B,C,B,A; omit only the repeated turnaround endpoint."""
    snapshot = tuple(dict(point) for point in route)
    if len(snapshot) < 2:
        raise ValueError("A sweep route needs at least two waypoints.")
    return tuple(dict(point) for point in snapshot + snapshot[-2::-1])


def preflight(editor, motions=MOTIONS):
    """Validate all nine installed move recordings before connecting."""
    routes, errors = {}, []
    for cell in ORDER:
        try:
            routes[cell] = read_route(editor, cell, motions)
        except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
            errors.append(f"{cell}m: {exc}")
    if errors:
        raise ValueError("Sweep preflight failed; no motor connection:\n  " +
                         "\n  ".join(errors))
    return routes


def _show_plan(routes, *, speed, acceleration):
    print("SAVED MOVE SWEEP: each recording forward, then the SAME waypoints back")
    print("Order: " + " -> ".join(f"{cell}m" for cell in ORDER))
    for cell in ORDER:
        count = len(routes[cell])
        indices = list(range(1, count + 1)) + list(range(count - 1, 0, -1))
        print(f"  {cell}m: " + " -> ".join(map(str, indices)))
    print(f"Arm speed={speed}, acceleration={acceleration}; normal gripper speed.")
    print("Each route starts at its saved first point and returns there before the next route.")
    print("All saved joint values are kept. No placement sequence or generated path is added.")


def preview(editor, motions=MOTIONS, *, speed=DEFAULT_SPEED,
            acceleration=DEFAULT_ACCELERATION):
    speed, acceleration = validate_playback_settings(speed, acceleration)
    routes = preflight(editor, motions)
    _show_plan(routes, speed=speed, acceleration=acceleration)
    print("PREVIEW: no motor connection.")
    return routes


def await_enter(terminal, message):
    print(f"\r\n{message} | ENTER=move, Space/Q/Esc=STOP", flush=True)
    while True:
        key = terminal.read_key(0.05)
        if key in STOP_KEYS:
            return False
        if key in ("\r", "\n"):
            return True


def run(editor, motions=MOTIONS, *, continuous=False, speed=DEFAULT_SPEED,
        acceleration=DEFAULT_ACCELERATION):
    """Play each move and its exact reverse; one snapshot per out-and-back."""
    speed, acceleration = validate_playback_settings(speed, acceleration)
    routes = preflight(editor, motions)
    _show_plan(routes, speed=speed, acceleration=acceleration)
    print("Check clearance to the first saved pose of each recording.")
    print("Stop = Space/Q/Esc/Ctrl+C; stopping releases torque, so support the arm.")
    expected = "CONTINUOUS" if continuous else "RUN"
    if input(f"Type {expected} to start the sweep, otherwise cancel: ").strip() != expected:
        print("Cancelled; no hardware access.")
        return False
    if speed > 600 or acceleration > 35:
        if input("High servo speed. Type HIGH to confirm this run: ").strip() != "HIGH":
            print("High-speed run cancelled; no hardware access.")
            return False
    # Catch edits made while the operator was reading the plan or confirming.
    preflight(editor, motions)

    port, handler = editor.connect()
    completed = False
    try:
        with editor.RawTerminal() as terminal:
            current = editor.read_all(handler)
            editor.validate_competition_target(current)
            if editor.stopped(terminal):
                return False
            # Hold the measured pose before enabling torque, as in normal playback.
            editor.move_all_sync(handler, current, speed=DEFAULT_SPEED,
                                 acceleration=DEFAULT_ACCELERATION)
            if editor.stopped(terminal):
                return False
            editor.enable_all_torque(handler)

            for visit, cell in enumerate(ORDER, 1):
                if editor.stopped(terminal):
                    return False
                # Resolve current file/bookmarks ONCE for both directions of this cell.
                route = read_route(editor, cell, motions)
                steps = round_trip_steps(route)
                indices = list(range(len(route))) + list(range(len(route) - 2, -1, -1))
                print(f"\r\n{visit}/9: {cell}m FORWARD then SAME PATH REVERSE", flush=True)
                for step, (index, target) in enumerate(zip(indices, steps)):
                    if editor.stopped(terminal):
                        return False
                    direction = "forward" if step < len(route) else "reverse"
                    label = f"{cell}m {direction}: waypoint {index + 1}/{len(route)}"
                    if not continuous and not await_enter(terminal, label):
                        return False
                    if editor.stopped(terminal):
                        return False
                    print(f"\r\n{label}", flush=True)
                    editor.move_for_playback(handler, dict(target), speed, acceleration)
                    if not editor.wait_move(handler, terminal):
                        return False
                    measured = editor.read_all(handler)
                    editor.validate_competition_target(measured)
                    if max_arm_delta(editor, measured, target) > ARRIVAL_TOLERANCE:
                        raise RuntimeError(
                            f"{label}: arm did not reach requested pose. "
                            "Stopped without starting the next move.")
            completed = True
        print(f"\nSweep finished: all nine moves reversed; holding {ORDER[-1]}m waypoint 1.")
        return True
    finally:
        try:
            if not completed:
                print("\nSTOP: releasing torque. SUPPORT the arm.", flush=True)
                editor.disable_all_torque(handler)
        finally:
            port.closePort()


def audit_existing(editor, motions=MOTIONS):
    """Report the installed move recordings without writing or connecting."""
    print("SAVED c#r#m ROUTE AUDIT (no hardware connection)")
    routes, flagged = {}, 0
    for cell in ORDER:
        try:
            route = read_route(editor, cell, motions)
            routes[cell] = route
            largest = max(max_arm_delta(editor, a, b)
                          for a, b in zip(route, route[1:]))
            print(f"  {cell}m: {len(route)} saved points; "
                  f"{2 * len(route) - 1} forward/reverse targets; "
                  f"largest recorded joint step {largest} ticks")
        except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
            print(f"  {cell}m: INVALID: {exc}")
            flagged += 1
    for previous, following in zip(ORDER, ORDER[1:]):
        if previous in routes and following in routes:
            delta = max_arm_delta(editor, routes[previous][0], routes[following][0])
            print(f"  {previous}m start -> {following}m start: {delta} ticks")
    print(f"Valid move recordings: {len(routes)}/9; invalid or missing: {flagged}.")
    return {"flagged_routes": flagged, "square_count": len(routes)}


def menu(*, speed=DEFAULT_SPEED, acceleration=DEFAULT_ACCELERATION):
    speed, acceleration = validate_playback_settings(speed, acceleration)
    editor = load_editor()
    print("\nSAVED MOVE SWEEP: c1r1m + reverse, c1r2m + reverse, ... c3r3m + reverse")
    print("C Continuous | R Guided (Enter per point) | P Preview | A Audit | Q Back")
    choice = input("sweep> ").strip().lower()
    if choice == "a":
        audit_existing(editor)
    elif choice == "p":
        preview(editor, speed=speed, acceleration=acceleration)
    elif choice in ("r", "c"):
        raw_speed = input(f"Arm speed [{speed}]: ").strip()
        raw_accel = input(f"Acceleration [{acceleration}]: ").strip()
        run(editor, continuous=choice == "c",
            speed=int(raw_speed) if raw_speed else speed,
            acceleration=int(raw_accel) if raw_accel else acceleration)
    elif choice not in ("q", ""):
        print("Choose C, R, P, A, or Q.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--audit", action="store_true", help="inspect existing c#r#m recordings")
    mode.add_argument("--menu", action="store_true", help="open the sweep options")
    mode.add_argument("--run", action="store_true", help="guided physical test, Enter per waypoint")
    mode.add_argument("--continuous", action="store_true",
                      help="play all nine forward/reverse routes without per-waypoint Enter")
    parser.add_argument("--speed", type=int, default=DEFAULT_SPEED,
                        help="arm speed 50..1200 (default 300)")
    parser.add_argument("--accel", "--acceleration", dest="acceleration", type=int,
                        default=DEFAULT_ACCELERATION,
                        help="arm acceleration 1..60 (default 20)")
    args = parser.parse_args(argv)
    validate_playback_settings(args.speed, args.acceleration)
    if args.menu:
        menu(speed=args.speed, acceleration=args.acceleration)
        return
    editor = load_editor()
    if args.audit:
        audit_existing(editor)
    elif args.run or args.continuous:
        run(editor, continuous=args.continuous, speed=args.speed,
            acceleration=args.acceleration)
    else:
        preview(editor, speed=args.speed, acceleration=args.acceleration)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, TimeoutError,
            KeyboardInterrupt, EOFError) as exc:
        print(f"\nSweep stopped: {exc}")
        raise SystemExit(1)
