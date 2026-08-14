using System.Text;

namespace PenguinConvexity.Desktop;

public sealed class DesktopLogger
{
    private readonly string _path;
    private readonly object _sync = new();

    public DesktopLogger(string path)
    {
        _path = path;
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
    }

    public void Info(string eventName, string detail) => Write("INFO", eventName, detail);
    public void Warning(string eventName, string detail) => Write("WARN", eventName, detail);
    public void Error(string eventName, string detail) => Write("ERROR", eventName, detail);

    private void Write(string level, string eventName, string detail)
    {
        var safe = detail.Replace('\r', ' ').Replace('\n', ' ').Trim();
        var line = $"{DateTimeOffset.Now:O}\t{level}\t{eventName}\t{safe}{Environment.NewLine}";
        lock (_sync)
        {
            File.AppendAllText(_path, line, new UTF8Encoding(false));
        }
    }
}
