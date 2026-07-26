"""Camera pan/tilt head-tracking via a PCA9685 driving two SG90 servos.

The Quest headset's orientation (yaw/pitch) is streamed from the viewer over the
control websocket; this module maps that to two servo angles so the on-boat
camera follows where the pilot looks. A PCA9685 (16-channel PWM over I2C) drives
the servos rather than the Pi's own PWM — the Pi's two hardware-PWM channels are
already taken by the L298N, and software PWM jitters servos badly.

Same self-contained, no-op-if-missing pattern as motor_control.py: importing
this never breaks the server. Without `adafruit_servokit` / the PCA9685 wired,
every method is a no-op that just tracks the last commanded angle.

    PCA9685 VCC -> Pi 3V3     SDA -> GPIO2/pin3     SCL -> GPIO3/pin5
    PCA9685 V+  -> 5V servo rail (buck converter, NOT the Pi 5V)
    servo signal -> PCA9685 channel PAN_CHANNEL / TILT_CHANNEL

NOTE: the PCA9685 and the INA219 (battery_control.py) BOTH default to I2C 0x40 —
they collide on the same bus. Move one: set PAN_TILT_I2C_ADDR (and bridge the
PCA9685's A0 solder jumper) or BATTERY_I2C_ADDR. See HARDWARE.md.

Bench test: `python3 pan_tilt_control.py` sweeps both servos through their range.

Config via env vars (all optional):
    PAN_TILT_I2C_ADDR   PCA9685 address (default 0x40)
    PAN_CHANNEL         PCA9685 channel for the pan servo (default 0)
    TILT_CHANNEL        PCA9685 channel for the tilt servo (default 1)
    PAN_RANGE_DEG       head yaw that maps to full pan travel (default 90)
    TILT_RANGE_DEG      head pitch that maps to full tilt travel (default 45)
    PAN_SIGN / TILT_SIGN  flip direction if a servo tracks backwards (1 or -1)
    PAN_CENTER / TILT_CENTER  neutral servo angle = camera straight/level
                          (default 90; e.g. TILT_CENTER=115 to level a mount
                          that aims down at 90)
    PAN_SPAN / TILT_SPAN  servo degrees of travel at full head rotation (90/45)
"""

import os

I2C_ADDR = int(os.environ.get("PAN_TILT_I2C_ADDR", "0x40"), 0)
PAN_CHANNEL = int(os.environ.get("PAN_CHANNEL", "0"))
TILT_CHANNEL = int(os.environ.get("TILT_CHANNEL", "1"))

# How much head rotation (degrees) maps to the servo's full half-travel.
PAN_RANGE_DEG = float(os.environ.get("PAN_RANGE_DEG", "90"))
TILT_RANGE_DEG = float(os.environ.get("TILT_RANGE_DEG", "45"))
PAN_SIGN = 1 if os.environ.get("PAN_SIGN", "1").lstrip("+") != "-1" else -1
TILT_SIGN = 1 if os.environ.get("TILT_SIGN", "1").lstrip("+") != "-1" else -1

# Servo travel limits (degrees, SG90 is 0..180). Tilt is clamped narrower so the
# camera can't crank into the hull.
PAN_MIN, PAN_MAX = 0, 180
TILT_MIN, TILT_MAX = 45, 135
# Neutral ("centered") servo angle — the angle the camera sits at when you're
# looking straight ahead / level. Tunable via env so you can point the camera
# level without remounting the servo horn (e.g. TILT_CENTER=115 if the mount
# aims it down at 90).
PAN_CENTER = float(os.environ.get("PAN_CENTER", "90"))
TILT_CENTER = float(os.environ.get("TILT_CENTER", "90"))
# Servo degrees of travel at full head rotation (kept independent of center so
# retuning the center doesn't shrink the range).
PAN_SPAN = float(os.environ.get("PAN_SPAN", "90"))
TILT_SPAN = float(os.environ.get("TILT_SPAN", "45"))


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def head_to_servo(yaw_deg, pitch_deg):
    """Map head yaw/pitch (deg; 0 = looking forward/level) to (pan, tilt) servo angles.

    Pure function: head rotation scales from the neutral center by PAN_SPAN/
    TILT_SPAN servo degrees at full PAN_RANGE_DEG/TILT_RANGE_DEG, clamped to the
    servo's safe travel. Sign env vars flip a servo that tracks the wrong way;
    the center env vars set where "straight/level" is.
    """
    pan = PAN_CENTER + PAN_SIGN * (yaw_deg / PAN_RANGE_DEG) * PAN_SPAN
    tilt = TILT_CENTER + TILT_SIGN * (pitch_deg / TILT_RANGE_DEG) * TILT_SPAN
    return _clamp(pan, PAN_MIN, PAN_MAX), _clamp(tilt, TILT_MIN, TILT_MAX)


class PanTiltController:
    """set_head(yaw, pitch) / center(); no-op (tracks angles only) without a PCA9685."""

    def __init__(self):
        self.pan = PAN_CENTER
        self.tilt = TILT_CENTER
        self._kit = None
        self.hardware = False
        try:
            from adafruit_servokit import ServoKit
            self._kit = ServoKit(channels=16, address=I2C_ADDR)
            self.hardware = True
            self.center()
        except Exception as e:
            print(f"[pantilt] hardware disabled ({e}); running in software-only mode")

    def set_head(self, yaw_deg, pitch_deg):
        pan, tilt = head_to_servo(yaw_deg, pitch_deg)
        self._write(pan, tilt)

    def center(self):
        self._write(PAN_CENTER, TILT_CENTER)

    def _write(self, pan, tilt):
        self.pan = pan
        self.tilt = tilt
        if not self.hardware:
            return
        try:
            self._kit.servo[PAN_CHANNEL].angle = pan
            self._kit.servo[TILT_CHANNEL].angle = tilt
        except Exception:
            pass


if __name__ == "__main__":
    import time

    pt = PanTiltController()
    print("hardware:", pt.hardware, "addr:", hex(I2C_ADDR))
    print("sweeping pan/tilt (Ctrl-C to stop)…")
    for yaw in (0, -PAN_RANGE_DEG, PAN_RANGE_DEG, 0):
        pt.set_head(yaw, 0)
        print(f"  yaw {yaw:+.0f} -> pan {pt.pan:.0f}")
        time.sleep(0.8)
    for pitch in (0, -TILT_RANGE_DEG, TILT_RANGE_DEG, 0):
        pt.set_head(0, pitch)
        print(f"  pitch {pitch:+.0f} -> tilt {pt.tilt:.0f}")
        time.sleep(0.8)
    pt.center()
    print("Done.")
