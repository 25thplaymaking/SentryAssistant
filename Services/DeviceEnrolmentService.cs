using System.Diagnostics;
using System.Text;
using System.Text.RegularExpressions;

namespace SentryAssistant.Services;

/// <summary>
/// Mints an enrolment code and redeems it, so enrolling a device is one action
/// rather than a errand run across two machines.
///
/// Why this needs SSH at all: <c>POST /api/auth/enroll/start</c> requires an
/// already-authenticated caller, which leaves the very first device with
/// nowhere to start. The Gateway deliberately does not punch an
/// unauthenticated hole for that case — it is handled by an operator-run
/// script on the server. So the automated path is "run that script over SSH,
/// then redeem what it prints", which is exactly what a person would otherwise
/// do by hand.
///
/// This never handles a long-lived secret. The code it carries is single-use
/// and expires in five minutes; the durable credential is minted by the
/// Gateway and sealed by <see cref="SettingsService"/>.
/// </summary>
public sealed class DeviceEnrolmentService
{
    // Deployment facts. They live here rather than in settings because they
    // describe the server's layout, not the user's preference — and a wrong
    // value produces a clear SSH error rather than a silent misconfiguration.
    private const string GatewayContainer = "sentry-gateway-1";
    private const string BootstrapScript = "scripts/bootstrap_device.py";

    private static readonly TimeSpan SshBudget = TimeSpan.FromSeconds(45);

    // The script prints "  enrollment code: XXXX-XXXX-XXXX" among other lines.
    private static readonly Regex CodeLine = new(
        @"enrollment\s+code:\s*(?<code>\S+)",
        RegexOptions.IgnoreCase | RegexOptions.Compiled);

    /// <summary>
    /// Ask the server for a fresh enrolment code.
    ///
    /// <paramref name="sshTarget"/> is the <c>user@host</c> the gateway is
    /// reached through — the same value the connections page shows. Returns the
    /// failure reason rather than throwing, because every failure here is
    /// something the person can act on.
    /// </summary>
    public async Task<(bool Success, string? Code, string Detail)> MintCodeAsync(
        string sshTarget,
        string displayName,
        CancellationToken cancellationToken = default)
    {
        if (string.IsNullOrWhiteSpace(sshTarget))
        {
            return (false, null,
                "No SSH host is configured, so a code cannot be requested automatically. "
                + "Set one on the gateway step, or paste a code below.");
        }

        // Single-quoted inside the remote shell, so a display name with spaces
        // stays one argument. Reject quotes outright rather than trying to
        // escape them: a name is not worth a shell-injection surface.
        if (displayName.Contains('\'') || displayName.Contains('"'))
        {
            return (false, null, "The device name cannot contain quotes.");
        }

        var remote =
            $"docker exec {GatewayContainer} python3 {BootstrapScript} "
            + $"--display-name '{displayName}' --device-kind desktop";

        var (ok, stdout, stderr) = await RunSshAsync(sshTarget, remote, cancellationToken);

        if (!ok)
        {
            // Surface the server's own words. "Permission denied (publickey)"
            // and "No such container" need completely different fixes, and
            // flattening them into "enrolment failed" helps nobody.
            var reason = FirstMeaningfulLine(stderr) ?? FirstMeaningfulLine(stdout);
            return (false, null,
                string.IsNullOrWhiteSpace(reason)
                    ? $"Could not reach {sshTarget}."
                    : $"Server refused: {reason}");
        }

        var match = CodeLine.Match(stdout);
        if (!match.Success)
        {
            return (false, null,
                "The server ran the bootstrap but printed no enrolment code.");
        }

        return (true, match.Groups["code"].Value, "Code minted.");
    }

    private static async Task<(bool Ok, string Stdout, string Stderr)> RunSshAsync(
        string target, string remoteCommand, CancellationToken cancellationToken)
    {
        var info = new ProcessStartInfo
        {
            FileName = "ssh.exe",
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            CreateNoWindow = true,
        };

        // BatchMode keeps a missing key from parking on an invisible password
        // prompt: it fails immediately and says why, which is recoverable.
        info.ArgumentList.Add("-o");
        info.ArgumentList.Add("BatchMode=yes");
        info.ArgumentList.Add("-o");
        info.ArgumentList.Add("StrictHostKeyChecking=accept-new");
        info.ArgumentList.Add(target);
        info.ArgumentList.Add(remoteCommand);

        try
        {
            using var process = Process.Start(info);
            if (process is null) return (false, string.Empty, "Could not start ssh.");

            using var deadline = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            deadline.CancelAfter(SshBudget);

            var stdoutTask = process.StandardOutput.ReadToEndAsync();
            var stderrTask = process.StandardError.ReadToEndAsync();

            try
            {
                await process.WaitForExitAsync(deadline.Token);
            }
            catch (OperationCanceledException)
            {
                TryKill(process);
                return (false, string.Empty, $"Timed out after {SshBudget.TotalSeconds:0}s.");
            }

            var stdout = await stdoutTask;
            var stderr = await stderrTask;
            return (process.ExitCode == 0, stdout, stderr);
        }
        catch (Exception exception)
        {
            return (false, string.Empty, exception.Message);
        }
    }

    private static void TryKill(Process process)
    {
        try { process.Kill(entireProcessTree: true); } catch { /* already gone */ }
    }

    /// <summary>
    /// SSH is chatty on failure. Take the first line with real content so the
    /// UI shows the cause rather than a banner.
    /// </summary>
    private static string? FirstMeaningfulLine(string text)
    {
        if (string.IsNullOrWhiteSpace(text)) return null;

        foreach (var raw in text.Split('\n'))
        {
            var line = raw.Trim();
            if (line.Length == 0) continue;
            if (line.StartsWith("Warning: Permanently added", StringComparison.Ordinal)) continue;

            return line.Length > 200 ? line[..200] : line;
        }

        return null;
    }
}
