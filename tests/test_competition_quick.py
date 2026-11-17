"""Hardware-free tests for Quick Competition macros, speed, and replay."""
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location(
    "quickcomp", ROOT / "scripts" / "competition_quick.py"
)
quick = importlib.util.module_from_spec(spec)
spec.loader.exec_module(quick)
import competition_queue as cq


class MockEditor:
    def __init__(self, folder):
        self.folder = folder
        self.competition = SimpleNamespace(
            build_motion_catalog=cq.build_motion_catalog,
            playback=self.playback,
        )
        self.calls = []
        self.joints = ("base_yaw", "shoulder", "elbow", "wrist", "gripper")
        self.settings = SimpleNamespace(JOINT_NAMES=self.joints)
        self.BOOKMARK_FILE = folder / "_pose_bookmarks.json"
        self.wp = SimpleNamespace(find_sequences=self.find_sequences,
                                  load_sequence=self.load_sequence)
        self.RawTerminal = object
        self.connect = lambda: None
        self.read_all = lambda _: {}
        self.enable_all_torque = lambda _: None
        self.disable_all_torque = lambda _: None
        self.check_result = lambda *args: None

    def find_sequences(self):
        return [(p, json.loads(p.read_text())) for p in sorted(self.folder.glob("*.json"))
                if p.stem in {"grab1", "c1r1m", "c1r1p", "unsafe_estimate"}]

    def load_sequence(self, path):
        data = json.loads(path.read_text())
        joints = self.joints
        return data, [dict(zip(joints, item["input_values"])) for item in data["points"]]

    def validate_competition_target(self, pose):
        if set(pose) != set(self.joints):
            raise ValueError("Invalid joints")

    def refresh_attacks(self):
        return 0

    def playback(self, catalog, plan, speed, **kwargs):
        self.calls.append((tuple(plan.pending), speed, kwargs))
        return True


def pose(s):
    return [2000 + s, 2000, 2000, 2000, 1500]


def write_motion(folder, name, value, description="Hand-taught"):
    d = {"name": name, "input_type": "raw", "description": description,
         "points": [{"input_values": pose(value)}, {"input_values": pose(value+5)}]}
    (folder / (name + ".json")).write_text(json.dumps(d))


class QuickCompTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.store_path = self.folder / "_competition_presets.json"
        self.editor = MockEditor(self.folder)
        write_motion(self.folder, "grab1", 0)
        write_motion(self.folder, "c1r1m", 20)
        write_motion(self.folder, "c1r1p", 40)
        write_motion(self.folder, "unsafe_estimate", 80,
                     "OFFLINE SIMULATION ONLY, NOT CLEARED FOR ROBOT")

    def test_named_queue_macro_persists_speed_without_changing_recordings(self):
        catalog, labels, aliases = quick.catalog_for(self.editor, report=lambda *_: None)
        plan, names = quick.resolve_queue(["grab1", "c1r1"], catalog, labels, aliases)
        self.assertEqual(names, ("grab1", "c1r1"))
        self.assertEqual(len(plan.pending), 2)
        original = {p.name: p.read_text() for p in self.folder.glob("*.json")}
        saved = quick.save_macro(quick.load_store(self.store_path), "opener", names,
                                 500, 25, path=self.store_path)
        self.assertEqual(saved["presets"]["opener"]["speed"], 500)
        self.assertEqual(saved["presets"]["opener"]["queue"], ["grab1", "c1r1"])
        for path in self.folder.glob("*.json"):
            if path.name in original:
                self.assertEqual(path.read_text(), original[path.name])

    def test_preset_replays_latest_live_waypoints(self):
        catalog, labels, aliases = quick.catalog_for(self.editor, report=lambda *_: None)
        _, names = quick.resolve_queue(["grab1", "c1r1"], catalog, labels, aliases)
        store = quick.save_macro(quick.load_store(self.store_path), "opener", names,
                                 450, 25, path=self.store_path)
        # Modify motion file AFTER recording the macro.
        write_motion(self.folder, "grab1", 99)
        with patch("builtins.input", return_value="RUN"):
            result = quick.execute(self.editor, store["presets"]["opener"]["queue"],
                                   450, 25, store, path=self.store_path, report=lambda *_: None)
        self.assertTrue(result)
        self.assertEqual(len(self.editor.calls), 1)
        pending, speed, kwargs = self.editor.calls[0]
        self.assertEqual(speed, (450, 25))
        self.assertEqual(kwargs["gripper_profile"], "normal")
        self.assertTrue(kwargs["finish_after_queue"])
        self.assertEqual(quick.load_store(self.store_path)["last"]["queue"],
                         ["grab1", "c1r1"])

    def test_dry_run_has_no_hardware_side_effects(self):
        store = quick.load_store(self.store_path)
        output = []
        result = quick.execute(self.editor, ["grab1"], 500, 25, store,
                               path=self.store_path, dry_run=True, report=output.append)
        self.assertIsNone(result)
        self.assertFalse(self.store_path.exists())
        self.assertEqual(self.editor.calls, [])
        self.assertTrue(any("DRY RUN" in line for line in output))

    def test_simulation_only_path_unavailable_to_quick_macros(self):
        catalog, labels, aliases = quick.catalog_for(self.editor, report=lambda *_: None)
        self.assertNotIn("unsafe_estimate", aliases)
        with self.assertRaisesRegex(ValueError, "Unavailable"):
            quick.resolve_queue(["unsafe_estimate"], catalog, labels, aliases)

    def test_speed_validation_prevents_hardware_or_mutation(self):
        store = quick.load_store(self.store_path)
        with self.assertRaisesRegex(ValueError, "Playback speed"):
            quick.execute(self.editor, ["grab1"], 5000, 25, store,
                          path=self.store_path)
        self.assertEqual(self.editor.calls, [])
        self.assertFalse(self.store_path.exists())

    def test_stale_preset_fails_before_any_motor_access(self):
        store = quick.load_store(self.store_path)
        with self.assertRaisesRegex(ValueError, "Unavailable"):
            quick.execute(self.editor, ["missing_motion"], 500, 25, store,
                          path=self.store_path, yes=True, report=lambda *_: None)
        self.assertEqual(self.editor.calls, [])
        self.assertFalse(self.store_path.exists())

    def test_bad_save_does_not_overwrite_existing_preset(self):
        store = quick.load_store(self.store_path)
        store = quick.save_macro(store, "opening", ["grab1"], 300, 20,
                                 path=self.store_path)
        with self.assertRaisesRegex(ValueError, "exists"):
            quick.save_macro(store, "opening", ["c1r1"], 400, 25,
                             path=self.store_path)
        self.assertEqual(quick.load_store(self.store_path)["presets"]["opening"]["queue"],
                         ["grab1"])

    def test_editor_saves_and_starts_from_single_run_command(self):
        # Four editor commands, one explicit 'run', no '2 34 Enter' workflow.
        cmds = iter(["a grab1 c1r1", "speed 500 25", "save opener", "run"])
        with patch("builtins.input", side_effect=lambda _: next(cmds)):
            self.assertTrue(quick.interactive(self.editor, path=self.store_path))
        self.assertEqual(len(self.editor.calls), 1)
        self.assertEqual(self.editor.calls[0][1], (500, 25))
        self.assertEqual(quick.load_store(self.store_path)["presets"]["opener"]["queue"],
                         ["grab1", "c1r1"])


if __name__ == "__main__":
    unittest.main()
