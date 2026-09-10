using System.Net;
using System.Text;
using System.Text.Json;
using Sentry.Node.Gateway;

namespace Sentry.Node.Tests;

public class NodeConnectionTests
{
    [Fact]
    public async Task UnauthorizedRequestRotatesPersistsAndRetriesOnce()
    {
        var calls = new List<(string Path, string? Bearer)>();
        var persisted = new List<(string Access, string Refresh)>();
        var handler = new Handler(async request =>
        {
            calls.Add((request.RequestUri!.AbsolutePath, request.Headers.Authorization?.Parameter));
            if (request.RequestUri.AbsolutePath == "/api/auth/refresh")
            {
                var requestBody = await request.Content!.ReadAsStringAsync();
                Assert.Contains("old-refresh", requestBody);
                return Json(HttpStatusCode.OK,
                    """{"access_token":"new-access","refresh_token":"new-refresh","device_id":"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa","profile_id":"bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"}""");
            }

            return calls.Count == 1
                ? new HttpResponseMessage(HttpStatusCode.Unauthorized)
                : Json(HttpStatusCode.OK, "[]");
        });
        using var client = new HttpClient(handler);
        var credentials = new NodeCredentialSession(
            "old-access", "old-refresh",
            (access, refresh, _) =>
            {
                persisted.Add((access, refresh));
                return Task.CompletedTask;
            });
        using var connection = new NodeConnection("http://gateway.test", credentials, client);

        var work = await connection.ClaimWorkAsync(TestContext.Current.CancellationToken);

        Assert.Empty(work);
        Assert.Equal(
            [
                ("/api/nodes/work", "old-access"),
                ("/api/auth/refresh", null),
                ("/api/nodes/work", "new-access")
            ],
            calls);
        Assert.Equal([("new-access", "new-refresh")], persisted);
        Assert.Equal("new-access", credentials.AccessToken);
        Assert.Equal("new-refresh", credentials.RefreshToken);
    }

    [Fact]
    public async Task ASecondUnauthorizedResponseIsReturnedWithoutARefreshLoop()
    {
        var workCalls = 0;
        var refreshCalls = 0;
        var handler = new Handler(request =>
        {
            if (request.RequestUri!.AbsolutePath == "/api/auth/refresh")
            {
                refreshCalls++;
                return Task.FromResult(Json(HttpStatusCode.OK,
                    """{"access_token":"new-access","refresh_token":"new-refresh","device_id":"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa","profile_id":"bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"}"""));
            }
            workCalls++;
            return Task.FromResult(new HttpResponseMessage(HttpStatusCode.Unauthorized));
        });
        using var client = new HttpClient(handler);
        using var connection = new NodeConnection(
            "http://gateway.test",
            new NodeCredentialSession("old-access", "old-refresh"),
            client);

        await Assert.ThrowsAsync<HttpRequestException>(
            () => connection.ClaimWorkAsync(TestContext.Current.CancellationToken));
        Assert.Equal(2, workCalls);
        Assert.Equal(1, refreshCalls);
    }

    [Fact]
    public async Task NativeEventsUseTheExactWorkOrderEndpointAndPayload()
    {
        var workOrderId = Guid.Parse("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa");
        string? path = null;
        string? body = null;
        var handler = new Handler(async request =>
        {
            path = request.RequestUri!.PathAndQuery;
            body = await request.Content!.ReadAsStringAsync();
            return Json(HttpStatusCode.OK, "{\"accepted\":true}");
        });
        using var client = new HttpClient(handler);
        using var connection = new NodeConnection("http://gateway.test", "node-token", client);

        await connection.SubmitEventAsync(
            workOrderId,
            new RunEventRequest(
                7,
                "approval.required",
                "Approve command",
                JsonSerializer.SerializeToElement(new { request_id = "rpc-9" })),
            TestContext.Current.CancellationToken);

        Assert.Equal($"/api/nodes/work/{workOrderId}/events", path);
        Assert.Contains("\"event_index\":7", body);
        Assert.Contains("\"request_id\":\"rpc-9\"", body);
    }

    [Fact]
    public async Task NativeResponsePollingEscapesTheExactRequestId()
    {
        var workOrderId = Guid.Parse("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb");
        string? path = null;
        var handler = new Handler(request =>
        {
            path = request.RequestUri!.PathAndQuery;
            return Task.FromResult(Json(HttpStatusCode.OK,
                "{\"ready\":true,\"terminal\":false,\"response\":{\"decision\":\"once\"}}"));
        });
        using var client = new HttpClient(handler);
        using var connection = new NodeConnection("http://gateway.test", "node-token", client);

        var result = await connection.WaitForResponseAsync(
            workOrderId,
            "rpc id/9",
            TestContext.Current.CancellationToken);

        Assert.True(result.Ready);
        Assert.Contains($"/api/nodes/work/{workOrderId}/response", path);
        Assert.Contains("request_id=rpc%20id%2F9", path);
        Assert.Contains("wait_seconds=20", path);
    }

    private static HttpResponseMessage Json(HttpStatusCode status, string body) => new(status)
    {
        Content = new StringContent(body, Encoding.UTF8, "application/json")
    };

    private sealed class Handler(
        Func<HttpRequestMessage, Task<HttpResponseMessage>> send) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request, CancellationToken cancellationToken) => send(request);
    }
}
