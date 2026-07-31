"""Real face embedding and quality checks.

No biometric fallback is provided: if InsightFace cannot initialize,
the API returns a controlled service error rather than authenticating a person.
Model is always loaded from local disk — never downloaded at runtime.
"""
import base64
import logging
import os
from dataclasses import dataclass
from typing import Iterable

import cv2
import numpy as np

try:
    from insightface.app import FaceAnalysis
except ImportError as exc:  # surfaced by get_engine()
    FaceAnalysis = None

logger = logging.getLogger("biometrics")

# Path to bundled models checked into the repo.
# FaceAnalysis(root=PROJECT_ROOT) looks for models under {root}/models/{name}.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MODELS_DIR = os.path.join(PROJECT_ROOT, "models")


def _model_available(name: str = "buffalo_s") -> bool:
    """Return True when the model directory exists and contains .onnx files."""
    model_dir = os.path.join(_MODELS_DIR, name)
    if not os.path.isdir(model_dir):
        return False
    onnx_files = [f for f in os.listdir(model_dir) if f.endswith(".onnx")]
    return len(onnx_files) > 0


class BiometricError(ValueError):
    pass


@dataclass
class FaceResult:
    embedding: list[float]
    confidence: float
    box: list[int]
    landmarks: list[list[float]]
    quality: float


@dataclass
class BatchResult:
    embedding: list[float]
    best_frame: int
    accepted: int
    discarded: int
    guidance: list[str]


class BiometricEngine:
    def __init__(self):
        if FaceAnalysis is None:
            raise RuntimeError("InsightFace must be installed before biometric authentication is enabled.")

        model_name = os.getenv("INSIGHTFACE_MODEL", "buffalo_s")

        # Never trigger a download — check local model bundle first
        if not _model_available(model_name):
            raise RuntimeError(
                f"Face recognition model '{model_name}' not installed. "
                "Run scripts/setup_models.py or bundle the model directory."
            )

        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"] if os.getenv("USE_GPU", "false").lower() == "true" else ["CPUExecutionProvider"]
        self.recognizer = FaceAnalysis(name=model_name, root=PROJECT_ROOT, providers=providers)
        self.recognizer.prepare(ctx_id=0 if os.getenv("USE_GPU", "false").lower() == "true" else -1, det_size=(640, 640))
        logger.info("InsightFace loaded from %s (model=%s)", _MODELS_DIR, model_name)

    @staticmethod
    def decode(image_data: str) -> np.ndarray:
        if not image_data:
            raise BiometricError("A camera image is required.")
        try:
            raw = base64.b64decode(image_data.split(",")[-1])
            frame = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        except Exception as exc:
            raise BiometricError("The supplied image could not be decoded.") from exc
        if frame is None or frame.size == 0:
            raise BiometricError("The supplied image is invalid.")
        return frame

    @staticmethod
    def _quality(frame: np.ndarray, box: Iterable[float]) -> float:
        x1, y1, x2, y2 = [max(0, int(v)) for v in box]
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return 0.0
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        sharpness = min(cv2.Laplacian(gray, cv2.CV_64F).var() / 130.0, 1.0)
        brightness = gray.mean() / 255.0
        exposure = max(0.0, 1.0 - abs(brightness - 0.52) / 0.52)
        return float((sharpness * 0.6) + (exposure * 0.4))

    def extract(self, image_data: str) -> FaceResult:
        frame = self.decode(image_data)
        faces = self.recognizer.get(frame)
        if len(faces) == 0:
            raise BiometricError("No face detected. Position one face in the camera frame.")
        if len(faces) > 1:
            raise BiometricError("Multiple faces detected. Only one person can authenticate at a time.")
        face = faces[0]
        confidence = float(face.det_score)
        if confidence < 0.50:
            raise BiometricError("Face confidence is too low. Improve lighting and try again.")
        quality = self._quality(frame, face.bbox)
        if quality < 0.20:
            raise BiometricError("Image quality is too low. Use better lighting and keep still.")
        return FaceResult(
            embedding=face.normed_embedding.astype(float).tolist(), confidence=confidence,
            box=[int(v) for v in face.bbox.tolist()], landmarks=face.kps.astype(float).tolist(), quality=quality
        )

    def extract_batch(self, images: list[str]) -> BatchResult:
        """Use three to five enrollment frames, retaining only well-framed faces."""
        if not 3 <= len(images) <= 5:
            raise BiometricError("Capture between three and five face images.")
        accepted: list[tuple[int, FaceResult]] = []
        guidance: list[str] = []
        for index, image in enumerate(images):
            try:
                frame = self.decode(image)
                faces = self.recognizer.get(frame)
                if len(faces) != 1:
                    guidance.append("Keep exactly one face in the camera frame.")
                    continue
                face = faces[0]
                height, width = frame.shape[:2]
                x1, y1, x2, y2 = face.bbox
                face_width, face_height = x2 - x1, y2 - y1
                center_offset = abs(((x1 + x2) / 2) - width / 2) / width + abs(((y1 + y2) / 2) - height / 2) / height
                if face_width < width * 0.20 or face_height < height * 0.20:
                    guidance.append("Move closer to the camera.")
                    continue
                if center_offset > 0.22:
                    guidance.append("Center your face in the camera frame.")
                    continue
                if float(face.det_score) < 0.75:
                    guidance.append("Look directly at the camera.")
                    continue
                quality = self._quality(frame, face.bbox)
                if quality < 0.20:
                    guidance.append("Improve lighting and hold still.")
                    continue
                accepted.append((index, FaceResult(face.normed_embedding.astype(float).tolist(), float(face.det_score),
                    [int(v) for v in face.bbox.tolist()], face.kps.astype(float).tolist(), quality)))
            except BiometricError:
                guidance.append("Hold still and keep your face visible.")
        if len(accepted) < 3:
            raise BiometricError(next(iter(dict.fromkeys(guidance)), "Hold still, improve lighting, and try again."))
        accepted.sort(key=lambda item: item[1].quality, reverse=True)
        # Averaging several normalized ArcFace vectors resists a single bad frame.
        embedding = average_vectors([face.embedding for _, face in accepted])
        return BatchResult(embedding, accepted[0][0], len(accepted), len(images) - len(accepted), list(dict.fromkeys(guidance))[:3])


_engine = None
def get_engine() -> BiometricEngine:
    global _engine
    if _engine is None:
        try:
            _engine = BiometricEngine()
        except Exception as exc:
            logger.exception("InsightFace failed to initialise: %s", exc)
            raise RuntimeError("Biometric service unavailable") from exc
    return _engine

def cosine_similarity(left: Iterable[float], right: Iterable[float]) -> float:
    a, b = np.asarray(list(left), dtype=np.float32), np.asarray(list(right), dtype=np.float32)
    if a.size != 512 or b.size != 512:
        raise BiometricError("Face embedding dimensions are invalid.")
    return float(np.dot(a, b) / max(np.linalg.norm(a) * np.linalg.norm(b), 1e-10))

def average_embeddings(vectors: list[list[float]]) -> list[float]:
    if not 3 <= len(vectors) <= 5:
        raise BiometricError("Capture between three and five face images.")
    return average_vectors(vectors)

def average_vectors(vectors: list[list[float]]) -> list[float]:
    matrix = np.asarray(vectors, dtype=np.float32)
    if matrix.shape[1] != 512:
        raise BiometricError("Face embedding dimensions are invalid.")
    mean = matrix.mean(axis=0)
    return (mean / np.linalg.norm(mean)).astype(float).tolist()
