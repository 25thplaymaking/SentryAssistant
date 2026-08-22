using System.Text.Json;
using Sentry.Node;
using Sentry.Node.Harnesses;
using Sentry.Node.Hooks;
using Sentry.Node.Security;
using Sentry.Node.Workspaces;

// Sentry execution node.
//
// Runs at sign-in under its owner's account, never as SYSTEM, and connects
// outbound to the Gateway. Nothing listens here.
//
// Configuration comes from appsettings.node.json next to the executable, or a
// path given as the first argument. Secrets are DPAPI-protected for this
// Windows user and never appear in the scheduled-task command line.

// Hook management and hook delivery run without a gateway token or a signing
// key: installing hooks is a local file edit, and receiving one must work on a
// machine that has never enrolled.
if (args.Length > 0 && args[0] is "install-hooks" or "uninstall-hooks" or "hook")
{
    return HookCommands.Run(args, Console.Out, Console.In);
}

var configPath = args.Length > 0
    ? args[0]
    : Path.Combine(AppContext.BaseDirectory, "appsettings.node.json");

if (!File.Exists(configPath))
{
    Console.Error.WriteLine($"No configuration at {configPath}");
    return 2;
}

NodeConfig config;
try
{
    config = JsonSerializer.Deserialize<NodeConfig>(
        File.ReadAllText(configPath),
        new JsonSerializerOptions(JsonSerializerDefaults.Web))
        ?? throw new InvalidOperationException("configuration was empty");
}
catch (Exception exception)
{
    Console.Error.WriteLine($"Could not read {configPath}: {exception.Message}");
    return 2;
}

var configDirectory = Path.GetDirectoryName(Path.GetFullPath(configPath))
    ?? AppContext.BaseDirectory;
var logPath = ResolvePath(config.LogPath, configDirectory, "node.log");
Directory.CreateDirectory(Path.GetDirectoryName(logPath)!);
if (File.Exists(logPath) && new FileInfo(logPath).Length > 5_000_000)
    File.Move(logPath, logPath + ".previous", overwrite: true);
using var logWriter = TextWriter.Synchronized(new StreamWriter(
    new FileStream(logPath, FileMode.Append, FileAccess.Write, FileShare.Read))
{
    AutoFlush = true
});
Console.SetOut(logWriter);
Console.SetError(logWriter);

var credentialPath = ResolvePath(
    config.CredentialPath, configDirectory, "node.credentials.json");
var credentialFile = new Sentry.Node.Gateway.NodeCredentialFile(credentialPath);
Sentry.Node.Gateway.NodeCredentialSecrets secrets;
try
{
    secrets = credentialFile.Load();
}
catch (Exception exception)
{
    Console.Error.WriteLine($"Could not load protected node credentials: {exception.Message}");
    return 2;
}

var credentialSession = new Sentry.Node.Gateway.NodeCredentialSession(
    secrets.AccessToken,
    secrets.RefreshToken,
    (access, refresh, cancellationToken) => credentialFile.SaveAsync(
        new Sentry.Node.Gateway.NodeCredentialSecrets(access, refresh, secrets.SigningKey),
        cancellationToken));

// Workspace roots are declared here, locally. The Gateway only ever sends the
// identifier, so this file is the single place a path is bound.
var registrations = config.Workspaces.Select(w => new WorkspaceRegistration(
    w.WorkspaceId,
    w.RootPath,
    new HashSet<string>(w.AllowedHarnesses, StringComparer.OrdinalIgnoreCase),
    new HashSet<string>(w.AllowedModes, StringComparer.Ordinal))).ToList();

WorkspaceRegistry registry;
try
{
    registry = new WorkspaceRegistry(registrations);
}
catch (ArgumentException exception)
{
    Console.Error.WriteLine($"Invalid workspace configuration: {exception.Message}");
    return 2;
}

var harnesses = new Dictionary<string, IHarnessAdapter>(StringComparer.OrdinalIgnoreCase)
{
    ["shell"] = new ProcessHarnessAdapter("shell"),
    ["claude"] = new ClaudeAdapter(
        config.ClaudeExecutable,
        Path.Combine(AppContext.BaseDirectory, "claude-automation-settings.json"))
};
var nativeRuntimes = new Dictionary<string, Sentry.Node.Gateway.NativeRuntimeRegistration>(
    StringComparer.OrdinalIgnoreCase);
if (!string.IsNullOrWhiteSpace(config.CodexExecutable)
    && File.Exists(config.CodexExecutable))
{
    var codexStatePath = ResolvePath(
        config.CodexSessionPath, configDirectory, "codex-sessions.json");
    var codex = new CodexAppServerAdapter(config.CodexExecutable, codexStatePath);
    var probe = await CodexAppServerAdapter.ProbeAsync(
        config.CodexExecutable, registrations, CancellationToken.None);
    nativeRuntimes["codex"] = probe;
    if (probe.Available)
        harnesses["codex"] = codex;
    else
        Console.WriteLine($"native Codex unavailable: {probe.Reason}");
}

var expectation = new NodeExpectation(
    NodeId: config.NodeId,
    NodeOwnerUserId: config.OwnerUserId,
    RegisteredWorkspaces: registrations.Select(r => r.WorkspaceId).ToHashSet(StringComparer.Ordinal),
    AllowedHarnesses: harnesses.Keys.ToHashSet(StringComparer.OrdinalIgnoreCase),
    TeamMembers: config.TeamMembers.ToHashSet(StringComparer.Ordinal));

using var shutdown = new CancellationTokenSource();
Console.CancelKeyPress += (_, e) =>
{
    e.Cancel = true;
    Console.WriteLine("shutting down...");
    shutdown.Cancel();
};

var worker = new NodeWorker(new NodeWorkerOptions(
    GatewayUrl: config.GatewayUrl,
    Credentials: credentialSession,
    SigningKey: secrets.SigningKey,
    NodeName: config.NodeName,
    Expectation: expectation,
    Workspaces: registry,
    Harnesses: harnesses,
    NativeRuntimes: nativeRuntimes,
    PollInterval: TimeSpan.FromSeconds(config.PollIntervalSeconds)));

Console.WriteLine($"Sentry node '{config.NodeName}' -> {config.GatewayUrl}");
Console.WriteLine($"workspaces: {string.Join(", ", registrations.Select(r => r.WorkspaceId))}");
Console.WriteLine($"harnesses:  {string.Join(", ", harnesses.Keys)}");

await worker.RunAsync(shutdown.Token);
return 0;

static string ResolvePath(string? configured, string baseDirectory, string fallbackName)
{
    var value = string.IsNullOrWhiteSpace(configured) ? fallbackName : configured;
    return Path.GetFullPath(value, baseDirectory);
}

internal sealed record WorkspaceConfig(
    string WorkspaceId,
    string RootPath,
    string[] AllowedHarnesses,
    string[] AllowedModes);

internal sealed record NodeConfig(
    string GatewayUrl,
    string NodeId,
    string NodeName,
    string OwnerUserId,
    WorkspaceConfig[] Workspaces,
    string[] TeamMembers,
    string? CredentialPath = null,
    string? LogPath = null,
    string ClaudeExecutable = "claude",
    string? CodexExecutable = null,
    string? CodexSessionPath = null,
    int PollIntervalSeconds = 5);
