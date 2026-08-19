# WanderingSwordVoiceMod

Source code and technical documentation for an offline AI voice pipeline for
*Wandering Sword*. The implementation covers dialogue extraction, evidence-based
character profiling, voice grouping, anchor design, resumable batch synthesis,
an UE4SS dialogue bridge, deterministic lookup, and model-free playback.

This repository includes the final dialogue catalog snapshot, character
registry, character profiles, and voice plan used by the project. It contains
**no original game archives or assets, generated speech, model weights, UE4SS
binaries, or other third-party executables**. It is still not a ready-to-play
Mod package.

The reference build validated the design at 849 voice groups and 44,643 unique
dialogue jobs. The catalog and planning metadata are included; generated audio
is distributed separately by the project author.

## Runtime design

1. An UE4SS Lua Mod observes confirmed dialogue widgets; no OCR is used.
2. Each event is appended to a JSONL stream as speaker plus text.
3. The player normalizes the event and hashes `speaker + NUL + text` with SHA-256.
4. A compact lookup maps the digest to a pre-generated WAV.
5. New dialogue interrupts the previous line to keep speech aligned with the UI.

See the Chinese [README](README.md), [architecture](docs/ARCHITECTURE.md), and
[reproduction guide](docs/REPRODUCTION.md) for the full pipeline.

## License

Original code in this repository is released under the [MIT License](LICENSE).
The license does not cover game content, generated audio, models, trademarks,
or third-party components. See [NOTICE.md](NOTICE.md).
