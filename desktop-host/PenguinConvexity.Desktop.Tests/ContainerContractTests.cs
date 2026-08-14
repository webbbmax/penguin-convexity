using System.Net;
using System.Text;
using PenguinConvexity.Desktop;

namespace PenguinConvexity.Desktop.Tests;

public sealed class ContainerContractTests
{
    [Theory]
    [InlineData("http://127.0.0.1:8766/desktop/index.html", true)]
    [InlineData("http://127.0.0.1:8766/api/health", true)]
    [InlineData("https://example.com/", false)]
    [InlineData("http://localhost:8766/", false)]
    [InlineData("file:///c:/temp/index.html", false)]
    public void NavigationPolicyOnlyAllowsExactLocalOrigin(string uri, bool expected) =>
        Assert.Equal(expected, NavigationPolicy.IsAllowedLocalUri(uri));

    [Theory]
    [InlineData("http://127.0.0.1:8766/project-detail.html", true, NavigationDisposition.AllowLocal)]
    [InlineData("https://github.com/example/project", true, NavigationDisposition.OpenExternal)]
    [InlineData("https://github.com/example/project", false, NavigationDisposition.Block)]
    [InlineData("file:///c:/temp/index.html", true, NavigationDisposition.Block)]
    public void NavigationPolicySeparatesLocalUserExternalAndBlockedNavigation(
        string uri,
        bool isUserInitiated,
        NavigationDisposition expected) =>
        Assert.Equal(expected, NavigationPolicy.Decide(uri, isUserInitiated));

    [Fact]
    public async Task HealthProbeRequiresFullProductIdentity()
    {
        var valid = """{"product":"企鹅投研-凸性","status":"ready","migrationRelease":"M1.0","convexityRelease":"C1.7","experienceRelease":"C2.4"}""";
        var probe = new ServiceHealthProbe(new HttpClient(new FakeHandler(valid)));
        Assert.Equal(HealthState.Healthy, (await probe.ProbeAsync(CancellationToken.None)).State);

        var wrong = valid.Replace("C2.4", "C9.9");
        probe = new ServiceHealthProbe(new HttpClient(new FakeHandler(wrong)));
        Assert.Equal(HealthState.IdentityMismatch, (await probe.ProbeAsync(CancellationToken.None)).State);
    }

    [Fact]
    public void OffscreenWindowReturnsToPrimaryWorkArea()
    {
        var state = new DesktopWindowState { Left = 9000, Top = 9000, Width = 1440, Height = 900 };
        var normalized = WindowStateStore.Normalize(state, new[] { new ScreenArea(0, 0, 1920, 1040, true) });
        Assert.Equal(240, normalized.Left);
        Assert.Equal(70, normalized.Top);
    }

    [Fact]
    public void FaultCopyIsPlainLanguageAndSanitized()
    {
        foreach (var kind in Enum.GetValues<UserFaultKind>())
        {
            var copy = UserFaultCopy.For(kind);
            Assert.DoesNotContain("Null", copy.Title + copy.Message);
            Assert.DoesNotContain("Exception", copy.Title + copy.Message);
        }
        Assert.Equal("first second", UserFaultCopy.SanitizeDetail("first\r\nsecond"));
        Assert.DoesNotContain("Null", UserFaultCopy.SanitizeDetail("NullReferenceException"), StringComparison.OrdinalIgnoreCase);
        Assert.Contains("WebView2 Evergreen", UserFaultCopy.For(UserFaultKind.WebViewUnavailable).Message);
    }

    private sealed class FakeHandler(string content) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken) =>
            Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new StringContent(content, Encoding.UTF8, "application/json")
            });
    }
}
