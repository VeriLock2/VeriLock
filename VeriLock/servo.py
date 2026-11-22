"""Control the servo that turns the deadbolt gear."""

import time
import config


class Servo:

    def __init__(self):
        self.position = "UNKNOWN"
        self.current_angle = None
        self.servo = None
        self.pin_factory = None

        print(f"[SERVO] Initializing {config.SERVO_MODEL}...")

        if config.SIMULATION_MODE:
            print("[SERVO] Simulation mode active.")
        else:
            self.initialize_hardware()

        print(f"[SERVO] Signal: GPIO{config.SERVO_GPIO} / physical pin 12")
        print(f"[SERVO] Lock position: {config.SERVO_LOCK_ANGLE} degrees")
        print(f"[SERVO] Unlock position: {config.SERVO_UNLOCK_ANGLE} degrees")


    def initialize_hardware(self):
        try:
            from gpiozero import AngularServo
            from gpiozero.pins.lgpio import LGPIOFactory
        except ImportError as error:
            raise RuntimeError(
                "GPIO Zero/lgpio is not installed. On Raspberry Pi 5, "
                "install current gpiozero and lgpio packages."
            ) from error

        try:
            self.pin_factory = LGPIOFactory()

            self.servo = AngularServo(
                config.SERVO_GPIO,
                initial_angle=None,
                min_angle=0.0,
                max_angle=config.SERVO_MAX_ANGLE,
                min_pulse_width=config.SERVO_MIN_PULSE_US / 1_000_000.0,
                max_pulse_width=config.SERVO_MAX_PULSE_US / 1_000_000.0,
                frame_width=1.0 / config.SERVO_PWM_FREQUENCY,
                pin_factory=self.pin_factory
            )

        except Exception as error:
            self.cleanup()
            raise RuntimeError(
                f"Could not initialize servo on GPIO{config.SERVO_GPIO}: {error}"
            ) from error

        print("[SERVO] Raspberry Pi 5 GPIO control initialized.")


    def move_to(self, angle):
        angle = float(angle)

        if not 0.0 <= angle <= config.SERVO_MAX_ANGLE:
            raise ValueError(
                f"Servo angle must be between 0 and "
                f"{config.SERVO_MAX_ANGLE} degrees."
            )

        print(f"[SERVO] Moving to {angle} degrees...")

        if config.SIMULATION_MODE:
            time.sleep(config.SERVO_MOVE_TIME)
        else:
            if self.servo is None:
                raise RuntimeError("Servo GPIO is not initialized.")

            self.servo.angle = angle
            time.sleep(config.SERVO_MOVE_TIME)

            # Stop sending PWM after the move; the gear does not need continuous drive.

            self.servo.detach()

        self.current_angle = angle
        print(f"[SERVO] Position reached: {angle} degrees.")


    def unlock(self):
        print("[SERVO] Moving deadbolt to UNLOCK position.")
        self.move_to(config.SERVO_UNLOCK_ANGLE)
        self.position = "UNLOCKED"
        print("[SERVO] Deadbolt UNLOCK position reached.")

    def lock(self):
        print("[SERVO] Moving deadbolt to LOCK position.")
        self.move_to(config.SERVO_LOCK_ANGLE)
        self.position = "LOCKED"
        print("[SERVO] Deadbolt LOCK position reached.")


    def cleanup(self):
        """Release every GPIO resource and report any steps that fail."""
        servo = self.servo
        factory = self.pin_factory
        if servo is None and factory is None:
            return not getattr(self, "cleanup_errors", ())

        # Clear these references so repeated cleanup calls do not close the same resources twice.
        self.servo = None
        self.pin_factory = None
        errors = []
        for resource, method, step in (
            (servo, "detach", "stop PWM"),
            (servo, "close", "close servo output"),
            (factory, "close", "close GPIO factory"),
        ):
            if resource is None:
                continue
            try:
                getattr(resource, method)()
            except Exception as error:
                detail = f"{step}: {type(error).__name__}: {error}"
                errors.append(detail)
                print(f"[SERVO] Cleanup error: {detail}")

        self.cleanup_errors = tuple(errors)
        if errors:
            print("[SERVO] Cleanup finished with errors; GPIO release is not confirmed.")
        else:
            print("[SERVO] GPIO resources released.")
        return not errors
