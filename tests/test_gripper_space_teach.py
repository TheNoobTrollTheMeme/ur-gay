"""Hardware-free checks for Space-to-teach gripper input and diagnostics."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest import TestCase, main
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "gripper_space_teach", ROOT / "scripts" / "gripper_space_teach.py")
teach = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = teach
SPEC.loader.exec_module(teach)


def settings(angle_limits=(-90, 90)):
    return SimpleNamespace(
        JOINT_NAMES=("base", "shoulder", "elbow", "wrist", "gripper"),
        SERVO_IDS={"base": 1, "shoulder": 2, "elbow": 3, "wrist": 4,
                   "gripper": 5},
        ZERO_TICKS={"gripper": 2048},
        DIRECTIONS={"gripper": 1},
        TICKS_PER_REVOLUTION=4096,
        ANGLE_LIMITS={"gripper": angle_limits},
    )


class KeyTerminal:
    def __init__(self, *keys):
        self.keys = list(keys)

    def read_key(self, _timeout):
        return self.keys.pop(0) if self.keys else None


class TeachTests(TestCase):
    def test_space_is_teach_only_while_closing(self):
        marker = {"key": None}
        self.assertTrue(teach.stop_keys(KeyTerminal(" "), marker, teaching=True))
        self.assertEqual(marker["key"], "space")

    def test_space_outside_closing_cancels(self):
        marker = {"key": None}
        self.assertTrue(teach.stop_keys(KeyTerminal(" "), marker, teaching=False))
        self.assertEqual(marker["key"], "cancel")

    def test_q_never_becomes_teach(self):
        for key in ("q", "Q", "\x1b", "\x03"):
            with self.subTest(key=repr(key)):
                marker = {"key": None}
                self.assertTrue(teach.stop_keys(
                    KeyTerminal(key), marker, teaching=True))
                self.assertEqual(marker["key"], "cancel")

    def test_no_press_does_not_stop(self):
        marker = {"key": None}
        self.assertFalse(teach.stop_keys(KeyTerminal(), marker, teaching=True))
        self.assertIsNone(marker["key"])

    def test_one_tick_closed_endpoint_is_clipped_inward(self):
        cfg = teach.endpoints_within_limits(settings(), 1979, 3073, 50)
        self.assertEqual((cfg.open_ticks, cfg.closed_ticks), (1979, 3072))

    def test_large_mismatch_aborts(self):
        with self.assertRaises(teach.TrialError):
            teach.endpoints_within_limits(settings(), 1979, 3300, 50)

    def test_existing_valid_endpoint_is_not_widened(self):
        cfg = teach.endpoints_within_limits(settings(), 1979, 3072, 20)
        self.assertEqual(cfg.closed_ticks, 3072)
        self.assertEqual(cfg.requested_cap, 20)

    def test_teach_sample_captures_pre_release_feedback_without_commissioning(self):
        feedback = SimpleNamespace(position=2560, load=12, current=7,
                                   temperature=30)
        result = SimpleNamespace(peak_load=14, peak_current=8, elapsed=2.33)
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "samples.jsonl"
            with patch.object(teach, "LOG_FILE", destination):
                sample = teach.record_sample("cylinder", result, feedback, 25)
            self.assertTrue(destination.exists())
            stored = json.loads(destination.read_text().strip())
            self.assertEqual(stored["position"], 2560)
            self.assertEqual(stored["load"], 12)
            self.assertEqual(stored["current_raw"], 7)
            self.assertEqual(stored["trial_torque_cap"], 25)
            self.assertEqual(stored["source"], "space_pressed_during_closing")
            self.assertIs(stored["calibrated_force"], False)
            self.assertEqual(sample, stored)


if __name__ == "__main__":
    main()
