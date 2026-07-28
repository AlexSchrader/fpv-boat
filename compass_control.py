"""Compass heading from a QMC5883P magnetometer over I2C.

IMPORTANT: the board on this boat answers at I2C 0x2C, which is the QMC5883P —
a DIFFERENT part from the QMC5883L (0x0D) that most libraries target; that
mismatch is why off-the-shelf code reads nothing. Identity is verified at init
via the chip-ID register (0x00 == 0x80); if it doesn't match, the module
disables itself rather than reading garbage.

Register map used (QMC5883P):
    0x00        chip ID (0x80)
    0x01-0x06   X/Y/Z int16, LSB first
    0x09        status (bit0 = data ready)
    0x0A        control 1 (mode/ODR/OSR)  -> 0xCD: continuous, 200 Hz
    0x0B        control 2 (set/reset, range) -> 0x08: set/reset on

Heading math: atan2(y, x) + magnetic declination, normalized 0-360. Declination
for the Raleigh NC area is about -9 deg (west); set COMPASS_DECLINATION_DEG for
your location (NOAA calculator — it drifts yearly).

Calibration is NOT optional on a boat full of motors and a buck converter.
Hard-iron offsets are captured with:
    python3 compass_control.py calibrate
(rotate the boat slowly through a full 360 during the 30 s window) and persist
to ~/.fpv-boat-compass.json so they survive reboots. Uncalibrated readings are
reported with calibrated=False so the HUD can flag them.

Known limits: tilt-naive (accurate only near level — chop will wander it), and
motor current swings the field, so mount it as far from the drive wiring as the
hull allows. Do NOT conflate this heading (where the bow points, works at rest)
with GPS course-over-ground (direction of travel, needs motion) — the HUD shows
both, labelled HDG and COG.

No-op-if-missing like the other modules: without smbus2 / the sensor, read()
reports valid=False and nothing raises.

Config via env vars:
    COMPASS_I2C_ADDR         (default 0x2C)
    COMPASS_DECLINATION_DEG  (default -9, Raleigh NC)
"""

import json
import math
import os

I2C_ADDR = int(os.environ.get("COMPASS_I2C_ADDR", "0x2C"), 0)
DECLINATION_DEG = float(os.environ.get("COMPASS_DECLINATION_DEG", "-9"))
CAL_FILE = os.path.expanduser("~/.fpv-boat-compass.json")

_REG_CHIP_ID = 0x00
_REG_DATA = 0x01
_REG_STATUS = 0x09
_REG_CTRL1 = 0x0A
_REG_CTRL2 = 0x0B
_CHIP_ID = 0x80


def heading_from(x, y, declination=DECLINATION_DEG):
    """Heading in degrees 0-360 from calibrated X/Y field components (tilt-naive)."""
    if x is None or y is None or (x == 0 and y == 0):
        return None
    deg = math.degrees(math.atan2(y, x))
    return round((deg + declination + 360.0) % 360.0, 1)


def tilt_compensated_heading(mx, my, mz, pitch_deg, roll_deg, declination=DECLINATION_DEG):
    """Heading corrected for boat pitch/roll (from the IMU).

    Rotates the measured field back into the horizontal plane before the atan2,
    so the heading stays put while the hull pitches in chop. Falls back to the
    tilt-naive heading when attitude is unavailable. Assumes the compass and IMU
    axes are mounted aligned (X forward, Y starboard) — flip signs here if a
    bench check against a phone compass shows mirrored behavior.
    """
    if pitch_deg is None or roll_deg is None or mz is None:
        return heading_from(mx, my, declination)
    if mx is None or my is None:
        return None
    p = math.radians(pitch_deg)
    r = math.radians(roll_deg)
    xh = mx * math.cos(p) + mz * math.sin(p)
    yh = (mx * math.sin(r) * math.sin(p) + my * math.cos(r)
          - mz * math.sin(r) * math.cos(p))
    if xh == 0 and yh == 0:
        return None
    deg = math.degrees(math.atan2(yh, xh))
    return round((deg + declination + 360.0) % 360.0, 1)


def apply_hard_iron(raw, offsets):
    """Subtract per-axis hard-iron offsets from a raw (x, y, z) tuple."""
    return tuple(r - o for r, o in zip(raw, offsets))


class Compass:
    """read() -> {heading, calibrated, valid}; no-op without the sensor."""

    def __init__(self):
        self._bus = None
        self.hardware = False
        self.offsets = (0.0, 0.0, 0.0)
        self.calibrated = self._load_cal()
        try:
            from smbus2 import SMBus
            self._bus = SMBus(1)
            chip = self._bus.read_byte_data(I2C_ADDR, _REG_CHIP_ID)
            if chip != _CHIP_ID:
                raise RuntimeError(
                    f"chip id 0x{chip:02X} at 0x{I2C_ADDR:02X} is not a QMC5883P (want 0x80)")
            self._bus.write_byte_data(I2C_ADDR, _REG_CTRL2, 0x08)  # set/reset on
            self._bus.write_byte_data(I2C_ADDR, _REG_CTRL1, 0xCD)  # continuous 200 Hz
            self.hardware = True
        except Exception as e:
            print(f"[compass] disabled ({e}); telemetry will report invalid")

    def _load_cal(self):
        try:
            with open(CAL_FILE) as f:
                cal = json.load(f)
            self.offsets = tuple(float(v) for v in cal["offsets"])
            return True
        except Exception:
            return False

    def _read_raw(self):
        data = self._bus.read_i2c_block_data(I2C_ADDR, _REG_DATA, 6)
        def s16(lo, hi):
            v = lo | (hi << 8)
            return v - 65536 if v > 32767 else v
        return (s16(data[0], data[1]), s16(data[2], data[3]), s16(data[4], data[5]))

    def read(self, tilt=None):
        """tilt: optional (pitch_deg, roll_deg) from the IMU for tilt compensation."""
        if not self.hardware:
            return {"heading": None, "calibrated": self.calibrated, "valid": False}
        try:
            x, y, z = apply_hard_iron(self._read_raw(), self.offsets)
        except Exception:
            return {"heading": None, "calibrated": self.calibrated, "valid": False}
        if tilt is not None:
            heading = tilt_compensated_heading(x, y, z, tilt[0], tilt[1])
        else:
            heading = heading_from(x, y)
        return {"heading": heading, "calibrated": self.calibrated, "valid": True}

    def calibrate(self, seconds=30):
        """Capture hard-iron offsets: rotate the boat a slow full 360 while this runs."""
        import time
        if not self.hardware:
            print("no sensor — cannot calibrate")
            return False
        print(f"calibrating for {seconds}s — rotate the boat slowly through a full circle…")
        mins = [float("inf")] * 3
        maxs = [float("-inf")] * 3
        end = time.monotonic() + seconds
        n = 0
        while time.monotonic() < end:
            try:
                raw = self._read_raw()
                for i, v in enumerate(raw):
                    mins[i] = min(mins[i], v)
                    maxs[i] = max(maxs[i], v)
                n += 1
            except Exception:
                pass
            time.sleep(0.05)
        if n < 50:
            print("not enough samples — check wiring")
            return False
        self.offsets = tuple((mx + mn) / 2.0 for mn, mx in zip(mins, maxs))
        with open(CAL_FILE, "w") as f:
            json.dump({"offsets": self.offsets}, f)
        self.calibrated = True
        print(f"saved offsets {self.offsets} -> {CAL_FILE}")
        return True


if __name__ == "__main__":
    import sys
    import time

    c = Compass()
    print("hardware:", c.hardware, "addr:", hex(I2C_ADDR),
          "calibrated:", c.calibrated, "declination:", DECLINATION_DEG)
    if len(sys.argv) > 1 and sys.argv[1] == "calibrate":
        c.calibrate()
    else:
        for _ in range(10):
            print(" ", c.read())
            time.sleep(0.5)
    print("Done.")
