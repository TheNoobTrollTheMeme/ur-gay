# STS3215 robot arm: strict safety-first development rules

This repository controls a real 5DOF robot arm. **Never run actual hardware motions or open the servo serial port during automated setup, testing, or code review.** Work in a separate copy. Do not overwrite live local edits or push to main automatically.

## Before any physical testing
- Require explicit operator approval for each real-world movement test, and require the operator to remain beside the arm with access to power cutoff/E-stop. Disconnect power before physical inspection. Never assume an E-stop can safely de-energize a raised load: check drop hazards.
- No unsupervised motion, no auto-deploy to hardware, no unattended remote movement.
- Enforce a positive hardware-enable gate which defaults to disabled; unit tests and simulations must not import/activate a real servo driver.
- Preserve exactly two *user-recorded* waypoints per sequence; collision checks may interpolate sampled states between them but must not silently add waypoints or claim that every 2-waypoint path is feasible.
- Derive servo IDs, units, zero offsets, valid ranges, speed and feedback capabilities from actual project code/docs or measured calibration. Never guess or fill placeholder defaults that can move the robot.
- First implement simulation/mock driver, then command validation, timeouts, servo feedback monitoring, and fail-closed state machine. Build unit tests with simulated stalls, stale feedback, missing devices, and out-of-range joint commands.
- Check the swept volume of the gripper, wrist and all other links against trays, frame and shelf with clearance margins informed by measurement uncertainty. Sampling only the endpoint is not collision checking.
- During a supervised low-speed physical test only, collect baseline position error and any available load/current/temperature telemetry for each servo during normal motion. Thresholds must be selected using real baselines and vendor documentation, then validated experimentally; more sensitive is not always safer if it causes false trips or uncontrolled load drop.
- A stop must abort queued motion and use documented safe hold/stop semantics, not an arbitrary torque-off; if telemetry is stale/unavailable or thresholds are exceeded, stop sending motion and alert the operator.
- Never claim collisions or damage can be prevented with certainty. Physical inspections for cracks, bent jaws, binding and loose fasteners are still required.
- For all code changes create a short test report, noting tests not run on hardware.
