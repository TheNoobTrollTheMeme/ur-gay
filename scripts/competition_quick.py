#!/usr/bin/env python3
"""Quick Competition Mode: named motion queues, reusable macros, one speed choice.

Usage:
    comp                           interactive macro-style editor
    comp grab1 c1r2                run two recorded motions, one RUN confirmation
    comp --preset opener           run a stored queue, one RUN confirmation
    comp --repeat                  replay most recently started queue
    comp --preset opener --dry-run  preview only; no arm connection
    comp --speed 500 --accel 25 grab1 c1r2
    comp --legacy                  original live competition editor / Joy-Con

Each run rebuilds the READY catalog from the Pi's LOCAL recordings. Presets
save motion NAMES rather than changing numeric IDs; invalid/simulation-only
files cannot play. Recorded waypoints themselves are never changed here.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SCRIPT6 = ROOT / "scripts" / "06_live_grid_tool.py"
PRESETS = ROOT / "motions" / "_competition_presets.json"
NAME = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
DEFAULT_SPEED = 300
DEFAULT_ACCEL = 20


def editor_module():
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("quick_comp_script6", SCRIPT6)
    if spec is None or spec.loader is None:
        raise FileNotFoundError(f"Script 6 missing: {SCRIPT6}")
    editor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(editor)
    return editor


def load_store(path=PRESETS):
    path = Path(path)
    if not path.exists():
        return {"version": 1, "presets": {}, "last": None,
                "speed": DEFAULT_SPEED, "accel": DEFAULT_ACCEL}
    store = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(store, dict) or store.get("version") != 1:
        raise ValueError(f"Unknown competition presets format: {path}")
    if not isinstance(store.get("presets"), dict):
        raise ValueError("Invalid competition presets dictionary")
    if store.get("last") is not None:
        validate_macro(store["last"])
    for name, payload in store["presets"].items():
        if not NAME.fullmatch(name):
            raise ValueError(f"Invalid competition macro name: {name}")
        validate_macro(payload)
    validate_speed(store.get("speed", DEFAULT_SPEED), store.get("accel", DEFAULT_ACCEL))
    return store


def validate_speed(speed, accel):
    from playback_speed import validate_playback_settings
    return validate_playback_settings(speed, accel)


def validate_macro(macro):
    if not isinstance(macro, dict):
        raise ValueError("Macro should be a saved queue object")
    queue = macro.get("queue")
    if (not isinstance(queue, list) or not queue or len(queue) > 150
            or any(not isinstance(name, str) or not re.fullmatch(r"@?[a-z][a-z0-9_-]{0,47}", name)
                   for name in queue)):
        raise ValueError("Macro must contain 1..150 saved motion names")
    validate_speed(macro.get("speed"), macro.get("accel"))
    return macro


def atomic_store(data, path=PRESETS):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    fd, temp_path = tempfile.mkstemp(prefix=".competition.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(blob)
            out.flush()
            os.fsync(out.fileno())
        if path.exists():
            backup = path.with_name(path.name + "."
                                    + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".bak")
            shutil.copy2(path, backup)
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def catalog_for(editor, *, report=print):
    """Re-read actual recordings BEFORE each play, without sending commands."""
    editor.refresh_attacks()
    from pose_bookmarks import list_bookmarks
    return editor.competition.build_motion_catalog(
        editor.wp.find_sequences(), editor.wp.load_sequence,
        editor.validate_competition_target, report=report,
        bookmarks=list_bookmarks(editor.BOOKMARK_FILE, editor.settings.JOINT_NAMES))


def resolve_queue(actions, catalog, labels, aliases):
    """Re-resolve all names (or IDs supplied at the prompt) atomically."""
    if not actions:
        raise ValueError("Queue empty. Add a recording or load a macro.")
    if len(actions) > 150:
        raise ValueError("At most 150 queued motions per macro.")
    plan_cls = __import__("competition_queue").QueuePlan
    plan = plan_cls(catalog, aliases=aliases, labels=labels)
    for action in actions:
        plan.append(action)
    names = tuple(plan.name(identifier) for identifier in plan.pending)
    return plan, names


def show_catalog(catalog, labels, *, limit=30, report=print):
    identifiers = sorted(catalog, key=int)
    visible = identifiers[:limit] if limit is not None else identifiers
    report("READY motions (name or ID):")
    for ident in visible:
        report(f"  {ident:>3} {labels[ident]} ({len(catalog[ident])} points)")
    if len(visible) < len(identifiers):
        report(f"  ... {len(identifiers) - len(visible)} more; 'catalog' lists every motion.")
    report("A cell name such as c2r1 combines its M + P recordings.")


def show_queue(names, speed, accel, report=print):
    report("Queue: " + (" -> ".join(names) if names else "(empty)"))
    report(f"Arm speed/accel: {speed}/{accel}. Gripper stays 300/20.")
    report("Motor clearance is NOT checked: use only routes taught and verified on this rig.")


def save_macro(store, name, names, speed, accel, *, path=PRESETS, replace=False):
    name = name.lower().strip()
    if not NAME.fullmatch(name):
        raise ValueError("Macro name: start with a letter, then letters/numbers/_/-.")
    payload = dict(queue=list(names), speed=speed, accel=accel)
    validate_macro(payload)
    if name in store["presets"] and not replace:
        raise ValueError(f"Macro {name!r} exists. Use a different name.")
    updated = dict(store, presets=dict(store["presets"]))
    updated["presets"][name] = payload
    atomic_store(updated, path)
    return updated


def remember_last(store, names, speed, accel, *, path=PRESETS):
    macro = dict(queue=list(names), speed=speed, accel=accel)
    validate_macro(macro)
    updated = dict(store, last=macro, speed=speed, accel=accel)
    atomic_store(updated, path)
    return updated


def preview(names, speed, accel, plan, *, report=print):
    show_queue(names, speed, accel, report=report)
    report(f"Total: {sum(len(plan.catalog[ident]) for ident in plan.pending)} waypoints.")
    report("Each motion is prevalidated from local files; no automatic clearance planner.")
    report("During motion: SPACE/Q/Esc stops; terminal motor position is monitored.")


def execute(editor, actions, speed, accel, store, *, yes=False, dry_run=False,
            path=PRESETS, report=print):
    validate_speed(speed, accel)  # Fail before any motor connection.
    # IMPORTANT: build a fresh catalog so macro links point to current saved poses.
    catalog, labels, aliases = catalog_for(editor, report=report)
    plan, names = resolve_queue(actions, catalog, labels, aliases)
    preview(names, speed, accel, plan, report=report)
    if dry_run:
        report("DRY RUN: no motor connection, no movement, no preset change.")
        return None
    if not yes:
        if input("Clear ALL motion paths; support arm. Type RUN to start (else cancel): ").strip() != "RUN":
            report("Cancelled; no motor connection.")
            return False
    if speed > 600 or accel > 35:
        if input("High servo speed. Type HIGH to confirm this run: ").strip() != "HIGH":
            report("High-speed run cancelled; no motor connection.")
            return False
    # Persist only the queue and preferences, never the motor positions.
    remember_last(store, names, speed, accel, path=path)
    return editor.competition.playback(
        catalog, plan, (speed, accel), connect=editor.connect,
        read_all=editor.read_all, enable_all_torque=editor.enable_all_torque,
        disable_all_torque=editor.disable_all_torque,
        terminal_class=editor.RawTerminal, settings=editor.settings,
        check_result=editor.check_result, gripper_profile="normal",
        report=report, finish_after_queue=True,
    )


def interactive(editor, *, path=PRESETS):
    store = load_store(path)
    speed, accel = store.get("speed", DEFAULT_SPEED), store.get("accel", DEFAULT_ACCEL)
    catalog, labels, aliases = catalog_for(editor)
    names = []
    print("\nQUICK COMP | Macro-style editor, no hardware until RUN.")
    show_catalog(catalog, labels, limit=18)
    print("Commands: a grab1 c2r1 | 2 34 (append IDs) | d 2 | m 2 1 | undo | clear")
    print("          speed 450 25 | save opener | load opener | presets | catalog")
    print("          r (repeat last) | run | dry | q")
    if store.get("last"):
        print("Last run available: 'r' replays it using LIVE local waypoint data.")
    show_queue(names, speed, accel)
    while True:
        try:
            line = input("comp> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nCompetition editor stopped.")
            return
        if not line:
            continue
        words = line.replace(",", " ").split()
        op, args = words[0], words[1:]
        try:
            if op in ("q", "quit", "exit"):
                return
            if op in ("?", "help"):
                print("a NAME ... | d INDEX | m FROM TO | undo | clear | speed N A")
                print("save NAME | load NAME | presets | r | run | dry | catalog | q")
                continue
            if op in ("catalog", "all"):
                # Catalog refresh picks up newly edited sequences.
                catalog, labels, aliases = catalog_for(editor)
                show_catalog(catalog, labels, limit=None)
                continue
            if op in ("presets", "ls"):
                print("Saved: " + (", ".join(sorted(store["presets"])) or "(none)"))
                continue
            if op == "load" and len(args) == 1:
                macro = validate_macro(store["presets"][args[0]])
                # Fail before updating current queue if a saved recording vanished.
                resolve_queue(macro["queue"], catalog, labels, aliases)
                names, speed, accel = list(macro["queue"]), macro["speed"], macro["accel"]
            elif op == "r" and not args:
                if store.get("last") is None:
                    raise ValueError("No last queue to repeat yet.")
                macro = validate_macro(store["last"])
                print("Repeating last saved queue:")
                return execute(editor, macro["queue"], macro["speed"], macro["accel"],
                               store, yes=True, path=path)
            elif op == "save" and len(args) == 1:
                _, canonical = resolve_queue(names, catalog, labels, aliases)
                overwrite = args[0] in store["presets"]
                if overwrite and input(f"Overwrite macro {args[0]}? [y/N]: ").strip().lower() != "y":
                    print("Save cancelled.")
                    continue
                store = save_macro(store, args[0], canonical, speed, accel,
                                   path=path, replace=overwrite)
                print(f"Saved {args[0]} ({len(canonical)} actions).")
            elif op in ("a", "add", "+") and args:
                _, canon = resolve_queue([*names, *args], catalog, labels, aliases)
                names = list(canon)
            elif all(x.isdigit() for x in words):
                _, canon = resolve_queue([*names, *words], catalog, labels, aliases)
                names = list(canon)
            elif op in ("d", "del") and len(args) == 1:
                index = int(args[0])
                if index < 1 or index > len(names):
                    raise ValueError("Remove index must refer to a queued motion.")
                removed = names.pop(index-1)
                print("Removed " + removed)
            elif op == "m" and len(args) == 2:
                source, dest = map(int, args)
                if not (1 <= source <= len(names) and 1 <= dest <= len(names)):
                    raise ValueError("Both indices must refer to queued motions.")
                names.insert(dest-1, names.pop(source-1))
            elif op in ("u", "undo") and not args:
                if names:
                    print("Removed " + names.pop())
            elif op == "clear" and not args:
                names.clear()
            elif op in ("speed", "s") and len(args) == 2:
                proposed = tuple(map(int, args))
                speed, accel = validate_speed(*proposed)
                store = dict(store, speed=speed, accel=accel)
                atomic_store(store, path)
            elif op == "dry" and not args:
                execute(editor, names, speed, accel, store, dry_run=True, path=path)
                continue
            elif op == "run" and not args:
                print("RUN selected. No further ready/waypoint prompts.")
                return execute(editor, names, speed, accel, store, yes=True, path=path)
            else:
                print("Unknown command; use '?' for help.")
                continue
            show_queue(names, speed, accel)
        except (ValueError, KeyError, TypeError, OSError, IndexError) as exc:
            print(f"Not applied: {exc}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("motions", nargs="*", help="saved motion names/IDs, e.g. grab1 c1r1")
    parser.add_argument("-p", "--preset", help="run a saved macro name")
    parser.add_argument("-s", "--speed", type=int, help="arm speed 50..1200; persists after run")
    parser.add_argument("-a", "--accel", type=int, help="arm acceleration 1..60")
    parser.add_argument("--repeat", action="store_true", help="run last queue with last speed")
    parser.add_argument("--dry-run", action="store_true", help="validate without motor connection")
    parser.add_argument("--list", action="store_true", help="list saved macros and recorded motions")
    parser.add_argument("--legacy", action="store_true", help="original dynamic queue/Joy-Con interface")
    args = parser.parse_args(argv)
    if sum(bool(x) for x in (args.preset, args.repeat, bool(args.motions))) > 1:
        parser.error("Choose saved --preset, --repeat, OR direct motion names.")
    editor = editor_module()
    if args.legacy:
        return editor.competition_placements()
    store = load_store()
    if args.list:
        catalog, labels, _ = catalog_for(editor)
        show_catalog(catalog, labels, limit=None)
        print("Presets: " + (", ".join(sorted(store["presets"])) or "(none)"))
        return
    if not (args.motions or args.preset or args.repeat) and not args.dry_run:
        return interactive(editor)
    if args.preset:
        try:
            macro = store["presets"][args.preset.lower()]
        except KeyError as error:
            raise ValueError(f"Unknown preset {args.preset!r}; run 'comp --list'.") from error
    elif args.repeat:
        macro = store["last"]
        if macro is None:
            raise ValueError("No previous queue yet. Build one with 'comp'.")
    elif args.motions:
        macro = dict(queue=args.motions, speed=store.get("speed", DEFAULT_SPEED),
                     accel=store.get("accel", DEFAULT_ACCEL))
    else:
        raise ValueError("Dry-run requires motions, --preset or --repeat.")
    macro = validate_macro(macro)
    speed = args.speed if args.speed is not None else macro["speed"]
    accel = args.accel if args.accel is not None else macro["accel"]
    return execute(editor, macro["queue"], speed, accel, store, dry_run=args.dry_run)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, RuntimeError, TimeoutError, KeyboardInterrupt, EOFError) as exc:
        print(f"\nCompetition stopped: {exc}", file=sys.stderr)
        raise SystemExit(1)
