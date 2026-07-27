"""Unit tests for the battery state-of-charge estimation (Track D / I.3).

Covers the pure voltage->percent mapping (with the reserve floor), the state
classifier, and the no-sensor fallback of BatteryMonitor. Runs in CI with no
hardware — importing battery_control never touches I2C.

Run: python -m unittest test_battery_control
"""

import unittest

from battery_control import (
    percent_from_voltage, state_from_percent, BatteryMonitor, EMPTY_V_PER_CELL,
)


class TestPercentFromVoltage(unittest.TestCase):
    def test_full_is_100(self):
        self.assertEqual(percent_from_voltage(8.40, cells=2), 100)

    def test_reserve_floor_is_zero(self):
        # 0% is the reserve floor (default 3.70 V/cell -> 7.40 V on 2S), NOT the
        # datasheet-empty voltage — hitting 0 must mean "come home", not stranded.
        self.assertEqual(percent_from_voltage(EMPTY_V_PER_CELL * 2, cells=2), 0)

    def test_below_floor_clamps_to_zero(self):
        self.assertEqual(percent_from_voltage(7.0, cells=2), 0)
        self.assertEqual(percent_from_voltage(6.0, cells=2), 0)

    def test_above_full_saturates(self):
        self.assertEqual(percent_from_voltage(9.0, cells=2), 100)

    def test_monotonic_non_decreasing_with_voltage(self):
        last = -1
        v = 7.0
        while v <= 8.45:
            p = percent_from_voltage(round(v, 3), cells=2)
            self.assertIsNotNone(p)
            self.assertGreaterEqual(p, last)   # never drops as voltage rises
            last = p
            v += 0.02

    def test_always_in_0_100(self):
        v = 5.0
        while v <= 9.0:
            p = percent_from_voltage(round(v, 3), cells=2)
            self.assertGreaterEqual(p, 0)
            self.assertLessEqual(p, 100)
            v += 0.1

    def test_cell_count_scales(self):
        # same per-cell voltage -> same percent regardless of pack size
        self.assertEqual(percent_from_voltage(3.90 * 2, cells=2),
                         percent_from_voltage(3.90 * 4, cells=4))

    def test_true_curve_floor_override(self):
        # with the datasheet floor, 3.70 V/cell reads well above 0
        p = percent_from_voltage(3.70 * 2, cells=2, empty_v_per_cell=3.27)
        self.assertGreater(p, 5)

    def test_bad_input_returns_none(self):
        self.assertIsNone(percent_from_voltage(None, cells=2))
        self.assertIsNone(percent_from_voltage(7.8, cells=0))
        self.assertIsNone(percent_from_voltage(7.8, cells=None))


class TestStateFromPercent(unittest.TestCase):
    def test_bands(self):
        self.assertEqual(state_from_percent(100, warn=30, crit=15), "ok")
        self.assertEqual(state_from_percent(31, warn=30, crit=15), "ok")
        self.assertEqual(state_from_percent(30, warn=30, crit=15), "warn")
        self.assertEqual(state_from_percent(16, warn=30, crit=15), "warn")
        self.assertEqual(state_from_percent(15, warn=30, crit=15), "critical")
        self.assertEqual(state_from_percent(0, warn=30, crit=15), "critical")

    def test_none_passthrough(self):
        self.assertIsNone(state_from_percent(None))


class TestBatteryMonitorSoftwareMode(unittest.TestCase):
    """Without the ina219 lib (CI), reads must report invalid and never raise."""

    def test_no_sensor_reads_invalid(self):
        batt = BatteryMonitor(cells=2)
        self.assertFalse(batt.hardware)
        r = batt.read()
        self.assertFalse(r["valid"])
        self.assertIsNone(r["voltage"])
        self.assertIsNone(r["percent"])
        self.assertIsNone(r["state"])
        self.assertFalse(r["amps_valid"])
        self.assertEqual(r["cells"], 2)

    def test_thresholds_exposed(self):
        batt = BatteryMonitor()
        self.assertIsInstance(batt.warn_pct, int)
        self.assertIsInstance(batt.crit_pct, int)
        self.assertGreater(batt.warn_pct, batt.crit_pct)


if __name__ == "__main__":
    unittest.main()
