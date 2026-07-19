using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using SentryAssistant.Models;

namespace SentryAssistant.Services;

public sealed class SettingsService : IDeviceCredentialStore
{
    private sealed class Envelope
    {
        public AssistantSettings Settings { get; set; } = new();
        public string ProtectedApiKey { get; set; } = string.Empty;

        /// <summary>
        /// The device refresh token, DPAPI-sealed to this Windows account.
        ///
        /// Only the refresh token is kept. The access token is short-lived and is
        /// re-obtained on launch, so persisting it would widen the blast radius
        /// of a stolen settings file for no benefit.
        /// </summary>
        public string ProtectedRefreshToken { get; set; } = string.Empty;
    }

    private readonly string _folder = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "SentryAssistant");

    private string SettingsPath => Path.Combine(_folder, "settings.json");
    private Envelope _envelope = new();

    public AssistantSettings Settings => _envelope.Settings;

    public async Task LoadAsync()
    {
        Directory.CreateDirectory(_folder);
        if (File.Exists(SettingsPath))
        {
            var json = await File.ReadAllTextAsync(SettingsPath);
            _envelope = JsonSerializer.Deserialize<Envelope>(json) ?? new Envelope();
        }

        if (string.IsNullOrWhiteSpace(GetApiKey()))
        {
            var legacyPath = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
                ".codex", "pet-tools", "sentry-voice", ".env.local");
            if (File.Exists(legacyPath))
            {
                var line = (await File.ReadAllLinesAsync(legacyPath))
                    .FirstOrDefault(value => value.TrimStart().StartsWith("OPENAI_API_KEY=", StringComparison.Ordinal));
                if (line is not null)
                {
                    SetApiKey(line[(line.IndexOf('=') + 1)..].Trim().Trim('"', '\''));
                    await SaveAsync();
                }
            }
        }
    }

    public string GetApiKey() => Unseal(_envelope.ProtectedApiKey);

    public void SetApiKey(string value)
    {
        if (string.IsNullOrWhiteSpace(value)) return;
        _envelope.ProtectedApiKey = Seal(value);
    }

    /// <summary>Whether this desktop has a stored device credential to try.</summary>
    public bool HasRefreshToken => !string.IsNullOrWhiteSpace(_envelope.ProtectedRefreshToken);

    public string GetRefreshToken() => Unseal(_envelope.ProtectedRefreshToken);

    /// <summary>
    /// Store a refresh token and flush it to disk immediately.
    ///
    /// Persisting is part of the operation rather than something the caller is
    /// trusted to remember: the gateway retires a refresh token the moment it is
    /// redeemed, so a rotated token that is held only in memory locks this
    /// desktop out if the process stops before the next save.
    /// </summary>
    public async Task SetRefreshTokenAsync(string value)
    {
        if (string.IsNullOrWhiteSpace(value)) return;
        _envelope.ProtectedRefreshToken = Seal(value);
        await SaveAsync();
    }

    /// <summary>
    /// Forget the device credential — used when the gateway rejects it. Keeping a
    /// token that is known to be dead only produces the same failure every launch.
    /// </summary>
    public async Task ClearRefreshTokenAsync()
    {
        if (!HasRefreshToken) return;
        _envelope.ProtectedRefreshToken = string.Empty;
        await SaveAsync();
    }

    private static string Seal(string value) => Convert.ToBase64String(
        ProtectedData.Protect(
            Encoding.UTF8.GetBytes(value.Trim()), null, DataProtectionScope.CurrentUser));

    private static string Unseal(string sealedValue)
    {
        if (string.IsNullOrWhiteSpace(sealedValue)) return string.Empty;
        try
        {
            return Encoding.UTF8.GetString(ProtectedData.Unprotect(
                Convert.FromBase64String(sealedValue), null, DataProtectionScope.CurrentUser));
        }
        catch
        {
            // A settings file copied from another Windows account cannot be
            // unsealed. Treating that as "no credential" sends the person back
            // through enrolment, which is exactly the right outcome.
            return string.Empty;
        }
    }

    public async Task SaveAsync()
    {
        Directory.CreateDirectory(_folder);
        var json = JsonSerializer.Serialize(_envelope, new JsonSerializerOptions { WriteIndented = true });
        await File.WriteAllTextAsync(SettingsPath, json);
    }
}
