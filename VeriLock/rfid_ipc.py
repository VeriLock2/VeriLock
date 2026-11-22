"""Let Admin request card operations, manual locking, and autolock changes.

The main app handles these requests over a local connection with a session token."""
import json
import socket
import socketserver
import threading


class _RFIDRequestHandler(socketserver.StreamRequestHandler):
    def handle(self):
        server = self.server
        try:
            raw = self.rfile.readline(1024 * 1024)
            request = json.loads(raw.decode("utf-8"))
            if request.get("token") != server.token:
                response = {"ok": False, "error": "unauthorized"}
            else:
                response = server.dispatch(request)
        except Exception as error:
            response = {"ok": False, "error": str(error)}
        self.wfile.write((json.dumps(response) + "\n").encode("utf-8"))


class _ThreadingServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class RFIDIPCServer:
    def __init__(self, reader, token, lock_controller=None, database=None):
        self.reader = reader
        self.lock_controller = lock_controller
        self.database = database
        self.token = token
        self.lock = threading.Lock()
        self.closing = threading.Event()
        self.closed = threading.Event()
        self.close_guard = threading.Lock()
        self.close_started = False
        self.server = _ThreadingServer(("127.0.0.1", 0), _RFIDRequestHandler)
        self.server.reader = reader
        self.server.token = token
        self.server.dispatch = self._dispatch
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            daemon=True,
            name="verilock-rfid-ipc",
        )
        self.thread.start()

    def _dispatch(self, request):
        if self.closing.is_set():
            raise RuntimeError("Administration bridge is closing.")
        operation = request.get("operation")
        if operation == "set_autolock":
            admin_id = int(request["admin_id"])
            if self.database is None or not self.database.is_admin(admin_id):
                raise RuntimeError("An enabled administrator is required to change settings.")
            if self.lock_controller is None:
                raise RuntimeError("Lock controller is unavailable.")
            enabled = request.get("enabled")
            if not isinstance(enabled, bool):
                raise ValueError("Autolock setting must be true or false.")
            result = self.lock_controller.set_autolock_enabled(
                enabled, persist=self.database.set_autolock_enabled
            )
            return {"ok": True, "result": result}
        if operation == "relock":
            admin_id = int(request["admin_id"])
            if self.database is None or not self.database.is_admin(admin_id):
                raise RuntimeError("An enabled administrator is required to lock.")
            if self.lock_controller is None:
                raise RuntimeError("Lock controller is unavailable.")
            result = self.lock_controller.relock_now()
            return {"ok": True, "result": result}
        with self.lock:
            if self.closing.is_set():
                raise RuntimeError("Administration bridge is closing.")
            if operation == "ping":
                return {"ok": True, "result": True}
            if operation == "read_verilock_card":
                result = self.reader.read_verilock_card(
                    timeout=float(request.get("timeout", 5.0))
                )
                # admin.py uses the formatted UID, not the raw UID bytes.
                if isinstance(result, dict):
                    result = dict(result)
                    result.pop("uid_bytes", None)
                return {"ok": True, "result": result}
            if operation == "write_verilock_card":
                result = self.reader.write_verilock_card(
                    int(request["user_id"]),
                    timeout=float(request.get("timeout", 10.0)),
                    expected_uid=request.get("expected_uid"),
                )
                return {"ok": True, "result": result}
            if operation == "clear_verilock_card":
                result = self.reader.clear_verilock_card(
                    timeout=float(request.get("timeout", 10.0))
                )
                return {"ok": True, "result": bool(result)}
            raise RuntimeError(f"Unsupported RFID IPC operation: {operation}")

    def begin_close(self):
        """Stop accepting requests and close the listener in the background."""
        self.closing.set()
        with self.close_guard:
            if self.close_started:
                return
            self.close_started = True
            def stop_server():
                try:
                    self.server.shutdown()
                    self.server.server_close()
                finally:
                    self.closed.set()
            threading.Thread(target=stop_server, daemon=True,
                             name="verilock-bridge-close").start()

    def close_if_idle(self):
        """Return True once RFID operations and the connection loop have stopped."""
        self.begin_close()
        if not self.lock.acquire(blocking=False):
            return False
        self.lock.release()
        return self.closed.is_set()

    def close(self):
        # Give shutdown up to two seconds to finish. When handing the reader
        # to another worker, use close_if_idle() to check that it is free first.
        self.begin_close()
        self.closed.wait(timeout=2.0)


class RFIDIPCClient:
    def __init__(self, port, token):
        self.port = int(port)
        self.token = token
        self._request("ping")

    def _request(self, operation, **kwargs):
        payload = {"token": self.token, "operation": operation, **kwargs}
        timeout = max(15.0, float(kwargs.get("timeout", 0.0)) + 5.0)
        with socket.create_connection(("127.0.0.1", self.port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            file = sock.makefile("rwb")
            file.write((json.dumps(payload) + "\n").encode("utf-8"))
            file.flush()
            raw = file.readline(1024 * 1024)
        if not raw:
            raise RuntimeError("RFID bridge closed without a response.")
        response = json.loads(raw.decode("utf-8"))
        if not response.get("ok"):
            raise RuntimeError(response.get("error", "RFID bridge request failed."))
        return response.get("result")

    def read_verilock_card(self, timeout=5.0):
        return self._request("read_verilock_card", timeout=timeout)

    def write_verilock_card(self, user_id, timeout=10.0, expected_uid=None):
        return self._request(
            "write_verilock_card", user_id=int(user_id), timeout=timeout,
            expected_uid=expected_uid,
        )

    def clear_verilock_card(self, timeout=10.0):
        return self._request("clear_verilock_card", timeout=timeout)

    def set_autolock(self, admin_id, enabled):
        return self._request("set_autolock", admin_id=int(admin_id), enabled=bool(enabled))

    def relock(self, admin_id):
        return self._request("relock", admin_id=int(admin_id), timeout=10.0)

    def simulate_scan(self, uid):
        return uid.strip().upper() if isinstance(uid, str) else uid

    def cleanup(self):
        # main.py keeps control of the PN532, so closing Admin should not release it.
        return None
