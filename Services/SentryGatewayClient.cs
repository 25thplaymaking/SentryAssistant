using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Serialization;
using Sentry.Contracts;

namespace SentryAssistant.Services;

/// <summary>
/// Durable storage for this desktop's device credential.
///
/// An interface rather than a direct dependency on the settings file so the
/// rotation ordering can be reasoned about — and tested — without a real
/// Windows credential store.
/// </summary>
public interface IDeviceCredentialStore
{
    bool HasRefreshToken { get; }
    string GetRefreshToken();
    Task SetRefreshTokenAsync(string value);
    Task ClearRefreshTokenAsync();
}

/// <summary>
/// The desktop's connection to the Sentry Gateway.
///
/// Only reports what it has actually observed. A probe that fails produces
/// Offline, and a probe that has not run produces Unknown — there is no path
/// that renders a component as healthy without a response backing it.
/// </summary>
public sealed class SentryGatewayClient : IDisposable
{
    private static readonly JsonSerializerOptions Json = new(JsonSerializerDefaults.Web);

    private readonly HttpClient _client;
    private readonly bool _configured;
    private readonly IDeviceCredentialStore? _credentials;

    // Access tokens last fifteen minutes, so any long-lived screen outlives one.
    // A single in-flight refresh keeps a burst of expiries from racing each
    // other and retiring one another's rotated tokens.
    private readonly SemaphoreSlim _refreshGate = new(1, 1);

    public SentryGatewayClient(
        string? baseUrl,
        HttpClient? client = null,
        IDeviceCredentialStore? credentials = null)
    {
        _configured = !string.IsNullOrWhiteSpace(baseUrl);
        _client = client ?? new HttpClient();
        _credentials = credentials;

        if (_configured)
        {
            _client.BaseAddress = new Uri(baseUrl!.TrimEnd('/') + "/");
        }

        // Deadlines are set per call, not here.
        //
        // HttpClient.Timeout applies to every request and is not overridden by a
        // CancellationToken: a six-second client with a forty-five-second token
        // still aborts at six. The admin endpoint probes the runtime for a live
        // model and takes about eight, so it failed every single time and was
        // reported as an unreachable gateway.
        _client.Timeout = Timeout.InfiniteTimeSpan;
    }

    /// <summary>Status polling. Decoration — it must never make the UI feel stuck.</summary>
    private static readonly TimeSpan StatusBudget = TimeSpan.FromSeconds(6);

    /// <summary>Enrolment and token refresh. A person is waiting on these.</summary>
    private static readonly TimeSpan AuthBudget = TimeSpan.FromSeconds(20);

    /// <summary>The admin snapshot, which probes the runtime for a live model.</summary>
    private static readonly TimeSpan AdminBudget = TimeSpan.FromSeconds(45);

    private static CancellationTokenSource Deadline(
        TimeSpan budget, CancellationToken cancellationToken)
    {
        var source = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        source.CancelAfter(budget);
        return source;
    }

    public bool IsConfigured => _configured;

    /// <summary>
    /// Probe the control plane. Never throws: a status check that takes the app
    /// down would be worse than a status check that reports Offline.
    /// </summary>
    public async Task<GatewayStatus> GetStatusAsync(CancellationToken cancellationToken = default)
    {
        var checkedAt = DateTimeOffset.Now;

        if (!_configured)
        {
            return new GatewayStatus(
                ComponentStatus.NotConfigured("Gateway", "No gateway address configured"),
                ComponentStatus.NotConfigured("Runtime", "Requires a gateway"),
                ComponentStatus.NotConfigured("Windows node", "Requires a gateway"),
                checkedAt);
        }

        try
        {
            using var deadline = Deadline(StatusBudget, cancellationToken);
            using var response = await _client.GetAsync("health/ready", deadline.Token);
            var payload = await response.Content.ReadFromJsonAsync<ReadyResponse>(
                Json, deadline.Token);

            if (payload is null)
            {
                return Offline(checkedAt, "Gateway returned an unreadable response");
            }

            var gateway = payload.Checks?.Database == "ok"
                ? new ComponentStatus("Gateway", ConnectionState.Online, "Connected")
                : new ComponentStatus(
                    "Gateway", ConnectionState.Degraded,
                    payload.Checks?.Database ?? "Database unavailable");

            var runtimeInfo = payload.Checks?.Runtime;
            var runtime = runtimeInfo switch
            {
                null => new ComponentStatus("Runtime", ConnectionState.Offline, "No runtime reported"),
                { Healthy: true } => new ComponentStatus(
                    "Runtime", ConnectionState.Online,
                    $"{runtimeInfo.Name} {runtimeInfo.PinnedVersion}"),
                _ => new ComponentStatus(
                    "Runtime", ConnectionState.Degraded,
                    runtimeInfo.DegradedReason ?? "Degraded"),
            };

            // The desktop is not itself an execution node. Reporting it as one
            // would be exactly the placeholder this replaces.
            var node = ComponentStatus.NotConfigured(
                "Windows node", "Run sentry-node to enrol this machine");

            return new GatewayStatus(gateway, runtime, node, checkedAt);
        }
        catch (Exception exception) when (exception is HttpRequestException or TaskCanceledException)
        {
            return Offline(checkedAt, "Gateway unreachable");
        }
        catch (Exception)
        {
            return Offline(checkedAt, "Gateway check failed");
        }
    }

    /// <summary>
    /// Redeem a one-time enrolment code. Returns the failure reason rather than
    /// throwing, because every failure here is something the person can act on.
    /// </summary>
    public async Task<(bool Success, string Detail)> EnrolAsync(
        string code, string deviceName, CancellationToken cancellationToken = default)
    {
        if (!_configured) return (false, "Set a gateway address first.");

        try
        {
            using var deadline = Deadline(AuthBudget, cancellationToken);
            using var response = await _client.PostAsJsonAsync(
                "api/auth/enroll/complete",
                new { code, device_name = deviceName },
                Json,
                deadline.Token);

            if (!response.IsSuccessStatusCode)
            {
                return (false, response.StatusCode == System.Net.HttpStatusCode.BadRequest
                    ? "That code is invalid or has expired."
                    : $"Gateway refused enrolment ({(int)response.StatusCode}).");
            }

            var pair = await response.Content.ReadFromJsonAsync<TokenPair>(Json, deadline.Token);
            if (pair is null || string.IsNullOrWhiteSpace(pair.AccessToken))
            {
                return (false, "Gateway returned no credentials.");
            }

            await AdoptAsync(pair);
            return (true, $"Enrolled as device {pair.DeviceId}.");
        }
        catch (Exception exception) when (exception is HttpRequestException or TaskCanceledException)
        {
            return (false, "Gateway unreachable.");
        }
    }

    /// <summary>
    /// Resume a saved session at launch, so enrolment is a one-time act rather
    /// than something repeated on every start.
    /// </summary>
    public async Task<SessionRestore> RestoreSessionAsync(
        CancellationToken cancellationToken = default)
    {
        if (!_configured)
        {
            return new SessionRestore(SessionRestoreOutcome.NoGatewayConfigured);
        }

        if (_credentials is null || !_credentials.HasRefreshToken)
        {
            return new SessionRestore(SessionRestoreOutcome.NoStoredCredential);
        }

        var stored = _credentials.GetRefreshToken();
        if (string.IsNullOrWhiteSpace(stored))
        {
            // Sealed by a different Windows account, so unusable here.
            return new SessionRestore(
                SessionRestoreOutcome.NoStoredCredential,
                "Stored credentials could not be read by this Windows account.");
        }

        return await RedeemAsync(stored, cancellationToken);
    }

    /// <summary>
    /// Exchange a refresh token for a fresh pair. The gateway retires the
    /// presented token as it is redeemed, so the replacement is written to
    /// durable storage before this returns.
    /// </summary>
    private async Task<SessionRestore> RedeemAsync(
        string refreshToken, CancellationToken cancellationToken)
    {
        try
        {
            using var deadline = Deadline(AuthBudget, cancellationToken);
            using var response = await _client.PostAsJsonAsync(
                "api/auth/refresh",
                new { refresh_token = refreshToken },
                Json,
                deadline.Token);

            if (response.StatusCode == System.Net.HttpStatusCode.Unauthorized)
            {
                // Terminal: revoked, expired, or already redeemed. Keeping the
                // token would reproduce this failure on every launch.
                if (_credentials is not null) await _credentials.ClearRefreshTokenAsync();
                _accessToken = null;

                // The gateway distinguishes "Device has been revoked" from a
                // retired or expired token. That distinction is the difference
                // between "an administrator did this" and "this simply lapsed",
                // so it is worth carrying through to the screen.
                var reason = await ReadDetailAsync(response, deadline.Token);
                return new SessionRestore(
                    SessionRestoreOutcome.Revoked,
                    string.IsNullOrWhiteSpace(reason)
                        ? "Stored credentials were rejected. Redeem a new enrolment code."
                        : $"{reason} Redeem a new enrolment code.");
            }

            if (!response.IsSuccessStatusCode)
            {
                return new SessionRestore(
                    SessionRestoreOutcome.Unreachable,
                    $"Gateway refused the credential refresh ({(int)response.StatusCode}).");
            }

            var pair = await response.Content.ReadFromJsonAsync<TokenPair>(Json, deadline.Token);
            if (pair is null || string.IsNullOrWhiteSpace(pair.AccessToken))
            {
                return new SessionRestore(
                    SessionRestoreOutcome.Unreachable, "Gateway returned no credentials.");
            }

            await AdoptAsync(pair);
            return new SessionRestore(SessionRestoreOutcome.Restored);
        }
        catch (Exception exception) when (exception is HttpRequestException or TaskCanceledException)
        {
            // Deliberately not Revoked: an unreachable gateway says nothing about
            // whether the credential is still good, and the stored token stays.
            return new SessionRestore(SessionRestoreOutcome.Unreachable);
        }
    }

    /// <summary>
    /// Pull the gateway's own explanation out of an error response, so the screen
    /// can say what actually happened instead of a generic stand-in.
    /// </summary>
    private static async Task<string> ReadDetailAsync(
        HttpResponseMessage response, CancellationToken cancellationToken)
    {
        try
        {
            var problem = await response.Content.ReadFromJsonAsync<ErrorDetail>(
                Json, cancellationToken);
            return problem?.Detail ?? string.Empty;
        }
        catch (Exception)
        {
            // An unparseable error body is not itself worth reporting; the caller
            // already has a usable fallback.
            return string.Empty;
        }
    }

    /// <summary>
    /// Take up a freshly issued pair. The rotated refresh token reaches disk
    /// before the access token is put to use, because a rotation that is lost to
    /// a crash cannot be recovered — the gateway has already retired its
    /// predecessor.
    /// </summary>
    private async Task AdoptAsync(TokenPair pair)
    {
        if (_credentials is not null && !string.IsNullOrWhiteSpace(pair.RefreshToken))
        {
            await _credentials.SetRefreshTokenAsync(pair.RefreshToken);
        }

        _accessToken = pair.AccessToken;
    }

    /// <summary>
    /// Renew the access token using the stored refresh token. Returns whether a
    /// usable access token is now held.
    /// </summary>
    private async Task<bool> TryRenewAsync(CancellationToken cancellationToken)
    {
        if (_credentials is null || !_credentials.HasRefreshToken) return false;

        await _refreshGate.WaitAsync(cancellationToken);
        try
        {
            var stored = _credentials.GetRefreshToken();
            if (string.IsNullOrWhiteSpace(stored)) return false;

            var restore = await RedeemAsync(stored, cancellationToken);
            return restore.IsAuthenticated;
        }
        finally
        {
            _refreshGate.Release();
        }
    }

    /// <summary>Why an admin snapshot could not be produced.</summary>
    public enum AdminAccess
    {
        Granted,
        NotEnrolled,
        NotAdministrator,
        Unreachable
    }

    /// <summary>
    /// Admin view of the runtime.
    ///
    /// Reports *why* it failed rather than collapsing every failure into "not an
    /// administrator" — a timeout reported as a permissions problem sends someone
    /// to fix the wrong thing.
    /// </summary>
    public async Task<(AdminAccess Access, HermesAdminSnapshot? Snapshot)> GetHermesAdminAsync(
        CancellationToken cancellationToken = default)
    {
        if (!_configured || string.IsNullOrWhiteSpace(_accessToken))
        {
            return (AdminAccess.NotEnrolled, null);
        }

        try
        {
            // This endpoint probes the runtime for a live model and measured
            // around eight seconds against the live gateway, so it needs a far
            // longer budget than a status poll.
            using var deadline = Deadline(AdminBudget, cancellationToken);

            var response = await SendAuthorizedAsync(
                () => new HttpRequestMessage(HttpMethod.Get, "api/admin/hermes"), deadline.Token);

            using (response)
            {
                if (response.StatusCode is System.Net.HttpStatusCode.NotFound
                    or System.Net.HttpStatusCode.Forbidden)
                {
                    return (AdminAccess.NotAdministrator, null);
                }

                // Still unauthorized after a renewal attempt: the credential is
                // gone, not merely stale.
                if (response.StatusCode == System.Net.HttpStatusCode.Unauthorized)
                {
                    return (AdminAccess.NotEnrolled, null);
                }

                if (!response.IsSuccessStatusCode) return (AdminAccess.Unreachable, null);

                var snapshot = await response.Content.ReadFromJsonAsync<HermesAdminSnapshot>(
                    Json, deadline.Token);

                return snapshot is null
                    ? (AdminAccess.Unreachable, null)
                    : (AdminAccess.Granted, snapshot);
            }
        }
        catch (Exception)
        {
            return (AdminAccess.Unreachable, null);
        }
    }

    /// <summary>
    /// Send a request bearing the access token, renewing once if the gateway
    /// says it has expired.
    ///
    /// Access tokens last fifteen minutes, so a window left open longer than a
    /// coffee break will hold a dead one. Without this, an expired token reads
    /// as an unreachable gateway and points at the wrong problem.
    /// </summary>
    /// <param name="build">
    /// Builds the request. A factory rather than an instance because a sent
    /// <see cref="HttpRequestMessage"/> cannot be sent a second time.
    /// </param>
    private async Task<HttpResponseMessage> SendAuthorizedAsync(
        Func<HttpRequestMessage> build, CancellationToken cancellationToken)
    {
        var response = await SendOnceAsync(build, cancellationToken);
        if (response.StatusCode != System.Net.HttpStatusCode.Unauthorized) return response;

        response.Dispose();
        if (!await TryRenewAsync(cancellationToken))
        {
            // Report the original refusal rather than inventing a status.
            return new HttpResponseMessage(System.Net.HttpStatusCode.Unauthorized);
        }

        return await SendOnceAsync(build, cancellationToken);
    }

    private async Task<HttpResponseMessage> SendOnceAsync(
        Func<HttpRequestMessage> build, CancellationToken cancellationToken)
    {
        // Safe to dispose the request once the response is in hand: these carry
        // no request body whose stream the response could still be reading.
        using var request = build();
        request.Headers.Authorization =
            new System.Net.Http.Headers.AuthenticationHeaderValue("Bearer", _accessToken);
        return await _client.SendAsync(request, cancellationToken);
    }

    private string? _accessToken;

    /// <summary>Whether this desktop currently holds device credentials.</summary>
    public bool IsEnrolled => !string.IsNullOrWhiteSpace(_accessToken);

    private static GatewayStatus Offline(DateTimeOffset checkedAt, string detail) => new(
        new ComponentStatus("Gateway", ConnectionState.Offline, detail),
        new ComponentStatus("Runtime", ConnectionState.Unknown, "Gateway unreachable"),
        ComponentStatus.NotConfigured("Windows node", "Run sentry-node to enrol this machine"),
        checkedAt);

    public void Dispose()
    {
        _client.Dispose();
        _refreshGate.Dispose();
    }

    // The Gateway's Pydantic response models serialize snake_case, which the Web
    // JSON defaults (camelCase) do not match. Naming each field explicitly is the
    // same approach the node connection already uses, and keeps a rename on
    // either side a compile-time concern rather than a silent null.
    private sealed record TokenPair(
        [property: JsonPropertyName("access_token")] string AccessToken,
        [property: JsonPropertyName("refresh_token")] string RefreshToken,
        [property: JsonPropertyName("device_id")] Guid DeviceId,
        [property: JsonPropertyName("profile_id")] Guid ProfileId);

    // FastAPI reports refusals as {"detail": "..."}.
    private sealed record ErrorDetail([property: JsonPropertyName("detail")] string? Detail);

    // Mirrors the Gateway's /health/ready payload.
    private sealed record ReadyResponse(string Status, ReadyChecks? Checks);

    private sealed record ReadyChecks(
        string? SigningKey,
        string? Database,
        RuntimeCheck? Runtime);

    private sealed record RuntimeCheck(
        string Name,
        string PinnedVersion,
        bool Healthy,
        string? DegradedReason);
}
