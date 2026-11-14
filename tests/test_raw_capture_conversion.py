"""Hardware-free tests: legacy angle capture conversion to raw servo ticks."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from raw_capture_conversion import convert_recording_to_raw

JOINTS = ("base", "shoulder", "elbow", "wrist", "gripper")


def example():
    return {
        "name": "legacy",
        "input_type": "angle",
        "description": "manually taught, do not replace",
        "points": [
            {"input_values": ["0", "90", "-90", "76", "60"], "note": "first"},
            {"input_values": ["0", "26", "-66", "-20", "0"], "note": "second"},
        ],
    }


class RawCaptureConversionTests(unittest.TestCase):
    def test_existing_waypoints_are_preserved_as_decoded_raw_values(self):
        original = example()
        poses = [
            dict(zip(JOINTS, (2000, 3000, 1000, 2500, 2750))),
            dict(zip(JOINTS, (2050, 2900, 1120, 2600, 2800))),
        ]
        result = convert_recording_to_raw(original, poses, JOINTS)
        self.assertEqual(result["input_type"], "raw")
        self.assertEqual(result["points"][0]["input_values"],
                         [2000, 3000, 1000, 2500, 2750])
        self.assertEqual(result["points"][1]["input_values"],
                         [2050, 2900, 1120, 2600, 2800])
        self.assertEqual(result["points"][0]["note"], "first")
        self.assertEqual(result["description"], original["description"])
        # No mutation: original bytes are not rewritten by this pure operation.
        self.assertEqual(original["input_type"], "angle")
        self.assertEqual(original["points"][0]["input_values"][1], "90")

    def test_rejects_invalid_raw_positions(self):
        original = example()
        base = dict(zip(JOINTS, (2000, 3000, 1000, 2500, 2750)))
        for bad in (-1, 4096, 3000.5, True):
            with self.subTest(bad=bad):
                altered = dict(base, shoulder=bad)
                with self.assertRaisesRegex(ValueError, "raw servo values"):
                    convert_recording_to_raw(original, [altered, base], JOINTS)
                self.assertEqual(original["input_type"], "angle")

    def test_rejects_mismatched_count_and_joints(self):
        original = example()
        good = dict(zip(JOINTS, (2000, 3000, 1000, 2500, 2750)))
        with self.assertRaisesRegex(ValueError, "count"):
            convert_recording_to_raw(original, [good], JOINTS)
        with self.assertRaisesRegex(ValueError, "missing/extra"):
            convert_recording_to_raw(original, [good, {"base": 2000}], JOINTS)

    def test_derived_attacks_stay_uneditable(self):
        original = example()
        original["derived_from"] = {"m": "test.json"}
        with self.assertRaisesRegex(ValueError, "Derived attack"):
            convert_recording_to_raw(original, [], JOINTS)


if __name__ == "__main__":
    unittest.main()
