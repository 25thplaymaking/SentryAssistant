using Sentry.Node.Gateway;

namespace Sentry.Node.Tests;

public class ReconnectBackoffTests
{
    [Fact]
    public void GrowsExponentially()
    {
        var backoff = new ReconnectBackoff(TimeSpan.FromSeconds(1), TimeSpan.FromMinutes(5));

        Assert.Equal(TimeSpan.FromSeconds(1), backoff.NextDelay());
        Assert.Equal(TimeSpan.FromSeconds(2), backoff.NextDelay());
        Assert.Equal(TimeSpan.FromSeconds(4), backoff.NextDelay());
        Assert.Equal(TimeSpan.FromSeconds(8), backoff.NextDelay());
    }

    [Fact]
    public void IsCappedSoALongOutageDoesNotStrandTheNode()
    {
        var backoff = new ReconnectBackoff(TimeSpan.FromSeconds(1), TimeSpan.FromSeconds(30));
        for (var i = 0; i < 20; i++) backoff.NextDelay();
        Assert.Equal(TimeSpan.FromSeconds(30), backoff.NextDelay());
    }

    // A very long outage must not overflow into a negative or absurd delay.
    [Fact]
    public void SurvivesAVeryLongOutageWithoutOverflow()
    {
        var backoff = new ReconnectBackoff(TimeSpan.FromSeconds(2), TimeSpan.FromMinutes(2));
        for (var i = 0; i < 10_000; i++)
        {
            var delay = backoff.NextDelay();
            Assert.True(delay > TimeSpan.Zero, $"delay went non-positive at attempt {i}");
            Assert.True(delay <= TimeSpan.FromMinutes(2), $"delay exceeded the cap at attempt {i}");
        }
    }

    [Fact]
    public void ResetsAfterASuccessfulConnection()
    {
        var backoff = new ReconnectBackoff(TimeSpan.FromSeconds(1), TimeSpan.FromMinutes(1));
        backoff.NextDelay();
        backoff.NextDelay();
        Assert.Equal(2, backoff.Failures);

        backoff.Reset();
        Assert.Equal(0, backoff.Failures);
        Assert.Equal(TimeSpan.FromSeconds(1), backoff.NextDelay());
    }
}
