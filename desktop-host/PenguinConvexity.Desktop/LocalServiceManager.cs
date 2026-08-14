using System.ComponentModel;
using System.Diagnostics;
using System.IO;

namespace PenguinConvexity.Desktop;

public sealed class LocalServiceManager
{
    private readonly AppPaths _paths;
    private readonly DesktopLogger _logger;
    private readonly ServiceHealthProbe _probe;
    private Process? _ownedProcess;
    private DateTime _ownedStartTimeUtc;

    public LocalServiceManager(AppPaths paths, DesktopLogger logger, ServiceHealthProbe probe)
    {
        _paths = paths;
        _logger = logger;
        _probe = probe;
    }

    public void MarkExternalService() => _logger.Info("service_ownership", "external");

    public async Task<HealthProbeResult> StartAndWaitAsync(TimeSpan timeout)
    {
        Directory.CreateDirectory(_paths.LogRoot);
        _ownedProcess = StartPython("python.exe", false) ?? StartPython("py.exe", true);
        if (_ownedProcess is null)
        {
            return new HealthProbeResult(HealthState.PortAvailable, "本机没有找到可用的Python运行环境");
        }

        _ownedStartTimeUtc = _ownedProcess.StartTime.ToUniversalTime();
        _logger.Info("service_started", $"ownership=container; pid={_ownedProcess.Id}; script={_paths.ServerScriptPath}");
        _ = DrainAsync(_ownedProcess.StandardOutput, _paths.ServiceStdoutPath);
        _ = DrainAsync(_ownedProcess.StandardError, _paths.ServiceStderrPath);

        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline)
        {
            if (_ownedProcess.HasExited)
            {
                var postExit = await _probe.ProbeAsync(CancellationToken.None);
                if (postExit.State is HealthState.PortOccupied or HealthState.IdentityMismatch)
                {
                    return postExit;
                }
                return new HealthProbeResult(HealthState.PortAvailable, $"本地服务提前结束，退出代码 {_ownedProcess.ExitCode}");
            }
            var result = await _probe.ProbeAsync(CancellationToken.None);
            if (result.State == HealthState.Healthy || result.State == HealthState.IdentityMismatch)
            {
                return result;
            }
            await Task.Delay(400);
        }
        return new HealthProbeResult(HealthState.PortAvailable, "本地服务在限定时间内没有完成启动");
    }

    private Process? StartPython(string executable, bool useLauncher)
    {
        var info = new ProcessStartInfo
        {
            FileName = executable,
            WorkingDirectory = _paths.ProjectRoot,
            UseShellExecute = false,
            CreateNoWindow = true,
            WindowStyle = ProcessWindowStyle.Hidden,
            RedirectStandardOutput = true,
            RedirectStandardError = true
        };
        if (useLauncher) info.ArgumentList.Add("-3");
        info.ArgumentList.Add(_paths.ServerScriptPath);
        info.ArgumentList.Add("--port");
        info.ArgumentList.Add("8766");
        try
        {
            return Process.Start(info);
        }
        catch (Win32Exception error)
        {
            _logger.Warning("python_not_found", $"launcher={executable}; {error.Message}");
            return null;
        }
    }

    private static async Task DrainAsync(StreamReader reader, string path)
    {
        while (await reader.ReadLineAsync() is { } line)
        {
            await File.AppendAllTextAsync(path, line + Environment.NewLine);
        }
    }

    public void StopOwnedService()
    {
        if (_ownedProcess is null)
        {
            return;
        }
        try
        {
            if (_ownedProcess.HasExited || _ownedProcess.StartTime.ToUniversalTime() != _ownedStartTimeUtc)
            {
                return;
            }
            _ownedProcess.CloseMainWindow();
            if (!_ownedProcess.WaitForExit(900))
            {
                _ownedProcess.Kill(entireProcessTree: false);
                _ownedProcess.WaitForExit(1000);
            }
            _logger.Info("owned_service_stopped", $"pid={_ownedProcess.Id}");
        }
        catch (Exception error)
        {
            _logger.Warning("owned_service_stop_failed", error.Message);
        }
        finally
        {
            _ownedProcess.Dispose();
            _ownedProcess = null;
        }
    }
}
