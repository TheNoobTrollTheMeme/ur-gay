# STS3215 square-grid motion code (offline candidates)

This branch contains **only the newly assembled grid-motion package**, not the existing `ur-gay` project files. It was published to a separate branch because repository creation is not available through the connected GitHub integration.

## Grid orientation (viewed from the robot side)

```
C1R1  C2R1  C3R1
C1R2  C2R2  C3R2
C1R3  C2R3  C3R3
```

## Files
- `motions/`: 18 two-waypoint servo JSONs (move `m` and place `p` for every square).
- `calibration_status.json`: identifies recorded vs estimated movements; **none are approved** for live execution.
- `tools/inspect.py`: standard-library-only offline validator and pose printer. Does **not** operate hardware.

**Hardware status:** Only C1R1/C2R1 (4 files) contain recorded values from the existing repository, and even those require current-hardware verification. The remaining 14 use assumptions/extrapolation. C3R3 in particular is predicted unreachable in one assumed arm model. THESE FILES ARE NOT COLLISION-CHECKED OR APPROVED FOR AUTOMATIC MOTOR CONTROL.

The two points do not prove a collision-free transition. Never overwrite the robot's working motion files blindly.

## Raspberry Pi: clone only these new grid files

```bash
cd ~
git clone --single-branch --depth 1 --branch sts3215-grid-controller https://github.com/TheNoobTrollTheMeme/ur-gay.git sts3215-grid-code
cd ~/sts3215-grid-code
python3 tools/inspect.py
python3 tools/inspect.py c1r1m
```

This downloads a **clean branch snapshot** into `~/sts3215-grid-code`, separate from `~/robot-arm-lab/STS3215-5DOF-Robot-Arm`. It does not activate a venv, install dependencies, contact servos, or change your current motions.

For updates:
```bash
cd ~/sts3215-grid-code && git pull --ff-only
```
