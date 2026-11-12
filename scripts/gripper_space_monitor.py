#!/usr/bin/env python3
"""Space-triggered, read-only STS3215 gripper effort sampling.

Does not enable torque or command motion. Values while torque is OFF are
expected to be near zero. This cannot measure jaw force.
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import arm_settings as settings
from arm_helpers import connect, check_result
from gripper_guard import read_feedback
from terminal_keys import RawTerminal


def main():
    print("GRIPPER EFFORT MONITOR (READ ONLY)")
    print("Space = 2-second sample; Q/Esc = quit. No motor commands.")
    print("Load is drive effort, not measured jaw torque or grip force.")
    port, handler = connect()
    try:
        sid = settings.SERVO_IDS["gripper"]
        with RawTerminal() as term:
            while True:
                key = term.read_key(0.05)
                if key in ("q", "\x1b", "\x03"):
                    return
                if key != " ":
                    continue
                samples = []
                deadline = time.monotonic() + 2.0
                print("\nSampling for 2 seconds; Space stops sampling.", flush=True)
                while time.monotonic() < deadline:
                    key = term.read_key(0.0)
                    if key in ("q", "\x1b", "\x03"):
                        return
                    if key == " ":
                        break
                    f = read_feedback(handler, sid, check_result)
                    samples.append(f)
                    time.sleep(0.05)
                if samples:
                    print(
                        f"n={len(samples)} position={samples[-1].position} "
                        f"load_peak={max(f.load for f in samples)} "
                        f"load_avg={sum(f.load for f in samples)/len(samples):.1f} "
                        f"current_peak_raw={max(f.current for f in samples)} "
                        f"current_avg_raw={sum(f.current for f in samples)/len(samples):.1f} "
                        f"temp_max_C={max(f.temperature for f in samples)}",
                        flush=True,
                    )
                else:
                    print("No readings obtained.", flush=True)
                print("Space = sample again; Q = quit.", flush=True)
    finally:
        port.closePort()


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\nStopped.")
