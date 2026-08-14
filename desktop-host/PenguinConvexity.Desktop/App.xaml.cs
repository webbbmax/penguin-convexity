using System.Windows;

namespace PenguinConvexity.Desktop;

public partial class App : System.Windows.Application
{
    private SingleInstanceCoordinator? _singleInstance;
    private DesktopLogger? _logger;

    protected override async void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);
        ShutdownMode = ShutdownMode.OnMainWindowClose;

        AppPaths paths;
        try
        {
            paths = AppPaths.Discover();
            _logger = new DesktopLogger(paths.DesktopLogPath);
        }
        catch (Exception error)
        {
            System.Windows.MessageBox.Show(
                "没有找到企鹅投研项目文件，软件无法启动。请从项目内的正式入口打开。",
                "企鹅投研-凸性",
                System.Windows.MessageBoxButton.OK,
                System.Windows.MessageBoxImage.Error);
            System.Diagnostics.Debug.WriteLine(error.Message);
            Shutdown(1);
            return;
        }

        _singleInstance = new SingleInstanceCoordinator(_logger);
        if (!_singleInstance.TryAcquirePrimary())
        {
            var activated = await _singleInstance.SignalActivationAsync(TimeSpan.FromSeconds(1.7));
            if (!activated)
            {
                System.Windows.MessageBox.Show(
                    "企鹅投研已经打开，但当前窗口暂时无法响应。请稍后再试。",
                    "企鹅投研-凸性",
                    System.Windows.MessageBoxButton.OK,
                    System.Windows.MessageBoxImage.Information);
            }
            Shutdown(activated ? 0 : 2);
            return;
        }

        var window = new MainWindow(paths, _logger);
        MainWindow = window;
        _singleInstance.StartListening(() => window.ActivateExistingWindow());
        window.Show();
        await window.InitializeProductAsync();
    }

    protected override void OnExit(ExitEventArgs e)
    {
        _singleInstance?.Dispose();
        _logger?.Info("app_exit", $"exitCode={e.ApplicationExitCode}");
        base.OnExit(e);
    }
}
