"""Front/rear running lights, switched via GPIO through an NPN transistor per group.

Two LED groups (per the ShareGoo 8-LED kit): 4 white front, 4 red rear, each
group wired through its own transistor so the Pi's GPIO pins (which can't
safely source the LEDs' combined current directly) just switch the transistor's
base on/off. See HARDWARE.md for the wiring table.

    GPIO pin --[1k resistor]--> transistor base
    transistor collector <----- LED group negative
    transistor emitter --------> GND (shared with Pi / buck converter)
    LED group positive ---------> 5V rail (buck converter output)

Currently wired to auto-trigger with recording (see webrtc_stream.py) rather
than a controller button -- lights on when record_start fires, off on
record_stop. Both groups switch together.

Same self-contained, no-op-if-missing pattern as motor_control.py: importing
this never breaks the server if gpiozero or the hardware isn't present.
"""

FRONT_PIN = 17   # 4x white LEDs, physical pin 11
REAR_PIN = 27    # 4x red LEDs, physical pin 13


class LightController:
    """on() / off() -- switches both LED groups together."""

    def __init__(self):
        try:
            from gpiozero import OutputDevice
            self._front = OutputDevice(FRONT_PIN)
            self._rear = OutputDevice(REAR_PIN)
            self.hardware = True
        except Exception as e:
            self._front = self._rear = None
            self.hardware = False
            print(f"[lights] hardware disabled ({e}); running in software-only mode")
        self.state = False

    def on(self):
        self.state = True
        if not self.hardware:
            return
        try:
            self._front.on()
            self._rear.on()
        except Exception:
            pass

    def off(self):
        self.state = False
        if not self.hardware:
            return
        try:
            self._front.off()
            self._rear.off()
        except Exception:
            pass


if __name__ == "__main__":
    # Bench test: blinks both groups a few times so you can confirm wiring
    # before wiring this into the server's record start/stop.
    import time

    lights = LightController()
    print("hardware:", lights.hardware)

    for i in range(3):
        print(f"  on  ({i + 1}/3)")
        lights.on()
        time.sleep(1)
        print(f"  off ({i + 1}/3)")
        lights.off()
        time.sleep(1)

    print("Done.")
