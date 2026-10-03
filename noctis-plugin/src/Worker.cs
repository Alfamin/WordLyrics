using System.Diagnostics;
using System.Text;
using System.Text.Json;

namespace WordLyricsForNoctis;

/// <summary>What the user chose in Settings → Plugins. Read on the UI thread, used everywhere.</summary>
internal sealed record Options(bool Auto, string Speed, bool Notify, string Folder, string Backup);

/// <summary>
/// Notices new songs in the library folders and hands them to the WordLyrics program, one run at
/// a time, without a window.
///
/// A song is handed over only after its file has been left alone for a while (a download or a
/// copy is finished, and Noctis has written what it writes), and never when it already has
/// word-by-word lyrics. The plugin itself reads and writes nothing in the music folders: the
/// program does the work, and it only ever adds new lyric files next to a song.
/// </summary>
internal sealed class Worker : IDisposable
{
    private static readonly HashSet<string> Audio = new(StringComparer.OrdinalIgnoreCase)
    {
        ".mp3", ".flac", ".m4a", ".mp4", ".ogg", ".oga", ".opus", ".wav", ".aiff", ".aif", ".wma", ".aac", ".ape", ".wv",
    };
    // A song with one of these next to it already has word-by-word lyrics.
    private static readonly string[] WordLyrics = { ".elrc", ".ttml", ".lyricsfile" };

    private static readonly TimeSpan Tick = TimeSpan.FromSeconds(1);
    // How long a new file has to stay unchanged. A file that got its name by a rename (a download
    // saved under a temporary name, a song renamed by hand) was whole before it got that name; one
    // that was created under its name may still be written in pieces. Anyone who still has the file
    // open for writing holds it back anyway (see Ripe).
    private static readonly TimeSpan QuietAfterRename = TimeSpan.FromSeconds(2);
    private static readonly TimeSpan QuietAfterCreate = TimeSpan.FromSeconds(10);
    private static readonly TimeSpan GiveUpWaiting = TimeSpan.FromHours(6);
    private static readonly TimeSpan FolderCheck = TimeSpan.FromSeconds(60);
    private const int MaxSongsPerRun = 200;
    private const int MaxTries = 2;
    private const int ExitAlreadyRunning = 3;

    private readonly string _data;
    private readonly string _settingsFile;
    private readonly Action<string> _log;
    private readonly Action<string> _notify;
    private readonly object _gate = new();
    private readonly Dictionary<string, Item> _items = new(StringComparer.OrdinalIgnoreCase);
    private readonly Dictionary<string, FileSystemWatcher> _watchers = new(StringComparer.OrdinalIgnoreCase);
    private readonly Timer _timer;
    private Options _options;
    private Process? _run;
    private Job? _job;
    private DateTime _foldersChecked = DateTime.MinValue;
    private DateTime _stateSaved = DateTime.MinValue;
    private DateTime _lastSeen;
    private bool _saidNotReady;
    private bool _ticking;
    private bool _disposed;

    private sealed class Item
    {
        public string Path = "";
        public string Root = "";
        public bool Asked;              // the user asked for this song by hand
        public int Tries;
        public DateTime Seen;
        public DateTime QuietSince;
        public TimeSpan Quiet = QuietAfterCreate;
        public DateTime NotBefore;
        public long Length = -1;
        public DateTime Stamp;
    }

    /// <param name="dataDirectory">The plugin's own folder (list of waiting songs, last result, last output).</param>
    /// <param name="settingsFile">Noctis's settings file, which names the library folders.</param>
    /// <param name="log">Called from any thread.</param>
    /// <param name="notify">Called from any thread.</param>
    public Worker(string dataDirectory, string settingsFile, Options options, Action<string> log, Action<string> notify)
    {
        _data = dataDirectory;
        _settingsFile = settingsFile;
        _options = options;
        _log = log;
        _notify = notify;
        Directory.CreateDirectory(_data);

        var first = !File.Exists(StateFile);
        LoadState();
        var since = _lastSeen;
        // Songs that arrived while Noctis was closed. The very first time there is nothing to
        // catch up with: the library as it is belongs to "Time the whole library".
        if (!first) Task.Run(() => Safe(() => CatchUp(since)));

        _timer = new Timer(_ => OnTick(), null, TimeSpan.FromSeconds(2), Tick);
    }

    public Options Options
    {
        get { lock (_gate) return _options; }
        set { lock (_gate) _options = value; }
    }

    /// <summary>The Noctis library folders that exist right now.</summary>
    public IReadOnlyList<string> LibraryFolders()
    {
        try
        {
            if (!File.Exists(_settingsFile)) return Array.Empty<string>();
            using var document = JsonDocument.Parse(File.ReadAllText(_settingsFile));
            if (!document.RootElement.TryGetProperty("musicFolders", out var folders) || folders.ValueKind != JsonValueKind.Array)
                return Array.Empty<string>();
            return folders.EnumerateArray()
                .Where(f => f.ValueKind == JsonValueKind.String)
                .Select(f => f.GetString()!)
                .Where(f => !string.IsNullOrWhiteSpace(f) && Directory.Exists(f))
                .Select(f => Path.GetFullPath(f))
                .ToList();
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or JsonException)
        {
            return Array.Empty<string>(); // being written this moment: asked again a minute later
        }
    }

    /// <summary>The user asked for this song. Returns what to tell them right away, or null.</summary>
    public string? Ask(string path)
    {
        if (string.IsNullOrWhiteSpace(path) || !File.Exists(path)) return "The song's file is not there.";
        if (!Audio.Contains(Path.GetExtension(path))) return "WordLyrics cannot read this kind of file.";
        if (HasWordLyrics(path)) return "This song already has word-by-word lyrics.";
        var tool = new Tool(Options.Folder);
        if (!tool.Ready) return NotReady(tool);

        path = Path.GetFullPath(path);
        var root = LibraryFolders().FirstOrDefault(f => Inside(path, f)) ?? Path.GetDirectoryName(path)!;
        var now = DateTime.UtcNow;
        lock (_gate)
        {
            if (_disposed) return null;
            _items[path] = new Item { Path = path, Root = root, Asked = true, Seen = now, QuietSince = now, NotBefore = now };
            SaveState();
        }
        _timer.Change(TimeSpan.Zero, Tick);
        return _run is null ? "Timing the words… this takes a moment." : "Queued: WordLyrics is busy with another song.";
    }

    public void Dispose()
    {
        Process? run;
        Job? job;
        lock (_gate)
        {
            if (_disposed) return;
            _disposed = true;
            run = _run;
            job = _job;
            _run = null;
            _job = null;
            foreach (var watcher in _watchers.Values) Quietly(watcher.Dispose);
            _watchers.Clear();
            _lastSeen = DateTime.UtcNow;
            SaveState();
        }
        Quietly(_timer.Dispose);
        // The program only ever adds complete files, so it can be stopped at any moment; the
        // songs it had not reached are still on the list and are done next time.
        if (run is not null)
        {
            try
            {
                if (job is not null) job.Dispose();
                else if (!run.HasExited) run.Kill(entireProcessTree: true);
            }
            catch (Exception ex) { Safe(() => _log($"WordLyrics could not be stopped: {ex.GetType().Name}: {ex.Message}")); }
        }
    }

    // ── noticing new songs ──

    private void OnTick()
    {
        lock (_gate)
        {
            if (_disposed || _ticking) return;
            _ticking = true;
        }
        try { Safe(Work); }
        finally { lock (_gate) _ticking = false; }
    }

    private void Work()
    {
        var now = DateTime.UtcNow;
        if (now - _foldersChecked >= FolderCheck)
        {
            _foldersChecked = now;
            WatchFolders();
        }

        List<Item> waiting;
        lock (_gate)
        {
            if (_disposed) return;
            if (now - _stateSaved >= TimeSpan.FromMinutes(5))
            {
                _lastSeen = now;
                SaveState();
            }
            if (_run is not null || _items.Count == 0) return;
            waiting = _items.Values.ToList();
        }

        // Looking at the files happens outside the lock: a slow drive must never make Noctis
        // wait (it calls into the plugin from its own thread). Only this timer changes these
        // entries while no run is going.
        var ripe = waiting.Where(i => Ripe(i, now)).OrderByDescending(i => i.Asked).ThenBy(i => i.Seen).ToList();
        var gone = waiting.Where(i => i.Length == -2).ToList();
        if (gone.Count > 0)
        {
            lock (_gate)
            {
                foreach (var item in gone) _items.Remove(item.Path);
                SaveState();
            }
        }
        if (ripe.Count == 0) return;
        var batch = ripe.Where(i => i.Asked == ripe[0].Asked && SamePath(i.Root, ripe[0].Root)).Take(MaxSongsPerRun).ToList();

        var options = Options;
        var tool = new Tool(options.Folder);
        if (!tool.Ready)
        {
            if (!_saidNotReady)
            {
                _saidNotReady = true;
                _log("songs are waiting, but WordLyrics is not set up in " + tool.Folder);
                _notify(NotReady(tool));
            }
            return;
        }
        _saidNotReady = false;
        Start(tool, batch, options);
    }

    /// <summary>Has the file been left alone long enough, and does it still need lyrics? Called under the lock.</summary>
    private bool Ripe(Item item, DateTime now)
    {
        if (now < item.NotBefore) return false;
        try
        {
            var file = new FileInfo(item.Path);
            if (!file.Exists || now - item.Seen > GiveUpWaiting)
            {
                if (!file.Exists && now - item.Seen < TimeSpan.FromMinutes(1)) return false; // may be on its way
                item.Length = -2; // taken off the list
                return false;
            }
            if (file.Length != item.Length || file.LastWriteTimeUtc != item.Stamp)
            {
                item.Length = file.Length;
                item.Stamp = file.LastWriteTimeUtc;
                if (!item.Asked) item.QuietSince = now;
            }
            if (file.Length == 0 || (!item.Asked && now - item.QuietSince < item.Quiet)) return false;
            // Still being written by whoever brought it?
            using (new FileStream(item.Path, FileMode.Open, FileAccess.Read, FileShare.Read)) { }
            if (HasWordLyrics(item.Path))
            {
                item.Length = -2;
                return false;
            }
            return true;
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            item.QuietSince = now;
            return false;
        }
        catch (Exception)
        {
            item.Length = -2; // a path nothing can be done with
            return false;
        }
    }

    private void WatchFolders()
    {
        var folders = LibraryFolders();
        lock (_gate)
        {
            if (_disposed) return;
            foreach (var old in _watchers.Keys.Where(k => !folders.Contains(k, StringComparer.OrdinalIgnoreCase)).ToList())
            {
                Quietly(_watchers[old].Dispose);
                _watchers.Remove(old);
            }
            foreach (var folder in folders)
            {
                if (_watchers.ContainsKey(folder)) continue;
                try
                {
                    var watcher = new FileSystemWatcher(folder)
                    {
                        IncludeSubdirectories = true,
                        NotifyFilter = NotifyFilters.FileName,
                        InternalBufferSize = 64 * 1024,
                    };
                    watcher.Created += (_, e) => Safe(() => Consider(e.FullPath, folder, QuietAfterCreate));
                    watcher.Renamed += (_, e) => Safe(() => Consider(e.FullPath, folder, QuietAfterRename));
                    watcher.Error += (_, e) => Safe(() => OnWatcherError(folder, e.GetException()));
                    watcher.EnableRaisingEvents = true;
                    _watchers[folder] = watcher;
                    _log("watching " + folder);
                }
                catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or ArgumentException)
                {
                    _log($"cannot watch {folder}: {ex.Message}");
                }
            }
        }
    }

    private void OnWatcherError(string folder, Exception? error)
    {
        _log($"the watch on {folder} broke ({error?.Message}); it is set up again");
        var since = DateTime.UtcNow - TimeSpan.FromMinutes(2);
        lock (_gate)
        {
            if (_watchers.Remove(folder, out var watcher)) Quietly(watcher.Dispose);
            _foldersChecked = DateTime.MinValue;
        }
        CatchUp(since); // whatever arrived while nobody was looking
    }

    /// <summary>A file appeared (or got its final name) in a library folder.</summary>
    private void Consider(string path, string root, TimeSpan quiet)
    {
        if (!Audio.Contains(Path.GetExtension(path))) return;
        // Taken now, so that the quiet time counts from this moment and not from the first look.
        long length = -1;
        var stamp = default(DateTime);
        try
        {
            var file = new FileInfo(path);
            if (file.Exists) (length, stamp) = (file.Length, file.LastWriteTimeUtc);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException) { }
        var now = DateTime.UtcNow;
        lock (_gate)
        {
            if (_disposed || !_options.Auto || _items.ContainsKey(path)) return;
            _items[path] = new Item { Path = path, Root = root, Seen = now, QuietSince = now, Quiet = quiet, NotBefore = now, Length = length, Stamp = stamp };
            SaveState();
        }
    }

    /// <summary>Songs created after <paramref name="since"/> that nobody told us about.</summary>
    private void CatchUp(DateTime since)
    {
        if (!Options.Auto) return;
        var listing = new EnumerationOptions { RecurseSubdirectories = true, IgnoreInaccessible = true, AttributesToSkip = FileAttributes.ReparsePoint };
        foreach (var folder in LibraryFolders())
        {
            var found = 0;
            foreach (var file in new DirectoryInfo(folder).EnumerateFiles("*", listing))
            {
                if (_disposed) return;
                if (file.CreationTimeUtc <= since || !Audio.Contains(file.Extension) || HasWordLyrics(file.FullName)) continue;
                Consider(file.FullName, folder, QuietAfterCreate);
                found++;
            }
            if (found > 0) _log($"{found} songs arrived in {folder} while Noctis was closed");
        }
    }

    // ── running the program ──

    private string ListFile => Path.Combine(_data, "songs.txt");
    private string ResultFile => Path.Combine(_data, "result.json");
    private string OutputFile => Path.Combine(_data, "last run.log");
    private string StateFile => Path.Combine(_data, "state.json");

    private void Start(Tool tool, List<Item> batch, Options options)
    {
        File.WriteAllLines(ListFile, batch.Select(i => i.Path), new UTF8Encoding(false));
        File.Delete(ResultFile);
        var speed = options.Speed.ToLowerInvariant() is "full" or "half" or "light" ? options.Speed.ToLowerInvariant() : "half";
        Process run;
        lock (_gate)
        {
            if (_disposed || _run is not null) return;
            run = _run = tool.StartQuietRun(batch[0].Root, ListFile, ResultFile, OutputFile, speed, options.Backup, retry: batch[0].Asked);
            _job = Job.Around(run);
        }
        _log($"started WordLyrics on {batch.Count} song{(batch.Count == 1 ? "" : "s")} ({speed} speed)");
        Task.Run(async () =>
        {
            try { await run.WaitForExitAsync().ConfigureAwait(false); }
            catch (Exception) { /* killed while we waited */ }
            Safe(() => Finished(run, batch));
        });
    }

    private void Finished(Process run, List<Item> batch)
    {
        int code;
        try { code = run.ExitCode; }
        catch (InvalidOperationException) { code = -1; }
        Quietly(run.Dispose);

        var now = DateTime.UtcNow;
        var outcome = new Dictionary<string, (string Status, string Title)>(StringComparer.OrdinalIgnoreCase);
        var error = "";
        if (code != ExitAlreadyRunning && File.Exists(ResultFile))
        {
            try
            {
                using var document = JsonDocument.Parse(File.ReadAllText(ResultFile));
                var root = document.RootElement;
                error = Text(root, "error");
                if (root.TryGetProperty("songs", out var songs) && songs.ValueKind == JsonValueKind.Array)
                    foreach (var song in songs.EnumerateArray())
                        outcome[Usual(Text(song, "path"))] = (Text(song, "status"), Text(song, "title"));
                if (root.TryGetProperty("left_out", out var left) && left.ValueKind == JsonValueKind.Array)
                    foreach (var song in left.EnumerateArray())
                        outcome[Usual(Text(song, "path"))] = ("left_out", "");
            }
            catch (Exception ex) when (ex is IOException or JsonException or ArgumentException)
            {
                error = "the result could not be read: " + ex.Message;
            }
        }

        var counts = new Dictionary<string, int>();
        var titles = new Dictionary<string, string>();
        bool asked = batch[0].Asked, stopped;
        lock (_gate)
        {
            stopped = _disposed;
            if (ReferenceEquals(_run, run))
            {
                _run = null;
                _job?.Dispose();
                _job = null;
            }
            foreach (var item in batch)
            {
                var status = outcome.TryGetValue(item.Path, out var o) ? o.Status : "";
                var title = string.IsNullOrWhiteSpace(o.Title) ? Path.GetFileNameWithoutExtension(item.Path) : o.Title;
                if (code == ExitAlreadyRunning)
                {
                    item.NotBefore = now + TimeSpan.FromMinutes(1); // a run in another window: wait for it
                    continue;
                }
                if (stopped) continue; // Noctis is closing: the song stays on the list
                if (status is "" or "failed" or "not_reached")
                {
                    // Noctis may have been writing to the file, or the run broke off: once more, later
                    if (++item.Tries < MaxTries)
                    {
                        item.NotBefore = now + TimeSpan.FromMinutes(2);
                        continue;
                    }
                    status = status.Length == 0 ? "failed" : status;
                }
                _items.Remove(item.Path);
                counts[status] = counts.GetValueOrDefault(status) + 1;
                titles[status] = title;
            }
            SaveState();
        }
        if (stopped) return;

        if (code == ExitAlreadyRunning)
        {
            _log("WordLyrics is running in another window; trying again in a minute");
            return;
        }
        _log($"WordLyrics finished (exit code {code}): " +
             (counts.Count == 0 ? "to be tried again" : string.Join(", ", counts.Select(c => $"{c.Value} {c.Key}"))) +
             (error.Length > 0 ? " - " + error : ""));
        var message = Summary(counts, titles, asked);
        if (message is not null && (asked || Options.Notify)) _notify(message);
        _timer.Change(TimeSpan.FromSeconds(1), Tick); // anything else waiting?
    }

    /// <summary>One sentence for the notice pill, or null when there is nothing worth saying.</summary>
    private static string? Summary(Dictionary<string, int> counts, Dictionary<string, string> titles, bool asked)
    {
        int Count(string status) => counts.GetValueOrDefault(status);
        var total = counts.Values.Sum();
        if (total == 0) return null;
        if (total == 1)
        {
            var (status, title) = (counts.Keys.First(), titles.Values.First());
            return status switch
            {
                "timed" => $"Word-by-word lyrics are ready: {title}",
                "lines_only" => $"Line-by-line lyrics written (words could not be timed): {title}",
                "no_lyrics" => $"No lyrics were found for: {title}",
                "rejected" => $"The lyrics found online do not match the recording: {title}",
                "not_timed" => $"The lyrics could not be timed well enough: {title}",
                "skipped" => asked ? $"Nothing to do (it already has word-by-word lyrics, or a twin file gets them): {title}" : null,
                "left_out" => asked ? $"WordLyrics could not use this file: {title}" : null,
                _ => $"WordLyrics could not read the song: {title}",
            };
        }
        var parts = new List<string>();
        if (Count("timed") > 0) parts.Add($"{Count("timed")} got word-by-word lyrics");
        if (Count("lines_only") > 0) parts.Add($"{Count("lines_only")} got line-by-line lyrics");
        if (Count("no_lyrics") > 0) parts.Add($"{Count("no_lyrics")} without lyrics");
        var other = total - Count("timed") - Count("lines_only") - Count("no_lyrics") - Count("skipped") - Count("left_out");
        if (other > 0) parts.Add($"{other} could not be timed");
        return parts.Count == 0 ? null : $"WordLyrics, {total} new songs: " + string.Join(", ", parts);
    }

    private static string NotReady(Tool tool)
    {
        const string how = " Settings → Plugins → WordLyrics → flip \"Install or update WordLyrics\".";
        if (!tool.Installed) return "The WordLyrics program is not installed." + how;
        return (tool.TooOld ? "The WordLyrics program needs an update." : "WordLyrics is not set up yet.") + how;
    }

    // ── the list of waiting songs, kept across restarts ──

    private void LoadState()
    {
        _lastSeen = DateTime.UtcNow;
        try
        {
            if (!File.Exists(StateFile)) return;
            using var document = JsonDocument.Parse(File.ReadAllText(StateFile));
            var root = document.RootElement;
            if (root.TryGetProperty("lastSeenUtc", out var seen) && seen.TryGetDateTime(out var when)) _lastSeen = when.ToUniversalTime();
            if (!root.TryGetProperty("waiting", out var waiting) || waiting.ValueKind != JsonValueKind.Array) return;
            var now = DateTime.UtcNow;
            foreach (var entry in waiting.EnumerateArray())
            {
                string path = Text(entry, "path"), folder = Text(entry, "root");
                if (path.Length == 0 || folder.Length == 0 || !File.Exists(path)) continue;
                _items[path] = new Item
                {
                    Path = path, Root = folder, Seen = now, QuietSince = now, NotBefore = now,
                    Asked = entry.TryGetProperty("asked", out var asked) && asked.ValueKind == JsonValueKind.True,
                };
            }
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or JsonException)
        {
            _log("the list of waiting songs could not be read: " + ex.Message);
        }
    }

    /// <summary>Called under the lock.</summary>
    private void SaveState()
    {
        _stateSaved = DateTime.UtcNow;
        try
        {
            using var stream = new MemoryStream();
            using (var json = new Utf8JsonWriter(stream, new JsonWriterOptions { Indented = true }))
            {
                json.WriteStartObject();
                json.WriteString("lastSeenUtc", _lastSeen);
                json.WriteStartArray("waiting");
                foreach (var item in _items.Values.Where(i => i.Length != -2))
                {
                    json.WriteStartObject();
                    json.WriteString("path", item.Path);
                    json.WriteString("root", item.Root);
                    json.WriteBoolean("asked", item.Asked);
                    json.WriteEndObject();
                }
                json.WriteEndArray();
                json.WriteEndObject();
            }
            var temporary = StateFile + ".tmp";
            File.WriteAllBytes(temporary, stream.ToArray());
            File.Move(temporary, StateFile, overwrite: true);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            _log("the list of waiting songs could not be saved: " + ex.Message);
        }
    }

    // ── small helpers ──

    private static string Text(JsonElement element, string name)
        => element.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.String ? value.GetString() ?? "" : "";

    private static bool HasWordLyrics(string song)
    {
        var stem = Path.Combine(Path.GetDirectoryName(song) ?? "", Path.GetFileNameWithoutExtension(song));
        return WordLyrics.Any(extension => File.Exists(stem + extension));
    }

    /// <summary>The program names very long paths in Windows' long form; the list uses the usual one.</summary>
    private static string Usual(string path)
    {
        const string longForm = @"\\?\";
        if (path.StartsWith(longForm + @"UNC\", StringComparison.OrdinalIgnoreCase)) return @"\\" + path[(longForm.Length + 4)..];
        return path.StartsWith(longForm, StringComparison.Ordinal) ? path[longForm.Length..] : path;
    }

    private static bool SamePath(string a, string b) => string.Equals(a, b, StringComparison.OrdinalIgnoreCase);

    private static bool Inside(string path, string folder)
        => path.StartsWith(folder.TrimEnd('\\', '/') + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase);

    /// <summary>Nothing that goes wrong on one of our own threads may reach Noctis.</summary>
    private void Safe(Action action)
    {
        try { action(); }
        catch (Exception ex)
        {
            try { _log($"error: {ex.GetType().Name}: {ex.Message}"); } catch { /* nothing more to do */ }
        }
    }

    private static void Quietly(Action action)
    {
        try { action(); } catch { /* shutting down */ }
    }
}
