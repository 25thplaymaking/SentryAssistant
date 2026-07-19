using Sentry.Node.Gateway;
using Sentry.Node.Harnesses;
using Sentry.Node.Security;
using Sentry.Node.Workspaces;

namespace Sentry.Node;

public sealed record NodeWorkerOptions(
    string GatewayUrl,
    string AccessToken,
    string SigningKey,
    string NodeName,
    NodeExpectation Expectation,
    WorkspaceRegistry Workspaces,
    IReadOnlyDictionary<string, IHarnessAdapter> Harnesses,
    TimeSpan PollInterval);

/// <summary>
/// The long-running node process.
///
/// Claims work outbound, re-validates every dispatch locally before executing,
/// runs it through the named harness inside the resolved workspace, and returns
/// a result. A refusal is reported as a failed run rather than silently dropped,
/// so a work order never disappears without an explanation.
/// </summary>
public sealed class NodeWorker
{
    private readonly NodeWorkerOptions _options;
    private readonly WorkOrderValidator _validator;
    private readonly Action<string> _log;

    public NodeWorker(NodeWorkerOptions options, Action<string>? log = null)
    {
        _options = options;
        _validator = new WorkOrderValidator(options.SigningKey, options.Expectation);
        _log = log ?? Console.WriteLine;
    }

    public async Task RunAsync(CancellationToken cancellationToken)
    {
        using var connection = new NodeConnection(_options.GatewayUrl, _options.AccessToken);
        var backoff = new ReconnectBackoff();

        await RegisterAsync(connection, backoff, cancellationToken);

        while (!cancellationToken.IsCancellationRequested)
        {
            try
            {
                var work = await connection.ClaimWorkAsync(cancellationToken);
                backoff.Reset();

                foreach (var dispatch in work)
                {
                    await HandleAsync(connection, dispatch, cancellationToken);
                }

                await Task.Delay(_options.PollInterval, cancellationToken);
            }
            catch (OperationCanceledException)
            {
                break;
            }
            catch (Exception exception)
            {
                // The Gateway being unreachable is expected, not fatal. Work
                // queues server-side until this node comes back.
                var delay = backoff.NextDelay();
                _log($"gateway unreachable ({exception.GetType().Name}); retrying in {delay.TotalSeconds:0}s");
                try { await Task.Delay(delay, cancellationToken); }
                catch (OperationCanceledException) { break; }
            }
        }
    }

    private async Task RegisterAsync(
        NodeConnection connection, ReconnectBackoff backoff, CancellationToken cancellationToken)
    {
        var workspaces = _options.Workspaces.RegisteredIds
            .Select(id =>
            {
                var registration = _options.Workspaces.Resolve(
                    id, _options.Harnesses.Keys.First(), "readOnly");
                return new WorkspaceRegistrationRequest(
                    id,
                    registration.AllowedHarnesses.ToList(),
                    registration.AllowedModes.ToList());
            })
            .ToList();

        while (!cancellationToken.IsCancellationRequested)
        {
            try
            {
                var response = await connection.RegisterAsync(
                    new NodeRegistrationRequest(_options.NodeName, workspaces), cancellationToken);
                _log($"registered as node {response?.NodeId} with {workspaces.Count} workspace(s)");
                backoff.Reset();
                return;
            }
            catch (Exception exception)
            {
                var delay = backoff.NextDelay();
                _log($"registration failed ({exception.GetType().Name}); retrying in {delay.TotalSeconds:0}s");
                try { await Task.Delay(delay, cancellationToken); }
                catch (OperationCanceledException) { return; }
            }
        }
    }

    private async Task HandleAsync(
        NodeConnection connection, DispatchedWorkOrder dispatch, CancellationToken cancellationToken)
    {
        RunResultRequest result;

        try
        {
            // A signature from the Gateway is not authority on its own. Everything
            // is re-checked here against what this node knows locally.
            var validated = _validator.Validate(dispatch.Token);

            var workspace = _options.Workspaces.Resolve(
                validated.WorkspaceId, validated.Harness, validated.Mode);

            if (!_options.Harnesses.TryGetValue(validated.Harness, out var harness))
            {
                result = Refusal($"Harness '{validated.Harness}' is not available on this node.");
            }
            else
            {
                _log($"running {validated.Harness} on {validated.WorkspaceId} ({validated.Mode})");

                var progress = new Progress<HarnessProgress>(
                    e => _log($"  {e.Type}: {Trim(e.Summary)}"));

                var run = await harness.ExecuteAsync(
                    dispatch.Prompt, validated.Mode, workspace, progress, cancellationToken);

                result = new RunResultRequest(
                    run.Outcome, run.Summary, run.StatusBoundary, run.Evidence);
            }
        }
        catch (WorkOrderRejectedException exception)
        {
            // A rejected order is reported, not dropped: the requester must be
            // able to see why nothing ran.
            result = Refusal($"Work order rejected: {exception.Message}");
        }
        catch (WorkspaceResolutionException exception)
        {
            result = Refusal($"Workspace refused: {exception.Message}");
        }
        catch (Exception exception)
        {
            result = Refusal($"Node error: {exception.GetType().Name}: {exception.Message}");
        }

        try
        {
            await connection.SubmitResultAsync(dispatch.WorkOrderId, result, cancellationToken);
            _log($"reported {result.Outcome} for {dispatch.WorkOrderId}");
        }
        catch (Exception exception)
        {
            // The run already happened; losing the report is recoverable because
            // the Gateway still holds the order and can be reconciled.
            _log($"could not report result ({exception.GetType().Name}); Gateway will reconcile");
        }
    }

    private static RunResultRequest Refusal(string reason) =>
        new("failed", reason, "implemented",
            new Dictionary<string, object> { ["refused"] = true, ["reason"] = reason });

    private static string Trim(string value) =>
        value.Length <= 160 ? value : value[..160] + "...";
}
