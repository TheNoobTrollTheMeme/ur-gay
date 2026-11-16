"""Keyboard-operated arm board for a Pi terminal."""
from collections import deque
import curses
from queue import Empty, Queue
from threading import Event, Thread
from textwrap import wrap


TRAY_LABELS = {
    1: "Cylinder", 2: "Cube", 3: "Trophy",
    4: "Cube", 5: "Cylinder", 6: "Trophy",
}
TRAY_ROWS = (
    ("        2 Cube", "3 Trophy   4 Cube", "5 Cylinder"),
    ("1 Cylinder", "", "           6 Trophy"),
)
ENTER_KEYS = (10, 13, curses.KEY_ENTER)
BACKSPACE_KEYS = (8, 127, curses.KEY_BACKSPACE)


def draw(screen, cursor, mode, live, pickup, selecting, pending, status, activity, busy, turbo, queue_count, link_filename):
    screen.erase()
    height, width = screen.getmaxyx()

    def line(y, text, attr=0, x=0):
        if 0 <= y < height and x < width - 1:
            screen.addnstr(y, x, text, width - x - 1, attr)

    line(0, f"ARM BOARD   {mode.upper()}   {'LIVE' if live else 'PREVIEW'}   {'TURBO' if turbo else 'NORMAL'}", curses.A_BOLD)
    placement_keys = (
        f"1-9 or arrows + Enter add a square   Queued: {queue_count}"
        if mode == "queue" else "1-9 selects a square   Arrows + Enter also work"
    )
    line(1, placement_keys)
    for row in range(3):
        for col in range(3):
            key = row * 3 + col + 1
            attr = curses.A_REVERSE if (col, row) == cursor else curses.A_NORMAL
            line(3 + row, f" {key} C{col + 1}R{row + 1} ", attr, 12 * col)

    line(7, "LEFT TRAY           CENTER TRAY         RIGHT TRAY", curses.A_BOLD)
    for row, trays in enumerate(TRAY_ROWS, 8):
        for col, tray in enumerate(trays):
            line(row, tray, x=20 * col)

    if selecting:
        pickup_label = "Press 1-6 for a tray slot, or 0 to clear"
    elif pickup is None:
        pickup_label = "Tray: no slot selected"
    else:
        pickup_label = f"Tray: Grab {pickup} ({TRAY_LABELS[pickup]})"
    line(11, pickup_label)
    line(12, "G tray   0 clear tray   B board/queue   L live/preview")
    if mode == "queue":
        line(13, "Space/Esc stop   Q quit   S sync lines   U link old line")
        line(14, "W export line   R run   Backspace undo   C clear")
    else:
        line(13, "Space/Esc stop   Q quit")
        line(14, "A attack/place")
    if link_filename is not None:
        line(16, f"File: {link_filename or '<matching filename>'}", curses.A_BOLD)
    elif pending:
        help_text = "Confirm a held cylinder or cube. Y run / N cancel." if pending.requires_held_block else "Y run attack / N cancel."
        line(16, help_text, curses.A_BOLD)
    elif busy:
        line(16, "Playback active", curses.A_BOLD)

    line(15, status, curses.A_BOLD)
    if height > 17 and width > 1:
        lines = []
        for entry in activity:
            lines.extend(wrap(entry, width=width - 1, break_on_hyphens=False))
        for y, entry in enumerate(lines[-(height - 17):], 17):
            line(y, entry)
    screen.refresh()


def terminal_loop(screen, build_plan, play, compose_queue, export_winning_line, sync_winning_lines, link_winning_line, turbo):
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    screen.keypad(True)
    screen.timeout(75)
    curses.set_escdelay(30)

    cursor = (0, 0)
    mode = "place"
    live = selecting = closing = False
    queueing = False
    placements = []
    pickup = None
    pending = None
    link_filename = None
    thread = None
    status = "Ready"
    activity = deque(maxlen=60)
    requested_stop = Event()
    events = Queue()
    arrows = {
        curses.KEY_LEFT: (-1, 0), curses.KEY_RIGHT: (1, 0),
        curses.KEY_UP: (0, -1), curses.KEY_DOWN: (0, 1),
    }

    def run(plan):
        try:
            complete = play(plan, requested_stop, lambda msg: events.put(("log", msg)))
        except Exception as error:
            events.put(("done", f"Run failed: {error}"))
        else:
            events.put(("done", "Finished; torque holding" if complete else "Stopped"))

    def start(plan):
        nonlocal thread, status
        requested_stop.clear()
        status = f"Running {plan.cell}"
        thread = Thread(target=run, args=(plan,), name="arm-playback")
        thread.start()

    try:
        while True:
            while True:
                try:
                    kind, message = events.get_nowait()
                except Empty:
                    break
                activity.append(message)
                if kind == "done":
                    thread.join()
                    thread = None
                    status = message
            if closing and thread is None:
                return
            draw(
                screen, cursor, "queue" if queueing else mode, live, pickup,
                selecting, pending, status, activity, thread is not None, turbo, len(placements), link_filename,
            )
            key = screen.getch()
            if link_filename is not None:
                if key == 27:
                    link_filename = None
                    status = "Link cancelled"
                elif key in ENTER_KEYS:
                    try:
                        path = link_winning_line(tuple(placements), link_filename.strip())
                    except (OSError, ValueError, RuntimeError) as error:
                        status = "Could not link winning line"
                        activity.append(str(error))
                    else:
                        status = "Winning line linked and synced"
                        activity.append(f"Linked {path.name}")
                    link_filename = None
                elif key in BACKSPACE_KEYS:
                    link_filename = link_filename[:-1]
                elif 32 <= key <= 126:
                    link_filename += chr(key)
                continue
            if key in (ord("q"), ord("Q")):
                closing = True
                requested_stop.set()
                if thread is not None:
                    status = "Stopping; releasing torque"
                continue
            if thread is not None:
                if key in (27, ord(" ")):
                    requested_stop.set()
                    status = "Stopping; releasing torque"
                continue
            if pending is not None:
                if key in (ord("y"), ord("Y")):
                    start(pending)
                    pending = None
                elif key in (ord("n"), ord("N"), 27, ord(" ")):
                    pending = None
                    status = "Cancelled"
                continue
            if selecting:
                if ord("0") <= key <= ord("6"):
                    pickup = key - ord("0") or None
                    selecting = False
                elif key in (27, ord(" "), ord("g"), ord("G")):
                    selecting = False
                continue
            if key in arrows:
                dx, dy = arrows[key]
                cursor = (min(2, max(0, cursor[0] + dx)), min(2, max(0, cursor[1] + dy)))
                continue
            if key in (ord("g"), ord("G")):
                selecting = True
                continue
            if key == ord("0"):
                pickup = None
                continue
            if key in (ord("b"), ord("B")):
                queueing = not queueing
                status = "Queue ready" if queueing else "Board ready"
                continue
            if key in (ord("a"), ord("A")):
                if not queueing:
                    mode = "attack" if mode == "place" else "place"
                continue
            if key in (ord("l"), ord("L")):
                live = not live
                continue
            if queueing and key in BACKSPACE_KEYS:
                if placements:
                    status = f"Removed {placements.pop().cell}"
                continue
            if queueing and key in (ord("c"), ord("C")):
                placements.clear()
                status = "Queue cleared"
                continue
            if queueing and key in (ord("s"), ord("S")):
                try:
                    activity.extend(sync_winning_lines())
                except (OSError, ValueError, RuntimeError) as error:
                    status = "Could not sync winning lines"
                    activity.append(str(error))
                else:
                    status = "Winning line sync complete"
                continue
            if queueing and key in (ord("u"), ord("U")):
                link_filename = ""
                status = "Enter filename, blank uses matching name. Esc cancels."
                continue
            if queueing and key in (ord("w"), ord("W")):
                try:
                    path = export_winning_line(tuple(placements))
                except (OSError, ValueError, RuntimeError) as error:
                    status = "Could not export winning line"
                    activity.append(str(error))
                else:
                    status = "Winning line saved"
                    activity.append(f"Saved {path.name} in motions/")
                continue
            if queueing and key in (ord("r"), ord("R")):
                if not placements:
                    status = "Queue is empty"
                    continue
                try:
                    plan = compose_queue(tuple(placements))
                except (OSError, ValueError, RuntimeError) as error:
                    status = "Could not build queue"
                    activity.append(str(error))
                    continue
                activity.append(
                    f"Queue: {len(placements)} placements, {len(plan.waypoints)} waypoints"
                )
                if live:
                    placements.clear()
                    start(plan)
                else:
                    status = "Queue preview. Press L then R to run."
                continue
            if ord("1") <= key <= ord("9"):
                index = key - ord("1")
                cursor = (index % 3, index // 3)
            elif key not in ENTER_KEYS:
                continue

            try:
                plan = build_plan(cursor[0] + 1, cursor[1] + 1, "place" if queueing else mode, pickup)
            except (OSError, ValueError, RuntimeError) as error:
                status = "Could not build route"
                activity.append(str(error))
                continue
            description = f"{plan.cell}: {' > '.join(plan.phases)} ({len(plan.waypoints)} waypoints)"
            if queueing:
                placements.append(plan)
                activity.append(f"{len(placements)}. {description}")
                status = f"Queued {plan.cell}"
                continue
            activity.append(description)
            if plan.requires_held_block:
                activity.append("Center: hold a cylinder or cube, or choose its tray slot.")
            if not live:
                status = f"Preview {plan.cell}"
            elif mode == "attack":
                pending = plan
                status = f"Review {plan.cell} attack"
            else:
                start(plan)
    finally:
        requested_stop.set()
        if thread is not None:
            thread.join()


def run_terminal(build_plan, play, *, compose_queue, export_winning_line, sync_winning_lines, link_winning_line, turbo=False):
    try:
        curses.wrapper(
            terminal_loop, build_plan, play, compose_queue, export_winning_line,
            sync_winning_lines, link_winning_line, turbo,
        )
    except KeyboardInterrupt:
        pass
