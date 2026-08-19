$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ModelScope = if ($env:MODELSCOPE_EXE) { $env:MODELSCOPE_EXE } else { "modelscope" }
$ModelsRoot = Join-Path $ProjectRoot "models"

if (-not (Get-Command $ModelScope -ErrorAction SilentlyContinue)) {
    throw "ModelScope CLI was not found. Install modelscope or set MODELSCOPE_EXE."
}

& $ModelScope download `
    --model Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign `
    --local_dir (Join-Path $ModelsRoot "Qwen3-TTS-12Hz-1.7B-VoiceDesign")
if ($LASTEXITCODE -ne 0) { throw "VoiceDesign model download failed." }

& $ModelScope download `
    --model Qwen/Qwen3-TTS-12Hz-0.6B-Base `
    --local_dir (Join-Path $ModelsRoot "Qwen3-TTS-12Hz-0.6B-Base")
if ($LASTEXITCODE -ne 0) { throw "Base model download failed." }

Write-Host "Both models were downloaded to: $ModelsRoot"
