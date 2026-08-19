param([string]$Speed = "")

. (Join-Path $PSScriptRoot "Common.ps1")

[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
$releaseRoot = Get-ReleaseRoot
$current = Get-PlaybackSpeedSetting $releaseRoot

Write-Host ("Current playback speed: {0:0.00}x" -f $current) -ForegroundColor Cyan
Write-Host "Recommended values: 1.00 (original), 1.10, 1.20, 1.30"
Write-Host "Allowed range: 1.00 to 1.50. The voice pitch is preserved as much as possible."

if ([string]::IsNullOrWhiteSpace($Speed)) {
    $Speed = Read-Host "New playback speed"
}

try {
    $validated = ConvertTo-PlaybackSpeed $Speed
    $path = Save-PlaybackSpeedSetting $releaseRoot $validated
}
catch {
    Write-Host ("Invalid setting: {0}" -f $_.Exception.Message) -ForegroundColor Red
    exit 2
}

Write-Host ("Playback speed saved: {0:0.00}x" -f $validated) -ForegroundColor Green
Write-Host ("Setting file: {0}" -f $path)
Write-Host "Restart the voice player to apply the new speed."
exit 0
