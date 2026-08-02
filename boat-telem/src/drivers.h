// Sensor drivers — one thread per physical interface (spec 3.1). Each owns its
// hardware handle exclusively and writes into SharedSensorState. All degrade
// gracefully: a missing/failed device logs once and keeps retrying at a slow
// cadence; nothing here can crash the daemon (spec 1.4).
//
// As-built ground truth (differs from the spec's hardware table):
//   - Magnetometer is a QMC5883P at 0x2C — NOT the QMC5883L at 0x0D that
//     libraries target, and not inside the M10 GPS. Chip-ID verified at init.
//     Hard-iron offsets are read from ~/.fpv-boat-compass.json so the C++ and
//     Python stacks share ONE calibration.
//   - INA219 at 0x40 with the full register-level init (reset -> verify ->
//     calibration -> config): the historical "stuck at 0 V" was a chip left
//     with MODE=ADC-off + cal=0. Never trust power-on defaults. Current is
//     flagged invalid on math-overflow instead of reported wrong.
//   - The accelerometer is NOT a stub: an MPU-6050 (GY-521) is wired at 0x68.
//     It supplies pitch/roll; accel-derived *speed* remains future fusion.
//
// I2C bus sharing: all I2C drivers in this process go through I2cBus, which
// serializes access with one mutex (spec 2 note). Cross-process (the Python
// pan/tilt on the same bus) is safe per-transaction at the kernel level.
#pragma once

#include <fcntl.h>
#include <termios.h>
#include <unistd.h>
#include <sys/ioctl.h>
#include <linux/i2c-dev.h>

#include <atomic>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <mutex>
#include <string>
#include <thread>

#include "nmea.h"
#include "ina219_math.h"
#include "state.h"

namespace telem {

inline double env_double(const char* name, double dflt) {
    const char* v = std::getenv(name);
    return v ? std::atof(v) : dflt;
}
inline int env_int(const char* name, int dflt) {
    const char* v = std::getenv(name);
    return v ? static_cast<int>(std::strtol(v, nullptr, 0)) : dflt;
}
inline std::string env_str(const char* name, const char* dflt) {
    const char* v = std::getenv(name);
    return v ? v : dflt;
}
inline std::string home_path(const char* rel) {
    const char* h = std::getenv("HOME");
    return std::string(h ? h : "/root") + "/" + rel;
}

// ---------------------------------------------------------------- I2C bus ---
class I2cBus {
public:
    bool open_bus(const char* dev = "/dev/i2c-1") {
        std::lock_guard<std::mutex> lock(mtx_);
        if (fd_ >= 0) return true;
        fd_ = ::open(dev, O_RDWR);
        return fd_ >= 0;
    }
    // 16-bit big-endian register ops (INA219/QMC style). Returns false on any
    // I/O failure so callers can mark themselves stale.
    bool write_reg8(int addr, uint8_t reg, uint8_t val) {
        std::lock_guard<std::mutex> lock(mtx_);
        uint8_t buf[2] = {reg, val};
        return select(addr) && ::write(fd_, buf, 2) == 2;
    }
    bool write_reg16be(int addr, uint8_t reg, uint16_t val) {
        std::lock_guard<std::mutex> lock(mtx_);
        uint8_t buf[3] = {reg, static_cast<uint8_t>(val >> 8), static_cast<uint8_t>(val & 0xFF)};
        return select(addr) && ::write(fd_, buf, 3) == 3;
    }
    bool read_reg16be(int addr, uint8_t reg, uint16_t& out) {
        std::lock_guard<std::mutex> lock(mtx_);
        uint8_t buf[2];
        if (!select(addr) || ::write(fd_, &reg, 1) != 1 || ::read(fd_, buf, 2) != 2) return false;
        out = static_cast<uint16_t>((buf[0] << 8) | buf[1]);
        return true;
    }
    bool read_block(int addr, uint8_t reg, uint8_t* out, size_t n) {
        std::lock_guard<std::mutex> lock(mtx_);
        if (!select(addr) || ::write(fd_, &reg, 1) != 1) return false;
        return ::read(fd_, out, n) == static_cast<ssize_t>(n);
    }

private:
    bool select(int addr) { return fd_ >= 0 && ioctl(fd_, I2C_SLAVE, addr) >= 0; }
    int fd_ = -1;
    std::mutex mtx_;
};

// ------------------------------------------------------------- base driver ---
class ThreadedDriver {
public:
    virtual ~ThreadedDriver() { stop(); }
    void start() {
        running_ = true;
        thread_ = std::thread([this] { run(); });
    }
    void stop() {
        running_ = false;
        if (thread_.joinable()) thread_.join();
    }

protected:
    virtual void run() = 0;
    std::atomic<bool> running_{false};

private:
    std::thread thread_;
};

// ------------------------------------------------------------------- GPS ----
class GpsDriver : public ThreadedDriver {
public:
    GpsDriver(SharedSensorState& s) : s_(s) {}

protected:
    void run() override {
        const std::string port = env_str("TELEM_GPS_PORT", "/dev/ttyAMA0");
        std::string line;
        while (running_) {
            if (fd_ < 0 && !open_port(port)) {
                warn_once("[gps] cannot open %s — retrying\n", port.c_str());
                sleep_s(5);
                continue;
            }
            char c;
            const ssize_t n = ::read(fd_, &c, 1);
            if (n <= 0) { std::this_thread::sleep_for(std::chrono::milliseconds(50)); continue; }
            if (c == '\n') {
                handle(line);
                line.clear();
            } else if (c != '\r' && line.size() < 120) {
                line.push_back(c);
            }
        }
        if (fd_ >= 0) ::close(fd_);
    }

private:
    bool open_port(const std::string& port) {
        fd_ = ::open(port.c_str(), O_RDONLY | O_NOCTTY);
        if (fd_ < 0) return false;
        termios tio{};
        tcgetattr(fd_, &tio);
        cfmakeraw(&tio);
        cfsetispeed(&tio, B38400);
        cfsetospeed(&tio, B38400);
        tio.c_cc[VMIN] = 0;
        tio.c_cc[VTIME] = 5;   // 0.5 s read timeout so the loop can exit
        tcsetattr(fd_, TCSANOW, &tio);
        return true;
    }
    void handle(const std::string& line) {
        nmea::Gga gga;
        nmea::Rmc rmc;
        if (nmea::parse_gga(line, gga)) {
            std::lock_guard<std::mutex> lock(s_.mtx);
            s_.fixQuality = gga.fixQuality;
            s_.satellites = gga.satellites;
            s_.hdop = gga.hdop;
            s_.gpsUpdated = now_ms();
        } else if (nmea::parse_rmc(line, rmc)) {
            std::lock_guard<std::mutex> lock(s_.mtx);
            if (rmc.valid) {
                s_.lat = rmc.lat; s_.lon = rmc.lon;
                s_.speedKnots = rmc.speedKnots; s_.courseDeg = rmc.courseDeg;
            } else {
                s_.fixQuality = 0;   // RMC 'V' vetoes the fix
            }
            s_.gpsUpdated = now_ms();
        }
    }
    void warn_once(const char* fmt, const char* arg) {
        if (!warned_) { std::fprintf(stderr, fmt, arg); warned_ = true; }
    }
    void sleep_s(int s) {
        for (int i = 0; i < s * 10 && running_; i++)
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }
    SharedSensorState& s_;
    int fd_ = -1;
    bool warned_ = false;
};

// ---------------------------------------------------- QMC5883P magnetometer --
class MagnetometerDriver : public ThreadedDriver {
public:
    MagnetometerDriver(SharedSensorState& s, I2cBus& bus) : s_(s), bus_(bus) {
        addr_ = env_int("COMPASS_I2C_ADDR", 0x2C);
        declination_ = env_double("COMPASS_DECLINATION_DEG", -9.0);
        load_calibration();
    }

protected:
    void run() override {
        while (running_) {
            if (!inited_ && !init_chip()) { sleep_retry(); continue; }
            uint8_t d[6];
            if (!bus_.read_block(addr_, 0x01, d, 6)) { inited_ = false; continue; }
            auto s16 = [](uint8_t lo, uint8_t hi) {
                int v = lo | (hi << 8);
                return v > 32767 ? v - 65536 : v;
            };
            // Heading is TILT-NAIVE here (raw X/Y): the tilt-compensated HDG
            // lives in the Python path with IMU fusion; this stream reports the
            // magnetometer's own view, flagged by mounting calibration only.
            const double x = s16(d[0], d[1]) - off_[0];
            const double y = s16(d[2], d[3]) - off_[1];
            double heading = NAN;
            if (x != 0 || y != 0) {
                heading = std::atan2(y, x) * 180.0 / M_PI + declination_;
                heading = std::fmod(heading + 360.0, 360.0);
            }
            {
                std::lock_guard<std::mutex> lock(s_.mtx);
                s_.magHeadingDeg = heading;
                s_.magCalibrated = calibrated_;
                s_.magUpdated = now_ms();
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
        }
    }

private:
    bool init_chip() {
        if (!bus_.open_bus()) return false;
        uint16_t id16;
        if (!bus_.read_reg16be(addr_, 0x00, id16)) return false;
        if ((id16 >> 8) != 0x80) {   // first byte = chip ID register
            warn_once("[mag] chip at 0x%02X is not a QMC5883P\n", addr_);
            return false;
        }
        if (!bus_.write_reg8(addr_, 0x0B, 0x08)) return false;   // set/reset on
        if (!bus_.write_reg8(addr_, 0x0A, 0xCD)) return false;   // continuous, 200 Hz
        inited_ = true;
        return true;
    }
    void load_calibration() {
        // shares ~/.fpv-boat-compass.json with the Python stack: {"offsets": [x, y, z]}
        std::ifstream f(home_path(".fpv-boat-compass.json"));
        if (!f) return;
        std::string txt((std::istreambuf_iterator<char>(f)), std::istreambuf_iterator<char>());
        const size_t br = txt.find('[');
        if (br == std::string::npos) return;
        if (std::sscanf(txt.c_str() + br, "[%lf , %lf , %lf", &off_[0], &off_[1], &off_[2]) == 3)
            calibrated_ = true;
    }
    void warn_once(const char* fmt, int arg) {
        if (!warned_) { std::fprintf(stderr, fmt, arg); warned_ = true; }
    }
    void sleep_retry() {
        for (int i = 0; i < 50 && running_; i++)
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }
    SharedSensorState& s_;
    I2cBus& bus_;
    int addr_;
    double declination_;
    double off_[3] = {0, 0, 0};
    bool calibrated_ = false;
    bool inited_ = false;
    bool warned_ = false;
};

// ------------------------------------------------------------------ INA219 ---
class Ina219Driver : public ThreadedDriver {
public:
    Ina219Driver(SharedSensorState& s, I2cBus& bus) : s_(s), bus_(bus) {
        addr_ = env_int("BATTERY_I2C_ADDR", 0x40);
    }

protected:
    void run() override {
        while (running_) {
            if (!inited_ && !init_chip()) { sleep_retry(); continue; }
            uint16_t busRaw;
            if (!bus_.read_reg16be(addr_, 0x02, busRaw)) { inited_ = false; continue; }
            const double volts = ina219::bus_voltage_v(busRaw);
            const bool overflow = ina219::math_overflow(busRaw);
            uint16_t curRaw = 0;
            const bool haveCur = bus_.read_reg16be(addr_, 0x04, curRaw);
            const double amps = ina219::current_a(static_cast<int16_t>(curRaw));
            {
                std::lock_guard<std::mutex> lock(s_.mtx);
                // A dead/misconfigured chip reads ~0 V; never dress that up as data.
                if (volts > 0.1) {
                    s_.busVoltage = volts;
                    s_.currentValid = haveCur && !overflow;
                    s_.currentA = s_.currentValid ? amps : NAN;
                    s_.powerW = s_.currentValid ? volts * amps : NAN;
                    s_.powerUpdated = now_ms();
                }
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(200));
        }
    }

private:
    bool init_chip() {
        // The required explicit sequence (spec 4.4): reset, VERIFY the chip is
        // real via the known post-reset config, then calibration + config.
        if (!bus_.open_bus()) return false;
        if (!bus_.write_reg16be(addr_, 0x00, ina219::kResetCommand)) return false;
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
        uint16_t cfg;
        if (!bus_.read_reg16be(addr_, 0x00, cfg) || cfg != ina219::kConfigAfterReset) {
            warn_once("[ina219] no INA219 at 0x%02X (bad post-reset config)\n", addr_);
            return false;
        }
        if (!bus_.write_reg16be(addr_, 0x05, ina219::kCalibration)) return false;
        if (!bus_.write_reg16be(addr_, 0x00, ina219::kConfig)) return false;
        inited_ = true;
        return true;
    }
    void warn_once(const char* fmt, int arg) {
        if (!warned_) { std::fprintf(stderr, fmt, arg); warned_ = true; }
    }
    void sleep_retry() {
        for (int i = 0; i < 50 && running_; i++)
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }
    SharedSensorState& s_;
    I2cBus& bus_;
    int addr_;
    bool inited_ = false;
    bool warned_ = false;
};

// ------------------------------------------------------- MPU-6050 (GY-521) ---
class AccelDriver : public ThreadedDriver {
public:
    AccelDriver(SharedSensorState& s, I2cBus& bus) : s_(s), bus_(bus) {
        addr_ = env_int("IMU_I2C_ADDR", 0x68);
        load_level();
    }

protected:
    void run() override {
        while (running_) {
            if (!inited_ && !init_chip()) { sleep_retry(); continue; }
            uint8_t d[6];
            if (!bus_.read_block(addr_, 0x3B, d, 6)) { inited_ = false; continue; }
            auto s16 = [](uint8_t hi, uint8_t lo) {
                int v = (hi << 8) | lo;
                return v > 32767 ? v - 65536 : v;
            };
            const double ax = s16(d[0], d[1]) / 16384.0;
            const double ay = s16(d[2], d[3]) / 16384.0;
            const double az = s16(d[4], d[5]) / 16384.0;
            const double mag = std::sqrt(ax * ax + ay * ay + az * az);
            if (mag > 1e-6) {
                const double pitch = std::atan2(-ax, std::sqrt(ay * ay + az * az)) * 180.0 / M_PI;
                const double roll = std::atan2(ay, az) * 180.0 / M_PI;
                std::lock_guard<std::mutex> lock(s_.mtx);
                // EMA + the same level-pose offsets the Python stack captured
                const double p = pitch - level_[0], r = roll - level_[1];
                s_.pitchDeg = std::isnan(s_.pitchDeg) ? p : s_.pitchDeg + (p - s_.pitchDeg) * 0.15;
                s_.rollDeg = std::isnan(s_.rollDeg) ? r : s_.rollDeg + (r - s_.rollDeg) * 0.15;
                s_.imuUpdated = now_ms();
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(20));
        }
    }

private:
    bool init_chip() {
        if (!bus_.open_bus()) return false;
        uint16_t who16;
        if (!bus_.read_reg16be(addr_, 0x75, who16)) return false;
        if ((who16 >> 8) != 0x68) {
            warn_once("[imu] chip at 0x%02X is not an MPU-6050\n", addr_);
            return false;
        }
        // the chip boots ASLEEP — clear PWR_MGMT_1 or every register reads 0
        if (!bus_.write_reg8(addr_, 0x6B, 0x00)) return false;
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
        inited_ = true;
        return true;
    }
    void load_level() {
        // shares ~/.fpv-boat-imu.json with Python: {"pitch0": x, "roll0": y}
        std::ifstream f(home_path(".fpv-boat-imu.json"));
        if (!f) return;
        std::string txt((std::istreambuf_iterator<char>(f)), std::istreambuf_iterator<char>());
        double p0, r0;
        const size_t pp = txt.find("pitch0");
        const size_t rp = txt.find("roll0");
        if (pp != std::string::npos && rp != std::string::npos &&
            std::sscanf(txt.c_str() + pp, "pitch0%*[\": ]%lf", &p0) == 1 &&
            std::sscanf(txt.c_str() + rp, "roll0%*[\": ]%lf", &r0) == 1) {
            level_[0] = p0;
            level_[1] = r0;
        }
    }
    void warn_once(const char* fmt, int arg) {
        if (!warned_) { std::fprintf(stderr, fmt, arg); warned_ = true; }
    }
    void sleep_retry() {
        for (int i = 0; i < 50 && running_; i++)
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }
    SharedSensorState& s_;
    I2cBus& bus_;
    int addr_;
    double level_[2] = {0, 0};
    bool inited_ = false;
    bool warned_ = false;
};

}  // namespace telem
