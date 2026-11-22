"""
VeriLock admin interface.

An enabled ADMIN account must verify its credentials before the dashboard opens.

The dashboard lets administrators manage users, enroll faces, assign RFID cards,
enable or disable accounts, and remove credentials. Card assignment checks the
user and admin session. The main screen’s LOCK action also requires verification.
Enrollment can finish or be cancelled without blocking the interface.
"""

import shutil
import tkinter as tk
from admin_touch_keyboard import AdminTouchKeyboard
import queue
import time
import os

from pathlib import Path
from tkinter import ttk

import cv2
from PIL import Image, ImageTk

import config
from monitor_layout import place_window_on_monitor, create_popup, show_messagebox


# VeriLock screen styling


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


from database import Database
from camera import Camera
from face_authentication import FaceRecognition
from rfid import RFIDReader
from rfid_ipc import RFIDIPCClient
from background_workers import EnrollmentWorker
from ui_tasks import background_callback, background_method

class VeriLockAdmin:

    
    # Set up the admin window
    

    def __init__(
        self,
        root
    ):

        self.root = root
        self.touch_keyboard = AdminTouchKeyboard(self.root)

        self.root.title(
            "VeriLock Administration"
        )

        self.root.configure(bg=BG)

        
        # Admin button styling
        
        # Apply the same light navy style to all Tk buttons
        # used by the administration screens and popups.

        self.root.option_add("*Button.background", BUTTON_BG)
        self.root.option_add("*Button.foreground", TEXT)
        self.root.option_add("*Button.activeBackground", BUTTON_ACTIVE)
        self.root.option_add("*Button.activeForeground", TEXT)
        self.root.option_add("*Button.relief", "flat")
        self.root.option_add("*Button.borderWidth", 0)
        self.root.option_add("*Button.font", ("Arial", 10, "bold"))

        
        # Admin text styling
        
        # Apply the same light-on-dark text styling throughout
        # administration screens and popup windows.

        self.root.option_add("*Label.background", BG)
        self.root.option_add("*Label.foreground", TEXT)
        self.root.option_add("*Label.font", ("Arial", 11))

        self.root.option_add("*Checkbutton.background", BG)
        self.root.option_add("*Checkbutton.foreground", TEXT)
        self.root.option_add("*Checkbutton.activeBackground", BG)
        self.root.option_add("*Checkbutton.activeForeground", TEXT)

        self.root.option_add("*Radiobutton.background", BG)
        self.root.option_add("*Radiobutton.foreground", TEXT)
        self.root.option_add("*Radiobutton.activeBackground", BG)
        self.root.option_add("*Radiobutton.activeForeground", TEXT)

        self.root.option_add("*Entry.background", PANEL_BG)
        self.root.option_add("*Entry.foreground", TEXT)
        self.root.option_add("*Entry.insertBackground", TEXT)

        self.root.option_add("*Toplevel.background", BG)

        
        # Admin table styling
        

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(
            "Treeview",
            background=PANEL_BG,
            fieldbackground=PANEL_BG,
            foreground=TEXT,
            rowheight=26,
            bordercolor=BORDER
        )
        style.map(
            "Treeview",
            background=[("selected", PRIMARY)],
            foreground=[("selected", TEXT)]
        )
        style.configure(
            "Treeview.Heading",
            background=PANEL_ALT,
            foreground=TEXT,
            relief="flat"
        )

        self.root.geometry(f"{config.DISPLAY_WIDTH}x{config.DISPLAY_HEIGHT}")
        if not config.SIMULATION_MODE:
            place_window_on_monitor(
                self.root,
                config.DISPLAY_WIDTH,
                config.DISPLAY_HEIGHT,
            )

        self.root.minsize(config.DISPLAY_WIDTH, config.DISPLAY_HEIGHT)

        print()
        print(
            "================================"
        )
        print(
            "     VERILOCK ADMINISTRATION"
        )
        print(
            "================================"
        )

        
        # Set up hardware components
        

        print(
            "[ADMIN] Initializing database..."
        )

        self.database = Database()

        print(
            "[ADMIN] Initializing RFID..."
        )

        self.rfid_reader = None

        if config.SIMULATION_MODE and not os.environ.get("VERILOCK_RFID_IPC_PORT"):
            # Simulation can run on its own because it does not use the Pi GPIO pins.
            try:
                self.rfid_reader = RFIDReader()
                print("[ADMIN] Simulation RFID initialized.")
            except Exception as error:
                print(f"[ADMIN] Simulation RFID unavailable: {error}")
        else:
            # Send hardware requests to main.py, which controls the PN532 and GPIO pins.
            try:
                port = os.environ.get("VERILOCK_RFID_IPC_PORT")
                token = os.environ.get("VERILOCK_RFID_IPC_TOKEN")
                if not port or not token:
                    raise RuntimeError("RFID bridge information was not provided by main.py.")
                self.rfid_reader = RFIDIPCClient(port, token)
                print("[ADMIN] Connected to main RFID bridge.")
            except Exception as error:
                self.rfid_reader = None
                print(f"[ADMIN] RFID bridge unavailable: {error}")

        self.camera = None
        self.face_recognition = None

        
        # Track authentication progress
        

        self.authenticated_admin = None

        self.admin_face_active = False
        self.admin_face_matches = 0
        self.admin_face_candidate = None


        
        # Track authentication progress
        

        self.authenticated_admin = None

        self.admin_face_active = False
        self.admin_face_matches = 0
        self.admin_face_candidate = None

        # Require five consecutive matches from distinct captured frames
        # before granting dashboard access or the requested LOCK action.

        self.admin_required_matches = 5

        
        # Track face enrollment
        

        self.enrollment_active = False
        self.enrollment_user_id = None
        self.enrollment_face_id = None
        self.enrollment_sample = 0
        self.enrollment_frame_counter = 0
        self.enrollment_capture_started = False
        self.enrollment_worker = None
        self.enrollment_generation = 0
        self.enrollment_overlay = None
        self.next_enrollment_preview = 0.0
        self.next_enrollment_analysis = 0.0
        self.last_enrollment_frame_sequence = None

        # Keep Tkinter image references alive.

        self.camera_image = None
        self.admin_camera_image = None

        
        # Handle window closing
        

        self.root.protocol(
            "WM_DELETE_WINDOW",
            self.shutdown
        )

        
        # Start on the locked screen
        

        self.build_access_screen()

    
    # Clear the current screen
    

    def clear_window(
        self
    ):

        for widget in (
            self.root.winfo_children()
        ):

            widget.destroy()

    
    # Show the access screen
    

    def build_access_screen(
        self
    ):

        self.clear_window()

        self.authenticated_admin = None

        self.root.title(
            "VeriLock Admin Access"
        )

        container = tk.Frame(
            self.root,
            bg=BG
        )

        container.pack(
            expand=True
        )

        tk.Label(
            container,
            text="VeriLock",
            font=(
                "Arial",
                32,
                "bold"
            )
        ).pack(
            pady=(20, 5)
        )

        tk.Label(
            container,
            text="ADMINISTRATION",
            font=(
                "Arial",
                18,
                "bold"
            )
        ).pack(
            pady=5
        )

        tk.Label(
            container,
            text=(
                "Administrator verification required"
            ),
            font=(
                "Arial",
                12
            )
        ).pack(
            pady=(5, 30)
        )

        tk.Button(
            container,
            text="VERIFY WITH FACE",
            width=28,
            height=2,
            command=self.start_admin_face_verification
        ).pack(
            pady=10
        )

        tk.Button(
            container,
            text="VERIFY WITH RFID",
            width=28,
            height=2,
            command=self.start_admin_rfid_verification
        ).pack(
            pady=10
        )

        tk.Button(
            container,
            text="EXIT",
            width=28,
            command=self.shutdown
        ).pack(
            pady=(30, 10)
        )

    
    # Start face recognition
    

    def initialize_face_system(
        self
    ):

        if self.camera is None:

            self.camera = Camera()

        if self.face_recognition is None:

            self.face_recognition = (
                FaceRecognition()
            )

    
    # Verify admin by face
    

    def start_admin_face_verification(
        self
    ):

        try:

            self.initialize_face_system()

        except Exception as error:

            show_messagebox(self.root, 'showerror', 
                "Camera Error",
                str(error)
            )

            return

        self.admin_face_active = True

        self.last_admin_face_sequence = None
        self.admin_face_matches = 0
        self.admin_face_candidate = None

        self.admin_face_window = (
            create_popup(
                self.root,
                bg=BG
            )
        )

        self.admin_face_window.title(
            "Administrator Face Verification"
        )

        self.admin_face_window.geometry(
            f"{config.DISPLAY_WIDTH}x{config.DISPLAY_HEIGHT}"
        )
        place_window_on_monitor(
            self.admin_face_window,
            config.DISPLAY_WIDTH,
            config.DISPLAY_HEIGHT,
        )

        self.admin_face_window.resizable(
            False,
            False
        )

        self.admin_face_window.protocol(
            "WM_DELETE_WINDOW",
            self.cancel_admin_face
        )

        tk.Label(
            self.admin_face_window,
            bg=BG,
            fg=TEXT,
            text="Administrator Verification",
            font=(
                "Arial",
                22,
                "bold"
            )
        ).pack(
            pady=10
        )

        tk.Label(
            self.admin_face_window,
            bg=BG,
            fg=TEXT,
            text=(
                "Look directly at the camera"
            ),
            font=(
                "Arial",
                12
            )
        ).pack()

        self.admin_camera_label = (
            tk.Label(
                self.admin_face_window
            )
        )

        self.admin_camera_label.pack(
            pady=10
        )

        self.admin_face_status = (
            tk.Label(
                self.admin_face_window,
                bg=BG,
                fg=TEXT,
                text="Searching for face...",
                font=(
                    "Arial",
                    14,
                    "bold"
                )
            )
        )

        self.admin_face_status.pack(
            pady=10
        )

        tk.Button(
            self.admin_face_window,
            bg=BUTTON_BG,
            fg=TEXT,
            activebackground=BUTTON_ACTIVE,
            activeforeground=TEXT,
            highlightbackground=PRIMARY,
            highlightcolor=PRIMARY,
            relief="flat",
            bd=1,
            text="CANCEL",
            width=15,
            command=self.cancel_admin_face
        ).pack(
            pady=10
        )

        self.admin_face_window.after(
            30,
            self.update_admin_face
        )

    
    # Check the latest admin face
    

    @background_method
    def update_admin_face(
        self
    ):

        if not self.admin_face_active:

            return

        try:

            if not self.admin_face_window.winfo_exists():

                return

        except Exception:

            return

        try:

            frame, sequence = self.camera.get_frame_with_sequence()

            if frame is None or sequence is None:

                self.admin_face_status.config(
                    text="Camera frame unavailable."
                )

                self.admin_face_window.after(
                    100,
                    self.update_admin_face
                )

                return

            # A cached frame should not count as another verification match.
            previous_sequence = getattr(self, 'last_admin_face_sequence', None)
            if previous_sequence is not None and sequence <= previous_sequence:
                self.admin_face_window.after(30, self.update_admin_face)
                return
            self.last_admin_face_sequence = sequence

            faces = (
                (yield lambda: self.face_recognition
                .detect_faces(
                    frame
                ))
            )
            label = "UNKNOWN"
            known = False
            light_issue = (
                (yield lambda: self.face_recognition.face_lighting_issue(frame, faces[0]))
                if len(faces) == 1 else None
            )

            
            # No face found
            

            if len(faces) == 1 and light_issue is not None:
                self.admin_face_status.config(
                    text="Add light near the camera."
                    if light_issue == "ADD LIGHT" else "Reduce glare on your face."
                )
                self.admin_face_matches = 0
                self.admin_face_candidate = None

            elif (
                len(faces) == 1
                and not (yield lambda: self.face_recognition.face_fully_in_frame(frame, faces[0]))
            ):
                self.admin_face_status.config(text="Fit your whole face in the guide; adjust distance.")
                self.admin_face_matches = 0
                self.admin_face_candidate = None

            elif len(faces) == 0:

                self.admin_face_status.config(
                    text=("Add light near the camera."
                          if (yield lambda: self.face_recognition.needs_more_light(frame))
                          else "Reduce glare on the camera."
                          if (yield lambda: self.face_recognition.needs_less_glare(frame))
                          else "No face detected.")
                )

                self.admin_face_matches = 0
                self.admin_face_candidate = None

            
            # More than one face
            

            elif len(faces) > 1:

                self.admin_face_status.config(
                    text=(
                        "Multiple faces detected. "
                        "Only one person should be visible."
                    )
                )

                self.admin_face_matches = 0
                self.admin_face_candidate = None

            
            # One face found
            

            else:

                face_coordinates = faces[0]

                face_id, confidence = (
                    (yield lambda: self.face_recognition
                    .recognize_face(
                        frame,
                        face_coordinates
                    ))
                )

                
                # Face not recognized
                

                if face_id is None:

                    self.admin_face_status.config(
                        text="Face not recognized."
                    )

                    self.admin_face_matches = 0
                    self.admin_face_candidate = None

                
                # Face recognized
                

                else:

                    user = (
                        self.database
                        .get_user_by_face_id(
                            face_id
                        )
                    )

                    if user is None:

                        self.admin_face_status.config(
                            text=(
                                "Face recognized but no "
                                "enabled user is assigned."
                            )
                        )

                        self.admin_face_matches = 0
                        self.admin_face_candidate = None

                    
                    # User is not an admin
                    

                    elif not self.database.is_admin(
                        user["id"]
                    ):
                        label = user["name"]
                        known = True

                        self.admin_face_status.config(
                            text=(
                                f"{user['name']} verified, "
                                "but administrator access "
                                "is not authorized."
                            )
                        )

                        self.admin_face_matches = 0
                        self.admin_face_candidate = None

                    
                    # Administrator verified
                    

                    else:
                        label = user["name"]
                        known = True

                        user_id = user["id"]

                        if (
                            self.admin_face_candidate
                            ==
                            user_id
                        ):

                            self.admin_face_matches += 1

                        else:

                            self.admin_face_candidate = (
                                user_id
                            )

                            self.admin_face_matches = 1

                        self.admin_face_status.config(
                            text=(
                                f"Verifying {user['name']}... "
                                f"{self.admin_face_matches}/"
                                f"{self.admin_required_matches}"
                            )
                        )

                        if (
                            self.admin_face_matches
                            >=
                            self.admin_required_matches
                        ):

                            self.admin_face_active = False

                            self.database.log_access(
                                user["id"],
                                "ADMIN_FACE",
                                "GRANTED"
                            )

                            self.grant_admin_access(
                                user
                            )

                            return

            
            # Update the camera preview
            

            if len(faces) == 1:
                self.face_recognition.draw_labeled_face(
                    frame, faces[0], label, known=known
                )
            elif len(faces) > 1:
                for face in faces:
                    self.face_recognition.draw_labeled_face(frame, face, "UNKNOWN")

            zx, zy, zw, zh = self.face_recognition.detection_zone(frame.shape)
            cv2.rectangle(frame, (zx, zy), (zx + zw, zy + zh), (255, 180, 0), 1)

            frame_rgb = cv2.cvtColor(
                frame,
                cv2.COLOR_BGR2RGB
            )

            frame_rgb = cv2.resize(
                frame_rgb,
                (
                    480,
                    360
                )
            )

            image = Image.fromarray(
                frame_rgb
            )

            self.admin_camera_image = (
                ImageTk.PhotoImage(
                    image=image
                )
            )

            self.admin_camera_label.config(
                image=self.admin_camera_image
            )

        except Exception as error:

            print(
                "[ADMIN] Face verification error: "
                f"{error}"
            )

            try:

                self.admin_face_status.config(
                    text=(
                        "Verification error: "
                        f"{error}"
                    )
                )

            except Exception:

                pass

        if self.admin_face_active:

            self.admin_face_window.after(
                30,
                self.update_admin_face
            )

    
    # Cancel administrator face verification
    

    def cancel_admin_face(
        self
    ):

        self.admin_face_active = False

        self.admin_face_matches = 0
        self.admin_face_candidate = None

        try:

            self.admin_face_window.destroy()

        except Exception:

            pass

    
    # Verify the administrator’s RFID card
    

    def start_admin_rfid_verification(
        self
    ):

        if self.rfid_reader is None:
            show_messagebox(self.root, 'showerror', "RFID", "RFID reader is unavailable.")
            return

        
        # Laptop simulation
        

        if config.SIMULATION_MODE:

            window = create_popup(
                self.root
            )

            window.title(
                "Administrator RFID Verification"
            )

            window.geometry(
                "420x300"
            )
            place_window_on_monitor(window, 420, 300)

            window.resizable(
                False,
                False
            )

            tk.Label(
                window,
                text="Administrator RFID",
                font=(
                    "Arial",
                    20,
                    "bold"
                )
            ).pack(
                pady=20
            )

            tk.Label(
                window,
                text="Simulated RFID UID"
            ).pack(
                pady=(10, 0)
            )

            uid_entry = tk.Entry(
                window,
                width=30
            )

            uid_entry.pack(
                pady=10
            )

            uid_entry.focus_set()

            def verify():

                uid = (
                    uid_entry
                    .get()
                    .strip()
                    .upper()
                )

                if not uid:

                    show_messagebox(self.root, 'showwarning', 
                        "RFID",
                        "Enter an RFID UID."
                    )

                    return

                uid = (
                    self.rfid_reader
                    .simulate_scan(
                        uid
                    )
                )

                user = (
                    self.database
                    .get_user_by_rfid(
                        uid
                    )
                )

                if user is None:

                    show_messagebox(self.root, 'showerror', 
                        "Access Denied",
                        "RFID credential not recognized."
                    )

                    self.database.log_access(
                        None,
                        "ADMIN_RFID",
                        "DENIED"
                    )

                    return

                if not self.database.is_admin(
                    user["id"]
                ):

                    show_messagebox(self.root, 'showerror', 
                        "Access Denied",
                        (
                            f"{user['name']} is not "
                            "authorized for administration."
                        )
                    )

                    self.database.log_access(
                        user["id"],
                        "ADMIN_RFID",
                        "DENIED"
                    )

                    return

                self.database.log_access(
                    user["id"],
                    "ADMIN_RFID",
                    "GRANTED"
                )

                window.destroy()

                self.grant_admin_access(
                    user
                )

            tk.Button(
                window,
                text="VERIFY",
                width=18,
                command=verify
            ).pack(
                pady=20
            )

            return

        
        # Read from the real PN532
        

        window = create_popup(
            self.root,
            bg=BG
        )

        window.title(
            "Administrator RFID Verification"
        )

        window.geometry(
            "450x260"
        )
        window.transient(self.root)
        place_window_on_monitor(window, 450, 260)


        window.resizable(
            False,
            False
        )

        tk.Label(
            window,
            text="Administrator RFID",
            font=(
                "Arial",
                20,
                "bold"
            )
        ).pack(
            pady=20
        )

        status = tk.Label(
            window,
            text=(
                "Present administrator card "
                "to the PN532 reader."
            ),
            font=(
                "Arial",
                12
            )
        )

        status.pack(
            pady=20
        )

        @background_callback(self, window)
        def read_card():

            try:

                result = (
                    (yield lambda: self.rfid_reader
                    .read_verilock_card(
                        timeout=5.0
                    ))
                )

                if result is None:

                    status.config(
                        text="No card detected."
                    )

                    return

                if not result.get(
                    "valid",
                    False
                ):

                    status.config(
                        text="Invalid VeriLock card."
                    )

                    self.database.log_access(
                        None,
                        "ADMIN_RFID",
                        "DENIED"
                    )

                    return

                uid = result.get(
                    "uid"
                )

                card_user_id = result.get(
                    "user_id"
                )

                
                # Look up the scanned card UID
                

                user = (
                    self.database
                    .get_user_by_rfid(
                        uid
                    )
                )

                if user is None:

                    status.config(
                        text=(
                            "Card UID is not assigned "
                            "to an enabled user."
                        )
                    )

                    self.database.log_access(
                        None,
                        "ADMIN_RFID",
                        "DENIED"
                    )

                    return

                
                # Check the card against the database
                

                if (
                    int(user["id"])
                    !=
                    int(card_user_id)
                ):

                    status.config(
                        text=(
                            "Credential verification failed."
                        )
                    )

                    self.database.log_access(
                        user["id"],
                        "ADMIN_RFID",
                        "DENIED"
                    )

                    return

                
                # Check the administrator’s role
                

                if not self.database.is_admin(
                    user["id"]
                ):

                    status.config(
                        text=(
                            "Identity verified, but "
                            "administrator access denied."
                        )
                    )

                    self.database.log_access(
                        user["id"],
                        "ADMIN_RFID",
                        "DENIED"
                    )

                    return

                
                # Access granted
                

                self.database.log_access(
                    user["id"],
                    "ADMIN_RFID",
                    "GRANTED"
                )

                window.destroy()

                self.grant_admin_access(
                    user
                )

            except Exception as error:

                status.config(
                    text=(
                        f"RFID error: {error}"
                    )
                )

        tk.Button(
            window,
            text="READ CARD",
            width=18,
            command=read_card
        ).pack(
            pady=10
        )

    
    # Grant administrator access
    

    def grant_admin_access(
        self,
        user
    ):

        self._admin_session_generation = getattr(self, '_admin_session_generation', 0) + 1
        self.authenticated_admin = {
            "id": user["id"],
            "name": user["name"],
            "role": user["role"]
        }

        if os.environ.get("VERILOCK_ADMIN_ACTION") == "LOCK":
            self.lock_after_verification()
            return

        print()
        print(
            "================================"
        )
        print(
            "      ADMIN ACCESS GRANTED"
        )
        print(
            "================================"
        )
        print(
            f"Administrator: "
            f"{user['name']}"
        )
        print(
            "================================"
        )

        try:

            if hasattr(
                self,
                "admin_face_window"
            ):

                self.admin_face_window.destroy()

        except Exception:

            pass

        self.build_gui()

        self.refresh_users()

    
    # Admin dashboard
    

    def lock_after_verification(self):
        """Lock after the existing administrator face/RFID verification succeeds."""
        if not self.authenticated_admin:
            return
        admin_id = self.authenticated_admin["id"]
        self.admin_face_active = False
        if hasattr(self, "admin_face_window"):
            try:
                self.admin_face_window.destroy()
            except tk.TclError:
                pass
        self.clear_window()
        status = tk.Label(self.root, text="Locking...", bg=BG, fg=TEXT,
                          font=("Arial", 20, "bold"))
        status.pack(expand=True)

        @background_callback(self, self.root)
        def perform_lock():
            try:
                if self.rfid_reader is None or not hasattr(self.rfid_reader, "relock"):
                    raise RuntimeError("Open LOCK from the running VeriLock main screen.")
                success = yield lambda: self.rfid_reader.relock(admin_id)
                if not success:
                    raise RuntimeError("The mechanism is busy or shutting down. Try again shortly.")
                self.database.log_access(admin_id, "ADMIN_LOCK", "LOCKED")
                show_messagebox(self.root, 'showinfo', "Lock Complete", "Lock sequence completed.")
            except Exception as error:
                self.database.log_access(admin_id, "ADMIN_LOCK", "FAILED")
                show_messagebox(self.root, 'showerror', "Lock Failed", str(error))
            finally:
                self.shutdown()

        # Give the verification callback time to finish before starting the lock command.
        self.root.after(50, perform_lock)

    def build_gui(
        self
    ):

        self.clear_window()
        self.root.title("VeriLock Administration")
        self.root.configure(bg=BG)

        title = tk.Label(
            self.root,
            bg=BG,
            fg=TEXT,
            text="VeriLock Administration",
            font=("Arial", 20, "bold")
        )
        title.pack(pady=(8, 2))

        if self.authenticated_admin:
            tk.Label(
                self.root,
                bg=BG,
                fg=TEXT_SECONDARY,
                text=(
                    "Authenticated Administrator: "
                    f"{self.authenticated_admin['name']}"
                ),
                font=("Arial", 10)
            ).pack(pady=(0, 5))

        
        # User list
        

        table_frame = tk.Frame(self.root, bg=BG)
        table_frame.pack(
            fill="both",
            expand=True,
            padx=12,
            pady=(3, 5)
        )

        columns = (
            "id",
            "name",
            "role",
            "rfid",
            "face",
            "enabled"
        )

        self.user_table = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            selectmode="browse",
            height=7
        )

        headings = {
            "id": "ID",
            "name": "Name",
            "role": "Role",
            "rfid": "RFID",
            "face": "Face ID",
            "enabled": "Enabled"
        }

        for column, heading in headings.items():
            self.user_table.heading(column, text=heading)

        self.user_table.column("id", width=45, anchor="center")
        self.user_table.column("name", width=150)
        self.user_table.column("role", width=85, anchor="center")
        self.user_table.column("rfid", width=155, anchor="center")
        self.user_table.column("face", width=85, anchor="center")
        self.user_table.column("enabled", width=75, anchor="center")

        scrollbar = ttk.Scrollbar(
            table_frame,
            orient="vertical",
            command=self.user_table.yview
        )

        self.user_table.configure(
            yscrollcommand=scrollbar.set
        )

        self.user_table.pack(
            side="left",
            fill="both",
            expand=True
        )

        scrollbar.pack(
            side="right",
            fill="y"
        )

        
        # Dashboard buttons
        
        
        # Ten dashboard controls use four columns across three rows
        # on the 800x480 LCD.
        

        button_frame = tk.Frame(self.root, bg=BG)
        button_frame.pack(pady=(3, 8))

        def admin_button(text, command, row, column, danger=False):
            button = tk.Button(
                button_frame,
                text=text,
                width=14,
                height=1,
                font=("Arial", 10, "bold"),
                bg=DANGER if danger else BUTTON_BG,
                fg=TEXT,
                activebackground=(
                    "#C0392B" if danger else BUTTON_ACTIVE
                ),
                activeforeground=TEXT,
                relief="flat",
                bd=0,
                cursor="hand2",
                command=command
            )
            button.grid(
                row=row,
                column=column,
                padx=4,
                pady=4,
                ipadx=2,
                ipady=4
            )

        admin_button(
            "ADD USER",
            self.add_user_window,
            0,
            0
        )

        admin_button(
            "ENROLL FACE",
            self.start_face_enrollment,
            0,
            1
        )

        admin_button(
            "ASSIGN RFID",
            self.assign_rfid_window,
            0,
            2
        )

        admin_button(
            "REFRESH",
            self.refresh_users,
            0,
            3
        )

        admin_button(
            "REMOVE RFID",
            self.remove_rfid,
            1,
            0
        )

        admin_button(
            "REMOVE FACE",
            self.remove_face,
            1,
            1
        )

        admin_button(
            "ENABLE/DISABLE",
            self.toggle_user,
            1,
            2
        )

        admin_button(
            "LOG OUT",
            self.logout,
            1,
            3,
            danger=True
        )

        admin_button(
            "SETTINGS",
            self.open_settings,
            2,
            0
        )

        admin_button(
            "DELETE USER",
            self.delete_user,
            2,
            1,
            danger=True
        )

    def open_settings(self):
        """Show the settings popup after an administrator has signed in."""
        if not self.authenticated_admin:
            return
        window = create_popup(self.root)
        window.title("VeriLock Settings")
        window.configure(bg=BG)
        window.geometry("480x380")
        place_window_on_monitor(window, 480, 380)
        window.transient(self.root)
        window.grab_set()

        status = tk.Label(window, bg=BG, fg=TEXT,
                          font=("Arial", 14, "bold"))
        status.pack(pady=(18, 8))
        tk.Label(window, bg=BG, fg=TEXT_SECONDARY,
                 text="Requires the same user's RFID card and face\n"
                      "within 30 seconds to unlock the door.",
                 font=("Arial", 11)).pack(pady=5)

        def refresh():
            enabled = self.database.ultra_secure_enabled()
            status.config(text="ULTRA SECURE MODE: " + ("ON" if enabled else "OFF"),
                          fg=SUCCESS if enabled else TEXT)
            toggle.config(text="TURN OFF" if enabled else "TURN ON")

        def toggle_mode():
            enabled = self.database.ultra_secure_enabled()
            action = "disable" if enabled else "enable"
            if not show_messagebox(self.root, 'askyesno', 
                    "Ultra Secure Mode",
                    f"{action.title()} RFID + facial recognition for door access?",
                    parent=window):
                return
            self.database.set_ultra_secure_enabled(not enabled)
            refresh()

        toggle = tk.Button(window, command=toggle_mode, width=20,
                           bg=BUTTON_BG, fg=TEXT)
        toggle.pack(pady=10)
        autolock_status = tk.Label(window, bg=BG, fg=TEXT,
                                   font=("Arial", 14, "bold"))
        autolock_status.pack(pady=(12, 4))
        tk.Label(window, bg=BG, fg=TEXT_SECONDARY,
                 text=f"Automatically relock after {config.UNLOCK_DURATION / 60:g} minute(s).",
                 font=("Arial", 11)).pack(pady=3)

        def refresh_autolock():
            enabled = self.database.autolock_enabled()
            autolock_status.config(
                text="AUTOMATIC RELOCKING: " + ("ON" if enabled else "OFF"),
                fg=SUCCESS if enabled else TEXT)
            autolock_toggle.config(text="TURN OFF" if enabled else "TURN ON")

        @background_callback(self, window)
        def toggle_autolock():
            enabled = not self.database.autolock_enabled()
            admin_id = self.authenticated_admin["id"]
            autolock_toggle.config(state=tk.DISABLED)
            try:
                if self.rfid_reader is None or not hasattr(self.rfid_reader, "set_autolock"):
                    raise RuntimeError("Open Admin from the running VeriLock application to change automatic relocking.")
                yield lambda: self.rfid_reader.set_autolock(admin_id, enabled)
                refresh_autolock()
            except Exception as error:
                show_messagebox(self.root, 'showerror', "Automatic Relocking", str(error), parent=window)
            finally:
                if window.winfo_exists():
                    autolock_toggle.config(state=tk.NORMAL)

        autolock_toggle = tk.Button(window, command=toggle_autolock, width=20,
                                    bg=BUTTON_BG, fg=TEXT)
        autolock_toggle.pack(pady=6)
        refresh_autolock()
        tk.Button(window, text="CLOSE", command=window.destroy,
                  bg=BUTTON_BG, fg=TEXT).pack(pady=4)
        refresh()

    
    # Refresh the user list
    

    def refresh_users(
        self
    ):

        for item in (
            self.user_table.get_children()
        ):

            self.user_table.delete(
                item
            )

        users = (
            self.database
            .list_users()
        )

        for user in users:

            enabled = (
                "YES"
                if user["enabled"]
                else "NO"
            )

            rfid = (
                user["rfid_uid"]
                if user["rfid_uid"]
                else "-"
            )

            face = (
                user["face_id"]
                if user["face_id"]
                else "-"
            )

            self.user_table.insert(
                "",
                "end",
                values=(
                    user["id"],
                    user["name"],
                    user["role"],
                    rfid,
                    face,
                    enabled
                )
            )

    
    # Find the selected user
    

    def get_selected_user(
        self
    ):

        selection = (
            self.user_table.selection()
        )

        if not selection:

            show_messagebox(self.root, 'showwarning', 
                "No User Selected",
                "Select a user first."
            )

            return None

        item = (
            self.user_table.item(
                selection[0]
            )
        )

        values = item["values"]

        if not values:

            return None

        user_id = int(
            values[0]
        )

        return (
            self.database
            .get_any_user_by_id(
                user_id
            )
        )

    
    # Add a user
    

    def add_user_window(
        self
    ):

        window = create_popup(
            self.root,
            bg=BG
        )

        window.title(
            "Add VeriLock User"
        )

        window.geometry(
            "400x300"
        )
        place_window_on_monitor(window, 400, 300)

        window.resizable(
            False,
            False
        )

        tk.Label(
            window,
            text="Add User",
            font=(
                "Arial",
                20,
                "bold"
            )
        ).pack(
            pady=20
        )

        tk.Label(
            window,
            text="Name"
        ).pack()

        name_entry = tk.Entry(
            window,
            width=30
        )

        name_entry.pack(
            pady=5
        )

        tk.Label(
            window,
            text="Role"
        ).pack(
            pady=(10, 0)
        )

        role_variable = tk.StringVar(
            value="USER"
        )

        role_box = ttk.Combobox(
            window,
            textvariable=role_variable,
            values=(
                "USER",
                "ADMIN"
            ),
            state="readonly",
            width=27
        )

        role_box.pack(
            pady=5
        )

        def create_user():

            name = (
                name_entry
                .get()
                .strip()
            )

            role = (
                role_variable
                .get()
                .strip()
                .upper()
            )

            if not name:

                show_messagebox(self.root, 'showwarning', 
                    "Invalid Name",
                    "Enter a user name."
                )

                return

            user_id = (
                self.database
                .add_user(
                    name=name,
                    role=role
                )
            )

            if user_id is None:

                show_messagebox(self.root, 'showerror', 
                    "User Error",
                    "Could not create user."
                )

                return

            window.destroy()

            self.refresh_users()

            show_messagebox(self.root, 'showinfo', 
                "User Created",
                (
                    f"{name} was created.\n\n"
                    f"Database User ID: {user_id}"
                )
            )

        tk.Button(
            window,
            text="CREATE USER",
            width=18,
            command=create_user
        ).pack(
            pady=25
        )

    
    # Assign an RFID card
    

    def assign_rfid_window(
        self
    ):

        if not self.authenticated_admin:
            return
        window_session = getattr(self, '_admin_session_generation', 0)
        window_admin_id = self.authenticated_admin["id"]

        user = self.get_selected_user()

        if user is None:

            return

        user_id = user["id"]
        user_name = user["name"]

        window = create_popup(
            self.root,
            bg=BG
        )

        window.title(
            "Assign RFID"
        )

        window.geometry(
            "440x320"
        )
        place_window_on_monitor(window, 440, 320)

        window.resizable(
            False,
            False
        )

        tk.Label(
            window,
            text="Assign RFID Card",
            font=(
                "Arial",
                20,
                "bold"
            )
        ).pack(
            pady=20
        )

        tk.Label(
            window,
            text=f"User: {user_name}",
            font=(
                "Arial",
                12
            )
        ).pack(
            pady=5
        )

        
        # Simulation
        

        if config.SIMULATION_MODE:

            tk.Label(
                window,
                text="RFID UID"
            ).pack(
                pady=(15, 0)
            )

            uid_entry = tk.Entry(
                window,
                width=30
            )

            uid_entry.pack(
                pady=5
            )

            def assign():

                if (window_session != getattr(self, '_admin_session_generation', 0)
                        or not self.authenticated_admin
                        or self.authenticated_admin["id"] != window_admin_id
                        or not self.database.is_admin(window_admin_id)):
                    show_messagebox(self.root, 'showwarning', "Session Ended", "Verify an administrator and reopen RFID assignment.")
                    return

                uid = (
                    uid_entry
                    .get()
                    .strip()
                    .upper()
                )

                if not uid:

                    show_messagebox(self.root, 'showwarning', 
                        "Invalid RFID",
                        "Enter an RFID UID."
                    )

                    return

                uid = (
                    self.rfid_reader
                    .simulate_scan(
                        uid
                    )
                )

                success = (
                    self.database
                    .assign_rfid(
                        user_id,
                        uid
                    )
                )

                if success:

                    window.destroy()

                    self.refresh_users()

                    show_messagebox(self.root, 'showinfo', 
                        "RFID Assigned",
                        (
                            f"RFID {uid} assigned "
                            f"to {user_name}."
                        )
                    )

            tk.Button(
                window,
                text="ASSIGN RFID",
                width=18,
                command=assign
            ).pack(
                pady=20
            )

            return

        
        # Enroll with the real PN532
        

        status = tk.Label(
            window,
            text=(
                "Present a compatible MIFARE Classic "
                "card to the PN532."
            ),
            wraplength=380,
            font=(
                "Arial",
                11
            )
        )

        status.pack(
            pady=20
        )

        @background_callback(self, window)
        def program_card():

            if (not self.authenticated_admin
                    or window_session != getattr(self, '_admin_session_generation', 0)
                    or self.authenticated_admin["id"] != window_admin_id):
                status.config(text="Administrator verification is required. Reopen RFID assignment.")
                return
            admin_id = self.authenticated_admin["id"]
            session = getattr(self, '_admin_session_generation', 0)

            status.config(
                text="Waiting for card..."
            )

            self.root.update_idletasks()

            try:

                card = (yield lambda: self.rfid_reader.read_verilock_card(timeout=10.0))
                if card is None or not card.get("uid"):
                    status.config(text="No card detected. Try again.")
                    return

                checked_uid = card["uid"]
                owner = self.database.get_any_user_by_rfid(checked_uid)
                if owner is not None and owner["id"] != user_id:
                    status.config(
                        text="This card is already assigned to another user. "
                             "No card data was changed."
                    )
                    return

                def write_and_assign():
                    # Finish the database update even if the dialog closes
                    # while the verified card write is in progress.
                    # Check once more immediately before starting the physical write.
                    if (getattr(self, '_closing', False)
                            or session != getattr(self, '_admin_session_generation', 0)
                            or not self.authenticated_admin
                            or self.authenticated_admin["id"] != admin_id
                            or not self.database.is_admin(admin_id)):
                        raise RuntimeError("Administrator session ended or authorization was revoked. Card was not written.")
                    uid = self.rfid_reader.write_verilock_card(
                        user_id, timeout=10.0, expected_uid=checked_uid,
                    )
                    success = uid is not None and self.database.assign_rfid(user_id, uid)
                    return uid, success

                uid, success = yield write_and_assign
                if uid is None:
                    status.config(text="Card programming failed or timed out.")
                    return

                if not success:

                    status.config(
                        text=(
                            "Card was programmed, but "
                            "database assignment failed."
                        )
                    )

                    return

                self.refresh_users()

                window.destroy()

                show_messagebox(self.root, 'showinfo', 
                    "RFID Enrollment Complete",
                    (
                        f"RFID card enrolled for "
                        f"{user_name}.\n\n"
                        f"UID: {uid}"
                    )
                )

            except Exception as error:

                status.config(
                    text=f"RFID error: {error}"
                )

        tk.Button(
            window,
            text="PROGRAM CARD",
            width=18,
            command=program_card
        ).pack(
            pady=10
        )

    
    # Remove an RFID card
    

    def remove_rfid(
        self
    ):

        user = self.get_selected_user()

        if user is None:

            return

        if not user["rfid_uid"]:

            show_messagebox(self.root, 'showinfo', 
                "No RFID",
                "This user does not have an RFID card."
            )

            return

        confirm = show_messagebox(self.root, 'askyesno', 
            "Remove RFID",
            (
                f"Remove the RFID credential "
                f"from {user['name']}?"
            )
        )

        if not confirm:

            return

        if self.database.remove_rfid(
            user["id"]
        ):

            self.refresh_users()

    
    # Enroll a face
    

    def start_face_enrollment(
        self
    ):

        if getattr(self, '_enrollment_finishing', False):
            show_messagebox(self.root, 'showinfo', "Enrollment Cleanup", "Previous enrollment is finishing. Try again shortly.")
            return

        user = self.get_selected_user()

        if user is None:

            return

        if user["face_id"]:

            confirm = show_messagebox(self.root, 'askyesno', 
                "Replace Face",
                (
                    f"{user['name']} already has "
                    f"Face ID {user['face_id']}.\n\n"
                    "Replace the existing enrollment?"
                )
            )

            if not confirm:

                return

        try:

            self.initialize_face_system()

        except Exception as error:

            show_messagebox(self.root, 'showerror', 
                "Camera Error",
                str(error)
            )

            return

        face_id = user["id"]

        self.enrollment_user_id = user["id"]
        self.enrollment_face_id = face_id
        self.enrollment_sample = 0
        self.enrollment_frame_counter = 0
        self.enrollment_capture_started = False
        self.enrollment_active = True
        self.enrollment_generation += 1
        self.enrollment_overlay = None
        self.next_enrollment_preview = 0.0
        self.next_enrollment_analysis = 0.0
        self.last_enrollment_frame_sequence = None

        # Keep the existing enrollment active until replacement succeeds.
        self.face_recognition.cancel_enrollment(face_id)
        self.enrollment_worker = EnrollmentWorker(self.face_recognition)

        self.face_window = create_popup(
            self.root,
            bg=BG
        )

        self.face_window.title(
            "Face Enrollment"
        )

        self.face_window.geometry(
            f"{config.DISPLAY_WIDTH}x{config.DISPLAY_HEIGHT}"
        )
        place_window_on_monitor(
            self.face_window,
            config.DISPLAY_WIDTH,
            config.DISPLAY_HEIGHT,
        )

        self.face_window.protocol(
            "WM_DELETE_WINDOW",
            self.cancel_face_enrollment
        )

        # Leave room at the bottom for the enrollment buttons.
        controls = tk.Frame(self.face_window, bg=BG)
        controls.pack(side="bottom", fill="x", pady=(4, 8))

        tk.Label(
            self.face_window,
            bg=BG,
            fg=TEXT,
            text="Face Enrollment",
            font=(
                "Arial",
                18,
                "bold"
            )
        ).pack(
            pady=4
        )

        tk.Label(
            self.face_window,
            bg=BG,
            fg=TEXT,
            text=f"User: {user['name']}",
            font=(
                "Arial",
                13
            )
        ).pack()

        self.face_enrollment_camera = (
            tk.Label(
                self.face_window
            )
        )

        self.face_enrollment_camera.pack(
            pady=4
        )

        self.face_enrollment_status = (
            tk.Label(
                self.face_window,
                bg=BG,
                fg=TEXT,
                text="Position your full face, then press START CAPTURE.",
                wraplength=max(240, config.DISPLAY_WIDTH - 40),
                font=(
                    "Arial",
                    12,
                    "bold"
                )
            )
        )

        self.face_enrollment_status.pack(
            pady=5
        )

        self.face_enrollment_progress = (
            tk.Label(
                self.face_window,
                bg=BG,
                fg=TEXT,
                text=(
                    f"Samples: 0/"
                    f"{config.FACE_ENROLLMENT_SAMPLES}"
                ),
                font=(
                    "Arial",
                    12
                )
            )
        )

        self.face_enrollment_progress.pack(
            pady=5
        )


        self.face_start_button = tk.Button(
            controls,
            bg=BUTTON_BG,
            fg=TEXT,
            activebackground=BUTTON_ACTIVE,
            activeforeground=TEXT,
            highlightbackground=PRIMARY,
            highlightcolor=PRIMARY,
            relief="flat",
            bd=1,
            text="START CAPTURE",
            width=18,
            command=self.begin_face_capture
        )
        self.face_start_button.pack(side="left", expand=True, padx=8, ipady=6)

        tk.Button(
            controls,
            bg=BUTTON_BG,
            fg=TEXT,
            activebackground=BUTTON_ACTIVE,
            activeforeground=TEXT,
            highlightbackground=PRIMARY,
            highlightcolor=PRIMARY,
            relief="flat",
            bd=1,
            text="CANCEL",
            width=15,
            command=self.cancel_face_enrollment
        ).pack(side="left", expand=True, padx=8, ipady=6)

        self.face_window.after(
            30,
            self.update_face_enrollment
        )

    def begin_face_capture(self):
        if not self.enrollment_active or self.enrollment_capture_started:
            return
        self.enrollment_capture_started = True
        self.enrollment_generation += 1
        self.next_enrollment_analysis = 0.0
        self.last_enrollment_frame_sequence = None
        self.enrollment_sample = 0
        self.enrollment_frame_counter = 0
        self.face_start_button.config(state=tk.DISABLED)
        self.face_enrollment_status.config(text="Capturing: keep your whole face in view.")

    
    # Update face enrollment
    

    def update_face_enrollment(
        self
    ):
        if not self.enrollment_active or not self.face_window.winfo_exists():
            return
        try:
            # The background worker reads the camera and checks faces.
            # This part only shows the latest image and updates the controls.
            if self.enrollment_worker is not None:
                self.consume_enrollment_results()
                if not self.enrollment_active:
                    return
            now = time.monotonic()
            if now >= self.next_enrollment_preview or now >= self.next_enrollment_analysis:
                if isinstance(self.camera, Camera):
                    frame, sequence = self.camera.get_frame_with_sequence()
                else:
                    frame, sequence = self.camera.get_frame(), None
                if frame is None:
                    self.face_enrollment_status.config(text="Camera frame unavailable.")
                else:
                    if (self.enrollment_worker is not None and
                            now >= self.next_enrollment_analysis and
                            (sequence is None or
                             sequence != self.last_enrollment_frame_sequence)):
                        self.next_enrollment_analysis = now + config.FACE_ANALYSIS_INTERVAL
                        self.last_enrollment_frame_sequence = sequence
                        self.enrollment_worker.submit(self.enrollment_generation, frame)
                    if now >= self.next_enrollment_preview:
                        self.next_enrollment_preview = now + config.CAMERA_PREVIEW_INTERVAL
                        self.display_enrollment_preview(frame)
        except Exception as error:
            print(f"[ADMIN] Face enrollment error: {error}")
            self.face_enrollment_status.config(text=f"Enrollment error: {error}")
        if self.enrollment_active:
            self.face_window.after(30, self.update_face_enrollment)

    def display_enrollment_preview(self, frame):
        view = frame.copy()
        if (self.enrollment_overlay is not None and
                time.monotonic() - self.enrollment_overlay[0] < 0.5):
            face = self.enrollment_overlay[1]
            x, y, w, h = self.face_recognition.face_box(face, view.shape)
            cv2.rectangle(view, (x, y), (x + w, y + h),
                          (255, 255, 255), 2)
        zx, zy, zw, zh = self.face_recognition.detection_zone(view.shape)
        cv2.rectangle(view, (zx, zy), (zx + zw, zy + zh), (255, 180, 0), 1)
        rgb = cv2.resize(cv2.cvtColor(view, cv2.COLOR_BGR2RGB), (320, 240))
        self.camera_image = ImageTk.PhotoImage(Image.fromarray(rgb))
        self.face_enrollment_camera.config(image=self.camera_image)

    def consume_enrollment_results(self):
        try:
            (generation, faces, issue, in_frame, embedding,
             brightness, error) = self.enrollment_worker.results.get_nowait()
        except queue.Empty:
            return
        # Ignore frames captured before START CAPTURE or a new enrollment.
        if generation != self.enrollment_generation or not self.enrollment_active:
            return
        if error is not None:
            self.face_enrollment_status.config(text=f"Enrollment error: {error}")
            self.enrollment_frame_counter = 0
            return
        self.enrollment_overlay = (time.monotonic(), faces[0]) if len(faces) == 1 else None
        if len(faces) == 1 and issue is not None:
            message = ("Add light near the camera." if issue == "ADD LIGHT"
                       else "Reduce glare on your face.")
        elif len(faces) == 1 and not in_frame:
            message = "Fit your whole face in the guide; adjust distance."
        elif not faces:
            dark, glare = brightness
            message = ("Add light near the camera." if dark else
                       "Reduce glare on the camera." if glare else
                       "No face detected.")
        elif len(faces) > 1:
            message = "Only one person should be visible."
        elif embedding is None:
            message = "Could not analyze face; try again."
        else:
            message = ("Face detected - capturing samples." if self.enrollment_capture_started
                       else "Face in view. Press START CAPTURE.")
            if self.enrollment_capture_started:
                self.enrollment_frame_counter += 1
                if self.enrollment_frame_counter >= 3:
                    self.enrollment_frame_counter = 0
                    self.enrollment_sample += 1
                    self.face_recognition.save_face_sample(
                        self.enrollment_face_id, self.enrollment_sample, embedding)
                    self.face_enrollment_progress.config(
                        text=f"Samples: {self.enrollment_sample}/{config.FACE_ENROLLMENT_SAMPLES}")
                    if self.enrollment_sample >= config.FACE_ENROLLMENT_SAMPLES:
                        self.complete_face_enrollment()
                        return
        if message != "Face detected - capturing samples.":
            self.enrollment_frame_counter = 0
        self.face_enrollment_status.config(text=message)

    
    # Finish face enrollment
    

    def wait_for_enrollment_stop(self, finished):
        worker = self.enrollment_worker
        if worker is not None:
            worker.stopping.set()

        def poll():
            if getattr(self, '_closing', False):
                return
            if worker is not None and not worker.close(timeout=0):
                self.root.after(100, poll)
                return
            if self.enrollment_worker is worker:
                self.enrollment_worker = None
            finished()

        self.root.after(0, poll)

    def complete_face_enrollment(self):
        self.enrollment_active = False
        self.enrollment_capture_started = False
        self._enrollment_finishing = True
        generation = self.enrollment_generation
        session = getattr(self, '_admin_session_generation', 0)
        self.face_enrollment_status.config(text="Finishing face analysis before saving...")

        def save_when_stopped():
            if generation != self.enrollment_generation:
                return  # Ignore the completion callback from an earlier operation.
            self._enrollment_finishing = False
            if (session != getattr(self, '_admin_session_generation', 0)
                    or not self.authenticated_admin):
                return
            self._save_completed_enrollment()

        self.wait_for_enrollment_stop(save_when_stopped)

    def _save_completed_enrollment(self):
        self.face_enrollment_status.config(
            text="Saving face enrollment..."
        )

        self.root.update_idletasks()

        old_user = self.database.get_any_user_by_id(self.enrollment_user_id)
        old_face_id = old_user["face_id"] if old_user else None
        success = (
            self.database
            .assign_face(
                self.enrollment_user_id,
                self.enrollment_face_id
            )
        )

        if not success:
            self.face_recognition.cancel_enrollment(self.enrollment_face_id)

            show_messagebox(self.root, 'showerror', 
                "Database Error",
                (
                    "Could not assign the Face ID "
                    "to the user."
                )
            )

            return

        try:
            success = self.face_recognition.train_model()
            if not success:
                raise ValueError("No face samples were captured")
        except (OSError, ValueError) as error:
            # Restore the old face assignment if saving the new face data fails.
            if old_face_id is None:
                self.database.remove_face(self.enrollment_user_id)
            else:
                self.database.assign_face(self.enrollment_user_id, old_face_id)
            self.face_recognition.cancel_enrollment(self.enrollment_face_id)
            show_messagebox(self.root, 'showerror', "Enrollment Error", str(error))
            return

        self.refresh_users()

        user = (
            self.database
            .get_any_user_by_id(
                self.enrollment_user_id
            )
        )

        user_name = (
            user["name"]
            if user
            else
            str(self.enrollment_user_id)
        )

        show_messagebox(self.root, 'showinfo', 
            "Enrollment Complete",
            (
                f"Face enrollment completed "
                f"for {user_name}.\n\n"
                f"Face ID: "
                f"{self.enrollment_face_id}\n"
                f"Samples: "
                f"{self.enrollment_sample}"
            )
        )

        try:

            self.face_window.destroy()

        except Exception:

            pass

    
    # Cancel face enrollment
    

    def cancel_face_enrollment(self):
        self.enrollment_active = False
        self.enrollment_capture_started = False
        self.enrollment_generation += 1
        self._enrollment_finishing = True
        session = getattr(self, '_admin_session_generation', 0)
        face_id = self.enrollment_face_id
        try:
            self.face_window.destroy()
        except tk.TclError:
            pass

        def discard_when_stopped():
            try:
                if (session == getattr(self, '_admin_session_generation', 0)
                        and self.face_recognition is not None):
                    self.face_recognition.cancel_enrollment(face_id)
            finally:
                self._enrollment_finishing = False

        self.wait_for_enrollment_stop(discard_when_stopped)

    def stop_enrollment_worker(self):
        if self.enrollment_worker is not None:
            if not self.enrollment_worker.close(timeout=0.1):
                raise RuntimeError("Face enrollment is still stopping; try again shortly.")
            self.enrollment_worker = None

    
    # Remove face data
    

    def remove_face(
        self
    ):

        user = self.get_selected_user()

        if user is None:

            return

        face_id = user["face_id"]

        if not face_id:

            show_messagebox(self.root, 'showinfo', 
                "No Face",
                (
                    "This user does not have "
                    "a face credential."
                )
            )

            return

        confirm = show_messagebox(self.root, 'askyesno', 
            "Remove Face",
            (
                f"Remove the face credential "
                f"from {user['name']}?"
            )
        )

        if not confirm:

            return

        if not self.database.remove_face(user["id"]):
            return
        if self.face_recognition is None:
            self.face_recognition = FaceRecognition()
        self.face_recognition.remove_enrollment(face_id)

        # Remove any legacy grayscale LBPH samples for this user as well.
        legacy_directory = Path(config.FACE_EMBEDDINGS_PATH) / f"user_{int(face_id)}"
        if legacy_directory.exists():
            shutil.rmtree(legacy_directory)

        self.refresh_users()

    
    # Delete a user
    

    def delete_user(
        self
    ):

        user = self.get_selected_user()

        if user is None:
            return

        user_id = user["id"]
        user_name = user["name"]
        face_id = user["face_id"]

        # Do not let the signed-in administrator delete their own account.
        if (
            self.authenticated_admin
            and user_id == self.authenticated_admin["id"]
        ):
            show_messagebox(self.root, 'showwarning', 
                "Action Blocked",
                (
                    "You cannot delete the administrator "
                    "account currently being used for this "
                    "session."
                )
            )
            return

        confirm = show_messagebox(self.root, 'askyesno', 
            "Delete User",
            (
                f"Permanently delete {user_name}?\n\n"
                "This will remove the user and their "
                "assigned credentials.\n\n"
                "This action cannot be undone."
            )
        )

        if not confirm:
            return

        # Delete the saved face data before removing the user. Removing the user
        # also removes their card assignment, but does not erase the physical card.
        if face_id:
            try:
                if self.face_recognition is None:
                    self.face_recognition = FaceRecognition()
                self.face_recognition.remove_enrollment(face_id)
            except Exception as error:
                print(
                    "[ADMIN] Warning removing face "
                    f"enrollment: {error}"
                )

            try:
                legacy_directory = (
                    Path(config.FACE_EMBEDDINGS_PATH)
                    / f"user_{int(face_id)}"
                )
                if legacy_directory.exists():
                    shutil.rmtree(legacy_directory)
            except Exception as error:
                print(
                    "[ADMIN] Warning removing legacy "
                    f"face data: {error}"
                )

        if not self.database.delete_user(user_id):
            show_messagebox(self.root, 'showerror', 
                "Delete User",
                "The user could not be deleted."
            )
            return

        print(
            f"[ADMIN] User deleted: "
            f"{user_name} "
            f"(ID {user_id})."
        )

        self.refresh_users()

        show_messagebox(self.root, 'showinfo', 
            "User Deleted",
            f"{user_name} was permanently deleted from VeriLock."
        )

    
    # Enable or disable a user
    

    def toggle_user(
        self
    ):

        user = self.get_selected_user()

        if user is None:

            return

        # Prevent the currently authenticated administrator
        # from accidentally disabling their own account.

        if (
            self.authenticated_admin
            and
            user["id"]
            ==
            self.authenticated_admin["id"]
            and
            user["enabled"]
        ):

            show_messagebox(self.root, 'showwarning', 
                "Action Blocked",
                (
                    "You cannot disable the account "
                    "currently being used for this "
                    "administrative session."
                )
            )

            return

        if user["enabled"]:

            confirm = show_messagebox(self.root, 'askyesno', 
                "Disable User",
                (
                    f"Disable access for "
                    f"{user['name']}?"
                )
            )

            if not confirm:

                return

            self.database.disable_user(
                user["id"]
            )

        else:

            self.database.enable_user(
                user["id"]
            )

        self.refresh_users()

    
    # Log out
    

    def logout(self):
        # Remove admin access and dashboard controls right away.
        self._admin_session_generation = getattr(self, '_admin_session_generation', 0) + 1
        self.authenticated_admin = None
        self.enrollment_active = False
        self.enrollment_capture_started = False
        self.admin_face_active = False
        if self.enrollment_worker is not None:
            self.enrollment_worker.stopping.set()
        self.clear_window()
        tk.Label(self.root, text="Logged out. Finishing face cleanup...",
                 bg=BG, fg=TEXT, font=("Arial", 16)).pack(expand=True)

        def finish_logout():
            if getattr(self, '_closing', False):
                return
            if self.enrollment_worker is not None:
                if not self.enrollment_worker.close(timeout=0):
                    self.root.after(100, finish_logout)
                    return
                self.enrollment_worker = None
            try:
                if self.face_recognition is not None:
                    self.face_recognition.cancel_enrollment(self.enrollment_face_id)
            except Exception as error:
                print(f"[ADMIN] Logout cleanup warning: {error}")
            self.build_access_screen()

        self.root.after(0, finish_logout)

    def shutdown(
        self
    ):

        print()
        print(
            "[ADMIN] Shutting down..."
        )

        self._closing = True
        self._admin_session_generation = getattr(self, '_admin_session_generation', 0) + 1
        self.authenticated_admin = None
        if self.enrollment_worker is not None:
            self.enrollment_worker.stopping.set()

        
        # Stop face capture and enrollment
        

        self.enrollment_active = False
        self.enrollment_capture_started = False

        try:
            self.stop_enrollment_worker()
        except RuntimeError as error:
            print(f"[ADMIN] {error}")

        if self.face_recognition is not None:

            try:
                self.face_recognition.cancel_enrollment(
                    self.enrollment_face_id
                )

            except Exception as error:
                print(
                    f"[ADMIN] Face enrollment cleanup warning: "
                    f"{error}"
                )

        self.admin_face_active = False

        
        # Release the camera
        

        try:

            if self.camera is not None:

                print(
                    "[ADMIN] Releasing camera..."
                )

                self.camera.release()
                self.camera = None

                print(
                    "[ADMIN] Camera released."
                )

        except Exception as error:

            print(
                f"[ADMIN] Camera cleanup error: {error}"
            )

            self.camera = None

        
        # Release the RFID reader
        

        try:

            if self.rfid_reader is not None:

                print(
                    "[ADMIN] Releasing RFID reader..."
                )

                if not getattr(self, '_ui_task_busy', False):
                    self.rfid_reader.cleanup()
                else:
                    print("[ADMIN] RFID operation still running; deferring reader release to process exit.")
                self.rfid_reader = None

                print(
                    "[ADMIN] RFID reader released."
                )

        except Exception as error:

            print(
                f"[ADMIN] RFID cleanup error: {error}"
            )

            self.rfid_reader = None

        
        # Close the admin window
        

        try:

            self.root.destroy()

        except Exception as error:

            print(
                f"[ADMIN] Window cleanup warning: {error}"
            )

        print(
            "[ADMIN] Shutdown complete."
        )



# Run the admin app


def main():

    root = None
    app = None

    try:

        root = tk.Tk(className="VeriLock")

        app = VeriLockAdmin(
            root
        )

        root.mainloop()

    except KeyboardInterrupt:

        print()
        print(
            "[ADMIN] Interrupted by user."
        )

    except Exception as error:

        print()
        print(
            "================================"
        )
        print(
            "        ADMIN SYSTEM ERROR"
        )
        print(
            "================================"
        )
        print()
        print(
            error
        )
        print()

    finally:

        
        # Final camera cleanup
        

        if app is not None:

            try:

                if app.camera is not None:

                    print(
                        "[ADMIN] Final camera cleanup..."
                    )

                    app.camera.release()
                    app.camera = None

            except Exception as error:

                print(
                    f"[ADMIN] Final camera cleanup error: {error}"
                )

            
            # Final RFID cleanup
            

            try:

                if app.rfid_reader is not None:

                    print(
                        "[ADMIN] Final RFID cleanup..."
                    )

                    app.rfid_reader.cleanup()
                    app.rfid_reader = None

            except Exception as error:

                print(
                    f"[ADMIN] Final RFID cleanup error: {error}"
                )


if __name__ == "__main__":

    main()
