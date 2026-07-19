namespace Sentry.Contracts;

/// <summary>
/// Reachability of a Sentry component as far as this client can actually tell.
/// </summary>
public enum ConnectionState
{
    /// <summary>Not yet checked. Never rendered as healthy.</summary>
    Unknown,

    /// <summary>Checked and answering.</summary>
    Online,

    /// <summary>Answering, but reporting a problem of its own.</summary>
    Degraded,

    /// <summary>Checked and not answering.</summary>
    Offline,

    /// <summary>Deliberately not configured. Distinct from a failure.</summary>
    NotConfigured
}

/// <summary>
/// A live status line for the shell. Every value here came from an actual probe;
/// there is no constructor that produces a healthy-looking record without one.
/// </summary>
public sealed record ComponentStatus(
    string Name,
    ConnectionState State,
    string Detail)
{
    public static ComponentStatus Unknown(string name) =>
        new(name, ConnectionState.Unknown, "Not checked yet");

    public static ComponentStatus NotConfigured(string name, string reason) =>
        new(name, ConnectionState.NotConfigured, reason);

    /// <summary>Whether this should read as reassuring in the UI.</summary>
    public bool IsHealthy => State is ConnectionState.Online;

    /// <summary>
    /// Short label for the status column. Deliberately not "ONLINE" unless it
    /// really is, so a placeholder can never masquerade as a live connection.
    /// </summary>
    public string Label => State switch
    {
        ConnectionState.Online => "ONLINE",
        ConnectionState.Degraded => "DEGRADED",
        ConnectionState.Offline => "OFFLINE",
        ConnectionState.NotConfigured => "NOT SET UP",
        _ => "UNKNOWN"
    };

    /// <summary>Maps onto the presence tones so the shell stays consistent.</summary>
    public PresenceTone Tone => State switch
    {
        ConnectionState.Online => PresenceTone.Positive,
        ConnectionState.Degraded => PresenceTone.Pending,
        ConnectionState.Offline => PresenceTone.Critical,
        ConnectionState.NotConfigured => PresenceTone.Neutral,
        _ => PresenceTone.Neutral
    };
}

/// <summary>The full control-plane picture the inspector renders.</summary>
public sealed record GatewayStatus(
    ComponentStatus Gateway,
    ComponentStatus Runtime,
    ComponentStatus Node,
    DateTimeOffset CheckedAt)
{
    public static GatewayStatus Unknown() => new(
        ComponentStatus.Unknown("Gateway"),
        ComponentStatus.Unknown("Runtime"),
        ComponentStatus.Unknown("Windows node"),
        DateTimeOffset.MinValue);

    /// <summary>
    /// True only when every component was probed and answered. Used to decide
    /// whether the shell may offer gateway-backed actions at all.
    /// </summary>
    public bool FullyOnline =>
        Gateway.IsHealthy && Runtime.IsHealthy && Node.IsHealthy;
}
