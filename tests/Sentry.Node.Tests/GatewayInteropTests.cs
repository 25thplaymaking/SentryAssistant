using Sentry.Node.Security;

namespace Sentry.Node.Tests;

/// <summary>
/// Proves the C# node accepts a work order actually signed by the Python Gateway.
///
/// Both halves are independently unit tested, but that does not prove they agree
/// on the wire format. This closed a real defect: the JWT handler remaps standard
/// claim names onto legacy URIs by default, so the node parsed its own tokens
/// happily while every Gateway-signed token would have failed.
///
/// Driven by scripts/verify-gateway-interop.ps1, which signs with the real Python
/// code and passes the token through the environment. Skipped when unset so the
/// ordinary test run stays self-contained.
/// </summary>
public class GatewayInteropTests
{
    private static string? Token => Environment.GetEnvironmentVariable("SENTRY_INTEROP_TOKEN");
    private static string? Key => Environment.GetEnvironmentVariable("SENTRY_INTEROP_KEY");

    private static NodeExpectation Expectation() => new(
        NodeId: "node-interop",
        NodeOwnerUserId: "bryce",
        RegisteredWorkspaces: new HashSet<string> { "ws-sentry" },
        AllowedHarnesses: new HashSet<string> { "codex" },
        TeamMembers: new HashSet<string> { "bryce" });

    [Fact]
    public void NodeAcceptsATokenSignedByThePythonGateway()
    {
        Assert.SkipWhen(
            string.IsNullOrEmpty(Token) || string.IsNullOrEmpty(Key),
            "Set SENTRY_INTEROP_TOKEN and SENTRY_INTEROP_KEY, or run scripts/verify-gateway-interop.ps1.");

        var order = new WorkOrderValidator(Key!, Expectation()).Validate(Token!);

        Assert.Equal("ws-sentry", order.WorkspaceId);
        Assert.Equal("codex", order.Harness);
        Assert.Equal("bryce", order.RequestingUserId);
        Assert.False(string.IsNullOrWhiteSpace(order.WorkOrderId));
        Assert.False(string.IsNullOrWhiteSpace(order.Nonce));
        Assert.False(string.IsNullOrWhiteSpace(order.CorrelationId));
    }
}
