#!/usr/bin/env python3
"""Live waypoint editor and randomized playback of taught placement routes."""
import argparse
import importlib.util
import json
import random
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import arm_settings as settings
from arm_helpers import (connect, disable_all_torque, enable_all_torque,
                         read_all, move_all_sync, position_to_angle, check_result)
from terminal_keys import RawTerminal
import competition_queue as competition
from playback_speed import (DEFAULT_SPEED, DEFAULT_ACCELERATION,
                            prompt_playback_settings, validate_playback_settings)
from raw_capture_conversion import convert_recording_to_raw
from raw_playback_validation import validate_raw_pose
from validated_raw_save import save_raw_recording, validate_raw_recording
from pose_bookmarks import (
    normalize_name, save_bookmark, list_bookmarks, get_bookmark,
    delete_bookmark, resolve_points, load_with_bookmarks, save_linked_sequence,
    edit_bookmark_reference,
)

spec = importlib.util.spec_from_file_location('waypoints', ROOT / 'scripts/05_waypoint_sequence.py')
wp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wp)
BOOKMARK_FILE = ROOT / 'motions' / '_pose_bookmarks.json'
_original_load_sequence = wp.load_sequence

def _load_sequence_with_bookmarks(path):
    return load_with_bookmarks(
        path, _original_load_sequence, wp.parse_point_values,
        settings.JOINT_NAMES, BOOKMARK_FILE)

# Script 6 resolves linked poses transparently; the original Kelvin file is unchanged.
wp.load_sequence = _load_sequence_with_bookmarks
STOP_KEYS = {' ', 'q', '\x03', '\x1b'}


def choose():
    refresh_attacks()
    routes = wp.find_sequences()
    for i, (path, data) in enumerate(routes, 1):
        print(f'{i}: {path.stem} ({len(data.get("points", []))} points)')
    return routes


def commit(path, data):
    """Raw mode stores measured encoder ticks without legacy angle checks.

    The old angle-based path is unchanged. Raw files still require complete,
    in-range integer ticks, and old recordings are backed up before writing.
    """
    if any(isinstance(point, dict) and 'bookmark' in point for point in data.get('points', [])):
        backup = save_linked_sequence(
            path, data, wp.parse_point_values, settings.JOINT_NAMES, BOOKMARK_FILE)
        if backup is not None:
            print(f'Backup: {backup.name}')
    elif data.get('input_type') == 'raw':
        backup = save_raw_recording(path, data, settings.JOINT_NAMES)
        if backup is not None:
            print(f'Backup: {backup.name}')
    else:
        for point in data['points']:
            if 'input_values' in point:
                wp.parse_point_values(point['input_values'], data['input_type'])
        if path.exists():
            backup = path.with_name(path.name + '.' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.bak')
            shutil.copy2(path, backup)
            print(f'Backup: {backup.name}')
        path.parent.mkdir(exist_ok=True)
        wp.save_sequence(path, data)
    if path.stem.endswith(('m', 'p')):
        refresh_attacks()


def capture():
    input('Support arm. Enter releases torque for hand positioning; Ctrl+C cancels: ')
    port, handler = connect()
    try:
        disable_all_torque(handler)
        print('Move arm by hand. R captures; Q cancels. Keep supporting it.', flush=True)
        with RawTerminal() as terminal:
            while True:
                key = terminal.read_key(0.05)
                if key in STOP_KEYS:
                    return None
                if key == 'r':
                    return read_all(handler)
    finally:
        port.closePort()


def remove_waypoint(path, data):
    """Remove one saved point without connecting to or moving the arm."""
    points = data['points']
    if len(points) <= 2:
        print('Cannot remove: playback requires at least two waypoints. You can replace a point instead.')
        return
    answer = input('Waypoint number to REMOVE, Q or Enter to cancel: ').strip().lower()
    if answer in ('', 'q'):
        return
    try:
        index = int(answer) - 1
    except ValueError:
        print('Choose a listed waypoint number.')
        return
    if not 0 <= index < len(points):
        print('No such waypoint')
        return
    point = points[index]
    print(f'Waypoint {index + 1}: ' + ('@' + point['bookmark'] if 'bookmark' in point else
          str(point.get('input_values', point.get('positions_raw')))))
    confirm = input(f'Remove waypoint {index + 1} from {path.stem}? [y/N]: ').strip().lower()
    if confirm not in ('y', 'yes'):
        print('Removal cancelled.')
        return
    updated = dict(data, points=points[:index] + points[index + 1:])
    commit(path, updated)
    data['points'] = updated['points']
    print(f'Removed waypoint {index + 1}. {len(data["points"])} remain; following points are renumbered.')



def _bookmark_choice():
    name = input('Bookmark name (e.g. safe_left): ').strip().lstrip('@')
    name = normalize_name(name)
    if name not in list_bookmarks(BOOKMARK_FILE, settings.JOINT_NAMES):
        raise ValueError(f'Unknown bookmark @{name}. Capture or save it first.')
    return name


def _bookmark_replace_confirm(name):
    existing = list_bookmarks(BOOKMARK_FILE, settings.JOINT_NAMES)
    if name not in existing:
        return False
    return input(f'@{name} exists. Update its pose for ALL linked routes? [y/N]: ').strip().lower() in ('y', 'yes')


def go_to_bookmark(name):
    """Explicit single-pose move with the existing playback checks and stop keys."""
    pose = get_bookmark(BOOKMARK_FILE, name, settings.JOINT_NAMES)
    validate_target(pose)
    print('Single-pose move: this does NOT plan a collision-free route.')
    print('Clear the full travel path and support any carried block.')
    if input('Type MOVE to travel directly to this bookmark (else cancel): ').strip() != 'MOVE':
        print('Cancelled; no motor motion.')
        return
    port, handler = connect()
    completed = False
    try:
        with RawTerminal() as terminal:
            measured = read_all(handler)
            move_all_sync(handler, measured, speed=DEFAULT_SPEED,
                          acceleration=DEFAULT_ACCELERATION)
            if stopped(terminal):
                return
            enable_all_torque(handler)
            if stopped(terminal):
                return
            move_for_playback(handler, pose, DEFAULT_SPEED, DEFAULT_ACCELERATION)
            if not wait_move(handler, terminal):
                return
            completed = True
        print('Bookmark reached. Torque remains ON; support the arm.')
    finally:
        try:
            if not completed:
                print('STOP: releasing torque; support arm and blocks.')
                disable_all_torque(handler)
        finally:
            port.closePort()


def bookmark_menu(path=None, data=None):
    """One captured pose, reused as an editable live reference in any motion."""
    while True:
        saved = list_bookmarks(BOOKMARK_FILE, settings.JOINT_NAMES)
        print('\nBOOKMARKS: ' + (', '.join('@' + name for name in saved) or '(none)'))
        print('C Capture current arm pose | G Move to a bookmark | D Delete unused bookmark')
        if path is not None:
            print('S Save a waypoint as bookmark | I Insert @bookmark | R Replace with @bookmark')
        answer = input('Bookmark command, L list, Q back: ').strip().lower()
        if answer == 'q':
            return
        try:
            if answer == 'l':
                for name, pose in saved.items():
                    print(f'@{name}: ' + ' '.join(str(pose[j]) for j in settings.JOINT_NAMES))
            elif answer == 'c':
                name = normalize_name(input('Name this single pose: ').strip())
                exists = name in saved
                if exists and not _bookmark_replace_confirm(name):
                    print('Cancelled; existing bookmark unchanged.')
                    continue
                pose = capture()
                if pose is None:
                    print('Capture cancelled.')
                    continue
                backup = save_bookmark(BOOKMARK_FILE, name, pose,
                                       settings.JOINT_NAMES, overwrite=exists)
                print(f'Saved @{name} (one five-servo pose).' +
                      (f' Backup: {backup.name}' if backup else ''))
            elif answer == 's' and path is not None:
                points = resolve_points(data, wp.parse_point_values,
                                        settings.JOINT_NAMES, BOOKMARK_FILE)
                if not points:
                    raise ValueError('This route has no recorded waypoints yet.')
                position = int(input(f'Save which waypoint? [1..{len(points)}]: '))
                if not 1 <= position <= len(points):
                    raise ValueError('Waypoint number out of range.')
                name = normalize_name(input('Bookmark name: ').strip())
                exists = name in saved
                if exists and not _bookmark_replace_confirm(name):
                    print('Cancelled; bookmark unchanged.')
                    continue
                save_bookmark(BOOKMARK_FILE, name, points[position - 1],
                              settings.JOINT_NAMES, overwrite=exists)
                print(f'Saved waypoint {position} as @{name}.')
                if input(f'Link {path.stem} waypoint {position} to @{name}? [Y/n]: ').strip().lower() in ('', 'y', 'yes'):
                    edited = dict(data, points=list(data['points']))
                    edited['points'][position - 1] = {'bookmark': name}
                    commit(path, edited)
                    data['points'] = edited['points']
                    print(f'Linked waypoint {position} to @{name}.')
            elif answer in ('i', 'r') and path is not None:
                name = _bookmark_choice()
                if answer == 'i':
                    position = int(input(f'Insert BEFORE waypoint number [1..{len(data["points"]) + 1}]: '))
                    if not 1 <= position <= len(data['points']) + 1:
                        raise ValueError('Insertion position out of range.')
                    points = list(data['points'])
                    points.insert(position - 1, {'bookmark': name})
                    verb = 'Inserted'
                else:
                    if not data['points']:
                        raise ValueError('No waypoints to replace. Use I to insert.')
                    position = int(input(f'Replace waypoint number [1..{len(data["points"])}]: '))
                    if not 1 <= position <= len(data['points']):
                        raise ValueError('Waypoint number out of range.')
                    points = list(data['points'])
                    points[position - 1] = {'bookmark': name}
                    verb = 'Replaced'
                edited = dict(data, points=points)
                commit(path, edited)
                data['points'] = points
                print(f'{verb} {path.stem} waypoint {position} with linked @{name}.')
            elif answer == 'g':
                go_to_bookmark(_bookmark_choice())
            elif answer == 'd':
                name = _bookmark_choice()
                if input(f'Delete @{name}? (Linked routes are protected.) [y/N]: ').strip().lower() not in ('y', 'yes'):
                    continue
                delete_bookmark(BOOKMARK_FILE, name, settings.JOINT_NAMES)
                print(f'Deleted unused bookmark @{name}.')
            else:
                print('Unknown bookmark command (or open a sequence for S/I/R).')
        except (ValueError, TypeError, OSError, RuntimeError, TimeoutError) as error:
            print(f'Bookmark error: {error}')




def _editor_link_bookmark(path, data, name, position=None, *, insert=False):
    """Link an existing named pose directly from the live waypoint prompt."""
    name = normalize_name(name)
    # Validate the saved pose before touching the route.
    get_bookmark(BOOKMARK_FILE, name, settings.JOINT_NAMES)
    updated, number, action = edit_bookmark_reference(
        data, name, position, insert=insert)
    commit(path, updated)
    data['points'] = updated['points']
    print(f'{action} @{name} at {path.stem} waypoint {number} (live linked).')


def _editor_capture_bookmark(path, data, name, position=None, *, insert=False):
    """Capture a new named pose and link it in the SAME editor operation."""
    name = normalize_name(name)
    # Check the requested slot before capturing or updating any shared pose.
    updated, number, action = edit_bookmark_reference(
        data, name, position, insert=insert)
    existing = name in list_bookmarks(BOOKMARK_FILE, settings.JOINT_NAMES)
    if existing and not _bookmark_replace_confirm(name):
        print('Cancelled; existing bookmark and route unchanged.')
        return
    print(f'Capture @{name} and {action.lower()} it at waypoint {number}.')
    pose = capture()
    if pose is None:
        print('Capture cancelled; bookmark and route unchanged.')
        return
    validate_raw_pose(pose, settings.JOINT_NAMES)
    backup = save_bookmark(BOOKMARK_FILE, name, pose,
                           settings.JOINT_NAMES, overwrite=existing)
    print(f'Saved @{name}.' + (f' Backup: {backup.name}' if backup else ''))
    # If route saving fails, the named pose is still retained for recovery.
    commit(path, updated)
    data['points'] = updated['points']
    print(f'{action} linked @{name} at {path.stem} waypoint {number}.')


def _editor_promote_waypoint(path, data, number, name):
    """Name an already recorded point and convert its slot to a live link."""
    if not 1 <= number <= len(data['points']):
        raise ValueError('Choose an existing waypoint number to bookmark.')
    name = normalize_name(name)
    points = resolve_points(data, wp.parse_point_values,
                            settings.JOINT_NAMES, BOOKMARK_FILE)
    updated, _, _ = edit_bookmark_reference(data, name, number)
    existing = name in list_bookmarks(BOOKMARK_FILE, settings.JOINT_NAMES)
    if existing and not _bookmark_replace_confirm(name):
        print('Cancelled; existing bookmark and route unchanged.')
        return
    backup = save_bookmark(BOOKMARK_FILE, name, points[number - 1],
                           settings.JOINT_NAMES, overwrite=existing)
    print(f'Saved waypoint {number} as @{name}.' +
          (f' Backup: {backup.name}' if backup else ''))
    commit(path, updated)
    data['points'] = updated['points']
    print(f'Waypoint {number} is now linked to @{name}.')


def _editor_bookmark_command(path, data, choice):
    """Process inline editor bookmark commands; return True when handled."""
    if choice in ('b', 'm'):
        bookmark_menu(path, data)
        return True
    if choice == 'l':
        saved = list_bookmarks(BOOKMARK_FILE, settings.JOINT_NAMES)
        print('Bookmarks: ' + (', '.join('@' + name for name in saved) or '(none yet)'))
        return True
    words = choice.split()
    if words and words[0] == 'b' and len(words) > 1:
        # B safe_left: capture & append.
        # B 2 safe_left: capture & replace #2.
        # B +2 safe_left: capture & insert before #2.
        if len(words) == 2:
            _editor_capture_bookmark(path, data, words[1])
        elif len(words) == 3 and re.fullmatch(r'\+?[1-9]\d*', words[1]):
            _editor_capture_bookmark(
                path, data, words[2], int(words[1]), insert=words[1].startswith('+'))
        else:
            print('Use B name, B 2 name, or B +2 name.')
        return True
    if words and words[0] == 's':
        if len(words) == 3 and re.fullmatch(r'[1-9]\d*', words[1]):
            _editor_promote_waypoint(path, data, int(words[1]), words[2])
        else:
            print('Use S 2 name to save and link existing waypoint #2.')
        return True
    if words and words[0] == 'g':
        if len(words) == 2:
            go_to_bookmark(words[1].lstrip('@'))
        else:
            print('Use G name to move to a bookmark (requires MOVE confirmation).')
        return True
    # @name = append; 2@name = replace #2; +2@name = insert before #2.
    shortcut = re.fullmatch(r'(\+?)([1-9]\d*)?@([a-z][a-z0-9_-]{0,47})', choice)
    if shortcut:
        plus, number, name = shortcut.groups()
        if plus and not number:
            print('Insert requires a waypoint number, such as +2@safe_left.')
        else:
            _editor_link_bookmark(path, data, name,
                                  int(number) if number else None, insert=bool(plus))
        return True
    return False



def record():
    routes = choose()
    answer = input('Sequence number to edit, N new, B bookmarks, Q return: ').strip().lower()
    if answer == 'q':
        return
    if answer == 'b':
        bookmark_menu()
        return
    if answer == 'n':
        name = input('New name (e.g. cell_A1): ').strip()
        if not wp.NAME_PATTERN.fullmatch(name):
            raise ValueError('Invalid sequence name')
        path = ROOT / 'motions' / (name + '.json')
        if path.exists():
            raise ValueError('Name already exists; select it from the list to edit')
        data = dict(schema_version=1, type='pose_sequence', name=name,
                    description='Live taught route', input_type='raw',
                    joint_order=[dict(servo_id=settings.SERVO_IDS[j], joint=j) for j in settings.JOINT_NAMES],
                    created_at=datetime.now().astimezone().isoformat(), points=[])
    else:
        path, _ = routes[int(answer) - 1] if answer.isdigit() and 1 <= int(answer) <= len(routes) else (None, None)
        if path is None:
            raise ValueError('Choose a listed number')
        if json.loads(path.read_text()).get('derived_from'):
            raise ValueError('Attack files are automatic. Edit the matching m or p sequence instead.')
        data = json.loads(path.read_text())
        if data.get('input_type') != 'raw':
            print('ANGLE-format recording. Hand-captured raw ticks avoid the '
                  'app-specific ±90-degree parser during recording.')
            response = input('Enter converts existing points to RAW ticks '
                             '(backup on first save), or Q cancels: ').strip().lower()
            if response == 'q':
                return
            if response:
                raise ValueError('Press Enter to convert or Q to cancel.')
            _, decoded = wp.load_sequence(path)
            original_points = list(data['points'])
            data = convert_recording_to_raw(
                data, decoded, settings.JOINT_NAMES)
            # Keep references linked if an old angle-format file gets converted.
            for point_index, original in enumerate(original_points):
                if isinstance(original, dict) and 'bookmark' in original:
                    data['points'][point_index] = {'bookmark': original['bookmark']}
            print('RAW conversion staged. Original untouched until save; '
                  'first save creates a timestamped backup.')
    while True:
        for i, point in enumerate(data['points'], 1):
            print(f'{i}: @{point["bookmark"]}' if 'bookmark' in point else
                  f'{i}: {point.get("input_values", point.get("positions_raw"))}')
        print('A capture append | # capture replace | B name capture+bookmark+append')
        print('B # name replace | B +# name insert | @name append | #@name replace | +#@name insert')
        print('S # name save existing point as bookmark | L list | G name move | M manage | D remove | Q finish')
        choice = input(f'{path.stem}> ').strip().lower()
        if choice == 'q':
            return
        try:
            if _editor_bookmark_command(path, data, choice):
                continue
        except (ValueError, TypeError, OSError, RuntimeError, TimeoutError) as error:
            print(f'Bookmark error: {error}')
            continue
        if choice == 'd':
            remove_waypoint(path, data)
            continue
        try:
            index = len(data['points']) if choice == 'a' else int(choice) - 1
        except ValueError:
            print('Choose a waypoint number, A, D, or Q.')
            continue
        if not 0 <= index <= len(data['points']) or (choice != 'a' and index == len(data['points'])):
            print('No such waypoint')
            continue
        positions = capture()
        if positions is None:
            continue
        if data['input_type'] == 'raw':
            values = [positions[j] for j in settings.JOINT_NAMES]
            if any(type(v) is not int or not 0 <= v <= 4095 for v in values):
                raise ValueError('Captured servo positions must be raw integers 0..4095.')
        else:
            values = [position_to_angle(j, positions[j])
                      for j in settings.JOINT_NAMES]
        if data['input_type'] == 'raw':
            # Only the raw-register and data-shape checks; do not roundtrip
            # a hand-taught pose through legacy configured ANGLE_LIMITS.
            if len(values) != len(settings.JOINT_NAMES):
                raise ValueError('Captured pose is missing a joint.')
        else:
            wp.parse_point_values(values, data['input_type'])
        point = dict(data['points'][index]) if index < len(data['points']) else {}
        point.pop('positions_raw', None)
        point.pop('bookmark', None)
        point['input_values'] = values
        if index == len(data['points']):
            data['points'].append(point)
        else:
            data['points'][index] = point
        commit(path, data)
        print(f'Saved waypoint {index + 1}. Torque remains OFF.')


def stopped(terminal):
    return terminal.read_key(0.01) in STOP_KEYS


def wait_move(handler, terminal, timeout=15):
    deadline = time.monotonic() + timeout
    while True:
        moving = False
        for joint in settings.JOINT_NAMES:
            if stopped(terminal):
                return False
            state, result, error = handler.ReadMoving(settings.SERVO_IDS[joint])
            check_result(handler, result, error, f'Read {joint} moving')
            moving |= bool(state)
        if not moving:
            return not stopped(terminal)
        if time.monotonic() >= deadline:
            raise TimeoutError('Move exceeded 15 seconds')


def sync_attack(path):
    """Materialize exactly M1, M2, P2 from the latest saved source values."""
    paired = path.with_name(path.stem[:-1] + 'p.json')
    _, m = wp.load_sequence(path)
    _, p = wp.load_sequence(paired)
    target_path = ROOT / 'motions' / (path.stem[:-1] + 'a.json')
    target_path.parent.mkdir(exist_ok=True)
    data = dict(schema_version=1, type='pose_sequence', name=target_path.stem,
                description=f'Auto attack: {path.stem}[1,2] + {paired.stem}[2]',
                input_type='raw',
                joint_order=[dict(servo_id=settings.SERVO_IDS[j], joint=j)
                             for j in settings.JOINT_NAMES],
                derived_from=dict(m=path.name, p=paired.name),
                points=[dict(input_values=[point[j] for j in settings.JOINT_NAMES])
                        for point in (m[0], m[1], p[1])])
    for point in (m[0], m[1], p[1]):
        validate_competition_target(point)
    try:
        old = json.loads(target_path.read_text())
    except (OSError, json.JSONDecodeError):
        old = None
    if old != data:
        wp.save_sequence(target_path, data)
        print(f'Updated {target_path.name}: M1 -> M2 -> P2')
    return target_path


def refresh_attacks():
    count = 0
    for path in sorted((ROOT / 'motions').glob('*m.json')):
        if path.with_name(path.stem[:-1] + 'p.json').exists():
            try:
                sync_attack(path)
                count += 1
            except (ValueError, OSError, RuntimeError) as error:
                print(f'Cannot update {path.stem[:-1]}a: {error}')
    return count


def attack_points(path):
    return wp.load_sequence(sync_attack(path))[1]


def validate_target(positions):
    """Diagnose invalid poses without altering motor limits or enabling torque."""
    for joint, value in positions.items():
        angle = position_to_angle(joint, value)
        lo, hi = settings.ANGLE_LIMITS[joint]
        if not lo <= angle <= hi:
            raise ValueError(
                f'{joint}: raw={value}, computed_angle={angle:.2f} deg, '
                f'allowed={lo}..{hi} deg (check this saved waypoint or '
                'arm_settings.py calibration; safety limits unchanged)')


def validate_competition_target(positions):
    """Validate raw register data for competition playback.

    A saved raw waypoint is not reinterpreted through legacy ANGLE_LIMITS,
    which may not match the actual joint calibration. Keep exact joint names,
    integer servo range checks, movement timeouts and operator stop handling.
    NOTE: This alone does NOT confirm physical joint clearance or safe travel.
    """
    validate_raw_pose(positions, settings.JOINT_NAMES)


def live_attack_targets(path):
    """Refresh the separate attack file before each of its three steps."""
    for index in range(3):
        attack = sync_attack(path)
        _, current = wp.load_sequence(attack)
        yield f'{attack.stem} waypoint {index + 1}/3', current[index]


def placement_targets(path):
    """Complete the selected square's current move, then its current place."""
    paired = path.with_name(path.stem[:-1] + 'p.json')
    for route in (path, paired):
        index = 0
        while True:
            _, points = wp.load_sequence(route)
            if index >= len(points):
                break
            validate_target(points[index])
            yield f'{route.stem} waypoint {index + 1}', points[index]
            index += 1


def cell_routes():
    return [p for p, _ in wp.find_sequences()
            if re.fullmatch(r'c[0-9]+r[0-9]+m', p.stem)
            and p.with_name(p.stem[:-1] + 'p.json').exists()]


def make_plan(paths, mode):
    plan = [(path, random.choice(('place', 'attack')) if mode == 'mixed' else mode)
            for path in paths]
    random.shuffle(plan)
    return plan


def move_for_playback(handler, target, speed, acceleration):
    """Use custom arm speed without accidentally accelerating the gripper."""
    speed, acceleration = validate_playback_settings(speed, acceleration)
    if (speed, acceleration) == (DEFAULT_SPEED, DEFAULT_ACCELERATION):
        move_all_sync(handler, target, speed=DEFAULT_SPEED,
                      acceleration=DEFAULT_ACCELERATION)
    else:
        competition.sync_targets(handler, settings, check_result, target,
                                 (speed, acceleration), gripper_profile="normal")


def test_grid(attack=False, auto=False):
    refresh_attacks()
    routes = cell_routes()
    if not routes:
        print('No matching c#r#m.json / c#r#p.json pairs in motions.')
        return
    for i, path in enumerate(routes, 1):
        print(f'{i}: {path.stem[:-1]}')
    answer = input('Square number for attack (Q cancels): ' if attack else
                   'Square numbers, or Enter for ALL (Q cancels): ').strip().lower()
    if answer == 'q':
        return
    numbers = list(range(1, len(routes) + 1)) if not answer and not attack else list(dict.fromkeys(int(s) for s in answer.split()))
    if not numbers or any(n < 1 or n > len(routes) for n in numbers):
        raise ValueError('Choose listed square numbers')
    if attack and len(numbers) != 1:
        raise ValueError('Choose one square')
    mode = 'attack'
    if not attack:
        choice = input('1 Move + place  2 Attack  3 Random mix [Enter=3]: ').strip()
        if choice not in ('', '1', '2', '3'):
            raise ValueError('Choose 1, 2, or 3')
        mode = {'1': 'place', '2': 'attack', '3': 'mixed', '': 'mixed'}[choice]
    batch = make_plan([routes[n - 1] for n in numbers], mode)
    # Validate the entire initial plan before opening the motor connection.
    for path, action in batch:
        targets = live_attack_targets(path) if action == 'attack' else placement_targets(path)
        for _, target in targets:
            validate_target(target)
    speed, acceleration = prompt_playback_settings()
    print('Order: ' + ', '.join(f'{p.stem[:-1]} {action}' for p, action in batch))
    print('One visit per square. Provide blocks for placement; set up attack squares as needed.')
    print('No camera/block detection. ' +
          ('AUTO: no per-square readiness pause. ' if auto else 'Enter starts each square. ') +
          'Space/Q/Esc/Ctrl+C releases torque.')
    port, handler = connect()
    completed = False
    try:
        with RawTerminal() as terminal:
            for path, action in batch:
                if auto:
                    print(f'\r\n{path.stem[:-1]} {action}: AUTO START (no readiness pause).', flush=True)
                else:
                    print(f'\r\n{path.stem[:-1]} {action}: clear/setup workspace, then ENTER. Stop = Space/Q/Esc/Ctrl+C.', flush=True)
                    while True:
                        key = terminal.read_key(0.05)
                        if key in STOP_KEYS:
                            return
                        if key in ('\r', '\n'):
                            break
                if stopped(terminal):
                    return
                # Seed the measured pose before enabling torque.
                current = read_all(handler)
                move_all_sync(handler, current, speed=DEFAULT_SPEED,
                              acceleration=DEFAULT_ACCELERATION)
                if stopped(terminal):
                    return
                enable_all_torque(handler)
                targets = live_attack_targets(path) if action == 'attack' else placement_targets(path)
                for label, positions in targets:
                    if stopped(terminal):
                        return
                    print(f'\r\n{label}', flush=True)
                    move_for_playback(handler, positions, speed, acceleration)
                    if not wait_move(handler, terminal):
                        return
            completed = True
        print('\nFinished. Torque remains ON to hold the final pose.')
    finally:
        try:
            if not completed:
                print('\nSTOP: releasing torque; support the arm.', flush=True)
                disable_all_torque(handler)
        finally:
            port.closePort()


def run_saved_sequence(name, *, auto=False, dry_run=False,
                       speed=DEFAULT_SPEED, acceleration=DEFAULT_ACCELERATION,
                       high_speed_confirmed=False):
    """Play any saved sequence, including a newly recorded one.

    --auto skips the initial confirmation. Stops remain available at all times.
    This validates servo angle limits, not collisions or block readiness.
    """
    speed, acceleration = validate_playback_settings(speed, acceleration)
    if name.startswith('@'):
        # Bookmarks are single reusable poses, not two-point motion files.
        # Keep the explicit MOVE confirmation and existing stop handling.
        bookmark_name = normalize_name(name[1:])
        if (speed, acceleration) != (DEFAULT_SPEED, DEFAULT_ACCELERATION):
            raise ValueError('Single-bookmark goto uses normal speed/acceleration only.')
        pose = get_bookmark(BOOKMARK_FILE, bookmark_name, settings.JOINT_NAMES)
        validate_target(pose)
        if dry_run:
            print(f'Bookmark @{bookmark_name}:')
            print(' '.join(f'{j}={pose[j]}' for j in settings.JOINT_NAMES))
            print('DRY RUN: one pose, no arm connection. Travel clearance is NOT checked.')
            return
        go_to_bookmark(bookmark_name)
        return
    refresh_attacks()
    matches = [(path, data) for path, data in wp.find_sequences()
               if path.stem.lower() == name.lower()]
    if len(matches) != 1:
        raise ValueError(f'Unknown/ambiguous saved sequence: {name!r}')
    path, _ = matches[0]
    _, initial_points = wp.load_sequence(path)
    if not initial_points:
        raise ValueError('Sequence has no waypoints')
    for positions in initial_points:
        validate_target(positions)
    print(f'Sequence: {path.stem} ({len(initial_points)} waypoints)')
    print(f'Playback speed={speed}, acceleration={acceleration} (all waypoints)')
    print('Angle limits checked; collision clearance and block readiness are NOT checked.')
    if dry_run:
        for i, positions in enumerate(initial_points, 1):
            print(f'{i}: ' + ' '.join(f'{joint}={positions[joint]}'
                                     for joint in settings.JOINT_NAMES))
        print('DRY RUN: no connection to the robot, no motion.')
        return
    if (speed > 600 or acceleration > 35) and not high_speed_confirmed:
        print('WARNING: High-speed motion may shift the arm or cause collisions.')
        if input('Type CONFIRM to run at this speed: ').strip() != 'CONFIRM':
            print('Playback cancelled.')
            return
    if not auto:
        ready = input('Clear the workspace and support the arm. Enter to START, Q to cancel: ')
        if ready.strip().lower() == 'q':
            return
    else:
        print('AUTO START requested. No additional Enter prompts.')
    print('Stop = Space/Q/Esc/Ctrl+C (software torque release).')
    port, handler = connect()
    completed = False
    try:
        with RawTerminal() as terminal:
            current = read_all(handler)
            move_all_sync(handler, current, speed=DEFAULT_SPEED,
                              acceleration=DEFAULT_ACCELERATION)
            if stopped(terminal):
                return
            enable_all_torque(handler)
            for i in range(len(initial_points)):
                if stopped(terminal):
                    return
                # Read again before each move, retaining edits made on disk.
                if path.stem.endswith('a') and json.loads(path.read_text()).get('derived_from'):
                    source = path.with_name(path.stem[:-1] + 'm.json')
                    sync_attack(source)
                _, points = wp.load_sequence(path)
                if i >= len(points):
                    raise ValueError('Sequence shortened during playback')
                positions = points[i]
                validate_target(positions)
                print(f'\r\n{path.stem}: waypoint {i + 1}/{len(initial_points)}', flush=True)
                move_for_playback(handler, positions, speed, acceleration)
                if not wait_move(handler, terminal):
                    return
            completed = True
        print('\nFinished. Torque remains ON to hold the final pose.')
    finally:
        try:
            if not completed:
                print('\nSTOP: releasing torque; support the arm.', flush=True)
                disable_all_torque(handler)
        finally:
            port.closePort()

def competition_placements():
    """Universal queue: any validated saved motion, plus combined square macros."""
    refresh_attacks()
    return competition.menu(
        wp.find_sequences(), wp.load_sequence, validate_competition_target,
        connect=connect, read_all=read_all,
        enable_all_torque=enable_all_torque,
        disable_all_torque=disable_all_torque, terminal_class=RawTerminal,
        settings=settings, check_result=check_result,
        bookmarks=list_bookmarks(BOOKMARK_FILE, settings.JOINT_NAMES))


def copy_current_values():
    """Print all measured servo values without changing torque or recordings."""
    port, handler = connect()
    try:
        positions = read_all(handler)
    finally:
        port.closePort()
    print('Joint order: ' + ' '.join(settings.JOINT_NAMES))
    print('Raw: ' + ' '.join(str(positions[j]) for j in settings.JOINT_NAMES))
    print('Angles: ' + ' '.join(f'{position_to_angle(j, positions[j]):.6f}' for j in settings.JOINT_NAMES))
    print('Use option 1 to write all five measured values directly into a chosen waypoint.')


def main():
    parser = argparse.ArgumentParser(description='Live robot waypoint editor and playback')
    parser.add_argument('--sequence', metavar='NAME',
                        help='play a saved sequence, including a newly recorded one')
    parser.add_argument('--auto', action='store_true',
                        help='with --sequence, skip readiness prompts')
    parser.add_argument('--dry-run', action='store_true',
                        help='with --sequence, validate and print poses without connecting')
    parser.add_argument('--speed', type=int,
                        help='with --sequence, servo playback speed (50..1200, default 300)')
    parser.add_argument('--acceleration', type=int,
                        help='with --sequence, servo playback acceleration (1..60, default 20)')
    args = parser.parse_args()
    if args.sequence:
        try:
            run_saved_sequence(args.sequence, auto=args.auto, dry_run=args.dry_run,
                               speed=args.speed if args.speed is not None else DEFAULT_SPEED,
                               acceleration=(args.acceleration if args.acceleration is not None
                                             else DEFAULT_ACCELERATION))
        except ValueError as error:
            parser.error(str(error))
        return
    if args.auto or args.dry_run or args.speed is not None or args.acceleration is not None:
        parser.error('--auto, --dry-run, --speed and --acceleration require --sequence NAME')
    refresh_attacks()
    while True:
        answer = input('\n1 Live record/replace/remove  2 Random squares  3 Attack square  4 Sync ALL attacks  5 Copy current values  6 Auto grid  7 Run saved sequence  8 Quick COMP macros  9 Live square sweep  10 Old COMP / Joy-Con  Q Quit: ').strip().lower()
        try:
            if answer == 'q':
                return
            if answer == '1':
                record()
            elif answer == '2':
                test_grid()
            elif answer == '3':
                test_grid(attack=True)
            elif answer == '4':
                count = refresh_attacks()
                print(f'Synced {count} attack files from current local M/P values.')
            elif answer == '5':
                copy_current_values()
            elif answer == '6':
                test_grid(auto=True)
            elif answer == '8':
                from competition_quick import launch
                launch()
            elif answer == '10':
                competition_placements()
            elif answer == '9':
                spec_sweep = importlib.util.spec_from_file_location(
                    'arm_live_sweep', ROOT / 'scripts/06_live_square_sweep.py')
                sweep = importlib.util.module_from_spec(spec_sweep)
                spec_sweep.loader.exec_module(sweep)
                sweep.menu()
            elif answer == '7':
                routes = choose()
                selected = input('Saved sequence number to run automatically, Q to cancel: ').strip().lower()
                if selected == 'q':
                    continue
                if not selected.isdigit() or not 1 <= int(selected) <= len(routes):
                    raise ValueError('Choose a listed sequence number')
                speed, acceleration = prompt_playback_settings()
                run_saved_sequence(routes[int(selected) - 1][0].stem, auto=True,
                                   speed=speed, acceleration=acceleration,
                                   high_speed_confirmed=True)
        except (ValueError, IndexError, OSError, RuntimeError, TimeoutError) as error:
            print(f'Error: {error}')


if __name__ == '__main__':
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print('\nTool stopped.')

