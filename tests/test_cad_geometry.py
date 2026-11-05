import math
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from simulation_safety.cad_geometry import ASSET, CadArm, RawCadCollision, cad_collision_model, norm
from simulation_safety.rack_scene import rack_boxes
from simulation_safety.supervisor import MockDriver, SafetyError, Supervisor, load_sequence


class CadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.arm = CadArm()

    def test_all_mesh_vertices_covered(self):
        with zipfile.ZipFile(ASSET) as archive:
            for name, (center, radius) in self.arm.covers.items():
                for line in archive.read(name+'.obj').decode().splitlines():
                    if line.startswith('v '):
                        vertex = tuple(map(float, line.split()[1:]))
                        self.assertLessEqual(norm(tuple(a-b for a, b in zip(vertex, center))), radius)

    def test_fk_base_axis_and_mimic_jaws(self):
        zero = self.arm.transforms((0.,)*5)
        yaw = self.arm.transforms((math.pi/2, 0, 0, 0, 0))
        self.assertAlmostEqual(zero['yaw_platform'][1][2], .0497)
        # Negative-Z yaw rotates X to -Y, and the sibling gear counter-rotates.
        self.assertAlmostEqual(yaw['yaw_platform'][0][1][0], -1.)
        self.assertAlmostEqual(yaw['base_gear'][0][1][0], 1.)
        jaws = self.arm.transforms((0, 0, 0, 0, 1))
        da = tuple(a-b for a, b in zip(jaws['jaw_a'][1], zero['jaw_a'][1]))
        db = tuple(a-b for a, b in zip(jaws['jaw_b'][1], zero['jaw_b'][1]))
        self.assertAlmostEqual(norm(da), .0125)
        self.assertTrue(all(abs(a+b) < 1e-10 for a, b in zip(da, db)))
        self.assertEqual(len(self.arm.geometry((0.,)*5)), 9)

    def test_all_link_transforms_match_16_upstream_browser_fixtures(self):
        fixture = ASSET.parent/'source_evidence'/'browser_fk_reference.json'
        for case in json.loads(fixture.read_text())['fk']:
            transforms = self.arm.transforms(case['q'])
            for name, expected in case['bodies'].items():
                rotation, position = transforms[name]
                self.assertLess(max(abs(a-b) for a, b in zip(position, expected['p'])), 1e-8)
                flat = [v for row in rotation for v in row]
                self.assertLess(max(abs(a-b) for a, b in zip(flat, expected['r'])), 1e-8)

    def test_displacement_bounds_on_multijoint_path(self):
        a, b = (0, -.3, -.4, .2, -.5), (.5, .4, .3, 1.2, .7)
        start = self.arm.geometry(a)
        for i in range(1, 31):
            q = tuple(x+(y-x)*i/30 for x, y in zip(a, b))
            end = self.arm.geometry(q)
            for name in start:
                distance = norm(tuple(x-y for x, y in zip(start[name][0], end[name][0])))
                bound = sum(abs(x-y)*v for x, y, v in zip(a, q, self.arm.bounds[name]))
                self.assertLessEqual(distance, bound+1e-10)

    def test_explicit_carried_block_cover_moves_with_wrist_and_collides(self):
        # Synthetic block dimensions/grasp, not a claim about the real payload.
        arm = CadArm(payload_covers={'test': ((0, 0, -.4), .02)})
        q = (0, 0, 0, .8, 0)
        name = 'carried_block:test'
        center, radius = arm.geometry(q)[name]
        self.assertEqual(radius, .02)
        self.assertEqual(arm.bounds[name], arm.bounds['wrist_carrier'])
        obstacle = (tuple(v-.001 for v in center), tuple(v+.001 for v in center))
        model = cad_collision_model(arm, {'synthetic_payload_obstacle': obstacle},
                                    clearance=0, uncertainty=0, samples=10)
        with self.assertRaisesRegex(SafetyError, 'carried_block'):
            model.check(q, q)
        with self.assertRaises(SafetyError):
            CadArm(payload_covers={})

    def test_cad_collision_engine_integration_and_range_rejection(self):
        # A synthetic obstruction overlaps a real CAD jaw cover.
        arm = self.arm
        a, b = (0, 0, 0, 0, -1), (0, 0, 0, 0, 1)
        center, radius = arm.geometry((0.,)*5)['jaw_a']
        # Check engine integration with actual CAD covers; collision is expected.
        box = (tuple(v-.0001 for v in center), tuple(v+.0001 for v in center))
        collision = cad_collision_model(arm, {'synthetic_jaw_obstruction': box}, clearance=0, uncertainty=0, samples=3)
        with self.assertRaisesRegex(SafetyError, 'collision'):
            collision.check(a, b)
        far = cad_collision_model(arm, {'synthetic_far_scene': ((10, 10, 10), (11, 11, 11))},
                                  clearance=.001, uncertainty=.002, samples=10)
        far.check(a, b)
        with self.assertRaisesRegex(SafetyError, 'range'):
            far.check(a, (0, 100, 0, 0, 0))

    def test_cad_swept_yaw_rejects_obstacle_between_clear_endpoints(self):
        a, b = (-1.5, 0, 0, 0, 0), (1.5, 0, 0, 0, 0)
        center, _ = self.arm.geometry((0.,)*5)['jaw_a']
        obstacle = (tuple(v-.0001 for v in center), tuple(v+.0001 for v in center))
        model = cad_collision_model(self.arm, {'synthetic_midpath': obstacle},
                                    clearance=0, uncertainty=0, samples=1)
        model.check(a, a)
        model.check(b, b)
        # With only endpoint samples, the displacement inflation still rejects.
        with self.assertRaisesRegex(SafetyError, 'collision'):
            model.check(a, b)

    def test_all_raw_paths_block_at_cad_adapter(self):
        class EncodingOnlyPolicy:
            stale_seconds = .1  # Synthetic mock-only feedback deadline.

            def validate(self, pose):
                # Test-only encoding screening, not mechanical calibration.
                if len(pose) != 5 or any(not 0 <= v <= 4095 for v in pose):
                    raise SafetyError('invalid encoding')
        root = Path(__file__).resolve().parents[1]
        files = sorted(root.glob('c[1-3]r[1-3][mp].json'))
        sequences = {p.stem: load_sequence(p) for p in files}
        paths = list(sequences.values())
        paths += [(sequences[f'c{c}r{r}m'][1], sequences[f'c{c}r{r}p'][0])
                  for c in range(1, 4) for r in range(1, 4)]
        self.assertEqual(len(paths), 27)
        with tempfile.TemporaryDirectory() as temp:
            for i, points in enumerate(paths):
                driver = MockDriver(points[0])
                s = Supervisor(driver, EncodingOnlyPolicy(), RawCadCollision(self.arm), Path(temp)/str(i))
                s.start(points, 0)
                self.assertEqual(s.state, 'FAULT')
                self.assertFalse(driver.commands)
                self.assertIn('unknown raw encoder', s.events[-1]['detail'])

    def test_rack_topology_uncertainty_and_unknowns(self):
        # Every dimension here is synthetic test data, not a measured scene.
        config = dict(origin=(0, 0, 0), orientation=(0, 0, 0), height=.350,
                      pitch=.100, grid_bottom=.025, depth=.12, divider_thickness=.004,
                      tray_thickness=.002, rim_height=.01, rim_thickness=.003, uncertainty=.005)
        boxes = rack_boxes(**config)
        self.assertEqual(sum('_tray_' in name for name in boxes), 45)
        self.assertEqual(sum('frame_vertical' in name for name in boxes), 4)
        self.assertEqual(sum('shelf_horizontal' in name for name in boxes), 6)
        self.assertAlmostEqual(boxes['frame_vertical_0'][0][0], -.007)
        for key in config:
            with self.subTest(missing=key), self.assertRaises(SafetyError):
                rack_boxes(**(config | {key: None}))
