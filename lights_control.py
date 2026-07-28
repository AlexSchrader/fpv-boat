"""Running lights + reverse lights, switched via GPIO through NPN transistors.

As built: ALL running lights (white front + red rear from the ShareGoo 8-LED
kit) are wired as ONE group on a single transistor — they switch together on
one GPIO. The reverse ("backup") lights are a separate group on their own
transistor/GPIO. The Pi's pins can't safely source the LEDs' combined current,
so each GPIO just drives a transistor base. See HARDWARE.md for the table.

    GPIO pin --[1k resistor]--> transistor base
    transistor collector <----- LED group negative
    transistor emitter --------> GND (shared with Pi / buck converter)
    LED group positive ---------> 5V rail (buck converter output)

Running lights: manual toggle (single-tap Y -> /lights/toggle) and auto-on with
recording. Reverse lights: driven by the server off the reverse flag — on while
the boat is in reverse, no button.

Same self-contained, no-op-if-missing pattern as motor_control.py: importing
this never breaks the server if gpiozero or the hardware isn't present.
"""

RUNNING_PIN = 17  # all running lights (front + rear together), physical pin 11
REVERSE_PIN = 22  # reverse ("backup") lights, physical pin 15


class LightController:
    """Running lights (one group) plus a separate reverse-light channel.

    - on() / off() / toggle(): the running lights, driven manually (single-tap
      Y) or auto-on with recording.
    - reverse(on): the backup lights, switched by the server whenever the boat
      enters/leaves reverse.
    """

    def __init__(self):
        try:
            from gpiozero import OutputDevice
            self._running = OutputDevice(RUNNING_PIN)
            self._reverse = OutputDevice(REVERSE_PIN)
            self.hardware = True
        except Exception as e:
            self._running = self._reverse = None
            self.hardware = False
            print(f"[lights] hardware disabled ({e}); running in software-only mode")
        self.state = False
        self.reverse_state = False

    def on(self):
        self.state = True
        if not self.hardware:
            return
        try:
            self._running.on()
        except Exception:
            pass

    def off(self):
        self.state = False
        if not self.hardware:
            return
        try:
            self._running.off()
        except Exception:
            pass

    def toggle(self):
        """Flip the running lights; returns the new state."""
        self.off() if self.state else self.on()
        return self.state

    def reverse(self, on):
        """Switch the reverse lights (idempotent; only acts on a change)."""
        on = bool(on)
        if on == self.reverse_state:
            return
        self.reverse_state = on
        if not self.hardware:
            return
        try:
            self._reverse.on() if on else self._reverse.off()
        except Exception:
            pass


if __name__ == "__main__":
    # Bench test: blinks the running lights, then the reverse lights, so you
    # can confirm both channels' wiring.
    import time

    lights = LightController()
    print("hardware:", lights.hardware)

    for i in range(3):
        print(f"  running on  ({i + 1}/3)")
        lights.on()
        time.sleep(1)
        print(f"  running off ({i + 1}/3)")
        lights.off()
        time.sleep(1)

    print("  reverse lights on")
    lights.reverse(True)
    time.sleep(1)
    print("  reverse lights off")
    lights.reverse(False)

    print("Done.")
