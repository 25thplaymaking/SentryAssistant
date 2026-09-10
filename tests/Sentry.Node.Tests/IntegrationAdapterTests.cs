using System.Text.Json;
using Sentry.Node.Gateway;
using Sentry.Node.Harnesses;
using Sentry.Node.Workspaces;

namespace Sentry.Node.Tests;

public class IntegrationAdapterTests
{
    [Fact]
    public async Task SyncAndReadExposeOnlyWorkspaceScopedPublicSessionData()
    {
        var root = Path.Combine(Path.GetTempPath(), $"sentry-integration-{Guid.NewGuid():N}");
        var profile = Path.Combine(root, "profile");
        var workspaceRoot = Path.Combine(root, "workspace");
        var outsideRoot = Path.Combine(root, "outside");
        Directory.CreateDirectory(workspaceRoot);
        Directory.CreateDirectory(outsideRoot);
        var sessions = Path.Combine(profile, ".codex", "sessions", "2026", "08", "22");
        Directory.CreateDirectory(sessions);
        var sessionId = "11111111-2222-3333-4444-555555555555";
        var sessionFile = Path.Combine(sessions, $"rollout-{sessionId}.jsonl");
        var outsideId = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee";
        var outsideFile = Path.Combine(sessions, $"rollout-{outsideId}.jsonl");
        try
        {
            File.WriteAllLines(sessionFile,
            [
                JsonSerializer.Serialize(new
                {
                    timestamp = "2026-08-22T12:00:00Z",
                    type = "session_meta",
                    payload = new { id = sessionId, cwd = workspaceRoot, source = "vscode", git = new { branch = "bishop/integrations" } }
                }),
                JsonSerializer.Serialize(new
                {
                    timestamp = "2026-08-22T12:01:00Z",
                    type = "response_item",
                    payload = new
                    {
                        type = "message",
                        role = "user",
                        content = new[]
                        {
                            new
                            {
                                type = "input_text",
                                text = "<recommended_plugins>generated context</recommended_plugins> # AGENTS.md instructions <INSTRUCTIONS>generated policy</INSTRUCTIONS> <environment_context>generated environment</environment_context>"
                            }
                        }
                    }
                }),
                JsonSerializer.Serialize(new
                {
                    timestamp = "2026-08-22T12:01:01Z",
                    type = "response_item",
                    payload = new { type = "message", role = "user", content = new[] { new { type = "input_text", text = "Inspect the Sentry surface" } } }
                }),
                JsonSerializer.Serialize(new
                {
                    timestamp = "2026-08-22T12:02:00Z",
                    type = "response_item",
                    payload = new { type = "message", role = "assistant", content = new[] { new { type = "output_text", text = "Inspection complete" } } }
                })
            ]);
            File.WriteAllLines(outsideFile,
            [
                JsonSerializer.Serialize(new
                {
                    timestamp = "2026-08-22T12:00:00Z",
                    type = "session_meta",
                    payload = new { id = outsideId, cwd = outsideRoot, source = "cli" }
                }),
                JsonSerializer.Serialize(new
                {
                    type = "response_item",
                    payload = new { type = "message", role = "user", content = new[] { new { type = "input_text", text = "Private outside session" } } }
                })
            ]);

            var adapter = new IntegrationAdapter(profile);
            var workspace = new WorkspaceRegistration(
                "server-work", workspaceRoot,
                new HashSet<string> { "integrations" },
                new HashSet<string> { "readOnly" });
            var syncBridge = new RecordingBridge();
            var sync = await adapter.ExecuteInteractiveAsync(
                "", "readOnly", workspace, "integration:test", "integration-v1",
                new NativeRuntimeOptions(Action: "sessionsSync", Sandbox: "readOnly", Provider: "all"),
                [], syncBridge, CancellationToken.None);

            Assert.Equal("succeeded", sync.Outcome);
            var syncPayload = Assert.Single(syncBridge.Events).Payload!.Value;
            var rows = syncPayload.GetProperty("sessions");
            var row = Assert.Single(rows.EnumerateArray());
            Assert.Equal(sessionId, row.GetProperty("id").GetString());
            Assert.Equal("Inspect the Sentry surface", row.GetProperty("title").GetString());
            Assert.DoesNotContain(root, syncPayload.GetRawText(), StringComparison.OrdinalIgnoreCase);
            Assert.DoesNotContain("_file", syncPayload.GetRawText(), StringComparison.Ordinal);
            Assert.DoesNotContain(outsideId, syncPayload.GetRawText(), StringComparison.Ordinal);

            var readBridge = new RecordingBridge();
            var read = await adapter.ExecuteInteractiveAsync(
                "", "readOnly", workspace, "integration:test", "integration-v1",
                new NativeRuntimeOptions(
                    Action: "sessionRead", Sandbox: "readOnly", Provider: "codex",
                    ProviderSessionId: sessionId),
                [], readBridge, CancellationToken.None);

            Assert.Equal("succeeded", read.Outcome);
            var snapshot = Assert.Single(readBridge.Events).Payload!.Value;
            Assert.Equal("session", snapshot.GetProperty("kind").GetString());
            Assert.Equal(2, snapshot.GetProperty("messages").GetArrayLength());
            Assert.True(snapshot.GetProperty("read_only").GetBoolean());
            Assert.DoesNotContain(root, snapshot.GetRawText(), StringComparison.OrdinalIgnoreCase);
        }
        finally
        {
            if (Directory.Exists(root)) Directory.Delete(root, recursive: true);
        }
    }

    private sealed class RecordingBridge : IInteractiveHarnessBridge
    {
        public List<HarnessProgress> Events { get; } = [];

        public Task ReportAsync(HarnessProgress progress, CancellationToken cancellationToken)
        {
            Events.Add(progress);
            return Task.CompletedTask;
        }

        public Task<JsonElement?> WaitForResponseAsync(string requestId, CancellationToken cancellationToken) =>
            Task.FromResult<JsonElement?>(null);
    }
}
