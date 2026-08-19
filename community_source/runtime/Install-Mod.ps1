param(
    [string]$GamePath = ""
)

. (Join-Path $PSScriptRoot "Common.ps1")

try {
    $releaseRoot = Get-ReleaseRoot
    $gameRoot = Resolve-GameRoot -RequestedPath $GamePath -AllowPrompt
    if ($null -eq $gameRoot) {
        throw "Wandering Sword installation was not found."
    }

    $win64 = Get-GameWin64Path $gameRoot
    $payloadRoot = [System.IO.Path]::Combine($releaseRoot, "payload")
    $runtimePayload = [System.IO.Path]::Combine($payloadRoot, "ue4ss")
    $modPayload = [System.IO.Path]::Combine($payloadRoot, "WanderingSwordVoiceProbe")
    $targetUe4ss = [System.IO.Path]::Combine($win64, "ue4ss")
    $targetDll = [System.IO.Path]::Combine($targetUe4ss, "UE4SS.dll")
    $targetProxy = [System.IO.Path]::Combine($win64, "dwmapi.dll")
    $targetSettings = [System.IO.Path]::Combine($targetUe4ss, "UE4SS-settings.ini")
    $settingsAlreadyExisted = [System.IO.File]::Exists($targetSettings)

    foreach ($required in @(
        [System.IO.Path]::Combine($runtimePayload, "dwmapi.dll"),
        [System.IO.Path]::Combine($runtimePayload, "ue4ss", "UE4SS.dll"),
        [System.IO.Path]::Combine($runtimePayload, "ue4ss", "UE4SS-settings.ini"),
        [System.IO.Path]::Combine($modPayload, "Scripts", "main.lua")
    )) {
        if (-not [System.IO.File]::Exists($required)) {
            throw "Release package is incomplete: $required"
        }
    }

    $installedRuntime = $false
    if (-not [System.IO.File]::Exists($targetDll)) {
        if ([System.IO.File]::Exists($targetProxy)) {
            throw "A different dwmapi.dll already exists beside the game executable. Back it up or confirm that it belongs to UE4SS before installing."
        }
        [System.IO.Directory]::CreateDirectory($targetUe4ss) | Out-Null
        Copy-Item -LiteralPath ([System.IO.Path]::Combine($runtimePayload, "dwmapi.dll")) -Destination $targetProxy
        Copy-Item -LiteralPath ([System.IO.Path]::Combine($runtimePayload, "ue4ss", "UE4SS.dll")) -Destination $targetDll
        Copy-Item -LiteralPath ([System.IO.Path]::Combine($runtimePayload, "ue4ss", "UE4SS-settings.ini")) -Destination ([System.IO.Path]::Combine($targetUe4ss, "UE4SS-settings.ini"))
        Copy-Item -LiteralPath ([System.IO.Path]::Combine($runtimePayload, "ue4ss", "LICENSE")) -Destination ([System.IO.Path]::Combine($targetUe4ss, "LICENSE"))
        [System.IO.File]::WriteAllText(
            [System.IO.Path]::Combine($targetUe4ss, "wsvoice_installed_ue4ss.marker"),
            "UE4SS runtime installed by Wandering Sword Voice Mod.",
            [System.Text.Encoding]::ASCII
        )
        $installedRuntime = $true
    }
    elseif (-not [System.IO.File]::Exists($targetProxy)) {
        Copy-Item -LiteralPath ([System.IO.Path]::Combine($runtimePayload, "dwmapi.dll")) -Destination $targetProxy
    }

    if (-not [System.IO.File]::Exists($targetSettings)) {
        Copy-Item -LiteralPath ([System.IO.Path]::Combine($runtimePayload, "ue4ss", "UE4SS-settings.ini")) -Destination $targetSettings
    }
    $settingsChanged = Set-Ue4ssEngineVersionOverride `
        -SettingsPath $targetSettings `
        -MajorVersion 4 `
        -MinorVersion 26 `
        -CreateBackup:$settingsAlreadyExisted
    $configuredVersion = Get-Ue4ssEngineVersionOverride $targetSettings
    if ($configuredVersion -ne "4.26") {
        throw "Could not configure UE4SS engine version override to 4.26: $targetSettings"
    }

    $targetMod = [System.IO.Path]::Combine($targetUe4ss, "Mods", "WanderingSwordVoiceProbe")
    $targetScripts = [System.IO.Path]::Combine($targetMod, "Scripts")
    [System.IO.Directory]::CreateDirectory($targetScripts) | Out-Null
    Copy-Item -LiteralPath ([System.IO.Path]::Combine($modPayload, "Scripts", "main.lua")) -Destination ([System.IO.Path]::Combine($targetScripts, "main.lua")) -Force

    $modsFile = [System.IO.Path]::Combine($targetUe4ss, "Mods", "mods.txt")
    Enable-VoiceProbeMod $modsFile
    Save-GameRoot $gameRoot

    Write-Host ""
    Write-Host "Installation completed." -ForegroundColor Green
    Write-Host "Game: $gameRoot"
    if ($installedRuntime) {
        Write-Host "UE4SS runtime was installed with the voice bridge."
    }
    else {
        Write-Host "Existing UE4SS installation was preserved; only the voice bridge was updated."
    }
    if ($settingsChanged) {
        Write-Host "UE4SS engine version was configured automatically: 4.26" -ForegroundColor Green
    }
    else {
        Write-Host "UE4SS engine version is already configured: 4.26" -ForegroundColor Green
    }
    Write-Host "Run launcher 2 (voice + game) or launcher 3 (voice only)."
    exit 0
}
catch {
    Write-Host ""
    Write-Host ("Installation failed: {0}" -f $_.Exception.Message) -ForegroundColor Red
    Write-Host "If the Steam library is under Program Files, run Install-Mod.cmd as administrator."
    exit 1
}
