using System.Reflection;
using System.Text.RegularExpressions;

namespace WordLyricsForNoctis;

/// <summary>Uses Noctis's optional native reader; older app versions keep the worker fallback.</summary>
internal static class EmbeddedWordLyrics
{
    private static readonly Regex WordStamp = new(@"<\d{1,3}:\d{2}(?:[.:]\d{1,3})?>\s*\S", RegexOptions.CultureInvariant,
        TimeSpan.FromMilliseconds(50));
    public static bool HasWords(string path, Func<string,string?>? read = null)
    {
        try
        {
            var text = (read ?? Native)(path);
            return text is { Length: > 2_000_000 } || (text != null && WordStamp.IsMatch(text));
        }
        catch { return false; } // the Python worker still performs its full supported-format check
    }
    private static string? Native(string path)
    {
        var type = AppDomain.CurrentDomain.GetAssemblies().FirstOrDefault(a=>a.GetName().Name=="Noctis.Core")?
            .GetType("Noctis.Services.EmbeddedLyrics");
        var method = type?.GetMethod("ReadFile",BindingFlags.Public|BindingFlags.Static,null,new[]{typeof(string)},null);
        var result = method?.Invoke(null,new object[]{path});
        return result?.GetType().GetProperty("TimedText")?.GetValue(result) as string;
    }
}
