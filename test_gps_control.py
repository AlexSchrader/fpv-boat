"""Unit tests for the GPS helpers (Track J.7).

Covers the pure fix-classification and unit-conversion logic plus the no-port
fallback. The serial thread itself needs hardware, so it isn't exercised here —
importing gps_control never opens a port when pyserial is absent.

Run: python -m unittest test_gps_control
"""

import unittest

from gps_control import fix_from, knots_to_mph, GPSReader, KNOTS_TO_MPH


class TestFixClassification(unittest.TestCase):
    def test_no_fix_when_qual_zero(self):
        # GGA qual 0 means the coordinates are meaningless — must be 'none'
        self.assertEqual(fix_from(0, "A"), "none")
        self.assertEqual(fix_from(0, None), "none")

    def test_void_rmc_vetoes(self):
        # RMC status V vetoes even a claimed GGA fix
        self.assertEqual(fix_from(1, "V"), "none")
        self.assertEqual(fix_from(2, "V"), "none")

    def test_gps_and_dgps(self):
        self.assertEqual(fix_from(1, "A"), "gps")
        self.assertEqual(fix_from(2, "A"), "dgps")

    def test_rmc_only(self):
        self.assertEqual(fix_from(None, "A"), "gps")
        self.assertEqual(fix_from(None, None), "none")


class TestKnotsToMph(unittest.TestCase):
    def test_conversion(self):
        self.assertEqual(knots_to_mph(10), round(10 * KNOTS_TO_MPH, 1))
        self.assertEqual(knots_to_mph(0), 0.0)

    def test_none_passthrough(self):
        self.assertIsNone(knots_to_mph(None))


class TestNoHardware(unittest.TestCase):
    def test_read_reports_invalid_never_zero_coords(self):
        gps = GPSReader(port="/dev/definitely-not-a-port")
        self.assertFalse(gps.hardware)
        r = gps.read()
        self.assertFalse(r["valid"])
        self.assertEqual(r["fix"], "none")
        # NO FIX must never masquerade as coordinates
        self.assertIsNone(r["lat"])
        self.assertIsNone(r["lon"])
        self.assertIsNone(r["sog_mph"])


if __name__ == "__main__":
    unittest.main()
