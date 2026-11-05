"""Offline travel audit. Never generates uncalibrated target/speed changes."""
import hashlib
import json
from pathlib import Path
import tempfile

from simulation_safety.supervisor import MockDriver, SafetyError, Supervisor, load_sequence


class MissingCalibration:
    """Fail closed rather than substitute encoder encoding for mechanical limits."""

    def validate(self, pose):
        raise SafetyError('measured joint limits and monitoring policy unavailable')


def travel(a, b):
    return [int(abs(x-y)) for x, y in zip(a, b)]


def blocked_preflight(points, log):
    # First pose is ONLY a mock initial condition, never an assumed real start.
    driver = MockDriver(points[0])
    supervisor = Supervisor(driver, MissingCalibration(), None, log)
    supervisor.start(points, 0.)
    supervisor.tick(0.)
    if supervisor.state != 'FAULT' or driver.commands:
        raise AssertionError('uncalibrated trajectory was not blocked')
    return dict(state=supervisor.state, commands=len(driver.commands),
                reason=supervisor.events[-1]['detail'], geometry='unavailable')


def audit(source, output):
    paths = sorted(source.glob('c[1-3]r[1-3][mp].json'))
    if len(paths) != 18:
        raise SafetyError('requires all 18 M/P source files')
    # Separate byte-exact input copies; refuse to overwrite an earlier audit.
    output.mkdir(parents=True, exist_ok=False)
    for folder in ('backups', 'candidates'):
        (output/folder).mkdir()
    report = dict(status='BLOCKED_NO_OPTIMISATION_VALIDATED',
                  hardware_ready=False, joint_order=['base_yaw', 'shoulder', 'elbow', 'wrist', 'gripper'],
                  units='absolute raw encoder differences; no wraparound or angle assumption',
                  speed_before=300, acceleration_before=20,
                  speed_after=None, acceleration_after=None,
                  timing='unknown: command units, limits, dynamics and load uncalibrated',
                  motions=[], cells=[])
    points_by_name = {}
    with tempfile.TemporaryDirectory() as temp:
        for path in paths:
            raw = path.read_bytes()
            for folder in ('backups', 'candidates'):
                (output/folder/path.name).write_bytes(raw)
            points = load_sequence(output/'backups'/path.name)
            points_by_name[path.stem] = points
            delta = travel(*points)
            report['motions'].append(dict(
                file=path.name, sha256=hashlib.sha256(raw).hexdigest(),
                candidate='unchanged baseline; blocked, not optimised', recorded_waypoints=2,
                before_joint_travel=delta, after_joint_travel=delta,
                before_total=sum(delta), after_total=sum(delta), reduction=0,
                simulation=blocked_preflight(points, Path(temp)/f'{path.stem}.jsonl')))
        for col in range(1, 4):
            for row in range(1, 4):
                cell = f'c{col}r{row}'
                m, p = points_by_name[cell+'m'], points_by_name[cell+'p']
                bridge = travel(m[1], p[0])
                segments = [travel(*m), bridge, travel(*p)]
                totals = [sum(segment[j] for segment in segments) for j in range(5)]
                reversals = []
                poses = m+p
                for j in range(5):
                    signed = [b[j]-a[j] for a, b in zip(poses, poses[1:]) if b[j] != a[j]]
                    reversals.append(sum(a*b < 0 for a, b in zip(signed, signed[1:])))
                report['cells'].append(dict(cell=cell, m2_to_p1_travel=bridge,
                    before_joint_travel=totals, after_joint_travel=totals,
                    before_total=sum(totals), after_total=sum(totals),
                    direction_reversals=reversals,
                    transition_simulation=blocked_preflight((m[1], p[0]), Path(temp)/f'{cell}-bridge.jsonl')))
    (output/'report.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    lines = ['# M/P offline travel audit', '',
        '**BLOCKED: no speed or target optimisation validated.** Candidates are byte-exact baseline copies, not approved motions.', '',
        'Travel is sum of absolute raw differences, not degrees, Cartesian distance or elapsed time. '
        'Before and after exclude unknown initial approach and inter-cell moves. No encoder wraparound is assumed.', '',
        '| Motion | Joint travel: base, shoulder, elbow, wrist, gripper | Before total | After total |',
        '| --- | --- | ---: | ---: |']
    for motion in report['motions']:
        lines.append(f"| {motion['file']} | {motion['before_joint_travel']} | {motion['before_total']} | {motion['after_total']} |")
    lines += ['', '| Cell M1 → M2 → P1 → P2 | M2 → P1 joint travel | Before total | After total | Direction reversals per joint |',
              '| --- | --- | ---: | ---: | --- |']
    for cell in report['cells']:
        lines.append(f"| {cell['cell']} | {cell['m2_to_p1_travel']} | {cell['before_total']} | {cell['after_total']} | {cell['direction_reversals']} |")
    lines += ['', '## Collision and dynamics assessment', '',
        'All 18 candidates and 9 M2 → P1 transitions fault at simulator preflight with zero commands. '
        'Missing measured limits block validation before collision checking; no collision clearance was computed. '
        'Tray, shelf and frame risks remain UNKNOWN for every path. The existing collision engine requires measured '
        'full-link, wrist, jaw and held-object covers, offsets, displacement bounds, obstacle geometry, clearance and uncertainty.', '',
        'Live source uses speed=300 and acceleration=20, with unconfirmed units. The mock tracks instantaneously; '
        'it cannot estimate speed, acceleration, jerk, braking, torque or time under load. Maximum safe speed is unknown. '
        'No replacement speed or acceleration is supplied.', '',
        'Wrist ticks alone do not determine gripper orientation: shoulder/elbow axes, signs and offsets are uncalibrated. '
        'Jaw opening direction and block dimensions are unknown. Direction reversals identify review priorities, '
        'not proven flailing; removing them could remove required clearance or release motion. '
        'Fixed endpoints already have minimum per-joint total variation under monotonic interpolation; '
        'changing endpoints requires verified task constraints and collision geometry.', '',
        'The motion archive labels C1R1/C2R1 as original and other cells estimated; these labels do not '
        'prove current root recordings were taught. C3R3 is especially uncertain. The preview archive explicitly '
        'uses invented geometry and offsets, which are excluded here.', '',
        '## Validation boundary', '',
        'Exactly two source points preserved per candidate; all backup/candidate bytes and hashes match sources. '
        'No additional recorded waypoints, real drivers, serial connections or motor commands. '
        'No hardware tests run. Measured calibration, geometry, load envelope and supervised baselines are required '
        'before any trajectory or speed improvement can be validated. Inter-cell starts, self-collision, cables '
        'and controller interpolation also remain unresolved. Collision prevention cannot be guaranteed.']
    (output/'REPORT.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    audit(args.source.resolve(), args.output.resolve())
