using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace Sentry.Node.Gateway;

public sealed record DispatchedWorkOrder(
    [property: JsonPropertyName("work_order_id")] Guid WorkOrderId,
    [property: JsonPropertyName("token")] string Token,
    [property: JsonPropertyName("prompt")] string Prompt,
    [property: JsonPropertyName("workspace_id")] string WorkspaceId,
    [property: JsonPropertyName("harness")] string Harness,
    [property: JsonPropertyName("mode")] string Mode,
    [property: JsonPropertyName("correlation_id")] string CorrelationId);

public sealed record WorkspaceRegistrationRequest(
    [property: JsonPropertyName("workspace_id")] string WorkspaceId,
    [property: JsonPropertyName("allowed_harnesses")] IReadOnlyList<string> AllowedHarnesses,
    [property: JsonPropertyName("allowed_modes")] IReadOnlyList<string> AllowedModes);

public sealed record NodeRegistrationRequest(
    [property: JsonPropertyName("name")] string Name,
    [property: JsonPropertyName("workspaces")] IReadOnlyList<WorkspaceRegistrationRequest> Workspaces);

public sealed record NodeRegistrationResponse(
    [property: JsonPropertyName("node_id")] Guid NodeId,
    [property: JsonPropertyName("name")] string Name,
    [property: JsonPropertyName("workspaces")] IReadOnlyList<string> Workspaces);

public sealed record RunResultRequest(
    [property: JsonPropertyName("outcome")] string Outcome,
    [property: JsonPropertyName("summary")] string Summary,
    [property: JsonPropertyName("status_boundary")] string StatusBoundary,
    [property: JsonPropertyName("evidence")] IReadOnlyDictionary<string, object> Evidence);

/// <summary>
/// The node's only channel to the Gateway.
///
/// Every call is outbound. Nothing listens on this machine, so there is no
/// inbound path to a Windows box and no port to expose. If the Gateway is
/// unreachable the node simply keeps retrying; it never falls back to accepting
/// work from anywhere else.
/// </summary>
public sealed class NodeConnection : IDisposable
{
    private static readonly JsonSerializerOptions Json = new(JsonSerializerDefaults.Web);

    private readonly HttpClient _client;
    private readonly bool _ownsClient;

    public NodeConnection(string baseUrl, string accessToken, HttpClient? client = null)
    {
        _ownsClient = client is null;
        _client = client ?? new HttpClient();
        _client.BaseAddress = new Uri(baseUrl.TrimEnd('/') + "/");
        _client.DefaultRequestHeaders.Authorization =
            new AuthenticationHeaderValue("Bearer", accessToken);
        _client.Timeout = TimeSpan.FromSeconds(60);
    }

    public async Task<NodeRegistrationResponse?> RegisterAsync(
        NodeRegistrationRequest request, CancellationToken cancellationToken)
    {
        using var response = await _client.PostAsJsonAsync(
            "api/nodes/register", request, Json, cancellationToken);
        response.EnsureSuccessStatusCode();
        return await response.Content.ReadFromJsonAsync<NodeRegistrationResponse>(
            Json, cancellationToken);
    }

    /// <summary>Claim assigned work. An empty list is the normal idle case.</summary>
    public async Task<IReadOnlyList<DispatchedWorkOrder>> ClaimWorkAsync(
        CancellationToken cancellationToken)
    {
        using var response = await _client.GetAsync("api/nodes/work", cancellationToken);
        response.EnsureSuccessStatusCode();
        return await response.Content.ReadFromJsonAsync<List<DispatchedWorkOrder>>(
            Json, cancellationToken) ?? [];
    }

    public async Task SubmitResultAsync(
        Guid workOrderId, RunResultRequest result, CancellationToken cancellationToken)
    {
        using var response = await _client.PostAsJsonAsync(
            $"api/nodes/work/{workOrderId}/result", result, Json, cancellationToken);
        response.EnsureSuccessStatusCode();
    }

    public void Dispose()
    {
        if (_ownsClient) _client.Dispose();
    }
}

/// <summary>
/// Reconnect backoff. A node that loses the Gateway must not hammer it, and must
/// not give up either: work queues on the server until the node returns.
/// </summary>
public sealed class ReconnectBackoff
{
    private readonly TimeSpan _initial;
    private readonly TimeSpan _max;
    private int _failures;

    public ReconnectBackoff(TimeSpan? initial = null, TimeSpan? max = null)
    {
        _initial = initial ?? TimeSpan.FromSeconds(2);
        _max = max ?? TimeSpan.FromMinutes(2);
    }

    public int Failures => _failures;

    public TimeSpan NextDelay()
    {
        _failures++;
        // Exponential, capped. Overflow is avoided by capping the exponent
        // rather than the resulting multiplication.
        var exponent = Math.Min(_failures - 1, 16);
        var delay = _initial * Math.Pow(2, exponent);
        return delay > _max ? _max : delay;
    }

    public void Reset() => _failures = 0;
}
