"""Hardware-free tests of user-adjustable playback settings."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from playback_speed import (DEFAULT_SPEED, DEFAULT_ACCELERATION,
                            prompt_playback_settings, validate_playback_settings)


class PlaybackSpeedTests(unittest.TestCase):
    def test_defaults_are_preserved(self):
        self.assertEqual(validate_playback_settings(DEFAULT_SPEED, DEFAULT_ACCELERATION),
                         (300, 20))
        answers = iter(("", ""))
        self.assertEqual(prompt_playback_settings(input_fn=lambda _: next(answers)),
                         (300, 20))

    def test_custom_settings(self):
        answers = iter(("220", "12"))
        self.assertEqual(prompt_playback_settings(input_fn=lambda _: next(answers)),
                         (220, 12))

    def test_out_of_range_or_noninteger_rejected(self):
        for speed, acceleration in ((0, 20), (1201, 20), (300, 0),
                                    (300, 61), (True, 20), (300.0, 20)):
            with self.subTest(speed=speed, acceleration=acceleration):
                with self.assertRaises(ValueError):
                    validate_playback_settings(speed, acceleration)

    def test_high_speed_requires_explicit_confirmation(self):
        answers = iter(("1200", "60", "NO"))
        with self.assertRaisesRegex(ValueError, "cancelled"):
            prompt_playback_settings(input_fn=lambda _: next(answers))
        answers = iter(("1200", "60", "CONFIRM"))
        self.assertEqual(prompt_playback_settings(input_fn=lambda _: next(answers)),
                         (1200, 60))


if __name__ == "__main__":
    unittest.main()
