# FPV Boat — Hardware Wiring

Raspberry Pi (CanaKit 40-pin header) → ESC + steering servo.

## Signal wiring

| Function            | BCM   | Physical pin | Notes                          |
| ------------------- | ----- | ------------ | ------------------------------ |
| Throttle (ESC)      | GPIO12 | **32**      | PWM0 — hardware PWM channel     |
| Steering servo      | GPIO13 | **33**      | PWM1 — hardware PWM channel     |
| Common ground       | GND   | **34**       | Shared ground for ESC + servo   |

GPIO12/13 are chosen because they are the Pi's two independent hardware-PWM
channels (PWM0 / PWM1), which give clean, jitter-free servo/ESC pulses. These
values live in `webrtc_stream.py` as `THROTTLE_PIN` / `STEER_PIN` — change them
there if you rewire.

## Power — read before connecting

- Run only the **signal** and **ground** wires from the Pi to the ESC and servo.
- Let the **ESC's BEC** power the steering servo. Do **not** connect the ESC's
  +5V/BEC lead back into the Pi's 5V rail unless you specifically intend to power
  the Pi from the ESC.
- Keep the ESC/motor battery ground and the Pi ground common (physical pin 34).

## Pulse quality (recommended)

`gpiozero`'s default pin factory uses software PWM, which jitters. For stable
1–2 ms ESC/servo pulses, run the server with the pigpio factory:

```sh
sudo pigpiod                                   # start the pigpio daemon once
GPIOZERO_PIN_FACTORY=pigpio python3 webrtc_stream.py
```

If the GPIO libraries are missing entirely, the server prints
`hardware actuation disabled ...` and still runs streaming + recording in
software-only mode.

## Control mapping (from the headset)

| Input                          | Action                                  |
| ------------------------------ | --------------------------------------- |
| Right thumbstick X             | Steer                                   |
| Left thumbstick Y (up = ahead) | Throttle                                |
| A — double-tap                 | Start recording                         |
| A — single-tap                 | Stop recording                          |
| X — tap                        | Toggle reverse (inverts throttle)       |
| B, Y, triggers L / R           | Reserved (unbound)                      |

Throttle and steer values are −1..1. Reverse is applied on the client so the Pi
just drives the signed values it receives. The control link zeroes the motor if
the websocket drops.
