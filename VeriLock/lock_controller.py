"""Handle lock movements one at a time, and wait for relocking before accepting another scan."""

import time
import threading

import config

class LockController:

    def __init__(
        self,
        servo,
        rack_servo,
        autolock_enabled=True
    ):

        self.servo = servo
        self.rack_servo = rack_servo

        self.state = "LOCKED"  # main.py locks at startup. This is the commanded state; there is no position sensor.
        self.autolock_enabled = bool(autolock_enabled)
        self.relock_timer = None
        # Manual commands and autolock share this lock so their movements cannot overlap.
        self.operation_lock = threading.RLock()
        self.stopping = threading.Event()
        # Let the screen read the status while the servos are moving.
        self.state_lock = threading.RLock()
        self.relock_generation = 0
        # main.py completes startup locking before scans begin. After an unlock,
        # wait for a successful relock before accepting another scan.
        self.authentication_ready = True

        print(
            "[LOCK] Lock controller initialized."
        )


    def move_lock(
        self,
        target
    ):
        # Do not let two motor movements run at once.
        with self.operation_lock:
            if self.stopping.is_set():
                raise RuntimeError("Lock controller is shutting down.")
            if target not in ("LOCK", "UNLOCK"):
                raise ValueError(f"Unknown lock target: {target}")
            with self.state_lock:
                self.state = "MOVING"
                self.authentication_ready = False
            try:
                self.rack_servo.engage()


                if target == "LOCK":
                    self.servo.lock()

                elif target == "UNLOCK":
                    self.servo.unlock()

                else:
                    raise ValueError(
                        f"Unknown lock target: {target}"
                    )

            except Exception:
                with self.state_lock:
                    self.state = "UNKNOWN"
                    self.authentication_ready = False
                raise
            finally:
                # Pull the rack out after each attempt, even if the deadbolt servo fails.
                try:
                    self.rack_servo.disengage()
                except Exception:
                    with self.state_lock:
                        self.state = "UNKNOWN"
                        self.authentication_ready = False
                    raise
            with self.state_lock:
                self.state = "LOCKED" if target == "LOCK" else "UNLOCKED"


    def unlock(self):

        print()
        print("==============================")
        print("       UNLOCK SEQUENCE")
        print("==============================")

        self.move_lock(
            "UNLOCK"
        )

        print()
        print(
            "[LOCK] Door is UNLOCKED."
        )


    def lock(self):

        print()
        print("==============================")
        print("        LOCK SEQUENCE")
        print("==============================")

        self.move_lock(
            "LOCK"
        )

        # The rack is disengaged, so scans can resume.
        with self.state_lock:
            self.authentication_ready = True

        print()
        print(
            "[LOCK] Door is LOCKED."
        )


    def can_authenticate(self):
        with self.state_lock:
            return self.authentication_ready

    def begin_unlock(self, completed):
        """Accept one access request and move the lock in a background thread."""
        with self.operation_lock:
            with self.state_lock:
                if self.stopping.is_set() or not self.authentication_ready:
                    return False
                self.authentication_ready = False
            self.relock_generation += 1
            generation = self.relock_generation
            if self.relock_timer is not None:
                self.relock_timer.cancel()
                self.relock_timer = None
            worker = threading.Thread(
                target=self._background_unlock, args=(generation, completed),
                daemon=True, name="verilock-lock",
            )
            worker.start()
            return True

    def _background_unlock(self, generation, completed):
        try:
            with self.operation_lock:
                if self.stopping.is_set() or generation != self.relock_generation:
                    return
                self.unlock()
                if self.stopping.is_set():
                    return
                self._schedule_relock(generation)
        except Exception as error:
            # A failed movement or timer setup may leave the lock between positions.
            # Keep scans blocked until the mechanism has been checked.
            print("[LOCK] Movement failed; manual inspection required before retry.")
            completed(False, error)
        else:
            completed(True, None)

    def unlock_temporarily(self):
        """Unlock now and start the relock timer if autolock is on."""
        with self.operation_lock:
            if self.stopping.is_set() or not self.authentication_ready:
                print("[LOCK] Authentication blocked until relock completes.")
                return False
            with self.state_lock:
                self.authentication_ready = False
            self.relock_generation += 1
            generation = self.relock_generation
            if self.relock_timer is not None:
                self.relock_timer.cancel()
                self.relock_timer = None

            try:
                self.unlock()
            except Exception:
                # The bolt may have moved partway before an unlock error.
                # Keep authentication blocked until the mechanism is checked.
                print("[LOCK] Movement failed; manual inspection required.")
                raise
            try:
                self._schedule_relock(generation)
            except Exception:
                self.relock_timer = None
                # If the timer cannot start, do not leave the bolt unlocked
                # without trying to lock it again.
                self.lock()
                raise
            return True

    def _schedule_relock(self, generation):
        # Call this while holding operation_lock. Scans stay blocked until relocking finishes.
        if not self.autolock_enabled or self.stopping.is_set():
            return
        timer = threading.Timer(
            config.UNLOCK_DURATION, self._automatic_relock, args=(generation,)
        )
        timer.daemon = True
        self.relock_timer = timer
        timer.start()
        print(f"[LOCK] Door will relock in {config.UNLOCK_DURATION} seconds.")

    def set_autolock_enabled(self, enabled, persist=None):
        # Wait for any movement to finish before changing the autolock setting.
        with self.operation_lock:
            if self.stopping.is_set():
                raise RuntimeError("Lock controller is shutting down.")
            enabled = bool(enabled)
            if persist is not None:
                persist(enabled)
            if enabled == self.autolock_enabled:
                return enabled
            self.autolock_enabled = enabled
            if self.relock_timer is not None:
                # Cancel the old countdown without canceling an unlock request
                # that is still waiting to start.
                self.relock_generation += 1
                self.relock_timer.cancel()
                self.relock_timer = None
            with self.state_lock:
                unlocked = self.state == "UNLOCKED"
            if enabled and unlocked:
                self._schedule_relock(self.relock_generation)
            return enabled

    def _automatic_relock(self, generation):
        with self.operation_lock:
            if (self.stopping.is_set() or generation != self.relock_generation
                    or not self.autolock_enabled):
                return
            self.relock_timer = None
            try:
                print("[LOCK] Automatic relock timer expired.")
                with self.state_lock:
                    self.authentication_ready = False
                self.lock()
            except Exception as error:
                print(f"[LOCK] Automatic relock failed: {error}")

    def relock_now(self):
        """Run an authorized manual lock command when the mechanism is free."""
        if not self.operation_lock.acquire(blocking=False):
            return False
        try:
            if self.stopping.is_set():
                return False
            with self.state_lock:
                self.authentication_ready = False
            self.relock_generation += 1
            if self.relock_timer is not None:
                self.relock_timer.cancel()
                self.relock_timer = None
            self.lock()
            return True
        finally:
            self.operation_lock.release()

    def request_stop(self):
        self.stopping.set()
        with self.state_lock:
            self.authentication_ready = False
        self.relock_generation += 1
        if self.relock_timer is not None:
            self.relock_timer.cancel()
            self.relock_timer = None

    def cleanup(self, timeout=0.1):
        self.request_stop()
        if not self.operation_lock.acquire(timeout=timeout):
            print("[LOCK] Movement still running; shutdown will not wait indefinitely.")
            return False
        self.operation_lock.release()
        return True
