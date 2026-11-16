#!/usr/bin/env python3
"""Run Kelvin's original Script 5 with read-only support for linked bookmarks.

Do not change the original 05_waypoint_sequence.py. A waypoint reference
{"bookmark": "ttl"} is resolved from motions/_pose_bookmarks.json whenever
Script 5 loads that sequence. Unlinked recordings retain Kelvin's loader.
"""
import importlib.util
import sys
from pathlib import Path

from pose_bookmarks import load_with_bookmarks

ROOT = Path(__file__).resolve().parents[1]


def enable_bookmarks(original_script, joint_names, bookmark_file):
    """Install a loader wrapper; never write to recordings or move the arm."""
    original_load = original_script.load_sequence
    if getattr(original_load, "_supports_pose_bookmarks", False):
        return original_script

    def load_with_references(path):
        return load_with_bookmarks(
            path, original_load, original_script.parse_point_values,
            joint_names, bookmark_file,
        )

    load_with_references._supports_pose_bookmarks = True
    original_script.load_sequence = load_with_references
    return original_script


def main():
    sys.path.insert(0, str(ROOT))
    import arm_settings as settings

    script = ROOT / "scripts" / "05_waypoint_sequence.py"
    if not script.is_file():
        raise FileNotFoundError(f"Kelvin's original waypoint script missing: {script}")
    spec = importlib.util.spec_from_file_location("kelvin_waypoint_script", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {script}")
    original = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(original)
    enable_bookmarks(original, settings.JOINT_NAMES,
                     ROOT / "motions" / "_pose_bookmarks.json")
    print("Kelvin Script 5: linked @bookmarks resolve to their current saved values.")
    print("Original recording functions, movement confirmation and E-stop are unchanged.")
    original.main()


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\nWaypoint editor stopped.")
