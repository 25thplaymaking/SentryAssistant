using System.Diagnostics;
using System.Net.Http;

namespace SentryShell;

/// <summary>
/// Owns the SSH local forward that the UI rides on.
///
/// This replaces the Scheduled Task + sentry-tunnel.ps1 pair. Same ssh
/// invocation, same supervision policy, but hosted inside the app so there is
/// no PowerShell process and no console to flash.
/// </summary>
internal sealed class Tunnel : IDisposable
{
    // Loopback-only on the server; reached over the forward, never published.
    // The destination is CONFIGURED, not compiled in: a distributed copy points
    // at the teammate's own restricted account, never the owner's admin one.
    private static readonly TunnelConfig Config = TunnelConfig.Load();
    private static string Target => Config.SshTarget;
    private const string RemoteAddr  = "127.0.0.1";   // loopback on the SERVER, where IPv4 works
    internal const int   GatewayPort = 8090;
    internal const int   WebUiPort   = 8787;
    internal const int   ServerControlLocalPort = 17443;
    private const int    ServerControlRemotePort = 7443;

    // MUST bind [::1], not 127.0.0.1. IPv4 loopback is broken on this machine:
    // a client "connects" to 127.0.0.1 but is silently forced onto the IPv6
    // stack, and a listener bound to IPv4 never accepts it. Getting this wrong
    // yields a live tunnel, a healthy gateway, and a UI that shows nothing.
    private const string BindAddr = "[::1]";

    internal static string WebUiUrl => $"http://{BindAddr}:{WebUiPort}/";

    private static readonly HttpClient Probe = new() { Timeout = TimeSpan.FromSeconds(6) };
    private static readonly string SshExe =
        Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "OpenSSH", "ssh.exe");

    private readonly string _log;
    private readonly CancellationTokenSource _cts = new();
    private readonly ChildJob _job = new();
    private readonly string _pidFile;
    private Process? _ssh;

    /// <summary>True when we started ssh ourselves, false when we adopted an existing forward.</summary>
    internal bool OwnsProcess { get; private set; }

    internal Tunnel()
    {
        // Areas are provisioned once at startup (AppPaths.EnsureAll); create the
        // root again here so a Tunnel is still safe to construct on its own.
        Directory.CreateDirectory(AppPaths.Root);
        _log = AppPaths.ShellLog;
        _pidFile = AppPaths.PidFile;
        if (!_job.Ready) Log("WARNING: job object unavailable; ssh will only be cleaned up on graceful exit");
        ReapOrphan();
    }

    /// <summary>
    /// Kill an ssh we started in a previous run that outlived its parent.
    /// The job object prevents new orphans, but a build from before that fix —
    /// or a job-object failure — can still leave one holding the ports, and
    /// ExitOnForwardFailure would then fail our dial.
    /// </summary>
    private void ReapOrphan()
    {
        try
        {
            if (!File.Exists(_pidFile)) return;
            if (int.TryParse(File.ReadAllText(_pidFile).Trim(), out var pid))
            {
                try
                {
                    var p = Process.GetProcessById(pid);
                    // Only ever kill an ssh — the PID may have been recycled.
                    if (p.ProcessName.Equals("ssh", StringComparison.OrdinalIgnoreCase))
                    {
                        p.Kill(entireProcessTree: true);
                        Log($"reaped orphaned ssh pid {pid} from a previous run");
                    }
                }
                catch (ArgumentException) { /* already gone — the normal case */ }
            }
            File.Delete(_pidFile);
        }
        catch (Exception ex) { Log($"orphan reap failed: {ex.Message}"); }
    }

    internal void Log(string message)
    {
        try
        {
            File.AppendAllText(_log, $"{DateTime.Now:yyyy-MM-dd HH:mm:ss}  {message}{Environment.NewLine}");
            // keep an unattended reconnect loop from filling the disk over months
            var fi = new FileInfo(_log);
            if (fi.Length > 1_000_000)
                File.WriteAllLines(_log, File.ReadLines(_log).TakeLast(500).ToArray());
        }
        catch { /* logging must never take the UI down */ }
    }

    /// <summary>
    /// Ask the service, not the OS. A bound socket only proves ssh is running;
    /// it does not prove the forward still reaches the far end.
    /// </summary>
    internal static async Task<bool> IsUpAsync()
    {
        try
        {
            using var r = await Probe.GetAsync(WebUiUrl, HttpCompletionOption.ResponseHeadersRead);
            return true;   // 200 or the 302 to /login both mean the far end answered
        }
        catch { return false; }
    }

    /// <summary>
    /// Bring the forward up if it is not already, then keep it up until disposed.
    /// Returns false if it could not be established within <paramref name="timeout"/>.
    /// </summary>
    internal async Task<bool> StartAsync(TimeSpan timeout, IProgress<string>? status = null)
    {
        if (await IsUpAsync())
        {
            // The old Scheduled Task may still be running and holding the ports.
            // Don't fight it — ExitOnForwardFailure would just fail our dial.
            Log("adopted an existing forward; not starting ssh");
            OwnsProcess = false;
            _ = Task.Run(SuperviseAsync);
            return true;
        }

        status?.Report("Connecting to grain.silo…");
        Spawn();
        OwnsProcess = true;

        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline)
        {
            if (await IsUpAsync()) { Log("forward is up"); _ = Task.Run(SuperviseAsync); return true; }
            if (_ssh is { HasExited: true })
            {
                Log($"ssh exited early with code {_ssh.ExitCode}; retrying");
                await Task.Delay(1500);
                Spawn();
            }
            await Task.Delay(500);
        }
        Log("timed out waiting for the forward");
        return false;
    }

    private void Spawn()
    {
        var psi = new ProcessStartInfo
        {
            FileName = SshExe,
            // CreateNoWindow + UseShellExecute=false is what makes this silent.
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardError = true,
        };
        var args = new List<string>
        {
            "-N",
            "-o", "ExitOnForwardFailure=yes",
            "-o", "ServerAliveInterval=30",
            "-o", "ServerAliveCountMax=3",
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=accept-new",
        };
        if (!string.IsNullOrWhiteSpace(Config.IdentityFile))
        {
            // IdentitiesOnly stops ssh offering every agent key first and
            // tripping MaxAuthTries before it reaches the one that works.
            args.Add("-i"); args.Add(Config.IdentityFile);
            args.Add("-o"); args.Add("IdentitiesOnly=yes");
        }
        // The Gateway forward is owner-only. A teammate's key is authorised for
        // the WebUI port alone, and ExitOnForwardFailure means asking for a port
        // you may not have would fail the ENTIRE dial, not just that forward.
        if (Config.ForwardGateway)
        {
            args.Add("-L"); args.Add($"{BindAddr}:{GatewayPort}:{RemoteAddr}:{GatewayPort}");
        }
        if (Config.ForwardServerControl)
        {
            args.Add("-L");
            args.Add($"{BindAddr}:{ServerControlLocalPort}:{RemoteAddr}:{ServerControlRemotePort}");
        }
        // The forwards ride one connection. ExitOnForwardFailure also means a
        // port already in use fails the dial rather than silently bringing up a
        // half-working tunnel.
        args.Add("-L"); args.Add($"{BindAddr}:{WebUiPort}:{RemoteAddr}:{WebUiPort}");
        args.Add(Target);
        foreach (var a in args) psi.ArgumentList.Add(a);

        try
        {
            _ssh = Process.Start(psi);
            if (_ssh is not null)
            {
                // Job membership BEFORE anything else can go wrong: from here
                // on, ssh cannot outlive this process even if we are killed.
                _job.Add(_ssh);
                try { File.WriteAllText(_pidFile, _ssh.Id.ToString()); } catch { }
            }
            Log($"dialed {Target} (pid {_ssh?.Id})");
        }
        catch (Exception ex) { Log($"failed to start ssh: {ex.Message}"); }
    }

    /// <summary>
    /// ssh exits on network loss, so supervision is a loop rather than one-shot.
    /// Exponential backoff, capped, so a server outage is not a dial storm.
    /// </summary>
    private async Task SuperviseAsync()
    {
        var backoff = TimeSpan.FromSeconds(5);
        while (!_cts.IsCancellationRequested)
        {
            await Task.Delay(TimeSpan.FromSeconds(20), _cts.Token).ContinueWith(_ => { });
            if (_cts.IsCancellationRequested) return;
            if (await IsUpAsync()) { backoff = TimeSpan.FromSeconds(5); continue; }

            Log("forward went down; redialling");
            KillSsh();
            Spawn();
            OwnsProcess = true;
            await Task.Delay(backoff, _cts.Token).ContinueWith(_ => { });
            backoff = TimeSpan.FromSeconds(Math.Min(backoff.TotalSeconds * 2, 120));
        }
    }

    private void KillSsh()
    {
        try { if (_ssh is { HasExited: false }) _ssh.Kill(entireProcessTree: true); }
        catch { /* already gone */ }
        _ssh = null;
    }

    public void Dispose()
    {
        _cts.Cancel();
        // Leaving ssh behind is exactly the leak that hammered the machine
        // before, so the child dies with the window that started it. The job
        // object below is the backstop for exits that never reach this method.
        KillSsh();
        try { if (File.Exists(_pidFile)) File.Delete(_pidFile); } catch { }
        _job.Dispose();
        Log("shell exited");
    }
}
