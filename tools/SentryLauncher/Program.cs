using System.Diagnostics;
using System.Net.Http.Json;
using System.Text.Json;

namespace SentryLauncher;

/// <summary>
/// One entry point for starting Sentry.
///
/// Sentry spans three layers that fail independently — an SSH tunnel on this
/// machine, the Gateway on grain.silo, and the Hermes runtime behind it. Any
/// one of them being down presents identically in the desktop app: a window
/// that says "offline". This checks them in dependency order so the first
/// failure names the actual layer instead of the symptom.
/// </summary>
internal static class Program
{
    // The Gateway is loopback-only on the server and reached through an SSH
    // forward. The forward MUST be IPv6: IPv4 loopback is broken on this
    // machine — a client "connects" to 127.0.0.1 but is silently forced onto
    // the IPv6 stack and a listener bound to 127.0.0.1 never accepts it.
    // Using 127.0.0.1 here yields a live tunnel and an app that reports
    // offline with no useful error.
    private const string GatewayUrl = "http://[::1]:8090";
    private const string TunnelTask = "SentryGatewayTunnel";
    private const string SshTarget = "bishop@205.209.116.114";
    private const string RemoteComposeDir = "/srv/sentry/repo/deploy/linux";

    private static readonly HttpClient Http = new() { Timeout = TimeSpan.FromSeconds(10) };

    private static async Task<int> Main(string[] args)
    {
        var check = args.Contains("--check") || args.Contains("-c");
        var repair = args.Contains("--repair") || args.Contains("-r");

        if (args.Contains("--help") || args.Contains("-h"))
        {
            Console.WriteLine("""
                Sentry launcher

                  Sentry            Preflight all layers, then open the desktop app.
                  Sentry --check    Preflight only. Prints status and exits. No window.
                  Sentry --repair   Also try to restart the remote stack over SSH.
                  Sentry --help     This text.

                Exit codes: 0 healthy · 1 tunnel · 2 gateway · 3 runtime · 4 app missing
                """);
            return 0;
        }

        Banner();

        // 1. Tunnel. Everything else is unreachable without it, so it is
        //    checked and repaired first.
        if (!await GatewayReachable())
        {
            Step("Tunnel", "down — starting…", ConsoleColor.Yellow);
            if (!StartTunnel())
            {
                Fail("Could not start the tunnel task.",
                     $"Check it exists:  Get-ScheduledTask {TunnelTask}");
                return 1;
            }

            if (!await WaitFor(GatewayReachable, seconds: 45))
            {
                Fail("Tunnel started but the Gateway never answered.",
                     $"Look at the log:  {TunnelLogPath()}",
                     $"Test by hand:     ssh {SshTarget} 'curl -s localhost:8090/health/ready'");
                return 1;
            }
            Step("Tunnel", "up", ConsoleColor.Green);
        }
        else
        {
            Step("Tunnel", "up", ConsoleColor.Green);
        }

        // 2. Gateway and 3. runtime arrive in the same payload: /health/ready
        //    reports its own state plus the runtime it depends on, so one call
        //    distinguishes "gateway down" from "gateway up, Hermes degraded".
        var health = await ReadHealth();
        if (health is null)
        {
            if (repair && await Repair())
            {
                health = await ReadHealth();
            }

            if (health is null)
            {
                Fail("Gateway is not answering.",
                     repair ? "Repair did not bring it back." : "Try again with --repair.",
                     $"Or by hand:  ssh {SshTarget} 'cd {RemoteComposeDir} && docker compose ps'");
                return 2;
            }
        }

        Step("Gateway", health.Value.Ready ? "ready" : "not ready",
             health.Value.Ready ? ConsoleColor.Green : ConsoleColor.Red);

        if (!health.Value.RuntimeHealthy)
        {
            var why = string.IsNullOrWhiteSpace(health.Value.DegradedReason)
                ? "no reason given"
                : health.Value.DegradedReason;
            Step("Hermes", $"degraded — {why}", ConsoleColor.Red);

            if (repair && await Repair() && (await ReadHealth())?.RuntimeHealthy == true)
            {
                Step("Hermes", "recovered", ConsoleColor.Green);
            }
            else
            {
                Fail("The runtime is not healthy, so the assistant cannot answer.",
                     repair ? "Repair did not fix it." : "Try again with --repair.",
                     $"Logs:  ssh {SshTarget} 'docker logs --tail 50 sentry-hermes-1'");
                return 3;
            }
        }
        else
        {
            Step("Hermes", $"healthy (v{health.Value.RuntimeVersion})", ConsoleColor.Green);
        }

        // 4. Desktop app. Resolved even under --check so the check exercises
        //    the same lookup the real launch uses — a check that skipped it
        //    would report all-clear right up until the moment it matters.
        var app = FindApp();

        if (check)
        {
            Step("Desktop", app is null ? "NOT BUILT" : "found",
                 app is null ? ConsoleColor.Red : ConsoleColor.Green);
            if (app is not null) Console.WriteLine($"            {app}");

            Console.WriteLine();
            if (app is null)
            {
                Fail("Everything upstream is healthy, but the desktop app is not built.",
                     "Build it:  & 'C:\\Program Files\\dotnet\\dotnet.exe' build SentryAssistant.csproj -c Debug -p:Platform=x64");
                return 4;
            }

            Ok("All layers healthy. (--check: not opening the app.)");
            return 0;
        }

        if (app is null)
        {
            Fail("Everything upstream is healthy, but the desktop app is not built.",
                 "Build it:  & 'C:\\Program Files\\dotnet\\dotnet.exe' build SentryAssistant.csproj -c Debug -p:Platform=x64");
            return 4;
        }

        Step("Desktop", "starting…", ConsoleColor.Green);
        try
        {
            // WorkingDirectory matters: WinUI resolves its resources relative
            // to the executable, and launching from elsewhere fails oddly.
            Process.Start(new ProcessStartInfo
            {
                FileName = app,
                WorkingDirectory = Path.GetDirectoryName(app)!,
                UseShellExecute = true,
            });
        }
        catch (Exception ex)
        {
            Fail($"Could not start the app: {ex.Message}");
            return 4;
        }

        Console.WriteLine();
        Ok("Sentry is up.");
        return 0;
    }

    private readonly record struct Health(
        bool Ready, bool RuntimeHealthy, string? RuntimeVersion, string? DegradedReason);

    private static async Task<Health?> ReadHealth()
    {
        try
        {
            // Read the body whatever the status. The Gateway answers 503 when
            // the runtime is degraded, and that response carries exactly the
            // diagnosis we want (healthy:false + degradedReason). Treating a
            // non-2xx as a failure would throw that away and report the
            // symptom as an unreachable tunnel — the precise confusion this
            // launcher exists to prevent. Only a transport-level failure,
            // handled below, means the tunnel is actually down.
            using var response = await Http.GetAsync($"{GatewayUrl}/health/ready");
            var payload = await response.Content.ReadAsStringAsync();
            if (string.IsNullOrWhiteSpace(payload)) return null;

            using var doc = JsonDocument.Parse(payload);
            var root = doc.RootElement;
            var ready = root.TryGetProperty("status", out var s)
                        && s.GetString() == "ready";

            bool healthy = false;
            string? version = null, reason = null;
            if (root.TryGetProperty("checks", out var checks)
                && checks.TryGetProperty("runtime", out var rt))
            {
                healthy = rt.TryGetProperty("healthy", out var h) && h.GetBoolean();
                version = rt.TryGetProperty("pinnedVersion", out var v) ? v.GetString() : null;
                reason = rt.TryGetProperty("degradedReason", out var d)
                         && d.ValueKind != JsonValueKind.Null
                    ? d.GetString()
                    : null;
            }

            return new Health(ready, healthy, version, reason);
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            // Genuinely could not reach the far end: no tunnel, or nothing
            // listening. Unreachable is a state, not an exception — the caller
            // decides what it means.
            return null;
        }
        catch (JsonException)
        {
            // Something answered but it was not the Gateway. Treating this as
            // unreachable would send the operator hunting the tunnel, so let
            // it surface as a gateway-layer failure instead.
            return new Health(false, false, null, "gateway returned unparseable response");
        }
    }

    /// <summary>
    /// Whether anything answered on the forwarded port at all. Deliberately
    /// distinct from "healthy": a degraded Gateway is still reachable, and
    /// conflating the two is what makes a runtime outage look like a tunnel
    /// outage.
    /// </summary>
    private static async Task<bool> GatewayReachable() => await ReadHealth() is not null;

    private static bool StartTunnel()
    {
        try
        {
            var p = Process.Start(new ProcessStartInfo
            {
                FileName = "schtasks.exe",
                Arguments = $"/Run /TN \"{TunnelTask}\"",
                UseShellExecute = false,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                CreateNoWindow = true,
            });
            p?.WaitForExit(15_000);
            return p is { ExitCode: 0 };
        }
        catch
        {
            return false;
        }
    }

    /// <summary>
    /// Bring the remote stack back. Only ever runs behind --repair: restarting
    /// someone's services is not something a launcher should do silently.
    /// Scoped to the Sentry compose project by absolute path — the 25vid
    /// production stack lives in a different directory on the same host and
    /// must never be touched.
    /// </summary>
    private static async Task<bool> Repair()
    {
        Step("Repair", "restarting remote stack…", ConsoleColor.Yellow);
        try
        {
            var p = Process.Start(new ProcessStartInfo
            {
                FileName = "ssh.exe",
                Arguments = $"-o BatchMode=yes {SshTarget} "
                          + $"\"cd {RemoteComposeDir} && docker compose up -d\"",
                UseShellExecute = false,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                CreateNoWindow = true,
            });
            if (p is null) return false;
            await p.WaitForExitAsync();
            if (p.ExitCode != 0) return false;

            // Wait for the RUNTIME to be healthy, not merely for the Gateway to
            // answer. When Hermes is the thing that is down the Gateway is
            // already reachable — it is serving 503 — so waiting on
            // reachability returns instantly and we would judge the repair a
            // failure while the container is still booting.
            return await WaitFor(
                async () => (await ReadHealth())?.RuntimeHealthy == true,
                seconds: 90);
        }
        catch
        {
            return false;
        }
    }

    private static async Task<bool> WaitFor(Func<Task<bool>> condition, int seconds)
    {
        var until = DateTime.UtcNow.AddSeconds(seconds);
        while (DateTime.UtcNow < until)
        {
            if (await condition()) return true;
            await Task.Delay(2000);
        }
        return false;
    }

    private static string? FindApp()
    {
        // An explicit override wins, so an install that lives anywhere still works.
        var env = Environment.GetEnvironmentVariable("SENTRY_APP_PATH");
        if (!string.IsNullOrWhiteSpace(env) && File.Exists(env)) return env;

        // Next to the launcher, so a published bundle is self-contained.
        var here = AppContext.BaseDirectory;
        var beside = Path.Combine(here, "SentryAssistant.exe");
        if (File.Exists(beside)) return beside;

        // Otherwise walk up looking for the development build output. Walking
        // beats a fixed "..\..\.." depth: the launcher can be run from dist/,
        // from bin/Release/, or from a copy elsewhere in the tree, and a
        // hard-coded depth silently resolves to the wrong directory in all but
        // one of those.
        var dir = new DirectoryInfo(here);
        for (var hop = 0; dir is not null && hop < 6; hop++, dir = dir.Parent)
        {
            foreach (var config in new[] { "Debug", "Release" })
            {
                var candidate = Path.Combine(
                    dir.FullName, "bin", "x64", config,
                    "net10.0-windows10.0.26100.0", "win-x64", "SentryAssistant.exe");
                if (File.Exists(candidate)) return candidate;
            }
        }

        return null;
    }

    private static string TunnelLogPath() => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "SentryAssistant", "tunnel.log");

    private static void Banner()
    {
        Console.ForegroundColor = ConsoleColor.Cyan;
        Console.WriteLine();
        Console.WriteLine("  SENTRY");
        Console.ResetColor();
        Console.WriteLine();
    }

    private static void Step(string label, string state, ConsoleColor colour)
    {
        Console.Write($"  {label,-9} ");
        Console.ForegroundColor = colour;
        Console.WriteLine(state);
        Console.ResetColor();
    }

    private static void Ok(string message)
    {
        Console.ForegroundColor = ConsoleColor.Green;
        Console.WriteLine($"  {message}");
        Console.ResetColor();
    }

    private static void Fail(params string[] lines)
    {
        Console.WriteLine();
        Console.ForegroundColor = ConsoleColor.Red;
        Console.WriteLine($"  {lines[0]}");
        Console.ResetColor();
        foreach (var line in lines.Skip(1))
        {
            Console.WriteLine($"    {line}");
        }
        Console.WriteLine();
    }
}
