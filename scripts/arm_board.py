#!/usr/bin/env python3
"""Control the ENGG1101 board using recorded Script 6 motions."""
import argparse
from dataclasses import dataclass, replace
from datetime import datetime
from functools import partial
import importlib.util
from itertools import count
import json
import os
from pathlib import Path
from queue import Empty, Queue
import re
import sys
from tempfile import TemporaryDirectory
from threading import Event, Thread


WINNING_LINES = {
    frozenset(line.split()) for line in (
        "C1R1 C2R1 C3R1", "C1R2 C2R2 C3R2", "C1R3 C2R3 C3R3",
        "C1R1 C1R2 C1R3", "C2R1 C2R2 C2R3", "C3R1 C3R2 C3R3",
        "C1R1 C2R2 C3R3", "C3R1 C2R2 C1R3",
    )
}
WINNING_LINE_FILE = re.compile(r"winning_line_(c[1-3]r[1-3])_(c[1-3]r[1-3])_(c[1-3]r[1-3])(?:_\d+)?\.json")


@dataclass(frozen=True)
class Waypoint:
    route: str
    number: int
    positions: dict[str, int]
    use_current_gripper: bool = False


@dataclass(frozen=True)
class Plan:
    cell: str
    mode: str
    pickup: int | None
    phases: tuple[str, ...]
    waypoints: tuple[Waypoint, ...]

    @property
    def requires_held_block(self):
        return self.mode == "attack" and self.pickup is None


def load_script6(repo):
    script = repo / "scripts" / "06_live_grid_tool.py"
    if not script.is_file():
        raise FileNotFoundError(f"Script 6 missing: {script}. Set --repo to the arm repo.")
    # Match the import path of running Script 6 directly from scripts/.
    sys.path.insert(0, str(script.parent))
    spec = importlib.util.spec_from_file_location("script6", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def pickup_for(col, row, mode, selected):
    if selected is not None and selected not in range(1, 7):
        raise ValueError("Pick a tray slot from 1 to 6.")
    if mode == "place":
        return selected
    if mode != "attack":
        raise ValueError(f"Unknown mode: {mode}")
    if (col, row) == (2, 2):
        if selected in (3, 6):
            raise ValueError("For center attack, select a cube or cylinder, or clear the tray selection if one is already held.")
        return selected
    return 3 if col <= 2 else 6


def read_route(script6, folder, stem):
    path = folder / f"{stem}.json"
    if not path.is_file():
        raise ValueError(f"Missing recording: {path.name}")
    try:
        _, positions = script6.wp.load_sequence(path)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        raise ValueError(f"{path.name}: {error}") from error
    if not positions:
        raise ValueError(f"{path.name}: no waypoints")
    return tuple(
        Waypoint(stem, index, dict(values))
        for index, values in enumerate(positions, 1)
    )


def validate_waypoint(script6, waypoint):
    try:
        script6.validate_competition_target(waypoint.positions)
    except (KeyError, ValueError) as error:
        raise ValueError(f"{waypoint.route} #{waypoint.number}: {error}") from error


def build_plan(script6, folder, col, row, mode, selected=None):
    if col not in (1, 2, 3) or row not in (1, 2, 3):
        raise ValueError("Choose a cell on the 3×3 board.")
    cell = f"c{col}r{row}"
    pickup = pickup_for(col, row, mode, selected)
    move = read_route(script6, folder, f"{cell}m")
    place = read_route(script6, folder, f"{cell}p")
    grab = read_route(script6, folder, f"grab{pickup}") if pickup else ()
    for waypoint in (*grab, *move, *place):
        validate_waypoint(script6, waypoint)

    if (grab or mode == "attack") and "gripper" not in script6.settings.JOINT_NAMES:
        raise ValueError("The arm settings have no joint named gripper.")
    if grab:
        held = grab[-1].positions["gripper"]
        move = tuple(replace(wp, positions={**wp.positions, "gripper": held}) for wp in move)
    elif mode == "attack":
        move = tuple(replace(wp, use_current_gripper=True) for wp in move)

    if mode == "attack":
        if len(move) < 2:
            raise ValueError(f"{cell}m needs at least two waypoints to reverse.")
        # Avoid commanding the same turnaround pose twice.
        approach = (*move, *reversed(move[:-1]), *move[1:])
        phases = (f"{cell}m", f"{cell}m reverse", f"{cell}m forward", f"{cell}p")
    else:
        approach = move
        phases = (f"{cell}m", f"{cell}p")
    if pickup:
        phases = (f"grab{pickup}", *phases)
    for waypoint in approach:
        validate_waypoint(script6, waypoint)
    return Plan(cell.upper(), mode, pickup, phases, (*grab, *approach, *place))


def compose_queue(plans):
    if not plans:
        raise ValueError("The placement queue is empty.")
    if any(plan.mode != "place" for plan in plans):
        raise ValueError("Only placements can be added to the board queue.")
    return Plan(
        cell=f"{len(plans)} queued placements",
        mode="place",
        pickup=None,
        phases=tuple(plan.cell for plan in plans),
        waypoints=tuple(waypoint for plan in plans for waypoint in plan.waypoints),
    )


def winning_line_motion(script6, plans):
    cells = tuple(plan.cell for plan in plans)
    if len(cells) != 3 or frozenset(cells) not in WINNING_LINES:
        raise ValueError("Queue three distinct squares in a row, column, or diagonal.")
    plan = compose_queue(plans)
    joints = script6.settings.JOINT_NAMES
    base = "winning_line_" + "_".join(cell.lower() for cell in cells)
    return {
        "schema_version": 1,
        "type": "pose_sequence",
        "name": base,
        "description": "Winning line " + " > ".join(cells),
        "input_type": "raw",
        "created_at": datetime.now().astimezone().isoformat(),
        "joint_order": [
            {"joint": joint, "servo_id": script6.settings.SERVO_IDS[joint]} for joint in joints
        ],
        "source_placements": [{"cell": item.cell, "pickup": item.pickup} for item in plans],
        "points": [
            {"input_values": [waypoint.positions[joint] for joint in joints]}
            for waypoint in plan.waypoints
        ],
    }


def export_winning_line(script6, folder, plans):
    sequence = winning_line_motion(script6, plans)
    base = sequence["name"]
    with TemporaryDirectory(prefix=".winning-line-", dir=folder) as directory:
        temporary = Path(directory) / "sequence.json"
        for number in count(1):
            name = base if number == 1 else f"{base}_{number}"
            output = folder / f"{name}.json"
            if output.exists():
                continue
            sequence["name"] = name
            script6.wp.save_sequence(temporary, sequence)
            try:
                os.link(temporary, output)
            except FileExistsError:
                continue
            return output


def read_winning_line(path):
    match = WINNING_LINE_FILE.fullmatch(path.name)
    if not match:
        raise ValueError("Enter a saved winning line filename.")
    original = path.read_bytes()
    data = json.loads(original)
    if not isinstance(data, dict) or data.get("type") != "pose_sequence" or data.get("name") != path.stem:
        raise ValueError("File is not a board winning-line export.")
    return tuple(cell.upper() for cell in match.groups()), original, data


def refresh_winning_line(script6, path, cells, original, data, recipe):
    if not isinstance(recipe, list) or len(recipe) != len(cells):
        raise ValueError("Invalid saved tray selections.")
    plans = []
    for cell, item in zip(cells, recipe):
        if not isinstance(item, dict) or item.get("cell") != cell or "pickup" not in item:
            raise ValueError("Saved tray selections do not match the line in the filename.")
        pickup = item["pickup"]
        if pickup is not None and (type(pickup) is not int or pickup not in range(1, 7)):
            raise ValueError(f"Invalid tray selection for {cell}.")
        plans.append(build_plan(script6, path.parent, int(cell[1]), int(cell[3]), "place", pickup))

    rebuilt = winning_line_motion(script6, plans)
    updated = {
        **data,
        **{key: rebuilt[key] for key in ("input_type", "joint_order", "source_placements", "points")},
    }
    if data == updated:
        return False
    updated["updated_at"] = datetime.now().astimezone().isoformat()
    with TemporaryDirectory(prefix=".winning-line-", dir=path.parent) as directory:
        temporary = Path(directory) / path.name
        script6.wp.save_sequence(temporary, updated)
        if path.read_bytes() != original:
            raise ValueError("Saved line changed during sync. Try again.")
        temporary.replace(path)
    return True


def sync_winning_lines(script6, folder):
    results = []
    for path in sorted(folder.glob("winning_line_*.json")):
        if not WINNING_LINE_FILE.fullmatch(path.name):
            continue
        try:
            cells, original, data = read_winning_line(path)
            if "source_placements" not in data:
                results.append(f"{path.name}: queue its placements and press U once to link it.")
                continue
            changed = refresh_winning_line(script6, path, cells, original, data, data["source_placements"])
        except (OSError, ValueError, RuntimeError) as error:
            results.append(f"{path.name}: {error}")
        else:
            results.append(f"{path.name}: {'updated' if changed else 'already current'}")
    return results or ["No saved winning lines found."]


def link_winning_line(script6, folder, plans, filename):
    sequence = winning_line_motion(script6, plans)
    filename = filename.strip() or sequence["name"]
    if not filename.endswith(".json"):
        filename += ".json"
    if not WINNING_LINE_FILE.fullmatch(filename):
        raise ValueError("Enter a saved winning line filename.")
    path = folder / filename
    cells, original, data = read_winning_line(path)
    if cells != tuple(plan.cell for plan in plans):
        raise ValueError("Queue the squares in the same order as the saved filename.")
    refresh_winning_line(script6, path, cells, original, data, sequence["source_placements"])
    return path


class StopInput:
    """Translate a GUI stop request into a Script 6 stop key."""

    def __init__(self, requested):
        self.requested = requested

    def read_key(self, timeout):
        return " " if self.requested.wait(timeout) else None


def play(script6, plan, requested, log, *, turbo=False):
    if requested.is_set():
        return False
    port, handler = script6.connect()
    completed = False
    try:
        current = script6.read_all(handler)
        waypoints = plan.waypoints
        if plan.requires_held_block:
            waypoints = tuple(
                replace(wp, positions={**wp.positions, "gripper": current["gripper"]})
                if wp.use_current_gripper else wp
                for wp in waypoints
            )
            for waypoint in waypoints:
                validate_waypoint(script6, waypoint)
        if requested.is_set():
            return False
        # Same start and settle behavior as Script 6 saved-sequence playback.
        script6.move_all_sync(handler, current, speed=300, acceleration=20)
        if requested.is_set():
            return False
        script6.enable_all_torque(handler)
        stop = StopInput(requested)
        for index, waypoint in enumerate(waypoints, 1):
            log(f"{index}/{len(waypoints)} · {waypoint.route.upper()} #{waypoint.number}")
            if requested.is_set():
                return False
            if turbo:
                script6.competition.sync_targets(
                    handler, script6.settings, script6.check_result, waypoint.positions, "turbo",
                )
            else:
                script6.move_all_sync(handler, waypoint.positions, speed=300, acceleration=20)
            if not script6.wait_move(handler, stop):
                return False
        completed = not requested.is_set()
        return completed
    finally:
        try:
            if not completed:
                script6.disable_all_torque(handler)
        finally:
            port.closePort()


def run_window(root, ui_class, script6, motions, *, turbo=False):
    from tkinter import messagebox

    events = Queue()
    stop = Event()
    busy = closing = False

    def worker(plan):
        try:
            completed = play(script6, plan, stop, lambda line: events.put(("log", line)), turbo=turbo)
        except Exception as error:
            events.put(("done", f"Run failed · {error}"))
        else:
            events.put(("done", "Finished · torque holding" if completed else "Stopped"))

    def square(col, row, mode, grab, live):
        nonlocal busy
        if busy:
            return
        try:
            plan = build_plan(script6, motions, col, row, mode, grab)
        except (OSError, ValueError, RuntimeError) as error:
            ui.set_status(str(error))
            ui.add_log(str(error))
            return

        summary = " → ".join(plan.phases)
        ui.add_log(f"{plan.cell} · {summary} · {len(plan.waypoints)} waypoints")
        if plan.requires_held_block:
            ui.add_log("Center · use a held cylinder or cube, or select its tray slot.")
        if not live:
            ui.set_status(f"Preview · {plan.cell} · {len(plan.waypoints)} waypoints")
            return
        if plan.mode == "attack":
            detail = "The arm will use the recorded approach in reverse, then approach and place."
            if plan.requires_held_block:
                detail += "\n\nConfirm a cylinder or cube is already in the gripper."
            if not messagebox.askyesno(f"Run {plan.cell} attack?", f"{summary}\n\n{detail}", parent=root):
                ui.set_status("Run cancelled")
                return
        stop.clear()
        busy = True
        ui.set_busy(True)
        ui.set_status(f"Running · {plan.cell}")
        Thread(target=worker, args=(plan,), name="arm-playback").start()

    def request_stop():
        if busy:
            stop.set()
            ui.set_status("Stopping · releasing torque")

    def close():
        nonlocal closing
        if not busy:
            root.destroy()
        elif messagebox.askyesno("Stop and close?", "Stopping releases torque. Support the arm.", parent=root):
            closing = True
            request_stop()

    def poll():
        nonlocal busy
        while True:
            try:
                kind, line = events.get_nowait()
            except Empty:
                break
            ui.add_log(line)
            if kind == "done":
                busy = False
                ui.set_busy(False)
                ui.set_status(line)
                if closing:
                    root.destroy()
                    return
        root.after(40, poll)

    ui = ui_class(root, square, request_stop, close)
    if turbo:
        root.title("Arm Board · Turbo")
    root.after(40, poll)
    root.mainloop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, help="Path to the arm repo")
    parser.add_argument("--terminal", action="store_true", help="Use the keyboard-operated terminal board")
    parser.add_argument("--turbo", action="store_true", help="Use the Competition Turbo profile with normal gripper speed")
    args = parser.parse_args()
    if args.repo:
        repo = args.repo.expanduser().resolve()
    else:
        here = Path(__file__).resolve().parent
        candidates = (Path.cwd(), here, here.parent)
        repo = next((path for path in candidates if (path / "scripts/06_live_grid_tool.py").is_file()), Path.cwd())
    try:
        script6 = load_script6(repo)
    except (OSError, ImportError) as error:
        parser.exit(1, f"Could not load Script 6: {error}\n")
    if args.turbo and not callable(getattr(script6.competition, "sync_targets", None)):
        parser.exit(1, "Installed competition queue does not support per-joint Turbo playback.\n")
    motions = repo / "motions"
    if args.terminal:
        import curses
        from arm_board_terminal import run_terminal

        try:
            run_terminal(
                partial(build_plan, script6, motions),
                partial(play, script6, turbo=args.turbo),
                compose_queue=compose_queue,
                export_winning_line=partial(export_winning_line, script6, motions),
                sync_winning_lines=partial(sync_winning_lines, script6, motions),
                link_winning_line=partial(link_winning_line, script6, motions),
                turbo=args.turbo,
            )
        except curses.error as error:
            parser.exit(1, f"Terminal board could not open: {error}\n")
        return
    try:
        import tkinter as tk
        from arm_board_ui import ArmBoardUI
    except ImportError as error:
        parser.exit(1, f"Could not load Tkinter or board UI: {error}\n")
    try:
        root = tk.Tk()
    except tk.TclError as error:
        parser.exit(1, f"Could not open a window: {error}. Use --terminal for a terminal session.\n")
    run_window(root, ArmBoardUI, script6, motions, turbo=args.turbo)


if __name__ == "__main__":
    main()
