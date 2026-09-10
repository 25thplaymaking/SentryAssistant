using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace Sentry.Node.Gateway;

public sealed record NodeCredentialSecrets(
    string AccessToken,
    string RefreshToken,
    string SigningKey);

/// <summary>
/// DPAPI-protected execution-node credentials for the current Windows account.
///
/// The file contains only encrypted blobs. It cannot be copied to another user
/// or machine and unsealed, and each token rotation replaces it atomically.
/// </summary>
public sealed class NodeCredentialFile
{
    private static readonly byte[] Entropy = Encoding.UTF8.GetBytes("Frontir.Sentry.Node/v1");
    private static readonly JsonSerializerOptions Json = new(JsonSerializerDefaults.Web)
    {
        WriteIndented = true
    };

    private sealed record Envelope(
        string ProtectedAccessToken,
        string ProtectedRefreshToken,
        string ProtectedSigningKey);

    public NodeCredentialFile(string path)
    {
        if (string.IsNullOrWhiteSpace(path))
            throw new ArgumentException("A credential path is required.", nameof(path));
        Path = System.IO.Path.GetFullPath(path);
    }

    public string Path { get; }

    public NodeCredentialSecrets Load()
    {
        EnsureWindows();
        var envelope = JsonSerializer.Deserialize<Envelope>(File.ReadAllText(Path), Json)
            ?? throw new InvalidOperationException("The node credential file is empty.");
        return new NodeCredentialSecrets(
            Unseal(envelope.ProtectedAccessToken),
            Unseal(envelope.ProtectedRefreshToken),
            Unseal(envelope.ProtectedSigningKey));
    }

    public async Task SaveAsync(
        NodeCredentialSecrets secrets,
        CancellationToken cancellationToken = default)
    {
        EnsureWindows();
        if (string.IsNullOrWhiteSpace(secrets.AccessToken)
            || string.IsNullOrWhiteSpace(secrets.RefreshToken)
            || string.IsNullOrWhiteSpace(secrets.SigningKey))
        {
            throw new ArgumentException("All node credentials are required.", nameof(secrets));
        }

        var directory = System.IO.Path.GetDirectoryName(Path)
            ?? throw new InvalidOperationException("The credential path has no parent directory.");
        Directory.CreateDirectory(directory);

        var envelope = new Envelope(
            Seal(secrets.AccessToken),
            Seal(secrets.RefreshToken),
            Seal(secrets.SigningKey));
        var encoded = JsonSerializer.Serialize(envelope, Json);
        var temporary = $"{Path}.tmp-{Guid.NewGuid():N}";
        try
        {
            await File.WriteAllTextAsync(temporary, encoded, Encoding.UTF8, cancellationToken);
            File.Move(temporary, Path, overwrite: true);
        }
        finally
        {
            if (File.Exists(temporary)) File.Delete(temporary);
        }
    }

    private static string Seal(string value)
    {
        if (!OperatingSystem.IsWindows())
            throw new PlatformNotSupportedException(
                "The protected node credential file requires Windows DPAPI.");
        return Convert.ToBase64String(ProtectedData.Protect(
            Encoding.UTF8.GetBytes(value), Entropy, DataProtectionScope.CurrentUser));
    }

    private static string Unseal(string value)
    {
        if (!OperatingSystem.IsWindows())
            throw new PlatformNotSupportedException(
                "The protected node credential file requires Windows DPAPI.");
        return Encoding.UTF8.GetString(ProtectedData.Unprotect(
            Convert.FromBase64String(value), Entropy, DataProtectionScope.CurrentUser));
    }

    private static void EnsureWindows()
    {
        if (!OperatingSystem.IsWindows())
            throw new PlatformNotSupportedException(
                "The protected node credential file requires Windows DPAPI.");
    }
}
