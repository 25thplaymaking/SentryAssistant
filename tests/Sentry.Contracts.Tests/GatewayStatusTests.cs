using Sentry.Contracts;

namespace Sentry.Contracts.Tests;

public class GatewayStatusTests
{
    // The whole point of this type: nothing reads as ONLINE unless a probe said so.
    [Theory]
    [InlineData(ConnectionState.Unknown, "UNKNOWN")]
    [InlineData(ConnectionState.Offline, "OFFLINE")]
    [InlineData(ConnectionState.Degraded, "DEGRADED")]
    [InlineData(ConnectionState.NotConfigured, "NOT SET UP")]
    [InlineData(ConnectionState.Online, "ONLINE")]
    public void LabelReflectsTheObservedState(ConnectionState state, string expected)
    {
        Assert.Equal(expected, new ComponentStatus("x", state, "").Label);
    }

    [Fact]
    public void OnlyOnlineCountsAsHealthy()
    {
        foreach (var state in Enum.GetValues<ConnectionState>())
        {
            var status = new ComponentStatus("x", state, "");
            Assert.Equal(state == ConnectionState.Online, status.IsHealthy);
        }
    }

    [Fact]
    public void UnknownIsNeverHealthyOrReassuring()
    {
        var status = ComponentStatus.Unknown("Gateway");
        Assert.False(status.IsHealthy);
        Assert.Equal(PresenceTone.Neutral, status.Tone);
        Assert.NotEqual("ONLINE", status.Label);
    }

    // A component nobody has set up is not a failure, and must not read as one.
    [Fact]
    public void NotConfiguredIsDistinctFromOffline()
    {
        var absent = ComponentStatus.NotConfigured("Node", "not enrolled");
        var broken = new ComponentStatus("Node", ConnectionState.Offline, "unreachable");

        Assert.NotEqual(absent.Label, broken.Label);
        Assert.Equal(PresenceTone.Neutral, absent.Tone);
        Assert.Equal(PresenceTone.Critical, broken.Tone);
    }

    [Fact]
    public void DegradedUsesThePendingToneNotSuccess()
    {
        var status = new ComponentStatus("Runtime", ConnectionState.Degraded, "version drift");
        Assert.Equal(PresenceTone.Pending, status.Tone);
        Assert.False(status.IsHealthy);
    }

    public class Aggregate
    {
        private static ComponentStatus Online(string name) =>
            new(name, ConnectionState.Online, "ok");

        [Fact]
        public void UnknownStatusIsNotFullyOnline()
        {
            Assert.False(GatewayStatus.Unknown().FullyOnline);
        }

        [Fact]
        public void EveryComponentMustBeOnline()
        {
            var all = new GatewayStatus(
                Online("Gateway"), Online("Runtime"), Online("Node"), DateTimeOffset.Now);
            Assert.True(all.FullyOnline);

            var oneDown = all with
            {
                Runtime = new ComponentStatus("Runtime", ConnectionState.Degraded, "drift")
            };
            Assert.False(oneDown.FullyOnline);
        }

        [Fact]
        public void AnUnconfiguredNodeStopsFullyOnline()
        {
            var status = new GatewayStatus(
                Online("Gateway"),
                Online("Runtime"),
                ComponentStatus.NotConfigured("Node", "not enrolled"),
                DateTimeOffset.Now);

            // Gateway-backed actions must not be offered when nothing can execute.
            Assert.False(status.FullyOnline);
        }
    }
}
