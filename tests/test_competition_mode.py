"""Hardware-free tests for competition placement route preflight and playback."""
import unittest
from pathlib import Path
from types import SimpleNamespace
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import competition_mode as cm

J = ('base', 'shoulder', 'elbow', 'wrist', 'gripper')


def s(route, i, base, grip=3000):
    return cm.Step(route, i, dict(base=base, shoulder=2000, elbow=2000,
                                  wrist=2000, gripper=grip))


class PrepTests(unittest.TestCase):
    def test_prevalidation_and_routes(self):
        seen = []
        def load(path):
            return None, [s('x', 1, 100).target, s('x', 2, 200).target]
        steps = cm.prepare_placements([Path('c1r1m.json')], load, seen.append)
        self.assertEqual([x.route for x in steps],
                         ['c1r1m', 'c1r1m', 'c1r1p', 'c1r1p'])
        self.assertEqual(len(seen), 4)

    def test_rejects_attack(self):
        with self.assertRaises(ValueError):
            cm.prepare_placements([Path('c1r1a.json')], None, None)

    def test_lookahead_only_without_reversal_or_gripper_change(self):
        self.assertTrue(cm.can_look_ahead(s('m', 1, 100), s('m', 2, 200),
                                          s('m', 3, 300), J))
        self.assertFalse(cm.can_look_ahead(s('m', 1, 100), s('m', 2, 200),
                                           s('m', 3, 100), J))
        self.assertFalse(cm.can_look_ahead(s('m', 1, 100), s('m', 2, 200),
                                           s('p', 3, 300), J))
        self.assertFalse(cm.can_look_ahead(s('m', 1, 100), s('m', 2, 200),
                                           s('m', 3, 300, 3100), J))


class MockHandler:
    def __init__(self, pos, moving=1):
        self.pos, self.moving = pos, moving

    def ReadPos(self, sid):
        return self.pos[sid], 0, 0

    def ReadMoving(self, sid):
        return self.moving, 0, 0


class WaitTests(unittest.TestCase):
    def setUp(self):
        self.settings = SimpleNamespace(JOINT_NAMES=J,
                        SERVO_IDS=dict(zip(J, range(1, 6))))
        self.target = s('m', 1, 200).target

    def test_near_threshold(self):
        h = MockHandler({1: 193, 2: 2000, 3: 2000, 4: 2000, 5: 3000})
        self.assertTrue(cm.wait_near(h, None, self.target, self.settings,
                                     lambda *_: None, lambda _t: False))

    def test_stop_before_reads(self):
        h = MockHandler({1: 193, 2: 2000, 3: 2000, 4: 2000, 5: 3000})
        self.assertFalse(cm.wait_near(h, None, self.target, self.settings,
                                      lambda *_: None, lambda _t: True))

    def test_stall_aborts(self):
        h = MockHandler({1: 150, 2: 2000, 3: 2000, 4: 2000, 5: 3000},
                        moving=0)
        with self.assertRaises(RuntimeError):
            cm.wait_near(h, None, self.target, self.settings,
                         lambda *_: None, lambda _t: False)


class ExecuteTests(unittest.TestCase):
    def setUp(self):
        self.settings = SimpleNamespace(JOINT_NAMES=J,
                        SERVO_IDS=dict(zip(J, range(1, 6))))
        self.calls = []
        calls = self.calls

        class Port:
            def closePort(self):
                calls.append('close')

        class Terminal:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        class Handler:
            def ReadPos(self, sid):
                return {1: 200, 2: 2000, 3: 2000, 4: 2000, 5: 3000}[sid], 0, 0

            def ReadMoving(self, sid):
                return 1, 0, 0

        self.Port, self.Terminal, self.Handler = Port, Terminal, Handler

    def run_execution(self, steps, stopped):
        calls = self.calls
        return cm.execute(steps, connect=lambda: (self.Port(), self.Handler()),
            read_all=lambda h: steps[0].target,
            move_all_sync=lambda h, target, **kw: calls.append(('move', target['base'])),
            enable_all_torque=lambda h: calls.append('enable'),
            disable_all_torque=lambda h: calls.append('disable'),
            terminal_class=self.Terminal, settings=self.settings,
            check_result=lambda *_: None, stopped=stopped,
            wait_move=lambda h, t: True, log=lambda *_: None)

    def test_success_leaves_final_pose_powered(self):
        steps = [s('m', 1, 100), s('m', 2, 200), s('m', 3, 300)]
        self.assertTrue(self.run_execution(steps, lambda _t: False))
        self.assertEqual([x for x in self.calls if isinstance(x, tuple)],
                         [('move', 100), ('move', 100),
                          ('move', 200), ('move', 300)])
        self.assertIn('enable', self.calls)
        self.assertNotIn('disable', self.calls)
        self.assertEqual(self.calls[-1], 'close')

    def test_stop_releases_torque_before_any_waypoint(self):
        steps = [s('m', 1, 100)]
        self.assertFalse(self.run_execution(steps, lambda _t: True))
        self.assertEqual(self.calls, [('move', 100), 'disable', 'close'])


if __name__ == '__main__':
    unittest.main()
