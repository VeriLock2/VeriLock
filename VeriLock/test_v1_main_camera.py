"""Check the main screen, face verification, and RFID access."""

import unittest
import queue
import contextlib
import io
import threading
from unittest.mock import Mock, patch

import numpy as np

import config
from camera import Camera
from main import VeriLockApp
import main as main_module
from lock_controller import LockController


class StartupLockTests(unittest.TestCase):
    def run_startup(self, failure=None):
        events = []
        bolt = Mock()
        rack = Mock()

        def movement(name):
            def run(*args):
                events.append(name)
                if name == failure:
                    raise RuntimeError("startup movement failed")
            return run

        rack.engage.side_effect = movement("engage")
        bolt.move_to.side_effect = movement("zero")
        bolt.lock.side_effect = movement("lock")
        release_count = 0
        def disengage():
            nonlocal release_count
            release_count += 1
            movement("initialize_rack" if release_count == 1 else "disengage")()
        rack.disengage.side_effect = disengage
        access = Mock(side_effect=lambda *args: events.append("access") or Mock())
        constructors = {
            "Database": Mock(), "Servo": Mock(return_value=bolt),
            "RackServo": Mock(return_value=rack),
            "LockController": LockController, "AccessController": access,
            "RFIDReader": Mock(), "Camera": Mock(), "FaceRecognition": Mock(),
            "VeriLockApp": Mock(),
        }
        with contextlib.ExitStack() as stack:
            for name, replacement in constructors.items():
                stack.enter_context(patch.object(main_module, name, replacement))
            tk_root = stack.enter_context(patch.object(main_module.tk, "Tk"))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            errors = stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
            exit_status = main_module.main()
        constructors["exit_status"] = exit_status
        constructors["error_log"] = errors.getvalue()
        return events, bolt, rack, access, constructors, tk_root

    def test_startup_locks_before_authentication_and_gui(self):
        events, bolt, rack, access, constructors, tk_root = self.run_startup()
        self.assertEqual(events, ["initialize_rack", "zero", "engage", "lock", "disengage", "access"])
        bolt.move_to.assert_called_once_with(0.0)
        bolt.unlock.assert_not_called()
        access.assert_called_once()
        constructors["VeriLockApp"].assert_called_once()
        tk_root.return_value.mainloop.assert_called_once()
        self.assertEqual(constructors["exit_status"], 0)
        self.assertEqual(constructors["error_log"], "")

    def test_startup_movement_failure_never_enables_authentication(self):
        for failure in ("initialize_rack", "zero", "engage", "lock", "disengage"):
            with self.subTest(failure=failure):
                events, bolt, rack, access, constructors, tk_root = self.run_startup(failure)
                access.assert_not_called()
                constructors["RFIDReader"].assert_not_called()
                constructors["VeriLockApp"].assert_not_called()
                tk_root.assert_not_called()
                self.assertEqual(constructors["exit_status"], 1)
                self.assertIn("Traceback (most recent call last)", constructors["error_log"])
                self.assertIn("RuntimeError: startup movement failed", constructors["error_log"])
                self.assertEqual(rack.disengage.call_count,
                                 1 if failure in ("initialize_rack", "zero") else 2)
                rack.cleanup.assert_called_once()
                bolt.cleanup.assert_called_once()
                if failure in ("initialize_rack", "zero", "engage"):
                    bolt.lock.assert_not_called()
                if failure in ("initialize_rack", "zero"):
                    rack.engage.assert_not_called()

    def test_service_requires_manual_restart_after_failure(self):
        import configparser
        from pathlib import Path

        unit = configparser.RawConfigParser(strict=False)
        unit.read(Path(__file__).with_name("verilock.service"))
        self.assertEqual(unit["Service"]["Restart"], "no")


class CameraRecoveryTests(unittest.TestCase):
    def make_app(self):
        app = VeriLockApp.__new__(VeriLockApp)
        app.root = Mock()
        app.running = True
        app.shutdown_started = False
        app.admin_process = None
        app.current_mode = "HOME"
        app.camera = None
        app.face_recognition = Mock()
        app.face_worker = None
        app.rfid_worker = Mock()
        app.rfid_reader = Mock()
        app.admin_camera_released = True
        app._camera_recovery = None
        app._camera_recovery_message = ""
        app.face_button = Mock()
        app.face_status_label = Mock()
        app.face_detail_label = Mock()
        app.home_status_label = Mock()
        app.lock_controller = Mock()
        app.lock_controller.can_authenticate.return_value = True
        return app

    def test_retries_then_adopts_camera_without_touching_rfid(self):
        app = self.make_app()
        camera = Mock()
        with patch("main.Camera", side_effect=[RuntimeError("busy"), RuntimeError("busy"), camera]) as ctor, \
                patch("main.CAMERA_RECOVERY_RETRY_DELAY", 0), patch("main.FaceWorker") as worker:
            self.assertFalse(app.restore_main_camera())
            job = app._camera_recovery
            self.assertTrue(job["ready"].wait(2))
            self.assertIsNone(app.camera)  # UI owns the handoff.
            app.poll_camera_recovery(job)
            job["thread"].join(2)
            self.assertEqual(ctor.call_count, 3)
            worker.assert_called_once_with(app.face_recognition)
        self.assertIs(app.camera, camera)
        self.assertIsNone(app._camera_recovery)
        self.assertTrue(job["adopted"].is_set())
        camera.release.assert_not_called()
        app.rfid_worker.close.assert_not_called()
        app.rfid_reader.cleanup.assert_not_called()
        app.face_button.config.assert_called_with(state="normal")

    def test_five_failures_leave_rfid_available(self):
        app = self.make_app()
        with patch("main.Camera", side_effect=RuntimeError("not detected")) as ctor, \
                patch("main.CAMERA_RECOVERY_RETRY_DELAY", 0):
            app.restore_main_camera()
            job = app._camera_recovery
            self.assertTrue(job["ready"].wait(2))
            job["thread"].join(2)
            app.poll_camera_recovery(job)
            self.assertEqual(ctor.call_count, 5)
        self.assertIsNone(app.camera)
        self.assertEqual(app.current_mode, "HOME")
        app.face_detail_label.config.assert_called_with(text="Camera unavailable—use RFID.")
        app.rfid_worker.close.assert_not_called()

    def test_slow_constructor_does_not_block_ui_and_duplicate_requests(self):
        app = self.make_app()
        entered, release = threading.Event(), threading.Event()
        camera = Mock()
        def slow_camera():
            entered.set()
            if not release.wait(2):
                raise RuntimeError("test timed out")
            return camera
        with patch("main.Camera", side_effect=slow_camera) as ctor:
            try:
                app.restore_main_camera()
                job = app._camera_recovery
                self.assertTrue(entered.wait(2))
                self.assertFalse(job["ready"].is_set())
                app.restore_main_camera()
                self.assertIs(app._camera_recovery, job)
                self.assertEqual(ctor.call_count, 1)
                self.assertFalse(app.cancel_camera_recovery())
            finally:
                release.set()
                job["thread"].join(2)
        self.assertFalse(job["thread"].is_alive())
        camera.release.assert_called_once()
        self.assertTrue(app.cancel_camera_recovery())
        self.assertIsNone(app.camera)

    def test_cancelled_success_is_not_adopted(self):
        app = self.make_app()
        camera = Mock()
        with patch("main.Camera", return_value=camera):
            app.restore_main_camera()
            job = app._camera_recovery
            self.assertTrue(job["ready"].wait(2))
            app.cancel_camera_recovery()
            job["thread"].join(2)
            app.poll_camera_recovery(job)
        camera.release.assert_called_once()
        self.assertIsNone(app.camera)
        self.assertIsNone(app._camera_recovery)
        self.assertFalse(job["adopted"].is_set())

    def test_shutdown_does_not_attach_a_recovered_camera(self):
        app = self.make_app()
        camera = Mock()
        with patch("main.Camera", return_value=camera):
            app.restore_main_camera()
            job = app._camera_recovery
            self.assertTrue(job["ready"].wait(2))
            app.running = False
            app.shutdown_started = True
            app.poll_camera_recovery(job)
            job["thread"].join(2)
        camera.release.assert_called_once()
        self.assertIsNone(app.camera)

    def test_admin_waits_for_recovery_to_release_camera(self):
        app = self.make_app()
        app.cancel_camera_recovery = Mock(return_value=False)
        app.access_controller = Mock()
        with patch("main.subprocess.Popen") as launch:
            app.open_admin_menu(action="LOCK")
        launch.assert_not_called()
        app.access_controller.clear_pending.assert_not_called()
        self.assertEqual(app.root.after.call_args.args[0], 100)

    def test_success_initializes_missing_model_off_ui_thread(self):
        app = self.make_app()
        app.face_recognition = None
        camera, recognizer = Mock(), Mock()
        ui_thread = threading.get_ident()
        def make_model():
            self.assertNotEqual(threading.get_ident(), ui_thread)
            return recognizer
        with patch("main.Camera", return_value=camera), \
                patch("main.FaceRecognition", side_effect=make_model), patch("main.FaceWorker"):
            app.restore_main_camera()
            job = app._camera_recovery
            self.assertTrue(job["ready"].wait(2))
            app.poll_camera_recovery(job)
            job["thread"].join(2)
        self.assertIs(app.face_recognition, recognizer)

    def test_recovery_preserves_active_rfid_screen(self):
        app = self.make_app()
        camera = Mock()
        with patch("main.Camera", return_value=camera), patch("main.FaceWorker"):
            app.restore_main_camera()
            job = app._camera_recovery
            self.assertTrue(job["ready"].wait(2))
            app.current_mode = "RFID"
            app.face_detail_label.reset_mock()
            app.poll_camera_recovery(job)
            job["thread"].join(2)
        self.assertEqual(app.current_mode, "RFID")
        app.face_detail_label.config.assert_not_called()


class MainCameraTests(unittest.TestCase):
    def make_app(self):
        app = VeriLockApp.__new__(VeriLockApp)
        app.camera = Mock()
        app.face_recognition = Mock()
        app.rfid_reader = Mock()
        app.lock_controller = Mock()
        app.lock_controller.can_authenticate.return_value = True
        app.current_mode = "HOME"
        app.session_generation = 0
        app.clear_screen = Mock(side_effect=AssertionError("Switched camera screen"))
        app.rfid_input_frame = Mock()
        app.cancel_button = Mock()
        app.face_status_label = Mock()
        app.face_detail_label = Mock()
        app.rfid_entry = Mock()
        return app

    def make_auth_app(self):
        app = VeriLockApp.__new__(VeriLockApp)
        app.face_authentication_active = True
        app.face_authentication_complete = False
        app.recognition_frame_counts = {}
        app.recognition_frame_histories = {}
        app.recognized_face_id = None
        app.recognition_frames = 0
        app.face_overlay_entries = []
        app.face_recognition = Mock()
        app.face_recognition.face_fully_in_frame.return_value = True
        app.face_recognition.face_lighting_issue.return_value = None
        app.database = Mock()
        users = {
            11: {"id": 1, "name": "Alex"},
            22: {"id": 2, "name": "Blair"},
        }
        app.database.get_user_by_face_id.side_effect = users.get
        app.face_status_label = Mock()
        app.face_detail_label = Mock()
        app.access_controller = Mock(last_result="GRANTED")
        app.access_controller.authenticate_face.return_value = True
        app.schedule_session_action = Mock()
        app.show_home_screen = Mock()
        app.restart_face_authentication = Mock()
        return app

    def test_face_and_simulated_rfid_use_the_existing_camera_pane(self):
        app = self.make_app()
        app.start_face_authentication()
        self.assertEqual(app.current_mode, "FACE")
        with patch.object(config, "SIMULATION_MODE", True):
            app.start_rfid_authentication()
        self.assertEqual(app.current_mode, "RFID")
        self.assertIs(app.rfid_status_label, app.face_status_label)
        app.clear_screen.assert_not_called()

    def test_r_key_starts_rfid_only_in_simulation_on_exterior_screens(self):
        app = self.make_app()
        app.start_rfid_authentication = Mock()
        with patch.object(config, "SIMULATION_MODE", True):
            for mode in ("HOME", "FACE"):
                app.current_mode = mode
                app.on_simulation_rfid_key()
            app.current_mode = "ADMIN"
            app.on_simulation_rfid_key()
        with patch.object(config, "SIMULATION_MODE", False):
            app.current_mode = "HOME"
            app.on_simulation_rfid_key()
        self.assertEqual(app.start_rfid_authentication.call_count, 2)

    def test_home_prompts_do_not_show_r_on_pi(self):
        app = self.make_app()
        app.database = Mock()
        with patch.object(config, "SIMULATION_MODE", False):
            for ultra in (True, False):
                app.database.ultra_secure_enabled.return_value = ultra
                self.assertNotIn("press r", app.home_instruction().lower())
                self.assertIn("card", app.home_instruction().lower())
        with patch.object(config, "SIMULATION_MODE", True):
            self.assertIn("press r", app.home_instruction().lower())

    def test_idle_preview_never_starts_face_authentication(self):
        app = self.make_app()
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        app.camera.get_frame.return_value = frame
        app.display_camera_frame = Mock()
        app.start_face_authentication = Mock()
        app.next_preview_frame = 0.0
        with patch("main.time.monotonic", return_value=1.0):
            app.update_idle_camera()
        app.display_camera_frame.assert_called_once_with(frame)
        app.start_face_authentication.assert_not_called()

    def test_old_face_result_cannot_authenticate_after_screen_change(self):
        app = self.make_app()
        app.current_mode = "FACE"
        app.face_authentication_active = True
        app.session_generation = 2
        app.face_worker = Mock(results=queue.Queue())
        app.face_worker.results.put((1, np.zeros((10, 10, 3)), [], None, None))
        app.process_face = Mock()
        app.update_face_results()
        app.process_face.assert_not_called()

    def test_face_result_does_not_replace_live_preview(self):
        app = self.make_app()
        app.current_mode = "FACE"
        app.face_authentication_active = True
        app.face_worker = Mock(results=queue.Queue())
        analyzed_frame = np.zeros((10, 10, 3), dtype=np.uint8)
        app.face_worker.results.put((app.session_generation, analyzed_frame, [], None, None))
        app.process_face = Mock()
        app.display_camera_frame = Mock()
        app.recognized_face_id = None
        app.update_face_results()
        app.process_face.assert_called_once_with(analyzed_frame, [], None)
        app.display_camera_frame.assert_not_called()
        self.assertEqual(app.face_overlay[2], "UNKNOWN")

    def test_two_authorized_faces_build_independent_windows_and_grant_access(self):
        app = self.make_auth_app()
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        faces = [object(), object()]
        matches = [(11, 0.91), (22, 0.94)]

        with patch.object(config, "REQUIRED_RECOGNITION_FRAMES", 2):
            app.process_face(frame, faces, matches)
            self.assertEqual(app.recognition_frame_counts, {11: 1, 22: 1})
            app.process_face(frame, faces, matches)

        app.access_controller.authenticate_face.assert_called_once_with(2)
        self.assertTrue(app.face_authentication_complete)
        self.assertFalse(app.face_authentication_active)
        self.assertEqual(
            [(entry[1], entry[2]) for entry in app.face_overlay_entries],
            [("Alex", True), ("Blair", True)],
        )
        self.assertEqual(app.face_status_label.config.call_args.kwargs["text"],
                         "ACCESS GRANTED")

    def test_matches_accumulate_across_missed_frames_within_window(self):
        app = self.make_auth_app()
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        face = object()
        observations = [
            ([face], [(11, 0.91)]),
            ([], []),
            ([face], [(11, 0.91)]),
            ([], []),
            ([face], [(11, 0.91)]),
        ]

        with patch.object(config, "REQUIRED_RECOGNITION_FRAMES", 3), \
                patch.object(config, "RECOGNITION_FRAME_WINDOW", 5):
            for index, (faces, matches) in enumerate(observations):
                app.process_face(frame, faces, matches)
                if index < len(observations) - 1:
                    app.access_controller.authenticate_face.assert_not_called()

        app.access_controller.authenticate_face.assert_called_once_with(1)
        self.assertTrue(app.face_authentication_complete)

    def test_match_expires_after_it_leaves_rolling_window(self):
        app = self.make_auth_app()
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        face = object()

        with patch.object(config, "REQUIRED_RECOGNITION_FRAMES", 2), \
                patch.object(config, "RECOGNITION_FRAME_WINDOW", 3):
            app.process_face(frame, [face], [(11, 0.91)])
            app.process_face(frame, [], [])
            app.process_face(frame, [], [])
            app.process_face(frame, [face], [(11, 0.91)])

            self.assertEqual(app.recognition_frame_counts, {11: 1})
            app.access_controller.authenticate_face.assert_not_called()

            app.process_face(frame, [face], [(11, 0.91)])

        app.access_controller.authenticate_face.assert_called_once_with(1)

    def test_authorized_face_is_not_blocked_by_an_unknown_second_face(self):
        app = self.make_auth_app()
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        faces = [object(), object()]
        matches = [(11, 0.91), (None, 0.22)]

        with patch.object(config, "REQUIRED_RECOGNITION_FRAMES", 2):
            app.process_face(frame, faces, matches)
            app.process_face(frame, faces, matches)

        app.access_controller.authenticate_face.assert_called_once_with(1)
        self.assertTrue(app.face_authentication_complete)

    def test_face_analysis_waits_for_fresh_camera_frame(self):
        app = self.make_app()
        app.current_mode = "FACE"
        app.face_authentication_active = True
        app.face_worker = Mock()
        app.face_overlay = None
        app.next_preview_frame = 0.0
        app.next_face_analysis = 0.0
        app.last_face_frame_sequence = None
        app.display_camera_frame = Mock()
        frame = np.zeros((10, 10, 3), dtype=np.uint8)
        app.camera = Camera.__new__(Camera)
        app.camera.get_frame_with_sequence = Mock(side_effect=[
            (frame, 1), (frame, 1), (frame, 2), (frame, 2)])
        with patch("main.time.monotonic", side_effect=[1.0, 1.10, 1.20, 1.40]):
            app.update_camera()
            app.update_camera()
            app.update_camera()
            app.update_camera()
        self.assertEqual(app.face_worker.submit.call_count, 2)
        self.assertEqual(app.display_camera_frame.call_count, 4)

    def test_preview_continues_after_face_authentication_finishes(self):
        app = self.make_app()
        app.current_mode = "FACE"
        app.face_authentication_active = False
        app.face_worker = Mock()
        app.next_preview_frame = 0.0
        app.display_camera_frame = Mock()
        app.camera.get_frame.return_value = np.zeros((10, 10, 3), dtype=np.uint8)
        with patch("main.time.monotonic", side_effect=[1.0, 1.10]):
            app.update_camera()
            app.update_camera()
        self.assertEqual(app.display_camera_frame.call_count, 2)
        app.face_worker.submit.assert_not_called()
        app.face_status_label.config.assert_not_called()

    def test_physical_rfid_reads_on_home_and_face_without_button(self):
        app = self.make_app()
        app.face_authentication_active = True
        app.rfid_authentication_active = True
        app.access_controller = Mock(last_result="IDLE")
        app.rfid_worker = Mock(results=queue.Queue())
        app.rfid_worker.results.put(("01:02:03:04", {"valid": True, "user_id": 1}))

        def begin_rfid():
            app.current_mode = "RFID"
            app.rfid_authentication_active = True

        app.start_rfid_authentication = Mock(side_effect=begin_rfid)
        app.process_rfid_uid = Mock()
        app.update_rfid()
        self.assertEqual(app.process_rfid_uid.call_count, 1)
        self.assertEqual(app.start_rfid_authentication.call_count, 1)

        app.current_mode = "FACE"
        app.face_authentication_active = True
        app.rfid_worker.results.put(("02:03:04:05", {"valid": True, "user_id": 2}))
        app.update_rfid()
        self.assertEqual(app.process_rfid_uid.call_count, 2)

        app.current_mode = "ADMIN"
        app.rfid_worker.results.put(("03:04:05:06", {"valid": True, "user_id": 3}))
        app.update_rfid()
        self.assertEqual(app.process_rfid_uid.call_count, 2)

    def test_card_first_starts_face_without_rebuilding_camera(self):
        app = self.make_app()
        app.database = Mock()
        app.rfid_authentication_active = True
        app.access_controller = Mock(last_result="PENDING_FACE")
        app.access_controller.authenticate_rfid.return_value = False
        app.rfid_status_label = app.face_status_label
        app.rfid_detail_label = app.face_detail_label
        app.schedule_session_action = Mock()
        app.process_rfid_uid("01:02:03:04")
        self.assertFalse(app.rfid_authentication_active)
        self.assertEqual(app.schedule_session_action.call_args.args[1],
                         app.start_face_authentication)
        app.current_mode = "RFID"
        app.start_face_authentication()
        self.assertEqual(app.current_mode, "FACE")
        app.clear_screen.assert_not_called()

    def test_face_first_accepts_transition_to_rfid(self):
        app = self.make_app()
        app.current_mode = "FACE"
        app.face_authentication_complete = True
        app.access_controller = Mock(last_result="PENDING_RFID")
        with patch.object(config, "SIMULATION_MODE", False):
            app.start_rfid_authentication()
        self.assertEqual(app.current_mode, "RFID")
        self.assertTrue(app.rfid_authentication_active)
        app.clear_screen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
