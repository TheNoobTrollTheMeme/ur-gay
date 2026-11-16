#!/usr/bin/env python3
"""Control the ENGG1101 board using recorded Script 6 motions."""
import argparse
from dataclasses import dataclass, replace
from functools import partial
import importlib.util
from pathlib import Path
from queue import Empty, Queue
import sys
from threading import Event, Thread


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
    if waypoint.positions.keys() != set(script6.settings.JOINT_NAMES):
        raise ValueError(f"{waypoint.route} #{waypoint.number}: joint names do not match the arm settings.")
    if any(type(tick) is not int or not 0 <= tick <= 4095 for tick in waypoint.positions.values()):
        raise ValueError(f"{waypoint.route} #{waypoint.number}: invalid raw servo tick.")
    try:
        script6.validate_target(waypoint.positions)
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


class StopInput:
    """Translate a GUI stop request into a Script 6 stop key."""

    def __init__(self, requested):
        self.requested = requested

    def read_key(self, timeout):
        return " " if self.requested.wait(timeout) else None


def play(script6, plan, requested, log):
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


def run_window(root, ui_class, script6, motions):
    from tkinter import messagebox

    events = Queue()
    stop = Event()
    busy = closing = False

    def worker(plan):
        try:
            completed = play(script6, plan, stop, lambda line: events.put(("log", line)))
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
    root.after(40, poll)
    root.mainloop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, help="Path to the arm repo")
    parser.add_argument("--terminal", action="store_true", help="Use the keyboard-operated terminal board")
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
    motions = repo / "motions"
    if args.terminal:
        import curses
        from arm_board_terminal import run_terminal

        try:
            run_terminal(partial(build_plan, script6, motions), partial(play, script6))
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
    run_window(root, ArmBoardUI, script6, motions)


if __name__ == "__main__":
    main()
