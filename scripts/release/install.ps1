#Requires -Version 5.1
param(
    [string]$InstallHome = $(if ($env:IGNOVATE_INSTALL_HOME) { $env:IGNOVATE_INSTALL_HOME } else { Join-Path $env:LOCALAPPDATA 'Ignovate' }),
    [string]$BinDirectory = $(if ($env:IGNOVATE_WINDOWS_BIN_DIR) { $env:IGNOVATE_WINDOWS_BIN_DIR } else { Join-Path $env:LOCALAPPDATA 'Ignovate\bin' })
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
Assert-IgnovateWindows
Assert-IgnovateBundle $PSScriptRoot
$version = Get-IgnovateVersion $PSScriptRoot
New-Item -ItemType Directory -Force -Path $InstallHome, $BinDirectory | Out-Null
$InstallHome = (Resolve-Path -LiteralPath $InstallHome).Path
$BinDirectory = (Resolve-Path -LiteralPath $BinDirectory).Path
$release = Join-Path $InstallHome "releases\$version"
New-Item -ItemType Directory -Force -Path $release | Out-Null
if ($PSScriptRoot -ne $release) {
    Get-ChildItem -LiteralPath $PSScriptRoot -File | ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $release -Force }
}
Assert-IgnovateBundle $release
# The small launcher uses .NET Framework already supplied with Windows 11.
$source = [IO.File]::ReadAllText((Join-Path $release 'launcher.cs'))
$source = $source.Replace('__INSTALL_HOME__', $InstallHome.Replace('"', '""')).Replace('__VERSION__', $version)
$temporary = Join-Path $BinDirectory ('ignovate-' + [Guid]::NewGuid().ToString('N') + '.exe')
try {
    Add-Type -TypeDefinition $source -OutputAssembly $temporary -OutputType ConsoleApplication
    Move-Item -LiteralPath $temporary -Destination (Join-Path $BinDirectory 'ignovate.exe') -Force
} finally { if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force } }
foreach ($legacy in @('ignovate.cmd', 'ignovate-wsl.ps1')) {
    $old = Join-Path $BinDirectory $legacy
    if (Test-Path -LiteralPath $old) { Remove-Item -LiteralPath $old -Force }
}
if ($env:IGNOVATE_NO_MODIFY_PATH -ne '1') {
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    if (($userPath -split ';') -notcontains $BinDirectory) { [Environment]::SetEnvironmentVariable('Path', "$BinDirectory;$userPath", 'User') }
}
$env:Path = "$BinDirectory;$env:Path"
Write-Host "Launcher installed: $BinDirectory\ignovate.exe"
Write-Host 'Open a new terminal and run: ignovate set up'
Write-Host 'Python, application dependencies and ripgrep will be prepared natively on Windows 11.'
