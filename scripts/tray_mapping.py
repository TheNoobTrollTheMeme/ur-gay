"""Offline map of six physical supply slots to blocks and recorded grab routes.

The map changes macro composition only; it never alters servo calibration,
coordinates, or original waypoint recordings.
"""
from copy import deepcopy
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

ZONES = ("B-left", "A-front", "B-right")
BLOCKS = ("cube", "cylinder", "trophy", "empty")
NAME = re.compile(r"[a-zA-Z][a-zA-Z0-9_-]{0,63}\Z")
DEFAULT_SLOTS = (
    {"slot": 1, "zone": "B-left", "position": "near", "block": "cylinder", "grab": "grab1"},
    {"slot": 2, "zone": "B-left", "position": "far", "block": "cube", "grab": "grab2"},
    {"slot": 3, "zone": "A-front", "position": "left", "block": "trophy", "grab": "grab3"},
    {"slot": 4, "zone": "A-front", "position": "right", "block": "cube", "grab": "grab4"},
    {"slot": 5, "zone": "B-right", "position": "near", "block": "cylinder", "grab": "grab5"},
    {"slot": 6, "zone": "B-right", "position": "far", "block": "trophy", "grab": "grab6"},
)


def mapping_file(motions_folder):
    return Path(motions_folder).parent / "tray_mapping.json"


def default_mapping():
    return {"version": 1, "slots": deepcopy(list(DEFAULT_SLOTS))}


def validate_mapping(mapping):
    if not isinstance(mapping, dict) or mapping.get("version") != 1:
        raise ValueError("Tray mapping requires version 1.")
    slots = mapping.get("slots")
    if not isinstance(slots, list) or len(slots) != 6:
        raise ValueError("Tray mapping must define exactly six slots.")
    numbers = set()
    for entry in slots:
        if not isinstance(entry, dict):
            raise ValueError("Tray slot is not a mapping.")
        number = entry.get("slot")
        if type(number) is not int or number not in range(1, 7) or number in numbers:
            raise ValueError("Slot IDs must be unique integers 1..6.")
        numbers.add(number)
        if entry.get("zone") not in ZONES:
            raise ValueError(f"Slot {number}: invalid tray zone.")
        block = entry.get("block")
        if block not in BLOCKS:
            raise ValueError(f"Slot {number}: invalid block type.")
        position = entry.get("position")
        if not isinstance(position, str) or not 1 <= len(position) <= 32:
            raise ValueError(f"Slot {number}: supply position must be 1..32 characters.")
        grab = entry.get("grab")
        if block == "empty":
            if grab is not None:
                raise ValueError(f"Slot {number}: empty slot cannot have a grab route.")
        elif not isinstance(grab, str) or not NAME.fullmatch(grab):
            raise ValueError(f"Slot {number}: invalid grab recording name.")
    return mapping


def load_mapping(motions_folder):
    path = mapping_file(motions_folder)
    if not path.is_file():
        return default_mapping()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeError) as error:
        raise ValueError(f"Invalid tray mapping: {error}") from error
    return validate_mapping(data)


def get_slot(mapping, number):
    if type(number) is not int or number not in range(1, 7):
        raise ValueError("Choose a tray slot 1..6.")
    return next(slot for slot in mapping["slots"] if slot["slot"] == number)


def save_mapping(motions_folder, mapping):
    """Atomic write; make a timestamped backup if the configuration exists."""
    validate_mapping(mapping)
    path = mapping_file(motions_folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if path.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup = path.with_name(path.name + f".{stamp}.bak")
        shutil.copy2(path, backup)
    fd, tmp = tempfile.mkstemp(prefix=".tray_mapping-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(mapping, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return path, backup
