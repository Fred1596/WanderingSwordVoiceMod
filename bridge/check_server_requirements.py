#!/usr/bin/env python3
"""Check server packages, model data, manifests, and optional GPU access."""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
import platform
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REQUIRED_IMPORTS = (
    ("torch", "torch"),
    ("torchaudio", "torchaudio"),
    ("numpy", "numpy"),
    ("soundfile", "soundfile"),
    ("qwen_tts", "qwen-tts"),
    ("transformers", "transformers"),
    ("accelerate", "accelerate"),
)
MODEL_DIRS = (
    "Qwen3-TTS-12Hz-1.7B-VoiceDesign",
    "Qwen3-TTS-12Hz-0.6B-Base",
)


def directory_bytes(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-gpu", action="store_true")
    args = parser.parse_args()

    print(f"Python: {sys.version.split()[0]} ({sys.executable})")
    print(f"Platform: {platform.platform()}")
    missing: list[str] = []
    for module_name, distribution_name in REQUIRED_IMPORTS:
        try:
            importlib.import_module(module_name)
            try:
                version = importlib.metadata.version(distribution_name)
            except importlib.metadata.PackageNotFoundError:
                version = "importable"
            print(f"[OK] {module_name}: {version}")
        except Exception as exc:
            missing.append(module_name)
            print(f"[MISSING] {module_name}: {type(exc).__name__}: {exc}")

    gpu_ok = False
    torch_version = ""
    torch_cuda_tag = ""
    if "torch" not in missing:
        import torch

        torch_version = torch.__version__.split("+", 1)[0]
        torch_cuda_tag = (
            "cu" + torch.version.cuda.replace(".", "") if torch.version.cuda else "cpu"
        )
        print(f"Torch build: {torch.__version__}")
        print(f"Torch CUDA runtime: {torch.version.cuda}")
        gpu_ok = torch.cuda.is_available()
        print(f"CUDA available: {gpu_ok}")
        if gpu_ok:
            print(f"GPU: {torch.cuda.get_device_name(0)}")
            total = torch.cuda.get_device_properties(0).total_memory / 1024**3
            print(f"GPU memory: {total:.2f} GiB")
            print(f"BF16 supported: {torch.cuda.is_bf16_supported()}")

    data_errors: list[str] = []
    for name in MODEL_DIRS:
        path = PROJECT_ROOT / "models" / name
        if path.is_dir() and (path / "config.json").is_file():
            print(f"[OK] model {name}: {directory_bytes(path) / 1024**3:.2f} GiB")
        else:
            data_errors.append(str(path))
            print(f"[MISSING] model: {path}")

    anchor_plan = PROJECT_ROOT / "offline" / "manifest" / "voice_anchors.json"
    jobs_path = PROJECT_ROOT / "offline" / "manifest" / "dialogue_jobs.jsonl"
    if anchor_plan.is_file():
        anchors = json.loads(anchor_plan.read_text(encoding="utf-8"))
        print(f"[OK] voice anchor plan: {len(anchors)} groups")
    else:
        data_errors.append(str(anchor_plan))
    if jobs_path.is_file():
        with jobs_path.open("r", encoding="utf-8") as handle:
            jobs = sum(bool(line.strip()) for line in handle)
        print(f"[OK] dialogue jobs: {jobs} lines")
    else:
        data_errors.append(str(jobs_path))

    print("\nSuggested install commands if packages are missing:")
    if torch_version and "torchaudio" in missing and torch_cuda_tag != "cpu":
        print(
            f"{sys.executable} -m pip install torchaudio=={torch_version} "
            f"--index-url https://download.pytorch.org/whl/{torch_cuda_tag}"
        )
    elif "torch" in missing:
        print(
            f"{sys.executable} -m pip install torch==2.10.0 torchaudio==2.10.0 "
            "--index-url https://download.pytorch.org/whl/cu130"
        )
    print(
        f"{sys.executable} -m pip install qwen-tts==0.1.1 "
        "transformers==4.57.3 accelerate==1.12.0 soundfile==0.14.0"
    )

    if missing or data_errors or (args.require_gpu and not gpu_ok):
        print("\nRESULT: NOT READY")
        return 1
    print("\nRESULT: READY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
