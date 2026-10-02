// Test host for the WordLyrics plugin: stands in for Noctis (no window, no player) so the
// plugin's own work can be checked: noticing new songs, starting the program, reading its
// result, the track menu command, the list that survives a restart.
// Nothing here ships in the plugin. Everything it touches is named in WL_* variables.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using System.Threading;
using Avalonia.Controls;
using Avalonia.Threading;
using Noctis.Plugins;

internal static class WlHost
{
    private static readonly Stopwatch Clock = Stopwatch.StartNew();
    private static StreamWriter? _out;
    public static readonly List<string> Notices = new();
    public static readonly List<string> Logs = new();

    private static string Env(string name) => Environment.GetEnvironmentVariable(name) ?? "";

    public static void Say(string kind, string text)
    {
        lock (Notices)
        {
            _out?.WriteLine($"{Clock.Elapsed.TotalSeconds,6:0.0}  {kind,-7} {text}");
            if (kind == "NOTICE") Notices.Add(text);
            if (kind == "log") Logs.Add(text);
        }
    }

    [STAThread]
    public static int Main(string[] args)
    {
        _out = new StreamWriter(Env("WL_OUT"), false, new UTF8Encoding(false)) { AutoFlush = true };
        try { return Run(); }
        catch (Exception ex) { Say("HOST", "EXCEPTION " + ex); return 1; }
        finally { _out.Dispose(); }
    }

    private static FakeHost _host = null!;
    private static Dispatcher _ui = null!;

    /// <summary>Lets the "UI thread" run for a while, or until the condition holds.</summary>
    private static bool Wait(double seconds, Func<bool>? until = null)
    {
        var end = Clock.Elapsed.TotalSeconds + seconds;
        while (Clock.Elapsed.TotalSeconds < end)
        {
            _ui.RunJobs();
            if (until is not null && until()) return true;
            Thread.Sleep(50);
        }
        _ui.RunJobs();
        return until is null;
    }

    private static int Count(string part)
    {
        lock (Notices) return Notices.Count(n => n.Contains(part, StringComparison.OrdinalIgnoreCase));
    }

    private static bool Logged(string part)
    {
        lock (Notices) return Logs.Any(n => n.Contains(part, StringComparison.OrdinalIgnoreCase));
    }

    private static TrackInfo Track(string path) => new(
        "id", Path.GetFileNameWithoutExtension(path), "artist", "album", "artist", TimeSpan.FromMinutes(3), 2020, "", 1, path, false, 0, 0);

    /// <summary>The way the download plugin saves: under a temporary name, bit by bit, then renamed.</summary>
    private static void Download(string source, string folder)
    {
        Directory.CreateDirectory(folder);
        var final = Path.Combine(folder, Path.GetFileName(source));
        var part = Path.ChangeExtension(final, ".part");
        var bytes = File.ReadAllBytes(source);
        using (var output = new FileStream(part, FileMode.Create, FileAccess.Write, FileShare.None))
        {
            for (var at = 0; at < bytes.Length; at += bytes.Length / 6 + 1)
            {
                output.Write(bytes, at, Math.Min(bytes.Length / 6 + 1, bytes.Length - at));
                output.Flush();
                Wait(0.5);
            }
        }
        File.Move(part, final);
        Say("HOST", "downloaded " + Path.GetFileName(final));
    }

    private static void Copy(string source, string folder, bool withLrc)
    {
        Directory.CreateDirectory(folder);
        var lrc = Path.ChangeExtension(source, ".lrc");
        if (withLrc && File.Exists(lrc)) File.Copy(lrc, Path.Combine(folder, Path.GetFileName(lrc)));
        File.Copy(source, Path.Combine(folder, Path.GetFileName(source)));
        Say("HOST", "copied " + Path.GetFileName(source));
    }

    private static string State() => File.Exists(Path.Combine(_host.DataDirectory, "state.json"))
        ? string.Join(" ", File.ReadAllLines(Path.Combine(_host.DataDirectory, "state.json")).Select(l => l.Trim()))
        : "(no state file)";

    private static int Run()
    {
        _ui = Dispatcher.UIThread; // this thread is the "UI thread" from here on
        string data = Env("WL_DATA"), music = Env("WL_MUSIC"), scenario = Env("WL_SCENARIO");
        var sources = Env("WL_SONGS").Split('|', StringSplitOptions.RemoveEmptyEntries);
        Directory.CreateDirectory(data);
        Directory.CreateDirectory(music);
        File.WriteAllText(Path.Combine(data, "settings.json"),
            "{\"musicFolders\": [" + System.Text.Json.JsonSerializer.Serialize(music) + "], \"watchFoldersEnabled\": true}");

        _host = new FakeHost(Path.Combine(data, "plugin-data", "dev.moshi.wordlyrics"));
        _host.Values["folder"] = Env("WL_TOOL");
        _host.Values["speed"] = "Full";
        _host.Values["backup"] = Env("WL_BACKUP");
        var assembly = Assembly.LoadFrom(Env("WL_PLUGIN"));
        var plugin = (INoctisPlugin)Activator.CreateInstance(assembly.GetType("WordLyricsForNoctis.WordLyricsPlugin", true)!)!;
        Say("HOST", $"scenario {scenario}: plugin {plugin.Info.Name} {plugin.Info.Version}");
        plugin.Initialize(_host);
        Wait(4);
        var ok = true;

        void Expect(bool condition, string what)
        {
            Say(condition ? "PASS" : "FAIL", what);
            ok &= condition;
        }

        switch (scenario)
        {
            case "new-songs":
                // 1: a download (no lyrics next to it), 2: a copied song with its .lrc, into a subfolder
                Download(sources[0], Path.Combine(music, "Noctis Free Music"));
                Wait(2);
                Copy(sources[1], Path.Combine(music, "Copied album"), withLrc: true);
                Expect(Wait(15, () => State().Contains(Path.GetFileName(sources[0]))), "the download is on the waiting list");
                Expect(!Logged("started WordLyrics"), "nothing is started while the files are still fresh");
                Expect(Wait(240, () => Logs.Count(l => l.Contains("finished")) >= 1 && !State().Contains("\"path\"")), "both songs were handled");
                Wait(2);
                foreach (var song in new[] { Path.Combine(music, "Noctis Free Music", Path.GetFileName(sources[0])), Path.Combine(music, "Copied album", Path.GetFileName(sources[1])) })
                    Expect(File.Exists(Path.ChangeExtension(song, ".elrc")), "word-by-word lyrics next to " + Path.GetFileName(song));
                Expect(Count("ready") + Count("got word-by-word") >= 1, "a notice said so");

                // 3: the track menu, on a song that was already there and on one that is done
                var old = Path.Combine(music, Path.GetFileName(sources[2]));
                _host.Command!(Track(old));
                Wait(1);
                Expect(Count("takes a moment") == 1, "the track command answers at once");
                Expect(Wait(120, () => File.Exists(Path.ChangeExtension(old, ".elrc"))), "the asked-for song got its lyrics");
                Wait(3);
                _host.Command!(Track(old));
                Wait(1);
                Expect(Count("already has word-by-word") == 1, "asking again says it is done already");
                _host.Command!(Track(Path.Combine(music, "not there.mp3")));
                Wait(1);
                Expect(Count("not there") == 1, "a missing file is said to be missing");

                // 4: switched off, a new song is left alone
                _host.Values["auto"] = "false";
                _host.Raise("auto");
                Wait(1);
                Copy(sources[3], Path.Combine(music, "Copied album"), withLrc: true);
                Wait(35);
                Expect(!File.Exists(Path.ChangeExtension(Path.Combine(music, "Copied album", Path.GetFileName(sources[3])), ".elrc"))
                       && !State().Contains("\"path\""), "with the switch off a new song is left alone");
                break;

            case "restart":
                // a song arrived while "Noctis" was closed; this start must find it, and a shutdown
                // in the middle of the run must stop the program and keep the song on the list
                Expect(Wait(20, () => Logged("while Noctis was closed")), "the song that arrived meanwhile was found");
                Expect(Wait(60, () => Logged("started WordLyrics")), "the program was started for it");
                Wait(9);
                plugin.Shutdown();
                Say("HOST", "shut down in the middle of the run");
                Wait(2);
                var left = Process.GetProcessesByName("python").Length;
                Expect(left == 0, "the program was stopped with it (python processes left: " + left + ")");
                Expect(State().Contains("\"path\""), "songs are still on the waiting list");
                Say("HOST", "state: " + State());
                return ok ? 0 : 1;

            case "switches":
                // the two switches that open a window; whoever runs the test looks at the windows' processes
                _host.Values["library"] = "true";
                _host.Raise("library");
                Wait(10);
                _host.Values["setup"] = "true";
                _host.Raise("setup");
                Wait(40);
                Expect(File.Exists(Path.Combine(_host.DataDirectory, "setup.cmd")), "the setup lines were written");
                break;

            case "busy":
                // WordLyrics is already working in another window (a whole-library run, say)
                Copy(sources[0], music, withLrc: true);
                Expect(Wait(90, () => Logged("another window")), "the plugin sees that WordLyrics is busy and waits");
                Expect(Wait(420, () => Logged("finished") && !State().Contains("\"path\"")), "... and does the song once the other run is over");
                Expect(File.Exists(Path.ChangeExtension(Path.Combine(music, Path.GetFileName(sources[0])), ".elrc")), "word-by-word lyrics are there");
                break;

            case "crash":
                // "Noctis" dies without saying goodbye while the program is working
                Expect(Wait(60, () => Logged("started WordLyrics")), "the program was started");
                Wait(9);
                Say("HOST", "killing myself in the middle of the run");
                _out!.Flush();
                Process.GetCurrentProcess().Kill();
                return 9;

            case "resume":
                Expect(Wait(180, () => Logged("finished") && !State().Contains("\"path\"")), "the waiting songs were done after the restart");
                Wait(2);
                Expect(Count("ready") + Count("got word-by-word") >= 1, "a notice said so");
                break;

            case "not-installed":
                Copy(sources[0], music, withLrc: true);
                var say = Env("WL_EXPECT").Length > 0 ? Env("WL_EXPECT") : "not installed";
                Expect(Wait(60, () => Count(say) == 1), "a notice says: " + say);
                Wait(12);
                Expect(Count(say) == 1, "... once, not again on every look");
                Expect(State().Contains("\"path\""), "the song stays on the waiting list");
                break;
        }
        Say("HOST", "state: " + State());
        plugin.Shutdown();
        Wait(1);
        return ok ? 0 : 1;
    }
}

internal sealed class FakeHost : IPluginHost
{
    public FakeHost(string data) => DataDirectory = data;

    public Dictionary<string, string> Values { get; } = new();
    public Action<TrackInfo>? Command;
    private readonly FakeSettings _settings = new();

    public void Raise(string key) => _settings.Raise(key);

    public string AppVersion => "1.5.8";
    public string DataDirectory { get; }
    public INowPlaying NowPlaying => throw new NotSupportedException();
    public IBeatSource Beat => throw new NotSupportedException();
    public ISpectrumSource Spectrum => throw new NotSupportedException();
    public void Log(string message) => WlHost.Say("log", message);
    public void RegisterVisualLayer(IVisualLayerProvider provider) => throw new NotSupportedException();
    public string PluginDirectory => throw new NotSupportedException();
    public IPluginSettings Settings { get { _settings.Values = Values; return _settings; } }
    public void Notify(string message) => WlHost.Say("NOTICE", message);

    public IDisposable RegisterTrackCommand(string label, string? icon, Action<TrackInfo> handler)
    {
        Command = handler;
        WlHost.Say("HOST", "track command registered: " + label);
        return new Nothing();
    }

    private sealed class Nothing : IDisposable { public void Dispose() { } }

    private sealed class FakeSettings : IPluginSettings
    {
        public Dictionary<string, string> Values = new();
        public string? GetString(string key) => Values.TryGetValue(key, out var v) ? v : null;
        public bool GetBool(string key, bool fallback = false) => Values.TryGetValue(key, out var v) ? v == "true" : fallback;
        public double GetNumber(string key, double fallback = 0) => fallback;
        public event EventHandler<string>? Changed;
        public void Raise(string key) => Changed?.Invoke(this, key);
    }
}
