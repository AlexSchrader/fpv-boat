// INA219 register math + the boat's 2S LiPo charge curve. Pure functions,
// unit-tested off-hardware.
//
// The register-level init sequence lives in drivers.h (Ina219Driver) — this
// header is the value math it produces. The init is explicit BY REQUIREMENT:
// the earlier Python readings were stuck at 0 V because the chip sat with
// MODE=ADC-off and calibration=0; power-on defaults are never trusted.
#pragma once

#include <cmath>
#include <cstdint>

namespace ina219 {

// Chosen operating point (matches the diagnostic brief):
//   Current_LSB = 100 uA, R_shunt = 0.1 ohm  ->  CAL = trunc(0.04096/(LSB*R)) = 4096
constexpr uint16_t kCalibration = 4096;
constexpr double kCurrentLsbAmps = 0.0001;
// Config 0x199F: 16 V bus range, PG /8 (+-320 mV), 12-bit bus + shunt ADC, continuous.
constexpr uint16_t kConfig = 0x199F;
constexpr uint16_t kResetCommand = 0x8000;
constexpr uint16_t kConfigAfterReset = 0x399F;   // expected readback right after reset

// Bus-voltage register: value in bits 15..3, LSB = 4 mV. Bit 1 = conversion
// ready, bit 0 = math overflow.
inline double bus_voltage_v(uint16_t raw) { return static_cast<double>(raw >> 3) * 0.004; }
inline bool math_overflow(uint16_t raw) { return raw & 0x1; }

// Current register is signed, scaled by Current_LSB.
inline double current_a(int16_t raw) { return raw * kCurrentLsbAmps; }

// --- 2S LiPo charge curve -----------------------------------------------
// Same table + reserve-floor semantics as the shipped Python
// battery_control.py: 0% is a RESERVE floor (default 3.70 V/cell = "come home
// now"), not the datasheet-empty 3.27 — a pack taken to true 0 on a boat is a
// swim. (The spec's rough "8.4->100 / 6.0->0" linear hint is deliberately
// replaced by this curve so C++ and Python gauges agree.)
struct CurvePoint { double v; double pct; };
constexpr CurvePoint kCurve[] = {
    {4.20, 100}, {4.15, 95}, {4.11, 90}, {4.08, 85}, {4.02, 80},
    {3.98, 75}, {3.95, 70}, {3.91, 65}, {3.87, 60}, {3.85, 55},
    {3.84, 50}, {3.82, 45}, {3.80, 40}, {3.79, 35}, {3.77, 30},
    {3.75, 25}, {3.73, 20}, {3.71, 15}, {3.69, 10}, {3.61, 5}, {3.27, 0},
};
constexpr int kCurveLen = sizeof(kCurve) / sizeof(kCurve[0]);

inline double curve_pct(double per_cell) {
    if (per_cell >= kCurve[0].v) return 100.0;
    if (per_cell <= kCurve[kCurveLen - 1].v) return 0.0;
    for (int i = 0; i + 1 < kCurveLen; i++) {
        const auto& hi = kCurve[i];
        const auto& lo = kCurve[i + 1];
        if (per_cell <= hi.v && per_cell >= lo.v) {
            const double frac = (per_cell - lo.v) / (hi.v - lo.v);
            return lo.pct + frac * (hi.pct - lo.pct);
        }
    }
    return 0.0;
}

// Pack voltage -> display percent, rescaled so the reserve floor reads 0.
inline int battery_percent(double pack_v, int cells = 2, double empty_per_cell = 3.70) {
    if (std::isnan(pack_v) || cells <= 0) return -1;   // -1 = unknown
    const double p_true = curve_pct(pack_v / cells);
    const double p_floor = curve_pct(empty_per_cell);
    if (p_floor >= 100.0) return 0;
    const double rescaled = 100.0 * (p_true - p_floor) / (100.0 - p_floor);
    const double clamped = rescaled < 0 ? 0 : (rescaled > 100 ? 100 : rescaled);
    return static_cast<int>(clamped + 0.5);
}

}  // namespace ina219
