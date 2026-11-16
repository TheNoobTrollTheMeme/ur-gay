"""Offline tests for selected-joint batch-paste operations, no robot required."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from batch_paste_bookmark_joint import (
    apply_batch, parse_numbers, parse_targets, prepare_batch,
)
from pose_bookmarks import save_bookmark

JOINTS = ("base_yaw", "shoulder", "elbow", "wrist", "gripper")
SOURCE = dict(zip(JOINTS, (1100, 2500, 1900, 2400, 3100)))
OTHER = dict(zip(JOINTS, (1550, 2700, 1800, 2450, 3000)))


def sequence(*points, name="grab1", input_type="raw", derived=False):
    record = {
        "schema_version": 1, "type": "pose_sequence",
        "name": name, "input_type": input_type,
        "joint_order": [
            {"servo_id": i + 1, "joint": joint}
            for i, joint in enumerate(JOINTS)
        ],
        "points": list(points),
    }
    if derived:
        record["derived_from"] = {"m": "c1r1m.json", "p": "c1r1p.json"}
    return record


def raw_string_point(pose):
    return {"input_values": [str(pose[j]) for j in JOINTS]}


def write(path, record):
    path.write_text(json.dumps(record), encoding="utf-8")


class BatchPasteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.motions = Path(self.tmp.name)
        self.library = self.motions / "_pose_bookmarks.json"
        save_bookmark(self.library, "ttl", SOURCE, JOINTS)
        save_bookmark(self.library, "other", OTHER, JOINTS)

    def test_one_joint_only_across_multiple_selected_waypoints_and_files(self):
        a = self.motions / "grab1.json"
        b = self.motions / "c1r1m.json"
        write(a, sequence(raw_string_point(OTHER),
                          raw_string_point(OTHER),
                          {"bookmark": "other"}))
        write(b, sequence(raw_string_point(OTHER),
                          raw_string_point(OTHER), name="c1r1m"))
        original_a = a.read_bytes()
        original_b = b.read_bytes()

        plans, value = prepare_batch(self.motions, "@ttl", "shoulder",
                                     {"grab1": "1,3", "c1r1m": "2"}, JOINTS)
        self.assertEqual(value, 2500)
        self.assertEqual(a.read_bytes(), original_a)
        self.assertEqual(b.read_bytes(), original_b)
        self.assertEqual([len(p.changes) for p in plans], [2, 1])
        self.assertTrue(plans[0].changes[1].detached)

        backups = apply_batch(plans)
        self.assertEqual(len(backups), 2)
        self.assertEqual(backups[0].read_bytes(), original_a)
        self.assertEqual(backups[1].read_bytes(), original_b)

        first = json.loads(a.read_text())["points"]
        second = json.loads(b.read_text())["points"]
        self.assertEqual(first[0]["input_values"], ["1550", 2500, "1800", "2450", "3000"])
        self.assertEqual(first[1], raw_string_point(OTHER))
        self.assertEqual(first[2]["input_values"],
                         [1550, 2500, 1800, 2450, 3000])
        self.assertEqual(second[0], raw_string_point(OTHER))
        self.assertEqual(second[1]["input_values"][1], 2500)
        # Source pose and other bookmark are not modified.
        self.assertEqual(json.loads(self.library.read_text())["bookmarks"]["other"]["positions_raw"],
                         OTHER)

    def test_same_value_skips_without_detaching_a_link(self):
        route = self.motions / "grab1.json"
        write(route, sequence({"bookmark": "ttl"}, {"bookmark": "ttl"}))
        original = route.read_bytes()
        edits, _ = prepare_batch(self.motions, "ttl", "shoulder",
                                 {"grab1": "all"}, JOINTS)
        self.assertFalse(edits[0].changes)
        self.assertEqual(apply_batch(edits), [])
        self.assertEqual(route.read_bytes(), original)
        self.assertEqual(list(self.motions.glob("grab1.json.*.bak")), [])

    def test_preflight_invalid_selection_does_not_change_any_file(self):
        a = self.motions / "grab1.json"
        b = self.motions / "c1r1a.json"
        write(a, sequence(raw_string_point(OTHER), raw_string_point(OTHER)))
        write(b, sequence(raw_string_point(OTHER), raw_string_point(OTHER),
                          name="c1r1a", derived=True))
        original_a = a.read_bytes()
        with self.assertRaisesRegex(ValueError, "generated"):
            prepare_batch(self.motions, "ttl", "shoulder",
                          {"grab1": "1", "c1r1a": "1"}, JOINTS)
        self.assertEqual(a.read_bytes(), original_a)
        self.assertEqual(list(self.motions.glob("grab1.json.*.bak")), [])

    def test_angle_format_not_silently_interpreted_as_raw(self):
        route = self.motions / "grab1.json"
        write(route, sequence(
            {"input_values": ["20", "30", "40", "50", "60"]},
            {"input_values": ["10", "20", "30", "40", "50"]},
            input_type="angles"))
        with self.assertRaisesRegex(ValueError, "angle-format"):
            prepare_batch(self.motions, "ttl", "shoulder",
                          {"grab1": "1-2"}, JOINTS)

    def test_fails_if_file_changed_after_preview(self):
        route = self.motions / "grab1.json"
        write(route, sequence(raw_string_point(OTHER), raw_string_point(OTHER)))
        plans, _ = prepare_batch(self.motions, "ttl", "shoulder",
                                 {"grab1": "1"}, JOINTS)
        original = route.read_text()
        route.write_text(original + "\n")
        changed = route.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "changed since preview"):
            apply_batch(plans)
        self.assertEqual(route.read_bytes(), changed)
        self.assertEqual(list(self.motions.glob("grab1.json.*.bak")), [])

    def test_invalid_raw_values_fail_preflight_without_backups(self):
        route = self.motions / "grab1.json"
        write(route, sequence(
            {"input_values": ["2000", "not-a-number", "2000", "2000", "2000"]},
            raw_string_point(OTHER)))
        original = route.read_bytes()
        with self.assertRaisesRegex(ValueError, "invalid raw tick"):
            prepare_batch(self.motions, "ttl", "shoulder",
                          {"grab1": "2"}, JOINTS)
        self.assertEqual(route.read_bytes(), original)

    def test_point_and_target_parsing(self):
        self.assertEqual(parse_numbers("1,3-5,3", 5), [1, 3, 4, 5])
        self.assertEqual(parse_numbers("all", 3), [1, 2, 3])
        self.assertEqual(parse_targets(["grab1:1,3", "c1r1m:all"]),
                         {"grab1": "1,3", "c1r1m": "all"})
        for selected in ("", "0", "4", "1-9", "3-1", "1,,2", "-1"):
            with self.subTest(selected=selected):
                with self.assertRaises(ValueError):
                    parse_numbers(selected, 3)
        with self.assertRaisesRegex(ValueError, "selected twice"):
            parse_targets(["grab1:1", "grab1:2"])
        with self.assertRaises(ValueError):
            parse_targets(["../outside:all"])


if __name__ == "__main__":
    unittest.main()
