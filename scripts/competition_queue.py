"""Editable pre-input queue for all validated STS3215 waypoint recordings.

Accepts individual move/place/grab/custom/derived-attack recordings, plus
optional square macros. No arbitrary Python execution, force-limit bypass
or firmware overclock. Only pending actions are editable during playback.
"""
from collections import deque
from dataclasses import dataclass
from pathlib import Path
import re
import time

# Conservative experimental profiles, not measured safe limits. The ST3215
# documentation demonstrates larger registers, but this program does not
# assume that the assembled arm has the same mechanical limits as the servo.
PROFILES = {'normal': (300, 20), 'fast': (600, 35), 'turbo': (1200, 60)}
# Explicitly opt-in to increased gripper movement speed; default stays at 300/20.
# These are servo command registers, not demonstrated safe mechanical speeds.
GRIPPER_PROFILES = dict(PROFILES)
NEAR_TICKS = 8
TIMEOUT_SECONDS = 15.0
STOP_KEYS = {' ', 'q', 'Q', '\x1b', '\x03'}


@dataclass(frozen=True)
class Point:
    route: str
    number: int
    target: dict


class QueuePlan:
    """Change waiting motions without altering the one currently executing."""
    def __init__(self, catalog, initial=(), *, aliases=None, labels=None):
        self.catalog = dict(catalog)
        self.aliases = dict(aliases or {})
        self.labels = dict(labels or {})
        self.pending = deque()
        for action in initial:
            self.append(action)

    def _check(self, action):
        action = str(action).strip().lower()
        resolved = action if action in self.catalog else self.aliases.get(action)
        if resolved not in self.catalog:
            raise ValueError(f'Unavailable motion {action!r}. Use a READY name or ID.')
        return resolved

    def name(self, identifier):
        return self.labels.get(identifier, identifier)

    def append(self, action):
        self.pending.append(self._check(action))

    def insert(self, index, action):
        index = int(index)
        if not 1 <= index <= len(self.pending) + 1:
            raise ValueError('Insert index must be between 1 and pending length + 1.')
        self.pending.insert(index - 1, self._check(action))

    def delete(self, index):
        index = int(index)
        if not 1 <= index <= len(self.pending):
            raise ValueError('Delete index must be a pending queue position.')
        value = self.pending[index - 1]
        del self.pending[index - 1]
        return value

    def move(self, index, destination):
        index, destination = int(index), int(destination)
        length = len(self.pending)
        if not 1 <= index <= length or not 1 <= destination <= length:
            raise ValueError('Move indices must refer to pending positions.')
        action = self.delete(index)
        self.pending.insert(destination - 1, action)

    def clear(self):
        self.pending.clear()

    def pop(self):
        return self.pending.popleft()

    def show(self, active=None):
        items = (' '.join(f'{index}:{self.name(identifier)}'
                          for index, identifier in enumerate(self.pending, 1))
                 or '(empty)')
        active_name = self.name(active) if active else 'idle'
        return f'Active: {active_name} | Pending: {items}'

    def edit(self, command):
        """Commands work by saved motion name or stable menu number."""
        words = command.strip().lower().split()
        if not words:
            return self.show()
        op, *args = words
        if op in ('l', 'ls', 'list') and not args:
            return self.show()
        if op in ('a', 'add') and len(args) == 1:
            self.append(args[0])
            return self.show()
        if op in ('i', 'insert') and len(args) == 2:
            self.insert(args[0], args[1])
            return self.show()
        if op in ('d', 'del', 'delete') and len(args) == 1:
            removed = self.delete(args[0])
            return f'Removed pending motion {self.name(removed)}. {self.show()}'
        if op in ('m', 'move') and len(args) == 2:
            self.move(args[0], args[1])
            return self.show()
        if op == 'clear' and not args:
            self.clear()
            return self.show()
        raise ValueError('Commands: a grab1, i 1 grab2, d 1, m 2 1, clear, l')


def build_catalog(routes, load_sequence, validate_target, *,
                  skip_invalid=False, report=print):
    """Preflight independently; one invalid square must not block the others.

    A disabled square never enters the queue catalog and cannot be commanded.
    Preserve its original menu number so queue inputs cannot silently refer
    to a different square. The default retains strict fail-fast semantics for
    callers and offline tests.
    """
    catalog = {}
    for index, move_path in enumerate(routes, 1):
        steps = []
        path = move_path
        number = None
        try:
            if not move_path.stem.endswith('m'):
                raise ValueError('Only move/place route pairs can be queued.')
            place_path = move_path.with_name(move_path.stem[:-1] + 'p.json')
            for path in (move_path, place_path):
                number = None
                _, points = load_sequence(path)
                if not points:
                    raise ValueError('Sequence has no waypoints.')
                for number, point in enumerate(points, 1):
                    validate_target(point)
                    steps.append(Point(path.stem, number, dict(point)))
        except (ValueError, RuntimeError, OSError, KeyError, IndexError, TypeError) as error:
            detail = (f'{path.stem} waypoint {number}: {error}'
                      if number is not None else f'{path.stem}: {error}')
            if not skip_invalid:
                raise ValueError(detail) from error
            report(f'DISABLED square {index} ({move_path.stem[:-1]}): {detail}')
            continue
        catalog[str(index)] = tuple(steps)
    return catalog


# Legacy description text is informational, not a live motion validation result.
# Locally re-taught waypoints may retain an outdated imported description.

def build_motion_catalog(routes, load_sequence, validate_target, *, report=print):
    """Discover all saved waypoint files, with stable IDs and named aliases.

    A recording with bad points is unavailable, not fatal for all other motions.
    Names and IDs remain stable despite invalid recordings. Move/place macros
    are extra entries (e.g. c1r1) rather than replacements for m/p/a variants.
    """
    catalog, labels, aliases = {}, {}, {}
    stem_ids = {}
    duplicated = set()
    for index, (path, data) in enumerate(routes, 1):
        identifier = str(index)
        name = path.stem.lower()
        point_number = None
        try:
            if path.suffix.lower() != '.json':
                raise ValueError('Not a saved JSON waypoint sequence')
            _, points = load_sequence(path)
            if not points:
                raise ValueError('Sequence has no waypoints')
            steps = []
            for point_number, point in enumerate(points, 1):
                validate_target(point)
                steps.append(Point(path.stem, point_number, dict(point)))
        except (ValueError, RuntimeError, OSError, KeyError, IndexError, TypeError) as error:
            loc = f' waypoint {point_number}' if point_number is not None else ''
            report(f'DISABLED motion {identifier} ({path.stem}{loc}): {error}')
            continue
        catalog[identifier] = tuple(steps)
        labels[identifier] = path.stem
        if name in aliases:
            duplicated.add(name)
        else:
            aliases[name] = identifier
        stem_ids[name] = identifier
    for name in duplicated:
        aliases.pop(name, None)
        report(f'Ambiguous motion name {name!r}. Queue by numeric ID.')

    next_id = len(routes) + 1
    for name, move_id in tuple(stem_ids.items()):
        if not re.fullmatch(r'c[1-9]\d*r[1-9]\d*m', name):
            continue
        base = name[:-1]
        place_id = stem_ids.get(base + 'p')
        if place_id is None or move_id not in catalog or place_id not in catalog:
            continue
        if base in aliases:
            report(f'Macro {base!r} skipped: conflicting recorded name.')
            continue
        identifier = str(next_id)
        next_id += 1
        catalog[identifier] = catalog[move_id] + catalog[place_id]
        labels[identifier] = base
        aliases[base] = identifier
    return catalog, labels, aliases


def eligible_blend(previous, current, following, names):
    """Never skip precise grip events, direction changes, or file boundaries."""
    if not (previous.route == current.route == following.route):
        return False
    if any(item.target['gripper'] != current.target['gripper']
           for item in (previous, following)):
        return False
    for name in names:
        a = current.target[name] - previous.target[name]
        b = following.target[name] - current.target[name]
        if a * b < 0:
            return False
    return True


class KeyInput:
    """Raw-key editor. Space/Esc/Ctrl+C always stop, even mid-command."""
    def __init__(self):
        self.command = None

    @staticmethod
    def translate(command):
        command = command.strip().lower()
        if command.startswith('a=') and command[2:]:
            return 'a ' + command[2:]
        match = re.fullmatch(r'a([a-z0-9_.-]+)', command)
        if match:
            return 'a ' + match[1]
        match = re.fullmatch(r'd([1-9]\d*)', command)
        if match:
            return 'd ' + match[1]
        match = re.fullmatch(r'(m|i)([1-9]\d*),([a-z0-9_.-]+)', command)
        if match:
            return match[1] + ' ' + match[2] + ' ' + match[3]
        return command

    def feed(self, key):
        if not key:
            return None
        # Allow lowercase q in saved names after ":" has opened the editor.
        if key in STOP_KEYS and not (key == 'q' and self.command is not None):
            return ('stop', None)
        if self.command is not None:
            if key in ('\r', '\n'):
                done = self.translate(self.command)
                self.command = None
                return ('edit', done)
            if key in ('\x7f', '\x08'):
                self.command = self.command[:-1]
            elif key.isprintable() and len(self.command) < 80:
                self.command += key
            return None
        if key == ':':
            self.command = ''
            return ('prompt', 'Live edit: :a=grab1 :a12 :i1,grab2 :d1 :m2,1 :clear. '
                    'Enter confirms. SPACE/Esc=STOP.')
        if key in '123456789':
            return ('add', key)
        if key in ('l', 'L', '?'):
            return ('list', None)
        if key in ('e', 'E'):
            return ('end', None)
        if key in ('\x7f', '\x08'):
            return ('undo', None)
        return None


class InputManager:
    def __init__(self, plan, reporter=print, *, gamepad=None):
        self.plan = plan
        self.gamepad = gamepad
        self.keys = KeyInput()
        self.report = reporter
        self.stopped = False
        self.end_after_queue = False
        self.active = None

    def handle(self, key):
        event = self.keys.feed(key)
        if not event:
            return
        action, value = event
        if action == 'stop':
            self.stopped = True
            return
        if action == 'prompt':
            self.report(value)
            return
        if action == 'end':
            self.end_after_queue = True
            self.report('Finish the pending queue, then exit; new entries may still be added.')
            return
        try:
            if action == 'add':
                self.plan.append(value)
            elif action == 'undo':
                if self.plan.pending:
                    self.plan.delete(len(self.plan.pending))
            elif action == 'edit':
                self.report(self.plan.edit(value))
                return
            # list/undo/add all show the updated queue.
            self.report(self.plan.show(self.active))
        except (ValueError, IndexError) as error:
            self.report(f'Queue edit ignored: {error}')

    def poll(self, terminal):
        # Gamepad events and keyboard stop are handled in the existing servo loop.
        # A dropped Bluetooth connection fails closed rather than replaying input.
        if self.gamepad is not None:
            try:
                for action, value in self.gamepad.poll():
                    if action == 'stop':
                        self.stopped = True
                        break
                    if action == 'notice':
                        self.report(value)
                    elif action == 'add':
                        try:
                            self.plan.append(value)
                            self.report(self.plan.show(self.active))
                        except ValueError as error:
                            self.report(f'Smash input ignored: {error}')
                    elif action == 'clear':
                        self.plan.clear()
                        self.report(self.plan.show(self.active))
            except (OSError, RuntimeError) as error:
                self.report(f'CONTROLLER LOST: {error}; stopping motion.')
                self.stopped = True
        if self.stopped:
            return False
        # Consume a handful of already-typed keys without blocking feedback.
        for _ in range(16):
            key = terminal.read_key(0.0)
            if not key:
                break
            self.handle(key)
            if self.stopped:
                break
        return not self.stopped


def sync_targets(handler, settings, check_result, target, profile, *, gripper_profile="normal"):
    """Synchronized movement with an independently selectable gripper profile."""
    if profile not in PROFILES:
        raise ValueError("Unknown competition speed profile")
    if gripper_profile not in GRIPPER_PROFILES:
        raise ValueError("Unknown gripper speed profile")
    speed, accel = PROFILES[profile]
    if not all(name in target for name in settings.JOINT_NAMES):
        raise ValueError('Incomplete waypoint')
    if not callable(getattr(handler, 'SyncWritePosEx', None)) or not hasattr(handler, 'groupSyncWrite'):
        raise RuntimeError('Installed servo SDK lacks per-joint SyncWritePosEx; no move sent.')
    try:
        for name in settings.JOINT_NAMES:
            joint_speed, joint_accel = (GRIPPER_PROFILES[gripper_profile]
                                        if name == 'gripper' else (speed, accel))
            result = handler.SyncWritePosEx(settings.SERVO_IDS[name], target[name],
                                            joint_speed, joint_accel)
            if not result:
                raise RuntimeError(f'Failed to queue servo {name} in sync packet.')
        result = handler.groupSyncWrite.txPacket()
        check_result(handler, result, 0, 'Competition sync packet')
    finally:
        handler.groupSyncWrite.clearParam()


def wait_step(handler, terminal, manager, previous, current, following,
              settings, check_result, *, clock=time.monotonic, sleep=time.sleep):
    """No user pause, but never skip a change of direction or gripper action."""
    blend = (previous is not None and following is not None and
             eligible_blend(previous, current, following, settings.JOINT_NAMES))
    started = clock()
    deadline = started + TIMEOUT_SECONDS
    while True:
        if not manager.poll(terminal):
            return False
        near = True
        moving = False
        for name in settings.JOINT_NAMES:
            if not manager.poll(terminal):
                return False
            sid = settings.SERVO_IDS[name]
            measured, result, error = handler.ReadPos(sid)
            check_result(handler, result, error, f'Read {name} position')
            if abs(measured - current.target[name]) > NEAR_TICKS:
                near = False
            status, result, error = handler.ReadMoving(sid)
            check_result(handler, result, error, f'Read {name} moving')
            moving |= bool(status)
        if blend and near:
            return manager.poll(terminal)
        if not moving:
            if near:
                return manager.poll(terminal)
            # The first status poll can precede the servo's moving flag.
            # Allow brief startup latency, not an indefinite missed waypoint.
            if clock() - started >= 0.25:
                raise RuntimeError('Motor stopped outside waypoint tolerance; aborting.')
        if clock() >= deadline:
            raise TimeoutError('Competition motion exceeded 15 seconds.')
        sleep(0.01)


def playback(catalog, plan, profile, *, connect, read_all, enable_all_torque,
             disable_all_torque, terminal_class, settings, check_result,
             gripper_profile='normal', gamepad=None, report=print,
             clock=time.monotonic, sleep=time.sleep):
    """Execute editable pending queue. Motor commands stay in this one thread."""
    if profile not in PROFILES:
        raise ValueError('Unknown speed profile')
    if gripper_profile not in GRIPPER_PROFILES:
        raise ValueError('Unknown gripper speed profile')
    manager = InputManager(plan, report, gamepad=gamepad)
    port, handler = connect()
    completed = False
    enabled = False
    try:
        with terminal_class() as terminal:
            report('LIVE MOTION QUEUE: 1..9 append by ID | :a=grab1 | :a12 | '
                   ':i1,grab2 | :d1 | :m2,1 | :clear | L=list | '
                   'Backspace=undo | E=finish | Space/Q/Esc=STOP')
            report(plan.show())
            while True:
                if not manager.poll(terminal):
                    return False
                if not plan.pending:
                    if manager.end_after_queue:
                        completed = True
                        break
                    sleep(0.02)
                    continue
                number = plan.pop()
                manager.active = number
                steps = catalog[number]  # Immutable snapshot, never loaded mid-motion.
                report(f'\nRunning motion {plan.name(number)} ({len(steps)} waypoints). {plan.show(number)}')
                if not enabled:
                    measured = read_all(handler)
                    # Seed at measured pose BEFORE enabling. No jump to saved first point.
                    sync_targets(handler, settings, check_result, measured, 'normal')
                    if not manager.poll(terminal):
                        return False
                    enable_all_torque(handler)
                    enabled = True
                for index, step in enumerate(steps):
                    if not manager.poll(terminal):
                        return False
                    sync_targets(handler, settings, check_result, step.target, profile,
                                 gripper_profile=gripper_profile)
                    prev = steps[index - 1] if index > 0 else None
                    nxt = steps[index + 1] if index + 1 < len(steps) else None
                    if not wait_step(handler, terminal, manager, prev, step, nxt,
                                     settings, check_result, clock=clock, sleep=sleep):
                        return False
                manager.active = None
                report(f'Motion {plan.name(number)} complete. {plan.show()}')
            report('Queue finished. Torque remains ON at the last pose; support the arm.')
            return True
    finally:
        try:
            if not completed:
                report('STOP/FAULT: releasing torque; support arm and blocks.')
                disable_all_torque(handler)
        finally:
            port.closePort()


def menu(routes, load_sequence, validate_target, *, connect, read_all,
         enable_all_torque, disable_all_torque, terminal_class,
         settings, check_result, report=print):
    """Queue ANY saved validated motion by name or ID, before or during motion."""
    if not routes:
        report('No saved waypoint sequences found.')
        return
    catalog, labels, aliases = build_motion_catalog(
        routes, load_sequence, validate_target, report=report)
    if not catalog:
        report('No valid saved waypoint motions. Repair recordings in script 6 menu 1.')
        return

    for identifier in sorted(catalog, key=int):
        report(f'{identifier}: {labels[identifier]} '
               f'({len(catalog[identifier])} points) [READY]')
    report('Use saved names (grab1, c1r1a, custom) or the listed IDs.')
    report('Square macros (e.g. c1r1) include its m and p sequences.')
    report('Only READY names/IDs can run. Collision clearance is NOT verified.')

    raw = input('Initial queue (e.g. grab1 c1r1a grab2; blank=empty; Q=cancel): ').strip().lower()
    if raw == 'q':
        return
    plan = QueuePlan(catalog, raw.split(), aliases=aliases, labels=labels)
    speed_input = input('Profile N=normal 300/20, F=fast 600/35, '
                        'T=turbo 1200/60 [N]: ').strip().lower()
    if speed_input not in ('', 'n', 'f', 't'):
        raise ValueError('Choose N, F or T')
    profile = {'': 'normal', 'n': 'normal', 'f': 'fast', 't': 'turbo'}[speed_input]
    gripper_input = input('Gripper N=normal 300/20 [default], F=fast 600/35, '
                          'T=turbo 1200/60: ').strip().lower()
    if gripper_input not in ('', 'n', 'f', 't'):
        raise ValueError('Choose gripper N, F or T')
    gripper_profile = {'': 'normal', 'n': 'normal', 'f': 'fast',
                       't': 'turbo'}[gripper_input]
    if gripper_profile != 'normal':
        report('WARNING: faster gripper commands apply to BOTH opening and closing. '
               'A calibrated closed pose does not limit collision/impact load.')
        confirmation = input('Type CONFIRM to enable faster gripper, else use normal: ')
        if confirmation.strip() != 'CONFIRM':
            gripper_profile = 'normal'
    report(f'Selected arm {profile} {PROFILES[profile]}; '
           f'gripper {gripper_profile} {GRIPPER_PROFILES[gripper_profile]}.')
    report('No collision checking, automatic grip-force control or firmware overclock.')
    controller_input = input('Controller K=keyboard [default], J=Joy-Con Smash: ').strip().lower()
    if controller_input not in ('', 'k', 'j'):
        raise ValueError('Choose controller K or J')
    gamepad = None
    if controller_input == 'j':
        from smash_joycon import JoyConGamepad
        gamepad = JoyConGamepad(report=report)
    report('Prestart queue editor: a grab1, i 1 grab2, d 1, m 2 1, clear, l, RUN, Q.')
    while True:
        cmd = input('queue> ').strip()
        if cmd.lower() == 'q':
            if gamepad is not None:
                gamepad.close()
            return
        if cmd.lower() == 'run':
            break
        try:
            report(plan.edit(cmd))
        except (ValueError, IndexError) as error:
            report(f'Queue edit rejected: {error}')
    report('Starting live queue. Clear ALL future paths; support arm; physical stop ready.')
    try:
        return playback(catalog, plan, profile, connect=connect, read_all=read_all,
                        enable_all_torque=enable_all_torque,
                        disable_all_torque=disable_all_torque,
                        terminal_class=terminal_class, settings=settings,
                        check_result=check_result, gripper_profile=gripper_profile,
                        gamepad=gamepad, report=report)
    finally:
        if gamepad is not None:
            gamepad.close()
