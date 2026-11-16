"""Hardware-free tests for Script 6's reusable single-pose bookmarks."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from pose_bookmarks import (
    save_bookmark, list_bookmarks, get_bookmark,
    resolve_points, save_linked_sequence, load_with_bookmarks,
    delete_bookmark, referenced_by,
)

JOINTS = ('base_yaw', 'shoulder', 'elbow', 'wrist', 'gripper')
POSE = dict(zip(JOINTS, [1000, 2500, 1900, 2500, 3100]))
POSE2 = dict(zip(JOINTS, [1500, 2400, 1800, 2600, 3000]))


def parse_values(values, input_type):
    if input_type != 'raw':
        raise ValueError('test supports only raw')
    return dict(zip(JOINTS, [int(value) for value in values]))


def sequence(*points):
    return dict(schema_version=1, type='pose_sequence', input_type='raw',
                joint_order=[{'servo_id': i + 1, 'joint': name}
                             for i, name in enumerate(JOINTS)],
                name='grab1', points=list(points))


class BookmarkTests(unittest.TestCase):
    def test_one_pose_updates_all_linked_waypoints_without_rewriting_routes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / '_pose_bookmarks.json'
            route = Path(tmp) / 'grab1.json'
            self.assertIsNone(save_bookmark(path, 'Safe_Left', POSE, JOINTS))
            data = sequence({'input_values': [1, 2, 3, 4, 5]},
                            {'bookmark': 'safe_left'})
            self.assertIsNone(save_linked_sequence(
                route, data, parse_values, JOINTS, path))
            self.assertEqual(resolve_points(data, parse_values, JOINTS, path)[1], POSE)
            with self.assertRaisesRegex(ValueError, 'exists'):
                save_bookmark(path, 'safe_left', POSE2, JOINTS)
            self.assertIsNotNone(save_bookmark(
                path, 'safe_left', POSE2, JOINTS, overwrite=True))
            self.assertEqual(resolve_points(data, parse_values, JOINTS, path)[1], POSE2)
            self.assertEqual(json.loads(route.read_text())['points'][1],
                             {'bookmark': 'safe_left'})
            with self.assertRaisesRegex(ValueError, 'referenced'):
                delete_bookmark(path, 'safe_left', JOINTS)
            self.assertEqual(referenced_by(tmp, 'safe_left'), ['grab1'])

    def test_bad_sensor_values_do_not_overwrite_saved_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / '_pose_bookmarks.json'
            save_bookmark(path, 'safe_left', POSE, JOINTS)
            original = path.read_bytes()
            for bad in (-1, 4096, True, '2500', 3.5):
                with self.subTest(bad=bad):
                    with self.assertRaises(ValueError):
                        save_bookmark(path, 'safe_left',
                                      {**POSE, 'shoulder': bad}, JOINTS,
                                      overwrite=True)
                    self.assertEqual(path.read_bytes(), original)
            self.assertIn('safe_left', list_bookmarks(path, JOINTS))
            self.assertEqual(get_bookmark(path, 'safe_left', JOINTS), POSE)

    def test_missing_links_and_mixed_fields_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / '_pose_bookmarks.json'
            save_bookmark(path, 'safe', POSE, JOINTS)
            good = sequence({'input_values': [1, 2, 3, 4, 5]},
                            {'bookmark': 'safe'})
            self.assertEqual(resolve_points(good, parse_values, JOINTS, path)[1], POSE)
            for bad in ({'bookmark': 'missing'},
                        {'bookmark': 'safe', 'input_values': [1, 2, 3, 4, 5]},
                        {'bookmark': 'Unsafe Name'}):
                with self.subTest(bad=bad):
                    with self.assertRaises(ValueError):
                        resolve_points(sequence(bad), parse_values, JOINTS, path)

    def test_existing_unlinked_recordings_use_original_loader(self):
        with tempfile.TemporaryDirectory() as tmp:
            library = Path(tmp) / '_pose_bookmarks.json'
            route = Path(tmp) / 'old.json'
            route.write_text(json.dumps(sequence({'input_values': [1, 2, 3, 4, 5]})))
            calls = []
            def base_loader(path):
                calls.append(path)
                return 123
            self.assertEqual(load_with_bookmarks(
                route, base_loader, parse_values, JOINTS, library), 123)
            save_bookmark(library, 'safe', POSE, JOINTS)
            route.write_text(json.dumps(sequence({'bookmark': 'safe'},
                                                 {'bookmark': 'safe'})))
            _, poses = load_with_bookmarks(
                route, base_loader, parse_values, JOINTS, library)
            self.assertEqual(poses, [POSE, POSE])
            self.assertEqual(calls, [route])

    def test_can_delete_unreferenced_bookmark(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / '_pose_bookmarks.json'
            save_bookmark(path, 'free', POSE, JOINTS)
            self.assertIsNotNone(delete_bookmark(path, 'free', JOINTS))
            self.assertEqual(list_bookmarks(path, JOINTS), {})


if __name__ == '__main__':
    unittest.main()
