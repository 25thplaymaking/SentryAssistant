using System.Net.NetworkInformation;
using System.Text.Json;
using Sentry.Contracts;

namespace SentryAssistant.Services;

/// <summary>
/// Looks at this machine and reports what Sentry can actually reach.
///
/// Everything here is read-only observation. Nothing is launched, nothing is
/// written, and no secret's value is ever read: credentials are judged by which
/// fields exist and whether they are empty, which is enough to tell an OAuth
/// session from an API key without the value entering memory.
/// </summary>
public sealed class LocalConnectionProbe
{
    private readonly SettingsService _settings;

    public LocalConnectionProbe(SettingsService settings) => _settings = settings;

    private static string Home => Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);

    /// <summary>
    /// Build the full picture. Takes the gateway status rather than probing it
    /// again so the page cannot disagree with the status line above it.
    /// </summary>
    public ConnectionInventory Inspect(GatewayStatus status)
    {
        var rows = new List<ConnectionDescriptor>();

        rows.Add(ForGateway(status));
        rows.Add(ForTunnel(status));
        rows.Add(ForRuntime(status));
        rows.Add(ForDeviceCredential());
        rows.Add(ForSentryApiKey());

        rows.Add(ConnectionRules.ForHarness(ObserveClaude()));
        rows.Add(ConnectionRules.ForHarness(ObserveCodex()));
        rows.Add(ConnectionRules.ForHarness(ObserveGrok()));

        return new ConnectionInventory(rows);
    }

    // --- control plane -------------------------------------------------------

    private const string ControlPlane = "Control plane";

    private ConnectionDescriptor ForGateway(GatewayStatus status)
    {
        var address = _settings.Settings.GatewayUrl;

        if (string.IsNullOrWhiteSpace(address))
        {
            return ConnectionDescriptor.Absent(
                "Sentry Gateway", ControlPlane,
                "No gateway address is configured.",
                "Set a gateway address in the setup walkthrough.");
        }

        return status.Gateway.State switch
        {
            ConnectionState.Online => ConnectionDescriptor.Working(
                "Sentry Gateway", ControlPlane, $"Answering at {address}."),

            ConnectionState.Degraded => ConnectionDescriptor.Broken(
                "Sentry Gateway", ControlPlane,
                $"Answering at {address}, but reporting: {status.Gateway.Detail}",
                "Check the gateway's database and logs on the server."),

            _ => ConnectionDescriptor.Broken(
                "Sentry Gateway", ControlPlane,
                $"{address} did not answer.",
                "Confirm the forward is up and the gateway container is running."),
        };
    }

    private ConnectionDescriptor ForRuntime(GatewayStatus status) => status.Runtime.State switch
    {
        ConnectionState.Online => ConnectionDescriptor.Working(
            "Agent runtime", ControlPlane, status.Runtime.Detail),

        ConnectionState.Degraded => ConnectionDescriptor.Broken(
            "Agent runtime", ControlPlane, status.Runtime.Detail,
            "Open the Hermes control centre for the specific finding."),

        ConnectionState.NotConfigured => ConnectionDescriptor.Absent(
            "Agent runtime", ControlPlane, status.Runtime.Detail,
            "Configure a gateway first."),

        _ => ConnectionDescriptor.Broken(
            "Agent runtime", ControlPlane, status.Runtime.Detail,
            "The runtime could not be reached through the gateway."),
    };

    /// <summary>
    /// The desktop's own device credential — a Sentry-issued token, not a
    /// provider key. Reported by presence only; the token is never unsealed here.
    /// </summary>
    private ConnectionDescriptor ForDeviceCredential() => _settings.HasRefreshToken
        ? ConnectionDescriptor.Working(
            "This desktop's enrolment", "Credential",
            "Enrolled, with a revocable device credential sealed to this Windows account.")
        : ConnectionDescriptor.Absent(
            "This desktop's enrolment", "Credential",
            "This desktop has not enrolled with the gateway.",
            "Redeem an enrolment code in the setup walkthrough.");

    /// <summary>
    /// Sentry's own provider key. Length only — the value stays sealed.
    /// </summary>
    private ConnectionDescriptor ForSentryApiKey()
    {
        var hasKey = _settings.GetApiKey().Length > 0;

        return hasKey
            ? ConnectionDescriptor.Working(
                "Sentry's provider key", "Credential",
                "An API key is stored, sealed to this Windows account. "
                + "This is the desktop's own key and is not shared with the runtime.")
            : ConnectionDescriptor.Absent(
                "Sentry's provider key", "Credential",
                "No provider API key is stored for the desktop.",
                "Add one in Settings to let Sentry answer directly.");
    }

    // --- the forward ---------------------------------------------------------

    private ConnectionDescriptor ForTunnel(GatewayStatus status)
    {
        var address = _settings.Settings.GatewayUrl;
        var isLoopback = false;
        var port = 0;

        if (Uri.TryCreate(address, UriKind.Absolute, out var uri))
        {
            port = uri.Port;
            isLoopback = uri.IsLoopback;
        }

        var target = _settings.Settings.GatewayTunnelTarget;
        if (string.IsNullOrWhiteSpace(target)) target = "<user>@<gateway-host>";

        return ConnectionRules.ForTunnel(
            address,
            isLoopback,
            port,
            listenerPresent: HasLocalListener(port),
            gatewayAnswered: status.Gateway.State is ConnectionState.Online,
            target);
    }

    /// <summary>
    /// Whether anything is listening on a local port.
    ///
    /// This is what separates "the forward is not running" from "the gateway is
    /// down" — without it, both present identically and point at the wrong end.
    /// </summary>
    private static bool HasLocalListener(int port)
    {
        if (port <= 0) return false;

        try
        {
            return IPGlobalProperties.GetIPGlobalProperties()
                .GetActiveTcpListeners()
                .Any(endpoint => endpoint.Port == port);
        }
        catch (Exception)
        {
            // Better to report no listener than to crash the page.
            return false;
        }
    }

    // --- harnesses -----------------------------------------------------------

    private HarnessObservation ObserveClaude()
    {
        var path = FindOnPath("claude");
        var credentials = Path.Combine(Home, ".claude", ".credentials.json");

        return new HarnessObservation(
            "Claude Code",
            BinaryOnPath: path is not null,
            ConfigPresent: File.Exists(Path.Combine(Home, ".claude", "settings.json"))
                           || File.Exists(Path.Combine(Home, ".claude.json")),
            Credential: ClassifyJsonCredential(
                credentials,
                oauthKeys: ["claudeAiOauth"],
                apiKeyKeys: ["apiKey", "ANTHROPIC_API_KEY"]),
            BinaryPath: path);
    }

    private HarnessObservation ObserveCodex()
    {
        var path = FindOnPath("codex");
        var home = Path.Combine(Home, ".codex");

        return new HarnessObservation(
            "Codex",
            BinaryOnPath: path is not null,
            ConfigPresent: File.Exists(Path.Combine(home, "config.toml")),
            Credential: ClassifyJsonCredential(
                Path.Combine(home, "auth.json"),
                oauthKeys: ["tokens"],
                apiKeyKeys: ["OPENAI_API_KEY"]),
            BinaryPath: path);
    }

    private HarnessObservation ObserveGrok()
    {
        var path = FindOnPath("grok");
        var config = Path.Combine(Home, ".config", "grok", "config.json");

        return new HarnessObservation(
            "Grok Build",
            BinaryOnPath: path is not null,
            ConfigPresent: File.Exists(config),
            Credential: ClassifyJsonCredential(
                config, oauthKeys: ["tokens"], apiKeyKeys: ["apiKey", "XAI_API_KEY"]),
            BinaryPath: path);
    }

    /// <summary>
    /// Decide what kind of credential a file holds from its shape alone.
    ///
    /// An API key field that is present but empty is treated as absent — Codex
    /// ships exactly that, and counting it would report a usable key where there
    /// is none. Only lengths and field names are examined; no value is returned.
    /// </summary>
    private static CredentialKind ClassifyJsonCredential(
        string file, string[] oauthKeys, string[] apiKeyKeys)
    {
        if (!File.Exists(file)) return CredentialKind.None;

        try
        {
            using var document = JsonDocument.Parse(File.ReadAllBytes(file));
            var root = document.RootElement;
            if (root.ValueKind != JsonValueKind.Object) return CredentialKind.None;

            foreach (var key in apiKeyKeys)
            {
                if (root.TryGetProperty(key, out var value) && IsNonEmpty(value))
                {
                    return CredentialKind.ApiKey;
                }
            }

            foreach (var key in oauthKeys)
            {
                if (root.TryGetProperty(key, out var value) && IsNonEmpty(value))
                {
                    return CredentialKind.OAuthSubscription;
                }
            }

            return CredentialKind.None;
        }
        catch (Exception)
        {
            // An unreadable or malformed credential file is not a credential.
            return CredentialKind.None;
        }
    }

    /// <summary>
    /// Presence test that stops at the length. Deliberately never calls
    /// GetString() on a secret field.
    /// </summary>
    private static bool IsNonEmpty(JsonElement value) => value.ValueKind switch
    {
        JsonValueKind.String => value.GetString()?.Length > 0,
        JsonValueKind.Object => value.EnumerateObject().Any(),
        JsonValueKind.Array => value.EnumerateArray().Any(),
        JsonValueKind.Null or JsonValueKind.Undefined => false,
        _ => true
    };

    /// <summary>
    /// Resolve a command the way a shell would, so "installed" means the same
    /// thing here as it does in a terminal.
    /// </summary>
    private static string? FindOnPath(string command)
    {
        var path = Environment.GetEnvironmentVariable("PATH");
        if (string.IsNullOrWhiteSpace(path)) return null;

        var extensions = (Environment.GetEnvironmentVariable("PATHEXT") ?? ".EXE;.CMD;.BAT")
            .Split(';', StringSplitOptions.RemoveEmptyEntries);

        foreach (var directory in path.Split(';', StringSplitOptions.RemoveEmptyEntries))
        {
            foreach (var extension in extensions)
            {
                try
                {
                    var candidate = Path.Combine(directory.Trim(), command + extension.Trim());
                    if (File.Exists(candidate)) return candidate;
                }
                catch (ArgumentException)
                {
                    // A malformed PATH entry is not worth failing the whole probe.
                }
            }
        }

        return null;
    }
}
