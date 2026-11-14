"""Hardware-free tests for the read-only raw debug capture."""
import ast
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import importlib.util
path = Path(__file__).resolve().parents[1] / "scripts" / "07_raw_debug_capture.py"
spec = importlib.util.spec_from_file_location("raw_debug_capture", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class RawCaptureTests(unittest.TestCase):
    def test_snapshot_preserves_angle_outlier_without_conversion(self):
        values = dict(base=2000, shoulder=3500, elbow=1050, wrist=2020, gripper=3100)
        names = tuple(values)
        snapshot = module.make_snapshot(values, names, timestamp="TEST")
        self.assertEqual(snapshot["positions_raw"], values)
        self.assertEqual(snapshot["angle_limits_checked"], False)
        self.assertFalse(snapshot["playback_eligible"])
        self.assertEqual(snapshot["torque_command"], "DISABLED")

    def test_reading_is_not_rewritten_to_motion_format(self):
        snapshot = module.make_snapshot({"shoulder": 3500}, ["shoulder"],
                                        timestamp="TEST")
        with tempfile.TemporaryDirectory() as directory:
            path = module.save_snapshot(snapshot, directory)
            written = json.loads(path.read_text())
            self.assertEqual(written["kind"], "torque_off_raw_debug_capture")
            self.assertNotIn("points", written)
            self.assertNotIn("input_type", written)
            self.assertTrue(path.name.startswith("raw_debug_"))

    def test_reject_incomplete_or_bad_sensor_read(self):
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            module.make_snapshot({"base": 123}, ["base", "shoulder"])
        with self.assertRaisesRegex(ValueError, "Non-integer"):
            module.make_snapshot({"shoulder": "97.73"}, ["shoulder"])

    def test_no_servo_target_write_functions(self):
        tree = ast.parse(path.read_text())
        names = {node.func.id for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
        attributes = {node.func.attr for node in ast.walk(tree)
                      if isinstance(node, ast.Call) and
                      isinstance(node.func, ast.Attribute)}
        self.assertFalse(names & {"move_all_sync", "enable_all_torque",
                                  "SyncWritePosEx"})
        self.assertNotIn("SyncWritePosEx", attributes)


if __name__ == "__main__":
    unittest.main()
