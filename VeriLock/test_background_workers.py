"""Check the background workers for face recognition, enrollment, and RFID scans."""

import threading
import unittest
from unittest.mock import Mock, patch

import numpy as np

import config
from background_workers import EnrollmentWorker, FaceWorker, RFIDWorker


class WorkerTests(unittest.TestCase):
    def test_enrollment_analysis_does_not_block_preview(self):
        started = threading.Event()
        release = threading.Event()
        recognizer = Mock()
        face = Mock()

        def detect(frame):
            started.set()
            release.wait(timeout=2)
            return [face]

        recognizer.detect_faces.side_effect = detect
        recognizer.face_lighting_issue.return_value = None
        recognizer.face_fully_in_frame.return_value = True
        recognizer.preprocess_face.return_value = np.ones(3)
        worker = EnrollmentWorker(recognizer)
        try:
            worker.submit(8, np.zeros((20, 20, 3), dtype=np.uint8))
            self.assertTrue(started.wait(timeout=2))
            self.assertTrue(worker.thread.is_alive())
            release.set()
            generation, faces, issue, in_frame, embedding, _, error = worker.results.get(timeout=2)
            self.assertEqual(generation, 8)
            self.assertEqual(faces, [face])
            self.assertTrue(in_frame)
            np.testing.assert_array_equal(embedding, np.ones(3))
            self.assertIsNone(error)
        finally:
            release.set()
            worker.close()

    def test_face_inference_runs_off_caller_thread_and_returns_session(self):
        caller = threading.current_thread()
        recognizer = Mock()
        seen_threads = []

        def detect(frame):
            seen_threads.append(threading.current_thread())
            return [frame.shape]

        recognizer.detect_faces.side_effect = detect
        recognizer.face_fully_in_frame.return_value = False
        worker = FaceWorker(recognizer)
        try:
            worker.submit(7, np.zeros((20, 20, 3), dtype=np.uint8))
            generation, frame, faces, match, error = worker.results.get(timeout=2)
            self.assertEqual(generation, 7)
            self.assertEqual(faces, [(20, 20, 3)])
            self.assertIsNone(error)
            self.assertIs(seen_threads[0], worker.thread)
            self.assertIsNot(seen_threads[0], caller)
        finally:
            worker.close()

    def test_face_worker_recognizes_each_eligible_face(self):
        first_face = object()
        second_face = object()
        recognizer = Mock()
        recognizer.detect_faces.return_value = [first_face, second_face]
        recognizer.face_fully_in_frame.return_value = True
        recognizer.face_lighting_issue.return_value = None
        recognizer.recognize_face.side_effect = [(11, 0.91), (22, 0.89)]
        worker = FaceWorker(recognizer)
        try:
            worker.submit(9, np.zeros((20, 20, 3), dtype=np.uint8))
            generation, _, faces, matches, error = worker.results.get(timeout=2)
            self.assertEqual(generation, 9)
            self.assertEqual(faces, [first_face, second_face])
            self.assertEqual(matches, [(11, 0.91), (22, 0.89)])
            self.assertEqual(recognizer.recognize_face.call_count, 2)
            self.assertIsNone(error)
        finally:
            worker.close()

    def test_rfid_worker_reads_payload_once_until_card_removed(self):
        card = {"uid": "01:02:03:04", "uid_bytes": b"\x01\x02\x03\x04"}
        reader = Mock()
        reader.read_card.side_effect = [card, card, card, None, None, card]
        reader.read_verilock_card.return_value = {"valid": True, "user_id": 1}

        # Advance the absence interval without a real 0.6 second delay.
        with patch("background_workers.time.monotonic", side_effect=[0, 0.1, 0.2, 0.3, 1.0, 1.1]), \
                patch.object(config, "RFID_SCAN_DELAY", 0.001):
            worker = RFIDWorker(reader)
            try:
                first = worker.results.get(timeout=2)
                second = worker.results.get(timeout=2)
                self.assertEqual(first[0], card["uid"])
                self.assertEqual(second[0], card["uid"])
                self.assertEqual(reader.read_verilock_card.call_count, 2)
                reader.read_verilock_card.assert_called_with(uid_bytes=card["uid_bytes"])
            finally:
                worker.close()


if __name__ == "__main__":
    unittest.main()
