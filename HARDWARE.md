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

## Control mapping (from the headset)

| Input                          | Action                                  |
| ------------------------------ | --------------------------------------- |
| Left trigger                   | Throttle (squeeze to go)                |
| Right thumbstick X             | Steer                                   |
| A — double-tap                 | Start recording                         |
| A — single-tap                 | Stop recording                          |
| X — tap                        | Toggle reverse; while cruising, hold = slower |
| Y — double-tap                 | Toggle cruise; while cruising, hold = faster  |
| B, right trigger, grips        | Reserved / unused                       |

> Note: this mapping follows the current build. `ROADMAP.md` Track A describes an
> earlier scheme (A = record toggle, B = reverse). The code above is canonical;
> reconcile the roadmap when convenient.
