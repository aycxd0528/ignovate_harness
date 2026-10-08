#Requires -Version 5.1
param(
    [ValidatePattern('^[A-Za-z0-9._-]+$')][string]$Distribution = 'Ubuntu',
    [string]$BinDirectory = $(if ($env:IGNOVATE_WINDOWS_BIN_DIR) { $env:IGNOVATE_WINDOWS_BIN_DIR } else { Join-Path $env:LOCALAPPDATA 'Ignovate\bin' })
)
$ErrorActionPreference = 'Stop'
$OutputEncoding = New-Object Text.UTF8Encoding $false
[Console]::OutputEncoding = $OutputEncoding

# Verify the local release before requesting WSL installation or copying files.
foreach ($line in Get-Content -LiteralPath (Join-Path $PSScriptRoot 'SHA256SUMS')) {
    if ($line -notmatch '^([a-f0-9]{64})  ([A-Za-z0-9_.-]+)$') { throw 'Invalid release checksum manifest.' }
    $expected = $Matches[1]
    $name = $Matches[2]
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $PSScriptRoot $name)).Hash.ToLowerInvariant()
    if ($actual -ne $expected) { throw "Checksum failed: $name. Download the release again." }
}

if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    throw 'WSL requires Windows 10 2004+ or Windows 11. Update Windows, then run this installer again.'
}
$distros = @(& wsl.exe --list --quiet 2>$null | ForEach-Object { ($_ -replace "`0", '').Trim() })
if ($LASTEXITCODE -ne 0 -or $distros -notcontains $Distribution) {
    Write-Host "Installing WSL2 and $Distribution. Windows may request administrator access."
    $process = Start-Process -FilePath 'wsl.exe' -ArgumentList @('--install', '--distribution', $Distribution, '--no-launch') -Verb RunAs -Wait -PassThru
    if ($process.ExitCode -notin @(0, 3010)) { throw "WSL installation failed (exit $($process.ExitCode))." }
    Write-Host 'Restart Windows if requested. Then rerun install.ps1; first launch may ask for a Linux username and password.'
    exit 0
}
$versions = (& wsl.exe --list --verbose 2>$null | Out-String) -replace "`0", ''
$versionPattern = '(?m)^\s*\*?\s*' + [Regex]::Escape($Distribution) + '\s+.+\s+([12])\s*$'
if ($LASTEXITCODE -ne 0 -or $versions -notmatch $versionPattern) { throw 'Cannot determine the WSL version. Update WSL and rerun this installer.' }
if ($Matches[1] -eq '1') {
    Write-Host "Converting $Distribution from WSL1 to WSL2. This may take a few minutes."
    & wsl.exe --set-version $Distribution 2
    if ($LASTEXITCODE -ne 0) {
        Write-Host 'Enabling the WSL2 platform. Windows may request administrator access.'
        $process = Start-Process -FilePath 'wsl.exe' -ArgumentList @('--install', '--no-distribution') -Verb RunAs -Wait -PassThru
        if ($process.ExitCode -notin @(0, 3010)) { throw "WSL2 platform installation failed (exit $($process.ExitCode))." }
        Write-Host 'Restart Windows if requested, then rerun this installer to finish WSL2 conversion.'
        exit 0
    }
}

# WSL owns the Linux Python environment; no native Windows Python is necessary.
$linuxPackage = (& wsl.exe --distribution $Distribution --exec wslpath -u $PSScriptRoot | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or -not $linuxPackage) { throw 'WSL is not ready. Restart Windows, launch the distribution once, then rerun this installer.' }
$linuxNoModify = $(if ($env:IGNOVATE_NO_MODIFY_PATH -eq '1') { '1' } else { '0' })
& wsl.exe --distribution $Distribution --exec env "IGNOVATE_NO_MODIFY_PATH=$linuxNoModify" sh "$linuxPackage/install.sh"
if ($LASTEXITCODE -ne 0) { throw 'Linux launcher installation failed.' }
$linuxHome = (& wsl.exe --distribution $Distribution --exec printenv HOME | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or -not $linuxHome) { throw 'Could not determine the WSL home directory.' }
$linuxLauncher = "$linuxHome/.local/bin/ignovate"
$escapedDistribution = $Distribution.Replace("'", "''")
$escapedLauncher = $linuxLauncher.Replace("'", "''")
New-Item -ItemType Directory -Force -Path $BinDirectory | Out-Null
$BinDirectory = (Resolve-Path -LiteralPath $BinDirectory).Path
$wrapper = @'
$ErrorActionPreference = 'Stop'
$OutputEncoding = New-Object Text.UTF8Encoding $false
[Console]::OutputEncoding = $OutputEncoding
$distribution = '__DISTRIBUTION__'
$launcher = '__LAUNCHER__'
function Convert-ProjectPath([string]$Value) {
    if ($Value.StartsWith('/')) { return $Value }
    $windowsPath = [IO.Path]::GetFullPath($Value)
    $linuxPath = (& wsl.exe --distribution $distribution --exec wslpath -a -u $windowsPath | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $linuxPath) { throw "Cannot translate project path: $Value" }
    return $linuxPath
}
$forward = @($args)
for ($index = 0; $index -lt $forward.Count; $index++) {
    if ($forward[$index] -eq '--project' -and $index + 1 -lt $forward.Count) {
        $index++
        $forward[$index] = Convert-ProjectPath $forward[$index]
    } elseif ($forward[$index].StartsWith('--project=')) {
        $forward[$index] = '--project=' + (Convert-ProjectPath $forward[$index].Substring(10))
    }
}
$nativeArguments = @('--distribution', $distribution, '--cd', (Get-Location).Path, '--exec', $launcher) + $forward
# Windows PowerShell 5.1's native serializer loses embedded double quotes.
$serialized = foreach ($value in $nativeArguments) {
    $escaped = [Regex]::Replace([string]$value, '(\\*)"', { param($match) ($match.Groups[1].Value * 2) + '\"' })
    $escaped = [Regex]::Replace($escaped, '(\\+)$', { param($match) $match.Value * 2 })
    '"' + $escaped + '"'
}
$start = New-Object Diagnostics.ProcessStartInfo
$resolvedWsl = Get-Command wsl.exe -CommandType Application | Select-Object -First 1
$start.FileName = $resolvedWsl.Path
if (-not [IO.File]::Exists($start.FileName)) { throw "Cannot find the WSL executable: $($start.FileName)" }
$start.Arguments = $serialized -join ' '
$start.UseShellExecute = $false
$process = [Diagnostics.Process]::Start($start)
$process.WaitForExit()
$code = $process.ExitCode
$process.Dispose()
exit $code
'@
$wrapper = $wrapper.Replace('__DISTRIBUTION__', $escapedDistribution).Replace('__LAUNCHER__', $escapedLauncher)
Set-Content -LiteralPath (Join-Path $BinDirectory 'ignovate-wsl.ps1') -Value $wrapper -Encoding UTF8
Set-Content -LiteralPath (Join-Path $BinDirectory 'ignovate.cmd') -Encoding ASCII -Value @'
@echo off
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0ignovate-wsl.ps1" %*
exit /b %ERRORLEVEL%
'@
if ($env:IGNOVATE_NO_MODIFY_PATH -ne '1') {
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    if (($userPath -split ';') -notcontains $BinDirectory) {
        [Environment]::SetEnvironmentVariable('Path', "$BinDirectory;$userPath", 'User')
    }
}
$env:Path = "$BinDirectory;$env:Path"
Write-Host 'Launcher installed. Run: ignovate set up'
Write-Host 'Python and application dependencies will be downloaded inside WSL2 when needed.'
