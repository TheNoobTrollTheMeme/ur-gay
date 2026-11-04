#!/usr/bin/env python3
"""Live waypoint editor and randomized playback of taught placement routes."""
import importlib.util
import json
import random
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
        choice = input('Waypoint number to REPLACE, A to append, Q to finish: ').strip().lower()
        if choice == 'q':
            return
        index = len(data['points']) if choice == 'a' else int(choice) - 1
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
    target_path = path.with_name(path.stem[:-1] + 'a.json')
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
    if not target_path.exists() or json.loads(target_path.read_text()) != data:
        wp.save_sequence(target_path, data)
        print(f'Updated {target_path.name}: M1 -> M2 -> P2')
    return target_path


def refresh_attacks():
    for path in sorted((ROOT / 'motions').glob('*m.json')):
        if path.with_name(path.stem[:-1] + 'p.json').exists():
            sync_attack(path)


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


def test_grid(attack=False):
    if attack:
        refresh_attacks()
        routes = [(p, d) for p, d in wp.find_sequences() if p.stem.endswith('m')]
        for i, (path, _) in enumerate(routes, 1):
            print(f'{i}: {path.stem} -> {path.stem[:-1]}p waypoint 2')
    else:
        routes = choose()
    answer = input('Select ONE m sequence (Q cancels): ' if attack else 'Numbers of COMPLETE cell placement routes, separated by spaces (Q cancels): ').strip()
    if answer.lower() == 'q':
        return
    numbers = list(dict.fromkeys(int(s) for s in answer.split()))
    if not numbers or any(n < 1 or n > len(routes) for n in numbers):
        raise ValueError('Choose listed route numbers')
    if attack and len(numbers) != 1:
        raise ValueError('Choose one attack sequence')
    batch = []
    for n in numbers:
        path = routes[n - 1][0]
        points = attack_points(path) if attack else wp.load_sequence(path)[1]
        # Each selected file must teach its full collision-checked route.
        for positions in points:
            for joint, value in positions.items():
                angle = position_to_angle(joint, value)
                lo, hi = settings.ANGLE_LIMITS[joint]
                if not lo <= angle <= hi:
                    raise ValueError(f'{path.name}: {joint} exceeds configured angle limits')
        batch.append((path, points))
    print('ATTACK file: exactly M waypoint 1, M waypoint 2, then P waypoint 2 (including gripper).' if attack else 'Each route must include approach, pickup, lift, cell approach, release and safe return.')
    print('Routes must use distinct empty cells. This tool has no camera or block detection.')
    if input('Type READY to confirm these routes are taught and the workspace is clear: ').strip() != 'READY':
        return
    random.shuffle(batch)
    print('Random order: ' + ', '.join(p.stem for p, _ in batch))
    print('One pass only. Verify the withdrawal path is clear.' if attack else 'One pass only; no repeated cells. Refill pickup blocks between routes.')
    port, handler = connect()
    completed = False
    try:
        with RawTerminal() as terminal:
            if stopped(terminal):
                return
            # Seed the current pose before enabling torque to avoid a stale target jump.
            current = read_all(handler)
            move_all_sync(handler, current, speed=300, acceleration=20)
            if stopped(terminal):
                return
            enable_all_torque(handler)
            for path, points in batch:
                print(f'\r\nNext: {path.stem}. Refill pickup/clear cell; R starts. Space/Q/Ctrl+C stops.', flush=True)
                while True:
                    key = terminal.read_key(0.05)
                    if key in STOP_KEYS:
                        return
                    if key == 'r':
                        break
                targets = live_attack_targets(path) if attack else (
                    (f'{path.stem}: waypoint {i}/{len(points)}', point)
                    for i, point in enumerate(points, 1)
                )
                for label, positions in targets:
                    if stopped(terminal):
                        return
                    print(f'\r\n{label}', flush=True)
                    move_all_sync(handler, positions, speed=300, acceleration=20)
                    if not wait_move(handler, terminal):
                        return
            completed = True
        print('\nBatch finished. Torque remains ON to hold the final pose.')
    finally:
        try:
            if not completed:
                print('\nSTOP: releasing torque. Support the arm; it may sag.', flush=True)
                disable_all_torque(handler)
        finally:
            port.closePort()


def main():
    refresh_attacks()
    while True:
        answer = input('\n1 Live record/replace  2 Random grid placement test  3 LIVE attack + withdraw  Q Quit: ').strip().lower()
        try:
            if answer == 'q':
                return
            if answer == '1':
                record()
            elif answer == '2':
                test_grid()
            elif answer == '3':
                test_grid(attack=True)
        except (ValueError, IndexError, OSError, RuntimeError, TimeoutError) as error:
            print(f'Error: {error}')


if __name__ == '__main__':
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print('\nTool stopped.')
