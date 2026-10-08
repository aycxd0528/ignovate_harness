#Requires -Version 5.1
param([Parameter(Mandatory=$true)][string]$Archive)
$ErrorActionPreference = 'Stop'
$root = Join-Path $env:RUNNER_TEMP ('ignovate-native-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $root | Out-Null
Expand-Archive -LiteralPath $Archive -DestinationPath $root
$bundle = Get-ChildItem -LiteralPath $root -Directory | Select-Object -First 1
$env:IGNOVATE_INSTALL_HOME = Join-Path $root 'application with spaces'
$env:IGNOVATE_WINDOWS_BIN_DIR = Join-Path $root 'bin'
$env:IGNOVATE_CONFIG_DIR = Join-Path $root 'config'
$env:IGNOVATE_NO_MODIFY_PATH = '1'
# Force real downloads rather than using the CI image's Python / uv / rg.
$env:UV_PYTHON_PREFERENCE = 'only-managed'
$env:Path = "$env:SystemRoot\System32;$env:SystemRoot\System32\WindowsPowerShell\v1.0"
& (Join-Path $bundle.FullName 'install.ps1')
$exe = Join-Path $env:IGNOVATE_WINDOWS_BIN_DIR 'ignovate.exe'
& $exe set up --environment-only
if ($LASTEXITCODE -ne 0) { throw "Fresh setup failed: $LASTEXITCODE" }
& $exe set up --environment-only
if ($LASTEXITCODE -ne 0) { throw "Repeat setup failed: $LASTEXITCODE" }
& $exe --help
if ($LASTEXITCODE -ne 0) { throw 'CLI help failed.' }
$doctor = & $exe doctor --output-format json
if ($LASTEXITCODE -notin @(0,1)) { throw 'Doctor failed.' }
$report = ($doctor -join "`n") | ConvertFrom-Json
foreach ($check in $report.checks) {
    if ($check.name -in @('python','textual','mcp','rg') -and $check.status -ne 'ok') { throw "Doctor check failed: $($check.name)" }
}
$version = [IO.File]::ReadAllText((Join-Path $bundle.FullName 'VERSION')).Trim()
$python = Join-Path $env:IGNOVATE_INSTALL_HOME "venvs\$version\Scripts\python.exe"
$env:Path = "$(Join-Path $env:IGNOVATE_INSTALL_HOME 'tools');$env:Path"
# Copy only acceptance tests outside the checkout: imports must come from wheel.
$tests = Join-Path $root 'acceptance'
New-Item -ItemType Directory -Path $tests | Out-Null
foreach ($name in @('test_native_tools.py','test_native_file_backend.py','test_native_processes.py')) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot "..\tests\$name") -Destination $tests
}
Push-Location $root
try {
    & $python -c 'import sys, local_tools; assert sys.platform == "win32"; assert sys.prefix != sys.base_prefix; print(local_tools.__file__)'
    if ($LASTEXITCODE -ne 0) { throw 'Native wheel import failed.' }
    & $python -m unittest discover -s $tests -v
    if ($LASTEXITCODE -ne 0) { throw 'Native installed-wheel acceptance failed.' }
} finally { Pop-Location }
