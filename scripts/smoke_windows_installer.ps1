#Requires -Version 5.1
param([Parameter(Mandatory = $true)][string]$Archive)
$ErrorActionPreference = 'Stop'
$root = Join-Path ([IO.Path]::GetTempPath()) ('ignovate windows space ' + [Guid]::NewGuid())
New-Item -ItemType Directory -Path $root | Out-Null
$previousNoModify = $env:IGNOVATE_NO_MODIFY_PATH
$previousPath = $env:Path
try {
    Expand-Archive -LiteralPath $Archive -DestinationPath $root
    $bundle = (Get-ChildItem -LiteralPath $root -Directory | Where-Object Name -Like 'ignovate-*').FullName
    $bin = Join-Path $root 'bin'
    $env:IGNOVATE_NO_MODIFY_PATH = '1'
    $env:IGNOVATE_FIXTURE_VERSION_PATH = Join-Path $root 'wsl-version'
    Set-Content -LiteralPath $env:IGNOVATE_FIXTURE_VERSION_PATH -Value '2'
    # Replace only the OS/WSL boundary with a real native executable.
    $nativeBin = Join-Path $root 'native-bin'
    New-Item -ItemType Directory -Path $nativeBin | Out-Null
    $source = @'
using System;
using System.IO;
public class WslFixture {
    static string Quote(string value) {
        return "\"" + value.Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\r", "\\r").Replace("\n", "\\n") + "\"";
    }
    public static int Main(string[] args) {
        Console.OutputEncoding = new System.Text.UTF8Encoding(false);
        string versionFile = Environment.GetEnvironmentVariable("IGNOVATE_FIXTURE_VERSION_PATH");
        if (Array.IndexOf(args, "--list") >= 0) {
            Console.WriteLine(Array.IndexOf(args, "--verbose") >= 0 ? "  Ubuntu Running " + File.ReadAllText(versionFile).Trim() : "Ubuntu");
            return 0;
        }
        if (Array.IndexOf(args, "--set-version") >= 0) { File.WriteAllText(versionFile, "2"); return 0; }
        if (Array.IndexOf(args, "wslpath") >= 0) {
            Console.WriteLine(args[args.Length - 1] == @"C:\a project with spaces" ? "/mnt/c/a project with spaces" : "/mnt/c/ignovate fixture"); return 0;
        }
        if (Array.IndexOf(args, "HOME") >= 0) { Console.WriteLine("/home/fixture"); return 0; }
        if (Array.IndexOf(args, "sh") >= 0) { return Array.IndexOf(args, "IGNOVATE_NO_MODIFY_PATH=1") >= 0 ? 0 : 8; }
        if (Array.IndexOf(args, "force-error") >= 0) { return 7; }
        Console.WriteLine("[" + string.Join(",", Array.ConvertAll(args, Quote)) + "]");
        return 0;
    }
}
'@
    Add-Type -TypeDefinition $source -OutputAssembly (Join-Path $nativeBin 'wsl.exe') -OutputType ConsoleApplication
    $env:Path = "$nativeBin;$env:Path"
    & (Join-Path $bundle 'install.ps1') -BinDirectory $bin
    if (-not (Test-Path (Join-Path $bin 'ignovate.cmd'))) { throw 'Windows launcher missing.' }
    if (Test-Path (Join-Path $bin 'ignovate.ps1')) { throw 'PowerShell command discovery would bypass the cmd entry point.' }
    $result = & (Join-Path $bin 'ignovate.cmd') -p 'a prompt with spaces' --project 'C:\a project with spaces' | ConvertFrom-Json
    $expected = @('--distribution', 'Ubuntu', '--cd', (Get-Location).Path, '--exec', '/home/fixture/.local/bin/ignovate', '-p', 'a prompt with spaces', '--project', '/mnt/c/a project with spaces')
    if (($result | ConvertTo-Json -Compress) -ne ($expected | ConvertTo-Json -Compress)) { throw 'Native cmd/PowerShell/WSL argv or cwd was lost.' }
    $result = & (Join-Path $bin 'ignovate.cmd') --project='C:\a project with spaces' | ConvertFrom-Json
    if ($result[-1] -ne '--project=/mnt/c/a project with spaces') { throw '--project= path was not translated.' }
    # Initial WinPS 5.1 -> cmd quoting uses the native shell's escaping convention.
    $result = & (Join-Path $bin 'ignovate.cmd') -p 'say \"hello\"' | ConvertFrom-Json
    if ($result[-1] -ne 'say "hello"') { throw 'Embedded quotes were lost inside the launcher.' }
    & (Join-Path $bin 'ignovate.cmd') force-error
    if ($LASTEXITCODE -ne 7) { throw 'Native launcher did not preserve the exit code.' }
    Set-Content -LiteralPath $env:IGNOVATE_FIXTURE_VERSION_PATH -Value '1'
    & (Join-Path $bundle 'install.ps1') -BinDirectory $bin
    if ((Get-Content $env:IGNOVATE_FIXTURE_VERSION_PATH).Trim() -ne '2') { throw 'Existing WSL1 distro was not upgraded.' }
    Set-Content -LiteralPath (Join-Path $bundle 'requirements-release.lock') -Value 'tampered'
    $rejected = $false
    try { & (Join-Path $bundle 'install.ps1') -BinDirectory (Join-Path $root 'bad-bin') }
    catch { $rejected = $_.Exception.Message -like '*Checksum failed*' }
    if (-not $rejected) { throw 'Corrupt release was accepted.' }
    Write-Host 'Windows installer smoke passed: checksums, WSL2 enforcement, PATH opt-out, native argv/cwd/exit.'
    $global:LASTEXITCODE = 0
}
finally {
    $env:IGNOVATE_NO_MODIFY_PATH = $previousNoModify
    $env:Path = $previousPath
    Remove-Item Env:\IGNOVATE_FIXTURE_VERSION_PATH -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $root -Recurse -Force
}
