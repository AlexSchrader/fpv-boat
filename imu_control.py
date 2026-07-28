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

Config via env vars:
    IMU_I2C_ADDR   (default 0x68; 0x69 if the board's AD0 is tied high)

Bench test: `python3 imu_control.py` prints pitch/roll — tilt the board and
watch them follow.
"""

import math
import os
import threading
import time

I2C_ADDR = int(os.environ.get("IMU_I2C_ADDR", "0x68"), 0)

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
            return {"pitch": None, "roll": None, "accel_g": None,
                    "gyro_dps": None, "temp_c": None, "valid": False}
        with self._lock:
            fresh = (time.monotonic() - self._last_ok) < 1.0
            return {
                "pitch": round(self._pitch, 1) if (fresh and self._pitch is not None) else None,
                "roll": round(self._roll, 1) if (fresh and self._roll is not None) else None,
                "accel_g": tuple(round(a, 3) for a in self._accel) if fresh and self._accel[0] is not None else None,
                "gyro_dps": tuple(round(g, 1) for g in self._gyro) if fresh and self._gyro[0] is not None else None,
                "temp_c": round(self._temp, 1) if (fresh and self._temp is not None) else None,
                "valid": fresh,
            }


if __name__ == "__main__":
    imu = IMU()
    print("hardware:", imu.hardware, "addr:", hex(I2C_ADDR))
    for _ in range(20):
        r = imu.read()
        print(f"  pitch {r['pitch']}  roll {r['roll']}  temp {r['temp_c']}C  valid {r['valid']}")
        time.sleep(0.5)
    print("Done.")
