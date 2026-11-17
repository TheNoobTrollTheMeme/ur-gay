"""SSH-friendly two-pane terminal pseudo-GUI for Quick Competition Mode.

Pure queue/model operations are separate from curses rendering so unit tests
need no display or robot. The curses wrapper ALWAYS closes before playback
takes over the terminal for its hardware-stop listener.
"""
from __future__ import annotations

import curses
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
import re

import competition_quick as quick
import comp_tray_reference as reference


@dataclass
class Selection:
    pane: int = 0  # 0 = available motions, 1 = staged queue
    motion: int = 0
    queue: int = 0
    tray: int = 1


class CompetitionState:
    def __init__(self, catalog, labels, aliases, store, *,
                 reference_path=reference.REFERENCE_PATH):
        self.catalog, self.labels, self.aliases = catalog, labels, aliases
        self.store = store
        self.reference_path = Path(reference_path)
        self.tray = reference.load_reference(self.reference_path)
        self.tray_saved = deepcopy(self.tray)
        self.tray_open = False
        self.queue = []
        self.speed = store.get("speed", 300)
        self.accel = store.get("accel", 20)
        quick.validate_speed(self.speed, self.accel)
        self.select = Selection()
        self.filter_text = ""
        self.status = "Ready. Choose a recording and press Enter to queue."
        self.active_macro = None

    @property
    def tray_dirty(self):
        return self.tray != self.tray_saved

    @property
    def selected_slot(self):
        return reference.slot_at(self.tray, self.select.tray)

    def select_tray_slot(self, number):
        if number not in range(1, 7):
            raise ValueError("Choose physical slot 1..6.")
        self.select.tray = number

    def edit_tray(self, **fields):
        """Staged reference labels ONLY: no modification to pickup configuration."""
        self.tray = reference.edit_reference(self.tray, self.select.tray, **fields)
        self.status = f"REFERENCE ONLY: slot {self.select.tray} edited, not saved."

    def cycle_tray_block(self):
        choices = reference.BLOCKS
        current = self.selected_slot["block"]
        self.edit_tray(block=choices[(choices.index(current) + 1) % len(choices)])

    def cycle_tray_zone(self):
        choices = reference.ZONES
        current = self.selected_slot["zone"]
        self.edit_tray(zone=choices[(choices.index(current) + 1) % len(choices)])

    def save_tray_reference(self):
        path, backup = reference.save_reference(self.tray, self.reference_path)
        self.tray_saved = deepcopy(self.tray)
        self.status = "Saved reference labels to " + path.name + (
            f"; backup {backup.name}" if backup else ""
        ) + ". Motion scripts unchanged."

    def discard_tray_reference(self):
        self.tray = reference.load_reference(self.reference_path)
        self.tray_saved = deepcopy(self.tray)
        self.status = "Reference edits discarded. Physical tray configuration unchanged."

    @property
    def shown(self):
        return [n for n in sorted(self.catalog, key=int)
                if self.filter_text.lower() in self.labels[n].lower()]

    def _validated(self, items):
        _, names = quick.resolve_queue(items, self.catalog, self.labels, self.aliases)
        return list(names)

    def add_selected(self):
        visible = self.shown
        if not visible:
            raise ValueError("No matching READY recordings.")
        self.select.motion = min(self.select.motion, len(visible)-1)
        identifier = visible[self.select.motion]
        self.queue = self._validated([*self.queue, identifier])
        self.select.queue = len(self.queue)-1
        self.status = f"Queued {self.labels[identifier]}. {len(self.queue)} actions."

    def remove_selected(self):
        if not self.queue:
            raise ValueError("Queue is empty.")
        i = min(self.select.queue, len(self.queue)-1)
        removed = self.queue.pop(i)
        self.select.queue = max(0, min(i, len(self.queue)-1))
        self.status = f"Removed {removed}."

    def clear_queue(self):
        self.queue = []
        self.select.queue = 0
        self.active_macro = None
        self.status = "Cleared staged queue."

    def reorder(self, step):
        if not self.queue:
            raise ValueError("Queue is empty.")
        i = min(self.select.queue, len(self.queue)-1)
        j = i + step
        if not 0 <= j < len(self.queue):
            return
        self.queue[i], self.queue[j] = self.queue[j], self.queue[i]
        self.select.queue = j
        self.status = "Moved " + self.queue[j] + (" up." if step < 0 else " down.")

    def change_speed(self, speed, accel, *, path=None):
        speed, accel = quick.validate_speed(speed, accel)
        if path is not None:
            # Persist just the preference; no waypoint files are altered.
            new_store = dict(self.store, speed=speed, accel=accel)
            quick.atomic_store(new_store, path)
            self.store = new_store
        self.speed, self.accel = speed, accel
        self.status = f"Arm speed {self.speed}/{self.accel}; gripper remains 300/20."

    def preview(self):
        plan, _ = quick.resolve_queue(self.queue, self.catalog, self.labels, self.aliases)
        steps = sum(len(plan.catalog[ident]) for ident in plan.pending)
        self.status = (f"{len(self.queue)} motions, {steps} saved waypoints. "
                       "No collision clearance is implied.")
        return steps

    def load_macro(self, name):
        if name == "@last":
            macro = self.store.get("last")
        else:
            macro = self.store["presets"].get(name)
        if macro is None:
            raise ValueError(f"No saved macro: {name}")
        macro = quick.validate_macro(macro)
        # Verify the complete macro before changing the working queue.
        new_queue = self._validated(macro["queue"])
        self.queue = new_queue
        self.speed, self.accel = quick.validate_speed(macro["speed"], macro["accel"])
        self.select.queue = 0
        self.active_macro = name
        self.status = f"Loaded {name}: {len(self.queue)} motions."

    def save_macro(self, name, *, path=quick.PRESETS, replace=False):
        validated = self._validated(self.queue)
        self.store = quick.save_macro(self.store, name, validated, self.speed,
                                      self.accel, path=path, replace=replace)
        self.active_macro = name
        self.status = f"Saved macro {name} ({len(validated)} motions)."

    def export_for_run(self):
        names = self._validated(self.queue)
        quick.validate_speed(self.speed, self.accel)
        return dict(queue=names, speed=self.speed, accel=self.accel)

    def move_cursor(self, step):
        if self.select.pane == 0:
            self.select.motion = max(0, min(max(0, len(self.shown)-1),
                                             self.select.motion + step))
        else:
            self.select.queue = max(0, min(max(0, len(self.queue)-1),
                                            self.select.queue + step))

    def switch_pane(self):
        self.select.pane = 1 - self.select.pane

    def set_filter(self, text):
        self.filter_text = text.strip()
        self.select.motion = 0
        self.status = (f"Filter: {self.filter_text}" if self.filter_text
                       else "Showing all READY motions.")


def put(screen, y, x, text, attr=0, width=None):
    h, w = screen.getmaxyx()
    if y < 0 or y >= h or x < 0 or x >= w:
        return
    room = max(0, w-x-1)
    if width is not None:
        room = min(room, max(0, width))
    if room:
        try:
            screen.addnstr(y, x, str(text), room, attr)
        except curses.error:
            pass


def bar(screen, y, x, width, char="-"):
    if width > 1:
        put(screen, y, x, char * (width-1))


def draw(screen, state):
    screen.erase()
    h, w = screen.getmaxyx()
    if h < 18 or w < 72:
        put(screen, 1, 2, f"Terminal too small ({w}x{h}). Need at least 72x18.")
        put(screen, 3, 2, "Resize terminal or press Q to quit.")
        screen.refresh()
        return

    try:
        title_attr = curses.color_pair(1) | curses.A_BOLD
        mark_attr = curses.color_pair(2) | curses.A_BOLD
        warn_attr = curses.color_pair(3) | curses.A_BOLD
    except curses.error:
        title_attr, mark_attr, warn_attr = curses.A_BOLD, curses.A_REVERSE, curses.A_BOLD

    put(screen, 0, 2, "COMP // LIVE MACRO BOARD", title_attr)
    label = f"ARM {state.speed}/{state.accel}  GRIP 300/20"
    put(screen, 0, max(24, w-len(label)-2), label, curses.A_BOLD)
    bar(screen, 1, 0, w, "=")
    put(screen, 2, 2, "No motor action while editing. Only LIVE, READY recordings are selectable.")
    split = w // 2
    pane_top = 4
    pane_bottom = h-6
    rows = max(1, pane_bottom-pane_top)
    for y in range(3, h-5):
        put(screen, y, split, "|")

    left_header = "[ MOTIONS ]" if state.select.pane == 0 else "  MOTIONS  "
    right_header = "[ QUEUE / MACRO ]" if state.select.pane == 1 else "  QUEUE / MACRO  "
    put(screen, 3, 2, left_header, title_attr if state.select.pane == 0 else curses.A_BOLD)
    put(screen, 3, split+2, right_header, title_attr if state.select.pane == 1 else curses.A_BOLD)

    visible = state.shown
    selected = min(state.select.motion, max(0, len(visible)-1))
    start = min(max(0, selected-rows+1), max(0, len(visible)-rows))
    for i, identifier in enumerate(visible[start:start+rows], start=start):
        focused = state.select.pane == 0 and i == selected
        prefix = ">" if focused else " "
        name = state.labels[identifier]
        line = f"{prefix} {identifier:>3}  {name:<21} [{len(state.catalog[identifier])} pts]"
        put(screen, pane_top+(i-start), 2, line,
            curses.A_REVERSE | curses.A_BOLD if focused else 0,
            width=split-4)
    if not visible:
        put(screen, pane_top, 2, "(no matching READY motions)")
    if state.filter_text:
        put(screen, h-5, 2, "Filter: " + state.filter_text, curses.A_DIM, width=split-4)

    if not state.queue:
        put(screen, pane_top, split+2, "(empty) Enter on a motion to add it.")
    else:
        selected_q = min(state.select.queue, len(state.queue)-1)
        start_q = min(max(0, selected_q-rows+1), max(0, len(state.queue)-rows))
        for i, name in enumerate(state.queue[start_q:start_q+rows], start=start_q):
            focused = state.select.pane == 1 and i == selected_q
            line = f"{'>' if focused else ' '} {i+1:>3}  {name}"
            put(screen, pane_top+i-start_q, split+2, line,
                curses.A_REVERSE | curses.A_BOLD if focused else 0,
                width=w-split-4)

    put(screen, h-5, split+2,
        f"{len(state.queue)} queued | preset: {state.active_macro or '(unsaved)'}",
        curses.A_BOLD, width=w-split-4)
    bar(screen, h-4, 0, w, "-")
    put(screen, h-3, 1, "TAB pane | arrows select | ENTER add | DEL remove | [/] reorder | / search")
    put(screen, h-2, 1, "S save | O open | F speed | T tray ref | R RUN | V preview | ? help | Q quit")
    put(screen, h-1, 1, state.status,
        warn_attr if ("NOT" in state.status or "Error" in state.status
                      or "invalid" in state.status) else mark_attr)
    screen.refresh()


def draw_tray(screen, state):
    """Three-tray sketch, solely for the operator's visual reference."""
    screen.erase()
    h, w = screen.getmaxyx()
    if h < 18 or w < 72:
        put(screen, 1, 2, f"Terminal too small ({w}x{h}); need 72x18.")
        put(screen, 3, 2, "Resize or press T / Esc to return.")
        screen.refresh()
        return
    try:
        heading = curses.color_pair(1) | curses.A_BOLD
        focused = curses.A_REVERSE | curses.A_BOLD
    except curses.error:
        heading = curses.A_BOLD
        focused = curses.A_REVERSE
    put(screen, 0, 2, "COMP // TRAY REFERENCE (NO MOTOR CONTROL)", heading)
    put(screen, 1, 2, "Visual notes only. Does not modify tray_mapping.json, grab motions or macros.")
    bar(screen, 2, 0, w, "=")
    width = (w - 4) // 3
    for col, zone in enumerate(reference.ZONES):
        x = 2 + col * width
        put(screen, 4, x, f"[ {zone} ]", heading, width=width-2)
        for n, slot in enumerate(sorted(
                (slot for slot in state.tray["slots"] if slot["zone"] == zone),
                key=lambda slot: slot["slot"])):
            # One line per slot leaves enough room for all six to be in one
            # reference zone, even on an 18-row SSH terminal.
            y = 6 + n
            selected = slot["slot"] == state.select.tray
            line = ((">" if selected else " ") + f" {slot['slot']} "
                    + f"{slot['block']:<9} {slot['position']}")
            put(screen, y, x, line, focused if selected else 0,
                width=width-2)
    note = state.selected_slot["note"] or "(no note)"
    put(screen, h-6, 2, f"Selected #{state.select.tray} note: {note}",
        curses.A_DIM)
    put(screen, h-5, 2, "REFERENCE FILE: comp_tray_reference.json "
        + ("[UNSAVED EDITS]" if state.tray_dirty else "[SAVED/DEFAULT]"),
        heading)
    bar(screen, h-4, 0, w)
    put(screen, h-3, 1, "1..6 select | Up/Down navigate | B block | Z tray zone | P position | N note")
    put(screen, h-2, 1, "S save ref | D discard changes | T/Esc back to COMP | ? help")
    put(screen, h-1, 1, state.status)
    screen.refresh()


def draw_active(screen, state):
    if state.tray_open:
        draw_tray(screen, state)
    else:
        draw(screen, state)


def prompt(screen, state, label, *, max_length=60, value=""):
    """Capture one short string within curses. ESC cancels without side effects."""
    h, w = screen.getmaxyx()
    text = value
    try:
        curses.curs_set(1)
    except curses.error:
        pass
    try:
        while True:
            draw_active(screen, state)
            put(screen, h-1, 1, " " * (w-2))
            put(screen, h-1, 1, f"{label}: {text}  [Enter=OK Esc=cancel]")
            screen.move(h-1, min(w-2, len(label)+len(text)+3))
            screen.refresh()
            ch = screen.getch()
            if ch in (10, 13, curses.KEY_ENTER):
                return text.strip()
            if ch in (27,):
                return None
            if ch in (curses.KEY_BACKSPACE, 8, 127):
                text = text[:-1]
            elif 32 <= ch <= 126 and len(text) < max_length:
                text += chr(ch)
    finally:
        try:
            curses.curs_set(0)
        except curses.error:
            pass


def confirm(screen, state, message):
    """No action until explicit Y while in the confirmation panel."""
    while True:
        draw_active(screen, state)
        h, w = screen.getmaxyx()
        put(screen, h-1, 1, " " * (w-2))
        put(screen, h-1, 1, message + "   [Y=yes / N=cancel]")
        screen.refresh()
        key = screen.getch()
        if key in (ord("y"), ord("Y")):
            return True
        if key in (ord("n"), ord("N"), ord("q"), ord("Q"), 27):
            return False


def choose_preset(screen, state):
    options = (["@last"] if state.store.get("last") else []) + sorted(
        state.store["presets"])
    if not options:
        raise ValueError("No saved macros. Press S to save the current queue.")
    chosen = 0
    while True:
        draw(screen, state)
        h, w = screen.getmaxyx()
        center = max(2, (w-48)//2)
        row = max(4, h//3)
        put(screen, row, center, "LOAD PRESET (Up/Down, Enter, Esc)", curses.A_BOLD)
        for i, item in enumerate(options[max(0, chosen-4):chosen+5]):
            real = max(0, chosen-4)+i
            if row+i+2 >= h-4:
                break
            put(screen, row+i+2, center+2, ("> " if real == chosen else "  ")+item,
                curses.A_REVERSE if real == chosen else 0)
        screen.refresh()
        key = screen.getch()
        if key in (curses.KEY_UP, ord("k")):
            chosen = max(0, chosen-1)
        elif key in (curses.KEY_DOWN, ord("j")):
            chosen = min(len(options)-1, chosen+1)
        elif key in (10, 13, curses.KEY_ENTER):
            return options[chosen]
        elif key in (27, ord("q")):
            return None


def choose_speed(screen, state):
    choices = [
        ("Normal", 300, 20), ("Medium", 450, 25),
        ("Fast", 600, 35), ("Turbo*", 1200, 60),
        ("Custom...", None, None),
    ]
    picked = 0
    while True:
        draw(screen, state)
        h, w = screen.getmaxyx()
        row = max(4, h//3)
        x = max(2, (w-44)//2)
        put(screen, row, x, "ARM SPEED: arrows + Enter; Esc cancels", curses.A_BOLD)
        for i, (label, speed, accel) in enumerate(choices):
            val = f"{speed}/{accel}" if speed else "enter speed + acceleration"
            put(screen, row+2+i, x+2, ("> " if picked==i else "  ")+f"{label:<12} {val}",
                curses.A_REVERSE if picked == i else 0)
        screen.refresh()
        k = screen.getch()
        if k in (curses.KEY_UP, ord("k")):
            picked = max(0, picked-1)
        elif k in (curses.KEY_DOWN, ord("j")):
            picked = min(len(choices)-1, picked+1)
        elif k in (27, ord("q")):
            return None
        elif k in (10, 13, curses.KEY_ENTER):
            _, speed, accel = choices[picked]
            if speed is not None:
                return speed, accel
            raw = prompt(screen, state, "Custom speed accel (e.g. 500 25)")
            if raw is None:
                return None
            parts = raw.replace(",", " ").split()
            if len(parts) != 2:
                raise ValueError("Enter both numbers, e.g. 500 25.")
            return tuple(map(int, parts))


def help_overlay(screen, state):
    lines = (
        "COMP keyboard controls",
        "Arrow Up/Down: select recordings or queued actions",
        "TAB or Left/Right: switch between the two panes",
        "ENTER / SPACE (left): add highlighted motion",
        "DEL / BACKSPACE (right): remove highlighted queue item",
        "[ / ] (right): move a queued action earlier/later",
        "S: name and SAVE reusable macro  |  O: OPEN macro",
        "F: set ARM speed once (gripper stays at 300/20)",
        "/: filter by motion name  |  V: preview waypoint count",
        "G: refresh local motion catalog  |  C: clear queue",
        "T: visual six-slot tray reference (NEVER affects motor commands)",
        "Tray screen: 1..6 select slot | B block | Z zone | P position | N note",
        "Tray screen: S saves independent reference | D discards | T returns",
        "R: confirm and RUN queue  |  Q / Esc: quit",
        "No mouse/X display required. No live edits while moving.",
        "Space, Q and Esc STOP motor playback after GUI closes.",
        "NOTE: speed controls do not verify path clearance.",
        "Press any key to return",
    )
    while True:
        draw(screen, state)
        h, w = screen.getmaxyx()
        start = max(1, (h-len(lines))//2)
        for i, line in enumerate(lines):
            if start+i < h:
                put(screen, start+i, 2, " " * (w-4))
                put(screen, start+i, 3, line,
                    curses.A_BOLD if i in (0, len(lines)-1) else 0)
        screen.refresh()
        screen.getch()
        return


def app(screen, editor, *, path=quick.PRESETS,
        start_tray=False, reference_path=reference.REFERENCE_PATH):
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    screen.keypad(True)
    try:
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_CYAN, -1)
        curses.init_pair(2, curses.COLOR_GREEN, -1)
        curses.init_pair(3, curses.COLOR_YELLOW, -1)
    except curses.error:
        pass

    # Discovery is allowed to print disabled filenames; capture them in a status.
    disabled = []
    catalog, labels, aliases = quick.catalog_for(editor, report=disabled.append)
    state = CompetitionState(catalog, labels, aliases, quick.load_store(path),
                             reference_path=reference_path)
    state.tray_open = bool(start_tray)
    if disabled:
        state.status = f"{len(disabled)} unavailable/invalid recordings skipped."
    while True:
        draw_active(screen, state)
        key = screen.getch()
        if key == curses.KEY_RESIZE:
            continue
        try:
            if state.tray_open:
                if key in (ord("t"), ord("T"), 27, ord("q"), ord("Q")):
                    if state.tray_dirty and not confirm(
                            screen, state, "Discard UNSAVED tray reference edits?"):
                        state.status = "Save with S or continue editing."
                    else:
                        if state.tray_dirty:
                            state.discard_tray_reference()
                        state.tray_open = False
                elif key in (curses.KEY_UP, ord("k")):
                    state.select_tray_slot(max(1, state.select.tray-1))
                elif key in (curses.KEY_DOWN, ord("j")):
                    state.select_tray_slot(min(6, state.select.tray+1))
                elif key in tuple(ord(str(n)) for n in range(1, 7)):
                    state.select_tray_slot(int(chr(key)))
                elif key in (ord("b"), ord("B")):
                    state.cycle_tray_block()
                elif key in (ord("z"), ord("Z")):
                    state.cycle_tray_zone()
                elif key in (ord("p"), ord("P")):
                    value = prompt(screen, state, "Position label (1..32)",
                                   max_length=32, value=state.selected_slot["position"])
                    if value is not None:
                        state.edit_tray(position=value)
                elif key in (ord("n"), ord("N")):
                    value = prompt(screen, state, "Note (0..72)",
                                   max_length=72, value=state.selected_slot["note"])
                    if value is not None:
                        state.edit_tray(note=value)
                elif key in (ord("s"), ord("S")):
                    state.save_tray_reference()
                elif key in (ord("d"), ord("D")):
                    if not state.tray_dirty or confirm(
                            screen, state, "Discard unsaved reference edits?"):
                        state.discard_tray_reference()
                elif key == ord("?"):
                    help_overlay(screen, state)
                # All tray events handled here; none can queue/play motors.
                continue
            if key in (ord("q"), ord("Q"), 27):
                return None
            if key in (9, curses.KEY_LEFT, curses.KEY_RIGHT):
                state.switch_pane()
            elif key in (curses.KEY_UP, ord("k")):
                state.move_cursor(-1)
            elif key in (curses.KEY_DOWN, ord("j")):
                state.move_cursor(1)
            elif key == curses.KEY_PPAGE:
                state.move_cursor(-10)
            elif key == curses.KEY_NPAGE:
                state.move_cursor(10)
            elif key in (10, 13, curses.KEY_ENTER, ord(" ")) and state.select.pane == 0:
                state.add_selected()
            elif key in (curses.KEY_BACKSPACE, curses.KEY_DC, 8, 127, ord("d")) and state.select.pane == 1:
                state.remove_selected()
            elif key in (ord("["), ord("{")) and state.select.pane == 1:
                state.reorder(-1)
            elif key in (ord("]"), ord("}")) and state.select.pane == 1:
                state.reorder(1)
            elif key in (ord("/"),):
                value = prompt(screen, state, "Search", value=state.filter_text)
                if value is not None:
                    state.set_filter(value)
            elif key in (ord("f"), ord("F")):
                settings = choose_speed(screen, state)
                if settings is not None:
                    state.change_speed(*settings, path=path)
            elif key in (ord("t"), ord("T")):
                state.tray_open = True
                state.status = ("REFERENCE ONLY: edit physical slot labels. "
                                "No influence on playback or pickup mapping.")
            elif key in (ord("s"), ord("S")):
                name = prompt(screen, state, "Macro name")
                if name:
                    existing = name in state.store["presets"]
                    if existing and not confirm(screen, state, f"Overwrite '{name}'?"):
                        state.status = "Save cancelled."
                    else:
                        state.save_macro(name, path=path, replace=existing)
            elif key in (ord("o"), ord("O")):
                preset = choose_preset(screen, state)
                if preset:
                    state.load_macro(preset)
            elif key in (ord("v"), ord("V")):
                state.preview()
            elif key in (ord("g"), ord("G")):
                messages = []
                catalog, labels, aliases = quick.catalog_for(editor, report=messages.append)
                state.catalog, state.labels, state.aliases = catalog, labels, aliases
                # A renamed or deleted saved motion does not delete queued entries.
                state.status = (f"Reloaded {len(catalog)} READY entries. "
                                f"{len(messages)} invalid entries excluded.")
            elif key in (ord("c"), ord("C")):
                if state.queue and confirm(screen, state, "Clear staged queue?"):
                    state.clear_queue()
            elif key in (ord("r"), ord("R")):
                state.preview()  # Reject missing/stale names before confirmation.
                if confirm(screen, state, f"RUN {len(state.queue)} motions at {state.speed}/{state.accel}?"):
                    # Hardware playback starts ONLY after curses restores the terminal.
                    return state.export_for_run()
            elif key == ord("?"):
                help_overlay(screen, state)
        except (ValueError, KeyError, TypeError, OSError) as exc:
            state.status = "Error: " + str(exc)


def launch(editor, *, path=quick.PRESETS, start_tray=False,
           reference_path=reference.REFERENCE_PATH):
    """Render curses editor; return selected immutable macro (or None)."""
    return curses.wrapper(lambda screen: app(
        screen, editor, path=path, start_tray=start_tray,
        reference_path=reference_path))
