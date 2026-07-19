using System.Diagnostics;
using System.Text;
using Sentry.Node.Workspaces;

namespace Sentry.Node.Harnesses;

/// <summary>
/// Runs Claude Code headlessly as a Sentry harness.
///
/// Invoked with an explicit settings file so it never inherits the interactive
/// installation's configuration — in particular not
/// <c>skipDangerousModePermissionPrompt</c>. Tools are allowlisted per
/// work-order mode, so a ReadOnly order cannot edit or run commands even if the
/// prompt asks it to.
///
/// The harness runs with its working directory pinned to the resolved workspace
/// root, so it cannot reach a directory this node has not registered.
/// </summary>
public sealed class ClaudeAdapter : IHarnessAdapter
{
    // ReadOnly gets inspection tools only. No Edit, Write, or Bash.
    private static readonly string[] ReadOnlyTools = ["Read", "Glob", "Grep"];

    // WorkspaceWrite adds editing and a bounded set of commands. The Bash
    // allowlist is per-verb; the node's CommandAllowlist is the second gate.
    private static readonly string[] WorkspaceWriteTools =
    [
        "Read", "Glob", "Grep", "Edit", "Write",
        "Bash(git status:*)", "Bash(git diff:*)", "Bash(git log:*)",
        "Bash(git add:*)", "Bash(git commit:*)",
        "Bash(dotnet build:*)", "Bash(dotnet test:*)"
    ];

    private readonly string _executable;
    private readonly string _settingsPath;
    private readonly TimeSpan _timeout;

    public ClaudeAdapter(string executable, string settingsPath, TimeSpan? timeout = null)
    {
        _executable = executable;
        _settingsPath = settingsPath;
        _timeout = timeout ?? TimeSpan.FromMinutes(15);
    }

    public string Name => "claude";

    public async Task<HarnessResult> ExecuteAsync(
        string prompt,
        string mode,
        WorkspaceRegistration workspace,
        IProgress<HarnessProgress> events,
        CancellationToken cancellationToken)
    {
        // Elevated work is approved per action. This adapter never runs it.
        if (mode == "approvedElevated")
        {
            return new HarnessResult(
                "failed",
                "Refused: elevated work requires a per-action approval this harness does not implement.",
                "implemented",
                new Dictionary<string, object> { ["refused"] = true });
        }

        var allowedTools = mode switch
        {
            "readOnly" => ReadOnlyTools,
            "workspaceWrite" => WorkspaceWriteTools,
            _ => []
        };

        if (allowedTools.Length == 0)
        {
            return new HarnessResult(
                "failed",
                $"Refused: mode '{mode}' grants no tools.",
                "implemented",
                new Dictionary<string, object> { ["refused"] = true });
        }

        if (!Directory.Exists(workspace.RootPath))
        {
            return new HarnessResult(
                "failed",
                "Workspace root does not exist on this node.",
                "implemented",
                new Dictionary<string, object> { ["missingRoot"] = true });
        }

        var startInfo = new ProcessStartInfo
        {
            FileName = _executable,
            WorkingDirectory = workspace.RootPath,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true
        };

        startInfo.ArgumentList.Add("-p");
        startInfo.ArgumentList.Add(prompt);
        startInfo.ArgumentList.Add("--output-format");
        startInfo.ArgumentList.Add("stream-json");
        startInfo.ArgumentList.Add("--verbose");
        // A dedicated settings file, so the interactive install's permissions
        // and dangerous-mode flags are not inherited.
        startInfo.ArgumentList.Add("--settings");
        startInfo.ArgumentList.Add(_settingsPath);
        startInfo.ArgumentList.Add("--allowed-tools");
        startInfo.ArgumentList.Add(string.Join(",", allowedTools));

        events.Report(new HarnessProgress("tool.started", $"claude ({mode})"));

        using var process = new Process { StartInfo = startInfo };
        var transcript = new List<string>();
        HarnessStreamEvent? terminal = null;
        var stderr = new StringBuilder();

        process.OutputDataReceived += (_, e) =>
        {
            if (e.Data is null) return;
            var parsed = ClaudeStreamParser.ParseLine(e.Data);
            if (parsed is null) return;

            lock (transcript)
            {
                transcript.Add($"{parsed.Type}: {parsed.Summary}");
                if (parsed.IsTerminal) terminal = parsed;
            }

            // Progress is streamed to the UI but is never speakable; only a
            // resolved result may reach a speech provider.
            events.Report(new HarnessProgress(parsed.Type, parsed.Summary));
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
                $"Could not start the Claude harness: {exception.Message}",
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
            try { if (!process.HasExited) process.Kill(entireProcessTree: true); } catch { }
            var byCaller = cancellationToken.IsCancellationRequested;
            return new HarnessResult(
                byCaller ? "cancelled" : "failed",
                byCaller ? "Harness run cancelled." : $"Harness exceeded {_timeout.TotalMinutes:0} minutes.",
                "implemented",
                new Dictionary<string, object> { ["timedOut"] = !byCaller });
        }

        // A run only succeeds if an explicit success result arrived. A clean exit
        // code with a truncated stream is still a failure.
        var succeeded = terminal is { IsTerminal: true, IsError: false } && process.ExitCode == 0;

        return new HarnessResult(
            succeeded ? "succeeded" : "failed",
            terminal?.Summary
                ?? (stderr.Length > 0
                    ? $"Harness produced no result. {Truncate(stderr.ToString(), 500)}"
                    : "Harness produced no result event."),
            "implemented",
            new Dictionary<string, object>
            {
                ["harness"] = Name,
                ["mode"] = mode,
                ["allowedTools"] = allowedTools,
                ["exitCode"] = process.ExitCode,
                ["workspaceId"] = workspace.WorkspaceId,
                ["events"] = transcript.Count,
                ["transcript"] = Truncate(string.Join("\n", transcript), 8000)
            });
    }

    private static string Truncate(string value, int limit) =>
        value.Length <= limit ? value : value[..limit] + "... truncated";
}
