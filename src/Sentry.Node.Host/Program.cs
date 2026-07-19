using System.Text.Json;
using Sentry.Node;
using Sentry.Node.Harnesses;
using Sentry.Node.Security;
using Sentry.Node.Workspaces;

// Sentry execution node.
//
// Runs at sign-in under its owner's account, never as SYSTEM, and connects
// outbound to the Gateway. Nothing listens here.
//
// Configuration comes from appsettings.node.json next to the executable, or a
// path given as the first argument. Secrets come from the environment:
//   SENTRY_NODE_TOKEN    node-audience access token from device enrolment
//   SENTRY_SIGNING_KEY   shared key used to validate signed work orders

var configPath = args.Length > 0
    ? args[0]
    : Path.Combine(AppContext.BaseDirectory, "appsettings.node.json");

if (!File.Exists(configPath))
{
    Console.Error.WriteLine($"No configuration at {configPath}");
    return 2;
}

var token = Environment.GetEnvironmentVariable("SENTRY_NODE_TOKEN");
var signingKey = Environment.GetEnvironmentVariable("SENTRY_SIGNING_KEY");

if (string.IsNullOrWhiteSpace(token) || string.IsNullOrWhiteSpace(signingKey))
{
    Console.Error.WriteLine(
        "SENTRY_NODE_TOKEN and SENTRY_SIGNING_KEY must both be set. " +
        "Enrol this machine as an executionNode device to obtain a token.");
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
    AccessToken: token,
    SigningKey: signingKey,
    NodeName: config.NodeName,
    Expectation: expectation,
    Workspaces: registry,
    Harnesses: harnesses,
    PollInterval: TimeSpan.FromSeconds(config.PollIntervalSeconds)));

Console.WriteLine($"Sentry node '{config.NodeName}' -> {config.GatewayUrl}");
Console.WriteLine($"workspaces: {string.Join(", ", registrations.Select(r => r.WorkspaceId))}");
Console.WriteLine($"harnesses:  {string.Join(", ", harnesses.Keys)}");

await worker.RunAsync(shutdown.Token);
return 0;

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
    string ClaudeExecutable = "claude",
    int PollIntervalSeconds = 5);
