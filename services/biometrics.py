"""Real face embedding and quality checks.

No biometric fallback is provided: if InsightFace cannot initialize,
the API returns a controlled service error rather than authenticating a person.
Model is always loaded from local disk — never downloaded at runtime.
"""
import base64
import concurrent.futures
import contextlib
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Iterable

import cv2
import numpy as np

try:
    import insightface.app.face_analysis as _face_analysis
    from insightface.app import FaceAnalysis
except ImportError as exc:  # surfaced by get_engine()
    _face_analysis = None
    FaceAnalysis = None

logger = logging.getLogger("biometrics")
logger.setLevel(logging.INFO)

try:
    cv2.setNumThreads(1)
except Exception:
    logger.debug("OpenCV thread limit could not be set.", exc_info=True)

# Path to bundled models checked into the repo.
# __file__ is at {PROJECT_ROOT}/services/biometrics.py
# FaceAnalysis(root=PROJECT_ROOT) internally resolves {root}/models/{name}
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELS_DIR = os.path.join(PROJECT_ROOT, "models")
_MODELS_DIR = MODELS_DIR
_ALLOWED_MODULES = ["detection", "recognition"]
_REQUIRED_ONNX = ("det_500m.onnx", "w600k_mbf.onnx")
_DEFAULT_TIMEOUT_SECONDS = 10.0
_engine_init_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="biometric-init")
_inference_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="biometric-infer")


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def _model_available(name: str = "buffalo_s") -> bool:
    """Return True when the local model directory has required ONNX files."""
    return not diagnose_models(name).get("missing")


def diagnose_models(name: str = "buffalo_s") -> dict:
    """Return a readable diagnostic dict for the bundled model directory.

    Used for startup logging and the /health endpoint. Never downloads.
    """
    model_dir = os.path.join(MODELS_DIR, name)
    info = {
        "project_root": PROJECT_ROOT,
        "models_dir": MODELS_DIR,
        "model_name": name,
        "model_dir": model_dir,
        "detected": [],
        "missing": [],
        "available": False,
    }
    if not os.path.isdir(model_dir):
        logger.warning("Model directory not found: %s", model_dir)
        info["missing"] = list(_REQUIRED_ONNX)
        return info
    files = set(os.listdir(model_dir))
    info["detected"] = sorted(files)
    missing = sorted(set(_REQUIRED_ONNX) - files)
    info["missing"] = missing
    info["available"] = not missing
    if missing:
        logger.warning(
            "Model directory %s is missing required files: %s (detected: %s)",
            model_dir, missing, info["detected"],
        )
    return info


def _local_model_dir(name: str) -> str:
    """Return the bundled model directory without downloading anything."""
    model_dir = os.path.join(MODELS_DIR, name)
    if not _model_available(name):
        diag = diagnose_models(name)
        raise RuntimeError(
            f"Face recognition model '{name}' is missing from bundled models at {diag['model_dir']}. "
            f"Missing files: {diag['missing']}. Detected files: {diag['detected']}. "
            "Bundle the two required ONNX files before deployment."
        )
    return model_dir


def _disable_insightface_downloads(model_name: str) -> None:
    """Force FaceAnalysis to resolve only the repo-local model directory."""
    if _face_analysis is None:
        return

    def local_ensure_available(sub_dir, name, root=None, download_zip=False):
        if sub_dir != "models" or name != model_name:
            raise RuntimeError(f"Unexpected InsightFace model request: {sub_dir}/{name}")
        return _local_model_dir(name)

    _face_analysis.ensure_available = local_ensure_available


@contextlib.contextmanager
def _only_required_onnx_files(model_name: str):
    """Make FaceAnalysis scan only detection and recognition models."""
    if _face_analysis is None:
        yield
        return

    model_dir = _local_model_dir(model_name)
    expected_pattern = os.path.normcase(os.path.normpath(os.path.join(model_dir, "*.onnx")))
    selected_files = [os.path.join(model_dir, filename) for filename in _REQUIRED_ONNX]
    original_glob = _face_analysis.glob.glob

    def local_glob(pattern):
        normalized = os.path.normcase(os.path.normpath(pattern))
        if normalized == expected_pattern:
            logger.info("Restricting InsightFace ONNX scan to: %s", selected_files)
            return selected_files
        return original_glob(pattern)

    _face_analysis.glob.glob = local_glob
    try:
        yield
    finally:
        _face_analysis.glob.glob = original_glob


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
        model_dir = os.path.join(MODELS_DIR, model_name)
        use_gpu = os.getenv("USE_GPU", "false").lower() == "true"
        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"] if use_gpu else ["CPUExecutionProvider"]

        # Log everything before attempting initialization
        diag = diagnose_models(model_name)
        logger.info("PROJECT_ROOT=%s", PROJECT_ROOT)
        logger.info("MODELS_DIR=%s", MODELS_DIR)
        logger.info("model_name=%s", model_name)
        logger.info("model_dir=%s", model_dir)
        logger.info("model_available=%s", diag["available"])
        logger.info("detected_onnx_files=%s", diag["detected"])
        logger.info("missing_onnx_files=%s", diag["missing"])
        logger.info("providers=%s", providers)
        logger.info("allowed_modules=%s", _ALLOWED_MODULES)

        # Never trigger a download — check local model bundle first
        if not diag["available"]:
            raise RuntimeError(
                f"Face recognition model '{model_name}' not installed at {diag['model_dir']}. "
                f"Missing files: {diag['missing']}. Detected files: {diag['detected']}. "
                "Bundle the model files before deployment."
            )
        _disable_insightface_downloads(model_name)

        started = time.perf_counter()
        logger.info("FaceAnalysis constructor started.")
        with _only_required_onnx_files(model_name):
            self.recognizer = FaceAnalysis(
                name=model_name,
                root=PROJECT_ROOT,
                providers=providers,
                allowed_modules=_ALLOWED_MODULES,
            )
        logger.info("FaceAnalysis constructor completed in %sms.", _elapsed_ms(started))
        logger.info("recognizer.prepare started.")
        self.recognizer.prepare(ctx_id=-1 if not use_gpu else 0, det_size=(320, 320))
        elapsed = _elapsed_ms(started)
        logger.info("InsightFace initialized successfully in %sms (%s/%s)", elapsed, MODELS_DIR, model_name)

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

    def extract(self, image_data: str, timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS) -> FaceResult:
        started = time.perf_counter()
        logger.info("decode image started.")
        frame = self.decode(image_data)
        logger.info("decode image completed in %sms.", _elapsed_ms(started))

        remaining = max(0.001, timeout_seconds - (time.perf_counter() - started))
        logger.info("recognizer.get started with %.3fs remaining.", remaining)
        future = _inference_executor.submit(self.recognizer.get, frame)
        try:
            faces = future.result(timeout=remaining)
        except concurrent.futures.TimeoutError as exc:
            logger.error("recognizer.get exceeded %.3fs; returning timeout.", remaining)
            raise TimeoutError("Biometric verification timeout.") from exc
        logger.info("recognizer.get completed in %sms.", _elapsed_ms(started))

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
_engine_lock = threading.Lock()
_engine_init_error = None
_engine_init_future = None


def get_engine(timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS) -> BiometricEngine:
    global _engine, _engine_init_error, _engine_init_future
    started = time.perf_counter()

    if _engine is not None:
        logger.info("Biometric engine reused in %sms.", _elapsed_ms(started))
        return _engine

    if _engine_init_error is not None:
        logger.error("Biometric engine unavailable from cached initialization error: %s", _engine_init_error)
        raise RuntimeError("Biometric service unavailable") from _engine_init_error

    with _engine_lock:
        if _engine is not None:
            logger.info("Biometric engine reused in %sms.", _elapsed_ms(started))
            return _engine
        if _engine_init_error is not None:
            raise RuntimeError("Biometric service unavailable") from _engine_init_error
        if _engine_init_future is None:
            logger.info("Starting lazy InsightFace initialization.")
            _engine_init_future = _engine_init_executor.submit(BiometricEngine)
        else:
            logger.info("Biometric engine initialization already in progress.")

    remaining = max(0.001, timeout_seconds - (time.perf_counter() - started))
    try:
        engine = _engine_init_future.result(timeout=remaining)
    except concurrent.futures.TimeoutError as exc:
        logger.error("Biometric engine initialization exceeded %.3fs.", remaining)
        raise TimeoutError("Biometric verification timeout.") from exc
    except Exception as exc:
        with _engine_lock:
            _engine_init_error = exc
        logger.exception("InsightFace failed to initialize: %s", exc)
        raise RuntimeError("Biometric service unavailable") from exc

    with _engine_lock:
        _engine = engine
    logger.info("Biometric engine initialized in %sms.", _elapsed_ms(started))
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
