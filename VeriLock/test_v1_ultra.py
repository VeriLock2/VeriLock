"""Check access with one credential and with Ultra Secure Mode."""

import tempfile
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

import config
from access_control import AccessController
from database import Database
from main import VeriLockApp


class FakeLock:
    def __init__(self):
        self.unlocks = 0
        self.ready = True

    def can_authenticate(self):
        return self.ready

    def unlock_temporarily(self):
        if not self.ready:
            return False
        self.unlocks += 1
        self.ready = False
        return True

    def relock(self):
        self.ready = True


class SecurityFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path_patch = patch.object(
            config, "DATABASE_PATH", str(Path(self.temp.name) / "verilock.db")
        )
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)
        self.db = Database()
        self.alex = self.db.add_user("Alex", rfid_uid="A1", face_id="face-a")
        self.blair = self.db.add_user("Blair", rfid_uid="B2", face_id="face-b")
        self.lock = FakeLock()
        self.access = AccessController(self.db, self.lock)

    def test_default_mode_preserves_single_factor_access(self):
        self.assertFalse(self.db.ultra_secure_enabled())
        self.assertTrue(self.access.authenticate_face(self.alex))
        self.assertEqual(self.lock.unlocks, 1)

    def test_database_connection_closes_before_temporary_directory_cleanup(self):
        with self.db.connect() as connection:
            self.assertIsNotNone(connection.execute("SELECT 1").fetchone())
        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")

    def test_async_grant_is_logged_only_after_lock_finishes(self):
        callbacks = []
        events = []
        self.lock.begin_unlock = lambda done: callbacks.append(done) or True
        self.access.on_grant_complete = lambda *result: events.append(result)
        self.assertFalse(self.access.authenticate_face(self.alex))
        self.assertEqual(self.access.last_result, "ACTUATING")
        self.assertEqual(len(self.db.list_access_log()), 0)
        callbacks[0](True, None)
        self.assertEqual(self.db.list_access_log()[0]["result"], "GRANTED")
        self.assertEqual(events[0][0], True)

    def test_authentication_resumes_only_after_relock_in_both_modes(self):
        self.assertTrue(self.access.authenticate_face(self.alex))
        self.assertFalse(self.access.authenticate_rfid("A1"))
        self.assertEqual(self.access.last_result, "WAIT_FOR_RELOCK")
        self.assertEqual(self.lock.unlocks, 1)
        self.lock.relock()
        self.db.set_ultra_secure_enabled(True)
        self.assertFalse(self.access.authenticate_face(self.alex))
        self.assertTrue(self.access.authenticate_rfid("A1"))
        self.assertEqual(self.lock.unlocks, 2)
        self.assertFalse(self.access.authenticate_face(self.alex))
        self.assertIsNone(self.access.pending_factor)
        self.assertEqual(self.access.last_result, "WAIT_FOR_RELOCK")

    def test_both_orders_and_single_factor_never_unlock(self):
        self.db.set_ultra_secure_enabled(True)
        self.assertFalse(self.access.authenticate_face(self.alex))
        self.assertEqual(self.access.last_result, "PENDING_RFID")
        self.assertEqual(self.lock.unlocks, 0)
        self.assertTrue(self.access.authenticate_rfid("A1"))
        self.assertEqual(self.lock.unlocks, 1)
        self.lock.relock()
        self.assertFalse(self.access.authenticate_rfid("A1"))
        self.assertEqual(self.access.last_result, "PENDING_FACE")
        self.assertTrue(self.access.authenticate_face(self.alex))
        self.assertEqual(self.lock.unlocks, 2)

    def test_mismatch_invalid_card_and_timeout_fail_closed(self):
        self.db.set_ultra_secure_enabled(True)
        self.assertFalse(self.access.authenticate_face(self.alex))
        self.assertFalse(self.access.authenticate_rfid("B2"))
        self.assertEqual(self.lock.unlocks, 0)
        self.assertFalse(self.access.authenticate_face(self.alex))
        self.assertFalse(self.access.reject_rfid("BAD"))
        self.assertFalse(self.access.authenticate_rfid("A1"))
        self.assertEqual(self.lock.unlocks, 0)
        self.access.pending_factor = (self.alex, "RFID", 0)
        self.assertFalse(self.access.authenticate_face(self.alex))
        self.assertEqual(self.access.last_result, "DENIED")
        self.assertEqual(self.lock.unlocks, 0)

    def test_missing_or_revoked_credentials_and_persistence(self):
        self.db.set_ultra_secure_enabled(True)
        self.assertTrue(Database().ultra_secure_enabled())
        self.db.remove_face(self.alex)
        self.assertFalse(self.access.authenticate_rfid("A1"))
        self.assertEqual(self.lock.unlocks, 0)
        self.assertFalse(self.access.authenticate_face(self.blair))
        self.db.disable_user(self.blair)
        self.assertFalse(self.access.authenticate_rfid("B2"))
        self.assertEqual(self.lock.unlocks, 0)

    def test_same_factor_does_not_extend_deadline(self):
        self.db.set_ultra_secure_enabled(True)
        self.access.authenticate_face(self.alex)
        initial = self.access.pending_factor
        self.access.authenticate_face(self.alex)
        self.assertEqual(self.access.pending_factor, initial)

    def test_programmed_card_must_match_its_user_and_face(self):
        self.db.set_ultra_secure_enabled(True)
        self.assertFalse(self.access.authenticate_face(self.alex))
        self.assertFalse(self.access.authenticate_rfid("A1", card_user_id=self.blair))
        self.assertEqual(self.lock.unlocks, 0)
        self.assertFalse(self.access.authenticate_face(self.alex))
        self.assertTrue(self.access.authenticate_rfid("A1", card_user_id=self.alex))
        self.assertEqual(self.lock.unlocks, 1)
