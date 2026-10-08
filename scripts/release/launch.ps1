#Requires -Version 5.1
$forward = @($args)
. (Join-Path $PSScriptRoot 'common.ps1')
Assert-IgnovateWindows
$version = Get-IgnovateVersion $PSScriptRoot
$installHome = $env:IGNOVATE_INSTALL_HOME
if (-not $installHome) { $installHome = Join-Path $env:LOCALAPPDATA 'Ignovate' }
$runtime = Join-Path $installHome "venvs\$version"
$python = Join-Path $runtime 'Scripts\python.exe'
$tools = Join-Path $installHome 'tools'
$env:Path = "$tools;$env:Path"
if ($forward.Count -ge 3 -and $forward[0] -eq 'set' -and $forward[1] -eq 'up') {
    if ($forward[2] -in @('--help', '-h')) { Write-Host 'Usage: ignovate set up [--environment-only] [configuration arguments]'; exit 0 }
    if ($forward[2] -eq '--environment-only' -and $forward.Count -ne 3) { throw '--environment-only does not accept extra arguments.' }
}
Assert-IgnovateBundle $PSScriptRoot
New-Item -ItemType Directory -Force -Path $installHome, $tools, (Join-Path $installHome 'venvs') | Out-Null
$lockPath = Join-Path $installHome 'setup.lock'
try { $lock = [IO.File]::Open($lockPath, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None) }
catch { throw "Another setup process is active. Retry after it finishes: $lockPath" }
$work = Join-Path $installHome ('.setup-' + [Guid]::NewGuid().ToString('N'))
try {
    New-Item -ItemType Directory -Path $work | Out-Null
    $ready = Join-Path $runtime '.ready'
    if (Test-Path -LiteralPath $ready) { Remove-Item -LiteralPath $ready -Force }
    $uv = Get-Command uv.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($uv) { $uv = $uv.Path }
    else {
        Write-Host 'Downloading environment manager...'
        $uvScript = Join-Path $work 'uv-install.ps1'
        Get-IgnovateDownload 'https://astral.sh/uv/0.12.23/install.ps1' $uvScript
        $previousUnmanaged = $env:UV_UNMANAGED_INSTALL
        try {
            $env:UV_UNMANAGED_INSTALL = $tools
            Invoke-IgnovateChecked (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $uvScript)
        } finally { $env:UV_UNMANAGED_INSTALL = $previousUnmanaged }
        $uv = Join-Path $tools 'uv.exe'
        if (-not (Test-Path -LiteralPath $uv)) { throw 'uv installation did not produce uv.exe.' }
    }
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $installHome 'python'
    $env:UV_CACHE_DIR = Join-Path $installHome 'cache'
    $env:UV_PYTHON_DOWNLOADS = 'automatic'
    if (-not $env:UV_PYTHON_PREFERENCE) { $env:UV_PYTHON_PREFERENCE = 'system' }
    if (-not (Test-Path -LiteralPath $python)) {
        Write-Host 'Preparing Python 3.11-3.13; a missing interpreter will be downloaded...'
        Invoke-IgnovateChecked $uv @('--no-config', 'venv', '--no-project', '--python', '>=3.11,<3.14', $runtime)
    }
    Write-Host 'Installing and verifying application dependencies...'
    Invoke-IgnovateChecked $uv @('--no-config', 'pip', 'install', '--python', $python, '--require-hashes', '-r', (Join-Path $PSScriptRoot 'requirements-release.lock'))
    Invoke-IgnovateChecked $uv @('--no-config', 'pip', 'install', '--python', $python, '--no-deps', '--reinstall-package', 'ignovate-harness', (Join-Path $PSScriptRoot "ignovate_harness-$version-py3-none-any.whl"))
    Invoke-IgnovateChecked $uv @('--no-config', 'pip', 'check', '--python', $python)
    if (-not (Get-Command rg.exe -CommandType Application -ErrorAction SilentlyContinue)) {
        Write-Host 'Downloading ripgrep...'
        $archive = 'ripgrep-15.2.0-x86_64-pc-windows-msvc.zip'
        $url = "https://github.com/BurntSushi/ripgrep/releases/download/15.2.0/$archive"
        $zip = Join-Path $work $archive
        $hashFile = Join-Path $work 'rg.sha256'
        Get-IgnovateDownload $url $zip
        Get-IgnovateDownload "$url.sha256" $hashFile
        $expected = ([IO.File]::ReadAllText($hashFile).Trim() -split '\s+')[0]
        if ((Get-FileHash -Algorithm SHA256 -LiteralPath $zip).Hash.ToLowerInvariant() -ne $expected) { throw 'ripgrep download checksum failed.' }
        Expand-Archive -LiteralPath $zip -DestinationPath $work
        Copy-Item -LiteralPath (Join-Path $work 'ripgrep-15.2.0-x86_64-pc-windows-msvc\rg.exe') -Destination $tools -Force
    }
    Invoke-IgnovateChecked $python @('-c', 'import main, textual, mcp, langchain_deepseek; import sys; assert sys.platform == "win32"')
    [IO.File]::WriteAllText($ready, $version + "`n", (New-Object Text.UTF8Encoding $false))
    Write-Host 'Environment ready. Model configuration is the next step.'
    if (-not (Get-Command git.exe -ErrorAction SilentlyContinue)) { Write-Host 'Git is optional. Install Git for Windows before using diff/review.' }
} finally {
    if (Test-Path -LiteralPath $work) { Remove-Item -LiteralPath $work -Recurse -Force }
    $lock.Dispose()
}
exit 0
