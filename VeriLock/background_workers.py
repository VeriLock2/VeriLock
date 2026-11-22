"""Run face analysis, enrollment, and RFID scans in background threads.

Only the UI thread updates Tkinter widgets."""

import queue
import threading
import time

import config


def _replace_latest(mailbox, value):
    try:
        mailbox.put_nowait(value)
    except queue.Full:
        try:
            mailbox.get_nowait()
        except queue.Empty:
            pass
        mailbox.put_nowait(value)


class FaceWorker:
    """Analyze one camera frame at a time and skip queued frames that are no longer needed."""

    def __init__(self, recognizer):
        self.recognizer = recognizer
        self.frames = queue.Queue(maxsize=1)
        self.results = queue.Queue(maxsize=1)
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True, name="verilock-face")
        self.thread.start()

    def submit(self, generation, frame):
        if not self.stopping.is_set():
            _replace_latest(self.frames, (generation, frame.copy()))

    def _run(self):
        while not self.stopping.is_set():
            try:
                generation, frame = self.frames.get(timeout=0.1)
            except queue.Empty:
                continue
            if self.stopping.is_set():
                break
            try:
                faces = self.recognizer.detect_faces(frame)
                matches = []
                for face in faces:
                    if (self.recognizer.face_fully_in_frame(frame, face) and
                            self.recognizer.face_lighting_issue(frame, face) is None):
                        matches.append(self.recognizer.recognize_face(frame, face))
                    else:
                        matches.append(None)
                _replace_latest(self.results, (generation, frame, faces, matches, None))
            except Exception as error:
                _replace_latest(self.results, (generation, frame, [], None, error))
            # Give the preview and buttons time to update during slower face checks.

            self.stopping.wait(config.FACE_ANALYSIS_COOLDOWN)

    def close(self, timeout=0.1):
        """Ask the worker to stop and report whether it finished before the timeout."""
        self.stopping.set()
        self.thread.join(timeout=timeout)
        return not self.thread.is_alive()


class EnrollmentWorker:
    """Analyze enrollment images in the background so the preview and buttons stay responsive."""

    def __init__(self, recognizer):
        self.recognizer = recognizer
        self.frames = queue.Queue(maxsize=1)
        self.results = queue.Queue(maxsize=1)
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True,
                                       name="verilock-enrollment")
        self.thread.start()

    def submit(self, generation, frame):
        if not self.stopping.is_set():
            _replace_latest(self.frames, (generation, frame.copy()))

    def _run(self):
        while not self.stopping.is_set():
            try:
                generation, frame = self.frames.get(timeout=0.1)
            except queue.Empty:
                continue
            if self.stopping.is_set():
                break
            try:
                faces = self.recognizer.detect_faces(frame)
                issue = (self.recognizer.face_lighting_issue(frame, faces[0])
                         if len(faces) == 1 else None)
                in_frame = (self.recognizer.face_fully_in_frame(frame, faces[0])
                            if len(faces) == 1 else False)
                embedding = (self.recognizer.preprocess_face(frame, faces[0])
                             if len(faces) == 1 and issue is None and in_frame
                             else None)
                brightness = (self.recognizer.needs_more_light(frame),
                              self.recognizer.needs_less_glare(frame)) if not faces else None
                _replace_latest(self.results, (generation, faces, issue,
                                               in_frame, embedding, brightness, None))
            except Exception as error:
                _replace_latest(self.results, (generation, [], None, False,
                                               None, None, error))
            self.stopping.wait(config.FACE_ANALYSIS_COOLDOWN)

    def close(self, timeout=0.1):
        """Ask the worker to stop and report whether it finished before the timeout."""
        self.stopping.set()
        self.thread.join(timeout=timeout)
        return not self.thread.is_alive()


class RFIDWorker:
    """Keep this worker in charge of the PN532 until admin mode or shutdown stops it."""

    def __init__(self, reader):
        self.reader = reader
        self.results = queue.Queue(maxsize=4)
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True, name="verilock-rfid")
        self.thread.start()

    def _run(self):
        present_uid = None
        absent_since = None
        while not self.stopping.is_set():
            try:
                card = self.reader.read_card(timeout=0.05)
                now = time.monotonic()
                if card is None:
                    if present_uid is not None:
                        if absent_since is None:
                            absent_since = now
                        elif now - absent_since >= 0.6:
                            present_uid = None
                            absent_since = None
                else:
                    absent_since = None
                    uid = card.get("uid")
                    if uid and uid != present_uid:
                        present_uid = uid
                        result = self.reader.read_verilock_card(uid_bytes=card["uid_bytes"])
                        if result is not None and not self.stopping.is_set():
                            _replace_latest(self.results, (uid, result))
            except Exception as error:
                print(f"[RFID] Background scan error: {error}")
            self.stopping.wait(config.RFID_SCAN_DELAY)

    def close(self, timeout=0.1):
        """Ask the worker to stop and report whether it finished before the timeout."""
        self.stopping.set()
        self.thread.join(timeout=timeout)
        return not self.thread.is_alive()
