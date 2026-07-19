using Sentry.Contracts;

namespace Sentry.Contracts.Tests;

public class ConnectionsTests
{
    // --- harness classification ---------------------------------------------

    // The finding that shaped this whole surface: both harnesses on this machine
    // authenticate with a subscription, so neither yields anything a runtime can
    // be configured with. Reporting them as connected would send someone looking
    // for a key that does not exist.
    [Fact]
    public void ASubscriptionHarnessIsNotReportedAsAUsableCredential()
    {
        var claude = ConnectionRules.ForHarness(new HarnessObservation(
            "Claude Code", BinaryOnPath: true, ConfigPresent: true,
            CredentialKind.OAuthSubscription, "C:/Users/x/.local/bin/claude.exe"));

        Assert.Equal(IntegrationState.Detected, claude.State);
        Assert.NotEqual(IntegrationState.Connected, claude.State);
        Assert.Contains("subscription", claude.Detail, StringComparison.OrdinalIgnoreCase);
        Assert.Contains("API key", claude.Remedy, StringComparison.OrdinalIgnoreCase);
    }

    // A working subscription harness is not a fault, so it must not be counted
    // among the things demanding action.
    [Fact]
    public void ADetectedHarnessDoesNotDemandAttention()
    {
        var claude = ConnectionRules.ForHarness(new HarnessObservation(
            "Claude Code", true, true, CredentialKind.OAuthSubscription));

        Assert.False(claude.NeedsAttention);
    }

    // The live Codex case: config.toml and auth.json on disk, no binary anywhere.
    [Fact]
    public void ConfigurationWithoutABinaryIsMisconfiguredNotMissing()
    {
        var codex = ConnectionRules.ForHarness(new HarnessObservation(
            "Codex", BinaryOnPath: false, ConfigPresent: true,
            CredentialKind.OAuthSubscription));

        Assert.Equal(IntegrationState.Misconfigured, codex.State);
        Assert.True(codex.NeedsAttention);
        Assert.Contains("not installed", codex.Detail, StringComparison.OrdinalIgnoreCase);
        Assert.Contains("Install", codex.Remedy);
    }

    [Fact]
    public void NothingOnDiskAtAllIsSimplyMissing()
    {
        var grok = ConnectionRules.ForHarness(new HarnessObservation(
            "Grok Build", BinaryOnPath: false, ConfigPresent: false, CredentialKind.None));

        Assert.Equal(IntegrationState.Missing, grok.State);
    }

    // These two must never collapse into each other: one needs installing, the
    // other needs fixing, and they look identical from a distance.
    [Fact]
    public void MissingAndMisconfiguredAreDistinguishable()
    {
        var missing = ConnectionRules.ForHarness(
            new HarnessObservation("A", false, false, CredentialKind.None));
        var broken = ConnectionRules.ForHarness(
            new HarnessObservation("B", false, true, CredentialKind.None));

        Assert.NotEqual(missing.State, broken.State);
        Assert.NotEqual(missing.Label, broken.Label);
        Assert.NotEqual(missing.Tone, broken.Tone);
    }

    [Fact]
    public void AnApiKeyHarnessIsUsable()
    {
        var harness = ConnectionRules.ForHarness(new HarnessObservation(
            "Some CLI", true, true, CredentialKind.ApiKey, "C:/bin/some.exe"));

        Assert.Equal(IntegrationState.Connected, harness.State);
        Assert.False(harness.HasRemedy);
    }

    [Fact]
    public void AnInstalledHarnessWithNoCredentialsIsMisconfigured()
    {
        var harness = ConnectionRules.ForHarness(new HarnessObservation(
            "Claude Code", BinaryOnPath: true, ConfigPresent: false, CredentialKind.None));

        Assert.Equal(IntegrationState.Misconfigured, harness.State);
        Assert.Contains("Sign in", harness.Remedy);
    }

    // --- the SSH forward -----------------------------------------------------

    // Nothing supervises the forward. Without this distinction a forward that is
    // not running looks exactly like a gateway that is down.
    [Fact]
    public void AnAbsentTunnelIsReportedSeparatelyFromTheGateway()
    {
        var tunnel = ConnectionRules.ForTunnel(
            "http://localhost:18090", gatewayIsLoopback: true, localPort: 18090,
            listenerPresent: false, gatewayAnswered: false, "bishop@203.0.113.9");

        Assert.Equal(IntegrationState.Missing, tunnel.State);
        Assert.Contains("18090", tunnel.Detail);
        Assert.Contains("ssh -N -L 18090:127.0.0.1:8090 bishop@203.0.113.9", tunnel.Remedy);
    }

    [Fact]
    public void AListeningPortThatDoesNotReachTheGatewayIsMisconfigured()
    {
        var tunnel = ConnectionRules.ForTunnel(
            "http://localhost:18090", true, 18090,
            listenerPresent: true, gatewayAnswered: false, "bishop@203.0.113.9");

        Assert.Equal(IntegrationState.Misconfigured, tunnel.State);
        Assert.Contains("Restart", tunnel.Remedy);
    }

    [Fact]
    public void AWorkingTunnelNeedsNoRemedy()
    {
        var tunnel = ConnectionRules.ForTunnel(
            "http://localhost:18090", true, 18090,
            listenerPresent: true, gatewayAnswered: true, "bishop@203.0.113.9");

        Assert.Equal(IntegrationState.Connected, tunnel.State);
        Assert.False(tunnel.HasRemedy);
    }

    // A gateway on a real hostname needs no forward, and nagging about one would
    // be noise.
    [Fact]
    public void ADirectlyReachableGatewayNeedsNoTunnelRow()
    {
        var tunnel = ConnectionRules.ForTunnel(
            "https://sentry.example.com", gatewayIsLoopback: false, 18090,
            false, true, "bishop@203.0.113.9");

        Assert.Equal(IntegrationState.NotApplicable, tunnel.State);
        Assert.False(tunnel.NeedsAttention);
    }

    // --- the descriptor contract --------------------------------------------

    // Enforced by construction: the factories for states needing action all
    // require a remedy, so a row cannot say "broken" without saying "do this".
    [Fact]
    public void EveryStateNeedingActionCarriesARemedy()
    {
        ConnectionDescriptor[] rows =
        [
            ConnectionRules.ForHarness(new HarnessObservation("A", false, true, CredentialKind.None)),
            ConnectionRules.ForHarness(new HarnessObservation("B", false, false, CredentialKind.None)),
            ConnectionRules.ForHarness(new HarnessObservation("C", true, true, CredentialKind.None)),
            ConnectionRules.ForTunnel("http://localhost:1", true, 1, false, false, "u@h"),
            ConnectionRules.ForTunnel("http://localhost:1", true, 1, true, false, "u@h"),
        ];

        foreach (var row in rows)
        {
            Assert.True(row.HasRemedy, $"{row.Name} is {row.Label} with no remedy");
        }
    }

    [Fact]
    public void AConnectedRowCannotClaimARemedy()
    {
        var row = ConnectionDescriptor.Working("X", "Runtime", "Answering.");
        Assert.False(row.HasRemedy);
        Assert.False(row.NeedsAttention);
    }

    [Theory]
    [InlineData(IntegrationState.Connected, "CONNECTED")]
    [InlineData(IntegrationState.Detected, "DETECTED")]
    [InlineData(IntegrationState.Missing, "MISSING")]
    [InlineData(IntegrationState.Misconfigured, "MISCONFIGURED")]
    [InlineData(IntegrationState.NotApplicable, "N/A")]
    public void EveryStateHasItsOwnLabel(IntegrationState state, string expected)
    {
        var row = ConnectionDescriptor.Working("X", "Y", "Z") with { State = state };
        Assert.Equal(expected, row.Label);
    }

    // --- the inventory -------------------------------------------------------

    // This is the live shape of this machine: Codex misconfigured, Grok absent.
    // The summary must account for both — a sentence that reads as a full tally
    // while dropping a category is worse than a longer one.
    [Fact]
    public void TheSummaryLeadsWithWhatIsBrokenWithoutDroppingWhatIsMissing()
    {
        var inventory = new ConnectionInventory([
            ConnectionDescriptor.Working("Gateway", "Control plane", "Online."),
            ConnectionDescriptor.Broken("Codex", "Coding harness", "No binary.", "Install it."),
            ConnectionDescriptor.Absent("Grok", "Coding harness", "Absent.", "Install it."),
        ]);

        Assert.Equal(
            "1 connection needs fixing, and 1 more is not set up.", inventory.Summary);
        Assert.Equal(2, inventory.NeedingAttention);
    }

    [Fact]
    public void TheCombinedSummaryAgreesWithBothCounts()
    {
        var inventory = new ConnectionInventory([
            ConnectionDescriptor.Broken("A", "c", "d", "r"),
            ConnectionDescriptor.Broken("B", "c", "d", "r"),
            ConnectionDescriptor.Absent("C", "c", "d", "r"),
            ConnectionDescriptor.Absent("D", "c", "d", "r"),
        ]);

        Assert.Equal(
            "2 connections need fixing, and 2 more are not set up.", inventory.Summary);
    }

    // The verb agrees with the count. An earlier surface in this app shipped
    // "1 of 2 item needs", so this is worth pinning down.
    [Fact]
    public void TheSummaryAgreesWithItsCount()
    {
        var two = new ConnectionInventory([
            ConnectionDescriptor.Broken("A", "c", "d", "r"),
            ConnectionDescriptor.Broken("B", "c", "d", "r"),
        ]);
        Assert.Equal("2 connections need fixing.", two.Summary);

        var oneMissing = new ConnectionInventory([
            ConnectionDescriptor.Absent("A", "c", "d", "r"),
        ]);
        Assert.Equal("1 connection is not set up.", oneMissing.Summary);

        var twoMissing = new ConnectionInventory([
            ConnectionDescriptor.Absent("A", "c", "d", "r"),
            ConnectionDescriptor.Absent("B", "c", "d", "r"),
        ]);
        Assert.Equal("2 connections are not set up.", twoMissing.Summary);
    }

    [Fact]
    public void AnAllPresentInventorySaysSo()
    {
        var inventory = new ConnectionInventory([
            ConnectionDescriptor.Working("Gateway", "Control plane", "Online."),
            ConnectionDescriptor.Found("Claude Code", "Coding harness", "Subscription.", "Provision a key."),
            ConnectionDescriptor.Irrelevant("SSH tunnel", "Transport", "Not needed."),
        ]);

        Assert.Equal(0, inventory.NeedingAttention);
        Assert.Equal("Everything Sentry depends on is present.", inventory.Summary);
    }

    // Before any probe runs, the surface must not imply a clean bill of health.
    [Fact]
    public void AnUncheckedInventoryClaimsNothing()
    {
        Assert.Equal("Nothing has been checked yet.", ConnectionInventory.Empty.Summary);
        Assert.Equal(0, ConnectionInventory.Empty.NeedingAttention);
    }

    [Fact]
    public void CategoriesKeepProbeOrderRatherThanSorting()
    {
        var inventory = new ConnectionInventory([
            ConnectionDescriptor.Working("Gateway", "Control plane", "Online."),
            ConnectionDescriptor.Absent("Codex", "Coding harness", "Absent.", "Install."),
            ConnectionDescriptor.Working("Postgres", "Control plane", "Online."),
        ]);

        Assert.Equal(
            ["Control plane", "Coding harness"],
            inventory.ByCategory.Select(g => g.Key).ToArray());
        Assert.Equal(2, inventory.ByCategory[0].Count());
    }
}
