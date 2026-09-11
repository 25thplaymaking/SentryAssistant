using Sentry.Node.Harnesses;
using Sentry.Node.Workspaces;

namespace Sentry.Node.Tests;

public class ProcessHarnessAdapterTests
{
    private static WorkspaceRegistration Workspace(string root) => new(
        "ws-test", root,
        new HashSet<string> { "codex" },
        new HashSet<string> { "readOnly", "workspaceWrite" });

    private static (IProgress<HarnessProgress> Progress, List<HarnessProgress> Events) Recorder()
    {
        var events = new List<HarnessProgress>();
        return (new Progress<HarnessProgress>(e => { lock (events) events.Add(e); }), events);
    }

    [Fact]
    public async Task RefusesACommandOutsideTheMode()
    {
        var (progress, _) = Recorder();
        var result = await new ProcessHarnessAdapter("codex").ExecuteAsync(
            "dotnet build", "readOnly", Workspace(Path.GetTempPath()), progress, TestContext.Current.CancellationToken);

        Assert.Equal("failed", result.Outcome);
        Assert.Contains("Refused", result.Summary);
        Assert.True((bool)result.Evidence["refused"]);
    }

    [Fact]
    public async Task RefusesADestructiveCommandInEveryMode()
    {
        var (progress, _) = Recorder();
        var result = await new ProcessHarnessAdapter("codex").ExecuteAsync(
            "git push origin main", "workspaceWrite", Workspace(Path.GetTempPath()), progress, TestContext.Current.CancellationToken);

        Assert.Equal("failed", result.Outcome);
        Assert.Contains("never permitted", result.Summary);
    }

    [Fact]
    public async Task ReportsAMissingWorkspaceRootRatherThanThrowing()
    {
        var (progress, _) = Recorder();
        var missing = Path.Combine(Path.GetTempPath(), $"sentry-missing-{Guid.NewGuid():N}");
        var result = await new ProcessHarnessAdapter("codex").ExecuteAsync(
            "git status", "readOnly", Workspace(missing), progress, TestContext.Current.CancellationToken);

        Assert.Equal("failed", result.Outcome);
        Assert.True((bool)result.Evidence["missingRoot"]);
    }

    [Fact]
    public async Task RunsAnAllowlistedCommandAndCapturesEvidence()
    {
        var root = Directory.CreateTempSubdirectory("sentry-node-test").FullName;
        try
        {
            var (progress, events) = Recorder();
            var result = await new ProcessHarnessAdapter("codex").ExecuteAsync(
                "git status", "readOnly", Workspace(root), progress, TestContext.Current.CancellationToken);

            // Outside a repository git exits non-zero; either way the adapter
            // must report an honest outcome with evidence rather than throw.
            Assert.Contains(result.Outcome, new[] { "succeeded", "failed" });
            Assert.Equal("git status", result.Evidence["command"]);
            Assert.Equal("ws-test", result.Evidence["workspaceId"]);
            Assert.True(result.Evidence.ContainsKey("exitCode"));
            Assert.Contains(events, e => e.Type == "tool.started");
        }
        finally
        {
            Directory.Delete(root, recursive: true);
        }
    }

    [Fact]
    public async Task NeverClaimsMoreThanImplemented()
    {
        var (progress, _) = Recorder();
        var result = await new ProcessHarnessAdapter("codex").ExecuteAsync(
            "git status", "readOnly", Workspace(Path.GetTempPath()), progress, TestContext.Current.CancellationToken);

        // Running a command proves it ran. It does not prove tested or deployed.
        Assert.Equal("implemented", result.StatusBoundary);
    }

    // A chained command must not execute its second half. Arguments are passed
    // as an array with UseShellExecute=false, so the separator is inert.
    [Fact]
    public async Task ChainedCommandDoesNotExecuteTheSecondHalf()
    {
        var root = Directory.CreateTempSubdirectory("sentry-node-chain").FullName;
        var marker = Path.Combine(root, "pwned.txt");
        try
        {
            var (progress, _) = Recorder();
            var result = await new ProcessHarnessAdapter("codex").ExecuteAsync(
                $"git status && echo pwned > {marker}", "readOnly", Workspace(root), progress, TestContext.Current.CancellationToken);

            Assert.False(File.Exists(marker), "the chained command must not have run");
            Assert.NotNull(result);
        }
        finally
        {
            Directory.Delete(root, recursive: true);
        }
    }

    [Fact]
    public async Task CancellationIsReportedRatherThanThrown()
    {
        using var source = new CancellationTokenSource();
        await source.CancelAsync();

        var root = Directory.CreateTempSubdirectory("sentry-node-cancel").FullName;
        try
        {
            var (progress, _) = Recorder();

            // The contract is that cancellation surfaces as a reported outcome,
            // never as an exception escaping the adapter. Whether the process is
            // killed first or exits on its own is a race, so the outcome is not
            // pinned to exactly "cancelled".
            var result = await new ProcessHarnessAdapter("codex").ExecuteAsync(
                "git log", "readOnly", Workspace(root), progress, source.Token);

            Assert.NotNull(result);
            Assert.Contains(result.Outcome, new[] { "cancelled", "succeeded", "failed" });
            Assert.Equal("implemented", result.StatusBoundary);
        }
        finally
        {
            // Best effort: a killed child can briefly hold a handle in the directory.
            try { Directory.Delete(root, recursive: true); } catch (IOException) { }
            catch (UnauthorizedAccessException) { }
        }
    }

    [Fact]
    public void ResolvesGitAndUnixUtilitiesWhenPresent()
    {
        var lsPath = ProcessHarnessAdapter.ResolveExecutable("ls");
        Assert.True(File.Exists(lsPath), $"ls executable should resolve to an existing file, got: {lsPath}");

        var catPath = ProcessHarnessAdapter.ResolveExecutable("cat");
        Assert.True(File.Exists(catPath), $"cat executable should resolve to an existing file, got: {catPath}");

        var gitPath = ProcessHarnessAdapter.ResolveExecutable("git");
        Assert.True(File.Exists(gitPath), $"git executable should resolve to an existing file, got: {gitPath}");

        var bashPath = ProcessHarnessAdapter.ResolveExecutable("bash");
        Assert.True(File.Exists(bashPath), $"bash executable should resolve to an existing file, got: {bashPath}");
    }

    [Fact]
    public void ParseCommandLineHandlesQuotesAndSeparators()
    {
        var parts1 = ProcessHarnessAdapter.ParseCommandLine("git commit -m \"fix: update allowlist\"");
        Assert.Equal(new[] { "git", "commit", "-m", "fix: update allowlist" }, parts1);

        var parts2 = ProcessHarnessAdapter.ParseCommandLine("bash -c 'echo \"hello\"'");
        Assert.Equal(new[] { "bash", "-c", "echo \"hello\"" }, parts2);

        var parts3 = ProcessHarnessAdapter.ParseCommandLine("git status && echo pwned");
        Assert.Equal(new[] { "git", "status", "&&", "echo", "pwned" }, parts3);
    }

    [Fact]
    public async Task RunsLsInWorkspace()
    {
        var root = Directory.CreateTempSubdirectory("sentry-node-ls").FullName;
        try
        {
            var testFile = Path.Combine(root, "marker.txt");
            await File.WriteAllTextAsync(testFile, "sample content", TestContext.Current.CancellationToken);

            var (progress, events) = Recorder();
            var result = await new ProcessHarnessAdapter("shell").ExecuteAsync(
                "ls", "readOnly", Workspace(root), progress, TestContext.Current.CancellationToken);

            Assert.Equal("succeeded", result.Outcome);
            Assert.Contains("marker.txt", (string)result.Evidence["stdout"]);
        }
        finally
        {
            Directory.Delete(root, recursive: true);
        }
    }

    [Fact]
    public async Task RunsChainedAllowlistedCommands()
    {
        var root = Directory.CreateTempSubdirectory("sentry-node-chain-ok").FullName;
        try
        {
            var f1 = Path.Combine(root, "one.txt");
            var f2 = Path.Combine(root, "two.txt");
            await File.WriteAllTextAsync(f1, "1", TestContext.Current.CancellationToken);
            await File.WriteAllTextAsync(f2, "2", TestContext.Current.CancellationToken);

            var (progress, _) = Recorder();
            var result = await new ProcessHarnessAdapter("shell").ExecuteAsync(
                "cat one.txt && cat two.txt", "readOnly", Workspace(root), progress, TestContext.Current.CancellationToken);

            Assert.Equal("succeeded", result.Outcome);
            var stdout = (string)result.Evidence["stdout"];
            Assert.Contains("1", stdout);
            Assert.Contains("2", stdout);
        }
        finally
        {
            Directory.Delete(root, recursive: true);
        }
    }

    [Fact]
    public async Task RefusesChainedCommandIfAnyPartNotAllowed()
    {
        var root = Directory.CreateTempSubdirectory("sentry-node-chain-bad").FullName;
        try
        {
            var (progress, _) = Recorder();
            var result = await new ProcessHarnessAdapter("shell").ExecuteAsync(
                "git status && curl http://attacker", "readOnly", Workspace(root), progress, TestContext.Current.CancellationToken);

            Assert.Equal("failed", result.Outcome);
            Assert.Contains("Refused", result.Summary);
            Assert.Contains("not on the allowlist", result.Summary);
        }
        finally
        {
            Directory.Delete(root, recursive: true);
        }
    }

    [Fact]
    public async Task RunsGitStatusAndGitLogChained()
    {
        var (progress, _) = Recorder();
        var result = await new ProcessHarnessAdapter("shell").ExecuteAsync(
            "git status && git log --oneline -1", "readOnly", Workspace("P:\\SentryAssistant"), progress, TestContext.Current.CancellationToken);

        Assert.Equal("succeeded", result.Outcome);
        var stdout = (string)result.Evidence["stdout"];
        Assert.Contains("branch", stdout);
    }
}

