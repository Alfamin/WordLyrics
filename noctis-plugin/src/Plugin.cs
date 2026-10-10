using Avalonia.Threading;
using Avalonia;
using Avalonia.Controls.ApplicationLifetimes;
using Noctis.Plugins;

namespace WordLyricsForNoctis;

/// <summary>
/// WordLyrics for Noctis: a song that is added to a library folder gets word-by-word lyrics by
/// itself. The work is done by the WordLyrics program (github.com/Alfamin/WordLyrics), which this
/// plugin starts in the background on exactly the new songs; the plugin is the part that
/// notices them. It also adds "Time the words (WordLyrics)" to the track menu.
/// </summary>
public sealed class WordLyricsPlugin : INoctisPlugin
{
    private const string SetupKey = "setup";
    private const string LibraryKey = "library";

    private readonly List<IDisposable> _registrations = new();
    private IPluginHost? _host;
    private Worker? _worker;
    private ProgressWindow? _progress;
    private readonly List<LyricEditorWindow> _editors = new();
    private ProgressState _lastProgress = new("Waiting for new music");
    private bool _autoShownForJob;

    public PluginInfo Info { get; } = new(
        Id: "dev.moshi.wordlyrics",
        Name: "WordLyrics",
        Version: "1.2.0",
        Author: "moshi",
        Description: "Word-by-word lyrics for new songs, by themselves: the WordLyrics program finds the lyrics of a song that was added to the library and times every word in the background.");

    public void Initialize(IPluginHost host)
    {
        _host = host;
        // The plugin API does not say where the library folders are; Noctis's own settings file,
        // two levels above the plugin's data folder, does.
        var settings = Path.GetFullPath(Path.Combine(host.DataDirectory, "..", "..", "settings.json"));
        _worker = new Worker(host.DataDirectory, settings, ReadOptions(host), Log, Notify, OnProgress);

        _registrations.Add(host.RegisterTrackCommand(
            "Time the words (WordLyrics)",
            // A 24×24 SVG path: lines of text.
            "M20 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zM4 12h4v2H4v-2zm10 6H4v-2h10v2zm6 0h-4v-2h4v2zm0-4H10v-2h10v2z",
            (Action<TrackInfo>)OnTrackCommand));
        _registrations.Add(host.RegisterTrackCommand(
            "Wrong lyrics / redo with fresh lyrics (WordLyrics)",
            "M12 2L1 21h22L12 2zm1 16h-2v-2h2v2zm0-4h-2V9h2v5z",
            (Action<TrackInfo>)OnRepairCommand));
        _registrations.Add(host.RegisterTrackCommand(
            "Edit / paste custom lyrics (WordLyrics)",
            "M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04a1 1 0 000-1.41l-2.34-2.34a1 1 0 00-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z",
            (Action<TrackInfo>)OnEditCommand));

        host.Settings.Changed += OnSettingChanged;
        host.Log("ready");
    }

    public void Shutdown()
    {
        if (_host is not null) _host.Settings.Changed -= OnSettingChanged;
        foreach (var registration in _registrations) registration.Dispose();
        _registrations.Clear();
        _worker?.Dispose();
        _progress?.Close();
        foreach (var editor in _editors.ToArray()) editor.Close();
        _editors.Clear();
        _progress = null;
        _worker = null;
        _host = null;
    }

    private static Options ReadOptions(IPluginHost host) => new(
        Auto: host.Settings.GetBool("auto", true),
        Speed: host.Settings.GetString("speed") ?? "Half",
        Notify: host.Settings.GetBool("notify", true),
        Folder: host.Settings.GetString("folder") ?? "",
        Backup: host.Settings.GetString("backup") ?? "");

    // Noctis calls the two handlers below on its UI thread and switches a plugin off when a
    // callback throws or takes longer than a second. So they only take note of what is wanted;
    // looking at files and starting programs happens on a thread of our own.

    private void OnTrackCommand(TrackInfo track)
    {
        var worker = _worker;
        var path = track.FilePath;
        if (worker is null) return;
        Task.Run(() =>
        {
            try
            {
                var answer = worker.Ask(path);
                if (answer is not null) Notify(answer);
            }
            catch (Exception ex)
            {
                Log("could not queue the song: " + ex.Message);
            }
        });
    }

    private void OnRepairCommand(TrackInfo track)
    {
        if (_host is null) return;
        var folder = ReadOptions(_host).Folder;
        var settingsFile = _worker?.SettingsFile;
        var path = track.FilePath;
        Task.Run(() =>
        {
            try
            {
                var tool = new Tool(folder);
                if (!tool.Installed || tool.TooOld)
                    Notify("Install or update WordLyrics first, then choose Wrong lyrics / redo again.");
                else { tool.OpenSongRepair(path,settingsFile:settingsFile); Notify("WordLyrics opened for this song. Choose fresh lyrics, Genius / LRCLIB or custom words, then confirm the backed-up rerun."); }
            }
            catch (Exception ex) { Notify("SONG_REPAIR_OPEN_FAILED: " + ex.GetType().Name + ". Use WordLyrics > 8 Search / fix a song."); }
        });
    }

    private void OnEditCommand(TrackInfo track)
    {
        if (_host is null) return;
        var host = _host; var path = track.FilePath; var folder = ReadOptions(host).Folder;
        var settingsFile = _worker?.SettingsFile;
        Task.Run(() =>
        {
            var initial = LyricEditorWindow.ExistingWords(path);
            OnUi(() =>
            {
                if (!ReferenceEquals(_host, host)) return;
                var window = new LyricEditorWindow(Path.GetFileNameWithoutExtension(path), initial, words =>
                {
                    var tool = new Tool(folder);
                    if (!tool.Installed || tool.TooOld) throw new InvalidOperationException("Install or update WordLyrics first");
                    var drafts = Path.Combine(host.DataDirectory, "lyric drafts"); Directory.CreateDirectory(drafts);
                    if ((new DirectoryInfo(drafts).Attributes & FileAttributes.ReparsePoint) != 0) throw new IOException("Linked draft folder");
                    var file = Path.Combine(drafts, Guid.NewGuid().ToString("N") + ".txt");
                    using (var stream = new FileStream(file, FileMode.CreateNew, FileAccess.Write))
                    using (var writer = new StreamWriter(stream, new System.Text.UTF8Encoding(false))) writer.Write(words);
                    tool.OpenSongRepair(path, file, settingsFile);
                });
                _editors.Add(window); window.Closed += (_, _) => _editors.Remove(window);
                var owner = (Application.Current?.ApplicationLifetime as IClassicDesktopStyleApplicationLifetime)?.MainWindow;
                if (owner is null) window.Show(); else window.Show(owner);
            });
        });
    }

    private void OnSettingChanged(object? sender, string key)
    {
        var worker = _worker;
        if (_host is null || worker is null) return;
        if (key == "progress") { ShowProgress(); return; }
        Options options;
        try { options = ReadOptions(_host); }
        catch (Exception) { return; }
        var data = _host.DataDirectory;
        Task.Run(() =>
        {
            try
            {
                worker.Options = options;
                var tool = new Tool(options.Folder);
                if (key == SetupKey)
                {
                    tool.OpenSetup(data);
                }
                else if (key == LibraryKey)
                {
                    if (!tool.Installed) Notify("The WordLyrics program is not installed yet. Flip \"Install or update WordLyrics\" first.");
                    else tool.OpenLibraryMenu(worker.SettingsFile);
                }
            }
            catch (Exception ex)
            {
                Log($"setting \"{key}\": {ex.Message}");
            }
        });
    }

    // The worker calls these from its own threads; Noctis is only ever talked to on the UI thread.

    private void Log(string message) => OnUi(() => _host?.Log(message));

    private void Notify(string message) => OnUi(() => _host?.Notify(message));

    private void OnProgress(ProgressState state) => OnUi(() =>
    {
        if (_host is null) return;
        _lastProgress = state;
        if (state.Message == "Starting WordLyrics") _autoShownForJob = false;
        if (!_autoShownForJob && _host.Settings.GetBool("showProgress", true))
        {
            _autoShownForJob = true;
            if (_progress is null) ShowProgress();
        }
        _progress?.Update(state);
    });

    private void ShowProgress()
    {
        if (_host is null) return;
        if (_progress is not null) { _progress.Activate(); return; }
        var host = _host;
        var worker = _worker;
        var configuredFolder = ReadOptions(host).Folder;
        void Launch(bool setup) => Task.Run(() =>
        {
            if (_host is null) return;
            try
            {
                var tool = new Tool(configuredFolder);
                if (setup || !tool.Installed) tool.OpenSetup(host.DataDirectory);
                else if (worker is not null) tool.OpenLibraryMenu(worker.SettingsFile);
            }
            catch (Exception ex) { Notify("WORDLYRICS_WINDOW_FAILED: " + ex.GetType().Name + ". Open WordLyrics from its desktop shortcut."); }
        });
        var window = _progress = new ProgressWindow(() => Launch(true), () => Launch(false), path => Task.Run(() =>
        {
            try { new Tool(configuredFolder).OpenResume(path); }
            catch (Exception ex) { Notify("RESUME_OPEN_FAILED: " + ex.GetType().Name + ". Use WordLyrics > 7 Resume / fix unfinished jobs."); }
        }));
        window.Update(_lastProgress);
        window.Closed += (_, _) => { if (ReferenceEquals(_progress, window)) _progress = null; };
        var owner = (Application.Current?.ApplicationLifetime as IClassicDesktopStyleApplicationLifetime)?.MainWindow;
        if (owner is null) window.Show(); else window.Show(owner);
    }

    private static void OnUi(Action action)
    {
        void Run()
        {
            try { action(); }
            catch (Exception) { /* a notice that cannot be shown is not worth failing over */ }
        }
        if (Dispatcher.UIThread.CheckAccess()) Run();
        else Dispatcher.UIThread.Post(Run);
    }
}
