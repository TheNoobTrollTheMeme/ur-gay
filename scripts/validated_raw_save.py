"""Save hand-taught raw-tick waypoints without invoking legacy angle validation.

This module only handles recording files. It has no servo SDK dependency, never
commands movement, and does not change motor/firmware safety settings.
"""
from __future__ import annotations
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
import shutil


def validate_raw_recording(data, joint_names):
    joints = tuple(joint_names)
    if data.get("input_type") != "raw":
        raise ValueError("RAW save requires input_type='raw'.")
    if data.get("type") != "pose_sequence" or data.get("schema_version") != 1:
        raise ValueError("Unrecognized recording schema.")
    points = data.get("points")
    if not isinstance(points, list):
        raise ValueError("Recording points must be a list.")
    if not joints or len(set(joints)) != len(joints):
        raise ValueError("Invalid joint definitions.")
    if len(data.get("joint_order", [])) != len(joints):
        raise ValueError("Recording joint order does not match expected servos.")
    for idx, point in enumerate(points, 1):
        values = point.get("input_values")
        if not isinstance(values, list) or len(values) != len(joints):
            raise ValueError(f"Waypoint {idx}: expected {len(joints)} raw values.")
        if any(type(v) is not int or not 0 <= v <= 4095 for v in values):
            raise ValueError(f"Waypoint {idx}: servo raw ticks must be integers 0..4095.")


def save_raw_recording(path, data, joint_names):
    """Atomic save with a timestamped backup. No angle conversion/checks."""
    path = Path(path)
    validate_raw_recording(data, joint_names)
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if path.exists():
        backup = path.with_name(path.name + "." +
            datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".bak")
        shutil.copy2(path, backup)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return backup
