"""Hardware-free queue/control tests; run with python3 -m unittest discover -s tests."""
import unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import competition_queue as cq

NAMES = ("base", "shoulder", "elbow", "wrist", "gripper")
SETTINGS = SimpleNamespace(JOINT_NAMES=NAMES,
    SERVO_IDS={name: i + 1 for i, name in enumerate(NAMES)})

def pt(route, number, base=2000, gripper=3000):
    return cq.Point(route, number, dict(base=base, shoulder=2000,
                         elbow=2000, wrist=2000, gripper=gripper))

class QueueChecks(unittest.TestCase):
    def test_edit_queue_without_touching_active(self):
        plan = cq.QueuePlan({"1": (), "2": (), "3": ()}, ("1", "2"))
        plan.insert(2, "3")
        self.assertEqual(list(plan.pending), ["1", "3", "2"])
        plan.move(3, 1)
        self.assertEqual(list(plan.pending), ["2", "1", "3"])
        plan.delete(2)
        self.assertEqual(list(plan.pending), ["2", "3"])
        controller = cq.InputManager(plan, lambda *_: None)
        controller.active = "1"
        for key in ":d1\r":
            controller.handle(key)
        self.assertEqual(controller.active, "1")
        self.assertEqual(list(plan.pending), ["3"])
        controller.handle("2")
        self.assertEqual(list(plan.pending), ["3", "2"])
        controller.handle("e")
        self.assertTrue(controller.end_after_queue)

    def test_invalid_queue_mutations_do_not_change_pending(self):
        plan = cq.QueuePlan({"1": (), "2": ()}, ("1", "2"))
        for command in ("a 3", "i 0 2", "m 10 1", "d 0", "clear xyz"):
            with self.subTest(command=command), self.assertRaises(ValueError):
                plan.edit(command)
            self.assertEqual(list(plan.pending), ["1", "2"])

    def test_stop_keys_override_queue_editor(self):
        for key in (" ", "Q", "\x03", "\x1b"):
            with self.subTest(key=repr(key)):
                controller = cq.InputManager(cq.QueuePlan({"1": ()}), lambda *_: None)
                controller.handle(":")
                controller.handle(key)
                self.assertTrue(controller.stopped)

    def test_colon_edits_and_immediate_add(self):
        parser = cq.KeyInput()
        self.assertEqual(parser.feed("3"), ("add", "3"))
        parser.feed(":")
        for ch in "m2,1":
            parser.feed(ch)
        self.assertEqual(parser.feed("\r"), ("edit", "m 2 1"))
        parser.feed(":")
        for ch in "i1,2":
            parser.feed(ch)
        self.assertEqual(parser.feed("\r"), ("edit", "i 1 2"))

    def test_blend_rejects_grip_turn_and_route_boundary(self):
        self.assertTrue(cq.eligible_blend(pt("m",1,100),pt("m",2,200),pt("m",3,300),NAMES))
        self.assertFalse(cq.eligible_blend(pt("m",1,100),pt("m",2,200),pt("m",3,100),NAMES))
        self.assertFalse(cq.eligible_blend(pt("m",1,100),pt("m",2,200),pt("p",3,300),NAMES))
        self.assertFalse(cq.eligible_blend(pt("m",1,100),pt("m",2,200),pt("m",3,300,2900),NAMES))

    def test_catalog_preflights_every_pose(self):
        def load(path):
            return None, [pt(path.stem, 1, 999 if path.stem.endswith("p") else 100).target]
        def validate(target):
            if target["base"] == 999:
                raise ValueError("unsafe recorded position")
        with self.assertRaisesRegex(ValueError, "unsafe"):
            cq.build_catalog([Path("c1r1m.json")], load, validate)

class Group:
    def __init__(self, motor): self.motor = motor
    def txPacket(self):
        self.motor.packets += 1
        self.motor.positions.update({i:v[0] for i,v in self.motor.pending.items()})
        return 0
    def clearParam(self): self.motor.pending.clear()

class Motor:
    def __init__(self):
        self.positions = {i:2000 for i in range(1,6)}
        self.pending, self.commands = {}, []
        self.packets = 0
        self.groupSyncWrite = Group(self)
    def SyncWritePosEx(self, sid, pos, speed, accel):
        self.pending[sid] = (pos, speed, accel)
        self.commands.append((sid, pos, speed, accel))
        return True
    def ReadPos(self, sid): return self.positions[sid], 0, 0
    def ReadMoving(self, sid): return 0, 0, 0

class Terminal:
    def __init__(self, keys): self.keys=keys
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def read_key(self, _timeout): return self.keys()

class HardwareChecks(unittest.TestCase):
    def test_turbo_arm_speed_but_normal_gripper_speed(self):
        motor = Motor()
        cq.sync_targets(motor, SETTINGS, lambda *_: None,
                        pt("m",1).target, "turbo")
        self.assertEqual([c[2:] for c in motor.commands], [(1200,60)]*4+[(300,20)])
        self.assertEqual(motor.packets, 1)

    def test_live_preinput_runs_next_square(self):
        catalog = {"1": (pt("m1",1,2100), pt("m1",2,2200)),
                   "2": (pt("m2",1,2300), pt("m2",2,2400))}
        plan = cq.QueuePlan(catalog, ["1"])
        motor, statuses, logs = Motor(), [], []
        keys = deque()
        def key():
            if motor.packets >= 2 and "injected" not in statuses:
                statuses.append("injected")
                keys.extend("2e")
            return keys.popleft() if keys else None
        class Port:
            def closePort(self): statuses.append("closed")
        result = cq.playback(catalog, plan, "fast",
            connect=lambda: (Port(), motor), read_all=lambda _: pt("seed",1).target,
            enable_all_torque=lambda _: statuses.append("enabled"),
            disable_all_torque=lambda _: statuses.append("disabled"),
            terminal_class=lambda:Terminal(key), settings=SETTINGS,
            check_result=lambda *_:None, report=logs.append, sleep=lambda _:None)
        self.assertTrue(result)
        self.assertEqual(motor.packets, 5)
        self.assertNotIn("disabled", statuses)
        self.assertIn("closed", statuses)
        self.assertTrue(any("Motion 2 complete" in line for line in logs))

    def test_emergency_stop_releases(self):
        catalog={"1": (pt("m1",1),)}
        plan=cq.QueuePlan(catalog, ["1"])
        statuses=[]
        class Port:
            def closePort(self): statuses.append("closed")
        result=cq.playback(catalog,plan,"normal",
            connect=lambda:(Port(),Motor()),
            read_all=lambda _:pt("seed",1).target,
            enable_all_torque=lambda _:statuses.append("enabled"),
            disable_all_torque=lambda _:statuses.append("disabled"),
            terminal_class=lambda:Terminal(lambda:" "),settings=SETTINGS,
            check_result=lambda *_:None,report=lambda *_:None,sleep=lambda _:None)
        self.assertFalse(result)
        self.assertIn("disabled",statuses)
        self.assertIn("closed",statuses)



class PreflightIsolationTests(unittest.TestCase):
    def test_bad_square_is_disabled_without_renumbering_good_square(self):
        paths = [Path("c1r1m.json"), Path("c1r2m.json")]
        messages = []
        def load(path):
            if path.stem == "c1r1m":
                return None, [pt(path.stem, 1, 200).target,
                              dict(base=200, shoulder=9999, elbow=2000,
                                   wrist=2000, gripper=3000)]
            return None, [pt(path.stem, 1, 200).target]
        def validate(target):
            if target["shoulder"] > 3000:
                raise ValueError("shoulder: raw=9999, computed_angle=200.0 deg, allowed=-90..90 deg")
        catalog = cq.build_catalog(paths, load, validate,
                                   skip_invalid=True, report=messages.append)
        self.assertEqual(list(catalog), ["2"])
        self.assertIn("c1r1m waypoint 2", messages[0])
        self.assertIn("shoulder: raw=9999", messages[0])
        plan = cq.QueuePlan(catalog)
        with self.assertRaises(ValueError):
            plan.append("1")
        plan.append("2")
        self.assertEqual(list(plan.pending), ["2"])

    def test_failed_place_route_reports_correct_file_without_stale_number(self):
        messages = []
        def load(path):
            if path.stem.endswith("p"):
                raise ValueError("cannot load file")
            return None, [pt(path.stem, 1, 200).target,
                          pt(path.stem, 2, 300).target]
        catalog = cq.build_catalog([Path("c3r3m.json")], load, lambda target: None,
                                   skip_invalid=True, report=messages.append)
        self.assertFalse(catalog)
        self.assertIn("c3r3p: cannot load file", messages[0])
        self.assertNotIn("waypoint 2", messages[0])

    def test_strict_catalog_reports_location_and_rejects_invalid(self):
        def reject(_target):
            raise ValueError("bad joint")
        with self.assertRaisesRegex(ValueError, "c2r2m waypoint 1"):
            cq.build_catalog([Path("c2r2m.json")],
                             lambda path: (None, [pt(path.stem, 1, 300).target]),
                             reject)


class UniversalMotionTests(unittest.TestCase):
    def pose(self, path):
        return None, [pt(path.stem, 1, 200).target, pt(path.stem, 2, 230).target]

    def test_grab_move_place_attack_custom_and_square_macro(self):
        names = ("grab1", "c1r1m", "c1r1p", "c1r1a", "custom_foo")
        routes = [(Path(name + ".json"), {"description": "recorded"})
                  for name in names]
        catalog, labels, aliases = cq.build_motion_catalog(
            routes, self.pose, lambda _: None, report=lambda *_: None)
        self.assertEqual(list(labels.values()),
                         list(names) + ["c1r1"])
        self.assertEqual(len(catalog), 6)
        self.assertEqual(len(catalog[aliases["grab1"]]), 2)
        self.assertEqual(len(catalog[aliases["c1r1a"]]), 2)
        self.assertEqual(len(catalog[aliases["c1r1"]]), 4)
        plan = cq.QueuePlan(catalog, ["grab1", "c1r1a", "c1r1"],
                            aliases=aliases, labels=labels)
        self.assertEqual([plan.name(x) for x in plan.pending],
                         ["grab1", "c1r1a", "c1r1"])
        plan.insert(1, "custom_foo")
        plan.move(4, 2)
        self.assertEqual([plan.name(x) for x in plan.pending],
                         ["custom_foo", "c1r1", "grab1", "c1r1a"])

    def test_invalid_and_simulation_only_motions_are_unavailable(self):
        routes = [(Path("bad.json"), {}),
                  (Path("good.json"), {"description": "live taught"}),
                  (Path("estimated.json"), {
                      "description": "OFFLINE SIMULATION ONLY, NOT CLEARED FOR ROBOT"})]
        logs = []
        def validate(target):
            if target["base"] == 999:
                raise ValueError("base outside joint limits")
        def load(path):
            return None, [pt(path.stem, 1,
                             999 if path.stem == "bad" else 200).target]
        catalog, labels, aliases = cq.build_motion_catalog(
            routes, load, validate, report=logs.append)
        self.assertEqual(list(catalog), ["2"])
        self.assertEqual(aliases["good"], "2")
        self.assertNotIn("estimated", aliases)
        self.assertTrue(any("DISABLED motion 1" in line for line in logs))
        self.assertTrue(any("simulation-only" in line for line in logs))
        plan = cq.QueuePlan(catalog, aliases=aliases)
        with self.assertRaises(ValueError):
            plan.append("bad")
        with self.assertRaises(ValueError):
            plan.append("3")

    def test_named_and_multi_digit_live_editing(self):
        catalog = {str(n): () for n in range(1, 13)}
        aliases = {"grab2": "11", "q_motion": "12"}
        labels = {"11": "grab2", "12": "q_motion"}
        plan = cq.QueuePlan(catalog, ["11"], aliases=aliases, labels=labels)
        controller = cq.InputManager(plan, lambda *_: None)
        controller.active = "1"
        for ch in ":a=q_motion\r":
            controller.handle(ch)
        self.assertFalse(controller.stopped)
        self.assertEqual(list(plan.pending), ["11", "12"])
        for ch in ":i1,grab2\r":
            controller.handle(ch)
        self.assertEqual(list(plan.pending), ["11", "11", "12"])
        for ch in ":d2\r":
            controller.handle(ch)
        self.assertEqual(list(plan.pending), ["11", "12"])
        for ch in ":a12\r":
            controller.handle(ch)
        self.assertEqual(list(plan.pending), ["11", "12", "12"])
        self.assertEqual(controller.active, "1")
        controller.handle(" ")
        self.assertTrue(controller.stopped)

    def test_more_than_nine_files_are_visible(self):
        routes = [(Path(f"motion{i}.json"), {"description": "taught"})
                  for i in range(1, 13)]
        catalog, labels, aliases = cq.build_motion_catalog(
            routes, self.pose, lambda _: None, report=lambda *_: None)
        self.assertEqual(len(catalog), 12)
        self.assertEqual(aliases["motion12"], "12")
        plan = cq.QueuePlan(catalog, aliases=aliases, labels=labels)
        plan.edit("a motion12")
        self.assertEqual(list(plan.pending), ["12"])


if __name__ == "__main__":
    unittest.main()
