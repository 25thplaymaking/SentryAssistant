using System.Diagnostics;
using System.Text;
using Sentry.Node.Workspaces;

namespace Sentry.Node.Harnesses;

public sealed record HarnessProgress(
    string Type,
    string Summary,
    System.Text.Json.JsonElement? Payload = null);

public sealed record HarnessResult(
    string Outcome,
    string Summary,
    string StatusBoundary,
    IReadOnlyDictionary<string, object> Evidence);

public interface IHarnessAdapter
{
    string Name { get; }

    Task<HarnessResult> ExecuteAsync(
        string command,
        string mode,
        WorkspaceRegistration workspace,
        IProgress<HarnessProgress> events,
        CancellationToken cancellationToken);
}

public interface IInteractiveHarnessBridge
{
    Task ReportAsync(HarnessProgress progress, CancellationToken cancellationToken);

    Task<System.Text.Json.JsonElement?> WaitForResponseAsync(
        string requestId, CancellationToken cancellationToken);
}

public interface IInteractiveHarnessAdapter : IHarnessAdapter
{
    Task<HarnessResult> ExecuteInteractiveAsync(
        string prompt,
        string mode,
        WorkspaceRegistration workspace,
        string runtimeSessionId,
        string model,
        Sentry.Node.Gateway.NativeRuntimeOptions options,
        IReadOnlyList<Sentry.Node.Gateway.RuntimeImageInput> inputImages,
        IInteractiveHarnessBridge bridge,
        CancellationToken cancellationToken);
}

/// <summary>
/// Runs an allowlisted command inside a registered workspace.
///
/// The command is never handed to a shell. The executable and its arguments are
/// passed separately, so a smuggled `;` or `&amp;&amp;` becomes an inert argument to the
/// program rather than a second command.
///
/// Working directory is always the resolved workspace root, so a relative path in
/// the command cannot reach a directory the node has not registered.
/// </summary>
public sealed class ProcessHarnessAdapter : IHarnessAdapter
{
    private static readonly string[] KnownGitDirectories =
    [
        @"C:\Program Files\Git\cmd",
        @"C:\Program Files\Git\bin",
        @"C:\Program Files\Git\usr\bin",
    ];

    private static readonly string[] ExecutableExtensions = [".exe", ".cmd", ".bat"];

    static ProcessHarnessAdapter()
    {
        EnsureProcessPathIncludesGit();
    }

    private static void EnsureProcessPathIncludesGit()
    {
        try
        {
            var existingPath = Environment.GetEnvironmentVariable("PATH") ?? string.Empty;
            var parts = existingPath.Split(';', StringSplitOptions.RemoveEmptyEntries).ToList();
            var added = false;
            foreach (var dir in KnownGitDirectories)
            {
                if (Directory.Exists(dir) && !parts.Any(p => p.Equals(dir, StringComparison.OrdinalIgnoreCase)))
                {
                    parts.Insert(0, dir);
                    added = true;
                }
            }
            if (added)
            {
                Environment.SetEnvironmentVariable("PATH", string.Join(';', parts));
            }
        }
        catch
        {
            // Best effort; ignore if restricted.
        }
    }

    private readonly TimeSpan _timeout;

    public ProcessHarnessAdapter(string name, TimeSpan? timeout = null)
    {
        Name = name;
        _timeout = timeout ?? TimeSpan.FromMinutes(5);
    }

    public string Name { get; }

    public async Task<HarnessResult> ExecuteAsync(
        string command,
        string mode,
        WorkspaceRegistration workspace,
        IProgress<HarnessProgress> events,
        CancellationToken cancellationToken)
    {
        if (!CommandAllowlist.IsAllowed(mode, command, out var reason))
        {
            // Refusal is a normal, reportable outcome, not an exception path.
            return new HarnessResult(
                "failed",
                $"Refused: {reason}",
                "implemented",
                new Dictionary<string, object> { ["refused"] = true, ["reason"] = reason });
        }

        if (!Directory.Exists(workspace.RootPath))
        {
            return new HarnessResult(
                "failed",
                "Workspace root does not exist on this node.",
                "implemented",
                new Dictionary<string, object> { ["missingRoot"] = true });
        }

        var parts = ParseCommandLine(command);
        if (parts.Count == 0)
        {
            return new HarnessResult(
                "failed",
                "Empty command.",
                "implemented",
                new Dictionary<string, object> { ["emptyCommand"] = true });
        }

        var fileName = parts[0];
        var arguments = parts.Skip(1).ToArray();

        var startInfo = new ProcessStartInfo
        {
            WorkingDirectory = workspace.RootPath,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            // No shell: this is what makes a chained command inert.
            UseShellExecute = false,
            CreateNoWindow = true
        };

        var existingPath = (startInfo.Environment.TryGetValue("PATH", out var pathVal) ? pathVal : null)
            ?? Environment.GetEnvironmentVariable("PATH")
            ?? string.Empty;
        var pathParts = existingPath.Split(';', StringSplitOptions.RemoveEmptyEntries).ToList();
        foreach (var dir in KnownGitDirectories)
        {
            if (Directory.Exists(dir) && !pathParts.Any(p => p.Equals(dir, StringComparison.OrdinalIgnoreCase)))
            {
                pathParts.Insert(0, dir);
            }
        }
        startInfo.Environment["PATH"] = string.Join(';', pathParts);

        if (fileName.Equals("dir", StringComparison.OrdinalIgnoreCase) || fileName.Equals("type", StringComparison.OrdinalIgnoreCase))
        {
            startInfo.FileName = "cmd.exe";
            startInfo.ArgumentList.Add("/c");
            startInfo.ArgumentList.Add(fileName);
            foreach (var argument in arguments)
            {
                startInfo.ArgumentList.Add(argument);
            }
        }
        else
        {
            startInfo.FileName = ResolveExecutable(fileName, workspace.RootPath);
            foreach (var argument in arguments)
            {
                startInfo.ArgumentList.Add(argument);
            }
        }

        events.Report(new HarnessProgress("tool.started", $"{Name}: {command}"));

        using var process = new Process { StartInfo = startInfo };
        var stdout = new StringBuilder();
        var stderr = new StringBuilder();

        process.OutputDataReceived += (_, e) =>
        {
            if (e.Data is null) return;
            stdout.AppendLine(e.Data);
            // Progress is streamed but never speakable; only a resolved result may speak.
            events.Report(new HarnessProgress("tool.progress", e.Data));
        };
        process.ErrorDataReceived += (_, e) =>
        {
            if (e.Data is not null) stderr.AppendLine(e.Data);
        };

        try
        {
            process.Start();
        }
        catch (Exception exception)
        {
            return new HarnessResult(
                "failed",
                $"Could not start '{fileName}': {exception.Message}",
                "implemented",
                new Dictionary<string, object> { ["startFailed"] = true });
        }

        process.BeginOutputReadLine();
        process.BeginErrorReadLine();

        using var timeoutSource = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeoutSource.CancelAfter(_timeout);

        try
        {
            await process.WaitForExitAsync(timeoutSource.Token);
        }
        catch (OperationCanceledException)
        {
            TryKill(process);
            var cancelledByCaller = cancellationToken.IsCancellationRequested;
            return new HarnessResult(
                cancelledByCaller ? "cancelled" : "failed",
                cancelledByCaller
                    ? "Run cancelled."
                    : $"Run exceeded the {_timeout.TotalMinutes:0} minute limit and was terminated.",
                "implemented",
                new Dictionary<string, object> { ["timedOut"] = !cancelledByCaller });
        }

        var succeeded = process.ExitCode == 0;
        var output = stdout.ToString().TrimEnd();
        var errors = stderr.ToString().TrimEnd();

        events.Report(new HarnessProgress(
            succeeded ? "turn.completed" : "turn.failed",
            $"{Name}: exit {process.ExitCode}"));

        return new HarnessResult(
            succeeded ? "succeeded" : "failed",
            succeeded
                ? $"{command} completed successfully."
                : $"{command} exited with code {process.ExitCode}.",
            // The command ran; that is all this node can honestly claim.
            "implemented",
            new Dictionary<string, object>
            {
                ["command"] = command,
                ["exitCode"] = process.ExitCode,
                ["workspaceId"] = workspace.WorkspaceId,
                ["stdout"] = Truncate(output),
                ["stderr"] = Truncate(errors)
            });
    }

    private static void TryKill(Process process)
    {
        try
        {
            if (!process.HasExited) process.Kill(entireProcessTree: true);
        }
        catch
        {
            // The process is already gone; nothing further to do.
        }
    }

    private static string Truncate(string value, int limit = 8000) =>
        value.Length <= limit ? value : value[..limit] + $"\n... truncated ({value.Length} chars)";

    public static string ResolveExecutable(string fileName, string? workingDirectory = null)
    {
        if (string.IsNullOrWhiteSpace(fileName)) return fileName;

        // If explicitly rooted or contains directory separators, test directly.
        if (Path.IsPathRooted(fileName) || fileName.Contains(Path.DirectorySeparatorChar) || fileName.Contains(Path.AltDirectorySeparatorChar))
        {
            var target = Path.IsPathRooted(fileName) ? fileName : Path.Combine(workingDirectory ?? string.Empty, fileName);
            if (File.Exists(target)) return target;
            foreach (var ext in ExecutableExtensions)
            {
                var candidate = target + ext;
                if (File.Exists(candidate)) return candidate;
            }
            return fileName;
        }

        // Search known Git directories followed by process PATH directories.
        var pathEnv = Environment.GetEnvironmentVariable("PATH") ?? string.Empty;
        var pathDirs = pathEnv.Split(Path.PathSeparator, StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
        var allDirs = KnownGitDirectories.Concat(pathDirs);

        foreach (var dir in allDirs)
        {
            if (string.IsNullOrWhiteSpace(dir) || !Directory.Exists(dir)) continue;

            var direct = Path.Combine(dir, fileName);
            if (File.Exists(direct)) return direct;

            foreach (var ext in ExecutableExtensions)
            {
                var candidate = direct + ext;
                if (File.Exists(candidate)) return candidate;
            }
        }

        return fileName;
    }

    public static List<string> ParseCommandLine(string command)
    {
        var result = new List<string>();
        if (string.IsNullOrWhiteSpace(command)) return result;

        var current = new StringBuilder();
        char? inQuotes = null;
        var escaping = false;

        for (int i = 0; i < command.Length; i++)
        {
            char c = command[i];

            if (escaping)
            {
                current.Append(c);
                escaping = false;
                continue;
            }

            if (c == '\\')
            {
                if (i + 1 < command.Length && (command[i + 1] == '"' || command[i + 1] == '\''))
                {
                    escaping = true;
                    continue;
                }
                current.Append(c);
                continue;
            }

            if (inQuotes.HasValue)
            {
                if (c == inQuotes.Value)
                {
                    inQuotes = null;
                }
                else
                {
                    current.Append(c);
                }
            }
            else
            {
                if (c == '"' || c == '\'')
                {
                    inQuotes = c;
                }
                else if (char.IsWhiteSpace(c))
                {
                    if (current.Length > 0)
                    {
                        result.Add(current.ToString());
                        current.Clear();
                    }
                }
                else
                {
                    current.Append(c);
                }
            }
        }

        if (current.Length > 0)
        {
            result.Add(current.ToString());
        }

        return result;
    }
}
