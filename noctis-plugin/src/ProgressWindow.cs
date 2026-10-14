using System.Diagnostics;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Layout;
using Avalonia.Media;

namespace WordLyricsForNoctis;

/// <param name="Controllable">A run that can be paused and cancelled is going: the buttons are shown.</param>
/// <param name="Paused">The user paused; the button then reads Resume.</param>
/// <param name="Cancelling">The user cancelled and the program is finishing the song it is on.</param>
internal sealed record ProgressState(string Message, string Detail = "", int Completed = 0, int Total = 0,
    double? Fraction = null, bool Finished = false, bool Error = false, string Report = "",
    bool Controllable = false, bool Paused = false, bool Cancelling = false);

/// <summary>An ordinary plugin-owned window; no unsupported Noctis page/queue modifications.</summary>
internal sealed class ProgressWindow : Window
{
    private readonly TextBlock _message = new() { FontSize = 21, FontWeight = FontWeight.SemiBold, TextWrapping = TextWrapping.Wrap };
    private readonly TextBlock _detail = new() { TextWrapping = TextWrapping.Wrap };
    private readonly TextBlock _counts = new() { FontSize = 14 };
    private readonly ProgressBar _bar = new() { Minimum = 0, Maximum = 1, Height = 12 };
    private readonly Button _report = new() { Content = "Open result report", IsEnabled = false };
    private readonly Button _setup = new() { Content = "Set up WordLyrics", IsVisible = false };
    private readonly Button _resume = new() { Content = "Fix problem / resume affected songs", IsVisible = false };
    private readonly Button _pause = new() { Content = "Pause", IsVisible = false };
    private readonly Button _cancel = new() { Content = "Cancel", IsVisible = false };
    private ProgressState _state = new("Waiting for new music", "Add a song to a Noctis library folder. To process existing songs, use Generate timestamps in the plugin settings.");

    /// <param name="pause">Called with true to pause and false to resume.</param>
    /// <param name="cancel">Called when the user cancels the run that is going.</param>
    public ProgressWindow(Action? setup = null, Action? generate = null, Action<string>? resume = null, Action<bool>? pause = null, Action? cancel = null)
    {
        Title = "WordLyrics progress";
        Width = 590; MinWidth = 470; Height = 420; MinHeight = 320;
        WindowStartupLocation = WindowStartupLocation.CenterOwner;
        var root = new StackPanel { Spacing = 16, Margin = new Thickness(24) };
        root.Children.Add(new TextBlock { Text = "WORDLYRICS", Foreground = Brushes.MediumTurquoise, FontWeight = FontWeight.Bold });
        root.Children.Add(_message); root.Children.Add(_bar); root.Children.Add(_counts); root.Children.Add(_detail);
        var actions = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 10 };
        _pause.Click += (_, _) => pause?.Invoke(!_state.Paused);
        _cancel.Click += (_, _) => { _cancel.IsEnabled = false; cancel?.Invoke(); };
        actions.Children.Add(_pause);
        actions.Children.Add(_cancel);
        actions.Children.Add(_report);
        _setup.IsEnabled = setup is not null;
        _setup.Click += (_, _) => setup?.Invoke();
        actions.Children.Add(_setup);
        _resume.Click += (_, _) => resume?.Invoke(Path.GetDirectoryName(_state.Report) ?? "");
        var close = new Button { Content = "Hide (keep working)" };
        close.Click += (_, _) => Close();
        actions.Children.Add(close);
        root.Children.Add(actions);
        root.Children.Add(_resume);
        if (generate is not null)
        {
            var library = new Button { Content = "Generate timestamps / choose folders" };
            library.Click += (_, _) => generate();
            root.Children.Add(library);
        }
        root.Children.Add(new TextBlock { Text = "Closing this window keeps the job running. Pause rests the models until you press Resume; Cancel stops the job and takes the songs that were not reached off the waiting list. Disabling WordLyrics stops its background jobs safely.", TextWrapping = TextWrapping.Wrap, FontSize = 12 });
        Content = new ScrollViewer { Content = root };
        _report.Click += (_, _) =>
        {
            try { if (File.Exists(_state.Report) && Path.GetExtension(_state.Report).Equals(".html", StringComparison.OrdinalIgnoreCase)) Process.Start(new ProcessStartInfo(_state.Report) { UseShellExecute = true }); }
            catch (Exception ex) { _detail.Text = "REPORT_OPEN_FAILED: " + ex.GetType().Name + ". Open the report from WordLyrics > Advanced > Reports."; }
        };
        Update(_state);
    }

    public void Update(ProgressState state)
    {
        _state = state;
        _message.Text = state.Message;
        _message.Foreground = state.Error ? Brushes.IndianRed : state.Paused ? Brushes.Orange : Brushes.MediumTurquoise;
        _detail.Text = state.Detail;
        _counts.Text = state.Total > 0 ? $"{state.Completed} of {state.Total} songs checked" : "Waiting for the next update";
        _bar.IsIndeterminate = !state.Finished && state.Fraction is null;
        _bar.Value = state.Finished ? 1 : Math.Clamp(state.Fraction ?? 0, 0, 1);
        _report.IsEnabled = state.Finished && File.Exists(state.Report);
        _setup.IsVisible = state.Message is "Setup is needed" or "WordLyrics could not start";
        _pause.IsVisible = state.Paused || (state.Controllable && !state.Finished);
        _pause.Content = state.Paused ? "Resume" : "Pause";
        _pause.IsEnabled = !state.Cancelling;
        _cancel.IsVisible = state.Controllable && !state.Finished;
        _cancel.Content = state.Cancelling ? "Cancelling..." : "Cancel";
        _cancel.IsEnabled = !state.Cancelling;
        _resume.IsVisible = state.Finished && state.Error && File.Exists(Path.Combine(Path.GetDirectoryName(state.Report) ?? "", "request.json"));
    }
}
