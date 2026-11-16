"""Named, reusable STS3215 poses for Script 6.

This is a storage/resolution module. It never connects to the arm or sends
motor commands. A linked sequence point is {"bookmark": "safe_left"}.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from raw_playback_validation import validate_raw_pose

NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,47}$")


def normalize_name(name):
    name = str(name).strip().lower()
    if not NAME_PATTERN.fullmatch(name):
        raise ValueError('Bookmark names: 1-48 letters, numbers, _ or -, starting with a letter.')
    return name


def _now():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def _new_store(joints):
    return dict(schema_version=1, type='pose_bookmarks', joint_order=list(joints),
                bookmarks={})


def read_store(path, joint_names):
    """Read and validate the entire stored library before any update."""
    joints = tuple(joint_names)
    path = Path(path)
    if not path.exists():
        return _new_store(joints)
    data = json.loads(path.read_text(encoding='utf-8'))
    if (not isinstance(data, dict) or data.get('schema_version') != 1 or
            data.get('type') != 'pose_bookmarks' or
            data.get('joint_order') != list(joints) or
            not isinstance(data.get('bookmarks'), dict)):
        raise ValueError(f'Invalid bookmark library at {path}. Nothing was changed.')
    for name, entry in data['bookmarks'].items():
        if normalize_name(name) != name or not isinstance(entry, dict):
            raise ValueError(f'Invalid saved bookmark {name!r}')
        validate_raw_pose(entry.get('positions_raw'), joints)
    return data


def list_bookmarks(path, joint_names):
    """Return name -> joint/position mapping, independent of stored JSON objects."""
    store = read_store(path, joint_names)
    return {name: dict(entry['positions_raw'])
            for name, entry in sorted(store['bookmarks'].items())}


def get_bookmark(path, name, joint_names):
    name = normalize_name(name)
    store = read_store(path, joint_names)
    try:
        return dict(store['bookmarks'][name]['positions_raw'])
    except KeyError as error:
        raise ValueError(f'Unknown bookmark @{name}. Save it from Script 6 > 1 > B.') from error


def _atomic_save(path, data):
    """Create timestamped backup and replace through a same-directory temp file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    old_backup = None
    if path.exists():
        old_backup = path.with_name(
            path.name + '.' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.bak')
        shutil.copy2(path, old_backup)
    fd, tmp = tempfile.mkstemp(prefix=f'.{path.name}.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return old_backup


def save_bookmark(path, name, pose, joint_names, *, overwrite=False):
    """Save one five-servo pose; existing bookmarks require explicit overwrite."""
    name = normalize_name(name)
    joints = tuple(joint_names)
    validate_raw_pose(pose, joints)
    data = read_store(path, joints)
    old = data['bookmarks'].get(name)
    if old is not None and not overwrite:
        raise ValueError(f'Bookmark @{name} exists. Confirm replacement first.')
    data['bookmarks'][name] = dict(
        positions_raw={joint: pose[joint] for joint in joints},
        created_at=old.get('created_at', _now()) if old else _now(),
        updated_at=_now())
    return _atomic_save(path, data)



def edit_bookmark_reference(data, name, position=None, *, insert=False):
    """Return a new sequence with a reusable bookmark link.

    No hardware access and no mutation of the input sequence. position is
    1-based. No position appends; +N inserts before N; N replaces point N.
    Explicit N=len+1 also appends for compatibility with Script 6 shortcuts.
    """
    name = normalize_name(name)
    if not isinstance(data, dict) or not isinstance(data.get('points'), list):
        raise ValueError('Expected a pose sequence with a waypoint list.')
    points = list(data['points'])
    if position is None:
        index = len(points)
        action = 'Appended'
    else:
        if type(position) is not int:
            raise ValueError('Waypoint number must be an integer.')
        index = position - 1
        if insert:
            if not 0 <= index <= len(points):
                raise ValueError('Insert position must be 1..number of waypoints + 1.')
            action = 'Inserted'
        else:
            if not 0 <= index <= len(points):
                raise ValueError('Replace position must be an existing waypoint number.')
            action = 'Appended' if index == len(points) else 'Replaced'
    link = {'bookmark': name}
    if insert or index == len(points):
        points.insert(index, link)
    else:
        points[index] = link
    return dict(data, points=points), index + 1, action


def referenced_by(motion_dir, name):
    """Find recordings referring to a bookmark before allowing deletion."""
    name = normalize_name(name)
    matches = []
    for path in sorted(Path(motion_dir).glob('*.json')):
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict) or record.get('type') != 'pose_sequence':
            continue
        points = record.get('points')
        if not isinstance(points, list):
            continue
        if any(isinstance(point, dict) and point.get('bookmark') == name
               for point in points):
            matches.append(path.stem)
    return matches


def delete_bookmark(path, name, joint_names, *, motion_dir=None):
    name = normalize_name(name)
    data = read_store(path, joint_names)
    if name not in data['bookmarks']:
        raise ValueError(f'Unknown bookmark @{name}')
    uses = referenced_by(motion_dir or Path(path).parent, name)
    if uses:
        raise ValueError(f'Cannot delete @{name}: referenced by {", ".join(uses)}')
    del data['bookmarks'][name]
    return _atomic_save(path, data)


def resolve_points(data, parse_point_values, joint_names, library_path,
                   *, min_points=0):
    """Validate/resolve static and linked poses, always returning raw tick dicts."""
    joints = tuple(joint_names)
    if not isinstance(data, dict) or data.get('type') != 'pose_sequence':
        raise ValueError('Not a saved pose sequence.')
    if data.get('schema_version') != 1:
        raise ValueError('Unsupported pose sequence version.')
    input_type = data.get('input_type')
    if input_type not in ('angles', 'raw'):
        raise ValueError('Expected sequence input_type raw or angles.')
    points = data.get('points')
    if not isinstance(points, list) or len(points) < min_points:
        raise ValueError(f'Sequence needs at least {min_points} waypoints.')
    library = None
    resolved = []
    for index, point in enumerate(points, 1):
        if not isinstance(point, dict):
            raise ValueError(f'Waypoint {index} must be an object.')
        if 'bookmark' in point:
            if set(point) != {'bookmark'}:
                raise ValueError(f'Waypoint {index}: bookmark may not mix with other fields.')
            name = normalize_name(point['bookmark'])
            if name != point['bookmark']:
                raise ValueError(f'Waypoint {index}: use normalized bookmark name @{name}.')
            if library is None:
                library = list_bookmarks(library_path, joints)
            try:
                pose = library[name]
            except KeyError as error:
                raise ValueError(f'Waypoint {index}: missing bookmark @{name}.') from error
        elif 'input_values' in point:
            try:
                pose = parse_point_values(point['input_values'], input_type)
            except (ValueError, TypeError, KeyError) as error:
                raise ValueError(f'Waypoint {index}: {error}') from error
        elif 'positions_raw' in point:
            pose = point['positions_raw']
        else:
            raise ValueError(f'Waypoint {index}: requires input_values or bookmark.')
        try:
            validate_raw_pose(pose, joints)
        except ValueError as error:
            raise ValueError(f'Waypoint {index}: {error}') from error
        resolved.append(dict(pose))
    return resolved


def load_with_bookmarks(path, base_loader, parse_point_values, joint_names,
                        library_path):
    """Preserve Kelvin's original loader for legacy files without references."""
    path = Path(path)
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or not isinstance(data.get('points'), list):
        return base_loader(path)
    if not any(isinstance(point, dict) and 'bookmark' in point
               for point in data['points']):
        return base_loader(path)
    return data, resolve_points(data, parse_point_values, joint_names,
                                library_path, min_points=2)


def save_linked_sequence(path, data, parse_point_values, joint_names,
                         library_path):
    """Save an edited route with live bookmark references, after full preflight."""
    joints = tuple(joint_names)
    if data.get('joint_order') is None or len(data['joint_order']) != len(joints):
        raise ValueError('Sequence joint order is incomplete.')
    resolve_points(data, parse_point_values, joints, library_path)
    if not any(isinstance(p, dict) and 'bookmark' in p for p in data['points']):
        raise ValueError('Expected at least one bookmark reference.')
    return _atomic_save(path, data)
