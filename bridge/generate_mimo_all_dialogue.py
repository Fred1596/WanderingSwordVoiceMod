#!/usr/bin/env python3
"""Generate every dialogue WAV directly with MiMo VoiceDesign through two providers.

The generator deliberately treats all keys for one provider as a single RPM pool.
Existing Qwen audio is regenerated unless the journal proves that the same line,
prompt, model, and speed policy have already completed successfully.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import queue
import sys
import threading
import time
import wave
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ANCHORS = PROJECT_ROOT / "offline" / "manifest" / "voice_anchors.json"
DEFAULT_JOBS = PROJECT_ROOT / "offline" / "manifest" / "dialogue_jobs.jsonl"
DEFAULT_JOURNAL = PROJECT_ROOT / "logs" / "mimo_direct_progress.jsonl"
DEFAULT_SUMMARY = PROJECT_ROOT / "logs" / "mimo_direct_summary.json"

MODEL = "mimo-v2.5-tts-voicedesign"
MIMO_URL = "https://api.xiaomimimo.com/v1/chat/completions"
DMX_URL = "https://www.dmxapi.cn/v1/chat/completions"
POLICY_VERSION = "mimo-direct-v1-speed-plus-10pct"
SPEED_SUFFIX = (
    "在保持角色固有语速特征的基础上，将实际说话节奏相对该角色原定语速加快约百分之十，"
    "适当缩短句间停顿；保持自然清晰，不要急促、抢字或机械加速。"
)
MIMO_KEY_NAMES = tuple(f"mimo{i}" for i in range(1, 6))
DMX_KEY_NAMES = tuple(f"DMX_API_KEY_{i}" for i in range(1, 6))

# Derived from the project's existing 12,500 Qwen WAVs. This is used only for
# the startup disk-size estimate; live progress uses actual MiMo response sizes.
MEASURED_SECONDS_PER_CHARACTER = 63270.105 / 299877
EXPECTED_SPEED_FACTOR = 1.10


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def format_duration(seconds: float | None) -> str:
    if seconds is None or seconds < 0 or seconds == float("inf"):
        return "--:--:--"
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours >= 24:
        days, hours = divmod(hours, 24)
        return f"{days}d {hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def read_user_environment(name: str) -> str | None:
    """Read a Windows user environment variable without printing its value."""
    if os.name != "nt":
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, name)
        return str(value).strip() or None
    except (FileNotFoundError, OSError):
        return None


def load_named_keys(names: Iterable[str]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for name in names:
        value = (os.environ.get(name) or "").strip() or read_user_environment(name)
        if value:
            found.append((name, value))
    return found


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON at {path}:{line_number}: {exc}") from exc


def resolve_project_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def canonical_prompt(base_prompt: str) -> str:
    base = base_prompt.strip()
    if not base:
        raise ValueError("Voice design prompt is empty")
    return f"{base}{SPEED_SUFFIX}"


def job_signature(job: dict[str, Any], prompt: str) -> str:
    material = {
        "policy": POLICY_VERSION,
        "model": MODEL,
        "voice_group_id": job["voice_group_id"],
        "prompt": prompt,
        "text": job["tts_text"],
        "optimize_text_preview": False,
        "format": "wav",
    }
    encoded = json.dumps(
        material, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class Console:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._handle = path.open("a", encoding="utf-8", buffering=1)
        self._lock = threading.Lock()

    def log(self, message: str) -> None:
        with self._lock:
            print(message, flush=True)
            self._handle.write(message + "\n")

    def close(self) -> None:
        with self._lock:
            self._handle.close()


class Journal:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._handle = path.open("a", encoding="utf-8", buffering=1)
        self._lock = threading.Lock()

    def append(self, value: dict[str, Any]) -> None:
        line = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            self._handle.write(line + "\n")
            self._handle.flush()

    def close(self) -> None:
        with self._lock:
            self._handle.close()


class SharedRateLimiter:
    """A fixed-spacing limiter shared by every key and worker of one provider."""

    def __init__(self, rpm: float, safety_factor: float) -> None:
        if rpm <= 0:
            raise ValueError("RPM must be positive")
        if safety_factor < 1.0:
            raise ValueError("Rate safety factor must be at least 1.0")
        self.rpm = rpm
        self.interval = 60.0 / rpm * safety_factor
        self._next_slot = time.monotonic()
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next_slot)
            self._next_slot = slot + self.interval
        delay = slot - now
        if delay > 0:
            time.sleep(delay)

    def defer(self, seconds: float) -> None:
        if seconds <= 0:
            return
        with self._lock:
            self._next_slot = max(self._next_slot, time.monotonic() + seconds)


class KeyPool:
    def __init__(self, keys: list[tuple[str, str]]) -> None:
        self._keys = list(keys)
        self._disabled: set[str] = set()
        self._index = 0
        self._lock = threading.Lock()

    def next(self) -> tuple[str, str]:
        with self._lock:
            if not self._keys:
                raise RuntimeError("No API keys are configured")
            for _ in range(len(self._keys)):
                name, value = self._keys[self._index % len(self._keys)]
                self._index += 1
                if name not in self._disabled:
                    return name, value
        raise RuntimeError("Every configured API key for this provider is disabled")

    def disable(self, name: str) -> None:
        with self._lock:
            self._disabled.add(name)

    @property
    def active_count(self) -> int:
        with self._lock:
            return sum(name not in self._disabled for name, _ in self._keys)


@dataclass
class Provider:
    name: str
    url: str
    key_pool: KeyPool
    limiter: SharedRateLimiter
    successes: int = 0
    failures: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record_success(self) -> None:
        with self._lock:
            self.successes += 1

    def record_failure(self) -> None:
        with self._lock:
            self.failures += 1

    def counts(self) -> tuple[int, int]:
        with self._lock:
            return self.successes, self.failures


@dataclass(frozen=True)
class WorkItem:
    job: dict[str, Any]
    prompt: str
    signature: str
    output: Path


class RequestFailure(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = True,
        authentication: bool = False,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable
        self.authentication = authentication
        self.retry_after = retry_after


def safe_response_excerpt(text: str, limit: int = 400) -> str:
    compact = " ".join(text.replace("\x00", "").split())
    return compact[:limit]


def parse_retry_after(headers: Any) -> float | None:
    value = headers.get("Retry-After") if headers is not None else None
    if value is None:
        return None
    try:
        return max(0.0, min(float(value), 600.0))
    except (TypeError, ValueError):
        return None


def request_audio(
    session: Any,
    provider: Provider,
    key_name: str,
    api_key: str,
    prompt: str,
    text: str,
    timeout: float,
) -> tuple[bytes, dict[str, Any]]:
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": text},
        ],
        "audio": {"format": "wav", "optimize_text_preview": False},
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    started = time.perf_counter()
    try:
        response = session.post(
            provider.url,
            headers=headers,
            json=payload,
            timeout=(15.0, timeout),
        )
    except Exception as exc:
        raise RequestFailure(f"network error: {type(exc).__name__}: {exc}") from exc
    request_seconds = time.perf_counter() - started

    if response.status_code != 200:
        status = response.status_code
        excerpt = safe_response_excerpt(response.text)
        authentication = status in (401, 403)
        retryable = authentication or status in (408, 409, 425, 429) or status >= 500
        raise RequestFailure(
            f"HTTP {status}: {excerpt}",
            status_code=status,
            retryable=retryable,
            authentication=authentication,
            retry_after=parse_retry_after(response.headers),
        )
    try:
        result = response.json()
        encoded = result["choices"][0]["message"]["audio"]["data"]
        audio_bytes = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise RequestFailure(f"invalid JSON/audio response: {type(exc).__name__}: {exc}") from exc

    if not (audio_bytes.startswith(b"RIFF") and audio_bytes[8:12] == b"WAVE"):
        raise RequestFailure("response is not a RIFF/WAVE file")
    try:
        with wave.open(io.BytesIO(audio_bytes), "rb") as handle:
            sample_rate = handle.getframerate()
            channels = handle.getnchannels()
            sample_width = handle.getsampwidth()
            frames = handle.getnframes()
    except (wave.Error, EOFError) as exc:
        raise RequestFailure(f"invalid WAV response: {exc}") from exc
    if sample_rate <= 0 or frames < sample_rate // 10:
        raise RequestFailure("WAV response is empty or implausibly short")
    metadata = {
        "key_name": key_name,
        "request_seconds": round(request_seconds, 3),
        "bytes": len(audio_bytes),
        "sample_rate": sample_rate,
        "channels": channels,
        "sample_width_bytes": sample_width,
        "duration_seconds": round(frames / sample_rate, 3),
    }
    return audio_bytes, metadata


def write_audio_atomic(output: Path, audio_bytes: bytes, job_id: str) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(f".{job_id[:10]}.tmp.wav")
    temporary.write_bytes(audio_bytes)
    temporary.replace(output)


def load_completed_journal(path: Path) -> dict[str, dict[str, Any]]:
    completed: dict[str, dict[str, Any]] = {}
    if not path.is_file():
        return completed
    for entry in iter_jsonl(path):
        if entry.get("status") == "ok" and entry.get("job_id"):
            completed[str(entry["job_id"])] = entry
    return completed


class Progress:
    def __init__(
        self,
        *,
        total: int,
        resumed: int,
        resumed_bytes: int,
        progress_every: int,
        providers: list[Provider],
        theoretical_rpm: float,
        console: Console,
    ) -> None:
        self.total = total
        self.resumed = resumed
        self.ok_session = 0
        self.failed = 0
        self.bytes_total = resumed_bytes
        self.audio_seconds = 0.0
        self.request_seconds = 0.0
        self.progress_every = max(1, progress_every)
        self.providers = providers
        self.theoretical_rpm = theoretical_rpm
        self.console = console
        self.started = time.monotonic()
        self._lock = threading.Lock()

    def success(self, provider: Provider, metadata: dict[str, Any]) -> None:
        with self._lock:
            self.ok_session += 1
            self.bytes_total += int(metadata["bytes"])
            self.audio_seconds += float(metadata["duration_seconds"])
            self.request_seconds += float(metadata["request_seconds"])
            should_print = (
                self.ok_session <= 2 or self.ok_session % self.progress_every == 0
            )
            if should_print:
                self._print_locked()

    def failure(self) -> None:
        with self._lock:
            self.failed += 1
            self._print_locked()

    def _print_locked(self) -> None:
        elapsed = max(time.monotonic() - self.started, 0.001)
        completed = self.resumed + self.ok_session
        # Failed jobs are still unfinished and must remain visible in the ETA.
        remaining = max(0, self.total - completed)
        live_rpm = self.ok_session / elapsed * 60.0 if self.ok_session else 0.0
        eta_live = remaining / live_rpm * 60.0 if live_rpm > 0 else None
        eta_floor = (
            remaining / self.theoretical_rpm * 60.0
            if self.theoretical_rpm > 0
            else None
        )
        provider_text = []
        for provider in self.providers:
            successes, failures = provider.counts()
            provider_text.append(f"{provider.name}:{successes}/{failures}")
        self.console.log(
            "[progress] "
            f"done={completed}/{self.total} session={self.ok_session} "
            f"failed={self.failed} remaining={remaining} "
            f"elapsed={format_duration(elapsed)} live_rpm={live_rpm:.2f} "
            f"eta={format_duration(eta_live)} floor={format_duration(eta_floor)} "
            f"wav={self.bytes_total / 2**30:.3f}GiB "
            f"providers={' '.join(provider_text)}"
        )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            elapsed = max(time.monotonic() - self.started, 0.001)
            completed = self.resumed + self.ok_session
            remaining = max(0, self.total - completed)
            return {
                "total": self.total,
                "resumed": self.resumed,
                "completed_this_run": self.ok_session,
                "completed_total": completed,
                "failed_this_run": self.failed,
                "remaining": remaining,
                "elapsed_seconds": round(elapsed, 3),
                "live_rpm": round(self.ok_session / elapsed * 60.0, 3),
                "wav_bytes_recorded": self.bytes_total,
                "wav_gib_recorded": round(self.bytes_total / 2**30, 3),
                "generated_audio_seconds_this_run": round(self.audio_seconds, 3),
                "request_seconds_total": round(self.request_seconds, 3),
                "average_request_seconds": round(
                    self.request_seconds / self.ok_session, 3
                )
                if self.ok_session
                else None,
                "providers": {
                    provider.name: {
                        "successes": provider.counts()[0],
                        "request_failures": provider.counts()[1],
                        "active_keys": provider.key_pool.active_count,
                    }
                    for provider in self.providers
                },
            }


def worker(
    *,
    provider: Provider,
    work_queue: queue.Queue[WorkItem],
    stop_event: threading.Event,
    journal: Journal,
    progress: Progress,
    console: Console,
    request_timeout: float,
    max_attempts: int,
) -> None:
    import requests

    session = requests.Session()
    while not stop_event.is_set():
        try:
            item = work_queue.get(timeout=0.5)
        except queue.Empty:
            continue
        try:
            final_error: RequestFailure | RuntimeError | None = None
            used_key_name: str | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    used_key_name, api_key = provider.key_pool.next()
                except RuntimeError as exc:
                    # Return the item for a worker belonging to the other provider.
                    work_queue.put(item)
                    final_error = exc
                    return
                provider.limiter.wait()
                try:
                    audio_bytes, metadata = request_audio(
                        session,
                        provider,
                        used_key_name,
                        api_key,
                        item.prompt,
                        item.job["tts_text"],
                        request_timeout,
                    )
                    write_audio_atomic(item.output, audio_bytes, item.job["job_id"])
                    provider.record_success()
                    journal.append(
                        {
                            "timestamp": utc_now(),
                            "status": "ok",
                            "job_id": item.job["job_id"],
                            "signature": item.signature,
                            "speaker": item.job.get("speaker"),
                            "voice_group_id": item.job["voice_group_id"],
                            "provider": provider.name,
                            "key_name": metadata["key_name"],
                            "output": str(item.output.relative_to(PROJECT_ROOT)),
                            "bytes": metadata["bytes"],
                            "duration_seconds": metadata["duration_seconds"],
                            "request_seconds": metadata["request_seconds"],
                            "sample_rate": metadata["sample_rate"],
                            "channels": metadata["channels"],
                            "sample_width_bytes": metadata["sample_width_bytes"],
                            "policy": POLICY_VERSION,
                            "model": MODEL,
                        }
                    )
                    progress.success(provider, metadata)
                    final_error = None
                    break
                except RequestFailure as exc:
                    provider.record_failure()
                    final_error = exc
                    if exc.authentication and used_key_name:
                        provider.key_pool.disable(used_key_name)
                        console.log(
                            f"[key-disabled] provider={provider.name} "
                            f"key={used_key_name} status={exc.status_code}"
                        )
                    if exc.retry_after:
                        provider.limiter.defer(exc.retry_after)
                    if not exc.retryable or attempt >= max_attempts:
                        break
                    if exc.status_code == 429 and not exc.retry_after:
                        provider.limiter.defer(min(60.0, 5.0 * attempt))
                    console.log(
                        f"[retry] provider={provider.name} "
                        f"job={item.job['job_id'][:12]} attempt={attempt}/{max_attempts} "
                        f"error={exc}"
                    )
            if final_error is not None:
                journal.append(
                    {
                        "timestamp": utc_now(),
                        "status": "failed",
                        "job_id": item.job["job_id"],
                        "signature": item.signature,
                        "speaker": item.job.get("speaker"),
                        "voice_group_id": item.job["voice_group_id"],
                        "provider": provider.name,
                        "key_name": used_key_name,
                        "error": str(final_error),
                        "policy": POLICY_VERSION,
                        "model": MODEL,
                    }
                )
                console.log(
                    f"[failed] provider={provider.name} "
                    f"job={item.job['job_id']} speaker={item.job.get('speaker')} "
                    f"error={final_error}"
                )
                progress.failure()
        except Exception as exc:
            # Keep a single local filesystem or unexpected response bug from
            # silently killing one worker and falsely reporting a complete run.
            provider.record_failure()
            try:
                journal.append(
                    {
                        "timestamp": utc_now(),
                        "status": "failed",
                        "job_id": item.job["job_id"],
                        "signature": item.signature,
                        "speaker": item.job.get("speaker"),
                        "voice_group_id": item.job["voice_group_id"],
                        "provider": provider.name,
                        "error": f"unexpected {type(exc).__name__}: {exc}",
                        "policy": POLICY_VERSION,
                        "model": MODEL,
                    }
                )
            except Exception as journal_exc:
                console.log(
                    f"[journal-error] job={item.job['job_id']} "
                    f"error={type(journal_exc).__name__}: {journal_exc}"
                )
            console.log(
                f"[failed-unexpected] provider={provider.name} "
                f"job={item.job['job_id']} error={type(exc).__name__}: {exc}"
            )
            progress.failure()
        finally:
            work_queue.task_done()
    session.close()


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--anchors", type=Path, default=DEFAULT_ANCHORS)
    parser.add_argument("--jobs", type=Path, default=DEFAULT_JOBS)
    parser.add_argument("--journal", type=Path, default=DEFAULT_JOURNAL)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--mimo-rpm", type=float, default=10.0)
    parser.add_argument("--dmx-rpm", type=float, default=10.0)
    parser.add_argument(
        "--rate-safety",
        type=float,
        default=1.02,
        help="Multiply request spacing by this factor; 1.02 avoids exact quota edges",
    )
    parser.add_argument("--workers-per-provider", type=int, default=5)
    parser.add_argument("--request-timeout", type=float, default=240.0)
    parser.add_argument("--max-attempts", type=int, default=6)
    parser.add_argument("--progress-every", type=int, default=10)
    parser.add_argument("--limit", type=int, help="Generate at most N pending lines")
    parser.add_argument(
        "--provider",
        choices=("both", "mimo", "dmx"),
        default="both",
        help="Use both providers or only one provider",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--force-current",
        action="store_true",
        help="Regenerate even lines already completed by this exact MiMo policy",
    )
    args = parser.parse_args()

    anchors_path = args.anchors.resolve()
    jobs_path = args.jobs.resolve()
    journal_path = args.journal.resolve()
    summary_path = args.summary.resolve()
    if not anchors_path.is_file():
        raise FileNotFoundError(anchors_path)
    if not jobs_path.is_file():
        raise FileNotFoundError(jobs_path)
    if args.workers_per_provider < 1:
        parser.error("--workers-per-provider must be at least 1")
    if args.max_attempts < 1:
        parser.error("--max-attempts must be at least 1")

    run_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    console = Console(PROJECT_ROOT / "logs" / f"mimo_direct_run_{run_stamp}.log")
    console.log(f"Run log: {console.path}")
    console.log(f"Policy: {POLICY_VERSION}")
    console.log(f"Model: {MODEL}")

    anchor_plan = json.loads(anchors_path.read_text(encoding="utf-8"))
    prompt_by_group: dict[str, str] = {}
    for anchor in anchor_plan:
        group_id = str(anchor["voice_group_id"])
        if group_id in prompt_by_group:
            raise ValueError(f"Duplicate voice group: {group_id}")
        prompt_by_group[group_id] = canonical_prompt(anchor["voice_design_prompt"])

    all_jobs = list(iter_jsonl(jobs_path))
    if not all_jobs:
        raise RuntimeError("No dialogue jobs found")
    missing_groups = sorted(
        {str(job["voice_group_id"]) for job in all_jobs} - set(prompt_by_group)
    )
    if missing_groups:
        raise RuntimeError(
            f"{len(missing_groups)} dialogue voice groups have no prompt: "
            + ", ".join(missing_groups[:10])
        )

    key_configs: list[tuple[str, str, list[tuple[str, str]], float]] = []
    if args.provider in ("both", "mimo"):
        key_configs.append(("mimo", MIMO_URL, load_named_keys(MIMO_KEY_NAMES), args.mimo_rpm))
    if args.provider in ("both", "dmx"):
        key_configs.append(("dmx", DMX_URL, load_named_keys(DMX_KEY_NAMES), args.dmx_rpm))

    providers: list[Provider] = []
    for name, url, keys, rpm in key_configs:
        if not keys:
            console.log(f"[warning] provider={name} no configured keys; provider disabled")
            continue
        console.log(
            f"[keys] provider={name} found={len(keys)} "
            f"names={','.join(key_name for key_name, _ in keys)} shared_rpm={rpm:g}"
        )
        providers.append(
            Provider(
                name=name,
                url=url,
                key_pool=KeyPool(keys),
                limiter=SharedRateLimiter(rpm, args.rate_safety),
            )
        )
    if not providers and not args.dry_run:
        raise RuntimeError("No usable MiMo or DMX API keys were found")

    if not args.dry_run:
        try:
            import requests  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "Missing dependency 'requests'. Run: python -m pip install requests"
            ) from exc

    previous = {} if args.force_current else load_completed_journal(journal_path)
    work_items: list[WorkItem] = []
    resumed = resumed_bytes = 0
    total_characters = 0
    for job in all_jobs:
        prompt = prompt_by_group[str(job["voice_group_id"])]
        signature = job_signature(job, prompt)
        output = resolve_project_path(job["audio_file"])
        total_characters += len(job["tts_text"])
        prior = previous.get(str(job["job_id"]))
        if (
            prior
            and prior.get("signature") == signature
            and output.is_file()
            and output.stat().st_size > 44
        ):
            resumed += 1
            resumed_bytes += int(prior.get("bytes") or output.stat().st_size)
            continue
        work_items.append(
            WorkItem(job=job, prompt=prompt, signature=signature, output=output)
        )

    if args.limit is not None:
        if args.limit < 0:
            parser.error("--limit cannot be negative")
        work_items = work_items[: args.limit]
    planned_total = resumed + len(work_items)
    theoretical_rpm = sum(
        config[3] / args.rate_safety
        for config in key_configs
        if any(provider.name == config[0] for provider in providers)
    )
    floor_seconds = (
        len(work_items) / theoretical_rpm * 60.0 if theoretical_rpm > 0 else None
    )
    estimated_audio_seconds = (
        total_characters
        * MEASURED_SECONDS_PER_CHARACTER
        / EXPECTED_SPEED_FACTOR
    )
    estimated_wav_gib = estimated_audio_seconds * 48000.0 / 2**30

    console.log(
        f"[plan] voice_groups={len(prompt_by_group)} all_jobs={len(all_jobs)} "
        f"resumed={resumed} pending={len(work_items)} run_total={planned_total}"
    )
    console.log(
        f"[estimate] aggregate_rpm={theoretical_rpm:.2f} "
        f"quota_floor={format_duration(floor_seconds)} "
        f"full_audio_hours={estimated_audio_seconds / 3600:.1f} "
        f"full_wav={estimated_wav_gib:.1f}GiB expected_range=8.0-11.0GiB"
    )
    console.log(
        "[policy] existing Qwen WAVs without a matching MiMo journal entry will be "
        "regenerated and atomically replaced"
    )
    if args.dry_run or not work_items:
        console.log("Dry run complete." if args.dry_run else "Nothing pending.")
        console.close()
        return 0

    journal = Journal(journal_path)
    progress = Progress(
        total=planned_total,
        resumed=resumed,
        resumed_bytes=resumed_bytes,
        progress_every=args.progress_every,
        providers=providers,
        theoretical_rpm=theoretical_rpm,
        console=console,
    )
    work_queue: queue.Queue[WorkItem] = queue.Queue()
    for item in work_items:
        work_queue.put(item)

    stop_event = threading.Event()
    threads: list[threading.Thread] = []
    for provider in providers:
        for worker_index in range(args.workers_per_provider):
            thread = threading.Thread(
                target=worker,
                kwargs={
                    "provider": provider,
                    "work_queue": work_queue,
                    "stop_event": stop_event,
                    "journal": journal,
                    "progress": progress,
                    "console": console,
                    "request_timeout": args.request_timeout,
                    "max_attempts": args.max_attempts,
                },
                name=f"{provider.name}-{worker_index + 1}",
                daemon=True,
            )
            thread.start()
            threads.append(thread)

    interrupted = False
    fatal_worker_loss = False
    try:
        while work_queue.unfinished_tasks:
            if not any(thread.is_alive() for thread in threads):
                fatal_worker_loss = True
                console.log(
                    "[fatal] all provider workers stopped while jobs are still pending"
                )
                break
            time.sleep(1.0)
    except KeyboardInterrupt:
        interrupted = True
        console.log("[stopped] Ctrl+C received; completed lines are journaled and resumable")
    finally:
        stop_event.set()
        for thread in threads:
            thread.join(timeout=2.0)
        snapshot = progress.snapshot()
        summary = {
            "generated_at": utc_now(),
            "policy": POLICY_VERSION,
            "model": MODEL,
            "speed_suffix": SPEED_SUFFIX,
            "rate_safety": args.rate_safety,
            "theoretical_aggregate_rpm": round(theoretical_rpm, 3),
            "interrupted": interrupted,
            "fatal_worker_loss": fatal_worker_loss,
            **snapshot,
        }
        write_json_atomic(summary_path, summary)
        console.log(json.dumps(summary, ensure_ascii=False, indent=2))
        journal.close()
        console.close()

    if interrupted:
        return 130
    if fatal_worker_loss or snapshot["failed_this_run"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
