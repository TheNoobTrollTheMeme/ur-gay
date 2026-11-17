"""Reference-only six-slot tray layout for the COMP terminal pseudo-GUI.

IMPORTANT: This file is NOT the robot's tray_mapping.json. Never interpret
these labels as motor commands, pickup routes, or confirmed geometry.
The COMP motion catalog, macros, board planner, and gripper do not read this.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import tempfile

from tray_mapping import DEFAULT_SLOTS, BLOCKS, ZONES

REFERENCE_PATH = Path(__file__).resolve().parents[1] / "comp_tray_reference.json"
POSITION_LIMIT = 32
NOTE_LIMIT = 72


def new_reference():
    return {
        "version": 1,
        "kind": "comp_tray_reference_only",
        "slots": [
            {
                "slot": slot["slot"],
                "zone": slot["zone"],
                "position": slot["position"],
                "block": slot["block"],
                "note": "",
            }
            for slot in deepcopy(DEFAULT_SLOTS)
        ],
    }


def validate_reference(data):
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("Unknown COMP tray reference version.")
    if data.get("kind") != "comp_tray_reference_only":
        raise ValueError("Not a COMP reference-only tray layout.")
    slots = data.get("slots")
    if not isinstance(slots, list) or len(slots) != 6:
        raise ValueError("Reference tray needs exactly six physical slots.")
    numbers = set()
    for entry in slots:
        if not isinstance(entry, dict):
            raise ValueError("Invalid reference slot.")
        number = entry.get("slot")
        if type(number) is not int or number not in range(1, 7) or number in numbers:
            raise ValueError("Reference slot IDs must be unique numbers 1..6.")
        numbers.add(number)
        if entry.get("zone") not in ZONES:
            raise ValueError(f"Slot {number}: invalid zone.")
        if entry.get("block") not in BLOCKS:
            raise ValueError(f"Slot {number}: invalid block.")
        position = entry.get("position")
        if (not isinstance(position, str)
                or not 1 <= len(position.strip()) <= POSITION_LIMIT):
            raise ValueError(f"Slot {number}: position needs 1..{POSITION_LIMIT} characters.")
        note = entry.get("note")
        if not isinstance(note, str) or len(note) > NOTE_LIMIT:
            raise ValueError(f"Slot {number}: note must be at most {NOTE_LIMIT} characters.")
        if any(key in entry for key in ("grab", "servo", "positions_raw", "waypoints")):
            raise ValueError("Reference-only layout cannot contain commands or grab routes.")
    return data


def slot_at(data, number):
    validate_reference(data)
    if type(number) is not int or number not in range(1, 7):
        raise ValueError("Pick a slot 1..6.")
    return next(item for item in data["slots"] if item["slot"] == number)


def edit_reference(data, number, **fields):
    """Apply staged label changes without altering caller data or the robot."""
    if not fields or set(fields) - {"zone", "position", "block", "note"}:
        raise ValueError("Only zone, position, block and note may be edited.")
    updated = deepcopy(validate_reference(data))
    slot_at(updated, number).update(fields)
    return validate_reference(updated)


def load_reference(path=REFERENCE_PATH):
    path = Path(path)
    if not path.exists():
        return new_reference()
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError) as exc:
        raise ValueError(f"Invalid COMP tray reference: {exc}") from exc
    return validate_reference(result)


def save_reference(data, path=REFERENCE_PATH):
    """Atomic save with backup, only ever to dedicated reference JSON."""
    validate_reference(data)
    path = Path(path)
    if path.name != "comp_tray_reference.json":
        # Tests can write a temporary file using the same literal filename.
        raise ValueError("COMP tray reference must use comp_tray_reference.json.")
    if path.resolve() == (path.parent / "tray_mapping.json").resolve():
        raise ValueError("Refusing to overwrite physical tray mapping.")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".comp-tray-ref-", suffix=".tmp", dir=path.parent)
    backup = None
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=2, ensure_ascii=False)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        if path.exists():
            backup = path.with_name(path.name + "."
                                    + datetime.now().strftime("%Y%m%d-%H%M%S-%f")
                                    + ".bak")
            shutil.copy2(path, backup)
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
    return path, backup
