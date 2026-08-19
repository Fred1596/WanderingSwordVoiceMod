# 从零复现技术链路

本文用于研究和二次开发。请只处理自己合法持有的游戏安装，并遵守游戏、模型、
工具和 API 服务的许可与条款。

## 1. 环境

推荐：

- Windows 10/11：资源提取、UE4SS 调试和玩家端打包；
- Linux + NVIDIA GPU：大规模离线合成；
- Python 3.10/3.11；
- 与本机 CUDA 驱动兼容的 PyTorch、torchaudio；
- 足够容纳模型、中间数据和 PCM WAV 的磁盘空间。

安装依赖：

```powershell
python -m pip install -r requirements-catalog.txt
python -m pip install -r requirements.txt
Copy-Item config\voice_profiles.example.json config\voice_profiles.json
```

Qwen3-TTS 与 PyTorch 的具体安装方式可能随上游版本变化，应以其官方文档为准。

## 2. 第三方工具

仓库不包含第三方可执行文件。按 [tools/README.md](../tools/README.md) 放置 repak、
UE4SS，以及可选的 UAssetGUI/umodel。所有路径均可通过命令参数覆盖。

## 3. 提取本地化资源

```powershell
$env:WS_GAME_ROOT = "D:\Path\To\Steam\steamapps\common\Wandering Sword"
python catalog\extract_localization.py --game-root $env:WS_GAME_ROOT
```

默认只解包构建对白目录所需的本地化资源，输出到 `extracted/catalog_source/`。
不要提交该目录。

## 4. 可选：补充官方人物元数据

使用 UAssetGUI 从自己持有的游戏中导出相应 DataTable JSON，并保存为：

```text
catalog/raw/NPCs.json
catalog/raw/NPCResources.json
```

随后压缩成流水线需要的字段：

```powershell
python catalog\extract_game_metadata.py
```

如果不执行这一步，目录仍可从 locres 建立，但角色身份、描述和肖像证据会减少。

## 5. 构建对白目录

```powershell
python catalog\build_catalog.py
```

主要输出：

```text
catalog/dialogue_catalog.jsonl
catalog/character_registry.json
catalog/character_registry.csv
```

脚本同时保留 numeric speaker ID，因为显示名可能重复，也可能随剧情变化。

## 6. 角色画像

这是可选步骤。参考实现通过 OpenAI-compatible DeepSeek Chat Completions 接口生成
结构化人物画像，并按角色单文件缓存以支持断点续跑：

```powershell
$env:DEEPSEEK_API_KEY = "在服务商控制台取得的密钥"
python catalog\profile_characters.py --workers 3 --batch-size 12
```

API Key 只能放在环境变量，不要写进源码、JSON 或日志。你也可以跳过 API，自己
按照 `docs/DATA_FORMATS.md` 生成 `catalog/profiles/character_profiles.json`。

## 7. 冲突消解和声线规划

```powershell
python catalog\build_voice_plan.py
```

可选的人像人工复核结果放在 `.gitignore` 排除的 `catalog/portraits/` 下。声线规划
必须检查所有 `generation_status`，只有 `ready` 组才能进入生成流水线。

## 8. 生成 job manifest

```powershell
python catalog\build_offline_jobs.py
python scripts\verify_generation_ready.py
```

该步骤生成锚点计划、对白 job、精确索引、唯一文本兜底索引和碰撞报告。音频文件
名由 speaker ID 和原始台词的稳定哈希决定。

## 9. 下载模型

确保 `modelscope` 在 PATH 中，或设置 `MODELSCOPE_EXE`：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\download_models.ps1
```

默认目录：

```text
models/Qwen3-TTS-12Hz-1.7B-VoiceDesign
models/Qwen3-TTS-12Hz-0.6B-Base
```

模型目录受 `.gitignore` 保护。

## 10. 小规模验证

首次运行不要直接生成全量：

```powershell
python bridge\check_server_requirements.py --require-gpu
python bridge\design_offline_anchors.py --limit 2 --batch-size 2
python bridge\synthesize_offline_dialogue.py --limit 20 --batch-size 2
```

人工试听、检查时长分布和日志后，再逐步提高 batch。不同显卡不能机械照搬参考值。

## 11. 全量生成

Linux 服务器：

```bash
DIALOGUE_BATCH=20 ANCHOR_BATCH=8 bash run_server_pipeline.sh
```

流水线会先检查环境，再生成缺失锚点和对白，最后执行完整性验证。再次运行会跳过
已有有效文件。

## 12. 安装 UE4SS 对话桥

把：

```text
src/ue4ss/Mods/WanderingSwordVoiceProbe
```

复制到游戏：

```text
Wandering_Sword/Binaries/Win64/ue4ss/Mods/WanderingSwordVoiceProbe
```

并在 UE4SS `Mods/mods.txt` 中启用：

```text
WanderingSwordVoiceProbe : 1
```

启动游戏后，事件应写入 Mod 目录中的 `dialogue_events.jsonl`。

## 13. 开发模式播放

```powershell
python bridge\offline_runtime.py --lookup offline\manifest\runtime_lookup.json
```

实际社区包使用 `community_source/runtime/VoicePlayer.ps1` 和 compact lookup，玩家端
无需 Python。

## 14. 构建社区包

`scripts/build_community_release.py` 需要：

- 已生成并校验的 WAV；
- `runtime_lookup.json`；
- 已下载并解压的 UE4SS runtime；
- `community_source/` 模板。

由于这些材料没有进入 Git，构建前应按照脚本报错逐一提供。发布时仍需自行确认
目标社区、游戏权利人和第三方组件的分发规则。
