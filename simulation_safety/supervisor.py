"""Offline simulator only. No hardware driver imports or hardware implementation."""
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math


def finite(x):
    return type(x) in (int, float) and math.isfinite(x)


class SafetyError(Exception):
    pass


def hardware_driver(*, hardware_enable=False):
    # A positive gate is necessary but never sufficient: no adapter is supplied.
    if not hardware_enable:
        raise SafetyError('hardware disabled by default')
    raise SafetyError('hardware integration unavailable')


def load_sequence(path):
    """Read without rewriting, converting raw strings only; no angle conversion."""
    data = json.loads(path.read_text())
    expected = [(1, 'base_yaw'), (2, 'shoulder'), (3, 'elbow'),
                (4, 'wrist'), (5, 'gripper')]
    if (data.get('schema_version') != 1 or data.get('type') != 'pose_sequence'
            or data.get('input_type') != 'raw'
            or [(j['servo_id'], j['joint']) for j in data['joint_order']] != expected
            or len(data['points']) != 2):
        raise SafetyError('requires exactly two raw recorded waypoints and known joint order')
    points = tuple(tuple(float(v) for v in p['input_values']) for p in data['points'])
    if any(len(p) != 5 or any(not finite(v) or v != int(v) for v in p) for p in points):
        raise SafetyError('invalid raw positions')
    return points


@dataclass(frozen=True)
class Policy:
    # All values must be provided. Test values are synthetic, NOT calibration.
    limits: tuple
    tracking_error: float
    jam_seconds: float
    stale_seconds: float
    move_timeout: float
    tolerance: float

    def __post_init__(self):
        if (len(self.limits) != 5 or any(len(r) != 2 or not all(finite(x) for x in r)
                or r[0] >= r[1] for r in self.limits)
                or any(not finite(x) or x <= 0 for x in
                       (self.tracking_error, self.jam_seconds, self.stale_seconds,
                        self.move_timeout, self.tolerance))
                or self.tolerance >= self.tracking_error):
            raise SafetyError('invalid simulation policy')

    def validate(self, pose):
        if len(pose) != 5 or any(not finite(x) or not lo <= x <= hi
                                for x, (lo, hi) in zip(pose, self.limits)):
            raise SafetyError('out-of-range or malformed joint command/feedback')


class CollisionModel:
    """Sphere covers for EVERY link/jaw, tested against expanded obstacle boxes.

    geometry(q) returns {body: ((x,y,z), radius)}. Bounds[body][joint] must
    bound sphere-center displacement per joint unit across the entire domain.
    Half a sample's maximum displacement inflates each sphere, conservatively
    covering the continuous linear joint path if those supplied bounds hold.
    No dimensions/bounds are inferred from preview artwork. Synthetic use only.
    """
    def __init__(self, geometry, bounds, obstacles, clearance, uncertainty, samples):
        if (geometry is None or not bounds or not obstacles or type(samples) is not int
                or samples < 1 or samples > 10000
                or not finite(clearance) or clearance < 0
                or not finite(uncertainty) or uncertainty < 0):
            raise SafetyError('geometry, obstacles and uncertainty configuration required')
        for b in bounds.values():
            if len(b) != 5 or any(not finite(v) or v < 0 for v in b):
                raise SafetyError('invalid displacement bounds')
        for lo, hi in obstacles.values():
            if len(lo) != 3 or len(hi) != 3 or any(
                    not finite(a) or not finite(b) or a > b for a, b in zip(lo, hi)):
                raise SafetyError('invalid obstacle')
        self.geometry, self.bounds, self.obstacles = geometry, bounds, obstacles
        self.margin, self.samples = clearance + uncertainty, samples

    def check(self, start, end):
        delta = [abs(a-b) for a, b in zip(start, end)]
        for i in range(self.samples + 1):
            q = tuple(a+(b-a)*i/self.samples for a, b in zip(start, end))
            bodies = self.geometry(q)
            if set(bodies) != set(self.bounds):
                raise SafetyError('incomplete link/wrist/gripper geometry')
            for name, (center, radius) in bodies.items():
                if len(center) != 3 or any(not finite(x) for x in center) or not finite(radius) or radius < 0:
                    raise SafetyError('invalid body geometry')
                inflation = sum(d*b for d, b in zip(delta, self.bounds[name]))/(2*self.samples)
                r = radius + self.margin + inflation
                for obstacle, (lo, hi) in self.obstacles.items():
                    distance2 = sum(max(a-x, 0, x-b)**2 for x, a, b in zip(center, lo, hi))
                    if distance2 <= r*r:
                        raise SafetyError(f'collision: {name} / {obstacle}')


class MockDriver:
    """Deterministic instantaneous tracking unless stall is injected.

    stop_hold cancels target and freezes simulated position; never torque-off.
    This contract says nothing about real STS3215 hold semantics.
    """
    def __init__(self, pose):
        self.pose = tuple(pose)
        self.target = None
        self.stall = self.stale = self.missing = self.fail_stop = False
        self.commands = []
        self.holds = 0

    def command(self, pose):
        self.commands.append(tuple(pose))
        self.target = tuple(pose)

    def feedback(self, now):
        if self.missing:
            raise SafetyError('missing device / communication failure')
        if self.target is not None and not self.stall:
            self.pose = self.target
        return self.pose, (now - 1000 if self.stale else now)

    def stop_hold(self):
        self.target = None
        self.holds += 1
        if self.fail_stop:
            raise SafetyError('mock hold failed')


class Supervisor:
    def __init__(self, driver, policy, collisions, log_path):
        # Exact type rejects real drivers and subclasses with hidden I/O.
        if type(driver) is not MockDriver:
            raise SafetyError('only the offline MockDriver is accepted')
        self.driver, self.policy, self.collisions = driver, policy, collisions
        self.log_path = log_path
        self.state = 'IDLE'
        self.queue = deque()
        self.active = None
        self.events = []
        self.last_time = None
        self.last_feedback = None
        self.log_failed = False

    def event(self, now, kind, detail):
        record = dict(utc=datetime.now(timezone.utc).isoformat(), simulation_time=now,
                      kind=kind, detail=detail, state=self.state,
                      feedback=self.last_feedback, target=self.active)
        self.events.append(record)
        with self.log_path.open('a', encoding='utf-8') as f:
            f.write(json.dumps(record, allow_nan=False) + '\n')

    def fault(self, now, reason):
        self.state = 'FAULT'
        self.queue.clear()
        try:
            self.driver.stop_hold()
        except Exception as error:
            reason += f'; HOLD FAILED: {error}'
        try:
            self.event(now, 'FAULT_OPERATOR_ALERT', reason)
        except Exception:
            self.log_failed = True  # Still latched, alert remains in memory.
        self.active = None

    def feedback(self, now):
        if not finite(now) or (self.last_time is not None and now < self.last_time):
            raise SafetyError('invalid or backward clock')
        self.last_time = now
        pose, stamp = self.driver.feedback(now)
        self.policy.validate(pose)
        if not finite(stamp) or stamp > now or now-stamp > self.policy.stale_seconds:
            raise SafetyError('stale or invalid feedback timestamp')
        self.last_feedback = dict(pose=pose, timestamp=stamp)
        return tuple(pose)

    def start(self, points, now):
        if self.state != 'IDLE':
            raise SafetyError('manual reset required or already running')
        try:
            if len(points) != 2:
                raise SafetyError('exactly two recorded waypoints required')
            points = tuple(tuple(p) for p in points)
            for p in points:
                self.policy.validate(p)
            current = self.feedback(now)
            if self.collisions is None:
                raise SafetyError('unknown geometry blocks motion')
            for target in points:
                self.collisions.check(current, target)
                current = target
            self.event(now, 'PREFLIGHT', 'two-waypoint simulation accepted')
            self.queue.extend(points)
            self.state = 'RUNNING'
        except Exception as error:
            self.fault(now, str(error))

    def tick(self, now):
        if self.state != 'RUNNING':
            return
        try:
            pose = self.feedback(now)
            self.event(now, 'FEEDBACK', 'simulation sample')
            if self.active is not None:
                error = max(abs(a-b) for a, b in zip(pose, self.active))
                if now-self.sent_at >= self.policy.move_timeout:
                    raise SafetyError('movement timeout')
                if error <= self.policy.tolerance:
                    self.event(now, 'ARRIVED', 'target reached')
                    self.active = None
                    self.bad_since = None
                else:
                    if error > self.policy.tracking_error:
                        if self.bad_since is None:
                            self.bad_since = now
                        if now-self.bad_since >= self.policy.jam_seconds:
                            raise SafetyError('jam / sustained tracking error')
                    else:
                        self.bad_since = None
            if self.active is None:
                if not self.queue:
                    self.state = 'COMPLETE'
                    self.event(now, 'COMPLETE', 'simulation complete; holding')
                    return
                target = self.queue[0]
                # Recheck from actual feedback before EVERY dispatch.
                self.collisions.check(pose, target)
                self.event(now, 'COMMAND', list(target))
                self.driver.command(target)
                self.active = self.queue.popleft()
                self.sent_at, self.bad_since = now, None
        except Exception as error:
            self.fault(now, str(error))

    def stop(self, now):
        self.fault(now, 'operator stop requested; queue aborted')

    def reset(self, now, *, operator_ack=False):
        if not operator_ack or self.state not in ('FAULT', 'COMPLETE'):
            raise SafetyError('explicit operator acknowledgement required')
        try:
            pose = self.feedback(now)
            if self.collisions is None:
                raise SafetyError('unknown geometry')
            self.collisions.check(pose, pose)
            self.event(now, 'RESET', 'operator acknowledged; no automatic restart')
            self.state = 'IDLE'
            self.log_failed = False
        except Exception as error:
            self.fault(now, str(error))
