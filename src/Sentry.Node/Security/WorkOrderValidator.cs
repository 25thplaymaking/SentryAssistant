using System.IdentityModel.Tokens.Jwt;
using System.Security.Claims;
using System.Text;
using Microsoft.IdentityModel.Tokens;
using Sentry.Node.Gateway;

namespace Sentry.Node.Security;

public sealed class WorkOrderRejectedException : Exception
{
    public WorkOrderRejectedException(string message) : base(message) { }
}

/// <summary>What this node knows locally and will insist a work order matches.</summary>
public sealed record NodeExpectation(
    string NodeId,
    string NodeOwnerUserId,
    IReadOnlySet<string> RegisteredWorkspaces,
    IReadOnlySet<string> AllowedHarnesses,
    IReadOnlySet<string> TeamMembers);

public sealed record ValidatedWorkOrder(
    string WorkOrderId,
    string RequestingUserId,
    string ProfileId,
    string? TeamId,
    string WorkspaceId,
    string Harness,
    string Mode,
    string CorrelationId,
    string Nonce,
    string? RuntimeSessionId,
    string? RuntimeModel,
    NativeRuntimeOptions? RuntimeOptions,
    string? InputImagesDigest);

/// <summary>
/// Validates a Gateway-signed work order before anything executes.
///
/// A valid signature is necessary but never sufficient. The node independently
/// re-checks audience, expiry, single-use nonce, node targeting, workspace
/// registration, harness enablement, team membership, and elevated-mode
/// ownership. If the Gateway were compromised it still could not make this node
/// run elevated work for someone who does not own it.
/// </summary>
public sealed class WorkOrderValidator
{
    public const string Issuer = "sentry-gateway";
    public const string WorkOrderAudience = "sentry.workorder";

    private readonly SymmetricSecurityKey _key;
    private readonly NodeExpectation _expectation;
    private readonly HashSet<string> _seenNonces = new(StringComparer.Ordinal);
    private readonly object _nonceLock = new();

    // MapInboundClaims must stay false. By default this handler rewrites standard
    // JWT claim names onto legacy schemas.xmlsoap.org URIs, so "sub" would arrive
    // as a URI and every lookup here would miss. The Gateway is Python and emits
    // standard names; this keeps both sides speaking the same claim set.
    private readonly JwtSecurityTokenHandler _handler = new() { MapInboundClaims = false };

    public WorkOrderValidator(string signingKey, NodeExpectation expectation)
    {
        if (Encoding.UTF8.GetByteCount(signingKey) < 32)
        {
            throw new ArgumentException(
                "Signing key must be at least 32 bytes.", nameof(signingKey));
        }

        _key = new SymmetricSecurityKey(Encoding.UTF8.GetBytes(signingKey));
        _expectation = expectation;
    }

    public ValidatedWorkOrder Validate(string token)
    {
        ClaimsPrincipal principal;
        try
        {
            principal = _handler.ValidateToken(
                token,
                new TokenValidationParameters
                {
                    ValidateIssuer = true,
                    ValidIssuer = Issuer,
                    ValidateAudience = true,
                    ValidAudience = WorkOrderAudience,
                    ValidateIssuerSigningKey = true,
                    IssuerSigningKey = _key,
                    ValidateLifetime = true,
                    // Work orders are short lived on purpose; do not widen this.
                    ClockSkew = TimeSpan.FromSeconds(30)
                },
                out _);
        }
        catch (SecurityTokenExpiredException)
        {
            throw new WorkOrderRejectedException("Work order has expired.");
        }
        catch (SecurityTokenInvalidAudienceException)
        {
            throw new WorkOrderRejectedException(
                "Work order audience is not accepted by this node.");
        }
        catch (SecurityTokenException exception)
        {
            throw new WorkOrderRejectedException(
                $"Work order signature is invalid: {exception.Message}");
        }

        string Claim(string type) =>
            principal.FindFirst(type)?.Value
            ?? throw new WorkOrderRejectedException($"Work order is missing '{type}'.");

        var nonce = Claim("nonce");
        lock (_nonceLock)
        {
            if (!_seenNonces.Add(nonce))
            {
                throw new WorkOrderRejectedException(
                    "Work order nonce has already been used.");
            }
        }

        var nodeId = Claim("nid");
        if (!string.Equals(nodeId, _expectation.NodeId, StringComparison.Ordinal))
        {
            throw new WorkOrderRejectedException(
                "Work order targets a different execution node.");
        }

        var workspaceId = Claim("wsp");
        if (!_expectation.RegisteredWorkspaces.Contains(workspaceId))
        {
            throw new WorkOrderRejectedException(
                "Workspace is not registered on this node. Raw remote paths are refused.");
        }

        var harness = Claim("hns");
        if (!_expectation.AllowedHarnesses.Contains(harness))
        {
            throw new WorkOrderRejectedException(
                $"Harness '{harness}' is not enabled on this node.");
        }

        var mode = Claim("mode");
        if (mode is not ("readOnly" or "workspaceWrite" or "approvedElevated"))
        {
            throw new WorkOrderRejectedException("Work order mode is unknown.");
        }

        var requestingUser = principal.FindFirst(JwtRegisteredClaimNames.Sub)?.Value
            ?? throw new WorkOrderRejectedException("Work order is missing 'sub'.");

        // Elevated work is only ever accepted from this node's own owner,
        // whatever the Gateway signed.
        if (mode == "approvedElevated" &&
            !string.Equals(requestingUser, _expectation.NodeOwnerUserId, StringComparison.Ordinal))
        {
            throw new WorkOrderRejectedException(
                "Elevated work orders are only accepted from the node owner.");
        }

        var teamId = principal.FindFirst("tid")?.Value;
        if (!string.IsNullOrEmpty(teamId) && !_expectation.TeamMembers.Contains(requestingUser))
        {
            throw new WorkOrderRejectedException(
                "Requesting user is not a current member of this team.");
        }

        NativeRuntimeOptions? runtimeOptions = null;
        var runtimeOptionsClaim = principal.FindFirst("ropts")?.Value;
        if (!string.IsNullOrWhiteSpace(runtimeOptionsClaim))
        {
            try
            {
                runtimeOptions = System.Text.Json.JsonSerializer.Deserialize<NativeRuntimeOptions>(
                    runtimeOptionsClaim,
                    new System.Text.Json.JsonSerializerOptions(System.Text.Json.JsonSerializerDefaults.Web));
            }
            catch (System.Text.Json.JsonException)
            {
                throw new WorkOrderRejectedException("Native runtime options are malformed.");
            }
            ValidateNativeOptions(runtimeOptions, mode, harness);
        }

        return new ValidatedWorkOrder(
            WorkOrderId: Claim("wid"),
            RequestingUserId: requestingUser,
            ProfileId: Claim("pid"),
            TeamId: string.IsNullOrEmpty(teamId) ? null : teamId,
            WorkspaceId: workspaceId,
            Harness: harness,
            Mode: mode,
            CorrelationId: Claim("cid"),
            Nonce: nonce,
            RuntimeSessionId: principal.FindFirst("rsid")?.Value,
            RuntimeModel: principal.FindFirst("rmodel")?.Value,
            RuntimeOptions: runtimeOptions,
            InputImagesDigest: principal.FindFirst("imgsha")?.Value);
    }

    private static void ValidateNativeOptions(
        NativeRuntimeOptions? options, string mode, string harness)
    {
        if (options is null) throw new WorkOrderRejectedException("Native runtime options are missing.");
        if (string.Equals(harness, "integrations", StringComparison.OrdinalIgnoreCase))
        {
            if (mode != "readOnly" || options.Sandbox != "readOnly")
                throw new WorkOrderRejectedException("Integration actions are read-only work orders.");
            if (options.Action is not (
                "sessionsSync" or "sessionRead" or "sessionWatch"
                or "workspaceInspect" or "openIde"))
                throw new WorkOrderRejectedException("Integration action is not allowed.");
            if (options.Provider is not null && options.Provider is not ("codex" or "claude" or "all"))
                throw new WorkOrderRejectedException("Integration provider is not allowed.");
            if (options.ProviderSessionId is { Length: > 160 })
                throw new WorkOrderRejectedException("Provider session identifier is too long.");
            if (options.Ide is not null && options.Ide is not ("vscode" or "cursor"))
                throw new WorkOrderRejectedException("IDE is not allowed.");
            if (options.WatchSeconds is < 0 or > 20)
                throw new WorkOrderRejectedException("Live watch duration is not allowed.");
            if ((options.Action is "sessionRead" or "sessionWatch")
                && (options.Provider is not ("codex" or "claude")
                    || string.IsNullOrWhiteSpace(options.ProviderSessionId)))
                throw new WorkOrderRejectedException(
                    "A supported provider and exact session identifier are required.");
            if (options.Action == "openIde" && options.Ide is not ("vscode" or "cursor"))
                throw new WorkOrderRejectedException("An available IDE must be selected.");
            return;
        }
        if (options.Action is not ("turn" or "review"))
            throw new WorkOrderRejectedException("Native runtime action is not allowed.");
        if (options.CollaborationMode is not ("default" or "plan"))
            throw new WorkOrderRejectedException("Native collaboration mode is not allowed.");
        if (options.Effort is not null && options.Effort is not
            ("minimal" or "low" or "medium" or "high" or "xhigh" or "max" or "ultra"))
            throw new WorkOrderRejectedException("Native reasoning effort is not allowed.");
        if (options.Personality is not ("none" or "friendly" or "pragmatic"))
            throw new WorkOrderRejectedException("Native personality is not allowed.");
        if (options.ApprovalPolicy is not ("on-request" or "untrusted"))
            throw new WorkOrderRejectedException("Native approval policy is not allowed.");
        if (options.Sandbox is not ("readOnly" or "workspaceWrite"))
            throw new WorkOrderRejectedException("Native sandbox is not allowed.");
        if (!string.Equals(options.Sandbox, mode, StringComparison.Ordinal))
            throw new WorkOrderRejectedException("Native sandbox does not match the signed work-order mode.");
        if (options.ReviewTarget != "uncommittedChanges")
            throw new WorkOrderRejectedException("Native review target is not allowed.");
    }
}
