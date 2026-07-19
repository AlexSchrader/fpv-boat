# FPV RC Boat — Project Roadmap

**How to use this doc:** Each track below is written to be picked up independently — you don't need to finish Track A before starting Track C. Dependencies are called out explicitly where they exist (mostly hardware arrival, not code order). Hand any single track to Claude Code and it should have enough context to work without needing the others done first.

---

## Current State Snapshot (as of this doc)

**Hardware in hand and wired:** Raspberry Pi Zero 2 W (`FPV-boat` hostname), Arducam Camera Module 3 Wide (IMX708, CSI-connected), heatsink installed, 128GB microSD as boot/storage drive.

**Hardware NOT yet in hand / not yet wired:** L298N dual H-bridge motor driver, DPDT failsafe switch, PCA9685 + pan/tilt servos (ordered, not confirmed arrived/tested), LiPo + buck converters + enclosures (ordered, build not started).

**Software running today, confirmed working:**
- `webrtc_stream.py` — aiohttp server on the Pi, HTTPS (self-signed cert), serves:
  - `/offer` — WebRTC signaling endpoint, streams live camera video via `aiortc` + `picamera2`
  - `/record/start`, `/record/stop`, `/record/status` — H.264 recording to `~/recordings/`, runs simultaneously with the live stream
  - `/telemetry` — JSON with recording state + disk space
  - `/viewer` — serves the WebXR HTML page
  - `/three.module.js` — locally hosted Three.js (no CDN dependency)
  - `/ws/control` — websocket endpoint, receives `{throttle, steer, reverse}` JSON and drives `motor_control.py` (differential thrust). No-op until the L298N is wired, but the software path is complete.
- `webxr_viewer.html` — Three.js WebXR page:
  - Head-locked video plane + HUD plane (canvas-texture based), rendered in immersive VR
  - HUD shows: link quality (bars + ping), battery (placeholder, no sensor), recording status + storage bar, throttle/steer gauges
  - Gamepad polling via `navigator.getGamepads()` — **currently broken for buttons, see Track A**
  - Debug overlay drawn onto the HUD canvas itself (necessary since HTML overlays aren't visible inside an active VR session)
- `three.module.js` — vendored Three.js r0.160, served locally to avoid CDN dependency issues in the Quest browser
- `stream.py` — legacy MJPEG-based server, superseded by `webrtc_stream.py`, can likely be deleted once WebRTC is confirmed fully stable

**~~Known active bug~~ (RESOLVED):** Gamepad button presses weren't registering via `navigator.getGamepads()` on the Quest. Fixed by reading `session.inputSources` directly (Track A). Controller input is now confirmed working in-headset. See the current mapping in `README.md` / `HARDWARE.md`.

**Repo:** `github.com/AlexSchrader/fpv-boat`, SSH-auth push access set up on the Pi. `.gitignore` in place excluding `.ssh/`, `.cache/`, `recordings/`, and other non-project files after an earlier incident where a private key was briefly staged (rotated afterward, not actually exposed). **Always `git add` explicit filenames on this repo, never `git add -A` or `git add .`, since the Pi's home directory contains far more than just the project.**

**Networking notes:** Pi's IP is DHCP-assigned and has changed at least once mid-project (was `10.0.0.20`, moved to `10.0.0.26`). If the viewer says "unreachable," check `hostname -I` on the Pi first before assuming a code problem. WiFi power management was disabled (`iwconfig wlan0 power off`) to fix intermittent SSH disconnects — this fix is only applied for the current boot; needs `/etc/rc.local` persistence confirmed if it hasn't been already.

---

## Track A — Fix Controller Input (WebXR inputSources) — ✅ DONE

> Completed: migrated to `session.inputSources`, mapped right stick → steer,
> left stick → throttle, A double-tap/tap → record start/stop, X → reverse
> toggle (B/Y/triggers reserved), streaming to `/ws/control`. Confirmed live in
> the headset. Note the final button scheme differs from the original text
> below (A was "record toggle", B was "reverse") — the mapping in `README.md`
> is canonical.

**Goal:** Get real throttle/steer/button data flowing reliably from the Quest controllers to the Pi.

**Independent of:** Everything else. This is pure browser JS work against the existing `webxr_viewer.html` and doesn't need motor hardware to test — you can verify it's working by watching values change at `/control_status` or on the HUD gauges.

**What's broken:** `navigator.getGamepads()` button arrays aren't returning presses in the Quest browser during an immersive session, even though axes (thumbstick position) appear to work.

**What to do:**
1. Replace `navigator.getGamepads()` polling with the WebXR-native approach: inside the `renderer.setAnimationLoop` callback, access `session.inputSources` (available via `renderer.xr.getSession()`), iterate each `XRInputSource`, and read `.gamepad.axes` / `.gamepad.buttons` from there instead. This is the spec-correct path and should be far more reliable than the generic Gamepad API during an XR session.
2. Use `inputSource.handedness` (`'left'` / `'right'`) instead of guessing from `gamepad.hand` or array index.
3. Keep the existing debug-overlay-on-HUD-canvas pattern for testing — HTML overlays aren't visible once inside VR, so any debug output must be drawn onto `hudCanvas` via `hctx`, not the DOM `#debug` div.
4. Once button events are confirmed working, re-wire: A → `/record/start`/`/record/stop` toggle, B → reverse mode toggle, X/Y → logged only (reserved for cruise control later, see Track H).
5. Verify end-to-end by checking `/control_status` on the Pi updates live while moving sticks/pressing buttons, before considering this done.

**Files touched:** `webxr_viewer.html` only.

---

## Track B — Motor Control (L298N + Differential Thrust)

**Goal:** Get the Pi actually driving the two boat motors via GPIO, using differential thrust (no rudder).

**Independent of:** Controller input fully working (can bench-test with hardcoded throttle/steer values first), video/HUD state.

**Blocked on:** L298N dual H-bridge motor driver arriving (not yet ordered/received as of this doc — confirm status). DPDT failsafe switch also needed before this touches the actual boat, though GPIO-level bench testing of the L298N itself doesn't require the switch.

**Design already locked in:**
```
left_motor  = throttle + steer
right_motor = throttle - steer
# clamp each to -1.0 .. 1.0
```
- Throttle comes from an exponential curve on the trigger/stick input: `throttle = sign(x) * x**2` (already implemented client-side in the viewer for display; needs to be the actual value sent, or applied server-side — decide one place to own this transform, not both, to avoid double-curving).
- L298N gives two independent H-bridge channels — one per motor, each independently forward/reverse/PWM-speed capable.
- **Current L298N is rated ~2A per channel continuously.** Stock toy motors are fine. If you upgrade to bigger motors later (e.g. 380-size), you MUST also upgrade the driver (BTS7960 or dual DRV8871) — don't put a bigger motor on this driver.

**What to do:**
1. Wire L298N to Pi GPIO (standard 4-pin control: IN1-4 for direction, ENA/ENB for PWM speed — Pi Zero 2 W has hardware PWM on limited pins, check which GPIO you're using support it or use software PWM via a library like `RPi.GPIO` or `gpiozero`).
2. Write a small standalone Python module (e.g. `motor_control.py`) with a class exposing `set_drive(throttle: float, steer: float)` that does the differential thrust math and writes to GPIO. Keep this decoupled from the web server so it can be bench-tested independently with a simple script before wiring to the websocket.
3. Bench-test on a breadboard with the motors OFF the boat first — confirm direction, speed response, and that both channels are independent.
4. Wire the `/ws/control` handler in `webrtc_stream.py` (the `latest_control` dict already receives `{throttle, steer, reverse}`) to call into this new motor module on every message.
5. Add the software watchdog: if no control message received in ~500ms, force throttle to 0. This is critical since there's no radio fallback when the Pi is in control.
6. DPDT failsafe switch installation is a physical/wiring task, not code — do this before any water testing, not before bench testing.

**Files touched:** New `motor_control.py`, edits to `webrtc_stream.py`'s `control_ws` handler.

---

## Track C — Video Pipeline Quality & Performance

**Goal:** Improve video quality/smoothness without regressing the stability that's already been hard-won.

**Independent of:** Everything else — this is purely `webrtc_stream.py` + camera config.

**Current state:** 1280x720, software H.264 encoding via `aiortc`'s default `libx264` path (no Pi GPU hardware encoding wired up — an earlier attempt to patch `aiortc`'s codec module to use `h264_v4l2m2m` was written but never confirmed successful; worth verifying whether it's actually active). Occasional ASW/reprojection glitches in VR mode, believed tied to frame timing rather than a hard bug (largely resolved after removing a duplicate animation loop — see Track A history).

**What to try, roughly in order of effort:**
1. **Confirm/verify hardware encoding is actually active.** Check the `aiortc` `h264.py` codec file on the Pi for the `h264_v4l2m2m` patch and confirm no "falling back to libx264" warning appears in server logs when a client connects.
2. **Resolution/bitrate tuning** — 1280x720 is the known-good baseline. Any increase (tried 1080p30 earlier, caused visible degradation from CPU/encoder overload) should be tested incrementally, watching Pi CPU usage (`htop`) live while streaming, not just eyeballing the result.
3. Consider exposing resolution/bitrate as environment variables or a config file rather than hardcoded, so this can be tuned without editing source each time.

**Files touched:** `webrtc_stream.py`, possibly the installed `aiortc` package files directly (document any such patches clearly since they don't survive a `pip install --upgrade`).

---

## Track D — HUD: Real Battery Telemetry

**Goal:** Replace the battery HUD placeholder ("BATTERY --%") with a real reading.

**Independent of:** Everything else.

**Blocked on:** A voltage divider circuit wired from the LiPo to a Pi ADC-capable input. The Pi has no native ADC — this needs either an external ADC chip (e.g. ADS1115 over I2C, commonly available) or a simple voltage divider into a GPIO with some other read method. Needs hardware decision before software work here is meaningful.

**What to do once hardware exists:**
1. Add a `/telemetry` field for `battery_voltage` and `battery_percent` (percent requires knowing your LiPo's full/empty voltage curve — 2S LiPo is roughly 8.4V full, 6.4V empty as a rough linear approximation, though real LiPo discharge curves aren't linear).
2. Update `drawHud()` in `webxr_viewer.html` to render the real value instead of the ghosted placeholder — the drawing code and layout position are already built, just swap the static string for the live value.

**Files touched:** `webrtc_stream.py` (`/telemetry` route), `webxr_viewer.html` (`drawHud` battery section).

---

## Track E — Recording Management

**Goal:** Prevent the SD card from silently filling up, and make old recordings easy to manage.

**Independent of:** Everything else. Recording itself already works.

**What to do:**
1. Add a simple cleanup policy — e.g. delete recordings older than N days, or cap total recordings folder size, triggered either on `/record/start` or via a cron job.
2. Consider adding a `/recordings` listing endpoint (filenames + sizes + timestamps) so a future UI could show/download/delete clips without SSHing in.
3. The storage bar on the HUD already shows free space live — no changes needed there unless the above listing endpoint gets a UI counterpart.

**Files touched:** `webrtc_stream.py`.

---

## Track F — Networking Reliability

**Goal:** Stop losing time to "unreachable" / SSH drops / changed IPs.

**Independent of:** Everything else.

**What to do:**
1. **Set a DHCP reservation** for the Pi's MAC address on your router (or a static IP directly on the Pi via `dhcpcd.conf`), so the IP stops changing between sessions. This alone would have saved real time earlier in the project.
2. **Confirm `iwconfig wlan0 power off` survives reboot** — check whether the earlier `/etc/rc.local` edit actually took effect (test with an actual reboot, not just trusting it was saved correctly).
3. Consider documenting the current IP/hostname prominently (e.g. in this repo's README) so it's not re-discovered via trial and error each session.

**Files touched:** Pi system config only (`/etc/dhcpcd.conf` or router admin panel, `/etc/rc.local`), no project code.

---

## Track G — Repo Hygiene

**Goal:** Keep the GitHub repo clean and safe going forward.

**Independent of:** Everything else. Already in a good state, this is about not regressing.

**Rules already established:**
- Always `git add <specific filenames>`, never `git add -A` / `git add .` in the Pi's home directory.
- `.gitignore` already excludes `.ssh/`, `.cache/`, `.config/`, `.local/`, `recordings/`, and other non-project files.
- SSH key auth is set up for pushing — no tokens/passwords needed.

**Possible follow-ups:**
1. Add a proper `README.md` to the repo summarizing the project (this roadmap doc could partially serve that purpose or be linked from it).
2. Consider moving `~/recordings/` off the Pi periodically (rsync/scp to a computer) rather than only relying on the `.gitignore` exclusion, since it's not backed up anywhere as-is.

**Files touched:** `.gitignore`, new `README.md`.

---

## Track H — Post-MVP / Future Work

**Goal:** Everything explicitly deferred until the core FPV + motor control loop is proven on water.

**Independent of:** All other tracks — these are intentionally last.

1. **Pan/tilt camera + head tracking** — PCA9685 + 2x SG90 servos, WebXR head quaternion → websocket → servo angles. Blocked on servo hardware being confirmed in-hand and bench-tested (per earlier note, this hardware may still be pending).
2. **Cruise control** — X or Y button (currently reserved/unused) engages a throttle-hold mode: locks current throttle value, steer stays live, any throttle input or reverse-toggle auto-disengages it as a safety default. Build this into the same control-handling code as Track A once buttons are working.
3. **Obstacle avoidance** — HC-SR04 ultrasonic sensor, auto-stop/steer-away logic.
4. **Motor/driver upgrade** — 380-size motor + BTS7960 or dual DRV8871 driver, only after the stock-motor + L298N version is proven reliable.
5. **Raspberry Pi AI Camera (IMX500)** — drop-in swap for the current camera module (same CSI mount), enables on-sensor AI inference without taxing the Pi's CPU.
6. **Native Android/OpenXR app** — a genuine platform migration away from the browser-based WebXR approach, mentioned as the right move specifically when AI features get added, since it opens up more direct hardware/performance access than a browser page can offer. Not a small task — treat as its own project phase, not a "track" alongside the others.
7. **Vision-based navigation / follow-me mode** — depends on #5 above being done first.

---

## Quick Reference — File Map

| File | Purpose | Status |
|---|---|---|
| `webrtc_stream.py` | Main server: WebRTC video, recording, telemetry, control websocket, optional HTTPS | Active, working |
| `webxr_viewer.html` | Client: Three.js WebXR viewer + HUD + controller input | Active, working |
| `README.md` | Project overview, setup, controls | Active |
| `HARDWARE.md` | L298N wiring, power, watchdog, pin map | Active |
| `.github/workflows/ci.yml` | CI: byte-compile Python + JS syntax check | Active |
| `three.module.js` | Vendored Three.js | Static, no changes expected |
| `stream.py` | Legacy MJPEG server | Superseded, candidate for deletion |
| `motor_control.py` | Motor GPIO driver (L298N differential thrust + watchdog) | Created; awaiting L298N hardware to go live |
| `.gitignore` | Repo hygiene | In place |
