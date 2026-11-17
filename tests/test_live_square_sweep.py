"""Hardware-free regression tests for exact saved move/reverse sweep playback."""
import importlib.util
import io
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "live_square_sweep", ROOT / "scripts/06_live_square_sweep.py")
sweep = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sweep)
JOINTS = ("base_yaw", "shoulder", "elbow", "wrist", "gripper")
EXPECTED_ORDER = (
    "c1r1", "c1r2", "c1r3", "c2r1", "c2r2", "c2r3", "c3r1", "c3r2", "c3r3")


def pose(base=1000, shoulder=1900, gripper=1500):
    return dict(zip(JOINTS, (base, shoulder, 1800, 1700, gripper)))


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
    """A pose simulator, with separate stationary-hold and playback records."""
    def __init__(self):
        self.settings = SimpleNamespace(
            JOINT_NAMES=JOINTS,
            SERVO_IDS={name: n + 1 for n, name in enumerate(JOINTS)},
        )
        self.bookmarks = {}
        self.current = pose(base=950, gripper=2200)
        self.holds = []
        self.playback = []
        self.loaded = []
        self.connect_calls = 0
        self.enable_calls = 0
        self.disable_calls = 0
        self.stop_requested = False
        self.wait_result = True
        self.on_move = None
        self.port = None
        self.wp = SimpleNamespace(load_sequence=self.load_sequence)
        self.RawTerminal = FakeTerminal

    def validate_competition_target(self, positions):
        if set(positions) != set(JOINTS) or not all(
                type(v) is int and 0 <= v <= 4095 for v in positions.values()):
            raise ValueError("Invalid raw pose")

    def validate_target(self, positions):
        raise AssertionError("Raw sweep must not reinterpret legacy angle calibration")

    def load_sequence(self, filename):
        self.loaded.append(filename.name)
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
        self.holds.append((dict(positions), speed, acceleration))

    def move_for_playback(self, handler, positions, speed, acceleration):
        self.current = dict(positions)
        self.playback.append((dict(positions), speed, acceleration))
        if self.on_move is not None:
            self.on_move()

    def enable_all_torque(self, handler):
        self.enable_calls += 1

    def disable_all_torque(self, handler):
        self.disable_calls += 1

    def stopped(self, terminal):
        return self.stop_requested

    def wait_move(self, handler, terminal):
        return self.wait_result


def save_route(folder, cell, points, *, input_type="raw"):
    data = {
        "schema_version": 1,
        "type": "pose_sequence",
        "name": f"{cell}m",
        "input_type": input_type,
        "joint_order": [{"servo_id": i + 1, "joint": j}
                        for i, j in enumerate(JOINTS)],
        "points": [{"input_values": [point[j] for j in JOINTS]}
                   for point in points],
    }
    path = folder / f"{cell}m.json"
    path.write_text(json.dumps(data))
    return path


class LiveSquareSweepTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.editor = FakeEditor()
        self.output = io.StringIO()
        self.stdout = patch("sys.stdout", self.output)
        self.stdout.start()
        self.addCleanup(self.stdout.stop)

    def populate(self):
        routes = {}
        for i, cell in enumerate(EXPECTED_ORDER):
            # Distinct starts, gripper values and >250-tick recorded steps.
            points = (pose(base=1000 + i * 30, gripper=1400 + i * 10),
                      pose(base=1350 + i * 30, gripper=2600 + i * 10))
            save_route(self.folder, cell, points)
            routes[cell] = points
        return routes

    def run_continuous(self, **kwargs):
        with patch("builtins.input", return_value="CONTINUOUS"):
            return sweep.run(self.editor, self.folder, continuous=True, **kwargs)

    def assert_stopped_and_closed(self):
        self.assertEqual(self.editor.disable_calls, 1)
        self.assertTrue(self.editor.port.closed)

    def test_column_major_order_visits_exactly_nine_move_files(self):
        self.assertEqual(sweep.ORDER, EXPECTED_ORDER)
        self.assertEqual(len(set(sweep.ORDER)), 9)
        for cell in EXPECTED_ORDER:
            self.assertEqual(sweep.route_path(cell, self.folder),
                             self.folder / f"{cell}m.json")

    def test_exact_reverse_excludes_duplicate_turnaround(self):
        a, b, c = pose(), pose(base=1400), pose(base=1900)
        self.assertEqual(sweep.round_trip_steps((a, b)), (a, b, a))
        self.assertEqual(sweep.round_trip_steps((a, b, c)), (a, b, c, b, a))

    def test_preflight_accepts_saved_two_point_routes_with_large_steps(self):
        expected = self.populate()
        routes = sweep.preflight(self.editor, self.folder)
        self.assertEqual(routes, expected)
        self.assertEqual(self.editor.connect_calls, 0)

    def test_read_route_copies_decoded_poses(self):
        expected = self.populate()["c1r1"]
        shared = [dict(p) for p in expected]
        data = json.loads((self.folder / "c1r1m.json").read_text())
        with patch.object(self.editor.wp, "load_sequence", return_value=(data, shared)):
            snapshot = sweep.read_route(self.editor, "c1r1", self.folder)
        shared[0]["gripper"] = 999
        shared[-1]["base_yaw"] = 999
        self.assertEqual(snapshot, expected)

    def test_full_run_replays_each_move_and_reverse_once_at_selected_speed(self):
        routes = self.populate()
        initial = dict(self.editor.current)
        self.assertTrue(self.run_continuous(speed=500, acceleration=25))
        expected = [point for cell in EXPECTED_ORDER
                    for point in (routes[cell][0], routes[cell][1], routes[cell][0])]
        self.assertEqual([item[0] for item in self.editor.playback], expected)
        self.assertEqual(len(self.editor.playback), 27)
        self.assertTrue(all(item[1:] == (500, 25) for item in self.editor.playback))
        self.assertEqual(self.editor.holds, [(initial, 300, 20)])
        self.assertEqual(self.editor.current, routes["c3r3"][0])
        self.assertEqual(self.editor.enable_calls, 1)
        self.assertEqual(self.editor.disable_calls, 0)
        self.assertTrue(self.editor.port.closed)

    def test_three_point_route_reverses_all_saved_joints(self):
        self.populate()
        points = (pose(base=1100, gripper=1400), pose(base=1200, gripper=2600),
                  pose(base=1500, gripper=1800))
        save_route(self.folder, "c1r1", points)
        self.assertTrue(self.run_continuous())
        expected = points + points[-2::-1]
        self.assertEqual(tuple(item[0] for item in self.editor.playback[:5]), expected)

    def test_route_snapshot_survives_file_edit_but_next_route_reloads(self):
        routes = self.populate()
        changed = (pose(base=1800, gripper=2000), pose(base=2200, gripper=2700))

        def edit_during_first_move():
            if len(self.editor.playback) == 1:
                save_route(self.folder, "c1r1", changed)
                save_route(self.folder, "c1r2", changed)

        self.editor.on_move = edit_during_first_move
        self.assertTrue(self.run_continuous())
        expected_first = routes["c1r1"] + routes["c1r1"][-2::-1]
        self.assertEqual(tuple(item[0] for item in self.editor.playback[:3]), expected_first)
        self.assertEqual(tuple(item[0] for item in self.editor.playback[3:6]),
                         changed + changed[-2::-1])

    def test_bookmark_edit_does_not_change_return_snapshot(self):
        routes = self.populate()
        before = routes["c1r1"]
        self.editor.bookmarks.update(entry=dict(before[0]), turn=dict(before[1]))
        for cell in ("c1r1", "c1r2"):
            path = self.folder / f"{cell}m.json"
            data = json.loads(path.read_text())
            data["points"] = [{"bookmark": "entry"}, {"bookmark": "turn"}]
            path.write_text(json.dumps(data))
        changed = (pose(base=1600, gripper=1800), pose(base=2000, gripper=2500))

        def edit_bookmarks():
            if len(self.editor.playback) == 1:
                self.editor.bookmarks["entry"].update(changed[0])
                self.editor.bookmarks["turn"].update(changed[1])

        self.editor.on_move = edit_bookmarks
        self.assertTrue(self.run_continuous())
        self.assertEqual(tuple(item[0] for item in self.editor.playback[:3]),
                         before + before[-2::-1])
        self.assertEqual(tuple(item[0] for item in self.editor.playback[3:6]),
                         changed + changed[-2::-1])

    def test_missing_or_invalid_route_fails_before_connection(self):
        cases = ("missing", "malformed_json", "empty", "one_point", "bad_register", "angles")
        for case in cases:
            with self.subTest(case=case):
                self.populate()
                path = self.folder / "c3r3m.json"
                if case == "missing":
                    path.unlink()
                elif case == "malformed_json":
                    path.write_text("{not JSON")
                elif case == "empty":
                    save_route(self.folder, "c3r3", ())
                elif case == "one_point":
                    save_route(self.folder, "c3r3", (pose(),))
                elif case == "bad_register":
                    save_route(self.folder, "c3r3", (pose(), pose(base=5000)))
                else:
                    save_route(self.folder, "c3r3", (pose(), pose()), input_type="angles")
                with patch("builtins.input", return_value="CONTINUOUS"):
                    with self.assertRaises(ValueError):
                        sweep.run(self.editor, self.folder, continuous=True)
                self.assertEqual(self.editor.connect_calls, 0)

    def test_confirmation_cancellation_never_connects(self):
        self.populate()
        with patch("builtins.input", return_value="cancel"):
            self.assertFalse(sweep.run(self.editor, self.folder, continuous=True))
        self.assertEqual(self.editor.connect_calls, 0)

    def test_confirmation_time_file_change_rechecked_before_connection(self):
        self.populate()

        def confirm_after_removal(_prompt):
            (self.folder / "c3r3m.json").unlink()
            return "CONTINUOUS"

        with patch("builtins.input", side_effect=confirm_after_removal):
            with self.assertRaises(ValueError):
                sweep.run(self.editor, self.folder, continuous=True)
        self.assertEqual(self.editor.connect_calls, 0)

    def test_guided_mode_waits_before_each_moving_waypoint(self):
        self.populate()
        with patch("builtins.input", return_value="RUN"):
            with patch.object(sweep, "await_enter", return_value=True) as gate:
                self.assertTrue(sweep.run(self.editor, self.folder))
        self.assertEqual(gate.call_count, 27)

    def test_cancel_guided_gate_does_not_play_first_target(self):
        self.populate()
        with patch("builtins.input", return_value="RUN"):
            with patch.object(sweep, "await_enter", return_value=False):
                self.assertFalse(sweep.run(self.editor, self.folder))
        self.assertEqual(self.editor.playback, [])
        self.assert_stopped_and_closed()

    def test_stop_during_outward_leg_never_runs_return_or_next_cell(self):
        self.populate()

        def request_stop():
            if len(self.editor.playback) == 2:
                self.editor.stop_requested = True

        self.editor.on_move = request_stop
        self.assertFalse(self.run_continuous())
        self.assertEqual(len(self.editor.playback), 2)
        self.assert_stopped_and_closed()

    def test_wait_failure_releases_torque_and_closes_port(self):
        self.populate()
        self.editor.wait_result = False
        self.assertFalse(self.run_continuous())
        self.assertEqual(len(self.editor.playback), 1)
        self.assert_stopped_and_closed()

    def test_failed_arrival_stops_without_next_waypoint(self):
        self.populate()

        def miss_target():
            self.editor.current["base_yaw"] += 91

        self.editor.on_move = miss_target
        with self.assertRaises(RuntimeError):
            self.run_continuous()
        self.assertEqual(len(self.editor.playback), 1)
        self.assert_stopped_and_closed()

    def test_motor_exception_still_releases_torque_and_closes_port(self):
        self.populate()

        def motor_failure():
            raise RuntimeError("simulated serial failure")

        self.editor.on_move = motor_failure
        with self.assertRaisesRegex(RuntimeError, "serial failure"):
            self.run_continuous()
        self.assertEqual(len(self.editor.playback), 1)
        self.assert_stopped_and_closed()

    def test_invalid_speed_settings_rejected_before_connection(self):
        self.populate()
        for speed, acceleration in ((0, 20), (1201, 20), (300, 0), (300, 61)):
            with self.subTest(speed=speed, acceleration=acceleration):
                with self.assertRaises(ValueError):
                    self.run_continuous(speed=speed, acceleration=acceleration)
        self.assertEqual(self.editor.connect_calls, 0)

    def test_audit_reads_existing_move_files_without_writing_or_connecting(self):
        self.populate()
        before = {p.name: p.read_bytes() for p in self.folder.iterdir()}
        report = sweep.audit_existing(self.editor, self.folder)
        self.assertEqual(report["square_count"], 9)
        self.assertEqual({p.name: p.read_bytes() for p in self.folder.iterdir()}, before)
        self.assertEqual(self.editor.connect_calls, 0)

    def test_menu_keeps_cli_speed_as_prompt_default(self):
        with patch.object(sweep, "load_editor", return_value=self.editor):
            with patch("builtins.input", side_effect=["c", "", ""]):
                with patch.object(sweep, "run", return_value=True) as run:
                    sweep.main(["--menu", "--speed", "500", "--accel", "25"])
        run.assert_called_once_with(self.editor, continuous=True,
                                    speed=500, acceleration=25)

    def test_cli_forwards_speed_and_both_acceleration_aliases(self):
        cases = (("--continuous", "--accel", True),
                 ("--run", "--acceleration", False))
        for mode, flag, continuous in cases:
            with self.subTest(mode=mode, flag=flag):
                with patch.object(sweep, "load_editor", return_value=self.editor):
                    with patch.object(sweep, "run", return_value=True) as run:
                        sweep.main([mode, "--speed", "500", flag, "25"])
                run.assert_called_once()
                self.assertIs(run.call_args.args[0], self.editor)
                self.assertEqual(run.call_args.kwargs["speed"], 500)
                self.assertEqual(run.call_args.kwargs["acceleration"], 25)
                self.assertEqual(run.call_args.kwargs["continuous"], continuous)


if __name__ == "__main__":
    unittest.main()
