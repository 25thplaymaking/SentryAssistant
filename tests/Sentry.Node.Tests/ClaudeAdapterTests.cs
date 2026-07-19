using System.Text.Json;
using Sentry.Node.Harnesses;
using Sentry.Node.Workspaces;

namespace Sentry.Node.Tests;

public class ClaudeAdapterTests
{
    private static WorkspaceRegistration Workspace(string root) => new(
        "ws-test", root,
        new HashSet<string> { "claude" },
        new HashSet<string> { "readOnly", "workspaceWrite" });

    private static IProgress<HarnessProgress> Sink() => new Progress<HarnessProgress>(_ => { });

    private static ClaudeAdapter Adapter() =>
        new("claude", "settings.json", TimeSpan.FromSeconds(30));

    [Fact]
    public async Task RefusesElevatedWork()
    {
        var result = await Adapter().ExecuteAsync(
            "do something", "approvedElevated", Workspace(Path.GetTempPath()), Sink(), TestContext.Current.CancellationToken);

        Assert.Equal("failed", result.Outcome);
        Assert.Contains("per-action approval", result.Summary);
        Assert.True((bool)result.Evidence["refused"]);
    }

    [Fact]
    public async Task RefusesAnUnknownMode()
    {
        var result = await Adapter().ExecuteAsync(
            "do something", "rootAccess", Workspace(Path.GetTempPath()), Sink(), TestContext.Current.CancellationToken);

        Assert.Equal("failed", result.Outcome);
        Assert.Contains("grants no tools", result.Summary);
    }

    [Fact]
    public async Task ReportsAMissingWorkspaceRoot()
    {
        var missing = Path.Combine(Path.GetTempPath(), $"sentry-absent-{Guid.NewGuid():N}");
        var result = await Adapter().ExecuteAsync(
            "inspect", "readOnly", Workspace(missing), Sink(), TestContext.Current.CancellationToken);

        Assert.Equal("failed", result.Outcome);
        Assert.True((bool)result.Evidence["missingRoot"]);
    }

    [Fact]
    public async Task MissingExecutableIsReportedNotThrown()
    {
        var root = Directory.CreateTempSubdirectory("sentry-claude-missing").FullName;
        try
        {
            var adapter = new ClaudeAdapter(
                $"claude-does-not-exist-{Guid.NewGuid():N}", "settings.json", TimeSpan.FromSeconds(10));
            var result = await adapter.ExecuteAsync("x", "readOnly", Workspace(root), Sink(), TestContext.Current.CancellationToken);

            Assert.Equal("failed", result.Outcome);
            Assert.True((bool)result.Evidence["startFailed"]);
        }
        finally
        {
            Directory.Delete(root, recursive: true);
        }
    }

    public class AutomationSettings
    {
        private static JsonDocument Load()
        {
            // Linked into the test output by the csproj, so this is the real
            // shipped file and not a source-relative guess.
            var path = Path.Combine(AppContext.BaseDirectory, "claude-automation-settings.json");
            Assert.True(File.Exists(path), $"automation settings were not copied to {path}");
            return JsonDocument.Parse(File.ReadAllText(path));
        }

        // The whole point of a separate settings file: an unattended run has
        // nobody to answer a prompt, so nothing may be auto-approved for them.
        [Fact]
        public void NeverSkipsTheDangerousModePrompt()
        {
            using var document = Load();
            var raw = document.RootElement.GetRawText();
            Assert.DoesNotContain("skipDangerousModePermissionPrompt", raw);
            Assert.DoesNotContain("bypassPermissions", raw);
            Assert.DoesNotContain("dangerously", raw, StringComparison.OrdinalIgnoreCase);
        }

        [Fact]
        public void DeniesOutboundAndDestructiveCommands()
        {
            using var document = Load();
            var deny = document.RootElement
                .GetProperty("permissions").GetProperty("deny")
                .EnumerateArray().Select(e => e.GetString() ?? "").ToList();

            foreach (var expected in new[]
                     {
                         "Bash(git push:*)", "Bash(git reset:*)", "Bash(curl:*)", "Bash(ssh:*)"
                     })
            {
                Assert.Contains(expected, deny);
            }
        }

        [Fact]
        public void DeniesReadingCredentialMaterial()
        {
            using var document = Load();
            var deny = document.RootElement
                .GetProperty("permissions").GetProperty("deny")
                .EnumerateArray().Select(e => e.GetString() ?? "").ToList();

            Assert.Contains(deny, rule => rule.Contains(".ssh"));
            Assert.Contains(deny, rule => rule.Contains(".env"));
            Assert.Contains(deny, rule => rule.Contains("id_ed25519"));
        }

        [Fact]
        public void DefaultPermissionModeIsNotPermissive()
        {
            using var document = Load();
            var mode = document.RootElement
                .GetProperty("permissions").GetProperty("defaultMode").GetString();
            Assert.Equal("default", mode);
        }
    }
}

