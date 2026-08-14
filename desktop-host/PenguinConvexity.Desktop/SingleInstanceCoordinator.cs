using System.Diagnostics;
using System.IO.Pipes;

namespace PenguinConvexity.Desktop;

public sealed class SingleInstanceCoordinator : IDisposable
{
    private const string MutexName = "Local\\PenguinResearchConvexityDesktop.C2_3";
    private readonly string _pipeName = $"PenguinResearchConvexityDesktop.C2_3.{Process.GetCurrentProcess().SessionId}";
    private readonly DesktopLogger _logger;
    private readonly CancellationTokenSource _stop = new();
    private Mutex? _mutex;
    private Task? _listener;

    public SingleInstanceCoordinator(DesktopLogger logger) => _logger = logger;

    public bool TryAcquirePrimary()
    {
        _mutex = new Mutex(true, MutexName, out var createdNew);
        if (!createdNew)
        {
            _mutex.Dispose();
            _mutex = null;
        }
        return createdNew;
    }

    public void StartListening(Action onActivate)
    {
        _listener = Task.Run(async () =>
        {
            while (!_stop.IsCancellationRequested)
            {
                try
                {
                    await using var server = new NamedPipeServerStream(
                        _pipeName, PipeDirection.In, 1, PipeTransmissionMode.Byte,
                        PipeOptions.Asynchronous | PipeOptions.CurrentUserOnly);
                    await server.WaitForConnectionAsync(_stop.Token);
                    using var reader = new StreamReader(server);
                    if (string.Equals(await reader.ReadLineAsync(_stop.Token), "activate", StringComparison.Ordinal))
                    {
                        onActivate();
                    }
                }
                catch (OperationCanceledException) { }
                catch (Exception error)
                {
                    _logger.Warning("activation_listener", error.Message);
                    await Task.Delay(150, _stop.Token).ConfigureAwait(false);
                }
            }
        });
    }

    public async Task<bool> SignalActivationAsync(TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline)
        {
            try
            {
                await using var client = new NamedPipeClientStream(
                    ".", _pipeName, PipeDirection.Out, PipeOptions.Asynchronous, System.Security.Principal.TokenImpersonationLevel.None);
                using var attempt = new CancellationTokenSource(TimeSpan.FromMilliseconds(400));
                await client.ConnectAsync(attempt.Token);
                await using var writer = new StreamWriter(client) { AutoFlush = true };
                await writer.WriteLineAsync("activate");
                return true;
            }
            catch (Exception) when (DateTime.UtcNow < deadline)
            {
                await Task.Delay(100);
            }
        }
        return false;
    }

    public void Dispose()
    {
        _stop.Cancel();
        try { _listener?.Wait(TimeSpan.FromMilliseconds(300)); } catch { }
        if (_mutex is not null)
        {
            try { _mutex.ReleaseMutex(); } catch { }
            _mutex.Dispose();
        }
        _stop.Dispose();
    }
}
