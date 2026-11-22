"""Check the command-line RFID enrollment process from start to finish."""

import io
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

import config
import enroll_rfid
from database import Database
from rfid import RFIDReader


class FakePN532:
    def __init__(self, card_reads=(), error=None):
        self.card_reads = list(card_reads)
        self.error = error
        self.read_calls = 0
        self.blocks = {}
        self.write_calls = []

    def read_passive_target(self, timeout):
        self.read_calls += 1
        if self.error is not None:
            raise self.error
        if self.card_reads:
            return self.card_reads.pop(0)
        return None

    def mifare_classic_authenticate_block(
        self, uid, block_number, key_number, key
    ):
        return True

    def mifare_classic_write_block(self, block_number, data):
        self.write_calls.append((block_number, bytes(data)))
        self.blocks[block_number] = bytes(data)
        return True

    def mifare_classic_read_block(self, block_number):
        return self.blocks.get(block_number, bytes(16))


class EnrollRFIDTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        database_path = Path(self.temp_dir.name) / "verilock-test.db"
        with patch.object(config, "DATABASE_PATH", str(database_path)):
            self.database = Database()

    def add_user(self, name, rfid_uid=None):
        output = io.StringIO()
        with redirect_stdout(output):
            return self.database.add_user(
                name,
                rfid_uid=rfid_uid,
            )

    def make_reader(self, card_reads=(), error=None):
        pn532 = FakePN532(card_reads=card_reads, error=error)
        reader = RFIDReader.__new__(RFIDReader)
        reader.pn532 = pn532
        reader.i2c = Mock()
        reader.reset_pin = Mock()
        reader.request_pin = Mock()
        return reader, pn532

    def run_cli(self, reader=None, answers=(), simulation=True, timeout=None):
        output = io.StringIO()
        patches = [
            patch("enroll_rfid.Database", return_value=self.database),
            patch("builtins.input", side_effect=list(answers)),
            patch.object(config, "SIMULATION_MODE", simulation),
        ]

        if reader is not None:
            patches.append(
                patch("enroll_rfid.RFIDReader", return_value=reader)
            )
            patches.append(patch("rfid.time.sleep"))
        if timeout is not None:
            patches.append(
                patch.object(
                    enroll_rfid,
                    "CARD_WAIT_TIMEOUT_SECONDS",
                    timeout,
                )
            )

        with redirect_stdout(output), ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            enroll_rfid.main()

        return output.getvalue()

    def test_simulation_enrollment_assigns_normalized_uid(self):
        user_id = self.add_user("Ada")

        output = self.run_cli(
            answers=[str(user_id), "aabbccdd"],
            simulation=True,
        )

        self.assertEqual(
            self.database.get_user_by_id(user_id)["rfid_uid"],
            "AA:BB:CC:DD",
        )
        self.assertIn("Simulated RFID enrollment complete", output)

    def test_simulation_rejects_uid_assigned_to_another_user(self):
        target_id = self.add_user("Target")
        owner_id = self.add_user("Owner", "AA:BB:CC:DD")

        output = self.run_cli(
            answers=[str(target_id), "aabbccdd"],
            simulation=True,
        )

        self.assertIsNone(
            self.database.get_user_by_id(target_id)["rfid_uid"]
        )
        self.assertEqual(
            self.database.get_user_by_id(owner_id)["rfid_uid"],
            "AA:BB:CC:DD",
        )
        self.assertIn("Card already belongs to", output)

    def test_simulation_rejects_invalid_uid(self):
        user_id = self.add_user("Mina")

        output = self.run_cli(
            answers=[str(user_id), "not-a-card"],
            simulation=True,
        )

        self.assertIsNone(
            self.database.get_user_by_id(user_id)["rfid_uid"]
        )
        self.assertIn("Invalid RFID UID", output)

    def test_physical_enrollment_polls_until_card_then_writes_and_cleans_up(self):
        user_id = self.add_user("Grace")
        reader, pn532 = self.make_reader(
            card_reads=[None, bytes([1, 2, 3, 4])]
        )
        request_pin = reader.request_pin
        reset_pin = reader.reset_pin
        i2c = reader.i2c

        output = self.run_cli(
            reader=reader,
            answers=[str(user_id), "YES"],
            simulation=False,
        )

        self.assertEqual(pn532.read_calls, 2)
        self.assertEqual(len(pn532.write_calls), 1)
        block_number, payload = pn532.write_calls[0]
        self.assertEqual(block_number, config.RFID_DATA_BLOCK)
        self.assertEqual(
            RFIDReader.__new__(RFIDReader)
            .parse_verilock_payload(payload)["user_id"],
            user_id,
        )
        self.assertEqual(
            self.database.get_user_by_id(user_id)["rfid_uid"],
            "01:02:03:04",
        )
        self.assertIn("ENROLLMENT COMPLETE", output)
        self.assertTrue(request_pin.deinit.called)
        self.assertTrue(reset_pin.deinit.called)
        self.assertTrue(i2c.deinit.called)
        self.assertIsNone(reader.pn532)

    def test_physical_timeout_leaves_database_unchanged_and_cleans_up(self):
        user_id = self.add_user("Lin")
        reader, pn532 = self.make_reader()
        request_pin = reader.request_pin
        reset_pin = reader.reset_pin
        i2c = reader.i2c

        output = self.run_cli(
            reader=reader,
            answers=[str(user_id)],
            simulation=False,
            timeout=0,
        )

        self.assertEqual(pn532.read_calls, 1)
        self.assertIsNone(
            self.database.get_user_by_id(user_id)["rfid_uid"]
        )
        self.assertIn("No card detected before timeout", output)
        self.assertTrue(request_pin.deinit.called)
        self.assertTrue(reset_pin.deinit.called)
        self.assertTrue(i2c.deinit.called)

    def test_physical_reader_error_still_cleans_up(self):
        user_id = self.add_user("Katherine")
        reader, _ = self.make_reader(error=OSError("PN532 read failed"))
        request_pin = reader.request_pin
        reset_pin = reader.reset_pin
        i2c = reader.i2c

        output = self.run_cli(
            reader=reader,
            answers=[str(user_id)],
            simulation=False,
        )

        self.assertIn("Physical enrollment failed: PN532 read failed", output)
        self.assertIsNone(
            self.database.get_user_by_id(user_id)["rfid_uid"]
        )
        self.assertTrue(request_pin.deinit.called)
        self.assertTrue(reset_pin.deinit.called)
        self.assertTrue(i2c.deinit.called)


if __name__ == "__main__":
    unittest.main()
