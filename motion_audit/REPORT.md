# M/P offline travel audit

**BLOCKED: no speed or target optimisation validated.** Candidates are byte-exact baseline copies, not approved motions.

Travel is sum of absolute raw differences, not degrees, Cartesian distance or elapsed time. Before and after exclude unknown initial approach and inter-cell moves. No encoder wraparound is assumed.

| Motion | Joint travel: base, shoulder, elbow, wrist, gripper | Before total | After total |
| --- | --- | ---: | ---: |
| c1r1m.json | [33, 306, 258, 1, 190] | 788 | 788 |
| c1r1p.json | [10, 437, 597, 200, 2] | 1246 | 1246 |
| c1r2m.json | [30, 300, 221, 244, 190] | 985 | 985 |
| c1r2p.json | [44, 306, 222, 408, 2] | 982 | 982 |
| c1r3m.json | [30, 348, 362, 274, 190] | 1204 | 1204 |
| c1r3p.json | [44, 317, 309, 501, 2] | 1173 | 1173 |
| c2r1m.json | [0, 369, 364, 1, 1] | 735 | 735 |
| c2r1p.json | [1, 500, 463, 129, 2] | 1095 | 1095 |
| c2r2m.json | [16, 324, 263, 174, 1] | 778 | 778 |
| c2r2p.json | [24, 319, 230, 396, 2] | 971 | 971 |
| c2r3m.json | [16, 360, 398, 194, 1] | 969 | 969 |
| c2r3p.json | [24, 322, 317, 476, 2] | 1141 | 1141 |
| c3r1m.json | [52, 370, 385, 75, 1] | 883 | 883 |
| c3r1p.json | [76, 380, 247, 509, 2] | 1214 | 1214 |
| c3r2m.json | [52, 353, 349, 152, 1] | 907 | 907 |
| c3r2p.json | [76, 350, 268, 449, 2] | 1145 | 1145 |
| c3r3m.json | [52, 389, 484, 172, 1] | 1098 | 1098 |
| c3r3p.json | [76, 353, 355, 529, 2] | 1315 | 1315 |

| Cell M1 → M2 → P1 → P2 | M2 → P1 joint travel | Before total | After total | Direction reversals per joint |
| --- | --- | ---: | ---: | --- |
| c1r1 | [9, 28, 29, 46, 1142] | 3288 | 3288 | [1, 1, 1, 0, 1] |
| c1r2 | [0, 21, 83, 135, 1142] | 3348 | 3348 | [1, 1, 1, 1, 1] |
| c1r3 | [0, 9, 49, 91, 1142] | 3668 | 3668 | [1, 1, 1, 1, 1] |
| c2r1 | [1, 3, 16, 1, 837] | 2688 | 2688 | [0, 1, 1, 1, 2] |
| c2r2 | [0, 19, 107, 206, 837] | 2918 | 2918 | [1, 1, 1, 1, 2] |
| c2r3 | [0, 5, 64, 144, 837] | 3160 | 3160 | [1, 1, 1, 1, 2] |
| c3r1 | [0, 3, 21, 63, 837] | 3021 | 3021 | [1, 1, 1, 1, 2] |
| c3r2 | [0, 4, 56, 133, 837] | 3082 | 3082 | [1, 1, 1, 1, 2] |
| c3r3 | [0, 10, 13, 71, 837] | 3344 | 3344 | [1, 1, 1, 1, 2] |

## Collision and dynamics assessment

All 18 candidates and 9 M2 → P1 transitions fault at simulator preflight with zero commands. Missing measured limits block validation before collision checking; no collision clearance was computed. Tray, shelf and frame risks remain UNKNOWN for every path. The existing collision engine requires measured full-link, wrist, jaw and held-object covers, offsets, displacement bounds, obstacle geometry, clearance and uncertainty.

Live source uses speed=300 and acceleration=20, with unconfirmed units. The mock tracks instantaneously; it cannot estimate speed, acceleration, jerk, braking, torque or time under load. Maximum safe speed is unknown. No replacement speed or acceleration is supplied.

Wrist ticks alone do not determine gripper orientation: shoulder/elbow axes, signs and offsets are uncalibrated. Jaw opening direction and block dimensions are unknown. Direction reversals identify review priorities, not proven flailing; removing them could remove required clearance or release motion. Fixed endpoints already have minimum per-joint total variation under monotonic interpolation; changing endpoints requires verified task constraints and collision geometry.

The motion archive labels C1R1/C2R1 as original and other cells estimated; these labels do not prove current root recordings were taught. C3R3 is especially uncertain. The preview archive explicitly uses invented geometry and offsets, which are excluded here.

## Validation boundary

Exactly two source points preserved per candidate; all backup/candidate bytes and hashes match sources. No additional recorded waypoints, real drivers, serial connections or motor commands. No hardware tests run. Measured calibration, geometry, load envelope and supervised baselines are required before any trajectory or speed improvement can be validated. Inter-cell starts, self-collision, cables and controller interpolation also remain unresolved. Collision prevention cannot be guaranteed.
