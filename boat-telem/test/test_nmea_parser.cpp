// NMEA parser unit tests — pure logic, no hardware (spec 8.3).
#include <cassert>
#include <cmath>
#include <cstdio>

#include "nmea.h"

static bool near(double a, double b, double eps = 1e-4) { return std::fabs(a - b) < eps; }

int main() {
    // checksum: valid, corrupted, malformed
    const std::string gga =
        "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47";
    assert(nmea::checksum_ok(gga));
    assert(!nmea::checksum_ok("$GPGGA,123519,4807.038,N*00"));
    assert(!nmea::checksum_ok("GPGGA,no,dollar"));
    assert(!nmea::checksum_ok(""));

    // GGA parse
    nmea::Gga g;
    assert(nmea::parse_gga(gga, g));
    assert(g.parsed && g.fixQuality == 1 && g.satellites == 8 && near(g.hdop, 0.9));

    // GGA with no fix: quality 0
    nmea::Gga g0;
    assert(nmea::parse_gga("$GPGGA,000000,,,,,0,00,,,M,,M,,*66", g0));
    assert(g0.fixQuality == 0 && g0.satellites == 0);

    // RMC valid: position/speed/course, incl. W longitude sign
    nmea::Rmc r;
    const std::string rmc =
        "$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A";
    assert(nmea::checksum_ok(rmc));
    assert(nmea::parse_rmc(rmc, r));
    assert(r.parsed && r.valid);
    assert(near(r.lat, 48.1173));            // 48 deg 07.038 min
    assert(near(r.lon, 11.5166667, 1e-4));   // 11 deg 31.000 min
    assert(near(r.speedKnots, 22.4));
    assert(near(r.courseDeg, 84.4));

    // Southern/western hemisphere sign
    assert(near(nmea::dm_to_deg("4807.038", "S"), -48.1173));
    assert(near(nmea::dm_to_deg("01131.000", "W"), -11.5166667, 1e-4));
    assert(std::isnan(nmea::dm_to_deg("", "N")));

    // RMC void: coordinates must be NAN, never 0.0 (the NO-FIX rule)
    nmea::Rmc rv;
    assert(nmea::parse_rmc("$GPRMC,000000,V,,,,,,,000000,,*31", rv));
    assert(rv.parsed && !rv.valid);
    assert(std::isnan(rv.lat) && std::isnan(rv.lon) && std::isnan(rv.speedKnots));

    // GN talker (u-blox M10 emits GNRMC/GNGGA)
    nmea::Gga gn;
    assert(nmea::parse_gga("$GNGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*59", gn));
    assert(gn.fixQuality == 1);

    // wrong sentence type is rejected, not misparsed
    nmea::Gga notg;
    assert(!nmea::parse_gga(rmc, notg));

    std::puts("nmea_parser: all assertions passed");
    return 0;
}
