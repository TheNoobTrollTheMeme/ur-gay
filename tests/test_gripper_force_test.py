"""Hardware-free integration checks for the bounded gripper commissioning trial."""
import importlib.util
from dataclasses import asdict
import io
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gripper_guard import Config, CONFIG_NAME

SPEC = importlib.util.spec_from_file_location(
    "gripper_force_test", ROOT / "scripts" / "gripper_force_test.py")
trial = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = trial
SPEC.loader.exec_module(trial)


def check(_handler, result, error, action):
    if result or error:
        raise RuntimeError(f"{action}: communication failure {result}/{error}")


def settings():
    return SimpleNamespace(
        SERVO_IDS={"base": 1, "shoulder": 2, "elbow": 3, "wrist": 4, "gripper": 5},
        JOINT_NAMES=("base", "shoulder", "elbow", "wrist", "gripper"),
        ZERO_TICKS={"gripper": 2048}, DIRECTIONS={"gripper": 1},
        TICKS_PER_REVOLUTION=4096, ANGLE_LIMITS={"gripper": (-180, 180)},
    )


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Hardware:
    """Minimal STS SDK fake with independent actual pose, goals and registers."""
    def __init__(self, clock, position=1979):
        self.clock = clock
        self.position = position
        self.goal = position
        self.torque = {i: 0 for i in range(1, 6)}
        self.cap = 1000
        self.maximum = 1000
        self.mode = 0
        self.load = 0
        self.current = 0
        self.temperature = 30
        self.status = 0
        self.writes = []
        self.goals = []
        self.reads = []
        self.freeze = False
        self.advance_by = 8
        self.advance_interval = 0.0
        self.last_advance = -1.0
        self.bad_cap_readback = False
        self.cap_written = False
        self.fail_position_when_enabled = False
        self.bad_load_when_enabled = False
        self.hot_when_enabled = False
        self.fault_status_when_enabled = False
        self.enabled_position_reads = 0
        self.delay_enabled_read = None
        self.contact_at = None
        self.contact_load = 45
        self.current_at = None
        self.contact_current = 8

    def ReadPos(self, sid):
        self.reads.append(("pos", sid))
        if self.torque[sid]:
            self.enabled_position_reads += 1
            if self.delay_enabled_read == self.enabled_position_reads:
                self.clock.now += 1.0
            if self.fail_position_when_enabled:
                return self.position, 1, 0
            if not self.freeze and self.clock() - self.last_advance >= self.advance_interval:
                difference = self.goal - self.position
                step = min(abs(difference), self.advance_by)
                self.position += step if difference >= 0 else -step
                self.last_advance = self.clock()
        return self.position, 0, 0

    def read1ByteTxRx(self, sid, address):
        self.reads.append((address, sid))
        if address == 40:
            return self.torque[sid], 0, 0
        if address == 33:
            return self.mode, 0, 0
        if address == 63:
            value = 55 if self.hot_when_enabled and self.torque[sid] else self.temperature
            return value, 0, 0
        if address == 65:
            value = 32 if self.fault_status_when_enabled and self.torque[sid] else self.status
            return value, 0, 0
        raise AssertionError(f"Unexpected 1-byte read: {sid}/{address}")

    def read2ByteTxRx(self, sid, address):
        self.reads.append((address, sid))
        if address == 16:
            return self.maximum, 0, 0
        if address == 48:
            offset = 1 if self.bad_cap_readback and self.cap_written else 0
            return self.cap + offset, 0, 0
        if address == 60:
            if self.bad_load_when_enabled and self.torque[sid]:
                return 2048, 0, 0
            value = self.load
            if self.contact_at is not None and self.position >= self.contact_at:
                value = self.contact_load
            return value, 0, 0
        if address == 69:
            value = self.current
            if self.current_at is not None and self.position >= self.current_at:
                value = self.contact_current
            return value, 0, 0
        raise AssertionError(f"Unexpected 2-byte read: {sid}/{address}")

    def write1ByteTxRx(self, sid, address, value):
        self.writes.append((1, sid, address, value))
        if address != 40:
            raise AssertionError(f"Unexpected 1-byte write: {sid}/{address}")
        self.torque[sid] = value
        return 0, 0

    def write2ByteTxRx(self, sid, address, value):
        self.writes.append((2, sid, address, value))
        if address != 48:
            raise AssertionError(f"Unexpected 2-byte write: {sid}/{address}")
        self.cap = value
        self.cap_written = True
        return 0, 0

    def WritePosEx(self, sid, target, speed, acceleration):
        self.writes.append(("goal", sid, target, (speed, acceleration)))
        self.goals.append((self.position, target, self.torque[sid]))
        self.goal = target
        return 0, 0


class ForceTrialTests(unittest.TestCase):
    def make(self, *, open_ticks=1979, closed_ticks=3073, cap=50, position=None,
             stop_requested=None):
        self.clock = Clock()
        self.hw = Hardware(self.clock, open_ticks if position is None else position)
        self.messages = []
        cfg = trial.TrialConfig(open_ticks, closed_ticks, requested_cap=cap)
        self.tester = trial.ForceTester(
            self.hw, settings(), check, cfg, clock=self.clock, sleep=self.clock.sleep,
            stop_requested=stop_requested or (lambda: False), report=self.messages.append)
        return self.tester

    def assert_released(self):
        self.assertEqual(self.hw.torque[5], 0)
        self.assertTrue(all(write[1] == 5 for write in self.hw.writes), self.hw.writes)

    def test_empty_move_zero_current_keeps_other_joints_off_and_bounds_each_goal(self):
        tester = self.make()
        tester.prepare()
        result = tester.move(3073, "empty closing")
        self.assertEqual(result.reason, "endpoint")
        self.assertEqual(result.peak_current, 0)
        self.assert_released()
        self.assertEqual(self.hw.torque, {i: 0 for i in range(1, 6)})
        self.assertGreater(len(self.hw.goals), 100)
        for measured, target, _enabled in self.hw.goals:
            self.assertLessEqual(abs(target - measured), 8)
            self.assertTrue(1979 <= target <= 3073)
        self.assertTrue(all(value <= 50 for size, _sid, address, value in self.hw.writes
                            if size == 2 and address == 48))

    def test_seed_position_precedes_gripper_enable(self):
        tester = self.make(position=2400)
        tester.prepare()
        enable_at = self.hw.writes.index((1, 5, 40, 1))
        seed_at = next(i for i, write in enumerate(self.hw.writes) if write[0] == "goal")
        cap_at = next(i for i, write in enumerate(self.hw.writes) if write[:3] == (2, 5, 48))
        self.assertLess(cap_at, seed_at)
        self.assertLess(seed_at, enable_at)
        self.assertEqual(self.hw.goals[0][:2], (2400, 2400))
        tester.release()
        self.assert_released()

    def test_preflight_refuses_an_active_other_joint_without_any_writes(self):
        tester = self.make()
        self.hw.torque[2] = 1
        with self.assertRaises(trial.TrialError):
            tester.prepare()
        self.assertEqual(self.hw.writes, [])
        self.assertEqual(self.hw.torque[2], 1)

    def test_preflight_refuses_already_active_gripper_without_writes(self):
        tester = self.make()
        self.hw.torque[5] = 1
        with self.assertRaises(trial.TrialError):
            tester.prepare()
        self.assertEqual(self.hw.writes, [])

    def test_mode_mismatch_refuses_without_mutation(self):
        tester = self.make()
        self.hw.mode = 1
        with self.assertRaises(trial.TrialError):
            tester.prepare()
        self.assertEqual(self.hw.writes, [])

    def test_existing_lower_cap_is_not_raised(self):
        tester = self.make()
        self.hw.cap = 20
        self.hw.maximum = 15
        tester.prepare()
        self.assertEqual(tester.effective_cap, 15)
        self.assertEqual(self.hw.cap, 15)
        tester.release()
        self.assert_released()
        self.assertEqual(self.hw.cap, 15)

    def test_failed_cap_readback_prevents_enable(self):
        tester = self.make()
        self.hw.bad_cap_readback = True
        with self.assertRaises(trial.TrialError):
            tester.prepare()
        self.assertNotIn((1, 5, 40, 1), self.hw.writes)
        self.assert_released()

    def test_pose_outside_measured_travel_refuses_without_writes(self):
        tester = self.make(position=1800)
        with self.assertRaises(trial.TrialError):
            tester.prepare()
        self.assertEqual(self.hw.writes, [])

    def test_load_rise_stops_before_closed_endpoint(self):
        tester = self.make()
        self.hw.contact_at = 2050
        tester.prepare()
        result = tester.move(3073, "object closing")
        self.assertNotEqual(result.reason, "endpoint")
        self.assertGreaterEqual(result.peak_load, 40)
        self.assertLess(result.position, 3073)
        self.assertLessEqual(max(goal[1] for goal in self.hw.goals), result.position + 8)
        self.assert_released()

    def test_current_rise_stops_even_when_load_is_below_trial_threshold(self):
        tester = self.make()
        self.hw.current_at = 2050
        tester.prepare()
        result = tester.move(3073, "object closing", current_trip=3)
        self.assertNotEqual(result.reason, "endpoint")
        self.assertEqual(result.peak_load, 0)
        self.assertGreaterEqual(result.peak_current, 3)
        self.assertLess(result.position, 3073)
        self.assert_released()

    def test_stall_with_zero_effort_is_not_reported_as_grasp(self):
        tester = self.make()
        self.hw.freeze = True
        tester.prepare()
        result = tester.move(3073, "object closing")
        self.assertIn("stall", result.reason.lower())
        self.assertNotIn("grasp", result.reason.lower())
        self.assertEqual(result.position, 1979)
        self.assertLess(self.clock(), 2.0)
        self.assert_released()

    def test_feedback_communication_failure_releases_gripper(self):
        tester = self.make()
        tester.prepare()
        self.hw.fail_position_when_enabled = True
        with self.assertRaises((trial.TrialError, RuntimeError)):
            tester.move(3073, "empty closing")
        self.assert_released()

    def test_invalid_feedback_releases_gripper(self):
        tester = self.make()
        tester.prepare()
        self.hw.bad_load_when_enabled = True
        with self.assertRaises((trial.TrialError, RuntimeError)):
            tester.move(3073, "empty closing")
        self.assert_released()

    def test_temperature_cutoff_releases_gripper(self):
        tester = self.make()
        tester.prepare()
        self.hw.hot_when_enabled = True
        with self.assertRaises(trial.TrialError):
            tester.move(3073, "empty closing")
        self.assert_released()

    def test_hardware_fault_status_releases_gripper(self):
        tester = self.make()
        tester.prepare()
        self.hw.fault_status_when_enabled = True
        with self.assertRaises(trial.TrialError):
            tester.move(3073, "empty closing")
        self.assert_released()

    def test_feedback_watchdog_releases_gripper(self):
        tester = self.make()
        tester.prepare()
        self.hw.delay_enabled_read = self.hw.enabled_position_reads + 2
        with self.assertRaises(trial.TrialError):
            tester.move(3073, "empty closing")
        self.assert_released()

    def test_current_rise_threshold_is_relative_to_empty_closing_measurement(self):
        self.assertEqual(trial.current_rise_trip(0), 3)
        self.assertEqual(trial.current_rise_trip(4), 7)
        self.assertEqual(trial.current_rise_trip(20), 25)

    def test_cancellation_stops_more_targets_and_releases(self):
        tester = self.make(stop_requested=lambda: len(self.hw.goals) >= 3)
        tester.prepare()
        result = tester.move(3073, "empty closing")
        self.assertEqual(result.reason, "cancelled")
        self.assertLessEqual(len(self.hw.goals), 3)
        self.assert_released()

    def test_total_timeout_stops_a_servo_that_keeps_creeping(self):
        tester = self.make()
        self.hw.advance_by = 1
        self.hw.advance_interval = 0.2
        tester.prepare()
        result = tester.move(3073, "empty closing")
        self.assertEqual(result.reason, "timeout")
        self.assertLess(result.position, 3073)
        self.assertLess(self.clock(), 31.0)
        self.assert_released()

    def test_empty_baseline_failure_prevents_a_third_move_or_torque_escalation(self):
        tester = self.make()
        self.hw.contact_at = 2050
        with self.assertRaises(trial.BaselineFailed):
            trial.run_baseline(tester)
        enable_writes = [write for write in self.hw.writes if write == (1, 5, 40, 1)]
        self.assertEqual(len(enable_writes), 2)
        self.assertTrue(all(value <= 50 for size, _sid, address, value in self.hw.writes
                            if size == 2 and address == 48))
        self.assert_released()

    def test_baseline_finishes_open_with_all_torque_off(self):
        tester = self.make(position=3073)
        results = trial.run_baseline(tester)
        self.assertEqual([result.reason for result in results], ["endpoint"] * 3)
        self.assertLessEqual(abs(self.hw.position - 1979), 8)
        self.assert_released()

    def test_negative_closing_direction_also_uses_bounded_steps(self):
        tester = self.make(open_ticks=3073, closed_ticks=1979)
        tester.prepare()
        result = tester.move(1979, "empty closing")
        self.assertEqual(result.reason, "endpoint")
        for measured, target, _enabled in self.hw.goals:
            self.assertLessEqual(abs(target - measured), 8)
            self.assertTrue(1979 <= target <= 3073)
        self.assert_released()

    def test_out_of_bounds_target_never_commands_it(self):
        tester = self.make()
        tester.prepare()
        with self.assertRaises((trial.TrialError, ValueError)):
            tester.move(3100, "object closing")
        self.assertTrue(all(1979 <= target <= 3073 for _measured, target, _enabled in self.hw.goals))
        self.assert_released()

    def test_late_prepare_cancellation_prevents_enable_after_seeding(self):
        tester = self.make(stop_requested=lambda: bool(self.hw.goals))
        with self.assertRaises(trial.TrialError):
            tester.prepare()
        self.assertEqual(len(self.hw.goals), 1)
        self.assertNotIn((1, 5, 40, 1), self.hw.writes)
        self.assert_released()

    def test_unreachable_current_rise_threshold_is_rejected(self):
        for peak in (32767, 32768, -1):
            with self.subTest(peak=peak), self.assertRaises(ValueError):
                trial.current_rise_trip(peak)


class ExistingLimitsTests(unittest.TestCase):
    def verified_config(self):
        return Config(servo_id=5, open_ticks=1979, closed_ticks=3073,
                      contact_load=12, close_torque=20, hold_torque=10,
                      max_current_raw=50, speed=60, acceleration=5, commissioned=True)

    def test_absent_config_allows_explicit_uncommissioned_trial(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(trial, "ROOT", Path(directory)):
                self.assertIsNone(trial._existing_limits(settings()))
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_present_invalid_config_is_not_silently_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / CONFIG_NAME
            source.write_text('{"commissioned": false}')
            before = source.read_bytes()
            with patch.object(trial, "ROOT", root), self.assertRaises(ValueError):
                trial._existing_limits(settings())
            self.assertEqual(source.read_bytes(), before)

    def test_valid_config_is_loaded_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / CONFIG_NAME
            expected = self.verified_config()
            source.write_text(json.dumps(asdict(expected)))
            before = source.read_bytes()
            with patch.object(trial, "ROOT", root):
                loaded = trial._existing_limits(settings())
            self.assertEqual(loaded, expected)
            self.assertEqual(source.read_bytes(), before)

    def run_cli_with_config(self, root):
        clock = Clock()
        hw = Hardware(clock, position=3073)
        closed = []
        port = SimpleNamespace(closePort=lambda: closed.append(True))
        connections = []

        def connect():
            connections.append(True)
            return port, hw

        class Terminal:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read_key(self, _timeout):
                return None

        actual_tester = trial.ForceTester
        captured_configs = []

        def tester_factory(handler, setting, checker, config, **kwargs):
            captured_configs.append(config)
            return actual_tester(handler, setting, checker, config,
                                 clock=clock, sleep=clock.sleep, **kwargs)

        def answer(prompt):
            if "Place the small cylinder" in prompt:
                hw.contact_at = 2050
            return ""

        modules = {
            "arm_settings": settings(),
            "arm_helpers": SimpleNamespace(connect=connect, check_result=check),
            "terminal_keys": SimpleNamespace(RawTerminal=Terminal),
        }
        with patch.dict(sys.modules, modules), \
                patch.object(trial, "ROOT", root), \
                patch.object(trial, "ForceTester", side_effect=tester_factory), \
                patch.object(sys.stdin, "isatty", return_value=True), \
                patch("builtins.input", side_effect=answer), \
                patch("sys.stdout", new_callable=io.StringIO):
            result = trial.main(["--open", "1979", "--closed", "3073"])
        return result, hw, connections, closed, captured_configs

    def test_main_honors_existing_lower_cap_after_power_cycle_without_rewriting_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / CONFIG_NAME
            source.write_text(json.dumps(asdict(self.verified_config())))
            before = source.read_bytes()
            result, hw, connections, closed, configs = self.run_cli_with_config(root)
            self.assertEqual(result, 0)
            self.assertEqual(len(connections), 1)
            self.assertEqual(len(closed), 1)
            self.assertEqual(configs[0].requested_cap, 20)
            caps = [write[3] for write in hw.writes if write[:3] == (2, 5, 48)]
            self.assertTrue(caps)
            self.assertTrue(all(cap <= 20 for cap in caps))
            self.assertEqual(hw.torque[5], 0)
            self.assertEqual(source.read_bytes(), before)
            self.assertEqual([entry.name for entry in root.iterdir()], [CONFIG_NAME])

    def test_main_invalid_existing_config_aborts_before_connecting(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / CONFIG_NAME
            source.write_text("not-json")
            result, hw, connections, closed, configs = self.run_cli_with_config(root)
            self.assertEqual(result, 1)
            self.assertEqual(connections, [])
            self.assertEqual(closed, [])
            self.assertEqual(configs, [])
            self.assertEqual(hw.writes, [])
            self.assertEqual(source.read_text(), "not-json")


if __name__ == "__main__":
    unittest.main()
