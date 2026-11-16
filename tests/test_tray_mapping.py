"""Hardware-free tests for editable tray mapping and linked winning lines."""
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import arm_board as board
from tray_mapping import (get_slot, default_mapping, load_mapping,
                          save_mapping, validate_mapping)


JOINTS = ("base", "shoulder", "elbow", "wrist", "gripper")


def pose(n):
    return dict(base=n, shoulder=2000, elbow=2000, wrist=2000, gripper=3000)


class FakeWP:
    @staticmethod
    def load_sequence(path):
        data = json.loads(path.read_text())
        return data, data["poses"]

    @staticmethod
    def save_sequence(path, data):
        path.write_text(json.dumps(data, indent=2))


class RemapTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.motions = Path(self.temp.name) / "motions"
        self.motions.mkdir()
        for i in range(1, 8):
            (self.motions / f"grab{i}.json").write_text(
                json.dumps({"poses": [pose(1900 + i)]})
            )
        for col in range(1, 4):
            for suffix in ("m", "p"):
                (self.motions / f"c{col}r1{suffix}.json").write_text(
                    json.dumps({"poses": [pose(2000 + col)]})
                )
        self.script = SimpleNamespace(
            settings=SimpleNamespace(
                JOINT_NAMES=JOINTS,
                SERVO_IDS={joint: i + 1 for i, joint in enumerate(JOINTS)},
            ),
            wp=FakeWP(),
            validate_competition_target=lambda point: None,
        )

    def test_mapping_defaults_and_flexible_grab_route(self):
        self.assertEqual(get_slot(load_mapping(self.motions), 3)["grab"], "grab3")
        mapping = load_mapping(self.motions)
        one = get_slot(mapping, 1)
        one["zone"] = "B-right"
        one["block"] = "cube"
        one["grab"] = "grab7"
        path, backup = save_mapping(self.motions, mapping)
        self.assertTrue(path.is_file())
        self.assertIsNone(backup)
        plan = board.build_plan(self.script, self.motions, 1, 1, "place", 1)
        self.assertEqual(plan.phases[0], "grab7")
        self.assertEqual(plan.waypoints[0].positions["base"], 1907)
        self.assertEqual(board.pickup_for(1, 1, "place", 1, mapping), 1)
        self.assertTrue(board.pickup_for(2, 2, "attack", 1, mapping) == 1)
        self.assertTrue((self.motions.parent / "tray_mapping.json").is_file())

    def test_reject_missing_and_unsafe_route_names(self):
        bad = default_mapping()
        get_slot(bad, 1)["grab"] = "../other"
        with self.assertRaises(ValueError):
            validate_mapping(bad)
        bad = default_mapping()
        get_slot(bad, 6)["block"] = "empty"
        with self.assertRaises(ValueError):
            validate_mapping(bad)

    def test_line_sync_changes_slot_grab_and_backs_up_original(self):
        picks = (1, 2, 3)
        plans = [board.build_plan(self.script, self.motions, col, 1, "place", pick)
                 for col, pick in zip((1, 2, 3), picks)]
        path = board.export_winning_line(self.script, self.motions, plans)
        original = json.loads(path.read_text())
        self.assertEqual(original["points"][0]["input_values"][0], 1901)
        mapping = load_mapping(self.motions)
        get_slot(mapping, 1)["grab"] = "grab7"
        save_mapping(self.motions, mapping)
        results = board.sync_winning_lines(self.script, self.motions)
        self.assertTrue(any("updated" in result for result in results))
        revised = json.loads(path.read_text())
        self.assertEqual(revised["points"][0]["input_values"][0], 1907)
        self.assertEqual(revised["source_placements"][0]["pickup"], 1)
        self.assertEqual(len(list((self.motions / ".winning-line-backups").glob("*.bak"))), 1)

    def test_center_attack_trophy_validation_follows_new_layout(self):
        mapping = default_mapping()
        with self.assertRaises(ValueError):
            board.pickup_for(2, 2, "attack", 3, mapping)
        get_slot(mapping, 3)["block"] = "cube"
        self.assertEqual(board.pickup_for(2, 2, "attack", 3, mapping), 3)
        get_slot(mapping, 6)["block"] = "cube"
        with self.assertRaisesRegex(ValueError, "select its slot"):
            board.pickup_for(1, 1, "attack", None, mapping)


if __name__ == "__main__":
    unittest.main()
