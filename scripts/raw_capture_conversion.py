"""Lossless-intent conversion of legacy angle waypoints to raw servo ticks.

Pure data conversion only. Never opens the motor port or enables torque.
Requires a decoded raw position for each saved waypoint, provided by the
installed waypoint loader. The editor writes only after explicit confirmation.
"""
from copy import deepcopy


def convert_recording_to_raw(data, decoded, joint_names):
    if data.get("input_type") == "raw":
        raise ValueError("Already raw: conversion not required.")
    if data.get("derived_from"):
        raise ValueError("Derived attack sequence: edit its source motion.")
    originals = data.get("points")
    if not isinstance(originals, list) or len(originals) != len(decoded):
        raise ValueError("Waypoint count mismatch; original recording unchanged.")
    joints = tuple(joint_names)
    if len(set(joints)) != len(joints) or not joints:
        raise ValueError("Invalid joint layout.")
    updated = deepcopy(data)
    converted = []
    for waypoint_number, point in enumerate(decoded, 1):
        if set(point) != set(joints):
            raise ValueError(f"Waypoint {waypoint_number}: missing/extra servo positions.")
        values = [point[j] for j in joints]
        if any(type(value) is not int or not 0 <= value <= 4095
               for value in values):
            raise ValueError(
                f"Waypoint {waypoint_number}: raw servo values must be integers 0..4095.")
        converted.append(values)
    for point, values in zip(updated["points"], converted):
        point.pop("positions_raw", None)
        point["input_values"] = values
    updated["input_type"] = "raw"
    return updated
