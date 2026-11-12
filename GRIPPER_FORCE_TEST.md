# Gripper current and resistance test

This is a short, supervised gripper-only commissioning experiment for the repaired arm. It measures free-motion current/load first, then checks for resistance while closing around an object resting on a support. It does not select a universally safe jaw force or complete Script 6's commissioning form.

## Start

Stop other programs that control the arm. Support the arm mechanically with all joints relaxed, leave the sponge jaw pads attached, and remove any object from the jaws.

```bash
python3 scripts/gripper_force_test.py --open 1979 --closed 3073
```

The endpoints above are the manually measured positions for this repaired gripper: open enough for the wide cylinder, and empty closed at light pad contact. They do not establish that the wider dumbbell orientation will fit.

If an endpoint is rejected by the installed angle limits, inspect the exact mapping first:

```bash
python3 scripts/gripper_force_test.py --open 1979 --closed 3073 --check
```

This prints the loaded settings path, gripper ID, zero, direction, ticks per revolution, allowed angular/raw intervals, and both supplied endpoint angles. It does not import the arm hardware helpers, require an interactive terminal, open a serial connection, or move any motor. Exit status 1 means a configuration check failed; status 0 means the configuration checks passed, not that physical clearance or force was tested. Neither mode widens limits or silently changes the supplied endpoints.

Follow the two prompts:

1. Keep the jaws empty and hands clear for the initial open-close-open measurement.
2. After the gripper is released, place a small object on a support between the pads, remove your hands, and start the closing test.

During a moving test, Space, Q, Escape, or Ctrl+C requests a gripper torque release. The physical motor-power stop remains the fallback if the computer or connection fails. The arm and object must remain supported: this test releases the gripper rather than carrying an object.

## What is limited and measured

The initial experimental torque-limit register cap is 50 out of 1000, or 5% of its register scale. An existing lower cap is retained. This is a deliberately low test output, **not a verified safe finger-force value**. The software never automatically raises the cap when movement fails. `--cap` permits a lower trial cap, not a higher one.

Only the configured gripper receives writes. The test refuses to begin if another configured joint reports enabled torque. Each motor target is a small step toward the supplied endpoint, with load/current feedback between steps. Low output, small target steps, a feedback deadline, a progress timeout, an overall move timeout, and torque release on exit bound the experiment. A pre-existing verified guard current limit, if available, is also honored.

The empty closing pass provides a current baseline. The object pass uses a current-rise threshold above that measured peak and a load threshold below the test output cap. These are experimental resistance detectors, not measurements of jaw force or the joint's strength. The full trial summary prints the thresholds and measured peaks.

## Reading a result

- A resistance stop means the readings crossed a detector threshold. Check visually that the jaws actually contacted the intended object; a jam or friction can also cause a rise.
- A no-progress stall means resistance or insufficient drive. It is not proof of a successful grip, and the software does not increase output to push through it.
- Reaching the empty closed endpoint without resistance is not a successful grasp.
- If the empty measurement stops early, the object test is not run. Keep the output and inspect the cause before retrying.

Valid zero current/load samples are accepted. Missing packets and invalid readings are errors, not zero effort. Current, load, temperature, and position are observed only while this test is polling.

No recordings, EEPROM settings, or `gripper_guard.local.json` are written. The helper never sets `commissioned=True`. The last reduced volatile output cap is left in place; power cycling can reset it to the servo's configured startup maximum. Script 6 must still apply its own verified limits before playback.

## Validation and reference

Run the hardware-free regression tests with:

```bash
python3 -m unittest discover -s tests -p 'test_gripper_force_test.py' -v
```

Register reference: [Waveshare ST servo memory table](https://docs.waveshare.com/Memory_Map_Explanation/ST_Servo_Memory_Map_Explanation). In particular, register 48 is the volatile output/torque limit, register 60 reports the motor drive duty-cycle magnitude, and register 69 reports current. A drive-effort estimate is not a calibrated force sensor at the jaws.
