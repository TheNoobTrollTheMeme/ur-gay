"""Editable pre-input queue for STS3215 competition placement routes.

Only validated move/place waypoint pairs are eligible. Queued commands are
snapshots loaded before the motors are enabled. During playback, keys edit
*pending* squares while the active square finishes. No unverified firmware
'overclock' or automatic gripper force adjustment is attempted.
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
GRIPPER_MAX_SPEED = 300
GRIPPER_MAX_ACCEL = 20
NEAR_TICKS = 8
TIMEOUT_SECONDS = 15.0
STOP_KEYS = {' ', 'q', 'Q', '\x1b', '\x03'}


@dataclass(frozen=True)
class Point:
    route: str
    number: int
    target: dict


class QueuePlan:
    """Manipulates only waiting actions, never the currently executing action."""
    def __init__(self, catalog, initial=()):
        self.catalog = dict(catalog)
        self.pending = deque()
        for action in initial:
            self.append(action)

    def _check(self, action):
        action = str(action)
        if action not in self.catalog:
            raise ValueError(f'Unknown square {action}. Valid: {" ".join(self.catalog)}')
        return action

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
        items = ' '.join(f'{index}:{name}' for index, name in enumerate(self.pending, 1)) or '(empty)'
        return f'Active: {active or "idle"} | Pending: {items}'

    def edit(self, command):
        """Parse queue editor commands. Caller catches ValueError and keeps motion going."""
        words = command.strip().lower().split()
        if not words:
            return self.show()
        op, *args = words
        if op in ('l', 'ls', 'list') and not args:
            return self.show()
        if op in ('a', 'add') and len(args) == 1:
            self.append(args[0]); return self.show()
        if op in ('i', 'insert') and len(args) == 2:
            self.insert(args[0], args[1]); return self.show()
        if op in ('d', 'del', 'delete') and len(args) == 1:
            removed = self.delete(args[0]); return f'Removed pending square {removed}. {self.show()}'
        if op in ('m', 'move') and len(args) == 2:
            self.move(args[0], args[1]); return self.show()
        if op == 'clear' and not args:
            self.clear(); return self.show()
        raise ValueError('Commands: a 2, i 1 2, d 1, m 2 1, clear, l')


def build_catalog(routes, load_sequence, validate_target):
    """Load every candidate once and reject invalid poses before hardware connection."""
    catalog = {}
    for index, move_path in enumerate(routes, 1):
        if not move_path.stem.endswith('m'):
            raise ValueError('Only move/place route pairs can be queued.')
        place_path = move_path.with_name(move_path.stem[:-1] + 'p.json')
        steps = []
        for path in (move_path, place_path):
            _, points = load_sequence(path)
            if not points:
                raise ValueError(f'Empty competition sequence: {path.name}')
            for n, point in enumerate(points, 1):
                validate_target(point)
                steps.append(Point(path.stem, n, dict(point)))
        catalog[str(index)] = tuple(steps)
    return catalog


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
    """Nonblocking raw-key queue editor; Space/Q/Escape always stop immediately."""
    def __init__(self):
        self.command = None

    def feed(self, key):
        if not key:
            return None
        if key in STOP_KEYS:
            return ('stop', None)
        if self.command is not None:
            if key in ('\r', '\n'):
                done = self.command
                self.command = None
                match = re.fullmatch(r'(a|d)([1-9][0-9]*)', done)
                if match:
                    done = f'{match[1]} {match[2]}'
                match = re.fullmatch(r'(m|i)([1-9][0-9]*),([1-9][0-9]*)', done)
                if match:
                    done = f'{match[1]} {match[2]} {match[3]}'
                return ('edit', done)
            if key in ('\x7f', '\x08'):
                self.command = self.command[:-1]
            elif key.isprintable() and len(self.command) < 80:
                self.command += key
            return None
        if key == ':':
            self.command = ''
            return ('prompt', 'Enter command then Enter (:a2 / :d1 / :m2,1 / :i1,2). SPACE ALWAYS STOPS.')
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
    def __init__(self, plan, reporter=print):
        self.plan = plan
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
            self.report('Finish queue then exit enabled. No additional square required.')
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
        # Consume a handful of already-typed keys without blocking feedback.
        for _ in range(16):
            key = terminal.read_key(0.0)
            if not key:
                break
            self.handle(key)
            if self.stopped:
                break
        return not self.stopped


def sync_targets(handler, settings, check_result, target, profile):
    """Per-servo synchronized command with the original gripper speed ceiling."""
    if profile not in PROFILES:
        raise ValueError('Unknown competition speed profile')
    speed, accel = PROFILES[profile]
    if not all(name in target for name in settings.JOINT_NAMES):
        raise ValueError('Incomplete waypoint')
    if not callable(getattr(handler, 'SyncWritePosEx', None)) or not hasattr(handler, 'groupSyncWrite'):
        raise RuntimeError('Installed servo SDK lacks per-joint SyncWritePosEx; no move sent.')
    try:
        for name in settings.JOINT_NAMES:
            joint_speed = min(speed, GRIPPER_MAX_SPEED) if name == 'gripper' else speed
            joint_accel = min(accel, GRIPPER_MAX_ACCEL) if name == 'gripper' else accel
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
             report=print, clock=time.monotonic, sleep=time.sleep):
    """Execute editable pending queue. Motor commands stay in this one thread."""
    if profile not in PROFILES:
        raise ValueError('Unknown speed profile')
    manager = InputManager(plan, report)
    port, handler = connect()
    completed = False
    enabled = False
    try:
        with terminal_class() as terminal:
            report('LIVE QUEUE: 1..9 append | :a2 | :i1,2 | :d1 | :m2,1 | '
                   ':clear | L=list | Backspace=undo | E=finish | Space/Q/Esc=STOP')
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
                report(f'\nRunning square {number} ({len(steps)} waypoints). {plan.show(number)}')
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
                    sync_targets(handler, settings, check_result, step.target, profile)
                    prev = steps[index - 1] if index > 0 else None
                    nxt = steps[index + 1] if index + 1 < len(steps) else None
                    if not wait_step(handler, terminal, manager, prev, step, nxt,
                                     settings, check_result, clock=clock, sleep=sleep):
                        return False
                manager.active = None
                report(f'Square {number} complete. {plan.show()}')
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
    """Configure queue and speed once; edit further while motors are moving."""
    if not routes:
        report('No move/place pairs found.')
        return
    catalog = build_catalog(routes, load_sequence, validate_target)
    for number, path in enumerate(routes, 1):
        report(f'{number}: {path.stem[:-1]} ({len(catalog[str(number)])} waypoints)')
    if len(catalog) > 9:
        raise ValueError('Live one-key queue supports at most 9 squares.')
    raw = input('Initial queue (e.g. 1 2 3; blank=empty; Q=cancel): ').strip().lower()
    if raw == 'q':
        return
    plan = QueuePlan(catalog)
    for item in raw.split():
        plan.append(item)
    speed_input = input('Profile N=normal 300/20, F=fast 600/35, '
                        'T=turbo 1200/60 [N]: ').strip().lower()
    if speed_input not in ('', 'n', 'f', 't'):
        raise ValueError('Choose N, F or T')
    profile = {'': 'normal', 'n': 'normal', 'f': 'fast', 't': 'turbo'}[speed_input]
    report(f'Selected {profile}: arm {PROFILES[profile]}; gripper <=300/20.')
    report('Only prevalidated placement routes. NO attack/flick paths or collision detection.')
    report('Prestart queue editor: a 2, i 1 2, d 1, m 2 1, clear, l, RUN, Q.')
    while True:
        cmd = input('queue> ').strip()
        if cmd.lower() == 'q':
            return
        if cmd.lower() == 'run':
            break
        try:
            report(plan.edit(cmd))
        except (ValueError, IndexError) as error:
            report(f'Queue edit rejected: {error}')
    report('Starting live queue. Physical power stop ready; clear all paths and support arm.')
    return playback(catalog, plan, profile, connect=connect, read_all=read_all,
                    enable_all_torque=enable_all_torque,
                    disable_all_torque=disable_all_torque,
                    terminal_class=terminal_class, settings=settings,
                    check_result=check_result, report=report)
