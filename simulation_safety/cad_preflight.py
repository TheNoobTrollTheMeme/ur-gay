"""Reproducible offline CAD preflight report; does not dispatch any motion."""
import hashlib
import json
from pathlib import Path

from simulation_safety.cad_geometry import ASSET_SHA256, CadArm, RawCadCollision
from simulation_safety.supervisor import SafetyError, load_sequence


def inspect(source):
    arm = CadArm()
    checker = RawCadCollision(arm)
    paths = sorted(source.glob('c[1-3]r[1-3][mp].json'))
    if len(paths) != 18:
        raise SafetyError('all 18 motion files required')
    sequences = {p.stem: load_sequence(p) for p in paths}
    routes = [(p.stem, sequences[p.stem]) for p in paths]
    routes += [(f'c{c}r{r}_M2_to_P1', (sequences[f'c{c}r{r}m'][1], sequences[f'c{c}r{r}p'][0]))
               for c in range(1, 4) for r in range(1, 4)]
    results = []
    for name, points in routes:
        try:
            checker.check(*points)
        except SafetyError as error:
            results.append(dict(route=name, status='BLOCKED', reason=str(error), clearance='UNKNOWN',
                                gripper_orientation='UNKNOWN', speed_approved=False))
        else:
            raise AssertionError('uncalibrated raw path unexpectedly accepted')
    return dict(package_sha256=ASSET_SHA256, cad_mesh_groups=len(arm.covers),
                mesh_cover_radii_m={name: radius for name, (_, radius) in arm.covers.items()},
                source_sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
                physical_measurements_verified=[], trajectories=results,
                original_waypoints_preserved=True, speed_reduction_or_improvement_percent=None,
                travel_reduction_percent=0, hardware_tests=0,
                note='CAD is verified against upstream source bytes, not measured physical installation. '
                     'Complete rack topology is implemented but physical placement/dimensions and uncertainty remain missing.')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = inspect(args.source)
    # Do not overwrite a previous report silently.
    with args.output.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(report, indent=2)+'\n')
