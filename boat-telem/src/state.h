// SharedSensorState — the mutex-guarded struct every sensor thread writes into
// and the 20 Hz fusion loop reads from — plus the TelemetrySnapshot assembly
// and its JSON wire format. Deliberately plain mutexes over lock-free
// cleverness (spec 3.1): a Zero 2 W runs this fine and it stays debuggable.
#pragma once

#include <chrono>
#include <cmath>
#include <mutex>
#include <sstream>
#include <string>

#include "ina219_math.h"

namespace telem {

using Clock = std::chrono::steady_clock;
inline int64_t now_ms() {
    return std::chrono::duration_cast<std::chrono::milliseconds>(
               Clock::now().time_since_epoch()).count();
}

// Per-sensor max-age thresholds (spec 3.3 / 4.x).
constexpr int64_t kGpsMaxAgeMs = 2000;
constexpr int64_t kMagMaxAgeMs = 200;
constexpr int64_t kPowerMaxAgeMs = 500;
constexpr int64_t kImuMaxAgeMs = 500;

// Pure staleness rule (unit-tested): never updated (last==0) is always stale.
inline bool is_stale(int64_t now, int64_t last_update, int64_t max_age) {
    if (last_update <= 0) return true;
    return (now - last_update) > max_age;
}

struct SharedSensorState {
    std::mutex mtx;

    // GPS (UART thread)
    double lat = NAN, lon = NAN, speedKnots = NAN, courseDeg = NAN, hdop = NAN;
    int fixQuality = 0, satellites = 0;
    int64_t gpsUpdated = 0;

    // Magnetometer (I2C thread) — tilt-naive heading (see MagnetometerDriver)
    double magHeadingDeg = NAN;
    bool magCalibrated = false;
    int64_t magUpdated = 0;

    // INA219 (I2C thread)
    double busVoltage = NAN, currentA = NAN, powerW = NAN;
    bool currentValid = false;   // false past shunt range / on math overflow
    int64_t powerUpdated = 0;

    // MPU-6050 (I2C thread) — as-built hardware, not the spec's stub
    double pitchDeg = NAN, rollDeg = NAN;
    int64_t imuUpdated = 0;
};

// One coherent, timestamped view — the wire/log format of Section 5, extended
// with the as-built IMU block. "speed" stays a stale placeholder until the
// accel/GPS fusion phase lands (spec 4.5 intent).
struct Snapshot {
    std::string timestampIso;
    // gps
    bool gpsStale = true;
    double lat = NAN, lon = NAN, speedKnots = NAN, headingDeg = NAN, hdop = NAN;
    int fixQuality = 0, satellites = 0;
    // compass
    bool magStale = true;
    double magHeadingDeg = NAN;
    bool magCalibrated = false;
    // power
    bool powerStale = true;
    double busVoltage = NAN, currentA = NAN, powerW = NAN;
    bool currentValid = false;
    int batteryPercent = -1;
    // imu
    bool imuStale = true;
    double pitchDeg = NAN, rollDeg = NAN;
};

inline Snapshot assemble(SharedSensorState& s, int64_t now, std::string timestamp) {
    std::lock_guard<std::mutex> lock(s.mtx);
    Snapshot o;
    o.timestampIso = std::move(timestamp);

    o.gpsStale = is_stale(now, s.gpsUpdated, kGpsMaxAgeMs) || s.fixQuality == 0;
    o.fixQuality = s.fixQuality;
    o.satellites = s.satellites;
    o.hdop = s.hdop;
    if (!o.gpsStale) {   // no-fix coordinates never surface (not even as stale values)
        o.lat = s.lat; o.lon = s.lon;
        o.speedKnots = s.speedKnots; o.headingDeg = s.courseDeg;
    }

    o.magStale = is_stale(now, s.magUpdated, kMagMaxAgeMs);
    o.magHeadingDeg = s.magHeadingDeg;
    o.magCalibrated = s.magCalibrated;

    o.powerStale = is_stale(now, s.powerUpdated, kPowerMaxAgeMs);
    o.busVoltage = s.busVoltage;
    o.currentA = s.currentA;
    o.powerW = s.powerW;
    o.currentValid = s.currentValid;
    o.batteryPercent = o.powerStale ? -1 : ina219::battery_percent(s.busVoltage);

    o.imuStale = is_stale(now, s.imuUpdated, kImuMaxAgeMs);
    o.pitchDeg = s.pitchDeg;
    o.rollDeg = s.rollDeg;
    return o;
}

// --- JSON serialization (emit-only; hand-rolled to keep zero dependencies) ---
inline void jnum(std::ostringstream& o, const char* key, double v, int prec = 6) {
    o << '"' << key << "\":";
    if (std::isnan(v)) { o << "null"; return; }
    o.precision(prec);
    o << std::fixed << v;
    o.unsetf(std::ios_base::floatfield);
}

inline std::string to_json(const Snapshot& s) {
    std::ostringstream o;
    o << "{\"timestamp\":\"" << s.timestampIso << "\",";

    o << "\"gps\":{";
    jnum(o, "lat", s.lat, 6); o << ',';
    jnum(o, "lon", s.lon, 6); o << ',';
    jnum(o, "speedKnots", s.speedKnots, 2); o << ',';
    jnum(o, "headingDeg", s.headingDeg, 1); o << ',';
    jnum(o, "hdop", s.hdop, 1); o << ',';
    o << "\"fixQuality\":" << s.fixQuality << ",\"satellites\":" << s.satellites
      << ",\"stale\":" << (s.gpsStale ? "true" : "false") << "},";

    o << "\"compass\":{";
    jnum(o, "headingDeg", s.magHeadingDeg, 1); o << ',';
    o << "\"calibrated\":" << (s.magCalibrated ? "true" : "false")
      << ",\"stale\":" << (s.magStale ? "true" : "false") << "},";

    o << "\"power\":{";
    jnum(o, "busVoltage", s.busVoltage, 2); o << ',';
    jnum(o, "current", s.currentValid ? s.currentA : NAN, 3); o << ',';
    jnum(o, "power", s.currentValid ? s.powerW : NAN, 2); o << ',';
    o << "\"batteryPercent\":";
    if (s.batteryPercent < 0) o << "null"; else o << s.batteryPercent;
    o << ",\"stale\":" << (s.powerStale ? "true" : "false") << "},";

    o << "\"imu\":{";
    jnum(o, "pitchDeg", s.imuStale ? NAN : s.pitchDeg, 1); o << ',';
    jnum(o, "rollDeg", s.imuStale ? NAN : s.rollDeg, 1); o << ',';
    o << "\"stale\":" << (s.imuStale ? "true" : "false") << "},";

    // speed: accel-derived source is Phase-2 fusion; a permanent-stale
    // placeholder keeps the wire format forward-compatible (spec 4.5).
    o << "\"speed\":{\"value\":null,\"stale\":true}}";
    return o.str();
}

}  // namespace telem
