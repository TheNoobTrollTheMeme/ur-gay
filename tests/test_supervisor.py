import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from simulation_safety.supervisor import (CollisionModel, MockDriver, Policy, SafetyError,
                                          Supervisor, hardware_driver, load_sequence)


ZERO = (0.,)*5
ONE = (1.,)*5
TWO = (2.,)*5


def model(obstacle=(50., 51.), samples=10):
    # Entirely synthetic bodies and scene, in arbitrary test distance units.
    names = ('base', 'upper_arm', 'forearm', 'wrist', 'left_jaw', 'right_jaw')
    return CollisionModel(
        lambda q: {name: ((q[0], i*3., 0.), .1) for i, name in enumerate(names)},
        {name: (1., 0., 0., 0., 0.) for name in names},
        {'tray': ((obstacle[0], -1., -1.), (obstacle[1], 20., 1.)),
         'frame': ((60., -1., -1.), (61., 20., 1.)),
         'shelf': ((70., -1., -1.), (71., 20., 1.))},
        clearance=.05, uncertainty=.02, samples=samples)


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.log = Path(self.temp.name)/'faults.jsonl'
        self.driver = MockDriver(ZERO)
        self.policy = Policy(((-10., 10.),)*5, .5, .5, .3, 2., .1)
        self.s = Supervisor(self.driver, self.policy, model(), self.log)

    def run_normal(self):
        self.s.start((ONE, TWO), 0.)
        for t in (0., .1, .2):
            self.s.tick(t)

    def assert_fault(self, text):
        self.assertEqual(self.s.state, 'FAULT')
        self.assertFalse(self.s.queue)
        self.assertIsNone(self.driver.target)
        self.assertIn(text, self.s.events[-1]['detail'])
        before = len(self.driver.commands)
        self.s.tick(9.)
        self.assertEqual(before, len(self.driver.commands))

    def test_normal_exactly_two_commands(self):
        self.run_normal()
        self.assertEqual(self.s.state, 'COMPLETE')
        self.assertEqual(self.driver.commands, [ONE, TWO])
        records = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertEqual(records[-1]['kind'], 'COMPLETE')
        self.assertIn('utc', records[0])

    def test_jam_latches_and_reset_does_not_restart(self):
        self.driver.stall = True
        self.s.start((ONE, TWO), 0.)
        for t in (0., .1, .7):
            self.s.tick(t)
        self.assert_fault('jam')
        with self.assertRaises(SafetyError):
            self.s.reset(.8)
        with self.assertRaises(SafetyError):
            self.s.start((ONE, TWO), .8)
        self.driver.stall = False
        self.s.reset(.8, operator_ack=True)
        self.assertEqual(self.s.state, 'IDLE')
        self.assertEqual(len(self.driver.commands), 1)

    def test_stale_feedback_before_dispatch(self):
        self.driver.stale = True
        self.s.start((ONE, TWO), 0.)
        self.assert_fault('stale')
        self.assertFalse(self.driver.commands)

    def test_missing_device_midflight(self):
        self.s.start((ONE, TWO), 0.)
        self.s.tick(0.)
        self.driver.missing = True
        self.s.tick(.1)
        self.assert_fault('missing device')
        self.s.reset(.2, operator_ack=True)
        self.assert_fault('missing device')

    def test_stale_midflight_prevents_second_command(self):
        self.s.start((ONE, TWO), 0.)
        self.s.tick(0.)
        self.driver.stale = True
        self.s.tick(.1)
        self.assert_fault('stale')
        self.assertEqual(len(self.driver.commands), 1)

    def test_late_arrival_does_not_bypass_deadline(self):
        self.s.start((ONE, TWO), 0.)
        self.s.tick(0.)
        self.s.tick(3.)
        self.assert_fault('timeout')

    def test_tracking_error_must_persist(self):
        self.driver.stall = True
        self.s.start((ONE, TWO), 0.)
        self.s.tick(0.)
        self.s.tick(.1)
        self.driver.pose = (.6,)*5  # Within error band, still outside arrival tolerance.
        self.s.tick(.4)
        self.driver.pose = ZERO
        self.s.tick(.7)
        self.s.tick(.9)
        self.assertEqual(self.s.state, 'RUNNING')
        self.s.tick(1.3)
        self.assert_fault('jam')

    def test_invalid_command(self):
        for bad in ((11.,)*5, (float('nan'),)*5, (1.,)*4):
            with self.subTest(bad=bad):
                s = Supervisor(MockDriver(ZERO), self.policy, model(), self.log)
                s.start((ONE, bad), 0.)
                self.assertEqual(s.state, 'FAULT')
                self.assertFalse(s.driver.commands)

    def test_only_two_waypoints(self):
        for points in ((), (ONE,), (ONE, TWO, ZERO)):
            s = Supervisor(MockDriver(ZERO), self.policy, model(), self.log)
            s.start(points, 0.)
            self.assertEqual(s.state, 'FAULT')

    def test_timeout_below_jam_threshold(self):
        self.driver.stall = True
        self.s.start(((.2,)*5, TWO), 0.)
        self.s.tick(0.)
        self.s.tick(2.)
        self.assert_fault('timeout')

    def test_operator_stop_cancels_queue_and_holds(self):
        self.s.start((ONE, TWO), 0.)
        self.s.tick(0.)
        self.s.stop(.1)
        self.assert_fault('operator stop')
        self.assertEqual(self.driver.holds, 1)

    def test_hold_failure_is_reported_and_latched(self):
        self.driver.fail_stop = True
        self.s.stop(0.)
        self.assert_fault('HOLD FAILED')

    def test_log_failure_still_stops(self):
        self.s.log_path = Path(self.temp.name)/'missing'/'log'
        self.s.start((ONE, TWO), 0.)
        self.assertEqual(self.s.state, 'FAULT')
        self.assertTrue(self.s.log_failed)
        self.assertFalse(self.driver.commands)
        self.assertEqual(self.driver.holds, 1)

    def test_collision_between_clear_endpoints(self):
        self.s.collisions = model((.45, .55))
        self.s.start((ONE, TWO), 0.)
        self.assert_fault('collision')
        self.assertFalse(self.driver.commands)

    def test_coverage_inflation_between_samples(self):
        # Endpoints alone miss this thin obstruction; inflated covers reject it.
        m = model((.49, .51), samples=1)
        with self.assertRaisesRegex(SafetyError, 'collision'):
            m.check(ZERO, ONE)

    def test_every_body_including_jaws(self):
        for index in range(6):
            m = model()
            m.obstacles = {'shelf': ((.4, index*3.-.1, -.1), (.6, index*3.+.1, .1))}
            with self.assertRaisesRegex(SafetyError, 'collision'):
                m.check(ZERO, ONE)

    def test_unknown_geometry(self):
        self.s.collisions = None
        self.s.start((ONE, TWO), 0.)
        self.assert_fault('unknown geometry')

    def test_backward_clock(self):
        self.s.start((ONE, TWO), 1.)
        self.s.tick(0.)
        self.assert_fault('clock')

    def test_hardware_gate_cannot_be_enabled(self):
        for flag in (False, True):
            with self.assertRaises(SafetyError):
                hardware_driver(hardware_enable=flag)
        with self.assertRaises(SafetyError):
            Supervisor(object(), self.policy, model(), self.log)

    def test_bad_configuration(self):
        with self.assertRaises(SafetyError):
            Policy(((-1., 1.),)*5, float('nan'), .1, .1, 1., .01)
        with self.assertRaises(SafetyError):
            CollisionModel(None, {}, {}, 0., 0., 10)

    def test_project_recordings_preserved(self):
        root = Path(__file__).resolve().parents[1]
        paths = sorted(root.glob('c[1-3]r[1-3][mp].json'))
        self.assertEqual(len(paths), 18)
        for path in paths:
            before = hashlib.sha256(path.read_bytes()).digest()
            points = load_sequence(path)
            self.assertEqual(len(points), 2)
            self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), before)

    def test_loader_rejects_extra_waypoint(self):
        root = Path(__file__).resolve().parents[1]
        data = json.loads((root/'c1r1m.json').read_text())
        data['points'].append(data['points'][0])
        path = Path(self.temp.name)/'bad.json'
        path.write_text(json.dumps(data))
        with self.assertRaises(SafetyError):
            load_sequence(path)


if __name__ == '__main__':
    unittest.main()
