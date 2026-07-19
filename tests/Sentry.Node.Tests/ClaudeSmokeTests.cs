using Sentry.Node.Harnesses;
using Sentry.Node.Workspaces;

namespace Sentry.Node.Tests;

/// <summary>
/// Drives the real Claude Code CLI once, read-only, in a throwaway workspace.
///
/// Parsing synthetic stream-json proves the parser, not the adapter. This proves
/// the process actually launches with the settings and allowlist, produces a
/// stream, and terminates with a result.
///
/// Opt-in via SENTRY_CLAUDE_SMOKE=1 so ordinary runs stay hermetic and free: it
/// spends real model quota. Never point it at a repository worktree — one writer
/// harness per worktree, and this test is not the writer.
/// </summary>
public class ClaudeSmokeTests
{
    private static bool Enabled =>
        Environment.GetEnvironmentVariable("SENTRY_CLAUDE_SMOKE") == "1";

    [Fact]
    public async Task ReadOnlyRunProducesATerminalResult()
    {
        Assert.SkipUnless(Enabled, "Set SENTRY_CLAUDE_SMOKE=1 to spend quota on a real run.");

        var root = Directory.CreateTempSubdirectory("sentry-claude-smoke").FullName;
        try
        {
            await File.WriteAllTextAsync(
                Path.Combine(root, "FACT.txt"), "The sentry codeword is HELIOTROPE.\n", TestContext.Current.CancellationToken);

            var settings = Path.Combine(AppContext.BaseDirectory, "claude-automation-settings.json");
            var adapter = new ClaudeAdapter("claude", settings, TimeSpan.FromMinutes(3));

            var events = new List<HarnessProgress>();
            var progress = new Progress<HarnessProgress>(e => { lock (events) events.Add(e); });

            var result = await adapter.ExecuteAsync(
                "Read FACT.txt and reply with only the codeword it contains.",
                "readOnly",
                new WorkspaceRegistration(
                    "ws-smoke", root,
                    new HashSet<string> { "claude" },
                    new HashSet<string> { "readOnly" }),
                progress,
                TestContext.Current.CancellationToken);

            Assert.Equal("succeeded", result.Outcome);
            // Proves the harness actually read the file rather than merely starting.
            Assert.Contains("HELIOTROPE", result.Summary, StringComparison.OrdinalIgnoreCase);
            Assert.Equal("implemented", result.StatusBoundary);
            Assert.Equal("ws-smoke", result.Evidence["workspaceId"]);
            Assert.True((int)result.Evidence["events"] > 0, "no stream events were parsed");
        }
        finally
        {
            try { Directory.Delete(root, recursive: true); } catch (IOException) { }
        }
    }
}

