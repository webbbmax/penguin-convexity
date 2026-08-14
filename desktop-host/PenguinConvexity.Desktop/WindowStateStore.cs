using System.Text.Json;
using System.Text.Json.Serialization;
using System.Windows;

namespace PenguinConvexity.Desktop;

public sealed record ScreenArea(double Left, double Top, double Width, double Height, bool Primary)
{
    public double Right => Left + Width;
    public double Bottom => Top + Height;
}

public sealed class DesktopWindowState
{
    [JsonPropertyName("version")] public int Version { get; set; } = 1;
    [JsonPropertyName("left")] public double Left { get; set; }
    [JsonPropertyName("top")] public double Top { get; set; }
    [JsonPropertyName("width")] public double Width { get; set; }
    [JsonPropertyName("height")] public double Height { get; set; }
    [JsonPropertyName("maximized")] public bool Maximized { get; set; }
    [JsonPropertyName("savedAt")] public DateTimeOffset SavedAt { get; set; }
}

public sealed class WindowStateStore
{
    private readonly string _path;
    private readonly string _legacyPath;

    public WindowStateStore(string path, string legacyPath)
    {
        _path = path;
        _legacyPath = legacyPath;
    }

    public void ApplyTo(System.Windows.Window window)
    {
        var state = Load();
        if (state is null)
        {
            window.WindowState = System.Windows.WindowState.Maximized;
            return;
        }
        var normalized = Normalize(state, CurrentScreens());
        window.WindowStartupLocation = System.Windows.WindowStartupLocation.Manual;
        window.Left = normalized.Left;
        window.Top = normalized.Top;
        window.Width = normalized.Width;
        window.Height = normalized.Height;
        window.WindowState = normalized.Maximized ? System.Windows.WindowState.Maximized : System.Windows.WindowState.Normal;
    }

    public DesktopWindowState? Load()
    {
        foreach (var candidate in new[] { _path, _legacyPath })
        {
            if (!File.Exists(candidate)) continue;
            try
            {
                var state = JsonSerializer.Deserialize<DesktopWindowState>(File.ReadAllText(candidate));
                if (state is not null && state.Width >= 480 && state.Height >= 360) return state;
            }
            catch (JsonException) { }
            catch (IOException) { }
        }
        return null;
    }

    public void Save(System.Windows.Window window)
    {
        var bounds = window.WindowState == System.Windows.WindowState.Normal
            ? new System.Windows.Rect(window.Left, window.Top, window.Width, window.Height)
            : window.RestoreBounds;
        if (bounds.Width < 480 || bounds.Height < 360) return;

        var state = new DesktopWindowState
        {
            Left = bounds.Left,
            Top = bounds.Top,
            Width = bounds.Width,
            Height = bounds.Height,
            Maximized = window.WindowState == System.Windows.WindowState.Maximized,
            SavedAt = DateTimeOffset.Now
        };
        Directory.CreateDirectory(Path.GetDirectoryName(_path)!);
        var temp = _path + ".tmp";
        File.WriteAllText(temp, JsonSerializer.Serialize(state, new JsonSerializerOptions { WriteIndented = true }));
        File.Move(temp, _path, true);
    }

    public static DesktopWindowState Normalize(DesktopWindowState state, IReadOnlyList<ScreenArea> screens)
    {
        if (screens.Count == 0) return state;
        var width = Math.Max(480, state.Width);
        var height = Math.Max(360, state.Height);
        var best = screens
            .Select(screen => new { Screen = screen, Area = IntersectionArea(state.Left, state.Top, width, height, screen) })
            .OrderByDescending(item => item.Area)
            .First();
        var screen = best.Area >= 12800 ? best.Screen : screens.FirstOrDefault(item => item.Primary) ?? screens[0];
        width = Math.Min(width, screen.Width);
        height = Math.Min(height, screen.Height);
        var left = best.Area >= 12800
            ? Math.Clamp(state.Left, screen.Left, screen.Right - width)
            : screen.Left + (screen.Width - width) / 2;
        var top = best.Area >= 12800
            ? Math.Clamp(state.Top, screen.Top, screen.Bottom - height)
            : screen.Top + (screen.Height - height) / 2;
        return new DesktopWindowState
        {
            Left = left,
            Top = top,
            Width = width,
            Height = height,
            Maximized = state.Maximized,
            SavedAt = state.SavedAt
        };
    }

    private static double IntersectionArea(double left, double top, double width, double height, ScreenArea screen)
    {
        var intersectionWidth = Math.Max(0, Math.Min(left + width, screen.Right) - Math.Max(left, screen.Left));
        var intersectionHeight = Math.Max(0, Math.Min(top + height, screen.Bottom) - Math.Max(top, screen.Top));
        return intersectionWidth * intersectionHeight;
    }

    private static IReadOnlyList<ScreenArea> CurrentScreens() =>
        System.Windows.Forms.Screen.AllScreens
            .Select(screen => new ScreenArea(
                screen.WorkingArea.Left,
                screen.WorkingArea.Top,
                screen.WorkingArea.Width,
                screen.WorkingArea.Height,
                screen.Primary))
            .ToArray();
}
