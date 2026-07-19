using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using SentryAssistant.Models;

namespace SentryAssistant.Services;

public sealed class SettingsService
{
    private sealed class Envelope
    {
        public AssistantSettings Settings { get; set; } = new();
        public string ProtectedApiKey { get; set; } = string.Empty;
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

    public string GetApiKey()
    {
        if (string.IsNullOrWhiteSpace(_envelope.ProtectedApiKey)) return string.Empty;
        try
        {
            var protectedBytes = Convert.FromBase64String(_envelope.ProtectedApiKey);
            return Encoding.UTF8.GetString(ProtectedData.Unprotect(protectedBytes, null, DataProtectionScope.CurrentUser));
        }
        catch
        {
            return string.Empty;
        }
    }

    public void SetApiKey(string value)
    {
        if (string.IsNullOrWhiteSpace(value)) return;
        var protectedBytes = ProtectedData.Protect(Encoding.UTF8.GetBytes(value.Trim()), null, DataProtectionScope.CurrentUser);
        _envelope.ProtectedApiKey = Convert.ToBase64String(protectedBytes);
    }

    public async Task SaveAsync()
    {
        Directory.CreateDirectory(_folder);
        var json = JsonSerializer.Serialize(_envelope, new JsonSerializerOptions { WriteIndented = true });
        await File.WriteAllTextAsync(SettingsPath, json);
    }
}
