#!/usr/bin/env python3
"""Wandering Sword native dialogue -> local TTS bridge.

The UE4SS mod writes one UTF-8 JSON object per dialogue line. This process tails
that file, routes the speaker to a voice profile, generates/cache a WAV, and
plays it. Qwen dependencies are imported lazily so --dry-run works on a clean
Python installation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import re
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
GAME_ROOT = PROJECT_ROOT.parent
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "voice_profiles.json"
DEFAULT_EVENTS = (
    GAME_ROOT
    / "Wandering_Sword"
    / "Binaries"
    / "Win64"
    / "ue4ss"
    / "Mods"
    / "WanderingSwordVoiceProbe"
    / "dialogue_events.jsonl"
)


@dataclass(frozen=True)
class DialogueEvent:
    speaker: str
    text: str
    timestamp: str = ""


class VoiceRouter:
    def __init__(self, config: dict[str, Any]) -> None:
        self.default_profile = config["default_profile"]
        self.overrides = config.get("speaker_overrides", {})
        self.profiles = config["profiles"]
        self.rules: list[tuple[re.Pattern[str], str]] = []
        for rule in config.get("speaker_rules", []):
            self.rules.append((re.compile(rule["pattern"]), rule["profile"]))
        self._validate()

    def _validate(self) -> None:
        referenced = {self.default_profile, *self.overrides.values()}
        referenced.update(profile for _, profile in self.rules)
        missing = sorted(referenced.difference(self.profiles))
        if missing:
            raise ValueError(f"配置引用了不存在的声线：{', '.join(missing)}")

    def route(self, speaker: str) -> tuple[str, dict[str, Any]]:
        profile_name = self.overrides.get(speaker)
        if profile_name is None:
            for pattern, candidate in self.rules:
                if pattern.search(speaker):
                    profile_name = candidate
                    break
        profile_name = profile_name or self.default_profile
        return profile_name, self.profiles[profile_name]


class QwenCustomVoiceBackend:
    def __init__(self, config: dict[str, Any], cache_dir: Path) -> None:
        model_cfg = config["model"]
        raw_model_path = Path(model_cfg["path"])
        self.model_path = (
            raw_model_path
            if raw_model_path.is_absolute()
            else PROJECT_ROOT / raw_model_path
        )
        if not self.model_path.exists():
            raise FileNotFoundError(
                "没有找到 Qwen3-TTS 模型目录：\n"
                f"  {self.model_path}\n"
                "请先运行 scripts\\download_live_model.ps1，或把手动下载的模型放到该目录。"
            )

        try:
            import torch
            import soundfile as sf
            from qwen_tts import Qwen3TTSModel
        except ImportError as exc:
            raise RuntimeError(
                "缺少配音环境。请先运行 scripts\\setup_runtime.ps1。"
            ) from exc

        dtype_name = model_cfg.get("dtype", "bfloat16")
        try:
            dtype = getattr(torch, dtype_name)
        except AttributeError as exc:
            raise ValueError(f"不支持的 torch dtype：{dtype_name}") from exc

        print(f"[模型] 正在加载 {self.model_path.name}，首次需要一点时间……")
        self.model = Qwen3TTSModel.from_pretrained(
            str(self.model_path),
            device_map=model_cfg.get("device", "cuda:0"),
            dtype=dtype,
            attn_implementation=model_cfg.get("attention", "sdpa"),
        )
        self.sf = sf
        self.language = model_cfg.get("language", "Chinese")
        self.use_instruct = bool(model_cfg.get("use_instruct", False))
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        print("[模型] 加载完成。")

    def synthesize(
        self, event: DialogueEvent, profile_name: str, profile: dict[str, Any]
    ) -> Path:
        cache_key = json.dumps(
            {
                "backend": "qwen_custom_voice",
                "model": self.model_path.name,
                "speaker": profile["qwen_speaker"],
                "instruct": profile.get("instruct", "") if self.use_instruct else "",
                "text": event.text,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
        digest = hashlib.sha256(cache_key).hexdigest()
        output_path = self.cache_dir / f"{digest}.wav"
        if output_path.exists():
            print(f"[缓存] {event.speaker}：{event.text}")
            return output_path

        kwargs: dict[str, Any] = {
            "text": event.text,
            "language": self.language,
            "speaker": profile["qwen_speaker"],
        }
        if self.use_instruct and profile.get("instruct"):
            kwargs["instruct"] = profile["instruct"]

        print(f"[合成] {event.speaker} -> {profile_name}：{event.text}")
        wavs, sample_rate = self.model.generate_custom_voice(**kwargs)
        temporary = output_path.with_suffix(".tmp.wav")
        self.sf.write(str(temporary), wavs[0], sample_rate, subtype="PCM_16")
        os.replace(temporary, output_path)
        return output_path


class QwenVoiceCloneBackend:
    """Live backend: clone stable, synthetic character anchors with the 0.6B model."""

    def __init__(self, config: dict[str, Any], cache_dir: Path) -> None:
        model_cfg = config["model"]
        raw_model_path = Path(model_cfg["path"])
        self.model_path = (
            raw_model_path
            if raw_model_path.is_absolute()
            else PROJECT_ROOT / raw_model_path
        )
        if not self.model_path.exists():
            raise FileNotFoundError(
                "没有找到实时克隆模型目录：\n"
                f"  {self.model_path}\n"
                "请运行 scripts\\download_models.ps1，或按 README 手动放入模型。"
            )
        try:
            import torch
            import soundfile as sf
            from qwen_tts import Qwen3TTSModel
        except ImportError as exc:
            raise RuntimeError(
                "缺少配音环境。请先运行 scripts\\setup_runtime.ps1。"
            ) from exc

        dtype_name = model_cfg.get("dtype", "bfloat16")
        try:
            dtype = getattr(torch, dtype_name)
        except AttributeError as exc:
            raise ValueError(f"不支持的 torch dtype：{dtype_name}") from exc

        print(f"[模型] 正在加载实时模型 {self.model_path.name}……")
        self.model = Qwen3TTSModel.from_pretrained(
            str(self.model_path),
            device_map=model_cfg.get("device", "cuda:0"),
            dtype=dtype,
            attn_implementation=model_cfg.get("attention", "sdpa"),
        )
        self.sf = sf
        self.language = model_cfg.get("language", "Chinese")
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.prompts: dict[str, Any] = {}
        self.anchor_digests: dict[str, str] = {}
        print("[模型] 实时模型加载完成。")

    @staticmethod
    def _resolve_anchor(profile: dict[str, Any]) -> Path:
        raw_path = Path(profile["anchor_file"])
        return raw_path if raw_path.is_absolute() else PROJECT_ROOT / raw_path

    def _prompt_for(self, profile_name: str, profile: dict[str, Any]) -> Any:
        if profile_name in self.prompts:
            return self.prompts[profile_name]
        anchor = self._resolve_anchor(profile)
        if not anchor.exists():
            raise FileNotFoundError(
                f"人物声线样本尚未生成：{anchor}\n"
                "请关闭游戏后运行 scripts\\design_voice_anchors.ps1。"
            )
        anchor_bytes = anchor.read_bytes()
        self.anchor_digests[profile_name] = hashlib.sha256(anchor_bytes).hexdigest()
        print(f"[声线] 载入 {profile_name}：{anchor.name}")
        prompt = self.model.create_voice_clone_prompt(
            ref_audio=str(anchor),
            ref_text=profile["anchor_text"],
            x_vector_only_mode=False,
        )
        self.prompts[profile_name] = prompt
        return prompt

    def synthesize(
        self, event: DialogueEvent, profile_name: str, profile: dict[str, Any]
    ) -> Path:
        prompt = self._prompt_for(profile_name, profile)
        cache_key = json.dumps(
            {
                "backend": "qwen_voice_clone",
                "model": self.model_path.name,
                "anchor": self.anchor_digests[profile_name],
                "text": event.text,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
        digest = hashlib.sha256(cache_key).hexdigest()
        output_path = self.cache_dir / f"{digest}.wav"
        if output_path.exists():
            print(f"[缓存] {event.speaker}：{event.text}")
            return output_path

        print(f"[合成] {event.speaker} -> {profile_name}：{event.text}")
        wavs, sample_rate = self.model.generate_voice_clone(
            text=event.text,
            language=self.language,
            voice_clone_prompt=prompt,
        )
        temporary = output_path.with_suffix(".tmp.wav")
        self.sf.write(str(temporary), wavs[0], sample_rate, subtype="PCM_16")
        os.replace(temporary, output_path)
        return output_path


class DryRunBackend:
    def synthesize(
        self, event: DialogueEvent, profile_name: str, profile: dict[str, Any]
    ) -> None:
        print(
            f"[测试] {event.speaker} -> {profile_name} / "
            f"{Path(profile['anchor_file']).name}：{event.text}"
        )
        return None


def stop_audio() -> None:
    if sys.platform == "win32":
        import winsound

        winsound.PlaySound(None, 0)


def play_audio(path: Path | None) -> None:
    if path is None:
        return
    if sys.platform != "win32":
        raise RuntimeError("当前播放实现仅支持 Windows。")
    import winsound

    winsound.PlaySound(
        str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT
    )


def parse_event(line: str) -> DialogueEvent | None:
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        print(f"[跳过] 无效 JSON：{line[:100]}", file=sys.stderr)
        return None
    speaker = str(value.get("speaker", "")).strip()
    text = str(value.get("text", "")).strip()
    if not speaker or not text:
        return None
    return DialogueEvent(speaker=speaker, text=text, timestamp=str(value.get("time", "")))


def tail_events(
    path: Path,
    output: queue.Queue[DialogueEvent | None],
    replay_existing: bool,
    stop: threading.Event,
) -> None:
    while not path.exists() and not stop.is_set():
        print(f"[等待] 对话事件文件尚未出现：{path}")
        stop.wait(2.0)
    if stop.is_set():
        return

    with path.open("r", encoding="utf-8") as stream:
        if not replay_existing:
            stream.seek(0, os.SEEK_END)
        while not stop.is_set():
            position = stream.tell()
            line = stream.readline()
            if not line:
                # UE4SS can recreate the file after a game restart.
                try:
                    if path.stat().st_size < position:
                        stream.seek(0)
                    else:
                        stop.wait(0.08)
                except FileNotFoundError:
                    stop.wait(0.25)
                continue
            event = parse_event(line)
            if event is not None:
                output.put(event)


def drain_to_latest(
    events: queue.Queue[DialogueEvent | None], current: DialogueEvent
) -> DialogueEvent:
    latest = current
    while True:
        try:
            candidate = events.get_nowait()
        except queue.Empty:
            return latest
        if candidate is not None:
            latest = candidate


def run(args: argparse.Namespace) -> int:
    with args.config.open("r", encoding="utf-8") as stream:
        config = json.load(stream)
    router = VoiceRouter(config)

    cache_dir = PROJECT_ROOT / "cache" / "audio"
    backend: Any
    if args.dry_run:
        backend = DryRunBackend()
    else:
        backend_name = config["model"].get("backend")
        if backend_name == "qwen_voice_clone":
            backend = QwenVoiceCloneBackend(config, cache_dir)
        elif backend_name == "qwen_custom_voice":
            backend = QwenCustomVoiceBackend(config, cache_dir)
        else:
            raise ValueError(f"不支持的配音后端：{backend_name}")

    events: queue.Queue[DialogueEvent | None] = queue.Queue()
    stop = threading.Event()
    watcher = threading.Thread(
        target=tail_events,
        args=(args.events, events, args.replay_existing, stop),
        daemon=True,
    )
    watcher.start()

    print(f"[监听] {args.events}")
    print("[提示] 保持此窗口运行；进入游戏后，出现新台词便会自动处理。")
    handled = 0
    try:
        while True:
            event = events.get()
            if event is None:
                break
            if config.get("playback", {}).get("interrupt_on_new_line", True):
                stop_audio()
                event = drain_to_latest(events, event)
            profile_name, profile = router.route(event.speaker)
            audio_path = backend.synthesize(event, profile_name, profile)

            # If the player advanced while a slow synthesis was running, do not
            # start speaking an obsolete line. The newest line is processed next.
            if not args.dry_run and not events.empty():
                continue
            play_audio(audio_path)
            handled += 1
            if args.once and handled >= 1:
                return 0
    except KeyboardInterrupt:
        print("\n[停止] 配音桥已退出。")
        return 0
    finally:
        stop.set()
        stop_audio()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="逸剑风云决原生对话 AI 配音桥")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS)
    parser.add_argument(
        "--replay-existing",
        action="store_true",
        help="从事件文件开头回放（默认只监听启动后的新台词）",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="不加载模型，只检查人物声线分配"
    )
    parser.add_argument("--once", action="store_true", help="处理一条台词后退出")
    return parser


if __name__ == "__main__":
    try:
        raise SystemExit(run(build_parser().parse_args()))
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        raise SystemExit(1)
