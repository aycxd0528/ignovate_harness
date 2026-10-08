#Requires -Version 5.1
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$OutputEncoding = New-Object Text.UTF8Encoding $false
[Console]::OutputEncoding = $OutputEncoding
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Assert-IgnovateWindows {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or [int](Get-CimInstance Win32_OperatingSystem).BuildNumber -lt 22000) {
        throw 'This installer supports Windows 11 (build 22000 or newer).'
    }
}
function Get-IgnovateVersion([string]$Directory) {
    $version = [IO.File]::ReadAllText((Join-Path $Directory 'VERSION')).Trim()
    if ($version -notmatch '^\d+\.\d+\.\d+(?:[a-zA-Z0-9.+-]*)$') { throw 'Invalid release version.' }
    return $version
}
function Assert-IgnovateBundle([string]$Directory) {
    foreach ($line in Get-Content -LiteralPath (Join-Path $Directory 'SHA256SUMS')) {
        if ($line -notmatch '^([a-f0-9]{64})  ([A-Za-z0-9_.-]+)$') { throw 'Invalid release checksum manifest.' }
        $expected, $name = $Matches[1], $Matches[2]
        $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $Directory $name)).Hash.ToLowerInvariant()
        if ($actual -ne $expected) { throw "Checksum failed: $name. Download the release again." }
    }
}
function Get-IgnovateDownload([string]$Url, [string]$Destination) {
    if (-not $Url.StartsWith('https://')) { throw 'Downloads require HTTPS.' }
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        try { Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $Destination -MaximumRedirection 5; return }
        catch { if ($attempt -eq 3) { throw }; Start-Sleep -Seconds 1 }
    }
}
function Invoke-IgnovateNative([string]$Executable, [string[]]$Arguments) {
    # Avoid WinPS 5.1's lossy native argument serializer.
    $serialized = foreach ($value in $Arguments) {
        $escaped = [Regex]::Replace([string]$value, '(\\*)"', { param($match) ($match.Groups[1].Value * 2) + '\"' })
        $escaped = [Regex]::Replace($escaped, '(\\+)$', { param($match) $match.Value * 2 })
        '"' + $escaped + '"'
    }
    $start = New-Object Diagnostics.ProcessStartInfo
    $start.FileName = $Executable
    $start.Arguments = $serialized -join ' '
    $start.WorkingDirectory = (Get-Location).ProviderPath
    $start.UseShellExecute = $false
    $process = [Diagnostics.Process]::Start($start)
    try { $process.WaitForExit(); return $process.ExitCode }
    finally { $process.Dispose() }
}
function Invoke-IgnovateChecked([string]$Executable, [string[]]$Arguments) {
    $code = Invoke-IgnovateNative $Executable $Arguments
    if ($code -ne 0) { throw "Environment preparation failed (exit $code): $Executable" }
}
