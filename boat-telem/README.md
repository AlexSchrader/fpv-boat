# BOAT-TELEM — Real-Time C++ Telemetry Daemon (Phase 1)

Implements the BOAT-TELEM v1.0 spec: a single C++17 daemon that owns every
sensor, fuses readings into one timestamped `TelemetrySnapshot` at 20 Hz,
broadcasts it over WebSocket, and logs every snapshot as JSON Lines.

```
[GPS UART thread] ─┐
[QMC5883P thread]  ─┼─► SharedSensorState ─► fusion @20Hz ─► snapshot ─┬─► ws://<pi>:8765 (broadcast)
[INA219 thread]    ─┤                                                  └─► telemetry_log_<ts>.jsonl
[MPU-6050 thread]  ─┘
```

## Build & run (on the Pi)

```sh
sudo apt install -y cmake g++        # one-time
cd ~/fpv-boat/boat-telem
cmake -B build && cmake --build build -j4     # a few minutes on a Zero 2 W
ctest --test-dir build                        # unit tests (no hardware needed)
./build/boat-telem                            # Ctrl-C to stop
```

Any WebSocket client can consume the stream — quick look from a laptop:
browser dev-console `new WebSocket("ws://<pi>:8765/").onmessage = e => console.log(e.data)`.

## Env vars

| Var | Default | Meaning |
|---|---|---|
| `TELEM_PORT` | `8765` | WebSocket port |
| `TELEM_RATE_HZ` | `20` | Fusion/broadcast rate |
| `TELEM_LOG_DIR` | `$HOME` | Where `telemetry_log_*.jsonl` land |
| `TELEM_GPS_PORT` | `/dev/ttyAMA0` | GPS serial device (38400 baud) |
| `BATTERY_I2C_ADDR` | `0x40` | INA219 address |
| `COMPASS_I2C_ADDR` | `0x2C` | QMC5883P address |
| `COMPASS_DECLINATION_DEG` | `-9` | Magnetic declination |
| `IMU_I2C_ADDR` | `0x68` | MPU-6050 address |

## Deviations from the spec (deliberate, documented)

1. **Magnetometer**: the spec says "M10-25Q magnetometer"; the as-built board is
   a **QMC5883P at 0x2C** (chip-ID verified at init — the same discovery that
   broke every off-the-shelf library). The driver **reads the existing
   calibration file `~/.fpv-boat-compass.json`**, so the C++ and Python stacks
   share one hard-iron calibration. Heading here is tilt-naive (noted per spec
   4.3); the tilt-compensated HDG lives in the Python/HUD path.
2. **AccelDriver is real, not a stub**: an MPU-6050 (GY-521) is wired at 0x68.
   It supplies `imu.pitchDeg/rollDeg` (a snapshot extension), honoring the
   level-pose file `~/.fpv-boat-imu.json`. The **`speed` field stays a stale
   placeholder** exactly as spec 4.5 intends — accel-derived speed is Phase-2
   fusion.
3. **Battery curve**: spec's rough "8.4→100 / 6.0→0" is replaced by the same
   nonlinear 2S curve + reserve floor (0% = 3.70 V/cell = *come home now*) the
   Python gauge ships — so both stacks agree on percent.
4. **WebSocket library**: the "minimal hand-rolled option" from Section 10 —
   RFC 6455 server-to-client text frames only (~150 lines, zero dependencies).
   uWebSockets/websocketpp buy nothing for a pure broadcast stream and cost
   real build pain on a Zero 2 W.
5. **INA219 init** is the full explicit sequence the spec demands: reset →
   verify post-reset config (`0x399F`) → calibration `4096` → config `0x199F`.
   Current is reported `null` on math-overflow (the 0.1 Ω shunt clips ~3.2 A)
   instead of a confidently wrong number; voltage stays valid regardless.

## Section 10 answers

- **WS library** → hand-rolled minimal (above).
- **I2C addresses** → INA219 `0x40`, QMC5883P `0x2C`, MPU-6050 `0x68`
  (PCA9685 → `0x41` after its A0 bridge; it stays owned by the Python
  head-tracking path and never touched here).
- **HUD data path** → **parallel** during transition: the aiohttp `/telemetry`
  route remains the HUD's source; this stream serves logging/replay/future
  control first. Swapping the HUD to this socket is a later, isolated change.
- **Fusion rate** → 20 Hz confirmed fine (HUD only polls ~1 Hz; the extra rate
  exists for the log/replay and Phase-4 control).

## Manual QA checklist (hardware-in-the-loop, spec 8.4)

- [x] Boot with **no sensors** → starts cleanly, everything `stale:true`, no
      crash *(verified in CI-like environment: no `/dev/i2c-1`, no UART)*
- [x] WebSocket client receives a steady stream *(verified: handshake + valid
      JSON frames at 20 Hz)*
- [x] Log file grows as valid JSON Lines *(verified: ~20 lines/s, one object
      per line)*
- [ ] GPS only → `gps.stale` flips false once a fix is acquired (needs sky)
- [ ] INA219 → real voltage (not 0), percent tracks charge state *(needs the
      A0 bridge on the PCA9685 first — shared-address corruption otherwise)*
- [ ] Pull GPS mid-run → block goes stale ≤2 s, everything else keeps reporting
- [ ] Extended session → log stays valid JSON Lines throughout

## Phases (context)

This is **Phase 1** (read/fuse/stream/log) only. Phase 2 = command channel,
Phase 3 = replay dashboard over the `.jsonl` logs, Phase 4 = closed-loop
control consuming this same snapshot stream. The driver interface /
SharedSensorState / fusion-loop split exists so those phases extend this
daemon rather than rewriting it.
