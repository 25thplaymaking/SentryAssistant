using System.Net.Http.Json;
using System.Text.Json;
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

    private static GatewayStatus Offline(DateTimeOffset checkedAt, string detail) => new(
        new ComponentStatus("Gateway", ConnectionState.Offline, detail),
        new ComponentStatus("Runtime", ConnectionState.Unknown, "Gateway unreachable"),
        ComponentStatus.NotConfigured("Windows node", "Run sentry-node to enrol this machine"),
        checkedAt);

    public void Dispose() => _client.Dispose();

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
