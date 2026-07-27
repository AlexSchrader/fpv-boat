"""LiPo battery telemetry via an INA219 (voltage + current over I2C).

The Pi has no native ADC, so pack voltage/current come from an INA219 breakout
on the battery line (I2C: SDA=GPIO2/pin3, SCL=GPIO3/pin5, plus 3V3 + GND). This
module reads bus voltage and current and estimates state-of-charge from a LiPo
discharge curve.

Same self-contained, no-op-if-missing pattern as motor_control.py /
lights_control.py: importing this never breaks the server. Without the `ina219`
library or the sensor wired, `read()` reports valid=False and the HUD shows
`--%` — nothing raises.

Design notes (from the telemetry brief):
- The INA219 is explicitly (re)configured on every process start — the chip does
  not reliably retain settings, and a chip left with MODE=ADC-off reads 0 V
  forever. 16 V range (a 2S pack tops out at 8.4 V), 12-bit ADCs, continuous.
- Voltage is EMA-smoothed BEFORE the curve lookup, and the displayed percent is
  monotonically non-increasing during a run (a climbing gauge reads as a bug),
  resetting only if the voltage jumps up >0.25 V (pack swap / charger).
- "Empty" is a RESERVE floor, not the datasheet floor: 0% = 3.70 V/cell by
  default so hitting 0 means "come home now", not "stranded". Tune with
  BATTERY_EMPTY_V_PER_CELL (set 3.27 for the true curve).
- Sag compensation: under load the pack reads low; if current is trusted we
  estimate the resting voltage as V + I*R_internal (BATTERY_R_INT_OHMS, ~0.04
  for 2S; calibrate on the bench by comparing idle vs loaded readings).
- The stock 0.1 ohm shunt saturates at ~3.2 A — motors exceed that. Voltage
  stays valid regardless; current is flagged amps_valid=False on overflow
  instead of displaying a confidently wrong number.

Config via env vars (all optional):
    BATTERY_CELLS            LiPo cells in series (default 2 -> 2S)
    BATTERY_SHUNT_OHMS       INA219 shunt resistance (default 0.1)
    BATTERY_MAX_AMPS         expected max current, tunes the INA219 gain
    BATTERY_WARN_PCT         amber alert threshold, percent (default 30)
    BATTERY_CRIT_PCT         red alert threshold, percent (default 15)
    BATTERY_EMPTY_V_PER_CELL reserve floor mapped to 0% (default 3.70)
    BATTERY_R_INT_OHMS       pack internal resistance for sag comp (default 0.04)
    BATTERY_I2C_ADDR         INA219 address (default 0x40 — keep the PCA9685 on
                             0x41 via its A0 pad / PAN_TILT_I2C_ADDR)

Bench test: `python3 battery_control.py` prints a few reads.
"""

import os

BATTERY_CELLS = int(os.environ.get("BATTERY_CELLS", "2"))
SHUNT_OHMS = float(os.environ.get("BATTERY_SHUNT_OHMS", "0.1"))
MAX_AMPS = os.environ.get("BATTERY_MAX_AMPS")  # None -> library auto-gain
WARN_PCT = int(os.environ.get("BATTERY_WARN_PCT", "30"))
CRIT_PCT = int(os.environ.get("BATTERY_CRIT_PCT", "15"))
EMPTY_V_PER_CELL = float(os.environ.get("BATTERY_EMPTY_V_PER_CELL", "3.70"))
R_INT_OHMS = float(os.environ.get("BATTERY_R_INT_OHMS", "0.04"))
I2C_ADDR = int(os.environ.get("BATTERY_I2C_ADDR", "0x40"), 0)

_V_EMA_ALPHA = 0.3        # per read (~1 Hz polling from the HUD)
_RESET_RISE_V = 0.25      # voltage jump that re-arms the monotonic percent

# Approximate resting per-cell voltage -> true state-of-charge, descending.
_LIPO_CURVE = [
    (4.20, 100), (4.15, 95), (4.11, 90), (4.08, 85), (4.02, 80),
    (3.98, 75), (3.95, 70), (3.91, 65), (3.87, 60), (3.85, 55),
    (3.84, 50), (3.82, 45), (3.80, 40), (3.79, 35), (3.77, 30),
    (3.75, 25), (3.73, 20), (3.71, 15), (3.69, 10), (3.61, 5), (3.27, 0),
]


def _curve_pct(per_cell):
    """Raw curve lookup (true SoC, unrescaled), linear-interpolated."""
    if per_cell >= _LIPO_CURVE[0][0]:
        return 100.0
    if per_cell <= _LIPO_CURVE[-1][0]:
        return 0.0
    for (v_hi, p_hi), (v_lo, p_lo) in zip(_LIPO_CURVE, _LIPO_CURVE[1:]):
        if v_lo <= per_cell <= v_hi:
            frac = (per_cell - v_lo) / (v_hi - v_lo)
            return p_lo + frac * (p_hi - p_lo)
    return 0.0


def percent_from_voltage(total_v, cells=BATTERY_CELLS, empty_v_per_cell=EMPTY_V_PER_CELL):
    """LiPo charge % from pack voltage, rescaled so the reserve floor reads 0%.

    0% means "come home now" (default 3.70 V/cell), not the datasheet-empty
    3.27 V/cell — a pack taken to true 0 on a boat is a swim. Returns None on
    bad input; clamped to 0-100.
    """
    if total_v is None or cells is None or cells <= 0:
        return None
    per_cell = total_v / cells
    p_true = _curve_pct(per_cell)
    p_floor = _curve_pct(empty_v_per_cell)
    if p_floor >= 100:
        return 0
    rescaled = 100.0 * (p_true - p_floor) / (100.0 - p_floor)
    return round(max(0.0, min(100.0, rescaled)))


def state_from_percent(pct, warn=WARN_PCT, crit=CRIT_PCT):
    """'ok' / 'warn' / 'critical' for the HUD, or None without a reading."""
    if pct is None:
        return None
    if pct <= crit:
        return "critical"
    if pct <= warn:
        return "warn"
    return "ok"


class BatteryMonitor:
    """read() -> dict with voltage/current/percent/state and validity flags."""

    def __init__(self, cells=BATTERY_CELLS, shunt_ohms=SHUNT_OHMS):
        self.cells = cells
        self.warn_pct = WARN_PCT
        self.crit_pct = CRIT_PCT
        self._ina = None
        self.hardware = False
        self._v_ema = None
        self._pct_hold = None     # monotonic non-increasing display percent
        try:
            from ina219 import INA219
            if MAX_AMPS is not None:
                self._ina = INA219(shunt_ohms, float(MAX_AMPS), address=I2C_ADDR)
            else:
                self._ina = INA219(shunt_ohms, address=I2C_ADDR)
            # Explicit full (re)config on every start: 16 V range (2S max 8.4 V),
            # auto gain, 12-bit ADCs, continuous. Never trust retained settings.
            self._ina.configure(voltage_range=self._ina.RANGE_16V)
            self.hardware = True
        except Exception as e:
            print(f"[battery] sensor disabled ({e}); telemetry will report invalid")

    def _invalid(self):
        return {"voltage": None, "current_ma": None, "amps_valid": False,
                "percent": None, "state": None, "cells": self.cells, "valid": False}

    def read(self):
        if not self.hardware:
            return self._invalid()
        try:
            v_raw = self._ina.voltage()               # bus voltage (V)
        except Exception:
            return self._invalid()
        if v_raw is None or v_raw <= 0.1:
            # ADC off / wiring gone — never dress a dead sensor as a full pack
            return self._invalid()

        # Current: unreliable past the shunt's ~3.2 A — flag, don't fake.
        amps = None
        amps_valid = False
        try:
            amps = self._ina.current() / 1000.0       # library returns mA
            amps_valid = True
        except Exception:
            pass

        # Sag compensation (needs trusted current), then EMA smoothing.
        v_comp = v_raw + (amps * R_INT_OHMS if amps_valid and amps and amps > 0 else 0.0)
        if self._v_ema is None:
            self._v_ema = v_comp
        else:
            # A big upward jump = pack swapped/charged: re-arm instead of slewing
            if v_comp - self._v_ema > _RESET_RISE_V:
                self._v_ema = v_comp
                self._pct_hold = None
            else:
                self._v_ema += (v_comp - self._v_ema) * _V_EMA_ALPHA

        pct = percent_from_voltage(self._v_ema, self.cells)
        # Display percent never climbs mid-run (physics-real recovery still
        # reads as a bug); the EMA reset above re-arms it on a pack change.
        if pct is not None:
            if self._pct_hold is None:
                self._pct_hold = pct
            else:
                self._pct_hold = min(self._pct_hold, pct)
            pct = self._pct_hold

        return {
            "voltage": round(self._v_ema, 2),
            "current_ma": round(amps * 1000.0, 1) if amps_valid else None,
            "amps_valid": amps_valid,
            "percent": pct,
            "state": state_from_percent(pct, self.warn_pct, self.crit_pct),
            "cells": self.cells,
            "valid": True,
        }


if __name__ == "__main__":
    import time

    batt = BatteryMonitor()
    print("hardware:", batt.hardware, "cells:", batt.cells,
          "floor:", EMPTY_V_PER_CELL, "V/cell")
    for _ in range(5):
        print(" ", batt.read())
        time.sleep(1)
    print("Done.")
