"""Store users, credentials, settings, and access history in SQLite."""

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import config

class Database:


    def __init__(self):

        Path(
            config.DATABASE_PATH
        ).parent.mkdir(
            parents=True,
            exist_ok=True
        )

        self.database_path = config.DATABASE_PATH

        self.create_tables()


    @contextmanager
    def connect(self):

        connection = sqlite3.connect(
            self.database_path
        )

        connection.row_factory = sqlite3.Row

        try:
            with connection:
                yield connection
        finally:
            connection.close()


    def create_tables(self):

        with self.connect() as connection:

            cursor = connection.cursor()

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'USER',
                    rfid_uid TEXT UNIQUE,
                    face_id TEXT UNIQUE,
                    enabled INTEGER NOT NULL DEFAULT 1
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS access_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    user_id INTEGER,
                    method TEXT NOT NULL,
                    result TEXT NOT NULL,

                    FOREIGN KEY (user_id)
                    REFERENCES users(id)
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)

    def autolock_enabled(self):
        with self.connect() as connection:
            row = connection.execute(
                "SELECT value FROM settings WHERE key = ?", ("autolock_enabled",)
            ).fetchone()
        return row is None or row["value"] == "1"

    def set_autolock_enabled(self, enabled):
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                ("autolock_enabled", "1" if enabled else "0"),
            )

    def ultra_secure_enabled(self):
        with self.connect() as connection:
            row = connection.execute(
                "SELECT value FROM settings WHERE key = ?",
                ("ultra_secure_mode",),
            ).fetchone()
        return row is not None and row["value"] == "1"

    def set_ultra_secure_enabled(self, enabled):
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                ("ultra_secure_mode", "1" if enabled else "0"),
            )


    def add_user(
        self,
        name,
        role="USER",
        rfid_uid=None,
        face_id=None
    ):

        if not name:

            print(
                "[DATABASE] User name cannot be empty."
            )

            return None

        name = str(name).strip()

        if not name:

            print(
                "[DATABASE] User name cannot be empty."
            )

            return None

        role = (
            str(role)
            .strip()
            .upper()
        )

        if role not in (
            "USER",
            "ADMIN"
        ):

            print(
                "[DATABASE] Invalid role."
            )

            return None

        if rfid_uid:

            rfid_uid = (
                str(rfid_uid)
                .strip()
                .upper()
            )

        if face_id is not None:

            face_id = (
                str(face_id)
                .strip()
            )

        try:

            with self.connect() as connection:

                cursor = connection.cursor()

                cursor.execute("""
                    INSERT INTO users
                    (
                        name,
                        role,
                        rfid_uid,
                        face_id
                    )
                    VALUES (?, ?, ?, ?)
                """, (
                    name,
                    role,
                    rfid_uid,
                    face_id
                ))

                user_id = cursor.lastrowid

            print(
                f"[DATABASE] User added: "
                f"{name} "
                f"(ID {user_id})"
            )

            return user_id

        except sqlite3.IntegrityError as error:

            print(
                "[DATABASE] Could not add user: "
                f"{error}"
            )

            return None


    def get_user_by_id(
        self,
        user_id
    ):

        with self.connect() as connection:

            cursor = connection.cursor()

            cursor.execute("""
                SELECT *
                FROM users
                WHERE id = ?
                AND enabled = 1
            """, (
                user_id,
            ))

            return cursor.fetchone()


    def get_any_user_by_id(
        self,
        user_id
    ):

        with self.connect() as connection:

            cursor = connection.cursor()

            cursor.execute("""
                SELECT *
                FROM users
                WHERE id = ?
            """, (
                user_id,
            ))

            return cursor.fetchone()


    def get_user_by_rfid(
        self,
        rfid_uid
    ):

        if not rfid_uid:

            return None

        rfid_uid = (
            str(rfid_uid)
            .strip()
            .upper()
        )

        with self.connect() as connection:

            cursor = connection.cursor()

            cursor.execute("""
                SELECT *
                FROM users
                WHERE rfid_uid = ?
                AND enabled = 1
            """, (
                rfid_uid,
            ))

            return cursor.fetchone()


    def get_any_user_by_rfid(
        self,
        rfid_uid
    ):
        """Find the user assigned to a card, including disabled users."""

        if not rfid_uid:

            return None

        rfid_uid = (
            str(rfid_uid)
            .strip()
            .upper()
        )

        with self.connect() as connection:

            cursor = connection.cursor()

            cursor.execute("""
                SELECT *
                FROM users
                WHERE rfid_uid = ?
            """, (
                rfid_uid,
            ))

            return cursor.fetchone()


    def get_user_for_rfid_card(self, user_id, rfid_uid):
        """Find an enabled user whose ID and card UID match the scanned card."""
        if user_id is None or not rfid_uid:
            return None
        with self.connect() as connection:
            return connection.execute(
                """SELECT * FROM users
                   WHERE id = ? AND rfid_uid = ? AND enabled = 1""",
                (user_id, str(rfid_uid).strip().upper()),
            ).fetchone()

    def get_user_by_face_id(
        self,
        face_id
    ):

        if face_id is None:

            return None

        face_id = (
            str(face_id)
            .strip()
        )

        with self.connect() as connection:

            cursor = connection.cursor()

            cursor.execute("""
                SELECT *
                FROM users
                WHERE face_id = ?
                AND enabled = 1
            """, (
                face_id,
            ))

            return cursor.fetchone()


    def is_admin(
        self,
        user_id
    ):

        with self.connect() as connection:

            cursor = connection.cursor()

            cursor.execute("""
                SELECT role, enabled
                FROM users
                WHERE id = ?
            """, (
                user_id,
            ))

            user = cursor.fetchone()

            if user is None:

                return False

            if not user["enabled"]:

                return False

            role = (
                str(user["role"])
                .strip()
                .upper()
            )

            return role == "ADMIN"


    def assign_rfid(
        self,
        user_id,
        rfid_uid
    ):

        if not rfid_uid:

            print(
                "[DATABASE] Invalid RFID UID."
            )

            return False

        rfid_uid = (
            str(rfid_uid)
            .strip()
            .upper()
        )

        try:

            with self.connect() as connection:

                cursor = connection.cursor()

                cursor.execute("""
                    SELECT id, name
                    FROM users
                    WHERE id = ?
                """, (
                    user_id,
                ))

                user = cursor.fetchone()

                if user is None:

                    print(
                        "[DATABASE] User not found."
                    )

                    return False

                cursor.execute("""
                    UPDATE users
                    SET rfid_uid = ?
                    WHERE id = ?
                """, (
                    rfid_uid,
                    user_id
                ))

            print(
                f"[DATABASE] RFID "
                f"{rfid_uid} assigned to "
                f"{user['name']} "
                f"(User ID {user_id})."
            )

            return True

        except sqlite3.IntegrityError:

            print(
                "[DATABASE] That RFID card "
                "is already assigned to "
                "another user."
            )

            return False


    def remove_rfid(
        self,
        user_id
    ):

        with self.connect() as connection:

            cursor = connection.cursor()

            cursor.execute("""
                UPDATE users
                SET rfid_uid = NULL
                WHERE id = ?
            """, (
                user_id,
            ))

            if cursor.rowcount == 0:

                print(
                    "[DATABASE] User not found."
                )

                return False

        print(
            f"[DATABASE] RFID removed "
            f"from user {user_id}."
        )

        return True


    def assign_face(
        self,
        user_id,
        face_id
    ):

        if face_id is None:

            print(
                "[DATABASE] Invalid Face ID."
            )

            return False

        face_id = (
            str(face_id)
            .strip()
        )

        if not face_id:

            print(
                "[DATABASE] Invalid Face ID."
            )

            return False

        try:

            with self.connect() as connection:

                cursor = connection.cursor()

                cursor.execute("""
                    SELECT id, name
                    FROM users
                    WHERE id = ?
                """, (
                    user_id,
                ))

                user = cursor.fetchone()

                if user is None:

                    print(
                        "[DATABASE] User not found."
                    )

                    return False

                cursor.execute("""
                    UPDATE users
                    SET face_id = ?
                    WHERE id = ?
                """, (
                    face_id,
                    user_id
                ))

            print(
                f"[DATABASE] Face ID "
                f"{face_id} assigned to "
                f"{user['name']} "
                f"(User ID {user_id})."
            )

            return True

        except sqlite3.IntegrityError:

            print(
                "[DATABASE] That Face ID "
                "is already assigned to "
                "another user."
            )

            return False


    def remove_face(
        self,
        user_id
    ):

        with self.connect() as connection:

            cursor = connection.cursor()

            cursor.execute("""
                UPDATE users
                SET face_id = NULL
                WHERE id = ?
            """, (
                user_id,
            ))

            if cursor.rowcount == 0:

                print(
                    "[DATABASE] User not found."
                )

                return False

        print(
            f"[DATABASE] Face ID removed "
            f"from user {user_id}."
        )

        return True


    def enable_user(
        self,
        user_id
    ):

        with self.connect() as connection:

            cursor = connection.cursor()

            cursor.execute("""
                UPDATE users
                SET enabled = 1
                WHERE id = ?
            """, (
                user_id,
            ))

            if cursor.rowcount == 0:

                print(
                    "[DATABASE] User not found."
                )

                return False

        print(
            f"[DATABASE] User "
            f"{user_id} enabled."
        )

        return True


    def disable_user(
        self,
        user_id
    ):

        with self.connect() as connection:

            cursor = connection.cursor()

            cursor.execute("""
                UPDATE users
                SET enabled = 0
                WHERE id = ?
            """, (
                user_id,
            ))

            if cursor.rowcount == 0:

                print(
                    "[DATABASE] User not found."
                )

                return False

        print(
            f"[DATABASE] User "
            f"{user_id} disabled."
        )

        return True


    def delete_user(
        self,
        user_id
    ):

        with self.connect() as connection:

            cursor = connection.cursor()

            cursor.execute("""
                SELECT id, name
                FROM users
                WHERE id = ?
            """, (
                user_id,
            ))

            user = cursor.fetchone()

            if user is None:

                print(
                    "[DATABASE] User not found."
                )

                return False

            # Keep access logs when the user record is deleted.

            cursor.execute("""
                UPDATE access_log
                SET user_id = NULL
                WHERE user_id = ?
            """, (
                user_id,
            ))

            cursor.execute("""
                DELETE FROM users
                WHERE id = ?
            """, (
                user_id,
            ))

        print(
            f"[DATABASE] User deleted: "
            f"{user['name']} "
            f"(ID {user_id})."
        )

        return True


    def log_access(
        self,
        user_id,
        method,
        result
    ):

        timestamp = (
            datetime.now()
            .isoformat(
                timespec="seconds"
            )
        )

        method = (
            str(method)
            .strip()
            .upper()
        )

        result = (
            str(result)
            .strip()
            .upper()
        )

        with self.connect() as connection:

            cursor = connection.cursor()

            cursor.execute("""
                INSERT INTO access_log
                (
                    timestamp,
                    user_id,
                    method,
                    result
                )
                VALUES (?, ?, ?, ?)
            """, (
                timestamp,
                user_id,
                method,
                result
            ))


    def list_users(
        self
    ):

        with self.connect() as connection:

            cursor = connection.cursor()

            cursor.execute("""
                SELECT *
                FROM users
                ORDER BY id
            """)

            return cursor.fetchall()


    def list_enabled_users(
        self
    ):

        with self.connect() as connection:

            cursor = connection.cursor()

            cursor.execute("""
                SELECT *
                FROM users
                WHERE enabled = 1
                ORDER BY id
            """)

            return cursor.fetchall()


    def list_access_log(
        self,
        limit=100
    ):

        with self.connect() as connection:

            cursor = connection.cursor()

            cursor.execute("""
                SELECT
                    access_log.id,
                    access_log.timestamp,
                    access_log.user_id,
                    users.name AS user_name,
                    access_log.method,
                    access_log.result

                FROM access_log

                LEFT JOIN users
                ON access_log.user_id = users.id

                ORDER BY access_log.id DESC

                LIMIT ?
            """, (
                limit,
            ))

            return cursor.fetchall()
