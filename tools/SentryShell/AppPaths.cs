namespace SentryShell;

/// <summary>
/// The single source of truth for where Frontir Sentry keeps its per-user
/// state on a Windows machine, and the one place that creates those areas.
///
/// Everything lives under one root so an end user (or an uninstaller) has a
/// single folder to reason about. Nothing here is the install location: the
/// executable ships in <c>%LOCALAPPDATA%\SentryAssistant\app</c>, while these
/// are the writable areas the running app owns.
/// </summary>
internal static class AppPaths
{
    /// <summary>Per-user data root: <c>%LOCALAPPDATA%\SentryAssistant</c>.</summary>
    internal static string Root { get; } = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "SentryAssistant");

    /// <summary>
    /// WebView2 user-data folder. Holds the login cookie, localStorage, and the
    /// chosen skin, which is why it must persist across launches rather than
    /// living in a temp dir.
    /// </summary>
    internal static string WebView2 { get; } = Path.Combine(Root, "webview2");

    /// <summary>The tunnel's rolling log file (created on first write).</summary>
    internal static string ShellLog { get; } = Path.Combine(Root, "shell.log");

    /// <summary>Records the ssh PID so an orphan can be reaped next launch.</summary>
    internal static string PidFile { get; } = Path.Combine(Root, "tunnel.pid");

    /// <summary>
    /// Create every directory the app writes into. Called once at startup,
    /// before any consumer touches these paths. Throws only if the root itself
    /// cannot be created — that is genuinely fatal and the caller reports it;
    /// the WebView2 profile is best-effort because WebView2 will also create it
    /// on demand, so a transient failure there should not block launch.
    /// </summary>
    internal static void EnsureAll()
    {
        Directory.CreateDirectory(Root);   // fatal if this throws
        try { Directory.CreateDirectory(WebView2); }
        catch { /* WebView2 recreates this itself; not worth aborting over */ }
    }
}
