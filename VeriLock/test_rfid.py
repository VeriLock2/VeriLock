"""Hardware-free RFID access tests for the deadbolt and rack-servo architecture.

Run: python3 -m unittest test_rfid
No GPIO, physical card reader, or real enrollment database is opened.
"""
import unittest
from unittest.mock import Mock, patch
import config
from access_control import AccessController
from lock_controller import LockController


class RFIDAccessTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.bolt = Mock()
        self.rack = Mock()
        self.rack.engage.side_effect = lambda: self.events.append("rack engage")
        self.rack.disengage.side_effect = lambda: self.events.append("rack disengage")
        self.bolt.unlock.side_effect = lambda: self.events.append("bolt unlock")
        self.bolt.lock.side_effect = lambda: self.events.append("bolt lock")
        self.lock = LockController(self.bolt, self.rack)
        self.addCleanup(self.lock.cleanup)
        self.db = Mock()
        self.db.ultra_secure_enabled.return_value = False
        self.user = {"id": 1, "name": "Test User", "role": "USER"}
        self.db.get_user_for_rfid_card.return_value = self.user
        self.db.get_user_by_rfid.return_value = self.user
        self.access = AccessController(self.db, self.lock)
        self.duration = patch.object(config, "UNLOCK_DURATION", 3600)
        self.duration.start()
        self.addCleanup(self.duration.stop)

    def test_programmed_card_unlocks_and_manual_relock_uses_both_servos(self):
        self.assertTrue(self.access.authenticate_rfid("AA:BB:CC:DD", card_user_id=1))
        self.db.get_user_for_rfid_card.assert_called_once_with(1, "AA:BB:CC:DD")
        self.assertEqual(self.events, ["rack engage", "bolt unlock", "rack disengage"])
        self.assertFalse(self.lock.can_authenticate())
        self.assertTrue(self.lock.relock_now())
        self.assertEqual(self.events[-3:], ["rack engage", "bolt lock", "rack disengage"])
        self.assertTrue(self.lock.can_authenticate())

    def test_unknown_card_never_moves_either_servo(self):
        self.db.get_user_for_rfid_card.return_value = None
        self.assertFalse(self.access.authenticate_rfid("AA:BB", card_user_id=99))
        self.assertEqual(self.events, [])

    def test_invalid_payload_never_moves_either_servo(self):
        self.access.reject_rfid("AA:BB")
        self.assertEqual(self.events, [])

    def test_second_card_cannot_repeat_unlock_before_relock(self):
        self.assertTrue(self.access.authenticate_rfid("AA:BB", card_user_id=1))
        timer = self.lock.relock_timer
        self.assertFalse(self.access.authenticate_rfid("AA:BB", card_user_id=1))
        self.assertIs(self.lock.relock_timer, timer)
        self.bolt.unlock.assert_called_once()

    def test_rack_failure_prevents_deadbolt_movement(self):
        self.rack.engage.side_effect = RuntimeError("rack jam")
        self.assertFalse(self.access.authenticate_rfid("AA:BB", card_user_id=1))
        self.bolt.unlock.assert_not_called()
        self.rack.disengage.assert_called_once()
        self.assertFalse(self.lock.can_authenticate())


if __name__ == "__main__":
    unittest.main()
