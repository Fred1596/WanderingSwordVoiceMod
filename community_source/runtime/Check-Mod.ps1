param(
    [string]$GamePath = ""
)

. (Join-Path $PSScriptRoot "Common.ps1")

$failed = $false
$releaseRoot = Get-ReleaseRoot
$gameRoot = Resolve-GameRoot -RequestedPath $GamePath
if ($null -eq $gameRoot) {
    Write-CheckLine $false "Wandering Sword installation could not be located. Run Install-Mod.cmd first."
    exit 1
}

$win64 = Get-GameWin64Path $gameRoot
$checks = @(
    @{ Label = "Game executable"; Path = Get-GameExecutablePath $gameRoot },
    @{ Label = "UE4SS proxy"; Path = [System.IO.Path]::Combine($win64, "dwmapi.dll") },
    @{ Label = "UE4SS runtime"; Path = [System.IO.Path]::Combine($win64, "ue4ss", "UE4SS.dll") },
    @{ Label = "Voice bridge"; Path = [System.IO.Path]::Combine($win64, "ue4ss", "Mods", "WanderingSwordVoiceProbe", "Scripts", "main.lua") },
    @{ Label = "Compact lookup"; Path = [System.IO.Path]::Combine($releaseRoot, "data", "runtime_lookup.compact.json") },
    @{ Label = "Audio directory"; Path = [System.IO.Path]::Combine($releaseRoot, "data", "offline", "audio") }
)

foreach ($check in $checks) {
    $exists = [System.IO.File]::Exists($check.Path) -or [System.IO.Directory]::Exists($check.Path)
    Write-CheckLine $exists ("{0}: {1}" -f $check.Label, $check.Path)
    if (-not $exists) {
        $failed = $true
    }
}

$modsFile = [System.IO.Path]::Combine($win64, "ue4ss", "Mods", "mods.txt")
$enabled = $false
if ([System.IO.File]::Exists($modsFile)) {
    $enabled = @([System.IO.File]::ReadAllLines($modsFile) | Where-Object {
        $_ -match '^\s*WanderingSwordVoiceProbe\s*:\s*1\s*$'
    }).Count -gt 0
}
Write-CheckLine $enabled "WanderingSwordVoiceProbe is enabled in mods.txt"
if (-not $enabled) {
    $failed = $true
}

$settingsPath = Get-Ue4ssSettingsPath $gameRoot
$configuredVersion = Get-Ue4ssEngineVersionOverride $settingsPath
$versionValid = $configuredVersion -eq "4.26"
$versionLabel = if ($null -eq $configuredVersion) { "missing" } else { $configuredVersion }
Write-CheckLine $versionValid ("UE4SS engine version override: {0} ({1})" -f $versionLabel, $settingsPath)
if (-not $versionValid) {
    $failed = $true
}

$runtimeDiagnostic = Get-Ue4ssRuntimeDiagnostic $gameRoot
$logIsOlderThanSettings = $false
if ([System.IO.File]::Exists($runtimeDiagnostic.LogPath) -and [System.IO.File]::Exists($settingsPath)) {
    $logIsOlderThanSettings = (
        [System.IO.File]::GetLastWriteTimeUtc($runtimeDiagnostic.LogPath) -lt
        [System.IO.File]::GetLastWriteTimeUtc($settingsPath)
    )
}

if ($runtimeDiagnostic.Code -eq "LogMissing") {
    Write-InfoLine "Runtime bridge has not been observed yet. Launch the game once to create UE4SS.log."
}
elseif ($logIsOlderThanSettings) {
    Write-InfoLine "UE4SS.log predates the repaired settings. Restart the game to perform the runtime check."
}
elseif ($runtimeDiagnostic.Code -eq "BridgeReady") {
    Write-CheckLine $true ("Runtime bridge: {0}" -f $runtimeDiagnostic.Message)
}
elseif ($runtimeDiagnostic.Code -eq "EngineVersionMissing" -or $runtimeDiagnostic.Code -eq "ScanTimedOut") {
    Write-CheckLine $false ("Runtime bridge: {0} Log: {1}" -f $runtimeDiagnostic.Message, $runtimeDiagnostic.LogPath)
    $failed = $true
}
else {
    Write-WarnLine ("Runtime bridge is not confirmed yet: {0} Log: {1}" -f $runtimeDiagnostic.Message, $runtimeDiagnostic.LogPath)
}

if (-not $failed) {
    Save-GameRoot $gameRoot
    Write-Host "All required installation checks passed." -ForegroundColor Green
    exit 0
}
Write-Host "Run Install-Mod.cmd to repair the installation." -ForegroundColor Yellow
exit 1
