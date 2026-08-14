namespace PenguinConvexity.Desktop;

public enum UserFaultKind
{
    WebViewUnavailable,
    LocalServiceFailure,
    PortConflict,
    IdentityFailure,
    PageFailure
}

public sealed record UserFaultCopy(string Title, string Message, string PrimaryAction, string Stage)
{
    public static UserFaultCopy For(UserFaultKind kind) => kind switch
    {
        UserFaultKind.WebViewUnavailable => new("软件显示组件需要安装或修复", "当前无法显示产品页面。在线可打开微软安装页；离线时使用微软WebView2 Evergreen独立安装程序。", "打开微软安装页", "软件显示组件检查"),
        UserFaultKind.LocalServiceFailure => new("本地服务没有启动", "当前无法读取本机项目数据。", "重试启动", "本地服务启动"),
        UserFaultKind.PortConflict => new("8766端口正在被其他程序使用", "企鹅投研没有结束该程序。", "重新检测", "端口检查"),
        UserFaultKind.IdentityFailure => new("本地服务没有通过身份检查", "当前服务不是本项目可用的企鹅投研服务。", "重新检测", "项目服务身份检查"),
        UserFaultKind.PageFailure => new("产品页面意外停止", "软件窗口仍然可用，可以重新加载。", "重新加载", "产品页面显示"),
        _ => throw new ArgumentOutOfRangeException(nameof(kind))
    };

    public static string SanitizeDetail(string? detail)
    {
        if (string.IsNullOrWhiteSpace(detail)) return "已写入软件日志。";
        var safe = string.Join(' ', detail.Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries));
        safe = System.Text.RegularExpressions.Regex.Replace(safe, "null", "未提供", System.Text.RegularExpressions.RegexOptions.IgnoreCase);
        safe = System.Text.RegularExpressions.Regex.Replace(safe, "exception", "内部错误", System.Text.RegularExpressions.RegexOptions.IgnoreCase);
        return safe.Length <= 240 ? safe : safe[..240] + "…";
    }
}
