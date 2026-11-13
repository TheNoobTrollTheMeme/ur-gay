"""Hardware-free tests for the Joy-Con's Smash-style six-block selector."""
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
    def test_six_slots_follow_three_plates_times_two_blocks(self):
        mapper = sj.SmashMapper()
        self.assertEqual(mapper.selection, ("grab1", "c2r2"))
        mapper.dpad("UP")              # central Plate A, left block
        self.assertEqual(mapper.pickup, "grab3")
        mapper.right_stick(0.9)        # central Plate A, right block
        self.assertEqual(mapper.pickup, "grab4")
        mapper.dpad("RIGHT")           # right Plate B, right block
        self.assertEqual(mapper.pickup, "grab6")
        mapper.right_stick(-0.9)       # right Plate B, left block
        self.assertEqual(mapper.pickup, "grab5")
        mapper.dpad("LEFT")            # left Plate B, left block
        self.assertEqual(mapper.pickup, "grab1")
        self.assertEqual(mapper.dpad("DOWN"), [])  # intentionally unused

    def test_selected_block_map_is_configurable(self):
        names = ("b_left1", "b_left2", "front1", "front2", "b_right1", "b_right2")
        mapper = sj.SmashMapper(pickup_names=names)
        mapper.dpad("UP")
        mapper.right_stick(.9)
        self.assertEqual(mapper.pickup, "front2")
        with self.assertRaises(ValueError):
            sj.SmashMapper(pickup_names=("grab1", "grab2"))

    def test_left_stick_aims_at_nine_squares(self):
        mapper = sj.SmashMapper()
        mapper.stick(.7, -.8)
        self.assertEqual(mapper.square, "c3r1")
        mapper.stick(.01, 0)
        self.assertEqual(mapper.square, "c3r1")

    def test_a_queues_combined_pickup_and_place(self):
        mapper = sj.SmashMapper()
        mapper.dpad("UP")
        mapper.right_stick(.9)
        mapper.stick(0, -.9)
        self.assertEqual(mapper.button("A", True),
                         [("add_combo", ("grab4", "c2r1"))])
        self.assertEqual(mapper.button("B", True), [("undo", None)])

    def test_b_cancels_and_zr_b_clears(self):
        mapper = sj.SmashMapper()
        self.assertEqual(mapper.button("B", True), [("undo", None)])
        mapper.button("ZR", True)
        self.assertEqual(mapper.button("B", True), [("clear", None)])
        mapper.button("ZR", False)
        self.assertEqual(mapper.button("B", True), [("undo", None)])

    def test_no_putting_or_tapin_inputs(self):
        mapper = sj.SmashMapper()
        self.assertEqual(mapper.button("Y", True)[0][0], "notice")
        self.assertNotIn("tapinr", repr(mapper.button("Y", True)))
        self.assertEqual(mapper.button("B", True), [("undo", None)])

    def test_plus_home_stop_without_trigger(self):
        mapper = sj.SmashMapper()
        self.assertEqual(mapper.button("PLUS", True), [("stop", None)])
        self.assertEqual(mapper.button("HOME", True), [("stop", None)])

    def test_line_macro_keeps_selected_pickup(self):
        mapper = sj.SmashMapper()
        mapper.dpad("UP")
        mapper.right_stick(.9)
        self.assertIn("middle", mapper.button("X", True)[0][1])
        mapper.button("ZR", True)
        self.assertEqual(mapper.button("X", True)[0],
                         ("add_combo", ("grab4", "c1r2")))
        self.assertEqual(mapper.button("X", True)[0],
                         ("add_combo", ("grab4", "c2r2")))

    def test_electric_input_prioritizes_full_combo_not_attack(self):
        mapper = sj.SmashMapper()
        mapper.stick(.9, -.9, now=.5)
        mapper.button("ZR", True)
        mapper.stick(.9, 0.0, now=1.0)
        mapper.stick(0.0, .9, now=1.1)
        mapper.stick(.9, .9, now=1.2)
        event = mapper.button("A", True, now=1.3)
        self.assertEqual(event[0], ("replace_pending_combo", ("grab1", "c3r1")))
        self.assertIn("ELECTRIC PLACE", event[1][1])

    def test_failed_electric_inputs_do_not_queue_accidental_place(self):
        mapper = sj.SmashMapper()
        mapper.button("ZR", True)
        mapper.stick(.9, 0., now=1.0)
        self.assertEqual(mapper.button("A", True, now=1.1)[0][0], "notice")
        mapper.button("ZR", False)
        self.assertEqual(mapper.button("A", True)[0],
                         ("add_combo", ("grab1", "c2r2")))

    def test_priority_place_and_attack_are_distinct(self):
        mapper = sj.SmashMapper()
        mapper.dpad("RIGHT")
        mapper.right_stick(.9)
        mapper.button("ZR", True)
        self.assertEqual(mapper.button("L", True)[0],
                         ("replace_pending_combo", ("grab6", "c2r2")))
        self.assertEqual(mapper.button("R", True)[0],
                         ("replace_pending", "c2r2a"))

    def test_queue_stitches_pickup_before_destination_as_one_action(self):
        a = (cq.Point("grab1", 1, {"gripper": 1200}),)
        b = (cq.Point("c2r2m", 1, {"base": 1500}),
             cq.Point("c2r2p", 1, {"base": 1600}))
        plan = cq.QueuePlan({"1": a, "2": b},
                            aliases={"grab1": "1", "c2r2": "2"},
                            labels={"1": "grab1", "2": "c2r2"})
        mgr = cq.InputManager(plan, lambda *_: None,
                              gamepad=FakeGamepad([
                                  ("add_combo", ("grab1", "c2r2"))]))
        mgr.active = "2"
        self.assertTrue(mgr.poll(FakeTerminal()))
        self.assertEqual(len(plan.pending), 1)
        composed = plan.catalog[plan.pending[0]]
        self.assertEqual([p.route for p in composed], ["grab1", "c2r2m", "c2r2p"])
        self.assertEqual(mgr.active, "2")
        mgr.gamepad = FakeGamepad([("undo", None)])
        self.assertTrue(mgr.poll(FakeTerminal()))
        self.assertEqual(list(plan.pending), [])
        self.assertEqual(mgr.active, "2")

    def test_missing_recording_does_not_queue_half_a_combo(self):
        plan = cq.QueuePlan({"1": ()}, aliases={"c2r2": "1"})
        logs = []
        mgr = cq.InputManager(plan, logs.append,
                              gamepad=FakeGamepad([
                                  ("add_combo", ("grab6", "c2r2"))]))
        self.assertTrue(mgr.poll(FakeTerminal()))
        self.assertEqual(list(plan.pending), [])
        self.assertIn("ignored", " ".join(logs))

    def test_replace_pending_is_atomic_and_does_not_touch_active(self):
        plan = cq.QueuePlan({"1": (), "2": (), "3": ()},
                            initial=("1", "2"), aliases={
                                "grab1": "1", "c2r2": "2", "c3r3": "3"})
        mgr = cq.InputManager(plan, lambda *_: None,
                              gamepad=FakeGamepad([
                                  ("replace_pending_combo", ("grab1", "c3r3"))]))
        mgr.active = "2"
        self.assertTrue(mgr.poll(FakeTerminal()))
        self.assertEqual(len(plan.pending), 1)
        self.assertEqual(mgr.active, "2")
        previous = list(plan.pending)
        mgr.gamepad = FakeGamepad([
            ("replace_pending_combo", ("missing", "c2r2"))])
        self.assertTrue(mgr.poll(FakeTerminal()))
        self.assertEqual(list(plan.pending), previous)

    def test_controller_disconnect_fails_closed(self):
        mgr = cq.InputManager(cq.QueuePlan({"1": ()}),
                              lambda *_: None,
                              gamepad=FakeGamepad(failure=OSError("gone")))
        self.assertFalse(mgr.poll(FakeTerminal()))
        self.assertTrue(mgr.stopped)

    def test_gamepad_stop_overrides_queue(self):
        plan = cq.QueuePlan({"1": ()}, ("1",))
        mgr = cq.InputManager(plan, lambda *_: None,
                              gamepad=FakeGamepad([("stop", None)]))
        self.assertFalse(mgr.poll(FakeTerminal()))
        self.assertEqual(list(plan.pending), ["1"])


if __name__ == "__main__":
    unittest.main()
