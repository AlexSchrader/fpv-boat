// Minimal NMEA-0183 parser — only the sentence types the boat actually uses
// (GGA: fix/sats/hdop, RMC: validity/position/speed/course), per the spec's
// "no heavy dependency" requirement. Pure functions, unit-tested off-hardware.
//
// Validity rules match the shipped Python gps_control.py: GGA quality 0 or RMC
// status 'V' means the coordinates are MEANINGLESS and must never surface as
// 0.000000.
#pragma once

#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <string>
#include <vector>

namespace nmea {

struct Gga {
    bool parsed = false;
    int fixQuality = 0;   // 0 = no fix, 1 = GPS, 2 = DGPS
    int satellites = 0;
    double hdop = NAN;
};

struct Rmc {
    bool parsed = false;
    bool valid = false;   // status 'A'
    double lat = NAN, lon = NAN;
    double speedKnots = NAN;
    double courseDeg = NAN;
};

// "$GPGGA,...*47" -> checksum over chars between '$' and '*' must equal hex tail.
inline bool checksum_ok(const std::string& line) {
    const size_t star = line.rfind('*');
    if (line.empty() || line[0] != '$' || star == std::string::npos || star + 3 > line.size())
        return false;
    uint8_t cs = 0;
    for (size_t i = 1; i < star; i++) cs ^= static_cast<uint8_t>(line[i]);
    return cs == static_cast<uint8_t>(std::strtol(line.c_str() + star + 1, nullptr, 16));
}

inline std::vector<std::string> fields(const std::string& line) {
    std::vector<std::string> out;
    const size_t star = line.rfind('*');
    const std::string body = line.substr(0, star == std::string::npos ? line.size() : star);
    size_t start = 0;
    for (size_t i = 0; i <= body.size(); i++) {
        if (i == body.size() || body[i] == ',') {
            out.push_back(body.substr(start, i - start));
            start = i + 1;
        }
    }
    return out;
}

// NMEA "ddmm.mmmm" (+ hemisphere) -> signed decimal degrees. NAN on empty.
inline double dm_to_deg(const std::string& dm, const std::string& hemi) {
    if (dm.empty() || hemi.empty()) return NAN;
    const double v = std::atof(dm.c_str());
    const double deg = std::floor(v / 100.0);
    const double min = v - deg * 100.0;
    double out = deg + min / 60.0;
    if (hemi == "S" || hemi == "W") out = -out;
    return out;
}

// Accepts any talker (GP/GN/GL...): matches "...GGA". Requires valid checksum.
inline bool parse_gga(const std::string& line, Gga& out) {
    if (!checksum_ok(line)) return false;
    const auto f = fields(line);
    if (f.empty() || f[0].size() < 6 || f[0].compare(f[0].size() - 3, 3, "GGA") != 0) return false;
    if (f.size() < 9) return false;
    out.parsed = true;
    out.fixQuality = f[6].empty() ? 0 : std::atoi(f[6].c_str());
    out.satellites = f[7].empty() ? 0 : std::atoi(f[7].c_str());
    out.hdop = f[8].empty() ? NAN : std::atof(f[8].c_str());
    return true;
}

inline bool parse_rmc(const std::string& line, Rmc& out) {
    if (!checksum_ok(line)) return false;
    const auto f = fields(line);
    if (f.empty() || f[0].size() < 6 || f[0].compare(f[0].size() - 3, 3, "RMC") != 0) return false;
    if (f.size() < 9) return false;
    out.parsed = true;
    out.valid = (f[2] == "A");
    if (out.valid) {
        out.lat = dm_to_deg(f[3], f[4]);
        out.lon = dm_to_deg(f[5], f[6]);
        out.speedKnots = f[7].empty() ? NAN : std::atof(f[7].c_str());
        out.courseDeg = f[8].empty() ? NAN : std::atof(f[8].c_str());
    } else {
        // void fix: coordinates are meaningless — leave them NAN, never 0.0
        out.lat = out.lon = out.speedKnots = out.courseDeg = NAN;
    }
    return true;
}

}  // namespace nmea
