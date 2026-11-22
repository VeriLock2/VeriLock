"""Start VeriLock and run its main Tkinter interface."""

import tkinter as tk
import subprocess
import sys
import signal
import time
import queue
import os
import secrets
import threading
import traceback
from pathlib import Path

CAMERA_RECOVERY_RETRY_DELAY = 1.0

import cv2

from PIL import Image
from PIL import ImageTk

import config
from monitor_layout import place_window_on_monitor


BG = "#0B1118"
PANEL_BG = "#121C27"
PANEL_ALT = "#172433"
BORDER = "#2B4F6F"
PRIMARY = "#2F80ED"
PRIMARY_HOVER = "#4A95F5"
TEXT = "#F2F4F7"
TEXT_SECONDARY = "#AAB4C0"
SUCCESS = "#27AE60"
DANGER = "#EB5757"
BUTTON_BG = "#4A6A8A"
BUTTON_ACTIVE = "#5B7FA3"
HOME_BG = "#FFFFFF"
HOME_NAVY = "#0B2547"
HOME_BLUE = "#1689E4"


from database import Database
from servo import Servo
from rack_servo import RackServo
from lock_controller import LockController
from access_control import AccessController
from rfid import RFIDReader
from camera import Camera
from face_authentication import FaceRecognition
from background_workers import FaceWorker, RFIDWorker
from rfid_ipc import RFIDIPCServer


class VeriLockApp:


    def __init__(
        self,
        root,
        database,
        servo,
        rack_servo,
        lock_controller,
        access_controller,
        rfid_reader,
        camera,
        face_recognition
    ):

        self.root = root
        self.shutdown_started = False

        self.database = database

        self.servo = servo

        self.rack_servo = rack_servo

        self.lock_controller = (
            lock_controller
        )

        self.access_controller = (
            access_controller
        )

        self.rfid_reader = (
            rfid_reader
        )

        self.camera = (
            camera
        )

        self.face_recognition = (
            face_recognition
        )


        self.running = True


        self.admin_process = None
        self.admin_camera_released = False
        self._camera_recovery = None
        self._camera_recovery_message = ""
        self.admin_rfid_released = False
        self.rfid_ipc_server = None

        self.current_mode = "HOME"
        self.session_generation = 0


        self.face_authentication_active = False

        self.face_authentication_complete = False

        self.recognized_face_id = None

        self.recognition_frames = 0

        self.recognition_frame_counts = {}
        self.recognition_frame_histories = {}
        self.face_overlay_entries = []


        self.rfid_authentication_active = False
        self.face_worker = None
        self.rfid_worker = None
        self.access_results = queue.Queue()
        self.access_controller.on_grant_complete = self.queue_access_result


        self.camera_image = None
        self.logo_image = None
        self.next_preview_frame = 0.0
        self.next_face_analysis = 0.0
        self.last_face_frame_sequence = None
        self.face_overlay = None


        self.root.title(
            "VeriLock"
        )

        self.root.configure(bg=BG)


        # Keep Python fullscreen off for display routing. A saved labwc rule
        # can make the main UI fullscreen without expanding its small dialogs.


        if config.SIMULATION_MODE:
            self.root.attributes("-fullscreen", False)
            self.root.geometry(
                f"{config.DISPLAY_WIDTH}x{config.DISPLAY_HEIGHT}"
            )
            self.root.configure(cursor="")
            self.root.protocol(
                "WM_DELETE_WINDOW",
                self.shutdown
            )
        else:
            if getattr(config, "FULLSCREEN", False):
                self.root.attributes("-fullscreen", True)
            else:
                self.root.geometry(
                    f"{config.DISPLAY_WIDTH}x{config.DISPLAY_HEIGHT}"
                )

            if getattr(config, "HIDE_CURSOR", False):
                self.root.configure(cursor="none")

            self.root.protocol(
                "WM_DELETE_WINDOW",
                self.prevent_window_close
            )

        # Clean up when the app is stopped from the terminal or systemd.

        signal.signal(signal.SIGTERM, self.handle_shutdown_signal)
        signal.signal(signal.SIGINT, self.handle_shutdown_signal)

        self.root.geometry(
            f"{config.DISPLAY_WIDTH}x"
            f"{config.DISPLAY_HEIGHT}"
        )
        if not config.SIMULATION_MODE:
            place_window_on_monitor(
                self.root,
                config.DISPLAY_WIDTH,
                config.DISPLAY_HEIGHT,
            )

        self.root.resizable(
            False,
            False
        )
        if config.SIMULATION_MODE:
            self.root.bind("<KeyPress-r>", self.on_simulation_rfid_key)
            self.root.bind("<KeyPress-R>", self.on_simulation_rfid_key)


        self.main_frame = tk.Frame(
            self.root,
            bg=BG
        )

        self.main_frame.pack(
            fill="both",
            expand=True
        )


        self.show_home_screen()
        if self.face_recognition is not None:
            self.face_worker = FaceWorker(self.face_recognition)
        if self.rfid_reader is not None and not config.SIMULATION_MODE:
            self.rfid_worker = RFIDWorker(self.rfid_reader)


        self.root.after(
            30,
            self.update
        )


    def clear_screen(
        self
    ):

        for widget in (
            self.main_frame.winfo_children()
        ):

            widget.destroy()
        self.main_frame.configure(bg=BG)
        self.root.configure(bg=BG)

    def schedule_session_action(self, delay_seconds, callback):
        """Run a delayed action only if the screen session is unchanged."""
        generation = self.session_generation
        self.root.after(
            int(delay_seconds * 1000),
            lambda: callback() if self.running and generation == self.session_generation else None,
        )

    def queue_access_result(self, success, user, method):
        """Pass the lock result back to the screen without updating widgets from a worker thread."""
        self.access_results.put((success, user, method))

    def update_access_results(self):
        try:
            success, user, method = self.access_results.get_nowait()
        except queue.Empty:
            return
        if self.current_mode not in ("HOME", "FACE", "RFID"):
            return
        self.access_controller.last_result = "GRANTED" if success else "ACTUATION_FAILED"
        self.face_status_label.config(text="ACCESS GRANTED" if success else "LOCK ERROR")
        self.face_detail_label.config(
            text=f"Welcome, {user['name']}." if success else
            "The lock did not finish moving. Check the mechanism."
        )
        self.schedule_session_action(
            config.ACCESS_GRANTED_DISPLAY_TIME if success else config.ACCESS_DENIED_DISPLAY_TIME,
            self.show_home_screen,
        )


    def home_instruction(self):
        """Show the R-key shortcut only in simulation."""
        if self.rfid_reader is None:
            return "RFID unavailable. Press FACIAL RECOGNITION."
        if self.camera is None or self.face_recognition is None:
            return ("Press R for RFID."
                    if config.SIMULATION_MODE else
                    "Present your RFID card.")
        if self.database.ultra_secure_enabled():
            return ("Ultra Secure: face and RFID required. Press R for RFID."
                    if config.SIMULATION_MODE else
                    "Ultra Secure: face and RFID card required.")
        return ("Press FACIAL RECOGNITION or press R for RFID."
                if config.SIMULATION_MODE else
                "Press FACIAL RECOGNITION or present an RFID card.")

    def show_home_screen(self):
        self.access_controller.clear_pending()
        self.session_generation += 1
        self.current_mode = "HOME"
        self.face_authentication_active = False
        self.face_authentication_complete = False
        self.rfid_authentication_active = False
        self.recognized_face_id = None
        self.recognition_frames = 0
        self.recognition_frame_counts = {}
        self.recognition_frame_histories = {}
        self.face_overlay_entries = []
        self.next_preview_frame = 0.0
        self.next_face_analysis = 0.0
        self.last_face_frame_sequence = None
        self.face_overlay = None

        self.clear_screen()
        self.main_frame.configure(bg=HOME_BG)
        self.root.configure(bg=HOME_BG)

        controls = tk.Frame(self.main_frame, bg=HOME_BG, width=350)
        controls.pack(side="left", fill="y", padx=(15, 5), pady=8)
        controls.pack_propagate(False)

        # Show the VeriLock logo on the home screen.
        logo_path = Path(__file__).resolve().parent / "verilock_logo.png"
        with Image.open(logo_path) as original_logo:
            display_logo = original_logo.copy()
        display_logo.thumbnail((205, 205), Image.Resampling.LANCZOS)
        self.logo_image = ImageTk.PhotoImage(display_logo)
        tk.Label(controls, image=self.logo_image, bg=HOME_BG).pack(pady=(5, 4))

        self.face_button = tk.Button(
            controls, bg=HOME_NAVY, fg=HOME_BG,
            activebackground=HOME_BLUE, activeforeground=HOME_BG,
            relief="flat", bd=1, text="FACIAL RECOGNITION",
            font=("Arial", 14, "bold"), width=22, height=2,
            command=self.start_face_authentication,
        )
        if self.camera is None or self.face_recognition is None:
            self.face_button.config(state=tk.DISABLED)
        self.face_button.pack(pady=(3, 5))

        control_row = tk.Frame(controls, bg=HOME_BG)
        control_row.pack(pady=(0, 5))
        tk.Button(
            control_row, bg=HOME_BLUE, fg=HOME_BG,
            activebackground=HOME_NAVY, activeforeground=HOME_BG,
            relief="flat", bd=1, text="ADMIN",
            font=("Arial", 11, "bold"), width=8,
            command=self.open_admin_menu,
        ).pack(side="left", padx=4)
        tk.Button(
            control_row, bg=HOME_BLUE, fg=HOME_BG,
            activebackground=HOME_NAVY, activeforeground=HOME_BG,
            relief="flat", bd=1, text="LOCK",
            font=("Arial", 11, "bold"), width=8,
            command=lambda: self.open_admin_menu(action="LOCK"),
        ).pack(side="left", padx=4)
        self.cancel_button = tk.Button(
            control_row, bg=HOME_NAVY, fg=HOME_BG,
            activebackground=HOME_BLUE, activeforeground=HOME_BG,
            relief="flat", bd=1, text="CANCEL",
            font=("Arial", 11, "bold"), width=8,
            command=self.show_home_screen, state=tk.DISABLED,
        )
        self.cancel_button.pack(side="left", padx=4)

        message = self.home_instruction()
        self.home_idle_message = message
        self.home_status_label = tk.Label(
            controls, bg=HOME_BG, fg=HOME_NAVY, text=message,
            font=("Arial", 11), wraplength=325, justify="center",
        )
        self.home_status_label.pack(pady=3)
        self.last_lock_ready = None
        self.refresh_lock_status()

        preview_area = tk.Frame(self.main_frame, bg=HOME_BG)
        preview_area.pack(side="right", fill="both", expand=True,
                          padx=(5, 15), pady=16)
        self.camera_image = ImageTk.PhotoImage(
            Image.new("RGB", (360, 270), "#DFEAF5")
        )
        self.camera_label = tk.Label(
            preview_area, image=self.camera_image, bg=HOME_BG,
            highlightbackground=HOME_BLUE, highlightthickness=2,
        )
        self.camera_label.pack(pady=(9, 7))
        self.face_status_label = tk.Label(
            preview_area, bg=HOME_BG, fg=HOME_NAVY,
            text="CAMERA READY" if self.camera is not None else "",
            font=("Arial", 18, "bold"),
        )
        self.face_status_label.pack(pady=(0, 4))
        self.face_detail_label = tk.Label(
            preview_area, bg=HOME_BG, fg=HOME_NAVY,
            text="Press Facial Recognition to begin verification.",
            font=("Arial", 11), wraplength=390, justify="center",
        )
        self.face_detail_label.pack(pady=(0, 3))

        # In simulation, pressing R opens the card UID entry.
        self.rfid_input_frame = tk.Frame(preview_area, bg=HOME_BG)
        tk.Label(
            self.rfid_input_frame, bg=HOME_BG, fg=HOME_NAVY,
            text="Simulation RFID UID:", font=("Arial", 10),
        ).pack(side="left", padx=3)
        self.rfid_entry = tk.Entry(self.rfid_input_frame, width=15,
                                   font=("Arial", 12))
        self.rfid_entry.pack(side="left", padx=3)
        self.rfid_entry.bind("<Return>", lambda _event: self.simulate_rfid_scan())
        tk.Button(
            self.rfid_input_frame, text="SCAN", bg=HOME_BLUE, fg=HOME_BG,
            activebackground=HOME_NAVY, activeforeground=HOME_BG,
            font=("Arial", 10, "bold"), command=self.simulate_rfid_scan,
        ).pack(side="left", padx=3)
        self.refresh_camera_recovery_status()


    def open_admin_menu(self, action=None):
        if not self.running:
            return
        if not self.cancel_camera_recovery():
            self.home_status_label.config(text="Finishing camera recovery before opening admin...")
            self.root.after(100, lambda: self.open_admin_menu(action=action))
            return
        self.session_generation += 1
        self.access_controller.clear_pending()

        print()
        print("================================")
        print("     ADMINISTRATION REQUEST")
        print("================================")

        if self.admin_process is not None and self.admin_process.poll() is None:
            print("[ADMIN] Administration interface is already open.")
            return

        self.current_mode = "ADMIN"
        self.face_authentication_active = False
        self.face_authentication_complete = False
        self.rfid_authentication_active = False
        self.recognized_face_id = None
        self.recognition_frames = 0
        self.recognition_frame_counts = {}
        self.recognition_frame_histories = {}
        self.face_overlay_entries = []

        if self.face_worker is not None:
            if not self.face_worker.close(timeout=0):
                self.home_status_label.config(text="Opening administrator verification...")
                self.root.after(100, lambda: self.open_admin_menu(action=action))
                return
            self.face_worker = None

        # Pause card scans during admin mode; the main app still owns the PN532.

        if self.rfid_worker is not None:
            print("[ADMIN] Pausing main RFID worker...")
            if not self.rfid_worker.close(timeout=0):
                self.home_status_label.config(text="Opening administrator verification...")
                self.root.after(100, lambda: self.open_admin_menu(action=action))
                return
            self.rfid_worker = None

        admin_path = Path(__file__).resolve().parent / "admin.py"
        if not admin_path.exists():
            print("[ADMIN] admin.py was not found.")
            print(f"[ADMIN] Expected location: {admin_path}")
            self.current_mode = "HOME"
            self.restore_main_camera()
            self.restore_main_rfid()
            return

        try:
            if self.camera is not None:
                print("[ADMIN] Releasing main camera...")
                self.camera.release()
                self.camera = None
                self.admin_camera_released = True
        except Exception as error:
            print(f"[ADMIN] Camera release warning: {error}")
            self.restore_main_camera()
            self.restore_main_rfid()
            return

        try:
            env = os.environ.copy()
            env.pop("VERILOCK_ADMIN_ACTION", None)
            if action == "LOCK":
                env["VERILOCK_ADMIN_ACTION"] = "LOCK"
            if self.lock_controller is not None:
                token = secrets.token_urlsafe(32)
                self.rfid_ipc_server = RFIDIPCServer(self.rfid_reader, token, self.lock_controller, self.database)
                env["VERILOCK_RFID_IPC_PORT"] = str(self.rfid_ipc_server.port)
                env["VERILOCK_RFID_IPC_TOKEN"] = token
                print("[ADMIN] RFID bridge ready; main retains PN532 ownership.")

            print("[ADMIN] Opening protected administration interface...")
            self.admin_process = subprocess.Popen(
                [sys.executable, str(admin_path)],
                cwd=str(admin_path.parent),
                env=env,
            )
            print("[ADMIN] Administration application launched.")
        except Exception as error:
            print("[ADMIN] Could not open administration interface.")
            print(f"[ADMIN] Error: {error}")
            self.admin_process = None
            if self.rfid_ipc_server is not None:
                self.rfid_ipc_server.close()
                self.rfid_ipc_server = None
            self.restore_main_camera()
            self.restore_main_rfid()


    def restore_main_camera(self, max_attempts=5):
        """Restore camera capture in the background without interrupting RFID."""
        if not self.running or self.shutdown_started:
            return False
        if self.admin_process is not None and self.admin_process.poll() is None:
            return False
        self.current_mode = "HOME"
        if self.camera is not None:
            self.admin_camera_released = False
            if self.face_recognition is not None and self.face_worker is None:
                self.face_worker = FaceWorker(self.face_recognition)
            self._camera_recovery_message = ""
            self.refresh_camera_recovery_status()
            return True
        if self._camera_recovery is not None:
            return False

        job = {
            "cancel": threading.Event(),
            "adopted": threading.Event(),
            "ready": threading.Event(),
            "camera": None,
            "recognizer": self.face_recognition,
            "error": None,
        }
        self._camera_recovery = job
        self._camera_recovery_message = "Restoring camera; RFID remains available."
        self.refresh_camera_recovery_status()

        def recover():
            candidate = None
            try:
                for attempt in range(1, max_attempts + 1):
                    if job["cancel"].is_set():
                        return
                    print(f"[CAMERA] Recovery attempt {attempt}/{max_attempts}...")
                    try:
                        candidate = Camera()
                        break
                    except Exception as error:
                        job["error"] = error
                        print(f"[CAMERA] Recovery attempt {attempt} failed: {error}")
                        if attempt < max_attempts:
                            if job["cancel"].wait(CAMERA_RECOVERY_RETRY_DELAY):
                                return
                if candidate is not None and not job["cancel"].is_set():
                    if job["recognizer"] is None:
                        try:
                            job["recognizer"] = FaceRecognition()
                        except Exception as error:
                            print(f"[FACE] Camera recovered, but face model unavailable: {error}")
                    job["camera"] = candidate
                job["ready"].set()
                # Leave the camera open only after the main screen takes it over.
                # If recovery is canceled, release it before Admin opens the camera.
                while candidate is not None and not job["adopted"].is_set():
                    if job["cancel"].wait(0.05):
                        return
            finally:
                if candidate is not None and not job["adopted"].is_set():
                    try:
                        candidate.release()
                    except Exception as error:
                        job["error"] = error
                        print(f"[CAMERA] Cancelled recovery cleanup failed: {error}")

        job["thread"] = threading.Thread(
            target=recover, daemon=True, name="verilock-camera-recovery"
        )
        try:
            job["thread"].start()
        except Exception as error:
            self._camera_recovery = None
            self._camera_recovery_message = ""
            print(f"[CAMERA] Could not start recovery worker: {error}")
            self.refresh_camera_recovery_status()
            return False
        self.root.after(50, lambda: self.poll_camera_recovery(job))
        return False

    def poll_camera_recovery(self, job):
        if self._camera_recovery is not job:
            return
        if not self.running or self.shutdown_started:
            job["cancel"].set()
            return
        if job["cancel"].is_set():
            if job["thread"].is_alive():
                self.root.after(50, lambda: self.poll_camera_recovery(job))
            else:
                self._camera_recovery = None
            return
        if not job["ready"].is_set():
            self.root.after(50, lambda: self.poll_camera_recovery(job))
            return

        camera = job["camera"]
        if camera is not None:
            self.camera = camera
            self.face_recognition = job["recognizer"]
            job["adopted"].set()
            self.admin_camera_released = False
            self._camera_recovery_message = (
                "" if self.face_recognition is not None
                else "Use RFID."
            )
            if self.face_recognition is not None and self.face_worker is None:
                self.face_worker = FaceWorker(self.face_recognition)
            print("[CAMERA] Main camera restored.")
        else:
            self._camera_recovery_message = ""
            print(f"[CAMERA] Recovery stopped after all attempts: {job['error']}")
        self._camera_recovery = None
        self.refresh_camera_recovery_status()

    def cancel_camera_recovery(self):
        """Wait for camera ownership to be released before opening admin mode."""
        job = self._camera_recovery
        if job is None:
            return True
        job["cancel"].set()
        if job["thread"].is_alive():
            return False
        self._camera_recovery = None
        return True

    def refresh_camera_recovery_status(self):
        if self.current_mode != "HOME" or not hasattr(self, "face_button"):
            return
        available = self.camera is not None and self.face_recognition is not None
        self.face_button.config(
            state=tk.NORMAL if available and self.lock_controller.can_authenticate()
            else tk.DISABLED
        )
        self.face_status_label.config(
            text="CAMERA READY" if self.camera is not None else ""
        )
        self.face_detail_label.config(
            text=self._camera_recovery_message
            or ("Press Facial Recognition to begin verification." if available
                else "" if self.camera is None
                else "Use RFID.")
        )

    def restore_main_rfid(self, attempt=1, max_attempts=5):
        if self.rfid_reader is not None:
            self.admin_rfid_released = False

            if (
                not config.SIMULATION_MODE
                and self.rfid_worker is None
            ):
                self.rfid_worker = RFIDWorker(
                    self.rfid_reader
                )

            return True

        print(
            f"[ADMIN] Restoring main RFID reader "
            f"(attempt {attempt}/{max_attempts})..."
        )

        try:
            self.rfid_reader = RFIDReader()

            self.admin_rfid_released = False

            if not config.SIMULATION_MODE:
                self.rfid_worker = RFIDWorker(
                    self.rfid_reader
                )

            print("[ADMIN] Main RFID reader restored.")

            return True

        except Exception as error:
            print(
                f"[ADMIN] RFID restore attempt "
                f"{attempt} failed: {error}"
            )

            self.rfid_reader = None

            if attempt < max_attempts:
                print(
                    "[ADMIN] Waiting before retrying "
                    "PN532 initialization..."
                )

                self.root.after(
                    1000,
                    lambda: self.restore_main_rfid(
                        attempt + 1,
                        max_attempts
                    )
                )

            else:
                print(
                    "[ADMIN] Could not restore main RFID "
                    "reader after all attempts."
                )

            return False

            
    def check_admin_process(
        self
    ):

        if self.admin_process is None:
            return

        if self.admin_process.poll() is None:
            return

        print()
        print("[ADMIN] Administration interface closed.")

        self.admin_process = None

        self.root.after(
            300,
            self.finish_admin_return
        )


    def finish_admin_return(
        self
    ):

        if not self.running:
            return

        if self.rfid_ipc_server is not None:
            print("[ADMIN] Closing RFID bridge...")
            if not self.rfid_ipc_server.close_if_idle():
                self.home_status_label.config(text="Finishing administrator card operation...")
                self.root.after(100, self.finish_admin_return)
                return
            self.rfid_ipc_server = None

        self.restore_main_camera()
        self.restore_main_rfid()
        # Refresh enrolled face data when the admin window closes.

        if self.face_recognition is not None:
            self.face_recognition.reload_templates()
        self.show_home_screen()


    def start_face_authentication(self):
        if self.camera is None or self.face_recognition is None:
            print("[FACE] Camera or face model unavailable; RFID remains active.")
            return
        if self.current_mode not in ("HOME", "FACE", "RFID"):
            return
        if not self.lock_controller.can_authenticate():
            self.face_status_label.config(text="WAIT FOR RELOCK")
            self.face_detail_label.config(text="Authentication resumes after the door relocks.")
            return
        if (self.current_mode == "FACE" and self.face_authentication_complete or
                self.current_mode == "RFID" and not self.rfid_authentication_active
                and self.access_controller.last_result != "PENDING_FACE"):
            return

        self.session_generation += 1
        self.current_mode = "FACE"
        self.face_authentication_active = True
        self.face_authentication_complete = False
        self.rfid_authentication_active = False
        self.recognized_face_id = None
        self.recognition_frames = 0
        self.recognition_frame_counts = {}
        self.recognition_frame_histories = {}
        self.face_overlay_entries = []
        self.rfid_input_frame.pack_forget()
        self.next_preview_frame = 0.0
        self.next_face_analysis = 0.0
        self.last_face_frame_sequence = None
        self.face_overlay = None
        self.cancel_button.config(state=tk.NORMAL)
        self.face_status_label.config(text="LOOK AT CAMERA")
        self.face_detail_label.config(text="Position your whole face in the guide.")
        print("[FACE] Facial authentication started in the main camera view.")


    def _record_recognition_frame(self, matched_face_ids):
        """Update one user's recent match history."""
        matched_face_ids = set(matched_face_ids)
        window_size = config.RECOGNITION_FRAME_WINDOW
        previous_histories = getattr(self, "recognition_frame_histories", {})
        histories = {}
        counts = {}

        for face_id in set(previous_histories) | matched_face_ids:
            history = list(previous_histories.get(face_id, []))
            history.append(face_id in matched_face_ids)
            history = history[-window_size:]
            match_count = sum(history)
            if match_count:
                histories[face_id] = history
                counts[face_id] = match_count

        self.recognition_frame_histories = histories
        self.recognition_frame_counts = counts

    def process_face(self, frame, faces=None, match=None):
        """Accept the face credential once enough recent frames match."""
        if not self.face_authentication_active or self.face_authentication_complete:
            return frame

        if faces is None:
            faces = self.face_recognition.detect_faces(frame)
        faces = list(faces or [])
        self.face_overlay_entries = []

        if not faces:
            self._record_recognition_frame(())
            self.recognized_face_id = None
            self.recognition_frames = 0
            dark = self.face_recognition.needs_more_light(frame)
            glare = self.face_recognition.needs_less_glare(frame)
            self.face_status_label.config(
                text="ADD LIGHT" if dark else "REDUCE GLARE" if glare else "LOOK AT CAMERA"
            )
            self.face_detail_label.config(
                text="Face too dark; add light near the camera."
                if dark else "Reduce glare on the camera or your face."
                if glare else "No face detected."
            )
            return frame

        # Handle both the newer per-face results and the older single-face format.

        if isinstance(match, list) and len(match) == len(faces):
            matches = match
        elif len(faces) == 1 and isinstance(match, tuple) and len(match) == 2:
            matches = [match]
        else:
            matches = [None] * len(faces)

        candidates = {}
        display_entries = []
        model_not_ready = False
        unrecognized = False
        unassigned = False
        outside_guide = False
        lighting_issues = set()

        for index, face in enumerate(faces):
            if not self.face_recognition.face_fully_in_frame(frame, face):
                outside_guide = True
                display_entries.append((face, "UNKNOWN", False))
                self.face_recognition.draw_labeled_face(frame, face, "UNKNOWN")
                continue

            lighting_issue = self.face_recognition.face_lighting_issue(frame, face)
            if lighting_issue is not None:
                lighting_issues.add(lighting_issue)
                display_entries.append((face, "UNKNOWN", False))
                self.face_recognition.draw_labeled_face(frame, face, "UNKNOWN")
                continue

            result = matches[index]
            if result is None:
                result = self.face_recognition.recognize_face(frame, face)
            if not isinstance(result, (tuple, list)) or len(result) != 2:
                result = (None, None)
            face_id, confidence = result

            if face_id is None and confidence is None:
                model_not_ready = True
                display_entries.append((face, "UNKNOWN", False))
                self.face_recognition.draw_labeled_face(frame, face, "UNKNOWN")
                continue

            if face_id is None:
                unrecognized = True
                display_entries.append((face, "UNKNOWN", False))
                self.face_recognition.draw_labeled_face(frame, face, "UNKNOWN")
                continue

            user = self.database.get_user_by_face_id(face_id)
            if user is None:
                unassigned = True
                display_entries.append((face, "UNKNOWN", False))
                self.face_recognition.draw_labeled_face(frame, face, "UNKNOWN")
                continue

            # Keep a separate recent-match history for each person.
            candidates.setdefault(face_id, {"user": user, "confidence": confidence})
            display_entries.append((face, user["name"], True))
            self.face_recognition.draw_labeled_face(
                frame, face, user["name"], known=True
            )

        self.face_overlay_entries = display_entries
        self._record_recognition_frame(candidates)

        if not candidates:
            self.recognized_face_id = None
            self.recognition_frames = 0
            if model_not_ready:
                self.face_status_label.config(text="FACE MODEL NOT READY")
                self.face_detail_label.config(text="No trained facial recognition model.")
            elif outside_guide:
                self.face_status_label.config(text="CENTER FACES")
                self.face_detail_label.config(
                    text="Fit each face you want to authenticate in the guide."
                )
            elif "ADD LIGHT" in lighting_issues:
                self.face_status_label.config(text="ADD LIGHT")
                self.face_detail_label.config(text="Face too dark; add light near the camera.")
            elif "REDUCE GLARE" in lighting_issues:
                self.face_status_label.config(text="REDUCE GLARE")
                self.face_detail_label.config(text="Reduce glare on the face or camera.")
            elif unassigned:
                self.face_status_label.config(text="ACCESS DENIED")
                self.face_detail_label.config(
                    text=f"No authorized face found among {len(faces)} detected."
                )
            elif unrecognized:
                self.face_status_label.config(text="FACE NOT RECOGNIZED")
                self.face_detail_label.config(
                    text=f"No enrolled face matched among {len(faces)} detected."
                )
            else:
                self.face_status_label.config(text="LOOK AT CAMERA")
                self.face_detail_label.config(text="Move into the camera guide.")
            return frame

        required = config.REQUIRED_RECOGNITION_FRAMES
        primary_id = max(
            candidates,
            key=lambda face_id: (
                self.recognition_frame_counts[face_id],
                candidates[face_id]["confidence"]
                if candidates[face_id]["confidence"] is not None else -1.0,
            ),
        )
        self.recognized_face_id = primary_id
        self.recognition_frames = self.recognition_frame_counts[primary_id]

        verification_details = []
        seen_names = set()
        for face_id, candidate in candidates.items():
            name = candidate["user"]["name"]
            if name not in seen_names:
                verification_details.append(
                    f"{name} {self.recognition_frame_counts[face_id]}/{required} "
                    f"of last {config.RECOGNITION_FRAME_WINDOW}"
                )
                seen_names.add(name)
        other_faces = len(faces) - sum(
            1 for entry in display_entries if entry[2]
        )
        if other_faces:
            verification_details.append(
                f"{other_faces} other face{'s' if other_faces != 1 else ''}"
            )

        if len(faces) > 1:
            self.face_status_label.config(text=f"VERIFYING {len(faces)} PEOPLE")
        else:
            self.face_status_label.config(text="VERIFYING...")
        self.face_detail_label.config(text=" | ".join(verification_details))

        ready_ids = [
            face_id for face_id in candidates
            if self.recognition_frame_counts.get(face_id, 0) >= required
        ]
        if not ready_ids:
            return frame

        # One recognized user is enough, even if other people are in view.

        grant_face_id = max(
            ready_ids,
            key=lambda face_id: (
                self.recognition_frame_counts[face_id],
                candidates[face_id]["confidence"]
                if candidates[face_id]["confidence"] is not None else -1.0,
            ),
        )
        user = candidates[grant_face_id]["user"]
        database_user_id = user["id"]

        print(
            f"[FACE] Face ID {grant_face_id} belongs to database user "
            f"{database_user_id} ({user['name']})."
        )

        self.face_authentication_complete = True
        self.face_authentication_active = False

        success = self.access_controller.authenticate_face(database_user_id)

        if self.access_controller.last_result == "ACTUATING":
            self.face_status_label.config(text="UNLOCKING")
            self.face_detail_label.config(text="Please wait while the lock moves.")
            return frame

        if self.access_controller.last_result == "PENDING_RFID":
            self.start_rfid_authentication()
            self.face_status_label.config(text="FACE VERIFIED")
            self.face_detail_label.config(
                text="Present your RFID card within 30 seconds."
                if not config.SIMULATION_MODE else
                "Type the RFID UID and press Enter within 30 seconds."
            )
            return frame

        if success:
            self.face_status_label.config(text="ACCESS GRANTED")
            self.face_detail_label.config(text=f"Welcome, {user['name']}.")
            print(f"[FACE] Access granted to {user['name']}.")
            self.schedule_session_action(
                config.ACCESS_GRANTED_DISPLAY_TIME, self.show_home_screen
            )
        else:
            self.face_status_label.config(
                text="WAIT FOR RELOCK"
                if self.access_controller.last_result == "WAIT_FOR_RELOCK"
                else "ACCESS DENIED"
            )
            self.face_detail_label.config(
                text="Authentication resumes after the door relocks."
                if self.access_controller.last_result == "WAIT_FOR_RELOCK"
                else "Authentication failed."
            )
            self.schedule_session_action(
                config.ACCESS_DENIED_DISPLAY_TIME, self.restart_face_authentication
            )

        return frame


    def restart_face_authentication(
        self
    ):

        if self.current_mode != "FACE":

            return
        if not self.lock_controller.can_authenticate():
            self.show_home_screen()
            return

        self.recognized_face_id = None

        self.recognition_frames = 0

        self.recognition_frame_counts = {}
        self.recognition_frame_histories = {}
        self.face_overlay_entries = []

        self.face_authentication_complete = False

        self.face_authentication_active = True

        self.face_status_label.config(
            text="LOOK AT CAMERA"
        )

        self.face_detail_label.config(
            text="Position your face in view."
        )


    def on_simulation_rfid_key(self, _event=None):
        if (config.SIMULATION_MODE and self.rfid_reader is not None and
                self.current_mode in ("HOME", "FACE")):
            self.start_rfid_authentication()

    def start_rfid_authentication(self):
        if self.rfid_reader is None:
            print("[RFID] Reader unavailable.")
            return
        if self.current_mode not in ("HOME", "FACE", "RFID"):
            return
        if not self.lock_controller.can_authenticate():
            self.face_status_label.config(text="WAIT FOR RELOCK")
            self.face_detail_label.config(text="Authentication resumes after the door relocks.")
            return
        if (self.current_mode == "FACE" and self.face_authentication_complete
                and self.access_controller.last_result != "PENDING_RFID" or
                self.current_mode == "RFID" and not self.rfid_authentication_active):
            return

        self.session_generation += 1
        self.current_mode = "RFID"
        self.face_authentication_active = False
        self.face_authentication_complete = False
        self.rfid_authentication_active = True
        self.cancel_button.config(state=tk.NORMAL)

        # Presenting a real card can start RFID verification from the home screen.
        self.rfid_status_label = self.face_status_label
        self.rfid_detail_label = self.face_detail_label
        self.rfid_status_label.config(text="SCAN YOUR CARD")
        self.rfid_detail_label.config(
            text="Hold your VeriLock card near the reader."
            if not config.SIMULATION_MODE else
            "Enter a UID and press SCAN or Enter."
        )
        if config.SIMULATION_MODE:
            self.rfid_entry.delete(0, tk.END)
            self.rfid_input_frame.pack(pady=(4, 0))
            self.rfid_entry.focus_set()
        else:
            self.rfid_input_frame.pack_forget()
        print("[RFID] Waiting for card in the main camera view.")


    def simulate_rfid_scan(
        self
    ):

        if not config.SIMULATION_MODE:

            return

        if not self.rfid_authentication_active:

            return

        uid = (
            self.rfid_entry
            .get()
            .strip()
        )

        if not uid:

            self.rfid_status_label.config(
                text="ENTER RFID UID"
            )

            self.rfid_detail_label.config(
                text=(
                    "Enter a simulated UID "
                    "and try again."
                )
            )

            return

        scanned_uid = (
            self.rfid_reader
            .simulate_scan(
                uid
            )
        )

        if scanned_uid is None:

            return

        self.process_rfid_uid(
            scanned_uid
        )


    def process_rfid_uid(
        self,
        uid,
        card_user_id=None,
        require_programmed=False
    ):

        if not self.rfid_authentication_active:

            return

        uid = (
            str(uid)
            .strip()
            .upper()
        )

        print(
            f"[RFID] Authenticating UID: "
            f"{uid}"
        )

        # Skip new scans until the current card has been handled.

        self.rfid_authentication_active = False

        if require_programmed and card_user_id is None:
            print("[RFID] Card is not a valid programmed VeriLock card.")
            success = self.access_controller.reject_rfid(uid)
        else:
            success = self.access_controller.authenticate_rfid(
                uid, card_user_id=card_user_id
            )

        if self.access_controller.last_result == "ACTUATING":
            self.rfid_status_label.config(text="UNLOCKING")
            self.rfid_detail_label.config(text="Please wait while the lock moves.")
            return

        if self.access_controller.last_result == "PENDING_FACE":
            self.rfid_status_label.config(text="CARD VERIFIED")
            self.rfid_detail_label.config(text="Look at the camera within 30 seconds.")
            self.schedule_session_action(0.8, self.start_face_authentication)
            return


        if success:

            user = (
                self.database
                .get_user_by_rfid(
                    uid
                )
            )

            self.rfid_status_label.config(
                text="ACCESS GRANTED"
            )

            if user is not None:

                self.rfid_detail_label.config(
                    text=(
                        f"Welcome, "
                        f"{user['name']}."
                    )
                )

            else:

                self.rfid_detail_label.config(
                    text=(
                        "Authentication successful."
                    )
                )

            self.schedule_session_action(
                config.ACCESS_GRANTED_DISPLAY_TIME, self.show_home_screen
            )


        else:

            self.rfid_status_label.config(
                text="WAIT FOR RELOCK" if self.access_controller.last_result == "WAIT_FOR_RELOCK"
                else "ACCESS DENIED"
            )

            self.rfid_detail_label.config(
                text=("Authentication resumes after the door relocks."
                      if self.access_controller.last_result == "WAIT_FOR_RELOCK"
                      else "Card not recognized. Try again.")
            )

            # Allow another scan after an access denial.

            self.schedule_session_action(
                config.ACCESS_DENIED_DISPLAY_TIME, self.restart_rfid_authentication
            )


    def restart_rfid_authentication(
        self
    ):
        if self.current_mode != "RFID":
            return
        if not self.lock_controller.can_authenticate():
            self.show_home_screen()
            return
        self.rfid_authentication_active = True
        self.rfid_status_label.config(
            text="SCAN YOUR CARD"
        )
        self.rfid_detail_label.config(
            text=(
                "Hold your VeriLock card "
                "near the reader."
            )
        )
        if (
            config.SIMULATION_MODE
            and
            hasattr(
                self,
                "rfid_entry"
            )
        ):
            self.rfid_entry.delete(
                0,
                tk.END
            )


    def update_camera(self):
        if self.current_mode != "FACE":
            return
        now = time.monotonic()
        if (now < self.next_preview_frame and
                (not self.face_authentication_active or now < self.next_face_analysis)):
            return
        if isinstance(self.camera, Camera):
            frame, sequence = self.camera.get_frame_with_sequence()
        else:
            frame, sequence = self.camera.get_frame(), None
        if frame is None:
            if self.face_authentication_active:
                self.face_status_label.config(text="CAMERA ERROR")
                self.face_detail_label.config(text="Could not capture camera frame.")
            return
        if not self.face_authentication_active:
            if now >= self.next_preview_frame:
                self.next_preview_frame = now + config.CAMERA_PREVIEW_INTERVAL
                self.display_camera_frame(frame)
            return
        if self.face_worker is not None:
            if (now >= self.next_face_analysis and
                    (sequence is None or sequence != self.last_face_frame_sequence)):
                self.next_face_analysis = now + config.FACE_ANALYSIS_INTERVAL
                self.last_face_frame_sequence = sequence
                self.face_worker.submit(self.session_generation, frame)
            if now >= self.next_preview_frame:
                self.next_preview_frame = now + config.CAMERA_PREVIEW_INTERVAL
                self.display_camera_frame(frame, show_guide=True,
                                          face_overlay=self.face_overlay)
        else:
            self.display_camera_frame(self.process_face(frame), show_guide=True)

    def update_face_results(self):
        if self.face_worker is None:
            return
        try:
            generation, frame, faces, match, error = self.face_worker.results.get_nowait()
        except queue.Empty:
            return
        if (generation != self.session_generation or self.current_mode != "FACE"
                or not self.face_authentication_active):
            return
        if error is not None:
            print(f"[FACE] Inference error: {error}")
            self.face_status_label.config(text="CAMERA ERROR")
            self.face_detail_label.config(text="Could not analyze this frame.")
            return
        # Match faces using the analyzed frame while the preview keeps updating.

        self.process_face(frame, faces, match)
        if self.current_mode != "FACE" or not self.face_authentication_active:
            self.face_overlay = None
            return
        entries = getattr(self, "face_overlay_entries", [])
        if faces and len(entries) == len(faces):
            labels = [entry[1] for entry in entries]
            known_flags = [entry[2] for entry in entries]
        else:
            labels = "UNKNOWN"
            known_flags = False
        self.face_overlay = (time.monotonic(), faces, labels, known_flags)

    def display_camera_frame(self, frame, show_guide=False, face_overlay=None):
        """Show the live camera frame in the main window."""
        if not self.camera_label.winfo_exists():
            return
        view = frame.copy()
        if face_overlay is not None:
            timestamp, faces, labels, known_flags = face_overlay
            if time.monotonic() - timestamp < 0.5:
                if isinstance(labels, (list, tuple)) and len(labels) == len(faces):
                    face_labels = labels
                else:
                    face_labels = [labels] * len(faces)
                if isinstance(known_flags, (list, tuple)) and len(known_flags) == len(faces):
                    face_known_flags = known_flags
                else:
                    face_known_flags = [known_flags] * len(faces)
                for face, label, known in zip(faces, face_labels, face_known_flags):
                    self.face_recognition.draw_labeled_face(
                        view, face, label, known=known,
                    )
        if show_guide and self.face_recognition is not None:
            zx, zy, zw, zh = self.face_recognition.detection_zone(view.shape)
            cv2.rectangle(view, (zx, zy), (zx + zw, zy + zh), (255, 180, 0), 1)
        rgb = cv2.cvtColor(view, cv2.COLOR_BGR2RGB)
        rgb = cv2.resize(rgb, (360, 270))
        self.camera_image = ImageTk.PhotoImage(Image.fromarray(rgb))
        self.camera_label.config(image=self.camera_image)

    def update_idle_camera(self):
        """Refresh the preview without running face recognition."""
        if self.camera is None:
            return
        now = time.monotonic()
        if now < self.next_preview_frame:
            return
        self.next_preview_frame = now + config.CAMERA_PREVIEW_INTERVAL
        frame = self.camera.get_frame()
        if frame is not None:
            self.display_camera_frame(frame)


    def update_rfid(
        self
    ):
        """Process a card read by the PN532 worker."""
        if self.rfid_worker is None:
            return
        try:
            uid, result = self.rfid_worker.results.get_nowait()
        except queue.Empty:
            return
        if (self.current_mode not in ("HOME", "FACE", "RFID")
                or not self.lock_controller.can_authenticate()):
            return
        if self.current_mode == "RFID" and not self.rfid_authentication_active:
            return
        if (self.current_mode == "FACE" and not self.face_authentication_active
                and self.access_controller.last_result != "PENDING_RFID"):
            return

        if self.current_mode != "RFID":
            self.start_rfid_authentication()
        self.process_rfid_uid(
            uid,
            card_user_id=result.get("user_id") if result.get("valid") else None,
            require_programmed=True,
        )


    def update(
        self
    ):

        if not self.running:
            return

        try:


            self.check_admin_process()
            self.update_access_results()
            self.refresh_lock_status()


            if self.current_mode == "FACE":
                self.update_camera()
                self.update_face_results()
            elif self.current_mode in ("HOME", "RFID"):
                self.update_idle_camera()


            self.update_rfid()
            if (self.access_controller.pending_factor is not None and
                    not self.access_controller.pending_seconds_remaining() and
                    self.current_mode in ("FACE", "RFID")):
                self.show_home_screen()


            # Rack movement is handled by the two-servo lock sequence.
        except Exception as error:
            print(
                f"[GUI] Update error: "
                f"{error}"
            )


        if self.running:
            self.root.after(
                30,
                self.update
            )

    def refresh_lock_status(self):
        if self.current_mode != "HOME":
            return
        ready = self.lock_controller.can_authenticate()
        status_key = (ready, self.lock_controller.autolock_enabled)
        if status_key == self.last_lock_ready:
            return
        self.last_lock_ready = status_key
        self.face_button.config(state=tk.NORMAL if ready and self.camera is not None
                                and self.face_recognition is not None else tk.DISABLED)
        self.home_status_label.config(
            text=self.home_idle_message if ready else
            ("Door unlocked. Authentication resumes after relock."
             if self.lock_controller.autolock_enabled else
             "Automatic relocking is OFF. Use LOCK to relock.")
        )


    def prevent_window_close(self):
        """Keep the app open when the close button is pressed; use the shutdown process instead."""
        print("[SYSTEM] Window close request ignored in kiosk mode.")


    def handle_shutdown_signal(self, signum, frame):
        print(f"[SYSTEM] Shutdown signal received: {signum}")
        try:
            self.root.after(0, self.shutdown)
        except Exception:
            self.shutdown()


    def cleanup_hardware(self):
        print("[SYSTEM] Performing fail-safe hardware cleanup...")
        if self._camera_recovery is not None:
            self._camera_recovery["cancel"].set()
        # Stop new lock commands before cleaning up the servo connections.
        if self.lock_controller is not None:
            self.lock_controller.request_stop()
        # Let any active rack or bolt movement finish before cleanup.
        for name in ("face_worker", "rfid_worker"):
            worker = getattr(self, name, None)
            if worker is not None:
                worker.stopping.set()
        rfid_stopped = True
        for name in ("face_worker", "rfid_worker"):
            worker = getattr(self, name, None)
            if worker is not None:
                try:
                    stopped = worker.close(timeout=0.1)
                    if not stopped:
                        print(f"[SHUTDOWN] {name} is still busy; continuing shutdown.")
                    if name == "rfid_worker":
                        rfid_stopped = stopped
                except Exception as error:
                    print(f"[SHUTDOWN] {name}: {error}")
                    if name == "rfid_worker":
                        rfid_stopped = False

        lock_idle = self.lock_controller is None or self.lock_controller.cleanup(timeout=0.1)
        for device, method, label in (
            (self.servo if lock_idle else None, "cleanup", "Servo"),
            (self.rack_servo if lock_idle else None, "cleanup", "Rack servo"),
            (self.camera, "release", "Camera"),
        ):
            try:
                if device is not None:
                    getattr(device, method)()
            except Exception as error:
                print(f"[SHUTDOWN] {label} cleanup error: {error}")

        bridge = getattr(self, "rfid_ipc_server", None)
        bridge_idle = bridge is None or bridge.lock.acquire(blocking=False)
        try:
            if rfid_stopped and bridge_idle and self.rfid_reader is not None:
                self.rfid_reader.cleanup()
            elif self.rfid_reader is not None:
                print("[SHUTDOWN] PN532 still busy; deferring release to process exit.")
        except Exception as error:
            print(f"[SHUTDOWN] RFID cleanup error: {error}")
        finally:
            if bridge is not None and bridge_idle:
                bridge.lock.release()
        print("[SYSTEM] Hardware cleanup complete.")


    def shutdown(
        self
    ):
        # Shutdown can come from the window, a signal, or an error.

        if getattr(self, "shutdown_started", False):
            return

        self.shutdown_started = True

        print()
        print("================================")
        print("[SYSTEM] Shutting down VeriLock...")

        self.running = False

        self.cleanup_hardware()

        print()
        print("================================")
        print("     VERILOCK SHUTDOWN COMPLETE")
        print("================================")

        try:
            self.root.destroy()
        except Exception:
            pass


def main():
    print()
    print(
        "================================"
    )
    print(
        "            VeriLock"
    )
    print(
        "================================"
    )
    print()


    if config.SIMULATION_MODE:
        print(
            f"[SYSTEM] Mode: {config.PLATFORM_NAME}"
        )
    else:
        print(
            "[SYSTEM] Mode: RASPBERRY PI"
        )
    print()


    database = None
    servo = None
    rack_servo = None
    lock_controller = None
    access_controller = None
    rfid_reader = None
    camera = None
    face_recognition = None
    root = None

    try:


        print(
            "[SYSTEM] Initializing database..."
        )
        database = Database()


        print(
            "[SYSTEM] Initializing servo..."
        )
        servo = Servo()


        print(
            "[SYSTEM] Initializing rack servo..."
        )
        rack_servo = RackServo()


        print(
            "[SYSTEM] Initializing "
            "lock controller..."
        )
        lock_controller = (
            LockController(
                servo,
                rack_servo,
                autolock_enabled=database.autolock_enabled()
            )
        )


        # Set the deadbolt servo to its starting angle with the rack out of the way.
        # Lock the door before enabling scans. If either movement fails,
        # stop startup and clean up the hardware.
        print("[SYSTEM] Disengaging the rack before setting the deadbolt servo to 0 degrees...")
        rack_servo.disengage()
        print("[SYSTEM] Setting the deadbolt servo to its 0-degree starting position...")
        servo.move_to(0.0)
        print("[SYSTEM] Locking the mechanism before enabling authentication...")
        lock_controller.lock()
        print("[SYSTEM] Startup lock sequence completed.")

        print(
            "[SYSTEM] Initializing "
            "access controller..."
        )

        access_controller = (
            AccessController(
                database,
                lock_controller
            )
        )


        print(
            "[SYSTEM] Initializing "
            "RFID reader..."
        )
        rfid_reader = None

        for attempt in range(1, 6):
            try:
                print(
                    f"[SYSTEM] RFID initialization "
                    f"attempt {attempt}/5..."
                )

                rfid_reader = RFIDReader()

                print(
                    "[SYSTEM] RFID reader initialized successfully."
                )

                break

            except Exception as error:
                rfid_reader = None

                print(
                    f"[SYSTEM] RFID initialization "
                    f"attempt {attempt} failed: {error}"
                )

                if attempt < 5:
                    print(
                        "[SYSTEM] Waiting before retrying "
                        "PN532 initialization..."
                    )

                    time.sleep(1.0)

                else:
                    print(
                        "[SYSTEM] RFID reader unavailable "
                        "after 5 attempts."
                    )


        print(
            "[SYSTEM] Initializing camera..."
        )
        try:
            camera = Camera()
        except Exception as error:
            print(f"[SYSTEM] Camera unavailable: {error}")
            print("[SYSTEM] RFID access remains available.")


        print(
            "[SYSTEM] Initializing "
            "face authentication..."
        )
        if camera is not None:
            try:
                face_recognition = FaceRecognition()
            except Exception as error:
                print(f"[SYSTEM] Face recognition unavailable: {error}")
                print("[SYSTEM] RFID access remains available if initialized.")

        if rfid_reader is None and face_recognition is None:
            raise RuntimeError("Neither RFID nor facial authentication is available.")


        print(
            "[SYSTEM] Initializing GUI..."
        )
        root = tk.Tk(className="VeriLock")
        app = VeriLockApp(
            root=root,
            database=database,
            servo=servo,
            rack_servo=rack_servo,
            lock_controller=lock_controller,
            access_controller=access_controller,
            rfid_reader=rfid_reader,
            camera=camera,
            face_recognition=face_recognition
        )


        print()
        print(
            "================================"
        )
        print(
            "         SYSTEM READY"
        )
        print(
            "================================"
        )
        root.mainloop()
        return 0


    except Exception as error:
        print()
        print(
            "================================"
        )
        print(
            "         SYSTEM ERROR"
        )
        print(
            "================================"
        )
        print()
        print(
            error
        )
        traceback.print_exc()
        print()
        print(
            "================================"
        )


        for component, method_name in (
            (rack_servo, "cleanup"),
            (servo, "cleanup"),
            (camera, "release"),
            (rfid_reader, "cleanup"),
        ):
            try:
                if component is not None and hasattr(component, method_name):
                    getattr(component, method_name)()
            except Exception:
                pass

        print()
        print(
            "================================"
        )
        print(
            "     VERILOCK SHUTDOWN COMPLETE"
        )
        print(
            "================================"
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
