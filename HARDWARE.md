# FPV Boat — Hardware Wiring

Raspberry Pi Zero 2 W → **L298N dual H-bridge → two motors** (differential
thrust, no rudder / no steering servo).

## L298N wiring

| Function                 | L298N pin | BCM    | Physical pin |
| ------------------------ | --------- | ------ | ------------ |
| Left motor speed (PWM)   | ENA       | GPIO12 | **32** (PWM0) |
| Left motor direction     | IN1       | GPIO5  | **29**       |
| Left motor direction     | IN2       | GPIO6  | **31**       |
| Right motor speed (PWM)  | ENB       | GPIO13 | **33** (PWM1) |
| Right motor direction    | IN3       | GPIO20 | **38**       |
| Right motor direction    | IN4       | GPIO21 | **40**       |
| Common ground            | GND       | GND    | **34** (and 39) |

Motor outputs: **OUT1/OUT2 → left motor**, **OUT3/OUT4 → right motor**. If a
motor spins the wrong way, swap its two output leads (or its IN pins).

ENA/ENB are on GPIO12/GPIO13 because those are the Pi's two hardware-PWM
channels (PWM0/PWM1) — smoothest speed control. IN1..IN4 are plain GPIO. Pins
are defined in `motor_control.py` (`LEFT_EN`/`LEFT_IN1`… etc.) — change them
there if you rewire.

## Power — read before connecting

- **Motor supply (Vs / +12V terminal):** from the motor battery (LiPo, through
  the buck converter per your power plan) — **not** from the Pi's 5V.
- **Grounds must be common:** tie the L298N GND, the battery ground, and the
  Pi ground (physical pin 34) together.
- **Do not backfeed** the L298N's onboard 5V regulator output into the Pi's 5V
  rail. Run only the six control lines (ENA/ENB/IN1-4) + ground between the
  L298N and the Pi.
- **DPDT failsafe switch:** install before any *water* testing (a wiring task,
  not code). Bench testing the L298N over GPIO does not require it.
- **Driver rating:** this L298N is ~2A/channel continuous — fine for stock toy
  motors. If you move to bigger motors (e.g. 380-size) you must also upgrade the
  driver (BTS7960 or dual DRV8871). Don't put a big motor on this driver.

## Differential thrust

Steering is done by driving the two motors at different speeds:

```
left_motor  = throttle + steer
right_motor = throttle - steer      # each clamped to -1.0 .. 1.0
```

Negative values reverse a motor (full H-bridge), so the viewer's reverse toggle
just sends a negative throttle. This math lives in `motor_control.py`.

## Safety watchdog

`MotorController` zeroes both motors if `set_drive()` isn't called within 0.5 s
(`WATCHDOG_S`). The viewer feeds control at ~20 Hz, so a dropped link or a
stalled client stops the boat rather than letting it run away. The server also
calls `motors.stop()` when the control websocket closes.

## Pulse quality (recommended)

`gpiozero`'s default pin factory uses software PWM, which jitters. For clean
PWM run the server with the pigpio factory:

```sh
sudo pigpiod                                   # start the pigpio daemon once
GPIOZERO_PIN_FACTORY=pigpio python3 webrtc_stream.py
```

If the GPIO libraries are missing entirely, `motor_control.py` prints
`[motor] hardware disabled ...` and the server still runs streaming + recording
in software-only mode.

## Bench testing

Test the driver on its own, motors **off** the boat, before wiring to the web
server:

```sh
python3 motor_control.py
```

This ramps both motors ahead, spins in place each way, then astern — confirming
direction, speed response, and channel independence.

## Thermal safety (auto-shutdown)

The Pi Zero 2 W is thermally marginal under video encoding. The server runs a
background monitor: if CPU temperature stays at/above `CPU_OVERHEAT_C` (default
**80 °C**) for a few seconds, it stops the motors and **shuts the Pi down** to
protect the hardware. The HUD temp readout is color-coded: **white** = OK,
**yellow** = caution (≥70 °C), **red** = overheating (≥80 °C, shutdown imminent).

For the shutdown to work, the server's user needs **passwordless `sudo shutdown`**.
Add a sudoers drop-in once:

```sh
echo "$USER ALL=(ALL) NOPASSWD: /sbin/shutdown" | sudo tee /etc/sudoers.d/thermal-shutdown
sudo chmod 440 /etc/sudoers.d/thermal-shutdown
```

Without it, the monitor logs `shutdown failed … is passwordless sudo set up?`
and the Pi keeps running (relying on the firmware's own ~85 °C hardware
throttle/shutdown as the last line of defense). Tune the threshold with the
`CPU_OVERHEAT_C` env var.

The same passwordless-`sudo shutdown` rule also powers the **in-headset
shutdown combo** (both grips + B → confirm popup → `/system/shutdown`), which
takes the identical safe-poweroff path (stop motors/lights, close any active
recording, then `sudo shutdown`). One sudoers drop-in covers both. Note the
client call is currently **stubbed for testing** (confirming Yes just flashes a
HUD badge) — the endpoint is live, but nothing hits it until the one-line swap
in `triggerShutdown()` is uncommented.

## Running lights (ShareGoo 8-LED kit)

Two LED groups (4 white front, 4 red rear), each switched by its own NPN
transistor — GPIO can't safely source the LEDs' combined current, so it just
drives the transistor base. **Lights auto-turn-on with recording** (on at
`/record/start`, off at `/record/stop` and on thermal shutdown) **and can be
toggled manually** (single-tap Y → `/lights/toggle`); both groups switch
together. Code: `lights_control.py` (`python3 lights_control.py` to bench-blink).
No-op without `gpiozero`.

A third channel drives rear **reverse ("backup") lights** that come on
automatically whenever the boat is in reverse (the server calls
`lights.reverse()` off the control websocket's reverse flag). These LEDs aren't
installed yet — the pin is already claimed so wiring is drop-in, and it stays a
no-op until then. Wire it like the other groups (GPIO → 1k → transistor base).

| Function          | BCM    | Physical pin |
| ----------------- | ------ | ------------ |
| White front group | GPIO17 | **11**       |
| Red rear group    | GPIO27 | **13**       |
| Reverse lights    | GPIO22 | **15** (future install) |

Per group:
```
GPIO pin --[1k]--> transistor base
transistor collector <-- LED group negative
transistor emitter ----> GND (shared with Pi / buck converter)
LED group positive -----> 5V rail (buck converter output)
```

Change the pins in `lights_control.py` (`FRONT_PIN` / `REAR_PIN`) if you rewire.

## Control mapping (from the headset)

| Input                          | Action                                  |
| ------------------------------ | --------------------------------------- |
| Left trigger                   | Throttle (squeeze to go)                |
| Right thumbstick X             | Steer                                   |
| A — double-tap                 | Start recording                         |
| A — single-tap                 | Stop recording                          |
| X — double-tap                 | Toggle cruise; while cruising, hold = faster  |
| Y — single-tap                 | Toggle running lights (also auto-on with recording) |
| Y — double-tap                 | Toggle reverse; while cruising, hold = slower |
| Both grips + B                 | Open the shutdown-confirm popup (stick to choose, A to select) |
| Right trigger, left thumbstick | Reserved / unused                       |

> Note: this mapping follows the current build. `ROADMAP.md` Track A describes an
> earlier scheme (A = record toggle, B = reverse). The code above is canonical;
> reconcile the roadmap when convenient.
