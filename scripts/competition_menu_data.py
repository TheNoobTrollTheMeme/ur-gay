"""Read-only motion discovery and display preferences for the COMP menu.

Display choices never change motion files, queue contents, or playback checks.
The READY catalog remains the existing competition_queue validation result.
"""
from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import dataclass
from datetime import datetime
import io
import json
import os
from pathlib import Path
import re
import shutil
import tempfile


VIEW_FILENAME = "comp_menu_view.json"
ROOT = Path(__file__).resolve().parents[1]
VIEW_PATH = ROOT / VIEW_FILENAME
WIN_PATTERN = re.compile(
    r"winning_line_(c[1-3]r[1-3])_(c[1-3]r[1-3])_(c[1-3]r[1-3])(?:_(\d+))?", re.I)


def is_winning_line(name):
    return str(name).casefold().startswith("winning_line_")


def display_name(name):
    match = WIN_PATTERN.fullmatch(str(name))
    if not match:
        return str(name)
    cells = ">".join(cell.upper() for cell in match.groups()[:3])
    return "WIN " + cells + (f" #{match.group(4)}" if match.group(4) else "")


def _valid_name(name):
    return (isinstance(name, str) and 0 < len(name) <= 255
            and not any(ord(ch) < 32 or ch in "/\\" for ch in name))


def load_hidden(path=VIEW_PATH):
    path = Path(path)
    if not path.exists():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(data, dict) or data.get("version") != 1
            or data.get("kind") != "comp_menu_visibility"
            or not isinstance(data.get("hidden"), list)
            or not all(_valid_name(name) for name in data["hidden"])):
        raise ValueError(f"Invalid menu preferences: {path.name}")
    return {name.casefold() for name in data["hidden"]}


def save_hidden(hidden, path=VIEW_PATH):
    """Save names, not changing numeric IDs; no motion or macro writes."""
    path = Path(path)
    if path.name != VIEW_FILENAME:
        raise ValueError(f"Visibility preferences must use {VIEW_FILENAME}.")
    if not all(_valid_name(name) for name in hidden):
        raise ValueError("Invalid motion name in show/hide preferences.")
    names = sorted({name.casefold() for name in hidden})
    data = {"version": 1, "kind": "comp_menu_visibility", "hidden": names}
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".comp-menu-view-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            shutil.copy2(path, path.with_name(f"{path.name}.{stamp}.bak"))
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return path


def window_bounds(index, total, height):
    """A bounded, nonwrapping viewport; all entries remain reachable."""
    height = max(1, int(height))
    if total <= 0:
        return 0, 0
    index = max(0, min(int(index), total - 1))
    first = min(max(0, index - height + 1), max(0, total - height))
    return first, min(total, first + height)


@dataclass
class CatalogSnapshot:
    catalog: dict
    labels: dict
    aliases: dict
    unavailable: dict
    notes: tuple
    folder: Path

    @property
    def all_labels(self):
        return {**self.labels, **{key: value[0] for key, value in self.unavailable.items()}}


def motions_folder(editor):
    bookmark = getattr(editor, "BOOKMARK_FILE", None)
    if bookmark is not None:
        return Path(bookmark).parent
    root = getattr(editor, "ROOT", None)
    if root is not None:
        return Path(root) / "motions"
    return Path(getattr(editor, "folder", ROOT / "motions"))


def build_snapshot(editor, *, report=lambda message: None):
    """Include files omitted by the legacy finder, and explain unavailable ones.

    No motor connection. Supplemental files still pass through the SAME loader
    and validator before they can enter the executable catalog.
    """
    notes = []
    reasons = {}
    output = io.StringIO()

    def collect(message):
        text = str(message)
        notes.append(text)
        match = re.match(r"DISABLED motion (\d+) \(.*?\):\s*(.*)", text, re.S)
        if match:
            reasons[match.group(1)] = match.group(2)

    with redirect_stdout(output):
        editor.refresh_attacks()
        try:
            routes = list(editor.wp.find_sequences())
        except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
            collect(f"Legacy discovery failed; inspecting local motion files: {exc}")
            routes = []
    for line in output.getvalue().splitlines():
        if line.strip():
            collect(line)

    folder = motions_folder(editor)
    seen = {Path(path).resolve() for path, _ in routes}
    read_errors = {}
    metadata = {"_pose_bookmarks.json", "_competition_presets.json",
                "comp_tray_reference.json", "tray_mapping.json", VIEW_FILENAME}
    for path in sorted(folder.glob("*.json"), key=lambda item: item.name.casefold()):
        if path.resolve() in seen or path.name in metadata:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            data = {"name": path.stem}
            read_errors[path.resolve()] = str(exc)
        else:
            if not (is_winning_line(path.stem) or
                    isinstance(data, dict) and
                    (data.get("type") == "pose_sequence" or "points" in data)):
                continue
            if not isinstance(data, dict):
                read_errors[path.resolve()] = "Recording must be a JSON object."
                data = {"name": path.stem}
        routes.append((path, data))
        seen.add(path.resolve())

    from pose_bookmarks import list_bookmarks
    try:
        bookmarks = list_bookmarks(editor.BOOKMARK_FILE, editor.settings.JOINT_NAMES)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        collect(f"Bookmarks unavailable: {exc}")
        bookmarks = {}

    def checked_load(path):
        error = read_errors.get(Path(path).resolve())
        if error is not None:
            raise ValueError(error)
        return editor.wp.load_sequence(path)

    # Collect loader prints, rather than corrupting the curses screen.
    output = io.StringIO()
    with redirect_stdout(output):
        catalog, labels, aliases = editor.competition.build_motion_catalog(
            routes, checked_load, editor.validate_competition_target,
            report=collect, bookmarks=bookmarks)
    for line in output.getvalue().splitlines():
        if line.strip():
            collect(line)
    unavailable = {
        str(index): (Path(path).stem, reasons.get(str(index),
                    "Rejected by the recording loader or validator."))
        for index, (path, _) in enumerate(routes, 1) if str(index) not in catalog
    }
    for note in notes:
        report(note)
    return CatalogSnapshot(catalog, labels, aliases, unavailable, tuple(notes), folder)
