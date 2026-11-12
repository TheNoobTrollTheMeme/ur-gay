#!/usr/bin/env python3
"""Bounded gripper-only resistance experiment; does not commission playback.

Run from the installed arm repository, with the repaired arm fully supported:
  python3 scripts/gripper_force_test.py --open 1979 --closed 3073

The low output cap and learned current-rise detector are experimental limits,
not measured jaw-force limits. No recordings or calibration files are changed.
"""

import argparse
from dataclasses import dataclass, replace
import math
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gripper_guard import read_feedback


STOP_KEYS = {" ", "q", "Q", "\x03", "\x1b"}
STEP_TICKS = 8
COMMAND_PERIOD = 0.05
SPEED = 60
ACCELERATION = 5
POSITION_TOLERANCE = 8
MAX_POLL_GAP = 0.35
STALL_SECONDS = 0.6
MAX_DURATION = 30.0
MAX_TEMPERATURE = 55


class TrialError(RuntimeError):
    """A test could not proceed with its stated experimental limits."""


def position_angle(settings, ticks):
    """Convert a raw position using the installed arm's calibration."""
    return ((ticks - settings.ZERO_TICKS["gripper"]) * 360.0 /
            (settings.DIRECTIONS["gripper"] * settings.TICKS_PER_REVOLUTION))


def _allowed_raw_interval(settings):
    lo, hi = settings.ANGLE_LIMITS["gripper"]
    # Use exactly the same comparison as validation, including at integer edges
    # and for reversed servos. This is a diagnostic; it never changes endpoints.
    allowed = [ticks for ticks in range(4096)
               if lo <= position_angle(settings, ticks) <= hi]
    return f"{allowed[0]}..{allowed[-1]}" if allowed else "none in 0..4095"


def geometry_summary(settings, config):
    """Describe supplied endpoints without importing hardware helpers."""
    lo, hi = settings.ANGLE_LIMITS["gripper"]
    lines = [
        f"Settings: {getattr(settings, '__file__', '(module path unavailable)')}",
        f"Gripper servo ID: {settings.SERVO_IDS['gripper']}",
        f"Calibration: zero={settings.ZERO_TICKS['gripper']}, "
        f"direction={settings.DIRECTIONS['gripper']}, "
        f"ticks/revolution={settings.TICKS_PER_REVOLUTION}",
        f"Configured angle limits: {lo}..{hi} degrees (inclusive)",
        f"Configured raw interval: {_allowed_raw_interval(settings)} (inclusive)",
    ]
    for label, ticks in (("OPEN", config.open_ticks), ("CLOSED", config.closed_ticks)):
        angle = position_angle(settings, ticks)
        status = "OK" if 0 <= ticks <= 4095 and lo <= angle <= hi else "OUTSIDE LIMITS"
        lines.append(f"{label}: raw={ticks}, angle={angle:.12g} degrees -- {status}")
    return lines


@dataclass(frozen=True)
class TrialConfig:
    open_ticks: int
    closed_ticks: int
    requested_cap: int = 50

    def validate(self, settings):
        if any(type(value) is not int for value in
               (self.open_ticks, self.closed_ticks, self.requested_cap)):
            raise ValueError("Endpoints and cap must be integer register values.")
        if not 1 <= self.requested_cap <= 50:
            raise ValueError("Trial --cap must be 1..50. It is never increased automatically.")
        if not (0 <= self.open_ticks <= 4095 and 0 <= self.closed_ticks <= 4095):
            raise ValueError("Endpoints must be raw positions 0..4095.")
        if not 32 <= abs(self.closed_ticks - self.open_ticks) <= 2048:
            raise ValueError("Use measured, non-wrapping endpoints 32..2048 ticks apart.")
        names = list(settings.JOINT_NAMES)
        if "gripper" not in names or len(names) != len(set(names)):
            raise ValueError("Invalid joint names in arm_settings.")
        ids = [settings.SERVO_IDS[name] for name in names]
        if (len(ids) != len(set(ids)) or
                any(type(sid) is not int or not 1 <= sid <= 253 for sid in ids)):
            raise ValueError("Servo IDs must be valid and unique.")
        lo, hi = settings.ANGLE_LIMITS["gripper"]
        errors = []
        for label, ticks in (("OPEN", self.open_ticks), ("CLOSED", self.closed_ticks)):
            angle = position_angle(settings, ticks)
            if not lo <= angle <= hi:
                errors.append(f"{label} endpoint {ticks} maps to {angle:.12g} degrees, "
                              f"outside configured [{lo}, {hi}] degrees")
        if errors:
            raise ValueError("; ".join(errors) + ". Configured raw interval: " +
                             _allowed_raw_interval(settings) +
                             ". Run with --check for the loaded calibration.")
        return self


@dataclass(frozen=True)
class TrialResult:
    reason: str
    position: int
    peak_load: int
    peak_current: int
    elapsed: float


class BaselineFailed(TrialError):
    def __init__(self, result):
        self.result = result
        super().__init__(
            f"Empty-jaw baseline stopped ({result.reason}); object test cancelled. "
            "No torque increase was attempted.")


def current_rise_trip(empty_closing_peak):
    """A repeatable experimental rise detector, not a calibrated current limit."""
    if type(empty_closing_peak) is not int or not 0 <= empty_closing_peak <= 32767:
        raise ValueError("Empty-closing current peak is outside the raw feedback range.")
    trip = empty_closing_peak + max(3, math.ceil(empty_closing_peak * 0.25))
    if trip > 32767:
        raise ValueError("Empty-closing current is too high/saturated for the rise detector.")
    return trip


class ForceTester:
    def __init__(self, handler, settings, check, config, *, clock=time.monotonic,
                 sleep=time.sleep, stop_requested=lambda: False, report=print,
                 verified_current_cutoff=None):
        self.h = handler
        self.settings = settings
        self.check = check
        self.config = config.validate(settings)
        self.clock = clock
        self.sleep = sleep
        self.stop_requested = stop_requested
        self.report = report
        self.id = settings.SERVO_IDS["gripper"]
        self.effective_cap = None
        self.load_trip = None
        self.owned = False
        self.enabled = False
        self.prepared = False
        self.released_verified = True
        self.last_feedback_at = None
        self.last_command_at = None
        self.last_feedback = None
        if (verified_current_cutoff is not None and
                (type(verified_current_cutoff) is not int or
                 not 1 <= verified_current_cutoff <= 32767)):
            raise ValueError("Existing current cutoff must be a positive raw register value.")
        self.verified_current_cutoff = verified_current_cutoff

    def _read1(self, sid, address, action):
        value, result, error = self.h.read1ByteTxRx(sid, address)
        self.check(self.h, result, error, action)
        return value

    def _read2(self, sid, address, action):
        value, result, error = self.h.read2ByteTxRx(sid, address)
        self.check(self.h, result, error, action)
        return value

    def _write(self, result, action):
        self.check(self.h, *result, action)

    def _feedback(self):
        before = self.clock()
        feedback = read_feedback(self.h, self.id, self.check)
        status = self._read1(self.id, 65, "Read gripper status")
        now = self.clock()
        if (now - before > MAX_POLL_GAP or
                (self.last_feedback_at is not None and
                 now - self.last_feedback_at > MAX_POLL_GAP)):
            raise TrialError("Gripper feedback deadline missed; stopping the test.")
        self.last_feedback_at = now
        self.last_feedback = feedback
        if status != 0:
            raise TrialError(f"Gripper status fault: register 65 = {status}.")
        if feedback.temperature >= MAX_TEMPERATURE:
            raise TrialError(f"Gripper temperature reached {MAX_TEMPERATURE} C.")
        if (self.verified_current_cutoff is not None and
                feedback.current >= self.verified_current_cutoff):
            raise TrialError("Existing configured gripper current cutoff reached.")
        lo, hi = sorted((self.config.open_ticks, self.config.closed_ticks))
        if not lo - POSITION_TOLERANCE <= feedback.position <= hi + POSITION_TOLERANCE:
            raise TrialError("Measured gripper position is outside the supplied travel.")
        return feedback

    def prepare(self):
        """Reject powered joints; write only the gripper after all preflight reads."""
        if self.enabled or self.prepared:
            raise TrialError("Release the gripper before preparing another test.")
        self.owned = False
        self.released_verified = True
        self.last_feedback_at = None
        try:
            for joint in self.settings.JOINT_NAMES:
                torque = self._read1(self.settings.SERVO_IDS[joint], 40,
                                     f"Read {joint} torque state")
                if torque != 0:
                    raise TrialError(
                        f"{joint} torque is not OFF. Stop the other controller and "
                        "support the arm before releasing it. This test changed no joints.")
            mode = self._read1(self.id, 33, "Read gripper operating mode")
            if mode != 0:
                raise TrialError("Gripper must already be in position mode 0.")
            hardware_max = self._read2(self.id, 16, "Read gripper maximum output")
            old_cap = self._read2(self.id, 48, "Read current gripper output cap")
            if not (1 <= hardware_max <= 1000 and 1 <= old_cap <= 1000):
                raise TrialError("Existing gripper output caps must be valid and nonzero.")
            self.effective_cap = min(50, self.config.requested_cap, old_cap, hardware_max)
            self.load_trip = max(1, math.ceil(self.effective_cap * 0.8))
            feedback = self._feedback()
            if self.stop_requested():
                raise TrialError("Test cancelled before enabling the gripper.")

            # Only now is this controller permitted to touch the gripper.
            self.owned = True
            self.released_verified = False
            self._write(self.h.write1ByteTxRx(self.id, 40, 0), "Keep gripper torque off")
            if self._read1(self.id, 40, "Verify gripper torque off") != 0:
                raise TrialError("Gripper torque-off verification failed.")
            self._write(self.h.write2ByteTxRx(self.id, 48, self.effective_cap),
                        "Set experimental gripper output cap")
            if self._read2(self.id, 48, "Verify experimental output cap") != self.effective_cap:
                raise TrialError("Experimental output cap did not read back correctly.")
            # Re-read immediately before seeding, so enabling cannot pursue an old target.
            feedback = self._feedback()
            self._write(self.h.WritePosEx(self.id, feedback.position, SPEED, ACCELERATION),
                        "Seed measured gripper position")
            self.last_command_at = self.clock()
            if self.stop_requested():
                raise TrialError("Test cancelled before enabling the gripper.")
            self._write(self.h.write1ByteTxRx(self.id, 40, 1), "Enable gripper only")
            self.enabled = True
            if self._read1(self.id, 40, "Verify gripper torque enabled") != 1:
                raise TrialError("Gripper enable verification failed.")
            self.prepared = True
        except BaseException as error:
            self.release()
            if isinstance(error, (KeyboardInterrupt, SystemExit, TrialError)):
                raise
            raise TrialError(str(error)) from error

    def release(self):
        """Release only this gripper; leave the reduced cap in place."""
        if not self.owned or self.released_verified:
            return
        self.prepared = False
        try:
            self._write(self.h.write1ByteTxRx(self.id, 40, 0), "Release gripper after trial")
            if self._read1(self.id, 40, "Verify released gripper") != 0:
                raise TrialError("Gripper torque still reports enabled.")
            self.enabled = False
            self.released_verified = True
        except BaseException as error:
            self.report("GRIPPER RELEASE FAILED. Use the physical power stop.")
            raise TrialError(f"Gripper release could not be verified: {error}") from error

    def move(self, target, phase, current_trip=None):
        """Run one bounded move, then release on every return or fault."""
        if not self.prepared:
            raise TrialError("Call prepare() before each trial move.")
        started = self.clock()
        peak_load = 0
        peak_current = 0
        feedback = self.last_feedback

        def result(reason):
            return TrialResult(reason, feedback.position, peak_load, peak_current,
                               self.clock() - started)

        try:
            lo, hi = sorted((self.config.open_ticks, self.config.closed_ticks))
            if type(target) is not int or not lo <= target <= hi:
                raise TrialError("Trial target is outside the supplied endpoints.")
            if current_trip is not None and (type(current_trip) is not int or current_trip <= 0):
                raise TrialError("Experimental current-rise trip must be a positive raw value.")
            direction = 1 if target > feedback.position else -1
            progress_position = feedback.position
            progress_at = started
            last_goal = feedback.position
            next_report = started
            while True:
                if self.stop_requested():
                    return result("cancelled")
                feedback = self._feedback()
                now = self.clock()
                peak_load = max(peak_load, feedback.load)
                peak_current = max(peak_current, feedback.current)
                if self.stop_requested():
                    return result("cancelled")
                if feedback.load >= self.load_trip:
                    return result("resistance_load")
                if current_trip is not None and feedback.current >= current_trip:
                    return result("resistance_current")
                if abs(feedback.position - target) <= POSITION_TOLERANCE:
                    return result("endpoint")
                if now - started >= MAX_DURATION:
                    return result("timeout")
                if direction * (feedback.position - progress_position) >= 2:
                    progress_position = feedback.position
                    progress_at = now
                elif now - progress_at >= STALL_SECONDS:
                    return result("stall")
                if now >= next_report:
                    self.report(f"{phase}: position={feedback.position}, load={feedback.load}, "
                                f"current_raw={feedback.current}")
                    next_report = now + 0.5
                if now - self.last_command_at >= COMMAND_PERIOD:
                    candidate = feedback.position + direction * STEP_TICKS
                    goal = min(target, candidate) if direction > 0 else max(target, candidate)
                    goal = min(hi, max(lo, goal))
                    # Do not chase sensor jitter back toward an earlier target.
                    if direction * (goal - last_goal) > 0:
                        self._write(self.h.WritePosEx(self.id, goal, SPEED, ACCELERATION),
                                    "Send small gripper-only trial step")
                        self.last_command_at = self.clock()
                        last_goal = goal
                self.sleep(0.01)
        except KeyboardInterrupt:
            return result("cancelled")
        except Exception as error:
            if isinstance(error, TrialError):
                raise
            raise TrialError(str(error)) from error
        finally:
            self.release()


def describe_result(phase, result):
    descriptions = {
        "endpoint": "ENDPOINT REACHED (no resistance stop)",
        "resistance_load": "RESISTANCE DETECTED (experimental load trip)",
        "resistance_current": "RESISTANCE DETECTED (experimental current-rise trip)",
        "stall": "STALLED / INSUFFICIENT DRIVE (not proof of a grasp)",
        "cancelled": "CANCELLED",
        "timeout": "TIME LIMIT REACHED",
    }
    return (f"{phase}: {descriptions[result.reason]}; position={result.position}, "
            f"peak_load={result.peak_load}, peak_current_raw={result.peak_current}, "
            f"elapsed={result.elapsed:.1f}s. Gripper torque is OFF.")


def run_baseline(tester):
    """Do not proceed past any unsuccessful empty-jaw move."""
    results = []
    for phase, target in (("Empty opening", tester.config.open_ticks),
                          ("Empty closing", tester.config.closed_ticks),
                          ("Empty reopening", tester.config.open_ticks)):
        tester.prepare()
        outcome = tester.move(target, phase)
        results.append(outcome)
        tester.report(describe_result(phase, outcome))
        if outcome.reason != "endpoint":
            raise BaselineFailed(outcome)
    return results


def _existing_limits(settings):
    from gripper_guard import CONFIG_NAME, load_config
    if not (ROOT / CONFIG_NAME).exists():
        return None
    # An existing protection file must validate; never discard its limits silently.
    return load_config(ROOT, settings)


def _confirm(prompt):
    answer = input(prompt).strip().lower()
    if answer not in ("", "y", "yes"):
        raise TrialError("Test cancelled. No further movement requested.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--open", dest="open_ticks", type=int, required=True,
                        help="Measured empty open raw position")
    parser.add_argument("--closed", dest="closed_ticks", type=int, required=True,
                        help="Measured empty closed position before the hard stop")
    parser.add_argument("--cap", type=int, default=50,
                        help="Experimental output cap 1..50/1000 (default 50; never auto-raised)")
    parser.add_argument("--check", action="store_true",
                        help="Print endpoint/calibration checks only; no serial connection or motion")
    args = parser.parse_args(argv)
    # The diagnostic path needs only settings: no SDK, serial port, or terminal.
    import arm_settings as settings

    config = TrialConfig(args.open_ticks, args.closed_ticks, args.cap)
    if args.check:
        print("GRIPPER ENDPOINT CHECK -- no serial connection or movement.")
        try:
            for line in geometry_summary(settings, config):
                print(line)
            config.validate(settings)
        except (ValueError, KeyError, AttributeError, TypeError, ZeroDivisionError) as error:
            print(f"CHECK FAILED: {error}")
            return 1
        print("Endpoint configuration is valid. Physical clearance and force are not tested.")
        return 0

    port = None
    tester = None
    try:
        config.validate(settings)
        from arm_helpers import connect, check_result
        from terminal_keys import RawTerminal

        if not sys.stdin.isatty():
            raise TrialError("Run this test in an interactive terminal so stop keys work.")
        print("GRIPPER-ONLY RESISTANCE TRIAL -- no calibration or recordings are changed.")
        print("Use repaired hardware. Support the whole arm with every servo torque OFF.")
        print("Stop other arm programs. Keep fingers clear; Space / Q / Esc / Ctrl+C stops.")
        print("The output cap is a low experiment, NOT a verified jaw-force limit.")
        _confirm("Empty the jaws and clear their travel. Enter starts open-close-open; Q cancels: ")
        existing = _existing_limits(settings)
        current_cutoff = existing.max_current_raw if existing is not None else None
        if existing is not None:
            config = replace(config, requested_cap=min(config.requested_cap,
                                                       existing.close_torque))
        port, handler = connect()
        tester = ForceTester(handler, settings, check_result, config,
                             verified_current_cutoff=current_cutoff)
        if current_cutoff is not None:
            print(f"Also honoring existing configured current cutoff: {current_cutoff} raw.")
        with RawTerminal() as terminal:
            tester.stop_requested = lambda: terminal.read_key(0.0) in STOP_KEYS
            baseline = run_baseline(tester)
        trip = current_rise_trip(baseline[1].peak_current)
        print(f"Trial output cap={tester.effective_cap}/1000; load trip={tester.load_trip}; "
              f"current-rise trip={trip} raw (empty-closing peak={baseline[1].peak_current}).")
        print("Current-rise trip is an experimental detector, not a verified force/current safety limit.")
        print("The object must fit between the open jaws and rest on a support throughout.")
        _confirm("Place the small cylinder between the jaws, remove your hands, then Enter; Q cancels: ")
        with RawTerminal() as terminal:
            tester.stop_requested = lambda: terminal.read_key(0.0) in STOP_KEYS
            tester.prepare()
            outcome = tester.move(config.closed_ticks, "Object closing", current_trip=trip)
        print(describe_result("Object closing", outcome))
        print("This trial releases immediately. Playback protection has NOT been commissioned.")
        return 0 if outcome.reason in ("resistance_load", "resistance_current") else 1
    except (KeyboardInterrupt, EOFError):
        print("\nTest cancelled.")
        return 1
    except (TrialError, ValueError) as error:
        print(f"STOP: {error}")
        return 1
    finally:
        if tester is not None:
            try:
                tester.release()
            except TrialError as error:
                print(f"STOP: {error}")
        if port is not None:
            port.closePort()


if __name__ == "__main__":
    raise SystemExit(main())
