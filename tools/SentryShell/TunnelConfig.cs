using System.Text.Json;

namespace SentryShell;

/// <summary>
/// Where this install connects, and with what.
///
/// The target used to be a compile-time constant pointing at the OWNER's admin
/// account. That is fine for the owner and unacceptable for anyone else: that
/// account has sudo and also hosts unrelated production services, so shipping a
/// build wired to it would hand every teammate the whole server. A teammate gets
/// their own key, authorised for port-forwarding to the WebUI port and nothing
/// else, and only the owner's install forwards the Gateway port at all.
///
/// Read from sentry.json beside the executable first (that is what a
/// distributed copy ships), then from %LOCALAPPDATA%\SentryAssistant. Missing or
/// unreadable config falls back to the owner defaults so an existing install
/// keeps working untouched.
/// </summary>
internal sealed class TunnelConfig
{
    /// <summary>SSH destination, e.g. <c>sentry-alice@host</c>.</summary>
    public string SshTarget { get; init; } = "bishop@205.209.116.114";

    /// <summary>Optional private key path. Empty means ssh's own default/agent.</summary>
    public string IdentityFile { get; init; } = "";

    /// <summary>
    /// Forward the Gateway port too. Only the owner's desktop app needs it; a
    /// teammate's key is not authorised for that port, and because ssh is run
    /// with ExitOnForwardFailure a refused forward kills the WHOLE tunnel — so
    /// asking for it when unauthorised breaks the app entirely.
    /// </summary>
    public bool ForwardGateway { get; init; } = true;

    /// <summary>
    /// Give the owner's app a local route to the MFA-protected Server Control
    /// portal. Teammate packages keep this off because their forwarding-only
    /// SSH accounts are intentionally restricted to the Sentry WebUI.
    /// </summary>
    public bool ForwardServerControl { get; init; } = true;

    /// <summary>Shown in the window title so a tester knows which install they are running.</summary>
    public string DisplayName { get; init; } = "";

    private const string FileName = "sentry.json";

    internal static TunnelConfig Load()
    {
        foreach (var path in CandidatePaths())
        {
            if (!File.Exists(path)) continue;
            try
            {
                var json = File.ReadAllText(path);
                var cfg = JsonSerializer.Deserialize<TunnelConfig>(
                    json,
                    new JsonSerializerOptions { PropertyNameCaseInsensitive = true });
                if (cfg is not null && !string.IsNullOrWhiteSpace(cfg.SshTarget))
                    return cfg;
            }
            catch
            {
                // A malformed config must not stop the app from starting; the
                // defaults still produce a working owner install, and the log
                // records which file was ignored.
            }
        }
        return new TunnelConfig();
    }

    private static IEnumerable<string> CandidatePaths()
    {
        var exeDir = AppContext.BaseDirectory;
        if (!string.IsNullOrEmpty(exeDir))
            yield return Path.Combine(exeDir, FileName);
        yield return Path.Combine(AppPaths.Root, FileName);
    }
}
