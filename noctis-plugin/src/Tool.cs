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
    private static readonly Version Needed = new(1, 1, 0);

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
                return Directory.Exists(python) && Directory.EnumerateFiles(python, "ready-*.txt").Any()
                    && Directory.Exists(models) && Directory.EnumerateFiles(models, "*.onnx").Take(2).Count() == 2;
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
    public Process StartQuietRun(string root, string listFile, string resultFile, string logFile, string speed, string backup, bool retry)
    {
        var run = $"\"{Bat}\" \"{FolderArgument(root)}\" --songs-from \"{listFile}\" --result \"{resultFile}\""
                  + $" --yes --no-open --plain-output --speed {speed}" + (retry ? " --retry" : "");
        backup = backup.Trim().Trim('"');
        if (backup.Length > 0) run += $" --backup-to \"{FolderArgument(Environment.ExpandEnvironmentVariables(backup))}\"";
        var info = new ProcessStartInfo(Cmd)
        {
            // /s: everything between the outer quotes is the command, with its own quotes left alone
            Arguments = $"/d /s /c \"{run} > \"{logFile}\" 2>&1\"",
            UseShellExecute = false,
            CreateNoWindow = true,
            WorkingDirectory = Folder,
        };
        info.Environment["WORDLYRICS_NO_PAUSE"] = "1"; // the launcher waits for a key press otherwise
        return Process.Start(info) ?? throw new InvalidOperationException("WordLyrics could not be started.");
    }

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
        File.WriteAllText(script, SetupScript.Replace("\r\n", "\n").Replace("\n", "\r\n"), new UTF8Encoding(false));
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
