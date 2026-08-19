$ErrorActionPreference = "Stop"

$WsVoicePython = if ($env:WSVOICE_PYTHON) { $env:WSVOICE_PYTHON } else { "python" }

if (-not (Get-Command $WsVoicePython -ErrorAction SilentlyContinue)) {
    throw "Python was not found. Add it to PATH or set WSVOICE_PYTHON."
}

& $WsVoicePython -c "import torch, torchaudio, soundfile, modelscope; from qwen_tts import Qwen3TTSModel; assert torch.cuda.is_available(), 'CUDA is unavailable'; print('wsvoice OK:', torch.__version__, torchaudio.__version__, torch.cuda.get_device_name(0))"
if ($LASTEXITCODE -ne 0) { throw "wsvoice validation failed." }
