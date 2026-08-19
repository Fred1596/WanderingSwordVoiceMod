#!/usr/bin/env python3
"""Create reusable, fully synthetic character voice anchors with Qwen VoiceDesign."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "voice_profiles.json"


def resolve_project_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def main(args: argparse.Namespace) -> int:
    with args.config.open("r", encoding="utf-8") as stream:
        config: dict[str, Any] = json.load(stream)

    selected = set(args.profiles or config["profiles"].keys())
    unknown = selected.difference(config["profiles"])
    if unknown:
        raise ValueError(f"未知声线：{', '.join(sorted(unknown))}")

    pending: list[tuple[str, dict[str, Any], Path]] = []
    for name, profile in config["profiles"].items():
        if name not in selected:
            continue
        output = resolve_project_path(profile["anchor_file"])
        if args.force or not output.exists():
            pending.append((name, profile, output))
        else:
            print(f"[跳过] 已存在：{output}")
    if not pending:
        print("所有选定声线样本都已存在。")
        return 0

    model_cfg = config["design_model"]
    model_path = resolve_project_path(model_cfg["path"])
    if not model_path.exists():
        raise FileNotFoundError(
            f"没有找到 VoiceDesign 模型：{model_path}\n"
            "请先运行 scripts\\download_models.ps1，或按 README 手动下载。"
        )

    try:
        import soundfile as sf
        import torch
        from qwen_tts import Qwen3TTSModel
    except ImportError as exc:
        raise RuntimeError("缺少配音环境。请先运行 scripts\\setup_runtime.ps1。") from exc

    dtype = getattr(torch, model_cfg.get("dtype", "bfloat16"))
    print("[注意] 1.7B 声线设计较占显存；RTX 4060 8GB 上请先退出游戏。")
    print(f"[模型] 正在加载 {model_path.name}……")
    model = Qwen3TTSModel.from_pretrained(
        str(model_path),
        device_map=model_cfg.get("device", "cuda:0"),
        dtype=dtype,
        attn_implementation=model_cfg.get("attention", "sdpa"),
    )

    manifest_path = PROJECT_ROOT / "voices" / "anchors" / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.setdefault("voices", {})
    else:
        manifest = {"voices": {}}
    manifest["model"] = model_path.name
    for name, profile, output in pending:
        # A stable per-profile seed makes reruns reproducible while allowing
        # otherwise similar archetypes to land on different synthetic timbres.
        seed = int(hashlib.sha256(name.encode("utf-8")).hexdigest()[:8], 16)
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
        output.parent.mkdir(parents=True, exist_ok=True)
        print(f"[设计] {name} -> {output.name}")
        wavs, sample_rate = model.generate_voice_design(
            text=profile["anchor_text"],
            language=model_cfg.get("language", "Chinese"),
            instruct=profile["voice_design"],
        )
        temporary = output.with_suffix(".tmp.wav")
        sf.write(str(temporary), wavs[0], sample_rate, subtype="PCM_16")
        temporary.replace(output)
        manifest["voices"][name] = {
            "file": str(output.relative_to(PROJECT_ROOT)),
            "seed": seed,
            "sample_rate": sample_rate,
        }

    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[完成] 已生成 {len(pending)} 个声线样本：{manifest_path.parent}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="生成逸剑风云决人物专属 AI 声线样本")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--profiles", nargs="*", help="只生成指定 profile；默认全部")
    parser.add_argument("--force", action="store_true", help="覆盖已存在的声线样本")
    return parser


if __name__ == "__main__":
    try:
        raise SystemExit(main(build_parser().parse_args()))
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        raise SystemExit(1)
