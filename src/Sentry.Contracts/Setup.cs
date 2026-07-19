namespace Sentry.Contracts;

/// <summary>
/// Where a step sits in the walkthrough. Deliberately explicit about "blocked":
/// a step that cannot start yet is different from one that failed.
/// </summary>
public enum SetupStepState
{
    /// <summary>Earlier steps are incomplete, so this cannot be attempted.</summary>
    Blocked,

    /// <summary>Ready to attempt.</summary>
    Ready,

    /// <summary>Attempt in progress.</summary>
    Working,

    /// <summary>Attempted and failed. Retryable.</summary>
    Failed,

    Done
}

public enum SetupStepId
{
    GatewayAddress,
    EnrolDevice,
    VerifyRuntime
}

/// <summary>One step, with whatever the last attempt told us.</summary>
public sealed record SetupStep(
    SetupStepId Id,
    string Title,
    string Description,
    SetupStepState State,
    string Detail = "")
{
    public bool CanAttempt => State is SetupStepState.Ready or SetupStepState.Failed;

    public PresenceTone Tone => State switch
    {
        SetupStepState.Done => PresenceTone.Positive,
        SetupStepState.Working => PresenceTone.Pending,
        SetupStepState.Failed => PresenceTone.Critical,
        SetupStepState.Ready => PresenceTone.Attention,
        _ => PresenceTone.Neutral
    };

    /// <summary>Number shown in the UI, or a tick once complete.</summary>
    public string Marker(int ordinal) => State is SetupStepState.Done ? "✓" : ordinal.ToString();
}

/// <summary>
/// The walkthrough.
///
/// Steps are strictly ordered and each unlocks the next, because attempting them
/// out of order produces confusing failures: enrolling before an address is set
/// fails for a reason that has nothing to do with enrolment.
/// </summary>
public sealed record SetupProgress(IReadOnlyList<SetupStep> Steps)
{
    public static SetupProgress Initial() => new(
    [
        new SetupStep(
            SetupStepId.GatewayAddress,
            "Connect to the gateway",
            "Point Sentry at your control plane and confirm it answers.",
            SetupStepState.Ready),
        new SetupStep(
            SetupStepId.EnrolDevice,
            "Enrol this device",
            "Redeem a one-time code so this desktop gets its own revocable credentials.",
            SetupStepState.Blocked),
        new SetupStep(
            SetupStepId.VerifyRuntime,
            "Verify the runtime",
            "Check that the agent runtime is reachable and able to answer.",
            SetupStepState.Blocked),
    ]);

    public SetupStep this[SetupStepId id] => Steps.First(step => step.Id == id);

    public bool IsComplete => Steps.All(step => step.State is SetupStepState.Done);

    /// <summary>The step the person should act on next, if any.</summary>
    public SetupStep? CurrentStep =>
        Steps.FirstOrDefault(step => step.State is not SetupStepState.Done);

    public int CompletedCount => Steps.Count(step => step.State is SetupStepState.Done);

    /// <summary>
    /// Record the outcome of a step. Completing one unlocks the next; failing one
    /// re-locks everything after it, so a broken gateway address cannot leave a
    /// later step looking attemptable.
    /// </summary>
    public SetupProgress With(SetupStepId id, SetupStepState state, string detail = "")
    {
        var index = Steps.ToList().FindIndex(step => step.Id == id);
        if (index < 0) throw new ArgumentOutOfRangeException(nameof(id), id, "Unknown setup step.");

        var updated = Steps.Select(step => step with { }).ToList();
        updated[index] = updated[index] with { State = state, Detail = detail };

        for (var next = index + 1; next < updated.Count; next++)
        {
            if (state is SetupStepState.Done)
            {
                // Only the immediately following step opens; the rest stay blocked
                // until it too completes.
                updated[next] = updated[next] with
                {
                    State = next == index + 1 && updated[next].State is SetupStepState.Blocked
                        ? SetupStepState.Ready
                        : updated[next].State
                };
            }
            else
            {
                // A regression invalidates everything downstream, including work
                // that had previously succeeded.
                updated[next] = updated[next] with { State = SetupStepState.Blocked, Detail = "" };
            }
        }

        return new SetupProgress(updated);
    }
}
