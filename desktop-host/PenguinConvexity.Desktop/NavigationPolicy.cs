using System.Diagnostics;

namespace PenguinConvexity.Desktop;

public enum NavigationDisposition
{
    AllowLocal,
    OpenExternal,
    Block
}

public static class NavigationPolicy
{
    public static bool IsAllowedLocalUri(string? value) =>
        Uri.TryCreate(value, UriKind.Absolute, out var uri) &&
        uri.Scheme == Uri.UriSchemeHttp &&
        uri.Host == "127.0.0.1" &&
        uri.Port == 8766;

    public static bool IsExternalHttpUri(string? value) =>
        Uri.TryCreate(value, UriKind.Absolute, out var uri) &&
        (uri.Scheme == Uri.UriSchemeHttp || uri.Scheme == Uri.UriSchemeHttps) &&
        !IsAllowedLocalUri(value);

    public static NavigationDisposition Decide(string? value, bool isUserInitiated)
    {
        if (IsAllowedLocalUri(value)) return NavigationDisposition.AllowLocal;
        if (isUserInitiated && IsExternalHttpUri(value)) return NavigationDisposition.OpenExternal;
        return NavigationDisposition.Block;
    }

    public static string SafeOrigin(string? value)
    {
        if (!Uri.TryCreate(value, UriKind.Absolute, out var uri)) return "invalid-uri";
        return $"{uri.Scheme}://{uri.Host}:{uri.Port}";
    }
}

public static class ExternalLinkLauncher
{
    public static void Open(string uri, DesktopLogger logger)
    {
        if (!NavigationPolicy.IsExternalHttpUri(uri)) return;
        try
        {
            Process.Start(new ProcessStartInfo(uri) { UseShellExecute = true });
            logger.Info("external_link_opened", NavigationPolicy.SafeOrigin(uri));
        }
        catch (Exception error)
        {
            logger.Warning("external_link_failed", error.Message);
        }
    }

    public static void OpenFolder(string path, DesktopLogger logger)
    {
        try
        {
            Directory.CreateDirectory(path);
            Process.Start(new ProcessStartInfo("explorer.exe", $"\"{path}\"") { UseShellExecute = true });
        }
        catch (Exception error)
        {
            logger.Warning("log_folder_open_failed", error.Message);
        }
    }
}
