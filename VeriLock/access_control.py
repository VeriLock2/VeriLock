"""Check face and RFID credentials and request an unlock when access is allowed."""

from datetime import datetime
import time
import config

class AccessController:

    def __init__(
        self,
        database,
        lock_controller
    ):

        self.database = database
        self.lock_controller = lock_controller
        self.pending_factor = None
        self.last_result = "IDLE"
        # The GUI sets a completion callback; CLI callers stay synchronous.

        self.on_grant_complete = None

        print(
            "[ACCESS] Access controller initialized."
        )


    def authenticate_face(
        self,
        user_id
    ):

        print()
        print(
            "[ACCESS] Checking face authentication..."
        )


        user = (
            self.database
            .get_user_by_id(
                user_id
            )
        )


        if user is None:

            self.clear_pending()

            self._access_denied(
                method="FACE",
                identifier=str(
                    user_id
                )
            )

            return False


        return self._verified_factor(user, "FACE")


    def reject_rfid(self, uid):
        """Reject and log a physical card with a missing or invalid payload."""
        self.clear_pending()
        self._access_denied(method="RFID", identifier=str(uid))
        return False

    def authenticate_rfid(
        self,
        uid,
        card_user_id=None
    ):

        print()
        print(
            "[ACCESS] Checking RFID authentication..."
        )


        if uid is None:

            self.clear_pending()

            self._access_denied(
                method="RFID",
                identifier="NONE"
            )

            return False

        uid = (
            str(uid)
            .strip()
            .upper()
        )

        if not uid:

            self.clear_pending()

            self._access_denied(
                method="RFID",
                identifier="EMPTY"
            )

            return False


        if card_user_id is not None:

            print(
                "[ACCESS] VeriLock programmed "
                "card detected."
            )

            print(
                f"[ACCESS] UID: {uid}"
            )

            print(
                f"[ACCESS] Card User ID: "
                f"{card_user_id}"
            )


            user = (
                self.database
                .get_user_for_rfid_card(
                    card_user_id,
                    uid
                )
            )

            if user is None:

                self.clear_pending()

                print(
                    "[ACCESS] UID/User ID "
                    "verification failed."
                )

                self._access_denied(
                    method="RFID",
                    identifier=uid
                )

                return False

            print(
                "[ACCESS] UID/User ID "
                "verification successful."
            )


        else:

            print(
                "[ACCESS] UID-only RFID "
                "authentication."
            )

            user = (
                self.database
                .get_user_by_rfid(
                    uid
                )
            )

            if user is None:

                self.clear_pending()

                self._access_denied(
                    method="RFID",
                    identifier=uid
                )

                return False


        return self._verified_factor(user, "RFID")

    def clear_pending(self):
        self.pending_factor = None
        self.last_result = "IDLE"

    def pending_seconds_remaining(self):
        if self.pending_factor is None:
            return 0.0
        return max(0.0, self.pending_factor[2] - time.monotonic())

    def _verified_factor(self, user, method):
        # Reject authentication while the lock is unlocked or relocking.
        if not self.lock_controller.can_authenticate():
            self.clear_pending()
            self.last_result = "WAIT_FOR_RELOCK"
            self._log_attempt(user=user, method=method, result="LOCK_BUSY")
            return False
        # Read the database on each attempt because admin.py runs separately.
        if not self.database.ultra_secure_enabled():
            self.clear_pending()
            granted = self._access_granted(user, method)
            if granted:
                self.last_result = "GRANTED"
            elif self.last_result not in ("WAIT_FOR_RELOCK", "ACTUATING"):
                self.last_result = "ACTUATION_FAILED"
            return granted

        # Both factors must belong to the same enabled user.
        if not user["face_id"] or not user["rfid_uid"]:
            self.clear_pending()
            self._access_denied(method, "BOTH_CREDENTIALS_REQUIRED")
            self.last_result = "DENIED"
            return False

        now = time.monotonic()
        pending = self.pending_factor
        if pending is not None:
            old_user_id, old_method, deadline = pending
            if now > deadline:
                self.clear_pending()
                self._access_denied("FACE+RFID", "FACTOR_TIMEOUT")
                self.last_result = "DENIED"
                return False
            if old_user_id != user["id"]:
                self.clear_pending()
                self._access_denied("FACE+RFID", "USER_MISMATCH")
                self.last_result = "DENIED"
                return False
            if old_method == method:
                # Repeated scans of one factor do not extend the pairing deadline.
                self.last_result = (
                    "PENDING_RFID" if method == "FACE" else "PENDING_FACE"
                )
                return False
            if old_method != method:
                self.clear_pending()
                # Recheck the account status and credentials before granting access.
                current = self.database.get_user_by_id(user["id"])
                if (current is None or not current["face_id"] or
                        not current["rfid_uid"] or
                        current["face_id"] != user["face_id"] or
                        current["rfid_uid"] != user["rfid_uid"]):
                    self._access_denied("FACE+RFID", "CREDENTIAL_CHANGED")
                    self.last_result = "DENIED"
                    return False
                granted = self._access_granted(current, "FACE+RFID")
                if granted:
                    self.last_result = "GRANTED"
                elif self.last_result not in ("WAIT_FOR_RELOCK", "ACTUATING"):
                    self.last_result = "ACTUATION_FAILED"
                return granted

        self.pending_factor = (
            user["id"], method, now + config.ULTRA_SECURE_FACTOR_TIMEOUT
        )
        self.last_result = "PENDING_RFID" if method == "FACE" else "PENDING_FACE"
        print(f"[ACCESS] {method} verified; waiting for other factor.")
        return False


    def _access_granted(
        self,
        user,
        method
    ):

        if self.on_grant_complete is not None:
            def completed(success, error):
                if not success:
                    print(f"[ACCESS] Lock actuation failed: {error}")
                try:
                    self._log_attempt(
                        user=user, method=method,
                        result="GRANTED" if success else "ACTUATION_FAILED",
                    )
                finally:
                    self.on_grant_complete(success, user, method)

            try:
                accepted = self.lock_controller.begin_unlock(completed)
            except Exception as error:
                print(f"[ACCESS] Could not start lock worker: {error}")
                self.last_result = "ACTUATION_FAILED"
                self._log_attempt(user=user, method=method, result="ACTUATION_FAILED")
                return False
            if not accepted:
                self.last_result = "WAIT_FOR_RELOCK"
                self._log_attempt(user=user, method=method, result="LOCK_BUSY")
                return False
            self.last_result = "ACTUATING"
            return False

        try:
            if not self.lock_controller.unlock_temporarily():
                self.last_result = "WAIT_FOR_RELOCK"
                self._log_attempt(user=user, method=method, result="LOCK_BUSY")
                return False
        except Exception as error:
            print(f"[ACCESS] Lock actuation failed: {error}")
            self.last_result = "ACTUATION_FAILED"
            self._log_attempt(user=user, method=method, result="ACTUATION_FAILED")
            return False

        print()
        print(
            "================================"
        )

        print(
            "         ACCESS GRANTED"
        )

        print(
            "================================"
        )


        try:

            name = user["name"]

        except (KeyError, IndexError):

            name = "UNKNOWN"

        print(
            f"User: {name}"
        )


        try:

            user_id = user["id"]

        except (KeyError, IndexError):

            user_id = None

        if user_id is not None:

            print(
                f"User ID: {user_id}"
            )


        try:

            role = user["role"]

        except (KeyError, IndexError):

            role = "USER"

        print(
            f"Role: {role}"
        )


        print(
            f"Method: {method}"
        )

        print(
            "================================"
        )

        self._log_attempt(user=user, method=method, result="GRANTED")
        return True


    def _access_denied(
        self,
        method,
        identifier=None
    ):

        print()
        print(
            "================================"
        )

        print(
            "         ACCESS DENIED"
        )

        print(
            "================================"
        )

        print(
            f"Method: {method}"
        )

        if identifier:

            print(
                f"Identifier: {identifier}"
            )

        print(
            "================================"
        )


        self._log_attempt(
            user=None,
            method=method,
            result="DENIED"
        )


    def _log_attempt(
        self,
        user,
        method,
        result
    ):

        timestamp = (
            datetime.now()
            .strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        )


        if user is None:

            user_name = "UNKNOWN"

            user_id = None


        else:

            try:

                user_name = (
                    user["name"]
                )

            except (KeyError, IndexError):

                user_name = "UNKNOWN"

            try:

                user_id = (
                    user["id"]
                )

            except (KeyError, IndexError):

                user_id = None


        print(
            f"[LOG] {timestamp} | "
            f"{method} | "
            f"{user_name} | "
            f"{result}"
        )


        try:

            self.database.log_access(
                user_id=user_id,
                method=method,
                result=result
            )

        except Exception as error:

            # Do not let a logging error interrupt lock control.


            print(
                "[WARNING] Could not save "
                "access log:"
            )

            print(
                f"          {error}"
            )
