using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Serialization;
using Sentry.Contracts;

namespace SentryAssistant.Services;

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

    public SentryGatewayClient(string? baseUrl, HttpClient? client = null)
    {
        _configured = !string.IsNullOrWhiteSpace(baseUrl);
        _client = client ?? new HttpClient();

        if (_configured)
        {
            _client.BaseAddress = new Uri(baseUrl!.TrimEnd('/') + "/");
        }

        // Status is decoration; it must never make the UI feel stuck.
        _client.Timeout = TimeSpan.FromSeconds(6);
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
            using var response = await _client.GetAsync("health/ready", cancellationToken);
            var payload = await response.Content.ReadFromJsonAsync<ReadyResponse>(
                Json, cancellationToken);

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
            using var response = await _client.PostAsJsonAsync(
                "api/auth/enroll/complete",
                new { code, device_name = deviceName },
                Json,
                cancellationToken);

            if (!response.IsSuccessStatusCode)
            {
                return (false, response.StatusCode == System.Net.HttpStatusCode.BadRequest
                    ? "That code is invalid or has expired."
                    : $"Gateway refused enrolment ({(int)response.StatusCode}).");
            }

            var pair = await response.Content.ReadFromJsonAsync<TokenPair>(Json, cancellationToken);
            if (pair is null || string.IsNullOrWhiteSpace(pair.AccessToken))
            {
                return (false, "Gateway returned no credentials.");
            }

            _accessToken = pair.AccessToken;
            return (true, $"Enrolled as device {pair.DeviceId}.");
        }
        catch (Exception exception) when (exception is HttpRequestException or TaskCanceledException)
        {
            return (false, "Gateway unreachable.");
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
            using var request = new HttpRequestMessage(HttpMethod.Get, "api/admin/hermes");
            request.Headers.Authorization =
                new System.Net.Http.Headers.AuthenticationHeaderValue("Bearer", _accessToken);

            // This endpoint probes the runtime for a live model, which is far
            // slower than a status poll. The client-wide 6s budget times it out.
            using var slow = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            slow.CancelAfter(TimeSpan.FromSeconds(45));

            using var response = await _client.SendAsync(request, slow.Token);

            if (response.StatusCode is System.Net.HttpStatusCode.NotFound
                or System.Net.HttpStatusCode.Forbidden)
            {
                return (AdminAccess.NotAdministrator, null);
            }

            if (!response.IsSuccessStatusCode) return (AdminAccess.Unreachable, null);

            var snapshot = await response.Content.ReadFromJsonAsync<HermesAdminSnapshot>(
                Json, slow.Token);

            return snapshot is null
                ? (AdminAccess.Unreachable, null)
                : (AdminAccess.Granted, snapshot);
        }
        catch (Exception)
        {
            return (AdminAccess.Unreachable, null);
        }
    }

    private string? _accessToken;

    /// <summary>Whether this desktop currently holds device credentials.</summary>
    public bool IsEnrolled => !string.IsNullOrWhiteSpace(_accessToken);

    private static GatewayStatus Offline(DateTimeOffset checkedAt, string detail) => new(
        new ComponentStatus("Gateway", ConnectionState.Offline, detail),
        new ComponentStatus("Runtime", ConnectionState.Unknown, "Gateway unreachable"),
        ComponentStatus.NotConfigured("Windows node", "Run sentry-node to enrol this machine"),
        checkedAt);

    public void Dispose() => _client.Dispose();

    // The Gateway's Pydantic response models serialize snake_case, which the Web
    // JSON defaults (camelCase) do not match. Naming each field explicitly is the
    // same approach the node connection already uses, and keeps a rename on
    // either side a compile-time concern rather than a silent null.
    private sealed record TokenPair(
        [property: JsonPropertyName("access_token")] string AccessToken,
        [property: JsonPropertyName("refresh_token")] string RefreshToken,
        [property: JsonPropertyName("device_id")] Guid DeviceId,
        [property: JsonPropertyName("profile_id")] Guid ProfileId);

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
