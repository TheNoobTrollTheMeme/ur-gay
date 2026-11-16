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


def draw(screen, cursor, mode, live, pickup, selecting, pending, status, activity, busy, turbo):
    screen.erase()
    height, width = screen.getmaxyx()

    def line(y, text, attr=0, x=0):
        if 0 <= y < height and x < width - 1:
            screen.addnstr(y, x, text, width - x - 1, attr)

    line(0, f"ARM BOARD   {mode.upper()}   {'LIVE' if live else 'PREVIEW'}   {'TURBO' if turbo else 'NORMAL'}", curses.A_BOLD)
    placement_keys = "1-9 selects a square   Arrows + Enter also work"
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
    line(12, "G tray   0 clear   A attack/place   L live/preview")
    line(13, "Space/Esc stop   Q quit")
    if pending:
        help_text = "Confirm a held cylinder or cube. Y run / N cancel." if pending.requires_held_block else "Y run attack / N cancel."
        line(14, help_text, curses.A_BOLD)
    elif busy:
        line(14, "Playback active", curses.A_BOLD)

    line(15, status, curses.A_BOLD)
    if height > 17 and width > 1:
        lines = []
        for entry in activity:
            lines.extend(wrap(entry, width=width - 1, break_on_hyphens=False))
        for y, entry in enumerate(lines[-(height - 17):], 17):
            line(y, entry)
    screen.refresh()


def terminal_loop(screen, build_plan, play, turbo):
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
    pickup = None
    pending = None
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
            draw(screen, cursor, mode, live, pickup, selecting, pending, status, activity, thread is not None, turbo)
            key = screen.getch()
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
            if key in (ord("a"), ord("A")):
                mode = "attack" if mode == "place" else "place"
                continue
            if key in (ord("l"), ord("L")):
                live = not live
                continue
            if ord("1") <= key <= ord("9"):
                index = key - ord("1")
                cursor = (index % 3, index // 3)
            elif key not in ENTER_KEYS:
                continue

            try:
                plan = build_plan(cursor[0] + 1, cursor[1] + 1, mode, pickup)
            except (OSError, ValueError, RuntimeError) as error:
                status = "Could not build route"
                activity.append(str(error))
                continue
            activity.append(f"{plan.cell}: {' > '.join(plan.phases)} ({len(plan.waypoints)} waypoints)")
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


def run_terminal(build_plan, play, *, turbo=False):
    try:
        curses.wrapper(terminal_loop, build_plan, play, turbo)
    except KeyboardInterrupt:
        pass
