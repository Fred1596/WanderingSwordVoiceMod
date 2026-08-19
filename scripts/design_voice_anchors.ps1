$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = if ($env:WSVOICE_PYTHON) { $env:WSVOICE_PYTHON } else { "python" }
$Designer = Join-Path $ProjectRoot "bridge\design_voice_anchors.py"

if (-not (Get-Command $Python -ErrorAction SilentlyContinue)) {
    throw "Python was not found. Add it to PATH or set WSVOICE_PYTHON."
}

& $Python $Designer @args
