"""No-motor regression tests for legacy angle-parser-free raw recording saves."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from validated_raw_save import save_raw_recording, validate_raw_recording

JOINTS = ("base", "shoulder", "elbow", "wrist", "gripper")

def recording(shoulder=3500):
    return {
        "schema_version": 1, "type": "pose_sequence", "input_type": "raw",
        "name": "tested_route",
        "joint_order": [{"servo_id": i+1, "joint": name}
                        for i, name in enumerate(JOINTS)],
        "points": [{"input_values": [2000, shoulder, 900, 2000, 3100]}]
    }

class RawSaveTests(unittest.TestCase):
    def test_raw_shoulder_capture_saves_without_angle_parser(self):
        data = recording(3500)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "tested_route.json"
            self.assertIsNone(save_raw_recording(path, data, JOINTS))
            stored = json.loads(path.read_text())
            self.assertEqual(stored["points"][0]["input_values"][1], 3500)
            self.assertEqual(stored["input_type"], "raw")

    def test_existing_file_is_backed_up_without_overwriting_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "tested_route.json"
            path.write_text('{"old": "angles"}')
            backup = save_raw_recording(path, recording(), JOINTS)
            self.assertIsNotNone(backup)
            self.assertEqual(json.loads(backup.read_text()), {"old": "angles"})
            self.assertEqual(json.loads(path.read_text())["input_type"], "raw")

    def test_broken_reading_fails_without_mutating_original(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "tested_route.json"
            path.write_text("original")
            for bad in (-1, 4096, 3.5, "3500", True):
                with self.subTest(bad=bad):
                    with self.assertRaises(ValueError):
                        save_raw_recording(path, recording(bad), JOINTS)
                    self.assertEqual(path.read_text(), "original")

    def test_missing_raw_position_rejected(self):
        data = recording()
        data["points"][0]["input_values"].pop()
        with self.assertRaisesRegex(ValueError, "expected 5"):
            validate_raw_recording(data, JOINTS)

if __name__ == "__main__":
    unittest.main()
