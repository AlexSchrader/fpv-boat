// INA219 register math + LiPo curve unit tests — pure logic (spec 8.3).
#include <cassert>
#include <cmath>
#include <cstdio>

#include "ina219_math.h"

static bool near(double a, double b, double eps = 1e-6) { return std::fabs(a - b) < eps; }

int main() {
    // bus voltage: value in bits 15..3, 4 mV LSB. 7.61 V -> raw counts 1902 (7.608 V)
    assert(near(ina219::bus_voltage_v(1902 << 3), 7.608));
    assert(near(ina219::bus_voltage_v(0), 0.0));

    // overflow flag is bit 0 and must not disturb the voltage math
    assert(ina219::math_overflow((1902 << 3) | 0x1));
    assert(!ina219::math_overflow(1902 << 3));
    assert(near(ina219::bus_voltage_v((1902 << 3) | 0x1), 7.608));

    // current: signed raw * 100 uA
    assert(near(ina219::current_a(12400), 1.24));
    assert(near(ina219::current_a(-500), -0.05));

    // calibration constants match the diagnostic brief
    assert(ina219::kCalibration == 4096);
    assert(ina219::kConfig == 0x199F);
    assert(ina219::kConfigAfterReset == 0x399F);

    // --- battery percent (2S, reserve floor 3.70 V/cell) ---
    assert(ina219::battery_percent(8.40) == 100);   // full
    assert(ina219::battery_percent(9.00) == 100);   // above full clamps
    assert(ina219::battery_percent(7.40) == 0);     // reserve floor = 0
    assert(ina219::battery_percent(6.00) == 0);     // below floor clamps
    assert(ina219::battery_percent(NAN) == -1);     // unknown
    assert(ina219::battery_percent(7.4, 0) == -1);  // bad cell count

    // monotone non-decreasing with voltage
    int last = -1;
    for (double v = 6.8; v <= 8.5; v += 0.02) {
        const int p = ina219::battery_percent(v);
        assert(p >= last);
        assert(p >= 0 && p <= 100);
        last = p;
    }

    // same per-cell voltage -> same percent on a different pack size
    assert(ina219::battery_percent(3.90 * 2, 2) == ina219::battery_percent(3.90 * 4, 4));

    // true-curve override: datasheet floor makes 3.70 V/cell read well above 0
    assert(ina219::battery_percent(7.40, 2, 3.27) > 5);

    std::puts("ina219_calc: all assertions passed");
    return 0;
}
