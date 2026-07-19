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

    // --- verifying the runtime ----------------------------------------------

    [Fact]
    public void AConfirmedRuntimeCompletesTheStep()
    {
        var verification = new RuntimeVerification(RuntimeCheck.Confirmed, "hermes 0.18.2");

        Assert.Equal(SetupStepState.Done, verification.State);
        Assert.True(verification.IsConfirmed);
        Assert.Contains("confirmed able to answer", verification.Detail);
    }

    // A runtime can pass every health check and still have no model behind it,
    // which only shows up when the first question fails.
    [Fact]
    public void ARuntimeWithNoProviderFailsAndNamesWhy()
    {
        var verification = new RuntimeVerification(RuntimeCheck.NoProvider, "hermes 0.18.2");

        Assert.Equal(SetupStepState.Failed, verification.State);
        Assert.False(verification.IsConfirmed);
        Assert.Contains("no inference provider", verification.Detail);
    }

    [Fact]
    public void AnUnreachableRuntimeFails()
    {
        var verification = new RuntimeVerification(RuntimeCheck.Unreachable, "Gateway unreachable");

        Assert.Equal(SetupStepState.Failed, verification.State);
        Assert.False(verification.IsConfirmed);
    }

    // The bug this replaces: a non-administrator got no snapshot back, the check
    // fell through, and the step reported Done as though something had verified
    // the runtime could answer. Setup may still finish — there is nothing more
    // this person can do — but it must not imply a check that never ran.
    [Fact]
    public void AnUnconfirmableRuntimeCompletesButSaysItWasNotConfirmed()
    {
        var verification = new RuntimeVerification(RuntimeCheck.Unconfirmed, "hermes 0.18.2");

        Assert.Equal(SetupStepState.Done, verification.State);
        Assert.False(verification.IsConfirmed);
        Assert.Contains("not confirmed", verification.Detail);
        Assert.Contains("administrator", verification.Detail);
    }

    [Fact]
    public void AnUnconfirmedRuntimeNeverClaimsItCanAnswer()
    {
        var unconfirmed = new RuntimeVerification(RuntimeCheck.Unconfirmed, "hermes 0.18.2");
        var confirmed = new RuntimeVerification(RuntimeCheck.Confirmed, "hermes 0.18.2");

        Assert.DoesNotContain("confirmed able to answer", unconfirmed.Detail);
        Assert.Contains("confirmed able to answer", confirmed.Detail);
        Assert.NotEqual(confirmed.Detail, unconfirmed.Detail);
    }

    [Fact]
    public void EveryOutcomeProducesADetailWorthReading()
    {
        foreach (var outcome in Enum.GetValues<RuntimeCheck>())
        {
            Assert.NotEmpty(new RuntimeVerification(outcome).Detail);
        }
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
