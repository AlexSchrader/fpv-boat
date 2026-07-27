"""GPS telemetry from a u-blox M10 (M10-25Q) over UART, parsed as NMEA.

Wiring: GPS TX -> Pi pin 10 (GPIO15/RXD), GPS RX -> pin 8 (GPIO14/TXD),
VCC -> 3V3, GND common. The Pi's serial port must be enabled with the login
console OFF (raspi-config -> Interface -> Serial: console No, port Yes) —
setup.sh attempts this.

The serial read runs in its own daemon thread so NMEA parsing can never block
the video/control loops; the server just calls read() for the latest snapshot.

Validity rules (from the telemetry brief):
- GGA fix quality 0 or RMC status 'V' means the coordinates are MEANINGLESS —
  they are reported as None ("NO FIX"), never as 0.000000.
- Every update is timestamped; if no valid sentence arrives for STALE_S seconds
  the whole block reports valid=False so a frozen coordinate can't look live.

Same no-op-if-missing pattern as the other modules: without pyserial/pynmea2 or
the port, read() reports valid=False and nothing raises.

Config via env vars:
    GPS_PORT   serial device (default /dev/ttyAMA0)
    GPS_BAUD   baud rate (default 38400)

Bench test: `python3 gps_control.py` prints snapshots (take it near a window).
"""

import os
import threading
import time

GPS_PORT = os.environ.get("GPS_PORT", "/dev/ttyAMA0")
GPS_BAUD = int(os.environ.get("GPS_BAUD", "38400"))
STALE_S = 3.0

KNOTS_TO_MPH = 1.15078


def fix_from(gga_qual, rmc_status):
    """Classify fix: 'none' / 'gps' / 'dgps'. Either source can veto."""
    if rmc_status == "V":
        return "none"
    if gga_qual is None:
        return "none" if rmc_status != "A" else "gps"
    if gga_qual == 0:
        return "none"
    return "dgps" if gga_qual == 2 else "gps"


def knots_to_mph(knots):
    return None if knots is None else round(float(knots) * KNOTS_TO_MPH, 1)


class GPSReader:
    """Background NMEA reader; read() -> latest snapshot with validity."""

    def __init__(self, port=GPS_PORT, baud=GPS_BAUD):
        self._lock = threading.Lock()
        self._data = {"qual": None, "sats": None, "hdop": None,
                      "lat": None, "lon": None, "sog_kn": None, "cog": None,
                      "status": None}
        self._last_valid = 0.0
        self.hardware = False
        try:
            import serial      # pyserial
            import pynmea2     # noqa: F401 (verified importable for the thread)
            self._ser = serial.Serial(port, baud, timeout=1)
            self.hardware = True
            t = threading.Thread(target=self._run, daemon=True)
            t.start()
        except Exception as e:
            print(f"[gps] disabled ({e}); telemetry will report invalid")

    def _run(self):
        import pynmea2
        while True:
            try:
                line = self._ser.readline().decode("ascii", errors="replace").strip()
                if not line.startswith("$"):
                    continue
                msg = pynmea2.parse(line)
            except Exception:
                continue
            now = time.monotonic()
            typ = getattr(msg, "sentence_type", "")
            with self._lock:
                if typ == "GGA":
                    try:
                        self._data["qual"] = int(msg.gps_qual)
                    except Exception:
                        self._data["qual"] = None
                    try:
                        self._data["sats"] = int(msg.num_sats)
                    except Exception:
                        pass
                    try:
                        self._data["hdop"] = float(msg.horizontal_dil)
                    except Exception:
                        pass
                    self._last_valid = now
                elif typ == "RMC":
                    self._data["status"] = msg.status
                    if msg.status == "A":
                        try:
                            self._data["lat"] = round(msg.latitude, 6)
                            self._data["lon"] = round(msg.longitude, 6)
                        except Exception:
                            pass
                        try:
                            self._data["sog_kn"] = float(msg.spd_over_grnd) if msg.spd_over_grnd is not None else None
                        except Exception:
                            self._data["sog_kn"] = None
                        try:
                            self._data["cog"] = float(msg.true_course) if msg.true_course is not None else None
                        except Exception:
                            self._data["cog"] = None
                    self._last_valid = now

    def read(self):
        if not self.hardware:
            return {"fix": "none", "sats": None, "hdop": None, "lat": None,
                    "lon": None, "sog_mph": None, "cog": None,
                    "age_s": None, "valid": False}
        with self._lock:
            d = dict(self._data)
            age = time.monotonic() - self._last_valid if self._last_valid else None
        stale = age is None or age > STALE_S
        fix = fix_from(d["qual"], d["status"])
        has_fix = fix != "none" and not stale
        return {
            "fix": fix if not stale else "none",
            "sats": d["sats"],
            "hdop": d["hdop"],
            "lat": d["lat"] if has_fix else None,     # never render 0.000000
            "lon": d["lon"] if has_fix else None,
            "sog_mph": knots_to_mph(d["sog_kn"]) if has_fix else None,
            "cog": d["cog"] if has_fix else None,
            "age_s": round(age, 1) if age is not None else None,
            "valid": not stale,
        }


if __name__ == "__main__":
    gps = GPSReader()
    print("hardware:", gps.hardware, "port:", GPS_PORT, "@", GPS_BAUD)
    for _ in range(10):
        print(" ", gps.read())
        time.sleep(1)
    print("Done.")
