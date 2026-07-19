using Sentry.Contracts;

namespace Sentry.Contracts.Tests;

public class SetupTests
{
    [Fact]
    public void OnlyTheFirstStepStartsAttemptable()
    {
        var progress = SetupProgress.Initial();

        Assert.Equal(SetupStepState.Ready, progress[SetupStepId.GatewayAddress].State);
        Assert.Equal(SetupStepState.Blocked, progress[SetupStepId.EnrolDevice].State);
        Assert.Equal(SetupStepState.Blocked, progress[SetupStepId.VerifyRuntime].State);
    }

    // Attempting out of order produces failures with misleading causes, so a
    // blocked step is not attemptable at all.
    [Fact]
    public void BlockedStepsCannotBeAttempted()
    {
        var progress = SetupProgress.Initial();
        Assert.True(progress[SetupStepId.GatewayAddress].CanAttempt);
        Assert.False(progress[SetupStepId.EnrolDevice].CanAttempt);
    }

    [Fact]
    public void CompletingAStepUnlocksOnlyTheNextOne()
    {
        var progress = SetupProgress.Initial()
            .With(SetupStepId.GatewayAddress, SetupStepState.Done);

        Assert.Equal(SetupStepState.Ready, progress[SetupStepId.EnrolDevice].State);
        // The third stays shut until the second is done.
        Assert.Equal(SetupStepState.Blocked, progress[SetupStepId.VerifyRuntime].State);
    }

    [Fact]
    public void WalkingAllStepsCompletesTheSetup()
    {
        var progress = SetupProgress.Initial()
            .With(SetupStepId.GatewayAddress, SetupStepState.Done)
            .With(SetupStepId.EnrolDevice, SetupStepState.Done)
            .With(SetupStepId.VerifyRuntime, SetupStepState.Done);

        Assert.True(progress.IsComplete);
        Assert.Null(progress.CurrentStep);
        Assert.Equal(3, progress.CompletedCount);
    }

    // A gateway address that stops working invalidates the enrolment that
    // depended on it, so later steps must not keep looking finished.
    [Fact]
    public void RegressingAnEarlierStepRelocksEverythingAfterIt()
    {
        var progress = SetupProgress.Initial()
            .With(SetupStepId.GatewayAddress, SetupStepState.Done)
            .With(SetupStepId.EnrolDevice, SetupStepState.Done)
            .With(SetupStepId.VerifyRuntime, SetupStepState.Done);
        Assert.True(progress.IsComplete);

        var broken = progress.With(SetupStepId.GatewayAddress, SetupStepState.Failed, "unreachable");

        Assert.False(broken.IsComplete);
        Assert.Equal(SetupStepState.Blocked, broken[SetupStepId.EnrolDevice].State);
        Assert.Equal(SetupStepState.Blocked, broken[SetupStepId.VerifyRuntime].State);
    }

    [Fact]
    public void RegressionAlsoClearsStaleDetail()
    {
        var progress = SetupProgress.Initial()
            .With(SetupStepId.GatewayAddress, SetupStepState.Done)
            .With(SetupStepId.EnrolDevice, SetupStepState.Done, "enrolled as desktop-1");

        var broken = progress.With(SetupStepId.GatewayAddress, SetupStepState.Failed);
        Assert.Equal("", broken[SetupStepId.EnrolDevice].Detail);
    }

    [Fact]
    public void AFailedStepIsRetryable()
    {
        var progress = SetupProgress.Initial()
            .With(SetupStepId.GatewayAddress, SetupStepState.Failed, "unreachable");

        Assert.True(progress[SetupStepId.GatewayAddress].CanAttempt);
    }

    [Fact]
    public void AWorkingStepIsNotReAttemptable()
    {
        var progress = SetupProgress.Initial()
            .With(SetupStepId.GatewayAddress, SetupStepState.Working);
        Assert.False(progress[SetupStepId.GatewayAddress].CanAttempt);
    }

    [Fact]
    public void CurrentStepPointsAtTheFirstUnfinishedOne()
    {
        var progress = SetupProgress.Initial()
            .With(SetupStepId.GatewayAddress, SetupStepState.Done);
        Assert.Equal(SetupStepId.EnrolDevice, progress.CurrentStep!.Id);
    }

    [Theory]
    [InlineData(SetupStepState.Done, PresenceTone.Positive)]
    [InlineData(SetupStepState.Working, PresenceTone.Pending)]
    [InlineData(SetupStepState.Failed, PresenceTone.Critical)]
    [InlineData(SetupStepState.Ready, PresenceTone.Attention)]
    [InlineData(SetupStepState.Blocked, PresenceTone.Neutral)]
    public void ToneMatchesTheSharedSemanticPalette(SetupStepState state, PresenceTone expected)
    {
        var step = SetupProgress.Initial()[SetupStepId.GatewayAddress] with { State = state };
        Assert.Equal(expected, step.Tone);
    }

    [Fact]
    public void CompletedStepsShowATickRatherThanANumber()
    {
        var step = SetupProgress.Initial()[SetupStepId.GatewayAddress];
        Assert.Equal("1", step.Marker(1));
        Assert.Equal("✓", (step with { State = SetupStepState.Done }).Marker(1));
    }

    [Fact]
    public void AnUnknownStepIsRejected()
    {
        Assert.Throws<ArgumentOutOfRangeException>(
            () => SetupProgress.Initial().With((SetupStepId)99, SetupStepState.Done));
    }

    [Fact]
    public void ProgressIsImmutable()
    {
        var original = SetupProgress.Initial();
        var updated = original.With(SetupStepId.GatewayAddress, SetupStepState.Done);

        Assert.Equal(SetupStepState.Ready, original[SetupStepId.GatewayAddress].State);
        Assert.Equal(SetupStepState.Done, updated[SetupStepId.GatewayAddress].State);
    }
}
