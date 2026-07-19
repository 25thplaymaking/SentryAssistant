using Sentry.Contracts;

namespace Sentry.Contracts.Tests;

public class DeviceSessionTests
{
    private const string Address = "http://localhost:18090";

    [Fact]
    public void AFirstRunOpensTheWalkthroughAtTheStart()
    {
        var progress = new SessionRestore(SessionRestoreOutcome.NoGatewayConfigured)
            .ToProgress("");

        Assert.Equal(SetupStepState.Ready, progress[SetupStepId.GatewayAddress].State);
        Assert.Equal(0, progress.CompletedCount);
    }

    // The whole point of persisting a credential: a restart should not send
    // someone back through enrolment.
    [Fact]
    public void ARestoredSessionSkipsStraightPastEnrolment()
    {
        var progress = new SessionRestore(SessionRestoreOutcome.Restored).ToProgress(Address);

        Assert.Equal(SetupStepState.Done, progress[SetupStepId.GatewayAddress].State);
        Assert.Equal(SetupStepState.Done, progress[SetupStepId.EnrolDevice].State);
        Assert.Equal(SetupStepState.Ready, progress[SetupStepId.VerifyRuntime].State);
    }

    [Fact]
    public void ASavedAddressWithoutCredentialsStopsAtEnrolment()
    {
        var progress = new SessionRestore(SessionRestoreOutcome.NoStoredCredential)
            .ToProgress(Address);

        Assert.Equal(SetupStepState.Done, progress[SetupStepId.GatewayAddress].State);
        Assert.Equal(SetupStepState.Ready, progress[SetupStepId.EnrolDevice].State);
        Assert.Equal(SetupStepId.EnrolDevice, progress.CurrentStep!.Id);
    }

    // The acceptance criterion for revocation: back to the walkthrough, and the
    // reason is on screen rather than left to be inferred from a failure later.
    [Fact]
    public void ARevokedDeviceReturnsToEnrolmentWithAReason()
    {
        var restore = new SessionRestore(SessionRestoreOutcome.Revoked);
        var progress = restore.ToProgress(Address);

        Assert.True(restore.RequiresReEnrolment);
        Assert.False(restore.IsAuthenticated);
        Assert.Equal(SetupStepState.Failed, progress[SetupStepId.EnrolDevice].State);
        Assert.True(progress[SetupStepId.EnrolDevice].CanAttempt);
        Assert.NotEmpty(progress[SetupStepId.EnrolDevice].Detail);
    }

    [Fact]
    public void ARevokedDeviceDoesNotClaimTheRuntimeWasVerified()
    {
        var progress = new SessionRestore(SessionRestoreOutcome.Revoked).ToProgress(Address);
        Assert.Equal(SetupStepState.Blocked, progress[SetupStepId.VerifyRuntime].State);
        Assert.False(progress.IsComplete);
    }

    // An unreachable gateway says nothing about the credential. Reporting it as a
    // credential failure would push someone to burn a fresh enrolment code to fix
    // a tunnel that was merely down.
    [Fact]
    public void AnUnreachableGatewayIsNotReportedAsACredentialProblem()
    {
        var restore = new SessionRestore(SessionRestoreOutcome.Unreachable);
        var progress = restore.ToProgress(Address);

        Assert.False(restore.RequiresReEnrolment);
        Assert.Equal(SetupStepState.Failed, progress[SetupStepId.GatewayAddress].State);
        Assert.Equal(SetupStepState.Blocked, progress[SetupStepId.EnrolDevice].State);
    }

    [Fact]
    public void AnUnreachableGatewayNamesTheAddressItTried()
    {
        var progress = new SessionRestore(SessionRestoreOutcome.Unreachable).ToProgress(Address);
        Assert.Contains(Address, progress[SetupStepId.GatewayAddress].Detail);
    }

    [Fact]
    public void ASuppliedDetailIsPreferredOverTheGenericOne()
    {
        var progress = new SessionRestore(
                SessionRestoreOutcome.Revoked, "This device was revoked on 12 July.")
            .ToProgress(Address);

        Assert.Equal(
            "This device was revoked on 12 July.",
            progress[SetupStepId.EnrolDevice].Detail);
    }

    [Fact]
    public void OnlyARestoredSessionCountsAsAuthenticated()
    {
        foreach (var outcome in Enum.GetValues<SessionRestoreOutcome>())
        {
            var restore = new SessionRestore(outcome);
            Assert.Equal(outcome is SessionRestoreOutcome.Restored, restore.IsAuthenticated);
        }
    }

    [Fact]
    public void TheSavedAddressIsVisibleWithoutOpeningSettings()
    {
        var progress = new SessionRestore(SessionRestoreOutcome.Restored).ToProgress(Address);
        Assert.Contains(Address, progress[SetupStepId.GatewayAddress].Detail);
    }
}
