# FPV RC Boat

An FPV RC boat you pilot from a Meta Quest headset: a Raspberry Pi Zero 2 W
streams live camera video over WebRTC into a head-locked WebXR viewer with a
telemetry HUD, records H.264 to the Pi while streaming, and takes throttle/steer
input from the Quest controllers over a websocket to drive the motors.

## Features

- **Live FPV video** — WebRTC (`aiortc` + `picamera2`), 1280×720, head-locked
  video plane rendered in immersive VR.
- **Telemetry HUD** — link quality + ping, recording status, storage, and live
  throttle/steer gauges (with a REV badge and L/R steer markers). Battery is a
  placeholder pending a voltage sensor.
- **Simultaneous recording** — H.264 to `~/recordings/`, runs alongside the
  live stream; start/stop from the controller.
- **Controller input** — Quest controllers read via WebXR `inputSources`;
  steer/throttle/record/reverse mapped as below.
- **Differential-thrust motor control** — `motor_control.py` drives an L298N
  H-bridge (software-only no-op until the driver is wired). See `HARDWARE.md`.

## Controls

| Input                            | Action                              |
| -------------------------------- | ----------------------------------- |
| Right thumbstick X               | Steer                               |
| Left thumbstick Y (up = ahead)   | Throttle                            |
| A — double-tap                   | Start recording                     |
| A — single-tap                   | Stop recording                      |
| X — tap                          | Toggle reverse (inverts throttle)   |
| B, Y, triggers L / R             | Reserved (unbound)                  |

Steering is **differential thrust** (no rudder): `left = throttle + steer`,
`right = throttle - steer`.

## Running (on the Pi)

```sh
# WebXR needs HTTPS — generate a self-signed cert once (VR won't start over plain HTTP)
openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
  -keyout ~/key.pem -out ~/cert.pem -subj "/CN=fpv-boat"

python3 webrtc_stream.py          # serves HTTPS on :5000 when the cert is present
```

Then in the Quest browser open **`https://<pi-ip>:5000/viewer`**, accept the
self-signed cert warning, and hit **Enter VR**. (Pi is `FPV-boat`, currently
`10.0.0.26` — see `NETWORKING.md` for keeping the IP stable and avoiding SSH
drops.)

For smooth motor PWM once the L298N is wired, run with the pigpio pin factory:

```sh
sudo apt install -y pigpio && sudo systemctl enable --now pigpiod
GPIOZERO_PIN_FACTORY=pigpio python3 webrtc_stream.py
```

The server runs streaming + recording even without the GPIO libs — it just logs
`hardware disabled` and skips motor output.

## Tuning (env vars)

Set these before launching `webrtc_stream.py` — defaults keep current behavior:

| Var | Default | Purpose |
| --- | ------- | ------- |
| `RECORD_WIDTH` / `RECORD_HEIGHT` | `1280` / `720` | Recorded (main) resolution |
| `STREAM_WIDTH` / `STREAM_HEIGHT` | `1280` / `720` | Streamed (lores) resolution — must be ≤ record size |
| `RECORD_BITRATE` | `0` (encoder default) | H.264 record bitrate, bits/sec |
| `RECORDINGS_MIN_FREE_GB` | `2.0` | Free-space floor before auto-deleting oldest clips (`0` disables) |

1280×720 is the known-good baseline — higher resolutions have overloaded the
Pi's encoder before, so bump incrementally while watching `htop`. Example:

```sh
RECORD_BITRATE=8000000 python3 webrtc_stream.py
```

## Endpoints

| Route              | Purpose                                            |
| ------------------ | -------------------------------------------------- |
| `/viewer`          | WebXR viewer page                                  |
| `/offer`           | WebRTC signaling (POST)                            |
| `/ws/control`      | Websocket: `{throttle, steer, reverse}` → motors   |
| `/control_status`  | Last received control values (JSON)                |
| `/record/start` `/record/stop` | Recording control (start auto-frees space) |
| `/telemetry`       | Recording state + storage (JSON)                   |
| `/recordings`      | List clips — name, size, timestamp (JSON)          |
| `/recordings/download?file=NAME` | Download a clip over HTTP             |
| `/three.module.js` | Vendored Three.js                                  |

## Files

| File | Purpose |
| ---- | ------- |
| `webrtc_stream.py` | Main server: WebRTC video, recording, telemetry, control websocket, optional HTTPS |
| `motor_control.py` | L298N differential-thrust driver with a 0.5 s safety watchdog (bench-test: `python3 motor_control.py`) |
| `webxr_viewer.html` | Three.js WebXR viewer + HUD + controller input |
| `three.module.js` | Vendored Three.js (served locally, no CDN) |
| `stream.py` | Legacy MJPEG server (superseded, kept for reference) |
| `HARDWARE.md` | Wiring, power safety, pin map, watchdog notes |
| `NETWORKING.md` | Keeping the Pi's IP stable + WiFi power-save fix |
| `ROADMAP.md` | Project tracks and current-state snapshot |

## Bench-testing the motors

With the motors **off** the boat and the L298N wired per `HARDWARE.md`:

```sh
python3 motor_control.py    # ramps ahead, spins each way, then astern
```

## Development

CI (`.github/workflows/ci.yml`) byte-compiles the Python (syntax only — no
Pi-only deps needed) and syntax-checks the viewer's ES module on every PR and
push to `main`.

**Repo hygiene:** on the Pi, the project shares the home directory, so always
`git add <specific files>` — never `git add -A` / `git add .`.

See `ROADMAP.md` for planned work (battery telemetry, recording management,
pan/tilt head-tracking, cruise control, and more).
