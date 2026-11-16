"""Hardware-free regression tests for @bookmark compatibility across arm scripts."""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from competition_queue import QueuePlan, build_motion_catalog
from pose_bookmarks import get_bookmark, save_bookmark

JOINTS = ("base_yaw", "shoulder", "elbow", "wrist", "gripper")
POSE = dict(zip(JOINTS, (1000, 2000, 2400, 2500, 3100)))
NEXT = dict(zip(JOINTS, (1400, 2100, 2300, 2600, 3200)))


def raw_sequence(*points):
    return {
        "schema_version": 1,
        "type": "pose_sequence",
        "input_type": "raw",
        "joint_order": [
            {"servo_id": number, "joint": name}
            for number, name in enumerate(JOINTS, 1)
        ],
        "name": "grab1",
        "points": list(points),
    }


def raw_parser(values, input_type):
    if input_type != "raw":
        raise ValueError("Expected raw")
    if len(values) != len(JOINTS):
        raise ValueError("Expected five servo ticks")
    return dict(zip(JOINTS, values))


class BookmarkPlaybackCompatTests(unittest.TestCase):
    def test_kevin_script_5_bridge_resolves_bookmarks_without_changing_original(self):
        spec = importlib.util.spec_from_file_location(
            "bookmark_compat_test", ROOT / "scripts" / "05_bookmark_compat.py")
        bridge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bridge)

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            library = folder / "_pose_bookmarks.json"
            route = folder / "grab1.json"
            save_bookmark(library, "ttl", POSE, JOINTS)
            route.write_text(json.dumps(
                raw_sequence({"input_values": list(NEXT.values())},
                             {"bookmark": "ttl"})), encoding="utf-8")

            original_calls = []
            def old_loader(path):
                original_calls.append(Path(path).name)
                data = json.loads(Path(path).read_text())
                for point in data["points"]:
                    if "bookmark" in point:
                        raise ValueError("point needs input_values or raw positions")
                return data, [raw_parser(p["input_values"], "raw")
                              for p in data["points"]]

            script5 = SimpleNamespace(
                load_sequence=old_loader, parse_point_values=raw_parser)
            bridge.enable_bookmarks(script5, JOINTS, library)
            _, points = script5.load_sequence(route)
            self.assertEqual(points, [NEXT, POSE])
            self.assertEqual(original_calls, [])
            self.assertEqual(json.loads(route.read_text())["points"][1],
                             {"bookmark": "ttl"})

            # Linked values change after updating the single saved pose.
            save_bookmark(library, "ttl", NEXT, JOINTS, overwrite=True)
            _, points = script5.load_sequence(route)
            self.assertEqual(points[-1], NEXT)
            bridge.enable_bookmarks(script5, JOINTS, library)
            _, points = script5.load_sequence(route)
            self.assertEqual(len(points), 2)

            plain = folder / "plain.json"
            plain.write_text(json.dumps(raw_sequence(
                {"input_values": list(POSE.values())},
                {"input_values": list(NEXT.values())})), encoding="utf-8")
            _, points = script5.load_sequence(plain)
            self.assertEqual(points, [POSE, NEXT])
            self.assertEqual(original_calls, ["plain.json"])

    def test_universal_queue_accepts_single_pose_by_bookmark_alias(self):
        catalog, labels, aliases = build_motion_catalog(
            [], lambda _: None, lambda pose: None,
            bookmarks={"ttl": POSE},
        )
        self.assertEqual(len(catalog), 1)
        key = aliases["@ttl"]
        self.assertEqual(labels[key], "@ttl")
        self.assertEqual(len(catalog[key]), 1)
        self.assertEqual(catalog[key][0].target, POSE)
        plan = QueuePlan(catalog, ["@ttl"], aliases=aliases, labels=labels)
        self.assertEqual(plan.pop(), key)

    def test_unusable_bookmark_fails_closed_without_a_queue_entry(self):
        warnings = []
        catalog, _, aliases = build_motion_catalog(
            [], lambda _: None,
            lambda pose: (_ for _ in ()).throw(ValueError("bad value"))
                         if pose["shoulder"] < 0 else None,
            report=warnings.append,
            bookmarks={"ttl": {**POSE, "shoulder": -1}},
        )
        self.assertEqual(catalog, {})
        self.assertEqual(aliases, {})
        self.assertTrue(any("DISABLED bookmark @ttl" in x for x in warnings))


if __name__ == "__main__":
    unittest.main()
