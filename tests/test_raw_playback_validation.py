"""Hardware-free tests for raw competition playback validation."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from raw_playback_validation import validate_raw_pose


JOINTS = ("base", "shoulder", "elbow", "wrist", "gripper")


def pose(**overrides):
    result = dict(base=2048, shoulder=3160, elbow=3162,
                  wrist=3010, gripper=3400)
    result.update(overrides)
    return result


class RawPlaybackTests(unittest.TestCase):
    def test_all_joints_accept_recorded_ticks_without_angle_conversion(self):
        # These are raw ticks, not converted angles. Validity of the path is
        # checked separately during actual hardware calibration.
        self.assertIsNone(validate_raw_pose(pose(), JOINTS))

    def test_missing_extra_and_nonmapping_rejected(self):
        invalid = [
            {key: val for key, val in pose().items() if key != "elbow"},
            {**pose(), "unknown": 2000},
            [2048] * 5,
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_raw_pose(value, JOINTS)

    def test_ill_formed_or_out_of_register_range_rejected(self):
        for value in (-1, 4096, 3000.0, True, "3160", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_raw_pose(pose(shoulder=value), JOINTS)

    def test_invalid_joint_configuration_rejected(self):
        with self.assertRaises(ValueError):
            validate_raw_pose(pose(), ("base", "base"))


if __name__ == "__main__":
    unittest.main()
