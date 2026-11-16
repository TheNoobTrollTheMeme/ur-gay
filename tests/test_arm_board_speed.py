"""Hardware-free regression tests for adjustable terminal-board playback."""
from pathlib import Path
import sys
import unittest
from threading import Event
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from arm_board import Plan, Waypoint, play


class BoardPlaybackSpeedTests(unittest.TestCase):
    def test_custom_arm_speed_sent_while_seeding_at_normal(self):
        calls = []
        positions = dict(base=2000, shoulder=2000, elbow=2000,
                         wrist=2000, gripper=3000)

        class Port:
            def closePort(self):
                calls.append(("close",))

        class Motor:
            pass

        handler = Motor()
        def sync_targets(_handler, _settings, _check, _target, profile):
            calls.append(("custom", profile))
        script6 = SimpleNamespace(
            connect=lambda: (Port(), handler),
            read_all=lambda _: positions,
            move_all_sync=lambda _h, _positions, **kw: calls.append(("seed", kw)),
            enable_all_torque=lambda _: calls.append(("enabled",)),
            disable_all_torque=lambda _: calls.append(("disabled",)),
            wait_move=lambda _h, _stop: True,
            competition=SimpleNamespace(sync_targets=sync_targets),
            settings=SimpleNamespace(),
            check_result=lambda *_: None,
        )
        plan = Plan("C1R1", "place", 1, ("grab1",),
                    (Waypoint("grab1", 1, positions),))
        completed = play(script6, plan, Event(), calls.append,
                         speed=200, acceleration=10)
        self.assertTrue(completed)
        self.assertIn(("custom", (200, 10)), calls)
        self.assertIn(("seed", dict(speed=300, acceleration=20)), calls)
        self.assertNotIn(("disabled",), calls)
        self.assertEqual(calls[-1], ("close",))

    def test_bad_speed_rejected_before_port_open(self):
        script6 = SimpleNamespace(connect=lambda: self.fail("Should not connect"))
        with self.assertRaises(ValueError):
            play(script6, None, Event(), lambda *_: None,
                 speed=2000, acceleration=20)


if __name__ == "__main__":
    unittest.main()
