"""Unit tests for the IMU attitude math (MPU-6050 / GY-521).

Covers the pure accel->pitch/roll conversion and the no-sensor fallback; the
I2C path needs hardware and isn't exercised here.

Run: python -m unittest test_imu_control
"""

import unittest

from imu_control import pitch_roll_from_accel, apply_level, IMU


class TestPitchRoll(unittest.TestCase):
    def test_level(self):
        # gravity straight down the Z axis -> flat
        pitch, roll = pitch_roll_from_accel(0.0, 0.0, 1.0)
        self.assertEqual(pitch, 0.0)
        self.assertEqual(roll, 0.0)

    def test_nose_up_90(self):
        # gravity along -X -> pitch +90 (nose up), roll defined 0
        pitch, roll = pitch_roll_from_accel(-1.0, 0.0, 0.0)
        self.assertEqual(pitch, 90.0)

    def test_nose_down_90(self):
        pitch, _ = pitch_roll_from_accel(1.0, 0.0, 0.0)
        self.assertEqual(pitch, -90.0)

    def test_roll_right_90(self):
        # gravity along +Y -> rolled 90 to starboard
        _, roll = pitch_roll_from_accel(0.0, 1.0, 0.0)
        self.assertEqual(roll, 90.0)

    def test_45_degree_pitch(self):
        pitch, roll = pitch_roll_from_accel(-0.7071, 0.0, 0.7071)
        self.assertAlmostEqual(pitch, 45.0, places=1)
        self.assertEqual(roll, 0.0)

    def test_freefall_degenerate(self):
        pitch, roll = pitch_roll_from_accel(0.0, 0.0, 0.0)
        self.assertIsNone(pitch)
        self.assertIsNone(roll)

    def test_none_input(self):
        pitch, roll = pitch_roll_from_accel(None, 0.0, 1.0)
        self.assertIsNone(pitch)
        self.assertIsNone(roll)


class TestApplyLevel(unittest.TestCase):
    def test_mounting_pose_zeroes_out(self):
        # board mounted at pitch 17.3 / roll 96 (on its side): after level
        # capture, that exact pose must read as boat-level (0, 0)
        self.assertEqual(apply_level(17.3, 96.0, (17.3, 96.0)), (0.0, 0.0))

    def test_relative_motion_preserved(self):
        pitch, roll = apply_level(22.3, 96.0, (17.3, 96.0))
        self.assertEqual(pitch, 5.0)     # boat pitched 5 deg from its level pose
        self.assertEqual(roll, 0.0)

    def test_no_level_passthrough(self):
        self.assertEqual(apply_level(10.0, -3.0, None), (10.0, -3.0))

    def test_none_passthrough(self):
        self.assertEqual(apply_level(None, None, (1.0, 2.0)), (None, None))


class TestNoHardware(unittest.TestCase):
    def test_read_reports_invalid(self):
        imu = IMU()   # no smbus2/sensor in CI
        self.assertFalse(imu.hardware)
        r = imu.read()
        self.assertFalse(r["valid"])
        self.assertIsNone(r["pitch"])
        self.assertIsNone(r["roll"])


if __name__ == "__main__":
    unittest.main()
