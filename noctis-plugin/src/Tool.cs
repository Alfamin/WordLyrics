using System.Diagnostics;
using System.Text;

namespace WordLyricsForNoctis;

/// <summary>
/// The WordLyrics program on this computer: where it is, whether it is set up, and how it is
/// started. The plugin never does any lyric work itself; it only starts this program.
/// </summary>
internal sealed class Tool
{
    public const string Launcher = "WordLyrics.bat";

    public Tool(string? configured)
    {
        var folder = configured?.Trim().Trim('"');
        Folder = string.IsNullOrEmpty(folder)
            ? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "WordLyrics")
            : Path.GetFullPath(Environment.ExpandEnvironmentVariables(folder));
    }

    public string Folder { get; }

    private string Bat => Path.Combine(Folder, Launcher);

    public bool Installed => File.Exists(Bat);

    /// <summary>The first WordLyrics that can be given single songs and reports what it did with them.</summary>
    private static readonly Version Needed = new(1, 6, 1);

    /// <summary>Installed, but from before it could be handed single songs.</summary>
    public bool TooOld
    {
        get
        {
            try
            {
                var about = File.ReadAllText(Path.Combine(Folder, "wordlyrics", "__init__.py"));
                var match = System.Text.RegularExpressions.Regex.Match(about, "__version__\\s*=\\s*\"(\\d+\\.\\d+\\.\\d+)");
                return !match.Success || Version.Parse(match.Groups[1].Value) < Needed;
            }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
            {
                return true;
            }
        }
    }

    /// <summary>Installed, new enough, its own Python set up and both models fetched: a run starts without downloading anything.</summary>
    public bool Ready
    {
        get
        {
            try
            {
                if (!Installed || TooOld) return false;
                var python = Path.Combine(Folder, ".python");
                var models = Path.Combine(Folder, "models");
                return File.Exists(Path.Combine(python, "python.exe")) && Directory.EnumerateFiles(python, "ready-*.txt").Any()
                    && new FileInfo(Path.Combine(models, "mms_fa_300m.onnx")).Length == 1262421764
                    && new FileInfo(Path.Combine(models, "Kim_Vocal_2.onnx")).Length == 66759214;
            }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
            {
                return false;
            }
        }
    }

    /// <summary>
    /// Times the songs listed in <paramref name="listFile"/>, without a window. What happened to
    /// each song is written to <paramref name="resultFile"/>, the program's own output to <paramref name="logFile"/>.
    /// </summary>
    public Process StartQuietRun(string root, string listFile, string resultFile, string logFile, string speed, string backup, bool retry, string? progressFile = null)
    {
        // Pass paths as real arguments. cmd.exe expands %variables% even inside quotes.
        var python = Environment.GetEnvironmentVariable("WORDLYRICS_PYTHON");
        var info = new ProcessStartInfo(string.IsNullOrWhiteSpace(python) ? Path.Combine(Folder, ".python", "python.exe") : python)
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            WorkingDirectory = Folder,
            RedirectStandardOutput = true, RedirectStandardError = true,
            StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8,
        };
        foreach (var arg in new[] { "-X", "utf8", "-m", "wordlyrics.runner", root, "--songs-from", listFile, "--result", resultFile,
            "--yes", "--no-open", "--plain-output", "--speed", speed }) info.ArgumentList.Add(arg);
        if (retry) info.ArgumentList.Add("--retry");
        if (progressFile is not null) { info.ArgumentList.Add("--progress"); info.ArgumentList.Add(progressFile); }
        backup = backup.Trim().Trim('"');
        if (backup.Length > 0) { info.ArgumentList.Add("--backup-to"); info.ArgumentList.Add(Environment.ExpandEnvironmentVariables(backup)); }
        info.Environment["WORDLYRICS_HOME"] = Folder;
        info.Environment["PYTHONPATH"] = Folder;
        info.Environment["WORDLYRICS_NO_PAUSE"] = "1"; // the launcher waits for a key press otherwise
        File.WriteAllText(logFile, "WordLyrics background run\n", new UTF8Encoding(false));
        var gate = new object();
        void Log(object sender, DataReceivedEventArgs e)
        {
            if (e.Data is null) return;
            try { lock (gate) { if (new FileInfo(logFile).Length < 5_000_000) File.AppendAllText(logFile, e.Data + "\n", new UTF8Encoding(false)); } }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
        }
        var process = new Process { StartInfo = info };
        process.OutputDataReceived += Log; process.ErrorDataReceived += Log;
        if (!process.Start()) { process.Dispose(); throw new InvalidOperationException("WordLyrics could not be started."); }
        process.BeginOutputReadLine(); process.BeginErrorReadLine();
        return process;
    }

    public void OpenLibraryMenu(string settingsFile)
    {
        var info = new ProcessStartInfo(Cmd)
        {
            Arguments = $"/d /s /c \"\"{Bat}\" menu\"", UseShellExecute = false, CreateNoWindow = false, WorkingDirectory = Folder,
        };
        info.Environment["WORDLYRICS_NOCTIS_SETTINGS"] = settingsFile;
        Process.Start(info)?.Dispose();
    }

    public void OpenResume(string runFolder)
        => OpenRequest(new[] { "resume", runFolder });

    public void OpenSongRepair(string path, string? draft = null, string? settingsFile = null)
        => OpenRequest(draft is null ? new[] { "repair-song", path } : new[] { "repair-song", path, "--lyrics-file", draft }, settingsFile);

    internal ProcessStartInfo RequestInfo(string[] request, string? settingsFile = null)
    {
        var info = new ProcessStartInfo(Cmd)
        {
            Arguments = $"/d /s /c \"\"{Bat}\" launch\"", UseShellExecute = false, CreateNoWindow = false, WorkingDirectory = Folder,
        };
        info.Environment["WORDLYRICS_LAUNCH_REQUEST"] = System.Text.Json.JsonSerializer.Serialize(request);
        if (settingsFile is not null) info.Environment["WORDLYRICS_NOCTIS_SETTINGS"] = settingsFile;
        return info;
    }

    private void OpenRequest(string[] request, string? settingsFile = null) => Process.Start(RequestInfo(request,settingsFile))?.Dispose();

    /// <summary>Opens WordLyrics in its own window on a whole folder; it asks its questions there.</summary>
    public void OpenOnFolder(string root)
    {
        var info = new ProcessStartInfo(Cmd)
        {
            Arguments = $"/d /s /c \"\"{Bat}\" \"{FolderArgument(root)}\"\"",
            UseShellExecute = false,
            CreateNoWindow = false,
            WorkingDirectory = Folder,
        };
        Process.Start(info)?.Dispose();
    }

    /// <summary>
    /// Opens a window that installs or updates WordLyrics into <see cref="Folder"/> with the
    /// program's own installer, then fetches its models. <paramref name="scriptFolder"/> is where
    /// the few lines that do this are written.
    /// </summary>
    public void OpenSetup(string scriptFolder)
    {
        Directory.CreateDirectory(scriptFolder);
        var script = Path.Combine(scriptFolder, "setup.cmd");
        File.WriteAllText(script, SetupText().Replace("\r\n", "\n").Replace("\n", "\r\n"), new UTF8Encoding(false));
        var info = new ProcessStartInfo(Cmd)
        {
            Arguments = $"/d /s /c \"\"{script}\"\"",
            UseShellExecute = false,
            CreateNoWindow = false,
            WorkingDirectory = scriptFolder,
        };
        info.Environment["WORDLYRICS_DIR"] = Folder;
        info.Environment["WORDLYRICS_NO_START"] = "1";
        info.Environment["WORDLYRICS_NO_PAUSE"] = "1";
        Process.Start(info)?.Dispose();
    }

    private static string Cmd => Path.Combine(Environment.SystemDirectory, "cmd.exe");

    internal string SetupText()
    {
        var complete = Installed && !TooOld && new[] { "__main__.py", "runner.py", "firststart.py", "menu.py", "library.py", "repair.py", "drafts.py", "search.py", "guides.py" }
            .All(name => File.Exists(Path.Combine(Folder, "wordlyrics", name))) && File.Exists(Path.Combine(Folder,"requirements.txt"));
        return complete ? SetupScript.Replace(DownloadInstaller, "echo  Reusing the installed WordLyrics program. Existing Python/packages/models are checked before any download.") : SetupScript;
    }

    private const string DownloadInstaller = "\"%SystemRoot%\\System32\\WindowsPowerShell\\v1.0\\powershell.exe\" -NoProfile -Command \"irm https://raw.githubusercontent.com/Alfamin/WordLyrics/main/install.ps1 | iex\"";

    /// <summary>A folder as a quoted argument: "D:\" would end in an escaped quote, "D:\." does not.</summary>
    private static string FolderArgument(string folder)
        => folder.EndsWith('\\') || folder.EndsWith('/') ? folder + "." : folder;

    private const string SetupScript = """
        @echo off
        rem Written by the WordLyrics plugin for Noctis. Installs or updates the WordLyrics program
        rem into the folder named in WORDLYRICS_DIR, then lets it fetch what it needs.
        title WordLyrics setup
        echo.
        echo  Installing WordLyrics into %WORDLYRICS_DIR%
        echo.
        "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -Command "irm https://raw.githubusercontent.com/Alfamin/WordLyrics/main/install.ps1 | iex"
        if not exist "%WORDLYRICS_DIR%\WordLyrics.bat" goto :failed
        call "%WORDLYRICS_DIR%\WordLyrics.bat" setup
        if errorlevel 1 goto :failed
        echo.
        echo  WordLyrics is ready. New songs in Noctis get word-by-word lyrics from now on.
        echo  Press a key to close this window.
        pause >nul
        exit /b 0
        :failed
        echo.
        echo  That did not finish. Check the internet connection, then flip the switch in Noctis
        echo  again: it continues where it stopped.
        echo  Press a key to close this window.
        pause >nul
        exit /b 1

        """;
}
