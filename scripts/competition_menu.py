"""COMP browser with winning lines, full catalog diagnostics and saved visibility.

All editing here is offline. A run request is returned to competition_quick
only after curses has restored the terminal; existing playback checks remain.
"""
from __future__ import annotations

import curses
from pathlib import Path
import subprocess
import sys
import textwrap

import competition_quick as quick
import competition_tui as base
from competition_menu_data import (
    VIEW_PATH, build_snapshot, display_name, is_winning_line,
    load_hidden, save_hidden, window_bounds,
)


class MenuState(base.CompetitionState):
    def __init__(self, snapshot, store, *, view_path=VIEW_PATH,
                 reference_path=base.reference.REFERENCE_PATH):
        super().__init__(snapshot.catalog, snapshot.all_labels, snapshot.aliases,
                         store, reference_path=reference_path)
        self.snapshot = snapshot
        self.view_path = Path(view_path)
        self.show_all = False
        preference_error = None
        try:
            self.hidden = load_hidden(self.view_path)
        except (OSError, ValueError, TypeError) as exc:
            self.hidden = set()
            preference_error = str(exc)
        self.status = (f"{len(self.labels)} entries, {len(self.catalog)} READY, "
                       f"{len(snapshot.unavailable)} blocked. H chooses what to show.")
        if preference_error:
            self.status = f"Preferences not loaded: {preference_error}. Showing all entries."

    @property
    def ordered(self):
        return sorted(self.labels, key=lambda key: (
            not is_winning_line(self.labels[key]), int(key)))

    @property
    def shown(self):
        query = self.filter_text.casefold()
        return [key for key in self.ordered
                if (self.show_all or self.labels[key].casefold() not in self.hidden)
                and (not self.winning_only or is_winning_line(self.labels[key]))
                and (query in self.labels[key].casefold()
                     or query in display_name(self.labels[key]).casefold())]

    @property
    def hidden_count(self):
        return sum(name.casefold() in self.hidden for name in self.labels.values())

    @property
    def selected_id(self):
        rows = self.shown
        return rows[min(self.select.motion, len(rows)-1)] if rows else None

    def add_selected(self):
        identifier = self.selected_id
        if identifier is None:
            raise ValueError("No visible entries. A shows all; H changes visibility.")
        if identifier not in self.catalog:
            name, reason = self.snapshot.unavailable[identifier]
            raise ValueError(f"{name} is blocked: {reason}")
        super().add_selected()

    def toggle_all(self):
        self.show_all = not self.show_all
        self.winning_only = False
        self.filter_text = ""
        self.select.pane = self.select.motion = 0
        self.status = ("ALL entries, including hidden and blocked. Saved choices unchanged."
                       if self.show_all else "MY MENU: saved visibility choices restored.")

    def toggle_winning_lines(self):
        self.winning_only = not self.winning_only
        # W deliberately reveals even hidden lines; it does not edit preferences.
        self.show_all = self.winning_only
        self.filter_text = ""
        self.select.pane = self.select.motion = 0
        self.status = ("WINNING LINES: all saved lines. Enter adds the whole READY line."
                       if self.winning_only else "MY MENU: winning lines first.")

    def save_visibility(self, hidden):
        save_hidden(hidden, self.view_path)
        self.hidden = {name.casefold() for name in hidden}
        self.show_all = self.winning_only = False
        self.filter_text = ""
        self.select.pane = self.select.motion = 0
        self.status = f"Menu saved: {self.hidden_count} hidden. Motion files and queue unchanged."

    def refresh_catalog(self, snapshot):
        selected_name = self.labels.get(self.selected_id)
        self.snapshot = snapshot
        self.catalog, self.labels, self.aliases = (
            snapshot.catalog, snapshot.all_labels, snapshot.aliases)
        visible = self.shown
        self.select.motion = next((i for i, key in enumerate(visible)
                                   if self.labels[key] == selected_name), 0)
        self.status = (f"Reloaded {len(self.labels)} entries: {len(self.catalog)} READY, "
                       f"{len(snapshot.unavailable)} blocked. Queue retained.")


class VisibilityDraft:
    """Checkbox selection, detached from playback and persistent until S saves."""
    def __init__(self, state):
        self.labels = dict(state.labels)
        self.ordered = list(state.ordered)
        self.hidden = set(state.hidden)
        self.original = set(state.hidden)
        self.index = 0
        self.query = ""
        self.winning_only = False

    @property
    def rows(self):
        query = self.query.casefold()
        return [key for key in self.ordered
                if (not self.winning_only or is_winning_line(self.labels[key]))
                and (query in self.labels[key].casefold()
                     or query in display_name(self.labels[key]).casefold())]

    @property
    def dirty(self):
        return self.hidden != self.original

    def toggle(self):
        rows = self.rows
        if not rows:
            return
        name = self.labels[rows[min(self.index, len(rows)-1)]].casefold()
        if name in self.hidden:
            self.hidden.remove(name)
        else:
            self.hidden.add(name)

    def set_all(self, visible):
        names = {self.labels[key].casefold() for key in self.rows}
        if visible:
            self.hidden.difference_update(names)
        else:
            self.hidden.update(names)

    def move(self, amount):
        self.index = max(0, min(max(0, len(self.rows)-1), self.index + amount))


put, bar = base.put, base.bar


def draw(screen, state):
    if state.tray_open:
        return base.draw_tray(screen, state)
    screen.erase()
    h, w = screen.getmaxyx()
    if h < 18 or w < 72:
        put(screen, 1, 1, f"COMP needs 72x18; terminal is {w}x{h}. Resize or Q to quit.")
        screen.refresh()
        return
    split, top, height = w // 2, 4, h-10
    put(screen, 0, 2, "COMP // MENU V2", curses.A_BOLD)
    label = f"ARM {state.speed}/{state.accel}  GRIP 300/20"
    put(screen, 0, max(24, w-len(label)-2), label, curses.A_BOLD)
    bar(screen, 1, 0, w, "=")
    put(screen, 2, 2, f"{len(state.labels)} entries | {len(state.catalog)} READY | "
        f"{len(state.snapshot.unavailable)} blocked | {state.hidden_count} hidden | No motion until R + Y")
    title = ("WINNING LINES" if state.winning_only else
             "ALL ENTRIES" if state.show_all else "MY MENU / WIN LINES FIRST")
    put(screen, 3, 2, title, curses.A_REVERSE if state.select.pane == 0 else curses.A_BOLD,
        width=split-4)
    put(screen, 3, split+2, "QUEUE / MACRO",
        curses.A_REVERSE if state.select.pane == 1 else curses.A_BOLD)
    for y in range(3, h-5):
        put(screen, y, split, "|")
    visible = state.shown
    first, end = window_bounds(state.select.motion, len(visible), height)
    for index in range(first, end):
        identifier = visible[index]
        name = state.labels[identifier]
        focused = state.select.pane == 0 and identifier == state.selected_id
        if identifier not in state.catalog:
            badge = "BLOCKED"
        else:
            badge = f"{len(state.catalog[identifier])} pts"
        hidden = " (hidden)" if name.casefold() in state.hidden else ""
        line = f"{'>' if focused else ' '} {identifier:>3} {display_name(name)} [{badge}]{hidden}"
        attr = curses.A_REVERSE | curses.A_BOLD if focused else (
            curses.A_DIM if identifier not in state.catalog else 0)
        put(screen, top+index-first, 2, line, attr, width=split-4)
    if not visible:
        put(screen, top, 2, "No matches. A = all; H = show/hide.", width=split-4)
    range_text = (f"{first+1}-{end}/{len(visible)}" if visible else "0/0")
    count = f"{range_text}  ^{first} above  v{len(visible)-end} below"
    put(screen, h-5, 2, count, curses.A_BOLD, width=split-4)
    if state.queue:
        start_q, end_q = window_bounds(state.select.queue, len(state.queue), height)
        for index in range(start_q, end_q):
            focused = state.select.pane == 1 and index == state.select.queue
            line = f"{'>' if focused else ' '} {index+1:>3} {display_name(state.queue[index])}"
            put(screen, top+index-start_q, split+2, line,
                curses.A_REVERSE if focused else 0, width=w-split-4)
    else:
        put(screen, top, split+2, "Enter adds the selected READY motion.", width=w-split-4)
    put(screen, h-5, split+2, f"{len(state.queue)} queued | {state.active_macro or 'unsaved'}",
        curses.A_BOLD, width=w-split-4)
    selected = state.selected_id
    name = state.labels[selected] if selected is not None else "(none)"
    put(screen, h-6, 2, "Selected: " + name, curses.A_DIM)
    bar(screen, h-4, 0, w)
    put(screen, h-3, 1, "Enter add | Tab pane | arrows/PgUp/PgDn | Del remove | R run")
    put(screen, h-2, 1, "B build | M map | H show/hide | W lines | F speed | T ref | ? help")
    put(screen, h-1, 1, state.status)
    screen.refresh()


def prompt(screen, state, label, *, value="", limit=72):
    text = str(value)
    while True:
        draw(screen, state)
        h, w = screen.getmaxyx()
        put(screen, h-1, 1, " " * max(0, w-2))
        put(screen, h-1, 1, f"{label}: {text}  [Enter OK / Esc cancel]")
        screen.refresh()
        key = screen.getch()
        if key in (27, 3):
            return None
        if key in (10, 13, curses.KEY_ENTER):
            return text.strip()
        if key in (8, 127, curses.KEY_BACKSPACE):
            text = text[:-1]
        elif 32 <= key <= 126 and len(text) < limit:
            text += chr(key)


def confirm(screen, state, message):
    while True:
        draw(screen, state)
        h, w = screen.getmaxyx()
        put(screen, h-1, 1, " " * max(0, w-2))
        put(screen, h-1, 1, message + " [Y yes / N cancel]")
        screen.refresh()
        key = screen.getch()
        if key in (ord("y"), ord("Y")):
            return True
        if key in (ord("n"), ord("N"), ord("q"), 27, 3):
            return False


def message_panel(screen, title, paragraphs):
    offset = 0
    while True:
        screen.erase()
        h, w = screen.getmaxyx()
        lines = []
        for paragraph in paragraphs:
            lines.extend(textwrap.wrap(str(paragraph), max(20, w-4)) or [""])
        height = max(1, h-5)
        offset = min(offset, max(0, len(lines)-height))
        put(screen, 0, 2, title, curses.A_BOLD)
        for index, line in enumerate(lines[offset:offset+height]):
            put(screen, 2+index, 2, line)
        put(screen, h-2, 1, "Up/Down or PgUp/PgDn scroll | Enter/Esc/Q back")
        screen.refresh()
        key = screen.getch()
        if key in (10, 13, 27, ord("q"), ord("Q")):
            return
        if key in (curses.KEY_DOWN, curses.KEY_NPAGE):
            offset += height if key == curses.KEY_NPAGE else 1
        elif key in (curses.KEY_UP, curses.KEY_PPAGE):
            offset = max(0, offset - (height if key == curses.KEY_PPAGE else 1))


def pick(screen, title, items):
    if not items:
        raise ValueError("No saved presets. S saves the current queue.")
    index = 0
    while True:
        screen.erase()
        h, w = screen.getmaxyx()
        height = max(1, h-5)
        start, end = window_bounds(index, len(items), height)
        put(screen, 0, 2, title, curses.A_BOLD)
        for pos in range(start, end):
            put(screen, 2+pos-start, 2, items[pos],
                curses.A_REVERSE if pos == index else 0)
        put(screen, h-2, 1, f"{start+1}-{end}/{len(items)} | arrows choose | Enter OK | Esc cancel")
        screen.refresh()
        key = screen.getch()
        if key in (27, ord("q")):
            return None
        if key in (10, 13, curses.KEY_ENTER):
            return index
        if key in (curses.KEY_UP, ord("k")):
            index = max(0, index-1)
        elif key in (curses.KEY_DOWN, ord("j")):
            index = min(len(items)-1, index+1)
        elif key == curses.KEY_HOME:
            index = 0
        elif key == curses.KEY_END:
            index = len(items)-1
        elif key == curses.KEY_NPAGE:
            index = min(len(items)-1, index+height)
        elif key == curses.KEY_PPAGE:
            index = max(0, index-height)


def choose_visibility(screen, state):
    draft = VisibilityDraft(state)
    while True:
        screen.erase()
        h, w = screen.getmaxyx()
        rows, height = draft.rows, max(1, h-7)
        first, end = window_bounds(draft.index, len(rows), height)
        put(screen, 0, 2, "SHOW / HIDE LEFT MENU", curses.A_BOLD)
        put(screen, 1, 2, "[x] show  [ ] hide. Visibility only; files, queue and checks stay unchanged.")
        for index in range(first, end):
            identifier = rows[index]
            name = draft.labels[identifier]
            mark = " " if name.casefold() in draft.hidden else "x"
            flag = " [BLOCKED]" if identifier not in state.catalog else ""
            put(screen, 3+index-first, 2,
                f"[{mark}] {identifier:>3} {display_name(name)}{flag}",
                curses.A_REVERSE if index == draft.index else 0)
        put(screen, h-4, 1, f"{first+1 if rows else 0}-{end}/{len(rows)} | "
            f"{'LINES' if draft.winning_only else 'ALL'} | search: {draft.query or '(none)'}")
        put(screen, h-3, 1, "Space/Enter toggle | A show matched | N hide matched | W lines | / search")
        put(screen, h-2, 1, "S save and return | Esc/Q cancel | PgUp/PgDn/Home/End scroll")
        put(screen, h-1, 1, "Blocked entries stay blocked, even when checked.")
        screen.refresh()
        key = screen.getch()
        if key in (27, ord("q"), ord("Q")):
            return False
        if key in (ord("s"), ord("S")):
            state.save_visibility(draft.hidden)
            return True
        if key in (32, 10, 13, curses.KEY_ENTER):
            draft.toggle()
        elif key in (ord("a"), ord("A")):
            draft.set_all(True)
        elif key in (ord("n"), ord("N")):
            draft.set_all(False)
        elif key in (ord("w"), ord("W")):
            draft.winning_only = not draft.winning_only
            draft.index = 0
        elif key == ord("/"):
            value = prompt(screen, state, "Menu search", value=draft.query)
            if value is not None:
                draft.query, draft.index = value, 0
        elif key == curses.KEY_UP:
            draft.move(-1)
        elif key == curses.KEY_DOWN:
            draft.move(1)
        elif key == curses.KEY_PPAGE:
            draft.move(-height)
        elif key == curses.KEY_NPAGE:
            draft.move(height)
        elif key == curses.KEY_HOME:
            draft.index = 0
        elif key == curses.KEY_END:
            draft.index = max(0, len(draft.rows)-1)


def tray_key(screen, state, key):
    """Reference-only editor. Never dispatches run or queue actions."""
    if key in (ord("t"), ord("T"), 27, ord("q"), ord("Q")):
        if not state.tray_dirty or confirm(screen, state, "Discard unsaved reference edits?"):
            if state.tray_dirty:
                state.discard_tray_reference()
            state.tray_open = False
    elif key in (curses.KEY_UP, ord("k")):
        state.select_tray_slot(max(1, state.select.tray-1))
    elif key in (curses.KEY_DOWN, ord("j")):
        state.select_tray_slot(min(6, state.select.tray+1))
    elif ord("1") <= key <= ord("6"):
        state.select_tray_slot(int(chr(key)))
    elif key in (ord("b"), ord("B")):
        state.cycle_tray_block()
    elif key in (ord("z"), ord("Z")):
        state.cycle_tray_zone()
    elif key in (ord("p"), ord("P"), ord("n"), ord("N")):
        field = "position" if chr(key).lower() == "p" else "note"
        value = prompt(screen, state, field.capitalize(), value=state.selected_slot[field],
                       limit=32 if field == "position" else 72)
        if value is not None:
            state.edit_tray(**{field: value})
    elif key in (ord("s"), ord("S")):
        state.save_tray_reference()
    elif key in (ord("d"), ord("D")):
        if not state.tray_dirty or confirm(screen, state, "Discard reference edits?"):
            state.discard_tray_reference()


def open_board_tool(screen, editor, state, tool):
    """Open the existing builder/remapper, then reload without losing the queue."""
    root = Path(state.snapshot.folder).resolve().parent
    script = root / "scripts" / "arm_board.py"
    if not script.is_file():
        raise ValueError("Old Board is missing. Run the one-click update to install it.")
    command = [sys.executable, str(script), "--repo", str(root)]
    if tool == "builder":
        command += ["--terminal", "--queue", "--speed", str(state.speed),
                    "--acceleration", str(state.accel)]
    elif tool == "mapping":
        command += ["--remap"]
    else:
        raise ValueError("Choose the winning-line builder or pickup mapping.")

    # Restore normal terminal input before another curses UI or input() runs.
    curses.def_prog_mode()
    try:
        curses.endwin()
        result = subprocess.run(command, cwd=root, check=False)
    finally:
        curses.reset_prog_mode()
        screen.keypad(True)
        screen.erase()
        screen.refresh()
        try:
            curses.curs_set(0)
        except curses.error:
            pass

    state.refresh_catalog(build_snapshot(editor))
    if tool == "builder":
        state.winning_only = state.show_all = True
        state.filter_text = ""
        state.select.pane = state.select.motion = 0
    label = "Winning-line builder" if tool == "builder" else "Pickup mapper"
    state.status = (f"{label} closed; recordings refreshed, COMP queue retained."
                    if result.returncode == 0 else
                    f"{label} exited with status {result.returncode}; COMP queue retained.")


def app(screen, editor, *, path=quick.PRESETS, start_tray=False,
        view_path=VIEW_PATH, reference_path=base.reference.REFERENCE_PATH):
    try:
        curses.curs_set(0)
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_CYAN, -1)
    except curses.error:
        pass
    screen.keypad(True)
    state = MenuState(build_snapshot(editor), quick.load_store(path),
                      view_path=view_path, reference_path=reference_path)
    state.tray_open = start_tray
    while True:
        draw(screen, state)
        key = screen.getch()
        h, w = screen.getmaxyx()
        if key == curses.KEY_RESIZE:
            continue
        if h < 18 or w < 72:
            if key in (ord("q"), ord("Q"), 27):
                return None
            continue
        try:
            if state.tray_open:
                tray_key(screen, state, key)
                continue
            if key in (ord("q"), ord("Q"), 27):
                return None
            if key in (9, curses.KEY_LEFT, curses.KEY_RIGHT):
                state.switch_pane()
            elif key in (curses.KEY_UP, ord("k")):
                state.move_cursor(-1)
            elif key in (curses.KEY_DOWN, ord("j")):
                state.move_cursor(1)
            elif key in (curses.KEY_PPAGE, curses.KEY_NPAGE):
                state.move_cursor((h-10) * (-1 if key == curses.KEY_PPAGE else 1))
            elif key in (curses.KEY_HOME, curses.KEY_END):
                state.move_cursor(-10**6 if key == curses.KEY_HOME else 10**6)
            elif key in (10, 13, curses.KEY_ENTER, 32) and state.select.pane == 0:
                state.add_selected()
            elif key in (curses.KEY_BACKSPACE, curses.KEY_DC, 8, 127, ord("d")) and state.select.pane == 1:
                state.remove_selected()
            elif key in (ord("["), ord("]")) and state.select.pane == 1:
                state.reorder(-1 if key == ord("[") else 1)
            elif key in (ord("h"), ord("H")):
                choose_visibility(screen, state)
            elif key in (ord("a"), ord("A")):
                state.toggle_all()
            elif key in (ord("w"), ord("W")):
                state.toggle_winning_lines()
            elif key in (ord("b"), ord("B")):
                open_board_tool(screen, editor, state, "builder")
            elif key in (ord("m"), ord("M")):
                open_board_tool(screen, editor, state, "mapping")
            elif key == ord("/"):
                value = prompt(screen, state, "Search", value=state.filter_text)
                if value is not None:
                    state.set_filter(value)
            elif key in (ord("f"), ord("F")):
                options = [(300, 20), (450, 25), (600, 35), (1200, 60)]
                choice = pick(screen, "ARM SPEED (gripper remains 300/20)",
                              ["Normal 300/20", "Medium 450/25", "Fast 600/35",
                               "Turbo 1200/60 (extra confirmation)", "Custom..."])
                if choice is not None:
                    if choice == 4:
                        raw = prompt(screen, state, "Speed acceleration", value=f"{state.speed} {state.accel}")
                        if raw is None:
                            continue
                        parts = raw.replace(",", " ").split()
                        if len(parts) != 2:
                            raise ValueError("Enter speed and acceleration, e.g. 500 25.")
                        settings = tuple(map(int, parts))
                    else:
                        settings = options[choice]
                    state.change_speed(*settings, path=path)
            elif key in (ord("s"), ord("S")):
                name = prompt(screen, state, "Macro name")
                if name:
                    name = name.casefold()
                    exists = name in state.store["presets"]
                    if not exists or confirm(screen, state, f"Overwrite {name}?"):
                        state.save_macro(name, path=path, replace=exists)
            elif key in (ord("o"), ord("O")):
                names = (["@last"] if state.store.get("last") else []) + sorted(state.store["presets"])
                choice = pick(screen, "OPEN SAVED PRESET", names)
                if choice is not None:
                    state.load_macro(names[choice])
            elif key in (ord("g"), ord("G")):
                state.refresh_catalog(build_snapshot(editor))
            elif key in (ord("v"), ord("V")):
                state.preview()
            elif key in (ord("t"), ord("T")):
                state.tray_open = True
                state.status = "REFERENCE ONLY: this screen cannot change motor commands."
            elif key in (ord("c"), ord("C")):
                if state.queue and confirm(screen, state, "Clear staged queue?"):
                    state.clear_queue()
            elif key in (ord("r"), ord("R")):
                state.preview()
                if confirm(screen, state, f"RUN {len(state.queue)} motions at {state.speed}/{state.accel}?"):
                    return state.export_for_run()
            elif key in (ord("i"), ord("I")):
                identifier = state.selected_id
                if identifier is not None:
                    name = state.labels[identifier]
                    reason = state.snapshot.unavailable.get(identifier, (name, "READY"))[1]
                    message_panel(screen, "SELECTED RECORDING", [name, reason,
                        f"Local motions folder: {state.snapshot.folder}",
                        "Visibility controls cannot override playback validation."])
            elif key == ord("?"):
                message_panel(screen, "COMP MENU V2 CONTROLS", [
                    "Winning lines appear first. Enter adds the entire READY saved line.",
                    "B: open the OLD board pseudo-GUI in queue/preview mode. G chooses a pickup; 1-9 adds its destination; W exports a new line. Q returns to COMP.",
                    "M: open actual pickup mapping. 2 maps slot to grab sequence; 4 changes a saved line's pickups; 6 syncs existing lines. Q returns to COMP.",
                    "After changing saved waypoint positions, use S in the old builder (queue mode), or mapper 6 then SYNC, to rebuild linked winning lines.",
                    "T is a separate reference-only tray: all its fields are cosmetic. In actual mapping, zone/position are labels; block type and grab sequence affect plans.",
                    "H: checkbox show/hide menu. Space toggles, A shows matched, N hides matched, S saves.",
                    "A: temporarily show ALL entries; press again to return to your saved menu.",
                    "W: all winning lines, including hidden ones. / searches names.",
                    "Up/Down, PgUp/PgDn, Home/End scroll. The counter shows entries above and below.",
                    "I: full filename, blocked reason and local folder. G refreshes local recordings.",
                    "Tab switches panes. Enter adds. Delete removes. [ and ] reorder queued actions.",
                    "S saves a macro, O opens one, F sets speed, T opens reference-only trays.",
                    "V previews the queue. C clears it. R then Y runs. Q exits.",
                    "Showing/hiding is not enabling/disabling motion. Blocked entries cannot run.",
                    "During physical playback: Space/Q/Esc stops; support the arm when torque releases."])
        except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
            state.status = "Error: " + str(exc)


def launch(editor, *, path=quick.PRESETS, start_tray=False,
           view_path=VIEW_PATH, reference_path=base.reference.REFERENCE_PATH):
    return curses.wrapper(lambda screen: app(
        screen, editor, path=path, start_tray=start_tray,
        view_path=view_path, reference_path=reference_path))
