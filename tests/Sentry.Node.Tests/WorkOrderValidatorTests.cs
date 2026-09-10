using System.IdentityModel.Tokens.Jwt;
using System.Security.Claims;
using System.Text;
using Microsoft.IdentityModel.Tokens;
using Sentry.Node.Security;

namespace Sentry.Node.Tests;

public class WorkOrderValidatorTests
{
    private const string Key = "node-test-signing-key-padded-past-the-32-byte-minimum";

    private static NodeExpectation Expectation() => new(
        NodeId: "node-1",
        NodeOwnerUserId: "bryce",
        RegisteredWorkspaces: new HashSet<string> { "ws-sentry", "ws-25vid" },
        AllowedHarnesses: new HashSet<string> { "codex", "claude", "integrations" },
        TeamMembers: new HashSet<string> { "bryce", "colleague" });

    /// <summary>Mirrors what the Gateway's sign_work_order produces.</summary>
    private static string Sign(
        string signingKey = Key,
        string user = "bryce",
        string nodeId = "node-1",
        string workspace = "ws-sentry",
        string harness = "codex",
        string mode = "readOnly",
        string? teamId = null,
        string? runtimeSessionId = null,
        string? runtimeModel = null,
        string? runtimeOptions = null,
        string? inputImagesDigest = null,
        int lifetimeMinutes = 15,
        string? nonce = null)
    {
        var credentials = new SigningCredentials(
            new SymmetricSecurityKey(Encoding.UTF8.GetBytes(signingKey)),
            SecurityAlgorithms.HmacSha256);

        var claims = new List<Claim>
        {
            new(JwtRegisteredClaimNames.Sub, user),
            new("wid", "wo-1"),
            new("pid", "p-1"),
            new("nid", nodeId),
            new("wsp", workspace),
            new("hns", harness),
            new("mode", mode),
            new("cid", "corr-1"),
            new("nonce", nonce ?? Guid.NewGuid().ToString("N"))
        };
        if (teamId is not null) claims.Add(new Claim("tid", teamId));
        if (runtimeSessionId is not null) claims.Add(new Claim("rsid", runtimeSessionId));
        if (runtimeModel is not null) claims.Add(new Claim("rmodel", runtimeModel));
        if (runtimeOptions is not null) claims.Add(new Claim("ropts", runtimeOptions));
        if (inputImagesDigest is not null) claims.Add(new Claim("imgsha", inputImagesDigest));

        // notBefore is derived from expiry so a negative lifetime still produces a
        // structurally valid (but expired) token rather than failing construction.
        var expires = DateTime.UtcNow.AddMinutes(lifetimeMinutes);
        var token = new JwtSecurityToken(
            issuer: WorkOrderValidator.Issuer,
            audience: WorkOrderValidator.WorkOrderAudience,
            claims: claims,
            notBefore: expires.AddMinutes(-30),
            expires: expires,
            signingCredentials: credentials);

        return new JwtSecurityTokenHandler().WriteToken(token);
    }

    [Fact]
    public void AcceptsAWellFormedOrder()
    {
        var order = new WorkOrderValidator(Key, Expectation()).Validate(Sign());
        Assert.Equal("wo-1", order.WorkOrderId);
        Assert.Equal("ws-sentry", order.WorkspaceId);
        Assert.Equal("codex", order.Harness);
    }

    [Fact]
    public void PreservesSignedNativeRuntimeIdentity()
    {
        var order = new WorkOrderValidator(Key, Expectation()).Validate(Sign(
            runtimeSessionId: "sentry-session-1",
            runtimeModel: "gpt-5.6-sol"));
        Assert.Equal("sentry-session-1", order.RuntimeSessionId);
        Assert.Equal("gpt-5.6-sol", order.RuntimeModel);
    }

    [Fact]
    public void PreservesAndValidatesSignedNativeRuntimeOptions()
    {
        var order = new WorkOrderValidator(Key, Expectation()).Validate(Sign(
            mode: "readOnly",
            runtimeOptions: """{"action":"review","collaboration_mode":"plan","effort":"high","personality":"friendly","approval_policy":"untrusted","sandbox":"readOnly","review_target":"uncommittedChanges"}"""));

        Assert.NotNull(order.RuntimeOptions);
        Assert.Equal("review", order.RuntimeOptions.Action);
        Assert.Equal("plan", order.RuntimeOptions.CollaborationMode);
        Assert.Equal("high", order.RuntimeOptions.Effort);
        Assert.Equal("readOnly", order.RuntimeOptions.Sandbox);
    }

    [Fact]
    public void AcceptsAConstrainedIntegrationSessionRead()
    {
        var order = new WorkOrderValidator(Key, Expectation()).Validate(Sign(
            harness: "integrations",
            mode: "readOnly",
            runtimeOptions: """{"action":"sessionRead","sandbox":"readOnly","provider":"codex","provider_session_id":"11111111-2222-3333-4444-555555555555"}"""));

        Assert.Equal("integrations", order.Harness);
        Assert.Equal("sessionRead", order.RuntimeOptions!.Action);
        Assert.Equal("codex", order.RuntimeOptions.Provider);
    }

    [Fact]
    public void RefusesIntegrationWritesAndUnknownProviderSessions()
    {
        Assert.Throws<WorkOrderRejectedException>(() =>
            new WorkOrderValidator(Key, Expectation()).Validate(Sign(
                harness: "integrations",
                mode: "workspaceWrite",
                runtimeOptions: """{"action":"workspaceInspect","sandbox":"workspaceWrite"}""")));
        Assert.Throws<WorkOrderRejectedException>(() =>
            new WorkOrderValidator(Key, Expectation()).Validate(Sign(
                harness: "integrations",
                mode: "readOnly",
                runtimeOptions: """{"action":"sessionRead","sandbox":"readOnly","provider":"other","provider_session_id":"x"}""")));
    }

    [Fact]
    public void PreservesSignedImageInputDigest()
    {
        var digest = new string('a', 64);
        var order = new WorkOrderValidator(Key, Expectation()).Validate(Sign(inputImagesDigest: digest));
        Assert.Equal(digest, order.InputImagesDigest);
    }

    [Fact]
    public void RejectsNativeSandboxThatDoesNotMatchTheSignedMode()
    {
        var exception = Assert.Throws<WorkOrderRejectedException>(
            () => new WorkOrderValidator(Key, Expectation()).Validate(Sign(
                mode: "readOnly",
                runtimeOptions: """{"action":"turn","collaboration_mode":"default","personality":"pragmatic","approval_policy":"on-request","sandbox":"workspaceWrite","review_target":"uncommittedChanges"}""")));

        Assert.Contains("does not match", exception.Message);
    }

    [Fact]
    public void RejectsAForgedSignature()
    {
        var forged = Sign(signingKey: "attacker-key-also-padded-past-the-32-byte-minimum");
        var exception = Assert.Throws<WorkOrderRejectedException>(
            () => new WorkOrderValidator(Key, Expectation()).Validate(forged));
        Assert.Contains("signature is invalid", exception.Message);
    }

    [Fact]
    public void RejectsAnExpiredOrder()
    {
        var expired = Sign(lifetimeMinutes: -5);
        var exception = Assert.Throws<WorkOrderRejectedException>(
            () => new WorkOrderValidator(Key, Expectation()).Validate(expired));
        Assert.Contains("expired", exception.Message);
    }

    [Fact]
    public void RejectsAReplayedNonce()
    {
        var validator = new WorkOrderValidator(Key, Expectation());
        var token = Sign(nonce: "fixed-nonce");
        validator.Validate(token);

        var exception = Assert.Throws<WorkOrderRejectedException>(() => validator.Validate(token));
        Assert.Contains("nonce has already been used", exception.Message);
    }

    // A valid signature is necessary but never sufficient: the node re-checks
    // everything it knows locally.
    [Fact]
    public void RejectsAnOrderAimedAtAnotherNode()
    {
        var exception = Assert.Throws<WorkOrderRejectedException>(
            () => new WorkOrderValidator(Key, Expectation()).Validate(Sign(nodeId: "node-2")));
        Assert.Contains("different execution node", exception.Message);
    }

    [Fact]
    public void RejectsAnUnregisteredWorkspace()
    {
        var exception = Assert.Throws<WorkOrderRejectedException>(
            () => new WorkOrderValidator(Key, Expectation())
                .Validate(Sign(workspace: @"C:\Users\Bryce\.ssh")));
        Assert.Contains("Raw remote paths are refused", exception.Message);
    }

    [Fact]
    public void RejectsADisabledHarness()
    {
        var exception = Assert.Throws<WorkOrderRejectedException>(
            () => new WorkOrderValidator(Key, Expectation()).Validate(Sign(harness: "grok-build")));
        Assert.Contains("not enabled", exception.Message);
    }

    [Fact]
    public void RejectsElevatedWorkFromAnyoneButTheNodeOwner()
    {
        var exception = Assert.Throws<WorkOrderRejectedException>(
            () => new WorkOrderValidator(Key, Expectation())
                .Validate(Sign(user: "colleague", mode: "approvedElevated")));
        Assert.Contains("node owner", exception.Message);
    }

    [Fact]
    public void AcceptsElevatedWorkFromTheNodeOwner()
    {
        var order = new WorkOrderValidator(Key, Expectation())
            .Validate(Sign(user: "bryce", mode: "approvedElevated"));
        Assert.Equal("approvedElevated", order.Mode);
    }

    [Fact]
    public void RejectsTeamWorkFromARemovedMember()
    {
        var exception = Assert.Throws<WorkOrderRejectedException>(
            () => new WorkOrderValidator(Key, Expectation())
                .Validate(Sign(user: "ex-colleague", teamId: "team-1")));
        Assert.Contains("not a current member", exception.Message);
    }

    [Fact]
    public void AcceptsTeamWorkFromACurrentMember()
    {
        var order = new WorkOrderValidator(Key, Expectation())
            .Validate(Sign(user: "colleague", teamId: "team-1"));
        Assert.Equal("team-1", order.TeamId);
    }

    [Fact]
    public void RejectsAnUnknownMode()
    {
        var exception = Assert.Throws<WorkOrderRejectedException>(
            () => new WorkOrderValidator(Key, Expectation()).Validate(Sign(mode: "rootAccess")));
        Assert.Contains("mode is unknown", exception.Message);
    }

    [Fact]
    public void RejectsAWeakSigningKeyAtConstruction()
    {
        Assert.Throws<ArgumentException>(() => new WorkOrderValidator("too-short", Expectation()));
    }
}
