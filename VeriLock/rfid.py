"""PN532 reader and MIFARE card operations for VeriLock."""

import struct
import time

import config


class RFIDReader:

    def __init__(self):
        self.pn532 = None
        self.i2c = None
        self.reset_pin = None
        self.request_pin = None

        print("[RFID] Initializing RFID reader...")

        if config.SIMULATION_MODE:
            print("[RFID] Simulation mode active.")
        else:
            self.initialize_hardware()


    def initialize_hardware(self):
        try:
            import board
            import busio
            from digitalio import DigitalInOut
            from adafruit_pn532.i2c import PN532_I2C

        except ImportError as error:
            raise RuntimeError(
                "Install Blinka and adafruit-circuitpython-pn532 "
                "before starting VeriLock."
            ) from error

        try:


            self.i2c = busio.I2C(
                board.SCL,
                board.SDA
            )


            self.reset_pin = DigitalInOut(
                getattr(
                    board,
                    f"D{config.RFID_RESET_GPIO}"
                )
            )


            self.request_pin = DigitalInOut(
                getattr(
                    board,
                    f"D{config.RFID_REQUEST_GPIO}"
                )
            )


            self.pn532 = PN532_I2C(
                self.i2c,
                debug=False,
                reset=self.reset_pin,
                req=self.request_pin
            )


            ic, version, revision, support = self.pn532.firmware_version


            self.pn532.SAM_configuration()

            print(
                f"[RFID] PN532 initialized over I2C. "
                f"Firmware {version}.{revision}"
            )

        except Exception as error:
            # Clean up anything opened before setup failed.

            self.cleanup()

            raise RuntimeError(
                f"Could not initialize PN532 over I2C: {error}"
            ) from error


    @staticmethod
    def uid_to_string(uid):
        if uid is None:
            return None

        return ":".join(
            f"{byte:02X}"
            for byte in uid
        )

    @staticmethod
    def normalize_uid(uid):
        if uid is None:
            return None

        if isinstance(uid, str):
            compact = (
                uid
                .replace(":", "")
                .replace("-", "")
                .replace(" ", "")
            )

            if len(compact) % 2:
                return uid.strip().upper()

            return ":".join(
                compact[i:i + 2].upper()
                for i in range(0, len(compact), 2)
            )

        return RFIDReader.uid_to_string(uid)


    def simulate_scan(self, uid):
        if not config.SIMULATION_MODE:
            return None

        return self.normalize_uid(uid)


    def _read_uid_bytes(self, timeout=0.1):
        if config.SIMULATION_MODE:
            return None

        if self.pn532 is None:
            return None

        uid = self.pn532.read_passive_target(
            timeout=timeout
        )

        if uid is None:
            return None

        return bytes(uid)

    def read_uid(self, timeout=0.1):
        uid = self._read_uid_bytes(timeout)

        return self.uid_to_string(uid)

    def read_card(self, timeout=0.05):
        """Poll the PN532 once for a card."""

        uid = self._read_uid_bytes(timeout)

        if uid is None:
            return None

        return {
            "uid": self.uid_to_string(uid),
            "uid_bytes": uid
        }

    def wait_for_card(self, timeout=None):
        start = time.monotonic()

        while True:
            uid = self._read_uid_bytes(
                timeout=0.25
            )

            if uid is not None:
                return {
                    "uid": self.uid_to_string(uid),
                    "uid_bytes": uid
                }

            if timeout is not None:
                if time.monotonic() - start >= timeout:
                    return None

            time.sleep(
                config.RFID_SCAN_DELAY
            )


    def build_verilock_payload(self, user_id):
        payload = bytearray(16)

        payload[0:3] = config.RFID_CARD_MAGIC
        payload[3] = config.RFID_CARD_VERSION

        payload[4:8] = struct.pack(
            ">I",
            int(user_id)
        )

        return bytes(payload)

    def parse_verilock_payload(self, data):
        if data is None:
            return None

        if len(data) != 16:
            return None

        if bytes(data[0:3]) != config.RFID_CARD_MAGIC:
            return None

        if int(data[3]) != config.RFID_CARD_VERSION:
            return None

        return {
            "version": int(data[3]),
            "user_id": struct.unpack(
                ">I",
                bytes(data[4:8])
            )[0]
        }


    def _authenticate_block(self, uid):
        if self.pn532 is None:
            return False

        return bool(
            self.pn532.mifare_classic_authenticate_block(
                uid,
                config.RFID_DATA_BLOCK,
                0x60,
                bytearray(
                    config.RFID_MIFARE_KEY
                )
            )
        )

    def read_verilock_card(
        self,
        timeout=0.5,
        uid_bytes=None
    ):
        """Read and validate a card, using the first scan UID when supplied."""

        if uid_bytes is not None:
            uid = bytes(uid_bytes)
        else:
            uid = self._read_uid_bytes(timeout)

        if uid is None:
            return None

        uid_string = self.uid_to_string(uid)

        try:
            if not self._authenticate_block(uid):
                return {
                    "uid": uid_string,
                    "uid_bytes": uid,
                    "valid": False,
                    "programmed": False,
                    "user_id": None
                }

            data = self.pn532.mifare_classic_read_block(
                config.RFID_DATA_BLOCK
            )

            parsed = self.parse_verilock_payload(
                data
            )

            if parsed is None:
                return {
                    "uid": uid_string,
                    "uid_bytes": uid,
                    "valid": False,
                    "programmed": False,
                    "user_id": None
                }

            return {
                "uid": uid_string,
                "uid_bytes": uid,
                "valid": True,
                "programmed": True,
                "user_id": parsed["user_id"],
                "version": parsed["version"]
            }

        except Exception as error:
            print(
                f"[RFID] Card read error: {error}"
            )

            return None


    def write_tag(self, uid_bytes, user_id):
        """Keep the existing enrollment calls working with this reader."""

        if config.SIMULATION_MODE:
            return True

        if self.pn532 is None:
            print(
                "[RFID] Card programming error: "
                "PN532 is not initialized."
            )
            return False

        uid = bytes(uid_bytes)

        try:
            if not self._authenticate_block(uid):
                return False

            payload = self.build_verilock_payload(
                user_id
            )

            result = self.pn532.mifare_classic_write_block(
                config.RFID_DATA_BLOCK,
                payload
            )

            if result is False:
                return False

            # Read the card again to make sure the write worked.
            verification = (
                self.pn532.mifare_classic_read_block(
                    config.RFID_DATA_BLOCK
                )
            )

            return (
                verification is not None
                and bytes(verification) == payload
            )

        except Exception as error:
            print(
                f"[RFID] Card programming error: {error}"
            )

            return False

    def write_verilock_card(
        self,
        user_id,
        timeout=10.0,
        expected_uid=None
    ):
        """Wait for a card, check its expected UID, then write and verify block 4."""

        if not config.SIMULATION_MODE:
            if self.pn532 is None:
                print(
                    "[RFID] Cannot program card: "
                    "PN532 is not initialized."
                )
                return None

        card = self.wait_for_card(
            timeout=timeout
        )

        if card is None:
            return None

        if expected_uid is not None and card["uid"] != expected_uid:
            print("[RFID] Card changed after ownership check; refusing to write.")
            return None

        if not self.write_tag(
            card["uid_bytes"],
            user_id
        ):
            return None

        print(
            f"[RFID] Card {card['uid']} "
            f"programmed and verified "
            f"for user {user_id}."
        )

        return card["uid"]


    def clear_verilock_card(
        self,
        timeout=10.0
    ):
        if not config.SIMULATION_MODE:
            if self.pn532 is None:
                print(
                    "[RFID] Cannot clear card: "
                    "PN532 is not initialized."
                )
                return False

        card = self.wait_for_card(
            timeout=timeout
        )

        if card is None:
            return False

        uid = card["uid_bytes"]

        try:
            if not self._authenticate_block(uid):
                return False

            blank = bytes(16)

            result = self.pn532.mifare_classic_write_block(
                config.RFID_DATA_BLOCK,
                blank
            )

            if result is False:
                return False

            verification = (
                self.pn532.mifare_classic_read_block(
                    config.RFID_DATA_BLOCK
                )
            )

            return (
                verification is not None
                and bytes(verification) == blank
            )

        except Exception as error:
            print(
                f"[RFID] Card clear error: {error}"
            )

            return False


    def cleanup(self):
        """Release the PN532, its GPIO lines, and the I2C bus."""


        # Drop the PN532 object before releasing GPIO and I2C.

        self.pn532 = None


        # Release both GPIO lines.

        for obj_name in (
            "request_pin",
            "reset_pin"
        ):
            obj = getattr(
                self,
                obj_name,
                None
            )

            if obj is not None:
                try:
                    obj.deinit()

                except Exception as error:
                    print(
                        f"[RFID] Warning releasing "
                        f"{obj_name}: {error}"
                    )

                setattr(
                    self,
                    obj_name,
                    None
                )


        # Release the I2C bus after the GPIO lines.

        if self.i2c is not None:
            try:
                self.i2c.deinit()

            except Exception as error:
                print(
                    f"[RFID] Warning releasing "
                    f"I2C: {error}"
                )

            self.i2c = None

        # Let Blinka and lgpio release the GPIO pins
        # before another reader starts.

        time.sleep(0.25)

        print("[RFID] Reader released.")