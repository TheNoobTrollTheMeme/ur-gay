"""Bounded, user-selectable STS3215 playback command settings.

These are servo register values, not a guarantee of collision safety.
"""
DEFAULT_SPEED = 300
DEFAULT_ACCELERATION = 20
MIN_SPEED = 50
MAX_SPEED = 1200
MIN_ACCELERATION = 1
MAX_ACCELERATION = 60


def validate_playback_settings(speed, acceleration):
    """Reject unsupported values BEFORE connecting to motors."""
    if type(speed) is not int or not MIN_SPEED <= speed <= MAX_SPEED:
        raise ValueError(f"Playback speed must be an integer {MIN_SPEED}..{MAX_SPEED}.")
    if type(acceleration) is not int or not MIN_ACCELERATION <= acceleration <= MAX_ACCELERATION:
        raise ValueError(
            f"Playback acceleration must be an integer {MIN_ACCELERATION}..{MAX_ACCELERATION}."
        )
    return speed, acceleration


def prompt_playback_settings(*, speed=DEFAULT_SPEED,
                             acceleration=DEFAULT_ACCELERATION, input_fn=input):
    """Choose one speed and acceleration for every waypoint in this playback."""
    speed, acceleration = validate_playback_settings(speed, acceleration)
    raw_speed = input_fn(f"Playback speed {MIN_SPEED}..{MAX_SPEED} [{speed}]: ").strip()
    raw_accel = input_fn(
        f"Playback acceleration {MIN_ACCELERATION}..{MAX_ACCELERATION} [{acceleration}]: "
    ).strip()
    try:
        chosen_speed = int(raw_speed) if raw_speed else speed
        chosen_accel = int(raw_accel) if raw_accel else acceleration
    except ValueError as error:
        raise ValueError("Speed and acceleration must be whole numbers.") from error
    chosen_speed, chosen_accel = validate_playback_settings(chosen_speed, chosen_accel)
    if chosen_speed > 600 or chosen_accel > 35:
        print("WARNING: High-speed motion can dislodge the arm or strike blocks.")
        if input_fn("Type CONFIRM to use the high-speed settings: ").strip() != "CONFIRM":
            raise ValueError("High-speed playback cancelled.")
    return chosen_speed, chosen_accel
