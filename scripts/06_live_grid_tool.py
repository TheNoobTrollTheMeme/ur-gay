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
import competition_mode as competition

spec = importlib.util.spec_from_file_location('waypoints', ROOT / 'scripts/05_waypoint_sequence.py')
wp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wp)
STOP_KEYS = {' ', 'q', '\x03', '\x1b'}


def choose():
    refresh_attacks()
    routes = wp.find_sequences()
    for i, (path, data) in enumerate(routes, 1):
        print(f'{i}: {path.stem} ({len(data.get("points", []))} points)')
    return routes


def commit(path, data):
    # Validate everything before touching an existing recording.
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
    print(f'Waypoint {index + 1}: {point.get("input_values", point.get("positions_raw"))}')
    confirm = input(f'Remove waypoint {index + 1} from {path.stem}? [y/N]: ').strip().lower()
    if confirm not in ('y', 'yes'):
        print('Removal cancelled.')
        return
    updated = dict(data, points=points[:index] + points[index + 1:])
    commit(path, updated)
    data['points'] = updated['points']
    print(f'Removed waypoint {index + 1}. {len(data["points"])} remain; following points are renumbered.')


def record():
    routes = choose()
    answer = input('Sequence number to edit, N for new, Q to return: ').strip().lower()
    if answer == 'q':
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
    while True:
        for i, point in enumerate(data['points'], 1):
            print(f'{i}: {point.get("input_values", point.get("positions_raw"))}')
        choice = input('Waypoint number to REPLACE, A to append, D to remove, Q to finish: ').strip().lower()
        if choice == 'q':
            return
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
        values = [positions[j] if data['input_type'] == 'raw' else position_to_angle(j, positions[j])
                  for j in settings.JOINT_NAMES]
        wp.parse_point_values(values, data['input_type'])
        point = dict(data['points'][index]) if index < len(data['points']) else {}
        point.pop('positions_raw', None)
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
        validate_target(point)
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
    for joint, value in positions.items():
        angle = position_to_angle(joint, value)
        lo, hi = settings.ANGLE_LIMITS[joint]
        if not lo <= angle <= hi:
            raise ValueError(f'{joint} exceeds configured angle limits')


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
                move_all_sync(handler, current, speed=300, acceleration=20)
                if stopped(terminal):
                    return
                enable_all_torque(handler)
                targets = live_attack_targets(path) if action == 'attack' else placement_targets(path)
                for label, positions in targets:
                    if stopped(terminal):
                        return
                    print(f'\r\n{label}', flush=True)
                    move_all_sync(handler, positions, speed=300, acceleration=20)
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


def run_saved_sequence(name, *, auto=False, dry_run=False):
    """Play any saved sequence, including a newly recorded one.

    --auto skips the initial confirmation. Stops remain available at all times.
    This validates servo angle limits, not collisions or block readiness.
    """
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
    print('Angle limits checked; collision clearance and block readiness are NOT checked.')
    if dry_run:
        for i, positions in enumerate(initial_points, 1):
            print(f'{i}: ' + ' '.join(f'{joint}={positions[joint]}'
                                     for joint in settings.JOINT_NAMES))
        print('DRY RUN: no connection to the robot, no motion.')
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
            move_all_sync(handler, current, speed=300, acceleration=20)
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
                move_all_sync(handler, positions, speed=300, acceleration=20)
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
    """Precompute a place-only route for responsive, bounded replay."""
    routes = cell_routes()
    if not routes:
        print('No matching c#r#m / c#r#p saved route pairs.')
        return
    for i, path in enumerate(routes, 1):
        print(f'{i}: {path.stem[:-1]}')
    selected = input('Competition squares (e.g. 1 2), Enter=ALL, Q=cancel: ').strip().lower()
    if selected == 'q':
        return
    try:
        nums = list(range(1, len(routes) + 1)) if not selected else list(dict.fromkeys(int(x) for x in selected.split()))
    except ValueError as error:
        raise ValueError('Choose listed square numbers separated by spaces') from error
    if not nums or any(n < 1 or n > len(routes) for n in nums):
        raise ValueError('Choose listed square numbers')
    # Snapshot/validate all poses before opening serial or enabling motors.
    steps = competition.prepare_placements([routes[n - 1] for n in nums],
                                            wp.load_sequence, validate_target)
    continuous = sum(competition.can_look_ahead(steps[i - 1], steps[i], steps[i + 1],
                                                settings.JOINT_NAMES)
                     for i in range(1, len(steps) - 1))
    print(f'{len(steps)} stored targets, {continuous} eligible for near-point transitions.')
    print('Place-only, no attack/flick moves. Speed/acceleration remain at 300/20.')
    print('No pauses between squares; gripper and direction changes stop at exact waypoints.')
    print('No collision checking. Confirm every transition is clear, and support the arm.')
    answer = input('P = preview targets, type RUN to arm, Q = cancel: ').strip().lower()
    if answer == 'p':
        for step in steps:
            print(f'{step.route} point {step.number}: {step.target}')
        return
    if answer != 'run':
        print('Competition cancelled.')
        return
    print('Space/Q/Esc/Ctrl+C stops and releases torque. Keep physical power stop accessible.')
    competition.execute(steps, connect=connect, read_all=read_all,
                        move_all_sync=move_all_sync, enable_all_torque=enable_all_torque,
                        disable_all_torque=disable_all_torque, terminal_class=RawTerminal,
                        settings=settings, check_result=check_result, stopped=stopped,
                        wait_move=wait_move)


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
    args = parser.parse_args()
    if args.sequence:
        run_saved_sequence(args.sequence, auto=args.auto, dry_run=args.dry_run)
        return
    if args.auto or args.dry_run:
        parser.error('--auto and --dry-run require --sequence NAME')
    refresh_attacks()
    while True:
        answer = input('\n1 Live record/replace/remove  2 Random squares  3 Attack square  4 Sync ALL attacks  5 Copy current values  6 Auto grid  7 Run saved sequence  8 Competition (place-only)  Q Quit: ').strip().lower()
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
                competition_placements()
            elif answer == '7':
                routes = choose()
                selected = input('Saved sequence number to run automatically, Q to cancel: ').strip().lower()
                if selected == 'q':
                    continue
                if not selected.isdigit() or not 1 <= int(selected) <= len(routes):
                    raise ValueError('Choose a listed sequence number')
                run_saved_sequence(routes[int(selected) - 1][0].stem, auto=True)
        except (ValueError, IndexError, OSError, RuntimeError, TimeoutError) as error:
            print(f'Error: {error}')


if __name__ == '__main__':
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print('\nTool stopped.')

