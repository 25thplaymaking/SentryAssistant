namespace Sentry.Node.Harnesses;

/// <summary>
/// What a work-order mode is permitted to run.
///
/// The allowlist is positive: a command is refused unless it is explicitly
/// listed. Arguments are passed as an array and never through a shell, so there
/// is no string for an injected separator to split.
/// </summary>
public sealed class CommandAllowlist
{
    // ReadOnly maps to inspection only. Nothing here can mutate a workspace.
    private static readonly IReadOnlySet<string> ReadOnlyCommands =
        new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            "git status", "git log", "git diff", "git show", "git branch",
            "git rev-parse", "git ls-files", "git describe", "git cat-file",
            "git fetch", "git blame", "git shortlog", "git check-ref-format",
            "python --version", "node --version", "dotnet --version", "cargo --version",
            "dir", "ls", "cat", "type", "head", "tail", "more", "wc",
            "rg", "ripgrep", "grep", "findstr", "find", "fd",
            "where", "which", "echo"
        };

    // WorkspaceWrite additionally allows bounded build, test, and execution verbs.
    private static readonly IReadOnlySet<string> WorkspaceWriteCommands =
        new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            "git add", "git commit", "git checkout", "git switch", "git restore",
            "git merge", "git stash",
            "dotnet build", "dotnet test", "dotnet restore", "dotnet run", "dotnet",
            "npm ci", "npm run", "npm test", "npm install", "npx", "pnpm", "yarn",
            "pytest", "python -m", "pip install", "uv run", "uv", "python", "node",
            "cargo build", "cargo test", "cargo check", "cargo run", "cargo",
            "xmake", "cmake", "msbuild",
            "mkdir", "cp", "copy", "touch"
        };

    // Verbs that reach outside the machine or destroy history are never allowed
    // by mode alone; they require an explicit per-run approval that this node
    // does not implement, so they stay refused.
    private static readonly IReadOnlySet<string> NeverAllowed =
        new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            "git push", "git reset", "git clean", "git remote",
            "npm publish", "dotnet nuget push"
        };

    public static bool IsAllowed(string mode, string command, out string reason)
    {
        var normalized = Normalize(command);

        if (NeverAllowed.Any(blocked => normalized.StartsWith(blocked, StringComparison.OrdinalIgnoreCase)))
        {
            reason = $"'{normalized}' is never permitted without an explicit per-run approval.";
            return false;
        }

        var permitted = mode switch
        {
            "readOnly" => ReadOnlyCommands,
            "workspaceWrite" => ReadOnlyCommands.Concat(WorkspaceWriteCommands).ToHashSet(StringComparer.OrdinalIgnoreCase),
            // Elevated work is approved per action, not per mode. Nothing is
            // implicitly runnable here.
            "approvedElevated" => new HashSet<string>(StringComparer.OrdinalIgnoreCase),
            _ => new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        };

        if (!permitted.Any(allowed => normalized.StartsWith(allowed, StringComparison.OrdinalIgnoreCase)))
        {
            reason = $"'{normalized}' is not on the allowlist for mode '{mode}'.";
            return false;
        }

        reason = string.Empty;
        return true;
    }

    /// <summary>
    /// Collapses whitespace so " git   status " and "git status" compare equal.
    /// Matching is on the leading verb, so flags do not defeat the allowlist.
    /// </summary>
    private static string Normalize(string command) =>
        string.Join(' ', command.Split(' ', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries));
}
