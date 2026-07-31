"""Download and extract InsightFace model during build phase.

This script must be run during Render build to pre-download models
so they are available at runtime without triggering downloads.
"""
import os
import sys
import zipfile
import urllib.request
import shutil

BASE_REPO_URL = "https://github.com/deepinsight/insightface/releases/download/v0.7"
MODEL_NAME = "buffalo_s"
MODELS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models")


def download_and_extract(name: str, dest_dir: str) -> None:
    """Download model zip and extract into dest_dir."""
    model_dir = os.path.join(dest_dir, name)
    if os.path.isdir(model_dir) and os.listdir(model_dir):
        print(f"[setup_models] Model '{name}' already exists at {model_dir}")
        return

    os.makedirs(dest_dir, exist_ok=True)
    zip_path = os.path.join(dest_dir, f"{name}.zip")
    url = f"{BASE_REPO_URL}/{name}.zip"

    print(f"[setup_models] Downloading {url} ...")
    urllib.request.urlretrieve(url, zip_path)
    print(f"[setup_models] Downloaded to {zip_path}")

    os.makedirs(model_dir, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(model_dir)
    print(f"[setup_models] Extracted to {model_dir}")

    os.remove(zip_path)
    print(f"[setup_models] Removed zip file {zip_path}")

    files = os.listdir(model_dir)
    print(f"[setup_models] Model files: {files}")


def main() -> None:
    print(f"[setup_models] Model directory: {MODELS_DIR}")
    download_and_extract(MODEL_NAME, MODELS_DIR)
    print("[setup_models] Done.")


if __name__ == "__main__":
    main()

