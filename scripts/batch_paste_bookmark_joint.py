#!/usr/bin/env python3
"""Offline batch paste: one joint coordinate from a saved pose bookmark.

Select explicit sequences and waypoint numbers. No servo SDK, robot connection,
torque control, or motion commands. Dry-run by default; backs up before writing.

A destination waypoint that is linked to a bookmark is detached into a static
raw pose ONLY when its selected coordinate actually changes. Its other joint
values are preserved at the time of editing; other references stay linked.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import re
import stat
import tempfile

from pose_bookmarks import get_bookmark, list_bookmarks, normalize_name, resolve_points
from raw_playback_validation import validate_raw_pose

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MOTIONS = ROOT / "motions"


@dataclass(frozen=True)
class Change:
    waypoint: int
    old: int
    new: int
    detached: bool = False


@dataclass(frozen=True)
class FileEdit:
    path: Path
    before: bytes
    after: bytes
    changes: tuple[Change, ...]


def parse_numbers(expression, maximum):
    """Parse 'all', '2', or '1,3-5' into distinct, sorted 1-based indices."""
    expression = expression.strip().lower()
    if maximum < 1:
        raise ValueError("This sequence has no waypoints.")
    if expression == "all":
        return list(range(1, maximum + 1))
    if not expression:
        raise ValueError("Specify waypoint numbers, e.g. 1,3-5, or all.")
    selected = set()
    for item in expression.split(","):
        match = re.fullmatch(r"([1-9]\d*)(?:-([1-9]\d*))?", item.strip())
        if not match:
            raise ValueError(f"Bad waypoint selection {item!r}. Use 1,3-5 or all.")
        first = int(match.group(1))
        last = int(match.group(2)) if match.group(2) else first
        if first > last or last > maximum:
            raise ValueError(f"Waypoint range {item!r} outside 1..{maximum}.")
        selected.update(range(first, last + 1))
    return sorted(selected)


def load_sequence_list(motions):
    """Read only local pose-sequence JSON files, never derived/attack outputs."""
    motions = Path(motions)
    if not motions.is_dir():
        raise ValueError(f"Motion directory missing: {motions}")
    found = {}
    for path in sorted(motions.glob("*.json")):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        if not isinstance(data, dict) or data.get("type") != "pose_sequence":
            continue
        name = path.stem.lower()
        if name in found:
            raise ValueError(f"Duplicate sequence name (case-insensitive): {path.stem}")
        found[name] = (path, data)
    return found


def parse_targets(arguments):
    """Turn repeated 'grab1:1,3' targets into a per-sequence selection map."""
    selections = {}
    for entry in arguments:
        if ":" not in entry:
            raise ValueError(f"Use SEQUENCE:POINTS, e.g. grab1:1,3, not {entry!r}.")
        name, spec = entry.split(":", 1)
        name = name.strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name):
            raise ValueError(f"Invalid sequence name {name!r}.")
        if name in selections:
            raise ValueError(f"Sequence {name!r} selected twice; combine its waypoint numbers.")
        selections[name] = spec.strip()
    if not selections:
        raise ValueError("Select at least one sequence.")
    return selections


def _parse_raw(values, input_type, joint_names):
    joints = tuple(joint_names)
    if input_type != "raw" or not isinstance(values, list) or len(values) != len(joints):
        raise ValueError(f"Expected {len(joints)} RAW servo tick values.")
    return dict(zip(joints, values))


def prepare_batch(motions, bookmark, joint, selections, joint_names):
    """Preflight all selected sequences before producing any changes.

    All original documents remain untouched until apply_batch is called.
    A linked destination point is materialized only if the target joint differs.
    """
    joints = tuple(joint_names)
    if joint not in joints:
        raise ValueError(f"Joint must be one of: {', '.join(joints)}")
    bookmark = normalize_name(bookmark.removeprefix("@"))
    library = Path(motions) / "_pose_bookmarks.json"
    source = get_bookmark(library, bookmark, joints)
    value = source[joint]
    available = load_sequence_list(motions)
    if not selections:
        raise ValueError("No sequences selected.")
    edits = []
    for name, spec in selections.items():
        key = name.lower()
        if key not in available:
            raise ValueError(f"Unknown sequence {name!r}. Choose a listed motion.")
        path, _ = available[key]
        before = path.read_bytes()
        data = json.loads(before)
        if data.get("derived_from"):
            raise ValueError(f"{path.stem} is generated; edit its source m/p sequence.")
        if data.get("type") != "pose_sequence" or data.get("schema_version") != 1:
            raise ValueError(f"{path.stem}: unsupported motion schema.")
        if data.get("input_type") != "raw":
            raise ValueError(f"{path.stem} is angle-format; convert it in Script 6 editor first.")
        order = data.get("joint_order")
        if (not isinstance(order, list) or
                [entry.get("joint") if isinstance(entry, dict) else None
                 for entry in order] != list(joints)):
            raise ValueError(f"{path.stem}: joint order differs from arm configuration.")
        points = data.get("points")
        if not isinstance(points, list):
            raise ValueError(f"{path.stem}: missing points list.")
        selected = parse_numbers(spec, len(points))
        decoded = resolve_points(
            data, lambda values, input_type: _parse_raw(values, input_type, joints),
            joints, library, min_points=2)
        new_points = list(points)
        changes = []
        joint_index = joints.index(joint)
        for number in selected:
            old_pose = decoded[number - 1]
            old_value = old_pose[joint]
            if old_value == value:
                continue
            amended_pose = dict(old_pose, **{joint: value})
            validate_raw_pose(amended_pose, joints)
            original = points[number - 1]
            detached = "bookmark" in original
            if detached:
                amended = {"input_values": [amended_pose[j] for j in joints]}
            elif "input_values" in original:
                amended = dict(original)
                amended_values = list(original["input_values"])
                amended_values[joint_index] = value
                amended["input_values"] = amended_values
            elif "positions_raw" in original:
                amended = dict(original)
                amended["positions_raw"] = amended_pose
            else:
                raise ValueError(f"{path.stem} waypoint {number}: unknown data structure.")
            new_points[number - 1] = amended
            changes.append(Change(number, old_value, value, detached))
        if changes:
            updated = dict(data, points=new_points)
            # Validate the entire modified recording, including surviving links.
            resolve_points(
                updated, lambda values, input_type: _parse_raw(values, input_type, joints),
                joints, library, min_points=2)
            after = (json.dumps(updated, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        else:
            after = before
        edits.append(FileEdit(path, before, after, tuple(changes)))
    return edits, value


def summarize(edits, bookmark, joint, value):
    print(f"\nSOURCE @{bookmark.lstrip('@')} | {joint} = {value} raw ticks")
    changed_files = changed_points = detached = 0
    for edit in edits:
        if not edit.changes:
            print(f"  {edit.path.stem}: no changes (already {value})")
            continue
        changed_files += 1
        changed_points += len(edit.changes)
        print(f"  {edit.path.stem}:")
        for change in edit.changes:
            suffix = "  [DETACH @bookmark -> raw waypoint]" if change.detached else ""
            print(f"    waypoint {change.waypoint}: {change.old} -> {change.new}{suffix}")
            detached += change.detached
    print(f"PREVIEW: {changed_points} waypoints in {changed_files} sequences; "
          f"{detached} linked points to detach.")
    print("Only the selected joint coordinate changes. No hardware commands.")
    print("This checks raw data integrity, NOT physical travel/collision clearance.")
    return changed_files


def _atomic_bytes(path, content):
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.",
                                            suffix=".tmp", dir=path.parent)
    try:
        os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def apply_batch(edits):
    """Back up all targets first. Abort on external edits; attempt rollback on fault."""
    changed = [item for item in edits if item.changes]
    if not changed:
        return []
    for item in changed:
        if item.path.read_bytes() != item.before:
            raise RuntimeError(f"{item.path.name} changed since preview. Nothing written.")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backups = []
    for item in changed:
        path = item.path.with_name(item.path.name + "." + stamp + ".bak")
        with path.open("xb") as output:
            output.write(item.before)
            output.flush()
            os.fsync(output.fileno())
        backups.append(path)
    written = []
    try:
        for item in changed:
            if item.path.read_bytes() != item.before:
                raise RuntimeError(f"{item.path.name} changed during the operation.")
            _atomic_bytes(item.path, item.after)
            written.append(item)
    except BaseException:
        for item in reversed(written):
            # Avoid replacing a concurrent edit if the file changed again.
            if item.path.read_bytes() == item.after:
                _atomic_bytes(item.path, item.before)
        raise
    return backups


def _select_interactive(motions, joint_names):
    bookmarks = list_bookmarks(Path(motions) / "_pose_bookmarks.json", joint_names)
    if not bookmarks:
        raise ValueError("No bookmarks found; capture one in Script 6 first.")
    print("\nSaved bookmarks:")
    names = list(bookmarks)
    for idx, name in enumerate(names, 1):
        print(f"  {idx}: @{name}")
    selected = input("Bookmark name or number (Q cancels): ").strip().lower()
    if selected in ("q", ""):
        return None
    bookmark = names[int(selected) - 1] if selected.isdecimal() and 1 <= int(selected) <= len(names) else selected.removeprefix("@")
    bookmark = normalize_name(bookmark)
    if bookmark not in bookmarks:
        raise ValueError(f"Unknown bookmark @{bookmark}.")

    print("\nPick ONE joint coordinate:")
    for idx, joint in enumerate(joint_names, 1):
        print(f"  {idx}: {joint} = {bookmarks[bookmark][joint]}")
    raw_joint = input("Joint name or number (Q cancels): ").strip().lower()
    if raw_joint in ("q", ""):
        return None
    joint = (joint_names[int(raw_joint) - 1]
             if raw_joint.isdecimal() and 1 <= int(raw_joint) <= len(joint_names)
             else raw_joint)
    if joint not in joint_names:
        raise ValueError("Unknown joint name/number.")

    available = load_sequence_list(motions)
    candidates = [(name, path, data) for name, (path, data) in available.items()
                  if data.get("input_type") == "raw" and not data.get("derived_from")]
    if not candidates:
        raise ValueError("No editable raw sequences. Record/convert a sequence in Script 6.")
    print("\nAvailable RAW sequences (generated attack files excluded):")
    for idx, (name, _, data) in enumerate(candidates, 1):
        print(f"  {idx}: {name} ({len(data.get('points', []))} waypoints)")
    selected = input("Sequence numbers/names separated by spaces (Q cancels): ").strip().lower()
    if selected in ("q", ""):
        return None
    words = selected.replace(",", " ").split()
    choices = {}
    for word in words:
        name = (candidates[int(word) - 1][0]
                if word.isdecimal() and 1 <= int(word) <= len(candidates) else word)
        if name not in {candidate[0] for candidate in candidates}:
            raise ValueError(f"Unknown/unsupported sequence {word!r}.")
        if name in choices:
            raise ValueError(f"Sequence {name!r} selected twice.")
        _, _, data = next(item for item in candidates if item[0] == name)
        spec = input(f"{name}: choose waypoints (e.g. 1,3-4 or all): ").strip()
        parse_numbers(spec, len(data.get("points", [])))
        choices[name] = spec
    return bookmark, joint, choices


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bookmark", "-b", help="source bookmark: ttl or @ttl")
    parser.add_argument("--joint", "-j", help="joint name: shoulder, elbow, etc.")
    parser.add_argument("--target", "-t", action="append", default=[],
                        metavar="SEQUENCE:POINTS",
                        help="repeat: -t grab1:1,3 -t grab2:all")
    parser.add_argument("--apply", action="store_true",
                        help="write validated edits and backup files; otherwise preview only")
    parser.add_argument("--motions", type=Path, default=DEFAULT_MOTIONS,
                        help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    sys_path = str(ROOT)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)
    import arm_settings as settings
    joints = tuple(settings.JOINT_NAMES)
    if not args.bookmark and not args.joint and not args.target:
        result = _select_interactive(args.motions, joints)
        if result is None:
            print("Cancelled.")
            return 0
        bookmark, joint, selections = result
        interactive = True
    else:
        if not args.bookmark or not args.joint or not args.target:
            parser.error("Use -b BOOKMARK -j JOINT -t SEQUENCE:POINTS (repeat -t).")
        bookmark = args.bookmark.removeprefix("@")
        joint = args.joint.lower()
        selections = parse_targets(args.target)
        interactive = False

    edits, value = prepare_batch(args.motions, bookmark, joint, selections, joints)
    count = summarize(edits, bookmark, joint, value)
    if not count:
        print("Nothing to write.")
        return 0
    if not args.apply:
        if not interactive:
            print("PREVIEW ONLY. Add --apply to write these changes.")
            return 0
        if input("Type APPLY to write ALL previewed files (anything else cancels): ").strip() != "APPLY":
            print("Cancelled. Files unchanged.")
            return 0

    backups = apply_batch(edits)
    print(f"APPLIED: {count} sequences updated. {len(backups)} backup files saved.")
    for path in backups:
        print(f"  Backup: {path}")
    print("Review movement paths before playback; this tool did not move the arm.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, RuntimeError, KeyboardInterrupt, EOFError) as error:
        print(f"Batch paste stopped: {error}")
        raise SystemExit(1)
