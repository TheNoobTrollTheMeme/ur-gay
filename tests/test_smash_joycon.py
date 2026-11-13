"""Hardware-free tests for Joy-Con Smash queue control."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import smash_joycon as sj
import competition_queue as cq

class FakeTerminal:
    def read_key(self, timeout):
        return None

class FakeGamepad:
    def __init__(self, actions=None, failure=None):
        self.actions = list(actions or [])
        self.failure = failure

    def poll(self):
        if self.failure is not None:
            raise self.failure
        events, self.actions = self.actions, []
        return events

class SmashMappingTests(unittest.TestCase):
    def test_stick_chooses_square_without_moving_robot(self):
        mapper = sj.SmashMapper()
        mapper.stick(0.7, -0.8)
        self.assertEqual(mapper.square, "c3r1")
        mapper.stick(0.01, 0)
        self.assertEqual(mapper.square, "c3r1")

    def test_actions_require_zr_hold(self):
        mapper = sj.SmashMapper()
        self.assertEqual(mapper.button("A", True), [])
        mapper.button("ZR", True)
        self.assertEqual(mapper.button("A", True), [("add", "c2r2")])
        self.assertEqual(mapper.button("B", True), [("add", "c2r2a")])
        self.assertEqual(mapper.button("Y", True), [("add", "tapinr")])
        mapper.button("ZR", False)
        self.assertEqual(mapper.button("B", True), [])

    def test_plus_home_stop_even_without_zr(self):
        mapper = sj.SmashMapper()
        self.assertEqual(mapper.button("PLUS", True), [("stop", None)])
        self.assertEqual(mapper.button("HOME", True), [("stop", None)])

    def test_x_steps_line_only_when_armed(self):
        mapper = sj.SmashMapper()
        preview = mapper.button("X", True)
        self.assertIn("middle", preview[0][1])
        mapper.button("ZR", True)
        self.assertEqual(mapper.button("X", True)[0], ("add", "c1r2"))
        self.assertEqual(mapper.button("X", True)[0], ("add", "c2r2"))
        self.assertEqual(mapper.button("X", True)[0], ("add", "c3r2"))
        self.assertEqual(mapper.button("X", True)[0], ("add", "c1r2"))

    def test_clear_requires_zr(self):
        mapper = sj.SmashMapper()
        self.assertEqual(mapper.button("MINUS", True), [])
        mapper.button("ZR", True)
        self.assertEqual(mapper.button("MINUS", True), [("clear", None)])


    def test_electric_wind_god_fist_uses_selected_target(self):
        mapper = sj.SmashMapper()
        mapper.stick(0.9, -0.9, now=0.5)
        self.assertEqual(mapper.square, "c3r1")
        mapper.button("ZR", True)
        mapper.stick(0.9, 0.0, now=1.0)
        mapper.stick(0.0, 0.9, now=1.1)
        mapper.stick(0.9, 0.9, now=1.2)
        event = mapper.button("A", True, now=1.3)
        self.assertEqual(event[0], ("replace_pending", "c3r1a"))
        self.assertIn("ELECTRIC", event[1][1])
        self.assertEqual(mapper.square, "c3r1")

    def test_failed_command_input_fails_closed(self):
        mapper = sj.SmashMapper()
        mapper.button("ZR", True)
        mapper.stick(0.9, 0.0, now=1.0)
        event = mapper.button("A", True, now=1.1)
        self.assertEqual(event[0][0], "notice")
        self.assertIn("Incomplete", event[0][1])
        mapper.button("ZR", False)
        mapper.button("ZR", True)
        self.assertEqual(mapper.button("A", True)[0],
                         ("add", "c2r2"))

    def test_priority_buttons_replace_pending_not_active(self):
        mapper = sj.SmashMapper()
        mapper.stick(-0.9, -0.9)
        mapper.button("ZR", True)
        self.assertEqual(mapper.button("L", True)[0],
                         ("replace_pending", "c1r1"))
        self.assertEqual(mapper.button("R", True)[0],
                         ("replace_pending", "c1r1a"))
        plan = cq.QueuePlan({"1": (), "2": (), "3": ()}, ["1", "2"],
                            aliases={"c1r1a": "3"})
        manager = cq.InputManager(plan, lambda *_: None,
               gamepad=FakeGamepad([("replace_pending", "c1r1a")]))
        manager.active = "1"
        self.assertTrue(manager.poll(FakeTerminal()))
        self.assertEqual(list(plan.pending), ["3"])
        self.assertEqual(manager.active, "1")

    def test_invalid_priority_does_not_erase_queue(self):
        plan = cq.QueuePlan({"1": (), "2": ()}, ["1", "2"])
        manager = cq.InputManager(plan, lambda *_: None,
               gamepad=FakeGamepad([("replace_pending", "not_recorded")]))
        self.assertTrue(manager.poll(FakeTerminal()))
        self.assertEqual(list(plan.pending), ["1", "2"])

    def test_input_manager_accepts_actions_and_does_not_touch_active(self):
        plan = cq.QueuePlan({"1": (), "2": ()}, ["1"],
                            aliases={"c2r2": "2"}, labels={"1": "existing", "2": "c2r2"})
        manager = cq.InputManager(plan, lambda *_: None,
                                  gamepad=FakeGamepad([("add", "c2r2")]))
        manager.active = "1"
        self.assertTrue(manager.poll(FakeTerminal()))
        self.assertEqual(list(plan.pending), ["1", "2"])
        self.assertEqual(manager.active, "1")

    def test_controller_disconnect_fails_closed(self):
        plan = cq.QueuePlan({"1": ()}, ["1"])
        messages = []
        manager = cq.InputManager(plan, messages.append,
                                  gamepad=FakeGamepad(failure=OSError("device gone")))
        self.assertFalse(manager.poll(FakeTerminal()))
        self.assertTrue(manager.stopped)
        self.assertIn("CONTROLLER LOST", "".join(messages))

    def test_gamepad_stop_overrides_queue(self):
        plan = cq.QueuePlan({"1": ()}, ["1"])
        manager = cq.InputManager(plan, lambda *_: None,
                                  gamepad=FakeGamepad([("stop", None)]))
        self.assertFalse(manager.poll(FakeTerminal()))
        self.assertEqual(list(plan.pending), ["1"])

if __name__ == "__main__":
    unittest.main()
