# CLAUDE.md — FPV RC Boat Project

This file orients Claude Code when working in this repo. Read this before making changes.

## What this project is

A Meta Quest 2 FPV-controlled RC boat. A Raspberry Pi Zero 2 W on the boat streams live camera video to a Quest 2 headset over WebRTC, renders it inside a WebXR viewer with a HUD overlay, records video simultaneously, and receives controller input from the Quest to drive the boat's motors via differential thrust. See `ROADMAP.md` for the full task breakdown by track — use that as the source of truth for "what to work on," and this file for "how the codebase is built and what not to break." `README.md` has setup/run steps and the control mapping, `HARDWARE.md` the wiring, `NETWORKING.md` the IP/WiFi notes.

## Environment

- Runs on a **Raspberry Pi Zero 2 W** — a genuinely weak quad-core ARM board. Assume limited CPU/RAM headroom: video encoding, GPIO, and web serving all compete for the same modest resources.
- SSH hostname: `FPV-boat.local` (fall back to IP via `hostname -I` on the Pi if `.local` resolution fails — this has happened before, especially from Windows clients).
- The Pi's IP is DHCP-assigned and **has changed mid-project before**. Don't hardcode IPs in documentation or assume a fixed address without checking. See `NETWORKING.md`.
- The server serves **HTTPS with a self-signed cert** (`~/cert.pem`, `~/key.pem`) when those files exist — this is **required** for WebXR's `navigator.xr` to be available in the Quest Browser. It falls back to plain HTTP only when no cert is present (fine for desktop testing, but VR won't start). Keep the cert path working; don't remove HTTPS support.
- Camera: Arducam Camera Module 3 Wide (Sony IMX708), via `picamera2`/`libcamera`. Resolution and record bitrate are tunable via env vars (`RECORD_WIDTH/HEIGHT`, `STREAM_WIDTH/HEIGHT`, `RECORD_BITRATE`). The **streamed (lores) default is 960×540** to limit CPU heat — aiortc software-encodes that stream, so its resolution is the main heat lever; **recording (main) stays 1280×720** on the hardware encoder. Higher stream resolutions have overloaded the Pi before.
- **Thermal safety:** the server runs a background monitor that **shuts the Pi down** if CPU temp holds ≥ `CPU_OVERHEAT_C` (default 80 °C) for ~6–9 s. Needs passwordless `sudo shutdown` (see `HARDWARE.md`). The HUD temp is color-coded white/yellow(≥70)/red(≥80).

## Architecture

- **`webrtc_stream.py`** — the server. aiohttp app, HTTPS. Owns the `Picamera2` object and everything camera-related. Routes:
  - `/offer` — WebRTC signaling (video stream to the browser)
  - `/record/start`, `/record/stop` — H.264 recording to `~/recordings/`, runs *simultaneously* with the live WebRTC stream (dual output from one `Picamera2` instance — don't refactor this into two separate camera opens, it'll fail with "device busy"). Before each recording, oldest clips are auto-deleted if free space is below `RECORDINGS_MIN_FREE_GB` (default 2 GB, `0` disables) — never the active file.
  - `/recordings`, `/recordings/download?file=NAME`, `/recordings/delete?file=NAME` — list/download/delete clips (basename-guarded; delete refuses the active file)
  - `/telemetry` — JSON status (recording state, disk space, CPU temp + load)
  - `/control_status` — last received control values
  - `/viewer`, `/clips`, `/three.module.js` — serves the client (VR viewer, recordings manager page, Three.js)
  - `/ws/control` — websocket, receives `{throttle, steer, reverse}` from the browser. Stores `latest_control` **and** drives `motor_control.py` (`motors.set_drive`). No-op physically until the L298N is wired, but the software path is complete.
- **`motor_control.py`** — the differential-thrust L298N motor driver, decoupled from the server so it can be bench-tested standalone (`python3 motor_control.py`). Implements `left = throttle + steer` / `right = throttle - steer` and a **~500 ms watchdog** (zeros the motors if no `set_drive` arrives). Runs as a **no-op if `gpiozero` is unavailable**, so the server works fine on a machine with no GPIO. Pin map lives in `HARDWARE.md`.
- **`webxr_viewer.html`** — the client. Single-file Three.js WebXR app (ES module, no build step). Key things to know:
  - Video and HUD are **separate canvas textures on separate planes**, both head-locked (manually synced to the XR camera every frame via `headLockGroup`, not physically parented).
  - The HUD is drawn with 2D Canvas API calls (`hctx.fillText`, etc.) onto an offscreen canvas, then uploaded as a texture. It is **not** DOM/HTML — the `#debug` div only shows on the flat pre-VR page and is invisible inside an active immersive session. Any in-VR debug output must be drawn onto the HUD canvas.
  - Both planes have `depthTest: false, depthWrite: false` and explicit `renderOrder` — a deliberate z-fighting fix. Don't remove without reason.
  - There is exactly **one** animation loop (`renderer.setAnimationLoop`). A second `requestAnimationFrame` loop caused visible stutter/reprojection glitches in VR (two loops competing for frame budget). Keep all per-frame work in the single loop.
  - The HUD **redraws only when a value changes** (a signature check), not every frame — constant canvas-texture re-upload was churning the GPU and causing dropped-frame black-flashing in VR. Keep this dirty-check.
  - Controller state is read via **`XRSession.inputSources`** (from `renderer.xr.getSession()`), **not** `navigator.getGamepads()` — see Controls below.
- **`three.module.js`** — vendored Three.js r0.160, served locally. The Quest Browser could not reliably reach the jsdelivr CDN during development; don't reintroduce a CDN dependency.
- **`.github/workflows/ci.yml`** — CI: byte-compiles all `*.py` (syntax only, so no Pi-only deps needed) and `node --check`s the viewer's ES module, on PRs and pushes to `main`. It only validates syntax — camera/GPIO behavior must be tested on the Pi.

## Controls (current mapping)

Read via `XRSession.inputSources` during the immersive session:

- **Left trigger** → throttle (0..1)
- **Right thumbstick X** → steer
- **A** — double-tap = start recording, single-tap = stop
- **X** — tap toggles reverse; while cruising, hold = slow down
- **Y** — double-tap toggles **cruise** (throttle hold); while cruising, hold = speed up
- **B, right trigger, thumbsticks, grips** — reserved / unused

Cruise is **client-side**: it holds `cruiseSpeed` as the throttle, X/Y adjust it, reverse is locked out, and a trigger squeeze past ~50% disengages it (safety). The viewer streams `{throttle, steer, reverse}` to `/ws/control` at ~20 Hz — the Pi only ever sees the resulting throttle, so it stays dumb.

> Resolved: an earlier bug where gamepad **buttons** didn't register via `navigator.getGamepads()` during a WebXR session was fixed by switching to `inputSources`. Don't reintroduce `navigator.getGamepads()` for the in-VR path (a desktop fallback that uses it is fine and clearly marked).

## Hardware status (check before assuming something exists)

**In hand and wired:** Pi Zero 2 W, Arducam Camera Module 3 Wide.
**Ordered, not confirmed wired:** PCA9685 + pan/tilt servos (for camera **head-tracking**, not steering), LiPo battery + buck converters, enclosures.
**Not yet in hand as of last check:** L298N motor driver, DPDT failsafe switch, ADC for battery voltage sensing.

Don't write code that assumes motor or servo hardware is connected. `motor_control.py` already no-ops safely without `gpiozero`; bench-test any new hardware-facing path with logged output first.

## Conventions

- **Git:** always stage explicit filenames (e.g. `git add webrtc_stream.py motor_control.py webxr_viewer.html`). **Never `git add -A` or `git add .`** — on the Pi the home directory is the git root and contains far more than the project (SSH keys, shell history, pip cache). A private SSH key was staged this way once; push protection caught it before it leaked — don't rely on that.
- `.gitignore` excludes `.ssh/`, `.cache/`, `.config/`, `.local/`, `recordings/`, `__pycache__/`, and other non-project files. Add new patterns there rather than relying on explicit-add discipline alone.
- Python: match `webrtc_stream.py`'s style (async/await via aiohttp, functions over classes for route handlers). `motor_control.py` is deliberately a class since it holds GPIO + watchdog state.
- `webxr_viewer.html` is a single large unbundled file that changes frequently — **read its current state before paste-in edits** rather than trusting this doc's snapshots.

## Safety notes for motor control (Track B)

- Differential thrust, no rudder: `left = throttle + steer`, `right = throttle - steer`, clamp both to [-1, 1]. Implemented in `motor_control.py`.
- The **software watchdog is implemented**: `motor_control.py` forces both motors to zero if no `set_drive` arrives within ~500 ms (`WATCHDOG_S`), and the server also stops the motors when the control websocket closes. There is no radio fallback while the Pi is driving — this is the main guard against a lost WiFi link running the boat away at its last throttle.
- L298N is rated ~2A/channel continuously. Stock motors are fine. Do **not** pair a larger motor (e.g. 380-size) with the L298N without also upgrading the driver — it will overheat/damage it.
- A physical DPDT failsafe switch is planned to swap the motor leads between the stock RC receiver and the L298N so only one is ever live. This is a wiring task, not software, but code must not assume it's the only safety mechanism (see watchdog above).
