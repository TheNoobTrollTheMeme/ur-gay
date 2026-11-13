"""Optional Nintendo Joy-Con / Pro Controller adapter for Competition Mode.

Linux evdev backend. Only selects/queues already-loaded waypoint macros.
No joystick-driven raw servo positions, no automatic collision detection.
Hold ZR before sending A/B/Y or line-step commands. PLUS/HOME = stop.

Linux Joy-Con names, key codes, and axis ranges can differ by driver.
Run --diagnose with torque disabled to inspect hardware mappings.
"""
from __future__ import annotations

import argparse
import os
import select
import sys
import time

LINES = (
    ("top", ("c1r1", "c2r1", "c3r1")),
    ("middle", ("c1r2", "c2r2", "c3r2")),
    ("bottom", ("c1r3", "c2r3", "c3r3")),
    ("left", ("c1r1", "c1r2", "c1r3")),
    ("center column", ("c2r1", "c2r2", "c2r3")),
    ("right", ("c3r1", "c3r2", "c3r3")),
    ("main diagonal", ("c1r1", "c2r2", "c3r3")),
    ("other diagonal", ("c3r1", "c2r2", "c1r3")),
)

class SmashMapper:
    """Convert semantic controls into abstract, testable queue actions."""

    def __init__(self):
        self.armed = False
        self.cursor = (2, 2)
        self.line_index = 0
        self.line_step = 0
        self._command_state = 0
        self._command_since = 0.0
        self._electric_until = 0.0

    @property
    def square(self):
        return f"c{self.cursor[0]}r{self.cursor[1]}"

    @property
    def line(self):
        return LINES[self.line_index]

    def _command_reset(self):
        self._command_state = 0
        self._command_since = 0.0
        self._electric_until = 0.0

    def stick(self, x, y, *, now=None):
        """Aim while unarmed; hold ZR to lock target and input Kazuya commands.

        EWGF-style gesture: right, down, down-right, then A within 0.45s.
        This is an OPTIONAL quick counter, not a servo-speed modifier.
        """
        x, y = max(-1., min(1., x)), max(-1., min(1., y))
        if self.armed:
            now = time.monotonic() if now is None else now
            if x > 0.55 and abs(y) < 0.35:
                direction = "F"
            elif y > 0.55 and abs(x) < 0.35:
                direction = "D"
            elif x > 0.5 and y > 0.5:
                direction = "DF"
            else:
                direction = "N"
            if self._command_state and now - self._command_since > 1.25:
                self._command_reset()
            if direction == "F" and self._command_state == 0:
                self._command_state = 1
                self._command_since = now
            elif direction == "D" and self._command_state == 1:
                self._command_state = 2
            elif direction == "DF" and self._command_state == 2:
                self._command_state = 3
                self._electric_until = now + 0.45
            return []
        threshold = .43
        if abs(x) > threshold or abs(y) > threshold:
            new_cursor = (
                2 + (1 if x > threshold else (-1 if x < -threshold else 0)),
                2 + (1 if y > threshold else (-1 if y < -threshold else 0)),
            )
            if new_cursor != self.cursor:
                self.cursor = new_cursor
                return [("notice", f"Selected {self.square}")]
        return []

    def button(self, name, pressed, *, now=None):
        if name == "ZR":
            self.armed = pressed
            self._command_reset()
            return [("notice", "ARMED: ZR held (target locked)"
                     if pressed else "Disarmed")]
        if not pressed:
            return []
        if name in ("PLUS", "HOME"):
            return [("stop", None)]
        if name == "X" and not self.armed:
            self.line_index = (self.line_index + 1) % len(LINES)
            self.line_step = 0
            label, squares = self.line
            return [("notice", f"Line preview: {label}: {' '.join(squares)}")]
        if not self.armed:
            return []
        if name == "A":
            now = time.monotonic() if now is None else now
            if self._command_state == 3 and now <= self._electric_until:
                self._command_reset()
                return [("replace_pending", self.square + "a"),
                        ("notice", f"ELECTRIC: counterattack {self.square} queued first")]
            if self._command_state != 0:
                self._command_reset()
                return [("notice", "Incomplete electric input: no motion queued")]
            return [("add", self.square)]
        if name == "B":
            # Attacks require a token and the referee's approval outside this program.
            self._command_reset()
            return [("add", self.square + "a")]
        if name == "L":
            self._command_reset()
            return [("replace_pending", self.square),
                    ("notice", f"PRIORITY PLACE: {self.square}, pending combo replaced")]
        if name == "R":
            self._command_reset()
            return [("replace_pending", self.square + "a"),
                    ("notice", f"PRIORITY ATTACK: {self.square}, pending combo replaced")]
        if name == "Y":
            return [("add", "tapinr")]
        if name == "X":
            label, squares = self.line
            square = squares[self.line_step]
            self.line_step = (self.line_step + 1) % 3
            return [("add", square), ("notice", f"Line {label}: queued {square}")]
        if name == "MINUS":
            return [("clear", None)]
        return []


class JoyConGamepad:
    """Read paired controllers without blocking Competition Mode's servo loop."""

    def __init__(self, *, report=print):
        try:
            from evdev import InputDevice, list_devices, ecodes
        except ImportError as exc:
            raise RuntimeError(
                "evdev is not installed. Install python3-evdev and use the system Python, "
                "or pip install evdev inside your active virtual environment."
            ) from exc
        self.ecodes = ecodes
        self.report = report
        self.mapper = SmashMapper()
        self.devices = []
        self.axes = {}
        self.axis_config = {}
        self.stick_source = None
        for path in list_devices():
            device = InputDevice(path)
            name = device.name.lower()
            if not any(term in name for term in ("joy-con", "joycon", "pro controller")):
                device.close()
                continue
            try:
                capabilities = device.capabilities(absinfo=True)
            except OSError:
                device.close()
                continue
            key_codes = capabilities.get(ecodes.EV_KEY, [])
            if not key_codes:
                device.close()
                continue
            self.devices.append(device)
            abs_codes = capabilities.get(ecodes.EV_ABS, [])
            for axis, info in abs_codes:
                if axis in (ecodes.ABS_X, ecodes.ABS_Y, ecodes.ABS_RX, ecodes.ABS_RY):
                    self.axis_config[(device.fd, axis)] = info
            if ((self.stick_source is None or "left" in name or "(l)" in name)
                    and any(axis in (ecodes.ABS_X, ecodes.ABS_Y)
                            for axis, _ in abs_codes)):
                self.stick_source = device.fd
        if not self.devices:
            raise RuntimeError("No paired Joy-Con / Pro Controller input device found.")
        self.button_codes = {
            ecodes.BTN_EAST: "A",
            ecodes.BTN_SOUTH: "B",
            ecodes.BTN_NORTH: "X",
            ecodes.BTN_WEST: "Y",
            ecodes.BTN_TR2: "ZR",
            ecodes.BTN_TL: "L",
            ecodes.BTN_TR: "R",
            ecodes.BTN_START: "PLUS",
            ecodes.BTN_SELECT: "MINUS",
            ecodes.BTN_MODE: "HOME",
        }
        # Neutral normalized stick; directions persist until a new non-neutral reading.
        self.axes = {"x": 0., "y": 0.}
        self.report("SMASH mode: stick selects 3x3 cell; hold ZR+A place; "
                    "ZR+B attack; X cycle line; ZR+X next line cell; "
                    "ZR+Y tapinr; ZR+L priority place; ZR+R priority attack; "
                    "ZR+F,D,DF+A Electric; ZR+MINUS clear pending; PLUS/HOME STOP.")
        self.report("Only use attack after a confirmed token and referee approval. "
                    "Controller disconnect stops playback; physical E-stop is still required.")
        for dev in self.devices:
            self.report(f"Controller: {dev.name} [{dev.path}]")

    @staticmethod
    def _normalize(raw, info):
        lo, hi = info.min, info.max
        if hi <= lo:
            return 0.
        center = (hi + lo) / 2
        scale = (hi - lo) / 2
        return max(-1., min(1., (raw - center) / scale))

    def poll(self):
        events = []
        # A disappearing /dev/input node is treated as a lost connection.
        if any(not os.path.exists(d.path) for d in self.devices):
            raise RuntimeError("Joy-Con disconnected; stop requested.")
        ready, _, _ = select.select(self.devices, [], [], 0)
        for device in ready:
            for event in device.read():
                if event.type == self.ecodes.EV_KEY:
                    label = self.button_codes.get(event.code)
                    if label and event.value in (0, 1):
                        events.extend(self.mapper.button(label, event.value == 1))
                elif (event.type == self.ecodes.EV_ABS and
                      device.fd == self.stick_source and
                      event.code in (self.ecodes.ABS_X, self.ecodes.ABS_Y)):
                    info = self.axis_config.get((device.fd, event.code))
                    if info is not None:
                        direction = "x" if event.code == self.ecodes.ABS_X else "y"
                        self.axes[direction] = self._normalize(event.value, info)
                        events.extend(self.mapper.stick(self.axes["x"], self.axes["y"]))
        return events

    def close(self):
        for device in self.devices:
            device.close()
        self.devices.clear()


def diagnose():
    from evdev import InputDevice, list_devices, categorize, ecodes
    devices = []
    for path in list_devices():
        device = InputDevice(path)
        if any(word in device.name.lower() for word in ("joy-con", "joycon", "pro controller")):
            devices.append(device)
        else:
            device.close()
    if not devices:
        print("No Joy-Con / Pro Controller detected.")
        return 1
    print("INPUT DIAGNOSTIC ONLY: no motor connection. Ctrl+C quits.")
    try:
        while True:
            ready, _, _ = select.select(devices, [], [], 0.1)
            for device in ready:
                for ev in device.read():
                    if ev.type in (ecodes.EV_KEY, ecodes.EV_ABS):
                        print(device.name, categorize(ev), flush=True)
    except KeyboardInterrupt:
        return 0
    finally:
        for device in devices:
            device.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--diagnose", action="store_true")
    args = parser.parse_args()
    if not args.diagnose:
        parser.error("Use --diagnose; gameplay is launched from script 6 Competition Mode.")
    sys.exit(diagnose())
