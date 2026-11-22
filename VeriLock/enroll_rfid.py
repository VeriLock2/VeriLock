"""Command-line utility for programming MIFARE cards and assigning their UIDs."""

import config

from database import Database
from rfid import RFIDReader


CARD_WAIT_TIMEOUT_SECONDS = 10.0


def main():

    print()
    print("================================")
    print("      VERILOCK RFID ENROLL")
    print("================================")
    print()

    database = Database()


    users = database.list_users()

    if not users:

        print(
            "[ENROLL] No users exist."
        )

        print(
            "[ENROLL] Create a user first."
        )

        return

    print("Existing users:")
    print()

    for user in users:

        print(
            f"ID {user['id']} | "
            f"{user['name']} | "
            f"{user['role']}"
        )

    print()


    try:

        user_id = int(
            input(
                "Enter user ID: "
            ).strip()
        )

    except ValueError:

        print(
            "[ENROLL] Invalid user ID."
        )

        return

    user = database.get_user_by_id(
        user_id
    )

    if user is None:

        print(
            "[ENROLL] User not found "
            "or user is disabled."
        )

        return

    print()
    print(
        f"[ENROLL] Selected: "
        f"{user['name']}"
    )


    if config.SIMULATION_MODE:

        print()
        print(
            "[ENROLL] Simulation mode."
        )

        entered_uid = input(
            "Enter simulated RFID UID: "
        ).strip().upper()

        uid = RFIDReader.normalize_uid(entered_uid)
        compact_uid = uid.replace(":", "") if uid else ""

        if (
            len(compact_uid) not in {8, 14, 20}
            or not compact_uid.isalnum()
        ):

            print(
                "[ENROLL] Invalid RFID UID. "
                "Enter a 4, 7, or 10 byte hexadecimal UID."
            )

            return

        try:
            bytes.fromhex(compact_uid)
        except ValueError:
            print("[ENROLL] Invalid RFID UID.")
            return

        existing = (
            database.get_any_user_by_rfid(
                uid
            )
        )

        if (
            existing is not None
            and
            existing["id"] != user_id
        ):

            print()
            print(
                "[ENROLL] Card already belongs to:"
            )

            print(
                f"{existing['name']} "
                f"(ID {existing['id']})"
            )

            return

        if not database.assign_rfid(
            user_id,
            uid
        ):

            return

        print()
        print(
            "[ENROLL] Simulated RFID "
            "enrollment complete."
        )

        return


    try:
        rfid = RFIDReader()
    except Exception as error:
        print(f"[ENROLL] Physical enrollment failed: {error}")
        return

    try:
        print()
        print("Place the RFID card on the PN532.")

        card = rfid.wait_for_card(
            timeout=CARD_WAIT_TIMEOUT_SECONDS
        )

        if card is None:
            print("[ENROLL] No card detected before timeout.")
            return

        uid = card["uid"]
        uid_bytes = card["uid_bytes"]

        print()
        print(f"[ENROLL] UID: {uid}")

        existing = database.get_any_user_by_rfid(uid)
        if existing is not None and existing["id"] != user_id:
            print()
            print("[ENROLL] This card is already assigned to another user.")
            print(f"[ENROLL] User: {existing['name']}")
            return

        print()
        print(f"Program this card for {user['name']}?")
        confirmation = input("Type YES to continue: ").strip().upper()
        if confirmation != "YES":
            print("[ENROLL] Enrollment cancelled.")
            return

        if not rfid.write_tag(uid_bytes, user_id):
            print()
            print("[ENROLL] Card programming failed.")
            print("[ENROLL] Database was NOT changed.")
            return

        if not database.assign_rfid(user_id, uid):
            print()
            print("[ENROLL] WARNING:")
            print("Card was programmed, but database assignment failed.")
            return

        print()
        print("================================")
        print("       ENROLLMENT COMPLETE")
        print("================================")
        print(f"User: {user['name']}")
        print(f"User ID: {user_id}")
        print(f"RFID UID: {uid}")
        print(f"Card format: VeriLock V{config.RFID_CARD_VERSION}")
        print("================================")
        print()
    except Exception as error:
        print(f"[ENROLL] Physical enrollment failed: {error}")
    finally:
        rfid.cleanup()

if __name__ == "__main__":

    main()
