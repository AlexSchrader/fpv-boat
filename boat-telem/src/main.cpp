// BOAT-TELEM v1.0 — real-time telemetry daemon (Phase 1: read, fuse, stream, log).
//
// Threads (spec 3.1): GPS UART reader, I2C pollers (magnetometer, INA219,
// MPU-6050), WebSocket accept loop — and this main thread as the 20 Hz fusion
// loop that assembles a TelemetrySnapshot, broadcasts it, and logs it.
//
// Env: TELEM_PORT (default 8765), TELEM_LOG_DIR (default $HOME),
//      TELEM_RATE_HZ (default 20), TELEM_GPS_PORT, BATTERY_I2C_ADDR,
//      COMPASS_I2C_ADDR, COMPASS_DECLINATION_DEG, IMU_I2C_ADDR.
//
// Run:  ./boat-telem          (Ctrl-C for clean shutdown)
// HUD/clients: connect a WebSocket to ws://<pi>:8765/ and receive one JSON
// snapshot per tick. Pure broadcast — no request protocol (spec Section 7).

#include <csignal>
#include <cstdio>

#include "drivers.h"
#include "logger.h"
#include "state.h"
#include "util.h"
#include "wsserver.h"

static std::atomic<bool> g_run{true};
static void on_signal(int) { g_run = false; }

int main() {
    std::signal(SIGINT, on_signal);
    std::signal(SIGTERM, on_signal);

    const int port = telem::env_int("TELEM_PORT", 8765);
    const int rate_hz = telem::env_int("TELEM_RATE_HZ", 20);
    const std::string log_dir = telem::env_str("TELEM_LOG_DIR", telem::home_path("").c_str());

    telem::SharedSensorState state;
    telem::I2cBus bus;

    telem::GpsDriver gps(state);
    telem::MagnetometerDriver mag(state, bus);
    telem::Ina219Driver power(state, bus);
    telem::AccelDriver imu(state, bus);
    gps.start();
    mag.start();
    power.start();
    imu.start();

    telem::WsServer server;
    if (!server.start(port)) {
        std::fprintf(stderr, "[telem] FATAL: cannot bind ws port %d\n", port);
        return 1;
    }

    telem::TelemetryLogger logger;
    const std::string session = util::iso8601_now();
    if (logger.open(log_dir, session)) {
        std::printf("[telem] logging to %s\n", logger.path().c_str());
    } else {
        std::fprintf(stderr, "[telem] WARNING: cannot open log file in %s — running without logging\n",
                     log_dir.c_str());
    }
    std::printf("[telem] up — ws://0.0.0.0:%d  fusion %d Hz\n", port, rate_hz);

    // Fixed-rate fusion loop. Stale sensors degrade their own block; nothing
    // here blocks on hardware (drivers own their I/O on their own threads).
    const auto period = std::chrono::milliseconds(1000 / (rate_hz > 0 ? rate_hz : 20));
    auto next = telem::Clock::now();
    int64_t ticks = 0;
    while (g_run) {
        next += period;
        const auto snap = telem::assemble(state, telem::now_ms(), util::iso8601_now());
        const std::string json = telem::to_json(snap);
        server.broadcast(json);
        logger.write(json);

        if (++ticks % (rate_hz * 10) == 0) {   // 10 s heartbeat, rate-limited (spec 3.3)
            std::printf("[telem] tick %lld  clients=%zu  gps%s mag%s pwr%s imu%s\n",
                        static_cast<long long>(ticks), server.client_count(),
                        snap.gpsStale ? "-" : "+", snap.magStale ? "-" : "+",
                        snap.powerStale ? "-" : "+", snap.imuStale ? "-" : "+");
            std::fflush(stdout);
        }
        std::this_thread::sleep_until(next);
    }

    std::printf("[telem] shutting down…\n");
    server.stop();
    gps.stop();
    mag.stop();
    power.stop();
    imu.stop();
    return 0;
}
