#!/usr/bin/env python3
"""Full, live-updating square attacks, using only the Pi's local M/P recordings.

Runs M forward -> M reverse -> M forward -> P forward for each selected
square. This is an ADD-ON to script 6: no existing script or motion JSON is
created, edited, reset, or synchronized by this file.

Waypoint VALUE edits are reloaded before each target. Adding/removing points
mid-square causes a safe abort rather than an unexpected change of index.
New recordings and point counts are picked up before each next square.
"""

import argparse
import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EDITOR = ROOT / "scripts" / "06_live_grid_tool.py"
MOTIONS = ROOT / "motions"
SQUARE_PATTERN = re.compile(r"c[0-9]+r[0-9]+", re.I)


def load_local_editor():
    if not EDITOR.is_file():
        raise FileNotFoundError(f"Cannot find local script 6: {EDITOR}")
    spec = importlib.util.spec_from_file_location("pi_local_script6", EDITOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    necessary = ("wp", "validate_target", "connect", "read_all", "move_all_sync",
                 "enable_all_torque", "disable_all_torque", "RawTerminal", "wait_move",
                 "stopped")
    missing = [name for name in necessary if not hasattr(module, name)]
    if missing:
        raise RuntimeError("Local script 6 is missing expected helpers: " + ", ".join(missing))
    return module


def available_squares():
    found = {}
    for move in sorted(MOTIONS.glob("*m.json")):
        square = move.stem[:-1].lower()
        if SQUARE_PATTERN.fullmatch(square) and (MOTIONS / f"{square}p.json").is_file():
            found[square] = move
    return found


def phases(move):
    place = move.with_name(move.stem[:-1] + "p.json")
    return (("M forward", move, False),
            ("M reverse", move, True),
            ("M forward again", move, False),
            ("P forward", place, False))


def read_and_validate(editor, path):
    _, points = editor.wp.load_sequence(path)
    if not points:
        raise ValueError(f"{path.name} has no waypoints")
    for i, point in enumerate(points, 1):
        try:
            editor.validate_target(point)
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError(f"{path.name} waypoint {i}: {exc}") from exc
    return points


def preflight(editor, moves):
    """Validate all current local recordings BEFORE connecting to hardware."""
    for move in moves:
        place = move.with_name(move.stem[:-1] + "p.json")
        read_and_validate(editor, move)
        read_and_validate(editor, place)


def live_targets(editor, move):
    """Reload target values immediately before EVERY motor command.

    Freeze each file's point count for this one square. If the user adds or
    removes points while playing, stop rather than accidentally retarget.
    """
    place = move.with_name(move.stem[:-1] + "p.json")
    counts = {
        move: len(read_and_validate(editor, move)),
        place: len(read_and_validate(editor, place)),
    }
    for phase_name, path, backwards in phases(move):
        count = counts[path]
        indices = range(count - 1, -1, -1) if backwards else range(count)
        for index in indices:
            _, fresh = editor.wp.load_sequence(path)
            if len(fresh) != count:
                raise RuntimeError(
                    f"Waypoint count changed in {path.name} during {move.stem[:-1]}; "
                    "stopping. Restart this square to use the new route.")
            target = fresh[index]
            editor.validate_target(target)
            yield f"{move.stem[:-1]} | {phase_name} | waypoint {index + 1}/{count}", target


def preview(editor, moves):
    for move in moves:
        print(f"\n{move.stem[:-1]} (local recordings only):")
        for phase_name, path, backwards in phases(move):
            count = len(read_and_validate(editor, path))
            numbers = range(count, 0, -1) if backwards else range(1, count + 1)
            print(f"  {phase_name:<16} {path.stem}: " + " -> ".join(map(str, numbers)))
    print("\nDRY RUN: no robot connection, motion, or file changes.")


def run(editor, moves, *, auto):
    print("\nUsing LIVE local recordings. No motion JSON files will be written.")
    print("Stop: Space/Q/Esc/Ctrl+C releases motor torque. Support the arm.")
    print("Angle limits are checked; collision clearance and block readiness are NOT checked.")
    if auto:
        print("AUTO: no readiness pauses between squares. Ensure all blocks are clear.")
    port, handler = editor.connect()
    completed = False
    try:
        with editor.RawTerminal() as terminal:
            current = editor.read_all(handler)
            editor.move_all_sync(handler, current, speed=300, acceleration=20)
            if editor.stopped(terminal):
                return
            editor.enable_all_torque(handler)
            for move in moves:
                square = move.stem[:-1]
                if not auto:
                    print(f"\r\n{square}: prepare the square, then ENTER. Stop = Space/Q/Esc/Ctrl+C.", flush=True)
                    while True:
                        key = terminal.read_key(0.05)
                        if key in getattr(editor, "STOP_KEYS", {" ", "q", "\x03", "\x1b"}):
                            return
                        if key in ("\r", "\n"):
                            break
                if editor.stopped(terminal):
                    return
                for label, target in live_targets(editor, move):
                    if editor.stopped(terminal):
                        return
                    print("\r\n" + label, flush=True)
                    editor.move_all_sync(handler, target, speed=300, acceleration=20)
                    if not editor.wait_move(handler, terminal):
                        return
            completed = True
        print("\nCompleted all selected squares. Torque remains ON at the final pose.")
    finally:
        try:
            if not completed:
                print("\nSTOP: releasing motor torque; support the arm.", flush=True)
                editor.disable_all_torque(handler)
        finally:
            port.closePort()


def select_squares(args, squares):
    if not squares:
        raise ValueError(f"No local matching c#r#m.json / c#r#p.json pairs in {MOTIONS}")
    if args.all:
        return list(squares.values())
    if args.square:
        codes = args.square
    else:
        print("Local square routes: " + ", ".join(squares))
        answer = input("Squares (e.g. c1r1 c2r2), ALL, or Q: ").strip().lower()
        if answer in ("q", ""):
            return []
        codes = list(squares) if answer == "all" else answer.replace(",", " ").split()
    selected = []
    for code in codes:
        code = code.lower()
        if code not in squares:
            raise ValueError(f"Unknown local square {code!r}. Choose: {', '.join(squares)}")
        if squares[code] not in selected:
            selected.append(squares[code])
    return selected


def main():
    parser = argparse.ArgumentParser(description="Full live local square attack: M, reverse M, M, P")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--square", nargs="+", metavar="CELL", help="e.g. --square c1r1 c2r2")
    group.add_argument("--all", action="store_true", help="every square with a local M/P pair")
    parser.add_argument("--dry-run", action="store_true", help="print route; never connect to arm")
    parser.add_argument("--auto", action="store_true", help="skip per-square ENTER confirmation")
    args = parser.parse_args()
    editor = load_local_editor()
    moves = select_squares(args, available_squares())
    if not moves:
        print("Cancelled.")
        return
    preflight(editor, moves)
    if args.dry_run:
        preview(editor, moves)
        return
    run(editor, moves, auto=args.auto)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, RuntimeError, TimeoutError, KeyboardInterrupt, EOFError) as exc:
        print(f"\nStopped: {exc}")
        raise SystemExit(1)
