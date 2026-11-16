"""Terminal editor for tray positions and winning-line recipes.

OFFLINE ONLY: no servo connection, motor commands, or movement.
It rebuilds *exported* winning lines from existing recorded motions.
"""
from pathlib import Path

from tray_mapping import (BLOCKS, ZONES, get_slot, load_mapping, save_mapping)


def _lines(folder, board):
    return sorted(
        path for path in Path(folder).glob("winning_line_*.json")
        if board.WINNING_LINE_FILE.fullmatch(path.name)
    )


def _show_slots(mapping, print_fn=print):
    print_fn("\nSLOT  TRAY       POSITION       BLOCK       GRAB ROUTE")
    for entry in sorted(mapping["slots"], key=lambda item: item["slot"]):
        print_fn(f'{entry["slot"]:>4}  {entry["zone"]:<10} '
                 f'{entry["position"]:<14} {entry["block"]:<11} '
                 f'{entry["grab"] or "(empty)"}')
    print_fn("Physical coordinates come from the selected grab recording, NOT this table.")


def _choose_line(folder, board, input_fn=input, print_fn=print):
    lines = _lines(folder, board)
    if not lines:
        print_fn("No exported winning_line_*.json files found.")
        return None
    print_fn("\nSaved winning lines:")
    for i, path in enumerate(lines, 1):
        try:
            _cells, _raw, data = board.read_winning_line(path)
            recipe = data.get("source_placements")
            picks = (", ".join(f'{r.get("cell")}:slot{r.get("pickup")}'
                               for r in recipe)
                     if isinstance(recipe, list) else "not linked")
        except (OSError, ValueError, TypeError):
            picks = "could not parse"
        print_fn(f"  {i}. {path.name} [{picks}]")
    answer = input_fn("Line number (blank cancels): ").strip()
    if not answer:
        return None
    if not answer.isdigit() or not 1 <= int(answer) <= len(lines):
        raise ValueError("Enter a listed line number.")
    return lines[int(answer) - 1]


def _edit_slot(folder, input_fn=input, print_fn=print):
    mapping = load_mapping(folder)
    _show_slots(mapping, print_fn)
    chosen = input_fn("Slot to edit 1..6 (blank cancels): ").strip()
    if not chosen:
        return
    if not chosen.isdigit():
        raise ValueError("Slot must be a number 1..6.")
    old = get_slot(mapping, int(chosen))
    entry = dict(old)
    print_fn("Press Enter to retain each value. No motor movement.")
    zone = input_fn(f'Tray zone {ZONES} [{entry["zone"]}]: ').strip()
    if zone:
        if zone not in ZONES:
            raise ValueError("Invalid zone. Use B-left, A-front, or B-right.")
        entry["zone"] = zone
    position = input_fn(f'Location label [{entry["position"]}]: ').strip()
    if position:
        entry["position"] = position
    block = input_fn(f'Block type {BLOCKS} [{entry["block"]}]: ').strip().lower()
    if block:
        if block not in BLOCKS:
            raise ValueError("Block must be cube, cylinder, trophy, or empty.")
        entry["block"] = block
    if entry["block"] == "empty":
        entry["grab"] = None
    else:
        grab = input_fn(f'Grab recording name [{entry["grab"] or "grab"+chosen}]: ').strip()
        if grab:
            entry["grab"] = grab.removesuffix(".json")
        elif entry["grab"] is None:
            entry["grab"] = f"grab{chosen}"
    mapping["slots"] = [entry if slot["slot"] == entry["slot"] else slot
                        for slot in mapping["slots"]]
    from tray_mapping import validate_mapping
    validate_mapping(mapping)
    print_fn(f'Proposed slot {chosen}: {entry["zone"]}, {entry["position"]}, '
             f'{entry["block"]} -> {entry["grab"]}')
    if entry["grab"] and not (Path(folder) / (entry["grab"] + ".json")).is_file():
        print_fn("NOTE: Grab motion not recorded yet; this slot cannot build a macro.")
    if input_fn("Type SAVE to write tray mapping: ").strip() != "SAVE":
        print_fn("Cancelled. No changes saved.")
        return
    path, backup = save_mapping(folder, mapping)
    print_fn(f"Saved {path}")
    if backup:
        print_fn(f"Backup: {backup.name}")


def _recipes_for_line(board, path):
    cells, original, data = board.read_winning_line(path)
    stored = data.get("source_placements")
    if not isinstance(stored, list) or len(stored) != len(cells):
        stored = [{"cell": cell, "pickup": None} for cell in cells]
    return cells, original, data, stored


def _edit_line(folder, script6, board, input_fn=input, print_fn=print):
    path = _choose_line(folder, board, input_fn, print_fn)
    if path is None:
        return
    cells, original, data, stored = _recipes_for_line(board, path)
    mapping = load_mapping(folder)
    _show_slots(mapping, print_fn)
    print_fn("\nPick a slot for each square, or '-' for no pickup. "
             "Enter keeps the existing assignment.")
    recipe = []
    for idx, cell in enumerate(cells):
        prior = stored[idx]["pickup"] if isinstance(stored[idx], dict) else None
        raw = input_fn(f"{cell} pickup slot ["
                       f"{prior if prior is not None else '-'}]: ").strip()
        if not raw:
            pickup = prior
        elif raw == "-":
            pickup = None
        elif raw.isdigit() and 1 <= int(raw) <= 6:
            pickup = int(raw)
        else:
            raise ValueError("Pick 1..6, '-', or Enter.")
        if pickup is not None and get_slot(mapping, pickup)["block"] == "empty":
            raise ValueError(f"Slot {pickup} is empty.")
        recipe.append({"cell": cell, "pickup": pickup})
    choices = [r["pickup"] for r in recipe if r["pickup"] is not None]
    if len(set(choices)) < len(choices):
        print_fn("WARNING: a slot is used more than once; a block cannot be picked twice.")
    if any(r["pickup"] is None for r in recipe):
        print_fn("WARNING: a placement without a pickup assumes a block is already held.")
    print_fn("New recipe: " + " -> ".join(f'{r["cell"]}:{r["pickup"]}'
                                        for r in recipe))
    # Verify every referenced recording BEFORE asking to overwrite the export.
    preview = [board.build_plan(script6, Path(folder), int(cell[1]),
                                int(cell[3]), "place", item["pickup"])
               for cell, item in zip(cells, recipe)]
    count = sum(len(plan.waypoints) for plan in preview)
    print_fn(f"Validated {count} saved waypoints for this exported line. "
             "Collision clearance and calibration NOT verified.")
    if input_fn("Type SAVE to rebuild exported winning line and back it up: ").strip() != "SAVE":
        print_fn("Cancelled. No changes saved.")
        return
    changed = board.refresh_winning_line(script6, path, cells, original, data, recipe)
    print_fn(f'{path.name}: {"updated (backup saved)" if changed else "already current"}')


def _preview_lines(folder, script6, board, print_fn=print):
    lines = _lines(folder, board)
    if not lines:
        print_fn("No saved winning lines found.")
        return
    for path in lines:
        try:
            cells, _original, _data, recipe = _recipes_for_line(board, path)
            if any(not isinstance(item, dict) or item.get("cell") != cell
                   for cell, item in zip(cells, recipe)):
                raise ValueError("Source placements do not match line cells.")
            plans = [
                board.build_plan(script6, Path(folder), int(cell[1]), int(cell[3]),
                                 "place", item.get("pickup"))
                for cell, item in zip(cells, recipe)
            ]
            num = sum(len(plan.waypoints) for plan in plans)
            seq = [point.positions for plan in plans for point in plan.waypoints]
            duplicates = sum(a == b for a, b in zip(seq, seq[1:]))
            info = " -> ".join(
                f'{plan.cell}:{plan.phases[0] if plan.pickup else "held/no-grab"}'
                for plan in plans
            )
            print_fn(f'{path.name}: OK {num} points, {duplicates} consecutive duplicates; {info}')
        except (OSError, ValueError, KeyError, IndexError, RuntimeError, TypeError) as error:
            print_fn(f'{path.name}: NEEDS REMAP / RECORDING: {error}')
    print_fn("Preview is offline. Duplicate points are NOT removed automatically.")


def run_editor(script6, folder, *, board, input_fn=input, print_fn=print):
    folder = Path(folder)
    if not folder.is_dir():
        raise ValueError(f"Motion folder missing: {folder}")
    print_fn("\nROBOARM OFFLINE TRAY + WINNING-LINE REMAPPER")
    print_fn("No motors can be commanded from this editor.")
    print_fn("Mappings do not repair inaccurate calibration or obsolete waypoints.")
    while True:
        print_fn("\n1 Show 6 tray slots | 2 Change slot/block/grab route | "
                 "3 List winning lines")
        print_fn("4 Change pickups in a winning line | 5 Preview/check lines | "
                 "6 Sync linked lines | Q Quit")
        choice = input_fn("remap> ").strip().lower()
        try:
            if choice in ("q", "quit", "exit"):
                return
            if choice == "1":
                _show_slots(load_mapping(folder), print_fn)
            elif choice == "2":
                _edit_slot(folder, input_fn, print_fn)
            elif choice == "3":
                _choose_line(folder, board, input_fn, print_fn)
            elif choice == "4":
                _edit_line(folder, script6, board, input_fn, print_fn)
            elif choice == "5":
                _preview_lines(folder, script6, board, print_fn)
            elif choice == "6":
                print_fn("Sync rebuilds linked winning-line exports using today's slot mapping.")
                print_fn("It does NOT adjust servo coordinates or recalibrate your arm.")
                if input_fn("Type SYNC to proceed: ").strip() != "SYNC":
                    print_fn("Sync cancelled.")
                    continue
                for result in board.sync_winning_lines(script6, folder):
                    print_fn(result)
            else:
                print_fn("Choose a listed option.")
        except (OSError, ValueError, KeyError, IndexError, RuntimeError, TypeError) as error:
            print_fn(f"Not changed: {error}")
