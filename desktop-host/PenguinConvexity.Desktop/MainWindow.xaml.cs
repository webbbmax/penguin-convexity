using System.ComponentModel;
using System.Diagnostics;
using System.Net.Http;
using System.Windows;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.Wpf;

namespace PenguinConvexity.Desktop;

public partial class MainWindow : System.Windows.Window
{
    private readonly AppPaths _paths;
    private readonly DesktopLogger _logger;
    private readonly ServiceHealthProbe _healthProbe;
    private readonly LocalServiceManager _serviceManager;
    private readonly WindowStateStore _windowStateStore;
    private readonly Stopwatch _startupWatch = Stopwatch.StartNew();
    private readonly System.Windows.Threading.DispatcherTimer _waitTimer;
    private WebView2? _webView;
    private UserFaultKind _faultKind;
    private bool _processRecoveryAttempted;
    private bool _closing;

    public MainWindow(AppPaths paths, DesktopLogger logger)
    {
        InitializeComponent();
        _paths = paths;
        _logger = logger;
        _healthProbe = new ServiceHealthProbe(new HttpClient { Timeout = TimeSpan.FromSeconds(3) });
        _serviceManager = new LocalServiceManager(paths, logger, _healthProbe);
        _windowStateStore = new WindowStateStore(paths.WindowStatePath, paths.LegacyWindowStatePath);
        _windowStateStore.ApplyTo(this);

        _waitTimer = new System.Windows.Threading.DispatcherTimer { Interval = TimeSpan.FromSeconds(1) };
        _waitTimer.Tick += (_, _) =>
        {
            if (_startupWatch.Elapsed >= TimeSpan.FromSeconds(5) && StatusLayer.Visibility == Visibility.Visible)
            {
                WaitMessage.Text = $"已等待 {Math.Max(1, (int)_startupWatch.Elapsed.TotalSeconds)} 秒，后台扫描仍在继续。";
                WaitMessage.Visibility = Visibility.Visible;
            }
        };
        _waitTimer.Start();
        _logger.Info("window_shown", "startup view visible");
    }

    public async Task InitializeProductAsync()
    {
        SetStage("检查本地服务", "正在确认本机页面服务是否可以使用。");
        try
        {
            var health = await _healthProbe.ProbeAsync(CancellationToken.None);
            if (health.State == HealthState.Healthy)
            {
                _serviceManager.MarkExternalService();
                _logger.Info("service_reused", "healthy identity matched; ownership=external");
            }
            else if (health.State == HealthState.IdentityMismatch)
            {
                ShowFault(UserFaultKind.IdentityFailure, health.Detail);
                return;
            }
            else if (health.State == HealthState.PortOccupied)
            {
                ShowFault(UserFaultKind.PortConflict, health.Detail);
                return;
            }
            else
            {
                SetStage("启动本地服务", "正在启动本机页面服务，后台扫描不会被重启。");
                var startResult = await _serviceManager.StartAndWaitAsync(TimeSpan.FromSeconds(28));
                if (startResult.State != HealthState.Healthy)
                {
                    ShowFault(
                        startResult.State == HealthState.IdentityMismatch
                            ? UserFaultKind.IdentityFailure
                            : startResult.State == HealthState.PortOccupied
                                ? UserFaultKind.PortConflict
                                : UserFaultKind.LocalServiceFailure,
                        startResult.Detail);
                    return;
                }
            }

            SetStage("加载产品页面", "正在打开现有机会中心和更新中心。");
            await CreateWebViewAsync();
        }
        catch (WebView2UnavailableException error)
        {
            ShowFault(UserFaultKind.WebViewUnavailable, error.Message);
        }
        catch (Exception error)
        {
            _logger.Error("startup_failure", error.Message);
            ShowFault(UserFaultKind.LocalServiceFailure, error.Message);
        }
    }

    public void ActivateExistingWindow()
    {
        Dispatcher.Invoke(() =>
        {
            if (WindowState == WindowState.Minimized)
            {
                WindowState = WindowState.Normal;
            }
            Show();
            Activate();
            Topmost = true;
            Topmost = false;
            Focus();
            _logger.Info("window_activated", "secondary launch activated primary window");
        });
    }

    private async Task CreateWebViewAsync()
    {
        string version;
        try
        {
            version = CoreWebView2Environment.GetAvailableBrowserVersionString();
        }
        catch (Exception error)
        {
            throw new WebView2UnavailableException("没有检测到可用的软件显示组件。", error);
        }

        if (_paths.WebViewUserDataPath.StartsWith("\\\\", StringComparison.Ordinal))
        {
            throw new WebView2UnavailableException("软件显示组件的数据目录不能位于网络路径。", null);
        }

        Directory.CreateDirectory(_paths.WebViewUserDataPath);
        _logger.Info("webview_runtime", $"version={version}; userData={_paths.WebViewUserDataPath}");

        if (_webView is not null)
        {
            BrowserHost.Children.Remove(_webView);
            _webView.Dispose();
        }

        _webView = new WebView2();
        BrowserHost.Children.Add(_webView);
        var environment = await CoreWebView2Environment.CreateAsync(null, _paths.WebViewUserDataPath);
        await _webView.EnsureCoreWebView2Async(environment);
        ConfigureWebView(_webView.CoreWebView2);
        _webView.CoreWebView2.Navigate(AppPaths.ProductUrl);
    }

    private void ConfigureWebView(CoreWebView2 core)
    {
        core.Settings.AreDevToolsEnabled = false;
        core.Settings.IsStatusBarEnabled = false;
        core.Settings.AreBrowserAcceleratorKeysEnabled = true;
        core.NavigationStarting += Core_NavigationStarting;
        core.FrameNavigationStarting += Core_FrameNavigationStarting;
        core.NavigationCompleted += Core_NavigationCompleted;
        core.NewWindowRequested += Core_NewWindowRequested;
        core.DownloadStarting += (_, args) =>
        {
            args.Cancel = true;
            _logger.Warning("download_blocked", "product container has no download channel");
        };
        core.ProcessFailed += Core_ProcessFailed;
    }

    private void Core_NavigationStarting(object? sender, CoreWebView2NavigationStartingEventArgs args)
    {
        HandleNavigation(args, "navigation_blocked");
    }

    private void Core_FrameNavigationStarting(object? sender, CoreWebView2NavigationStartingEventArgs args)
    {
        HandleNavigation(args, "frame_navigation_blocked");
    }

    private void HandleNavigation(CoreWebView2NavigationStartingEventArgs args, string blockedEvent)
    {
        var disposition = NavigationPolicy.Decide(args.Uri, args.IsUserInitiated);
        if (disposition == NavigationDisposition.AllowLocal)
        {
            return;
        }

        args.Cancel = true;
        if (disposition == NavigationDisposition.OpenExternal)
        {
            ExternalLinkLauncher.Open(args.Uri, _logger);
            return;
        }
        _logger.Warning(blockedEvent, NavigationPolicy.SafeOrigin(args.Uri));
    }

    private void Core_NewWindowRequested(object? sender, CoreWebView2NewWindowRequestedEventArgs args)
    {
        args.Handled = true;
        if (NavigationPolicy.Decide(args.Uri, args.IsUserInitiated) == NavigationDisposition.OpenExternal)
        {
            ExternalLinkLauncher.Open(args.Uri, _logger);
            return;
        }
        _logger.Warning("new_window_blocked", NavigationPolicy.SafeOrigin(args.Uri));
    }

    private void Core_NavigationCompleted(object? sender, CoreWebView2NavigationCompletedEventArgs args)
    {
        if (!args.IsSuccess)
        {
            ShowFault(UserFaultKind.PageFailure, $"页面加载状态：{args.WebErrorStatus}");
            return;
        }

        BrowserHost.Visibility = Visibility.Visible;
        StatusLayer.Visibility = Visibility.Collapsed;
        _waitTimer.Stop();
        _logger.Info("product_ready", $"elapsedMs={_startupWatch.ElapsedMilliseconds}");
    }

    private async void Core_ProcessFailed(object? sender, CoreWebView2ProcessFailedEventArgs args)
    {
        _logger.Warning("webview_process_failed", $"kind={args.ProcessFailedKind}");
        if (!_processRecoveryAttempted)
        {
            _processRecoveryAttempted = true;
            SetStage("加载产品页面", "产品页面正在自动恢复，后台扫描仍在继续。");
            try
            {
                await CreateWebViewAsync();
                return;
            }
            catch (Exception error)
            {
                _logger.Error("page_recovery_failed", error.Message);
            }
        }
        ShowFault(UserFaultKind.PageFailure, $"显示进程状态：{args.ProcessFailedKind}");
    }

    private void SetStage(string title, string message)
    {
        StatusLayer.Visibility = Visibility.Visible;
        BrowserHost.Visibility = Visibility.Collapsed;
        StatusTitle.Text = title;
        StatusMessage.Text = message;
        ActionPanel.Visibility = Visibility.Collapsed;
        TechnicalExpander.Visibility = Visibility.Collapsed;
        _logger.Info("startup_stage", title);
    }

    private void ShowFault(UserFaultKind kind, string detail)
    {
        _faultKind = kind;
        var copy = UserFaultCopy.For(kind);
        StatusLayer.Visibility = Visibility.Visible;
        BrowserHost.Visibility = Visibility.Collapsed;
        StatusTitle.Text = copy.Title;
        StatusMessage.Text = copy.Message + " 后台扫描仍在继续，本次只恢复软件页面。";
        PrimaryAction.Content = copy.PrimaryAction;
        LogAction.Content = kind == UserFaultKind.WebViewUnavailable ? "重新检测" : "打开日志位置";
        ActionPanel.Visibility = Visibility.Visible;
        TechnicalDetail.Text = $"发生位置：{copy.Stage}\n记录：{UserFaultCopy.SanitizeDetail(detail)}";
        TechnicalExpander.Visibility = Visibility.Visible;
        _logger.Error("user_fault", $"kind={kind}; {UserFaultCopy.SanitizeDetail(detail)}");
    }

    private async void PrimaryAction_Click(object sender, RoutedEventArgs e)
    {
        if (_faultKind == UserFaultKind.WebViewUnavailable)
        {
            ExternalLinkLauncher.Open("https://developer.microsoft.com/microsoft-edge/webview2/", _logger);
            return;
        }

        if (_faultKind == UserFaultKind.PageFailure)
        {
            SetStage("加载产品页面", "正在重新加载产品页面，后台扫描不会被重启。");
            try
            {
                await CreateWebViewAsync();
            }
            catch (Exception error)
            {
                ShowFault(UserFaultKind.PageFailure, error.Message);
            }
            return;
        }

        await InitializeProductAsync();
    }

    private async void LogAction_Click(object sender, RoutedEventArgs e)
    {
        if (_faultKind == UserFaultKind.WebViewUnavailable)
        {
            await InitializeProductAsync();
            return;
        }
        ExternalLinkLauncher.OpenFolder(_paths.LogRoot, _logger);
    }

    protected override void OnClosing(CancelEventArgs e)
    {
        if (!_closing)
        {
            _closing = true;
            _windowStateStore.Save(this);
            _serviceManager.StopOwnedService();
            _logger.Info("window_closing", "desktop container closing; background jobs untouched");
        }
        base.OnClosing(e);
    }
}

internal sealed class WebView2UnavailableException : Exception
{
    public WebView2UnavailableException(string message, Exception? inner) : base(message, inner) { }
}
