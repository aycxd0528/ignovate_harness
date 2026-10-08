#Requires -Version 5.1
param([Parameter(Mandatory = $true)][string]$Archive)
$ErrorActionPreference = 'Stop'
$root = Join-Path ([IO.Path]::GetTempPath()) ('ignovate native space ' + [Guid]::NewGuid())
New-Item -ItemType Directory -Path $root | Out-Null
$oldNoModify, $oldPath = $env:IGNOVATE_NO_MODIFY_PATH, $env:Path
$originalUserPath = [Environment]::GetEnvironmentVariable('Path', 'User')
try {
    Expand-Archive -LiteralPath $Archive -DestinationPath $root
    $bundle = (Get-ChildItem -LiteralPath $root -Directory | Where-Object Name -Like 'ignovate-*').FullName
    $version = [IO.File]::ReadAllText((Join-Path $bundle 'VERSION')).Trim()
    $bin, $data = (Join-Path $root 'bin'), (Join-Path $root 'data')
    $env:IGNOVATE_NO_MODIFY_PATH = '1'
    & (Join-Path $bundle 'install.ps1') -InstallHome $data -BinDirectory $bin
    $launcher = Join-Path $bin 'ignovate.exe'
    if (-not (Test-Path -LiteralPath $launcher)) { throw 'Native launcher missing.' }
    if (Test-Path -LiteralPath (Join-Path $data 'venvs')) { throw 'Installer prepared Python before set up.' }
    if ([Environment]::GetEnvironmentVariable('Path', 'User') -ne $originalUserPath) { throw 'PATH opt-out was ignored.' }
    # Substitute a real native executable only at the Python process boundary.
    $runtime = Join-Path $data "venvs\$version"
    $scripts = Join-Path $runtime 'Scripts'
    New-Item -ItemType Directory -Path $scripts | Out-Null
    [IO.File]::WriteAllText((Join-Path $runtime '.ready'), $version)
    $source = @'
using System;
using System.IO;
public class PythonFixture {
    static string Quote(string value) { return "\"" + value.Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\r", "\\r").Replace("\n", "\\n") + "\""; }
    public static int Main(string[] args) {
        Console.OutputEncoding = new System.Text.UTF8Encoding(false);
        if (Array.IndexOf(args, "force-error") >= 0) { return 7; }
        Console.WriteLine("{\"cwd\":" + Quote(Environment.CurrentDirectory) + ",\"args\":[" + string.Join(",", Array.ConvertAll(args, Quote)) + "]}");
        return 0;
    }
}
'@
    Add-Type -TypeDefinition $source -OutputAssembly (Join-Path $scripts 'python.exe') -OutputType ConsoleApplication
    function Invoke-Fixture([string[]]$Values) {
        $serialized = foreach ($value in $Values) {
            $escaped = [Regex]::Replace($value, '(\\*)"', { param($match) ($match.Groups[1].Value * 2) + '\"' })
            $escaped = [Regex]::Replace($escaped, '(\\+)$', { param($match) $match.Value * 2 })
            '"' + $escaped + '"'
        }
        $start = New-Object Diagnostics.ProcessStartInfo
        $start.FileName = $launcher
        $start.Arguments = $serialized -join ' '
        $start.WorkingDirectory = $root
        $start.UseShellExecute = $false
        $start.RedirectStandardOutput = $true
        $start.RedirectStandardError = $true
        $start.StandardOutputEncoding = New-Object Text.UTF8Encoding $false
        $process = [Diagnostics.Process]::Start($start)
        $output, $errors = $process.StandardOutput.ReadToEnd(), $process.StandardError.ReadToEnd()
        $process.WaitForExit()
        $result = @{Code=$process.ExitCode; Output=$output; Errors=$errors}
        $process.Dispose()
        return $result
    }
    $values = @('-p', 'say "hello" with spaces', '--project', 'C:\a project\', '', '--project=C:\other project')
    $result = Invoke-Fixture $values
    if ($result.Code -ne 0) { throw $result.Errors }
    $actual = $result.Output | ConvertFrom-Json
    $expected = @('-m', 'nailong.cli') + $values
    if (($actual.args | ConvertTo-Json -Compress) -ne ($expected | ConvertTo-Json -Compress)) { throw 'Native launcher lost argv.' }
    if ($actual.cwd -ne $root) { throw 'Native launcher lost cwd.' }
    if ((Invoke-Fixture @('force-error')).Code -ne 7) { throw 'Native exit code was lost.' }
    # Reinstallation must be safe in the same PowerShell process.
    & (Join-Path $bundle 'install.ps1') -InstallHome $data -BinDirectory $bin
    $help = Invoke-Fixture @('set', 'up', '--help')
    if ($help.Code -ne 0 -or $help.Output -notlike '*Usage: ignovate set up*') { throw 'Setup help unavailable.' }
    $tools = Join-Path $data 'tools'
    New-Item -ItemType Directory -Path $tools | Out-Null
    Add-Type -TypeDefinition 'public class FailedUv { public static int Main(string[] args) { return 69; } }' -OutputAssembly (Join-Path $tools 'uv.exe') -OutputType ConsoleApplication
    $repair = Invoke-Fixture @('set', 'up', '--environment-only')
    if ($repair.Code -eq 0 -or (Test-Path -LiteralPath (Join-Path $runtime '.ready'))) { throw 'Failed repair left ready state.' }
    $released = [IO.File]::Open((Join-Path $data 'setup.lock'), [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    $released.Dispose()
    if (@(Get-ChildItem -LiteralPath $data -Directory -Filter '.setup-*').Count -ne 0) { throw 'Temporary setup files were not cleaned.' }
    Set-Content -LiteralPath (Join-Path $bundle 'requirements-release.lock') -Value 'tampered'
    $rejected = $false
    try { & (Join-Path $bundle 'install.ps1') -InstallHome (Join-Path $root 'bad-data') -BinDirectory (Join-Path $root 'bad-bin') }
    catch { $rejected = $_.Exception.Message -like '*Checksum failed*' }
    if (-not $rejected) { throw 'Corrupt release was accepted.' }
    Write-Host 'Native installer smoke passed: integrity, PATH opt-out, cwd/argv/exit, help, repeat install and failed repair.'
    $global:LASTEXITCODE = 0
} finally {
    $env:IGNOVATE_NO_MODIFY_PATH, $env:Path = $oldNoModify, $oldPath
    Remove-Item -LiteralPath $root -Recurse -Force
}
