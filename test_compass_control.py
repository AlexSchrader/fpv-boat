"""Unit tests for the compass heading math (Track I.4).

Covers the pure heading computation, hard-iron correction, and the no-sensor
fallback. The I2C path needs the QMC5883P, so it isn't exercised here.

Run: python -m unittest test_compass_control
"""

import unittest

from compass_control import heading_from, apply_hard_iron, Compass


class TestHeadingMath(unittest.TestCase):
    def test_cardinal_directions_no_declination(self):
        # atan2(y, x): +x axis -> 0, +y axis -> 90, wrapping 0-360
        self.assertEqual(heading_from(1, 0, declination=0), 0.0)
        self.assertEqual(heading_from(0, 1, declination=0), 90.0)
        self.assertEqual(heading_from(-1, 0, declination=0), 180.0)
        self.assertEqual(heading_from(0, -1, declination=0), 270.0)

    def test_declination_shifts_and_wraps(self):
        self.assertEqual(heading_from(1, 0, declination=-9), 351.0)
        self.assertEqual(heading_from(0, -1, declination=100), 10.0)

    def test_always_0_360(self):
        for x in (-3, -1, 0, 1, 3):
            for y in (-3, -1, 1, 3):
                h = heading_from(x, y, declination=-9)
                self.assertGreaterEqual(h, 0)
                self.assertLess(h, 360)

    def test_degenerate_input_none(self):
        self.assertIsNone(heading_from(0, 0))
        self.assertIsNone(heading_from(None, 1))


class TestHardIron(unittest.TestCase):
    def test_offsets_subtracted(self):
        self.assertEqual(apply_hard_iron((100, -50, 30), (10, -10, 30)), (90, -40, 0))

    def test_zero_offsets_identity(self):
        self.assertEqual(apply_hard_iron((5, 6, 7), (0, 0, 0)), (5, 6, 7))


class TestNoHardware(unittest.TestCase):
    def test_read_reports_invalid(self):
        c = Compass()   # no smbus2/sensor in CI
        self.assertFalse(c.hardware)
        r = c.read()
        self.assertFalse(r["valid"])
        self.assertIsNone(r["heading"])


if __name__ == "__main__":
    unittest.main()
