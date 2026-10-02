using Avalonia.Threading;
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

    public PluginInfo Info { get; } = new(
        Id: "dev.moshi.wordlyrics",
        Name: "WordLyrics",
        Version: "1.0.0",
        Author: "moshi",
        Description: "Word-by-word lyrics for new songs, by themselves: the WordLyrics program finds the lyrics of a song that was added to the library and times every word in the background.");

    public void Initialize(IPluginHost host)
    {
        _host = host;
        // The plugin API does not say where the library folders are; Noctis's own settings file,
        // two levels above the plugin's data folder, does.
        var settings = Path.GetFullPath(Path.Combine(host.DataDirectory, "..", "..", "settings.json"));
        _worker = new Worker(host.DataDirectory, settings, ReadOptions(host), Log, Notify);

        _registrations.Add(host.RegisterTrackCommand(
            "Time the words (WordLyrics)",
            // A 24×24 SVG path: lines of text.
            "M20 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zM4 12h4v2H4v-2zm10 6H4v-2h10v2zm6 0h-4v-2h4v2zm0-4H10v-2h10v2z",
            (Action<TrackInfo>)OnTrackCommand));

        host.Settings.Changed += OnSettingChanged;
        host.Log("ready");
    }

    public void Shutdown()
    {
        if (_host is not null) _host.Settings.Changed -= OnSettingChanged;
        foreach (var registration in _registrations) registration.Dispose();
        _registrations.Clear();
        _worker?.Dispose();
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

    private void OnSettingChanged(object? sender, string key)
    {
        var worker = _worker;
        if (_host is null || worker is null) return;
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
                    var folder = worker.LibraryFolders().FirstOrDefault();
                    if (!tool.Installed) Notify("The WordLyrics program is not installed yet. Flip \"Install or update WordLyrics\" first.");
                    else if (folder is null) Notify("Noctis has no library folder yet.");
                    else tool.OpenOnFolder(folder);
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
