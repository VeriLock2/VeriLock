"""Check that a slow camera capture does not hold up preview-frame requests."""

import threading
import time
import unittest
from unittest.mock import Mock, patch

import numpy as np

import config
from camera import Camera


class CameraWorkerTests(unittest.TestCase):
    def test_preview_uses_recent_frame_without_waiting_for_camera_read(self):
        camera = Camera.__new__(Camera)
        camera._frame_lock = threading.Lock()
        camera._latest_frame = None
        camera._latest_at = 0.0
        camera.frame_sequence = 0
        camera._stop_capture = threading.Event()
        camera._capture_thread = None
        camera.started = True
        camera.camera = Mock()
        reading = threading.Event()

        def slow_read():
            reading.set()
            camera._stop_capture.wait(timeout=1)
            return False, None

        camera.camera.read.side_effect = slow_read
        camera._store_frame(np.ones((10, 10, 3), dtype=np.uint8))
        with patch.object(config, "SIMULATION_MODE", True):
            camera._start_capture()
            try:
                self.assertTrue(reading.wait(timeout=1))
                start = time.monotonic()
                frame, number = camera.get_frame_with_sequence()
                self.assertLess(time.monotonic() - start, 0.15)
                self.assertEqual(number, 1)
                self.assertEqual(frame.shape, (10, 10, 3))
                camera._latest_at = time.monotonic() - 2
                self.assertEqual(camera.get_frame_with_sequence(), (None, None))
            finally:
                camera.release()
        camera.camera = None


if __name__ == "__main__":
    unittest.main()
