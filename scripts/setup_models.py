"""Validate bundled InsightFace models.

This project must deploy with models already present in the repository.
The script intentionally performs no network access and never downloads files.

It validates that the required ``buffalo_s`` ONNX files exist in the models
directory. When the files are present it returns 0; otherwise it prints a
readable diagnostic and returns 1 so deployment fails fast.

The two required files are small enough to be committed to git:
  - det_500m.onnx   (detection, ~2.5 MB)
  - w600k_mbf.onnx  (recognition, ~13 MB)

The larger auxiliary models (landmark / gender-age) are NOT required and are
excluded from the repository to keep the bundle small.
"""
from pathlib import Path
import os
import sys


MODEL_NAME = os.getenv("INSIGHTFACE_MODEL", "buffalo_s")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODELS_DIR = Path(os.getenv("MODELS_DIR", PROJECT_ROOT / "models"))
REQUIRED_FILES = {
    "det_500m.onnx": "face detection",
    "w600k_mbf.onnx": "face recognition",
}


def main() -> int:
    model_dir = MODELS_DIR / MODEL_NAME
    print(f"[setup_models] Project root: {PROJECT_ROOT}")
    print(f"[setup_models] Models directory: {MODELS_DIR}")
    print(f"[setup_models] Model name: {MODEL_NAME}")
    print(f"[setup_models] Model directory: {model_dir}")

    if not model_dir.is_dir():
        print(f"[setup_models] ERROR: Missing model directory: {model_dir}", file=sys.stderr)
        print(f"[setup_models] ERROR: Bundle the '{MODEL_NAME}' model directory (or the two required ONNX files) before deploying.", file=sys.stderr)
        return 1

    files = {path.name for path in model_dir.iterdir() if path.is_file()}
    missing = {name: purpose for name, purpose in REQUIRED_FILES.items() if name not in files}
    if missing:
        for name, purpose in missing.items():
            print(f"[setup_models] ERROR: Missing {name} ({purpose}) in {model_dir}", file=sys.stderr)
        for name in sorted(REQUIRED_FILES):
            size = model_dir.joinpath(name).stat().st_size if (model_dir / name).is_file() else None
            present = "OK" if size is not None else "MISSING"
            print(f"[setup_models]   {name}: {present}{f' ({size:,} bytes)' if size is not None else ''}")
        return 1

    print(f"[setup_models] Local model bundle is valid: {MODEL_NAME}")
    for name in sorted(REQUIRED_FILES):
        size = (model_dir / name).stat().st_size
        print(f"[setup_models]   {name}: OK ({size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
