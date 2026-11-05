import unittest
from simulation_safety.attack_policy import attack_targets, JOINTS

class AttackPolicyTests(unittest.TestCase):
    def test_keeps_servo_five_closed_when_p2_would_open(self):
        m=[dict(zip(JOINTS,(2000,2100,2200,2300,2800))),
           dict(zip(JOINTS,(2010,2110,2210,2310,2900)))]
        p=[dict(zip(JOINTS,(2400,2500,2600,2700,1500))),
           dict(zip(JOINTS,(2410,2510,2610,2710,1400)))]
        first, second, retreat = attack_targets(m,p)
        self.assertEqual((first,second), tuple(m))
        self.assertEqual(tuple(retreat[j] for j in JOINTS), (2410,2510,2610,2710,2900))
        self.assertEqual(m[1]['gripper'], 2900)
        self.assertEqual(p[1]['gripper'], 1400) # must NEVER be sent for attack

    def test_preserves_source(self):
        m=[dict(zip(JOINTS,(2000,)*5)) for _ in range(2)]
        p=[dict(zip(JOINTS,(2100,)*5)) for _ in range(2)]
        attack_targets(m,p)
        self.assertEqual(len(m),2)
        self.assertEqual(len(p),2)
        self.assertEqual(m[1]['gripper'],2000)

    def test_invalid_sources_rejected(self):
        point=dict(zip(JOINTS,(2000,)*5))
        with self.assertRaises(ValueError):
            attack_targets([point], [point,point])
        with self.assertRaises(ValueError):
            attack_targets([point,point],[{**point,'gripper':-1},point])

if __name__ == '__main__':
    unittest.main()
