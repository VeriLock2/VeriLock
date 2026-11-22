"""Find faces with InsightFace and compare their ArcFace embeddings with saved templates."""

import os
import tempfile
import math
from zipfile import BadZipFile
from pathlib import Path

import numpy as np
import cv2

import config


class FaceRecognition:
    def __init__(self):
        try:
            from insightface.app import FaceAnalysis
        except ImportError as error:
            raise RuntimeError(
                "InsightFace is unavailable. Install insightface and onnxruntime."
            ) from error
        self.face_data_path = Path(config.FACE_EMBEDDINGS_PATH)
        self.face_data_path.mkdir(parents=True, exist_ok=True)
        self.pending = {}
        self.templates = {}
        self.analysis = FaceAnalysis(
            name=config.FACE_MODEL_NAME,
            allowed_modules=["detection", "recognition"],
            providers=["CPUExecutionProvider"],
        )
        self.analysis.prepare(ctx_id=-1, det_size=config.FACE_DETECTION_SIZE)
        self.reload_templates()

    @staticmethod
    def face_box(face, frame_shape=None):
        """Return a padded box for drawing around a detected face."""
        left, top, right, bottom = np.asarray(face.bbox, dtype=float)
        width, height = right - left, bottom - top
        pad = config.FACE_BOX_PADDING
        left -= width * pad
        right += width * pad
        top -= height * pad
        bottom += height * pad
        if frame_shape is not None:
            frame_height, frame_width = frame_shape[:2]
            left, right = max(0, left), min(frame_width - 1, right)
            top, bottom = max(0, top), min(frame_height - 1, bottom)
        x, y = math.floor(left), math.floor(top)
        r, b = math.ceil(right), math.ceil(bottom)
        return x, y, max(0, r - x), max(0, b - y)

    @staticmethod
    def draw_labeled_face(frame, face, label, known=False):
        """Draw the face box and identity label on the preview."""
        import cv2

        x, y, width, height = FaceRecognition.face_box(face, frame.shape)
        color = (0, 170, 0) if known else (0, 0, 210)
        cv2.rectangle(frame, (x, y), (x + width, y + height), color, 2)

        font = cv2.FONT_HERSHEY_SIMPLEX
        scale, thickness, pad = 0.85, 2, 5
        # Clip face labels to the image bounds.
        text = str(label).replace("\n", " ").encode("ascii", "replace").decode("ascii")
        available = max(1, frame.shape[1] - 2 * pad)
        while len(text) > 1 and cv2.getTextSize(text, font, scale, thickness)[0][0] > available:
            text = text[:-2] + "."
        (text_width, text_height), baseline = cv2.getTextSize(text, font, scale, thickness)
        label_x = max(0, min(x, frame.shape[1] - text_width - 2 * pad))
        label_top = y - text_height - baseline - 2 * pad
        if label_top < 0:
            label_top = min(frame.shape[0] - text_height - baseline - 2 * pad,
                            y + height + 2)
        label_top = max(0, label_top)
        label_bottom = min(frame.shape[0] - 1, label_top + text_height + baseline + 2 * pad)
        cv2.rectangle(frame, (label_x, label_top),
                      (min(frame.shape[1] - 1, label_x + text_width + 2 * pad), label_bottom),
                      color, -1)
        cv2.putText(frame, text, (label_x + pad, label_top + pad + text_height),
                    font, scale, (255, 255, 255), thickness, cv2.LINE_AA)

    @staticmethod
    def detection_zone(frame_shape):
        """Return the centered region used for face acceptance."""
        height, width = frame_shape[:2]
        zone_width = max(1, round(width * config.FACE_REGION_WIDTH_FRACTION))
        zone_height = max(1, round(height * config.FACE_REGION_HEIGHT_FRACTION))
        return ((width - zone_width) // 2, (height - zone_height) // 2,
                zone_width, zone_height)

    @staticmethod
    def face_fully_in_frame(frame, face):
        """Check face size and margin against the acceptance region."""
        if frame is None or not hasattr(frame, "shape"):
            return False
        height, _ = frame.shape[:2]
        x, y, zone_width, zone_height = FaceRecognition.detection_zone(frame.shape)
        left, top, right, bottom = np.asarray(face.bbox, dtype=float)
        face_width, face_height = right - left, bottom - top
        if (not np.isfinite((left, top, right, bottom)).all()
                or face_width <= 0 or face_height <= 0
                or face_height < height * config.FACE_MIN_HEIGHT_FRACTION):
            return False
        margin = config.FACE_FRAME_MARGIN
        return (
            left - face_width * margin >= x
            and top - face_height * margin >= y
            and right + face_width * margin <= x + zone_width
            and bottom + face_height * margin <= y + zone_height
        )

    @staticmethod
    def _embedding(face):
        value = np.asarray(face.embedding, dtype=np.float32).reshape(-1)
        norm = np.linalg.norm(value)
        if not np.isfinite(value).all() or not np.isfinite(norm) or norm == 0:
            raise ValueError("Face embedding is invalid")
        return value / norm

    def detect_faces(self, frame):
        if frame is None:
            return []
        return self.analysis.get(self._analysis_frame(frame))

    @staticmethod
    def frame_brightness(frame):
        """Measure mean brightness inside the acceptance region."""
        x, y, width, height = FaceRecognition.detection_zone(frame.shape)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return float(np.mean(gray[y:y + height, x:x + width]))

    @staticmethod
    def needs_more_light(frame):
        return FaceRecognition.frame_brightness(frame) < config.FACE_DARK_FRAME_THRESHOLD

    @staticmethod
    def needs_less_glare(frame):
        x, y, width, height = FaceRecognition.detection_zone(frame.shape)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return float(np.mean(gray[y:y + height, x:x + width] >= 245)) > 0.55

    @staticmethod
    def face_lighting_issue(frame, face):
        """Flag faces that are too dark or overexposed."""
        left, top, right, bottom = np.asarray(face.bbox, dtype=float)
        if not np.isfinite((left, top, right, bottom)).all():
            return None
        x1 = max(0, min(frame.shape[1], math.floor(left)))
        y1 = max(0, min(frame.shape[0], math.floor(top)))
        x2 = max(0, min(frame.shape[1], math.ceil(right)))
        y2 = max(0, min(frame.shape[0], math.ceil(bottom)))
        if x2 <= x1 or y2 <= y1:
            return None
        gray = cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
        if float(np.mean(gray)) < config.FACE_DARK_FRAME_THRESHOLD:
            return "ADD LIGHT"
        if float(np.mean(gray >= 245)) > config.FACE_OVEREXPOSED_PIXEL_FRACTION:
            return "REDUCE GLARE"
        return None

    @staticmethod
    def _analysis_frame(frame):
        """Improve contrast in dim recognition images with CLAHE."""
        if not config.FACE_LOW_LIGHT_ENHANCEMENT:
            return frame
        brightness = FaceRecognition.frame_brightness(frame)
        if brightness >= 100:
            return frame
        # Avoid boosting noise in very dark frames.
        strength = min(0.55, max(0.0, (100.0 - brightness) / 100.0))
        lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
        lightness = lab[:, :, 0]
        limited = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(lightness)
        lab[:, :, 0] = cv2.addWeighted(lightness, 1 - strength, limited, strength, 0)
        return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)

    def preprocess_face(self, frame, face):
        if not self.face_fully_in_frame(frame, face):
            return None
        return self._embedding(face)

    def save_face_sample(self, user_id, sample_number, embedding):
        value = np.asarray(embedding, dtype=np.float32).reshape(-1)
        if not np.isfinite(value).all() or not value.size:
            raise ValueError("Cannot enroll an invalid embedding")
        self.pending.setdefault(int(user_id), []).append(value.copy())

    def cancel_enrollment(self, user_id):
        if user_id is not None:
            self.pending.pop(int(user_id), None)

    def _template_path(self, user_id):
        return self.face_data_path / f"user_{int(user_id)}" / "embeddings.npz"

    def reload_templates(self):
        templates = {}
        for path in self.face_data_path.glob("user_*/embeddings.npz"):
            try:
                user_id = int(path.parent.name.removeprefix("user_"))
                with np.load(path, allow_pickle=False) as data:
                    if data["model"].item() != config.FACE_MODEL_NAME:
                        raise ValueError("Model differs from current configuration")
                    values = np.asarray(data["embeddings"], dtype=np.float32)
                if values.ndim != 2 or not np.isfinite(values).all() or not len(values):
                    raise ValueError("Invalid enrollment")
                norms = np.linalg.norm(values, axis=1, keepdims=True)
                if (norms == 0).any():
                    raise ValueError("Zero length embedding")
                templates[user_id] = values / norms
            except (OSError, ValueError, KeyError, BadZipFile, EOFError, TypeError) as error:
                print(f"[FACE] Skipping invalid template {path}: {error}")
        self.templates = templates

    def train_model(self):
        """Normalize the collected face embeddings and save them."""
        if not self.pending:
            return False
        for user_id, samples in list(self.pending.items()):
            values = np.stack(samples).astype(np.float32)
            norms = np.linalg.norm(values, axis=1, keepdims=True)
            if not np.isfinite(values).all() or (norms == 0).any():
                raise ValueError("Invalid enrollment samples")
            values /= norms
            path = self._template_path(user_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="wb", suffix=".npz", dir=path.parent, delete=False
                ) as output:
                    temporary = Path(output.name)
                    np.savez_compressed(
                        output, model=config.FACE_MODEL_NAME, embeddings=values
                    )
                os.replace(temporary, path)
                self.templates[user_id] = values
                del self.pending[user_id]
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        return True

    def remove_enrollment(self, user_id):
        self.cancel_enrollment(user_id)
        self._template_path(user_id).unlink(missing_ok=True)
        self.templates.pop(int(user_id), None)

    def recognize_face(self, frame, face):
        if not self.templates:
            return None, None
        if not self.face_fully_in_frame(frame, face):
            return None, 0.0
        probe = self._embedding(face)
        best_id, best_score = None, -1.0
        for user_id, values in self.templates.items():
            if values.shape[1] != probe.size:
                continue
            score = float(np.max(values @ probe))
            if score > best_score:
                best_id, best_score = user_id, score
        if best_id is None:
            return None, None
        print(f"[FACE] Best cosine similarity: {best_score:.3f}")
        if best_score >= config.FACE_SIMILARITY_THRESHOLD:
            return best_id, best_score
        return None, best_score
