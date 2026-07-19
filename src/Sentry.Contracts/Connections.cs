namespace Sentry.Contracts;

/// <summary>
/// How a dependency stands relative to Sentry.
///
/// The split that matters most here is Missing versus Misconfigured. Something
/// absent needs installing; something half-present looks set up and is not, and
/// that is the case that costs an afternoon.
/// </summary>
public enum IntegrationState
{
    /// <summary>Probed, answered, and usable by Sentry.</summary>
    Connected,

    /// <summary>
    /// Present and working on this machine, but not something Sentry can use.
    /// A subscription-authenticated harness is the archetype: it works perfectly
    /// for the person and offers nothing a runtime can be handed.
    /// </summary>
    Detected,

    /// <summary>Not on this machine at all.</summary>
    Missing,

    /// <summary>
    /// Partly present in a way that will not work — configuration without the
    /// program it configures, or a credential of the wrong kind.
    /// </summary>
    Misconfigured,

    /// <summary>Does not apply to how this machine is set up.</summary>
    NotApplicable
}

/// <summary>
/// What kind of credential something holds. Kept separate from
/// <see cref="IntegrationState"/> because "has credentials" is the exact
/// ambiguity this surface exists to remove: an OAuth session and an API key are
/// both credentials, and only one of them can be given to a runtime.
/// </summary>
public enum CredentialKind
{
    None,

    /// <summary>Subscription OAuth. Not transferable to a third-party runtime.</summary>
    OAuthSubscription,

    /// <summary>A provider API key, which a runtime can actually be configured with.</summary>
    ApiKey,

    /// <summary>A Sentry-issued, revocable device credential.</summary>
    DeviceToken
}

/// <summary>
/// One dependency, as observed.
///
/// Built through the static factories rather than directly, so that a state
/// needing action cannot be constructed without saying what the action is —
/// the compiler enforces what would otherwise be a review comment.
/// </summary>
public sealed record ConnectionDescriptor
{
    private ConnectionDescriptor(
        string name, string category, IntegrationState state, string detail, string remedy)
    {
        Name = name;
        Category = category;
        State = state;
        Detail = detail;
        Remedy = remedy;
    }

    public string Name { get; init; }

    /// <summary>Harness, Runtime, Credential, Transport, or Control plane.</summary>
    public string Category { get; init; }

    public IntegrationState State { get; init; }

    /// <summary>What was actually observed — never a guess.</summary>
    public string Detail { get; init; }

    /// <summary>The specific next action. Empty only when nothing is needed.</summary>
    public string Remedy { get; init; }

    /// <summary>Working, and Sentry can use it.</summary>
    public static ConnectionDescriptor Working(string name, string category, string detail) =>
        new(name, category, IntegrationState.Connected, detail, string.Empty);

    /// <summary>Found, but not usable by Sentry. Says why, and what would change that.</summary>
    public static ConnectionDescriptor Found(
        string name, string category, string detail, string remedy) =>
        new(name, category, IntegrationState.Detected, detail, remedy);

    public static ConnectionDescriptor Absent(
        string name, string category, string detail, string remedy) =>
        new(name, category, IntegrationState.Missing, detail, remedy);

    public static ConnectionDescriptor Broken(
        string name, string category, string detail, string remedy) =>
        new(name, category, IntegrationState.Misconfigured, detail, remedy);

    public static ConnectionDescriptor Irrelevant(string name, string category, string detail) =>
        new(name, category, IntegrationState.NotApplicable, detail, string.Empty);

    public bool HasRemedy => !string.IsNullOrWhiteSpace(Remedy);

    /// <summary>
    /// Whether this should draw the eye. Deliberately excludes Detected: a
    /// working subscription harness is not a problem to be fixed.
    /// </summary>
    public bool NeedsAttention => State is IntegrationState.Missing or IntegrationState.Misconfigured;

    public string Label => State switch
    {
        IntegrationState.Connected => "CONNECTED",
        IntegrationState.Detected => "DETECTED",
        IntegrationState.Missing => "MISSING",
        IntegrationState.Misconfigured => "MISCONFIGURED",
        _ => "N/A"
    };

    public PresenceTone Tone => State switch
    {
        IntegrationState.Connected => PresenceTone.Positive,
        IntegrationState.Detected => PresenceTone.Attention,
        IntegrationState.Missing => PresenceTone.Pending,

        // Worse than missing: it reads as set up and is not.
        IntegrationState.Misconfigured => PresenceTone.Critical,
        _ => PresenceTone.Neutral
    };
}

/// <summary>
/// What a probe actually saw of a coding harness. Raw observation only — no
/// judgement, and never the value of a secret.
/// </summary>
public sealed record HarnessObservation(
    string Name,
    bool BinaryOnPath,
    bool ConfigPresent,
    CredentialKind Credential,
    string? BinaryPath = null);

/// <summary>
/// Turns observations into states.
///
/// Kept apart from the probing so the judgements can be tested without a
/// machine that happens to be in the right condition. The probe decides what is
/// true; this decides what it means.
/// </summary>
public static class ConnectionRules
{
    public const string HarnessCategory = "Coding harness";
    public const string TransportCategory = "Transport";

    /// <summary>
    /// Classify a harness.
    ///
    /// The case this exists for: configuration present with no program to run.
    /// Reporting that as Missing would hide that something is already set up and
    /// wrong, and reporting it as present would be a lie.
    /// </summary>
    public static ConnectionDescriptor ForHarness(HarnessObservation observed)
    {
        if (!observed.BinaryOnPath)
        {
            return observed.ConfigPresent
                ? ConnectionDescriptor.Broken(
                    observed.Name, HarnessCategory,
                    "Configured on this machine, but the program is not installed.",
                    $"Install the {observed.Name} CLI, or remove its configuration.")
                : ConnectionDescriptor.Absent(
                    observed.Name, HarnessCategory,
                    "Not installed.",
                    $"Install the {observed.Name} CLI to let Sentry drive it.");
        }

        return observed.Credential switch
        {
            // Installed and signed in, but with a subscription rather than a key.
            // Calling this "connected" would imply Sentry holds something it can
            // hand to a runtime, which is the misunderstanding that led to
            // hunting for an API key that was never there.
            CredentialKind.OAuthSubscription => ConnectionDescriptor.Found(
                observed.Name, HarnessCategory,
                "Installed, signed in with a subscription account. Sentry can drive it "
                + "locally, but subscription sign-in is not a key the runtime can use.",
                "To give the runtime its own inference, provision a provider API key."),

            CredentialKind.ApiKey => ConnectionDescriptor.Working(
                observed.Name, HarnessCategory,
                $"Installed at {observed.BinaryPath ?? "an unknown path"}, authenticated with an API key."),

            _ => ConnectionDescriptor.Broken(
                observed.Name, HarnessCategory,
                "Installed, but no credentials were found.",
                $"Sign in with {observed.Name} so Sentry can drive it.")
        };
    }

    /// <summary>
    /// Classify the hop between this desktop and the gateway.
    ///
    /// The gateway binds loopback on the server, so on this setup it is reached
    /// through an SSH forward that nothing supervises. Without this row, a
    /// forward that is simply not running presents as an offline gateway and
    /// sends someone to inspect a server that is perfectly healthy.
    /// </summary>
    public static ConnectionDescriptor ForTunnel(
        string gatewayUrl,
        bool gatewayIsLoopback,
        int localPort,
        bool listenerPresent,
        bool gatewayAnswered,
        string sshTarget)
    {
        if (!gatewayIsLoopback)
        {
            return ConnectionDescriptor.Irrelevant(
                "SSH tunnel", TransportCategory,
                string.IsNullOrWhiteSpace(gatewayUrl)
                    ? "No gateway address is configured."
                    : $"{gatewayUrl} is reached directly, so no forward is needed.");
        }

        var command = $"ssh -N -L {localPort}:127.0.0.1:8090 {sshTarget}";

        if (!listenerPresent)
        {
            return ConnectionDescriptor.Absent(
                "SSH tunnel", TransportCategory,
                $"Nothing is listening on port {localPort}, so the gateway cannot be reached.",
                $"Start the forward: {command}");
        }

        return gatewayAnswered
            ? ConnectionDescriptor.Working(
                "SSH tunnel", TransportCategory,
                $"Forwarding port {localPort} to the gateway.")

            // A listener that forwards to nothing is the confusing one: the port
            // accepts a connection and the request still fails.
            : ConnectionDescriptor.Broken(
                "SSH tunnel", TransportCategory,
                $"Port {localPort} is listening, but the gateway did not answer through it.",
                $"Restart the forward: {command}");
    }
}

/// <summary>
/// Everything Sentry depends on, and whether it is actually there.
/// </summary>
public sealed record ConnectionInventory(IReadOnlyList<ConnectionDescriptor> Connections)
{
    public static ConnectionInventory Empty { get; } = new([]);

    public int NeedingAttention => Connections.Count(c => c.NeedsAttention);

    public IReadOnlyList<ConnectionDescriptor> Broken =>
        Connections.Where(c => c.State is IntegrationState.Misconfigured).ToList();

    /// <summary>
    /// Groups preserve the order things were probed in rather than sorting
    /// alphabetically, so the control plane stays above the harnesses.
    /// </summary>
    public IReadOnlyList<IGrouping<string, ConnectionDescriptor>> ByCategory =>
        Connections.GroupBy(c => c.Category).ToList();

    /// <summary>
    /// One line for the top of the page.
    ///
    /// Leads with what is broken, because a count of working things is not what
    /// someone opening this page is looking for.
    /// </summary>
    public string Summary
    {
        get
        {
            if (Connections.Count == 0) return "Nothing has been checked yet.";

            var broken = Broken.Count;
            var missing = Connections.Count(c => c.State is IntegrationState.Missing);

            // Both are reported when both exist. Leading with the broken count and
            // stopping there would quietly drop the missing ones from a sentence
            // that reads like a complete tally.
            if (broken > 0 && missing > 0)
            {
                var brokenNoun = broken == 1 ? "connection needs" : "connections need";
                return $"{broken} {brokenNoun} fixing, and {missing} more "
                       + $"{(missing == 1 ? "is" : "are")} not set up.";
            }

            if (broken > 0)
            {
                var noun = broken == 1 ? "connection needs" : "connections need";
                return $"{broken} {noun} fixing.";
            }

            if (missing > 0)
            {
                var noun = missing == 1 ? "connection is" : "connections are";
                return $"{missing} {noun} not set up.";
            }

            return "Everything Sentry depends on is present.";
        }
    }
}
