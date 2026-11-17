"""Hardware-free checks for Kelvin Script 5 debug wrapper."""
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("script5_debug_test", ROOT / "scripts" / "05_waypoint_debug.py")
debug = importlib.util.module_from_spec(spec)
spec.loader.exec_module(debug)


class Script5DebugTests(unittest.TestCase):
    def test_one_speed_pair_controls_every_waypoint(self):
        settings = SimpleNamespace(POSE_SEQUENCE_MOVE_SPEED=3000,
                                   POSE_SEQUENCE_MOVE_ACCELERATION=254)
        self.assertEqual(debug.apply_speed(settings, 500, 25), (500, 25))
        self.assertEqual(settings.POSE_SEQUENCE_MOVE_SPEED, 500)
        self.assertEqual(settings.POSE_SEQUENCE_MOVE_ACCELERATION, 25)
        with self.assertRaises(ValueError):
            debug.apply_speed(settings, 9999, 25)
        self.assertEqual(settings.POSE_SEQUENCE_MOVE_SPEED, 500)

    def test_menu_can_change_speed_before_play_without_record_edits(self):
        settings = SimpleNamespace(POSE_SEQUENCE_MOVE_SPEED=300,
                                   POSE_SEQUENCE_MOVE_ACCELERATION=20)
        calls = []
        script = SimpleNamespace(
            find_sequences=lambda: [(Path("example.json"), {"points": [1, 2]})],
            play_sequence=lambda path: calls.append((str(path), settings.POSE_SEQUENCE_MOVE_SPEED,
                                                     settings.POSE_SEQUENCE_MOVE_ACCELERATION)),
            create_sequence=lambda: None,
        )
        responses = iter(["3", "2", "1", "q"])
        debug.menu(script, settings, speed=300, acceleration=20,
                   input_fn=lambda _prompt: next(responses),
                   speed_prompt=lambda **_: (450, 30))
        self.assertEqual(calls, [("example.json", 450, 30)])

    def test_high_speed_requires_explicit_confirmation(self):
        self.assertTrue(debug.confirm_high_speed(300, 20, lambda _: ""))
        self.assertFalse(debug.confirm_high_speed(700, 40, lambda _: "no"))
        self.assertTrue(debug.confirm_high_speed(700, 40, lambda _: "CONFIRM"))


if __name__ == "__main__":
    unittest.main()
