param(
    [string]$GamePath = ""
)

. (Join-Path $PSScriptRoot "Common.ps1")

try {
    $gameRoot = Resolve-GameRoot -RequestedPath $GamePath -AllowPrompt
    if ($null -eq $gameRoot) {
        throw "Wandering Sword installation was not found."
    }
    $win64 = Get-GameWin64Path $gameRoot
    $modsRoot = [System.IO.Path]::Combine($win64, "ue4ss", "Mods")
    $targetMod = [System.IO.Path]::Combine($modsRoot, "WanderingSwordVoiceProbe")
    $expectedParent = [System.IO.Path]::GetFullPath($modsRoot).TrimEnd('\') + '\'
    $resolvedTarget = [System.IO.Path]::GetFullPath($targetMod)
    if (-not $resolvedTarget.StartsWith($expectedParent, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove an unexpected path: $resolvedTarget"
    }
    if ([System.IO.Directory]::Exists($resolvedTarget)) {
        Remove-Item -LiteralPath $resolvedTarget -Recurse -Force
    }
    Disable-VoiceProbeMod ([System.IO.Path]::Combine($modsRoot, "mods.txt"))
    Write-Host "Voice bridge removed." -ForegroundColor Green
    Write-Host "UE4SS was left installed so other UE4SS mods are not damaged."
    Write-Host "You can delete this release folder after closing the player."
    exit 0
}
catch {
    Write-Host ("Uninstall failed: {0}" -f $_.Exception.Message) -ForegroundColor Red
    exit 1
}
