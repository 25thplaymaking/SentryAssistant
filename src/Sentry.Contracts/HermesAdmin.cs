using System.Text.Json.Serialization;

namespace Sentry.Contracts;

/// <summary>
/// Severity of an operational finding. Mirrors the Gateway's admin payload.
/// </summary>
public enum FindingSeverity
{
    Info,
    Warning,
    Critical
}

/// <summary>
/// Something an admin should know, paired with what to do about it.
/// A finding without a remedy just makes someone guess.
/// </summary>
public sealed record AdminFinding([property: JsonPropertyName("severity")] string Severity, [property: JsonPropertyName("summary")] string Summary, [property: JsonPropertyName("remedy")] string Remedy = "")
{
    public FindingSeverity Level => Severity?.ToLowerInvariant() switch
    {
        "critical" => FindingSeverity.Critical,
        "warning" => FindingSeverity.Warning,
        _ => FindingSeverity.Info
    };

    public PresenceTone Tone => Level switch
    {
        FindingSeverity.Critical => PresenceTone.Critical,
        FindingSeverity.Warning => PresenceTone.Pending,
        _ => PresenceTone.Positive
    };

    public bool HasRemedy => !string.IsNullOrWhiteSpace(Remedy);
}

public sealed record RuntimeCapabilityView(
    [property: JsonPropertyName("sessions")] bool Sessions,
    [property: JsonPropertyName("session_search")] bool SessionSearch,
    [property: JsonPropertyName("work_board")] bool WorkBoard,
    [property: JsonPropertyName("cancellation")] bool Cancellation,
    [property: JsonPropertyName("delegation")] bool Delegation);

/// <summary>
/// The admin picture of the agent runtime.
///
/// Field names are declared explicitly because the Gateway serializes snake_case;
/// relying on camelCase inference would deserialize to defaults and quietly
/// report a healthy runtime as unreachable.
/// </summary>
public sealed record HermesAdminSnapshot(
    [property: JsonPropertyName("runtime_name")] string RuntimeName,
    [property: JsonPropertyName("pinned_version")] string PinnedVersion,
    [property: JsonPropertyName("healthy")] bool Healthy,
    [property: JsonPropertyName("degraded_reason")] string? DegradedReason,
    [property: JsonPropertyName("can_answer")] bool CanAnswer,
    [property: JsonPropertyName("capabilities")] RuntimeCapabilityView Capabilities,
    [property: JsonPropertyName("profiles_registered")] int ProfilesRegistered,
    [property: JsonPropertyName("findings")] IReadOnlyList<AdminFinding> Findings)
{
    /// <summary>
    /// A runtime can be perfectly healthy and still useless — reachable, pinned,
    /// answering its health probe, with no model behind it. The control centre
    /// leads with this rather than with Healthy.
    /// </summary>
    public bool FullyOperational => Healthy && CanAnswer;

    public string HeadlineLabel => (Healthy, CanAnswer) switch
    {
        (true, true) => "OPERATIONAL",
        (true, false) => "NO MODEL",
        _ => "UNREACHABLE"
    };

    public PresenceTone HeadlineTone => (Healthy, CanAnswer) switch
    {
        (true, true) => PresenceTone.Positive,
        (true, false) => PresenceTone.Critical,
        _ => PresenceTone.Critical
    };

    public IReadOnlyList<AdminFinding> CriticalFindings =>
        Findings.Where(f => f.Level is FindingSeverity.Critical).ToList();
}

