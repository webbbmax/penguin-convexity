using System.Net.Sockets;
using System.Net.Http;
using System.Text.Json;

namespace PenguinConvexity.Desktop;

public enum HealthState
{
    Healthy,
    PortAvailable,
    PortOccupied,
    IdentityMismatch
}

public sealed record HealthProbeResult(HealthState State, string Detail);

public sealed class ServiceHealthProbe
{
    private readonly HttpClient _client;

    public ServiceHealthProbe(HttpClient client) => _client = client;

    public async Task<HealthProbeResult> ProbeAsync(CancellationToken cancellationToken)
    {
        try
        {
            using var response = await _client.GetAsync(AppPaths.HealthUrl, cancellationToken);
            if (response.IsSuccessStatusCode)
            {
                await using var stream = await response.Content.ReadAsStreamAsync(cancellationToken);
                using var json = await JsonDocument.ParseAsync(stream, cancellationToken: cancellationToken);
                var root = json.RootElement;
                if (Matches(root, "product", "企鹅投研-凸性") &&
                    Matches(root, "status", "ready") &&
                    Matches(root, "migrationRelease", "M1.0") &&
                    Matches(root, "convexityRelease", "C1.7") &&
                    Matches(root, "experienceRelease", "C2.4"))
                {
                    return new HealthProbeResult(HealthState.Healthy, "企鹅投研身份检查通过");
                }
                return new HealthProbeResult(HealthState.IdentityMismatch, "8766服务响应与企鹅投研项目身份不一致");
            }
        }
        catch (JsonException)
        {
            return new HealthProbeResult(HealthState.PortOccupied, "8766端口返回了无法识别的内容");
        }
        catch (HttpRequestException) { }
        catch (TaskCanceledException) when (!cancellationToken.IsCancellationRequested) { }

        return await IsPortOpenAsync(cancellationToken)
            ? new HealthProbeResult(HealthState.PortOccupied, "8766端口已被其他程序占用")
            : new HealthProbeResult(HealthState.PortAvailable, "8766端口可用");
    }

    private static bool Matches(JsonElement root, string name, string expected) =>
        root.TryGetProperty(name, out var value) && string.Equals(value.GetString(), expected, StringComparison.Ordinal);

    private static async Task<bool> IsPortOpenAsync(CancellationToken cancellationToken)
    {
        using var client = new TcpClient();
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeout.CancelAfter(TimeSpan.FromMilliseconds(600));
        try
        {
            await client.ConnectAsync("127.0.0.1", 8766, timeout.Token);
            return true;
        }
        catch
        {
            return false;
        }
    }
}
