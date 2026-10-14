using System.Reflection;
using System.Text.Json;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Headless;
using Avalonia.Interactivity;
using Avalonia.Themes.Fluent;
using Avalonia.Threading;
using Noctis.Plugins;
using WordLyricsForNoctis;

internal static class WorkflowTests
{
    private static int _passed;
    private static readonly BindingFlags Hidden = BindingFlags.Instance | BindingFlags.NonPublic;
    private static string _root = "";
    private static void Check(bool condition, string message) { if (!condition) throw new Exception(message); _passed++; }
    private static T Field<T>(object target, string name) => (T)target.GetType().GetField(name, Hidden)!.GetValue(target)!;

    [STAThread]
    public static int Main(string[] args)
    {
        _root = Path.Combine(Path.GetTempPath(), "wordlyrics-workflow-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(_root);
        var oldPython = Environment.GetEnvironmentVariable("WORDLYRICS_PYTHON");
        try
        {
            Check(EmbeddedWordLyrics.HasWords("synthetic",_=>"[00:01.00]<00:01.00>alpha <00:01.50>beta"),"embedded word timing skips automatic model work");
            Check(!EmbeddedWordLyrics.HasWords("synthetic",_=>"[00:01.00]alpha beta"),"line timing remains eligible for word alignment");
            Check(!EmbeddedWordLyrics.HasWords("synthetic",_=>throw new IOException("unreadable")),"optional native reader failure leaves full worker fallback available");
            AppBuilder.Configure<TestApp>().UseSkia().UseHarfBuzz().UseHeadless(new AvaloniaHeadlessPlatformOptions { UseHeadlessDrawing = false }).SetupWithoutStarting();
            Backend(args[0]);
            Controls(args[0]);
            Windows(args.Length > 1 ? args[1] : null);
            Console.WriteLine($"PASS: {_passed} WordLyrics plugin workflow assertions; headless windows, synthetic songs, no models or network.");
            return 0;
        }
        catch (Exception ex) { Console.WriteLine(ex); return 1; }
        finally { Environment.SetEnvironmentVariable("WORDLYRICS_PYTHON", oldPython); }
    }

    private static void Backend(string python)
    {
        var app = Path.Combine(_root, "tool");
        var module = Path.Combine(app, "wordlyrics"); Directory.CreateDirectory(module);
        File.WriteAllText(Path.Combine(module, "__init__.py"), "__version__ = \"1.6.4\"\n");
        File.WriteAllText(Path.Combine(app, "WordLyrics.bat"), "@echo off\r\nexit /b 99\r\n");
        File.WriteAllText(Path.Combine(module, "runner.py"), "import runpy\nrunpy.run_module('wordlyrics',run_name='__main__')\n");
        File.WriteAllText(Path.Combine(module, "__main__.py"), """
            import json,sys
            from pathlib import Path
            args=sys.argv[1:]
            def value(key): return args[args.index(key)+1]
            songs=Path(value('--songs-from')).read_text(encoding='utf-8').splitlines()
            rows=[{'path':p,'title':Path(p).stem,'status':'network_error','reason':'LRCLIB [DNS_FAILED]: provider cannot be resolved'} for p in songs]
            Path(value('--result')).write_text(json.dumps({'music_folder':args[0],'run_folder':'','error':'','songs':rows,'args':args}),encoding='utf-8')
            Path(value('--progress')).write_text(json.dumps({'total':len(songs),'completed':0,'notice':'DNS_FAILED','stages':[{'name':'Find lyrics','state':'running','total':len(songs),'done':1,'count':'1 of 2','current':Path(songs[0]).name,'note':''}]}),encoding='utf-8')
            print('DNS_FAILED: no timestamps were written',flush=True)
            sys.exit(4)
            """);
        var data = Path.Combine(_root, "plugin-data"); Directory.CreateDirectory(data);
        var music = Path.Combine(_root, "Telegram downloads %TEMP% & Persian فارسی"); Directory.CreateDirectory(music);
        var second = Path.Combine(_root, "Music"); Directory.CreateDirectory(second);
        var settings = Path.Combine(_root, "settings.json");
        File.WriteAllText(settings, JsonSerializer.Serialize(new { musicFolders = new[] { music, second } }));
        var paths = new[] { Path.Combine(music, "one.mp3"), Path.Combine(music, "two.mp3") };
        foreach (var p in paths) File.WriteAllText(p, "synthetic audio");
        var list = Path.Combine(data, "songs.txt"); File.WriteAllLines(list, paths);
        var result = Path.Combine(data, "result.json"); var progress = Path.Combine(data, "progress.json");
        var tool = new Tool(app);
        Check(tool.Installed && !tool.TooOld && tool.SupportsControl, "new backend version recognized, and it can be paused and stopped");
        Check(!tool.Ready, "missing Python/models never presented as ready");
        Check(tool.SetupText().Contains("irm https://"), "incomplete installation still offers the installer");
        foreach (var name in new[] { "firststart.py", "menu.py", "library.py", "repair.py", "drafts.py", "search.py", "guides.py" }) File.WriteAllText(Path.Combine(module,name),"# fixture");
        File.WriteAllText(Path.Combine(app,"requirements.txt"),"# fixture");
        Check(!tool.SetupText().Contains("irm https://") && tool.SetupText().Contains("Reusing the installed"), "complete local setup does not fetch GitHub's installer again");
        File.WriteAllText(Path.Combine(module, "__init__.py"), "__version__ = \"1.6.1\"\n");
        Check(tool.TooOld && tool.SetupText().Contains("irm https://"), "old backend with timestamp stripping must update before new editor/repair work");
        File.WriteAllText(Path.Combine(module, "__init__.py"), "__version__ = \"1.6.3\"\n");
        Check(tool.TooOld && !tool.SupportsControl && !tool.Ready, "a program without --control is too old for this plugin");
        File.WriteAllText(Path.Combine(module, "__init__.py"), "__version__ = \"1.6.4\"\n");
        File.Delete(Path.Combine(module,"guides.py"));
        Check(tool.SetupText().Contains("irm https://"), "missing line-guide component is not treated as complete offline setup");
        File.WriteAllText(Path.Combine(module,"guides.py"),"# fixture");
        var repairInfo = tool.RequestInfo(new[] { "repair-song", paths[0], "--lyrics-file", Path.Combine(data, "words %TEMP% & فارسی.txt") });
        Check(!repairInfo.Arguments.Contains(paths[0]), "song repair paths never enter cmd command text");
        var repairArgs = JsonSerializer.Deserialize<string[]>(repairInfo.Environment["WORDLYRICS_LAUNCH_REQUEST"]!);
        Check(repairArgs![1] == paths[0] && repairArgs[3].Contains("%TEMP% & فارسی"), "repair song/draft arguments survive literally in JSON");
        var oldLyrics = Path.ChangeExtension(paths[0], ".elrc");
        File.WriteAllText(oldLyrics, "[ar:Artist]\n[00:01.00]<00:01.00>alpha<00:02.00> beta\n");
        Check(LyricEditorWindow.ExistingWords(paths[0]) == "[00:01.00]alpha beta", "editor keeps line guides while removing old word timestamps");
        File.WriteAllText(oldLyrics, "[offset:500]\n[ar:Artist]\n[00:01.00]<00:01.00>alpha<00:02.00> beta\n");
        Check(LyricEditorWindow.ExistingWords(paths[0]).Contains("[offset:500]") && !LyricEditorWindow.ExistingWords(paths[0]).Contains("[ar:"), "editor preserves LRC offsets while discarding display metadata");
        Environment.SetEnvironmentVariable("WORDLYRICS_PYTHON", python);
        var controlFile = Path.Combine(data, "control %TEMP% & فارسی.txt");
        var process = tool.StartQuietRun(music, list, result, Path.Combine(data, "last run.log"), "half", "", true, progress, controlFile);
        Check(process.WaitForExit(15000), "synthetic backend finishes"); process.WaitForExit();
        Check(process.ExitCode == 4, "incomplete exit code preserved");
        using (var document = JsonDocument.Parse(File.ReadAllText(result)))
        {
            Check(document.RootElement.GetProperty("music_folder").GetString() == music, "shell characters and Unicode paths are passed literally");
            var given = document.RootElement.GetProperty("args").EnumerateArray().Select(a => a.GetString()).ToList();
            Check(given.IndexOf("--control") >= 0 && given[given.IndexOf("--control") + 1] == controlFile, "the control file is handed to the program, path unchanged");
        }
        Check(File.ReadAllText(Path.Combine(data, "last run.log")).Contains("DNS_FAILED"), "background error is visible in startup log");
        var updates = new List<ProgressState>();
        using var worker = new Worker(data, settings, new Options(false, "Half", true, app, ""), _ => { }, _ => { }, updates.Add);
        Check(worker.LibraryFolders().Count == 2, "both independent Noctis folders detected");
        typeof(Worker).GetField("_run", Hidden)!.SetValue(worker, process);
        typeof(Worker).GetMethod("ReadProgress", Hidden)!.Invoke(worker, null);
        Check(updates[^1].Message == "Find lyrics" && updates[^1].Fraction == .5, "live stage progress reaches plugin");
        var type = typeof(Worker).GetNestedType("Item", BindingFlags.NonPublic)!;
        var listType = typeof(List<>).MakeGenericType(type);
        var batch = (System.Collections.IList)Activator.CreateInstance(listType)!;
        foreach (var path in paths)
        {
            var item = Activator.CreateInstance(type)!;
            type.GetField("Path")!.SetValue(item, path); type.GetField("Root")!.SetValue(item, music);
            type.GetField("Asked")!.SetValue(item, true); type.GetField("Tries")!.SetValue(item, 1);
            batch.Add(item);
        }
        var waiting = Field<System.Collections.IDictionary>(worker, "_items");
        foreach (var item in batch) { type.GetField("NotBefore")!.SetValue(item, DateTime.UtcNow.AddHours(1)); waiting[type.GetField("Path")!.GetValue(item)!] = item; } // not for the worker's own timer to pick up
        int Count(object item, string name) => (int)type.GetField(name)!.GetValue(item)!;
        TimeSpan Delay(object item) => (DateTime)type.GetField("NotBefore")!.GetValue(item)! - DateTime.UtcNow;
        var finished = typeof(Worker).GetMethod("Finished", Hidden)!;
        finished.Invoke(worker, new object[] { process, batch });
        Check(updates[^1].Error && updates[^1].Message == "Retry scheduled", "an unreachable lyric provider is a retry, not a verdict");
        Check(updates[^1].Detail.Contains("DNS_FAILED"), "provider error reason reaches result window");
        Check(waiting.Count == 2 && batch.Cast<object>().All(i => Count(i, "Tries") == 1 && Count(i, "Waits") == 1), "network errors keep the songs on the list and leave their tries alone");
        Check(batch.Cast<object>().All(i => Delay(i) > TimeSpan.FromMinutes(9) && Delay(i) < TimeSpan.FromMinutes(11)), "the first wait after a network error is ten minutes");
        finished.Invoke(worker, new object[] { process, batch });
        Check(batch.Cast<object>().All(i => Count(i, "Tries") == 1 && Count(i, "Waits") == 2 && Delay(i) > TimeSpan.FromMinutes(29) && Delay(i) < TimeSpan.FromMinutes(31)), "the next wait is thirty minutes");
        Check(new[] { 0, 1, 2, 3, 9 }.Select(Worker.Backoff).SequenceEqual(new[] { TimeSpan.FromMinutes(10), TimeSpan.FromMinutes(30), TimeSpan.FromHours(1), TimeSpan.FromHours(3), TimeSpan.FromHours(3) }), "waits grow to every three hours and stay there");

        // The setup failure (exit code 70) is no verdict on the songs either. This run also lets the
        // priority be looked at while it is still going.
        File.WriteAllText(Path.Combine(module, "__main__.py"), "import sys,time\ntime.sleep(2)\nsys.exit(70)\n");
        File.Delete(result);
        var broken = tool.StartQuietRun(music, list, result, Path.Combine(data, "last run.log"), "half", "", false, progress);
        Check(broken.PriorityClass == System.Diagnostics.ProcessPriorityClass.BelowNormal, "the background run starts below normal priority");
        Check(broken.WaitForExit(15000), "failing backend finishes"); broken.WaitForExit();
        Check(broken.ExitCode == 70, "setup failure exit code preserved");
        foreach (var item in batch) type.GetField("Waits")!.SetValue(item, 0);
        finished.Invoke(worker, new object[] { broken, batch });
        Check(updates[^1].Message == "Setup is needed" && waiting.Count == 2 && batch.Cast<object>().All(i => Count(i, "Tries") == 1 && Count(i, "Waits") == 1 && Delay(i) > TimeSpan.FromMinutes(9)), "a setup failure keeps the songs, with their tries, for later");

        // A result that is not what the program writes must neither wedge the worker nor escape.
        File.WriteAllText(result, "{\"songs\":[\"not an object\"],\"left_out\":[7]}");
        typeof(Worker).GetField("_run", Hidden)!.SetValue(worker, process);
        finished.Invoke(worker, new object[] { process, batch });
        Check(updates[^1].Detail.Contains("could not be read") && Field<object?>(worker, "_run") is null, "an unreadable result is reported and the worker is free again");

        // A state file nobody can read must not fail the start of the plugin.
        foreach (var damaged in new[] { "[]", "not json at all", "{\"waiting\":[\"x\"]}", "{\"lastSeenUtc\":5,\"waiting\":[]}" })
        {
            var badData = Path.Combine(_root, "bad-state"); Directory.CreateDirectory(badData);
            File.Delete(Path.Combine(badData, "state.json.broken"));
            File.WriteAllText(Path.Combine(badData, "state.json"), damaged);
            var said = new List<string>();
            using var restarted = new Worker(badData, settings, new Options(false, "Half", true, app, ""), said.Add, _ => { }, _ => { });
            Check(File.ReadAllText(Path.Combine(badData, "state.json.broken")) == damaged && Field<System.Collections.IDictionary>(restarted, "_items").Count == 0, "an unreadable state file is kept as state.json.broken and the list starts empty: " + damaged);
            Check(said.Any(s => s.Contains("state.json.broken")), "... and the log says so");
        }

        // A folder name that cannot be used (here: far too long for Windows) is reported once, not at every tick.
        var notices = 0;
        var complaints = new List<ProgressState>();
        using (var misconfigured = new Worker(Path.Combine(_root, "bad-folder-data"), settings, new Options(false, "Half", true, new string('a', 40000), ""), _ => { }, _ => Interlocked.Increment(ref notices), complaints.Add))
        {
            var one = Activator.CreateInstance(type)!;
            type.GetField("Path")!.SetValue(one, paths[1]); type.GetField("Root")!.SetValue(one, music);
            type.GetField("Asked")!.SetValue(one, true); type.GetField("NotBefore")!.SetValue(one, DateTime.UtcNow);
            Field<System.Collections.IDictionary>(misconfigured, "_items")[paths[1]] = one;
            var work = typeof(Worker).GetMethod("Work", Hidden)!;
            for (var tick = 0; tick < 3; tick++) work.Invoke(misconfigured, null);
            Check(notices == 1 && complaints.Count(c => c.Message == "Setup is needed") == 1, "a bad WordLyrics folder setting is reported once");
            Check(Field<System.Collections.IDictionary>(misconfigured, "_items").Count == 1, "... and the waiting song is kept");
            misconfigured.Options = new Options(false, "Half", true, new string('b', 40000), "");
            work.Invoke(misconfigured, null);
            Check(notices == 2, "... and reported again after the setting changed (" + notices + " notices)");
        }

        // A folder moved or renamed into a library folder brings its songs along without one event for each.
        var library = Path.Combine(_root, "watched library"); Directory.CreateDirectory(library);
        var outside = Path.Combine(_root, "outside"); Directory.CreateDirectory(outside);
        var watchSettings = Path.Combine(_root, "watch-settings.json");
        File.WriteAllText(watchSettings, JsonSerializer.Serialize(new { musicFolders = new[] { library } }));
        using (var watching = new Worker(Path.Combine(_root, "watch-data"), watchSettings, new Options(true, "Half", true, app, ""), _ => { }, _ => { }, _ => { }))
        {
            var folders = Field<System.Collections.IDictionary>(watching, "_watchers");
            var queued = Field<System.Collections.IDictionary>(watching, "_items");
            var gate = Field<object>(watching, "_gate");
            string[] Queued() { lock (gate) return queued.Keys.Cast<string>().OrderBy(k => k, StringComparer.OrdinalIgnoreCase).ToArray(); }
            Check(Until(() => folders.Count == 1, 15000), "the library folder is watched");
            var album = Path.Combine(outside, "Album"); Directory.CreateDirectory(Path.Combine(album, "CD2"));
            foreach (var name in new[] { "a.mp3", "CD2\\b.flac", "cover.jpg", "done.mp3", "done.elrc" }) File.WriteAllText(Path.Combine(album, name), "synthetic audio");
            Directory.Move(album, Path.Combine(library, "Album"));
            var expected = new[] { Path.Combine(library, "Album", "a.mp3"), Path.Combine(library, "Album", "CD2", "b.flac") };
            Check(Until(() => Queued().Length >= 2, 10000), "songs inside a folder moved into the library are noticed");
            Thread.Sleep(500);
            Check(Queued().SequenceEqual(expected.OrderBy(k => k, StringComparer.OrdinalIgnoreCase)), "... only the songs that have no word-by-word lyrics yet");
            var draft = Path.Combine(library, "Unpacking"); Directory.CreateDirectory(draft);
            File.WriteAllText(Path.Combine(draft, "c.mp3"), "synthetic audio");
            Directory.Move(draft, Path.Combine(library, "Final name"));
            Check(Until(() => Queued().Contains(Path.Combine(library, "Final name", "c.mp3"), StringComparer.OrdinalIgnoreCase), 10000), "a folder given its final name after the songs arrived is noticed under the new name");
        }
    }

    // A program that behaves like the real one towards --control: it reports "paused" in its progress file
    // while the control file says pause, and ends the way Ctrl+C does (exit 130, its first song done, the
    // rest not reached) when it says stop. A file named ignore-stop next to its result makes it deaf to stop.
    private const string ReactingBackend = """
        import json,sys,time
        from pathlib import Path
        args=sys.argv[1:]
        def value(key): return args[args.index(key)+1]
        songs=Path(value('--songs-from')).read_text(encoding='utf-8').splitlines()
        control=Path(value('--control')); progress=Path(value('--progress')); result=Path(value('--result'))
        (result.parent/'seen-args.txt').write_text(' '.join(args),encoding='utf-8')
        ignore=(result.parent/'ignore-stop').exists()
        def word():
            try: return control.read_text(encoding='utf-8').strip()
            except OSError: return 'run'
        done=0
        end=time.time()+90
        while time.time()<end:
            w=word()
            if w=='stop' and not ignore: break
            paused=(w=='pause')
            if not paused: done=1
            progress.write_text(json.dumps({'total':len(songs),'completed':done,'paused':paused,'notice':'','stages':[{'name':'Time the words','state':'running','total':len(songs),'done':done,'count':str(done)+' of '+str(len(songs)),'current':Path(songs[0]).name,'note':''}]}),encoding='utf-8')
            time.sleep(0.1)
        rows=[{'path':songs[0],'title':Path(songs[0]).stem,'status':'timed','reason':''}]+[{'path':p,'title':Path(p).stem,'status':'not_reached','reason':'the run was stopped before this song'} for p in songs[1:]]
        result.write_text(json.dumps({'music_folder':args[0],'run_folder':'','error':'stopped','songs':rows}),encoding='utf-8')
        sys.exit(130)
        """;

    // The same kind of program from before --control: it only notes its arguments and finishes.
    private const string OldBackend = """
        import json,sys,time
        from pathlib import Path
        args=sys.argv[1:]
        def value(key): return args[args.index(key)+1]
        songs=Path(value('--songs-from')).read_text(encoding='utf-8').splitlines()
        result=Path(value('--result'))
        (result.parent/'seen-args.txt').write_text(' '.join(args),encoding='utf-8')
        time.sleep(1.5)
        result.write_text(json.dumps({'music_folder':args[0],'run_folder':'','error':'','songs':[{'path':p,'title':Path(p).stem,'status':'skipped','reason':''} for p in songs]}),encoding='utf-8')
        """;

    private static string FixtureProgram(string name, string version, string main)
    {
        var app = Path.Combine(_root, name);
        var module = Path.Combine(app, "wordlyrics"); Directory.CreateDirectory(module);
        File.WriteAllText(Path.Combine(module, "__init__.py"), $"__version__ = \"{version}\"\n");
        File.WriteAllText(Path.Combine(module, "runner.py"), "import runpy\nrunpy.run_module('wordlyrics',run_name='__main__')\n");
        File.WriteAllText(Path.Combine(module, "__main__.py"), main);
        return app;
    }

    /// <summary>Pause, Resume and Cancel as the worker handles them, against programs that read the control file.</summary>
    private static void Controls(string python)
    {
        Environment.SetEnvironmentVariable("WORDLYRICS_PYTHON", python);
        var app = FixtureProgram("control-tool", "1.6.4", ReactingBackend);
        var music = Path.Combine(_root, "control music"); Directory.CreateDirectory(music);
        var settings = Path.Combine(_root, "control-settings.json");
        File.WriteAllText(settings, JsonSerializer.Serialize(new { musicFolders = new[] { music } }));
        var songs = Enumerable.Range(1, 4).Select(i => Path.Combine(music, $"song {i}.mp3")).ToArray();
        foreach (var song in songs) File.WriteAllText(song, "synthetic audio");
        var options = new Options(false, "Full", true, app, "");
        var item = typeof(Worker).GetNestedType("Item", BindingFlags.NonPublic)!;
        var itemList = typeof(List<>).MakeGenericType(item);

        // Every song goes on the waiting list, "not yet" for the worker's own timer; the first two are handed over as one batch.
        (Worker Worker, string Data, List<ProgressState> Updates, List<string> Notices, System.Collections.IList Batch) Setup(string name, string[] paths, int inBatch)
        {
            var data = Path.Combine(_root, name); Directory.CreateDirectory(data);
            var updates = new List<ProgressState>(); var notices = new List<string>();
            var worker = new Worker(data, settings, options, _ => { }, n => { lock (notices) notices.Add(n); }, s => { lock (updates) updates.Add(s); });
            var waiting = Field<System.Collections.IDictionary>(worker, "_items");
            var batch = (System.Collections.IList)Activator.CreateInstance(itemList)!;
            foreach (var path in paths)
            {
                var one = Activator.CreateInstance(item)!;
                item.GetField("Path")!.SetValue(one, path); item.GetField("Root")!.SetValue(one, music);
                item.GetField("Asked")!.SetValue(one, true); item.GetField("NotBefore")!.SetValue(one, DateTime.UtcNow.AddHours(1));
                waiting[path] = one;
                if (batch.Count < inBatch) batch.Add(one);
            }
            return (worker, data, updates, notices, batch);
        }
        void Start(Worker worker, Tool tool, System.Collections.IList batch) => typeof(Worker).GetMethod("Start", Hidden)!.Invoke(worker, new object[] { tool, batch, options });

        // ── pause, resume, cancel ──
        var (worker, data, updates, notices, batch) = Setup("control-data", songs, 2);
        using (worker)
        {
            ProgressState Last() { lock (updates) return updates[^1]; }
            bool Seen(Func<ProgressState, bool> test) { lock (updates) return updates.Any(test); }
            var control = Path.Combine(data, "control.txt");
            Start(worker, new Tool(app), batch);
            Check(File.ReadAllText(control) == "run" && Last().Controllable && Last().Message == "Starting WordLyrics", "a run that can be controlled starts with a control file that says run, and shows the buttons");
            Check(Until(() => File.Exists(Path.Combine(data, "seen-args.txt")), 30000) && File.ReadAllText(Path.Combine(data, "seen-args.txt")).Contains("--control " + control), "the program was given the control file");
            Check(Until(() => Seen(s => s.Message == "Time the words" && !s.Paused && s.Controllable), 20000), "its progress reaches the window with the buttons shown");

            worker.SetPaused(true);
            Check(File.ReadAllText(control) == "pause" && Last().Paused && Last().Message == "Paused" && Last().Controllable, "Pause writes pause and the window says Paused at once");
            Check(Until(() => Last().Paused && Last().Detail.StartsWith("WordLyrics is paused"), 15000), "... and says so as soon as the program reports that it rests");
            Check(Field<System.Collections.IDictionary>(worker, "_items").Count == 4, "... the waiting list is untouched");
            worker.SetPaused(true);
            Check(File.ReadAllText(control) == "pause", "pausing twice changes nothing");

            worker.SetPaused(false);
            Check(File.ReadAllText(control) == "run" && !Last().Paused && Last().Message != "Paused" && Last().Controllable, "Resume writes run and the window works again");

            worker.Cancel();
            Check(File.ReadAllText(control) == "stop" && Last().Cancelling && Last().Message == "Stopping WordLyrics", "Cancel writes stop and the window says it is stopping");
            Check(Until(() => Field<object?>(worker, "_run") is null && Last().Finished, 40000), "the program stopped and the worker is free again");
            Check(Last().Message == "Cancelled" && !Last().Error && !Last().Controllable && !Last().Paused, "the window says Cancelled, without an error");
            Check(Field<System.Collections.IDictionary>(worker, "_items").Count == 0 && !File.ReadAllText(Path.Combine(data, "state.json")).Contains("\"path\""), "the song not reached and all the songs still waiting are off the list, and the list is saved that way");
            lock (notices)
                Check(notices.Count == 1 && notices[0].Contains("1 song finished") && notices[0].Contains("3 songs were taken off the waiting list")
                      && notices[0].Contains("right-click") && notices[0].Contains("WordLyrics program"), "one notice says what was kept and dropped, and how to start those songs again: " + string.Join(" | ", notices));
            Check(!File.Exists(control), "the control file is removed after the run");
            using (var document = JsonDocument.Parse(File.ReadAllText(Path.Combine(data, "result.json"))))
                Check(document.RootElement.GetProperty("songs")[0].GetProperty("status").GetString() == "timed", "the song that was finished keeps its result");
            worker.Cancel(); worker.SetPaused(false);
            Check(Last().Message == "Cancelled", "Cancel with nothing running does nothing");
        }

        // ── a program that does not react to Cancel is stopped by force after a while ──
        var (deaf, deafData, deafUpdates, _, deafBatch) = Setup("control-deaf", songs, 2);
        using (deaf)
        {
            File.WriteAllText(Path.Combine(deafData, "ignore-stop"), "");
            deaf.CancelGrace = TimeSpan.FromSeconds(2);
            Start(deaf, new Tool(app), deafBatch);
            Check(Until(() => File.Exists(Path.Combine(deafData, "seen-args.txt")), 30000), "the deaf program is running");
            deaf.Cancel();
            Check(Until(() => { lock (deafUpdates) return Field<object?>(deaf, "_run") is null && deafUpdates[^1].Message == "Cancelled"; }, 40000), "a program that ignores Cancel is stopped after the grace time");
            Check(Field<System.Collections.IDictionary>(deaf, "_items").Count == 0, "... and everything it was working on is off the list");
        }

        // ── Pause with nothing running holds the waiting songs; Resume lets them go ──
        var (held, _, heldUpdates, _, _) = Setup("control-held", songs, 0);
        using (held)
        {
            ProgressState Last() { lock (heldUpdates) return heldUpdates[^1]; }
            var one = Field<System.Collections.IDictionary>(held, "_items")[songs[0]]!;
            item.GetField("NotBefore")!.SetValue(one, DateTime.UtcNow.AddSeconds(-1));
            var work = typeof(Worker).GetMethod("Work", Hidden)!;
            held.SetPaused(true);
            Check(Last().Message == "Paused" && Last().Paused && !Last().Controllable, "pausing with nothing running says Paused and offers Resume only");
            for (var tick = 0; tick < 3; tick++) work.Invoke(held, null);
            lock (heldUpdates) Check(heldUpdates.Count == 1 && Field<System.Collections.IDictionary>(held, "_items").Count == 4, "while paused no batch is looked at, let alone started");
            held.SetPaused(false);
            Check(Last().Message == "Resumed", "Resume says so");
            Check(Until(() => { work.Invoke(held, null); lock (heldUpdates) return heldUpdates.Any(s => s.Message == "Setup is needed"); }, 15000), "after Resume the waiting song is picked up again (here: found that nothing is set up)");
        }

        // ── a program from before --control: no control file, no buttons, Cancel does nothing ──
        var old = FixtureProgram("control-old-tool", "1.6.3", OldBackend);
        var (aged, agedData, agedUpdates, _, agedBatch) = Setup("control-old-data", songs, 2);
        using (aged)
        {
            var oldTool = new Tool(old);
            Check(oldTool.TooOld && !oldTool.SupportsControl, "the old program does not support control");
            Start(aged, oldTool, agedBatch);
            aged.Cancel();
            Check(!File.Exists(Path.Combine(agedData, "control.txt")) && !Field<bool>(aged, "_cancelling"), "no control file is made, and Cancel does nothing");
            lock (agedUpdates) Check(agedUpdates.All(s => !s.Controllable && !s.Cancelling), "the window is not offered the buttons");
            Check(Until(() => File.Exists(Path.Combine(agedData, "seen-args.txt")), 30000) && !File.ReadAllText(Path.Combine(agedData, "seen-args.txt")).Contains("--control"), "--control is not given to a program that does not know it");
            Check(Until(() => Field<object?>(aged, "_run") is null, 30000), "the old program's run still ends normally");
        }
    }

    private static bool Until(Func<bool> condition, int milliseconds)
    {
        for (var end = Environment.TickCount64 + milliseconds; Environment.TickCount64 < end; Thread.Sleep(50))
            if (condition()) return true;
        return condition();
    }

    private static void Windows(string? pictures)
    {
        string? acceptedWords = null;
        var editor = new LyricEditorWindow("Example song - custom lyric editor", "", text => acceptedWords = text);
        editor.Show(); Dispatcher.UIThread.RunJobs();
        Field<Button>(editor, "_use").RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        Check(acceptedWords is null && Field<TextBlock>(editor, "_status").Text!.Contains("full lyrics"), "empty custom lyrics cannot start timing");
        Field<TextBox>(editor, "_words").Text = "alpha beta\nCorrect this line\nفارسی";
        if (pictures is not null) {
            Directory.CreateDirectory(pictures); Dispatcher.UIThread.RunJobs();
#pragma warning disable CS0618
            editor.CaptureRenderedFrame()?.Save(Path.Combine(pictures, "wordlyrics-custom-editor.png"));
#pragma warning restore CS0618
        }
        Field<Button>(editor, "_use").RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        Check(acceptedWords == "alpha beta\nCorrect this line\nفارسی", "custom editor preserves corrected multiline and Unicode words");
        Check(!Field<Button>(editor, "_use").IsEnabled, "saved draft cannot accidentally launch duplicate repairs");
        editor.Close();
        var window = new ProgressWindow(); window.Show();
        window.Update(new ProgressState("Time the words", "Example song.mp3\nListening to the vocals", 2, 5, .4));
        Check(Field<ProgressBar>(window, "_bar").Value == .4, "progress bar uses measured stage fraction");
        Check(Field<TextBlock>(window, "_counts").Text == "2 of 5 songs checked", "song count visible");
        if (pictures is not null)
        {
            Directory.CreateDirectory(pictures); Dispatcher.UIThread.RunJobs(); window.UpdateLayout(); AvaloniaHeadlessPlatform.ForceRenderTimerTick();
            using var image = window.CaptureRenderedFrame();
#pragma warning disable CS0618 // Keep preview saving compatible with the oldest supported Avalonia.
            image?.Save(Path.Combine(pictures, "wordlyrics-progress.png"));
#pragma warning restore CS0618
        }
        window.Update(new ProgressState("No new word timestamps were written", "LRCLIB [DNS_FAILED]: connection failed", 5, 5, Finished: true, Error: true));
        Check(!Field<ProgressBar>(window, "_bar").IsIndeterminate, "error completion stops spinner");
        Check(!Field<Button>(window, "_report").IsEnabled, "missing report never presented as openable");
        var savedRun=Path.Combine(_root,"saved job");Directory.CreateDirectory(savedRun);File.WriteAllText(Path.Combine(savedRun,"request.json"),"{}");
        window.Update(new ProgressState("Needs action", "PERMISSION_DENIED: original Windows user needs access", Finished:true,Error:true,Report:Path.Combine(savedRun,"report.html")));
        Check(Field<Button>(window,"_resume").IsVisible,"saved job exposes Fix/resume even before report exists");
        window.Close();

        // Pause and Cancel: shown only while a run that can be controlled is going.
        var pauseCalls = new List<bool>(); var cancelCalls = 0;
        var controls = new ProgressWindow(pause: paused => pauseCalls.Add(paused), cancel: () => cancelCalls++); controls.Show();
        var pause = Field<Button>(controls, "_pause"); var cancel = Field<Button>(controls, "_cancel"); var heading = Field<TextBlock>(controls, "_message");
        void Press(Button button) { button.RaiseEvent(new RoutedEventArgs(Button.ClickEvent)); Dispatcher.UIThread.RunJobs(); }
        Check(!pause.IsVisible && !cancel.IsVisible, "no Pause or Cancel button while nothing runs");
        controls.Update(new ProgressState("Time the words", "Example song.mp3", 1, 5, .2, Controllable: true));
        Check(pause.IsVisible && cancel.IsVisible && (string?)pause.Content == "Pause" && (string?)cancel.Content == "Cancel", "a run that can be controlled shows Pause and Cancel");
        Press(pause);
        Check(pauseCalls.SequenceEqual(new[] { true }), "Pause asks the worker to pause");
        controls.Update(new ProgressState("Paused", "WordLyrics is paused. Press Resume to continue.", 1, 5, .2, Controllable: true, Paused: true));
        Check(heading.Text == "Paused" && (string?)pause.Content == "Resume" && cancel.IsVisible, "the window says Paused and the button reads Resume");
        Press(pause);
        Check(pauseCalls.SequenceEqual(new[] { true, false }), "Resume asks the worker to go on");
        controls.Update(new ProgressState("Stopping WordLyrics", "...", 1, 5, .2, Controllable: true, Cancelling: true));
        Check(!cancel.IsEnabled && (string?)cancel.Content == "Cancelling..." && !pause.IsEnabled, "while it stops, neither button can be pressed again");
        controls.Update(new ProgressState("Time the words", "", 1, 5, .2, Controllable: true));
        Press(cancel);
        Check(cancelCalls == 1 && !cancel.IsEnabled, "Cancel asks the worker once and cannot be pressed twice");
        controls.Update(new ProgressState("Cancelled", "", 1, 5, Finished: true));
        Check(!pause.IsVisible && !cancel.IsVisible, "no buttons once the run is over");
        controls.Update(new ProgressState("2 songs received word timestamps", "", 2, 2, Finished: true, Paused: true));
        Check(pause.IsVisible && (string?)pause.Content == "Resume" && !cancel.IsVisible, "paused with nothing running: only Resume is offered");
        controls.Close();
        var host = new Host(Path.Combine(_root, "ui", "plugin-data", "dev.moshi.wordlyrics"));
        var plugin = new WordLyricsPlugin(); plugin.Initialize(host);
        Check(host.Commands == 3 && plugin.Info.Version == "1.2.4", "plugin registers timing, repair and custom editor without launching work");
        typeof(WordLyricsPlugin).GetMethod("OnProgress", Hidden)!.Invoke(plugin, new object[] { new ProgressState("Starting WordLyrics", Total: 1) }); Dispatcher.UIThread.RunJobs();
        var automatic = Field<ProgressWindow?>(plugin, "_progress");
        Check(automatic is { IsVisible: true, ShowActivated: false }, "the progress window that opens by itself does not take the keyboard");
        automatic!.Close(); Dispatcher.UIThread.RunJobs();
        host.Raise("progress"); Dispatcher.UIThread.RunJobs();
        var panel = Field<ProgressWindow>(plugin, "_progress");
        Check(panel.IsVisible && panel.ShowActivated, "progress can be opened from settings, and then it is brought forward");
        var publish = typeof(WordLyricsPlugin).GetMethod("OnProgress", Hidden)!;
        publish.Invoke(plugin, new object[] { new ProgressState("Starting WordLyrics", Total: 5) }); Dispatcher.UIThread.RunJobs();
        panel.Close();
        publish.Invoke(plugin, new object[] { new ProgressState("Time the words", Total: 5) }); Dispatcher.UIThread.RunJobs();
        Check(Field<ProgressWindow?>(plugin, "_progress") is null, "hiding progress does not reopen on the next heartbeat");
        Check(Field<Worker>(plugin, "_worker") is not null, "hiding progress keeps worker alive");

        // The buttons of the window the plugin opens are wired to its worker.
        publish.Invoke(plugin, new object[] { new ProgressState("Time the words", Total: 5, Controllable: true) }); Dispatcher.UIThread.RunJobs();
        host.Raise("progress"); Dispatcher.UIThread.RunJobs();
        var wired = Field<ProgressWindow>(plugin, "_progress");
        var worker = Field<Worker>(plugin, "_worker");
        Check((string?)Field<Button>(wired, "_pause").Content == "Pause" && Field<Button>(wired, "_cancel").IsVisible, "the window the plugin opens shows both buttons for a run that can be controlled");
        Field<Button>(wired, "_pause").RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        Check(Until(() => Field<bool>(worker, "_paused"), 5000), "the Pause button reaches the worker");
        Until(() => { Dispatcher.UIThread.RunJobs(); return Field<TextBlock>(wired, "_message").Text == "Paused"; }, 5000);
        Check(Field<TextBlock>(wired, "_message").Text == "Paused" && (string?)Field<Button>(wired, "_pause").Content == "Resume", "... and the window shows Paused with a Resume button");
        Field<Button>(wired, "_pause").RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        Check(Until(() => !Field<bool>(worker, "_paused"), 5000), "the Resume button reaches the worker");
        wired.Close(); Dispatcher.UIThread.RunJobs();
        plugin.Shutdown(); Check(host.Commands == 0, "shutdown unregisters commands");
        publish.Invoke(plugin, new object[] { new ProgressState("late update") }); Dispatcher.UIThread.RunJobs();
        Check(Field<ProgressWindow?>(plugin, "_progress") is null, "late updates cannot reopen after disable");
    }

    private sealed class Host(string directory) : IPluginHost, IPluginSettings
    {
        public int Commands;
        public string AppVersion => "1.5.9";
        public string DataDirectory => directory;
        public INowPlaying NowPlaying => throw new NotSupportedException();
        public IBeatSource Beat => throw new NotSupportedException();
        public ISpectrumSource Spectrum => throw new NotSupportedException();
        public IPluginSettings Settings => this;
        public void Log(string message) { }
        public void Notify(string message) { }
        public void RegisterVisualLayer(IVisualLayerProvider provider) { }
        public IDisposable RegisterTrackCommand(string label, string? icon, Action<TrackInfo> handler) { Commands++; return new Removal(() => Commands--); }
        public string? GetString(string key) => null;
        public bool GetBool(string key, bool fallback = false) => fallback;
        public double GetNumber(string key, double fallback = 0) => fallback;
        public event EventHandler<string>? Changed;
        public void Raise(string key) => Changed?.Invoke(this, key);
    }
    private sealed class Removal(Action action) : IDisposable { public void Dispose() => action(); }
    private sealed class TestApp : Application { public override void Initialize() { Styles.Add(new FluentTheme()); RequestedThemeVariant = Avalonia.Styling.ThemeVariant.Dark; } }
}
