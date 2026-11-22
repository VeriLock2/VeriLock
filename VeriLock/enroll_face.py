"""Command-line tool for saving a user's face embeddings."""

import cv2
import tkinter as tk
from PIL import Image, ImageTk

import config

from camera import Camera
from database import Database
from face_authentication import FaceRecognition

class EnrollmentPreview:
    """Tkinter preview compatible with the headless OpenCV distribution."""

    def __init__(self):
        self.root = tk.Tk(className="VeriLock")
        self.root.title("VeriLock - Face Enrollment")
        self.label = tk.Label(self.root)
        self.label.pack()
        self.cancelled = False
        self.waiting = tk.BooleanVar(master=self.root, value=False)
        self.root.protocol("WM_DELETE_WINDOW", self.cancel)
        self.root.bind("<KeyPress-q>", self.cancel)
        self.root.bind("<KeyPress-Q>", self.cancel)
        self.image = None

    def cancel(self, event=None):
        self.cancelled = True
        self.waiting.set(True)

    def show(self, frame, delay=150):
        if self.cancelled:
            return False
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        self.image = ImageTk.PhotoImage(Image.fromarray(rgb), master=self.root)
        self.label.configure(image=self.image)
        self.waiting.set(False)
        timer = self.root.after(delay, lambda: self.waiting.set(True))
        try:
            # Tk can still handle Q, window-close, and redraw events.
            self.root.wait_variable(self.waiting)
        finally:
            self.root.after_cancel(timer)
        return not self.cancelled

    def close(self):
        self.root.destroy()


def main():

    print()
    print("================================")
    print("      VeriLock Enrollment")
    print("================================")
    print()

    camera = None
    preview = None

    try:


        print("[SYSTEM] Initializing database...")
        database = Database()

        print("[SYSTEM] Initializing camera...")
        camera = Camera()

        print("[SYSTEM] Initializing face authentication...")
        face_system = FaceRecognition()

        print()
        print("[SYSTEM] Enrollment system ready.")
        print()


        name = input(
            "Enter user name: "
        ).strip()

        if not name:

            print("[ERROR] Name cannot be empty.")
            return

        role = input(
            "Enter role [USER]: "
        ).strip().upper()

        if not role:
            role = "USER"


        success = database.add_user(
            name=name,
            role=role
        )

        if not success:

            print(
                "[ERROR] Could not create user."
            )

            return


        with database.connect() as connection:

            cursor = connection.cursor()

            cursor.execute("""
                SELECT *
                FROM users
                WHERE name = ?
                ORDER BY id DESC
                LIMIT 1
            """, (name,))

            user = cursor.fetchone()

        if user is None:

            print(
                "[ERROR] Could not find new user."
            )

            return

        user_id = user["id"]

        print()
        print("--------------------------------")
        print("User created")
        print("--------------------------------")
        print(f"Name: {name}")
        print(f"Role: {role}")
        print(f"User ID: {user_id}")
        print("--------------------------------")
        print()

        print("FACE ENROLLMENT")
        print()
        print("Look directly at the camera.")
        print("Move your head slightly between samples.")
        print("Only one person should be visible.")
        print()
        print("Press Q to cancel.")
        print()


        preview = EnrollmentPreview()
        sample_count = 0

        while (
            sample_count
            < config.FACE_ENROLLMENT_SAMPLES
        ):

            frame = camera.get_frame()

            if frame is None:

                print(
                    "[ERROR] Could not capture camera frame."
                )

                break

            faces = face_system.detect_faces(
                frame
            )


            # Save a sample only when one face is visible.


            if (len(faces) == 1
                    and face_system.face_lighting_issue(frame, faces[0]) is None
                    and face_system.face_fully_in_frame(frame, faces[0])):

                face_coordinates = faces[0]

                face_image = (
                    face_system.preprocess_face(
                        frame,
                        face_coordinates
                    )
                )

                sample_count += 1

                face_system.save_face_sample(
                    user_id,
                    sample_count,
                    face_image
                )

                x, y, w, h = face_system.face_box(face_coordinates, frame.shape)

                cv2.rectangle(
                    frame,
                    (x, y),
                    (x + w, y + h),
                    (0, 255, 0),
                    2
                )


            # Reject frames with multiple faces.


            elif len(faces) <= 1 and (face_system.needs_more_light(frame) or
                                      (len(faces) == 1 and face_system.face_lighting_issue(frame, faces[0]) == "ADD LIGHT")):

                cv2.putText(
                    frame,
                    "Add light near the camera",
                    (10, 70),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7,
                    (0, 0, 255),
                    2
                )

            elif len(faces) <= 1 and (face_system.needs_less_glare(frame) or
                                      (len(faces) == 1 and face_system.face_lighting_issue(frame, faces[0]) == "REDUCE GLARE")):

                cv2.putText(
                    frame,
                    "Reduce glare on your face",
                    (10, 70),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7,
                    (0, 0, 255),
                    2
                )

            elif len(faces) == 1:

                cv2.putText(
                    frame,
                    "Fit whole face in guide; adjust distance",
                    (10, 70),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7,
                    (0, 0, 255),
                    2
                )

            elif len(faces) > 1:

                cv2.putText(
                    frame,
                    "Only one person allowed",
                    (10, 70),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7,
                    (0, 0, 255),
                    2
                )


            cv2.putText(
                frame,
                (
                    f"Samples: "
                    f"{sample_count}/"
                    f"{config.FACE_ENROLLMENT_SAMPLES}"
                ),
                (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (0, 255, 0),
                2
            )

            zx, zy, zw, zh = face_system.detection_zone(frame.shape)
            cv2.rectangle(frame, (zx, zy), (zx + zw, zy + zh), (255, 180, 0), 1)

            # Pause between samples while keeping Tk's event loop active.
            if not preview.show(frame, delay=150):

                print()
                print(
                    "[FACE] Enrollment cancelled."
                )

                break


        if (
            sample_count
            >= config.FACE_ENROLLMENT_SAMPLES
            and not preview.cancelled
        ):

            print()
            print(
                "[FACE] Face samples captured."
            )

            print(
                "[FACE] Saving face embeddings..."
            )

            success = face_system.train_model()
            if success:
                success = database.assign_face(user_id, user_id)
                if not success:
                    face_system.remove_enrollment(user_id)

            if success:

                print()
                print("==============================")
                print("     ENROLLMENT COMPLETE")
                print("==============================")
                print()
                print(f"User: {name}")
                print(f"User ID: {user_id}")
                print(
                    f"Samples: {sample_count}"
                )

            else:

                print(
                    "[ERROR] Face enrollment failed."
                )

    except KeyboardInterrupt:

        print()
        print(
            "[SYSTEM] Enrollment stopped."
        )

    except Exception as error:

        print()
        print(
            "[ERROR] Enrollment error:"
        )

        print(error)

    finally:

        if camera is not None:
            camera.release()

        if preview is not None:
            preview.close()

        print()
        print(
            "[SYSTEM] Enrollment program closed."
        )

if __name__ == "__main__":
    main()
