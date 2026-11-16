"""Check the shape and register range of recorded raw STS3215 positions.

This is a data-integrity validator, NOT a physical collision or joint-travel
safety check. It deliberately does not interpret raw ticks as joint angles.
"""


def validate_raw_pose(positions, joint_names):
    """Require exactly one in-range integer raw tick for every expected joint."""
    joints = tuple(joint_names)
    if not joints or len(set(joints)) != len(joints):
        raise ValueError("Invalid configured joint list.")
    if not isinstance(positions, dict):
        raise ValueError("Recorded waypoint must be a joint-position mapping.")
    missing = set(joints) - set(positions)
    extra = set(positions) - set(joints)
    if missing or extra:
        raise ValueError(
            f"Waypoint joint mismatch: missing={sorted(missing)}, extra={sorted(extra)}"
        )
    for joint in joints:
        value = positions[joint]
        if type(value) is not int or not 0 <= value <= 4095:
            raise ValueError(
                f"{joint}: invalid raw servo tick {value!r}; must be 0..4095"
            )
