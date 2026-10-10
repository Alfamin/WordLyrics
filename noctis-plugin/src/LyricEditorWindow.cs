using Avalonia;
using Avalonia.Controls;
using Avalonia.Layout;
using Avalonia.Media;
using System.Text.RegularExpressions;

namespace WordLyricsForNoctis;

/// <summary>Editable private words for one song; timing and backup approval remain in WordLyrics.</summary>
internal sealed class LyricEditorWindow : Window
{
    private readonly TextBox _words;
    private readonly TextBlock _status;
    private readonly Button _use;

    public LyricEditorWindow(string title, string initial, Action<string> use)
    {
        Title = "WordLyrics - edit lyrics"; Width = 680; Height = 630; MinWidth = 480; MinHeight = 400;
        Background = new SolidColorBrush(Color.Parse("#111827"));
        _words = new TextBox { Text = initial, AcceptsReturn = true, TextWrapping = TextWrapping.Wrap, MaxLength = 500_000, Margin = new Thickness(0, 14, 0, 0), PlaceholderText = "Paste or edit complete song lyrics here", VerticalContentAlignment = VerticalAlignment.Top };
        var content = new DockPanel { Margin = new Thickness(22), LastChildFill = true };
        var heading = new StackPanel { Spacing = 8 };
        heading.Children.Add(new TextBlock { Text = title, FontSize = 22, FontWeight = FontWeight.Bold, Foreground = Brushes.White, TextWrapping = TextWrapping.Wrap });
        heading.Children.Add(new TextBlock { Text = "Correct a line, or paste complete plain or synced LRC lyrics. Keep the [mm:ss] line guides when available; WordLyrics recalculates word timing from audio. Audio and tags stay unchanged.", Foreground = Brushes.LightGray, TextWrapping = TextWrapping.Wrap });
        heading.Children.Add(new TextBlock { Text = initial.Length == 0 ? "No readable LRC/text sidecar found. Paste the full lyrics below, or use Search / fix a song to import embedded words or choose Genius / LRCLIB." : "Current LRC/text words loaded. Check that this is the version you want; preferred TTML/Lyricsfile content is not imported here.", Foreground = Brushes.LightGray, TextWrapping = TextWrapping.Wrap });
        DockPanel.SetDock(heading, Dock.Top); content.Children.Add(heading);
        var footer = new StackPanel { Spacing = 8, Margin = new Thickness(0, 12, 0, 0) };
        _status = new TextBlock { Foreground = Brushes.LightGray, TextWrapping = TextWrapping.Wrap };
        _use = new Button { Content = "Use these lyrics - review timing run", HorizontalAlignment = HorizontalAlignment.Stretch, Padding = new Thickness(16, 10) };
        _use.Click += (_, _) =>
        {
            if (string.IsNullOrWhiteSpace(_words.Text)) { _status.Text = "Paste the full lyrics first."; return; }
            try { use(_words.Text); _status.Text = "Private draft saved. Confirm START RERUN in the WordLyrics window."; _use.IsEnabled = false; }
            catch (InvalidOperationException) { _status.Text = "WORDLYRICS_SETUP_NEEDED: install or update WordLyrics first. Your edited text stays here; copy it before closing."; }
            catch (UnauthorizedAccessException) { _status.Text = "DRAFT_PERMISSION_DENIED: the private draft could not be saved. Copy your words to a text file, then choose it in Search / fix a song."; }
            catch (Exception ex) { _status.Text = "LYRIC_DRAFT_FAILED (" + ex.GetType().Name + "). Your text is still here; copy it before closing."; }
        };
        footer.Children.Add(_use); footer.Children.Add(_status);
        DockPanel.SetDock(footer, Dock.Bottom); content.Children.Add(footer);
        content.Children.Add(_words); Content = content;
    }

    internal static string ExistingWords(string path)
    {
        foreach (var extension in new[] { ".elrc", ".lrc", ".txt" })
        {
            var file = Path.ChangeExtension(path, extension);
            try
            {
                var info = new FileInfo(file);
                if (!info.Exists || info.Length > 2_000_000 || (info.Attributes & FileAttributes.ReparsePoint) != 0) continue;
                var text = File.ReadAllText(file);
                text = Regex.Replace(text, @"^\s*\[(?!offset:)[a-zA-Z]{2,}:[^\]]*\]\s*$", "", RegexOptions.Multiline | RegexOptions.IgnoreCase);
                text = Regex.Replace(text, @"<\d+:\d+(?:[.:]\d+)?>", "");
                return text.Trim();
            }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
        }
        return "";
    }
}
