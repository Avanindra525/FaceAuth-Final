"""Validate bundled InsightFace models.

This project must deploy with models already present in the repository.
The script intentionally performs no network access and never downloads files.
"""
from pathlib import Path
import sys


MODEL_NAME = "buffalo_s"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODELS_DIR = PROJECT_ROOT / "models"
REQUIRED_FILES = {"det_500m.onnx", "w600k_mbf.onnx"}


def main() -> int:
    model_dir = MODELS_DIR / MODEL_NAME
    print(f"[setup_models] Project root: {PROJECT_ROOT}")
    print(f"[setup_models] Models directory: {MODELS_DIR}")
    print(f"[setup_models] Model directory: {model_dir}")

    if not model_dir.is_dir():
        print(f"[setup_models] Missing model directory: {model_dir}", file=sys.stderr)
        return 1

    files = {path.name for path in model_dir.iterdir() if path.is_file()}
    missing = sorted(REQUIRED_FILES - files)
    if missing:
        print(f"[setup_models] Missing required model files: {missing}", file=sys.stderr)
        return 1

    print(f"[setup_models] Local model bundle is valid: {MODEL_NAME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
