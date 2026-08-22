using System.Net;
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

public sealed record NodeTokenPair(
    [property: JsonPropertyName("access_token")] string AccessToken,
    [property: JsonPropertyName("refresh_token")] string RefreshToken,
    [property: JsonPropertyName("device_id")] Guid DeviceId,
    [property: JsonPropertyName("profile_id")] Guid ProfileId);

/// <summary>
/// Mutable node token pair with serialized refresh and durable rotation.
///
/// Gateway access tokens live for fifteen minutes. A daemon that only keeps the
/// enrollment access token becomes permanently offline after that window, so a
/// 401 refreshes once, persists the rotated pair, and retries the exact request.
/// </summary>
public sealed class NodeCredentialSession
{
    private static readonly JsonSerializerOptions Json = new(JsonSerializerDefaults.Web);
    private readonly Func<string, string, CancellationToken, Task>? _persist;
    private readonly SemaphoreSlim _refreshGate = new(1, 1);

    public NodeCredentialSession(
        string accessToken,
        string? refreshToken,
        Func<string, string, CancellationToken, Task>? persist = null)
    {
        if (string.IsNullOrWhiteSpace(accessToken))
            throw new ArgumentException("An access token is required.", nameof(accessToken));

        AccessToken = accessToken;
        RefreshToken = refreshToken;
        _persist = persist;
    }

    public string AccessToken { get; private set; }
    public string? RefreshToken { get; private set; }

    internal async Task RefreshAsync(
        HttpClient client,
        string rejectedAccessToken,
        CancellationToken cancellationToken)
    {
        await _refreshGate.WaitAsync(cancellationToken);
        try
        {
            // Another request may already have rotated the pair while this one
            // waited. In that case the fresh access token is ready to retry.
            if (!string.Equals(AccessToken, rejectedAccessToken, StringComparison.Ordinal))
                return;

            if (string.IsNullOrWhiteSpace(RefreshToken))
                throw new InvalidOperationException(
                    "The execution node credential expired and has no refresh token.");

            using var response = await client.PostAsJsonAsync(
                "api/auth/refresh",
                new Dictionary<string, string> { ["refresh_token"] = RefreshToken },
                Json,
                cancellationToken);
            response.EnsureSuccessStatusCode();

            var pair = await response.Content.ReadFromJsonAsync<NodeTokenPair>(Json, cancellationToken)
                ?? throw new InvalidOperationException("The Gateway returned an empty token refresh.");
            if (string.IsNullOrWhiteSpace(pair.AccessToken) || string.IsNullOrWhiteSpace(pair.RefreshToken))
                throw new InvalidOperationException("The Gateway returned an incomplete token refresh.");

            // Rotation invalidates the old refresh token immediately. Persist
            // the replacement before accepting it in memory so a restart can
            // never resurrect the retired token.
            if (_persist is not null)
                await _persist(pair.AccessToken, pair.RefreshToken, cancellationToken);

            AccessToken = pair.AccessToken;
            RefreshToken = pair.RefreshToken;
        }
        finally
        {
            _refreshGate.Release();
        }
    }
}

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
    private readonly NodeCredentialSession _credentials;

    public NodeConnection(string baseUrl, string accessToken, HttpClient? client = null)
        : this(baseUrl, new NodeCredentialSession(accessToken, refreshToken: null), client)
    {
    }

    public NodeConnection(
        string baseUrl,
        NodeCredentialSession credentials,
        HttpClient? client = null)
    {
        _ownsClient = client is null;
        _client = client ?? new HttpClient();
        _client.BaseAddress = new Uri(baseUrl.TrimEnd('/') + "/");
        _credentials = credentials;
        _client.Timeout = TimeSpan.FromSeconds(60);
    }

    public async Task<NodeRegistrationResponse?> RegisterAsync(
        NodeRegistrationRequest request, CancellationToken cancellationToken)
    {
        using var response = await SendAsync(
            () => new HttpRequestMessage(HttpMethod.Post, "api/nodes/register")
            {
                Content = JsonContent.Create(request, options: Json)
            },
            cancellationToken);
        response.EnsureSuccessStatusCode();
        return await response.Content.ReadFromJsonAsync<NodeRegistrationResponse>(
            Json, cancellationToken);
    }

    /// <summary>Claim assigned work. An empty list is the normal idle case.</summary>
    public async Task<IReadOnlyList<DispatchedWorkOrder>> ClaimWorkAsync(
        CancellationToken cancellationToken)
    {
        using var response = await SendAsync(
            () => new HttpRequestMessage(HttpMethod.Get, "api/nodes/work"),
            cancellationToken);
        response.EnsureSuccessStatusCode();
        return await response.Content.ReadFromJsonAsync<List<DispatchedWorkOrder>>(
            Json, cancellationToken) ?? [];
    }

    public async Task SubmitResultAsync(
        Guid workOrderId, RunResultRequest result, CancellationToken cancellationToken)
    {
        using var response = await SendAsync(
            () => new HttpRequestMessage(HttpMethod.Post, $"api/nodes/work/{workOrderId}/result")
            {
                Content = JsonContent.Create(result, options: Json)
            },
            cancellationToken);
        response.EnsureSuccessStatusCode();
    }

    private async Task<HttpResponseMessage> SendAsync(
        Func<HttpRequestMessage> createRequest,
        CancellationToken cancellationToken)
    {
        var attemptedToken = _credentials.AccessToken;
        for (var attempt = 0; attempt < 2; attempt++)
        {
            using var request = createRequest();
            request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", attemptedToken);
            var response = await _client.SendAsync(request, cancellationToken);
            if (response.StatusCode != HttpStatusCode.Unauthorized || attempt == 1)
                return response;

            response.Dispose();
            await _credentials.RefreshAsync(_client, attemptedToken, cancellationToken);
            attemptedToken = _credentials.AccessToken;
        }

        throw new InvalidOperationException("The execution-node request could not be sent.");
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
