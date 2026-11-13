"""Optional Nintendo Joy-Con / Pro Controller adapter for Competition Mode.

Linux evdev backend. Only selects/queues already-loaded waypoint macros.
No joystick-driven raw servo positions, no automatic collision detection.
A queues a combined pickup+placement. B cancels waiting combos. No putting commands.
PLUS/HOME = stop. Only READY recordings may enter the motion queue.

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

DEFAULT_PICKUPS = tuple(f"grab{i}" for i in range(1, 7))
SUPPLY_COLS = ("Plate B left", "Plate A center", "Plate B right")


class SmashMapper:
    """Aim with left stick, select source block with right stick or D-pad."""

    def __init__(self, pickup_names=DEFAULT_PICKUPS):
        if len(pickup_names) != 6 or any(not str(n).strip() for n in pickup_names):
            raise ValueError("Specify exactly six nonempty pickup motion names.")
        self.pickup_names = tuple(str(n).strip().lower() for n in pickup_names)
        self.source_index = 0
        self.armed = False
        self.cursor = (2, 2)
        self.line_index = 0
        self.line_step = 0
        self._command_state = 0
        self._command_since = 0.0
        self._electric_until = 0.0

    @property
    def pickup(self):
        return self.pickup_names[self.source_index]

    @property
    def selection(self):
        return (self.pickup, self.square)

    def _source_notice(self):
        col = self.source_index % 3
        row = self.source_index // 3
        return ("notice", f"Block {self.source_index+1}/6: "
                f"{SUPPLY_COLS[col]}, tier {row+1}, pickup {self.pickup}")

    def dpad(self, direction, pressed=True):
        """LEFT/RIGHT cycle all six slots, UP/DOWN choose source tier."""
        if not pressed:
            return []
        old = self.source_index
        if direction == "LEFT":
            self.source_index = (old - 1) % 6
        elif direction == "RIGHT":
            self.source_index = (old + 1) % 6
        elif direction == "UP":
            self.source_index = old % 3
        elif direction == "DOWN":
            self.source_index = (old % 3) + 3
        return [self._source_notice()] if self.source_index != old else []

    def right_stick(self, x, y):
        """Right stick selects one of three supply plates and one of two tiers."""
        if abs(x) < 0.43 and abs(y) < 0.43:
            return []
        column = 0 if x < -0.43 else 2 if x > 0.43 else 1
        row = 0 if y < 0.43 else 1
        chosen = row * 3 + column
        if chosen == self.source_index:
            return []
        self.source_index = chosen
        return [self._source_notice()]

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
            return [("notice", "Electric input ready (ZR held)" if pressed
                     else "Electric input released")]
        if not pressed:
            return []
        if name in ("PLUS", "HOME"):
            return [("stop", None)]
        # B is NEVER an attack: cancel the last queued combo, or clear all with ZR.
        if name == "B":
            self._command_reset()
            return [("clear", None)] if self.armed else [("undo", None)]
        if name.startswith("DPAD_"):
            return self.dpad(name[5:], pressed)
        if name == "X" and not self.armed:
            self.line_index = (self.line_index + 1) % len(LINES)
            self.line_step = 0
            label, squares = self.line
            return [("notice", f"Line preview: {label}: {' '.join(squares)}")]
        if name == "A":
            now = time.monotonic() if now is None else now
            combo = self.selection
            if self.armed and self._command_state == 3 and now <= self._electric_until:
                self._command_reset()
                return [("replace_pending_combo", combo),
                        ("notice", f"ELECTRIC PLACE: {combo[0]} -> {combo[1]}")]
            if self.armed and self._command_state != 0:
                self._command_reset()
                return [("notice", "Incomplete electric input: no motion queued")]
            return [("add_combo", combo)]
        if name == "Y":
            return [("notice", f"Selected: block {self.source_index+1} "
                    f"({self.pickup}) -> {self.square}. Putting disabled.")]
        if name == "R" and self.armed:
            self._command_reset()
            return [("replace_pending", self.square + "a"),
                    ("notice", f"PRIORITY ATTACK: {self.square}; token/approval required")]
        if name == "L" and self.armed:
            self._command_reset()
            return [("replace_pending_combo", self.selection),
                    ("notice", f"PRIORITY PLACE: {self.pickup} -> {self.square}")]
        if not self.armed:
            return []
        if name == "X":
            label, squares = self.line
            square = squares[self.line_step]
            self.line_step = (self.line_step + 1) % 3
            return [("add_combo", (self.pickup, square)),
                    ("notice", f"Line {label}: queued {self.pickup} -> {square}")]
        if name == "MINUS":
            return [("clear", None)]
        return []


class JoyConGamepad:
    """Read paired controllers without blocking Competition Mode's servo loop."""

    def __init__(self, *, report=print, pickup_names=DEFAULT_PICKUPS):
        try:
            from evdev import InputDevice, list_devices, ecodes
        except ImportError as exc:
            raise RuntimeError(
                "evdev is not installed. Install python3-evdev and use the system Python, "
                "or pip install evdev inside your active virtual environment."
            ) from exc
        self.ecodes = ecodes
        self.report = report
        self.mapper = SmashMapper(pickup_names=pickup_names)
        self.devices = []
        self.axes = {}
        self.axis_config = {}
        self.stick_source = None
        self.right_source = None
        self.right_codes = None
        self.right_axes = {"x": 0., "y": 0.}
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
            axis_set = {axis for axis, _ in abs_codes}
            # A separate right Joy-Con reports ABS_X/Y, while a Pro Controller
            # normally reports its right stick on ABS_RX/RY.
            if (("joy-con (r)" in name or "joy-con right" in name)
                    and {ecodes.ABS_X, ecodes.ABS_Y} <= axis_set):
                self.right_source = device.fd
                self.right_codes = (ecodes.ABS_X, ecodes.ABS_Y)
            elif (self.right_source is None and
                    {ecodes.ABS_RX, ecodes.ABS_RY} <= axis_set):
                self.right_source = device.fd
                self.right_codes = (ecodes.ABS_RX, ecodes.ABS_RY)
            if ((self.stick_source is None or "left" in name or "(l)" in name)
                    and {ecodes.ABS_X, ecodes.ABS_Y} <= axis_set
                    and self.right_source != device.fd):
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
            ecodes.BTN_DPAD_UP: "DPAD_UP",
            ecodes.BTN_DPAD_DOWN: "DPAD_DOWN",
            ecodes.BTN_DPAD_LEFT: "DPAD_LEFT",
            ecodes.BTN_DPAD_RIGHT: "DPAD_RIGHT",
        }
        # Neutral normalized stick; directions persist until a new non-neutral reading.
        self.axes = {"x": 0., "y": 0.}
        self.report("SMASH: LEFT stick aim; RIGHT stick / D-pad pick block 1..6; "
                    "A=pickup+place combo; B=undo last pending; ZR+B=clear all; "
                    "X=cycle lines; ZR+X=next line square; ZR+L=priority place; "
                    "ZR+R=priority attack; ZR+F,D,DF+A=Electric PLACE; "
                    "PLUS/HOME=STOP. NO PUTTING.")
        self.report("A pickup+place is queued ONLY if both saved motion names exist. "
                    "A gamepad disconnect stops playback; physical E-stop remains required.")
        self.report("Block layout: slots 1-3 first tier, 4-6 second; "
                    "columns Plate B left, Plate A center, Plate B right.")
        for i, name in enumerate(self.mapper.pickup_names, 1):
            self.report(f"Supply slot {i}: {name}")
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
        if any(not os.path.exists(d.path) for d in self.devices):
            raise RuntimeError("Joy-Con disconnected; stop requested.")
        ready, _, _ = select.select(self.devices, [], [], 0)
        for device in ready:
            for event in device.read():
                if event.type == self.ecodes.EV_KEY:
                    label = self.button_codes.get(event.code)
                    if label and event.value in (0, 1):
                        events.extend(self.mapper.button(label, event.value == 1))
                elif event.type == self.ecodes.EV_ABS:
                    key = (device.fd, event.code)
                    info = self.axis_config.get(key)
                    if (self.right_source == device.fd and self.right_codes
                            and event.code in self.right_codes and info):
                        which = "x" if event.code == self.right_codes[0] else "y"
                        self.right_axes[which] = self._normalize(event.value, info)
                        events.extend(self.mapper.right_stick(
                            self.right_axes["x"], self.right_axes["y"]))
                    elif (device.fd == self.stick_source and info
                          and event.code in (self.ecodes.ABS_X, self.ecodes.ABS_Y)):
                        which = "x" if event.code == self.ecodes.ABS_X else "y"
                        self.axes[which] = self._normalize(event.value, info)
                        events.extend(self.mapper.stick(self.axes["x"], self.axes["y"]))
                    elif event.code == self.ecodes.ABS_HAT0X:
                        if event.value < 0:
                            events.extend(self.mapper.dpad("LEFT"))
                        elif event.value > 0:
                            events.extend(self.mapper.dpad("RIGHT"))
                    elif event.code == self.ecodes.ABS_HAT0Y:
                        if event.value < 0:
                            events.extend(self.mapper.dpad("UP"))
                        elif event.value > 0:
                            events.extend(self.mapper.dpad("DOWN"))
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
