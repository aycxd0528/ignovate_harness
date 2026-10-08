using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Text;

public static class IgnovateLauncher {
    static readonly string InstallHome = @"__INSTALL_HOME__";
    const string Version = "__VERSION__";
    static string Quote(string value) {
        var result = new StringBuilder("\"");
        int backslashes = 0;
        foreach (char character in value) {
            if (character == '\\') { backslashes++; continue; }
            if (character == '"') { result.Append('\\', backslashes * 2 + 1).Append('"'); }
            else { result.Append('\\', backslashes).Append(character); }
            backslashes = 0;
        }
        return result.Append('\\', backslashes * 2).Append('"').ToString();
    }
    static int Run(string executable, IEnumerable<string> arguments, string[] bootstrapArgs) {
        var quoted = new List<string>();
        foreach (string argument in arguments) { quoted.Add(Quote(argument)); }
        var start = new ProcessStartInfo(executable, String.Join(" ", quoted));
        start.UseShellExecute = false;
        start.WorkingDirectory = Environment.CurrentDirectory;
        start.EnvironmentVariables["IGNOVATE_INSTALL_HOME"] = InstallHome;
        start.EnvironmentVariables["PYTHONUTF8"] = "1";
        start.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
        start.EnvironmentVariables["PATH"] = Path.Combine(InstallHome, "tools") + ";" + Environment.GetEnvironmentVariable("PATH");
        if (bootstrapArgs != null) {
            start.EnvironmentVariables["IGNOVATE_ARGC"] = bootstrapArgs.Length.ToString();
            for (int index = 0; index < bootstrapArgs.Length; index++) { start.EnvironmentVariables["IGNOVATE_ARG_" + index] = Convert.ToBase64String(Encoding.UTF8.GetBytes(bootstrapArgs[index])); }
        }
        using (var process = Process.Start(start)) { process.WaitForExit(); return process.ExitCode; }
    }
    public static int Main(string[] args) {
        try {
            Console.OutputEncoding = new UTF8Encoding(false);
            Console.CancelKeyPress += delegate(object sender, ConsoleCancelEventArgs eventArgs) { eventArgs.Cancel = true; };
            string runtime = Path.Combine(InstallHome, "venvs", Version);
            string python = Path.Combine(runtime, "Scripts", "python.exe");
            bool setup = args.Length >= 2 && args[0] == "set" && args[1] == "up";
            bool setupFlag = args.Length >= 1 && args[0] == "--setup";
            bool environmentOnly = setup && args.Length >= 3 && args[2] == "--environment-only";
            bool setupHelp = setup && args.Length >= 3 && (args[2] == "--help" || args[2] == "-h");
            if (setup || setupFlag || !File.Exists(python) || !File.Exists(Path.Combine(runtime, ".ready"))) {
                string script = Path.Combine(InstallHome, "releases", Version, "launch.ps1").Replace("'", "''");
                string command = "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; try { $forward=@(for($i=0;$i -lt [int]$env:IGNOVATE_ARGC;$i++){[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String([Environment]::GetEnvironmentVariable('IGNOVATE_ARG_'+$i)))}); & '" + script + "' @forward; exit $LASTEXITCODE } catch { [Console]::Error.WriteLine($_.Exception.ToString()); exit 1 }";
                string powershell = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows), @"System32\WindowsPowerShell\v1.0\powershell.exe");
                int code = Run(powershell, new [] { "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-OutputFormat", "Text", "-EncodedCommand", Convert.ToBase64String(Encoding.Unicode.GetBytes(command)) }, args);
                if (code != 0 || environmentOnly || setupHelp) { return code; }
            }
            var appArgs = new List<string> { "-m", "nailong.cli" };
            if (setup) { appArgs.Add("--setup"); for (int index = 2; index < args.Length; index++) { appArgs.Add(args[index]); } }
            else { appArgs.AddRange(args); }
            return Run(python, appArgs, null);
        } catch (Exception error) { Console.Error.WriteLine("ignovate: " + error.Message); return 1; }
    }
}
