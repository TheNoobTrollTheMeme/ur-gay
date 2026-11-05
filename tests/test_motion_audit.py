import hashlib
from pathlib import Path
import tempfile
import unittest

from simulation_safety.audit_motions import audit
from simulation_safety.supervisor import load_sequence


class MotionAuditTests(unittest.TestCase):
    def test_full_audit_preserves_sources_and_blocks_all_paths(self):
        root = Path(__file__).resolve().parents[1]
        paths = sorted(root.glob('c[1-3]r[1-3][mp].json'))
        before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)/'audit'
            report = audit(root, output)
            self.assertEqual(len(report['motions']), 18)
            self.assertEqual(len(report['cells']), 9)
            for path in paths:
                for folder in ('backups', 'candidates'):
                    copy = output/folder/path.name
                    self.assertEqual(copy.read_bytes(), path.read_bytes())
                    self.assertEqual(len(load_sequence(copy)), 2)
            for item in report['motions']+report['cells']:
                result = item.get('simulation', item.get('transition_simulation'))
                self.assertEqual(result['state'], 'FAULT')
                self.assertEqual(result['commands'], 0)
                self.assertEqual(item['before_total'], item['after_total'])
            first = report['motions'][0]
            self.assertEqual(first['before_joint_travel'], [33, 306, 258, 1, 190])
            cell = report['cells'][0]
            self.assertEqual(cell['m2_to_p1_travel'], [9, 28, 29, 46, 1142])
            self.assertIsNone(report['speed_after'])
            with self.assertRaises(FileExistsError):
                audit(root, output)
        self.assertEqual(before, {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})
