param(
    [string]$GamePath = "",
    [switch]$ReplayExisting,
    [switch]$LaunchGame,
    [switch]$SelfTest,
    [switch]$DryRun,
    [switch]$Once,
    [string]$PlaybackSpeed = "",
    [int]$PollMilliseconds = 80
)

. (Join-Path $PSScriptRoot "Common.ps1")

[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding

if ($PollMilliseconds -lt 20) {
    $PollMilliseconds = 20
}

$createdNew = $false
$mutex = New-Object System.Threading.Mutex($true, "Local\WanderingSwordVoicePlayer", [ref]$createdNew)
if (-not $createdNew) {
    Write-Host "Another voice player is already running." -ForegroundColor Yellow
    exit 2
}

Add-Type -TypeDefinition @"
using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
public static class WanderingSwordNativeAudio
{
    [DllImport("winmm.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern bool PlaySound(string pszSound, IntPtr hmod, uint fdwSound);
}

public static class WanderingSwordTimeStretch
{
    private sealed class WaveData
    {
        public int SampleRate;
        public short[] Samples;
    }

    public static void CreateFasterWave(string sourcePath, string destinationPath, double speed)
    {
        if (speed < 1.0 || speed > 1.5)
        {
            throw new ArgumentOutOfRangeException("speed", "Speed must be between 1.00 and 1.50.");
        }
        WaveData wave = ReadPcm16MonoWave(sourcePath);
        short[] output = Stretch(wave.Samples, speed, wave.SampleRate);
        WritePcm16MonoWave(destinationPath, wave.SampleRate, output);
    }

    private static string ReadFourCc(BinaryReader reader)
    {
        byte[] value = reader.ReadBytes(4);
        if (value.Length != 4)
        {
            throw new EndOfStreamException();
        }
        return Encoding.ASCII.GetString(value);
    }

    private static WaveData ReadPcm16MonoWave(string path)
    {
        using (FileStream stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read))
        using (BinaryReader reader = new BinaryReader(stream, Encoding.ASCII))
        {
            if (ReadFourCc(reader) != "RIFF")
            {
                throw new InvalidDataException("The audio file is not RIFF WAV.");
            }
            reader.ReadUInt32();
            if (ReadFourCc(reader) != "WAVE")
            {
                throw new InvalidDataException("The audio file is not WAVE.");
            }

            short format = 0;
            short channels = 0;
            int sampleRate = 0;
            short bitsPerSample = 0;
            byte[] pcmBytes = null;

            while (stream.Position + 8 <= stream.Length)
            {
                string chunkId = ReadFourCc(reader);
                uint chunkSizeValue = reader.ReadUInt32();
                if (chunkSizeValue > Int32.MaxValue)
                {
                    throw new InvalidDataException("WAV chunk is too large.");
                }
                int chunkSize = (int)chunkSizeValue;
                long chunkEnd = stream.Position + chunkSize;
                if (chunkEnd > stream.Length)
                {
                    throw new InvalidDataException("WAV chunk is truncated.");
                }

                if (chunkId == "fmt ")
                {
                    if (chunkSize < 16)
                    {
                        throw new InvalidDataException("Invalid WAV format chunk.");
                    }
                    format = reader.ReadInt16();
                    channels = reader.ReadInt16();
                    sampleRate = reader.ReadInt32();
                    reader.ReadInt32();
                    reader.ReadInt16();
                    bitsPerSample = reader.ReadInt16();
                }
                else if (chunkId == "data")
                {
                    pcmBytes = reader.ReadBytes(chunkSize);
                    if (pcmBytes.Length != chunkSize)
                    {
                        throw new InvalidDataException("WAV audio data is truncated.");
                    }
                }

                stream.Position = chunkEnd + (chunkSize % 2);
            }

            if (format != 1 || channels != 1 || bitsPerSample != 16 || sampleRate <= 0 || pcmBytes == null)
            {
                throw new InvalidDataException("Only PCM16 mono WAV audio is supported.");
            }
            int sampleCount = pcmBytes.Length / 2;
            short[] samples = new short[sampleCount];
            Buffer.BlockCopy(pcmBytes, 0, samples, 0, sampleCount * 2);
            WaveData result = new WaveData();
            result.SampleRate = sampleRate;
            result.Samples = samples;
            return result;
        }
    }

    private static short[] Stretch(short[] input, double speed, int sampleRate)
    {
        if (speed <= 1.001 || input.Length < 128)
        {
            return (short[])input.Clone();
        }

        int targetLength = Math.Max(1, (int)Math.Round(input.Length / speed));
        int window = Math.Min(1200, input.Length / 2);
        if ((window & 1) != 0)
        {
            window--;
        }
        if (window < 64 || targetLength <= 1)
        {
            return (short[])input.Clone();
        }

        int overlap = window / 2;
        int synthesisHop = window - overlap;
        double analysisHop = synthesisHop * speed;
        int searchRadius = Math.Min(Math.Max(24, sampleRate / 100), Math.Max(24, window / 2));
        int maxCandidate = input.Length - window;
        short[] working = new short[targetLength + window];
        int firstLength = Math.Min(window, targetLength);
        Array.Copy(input, 0, working, 0, firstLength);

        int outputPosition = synthesisHop;
        double expectedInputPosition = analysisHop;
        while (outputPosition < targetLength)
        {
            int expected = (int)Math.Round(expectedInputPosition);
            expected = Math.Max(0, Math.Min(maxCandidate, expected));
            int searchStart = Math.Max(0, expected - searchRadius);
            int searchEnd = Math.Min(maxCandidate, expected + searchRadius);
            int compareLength = Math.Min(overlap, targetLength - outputPosition);
            int bestCandidate = expected;
            long bestScore = Int64.MaxValue;

            for (int candidate = searchStart; candidate <= searchEnd; candidate += 3)
            {
                long score = 0;
                for (int index = 0; index < compareLength; index += 4)
                {
                    long difference = (long)working[outputPosition + index] - input[candidate + index];
                    score += difference * difference;
                    if (score >= bestScore)
                    {
                        break;
                    }
                }
                if (score < bestScore)
                {
                    bestScore = score;
                    bestCandidate = candidate;
                }
            }

            int blendLength = Math.Min(overlap, targetLength - outputPosition);
            for (int index = 0; index < blendLength; index++)
            {
                double incomingWeight = (index + 1.0) / (blendLength + 1.0);
                double mixed = working[outputPosition + index] * (1.0 - incomingWeight)
                    + input[bestCandidate + index] * incomingWeight;
                working[outputPosition + index] = (short)Math.Max(
                    Int16.MinValue,
                    Math.Min(Int16.MaxValue, Math.Round(mixed))
                );
            }

            for (int index = overlap; index < window; index++)
            {
                int destination = outputPosition + index;
                if (destination >= targetLength)
                {
                    break;
                }
                working[destination] = input[bestCandidate + index];
            }

            outputPosition += synthesisHop;
            expectedInputPosition += analysisHop;
        }

        short[] result = new short[targetLength];
        Array.Copy(working, result, targetLength);
        return result;
    }

    private static void WritePcm16MonoWave(string path, int sampleRate, short[] samples)
    {
        string directory = Path.GetDirectoryName(path);
        if (!String.IsNullOrEmpty(directory))
        {
            Directory.CreateDirectory(directory);
        }
        string temporaryPath = path + ".tmp";
        using (FileStream stream = new FileStream(temporaryPath, FileMode.Create, FileAccess.Write, FileShare.None))
        using (BinaryWriter writer = new BinaryWriter(stream, Encoding.ASCII))
        {
            int dataSize = checked(samples.Length * 2);
            writer.Write(Encoding.ASCII.GetBytes("RIFF"));
            writer.Write(checked(36 + dataSize));
            writer.Write(Encoding.ASCII.GetBytes("WAVE"));
            writer.Write(Encoding.ASCII.GetBytes("fmt "));
            writer.Write(16);
            writer.Write((short)1);
            writer.Write((short)1);
            writer.Write(sampleRate);
            writer.Write(checked(sampleRate * 2));
            writer.Write((short)2);
            writer.Write((short)16);
            writer.Write(Encoding.ASCII.GetBytes("data"));
            writer.Write(dataSize);
            byte[] bytes = new byte[dataSize];
            Buffer.BlockCopy(samples, 0, bytes, 0, dataSize);
            writer.Write(bytes);
        }
        if (File.Exists(path))
        {
            File.Delete(path);
        }
        File.Move(temporaryPath, path);
    }
}
"@

$SND_ASYNC = 0x0001
$SND_NODEFAULT = 0x0002
$SND_FILENAME = 0x00020000
$releaseRoot = Get-ReleaseRoot
$script:PlaybackSpeed = if ([string]::IsNullOrWhiteSpace($PlaybackSpeed)) {
    [double](Get-PlaybackSpeedSetting $releaseRoot)
}
else {
    try {
        [double](ConvertTo-PlaybackSpeed $PlaybackSpeed)
    }
    catch {
        Write-Host ("Invalid playback speed: {0}" -f $_.Exception.Message) -ForegroundColor Red
        exit 6
    }
}
$script:SpeedAdjustedAudio = [System.IO.Path]::Combine(
    [System.IO.Path]::GetTempPath(),
    ("WanderingSwordVoiceMod-speed-{0}.wav" -f $PID)
)
$dataRoot = [System.IO.Path]::Combine($releaseRoot, "data")
$lookupPath = [System.IO.Path]::Combine($dataRoot, "runtime_lookup.compact.json")
$logDirectory = [System.IO.Path]::Combine($releaseRoot, "logs")
$missLog = [System.IO.Path]::Combine($logDirectory, "unmatched_dialogue.jsonl")
$script:ProcessedDialogues = 0

function Stop-CurrentVoice {
    [void][WanderingSwordNativeAudio]::PlaySound($null, [IntPtr]::Zero, 0)
}

function Remove-SpeedAdjustedAudio {
    foreach ($path in @($script:SpeedAdjustedAudio, $script:SpeedAdjustedAudio + ".tmp")) {
        try {
            if ([System.IO.File]::Exists($path)) {
                [System.IO.File]::Delete($path)
            }
        }
        catch {
        }
    }
}

function Remove-StaleSpeedAdjustedAudio {
    try {
        foreach ($path in [System.IO.Directory]::GetFiles(
            [System.IO.Path]::GetTempPath(),
            "WanderingSwordVoiceMod-speed-*.wav*"
        )) {
            if (-not $path.Equals($script:SpeedAdjustedAudio, [System.StringComparison]::OrdinalIgnoreCase)) {
                try {
                    [System.IO.File]::Delete($path)
                }
                catch {
                }
            }
        }
    }
    catch {
    }
}

function Get-PlaybackAudioPath {
    param([string]$SourcePath)

    if ($script:PlaybackSpeed -le 1.001) {
        return $SourcePath
    }
    [WanderingSwordTimeStretch]::CreateFasterWave(
        $SourcePath,
        $script:SpeedAdjustedAudio,
        $script:PlaybackSpeed
    )
    return $script:SpeedAdjustedAudio
}

Remove-StaleSpeedAdjustedAudio

function Normalize-DialogueText {
    param([AllowEmptyString()][string]$Value)
    if ($null -eq $Value) {
        return ""
    }
    $normalized = $Value.Normalize([System.Text.NormalizationForm]::FormC)
    $normalized = $normalized.Replace("`r`n", "`n").Replace("`r", "`n")
    $normalized = [regex]::Replace($normalized, "[ `t`f`v]+", " ")
    $normalized = [regex]::Replace($normalized, " *`n *", "`n")
    return $normalized.Trim()
}

function Normalize-DialogueTextCompat {
    param([AllowEmptyString()][string]$Value)
    if ($null -eq $Value) {
        return ""
    }
    $normalized = $Value.Normalize([System.Text.NormalizationForm]::FormKC)
    $normalized = $normalized.Replace("`r`n", "`n").Replace("`r", "`n")
    $normalized = [regex]::Replace($normalized, "[ `t`f`v]+", " ")
    $normalized = [regex]::Replace($normalized, " *`n *", "`n")
    return $normalized.Trim()
}

function Get-LookupKey {
    param(
        [string]$Speaker,
        [string]$Text
    )
    $material = $Speaker + [char]0 + $Text
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($material)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $digest = $sha.ComputeHash($bytes)
    }
    finally {
        $sha.Dispose()
    }
    return ([System.BitConverter]::ToString($digest)).Replace("-", "").ToLowerInvariant()
}

function Get-TextLookupKey {
    param([string]$Text)
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($Text)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $digest = $sha.ComputeHash($bytes)
    }
    finally {
        $sha.Dispose()
    }
    return ([System.BitConverter]::ToString($digest)).Replace("-", "").ToLowerInvariant()
}

if (-not [System.IO.File]::Exists($lookupPath)) {
    Write-Host "Voice lookup is missing: $lookupPath" -ForegroundColor Red
    exit 3
}

Write-Host "Loading offline voice index..."
$lookupDocument = [System.IO.File]::ReadAllText($lookupPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
$exact = @{}
foreach ($property in @($lookupDocument.exact.PSObject.Properties)) {
    $exact[$property.Name] = [string]$property.Value
}
$textFallback = @{}
if ($null -ne $lookupDocument.text_fallback) {
    foreach ($property in @($lookupDocument.text_fallback.PSObject.Properties)) {
        $textFallback[$property.Name] = [string]$property.Value
    }
}
$prefixes = @($lookupDocument.prefix | Sort-Object { $_.text_prefix.Length } -Descending)

$gameRoot = Resolve-GameRoot -RequestedPath $GamePath -AllowPrompt
if ($null -eq $gameRoot) {
    Write-Host "Wandering Sword installation was not found. Run Install-Mod.cmd first." -ForegroundColor Red
    exit 4
}
if (-not $SelfTest) {
    Save-GameRoot $gameRoot
}
$eventPath = [System.IO.Path]::Combine(
    (Get-GameWin64Path $gameRoot),
    "ue4ss",
    "Mods",
    "WanderingSwordVoiceProbe",
    "dialogue_events.jsonl"
)

if ($SelfTest) {
    $sample = @($exact.Values)[0]
    $samplePath = [System.IO.Path]::Combine($dataRoot, $sample.Replace('/', '\'))
    $sampleExists = [System.IO.File]::Exists($samplePath)
    $speedTestPassed = $sampleExists
    $speedSamplePath = $samplePath
    if ($sampleExists -and $script:PlaybackSpeed -gt 1.001) {
        try {
            $speedSamplePath = Get-PlaybackAudioPath $samplePath
            $speedTestPassed = (
                [System.IO.File]::Exists($speedSamplePath) -and
                (Get-Item -LiteralPath $speedSamplePath).Length -gt 44
            )
        }
        catch {
            $speedTestPassed = $false
            Write-Host ("Playback speed self-test failed: {0}" -f $_.Exception.Message) -ForegroundColor Red
        }
    }
    Write-CheckLine ($exact.Count -gt 40000) ("Exact lookup entries: {0}" -f $exact.Count)
    Write-CheckLine ($textFallback.Count -gt 30000) ("Text fallback entries: {0}" -f $textFallback.Count)
    Write-CheckLine ($prefixes.Count -gt 0) ("Prefix lookup entries: {0}" -f $prefixes.Count)
    Write-CheckLine $sampleExists ("Sample audio: {0}" -f $samplePath)
    Write-CheckLine $speedTestPassed ("Playback speed: {0:0.00}x" -f $script:PlaybackSpeed)
    Write-CheckLine (Test-GameRoot $gameRoot) ("Game: {0}" -f $gameRoot)
    Remove-SpeedAdjustedAudio
    if ($exact.Count -gt 40000 -and $textFallback.Count -gt 30000 -and $prefixes.Count -gt 0 -and $sampleExists -and $speedTestPassed) {
        Write-Host "Player self-test passed." -ForegroundColor Green
        exit 0
    }
    exit 5
}

[System.IO.Directory]::CreateDirectory($logDirectory) | Out-Null

function Process-DialogueEvent {
    param([string]$Line)
    if ([string]::IsNullOrWhiteSpace($Line)) {
        return
    }
    try {
        $eventValue = $Line | ConvertFrom-Json
    }
    catch {
        return
    }
    if ([string]$eventValue.event -ne "dialogue") {
        return
    }
    $speaker = Normalize-DialogueText ([string]$eventValue.speaker)
    if ([string]::IsNullOrWhiteSpace($speaker)) {
        $speaker = [string]([char]0x65C1) + [char]0x767D
    }
    $text = Normalize-DialogueText ([string]$eventValue.text)
    if ([string]::IsNullOrWhiteSpace($text)) {
        return
    }
    $script:ProcessedDialogues++

    $key = Get-LookupKey $speaker $text
    $relativeAudio = $null
    if ($exact.ContainsKey($key)) {
        $relativeAudio = [string]$exact[$key]
    }
    else {
        $compatSpeaker = Normalize-DialogueTextCompat $speaker
        $compatText = Normalize-DialogueTextCompat $text
        $compatKey = Get-LookupKey $compatSpeaker $compatText
        if ($exact.ContainsKey($compatKey)) {
            $relativeAudio = [string]$exact[$compatKey]
        }
    }
    if ([string]::IsNullOrWhiteSpace($relativeAudio)) {
        $textOnlyKey = Get-TextLookupKey $text
        if ($textFallback.ContainsKey($textOnlyKey)) {
            $relativeAudio = [string]$textFallback[$textOnlyKey]
        }
        elseif ($compatText -ne $text) {
            $textOnlyKey = Get-TextLookupKey $compatText
            if ($textFallback.ContainsKey($textOnlyKey)) {
                $relativeAudio = [string]$textFallback[$textOnlyKey]
            }
        }
    }
    if ([string]::IsNullOrWhiteSpace($relativeAudio)) {
        foreach ($item in $prefixes) {
            $prefixSpeaker = [string]$item.speaker
            $prefixText = [string]$item.text_prefix
            $primaryMatch = $prefixSpeaker -eq $speaker -and $text.StartsWith(
                $prefixText,
                [System.StringComparison]::Ordinal
            )
            $compatMatch = (
                (Normalize-DialogueTextCompat $prefixSpeaker) -eq $compatSpeaker -and
                $compatText.StartsWith(
                    (Normalize-DialogueTextCompat $prefixText),
                    [System.StringComparison]::Ordinal
                )
            )
            if ($primaryMatch -or $compatMatch) {
                $relativeAudio = [string]$item.audio_file
                break
            }
        }
    }

    if ([string]::IsNullOrWhiteSpace($relativeAudio)) {
        Write-Host ("[MISS] {0}: {1}" -f $speaker, $text) -ForegroundColor Yellow
        [System.IO.File]::AppendAllText(
            $missLog,
            $Line + [Environment]::NewLine,
            (New-Object System.Text.UTF8Encoding($false))
        )
        return
    }

    $audioPath = [System.IO.Path]::Combine($dataRoot, $relativeAudio.Replace('/', '\'))
    if (-not [System.IO.File]::Exists($audioPath)) {
        Write-Host ("[MISSING AUDIO] {0}: {1}" -f $speaker, $audioPath) -ForegroundColor Red
        return
    }
    Write-Host ("[PLAY] {0}: {1}" -f $speaker, $text)
    if (-not $DryRun) {
        Stop-CurrentVoice
        $playbackAudioPath = $audioPath
        try {
            $playbackAudioPath = Get-PlaybackAudioPath $audioPath
        }
        catch {
            Write-Host ("[SPEED FALLBACK] {0}" -f $_.Exception.Message) -ForegroundColor Yellow
            $playbackAudioPath = $audioPath
        }
        [void][WanderingSwordNativeAudio]::PlaySound(
            $playbackAudioPath,
            [IntPtr]::Zero,
            ($SND_FILENAME -bor $SND_ASYNC -bor $SND_NODEFAULT)
        )
    }
}

Write-Host ("[READY] {0} exact, {1} text fallback, {2} prefix lines." -f $exact.Count, $textFallback.Count, $prefixes.Count) -ForegroundColor Green
Write-Host "No AI model, Python, CUDA, or network connection is used."
Write-Host ("Playback speed: {0:0.00}x (run launcher 6 to change it)." -f $script:PlaybackSpeed)
Write-Host "Press Ctrl+C to stop the player."
Write-Host ("Event file: {0}" -f $eventPath)

if ($LaunchGame) {
    Write-Host "Launching Wandering Sword through Steam..."
    Start-Process "steam://rungameid/1876890"
}

$firstOpen = $true
$waitStarted = [DateTime]::UtcNow
$lastWaitNotice = [DateTime]::MinValue
try {
    while ($true) {
        while (-not [System.IO.File]::Exists($eventPath)) {
            $now = [DateTime]::UtcNow
            $elapsedSeconds = ($now - $waitStarted).TotalSeconds
            if ($lastWaitNotice -eq [DateTime]::MinValue) {
                Write-Host "Waiting for the game dialogue bridge..."
                $lastWaitNotice = $now
            }
            elseif ($elapsedSeconds -ge 8 -and ($now - $lastWaitNotice).TotalSeconds -ge 8) {
                $diagnostic = Get-Ue4ssRuntimeDiagnostic $gameRoot
                $diagnosticIsFresh = (
                    [System.IO.File]::Exists($diagnostic.LogPath) -and
                    [System.IO.File]::GetLastWriteTimeUtc($diagnostic.LogPath) -ge $waitStarted.AddSeconds(-2)
                )
                if (-not $diagnosticIsFresh -and $diagnostic.Code -ne "LogMissing") {
                    Write-InfoLine "Waiting for a new UE4SS.log from the current game launch."
                }
                elseif ($diagnostic.Code -eq "EngineVersionMissing" -or $diagnostic.Code -eq "ScanTimedOut") {
                    Write-Host "" 
                    Write-Host ("[BRIDGE ERROR] {0}" -f $diagnostic.Message) -ForegroundColor Red
                    Write-Host "Close the game, run launcher 1 to repair UE4SS, then start again." -ForegroundColor Yellow
                    Write-Host ("UE4SS log: {0}" -f $diagnostic.LogPath)
                    return
                }
                elseif ($diagnostic.Code -eq "LogMissing") {
                    Write-WarnLine "UE4SS.log has not appeared. The game may still be starting, or UE4SS was blocked by security software."
                }
                else {
                    Write-WarnLine ("Still waiting: {0}" -f $diagnostic.Message)
                    Write-Host ("UE4SS log: {0}" -f $diagnostic.LogPath)
                }
                $lastWaitNotice = $now
            }
            Start-Sleep -Milliseconds 250
        }

        Write-Host "[BRIDGE] Dialogue event file connected." -ForegroundColor Green

        $stream = $null
        $reader = $null
        try {
            $stream = New-Object System.IO.FileStream(
                $eventPath,
                [System.IO.FileMode]::Open,
                [System.IO.FileAccess]::Read,
                ([System.IO.FileShare]::ReadWrite -bor [System.IO.FileShare]::Delete)
            )
            $reader = New-Object System.IO.StreamReader(
                $stream,
                (New-Object System.Text.UTF8Encoding($false, $true)),
                $true,
                4096,
                $true
            )
            if ($firstOpen -and -not $ReplayExisting) {
                $reader.DiscardBufferedData()
                [void]$stream.Seek(0, [System.IO.SeekOrigin]::End)
            }
            $firstOpen = $false

            while ($true) {
                $line = $reader.ReadLine()
                if ($null -ne $line) {
                    Process-DialogueEvent $line
                    if ($Once -and $script:ProcessedDialogues -gt 0) {
                        return
                    }
                    continue
                }
                try {
                    $currentLength = (Get-Item -LiteralPath $eventPath).Length
                    if ($currentLength -lt $stream.Position) {
                        break
                    }
                }
                catch {
                    break
                }
                Start-Sleep -Milliseconds $PollMilliseconds
            }
        }
        catch {
            Write-Host ("Dialogue stream was reopened: {0}" -f $_.Exception.Message) -ForegroundColor Yellow
            Start-Sleep -Milliseconds 500
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
}
finally {
    Stop-CurrentVoice
    Remove-SpeedAdjustedAudio
    if ($null -ne $mutex) {
        $mutex.ReleaseMutex()
        $mutex.Dispose()
    }
}
