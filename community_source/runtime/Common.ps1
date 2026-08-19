Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

function Get-ReleaseRoot {
    return [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
}

function Get-GameExecutablePath {
    param([string]$GameRoot)
    return [System.IO.Path]::Combine(
        $GameRoot,
        "Wandering_Sword",
        "Binaries",
        "Win64",
        "JH-Win64-Shipping.exe"
    )
}

function Get-GameWin64Path {
    param([string]$GameRoot)
    return [System.IO.Path]::Combine(
        $GameRoot,
        "Wandering_Sword",
        "Binaries",
        "Win64"
    )
}

function Get-Ue4ssSettingsPath {
    param([string]$GameRoot)
    return [System.IO.Path]::Combine(
        (Get-GameWin64Path $GameRoot),
        "ue4ss",
        "UE4SS-settings.ini"
    )
}

function Get-Ue4ssLogPath {
    param([string]$GameRoot)
    return [System.IO.Path]::Combine(
        (Get-GameWin64Path $GameRoot),
        "ue4ss",
        "UE4SS.log"
    )
}

function Test-GameRoot {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) {
        return $false
    }
    try {
        $expanded = [Environment]::ExpandEnvironmentVariables($Path.Trim().Trim('"'))
        $fullPath = [System.IO.Path]::GetFullPath($expanded)
        return [System.IO.File]::Exists((Get-GameExecutablePath $fullPath))
    }
    catch {
        return $false
    }
}

function Add-GameCandidate {
    param(
        [System.Collections.ArrayList]$Candidates,
        [string]$Path
    )
    if (-not [string]::IsNullOrWhiteSpace($Path)) {
        [void]$Candidates.Add($Path)
    }
}

function Get-SteamRoots {
    $roots = New-Object System.Collections.ArrayList
    $registryCandidates = @(
        @{ Key = "HKCU:\Software\Valve\Steam"; Name = "SteamPath" },
        @{ Key = "HKLM:\Software\WOW6432Node\Valve\Steam"; Name = "InstallPath" },
        @{ Key = "HKLM:\Software\Valve\Steam"; Name = "InstallPath" }
    )
    foreach ($candidate in $registryCandidates) {
        try {
            $value = (Get-ItemProperty -LiteralPath $candidate.Key -ErrorAction Stop).($candidate.Name)
            if (-not [string]::IsNullOrWhiteSpace($value)) {
                [void]$roots.Add($value)
            }
        }
        catch {
        }
    }
    return @($roots)
}

function Resolve-GameRoot {
    param(
        [string]$RequestedPath,
        [switch]$AllowPrompt
    )

    $releaseRoot = Get-ReleaseRoot
    $candidates = New-Object System.Collections.ArrayList
    Add-GameCandidate $candidates $RequestedPath
    Add-GameCandidate $candidates $env:WS_GAME_ROOT

    $savedPathFile = [System.IO.Path]::Combine($releaseRoot, "config", "game_path.txt")
    if ([System.IO.File]::Exists($savedPathFile)) {
        Add-GameCandidate $candidates ([System.IO.File]::ReadAllText($savedPathFile).Trim())
    }

    $ancestor = $releaseRoot
    for ($index = 0; $index -lt 5 -and $ancestor; $index++) {
        Add-GameCandidate $candidates $ancestor
        $parent = [System.IO.Directory]::GetParent($ancestor)
        $ancestor = if ($null -eq $parent) { $null } else { $parent.FullName }
    }

    foreach ($steamRoot in Get-SteamRoots) {
        Add-GameCandidate $candidates ([System.IO.Path]::Combine(
            $steamRoot, "steamapps", "common", "Wandering Sword"
        ))
        $libraryFile = [System.IO.Path]::Combine($steamRoot, "steamapps", "libraryfolders.vdf")
        if ([System.IO.File]::Exists($libraryFile)) {
            foreach ($line in [System.IO.File]::ReadLines($libraryFile)) {
                $match = [regex]::Match($line, '"path"\s+"([^"]+)"')
                if ($match.Success) {
                    $libraryRoot = $match.Groups[1].Value.Replace('\\', '\')
                    Add-GameCandidate $candidates ([System.IO.Path]::Combine(
                        $libraryRoot, "steamapps", "common", "Wandering Sword"
                    ))
                }
            }
        }
    }

    foreach ($drive in Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue) {
        foreach ($relative in @(
            "SteamLibrary\steamapps\common\Wandering Sword",
            "Steam\steamapps\common\Wandering Sword",
            "Program\Steam\steamapps\common\Wandering Sword",
            "Program Files (x86)\Steam\steamapps\common\Wandering Sword"
        )) {
            Add-GameCandidate $candidates ([System.IO.Path]::Combine($drive.Root, $relative))
        }
    }

    $seen = @{}
    foreach ($candidate in $candidates) {
        try {
            $fullPath = [System.IO.Path]::GetFullPath(
                [Environment]::ExpandEnvironmentVariables($candidate.Trim().Trim('"'))
            )
        }
        catch {
            continue
        }
        $key = $fullPath.ToLowerInvariant()
        if ($seen.ContainsKey($key)) {
            continue
        }
        $seen[$key] = $true
        if (Test-GameRoot $fullPath) {
            return $fullPath
        }
    }

    if ($AllowPrompt) {
        Write-Host "Game directory was not detected automatically."
        Write-Host "Paste the folder containing JH.exe, then press Enter."
        $manual = Read-Host "Game directory"
        if (Test-GameRoot $manual) {
            return [System.IO.Path]::GetFullPath($manual.Trim().Trim('"'))
        }
    }
    return $null
}

function Save-GameRoot {
    param([string]$GameRoot)
    $releaseRoot = Get-ReleaseRoot
    $configDirectory = [System.IO.Path]::Combine($releaseRoot, "config")
    [System.IO.Directory]::CreateDirectory($configDirectory) | Out-Null
    $pathFile = [System.IO.Path]::Combine($configDirectory, "game_path.txt")
    [System.IO.File]::WriteAllText(
        $pathFile,
        [System.IO.Path]::GetFullPath($GameRoot),
        (New-Object System.Text.UTF8Encoding($false))
    )
}

function Get-Ue4ssEngineVersionOverride {
    param([string]$SettingsPath)

    if (-not [System.IO.File]::Exists($SettingsPath)) {
        return $null
    }

    $major = $null
    $minor = $null
    $inSection = $false
    foreach ($line in [System.IO.File]::ReadAllLines($SettingsPath)) {
        $sectionMatch = [regex]::Match($line, '^\s*\[([^\]]+)\]\s*$')
        if ($sectionMatch.Success) {
            $inSection = $sectionMatch.Groups[1].Value -eq "EngineVersionOverride"
            continue
        }
        if (-not $inSection) {
            continue
        }
        $valueMatch = [regex]::Match($line, '^\s*(MajorVersion|MinorVersion)\s*=\s*([^;\s]*)')
        if (-not $valueMatch.Success) {
            continue
        }
        if ($valueMatch.Groups[1].Value -eq "MajorVersion") {
            $major = $valueMatch.Groups[2].Value
        }
        else {
            $minor = $valueMatch.Groups[2].Value
        }
    }

    if ([string]::IsNullOrWhiteSpace($major) -or [string]::IsNullOrWhiteSpace($minor)) {
        return $null
    }
    return "{0}.{1}" -f $major, $minor
}

function Set-Ue4ssEngineVersionOverride {
    param(
        [string]$SettingsPath,
        [int]$MajorVersion = 4,
        [int]$MinorVersion = 26,
        [switch]$CreateBackup
    )

    if (-not [System.IO.File]::Exists($SettingsPath)) {
        throw "UE4SS settings file does not exist: $SettingsPath"
    }

    $original = [System.IO.File]::ReadAllText($SettingsPath)
    $newline = if ($original.Contains("`r`n")) { "`r`n" } else { "`n" }
    $lines = @([regex]::Split($original, '\r?\n'))
    $result = New-Object System.Collections.Generic.List[string]
    $sectionFound = $false
    $inSection = $false
    $majorFound = $false
    $minorFound = $false

    foreach ($line in $lines) {
        $sectionMatch = [regex]::Match($line, '^\s*\[([^\]]+)\]\s*$')
        if ($sectionMatch.Success) {
            if ($inSection) {
                if (-not $majorFound) {
                    $result.Add("MajorVersion = $MajorVersion")
                }
                if (-not $minorFound) {
                    $result.Add("MinorVersion = $MinorVersion")
                }
            }
            $inSection = $sectionMatch.Groups[1].Value -eq "EngineVersionOverride"
            if ($inSection) {
                $sectionFound = $true
                $majorFound = $false
                $minorFound = $false
            }
            $result.Add($line)
            continue
        }

        if ($inSection -and $line -match '^\s*MajorVersion\s*=') {
            $result.Add("MajorVersion = $MajorVersion")
            $majorFound = $true
            continue
        }
        if ($inSection -and $line -match '^\s*MinorVersion\s*=') {
            $result.Add("MinorVersion = $MinorVersion")
            $minorFound = $true
            continue
        }
        $result.Add($line)
    }

    if ($inSection) {
        if (-not $majorFound) {
            $result.Add("MajorVersion = $MajorVersion")
        }
        if (-not $minorFound) {
            $result.Add("MinorVersion = $MinorVersion")
        }
    }
    elseif (-not $sectionFound) {
        if ($result.Count -gt 0 -and $result[$result.Count - 1] -ne "") {
            $result.Add("")
        }
        $result.Add("[EngineVersionOverride]")
        $result.Add("MajorVersion = $MajorVersion")
        $result.Add("MinorVersion = $MinorVersion")
        $result.Add("DebugBuild =")
    }

    $updated = [string]::Join($newline, $result)
    if ($updated -eq $original) {
        return $false
    }

    if ($CreateBackup) {
        $backupPath = $SettingsPath + ".wsvoice-backup"
        if (-not [System.IO.File]::Exists($backupPath)) {
            [System.IO.File]::Copy($SettingsPath, $backupPath, $false)
        }
    }
    [System.IO.File]::WriteAllText(
        $SettingsPath,
        $updated,
        (New-Object System.Text.UTF8Encoding($false))
    )
    return $true
}

function Read-SharedTextFile {
    param([string]$Path)

    if (-not [System.IO.File]::Exists($Path)) {
        return $null
    }
    $stream = $null
    $reader = $null
    try {
        $stream = New-Object System.IO.FileStream(
            $Path,
            [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::Read,
            ([System.IO.FileShare]::ReadWrite -bor [System.IO.FileShare]::Delete)
        )
        $reader = New-Object System.IO.StreamReader($stream, [System.Text.Encoding]::UTF8)
        return $reader.ReadToEnd()
    }
    finally {
        if ($null -ne $reader) {
            $reader.Dispose()
        }
        if ($null -ne $stream) {
            $stream.Dispose()
        }
    }
}

function Get-Ue4ssRuntimeDiagnostic {
    param([string]$GameRoot)

    $logPath = Get-Ue4ssLogPath $GameRoot
    if (-not [System.IO.File]::Exists($logPath)) {
        return [PSCustomObject]@{
            Code = "LogMissing"
            Message = "UE4SS.log has not been created. UE4SS may not have loaded yet."
            LogPath = $logPath
        }
    }

    $content = Read-SharedTextFile $logPath
    $sessionMarker = $content.LastIndexOf("Console created", [System.StringComparison]::Ordinal)
    if ($sessionMarker -gt 0) {
        $content = $content.Substring($sessionMarker)
    }
    if ($content -match 'Failed to find EngineVersion' -or $content -match 'override the engine version') {
        return [PSCustomObject]@{
            Code = "EngineVersionMissing"
            Message = "UE4SS did not read the required Unreal Engine 4.26 override."
            LogPath = $logPath
        }
    }
    if ($content -match 'Fatal Error:\s*PS scan timed out') {
        return [PSCustomObject]@{
            Code = "ScanTimedOut"
            Message = "UE4SS signature scanning timed out before the voice bridge could start."
            LogPath = $logPath
        }
    }
    if ($content -match '\[WSVOICE\] native dialogue bridge ready') {
        return [PSCustomObject]@{
            Code = "BridgeReady"
            Message = "The game dialogue bridge started successfully."
            LogPath = $logPath
        }
    }
    if ($content -match "Starting Lua mod 'WanderingSwordVoiceProbe'") {
        return [PSCustomObject]@{
            Code = "BridgeStarting"
            Message = "UE4SS found the voice bridge and is starting it."
            LogPath = $logPath
        }
    }
    if ($content -match 'PS scan successful') {
        return [PSCustomObject]@{
            Code = "Ue4ssReady"
            Message = "UE4SS initialized, but the voice bridge has not confirmed startup."
            LogPath = $logPath
        }
    }
    return [PSCustomObject]@{
        Code = "Unknown"
        Message = "UE4SS.log exists, but startup has not completed or contains an unknown error."
        LogPath = $logPath
    }
}

function Enable-VoiceProbeMod {
    param([string]$ModsFile)
    $lines = @()
    if ([System.IO.File]::Exists($ModsFile)) {
        $lines = @([System.IO.File]::ReadAllLines($ModsFile))
    }
    $filtered = @($lines | Where-Object {
        $_ -notmatch '^\s*WanderingSwordVoiceProbe\s*:'
    })
    $updated = @($filtered) + "WanderingSwordVoiceProbe : 1"
    [System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($ModsFile)) | Out-Null
    [System.IO.File]::WriteAllLines(
        $ModsFile,
        $updated,
        (New-Object System.Text.UTF8Encoding($false))
    )
}

function Disable-VoiceProbeMod {
    param([string]$ModsFile)
    if (-not [System.IO.File]::Exists($ModsFile)) {
        return
    }
    $filtered = @([System.IO.File]::ReadAllLines($ModsFile) | Where-Object {
        $_ -notmatch '^\s*WanderingSwordVoiceProbe\s*:'
    })
    [System.IO.File]::WriteAllLines(
        $ModsFile,
        $filtered,
        (New-Object System.Text.UTF8Encoding($false))
    )
}

function Write-CheckLine {
    param(
        [bool]$Success,
        [string]$Message
    )
    $label = if ($Success) { "OK" } else { "FAIL" }
    $color = if ($Success) { "Green" } else { "Red" }
    Write-Host ("[{0}] {1}" -f $label, $Message) -ForegroundColor $color
}

function Write-InfoLine {
    param([string]$Message)
    Write-Host ("[INFO] {0}" -f $Message) -ForegroundColor Cyan
}

function Write-WarnLine {
    param([string]$Message)
    Write-Host ("[WARN] {0}" -f $Message) -ForegroundColor Yellow
}

function ConvertTo-PlaybackSpeed {
    param([string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) {
        throw "Playback speed cannot be empty."
    }
    $normalized = $Value.Trim().Replace(',', '.')
    [double]$speed = 0.0
    $parsed = [double]::TryParse(
        $normalized,
        [System.Globalization.NumberStyles]::Float,
        [System.Globalization.CultureInfo]::InvariantCulture,
        [ref]$speed
    )
    if (-not $parsed -or [double]::IsNaN($speed) -or [double]::IsInfinity($speed)) {
        throw "Invalid playback speed: $Value"
    }
    if ($speed -lt 1.0 -or $speed -gt 1.5) {
        throw "Playback speed must be between 1.00 and 1.50."
    }
    return [math]::Round($speed, 2)
}

function Get-PlaybackSpeedPath {
    param([string]$ReleaseRoot)
    return [System.IO.Path]::Combine($ReleaseRoot, "config", "playback_speed.txt")
}

function Get-PlaybackSpeedSetting {
    param([string]$ReleaseRoot)

    $path = Get-PlaybackSpeedPath $ReleaseRoot
    if (-not [System.IO.File]::Exists($path)) {
        return [double]1.0
    }
    try {
        return [double](ConvertTo-PlaybackSpeed ([System.IO.File]::ReadAllText($path)))
    }
    catch {
        Write-WarnLine ("Ignoring invalid playback speed setting: {0}" -f $_.Exception.Message)
        return [double]1.0
    }
}

function Save-PlaybackSpeedSetting {
    param(
        [string]$ReleaseRoot,
        [double]$Speed
    )

    $validated = ConvertTo-PlaybackSpeed (
        $Speed.ToString("0.00", [System.Globalization.CultureInfo]::InvariantCulture)
    )
    $path = Get-PlaybackSpeedPath $ReleaseRoot
    [System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($path)) | Out-Null
    [System.IO.File]::WriteAllText(
        $path,
        $validated.ToString("0.00", [System.Globalization.CultureInfo]::InvariantCulture) + [Environment]::NewLine,
        (New-Object System.Text.UTF8Encoding($false))
    )
    return $path
}
