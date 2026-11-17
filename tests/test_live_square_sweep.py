"""Hardware-free checks of the conservative live 9-square round-trip test."""
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("live_square_sweep", ROOT / "scripts/06_live_square_sweep.py")
sweep = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sweep)
JOINTS = ("base_yaw", "shoulder", "elbow", "wrist", "gripper")
HOME_POSE = dict(zip(JOINTS, (1000, 1000, 1000, 1000, 1500)))


class FakePort:
    def __init__(self):
        self.closed = False

    def closePort(self):
        self.closed = True


class FakeTerminal:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read_key(self, timeout):
        return "\n"


class FakeEditor:
    def __init__(self):
        self.settings = SimpleNamespace(
            JOINT_NAMES=JOINTS,
            SERVO_IDS={name: n + 1 for n, name in enumerate(JOINTS)},
        )
        self.BOOKMARK_FILE = Path("mock-bookmarks.json")
        self.bookmarks = {"sweep_home": dict(HOME_POSE)}
        self.current = dict(HOME_POSE)
        self.moves = []
        self.connect_calls = 0
        self.enable_calls = 0
        self.disable_calls = 0
        self.port = None
        self.wp = SimpleNamespace(load_sequence=self.load_sequence)
        self.RawTerminal = FakeTerminal

    def get_bookmark(self, filename, name, joints):
        return dict(self.bookmarks[name])

    def validate_competition_target(self, pose):
        if set(pose) != set(JOINTS) or not all(type(v) is int and 0 <= v <= 4095
                                             for v in pose.values()):
            raise ValueError("Invalid raw pose")

    def validate_target(self, pose):
        self.validate_competition_target(pose)

    def load_sequence(self, filename):
        data = json.loads(filename.read_text())
        points = []
        for item in data["points"]:
            if "bookmark" in item:
                points.append(dict(self.bookmarks[item["bookmark"]]))
            else:
                points.append(dict(zip(JOINTS, map(int, item["input_values"]))))
        return data, points

    def connect(self):
        self.connect_calls += 1
        self.port = FakePort()
        return self.port, self

    def read_all(self, handler):
        return dict(self.current)

    def move_all_sync(self, handler, positions, speed, acceleration):
        self.current = dict(positions)
        self.moves.append((dict(positions), speed, acceleration))

    def enable_all_torque(self, handler):
        self.enable_calls += 1

    def disable_all_torque(self, handler):
        self.disable_calls += 1

    def stopped(self, terminal):
        return False

    def wait_move(self, handler, terminal):
        return True


def save_route(folder, cell, *, via=40, end=60):
    name = f"live_{cell}"
    data = {
        "name": name,
        "input_type": "raw",
        "points": [
            {"bookmark": "sweep_home"},
            {"input_values": [1000 + via, 1000, 1000, 1000, 1500]},
            {"input_values": [1000 + end, 1000, 1000, 1000, 1500]},
        ],
    }
    path = folder / (name + ".json")
    path.write_text(json.dumps(data))
    return path


class LiveSquareSweepTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.editor = FakeEditor()

    def populate(self):
        return {cell: save_route(self.folder, cell, via=40, end=60 + i * 4)
                for i, cell in enumerate(sweep.ORDER)}

    def test_snake_order_retraces_all_nine(self):
        self.assertEqual(len(sweep.ORDER), 9)
        self.assertEqual(len(set(sweep.ORDER)), 9)
        self.assertEqual(len(sweep.ROUND_TRIP), 17)
        self.assertEqual(sweep.ROUND_TRIP[:9], sweep.ORDER)
        self.assertEqual(sweep.ROUND_TRIP[9:], sweep.ORDER[-2::-1])
        self.assertEqual(sweep.ROUND_TRIP[-1], "c1r1")

    def test_teaching_templates_never_overwrite(self):
        sweep.initialize_routes(self.editor, self.folder)
        created = list(self.folder.glob("live_*.json"))
        self.assertEqual(len(created), 9)
        for path in created:
            data = json.loads(path.read_text())
            self.assertEqual(data["points"][0], {"bookmark": "sweep_home"})
        marker = created[0].read_text()
        sweep.initialize_routes(self.editor, self.folder)
        self.assertEqual(created[0].read_text(), marker)
        self.assertEqual(self.editor.connect_calls, 0)

    def test_placeholders_fail_preflight_before_hardware(self):
        sweep.initialize_routes(self.editor, self.folder)
        with self.assertRaisesRegex(ValueError, "preflight failed"):
            sweep.preflight(self.editor, self.folder)
        self.assertEqual(self.editor.connect_calls, 0)

    def test_real_routes_validate_and_respond_to_live_edit(self):
        self.populate()
        _, routes = sweep.preflight(self.editor, self.folder)
        self.assertEqual(len(routes), 9)
        self.assertEqual(routes["c1r1"][-1]["base_yaw"], 1060)
        save_route(self.folder, "c1r1", via=50, end=90)
        updated = sweep.read_route(self.editor, "c1r1", HOME_POSE, self.folder)
        self.assertEqual(updated[-1]["base_yaw"], 1090)
        self.assertEqual(self.editor.connect_calls, 0)

    def test_rejects_large_joint_step_not_just_bad_register(self):
        self.populate()
        save_route(self.folder, "c1r1", via=300, end=330)
        with self.assertRaisesRegex(ValueError, "segment 1->2"):
            sweep.preflight(self.editor, self.folder)

    def test_rejects_unlinked_home(self):
        paths = self.populate()
        data = json.loads(paths["c1r1"].read_text())
        data["points"][0] = {"input_values": [1000] * 4 + [1500]}
        paths["c1r1"].write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "starting with linked"):
            sweep.preflight(self.editor, self.folder)

    def test_rejects_wrong_start_before_first_motor_move(self):
        self.populate()
        self.editor.current["shoulder"] = 1500
        with patch("builtins.input", return_value="RUN"):
            with self.assertRaisesRegex(RuntimeError, "not at @sweep_home"):
                sweep.run(self.editor, self.folder)
        self.assertEqual(self.editor.enable_calls, 0)
        self.assertEqual(len(self.editor.moves), 0)
        self.assertEqual(self.editor.disable_calls, 1)
        self.assertTrue(self.editor.port.closed)

    def test_full_round_trip_uses_home_between_visits(self):
        self.populate()
        with patch("builtins.input", return_value="CONTINUOUS"):
            self.assertTrue(sweep.run(self.editor, self.folder, continuous=True))
        self.assertEqual(self.editor.enable_calls, 1)
        self.assertEqual(self.editor.disable_calls, 0)
        self.assertTrue(self.editor.port.closed)
        self.assertEqual(self.editor.current, HOME_POSE)
        # Initial stationary hold + (2 points out, 2 back) for each of 17 visits.
        self.assertEqual(len(self.editor.moves), 1 + 17 * 4)
        # Every fourth travel command returns to the home pose.
        for n in range(4, len(self.editor.moves), 4):
            self.assertEqual(self.editor.moves[n][0], HOME_POSE)
        self.assertTrue(all((speed, accel) == (300, 20)
                            for _, speed, accel in self.editor.moves))


if __name__ == "__main__":
    unittest.main()
