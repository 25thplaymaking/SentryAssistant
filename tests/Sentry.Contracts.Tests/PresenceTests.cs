using Sentry.Contracts;

namespace Sentry.Contracts.Tests;

public class PresenceTests
{
    private static readonly SentryLifecycleState[] AllStates =
        Enum.GetValues<SentryLifecycleState>();

    [Fact]
    public void EveryLifecycleStateHasADistinctNonEmptyLabel()
    {
        var labels = AllStates.Select(state => SentryPresence.Describe(state).Label).ToList();

        Assert.All(labels, label => Assert.False(string.IsNullOrWhiteSpace(label)));
        Assert.Equal(labels.Count, labels.Distinct(StringComparer.Ordinal).Count());
    }

    [Fact]
    public void EveryLifecycleStateHasAnAccessibleAnnouncement()
    {
        foreach (var state in AllStates)
        {
            var descriptor = SentryPresence.Describe(state);
            Assert.False(string.IsNullOrWhiteSpace(descriptor.Announcement));
            Assert.False(string.IsNullOrWhiteSpace(descriptor.Detail));
        }
    }

    // Product rule: TTS fires only on a resolved result, never during commands,
    // tool calls, progress, or approval prompts.
    [Fact]
    public void OnlyResolvedMayRequestSpeech()
    {
        foreach (var state in AllStates)
        {
            var descriptor = SentryPresence.Describe(state);
            if (state == SentryLifecycleState.Resolved)
            {
                Assert.True(descriptor.MaySpeak);
            }
            else
            {
                Assert.False(descriptor.MaySpeak, $"{state} must never request speech.");
            }
        }
    }

    // Product rule: field green means selected/healthy/resolved, amber means
    // queued or working, blue means needs-input, red is reserved for failure.
    [Theory]
    [InlineData(SentryLifecycleState.Idle, PresenceTone.Neutral)]
    [InlineData(SentryLifecycleState.Queued, PresenceTone.Pending)]
    [InlineData(SentryLifecycleState.Running, PresenceTone.Pending)]
    [InlineData(SentryLifecycleState.NeedsInput, PresenceTone.Attention)]
    [InlineData(SentryLifecycleState.ReadyForReview, PresenceTone.Attention)]
    [InlineData(SentryLifecycleState.Resolved, PresenceTone.Positive)]
    [InlineData(SentryLifecycleState.Failed, PresenceTone.Critical)]
    [InlineData(SentryLifecycleState.Offline, PresenceTone.Neutral)]
    public void ToneFollowsSemanticColourPolicy(SentryLifecycleState state, PresenceTone expected)
    {
        Assert.Equal(expected, SentryPresence.Describe(state).Tone);
    }

    // Product rule: Resolved must read as calm completion, not another working animation.
    [Fact]
    public void OnlyWorkingStatesUseContinuousMotion()
    {
        Assert.Equal(PresenceMotion.Continuous, SentryPresence.Describe(SentryLifecycleState.Running).Motion);
        Assert.Equal(PresenceMotion.Continuous, SentryPresence.Describe(SentryLifecycleState.Queued).Motion);
        Assert.Equal(PresenceMotion.Completion, SentryPresence.Describe(SentryLifecycleState.Resolved).Motion);
        Assert.Equal(PresenceMotion.None, SentryPresence.Describe(SentryLifecycleState.Idle).Motion);
        Assert.Equal(PresenceMotion.None, SentryPresence.Describe(SentryLifecycleState.Offline).Motion);
    }

    // Accessibility rule: with Windows animations disabled nothing may animate,
    // yet the state must still be communicated by tone, label, and text.
    [Fact]
    public void ReducedMotionRemovesAnimationButPreservesMeaning()
    {
        foreach (var state in AllStates)
        {
            var descriptor = SentryPresence.Describe(state);
            var reduced = descriptor.WithMotionAllowed(false);

            Assert.Equal(PresenceMotion.None, reduced.Motion);
            Assert.Equal(descriptor.Tone, reduced.Tone);
            Assert.Equal(descriptor.Label, reduced.Label);
            Assert.Equal(descriptor.Announcement, reduced.Announcement);
            Assert.Equal(descriptor.MaySpeak, reduced.MaySpeak);
        }
    }

    [Fact]
    public void MotionAllowedLeavesDescriptorUnchanged()
    {
        foreach (var state in AllStates)
        {
            var descriptor = SentryPresence.Describe(state);
            Assert.Equal(descriptor, descriptor.WithMotionAllowed(true));
        }
    }

    // Every durable work-order state must resolve to exactly one presence state,
    // so the desktop can render a remote order without an unmapped gap.
    [Fact]
    public void EveryWorkOrderStateMapsToAPresenceState()
    {
        foreach (var state in Enum.GetValues<WorkOrderState>())
        {
            var mapped = SentryPresence.FromWorkOrderState(state);
            Assert.Contains(mapped, AllStates);
        }
    }

    [Theory]
    [InlineData(WorkOrderState.Draft, SentryLifecycleState.Idle)]
    [InlineData(WorkOrderState.Submitted, SentryLifecycleState.Queued)]
    [InlineData(WorkOrderState.Triaged, SentryLifecycleState.Queued)]
    [InlineData(WorkOrderState.Assigned, SentryLifecycleState.Queued)]
    [InlineData(WorkOrderState.InProgress, SentryLifecycleState.Running)]
    [InlineData(WorkOrderState.NeedsClarification, SentryLifecycleState.NeedsInput)]
    [InlineData(WorkOrderState.NeedsInput, SentryLifecycleState.NeedsInput)]
    [InlineData(WorkOrderState.ReadyForReview, SentryLifecycleState.ReadyForReview)]
    [InlineData(WorkOrderState.ChangesRequested, SentryLifecycleState.NeedsInput)]
    [InlineData(WorkOrderState.Resolved, SentryLifecycleState.Resolved)]
    [InlineData(WorkOrderState.Closed, SentryLifecycleState.Idle)]
    [InlineData(WorkOrderState.Cancelled, SentryLifecycleState.Idle)]
    [InlineData(WorkOrderState.Failed, SentryLifecycleState.Failed)]
    public void WorkOrderStateMapsToExpectedPresence(WorkOrderState state, SentryLifecycleState expected)
    {
        Assert.Equal(expected, SentryPresence.FromWorkOrderState(state));
    }

    [Fact]
    public void UnknownLifecycleStateIsRejectedRatherThanRenderedBlank()
    {
        Assert.Throws<ArgumentOutOfRangeException>(
            () => SentryPresence.Describe((SentryLifecycleState)999));
    }
}
