"""Read-only CAD kinematics and conservative mesh covers. No servo imports.

All lengths are metres, joint coordinates CAD radians. Visual CAD meshes are
covered conservatively, not asserted to match the physical robot or payload.
"""
import hashlib
import math
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

from simulation_safety.supervisor import CollisionModel, SafetyError, finite

CONTROLS = ('base_yaw', 'shoulder', 'elbow', 'wrist_pitch', 'gripper_drive')
ASSET = Path(__file__).with_name('assets')/'engg1101-robot-package.zip'
ASSET_SHA256 = 'b062da08782fc129033e8ca7a4d8ac500405276bc97443047f882143fc7795d5'


def vec(text):
    result = tuple(map(float, text.split()))
    if len(result) != 3 or not all(finite(x) for x in result):
        raise SafetyError('invalid CAD vector')
    return result


def add(a, b):
    return tuple(x+y for x, y in zip(a, b))


def norm(a):
    return math.sqrt(sum(x*x for x in a))


def matmul(a, b):
    return tuple(tuple(sum(a[i][k]*b[k][j] for k in range(3)) for j in range(3)) for i in range(3))


def apply(r, v):
    return tuple(sum(row[j]*v[j] for j in range(3)) for row in r)


IDENTITY = ((1., 0., 0.), (0., 1., 0.), (0., 0., 1.))


def rotation(axis, angle):
    length = norm(axis)
    if not length or not finite(angle):
        raise SafetyError('invalid rotation')
    x, y, z = (a/length for a in axis)
    c, s, t = math.cos(angle), math.sin(angle), 1-math.cos(angle)
    return ((t*x*x+c, t*x*y-s*z, t*x*z+s*y),
            (t*x*y+s*z, t*y*y+c, t*y*z-s*x),
            (t*x*z-s*y, t*y*z+s*x, t*z*z+c))


def rpy(angles):
    x, y, z = angles
    return matmul(rotation((0, 0, 1), z), matmul(rotation((0, 1, 0), y), rotation((1, 0, 0), x)))


class CadArm:
    def __init__(self, package=ASSET, *, payload_covers=None):
        if hashlib.sha256(Path(package).read_bytes()).hexdigest() != ASSET_SHA256:
            raise SafetyError('CAD package hash does not match reviewed upstream snapshot')
        self.covers, self.joints = {}, {}
        with zipfile.ZipFile(package) as archive:
            root = ET.fromstring(archive.read('engg1101.urdf'))
            for link in root.findall('link'):
                visual = link.find('visual')
                if visual is None:
                    if link.attrib['name'] != 'tool_tip':
                        raise SafetyError('uncovered CAD link')
                    continue
                # Exact known upstream subset; reject silently ignored transforms.
                mesh = visual.find('geometry/mesh')
                if mesh is None or visual.find('origin') is not None or 'scale' in mesh.attrib:
                    raise SafetyError('unsupported CAD visual transform')
                vertices = [vec(line[2:]) for line in archive.read(mesh.attrib['filename']).decode().splitlines()
                            if line.startswith('v ')]
                if not vertices:
                    raise SafetyError('empty mesh')
                lo = tuple(min(v[j] for v in vertices) for j in range(3))
                hi = tuple(max(v[j] for v in vertices) for j in range(3))
                center = tuple((a+b)/2 for a, b in zip(lo, hi))
                # Encloses every vertex and triangle, with numerical padding.
                radius = max(norm(tuple(x-y for x, y in zip(v, center))) for v in vertices)+1e-9
                self.covers[link.attrib['name']] = (center, radius)
            for joint in root.findall('joint'):
                origin, axis, limit = joint.find('origin'), joint.find('axis'), joint.find('limit')
                mimic = joint.find('mimic')
                self.joints[joint.attrib['name']] = dict(
                    kind=joint.attrib['type'], parent=joint.find('parent').attrib['link'],
                    child=joint.find('child').attrib['link'],
                    pos=vec(origin.attrib.get('xyz', '0 0 0')),
                    rot=rpy(vec(origin.attrib.get('rpy', '0 0 0'))),
                    axis=vec(axis.attrib['xyz']) if axis is not None else (1., 0., 0.),
                    limits=(float(limit.attrib['lower']), float(limit.attrib['upper'])) if limit is not None else None,
                    mimic=(mimic.attrib['joint'], float(mimic.attrib.get('multiplier', 1)),
                           float(mimic.attrib.get('offset', 0))) if mimic is not None else None)
        if set(self.covers) != {'base', 'base_gear', 'yaw_platform', 'upper_arm', 'forearm',
                                'wrist_carrier', 'gripper_pinion', 'jaw_a', 'jaw_b'}:
            raise SafetyError('unexpected CAD coverage')
        self.attachments = {}
        # Optional measured block covers in the wrist_carrier local frame.
        # No default block dimensions, grasp pose or absence-of-load assumption.
        if payload_covers is not None:
            if not payload_covers:
                raise SafetyError('payload covers must not be empty')
            for label, (center, radius) in payload_covers.items():
                if (len(center) != 3 or not all(finite(v) for v in center)
                        or not finite(radius) or radius <= 0):
                    raise SafetyError('invalid carried-block sphere cover')
                name = 'carried_block:'+label
                self.covers[name] = (tuple(center), radius)
                self.attachments[name] = 'wrist_carrier'
        # A deliberately loose global lever-arm bound across all CAD limits.
        self.reach = sum(norm(j['pos']) for j in self.joints.values())
        self.reach += sum(max(abs(x) for x in j['limits'])*norm(j['axis']) for j in self.joints.values()
                          if j['kind'] == 'prismatic')
        self.reach += max(norm(c)+r for c, r in self.covers.values())
        self.bounds = self._bounds()

    def dependency(self, name):
        j = self.joints[name]
        if j['kind'] == 'fixed':
            return None, 0.
        if j['mimic']:
            master, factor, _ = j['mimic']
            control, gain = self.dependency(master)
            return control, gain*factor
        return CONTROLS.index(name), 1.

    def _bounds(self):
        bounds = {'base': (0.,)*5}
        pending = dict(self.joints)
        while pending:
            progress = False
            for name, j in list(pending.items()):
                if j['parent'] not in bounds:
                    continue
                b = list(bounds[j['parent']])
                control, gain = self.dependency(name)
                if control is not None:
                    b[control] += abs(gain)*(norm(j['axis']) if j['kind'] == 'prismatic' else self.reach)
                bounds[j['child']] = tuple(b)
                del pending[name]
                progress = True
            if not progress:
                raise SafetyError('disconnected CAD tree')
        return {name: bounds[self.attachments.get(name, name)] for name in self.covers}

    def transforms(self, q):
        if len(q) != 5 or not all(finite(v) for v in q):
            raise SafetyError('five finite CAD coordinates required')
        values = dict(zip(CONTROLS, q))
        def value(name):
            j = self.joints[name]
            if j['kind'] == 'fixed':
                return 0.
            if j['mimic']:
                master, gain, offset = j['mimic']
                return value(master)*gain+offset
            return values[name]
        transforms = {'base': (IDENTITY, (0., 0., 0.))}
        pending = dict(self.joints)
        while pending:
            progress = False
            for name, j in list(pending.items()):
                if j['parent'] not in transforms:
                    continue
                v = value(name)
                if j['limits'] and j['kind'] not in ('continuous', 'fixed'):
                    if not j['limits'][0]-1e-10 <= v <= j['limits'][1]+1e-10:
                        raise SafetyError('CAD joint range exceeded: '+name)
                parent_r, parent_p = transforms[j['parent']]
                orient = matmul(parent_r, j['rot'])
                pos = add(parent_p, apply(parent_r, j['pos']))
                if j['kind'] == 'prismatic':
                    pos = add(pos, apply(orient, tuple(x*v for x in j['axis'])))
                elif j['kind'] in ('revolute', 'continuous'):
                    orient = matmul(orient, rotation(j['axis'], v))
                elif j['kind'] != 'fixed':
                    raise SafetyError('unsupported CAD joint')
                transforms[j['child']] = orient, pos
                del pending[name]
                progress = True
            if not progress:
                raise SafetyError('disconnected CAD tree')
        return transforms

    def geometry(self, q):
        transforms = self.transforms(q)
        result = {}
        for name, (center, radius) in self.covers.items():
            rotation, position = transforms[self.attachments.get(name, name)]
            result[name] = (add(position, apply(rotation, center)), radius)
        return result


class RawCadCollision:
    """Supervisor adapter. Unknown mapping/scene always rejects raw paths.

    A caller may supply explicit affine raw->CAD gains/offsets and a complete
    measured scene; no upstream 'confirmed' flag is adopted as local evidence.
    """
    def __init__(self, arm, scene=None, mapping=None):
        self.arm, self.scene, self.mapping = arm, scene, mapping

    def check(self, start, end):
        if self.mapping is None:
            raise SafetyError('unknown raw encoder to CAD calibration')
        raise SafetyError('measured scene, payload and physical coverage approval unavailable')


def cad_collision_model(arm, obstacles, *, clearance, uncertainty, samples):
    """CAD-coordinate diagnostic only; not a raw-encoder safety approval."""
    return CollisionModel(arm.geometry, arm.bounds, obstacles, clearance, uncertainty, samples)
