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
        ".mp3", ".flac", ".m4a", ".mp4", ".ogg", ".oga", ".opus", ".wav", ".aiff", ".aif", ".aifc", ".wma", ".aac", ".ape", ".wv", ".alac", ".asf", ".dsf", ".dff",
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
    private static readonly TimeSpan FolderCheck = TimeSpan.FromSeconds(60);
    private const int MaxSongsPerRun = 200;
    private const int MaxTries = 2;
    private const int ExitAlreadyRunning = 3;
    private const int ExitSetupFailed = 70;

    private readonly string _data;
    private readonly string _settingsFile;
    private readonly Action<string> _log;
    private readonly Action<string> _notify;
    private readonly Action<ProgressState> _progress;
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
    private bool _saidBadFolder;
    private bool _ticking;
    private bool _disposed;
    private bool _paused;               // the user paused: no new batch starts until Resume; the waiting list is kept
    private bool _cancelling;           // the user cancelled the run that is going; it is finishing the song it is on
    private bool _controlled;           // the run that is going looks at the control file (so the buttons are shown)
    private View _view = new("Starting WordLyrics", "", 0, 0, null, false);

    /// <summary>What the last progress update of the program said (before pause and cancel are added to it).</summary>
    private sealed record View(string Message, string Detail, int Completed, int Total, double? Fraction, bool EnginePaused);

    private sealed class Item
    {
        public string Path = "";
        public string Root = "";
        public bool Asked;              // the user asked for this song by hand
        public int Tries;               // runs that failed because of the song
        public int Waits;               // runs that could not do anything (no internet, broken setup): see Backoff
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
    public Worker(string dataDirectory, string settingsFile, Options options, Action<string> log, Action<string> notify, Action<ProgressState>? progress = null)
    {
        _data = dataDirectory;
        _settingsFile = settingsFile;
        _options = options;
        _log = log;
        _notify = notify;
        _progress = progress ?? (_ => { });
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
        set { lock (_gate) { _options = value; _saidBadFolder = false; } } // a changed setting may be the repair
    }

    public string SettingsFile => _settingsFile;

    /// <summary>How long the program gets to stop after Cancel before it is stopped by force.</summary>
    internal TimeSpan CancelGrace { get; set; } = TimeSpan.FromMinutes(3);

    /// <summary>
    /// Pause (true) or Resume (false), from the progress window. The program rests between two steps of
    /// the models; meanwhile no new batch is started and the waiting songs stay on the list.
    /// </summary>
    public void SetPaused(bool paused)
    {
        var resumed = false;
        lock (_gate)
        {
            if (_disposed || _paused == paused) return;
            _paused = paused;
            if (_run is not null && _controlled && !_cancelling) WriteControl(paused ? "pause" : "run");
            if (_run is null && !paused)
            {
                resumed = true;
                _progress(new ProgressState("Resumed", _items.Count == 0 ? "Nothing is waiting." : $"{_items.Count} song{(_items.Count == 1 ? " is" : "s are")} waiting and start in a moment."));
            }
            else Emit();
        }
        if (resumed) _timer.Change(TimeSpan.Zero, Tick);
    }

    /// <summary>
    /// Cancel, from the progress window: the program finishes the song it is timing and stops. When it
    /// has ended, the songs of this batch that it did not reach and every song still waiting are taken
    /// off the list (see <see cref="Finished"/>); a stop by the user is no failed try for any song.
    /// </summary>
    public void Cancel()
    {
        Process? active;
        lock (_gate)
        {
            active = _run;
            if (_disposed || active is null || !_controlled || _cancelling) return;
            _cancelling = true;
            _paused = false;
            WriteControl("stop");
            Emit();
        }
        // A program that does not react (stuck in a model call, say) is stopped by force after a while.
        Task.Run(async () =>
        {
            try { await Task.Delay(CancelGrace).ConfigureAwait(false); }
            catch (Exception) { return; }
            Job? job;
            lock (_gate)
            {
                if (!ReferenceEquals(_run, active)) return;
                job = _job;
            }
            Safe(() => _log("WordLyrics did not stop by itself in time; it is stopped now"));
            Safe(() =>
            {
                if (job is not null) job.Dispose();
                else if (!active.HasExited) active.Kill(entireProcessTree: true);
            });
        });
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
        if (HasWordLyrics(path) || EmbeddedWordLyrics.HasWords(path)) return "Word-timed lyrics already exist. To fix wrong words or timing, right-click this song and choose Wrong lyrics / redo with fresh lyrics (WordLyrics), or Edit / paste custom lyrics.";
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
            Quietly(() => File.Delete(ControlFile));
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
        if (_run is not null) ReadProgress();
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
            if (_paused) return;            // paused by the user: the waiting songs stay on the list until Resume
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
        Tool tool;
        try { tool = new Tool(options.Folder); }
        catch (Exception ex) when (ex is ArgumentException or NotSupportedException or IOException)
        {
            // Said once, not at every tick; the next change of a setting lets it be said again.
            if (!_saidBadFolder)
            {
                _saidBadFolder = true;
                var message = $"The WordLyrics folder in the plugin settings is not a usable folder name ({ex.GetType().Name}). Correct it in Settings → Plugins → WordLyrics; the waiting songs are kept.";
                _log(message); _notify(message);
                _progress(new ProgressState("Setup is needed", message, Error: true, Finished: true));
            }
            return;
        }
        _saidBadFolder = false;
        if (!tool.Ready)
        {
            if (!_saidNotReady)
            {
                _saidNotReady = true;
                _log("songs are waiting, but WordLyrics is not set up in " + tool.Folder);
                _notify(NotReady(tool));
                _progress(new ProgressState("Setup is needed", NotReady(tool), Error: true, Finished: true));
            }
            return;
        }
        _saidNotReady = false;
        try { Start(tool, batch, options); }
        catch (Exception ex)
        {
            foreach (var item in batch) item.NotBefore = now + TimeSpan.FromMinutes(2);
            var message = $"WORDLYRICS_START_FAILED ({ex.GetType().Name}): the background program could not start. Check the WordLyrics folder and installation. Retrying in two minutes.";
            _log(message); _notify(message);
            _progress(new ProgressState("WordLyrics could not start", message, Total: batch.Count, Finished: true, Error: true));
        }
    }

    /// <summary>Has the file been left alone long enough, and does it still need lyrics? Called under the lock.</summary>
    private bool Ripe(Item item, DateTime now)
    {
        if (now < item.NotBefore) return false;
        try
        {
            var file = new FileInfo(item.Path);
            if (!file.Exists)
            {
                if (!Directory.Exists(item.Root)) { item.NotBefore=now+TimeSpan.FromMinutes(1);return false; }
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
            if (HasWordLyrics(item.Path) || EmbeddedWordLyrics.HasWords(item.Path))
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
                        NotifyFilter = NotifyFilters.FileName | NotifyFilters.DirectoryName,
                        InternalBufferSize = 64 * 1024,
                    };
                    watcher.Created += (_, e) => Safe(() => Noticed(e.FullPath, folder, QuietAfterCreate));
                    watcher.Renamed += (_, e) => Safe(() => Noticed(e.FullPath, folder, QuietAfterRename));
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

    /// <summary>Something appeared (or got its final name) in a library folder: a song, or a folder that may hold songs.</summary>
    private void Noticed(string path, string root, TimeSpan quiet)
    {
        if (Audio.Contains(Path.GetExtension(path))) { Consider(path, root, quiet); return; }
        // A folder moved or copied in is announced by itself, not by the songs inside it; looking
        // inside happens on a thread of its own so that the watcher is never kept waiting.
        if (Directory.Exists(path)) Task.Run(() => Safe(() => ConsiderFolder(path, root, quiet)));
    }

    /// <summary>The songs inside a folder that was moved, copied or renamed into a library folder.</summary>
    private void ConsiderFolder(string folder, string root, TimeSpan quiet)
    {
        var listing = new EnumerationOptions { RecurseSubdirectories = true, IgnoreInaccessible = true, AttributesToSkip = FileAttributes.ReparsePoint };
        var found = 0;
        try
        {
            foreach (var file in new DirectoryInfo(folder).EnumerateFiles("*", listing))
            {
                if (_disposed) return;
                if (!Audio.Contains(file.Extension) || HasWordLyrics(file.FullName)) continue;
                if (Consider(file.FullName, root, quiet, save: false)) found++;
            }
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            // gone again already (a temporary name), or not ours to read: what was found stays
        }
        if (found == 0) return;
        lock (_gate)
            if (!_disposed) SaveState();
        _log($"{found} songs arrived inside the folder {folder}");
    }

    /// <summary>A file appeared (or got its final name) in a library folder. True when it was added to the list.</summary>
    private bool Consider(string path, string root, TimeSpan quiet, bool save = true)
    {
        if (!Audio.Contains(Path.GetExtension(path))) return false;
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
            if (_disposed || !_options.Auto || _items.ContainsKey(path)) return false;
            _items[path] = new Item { Path = path, Root = root, Seen = now, QuietSince = now, Quiet = quiet, NotBefore = now, Length = length, Stamp = stamp };
            if (save) SaveState();
            return true;
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
                if (file.CreationTimeUtc <= since || !Audio.Contains(file.Extension) || HasWordLyrics(file.FullName) || EmbeddedWordLyrics.HasWords(file.FullName)) continue;
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
    private string ProgressFile => Path.Combine(_data, "progress.json");
    private string StateFile => Path.Combine(_data, "state.json");
    private string ControlFile => Path.Combine(_data, "control.txt");

    private void Start(Tool tool, List<Item> batch, Options options)
    {
        File.WriteAllLines(ListFile, batch.Select(i => i.Path), new UTF8Encoding(false));
        File.Delete(ResultFile);
        File.Delete(ProgressFile);
        var speed = options.Speed.ToLowerInvariant() is "full" or "half" or "light" ? options.Speed.ToLowerInvariant() : "half";
        // Pause and Cancel need a program that looks at a control file (1.6.4 on); an older one runs without the buttons.
        var controlled = tool.SupportsControl;
        Process run;
        lock (_gate)
        {
            if (_disposed || _run is not null) return;
            if (controlled) WriteControl(_paused ? "pause" : "run");
            run = _run = tool.StartQuietRun(batch[0].Root, ListFile, ResultFile, OutputFile, speed, options.Backup, retry: batch[0].Asked, ProgressFile,
                controlled ? ControlFile : null);
            _job = Job.Around(run);
            _controlled = controlled;
            _cancelling = false;
            _view = new View("Starting WordLyrics", $"{batch.Count} songs queued. Preparing backup and lyric search...", 0, batch.Count, null, false);
        }
        _log($"started WordLyrics on {batch.Count} song{(batch.Count == 1 ? "" : "s")} ({speed} speed)");
        lock (_gate) Emit();
        Task.Run(async () =>
        {
            try { await run.WaitForExitAsync().ConfigureAwait(false); }
            catch (Exception) { /* killed while we waited */ }
            Safe(() => Finished(run, batch));
        });
    }

    private void ReadProgress()
    {
        Process? active;
        lock (_gate) active = _run;
        if (active is null) return;
        try
        {
            var file = new FileInfo(ProgressFile);
            if (!file.Exists || file.Length > 64 * 1024) return;
            using var doc = JsonDocument.Parse(File.ReadAllText(ProgressFile));
            var root = doc.RootElement;
            var completed = root.GetProperty("completed").GetInt32();
            var total = root.GetProperty("total").GetInt32();
            var detail = Text(root, "notice");
            string message = "Preparing WordLyrics";
            double? fraction = null;
            if (root.TryGetProperty("stages", out var stages))
                foreach (var stage in stages.EnumerateArray())
                    if (Text(stage, "state") == "running")
                    {
                        message = Text(stage, "name");
                        detail = string.Join("\n", new[] { Text(stage, "count"), Text(stage, "current"), Text(stage, "note"), Text(root, "notice") }.Where(s => s.Length > 0));
                        detail += "\nWorking - last update " + file.LastWriteTime.ToString("HH:mm:ss");
                        var count = stage.GetProperty("total").GetDouble();
                        fraction = count > 0 ? Math.Clamp(stage.GetProperty("done").GetDouble() / count, 0, 1) : null;
                    }
            var enginePaused = root.TryGetProperty("paused", out var rests) && rests.ValueKind == JsonValueKind.True;
            lock (_gate)
                if (ReferenceEquals(_run, active))
                {
                    _view = new View(message, detail, completed, total, fraction, enginePaused);
                    Emit();
                }
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or JsonException or InvalidOperationException or KeyNotFoundException or FormatException) { }
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
        var reason = "";
        var report = "";
        if (code != ExitAlreadyRunning && File.Exists(ResultFile))
        {
            try
            {
                using var document = JsonDocument.Parse(File.ReadAllText(ResultFile));
                var root = document.RootElement;
                error = Text(root, "error");
                report = Path.Combine(Text(root, "run_folder"), "report.html");
                if (root.TryGetProperty("songs", out var songs) && songs.ValueKind == JsonValueKind.Array)
                    foreach (var song in songs.EnumerateArray())
                    {
                        outcome[Usual(Text(song, "path"))] = (Text(song, "status"), Text(song, "title"));
                        if (reason.Length == 0 && Text(song, "status") is not "timed" and not "skipped") reason = Text(song, "reason");
                    }
                if (root.TryGetProperty("left_out", out var left) && left.ValueKind == JsonValueKind.Array)
                    foreach (var song in left.EnumerateArray())
                        outcome[Usual(Text(song, "path"))] = ("left_out", "");
            }
            catch (Exception ex) when (ex is IOException or JsonException or ArgumentException or InvalidOperationException or KeyNotFoundException or FormatException)
            {
                error = "the result could not be read: " + ex.Message;
            }
        }

        var counts = new Dictionary<string, int>();
        var titles = new Dictionary<string, string>();
        bool asked = batch[0].Asked, stopped, cancelled, held;
        var firstDelays = 0;
        var dropped = 0;
        lock (_gate)
        {
            stopped = _disposed;
            held = _paused;                 // paused too late for this run to notice: the songs still waiting stay held
            cancelled = _cancelling && ReferenceEquals(_run, run);
            if (ReferenceEquals(_run, run))
            {
                _run = null;
                _job?.Dispose();
                _job = null;
                _cancelling = false;
                _controlled = false;
                Quietly(() => File.Delete(ControlFile));
            }
            foreach (var item in batch)
            {
                var status = outcome.TryGetValue(item.Path, out var o) ? o.Status : "";
                var title = string.IsNullOrWhiteSpace(o.Title) ? Path.GetFileNameWithoutExtension(item.Path) : o.Title;
                if (code == ExitAlreadyRunning && !cancelled)
                {
                    item.NotBefore = now + TimeSpan.FromMinutes(1); // a run in another window: wait for it
                    continue;
                }
                if (stopped) continue; // Noctis is closing: the song stays on the list
                if (cancelled)
                {
                    // A stop by the user says nothing about a song: no failed try, no wait. What was finished
                    // stays finished (its lyrics are written); everything else is taken off the list below.
                    if (status is not ("timed" or "lines_only" or "no_lyrics" or "rejected" or "not_timed" or "skipped" or "left_out")) continue;
                }
                else
                {
                    if (status == "network_error" || (code == ExitSetupFailed && status is "" or "not_reached"))
                    {
                        // Not the song's fault, and it can last for hours (no internet, a broken setup):
                        // it keeps its tries and waits longer each time instead of being given up.
                        if (item.Waits == 0) firstDelays++;
                        item.NotBefore = now + Backoff(item.Waits++);
                        continue;
                    }
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
                }
                _items.Remove(item.Path);
                counts[status] = counts.GetValueOrDefault(status) + 1;
                titles[status] = title;
            }
            if (cancelled && !stopped)
            {
                // The songs of this batch that were not reached and all that were still waiting.
                dropped = _items.Count;
                _items.Clear();
            }
            SaveState();
        }
        if (stopped) return;

        if (cancelled)
        {
            var finished = counts.Values.Sum();
            _log($"WordLyrics was cancelled: {finished} finished, {dropped} taken off the waiting list");
            _progress(new ProgressState("Cancelled", CancelNotice(finished, dropped) + $"\nDetails: {ResultFile}", finished, batch.Count, Finished: true, Report: report));
            _notify(CancelNotice(finished, dropped));
            return;
        }

        if (code == ExitAlreadyRunning)
        {
            _log("WordLyrics is running in another window; trying again in a minute");
            _progress(new ProgressState("Waiting for another WordLyrics run", "Another window is using the model. This batch will retry in a minute.", Total: batch.Count, Paused: held));
            return;
        }
        if (code == ExitSetupFailed)
        {
            _progress(new ProgressState("Setup is needed", "ENVIRONMENT_REPAIR_FAILED: private packages could not be repaired. Open Set up WordLyrics; the queued songs are saved and tried again later. Startup log: " + OutputFile, Finished: true, Error: true, Report: report, Paused: held));
            return;
        }
        _log($"WordLyrics finished (exit code {code}): " +
             (counts.Count == 0 ? "to be tried again" : string.Join(", ", counts.Select(c => $"{c.Value} {c.Key}"))) +
             (error.Length > 0 ? " - " + error : ""));
        var message = Summary(counts, titles, asked);
        var timed = counts.GetValueOrDefault("timed");
        var failed = counts.Where(c => c.Key is not "timed" and not "skipped").Sum(c => c.Value);
        var detail = string.Join("\n", new[] { error, reason, $"Details: {ResultFile}", $"Startup log: {OutputFile}" }.Where(s => s.Length > 0));
        var retrying = counts.Count == 0 && outcome.Count != 0 || (counts.Count == 0 && code != 0);
        _progress(new ProgressState(retrying ? "Retry scheduled" : timed > 0 ? $"{timed} songs received word timestamps" : "No new word timestamps were written",
            detail + (failed > 0 ? $"\n{failed} songs need attention." : "") + (held ? "\nPaused: the songs still waiting start when you press Resume." : ""),
            counts.Values.Sum(), batch.Count, Finished: true, Error: failed > 0 || code != 0, Report: report, Paused: held));
        if (message is not null && (asked || Options.Notify)) _notify(message);
        // Said when a song first has to wait, not at every later try.
        if (firstDelays > 0 && (asked || Options.Notify))
            _notify($"The lyric provider could not be reached. {firstDelays} song{(firstDelays == 1 ? "" : "s")} will be tried again later.");
        _timer.Change(TimeSpan.FromSeconds(1), Tick); // anything else waiting?
    }

    /// <summary>The one notice after Cancel: what was kept, what was dropped, and how to start those songs again.</summary>
    internal static string CancelNotice(int finished, int dropped)
    {
        static string Songs(int n) => n == 1 ? "1 song" : $"{n} songs";
        var done = finished == 0 ? "" : $" {Songs(finished)} finished before the stop and keep their lyrics.";
        return dropped == 0
            ? "WordLyrics stopped." + done + " Nothing else was waiting."
            : $"WordLyrics stopped.{done} {Songs(dropped)} {(dropped == 1 ? "was" : "were")} taken off the waiting list. " +
              $"Start {(dropped == 1 ? "it" : "them")} again from the song's right-click menu (Time the words) or from the WordLyrics program.";
    }

    /// <summary>Tells the progress window how things stand, with pause and cancel added to what the program last said. Called under the lock.</summary>
    private void Emit()
    {
        if (_run is null)
        {
            if (_paused)
                _progress(new ProgressState("Paused", $"{_items.Count} song{(_items.Count == 1 ? " is" : "s are")} waiting. Press Resume to continue.", Paused: true));
            return;
        }
        var view = _view;
        var message = view.Message;
        var detail = view.Detail;
        if (_cancelling)
        {
            message = "Stopping WordLyrics";
            detail = "The song being timed is finished first. Songs that were not reached, and the songs still waiting, are taken off the list.\n" + detail;
        }
        else if (_paused)
        {
            message = "Paused";
            detail = (view.EnginePaused ? "WordLyrics is paused. Press Resume to continue." : "Pausing: the step in progress is finishing. Press Resume to continue.") + "\n" + detail;
        }
        _progress(new ProgressState(message, detail, view.Completed, view.Total, view.Fraction,
            Controllable: _controlled, Paused: _paused && !_cancelling, Cancelling: _cancelling));
    }

    /// <summary>Writes "pause", "run" or "stop" for the program to find. Called under the lock; the file is replaced whole.</summary>
    private void WriteControl(string word)
    {
        try
        {
            var temporary = ControlFile + ".tmp";
            File.WriteAllText(temporary, word, new UTF8Encoding(false));
            File.Move(temporary, ControlFile, overwrite: true);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            _log("the control file could not be written: " + ex.Message);
        }
    }

    /// <summary>How long a song waits after a run that could not do anything: 10 minutes, 30 minutes, an hour, then every 3 hours.</summary>
    internal static TimeSpan Backoff(int waits) => waits switch
    {
        <= 0 => TimeSpan.FromMinutes(10),
        1 => TimeSpan.FromMinutes(30),
        2 => TimeSpan.FromHours(1),
        _ => TimeSpan.FromHours(3),
    };

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
                "network_error" => $"Lyric provider unavailable. Open WordLyrics progress for the connection error: {title}",
                "rejected" => $"The model could not confirm the online lyrics for this recording: {title}",
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
                if (path.Length == 0 || folder.Length == 0 || (!File.Exists(path) && Directory.Exists(folder))) continue;
                _items[path] = new Item
                {
                    Path = path, Root = folder, Seen = now, QuietSince = now, NotBefore = now,
                    Asked = entry.TryGetProperty("asked", out var asked) && asked.ValueKind == JsonValueKind.True,
                };
            }
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            _log("the list of waiting songs could not be read: " + ex.Message);
        }
        catch (Exception ex) when (ex is JsonException or InvalidOperationException or KeyNotFoundException or FormatException)
        {
            // Not in a form this plugin wrote: Noctis must still start. The file is kept for a look
            // (the next save replaces state.json), and the list starts empty.
            _items.Clear();
            try { File.Copy(StateFile, StateFile + ".broken", overwrite: true); }
            catch (Exception copy) when (copy is IOException or UnauthorizedAccessException) { }
            _log("the list of waiting songs was not readable (" + ex.Message + "); a copy was kept as state.json.broken and the list starts empty");
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
            try { _progress(new ProgressState("WordLyrics encountered an error", $"WORKER_ERROR ({ex.GetType().Name}): {ex.Message}", Finished: true, Error: true)); } catch { }
        }
    }

    private static void Quietly(Action action)
    {
        try { action(); } catch { /* shutting down */ }
    }
}
