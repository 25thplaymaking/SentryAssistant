namespace Sentry.Contracts;

/// <summary>
/// Semantic lifecycle a Sentry surface can present. This is deliberately separate
/// from <see cref="WorkOrderState"/>: the durable work-order machine has more
/// states than a person needs to distinguish at a glance.
/// </summary>
public enum SentryLifecycleState
{
    Idle,
    Queued,
    Running,
    NeedsInput,
    ReadyForReview,
    Resolved,
    Failed,
    Offline
}

/// <summary>
/// Semantic colour meaning. Field green is reserved for healthy/resolved, amber
/// for queued or working, blue for needs-input, and red only for failure.
/// </summary>
public enum PresenceTone
{
    Neutral,
    Pending,
    Attention,
    Positive,
    Critical
}

public enum PresenceMotion
{
    /// <summary>No animation. Also the forced value when the system reduces motion.</summary>
    None,

    /// <summary>Repeating, restrained movement that reads as ongoing work.</summary>
    Continuous,

    /// <summary>A single settling flourish that reads as completion, not activity.</summary>
    Completion
}

/// <summary>
/// Everything a view needs to render one lifecycle state. Values are data, not
/// XAML, so the policy can be unit tested without a UI thread.
/// </summary>
public sealed record PresenceDescriptor(
    SentryLifecycleState State,
    string Label,
    string Detail,
    string Announcement,
    PresenceTone Tone,
    PresenceMotion Motion,
    bool MaySpeak)
{
    /// <summary>
    /// Applies the Windows reduced-motion setting. Motion is dropped, but tone,
    /// label, detail, and the screen-reader announcement are preserved so the
    /// state stays fully legible without animation.
    /// </summary>
    public PresenceDescriptor WithMotionAllowed(bool motionAllowed) =>
        motionAllowed || Motion == PresenceMotion.None
            ? this
            : this with { Motion = PresenceMotion.None };
}

public static class SentryPresence
{
    public static PresenceDescriptor Describe(SentryLifecycleState state) => state switch
    {
        SentryLifecycleState.Idle => new(
            state,
            "READY",
            "Awaiting work order",
            "Sentry is ready and idle.",
            PresenceTone.Neutral,
            PresenceMotion.None,
            MaySpeak: false),

        SentryLifecycleState.Queued => new(
            state,
            "QUEUED",
            "Waiting for an execution node",
            "Work order queued, waiting for an execution node.",
            PresenceTone.Pending,
            PresenceMotion.Continuous,
            MaySpeak: false),

        SentryLifecycleState.Running => new(
            state,
            "RUNNING",
            "Executing the work order",
            "Sentry is running the work order.",
            PresenceTone.Pending,
            PresenceMotion.Continuous,
            MaySpeak: false),

        SentryLifecycleState.NeedsInput => new(
            state,
            "NEEDS INPUT",
            "Waiting on your answer",
            "Sentry needs your input to continue.",
            PresenceTone.Attention,
            PresenceMotion.Continuous,
            MaySpeak: false),

        SentryLifecycleState.ReadyForReview => new(
            state,
            "REVIEW",
            "Implementation ready for your review",
            "An implementation is ready for your review.",
            PresenceTone.Attention,
            PresenceMotion.Continuous,
            MaySpeak: false),

        SentryLifecycleState.Resolved => new(
            state,
            "RESOLVED",
            "Verified result and evidence ready",
            "Work order resolved.",
            PresenceTone.Positive,
            PresenceMotion.Completion,
            MaySpeak: true),

        SentryLifecycleState.Failed => new(
            state,
            "FAILED",
            "The run stopped without a result",
            "The work order failed.",
            PresenceTone.Critical,
            PresenceMotion.None,
            MaySpeak: false),

        SentryLifecycleState.Offline => new(
            state,
            "OFFLINE",
            "No gateway connection",
            "Sentry is offline and cannot reach the gateway.",
            PresenceTone.Neutral,
            PresenceMotion.None,
            MaySpeak: false),

        _ => throw new ArgumentOutOfRangeException(
            nameof(state), state, "Unknown lifecycle state has no presence descriptor.")
    };

    /// <summary>
    /// Collapses the durable work-order state machine onto the smaller set of
    /// states a person reads at a glance. Terminal states that need no attention
    /// (closed, cancelled) settle back to idle rather than inventing a new visual.
    /// </summary>
    public static SentryLifecycleState FromWorkOrderState(WorkOrderState state) => state switch
    {
        WorkOrderState.Draft => SentryLifecycleState.Idle,
        WorkOrderState.Submitted => SentryLifecycleState.Queued,
        WorkOrderState.Triaged => SentryLifecycleState.Queued,
        WorkOrderState.Assigned => SentryLifecycleState.Queued,
        WorkOrderState.InProgress => SentryLifecycleState.Running,
        WorkOrderState.NeedsClarification => SentryLifecycleState.NeedsInput,
        WorkOrderState.NeedsInput => SentryLifecycleState.NeedsInput,
        WorkOrderState.ChangesRequested => SentryLifecycleState.NeedsInput,
        WorkOrderState.ReadyForReview => SentryLifecycleState.ReadyForReview,
        WorkOrderState.Resolved => SentryLifecycleState.Resolved,
        WorkOrderState.Closed => SentryLifecycleState.Idle,
        WorkOrderState.Cancelled => SentryLifecycleState.Idle,
        WorkOrderState.Failed => SentryLifecycleState.Failed,
        _ => throw new ArgumentOutOfRangeException(
            nameof(state), state, "Unknown work-order state has no presence mapping.")
    };
}
