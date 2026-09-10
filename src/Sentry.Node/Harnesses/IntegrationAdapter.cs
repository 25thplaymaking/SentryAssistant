using System.Diagnostics;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using Sentry.Node.Gateway;
using Sentry.Node.Workspaces;

namespace Sentry.Node.Harnesses;

/// <summary>
/// Read-only bridge for local provider sessions, repository state, and explicit
/// IDE handoff. Every action arrives through the same signed, outbound-only
/// work-order path as native Codex. Raw local paths and provider credentials are
/// never returned to the Gateway.
/// </summary>
public sealed partial class IntegrationAdapter : IInteractiveHarnessAdapter
{
    private const int MaxSessionsPerProvider = 40;
    private const int MaxTranscriptMessages = 200;
    private const int MaxTranscriptCharacters = 120_000;
    private const int MaxDiffCharacters = 160_000;
    private static readonly JsonSerializerOptions Json = new(JsonSerializerDefaults.Web);
    private readonly string _userProfile;

    public IntegrationAdapter(string? userProfile = null)
    {
        _userProfile = Path.GetFullPath(
            userProfile ?? Environment.GetFolderPath(Environment.SpecialFolder.UserProfile));
    }

    public string Name => "integrations";

    public NativeRuntimeRegistration Registration()
    {
        var providers = SupportedProviders()
            .Select(item => new Dictionary<string, object>
            {
                ["id"] = item.Id,
                ["label"] = item.Label,
                ["available"] = Directory.Exists(item.Root),
                ["history_scope"] = "local-installation"
            })
            .ToList();
        var ides = DetectIdes()
            .Select(item => new Dictionary<string, object>
            {
                ["id"] = item.Key,
                ["label"] = item.Key == "cursor" ? "Cursor" : "Visual Studio Code",
                ["available"] = true
            })
            .ToList();
        var available = providers.Any(item => (bool)item["available"])
            || ides.Count > 0;
        return new NativeRuntimeRegistration(
            available,
            "1",
            "local-user",
            [],
            ["provider-sessions", "live-session-view", "workspace-diff", "github", "ide-handoff"],
            available ? null : "No supported local provider history or IDE was found.",
            new Dictionary<string, object>
            {
                ["providers"] = providers,
                ["ides"] = ides
            });
    }

    public Task<HarnessResult> ExecuteAsync(
        string command,
        string mode,
        WorkspaceRegistration workspace,
        IProgress<HarnessProgress> events,
        CancellationToken cancellationToken) =>
        Task.FromResult(Failed("Integration actions require signed structured options."));

    public async Task<HarnessResult> ExecuteInteractiveAsync(
        string prompt,
        string mode,
        WorkspaceRegistration workspace,
        string runtimeSessionId,
        string model,
        NativeRuntimeOptions options,
        IReadOnlyList<RuntimeImageInput> inputImages,
        IInteractiveHarnessBridge bridge,
        CancellationToken cancellationToken)
    {
        if (mode != "readOnly" || options.Sandbox != "readOnly")
            return Failed("Integration actions are read-only.");
        if (!Directory.Exists(workspace.RootPath))
            return Failed("The selected workspace is unavailable on this machine.");

        try
        {
            return options.Action switch
            {
                "sessionsSync" => await SyncSessionsAsync(workspace, options, bridge, cancellationToken),
                "sessionRead" => await ReadSessionAsync(workspace, options, bridge, watch: false, cancellationToken),
                "sessionWatch" => await ReadSessionAsync(workspace, options, bridge, watch: true, cancellationToken),
                "workspaceInspect" => await InspectWorkspaceAsync(workspace, bridge, cancellationToken),
                "openIde" => await OpenIdeAsync(workspace, options, bridge, cancellationToken),
                _ => Failed("The requested integration action is not supported.")
            };
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            return new HarnessResult(
                "cancelled", "Integration action cancelled.", "implemented",
                new Dictionary<string, object> { ["cancelled"] = true });
        }
        catch (Exception exception)
        {
            return Failed($"Integration action failed: {exception.GetType().Name}.");
        }
    }

    private async Task<HarnessResult> SyncSessionsAsync(
        WorkspaceRegistration workspace,
        NativeRuntimeOptions options,
        IInteractiveHarnessBridge bridge,
        CancellationToken cancellationToken)
    {
        var requested = options.Provider is "codex" or "claude" ? options.Provider : "all";
        var sessions = new List<Dictionary<string, object?>>(MaxSessionsPerProvider * 2);
        foreach (var provider in SupportedProviders())
        {
            if (requested != "all" && provider.Id != requested) continue;
            sessions.AddRange(
                ScanSessions(provider, workspace)
                    .Select(PublicDescriptor));
        }
        sessions = sessions
            .OrderByDescending(item => item.TryGetValue("updated_at", out var value) ? value : "")
            .ToList();
        var payload = new Dictionary<string, object?>
        {
            ["kind"] = "sessions",
            ["workspace_id"] = workspace.WorkspaceId,
            ["sessions"] = sessions,
            ["boundary"] = "Local Codex and Claude installations only; provider website history is not exposed by model OAuth."
        };
        await ReportAsync(bridge, "integration.sessions", $"Found {sessions.Count} linked local session(s).", payload, cancellationToken);
        return Succeeded($"Synced {sessions.Count} linked local session(s).", "sessions", sessions.Count);
    }

    private async Task<HarnessResult> ReadSessionAsync(
        WorkspaceRegistration workspace,
        NativeRuntimeOptions options,
        IInteractiveHarnessBridge bridge,
        bool watch,
        CancellationToken cancellationToken)
    {
        var provider = SupportedProviders().First(item => item.Id == options.Provider);
        var descriptor = ScanSessions(provider, workspace, int.MaxValue)
            .FirstOrDefault(item => string.Equals(
                Convert.ToString(item["id"]), options.ProviderSessionId,
                StringComparison.Ordinal));
        if (descriptor is null || !descriptor.TryGetValue("_file", out var rawFile))
            return Failed("That provider session is not available inside the selected workspace.");

        var file = Convert.ToString(rawFile) ?? "";
        var lastWrite = DateTime.MinValue;
        var duration = watch ? Math.Clamp(options.WatchSeconds <= 0 ? 15 : options.WatchSeconds, 1, 20) : 0;
        var deadline = DateTime.UtcNow.AddSeconds(duration);
        do
        {
            cancellationToken.ThrowIfCancellationRequested();
            var currentWrite = File.GetLastWriteTimeUtc(file);
            if (currentWrite != lastWrite)
            {
                lastWrite = currentWrite;
                var snapshot = ReadTranscript(provider.Id, file, descriptor, workspace.WorkspaceId);
                await ReportAsync(
                    bridge, "integration.snapshot",
                    watch ? "Linked provider session updated." : "Linked provider session loaded.",
                    snapshot, cancellationToken);
            }
            if (!watch || DateTime.UtcNow >= deadline) break;
            await Task.Delay(TimeSpan.FromSeconds(1), cancellationToken);
        } while (true);

        return Succeeded(watch ? "Live provider-session window completed." : "Provider session loaded.", "provider", provider.Id);
    }

    private async Task<HarnessResult> InspectWorkspaceAsync(
        WorkspaceRegistration workspace,
        IInteractiveHarnessBridge bridge,
        CancellationToken cancellationToken)
    {
        var inside = await RunProcessAsync("git", ["rev-parse", "--is-inside-work-tree"], workspace.RootPath, cancellationToken);
        if (inside.ExitCode != 0 || !inside.Stdout.Trim().Equals("true", StringComparison.OrdinalIgnoreCase))
        {
            var empty = new Dictionary<string, object?>
            {
                ["kind"] = "workspace",
                ["workspace_id"] = workspace.WorkspaceId,
                ["is_repository"] = false,
                ["ides"] = PublicIdes()
            };
            await ReportAsync(bridge, "integration.workspace", "This workspace is not a Git repository.", empty, cancellationToken);
            return Succeeded("Workspace inspected; no Git repository was found.", "repository", false);
        }

        var statusTask = RunProcessAsync("git", ["status", "--porcelain=v1", "--branch"], workspace.RootPath, cancellationToken);
        var diffTask = RunProcessAsync("git", ["diff", "--no-ext-diff", "--no-color", "HEAD", "--"], workspace.RootPath, cancellationToken);
        var remoteTask = RunProcessAsync("git", ["remote", "get-url", "origin"], workspace.RootPath, cancellationToken);
        var branchTask = RunProcessAsync("git", ["branch", "--show-current"], workspace.RootPath, cancellationToken);
        await Task.WhenAll(statusTask, diffTask, remoteTask, branchTask);

        var remote = remoteTask.Result.ExitCode == 0 ? remoteTask.Result.Stdout.Trim() : "";
        var githubUrl = GitHubUrl(remote);
        var gh = await GhAvailableAsync(workspace.RootPath, cancellationToken);
        var pullRequest = gh && githubUrl is not null
            ? await CurrentPullRequestAsync(workspace.RootPath, cancellationToken)
            : null;
        var statusLines = statusTask.Result.Stdout
            .Split(['\r', '\n'], StringSplitOptions.RemoveEmptyEntries)
            .Take(400)
            .ToArray();
        var changed = statusLines.Where(line => !line.StartsWith("## ", StringComparison.Ordinal)).ToArray();
        var payload = new Dictionary<string, object?>
        {
            ["kind"] = "workspace",
            ["workspace_id"] = workspace.WorkspaceId,
            ["is_repository"] = true,
            ["branch"] = branchTask.Result.Stdout.Trim(),
            ["status"] = statusLines,
            ["changed_count"] = changed.Length,
            ["diff"] = Truncate(diffTask.Result.Stdout, MaxDiffCharacters),
            ["diff_truncated"] = diffTask.Result.Stdout.Length > MaxDiffCharacters,
            ["remote"] = PublicRemote(remote),
            ["github_url"] = githubUrl,
            ["github_authenticated"] = gh,
            ["pull_request"] = pullRequest,
            ["ides"] = PublicIdes()
        };
        await ReportAsync(bridge, "integration.workspace", $"Workspace has {changed.Length} changed item(s).", payload, cancellationToken);
        return Succeeded($"Workspace inspected with {changed.Length} changed item(s).", "changed", changed.Length);
    }

    private async Task<HarnessResult> OpenIdeAsync(
        WorkspaceRegistration workspace,
        NativeRuntimeOptions options,
        IInteractiveHarnessBridge bridge,
        CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        var ides = DetectIdes();
        if (options.Ide is null || !ides.TryGetValue(options.Ide, out var executable))
            return Failed("That IDE is not installed on the linked machine.");

        var startInfo = new ProcessStartInfo
        {
            FileName = executable,
            WorkingDirectory = workspace.RootPath,
            UseShellExecute = false,
            CreateNoWindow = true
        };
        startInfo.ArgumentList.Add(workspace.RootPath);
        Process.Start(startInfo);
        var payload = new Dictionary<string, object?>
        {
            ["kind"] = "ide",
            ["workspace_id"] = workspace.WorkspaceId,
            ["ide"] = options.Ide,
            ["opened"] = true
        };
        await ReportAsync(bridge, "integration.ide", $"Opened {options.Ide} on the linked machine.", payload, cancellationToken);
        return Succeeded($"Opened {options.Ide} on the linked machine.", "ide", options.Ide);
    }

    private IEnumerable<Dictionary<string, object?>> ScanSessions(
        ProviderRoot provider,
        WorkspaceRegistration workspace,
        int limit = MaxSessionsPerProvider)
    {
        if (!Directory.Exists(provider.Root)) yield break;
        IEnumerable<string> files;
        try
        {
            files = Directory.EnumerateFiles(provider.Root, "*.jsonl", SearchOption.AllDirectories)
                .OrderByDescending(File.GetLastWriteTimeUtc);
        }
        catch
        {
            yield break;
        }

        var emitted = 0;
        foreach (var file in files)
        {
            if (emitted >= limit) yield break;
            Dictionary<string, object?>? descriptor;
            try { descriptor = ReadDescriptor(provider.Id, file, workspace); }
            catch { continue; }
            if (descriptor is null) continue;
            descriptor["_file"] = file;
            emitted++;
            yield return descriptor;
        }
    }

    private Dictionary<string, object?>? ReadDescriptor(
        string provider,
        string file,
        WorkspaceRegistration workspace)
    {
        string id = SessionIdFromFile(file);
        string cwd = "";
        string title = "";
        string source = provider == "codex" ? "Codex" : "Claude Code";
        string model = "";
        string branch = "";
        var inspected = 0;
        foreach (var line in File.ReadLines(file))
        {
            if (++inspected > 500 || (cwd.Length > 0 && title.Length > 0 && id.Length > 0)) break;
            if (line.Length > 2_000_000) continue;
            using var document = JsonDocument.Parse(line);
            var root = document.RootElement;
            if (provider == "codex")
            {
                var type = String(root, "type");
                var payload = Object(root, "payload");
                if (type == "session_meta")
                {
                    id = String(payload, "id") is { Length: > 0 } metaId ? metaId : id;
                    cwd = String(payload, "cwd");
                    source = SourceLabel(String(payload, "source"), "Codex");
                    var git = Object(payload, "git");
                    branch = String(git, "branch");
                }
                else if (type == "turn_context")
                {
                    cwd = cwd.Length > 0 ? cwd : String(payload, "cwd");
                    model = String(payload, "model");
                }
                else if (type == "response_item" && String(payload, "type") == "message"
                    && String(payload, "role") == "user")
                {
                    title = CleanTitle(Text(ObjectOrArray(payload, "content")));
                }
            }
            else
            {
                id = String(root, "sessionId") is { Length: > 0 } claudeId ? claudeId : id;
                cwd = String(root, "cwd") is { Length: > 0 } value ? value : cwd;
                branch = String(root, "gitBranch") is { Length: > 0 } valueBranch ? valueBranch : branch;
                source = SourceLabel(String(root, "entrypoint"), "Claude Code");
                var message = Object(root, "message");
                model = String(message, "model") is { Length: > 0 } valueModel ? valueModel : model;
                if (String(root, "type") == "user" && !Boolean(root, "isMeta"))
                    title = CleanTitle(Text(ObjectOrArray(message, "content")));
            }
        }
        if (id.Length == 0 || cwd.Length == 0 || !PathWithin(cwd, workspace.RootPath)) return null;
        if (title.Length == 0) title = $"{provider[..1].ToUpperInvariant()}{provider[1..]} session";
        var updated = File.GetLastWriteTimeUtc(file);
        return new Dictionary<string, object?>
        {
            ["id"] = id,
            ["provider"] = provider,
            ["title"] = title,
            ["source"] = source,
            ["model"] = model,
            ["branch"] = branch,
            ["workspace_id"] = workspace.WorkspaceId,
            ["updated_at"] = updated.ToString("O"),
            ["live"] = DateTime.UtcNow - updated <= TimeSpan.FromSeconds(45),
            ["read_only"] = true
        };
    }

    private Dictionary<string, object?> ReadTranscript(
        string provider,
        string file,
        Dictionary<string, object?> descriptor,
        string workspaceId)
    {
        var messages = new List<Dictionary<string, object?>>(MaxTranscriptMessages);
        var characters = 0;
        foreach (var line in File.ReadLines(file))
        {
            if (messages.Count >= MaxTranscriptMessages || characters >= MaxTranscriptCharacters) break;
            if (line.Length > 2_000_000) continue;
            try
            {
                using var document = JsonDocument.Parse(line);
                var root = document.RootElement;
                string role;
                string content;
                string timestamp;
                if (provider == "codex")
                {
                    if (String(root, "type") != "response_item") continue;
                    var payload = Object(root, "payload");
                    if (String(payload, "type") != "message") continue;
                    role = String(payload, "role");
                    content = Text(ObjectOrArray(payload, "content"));
                    timestamp = String(root, "timestamp");
                }
                else
                {
                    role = String(root, "type");
                    if (role is not ("user" or "assistant") || Boolean(root, "isMeta")) continue;
                    var message = Object(root, "message");
                    content = Text(ObjectOrArray(message, "content"));
                    timestamp = String(root, "timestamp");
                }
                if (role is not ("user" or "assistant") || string.IsNullOrWhiteSpace(content)) continue;
                if (role == "user") content = CleanUserContent(content);
                if (string.IsNullOrWhiteSpace(content)) continue;
                var remaining = MaxTranscriptCharacters - characters;
                content = Truncate(content.Trim(), remaining);
                characters += content.Length;
                messages.Add(new Dictionary<string, object?>
                {
                    ["role"] = role,
                    ["content"] = content,
                    ["timestamp"] = timestamp
                });
            }
            catch (JsonException) { }
        }
        var publicDescriptor = descriptor
            .Where(item => item.Key != "_file")
            .ToDictionary(item => item.Key, item => item.Value);
        publicDescriptor["live"] = DateTime.UtcNow - File.GetLastWriteTimeUtc(file) <= TimeSpan.FromSeconds(45);
        publicDescriptor["updated_at"] = File.GetLastWriteTimeUtc(file).ToString("O");
        return new Dictionary<string, object?>
        {
            ["kind"] = "session",
            ["workspace_id"] = workspaceId,
            ["session"] = publicDescriptor,
            ["messages"] = messages,
            ["truncated"] = messages.Count >= MaxTranscriptMessages || characters >= MaxTranscriptCharacters,
            ["read_only"] = true
        };
    }

    private IEnumerable<ProviderRoot> SupportedProviders()
    {
        yield return new ProviderRoot("codex", "Codex", Path.Combine(_userProfile, ".codex", "sessions"));
        yield return new ProviderRoot("claude", "Claude Code", Path.Combine(_userProfile, ".claude", "projects"));
    }

    private Dictionary<string, string> DetectIdes()
    {
        var local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        var programFiles = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles);
        var candidates = new Dictionary<string, string[]>(StringComparer.Ordinal)
        {
            ["vscode"] =
            [
                Path.Combine(local, "Programs", "Microsoft VS Code", "Code.exe"),
                Path.Combine(programFiles, "Microsoft VS Code", "Code.exe")
            ],
            ["cursor"] =
            [
                Path.Combine(local, "Programs", "cursor", "Cursor.exe"),
                Path.Combine(programFiles, "Cursor", "Cursor.exe")
            ]
        };
        return candidates
            .Select(pair => new { pair.Key, Path = pair.Value.FirstOrDefault(File.Exists) })
            .Where(item => item.Path is not null)
            .ToDictionary(item => item.Key, item => item.Path!, StringComparer.Ordinal);
    }

    private IReadOnlyList<Dictionary<string, object>> PublicIdes() => DetectIdes()
        .Keys.Select(id => new Dictionary<string, object>
        {
            ["id"] = id,
            ["label"] = id == "cursor" ? "Cursor" : "Visual Studio Code"
        }).ToList();

    private static async Task<bool> GhAvailableAsync(string cwd, CancellationToken cancellationToken)
    {
        try
        {
            var result = await RunProcessAsync(
                "gh", ["auth", "status", "--hostname", "github.com"], cwd,
                cancellationToken, TimeSpan.FromSeconds(5));
            return result.ExitCode == 0;
        }
        catch { return false; }
    }

    private static async Task<Dictionary<string, object?>?> CurrentPullRequestAsync(
        string cwd,
        CancellationToken cancellationToken)
    {
        try
        {
            var result = await RunProcessAsync(
                "gh",
                [
                    "pr", "view", "--json",
                    "number,title,url,state,isDraft,headRefName,baseRefName"
                ],
                cwd,
                cancellationToken,
                TimeSpan.FromSeconds(8));
            if (result.ExitCode != 0 || string.IsNullOrWhiteSpace(result.Stdout)) return null;
            using var document = JsonDocument.Parse(result.Stdout);
            var root = document.RootElement;
            var url = String(root, "url");
            if (!url.StartsWith("https://github.com/", StringComparison.OrdinalIgnoreCase))
                return null;
            return new Dictionary<string, object?>
            {
                ["number"] = root.TryGetProperty("number", out var number)
                    && number.TryGetInt32(out var value) ? value : 0,
                ["title"] = Truncate(String(root, "title"), 160),
                ["url"] = url,
                ["state"] = Truncate(String(root, "state"), 40),
                ["draft"] = Boolean(root, "isDraft"),
                ["head"] = Truncate(String(root, "headRefName"), 120),
                ["base"] = Truncate(String(root, "baseRefName"), 120)
            };
        }
        catch { return null; }
    }

    private static async Task<ProcessResult> RunProcessAsync(
        string executable,
        IReadOnlyList<string> arguments,
        string cwd,
        CancellationToken cancellationToken,
        TimeSpan? timeout = null)
    {
        var startInfo = new ProcessStartInfo
        {
            FileName = executable,
            WorkingDirectory = cwd,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true
        };
        foreach (var argument in arguments) startInfo.ArgumentList.Add(argument);
        using var process = new Process { StartInfo = startInfo };
        process.Start();
        var stdoutTask = process.StandardOutput.ReadToEndAsync(cancellationToken);
        var stderrTask = process.StandardError.ReadToEndAsync(cancellationToken);
        using var limit = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        limit.CancelAfter(timeout ?? TimeSpan.FromSeconds(12));
        try
        {
            await process.WaitForExitAsync(limit.Token);
        }
        catch
        {
            try { if (!process.HasExited) process.Kill(entireProcessTree: true); } catch { }
            throw;
        }
        return new ProcessResult(
            process.ExitCode, await stdoutTask, await stderrTask);
    }

    private static async Task ReportAsync(
        IInteractiveHarnessBridge bridge,
        string type,
        string summary,
        Dictionary<string, object?> payload,
        CancellationToken cancellationToken) =>
        await bridge.ReportAsync(
            new HarnessProgress(type, summary, JsonSerializer.SerializeToElement(payload, Json)),
            cancellationToken);

    private static Dictionary<string, object?> PublicDescriptor(
        Dictionary<string, object?> descriptor) => descriptor
        .Where(item => item.Key != "_file")
        .ToDictionary(item => item.Key, item => item.Value);

    private static HarnessResult Succeeded(string summary, string key, object value) =>
        new("succeeded", summary, "implemented", new Dictionary<string, object> { [key] = value });

    private static HarnessResult Failed(string summary) =>
        new("failed", summary, "implemented", new Dictionary<string, object>
        {
            ["refused"] = true,
            ["reason"] = summary
        });

    private static string SessionIdFromFile(string file)
    {
        var match = SessionIdRegex().Match(Path.GetFileNameWithoutExtension(file));
        return match.Success ? match.Value : "";
    }

    private static bool PathWithin(string candidate, string root)
    {
        try
        {
            var fullCandidate = Path.TrimEndingDirectorySeparator(Path.GetFullPath(candidate));
            var fullRoot = Path.TrimEndingDirectorySeparator(Path.GetFullPath(root));
            return fullCandidate.Equals(fullRoot, StringComparison.OrdinalIgnoreCase)
                || fullCandidate.StartsWith(fullRoot + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase);
        }
        catch { return false; }
    }

    private static JsonElement Object(JsonElement element, string key) =>
        element.ValueKind == JsonValueKind.Object
        && element.TryGetProperty(key, out var value)
        && value.ValueKind == JsonValueKind.Object
            ? value : default;

    private static JsonElement ObjectOrArray(JsonElement element, string key) =>
        element.ValueKind == JsonValueKind.Object
        && element.TryGetProperty(key, out var value)
        && value.ValueKind is JsonValueKind.Object or JsonValueKind.Array or JsonValueKind.String
            ? value : default;

    private static string String(JsonElement element, string key) =>
        element.ValueKind == JsonValueKind.Object
        && element.TryGetProperty(key, out var value)
        && value.ValueKind == JsonValueKind.String
            ? value.GetString() ?? "" : "";

    private static bool Boolean(JsonElement element, string key) =>
        element.ValueKind == JsonValueKind.Object
        && element.TryGetProperty(key, out var value)
        && value.ValueKind == JsonValueKind.True;

    private static string Text(JsonElement element)
    {
        if (element.ValueKind == JsonValueKind.String) return element.GetString() ?? "";
        if (element.ValueKind == JsonValueKind.Array)
            return string.Join("\n", element.EnumerateArray().Select(Text).Where(value => value.Length > 0));
        if (element.ValueKind != JsonValueKind.Object) return "";
        foreach (var key in new[] { "text", "input_text", "output_text", "content" })
        {
            if (!element.TryGetProperty(key, out var value)) continue;
            var text = Text(value);
            if (text.Length > 0) return text;
        }
        return "";
    }

    private static string CleanTitle(string value)
    {
        var collapsed = WhitespaceRegex().Replace(CleanUserContent(value), " ").Trim();
        return collapsed.Length <= 92 ? collapsed : collapsed[..91].TrimEnd() + "…";
    }

    private static string CleanUserContent(string value)
    {
        var candidate = (value ?? "").Trim();
        var requestMarker = candidate.LastIndexOf("## My request:", StringComparison.OrdinalIgnoreCase);
        if (requestMarker >= 0)
        {
            candidate = candidate[(requestMarker + "## My request:".Length)..].Trim();
        }
        else
        {
            var boundaryEnd = 0;
            foreach (var marker in new[]
            {
                "</recommended_plugins>", "</INSTRUCTIONS>",
                "</environment_context>", "</permissions instructions>"
            })
            {
                var index = candidate.LastIndexOf(marker, StringComparison.OrdinalIgnoreCase);
                if (index >= 0) boundaryEnd = Math.Max(boundaryEnd, index + marker.Length);
            }
            if (boundaryEnd > 0 && candidate[boundaryEnd..].Trim().Length > 0)
                candidate = candidate[boundaryEnd..].Trim();
            else if (candidate.StartsWith("<recommended_plugins>", StringComparison.OrdinalIgnoreCase)
                || candidate.StartsWith("# AGENTS.md instructions", StringComparison.OrdinalIgnoreCase)
                || candidate.StartsWith("<environment_context", StringComparison.OrdinalIgnoreCase)
                || candidate.StartsWith("<permissions instructions", StringComparison.OrdinalIgnoreCase))
                return "";
        }
        return candidate;
    }

    private static string SourceLabel(string value, string fallback)
    {
        var normalized = (value ?? "").Trim().ToLowerInvariant();
        if (normalized.Contains("vscode")) return $"{fallback} · VS Code";
        if (normalized.Contains("cursor")) return $"{fallback} · Cursor";
        if (normalized.Contains("cli")) return $"{fallback} · CLI";
        return fallback;
    }

    private static string PublicRemote(string remote)
    {
        if (string.IsNullOrWhiteSpace(remote)) return "";
        var github = GitHubUrl(remote);
        return github ?? "Configured remote";
    }

    private static string? GitHubUrl(string remote)
    {
        var value = (remote ?? "").Trim();
        if (value.StartsWith("git@github.com:", StringComparison.OrdinalIgnoreCase))
            value = "https://github.com/" + value[15..];
        else if (value.StartsWith("ssh://git@github.com/", StringComparison.OrdinalIgnoreCase))
            value = "https://github.com/" + value[21..];
        if (!value.StartsWith("https://github.com/", StringComparison.OrdinalIgnoreCase)) return null;
        return value.EndsWith(".git", StringComparison.OrdinalIgnoreCase) ? value[..^4] : value;
    }

    private static string Truncate(string value, int limit) =>
        value.Length <= limit ? value : value[..limit] + "\n… truncated";

    [GeneratedRegex(@"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")]
    private static partial Regex SessionIdRegex();

    [GeneratedRegex(@"\s+")]
    private static partial Regex WhitespaceRegex();

    private sealed record ProviderRoot(string Id, string Label, string Root);
    private sealed record ProcessResult(int ExitCode, string Stdout, string Stderr);
}
