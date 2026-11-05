# ENG1101 robot physical context (read before planning motions)

This is the persistent context for Codex. This file is user observations and measured dimensions, **not** proof of safe motion.

## Source of truth
- User's live motion files are the M/P JSON sequences in this repository. The development branch is `codex-pi-safe-setup`.
- Robot is an STS3215 5DOF arm with Waveshare ESP32 serial servo controller.
- Teaching source: https://github.com/Kelvinchan324/STS3215-5DOF-Robot-Arm
- Actual Fusion 360-derived CAD: https://github.com/Kelvinchan324/robot-arm-simulation (URDF, MJCF, OBJ assets under `dist/robot`).
- Reviewed CAD package is pinned at `simulation_safety/assets/engg1101-robot-package.zip`; source controls in `simulation_safety/cad_geometry.py`.
- This project's preliminary geometry/config is at `simulation_safety/assets/preliminary_scene.json`.
- A **visual** viewer with upstream OBJ parts and the provisional 3x3 rack is at `visualization/engg1101_scene.html`. This is not a collision certifier.

## User measurements
- 287 mm: from **Servo ID 2 shoulder-pitch rotation axis** (the first up/down motor, not ID 1 yaw) to nearest **front edge** of wooden rack. Distance's direction/projection and rack yaw relative to robot are not independently calibrated.
- Rack depth 80 mm.
- Rack vertical 90 degrees to ground, which says nothing about yaw around the vertical.
- Blue tray exterior height 20 mm; interior height 15 mm; the 5 mm difference may describe the floor thickness only if measured from compatible references.
- Photo estimates: gripper overall width ~130 mm, depth ~70–80 mm, rack height ~350 mm, column pitch ~100 mm; not precision measurements.
- Missing: actual rack yaw and offset; divider and rim thickness; grid bottom offset; precise gripper and block envelopes; physical raw-encoder → CAD map and offsets; motion dynamics/safe deceleration.

## Motion context
- Preserve exactly TWO user-recorded waypoints per individual M/P sequence. Path interpolation for analysis may have many samples.
- Existing placement execution may include M1 → M2 → P1 → P2; validate the M2 → P1 bridge separately. The derived attack is M1 → M2 → P2, and inter-cell transitions and withdrawal also matter.
- User reports gripper grabbing tray rim, colliding with wooden frame, and visibly flailing under commanded motion; gravity/overshoot suspected but not verified.
- Critical user concern: **C1R3 and C3R1 withdrawal/transition** may draw unsafe diagonal movement through the frame or rack. Analyse the complete swept geometry, not only endpoints.
- Keep two recorded waypoints if possible, but REJECT motion if two are insufficient for safe withdrawal; do not automatically home/retract a jam.
- Priority: no collision, safe fault response, then execution time. Don't unilaterally increase speed/acceleration.

## Safe development boundary
- Physical arm currently may be used by other students; work Windows-side and offline unless the user explicitly authorises a supervised hardware test.
- Simulator CAD q=0 is exported Fusion pose; **raw encoder ticks are not automatically CAD q**. Never assume 2048 = exact physical zero or simply 4096 ticks = one CAD revolution with correct signs/offsets without validation.
- Visual scenes with assumed rack facing, pitch, thickness or grid zero are conceptual only, not clearance/speed approval.
- Do not execute motor scripts, change hardware calibration, automatically push/deploy live code or release torque as a blind emergency measure. Validate safe-hold strategy first.
- Reports should distinguish real measured numbers, user photo estimates, software source values and arbitrary scene defaults.
