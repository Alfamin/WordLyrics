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
            AppBuilder.Configure<TestApp>().UseSkia().UseHarfBuzz().UseHeadless(new AvaloniaHeadlessPlatformOptions { UseHeadlessDrawing = false }).SetupWithoutStarting();
            Backend(args[0]);
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
        File.WriteAllText(Path.Combine(module, "__init__.py"), "__version__ = \"1.6.1\"\n");
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
        Check(tool.Installed && !tool.TooOld, "new backend version recognized");
        Check(!tool.Ready, "missing Python/models never presented as ready");
        Check(tool.SetupText().Contains("irm https://"), "incomplete installation still offers the installer");
        foreach (var name in new[] { "firststart.py", "menu.py", "library.py", "repair.py", "drafts.py", "search.py", "guides.py" }) File.WriteAllText(Path.Combine(module,name),"# fixture");
        File.WriteAllText(Path.Combine(app,"requirements.txt"),"# fixture");
        Check(!tool.SetupText().Contains("irm https://") && tool.SetupText().Contains("Reusing the installed"), "complete local setup does not fetch GitHub's installer again");
        File.WriteAllText(Path.Combine(module, "__init__.py"), "__version__ = \"1.6.0\"\n");
        Check(tool.TooOld && tool.SetupText().Contains("irm https://"), "old backend with timestamp stripping must update before new editor/repair work");
        File.WriteAllText(Path.Combine(module, "__init__.py"), "__version__ = \"1.6.1\"\n");
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
        var process = tool.StartQuietRun(music, list, result, Path.Combine(data, "last run.log"), "half", "", true, progress);
        Check(process.WaitForExit(15000), "synthetic backend finishes"); process.WaitForExit();
        Check(process.ExitCode == 4, "incomplete exit code preserved");
        using (var document = JsonDocument.Parse(File.ReadAllText(result)))
            Check(document.RootElement.GetProperty("music_folder").GetString() == music, "shell characters and Unicode paths are passed literally");
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
        typeof(Worker).GetMethod("Finished", Hidden)!.Invoke(worker, new object[] { process, batch });
        Check(updates[^1].Error && updates[^1].Message == "No new word timestamps were written", "finished process is not false success");
        Check(updates[^1].Detail.Contains("DNS_FAILED"), "provider error reason reaches result window");
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
        var host = new Host(Path.Combine(_root, "ui", "plugin-data", "dev.moshi.wordlyrics"));
        var plugin = new WordLyricsPlugin(); plugin.Initialize(host);
        Check(host.Commands == 3 && plugin.Info.Version == "1.2.1", "plugin registers timing, repair and custom editor without launching work");
        host.Raise("progress"); Dispatcher.UIThread.RunJobs();
        var panel = Field<ProgressWindow>(plugin, "_progress");
        Check(panel.IsVisible, "progress can be opened from settings");
        var publish = typeof(WordLyricsPlugin).GetMethod("OnProgress", Hidden)!;
        publish.Invoke(plugin, new object[] { new ProgressState("Starting WordLyrics", Total: 5) }); Dispatcher.UIThread.RunJobs();
        panel.Close();
        publish.Invoke(plugin, new object[] { new ProgressState("Time the words", Total: 5) }); Dispatcher.UIThread.RunJobs();
        Check(Field<ProgressWindow?>(plugin, "_progress") is null, "hiding progress does not reopen on the next heartbeat");
        Check(Field<Worker>(plugin, "_worker") is not null, "hiding progress keeps worker alive");
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
