# Safety status — simulation only

Report date: 2026-11-05. **Not ready for hardware.** No serial port was opened,
no real driver was imported, and no physical servo commands or hardware tests
were executed. No deployment or calibration changes were made.

Development initially took place in the separate `safety_workcopy/` directory.
Its actual implementation is now organised as `simulation_safety/supervisor.py`
and `tests/test_supervisor.py`, with a package initializer. The original live
script and M/P motion files remain untouched. Inspection found duplicate source
snapshots and generated Python bytecode, but no nested Git repository, virtual
environment or secret files in that work copy. Those duplicate/temporary
artifacts remain local under the ignored `safety_workcopy/`; they are excluded
from the commit. Root ignore rules also exclude caches, environments, local
credentials, generated logs and temporary files.

## Existing M/P behaviour

`scripts/06_live_grid_tool.py` is the available live execution entry point.
`arm_settings.py`, `arm_helpers.py`, `terminal_keys.py` and
`scripts/05_waypoint_sequence.py` are referenced but absent. They were not
imported. Setup scripts were read, never run.

Each `c#r#m.json` is a move sequence and its paired `c#r#p.json` a place
sequence. All 18 root grid files contain exactly two poses, in this order:

| Servo ID | Joint |
| --- | --- |
| 1 | base_yaw |
| 2 | shoulder |
| 3 | elbow |
| 4 | wrist |
| 5 | gripper |

Schema version is 1, type `pose_sequence`, input type `raw`; the five
`input_values` are raw encoder values stored as strings. The archive describes
raw values as encoder ticks, with a numeric encoding range of 0..4095. This is
not evidence of safe mechanical limits. The new loader accepts finite integer
raw values only and requires explicit simulation limits before dispatch.

Placement runs **M1 → M2 → P1 → P2**. The preview describes M as clearance
to cell and P as release/withdrawal, but physical intent, clearance and jaw
opening direction cannot be verified from raw numbers alone. For example,
`c1r1m` contains `[2257,2196,2020,1796,2601]` then
`[2224,1890,2278,1797,2791]`; `c1r1p` contains
`[2233,1918,2249,1843,1649]` then `[2243,2355,1652,2043,1647]`.

The live tool discovers pairs under `motions/`, although the supplied files
are at the repository root. It reloads saved targets while executing. The
separate derived attack file contains **M1 → M2 → P2**, skipping P1; this
three-target derived route is distinct from each two-pose source sequence.
It is also distinct from the archive's relative wrist-nod attack gesture.
The simulation supervisor accepts one two-waypoint source sequence at a time;
it does not execute derived attacks or random batches.

The live editor permits appending arbitrary points and does not enforce the
two-point invariant. Playback shuffles selected cells, offers placement,
attack or mixed mode, and asks for Enter before each cell. It reads the pose,
seeds that pose as a command, enables torque and sends targets with speed=300,
acceleration=20 (units unconfirmed). `wait_move` polls `ReadMoving` for all
servos with a 15-second deadline. This does not establish actual arrival,
position tracking, jam detection or collision clearance. Successful completion
leaves torque enabled; interruption/error calls `disable_all_torque`.
That torque-release path is not a verified safe hold for a raised load.
**The new supervisor is not wired into or protecting this live runner.**

The motions archive labels only C1R1 and C2R1 M/P as original recordings;
the other 14 grid files are estimated/unverified and C3R3 is especially
uncertain. These provenance labels apply to archive contents; they do not
prove the current root files were physically taught or tested. The simulation
archive explicitly uses invented geometry, offsets and standby for artwork.
Those values were not adopted as calibration.

## Implemented offline behaviour

`simulation_safety/supervisor.py` uses only the Python standard library.

- Only the exact `MockDriver` type is accepted. Hardware defaults disabled;
  even an explicit enable request fails because no hardware adapter exists.
- Strict two-waypoint loading and preflight validation reject malformed,
  nonfinite and out-of-range commands. No source recordings are rewritten.
  Interpolation is internal collision validation, never added recorded poses.
- States are IDLE, RUNNING, COMPLETE and FAULT. Sustained tracking error is a
  simulated jam indicator. Stale/invalid timestamps, missing devices,
  invalid feedback, backward clocks, movement deadlines and unexpected
  exceptions latch FAULT and prevent further target dispatch.
- Stops clear pending targets and cancel the active mock target with
  `stop_hold`, freezing simulated position. No torque-off, auto-home,
  auto-retract or automatic restart occurs. Failed holds are recorded as
  operator alerts. This is a mock contract, not an STS3215 guarantee.
- Reset requires explicit operator acknowledgement, fresh in-range feedback,
  valid collision configuration and a successful log write. Reset returns to
  IDLE; a separate start is required. A reset does not diagnose a physical jam.
- JSONL flight records include UTC time, simulation time, state, target,
  latest feedback, commands, arrivals and faults. Every active tick records
  feedback. Fault alerts are available in `events`; file logging failure still
  stops/latches and sets `log_failed`. No external notification service or
  durable crash-proof recorder is implemented.

Tracking/error limits and timing values have no production defaults. The tests
explicitly supply synthetic thresholds; they are **UNCALIBRATED** and not
recommended real servo settings. Current, load and temperature monitoring are
not implemented because available source does not confirm their interfaces,
units or capabilities. No unsupported telemetry is treated as a zero reading.

## Collision checks and their limits

The collision interface requires explicit body sphere covers, kinematics,
per-joint displacement bounds, obstacle boxes, clearance and uncertainty.
Tests include base, upper arm, forearm, wrist and both jaws, checked against
synthetic tray/frame/shelf obstacles. Each caller must supply complete covers
for all physical parts; names alone cannot establish coverage.

Checks include current pose → first target and first → second target, with
fresh-pose rechecking before every dispatch. Sampled joint interpolation is
inflated by a half-sample displacement bound, covering motion between samples
**if** the supplied bounds and sphere covers are valid throughout the domain.
This can reject feasible paths conservatively. Missing geometry blocks motion.
No measured project collision model is available, so no supplied robot M/P
path has been declared collision-free or feasible.

Synthetic tests prove algorithm behaviour only. Actual dimensions, jaw travel,
held-object envelope, link covers, joint mapping, zero offsets, scene geometry,
uncertainty and displacement bounds still require measurement. Self-collision,
moving people/obstacles, flexible cables, dynamics, braking distance and
controller interpolation differences are not modelled. The algorithm assumes
a static scene and linear joint interpolation. Collision or damage prevention
cannot be guaranteed.

## Test report

Run from repository root, without running any live script:

```powershell
python -m unittest discover -s tests -v
```

Final result after reorganisation: **22 tests passed** in 0.273 seconds on
Windows with Python's standard-library
unittest runner. Tests cover normal completion and exactly two commands;
sustained jam and transient error recovery; acknowledgement/reset with no
automatic restart; stale feedback before and during motion; missing devices;
out-of-range, NaN and malformed commands; incorrect waypoint counts;
timeout including late arrival; operator stop/queue cancellation; failed hold;
failed logging; intermediate collisions and between-sample coverage inflation;
all six synthetic bodies; unknown geometry; backward clock; invalid policy;
hardware gate rejection; and two-waypoint loader/source preservation.
All 18 root grid M/P files are loaded read-only with exactly two waypoints;
tests hash them before and after loading to verify no writes. Git baseline
comparison separately verifies the original motion files and live script are
unchanged. Test logs use temporary directories.

Tests import only the new simulator, not the source snapshot, live runner or
servo modules. Hardware tests: **none**. Thermal/current/load threshold tests:
not applicable until confirmed telemetry support and calibrated limits exist.

## Still required before any hardware integration

Recover and review the actual settings/helpers/vendor protocol documentation.
Confirm safe ranges, zeros, speed units, feedback and error semantics. Implement
documented bounded-I/O stop/hold behaviour and verify queued-command abortion.
The current mock responds synchronously and does not exercise serial timeouts,
real actuator lag, background watchdogs or process death. The caller must tick
the offline supervisor; stalled scheduling is not independently detected.

Measure the arm and environment and validate full swept volumes, including
transitions between M and P or between cells, held blocks and self-collisions.
No alternate waypoint is silently inserted when a two-point route is blocked.
Collect normal position-error and available telemetry baselines only in an
explicitly approved supervised low-speed physical test; choose thresholds
using those baselines and vendor documentation, then validate experimentally.
Excessively sensitive trips can introduce hazards too.

Each real movement test needs separate explicit operator approval and an
operator beside the arm with access to cutoff/E-stop. Check raised-load drop
hazards before relying on power removal. Disconnect power before inspecting
for cracks, bent jaws, binding or loose fasteners. Physical inspection and
supervised validation remain necessary; no unsupervised movement is authorized.
