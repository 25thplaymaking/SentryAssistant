namespace Sentry.Contracts;

/// <summary>
/// What happened when the desktop tried to resume a saved session at launch.
///
/// These are deliberately five distinct outcomes rather than a bool. "We have no
/// credential", "the credential was rejected", and "we could not ask" call for
/// three different things from the person, and collapsing them would send
/// someone to burn a fresh enrolment code over a gateway that was merely
/// unreachable.
/// </summary>
public enum SessionRestoreOutcome
{
    /// <summary>No gateway address has ever been saved. First run.</summary>
    NoGatewayConfigured,

    /// <summary>An address is saved, but this desktop has never enrolled.</summary>
    NoStoredCredential,

    /// <summary>The stored credential was accepted and rotated.</summary>
    Restored,

    /// <summary>
    /// The gateway rejected the credential. The device was revoked, or the token
    /// expired or was already used. Re-enrolment is the only way forward.
    /// </summary>
    Revoked,

    /// <summary>
    /// The gateway could not be reached, so the credential's validity is
    /// unknown. Explicitly not a failure of the credential.
    /// </summary>
    Unreachable
}

/// <summary>
/// The outcome of a restore attempt, and what the walkthrough should show
/// because of it.
/// </summary>
/// <remarks>
/// This is a pure function of the outcome so the launch path can be tested
/// without a gateway, a network, or a Windows credential store. The HTTP call
/// that produces the outcome lives in the desktop; the meaning of the outcome
/// lives here.
/// </remarks>
public sealed record SessionRestore(SessionRestoreOutcome Outcome, string Detail = "")
{
    /// <summary>Whether the desktop holds usable credentials after this attempt.</summary>
    public bool IsAuthenticated => Outcome is SessionRestoreOutcome.Restored;

    /// <summary>
    /// Whether the person has to redeem a new enrolment code. Only a rejection
    /// implies this — an unreachable gateway does not.
    /// </summary>
    public bool RequiresReEnrolment => Outcome is SessionRestoreOutcome.Revoked;

    /// <summary>
    /// Project a launch-time restore onto the setup walkthrough.
    ///
    /// The <paramref name="address"/> is echoed back into step one's detail so a
    /// saved gateway is visible without opening Settings.
    /// </summary>
    public SetupProgress ToProgress(string address)
    {
        var initial = SetupProgress.Initial();

        return Outcome switch
        {
            // Nothing saved: the walkthrough starts where it always starts.
            SessionRestoreOutcome.NoGatewayConfigured => initial,

            // The gateway did not answer, so step one — "confirm it answers" — is
            // the step that actually failed. Letting the state machine re-lock
            // what follows is more honest than reporting a credential problem we
            // never observed. The stored credential is untouched and is retried
            // on the next successful attempt.
            SessionRestoreOutcome.Unreachable => initial.With(
                SetupStepId.GatewayAddress, SetupStepState.Failed,
                string.IsNullOrWhiteSpace(Detail)
                    ? $"{address} did not answer."
                    : Detail),

            SessionRestoreOutcome.NoStoredCredential => initial
                .With(SetupStepId.GatewayAddress, SetupStepState.Done, $"Saved: {address}"),

            SessionRestoreOutcome.Revoked => initial
                .With(SetupStepId.GatewayAddress, SetupStepState.Done, $"Saved: {address}")
                .With(SetupStepId.EnrolDevice, SetupStepState.Failed,
                    string.IsNullOrWhiteSpace(Detail)
                        ? "Stored credentials were rejected. Redeem a new enrolment code."
                        : Detail),

            SessionRestoreOutcome.Restored => initial
                .With(SetupStepId.GatewayAddress, SetupStepState.Done, $"Saved: {address}")
                .With(SetupStepId.EnrolDevice, SetupStepState.Done,
                    string.IsNullOrWhiteSpace(Detail)
                        ? "Signed in with stored credentials."
                        : Detail),

            _ => initial
        };
    }
}
