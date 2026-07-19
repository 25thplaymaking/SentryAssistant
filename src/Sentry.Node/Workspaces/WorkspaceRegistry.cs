using System.Diagnostics.CodeAnalysis;

namespace Sentry.Node.Workspaces;

/// <summary>
/// One registered workspace: a Sentry-owned identifier bound to a fixed local path.
/// </summary>
public sealed record WorkspaceRegistration(
    string WorkspaceId,
    string RootPath,
    IReadOnlySet<string> AllowedHarnesses,
    IReadOnlySet<string> AllowedModes);

public sealed class WorkspaceResolutionException : Exception
{
    public WorkspaceResolutionException(string message) : base(message) { }
}

/// <summary>
/// Maps remote workspace IDs to fixed local paths.
///
/// The Gateway never sends a filesystem path — only an identifier this node has
/// already registered. That is the whole point: a compromised or malicious
/// Gateway still cannot name a directory on this machine.
///
/// Every resolved path is additionally proven to sit inside its registered root,
/// so a traversal sequence smuggled through a relative path cannot escape.
/// </summary>
public sealed class WorkspaceRegistry
{
    private readonly Dictionary<string, WorkspaceRegistration> _registrations;

    public WorkspaceRegistry(IEnumerable<WorkspaceRegistration> registrations)
    {
        _registrations = new Dictionary<string, WorkspaceRegistration>(StringComparer.Ordinal);
        foreach (var registration in registrations)
        {
            var root = NormalizeRoot(registration.RootPath);
            _registrations[registration.WorkspaceId] = registration with { RootPath = root };
        }
    }

    public IReadOnlyCollection<string> RegisteredIds => _registrations.Keys;

    /// <summary>
    /// Resolves a workspace ID to its registered root. Anything not registered is
    /// refused; a value that looks like a path is refused for the same reason.
    /// </summary>
    public WorkspaceRegistration Resolve(string workspaceId, string harness, string mode)
    {
        if (string.IsNullOrWhiteSpace(workspaceId))
        {
            throw new WorkspaceResolutionException("Workspace identifier was empty.");
        }

        if (!_registrations.TryGetValue(workspaceId, out var registration))
        {
            // Deliberately does not echo the requested value, and does not fall
            // back to treating it as a path.
            throw new WorkspaceResolutionException(
                "Workspace is not registered on this node. Raw remote paths are refused.");
        }

        if (!registration.AllowedHarnesses.Contains(harness))
        {
            throw new WorkspaceResolutionException(
                $"Harness '{harness}' is not enabled for this workspace.");
        }

        if (!registration.AllowedModes.Contains(mode))
        {
            throw new WorkspaceResolutionException(
                $"Mode '{mode}' is not permitted for this workspace.");
        }

        return registration;
    }

    /// <summary>
    /// Resolves a path relative to a registered root and proves the result stays
    /// inside it. Absolute paths and traversal sequences are refused.
    /// </summary>
    public string ResolvePathWithin(WorkspaceRegistration registration, string relativePath)
    {
        if (string.IsNullOrWhiteSpace(relativePath))
        {
            return registration.RootPath;
        }

        if (Path.IsPathRooted(relativePath) || relativePath.Contains(':'))
        {
            throw new WorkspaceResolutionException(
                "Absolute paths are refused; supply a path relative to the workspace root.");
        }

        var combined = Path.GetFullPath(Path.Combine(registration.RootPath, relativePath));

        // Compare against the root with a trailing separator so "C:\ws-evil"
        // cannot pass as a child of "C:\ws".
        var rootWithSeparator = registration.RootPath.EndsWith(Path.DirectorySeparatorChar)
            ? registration.RootPath
            : registration.RootPath + Path.DirectorySeparatorChar;

        if (!combined.Equals(registration.RootPath, StringComparison.OrdinalIgnoreCase) &&
            !combined.StartsWith(rootWithSeparator, StringComparison.OrdinalIgnoreCase))
        {
            throw new WorkspaceResolutionException(
                "Resolved path escapes the workspace root.");
        }

        return combined;
    }

    public bool TryResolve(
        string workspaceId,
        string harness,
        string mode,
        [NotNullWhen(true)] out WorkspaceRegistration? registration,
        out string? error)
    {
        try
        {
            registration = Resolve(workspaceId, harness, mode);
            error = null;
            return true;
        }
        catch (WorkspaceResolutionException exception)
        {
            registration = null;
            error = exception.Message;
            return false;
        }
    }

    private static string NormalizeRoot(string rootPath)
    {
        if (string.IsNullOrWhiteSpace(rootPath))
        {
            throw new ArgumentException("Workspace root path cannot be empty.", nameof(rootPath));
        }

        // Registration roots are operator-supplied and must be absolute, so a
        // relative root cannot silently resolve against the process directory.
        if (!Path.IsPathRooted(rootPath))
        {
            throw new ArgumentException(
                $"Workspace root '{rootPath}' must be an absolute path.", nameof(rootPath));
        }

        return Path.TrimEndingDirectorySeparator(Path.GetFullPath(rootPath));
    }
}
