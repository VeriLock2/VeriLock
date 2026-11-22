"""Capture camera frames on the Raspberry Pi or a laptop running simulation mode."""

import time
import threading

import cv2
import config


class Camera:


    def __init__(self):
        print("[CAMERA] Initializing camera...")

        self.camera = None
        self.started = False
        self._frame_lock = threading.Lock()
        self._latest_frame = None
        self._latest_at = 0.0
        self.frame_sequence = 0
        self._stop_capture = threading.Event()
        self._capture_thread = None

        if config.SIMULATION_MODE:
            self.initialize_laptop_camera()
        else:
            self.initialize_pi_camera()


    def initialize_laptop_camera(self):
        print("[CAMERA] Initializing laptop camera...")

        self.camera = cv2.VideoCapture(
            config.CAMERA_INDEX,
            cv2.CAP_DSHOW
        )

        if not self.camera.isOpened():
            raise RuntimeError("Could not open laptop camera.")

        self.camera.set(
            cv2.CAP_PROP_FRAME_WIDTH,
            config.CAMERA_WIDTH
        )
        self.camera.set(
            cv2.CAP_PROP_FRAME_HEIGHT,
            config.CAMERA_HEIGHT
        )
        self.camera.set(
            cv2.CAP_PROP_FPS,
            config.CAMERA_FPS
        )
        self.camera.set(
            cv2.CAP_PROP_BUFFERSIZE,
            1
        )

        success, frame = self.camera.read()

        if not success or frame is None:
            self.camera.release()
            self.camera = None
            raise RuntimeError(
                "Laptop camera opened but could not capture a frame."
            )

        self.started = True
        self._store_frame(frame)
        self._start_capture()

        print("[CAMERA] Laptop camera initialized.")
        print(
            f"[CAMERA] Resolution: "
            f"{config.CAMERA_WIDTH}x{config.CAMERA_HEIGHT}"
        )
        print(f"[CAMERA] Target FPS: {config.CAMERA_FPS}")


    def initialize_pi_camera(self):
        print("[CAMERA] Initializing Raspberry Pi camera...")

        try:
            from picamera2 import Picamera2
        except ImportError as error:
            raise RuntimeError(
                "Picamera2 is not installed. Install python3-picamera2 "
                "on the Raspberry Pi."
            ) from error

        try:
            self.camera = Picamera2()

            configuration = self.camera.create_preview_configuration(
                main={
                    "size": (
                        config.CAMERA_WIDTH,
                        config.CAMERA_HEIGHT
                    ),
                    # Picamera2's RGB888 buffer is [B, G, R], which OpenCV already expects.
                    "format": "RGB888"
                },
                buffer_count=2,
                controls={
                    "AeEnable": True,
                    "AwbEnable": True,
                    # In low light, the camera may slow to 15 fps for longer exposures.
                    "FrameDurationLimits": (
                        round(1_000_000 / config.CAMERA_FPS),
                        round(1_000_000 / config.CAMERA_LOW_LIGHT_MIN_FPS),
                    ),
                },
            )

            self.camera.configure(configuration)
            self.camera.start()
            self.started = True

            # Let exposure and white balance settle before face analysis starts.

            time.sleep(
                getattr(config, "CAMERA_WARMUP_TIME", 1.0)
            )

            test_frame = self._read_frame()

            if test_frame is None:
                raise RuntimeError(
                    "Pi camera started but no frame could be captured."
                )

            self._store_frame(test_frame)
            self._start_capture()

            print("[CAMERA] Raspberry Pi camera initialized.")
            print(
                f"[CAMERA] Resolution: "
                f"{config.CAMERA_WIDTH}x{config.CAMERA_HEIGHT}"
            )

        except Exception as error:
            self.release()
            raise RuntimeError(
                f"Could not initialize Raspberry Pi camera: {error}"
            ) from error


    def _store_frame(self, frame):
        with self._frame_lock:
            self._latest_frame = frame
            self._latest_at = time.monotonic()
            self.frame_sequence += 1

    def _start_capture(self):
        self._stop_capture.clear()
        self._capture_thread = threading.Thread(
            target=self._capture_loop, daemon=True, name="verilock-camera")
        self._capture_thread.start()

    def _capture_loop(self):
        while not self._stop_capture.is_set():
            started_at = time.monotonic()
            frame = self._read_frame()
            if frame is not None and not self._stop_capture.is_set():
                self._store_frame(frame)
            self._stop_capture.wait(max(
                0.0, 1.0 / config.CAMERA_FPS - (time.monotonic() - started_at)))

    def get_frame(self):
        """Return the newest frame if it is still fresh."""
        frame, _ = self.get_frame_with_sequence()
        return frame

    def get_frame_with_sequence(self):
        """Return the newest frame and its capture sequence."""
        with self._frame_lock:
            if (not self.started or self._latest_frame is None or
                    time.monotonic() - self._latest_at > 0.6):
                return None, None
            return self._latest_frame.copy(), self.frame_sequence

    def _read_frame(self):
        if self.camera is None or not self.started:
            return None


        if config.SIMULATION_MODE:
            success, frame = self.camera.read()

            if not success or frame is None:
                return None

            return frame


        try:
            frame = self.camera.capture_array()

            if frame is None:
                return None


            return frame

        except Exception as error:
            print(f"[CAMERA] Frame capture error: {error}")
            return None


    def release(self):
        self._stop_capture.set()
        if self._capture_thread is not None:
            self._capture_thread.join(timeout=2.0)
            if self._capture_thread.is_alive():
                # Do not reopen the camera while its capture thread is still blocked.

                raise RuntimeError("Camera capture is still busy; cannot release safely.")
            self._capture_thread = None
        with self._frame_lock:
            self._latest_frame = None
            self._latest_at = 0.0
        if self.camera is None:
            self.started = False
            return

        if config.SIMULATION_MODE:
            try:
                self.camera.release()
            except Exception:
                pass

        else:
            try:
                if self.started:
                    self.camera.stop()
            except Exception:
                pass

            try:
                self.camera.close()
            except Exception:
                pass

        self.camera = None
        self.started = False

        print("[CAMERA] Camera released.")
