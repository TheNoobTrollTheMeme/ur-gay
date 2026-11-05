"""Pure, offline-only attack route composer.

Attack must use M1 -> M2 -> (P2 joints 1-4, M2 joint 5).
Never import a servo driver here.
"""
JOINTS = ("base_yaw", "shoulder", "elbow", "wrist", "gripper")

def attack_targets(move_points, place_points):
    if len(move_points) != 2 or len(place_points) != 2:
        raise ValueError("exactly two M and two P points required")
    for point in (*move_points, *place_points):
        if set(point) != set(JOINTS):
            raise ValueError("expected all five named servo positions")
        if any(isinstance(point[j], bool) or not isinstance(point[j], (int, float))
               or not 0 <= point[j] <= 4095 or int(point[j]) != point[j]
               for j in JOINTS):
            raise ValueError("invalid raw servo position")
    first = dict(move_points[0])
    closed = dict(move_points[1])
    retract = {j: place_points[1][j] for j in JOINTS[:4]}
    retract["gripper"] = closed["gripper"]  # critical: never open the attack claw
    return (first, closed, retract)
