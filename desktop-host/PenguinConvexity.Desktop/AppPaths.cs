namespace PenguinConvexity.Desktop;

public sealed record AppPaths(string ProjectRoot)
{
    public const string ProductUrl = "http://127.0.0.1:8766/desktop/index.html";
    public const string HealthUrl = "http://127.0.0.1:8766/api/health";

    public string RuntimeRoot => Path.Combine(ProjectRoot, "runtime");
    public string LogRoot => Path.Combine(RuntimeRoot, "logs");
    public string DesktopLogPath => Path.Combine(LogRoot, "c2.3-desktop.log");
    public string ServiceStdoutPath => Path.Combine(LogRoot, "c2.3-service.stdout.log");
    public string ServiceStderrPath => Path.Combine(LogRoot, "c2.3-service.stderr.log");
    public string WebViewUserDataPath => Path.Combine(RuntimeRoot, "webview2", "user-data");
    public string WindowStatePath => Path.Combine(RuntimeRoot, "window-state-c2.3.json");
    public string LegacyWindowStatePath => Path.Combine(RuntimeRoot, "window-state.json");
    public string ServerScriptPath => Path.Combine(ProjectRoot, "scripts", "serve_local.py");

    public static AppPaths Discover()
    {
        foreach (var start in new[] { AppContext.BaseDirectory, Environment.CurrentDirectory })
        {
            var current = new DirectoryInfo(Path.GetFullPath(start));
            for (var depth = 0; current is not null && depth < 10; depth++, current = current.Parent)
            {
                if (File.Exists(Path.Combine(current.FullName, "scripts", "serve_local.py")) &&
                    File.Exists(Path.Combine(current.FullName, "desktop", "index.html")))
                {
                    return new AppPaths(current.FullName);
                }
            }
        }
        throw new DirectoryNotFoundException("project root markers were not found");
    }
}
