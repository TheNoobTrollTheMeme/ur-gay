"""Contact-limited STS3215 gripper control for script 06 only.

NOT a safety-rated controller or a calibrated force sensor. Repair damaged parts
before commissioning. Never test with fingers, animals, or a suspended load.
Registers: Waveshare ST servo memory table, Feetech SMS/STS SDK.
Torque limits use volatile register 48; EEPROM is not changed.
"""
from dataclasses import asdict, dataclass
from datetime import datetime
import json
import math
from pathlib import Path
import shutil
import time

VERSION = 'gripper-guard-1'
CONFIG_NAME = 'gripper_guard.local.json'


@dataclass(frozen=True)
class Config:
    servo_id: int
    open_ticks: int
    closed_ticks: int
    contact_load: int
    close_torque: int
    hold_torque: int
    max_current_raw: int
    speed: int
    acceleration: int
    commissioned: bool = False
    position_tolerance: int = 8
    release_margin: int = 16
    stall_seconds: float = 0.6
    max_poll_gap: float = 0.35
    max_temperature: int = 55

    @property
    def direction(self):
        return 1 if self.closed_ticks > self.open_ticks else -1

    def validate(self, servo_id):
        ints = ('servo_id', 'open_ticks', 'closed_ticks', 'contact_load',
                'close_torque', 'hold_torque', 'max_current_raw', 'speed',
                'acceleration', 'position_tolerance', 'release_margin',
                'max_temperature')
        if any(type(getattr(self, name)) is not int for name in ints):
            raise ValueError('Gripper settings must use integer register values.')
        if self.commissioned is not True:
            raise ValueError('Gripper guard is not commissioned. Use menu 8 after repair.')
        if self.servo_id != servo_id or not 1 <= self.servo_id <= 253:
            raise ValueError('Configured gripper ID does not match arm_settings.')
        if not (0 <= self.open_ticks <= 4095 and 0 <= self.closed_ticks <= 4095):
            raise ValueError('Gripper endpoints must be raw positions 0..4095.')
        travel = abs(self.closed_ticks - self.open_ticks)
        if not 32 <= travel <= 2048:
            raise ValueError('Verify non-wrapping gripper endpoints, 32..2048 ticks apart.')
        if not 0 < self.hold_torque <= self.contact_load < self.close_torque <= 1000:
            raise ValueError('Require 0 < hold torque <= contact load < close torque <= 1000.')
        if not 0 < self.max_current_raw <= 32767:
            raise ValueError('A positive, physically verified current cutoff is required.')
        if not 1 <= self.speed <= 300 or not 1 <= self.acceleration <= 20:
            raise ValueError('Guarded speed must be 1..300 and acceleration 1..20 (not zero).')
        if not 1 <= self.position_tolerance < self.release_margin < travel:
            raise ValueError('Require tolerance < release margin < gripper travel.')
        if not 20 <= self.max_temperature <= 60:
            raise ValueError('Temperature cutoff must be 20..60 C.')
        for name in ('stall_seconds', 'max_poll_gap'):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f'{name} must be a finite number.')
        if not 0.1 <= self.max_poll_gap <= 0.5 or not 0.2 <= self.stall_seconds <= 2.0:
            raise ValueError('Polling/stall limits are out of range.')
        return self


def load_config(root, settings):
    path = Path(root) / CONFIG_NAME
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        config = Config(**data).validate(settings.SERVO_IDS['gripper'])
    except (OSError, TypeError, ValueError) as error:
        raise ValueError(f'Playback blocked: configure gripper protection using menu 8. {error}') from error
    # Confirm both measured endpoints also satisfy the existing arm calibration.
    for ticks in (config.open_ticks, config.closed_ticks):
        angle = ((ticks - settings.ZERO_TICKS['gripper']) * 360.0 /
                 (settings.DIRECTIONS['gripper'] * settings.TICKS_PER_REVOLUTION))
        low, high = settings.ANGLE_LIMITS['gripper']
        if not low <= angle <= high:
            raise ValueError('Gripper endpoint is outside arm_settings angle limits.')
    return config


@dataclass(frozen=True)
class Feedback:
    position: int
    load: int
    current: int
    temperature: int


def read_feedback(handler, servo_id, check):
    """Read signed-magnitude feedback; reject bad packets rather than treating as zero."""
    pos, result, error = handler.ReadPos(servo_id)
    check(handler, result, error, 'Read gripper position')
    load, result, error = handler.read2ByteTxRx(servo_id, 60)
    check(handler, result, error, 'Read gripper load')
    current, result, error = handler.read2ByteTxRx(servo_id, 69)
    check(handler, result, error, 'Read gripper current')
    temp, result, error = handler.read1ByteTxRx(servo_id, 63)
    check(handler, result, error, 'Read gripper temperature')
    if not 0 <= pos <= 4095 or not 0 <= load <= 2047 or not 0 <= current <= 65535:
        raise RuntimeError('Invalid gripper position/load/current feedback.')
    load = load & 0x3ff  # Bit 10 is direction, NOT part of the load magnitude.
    current = current & 0x7fff  # Bit 15 is direction.
    if load > 1000 or not 0 <= temp <= 100:
        raise RuntimeError('Invalid gripper load/temperature feedback.')
    return Feedback(pos, load, current, temp)


class GripperGuard:
    def __init__(self, handler, settings, check, config, *, clock=time.monotonic, report=print):
        self.h = handler
        self.settings = settings
        self.check = check
        self.c = config.validate(settings.SERVO_IDS['gripper'])
        self.clock = clock
        self.report = report
        self.id = self.c.servo_id
        self.state = 'unprepared'
        self.latched = None
        self.goal = None
        self.cap = None
        self.last_poll = None
        self.progress_at = None
        self.progress_pos = None
        self.last_feedback = None
        self.faulted = False

    def _write(self, result, action):
        self.check(self.h, *result, action)

    def _limit(self, value):
        if self.cap == value:
            return
        self._write(self.h.write2ByteTxRx(self.id, 48, value), 'Set gripper torque cap')
        actual, result, error = self.h.read2ByteTxRx(self.id, 48)
        self.check(self.h, result, error, 'Verify gripper torque cap')
        if actual != value:
            raise RuntimeError('Gripper torque cap did not read back correctly.')
        self.cap = value

    def emergency_release(self):
        """Best effort gripper-only release; caller must abort motion on a fault."""
        self.faulted = True
        try:
            self._write(self.h.write1ByteTxRx(self.id, 40, 0), 'Release gripper on fault')
        except Exception as error:
            self.report(f'GRIPPER RELEASE FAILED: {error}. Use the physical power stop.')

    def _feedback(self):
        f = read_feedback(self.h, self.id, self.check)
        if f.current >= self.c.max_current_raw:
            raise RuntimeError('Gripper current cutoff: stop and inspect the mechanism.')
        if f.temperature >= self.c.max_temperature:
            raise RuntimeError('Gripper temperature cutoff: stop and inspect the mechanism.')
        self.last_feedback = f
        return f

    def prepare(self):
        """Verify feedback/mode and cap torque BEFORE any sequence enables servos."""
        try:
            mode, result, error = self.h.read1ByteTxRx(self.id, 33)
            self.check(self.h, result, error, 'Read gripper operating mode')
            if mode != 0:
                raise RuntimeError('Gripper must be in position mode 0; mode was not changed.')
            maximum, result, error = self.h.read2ByteTxRx(self.id, 16)
            self.check(self.h, result, error, 'Read gripper EEPROM maximum torque')
            if not self.c.close_torque <= maximum <= 1000:
                raise RuntimeError('Configured gripper cap exceeds its hardware maximum.')
            # A reduced cap is applied even if torque was already on.
            self._limit(self.c.hold_torque)
            f = self._feedback()
            lo, hi = sorted((self.c.open_ticks, self.c.closed_ticks))
            if not lo - self.c.position_tolerance <= f.position <= hi + self.c.position_tolerance:
                raise RuntimeError('Current gripper pose is outside the calibrated travel.')
            self.goal = f.position
            self._write(self.h.WritePosEx(self.id, f.position, self.c.speed, self.c.acceleration),
                        'Seed measured gripper pose')
            # Restarting playback must not crush an object already being held.
            if abs(f.position - self.c.open_ticks) > self.c.position_tolerance:
                self.latched = f.position
                self.report('Gripper starts partly closed: opening is required before a new squeeze.')
            self.state = 'holding' if self.latched is not None else 'idle'
            self.last_poll = self.progress_at = self.clock()
            self.progress_pos = f.position
        except Exception:
            self.emergency_release()
            raise

    def _contact(self, feedback):
        self.latched = self.goal = feedback.position
        self._limit(self.c.hold_torque)
        self._write(self.h.WritePosEx(self.id, self.goal, self.c.speed, self.c.acceleration),
                    'Hold gripper at measured contact position')
        self.state = 'holding'
        self.report(f'GRIPPER CONTACT: hold {self.goal}, load {feedback.load}; other joints continue.')

    def poll(self):
        if self.faulted or self.state == 'unprepared':
            raise RuntimeError('Gripper guard is not ready.')
        try:
            f = self._feedback()
            now = self.clock()
            if now - self.last_poll > self.c.max_poll_gap:
                raise RuntimeError('Gripper feedback deadline missed; aborting rather than closing blind.')
            self.last_poll = now
            if self.state == 'closing' and f.load >= self.c.contact_load:
                self._contact(f)
            elif self.state in ('closing', 'opening'):
                if self.state == 'opening' and f.load >= self.c.contact_load:
                    raise RuntimeError('Gripper resisted opening; this is an obstruction, not a successful grasp.')
                if abs(f.position - self.goal) <= self.c.position_tolerance:
                    if self.state == 'opening':
                        self.latched = None
                    self.state = 'idle'
                    self._limit(self.c.hold_torque)
                elif abs(f.position - self.progress_pos) >= 2:
                    self.progress_pos = f.position
                    self.progress_at = now
                elif now - self.progress_at >= self.c.stall_seconds:
                    raise RuntimeError('Gripper stalled without valid contact feedback; inspect before retrying.')
            return self.state in ('holding', 'idle')
        except Exception:
            self.emergency_release()
            raise

    def send(self, positions, speed=300, acceleration=20):
        """Preserve all four arm targets; limit and independently monitor the gripper."""
        try:
            self.poll()
            f = self.last_feedback
            if set(positions) != set(self.settings.JOINT_NAMES):
                raise ValueError('A guarded waypoint must contain all configured joints.')
            if any(type(v) is not int or not 0 <= v <= 4095 for v in positions.values()):
                raise ValueError('Waypoint raw values must be integers in 0..4095.')
            lo, hi = sorted((self.c.open_ticks, self.c.closed_ticks))
            target = min(hi, max(lo, positions['gripper']))
            if target != positions['gripper']:
                self.report(f'Gripper target clipped to calibrated travel: {target}. Recording unchanged.')
            if self.latched is not None:
                opening = self.c.direction * (target - self.latched) <= -self.c.release_margin
                if not opening:
                    target = self.latched
                    state = 'holding'
                else:
                    state = 'opening'
            else:
                delta = self.c.direction * (target - f.position)
                state = ('closing' if delta > self.c.position_tolerance else
                         'opening' if delta < -self.c.position_tolerance else 'idle')
            self._limit(self.c.close_torque if state in ('closing', 'opening') else self.c.hold_torque)
            self.goal = target
            self.state = state
            self.progress_pos = f.position
            self.progress_at = self.clock()
            # Different speed/acceleration for ID 5 in the same synchronized packet.
            try:
                for joint in self.settings.JOINT_NAMES:
                    gripper = joint == 'gripper'
                    added = self.h.SyncWritePosEx(
                        self.settings.SERVO_IDS[joint], target if gripper else positions[joint],
                        self.c.speed if gripper else speed,
                        self.c.acceleration if gripper else acceleration)
                    if not added:
                        raise RuntimeError(f'Cannot add {joint} to guarded synchronized move.')
                result = self.h.groupSyncWrite.txPacket()
                self.check(self.h, result, 0, 'Send guarded synchronized waypoint')
            finally:
                self.h.groupSyncWrite.clearParam()
            # Include command time in the next watchdog interval.
        except Exception:
            if not self.faulted:
                self.emergency_release()
            raise


def configure(root, settings):
    """Collect measured calibration; no motor commands and no guessed force limits."""
    print('Repair/inspect the gripper first. This setup sends NO motor commands.')
    print('Use STS3215 position mode. Verify the gripper servo ID and unloaded endpoints.')
    print('Closed endpoint must be BEFORE a hard stop. Never calibrate using fingers.')
    print('Load is an effort proxy, NOT jaw force. Have a technician establish safe limits.')
    print('Use menu 9 for read-only telemetry. Enter Q at any prompt to cancel.')
    def number(prompt):
        text = input(prompt + ': ').strip()
        if text.lower() == 'q':
            raise ValueError('Configuration cancelled; previous settings unchanged.')
        return int(text)
    sid = number(f'Confirmed gripper servo ID (arm_settings says {settings.SERVO_IDS["gripper"]})')
    config = Config(
        servo_id=sid,
        open_ticks=number('Measured safe OPEN raw position'),
        closed_ticks=number('Measured safe unloaded CLOSED raw position, before hard stop'),
        contact_load=number('Verified CONTACT load magnitude, 1..999'),
        close_torque=number('Verified CLOSING torque cap, above contact load, maximum 1000'),
        hold_torque=number('Verified HOLD torque cap, no greater than contact load'),
        max_current_raw=number('Verified CURRENT cutoff in raw register units, NOT mA'),
        speed=number('Verified gripper speed register value, 1..300'),
        acceleration=number('Verified gripper acceleration register value, 1..20'),
        commissioned=True).validate(settings.SERVO_IDS['gripper'])
    # Validate the angle mapping before saving, without opening the serial port.
    for ticks in (config.open_ticks, config.closed_ticks):
        angle = ((ticks - settings.ZERO_TICKS['gripper']) * 360.0 /
                 (settings.DIRECTIONS['gripper'] * settings.TICKS_PER_REVOLUTION))
        if not settings.ANGLE_LIMITS['gripper'][0] <= angle <= settings.ANGLE_LIMITS['gripper'][1]:
            raise ValueError('Endpoint outside arm_settings angle limits. Nothing saved.')
    print(json.dumps(asdict(config), indent=2))
    if input('Type VERIFIED to confirm repaired hardware and measured settings: ').strip() != 'VERIFIED':
        print('Nothing saved.')
        return
    path = Path(root) / CONFIG_NAME
    temp = path.with_suffix('.json.tmp')
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + datetime.now().strftime('.%Y%m%d-%H%M%S-%f.bak')))
    try:
        temp.write_text(json.dumps(asdict(config), indent=2) + '\n', encoding='utf-8')
        temp.replace(path)
    finally:
        if temp.exists():
            temp.unlink()
    print('Saved local gripper calibration. No movement performed. Test low over a padded tray.')


def inspect_gripper(connect, settings, check):
    """One read-only snapshot; never enable torque, move, or edit registers."""
    port, handler = connect()
    try:
        f = read_feedback(handler, settings.SERVO_IDS['gripper'], check)
        cap, result, error = handler.read2ByteTxRx(settings.SERVO_IDS['gripper'], 48)
        check(handler, result, error, 'Read gripper torque cap')
        print(f'ID {settings.SERVO_IDS["gripper"]}: position={f.position}, load={f.load}, '
              f'current_raw={f.current}, temperature={f.temperature}C, torque_cap={cap}')
        print('Read only. Load is not a measured jaw-force value.')
    finally:
        port.closePort()
