"""Competition placement playback for script 6.

Prevalidates all saved points and advances slightly early through monotonic,
unchanging-gripper transit waypoints. Never raises the existing motor speed or
acceleration, skips a grip/release, or executes attack/flick routes.
"""
import time
from dataclasses import dataclass

# Same speed/acceleration as the existing script 6. Optimizations are in
# removing operator/disk delays and conservative, near-point lookahead.
SPEED = 300
ACCELERATION = 20
NEAR_TICKS = 8
TIMEOUT_SECONDS = 15


@dataclass(frozen=True)
class Step:
    route: str
    number: int
    target: dict


def prepare_placements(paths, load_sequence, validate_target):
    """Snapshot and prevalidate every point BEFORE opening the motor connection."""
    steps = []
    for move_path in paths:
        if not move_path.stem.endswith('m'):
            raise ValueError('Competition supports move/place routes only.')
        place_path = move_path.with_name(move_path.stem[:-1] + 'p.json')
        for path in (move_path, place_path):
            _, points = load_sequence(path)
            if not points:
                raise ValueError(f'{path.stem} has no waypoints')
            for number, point in enumerate(points, 1):
                validate_target(point)
                steps.append(Step(path.stem, number, dict(point)))
    return steps


def can_look_ahead(previous, current, following, joint_names):
    """Only cross interior transit points with no grip motion or reversal."""
    if previous.route != current.route or current.route != following.route:
        return False
    if any(step.target['gripper'] != current.target['gripper']
           for step in (previous, following)):
        return False
    for joint in joint_names:
        incoming = current.target[joint] - previous.target[joint]
        outgoing = following.target[joint] - current.target[joint]
        if incoming * outgoing < 0:
            return False
    return True


def wait_near(handler, terminal, target, settings, check_result, stopped,
              *, now=time.monotonic, timeout=TIMEOUT_SECONDS):
    """Advance before settling only when all measured joints are within 8 ticks."""
    deadline = now() + timeout
    while True:
        if stopped(terminal):
            return False
        near = True
        moving = False
        for joint in settings.JOINT_NAMES:
            if stopped(terminal):
                return False
            sid = settings.SERVO_IDS[joint]
            position, result, error = handler.ReadPos(sid)
            check_result(handler, result, error, f'Read {joint} position')
            if abs(position - target[joint]) > NEAR_TICKS:
                near = False
            state, result, error = handler.ReadMoving(sid)
            check_result(handler, result, error, f'Read {joint} moving')
            moving |= bool(state)
        if near:
            return not stopped(terminal)
        if not moving:
            raise RuntimeError('Waypoint stopped outside near-target tolerance; aborting.')
        if now() >= deadline:
            raise TimeoutError('Competition waypoint exceeded movement timeout.')


def execute(steps, *, connect, read_all, move_all_sync, enable_all_torque,
            disable_all_torque, terminal_class, settings, check_result,
            stopped, wait_move, log=print):
    """Keep all original joint limits and stop handling, without pause between steps."""
    if not steps:
        raise ValueError('Competition has no waypoints.')
    port, handler = connect()
    completed = False
    try:
        with terminal_class() as terminal:
            current = read_all(handler)
            move_all_sync(handler, current, speed=SPEED, acceleration=ACCELERATION)
            if stopped(terminal):
                return False
            enable_all_torque(handler)
            for index, step in enumerate(steps):
                if stopped(terminal):
                    return False
                look_ahead = (index > 0 and index + 1 < len(steps) and
                              can_look_ahead(steps[index - 1], step,
                                             steps[index + 1], settings.JOINT_NAMES))
                log(f'\r\n{step.route}: {step.number} ({index + 1}/{len(steps)})'
                    + (' [continuous transit]' if look_ahead else ' [exact point]'))
                move_all_sync(handler, step.target, speed=SPEED,
                              acceleration=ACCELERATION)
                if look_ahead:
                    success = wait_near(handler, terminal, step.target,
                                        settings, check_result, stopped)
                else:
                    success = wait_move(handler, terminal)
                if not success:
                    return False
            completed = True
        log('Competition placement route completed; torque remains ON at final pose.')
        return True
    finally:
        try:
            if not completed:
                log('STOP: releasing torque; support the arm.')
                disable_all_torque(handler)
        finally:
            port.closePort()
