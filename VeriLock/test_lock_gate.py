"""Check servo order, movement failures, and when another scan is allowed."""

import threading
import unittest
import contextlib
import io
import sys
import types
from unittest.mock import Mock, patch

import config
from access_control import AccessController
from lock_controller import LockController
from servo import Servo
from rack_servo import RackServo


class ServoCleanupTests(unittest.TestCase):
    def make_driver(self, driver_type):
        driver = driver_type.__new__(driver_type)
        driver.servo = Mock()
        driver.pin_factory = Mock()
        calls = Mock()
        calls.attach_mock(driver.servo, "servo")
        calls.attach_mock(driver.pin_factory, "factory")
        return driver, calls

    def test_cleanup_tries_all_steps_even_when_one_fails(self):
        for driver_type in (Servo, RackServo):
            for failure in ("detach", "servo_close", "factory_close"):
                with self.subTest(driver=driver_type.__name__, failure=failure):
                    driver, calls = self.make_driver(driver_type)
                    failed_step = {
                        "detach": driver.servo.detach,
                        "servo_close": driver.servo.close,
                        "factory_close": driver.pin_factory.close,
                    }[failure]
                    failed_step.side_effect = RuntimeError("bad PWM micros")
                    output = io.StringIO()
                    with contextlib.redirect_stdout(output):
                        self.assertFalse(driver.cleanup())
                    self.assertEqual([str(call) for call in calls.mock_calls],
                                     ["call.servo.detach()", "call.servo.close()", "call.factory.close()"])
                    self.assertIn("bad PWM micros", output.getvalue())
                    self.assertIn("not confirmed", output.getvalue())
                    self.assertNotIn("GPIO resources released", output.getvalue())
                    self.assertIsNone(driver.servo)
                    self.assertIsNone(driver.pin_factory)
                    self.assertEqual(len(driver.cleanup_errors), 1)
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertFalse(driver.cleanup())
                    self.assertEqual(len(calls.mock_calls), 3)

    def test_successful_cleanup_can_be_called_twice(self):
        for driver_type in (Servo, RackServo):
            with self.subTest(driver=driver_type.__name__):
                driver, calls = self.make_driver(driver_type)
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertTrue(driver.cleanup())
                    self.assertTrue(driver.cleanup())
                self.assertEqual(len(calls.mock_calls), 3)
                self.assertEqual(driver.cleanup_errors, ())

    def test_cleanup_reports_all_failures(self):
        for driver_type in (Servo, RackServo):
            driver, calls = self.make_driver(driver_type)
            driver.servo.detach.side_effect = RuntimeError("detach failed")
            driver.servo.close.side_effect = RuntimeError("output close failed")
            driver.pin_factory.close.side_effect = RuntimeError("factory close failed")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertFalse(driver.cleanup())
            self.assertEqual(len(driver.cleanup_errors), 3)
            self.assertEqual(len(calls.mock_calls), 3)

    def test_partial_initialization_still_closes_factory(self):
        for driver_type in (Servo, RackServo):
            driver, calls = self.make_driver(driver_type)
            driver.servo = None
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(driver.cleanup())
            self.assertEqual([str(call) for call in calls.mock_calls], ["call.factory.close()"])

    def test_cleanup_uses_owned_resources_even_if_simulation_flag_changes(self):
        for driver_type in (Servo, RackServo):
            driver, calls = self.make_driver(driver_type)
            with patch.object(config, "SIMULATION_MODE", True), contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(driver.cleanup())
            self.assertEqual(len(calls.mock_calls), 3)

    def test_simulation_without_resources_needs_no_gpio(self):
        for driver_type in (Servo, RackServo):
            with patch.object(config, "SIMULATION_MODE", True), contextlib.redirect_stdout(io.StringIO()):
                driver = driver_type()
                self.assertTrue(driver.cleanup())
                self.assertTrue(driver.cleanup())

    def test_initialization_error_is_preserved_when_factory_cleanup_also_fails(self):
        for driver_type in (Servo, RackServo):
            with self.subTest(driver=driver_type.__name__):
                factory = Mock()
                factory.close.side_effect = RuntimeError("factory cleanup failed")
                gpiozero = types.ModuleType("gpiozero")
                gpiozero.AngularServo = Mock(side_effect=ValueError("servo creation failed"))
                pins = types.ModuleType("gpiozero.pins")
                lgpio = types.ModuleType("gpiozero.pins.lgpio")
                lgpio.LGPIOFactory = Mock(return_value=factory)
                with patch.dict(sys.modules, {"gpiozero": gpiozero, "gpiozero.pins": pins,
                                              "gpiozero.pins.lgpio": lgpio}), \
                        patch.object(config, "SIMULATION_MODE", False), \
                        patch.object(config, "RACK_SERVO_CALIBRATED", True), \
                        contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaisesRegex(Exception, "servo creation failed"):
                        driver_type()
                factory.close.assert_called_once()


class FakeServo:
    def __init__(self):
        self.unlocks = 0
        self.locks = 0
        self.relocked = threading.Event()
        self.fail_relock = False

    def unlock(self):
        self.unlocks += 1

    def lock(self):
        self.locks += 1
        if self.fail_relock:
            raise RuntimeError("servo stalled")
        self.relocked.set()


class FakeRackServo:
    def __init__(self):
        self.engaged = False

    def engage(self):
        self.engaged = True

    def disengage(self):
        self.engaged = False


class LockGateTests(unittest.TestCase):
    def test_background_actuation_failure_disengages_and_blocks_access(self):
        servo = FakeServo()

        def fail_unlock():
            raise RuntimeError("servo jam")

        servo.unlock = fail_unlock
        rack = FakeRackServo()
        lock = LockController(servo, rack)
        finished = threading.Event()
        results = []
        self.assertTrue(lock.begin_unlock(
            lambda ok, error: (results.append((ok, error)), finished.set())
        ))
        self.assertTrue(finished.wait(2))
        self.assertFalse(results[0][0])
        self.assertIsInstance(results[0][1], RuntimeError)
        self.assertEqual(servo.locks, 0)  # Do not try another motor movement after a jam.
        self.assertFalse(rack.engaged)
        self.assertEqual(lock.state, "UNKNOWN")
        self.assertFalse(lock.can_authenticate())
        self.assertIsNone(lock.relock_timer)
        lock.cleanup()

    def test_background_unlock_reserves_gate_without_waiting_for_servo(self):
        servo = FakeServo()
        rack = FakeRackServo()
        order = []
        moving = threading.Event()
        release = threading.Event()
        finished = threading.Event()
        original_unlock = servo.unlock
        original_engage = rack.engage
        original_disengage = rack.disengage

        def engage():
            order.append("engage")
            original_engage()

        def disengage():
            order.append("disengage")
            original_disengage()

        def slow_unlock():
            order.append("servo")
            moving.set()
            self.assertTrue(release.wait(2))
            original_unlock()

        servo.unlock = slow_unlock
        rack.engage = engage
        rack.disengage = disengage
        lock = LockController(servo, rack)
        results = []
        with patch.object(config, "UNLOCK_DURATION", 10):
            self.assertTrue(lock.begin_unlock(lambda ok, error: (results.append(ok), finished.set())))
            self.assertTrue(moving.wait(2))
            self.assertFalse(lock.can_authenticate())
            # This read must not wait for the stalled movement to complete.
            self.assertFalse(lock.can_authenticate())
            release.set()
            self.assertTrue(finished.wait(2))
            self.assertEqual(results, [True])
            self.assertEqual(servo.unlocks, 1)
            self.assertEqual(order, ["engage", "servo", "disengage"])
            lock.cleanup()

    def test_both_directions_engage_before_bolt_and_release_after(self):
        for target, method in (("LOCK", "lock"), ("UNLOCK", "unlock")):
            with self.subTest(target=target):
                events = []
                servo = FakeServo()
                rack = FakeRackServo()
                rack.engage = lambda: events.append("engage")
                rack.disengage = lambda: events.append("disengage")
                setattr(servo, method, lambda: events.append("bolt"))
                controller = LockController(servo, rack)
                try:
                    controller.move_lock(target)
                    self.assertEqual(events, ["engage", "bolt", "disengage"])
                finally:
                    controller.cleanup()

    def test_failed_engagement_never_moves_deadbolt(self):
        servo = FakeServo()
        rack = Mock()
        rack.engage.side_effect = RuntimeError("rack stalled")
        controller = LockController(servo, rack)
        try:
            with self.assertRaisesRegex(RuntimeError, "rack stalled"):
                controller.move_lock("UNLOCK")
            self.assertEqual(servo.unlocks, 0)
            rack.disengage.assert_called_once_with()
            self.assertFalse(controller.can_authenticate())
        finally:
            controller.cleanup()

    def test_failed_disengagement_leaves_state_unknown(self):
        rack = Mock()
        rack.disengage.side_effect = RuntimeError("rack stuck")
        controller = LockController(FakeServo(), rack)
        try:
            with self.assertRaisesRegex(RuntimeError, "rack stuck"):
                controller.move_lock("UNLOCK")
            self.assertEqual(controller.state, "UNKNOWN")
            self.assertFalse(controller.can_authenticate())
        finally:
            controller.cleanup()

    def test_face_and_rfid_share_the_same_lock_gate(self):
        lock = LockController(FakeServo(), FakeRackServo())
        db = Mock()
        db.ultra_secure_enabled.return_value = False
        user = {"id": 1, "name": "Alex", "role": "USER"}
        db.get_user_by_id.return_value = user
        db.get_user_by_rfid.return_value = user
        access = AccessController(db, lock)
        with patch.object(config, "UNLOCK_DURATION", 10):
            self.assertTrue(access.authenticate_face(1))
            timer = lock.relock_timer
            self.assertFalse(access.authenticate_rfid("A1"))
            self.assertEqual(access.last_result, "WAIT_FOR_RELOCK")
            self.assertIs(lock.relock_timer, timer)
            lock.lock()
            self.assertTrue(access.authenticate_rfid("A1"))
            lock.cleanup()

    def test_second_authentication_waits_for_completed_relock(self):
        servo = FakeServo()
        lock = LockController(servo, FakeRackServo())
        with patch.object(config, "UNLOCK_DURATION", 0.03):
            self.assertTrue(lock.can_authenticate())
            self.assertTrue(lock.unlock_temporarily())
            first_timer = lock.relock_timer
            self.assertFalse(lock.can_authenticate())
            self.assertFalse(lock.unlock_temporarily())
            self.assertIs(lock.relock_timer, first_timer)
            self.assertEqual(servo.unlocks, 1)
            self.assertTrue(servo.relocked.wait(1.0))
            self.assertTrue(lock.can_authenticate())
            self.assertTrue(lock.unlock_temporarily())
            self.assertEqual(servo.unlocks, 2)
            lock.cleanup()

    def test_failed_relock_keeps_authentication_blocked(self):
        servo = FakeServo()
        servo.fail_relock = True
        lock = LockController(servo, FakeRackServo())
        with patch.object(config, "UNLOCK_DURATION", 0.03):
            self.assertTrue(lock.unlock_temporarily())
            # Wait for the attempted relock, then synchronize with its lock.
            for _ in range(100):
                with lock.operation_lock:
                    if servo.locks:
                        break
                threading.Event().wait(0.01)
            self.assertEqual(servo.locks, 1)
            self.assertFalse(lock.can_authenticate())
            self.assertFalse(lock.unlock_temporarily())
            servo.fail_relock = False
            lock.lock()
            self.assertTrue(lock.can_authenticate())
            lock.cleanup()


if __name__ == "__main__":
    unittest.main()
