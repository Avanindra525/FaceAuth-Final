"""Real face embedding, quality and liveness checks.

No biometric fallback is provided: if InsightFace or MediaPipe cannot initialize,
the API returns a controlled service error rather than authenticating a person.
"""
import base64
import math
import os
import random
import time
from dataclasses import dataclass
from typing import Iterable

import cv2
import numpy as np

try:
    from insightface.app import FaceAnalysis
    import mediapipe as mp
except ImportError as exc:  # surfaced by get_engine()
    FaceAnalysis = None
    mp = None


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
        if FaceAnalysis is None or mp is None:
            raise RuntimeError("InsightFace and MediaPipe must be installed before biometric authentication is enabled.")
        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"] if os.getenv("USE_GPU", "false").lower() == "true" else ["CPUExecutionProvider"]
        self.recognizer = FaceAnalysis(name=os.getenv("INSIGHTFACE_MODEL", "buffalo_l"), providers=providers)
        self.recognizer.prepare(ctx_id=0 if os.getenv("USE_GPU", "false").lower() == "true" else -1, det_size=(640, 640))
        self.mesh = mp.solutions.face_mesh.FaceMesh(static_image_mode=False, max_num_faces=2, refine_landmarks=True,
                                                     min_detection_confidence=0.6, min_tracking_confidence=0.6)

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
        if confidence < 0.75:
            raise BiometricError("Face confidence is too low. Improve lighting and try again.")
        quality = self._quality(frame, face.bbox)
        if quality < 0.20:
            raise BiometricError("Image quality is too low. Use better lighting and keep still.")
        return FaceResult(
            embedding=face.normed_embedding.astype(float).tolist(), confidence=confidence,
            box=[int(v) for v in face.bbox.tolist()], landmarks=face.kps.astype(float).tolist(), quality=quality
        )

    def extract_batch(self, images: list[str]) -> BatchResult:
        """Use a short camera burst, retaining only well-framed live-quality faces."""
        if not 5 <= len(images) <= 10:
            raise BiometricError("Capture between five and ten camera frames.")
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

    def liveness(self, image_data: str, challenge: str, observations: dict | None = None) -> dict:
        """Evaluate live FaceMesh geometry and a client-observed random challenge.

        A production deployment should additionally plug in a trained PAD model;
        texture checks are intentionally a supplemental signal, not a claim of
        universal printed-screen/deepfake detection.
        """
        frame = self.decode(image_data)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        result = self.mesh.process(rgb)
        if not result.multi_face_landmarks or len(result.multi_face_landmarks) != 1:
            return {"passed": False, "reason": "Exactly one live face mesh is required.", "score": 0.0}
        points = result.multi_face_landmarks[0].landmark
        # Eye aspect ratio for both eyes (MediaPipe refined iris/eye landmarks).
        def distance(a, b): return math.hypot(points[a].x - points[b].x, points[a].y - points[b].y)
        left_ear = (distance(159, 145) + distance(158, 153)) / max(2 * distance(33, 133), 0.001)
        right_ear = (distance(386, 374) + distance(385, 380)) / max(2 * distance(362, 263), 0.001)
        ear = (left_ear + right_ear) / 2
        nose, left_eye, right_eye = points[1], points[33], points[263]
        yaw = (nose.x - ((left_eye.x + right_eye.x) / 2)) / max(abs(right_eye.x - left_eye.x), 0.001)
        pitch = nose.y - ((points[10].y + points[152].y) / 2)
        observed = observations or {}
        completed = bool(observed.get("completed"))
        mouth_open = distance(13, 14) / max(distance(61, 291), 0.001)
        valid_motion = {"blink": ear < 0.20, "turn-left": yaw < -0.08, "turn-right": yaw > 0.08,
                        "smile": mouth_open > 0.10, "nod": abs(pitch) > 0.035}.get(challenge, False)
        # Laplacian texture is only a weak spoof-risk indicator.
        texture = cv2.Laplacian(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()
        passed = completed and valid_motion and texture > 18
        return {"passed": passed, "score": round(min(1.0, texture / 120.0), 3), "ear": round(ear, 3),
                "yaw": round(yaw, 3), "reason": None if passed else "Complete the requested live movement in good lighting."}


_engine = None
def get_engine() -> BiometricEngine:
    global _engine
    if _engine is None:
        _engine = BiometricEngine()
    return _engine

def cosine_similarity(left: Iterable[float], right: Iterable[float]) -> float:
    a, b = np.asarray(list(left), dtype=np.float32), np.asarray(list(right), dtype=np.float32)
    if a.size != 512 or b.size != 512:
        raise BiometricError("Face embedding dimensions are invalid.")
    return float(np.dot(a, b) / max(np.linalg.norm(a) * np.linalg.norm(b), 1e-10))

def average_embeddings(vectors: list[list[float]]) -> list[float]:
    if not 3 <= len(vectors) <= 5:
        raise BiometricError("Capture between three and five live face images.")
    return average_vectors(vectors)

def average_vectors(vectors: list[list[float]]) -> list[float]:
    matrix = np.asarray(vectors, dtype=np.float32)
    if matrix.shape[1] != 512:
        raise BiometricError("Face embedding dimensions are invalid.")
    mean = matrix.mean(axis=0)
    return (mean / np.linalg.norm(mean)).astype(float).tolist()

def random_challenge() -> str:
    return random.choice(["blink", "turn-left", "turn-right", "smile", "nod"])
