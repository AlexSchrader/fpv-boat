// Fusion staleness + snapshot assembly tests — uses SharedSensorState directly
// with fake timestamps (the mock-driver strategy of spec 8.3).
#include <cassert>
#include <cmath>
#include <cstdio>
#include <string>

#include "state.h"

int main() {
    using namespace telem;

    // --- pure staleness rule ---
    assert(is_stale(1000, 0, 500));          // never updated -> stale
    assert(is_stale(1000, -5, 500));
    assert(!is_stale(1000, 900, 500));       // fresh
    assert(!is_stale(1000, 500, 500));       // exactly at max age -> still fresh
    assert(is_stale(1000, 499, 500));        // just past -> stale

    // --- assembly: everything stale on a cold start, but nothing crashes ---
    SharedSensorState s;
    Snapshot cold = assemble(s, now_ms(), "2026-08-02T00:00:00.000Z");
    assert(cold.gpsStale && cold.magStale && cold.powerStale && cold.imuStale);
    assert(std::isnan(cold.lat) && std::isnan(cold.lon));
    assert(cold.batteryPercent == -1);
    const std::string coldJson = to_json(cold);
    assert(coldJson.find("\"lat\":null") != std::string::npos);
    assert(coldJson.find("\"batteryPercent\":null") != std::string::npos);
    assert(coldJson.find("\"speed\":{\"value\":null,\"stale\":true}") != std::string::npos);

    // --- fresh GPS with a fix surfaces coordinates ---
    const int64_t now = now_ms();
    {
        std::lock_guard<std::mutex> lock(s.mtx);
        s.lat = 35.7796; s.lon = -78.6382; s.speedKnots = 4.2; s.courseDeg = 187.5;
        s.fixQuality = 1; s.satellites = 7; s.gpsUpdated = now;
        s.busVoltage = 7.61; s.currentA = 1.24; s.powerW = 9.44;
        s.currentValid = true; s.powerUpdated = now;
    }
    Snapshot live = assemble(s, now, "t");
    assert(!live.gpsStale && !live.powerStale);
    assert(std::fabs(live.lat - 35.7796) < 1e-9);
    assert(live.batteryPercent > 0 && live.batteryPercent <= 100);
    const std::string liveJson = to_json(live);
    assert(liveJson.find("\"lat\":35.7796") != std::string::npos);
    assert(liveJson.find("\"stale\":false") != std::string::npos);

    // --- fresh timestamp but NO FIX: coordinates must NOT surface ---
    {
        std::lock_guard<std::mutex> lock(s.mtx);
        s.fixQuality = 0;   // e.g. RMC 'V' vetoed the fix
    }
    Snapshot nofix = assemble(s, now, "t");
    assert(nofix.gpsStale);
    assert(std::isnan(nofix.lat) && std::isnan(nofix.lon));
    assert(to_json(nofix).find("\"lat\":null") != std::string::npos);

    // --- one sensor going stale never takes the others down (spec 3.3) ---
    {
        std::lock_guard<std::mutex> lock(s.mtx);
        s.fixQuality = 1;
        s.gpsUpdated = now - kGpsMaxAgeMs - 1;   // GPS aged out
    }
    Snapshot degraded = assemble(s, now, "t");
    assert(degraded.gpsStale);
    assert(!degraded.powerStale);                // power still reporting
    assert(degraded.batteryPercent > 0);

    // --- current overflow: voltage stays, current/power go null ---
    {
        std::lock_guard<std::mutex> lock(s.mtx);
        s.currentValid = false;
    }
    const std::string ovJson = to_json(assemble(s, now, "t"));
    assert(ovJson.find("\"busVoltage\":7.61") != std::string::npos);
    assert(ovJson.find("\"current\":null") != std::string::npos);
    assert(ovJson.find("\"power\":null") != std::string::npos);

    std::puts("fusion_staleness: all assertions passed");
    return 0;
}
