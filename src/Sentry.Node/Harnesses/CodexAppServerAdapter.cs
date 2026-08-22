using System.Diagnostics;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using Sentry.Node.Gateway;
using Sentry.Node.Workspaces;

namespace Sentry.Node.Harnesses;

/// <summary>
/// Runs the official Codex App Server beside the user's local projects and
/// relays its native JSON-RPC lifecycle through Sentry's outbound node channel.
/// A fresh stdio child is used per turn; the Codex thread id is persisted
/// locally so no listener or long-lived orphan process is required.
/// </summary>
public sealed class CodexAppServerAdapter : IInteractiveHarnessAdapter
{
    private static readonly string[] NativeFeatures =
    [
        "threads", "streaming", "approvals", "user-input", "goals",
        "skills", "apps", "mcp", "plugins", "sandbox", "review",
        "worktrees", "filesystem", "web-search", "images", "subagents",
        "hooks", "configuration", "interrupt"
    ];
    private static readonly HashSet<string> InteractiveRequests = new(StringComparer.Ordinal)
    {
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
        "item/permissions/requestApproval",
        "item/tool/requestUserInput",
        "mcpServer/elicitation/request",
        "applyPatchApproval",
        "execCommandApproval"
    };

    private static readonly JsonSerializerOptions Json = new(JsonSerializerDefaults.Web);
    private readonly string _executable;
    private readonly CodexSessionStore _sessions;
    private readonly TimeSpan _timeout;

    public CodexAppServerAdapter(
        string executable,
        string sessionStorePath,
        TimeSpan? timeout = null)
    {
        _executable = Path.GetFullPath(executable);
        _sessions = new CodexSessionStore(sessionStorePath);
        _timeout = timeout ?? TimeSpan.FromHours(2);
    }

    public string Name => "codex";

    public Task<HarnessResult> ExecuteAsync(
        string command,
        string mode,
        WorkspaceRegistration workspace,
        IProgress<HarnessProgress> events,
        CancellationToken cancellationToken) => Task.FromResult(new HarnessResult(
            "failed",
            "Native Codex requires Sentry's interactive App Server relay.",
            "implemented",
            new Dictionary<string, object> { ["refused"] = true }));

    public static async Task<NativeRuntimeRegistration> ProbeAsync(
        string executable,
        CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(executable) || !File.Exists(executable))
        {
            return new NativeRuntimeRegistration(
                false, null, null, [], NativeFeatures,
                "The Codex executable was not found.");
        }

        string? version = null;
        try
        {
            version = await ReadVersionAsync(executable, cancellationToken);
            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            timeout.CancelAfter(TimeSpan.FromSeconds(30));
            await using var rpc = await AppServerProcess.StartAsync(executable, timeout.Token);
            await rpc.InitializeAsync(timeout.Token);

            var modelResponse = await rpc.CallAsync(
                1,
                "model/list",
                new Dictionary<string, object?>
                {
                    ["limit"] = 100,
                    ["includeHidden"] = false
                },
                timeout.Token);
            var accountResponse = await rpc.CallAsync(
                2, "account/read", new Dictionary<string, object?>(), timeout.Token);

            var models = ExtractModels(modelResponse);
            var authMode = ExtractString(accountResponse, "result", "account", "authMode")
                ?? ExtractString(accountResponse, "result", "authMode");
            var error = ErrorMessage(modelResponse) ?? ErrorMessage(accountResponse);
            var available = error is null && models.Count > 0;
            return new NativeRuntimeRegistration(
                available,
                version,
                authMode,
                models,
                NativeFeatures,
                available ? null : error ?? "Codex returned no subscription models.");
        }
        catch (Exception exception)
        {
            return new NativeRuntimeRegistration(
                false,
                version,
                null,
                [],
                NativeFeatures,
                $"Codex App Server probe failed: {exception.GetType().Name}");
        }
    }

    public async Task<HarnessResult> ExecuteInteractiveAsync(
        string prompt,
        string mode,
        WorkspaceRegistration workspace,
        string runtimeSessionId,
        string model,
        IInteractiveHarnessBridge bridge,
        CancellationToken cancellationToken)
    {
        if (!File.Exists(_executable))
            return Failure("The configured Codex executable is unavailable.");
        if (!Directory.Exists(workspace.RootPath))
            return Failure("The selected local workspace is unavailable.");
        if (mode is not ("readOnly" or "workspaceWrite"))
            return Failure($"Mode '{mode}' is not supported by native Codex.");

        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeout.CancelAfter(_timeout);
        var token = timeout.Token;
        string? threadId = null;
        string? turnId = null;
        var eventCount = 0;
        var finalText = new StringBuilder();

        try
        {
            await using var rpc = await AppServerProcess.StartAsync(_executable, token);
            await rpc.InitializeAsync(token);

            var sessionKey = $"{workspace.WorkspaceId}\n{runtimeSessionId}";
            threadId = await _sessions.ReadAsync(sessionKey, token);
            JsonElement threadResponse;
            if (!string.IsNullOrWhiteSpace(threadId))
            {
                threadResponse = await rpc.CallAsync(
                    10,
                    "thread/resume",
                    ThreadParams(threadId, model, mode, workspace.RootPath),
                    token);
                if (ErrorMessage(threadResponse) is not null)
                {
                    await _sessions.RemoveAsync(sessionKey, token);
                    threadId = null;
                }
            }

            if (string.IsNullOrWhiteSpace(threadId))
            {
                threadResponse = await rpc.CallAsync(
                    11,
                    "thread/start",
                    ThreadParams(null, model, mode, workspace.RootPath),
                    token);
                var startError = ErrorMessage(threadResponse);
                if (startError is not null)
                    return Failure($"Codex could not start a thread: {startError}");
                threadId = ExtractString(threadResponse, "result", "thread", "id");
                if (string.IsNullOrWhiteSpace(threadId))
                    return Failure("Codex started without returning a thread id.");
                await _sessions.WriteAsync(sessionKey, threadId, token);
            }

            var turnResponse = await rpc.CallAsync(
                20,
                "turn/start",
                new Dictionary<string, object?>
                {
                    ["threadId"] = threadId,
                    ["input"] = new object[]
                    {
                        new Dictionary<string, object?>
                        {
                            ["type"] = "text",
                            ["text"] = prompt
                        }
                    }
                },
                token);
            var turnError = ErrorMessage(turnResponse);
            if (turnError is not null)
                return Failure($"Codex could not start the turn: {turnError}", threadId);
            turnId = ExtractString(turnResponse, "result", "turn", "id");
            if (string.IsNullOrWhiteSpace(turnId))
                return Failure("Codex started without returning a turn id.", threadId);

            var pendingDelta = new StringBuilder();
            var lastDeltaFlush = Stopwatch.StartNew();
            try
            {
                while (!token.IsCancellationRequested)
                {
                    var frame = await rpc.ReadAsync(token);
                    if (frame is null)
                        return Failure("Codex App Server closed before the turn completed.");

                    var root = frame.Value;
                    if (root.TryGetProperty("method", out var methodElement)
                        && methodElement.ValueKind == JsonValueKind.String)
                    {
                        var method = methodElement.GetString() ?? "";
                        if (root.TryGetProperty("id", out _))
                        {
                            await FlushDeltaAsync();
                            var requestId = RequestId(root);
                            if (!InteractiveRequests.Contains(method))
                            {
                                await rpc.SendAsync(
                                    new Dictionary<string, object?>
                                    {
                                        ["id"] = JsonNode.Parse(root.GetProperty("id").GetRawText()),
                                        ["error"] = new Dictionary<string, object?>
                                        {
                                            ["code"] = -32601,
                                            ["message"] = $"Sentry did not register a client handler for {method}."
                                        }
                                    },
                                    token);
                                continue;
                            }
                            var progress = NativeRequestProgress(
                                method, requestId, root, workspace);
                            await bridge.ReportAsync(progress, token);
                            eventCount++;
                            var response = await bridge.WaitForResponseAsync(requestId, token);
                            if (response is null)
                                return Failure("The native Codex request ended without a user response.");
                            var appServerResult = NativeResponseResult(method, root, response.Value);
                            await rpc.SendAsync(
                                new Dictionary<string, object?>
                                {
                                    ["id"] = JsonNode.Parse(root.GetProperty("id").GetRawText()),
                                    ["result"] = JsonNode.Parse(appServerResult.GetRawText())
                                },
                                token);
                            continue;
                        }

                        if (method == "item/agentMessage/delta")
                        {
                            var delta = ExtractString(root, "params", "delta") ?? "";
                            if (delta.Length > 0)
                            {
                                pendingDelta.Append(delta);
                                finalText.Append(delta);
                            }
                            if (pendingDelta.Length >= 512 || lastDeltaFlush.ElapsedMilliseconds >= 150)
                                await FlushDeltaAsync();
                            continue;
                        }

                        await FlushDeltaAsync();
                        var normalized = NativeNotificationProgress(method, root, workspace);
                        if (normalized is not null)
                        {
                            await bridge.ReportAsync(normalized, token);
                            eventCount++;
                        }

                        if (method == "turn/completed")
                        {
                            var status = ExtractString(root, "params", "turn", "status") ?? "failed";
                            if (status != "completed")
                            {
                                var error = ExtractString(root, "params", "turn", "error", "message")
                                    ?? $"Codex turn ended with status '{status}'.";
                                return Failure(error, threadId, eventCount);
                            }
                            return new HarnessResult(
                                "succeeded",
                                finalText.Length > 0 ? Truncate(finalText.ToString(), 4000) : "Codex completed the turn.",
                                "implemented",
                                new Dictionary<string, object>
                                {
                                    ["harness"] = Name,
                                    ["nativeRuntime"] = "codex-app-server",
                                    ["threadId"] = threadId,
                                    ["turnId"] = turnId,
                                    ["model"] = model,
                                    ["workspaceId"] = workspace.WorkspaceId,
                                    ["mode"] = mode,
                                    ["events"] = eventCount
                                });
                        }
                    }
                    else if (ErrorMessage(root) is string rpcError)
                    {
                        await FlushDeltaAsync();
                        return Failure($"Codex App Server error: {rpcError}", threadId, eventCount);
                    }
                }

                return Failure("Codex turn was cancelled.", threadId, eventCount, cancelled: true);
            }
            catch (OperationCanceledException)
            {
                await TryInterruptAsync(rpc, threadId, turnId);
                throw;
            }

            async Task FlushDeltaAsync()
            {
                if (pendingDelta.Length == 0) return;
                var text = pendingDelta.ToString();
                pendingDelta.Clear();
                lastDeltaFlush.Restart();
                var payload = WrapNative(
                    "item/agentMessage/delta",
                    JsonSerializer.SerializeToElement(new { delta = text }),
                    workspace);
                await bridge.ReportAsync(new HarnessProgress("message", text, payload), token);
                eventCount++;
            }
        }
        catch (OperationCanceledException)
        {
            return Failure(
                cancellationToken.IsCancellationRequested
                    ? "Codex turn was cancelled."
                    : $"Codex turn exceeded the {_timeout.TotalMinutes:0} minute limit.",
                threadId,
                eventCount,
                cancelled: cancellationToken.IsCancellationRequested);
        }
        catch (Exception exception)
        {
            return Failure(
                $"Native Codex failed: {exception.GetType().Name}: {exception.Message}",
                threadId,
                eventCount);
        }
    }

    private static async Task TryInterruptAsync(
        AppServerProcess rpc, string? threadId, string? turnId)
    {
        if (string.IsNullOrWhiteSpace(threadId) || string.IsNullOrWhiteSpace(turnId)) return;
        try
        {
            using var interrupt = new CancellationTokenSource(TimeSpan.FromSeconds(2));
            await rpc.CallAsync(
                21,
                "turn/interrupt",
                new Dictionary<string, object?>
                {
                    ["threadId"] = threadId,
                    ["turnId"] = turnId
                },
                interrupt.Token);
        }
        catch
        {
            // Disposal below still terminates the entire direct child tree.
        }
    }

    private static Dictionary<string, object?> ThreadParams(
        string? threadId, string model, string mode, string cwd)
    {
        var result = new Dictionary<string, object?>
        {
            ["model"] = model,
            ["cwd"] = cwd,
            ["approvalPolicy"] = "on-request",
            ["sandbox"] = mode == "readOnly" ? "read-only" : "workspace-write",
            ["serviceName"] = "frontir-sentry"
        };
        if (!string.IsNullOrWhiteSpace(threadId)) result["threadId"] = threadId;
        return result;
    }

    private static HarnessProgress NativeRequestProgress(
        string method, string requestId, JsonElement root, WorkspaceRegistration workspace)
    {
        var summary = method switch
        {
            "item/commandExecution/requestApproval" =>
                ExtractString(root, "params", "command") ?? "Approve command execution",
            "item/fileChange/requestApproval" =>
                ExtractString(root, "params", "reason") ?? "Approve file changes",
            "item/permissions/requestApproval" =>
                ExtractString(root, "params", "reason") ?? "Approve additional permissions",
            "execCommandApproval" =>
                ExtractString(root, "params", "reason") ?? "Approve command execution",
            "applyPatchApproval" =>
                ExtractString(root, "params", "reason") ?? "Approve file changes",
            "item/tool/requestUserInput" => "Codex needs your input",
            "mcpServer/elicitation/request" =>
                ExtractString(root, "params", "message") ?? "A connected tool needs your input",
            _ => $"Codex requested client action: {method}"
        };
        var type = method.Contains("requestApproval", StringComparison.Ordinal)
            || method is "execCommandApproval" or "applyPatchApproval"
            ? "approval.required"
            : "needs.input";
        return new HarnessProgress(type, summary, WrapNative(method, root, workspace, requestId));
    }

    private static HarnessProgress? NativeNotificationProgress(
        string method, JsonElement root, WorkspaceRegistration workspace)
    {
        var (type, summary) = method switch
        {
            "turn/started" => ("turn.started", "Codex started working."),
            "turn/completed" => (
                ExtractString(root, "params", "turn", "status") == "completed"
                    ? "turn.completed" : "turn.failed",
                ExtractString(root, "params", "turn", "error", "message")
                    ?? "Codex completed the turn."),
            "item/started" => ("tool.progress", ItemSummary(root, "started")),
            "item/completed" => ("tool.progress", ItemSummary(root, "completed")),
            "turn/plan/updated" => ("tool.progress", "Codex updated its plan."),
            "turn/diff/updated" => ("tool.progress", "Codex updated the workspace diff."),
            "thread/tokenUsage/updated" => ("tool.progress", "Codex updated token usage."),
            "error" => ("error", ExtractString(root, "params", "error", "message") ?? "Codex reported an error."),
            _ when method.EndsWith("/updated", StringComparison.Ordinal)
                || method.EndsWith("/changed", StringComparison.Ordinal)
                => ("tool.progress", Humanize(method)),
            _ => ("", "")
        };
        return type.Length == 0
            ? null
            : new HarnessProgress(type, summary, WrapNative(method, root, workspace));
    }

    private static JsonElement NativeResponseResult(
        string method, JsonElement request, JsonElement response)
    {
        if (method is "item/tool/requestUserInput" or "mcpServer/elicitation/request")
            return response.Clone();

        var choice = ExtractString(response, "decision") ?? "deny";
        var session = choice is "session" or "always";
        var approved = choice is "once" or "session" or "always";

        if (method is "item/commandExecution/requestApproval" or "item/fileChange/requestApproval")
        {
            return JsonSerializer.SerializeToElement(
                new Dictionary<string, object?>
                {
                    ["decision"] = approved ? (session ? "acceptForSession" : "accept") : "decline"
                },
                Json);
        }

        if (method is "execCommandApproval" or "applyPatchApproval")
        {
            return JsonSerializer.SerializeToElement(
                new Dictionary<string, object?>
                {
                    ["decision"] = approved ? (session ? "approved_for_session" : "approved") : "denied"
                },
                Json);
        }

        if (method == "item/permissions/requestApproval")
        {
            JsonNode permissions = new JsonObject();
            if (approved
                && request.TryGetProperty("params", out var parameters)
                && parameters.TryGetProperty("permissions", out var requestedPermissions))
            {
                permissions = JsonNode.Parse(requestedPermissions.GetRawText()) ?? new JsonObject();
            }
            var result = new JsonObject
            {
                ["permissions"] = permissions,
                ["scope"] = session ? "session" : "turn"
            };
            return JsonSerializer.SerializeToElement(result, Json);
        }

        return response.Clone();
    }

    private static string ItemSummary(JsonElement root, string phase)
    {
        var itemType = ExtractString(root, "params", "item", "type") ?? "activity";
        var command = ExtractString(root, "params", "item", "command");
        var tool = ExtractString(root, "params", "item", "tool");
        return command ?? (tool is not null ? $"{tool} {phase}." : $"{Humanize(itemType)} {phase}.");
    }

    private static JsonElement WrapNative(
        string method,
        JsonElement root,
        WorkspaceRegistration workspace,
        string? requestId = null)
    {
        var native = Sanitize(root, workspace);
        var wrapper = new JsonObject
        {
            ["runtime"] = "codex",
            ["native_method"] = method,
            ["native"] = JsonNode.Parse(native.GetRawText())
        };
        if (!string.IsNullOrWhiteSpace(requestId)) wrapper["request_id"] = requestId;
        return JsonSerializer.SerializeToElement(wrapper, Json);
    }

    private static JsonElement Sanitize(JsonElement root, WorkspaceRegistration workspace)
    {
        var node = JsonNode.Parse(root.GetRawText());
        SanitizeNode(node, Path.GetFullPath(workspace.RootPath), workspace.WorkspaceId);
        return JsonSerializer.SerializeToElement(node, Json);
    }

    private static void SanitizeNode(JsonNode? node, string workspaceRoot, string workspaceId)
    {
        if (node is JsonObject obj)
        {
            foreach (var property in obj.ToList())
            {
                if (property.Value is JsonValue value && value.TryGetValue<string>(out var text))
                    obj[property.Key] = SanitizeText(text, workspaceRoot, workspaceId);
                else
                    SanitizeNode(property.Value, workspaceRoot, workspaceId);
            }
        }
        else if (node is JsonArray array)
        {
            for (var index = 0; index < array.Count; index++)
            {
                if (array[index] is JsonValue value && value.TryGetValue<string>(out var text))
                    array[index] = SanitizeText(text, workspaceRoot, workspaceId);
                else
                    SanitizeNode(array[index], workspaceRoot, workspaceId);
            }
        }
    }

    private static string SanitizeText(string value, string workspaceRoot, string workspaceId)
    {
        var result = value.Replace(
            workspaceRoot,
            $"workspace://{workspaceId}",
            StringComparison.OrdinalIgnoreCase);
        var home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        if (!string.IsNullOrWhiteSpace(home))
            result = result.Replace(home, "home://", StringComparison.OrdinalIgnoreCase);
        return result;
    }

    private static string RequestId(JsonElement root)
    {
        var id = root.GetProperty("id");
        return id.ValueKind == JsonValueKind.String
            ? id.GetString() ?? ""
            : id.GetRawText();
    }

    private static string? ExtractString(JsonElement root, params string[] path)
    {
        var current = root;
        foreach (var part in path)
        {
            if (current.ValueKind != JsonValueKind.Object
                || !current.TryGetProperty(part, out current)) return null;
        }
        return current.ValueKind == JsonValueKind.String ? current.GetString() : null;
    }

    private static List<string> ExtractModels(JsonElement response)
    {
        var result = new List<string>();
        if (!response.TryGetProperty("result", out var body)
            || !body.TryGetProperty("data", out var data)
            || data.ValueKind != JsonValueKind.Array) return result;
        foreach (var item in data.EnumerateArray())
        {
            var id = ExtractString(item, "id") ?? ExtractString(item, "model");
            if (!string.IsNullOrWhiteSpace(id) && !result.Contains(id, StringComparer.Ordinal))
                result.Add(id);
        }
        return result;
    }

    private static string? ErrorMessage(JsonElement response)
    {
        if (!response.TryGetProperty("error", out var error)) return null;
        return ExtractString(error, "message") ?? error.GetRawText();
    }

    private static string Humanize(string value) =>
        value.Replace('/', ' ').Replace('_', ' ').Trim();

    private static HarnessResult Failure(
        string summary,
        string? threadId = null,
        int events = 0,
        bool cancelled = false) => new(
            cancelled ? "cancelled" : "failed",
            summary,
            "implemented",
            new Dictionary<string, object>
            {
                ["harness"] = "codex",
                ["nativeRuntime"] = "codex-app-server",
                ["threadId"] = threadId ?? "",
                ["events"] = events
            });

    private static string Truncate(string value, int limit) =>
        value.Length <= limit ? value : value[..limit] + "... truncated";

    private static async Task<string?> ReadVersionAsync(
        string executable, CancellationToken cancellationToken)
    {
        var info = BaseStartInfo(executable);
        info.ArgumentList.Add("--version");
        using var process = new Process { StartInfo = info };
        process.Start();
        var stdout = await process.StandardOutput.ReadToEndAsync(cancellationToken);
        await process.WaitForExitAsync(cancellationToken);
        return process.ExitCode == 0 ? stdout.Trim() : null;
    }

    private static ProcessStartInfo BaseStartInfo(string executable) => new()
    {
        FileName = executable,
        RedirectStandardInput = true,
        RedirectStandardOutput = true,
        RedirectStandardError = true,
        UseShellExecute = false,
        CreateNoWindow = true
    };

    private sealed class AppServerProcess : IAsyncDisposable
    {
        private readonly Process _process;
        private readonly StreamWriter _input;
        private readonly StreamReader _output;
        private readonly Task<string> _stderr;

        private AppServerProcess(Process process)
        {
            _process = process;
            _input = process.StandardInput;
            _input.AutoFlush = true;
            _output = process.StandardOutput;
            _stderr = process.StandardError.ReadToEndAsync();
        }

        public static Task<AppServerProcess> StartAsync(
            string executable, CancellationToken cancellationToken)
        {
            cancellationToken.ThrowIfCancellationRequested();
            var info = BaseStartInfo(executable);
            info.ArgumentList.Add("app-server");
            info.ArgumentList.Add("--stdio");
            var process = new Process { StartInfo = info };
            if (!process.Start())
                throw new InvalidOperationException("Codex App Server did not start.");
            return Task.FromResult(new AppServerProcess(process));
        }

        public async Task InitializeAsync(CancellationToken cancellationToken)
        {
            var response = await CallAsync(
                0,
                "initialize",
                new Dictionary<string, object?>
                {
                    ["clientInfo"] = new Dictionary<string, object?>
                    {
                        ["name"] = "frontir_sentry",
                        ["title"] = "Frontir Sentry",
                        ["version"] = "1"
                    },
                    ["capabilities"] = new Dictionary<string, object?>
                    {
                        ["experimentalApi"] = true
                    }
                },
                cancellationToken);
            if (ErrorMessage(response) is string error)
                throw new InvalidOperationException($"Codex initialize failed: {error}");
            await SendAsync(
                new Dictionary<string, object?>
                {
                    ["method"] = "initialized",
                    ["params"] = new Dictionary<string, object?>()
                },
                cancellationToken);
        }

        public async Task<JsonElement> CallAsync(
            int id,
            string method,
            object parameters,
            CancellationToken cancellationToken)
        {
            await SendAsync(
                new Dictionary<string, object?>
                {
                    ["id"] = id,
                    ["method"] = method,
                    ["params"] = parameters
                },
                cancellationToken);
            while (true)
            {
                var frame = await ReadAsync(cancellationToken)
                    ?? throw new InvalidOperationException(
                        $"Codex App Server closed while waiting for {method}.");
                if (frame.TryGetProperty("id", out var responseId)
                    && responseId.GetRawText() == id.ToString())
                    return frame;
            }
        }

        public Task SendAsync(object message, CancellationToken cancellationToken)
        {
            cancellationToken.ThrowIfCancellationRequested();
            return _input.WriteLineAsync(JsonSerializer.Serialize(message, Json));
        }

        public async Task<JsonElement?> ReadAsync(CancellationToken cancellationToken)
        {
            var line = await _output.ReadLineAsync(cancellationToken);
            if (line is null) return null;
            using var document = JsonDocument.Parse(line);
            return document.RootElement.Clone();
        }

        public async ValueTask DisposeAsync()
        {
            try { _input.Close(); } catch { }
            try
            {
                using var wait = new CancellationTokenSource(TimeSpan.FromSeconds(3));
                await _process.WaitForExitAsync(wait.Token);
            }
            catch
            {
                try
                {
                    if (!_process.HasExited) _process.Kill(entireProcessTree: true);
                }
                catch { }
            }
            try { await _stderr.WaitAsync(TimeSpan.FromSeconds(1)); } catch { }
            _process.Dispose();
        }
    }

    private sealed class CodexSessionStore
    {
        private readonly string _path;
        private readonly SemaphoreSlim _gate = new(1, 1);

        public CodexSessionStore(string path)
        {
            _path = Path.GetFullPath(path);
        }

        public async Task<string?> ReadAsync(string key, CancellationToken cancellationToken)
        {
            await _gate.WaitAsync(cancellationToken);
            try
            {
                var sessions = await LoadAsync(cancellationToken);
                return sessions.TryGetValue(key, out var value) ? value : null;
            }
            finally { _gate.Release(); }
        }

        public async Task WriteAsync(
            string key, string value, CancellationToken cancellationToken)
        {
            await _gate.WaitAsync(cancellationToken);
            try
            {
                var sessions = await LoadAsync(cancellationToken);
                sessions[key] = value;
                await SaveAsync(sessions, cancellationToken);
            }
            finally { _gate.Release(); }
        }

        public async Task RemoveAsync(string key, CancellationToken cancellationToken)
        {
            await _gate.WaitAsync(cancellationToken);
            try
            {
                var sessions = await LoadAsync(cancellationToken);
                if (sessions.Remove(key)) await SaveAsync(sessions, cancellationToken);
            }
            finally { _gate.Release(); }
        }

        private async Task<Dictionary<string, string>> LoadAsync(
            CancellationToken cancellationToken)
        {
            if (!File.Exists(_path)) return new Dictionary<string, string>(StringComparer.Ordinal);
            await using var stream = new FileStream(
                _path, FileMode.Open, FileAccess.Read, FileShare.Read);
            return await JsonSerializer.DeserializeAsync<Dictionary<string, string>>(
                stream, Json, cancellationToken)
                ?? new Dictionary<string, string>(StringComparer.Ordinal);
        }

        private async Task SaveAsync(
            Dictionary<string, string> sessions, CancellationToken cancellationToken)
        {
            Directory.CreateDirectory(Path.GetDirectoryName(_path)!);
            var temporary = _path + ".new";
            await using (var stream = new FileStream(
                temporary, FileMode.Create, FileAccess.Write, FileShare.None))
            {
                await JsonSerializer.SerializeAsync(stream, sessions, Json, cancellationToken);
                await stream.FlushAsync(cancellationToken);
            }
            File.Move(temporary, _path, overwrite: true);
        }
    }
}
