"""Control the SER0057 rack servo. Check its positions before enabling hardware mode."""
import time
import config

class RackServo:
    def __init__(self):
        self.servo = None
        self.pin_factory = None
        self.engaged = False
        if config.SIMULATION_MODE:
            return
        if not config.RACK_SERVO_CALIBRATED:
            raise RuntimeError("Rack servo calibration required: set verified angles and RACK_SERVO_CALIBRATED=True")
        from gpiozero import AngularServo
        from gpiozero.pins.lgpio import LGPIOFactory
        self.pin_factory = LGPIOFactory()
        try:
            self.servo = AngularServo(
                config.RACK_SERVO_GPIO, initial_angle=None, min_angle=0,
                max_angle=config.RACK_SERVO_MAX_ANGLE,
                min_pulse_width=config.RACK_SERVO_MIN_PULSE_US / 1_000_000,
                max_pulse_width=config.RACK_SERVO_MAX_PULSE_US / 1_000_000,
                frame_width=1 / config.RACK_SERVO_PWM_FREQUENCY,
                pin_factory=self.pin_factory)
        except Exception:
            self.cleanup()
            raise

    def _move(self, angle, delay):
        if not config.SIMULATION_MODE:
            if self.servo is None:
                raise RuntimeError("Rack servo is not initialized")
            self.servo.angle = angle
        time.sleep(delay)

    def engage(self):
        self._move(config.RACK_SERVO_ENGAGED_ANGLE, config.RACK_SERVO_MOVE_TIME)
        self.engaged = True

    def disengage(self):
        self._move(config.RACK_SERVO_DISENGAGED_ANGLE, config.RACK_SERVO_RELEASE_TIME)
        self.engaged = False
        # Leave PWM active here if the rack needs holding force against the spring.

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
                print(f"[RACK SERVO] Cleanup error: {detail}")

        self.cleanup_errors = tuple(errors)
        if errors:
            print("[RACK SERVO] Cleanup finished with errors; GPIO release is not confirmed.")
        else:
            print("[RACK SERVO] GPIO resources released.")
        return not errors
