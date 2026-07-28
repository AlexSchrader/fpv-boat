"""IMU (accelerometer + gyro) from an MPU-6050 on a GY-521 board, over I2C.

Provides pitch/roll for the HUD and for tilt-compensating the compass heading
(the QMC5883P heading is tilt-naive — it wanders as the boat pitches in chop).
A 50 Hz daemon thread samples the sensor and keeps an EMA-smoothed attitude;
the server just calls read() for the latest snapshot.

Wiring (shares the I2C bus): VCC -> 3V3, GND -> common ground, SDA -> pin 3,
SCL -> pin 5. Leave AD0/INT/XDA/XCL unconnected (AD0 low -> address 0x68).

Identity is verified at init via WHO_AM_I (0x75 == 0x68); the chip boots in
sleep mode, so init clears PWR_MGMT_1 — without that write every register
reads zero, which is the classic "wired right, reads nothing" trap.

Same no-op-if-missing pattern as the other modules: without smbus2 or the
sensor, read() reports valid=False and nothing raises.

Mounting: the board rarely sits perfectly flat in the hull, so pitch/roll are
reported relative to a captured "level" pose, not the bare board. Capture it
once with the boat sitting level:

    python3 imu_control.py level

which stores the current attitude as zero in ~/.fpv-boat-imu.json (survives
reboots, like the compass calibration). Until captured, raw board angles are
reported and — if they're large (board on its side) — the server skips compass
tilt-compensation rather than feeding it garbage.

Config via env vars:
    IMU_I2C_ADDR   (default 0x68; 0x69 if the board's AD0 is tied high)

Bench test: `python3 imu_control.py` prints pitch/roll — tilt the board and
watch them follow.
"""

import json
import math
import os
import threading
import time

I2C_ADDR = int(os.environ.get("IMU_I2C_ADDR", "0x68"), 0)
LEVEL_FILE = os.path.expanduser("~/.fpv-boat-imu.json")

_REG_WHO_AM_I = 0x75
_REG_PWR_MGMT_1 = 0x6B
_REG_ACCEL = 0x3B          # 14 bytes: accel xyz, temp, gyro xyz (big-endian)
_WHO_AM_I = 0x68

_ACCEL_LSB_PER_G = 16384.0   # ±2 g full scale (power-on default)
_GYRO_LSB_PER_DPS = 131.0    # ±250 °/s full scale (power-on default)

_SAMPLE_HZ = 50
_ATT_EMA = 0.15              # attitude smoothing per sample


def pitch_roll_from_accel(ax, ay, az):
    """Pitch/roll in degrees from an accelerometer gravity vector.

    Convention: level = (0, 0); pitch positive nose-up (X axis forward),
    roll positive right-side-down (Y axis to starboard). Pure and unit-tested.
    Returns (None, None) for a degenerate (free-fall) vector.
    """
    if ax is None or ay is None or az is None:
        return None, None
    mag = math.sqrt(ax * ax + ay * ay + az * az)
    if mag < 1e-6:
        return None, None
    pitch = math.degrees(math.atan2(-ax, math.sqrt(ay * ay + az * az)))
    roll = math.degrees(math.atan2(ay, az))
    return round(pitch, 1), round(roll, 1)


def apply_level(pitch, roll, level):
    """Subtract the captured mounting pose so 'boat level' reads (0, 0).

    level is (pitch0, roll0) or None. First-order correction — fine for the
    small angles a hull sees; mount the board near-flat for best accuracy.
    """
    if pitch is None or roll is None:
        return pitch, roll
    if not level:
        return pitch, roll
    return round(pitch - level[0], 1), round(roll - level[1], 1)


class IMU:
    """read() -> {pitch, roll, accel_g, gyro_dps, temp_c, valid}."""

    def __init__(self):
        self._bus = None
        self.hardware = False
        self._lock = threading.Lock()
        self._pitch = None
        self._roll = None
        self._accel = (None, None, None)
        self._gyro = (None, None, None)
        self._temp = None
        self._last_ok = 0.0
        self.level = self._load_level()   # (pitch0, roll0) or None
        self.leveled = self.level is not None
        try:
            from smbus2 import SMBus
            self._bus = SMBus(1)
            who = self._bus.read_byte_data(I2C_ADDR, _REG_WHO_AM_I)
            if who != _WHO_AM_I:
                raise RuntimeError(
                    f"WHO_AM_I 0x{who:02X} at 0x{I2C_ADDR:02X} is not an MPU-6050 (want 0x68)")
            # the chip powers up ASLEEP — wake it or every register reads 0
            self._bus.write_byte_data(I2C_ADDR, _REG_PWR_MGMT_1, 0x00)
            time.sleep(0.05)
            self.hardware = True
            threading.Thread(target=self._run, daemon=True).start()
        except Exception as e:
            print(f"[imu] disabled ({e}); telemetry will report invalid")

    def _load_level(self):
        try:
            with open(LEVEL_FILE) as f:
                cal = json.load(f)
            return (float(cal["pitch0"]), float(cal["roll0"]))
        except Exception:
            return None

    def capture_level(self, seconds=2.0):
        """Store the current (averaged) attitude as the boat-level zero pose."""
        if not self.hardware:
            print("no sensor — cannot capture level")
            return False
        samples = []
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            with self._lock:
                if self._pitch is not None:
                    samples.append((self._pitch, self._roll))
            time.sleep(0.05)
        if len(samples) < 10:
            print("not enough samples — is the thread running?")
            return False
        p0 = sum(s[0] for s in samples) / len(samples)
        r0 = sum(s[1] for s in samples) / len(samples)
        self.level = (round(p0, 2), round(r0, 2))
        self.leveled = True
        with open(LEVEL_FILE, "w") as f:
            json.dump({"pitch0": self.level[0], "roll0": self.level[1]}, f)
        print(f"saved level pose pitch0={self.level[0]} roll0={self.level[1]} -> {LEVEL_FILE}")
        return True

    def _sample(self):
        raw = self._bus.read_i2c_block_data(I2C_ADDR, _REG_ACCEL, 14)
        def s16(hi, lo):
            v = (hi << 8) | lo
            return v - 65536 if v > 32767 else v
        ax = s16(raw[0], raw[1]) / _ACCEL_LSB_PER_G
        ay = s16(raw[2], raw[3]) / _ACCEL_LSB_PER_G
        az = s16(raw[4], raw[5]) / _ACCEL_LSB_PER_G
        temp = s16(raw[6], raw[7]) / 340.0 + 36.53
        gx = s16(raw[8], raw[9]) / _GYRO_LSB_PER_DPS
        gy = s16(raw[10], raw[11]) / _GYRO_LSB_PER_DPS
        gz = s16(raw[12], raw[13]) / _GYRO_LSB_PER_DPS
        return (ax, ay, az), (gx, gy, gz), temp

    def _run(self):
        period = 1.0 / _SAMPLE_HZ
        while True:
            try:
                accel, gyro, temp = self._sample()
                pitch, roll = pitch_roll_from_accel(*accel)
                with self._lock:
                    self._accel, self._gyro, self._temp = accel, gyro, temp
                    if pitch is not None:
                        # EMA so wave slap doesn't jitter the attitude
                        self._pitch = pitch if self._pitch is None else \
                            self._pitch + (pitch - self._pitch) * _ATT_EMA
                        self._roll = roll if self._roll is None else \
                            self._roll + (roll - self._roll) * _ATT_EMA
                    self._last_ok = time.monotonic()
            except Exception:
                pass
            time.sleep(period)

    def read(self):
        if not self.hardware:
            return {"pitch": None, "roll": None, "leveled": self.leveled,
                    "accel_g": None, "gyro_dps": None, "temp_c": None, "valid": False}
        with self._lock:
            fresh = (time.monotonic() - self._last_ok) < 1.0
            pitch = round(self._pitch, 1) if (fresh and self._pitch is not None) else None
            roll = round(self._roll, 1) if (fresh and self._roll is not None) else None
            pitch, roll = apply_level(pitch, roll, self.level)
            return {
                "pitch": pitch,
                "roll": roll,
                "leveled": self.leveled,
                "accel_g": tuple(round(a, 3) for a in self._accel) if fresh and self._accel[0] is not None else None,
                "gyro_dps": tuple(round(g, 1) for g in self._gyro) if fresh and self._gyro[0] is not None else None,
                "temp_c": round(self._temp, 1) if (fresh and self._temp is not None) else None,
                "valid": fresh,
            }


if __name__ == "__main__":
    import sys

    imu = IMU()
    print("hardware:", imu.hardware, "addr:", hex(I2C_ADDR), "leveled:", imu.leveled)
    if len(sys.argv) > 1 and sys.argv[1] == "level":
        print("capturing level pose — keep the boat sitting level/still…")
        time.sleep(1.0)   # let the EMA settle
        imu.capture_level()
    else:
        for _ in range(20):
            r = imu.read()
            print(f"  pitch {r['pitch']}  roll {r['roll']}  temp {r['temp_c']}C  valid {r['valid']}")
            time.sleep(0.5)
    print("Done.")
