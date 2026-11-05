# CAD geometry integration — simulation only

No faster trajectory has been validated. All 18 M/P sequences and nine M2-to-P1
transitions remain **BLOCKED / UNKNOWN CLEARANCE**. No real servo driver was
imported, serial port opened, calibration changed or hardware deployed.

## Source evidence and what is verified

The user-specified [Fusion-derived simulation repository](https://github.com/Kelvinchan324/robot-arm-simulation/tree/f4fff721c57a65ee40b7ffd3269b629b9e97310b)
was downloaded read-only at commit `f4fff721c57a65ee40b7ffd3269b629b9e97310b`.
Its metre-scale OBJ meshes, URDF, MJCF, metadata and README are retained in
`simulation_safety/assets/engg1101-robot-package.zip`. All 14 archive members
match the upstream working files byte-for-byte. Package SHA-256:
`b062da08782fc129033e8ca7a4d8ac500405276bc97443047f882143fc7795d5`.
The loader rejects a changed package hash.

The [servo project](https://github.com/Kelvinchan324/STS3215-5DOF-Robot-Arm/tree/e0ba3c0edc5da2117bb84653c5d4c1ea1d8d6394)
was inspected as text at commit `e0ba3c0edc5da2117bb84653c5d4c1ea1d8d6394`.
Evidence snapshots under `simulation_safety/assets/source_evidence` have `.txt`
extensions to prevent ordinary Python imports. They are evidence only.

| Quantity | Evidence | Status for this robot |
| --- | --- | --- |
| Nine CAD rigid groups; five controls; eight moving joints | URDF/MJCF and meshes | Verified source model, physical match unmeasured |
| Base, gear, yaw platform, upper arm, forearm, wrist/housing, pinion, two fingers | All nine OBJ groups included | CAD coverage only; accessories/cables/payload unmeasured |
| Base gear −1× yaw; jaw A −0.0125 m/rad of pinion; jaw B follows jaw A on opposite axis | URDF mimic / MJCF equality constraints | Implemented as source kinematics |
| Joint axes, origins, CAD range and tool orientation | URDF | Implemented, not encoder calibration |
| Raw IDs 1–5 and 4096 encoder ticks/revolution | Servo settings | Source-confirmed encoding; transmission/physical limits still require verification |
| 2048 zeros, signs and approximate software angle ranges | Servo settings explicitly call zeros defaults to replace | Not adopted as measured calibration |
| Simulation display offsets and gripper opening scale | `dist/joint-angles.js` | Teaching coordinates differ from CAD coordinates |
| Simulation Pi mapping | Config assumes source angles match display coordinates | Assumption; its `CALIBRATION_CONFIRMED=True` is not local measurement evidence |
| Local speed 300, acceleration 20 | Existing local runner | Units/dynamics unvalidated |
| Upstream speed 3000, acceleration 254; simulation config acceleration 50 | Upstream source settings | Configuration values, not validated safe maxima; not copied into live code |
| SDK speed, position and moving-status reads | SDK/helpers | Interfaces confirmed; physical units/response timing unverified |
| MJCF inertia, effort/velocity values | Upstream README and XML comment | Placeholders; contacts disabled, no validated dynamics |

**Verified physical measurements: none supplied or collected.** Source-file
verification and agreement with upstream numerical fixtures do not establish
the physical installation's calibration or collision clearance.

## Collision implementation

`CadArm` parses the pinned URDF and all OBJ vertices without running upstream
code. Each rigid mesh is enclosed by a link-local sphere, transformed through
the full joint tree including the gear and jaw couplings. Every mesh vertex
is enclosed; convexity of the sphere also encloses all mesh triangles. The
wrist-carrier mesh includes CAD gripper housing geometry; both fingers and
pinion are separate covers. Physical completeness remains unverified.

The existing collision engine receives these covers and per-control displacement
bounds in CAD radians. Bounds use a deliberately loose global lever-arm upper
bound of approximately 1.146 m, formed from all tree translations, bounded jaw
slides and mesh extents. Each ancestor hinge contributes its lever bound; each
slider contributes its coupling gain. Half-sample bound inflation covers motion
between samples under the engine's linear joint-space interpolation assumption.
This is conservative: the wrist cover radius is about 86 mm, and finger cover
radii about 52 and 50 mm. Sphere covers can reject a geometrically feasible fit;
mesh-level narrow-phase checking is not implemented. A rejection is not proof
of physical contact.

`cad_collision_model` integrates CAD-coordinate trajectories with the existing
supervisor collision engine. `RawCadCollision` rejects raw M/P trajectories
without a measured encoder-to-CAD mapping. It deliberately also rejects supplied
mapping arguments pending measured scene/payload/coverage approval; it is a
blocking adapter, not a completed physical-clearance certifier. No unverified
raw-to-display-to-CAD conversion or physical enable path is provided.

Self-collision, moving obstacles/people, cable flex, load motion, braking,
controller path differences and validated dynamics remain outside the model.
The tool frame is a coordinate frame, not another physical body. Payload
geometry must be supplied separately before loaded motions can be cleared.

## Preliminary rack and gripper estimates

`preliminary_scene.json` records the supplied 130 mm gripper width, 70–80 mm
gripper depth, 350 mm rack height and 100 mm divider spacing as **photo estimates**.
No numerical uncertainty was supplied for width, height or spacing, and the
70–80 mm depth interval is not a guaranteed physical bound. Additional
uncertainty stays `null`, meaning unknown, never zero. No CAD scale or geometry
was changed to force a fit to the photo.

`rack_boxes` models four full-depth vertical dividers/posts, the four horizontal
grid levels plus outer top/bottom caps where distinct, and nine trays with floors
and front/back/left/right rims. It transforms all box corners to world coordinates
and inflates the world AABBs by an explicitly supplied uncertainty bound. In the
synthetic test this produces 55 boxes. Rotated enclosing AABBs can over-reject.
Its scalar uncertainty must bound all dimensional and placement errors, including
orientation error across the full rack; no such bound has been measured here.

Rack position/orientation relative to the base, rack depth, divider thickness,
tray floor/rim thickness, rim height and grid bottom offset remain unknown.
Three 100 mm pitches cover 300 mm; the remaining 50 mm of estimated rack height
cannot be assigned to a guessed base/top offset. Gripper height, photo-axis
alignment, jaw travel, held-object envelope and physical CAD mismatch are also
unknown. The model rejects incomplete scene inputs. No numerical tray clearance,
safe orientation or shelf/frame collision result is asserted for any M/P path.

## Travel, speed and candidates

The original two points in every file, byte-exact backups and separate baseline
candidates are preserved. The per-joint before/after travel table and M2-to-P1
bridge audit remain in `REPORT.md` and `report.json`; travel reduction is 0%.
The new `cad_preflight.json` records all 27 CAD-adapter rejections and source
hashes. No candidate has been relabeled optimised or hardware ready.

Reducing reversals or replacing clearance poses without task and scene calibration
could remove necessary withdrawal/release motion. Maximum safe speed cannot be
derived from kinematics, SDK register widths or placeholder inertias. No timing
gain, acceleration limit or speed increase is claimed. Measured mapping, full
scene uncertainty bounds and load-dependent dynamic/feedback baselines are
required before any faster two-waypoint candidate can be validated.

## Offline test report

Run `python -m unittest discover -s tests -v`. Tests include source preservation,
all 27 raw CAD-adapter preflight rejections with zero mock commands, mesh vertex
coverage, all ten body/tool transforms in 16 upstream browser fixtures (tolerance
`1e-8` m / rotation matrix entry), gear/jaw couplings, CAD range rejection,
displacement-bound checks, collision-engine integration, between-sample collision
inflation, all rack parts and missing-parameter rejection. Fixtures validate
agreement with upstream software, not with measured hardware. Existing jam,
stale-feedback, timeout, stop/hold and synthetic collision tests remain included.

Separate-copy result: **31 tests passed**. The CAD preflight report reproduced
byte-for-byte from that copy. Git comparison confirms original M/P files and
the live runner remain unchanged.

Hardware tests: **none**. Physical motion/speed optimisation remains blocked.
Collision or damage prevention cannot be guaranteed.
